"""Local HTTP server for ScreenVision Sentinel OCR requests."""

from __future__ import annotations

import json
import logging
import math
import re
import socket
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, RLock, Thread
from time import perf_counter, time

from screenvision_sentinel.app.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_SCREENSHOT_DIR,
    AppConfig,
    load_config,
)
from screenvision_sentinel.capture.base import ScreenRegion
from screenvision_sentinel.capture.mss_capture import MssCaptureService
from screenvision_sentinel.detection.base import ObservationStabilityTracker
from screenvision_sentinel.ocr.base import OCRResult
from screenvision_sentinel.ocr.engine_factory import create_ocr_engine
from screenvision_sentinel.vision import (
    ERROR_CAPTURE_FAILED,
    ERROR_INVALID_REGION,
    ERROR_OCR_FAILED,
    CapturePolicy,
    CapturePolicyError,
    DebugImageStorage,
    VisionPipeline,
    VisionResult,
)

LOGGER = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 65_536
MAX_BATCH_ITEMS = 64
MAX_FIELD_NAME_CHARS = 64
MAX_FIELD_LABEL_CHARS = 128
MAX_OCR_ATTEMPTS_PER_FIELD = 2
MONITOR_OCR_ATTEMPTS_PER_FIELD = 1
MIN_MONITOR_INTERVAL_SECONDS = 0.5
MAX_MONITOR_INTERVAL_SECONDS = 60.0
REQUEST_SOCKET_TIMEOUT_SECONDS = 10.0
SERVER_API_REVISION = "2026-07-10-monitor-latest-v2"
SUPPORTED_FIELD_TYPES = frozenset({"text", "number", "date", "datetime", "gender"})
NUMBER_VALUE_PATTERN = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
DATE_VALUE_PATTERN = re.compile(
    r"\d{2,4}\s*(?:[-/.年])\s*\d{1,2}\s*(?:[-/.月])\s*\d{1,2}\s*(?:日)?"
)
TIME_VALUE_PATTERN = re.compile(r"\d{1,2}\s*:\s*\d{2}")


