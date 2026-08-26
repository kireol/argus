"""CI provider detection and context normalization."""

import pytest

from argus.ci.providers import (
    AzureProvider,
    GenericProvider,
    GitHubProvider,
    GitLabProvider,
    JenkinsProvider,
    LocalProvider,
    ProviderRegistry,
    default_provider_registry,
)
from argus.exceptions import ConfigurationError

GITHUB_ENV = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_REPOSITORY": "kireol/argus",
    "GITHUB_REF": "refs/pull/142/merge",
    "GITHUB_REF_NAME": "142/merge",
    "GITHUB_HEAD_REF": "feature/ci",
    "GITHUB_BASE_REF": "main",
    "GITHUB_SHA": "abc123def456",
    "GITHUB_WORKFLOW": "CI",
    "GITHUB_JOB": "visual-tests",
    "GITHUB_RUN_ID": "123456",
    "GITHUB_RUN_NUMBER": "17",
    "GITHUB_ACTOR": "octocat",
    "GITHUB_EVENT_NAME": "pull_request",
    "GITHUB_WORKSPACE": "/home/runner/work/argus",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_TOKEN": "ghs_secret",
}


def test_github_detected_and_normalized():
    registry = default_provider_registry()
    provider = registry.detect(GITHUB_ENV)
    assert isinstance(provider, GitHubProvider)
    ctx = provider.collect_context(GITHUB_ENV)
    assert ctx.provider == "github"
    assert ctx.detected
    assert ctx.repository == "kireol/argus"
    assert ctx.repository_url == "https://github.com/kireol/argus"
    assert ctx.branch == "feature/ci"
    assert ctx.commit_sha == "abc123def456"
    assert ctx.short_commit == "abc123d"
    assert ctx.pull_request == "142"
    assert ctx.workflow == "CI"
    assert ctx.job == "visual-tests"
    assert ctx.run_id == "123456"
    assert ctx.run_number == "17"
    assert ctx.run_url == "https://github.com/kireol/argus/actions/runs/123456"
    assert ctx.actor == "octocat"
    assert ctx.event == "pull_request"
    assert ctx.base_branch == "main"
    assert ctx.head_branch == "feature/ci"
    # Secrets never enter the context.
    assert "ghs_secret" not in str(ctx.to_dict())


def test_github_push_branch_from_ref():
    env = {"GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": "1234567"}
    ctx = GitHubProvider().collect_context(env)
    assert ctx.branch == "main"
    assert ctx.pull_request is None
    assert ctx.repository is None  # missing values are None, never fabricated


def test_gitlab_detected():
    env = {
        "GITLAB_CI": "true",
        "CI_PROJECT_PATH": "group/argus",
        "CI_COMMIT_REF_NAME": "feature/x",
        "CI_COMMIT_SHA": "deadbeefcafe",
        "CI_MERGE_REQUEST_IID": "7",
        "CI_JOB_NAME": "test",
        "CI_PIPELINE_ID": "99",
        "CI_PIPELINE_IID": "12",
        "CI_PIPELINE_SOURCE": "merge_request_event",
        "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "main",
        "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": "feature/x",
    }
    provider = default_provider_registry().detect(env)
    assert isinstance(provider, GitLabProvider)
    ctx = provider.collect_context(env)
    assert ctx.repository == "group/argus"
    assert ctx.branch == "feature/x"
    assert ctx.pull_request == "7"
    assert ctx.run_id == "99"
    assert ctx.run_number == "12"
    assert ctx.base_branch == "main"


def test_jenkins_detected():
    env = {
        "JENKINS_URL": "https://ci.example/",
        "BUILD_ID": "42",
        "BUILD_NUMBER": "42",
        "JOB_NAME": "argus/main",
        "GIT_COMMIT": "0123456789ab",
        "GIT_BRANCH": "origin/main",
        "BUILD_URL": "https://ci.example/job/argus/42/",
    }
    provider = default_provider_registry().detect(env)
    assert isinstance(provider, JenkinsProvider)
    ctx = provider.collect_context(env)
    assert ctx.branch == "main"
    assert ctx.run_id == "42"
    assert ctx.workflow == "argus/main"
    assert ctx.run_url == "https://ci.example/job/argus/42/"


def test_jenkins_detected_without_url():
    assert JenkinsProvider().detect({"BUILD_ID": "1", "JOB_NAME": "x"})
    assert not JenkinsProvider().detect({"BUILD_ID": "1"})


def test_azure_detected():
    env = {
        "TF_BUILD": "True",
        "BUILD_REPOSITORY_NAME": "org/argus",
        "BUILD_SOURCEBRANCH": "refs/heads/main",
        "BUILD_SOURCEVERSION": "fedcba987654",
        "BUILD_BUILDID": "555",
        "BUILD_BUILDNUMBER": "20260826.1",
        "BUILD_DEFINITIONNAME": "argus-ci",
        "SYSTEM_PULLREQUEST_PULLREQUESTNUMBER": "9",
        "SYSTEM_PULLREQUEST_SOURCEBRANCH": "refs/heads/feature/y",
        "SYSTEM_PULLREQUEST_TARGETBRANCH": "refs/heads/main",
        "SYSTEM_TEAMFOUNDATIONCOLLECTIONURI": "https://dev.azure.com/org/",
        "SYSTEM_TEAMPROJECT": "proj",
    }
    provider = default_provider_registry().detect(env)
    assert isinstance(provider, AzureProvider)
    ctx = provider.collect_context(env)
    assert ctx.pull_request == "9"
    assert ctx.head_branch == "feature/y"
    assert ctx.base_branch == "main"
    assert ctx.branch == "feature/y"
    assert ctx.run_url == "https://dev.azure.com/org/proj/_build/results?buildId=555"


def test_generic_detected():
    env = {"CI": "true", "CIRCLECI": "true", "CIRCLE_BRANCH": "dev", "CIRCLE_SHA1": "abc"}
    provider = default_provider_registry().detect(env)
    assert isinstance(provider, GenericProvider)
    ctx = provider.collect_context(env)
    assert ctx.branch == "dev"
    assert ctx.commit == "abc"


def test_generic_ignores_false_ci_variable():
    assert not GenericProvider().detect({"CI": "false"})


def test_local_when_nothing_detected():
    provider = default_provider_registry().detect({"HOME": "/home/x", "PATH": "/bin"})
    assert isinstance(provider, LocalProvider)
    ctx = provider.collect_context({})
    assert ctx.provider == "local"
    assert not ctx.detected
    assert ctx.branch is None


def test_registry_resolve_named_and_unknown():
    registry = default_provider_registry()
    assert registry.resolve("github", {}).name == "github"
    assert registry.resolve("auto", GITHUB_ENV).name == "github"
    with pytest.raises(ConfigurationError) as exc:
        registry.resolve("bamboo", {})
    assert "Unknown CI provider 'bamboo'" in str(exc.value)
    assert "github" in str(exc.value)


def test_registry_order_and_replacement():
    registry = ProviderRegistry()
    registry.register(LocalProvider())
    registry.register(GitHubProvider())
    # Registration order matters: local matches everything, so it wins here.
    assert registry.detect(GITHUB_ENV).name == "local"
    registry.register(LocalProvider())  # re-register moves it to the end
    assert registry.detect(GITHUB_ENV).name == "github"
    assert registry.names() == ["github", "local"]
