"""Lap processing/presentation service extracted from SimLapsApp.

Owns lap-complete orchestration for telemetry lap boundary recording,
submission eligibility, history/card synchronization, and auto-submit trigger.
"""

from dataclasses import dataclass
from typing import Callable, Optional
from weakref import ReferenceType, ref

from src.core.pb_cache import PBCache
from src.core.telemetry_capture import TelemetryCapture
from src.models import LapData, SessionData, SharedSessionManager
from src.utils.config import AppConfig
from src.utils.structured_logger import (
    Component,
    log_debug,
    log_exception,
)

from ..components.lap_card import LapCardStatus
from ..pages.history import HistoryEntry
from ..pages.home import HomePage


@dataclass
class _Presentation:
    lap_ref: ReferenceType[LapData]
    boundary_seen: bool = False
    _card_ref: object = None
    history: Optional[HistoryEntry] = None

    @property
    def card(self):
        return self._card_ref() if isinstance(self._card_ref, ReferenceType) else self._card_ref

    @card.setter
    def card(self, value):
        try:
            self._card_ref = ref(value)
        except TypeError:
            # Small legacy/test adapters may not support weak references.
            self._card_ref = value


class LapProcessingService:
    """Encapsulates app-side lap completion processing flow."""

    def __init__(self):
        self._presentations: dict[tuple[str, int], _Presentation] = {}

    async def handle_lap_complete(
        self,
        *,
        session: SessionData,
        lap: LapData,
        home_page: HomePage,
        telemetry_capture: Optional[TelemetryCapture],
        config: AppConfig,
        session_manager: SharedSessionManager,
        pb_cache: PBCache,
        history_entries: list[HistoryEntry],
        schedule_submission: Callable[..., None],
        create_history_entry: Callable[..., HistoryEntry],
        record_boundary: bool = True,
    ) -> Optional[str]:
        """Process lap completion, update UI/history, and optionally auto-submit.

        Returns the track name if it was updated (caller should sync
        ``current_track_name``), otherwise ``None``.
        """
        updated_track: Optional[str] = None
        result_key = (session.session_id, id(lap))
        presentation = self._presentations.get(result_key)
        if presentation is None:
            presentation = _Presentation(ref(lap, lambda _ref: self._presentations.pop(result_key, None)))
            self._presentations[result_key] = presentation
        first_boundary = record_boundary and not presentation.boundary_seen
        if record_boundary:
            presentation.boundary_seen = True

        active_session_id = session_manager.get_active_session_id()
        owns_live_context = not isinstance(active_session_id, str) or active_session_id == session.session_id
        if active_session_id is None and isinstance(session_manager, SharedSessionManager):
            owns_live_context = session_manager.get_session_origin().epoch == 0
        # Late outgoing results keep their own cards without replacing the
        # active capture's track/player context.
        if session.player_id and owns_live_context and record_boundary:
            log_debug(Component.APP, "Updating detected user", steam_id=session.player_id)
            home_page.set_detected_user(session.player_id, session.player_name)

        # Return updated track name for telemetry (caller sets it on the app)
        if session.track and session.track != "Unknown" and owns_live_context and record_boundary:
            updated_track = session.track

        # Record lap boundary so the analyzer can use authoritative lap splits.
        # Fuel per lap is owned entirely by the log parser (Physics SHM + spike
        # detection) and is already set on lap.fuel_used before this point.
        capture_owner_check = getattr(telemetry_capture, "owns_session", None)
        capture_ownership = (
            capture_owner_check(session.session_id)
            if callable(capture_owner_check)
            else None
        ) if telemetry_capture is not None else None
        if isinstance(capture_ownership, bool):
            owns_capture_boundary = capture_ownership
        else:
            # Legacy capture implementations do not expose immutable origin
            # ownership; retain the manager-id guard for those embedders.
            owns_capture_boundary = (
                not isinstance(active_session_id, str)
                or active_session_id == session.session_id
            )
        if (
            first_boundary
            and telemetry_capture
            and telemetry_capture.is_capturing()
            and owns_capture_boundary
        ):
            lap_type = getattr(lap, "lap_type", None) or getattr(getattr(lap, "lap_state", None), "value", None)
            telemetry_capture.record_lap_boundary(
                lap.lap_time_ms,
                lap.lap_number,
                lap_type or "UNVERIFIED",
            )
            bind_boundary = getattr(telemetry_capture, "bind_lap_boundary", None)
            if callable(bind_boundary):
                bind_boundary(session.session_id, lap)

        elif first_boundary and config.telemetry_enabled and telemetry_capture:
            # A lap-complete event is too late to begin a useful capture
            # for that lap and can fire during post-session shutdown.
            log_debug(
                Component.APP,
                "Telemetry missed lap boundary; not starting capture from lap-complete",
                lap_number=lap.lap_number,
            )

        if not record_boundary and telemetry_capture and owns_capture_boundary:
            # A delayed verdict may correct the displayed number/time, but
            # its capture marker must retain the same result identity/index.
            reconcile_boundary = getattr(telemetry_capture, "reconcile_lap_boundary", None)
            if callable(reconcile_boundary):
                reconcile_boundary(session.session_id, lap)

        # Structural outlaps own a capture boundary but no result card. Keep
        # the presentation record so a later authoritative upgrade creates one
        # card/history row without recording the boundary a second time.
        if lap.lap_type == "OUTLAP":
            return updated_track

        unverified = lap.is_unverified
        eligible = not unverified and (lap.is_valid or config.submit_invalid_laps)
        known_combo = bool(
            session.track and session.track != "Unknown"
            and session.car and session.car != "Unknown"
        )
        reconcile_pb = getattr(pb_cache, "reconcile_lap_candidate", None)
        pb_was_new = False
        if callable(reconcile_pb):
            pb_was_new = reconcile_pb(
                result_key, session.track, session.car, lap.lap_time_ms,
                eligible=known_combo and lap.is_valid and not unverified,
            )
        elif known_combo and lap.is_valid and not unverified:
            # Older embedders retain their established PB API.
            pb_was_new = pb_cache.check_and_update_pb(session.track, session.car, lap.lap_time_ms)

        history_entry = presentation.history
        if history_entry is not None and not any(history_entry is item for item in history_entries):
            # A deliberately trimmed result must not be resurrected by metadata.
            return updated_track
        should_submit = config.auto_submit and eligible and (
            history_entry is None or not (
                history_entry.was_submitted
                or getattr(history_entry, "submission_pending", False)
                or getattr(history_entry, "submission_attempted", False)
            )
        )
        status = (
            LapCardStatus.UNVERIFIED if unverified else
            LapCardStatus.INVALID if not eligible else
            LapCardStatus.SUBMITTING if should_submit else LapCardStatus.PENDING
        )
        if history_entry is None:
            history_entry = create_history_entry(
                track=session.track, car=session.car, lap_time_ms=lap.lap_time_ms,
                timestamp=lap.timestamp, was_submitted=False,
                was_valid=lap.is_valid and not unverified,
            )
            history_entries.append(history_entry)
            try:
                presentation.card = home_page.add_lap(session, lap, status)
            except Exception as exc:
                history_entries.remove(history_entry)
                log_exception(Component.APP, "Failed to add lap card to home page", exc)
                raise
            presentation.history = history_entry
        else:
            history_entry.lap_time_ms = lap.lap_time_ms
            history_entry.timestamp = lap.timestamp
            history_entry.was_valid = lap.is_valid and not unverified
            if history_entry.was_submitted:
                submitted_valid = getattr(history_entry, "submitted_is_valid", None)
                submitted_time = getattr(history_entry, "submitted_lap_time_ms", None)
                history_entry.submission_needs_review = bool(
                    unverified or
                    (submitted_valid is not None and submitted_valid != lap.is_valid) or
                    (submitted_time is not None and submitted_time != lap.lap_time_ms)
                )
                status = (LapCardStatus.REVIEW_REQUIRED if history_entry.submission_needs_review
                          else LapCardStatus.SUBMITTED)
            elif eligible and not should_submit:
                existing = getattr(getattr(presentation.card, "data", None), "status", None)
                if existing in {LapCardStatus.SUBMITTING, LapCardStatus.FAILED}:
                    status = existing
            if presentation.card is not None:
                presentation.card.update_status(status)
            home_page.refresh_lap(lap)
        history_entry.lap_state = lap.lap_type

        if should_submit:
            history_entry.submission_pending = True
            schedule_submission(
                presentation.card, session, lap, history_entry, bool(pb_was_new),
            )
        return updated_track