class BackgroundMonitorController:
    """Run read-only observations and retain only the latest in-memory value snapshot."""

    def __init__(
        self,
        *,
        monitor: ObservationStabilityTracker,
        execution_lock: object,
        monitor_lock: object,
        default_interval_seconds: float,
    ) -> None:
        self._monitor = monitor
        self._execution_lock = execution_lock
        self._monitor_lock = monitor_lock
        self._default_interval_seconds = _normalize_monitor_interval(default_interval_seconds)
        self._state_lock = RLock()
        self._stop_event: Event | None = None
        self._thread: Thread | None = None
        self._interval_seconds: float | None = None
        self._started_at_ms: int | None = None
        self._last_tick_at_ms: int | None = None
        self._last_tick_success: bool | None = None
        self._last_tick_elapsed_ms: float | None = None
        self._last_error_type: str | None = None
        self._last_empty_count: int | None = None
        self._last_review_count: int | None = None
        self._last_fallback_count: int | None = None
        self._field_count = 0
        self._fast_mode = False
        self._completed_tick_count = 0
        self._latest_snapshot: dict[str, object] | None = None
        self._stop_requested = False
        self._was_started = False

    @property
    def default_interval_seconds(self) -> float:
        """Return the configured safe default interval."""
        return self._default_interval_seconds

    def start(
        self,
        pipeline: VisionPipeline,
        items: list[dict[str, object]],
        *,
        fast_mode: bool,
        interval_seconds: float,
    ) -> bool:
        """Start one background worker, returning false when a worker already runs."""
        with self._state_lock:
            if self._is_running_locked():
                return False

            with self._monitor_lock:
                self._monitor.reset()
            stop_event = Event()
            self._stop_event = stop_event
            self._interval_seconds = interval_seconds
            self._started_at_ms = _now_ms()
            self._last_tick_at_ms = None
            self._last_tick_success = None
            self._last_tick_elapsed_ms = None
            self._last_error_type = None
            self._last_empty_count = None
            self._last_review_count = None
            self._last_fallback_count = None
            self._field_count = len(items)
            self._fast_mode = fast_mode
            self._completed_tick_count = 0
            self._latest_snapshot = None
            self._stop_requested = False
            self._was_started = True
            self._thread = Thread(
                target=self._run,
                args=(stop_event, pipeline, items, fast_mode, interval_seconds),
                daemon=True,
                name="ScreenVisionMonitor",
            )
            self._thread.start()
            LOGGER.info(
                "background monitor started fields=%d interval_seconds=%.1f "
                "fast_mode=%s performance_mode=low_load attempt_limit=%d",
                self._field_count,
                interval_seconds,
                fast_mode,
                MONITOR_OCR_ATTEMPTS_PER_FIELD,
            )
            return True

    def stop(self) -> bool:
        """Request a stop without pretending that in-flight OCR can be cancelled."""
        with self._state_lock:
            if not self._is_running_locked() or self._stop_event is None:
                return False
            self._stop_requested = True
            self._stop_event.set()
            LOGGER.info("background monitor stop requested")
            return True

    def status(self) -> dict[str, object]:
        """Return background monitor metadata without OCR text or screen coordinates."""
        with self._state_lock:
            running = self._is_running_locked()
            mode = "background" if self._was_started else "manual_tick"
            controller_state = {
                "mode": mode,
                "running": running,
                "stop_requested": self._stop_requested and running,
                "interval_seconds": self._interval_seconds if self._was_started else None,
                "started_at_ms": self._started_at_ms,
                "last_tick_at_ms": self._last_tick_at_ms,
                "last_tick_success": self._last_tick_success,
                "last_tick_elapsed_ms": self._last_tick_elapsed_ms,
                "last_error_type": self._last_error_type,
                "last_empty_count": self._last_empty_count,
                "last_review_count": self._last_review_count,
                "last_fallback_count": self._last_fallback_count,
                "field_count": self._field_count if self._was_started else 0,
                "fast_mode": self._fast_mode if self._was_started else None,
                "performance_mode": "low_load" if self._was_started else None,
                "attempt_limit": (MONITOR_OCR_ATTEMPTS_PER_FIELD if self._was_started else None),
            }
        with self._monitor_lock:
            monitor_state = self._monitor.status()
        return {**monitor_state, **controller_state}

    def latest(self) -> dict[str, object]:
        """Return the latest volatile OCR values without coordinates or image metadata."""
        with self._state_lock:
            running = self._is_running_locked()
            snapshot = self._latest_snapshot
            fields = (
                []
                if snapshot is None
                else [dict(field) for field in snapshot["fields"] if isinstance(field, dict)]
            )
            texts = {} if snapshot is None else dict(snapshot["texts"])
            return {
                "success": True,
                "running": running,
                "stop_requested": self._stop_requested and running,
                "snapshot_available": snapshot is not None,
                "tick_count": self._completed_tick_count,
                "snapshot_tick_count": (
                    None if snapshot is None else snapshot["snapshot_tick_count"]
                ),
                "observed_at_ms": None if snapshot is None else snapshot["observed_at_ms"],
                "last_tick_success": self._last_tick_success,
                "last_error_type": self._last_error_type,
                "field_count": len(fields),
                "empty_count": None if snapshot is None else snapshot["empty_count"],
                "review_count": None if snapshot is None else snapshot["review_count"],
                "performance_mode": "low_load" if self._was_started else None,
                "texts": texts,
                "fields": fields,
            }

    def _run(
        self,
        stop_event: Event,
        pipeline: VisionPipeline,
        items: list[dict[str, object]],
        fast_mode: bool,
        interval_seconds: float,
    ) -> None:
        while not stop_event.is_set():
            try:
                with self._execution_lock:
                    payload = build_batch_ocr_response_from_items(
                        pipeline,
                        items,
                        fast_mode=fast_mode,
                        save_debug=False,
                        attempt_limit=MONITOR_OCR_ATTEMPTS_PER_FIELD,
                        allow_individual_fallback=False,
                    )
                with self._monitor_lock:
                    _attach_monitor_observation(payload, self._monitor)
            except Exception as exc:
                LOGGER.error("background monitor tick failed error_type=%s", type(exc).__name__)
                self._record_error(type(exc).__name__)
            else:
                self._record_tick(payload)

            if stop_event.wait(interval_seconds):
                break

        with self._state_lock:
            if self._stop_event is stop_event:
                self._stop_requested = False
        LOGGER.info("background monitor stopped completed_ticks=%d", self._completed_tick_count)

    def _record_tick(self, payload: dict[str, object]) -> None:
        elapsed_ms = payload.get("batch_elapsed_ms")
        with self._state_lock:
            self._completed_tick_count += 1
            self._last_tick_at_ms = _now_ms()
            self._last_tick_success = bool(payload.get("success"))
            self._last_tick_elapsed_ms = (
                float(elapsed_ms)
                if isinstance(elapsed_ms, (int, float)) and not isinstance(elapsed_ms, bool)
                else None
            )
            self._last_error_type = None
            self._last_empty_count = _optional_nonnegative_int(payload.get("empty_count"))
            self._last_review_count = _optional_nonnegative_int(payload.get("review_count"))
            self._last_fallback_count = _optional_nonnegative_int(payload.get("fallback_count"))
            self._latest_snapshot = _build_latest_monitor_snapshot(
                payload,
                tick_count=self._completed_tick_count,
                observed_at_ms=self._last_tick_at_ms,
            )
            tick_count = self._completed_tick_count
            success = self._last_tick_success
            tick_elapsed_ms = self._last_tick_elapsed_ms
            empty_count = self._last_empty_count
            review_count = self._last_review_count
            fallback_count = self._last_fallback_count
        LOGGER.info(
            "background monitor heartbeat tick=%d success=%s elapsed_ms=%s "
            "empty=%s review=%s fallback=%s",
            tick_count,
            success,
            f"{tick_elapsed_ms:.1f}" if tick_elapsed_ms is not None else "unknown",
            empty_count,
            review_count,
            fallback_count,
        )

    def _record_error(self, error_type: str) -> None:
        with self._state_lock:
            self._last_tick_at_ms = _now_ms()
            self._last_tick_success = False
            self._last_tick_elapsed_ms = None
            self._last_error_type = error_type
            self._completed_tick_count += 1

    def _is_running_locked(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


class LocalHTTPServer(HTTPServer):
    """Loopback server that refuses to share its listening port with stale instances."""

    allow_reuse_address = False
    allow_reuse_port = False

    def server_bind(self) -> None:
        exclusive_address_use = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive_address_use is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive_address_use, 1)
        super().server_bind()


