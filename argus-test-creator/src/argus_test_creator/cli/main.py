"""argus-test-creator CLI."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from argus_test_creator import __version__
from argus_test_creator.core.errors import CreatorError

app = typer.Typer(
    name="argus-test-creator",
    help="Argus Test Creator — visual authoring of Argus YAML tests.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)
console = Console(highlight=False)


def _fail(exc: CreatorError, code: int = 1) -> None:
    console.print(f"[bold red]ERROR[/bold red] {exc.message}")
    if exc.remediation:
        console.print(f"[dim]Suggested action:[/dim] {exc.remediation}")
    raise typer.Exit(code)


@app.command()
def version() -> None:
    """Show the Creator version (and the detected Argus version)."""
    from argus_test_creator.integrations.argus import discover_argus

    console.print(f"argus-test-creator {__version__}")
    info = discover_argus()
    if info:
        console.print(f"argus {info.version} ({info.executable}, via {info.source})")
    else:
        console.print("argus: not found")


@app.command()
def new(
    directory: Annotated[Path, typer.Argument(help="Project directory to create.")],
    name: Annotated[str | None, typer.Option("--name", help="Project name.")] = None,
) -> None:
    """Create an empty Creator project (an Argus project with a .argus-creator folder)."""
    from argus_test_creator.project import CreatorProject

    try:
        project = CreatorProject.create(directory, name=name)
    except CreatorError as exc:
        _fail(exc)
        return
    console.print(f"Created project at {project.root}")
    console.print("Next: argus-test-creator gui " + str(project.root))


@app.command()
def validate(
    project_dir: Annotated[Path, typer.Argument(help="Creator project directory.")],
    test: Annotated[str | None, typer.Option("--test", "-t", help="Test ID (default: all).")] = None,  # noqa: E501
    argus: Annotated[bool, typer.Option("--argus", help="Also run Argus validation.")] = False,
) -> None:
    """Validate the project's tests (Creator rules, optionally Argus)."""
    from argus_test_creator.project import CreatorProject
    from argus_test_creator.quality import TestQualityAnalyzer
    from argus_test_creator.validation import DocumentValidator

    try:
        project = CreatorProject.open(project_dir)
    except CreatorError as exc:
        _fail(exc)
        return
    ids = [test] if test else project.list_test_ids()
    if not ids:
        console.print("No tests in project.")
        raise typer.Exit(0)
    failed = False
    validator = DocumentValidator(asset_root=project.assets_dir)
    for test_id in ids:
        try:
            document = project.load_document(test_id)
        except CreatorError as exc:
            console.print(f"[red]✗[/red] {test_id}: {exc.message}")
            failed = True
            continue
        issues = validator.validate(document)
        errors = [i for i in issues if i.is_error]
        symbol = "[red]✗[/red]" if errors else "[green]✓[/green]"
        console.print(f"{symbol} {test_id}  {document.metadata.name}")
        for issue in issues:
            style = "red" if issue.is_error else "yellow"
            console.print(f"    [{style}]{issue.severity}[/{style}] {issue.message}")
            if issue.fix:
                console.print(f"      [dim]fix:[/dim] {issue.fix}")
        for finding in TestQualityAnalyzer().analyze(document).warnings:
            console.print(f"    [yellow]quality[/yellow] {finding.message}")
        failed = failed or bool(errors)
    if argus:
        from argus_test_creator.integrations.argus import ArgusIntegration

        try:
            result = ArgusIntegration(project_root=project.root).validate(
                project.config_path, test_id=test
            )
        except CreatorError as exc:
            _fail(exc, 3)
            return
        console.print("\nArgus: " + ("[green]READY[/green]" if result.ready else "[red]NOT READY[/red]"))  # noqa: E501
        for issue in result.issues:
            console.print(f"    [red]{issue.message}[/red]")
        failed = failed or not result.ready
    raise typer.Exit(1 if failed else 0)


@app.command()
def export(
    project_dir: Annotated[Path, typer.Argument(help="Creator project directory.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output directory.")] = Path("export"),
    test: Annotated[str | None, typer.Option("--test", "-t", help="Test ID (default: all).")] = None,  # noqa: E501
) -> None:
    """Regenerate Argus YAML (and copy referenced assets) into a directory."""
    from argus_test_creator.project import CreatorProject
    from argus_test_creator.serialization import document_to_yaml

    try:
        project = CreatorProject.open(project_dir)
    except CreatorError as exc:
        _fail(exc)
        return
    import shutil

    ids = [test] if test else project.list_test_ids()
    out.mkdir(parents=True, exist_ok=True)
    for test_id in ids:
        document = project.load_document(test_id)
        target = out / f"{test_id}.yaml"
        target.write_text(document_to_yaml(document), encoding="utf-8")
        for image in document.referenced_images():
            source = project.assets_dir / image
            if source.is_file():
                (out / "assets" / "images").mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, out / "assets" / "images" / image)
        console.print(f"wrote {target}")


@app.command()
def doctor(
    project_dir: Annotated[Path | None, typer.Argument(help="Project directory (optional).")] = None,  # noqa: E501
) -> None:
    """Check the environment: Python, Argus, Playwright, OCR, ADB, permissions, project."""
    from argus_test_creator.cli.doctor import run_doctor

    report = run_doctor(project_dir)
    for section, items in report.items():
        console.print(f"\n[bold]{section}[/bold]")
        for state, name, detail in items:
            symbol = {"ok": "[green]✓[/green]", "warn": "[yellow]⚠[/yellow]",
                      "fail": "[red]✗[/red]"}[state]
            console.print(f"  {symbol} {escape(name)}  [dim]{escape(detail)}[/dim]")
    failed = any(state == "fail" for items in report.values() for state, _, _ in items)
    console.print("\nResult: " + ("[red]NOT READY[/red]" if failed else "[green]READY[/green]"))
    raise typer.Exit(3 if failed else 0)


@app.command()
def demo(
    directory: Annotated[Path, typer.Argument(help="Project directory to create.")],
    run: Annotated[bool, typer.Option("--run", help="Run the generated test with Argus.")] = False,
) -> None:
    """Record the built-in Movies demo end-to-end without the GUI (smoke/demo)."""
    from argus_test_creator.app.demo_flow import run_demo_flow

    try:
        summary = run_demo_flow(directory, run_with_argus=run, echo=console.print)
    except CreatorError as exc:
        _fail(exc)
        return
    console.print(summary)


@app.command()
def gui(
    project_dir: Annotated[Path | None, typer.Argument(help="Project to open.")] = None,
    test: Annotated[str | None, typer.Option("--test", "-t", help="Test ID to open.")] = None,
) -> None:
    """Launch the desktop application."""
    try:
        from argus_test_creator.ui.main import run_gui
    except ImportError as exc:
        console.print(f"[red]The GUI needs PySide6:[/red] {exc}\n"
                      "Install with: pip install 'argus-test-creator[ui]'")
        raise typer.Exit(1) from exc
    sys.exit(run_gui(project_dir, test_id=test))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
