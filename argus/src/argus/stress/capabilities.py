"""DeviceProbe — what an action may ask of the device.

Requirements are ``DeviceCapabilities`` flag names (``tap``, ``keyboard``,
``app_lifecycle``...) or optional adapter methods (``rotate``, ``background``)
that not every adapter implements. Probing is explicit and cached so a
scenario never crashes on an unsupported action — it is skipped and reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from argus.adapters.base import Device

#: Requirement names that map to optional adapter methods rather than flags.
OPTIONAL_METHODS: dict[str, str] = {
    "rotate": "rotate",
    "background": "background_application",
    "foreground": "foreground_application",
    "reload": "reload",
    "clear_text": "clear_text",
    "type_text": "type_text",
}


@dataclass
class DeviceProbe:
    device: Device | None
    _cache: dict[str, bool] = field(default_factory=dict)

    def has(self, requirement: str) -> bool:
        cached = self._cache.get(requirement)
        if cached is not None:
            return cached
        result = self._probe(requirement)
        self._cache[requirement] = result
        return result

    def _probe(self, requirement: str) -> bool:
        if self.device is None:
            return False
        caps = self.device.capabilities
        flag = f"supports_{requirement}"
        if hasattr(caps, flag):
            return bool(getattr(caps, flag))
        method = OPTIONAL_METHODS.get(requirement, requirement)
        return callable(getattr(self.device, method, None))

    def summary(self) -> dict[str, bool]:
        if self.device is None:
            return {}
        caps = self.device.capabilities
        out = {
            name.removeprefix("supports_"): bool(value)
            for name, value in vars(caps).items() if name.startswith("supports_")
        }
        for requirement in OPTIONAL_METHODS:
            out[requirement] = self.has(requirement)
        return out


__all__ = ["OPTIONAL_METHODS", "DeviceProbe"]