class OCRHandler(BaseHTTPRequestHandler):
    """Minimal JSON API handler for local OCR requests."""

    pipeline: VisionPipeline | None = None
    monitor: ObservationStabilityTracker | None = None
    execution_lock: object | None = None
    monitor_lock: object | None = None
    monitor_controller: BackgroundMonitorController | None = None

    def setup(self) -> None:
        """Bound idle clients so one partial request cannot block the serial server forever."""
        super().setup()
        self.connection.settimeout(REQUEST_SOCKET_TIMEOUT_SECONDS)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_response(
                200,
                build_health_response(
                    self._get_pipeline(),
                    monitor_status=self._get_monitor_status(),
                ),
            )
            return
        if self.path == "/monitor/status":
            self._send_response(200, self._get_monitor_status())
            return
        if self.path == "/monitor/latest":
            self._send_response(200, self._get_monitor_controller().latest())
            return
        self._send_response(404, {"success": False, "error": "Not found"})

    def do_POST(self) -> None:
        if self.path not in {
            "/ocr",
            "/ocr/batch",
            "/monitor/tick",
            "/monitor/start",
            "/monitor/stop",
        }:
            self._send_response(404, {"success": False, "error": "Not found"})
            return

        pipeline = self._get_pipeline()
        try:
            request = self._read_json_request()
            if self.path == "/ocr":
                with self._get_execution_lock():
                    response = build_single_ocr_response(pipeline, request)
                status = (
                    200
                    if response.get("success")
                    else _status_for_error(_optional_string(response.get("error_code")))
                )
                self._send_response(status, response)
                return

            if self.path == "/ocr/batch":
                with self._get_execution_lock():
                    response = build_batch_ocr_response(pipeline, request)
                self._send_response(200 if response.get("success") else 207, response)
                return

            if self.path == "/monitor/tick":
                controller = self.monitor_controller
                if controller is not None and controller.status().get("running"):
                    self._send_response(
                        409,
                        {
                            "success": False,
                            "error": (
                                "manual monitor tick is unavailable while "
                                "background monitoring runs"
                            ),
                        },
                    )
                    return
                with self._get_execution_lock(), self._get_monitor_lock():
                    response = build_monitor_tick_response(
                        pipeline,
                        self._get_monitor(),
                        request,
                    )
                self._send_response(200 if response.get("success") else 207, response)
                return

            controller = self._get_monitor_controller()
            if self.path == "/monitor/start":
                items, fast_mode, interval_seconds = parse_background_monitor_start_request(
                    pipeline,
                    request,
                    default_interval_seconds=controller.default_interval_seconds,
                )
                started = controller.start(
                    pipeline,
                    items,
                    fast_mode=fast_mode,
                    interval_seconds=interval_seconds,
                )
                if not started:
                    self._send_response(
                        409,
                        {
                            "success": False,
                            "error": "background monitor is already running",
                            "monitor": controller.status(),
                        },
                    )
                    return
                self._send_response(202, {"success": True, "monitor": controller.status()})
                return

            stopped = controller.stop()
            self._send_response(
                200,
                {
                    "success": True,
                    "stop_requested": stopped,
                    "monitor": controller.status(),
                },
            )
        except CapturePolicyError as exc:
            self._send_response(
                400,
                {
                    "success": False,
                    "error_code": ERROR_INVALID_REGION,
                    "error": str(exc),
                },
            )
        except ValueError as exc:
            self._send_response(400, {"success": False, "error": str(exc)})
        except Exception as exc:
            LOGGER.error("server request failed error_type=%s", type(exc).__name__)
            self._send_response(500, {"success": False, "error": "internal server error"})

    def _get_pipeline(self) -> VisionPipeline:
        if self.pipeline is None:
            raise RuntimeError("OCR pipeline has not been initialised")
        return self.pipeline

    def _get_monitor(self) -> ObservationStabilityTracker:
        if self.monitor is None:
            raise RuntimeError("monitor tracker has not been initialised")
        return self.monitor

    def _get_execution_lock(self) -> object:
        if self.execution_lock is None:
            raise RuntimeError("OCR execution lock has not been initialised")
        return self.execution_lock

    def _get_monitor_lock(self) -> object:
        if self.monitor_lock is None:
            raise RuntimeError("monitor lock has not been initialised")
        return self.monitor_lock

    def _get_monitor_controller(self) -> BackgroundMonitorController:
        if self.monitor_controller is None:
            raise RuntimeError("background monitor controller has not been initialised")
        return self.monitor_controller

    def _get_monitor_status(self) -> dict[str, object]:
        if self.monitor_controller is not None:
            return self.monitor_controller.status()
        with self._get_monitor_lock():
            return self._get_monitor().status()

    def _read_json_request(self) -> dict[str, object]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Content-Length must be an integer") from exc
        if content_length < 0 or content_length > MAX_REQUEST_BYTES:
            raise ValueError("request body is too large")

        try:
            payload = self.rfile.read(content_length)
        except TimeoutError as exc:
            raise ValueError("request body read timed out") from exc
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("request body must be valid JSON") from exc
        if not isinstance(decoded, dict):
            raise ValueError("request body must be a JSON object")
        return decoded

    def _send_response(self, status: int, payload: dict[str, object]) -> None:
        response_body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, _format: str, *_args: object) -> None:
        """Disable default HTTP access logging to avoid request payload leakage."""


def run(port: int | None = None, host: str | None = None) -> None:
    """Run the local serial HTTP server. This does not start on import."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    config = load_config(DEFAULT_CONFIG_PATH)
    server_address = build_server_address(config, port=port, host=host)
    OCRHandler.pipeline = build_pipeline(config)
    OCRHandler.monitor = ObservationStabilityTracker(config.confirmation_count)
    OCRHandler.execution_lock = RLock()
    OCRHandler.monitor_lock = RLock()
    OCRHandler.monitor_controller = BackgroundMonitorController(
        monitor=OCRHandler.monitor,
        execution_lock=OCRHandler.execution_lock,
        monitor_lock=OCRHandler.monitor_lock,
        default_interval_seconds=config.screenshot_interval_seconds,
    )
    try:
        httpd = LocalHTTPServer(server_address, OCRHandler)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 10048:
            print(f"[ERROR] {server_address[0]}:{server_address[1]} 已被另一份 OCR 服务占用。")
            print("请关闭旧服务窗口后，再重新启动当前版本。")
            return
        raise
    print("==================================================")
    print(" ScreenVision Sentinel OCR API Server is running")
    print(f" Listening on http://{server_address[0]}:{server_address[1]}")
    print(" Server implementation: HTTPServer (serial requests)")
    print("==================================================")
    try:
        httpd.serve_forever()
    finally:
        OCRHandler.monitor_controller.stop()
        httpd.server_close()


def build_server_address(
    config: AppConfig,
    *,
    port: int | None = None,
    host: str | None = None,
) -> tuple[str, int]:
    """Return the configured local bind address."""
    selected_host = config.server_host if host is None else host
    if selected_host != "127.0.0.1":
        raise ValueError("server host must be 127.0.0.1")
    selected_port = config.server_port if port is None else port
    if isinstance(selected_port, bool) or not isinstance(selected_port, int):
        raise ValueError("server port must be an integer")
    if not 0 <= selected_port <= 65535:
        raise ValueError("server port must be between 0 and 65535")
    return (selected_host, selected_port)


def build_pipeline(config: AppConfig) -> VisionPipeline:
    """Build the shared vision pipeline for server requests."""
    LOGGER.info("loading OCR engine engine=%s", config.ocr_engine)
    return VisionPipeline(
        capture_service=MssCaptureService(DEFAULT_SCREENSHOT_DIR),
        ocr_engine=create_ocr_engine(config.ocr_engine),
        policy=CapturePolicy.from_config(config),
        debug_storage=DebugImageStorage(config.debug_image_dir),
        ocr_confidence_threshold=config.ocr_confidence_threshold,
    )


def build_health_response(
    pipeline: VisionPipeline,
    *,
    monitor: ObservationStabilityTracker | None = None,
    monitor_status: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return server and OCR-engine readiness details for UI diagnostics."""
    engine = pipeline.ocr_engine
    engine_name = getattr(engine, "name", "unknown")
    requested_engine = str(getattr(engine, "requested_engine", engine_name))
    fallback_reason = str(getattr(engine, "fallback_reason", ""))
    ocr_ready = not (requested_engine != "mock" and engine_name == "mock")
    payload: dict[str, object] = {
        "success": True,
        "status": "ok",
        "api_revision": SERVER_API_REVISION,
        "background_monitor_available": True,
        "monitor_latest_available": True,
        "engine_name": engine_name,
        "requested_engine": requested_engine,
        "ocr_ready": ocr_ready,
        "fallback_reason": fallback_reason,
        "runtime": _build_runtime_diagnostics(engine),
    }
    if monitor_status is not None:
        payload["monitor"] = monitor_status
    elif monitor is not None:
        payload["monitor"] = monitor.status()
    return payload


