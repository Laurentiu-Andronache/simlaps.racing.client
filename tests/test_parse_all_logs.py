"""Regression tests for the portable log inspection CLI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "tools" / "parse_all_logs.py"


def _run_cli(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def test_cli_can_run_from_another_working_directory(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "session.txt").write_text("", encoding="utf-8")
    caller_directory = tmp_path / "caller"
    caller_directory.mkdir()

    result = _run_cli(caller_directory, str(logs))

    assert result.returncode == 0
    assert "FILE: session.txt" in result.stdout
    assert "No sessions found." in result.stdout
    assert result.stderr == ""


def test_cli_reports_missing_input_with_nonzero_exit(tmp_path: Path) -> None:
    result = _run_cli(tmp_path, str(tmp_path / "does-not-exist"))

    assert result.returncode != 0
    assert "input path does not exist" in result.stderr


def test_cli_accepts_explicit_file_and_writes_optional_output(tmp_path: Path) -> None:
    log_file = tmp_path / "session.log"
    log_file.write_text("", encoding="utf-8")
    report = tmp_path / "report.txt"

    result = _run_cli(tmp_path, str(log_file), "--output", str(report))

    assert result.returncode == 0
    assert result.stdout == ""
    assert "FILE: session.log" in report.read_text(encoding="utf-8")


def test_cli_reports_empty_directory_with_nonzero_exit(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()

    result = _run_cli(tmp_path, str(logs))

    assert result.returncode != 0
    assert "no files matching" in result.stderr
