import json
import logging
import sys
import time
import types
from io import BytesIO
from pathlib import Path
from threading import RLock

import pytest

from screenvision_sentinel import cli
from screenvision_sentinel.app.config import AppConfig
from screenvision_sentinel.capture.base import ScreenRegion, ScreenshotResult
from screenvision_sentinel.capture.mss_capture import MssCaptureService
from screenvision_sentinel.detection.base import ObservationStabilityTracker
from screenvision_sentinel.ocr import engine_factory
from screenvision_sentinel.ocr.base import OCRResult
from screenvision_sentinel.ocr.mock import MockOCREngine
from screenvision_sentinel.ocr.rapid_ocr import RapidOCREngine, _iter_ocr_inputs
from screenvision_sentinel.server import (
    BackgroundMonitorController,
    LocalHTTPServer,
    OCRHandler,
    build_batch_ocr_response,
    build_health_response,
    build_monitor_tick_response,
    build_server_address,
    build_single_ocr_response,
    parse_background_monitor_start_request,
    parse_batch_items,
    parse_boolean_request_flag,
    parse_field_type,
    parse_monitor_interval,
    recognize_cropped_image_with_fallbacks,
)
from screenvision_sentinel.vision import (
    ERROR_CAPTURE_FAILED,
    CapturePolicy,
    VisionPipeline,
    VisionResult,
)


def test_mock_ocr_returns_deterministic_result(tmp_path: Path) -> None:
    image_path = tmp_path / "fake.png"
    image_path.write_bytes(b"not a real image")
    engine = MockOCREngine(text="床号 01", confidence=0.99)

    result = engine.recognize(image_path)

    assert result.success is True
    assert result.text == "床号 01"
    assert result.confidence == 0.99
    assert result.engine_name == "mock"


def test_mock_ocr_reports_missing_image(tmp_path: Path) -> None:
    engine = MockOCREngine()

    result = engine.recognize(tmp_path / "missing.png")

    assert result.success is False
    assert result.error is not None


def test_unknown_ocr_engine_falls_back_to_mock() -> None:
    engine = engine_factory.create_ocr_engine("missing-engine")

    assert engine.name == "mock"
    result = engine.recognize(object())
    assert result.success is False
    assert result.text == ""


def test_rapidocr_initialization_failure_falls_back_to_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = types.ModuleType("screenvision_sentinel.ocr.rapid_ocr")

    class BrokenRapidOCREngine:
        def __init__(self) -> None:
            raise RuntimeError("boom")

    fake_module.RapidOCREngine = BrokenRapidOCREngine
    monkeypatch.setitem(sys.modules, "screenvision_sentinel.ocr.rapid_ocr", fake_module)

    engine = engine_factory._try_create_rapid_ocr()

    assert engine.name == "mock"
    assert "RapidOCR" in engine.fallback_reason
    assert "boom" in engine.fallback_reason
    assert engine.recognize(object()).success is False


def test_rapidocr_initializes_onnx_runtime_with_low_load_thread_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = types.ModuleType("rapidocr_onnxruntime")

    class CapturingBackend:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    fake_module.RapidOCR = CapturingBackend
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", fake_module)

    engine = RapidOCREngine()

    assert engine._engine.kwargs == {
        "intra_op_num_threads": 2,
        "inter_op_num_threads": 1,
    }


def test_health_response_reports_mock_fallback_not_ocr_ready() -> None:
    engine = MockOCREngine()
    engine.requested_engine = "rapidocr"
    engine.fallback_reason = "RapidOCR initialisation failed"
    pipeline = types.SimpleNamespace(ocr_engine=engine)

    payload = build_health_response(pipeline)

    assert payload["success"] is True
    assert payload["engine_name"] == "mock"
    assert payload["requested_engine"] == "rapidocr"
    assert payload["ocr_ready"] is False
    assert payload["fallback_reason"] == "RapidOCR initialisation failed"
    assert payload["runtime"]["device_label"] == "未知"
    assert payload["background_monitor_available"] is True
    assert payload["monitor_latest_available"] is True
    assert payload["api_revision"] == "2026-07-10-monitor-latest-v2"


def test_local_server_does_not_reuse_an_existing_listening_port() -> None:
    assert LocalHTTPServer.allow_reuse_address is False
    assert LocalHTTPServer.allow_reuse_port is False


def test_rapidocr_retries_small_field_image_variants_when_original_is_empty() -> None:
    np = pytest.importorskip("numpy")
    engine = RapidOCREngine.__new__(RapidOCREngine)
    backend = VariantRapidOCRBackend()
    engine._engine = backend

    image = np.zeros((20, 40, 4), dtype=np.uint8)

    result = engine.recognize(image, use_det=False)

    assert result.success is True
    assert result.text == "70"
    assert backend.calls[0] == ((20, 40, 3), False)
    assert backend.calls[1] == ((80, 160, 3), False)


def test_rapidocr_detection_uses_one_normalized_input_without_variant_multiplication() -> None:
    np = pytest.importorskip("numpy")
    engine = RapidOCREngine.__new__(RapidOCREngine)
    backend = DetectionVariantRapidOCRBackend()
    engine._engine = backend

    result = engine.recognize(np.zeros((20, 40, 3), dtype=np.uint8), use_det=True)

    assert result.success is True
    assert result.text == ""
    assert backend.calls == [((20, 40, 3), True)]


def test_rapidocr_does_not_enlarge_large_images_for_small_field_retries() -> None:
    np = pytest.importorskip("numpy")
    image = np.zeros((120, 200, 3), dtype=np.uint8)

    variants = _iter_ocr_inputs(image)

    assert [variant.shape for variant in variants] == [(120, 200, 3)]


