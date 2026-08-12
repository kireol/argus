"""Alerting abstraction (spec §33).

V1 ships a terminal provider; GUI/email/Slack/webhook providers plug in later
through the same interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from rich.console import Console


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Alert:
    title: str
    message: str
    severity: AlertSeverity = AlertSeverity.ERROR
    details: dict[str, Any] = field(default_factory=dict)


class AlertProvider(ABC):
    @abstractmethod
    def alert(self, alert: Alert) -> None:
        ...


class ConsoleAlertProvider(AlertProvider):
    """Prints prominent alerts to the terminal."""

    _STYLES = {
        AlertSeverity.INFO: "bold blue",
        AlertSeverity.WARNING: "bold yellow",
        AlertSeverity.ERROR: "bold red",
    }

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console(stderr=True, highlight=False)

    def alert(self, alert: Alert) -> None:
        style = self._STYLES[alert.severity]
        self._console.print(f"\n[{style}]{alert.title}[/{style}]")
        self._console.print(alert.message)
        for key, value in alert.details.items():
            self._console.print(f"  {key}: {value}")