def _build_runtime_diagnostics(engine: object) -> dict[str, object]:
    """Return non-sensitive OCR runtime details for the local workbench."""
    runtime_details = getattr(engine, "runtime_details", None)
    if callable(runtime_details):
        try:
            details = runtime_details()
        except Exception as exc:
            LOGGER.warning("OCR runtime diagnostics failed error_type=%s", type(exc).__name__)
        else:
            if isinstance(details, dict):
                return details
    return {
        "device_label": "未知",
        "device_detail": "当前 OCR 引擎未报告推理设备",
        "available_execution_providers": [],
        "gpu_switch_available": False,
    }


def build_single_ocr_response(
    pipeline: VisionPipeline,
    request: dict[str, object],
) -> dict[str, object]:
    """Run one OCR request and return a JSON payload."""
    rect = request.get("rect")
    if not isinstance(rect, list):
        raise ValueError("rect must be an array")

    region = pipeline.policy.parse_rect_values(rect)
    save_debug = parse_boolean_request_flag(request, "save_debug")
    fast_mode = parse_boolean_request_flag(request, "fast_mode")
    field_type = parse_field_type(request.get("field_type"))
    result, fallback_used, fallback_strategy = capture_and_ocr_with_fallbacks(
        pipeline,
        region,
        save_debug=save_debug,
        fast_mode=fast_mode,
    )
    payload = result.to_dict()
    payload["fallback_used"] = fallback_used
    payload["fallback_strategy"] = fallback_strategy
    payload["ocr_mode"] = _ocr_mode_name(fast_mode)
    payload["ocr_mode_label"] = _ocr_mode_label(fast_mode)
    payload["attempt_limit"] = MAX_OCR_ATTEMPTS_PER_FIELD
    _annotate_field_validation(payload, field_type=field_type, pipeline=pipeline)
    return payload


def build_batch_ocr_response(
    pipeline: VisionPipeline,
    request: dict[str, object],
) -> dict[str, object]:
    """Run a read-only batch OCR request for named screen regions."""
    fast_mode = parse_boolean_request_flag(request, "fast_mode")
    save_debug = parse_boolean_request_flag(request, "save_debug")
    items = parse_batch_items(pipeline, request)
    return build_batch_ocr_response_from_items(
        pipeline,
        items,
        save_debug=save_debug,
        fast_mode=fast_mode,
    )


def build_batch_ocr_response_from_items(
    pipeline: VisionPipeline,
    items: list[dict[str, object]],
    *,
    save_debug: bool,
    fast_mode: bool,
    attempt_limit: int = MAX_OCR_ATTEMPTS_PER_FIELD,
    allow_individual_fallback: bool = True,
) -> dict[str, object]:
    """Run a read-only batch OCR request from already validated item definitions."""
    attempt_limit = _normalize_ocr_attempt_limit(attempt_limit)
    if len(items) > 1 and _supports_shared_batch_capture(pipeline):
        try:
            shared_response = build_shared_capture_batch_ocr_response(
                pipeline,
                items,
                save_debug=save_debug,
                fast_mode=fast_mode,
                attempt_limit=attempt_limit,
            )
        except CapturePolicyError:
            LOGGER.info("shared batch capture skipped because its union violates policy")
            fallback_response = build_individual_batch_ocr_response(
                pipeline,
                items,
                save_debug=save_debug,
                fast_mode=fast_mode,
                attempt_limit=attempt_limit,
            )
            fallback_response["shared_capture_skipped"] = True
            fallback_response["shared_capture_skip_reason"] = "capture_policy"
            return _add_batch_diagnostics(
                fallback_response,
                fast_mode=fast_mode,
                attempt_limit=attempt_limit,
            )
        if _all_texts_empty(shared_response["texts"]) and allow_individual_fallback:
            fallback_response = build_individual_batch_ocr_response(
                pipeline,
                items,
                save_debug=save_debug,
                fast_mode=fast_mode,
                attempt_limit=attempt_limit,
            )
            fallback_response["shared_capture_fallback"] = True
            return _add_batch_diagnostics(
                fallback_response,
                fast_mode=fast_mode,
                attempt_limit=attempt_limit,
            )
        if _all_texts_empty(shared_response["texts"]):
            shared_response["shared_capture_fallback_suppressed"] = True
        return _add_batch_diagnostics(
            shared_response,
            fast_mode=fast_mode,
            attempt_limit=attempt_limit,
        )

    return _add_batch_diagnostics(
        build_individual_batch_ocr_response(
            pipeline,
            items,
            save_debug=save_debug,
            fast_mode=fast_mode,
            attempt_limit=attempt_limit,
        ),
        fast_mode=fast_mode,
        attempt_limit=attempt_limit,
    )


def build_individual_batch_ocr_response(
    pipeline: VisionPipeline,
    items: list[dict[str, object]],
    *,
    save_debug: bool,
    fast_mode: bool,
    attempt_limit: int,
) -> dict[str, object]:
    """Run batch OCR with one capture per item."""
    started_at = perf_counter()

    results: list[dict[str, object]] = []
    texts: dict[str, str] = {}
    for item in items:
        result, fallback_used, fallback_strategy = capture_and_ocr_with_fallbacks(
            pipeline,
            item["region"],
            save_debug=save_debug,
            fast_mode=fast_mode,
            attempt_limit=attempt_limit,
        )
        payload = {
            "name": item["name"],
            "label": item["label"],
            "field_type": item["field_type"],
            **result.to_dict(),
            "fallback_used": fallback_used,
            "fallback_strategy": fallback_strategy,
        }
        _annotate_field_validation(
            payload,
            field_type=str(item["field_type"]),
            pipeline=pipeline,
        )
        results.append(payload)
        texts[item["name"]] = result.text

    elapsed_ms = (perf_counter() - started_at) * 1000
    return {
        "success": all(bool(result["success"]) for result in results),
        "results": results,
        "texts": texts,
        "elapsed_ms": elapsed_ms,
        "summary": _build_summary(results),
    }