def test_vision_pipeline_uses_mock_capture_and_ocr_without_logging_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pipeline = VisionPipeline(
        capture_service=FakeCaptureService(),
        ocr_engine=MockOCREngine(text="患者张三", confidence=0.88),
    )
    caplog.set_level(logging.INFO, logger="screenvision_sentinel.vision.pipeline")

    result = pipeline.capture_and_ocr(ScreenRegion(left=0, top=0, width=20, height=10))

    assert result.success is True
    assert result.text == "患者张三"
    assert result.confidence == 0.88
    assert "患者张三" not in caplog.text
    assert "result_chars=4" in caplog.text


def test_vision_pipeline_reports_capture_failure() -> None:
    pipeline = VisionPipeline(
        capture_service=FakeCaptureService(success=False, error="mss failed"),
        ocr_engine=MockOCREngine(),
    )

    result = pipeline.capture_and_ocr(ScreenRegion(left=0, top=0, width=20, height=10))

    assert result.success is False
    assert result.error_code == ERROR_CAPTURE_FAILED
    assert result.error_message == "mss failed"


def test_vision_pipeline_converts_capture_exception_to_structured_failure() -> None:
    class RaisingCaptureService:
        def capture_region(
            self,
            region: ScreenRegion,
            save_to_disk: bool = True,
        ) -> ScreenshotResult:
            raise RuntimeError("boom")

    pipeline = VisionPipeline(
        capture_service=RaisingCaptureService(),
        ocr_engine=MockOCREngine(),
    )

    result = pipeline.capture_and_ocr(ScreenRegion(left=0, top=0, width=20, height=10))

    assert result.success is False
    assert result.error_code == ERROR_CAPTURE_FAILED
    assert result.error_message == "capture service failed: RuntimeError"


def test_vision_pipeline_converts_ocr_exception_to_structured_failure() -> None:
    class RaisingOCREngine:
        name = "raising"

        def recognize(self, image_source: object, use_det: bool = True) -> OCRResult:
            raise RuntimeError("boom")

    pipeline = VisionPipeline(
        capture_service=FakeCaptureService(),
        ocr_engine=RaisingOCREngine(),
    )

    result = pipeline.capture_and_ocr(ScreenRegion(left=0, top=0, width=20, height=10))

    assert result.success is False
    assert result.error_code == "ocr_failed"
    assert result.error_message == "OCR engine failed: RuntimeError"


def test_mss_capture_without_disk_output_does_not_create_output_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    np = pytest.importorskip("numpy")
    import mss

    class FakeMSS:
        def __enter__(self) -> "FakeMSS":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def grab(self, _monitor: dict[str, int]) -> object:
            return np.zeros((4, 4, 4), dtype=np.uint8)

    monkeypatch.setattr(mss, "MSS", FakeMSS)
    output_dir = tmp_path / "screenshots"

    result = MssCaptureService(output_dir).capture_region(
        ScreenRegion(left=0, top=0, width=4, height=4),
        save_to_disk=False,
    )

    assert result.success is True
    assert result.image_path is None
    assert not output_dir.exists()


def test_mss_capture_returns_failure_for_mss_screenshot_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mss
    from mss.exception import ScreenShotError

    class BrokenMSS:
        def __enter__(self) -> "BrokenMSS":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def grab(self, _monitor: dict[str, int]) -> object:
            raise ScreenShotError("capture unavailable")

    monkeypatch.setattr(mss, "MSS", BrokenMSS)

    result = MssCaptureService(tmp_path).capture_region(
        ScreenRegion(left=0, top=0, width=4, height=4),
        save_to_disk=False,
    )

    assert result.success is False
    assert result.error == "capture unavailable"


