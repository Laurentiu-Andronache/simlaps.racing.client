"""Completed-lap evidence crosses real app callbacks and submission gates."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.api_client import APIClient, SubmissionStatus
from src.core.pb_cache import PBCache, PersonalBest
from src.models import LapData, LapState, SessionData, SharedSessionManager
from src.ui.app import SimLapsApp
from src.ui.components.lap_card import LapCardStatus
from src.ui.services.lap_processing_service import LapProcessingService
from src.ui.services.lap_submission_service import LapSubmissionService
from src.utils.config import AppConfig


def _app(*, invalid=False):
    app = SimLapsApp.__new__(SimLapsApp)
    app._config = AppConfig(auto_submit=True, submit_invalid_laps=invalid)
    app._session_manager = SharedSessionManager()
    app._pb_cache = PBCache("https://unused.invalid")
    app._telemetry_capture = MagicMock()
    app._telemetry_capture.owns_session.return_value = True
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
            data=SimpleNamespace(session=session, lap=lap, status=status),
            update_status=MagicMock(),
        )

    app._home_page.add_lap.side_effect = add_lap
    return app


def _lap(state="UNVERIFIED", *, time=90000):
    return LapData(
        lap_number=1, physics_lap_number=1, lap_time_ms=time,
        lap_time_str="01:30.000", lap_state=LapState(state), lap_type=state,
        is_valid=state == "VALID", validity_source="unknown" if state == "UNVERIFIED" else "authoritative",
    )


def _verdict(lap, valid):
    lap.lap_state = LapState.VALID if valid else LapState.INVALID_GAME
    lap.lap_type = lap.lap_state.value
    lap.is_valid = valid
    lap.validity_source = "authoritative"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [False, True])
async def test_public_unknown_then_valid_updates_one_result_and_submits_once(invalid):
    app = _app(invalid=invalid)
    session = SessionData(track="Laguna Seca", car="Ferrari F2004")
    lap = _lap()
    await app._on_lap_complete(session, lap)
    entry = app._history_entries[0]
    assert entry.lap_state == "UNVERIFIED"
    assert not entry.was_valid
    app.page.run_task.assert_not_called()
    assert not app._pb_cache.get_all_pbs()
    app._home_page.add_lap.assert_called_once_with(session, lap, LapCardStatus.UNVERIFIED)

    _verdict(lap, True)
    await app._on_lap_update(session, lap)
    await app._on_lap_update(session, lap)
    assert app._history_entries == [entry]
    assert entry.was_valid
    assert app._home_page.add_lap.call_count == 1
    assert app._telemetry_capture.record_lap_boundary.call_count == 1
    assert app.page.run_task.call_count == 1
    assert app._pb_cache.get_all_pbs()[("laguna seca", "ferrari f2004")].best_time_ms == 90000


@pytest.mark.asyncio
async def test_public_suppressed_outlap_upgrade_presents_once_without_new_boundary():
    app = _app(invalid=True)
    session = SessionData(track="Laguna Seca", car="Ferrari F2004")
    lap = _lap("OUTLAP")
    await app._on_lap_complete(session, lap)
    assert not app._history_entries
    app._home_page.add_lap.assert_not_called()
    app.page.run_task.assert_not_called()
    _verdict(lap, True)
    await app._on_lap_update(session, lap)
    await app._on_lap_update(session, lap)
    assert len(app._history_entries) == 1
    assert app._home_page.add_lap.call_count == 1
    assert app._telemetry_capture.record_lap_boundary.call_count == 1
    assert app.page.run_task.call_count == 1


@pytest.mark.asyncio
async def test_public_late_invalid_pb_keeps_other_valid_candidate_and_server_baseline():
    app = _app()
    session = SessionData(track="Laguna Seca", car="Ferrari F2004")
    key = ("laguna seca", "ferrari f2004")
    app._pb_cache._cache[key] = PersonalBest(100000)
    fastest, other = _lap("VALID", time=80000), _lap("VALID", time=90000)
    await app._on_lap_complete(session, fastest)
    await app._on_lap_complete(session, other)
    _verdict(fastest, False)
    await app._on_lap_update(session, fastest)
    assert app._pb_cache.get_all_pbs()[key].best_time_ms == 90000
    _verdict(other, False)
    await app._on_lap_update(session, other)
    assert app._pb_cache.get_all_pbs()[key].best_time_ms == 100000


@pytest.mark.asyncio
async def test_api_unknown_guard_is_after_no_secret_before_game_and_transport(monkeypatch):
    from src.core import api_client as module
    client = APIClient(session_manager=SharedSessionManager())
    monkeypatch.setattr(module, "is_secret_configured", lambda: False)
    game = MagicMock(side_effect=AssertionError("game check not expected"))
    monkeypatch.setattr(module, "is_game_running", game)
    session, lap = SessionData(), _lap()
    assert (await client.submit_lap(session, lap, submit_invalid=True)).status == SubmissionStatus.NO_SECRET
    monkeypatch.setattr(module, "is_secret_configured", lambda: True)
    assert (await client.submit_lap(session, lap, submit_invalid=True)).status == SubmissionStatus.UNVERIFIED_LAP
    game.assert_not_called()


@pytest.mark.asyncio
async def test_submission_success_after_verdict_changes_records_sent_evidence_and_requires_review():
    from src.core.api_client import SubmissionResult, SubmittedLapSnapshot
    from src.ui.pages.history import HistoryEntry
    lap = _lap("VALID")
    entry = HistoryEntry("Laguna Seca", "Ferrari F2004", 90000, lap.timestamp, False, True)
    sent = SubmittedLapSnapshot(is_valid=True, lap_time_ms=90000)

    async def submit(**_kwargs):
        _verdict(lap, False)
        return SubmissionResult(SubmissionStatus.SUCCESS, "ok", submitted_snapshot=sent)

    api = SimpleNamespace(submit_lap=submit)
    card, discord = MagicMock(), AsyncMock()
    await LapSubmissionService().submit_lap(
        api_client=api, config=AppConfig(), card=card,
        session=SessionData(track="Laguna Seca", car="Ferrari F2004"),
        lap=lap, history_entry=entry, pb_was_new=True, post_to_discord=discord,
    )
    assert entry.was_submitted
    assert entry.submitted_is_valid is True
    assert entry.submission_needs_review
    card.update_status.assert_called_with(LapCardStatus.REVIEW_REQUIRED)
    discord.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_outgoing_update_does_not_replace_active_track_or_player():
    app = _app()
    old = SessionData(track="Old Track", car="Old Car", player_id="old-driver")
    lap = _lap()
    await app._on_lap_complete(old, lap)
    replacement = SessionData(track="Active Track", car="Active Car", player_id="active-driver")
    app._session_manager.begin_session(replacement.session_id, car_model=replacement.car)
    app._current_track_name = replacement.track
    app._home_page.set_detected_user.reset_mock()
    _verdict(lap, True)
    await app._on_lap_update(old, lap)
    assert app._current_track_name == "Active Track"
    app._home_page.set_detected_user.assert_not_called()
    assert app._history_entries[0].track == "Old Track"


@pytest.mark.asyncio
async def test_preflight_unverified_rejection_allows_later_first_eligible_attempt():
    from src.core.api_client import SubmissionResult
    app = _app()
    session = SessionData(track="Laguna Seca", car="Ferrari F2004")
    lap = _lap("VALID")
    await app._on_lap_complete(session, lap)
    queued = app.page.run_task.call_args.args

    async def reject(**_kwargs):
        lap.lap_state = LapState.UNVERIFIED
        lap.lap_type = "UNVERIFIED"
        lap.is_valid = False
        return SubmissionResult(SubmissionStatus.UNVERIFIED_LAP, "waiting")

    app._api_client = SimpleNamespace(submit_lap=reject)
    app._post_to_discord = AsyncMock()
    await app._submit_lap(*queued[1:])
    assert not app._history_entries[0].submission_attempted
    assert not app._history_entries[0].was_submitted
    _verdict(lap, True)
    await app._on_lap_update(session, lap)
    await app._on_lap_update(session, lap)
    assert app.page.run_task.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("unverified", [False, True])
async def test_queued_submission_rechecks_current_eligibility_without_api(unverified):
    app = _app(invalid=unverified)
    session, lap = SessionData(track="Laguna Seca", car="Ferrari F2004"), _lap("VALID")
    await app._on_lap_complete(session, lap)
    queued = app.page.run_task.call_args.args
    _verdict(lap, False)
    if unverified:
        lap.lap_state, lap.lap_type = LapState.UNVERIFIED, "UNVERIFIED"
    app._api_client = SimpleNamespace(submit_lap=AsyncMock())
    app._post_to_discord = AsyncMock()
    await app._on_lap_update(session, lap)
    await app._submit_lap(*queued[1:])
    app._api_client.submit_lap.assert_not_awaited()
    app._post_to_discord.assert_not_awaited()
    assert not app._history_entries[0].submission_attempted


@pytest.mark.asyncio
async def test_queued_invalid_submission_never_announces_personal_best():
    service = LapSubmissionService()
    notifier = SimpleNamespace(post_lap=AsyncMock(return_value=True))
    lap = _lap("INVALID_GAME")
    config = AppConfig(discord_enabled=True, discord_pb_only=True,
                       discord_webhook_url="https://discord.com/api/webhooks/test/test")
    await service.post_to_discord(
        config=config, discord_notifier=notifier,
        session=SessionData(track="Laguna Seca", car="Ferrari F2004"),
        lap=lap, steam_id="driver", pb_was_new=True,
    )
    notifier.post_lap.assert_not_awaited()
    config.discord_pb_only = False
    await service.post_to_discord(
        config=config, discord_notifier=notifier,
        session=SessionData(track="Laguna Seca", car="Ferrari F2004"),
        lap=lap, steam_id="driver", pb_was_new=True,
    )
    assert not notifier.post_lap.call_args.args[0].is_personal_best


def _api_test_client(monkeypatch):
    from src.core import api_client as module
    monkeypatch.setattr(module, "is_secret_configured", lambda: True)
    monkeypatch.setattr(module, "is_game_running", lambda: module.GameProcessStatus.RUNNING)
    monkeypatch.setattr(module, "get_app_secret", lambda: "test-only")
    monkeypatch.setattr(module, "get_secret_source", lambda: "test")
    monkeypatch.setattr(module, "verify_signature_locally", lambda _payload: True)
    signer = MagicMock(side_effect=lambda payload: {
        **payload, "_timestamp": "test", "_nonce": "test", "_signature": "test",
    })
    monkeypatch.setattr(module, "sign_payload", signer)
    response = SimpleNamespace(status_code=201, json=lambda: {"id": "acknowledged"})
    transport = SimpleNamespace(post=AsyncMock(return_value=response))
    client = APIClient(session_manager=SharedSessionManager())
    return client, transport, signer


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["UNVERIFIED", "INVALID_GAME"])
async def test_api_rechecks_after_await_before_signing_or_posting(monkeypatch, state):
    client, transport, signer = _api_test_client(monkeypatch)
    lap = _lap("VALID")

    async def prepare():
        lap.lap_state, lap.lap_type, lap.is_valid = LapState(state), state, False
        return transport

    monkeypatch.setattr(client, "_get_client", prepare)
    result = await client.submit_lap(SessionData(player_id="driver", track="Laguna Seca", car="Ferrari F2004"), lap)
    expected = SubmissionStatus.UNVERIFIED_LAP if state == "UNVERIFIED" else SubmissionStatus.INVALID_LAP
    assert result.status == expected
    signer.assert_not_called()
    transport.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_api_returned_snapshot_is_actual_post_payload_after_prepare(monkeypatch):
    client, transport, _signer = _api_test_client(monkeypatch)
    lap = _lap("VALID")

    async def prepare():
        lap.lap_time_ms = 91000
        return transport

    async def post(_url, *, json):
        assert json["time"] == 91000
        _verdict(lap, False)
        lap.lap_time_ms = 92000
        return SimpleNamespace(status_code=201, json=lambda: {"id": "acknowledged"})

    transport.post = AsyncMock(side_effect=post)
    monkeypatch.setattr(client, "_get_client", prepare)
    result = await client.submit_lap(SessionData(player_id="driver", track="Laguna Seca", car="Ferrari F2004"), lap)
    assert result.status == SubmissionStatus.SUCCESS
    assert result.submitted_snapshot.is_valid is True
    assert result.submitted_snapshot.lap_time_ms == 91000
    assert lap.is_valid is False


@pytest.mark.asyncio
async def test_acknowledged_lap_late_invalid_preserves_ack_without_resubmit():
    app = _app()
    session, lap = SessionData(track="Laguna Seca", car="Ferrari F2004"), _lap("VALID")
    await app._on_lap_complete(session, lap)
    entry = app._history_entries[0]
    entry.was_submitted = True
    entry.submitted_is_valid = True
    entry.submitted_lap_time_ms = 90000
    _verdict(lap, False)
    await app._on_lap_update(session, lap)
    assert entry.was_submitted and entry.submission_needs_review
    assert app.page.run_task.call_count == 1
    assert not app._pb_cache.get_all_pbs()


def test_unknown_retry_is_blocked_by_app_home_and_card():
    from src.ui.components.lap_card import LapCard, LapCardData
    from src.ui.pages.home import HomePage
    app, lap, session = _app(invalid=True), _lap(), SessionData()
    card = LapCard(LapCardData(session, lap, 1, LapCardStatus.FAILED, "network failure"))
    app._on_retry_lap(card)
    app.page.run_task.assert_not_called()
    home = HomePage.__new__(HomePage)
    home.config = app._config
    home.on_retry_lap = MagicMock()
    home._on_retry_lap(card)
    home.on_retry_lap.assert_not_called()
    assert not any(getattr(item, "text", None) == "Retry" for item in card.content.controls)


@pytest.mark.asyncio
async def test_pb_preload_refresh_keeps_candidates_but_driver_change_clears(monkeypatch):
    from src.core import pb_cache as module
    cache = PBCache("https://unused.invalid")
    response = SimpleNamespace(status_code=200, json=lambda: {"personalBests": [
        {"trackId": "track", "carId": "car", "bestTime": 100000},
    ]})
    http = MagicMock()
    http.return_value.__aenter__.return_value.get = AsyncMock(return_value=response)
    monkeypatch.setattr(module.httpx, "AsyncClient", http)
    assert await cache.preload_from_api("driver-a")
    assert cache.reconcile_lap_candidate(("session", 1), "TRACK", "CAR", 90000, eligible=True)
    assert not cache.reconcile_lap_candidate(("session", 1), "TRACK", "CAR", 90000, eligible=True)
    assert await cache.preload_from_api("driver-a")
    assert cache.get_personal_best("track", "car").best_time_ms == 90000
    assert await cache.preload_from_api("driver-b")
    assert cache.get_personal_best("track", "car").best_time_ms == 100000
    cache.clear()
    assert not cache.get_all_pbs() and not cache._candidates and not cache._baseline


def test_pb_legacy_candidate_survives_removal_of_faster_identified_lap():
    cache = PBCache("https://unused.invalid")
    cache.check_and_update_pb("track", "car", 100000)
    assert cache.reconcile_lap_candidate("lap", "track", "car", 80000, eligible=True)
    assert not cache.check_and_update_pb("track", "car", 90000)
    cache.reconcile_lap_candidate("lap", "track", "car", 80000, eligible=False)
    assert cache.get_all_pbs()[("track", "car")].best_time_ms == 90000


@pytest.mark.asyncio
async def test_submission_repeated_queued_task_never_duplicates_acknowledged_post():
    from src.core.api_client import SubmissionResult
    from src.ui.pages.history import HistoryEntry
    service = LapSubmissionService()
    api = SimpleNamespace(submit_lap=AsyncMock(return_value=SubmissionResult(SubmissionStatus.SUCCESS, "ok")))
    session, lap = SessionData(track="Laguna Seca", car="Ferrari F2004"), _lap("VALID")
    entry = HistoryEntry(session.track, session.car, lap.lap_time_ms, lap.timestamp, False, True)
    deps = dict(api_client=api, config=AppConfig(), card=MagicMock(), session=session,
                lap=lap, history_entry=entry, pb_was_new=True, post_to_discord=AsyncMock())
    await service.submit_lap(**deps)
    await service.submit_lap(**deps)
    api.submit_lap.assert_awaited_once()
    deps["post_to_discord"].assert_awaited_once()


@pytest.mark.asyncio
async def test_queued_invalid_opt_in_passes_false_pb_flag_to_discord_callback():
    from src.core.api_client import SubmissionResult, SubmittedLapSnapshot
    from src.ui.pages.history import HistoryEntry
    lap = _lap("INVALID_GAME")
    result = SubmissionResult(SubmissionStatus.SUCCESS, "ok",
                              submitted_snapshot=SubmittedLapSnapshot(False, lap.lap_time_ms))
    discord = AsyncMock()
    await LapSubmissionService().submit_lap(
        api_client=SimpleNamespace(submit_lap=AsyncMock(return_value=result)),
        config=AppConfig(submit_invalid_laps=True), card=MagicMock(),
        session=SessionData(track="Laguna Seca", car="Ferrari F2004"), lap=lap,
        history_entry=HistoryEntry("Laguna Seca", "Ferrari F2004", 90000, lap.timestamp, False, False),
        pb_was_new=True, post_to_discord=discord,
    )
    assert discord.call_args.kwargs["pb_was_new"] is False


@pytest.mark.asyncio
async def test_evicted_card_is_not_retained_and_late_valid_result_still_submits():
    import gc
    from weakref import ref

    from src.core.api_client import SubmissionResult
    from src.ui.pages.home import HomePage

    app = _app()
    app._home_page = HomePage(config=app._config)
    session = SessionData(track="Laguna Seca", car="Ferrari F2004")
    first = _lap()
    await app._on_lap_complete(session, first)
    card_ref = ref(app._home_page._lap_cards[0])
    # Retain model ownership as the parser does, but evict the first UI card.
    laps = [first]
    for _ in range(app._home_page.MAX_VISIBLE_LAPS):
        lap = _lap()
        laps.append(lap)
        await app._on_lap_complete(session, lap)
    gc.collect()
    assert card_ref() is None
    _verdict(first, True)
    await app._on_lap_update(session, first)
    queued = app.page.run_task.call_args.args
    assert queued[1] is None
    assert app._history_entries[0].was_valid
    app._api_client = SimpleNamespace(submit_lap=AsyncMock(
        return_value=SubmissionResult(SubmissionStatus.SUCCESS, "ok")))
    app._post_to_discord = AsyncMock()
    await app._submit_lap(*queued[1:])
    assert app._history_entries[0].was_submitted
    assert len(app._home_page._lap_cards) == app._home_page.MAX_VISIBLE_LAPS


@pytest.mark.asyncio
async def test_result_callbacks_bind_then_reconcile_owned_capture_boundary():
    app = _app()
    session, lap = SessionData(track="Laguna Seca", car="Ferrari F2004"), _lap()
    capture = app._telemetry_capture
    await app._on_lap_complete(session, lap)
    capture.bind_lap_boundary.assert_called_once_with(session.session_id, lap)
    capture.reconcile_lap_boundary.assert_not_called()
    lap.lap_number = 2
    _verdict(lap, True)
    await app._on_lap_update(session, lap)
    capture.reconcile_lap_boundary.assert_called_once_with(session.session_id, lap)
    capture.record_lap_boundary.assert_called_once_with(90000, 1, "UNVERIFIED")
    capture.owns_session.return_value = False
    await app._on_lap_update(session, lap)
    assert capture.reconcile_lap_boundary.call_count == 1


@pytest.mark.asyncio
async def test_legacy_capture_without_boundary_binding_still_reconciles_ui():
    app = _app()
    capture = SimpleNamespace(is_capturing=lambda: True, record_lap_boundary=MagicMock())
    app._telemetry_capture = capture
    session, lap = SessionData(track="Laguna Seca", car="Ferrari F2004"), _lap()
    await app._on_lap_complete(session, lap)
    _verdict(lap, True)
    await app._on_lap_update(session, lap)
    capture.record_lap_boundary.assert_called_once()
    assert app._history_entries[0].was_valid
