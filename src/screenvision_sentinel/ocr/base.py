"""OCR engine abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class OCRBoundingBox:
    """Optional text bounding box in image coordinates."""

    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class OCRResult:
    """Unified OCR result returned by all OCR engines."""

    text: str
    confidence: float
    boxes: tuple[OCRBoundingBox, ...]
    elapsed_ms: float
    engine_name: str
    success: bool
    error: str | None = None


class BaseOCREngine(Protocol):
    """OCR engine contract."""

    name: str

    def recognize(self, image_source: Path | Any, use_det: bool = True) -> OCRResult:
        """Recognize text from an image.

        Args:
            image_source: Path to image file or numpy array/PIL Image
            use_det: Whether to use text detection. If False, assumes the whole image
                is a single text line.
        """
        pass