def test_cli_outputs_parseable_json_for_valid_rect(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.run(
        ["--rect", "1,2,3,4", "--engine", "mock"],
        pipeline_factory=lambda _config, _engine_name: StaticPipeline(),
    )

    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["success"] is True
    assert payload["text"] == "ok"


def test_cli_reports_invalid_config_as_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "invalid.toml"
    config_path.write_text("[monitoring]\nconfirmation_count = 0\n", encoding="utf-8")
    monkeypatch.setattr(cli, "DEFAULT_CONFIG_PATH", config_path)

    exit_code = cli.run(
        ["--rect", "1,2,3,4", "--engine", "mock"],
        pipeline_factory=lambda _config, _engine_name: StaticPipeline(),
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["success"] is False
    assert "Invalid configuration" in payload["error"]


def test_cli_save_debug_rejects_path_argument(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.run(
            ["--rect", "1,2,3,4", "--save-debug", "C:\\temp\\debug.png"],
            pipeline_factory=lambda _config, _engine_name: StaticPipeline(),
        )

    payload = json.loads(capsys.readouterr().out)

    assert exc.value.code == 2
    assert payload["success"] is False
    assert "Invalid arguments" in payload["error"]


def test_server_defaults_to_loopback_address() -> None:
    assert build_server_address(AppConfig()) == ("127.0.0.1", 8181)


def test_server_explicit_zero_port_is_not_replaced_by_configured_port() -> None:
    assert build_server_address(AppConfig(), port=0) == ("127.0.0.1", 0)


@pytest.mark.parametrize("port", [True, -1, 65536, "8181"])
def test_server_rejects_invalid_port_override(port: object) -> None:
    with pytest.raises(ValueError, match="server port"):
        build_server_address(AppConfig(), port=port)


def test_server_rejects_non_loopback_address() -> None:
    with pytest.raises(ValueError):
        build_server_address(AppConfig(server_host="0.0.0.0"))


def test_server_save_debug_rejects_path_value() -> None:
    with pytest.raises(ValueError):
        parse_boolean_request_flag({"save_debug": "C:\\temp\\debug.png"}, "save_debug")


def test_server_rejects_non_utf8_json_as_client_error() -> None:
    handler = OCRHandler.__new__(OCRHandler)
    handler.headers = {"Content-Length": "1"}
    handler.rfile = BytesIO(b"\xff")

    with pytest.raises(ValueError, match="valid JSON"):
        handler._read_json_request()


def test_server_request_limit_accepts_documented_maximum_batch_shape() -> None:
    request = {
        "items": [
            {
                "name": f"FIELD_{index:02d}",
                "label": "字段" * 64,
                "rect": [1, 2, 3, 4],
            }
            for index in range(64)
        ]
    }
    body = json.dumps(request, ensure_ascii=False).encode("utf-8")
    assert len(body) > 8192
    handler = OCRHandler.__new__(OCRHandler)
    handler.headers = {"Content-Length": str(len(body))}
    handler.rfile = BytesIO(body)

    assert handler._read_json_request() == request


def test_server_batch_response_returns_named_texts() -> None:
    pipeline = StaticPipeline()

    payload = build_batch_ocr_response(
        pipeline,
        {
            "fast_mode": True,
            "save_debug": False,
            "items": [
                {"name": "HR", "label": "心室率", "rect": [1, 2, 3, 4]},
                {"name": "PR", "label": "PR 间期", "rect": [5, 6, 7, 8]},
            ],
        },
    )

    assert payload["success"] is True
    assert payload["texts"] == {"HR": "ok", "PR": "ok"}
    assert len(payload["results"]) == 2
    assert payload["ocr_mode"] == "fast"
    assert payload["attempt_limit"] == 2
    assert payload["fallback_count"] == 0
    assert payload["empty_count"] == 0
    assert payload["batch_elapsed_ms"] >= 0


def test_server_batch_summary_distinguishes_runtime_failure_from_empty_text() -> None:
    payload = build_batch_ocr_response(
        FailingPipeline(),
        {"items": [{"name": "HR", "label": "心室率", "rect": [1, 2, 3, 4]}]},
    )

    assert payload["success"] is False
    assert payload["summary"] == "心室率: [读取失败：ocr_failed]"


def test_server_flags_structurally_invalid_number_without_changing_ocr_text() -> None:
    payload = build_batch_ocr_response(
        StaticPipeline(),
        {
            "items": [
                {
                    "name": "HR",
                    "label": "心室率",
                    "field_type": "number",
                    "rect": [1, 2, 3, 4],
                }
            ]
        },
    )

    result = payload["results"][0]

    assert result["text"] == "ok"
    assert result["field_type"] == "number"
    assert result["validation_status"] == "format_mismatch"
    assert result["is_valid"] is False
    assert result["requires_review"] is True
    assert payload["review_count"] == 1
    assert payload["summary"] == "心室率: [需复核：ok]"


def test_server_treats_non_finite_confidence_as_low_confidence() -> None:
    payload = build_single_ocr_response(
        NonFiniteConfidencePipeline(),
        {"rect": [1, 2, 3, 4]},
    )

    assert payload["success"] is True
    assert payload["validation_status"] == "low_confidence"
    assert payload["is_valid"] is False
    assert payload["requires_review"] is True
    assert payload["confidence"] == 0.0
    json.dumps(payload, allow_nan=False)


def test_monitor_tick_confirms_repeated_valid_results_without_returning_values_in_state() -> None:
    tracker = ObservationStabilityTracker(confirmation_count=2)
    request = {
        "items": [
            {
                "name": "NAME",
                "label": "姓名",
                "field_type": "text",
                "rect": [1, 2, 3, 4],
            }
        ]
    }

    first = build_monitor_tick_response(StaticPipeline(), tracker, request)
    second = build_monitor_tick_response(StaticPipeline(), tracker, request)

    assert first["monitor"]["fields"][0]["stable"] is False
    assert second["monitor"]["fields"][0]["stable"] is True
    assert second["monitor"]["fields"][0]["transition"] == "stabilized"
    assert second["texts"] == {"NAME": "ok"}
    assert "text" not in second["monitor"]["fields"][0]


def test_background_monitor_exposes_latest_values_but_keeps_status_text_free() -> None:
    tracker = ObservationStabilityTracker(confirmation_count=2)
    tracker.observe(
        [
            {
                "name": "OLD",
                "field_type": "text",
                "validation_status": "valid",
                "is_valid": True,
                "text": "old value",
            }
        ]
    )
    controller = BackgroundMonitorController(
        monitor=tracker,
        execution_lock=RLock(),
        monitor_lock=RLock(),
        default_interval_seconds=0.5,
    )
    pipeline = StaticPipeline()
    items = parse_batch_items(
        pipeline,
        {
            "items": [
                {
                    "name": "NAME",
                    "label": "姓名",
                    "field_type": "text",
                    "rect": [1, 2, 3, 4],
                }
            ]
        },
    )

    initial_latest = controller.latest()
    assert initial_latest["snapshot_available"] is False
    assert initial_latest["texts"] == {}
    assert initial_latest["fields"] == []
    assert controller.start(pipeline, items, fast_mode=False, interval_seconds=0.5) is True
    deadline = time.monotonic() + 2.0
    while controller.status()["tick_count"] < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    running_status = controller.status()

    assert running_status["running"] is True
    assert running_status["success"] is True
    assert running_status["mode"] == "background"
    assert running_status["performance_mode"] == "low_load"
    assert running_status["attempt_limit"] == 1
    assert running_status["field_count"] == 1
    assert running_status["fast_mode"] is False
    assert running_status["last_empty_count"] == 0
    assert running_status["last_review_count"] == 0
    assert running_status["fields"][0]["stable"] is True
    assert [field["name"] for field in running_status["fields"]] == ["NAME"]
    assert "text" not in running_status["fields"][0]

    latest = controller.latest()
    assert latest["running"] is True
    assert latest["snapshot_available"] is True
    assert latest["snapshot_tick_count"] == latest["tick_count"]
    assert latest["texts"] == {"NAME": "ok"}
    assert latest["field_count"] == 1
    assert latest["empty_count"] == 0
    assert latest["review_count"] == 0
    assert latest["fields"][0] == {
        "name": "NAME",
        "label": "姓名",
        "field_type": "text",
        "success": True,
        "text": "ok",
        "confidence": 1.0,
        "validation_status": "valid",
        "is_valid": True,
        "requires_review": False,
        "error_code": None,
    }
    for forbidden_key in ("region", "rect", "boxes", "debug_image_path", "request_id"):
        assert forbidden_key not in latest["fields"][0]
    assert controller.stop() is True

    deadline = time.monotonic() + 2.0
    while controller.status()["running"] and time.monotonic() < deadline:
        time.sleep(0.02)

    assert controller.status()["running"] is False
    assert controller.latest()["texts"] == {"NAME": "ok"}


def test_background_monitor_request_rejects_debug_images_and_invalid_intervals() -> None:
    with pytest.raises(ValueError, match="save_debug must be false"):
        parse_background_monitor_start_request(
            StaticPipeline(),
            {"items": [{"name": "HR", "rect": [1, 2, 3, 4]}], "save_debug": True},
            default_interval_seconds=2.0,
        )

    with pytest.raises(ValueError, match="interval_seconds must be between"):
        parse_monitor_interval(0.1)


def test_server_rejects_unsupported_field_type() -> None:
    with pytest.raises(ValueError, match="field_type must be one of"):
        parse_field_type("clinical_diagnosis")


def test_server_batch_uses_one_shared_capture_when_pipeline_supports_it() -> None:
    np = pytest.importorskip("numpy")
    pipeline = SharedBatchPipeline(np.zeros((80, 80, 4), dtype=np.uint8))

    payload = build_batch_ocr_response(
        pipeline,
        {
            "fast_mode": True,
            "save_debug": False,
            "items": [
                {"name": "HR", "label": "心室率", "rect": [1, 2, 3, 4]},
                {"name": "PR", "label": "PR 间期", "rect": [5, 6, 7, 8]},
            ],
        },
    )

    assert payload["success"] is True
    assert payload["shared_capture"] is True
    assert payload["texts"] == {"HR": "value-1", "PR": "value-2"}
    assert payload["summary"] == "心室率: value-1\nPR 间期: value-2"
    assert pipeline.capture_service.calls == [ScreenRegion(left=-7, top=-6, width=27, height=28)]
    assert pipeline.ocr_engine.calls == [False, False]


def test_server_batch_falls_back_when_shared_capture_texts_are_all_empty() -> None:
    np = pytest.importorskip("numpy")
    pipeline = EmptySharedFallbackPipeline(np.zeros((80, 80, 4), dtype=np.uint8))

    payload = build_batch_ocr_response(
        pipeline,
        {
            "fast_mode": True,
            "save_debug": False,
            "items": [
                {"name": "HR", "label": "心室率", "rect": [1, 2, 3, 4]},
                {"name": "PR", "label": "PR 间期", "rect": [5, 6, 7, 8]},
            ],
        },
    )

    assert payload["success"] is True
    assert payload["shared_capture_fallback"] is True
    assert payload["texts"] == {"HR": "stable-1", "PR": "stable-2"}
    assert payload["summary"] == "心室率: stable-1\nPR 间期: stable-2"


def test_monitor_low_load_mode_suppresses_all_empty_individual_retry() -> None:
    np = pytest.importorskip("numpy")
    pipeline = EmptySharedFallbackPipeline(np.zeros((80, 80, 4), dtype=np.uint8))
    tracker = ObservationStabilityTracker(confirmation_count=2)

    payload = build_monitor_tick_response(
        pipeline,
        tracker,
        {
            "fast_mode": True,
            "items": [
                {"name": "HR", "label": "心室率", "rect": [1, 2, 3, 4]},
                {"name": "PR", "label": "PR 间期", "rect": [5, 6, 7, 8]},
            ],
        },
    )

    assert payload["attempt_limit"] == 1
    assert payload["shared_capture_fallback_suppressed"] is True
    assert payload["empty_count"] == 2
    assert pipeline.individual_calls == 0
    assert len(pipeline.ocr_engine.calls) == 2


@pytest.mark.parametrize("raise_on_fallback", [False, True])
def test_shared_crop_fallback_failure_preserves_primary_unreadable_result(
    raise_on_fallback: bool,
) -> None:
    np = pytest.importorskip("numpy")
    pipeline = types.SimpleNamespace(
        ocr_engine=EmptyThenFailOCREngine(raise_on_fallback=raise_on_fallback)
    )
    region = ScreenRegion(left=0, top=0, width=20, height=10)

    result, fallback_used, strategy, _elapsed_ms = recognize_cropped_image_with_fallbacks(
        pipeline,
        np.zeros((10, 20, 3), dtype=np.uint8),
        region,
        region,
        fast_mode=False,
    )

    assert result.success is True
    assert result.text == ""
    assert fallback_used is False
    assert strategy == ""


def test_server_batch_skips_shared_capture_when_union_violates_policy() -> None:
    np = pytest.importorskip("numpy")
    pipeline = EmptySharedFallbackPipeline(np.zeros((80, 80, 4), dtype=np.uint8))

    payload = build_batch_ocr_response(
        pipeline,
        {
            "items": [
                {"name": "LEFT", "rect": [0, 0, 10, 10]},
                {"name": "RIGHT", "rect": [90_000, 0, 10, 10]},
            ]
        },
    )

    assert payload["success"] is True
    assert payload["shared_capture_skipped"] is True
    assert payload["shared_capture_skip_reason"] == "capture_policy"
    assert pipeline.capture_service.calls == []
    assert pipeline.individual_calls == 2


def test_server_single_item_batch_avoids_shared_capture_overhead() -> None:
    np = pytest.importorskip("numpy")
    pipeline = EmptySharedFallbackPipeline(np.zeros((80, 80, 4), dtype=np.uint8))

    payload = build_batch_ocr_response(
        pipeline,
        {"items": [{"name": "ONLY", "rect": [1, 2, 3, 4]}]},
    )

    assert payload["success"] is True
    assert payload["texts"] == {"ONLY": "stable-1"}
    assert pipeline.capture_service.calls == []
    assert pipeline.individual_calls == 1


def test_server_fast_mode_falls_back_when_text_is_empty() -> None:
    pipeline = FastModeFallbackPipeline()

    payload = build_single_ocr_response(
        pipeline,
        {"rect": [1, 2, 3, 4], "fast_mode": True, "save_debug": True},
    )

    assert payload["success"] is True
    assert payload["text"] == "fallback text"
    assert payload["fallback_used"] is True
    assert payload["fallback_strategy"] == "full_detection"
    assert pipeline.calls == [
        {"save_debug": True, "use_det": False},
        {"save_debug": True, "use_det": True},
    ]


def test_server_reliable_mode_falls_back_to_whole_crop_when_detection_is_empty() -> None:
    pipeline = WholeCropFallbackPipeline()

    payload = build_single_ocr_response(
        pipeline,
        {"rect": [1, 2, 3, 4], "fast_mode": False, "save_debug": True},
    )

    assert payload["success"] is True
    assert payload["text"] == "whole crop text"
    assert payload["fallback_used"] is True
    assert payload["fallback_strategy"] == "whole_crop"
    assert pipeline.calls == [
        {"save_debug": True, "use_det": True},
        {"save_debug": True, "use_det": False},
    ]


def test_server_stops_empty_field_retries_after_bounded_attempt_limit() -> None:
    pipeline = EmptyFallbackPipeline()

    payload = build_single_ocr_response(
        pipeline,
        {"rect": [1, 2, 3, 4], "fast_mode": False, "save_debug": True},
    )

    assert payload["success"] is True
    assert payload["text"] == ""
    assert payload["attempt_limit"] == 2
    assert pipeline.calls == [
        {"save_debug": True, "use_det": True},
        {"save_debug": True, "use_det": False},
    ]


@pytest.mark.parametrize(
    ("fallback_text", "selected_name", "removed_name"),
    [
        ("fallback text", "fallback.png", "primary.png"),
        ("", "primary.png", "fallback.png"),
        (None, "primary.png", "fallback.png"),
    ],
)
def test_single_fallback_keeps_only_the_selected_debug_frame(
    tmp_path: Path,
    fallback_text: str | None,
    selected_name: str,
    removed_name: str,
) -> None:
    primary_path = tmp_path / "primary.png"
    fallback_path = tmp_path / "fallback.png"
    primary_path.write_bytes(b"primary")
    fallback_path.write_bytes(b"fallback")
    pipeline = DebugPathFallbackPipeline(primary_path, fallback_path, fallback_text)

    payload = build_single_ocr_response(
        pipeline,
        {"rect": [1, 2, 3, 4], "save_debug": True},
    )

    assert payload["debug_image_path"] == str(tmp_path / selected_name)
    assert (tmp_path / selected_name).exists()
    assert not (tmp_path / removed_name).exists()


def test_server_batch_rejects_empty_items() -> None:
    with pytest.raises(ValueError):
        parse_batch_items(StaticPipeline(), {"items": []})


def test_server_batch_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="name must be unique"):
        parse_batch_items(
            StaticPipeline(),
            {
                "items": [
                    {"name": "HR", "rect": [1, 2, 3, 4]},
                    {"name": "HR", "rect": [5, 6, 7, 8]},
                ]
            },
        )


def test_server_batch_rejects_excessive_item_count() -> None:
    items = [{"name": f"F{index}", "rect": [1, 2, 3, 4]} for index in range(65)]

    with pytest.raises(ValueError, match="at most 64"):
        parse_batch_items(StaticPipeline(), {"items": items})


def test_ahk_workbench_debug_mode_does_not_send_path() -> None:
    helper = Path("scripts/ahk_example.ahk").read_text(encoding="utf-8")

    assert '""save_debug"": " . SvsBoolJson(SaveDebug)' in helper
    assert '""save_debug"": """' not in helper


def test_ahk_workbench_exposes_read_only_buttons_without_function_hotkeys() -> None:
    workbench = Path("scripts/ahk_example.ahk").read_text(encoding="utf-8")

    for button_label in (
        "选择目标窗口",
        "检查服务",
        "读取 HR",
        "批量心电",
        "拾取坐标",
        "启动后台监测",
        "停止监测",
        "监测状态",
    ):
        assert button_label in workbench
    for hotkey in ("F1::", "F2::", "F4::", "F5::", "F8::", "F9::", "F12::"):
        assert hotkey not in workbench
    assert "SvsOCRBatchJson" in workbench
    assert "#Include" not in workbench
    assert "%Title%" not in workbench
    assert "只读" in workbench
    assert "Click" not in workbench
    assert "SendInput" not in workbench


def test_ahk_workbench_patient_fields_and_picker_are_integrated() -> None:
    workbench = Path("scripts/ahk_example.ahk").read_text(encoding="utf-8")

    for token in ("AGE 年龄", "EXAM_TIME 检查时间", "EDIT_TIME 编辑时间"):
        assert token in workbench
    assert "NAME|GENDER|DOB|AGE|PAT_ID" in workbench
    assert "GetOrSelectTargetWindow(false)" in workbench
    assert "CaptureTargetWindowFromMouse" in workbench
    assert "窗口句柄不稳定时可切到屏幕坐标" in workbench


def test_ahk_workbench_uses_picker_and_safe_batch_reads() -> None:
    workbench = Path("scripts/ahk_example.ahk").read_text(encoding="utf-8")

    assert "global OCR_FAST_MODE := false" in workbench
    assert "global MONITOR_FAST_MODE := true" in workbench
    assert "global MONITOR_INTERVAL_SECONDS := 5" in workbench
    assert "极速模式（速度优先，可能降低准确率）" in workbench
    assert "InputBox" not in workbench
    assert "Reload" not in workbench
    assert "Gui, PrefixPicker:Add, ListBox" in workbench
    assert "SvsOCRBatchJson(ItemsJson, OCR_FAST_MODE)" in workbench
    assert '""label"":""" . SvsJsonEscape(Label)' in workbench
    assert "SvsParseJsonSummary(ApiJson)" in workbench
    assert "BuildBatchDiagnostics(ApiJson)" in workbench
    assert "SvsBuildClientErrorJson" in workbench
    assert "Http.SetTimeouts(1000, 1000, 3000, 30000)" in workbench
    assert "LastJson := ApiJson" in workbench
    assert "[未配置坐标]" in workbench
    assert "立即 OCR：" in workbench
    assert "NormalizeTargetWindowID(WindowID)" in workbench
    assert 'DllCall("GetAncestor"' in workbench
    assert "SetRegionByPrefix(Prefix, X, Y, W, H)" in workbench
    assert '""screen_rect"":[' in workbench
    assert (
        'global COORDINATE_MODE := "window"' in workbench
        or 'global COORDINATE_MODE := "screen"' in workbench
    )
    assert "ToggleCoordinateMode()" in workbench
    assert 'if (IsScreenCoordinateMode())\n        COORDINATE_MODE := "window"' in workbench
    assert 'else\n        COORDINATE_MODE := "screen"' in workbench
    assert "SvsWriteScriptAtomically(TargetScript, ScriptContent)" in workbench
    assert "FileDelete, %TargetScript%" not in workbench
    assert "BuildScreenRect(ActiveID" in workbench
    assert "SvsHealthOcrReady(HealthJson)" in workbench
    assert 'SvsParseJsonBoolean(Json, "ocr_ready")' in workbench
    assert 'ModePattern := "im)^(' in workbench
    assert "ReplacementCount, 1" in workbench
    assert "后台监测仅支持屏幕绝对坐标" in workbench
    assert "部分或全部字段读取失败" in workbench
    assert "当前服务正在使用模拟 OCR" in workbench
    assert "GetFieldType(Prefix)" in workbench
    assert "SvsOCRMonitorTickJson" in workbench
    assert "SvsStartMonitorJson" in workbench
    assert "SvsStopMonitorJson" in workbench
    assert "StartBackgroundMonitor(Mode)" in workbench
    assert "StopBackgroundMonitor()" in workbench
    assert "SvsHealthBackgroundMonitorAvailable" in workbench
    assert "SvsHealthMonitorLatestAvailable" in workbench
    assert "SvsGetMonitorLatestJson" in workbench
    assert '"/monitor/latest"' in workbench
    assert "实时参数屏" in workbench
    assert "BuildLiveDataSummary" in workbench
    assert "BuildLiveDataScreenGui" in workbench
    assert "global LIVE_DATA_SCREEN_OPEN, LiveDataTitle, LiveDataHeader" in workbench
    assert "LiveDataValues, LiveDataPrivacy" in workbench
    assert 'A_Args[1] = "--live-screen-selftest"' in workbench
    assert "3GuiSize:" in workbench
    assert "ResizeLiveDataScreen(A_GuiWidth, A_GuiHeight)" in workbench
    assert "Gui, 3:Show, w680 h680, 后台实时参数" in workbench
    assert "GuiControl, 3:Move, LiveDataValues" in workbench
    assert "SvsJsonHasNamedText" in workbench
    assert "RefreshMonitorLiveStatus()" in workbench
    assert "RefreshMonitorStatusTimer:" in workbench
    assert "后台监测：" in workbench
    assert "心跳" in workbench
    assert "低负载" in workbench
    assert '""field_type"":""" . SvsJsonEscape(FieldType)' in workbench
    assert 'A_Args[1] = "--syntax-check"' in workbench
    assert "LastJson := ApiJson" in workbench
    assert "Gui, 1:Show" in workbench
    assert "Gui, 1:Hide" in workbench
    assert "Gui, 1:Default" in workbench
    assert "Gui, Show" not in workbench
    assert "Gui, Hide" not in workbench
    assert "GuiControl,," not in workbench


def test_ahk_workbench_is_utf8_bom_for_autohotkey_v1() -> None:
    assert Path("scripts/ahk_example.ahk").read_bytes().startswith(b"\xef\xbb\xbf")


def test_offline_workbench_keeps_work_computer_coordinates_and_monitor_helpers() -> None:
    offline_root = Path("dist/ScreenVisionSentinel_Offline_2026年07月_08日测试")
    workbench = (offline_root / "scripts/ahk_example.ahk").read_text(encoding="utf-8")

    assert (offline_root / "scripts/ahk_example.ahk").read_bytes().startswith(b"\xef\xbb\xbf")
    assert 'global COORDINATE_MODE := "screen"' in workbench
    assert 'global COORDINATE_PROFILE_NAME := "工作电脑（2026-07-08 人工测量）"' in workbench
    assert "HR_X := 1038" in workbench
    assert "HR_Y := 186" in workbench
    assert "SvsOCRMonitorTickJson" in workbench
    assert "SvsGetMonitorStatusJson" in workbench
    assert "SvsStartMonitorJson" in workbench
    assert "SvsStopMonitorJson" in workbench
    assert "SvsHealthBackgroundMonitorAvailable" in workbench
    assert "SvsHealthMonitorLatestAvailable" in workbench
    assert "SvsGetMonitorLatestJson" in workbench
    assert "实时参数屏" in workbench
    assert "global LIVE_DATA_SCREEN_OPEN, LiveDataTitle, LiveDataHeader" in workbench
    assert "LiveDataValues, LiveDataPrivacy" in workbench
    assert 'A_Args[1] = "--live-screen-selftest"' in workbench
    assert "3GuiSize:" in workbench
    assert "ResizeLiveDataScreen(A_GuiWidth, A_GuiHeight)" in workbench
    assert "global MONITOR_INTERVAL_SECONDS := 5" in workbench
    assert "RefreshMonitorLiveStatus()" in workbench
    assert 'if (IsScreenCoordinateMode())\n        COORDINATE_MODE := "window"' in workbench
    assert "SvsWriteScriptAtomically(TargetScript, ScriptContent)" in workbench
    assert "FileDelete, %TargetScript%" not in workbench
    assert 'ModePattern := "im)^(' in workbench
    assert "后台监测仅支持屏幕绝对坐标" in workbench
    assert 'A_Args[1] = "--syntax-check"' in workbench


def test_offline_python_sources_match_root_sources() -> None:
    offline_root = Path("dist/ScreenVisionSentinel_Offline_2026年07月_08日测试")
    source_root = Path("src/screenvision_sentinel")
    offline_source_root = offline_root / source_root
    source_files = {path.relative_to(source_root) for path in source_root.rglob("*.py")}
    offline_files = {
        path.relative_to(offline_source_root) for path in offline_source_root.rglob("*.py")
    }

    assert offline_files == source_files
    for relative_path in source_files:
        assert (offline_source_root / relative_path).read_bytes() == (
            source_root / relative_path
        ).read_bytes()


def test_start_server_uses_local_src_before_installed_package() -> None:
    script_path = Path("start_server.bat")
    script = script_path.read_text(encoding="utf-8")
    script_bytes = script_path.read_bytes()

    assert "chcp 65001 >nul" in script
    assert b"\n" not in script_bytes.replace(b"\r\n", b"")
    assert 'set "SVS_ROOT=%~dp0"' in script
    assert 'set "PYTHONPATH=%SVS_ROOT%\\src;%PYTHONPATH%"' in script
    assert '"%SVS_ROOT%\\.venv\\Scripts\\python.exe" -m screenvision_sentinel.server' in script


class FakeCaptureService:
    def __init__(self, *, success: bool = True, error: str | None = None) -> None:
        self._success = success
        self._error = error

    def capture_region(
        self,
        region: ScreenRegion,
        save_to_disk: bool = True,
    ) -> ScreenshotResult:
        return ScreenshotResult(
            region=region,
            image_path=None,
            image_data=object() if self._success else None,
            success=self._success,
            error=self._error,
        )


class SharedBatchPipeline:
    def __init__(self, image_data: object) -> None:
        self.policy = CapturePolicy()
        self.capture_service = SharedBatchCaptureService(image_data)
        self.ocr_engine = SharedBatchOCREngine()
        self.debug_storage = None


class SharedBatchCaptureService:
    def __init__(self, image_data: object) -> None:
        self.image_data = image_data
        self.calls: list[ScreenRegion] = []

    def capture_region(
        self,
        region: ScreenRegion,
        save_to_disk: bool = True,
    ) -> ScreenshotResult:
        self.calls.append(region)
        return ScreenshotResult(
            region=region,
            image_path=None,
            image_data=self.image_data,
            success=True,
        )


class SharedBatchOCREngine:
    name = "shared-batch"

    def __init__(self) -> None:
        self.calls: list[bool] = []

    def recognize(self, image_source: object, use_det: bool = True) -> OCRResult:
        self.calls.append(use_det)
        return OCRResult(
            text=f"value-{len(self.calls)}",
            confidence=1.0,
            boxes=(),
            elapsed_ms=1.0,
            engine_name=self.name,
            success=True,
        )


class EmptySharedFallbackPipeline:
    def __init__(self, image_data: object) -> None:
        self.policy = CapturePolicy()
        self.capture_service = SharedBatchCaptureService(image_data)
        self.ocr_engine = EmptySharedOCREngine()
        self.debug_storage = None
        self.individual_calls = 0

    def capture_and_ocr(
        self,
        region: ScreenRegion,
        *,
        save_debug: bool = False,
        use_det: bool = True,
    ) -> VisionResult:
        self.individual_calls += 1
        return VisionResult(
            success=True,
            text=f"stable-{self.individual_calls}",
            confidence=1.0,
            boxes=(),
            elapsed_ms=1.0,
            capture_elapsed_ms=0.5,
            ocr_elapsed_ms=0.5,
            engine_name="stable",
            region=region,
            request_id=f"stable-{self.individual_calls}",
        )


class EmptySharedOCREngine:
    name = "empty-shared"

    def __init__(self) -> None:
        self.calls: list[bool] = []

    def recognize(self, image_source: object, use_det: bool = True) -> OCRResult:
        self.calls.append(use_det)
        return OCRResult(
            text="",
            confidence=0.0,
            boxes=(),
            elapsed_ms=1.0,
            engine_name=self.name,
            success=True,
        )


class VariantRapidOCRBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[int, ...], bool]] = []

    def __call__(self, image: object, use_det: bool = True) -> tuple[list[list[object]], float]:
        shape = tuple(getattr(image, "shape", ()))
        self.calls.append((shape, use_det))
        if len(self.calls) == 1:
            return [], 0.0
        return [["70", 0.98]], 1.0


class DetectionVariantRapidOCRBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[int, ...], bool]] = []

    def __call__(self, image: object, use_det: bool = True) -> tuple[list[object], float]:
        shape = tuple(getattr(image, "shape", ()))
        self.calls.append((shape, use_det))
        if len(self.calls) == 1:
            return [], 0.0
        return [
            [
                [[40.0, 20.0], [80.0, 20.0], [80.0, 60.0], [40.0, 60.0]],
                "70",
                0.98,
            ]
        ], 1.0


class EmptyThenFailOCREngine:
    name = "empty-then-fail"

    def __init__(self, *, raise_on_fallback: bool) -> None:
        self.calls = 0
        self.raise_on_fallback = raise_on_fallback

    def recognize(self, image_source: object, use_det: bool = True) -> OCRResult:
        self.calls += 1
        if self.calls == 1:
            return OCRResult(
                text="",
                confidence=0.0,
                boxes=(),
                elapsed_ms=1.0,
                engine_name=self.name,
                success=True,
            )
        if self.raise_on_fallback:
            raise RuntimeError("fallback failed")
        return OCRResult(
            text="",
            confidence=0.0,
            boxes=(),
            elapsed_ms=1.0,
            engine_name=self.name,
            success=False,
            error="fallback failed",
        )


class StaticPipeline:
    def __init__(self) -> None:
        self.policy = CapturePolicy()

    def capture_and_ocr(
        self,
        region: ScreenRegion,
        *,
        save_debug: bool = False,
        use_det: bool = True,
    ) -> VisionResult:
        return VisionResult(
            success=True,
            text="ok",
            confidence=1.0,
            boxes=(),
            elapsed_ms=1.0,
            capture_elapsed_ms=0.5,
            ocr_elapsed_ms=0.5,
            engine_name="mock",
            region=region,
            request_id="test",
        )


