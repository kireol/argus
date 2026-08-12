"""Simple self-contained HTML report."""

from __future__ import annotations

import html
from pathlib import Path

from utf import __version__
from utf.models.results import RunResult, TestStatus
from utf.utilities.duration import format_duration

_STATUS_CLASS = {
    TestStatus.PASSED: "pass",
    TestStatus.FAILED: "fail",
    TestStatus.ERROR: "fail",
    TestStatus.SKIPPED: "skip",
}

_CSS = """
body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 2rem;
       color: #1a1a2e; background: #fafafa; }
h1 { font-size: 1.4rem; } h2 { font-size: 1.1rem; margin-top: 2rem; }
.summary { display: flex; gap: 1.5rem; margin: 1rem 0; }
.summary div { background: #fff; border: 1px solid #ddd; border-radius: 8px;
               padding: .8rem 1.2rem; }
.summary .n { font-size: 1.5rem; font-weight: 700; display: block; }
table { border-collapse: collapse; width: 100%; background: #fff; }
th, td { text-align: left; padding: .5rem .8rem; border-bottom: 1px solid #eee;
         font-size: .9rem; }
tr.pass td:first-child { color: #0a7a3d; }
tr.fail td:first-child { color: #c0212f; }
tr.skip td:first-child { color: #888; }
.detail { background: #fff6f6; font-size: .85rem; white-space: pre-wrap; }
.muted { color: #777; font-size: .8rem; }
"""


def write_html_report(result: RunResult, path: Path) -> Path:
    rows: list[str] = []
    for test in result.tests:
        status_class = _STATUS_CLASS[test.status]
        symbol = {"pass": "✓", "fail": "✗", "skip": "–"}[status_class]
        platform = html.escape(test.platform or "")
        rows.append(
            f'<tr class="{status_class}"><td>{symbol}</td>'
            f"<td>{html.escape(test.test_id)}</td>"
            f"<td>{html.escape(test.name)}</td>"
            f"<td>{html.escape(test.feature)}</td>"
            f"<td>{platform}</td>"
            f"<td>{format_duration(test.duration)}</td></tr>"
        )
        if test.status in (TestStatus.FAILED, TestStatus.ERROR):
            detail_parts = [html.escape(test.error or "")]
            if test.instrumentation_state:
                pairs = ", ".join(
                    f"{k}={v}"
                    for k, v in sorted(test.instrumentation_state.items())
                    if k != "capabilities"
                )
                detail_parts.append(f"instrumentation: {html.escape(pairs)}")
            if test.artifact_dir:
                detail_parts.append(f"artifacts: {html.escape(test.artifact_dir)}")
            rows.append(
                f'<tr class="detail"><td></td><td colspan="5">'
                + "<br>".join(detail_parts)
                + "</td></tr>"
            )

    status_label = result.status.value.replace("_", " ").upper()
    document = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Test Report — {status_label}</title>
<style>{_CSS}</style></head><body>
<h1>Universal Test Framework — Run Report</h1>
<p class="muted">Framework version {__version__} · started {result.started_at:%Y-%m-%d %H:%M:%S}</p>
<div class="summary">
  <div><span class="n">{result.executed}</span>executed</div>
  <div><span class="n">{result.passed_count}</span>passed</div>
  <div><span class="n">{result.failed_count}</span>failed</div>
  <div><span class="n">{result.skipped_count}</span>skipped</div>
  <div><span class="n">{format_duration(result.duration)}</span>duration</div>
</div>
<h2>Result: {status_label}</h2>
<table>
<tr><th></th><th>ID</th><th>Name</th><th>Feature</th><th>Platform</th><th>Duration</th></tr>
{''.join(rows)}
</table>
</body></html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    return path
