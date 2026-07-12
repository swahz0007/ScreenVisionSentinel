"""RapidOCR engine backed by ONNX Runtime for lightweight local OCR."""

from __future__ import annotations

import logging
from math import ceil, floor
from pathlib import Path
from time import perf_counter
from typing import Any

from screenvision_sentinel.ocr.base import OCRBoundingBox, OCRResult

LOGGER = logging.getLogger(__name__)
LOCAL_GPU_PROVIDERS = ("DmlExecutionProvider", "CUDAExecutionProvider")
SMALL_FIELD_TARGET_SIZE = 80
RAPID_OCR_INTRA_OP_THREADS = 2
RAPID_OCR_INTER_OP_THREADS = 1


class RapidOCREngine:
    """OCR engine using rapidocr_onnxruntime (PP-OCRv4 models on ONNX Runtime)."""

    name = "rapidocr"

    def __init__(self) -> None:
        self._engine = None
        try:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR(
                intra_op_num_threads=RAPID_OCR_INTRA_OP_THREADS,
                inter_op_num_threads=RAPID_OCR_INTER_OP_THREADS,
            )
        except ImportError:
            LOGGER.warning(
                "rapidocr_onnxruntime is not installed. "
                "Install it with: pip install rapidocr_onnxruntime"
            )
        except Exception as exc:
            LOGGER.warning("Failed to initialise RapidOCR engine: %s", exc)

    def recognize(self, image_source: Path | Any, use_det: bool = True) -> OCRResult:
        """Recognize text from a screenshot image or memory array."""
        started_at = perf_counter()

        if self._engine is None:
            elapsed_ms = (perf_counter() - started_at) * 1000
            return OCRResult(
                text="",
                confidence=0.0,
                boxes=(),
                elapsed_ms=elapsed_ms,
                engine_name=self.name,
                success=False,
                error=(
                    "rapidocr_onnxruntime 未安装或初始化失败，请执行 "
                    "pip install rapidocr_onnxruntime"
                ),
            )

        if isinstance(image_source, Path):
            if not image_source.exists():
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
            img_input = str(image_source)
        else:
            img_input = image_source

        try:
            result, coordinate_scale = self._recognize_with_variants(
                img_input,
                use_det=use_det,
            )
            if not use_det and result:
                # RapidOCR returns [['text', confidence]] when detection is disabled.
                texts = [str(r[0]) for r in result]
                confidences = [float(r[1]) for r in result]

                full_text = "\n".join(texts)
                avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

                elapsed_ms = (perf_counter() - started_at) * 1000
                return OCRResult(
                    text=full_text,
                    confidence=avg_confidence,
                    boxes=(),
                    elapsed_ms=elapsed_ms,
                    engine_name=self.name,
                    success=True,
                )
        except Exception as exc:
            elapsed_ms = (perf_counter() - started_at) * 1000
            return OCRResult(
                text="",
                confidence=0.0,
                boxes=(),
                elapsed_ms=elapsed_ms,
                engine_name=self.name,
                success=False,
                error=f"OCR 识别异常: {exc}",
            )

        elapsed_ms = (perf_counter() - started_at) * 1000

        if result is None or len(result) == 0:
            return OCRResult(
                text="",
                confidence=0.0,
                boxes=(),
                elapsed_ms=elapsed_ms,
                engine_name=self.name,
                success=True,
            )

        texts: list[str] = []
        confidences: list[float] = []
        boxes: list[OCRBoundingBox] = []

        for item in result:
            box_points, text, confidence = item[0], item[1], item[2]
            texts.append(str(text))
            confidences.append(float(confidence))
            boxes.append(
                _box_points_to_bounding_box(
                    box_points,
                    coordinate_scale=coordinate_scale,
                )
            )

        full_text = "\n".join(texts)
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        return OCRResult(
            text=full_text,
            confidence=avg_confidence,
            boxes=tuple(boxes),
            elapsed_ms=elapsed_ms,
            engine_name=self.name,
            success=True,
        )

    def runtime_details(self) -> dict[str, object]:
        """Report local inference capability without exposing OCR content or screenshots."""
        try:
            import onnxruntime as ort

            providers = list(ort.get_available_providers())
        except Exception:
            providers = []

        available_gpu_providers = [
            provider for provider in providers if provider in LOCAL_GPU_PROVIDERS
        ]
        if available_gpu_providers:
            device_detail = (
                "当前 RapidOCR 按低负载 CPU 配置启动；检测到可供后续测试的本地 GPU 执行器："
                + ", ".join(available_gpu_providers)
            )
        else:
            device_detail = "当前未检测到 DirectML/CUDA；OCR 使用低负载 CPU 配置。"

        return {
            "device_label": "CPU",
            "device_detail": device_detail,
            "available_execution_providers": providers,
            "gpu_switch_available": bool(available_gpu_providers),
            "intra_op_num_threads": RAPID_OCR_INTRA_OP_THREADS,
            "inter_op_num_threads": RAPID_OCR_INTER_OP_THREADS,
        }

    def _recognize_with_variants(self, img_input: Any, *, use_det: bool) -> tuple[Any, float]:
        result = None
        coordinate_scale = 1.0
        variants = _iter_ocr_input_variants(img_input)
        # Full text detection is by far the most expensive path. One normalized
        # input is enough here; bounded server fallbacks or a later monitor tick
        # can retry without multiplying detector work inside a single request.
        if use_det:
            variants = variants[:1]
        for variant, variant_scale in variants:
            if use_det:
                result, _elapse = self._engine(variant)
            else:
                result, _elapse = self._engine(variant, use_det=False)
            if result is not None and len(result) > 0:
                return result, variant_scale
            coordinate_scale = variant_scale
        return result, coordinate_scale


