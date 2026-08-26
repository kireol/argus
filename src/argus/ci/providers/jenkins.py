"""Jenkins provider (detection + normalized context; generic reporting)."""

from __future__ import annotations

from argus.ci.context import CIContext, Environment, clean
from argus.ci.providers.base import CIProvider, ProviderCapabilities


class JenkinsProvider(CIProvider):
    name = "jenkins"
    display_name = "Jenkins"
    capabilities = ProviderCapabilities(supports_artifacts=True)

    def detect(self, environment: Environment) -> bool:
        if clean(environment, "JENKINS_URL"):
            return True
        return bool(clean(environment, "BUILD_ID") and clean(environment, "JOB_NAME"))

    def collect_context(self, environment: Environment) -> CIContext:
        get = lambda key: clean(environment, key)  # noqa: E731
        sha = get("GIT_COMMIT")
        branch = get("GIT_BRANCH") or get("BRANCH_NAME")
        if branch and branch.startswith("origin/"):
            branch = branch.removeprefix("origin/")
        return CIContext(
            provider=self.name,
            display_name=self.display_name,
            detected=True,
            repository=get("GIT_URL"),
            repository_url=get("GIT_URL"),
            branch=branch,
            commit=sha,
            commit_sha=sha,
            pull_request=get("CHANGE_ID"),
            workflow=get("JOB_NAME"),
            job=get("JOB_BASE_NAME") or get("JOB_NAME"),
            run_id=get("BUILD_ID"),
            run_number=get("BUILD_NUMBER"),
            run_url=get("BUILD_URL"),
            actor=get("BUILD_USER_ID") or get("CHANGE_AUTHOR"),
            event=None,
            workspace=get("WORKSPACE"),
            base_branch=get("CHANGE_TARGET"),
            head_branch=get("CHANGE_BRANCH"),
        )
