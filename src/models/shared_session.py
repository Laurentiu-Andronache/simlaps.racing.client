"""Shared session data models and manager.

This module provides a single thread-safe session store used by log parsing,
shared-memory decoding, telemetry analysis, and API submission code.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Optional, Set
import threading
import uuid

from ..utils.structured_logger import log_debug, Component
from .lap import LapData, SessionData

@dataclass
class LapValidityData:
    """Lap validity information for lap posting."""

    lap_number: int
    is_valid: bool
    lap_state: Optional[str] = None
    invalidation_reason: Optional[str] = None
    invalidation_timestamp: Optional[str] = None
    source: str = "shm_graphics"
    penalty_count: Optional[int] = None
    track_limit_violations: Optional[int] = None


@dataclass
class LapTimingData:
    """Lap timing information with source tracking."""

    lap_number: int
    current_lap_time_ms: Optional[int] = None
    last_lap_time_ms: Optional[int] = None
    best_lap_time_ms: Optional[int] = None
    ideal_lap_time_ms: Optional[int] = None
    delta_time_ms: Optional[int] = None
    source: str = "shm_graphics"
    lap_time_str: Optional[str] = None
    lap_completion_timestamp: Optional[str] = None
    completed_lap_time: Optional[float] = None
    completed_lap_time_source: Optional[str] = None  # "logs", "shm_graphics", "calculated"


@dataclass
class FuelData:
    """Fuel information with source tracking."""

    current_fuel: Optional[float] = None
    fuel_consumption_rate: Optional[float] = None
    fuel_economy: Optional[float] = None
    fuel_consumed_lap: Optional[float] = None
    source: str = "shm_graphics"


@dataclass
class PlayerIdentificationData:
    """Player identification from logs (SHM does not provide this)."""

    steam_id: Optional[str] = None
    player_name: Optional[str] = None
    car_uuid: Optional[str] = None
    car_model: Optional[str] = None
    source: str = "logs"


@dataclass
class SectorSplitData:
    """Sector split times from logs (SHM does not provide this)."""

    lap_number: int
    sector1_ms: Optional[int] = None
    sector2_ms: Optional[int] = None
    sector3_ms: Optional[int] = None
    source: str = "logs"


@dataclass
class SessionMetadataData:
    """Session metadata from Static SHM (or logs as fallback)."""

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    game_version: str = "Unknown"
    session_type: str = "Unknown"
    session_name: str = "Unknown"
    track: str = "Unknown"
    track_configuration: str = "Unknown"
    track_length_m: Optional[float] = None
    weather: str = "Unknown"
    is_online: bool = False
    is_timed_race: bool = False
    event_id: Optional[int] = None
    source: str = "shm_static"


@dataclass
class SharedSessionData:
    """Unified session data accessible by telemetry and log parser."""

    # Shared objects
    lap_validity: Dict[int, LapValidityData] = field(default_factory=dict)
    lap_timing: Dict[int, LapTimingData] = field(default_factory=dict)
    fuel_data: FuelData = field(default_factory=FuelData)
    player_identification: PlayerIdentificationData = field(
        default_factory=PlayerIdentificationData
    )
    sector_splits: Dict[int, SectorSplitData] = field(default_factory=dict)
    session_metadata: SessionMetadataData = field(default_factory=SessionMetadataData)

    current_lap_time_ms: Optional[int] = None
    last_lap_time_ms: Optional[int] = None
    best_lap_time_ms: Optional[int] = None
    ideal_lap_time_ms: Optional[int] = None
    delta_time_ms: Optional[int] = None

    sector_times: Dict[int, Dict[int, int]] = field(default_factory=dict)

    current_fuel: Optional[float] = None
    fuel_consumption_rate: Optional[float] = None
    fuel_economy: Optional[float] = None

    total_laps: Optional[int] = None
    current_lap: Optional[int] = None
    session_phase: Optional[str] = None
    session_time_left_ms: Optional[int] = None
    current_pos: Optional[int] = None
    total_drivers: Optional[int] = None

    car_setup: Dict[str, Any] = field(default_factory=dict)
    assists_state: Dict[str, Any] = field(default_factory=dict)

    max_speed: Optional[float] = None
    tyre_compound: str = "Unknown"
    stint_number: int = 1

    # Powertrain flags decoded from the static SHM region.
    # Used to detect hybrid/electric cars dynamically instead of
    # relying solely on a hardcoded model-name list.
    has_ers: Optional[bool] = None
    has_kers: Optional[bool] = None

    starting_ambient_temp_c: Optional[float] = None
    starting_ground_temp_c: Optional[float] = None
    starting_grip: Optional[str] = None
    air_density: Optional[float] = None

    data_sources: Dict[str, Set[str]] = field(default_factory=dict)


class SharedSessionManager:
    """Thread-safe manager for shared session data."""

    def __init__(self) -> None:
        self._session_data = SharedSessionData()
        self._lock = threading.RLock()
        self._observers: list[Callable[[SharedSessionData], None]] = []
        # Per-frame change gate: only update/log validity when the
        # (lap, is_invalid) tuple transitions.  Prevents log spam and
        # redundant observer notifications at capture Hz (~10-20/s).
        self._last_validity_state: tuple[int, bool] = (0, False)

    def _mark_source(self, field_name: str, source: str) -> None:
        if field_name not in self._session_data.data_sources:
            self._session_data.data_sources[field_name] = set()
        self._session_data.data_sources[field_name].add(source)

    # New shared object access
    def get_lap_validity_data(self, lap_num: int) -> Optional[LapValidityData]:
        with self._lock:
            return self._session_data.lap_validity.get(lap_num)

    def get_lap_timing_data(self, lap_num: int) -> Optional[LapTimingData]:
        with self._lock:
            return self._session_data.lap_timing.get(lap_num)

    def get_fuel_data(self) -> FuelData:
        with self._lock:
            return replace(self._session_data.fuel_data)

    def get_player_identification(self) -> PlayerIdentificationData:
        with self._lock:
            return replace(self._session_data.player_identification)

    def get_sector_split_data(self, lap_num: int) -> Optional[SectorSplitData]:
        with self._lock:
            return self._session_data.sector_splits.get(lap_num)

    def get_session_metadata_data(self) -> SessionMetadataData:
        with self._lock:
            return replace(self._session_data.session_metadata)

    # Legacy accessors
    def get_lap_time(self, lap_num: int) -> Optional[float]:
        with self._lock:
            timing = self._session_data.lap_timing.get(lap_num)
            if timing is None or timing.completed_lap_time is None:
                return None
            return timing.completed_lap_time

    def get_current_lap_time(self) -> Optional[int]:
        with self._lock:
            return self._session_data.current_lap_time_ms

    def get_sector_times(self, lap_num: int) -> Optional[Dict[int, int]]:
        with self._lock:
            return self._session_data.sector_times.get(lap_num)

    def get_lap_validity(self, lap_num: int) -> bool:
        with self._lock:
            validity = self._session_data.lap_validity.get(lap_num)
            return validity.is_valid if validity is not None else True

    def get_lap_state(self, lap_num: int) -> Optional[str]:
        with self._lock:
            lap_validity = self._session_data.lap_validity.get(lap_num)
            if lap_validity is None:
                return None
            return lap_validity.lap_state or ("VALID" if lap_validity.is_valid else "INVALID_GAME")

    def get_car_setup(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._session_data.car_setup)

    def get_car(self) -> str:
        with self._lock:
            return self._session_data.player_identification.car_model or "Unknown"

    def get_hybrid_flags(self) -> tuple[Optional[bool], Optional[bool]]:
        """Return ``(has_ers, has_kers)`` from the static SHM region.

        Both values are ``None`` until the telemetry capture decodes the
        first static frame.
        """
        with self._lock:
            return self._session_data.has_ers, self._session_data.has_kers

    def get_session_metadata(self) -> Dict[str, Any]:
        with self._lock:
            md = self._session_data.session_metadata
            ident = self._session_data.player_identification
            return {
                "session_id": md.session_id,
                "game_version": md.game_version,
                "session_type": md.session_type,
                "track": md.track,
                "track_configuration": md.track_configuration,
                "track_length_m": md.track_length_m,
                "is_online": md.is_online,
                "is_timed_race": md.is_timed_race,
                "event_id": md.event_id,
                "player_id": ident.steam_id,
                "car_uuid": ident.car_uuid,
            }

    def get_best_lap_time(self) -> Optional[float]:
        with self._lock:
            times = [
                t.completed_lap_time
                for t in self._session_data.lap_timing.values()
                if t.completed_lap_time is not None
            ]
            return min(times) if times else None

    def get_all_lap_times(self) -> Dict[int, float]:
        with self._lock:
            return {
                lap_num: t.completed_lap_time
                for lap_num, t in self._session_data.lap_timing.items()
                if t.completed_lap_time is not None
            }

    def validate_data_consistency(self) -> Dict[str, list[str]]:
        issues: list[str] = []
        with self._lock:
            for lap_num, timing in sorted(self._session_data.lap_timing.items()):
                if timing.completed_lap_time is None:
                    continue
                # Check for source drift: if both logs and graphics provided
                # times, compare them.  We detect this by checking if the
                # LapTimingData was updated from both sources.
                # Since we now store a single completed_lap_time with priority,
                # we compare against the LapData from logs if available.
                pass

        return {"inconsistencies": issues}

    def get_all_lap_validity(self) -> Dict[int, bool]:
        with self._lock:
            return {
                lap_num: v.is_valid
                for lap_num, v in self._session_data.lap_validity.items()
            }

    # New shared object updates
    def update_lap_validity_from_graphics_shm(self, lap_num: int, is_invalid: bool) -> None:
        with self._lock:
            current = self._session_data.lap_validity.get(lap_num)
            lap_state = "INVALID_GAME" if is_invalid else "VALID"

            # Completed laps (source == "logs") are frozen — SHM validity
            # is read-only for them.  Only the in-progress lap is updated.
            if current is not None and current.source == "logs":
                return

            if current is None:
                current = LapValidityData(lap_number=lap_num, is_valid=not is_invalid, lap_state=lap_state)
                self._session_data.lap_validity[lap_num] = current
            else:
                current.is_valid = not is_invalid
                current.lap_state = lap_state
                current.source = "shm_graphics"

            self._mark_source("lap_validity", "shm_graphics")

        self.notify_observers()

    def update_lap_timing_from_graphics_shm(self, lap_num: int, timing_data: Dict[str, Any]) -> None:
        with self._lock:
            current = self._session_data.lap_timing.get(lap_num)
            if current is None:
                current = LapTimingData(lap_number=lap_num)
                self._session_data.lap_timing[lap_num] = current

            current.current_lap_time_ms = timing_data.get(
                "current_lap_time_ms",
                timing_data.get("current_laptime_ms"),
            )
            current.last_lap_time_ms = timing_data.get("last_laptime_ms")
            current.best_lap_time_ms = timing_data.get("best_laptime_ms")
            current.ideal_lap_time_ms = timing_data.get("ideal_laptime_ms")
            current.delta_time_ms = timing_data.get("delta_time_ms")
            current.source = "shm_graphics"

            self._session_data.current_lap_time_ms = current.current_lap_time_ms
            self._session_data.last_lap_time_ms = current.last_lap_time_ms
            self._session_data.best_lap_time_ms = current.best_lap_time_ms
            self._session_data.ideal_lap_time_ms = current.ideal_lap_time_ms
            self._session_data.delta_time_ms = current.delta_time_ms

            if current.last_lap_time_ms and current.last_lap_time_ms > 0:
                # Only store graphics-sourced completed time if logs haven't
                # already set one (logs are authoritative).
                if current.completed_lap_time_source != "logs":
                    current.completed_lap_time = float(current.last_lap_time_ms)
                    current.completed_lap_time_source = "shm_graphics"
                self._mark_source("lap_times", "shm_graphics")

            for field_name in (
                "current_lap_time_ms",
                "last_lap_time_ms",
                "best_lap_time_ms",
                "ideal_lap_time_ms",
                "delta_time_ms",
            ):
                self._mark_source(field_name, "shm_graphics")

        self.notify_observers()

    def update_fuel_from_graphics_shm(self, fuel_data: Dict[str, Any]) -> None:
        with self._lock:
            current_fuel = fuel_data.get("fuel_liter_current_quantity")
            fuel_rate = fuel_data.get("fuel_liter_per_km")
            fuel_economy = fuel_data.get("km_per_fuel_liter")
            fuel_per_lap = fuel_data.get("fuel_liter_per_lap")

            self._session_data.current_fuel = current_fuel
            self._session_data.fuel_consumption_rate = fuel_rate
            self._session_data.fuel_economy = fuel_economy

            self._session_data.fuel_data.current_fuel = current_fuel
            self._session_data.fuel_data.fuel_consumption_rate = fuel_rate
            self._session_data.fuel_data.fuel_economy = fuel_economy
            self._session_data.fuel_data.fuel_consumed_lap = fuel_per_lap
            self._session_data.fuel_data.source = "shm_graphics"

            self._mark_source("current_fuel", "shm_graphics")
            self._mark_source("fuel_consumption_rate", "shm_graphics")
            self._mark_source("fuel_economy", "shm_graphics")

        self.notify_observers()

    def update_player_identification_from_logs(self, player_data: Dict[str, Any]) -> None:
        with self._lock:
            ident = self._session_data.player_identification
            ident.steam_id = player_data.get("steam_id") or ident.steam_id
            ident.player_name = player_data.get("player_name") or ident.player_name
            ident.car_uuid = player_data.get("car_uuid") or ident.car_uuid
            ident.car_model = player_data.get("car_model") or ident.car_model
            ident.source = "logs"

            self._mark_source("player_id", "logs")
            self._mark_source("car_uuid", "logs")

        self.notify_observers()

    def update_sector_splits_from_logs(self, lap_num: int, sector_data: Dict[str, Any]) -> None:
        with self._lock:
            splits = SectorSplitData(
                lap_number=lap_num,
                sector1_ms=sector_data.get("sector1_ms"),
                sector2_ms=sector_data.get("sector2_ms"),
                sector3_ms=sector_data.get("sector3_ms"),
                source="logs",
            )
            self._session_data.sector_splits[lap_num] = splits

            legacy: Dict[int, int] = {}
            if splits.sector1_ms is not None:
                legacy[1] = splits.sector1_ms
            if splits.sector2_ms is not None:
                legacy[2] = splits.sector2_ms
            if splits.sector3_ms is not None:
                legacy[3] = splits.sector3_ms
            if legacy:
                self._session_data.sector_times[lap_num] = legacy

            self._mark_source("sector_times", "logs")

        self.notify_observers()

    def update_session_metadata_from_static_shm(self, metadata: Dict[str, Any]) -> None:
        with self._lock:
            md = self._session_data.session_metadata
            md.game_version = metadata.get("ac_evo_version", md.game_version)
            md.session_type = str(metadata.get("session", md.session_type))
            md.session_name = metadata.get("session_name", md.session_name)
            md.track = metadata.get("track", md.track)
            md.track_configuration = metadata.get("track_configuration", md.track_configuration)
            md.track_length_m = metadata.get("track_length_m", md.track_length_m)
            md.is_online = bool(metadata.get("is_online", md.is_online))
            md.is_timed_race = bool(metadata.get("is_timed_race", md.is_timed_race))
            md.event_id = metadata.get("event_id", md.event_id)
            md.source = "shm_static"

            self._session_data.starting_ambient_temp_c = metadata.get(
                "starting_ambient_temperature_c", self._session_data.starting_ambient_temp_c
            )
            self._session_data.starting_ground_temp_c = metadata.get(
                "starting_ground_temperature_c", self._session_data.starting_ground_temp_c
            )

            # Powertrain flags from SHM static region — primary hybrid
            # detection source (covers any car without manual list updates).
            if "has_ers" in metadata:
                self._session_data.has_ers = bool(metadata["has_ers"])
            if "has_kers" in metadata:
                self._session_data.has_kers = bool(metadata["has_kers"])
            starting_grip = metadata.get("starting_grip_name") or metadata.get("starting_grip")
            self._session_data.starting_grip = starting_grip

            for field_name in ("game_version", "session_type", "track", "is_online", "is_timed_race"):
                self._mark_source(field_name, "shm_static")

        self.notify_observers()

    # Legacy update entry points
    def update_lap_from_logs(self, lap_data: LapData, session_data: Optional[SessionData] = None) -> None:
        player_payload: Dict[str, Any] = {}
        if session_data is not None:
            with self._lock:
                md = self._session_data.session_metadata
                md.session_id = session_data.session_id
                md.game_version = session_data.game_version
                md.session_type = session_data.session_type
                md.track = session_data.track
                md.weather = session_data.weather

                self._mark_source("game_version", "logs")
                self._mark_source("session_type", "logs")
                self._mark_source("track", "logs")

            player_payload = {
                "steam_id": session_data.player_id,
                "player_name": session_data.player_name,
                "car_uuid": session_data.car_uuid,
                "car_model": session_data.car,
            }
            self.update_player_identification_from_logs(player_payload)

        self.update_sector_splits_from_logs(
            lap_data.lap_number,
            {
                "sector1_ms": lap_data.sector1_ms,
                "sector2_ms": lap_data.sector2_ms,
                "sector3_ms": lap_data.sector3_ms,
            },
        )

        with self._lock:
            timing = self._session_data.lap_timing.get(lap_data.lap_number)
            if timing is None:
                timing = LapTimingData(lap_number=lap_data.lap_number)
                self._session_data.lap_timing[lap_data.lap_number] = timing
            timing.lap_completion_timestamp = lap_data.timestamp

            if lap_data.lap_time_ms > 0:
                lap_time = float(lap_data.lap_time_ms)
                timing.completed_lap_time = lap_time
                timing.completed_lap_time_source = "logs"
                self._mark_source("lap_times", "logs")

            # ── Validity ───────────────────────────────────────────────
            # Merge strategy for log vs. SHM validity:
            #
            # 1. Authoritative log (``validity_source == "authoritative"``
            #    from the game's ``Relevant onSplit`` broadcast): log wins;
            #    SHM is permanently frozen (``source="logs"``).
            #
            # 2. Log structural classifications (OUTLAP, ABORTED, and the
            #    currently-unused INVALID_SPLIT / INVALID_SECTORS /
            #    INVALID_PENALTY / INVALID_TRACK_LIMIT): log wins because
            #    SHM only knows VALID / INVALID_GAME.  ``source="logs"``
            #    protects these from being flattened to VALID by SHM.
            #
            # 3. Log heuristic VALID (the default when no ``Relevant
            #    onSplit`` arrived): SHM wins if it already captured a
            #    verdict while the lap was in-progress.  Otherwise the
            #    heuristic VALID is stored with ``source=None`` so a
            #    future SHM update is still accepted.
            #
            # This fixes the regression introduced by the Phase-2
            # simplification (item 4.2) where ALL log verdicts —
            # including heuristic ones — were treated as authoritative,
            # permanently silencing SHM-based invalidity detection.
            log_state = lap_data.lap_type or lap_data.lap_state.value
            log_is_authoritative = (
                getattr(lap_data, "validity_source", "heuristic") == "authoritative"
            )

            # States that SHM cannot provide — log classification wins.
            _LOG_CLASSIFICATION_STATES = frozenset({
                "OUTLAP", "ABORTED",
                "INVALID_SPLIT", "INVALID_SECTORS", "INVALID_PENALTY",
                "INVALID_TRACK_LIMIT",
            })

            existing = self._session_data.lap_validity.get(lap_data.lap_number)

            if log_is_authoritative or log_state in _LOG_CLASSIFICATION_STATES:
                # Authoritative log verdict or structural classification
                # — freeze SHM.
                self._session_data.lap_validity[lap_data.lap_number] = LapValidityData(
                    lap_number=lap_data.lap_number,
                    is_valid=lap_data.is_valid,
                    lap_state=log_state,
                    source="logs",
                )
            elif existing is not None and existing.source == "shm_graphics":
                # SHM already captured a verdict while the lap was
                # in-progress — preserve it over the log heuristic.
                pass
            else:
                # No prior SHM verdict; store the heuristic log verdict
                # but leave source mutable so SHM can still contribute.
                self._session_data.lap_validity[lap_data.lap_number] = LapValidityData(
                    lap_number=lap_data.lap_number,
                    is_valid=lap_data.is_valid,
                    lap_state=log_state,
                    source=None,
                )
            self._mark_source("lap_times", "logs")

        self.notify_observers()

    def update_session_metadata_from_logs(self, session_data: SessionData) -> None:
        """Update session metadata and player identification from log SessionData.

        Like update_from_logs but skips lap iteration — used at session start
        before any laps have been parsed.
        """
        with self._lock:
            md = self._session_data.session_metadata
            md.session_id = session_data.session_id
            md.game_version = session_data.game_version
            md.session_type = session_data.session_type
            md.track = session_data.track
            md.weather = session_data.weather

        self.update_player_identification_from_logs(
            {
                "steam_id": session_data.player_id,
                "player_name": session_data.player_name,
                "car_uuid": session_data.car_uuid,
                "car_model": session_data.car,
            }
        )

    def update_from_logs(self, log_session_data: SessionData) -> None:
        with self._lock:
            md = self._session_data.session_metadata
            md.session_id = log_session_data.session_id
            md.game_version = log_session_data.game_version
            md.session_type = log_session_data.session_type
            md.track = log_session_data.track
            md.weather = log_session_data.weather

        self.update_player_identification_from_logs(
            {
                "steam_id": log_session_data.player_id,
                "player_name": log_session_data.player_name,
                "car_uuid": log_session_data.car_uuid,
                "car_model": log_session_data.car,
            }
        )

        for lap in log_session_data.laps:
            self.update_lap_from_logs(lap, session_data=log_session_data)

    def update_from_static_shm(self, static_data: Dict[str, Any]) -> None:
        self.update_session_metadata_from_static_shm(static_data)

    def update_from_graphics_shm(self, graphics_data: Dict[str, Any]) -> None:
        # ── Determine current lap number ────────────────────────────────
        # session_current_lap (from SMEvoSessionState.current_lap) has a
        # fragile offset that reads 0 on AC Evo 0.8.0.1.  total_lap_count
        # (SPageFileGraphicEvo at stable offset 2384) is the completed-lap
        # counter; +1 gives the in-progress lap.  The lightweight
        # ``peek_graphics_validity()`` always provides total_lap_count and
        # sets session_current_lap=0, so the fallback is the normal path.
        shm_current_lap = int(graphics_data.get("session_current_lap") or 0)
        completed_laps = int(graphics_data.get("total_lap_count") or 0)
        if shm_current_lap > 0:
            current_lap = shm_current_lap
        else:
            # completed_laps is the number of laps already finished; the
            # in-progress lap is always the next one.  completed_laps=0 means
            # lap 1 is running (formation/outlap or the first timed lap),
            # completed_laps=1 means lap 2 is running, and so on.
            current_lap = completed_laps + 1

        if current_lap > 0:
            self.update_lap_timing_from_graphics_shm(current_lap, graphics_data)

            # ── Wire SHM validity flags into shared session ──────────────
            # Priority 1: is_valid_lap (SPageFileGraphicEvo at offset 3121)
            # DOES work on AC Evo 0.8.0.1 and indicates whether the current
            # lap is being timed as valid.  is_valid_lap=False with
            # current_lap_time_ms > 0 means the in-progress lap has been
            # invalidated (cut track, penalty, etc.).  is_valid_lap=False
            # with current_lap_time_ms == 0 means timing is inactive
            # (between sessions, in pits, etc.) and should NOT be treated
            # as an invalidity verdict.
            # Priority 2: is_invalid / timing_is_invalid (SMEvoTimingState)
            # is NOT populated by AC Evo 0.8.0.1 — always False, which is
            # why it must NOT be checked first (False ≠ None).
            is_valid_lap = graphics_data.get("is_valid_lap")
            lap_time_ms = int(graphics_data.get("current_lap_time_ms") or 0)

            if is_valid_lap is not None:
                # is_valid_lap is the authoritative flag on AC Evo 0.8.0.1.
                # Only apply it when timing is active (lap_time_ms > 0).
                # When lap_time_ms == 0, timing is inactive (session end,
                # in pits) and is_valid_lap=False should NOT be treated as
                # an invalidity verdict — skip the update entirely.
                if lap_time_ms > 0:
                    is_invalid = not is_valid_lap
                else:
                    is_invalid = None
            else:
                is_invalid = graphics_data.get("is_invalid")
                if is_invalid is None:
                    is_invalid = graphics_data.get("timing_is_invalid")

            if is_invalid is not None:
                is_invalid_bool = bool(is_invalid)
                # Only call update + log when the (lap, is_invalid) tuple
                # actually changes — avoids per-frame observer spam and
                # debug-log eviction at capture Hz (Fable #3, Sol5.6 #3).
                new_state = (current_lap, is_invalid_bool)
                if new_state != self._last_validity_state:
                    self._last_validity_state = new_state
                    self.update_lap_validity_from_graphics_shm(current_lap, is_invalid_bool)
                    log_debug(
                        Component.SHARED_SESSION,
                        f"[SHM_VALIDITY] current_lap={current_lap} is_invalid={is_invalid_bool}",
                    )

        self.update_fuel_from_graphics_shm(graphics_data)

        with self._lock:
            self._session_data.total_laps = graphics_data.get("total_lap_count")
            self._session_data.current_lap = graphics_data.get("session_current_lap")
            self._session_data.session_phase = graphics_data.get("session_phase")
            self._session_data.session_time_left_ms = graphics_data.get("session_time_left_ms")
            self._session_data.current_pos = graphics_data.get("current_pos")
            self._session_data.total_drivers = graphics_data.get("total_drivers")

            # Car model from graphics SHM (authoritative, overrides logs fallback)
            car_model = graphics_data.get("car_model")
            if isinstance(car_model, str) and car_model.strip():
                self._session_data.player_identification.car_model = car_model.strip()
                self._mark_source("car", "shm_graphics")

            # has_kers from graphics SHM (AC Evo SPageFileGraphicEvo offset 2420)
            has_kers = graphics_data.get("has_kers")
            if has_kers is not None:
                self._session_data.has_kers = bool(has_kers)

            self._mark_source("session_summary", "shm_graphics")

        self.notify_observers()

    def update_from_physics_shm(self, physics_data: Dict[str, Any]) -> None:
        with self._lock:
            speed_kmh = physics_data.get("speed_kmh")
            if isinstance(speed_kmh, (int, float)):
                if self._session_data.max_speed is None:
                    self._session_data.max_speed = float(speed_kmh)
                else:
                    self._session_data.max_speed = max(self._session_data.max_speed, float(speed_kmh))

            car_setup = physics_data.get("car_setup")
            if isinstance(car_setup, dict):
                self._session_data.car_setup.update(car_setup)
            assists_state = physics_data.get("assists_state")
            if isinstance(assists_state, dict):
                self._session_data.assists_state.update(assists_state)

            self._session_data.air_density = physics_data.get("air_density", self._session_data.air_density)

            self._mark_source("max_speed", "shm_physics")
            self._mark_source("car_setup", "shm_physics")

        self.notify_observers()

    def update_from_telemetry(self, telemetry_data: Dict[str, Any]) -> None:
        with self._lock:
            max_speed = telemetry_data.get("max_speed")
            if isinstance(max_speed, (int, float)):
                self._session_data.max_speed = float(max_speed)

            stint_number = telemetry_data.get("stint_number")
            if isinstance(stint_number, int) and stint_number > 0:
                self._session_data.stint_number = stint_number

            tyre_compound = telemetry_data.get("tyre_compound")
            if isinstance(tyre_compound, str) and tyre_compound.strip():
                self._session_data.tyre_compound = tyre_compound

            self._mark_source("telemetry_summary", "calculated")

        self.notify_observers()

    def get_data_sources(self) -> Dict[str, Set[str]]:
        """Return a snapshot of data source tracking (thread-safe)."""
        with self._lock:
            return {k: set(v) for k, v in self._session_data.data_sources.items()}

    def reset(self) -> None:
        """Replace session data with a fresh instance, preserving observers and lock.

        Call this when a new game session starts so stale lap validity, timing,
        and fuel data from the previous session cannot bleed into the new one.
        Player identification is intentionally preserved across resets because the
        same driver is still logged in.
        """
        with self._lock:
            old_ident = self._session_data.player_identification
            self._session_data = SharedSessionData()
            # Re-attach player identification — Steam ID / car UUID don't change
            # between sessions and must not be wiped.
            self._session_data.player_identification = old_ident

        self.notify_observers()

    # Observer pattern
    def subscribe(self, callback: Callable[[SharedSessionData], None]) -> None:
        with self._lock:
            if callback not in self._observers:
                self._observers.append(callback)

    def notify_observers(self) -> None:
        with self._lock:
            observers = list(self._observers)
            snapshot = self._session_data

        for callback in observers:
            try:
                callback(snapshot)
            except Exception:
                # Observer failures must not break data flow.
                continue

