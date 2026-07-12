"""Safe debug-image storage confined to a fixed project directory."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class DebugImageStorage:
    """Store optional debug screenshots without accepting caller-supplied paths."""

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    def save(self, image_data: Any) -> Path:
        """Save one debug image with a generated non-overwriting PNG filename."""
        import cv2

        self._output_dir.mkdir(parents=True, exist_ok=True)
        for _attempt in range(10):
            path = self._output_dir / self._generate_name()
            if path.exists():
                continue
            saved = cv2.imwrite(str(path), image_data)
            if not saved:
                raise OSError("cv2.imwrite returned false")
            return path
        raise FileExistsError("unable to generate a unique debug image filename")

    @staticmethod
    def _generate_name() -> str:
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")
        random_id = secrets.token_hex(4)
        return f"debug_{timestamp}_{random_id}.png"
