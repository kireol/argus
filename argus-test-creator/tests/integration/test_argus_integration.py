"""Tests against a real Argus installation (skipped when none is available)."""

from __future__ import annotations

import pytest

from argus_test_creator.app.demo_flow import run_demo_flow
from argus_test_creator.argus_schema import ACTIONS, CONDITIONS
from argus_test_creator.core.errors import ArgusIntegrationError
from argus_test_creator.integrations.argus import ArgusIntegration, discover_argus
from argus_test_creator.integrations.argus import integration as integration_module
from argus_test_creator.project import CreatorProject

pytestmark = pytest.mark.integration


def test_discovery_and_missing(tmp_path, argus_executable, monkeypatch):
    info = discover_argus(configured=argus_executable)
    assert info is not None and info.source == "configured" and info.version
    # The interpreter-prefix fallback would find argus in the shared monorepo venv;
    # point it at an empty prefix so "nothing installed" is what we actually test.
    monkeypatch.setattr(integration_module.sys, "prefix", str(tmp_path))
    assert discover_argus(configured=tmp_path / "nope", env={"PATH": str(tmp_path)}) is None
    integration = ArgusIntegration(executable=tmp_path / "nope")
    integration._info = None
    integration.info = lambda refresh=False: None  # type: ignore[method-assign]
    with pytest.raises(ArgusIntegrationError) as exc:
        integration.require()
    assert "ARGUS_EXECUTABLE" in str(exc.value)


def test_schema_catalog_in_sync_with_installed_argus(argus_executable):
    schema = ArgusIntegration(executable=argus_executable).inspect_schema()
    if schema is None:
        pytest.skip("could not inspect installed Argus schema")
    assert set(schema["actions"]) == set(ACTIONS), "update argus_schema/actions.py"
    assert set(schema["conditions"]) == set(CONDITIONS), "update argus_schema/conditions.py"


def test_validate_reports_definition_errors(tmp_path, argus_executable):
    project = CreatorProject.create(tmp_path / "p")
    (project.tests_dir / "BAD.yaml").write_text("id: BAD\nname: x\nsteps: []\n")  # no feature
    result = ArgusIntegration(executable=argus_executable).validate(project.config_path,
                                                                    test_id="BAD")
    assert not result.ready and result.issues and result.issues[0].source == "argus"


def test_demo_flow_runs_through_argus(tmp_path, argus_executable):
    summary = run_demo_flow(tmp_path / "demo", run_with_argus=True, echo=lambda s: None,
                            argus_executable=argus_executable)
    assert "Argus result: passed" in summary, summary
    assert (tmp_path / "demo" / "results").is_dir()


def test_run_failure_is_reported(tmp_path, argus_executable):
    from argus_test_creator.app import CreatorApp
    from argus_test_creator.models import ConditionDraft
    from argus_test_creator.observation import FakeOCRProvider

    app = CreatorApp(ocr=FakeOCRProvider())
    app.config.argus.executable = argus_executable
    try:
        app.create_project(tmp_path / "p")
        app.select_target("fake-movies")
        app.new_test(test_id="FAIL-1", name="Deliberately failing", feature="Demo")
        app.connect_target()
        app.start_recording()
        app.stop_recording()
        app.authoring.add_step("device.tap", {"x": 1, "y": 1})
        app.authoring.add_verification(
            ConditionDraft(type="text_present", params={"text": "Definitely Not On Screen"}),
            wait=True, timeout="1s",
        )
        result = app.run_with_argus().result(300)
        assert result.status == "failed" and result.exit_code == 1
        assert result.report is not None
        tests = result.test_results()
        assert tests[0]["status"] == "failed"
        assert app.last_run is result
    finally:
        app.shutdown()
