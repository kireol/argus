from __future__ import annotations

import os

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from argus_test_creator.app import CreatorApp  # noqa: E402
from argus_test_creator.observation import FakeOCRProvider  # noqa: E402
from argus_test_creator.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture
def window(qtbot, tmp_path):
    app = CreatorApp(ocr=FakeOCRProvider())
    app.config.recording.settle_ms = 0
    app.config.recording.live_preview_fps = 30
    app.create_project(tmp_path / "proj", name="ui")
    app.select_target("fake-movies")
    app.new_test(test_id="UI-1", name="UI created test", feature="UI")
    win = MainWindow(app)
    win.prompt_on_close = False
    qtbot.addWidget(win)
    win.show()
    yield win
    win._preview_timer.stop()
    win._bridge.close()
    app.shutdown()
