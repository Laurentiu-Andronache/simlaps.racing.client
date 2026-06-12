"""Canonical lap resampling — extracted from telemetry_analyzer.py."""
from typing import Any, Dict, List, Optional

from src.core.analyzer._util import _interpolate_value, _optional_float


def _build_canonical_lap(
    lap_track: List[Dict],
    lap_start_frame: int,
    hz: float,
    bins: int = 200,
) -> Optional[Dict[str, Any]]:
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
        "frame", "time_s", "x", "z", "speed", "heading", "steer",
        "brake", "gas", "yaw_rate", "acc_g_x", "acc_g_y", "acc_g_z",
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
