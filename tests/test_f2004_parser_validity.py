"""Completed validity needs an owned verdict, including late public updates."""

import asyncio

import pytest

from src.core.log_parser import LogParser
from src.models import LapData, LapState, SessionData, SharedSessionManager

CAR = "11111111-1111-1111-1111-111111111111"


def test_new_lap_defaults_to_unverified():
    lap = LapData(1, 1, 100000, "01:40.000")
    assert lap.lap_state.value == "UNVERIFIED"
    assert lap.lap_type == "UNVERIFIED"
    assert lap.is_valid is False
    assert lap.validity_source == "unknown"
    assert lap.is_unverified


@pytest.mark.asyncio
@pytest.mark.parametrize("live_valid", [False, True])
async def test_file_lap_without_owned_completed_verdict_is_unverified(tmp_path, live_valid):
    path = tmp_path / "unknown.log"
    path.write_text(f"[2026-09-17 10:00:00.000] [gameplay] [info] New lap carId {CAR}: 01:40.000\n", encoding="utf-8")
    manager = SharedSessionManager()
    parser = LogParser(log_path=str(path), session_manager=manager)
    parser.current_session = SessionData(car="bmw_m4_gt3", car_uuid=CAR, session_type="RACE")
    parser.context.car_uuid = CAR
    manager.update_lap_validity_from_graphics_shm(1, live_valid)
    sessions = await parser.parse_file()
    assert len(sessions[0].laps) == 1
    assert sessions[0].laps[0].lap_state.value == "UNVERIFIED"
    assert sessions[0].best_lap is None


@pytest.mark.asyncio
@pytest.mark.parametrize("partial_line", [False, True])
@pytest.mark.parametrize("completed_valid", [False, True])
async def test_late_shm_reconciles_original_emitted_object_through_follow(tmp_path, partial_line, completed_valid):
    path = tmp_path / "late.log"
    path.write_text("", encoding="utf-8")
    manager = SharedSessionManager()
    emitted, updates = [], []

    async def on_lap(session, lap):
        emitted.append((session, lap))

    async def on_update(session, lap):
        updates.append((session, lap))

    parser = LogParser(log_path=str(path), session_manager=manager, on_lap_complete=on_lap, on_lap_update=on_update)
    parser.PENDING_VALIDITY_GRACE_SECONDS = 0
    task = asyncio.create_task(parser.follow(poll_interval=0.001))

    async def until(predicate):
        for _ in range(500):
            if predicate():
                return
            await asyncio.sleep(0.002)
        assert predicate()

    try:
        await asyncio.sleep(0.02)
        with path.open("a", encoding="utf-8") as log:
            log.write(f"76561198000000000 connected on car bmw_m4_gt3, with new carId {CAR}\n")
            log.write(f"[2026-09-17 10:00:00.000] [gameplay] [info] New lap carId {CAR}: 01:40.000\n")
        await until(lambda: emitted)
        session, lap = emitted[0]
        assert lap.is_unverified
        if partial_line:
            with path.open("a", encoding="utf-8") as log:
                log.write("[incomplete")
        manager.update_from_graphics_shm(
            {
                "car_model": "bmw_m4_gt3",
                "status": 2,
                "status_name": "AC_LIVE",
                "session_type": 2,
                "current_lap_time_ms": 99900,
                "last_laptime_ms": 0,
                "total_lap_count": 0,
                "is_valid_lap": completed_valid,
            }
        )
        manager.update_from_graphics_shm(
            {
                "car_model": "bmw_m4_gt3",
                "status": 2,
                "status_name": "AC_LIVE",
                "session_type": 2,
                "current_lap_time_ms": 100,
                "last_laptime_ms": 100000,
                "total_lap_count": 1,
                "is_valid_lap": True,
            }
        )
        await until(lambda: updates)
        assert len(emitted) == len(updates) == 1
        assert updates[0][0] is session and updates[0][1] is lap
        assert lap.is_valid is completed_valid
        assert lap.validity_source == "shm_graphics"
        await asyncio.sleep(0.02)
        assert len(updates) == 1
        # An authoritative broadcast can correct SHM, but repeating that
        # exact verdict must not create another update or completion.
        if not partial_line:
            official_valid = not completed_valid
            flag = 2 if official_valid else 1
            verdict = (
                "Relevant onSplit for Combo 1@1: laptime 100000, "
                f"valid {str(official_valid).lower()}, flags {flag}, lap 1 (prev 0)\n"
            )
            with path.open("a", encoding="utf-8") as log:
                log.write(verdict + verdict)
            await until(lambda: len(updates) == 2)
            await asyncio.sleep(0.02)
            assert len(updates) == 2 and len(emitted) == 1
            assert lap.is_valid is official_valid
            assert lap.validity_source == "authoritative"
    finally:
        parser.stop()
        await task


