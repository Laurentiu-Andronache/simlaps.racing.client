import re

import os

import sys

import time

import uuid

import asyncio

from datetime import datetime

from dataclasses import dataclass, field

from typing import Optional, Callable, Awaitable

from pathlib import Path


def _get_debug_log_path() -> Path:
    """Get path for debug log file next to the executable/script."""

    if getattr(sys, "frozen", False):
        # Running as compiled executable

        base_path = Path(sys.executable).parent

    else:
        # Running as script - use project root

        base_path = Path(__file__).parent.parent.parent

    return base_path / "simlaps_debug.log"


class DebugLogger:
    """Simple debug logger that writes to a file."""

    # Singleton instance for shared file handle

    _instance = None

    _file = None

    _log_path = None

    _started = False

    def __new__(cls):

        if cls._instance is None:
            cls._instance = super().__new__(cls)

        return cls._instance

    def __init__(self):

        # Only initialize once

        if DebugLogger._log_path is None:
            DebugLogger._log_path = _get_debug_log_path()

        # Auto-start on first use

        if not DebugLogger._started:
            self.start()

    def start(self):
        """Start logging."""

        # Debug logging disabled by default for production

        # Set to True only for internal debugging

        ENABLE_DEBUG = False

        if DebugLogger._started or not ENABLE_DEBUG:
            return

        try:
            DebugLogger._file = open(DebugLogger._log_path, "a", encoding="utf-8")

            DebugLogger._started = True

            self._write_raw(f"\n{'=' * 60}")

            self._write_raw("SimLaps Debug Log Started")

            self._write_raw(f"Log file: {DebugLogger._log_path}")

            self._write_raw(f"{'=' * 60}\n")

        except Exception as e:
            print(f"Failed to open debug log: {e}")

    def _write_raw(self, message: str):
        """Write without checking _started to avoid recursion."""

        if DebugLogger._file:
            try:
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

                DebugLogger._file.write(f"[{timestamp}] {message}\n")

                DebugLogger._file.flush()

            except Exception:
                pass

    def log(self, message: str):
        """Log a message with timestamp."""

        if not DebugLogger._started or not DebugLogger._file:
            return

        try:
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

            DebugLogger._file.write(f"[{timestamp}] {message}\n")

            DebugLogger._file.flush()

        except Exception:
            pass

    def close(self):
        """Close the log file."""

        if DebugLogger._file:
            try:
                DebugLogger._file.close()

                DebugLogger._file = None

                DebugLogger._started = False

            except Exception:
                pass


# Global debug logger

_debug = DebugLogger()


@dataclass
class LapData:
    """Represents a single completed lap."""

    lap_time_ms: int

    lap_time_str: str

    sector1_ms: Optional[int] = None

    sector2_ms: Optional[int] = None

    sector3_ms: Optional[int] = None

    is_valid: bool = True

    lap_type: str = "PUSH"  # PUSH / OUTLAP / INVALID_PUSH - Enhanced classification

    fuel_used: Optional[float] = None

    tyre_compound: str = "Unknown"

    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    distance_km: Optional[float] = None  # Distance covered in this lap


