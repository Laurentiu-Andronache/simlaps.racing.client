"""Public app-flow coverage for session-relative lap/history identity."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models import LapData, SessionData
from src.ui.app import SimLapsApp
from src.ui.services.lap_processing_service import LapProcessingService
from src.utils.config import AppConfig


def _lap(timestamp: str, lap_time_ms: int) -> LapData:
    return LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=lap_time_ms,
        lap_time_str="1:05.000",
        timestamp=timestamp,
    )


def _app() -> SimLapsApp:
    app = SimLapsApp.__new__(SimLapsApp)
    app._config = AppConfig(auto_submit=False)
    app._session_manager = MagicMock()
    app._pb_cache = MagicMock()
    app._telemetry_capture = None
    app._history_entries = []
    app._history_entry_by_lap_id = {}
    app._history_identity_enabled = False
    app._lap_processing_service = LapProcessingService()
    app._home_page = MagicMock()
    app._home_page._lap_count = 0

    def add_lap(*_args):
        app._home_page._lap_count += 1
        return MagicMock()

    app._home_page.add_lap.side_effect = add_lap
    return app


@pytest.mark.asyncio
async def test_delayed_log_update_targets_originating_entry_across_sessions():
    """Two ACE lap 1 values must retain distinct application history rows."""
    app = _app()
    session_a = SessionData(track="Track A", car="Car A")
    session_b = SessionData(track="Track B", car="Car B")
    lap_a = _lap("2026-08-26T10:00:00+00:00", 65_000)
    lap_b = _lap("2026-08-26T11:00:00+00:00", 75_000)

    await app._on_lap_complete(session_a, lap_a)
    await app._on_lap_complete(session_b, lap_b)

    first_entry, second_entry = app._history_entries
    lap_b.timestamp = "2026-08-26T11:00:01+00:00"
    lap_b.lap_time_ms = 74_000
    lap_b.is_valid = False
    await app._on_lap_update(session_b, lap_b)

    assert first_entry.lap_time_ms == 65_000
    assert first_entry.timestamp == "2026-08-26T10:00:00+00:00"
    assert first_entry.was_valid is True
    assert second_entry.lap_time_ms == 74_000
    assert second_entry.timestamp == "2026-08-26T11:00:01+00:00"
    assert second_entry.was_valid is False


@pytest.mark.asyncio
async def test_trimmed_history_entry_binding_is_discarded():
    app = _app()
    session_a = SessionData(track="Track A", car="Car A")
    lap_a = _lap("2026-08-26T10:00:00+00:00", 65_000)

    await app._on_lap_complete(session_a, lap_a)
    entry = app._history_entries.pop()
    await app._on_lap_update(session_a, lap_a)

    assert entry.lap_time_ms == 65_000
    assert app._history_entry_by_lap_id == {}


@pytest.mark.asyncio
async def test_session_reset_clears_bindings_but_keeps_visible_history():
    app = _app()
    app._session_lifecycle_service = MagicMock()
    app._session_lifecycle_service.handle_session_restart = AsyncMock()

    session = SessionData(track="Track A", car="Car A")
    lap = _lap("2026-08-26T10:00:00+00:00", 65_000)
    await app._on_lap_complete(session, lap)
    assert app._history_entry_by_lap_id

    await app._on_session_restart()

    assert app._history_entries
    assert app._history_entry_by_lap_id == {}
