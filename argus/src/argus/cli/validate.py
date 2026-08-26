"""``argus validate`` presentation.

The checks themselves live in :mod:`argus.service.validation` so that MCP and
a future GUI share them; this module only renders the report.
"""

from __future__ import annotations

from rich.console import Console

from argus.service.validation import (
    CheckState,
    ValidationItem,
    ValidationReport,
    ValidationSection,
    validate_environment,
)

__all__ = [
    "CheckState",
    "ValidationItem",
    "ValidationReport",
    "ValidationSection",
    "print_report",
    "validate_environment",
]

_SYMBOLS = {
    CheckState.OK: ("✓", "green"),
    CheckState.WARN: ("⚠", "yellow"),
    CheckState.FAIL: ("✗", "red"),
    CheckState.NOT_CONFIGURED: ("○", "dim"),
}


def print_report(report: ValidationReport, console: Console) -> None:
    console.print("\n[bold]Universal Test Framework[/bold]")
    console.print("Environment Validation")
    console.print("─" * 36)
    for section in report.sections:
        console.print(f"\n[bold]{section.title}[/bold]")
        for item in section.items:
            symbol, style = _SYMBOLS[item.state]
            detail = f"  [dim]{item.detail}[/dim]" if item.detail else ""
            console.print(f"  [{style}]{symbol}[/{style}] {item.name}{detail}")
    console.print("\n[bold]RESULT[/bold]\n")
    if report.ready:
        console.print("Framework: [bold green]READY[/bold green]\n")
    else:
        console.print("Framework: [bold red]NOT READY[/bold red]")
        console.print("Fix the ✗ items above and run 'argus validate' again.\n")
