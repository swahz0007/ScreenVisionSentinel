"""Shared models returned by the vision pipeline."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path

from screenvision_sentinel.capture.base import ScreenRegion
from screenvision_sentinel.ocr.base import OCRBoundingBox

ERROR_INVALID_REGION = "invalid_region"
ERROR_CAPTURE_FAILED = "capture_failed"
ERROR_OCR_FAILED = "ocr_failed"


@dataclass(frozen=True)
class VisionResult:
    """Structured result for a single capture-and-OCR request."""

    success: bool
    text: str
    confidence: float
    boxes: tuple[OCRBoundingBox, ...]
    elapsed_ms: float
    capture_elapsed_ms: float
    ocr_elapsed_ms: float
    engine_name: str
    region: ScreenRegion
    request_id: str
    debug_image_path: Path | None = None
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable payload without leaking logs by default."""
        confidence = _finite_float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            confidence = 0.0
        payload: dict[str, object] = {
            "success": self.success,
            "text": self.text,
            "confidence": confidence,
            "boxes": [asdict(box) for box in self.boxes],
            "elapsed_ms": max(0.0, _finite_float(self.elapsed_ms)),
            "capture_elapsed_ms": max(0.0, _finite_float(self.capture_elapsed_ms)),
            "ocr_elapsed_ms": max(0.0, _finite_float(self.ocr_elapsed_ms)),
            "engine_name": self.engine_name,
            "region": asdict(self.region),
            "request_id": self.request_id,
        }
        if self.debug_image_path is not None:
            payload["debug_image_path"] = str(self.debug_image_path)
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        if self.error_message is not None:
            payload["error"] = self.error_message
            payload["error_message"] = self.error_message
        return payload


def _finite_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    numeric_value = float(value)
    return numeric_value if math.isfinite(numeric_value) else 0.0
