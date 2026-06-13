"""
Telemetry Analyzer Module

Analyzes captured telemetry data and generates HTML reports and AI coaching prompts.
Based on test_scripts/telemetry/2-analyze.py
"""

import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.core.analyzer.ai_prompt import generate_ai_prompt
from src.core.analyzer.html_renderer import render_html
from src.core.telemetry_capture import CaptureMetadata, FrameData
from src.core.track_catalog import select_track_profile
from src.models import SharedSessionManager
from src.utils.structured_logger import log_debug, log_info, log_warning, log_error, log_exception, Component


@dataclass
class AnalysisResult:
    """Result of telemetry analysis."""
    html_path: Optional[str]
    ai_prompt_path: Optional[str]
    laps_detected: int
    best_lap_time: float
    track_name: Optional[str]


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


# Quality-gate thresholds for the analyzer. Documented here so the single
# source of truth lives next to the decision helper below.
_AUTHORITATIVE_PROGRESS_THRESHOLD = 0.60
_PLAUSIBLE_FRAME_THRESHOLD = 0.66
_HIGH_PLAUSIBLE_FALLBACK = 0.95

# Fixed lap_progress measurement window for corner segment times.
# Anchored on the track-profile centre so every lap is measured over
# the identical track section, eliminating line-dependent crossing jitter.
#
# Sanity check (5.8km track, 0.04 total progress window = 232m):
#   90 km/h -> 9.3s  (~93 frames @ 10Hz)
#   200 km/h -> 4.2s (~42 frames @ 10Hz)
#   300 km/h -> 2.8s (~28 frames @ 10Hz)
# All well above the 2-3s minimum needed for stable timing.
_CORNER_MEASUREMENT_WINDOW_BEFORE = 0.015
_CORNER_MEASUREMENT_WINDOW_AFTER = 0.025


def _decide_analysis_mode(
    authoritative_progress_ratio: float,
    plausible_frame_ratio: float,
) -> Tuple[str, bool, bool]:
    """Decide whether to run full coaching or the diagnostic stub.

    Ideally we have authoritative track progress from the graphics SHM
    region (>= 60% coverage). Until the AC Evo ``SPageFileGraphicEvo``
    decoder exists, live sessions fall back to physics-derived
    dead-reckoning ``normalized_car_position``. That signal is still
    accurate enough for lap-over-lap coaching when physics frames are
    consistently plausible across the whole capture (>= 95% coverage with
    ``frame_quality`` >= 0.66).

    Returns ``(mode, has_authoritative, has_high_plausible)`` — the two
    booleans are exposed so callers can choose tailored status messages
    without re-running the comparison.

    Once ``decode_graphics_evo`` lands and ``authoritative_progress_ratio``
    becomes the norm, the plausible-physics fallback degrades to a no-op.
    """
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


def _median(values: List[float]) -> Optional[float]:
    clean = sorted(v for v in values if isinstance(v, (int, float)) and math.isfinite(v))
    if not clean:
        return None
    mid = len(clean) // 2
    if len(clean) % 2:
        return float(clean[mid])
    return float((clean[mid - 1] + clean[mid]) / 2.0)


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


def _build_canonical_lap(lap_track: List[Dict], lap_start_frame: int, hz: float, bins: int = 200) -> Optional[Dict[str, Any]]:
    """Resample a lap onto a common progress grid."""
    samples: List[Dict[str, Any]] = []
    last_progress = None

    for pt in lap_track:
        progress = _optional_float(pt.get("norm_pos"))
        if progress is None or progress < 0.0 or progress > 1.0:
            continue
        if last_progress is not None and progress + 0.02 < last_progress:
            continue
        sample = dict(pt)
        sample["lap_progress"] = progress
        sample["lap_pos"] = progress
        sample["time_s"] = (pt["frame"] - lap_start_frame) / hz
        samples.append(sample)
        last_progress = progress

    if len(samples) < 8:
        return None

    progress_start = samples[0]["lap_progress"]
    progress_end = samples[-1]["lap_progress"]
    if progress_start > 0.10 or progress_end < 0.90:
        return None

    grid = [idx / max(bins - 1, 1) for idx in range(bins)]
    scalar_fields = [
        "frame",
        "time_s",
        "x",
        "z",
        "speed",
        "heading",
        "steer",
        "brake",
        "gas",
        "yaw_rate",
        "acc_g_x",
        "acc_g_y",
        "acc_g_z",
    ]
    canonical: List[Dict[str, Any]] = []
    cursor = 0

    for gp in grid:
        while cursor + 1 < len(samples) - 1 and samples[cursor + 1]["lap_progress"] < gp:
            cursor += 1

        left = samples[cursor]
        right = samples[min(cursor + 1, len(samples) - 1)]
        left_progress = left["lap_progress"]
        right_progress = right["lap_progress"]

        if gp < left_progress or gp > right_progress:
            continue

        ratio = 0.0 if right_progress <= left_progress else (gp - left_progress) / (right_progress - left_progress)
        nearest = left if ratio <= 0.5 else right
        point = dict(nearest)
        point["lap_progress"] = gp
        point["lap_pos"] = gp

        for field in scalar_fields:
            value = _interpolate_value(left, right, field, ratio)
            if field == "frame":
                point[field] = int(round(value)) if value is not None else nearest.get("frame")
            elif value is not None:
                point[field] = value

        canonical.append(point)

    if len(canonical) < 20:
        return None

    return {
        "samples": canonical,
        "progress_start": progress_start,
        "progress_end": progress_end,
        "source_samples": len(samples),
        "grid_bins": bins,
    }


def _detect_profiled_corners_canonical(
    canonical_track: List[Dict],
    profile: Dict[str, Any],
    hz: float,
    authoritative_progress: bool,
) -> List[Dict]:
    """Detect profiled corners on a canonical progress grid."""
    result = []
    for spec in profile.get("corners", []):
        window = [
            pt for pt in canonical_track
            if spec["start"] <= pt.get("lap_progress", -1.0) < spec["end"]
        ]
        if len(window) < 4:
            continue

        speed_series = [_optional_float(pt.get("speed")) for pt in window]
        smoothed_speed = _median3(speed_series)
        apex_candidates = [
            (idx, value)
            for idx, value in enumerate(smoothed_speed)
            if value is not None
        ]
        if not apex_candidates:
            continue

        apex_idx, apex_speed = min(apex_candidates, key=lambda item: item[1])
        entry_idx = 0
        for idx, pt in enumerate(window[:apex_idx + 1]):
            brake = _optional_float(pt.get("brake")) or 0.0
            steer = abs(_optional_float(pt.get("steer")) or 0.0)
            if brake >= 0.08 or steer >= 0.03:
                entry_idx = idx
                break

        # Ensure entry is distinct from apex — if they collided, back
        # entry up to the window start so we get a real speed delta.
        if entry_idx >= apex_idx and apex_idx > 0:
            entry_idx = 0

        exit_idx = len(window) - 1
        for idx in range(apex_idx + 1, len(window)):
            gas = _optional_float(window[idx].get("gas_percent", window[idx].get("gas"))) or 0.0
            if gas >= 0.20:
                exit_idx = idx
                break

        if exit_idx <= apex_idx:
            exit_idx = min(len(window) - 1, apex_idx + 1)
        # Ensure exit is distinct from apex
        if exit_idx == apex_idx and exit_idx < len(window) - 1:
            exit_idx = min(len(window) - 1, apex_idx + 1)

        entry = window[entry_idx]
        apex = window[apex_idx]
        exit_pt = window[exit_idx]
        valid_speed_ratio = sum(1 for value in speed_series if value is not None) / len(window)
        confidence = round(
            min(1.0, len(window) / 8.0) * 0.2
            + valid_speed_ratio * 0.4
            + (0.4 if authoritative_progress else 0.1),
            3,
        )

        # Measure segment time over a fixed lap_progress window so every lap
        # is evaluated on the identical track section.
        m_start, m_end = _corner_measurement_window(spec)
        measurement = [
            pt for pt in canonical_track
            if m_start <= pt.get("lap_progress", -1.0) < m_end
        ]
        if len(measurement) >= 2:
            segment_time_s = max(
                0.0,
                (_optional_float(measurement[-1].get("time_s")) or 0.0)
                - (_optional_float(measurement[0].get("time_s")) or 0.0),
            )
        else:
            segment_time_s = 0.0

        result.append({
            "id": spec["id"],
            "name": spec["name"],
            "start_frame": entry["frame"],
            "end_frame": exit_pt["frame"],
            "apex_frame": apex["frame"],
            "apex_speed": min(value for value in smoothed_speed[max(0, apex_idx - 1):min(len(window), apex_idx + 2)] if value is not None),
            "min_speed": min(value for value in speed_series if value is not None),
            "entry_speed": _local_average(window, entry_idx, "speed"),
            "exit_speed": _local_average(window, exit_idx, "speed"),
            "apex_x": _optional_float(apex.get("x")) or 0.0,
            "apex_z": _optional_float(apex.get("z")) or 0.0,
            "lap_pos": apex.get("lap_progress", spec["start"]),
            "segment_time_s": segment_time_s,
            "confidence": confidence,
            "confidence_label": _confidence_label(confidence),
            "entry_state": extract_car_state(entry),
            "apex_state": extract_car_state(apex),
            "exit_state": extract_car_state(exit_pt),
        })

    return result


