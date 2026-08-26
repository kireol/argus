from __future__ import annotations

import pytest

from argus_test_creator.models import (
    ConditionDraft,
    Rect,
    StepDraft,
    TestMetadata,
    format_duration,
    parse_duration,
)
from argus_test_creator.models.authoring import AuthoringDocument, Provenance, StepKind
from argus_test_creator.models.capabilities import RecorderCapabilities


@pytest.mark.parametrize(
    "value,expected", [("10s", 10.0), ("250ms", 0.25), ("2m", 120.0), (5, 5.0), ("1.5s", 1.5)]
)
def test_parse_duration(value, expected):
    assert parse_duration(value) == expected


@pytest.mark.parametrize("bad", ["abc", "-1s", "", True])
def test_parse_duration_rejects(bad):
    with pytest.raises(ValueError):
        parse_duration(bad)


@pytest.mark.parametrize(
    "seconds,expected", [(0.25, "250ms"), (1.5, "1.5s"), (10, "10s"), (120, "2m"), (0, "0s")]
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


def test_rect_helpers():
    r = Rect.from_points(50, 60, 10, 20)
    assert r.to_argus() == {"x": 10, "y": 20, "width": 40, "height": 40}
    assert r.contains(10, 20) and not r.contains(50, 60)
    assert r.as_box() == (10, 20, 50, 60)
    assert Rect.from_any({"x": 1, "y": 2, "width": 3, "height": 4}).area == 12
    assert Rect.from_any(None) is None


def test_condition_round_trip_and_describe():
    raw = {"all": [{"type": "image_present", "image": "a.png", "threshold": 0.9},
                   {"not": {"type": "text_present", "text": "Error"}}]}
    draft = ConditionDraft.from_argus(raw)
    assert draft.to_argus() == raw
    assert [leaf.type for leaf in draft.leaves()] == ["image_present", "text_present"]
    assert "Error" in draft.describe()
    assert ConditionDraft(type="text_present", params={"text": "Hi"}).describe() == \
        'Text "Hi" is visible'


def test_step_kind_and_to_argus():
    step = StepDraft(action="wait_until", params={"timeout": "5s"},
                     condition=ConditionDraft(type="text_present", params={"text": "x"}))
    assert step.kind == StepKind.WAIT_UNTIL and step.is_assertion
    assert step.to_argus() == {"action": "wait_until",
                               "condition": {"type": "text_present", "text": "x"},
                               "timeout": "5s"}
    assert StepDraft(action="frob", custom=True).kind == StepKind.CUSTOM
    assert StepDraft(action="device.tap", params={"x": 1, "y": 2}).display_name() == "Tap (1, 2)"


def test_metadata_coerces_comma_lists():
    meta = TestMetadata(tags="a, b", platforms=None)
    assert meta.tags == ["a", "b"] and meta.platforms == []


def test_document_lookups():
    doc = AuthoringDocument()
    step = StepDraft(action="verify",
                     condition=ConditionDraft(type="image_present", params={"image": "m.png"}))
    doc.steps.append(step)
    assert doc.step_index(step.id) == 0 and doc.find_step(step.id) is step
    assert doc.referenced_images() == {"m.png"}
    with pytest.raises(KeyError):
        doc.step_index("nope")


def test_capabilities_enabled_disabled():
    caps = RecorderCapabilities(supports_tap=True)
    assert "tap" in caps.enabled() and "swipe" in caps.disabled()
    assert caps.has("tap") and not caps.has("pinch")


def test_provenance_describe():
    assert "event(s) e1" in Provenance(source="recording", event_ids=("e1",)).describe()
    assert Provenance().describe() == "added manually"
