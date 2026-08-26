"""GUI entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from argus_test_creator.app import CreatorApp, load_config
from argus_test_creator.core.logging import configure_logging


def run_gui(project_dir: Path | None = None, *, test_id: str | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    from argus_test_creator.ui.main_window import MainWindow

    config = load_config(project_root=project_dir)
    configure_logging(diagnostic=config.diagnostic)
    qt_app = QApplication.instance() or QApplication(sys.argv[:1])
    qt_app.setApplicationName("Argus Test Creator")
    app = CreatorApp(config=config)
    window = MainWindow(app)
    if project_dir is not None:
        window.open_project(project_dir, test_id)
    window.show()
    return qt_app.exec()


if __name__ == "__main__":
    sys.exit(run_gui(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
