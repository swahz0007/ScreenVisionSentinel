"""Capture-region input parsing and bounds policy."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from screenvision_sentinel.capture.base import ScreenRegion

INTEGER_TEXT = re.compile(r"^[+-]?\d+$")


class CapturePolicyError(ValueError):
    """Raised when an incoming capture region violates the capture policy."""


@dataclass(frozen=True)
class CapturePolicy:
    """Conservative limits for local screen capture requests."""

    max_width: int = 7680
    max_height: int = 4320
    max_pixels: int = 16_777_216
    min_coordinate: int = -100_000
    max_coordinate: int = 100_000

    @classmethod
    def from_config(cls, config: Any) -> CapturePolicy:
        """Create policy from AppConfig-like objects without coupling imports."""
        return cls(
            max_width=int(config.max_capture_width),
            max_height=int(config.max_capture_height),
            max_pixels=int(config.max_capture_pixels),
            min_coordinate=int(config.min_capture_coordinate),
            max_coordinate=int(config.max_capture_coordinate),
        )

    def parse_csv_rect(self, value: str) -> ScreenRegion:
        """Parse a CLI rectangle in left,top,width,height format."""
        parts = [part.strip() for part in value.split(",")]
        return self.parse_rect_values(parts, allow_string_numbers=True)

    def parse_rect_values(
        self,
        values: Sequence[object],
        *,
        allow_string_numbers: bool = False,
    ) -> ScreenRegion:
        """Parse and validate four strict integer rectangle values."""
        if isinstance(values, (str, bytes)) or len(values) != 4:
            raise CapturePolicyError("rect must contain exactly 4 values")

        region = ScreenRegion(
            left=self._parse_int(values[0], allow_string_numbers=allow_string_numbers),
            top=self._parse_int(values[1], allow_string_numbers=allow_string_numbers),
            width=self._parse_int(values[2], allow_string_numbers=allow_string_numbers),
            height=self._parse_int(values[3], allow_string_numbers=allow_string_numbers),
        )
        self.validate(region)
        return region

    def validate(self, region: ScreenRegion) -> None:
        """Validate a normalized screen region."""
        values = (region.left, region.top, region.width, region.height)
        if any(not self._is_strict_int(value) for value in values):
            raise CapturePolicyError("region values must be integers")
        if region.width <= 0:
            raise CapturePolicyError("region width must be greater than 0")
        if region.height <= 0:
            raise CapturePolicyError("region height must be greater than 0")
        if region.width > self.max_width:
            raise CapturePolicyError(f"region width exceeds limit: {self.max_width}")
        if region.height > self.max_height:
            raise CapturePolicyError(f"region height exceeds limit: {self.max_height}")
        if region.width * region.height > self.max_pixels:
            raise CapturePolicyError(f"region pixel count exceeds limit: {self.max_pixels}")

        right = region.left + region.width
        bottom = region.top + region.height
        if (
            region.left < self.min_coordinate
            or region.top < self.min_coordinate
            or right > self.max_coordinate
            or bottom > self.max_coordinate
        ):
            raise CapturePolicyError("region coordinates are outside the allowed range")

    @classmethod
    def _parse_int(cls, value: object, *, allow_string_numbers: bool) -> int:
        if cls._is_strict_int(value):
            return value
        if allow_string_numbers and isinstance(value, str) and INTEGER_TEXT.match(value):
            return int(value)
        raise CapturePolicyError("region values must be integers")

    @staticmethod
    def _is_strict_int(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)
