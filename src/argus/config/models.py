"""Configuration models.

All environment-specific values (hosts, credentials, serials, thresholds)
live in configuration — never in code or test definitions. Secrets are
referenced via ``${ENV_VAR}`` and resolved at load time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from argus.models.common import Region
from argus.utilities.duration import parse_duration


class BackendConfig(BaseModel):
    """Generic HTTP backend configuration.

    ``type: fake`` swaps in the in-memory FakeBackend (development/demos).
    """

    model_config = ConfigDict(extra="forbid")

    type: str = "http"  # "http" | "fake"
    initial_state: dict[str, Any] = Field(default_factory=dict)
    base_url: str | None = None
    token: str | None = None
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: str | float = "10s"
    retries: int = Field(default=2, ge=0, le=10)
    verify_tls: bool = True
    state_endpoint: str = "/api/state"
    health_endpoint: str = "/health"

    @property
    def timeout_seconds(self) -> float:
        return parse_duration(self.timeout)

    @property
    def configured(self) -> bool:
        if self.type == "fake":
            return True
        return bool(self.base_url) and "${" not in (self.base_url or "")


class InstrumentationConfig(BaseModel):
    """HTTP instrumentation endpoint for one application/device.

    ``type: fake`` swaps in the in-memory FakeInstrumentation, seeded with
    ``status``/``state`` (development/demos). ``type: device`` asks the device
    adapter for a client instead (e.g. the ESP32 agent's status/state over serial).
    """

    model_config = ConfigDict(extra="forbid")

    type: str = "http"  # "http" | "fake" | "device"
    status: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)
    base_url: str | None = None
    timeout: str | float = "5s"
    status_endpoint: str = "/test/status"
    health_endpoint: str = "/test/health"
    state_endpoint: str = "/test/state"

    @property
    def timeout_seconds(self) -> float:
        return parse_duration(self.timeout)

    @property
    def configured(self) -> bool:
        if self.type == "fake":
            return True
        return bool(self.base_url) and "${" not in (self.base_url or "")


class DeviceConfig(BaseModel):
    """A named device entry.

    ``type`` selects the adapter; adapter-specific settings are free-form and
    validated by the adapter itself, so new adapters need no core changes.
    """

    model_config = ConfigDict(extra="allow")

    type: str
    platform: str | None = None
    instrumentation: InstrumentationConfig | None = None

    @property
    def options(self) -> dict[str, Any]:
        return dict(self.model_extra or {})

    @property
    def effective_platform(self) -> str:
        """Platform label used for test filtering; defaults to adapter type."""
        return self.platform or self.type

    @property
    def configured(self) -> bool:
        """False when required values still contain unresolved ${...} refs."""
        return not _has_unresolved(self.options)


def _has_unresolved(value: Any) -> bool:
    if isinstance(value, str):
        return "${" in value
    if isinstance(value, dict):
        return any(_has_unresolved(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_unresolved(v) for v in value)
    return False


class ImageVerificationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    grayscale: bool = False
    scale_tolerance: float = Field(default=0.0, ge=0.0, le=0.5)
    match_method: str = "ccoeff_normed"


class VerificationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: ImageVerificationConfig = Field(default_factory=ImageVerificationConfig)


class OCRConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "tesseract"
    language: str = "eng"
    # Isolate near-white glyphs (e.g. letters and numbers on colorful wallpaper).
    isolate_light_text: bool = False
    isolate_light_text_luminance: int = Field(default=180, ge=0, le=255)


class ResultsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dir: str = "results"
    retain_on_success: bool = False
    save_screenshots_on_failure: bool = True
    # Save actual/expected/diff for image verifies (pass or fail) and keep them
    # for the HTML report. Implies retaining those artifact dirs on success.
    save_comparison_images: bool = False


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str = "INFO"
    format: str = "text"  # "text" | "json"
    file: str | None = None


class WaitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_timeout: str | float = "10s"
    # 500ms balances UI settle time vs capture cost; Android screencap often
    # takes 250–800ms so tighter intervals mainly stack adb load.
    default_poll_interval: str | float = "500ms"
    # When verify immediately follows a successful wait_until with the same
    # condition, reuse that result instead of another screencap + match.
    reuse_wait_result_on_verify: bool = True

    @property
    def timeout_seconds(self) -> float:
        return parse_duration(self.default_timeout)

    @property
    def poll_interval_seconds(self) -> float:
        return parse_duration(self.default_poll_interval)


class SetupCommand(BaseModel):
    """A host command run from configuration (``setup`` or ``before_each``).

    Same shape as ``shell.run``: prefer ``args`` as a list so values are not
    re-parsed by a shell.
    """

    model_config = ConfigDict(extra="forbid")

    command: str
    args: list[str] = Field(default_factory=list)
    timeout: str | float = "60s"
    name: str | None = None
    cwd: str | None = None

    @property
    def timeout_seconds(self) -> float:
        return parse_duration(self.timeout)


class PreflightTcpService(BaseModel):
    """Require a TCP listener to be reachable before tests run.

    Use for non-HTTP backends such as GelOS DataPipe (``host:port``).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    address: str  # host:port (IPv6 as [addr]:port)
    timeout: str | float = "2s"
    required: bool = True
    remediation: str | None = None

    @property
    def timeout_seconds(self) -> float:
        return parse_duration(self.timeout)


class PreflightConfig(BaseModel):
    """Optional pre-flight probes beyond the built-in subsystem checks."""

    model_config = ConfigDict(extra="forbid")

    services: list[PreflightTcpService] = Field(default_factory=list)


class MCPAuthConfig(BaseModel):
    """Authentication for the Streamable HTTP transport.

    ``tokens`` are static bearer tokens (reference them as ``${ENV_VAR}``).
    Binding to a non-loopback host without tokens is refused.
    """

    model_config = ConfigDict(extra="forbid")

    tokens: list[str] = Field(default_factory=list)

    @property
    def configured_tokens(self) -> list[str]:
        """Tokens that resolved to a real value (unresolved ``${...}`` are ignored)."""
        return [t for t in self.tokens if t and "${" not in t]


class MCPLimitsConfig(BaseModel):
    """Response-size and concurrency safeguards for the MCP server."""

    model_config = ConfigDict(extra="forbid")

    max_results: int = Field(default=50, ge=1, le=1000)
    max_artifact_bytes: int = Field(default=1_000_000, ge=1024)
    max_log_bytes: int = Field(default=32_768, ge=256)
    max_screenshot_dimension: int = Field(default=1280, ge=64, le=8192)
    max_concurrent_runs: int = Field(default=1, ge=1, le=64)
    max_run_events: int = Field(default=2000, ge=50)
    max_retained_runs: int = Field(default=100, ge=1)
    # Longest a run_test/run_tests call may block waiting for completion.
    max_wait: str | float = "10m"

    @property
    def max_wait_seconds(self) -> float:
        return parse_duration(self.max_wait)


class MCPConfig(BaseModel):
    """Model Context Protocol server settings (``argus mcp``)."""

    model_config = ConfigDict(extra="forbid")

    transport: str = "stdio"  # "stdio" | "streamable-http"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    path: str = "/mcp"
    # Stateless HTTP lets replicas sit behind a load balancer (no session affinity).
    stateless_http: bool = True
    json_response: bool = False
    allowed_hosts: list[str] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=list)
    auth: MCPAuthConfig = Field(default_factory=MCPAuthConfig)
    limits: MCPLimitsConfig = Field(default_factory=MCPLimitsConfig)

    @field_validator("transport")
    @classmethod
    def _known_transport(cls, value: str) -> str:
        if value not in ("stdio", "streamable-http"):
            raise ValueError("mcp.transport must be 'stdio' or 'streamable-http'")
        return value

    @field_validator("path")
    @classmethod
    def _path_shape(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("mcp.path must start with '/'")
        return value.rstrip("/") or "/"


class AppConfig(BaseModel):
    """Root configuration object."""

    model_config = ConfigDict(extra="forbid")

    backend: BackendConfig = Field(default_factory=BackendConfig)
    devices: dict[str, DeviceConfig] = Field(default_factory=dict)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    ocr: OCRConfig = Field(default_factory=OCRConfig)
    regions: dict[str, Region] = Field(default_factory=dict)
    results: ResultsConfig = Field(default_factory=ResultsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    wait: WaitConfig = Field(default_factory=WaitConfig)
    preflight: PreflightConfig = Field(default_factory=PreflightConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    test_paths: list[str] = Field(default_factory=lambda: ["test_suites"])
    asset_paths: list[str] = Field(default_factory=lambda: ["assets/images"])
    variables: dict[str, Any] = Field(default_factory=dict)
    setup: list[SetupCommand] = Field(default_factory=list)
    before_each: list[SetupCommand] = Field(default_factory=list)

    # Set by the loader; not user-authored.
    config_file: str | None = None
    root_dir: str | None = None

    def resolve_path(self, path: str | Path) -> Path:
        """Resolve a possibly-relative path against the project root."""
        p = Path(path)
        if p.is_absolute():
            return p
        base = Path(self.root_dir) if self.root_dir else Path.cwd()
        return base / p

    def devices_for_platform(self, platform: str) -> dict[str, DeviceConfig]:
        return {
            name: dev
            for name, dev in self.devices.items()
            if dev.effective_platform == platform
        }
