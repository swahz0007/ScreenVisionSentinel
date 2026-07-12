"""Mock OCR engine for tests and stage 0 UI wiring."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Any

from screenvision_sentinel.ocr.base import BaseOCREngine, OCRResult


class MockOCREngine(BaseOCREngine):
    """Deterministic OCR engine that does not inspect real image pixels."""

    name = "mock"

    def __init__(
        self,
        text: str = "模拟 OCR 结果",
        confidence: float = 1.0,
        *,
        simulated_results_enabled: bool = True,
    ) -> None:
        self._text = text
        self._confidence = confidence
        self._simulated_results_enabled = simulated_results_enabled

    def recognize(self, image_source: Path | Any, use_det: bool = True) -> OCRResult:
        """Mock recognition."""
        started_at = perf_counter()
        if not self._simulated_results_enabled:
            elapsed_ms = (perf_counter() - started_at) * 1000
            return OCRResult(
                text="",
                confidence=0.0,
                boxes=(),
                elapsed_ms=elapsed_ms,
                engine_name=self.name,
                success=False,
                error=str(getattr(self, "fallback_reason", "OCR 引擎不可用")),
            )
        if isinstance(image_source, Path) and not image_source.exists():
            elapsed_ms = (perf_counter() - started_at) * 1000
            return OCRResult(
                text="",
                confidence=0.0,
                boxes=(),
                elapsed_ms=elapsed_ms,
                engine_name=self.name,
                success=False,
                error=f"图像不存在: {image_source}",
            )

        elapsed_ms = (perf_counter() - started_at) * 1000
        return OCRResult(
            text=self._text,
            confidence=self._confidence,
            boxes=(),
            elapsed_ms=elapsed_ms,
            engine_name=self.name,
            success=True,
        )
