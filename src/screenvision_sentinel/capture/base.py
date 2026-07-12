"""Screen capture abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ScreenRegion:
    """A rectangular region in screen coordinates."""

    left: int
    top: int
    width: int
    height: int

    def is_valid(self) -> bool:
        """Return whether the region has a capturable size."""
        return self.width > 0 and self.height > 0


@dataclass(frozen=True)
class ScreenshotResult:
    """Result metadata for a single manual screenshot attempt."""

    region: ScreenRegion
    image_path: Path | None
    image_data: Any | None
    success: bool
    error: str | None = None


class BaseCaptureService(Protocol):
    """Capture service contract.

    Implementations must not start loops from constructors.
    """

    def capture_region(self, region: ScreenRegion, save_to_disk: bool = True) -> ScreenshotResult:
        """Capture a single region on explicit user request."""
