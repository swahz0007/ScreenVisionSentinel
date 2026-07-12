"""Unified capture and OCR pipeline shared by CLI and local server."""

from __future__ import annotations

import logging
import math
import uuid
from pathlib import Path
from time import perf_counter

from screenvision_sentinel.capture.base import BaseCaptureService, ScreenRegion
from screenvision_sentinel.ocr.base import BaseOCREngine
from screenvision_sentinel.vision.debug_storage import DebugImageStorage
from screenvision_sentinel.vision.models import (
    ERROR_CAPTURE_FAILED,
    ERROR_INVALID_REGION,
    ERROR_OCR_FAILED,
    VisionResult,
)
from screenvision_sentinel.vision.policy import CapturePolicy, CapturePolicyError

LOGGER = logging.getLogger(__name__)


class VisionPipeline:
    """Run validated screen capture, OCR, optional debug storage, and safe logging."""

    def __init__(
        self,
        *,
        capture_service: BaseCaptureService,
        ocr_engine: BaseOCREngine,
        policy: CapturePolicy | None = None,
        debug_storage: DebugImageStorage | None = None,
        ocr_confidence_threshold: float = 0.0,
    ) -> None:
        self.capture_service = capture_service
        self.ocr_engine = ocr_engine
        self.policy = policy or CapturePolicy()
        self.debug_storage = debug_storage
        threshold = float(ocr_confidence_threshold)
        self.ocr_confidence_threshold = (
            min(1.0, max(0.0, threshold)) if math.isfinite(threshold) else 0.0
        )

    def capture_and_ocr(
        self,
        region: ScreenRegion,
        *,
        save_debug: bool = False,
        use_det: bool = True,
    ) -> VisionResult:
        """Capture one region and OCR it; never performs mouse or keyboard actions."""
        request_id = uuid.uuid4().hex[:12]
        started_at = perf_counter()

        try:
            self.policy.validate(region)
        except CapturePolicyError as exc:
            elapsed_ms = self._elapsed_ms(started_at)
            self._log_failure(request_id, ERROR_INVALID_REGION, elapsed_ms)
            return self._failure_result(
                request_id=request_id,
                region=region,
                elapsed_ms=elapsed_ms,
                error_code=ERROR_INVALID_REGION,
                error_message=str(exc),
            )

        capture_started_at = perf_counter()
        try:
            capture_result = self.capture_service.capture_region(region, save_to_disk=False)
        except Exception as exc:
            capture_elapsed_ms = self._elapsed_ms(capture_started_at)
            elapsed_ms = self._elapsed_ms(started_at)
            LOGGER.error(
                "capture service failed request_id=%s error_type=%s",
                request_id,
                type(exc).__name__,
            )
            self._log_failure(request_id, ERROR_CAPTURE_FAILED, elapsed_ms)
            return self._failure_result(
                request_id=request_id,
                region=region,
                elapsed_ms=elapsed_ms,
                capture_elapsed_ms=capture_elapsed_ms,
                error_code=ERROR_CAPTURE_FAILED,
                error_message=f"capture service failed: {type(exc).__name__}",
            )
        capture_elapsed_ms = self._elapsed_ms(capture_started_at)
        if not capture_result.success or capture_result.image_data is None:
            elapsed_ms = self._elapsed_ms(started_at)
            self._log_failure(request_id, ERROR_CAPTURE_FAILED, elapsed_ms)
            return self._failure_result(
                request_id=request_id,
                region=region,
                elapsed_ms=elapsed_ms,
                capture_elapsed_ms=capture_elapsed_ms,
                error_code=ERROR_CAPTURE_FAILED,
                error_message=capture_result.error or "capture failed",
            )

        debug_image_path = None
        if save_debug and self.debug_storage is not None:
            try:
                debug_image_path = self.debug_storage.save(capture_result.image_data)
            except Exception as exc:
                LOGGER.warning(
                    "debug image save failed request_id=%s error_type=%s",
                    request_id,
                    type(exc).__name__,
                )

        ocr_started_at = perf_counter()
        try:
            ocr_result = self.ocr_engine.recognize(capture_result.image_data, use_det=use_det)
        except Exception as exc:
            ocr_elapsed_ms = self._elapsed_ms(ocr_started_at)
            elapsed_ms = self._elapsed_ms(started_at)
            LOGGER.error(
                "OCR engine failed request_id=%s error_type=%s",
                request_id,
                type(exc).__name__,
            )
            self._log_failure(request_id, ERROR_OCR_FAILED, elapsed_ms)
            return self._failure_result(
                request_id=request_id,
                region=region,
                elapsed_ms=elapsed_ms,
                capture_elapsed_ms=capture_elapsed_ms,
                ocr_elapsed_ms=ocr_elapsed_ms,
                error_code=ERROR_OCR_FAILED,
                error_message=f"OCR engine failed: {type(exc).__name__}",
            )
        ocr_elapsed_ms = self._elapsed_ms(ocr_started_at)
        elapsed_ms = self._elapsed_ms(started_at)

        if not ocr_result.success:
            self._log_failure(request_id, ERROR_OCR_FAILED, elapsed_ms)
            return self._failure_result(
                request_id=request_id,
                region=region,
                elapsed_ms=elapsed_ms,
                capture_elapsed_ms=capture_elapsed_ms,
                ocr_elapsed_ms=ocr_elapsed_ms,
                engine_name=ocr_result.engine_name,
                debug_image_path=debug_image_path,
                error_code=ERROR_OCR_FAILED,
                error_message=ocr_result.error or "OCR failed",
            )

        LOGGER.info(
            "vision request completed request_id=%s success=true engine=%s elapsed_ms=%.1f "
            "capture_elapsed_ms=%.1f ocr_elapsed_ms=%.1f result_chars=%d",
            request_id,
            ocr_result.engine_name,
            elapsed_ms,
            capture_elapsed_ms,
            ocr_elapsed_ms,
            len(ocr_result.text),
        )
        return VisionResult(
            success=True,
            text=ocr_result.text,
            confidence=ocr_result.confidence,
            boxes=ocr_result.boxes,
            elapsed_ms=elapsed_ms,
            capture_elapsed_ms=capture_elapsed_ms,
            ocr_elapsed_ms=ocr_elapsed_ms,
            engine_name=ocr_result.engine_name,
            region=region,
            request_id=request_id,
            debug_image_path=debug_image_path,
        )

    @staticmethod
    def _elapsed_ms(started_at: float) -> float:
        return (perf_counter() - started_at) * 1000

    def _failure_result(
        self,
        *,
        request_id: str,
        region: ScreenRegion,
        elapsed_ms: float,
        error_code: str,
        error_message: str,
        capture_elapsed_ms: float = 0.0,
        ocr_elapsed_ms: float = 0.0,
        engine_name: str | None = None,
        debug_image_path: Path | None = None,
    ) -> VisionResult:
        return VisionResult(
            success=False,
            text="",
            confidence=0.0,
            boxes=(),
            elapsed_ms=elapsed_ms,
            capture_elapsed_ms=capture_elapsed_ms,
            ocr_elapsed_ms=ocr_elapsed_ms,
            engine_name=engine_name or self.ocr_engine.name,
            region=region,
            request_id=request_id,
            debug_image_path=debug_image_path,
            error_code=error_code,
            error_message=error_message,
        )

    @staticmethod
    def _log_failure(request_id: str, error_code: str, elapsed_ms: float) -> None:
        LOGGER.info(
            "vision request completed request_id=%s success=false error_code=%s elapsed_ms=%.1f",
            request_id,
            error_code,
            elapsed_ms,
        )
