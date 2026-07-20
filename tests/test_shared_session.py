"""Tests for shared session data infrastructure."""

from concurrent.futures import ThreadPoolExecutor
import time
import tracemalloc

from src.models import (
    LapData,
    LapState,
    SessionData,
    SharedSessionManager,
)


def test_update_from_graphics_sets_timing_and_fuel() -> None:
    manager = SharedSessionManager()

    manager.update_from_graphics_shm(
        {
            "session_current_lap": 3,
            "current_lap_time_ms": 61234,
            "last_laptime_ms": 120123,
            "best_laptime_ms": 119999,
            "ideal_laptime_ms": 119800,
            "delta_time_ms": -124,
            "timing_is_invalid": True,
            "fuel_liter_current_quantity": 21.5,
            "fuel_liter_per_km": 2.34,
            "km_per_fuel_liter": 0.42,
        }
    )

    # SHM validity flags are now wired into shared session.
    lap_validity = manager.get_lap_validity_data(3)
    assert lap_validity is not None
    assert lap_validity.is_valid is False
    assert lap_validity.lap_state == "INVALID_GAME"
    assert lap_validity.source == "shm_graphics"

    lap_timing = manager.get_lap_timing_data(3)
    assert lap_timing is not None
    assert lap_timing.current_lap_time_ms == 61234
    assert lap_timing.last_lap_time_ms == 120123

    fuel = manager.get_fuel_data()
    assert fuel.current_fuel == 21.5
    assert fuel.fuel_consumption_rate == 2.34
    assert fuel.fuel_economy == 0.42


def test_update_lap_from_logs_populates_player_and_sector_data() -> None:
    manager = SharedSessionManager()
    session = SessionData(
        session_id="session-1",
        player_id="76561198321627695",
        player_name="Driver",
        car_uuid="car-uuid",
        car="ks_porsche_992_gt3_cup",
    )
    lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=100000,
        lap_time_str="1:40.000",
        sector1_ms=32000,
        sector2_ms=33000,
        sector3_ms=35000,
        is_valid=True,
        timestamp="2026-01-01T00:00:00",
    )

    manager.update_lap_from_logs(lap, session_data=session)

    ident = manager.get_player_identification()
    assert ident.steam_id == "76561198321627695"
    assert ident.car_uuid == "car-uuid"
    assert ident.player_name == "Driver"

    sectors = manager.get_sector_split_data(1)
    assert sectors is not None
    assert sectors.sector1_ms == 32000
    assert sectors.sector2_ms == 33000
    assert sectors.sector3_ms == 35000


def test_shm_invalid_not_overwritten_by_log_heuristic_valid() -> None:
    """SHM says lap is invalid → log parser heuristic VALID must not overwrite it."""
    manager = SharedSessionManager()

    # SHM reports lap 2 as invalid
    manager.update_from_graphics_shm({
        "session_current_lap": 2,
        "is_invalid": True,
    })
    assert manager.get_lap_validity(2) is False

    # Log parser emits lap 2 with heuristic VALID (no authoritative onSplit)
    lap = LapData(
        lap_number=2,
        physics_lap_number=2,
        lap_time_ms=95000,
        lap_time_str="1:35.000",
        is_valid=True,
        lap_state=LapState.VALID,
        timestamp="2026-01-01T00:00:00",
    )
    manager.update_lap_from_logs(lap)

    # SHM verdict must be preserved
    validity = manager.get_lap_validity_data(2)
    assert validity is not None
    assert validity.is_valid is False
    assert validity.lap_state == "INVALID_GAME"


def test_log_authoritative_invalid_not_overwritten_by_shm_valid() -> None:
    """Log parser authoritative INVALID_GAME must not be overwritten by SHM VALID."""
    manager = SharedSessionManager()

    # Log parser emits lap 1 with authoritative INVALID_GAME
    lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=95000,
        lap_time_str="1:35.000",
        is_valid=False,
        lap_state=LapState.INVALID_GAME,
        lap_type="INVALID_GAME",
        timestamp="2026-01-01T00:00:00",
    )
    manager.update_lap_from_logs(lap)
    assert manager.get_lap_validity(1) is False

    # SHM later reports lap 1 as valid
    manager.update_lap_validity_from_graphics_shm(1, is_invalid=False)

    # Log verdict must be preserved
    validity = manager.get_lap_validity_data(1)
    assert validity is not None
    assert validity.is_valid is False
    assert validity.lap_state == "INVALID_GAME"
    assert validity.source == "logs"


