"""Lap detection functions — extracted from telemetry_analyzer.py."""
import math
from typing import Dict, List, Optional

from src.utils.structured_logger import log_debug, Component


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
