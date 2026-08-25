"""SerialInstrumentationClient over a fake AgentLink."""

from __future__ import annotations

import pytest

from argus.adapters.esp32.instrumentation import SerialInstrumentationClient
from argus.adapters.esp32.protocol import AgentInfo
from argus.exceptions import DeviceConnectionError, InstrumentationError


class FakeLink:
    def __init__(self) -> None:
        self.answers: dict[str, bytes] = {
            "status": b'{"ready": true, "screen": "menu", "version": "1.2"}',
            "state": b'{"selected": 1, "player": {"state": "playing"}}',
            "hello": b"name=menu version=1.2 fb=none caps=status,state",
        }
        self.fail: str | None = None
        self.requests: list[str] = []

    def request(self, cmd: str, *args: str) -> bytes:
        self.requests.append(cmd)
        if self.fail == cmd:
            raise DeviceConnectionError("link down")
        return self.answers[cmd]

    def hello(self) -> AgentInfo:
        from argus.adapters.esp32.protocol import parse_hello

        return parse_hello(self.request("hello"))


INFO = AgentInfo("menu", "1.2", None, 0, 0, frozenset({"status", "state"}))


@pytest.fixture
def link() -> FakeLink:
    return FakeLink()


def test_status(link):
    client = SerialInstrumentationClient(link, INFO)
    status = client.status()
    assert status.ready is True and status.screen == "menu" and status.version == "1.2"


def test_state_dotted(link):
    client = SerialInstrumentationClient(link, INFO)
    assert client.state() == {"selected": 1, "player": {"state": "playing"}}


def test_capabilities(link):
    assert SerialInstrumentationClient(link, INFO).capabilities() == ["state", "status"]


def test_health(link):
    client = SerialInstrumentationClient(link, INFO)
    assert client.health_check().healthy
    link.fail = "hello"
    assert not client.health_check().healthy


def test_invalid_json(link):
    link.answers["status"] = b"not json"
    with pytest.raises(InstrumentationError, match="JSON"):
        SerialInstrumentationClient(link, INFO).status()


def test_link_error_wrapped(link):
    link.fail = "state"
    with pytest.raises(InstrumentationError, match="link down"):
        SerialInstrumentationClient(link, INFO).state()


def test_unsupported_command(link):
    info = AgentInfo("x", "1", None, 0, 0, frozenset({"status"}))
    with pytest.raises(InstrumentationError, match="state"):
        SerialInstrumentationClient(link, info).state()