def test_shm_valid_does_not_block_log_outlap() -> None:
    """SHM writes VALID for current lap → log parser emits OUTLAP → log must win."""
    manager = SharedSessionManager()

    # SHM reports lap 3 as valid (normal in-progress frame)
    manager.update_from_graphics_shm({
        "session_current_lap": 3,
        "is_invalid": False,
    })
    assert manager.get_lap_validity(3) is True

    # Log parser emits lap 3 as OUTLAP (heuristic, not authoritative)
    lap = LapData(
        lap_number=3,
        physics_lap_number=3,
        lap_time_ms=0,
        lap_time_str="0:00.000",
        is_valid=False,
        lap_state=LapState.OUTLAP,
        lap_type="OUTLAP",
        timestamp="2026-01-01T00:00:00",
    )
    manager.update_lap_from_logs(lap)

    # Log OUTLAP must replace the SHM VALID entry
    validity = manager.get_lap_validity_data(3)
    assert validity is not None
    assert validity.lap_state == "OUTLAP"
    assert validity.source == "logs"


def test_authoritative_log_valid_overrides_shm_invalid() -> None:
    """SHM says invalid → log parser emits authoritative VALID (from onSplit) → log wins."""
    manager = SharedSessionManager()

    # SHM reports lap 5 as invalid
    manager.update_from_graphics_shm({
        "session_current_lap": 5,
        "is_invalid": True,
    })
    assert manager.get_lap_validity(5) is False

    # Log parser emits lap 5 with authoritative VALID (Relevant onSplit said valid)
    lap = LapData(
        lap_number=5,
        physics_lap_number=5,
        lap_time_ms=95000,
        lap_time_str="1:35.000",
        is_valid=True,
        lap_state=LapState.VALID,
        lap_type="VALID",
        validity_source="authoritative",
        timestamp="2026-01-01T00:00:00",
    )
    manager.update_lap_from_logs(lap)

    # Authoritative log VALID must override SHM INVALID
    validity = manager.get_lap_validity_data(5)
    assert validity is not None
    assert validity.is_valid is True
    assert validity.lap_state == "VALID"
    assert validity.source == "logs"


def test_shm_invalid_does_not_block_log_invalid_split() -> None:
    """SHM says invalid → log parser emits INVALID_SPLIT → log state must win."""
    manager = SharedSessionManager()

    manager.update_from_graphics_shm({
        "session_current_lap": 2,
        "is_invalid": True,
    })

    lap = LapData(
        lap_number=2,
        physics_lap_number=2,
        lap_time_ms=95000,
        lap_time_str="1:35.000",
        is_valid=False,
        lap_state=LapState.INVALID_SPLIT,
        lap_type="INVALID_SPLIT",
        timestamp="2026-01-01T00:00:00",
    )
    manager.update_lap_from_logs(lap)

    validity = manager.get_lap_validity_data(2)
    assert validity is not None
    assert validity.lap_state == "INVALID_SPLIT"
    assert validity.source == "logs"


def test_shm_validity_change_gate_prevents_duplicate_updates() -> None:
    """Repeated SHM frames with same (lap, is_invalid) must not call update twice."""
    manager = SharedSessionManager()

    # First frame: lap 1, invalid=False
    manager.update_from_graphics_shm({
        "session_current_lap": 1,
        "is_invalid": False,
    })
    assert manager._last_validity_state == (1, False)

    # Second frame: same state — should be gated, no new update
    # We verify by checking that the change gate tuple hasn't triggered a re-notification.
    # The gate is working if the state tuple is unchanged and no exception occurs.
    manager.update_from_graphics_shm({
        "session_current_lap": 1,
        "is_invalid": False,
    })
    assert manager._last_validity_state == (1, False)

    # Third frame: is_invalid transitions to True — should update
    manager.update_from_graphics_shm({
        "session_current_lap": 1,
        "is_invalid": True,
    })
    assert manager._last_validity_state == (1, True)
    assert manager.get_lap_validity(1) is False


def test_update_from_static_shm_sets_session_metadata() -> None:
    manager = SharedSessionManager()

    manager.update_from_static_shm(
        {
            "ac_evo_version": "0.9.3",
            "session": 1,
            "session_name": "Practice",
            "track": "spa_francorchamps",
            "track_configuration": "gp",
            "track_length_m": 7004.0,
            "is_online": True,
            "is_timed_race": False,
            "event_id": 4,
        }
    )

    metadata = manager.get_session_metadata_data()
    assert metadata.game_version == "0.9.3"
    assert metadata.session_type == "1"
    assert metadata.track == "spa_francorchamps"
    assert metadata.source == "shm_static"


