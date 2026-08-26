"""Session summary persistence — extracted from telemetry_analyzer.py."""
import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Session history is append-only. Keep recovery from an unexpectedly large
# file bounded while retaining enough recent entries to find the prior run.
_MAX_HISTORY_SCAN_BYTES = 1024 * 1024
_MAX_HISTORY_SCAN_ENTRIES = 1_000
_OPTIONAL_NUMERIC_FIELDS = ("top_speed", "laps", "avg_fuel_per_lap")


def _session_summary_path(output_dir: str) -> str:
    return os.path.join(output_dir, "session_history.jsonl")


def _write_session_summary(
    output_dir: str,
    track: str,
    car: str,
    best_lap_time_s: float,
    top_speed: float,
    lap_count: int,
    avg_fuel_per_lap: Optional[float],
) -> None:
    path = _session_summary_path(output_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "track": track,
        "car": car,
        "best_lap_time_s": best_lap_time_s,
        "best_lap_time_str": (
            f"{int(best_lap_time_s // 60)}:{best_lap_time_s % 60:05.2f}"
        ),
        "top_speed": top_speed,
        "laps": lap_count,
        "avg_fuel_per_lap": avg_fuel_per_lap,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _load_previous_summary(
    output_dir: str, track: str, car: str
) -> Optional[Dict[str, Any]]:
    path = _session_summary_path(output_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            scan_size = min(file_size, _MAX_HISTORY_SCAN_BYTES)
            f.seek(file_size - scan_size)
            data = f.read(scan_size)
    except OSError:
        return None

    # A bounded tail can begin in the middle of an entry. Discard that
    # partial line; all following lines are complete and are newest-first.
    if file_size > scan_size:
        first_newline = data.find(b"\n")
        if first_newline < 0:
            return None
        data = data[first_newline + 1 :]

    entries_seen = 0
    for line in reversed(data.splitlines()):
        if not line.strip():
            continue
        entries_seen += 1
        if entries_seen > _MAX_HISTORY_SCAN_ENTRIES:
            break
        try:
            entry = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            continue
        if _is_usable_summary(entry, track, car):
            return entry
    return None


def _is_finite_number(value: Any, *, positive: bool = False) -> bool:
    """Return whether a JSON number is finite, optionally strictly positive."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return False
    return math.isfinite(number) and (not positive or number > 0)


def _is_usable_summary(entry: Any, track: str, car: str) -> bool:
    """Validate the fields consumed by analyzer session-over-session notes."""
    if not isinstance(entry, dict):
        return False
    entry_track = entry.get("track")
    entry_car = entry.get("car")
    if (
        not isinstance(entry_track, str)
        or not entry_track.strip()
        or not isinstance(entry_car, str)
        or not entry_car.strip()
        or entry_track != track
        or entry_car != car
    ):
        return False
    if not _is_finite_number(entry.get("best_lap_time_s"), positive=True):
        return False
    display = entry.get("best_lap_time_str")
    if not isinstance(display, str) or not display.strip():
        return False
    for field in _OPTIONAL_NUMERIC_FIELDS:
        value = entry.get(field)
        if value is not None and not _is_finite_number(value):
            return False
    return True
