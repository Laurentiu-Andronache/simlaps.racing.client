"""Shared utility functions extracted from telemetry_analyzer.py."""
import math
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from src.core.telemetry_capture import FrameData
from src.core.track_catalog import select_track_profile
from src.utils.structured_logger import log_debug, Component


# ── Constants ──────────────────────────────────────────────────────────────

# Quality-gate thresholds for the analyzer.
_AUTHORITATIVE_PROGRESS_THRESHOLD = 0.60
_PLAUSIBLE_FRAME_THRESHOLD = 0.66
_HIGH_PLAUSIBLE_FALLBACK = 0.95

# Fixed lap_progress measurement window for corner segment times.
_CORNER_MEASUREMENT_WINDOW_BEFORE = 0.015
_CORNER_MEASUREMENT_WINDOW_AFTER = 0.025


# ── Array / scalar helpers ─────────────────────────────────────────────────

def _safe_4(arr: list, default: float = 0.0) -> List[float]:
    """Safely extract 4-element array."""
    if not isinstance(arr, (list, tuple)):
        return [default, default, default, default]
    out = [default, default, default, default]
    for i in range(min(4, len(arr))):
        out[i] = arr[i]
    return out


def _sanitize_slip(v: Any) -> float:
    """Sanitize wheel slip value."""
    try:
        v = float(v)
    except Exception:
        return 0.0
    if not math.isfinite(v) or v < 0:
        return 0.0
    return min(v, 5.0)


