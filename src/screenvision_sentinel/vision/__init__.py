"""Unified vision pipeline for capture, OCR, and safe debug artifacts."""

from screenvision_sentinel.vision.debug_storage import DebugImageStorage
from screenvision_sentinel.vision.models import (
    ERROR_CAPTURE_FAILED,
    ERROR_INVALID_REGION,
    ERROR_OCR_FAILED,
    VisionResult,
)
from screenvision_sentinel.vision.pipeline import VisionPipeline
from screenvision_sentinel.vision.policy import CapturePolicy, CapturePolicyError

__all__ = [
    "ERROR_CAPTURE_FAILED",
    "ERROR_INVALID_REGION",
    "ERROR_OCR_FAILED",
    "CapturePolicy",
    "CapturePolicyError",
    "DebugImageStorage",
    "VisionPipeline",
    "VisionResult",
]
