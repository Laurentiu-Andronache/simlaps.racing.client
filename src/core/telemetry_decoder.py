"""
AC Evo Shared Memory Decoder with Fallback
========================================
Tries AC/ACC structure first, falls back to pattern detection for AC Evo.

Ported from test_scripts/telemetry/ac_evo_decoder.py
"""

import struct
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from typing import Any, Dict, List, Optional


class AC_STATUS(Enum):
    AC_OFF = 0
    AC_REPLAY = 1
    AC_LIVE = 2
    AC_PAUSE = 3


class AC_SESSION_TYPE(Enum):
    AC_UNKNOWN = -1
    AC_PRACTICE = 0
    AC_QUALIFY = 1
    AC_RACE = 2
    AC_HOTLAP = 3
    AC_TIME_ATTACK = 4
    AC_DRIFT = 5
    AC_DRAG = 6


class AC_FLAG_TYPE(Enum):
    AC_NO_FLAG = 0
    AC_BLUE_FLAG = 1
    AC_YELLOW_FLAG = 2
    AC_BLACK_FLAG = 3
    AC_WHITE_FLAG = 4
    AC_CHECKERED_FLAG = 5
    AC_PENALTY_FLAG = 6


@dataclass
class Coords:
    x: float
    y: float
    z: float


@dataclass
class Physics:
    packet_id: int
    gas: float
    brake: float
    fuel: float
    gear: int
    rpms: int
    steer_angle: float
    speed_kmh: float
    velocity: Coords
    acc_g: Coords
    wheel_slip: List[float]
    wheel_load: List[float]
    wheels_pressure: List[float]
    wheel_angular_speed: List[float]
    tyre_wear: List[float]
    tyre_dirty_level: List[float]
    tyre_core_temp: List[float]
    camber_rad: List[float]
    suspension_travel: List[float]
    drs: float
    tc: float
    heading: float
    pitch: float
    roll: float
    cg_height: float
    car_damage: List[float]
    number_of_tyres_out: int
    pit_limiter_on: bool
    abs: float
    kers_charge: float
    kers_input: float
    auto_shifter_on: bool
    ride_height: List[float]
    turbo_boost: float
    ballast: float
    air_density: float
    air_temp: float
    road_temp: float
    local_angular_velocity: Coords
    final_ff: float
    performance_meter: float
    engine_brake: int
    ers_recovery_level: int
    ers_power_level: int
    ers_heat_charging: int
    ers_is_charging: int
    kers_current_kj: float
    drs_available: bool
    drs_enabled: bool
    brake_temp: List[float]
    clutch: float
    tyre_temp_i: List[float]
    tyre_temp_m: List[float]
    tyre_temp_o: List[float]
    is_ai_controlled: bool
    tyre_contact_point: List[Coords]
    tyre_contact_normal: List[Coords]
    tyre_contact_heading: List[Coords]
    brake_bias: float
    local_velocity: Coords


class R:
    """Binary reader for AC shared memory structures."""

    def __init__(self, data: bytes):
        self._b = BytesIO(data)
        self._pos = 0

    def i(self) -> int:
        val = struct.unpack("=i", self._b.read(4))[0]
        self._pos += 4
        return val

    def f(self) -> float:
        val = struct.unpack("=f", self._b.read(4))[0]
        self._pos += 4
        return val

    def fa(self, n: int) -> List[float]:
        vals = list(struct.unpack(f"={n}f", self._b.read(4 * n)))
        self._pos += 4 * n
        return vals

    def ia(self, n: int) -> List[int]:
        vals = list(struct.unpack(f"={n}i", self._b.read(4 * n)))
        self._pos += 4 * n
        return vals

    def coords(self) -> Coords:
        x, y, z = struct.unpack("=3f", self._b.read(12))
        self._pos += 12
        return Coords(x, y, z)

    def coords_list(self, n: int) -> List[Coords]:
        coords = []
        for _ in range(n):
            coords.append(self.coords())
        return coords

    def s(self, n: int, pad: int = 0) -> str:
        raw = self._b.read(2 * n + pad)
        self._pos += 2 * n + pad
        return raw[:2 * n].decode("utf-16-le", errors="ignore").rstrip("\x00")

    def skip(self, n: int):
        self._b.read(n)
        self._pos += n


