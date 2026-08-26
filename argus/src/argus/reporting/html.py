"""Self-contained HTML run report with embedded artifact images."""

from __future__ import annotations

import html
from collections import defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path

from argus import __version__
from argus.models.results import RunResult, TestResult, TestStatus
from argus.utilities.duration import format_duration

_STATUS_CLASS = {
    TestStatus.PASSED: "pass",
    TestStatus.FAILED: "fail",
    TestStatus.ERROR: "fail",
    TestStatus.SKIPPED: "skip",
}

_STATUS_LABEL = {
    TestStatus.PASSED: "passed",
    TestStatus.FAILED: "failed",
    TestStatus.ERROR: "error",
    TestStatus.SKIPPED: "skipped",
}

_PRIMARY_IMAGES = ("actual.png", "expected.png", "diff.png")

_CSS = """
:root {
  --bg: #f4f5f7;
  --card: #fff;
  --border: #e2e5ea;
  --text: #1b1f24;
  --muted: #6b7280;
  --pass: #0a7a3d;
  --fail: #c0212f;
  --skip: #6b7280;
  --accent: #2563eb;
}
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  margin: 0; padding: 1.5rem 2rem 3rem;
  color: var(--text); background: var(--bg); line-height: 1.45;
}
h1 { font-size: 1.45rem; margin: 0 0 .25rem; }
h2 { font-size: 1.05rem; margin: 1.75rem 0 .75rem; }
.muted { color: var(--muted); font-size: .85rem; }
.summary {
  display: flex; flex-wrap: wrap; gap: .75rem; margin: 1rem 0 1.5rem;
}
.summary .card {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: .75rem 1.1rem; min-width: 6.5rem;
}
.summary .n { font-size: 1.45rem; font-weight: 700; display: block; }
.filters { display: flex; flex-wrap: wrap; gap: .5rem; margin-bottom: 1rem; }
.filters button {
  border: 1px solid var(--border); background: var(--card); border-radius: 999px;
  padding: .35rem .85rem; cursor: pointer; font-size: .85rem; color: var(--text);
}
.filters button.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.feature { margin-bottom: 1.5rem; }
.feature h2 {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px 10px 0 0;
  margin: 0; padding: .65rem 1rem; border-bottom: none;
}
table {
  border-collapse: collapse; width: 100%; background: var(--card);
  border: 1px solid var(--border); border-radius: 0 0 10px 10px; overflow: hidden;
}
th, td { text-align: left; padding: .55rem .8rem; border-bottom: 1px solid var(--border);
         font-size: .9rem; vertical-align: top; }
th { background: #f8f9fb; color: var(--muted); font-weight: 600; font-size: .8rem;
     text-transform: uppercase; letter-spacing: .03em; }
tr:last-child td { border-bottom: none; }
tr.pass .status { color: var(--pass); font-weight: 600; }
tr.fail .status { color: var(--fail); font-weight: 600; }
tr.skip .status { color: var(--skip); font-weight: 600; }
tr.hidden { display: none; }
.detail {
  background: #fafbfc; font-size: .85rem; white-space: pre-wrap;
  border-top: 1px dashed var(--border);
}
.detail td { padding-top: .75rem; padding-bottom: .9rem; }
.error { color: var(--fail); margin: 0 0 .5rem; white-space: pre-wrap; }
.steps { margin: .4rem 0 .75rem; padding-left: 1.1rem; color: var(--muted); }
.steps li.fail { color: var(--fail); }
.images {
  display: flex; flex-wrap: wrap; gap: .75rem; margin-top: .5rem;
}
.images figure {
  margin: 0; background: #111; border-radius: 8px; overflow: hidden;
  border: 1px solid var(--border); max-width: 320px;
}
.images img {
  display: block; max-width: 320px; max-height: 240px; width: auto; height: auto;
  object-fit: contain; background: #111;
}
.images figcaption {
  background: #fff; color: var(--muted); font-size: .75rem; padding: .35rem .55rem;
}
.ocr { margin-top: .6rem; }
.ocr summary { cursor: pointer; color: var(--muted); font-size: .8rem; }
.ocr pre {
  background: #fff; border: 1px solid var(--border); border-radius: 8px;
  padding: .6rem .8rem; font-size: .78rem; max-height: 18rem; overflow: auto;
  white-space: pre-wrap; margin: .4rem 0 0;
}
a { color: var(--accent); }
.footer { margin-top: 2rem; color: var(--muted); font-size: .8rem; }
.meta { display: grid; grid-template-columns: max-content 1fr; gap: .2rem 1rem;
        margin: .75rem 0 1rem; font-size: .9rem; }
.meta dt { color: var(--muted); }
.meta dd { margin: 0; }
.badge { display: inline-block; border-radius: 999px; padding: .05rem .5rem; font-size: .72rem;
         margin-left: .4rem; background: #fef3c7; color: #92400e; }
.badge.known { background: #e0e7ff; color: #3730a3; }
.badge.notrun { background: #f3f4f6; color: #374151; }
.notice { background: #fff7ed; border: 1px solid #fed7aa; border-radius: 8px;
          padding: .6rem .9rem; margin: .5rem 0; }
.notice.bad { background: #fef2f2; border-color: #fecaca; }
"""

