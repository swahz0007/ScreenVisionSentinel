"""Factory for creating OCR engine instances by name."""

from __future__ import annotations

import logging

from screenvision_sentinel.ocr.base import BaseOCREngine
from screenvision_sentinel.ocr.mock import MockOCREngine

LOGGER = logging.getLogger(__name__)

AVAILABLE_ENGINES = ("rapidocr", "mock")
DEFAULT_ENGINE = "rapidocr"


def create_ocr_engine(name: str = DEFAULT_ENGINE) -> BaseOCREngine:
    """Create an OCR engine by name, falling back to MockOCR on failure."""
    if name == "mock":
        engine = MockOCREngine()
        engine.requested_engine = name
        return engine

    if name == "rapidocr":
        engine = _try_create_rapid_ocr()
        engine.requested_engine = name
        return engine

    LOGGER.warning("Unknown OCR engine '%s', falling back to mock.", name)
    engine = _disabled_mock(f"unknown OCR engine: {name}")
    engine.requested_engine = name
    return engine


def _try_create_rapid_ocr() -> BaseOCREngine:
    try:
        from screenvision_sentinel.ocr.rapid_ocr import RapidOCREngine

        engine = RapidOCREngine()
        if engine._engine is not None:
            return engine
        LOGGER.warning("RapidOCR initialisation failed, falling back to mock.")
        return _disabled_mock("RapidOCR initialisation failed")
    except Exception as exc:
        LOGGER.warning("Cannot create RapidOCR engine: %s. Falling back to mock.", exc)
        return _disabled_mock(f"Cannot create RapidOCR engine: {exc}")


def _disabled_mock(reason: str) -> MockOCREngine:
    """Return a health-reporting fallback that cannot emit simulated OCR as real data."""
    engine = MockOCREngine(simulated_results_enabled=False)
    engine.fallback_reason = reason
    return engine
