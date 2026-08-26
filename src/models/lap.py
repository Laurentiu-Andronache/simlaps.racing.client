"""
ACE Log Parser Models

Data models for lap, session, stint, and tyre tracking.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import uuid


# ─── LapState enum ────────────────────────────────────────────────────────────

class LapState(str, Enum):
    """Explicit classification of every lap.

    Using `str` mixin so values serialise naturally to JSON without extra work.
    """
    VALID              = "VALID"               # Clean timed lap
    OUTLAP             = "OUTLAP"              # Pit-exit or formation lap
    INLAP              = "INLAP"               # Return to pits lap
    INVALID_TRACK_LIMIT = "INVALID_TRACK_LIMIT"  # tyres out → 4 during lap
    INVALID_PENALTY    = "INVALID_PENALTY"     # UI penalty notification
    INVALID_SPLIT      = "INVALID_SPLIT"       # Missing / out-of-order sectors
    INVALID_SECTORS    = "INVALID_SECTORS"     # S1+S2+S3 ≠ lap_time (desync)
    INVALID_GAME       = "INVALID_GAME"        # Game's authoritative validity flag = false
    ABORTED            = "ABORTED"             # Lap in progress when session ends


# ─── In-progress lap accumulator ─────────────────────────────────────────────

@dataclass
class InProgressLap:
    """Collects all per-lap signals until 'New lap carId' fires."""

    # Sector times: key = split index (0..N), value = ms
    splits: dict[int, int] = field(default_factory=dict)

    # Outlap classification (structural, not validity)
    is_outlap: bool = False

    # Energy-source event (fires once, just before New lap)
    fuel_used: Optional[float] = None
    fuel_reliable: bool = True

    # Distance covered this lap (hundredmeters counter delta)
    distance_hundredm: Optional[int] = None

    # Physics engine lap counter (evOnLapCompleted N)
    physics_lap_num: Optional[int] = None

    def reset(self) -> None:
        self.__init__()  # type: ignore[misc]


# ─── Stint data ───────────────────────────────────────────────────────────────

@dataclass
class StintData:
    """A continuous run on the same tyre compound."""

    stint_number: int
    tyre_compound: str
    lap_numbers: list[int] = field(default_factory=list)

    @property
    def lap_count(self) -> int:
        return len(self.lap_numbers)

    @property
    def fuel_used_total(self) -> Optional[float]:
        return self._fuel_total

    @property
    def fuel_per_lap_avg(self) -> Optional[float]:
        if self._fuel_total is not None and self.lap_count > 0:
            return round(self._fuel_total / self.lap_count, 3)
        return None

    # Internal accumulator — use add_lap()
    _fuel_total: Optional[float] = field(default=None, repr=False)

    def add_lap(self, lap_number: int, fuel_used: Optional[float]) -> None:
        self.lap_numbers.append(lap_number)
        if fuel_used is not None and fuel_used > 0:
            self._fuel_total = (self._fuel_total or 0.0) + fuel_used

    def renumber_lap(self, old_number: int, new_number: int) -> bool:
        """Replace a provisional lap number without changing fuel totals.

        Lap numbers are membership indexes, while ``_fuel_total`` is an
        aggregate of the laps that have already been enrolled.  Renumbering
        must therefore only move the membership entry.  If the destination is
        already present, discard the old membership entry rather than leaving
        a duplicate number in the stint.
        """
        if old_number == new_number or old_number not in self.lap_numbers:
            return False

        destination_present = new_number in self.lap_numbers
        if destination_present:
            self.lap_numbers = [
                number for number in self.lap_numbers if number != old_number
            ]
        else:
            self.lap_numbers = [
                new_number if number == old_number else number
                for number in self.lap_numbers
            ]
            self.lap_numbers.sort()
        return True

    def to_dict(self) -> dict:
        return {
            "stint_number": self.stint_number,
            "tyre_compound": self.tyre_compound,
            "lap_numbers": self.lap_numbers,
            "lap_count": self.lap_count,
            "fuel_used_total": self._fuel_total,
            "fuel_per_lap_avg": self.fuel_per_lap_avg,
        }


# ─── LapData ─────────────────────────────────────────────────────────────────

@dataclass
class LapData:
    """An immutable snapshot of one completed (or aborted) lap."""

    lap_number: int                        # 1-indexed within session
    physics_lap_number: Optional[int]      # from evOnLapCompleted N; ground truth
    lap_time_ms: int
    lap_time_str: str

    sector1_ms: Optional[int] = None
    sector2_ms: Optional[int] = None
    sector3_ms: Optional[int] = None
    sectors_consistent: Optional[bool] = None  # |S1+S2+S3 − lap_time| ≤ 50 ms

    lap_state: LapState = field(default_factory=lambda: LapState.VALID)
    lap_type: str = "VALID"              # String alias of lap_state.value (compat)
    is_valid: bool = True
    validity_source: str = "heuristic"   # heuristic, shm_graphics, or authoritative (Relevant onSplit)

    fuel_used: Optional[float] = None
    fuel_reliable: bool = True

    tyre_compound: str = "Unknown"
    stint_number: int = 1

    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    distance_hundredm: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "lap_number": self.lap_number,
            "physics_lap_number": self.physics_lap_number,
            "lap_time_ms": self.lap_time_ms,
            "lap_time_str": self.lap_time_str,
            "sector1_ms": self.sector1_ms,
            "sector2_ms": self.sector2_ms,
            "sector3_ms": self.sector3_ms,
            "sectors_consistent": self.sectors_consistent,
            "lap_state": self.lap_state.value,
            "lap_type": self.lap_type,
            "is_valid": self.is_valid,
            "validity_source": self.validity_source,
            "fuel_used": self.fuel_used,
            "fuel_reliable": self.fuel_reliable,
            "tyre_compound": self.tyre_compound,
            "stint_number": self.stint_number,
            "timestamp": self.timestamp,
            "distance_hundredm": self.distance_hundredm,
        }


# ─── SessionData ─────────────────────────────────────────────────────────────

@dataclass
class SessionData:
    """All metadata and laps for one game session."""

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    game_version: str = "Unknown"
    session_type: str = "Unknown"
    car: str = "Unknown"
    track: str = "Unknown"
    weather: str = "Unknown"
    player_name: Optional[str] = None
    player_id: Optional[str] = None
    car_uuid: Optional[str] = None
    tyre_compound: str = "Unknown"     # Compound at session end
    initial_fuel: float = 0.0
    fuel_used_session: float = 0.0
    fuel_reliable: bool = True
    setup_notes: Optional[str] = None
    start_time: str = field(default_factory=lambda: datetime.now().isoformat())
    laps: list[LapData] = field(default_factory=list)
    stints: list[StintData] = field(default_factory=list)

    # ── Convenience accessors ───────────────────────────────────────────────

    @property
    def valid_laps(self) -> list[LapData]:
        return [l for l in self.laps if l.is_valid]

    @property
    def best_lap(self) -> Optional[LapData]:
        valid = self.valid_laps
        return min(valid, key=lambda l: l.lap_time_ms) if valid else None

    def renumber_lap(self, lap: LapData, new_number: int) -> bool:
        """Apply an authoritative number to a lap and its stint membership.

        ``LapData`` instances are shared with UI callbacks and the parser's
        pending/SHM reconciliation queues, so mutate the instance in place.
        Stint membership is updated in the same model operation and handles a
        destination collision by keeping one membership entry.
        """
        old_number = lap.lap_number
        if old_number == new_number:
            return False

        lap.lap_number = new_number
        for stint in self.stints:
            stint.renumber_lap(old_number, new_number)
        return True

    def to_dict(self) -> dict:
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
            "fuel_reliable": self.fuel_reliable,
            "setup_notes": self.setup_notes,
            "start_time": self.start_time,
            "laps": [lap.to_dict() for lap in self.laps],
            "stints": [s.to_dict() for s in self.stints],
        }
