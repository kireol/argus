"""Azure DevOps Pipelines provider (detection + normalized context)."""

from __future__ import annotations

from argus.ci.context import CIContext, Environment, clean, is_truthy
from argus.ci.providers.base import CIProvider, ProviderCapabilities


class AzureProvider(CIProvider):
    name = "azure"
    display_name = "Azure Pipelines"
    capabilities = ProviderCapabilities(supports_artifacts=True)

    def detect(self, environment: Environment) -> bool:
        return is_truthy(environment.get("TF_BUILD")) or bool(
            clean(environment, "AZURE_HTTP_USER_AGENT") and clean(environment, "BUILD_BUILDID")
        )

    def collect_context(self, environment: Environment) -> CIContext:
        get = lambda key: clean(environment, key)  # noqa: E731
        sha = get("BUILD_SOURCEVERSION")
        branch = get("BUILD_SOURCEBRANCHNAME")
        source_branch = get("BUILD_SOURCEBRANCH")
        pull_request = get("SYSTEM_PULLREQUEST_PULLREQUESTNUMBER") or get(
            "SYSTEM_PULLREQUEST_PULLREQUESTID"
        )
        head = get("SYSTEM_PULLREQUEST_SOURCEBRANCH")
        base = get("SYSTEM_PULLREQUEST_TARGETBRANCH")
        for prefix in ("refs/heads/",):
            head = head.removeprefix(prefix) if head else head
            base = base.removeprefix(prefix) if base else base
        if branch is None and source_branch and source_branch.startswith("refs/heads/"):
            branch = source_branch.removeprefix("refs/heads/")
        collection = get("SYSTEM_TEAMFOUNDATIONCOLLECTIONURI")
        project = get("SYSTEM_TEAMPROJECT")
        build_id = get("BUILD_BUILDID")
        run_url = (
            f"{collection.rstrip('/')}/{project}/_build/results?buildId={build_id}"
            if collection and project and build_id
            else None
        )
        return CIContext(
            provider=self.name,
            display_name=self.display_name,
            detected=True,
            repository=get("BUILD_REPOSITORY_NAME"),
            repository_url=get("BUILD_REPOSITORY_URI"),
            branch=head or branch,
            commit=sha,
            commit_sha=sha,
            pull_request=pull_request,
            workflow=get("BUILD_DEFINITIONNAME"),
            job=get("SYSTEM_JOBDISPLAYNAME") or get("AGENT_JOBNAME"),
            run_id=build_id,
            run_number=get("BUILD_BUILDNUMBER"),
            run_url=run_url,
            actor=get("BUILD_REQUESTEDFOR"),
            event=get("BUILD_REASON"),
            workspace=get("BUILD_SOURCESDIRECTORY"),
            base_branch=base,
            head_branch=head,
        )
