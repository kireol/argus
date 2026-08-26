"""Suite resolution and deterministic selector precedence."""

import pytest
from tests.conftest import make_test

from argus.ci.suites import combine_filters, resolve_suite, suite_filter
from argus.config.models import CIConfig, CISuiteConfig
from argus.engine.filters import TestFilter, build_filter
from argus.exceptions import ConfigurationError


def _ci(**suites) -> CIConfig:
    return CIConfig(suites={k: CISuiteConfig(**v) for k, v in suites.items()})


def test_resolve_with_extends_unions_selectors():
    ci = _ci(pr={"tags": ["smoke"]}, merge={"extends": "pr", "tags": ["critical"]})
    merged = resolve_suite(ci, "merge")
    assert merged.tags == ["smoke", "critical"]
    assert suite_filter(merged).tags == ["smoke", "critical"]


def test_unknown_suite_lists_defined_ones():
    with pytest.raises(ConfigurationError) as exc:
        resolve_suite(_ci(pr={"tags": ["smoke"]}), "nightly")
    assert "Unknown suite 'nightly'" in str(exc.value)
    assert "Defined suites: pr" in str(exc.value)


def test_extends_cycle_rejected():
    ci = CIConfig.model_construct(
        suites={
            "a": CISuiteConfig.model_construct(
                extends="b", tags=[], features=[], platforms=[], tests=[]
            ),
            "b": CISuiteConfig.model_construct(
                extends="a", tags=[], features=[], platforms=[], tests=[]
            ),
        }
    )
    with pytest.raises(ConfigurationError) as exc:
        resolve_suite(ci, "a")
    assert "cycle" in str(exc.value)


def test_cli_selectors_narrow_the_suite():
    suite = TestFilter(tags=["smoke"], platforms=["android", "yocto"])
    cli = build_filter(tags=["player"], platforms=["android"], features=["Movies"])
    combined = combine_filters(suite, cli)
    assert combined.tags == ["smoke", "player"]  # both must be present
    assert combined.platforms == ["android"]  # intersection
    assert combined.features == ["Movies"]  # suite had none -> CLI applies
    smoke_player = make_test(tags=["smoke", "player"], platforms=["android"], feature="Movies")
    smoke_only = make_test(tags=["smoke"], platforms=["android"], feature="Movies")
    assert combined.matches(smoke_player)
    assert not combined.matches(smoke_only)


def test_disjoint_selectors_select_nothing_instead_of_everything():
    combined = combine_filters(TestFilter(features=["Movies"]), TestFilter(features=["Settings"]))
    assert not combined.matches(make_test(feature="Movies"))
    assert not combined.matches(make_test(feature="Settings"))


def test_tag_expressions_are_conjoined():
    combined = combine_filters(
        TestFilter(tag_expression="smoke or critical"), TestFilter(tag_expression="not slow")
    )
    assert combined.tag_expression == "(smoke or critical) and (not slow)"
    assert combined.matches(make_test(tags=["smoke"]))
    assert not combined.matches(make_test(tags=["smoke", "slow"]))