def _select_track_profile_for_analysis(track_name: Optional[str]) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Resolve a track profile from the reported session track name."""
    if not track_name:
        return None, None

    track_key, track_profile = select_track_profile(track_name=track_name)
    if track_profile:
        return track_key, track_profile

    # Fallback to path-style substring matching for names like "circuit_de_spa_francorchamps gp".
    return select_track_profile(path=track_name)


def build_track(frames: List[FrameData], hz: float = 1.0, start_idx: int = 0) -> List[Dict]:
    """Build track map from frames."""
    track = []
    x = z = 0.0
    dt = 1.0 / hz

    for i in range(start_idx, len(frames)):
        f = frames[i]
        ph = get_physics(f)
        gr = get_graphics(f)
        if not ph or ph.get("is_plausible") is False:
            continue
        
        # Debug: Check graphics data on first frame
        if i == start_idx:
            has_graphics = bool(gr)
            has_auth_progress = gr.get("has_authoritative_progress", False) if gr else False
            norm_pos = gr.get("normalized_car_position") if gr else None
            log_debug(Component.ANALYZER, "Frame graphics check", frame=i, has_graphics=has_graphics, has_auth_progress=has_auth_progress, norm_pos=norm_pos)

        speed = _optional_float(ph.get("speed_kmh"))
        if speed is None:
            continue

        wp = ph.get("world_position") or ph.get("worldPosition")
        if wp and isinstance(wp, dict):
            wp_x = _optional_float(wp.get("x"))
            wp_z = _optional_float(wp.get("z"))
            if wp_x is not None:
                x = wp_x
            if wp_z is not None:
                z = wp_z
        else:
            velocity = ph.get("velocity", {})
            vx = _optional_float(velocity.get("x")) if isinstance(velocity, dict) else _optional_float(getattr(velocity, "x", None))
            vz = _optional_float(velocity.get("z")) if isinstance(velocity, dict) else _optional_float(getattr(velocity, "z", None))
            if vx is not None:
                x += vx * dt
            if vz is not None:
                z += vz * dt

        graphics_norm_pos = None
        if gr.get("has_authoritative_progress"):
            graphics_norm_pos = _optional_float(gr.get("normalized_car_position"))
            # Debug: Log graphics position on first frame
            if i == start_idx and graphics_norm_pos is not None:
                log_debug(Component.ANALYZER, "First frame graphics normalized_position", norm_pos=graphics_norm_pos)

        physics_norm_pos = _optional_float(
            ph.get("normalized_spline_position")
            or ph.get("spNormalizedCarPosition")
            or ph.get("normalizedCarPosition")
            or ph.get("normalized_car_position")
        )

        norm_pos = graphics_norm_pos if graphics_norm_pos is not None else physics_norm_pos
        progress_source = "graphics" if graphics_norm_pos is not None else "physics" if physics_norm_pos is not None else None
        physics_quality = _optional_float(ph.get("quality_score")) or 0.0
        graphics_quality = _optional_float(gr.get("quality_score"))
        frame_quality = physics_quality if progress_source != "graphics" or graphics_quality is None else min(physics_quality, graphics_quality)

        tyre_core_temp = _safe_4(ph.get("tyre_core_temp", []), default=0.0)
        wheels_pressure = _safe_4(ph.get("wheels_pressure", []), default=0.0)
        wheel_slip_raw = _safe_4(ph.get("wheel_slip", []), default=0.0)
        wheel_slip = [_sanitize_slip(v) for v in wheel_slip_raw]
        wheel_load = _safe_4(ph.get("wheel_load", []), default=0.0)
        suspension_travel = _safe_4(ph.get("suspension_travel", []), default=0.0)
        camber_rad = _safe_4(ph.get("camber_rad", []), default=0.0)
        brake_temp = _safe_4(ph.get("brake_temp", []), default=0.0)
        # Tyre wear (0.0 fresh -> 1.0 worn) and dirt level for grip-degradation analysis.
        tyre_wear = _safe_4(ph.get("tyre_wear", []), default=0.0)
        tyre_dirty_level = _safe_4(ph.get("tyre_dirty_level", []), default=0.0)
        
        # AC Evo precision fields
        fx = _safe_4(ph.get("fx", []), default=0.0)
        fy = _safe_4(ph.get("fy", []), default=0.0)
        slip_ratio = _safe_4(ph.get("slip_ratio", []), default=0.0)
        slip_angle = _safe_4(ph.get("slip_angle", []), default=0.0)
        brake_torque = _safe_4(ph.get("brake_torque", []), default=0.0)

        acc_g = ph.get("acc_g", {}) or {}
        local_ang_vel = ph.get("local_angular_velocity", {}) or {}

        if isinstance(acc_g, dict):
            acc_g_x = acc_g.get("x", 0)
            acc_g_y = acc_g.get("y", 0)
            acc_g_z = acc_g.get("z", 0)
        else:
            acc_g_x = acc_g_y = acc_g_z = 0

        if isinstance(local_ang_vel, dict):
            yaw_rate = local_ang_vel.get("y", 0)
        else:
            yaw_rate = 0

        track.append({
            "frame": i,
            "x": x,
            "z": z,
            "speed": speed,
            "heading": _optional_float(ph.get("heading")) or 0.0,
            "steer": _optional_float(ph.get("steer_angle")) or 0.0,
            "brake": _optional_float(ph.get("brake")) or 0.0,
            "gas": _optional_float(ph.get("gas")) or 0.0,
            "gear": ph.get("gear", 0) or 0,
            "rpms": ph.get("rpms", 0) or 0,
            "norm_pos": float(norm_pos) if norm_pos is not None else None,
            "progress_source": progress_source,
            "has_authoritative_progress": progress_source == "graphics",
            "physics_quality": physics_quality,
            "graphics_quality": graphics_quality,
            "frame_quality": frame_quality,
            "abs": _optional_float(ph.get("abs")) or 0.0,
            "absin_action": ph.get("absin_action", False),
            "tc": _optional_float(ph.get("tc")) or 0.0,
            "drs": _optional_float(ph.get("drs")) or 0.0,
            "drs_available": ph.get("drs_available", False),
            "drs_enabled": ph.get("drs_enabled", False),
            "acc_g_x": acc_g_x,
            "acc_g_y": acc_g_y,
            "acc_g_z": acc_g_z,
            "yaw_rate": yaw_rate,
            "air_temp": _optional_float(ph.get("air_temp")) or 0.0,
            "road_temp": _optional_float(ph.get("road_temp")) or 0.0,
            "completed_laps": gr.get("completed_laps"),
            "current_sector_index": gr.get("current_sector_index"),
            "is_valid_lap": gr.get("is_valid_lap"),
            "is_in_pit": gr.get("is_in_pit"),
            "is_in_pit_lane": gr.get("is_in_pit_lane"),
            "distance_traveled": gr.get("distance_traveled"),
            "lap_time_ms": gr.get("current_time_ms"),
            "last_lap_time_ms": gr.get("last_time_ms"),
            "best_lap_time_ms": gr.get("best_time_ms"),
            "tyre_temp_fl": tyre_core_temp[0] if len(tyre_core_temp) > 0 else 0,
            "tyre_temp_fr": tyre_core_temp[1] if len(tyre_core_temp) > 1 else 0,
            "tyre_temp_rl": tyre_core_temp[2] if len(tyre_core_temp) > 2 else 0,
            "tyre_temp_rr": tyre_core_temp[3] if len(tyre_core_temp) > 3 else 0,
            "pressure_fl": wheels_pressure[0] if len(wheels_pressure) > 0 else 0,
            "pressure_fr": wheels_pressure[1] if len(wheels_pressure) > 1 else 0,
            "pressure_rl": wheels_pressure[2] if len(wheels_pressure) > 2 else 0,
            "pressure_rr": wheels_pressure[3] if len(wheels_pressure) > 3 else 0,
            "slip_fl": wheel_slip[0] if len(wheel_slip) > 0 else 0,
            "slip_fr": wheel_slip[1] if len(wheel_slip) > 1 else 0,
            "slip_rl": wheel_slip[2] if len(wheel_slip) > 2 else 0,
            "slip_rr": wheel_slip[3] if len(wheel_slip) > 3 else 0,
            "load_fl": wheel_load[0] if len(wheel_load) > 0 else 0,
            "load_fr": wheel_load[1] if len(wheel_load) > 1 else 0,
            "load_rl": wheel_load[2] if len(wheel_load) > 2 else 0,
            "load_rr": wheel_load[3] if len(wheel_load) > 3 else 0,
            "sus_fl": suspension_travel[0] if len(suspension_travel) > 0 else 0,
            "sus_fr": suspension_travel[1] if len(suspension_travel) > 1 else 0,
            "sus_rl": suspension_travel[2] if len(suspension_travel) > 2 else 0,
            "sus_rr": suspension_travel[3] if len(suspension_travel) > 3 else 0,
            "camber_fl": camber_rad[0] if len(camber_rad) > 0 else 0,
            "camber_fr": camber_rad[1] if len(camber_rad) > 1 else 0,
            "camber_rl": camber_rad[2] if len(camber_rad) > 2 else 0,
            "camber_rr": camber_rad[3] if len(camber_rad) > 3 else 0,
            "brake_temp_fl": brake_temp[0] if len(brake_temp) > 0 else 0,
            "brake_temp_fr": brake_temp[1] if len(brake_temp) > 1 else 0,
            "brake_temp_rl": brake_temp[2] if len(brake_temp) > 2 else 0,
            "brake_temp_rr": brake_temp[3] if len(brake_temp) > 3 else 0,
            # Tyre wear (0.0 fresh -> 1.0 worn) — used for stint grip-degradation analysis.
            "tyre_wear_fl": tyre_wear[0] if len(tyre_wear) > 0 else 0.0,
            "tyre_wear_fr": tyre_wear[1] if len(tyre_wear) > 1 else 0.0,
            "tyre_wear_rl": tyre_wear[2] if len(tyre_wear) > 2 else 0.0,
            "tyre_wear_rr": tyre_wear[3] if len(tyre_wear) > 3 else 0.0,
            "tyre_dirty_fl": tyre_dirty_level[0] if len(tyre_dirty_level) > 0 else 0.0,
            "tyre_dirty_fr": tyre_dirty_level[1] if len(tyre_dirty_level) > 1 else 0.0,
            "tyre_dirty_rl": tyre_dirty_level[2] if len(tyre_dirty_level) > 2 else 0.0,
            "tyre_dirty_rr": tyre_dirty_level[3] if len(tyre_dirty_level) > 3 else 0.0,
            # AC Evo precision fields
            "fx_fl": fx[0] if len(fx) > 0 else 0,
            "fx_fr": fx[1] if len(fx) > 1 else 0,
            "fx_rl": fx[2] if len(fx) > 2 else 0,
            "fx_rr": fx[3] if len(fx) > 3 else 0,
            "fy_fl": fy[0] if len(fy) > 0 else 0,
            "fy_fr": fy[1] if len(fy) > 1 else 0,
            "fy_rl": fy[2] if len(fy) > 2 else 0,
            "fy_rr": fy[3] if len(fy) > 3 else 0,
            "slip_ratio_fl": slip_ratio[0] if len(slip_ratio) > 0 else 0,
            "slip_ratio_fr": slip_ratio[1] if len(slip_ratio) > 1 else 0,
            "slip_ratio_rl": slip_ratio[2] if len(slip_ratio) > 2 else 0,
            "slip_ratio_rr": slip_ratio[3] if len(slip_ratio) > 3 else 0,
            "slip_angle_fl": slip_angle[0] if len(slip_angle) > 0 else 0,
            "slip_angle_fr": slip_angle[1] if len(slip_angle) > 1 else 0,
            "slip_angle_rl": slip_angle[2] if len(slip_angle) > 2 else 0,
            "slip_angle_rr": slip_angle[3] if len(slip_angle) > 3 else 0,
            "brake_torque_fl": brake_torque[0] if len(brake_torque) > 0 else 0,
            "brake_torque_fr": brake_torque[1] if len(brake_torque) > 1 else 0,
            "brake_torque_rl": brake_torque[2] if len(brake_torque) > 2 else 0,
            "brake_torque_rr": brake_torque[3] if len(brake_torque) > 3 else 0,
            "brake_bias": _optional_float(ph.get("brake_bias")),
            "engine_brake": ph.get("engine_brake", 0),
            "water_temp": _optional_float(ph.get("water_temp")),
            "air_density": _optional_float(ph.get("air_density")),
            "air_temp": _optional_float(ph.get("air_temp")),
            "road_temp": _optional_float(ph.get("road_temp")),
            # Graphics-sourced performance fields
            "gear_rpm_window": _optional_float(gr.get("gear_rpm_window")),
            "predicted_lap_time_ms": gr.get("predicted_lap_time_ms"),
            "delta_time_ms": gr.get("delta_time_ms"),
            "current_bhp": gr.get("current_bhp"),
            "current_torque": _optional_float(gr.get("current_torque")),
            "rpm_percent": _optional_float(gr.get("rpm_percent")),
            "gas_percent": _optional_float(gr.get("gas_percent")),
            "brake_percent": _optional_float(gr.get("brake_percent")),
            "clutch_percent": _optional_float(gr.get("clutch_percent")),
            "steering_percent": _optional_float(gr.get("steering_percent")),
            "turbo_boost": _optional_float(gr.get("turbo_boost")),
            "turbo_boost_perc": _optional_float(gr.get("turbo_boost_perc")),
            # Electronics / aids from Graphics SHM SMEvoElectronics (None if buffer too small)
            "tc_level": gr.get("electronics_tc_level"),
            "abs_level": gr.get("electronics_abs_level"),
            "engine_map_level": gr.get("electronics_engine_map"),
            "diff_power_level": gr.get("electronics_diff_power"),
            "diff_coast_level": gr.get("electronics_diff_coast"),
            "front_bump_damper": gr.get("electronics_front_bump_damper"),
            "front_rebound_damper": gr.get("electronics_front_rebound_damper"),
            "rear_bump_damper": gr.get("electronics_rear_bump_damper"),
            "rear_rebound_damper": gr.get("electronics_rear_rebound_damper"),
            "electronics_perf_mode": gr.get("electronics_perf_mode"),
            "electronics_pitlimiter_on": gr.get("electronics_pitlimiter_on"),
            # Electronics limits (min/max) from Graphics SHM
            "tc_level_min": gr.get("electronics_tc_level_min"),
            "abs_level_min": gr.get("electronics_abs_level_min"),
            "brake_bias_min": gr.get("electronics_brake_bias_min"),
            "engine_map_min": gr.get("electronics_engine_map_min"),
            "diff_power_min": gr.get("electronics_diff_power_min"),
            "diff_coast_min": gr.get("electronics_diff_coast_min"),
            "front_bump_damper_min": gr.get("electronics_front_bump_damper_min"),
            "front_rebound_damper_min": gr.get("electronics_front_rebound_damper_min"),
            "rear_bump_damper_min": gr.get("electronics_rear_bump_damper_min"),
            "rear_rebound_damper_min": gr.get("electronics_rear_rebound_damper_min"),
            "perf_mode_min": gr.get("electronics_perf_mode_min"),
            "tc_level_max": gr.get("electronics_tc_level_max"),
            "abs_level_max": gr.get("electronics_abs_level_max"),
            "brake_bias_max": gr.get("electronics_brake_bias_max"),
            "engine_map_max": gr.get("electronics_engine_map_max"),
            "diff_power_max": gr.get("electronics_diff_power_max"),
            "diff_coast_max": gr.get("electronics_diff_coast_max"),
            "front_bump_damper_max": gr.get("electronics_front_bump_damper_max"),
            "front_rebound_damper_max": gr.get("electronics_front_rebound_damper_max"),
            "rear_bump_damper_max": gr.get("electronics_rear_bump_damper_max"),
            "rear_rebound_damper_max": gr.get("electronics_rear_rebound_damper_max"),
            "perf_mode_max": gr.get("electronics_perf_mode_max"),
            # Electronics modifiable flags from Graphics SHM
            "tc_level_modifiable": gr.get("electronics_tc_level_modifiable"),
            "abs_level_modifiable": gr.get("electronics_abs_level_modifiable"),
            "brake_bias_modifiable": gr.get("electronics_brake_bias_modifiable"),
            "engine_map_modifiable": gr.get("electronics_engine_map_modifiable"),
            "diff_power_modifiable": gr.get("electronics_diff_power_modifiable"),
            "diff_coast_modifiable": gr.get("electronics_diff_coast_modifiable"),
            "pitlimiter_modifiable": gr.get("electronics_pitlimiter_modifiable"),
            "perf_mode_modifiable": gr.get("electronics_perf_mode_modifiable"),
        })
    return track


def _detect_laps_by_norm_pos(track: List[Dict], hz: float = 1.0) -> Optional[List[int]]:
    """Detect laps using normalized spline position."""
    # Use a conservative minimum based on sample rate - 10 frames at 10Hz = 1 second minimum
    # This prevents false positives from noise in the data
    min_lap_frames = max(10, int(round(1.0 * hz)))
    boundaries = []
    prev_norm = None

    for pt in track:
        norm = pt.get("norm_pos")
        if norm is None:
            return None
        if prev_norm is not None and prev_norm > 0.92 and norm < 0.08:
            frame = pt["frame"]
            if not boundaries or (frame - boundaries[-1]) >= min_lap_frames:
                boundaries.append(frame)
        prev_norm = norm

    return boundaries if len(boundaries) >= 2 else None


def _detect_laps_by_position(track: List[Dict], hz: float = 1.0, warmup_time_s: float = 40.0) -> List[int]:
    """Fallback lap detection using position."""
    # Use a conservative minimum based on sample rate - 10 frames at 10Hz = 1 second minimum
    # This prevents false positives from noise in the data
    min_lap_frames = max(10, int(round(1.0 * hz)))
    warmup_frames = max(0, int(round(warmup_time_s * hz)))

    ref_pt = None
    for pt in track[warmup_frames:]:
        if pt["speed"] > 80 and abs(pt["steer"]) < 0.05:
            ref_pt = pt
            break
    if ref_pt is None:
        ref_pt = track[min(warmup_frames, len(track) - 1)]

    ref_x, ref_z = ref_pt["x"], ref_pt["z"]
    boundaries = [ref_pt["frame"]]

    for pt in track:
        if pt["frame"] <= ref_pt["frame"] + min_lap_frames:
            continue
        dx = pt["x"] - ref_x
        dz = pt["z"] - ref_z
        dist = math.sqrt(dx * dx + dz * dz)
        if dist < 20 and pt["speed"] > 20:
            if (pt["frame"] - boundaries[-1]) >= min_lap_frames:
                boundaries.append(pt["frame"])

    return boundaries


def _detect_laps_by_timing_state(track: List[Dict], hz: float = 1.0) -> Optional[List[int]]:
    """Detect laps using shared memory timing state (last_laptime_ms updates)."""
    min_lap_frames = max(10, int(round(1.0 * hz)))
    boundaries = []
    prev_last_laptime = None
    
    for pt in track:
        last_laptime = pt.get("last_laptime_ms")
        if last_laptime is None:
            continue
            
        # Detect when last_laptime changes (lap completion event)
        if prev_last_laptime is not None and last_laptime != prev_last_laptime:
            frame = pt["frame"]
            if not boundaries or (frame - boundaries[-1]) >= min_lap_frames:
                boundaries.append(frame)
        prev_last_laptime = last_laptime
    
    return boundaries if len(boundaries) >= 1 else None


def detect_laps(track: List[Dict], hz: float = 1.0, allow_position_fallback: bool = True) -> List[int]:
    """Detect lap boundaries."""
    norm_result = _detect_laps_by_norm_pos(track, hz=hz)
    if norm_result:
        log_debug(Component.ANALYZER, "Lap detection: using normalized car position")
        return norm_result

    if not allow_position_fallback:
        log_debug(Component.ANALYZER, "Lap detection: normalized progress unavailable")
        return []

    log_debug(Component.ANALYZER, "Lap detection: using dead-reckoning position")
    return _detect_laps_by_position(track, hz=hz)


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


def detect_corners(track: List[Dict], lap_start_frame: int, lap_end_frame: int, hz: float = 1.0) -> List[Dict]:
    """Identify corners within a lap segment."""
    dheading_rate_thresh = 0.60
    merge_gap_s = 0.6
    min_dur_s = 0.8

    merge_gap = max(1, int(round(merge_gap_s * hz)))
    min_dur = max(1, int(round(min_dur_s * hz)))

    seg = [dict(pt) for pt in track if lap_start_frame <= pt["frame"] < lap_end_frame]
    if len(seg) < 4:
        return []

    n = max(len(seg) - 1, 1)
    for idx, pt in enumerate(seg):
        pt["lap_pos"] = idx / n

    corner_flags = [False]
    for i in range(1, len(seg)):
        dh = seg[i]["heading"] - seg[i - 1]["heading"]
        dh = (dh + math.pi) % (2 * math.pi) - math.pi
        corner_flags.append(abs(dh) * hz > dheading_rate_thresh)

    in_corner = False
    corners = []
    cur_start = None
    gap = 0
    for i, flag in enumerate(corner_flags):
        if flag:
            if not in_corner:
                in_corner = True
                cur_start = i
            gap = 0
        else:
            if in_corner:
                gap += 1
                if gap > merge_gap:
                    corners.append((cur_start, i - gap))
                    in_corner = False
                    gap = 0
    if in_corner:
        corners.append((cur_start, len(seg) - 1))

    result = []
    for cid, (ci_start, ci_end) in enumerate(corners):
        dur = ci_end - ci_start + 1
        if dur < min_dur:
            continue
        window = seg[ci_start:ci_end + 1]
        apex_idx = min(range(len(window)), key=lambda i: window[i]["speed"])
        apex = window[apex_idx]
        entry = window[0]
        exit_pt = window[-1]

        # Average entry/exit speeds over a few frames to reduce
        # single-point jitter on braking zones and acceleration zones.
        _N_AVG = min(3, max(1, len(window) // 3))
        entry_speed = sum(pt["speed"] for pt in window[:_N_AVG]) / _N_AVG
        exit_speed = sum(pt["speed"] for pt in window[-_N_AVG:]) / _N_AVG

        result.append({
            "id": cid,
            "start_frame": seg[ci_start]["frame"],
            "end_frame": seg[ci_end]["frame"],
            "apex_frame": apex["frame"],
            "apex_speed": apex["speed"],
            "min_speed": min(pt["speed"] for pt in window),
            "entry_speed": entry_speed,
            "exit_speed": exit_speed,
            "apex_x": apex["x"],
            "apex_z": apex["z"],
            "lap_pos": seg[ci_start]["lap_pos"],
            "entry_state": extract_car_state(entry),
            "apex_state": extract_car_state(apex),
            "exit_state": extract_car_state(exit_pt),
        })

    for i, c in enumerate(result):
        c["id"] = i + 1

    return result


def detect_profiled_corners(track: List[Dict], lap_start_frame: int, lap_end_frame: int, profile: Dict[str, Any], hz: float = 10.0) -> List[Dict]:
    """Detect corners using predefined track profile windows."""
    seg = [dict(pt) for pt in track if lap_start_frame <= pt["frame"] < lap_end_frame]
    if not seg:
        return []

    has_norm_pos = seg[0].get("norm_pos") is not None
    n = max(len(seg) - 1, 1)
    for idx, pt in enumerate(seg):
        pt["lap_pos"] = pt["norm_pos"] if has_norm_pos else idx / n

    result = []
    for spec in profile.get("corners", []):
        window = [pt for pt in seg if spec["start"] <= pt["lap_pos"] < spec["end"]]
        if not window:
            continue

        apex = min(window, key=lambda pt: pt["speed"])
        entry = window[0]
        exit_pt = window[-1]

        # Average entry/exit speeds over a few frames to reduce
        # single-point jitter on braking zones and acceleration zones.
        _N_AVG = min(3, max(1, len(window) // 3))
        entry_speed = sum(pt["speed"] for pt in window[:_N_AVG]) / _N_AVG
        exit_speed = sum(pt["speed"] for pt in window[-_N_AVG:]) / _N_AVG

        # Measure segment time over a fixed lap_progress window so every lap
        # is evaluated on the identical track section.
        m_start, m_end = _corner_measurement_window(spec)
        if has_norm_pos:
            measurement = [pt for pt in seg if m_start <= pt["lap_pos"] < m_end]
            if len(measurement) >= 2:
                segment_time_s = (measurement[-1]["frame"] - measurement[0]["frame"]) / hz
                confidence = 0.5
            else:
                segment_time_s = None
                confidence = 0.3
            confidence_label = _confidence_label(confidence)
        else:
            segment_time_s = None
            confidence = 0.0
            confidence_label = "low"

        result.append({
            "id": spec["id"],
            "name": spec["name"],
            "start_frame": entry["frame"],
            "end_frame": exit_pt["frame"],
            "apex_frame": apex["frame"],
            "apex_speed": apex["speed"],
            "min_speed": min(pt["speed"] for pt in window),
            "entry_speed": entry_speed,
            "exit_speed": exit_speed,
            "apex_x": apex["x"],
            "apex_z": apex["z"],
            "lap_pos": apex["lap_pos"],
            "segment_time_s": segment_time_s,
            "confidence": confidence,
            "confidence_label": confidence_label,
            "entry_state": extract_car_state(entry),
            "apex_state": extract_car_state(apex),
            "exit_state": extract_car_state(exit_pt),
        })

    return result


def match_profiled_corners(ref_corners: List[Dict], lap_corners: List[Dict]) -> Dict[int, Optional[Dict]]:
    """Match profiled corners by stable corner id."""
    lap_by_id = {corner["id"]: corner for corner in lap_corners}
    return {ref_corner["id"]: lap_by_id.get(ref_corner["id"]) for ref_corner in ref_corners}


def match_corners(ref_corners: List[Dict], lap_corners: List[Dict], tol: float = 0.15) -> Dict:
    """Sequential nearest-neighbor corner matching."""
    matched = {}
    last_idx = 0
    for ref_corner in ref_corners:
        best = None
        best_dist = tol
        for i in range(last_idx, len(lap_corners)):
            dist = abs(lap_corners[i]["lap_pos"] - ref_corner["lap_pos"])
            if dist < best_dist:
                best_dist = dist
                best = lap_corners[i]
                last_idx = i
        matched[ref_corner["id"]] = best
    return matched


def _corner_measurement_window(spec: Dict[str, Any]) -> Tuple[float, float]:
    """Return a fixed lap_progress range centred on the corner profile.

    The window is clamped to the corner profile bounds so that braking
    zones and adjacent straights never inflate the segment time delta.
    """
    center = (spec["start"] + spec["end"]) / 2.0
    return (
        max(spec["start"], center - _CORNER_MEASUREMENT_WINDOW_BEFORE),
        min(spec["end"], center + _CORNER_MEASUREMENT_WINDOW_AFTER),
    )


def corner_segment_time(corner: Dict, hz: float) -> float:
    """Seconds elapsed from corner start_frame to end_frame."""
    if corner.get("segment_time_s") is not None:
        return float(corner["segment_time_s"])
    return (corner["end_frame"] - corner["start_frame"]) / hz


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
    slip_angle_vals = [float(state.get(f"slip_angle_{x}", 0) or 0) * (180.0 / math.pi) for x in ["fl", "fr", "rl", "rr"]]  # Convert to degrees
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
    
    # Build precision data string if any tire force data is present
    precision_str = ""
    if abs(front_fx) + abs(rear_fx) + abs(front_fy) + abs(rear_fy) > 100:  # Only show if meaningful values
        precision_str = (
            f" | Fx(F/R):{front_fx:.0f}/{rear_fx:.0f}N Fy(F/R):{front_fy:.0f}/{rear_fy:.0f}N"
            f" SlipRatio(F/R):{front_slip_ratio:.2f}/{rear_slip_ratio:.2f}"
            f" SlipAngle(F/R):{front_slip_angle:.1f}/{rear_slip_angle:.1f}deg"
        )
    
    brake_torque_str = ""
    if abs(front_brake_torque) + abs(rear_brake_torque) > 100:
        brake_torque_str = f" BrakeTq(F/R):{front_brake_torque:.0f}/{rear_brake_torque:.0f}Nm"

    # DRS status
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
    """Rough balance hint (understeer/oversteer/neutral) from per-point telemetry.

    Derived from front-vs-rear wheel slip ratio, steering angle, and yaw rate:
    - understeer: front slip > rear slip * 1.15 with meaningful steering but low yaw
    - oversteer: rear slip > front slip * 1.15 with significant yaw rate
    - neutral: neither condition met
    """
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


def _find_frame_index(track: List[Dict], frame: int) -> int:
    """Find the index in track list closest to a given frame number."""
    for i, pt in enumerate(track):
        if pt["frame"] >= frame:
            return i
    return len(track) - 1


def analyze_corner_phases(
    track: List[Dict],
    corner: Dict,
    lap_start_frame: int,
    hz: float,
    approach_seconds: float = 3.0,
    exit_seconds: float = 2.0,
) -> Optional[Dict]:
    """Analyze brake, turn-in, and throttle timing around a corner.

    Scans the approach zone (before corner start) and exit zone (after apex)
    to find:
      - brake_onset_dt: seconds before corner entry that brake > threshold
      - turn_in_dt: seconds before corner entry that |steer| exceeds threshold
      - gas_on_dt: seconds after apex that throttle > threshold
      - trail_brake_frames: how many frames from entry to apex still have brake > 0.05
      - coast_frames: frames near apex with both gas < 0.1 and brake < 0.1

    Returns None if there isn't enough data.
    """
    BRAKE_THRESH = 0.10
    STEER_THRESH = 0.03  # ~1.7 degrees
    GAS_THRESH = 0.15

    corner_start = corner["start_frame"]
    apex_frame = corner["apex_frame"]
    corner_end = corner["end_frame"]

    approach_frames = int(approach_seconds * hz)
    exit_frames = int(exit_seconds * hz)

    # Get approach zone: frames leading up to corner entry
    approach_start = max(lap_start_frame, corner_start - approach_frames)
    approach = [pt for pt in track if approach_start <= pt["frame"] < corner_start]
    # Corner zone: entry to exit
    corner_zone = [pt for pt in track if corner_start <= pt["frame"] <= corner_end]
    # Exit zone: from apex onward
    exit_zone = [pt for pt in track if apex_frame <= pt["frame"] <= corner_end + exit_frames]

    if len(approach) < 3 or len(corner_zone) < 3:
        return None

    # ── Brake onset: scan approach backwards from corner entry to find first brake
    brake_onset_dt = None
    for pt in reversed(approach):
        if pt.get("brake", 0) >= BRAKE_THRESH:
            brake_onset_dt = (corner_start - pt["frame"]) / hz
        else:
            if brake_onset_dt is not None:
                break  # Found the start of the braking zone

    # If brake was already applied at approach start, mark it
    if brake_onset_dt is None:
        # Check if braking at corner entry
        if corner_zone and corner_zone[0].get("brake", 0) >= BRAKE_THRESH:
            brake_onset_dt = 0.0

    # ── Turn-in: scan approach backwards to find steer onset
    turn_in_dt = None
    for pt in reversed(approach):
        if abs(pt.get("steer", 0)) >= STEER_THRESH:
            turn_in_dt = (corner_start - pt["frame"]) / hz
        else:
            if turn_in_dt is not None:
                break

    if turn_in_dt is None:
        if corner_zone and abs(corner_zone[0].get("steer", 0)) >= STEER_THRESH:
            turn_in_dt = 0.0

    # ── Gas-on: scan from apex forward to find throttle application
    # Prefer graphics gas_percent over physics gas for better data quality
    gas_on_dt = None
    for pt in exit_zone:
        gas_val = pt.get("gas_percent", pt.get("gas", 0))
        if gas_val >= GAS_THRESH:
            gas_on_dt = (pt["frame"] - apex_frame) / hz
            break

    # ── Trail braking: frames from entry to apex with brake > threshold
    entry_to_apex = [pt for pt in corner_zone if pt["frame"] <= apex_frame]
    trail_brake_frames = sum(1 for pt in entry_to_apex if pt.get("brake", 0) > 0.05)
    trail_brake_pct = trail_brake_frames / max(len(entry_to_apex), 1)

    # ── Coast frames: near apex, both gas and brake below threshold
    coast_frames_half_window = int(0.5 * hz)
    apex_vicinity = [
        pt for pt in corner_zone
        if abs(pt["frame"] - apex_frame) <= coast_frames_half_window
    ]
    coast_frames = sum(
        1 for pt in apex_vicinity
        if (pt.get("gas_percent", pt.get("gas", 0)) < 0.10 
            and pt.get("brake", 0) < 0.10)
    )

    # ── Peak braking G (longitudinal deceleration)
    peak_brake_g = 0.0
    for pt in approach + entry_to_apex:
        long_g = abs(pt.get("acc_g_z", 0))
        if long_g > peak_brake_g:
            peak_brake_g = long_g

    return {
        "brake_onset_dt": brake_onset_dt,
        "turn_in_dt": turn_in_dt,
        "gas_on_dt": gas_on_dt,
        "trail_brake_pct": trail_brake_pct,
        "coast_frames": coast_frames,
        "peak_brake_g": peak_brake_g,
        "entry_speed": corner.get("entry_speed", 0),
        "apex_speed": corner.get("apex_speed", 0),
        "exit_speed": corner.get("exit_speed", 0),
    }


def analyze_grip_utilization(
    track: List[Dict],
    corner: Dict,
    hz: float,
) -> Optional[Dict]:
    """Analyze grip usage through friction circle metrics.

    For the corner window, compute:
      - peak_total_g: max sqrt(lat_g^2 + long_g^2) — the grip envelope
      - avg_total_g: average combined G through the corner
      - peak_lat_g: peak lateral G (cornering force)
      - peak_long_g: peak longitudinal G (braking/accel)
      - grip_fill_pct: average total_g / peak_total_g — how much of the grip
        envelope is being used on average (100% = always at the limit)
      - combined_braking_pct: % of braking-zone frames where lat_g > 0.3
        (trail braking into corner = using combined grip)
    """
    corner_pts = [
        pt for pt in track
        if corner["start_frame"] <= pt["frame"] <= corner["end_frame"]
    ]
    if len(corner_pts) < 3:
        return None

    total_gs = []
    lat_gs = []
    long_gs = []
    combined_brake_count = 0
    brake_count = 0

    for pt in corner_pts:
        lat_g = abs(pt.get("acc_g_x", 0))
        long_g = abs(pt.get("acc_g_z", 0))
        total_g = math.sqrt(lat_g ** 2 + long_g ** 2)
        total_gs.append(total_g)
        lat_gs.append(lat_g)
        long_gs.append(long_g)

        if pt.get("brake", 0) > 0.05:
            brake_count += 1
            if lat_g > 0.3:
                combined_brake_count += 1

    peak_total_g = max(total_gs) if total_gs else 0
    avg_total_g = sum(total_gs) / len(total_gs) if total_gs else 0
    peak_lat_g = max(lat_gs) if lat_gs else 0
    peak_long_g = max(long_gs) if long_gs else 0

    grip_fill_pct = (avg_total_g / peak_total_g * 100) if peak_total_g > 0.1 else 0
    combined_braking_pct = (combined_brake_count / brake_count * 100) if brake_count > 0 else 0

    return {
        "peak_total_g": peak_total_g,
        "avg_total_g": avg_total_g,
        "peak_lat_g": peak_lat_g,
        "peak_long_g": peak_long_g,
        "grip_fill_pct": grip_fill_pct,
        "combined_braking_pct": combined_braking_pct,
    }


# ── Tyre grip-degradation analysis (stint-level) ──────────────────────────────
#
# For longer races the user can lose grip from any combination of:
#   - tyre core temperature climbing past the optimal window (overheating)
#   - mechanical wear (tyre_wear field, 0.0 fresh -> 1.0 worn)
#   - dirt pickup off-line (tyre_dirty_level)
#   - increasing slip angles / sliding as the carcass softens
#
# The functions below compute compact per-lap summaries of those signals so
# the AI prompt can show a stint-progression table and call out monotonic
# degradation trends (e.g. "lat-G falling lap after lap with similar inputs
# = tyres losing grip; back off slightly for longer races").

def _avg(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _trend_direction(values: List[float], threshold: float) -> str:
    """Classify a per-lap series as RISING / FALLING / FLAT.

    Uses the per-step delta against ``threshold``. A series is only flagged
    monotonic if every step moves in the same direction beyond the threshold;
    otherwise it is FLAT. Needs at least 3 laps to be meaningful.
    """
    if len(values) < 3:
        return "FLAT"
    diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    if all(d > threshold for d in diffs):
        return "RISING"
    if all(d < -threshold for d in diffs):
        return "FALLING"
    # Net trend if endpoints diverge enough even with noise in between.
    span = values[-1] - values[0]
    if span > threshold * (len(values) - 1):
        return "RISING"
    if span < -threshold * (len(values) - 1):
        return "FALLING"
    return "FLAT"


def analyze_lap_tyre_state(lap_track: List[Dict]) -> Optional[Dict]:
    """Per-lap tyre summary for stint-level grip degradation analysis.

    Cornering frames are isolated by lateral G threshold so that the slip,
    lat-G, and slip-angle metrics reflect grip-limited driving rather than
    straight-line cruising. Wear and core-temp metrics use the whole lap
    because they evolve continuously.
    """
    if not lap_track:
        return None

    # ── Cornering subset: |lat_g| > 0.5 (loose threshold; covers any non-trivial corner)
    corner_frames = [pt for pt in lap_track if abs(pt.get("acc_g_x", 0.0)) > 0.5]

    # ── Tyre core temps (per corner)
    temps_fl = [pt.get("tyre_temp_fl", 0.0) for pt in lap_track if pt.get("tyre_temp_fl") is not None]
    temps_fr = [pt.get("tyre_temp_fr", 0.0) for pt in lap_track if pt.get("tyre_temp_fr") is not None]
    temps_rl = [pt.get("tyre_temp_rl", 0.0) for pt in lap_track if pt.get("tyre_temp_rl") is not None]
    temps_rr = [pt.get("tyre_temp_rr", 0.0) for pt in lap_track if pt.get("tyre_temp_rr") is not None]
    avg_core_temp = _avg([_avg(temps_fl), _avg(temps_fr), _avg(temps_rl), _avg(temps_rr)])
    peak_core_temp = max(
        (max(temps_fl) if temps_fl else 0.0),
        (max(temps_fr) if temps_fr else 0.0),
        (max(temps_rl) if temps_rl else 0.0),
        (max(temps_rr) if temps_rr else 0.0),
    )

    # ── Wear delta across the lap (front + rear average)
    wear_keys = ("tyre_wear_fl", "tyre_wear_fr", "tyre_wear_rl", "tyre_wear_rr")
    start_wear = _avg([float(lap_track[0].get(k, 0.0) or 0.0) for k in wear_keys])
    end_wear = _avg([float(lap_track[-1].get(k, 0.0) or 0.0) for k in wear_keys])
    wear_delta = max(0.0, end_wear - start_wear)

    # ── Dirt pickup (end-of-lap snapshot, average across wheels)
    dirty_keys = ("tyre_dirty_fl", "tyre_dirty_fr", "tyre_dirty_rl", "tyre_dirty_rr")
    end_dirty = _avg([float(lap_track[-1].get(k, 0.0) or 0.0) for k in dirty_keys])

    # ── Cornering-only metrics (grip-limited frames)
    if corner_frames:
        peak_lat_g = max(abs(pt.get("acc_g_x", 0.0)) for pt in corner_frames)
        avg_lat_g = _avg([abs(pt.get("acc_g_x", 0.0)) for pt in corner_frames])
        # Slip angle peaks (radians) — use max wheel each frame, then session peak
        slip_angle_keys = ("slip_angle_fl", "slip_angle_fr", "slip_angle_rl", "slip_angle_rr")
        peak_slip_angle = max(
            (max(abs(pt.get(k, 0.0) or 0.0) for k in slip_angle_keys) for pt in corner_frames),
            default=0.0,
        )
    else:
        peak_lat_g = 0.0
        avg_lat_g = 0.0
        peak_slip_angle = 0.0

    return {
        "avg_core_temp_c": round(avg_core_temp, 1),
        "peak_core_temp_c": round(peak_core_temp, 1),
        "wear_delta_pct": round(wear_delta * 100.0, 3),
        "end_wear_pct": round(end_wear * 100.0, 2),
        "end_dirty_pct": round(end_dirty * 100.0, 2),
        "peak_lat_g": round(peak_lat_g, 2),
        "avg_lat_g": round(avg_lat_g, 2),
        "peak_slip_angle_deg": round(math.degrees(peak_slip_angle), 1),
        "corner_frames": len(corner_frames),
    }


def analyze_tyre_grip_degradation(laps: List[Dict]) -> Dict:
    """Compute per-lap tyre states and detect stint-level grip-loss trends.

    Returns a dict with:
      - per_lap: list of (lap_num, state_dict) tuples
      - trends: dict of trend labels for each tracked metric
      - flags: human-readable warnings (e.g. "lat-G falling across stint")
    """
    per_lap: List[tuple] = []
    for lap in laps:
        lap_track = lap.get("track") or []
        state = analyze_lap_tyre_state(lap_track)
        if state is not None:
            per_lap.append((lap["lap_num"], state))

    trends: Dict[str, str] = {}
    flags: List[str] = []
    if len(per_lap) >= 3:
        avg_temps = [s["avg_core_temp_c"] for _, s in per_lap]
        peak_lat_gs = [s["peak_lat_g"] for _, s in per_lap]
        peak_slips = [s["peak_slip_angle_deg"] for _, s in per_lap]
        end_wear = [s["end_wear_pct"] for _, s in per_lap]

        trends["core_temp"] = _trend_direction(avg_temps, threshold=1.5)        # >1.5 °C/lap
        trends["peak_lat_g"] = _trend_direction(peak_lat_gs, threshold=0.03)    # >0.03G/lap
        trends["peak_slip_angle"] = _trend_direction(peak_slips, threshold=0.3)  # >0.3°/lap
        trends["wear"] = _trend_direction(end_wear, threshold=0.2)              # >0.2%/lap

        if trends["peak_lat_g"] == "FALLING":
            flags.append(
                "Peak cornering lat-G is dropping lap-over-lap — driver is getting "
                "less grip out of the same inputs. Tyres are likely past their peak."
            )
        if trends["core_temp"] == "RISING":
            flags.append(
                "Tyre core temperatures are climbing across the stint — overheating "
                "tyres lose grip; consider cooler inputs (smoother throttle/steering)."
            )
        if trends["peak_slip_angle"] == "RISING":
            flags.append(
                "Peak slip angles are growing each lap — the car is sliding more, "
                "another sign of grip falloff."
            )
        if trends["wear"] == "RISING" and end_wear and end_wear[-1] - end_wear[0] > 1.0:
            flags.append(
                f"Mechanical tyre wear is accumulating ({end_wear[0]:.1f}% -> "
                f"{end_wear[-1]:.1f}%); for longer races, manage rears in particular."
            )

    return {
        "per_lap": per_lap,
        "trends": trends,
        "flags": flags,
    }


def analyze_electronics_per_lap(laps: List[Dict]) -> List[Dict]:
    """Per-lap snapshot of electronic aid settings (TC, ABS, engine map, diff).

    Uses the first track-point of each lap as the representative settings
    snapshot and detects whether any key setting was changed by the final
    track-point (mid-lap adjustment). Also extracts limits and modifiable flags.
    """
    result: List[Dict] = []
    for lap in laps:
        track = lap.get("track") or []
        if not track:
            continue
        first = track[0]
        last = track[-1]

        def _val(pt: Dict, key: str):
            v = pt.get(key)
            return int(v) if v is not None else None

        def _changed(key: str) -> bool:
            f = first.get(key)
            ll = last.get(key)
            return f is not None and ll is not None and f != ll

        result.append({
            "lap_num": lap["lap_num"],
            "tc_level": _val(first, "tc_level"),
            "abs_level": _val(first, "abs_level"),
            "engine_map": _val(first, "engine_map_level"),
            "diff_power": _val(first, "diff_power_level"),
            "diff_coast": _val(first, "diff_coast_level"),
            "front_bump_damper": _val(first, "front_bump_damper"),
            "front_rebound_damper": _val(first, "front_rebound_damper"),
            "rear_bump_damper": _val(first, "rear_bump_damper"),
            "rear_rebound_damper": _val(first, "rear_rebound_damper"),
            "perf_mode": _val(first, "electronics_perf_mode"),
            "tc_changed": _changed("tc_level"),
            "abs_changed": _changed("abs_level"),
            "engine_map_changed": _changed("engine_map_level"),
            # Limits (min/max)
            "tc_level_min": _val(first, "tc_level_min"),
            "abs_level_min": _val(first, "abs_level_min"),
            "brake_bias_min": first.get("brake_bias_min"),
            "engine_map_min": _val(first, "engine_map_min"),
            "diff_power_min": _val(first, "diff_power_min"),
            "diff_coast_min": _val(first, "diff_coast_min"),
            "front_bump_damper_min": _val(first, "front_bump_damper_min"),
            "front_rebound_damper_min": _val(first, "front_rebound_damper_min"),
            "rear_bump_damper_min": _val(first, "rear_bump_damper_min"),
            "rear_rebound_damper_min": _val(first, "rear_rebound_damper_min"),
            "perf_mode_min": _val(first, "perf_mode_min"),
            "tc_level_max": _val(first, "tc_level_max"),
            "abs_level_max": _val(first, "abs_level_max"),
            "brake_bias_max": first.get("brake_bias_max"),
            "engine_map_max": _val(first, "engine_map_max"),
            "diff_power_max": _val(first, "diff_power_max"),
            "diff_coast_max": _val(first, "diff_coast_max"),
            "front_bump_damper_max": _val(first, "front_bump_damper_max"),
            "front_rebound_damper_max": _val(first, "front_rebound_damper_max"),
            "rear_bump_damper_max": _val(first, "rear_bump_damper_max"),
            "rear_rebound_damper_max": _val(first, "rear_rebound_damper_max"),
            "perf_mode_max": _val(first, "perf_mode_max"),
            # Modifiable flags
            "tc_level_modifiable": first.get("tc_level_modifiable"),
            "abs_level_modifiable": first.get("abs_level_modifiable"),
            "brake_bias_modifiable": first.get("brake_bias_modifiable"),
            "engine_map_modifiable": first.get("engine_map_modifiable"),
            "diff_power_modifiable": first.get("diff_power_modifiable"),
            "diff_coast_modifiable": first.get("diff_coast_modifiable"),
            "front_bump_damper_modifiable": first.get("front_bump_damper_modifiable"),
            "front_rebound_damper_modifiable": first.get("front_rebound_damper_modifiable"),
            "rear_bump_damper_modifiable": first.get("rear_bump_damper_modifiable"),
            "rear_rebound_damper_modifiable": first.get("rear_rebound_damper_modifiable"),
            "pitlimiter_modifiable": first.get("pitlimiter_modifiable"),
            "perf_mode_modifiable": first.get("perf_mode_modifiable"),
        })
    return result



def analyze_brake_thermals(laps: List[Dict]) -> Dict[str, Any]:
    """Analyze brake temperatures across laps for imbalance and fade.

    Returns a dict with:
        per_lap: list of {lap_num, front_avg, rear_avg, peak_front} where the
            averages are taken over heavy-braking frames (brake > 0.4) and
            ``None`` when no valid temperature samples exist.
        imbalance_note: front/rear thermal-bias warning string or None.
        fade_note: lap-over-lap rising-temperature warning string or None.
    """
    def _avg_temp(points: List[Dict], keys: List[str]) -> Optional[float]:
        values = [
            pt.get(key, 0) for pt in points for key in keys
            if isinstance(pt.get(key, 0), (int, float)) and pt.get(key, 0) > 0
        ]
        return sum(values) / len(values) if values else None

    per_lap: List[Dict[str, Any]] = []
    for lap in laps:
        track_pts = lap.get("track", [])
        braking_pts = [pt for pt in track_pts if (pt.get("brake") or 0) > 0.4]
        front_peaks = [
            pt.get(key, 0) for pt in track_pts for key in ("brake_temp_fl", "brake_temp_fr")
            if isinstance(pt.get(key, 0), (int, float)) and pt.get(key, 0) > 0
        ]
        per_lap.append({
            "lap_num": lap["lap_num"],
            "front_avg": _avg_temp(braking_pts, ["brake_temp_fl", "brake_temp_fr"]),
            "rear_avg": _avg_temp(braking_pts, ["brake_temp_rl", "brake_temp_rr"]),
            "peak_front": max(front_peaks) if front_peaks else None,
        })

    imbalance_note: Optional[str] = None
    front_avgs = [e["front_avg"] for e in per_lap if e["front_avg"] is not None]
    rear_avgs = [e["rear_avg"] for e in per_lap if e["rear_avg"] is not None]
    if front_avgs and rear_avgs:
        front_mean = sum(front_avgs) / len(front_avgs)
        rear_mean = sum(rear_avgs) / len(rear_avgs)
        if rear_mean > 0 and front_mean > 1.5 * rear_mean:
            imbalance_note = (
                f"Front brakes run {front_mean / rear_mean:.1f}x hotter than rears under heavy braking "
                f"({front_mean:.0f}C vs {rear_mean:.0f}C) - brake bias may be too far forward, "
                "or the rears are underworked."
            )
        elif front_mean > 0 and rear_mean > front_mean:
            imbalance_note = (
                f"Rear brakes run hotter than fronts under heavy braking "
                f"({rear_mean:.0f}C vs {front_mean:.0f}C) - rear bias or rear-duct issue."
            )

    fade_note: Optional[str] = None
    peaks = [e["peak_front"] for e in per_lap if e["peak_front"] is not None]
    if len(peaks) >= 3:
        rising = all(later >= earlier for earlier, later in zip(peaks, peaks[1:]))
        total_rise = peaks[-1] - peaks[0]
        if rising and total_rise > 60:
            fade_note = (
                f"Peak front brake temps climbing every lap ({peaks[0]:.0f}C -> {peaks[-1]:.0f}C, "
                f"+{total_rise:.0f}C) - heat is not recovering between braking zones; "
                "consider more brake duct or earlier/shorter braking before fade sets in."
            )

    return {"per_lap": per_lap, "imbalance_note": imbalance_note, "fade_note": fade_note}


def analyze_suspension(laps: List[Dict], ref_corners: List[Dict]) -> Dict[str, Any]:
    """Analyze suspension travel and camber for setup hints.

    Returns dict with keys:
        bottoming_notes: list of strings
        travel_delta_notes: list of strings
        camber_notes: list of strings
    """
    notes: Dict[str, List[str]] = {
        "bottoming_notes": [],
        "travel_delta_notes": [],
        "camber_notes": [],
    }

    # Guard: skip if all suspension values are zero (decoder fallback)
    _any_sus = any(
        pt.get(f"sus_{w}", 0) != 0
        for lap in laps for pt in lap.get("track", []) for w in ("fl", "fr", "rl", "rr")
    )
    if not _any_sus:
        return notes

    # Session max travel per wheel for bottoming detection
    _max_per_wheel = {w: 0.0 for w in ("fl", "fr", "rl", "rr")}
    for lap in laps:
        for pt in lap.get("track", []):
            for w in ("fl", "fr", "rl", "rr"):
                v = pt.get(f"sus_{w}", 0)
                if isinstance(v, (int, float)) and v > _max_per_wheel[w]:
                    _max_per_wheel[w] = v

    # Bottoming: within 2% of max for >2 consecutive frames inside a corner window
    for spec in ref_corners:
        cid = spec["id"]
        name = spec.get("name") or f"Corner {cid}"
        for w in ("fl", "fr", "rl", "rr"):
            for lap in laps:
                corner_pts = [
                    pt for pt in lap.get("track", [])
                    if spec["start"] <= pt.get("lap_progress", -1) < spec["end"]
                ]
                if len(corner_pts) < 3:
                    continue
                streak = 0
                max_streak = 0
                threshold = _max_per_wheel[w] * 0.98
                for pt in corner_pts:
                    v = pt.get(f"sus_{w}", 0)
                    if isinstance(v, (int, float)) and v >= threshold:
                        streak += 1
                        max_streak = max(max_streak, streak)
                    else:
                        streak = 0
                if max_streak > 2:
                    notes["bottoming_notes"].append(
                        f"{w.upper()} bottoming at {name} "
                        f"({max_streak} frames near max travel)"
                    )

    # Apex travel delta best-vs-worst lap per corner (>5mm)
    for spec in ref_corners:
        cid = spec["id"]
        name = spec.get("name") or f"Corner {cid}"
        apex_sus: Dict[int, Dict[str, float]] = {}
        for lap in laps:
            for pt in lap.get("track", []):
                if spec["start"] <= pt.get("lap_progress", -1) < spec["end"]:
                    for w in ("fl", "fr", "rl", "rr"):
                        v = pt.get(f"sus_{w}", 0)
                        if isinstance(v, (int, float)) and v > 0:
                            apex_sus.setdefault(lap["lap_num"], {})[w] = v
                    break
        if len(apex_sus) >= 2:
            for w in ("fl", "fr", "rl", "rr"):
                vals = [
                    apex_sus[ln].get(w, 0)
                    for ln in apex_sus
                    if apex_sus[ln].get(w, 0) > 0
                ]
                if vals and max(vals) - min(vals) > 0.005:
                    notes["travel_delta_notes"].append(
                        f"{name} {w.upper()} apex travel varies "
                        f"{min(vals)*1000:.0f}-{max(vals)*1000:.0f}mm "
                        f"({(max(vals)-min(vals))*1000:.1f}mm spread) - line/curb usage tip"
                    )

    # Camber mismatch at apex: |camber_fl - camber_fr| > 0.5 deg (0.0087 rad)
    for spec in ref_corners:
        cid = spec["id"]
        name = spec.get("name") or f"Corner {cid}"
        for lap in laps:
            for pt in lap.get("track", []):
                if spec["start"] <= pt.get("lap_progress", -1) < spec["end"]:
                    cfl = pt.get("camber_fl", 0)
                    cfr = pt.get("camber_fr", 0)
                    if isinstance(cfl, (int, float)) and isinstance(cfr, (int, float)):
                        if abs(cfl - cfr) > 0.0087:
                            deg = abs(cfl - cfr) * (180 / 3.14159)
                            notes["camber_notes"].append(
                                f"{name}: front camber mismatch {deg:.1f}deg at apex - "
                                "excessive body roll for camber setting"
                            )
                    break

    return notes




def _session_summary_path(output_dir: str) -> str:
    return os.path.join(output_dir, "session_history.jsonl")


def _write_session_summary(
    output_dir: str,
    track: str,
    car: str,
    best_lap_time_s: float,
    top_speed: float,
    lap_count: int,
    avg_fuel_per_lap: Optional[float],
) -> None:
    path = _session_summary_path(output_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "track": track,
        "car": car,
        "best_lap_time_s": best_lap_time_s,
        "best_lap_time_str": f"{int(best_lap_time_s // 60)}:{best_lap_time_s % 60:05.2f}",
        "top_speed": top_speed,
        "laps": lap_count,
        "avg_fuel_per_lap": avg_fuel_per_lap,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _load_previous_summary(output_dir: str, track: str, car: str) -> Optional[Dict[str, Any]]:
    path = _session_summary_path(output_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("track") == track and entry.get("car") == car:
            return entry
    return None


class TelemetryAnalyzer:
    """Analyzes telemetry data and generates reports."""

    def __init__(
        self,
        output_dir: str,
        track_catalog: dict = None,
        session_manager: Optional[SharedSessionManager] = None,
    ):
        self._output_dir = output_dir
        self._track_catalog = track_catalog
        self._session_manager = session_manager or SharedSessionManager()

    async def analyze(
        self,
        frames: List[FrameData],
        hz: float,
        metadata: Optional[CaptureMetadata] = None,
        track_name: Optional[str] = None,
        output_prefix: Optional[str] = None,
        game_lap_boundaries: Optional[List] = None,  # Can be List[int] or List[Tuple[int, Optional[float], Optional[int]]]
    ) -> AnalysisResult:
        """Run full analysis pipeline and generate outputs."""
        log_info(Component.ANALYZER, "Starting analysis", frames=len(frames), hz=hz, track=track_name, prefix=output_prefix)

        if len(frames) < 20:
            log_warning(Component.ANALYZER, "Analysis skipped: insufficient frames", frames=len(frames), prefix=output_prefix)
            return await self._generate_empty_result(output_prefix)

        track_key, track_profile = _select_track_profile_for_analysis(track_name)
        if track_profile:
            log_info(Component.ANALYZER, "Track profile selected", profile=track_profile['display_name'])
        else:
            log_debug(Component.ANALYZER, "Track profile: none - using auto corner detection")

        drive_start = 0
        for i, f in enumerate(frames):
            ph = get_physics(f)
            if ph and ph.get("speed_kmh", 0) > 5:
                if all(
                    get_physics(frames[min(i + j, len(frames) - 1)]).get("speed_kmh", 0) > 2
                    for j in range(5)
                    if get_physics(frames[min(i + j, len(frames) - 1)])
                ):
                    drive_start = max(0, i - 5)
                    break

        track = build_track(frames, hz=hz, start_idx=drive_start)
        if not track:
            log_warning(Component.ANALYZER, "No plausible telemetry frames after quality filtering")
            return await self._generate_empty_result(output_prefix)

        authoritative_progress_ratio = _fraction(track, lambda pt: pt.get("has_authoritative_progress") and pt.get("norm_pos") is not None)
        plausible_frame_ratio = _fraction(
            track,
            lambda pt: (pt.get("frame_quality") or 0.0) >= _PLAUSIBLE_FRAME_THRESHOLD,
        )
        analysis_confidence_score = round(authoritative_progress_ratio * 0.7 + plausible_frame_ratio * 0.3, 3)
        analysis_confidence = _confidence_label(analysis_confidence_score)

        analysis_mode, has_authoritative, has_high_plausible = _decide_analysis_mode(
            authoritative_progress_ratio, plausible_frame_ratio,
        )
        analysis_notes: List[str] = []

        if track_profile and track_profile.get("confidence") == "estimated":
            analysis_notes.append(
                "Track profile corner windows are estimated from public track maps, "
                "not verified telemetry - treat per-corner segment deltas as directional only."
            )

        log_info(Component.ANALYZER, "Data quality assessed",
                progress_ratio=f"{authoritative_progress_ratio:.1%}",
                frame_ratio=f"{plausible_frame_ratio:.1%}",
                confidence=analysis_confidence,
                confidence_score=analysis_confidence_score)
        log_info(Component.ANALYZER, "Analysis mode determined", mode=analysis_mode,
                 auth_ok=has_authoritative, plausible_fallback_ok=has_high_plausible)

        if not has_authoritative and has_high_plausible:
            # Full coaching is unlocked via the plausible-physics fallback;
            # flag this in the notes so the user knows authoritative progress
            # from graphics SHM would further improve analysis quality.
            analysis_notes.append(
                f"Authoritative graphics progress coverage is {authoritative_progress_ratio:.0%}, "
                f"but physics frame plausibility is {plausible_frame_ratio:.0%} — using "
                "dead-reckoning progress for coaching. Lap 1 may be missing if capture "
                "started mid-lap."
            )
        elif not has_authoritative and not has_high_plausible:
            analysis_notes.append(
                f"Authoritative graphics progress coverage too low ({authoritative_progress_ratio:.0%}) "
                f"and plausible physics coverage is only {plausible_frame_ratio:.0%}; detailed coaching disabled."
            )
        if plausible_frame_ratio < 0.75:
            analysis_notes.append(
                f"Physics frame plausibility coverage is only {plausible_frame_ratio:.0%}; derived metrics are degraded."
            )

        # Prioritize definitive lap detection sources over telemetry heuristics.
        # 1st: Game log boundaries (most authoritative)
        # 2nd: Shared memory timing state (last_laptime_ms updates) 
        # 3rd: Telemetry-based detection (position crossing as fallback)
        lap_bounds = None
        lap_times_ms = None
        lap_numbers = None
        prefer_game_lap_times = False

        # 1st priority: Game log boundaries (most definitive)
        if game_lap_boundaries and len(game_lap_boundaries) >= 1:
            # Extract frame indices and lap times from tuples
            if isinstance(game_lap_boundaries[0], (tuple, list)):
                initial_completed_laps = 0
                try:
                    initial_completed_laps = int(track[0].get("completed_laps") or 0)
                except (TypeError, ValueError):
                    initial_completed_laps = 0

                sorted_markers = sorted(
                    (
                        (
                            int(b[0]),
                            b[1] if len(b) > 1 else None,
                            int(b[2]) if len(b) > 2 and b[2] is not None else None,
                        )
                        for b in game_lap_boundaries
                    ),
                    key=lambda item: item[0],
                )
                start_frame = track[0]["frame"] if track else 0
                lap_bounds = [start_frame] + [marker[0] for marker in sorted_markers]
                lap_times_ms = [marker[1] for marker in sorted_markers]
                lap_numbers = [
                    marker[2] if marker[2] is not None else initial_completed_laps + idx + 1
                    for idx, marker in enumerate(sorted_markers)
                ]
                prefer_game_lap_times = True
                if initial_completed_laps > 0 and (not lap_numbers or lap_numbers[0] > 1):
                    analysis_notes.append(
                        f"Capture started after {initial_completed_laps} completed game lap(s); earlier laps are omitted from telemetry."
                    )
            else:
                lap_bounds = game_lap_boundaries
            log_info(Component.ANALYZER, "Lap detection successful", method="authoritative game log boundaries", laps=len(lap_bounds))
        # 2nd priority: Shared memory timing state (last_laptime_ms updates)
        else:
            timing_bounds = _detect_laps_by_timing_state(track, hz=hz)
            if timing_bounds and len(timing_bounds) >= 1:
                start_frame = track[0]["frame"] if track else 0
                lap_bounds = [start_frame] + timing_bounds
                log_info(Component.ANALYZER, "Lap detection successful", method="shared memory timing state", laps=len(lap_bounds))
            # 3rd priority: Telemetry-based detection (normalized position)
            else:
                lap_bounds = detect_laps(track, hz=hz, allow_position_fallback=False)
                if lap_bounds and len(lap_bounds) >= 2:
                    log_info(Component.ANALYZER, "Lap detection successful", method="telemetry-based (normalized position)", laps=len(lap_bounds))
                else:
                    # Try position-based lap detection as final fallback
                    lap_bounds = detect_laps(track, hz=hz, allow_position_fallback=True)
                    if lap_bounds and len(lap_bounds) >= 2:
                        log_info(Component.ANALYZER, "Lap detection successful", method="telemetry-based (position fallback)", laps=len(lap_bounds))

        if not lap_bounds or len(lap_bounds) < 2:
            log_warning(Component.ANALYZER, "Lap detection failed", reason="no valid boundaries")
            analysis_mode = "diagnostic"
            analysis_notes.append("No reliable lap boundaries were found from any detection method.")
            lap_bounds = []

        laps = []
        for i in range(len(lap_bounds) - 1):
            s, e = lap_bounds[i], lap_bounds[i + 1]
            game_lap_num = lap_numbers[i] if lap_numbers and i < len(lap_numbers) else i + 1
            lap_track = [pt for pt in track if s <= pt["frame"] < e]
            if len(lap_track) < 20:
                continue

            lap_progress_ratio = _fraction(
                lap_track,
                lambda pt: pt.get("has_authoritative_progress") and pt.get("norm_pos") is not None,
            )
            lap_plausible_ratio = _fraction(
                lap_track,
                lambda pt: (pt.get("frame_quality") or 0.0) >= _PLAUSIBLE_FRAME_THRESHOLD,
            )
            lap_quality_score = round(lap_progress_ratio * 0.7 + lap_plausible_ratio * 0.3, 3)
            canonical_lap = _build_canonical_lap(lap_track, lap_start_frame=s, hz=hz, bins=200)
            uses_canonical_progress = canonical_lap is not None

            if track_profile and track_profile.get("corners") and uses_canonical_progress:
                corners = _detect_profiled_corners_canonical(
                    canonical_lap["samples"],
                    track_profile,
                    hz,
                    authoritative_progress=lap_progress_ratio >= 0.60,
                )
            elif track_profile and track_profile.get("corners"):
                # Use profile-based corner detection even without canonical progress
                corners = detect_profiled_corners(track, s, e, track_profile, hz=hz)
            else:
                corners = detect_corners(track, s, e, hz=hz)

            # Use game-reported lap times when available.
            if lap_times_ms and i < len(lap_times_ms) and lap_times_ms[i] is not None:
                lap_time = lap_times_ms[i] / 1000.0  # Convert ms to seconds
            elif prefer_game_lap_times:
                # If game times were provided for this analysis, do not silently
                # mix in telemetry-derived durations for missing entries.
                continue
            else:
                lap_time = (e - s) / hz
            
            # Calculate fuel consumption from telemetry (start fuel - end fuel)
            fuel_used = None
            if lap_track:
                # Get fuel level at lap start and end
                start_pt = next((pt for pt in track if pt["frame"] == s), None)
                end_pt = next((pt for pt in track if pt["frame"] == e), None)
                
                if start_pt and end_pt:
                    fuel_start = start_pt.get("fuel")
                    fuel_end = end_pt.get("fuel")
                    
                    if fuel_start is not None and fuel_end is not None and fuel_start > fuel_end:
                        fuel_used = round(fuel_start - fuel_end, 3)
            
            laps.append({
                "lap_num": game_lap_num,
                "capture_lap_index": i + 1,
                "start_frame": s,
                "end_frame": e,
                "lap_time_s": lap_time,
                "lap_time_str": f"{int(lap_time // 60)}:{lap_time % 60:05.2f}",
                "max_speed": max(pt["speed"] for pt in lap_track),
                "avg_speed": sum(pt["speed"] for pt in lap_track) / len(lap_track),
                "fuel_used": fuel_used,
                "track": lap_track,
                "canonical_track": canonical_lap["samples"] if canonical_lap else None,
                "corners": corners,
                "quality_score": lap_quality_score,
                "confidence_label": _confidence_label(lap_quality_score),
                "progress_ratio": lap_progress_ratio,
                "plausible_frame_ratio": lap_plausible_ratio,
                "uses_canonical_progress": uses_canonical_progress,
            })
            fuel_str = f"  fuel {fuel_used:.3f}L" if fuel_used is not None else ""
            log_debug(Component.ANALYZER, "Lap summary", lap_num=game_lap_num, lap_time=f"{lap_time:.0f}s", max_speed=f"{max(pt['speed'] for pt in lap_track):.0f} km/h", corners=len(corners), fuel=fuel_str)

        if not laps:
            log_warning(Component.ANALYZER, "Analysis complete: no valid laps found")
            return await self._generate_empty_result(output_prefix)

        # Prefer authoritative lap data already merged into the shared session
        # state (e.g. log parser + graphics SHM) when available.
        shared_lap_times = self._session_manager.get_all_lap_times()
        shared_lap_validity = self._session_manager.get_all_lap_validity()
        if shared_lap_times and laps:
            try:
                max_shared_lap = max(int(k) for k in shared_lap_times.keys())
                max_analyzed_lap = max(int(lap["lap_num"]) for lap in laps)
                min_analyzed_lap = min(int(lap["lap_num"]) for lap in laps)
                if min_analyzed_lap > 1:
                    analysis_notes.append(
                        f"Telemetry starts at game lap {min_analyzed_lap}; earlier logged laps are not included."
                    )
                if max_shared_lap > max_analyzed_lap:
                    if analysis_mode != "full":
                        analysis_mode = "diagnostic"
                    analysis_notes.append(
                        f"Log/shared session reaches lap {max_shared_lap}, but telemetry only reaches lap {max_analyzed_lap}."
                    )
            except (TypeError, ValueError):
                pass
        for lap in laps:
            shared_time_ms = shared_lap_times.get(lap["lap_num"])
            if isinstance(shared_time_ms, (int, float)) and shared_time_ms > 0:
                shared_lap_time_s = float(shared_time_ms) / 1000.0
                lap["lap_time_s"] = shared_lap_time_s
                lap["lap_time_str"] = f"{int(shared_lap_time_s // 60)}:{shared_lap_time_s % 60:05.2f}"

            shared_validity = shared_lap_validity.get(lap["lap_num"])
            if isinstance(shared_validity, bool):
                lap["is_valid"] = shared_validity

        profile_sanity_notes = _profile_corner_sanity_notes(laps)
        if profile_sanity_notes:
            analysis_mode = "diagnostic"
            analysis_notes.extend(profile_sanity_notes)

        best_lap = min(laps, key=lambda lap: lap["lap_time_s"])
        laps_with_corners = [lap for lap in laps if lap.get("corners")]
        ref_lap = min(laps_with_corners, key=lambda lap: lap["lap_time_s"]) if laps_with_corners else best_lap
        coachable_laps = [lap for lap in laps_with_corners if lap.get("confidence_label") != "low"]
        comparison_pool = coachable_laps or laps_with_corners or [best_lap]
        comparison_pool = sorted(comparison_pool, key=lambda lap: lap["lap_time_s"])
        comparison_lap = comparison_pool[len(comparison_pool) // 2]
        ref_corners = ref_lap.get("corners", [])

        log_info(Component.ANALYZER, "Analysis complete", 
                laps=len(laps), 
                best_lap_time=f"{best_lap['lap_time_s']:.1f}s", 
                coachable_laps=len(coachable_laps))

        if not ref_corners:
            analysis_mode = "diagnostic"
            analysis_notes.append("No trustworthy canonical corners were available for comparison.")

        corner_data = defaultdict(dict)
        corner_speeds = defaultdict(dict)
        for lap in laps:
            if track_profile and track_profile.get("corners"):
                matched = match_profiled_corners(ref_corners, lap["corners"])
            else:
                matched = match_corners(ref_corners, lap["corners"])
            for cid, corner in matched.items():
                if corner and corner.get("confidence_label") != "low":
                    seg_time = corner_segment_time(corner, hz)
                    corner_data[cid][lap["lap_num"]] = {
                        "apex": round(corner["apex_speed"], 1),
                        "entry": round(corner["entry_speed"], 1),
                        "exit": round(corner["exit_speed"], 1),
                        "seg_time": round(seg_time, 3),
                        "confidence": round(float(corner.get("confidence", 0.0)), 3),
                        "confidence_label": corner.get("confidence_label", "low"),
                    }
                    corner_speeds[cid][lap["lap_num"]] = corner["apex_speed"]

        data = {
            "meta": metadata.to_dict() if metadata else {},
            "hz": hz,
            "track_key": track_key,
            "track_name": track_profile["track_name"] if track_profile else track_name,
            "config_key": track_profile["config_key"] if track_profile else None,
            "config_name": track_profile["config_name"] if track_profile else None,
            "track_label": track_profile["display_name"] if track_profile else track_name,
            "car": self._session_manager.get_car(),
            "laps": laps,
            "best_lap_num": best_lap["lap_num"],
            "reference_lap_num": ref_lap["lap_num"],
            "comparison_lap_num": comparison_lap["lap_num"],
            "ref_corners": ref_corners,
            "profile_corners": track_profile.get("corners", []) if track_profile else [],
            "corner_data": corner_data,
            "corner_speeds": corner_speeds,
            "telem": track,
            "drive_start": drive_start,
            "lap_bounds": lap_bounds,
            "analysis_mode": analysis_mode,
            "analysis_confidence": analysis_confidence,
            "analysis_confidence_score": analysis_confidence_score,
            "analysis_notes": analysis_notes,
            "authoritative_progress_ratio": authoritative_progress_ratio,
            "plausible_frame_ratio": plausible_frame_ratio,
        }

        # ── Session-over-session comparison
        _track_label = data.get("track_label") or data.get("track_name") or ""
        _car = data.get("car") or ""
        _laps_with_fuel = [lap for lap in laps if lap.get("fuel_used") is not None]
        _avg_fuel = (
            sum(lap["fuel_used"] for lap in _laps_with_fuel) / len(_laps_with_fuel)
            if _laps_with_fuel else None
        )
        _write_session_summary(
            self._output_dir,
            _track_label,
            _car,
            best_lap["lap_time_s"],
            max((lap.get("max_speed") or 0.0) for lap in laps),
            len(laps),
            _avg_fuel,
        )
        _prev = _load_previous_summary(self._output_dir, _track_label, _car)
        if _prev:
            _delta = best_lap["lap_time_s"] - _prev["best_lap_time_s"]
            _delta_str = f"+{_delta:.2f}s" if _delta > 0 else f"{_delta:.2f}s"
            analysis_notes.append(
                f"Last session best: {_prev['best_lap_time_str']} "
                f"(today {best_lap['lap_time_str']}, {_delta_str})."
            )

        telemetry_summary = {
            "max_speed": max((lap.get("max_speed") or 0.0) for lap in laps),
            "stint_number": 1,
        }
        self._session_manager.update_from_telemetry(telemetry_summary)

        log_info(Component.ANALYZER, "Generating outputs", prefix=output_prefix)
        html_path = await self._generate_html(data, output_prefix)
        ai_prompt_path = await self._generate_ai_prompt(data, output_prefix)
        log_info(Component.ANALYZER, "Outputs generated", html=html_path, ai_prompt=ai_prompt_path)

        return AnalysisResult(
            html_path=html_path,
            ai_prompt_path=ai_prompt_path,
            laps_detected=len(laps),
            best_lap_time=best_lap["lap_time_s"],
            track_name=data.get("track_label") or data.get("track_name"),
        )

    async def _generate_empty_result(self, output_prefix: Optional[str] = None) -> AnalysisResult:
        """Generate result for empty/invalid data without creating files."""
        log_info(Component.ANALYZER, "Skipping output: insufficient or invalid telemetry data", prefix=output_prefix)
        return AnalysisResult(
            html_path=None,
            ai_prompt_path=None,
            laps_detected=0,
            best_lap_time=0.0,
            track_name=None,
        )

    async def _generate_html(self, data: Dict, output_prefix: Optional[str] = None) -> str:
        """Generate HTML report with full telemetry visualization."""
        return await render_html(data, self._output_dir, output_prefix)


    async def _generate_ai_prompt(self, data: Dict, output_prefix: Optional[str] = None) -> str:
        """Generate detailed AI coaching prompt with per-corner analysis and setup recommendations."""
        return await generate_ai_prompt(data, self._output_dir, output_prefix)
