"""Application entry point."""

import sys

from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    application = QApplication.instance()
    owns_application = application is None
    if application is None:
        application = QApplication(list(argv) if argv is not None else sys.argv)

    window = MainWindow()
    window.show()
    return application.exec() if owns_application else 0


if __name__ == "__main__":
    raise SystemExit(main())
