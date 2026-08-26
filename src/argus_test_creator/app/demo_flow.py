"""Scripted end-to-end demo (used by ``argus-test-creator demo`` and integration tests)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from argus_test_creator.app.context import CreatorApp
from argus_test_creator.models.common import Rect
from argus_test_creator.recording.session import AssertionSuggested


def run_demo_flow(
    directory: Path, *, run_with_argus: bool = False, echo: Callable[[str], None] = print,
    argus_executable: str | None = None,
) -> str:
    from argus_test_creator.adapters.fake import FakeRecorder

    app = CreatorApp()
    if argus_executable:
        app.config.argus.executable = argus_executable
    suggestions: list[AssertionSuggested] = []
    app.events.subscribe(AssertionSuggested, suggestions.append)
    try:
        app.create_project(directory, name="movies-demo") if not (
            directory / "argus.yaml"
        ).exists() else app.open_project(directory)
        app.select_target("fake-movies")
        app.new_test(test_id="DEMO-001", name="Search finds Batman Begins", feature="Movies")
        recorder = app.connect_target()
        assert isinstance(recorder, FakeRecorder)
        app.start_recording()
        echo("Recording: Movies → Search → type 'Batman' → Go")
        recorder.send_tap(200, 250)
        recorder.send_tap(200, 180)
        recorder.send_tap(300, 190)
        recorder.send_text("Batman")
        recorder.send_key("ENTER")
        steps = app.stop_recording()
        for job in app.workers.active_jobs():
            job.result(30)
        echo(f"Recorded {len(steps)} steps")
        capture = app.capture_screen().result(30)
        app.add_image_verification(capture, Rect(x=120, y=160, width=700, height=72),
                                   label="batman row")
        app.add_text_verification("Batman Begins", capture=capture, wait=False)
        issues = app.validate()
        echo(f"Validation: {len(issues)} issue(s)")
        path = app.save_test()
        echo(f"Saved {path}")
        summary = f"Project ready at {directory}"
        if run_with_argus:
            result = app.run_with_argus(on_output=echo).result(600)
            summary += f"\nArgus result: {result.status}"
            if result.html_report:
                summary += f"\nReport: {result.html_report}"
        return summary
    finally:
        app.shutdown()