_JS = """
(function () {
  const buttons = document.querySelectorAll('.filters button');
  const rows = document.querySelectorAll('tr.test-row');
  function apply(filter) {
    buttons.forEach(b => b.classList.toggle('active', b.dataset.filter === filter));
    rows.forEach(row => {
      const status = row.dataset.status;
      const show = filter === 'all'
        || (filter === 'passed' && status === 'passed')
        || (filter === 'failed' && (status === 'failed' || status === 'error'))
        || (filter === 'skipped' && status === 'skipped');
      row.classList.toggle('hidden', !show);
      const detail = row.nextElementSibling;
      if (detail && detail.classList.contains('detail')) {
        detail.classList.toggle('hidden', !show);
      }
    });
  }
  buttons.forEach(btn => btn.addEventListener('click', () => apply(btn.dataset.filter)));
})();
"""


def _discover_images(artifact_dir: Path) -> list[Path]:
    """Return image files in a test artifact directory (primary first)."""
    if not artifact_dir.is_dir():
        return []
    found: list[Path] = []
    seen: set[str] = set()
    for name in _PRIMARY_IMAGES:
        path = artifact_dir / name
        if path.is_file():
            found.append(path)
            seen.add(name.lower())
    extras = sorted(
        p
        for p in artifact_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        and p.name.lower() not in seen
    )
    found.extend(extras)
    return found


def _rel_src(image: Path, report_dir: Path) -> str:
    try:
        return html.escape(image.relative_to(report_dir).as_posix())
    except ValueError:
        return html.escape(image.as_posix())


def _image_gallery(test: TestResult, report_dir: Path) -> str:
    if not test.artifact_dir:
        return ""
    images = _discover_images(Path(test.artifact_dir))
    if not images:
        return ""
    figures: list[str] = []
    for image in images:
        src = _rel_src(image, report_dir)
        caption = html.escape(image.name)
        figures.append(
            f'<figure><a href="{src}" target="_blank" rel="noopener">'
            f'<img src="{src}" alt="{caption}" loading="lazy"></a>'
            f"<figcaption>{caption}</figcaption></figure>"
        )
    return f'<div class="images">{"".join(figures)}</div>'


_OCR_TEXT_LIMIT = 4000


def _ocr_text(test: TestResult) -> str:
    """Contents of ``ocr.txt`` (text-verification evidence), truncated for the page."""
    if not test.artifact_dir:
        return ""
    path = Path(test.artifact_dir) / "ocr.txt"
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if len(text) > _OCR_TEXT_LIMIT:
        text = text[:_OCR_TEXT_LIMIT] + "\n… (truncated; see ocr.txt)"
    return text


def _steps_html(test: TestResult) -> str:
    if not test.steps:
        return ""
    items: list[str] = []
    for step in test.steps:
        label = html.escape(step.name or step.action)
        cls = "fail" if not step.passed else "pass"
        mark = "✗" if not step.passed else "✓"
        msg = ""
        if step.error:
            msg = f" — {html.escape(step.error)}"
        elif step.message and not step.passed:
            msg = f" — {html.escape(step.message)}"
        items.append(f'<li class="{cls}">{mark} {label}{msg}</li>')
    return f'<ol class="steps">{"".join(items)}</ol>'


def _detail_row(test: TestResult, report_dir: Path) -> str | None:
    needs_detail = (
        test.status in (TestStatus.FAILED, TestStatus.ERROR)
        or bool(test.artifact_dir and _discover_images(Path(test.artifact_dir)))
        or bool(test.steps)
    )
    if not needs_detail:
        return None

    parts: list[str] = []
    if test.error:
        parts.append(f'<p class="error">{html.escape(test.error)}</p>')
    if test.failure_category:
        parts.append(
            f'<p class="muted">category: {html.escape(test.failure_category)}</p>'
        )
    steps = _steps_html(test)
    if steps:
        parts.append(steps)
    if test.instrumentation_state:
        pairs = ", ".join(
            f"{k}={v}"
            for k, v in sorted(test.instrumentation_state.items())
            if k != "capabilities"
        )
        if pairs:
            parts.append(f'<p class="muted">instrumentation: {html.escape(pairs)}</p>')
    gallery = _image_gallery(test, report_dir)
    if gallery:
        parts.append(gallery)
    elif test.artifact_dir:
        parts.append(
            f'<p class="muted">artifacts: {html.escape(test.artifact_dir)}</p>'
        )
    ocr_text = _ocr_text(test)
    if ocr_text:
        parts.append(
            '<details class="ocr"><summary>OCR evidence (ocr.txt)</summary>'
            f"<pre>{html.escape(ocr_text)}</pre></details>"
        )
    if not parts:
        return None
    return (
        f'<tr class="detail">'
        f'<td></td><td colspan="5">{"".join(parts)}</td></tr>'
    )


Badges = Callable[[TestResult], Sequence[str]]