def _optional_float(value: Any) -> Optional[float]:
    """Convert a value to a finite float or return None."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _fraction(points: List[Dict], predicate) -> float:
    """Return the fraction of points matching a predicate."""
    if not points:
        return 0.0
    return sum(1 for point in points if predicate(point)) / len(points)


def _median(values: List[float]) -> Optional[float]:
    clean = sorted(v for v in values if isinstance(v, (int, float)) and math.isfinite(v))
    if not clean:
        return None
    mid = len(clean) // 2
    if len(clean) % 2:
        return float(clean[mid])
    return float((clean[mid - 1] + clean[mid]) / 2.0)


def _interpolate_value(left: Dict, right: Dict, field: str, ratio: float) -> Optional[float]:
    """Linearly interpolate a scalar field between two samples."""
    left_value = _optional_float(left.get(field))
    right_value = _optional_float(right.get(field))
    if left_value is None and right_value is None:
        return None
    if left_value is None:
        return right_value
    if right_value is None:
        return left_value
    return left_value + (right_value - left_value) * ratio


def _median3(values: List[Optional[float]]) -> List[Optional[float]]:
    """Apply a 3-point median filter to a numeric series."""
    smoothed: List[Optional[float]] = []
    for idx in range(len(values)):
        window = [
            value
            for value in values[max(0, idx - 1):min(len(values), idx + 2)]
            if value is not None
        ]
        if not window:
            smoothed.append(None)
            continue
        window.sort()
        smoothed.append(window[len(window) // 2])
    return smoothed


def _local_average(points: List[Dict], center_idx: int, field: str, radius: int = 1) -> float:
    """Average a scalar field in a small local neighborhood."""
    values = [
        _optional_float(points[idx].get(field))
        for idx in range(max(0, center_idx - radius), min(len(points), center_idx + radius + 1))
    ]
    finite_values = [value for value in values if value is not None]
    if not finite_values:
        return 0.0
    return sum(finite_values) / len(finite_values)


# ── Frame data helpers ─────────────────────────────────────────────────────

def get_physics(frame: FrameData) -> Dict[str, Any]:
    """Get physics data from frame."""
    return frame.physics


def get_graphics(frame: FrameData) -> Dict[str, Any]:
    """Get graphics data from frame.

    Prefers the decoded ``frame.graphics`` payload (AC Evo
    ``SPageFileGraphicEvo`` via ``decode_graphics_evo``) when it carries
    authoritative track progress; falls back to physics-derived
    dead-reckoning values otherwise so older captures (taken before the
    graphics decoder landed) still analyze.
    """
    graphics = frame.graphics or {}
    if graphics.get("has_authoritative_progress") and graphics.get("normalized_car_position") is not None:
        return graphics

    # Fallback: physics dead-reckoning (legacy behaviour for old captures
    # without a decoded graphics region).
    physics = frame.physics or {}
    return {
        "normalized_car_position": physics.get("normalized_car_position"),
        "normalized_position_source": physics.get("normalized_position_source"),
        "has_authoritative_progress": physics.get("has_authoritative_progress", False),
        "completed_laps": graphics.get("completed_laps", 0),
        "current_time_ms": graphics.get("current_time_ms", 0),
        "last_time_ms": graphics.get("last_time_ms", 0),
        "best_time_ms": graphics.get("best_time_ms", 0),
        "is_valid_lap": graphics.get("is_valid_lap"),
        "is_in_pit_lane": graphics.get("is_in_pit_lane", False),
    }


# ── Analysis mode / confidence ────────────────────────────────────────────

def _decide_analysis_mode(
    authoritative_progress_ratio: float,
    plausible_frame_ratio: float,
) -> Tuple[str, bool, bool]:
    """Decide whether to run full coaching or the diagnostic stub."""
    has_authoritative = authoritative_progress_ratio >= _AUTHORITATIVE_PROGRESS_THRESHOLD
    has_high_plausible = plausible_frame_ratio >= _HIGH_PLAUSIBLE_FALLBACK
    mode = "full" if (has_authoritative or has_high_plausible) else "diagnostic"
    return mode, has_authoritative, has_high_plausible


def _confidence_label(score: float) -> str:
    """Convert a numeric confidence score into a label."""
    if score >= 0.8:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


# ── Profile sanity ────────────────────────────────────────────────────────

def _profile_corner_sanity_notes(laps: List[Dict]) -> List[str]:
    """Catch obviously shifted track-profile windows before coaching."""
    by_name: Dict[str, List[float]] = defaultdict(list)
    for lap in laps:
        for corner in lap.get("corners", []):
            name = (corner.get("name") or "").lower()
            apex = corner.get("apex_speed")
            if isinstance(apex, (int, float)) and math.isfinite(apex):
                by_name[name].append(float(apex))

    notes: List[str] = []
    for name, speeds in by_name.items():
        median_apex = _median(speeds)
        if median_apex is None:
            continue
        if "rettifilo" in name and median_apex > 190:
            notes.append(
                f"Track profile sanity check failed: {name} median apex is {median_apex:.0f} km/h, which is too fast for the first chicane."
            )
        if "curva grande" in name and median_apex < 120:
            notes.append(
                f"Track profile sanity check failed: {name} median apex is {median_apex:.0f} km/h, which is too slow for Curva Grande."
            )
    return notes


# ── Corner measurement window ─────────────────────────────────────────────

def _corner_measurement_window(spec: Dict[str, Any]) -> Tuple[float, float]:
    """Return a fixed lap_progress range centred on the corner profile."""
    center = (spec["start"] + spec["end"]) / 2.0
    return (
        max(spec["start"], center - _CORNER_MEASUREMENT_WINDOW_BEFORE),
        min(spec["end"], center + _CORNER_MEASUREMENT_WINDOW_AFTER),
    )


def _find_frame_index(track: List[Dict], frame: int) -> int:
    """Find the index in track list closest to a given frame number."""
    for i, pt in enumerate(track):
        if pt["frame"] >= frame:
            return i
    return len(track) - 1


def _trend_direction(values: List[float], threshold: float) -> str:
    """Classify a per-lap series as RISING / FALLING / FLAT."""
    if len(values) < 3:
        return "FLAT"
    diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    if all(d > threshold for d in diffs):
        return "RISING"
    if all(d < -threshold for d in diffs):
        return "FALLING"
    span = values[-1] - values[0]
    if span > threshold * (len(values) - 1):
        return "RISING"
    if span < -threshold * (len(values) - 1):
        return "FALLING"
    return "FLAT"


# ── Track profile selection ───────────────────────────────────────────────

def _select_track_profile_for_analysis(track_name: Optional[str]) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Resolve a track profile from the reported session track name."""
    if not track_name:
        return None, None
    track_key, track_profile = select_track_profile(track_name=track_name)
    if track_profile:
        return track_key, track_profile
    return select_track_profile(path=track_name)


