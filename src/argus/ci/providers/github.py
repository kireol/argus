"""GitHub Actions provider (reference implementation)."""

from __future__ import annotations

from argus.ci.context import CIContext, Environment, clean, is_truthy
from argus.ci.providers.base import CIProvider, ProviderCapabilities


class GitHubProvider(CIProvider):
    name = "github"
    display_name = "GitHub Actions"
    capabilities = ProviderCapabilities(
        supports_summary=True,
        supports_annotations=True,
        supports_checks=False,  # extension point: needs API credentials
        supports_artifacts=True,  # via the action (actions/upload-artifact)
        supports_pr_comments=False,
    )

    def detect(self, environment: Environment) -> bool:
        return is_truthy(environment.get("GITHUB_ACTIONS"))

    def collect_context(self, environment: Environment) -> CIContext:
        get = lambda key: clean(environment, key)  # noqa: E731
        server = get("GITHUB_SERVER_URL") or "https://github.com"
        repository = get("GITHUB_REPOSITORY")
        ref = get("GITHUB_REF")
        ref_name = get("GITHUB_REF_NAME")
        head_ref = get("GITHUB_HEAD_REF")
        pull_request: str | None = None
        if ref and ref.startswith("refs/pull/"):
            parts = ref.split("/")
            if len(parts) >= 3 and parts[2].isdigit():
                pull_request = parts[2]
        branch = head_ref or ref_name
        if branch is None and ref and ref.startswith("refs/heads/"):
            branch = ref.removeprefix("refs/heads/")
        sha = get("GITHUB_SHA")
        run_id = get("GITHUB_RUN_ID")
        run_url = f"{server}/{repository}/actions/runs/{run_id}" if repository and run_id else None
        return CIContext(
            provider=self.name,
            display_name=self.display_name,
            detected=True,
            repository=repository,
            repository_url=f"{server}/{repository}" if repository else None,
            branch=branch,
            commit=sha,
            commit_sha=sha,
            pull_request=pull_request,
            workflow=get("GITHUB_WORKFLOW"),
            job=get("GITHUB_JOB"),
            run_id=run_id,
            run_number=get("GITHUB_RUN_NUMBER"),
            run_url=run_url,
            actor=get("GITHUB_ACTOR"),
            event=get("GITHUB_EVENT_NAME"),
            workspace=get("GITHUB_WORKSPACE"),
            base_branch=get("GITHUB_BASE_REF"),
            head_branch=head_ref,
        )
