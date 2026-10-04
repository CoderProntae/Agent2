"""Application entry point for the Agent2 desktop workspace."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from PySide6.QtWidgets import QApplication, QMessageBox

from .core.paths import user_data_dir
from .ui.main_window import MainWindow


def configure_logging() -> None:
    log_dir = user_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if root.handlers:
        return
    handler = RotatingFileHandler(log_dir / "agent2.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)


def main() -> int:
    configure_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Agent2")
    app.setOrganizationName("Agent2")
    app.setStyle("Fusion")
    try:
        window = MainWindow()
    except Exception as exc:
        logging.getLogger(__name__).exception("Agent2 başlatılamadı")
        QMessageBox.critical(None, "Agent2 başlatılamadı", f"Uygulama başlatılırken hata oluştu:\n\n{exc}")
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