def _box_points_to_bounding_box(
    points: list[list[float]],
    *,
    coordinate_scale: float = 1.0,
) -> OCRBoundingBox:
    """Convert RapidOCR 4-point coordinates to an axis-aligned bounding box."""
    xs = [p[0] / coordinate_scale for p in points]
    ys = [p[1] / coordinate_scale for p in points]
    left = floor(min(xs))
    top = floor(min(ys))
    right = ceil(max(xs))
    bottom = ceil(max(ys))
    width = max(1, right - left)
    height = max(1, bottom - top)
    return OCRBoundingBox(left=left, top=top, width=width, height=height)


def _iter_ocr_inputs(img_input: Any) -> list[Any]:
    """Return channel-normalized and small-field variants for OCR retries."""
    return [variant for variant, _scale in _iter_ocr_input_variants(img_input)]


def _iter_ocr_input_variants(img_input: Any) -> list[tuple[Any, float]]:
    """Return OCR inputs together with their scale relative to the original image."""
    variants = [(img_input, 1.0)]
    if isinstance(img_input, str):
        return variants

    try:
        import cv2
        import numpy as np
    except ImportError:
        return variants

    if not isinstance(img_input, np.ndarray) or img_input.ndim < 2:
        return variants

    base = img_input
    if base.ndim == 3 and base.shape[2] == 4:
        # mss/OpenCV arrays are BGRA. RapidOCR treats a four-channel ndarray as
        # RGBA, which can invert or otherwise alter the screenshot before OCR.
        base = np.ascontiguousarray(base[:, :, :3])
        variants = [(base, 1.0)]

    height, width = base.shape[:2]
    if height <= 0 or width <= 0:
        return variants

    min_dimension = min(height, width)
    if min_dimension >= SMALL_FIELD_TARGET_SIZE:
        return variants
    if base.ndim == 3 and base.shape[2] != 3:
        return variants

    scale = max(2, min(4, ceil(SMALL_FIELD_TARGET_SIZE / min_dimension)))
    enlarged = cv2.resize(base, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    variants.append((enlarged, float(scale)))

    gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY) if enlarged.ndim == 3 else enlarged
    _threshold, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    variants.append((cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR), float(scale)))

    return variants
