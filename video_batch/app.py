"""Entry point for the Batch H.264 Encoder GUI."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from video_batch.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