def _badge_html(badges: Sequence[str]) -> str:
    parts = []
    for badge in badges:
        lowered = badge.lower()
        cls = "known" if "known" in lowered else "notrun" if "not run" in lowered else ""
        parts.append(f'<span class="badge {cls}">{html.escape(badge)}</span>')
    return "".join(parts)


def _test_rows(tests: list[TestResult], report_dir: Path, badges: Badges | None = None) -> str:
    rows: list[str] = []
    for test in tests:
        status_class = _STATUS_CLASS[test.status]
        label = _STATUS_LABEL[test.status]
        symbol = {"pass": "✓", "fail": "✗", "skip": "–"}[status_class]
        platform = html.escape(test.platform or "—")
        extra = _badge_html(badges(test)) if badges is not None else ""
        rows.append(
            f'<tr class="test-row {status_class}" data-status="{label}">'
            f'<td class="status">{symbol} {label}{extra}</td>'
            f"<td>{html.escape(test.test_id)}</td>"
            f"<td>{html.escape(test.name)}</td>"
            f"<td>{platform}</td>"
            f"<td>{format_duration(test.duration)}</td>"
            f"<td>{test.attempts}</td></tr>"
        )
        detail = _detail_row(test, report_dir)
        if detail:
            rows.append(detail)
    return "".join(rows)


def _grouped_sections(
    result: RunResult, report_dir: Path, badges: Badges | None = None
) -> str:
    by_feature: dict[str, list[TestResult]] = defaultdict(list)
    for test in result.tests:
        by_feature[test.feature].append(test)

    sections: list[str] = []
    for feature in sorted(by_feature):
        tests = by_feature[feature]
        failed = sum(
            1 for t in tests if t.status in (TestStatus.FAILED, TestStatus.ERROR)
        )
        passed = sum(1 for t in tests if t.status == TestStatus.PASSED)
        meta = f"{passed} passed"
        if failed:
            meta += f", {failed} failed"
        sections.append(
            f'<section class="feature">'
            f"<h2>{html.escape(feature)} "
            f'<span class="muted">({len(tests)} tests · {meta})</span></h2>'
            f"<table><thead><tr>"
            f"<th>Status</th><th>ID</th><th>Name</th>"
            f"<th>Platform</th><th>Duration</th><th>Attempts</th>"
            f"</tr></thead><tbody>"
            f"{_test_rows(tests, report_dir, badges)}"
            f"</tbody></table></section>"
        )
    return "".join(sections)


def _meta_html(fields: Sequence[tuple[str, str]]) -> str:
    if not fields:
        return ""
    items = "".join(
        f"<dt>{html.escape(k)}</dt><dd>{html.escape(v)}</dd>" for k, v in fields if v
    )
    return f'<dl class="meta">{items}</dl>'


def _notices_html(notices: Sequence[tuple[str, str]]) -> str:
    return "".join(
        f'<div class="notice {"bad" if level == "error" else ""}">{html.escape(text)}</div>'
        for level, text in notices
    )


def write_html_report(
    result: RunResult,
    path: Path,
    *,
    title: str = "Argus test report",
    status_label: str | None = None,
    header_fields: Sequence[tuple[str, str]] = (),
    notices: Sequence[tuple[str, str]] = (),
    badges: Badges | None = None,
) -> Path:
    """Write an HTML results page next to per-test artifact images.

    The keyword arguments let a caller (the CI layer) add context rows
    (provider, branch, commit...), notices (policy violations) and per-test
    badges (flaky / known failure) without a second renderer.
    """
    report_dir = path.parent
    status_label = status_label or result.status.value.replace("_", " ").upper()
    started = f"{result.started_at:%Y-%m-%d %H:%M:%S UTC}"
    document = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — {html.escape(status_label)}</title>
<style>{_CSS}</style></head><body>
<h1>{html.escape(title)}</h1>
<p class="muted">Framework {html.escape(__version__)} · started {started}
{" · " + html.escape(result.results_dir) if result.results_dir else ""}</p>
{_meta_html(header_fields)}
<div class="summary">
  <div class="card"><span class="n">{result.executed}</span>executed</div>
  <div class="card"><span class="n">{result.passed_count}</span>passed</div>
  <div class="card"><span class="n">{result.failed_count}</span>failed</div>
  <div class="card"><span class="n">{result.skipped_count}</span>skipped</div>
  <div class="card"><span class="n">{format_duration(result.duration)}</span>duration</div>
</div>
<p><strong>Result:</strong> {html.escape(status_label)}</p>
{f'<p class="error">{html.escape(result.stop_reason)}</p>' if result.stop_reason else ""}
{_notices_html(notices)}
<div class="filters">
  <button type="button" class="active" data-filter="all">All</button>
  <button type="button" data-filter="passed">Passed</button>
  <button type="button" data-filter="failed">Failed</button>
  <button type="button" data-filter="skipped">Skipped</button>
</div>
{_grouped_sections(result, report_dir, badges)}
<p class="footer">Open image thumbnails for full size. Artifact folders sit beside this report.</p>
<script>{_JS}</script>
</body></html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    return path
