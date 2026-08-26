"""Regression tests for the track catalog validation CLI."""

import json
import subprocess
import sys
from pathlib import Path

from src.core.scripts.validate_catalog import validate_catalog


SCRIPT_PATH = (
    Path(__file__).parents[1] / "src" / "core" / "scripts" / "validate_catalog.py"
)


def _catalog(*corners: dict) -> dict:
    return {
        "test_track": {
            "name": "Test Track",
            "aliases": ["test"],
            "default_config": "full",
            "configs": {"full": {"name": "Full", "corners": list(corners)}},
        }
    }


def test_valid_catalog_with_geometry_warnings_is_valid():
    result = validate_catalog(
        _catalog(
            {"id": 1, "name": "T1", "start": 0.1, "end": 0.15},
            {"id": 2, "name": "T2", "start": 0.3, "end": 0.35},
        )
    )

    assert result.errors == ()
    assert any(issue.startswith("GAP:") for issue in result.warnings)
    assert result.is_valid


def test_cli_returns_success_for_catalog_with_warnings(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            _catalog(
                {"id": 1, "name": "T1", "start": 0.1, "end": 0.15},
                {"id": 2, "name": "T2", "start": 0.3, "end": 0.35},
            )
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(catalog_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "Found 2 warnings:" in completed.stdout
    assert "No errors found." in completed.stdout


def test_cli_returns_failure_for_invalid_corner_bounds(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            _catalog(
                {"id": 1, "name": "T1", "start": -0.1, "end": 0.15},
            )
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(catalog_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "Found 1 errors:" in completed.stdout
    assert "BOUNDS:" in completed.stdout
