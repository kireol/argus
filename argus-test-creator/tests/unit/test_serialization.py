from __future__ import annotations

import textwrap

import pytest
import yaml

from argus_test_creator.core.errors import SerializationError
from argus_test_creator.models import ConditionDraft, StepDraft
from argus_test_creator.models.authoring import AuthoringDocument, TestMetadata
from argus_test_creator.serialization import (
    document_from_yaml,
    document_to_argus,
    document_to_yaml,
)

SAMPLE = textwrap.dedent(
    """
    id: MOV-001
    name: Movie artwork appears
    description: >
      Verify that the correct movie artwork appears.
    feature: Movies
    tags: [smoke, movies]
    platforms: [android]
    priority: high
    timeout: 60s
    parameters:
      movie_id: 123
    retry:
      count: 2
      only: [timeout]
    setup:
      - action: device.reset
    steps:
      - action: backend.set
        data:
          movieId: ${movie_id}
      - action: wait_until
        name: Artwork shows
        condition:
          all:
            - type: image_present
              image: movie_123.png
              threshold: 0.9
            - not:
                type: text_present
                text: Loading
        timeout: 10s
      - action: custom.thing
        foo: [1, 2]
    teardown:
      - action: backend.set
        data: {movieId: null}
    x-extra: keep me
    """
)


def test_import_preserves_everything():
    doc = document_from_yaml(SAMPLE)
    m = doc.metadata
    assert m.id == "MOV-001" and m.tags == ["smoke", "movies"] and m.retry_count == 2
    assert m.retry_only == ["timeout"] and m.parameters == {"movie_id": 123}
    assert len(doc.setup) == 1 and len(doc.teardown) == 1 and len(doc.steps) == 3
    wait = doc.steps[1]
    assert wait.name == "Artwork shows" and wait.condition is not None
    assert wait.condition.form == "all" and wait.params == {"timeout": "10s"}
    custom = doc.steps[2]
    assert custom.custom and custom.params == {"foo": [1, 2]}
    assert doc.unknown_fields == {"x-extra": "keep me"}
    assert doc.steps[0].provenance.source == "import"


def test_round_trip_is_semantically_lossless_and_stable():
    doc = document_from_yaml(SAMPLE)
    text = document_to_yaml(doc)
    assert yaml.safe_load(text) == yaml.safe_load(SAMPLE)
    assert document_to_yaml(document_from_yaml(text)) == text


def test_writer_key_order_and_omits_defaults():
    doc = AuthoringDocument(metadata=TestMetadata(id="T-1", name="N", feature="F"))
    doc.steps.append(StepDraft(action="device.tap", params={"y": 2, "x": 1}))
    data = document_to_argus(doc)
    assert list(data) == ["id", "name", "feature", "steps"]
    text = document_to_yaml(doc)
    assert text == "id: T-1\nname: N\nfeature: F\n\nsteps:\n  - action: device.tap\n    y: 2\n    x: 1\n"  # noqa: E501


def test_writer_skips_disabled_steps_unless_asked():
    doc = AuthoringDocument(metadata=TestMetadata(id="T", name="N", feature="F"))
    doc.steps.append(StepDraft(action="log", params={"message": "a"}, enabled=False))
    doc.steps.append(StepDraft(action="log", params={"message": "b"}))
    assert len(document_to_argus(doc)["steps"]) == 1
    assert len(document_to_argus(doc, include_disabled=True)["steps"]) == 2


def test_writer_condition_precedes_params_and_quotes_when_needed():
    doc = AuthoringDocument(metadata=TestMetadata(id="T", name="N", feature="F"))
    doc.steps.append(StepDraft(
        action="verify", params={"timeout": "5s"},
        condition=ConditionDraft(type="text_present", params={"text": "yes: no"}),
    ))
    text = document_to_yaml(doc)
    assert "condition:\n      type: text_present\n      text: 'yes: no'" in text
    assert yaml.safe_load(text)["steps"][0]["condition"]["text"] == "yes: no"


def test_multiline_description_uses_block_style():
    doc = AuthoringDocument(metadata=TestMetadata(id="T", name="N", feature="F",
                                                  description="line one\nline two\n"))
    doc.steps.append(StepDraft(action="log", params={"message": "x"}))
    text = document_to_yaml(doc)
    assert "description: >" in text
    assert yaml.safe_load(text)["description"].strip() == "line one\nline two"


@pytest.mark.parametrize(
    "bad", ["", "- just a list", "steps: 3\nid: x", "tests: [{id: a}, {id: b}]"]
)
def test_import_errors_are_actionable(bad):
    with pytest.raises(SerializationError):
        document_from_yaml(bad)


def test_import_single_test_from_tests_list():
    doc = document_from_yaml("tests:\n  - id: A\n    name: n\n    feature: f\n    steps: []\n")
    assert doc.metadata.id == "A"


def test_import_invalid_step_and_condition():
    with pytest.raises(SerializationError):
        document_from_yaml("id: A\nname: n\nfeature: f\nsteps:\n  - {x: 1}\n")
    with pytest.raises(SerializationError):
        document_from_yaml("id: A\nname: n\nfeature: f\nsteps:\n  - action: verify\n    condition: 5\n")  # noqa: E501
