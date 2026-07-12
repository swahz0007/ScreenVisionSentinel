"""Logging configuration."""

from __future__ import annotations

import logging
import sys


def configure_logging(level: int = logging.INFO) -> None:
    """Configure console logging once without creating runtime log files."""
    root_logger = logging.getLogger()
    if root_logger.handlers:
        root_logger.setLevel(level)
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    root_logger.addHandler(handler)
    root_logger.setLevel(level)
