"""GitLab CI provider (detection + normalized context; generic reporting)."""

from __future__ import annotations

from argus.ci.context import CIContext, Environment, clean, is_truthy
from argus.ci.providers.base import CIProvider, ProviderCapabilities


class GitLabProvider(CIProvider):
    name = "gitlab"
    display_name = "GitLab CI"
    capabilities = ProviderCapabilities(supports_artifacts=True)

    def detect(self, environment: Environment) -> bool:
        return is_truthy(environment.get("GITLAB_CI"))

    def collect_context(self, environment: Environment) -> CIContext:
        get = lambda key: clean(environment, key)  # noqa: E731
        sha = get("CI_COMMIT_SHA")
        mr = get("CI_MERGE_REQUEST_IID")
        return CIContext(
            provider=self.name,
            display_name=self.display_name,
            detected=True,
            repository=get("CI_PROJECT_PATH"),
            repository_url=get("CI_PROJECT_URL"),
            branch=get("CI_COMMIT_REF_NAME"),
            commit=sha,
            commit_sha=sha,
            pull_request=mr,
            workflow=get("CI_PIPELINE_SOURCE"),
            job=get("CI_JOB_NAME"),
            run_id=get("CI_PIPELINE_ID"),
            run_number=get("CI_PIPELINE_IID"),
            run_url=get("CI_PIPELINE_URL"),
            actor=get("GITLAB_USER_LOGIN"),
            event=get("CI_PIPELINE_SOURCE"),
            workspace=get("CI_PROJECT_DIR"),
            base_branch=get("CI_MERGE_REQUEST_TARGET_BRANCH_NAME"),
            head_branch=get("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME"),
        )