# ── Car state extraction ──────────────────────────────────────────────────

def extract_car_state(pt: Dict) -> Optional[Dict]:
    """Extract car state data from a track point."""
    if not pt:
        return None
    return {
        "abs": pt.get("abs", 0),
        "tc": pt.get("tc", 0),
        "steer": pt.get("steer", 0),
        "speed": pt.get("speed", 0),
        "gas": pt.get("gas", 0),
        "brake": pt.get("brake", 0),
        "acc_g_x": pt.get("acc_g_x", 0),
        "acc_g_y": pt.get("acc_g_y", 0),
        "acc_g_z": pt.get("acc_g_z", 0),
        "yaw_rate": pt.get("yaw_rate", 0),
        "air_temp": pt.get("air_temp", 0),
        "road_temp": pt.get("road_temp", 0),
        "tyre_temp_fl": pt.get("tyre_temp_fl", 0),
        "tyre_temp_fr": pt.get("tyre_temp_fr", 0),
        "tyre_temp_rl": pt.get("tyre_temp_rl", 0),
        "tyre_temp_rr": pt.get("tyre_temp_rr", 0),
        "pressure_fl": pt.get("pressure_fl", 0),
        "pressure_fr": pt.get("pressure_fr", 0),
        "pressure_rl": pt.get("pressure_rl", 0),
        "pressure_rr": pt.get("pressure_rr", 0),
        "slip_fl": pt.get("slip_fl", 0),
        "slip_fr": pt.get("slip_fr", 0),
        "slip_rl": pt.get("slip_rl", 0),
        "slip_rr": pt.get("slip_rr", 0),
        "load_fl": pt.get("load_fl", 0),
        "load_fr": pt.get("load_fr", 0),
        "load_rl": pt.get("load_rl", 0),
        "load_rr": pt.get("load_rr", 0),
        "sus_fl": pt.get("sus_fl", 0),
        "sus_fr": pt.get("sus_fr", 0),
        "sus_rl": pt.get("sus_rl", 0),
        "sus_rr": pt.get("sus_rr", 0),
        "camber_fl": pt.get("camber_fl", 0),
        "camber_fr": pt.get("camber_fr", 0),
        "camber_rl": pt.get("camber_rl", 0),
        "camber_rr": pt.get("camber_rr", 0),
        "brake_temp_fl": pt.get("brake_temp_fl", 0),
        "brake_temp_fr": pt.get("brake_temp_fr", 0),
        "brake_temp_rl": pt.get("brake_temp_rl", 0),
        "brake_temp_rr": pt.get("brake_temp_rr", 0),
        # AC Evo precision fields
        "fx_fl": pt.get("fx_fl", 0),
        "fx_fr": pt.get("fx_fr", 0),
        "fx_rl": pt.get("fx_rl", 0),
        "fx_rr": pt.get("fx_rr", 0),
        "fy_fl": pt.get("fy_fl", 0),
        "fy_fr": pt.get("fy_fr", 0),
        "fy_rl": pt.get("fy_rl", 0),
        "fy_rr": pt.get("fy_rr", 0),
        "slip_ratio_fl": pt.get("slip_ratio_fl", 0),
        "slip_ratio_fr": pt.get("slip_ratio_fr", 0),
        "slip_ratio_rl": pt.get("slip_ratio_rl", 0),
        "slip_ratio_rr": pt.get("slip_ratio_rr", 0),
        "slip_angle_fl": pt.get("slip_angle_fl", 0),
        "slip_angle_fr": pt.get("slip_angle_fr", 0),
        "slip_angle_rl": pt.get("slip_angle_rl", 0),
        "slip_angle_rr": pt.get("slip_angle_rr", 0),
        "brake_torque_fl": pt.get("brake_torque_fl", 0),
        "brake_torque_fr": pt.get("brake_torque_fr", 0),
        "brake_torque_rl": pt.get("brake_torque_rl", 0),
        "brake_torque_rr": pt.get("brake_torque_rr", 0),
        "brake_bias": pt.get("brake_bias"),
        "water_temp": pt.get("water_temp"),
        "gear_rpm_window": pt.get("gear_rpm_window"),
        "rpm_percent": pt.get("rpm_percent"),
        "current_bhp": pt.get("current_bhp"),
        "current_torque": pt.get("current_torque"),
    }


