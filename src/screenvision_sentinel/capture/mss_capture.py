"""mss-backed screen capture implementation for future manual capture use."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from screenvision_sentinel.capture.base import ScreenRegion, ScreenshotResult


class MssCaptureService:
    """Capture a single screen region with mss when explicitly called."""

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir

    def capture_region(self, region: ScreenRegion, save_to_disk: bool = True) -> ScreenshotResult:
        """Capture one region, optionally save it, and return the image data."""
        if not region.is_valid():
            return ScreenshotResult(
                region=region,
                image_path=None,
                image_data=None,
                success=False,
                error="监控区域宽度和高度必须大于 0",
            )

        try:
            from mss import MSS
            from mss.exception import ScreenShotError
            from mss.tools import to_png
        except ImportError as exc:
            return ScreenshotResult(
                region=region, image_path=None, image_data=None, success=False, error=str(exc)
            )

        import numpy as np

        image_path = None
        if save_to_disk:
            timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")
            image_path = self._output_dir / f"manual_capture_{timestamp}.png"
        monitor = {
            "left": region.left,
            "top": region.top,
            "width": region.width,
            "height": region.height,
        }

        try:
            if image_path is not None:
                self._output_dir.mkdir(parents=True, exist_ok=True)
            with MSS() as sct:
                shot = sct.grab(monitor)
                img_data = np.array(shot)
                if image_path is not None:
                    to_png(shot.rgb, shot.size, output=str(image_path))
        except (OSError, ScreenShotError) as exc:
            return ScreenshotResult(
                region=region, image_path=None, image_data=None, success=False, error=str(exc)
            )

        return ScreenshotResult(
            region=region, image_path=image_path, image_data=img_data, success=True
        )
