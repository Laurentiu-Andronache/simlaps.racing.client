"""
Log Parser for ACE game logs.

Adapted from telemetry.py - monitors ACE log files in real-time and extracts
lap time data for submission to the SimLaps server.

Includes anti-cheat measures: only processes logs when game is running.
"""

import re
import os
import time
import uuid
import asyncio
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable
from pathlib import Path

from .security import is_game_running


@dataclass
class LapData:
    """Represents a single completed lap."""
    lap_time_ms: int
    lap_time_str: str
    sector1_ms: Optional[int] = None
    sector2_ms: Optional[int] = None
    sector3_ms: Optional[int] = None
    is_valid: bool = True
    fuel_used: Optional[float] = None
    tyre_compound: str = "Unknown"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


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
                    "fuel_used": lap.fuel_used,
                    "tyre_compound": lap.tyre_compound,
                    "timestamp": lap.timestamp,
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
        self.car_meta: dict[str, dict] = {}


# Type alias for callbacks
LapCallback = Callable[[SessionData, LapData], Awaitable[None]]
StatusCallback = Callable[[str], Awaitable[None]]
GameStatusCallback = Callable[[bool], Awaitable[None]]


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
    ):
        self.log_path = Path(log_path) if log_path else self.DEFAULT_LOG_PATH
        self.on_lap_complete = on_lap_complete
        self.on_status_change = on_status_change
        self.on_game_status_change = on_game_status_change
        
        self.sessions: list[SessionData] = []
        self.current_session: Optional[SessionData] = None
        self.context = LogContext()
        
        self._last_activity_ts: Optional[float] = None
        self._running = False
        self._game_was_running = False  # Track game state for callbacks
        self._current_lap_data: dict = {
            "splits": [],
            "is_valid": True,
            "fuel_used_lap": None,
        }
        self._fuel_logged_this_lap = False

        # Compile regex patterns for performance
        self._patterns = {
            "version": re.compile(r"Build release ([^,]+),"),
            "session_type": re.compile(r"Changed to .* GameModeType_([A-Z_]+)"),
            "session_start_alt": re.compile(r"Game Started!\s*GameModeType_([A-Z_]+)"),
            "driver_line": re.compile(r"\tDriver (.+) on car ([\w_]+)"),
            "connecting_gamecar": re.compile(r"connecting gamecar ([a-f0-9\-]+) \((.+)\)"),
            "connect": re.compile(r"(\S+) connected on car ([\w_]+), with new carId ([a-f0-9\-]+)"),
            "track_name": re.compile(r"TRACK NAME (.+)"),
            "track_load": re.compile(r"Scene::load\('content\\\\tracks\\\\([^\\\\]+)"),
            "fuel": re.compile(r"FUEL car ([a-f0-9\-]+) setup with ([\d.]+) L"),
            "compound": re.compile(r"setCompound Tyre: \d compound name: (\w+)"),
            "fuel_consumed": re.compile(
                r"Energy source car ([a-f0-9\-]+) for driver [a-f0-9\-]+ "
                r"hundredmeters done: \d+ fuel consumed: ([\d.]+) L"
            ),
            "split": re.compile(r"On Split .* id (\d+) splittime (\d+)"),
            "lap_finish": re.compile(r"New lap carId ([a-f0-9\-]+): ([\d:.]+)"),
            "penalty": re.compile(r"\{PENALTY_ADDED_KEY\}"),
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

    async def _emit_status(self, status: str) -> None:
        """Emit status update to callback."""
        if self.on_status_change:
            await self.on_status_change(status)

    async def _emit_lap(self, session: SessionData, lap: LapData) -> None:
        """Emit completed lap to callback."""
        if self.on_lap_complete:
            await self.on_lap_complete(session, lap)

    async def _emit_game_status(self, is_running: bool) -> None:
        """Emit game status change to callback."""
        if self.on_game_status_change:
            await self.on_game_status_change(is_running)

    def _process_line(self, line: str) -> Optional[LapData]:
        """
        Process a single log line and extract relevant data.
        
        Returns a LapData object if a lap was completed, None otherwise.
        """
        line = line.strip()
        self._last_activity_ts = time.time()
        completed_lap: Optional[LapData] = None

        # --- Metadata & Context ---

        # Game version
        if "Build release" in line:
            m = self._patterns["version"].search(line)
            if m:
                self.context.game_version = m.group(1)

        # Track name (primary)
        if "TRACK NAME" in line:
            m = self._patterns["track_name"].search(line)
            if m:
                track_name = m.group(1).strip()
                self.context.current_track = track_name
                if self.current_session:
                    self.current_session.track = track_name

        # Track name (fallback from scene load)
        elif "Scene::load" in line and "content\\tracks" in line:
            m = self._patterns["track_load"].search(line)
            if m:
                self.context.current_track = m.group(1)
                if self.current_session and self.current_session.track == "Unknown":
                    self.current_session.track = self.context.current_track

        # Player connection with Steam ID
        if "connected on car" in line:
            m = self._patterns["connect"].search(line)
            if m:
                new_pid = m.group(1)
                new_car = m.group(2)
                new_uuid = m.group(3)

                is_steam_id = len(new_pid) > 10
                current_has_steam = self.context.player_id and len(str(self.context.player_id)) > 10

                if is_steam_id or not current_has_steam:
                    self.context.player_id = new_pid
                    self.context.current_car = new_car
                    self.context.car_uuid = new_uuid

                    if new_uuid in self.context.car_meta:
                        meta = self.context.car_meta[new_uuid]
                        if meta.get("player_name"):
                            self.context.player_name = meta["player_name"]
                        if meta.get("player_id") and not current_has_steam:
                            self.context.player_id = meta["player_id"]

                    if self.current_session:
                        self.current_session.car_uuid = self.context.car_uuid
                        self.current_session.car = self.context.current_car
                        self.current_session.player_name = self.context.player_name
                        self.current_session.player_id = self.context.player_id

        # Driver info
        if "\tDriver " in line and " on car " in line:
            m = self._patterns["driver_line"].search(line)
            if m:
                self.context.player_name = m.group(1).strip()
                self.context.current_car = m.group(2).strip()
                if self.current_session:
                    self.current_session.player_name = self.context.player_name
                    self.current_session.car = self.context.current_car

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
                    current_has_steam = self.context.player_id and len(str(self.context.player_id)) > 10
                    if player_id and not current_has_steam:
                        self.context.player_id = player_id
                    if self.current_session:
                        self.current_session.player_name = self.context.player_name
                        self.current_session.player_id = self.context.player_id

        # Weather
        if "GameModeSelectionWeatherBehaviour_" in line:
            m = self._patterns["weather"].search(line)
            if m:
                self.context.weather = m.group(1)

        # --- Session Lifecycle ---

        # New session (Game Started!)
        if "Game Started!" in line and "GameModeType_" in line:
            m = self._patterns["session_start_alt"].search(line)
            if m:
                self._finalize_current_session()
                self._start_new_session(m.group(1), line)
                return None

        # New session (mode change)
        if "GameModeType_" in line:
            m = self._patterns["session_type"].search(line)
            if m:
                self._finalize_current_session()
                self._start_new_session(m.group(1), line)
                return None

        if not self.current_session:
            return None

        # --- Lap Data ---

        # Split times
        if "On Split" in line and "splittime" in line:
            m = self._patterns["split"].search(line)
            if m:
                split_idx = int(m.group(1))
                time_ms = int(m.group(2))
                self._current_lap_data["splits"].append({
                    "index": split_idx,
                    "time_ms": time_ms,
                })

        # Tyre compound
        if "setCompound Tyre:" in line:
            m = self._patterns["compound"].search(line)
            if m:
                self.context.tyre_compound = m.group(1)

        # Penalty (invalidates lap)
        if "PENALTY_ADDED_KEY" in line:
            self._current_lap_data["is_valid"] = False

        # Fuel setup
        if "FUEL car" in line and "setup with" in line:
            m = self._patterns["fuel"].search(line)
            if m:
                car_id = m.group(1)
                fuel_amount = float(m.group(2))
                if self.current_session and car_id == self.current_session.car_uuid:
                    self.current_session.initial_fuel = fuel_amount

        # Fuel consumption
        if "Energy source car" in line and "fuel consumed:" in line:
            m = self._patterns["fuel_consumed"].search(line)
            if m and not self._fuel_logged_this_lap:
                car_id = m.group(1)
                fuel_consumed_total = float(m.group(2))
                if self.current_session and car_id == self.current_session.car_uuid:
                    if len(self.current_session.laps) == 0 and self.current_session.initial_fuel > 0:
                        fuel_used = fuel_consumed_total - self.current_session.initial_fuel
                    else:
                        fuel_used = fuel_consumed_total

                    self._current_lap_data["fuel_used_lap"] = fuel_used
                    self.current_session.fuel_used_session += fuel_used
                    self._fuel_logged_this_lap = True

        # Lap completion
        if "New lap carId" in line:
            m = self._patterns["lap_finish"].search(line)
            if m:
                car_id = m.group(1)
                lap_time_str = m.group(2)

                if car_id == self.current_session.car_uuid:
                    lap_time_ms = self._parse_lap_time_to_ms(lap_time_str)
                    
                    # Extract sector times
                    splits = self._current_lap_data["splits"]
                    sector1_ms = splits[0]["time_ms"] if len(splits) > 0 else None
                    sector2_ms = (splits[1]["time_ms"] - splits[0]["time_ms"]) if len(splits) > 1 else None
                    sector3_ms = (lap_time_ms - splits[1]["time_ms"]) if len(splits) > 1 else None

                    completed_lap = LapData(
                        lap_time_ms=lap_time_ms,
                        lap_time_str=lap_time_str,
                        sector1_ms=sector1_ms,
                        sector2_ms=sector2_ms,
                        sector3_ms=sector3_ms,
                        is_valid=self._current_lap_data["is_valid"],
                        fuel_used=self._current_lap_data["fuel_used_lap"],
                        tyre_compound=self.context.tyre_compound or "Unknown",
                    )

                    self.current_session.laps.append(completed_lap)
                    self.current_session.tyre_compound = self.context.tyre_compound or "Unknown"

                    # Reset for next lap
                    self._current_lap_data = {
                        "splits": [],
                        "is_valid": True,
                        "fuel_used_lap": None,
                    }
                    self._fuel_logged_this_lap = False

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

        # Reset lap data
        self._current_lap_data = {
            "splits": [],
            "is_valid": True,
            "fuel_used_lap": None,
        }
        self._fuel_logged_this_lap = False

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
        idle_finalize_seconds: float = 5.0,
        game_check_interval: float = 2.0,
    ) -> None:
        """
        Follow log file in real-time (tail -f style).
        
        Only processes logs when the game is running (anti-cheat measure).
        
        Args:
            poll_interval: Seconds between poll attempts
            idle_finalize_seconds: Seconds of inactivity before finalizing session
            game_check_interval: Seconds between game process checks
        """
        self._running = True
        last_game_check = 0.0
        self._game_was_running = False

        # Wait for log file to exist
        while self._running and not self.log_path.exists():
            await self._emit_status(f"Waiting for log file: {self.log_path}")
            await asyncio.sleep(poll_interval)

        if not self._running:
            return

        await self._emit_status(f"Monitoring: {self.log_path}")

        with open(self.log_path, "r", encoding="utf-8", errors="ignore") as f:
            # Seek to end of file
            f.seek(0, os.SEEK_END)

            while self._running:
                # Periodically check if game is running (anti-cheat)
                current_time = time.time()
                if current_time - last_game_check >= game_check_interval:
                    game_running = is_game_running()
                    last_game_check = current_time
                    
                    # Emit status change if game state changed
                    if game_running != self._game_was_running:
                        self._game_was_running = game_running
                        await self._emit_game_status(game_running)
                        
                        if game_running:
                            await self._emit_status("Game detected - monitoring active")
                        else:
                            await self._emit_status("Waiting for game to start...")
                            # Finalize any open session when game closes
                            self._finalize_current_session()
                
                # Only process logs if game is running
                if not self._game_was_running:
                    await asyncio.sleep(poll_interval)
                    continue
                
                line = f.readline()
                
                if line:
                    completed_lap = self._process_line(line)
                    if completed_lap and self.current_session:
                        await self._emit_lap(self.current_session, completed_lap)
                    continue

                # Check for idle session finalization
                if self.current_session and self._last_activity_ts is not None:
                    if (time.time() - self._last_activity_ts) >= idle_finalize_seconds:
                        self._finalize_current_session()

                # Check for log file truncation (game restart)
                try:
                    current_size = os.path.getsize(self.log_path)
                except OSError:
                    current_size = None

                if current_size is not None and current_size < f.tell():
                    await self._emit_status("Log file reset detected, restarting...")
                    f.seek(0)

                await asyncio.sleep(poll_interval)

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

    @property
    def is_game_running(self) -> bool:
        """Check if the game is currently running."""
        return self._game_was_running