# ── Classification / formatting helpers ───────────────────────────────────

def variation_label(delta_kmh: float) -> str:
    if delta_kmh >= 25:
        return "HIGH"
    if delta_kmh >= 15:
        return "MEDIUM"
    return "LOW"


def classify_corner_issue(entry_delta: float, apex_delta: float, exit_delta: float) -> str:
    """Heuristic: given speed deltas (best - worst) at entry/apex/exit, suggest root cause."""
    if entry_delta > apex_delta and entry_delta > exit_delta:
        return "Braking inconsistency — arriving at different speeds"
    if exit_delta > entry_delta and exit_delta > apex_delta:
        return "Throttle application point varies — losing drive on exit"
    if apex_delta > entry_delta and apex_delta > exit_delta:
        return "Line variation — mid-corner speed differs despite similar entry"
    return "Mixed — entry and exit both vary"


def format_car_state(state: Optional[Dict]) -> str:
    """Format car state (ABS, TC, temps, slip) for AI prompt."""
    if not state:
        return "No data"

    abs_active = "YES" if state.get("abs", 0) > 0.5 else "no"
    tc_active = "YES" if state.get("tc", 0) > 0.5 else "no"

    steer_rad = float(state.get("steer", 0) or 0)
    steer_deg = steer_rad * (180.0 / math.pi)
    yaw_rate = float(state.get("yaw_rate", 0) or 0)

    lat_g = float(state.get("acc_g_x", 0) or 0)
    long_g = float(state.get("acc_g_z", 0) or 0)

    temps = [float(state.get(f"tyre_temp_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    avg_temp = sum(temps) / len(temps) if temps else 0
    temp_range = f"{min(temps):.0f}-{max(temps):.0f}" if temps else "N/A"

    pressures = [float(state.get(f"pressure_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    avg_pressure = sum(pressures) / len(pressures) if pressures else 0

    slips = [float(state.get(f"slip_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    front_slip = max(slips[0], slips[1]) if len(slips) >= 2 else 0
    rear_slip = max(slips[2], slips[3]) if len(slips) >= 4 else 0

    loads = [float(state.get(f"load_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    front_load = (loads[0] + loads[1]) / 2 if len(loads) >= 2 else 0
    rear_load = (loads[2] + loads[3]) / 2 if len(loads) >= 4 else 0

    sus = [float(state.get(f"sus_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    front_sus = (sus[0] + sus[1]) / 2 if len(sus) >= 2 else 0
    rear_sus = (sus[2] + sus[3]) / 2 if len(sus) >= 4 else 0

    bt = [float(state.get(f"brake_temp_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    front_bt = (bt[0] + bt[1]) / 2 if len(bt) >= 2 else 0
    rear_bt = (bt[2] + bt[3]) / 2 if len(bt) >= 4 else 0

    # AC Evo precision fields
    fx_vals = [float(state.get(f"fx_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    fy_vals = [float(state.get(f"fy_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    slip_ratio_vals = [float(state.get(f"slip_ratio_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    slip_angle_vals = [float(state.get(f"slip_angle_{x}", 0) or 0) * (180.0 / math.pi) for x in ["fl", "fr", "rl", "rr"]]
    brake_torque_vals = [float(state.get(f"brake_torque_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]

    front_fx = (fx_vals[0] + fx_vals[1]) / 2 if len(fx_vals) >= 2 else 0
    rear_fx = (fx_vals[2] + fx_vals[3]) / 2 if len(fx_vals) >= 4 else 0
    front_fy = (fy_vals[0] + fy_vals[1]) / 2 if len(fy_vals) >= 2 else 0
    rear_fy = (fy_vals[2] + fy_vals[3]) / 2 if len(fy_vals) >= 4 else 0

    front_slip_ratio = (slip_ratio_vals[0] + slip_ratio_vals[1]) / 2 if len(slip_ratio_vals) >= 2 else 0
    rear_slip_ratio = (slip_ratio_vals[2] + slip_ratio_vals[3]) / 2 if len(slip_ratio_vals) >= 4 else 0
    front_slip_angle = (slip_angle_vals[0] + slip_angle_vals[1]) / 2 if len(slip_angle_vals) >= 2 else 0
    rear_slip_angle = (slip_angle_vals[2] + slip_angle_vals[3]) / 2 if len(slip_angle_vals) >= 4 else 0

    front_brake_torque = (brake_torque_vals[0] + brake_torque_vals[1]) / 2 if len(brake_torque_vals) >= 2 else 0
    rear_brake_torque = (brake_torque_vals[2] + brake_torque_vals[3]) / 2 if len(brake_torque_vals) >= 4 else 0

    brake_bias_val = state.get("brake_bias")
    brake_bias_str = f" BrakeBias:{brake_bias_val:.2f}" if brake_bias_val is not None and brake_bias_val > 0 else ""

    gear_rpm_window = state.get("gear_rpm_window")
    rpm_percent = state.get("rpm_percent")
    gear_str = ""
    if gear_rpm_window is not None and gear_rpm_window > 0:
        gear_str = f" GearOpt:{gear_rpm_window:.2f}"
    elif rpm_percent is not None and rpm_percent > 0:
        gear_str = f" RPM%:{rpm_percent:.1%}"

    precision_str = ""
    if abs(front_fx) + abs(rear_fx) + abs(front_fy) + abs(rear_fy) > 100:
        precision_str = (
            f" | Fx(F/R):{front_fx:.0f}/{rear_fx:.0f}N Fy(F/R):{front_fy:.0f}/{rear_fy:.0f}N"
            f" SlipRatio(F/R):{front_slip_ratio:.2f}/{rear_slip_ratio:.2f}"
            f" SlipAngle(F/R):{front_slip_angle:.1f}/{rear_slip_angle:.1f}deg"
        )

    brake_torque_str = ""
    if abs(front_brake_torque) + abs(rear_brake_torque) > 100:
        brake_torque_str = f" BrakeTq(F/R):{front_brake_torque:.0f}/{rear_brake_torque:.0f}Nm"

    drs_state = state.get("drs", 0)
    drs_enabled = state.get("drs_enabled", False)
    drs_str = ""
    if drs_enabled or drs_state > 0.5:
        drs_str = f" DRS:OPEN"
    elif state.get("drs_available", False):
        drs_str = f" DRS:AVAIL"

    return (
        f"ABS:{abs_active} TC:{tc_active} "
        f"Steer:{steer_deg:+.1f}deg Yaw:{yaw_rate:+.3f} "
        f"G(lat/long):{lat_g:+.2f}/{long_g:+.2f} "
        f"Slip(F/R):{front_slip:.2f}/{rear_slip:.2f} "
        f"Load(F/R):{front_load:.0f}/{rear_load:.0f} "
        f"Sus(F/R):{front_sus:.3f}/{rear_sus:.3f} "
        f"BrakeT(F/R):{front_bt:.0f}/{rear_bt:.0f} "
        f"TyreT:{avg_temp:.0f}C({temp_range}) "
        f"P:{avg_pressure:.1f}psi"
        f"{brake_bias_str}{gear_str}{brake_torque_str}{drs_str}{precision_str}"
    )


def balance_hint(state: Optional[Dict]) -> str:
    """Rough balance hint (understeer/oversteer/neutral) from per-point telemetry."""
    if not state:
        return "unknown"
    slips = [float(state.get(f"slip_{x}", 0) or 0) for x in ["fl", "fr", "rl", "rr"]]
    front_slip = max(slips[0], slips[1]) if len(slips) >= 2 else 0
    rear_slip = max(slips[2], slips[3]) if len(slips) >= 4 else 0
    steer = abs(float(state.get("steer", 0) or 0))
    yaw = abs(float(state.get("yaw_rate", 0) or 0))

    if front_slip > rear_slip * 1.15 and steer > 0.03 and yaw < 0.20:
        return "understeer"
    if rear_slip > front_slip * 1.15 and yaw > 0.25:
        return "oversteer"
    return "neutral"


# ── Internal average helper ───────────────────────────────────────────────

def _avg(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0
