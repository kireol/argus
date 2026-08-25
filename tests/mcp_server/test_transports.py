"""Server assembly, configuration, CLI, and transports (stdio + HTTP)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from tests.mcp_server.conftest import Project
from typer.testing import CliRunner

from argus.cli.main import app
from argus.config.models import AppConfig, MCPConfig
from argus.exceptions import ConfigurationError
from argus.mcp.server import build_http_app, create_server

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
# The SDK rejects Host headers that are not the bound loopback address (DNS rebinding).
LOCAL = "http://127.0.0.1:8000"


# -- configuration ---------------------------------------------------------------------------------


def test_mcp_config_defaults_and_validation():
    config = AppConfig()
    assert config.mcp.transport == "stdio"
    assert config.mcp.host == "127.0.0.1" and config.mcp.port == 8000
    assert config.mcp.limits.max_concurrent_runs == 1
    with pytest.raises(ValueError, match="transport"):
        MCPConfig(transport="websocket")
    with pytest.raises(ValueError, match="path"):
        MCPConfig(path="mcp")
    assert MCPConfig(path="/api/mcp/").path == "/api/mcp"
    with pytest.raises(ValueError):
        AppConfig.model_validate({"mcp": {"unknown": 1}})
    with pytest.raises(ValueError):
        AppConfig.model_validate({"mcp": {"limits": {"max_results": 0}}})


def test_tokens_with_unresolved_env_are_ignored():
    config = AppConfig.model_validate({"mcp": {"auth": {"tokens": ["${NOPE_TOKEN}", "real"]}}})
    assert config.mcp.auth.configured_tokens == ["real"]


def test_missing_sdk_is_a_remediated_error(project: Project, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "mcp.server", None)
    with pytest.raises(ConfigurationError, match=r"argus\[mcp\]"):
        create_server(project.config())


# -- CLI -------------------------------------------------------------------------------------------


def test_cli_lists_mcp_command_and_options():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"], env={"COLUMNS": "120"})
    assert "mcp" in result.output
    result = runner.invoke(app, ["mcp", "--help"], env={"COLUMNS": "120"})
    assert result.exit_code == 0
    for flag in ("--transport", "--host", "--port", "--path", "--config"):
        assert flag in result.output


def test_cli_rejects_invalid_transport(project: Project):
    runner = CliRunner()
    result = runner.invoke(
        app, ["mcp", "--config", str(project.config_file), "--transport", "carrier-pigeon"]
    )
    assert result.exit_code == 2
    assert "MCP ERROR" in result.output


def test_cli_reports_missing_sdk(project: Project, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "mcp.server", None)
    runner = CliRunner()
    result = runner.invoke(app, ["mcp", "--config", str(project.config_file)])
    assert result.exit_code == 2
    assert "argus[mcp]" in result.output


def test_cli_bad_config_goes_to_stderr(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(app, ["mcp", "--config", str(tmp_path / "missing.yaml")])
    assert result.exit_code == 2


# -- stdio (real subprocess, real client) ----------------------------------------------------------


@pytest.mark.anyio
async def test_stdio_transport_end_to_end(project: Project):
    """Launch `argus mcp` as a child process and drive it with the SDK client."""
    from mcp import Client, StdioServerParameters

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "argus.cli.main", "mcp", "--config", str(project.config_file)],
        cwd=str(project.root),
    )
    async with Client(params) as client:
        tools = await client.list_tools()
        assert any(t.name == "argus_list_tests" for t in tools.tools)
        listed = await client.call_tool("argus_list_tests", {"limit": 1})
        assert listed.structured_content["total"] == 6
        detail = await client.call_tool("argus_get_test", {"test_id": "PASS-001"})
        assert detail.structured_content["id"] == "PASS-001"
        pre = await client.call_tool("argus_preflight", {"test_ids": ["PASS-001"]})
        assert pre.structured_content["status"] == "ready"
        run = await client.call_tool(
            "argus_run_test", {"test_id": "FAIL-001", "device": "fake_android"}
        )
        run_id = run.structured_content["run"]["run_id"]
        assert run.structured_content["run"]["status"] == "failed"
        status = await client.call_tool("argus_get_run", {"run_id": run_id})
        assert status.structured_content["state"] == "completed"
        artifact = await client.call_tool(
            "argus_get_artifact",
            {"run_id": run_id, "artifact_id": "FAIL-001_android/actual.png"},
        )
        assert artifact.content[1].type == "image"


# -- streamable HTTP -------------------------------------------------------------------------------


def http_config(project: Project, **mcp: object) -> AppConfig:
    return project.config(mcp={"transport": "streamable-http", "json_response": True, **mcp})


def test_http_without_auth_on_loopback(project: Project):
    config = http_config(project)
    app_ = build_http_app(create_server(config), config.mcp)
    with TestClient(app_, base_url=LOCAL) as http:
        response = http.post("/mcp", json=INIT, headers=HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["result"]["serverInfo"]["name"] == "argus"
    # A fresh app per lifespan; the SDK's DNS-rebinding guard rejects foreign Host headers.
    other = build_http_app(create_server(config), config.mcp)
    with TestClient(other, base_url="http://evil.example") as http:
        assert http.post("/mcp", json=INIT, headers=HEADERS).status_code == 421


def test_http_refuses_non_loopback_without_auth(project: Project):
    config = http_config(project, host="0.0.0.0")
    with pytest.raises(ConfigurationError, match="without authentication"):
        build_http_app(create_server(config), config.mcp)


def test_http_bearer_token_required(project: Project):
    config = http_config(project, host="0.0.0.0", auth={"tokens": ["${NOPE}", "top-secret"]})
    app_ = build_http_app(create_server(config), config.mcp)
    with TestClient(app_, base_url=LOCAL) as http:
        anonymous = http.post("/mcp", json=INIT, headers=HEADERS)
        assert anonymous.status_code == 401
        assert anonymous.headers["www-authenticate"].startswith("Bearer")
        assert anonymous.json()["error"] == "unauthorized"

        wrong = http.post(
            "/mcp", json=INIT, headers={**HEADERS, "Authorization": "Bearer nope"}
        )
        assert wrong.status_code == 401
        basic = http.post(
            "/mcp", json=INIT, headers={**HEADERS, "Authorization": "Basic dG9wLXNlY3JldA=="}
        )
        assert basic.status_code == 401

        ok = http.post(
            "/mcp", json=INIT, headers={**HEADERS, "Authorization": "Bearer top-secret"}
        )
        assert ok.status_code == 200
        assert ok.json()["result"]["serverInfo"]["name"] == "argus"


def test_http_custom_path_and_tool_call(project: Project):
    config = http_config(project, path="/argus/mcp", auth={"tokens": ["t"]})
    app_ = build_http_app(create_server(config), config.mcp)
    auth = {**HEADERS, "Authorization": "Bearer t"}
    with TestClient(app_, base_url=LOCAL) as http:
        assert http.post("/mcp", json=INIT, headers=auth).status_code == 404
        init = http.post("/argus/mcp", json=INIT, headers=auth)
        assert init.status_code == 200
        # Stateless mode: no session id needed between requests.
        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "argus_list_devices", "arguments": {}},
        }
        response = http.post("/argus/mcp", json=call, headers=auth)
        assert response.status_code == 200
        payload = response.json()["result"]
        names = [d["name"] for d in payload["structuredContent"]["items"]]
        assert "fake_android" in names
        assert json.loads(payload["content"][0]["text"])["total"] == 3
