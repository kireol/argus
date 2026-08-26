"""Generic and local providers — always available, never need provider APIs."""

from __future__ import annotations

from argus.ci.context import CIContext, Environment, clean, is_truthy
from argus.ci.providers.base import CIProvider, ProviderCapabilities

# Common indicators set by CircleCI, Buildkite, TeamCity, Bitbucket, Travis,
# Drone, Woodpecker, and most shell-based CI systems.
_GENERIC_INDICATORS = (
    "CI",
    "CONTINUOUS_INTEGRATION",
    "BUILD_NUMBER",
    "CIRCLECI",
    "BUILDKITE",
    "TEAMCITY_VERSION",
    "BITBUCKET_BUILD_NUMBER",
    "TRAVIS",
    "DRONE",
    "CODEBUILD_BUILD_ID",
)

# Best-effort, widely used variable names for normalized fields.
_BRANCH_VARS = (
    "CIRCLE_BRANCH",
    "BUILDKITE_BRANCH",
    "BITBUCKET_BRANCH",
    "TRAVIS_BRANCH",
    "DRONE_BRANCH",
    "CI_BRANCH",
    "BRANCH_NAME",
)
_COMMIT_VARS = (
    "CIRCLE_SHA1",
    "BUILDKITE_COMMIT",
    "BITBUCKET_COMMIT",
    "TRAVIS_COMMIT",
    "DRONE_COMMIT",
    "CI_COMMIT",
    "GIT_COMMIT",
    "CODEBUILD_RESOLVED_SOURCE_VERSION",
)
_RUN_ID_VARS = (
    "CIRCLE_WORKFLOW_ID",
    "BUILDKITE_BUILD_ID",
    "BITBUCKET_PIPELINE_UUID",
    "TRAVIS_BUILD_ID",
    "DRONE_BUILD_NUMBER",
    "CODEBUILD_BUILD_ID",
    "BUILD_ID",
)
_RUN_NUMBER_VARS = (
    "CIRCLE_BUILD_NUM",
    "BUILDKITE_BUILD_NUMBER",
    "BITBUCKET_BUILD_NUMBER",
    "TRAVIS_BUILD_NUMBER",
    "DRONE_BUILD_NUMBER",
    "BUILD_NUMBER",
)
_PR_VARS = (
    "CIRCLE_PR_NUMBER",
    "BUILDKITE_PULL_REQUEST",
    "BITBUCKET_PR_ID",
    "TRAVIS_PULL_REQUEST",
    "DRONE_PULL_REQUEST",
    "CHANGE_ID",
)
_REPO_VARS = (
    "CIRCLE_PROJECT_REPONAME",
    "BUILDKITE_PIPELINE_SLUG",
    "BITBUCKET_REPO_FULL_NAME",
    "TRAVIS_REPO_SLUG",
    "DRONE_REPO",
)
_JOB_VARS = ("CIRCLE_JOB", "BUILDKITE_LABEL", "BITBUCKET_STEP_UUID", "TRAVIS_JOB_NAME")
_ACTOR_VARS = ("CIRCLE_USERNAME", "BUILDKITE_BUILD_CREATOR", "DRONE_COMMIT_AUTHOR")
_WORKSPACE_VARS = (
    "CIRCLE_WORKING_DIRECTORY",
    "BUILDKITE_BUILD_CHECKOUT_PATH",
    "BITBUCKET_CLONE_DIR",
    "TRAVIS_BUILD_DIR",
    "DRONE_WORKSPACE",
    "WORKSPACE",
)


def _first(environment: Environment, names: tuple[str, ...]) -> str | None:
    for name in names:
        value = clean(environment, name)
        if value is not None and value.lower() not in {"false", "0"}:
            return value
    return None


class GenericProvider(CIProvider):
    """Any CI system that sets a standard indicator such as ``CI=true``."""

    name = "generic"
    display_name = "Generic CI"
    capabilities = ProviderCapabilities()

    def detect(self, environment: Environment) -> bool:
        return any(
            is_truthy(environment.get(name)) or (name != "CI" and bool(clean(environment, name)))
            for name in _GENERIC_INDICATORS
        )

    def collect_context(self, environment: Environment) -> CIContext:
        sha = _first(environment, _COMMIT_VARS)
        pull_request = _first(environment, _PR_VARS)
        return CIContext(
            provider=self.name,
            display_name=self.display_name,
            detected=True,
            repository=_first(environment, _REPO_VARS),
            branch=_first(environment, _BRANCH_VARS),
            commit=sha,
            commit_sha=sha,
            pull_request=pull_request,
            job=_first(environment, _JOB_VARS),
            run_id=_first(environment, _RUN_ID_VARS),
            run_number=_first(environment, _RUN_NUMBER_VARS),
            actor=_first(environment, _ACTOR_VARS),
            workspace=_first(environment, _WORKSPACE_VARS),
        )


class LocalProvider(CIProvider):
    """No CI detected: ``argus ci run`` still works (reports, exit codes)."""

    name = "local"
    display_name = "Local"
    capabilities = ProviderCapabilities()

    def detect(self, environment: Environment) -> bool:
        return True

    def collect_context(self, environment: Environment) -> CIContext:
        return CIContext(provider=self.name, display_name=self.display_name, detected=False)
