"""MCP resources and prompts."""

from __future__ import annotations

import json

import pytest
from mcp import Client
from mcp.types import TextResourceContents
from tests.mcp_server.conftest import Project

from argus.mcp.server import create_server

pytestmark = pytest.mark.anyio


@pytest.fixture
async def client(project: Project):
    async with Client(create_server(project.config())) as c:
        yield c


async def read(client: Client, uri: str) -> dict | list:
    result = await client.read_resource(uri)
    contents = result.contents[0]
    assert isinstance(contents, TextResourceContents)
    assert contents.mime_type == "application/json"
    return json.loads(contents.text)


async def test_resource_catalog(client: Client):
    listed = await client.list_resources()
    assert [str(r.uri) for r in listed.resources] == [
        "argus://tests",
        "argus://runs",
        "argus://devices",
        "argus://configuration",
    ]
    templates = await client.list_resource_templates()
    assert [t.uri_template for t in templates.resource_templates] == [
        "argus://tests/{test_id}",
        "argus://runs/{run_id}",
        "argus://runs/{run_id}/report",
        "argus://runs/{run_id}/test/{test_id}",
        "argus://devices/{device_name}",
    ]


async def test_test_resources(client: Client):
    index = await read(client, "argus://tests")
    assert index["total"] == 6 and not index["truncated"]
    assert index["items"][0]["id"] == "FAIL-001"
    detail = await read(client, "argus://tests/SET-001")
    assert detail["steps"] == [{"action": "log", "name": None, "params": {"message": "hello"}}]
    with pytest.raises(Exception, match="Unknown test"):
        await client.read_resource("argus://tests/NOPE")


async def test_test_index_is_bounded(project: Project):
    config = project.config(mcp={"limits": {"max_results": 2}})
    async with Client(create_server(config)) as client:
        index = await read(client, "argus://tests")
        assert len(index["items"]) == 2 and index["truncated"] and index["total"] == 6


async def test_run_resources(client: Client):
    outcome = await client.call_tool(
        "argus_run_test", {"test_id": "FAIL-001", "device": "fake_android"}
    )
    run_id = outcome.structured_content["run"]["run_id"]

    runs = await read(client, "argus://runs")
    assert runs["items"][0]["run_id"] == run_id

    run = await read(client, f"argus://runs/{run_id}")
    assert run["status"] == "failed"
    assert run["tests"][0]["test_id"] == "FAIL-001"

    report = await read(client, f"argus://runs/{run_id}/report")
    assert report["schema_version"] == 1
    assert report["summary"]["failed"] == 1

    test = await read(client, f"argus://runs/{run_id}/test/FAIL-001")
    assert test[0]["steps"][-1]["action"] == "verify"

    with pytest.raises(Exception, match="not executed"):
        await client.read_resource(f"argus://runs/{run_id}/test/SET-001")
    with pytest.raises(Exception, match="Unknown run"):
        await client.read_resource("argus://runs/run-nope")


async def test_device_resources(client: Client):
    devices = await read(client, "argus://devices")
    assert {d["name"] for d in devices["items"]} == {"fake_android", "fake_broken", "fake_ghost"}
    device = await read(client, "argus://devices/fake_android")
    assert device["probed"] is False
    assert device["options"]["render"]["state_image"]["key"] == "movieId"
    with pytest.raises(Exception, match="Unknown device"):
        await client.read_resource("argus://devices/nope")


async def test_configuration_resource_is_redacted(client: Client):
    data = await read(client, "argus://configuration")
    config = data["configuration"]
    assert data["mcp_api_version"] == "1.0"
    assert config["backend"]["token"] == "[REDACTED]"
    assert "auth" not in config["mcp"]
    assert config["devices"]["fake_android"]["platform"] == "android"
    assert "s3cret" not in json.dumps(data)


async def test_prompts(client: Client):
    listed = await client.list_prompts()
    names = [p.name for p in listed.prompts]
    assert names == ["argus_debug_failed_test", "argus_create_test", "argus_investigate_failure"]
    assert all(p.description for p in listed.prompts)

    debug = await client.get_prompt(
        "argus_debug_failed_test", {"run_id": "run-1", "test_id": "FAIL-001"}
    )
    text = debug.messages[0].content.text
    assert "argus_diagnose_run('run-1', test_id='FAIL-001')" in text
    assert "Never lower a threshold" in text

    create = await client.get_prompt(
        "argus_create_test", {"feature": "Movies", "goal": "artwork shows", "platform": "android"}
    )
    text = create.messages[0].content.text
    assert "docs/test-authoring.md" in text
    assert "image_present" in text and "backend.set" in text
    assert "[android]" in text

    investigate = await client.get_prompt("argus_investigate_failure", {})
    assert "argus_list_runs" in investigate.messages[0].content.text