def decode_physics_ac(data: bytes) -> Optional[Physics]:
    """Try to decode physics using AC/ACC structure."""
    try:
        r = R(data)
        return Physics(
            packet_id=r.i(),
            gas=r.f(),
            brake=r.f(),
            fuel=r.f(),
            gear=r.i(),
            rpms=r.i(),
            steer_angle=r.f(),
            speed_kmh=r.f(),
            velocity=r.coords(),
            acc_g=r.coords(),
            wheel_slip=r.fa(4),
            wheel_load=r.fa(4),
            wheels_pressure=r.fa(4),
            wheel_angular_speed=r.fa(4),
            tyre_wear=r.fa(4),
            tyre_dirty_level=r.fa(4),
            tyre_core_temp=r.fa(4),
            camber_rad=r.fa(4),
            suspension_travel=r.fa(4),
            drs=r.f(),
            tc=r.f(),
            heading=r.f(),
            pitch=r.f(),
            roll=r.f(),
            cg_height=r.f(),
            car_damage=r.fa(5),
            number_of_tyres_out=r.i(),
            pit_limiter_on=bool(r.i()),
            abs=r.f(),
            kers_charge=r.f(),
            kers_input=r.f(),
            auto_shifter_on=bool(r.i()),
            ride_height=r.fa(2),
            turbo_boost=r.f(),
            ballast=r.f(),
            air_density=r.f(),
            air_temp=r.f(),
            road_temp=r.f(),
            local_angular_velocity=r.coords(),
            final_ff=r.f(),
            performance_meter=r.f(),
            engine_brake=r.i(),
            ers_recovery_level=r.i(),
            ers_power_level=r.i(),
            ers_heat_charging=r.i(),
            ers_is_charging=r.i(),
            kers_current_kj=r.f(),
            drs_available=bool(r.i()),
            drs_enabled=bool(r.i()),
            brake_temp=r.fa(4),
            clutch=r.f(),
            tyre_temp_i=r.fa(4),
            tyre_temp_m=r.fa(4),
            tyre_temp_o=r.fa(4),
            is_ai_controlled=bool(r.i()),
            tyre_contact_point=r.coords_list(4),
            tyre_contact_normal=r.coords_list(4),
            tyre_contact_heading=r.coords_list(4),
            brake_bias=r.f(),
            local_velocity=r.coords(),
        )
    except Exception:
        return None


def decode_physics_fallback(data: bytes) -> Dict[str, Any]:
    """Fallback pattern detection for unknown structures."""
    result = {"_decoder": "fallback", "size": len(data)}

    floats = []
    ints = []

    for i in range(0, min(len(data), 200), 4):
        if i + 4 <= len(data):
            try:
                f = struct.unpack_from('<f', data, i)[0]
                if not (f != f or abs(f) > 1e6):
                    floats.append(round(f, 6))
            except Exception:
                break

    for i in range(0, min(len(data), 200), 4):
        if i + 4 <= len(data):
            try:
                val = struct.unpack_from('<i', data, i)[0]
                if abs(val) < 100000:
                    ints.append(val)
            except Exception:
                break

    result["floats"] = floats[:20]
    result["ints"] = ints[:20]
    result["raw_hex_start"] = data[:100].hex()

    return result


def decode_graphics_fallback(data: bytes) -> Dict[str, Any]:
    """Fallback pattern detection for graphics data."""
    result = {"_decoder": "fallback", "size": len(data)}

    try:
        ascii_part = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in data[:200])
        result["ascii_start"] = ascii_part
    except Exception:
        pass

    floats = []
    for i in range(0, min(len(data), 200), 4):
        if i + 4 <= len(data):
            try:
                f = struct.unpack_from('<f', data, i)[0]
                if not (f != f or abs(f) > 1e6):
                    floats.append(round(f, 6))
            except Exception:
                break

    result["floats"] = floats[:20]
    result["raw_hex_start"] = data[:100].hex()

    return result


def decode_static_fallback(data: bytes) -> Dict[str, Any]:
    """Fallback pattern detection for static data."""
    result = {"_decoder": "fallback", "size": len(data)}

    result["bytes"] = list(data[:100])
    result["ascii"] = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in data[:100])

    return result


def decode_physics(data: bytes) -> Dict[str, Any]:
    """Decode physics with AC/ACC structure fallback."""
    physics = decode_physics_ac(data)
    if physics:
        return {"_decoder": "ac_structure", **physics.__dict__}
    return decode_physics_fallback(data)


def decode_graphics(data: bytes) -> Dict[str, Any]:
    """Decode graphics with fallback."""
    return decode_graphics_fallback(data)


def decode_static(data: bytes) -> Dict[str, Any]:
    """Decode static with fallback."""
    return decode_static_fallback(data)


def physics_to_dict(physics_data: Any) -> Dict[str, Any]:
    """Convert physics data (dataclass or dict) to a flat dictionary."""
    if isinstance(physics_data, dict):
        return physics_data

    if hasattr(physics_data, "__dataclass_fields__"):
        result = {}
        for field, value in vars(physics_data).items():
            if isinstance(value, Coords):
                result[field] = {"x": value.x, "y": value.y, "z": value.z}
            elif isinstance(value, list):
                result[field] = value
            elif isinstance(value, (int, float, bool, str)):
                result[field] = value
            elif value is None:
                result[field] = None
            else:
                result[field] = str(value)
        return result

    return {"error": "Unknown physics data type"}


def graphics_to_dict(graphics_data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert graphics data to a flat dictionary."""
    if isinstance(graphics_data, dict):
        return graphics_data
    return {"error": "Unknown graphics data type"}


def static_to_dict(static_data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert static data to a flat dictionary."""
    if isinstance(static_data, dict):
        return static_data
    return {"error": "Unknown static data type"}