def build_shared_capture_batch_ocr_response(
    pipeline: VisionPipeline,
    items: list[dict[str, object]],
    *,
    save_debug: bool,
    fast_mode: bool,
    attempt_limit: int,
) -> dict[str, object]:
    """Run batch OCR from one shared screenshot for speed."""
    started_at = perf_counter()
    regions = [item["region"] for item in items]
    capture_region = _expand_region(_union_region(regions), 8)
    pipeline.policy.validate(capture_region)

    capture_started_at = perf_counter()
    try:
        capture_result = pipeline.capture_service.capture_region(
            capture_region,
            save_to_disk=False,
        )
    except Exception as exc:
        capture_elapsed_ms = (perf_counter() - capture_started_at) * 1000
        LOGGER.error("shared batch capture failed error_type=%s", type(exc).__name__)
        return _batch_capture_failure_payload(
            pipeline,
            items,
            capture_region,
            capture_elapsed_ms=capture_elapsed_ms,
            elapsed_ms=(perf_counter() - started_at) * 1000,
            error_message=f"capture service failed: {type(exc).__name__}",
        )
    capture_elapsed_ms = (perf_counter() - capture_started_at) * 1000
    if not capture_result.success or capture_result.image_data is None:
        return _batch_capture_failure_payload(
            pipeline,
            items,
            capture_region,
            capture_elapsed_ms=capture_elapsed_ms,
            elapsed_ms=(perf_counter() - started_at) * 1000,
            error_message=capture_result.error or "capture failed",
        )

    debug_image_path = None
    if save_debug and pipeline.debug_storage is not None:
        try:
            debug_image_path = pipeline.debug_storage.save(capture_result.image_data)
        except Exception as exc:
            LOGGER.warning(
                "batch debug image save failed error_type=%s",
                type(exc).__name__,
            )

    results: list[dict[str, object]] = []
    texts: dict[str, str] = {}
    ocr_elapsed_total_ms = 0.0
    for item in items:
        item_region = item["region"]
        ocr_result, fallback_used, fallback_strategy, ocr_elapsed_ms = (
            recognize_cropped_image_with_fallbacks(
                pipeline,
                capture_result.image_data,
                capture_region,
                item_region,
                fast_mode=fast_mode,
                attempt_limit=attempt_limit,
            )
        )
        ocr_elapsed_total_ms += ocr_elapsed_ms
        elapsed_ms = capture_elapsed_ms + ocr_elapsed_ms
        vision_result = VisionResult(
            success=ocr_result.success,
            text=ocr_result.text if ocr_result.success else "",
            confidence=ocr_result.confidence if ocr_result.success else 0.0,
            boxes=ocr_result.boxes if ocr_result.success else (),
            elapsed_ms=elapsed_ms,
            capture_elapsed_ms=capture_elapsed_ms,
            ocr_elapsed_ms=ocr_elapsed_ms,
            engine_name=ocr_result.engine_name,
            region=item_region,
            request_id=uuid.uuid4().hex[:12],
            debug_image_path=debug_image_path,
            error_code=None if ocr_result.success else ERROR_OCR_FAILED,
            error_message=None if ocr_result.success else ocr_result.error or "OCR failed",
        )
        payload = {
            "name": item["name"],
            "label": item["label"],
            "field_type": item["field_type"],
            **vision_result.to_dict(),
            "fallback_used": fallback_used,
            "fallback_strategy": fallback_strategy,
        }
        _annotate_field_validation(
            payload,
            field_type=str(item["field_type"]),
            pipeline=pipeline,
        )
        results.append(payload)
        texts[item["name"]] = vision_result.text

    elapsed_ms = (perf_counter() - started_at) * 1000
    return {
        "success": all(bool(result["success"]) for result in results),
        "results": results,
        "texts": texts,
        "elapsed_ms": elapsed_ms,
        "capture_elapsed_ms": capture_elapsed_ms,
        "ocr_elapsed_ms": ocr_elapsed_total_ms,
        "shared_capture": True,
        "capture_region": _region_to_dict(capture_region),
        "summary": _build_summary(results),
    }


def recognize_cropped_image_with_fallbacks(
    pipeline: VisionPipeline,
    image_data: object,
    capture_region: ScreenRegion,
    item_region: ScreenRegion,
    *,
    fast_mode: bool,
    attempt_limit: int = MAX_OCR_ATTEMPTS_PER_FIELD,
) -> tuple[OCRResult, bool, str, float]:
    """OCR one subregion from an already captured image with a bounded retry budget."""
    primary_use_det = not fast_mode
    attempts = [
        (0, primary_use_det, ""),
        (0, not primary_use_det, _strategy_name(not primary_use_det)),
        (4, primary_use_det, "expanded_4px_primary"),
        (4, not primary_use_det, "expanded_4px_opposite"),
        (8, primary_use_det, "expanded_8px_primary"),
        (8, not primary_use_det, "expanded_8px_opposite"),
    ]

    first_result: OCRResult | None = None
    total_ocr_elapsed_ms = 0.0
    for padding, use_det, strategy in attempts[: _normalize_ocr_attempt_limit(attempt_limit)]:
        started_at = perf_counter()
        try:
            crop = _crop_from_capture(image_data, capture_region, item_region, padding=padding)
            ocr_result = pipeline.ocr_engine.recognize(crop, use_det=use_det)
        except Exception as exc:
            elapsed_ms = (perf_counter() - started_at) * 1000
            total_ocr_elapsed_ms += elapsed_ms
            LOGGER.error("shared batch OCR failed error_type=%s", type(exc).__name__)
            if strategy and first_result is not None:
                return first_result, False, "", total_ocr_elapsed_ms
            return (
                OCRResult(
                    text="",
                    confidence=0.0,
                    boxes=(),
                    elapsed_ms=elapsed_ms,
                    engine_name=str(getattr(pipeline.ocr_engine, "name", "unknown")),
                    success=False,
                    error=f"OCR engine failed: {type(exc).__name__}",
                ),
                bool(strategy),
                strategy,
                total_ocr_elapsed_ms,
            )
        total_ocr_elapsed_ms += (perf_counter() - started_at) * 1000
        if first_result is None:
            first_result = ocr_result
        if not ocr_result.success:
            if strategy and first_result is not None:
                return first_result, False, "", total_ocr_elapsed_ms
            return ocr_result, bool(strategy), strategy, total_ocr_elapsed_ms
        if ocr_result.text.strip():
            return ocr_result, bool(strategy), strategy, total_ocr_elapsed_ms
    return first_result or _empty_ocr_result(pipeline), False, "", total_ocr_elapsed_ms