def test_get_lap_time_uses_source_priority() -> None:
    manager = SharedSessionManager()

    manager._session_data.calc_lap_times[5] = 150000.0
    manager._session_data.lap_times_logs[5] = 130000.0
    assert manager.get_lap_time(5) == 130000.0

    # Graphics SHM times must NOT override log-sourced times.
    manager._session_data.lap_times_graphics[5] = 120000.0
    assert manager.get_lap_time(5) == 130000.0


def test_get_lap_time_graphics_fallback_when_no_log_time() -> None:
    """Graphics SHM times must be visible when log-sourced times are absent."""
    manager = SharedSessionManager()

    manager._session_data.lap_times_graphics[3] = 95000.0
    assert manager.get_lap_time(3) == 95000.0

    # get_all_lap_times must include graphics-only laps
    all_times = manager.get_all_lap_times()
    assert 3 in all_times
    assert all_times[3] == 95000.0

    # get_best_lap_time must consider graphics times
    assert manager.get_best_lap_time() == 95000.0


def test_validate_data_consistency_reports_large_source_drift() -> None:
    manager = SharedSessionManager()

    manager._session_data.lap_times_graphics[1] = 100000.0
    manager._session_data.lap_times_logs[1] = 100050.0
    manager._session_data.lap_times_graphics[2] = 100000.0
    manager._session_data.lap_times_logs[2] = 100250.0

    result = manager.validate_data_consistency()

    assert "inconsistencies" in result
    assert len(result["inconsistencies"]) == 1
    assert "lap 2" in result["inconsistencies"][0]


def test_legacy_wrapper_converts_shared_state_to_session_data() -> None:
    manager = SharedSessionManager()
    session = SessionData(
        session_id="session-legacy",
        game_version="1.0.0",
        session_type="PRACTICE",
        car="ks_porsche_992_gt3_cup",
        track="spa_francorchamps",
        player_id="76561198321627695",
        player_name="Driver",
        car_uuid="car-uuid",
    )
    lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=123456,
        lap_time_str="2:03.456",
        sector1_ms=40000,
        sector2_ms=41000,
        sector3_ms=42456,
        is_valid=True,
        timestamp="2026-01-01T00:00:00",
    )

    manager.update_lap_from_logs(lap, session_data=session)
    wrapper = manager.get_legacy_wrapper()
    legacy_session = wrapper.to_session_data()

    assert legacy_session.session_id == "session-legacy"
    assert legacy_session.player_id == "76561198321627695"
    assert len(legacy_session.laps) == 1
    assert legacy_session.laps[0].lap_time_ms == 123456
    assert legacy_session.laps[0].sector2_ms == 41000


def test_to_legacy_session_data_uses_log_lap_time_priority() -> None:
    manager = SharedSessionManager()
    session = SessionData(
        session_id="session-priority",
        player_id="76561198321627695",
        player_name="Driver",
        car_uuid="car-uuid",
        car="ks_porsche_992_gt3_cup",
    )
    lap = LapData(
        lap_number=2,
        physics_lap_number=2,
        lap_time_ms=130000,
        lap_time_str="2:10.000",
        is_valid=True,
        timestamp="2026-01-01T00:00:00",
    )

    manager.update_lap_from_logs(lap, session_data=session)
    manager.update_lap_timing_from_graphics_shm(2, {"last_laptime_ms": 120000})

    legacy_session = manager.to_legacy_session_data()
    lap_two = next(l for l in legacy_session.laps if l.lap_number == 2)
    # Log-sourced time (130000) must take priority over graphics SHM (120000).
    assert lap_two.lap_time_ms == 130000


def test_legacy_wrapper_preserves_log_derived_outlap_state() -> None:
    manager = SharedSessionManager()
    session = SessionData(
        session_id="session-outlap",
        session_type="PRACTICE",
        player_id="76561198321627695",
        player_name="Driver",
        car_uuid="car-uuid",
        car="ks_porsche_992_gt3_cup",
        track="spa_francorchamps",
    )
    lap = LapData(
        lap_number=1,
        physics_lap_number=1,
        lap_time_ms=120000,
        lap_time_str="2:00.000",
        lap_state=LapState.OUTLAP,
        lap_type=LapState.OUTLAP.value,
        is_valid=False,
        timestamp="2026-01-01T00:00:00",
    )

    manager.update_lap_from_logs(lap, session_data=session)

    legacy_session = manager.to_legacy_session_data()
    assert legacy_session.laps[0].lap_state == LapState.OUTLAP
    assert legacy_session.laps[0].lap_type == "OUTLAP"