@pytest.mark.parametrize(("elapsed_ms", "retained"), [(59999, True), (60000, False), (60001, False)])
@pytest.mark.parametrize("timestamped", [False, True])
def test_rejected_prefix_exact_threshold_and_split_fallback(elapsed_ms, retained, timestamped):
    from datetime import datetime, timedelta

    parser = LogParser(session_manager=SharedSessionManager())
    parser.current_session = SessionData(session_type="PRACTICE")
    start = datetime(2026, 9, 17, 10, 0, 0, 500000)
    marker = f"[{start.isoformat(sep=' ', timespec='milliseconds')}] " if timestamped else ""
    end = start + timedelta(milliseconds=elapsed_ms)
    rejection = f"[{end.isoformat(sep=' ', timespec='milliseconds')}] " if timestamped else ""
    parser._process_line(marker + "Outplap split")
    if not timestamped:
        parser._outlap_candidate_splits[0] = elapsed_ms
    parser._process_line(rejection + "Couldn't create lap from opensplits")
    assert parser._ip.is_outlap is retained


@pytest.mark.asyncio
async def test_historical_restart_preserves_per_wheel_tyres_and_one_epoch(tmp_path):
    path = tmp_path / "restart.log"
    path.write_text("request made GameModeRequestRestartSession\n", encoding="utf-8")
    manager = SharedSessionManager()
    parser = LogParser(log_path=str(path), session_manager=manager)
    parser.current_session = SessionData(car="bmw_m4_gt3", car_uuid=CAR, session_type="RACE")
    parser._establish_session_ownership(parser.current_session)
    epoch = manager.get_session_origin().epoch
    for wheel, compound in enumerate(["S", "M", "H", "S"]):
        parser.context.tyre.set(wheel, compound)
    await parser.parse_file()
    assert parser.context.tyre._compounds == {0: "S", 1: "M", 2: "H", 3: "S"}
    assert manager.get_session_origin().epoch == epoch + 1


@pytest.mark.asyncio
async def test_live_track_boundary_opens_owner_before_callback_and_ignores_old_remove(tmp_path):
    path = tmp_path / "track.log"
    path.write_text("", encoding="utf-8")
    manager = SharedSessionManager()
    transitions, ended, emitted = [], [], []

    async def status(running):
        if running:
            transitions.append((parser.current_session, manager.get_session_origin()))

    async def lap(session, result):
        emitted.append((session, result))

    async def session_end():
        ended.append(True)

    parser = LogParser(
        log_path=str(path),
        session_manager=manager,
        on_game_status_change=status,
        on_lap_complete=lap,
        on_session_end=session_end,
    )
    parser.PENDING_VALIDITY_GRACE_SECONDS = 0
    task = asyncio.create_task(parser.follow(poll_interval=0.001))

    async def until(predicate):
        for _ in range(500):
            if predicate():
                return
            await asyncio.sleep(0.002)
        assert predicate()

    try:
        await asyncio.sleep(0.02)
        with path.open("a", encoding="utf-8") as log:
            log.write(f"76561198000000000 connected on car bmw_m4_gt3, with new carId {CAR}\n")
            log.write("TRACK NAME old_track\n")
            log.write(f"[2026-09-17 10:00:00.000] [gameplay] [info] New lap carId {CAR}: 01:40.000\n")
        await until(lambda: emitted)
        old = emitted[0][0]
        with path.open("a", encoding="utf-8") as log:
            log.write("TRACK NAME new_track\n")
        await until(lambda: transitions)
        replacement, origin = transitions[-1]
        assert replacement is not old
        assert origin.session_id == replacement.session_id
        assert old.track == "old_track" and replacement.track == "new_track"
        assert len(transitions) == 1
        other = "22222222-2222-2222-2222-222222222222"
        with path.open("a", encoding="utf-8") as log:
            log.write(f"76561198000000000 connected on car porsche_992_gt3_cup, with new carId {other}\n")
            log.write(f"onSetPlayerCurrentCarCommand: remove car {CAR}\n")
        await until(lambda: len(transitions) == 2)
        await asyncio.sleep(0.02)
        assert ended == []
        assert parser.current_session.car_uuid == other
    finally:
        parser.stop()
        await task