class FailingPipeline(StaticPipeline):
    def capture_and_ocr(
        self,
        region: ScreenRegion,
        *,
        save_debug: bool = False,
        use_det: bool = True,
    ) -> VisionResult:
        return VisionResult(
            success=False,
            text="",
            confidence=0.0,
            boxes=(),
            elapsed_ms=1.0,
            capture_elapsed_ms=0.5,
            ocr_elapsed_ms=0.5,
            engine_name="failing",
            region=region,
            request_id="failing",
            error_code="ocr_failed",
            error_message="OCR failed",
        )


class NonFiniteConfidencePipeline(StaticPipeline):
    ocr_confidence_threshold = 0.85

    def capture_and_ocr(
        self,
        region: ScreenRegion,
        *,
        save_debug: bool = False,
        use_det: bool = True,
    ) -> VisionResult:
        return VisionResult(
            success=True,
            text="ok",
            confidence=float("nan"),
            boxes=(),
            elapsed_ms=1.0,
            capture_elapsed_ms=0.5,
            ocr_elapsed_ms=0.5,
            engine_name="mock",
            region=region,
            request_id="non-finite",
        )


class FastModeFallbackPipeline:
    def __init__(self) -> None:
        self.policy = CapturePolicy()
        self.calls: list[dict[str, object]] = []

    def capture_and_ocr(
        self,
        region: ScreenRegion,
        *,
        save_debug: bool = False,
        use_det: bool = True,
    ) -> VisionResult:
        self.calls.append({"save_debug": save_debug, "use_det": use_det})
        request_id = f"test-{len(self.calls)}"
        return VisionResult(
            success=True,
            text="fallback text" if use_det else "",
            confidence=1.0,
            boxes=(),
            elapsed_ms=1.0,
            capture_elapsed_ms=0.5,
            ocr_elapsed_ms=0.5,
            engine_name="mock",
            region=region,
            request_id=request_id,
        )


