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
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.core.telemetry_capture import CaptureMetadata, FrameData
from src.core.track_catalog import select_track_profile


@dataclass
class AnalysisResult:
    """Result of telemetry analysis."""
    html_path: str
    ai_prompt_path: str
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


def build_track(frames: List[FrameData], hz: float = 1.0, start_idx: int = 0) -> List[Dict]:
    """Build track map from frames."""
    track = []
    x = z = 0.0
    dt = 1.0 / hz

    for i in range(start_idx, len(frames)):
        f = frames[i]
        ph = get_physics(f)
        if not ph:
            continue

        wp = ph.get("world_position") or ph.get("worldPosition")
        if wp and isinstance(wp, dict):
            x = float(wp.get("x", x))
        else:
            velocity = ph.get("velocity", {})
            vx = velocity.get("x", 0) if isinstance(velocity, dict) else 0
            vz = velocity.get("z", 0) if isinstance(velocity, dict) else 0
            x += vx * dt
            z += vz * dt

        norm_pos = (
            ph.get("normalized_spline_position")
            or ph.get("spNormalizedCarPosition")
            or ph.get("normalizedCarPosition")
        )

        tyre_core_temp = _safe_4(ph.get(" tyre_core_temp", []), default=0.0)
        wheels_pressure = _safe_4(ph.get("wheels_pressure", []), default=0.0)
        wheel_slip_raw = _safe_4(ph.get("wheel_slip", []), default=0.0)
        wheel_slip = [_sanitize_slip(v) for v in wheel_slip_raw]
        wheel_load = _safe_4(ph.get("wheel_load", []), default=0.0)
        suspension_travel = _safe_4(ph.get("suspension_travel", []), default=0.0)
        camber_rad = _safe_4(ph.get("camber_rad", []), default=0.0)
        brake_temp = _safe_4(ph.get("brake_temp", []), default=0.0)

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
            "speed": ph.get("speed_kmh", 0) or 0,
            "heading": ph.get("heading", 0) or 0,
            "steer": ph.get("steer_angle", 0) or 0,
            "brake": ph.get("brake", 0) or 0,
            "gas": ph.get("gas", 0) or 0,
            "gear": ph.get("gear", 0) or 0,
            "rpms": ph.get("rpms", 0) or 0,
            "norm_pos": float(norm_pos) if norm_pos is not None else None,
            "abs": ph.get("abs", 0) or 0,
            "tc": ph.get("tc", 0) or 0,
            "acc_g_x": acc_g_x,
            "acc_g_y": acc_g_y,
            "acc_g_z": acc_g_z,
            "yaw_rate": yaw_rate,
            "air_temp": ph.get("air_temp", 0) or 0,
            "road_temp": ph.get("road_temp", 0) or 0,
            "tyre_temp_fl": tyre_core_temp[0] if len(tyre_core_temp) > 0 else 0,
            " tyre_temp_fr": tyre_core_temp[1] if len( tyre_core_temp) > 1 else 0,
            " tyre_temp_rl": tyre_core_temp[2] if len( tyre_core_temp) > 2 else 0,
            " tyre_temp_rr": tyre_core_temp[3] if len( tyre_core_temp) > 3 else 0,
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
        })
    return track


def _detect_laps_by_norm_pos(track: List[Dict], hz: float = 1.0, min_lap_time_s: float = 60.0) -> Optional[List[int]]:
    """Detect laps using normalized spline position."""
    min_lap_frames = max(1, int(round(min_lap_time_s * hz)))
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


def _detect_laps_by_position(track: List[Dict], hz: float = 1.0, min_lap_time_s: float = 60.0, warmup_time_s: float = 40.0) -> List[int]:
    """Fallback lap detection using position."""
    min_lap_frames = max(1, int(round(min_lap_time_s * hz)))
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