def _crop_from_capture(
    image_data: object,
    capture_region: ScreenRegion,
    item_region: ScreenRegion,
    *,
    padding: int,
) -> object:
    left = max(0, item_region.left - capture_region.left - padding)
    top = max(0, item_region.top - capture_region.top - padding)
    right = max(left + 1, item_region.left - capture_region.left + item_region.width + padding)
    bottom = max(top + 1, item_region.top - capture_region.top + item_region.height + padding)
    return image_data[top:bottom, left:right].copy()


def _union_region(regions: list[ScreenRegion]) -> ScreenRegion:
    left = min(region.left for region in regions)
    top = min(region.top for region in regions)
    right = max(region.left + region.width for region in regions)
    bottom = max(region.top + region.height for region in regions)
    return ScreenRegion(left=left, top=top, width=right - left, height=bottom - top)


def _batch_capture_failure_payload(
    pipeline: VisionPipeline,
    items: list[dict[str, object]],
    capture_region: ScreenRegion,
    *,
    capture_elapsed_ms: float,
    elapsed_ms: float,
    error_message: str,
) -> dict[str, object]:
    results = []
    texts = {}
    for item in items:
        result = VisionResult(
            success=False,
            text="",
            confidence=0.0,
            boxes=(),
            elapsed_ms=elapsed_ms,
            capture_elapsed_ms=capture_elapsed_ms,
            ocr_elapsed_ms=0.0,
            engine_name=str(getattr(pipeline.ocr_engine, "name", "unknown")),
            region=item["region"],
            request_id=uuid.uuid4().hex[:12],
            error_code=ERROR_CAPTURE_FAILED,
            error_message=error_message,
        )
        payload = {
            "name": item["name"],
            "label": item["label"],
            "field_type": item["field_type"],
            **result.to_dict(),
        }
        _annotate_field_validation(
            payload,
            field_type=str(item["field_type"]),
            pipeline=pipeline,
        )
        results.append(payload)
        texts[item["name"]] = ""
    return {
        "success": False,
        "results": results,
        "texts": texts,
        "elapsed_ms": elapsed_ms,
        "capture_elapsed_ms": capture_elapsed_ms,
        "ocr_elapsed_ms": 0.0,
        "shared_capture": True,
        "capture_region": _region_to_dict(capture_region),
        "summary": _build_summary(results),
    }


def _empty_ocr_result(pipeline: VisionPipeline) -> OCRResult:
    return OCRResult(
        text="",
        confidence=0.0,
        boxes=(),
        elapsed_ms=0.0,
        engine_name=pipeline.ocr_engine.name,
        success=True,
    )


def _supports_shared_batch_capture(pipeline: object) -> bool:
    return all(
        hasattr(pipeline, attr)
        for attr in ("capture_service", "ocr_engine", "policy", "debug_storage")
    )


def _all_texts_empty(texts: object) -> bool:
    if not isinstance(texts, dict) or not texts:
        return True
    return all(not str(value).strip() for value in texts.values())


def _build_summary(results: list[dict[str, object]]) -> str:
    lines = []
    for result in results:
        label = str(result.get("label") or result.get("name") or "")
        text = str(result.get("text") or "").strip()
        if not result.get("success"):
            error_code = str(result.get("error_code") or "unknown_error")
            display_text = f"[读取失败：{error_code}]"
        elif not text:
            display_text = "[空]"
        elif result.get("requires_review"):
            display_text = f"[需复核：{text}]"
        else:
            display_text = text
        lines.append(f"{label}: {display_text}")
    return "\n".join(lines)


def _add_batch_diagnostics(
    payload: dict[str, object],
    *,
    fast_mode: bool,
    attempt_limit: int,
) -> dict[str, object]:
    """Attach aggregate, non-sensitive diagnostics to a batch response."""
    raw_results = payload.get("results")
    results = raw_results if isinstance(raw_results, list) else []
    typed_results = [result for result in results if isinstance(result, dict)]
    payload["ocr_mode"] = _ocr_mode_name(fast_mode)
    payload["ocr_mode_label"] = _ocr_mode_label(fast_mode)
    payload["attempt_limit"] = attempt_limit
    payload["fallback_count"] = sum(bool(result.get("fallback_used")) for result in typed_results)
    payload["empty_count"] = sum(
        not str(result.get("text") or "").strip() for result in typed_results
    )
    payload["review_count"] = sum(bool(result.get("requires_review")) for result in typed_results)
    payload["batch_elapsed_ms"] = _batch_metric(payload, typed_results, "elapsed_ms")
    payload["batch_capture_elapsed_ms"] = _batch_metric(
        payload,
        typed_results,
        "capture_elapsed_ms",
    )
    payload["batch_ocr_elapsed_ms"] = _batch_metric(payload, typed_results, "ocr_elapsed_ms")
    return payload


def build_monitor_tick_response(
    pipeline: VisionPipeline,
    monitor: ObservationStabilityTracker,
    request: dict[str, object],
) -> dict[str, object]:
    """Run one caller-triggered observation and expose only confirmation metadata."""
    fast_mode = parse_boolean_request_flag(request, "fast_mode")
    save_debug = parse_boolean_request_flag(request, "save_debug")
    items = parse_batch_items(pipeline, request)
    return build_monitor_observation_response(
        pipeline,
        monitor,
        items,
        fast_mode=fast_mode,
        save_debug=save_debug,
    )


