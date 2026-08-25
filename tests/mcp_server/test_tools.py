"""MCP tools through the SDK's in-memory client (fake devices only)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from mcp import Client
from tests.mcp_server.conftest import Project

from argus.mcp.server import create_server
from argus.mcp.tools import REGISTRARS

pytestmark = pytest.mark.anyio

EXPECTED_TOOLS = [
    "argus_list_tests",
    "argus_get_test",
    "argus_validate",
    "argus_preflight",
    "argus_run_test",
    "argus_run_tests",
    "argus_get_run",
    "argus_get_run_events",
    "argus_list_runs",
    "argus_list_devices",
    "argus_get_device",
    "argus_capture_screenshot",
    "argus_list_artifacts",
    "argus_get_artifact",
    "argus_diagnose_run",
]


@pytest.fixture
async def client(project: Project):
    server = create_server(project.config())
    async with Client(server) as c:
        yield c


async def call(client: Client, name: str, **arguments: Any):
    return await client.call_tool(name, arguments)


async def run_test(client: Client, test_id: str, **extra: Any) -> dict[str, Any]:
    result = await call(client, "argus_run_test", test_id=test_id, device="fake_android", **extra)
    assert not result.is_error, result.content
    assert result.structured_content["completed"] is True
    return result.structured_content


# -- discovery -------------------------------------------------------------------------------------


async def test_tool_catalog_is_deterministic_and_documented(client: Client):
    listed = await client.list_tools()
    names = [t.name for t in listed.tools]
    assert names == EXPECTED_TOOLS
    assert len(REGISTRARS) == 6
    for tool in listed.tools:
        assert len(tool.description) > 80, tool.name
        assert tool.input_schema["type"] == "object"
        assert tool.annotations is not None
    by_name = {t.name: t for t in listed.tools}
    assert by_name["argus_run_test"].annotations.read_only_hint is False
    assert by_name["argus_run_test"].annotations.destructive_hint is True
    assert by_name["argus_list_tests"].annotations.read_only_hint is True
    assert by_name["argus_run_test"].input_schema["required"] == ["test_id"]
    assert "context" not in by_name["argus_run_test"].input_schema["properties"]
    assert by_name["argus_get_run"].output_schema is not None
    second = await client.list_tools()
    assert [t.name for t in second.tools] == names


async def test_list_tests_filters_and_pagination(client: Client):
    result = await call(client, "argus_list_tests")
    data = result.structured_content
    assert data["total"] == 6 and not data["truncated"]
    assert [t["id"] for t in data["items"]][:2] == ["FAIL-001", "FAIL-002"]  # by feature, id

    page = (await call(client, "argus_list_tests", limit=2)).structured_content
    assert len(page["items"]) == 2 and page["truncated"] and page["next_cursor"]
    page2 = (await call(client, "argus_list_tests", limit=2, cursor=page["next_cursor"]))
    assert [t["id"] for t in page2.structured_content["items"]] == ["PASS-001", "PASS-002"]

    by_feature = (await call(client, "argus_list_tests", feature="settings")).structured_content
    assert [t["id"] for t in by_feature["items"]] == ["SET-001"]
    by_tags = (await call(client, "argus_list_tests", tags=["smoke", "visual"])).structured_content
    assert [t["id"] for t in by_tags["items"]] == ["PASS-001"]
    expr = (await call(client, "argus_list_tests", tags=["visual and not smoke"]))
    assert [t["id"] for t in expr.structured_content["items"]] == ["FAIL-001", "PASS-002"]
    by_platform = (await call(client, "argus_list_tests", platform="android")).structured_content
    assert by_platform["total"] == 5
    by_query = (await call(client, "argus_list_tests", query="rendered")).structured_content
    assert [t["id"] for t in by_query["items"]] == ["PASS-001"]
    by_ids = (await call(client, "argus_list_tests", test_ids=["SET-001", "x"]))
    assert by_ids.structured_content["total"] == 1


async def test_get_test_returns_full_definition(client: Client):
    result = await call(client, "argus_get_test", test_id="PASS-001")
    data = result.structured_content
    assert data["steps"][1]["action"] == "wait_until"
    assert data["steps"][1]["params"]["condition"]["type"] == "image_present"
    assert data["parameters"] == {"movie_id": 123}
    assert data["source"] == "suites/suite.yaml"
    assert data["retry"] == {"count": 0, "only": ["timeout", "device_connection"]}


async def test_get_unknown_test_is_structured_error(client: Client):
    result = await call(client, "argus_get_test", test_id="PASS-9")
    assert result.is_error
    error = result.structured_content["error"]
    assert error["type"] == "InvalidArgumentError"
    assert error["category"] == "invalid_argument"
    assert error["retryable"] is False
    assert "PASS-001" in error["remediation"]
    assert "InvalidArgumentError" in result.content[0].text


# -- validation ------------------------------------------------------------------------------------


async def test_validate_framework_only(client: Client):
    data = (await call(client, "argus_validate", framework_only=True)).structured_content
    assert data["status"] == "ready"
    assert data["framework_only"] is True
    assert {c["section"] for c in data["checks"]} == {"Framework", "Visual Testing"}
    assert data["failures"] == []


async def test_validate_full_reports_device_failures(client: Client):
    data = (await call(client, "argus_validate", framework_only=False)).structured_content
    assert data["status"] == "not_ready"
    assert any("fake_broken" in f and "Screenshot" in f for f in data["failures"])
    assert data["remediation"]


async def test_preflight_ready_and_not_ready(client: Client):
    ok = (await call(client, "argus_preflight", test_ids=["PASS-001"])).structured_content
    assert ok["status"] == "ready"
    assert ok["requirements"]["devices"] == ["fake_android"]
    assert ok["requirements"]["backend"] is True
    assert "Device: fake_android" in ok["passed"]

    bad = await call(client, "argus_preflight", test_ids=["SET-001"], device="fake_broken")
    data = bad.structured_content
    assert data["status"] == "not_ready"
    assert data["failed"] == ["Screenshot: fake_broken"]
    assert data["remediation"] and "Screenshot: fake_broken" in data["remediation"][0]

    none = (await call(client, "argus_preflight", feature="nothing")).structured_content
    assert none["status"] == "no_tests"


# -- execution -------------------------------------------------------------------------------------


async def test_run_test_success(client: Client):
    data = await run_test(client, "PASS-001")
    assert data["run"]["state"] == "completed"
    assert data["run"]["status"] == "passed"
    assert data["run"]["run_id"].startswith("run-")
    assert data["tests"][0]["status"] == "passed"
    assert data["failures"] == []
    assert data["next_step"].startswith("All tests passed")


async def test_run_test_failure_and_diagnosis(client: Client):
    data = await run_test(client, "FAIL-001")
    assert data["run"]["status"] == "failed"
    assert data["failures"][0]["failure_category"] == "assertion"
    assert "FAIL-001_android/actual.png" in data["artifacts"]
    assert "argus_diagnose_run" in data["next_step"]

    run_id = data["run"]["run_id"]
    diag = (await call(client, "argus_diagnose_run", run_id=run_id)).structured_content
    test = diag["tests"][0]
    assert test["test_id"] == "FAIL-001"
    assert test["failed_step"]["action"] == "verify"
    assert test["expected"]["image"] == "movie_456.png"
    assert test["observed"]["confidence"] is not None
    assert test["device"]["platform"] == "android"
    assert any("diff.png" in step for step in diag["next_steps"])
    only = (await call(client, "argus_diagnose_run", run_id=run_id, test_id="PASS-001"))
    assert only.structured_content["tests"] == []


async def test_run_tests_filters_and_failure_policy(client: Client):
    stop = await call(client, "argus_run_tests", tags=["broken"], device="fake_android")
    data = stop.structured_content
    assert data["run"]["status"] == "stopped"
    assert data["run"]["failed"] == 1 and data["run"]["skipped"] == 1
    assert [t["status"] for t in data["tests"]] == ["failed", "skipped"]

    cont = await call(
        client, "argus_run_tests", tags=["broken"], device="fake_android", continue_on_failure=True
    )
    assert cont.structured_content["run"]["status"] == "failed"
    assert cont.structured_content["run"]["failed"] == 2

    capped = await call(
        client, "argus_run_tests", feature="Movies", device="fake_android", max_failures=1
    )
    assert capped.structured_content["run"]["stop_reason"] == "maximum failures reached (1)"

    empty = await call(client, "argus_run_tests", feature="nothing")
    assert empty.structured_content["run"]["total_tests"] == 0


async def test_run_test_platform_restriction(client: Client):
    result = await call(client, "argus_run_test", test_id="PASS-001", platform="yocto")
    assert result.structured_content["run"]["executed"] == 0  # no yocto device configured


async def test_long_run_returns_early_and_is_pollable(client: Client):
    started = await call(client, "argus_run_test", test_id="SLOW-001", wait_seconds=0)
    data = started.structured_content
    assert data["completed"] is False
    assert data["run"]["state"] in ("queued", "running")
    assert "argus_get_run" in data["next_step"]
    run_id = data["run"]["run_id"]

    busy = await call(client, "argus_run_test", test_id="PASS-001")
    assert busy.is_error
    assert busy.structured_content["error"]["category"] == "busy"
    assert busy.structured_content["error"]["retryable"] is True

    shot = await call(client, "argus_capture_screenshot", device="fake_android")
    assert shot.is_error and "busy" in shot.content[0].text

    for _ in range(100):
        status = (await call(client, "argus_get_run", run_id=run_id)).structured_content
        if status["state"] == "completed":
            break
        await asyncio.sleep(0.1)
    assert status["status"] == "passed"
    assert status["duration_ms"] >= 2500

    events = (await call(client, "argus_get_run_events", run_id=run_id, limit=3))
    first = events.structured_content
    assert [e["type"] for e in first["events"]][0] == "run_started"
    assert first["has_more"] and first["next_after"] == 3
    rest = await call(client, "argus_get_run_events", run_id=run_id, after=first["next_after"])
    assert rest.structured_content["events"][0]["seq"] == 4
    assert rest.structured_content["events"][-1]["type"] == "run_completed"

    runs = (await call(client, "argus_list_runs")).structured_content
    assert runs["items"][0]["run_id"] == run_id


async def test_progress_is_reported_while_waiting(client: Client):
    seen: list[tuple[float, float | None, str | None]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        seen.append((progress, total, message))

    result = await client.call_tool(
        "argus_run_test", {"test_id": "SLOW-001"}, progress_callback=on_progress
    )
    assert result.structured_content["completed"] is True
    assert seen and seen[0][0] == 0.0


async def test_get_unknown_run(client: Client):
    result = await call(client, "argus_get_run", run_id="run-missing")
    assert result.is_error
    assert result.structured_content["error"]["type"] == "ConfigurationError"


# -- devices ---------------------------------------------------------------------------------------


async def test_list_devices_and_get_device(client: Client):
    data = (await call(client, "argus_list_devices")).structured_content
    names = {d["name"]: d for d in data["items"]}
    assert names["fake_android"]["state"] == "idle"
    assert names["fake_ghost"]["state"] == "not_configured"
    assert "tap" in names["fake_android"]["capabilities"]

    detail = (await call(client, "argus_get_device", device="fake_android")).structured_content
    assert detail["probed"] is False and detail["health"] is None
    assert detail["options"]["screen_size"] == [1280, 720]

    probed = await call(client, "argus_get_device", device="fake_android", probe=True)
    assert probed.structured_content["health"]["status"] == "healthy"
    assert probed.structured_content["screen"] == {"width": 1280, "height": 720}

    missing = await call(client, "argus_get_device", device="nope")
    assert missing.is_error
    assert missing.structured_content["error"]["category"] == "configuration"


async def test_capture_screenshot_variants(client: Client):
    result = await call(client, "argus_capture_screenshot", device="fake_android")
    assert not result.is_error
    text, image = result.content
    assert text.type == "text" and "1280x720" in text.text
    assert image.type == "image" and image.mime_type == "image/png"
    assert "scale=1.000" in text.text

    small = await call(
        client, "argus_capture_screenshot", device="fake_android", format="jpeg", max_dimension=320
    )
    assert small.content[1].mime_type == "image/jpeg"
    assert "returned=320x180" in small.content[0].text

    cropped = await call(
        client,
        "argus_capture_screenshot",
        device="fake_android",
        region={"x": 0, "y": 0, "width": 200, "height": 100},
    )
    assert "returned=200x100" in cropped.content[0].text

    bad_region = await call(
        client,
        "argus_capture_screenshot",
        device="fake_android",
        region={"x": 1200, "y": 0, "width": 200, "height": 100},
    )
    assert bad_region.is_error and "exceeds" in bad_region.content[0].text

    failing = await call(client, "argus_capture_screenshot", device="fake_broken")
    assert failing.is_error
    assert failing.structured_content["error"]["category"] == "screenshot"
    assert failing.structured_content["error"]["retryable"] is True

    bad_format = await call(client, "argus_capture_screenshot", device="fake_android", format="bmp")
    assert bad_format.is_error


async def test_screenshot_is_bounded_by_server_limit(project: Project):
    config = project.config(mcp={"limits": {"max_screenshot_dimension": 256}})
    async with Client(create_server(config)) as client:
        result = await call(
            client, "argus_capture_screenshot", device="fake_android", max_dimension=2000
        )
        assert "returned=256x144" in result.content[0].text


# -- artifacts -------------------------------------------------------------------------------------


async def test_artifacts_list_and_get(client: Client):
    run_id = (await run_test(client, "FAIL-001"))["run"]["run_id"]
    listed = (await call(client, "argus_list_artifacts", run_id=run_id)).structured_content
    ids = {a["artifact_id"]: a for a in listed["items"]}
    assert ids["FAIL-001_android/diff.png"]["kind"] == "diff"
    assert ids["FAIL-001_android/diff.png"]["test_id"] == "FAIL-001"
    assert ids["report.json"]["kind"] == "report" and ids["report.json"]["test_id"] is None
    assert ids["FAIL-001_android/logs.txt"]["mime_type"] == "text/plain"

    diffs = await call(client, "argus_list_artifacts", run_id=run_id, kind="diff")
    assert [a["kind"] for a in diffs.structured_content["items"]] == ["diff"]
    paged = await call(client, "argus_list_artifacts", run_id=run_id, limit=2)
    assert paged.structured_content["truncated"] and paged.structured_content["next_cursor"]

    image = await call(
        client, "argus_get_artifact", run_id=run_id, artifact_id="FAIL-001_android/diff.png"
    )
    assert not image.is_error
    assert [b.type for b in image.content] == ["text", "image"]
    assert image.content[1].mime_type == "image/png"
    assert image.structured_content["delivery"] == "image"

    text = await call(
        client, "argus_get_artifact", run_id=run_id, artifact_id="FAIL-001_android/logs.txt"
    )
    assert text.content[1].text == "fake device log"
    assert text.structured_content["delivery"] == "text"

    metadata = await call(
        client, "argus_get_artifact", run_id=run_id, artifact_id="FAIL-001_android/metadata.json"
    )
    assert metadata.structured_content["delivery"] == "json"
    assert json.loads(metadata.content[1].text)["test_id"] == "FAIL-001"

    truncated = await call(
        client, "argus_get_artifact", run_id=run_id, artifact_id="report.json", max_bytes=256
    )
    assert truncated.structured_content["truncated"] is True
    assert truncated.structured_content["returned_bytes"] == 256
    assert "Truncated" in truncated.structured_content["note"]


async def test_artifact_errors(client: Client):
    run_id = (await run_test(client, "FAIL-001"))["run"]["run_id"]
    for bad in ("../config.yaml", "/etc/passwd", "FAIL-001_android/../../x", "nope.png", "a\\b"):
        result = await call(client, "argus_get_artifact", run_id=run_id, artifact_id=bad)
        assert result.is_error, bad
        assert result.structured_content["error"]["category"] == "configuration"
    passing = (await run_test(client, "PASS-001"))["run"]["run_id"]
    listed = (await call(client, "argus_list_artifacts", run_id=passing)).structured_content
    assert {a["kind"] for a in listed["items"]} == {"report"}  # nothing per-test retained


async def test_large_artifact_handling(project: Project):
    config = project.config(mcp={"limits": {"max_artifact_bytes": 2048, "max_log_bytes": 512}})
    async with Client(create_server(config)) as client:
        run_id = (await run_test(client, "FAIL-001"))["run"]["run_id"]
        image = await call(
            client, "argus_get_artifact", run_id=run_id, artifact_id="FAIL-001_android/diff.png"
        )
        assert image.structured_content["delivery"] == "omitted"
        assert "exceeds" in image.structured_content["note"]
        report = await call(client, "argus_get_artifact", run_id=run_id, artifact_id="report.json")
        assert report.structured_content["returned_bytes"] == 512
        assert report.structured_content["truncated"]


# -- errors ----------------------------------------------------------------------------------------


async def test_unexpected_exceptions_do_not_leak(client: Client, monkeypatch: pytest.MonkeyPatch):
    from argus.service import ArgusService

    def boom(self):  # noqa: ANN001
        raise RuntimeError("database password is hunter2")

    monkeypatch.setattr(ArgusService, "list_devices", boom)
    result = await call(client, "argus_list_devices")
    assert result.is_error
    assert result.content[0].text == "Error executing tool argus_list_devices"
    assert "hunter2" not in result.content[0].text


async def test_malformed_and_oversized_input(client: Client):
    missing = await client.call_tool("argus_get_test", {})
    assert missing.is_error and "test_id" in missing.content[0].text

    wrong_type = await client.call_tool("argus_list_tests", {"limit": "many"})
    assert wrong_type.is_error

    huge = (await call(client, "argus_list_tests", limit=10_000_000)).structured_content
    assert len(huge["items"]) <= 50  # clamped to mcp.limits.max_results

    negative = await call(client, "argus_get_run_events", run_id="x", after=-1)
    assert negative.is_error

    cursor = await call(client, "argus_list_tests", cursor="not-a-cursor")
    assert cursor.is_error
    assert cursor.structured_content["error"]["category"] == "invalid_argument"


async def test_concurrent_read_requests(client: Client):
    results = await asyncio.gather(
        *[call(client, "argus_list_tests", limit=3) for _ in range(10)],
        *[call(client, "argus_list_devices") for _ in range(10)],
        call(client, "argus_get_test", test_id="PASS-001"),
    )
    assert all(not r.is_error for r in results)
