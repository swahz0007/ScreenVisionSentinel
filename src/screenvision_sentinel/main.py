"""Application entry point."""

from __future__ import annotations

import sys

from screenvision_sentinel.utils.logging import configure_logging


def main() -> int:
    """Start the desktop application without enabling any background automation."""
    configure_logging()

    from PySide6.QtWidgets import QApplication

    from screenvision_sentinel.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