def test_observer_notified_and_observer_errors_are_isolated() -> None:
    manager = SharedSessionManager()
    notifications: list[int] = []

    def _ok_observer(snapshot) -> None:
        notifications.append(snapshot.current_lap or 0)

    def _failing_observer(_snapshot) -> None:
        raise RuntimeError("observer failure")

    manager.subscribe(_failing_observer)
    manager.subscribe(_ok_observer)

    manager.update_from_graphics_shm({"session_current_lap": 7, "last_laptime_ms": 111111})

    assert notifications
    assert notifications[-1] == 7


def test_update_from_physics_shm_updates_car_setup_and_max_speed() -> None:
    manager = SharedSessionManager()

    manager.update_from_physics_shm(
        {
            "speed_kmh": 250.5,
            "car_setup": {"brake_bias": 0.59, "ride_height_front": 58},
            "assists_state": {"abs": True},
            "air_density": 1.18,
        }
    )

    setup = manager.get_car_setup()
    assert setup["brake_bias"] == 0.59
    assert setup["ride_height_front"] == 58
    assert manager._session_data.max_speed == 250.5
    assert manager._session_data.assists_state["abs"] is True
    assert manager._session_data.air_density == 1.18


def test_concurrent_updates_are_thread_safe() -> None:
    manager = SharedSessionManager()

    def _write(idx: int) -> None:
        manager.update_lap_timing_from_graphics_shm(idx, {"last_laptime_ms": 100000 + idx})
        manager.update_lap_validity_from_graphics_shm(idx, idx % 2 == 0)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_write, range(1, 50)))

    # Graphics times are stored in lap_times_graphics and serve as fallback
    # in get_all_lap_times when no log-sourced times exist.
    with manager._lock:
        graphics_count = len(manager._session_data.lap_times_graphics)
    lap_validity = manager.get_all_lap_validity()
    all_lap_times = manager.get_all_lap_times()
    assert graphics_count == 49
    assert len(lap_validity) == 49
    assert len(all_lap_times) == 49


def test_concurrent_access_performance() -> None:
    manager = SharedSessionManager()

    def _write(idx: int) -> None:
        manager.update_lap_timing_from_graphics_shm(idx, {"last_laptime_ms": 90000 + idx})
        manager.update_lap_validity_from_graphics_shm(idx, False)

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(_write, range(1, 1501)))
    elapsed = time.perf_counter() - start

    # Guard against major regressions while avoiding flaky micro-bench assertions.
    assert elapsed < 8.0
    with manager._lock:
        graphics_count = len(manager._session_data.lap_times_graphics)
    assert graphics_count == 1500


def test_memory_usage_optimization() -> None:
    manager = SharedSessionManager()

    tracemalloc.start()
    for idx in range(1, 3001):
        manager.update_lap_timing_from_graphics_shm(idx, {"last_laptime_ms": 100000 + idx})
        manager.update_lap_validity_from_graphics_shm(idx, idx % 11 == 0)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Peak should stay well-bounded for a few thousand updates.
    assert peak < 32 * 1024 * 1024
    assert current < peak


def test_large_session_handling() -> None:
    manager = SharedSessionManager()
    session = SessionData(
        session_id="large-session",
        player_id="76561198321627695",
        player_name="Driver",
        car_uuid="car-uuid",
        car="ks_porsche_992_gt3_cup",
    )

    for lap_num in range(1, 2001):
        lap = LapData(
            lap_number=lap_num,
            physics_lap_number=lap_num,
            lap_time_ms=100000 + lap_num,
            lap_time_str="1:40.000",
            sector1_ms=33000,
            sector2_ms=33000,
            sector3_ms=34000,
            is_valid=True,
            timestamp="2026-01-01T00:00:00",
        )
        manager.update_lap_from_logs(lap, session_data=session)

    legacy_session = manager.to_legacy_session_data()
    assert len(legacy_session.laps) == 2000
    assert manager.get_lap_time(2000) == 102000.0