def build_monitor_observation_response(
    pipeline: VisionPipeline,
    monitor: ObservationStabilityTracker,
    items: list[dict[str, object]],
    *,
    fast_mode: bool,
    save_debug: bool = False,
) -> dict[str, object]:
    """Run one parsed monitor observation and attach non-sensitive confirmation state."""
    payload = build_batch_ocr_response_from_items(
        pipeline,
        items,
        fast_mode=fast_mode,
        save_debug=save_debug,
        attempt_limit=MONITOR_OCR_ATTEMPTS_PER_FIELD,
        allow_individual_fallback=False,
    )
    return _attach_monitor_observation(payload, monitor)


def _attach_monitor_observation(
    payload: dict[str, object],
    monitor: ObservationStabilityTracker,
) -> dict[str, object]:
    """Attach tracker metadata after OCR so status reads need only a short lock."""
    raw_results = payload.get("results")
    results = (
        [result for result in raw_results if isinstance(result, dict)]
        if isinstance(raw_results, list)
        else []
    )
    payload["monitor"] = {
        "mode": "manual_tick",
        "confirmation_count": monitor.confirmation_count,
        "fields": monitor.observe(results),
    }
    return payload


def _annotate_field_validation(
    payload: dict[str, object],
    *,
    field_type: str,
    pipeline: object,
) -> None:
    """Flag unreadable or structurally implausible OCR without changing OCR text."""
    payload["field_type"] = field_type
    success = bool(payload.get("success"))
    text = str(payload.get("text") or "").strip()
    confidence = payload.get("confidence")
    threshold = _get_ocr_confidence_threshold(pipeline)

    if not success:
        validation_status = "ocr_failed"
    elif not text:
        validation_status = "unreadable"
    elif threshold > 0 and _is_low_confidence(confidence, threshold):
        validation_status = "low_confidence"
    elif not _value_matches_field_type(text, field_type):
        validation_status = "format_mismatch"
    else:
        validation_status = "valid"

    payload["validation_status"] = validation_status
    payload["is_valid"] = validation_status == "valid"
    payload["requires_review"] = validation_status != "valid"
    if threshold > 0:
        payload["confidence_threshold"] = threshold


def _get_ocr_confidence_threshold(pipeline: object) -> float:
    raw_threshold = getattr(pipeline, "ocr_confidence_threshold", 0.0)
    if isinstance(raw_threshold, bool):
        return 0.0
    if isinstance(raw_threshold, (int, float)):
        threshold = float(raw_threshold)
        if math.isfinite(threshold):
            return min(1.0, max(0.0, threshold))
    return 0.0


def _is_low_confidence(confidence: object, threshold: float) -> bool:
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return True
    numeric_confidence = float(confidence)
    return not math.isfinite(numeric_confidence) or numeric_confidence < threshold


def _value_matches_field_type(text: str, field_type: str) -> bool:
    if field_type == "text":
        return True
    if field_type == "number":
        return NUMBER_VALUE_PATTERN.search(text) is not None
    if field_type == "date":
        return DATE_VALUE_PATTERN.search(text) is not None
    if field_type == "datetime":
        return (
            DATE_VALUE_PATTERN.search(text) is not None
            and TIME_VALUE_PATTERN.search(text) is not None
        )
    if field_type == "gender":
        normalized = re.sub(r"\s+", "", text).casefold()
        return (
            "男" in normalized or "女" in normalized or normalized in {"male", "female", "m", "f"}
        )
    return False


def _ocr_mode_name(fast_mode: bool) -> str:
    return "fast" if fast_mode else "stable"


def _ocr_mode_label(fast_mode: bool) -> str:
    return "极速" if fast_mode else "稳定"


def _batch_metric(
    payload: dict[str, object],
    results: list[dict[str, object]],
    name: str,
) -> float:
    value = payload.get(name)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return sum(
        float(value)
        for result in results
        if isinstance((value := result.get(name)), (int, float)) and not isinstance(value, bool)
    )


def _region_to_dict(region: ScreenRegion) -> dict[str, int]:
    return {
        "left": region.left,
        "top": region.top,
        "width": region.width,
        "height": region.height,
    }


def capture_and_ocr_with_fallbacks(
    pipeline: VisionPipeline,
    region: ScreenRegion,
    *,
    save_debug: bool,
    fast_mode: bool,
    attempt_limit: int = MAX_OCR_ATTEMPTS_PER_FIELD,
) -> tuple[VisionResult, bool, str]:
    """Try a bounded OCR fallback sequence for small, tightly-cropped fields."""
    primary_use_det = not fast_mode
    primary = pipeline.capture_and_ocr(
        region,
        save_debug=save_debug,
        use_det=primary_use_det,
    )
    if not primary.success or primary.text.strip():
        return primary, False, ""

    attempts = [
        (region, not primary_use_det, _strategy_name(not primary_use_det)),
        (_expand_region(region, 4), primary_use_det, "expanded_4px_primary"),
        (_expand_region(region, 4), not primary_use_det, "expanded_4px_opposite"),
        (_expand_region(region, 8), primary_use_det, "expanded_8px_primary"),
        (_expand_region(region, 8), not primary_use_det, "expanded_8px_opposite"),
    ]
    fallback_limit = max(0, _normalize_ocr_attempt_limit(attempt_limit) - 1)
    for attempt_region, use_det, strategy in attempts[:fallback_limit]:
        fallback = pipeline.capture_and_ocr(
            attempt_region,
            save_debug=save_debug,
            use_det=use_det,
        )
        if fallback.success and fallback.text.strip():
            _remove_unselected_debug_image(primary, fallback)
            LOGGER.info(
                "OCR fallback used request_id=%s fallback_request_id=%s strategy=%s",
                primary.request_id,
                fallback.request_id,
                strategy,
            )
            return fallback, True, strategy
        _remove_unselected_debug_image(fallback, primary)
    return primary, False, ""


def _remove_unselected_debug_image(result: VisionResult, selected: VisionResult) -> None:
    """Remove a generated debug frame that does not correspond to the adopted result."""
    path = result.debug_image_path
    if path is None or path == selected.debug_image_path:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        LOGGER.warning("unselected debug image cleanup failed error_type=%s", type(exc).__name__)


def _strategy_name(use_det: bool) -> str:
    return "full_detection" if use_det else "whole_crop"


