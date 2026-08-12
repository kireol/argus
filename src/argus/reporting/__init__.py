"""Reporters: console, JSON, JUnit XML, HTML."""

from argus.reporting.console import ConsoleReporter
from argus.reporting.html import write_html_report
from argus.reporting.json_report import run_result_to_dict, write_json_report
from argus.reporting.junit import write_junit_report

__all__ = [
    "ConsoleReporter",
    "run_result_to_dict",
    "write_html_report",
    "write_json_report",
    "write_junit_report",
]
