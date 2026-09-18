"""Regression coverage for capture-result identity and telemetry trust."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.core.telemetry_analyzer import TelemetryAnalyzer
from src.core.telemetry_capture import FrameData, LapBoundary, TelemetryCapture
from src.models import LapData, LapState, SharedSessionManager


def _frame(index: int, timer: int, *, last: int = 0) -> FrameData:
    return FrameData(
        timestamp=datetime.now(timezone.utc).isoformat(),
        frame_number=index,
        physics={"speed_kmh": 100.0, "fuel": 20.0 - index * 0.001},
        graphics={
            "normalized_car_position": (index % 200) / 200.0,
            "has_authoritative_progress": True,
            "current_time_ms": timer,
            "last_time_ms": last,
            "completed_laps": 0,
            "is_valid_lap": True,
        },
    )


def test_late_result_update_keeps_original_capture_frame_boundary():
    capture = TelemetryCapture(hz=10.0)
    capture._frames = [FrameData("2026-01-01T00:00:00Z", i, {"speed_kmh": 100.0}) for i in range(50)]
    lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=20_000,
        lap_time_str="0:20.000",
    )

    capture.record_lap_boundary(20_000, 1, "VALID")
    assert capture.bind_lap_boundary("session-a", lap)

    lap.lap_number = 4
    lap.lap_time_ms = 19_500
    lap.lap_type = LapState.INVALID_GAME.value
    lap.is_valid = False
    assert capture.reconcile_lap_boundary("session-a", lap)

    boundary = capture.get_lap_boundaries()[0]
    assert boundary.frame_index == 49
    assert boundary.lap_number == 4
    assert boundary.original_lap_number == 1
    assert boundary.lap_type == "INVALID_GAME"
    assert boundary.result_id == lap.result_id


@pytest.mark.asyncio
async def test_identity_boundary_does_not_use_stale_display_number_maps(tmp_path):
    manager = SharedSessionManager()
    manager._session_data.lap_timing[4] = type("Timing", (), {"completed_lap_time": 99_999.0})()
    manager._session_data.lap_validity[4] = type("Validity", (), {"is_valid": False})()
    analyzer = TelemetryAnalyzer(str(tmp_path), session_manager=manager)
    analyzer._generate_html = AsyncMock(return_value="report.html")
    analyzer._generate_ai_prompt = AsyncMock(return_value="prompt.txt")

    frames = [_frame(i, i * 100) for i in range(200)]
    result = await analyzer.analyze(
        frames,
        hz=10.0,
        game_lap_boundaries=[
            LapBoundary(199, 20_000, 4, "VALID", "session-a", "result-a", 1),
        ],
        output_prefix="identity",
    )

    data = analyzer._generate_html.await_args.args[0]
    assert result.laps_detected == 1
    assert data["laps"][0]["lap_num"] == 4
    assert data["laps"][0]["source_lap_num"] == 1
    assert data["laps"][0]["lap_time_s"] == pytest.approx(20.0)
    assert data["laps"][0]["is_valid"] is True


@pytest.mark.asyncio
async def test_partial_timer_coverage_keeps_official_result_without_metrics(tmp_path):
    analyzer = TelemetryAnalyzer(str(tmp_path))
    analyzer._generate_html = AsyncMock(return_value="report.html")
    analyzer._generate_ai_prompt = AsyncMock(return_value="prompt.txt")
    frames = [_frame(i, i * 100) for i in range(100)]

    result = await analyzer.analyze(
        frames,
        hz=10.0,
        game_lap_boundaries=[LapBoundary(99, 20_000, 1, "VALID", "session-a", "result-a", 1)],
        output_prefix="partial",
    )

    data = analyzer._generate_html.await_args.args[0]
    lap = data["laps"][0]
    assert result.best_lap_time == pytest.approx(20.0)
    assert lap["derived_metrics_trustworthy"] is False
    assert lap["max_speed"] is None
    assert lap["avg_speed"] is None
    assert lap["fuel_used"] is None
    assert data["coaching_lap_nums"] == []