def detect_laps(track: List[Dict], hz: float = 1.0) -> List[int]:
    """Detect lap boundaries."""
    norm_result = _detect_laps_by_norm_pos(track, hz=hz)
    if norm_result:
        print("[ANALYZER] Lap detection: using normalized car position")
        return norm_result

    print("[ANALYZER] Lap detection: using dead-reckoning position")
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
        " tyre_temp_fl": pt.get(" tyre_temp_fl", 0),
        " tyre_temp_fr": pt.get(" tyre_temp_fr", 0),
        " tyre_temp_rl": pt.get(" tyre_temp_rl", 0),
        " tyre_temp_rr": pt.get(" tyre_temp_rr", 0),
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

        result.append({
            "id": cid,
            "start_frame": seg[ci_start]["frame"],
            "end_frame": seg[ci_end]["frame"],
            "apex_frame": apex["frame"],
            "apex_speed": apex["speed"],
            "min_speed": min(pt["speed"] for pt in window),
            "entry_speed": entry["speed"],
            "exit_speed": exit_pt["speed"],
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


class TelemetryAnalyzer:
    """Analyzes telemetry data and generates reports."""

    def __init__(self, output_dir: str, track_catalog: dict = None):
        self._output_dir = output_dir
        self._track_catalog = track_catalog

    async def analyze(
        self,
        frames: List[FrameData],
        hz: float,
        metadata: Optional[CaptureMetadata] = None,
        track_name: Optional[str] = None,
    ) -> AnalysisResult:
        """Run full analysis pipeline and generate outputs."""
        print(f"[ANALYZER] Analyzing {len(frames)} frames at {hz} Hz, track: {track_name}")

        if len(frames) < 20:
            print("[ANALYZER] Not enough frames for analysis")
            return await self._generate_empty_result()

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
        lap_bounds = detect_laps(track, hz=hz)

        laps = []
        for i in range(len(lap_bounds) - 1):
            s, e = lap_bounds[i], lap_bounds[i + 1]
            lap_track = [pt for pt in track if s <= pt["frame"] < e]
            if len(lap_track) < 20:
                continue
            corners = detect_corners(track, s, e, hz=hz)
            lap_time = (e - s) / hz
            laps.append({
                "lap_num": i + 1,
                "start_frame": s,
                "end_frame": e,
                "lap_time_s": lap_time,
                "lap_time_str": f"{int(lap_time // 60)}:{lap_time % 60:05.2f}",
                "max_speed": max(pt["speed"] for pt in lap_track),
                "avg_speed": sum(pt["speed"] for pt in lap_track) / len(lap_track),
                "track": lap_track,
                "corners": corners,
            })
            print(f"[ANALYZER] Lap {i + 1}: {lap_time:.0f}s  max {max(pt['speed'] for pt in lap_track):.0f} km/h  {len(corners)} corners")

        if not laps:
            print("[ANALYZER] No complete laps detected")
            return await self._generate_empty_result()

        best_lap = min(laps, key=lambda lap: lap["lap_time_s"])
        ref_corners = best_lap["corners"]

        corner_data = defaultdict(dict)
        corner_speeds = defaultdict(dict)
        for lap in laps:
            matched = match_corners(ref_corners, lap["corners"])
            for cid, corner in matched.items():
                if corner:
                    seg_time = (corner["end_frame"] - corner["start_frame"]) / hz
                    corner_data[cid][lap["lap_num"]] = {
                        "apex": round(corner["apex_speed"], 1),
                        "entry": round(corner["entry_speed"], 1),
                        "exit": round(corner["exit_speed"], 1),
                        "seg_time": round(seg_time, 3),
                    }
                    corner_speeds[cid][lap["lap_num"]] = corner["apex_speed"]

        data = {
            "meta": metadata.to_dict() if metadata else {},
            "hz": hz,
            "track_key": None,
            "track_name": track_name,
            "config_key": None,
            "config_name": None,
            "track_label": track_name,
            "laps": laps,
            "best_lap_num": best_lap["lap_num"],
            "ref_corners": ref_corners,
            "corner_data": corner_data,
            "corner_speeds": corner_speeds,
            "telem": track,
            "drive_start": drive_start,
            "lap_bounds": lap_bounds,
        }

        html_path = await self._generate_html(data)
        ai_prompt_path = await self._generate_ai_prompt(data)

        return AnalysisResult(
            html_path=html_path,
            ai_prompt_path=ai_prompt_path,
            laps_detected=len(laps),
            best_lap_time=best_lap["lap_time_s"],
            track_name=data.get("track_label") or data.get("track_name"),
        )

    async def _generate_empty_result(self) -> AnalysisResult:
        """Generate result for empty/invalid data."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        html_path = os.path.join(self._output_dir, f"telemetry_{timestamp}.html")
        ai_prompt_path = os.path.join(self._output_dir, f"telemetry_{timestamp}_ai_prompt.txt")

        os.makedirs(self._output_dir, exist_ok=True)

        with open(html_path, "w", encoding="utf-8") as f:
            f.write("<html><body><h1>No telemetry data</h1><p>Not enough data to analyze.</p></body></html>")

        with open(ai_prompt_path, "w", encoding="utf-8") as f:
            f.write("No telemetry data available for analysis.\n")

        return AnalysisResult(
            html_path=html_path,
            ai_prompt_path=ai_prompt_path,
            laps_detected=0,
            best_lap_time=0.0,
            track_name=None,
        )

    async def _generate_html(self, data: Dict) -> str:
        """Generate HTML report."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        html_path = os.path.join(self._output_dir, f"telemetry_{timestamp}.html")

        os.makedirs(self._output_dir, exist_ok=True)

        laps_json = []
        for lap in data["laps"]:
            track_slim = [
                {
                    "frame": pt["frame"],
                    "x": round(pt["x"], 2),
                    "z": round(pt["z"], 2),
                    "speed": round(pt["speed"], 1),
                    "brake": round(pt["brake"], 3),
                    "gas": round(pt["gas"], 3),
                    "gear": pt["gear"],
                    "steer": round(pt.get("steer", 0), 6),
                    "yaw_rate": round(pt.get("yaw_rate", 0), 6),
                    "acc_g_x": round(pt.get("acc_g_x", 0), 6),
                    "acc_g_z": round(pt.get("acc_g_z", 0), 6),
                    "brake_temp_fl": round(pt.get("brake_temp_fl", 0), 2),
                    "brake_temp_fr": round(pt.get("brake_temp_fr", 0), 2),
                    "brake_temp_rl": round(pt.get("brake_temp_rl", 0), 2),
                    "brake_temp_rr": round(pt.get("brake_temp_rr", 0), 2),
                }
                for pt in lap["track"]
            ]
            corners_json = [
                {
                    "id": c["id"],
                    "name": c.get("name"),
                    "start_frame": c["start_frame"],
                    "end_frame": c["end_frame"],
                    "apex_frame": c["apex_frame"],
                    "apex_speed": round(c["apex_speed"], 1),
                    "entry_speed": round(c["entry_speed"], 1),
                    "exit_speed": round(c["exit_speed"], 1),
                    "apex_x": round(c["apex_x"], 1),
                    "apex_z": round(c["apex_z"], 1),
                    "lap_pos": round(c["lap_pos"], 4),
                }
                for c in lap["corners"]
            ]
            laps_json.append({
                "lap_num": lap["lap_num"],
                "lap_time_s": round(lap["lap_time_s"], 3),
                "lap_time_str": lap["lap_time_str"],
                "max_speed": round(lap["max_speed"], 1),
                "avg_speed": round(lap["avg_speed"], 1),
                "track": track_slim,
                "corners": corners_json,
            })

        ref_corners_json = [
            {
                "id": c["id"],
                "name": c.get("name"),
                "lap_pos": round(c["lap_pos"], 4),
            }
            for c in data["ref_corners"]
        ]

        corner_data_json = {}
        for cid, speeds in data["corner_data"].items():
            corner_data_json[str(cid)] = {str(k): v for k, v in speeds.items()}

        corner_speeds_json = {}
        for cid, speeds in data["corner_speeds"].items():
            corner_speeds_json[str(cid)] = {str(k): v for k, v in speeds.items()}

        data_json = json.dumps({
            "meta": data["meta"],
            "hz": data["hz"],
            "track_key": data["track_key"],
            "track_name": data["track_name"],
            "config_key": data["config_key"],
            "config_name": data["config_name"],
            "track_label": data["track_label"],
            "laps": laps_json,
            "best_lap_num": data["best_lap_num"],
            "ref_corners": ref_corners_json,
            "corner_data": corner_data_json,
            "corner_speeds": corner_speeds_json,
        })

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AC Evo Lap Analysis</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js"></script>
<style>
  :root {{ --bg: #0d0d0f; --panel: #16181d; --border: #2a2d36; --text: #e0e2ea; --muted: #6b7280; --accent: #3b82f6; --green: #22c55e; --red: #ef4444; --orange: #f97316; --yellow: #eab308; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; font-size: 14px; }}
  header {{ background: var(--panel); border-bottom: 1px solid var(--border); padding: 14px 24px; display: flex; align-items: center; gap: 16px; }}
  header h1 {{ font-size: 18px; font-weight: 600; letter-spacing: 0.02em; }}
  header .sub {{ color: var(--muted); font-size: 12px; }}
  .container {{ max-width: 1400px; margin: 0 auto; padding: 20px 24px; }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }}
  .grid-3 {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-bottom: 16px; }}
  @media (max-width: 900px) {{ .grid-2, .grid-3 {{ grid-template-columns: 1fr; }} }}
  .card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }}
  .card h2 {{ font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 12px; }}
  .stat-row {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }}
  .stat {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 10px 16px; min-width: 130px; }}
  .stat .label {{ font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }}
  .stat .value {{ font-size: 22px; font-weight: 700; margin-top: 2px; }}
  .lap-filters {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; align-items: center; }}
  .lap-btn {{ background: var(--border); border: 1px solid transparent; border-radius: 6px; padding: 5px 13px; cursor: pointer; font-size: 12px; font-weight: 600; color: var(--muted); transition: all 0.15s; }}
  .lap-btn.active {{ border-color: currentColor; color: var(--text); }}
  .lap-btn:hover {{ background: #2a2d3a; }}
  canvas {{ max-width: 100%; }}
  .track-wrap {{ position: relative; }}
  #track-canvas {{ border-radius: 6px; }}
  .corner-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  .corner-table th {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }}
  .corner-table td {{ padding: 6px 10px; border-bottom: 1px solid #1e2028; }}
  .corner-table tr:hover td {{ background: #1e2028; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; }}
  .pill {{ display: inline-block; padding: 2px 7px; border-radius: 99px; font-size: 11px; background: var(--border); }}
  .legend {{ display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 10px; }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; font-size: 12px; }}
  .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}
  .section-title {{ font-size: 15px; font-weight: 700; margin: 20px 0 10px; padding-left: 2px; }}
  select {{ background: var(--border); border: 1px solid #3a3d4a; border-radius: 6px; padding: 5px 10px; color: var(--text); font-size: 12px; cursor: pointer; }}
</style>
</head>
<body>
<header>
  <div>
    <h1>🏎 AC Evo — Lap Telemetry</h1>
    <div class="sub" id="session-info">Loading…</div>
  </div>
</header>
<div class="container">
  <div class="stat-row" id="stats-row"></div>
  <div class="grid-2">
    <div class="card">
      <h2>Track Map</h2>
      <div style="margin-bottom:10px; display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
        <span style="font-size:12px; color:var(--muted)">Color by:</span>
        <select id="map-color-mode" onchange="drawTrackMap()">
          <option value="speed">Speed</option>
          <option value="brake">Brake</option>
          <option value="gas">Throttle</option>
        </select>
        <select id="map-lap-select" onchange="drawTrackMap()"></select>
      </div>
      <div class="track-wrap">
        <canvas id="track-canvas"></canvas>
      </div>
    </div>
    <div class="card">
      <h2>Speed Trace</h2>
      <div class="lap-filters" id="speed-lap-filters"></div>
      <canvas id="speed-chart" height="260"></canvas>
    </div>
  </div>
  <div class="section-title">Corner Speed Comparison</div>
  <div class="grid-2">
    <div class="card">
      <h2>Apex Speed by Corner</h2>
      <canvas id="corner-chart" height="280"></canvas>
    </div>
    <div class="card" style="overflow-x:auto">
      <h2>Corner Speed Table (km/h)</h2>
      <table class="corner-table" id="corner-table"></table>
    </div>
  </div>
</div>
<script>
const DATA = {data_json};
const LAP_COLORS = ['#3b82f6','#22c55e','#f97316','#a855f7','#eab308','#ec4899','#06b6d4'];
function lapColor(n) {{ return LAP_COLORS[(n - 1) % LAP_COLORS.length]; }}
function speedColor(frac) {{
  const stops = [[0,[30,120,255]],[0.25,[0,210,220]],[0.5,[0,200,80]],[0.75,[240,200,0]],[1,[255,40,40]]];
  frac = Math.max(0, Math.min(1, frac));
  for (let i = 1; i < stops.length; i++) {{
    if (frac <= stops[i][0]) {{
      const t = (frac - stops[i-1][0]) / (stops[i][0] - stops[i-1][0]);
      const c0 = stops[i-1][1], c1 = stops[i][1];
      return `rgb(${{Math.round(c0[0]+t*(c1[0]-c0[0]))}},${{Math.round(c0[1]+t*(c1[1]-c0[1]))}},${{Math.round(c0[2]+t*(c1[2]-c0[2]))}})`;
    }}
  }}
  return 'rgb(255,40,40)';
}}
const activeLaps = new Set(DATA.laps.map(l => l.lap_num));
function makeLapFilters(containerId, onChange) {{
  const el = document.getElementById(containerId);
  el.innerHTML = '<span style="font-size:12px;color:var(--muted);margin-right:4px">Laps:</span>';
  DATA.laps.forEach(lap => {{
    const btn = document.createElement('button');
    btn.className = 'lap-btn active';
    btn.style.color = lapColor(lap.lap_num);
    btn.textContent = `L${{lap.lap_num}}${{lap.lap_num===DATA.best_lap_num?'*':''}} - ${{lap.lap_time_str}}`;
    btn.dataset.lap = lap.lap_num;
    btn.addEventListener('click', () => {{
      if (activeLaps.has(lap.lap_num)) activeLaps.delete(lap.lap_num);
      else activeLaps.add(lap.lap_num);
      btn.classList.toggle('active', activeLaps.has(lap.lap_num));
      onChange();
    }});
    el.appendChild(btn);
  }});
}}
function renderStats() {{
  const row = document.getElementById('stats-row');
  const bestLap = DATA.laps.find(l => l.lap_num === DATA.best_lap_num) || DATA.laps.reduce((best, lap) => lap.lap_time_s < best.lap_time_s ? lap : best, DATA.laps[0]);
  const maxSpd = Math.max(...DATA.laps.map(l => l.max_speed));
  const stats = [
    {{ label: 'Laps', value: DATA.laps.length }},
    {{ label: 'Best Lap', value: bestLap.lap_time_str }},
    {{ label: 'Top Speed', value: maxSpd.toFixed(0) + ' km/h' }},
    {{ label: 'Corners / Lap', value: DATA.ref_corners.length }},
  ];
  row.innerHTML = stats.map(s => `<div class="stat"><div class="label">${{s.label}}</div><div class="value">${{s.value}}</div></div>`).join('');
  const prefix = DATA.track_label || DATA.track_name || '';
  document.getElementById('session-info').textContent = `${{prefix ? prefix + '  |  ' : ''}}${{DATA.laps.length}} laps detected  -  best ${{bestLap.lap_time_str}}`;
}}
function drawTrackMap() {{
  const canvas = document.getElementById('track-canvas');
  const mode = document.getElementById('map-color-mode').value;
  const sel = document.getElementById('map-lap-select');
  const lapNum = parseInt(sel.value);
  const lap = DATA.laps.find(l => l.lap_num === lapNum);
  if (!lap) return;
  const pts = lap.track;
  const xs = pts.map(p => p.x), zs = pts.map(p => p.z);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minZ = Math.min(...zs), maxZ = Math.max(...zs);
  const wrap = canvas.parentElement;
  const size = Math.min(wrap.clientWidth, 420);
  canvas.width = size; canvas.height = size;
  const pad = 24, sc = Math.min((size - 2*pad) / (maxX - minX || 1), (size - 2*pad) / (maxZ - minZ || 1));
  const offX = pad + ((size - 2*pad) - (maxX-minX)*sc) / 2, offZ = pad + ((size - 2*pad) - (maxZ-minZ)*sc) / 2;
  const cx = x => offX + (x - minX) * sc, cz = z => offZ + (z - minZ) * sc;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, size, size);
  ctx.fillStyle = '#0d0f14';
  ctx.fillRect(0, 0, size, size);
  let vals = mode === 'speed' ? pts.map(p => p.speed) : mode === 'brake' ? pts.map(p => p.brake) : pts.map(p => p.gas);
  const minV = Math.min(...vals), maxV = Math.max(...vals);
  ctx.beginPath(); ctx.strokeStyle = '#1e2028'; ctx.lineWidth = 12; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  pts.forEach((p, i) => {{ i === 0 ? ctx.moveTo(cx(p.x), cz(p.z)) : ctx.lineTo(cx(p.x), cz(p.z)); }});
  ctx.stroke();
  for (let i = 1; i < pts.length; i++) {{
    const p0 = pts[i - 1], p1 = pts[i];
    const frac = (vals[i] - minV) / (maxV - minV || 1);
    ctx.beginPath(); ctx.lineWidth = 6;
    ctx.strokeStyle = mode === 'speed' ? speedColor(frac) : mode === 'brake' ? `rgb(${{60 + vals[i] * 195}},${{30*(1-vals[i])}},${{30*(1-vals[i])}})` : `rgb(${{30*(1-vals[i])}},${{60 + vals[i] * 175}},${{30*(1-vals[i])}})`;
    ctx.moveTo(cx(p0.x), cz(p0.z)); ctx.lineTo(cx(p1.x), cz(p1.z)); ctx.stroke();
  }}
  const corners = lap.corners;
  corners.forEach(c => {{
    const p = pts.find(pt => pt.frame === c.apex_frame) || pts[0];
    ctx.beginPath(); ctx.arc(cx(p.x), cz(p.z), 6, 0, Math.PI * 2); ctx.fillStyle = '#fff'; ctx.fill(); ctx.fillStyle = '#000'; ctx.font = 'bold 9px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(c.id, cx(p.x), cz(p.z));
  }});
  const start = pts[0];
  ctx.beginPath(); ctx.arc(cx(start.x), cz(start.z), 8, 0, Math.PI * 2); ctx.fillStyle = '#fff'; ctx.fill(); ctx.fillStyle = '#111'; ctx.font = 'bold 9px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText('S', cx(start.x), cz(start.z));
}}
let speedChart = null;
function buildSpeedChart() {{
  const ctx = document.getElementById('speed-chart').getContext('2d');
  if (speedChart) speedChart.destroy();
  const datasets = DATA.laps.filter(l => activeLaps.has(l.lap_num)).map(lap => ({{
    label: `Lap ${{lap.lap_num}}${{lap.lap_num===DATA.best_lap_num?'*':''}} (${{lap.lap_time_str}})`,
    data: lap.track.map((pt, i) => ({{ x: i / Math.max(lap.track.length - 1, 1) * 100, y: pt.speed }})),
    borderColor: lapColor(lap.lap_num), backgroundColor: 'transparent', borderWidth: 1.8, pointRadius: 0, tension: 0.3,
  }}));
  speedChart = new Chart(ctx, {{ type: 'line', data: {{ datasets }}, options: {{ responsive: true, animation: false, interaction: {{ mode: 'index', intersect: false }}, scales: {{ x: {{ type: 'linear', min: 0, max: 100, title: {{ display: true, text: 'Lap progress (%)', color: '#6b7280' }}, grid: {{ color: '#1e2028' }}, ticks: {{ color: '#6b7280', callback: v => v + '%' }} }}, y: {{ title: {{ display: true, text: 'Speed (km/h)', color: '#6b7280' }}, grid: {{ color: '#1e2028' }}, ticks: {{ color: '#6b7280' }} }} }}, plugins: {{ legend: {{ labels: {{ color: '#e0e2ea', boxWidth: 12 }} }} }} }});
}}
let cornerChart = null;
function buildCornerChart() {{
  const ctx = document.getElementById('corner-chart').getContext('2d');
  if (cornerChart) cornerChart.destroy();
  const labels = DATA.ref_corners.map(c => c.name || `C${{c.id}}`);
  const datasets = DATA.laps.map(lap => ({{
    label: `Lap ${{lap.lap_num}}`,
    data: DATA.ref_corners.map(c => {{ const s = DATA.corner_speeds[c.id]; return s ? (s[lap.lap_num] || null) : null; }}),
    backgroundColor: lapColor(lap.lap_num) + 'cc', borderColor: lapColor(lap.lap_num), borderWidth: 1, borderRadius: 4,
  }}));
  cornerChart = new Chart(ctx, {{ type: 'bar', data: {{ labels, datasets }}, options: {{ responsive: true, animation: false, interaction: {{ mode: 'index' }}, scales: {{ x: {{ grid: {{ color: '#1e2028' }}, ticks: {{ color: '#6b7280' }} }}, y: {{ title: {{ display: true, text: 'Apex Speed (km/h)', color: '#6b7280' }}, grid: {{ color: '#1e2028' }}, ticks: {{ color: '#6b7280' }} }} }}, plugins: {{ legend: {{ labels: {{ color: '#e0e2ea', boxWidth: 12 }} }} }} }});
}}
function buildCornerTable() {{
  const table = document.getElementById('corner-table');
  const lapNums = DATA.laps.map(l => l.lap_num);
  let html = `<thead><tr><th>Corner</th>` + lapNums.map(n => `<th>Lap ${n}</th>`).join('') + `<th>Δ Best–Worst</th></tr></thead><tbody>`;
  DATA.ref_corners.forEach(c => {{
    const speeds = DATA.corner_data?.[c.id] || {{}};
    const vals = lapNums.map(n => speeds[n]?.apex).filter(v => v !== undefined);
    const best = vals.length ? Math.max(...vals) : null;
    const worst = vals.length ? Math.min(...vals) : null;
    const delta = best !== null ? (best - worst).toFixed(1) : '—';
    html += `<tr><td><span class="badge" style="background:var(--border)">${{c.name || `C${{c.id}}`}}</span></td>`;
    lapNums.forEach(n => {{
      const v = speeds[n]?.apex;
      if (v === undefined) {{ html += `<td style="color:var(--muted)">-</td>`; return; }}
      const isB = v === best, isW = v === worst;
      const color = isB ? 'var(--green)' : isW ? 'var(--red)' : 'var(--text)';
      html += `<td style="color:${{color}};font-weight:${{isB||isW?700:400}}">${{v.toFixed(1)}}</td>`;
    }});
    html += `<td style="color:var(--orange)">${{delta}}</td></tr>`;
  }});
  html += '</tbody>'; table.innerHTML = html;
}}
window.addEventListener('DOMContentLoaded', () => {{
  renderStats();
  const sel = document.getElementById('map-lap-select');
  DATA.laps.forEach(l => {{ const opt = document.createElement('option'); opt.value = l.lap_num; opt.textContent = `Lap ${{l.lap_num}}${{l.lap_num===DATA.best_lap_num?'*':''}} (${{l.lap_time_str}})`; sel.appendChild(opt); }});
  makeLapFilters('speed-lap-filters', () => {{ buildSpeedChart(); }});
  drawTrackMap(); buildSpeedChart(); buildCornerChart(); buildCornerTable();
}});
</script>
</body>
</html>"""

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        print(f"[ANALYZER] Generated HTML report: {html_path}")
        return html_path

    async def _generate_ai_prompt(self, data: Dict) -> str:
        """Generate AI coaching prompt."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ai_prompt_path = os.path.join(self._output_dir, f"telemetry_{timestamp}_ai_prompt.txt")

        os.makedirs(self._output_dir, exist_ok=True)

        laps = data.get("laps", [])
        if not laps:
            with open(ai_prompt_path, "w", encoding="utf-8") as f:
                f.write("No telemetry data available for coaching.\n")
            return ai_prompt_path

        best_lap = min(laps, key=lambda l: l["lap_time_s"])
        track_name = data.get("track_label") or data.get("track_name") or "Unknown Track"

        prompt = f"""# Racing Coaching Request

## Session Overview
- **Track**: {track_name}
- **Total Laps**: {len(laps)}
- **Best Lap**: {best_lap['lap_time_str']}
- **Top Speed**: {max(l['max_speed'] for l in laps):.0f} km/h

## Lap Times
"""
        for lap in laps:
            marker = " <-- BEST" if lap["lap_num"] == best_lap["lap_num"] else ""
            prompt += f"- Lap {lap['lap_num']}: {lap['lap_time_str']}{marker}\n"

        if data.get("ref_corners"):
            prompt += f"\n## Corner Analysis ({len(data['ref_corners'])} corners)\n\n"
            for corner in data["ref_corners"]:
                corner_id = corner["id"]
                name = corner.get("name", f"Corner {corner_id}")
                speeds = data.get("corner_speeds", {}).get(corner_id, {})
                if speeds:
                    best_speed = max(speeds.values()) if speeds else 0
                    worst_speed = min(speeds.values()) if speeds else 0
                    prompt += f"- **{name}**: Best {best_speed:.0f} km/h, Worst {worst_speed:.0f} km/h\n"

        prompt += """
## Coaching Request
Please analyze this telemetry data and provide:
1. Key areas where time can be gained
2. Corner entry/exit technique observations
3. Brake and throttle application patterns
4. Suggestions for improving consistency across laps
"""

        with open(ai_prompt_path, "w", encoding="utf-8") as f:
            f.write(prompt)

        print(f"[ANALYZER] Generated AI prompt: {ai_prompt_path}")
        return ai_prompt_path