@dataclass
class SessionData:
    """Represents a game session with all its metadata."""

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    game_version: str = "Unknown"

    session_type: str = "Unknown"

    car: str = "Unknown"

    track: str = "Unknown"

    weather: str = "Unknown"

    player_name: Optional[str] = None

    player_id: Optional[str] = None  # Steam ID

    car_uuid: Optional[str] = None

    tyre_compound: str = "Unknown"

    initial_fuel: float = 0.0

    fuel_used_session: float = 0.0

    start_time: str = field(default_factory=lambda: datetime.now().isoformat())

    laps: list[LapData] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert session to dictionary for serialization."""

        return {
            "session_id": self.session_id,
            "game_version": self.game_version,
            "session_type": self.session_type,
            "car": self.car,
            "track": self.track,
            "weather": self.weather,
            "player_name": self.player_name,
            "player_id": self.player_id,
            "car_uuid": self.car_uuid,
            "tyre_compound": self.tyre_compound,
            "initial_fuel": self.initial_fuel,
            "fuel_used_session": self.fuel_used_session,
            "start_time": self.start_time,
            "laps": [
                {
                    "lap_time_ms": lap.lap_time_ms,
                    "lap_time_str": lap.lap_time_str,
                    "sector1_ms": lap.sector1_ms,
                    "sector2_ms": lap.sector2_ms,
                    "sector3_ms": lap.sector3_ms,
                    "is_valid": lap.is_valid,
                    "lap_type": lap.lap_type,  # Enhanced classification
                    "fuel_used": lap.fuel_used,
                    "tyre_compound": lap.tyre_compound,
                    "timestamp": lap.timestamp,
                    "distance_km": lap.distance_km,
                }
                for lap in self.laps
            ],
        }


class LogContext:
    """Maintains context across log parsing for metadata extraction."""

    def __init__(self):

        self.game_version: str = "Unknown"

        self.current_track: str = "Unknown"

        self.current_car: str = "Unknown"

        self.player_name: Optional[str] = None

        self.player_id: Optional[str] = None  # Steam ID

        self.car_uuid: Optional[str] = None

        self.weather: str = "Unknown"

        self.tyre_compound: str = "Unknown"

        self.car_meta: dict[str, dict] = {}  # car_uuid -> {player_name, player_id}

        self.player_car_uuids: set[str] = set()  # All car UUIDs belonging to our player

        self.last_fuel_reading: Optional[float] = (
            None  # Track last fuel reading for per-lap calculation
        )


# Type alias for callbacks

LapCallback = Callable[[SessionData, LapData], Awaitable[None]]

StatusCallback = Callable[[str], Awaitable[None]]

GameStatusCallback = Callable[[bool], Awaitable[None]]

UserDetectedCallback = Callable[
    [str, Optional[str]], Awaitable[None]
]  # steam_id, player_name

GameVersionCallback = Callable[[str], Awaitable[None]]  # game_version


class LogParser:
    """

    Parses ACE game logs to extract lap times and session data.



    Can operate in one-shot parse mode or continuous follow mode.

    """

    # Default log path for ACE

    DEFAULT_LOG_PATH = Path.home() / "Documents" / "ACE" / "log.txt"

    def __init__(
        self,
        log_path: Optional[str] = None,
        on_lap_complete: Optional[LapCallback] = None,
        on_status_change: Optional[StatusCallback] = None,
        on_game_status_change: Optional[GameStatusCallback] = None,
        on_user_detected: Optional[UserDetectedCallback] = None,
        on_game_version: Optional[GameVersionCallback] = None,
    ):

        self.log_path = Path(log_path) if log_path else self.DEFAULT_LOG_PATH

        self.on_lap_complete = on_lap_complete

        self.on_status_change = on_status_change

        self.on_game_status_change = on_game_status_change

        self.on_user_detected = on_user_detected

        self.on_game_version = on_game_version

        self.sessions: list[SessionData] = []

        self.current_session: Optional[SessionData] = None

        self.context = LogContext()

        # In-memory log storage for preservation
        self.log_buffer: list[str] = []
        self.max_log_lines = 100000  # Limit to prevent memory issues

        self._last_activity_ts: Optional[float] = None

        self._running = False

        self._emit_callbacks = False  # Don't emit until initial parsing is done

        self._current_lap_data: dict = {
            "splits": {},  # Changed from list to dict for new split format
            "is_valid": True,
            "fuel_used_lap": 0.0,  # Accumulate fuel during the lap
            "lap_start_time": None,  # Track when current lap started
            "split_end_received": False,  # Track if split end confirmation received
            "unexpected_split": False,  # Track if unexpected split detected
            "physics_lap_num": None,  # Track physics lap counter for validity
        }

        # Compile regex patterns for performance

        self._patterns = {
            "version": re.compile(r"Build release ([^,]+),"),
            "session_type": re.compile(r"Changed to .* GameModeType_([A-Z_]+)"),
            "session_start_alt": re.compile(r"Game Started!\s*GameModeType_([A-Z_]+)"),
            "driver_line": re.compile(r"\tDriver (.+) on car ([\w_]+)"),
            "connecting_gamecar": re.compile(
                r"connecting gamecar ([a-f0-9\-]+) \((.+)\)"
            ),
            "connect": re.compile(
                r"(\S+) connected on car ([\w_]+), with new carId ([a-f0-9\-]+)"
            ),
            "track_name": re.compile(r"TRACK NAME (.+)"),
            "track_load": re.compile(
                r"Loading (?:scene|Scene) .+ content\\tracks\\([^\\]+)"
            ),
            "fuel": re.compile(r"FUEL car ([a-f0-9\-]+) setup with ([\d.]+) L"),
            "compound": re.compile(r"compound: (\d+)"),
            "car_tyre_compound": re.compile(
                r"\[platformCore\] \[info\] CarId: ([a-f0-9\-]+) Tyre: (\d+) compound: (\d+)"
            ),
            "physics_compound": re.compile(
                r"setCompound Tyre: (\d+) compound name: (\S+)"
            ),
            "fuel_consumed": re.compile(
                r"\[([\d\-: .]+)\] \[gameplay\] \[info\] Energy source car ([a-f0-9\-]+) for driver [a-f0-9\-]+ "
                r"hundredmeters done: (\d+) fuel consumed: ([\-\d.]+) L"
            ),
            "split_on": re.compile(
                r"\[([\d\-: .]+)\] \[gameplay\] \[info\] Split completed for car ([a-f0-9\-]+): \((\d+) ms, splitindex (\d+)\) lap:\d+"
            ),
            "split_start": re.compile(
                r"\[gameplay\] \[info\] On Split start (\d+) end (\d+) id (\d+) splittime (\d+)"
            ),
            "split_end": re.compile(
                r"\[([\d\-: .]+)\] \[gameplay\] \[info\] On Split end with all splits, id (\d+)"
            ),
            "physics_lap": re.compile(
                r"\[physics\] \[info\] Lap test evOnLapCompleted (\d+) completed"
            ),
            "lap_finish": re.compile(
                r"\[([\d\-: .]+)\] \[gameplay\] \[info\] New lap carId ([a-f0-9\-]+): ([\d:.]+)"
            ),
            "penalty": re.compile(
                r"\{PENALTY_ADDED_KEY\}|UINotificationType_SessionPenalty"
            ),
            "weather": re.compile(r"GameModeSelectionWeatherBehaviour_([A-Z_]+)"),
            "date": re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"),
        }

    def _parse_lap_time_to_ms(self, time_str: str) -> int:
        """Convert lap time string (e.g., '2:18.456') to milliseconds."""

        parts = time_str.replace(":", ".").split(".")

        if len(parts) == 3:
            # Format: M:SS.mmm

            minutes = int(parts[0])

            seconds = int(parts[1])

            millis = int(parts[2].ljust(3, "0")[:3])

            return (minutes * 60 + seconds) * 1000 + millis

        elif len(parts) == 2:
            # Format: SS.mmm

            seconds = int(parts[0])

            millis = int(parts[1].ljust(3, "0")[:3])

            return seconds * 1000 + millis

        else:
            return 0

    def _add_to_log_buffer(self, line: str):
        """Add a line to the in-memory log buffer with size limit."""
        self.log_buffer.append(line)

        # Prevent memory issues by limiting buffer size
        if len(self.log_buffer) > self.max_log_lines:
            # Remove oldest lines to maintain limit
            excess = len(self.log_buffer) - self.max_log_lines
            self.log_buffer = self.log_buffer[excess:]

    def get_log_buffer(self) -> list[str]:
        """Get a copy of the current log buffer."""
        return self.log_buffer.copy()

    def clear_log_buffer(self):
        """Clear the in-memory log buffer."""
        self.log_buffer.clear()

    def export_logs_to_file(self, file_path: str) -> bool:
        """Export the in-memory log buffer to a file."""
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(self.log_buffer))
            return True
        except Exception as e:
            _debug.log(f"[ERROR] Failed to export logs to {file_path}: {e}")
            return False

    async def _emit_status(self, status: str) -> None:
        """Emit status update to callback."""

        _debug.log(f"[STATUS] {status}")

        if self.on_status_change:
            try:
                await self.on_status_change(status)

            except Exception as e:
                _debug.log(f"[ERROR] _emit_status callback failed: {e}")

    async def _emit_lap(self, session: SessionData, lap: LapData) -> None:
        """Emit completed lap to callback."""

        _debug.log(f"[EMIT_LAP] Calling callback for lap {lap.lap_time_str}")

        _debug.log(f"  Session: track={session.track}, car={session.car}")

        _debug.log(f"  Player: {session.player_name} ({session.player_id})")

        if self.on_lap_complete:
            try:
                await self.on_lap_complete(session, lap)

                _debug.log("[EMIT_LAP] Callback completed successfully")

            except Exception as e:
                _debug.log(f"[ERROR] _emit_lap callback failed: {e}")

                import traceback

                _debug.log(f"[ERROR] Traceback: {traceback.format_exc()}")

    async def _emit_game_status(self, is_running: bool) -> None:
        """Emit game status change to callback."""

        _debug.log(f"[GAME_STATUS] is_running={is_running}")

        if self.on_game_status_change:
            try:
                await self.on_game_status_change(is_running)

            except Exception as e:
                _debug.log(f"[ERROR] _emit_game_status callback failed: {e}")

    async def _emit_user_detected(
        self, steam_id: str, player_name: Optional[str]
    ) -> None:
        """Emit user detection to callback."""

        _debug.log(f"[USER_DETECTED] steam_id={steam_id}, player_name={player_name}")

        if self.on_user_detected:
            try:
                await self.on_user_detected(steam_id, player_name)

            except Exception as e:
                _debug.log(f"[ERROR] _emit_user_detected callback failed: {e}")

    def _process_line(self, line: str) -> Optional[LapData]:
        """

        Process a single log line and extract relevant data.



        Returns a LapData object if a lap was completed, None otherwise.

        """

        line = line.strip()

        # Store line in memory buffer for preservation
        self._add_to_log_buffer(line)

        self._last_activity_ts = time.time()

        completed_lap: Optional[LapData] = None

        # --- Metadata & Context ---

        # Game version

        if "Build release" in line:
            m = self._patterns["version"].search(line)

            if m:
                self.context.game_version = m.group(1)

                # Emit game version callback (only if we're past initial parsing)

                if self.on_game_version and self._emit_callbacks:
                    asyncio.create_task(self.on_game_version(self.context.game_version))

        # Track name (primary)

        if "TRACK NAME" in line:
            m = self._patterns["track_name"].search(line)

            if m:
                track_name = m.group(1).strip()

                self.context.current_track = track_name

                if self.current_session:
                    self.current_session.track = track_name

        # Track name (fallback from scene load)

        elif (
            "Loading scene" in line or "Loading Scene" in line
        ) and "content\\tracks" in line:
            m = self._patterns["track_load"].search(line)

            if m:
                self.context.current_track = m.group(1)

                if self.current_session and self.current_session.track == "Unknown":
                    self.current_session.track = self.context.current_track

        # Player connection with Steam ID - this also indicates session start

        if "connected on car" in line:
            m = self._patterns["connect"].search(line)

            if m:
                new_pid = m.group(1)

                new_car = m.group(2)

                new_uuid = m.group(3)

                _debug.log(
                    f"[CONNECT] Player connected: pid={new_pid}, car={new_car}, uuid={new_uuid}"
                )

                is_steam_id = len(new_pid) > 10

                current_has_steam = (
                    self.context.player_id and len(str(self.context.player_id)) > 10
                )

                _debug.log(
                    f"  is_steam_id: {is_steam_id}, current_has_steam: {current_has_steam}"
                )

                if is_steam_id or not current_has_steam:
                    self.context.player_id = new_pid

                    self.context.current_car = new_car

                    self.context.car_uuid = new_uuid

                    # Track this car UUID as belonging to our player

                    self.context.player_car_uuids.add(new_uuid)

                    _debug.log(
                        f"  Updated context: player_id={new_pid}, car_uuid={new_uuid}"
                    )

                    _debug.log(
                        f"  player_car_uuids now: {self.context.player_car_uuids}"
                    )

                    if new_uuid in self.context.car_meta:
                        meta = self.context.car_meta[new_uuid]

                        if meta.get("player_name"):
                            self.context.player_name = meta["player_name"]

                        if meta.get("player_id") and not current_has_steam:
                            self.context.player_id = meta["player_id"]

                    # Create session if one doesn't exist (player connection = session start)

                    if not self.current_session:
                        _debug.log("  No session exists, creating one")

                        self._start_new_session("UNKNOWN", line)

                    if self.current_session:
                        self.current_session.car_uuid = self.context.car_uuid

                        self.current_session.car = self.context.current_car

                        self.current_session.player_name = self.context.player_name

                        self.current_session.player_id = self.context.player_id

                        _debug.log(
                            f"  Session updated with car_uuid={self.current_session.car_uuid}"
                        )

        # Driver info - only use if we don't already have a Steam ID player

        # (these lines are also emitted for AI drivers)

        if "\tDriver " in line and " on car " in line:
            m = self._patterns["driver_line"].search(line)

            if m:
                driver_name = m.group(1).strip()

                driver_car = m.group(2).strip()

                # Only update player info if we don't have a Steam ID yet

                # (Steam ID is 17 digits starting with 7656)

                has_steam_id = (
                    self.context.player_id
                    and len(str(self.context.player_id)) == 17
                    and str(self.context.player_id).startswith("7656")
                )

                if not has_steam_id:
                    self.context.player_name = driver_name

                    self.context.current_car = driver_car

                    if self.current_session:
                        self.current_session.player_name = driver_name

                        self.current_session.car = driver_car

        # Game car connection metadata

        if "connecting gamecar" in line and "(" in line and ")" in line:
            m = self._patterns["connecting_gamecar"].search(line)

            if m:
                car_uuid = m.group(1)

                raw = m.group(2)

                cleaned = raw.replace("â€¢", "").replace("•", "").strip()

                player_name = None

                player_id = None

                if "|" in cleaned:
                    left, right = cleaned.split("|", 1)

                    player_name = left.strip()

                    player_id = right.strip()

                else:
                    player_name = cleaned

                self.context.car_meta[car_uuid] = {
                    "player_name": player_name,
                    "player_id": player_id,
                }

                if self.context.car_uuid == car_uuid:
                    if player_name:
                        self.context.player_name = player_name

                    current_has_steam = (
                        self.context.player_id and len(str(self.context.player_id)) > 10
                    )

                    if player_id and not current_has_steam:
                        self.context.player_id = player_id

                    if self.current_session:
                        self.current_session.player_name = self.context.player_name

                        self.current_session.player_id = self.context.player_id

                    else:
                        # Create session when connecting to car if no session exists

                        self._start_new_session("PRACTICE", line)

        # Weather

        if "GameModeSelectionWeatherBehaviour_" in line:
            m = self._patterns["weather"].search(line)

            if m:
                self.context.weather = m.group(1)

        # --- Session Lifecycle ---

        # New session (Game Started!) - just start fresh, no need to finalize old

        if "Game Started!" in line and "GameModeType_" in line:
            m = self._patterns["session_start_alt"].search(line)

            if m:
                _debug.log(f"[SESSION] Game Started! detected, type={m.group(1)}")

                # Just start a new session - old one doesn't matter

                self._start_new_session(m.group(1), line)

                _debug.log(
                    f"[SESSION] New session created, car_uuid={self.current_session.car_uuid if self.current_session else 'None'}"
                )

                return None

        # New session (mode change) - also just start fresh

        if "GameModeType_" in line:
            m = self._patterns["session_type"].search(line)

            if m:
                _debug.log(f"[SESSION] Mode change detected, type={m.group(1)}")

                self._start_new_session(m.group(1), line)

                return None

        # Session end - only when OUR car's session ends

        if "END_SESSION car" in line and self.context.car_uuid:
            if self.context.car_uuid in line:
                _debug.log("[SESSION] END_SESSION for our car, finalizing")

                self._finalize_current_session()

                # Don't return - allow further processing if needed

        if not self.current_session:
            return None

        if "compound:" in line:
            m = self._patterns["compound"].search(line)
            if m:
                self.context.tyre_compound = f"compound {m.group(1)}"

        # Extract tyre compound from platformCore CarId lines (binds car to compound)
        if (
            "[platformCore] [info] CarId:" in line
            and "Tyre:" in line
            and "compound:" in line
        ):
            m = self._patterns["car_tyre_compound"].search(line)
            if m:
                car_id = m.group(1)
                tyre_pos = m.group(2)
                compound_num = m.group(3)

                # Only process if this is our target car
                if self._normalize_uuid(car_id) == self._normalize_uuid(
                    self.context.car_uuid
                ):
                    # Track compounds to detect mixed setups
                    if not hasattr(self.context, "_tyre_compounds_seen"):
                        self.context._tyre_compounds_seen = []

                    if compound_num not in self.context._tyre_compounds_seen:
                        self.context._tyre_compounds_seen.append(compound_num)

                    # If multiple different compounds, mark as "Mixed"
                    if len(self.context._tyre_compounds_seen) > 1:
                        self.context.tyre_compound = "Mixed"
                    else:
                        self.context.tyre_compound = f"compound {compound_num}"

        # Tyre compound from physics log
        if "setCompound Tyre" in line and "compound name:" in line:
            m = self._patterns["physics_compound"].search(line)
            if m:
                tyre_pos = m.group(1)
                compound_name = m.group(2)

                # Reset compound tracking on first tyre
                if tyre_pos == "0":
                    if hasattr(self.context, "_physics_tyre_compounds_seen"):
                        del self.context._physics_tyre_compounds_seen

                # Track compounds to detect mixed setups
                if not hasattr(self.context, "_physics_tyre_compounds_seen"):
                    self.context._physics_tyre_compounds_seen = []

                if compound_name not in self.context._physics_tyre_compounds_seen:
                    self.context._physics_tyre_compounds_seen.append(compound_name)

                # If multiple different compounds, mark as "Mixed"
                if len(self.context._physics_tyre_compounds_seen) > 1:
                    self.context.tyre_compound = "Mixed"
                else:
                    self.context.tyre_compound = compound_name
                _debug.log(
                    f"[TYRE] Physics tyre compound update: {self.context.tyre_compound}"
                )

        # Physics lap counter - reliable lap 1 / out-lap detection
        if "[physics] [info] Lap test evOnLapCompleted" in line:
            m = self._patterns["physics_lap"].search(line)
            if m:
                lap_num = int(m.group(1))
                self._current_lap_data["physics_lap_num"] = lap_num
                _debug.log(f"[PHYSICS_LAP] Physics lap counter: {lap_num}")

        # Split start - cumulative time extraction (assumes player's car)
        if "On Split start" in line:
            m = self._patterns["split_start"].search(line)

            # Create session if needed (fallback for test scenarios)
            if m and not self.current_session:
                _debug.log("[SPLIT_START] Creating session for split data")
                self._start_new_session("PRACTICE", "Split data detected")

                # Set a default car UUID for testing scenarios
                self.current_session.car_uuid = "test_car_uuid"
                self.context.car_uuid = "test_car_uuid"
                self.context.player_car_uuids.add("test_car_uuid")

            if m and self.current_session:
                split_idx = int(m.group(3))
                split_ms = int(m.group(4))

                # Store split time - these are individual sector times
                self._current_lap_data["splits"][split_idx] = split_ms
                _debug.log(f"[SPLIT_START] Split {split_idx}: {split_ms}ms")

        # Split end - all sectors confirmed for this lap
        if "On Split end with all splits" in line:
            m = self._patterns["split_end"].search(line)
            if m:
                self._current_lap_data["split_end_received"] = True
                _debug.log("[SPLIT_END] split_end_received set to True")

        # Penalty (invalidates lap) - Enhanced detection based on real log analysis
        penalty_match = self._patterns["penalty"].search(line)
        if penalty_match:
            self._current_lap_data["is_valid"] = False
            _debug.log(
                f"[VALIDITY] INVALID: Penalty detected - {penalty_match.group(0)}"
            )

        # Unexpected split (invalidates lap)
        if "Unexpected On Split" in line:
            self._current_lap_data["is_valid"] = False
            self._current_lap_data["unexpected_split"] = True
            _debug.log("[VALIDITY] INVALID: Unexpected split detected")

        # Fuel setup

        if "FUEL car" in line and "setup with" in line:
            m = self._patterns["fuel"].search(line)

            if m:
                car_id = m.group(1)

                fuel_amount = float(m.group(2))

                is_player_car = (
                    self.current_session and car_id == self.current_session.car_uuid
                )

                if is_player_car:
                    self.current_session.initial_fuel = fuel_amount

        # Fuel consumption

        if (
            "[gameplay] [info] Energy source car" in line
            and "fuel consumed:" in line
            and "hundredmeters done:" in line
        ):
            m = self._patterns["fuel_consumed"].search(line)

            if m:
                timestamp = m.group(1)

                car_id = m.group(2)

                hundreds_done = int(m.group(3))

                fuel_reading = float(m.group(4))

                # Check if this is our player's car

                is_player_car = (
                    self.current_session and car_id == self.current_session.car_uuid
                )

                if is_player_car:
                    # Simply accumulate fuel during the current lap
                    if fuel_reading > 0:  # Only count positive fuel consumption
                        # CRITICAL: Add fuel spike protection based on real log analysis
                        if fuel_reading > 1.5:  # FUEL SPIKE PROTECTION
                            _debug.log(
                                f"[FUEL] Ignoring fuel spike: {fuel_reading}L (exceeds 1.5L threshold)"
                            )
                            return None  # Likely corrupted spike - skip this line
                        # Initialize fuel accumulator if it's None
                        if self._current_lap_data["fuel_used_lap"] is None:
                            self._current_lap_data["fuel_used_lap"] = 0.0
                        self._current_lap_data["fuel_used_lap"] += fuel_reading
                        _debug.log(
                            f"[FUEL] Added fuel: {fuel_reading}L, lap total: {self._current_lap_data['fuel_used_lap']}L"
                        )

        # Lap completion

        if "New lap carId" in line:
            m = self._patterns["lap_finish"].search(line)

            _debug.log("[LAP_CHECK] New lap line detected")

            _debug.log(f"  regex match: {m is not None}")

            _debug.log(f"  current_session: {self.current_session is not None}")

            if m:
                lap_timestamp = m.group(1)

                car_id = m.group(2)

                lap_time_str = m.group(3)

                _debug.log(f"  lap_timestamp: {lap_timestamp}")

                _debug.log(f"  car_id from log: {car_id}")

                _debug.log(f"  lap_time: {lap_time_str}")

                _debug.log(
                    f"  context.player_car_uuids: {self.context.player_car_uuids}"
                )

                _debug.log(
                    f"  fuel_used_lap: {self._current_lap_data.get('fuel_used_lap', 0.0)}L"
                )

                # Check if this is our player's car

                is_player_car = car_id == self.current_session.car_uuid

                _debug.log(f"  session.car_uuid: {self.current_session.car_uuid}")

                _debug.log(f"  is_player_car: {is_player_car}")

                if is_player_car:
                    # Use the accumulated fuel for this lap
                    fuel_used_lap = self._current_lap_data["fuel_used_lap"] or 0.0

                    if fuel_used_lap > 0:
                        # Add to session total
                        self.current_session.fuel_used_session += fuel_used_lap
                        _debug.log(
                            f"[FUEL] Lap fuel used: {fuel_used_lap}L, session total: {self.current_session.fuel_used_session}L"
                        )
                    else:
                        _debug.log("[FUEL] No fuel consumed this lap")

                    # Create session on-the-fly if we don't have one but have player context

                    if not self.current_session and self.context.player_id:
                        _debug.log(
                            "  No session but have player context, creating session"
                        )

                        self._start_new_session("UNKNOWN", line)

                    if not self.current_session:
                        _debug.log("  Still no session, skipping lap")

                        return None

                    lap_time_ms = self._parse_lap_time_to_ms(lap_time_str)

                    # Extract sector times from split data
                    # NOTE: splits are now stored as dict with split_idx as key
                    splits = self._current_lap_data["splits"]

                    # Extract sector times (these are already individual sector times)
                    sector1_ms = splits.get(0)
                    sector2_ms = splits.get(1)
                    sector3_ms = splits.get(2)

                    # Enhanced validity checks based on real log analysis
                    # Note: These checks must run AFTER all split processing is complete
                    # 1. Penalty detected - already handled above
                    if self._current_lap_data["unexpected_split"]:
                        self._current_lap_data["is_valid"] = False
                        _debug.log("[VALIDITY] INVALID: Unexpected split")
                    # 2. Split 2 present but no "end with all splits" confirmation
                    elif (
                        2 in splits and not self._current_lap_data["split_end_received"]
                    ):
                        self._current_lap_data["is_valid"] = False
                        _debug.log(
                            "[VALIDITY] INVALID: Has split 2 but no end confirmation"
                        )
                    # 3. Outlap detection - ONLY in PRACTICE sessions
                    physics_lap_num = self._current_lap_data.get("physics_lap_num")
                    session_type = (
                        self.current_session.session_type.upper()
                        if self.current_session
                        else "UNKNOWN"
                    )
                    lap_type = "PUSH"  # Default classification

                    if physics_lap_num == 1 and "PRACTICE" in session_type:
                        self._current_lap_data["is_valid"] = False
                        lap_type = "OUTLAP"
                        _debug.log(
                            "[VALIDITY] INVALID: Outlap detected (lap 1 in PRACTICE session)"
                        )
                    elif not self._current_lap_data["is_valid"]:
                        lap_type = "INVALID_PUSH"
                        _debug.log("[VALIDITY] INVALID: Invalid push lap detected")
                    # 4. Missing all 3 sectors - invalid lap
                    elif not all(sector in splits for sector in [0, 1, 2]):
                        self._current_lap_data["is_valid"] = False
                        lap_type = "INVALID_PUSH"
                        _debug.log(
                            f"[VALIDITY] INVALID: Missing sector data - have {list(splits.keys())}"
                        )

                    _debug.log(
                        f"[VALIDITY] Final: {self._current_lap_data['is_valid']}, lap_type: {lap_type}, splits: {splits}, split_end: {self._current_lap_data['split_end_received']}"
                    )

                    # Distance extraction removed - using simplified fuel logic
                    distance_km = None

                    completed_lap = LapData(
                        lap_time_ms=lap_time_ms,
                        lap_time_str=lap_time_str,
                        sector1_ms=sector1_ms,
                        sector2_ms=sector2_ms,
                        sector3_ms=sector3_ms,
                        is_valid=self._current_lap_data["is_valid"],
                        lap_type=lap_type,  # Enhanced classification
                        fuel_used=fuel_used_lap,
                        tyre_compound=self.context.tyre_compound or "Unknown",
                        timestamp=lap_timestamp,  # Use actual lap timestamp
                        distance_km=distance_km,
                    )

                    self.current_session.laps.append(completed_lap)

                    self.current_session.tyre_compound = (
                        self.context.tyre_compound or "Unknown"
                    )

            # Reset for next lap - clear fuel accumulator
            self._current_lap_data = {
                "splits": {},  # Changed from list to dict
                "is_valid": True,
                "fuel_used_lap": 0.0,  # Reset fuel for next lap
                "lap_start_time": None,
                "split_end_received": False,  # Reset split end confirmation
                "unexpected_split": False,  # Reset unexpected split flag
                "physics_lap_num": None,  # Reset physics lap counter
            }

        return completed_lap

    def _start_new_session(self, session_type: str, line: str) -> None:
        """Initialize a new session with current context."""

        self.current_session = SessionData(
            session_type=session_type,
            game_version=self.context.game_version,
            track=self.context.current_track,
            car=self.context.current_car,
            player_name=self.context.player_name,
            player_id=self.context.player_id,
            car_uuid=self.context.car_uuid,
            weather=self.context.weather,
            tyre_compound=self.context.tyre_compound or "Unknown",
        )

        # Reset fuel tracking for new session

        self.context.last_fuel_reading = None

        # Clear old car UUIDs to prevent cross-session split contamination
        self.context.player_car_uuids.clear()
        if self.context.car_uuid:
            self.context.player_car_uuids.add(self.context.car_uuid)

        # Reset lap data for new session
        self._current_lap_data = {
            "splits": {},  # Changed from list to dict
            "is_valid": True,
            "fuel_used_lap": 0.0,  # Start with zero fuel for new session
            "lap_start_time": None,
            "split_end_received": False,  # Reset split end confirmation
            "unexpected_split": False,  # Reset unexpected split flag
            "physics_lap_num": None,  # Reset physics lap counter
            "lap_start_sector": None,  # Reset lap start sector
            "lap_start_sector_time": None,  # Reset lap start sector time
        }

        # Parse additional info from Game Started line

        parts = [p.strip() for p in line.split("|")]

        if len(parts) > 1 and parts[1]:
            self.context.current_track = parts[1]

            self.current_session.track = parts[1]

        if len(parts) > 2 and parts[2]:
            self.context.current_car = parts[2]

            self.current_session.car = parts[2]

        if len(parts) > 3 and parts[3]:
            w = parts[3].replace("GameModeSelectionWeatherType_", "")

            self.context.weather = w

            self.current_session.weather = w

        # Extract timestamp

        tm = self._patterns["date"].match(line)

        if tm:
            self.current_session.start_time = tm.group(1)

    def _finalize_current_session(self) -> None:
        """Save current session if it has completed laps."""

        if self.current_session and self.current_session.laps:
            self.sessions.append(self.current_session)

        self.current_session = None

    async def parse_file(self) -> list[SessionData]:
        """Parse entire log file (one-shot mode)."""

        if not self.log_path.exists():
            await self._emit_status(f"Log file not found: {self.log_path}")

            return []

        await self._emit_status(f"Parsing {self.log_path}...")

        with open(self.log_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                completed_lap = self._process_line(line)

                if completed_lap and self.current_session:
                    await self._emit_lap(self.current_session, completed_lap)

        self._finalize_current_session()

        await self._emit_status(f"Found {len(self.sessions)} sessions")

        return self.sessions

    async def follow(
        self,
        poll_interval: float = 0.25,
    ) -> None:
        """

        Follow log file in real-time (tail -f style).



        Based on working telemetry.py implementation - simple and reliable.

        Parses existing log first to catch any laps already recorded.



        Args:

            poll_interval: Seconds between poll attempts

        """

        # Start debug logging

        _debug.start()

        _debug.log(f"follow() called with poll_interval={poll_interval}")

        _debug.log(f"Log path: {self.log_path}")

        self._running = True

        # Wait for log file to exist

        while self._running and not self.log_path.exists():
            _debug.log("Waiting for log file to exist...")

            await self._emit_status(f"Waiting for log file: {self.log_path}")

            await asyncio.sleep(poll_interval)

        if not self._running:
            _debug.log("_running is False, exiting")

            return

        _debug.log("Log file exists, opening...")

        await self._emit_status("Parsing existing log...")

        with open(self.log_path, "r", encoding="utf-8", errors="ignore") as f:
            _debug.log("File opened successfully")

            # First, parse the entire existing log to establish context

            # DON'T emit laps to UI - just count them for context

            line_count = 0

            existing_laps = 0

            _debug.log("Starting initial log parsing (historical context only)...")

            try:
                for line in f:
                    line_count += 1

                    try:
                        completed_lap = self._process_line(line)

                        if completed_lap and self.current_session:
                            existing_laps += 1

                            _debug.log(
                                f"[EXISTING] Lap {existing_laps}: {completed_lap.lap_time_str}"
                            )

                            # Don't emit existing laps - they're historical

                    except Exception as e:
                        _debug.log(
                            f"[ERROR] Exception processing line {line_count}: {e}"
                        )

                        _debug.log(f"  Line content: {line.strip()[:100]}")

            except Exception as e:
                _debug.log(f"[ERROR] Exception in initial parsing loop: {e}")

                import traceback

                _debug.log(f"[ERROR] Traceback: {traceback.format_exc()}")

            _debug.log(
                f"Initial parsing complete: {line_count} lines, {existing_laps} historical laps"
            )

            _debug.log(f"Current session: {self.current_session is not None}")

            if self.current_session:
                _debug.log(f"  Track: {self.current_session.track}")

                _debug.log(f"  Car: {self.current_session.car}")

                _debug.log(
                    f"  Player: {self.current_session.player_name} ({self.current_session.player_id})"
                )

                _debug.log(f"  Car UUID: {self.current_session.car_uuid}")

                # Clear historical laps from session - we only want NEW laps going forward

                self.current_session.laps.clear()

                _debug.log("  Cleared historical laps, ready for new ones")

            _debug.log(f"Context car_uuid: {self.context.car_uuid}")

            _debug.log(f"Context player_car_uuids: {self.context.player_car_uuids}")

            # Initial parsing done - now enable callbacks for real-time events

            self._emit_callbacks = True

            _debug.log("Callbacks enabled for real-time processing")

            # Report what we found
            _debug.log("Sending initial UI updates...")

            if self.current_session:
                _debug.log("  Have session, notifying UI")

                await self._emit_status("Monitoring for new laps...")

                # Notify about detected user
                if self.current_session.player_id:
                    _debug.log(
                        f"  Notifying user detected: {self.current_session.player_id}"
                    )
                    await self._emit_user_detected(
                        self.current_session.player_id, self.current_session.player_name
                    )

                # Notify about game version if detected
                if self.context.game_version and self.context.game_version != "Unknown":
                    _debug.log(f"  Notifying game version: {self.context.game_version}")
                    if self.on_game_version:
                        try:
                            await self.on_game_version(self.context.game_version)
                        except Exception as e:
                            _debug.log(f"[ERROR] on_game_version callback failed: {e}")
            else:
                _debug.log("  No session found, waiting...")
                await self._emit_status("Ready - waiting for session...")

                # Still notify about game version if we found it
                if self.context.game_version and self.context.game_version != "Unknown":
                    _debug.log(
                        f"  Notifying game version (no session): {self.context.game_version}"
                    )
                    if self.on_game_version:
                        try:
                            await self.on_game_version(self.context.game_version)
                        except Exception as e:
                            _debug.log(f"[ERROR] on_game_version callback failed: {e}")

            # Now at end of file - follow new lines

            file_pos = f.tell()

            _debug.log(f"Starting follow loop at file position {file_pos}")

            lines_read = 0

            _debug.log("Entering follow loop - monitoring for new lines...")

            while self._running:
                line = f.readline()

                if line:
                    lines_read += 1

                    # Log important lines and emit game status

                    if "Game Started!" in line:
                        _debug.log(f"[LIVE] [GAME_STARTED] {line.strip()[:100]}")

                        # Notify UI that game session started

                        await self._emit_game_status(True)

                    if "connected on car" in line:
                        _debug.log(f"[LIVE] [CONNECTED] {line.strip()[:150]}")

                    if "New lap carId" in line:
                        _debug.log(f"[LIVE] [NEW_LAP_LINE] {line.strip()}")

                    if "END_SESSION car" in line:
                        _debug.log(f"[LIVE] [END_SESSION] {line.strip()[:80]}")

                        # Check if it's our car - emit game status false

                        if self.context.car_uuid and self.context.car_uuid in line:
                            _debug.log("[LIVE] Our session ended, notifying UI")

                            await self._emit_game_status(False)

                    try:
                        completed_lap = self._process_line(line)

                    except Exception as e:
                        _debug.log(f"[ERROR] Exception in _process_line: {e}")

                        import traceback

                        _debug.log(f"[ERROR] Traceback: {traceback.format_exc()}")

                        continue

                    if completed_lap:
                        _debug.log(
                            f"[LIVE] [LAP_COMPLETE] {completed_lap.lap_time_str}"
                        )

                        _debug.log(f"  is_valid: {completed_lap.is_valid}")

                        _debug.log(
                            f"  session exists: {self.current_session is not None}"
                        )

                        if self.current_session:
                            _debug.log(f"  session.track: {self.current_session.track}")

                            _debug.log(f"  session.car: {self.current_session.car}")

                            _debug.log(
                                f"  session.player_id: {self.current_session.player_id}"
                            )

                    if completed_lap:
                        # Always emit laps, use current session or create a default one
                        session = self.current_session
                        if not session:
                            # Create a default session for laps without an active session
                            session = SessionData(
                                track="Unknown Track",
                                car="Unknown Car",
                                player_id=None,
                                player_name="Unknown Player",
                                car_uuid=None,
                            )
                            _debug.log(
                                f"[LIVE] Created default session for lap: {completed_lap.lap_time_str}"
                            )

                        _debug.log("[LIVE] [EMIT] About to emit lap to UI callback...")

                        try:
                            await self._emit_lap(session, completed_lap)
                            _debug.log("[LIVE] [EMIT] Successfully emitted lap!")
                        except Exception as e:
                            _debug.log(f"[ERROR] Failed to emit lap: {e}")
                            import traceback

                            _debug.log(f"[ERROR] Traceback: {traceback.format_exc()}")

                    continue

                # Session finalization happens via END_SESSION log line, not timeout

                # Check for log file truncation (game restart)

                try:
                    current_size = os.path.getsize(self.log_path)

                except OSError:
                    current_size = None

                if current_size is not None and current_size < f.tell():
                    _debug.log(
                        f"[TRUNCATE] Log file truncated! current_size={current_size}, file_pos={f.tell()}"
                    )

                    _debug.log(
                        "[TRUNCATE] Resetting parser state and seeking to start"
                    )

                    # Reset context for new game session

                    self.context = LogContext()

                    self.current_session = None

                    self._emit_callbacks = True  # Keep callbacks enabled

                    await self._emit_status("Log file reset detected, restarting...")

                    f.seek(0)

                await asyncio.sleep(poll_interval)

        _debug.log("follow() exiting")

        _debug.close()

    def stop(self) -> None:
        """Stop the follow loop."""

        self._running = False

    @property
    def is_running(self) -> bool:
        """Check if parser is currently running."""

        return self._running

    def get_current_session(self) -> Optional[SessionData]:
        """Get the current active session."""

        return self.current_session

    def get_player_id(self) -> Optional[str]:
        """Get the detected player Steam ID."""

        return self.context.player_id
