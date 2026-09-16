"""Lap submission lifecycle service extracted from SimLapsApp.

Owns lap submit result mapping and optional Discord post flow.
"""

from typing import Awaitable, Callable, Optional

from src.core.api_client import APIClient, SubmissionStatus, SubmittedLapSnapshot
from src.core.discord_notifier import DiscordLapPayload, DiscordNotifier
from src.models import LapData, SessionData
from src.utils.config import AppConfig
from src.utils.structured_logger import (
    Component,
    log_debug,
    log_error,
    log_exception,
    log_info,
    log_warning,
)

from ..components.lap_card import LapCard, LapCardStatus
from ..pages.history import HistoryEntry


def _update_card(card: Optional[LapCard], *args) -> None:
    """An evicted recent-lap card does not prevent its history reconciliation."""
    if card is not None:
        card.update_status(*args)


class LapSubmissionService:
    """Encapsulates lap submission + Discord posting orchestration."""

    async def submit_lap(
        self, *, history_entry: HistoryEntry, **kwargs,
    ) -> None:
        """Serialize attempts for a result; success is never submitted twice."""
        if history_entry.was_submitted or getattr(history_entry, "submission_in_flight", False):
            return
        history_entry.submission_in_flight = True
        try:
            await self._submit_lap(history_entry=history_entry, **kwargs)
        finally:
            history_entry.submission_in_flight = False
            history_entry.submission_pending = False

    async def _submit_lap(
        self,
        *,
        api_client: APIClient,
        config: AppConfig,
        card: Optional[LapCard],
        session: SessionData,
        lap: LapData,
        history_entry: HistoryEntry,
        pb_was_new: Optional[bool],
        post_to_discord: Callable[..., Awaitable[None]],
    ) -> None:
        """Submit a lap and update UI card state from result."""
        # A queued UI task can run after its lap has been reclassified. Never
        # rely on the eligibility decision made when the task was scheduled.
        if getattr(lap, "is_unverified", False):
            _update_card(card, LapCardStatus.UNVERIFIED)
            return
        if getattr(lap, "lap_type", None) == "OUTLAP":
            return
        if not lap.is_valid and not config.submit_invalid_laps:
            _update_card(card, LapCardStatus.INVALID, "Lap invalidated before submission")
            return
        history_entry.submission_attempted = True
        initial_valid = lap.is_valid
        initial_time = getattr(lap, "lap_time_ms", None)
        log_info(
            Component.APP,
            "Starting lap submission",
            lap_time=lap.lap_time_str,
            track=session.track,
            lap_valid=lap.is_valid,
            submit_invalid=config.submit_invalid_laps,
            server_url=config.server_url,
        )

        _update_card(card, LapCardStatus.SUBMITTING)

        try:
            log_debug(Component.APP, "Sending lap submission request")
            result = await api_client.submit_lap(
                session=session,
                lap=lap,
                submit_invalid=config.submit_invalid_laps,
            )
            log_debug(Component.APP, "Lap submission response received", status=getattr(result, "status", None))
        except Exception as exc:
            log_exception(Component.APP, "Submit error", exc)
            _update_card(card, LapCardStatus.FAILED, f"Submit error: {str(exc)}")
            return

        if result is None:
            log_error(Component.APP, "No response from server")
            _update_card(card, LapCardStatus.FAILED, "No response from server")
            return

        if result.status == SubmissionStatus.SUCCESS:
            log_info(Component.APP, "Lap submitted successfully", lap_time=lap.lap_time_str, track=session.track)
            history_entry.was_submitted = True
            snapshot = getattr(result, "submitted_snapshot", None)
            # Legacy API implementations do not return a sent snapshot. Keep
            # their pre-await values rather than recording a later mutation
            # as if it had already been accepted by the server.
            history_entry.submitted_is_valid = (
                snapshot.is_valid if isinstance(snapshot, SubmittedLapSnapshot) else initial_valid
            )
            history_entry.submitted_lap_time_ms = (
                snapshot.lap_time_ms if isinstance(snapshot, SubmittedLapSnapshot) else initial_time
            )
            history_entry.was_valid = lap.is_valid and not getattr(lap, "is_unverified", False)
            history_entry.lap_state = getattr(lap, "lap_type", None)
            history_entry.submission_needs_review = bool(
                getattr(lap, "is_unverified", False)
                or history_entry.submitted_is_valid != lap.is_valid
                or (
                    history_entry.submitted_lap_time_ms is not None
                    and history_entry.submitted_lap_time_ms != getattr(lap, "lap_time_ms", None)
                )
            )
            if history_entry.submission_needs_review:
                _update_card(card, LapCardStatus.REVIEW_REQUIRED)
                return
            _update_card(card, LapCardStatus.SUBMITTED)

            log_debug(Component.APP, "Checking Discord posting eligibility")
            await post_to_discord(
                session,
                lap,
                steam_id=session.player_id,
                steam_name=session.player_name,
                pb_was_new=bool(pb_was_new and lap.is_valid),
            )
            return

        if result.status == SubmissionStatus.UNVERIFIED_LAP or getattr(lap, "is_unverified", False):
            if result.status == SubmissionStatus.UNVERIFIED_LAP:
                history_entry.submission_attempted = False
            _update_card(card, LapCardStatus.UNVERIFIED)
            return

        if not lap.is_valid and not config.submit_invalid_laps:
            if result.status == SubmissionStatus.INVALID_LAP and getattr(result, "submitted_snapshot", None) is None:
                history_entry.submission_attempted = False
            _update_card(card, LapCardStatus.INVALID, result.message)
            return

        if result.status == SubmissionStatus.INVALID_LAP:
            log_warning(Component.APP, "Lap rejected as invalid", server_message=result.message)
            _update_card(card, LapCardStatus.INVALID, result.message)
            return

        if result.status in {
            SubmissionStatus.GAME_NOT_RUNNING,
            SubmissionStatus.SIGNATURE_ERROR,
            SubmissionStatus.RATE_LIMITED,
            SubmissionStatus.PLAUSIBILITY_FAILED,
            SubmissionStatus.NO_SECRET,
            SubmissionStatus.NETWORK_ERROR,
        }:
            log_warning(
                Component.APP,
                "Submission failed",
                status=result.status.value,
                server_message=result.message,
            )
            _update_card(card, LapCardStatus.FAILED, result.message)
            return

        log_error(Component.APP, "Unknown submission error", server_message=result.message)
        card.update_status(LapCardStatus.FAILED, result.message)

    async def post_to_discord(
        self,
        *,
        config: AppConfig,
        discord_notifier: DiscordNotifier,
        session: SessionData,
        lap: LapData,
        steam_id: str,
        steam_name: Optional[str] = None,
        pb_was_new: Optional[bool] = None,
    ) -> None:
        """Post lap to Discord if configured and eligible."""
        if getattr(lap, "is_unverified", False) or getattr(lap, "lap_type", None) == "OUTLAP":
            return
        pb_was_new = bool(pb_was_new and lap.is_valid)
        try:
            log_debug(Component.APP, "Starting Discord post check")

            if not config.discord_enabled:
                log_debug(Component.APP, "Discord disabled in settings")
                return

            if not config.discord_webhook_url or not config.discord_webhook_url.strip():
                log_debug(Component.APP, "No Discord webhook URL configured")
                return

            if not discord_notifier:
                log_warning(Component.APP, "Discord notifier not initialized - skipping post")
                return

            log_debug(Component.APP, "Discord configured, checking PB criteria")

            is_pb = False
            log_debug(Component.APP, "Discord PB-only mode", discord_pb_only=config.discord_pb_only)
            if config.discord_pb_only:
                is_pb = bool(pb_was_new)
                log_debug(Component.APP, "Discord PB check result", is_pb=is_pb)
                if not is_pb:
                    log_debug(Component.APP, "Skipping Discord post: not a personal best")
                    return
            else:
                is_pb = bool(pb_was_new)
                log_debug(Component.APP, "Discord PB check result (non-PB-only mode)", is_pb=is_pb)

            log_debug(Component.APP, "Creating Discord lap data")
            sector_times = None
            if lap.sector1_ms is not None and lap.sector2_ms is not None and lap.sector3_ms is not None:
                sector_times = [lap.sector1_ms, lap.sector2_ms, lap.sector3_ms]

            discord_lap = DiscordLapPayload(
                track_name=session.track,
                car_name=session.car,
                lap_time_ms=lap.lap_time_ms,
                valid=lap.is_valid,
                steam_id=steam_id,
                steam_name=steam_name,
                is_personal_best=is_pb,
                created_at=lap.timestamp,
                sector_times_ms=sector_times,
                fuel_used_liters=lap.fuel_used,
                tire_compound=lap.tyre_compound if lap.tyre_compound != "Unknown" else None,
            )

            log_debug(Component.APP, "Posting lap to Discord webhook")
            success = await discord_notifier.post_lap(discord_lap)
            if success:
                log_info(Component.APP, "Discord post successful", lap_time=lap.lap_time_str, track=session.track)
            else:
                log_warning(Component.APP, "Discord post failed", lap_time=lap.lap_time_str, track=session.track)

        except Exception as exc:
            log_exception(Component.APP, "Error posting to Discord", exc)
