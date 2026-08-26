"""Regression tests for persisted session-summary loading."""

import builtins
import json

from src.core.analyzer import session_summary


def _write_entries(path, entries):
    path.write_text(
        "".join(json.dumps(entry, allow_nan=True) + "\n" for entry in entries),
        encoding="utf-8",
    )


def _valid_entry(best_time=70.0):
    return {
        "track": "Test Track",
        "car": "Test Car",
        "best_lap_time_s": best_time,
        "best_lap_time_str": "1:10.00",
        "top_speed": 150.0,
        "laps": 2,
        "avg_fuel_per_lap": None,
    }


def test_load_previous_summary_skips_malformed_matching_entries(tmp_path):
    older = _valid_entry(72.0)
    malformed_entries = [
        {"track": "Test Track", "car": "Test Car"},
        {"track": "Test Track", "car": "Test Car", "best_lap_time_s": "70"},
        {
            "track": "Test Track",
            "car": "Test Car",
            "best_lap_time_s": float("nan"),
            "best_lap_time_str": "1:10.00",
        },
        {
            "track": "Test Track",
            "car": "Test Car",
            "best_lap_time_s": 70.0,
            "best_lap_time_str": "",
            "top_speed": float("inf"),
        },
    ]
    path = tmp_path / "session_history.jsonl"
    path.write_text(
        json.dumps(older)
        + "\nnot valid json\n"
        + "".join(
            json.dumps(entry, allow_nan=True) + "\n" for entry in malformed_entries
        ),
        encoding="utf-8",
    )

    assert (
        session_summary._load_previous_summary(str(tmp_path), "Test Track", "Test Car")
        == older
    )


def test_load_previous_summary_skips_wrong_types_and_nonfinite_optional_fields(
    tmp_path,
):
    older = _valid_entry(72.0)
    newest = _valid_entry(70.0)
    newest["track"] = ["Test Track"]
    path = tmp_path / "session_history.jsonl"
    _write_entries(path, [older, newest])

    assert (
        session_summary._load_previous_summary(str(tmp_path), "Test Track", "Test Car")
        == older
    )

    for field in ("top_speed", "laps", "avg_fuel_per_lap"):
        newest = _valid_entry(70.0)
        newest[field] = float("inf")
        _write_entries(path, [older, newest])
        assert (
            session_summary._load_previous_summary(
                str(tmp_path), "Test Track", "Test Car"
            )
            == older
        )


def test_load_previous_summary_scans_bounded_tail_without_readlines(
    tmp_path, monkeypatch
):
    path = tmp_path / "session_history.jsonl"
    entries = [_valid_entry(72.0)]
    entries.extend(
        {
            "track": "Other Track",
            "car": "Other Car",
            "best_lap_time_s": 80.0,
            "best_lap_time_str": "1:20.00",
        }
        for _ in range(20_000)
    )
    entries.append(_valid_entry(70.0))
    _write_entries(path, entries)
    assert path.stat().st_size > session_summary._MAX_HISTORY_SCAN_BYTES

    real_open = builtins.open

    class GuardedFile:
        def __init__(self, file):
            self._file = file

        def __enter__(self):
            self._file.__enter__()
            return self

        def __exit__(self, *args):
            return self._file.__exit__(*args)

        def read(self, size=-1):
            assert 0 <= size <= session_summary._MAX_HISTORY_SCAN_BYTES
            return self._file.read(size)

        def readlines(self, *args, **kwargs):
            raise AssertionError("history loader must not call readlines")

        def __getattr__(self, name):
            return getattr(self._file, name)

    def guarded_open(*args, **kwargs):
        return GuardedFile(real_open(*args, **kwargs))

    monkeypatch.setattr(session_summary, "open", guarded_open, raising=False)
    assert (
        session_summary._load_previous_summary(str(tmp_path), "Test Track", "Test Car")
        == entries[-1]
    )
