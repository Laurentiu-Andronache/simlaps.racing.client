"""Synthetic regressions for live stationary laps and unverified reports."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.telemetry_analyzer import TelemetryAnalyzer
from src.core.telemetry_capture import FrameData, GameProcessStatus, TelemetryCapture
from src.models import LapData, LapState, SessionData, SharedSessionManager
from src.ui.app import SimLapsApp
from src.ui.services.lap_processing_service import LapProcessingService
from src.ui.services.lap_submission_service import LapSubmissionService
from src.ui.services.telemetry_lifecycle_service import TelemetryLifecycleService
from src.utils.config import AppConfig


class _Reader:
    size = 4
    _path_used = "synthetic"

    def read_raw(self):
        return bytes(self.size)

    def close(self):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("record", [True, False])
async def test_stationary_first_lap_keeps_sampling_until_completion(tmp_path, monkeypatch, record):
    manager = SharedSessionManager()
    manager.begin_session("stationary", car_model="Ferrari F2004")
    capture = TelemetryCapture(hz=10, output_dir=str(tmp_path), record_frames=record, session_manager=manager)
    clock = SimpleNamespace(now=0.0, sample=0)
    lifecycle = TelemetryLifecycleService()
    analyzer = TelemetryAnalyzer(str(tmp_path), session_manager=manager)
    auto_stop = AsyncMock()
    capture.set_on_stop_callback(auto_stop)
    real_sleep = asyncio.sleep
    stop_task = None

    def graphics(_raw):
        n = clock.sample
        clock.sample += 1
        timer = 30_000 if n < 2 else (n - 2) * 100 if n < 1602 else 0
        return {
            "status_name": "AC_LIVE", "session_phase": "Session", "car_model": "Ferrari F2004",
            "is_valid_lap": not (1300 <= n < 1602), "total_lap_count": int(n >= 1602),
            "completed_laps": int(n >= 1602), "current_lap_time_ms": timer, "current_time_ms": timer,
            "last_laptime_ms": 160_000 if n >= 1602 else 0,
            "last_time_ms": 160_000 if n >= 1602 else 0,
            "normalized_car_position": min(0.99, max(0, n - 2) / 1600),
            "has_authoritative_progress": True,
        }

    def physics(_raw):
        return {"speed_kmh": 0.0 if clock.sample < 1250 else 100.0, "gear": 1, "fuel": 20.0}

    async def advance(delay):
        nonlocal stop_task
        clock.now += max(delay, 0.001)
        if clock.sample >= 1603 and stop_task is None:
            completion = manager.get_latest_lap_completion()
            assert completion is not None and completion.is_valid is False
            manager.update_lap_from_logs(LapData(
                lap_number=1, physics_lap_number=1, lap_time_ms=completion.lap_time_ms,
                lap_time_str="2:40.000", is_valid=False, lap_state=LapState.INVALID_GAME,
                lap_type="INVALID_GAME", validity_source="shm_graphics",
            ))
            capture.record_lap_boundary(completion.lap_time_ms, 1, "INVALID_GAME")
            # An explicit session stop owns finalization, not a speed heuristic.
            stop_task = asyncio.create_task(lifecycle.stop_capture(
                reason="session_end", discard=False, telemetry_capture=capture,
                telemetry_analyzer=analyzer if record else None,
                home_page=None, current_track_name="Laguna Seca GP Time Attack",
            ))
            await real_sleep(0)

    monkeypatch.setattr(capture, "_connect_regions", lambda: {"physics": _Reader(), "graphics": _Reader()})
    monkeypatch.setattr(capture, "_reconnect_missing", lambda _readers: None)
    monkeypatch.setattr("src.core.telemetry_capture.decode_physics", physics)
    monkeypatch.setattr("src.core.telemetry_capture.decode_graphics", graphics)
    monkeypatch.setattr("src.core.telemetry_capture.peek_graphics_validity", lambda _raw: {})
    monkeypatch.setattr("src.core.telemetry_capture.is_game_running", lambda: GameProcessStatus.RUNNING)
    monkeypatch.setattr("src.core.telemetry_capture.time", SimpleNamespace(perf_counter=lambda: clock.now))
    monkeypatch.setattr("src.core.telemetry_capture.asyncio.sleep", advance)

    await lifecycle.start_capture(telemetry_capture=capture, home_page=None, telemetry_enabled=record)
    await capture._task
    if stop_task is not None:
        await stop_task

    assert clock.sample == 1603
    assert capture.get_stop_reason() == "session_end"
    completion = manager.get_latest_lap_completion()
    assert completion is not None
    assert completion.lap_time_ms == 160_000
    assert completion.is_valid is False
    auto_stop.assert_not_called()
    if not record:
        assert capture.get_frames() == []
        assert list(tmp_path.iterdir()) == []
        return

    assert len(capture.get_frames()) == 1601
    assert capture.get_frames()[0].graphics["current_lap_time_ms"] == 0
    assert sum(frame.physics["speed_kmh"] < 1 for frame in capture.get_frames()) > 1200
    assert len(list(tmp_path.glob("*.html"))) == 1
    assert len(list(tmp_path.glob("*_ai_prompt.txt"))) == 1
    html = next(tmp_path.glob("*.html")).read_text(encoding="utf-8")
    report = json.JSONDecoder().raw_decode(html.split("const DATA = ", 1)[1])[0]
    assert report["laps"][0]["lap_time_s"] == 160.0
    assert report["laps"][0]["is_unverified"] is False
    assert report["laps"][0]["derived_metrics_trustworthy"] is True


def test_capture_does_not_write_with_a_new_origin_obtained_after_readiness(monkeypatch):
    manager = SharedSessionManager()
    manager.begin_session("old", car_model="Ferrari F2004")
    graphics = {"car_model": "Ferrari F2004", "status_name": "AC_LIVE", "session_phase": "Session"}
    manager.update_from_graphics_shm(graphics)
    capture = TelemetryCapture(session_manager=manager)
    capture._capture_origin = manager.get_session_origin()
    capture._readers = {"physics": _Reader(), "graphics": _Reader()}
    monkeypatch.setattr("src.core.telemetry_capture.decode_physics", lambda _raw: {"speed_kmh": 333.0})
    monkeypatch.setattr("src.core.telemetry_capture.decode_graphics", lambda _raw: graphics)
    monkeypatch.setattr("src.core.telemetry_capture.peek_graphics_validity", lambda _raw: {})
    original_confirm = capture._confirm_capture_origin

    def rollover_after_validation(origin):
        original_confirm(origin)
        manager.begin_session("replacement", car_model="Ferrari F2004")
        manager.update_from_graphics_shm(graphics)

    monkeypatch.setattr(capture, "_confirm_capture_origin", rollover_after_validation)
    capture._capture_frame(0)

    assert manager.get_data_for_origin(manager.get_session_origin()).max_speed != 333.0
    assert capture._last_frame_origin_stable is False


def _lap_frames():
    return [
        FrameData(
            timestamp="2026-01-01T00:00:00Z", frame_number=n,
            physics={"speed_kmh": 100.0, "gear": 3, "fuel": 20.0 - n / 1000},
            graphics={"current_time_ms": n * 100, "last_time_ms": 0,
                      "normalized_car_position": n / 600, "has_authoritative_progress": True},
        )
        for n in range(601)
    ]


@pytest.mark.asyncio
async def test_unverified_boundary_preserves_official_result_without_pb_or_coaching(tmp_path):
    manager = SharedSessionManager()
    analyzer = TelemetryAnalyzer(str(tmp_path), session_manager=manager)
    result = await analyzer.analyze(
        _lap_frames(), hz=10, track_name="Laguna Seca", output_prefix="unverified",
        game_lap_boundaries=[(600, 60_000, 1, "UNVERIFIED")],
    )
    html = (tmp_path / "telemetry_unverified.html").read_text(encoding="utf-8")
    data = json.JSONDecoder().raw_decode(html.split("const DATA = ", 1)[1])[0]
    assert result.best_lap_time is None
    assert data["laps"][0]["lap_time_s"] == 60.0
    assert data["laps"][0]["is_unverified"] is True
    assert data["best_lap_num"] is None
    assert not list(tmp_path.glob("session_history*"))
    prompt = (tmp_path / "telemetry_unverified_ai_prompt.txt").read_text(encoding="utf-8")
    assert "UNVERIFIED" in prompt
    assert "invalid-lap diagnostics" not in prompt


@pytest.mark.asyncio
async def test_unverified_boundary_is_not_promoted_by_in_progress_validity(tmp_path):
    manager = SharedSessionManager()
    manager.update_lap_validity_from_graphics_shm(1, is_invalid=False)
    analyzer = TelemetryAnalyzer(str(tmp_path), session_manager=manager)
    analyzer._generate_html = AsyncMock(return_value="html")
    analyzer._generate_ai_prompt = AsyncMock(return_value="prompt")
    await analyzer.analyze(
        _lap_frames(), hz=10, track_name="Laguna Seca",
        game_lap_boundaries=[(600, 60_000, 1, "UNVERIFIED")],
    )
    data = analyzer._generate_html.call_args.args[0]
    assert data["laps"][0]["is_unverified"] is True
    assert data["best_lap_num"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [True, False])
@pytest.mark.parametrize("source", ["completed", "live_reused_counter", "different_session"])
async def test_unknown_report_upgrade_requires_owned_frozen_completed_verdict(tmp_path, valid, source):
    manager = SharedSessionManager()
    manager.begin_session("captured", car_model="Ferrari F2004")
    graphics = {"car_model": "Ferrari F2004", "status_name": "AC_LIVE", "session_phase": "Session"}
    manager.update_from_graphics_shm(graphics)
    origin = manager.get_session_origin()
    if source == "different_session":
        manager.begin_session("replacement", car_model="Ferrari F2004")
        manager.update_from_graphics_shm(graphics)
    if source == "live_reused_counter":
        from src.models.shared_session import LapTimingData
        manager._session_data.lap_timing[1] = LapTimingData(lap_number=1, completed_lap_time=60_000)
        manager.update_lap_validity_from_graphics_shm(1, is_invalid=not valid)
    else:
        state = LapState.VALID if valid else LapState.INVALID_GAME
        manager.update_lap_from_logs(LapData(
            lap_number=1, physics_lap_number=1, lap_time_ms=60_000, lap_time_str="1:00.000",
            is_valid=valid, lap_state=state, lap_type=state.value, validity_source="authoritative",
        ))
    analyzer = TelemetryAnalyzer(str(tmp_path), session_manager=manager)
    analyzer._generate_html = AsyncMock(return_value="html")
    analyzer._generate_ai_prompt = AsyncMock(return_value="prompt")
    await analyzer.analyze(
        _lap_frames(), hz=10, capture_origin=origin, capture_track_name="Laguna Seca",
        game_lap_boundaries=[(600, 60_000, 1, "UNVERIFIED")],
    )
    data = analyzer._generate_html.call_args.args[0]
    lap = data["laps"][0]
    assert lap["is_unverified"] is (source != "completed")
    assert lap["is_valid"] is (source == "completed" and valid)
    assert data["best_lap_num"] == (1 if source == "completed" and valid else None)
    assert (tmp_path / "session_history.jsonl").exists() is (source == "completed" and valid)


@pytest.mark.asyncio
async def test_missing_boundary_verdict_stays_unverified(tmp_path):
    analyzer = TelemetryAnalyzer(str(tmp_path))
    analyzer._generate_html = AsyncMock(return_value="html")
    analyzer._generate_ai_prompt = AsyncMock(return_value="prompt")
    await analyzer.analyze(_lap_frames(), hz=10, game_lap_boundaries=[(600, 60_000)])
    data = analyzer._generate_html.call_args.args[0]
    assert data["laps"][0]["is_unverified"] is True
    assert data["best_lap_num"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("complete_telemetry", [True, False])
@pytest.mark.parametrize("initial_state", ["OUTLAP", "UNVERIFIED"])
@pytest.mark.parametrize("renumber", [False, True])
async def test_public_outlap_correction_reaches_report_once(tmp_path, complete_telemetry, initial_state, renumber):
    app = SimLapsApp.__new__(SimLapsApp)
    app._config = AppConfig(auto_submit=False)
    app._session_manager = manager = SharedSessionManager()
    app._pb_cache = MagicMock()
    app._history_entries = []
    app._history_entry_by_lap_id = {}
    app._lap_processing_service = LapProcessingService()
    app._lap_submission_service = LapSubmissionService()
    app._home_page = MagicMock()
    app._home_page._lap_count = 0
    app.page = MagicMock()

    def add_lap(session, lap, status):
        app._home_page._lap_count += 1
        return SimpleNamespace(
            data=SimpleNamespace(session=session, lap=lap, status=status), update_status=MagicMock(),
        )

    app._home_page.add_lap.side_effect = add_lap
    session = SessionData(track="Laguna Seca", car="Ferrari F2004")
    manager.begin_session(session.session_id, car_model=session.car)
    manager.update_from_graphics_shm({
        "car_model": session.car, "status_name": "AC_LIVE", "session_phase": "Session",
    })
    capture = TelemetryCapture(output_dir=str(tmp_path), session_manager=manager)
    capture._capture_origin = manager.get_session_origin()
    capture._running = True
    capture._frames = _lap_frames()[:601 if complete_telemetry else 301]
    app._telemetry_capture = capture
    lap = LapData(
        lap_number=1, physics_lap_number=1, lap_time_ms=60_000, lap_time_str="1:00.000",
        lap_state=LapState(initial_state), lap_type=initial_state, is_valid=False,
    )
    manager.update_lap_from_logs(lap, session)
    await app._on_lap_complete(session, lap)
    original_boundaries = capture.get_lap_boundaries()
    assert original_boundaries[0].lap_type == initial_state
    assert app._home_page.add_lap.call_count == int(initial_state != "OUTLAP")

    lap.lap_state = LapState.VALID
    lap.lap_type = "VALID"
    lap.is_valid = True
    lap.validity_source = "authoritative"
    if renumber:
        lap.lap_number = 2
    manager.update_lap_from_logs(lap, session)
    await app._on_lap_update(session, lap)
    await app._on_lap_update(session, lap)

    assert app._home_page.add_lap.call_count == 1
    assert len(app._history_entries) == 1
    assert len(capture.get_lap_boundaries()) == 1
    assert capture.get_lap_boundaries()[0].frame_index == original_boundaries[0].frame_index
    assert capture.get_lap_boundaries()[0].original_lap_number == 1
    analyzer = TelemetryAnalyzer(str(tmp_path), session_manager=manager)
    result = await analyzer.analyze(
        capture.get_frames(), hz=10, capture_origin=capture.get_capture_origin(),
        capture_track_name=session.track, game_lap_boundaries=capture.get_lap_boundaries(),
        output_prefix="outlap_correction",
    )
    assert result.laps_detected == 1
    assert result.best_lap_time == 60.0
    html = (tmp_path / "telemetry_outlap_correction.html").read_text(encoding="utf-8")
    data = json.JSONDecoder().raw_decode(html.split("const DATA = ", 1)[1])[0]
    assert len(data["laps"]) == 1
    assert data["laps"][0]["is_valid"] is True
    assert data["laps"][0]["lap_num"] == lap.lap_number
    assert data["laps"][0]["derived_metrics_trustworthy"] is complete_telemetry
    assert (tmp_path / "telemetry_outlap_correction_ai_prompt.txt").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary_type,correction", [
    ("OUTLAP", "invalid"), ("OUTLAP", "live_flag"), ("OUTLAP", "different_owner"),
    ("OUTLAP", "different_time"), ("INLAP", "valid"), ("ABORTED", "valid"),
])
async def test_structural_report_boundary_requires_owned_valid_correction(tmp_path, boundary_type, correction):
    manager = SharedSessionManager()
    manager.begin_session("original", car_model="Ferrari F2004")
    graphics = {"car_model": "Ferrari F2004", "status_name": "AC_LIVE", "session_phase": "Session"}
    manager.update_from_graphics_shm(graphics)
    origin = manager.get_session_origin()
    if correction == "different_owner":
        manager.begin_session("replacement", car_model="Ferrari F2004")
        manager.update_from_graphics_shm(graphics)
    state = LapState.INVALID_GAME if correction == "invalid" else LapState.VALID
    manager.update_lap_from_logs(LapData(
        lap_number=1, physics_lap_number=1,
        lap_time_ms=65_000 if correction == "different_time" else 60_000,
        lap_time_str="1:00.000", lap_state=state, lap_type=state.value,
        is_valid=state == LapState.VALID, validity_source="authoritative",
    ))
    if correction == "live_flag":
        # Reproduce a live reused-counter flag sharing an old completed time.
        manager._session_data.lap_validity[1].source = "shm_graphics"
    analyzer = TelemetryAnalyzer(str(tmp_path), session_manager=manager)
    result = await analyzer.analyze(
        _lap_frames(), hz=10, capture_origin=origin, capture_track_name="Laguna Seca",
        game_lap_boundaries=[(600, 60_000, 1, boundary_type)],
    )
    assert result.laps_detected == 0
    assert result.html_path is None
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_renumbered_result_cannot_promote_equal_time_neighbor(tmp_path):
    manager = SharedSessionManager()
    manager.begin_session("captured", car_model="Ferrari F2004")
    manager.update_from_graphics_shm({
        "car_model": "Ferrari F2004", "status_name": "AC_LIVE", "session_phase": "Session",
    })
    capture = TelemetryCapture(output_dir=str(tmp_path), session_manager=manager)
    capture._capture_origin = manager.get_session_origin()
    capture._running = True
    first, neighbor = [
        LapData(lap_number=n, physics_lap_number=n, lap_time_ms=60_000, lap_time_str="1:00.000")
        for n in (1, 2)
    ]
    capture._frames = _lap_frames()
    capture.record_lap_boundary(60_000, 1, "UNVERIFIED")
    assert capture.bind_lap_boundary("captured", first)
    capture._frames.extend(replace(frame, frame_number=frame.frame_number + 600) for frame in _lap_frames()[1:])
    capture.record_lap_boundary(60_000, 2, "UNVERIFIED")
    assert capture.bind_lap_boundary("captured", neighbor)
    manager.update_lap_from_logs(first)
    manager.update_lap_from_logs(neighbor)

    first.lap_number = 2
    first.lap_state = LapState.VALID
    first.lap_type = "VALID"
    first.is_valid = True
    first.validity_source = "authoritative"
    manager.update_lap_from_logs(first)
    assert capture.reconcile_lap_boundary("captured", first)
    assert not capture.reconcile_lap_boundary("different-owner", neighbor)
    boundaries = capture.get_lap_boundaries()
    assert [boundary.frame_index for boundary in boundaries] == [600, 1200]
    assert [boundary.original_lap_number for boundary in boundaries] == [1, 2]
    assert [boundary.lap_number for boundary in boundaries] == [2, 2]
    analyzer = TelemetryAnalyzer(str(tmp_path), session_manager=manager)
    await analyzer.analyze(
        capture.get_frames(), hz=10, capture_origin=capture.get_capture_origin(),
        capture_track_name="Laguna Seca", game_lap_boundaries=boundaries, output_prefix="equal_neighbors",
    )
    html = (tmp_path / "telemetry_equal_neighbors.html").read_text(encoding="utf-8")
    data = json.JSONDecoder().raw_decode(html.split("const DATA = ", 1)[1])[0]
    assert [lap["is_valid"] for lap in data["laps"]] == [True, False]
    assert [lap["is_unverified"] for lap in data["laps"]] == [False, True]
    assert str(id(first)) not in html
    assert str(id(neighbor)) not in html
    capture.set_record_frames(False)
    assert capture.get_lap_boundaries() == []
    assert capture._lap_boundary_bindings == {}
    assert not capture.reconcile_lap_boundary("captured", first)