def _normalize_ocr_attempt_limit(value: int) -> int:
    """Keep internal OCR attempt budgets within the supported bounded sequence."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("attempt_limit must be an integer")
    if not 1 <= value <= MAX_OCR_ATTEMPTS_PER_FIELD:
        raise ValueError(f"attempt_limit must be between 1 and {MAX_OCR_ATTEMPTS_PER_FIELD}")
    return value


def _optional_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, int(value))


def _expand_region(region: ScreenRegion, padding: int) -> ScreenRegion:
    return ScreenRegion(
        left=region.left - padding,
        top=region.top - padding,
        width=region.width + padding * 2,
        height=region.height + padding * 2,
    )


def parse_batch_items(
    pipeline: VisionPipeline,
    request: dict[str, object],
) -> list[dict[str, object]]:
    """Parse batch request items without accepting actions or commands."""
    raw_items = request.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("items must be a non-empty array")
    if len(raw_items) > MAX_BATCH_ITEMS:
        raise ValueError(f"items must contain at most {MAX_BATCH_ITEMS} entries")

    parsed: list[dict[str, object]] = []
    seen_names: set[str] = set()
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            raise ValueError(f"items[{index}] must be an object")
        name = raw_item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"items[{index}].name must be a non-empty string")
        normalized_name = name.strip()
        if len(normalized_name) > MAX_FIELD_NAME_CHARS:
            raise ValueError(
                f"items[{index}].name must not exceed {MAX_FIELD_NAME_CHARS} characters"
            )
        if normalized_name in seen_names:
            raise ValueError(f"items[{index}].name must be unique")
        seen_names.add(normalized_name)
        rect = raw_item.get("rect")
        if not isinstance(rect, list):
            raise ValueError(f"items[{index}].rect must be an array")
        label = raw_item.get("label")
        if not isinstance(label, str) or not label.strip():
            label = normalized_name
        normalized_label = label.strip()
        if len(normalized_label) > MAX_FIELD_LABEL_CHARS:
            raise ValueError(
                f"items[{index}].label must not exceed {MAX_FIELD_LABEL_CHARS} characters"
            )
        field_type = parse_field_type(
            raw_item.get("field_type"),
            context=f"items[{index}].field_type",
        )
        parsed.append(
            {
                "name": normalized_name,
                "label": normalized_label,
                "field_type": field_type,
                "region": pipeline.policy.parse_rect_values(rect),
            }
        )
    return parsed


def parse_field_type(
    value: object,
    *,
    context: str = "field_type",
) -> str:
    """Parse an optional structural validation hint for an OCR field."""
    if value is None:
        return "text"
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be a non-empty string")
    field_type = value.strip().lower()
    if field_type not in SUPPORTED_FIELD_TYPES:
        supported = ", ".join(sorted(SUPPORTED_FIELD_TYPES))
        raise ValueError(f"{context} must be one of: {supported}")
    return field_type


def parse_background_monitor_start_request(
    pipeline: VisionPipeline,
    request: dict[str, object],
    *,
    default_interval_seconds: float,
) -> tuple[list[dict[str, object]], bool, float]:
    """Validate an explicit background monitor start request without persisting regions."""
    save_debug = parse_boolean_request_flag(request, "save_debug")
    if save_debug:
        raise ValueError("save_debug must be false for background monitoring")
    fast_mode = parse_boolean_request_flag(request, "fast_mode")
    interval_seconds = parse_monitor_interval(
        request.get("interval_seconds", default_interval_seconds)
    )
    return parse_batch_items(pipeline, request), fast_mode, interval_seconds


def parse_monitor_interval(value: object) -> float:
    """Parse a bounded background observation interval in seconds."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("interval_seconds must be a number")
    interval_seconds = float(value)
    if not math.isfinite(interval_seconds):
        raise ValueError("interval_seconds must be finite")
    if not MIN_MONITOR_INTERVAL_SECONDS <= interval_seconds <= MAX_MONITOR_INTERVAL_SECONDS:
        raise ValueError(
            "interval_seconds must be between "
            f"{MIN_MONITOR_INTERVAL_SECONDS:g} and {MAX_MONITOR_INTERVAL_SECONDS:g}"
        )
    return interval_seconds


def _build_latest_monitor_snapshot(
    payload: dict[str, object],
    *,
    tick_count: int,
    observed_at_ms: int,
) -> dict[str, object]:
    """Whitelist the latest values exposed to local scripts; omit regions and images."""
    raw_results = payload.get("results")
    results = raw_results if isinstance(raw_results, list) else []
    fields: list[dict[str, object]] = []
    texts: dict[str, str] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        name = str(result.get("name") or "").strip()
        if not name:
            continue
        text = str(result.get("text") or "")
        confidence = result.get("confidence")
        numeric_confidence = (
            float(confidence)
            if isinstance(confidence, (int, float))
            and not isinstance(confidence, bool)
            and math.isfinite(float(confidence))
            else 0.0
        )
        fields.append(
            {
                "name": name,
                "label": str(result.get("label") or name),
                "field_type": str(result.get("field_type") or "text"),
                "success": bool(result.get("success")),
                "text": text,
                "confidence": numeric_confidence,
                "validation_status": str(result.get("validation_status") or "unreadable"),
                "is_valid": bool(result.get("is_valid")),
                "requires_review": bool(result.get("requires_review")),
                "error_code": _optional_string(result.get("error_code")),
            }
        )
        texts[name] = text
    return {
        "snapshot_tick_count": tick_count,
        "observed_at_ms": observed_at_ms,
        "empty_count": sum(not text.strip() for text in texts.values()),
        "review_count": sum(bool(field["requires_review"]) for field in fields),
        "texts": texts,
        "fields": fields,
    }


def _normalize_monitor_interval(value: float) -> float:
    """Clamp configuration-derived intervals to the safe runtime range."""
    if not math.isfinite(value):
        return MIN_MONITOR_INTERVAL_SECONDS
    return min(MAX_MONITOR_INTERVAL_SECONDS, max(MIN_MONITOR_INTERVAL_SECONDS, value))


def _now_ms() -> int:
    return int(time() * 1000)


def parse_boolean_request_flag(
    request: dict[str, object],
    name: str,
    *,
    default: bool = False,
) -> bool:
    """Parse a JSON boolean flag while rejecting path-like strings."""
    value = request.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _status_for_error(error_code: str | None) -> int:
    return 400 if error_code == ERROR_INVALID_REGION else 500


if __name__ == "__main__":
    run()
