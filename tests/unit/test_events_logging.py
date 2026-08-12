"""Event bus and logging redaction."""

import logging

import pytest

from argus.events.bus import EventBus
from argus.events.events import TestRunStarted, TestStarted
from argus.logging import get_logger, redact


class TestEventBus:
    def test_typed_subscription(self):
        bus = EventBus()
        received = []
        bus.subscribe(received.append, TestStarted)
        bus.publish(TestStarted(test_id="T-1", name="n", feature="f"))
        bus.publish(TestRunStarted(total_tests=1))
        assert len(received) == 1
        assert received[0].test_id == "T-1"

    def test_wildcard_subscription(self):
        bus = EventBus()
        received = []
        bus.subscribe(received.append)
        bus.publish(TestStarted(test_id="T-1", name="n", feature="f"))
        bus.publish(TestRunStarted(total_tests=1))
        assert len(received) == 2

    def test_failing_subscriber_does_not_break_dispatch(self):
        bus = EventBus()
        received = []

        def bad(_event):
            raise RuntimeError("boom")

        bus.subscribe(bad, TestStarted)
        bus.subscribe(received.append, TestStarted)
        bus.publish(TestStarted(test_id="T-1", name="n", feature="f"))
        assert len(received) == 1

    def test_unsubscribe(self):
        bus = EventBus()
        received = []
        bus.subscribe(received.append, TestStarted)
        bus.unsubscribe(received.append, TestStarted)
        bus.publish(TestStarted(test_id="T-1", name="n", feature="f"))
        assert received == []


class TestRedaction:
    def test_authorization_header(self):
        assert "[REDACTED]" in redact("Authorization: Bearer abc123secret")
        assert "abc123secret" not in redact("Authorization: Bearer abc123secret")

    def test_token_assignment(self):
        assert "s3cr3t" not in redact("token=s3cr3t")
        assert "s3cr3t" not in redact("api_key: s3cr3t")
        assert "s3cr3t" not in redact("password = s3cr3t")

    def test_normal_text_untouched(self):
        text = "Movie artwork appears on screen at (100, 200)"
        assert redact(text) == text


class TestContextLogger:
    @pytest.fixture(autouse=True)
    def _propagate_to_caplog(self):
        # configure_logging() disables propagation on the "argus" hierarchy;
        # re-enable it so pytest's caplog handler (on the root logger) sees records.
        utf_logger = logging.getLogger("argus")
        utf_logger.handlers.clear()
        utf_logger.propagate = True
        utf_logger.setLevel(logging.DEBUG)
        yield

    def test_context_attached_to_records(self, caplog):
        logger = get_logger("argus.test", test_id="T-9", feature="Movies")
        with caplog.at_level(logging.INFO, logger="argus.test"):
            logger.info("hello")
        record = caplog.records[-1]
        assert record.test_id == "T-9"
        assert record.feature == "Movies"

    def test_bind_creates_derived_logger(self, caplog):
        logger = get_logger("argus.test", test_id="T-9")
        bound = logger.bind(device="dev-1")
        with caplog.at_level(logging.INFO, logger="argus.test"):
            bound.info("hi")
        record = caplog.records[-1]
        assert record.test_id == "T-9"
        assert record.device == "dev-1"
