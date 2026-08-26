from __future__ import annotations

from argus_test_creator.models import ConditionDraft, StepDraft
from argus_test_creator.models.authoring import AuthoringDocument, TestMetadata
from argus_test_creator.quality import TestQualityAnalyzer
from argus_test_creator.targets import builtin_targets
from argus_test_creator.validation import validate_document


def _doc(**meta) -> AuthoringDocument:
    base = {"id": "T-1", "name": "Search shows results", "feature": "Search"}
    return AuthoringDocument(metadata=TestMetadata(**{**base, **meta}))


def codes(issues):
    return sorted(i.code for i in issues)


def test_metadata_rules():
    doc = _doc(id="", name="", feature="", timeout="lots", retry_only=["bogus"], retry_count=99)
    doc.steps.append(StepDraft(action="log", params={"message": "x"}))
    assert codes(validate_document(doc)) == sorted([
        "id_required", "name_required", "feature_required", "timeout_format",
        "retry_categories", "retry_count",
    ])
    assert "id_format" in codes(validate_document(_doc(id="1bad")))


def test_step_rules():
    doc = _doc()
    doc.steps += [
        StepDraft(action="device.tap", params={"x": "abc"}),           # missing y, bad x
        StepDraft(action="wait", params={"duration": "2s"}),           # fixed wait warning
        StepDraft(action="verify"),                                    # missing condition
        StepDraft(action="nope.action", custom=True),                  # unknown → warning
        StepDraft(action="shell.run", params={"command": "ls"}),       # info
        StepDraft(action="device.swipe", params={"from_x": 1, "from_y": 1, "to_x": 2,
                                                 "to_y": 2, "duration": "fast"}),
    ]
    issues = validate_document(doc)
    assert {"missing_param", "param_type", "fixed_wait", "missing_condition", "unknown_action",
            "shell_run"} <= set(codes(issues))
    assert sum(1 for i in issues if i.code == "param_type") == 2
    assert all(i.fix for i in issues if i.code == "missing_param")


def test_condition_rules_and_assets(tmp_path):
    doc = _doc()
    doc.steps += [
        StepDraft(action="verify", condition=ConditionDraft(type="image_present", params={})),
        StepDraft(action="verify", condition=ConditionDraft(type="image_present",
                                                            params={"image": "x.png",
                                                                    "threshold": 0.3})),
        StepDraft(action="verify", condition=ConditionDraft(type="log_contains", params={})),
        StepDraft(action="verify", condition=ConditionDraft(type="pixel_matches",
                                                            params={"x": 1, "y": 1,
                                                                    "color": "zzz"})),
        StepDraft(action="verify", condition=ConditionDraft(type="text_present",
                                                            params={"text": "a",
                                                                    "region": {"x": 1}})),
        StepDraft(action="verify", condition=ConditionDraft(all=[])),
        StepDraft(action="verify", condition=ConditionDraft(type="weird", params={})),
        StepDraft(action="verify", condition=ConditionDraft(type="image_present",
                                                            params={"image": "exists.png"})),
    ]
    (tmp_path / "exists.png").write_bytes(b"png")
    issues = validate_document(doc, asset_root=tmp_path)
    got = set(codes(issues))
    assert {"condition_missing_param", "threshold_low", "condition_one_of", "color_format",
            "region_shape", "condition_empty_composite", "unknown_condition",
            "missing_asset"} <= got
    missing = [i for i in issues if i.code == "missing_asset"]
    assert [i.message for i in missing] == ["Image asset 'x.png' is not in the project."]
    first = next(i for i in issues if i.code == "condition_missing_param")
    assert "Missing: image" in first.message and first.fix == "Select an image asset."


def test_target_capability_rules():
    doc = _doc()
    doc.target = builtin_targets()[0].model_copy(update={
        "capabilities": builtin_targets()[0].capabilities.model_copy(
            update={"supports_pinch": False, "supports_ocr": False})
    })
    doc.steps += [
        StepDraft(action="device.pinch", params={"x": 1, "y": 1, "from_distance": 1,
                                                 "to_distance": 2}),
        StepDraft(action="verify", condition=ConditionDraft(type="text_present",
                                                            params={"text": "a"})),
    ]
    assert {"unsupported_action", "unsupported_condition"} <= set(codes(validate_document(doc)))


def test_no_steps_is_error():
    assert "no_steps" in codes(validate_document(_doc()))


def test_quality_analyzer_findings():
    doc = _doc(name="Bad")
    doc.steps += [
        StepDraft(action="device.tap", params={"x": 1, "y": 1}),
        StepDraft(action="device.tap", params={"x": 1, "y": 1}),
        StepDraft(action="wait", params={"duration": "2s"}),
        StepDraft(action="wait", params={"duration": "3s"}),
        StepDraft(action="verify", condition=ConditionDraft(type="screenshot_matches",
                                                            params={"image": "full.png",
                                                                    "threshold": 0.5})),
        StepDraft(action="verify", condition=ConditionDraft(type="text_present",
                                                            params={"text": "${title}"})),
    ]
    report = TestQualityAnalyzer(screen_size=(1920, 1080)).analyze(doc)
    warn_codes = {f.code for f in report.warnings}
    assert {"name", "sync", "fixed_waits", "redundant_tap", "broad_screenshot", "low_threshold",
            "assets", "variables"} <= warn_codes
    assert "1920x1080" in next(f.message for f in report.warnings if f.code == "broad_screenshot")
    assert "totalling 5s" in next(f.message for f in report.warnings if f.code == "fixed_waits")
    assert 0 <= report.score < 50
    assert "Test Quality" in report.render()


def test_quality_analyzer_good_test():
    doc = _doc()
    doc.steps += [
        StepDraft(action="device.tap", params={"x": 1, "y": 1}),
        StepDraft(action="wait_until", params={"timeout": "5s"},
                  condition=ConditionDraft(type="text_present", params={"text": "Done"})),
    ]
    report = TestQualityAnalyzer().analyze(doc)
    assert report.warnings == [] and report.score == 100