class WholeCropFallbackPipeline:
    def __init__(self) -> None:
        self.policy = CapturePolicy()
        self.calls: list[dict[str, object]] = []

    def capture_and_ocr(
        self,
        region: ScreenRegion,
        *,
        save_debug: bool = False,
        use_det: bool = True,
    ) -> VisionResult:
        self.calls.append({"save_debug": save_debug, "use_det": use_det})
        request_id = f"test-{len(self.calls)}"
        return VisionResult(
            success=True,
            text="" if use_det else "whole crop text",
            confidence=1.0,
            boxes=(),
            elapsed_ms=1.0,
            capture_elapsed_ms=0.5,
            ocr_elapsed_ms=0.5,
            engine_name="mock",
            region=region,
            request_id=request_id,
        )


class EmptyFallbackPipeline:
    def __init__(self) -> None:
        self.policy = CapturePolicy()
        self.calls: list[dict[str, object]] = []

    def capture_and_ocr(
        self,
        region: ScreenRegion,
        *,
        save_debug: bool = False,
        use_det: bool = True,
    ) -> VisionResult:
        self.calls.append({"save_debug": save_debug, "use_det": use_det})
        return VisionResult(
            success=True,
            text="",
            confidence=0.0,
            boxes=(),
            elapsed_ms=1.0,
            capture_elapsed_ms=0.5,
            ocr_elapsed_ms=0.5,
            engine_name="mock",
            region=region,
            request_id=f"empty-{len(self.calls)}",
        )


class DebugPathFallbackPipeline:
    def __init__(
        self,
        primary_path: Path,
        fallback_path: Path,
        fallback_text: str | None,
    ) -> None:
        self.policy = CapturePolicy()
        self.paths = [primary_path, fallback_path]
        self.fallback_text = fallback_text
        self.calls = 0

    def capture_and_ocr(
        self,
        region: ScreenRegion,
        *,
        save_debug: bool = False,
        use_det: bool = True,
    ) -> VisionResult:
        path = self.paths[self.calls]
        self.calls += 1
        is_primary = self.calls == 1
        success = is_primary or self.fallback_text is not None
        text = "" if is_primary or self.fallback_text is None else self.fallback_text
        return VisionResult(
            success=success,
            text=text,
            confidence=1.0 if text else 0.0,
            boxes=(),
            elapsed_ms=1.0,
            capture_elapsed_ms=0.5,
            ocr_elapsed_ms=0.5,
            engine_name="debug-fallback",
            region=region,
            request_id=f"debug-{self.calls}",
            debug_image_path=path if save_debug else None,
            error_code=None if success else "ocr_failed",
            error_message=None if success else "fallback failed",
        )