@pytest.mark.asyncio
async def test_owned_old_completion_updates_original_session_after_track_rollover(tmp_path):
    path = tmp_path / "late-old.log"
    path.write_text("", encoding="utf-8")
    manager = SharedSessionManager()
    emitted, updates, owners = [], [], []

    async def on_lap(session, lap):
        emitted.append((session, lap))

    async def on_update(session, lap):
        updates.append((session, lap))

    async def on_status(running):
        if running:
            owners.append(parser.current_session)

    parser = LogParser(
        log_path=str(path),
        session_manager=manager,
        on_lap_complete=on_lap,
        on_lap_update=on_update,
        on_game_status_change=on_status,
    )
    parser.PENDING_VALIDITY_GRACE_SECONDS = 0
    task = asyncio.create_task(parser.follow(poll_interval=0.001))

    async def until(predicate):
        for _ in range(500):
            if predicate():
                return
            await asyncio.sleep(0.002)
        assert predicate()

    try:
        await asyncio.sleep(0.02)
        with path.open("a", encoding="utf-8") as log:
            log.write(f"76561198000000000 connected on car bmw_m4_gt3, with new carId {CAR}\n")
            log.write("TRACK NAME old_track\n")
            log.write(f"[2026-09-17 10:00:00.000] [gameplay] [info] New lap carId {CAR}: 01:40.000\n")
        await until(lambda: emitted)
        old, lap = emitted[0]
        sample = {
            "car_model": "bmw_m4_gt3",
            "status_name": "AC_LIVE",
            "current_lap_time_ms": 99900,
            "last_laptime_ms": 0,
            "total_lap_count": 0,
            "is_valid_lap": False,
        }
        manager.update_from_graphics_shm(sample)
        manager.update_from_graphics_shm(
            {**sample, "current_lap_time_ms": 20, "last_laptime_ms": 100000, "total_lap_count": 1, "is_valid_lap": True}
        )
        # Append the replacement before yielding, so the parser encounters
        # the track boundary before draining the old frozen verdict.
        with path.open("a", encoding="utf-8") as log:
            log.write("TRACK NAME new_track\n")
        await until(lambda: updates and owners)
        assert len(updates) == len(emitted) == 1
        assert updates[0][0] is old and updates[0][1] is lap
        assert not lap.is_valid and lap.lap_state == LapState.INVALID_GAME
        assert owners[-1] is not old and owners[-1].track == "new_track"
        assert old.track == "old_track" and old.laps == [lap]
        assert manager.get_lap_completions_after(0) == []
    finally:
        parser.stop()
        await task


def test_analysis_snapshot_preserves_unknown_state_and_source():
    manager = SharedSessionManager()
    session = SessionData(car="bmw_m4_gt3", car_uuid=CAR)
    manager.begin_session(session.session_id, car_model=session.car, car_uuid=CAR)
    lap = LapData(1, 1, 100000, "01:40.000")
    manager.update_lap_from_logs(lap, session)
    snapshot = manager.get_analysis_snapshot_for_origin(manager.get_session_origin()).snapshot
    assert snapshot.validity_records == ((1, None),)
    assert snapshot.validity_state_records == ((1, "UNVERIFIED"),)
    assert snapshot.validity_source_records == ((1, "logs"),)
    assert snapshot.validity_result_records == ((1, id(lap)),)


@pytest.mark.asyncio
@pytest.mark.parametrize("other_completion", ["none", "replacement", "outgoing"])
async def test_late_outlap_promotion_uses_originating_queue_and_stint(other_completion):
    """Neither ambiguity checks nor stint writes can cross a car boundary."""
    manager = SharedSessionManager()
    updates = []

    async def on_update(session, lap):
        updates.append((session, lap))

    parser = LogParser(session_manager=manager, on_lap_update=on_update)
    parser.PENDING_VALIDITY_GRACE_SECONDS = 0
    old = SessionData(car="bmw_m4_gt3", car_uuid=CAR, tyre_compound="SOFT")
    parser.current_session = old
    parser._establish_session_ownership(old)
    lap = LapData(
        1,
        1,
        60000,
        "01:00.000",
        lap_state=LapState.OUTLAP,
        lap_type="OUTLAP",
        is_valid=False,
        tyre_compound="SOFT",
        fuel_used=2.0,
    )
    old.laps.append(lap)

    def finish(car, counter):
        sample = {
            "car_model": car,
            "status_name": "AC_LIVE",
            "current_lap_time_ms": 59900,
            "last_laptime_ms": 0,
            "total_lap_count": counter,
            "is_valid_lap": True,
        }
        manager.update_from_graphics_shm(sample)
        manager.update_from_graphics_shm(
            {**sample, "current_lap_time_ms": 20, "last_laptime_ms": 60000, "total_lap_count": counter + 1}
        )

    finish(old.car, 0)
    if other_completion == "outgoing":
        finish(old.car, 1)
    replacement = SessionData(car="alfa_romeo_giulia_quadrifoglio", car_uuid="22222222-2222-2222-2222-222222222222")
    parser.current_session = replacement
    parser._establish_session_ownership(replacement)
    if other_completion == "replacement":
        finish(replacement.car, 0)

    assert parser._take_ready_shm_lap() is None
    await parser._emit_reconciled_lap()
    assert replacement.stints == []
    assert replacement.laps == []
    assert parser._current_stint is None
    if other_completion == "outgoing":
        assert lap.lap_state == LapState.OUTLAP
        assert old.stints == []
        assert updates == []
    else:
        assert lap.lap_state == LapState.VALID
        assert len(old.stints) == 1
        assert old.stints[0].tyre_compound == "SOFT"
        assert old.stints[0].lap_numbers == [1]
        assert old.stints[0].fuel_used_total == 2.0
        assert updates == [(old, lap)]
