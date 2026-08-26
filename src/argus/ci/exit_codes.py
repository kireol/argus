"""The public exit-code contract of ``argus ci run`` (centralized; never inline literals).

``argus run`` keeps its historical codes (0/1/2/3). The CI command exposes a
finer-grained, documented contract so pipelines can branch on the cause::

    0  SUCCESS                all required tests passed (policy satisfied)
    1  TEST_FAILURE           one or more tests failed
    2  CONFIGURATION_ERROR    invalid configuration / unknown suite / bad option
    3  ENVIRONMENT_ERROR      preflight or setup failed; devices unavailable
    4  TEST_DEFINITION_ERROR  a test definition is invalid
    5  CI_ERROR               reporting/artifact publication failed
    6  POLICY_FAILURE         quality gate failed independently of raw results
    7  INTERNAL_ERROR         unexpected Argus error
    8  CANCELLED              interrupted (SIGINT/SIGTERM) before completion
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    TEST_FAILURE = 1
    CONFIGURATION_ERROR = 2
    ENVIRONMENT_ERROR = 3
    TEST_DEFINITION_ERROR = 4
    CI_ERROR = 5
    POLICY_FAILURE = 6
    INTERNAL_ERROR = 7
    CANCELLED = 8

    @property
    def description(self) -> str:
        return _DESCRIPTIONS[self]


_DESCRIPTIONS: dict[ExitCode, str] = {
    ExitCode.SUCCESS: "all required tests passed",
    ExitCode.TEST_FAILURE: "one or more tests failed",
    ExitCode.CONFIGURATION_ERROR: "configuration error",
    ExitCode.ENVIRONMENT_ERROR: "environment/device unavailable",
    ExitCode.TEST_DEFINITION_ERROR: "invalid test definition",
    ExitCode.CI_ERROR: "CI infrastructure/reporting failure",
    ExitCode.POLICY_FAILURE: "quality-policy failure",
    ExitCode.INTERNAL_ERROR: "internal Argus error",
    ExitCode.CANCELLED: "run cancelled",
}
