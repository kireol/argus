"""Workflow prompts: debugging, authoring, investigation.

Prompts guide the model through the tools; they never claim to know the
answer and they point at Argus documentation instead of embedding it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from pydantic import Field

from argus.mcp.context import ServerContext

_ACTIONS = (
    "backend.set, backend.get/post/put/patch/delete, device.start/stop/restart/reset, "
    "device.tap, device.swipe, device.long_press, device.drag, device.multi_touch, "
    "device.pinch, device.key, wait, wait_until, verify, screenshot, log, shell.run"
)
_CONDITIONS = (
    "image_present, image_not_present, screenshot_matches, text_present, text_not_present, "
    "pixel_matches, instrumentation_value, application_state, backend_value, log_contains, "
    "now_playing; composable with all / any / not"
)

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_testing_prompts(server: MCPServer, ctx: ServerContext) -> None:
    @server.prompt(
        name="argus_debug_failed_test",
        title="Debug a failed Argus test",
        description="Step-by-step investigation of one failed test in a run.",
    )
    def debug_failed_test(
        run_id: Annotated[str, Field(description="Run ID (run-…).")],
        test_id: Annotated[str | None, Field(description="Failed test ID (optional).")] = None,
    ) -> str:
        target = f"test {test_id}" if test_id else "the failed test(s)"
        return f"""Investigate why {target} failed in Argus run {run_id}. Work step by step and
report evidence, not guesses.

1. argus_get_run({run_id!r}) — confirm the run finished and note status, stop reason and counts.
2. argus_diagnose_run({run_id!r}{f", test_id={test_id!r}" if test_id else ""}) — identify the
   failing step, failure_category, expected vs observed, and the artifact ids.
3. argus_get_artifact for the actual.png, expected.png and diff.png of the failed test and
   describe concretely what differs on screen (missing element, wrong content, offset, timing).
4. argus_get_artifact for logs.txt and instrumentation.json — look for errors, the app's
   reported screen/state, and whether the backend change was observed by the app.
5. argus_get_test(test_id) — check thresholds, regions, timeouts and the reference image name.
6. argus_get_device(device, probe=true) for the device used — is it healthy and showing the app?
7. Conclude with: the most likely cause (product bug / flaky timing / wrong reference image /
   environment), the evidence for it, and the single next diagnostic or fix step. If the
   evidence is inconclusive say so and propose what to capture next (e.g. a screenshot now
   via argus_capture_screenshot, or rerun with save_comparisons=true).
Never lower a threshold or replace a reference image just to make the test pass."""

    @server.prompt(
        name="argus_create_test",
        title="Write a new Argus test",
        description="Author a valid Argus YAML test for a feature.",
    )
    def create_test(
        feature: Annotated[str, Field(description="Feature the test belongs to.")],
        goal: Annotated[str, Field(description="What the test must verify, in one sentence.")],
        platform: Annotated[str | None, Field(description="Target platform label.")] = None,
    ) -> str:
        platforms = f"[{platform}]" if platform else "the platforms configured (argus_list_devices)"
        return f"""Write an Argus YAML test for feature {feature!r} that verifies: {goal}
Target platforms: {platforms}.

Before writing:
- argus_list_tests(feature={feature!r}) and argus_get_test on one similar test to copy the
  house style (ids like MOV-001, tags, parameters, timeouts).
- Read resource argus://configuration for variables, regions and backend setup.
- If you need to see the screen, argus_capture_screenshot(device).

Schema (all validated at load time):
  id (unique, ^[A-Za-z][A-Za-z0-9_-]*$), name, description, feature, tags[], platforms[],
  parameters{{}}, requires{{devices: []}}, timeout, retry{{count, only}}, setup[], steps[],
  teardown[]. Each step: action + parameters; wait_until/verify take a condition.
Actions: {_ACTIONS}.
Conditions: {_CONDITIONS}.

Verification philosophy — non-negotiable:
- A test passes only on externally observed evidence (image/text on screen). Instrumentation
  (instrumentation_value, application_state) may synchronize or diagnose, never replace a
  visual verify.
- Drive state through backend.* actions, wait_until the screen shows it (with a timeout),
  then verify. Reference images live under asset_paths; name them in the condition.
- Keep thresholds at the configured default unless there is a documented reason.
- Put environment specifics (hosts, credentials) in configuration, never in the test.

Common mistakes: duplicate ids; platforms not matching any configured device; missing
reference image; verify without a preceding wait_until; retrying assertion failures;
hard-coded sleeps instead of wait_until.

Full reference: docs/test-authoring.md and docs/image-verification.md in the repository.
After writing the file, run argus_validate(framework_only=true) to confirm it loads, then
argus_preflight(test_ids=[...]) and argus_run_test."""

    @server.prompt(
        name="argus_investigate_failure",
        title="Investigate an Argus failure",
        description="General workflow when a run or the environment is failing.",
    )
    def investigate_failure(
        run_id: Annotated[str | None, Field(description="Run ID if a run failed.")] = None,
    ) -> str:
        run_part = (
            f"Start with argus_get_run({run_id!r}) and argus_diagnose_run({run_id!r})."
            if run_id
            else "If a run_id is known, start with argus_get_run and argus_diagnose_run; "
            "otherwise argus_list_runs."
        )
        return f"""Investigate the failure methodically. {run_part}

Classify first:
- preflight_failed / errored → environment problem: argus_validate(framework_only=false),
  argus_list_devices, argus_get_device(probe=true); fix what the remediation says, confirm with
  argus_preflight, then rerun.
- failure_category timeout / assertion → product or test problem: inspect actual/expected/diff
  via argus_get_artifact, logs.txt, instrumentation.json; compare with argus_get_test.
- device_connection / screenshot / backend → infrastructure: probe the device, check the
  backend health in argus_validate, retry once only if the error is marked retryable.

Then state: what failed, the evidence, the most likely cause, and the next action. Do not
modify tests, thresholds, or reference images as a first response to a failure."""
