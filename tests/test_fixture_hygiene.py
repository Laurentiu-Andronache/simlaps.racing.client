"""Keep checked-in fixtures synthetic, small, and free of credentials."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent / "fixtures"
MAX_FIXTURE_BYTES = 256 * 1024

# A synthetic Steam ID is acceptable when a protocol test needs to exercise
# the ID-shaped field. Keep the exception explicit and visibly deterministic.
SYNTHETIC_STEAM_ID64 = b"76561198000000000"

SENSITIVE_PATTERNS = {
    "JWT": re.compile(rb"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "Discord webhook": re.compile(rb"discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+", re.I),
    "Steam ID64": re.compile(rb"(?<!\d)7656119\d{10}(?!\d)"),
    "Windows user path": re.compile(rb"[A-Za-z]:\\Users\\[A-Za-z0-9._-]+", re.I),
    "Unix user path": re.compile(rb"/Users/[A-Za-z0-9._-]+", re.I),
    "timestamp": re.compile(rb"\b20\d{2}-\d{2}-\d{2}(?:[T ][0-9]{2}:[0-9]{2}:[0-9]{2})?\b"),
}
PERSONAL_ALIASES = (b"gleb", b"glebulon")


def _fixture_files() -> list[Path]:
    """Return tracked fixtures, with a source-tree fallback for sdists."""
    repo_root = FIXTURES_DIR.parents[1]
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--", "tests/fixtures"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return sorted(repo_root / line for line in result.stdout.splitlines())
    return sorted(path for path in FIXTURES_DIR.rglob("*") if path.is_file())


def _without_allowed_ids(data: bytes) -> bytes:
    return data.replace(SYNTHETIC_STEAM_ID64, b"<synthetic-steam-id>")


def test_tracked_fixtures_are_small_and_redacted() -> None:
    findings: list[str] = []
    for path in _fixture_files():
        data = _without_allowed_ids(path.read_bytes())
        if len(data) > MAX_FIXTURE_BYTES:
            findings.append(f"{path}: {len(data)} bytes exceeds {MAX_FIXTURE_BYTES}")
        lowered = data.lower()
        for alias in PERSONAL_ALIASES:
            if alias in lowered:
                findings.append(f"{path}: personal alias {alias.decode()!r}")
        for label, pattern in SENSITIVE_PATTERNS.items():
            if pattern.search(data):
                findings.append(f"{path}: {label}")

    assert not findings, "Fixture hygiene violations:\n" + "\n".join(findings)


@pytest.mark.parametrize("path", _fixture_files(), ids=lambda path: path.name)
def test_fixture_files_exist_under_fixture_root(path: Path) -> None:
    """Keep the scanner's parametrized inventory limited to fixture files."""
    assert path.is_relative_to(FIXTURES_DIR)
