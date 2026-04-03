#!/usr/bin/env python3
"""
AC Evo Lap Analyzer
Parses raw Assetto Corsa Evo JSONL telemetry and generates an interactive
HTML report showing track sections, corner speeds, and lap comparisons.

Usage:
    python3 ac_lap_analyzer.py <input.jsonl> [output.html]
    python3 ac_lap_analyzer.py ac_evo_raw_20260330_223000.jsonl
"""

import argparse
import json
import math
import sys
import os
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from track_catalog import select_track_profile

# ─── Parse JSONL ─────────────────────────────────────────────────────────────

def load_frames(path):
    frames = []
    meta = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("_record_type") == "meta":
                meta = d
            elif d.get("_record_type") == "frame":
                frames.append(d)
    return meta, frames

def get_physics(frame):
    """Get physics data from frame, returning None if not present."""
    return frame.get("regions", {}).get("physics")

# ─── Build track map (integrate world velocity) ───────────────────────────────

def build_track(frames, hz=1.0, start_idx=0):
    """Build track points from world position when available, else velocity."""
    x, z = 0.0, 0.0
    dt = 1.0 / max(hz, 1e-9)
    track = []
    for i, frame in enumerate(frames[start_idx:], start_idx):
        ph = get_physics(frame)
        if not ph:
            continue
        wp = ph.get("world_position") or ph.get("worldPosition")
        if wp and isinstance(wp, dict):
            x = float(wp.get("x", x))
            z = float(wp.get("z", z))
        else:
            vx = ph["velocity"]["x"]
            vz = ph["velocity"]["z"]
            x += vx * dt
            z += vz * dt

        norm_pos = (
            ph.get("normalized_spline_position")
            or ph.get("spNormalizedCarPosition")
            or ph.get("normalizedCarPosition")
        )
        track.append({
            "frame": i,
            "x": x,
            "z": z,
            "speed": ph.get("speed_kmh", 0),
            "heading": ph.get("heading", 0),
            "steer": ph.get("steer_angle", 0),
            "brake": ph.get("brake", 0),
            "gas": ph.get("gas", 0),
            "gear": ph.get("gear", 0),
            "rpms": ph.get("rpms", 0),
            "norm_pos": float(norm_pos) if norm_pos is not None else None,
        })
    return track

# ─── Detect laps ──────────────────────────────────────────────────────────────

def _detect_laps_by_norm_pos(track, hz=1.0, min_lap_time_s=60.0):
    """Detect laps from the game's normalized spline position when present."""
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

def _detect_laps_by_position(track, hz=1.0, min_lap_time_s=60.0, warmup_time_s=40.0):
    """Fallback lap detection using dead-reckoned world position."""
    min_lap_frames = max(1, int(round(min_lap_time_s * hz)))
    warmup_frames = max(0, int(round(warmup_time_s * hz)))

    # Find a suitable S/F reference: first high-speed frame after warmup
    ref_pt = None
    for pt in track[warmup_frames:]:
        if pt["speed"] > 80 and abs(pt["steer"]) < 0.05:
            ref_pt = pt
            break
    if ref_pt is None:
        # Fallback: just use the warmup boundary
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

def detect_laps(track, hz=1.0, min_lap_time_s=60.0, warmup_time_s=40.0):
    """Detect lap boundaries, preferring normalized spline position."""
    norm_result = _detect_laps_by_norm_pos(track, hz=hz, min_lap_time_s=min_lap_time_s)
    if norm_result:
        print("Lap detection: using normalized car position")
        return norm_result

    print("Lap detection: using dead-reckoning position")
    return _detect_laps_by_position(
        track,
        hz=hz,
        min_lap_time_s=min_lap_time_s,
        warmup_time_s=warmup_time_s,
    )

# ─── Detect corners within a lap ─────────────────────────────────────────────

def detect_corners(track, lap_start_frame, lap_end_frame, hz=1.0,
                   dheading_rate_thresh=0.60, merge_gap_s=0.6,
                   min_dur_s=0.8):
    """
    Identify corners within a lap segment.
    A corner is a run of frames where the absolute heading change rate
    exceeds `dheading_rate_thresh` rad/s.
    Returns list of dicts: {id, start_frame, end_frame, apex_frame, apex_speed, ...}
    """
    merge_gap = max(1, int(round(merge_gap_s * hz)))
    min_dur = max(1, int(round(min_dur_s * hz)))

    seg = [dict(pt) for pt in track
           if lap_start_frame <= pt["frame"] < lap_end_frame]
    if len(seg) < 4:
        return []

    n = max(len(seg) - 1, 1)
    for idx, pt in enumerate(seg):
        pt["lap_pos"] = idx / n

    # Heading delta per frame
    corner_flags = [False]
    for i in range(1, len(seg)):
        dh = seg[i]["heading"] - seg[i - 1]["heading"]
        dh = (dh + math.pi) % (2 * math.pi) - math.pi  # normalize to [-π, π]
        corner_flags.append(abs(dh) * hz > dheading_rate_thresh)

    # Merge nearby corner flags
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

    # Filter short ones and compute apex
    result = []
    for cid, (ci_start, ci_end) in enumerate(corners):
        dur = ci_end - ci_start + 1
        if dur < min_dur:
            continue
        window = seg[ci_start: ci_end + 1]
        apex_idx = min(range(len(window)), key=lambda i: window[i]["speed"])
        apex = window[apex_idx]
        result.append({
            "id": cid,
            "start_frame": seg[ci_start]["frame"],
            "end_frame": seg[ci_end]["frame"],
            "apex_frame": apex["frame"],
            "apex_speed": apex["speed"],
            "min_speed": min(pt["speed"] for pt in window),
            "entry_speed": window[0]["speed"],
            "exit_speed": window[-1]["speed"],
            "apex_x": apex["x"],
            "apex_z": apex["z"],
            "lap_pos": seg[ci_start]["lap_pos"],
        })

    # Re-number corners
    for i, c in enumerate(result):
        c["id"] = i + 1

    return result

def detect_profiled_corners(track, lap_start_frame, lap_end_frame, profile):
    seg = [dict(pt) for pt in track if lap_start_frame <= pt["frame"] < lap_end_frame]
    if not seg:
        return []

    has_norm_pos = seg[0].get("norm_pos") is not None
    n = max(len(seg) - 1, 1)
    for idx, pt in enumerate(seg):
        pt["lap_pos"] = pt["norm_pos"] if has_norm_pos else idx / n

    result = []
    for spec in profile["corners"]:
        # Use half-open intervals so adjacent profiled corners do not share
        # the same boundary frame and report duplicated apex speeds.
        window = [pt for pt in seg if spec["start"] <= pt["lap_pos"] < spec["end"]]
        if not window:
            continue
        apex = min(window, key=lambda pt: pt["speed"])
        result.append({
            "id": spec["id"],
            "name": spec["name"],
            "start_frame": window[0]["frame"],
            "end_frame": window[-1]["frame"],
            "apex_frame": apex["frame"],
            "apex_speed": apex["speed"],
            "min_speed": min(pt["speed"] for pt in window),
            "entry_speed": window[0]["speed"],
            "exit_speed": window[-1]["speed"],
            "apex_x": apex["x"],
            "apex_z": apex["z"],
            "lap_pos": apex["lap_pos"],
        })

    return result


def match_profiled_corners(ref_corners, lap_corners):
    """Match profiled corners by stable corner id rather than lap position."""
    lap_by_id = {corner["id"]: corner for corner in lap_corners}
    return {ref_corner["id"]: lap_by_id.get(ref_corner["id"]) for ref_corner in ref_corners}

def match_corners(ref_corners, lap_corners, tol=0.15):
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

# ─── Main analysis ────────────────────────────────────────────────────────────

def analyze(path, track_name=None, config_name=None):
    meta, frames = load_frames(path)
    hz = float(meta.get("_hz", 1.0)) if meta else 1.0
    track_key, track_profile = select_track_profile(path=path, track_name=track_name, config_name=config_name)
    print(f"Loaded {len(frames)} frames at {hz} Hz "
          f"({len(frames) / hz / 60:.1f} min)")
    if track_profile:
        print(f"Track profile: {track_profile['display_name']}")
    else:
        print("Track profile: none - using auto corner detection")

    # Find driving start (first frame with speed > 5 km/h after initial standstill)
    drive_start = 0
    for i, f in enumerate(frames):
        ph = get_physics(f)
        if ph and ph.get("speed_kmh", 0) > 5:
            # Confirm sustained by checking a few frames ahead
            if all(get_physics(frames[min(i+j, len(frames)-1)]).get("speed_kmh", 0) > 2 
                   for j in range(5) if get_physics(frames[min(i+j, len(frames)-1)])):
                drive_start = max(0, i - 5)
                break

    track = build_track(frames, hz=hz, start_idx=drive_start)
    lap_bounds = detect_laps(track, hz=hz)

    # Build full laps (need at least 2 boundaries = 1 lap)
    laps = []
    for i in range(len(lap_bounds) - 1):
        s, e = lap_bounds[i], lap_bounds[i + 1]
        lap_track = [pt for pt in track if s <= pt["frame"] < e]
        if len(lap_track) < 20:
            continue
        if track_profile and track_profile["corners"]:
            corners = detect_profiled_corners(track, s, e, track_profile)
        else:
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
        print(f"Lap {i+1}: {lap_time:.0f}s  "
              f"max {max(pt['speed'] for pt in lap_track):.0f} km/h  "
              f"{len(corners)} corners detected")

    if not laps:
        print("ERROR: No complete laps detected. Check the file.")
        sys.exit(1)

    best_lap = min(laps, key=lambda lap: lap["lap_time_s"])
    ref_corners = best_lap["corners"]
    print(f"\nReference corners from Lap {best_lap['lap_num']} (best lap): {len(ref_corners)} corners")

    corner_data = defaultdict(dict)
    corner_speeds = defaultdict(dict)
    for lap in laps:
        if track_profile and track_profile["corners"]:
            matched = match_profiled_corners(ref_corners, lap["corners"])
        else:
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

    session_start = laps[0]["start_frame"]
    session_end = laps[-1]["end_frame"]
    telem = [pt for pt in track if session_start <= pt["frame"] < session_end]

    return {
        "meta": meta,
        "hz": hz,
        "track_key": track_key,
        "track_name": track_profile["track_name"] if track_profile else None,
        "config_key": track_profile["config_key"] if track_profile else None,
        "config_name": track_profile["config_name"] if track_profile else None,
        "track_label": track_profile["display_name"] if track_profile else None,
        "laps": laps,
        "best_lap_num": best_lap["lap_num"],
        "ref_corners": ref_corners,
        "corner_data": corner_data,
        "corner_speeds": corner_speeds,
        "telem": telem,
        "drive_start": drive_start,
        "lap_bounds": lap_bounds,
    }

# ─── HTML generation ──────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AC Evo Lap Analysis</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js"></script>
<style>
  :root {
    --bg: #0d0d0f;
    --panel: #16181d;
    --border: #2a2d36;
    --text: #e0e2ea;
    --muted: #6b7280;
    --accent: #3b82f6;
    --green: #22c55e;
    --red: #ef4444;
    --orange: #f97316;
    --yellow: #eab308;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; font-size: 14px; }
  header { background: var(--panel); border-bottom: 1px solid var(--border); padding: 14px 24px; display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 18px; font-weight: 600; letter-spacing: 0.02em; }
  header .sub { color: var(--muted); font-size: 12px; }
  .container { max-width: 1400px; margin: 0 auto; padding: 20px 24px; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
  .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-bottom: 16px; }
  @media (max-width: 900px) { .grid-2, .grid-3 { grid-template-columns: 1fr; } }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
  .card h2 { font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 12px; }
  .stat-row { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }
  .stat { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 10px 16px; min-width: 130px; }
  .stat .label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
  .stat .value { font-size: 22px; font-weight: 700; margin-top: 2px; }
  .lap-filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; align-items: center; }
  .lap-btn { background: var(--border); border: 1px solid transparent; border-radius: 6px; padding: 5px 13px; cursor: pointer; font-size: 12px; font-weight: 600; color: var(--muted); transition: all 0.15s; }
  .lap-btn.active { border-color: currentColor; color: var(--text); }
  .lap-btn:hover { background: #2a2d3a; }
  canvas { max-width: 100%; }
  .track-wrap { position: relative; }
  #track-canvas { border-radius: 6px; }
  .corner-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .corner-table th { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }
  .corner-table td { padding: 6px 10px; border-bottom: 1px solid #1e2028; }
  .corner-table tr:hover td { background: #1e2028; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; }
  .pill { display: inline-block; padding: 2px 7px; border-radius: 99px; font-size: 11px; background: var(--border); }
  .legend { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 10px; }
  .legend-item { display: flex; align-items: center; gap: 6px; font-size: 12px; }
  .legend-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  .section-title { font-size: 15px; font-weight: 700; margin: 20px 0 10px; padding-left: 2px; }
  select { background: var(--border); border: 1px solid #3a3d4a; border-radius: 6px; padding: 5px 10px; color: var(--text); font-size: 12px; cursor: pointer; }
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

  <!-- Summary stats -->
  <div class="stat-row" id="stats-row"></div>

  <!-- Track map + speed chart -->
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
      <div style="margin-top:8px; display:flex; gap:6px; align-items:center; font-size:11px; color:var(--muted)">
        <span>Low</span>
        <canvas id="colorbar" width="200" height="14" style="border-radius:3px"></canvas>
        <span>High</span>
        <span id="colorbar-label" style="margin-left:8px"></span>
      </div>
    </div>

    <div class="card">
      <h2>Speed Trace</h2>
      <div class="lap-filters" id="speed-lap-filters"></div>
      <canvas id="speed-chart" height="260"></canvas>
    </div>
  </div>

  <!-- Corner speed -->
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

  <!-- Brake / Gas / Gear -->
  <div class="section-title">Input Channels</div>
  <div class="card" style="margin-bottom:16px">
    <h2>Brake • Throttle • Gear</h2>
    <div class="lap-filters" id="inputs-lap-filters"></div>
    <canvas id="inputs-chart" height="200"></canvas>
  </div>

</div>

<script>
// ─── Injected telemetry data ──────────────────────────────────────────────────
const DATA = __DATA__;

// ─── Colour helpers ───────────────────────────────────────────────────────────
const LAP_COLORS = ['#3b82f6','#22c55e','#f97316','#a855f7','#eab308','#ec4899','#06b6d4'];
function lapColor(n) { return LAP_COLORS[(n - 1) % LAP_COLORS.length]; }

function speedColor(frac) {
  // blue → cyan → green → yellow → red
  const stops = [
    [0,   [30, 120, 255]],
    [0.25,[0,  210, 220]],
    [0.5, [0,  200, 80]],
    [0.75,[240,200, 0]],
    [1,   [255, 40, 40]],
  ];
  frac = Math.max(0, Math.min(1, frac));
  for (let i = 1; i < stops.length; i++) {
    if (frac <= stops[i][0]) {
      const t = (frac - stops[i-1][0]) / (stops[i][0] - stops[i-1][0]);
      const c0 = stops[i-1][1], c1 = stops[i][1];
      const r = Math.round(c0[0] + t*(c1[0]-c0[0]));
      const g = Math.round(c0[1] + t*(c1[1]-c0[1]));
      const b = Math.round(c0[2] + t*(c1[2]-c0[2]));
      return `rgb(${r},${g},${b})`;
    }
  }
  return 'rgb(255,40,40)';
}

function brakeColor(v) {
  const r = Math.round(60 + v * 195);
  return `rgb(${r},${Math.round(30*(1-v))},${Math.round(30*(1-v))})`;
}
function gasColor(v) {
  const g = Math.round(60 + v * 175);
  return `rgb(${Math.round(30*(1-v))},${g},${Math.round(30*(1-v))})`;
}

// ─── Lap filters widget ───────────────────────────────────────────────────────
const activeLaps = new Set(DATA.laps.map(l => l.lap_num));

function makeLapFilters(containerId, onChange) {
  const el = document.getElementById(containerId);
  el.innerHTML = '<span style="font-size:12px;color:var(--muted);margin-right:4px">Laps:</span>';
  DATA.laps.forEach(lap => {
    const btn = document.createElement('button');
    btn.className = 'lap-btn active';
    btn.style.color = lapColor(lap.lap_num);
    btn.textContent = `L${lap.lap_num}${lap.lap_num===DATA.best_lap_num?'*':''} - ${lap.lap_time_str}`;
    btn.dataset.lap = lap.lap_num;
    btn.addEventListener('click', () => {
      if (activeLaps.has(lap.lap_num)) activeLaps.delete(lap.lap_num);
      else activeLaps.add(lap.lap_num);
      btn.classList.toggle('active', activeLaps.has(lap.lap_num));
      onChange();
    });
    el.appendChild(btn);
  });
}

// Sync all lap filter buttons
function syncFilterButtons() {
  document.querySelectorAll('.lap-btn').forEach(btn => {
    const n = parseInt(btn.dataset.lap);
    btn.classList.toggle('active', activeLaps.has(n));
  });
}

// ─── Summary stats ────────────────────────────────────────────────────────────
function renderStats() {
  const row = document.getElementById('stats-row');
  const bestLap = DATA.laps.find(l => l.lap_num === DATA.best_lap_num)
    || DATA.laps.reduce((best, lap) => lap.lap_time_s < best.lap_time_s ? lap : best, DATA.laps[0]);
  const maxSpd = Math.max(...DATA.laps.map(l => l.max_speed));
  const stats = [
    { label: 'Laps', value: DATA.laps.length },
    { label: 'Best Lap', value: bestLap.lap_time_str },
    { label: 'Top Speed', value: maxSpd.toFixed(0) + ' km/h' },
    { label: 'Corners / Lap', value: DATA.ref_corners.length },
  ];
  row.innerHTML = stats.map(s =>
    `<div class="stat"><div class="label">${s.label}</div><div class="value">${s.value}</div></div>`
  ).join('');
  const prefix = DATA.track_label || DATA.track_name || '';
  document.getElementById('session-info').textContent =
    `${prefix ? prefix + '  |  ' : ''}${DATA.laps.length} laps detected  -  best ${bestLap.lap_time_str}`;
}

// ─── Track map ────────────────────────────────────────────────────────────────
function drawTrackMap() {
  const canvas = document.getElementById('track-canvas');
  const mode = document.getElementById('map-color-mode').value;
  const sel = document.getElementById('map-lap-select');
  const lapNum = parseInt(sel.value);
  const lap = DATA.laps.find(l => l.lap_num === lapNum);
  if (!lap) return;

  const pts = lap.track;
  const xs = pts.map(p => p.x), zs = pts.map(p => p.z);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minZ = Math.min(...zs), maxZ = Math.max(...zs);

  // Square canvas that fits parent
  const wrap = canvas.parentElement;
  const size = Math.min(wrap.clientWidth, 420);
  canvas.width = size; canvas.height = size;
  const pad = 24;
  const scaleX = (size - 2*pad) / (maxX - minX || 1);
  const scaleZ = (size - 2*pad) / (maxZ - minZ || 1);
  const sc = Math.min(scaleX, scaleZ);
  const offX = pad + ((size - 2*pad) - (maxX-minX)*sc) / 2;
  const offZ = pad + ((size - 2*pad) - (maxZ-minZ)*sc) / 2;

  const cx = x => offX + (x - minX) * sc;
  const cz = z => offZ + (z - minZ) * sc;

  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, size, size);
  ctx.fillStyle = '#0d0f14';
  ctx.fillRect(0, 0, size, size);

  // Determine range for color mapping
  let vals;
  if (mode === 'speed') {
    vals = pts.map(p => p.speed);
  } else if (mode === 'brake') {
    vals = pts.map(p => p.brake);
  } else {
    vals = pts.map(p => p.gas);
  }
  const minV = Math.min(...vals), maxV = Math.max(...vals);

  // Draw track as thick background line first
  ctx.beginPath();
  ctx.strokeStyle = '#1e2028';
  ctx.lineWidth = 12;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  pts.forEach((p, i) => {
    i === 0 ? ctx.moveTo(cx(p.x), cz(p.z)) : ctx.lineTo(cx(p.x), cz(p.z));
  });
  ctx.stroke();

  // Draw colored segments
  for (let i = 1; i < pts.length; i++) {
    const p0 = pts[i - 1], p1 = pts[i];
    const frac = (vals[i] - minV) / (maxV - minV || 1);
    ctx.beginPath();
    ctx.lineWidth = 6;
    ctx.strokeStyle = mode === 'speed' ? speedColor(frac)
      : mode === 'brake' ? brakeColor(vals[i])
      : gasColor(vals[i]);
    ctx.moveTo(cx(p0.x), cz(p0.z));
    ctx.lineTo(cx(p1.x), cz(p1.z));
    ctx.stroke();
  }

  // Draw corner markers
  const corners = lap.corners;
  corners.forEach(c => {
    const p = pts.find(pt => pt.frame === c.apex_frame) || pts[0];
    ctx.beginPath();
    ctx.arc(cx(p.x), cz(p.z), 6, 0, Math.PI * 2);
    ctx.fillStyle = '#fff';
    ctx.fill();
    ctx.fillStyle = '#000';
    ctx.font = 'bold 9px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(c.id, cx(p.x), cz(p.z));
  });

  // Start/finish marker
  const start = pts[0];
  ctx.beginPath();
  ctx.arc(cx(start.x), cz(start.z), 8, 0, Math.PI * 2);
  ctx.fillStyle = '#fff';
  ctx.fill();
  ctx.fillStyle = '#111';
  ctx.font = 'bold 9px sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('S', cx(start.x), cz(start.z));

  // Colorbar
  drawColorbar(mode, minV, maxV);
}

function drawColorbar(mode, minV, maxV) {
  const cb = document.getElementById('colorbar');
  const ctx = cb.getContext('2d');
  const grad = ctx.createLinearGradient(0, 0, 200, 0);
  if (mode === 'speed') {
    for (let i = 0; i <= 10; i++) { grad.addColorStop(i/10, speedColor(i/10)); }
  } else if (mode === 'brake') {
    grad.addColorStop(0, brakeColor(0)); grad.addColorStop(1, brakeColor(1));
  } else {
    grad.addColorStop(0, gasColor(0)); grad.addColorStop(1, gasColor(1));
  }
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 200, 14);
  document.getElementById('colorbar-label').textContent =
    mode === 'speed' ? `${minV.toFixed(0)}–${maxV.toFixed(0)} km/h`
    : '0–100%';
}

// ─── Speed chart ─────────────────────────────────────────────────────────────
let speedChart = null;
function buildSpeedChart() {
  const ctx = document.getElementById('speed-chart').getContext('2d');
  if (speedChart) speedChart.destroy();

  const datasets = DATA.laps
    .filter(l => activeLaps.has(l.lap_num))
    .map(lap => ({
      label: `Lap ${lap.lap_num}${lap.lap_num===DATA.best_lap_num?'*':''} (${lap.lap_time_str})`,
      data: lap.track.map((pt, i) => ({ x: i / Math.max(lap.track.length - 1, 1) * 100, y: pt.speed })),
      borderColor: lapColor(lap.lap_num),
      backgroundColor: 'transparent',
      borderWidth: 1.8,
      pointRadius: 0,
      tension: 0.3,
    }));

  // Add corner shading on the best lap
  const activeLap = DATA.laps.find(l => l.lap_num === DATA.best_lap_num) || DATA.laps[0];
  const annotations = {};
  if (activeLap) {
    activeLap.corners.forEach(c => {
      const s = (c.start_frame - activeLap.start_frame) / Math.max(activeLap.track.length - 1, 1) * 100;
      const e = (c.end_frame - activeLap.start_frame) / Math.max(activeLap.track.length - 1, 1) * 100;
      annotations[`corner${c.id}`] = {
        type: 'box',
        xMin: s, xMax: e,
        backgroundColor: 'rgba(255,255,255,0.04)',
        borderColor: 'rgba(255,255,255,0.1)',
        borderWidth: 1,
        label: { content: c.name || `C${c.id}`, display: true, color: '#9ca3af', font: { size: 9 } },
      };
    });
  }

  speedChart = new Chart(ctx, {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: { type: 'linear', min: 0, max: 100, title: { display: true, text: 'Lap progress (%)', color: '#6b7280' },
             grid: { color: '#1e2028' }, ticks: { color: '#6b7280', callback: v => v + '%' } },
        y: { title: { display: true, text: 'Speed (km/h)', color: '#6b7280' },
             grid: { color: '#1e2028' }, ticks: { color: '#6b7280' } },
      },
      plugins: {
        legend: { labels: { color: '#e0e2ea', boxWidth: 12 } },
        annotation: { annotations },
      }
    }
  });
}

// ─── Corner speed bar chart ────────────────────────────────────────────────
let cornerChart = null;
function buildCornerChart() {
  const ctx = document.getElementById('corner-chart').getContext('2d');
  if (cornerChart) cornerChart.destroy();

  const labels = DATA.ref_corners.map(c => c.name || `C${c.id}`);
  const datasets = DATA.laps.map(lap => ({
    label: `Lap ${lap.lap_num}`,
    data: DATA.ref_corners.map(c => {
      const s = DATA.corner_speeds[c.id];
      return s ? (s[lap.lap_num] || null) : null;
    }),
    backgroundColor: lapColor(lap.lap_num) + 'cc',
    borderColor: lapColor(lap.lap_num),
    borderWidth: 1,
    borderRadius: 4,
  }));

  cornerChart = new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets },
    options: {
      responsive: true,
      animation: false,
      interaction: { mode: 'index' },
      scales: {
        x: { grid: { color: '#1e2028' }, ticks: { color: '#6b7280' } },
        y: { title: { display: true, text: 'Apex Speed (km/h)', color: '#6b7280' },
             grid: { color: '#1e2028' }, ticks: { color: '#6b7280' } },
      },
      plugins: { legend: { labels: { color: '#e0e2ea', boxWidth: 12 } } }
    }
  });
}

// ─── Corner speed table ───────────────────────────────────────────────────────
function buildCornerTable() {
  const table = document.getElementById('corner-table');
  const lapNums = DATA.laps.map(l => l.lap_num);

  let html = `<thead><tr><th>Corner</th>${lapNums.map(n => `<th>Lap ${n}</th>`).join('')}<th>Δ Best–Worst</th></tr></thead><tbody>`;

  DATA.ref_corners.forEach(c => {
    const speeds = DATA.corner_data?.[c.id] || {};
    const vals = lapNums.map(n => speeds[n]?.apex).filter(v => v !== undefined);
    const best = vals.length ? Math.max(...vals) : null;
    const worst = vals.length ? Math.min(...vals) : null;
    const delta = best !== null ? (best - worst).toFixed(1) : '—';

    html += `<tr><td><span class="badge" style="background:var(--border)">${c.name || `C${c.id}`}</span></td>`;
    lapNums.forEach(n => {
      const v = speeds[n]?.apex;
      if (v === undefined) { html += `<td style="color:var(--muted)">-</td>`; return; }
      const isB = v === best;
      const isW = v === worst;
      const color = isB ? 'var(--green)' : isW ? 'var(--red)' : 'var(--text)';
      html += `<td style="color:${color};font-weight:${isB||isW?700:400}">${v.toFixed(1)}</td>`;
    });
    html += `<td style="color:var(--orange)">${delta}</td></tr>`;
  });

  html += '</tbody>';
  table.innerHTML = html;
}

// ─── Inputs chart ────────────────────────────────────────────────────────────
let inputsChart = null;
function buildInputsChart() {
  const ctx = document.getElementById('inputs-chart').getContext('2d');
  if (inputsChart) inputsChart.destroy();

  const activeLapsList = DATA.laps.filter(l => activeLaps.has(l.lap_num));
  if (!activeLapsList.length) return;
  const lap = activeLapsList[0];

  const datasets = [
    {
      label: 'Brake',
      data: lap.track.map((pt, i) => ({ x: i / Math.max(lap.track.length - 1, 1) * 100, y: pt.brake * 100 })),
      borderColor: '#ef4444', backgroundColor: 'rgba(239,68,68,0.15)',
      fill: true, borderWidth: 1.5, pointRadius: 0, tension: 0.2,
    },
    {
      label: 'Throttle',
      data: lap.track.map((pt, i) => ({ x: i / Math.max(lap.track.length - 1, 1) * 100, y: pt.gas * 100 })),
      borderColor: '#22c55e', backgroundColor: 'rgba(34,197,94,0.1)',
      fill: true, borderWidth: 1.5, pointRadius: 0, tension: 0.2,
    },
    {
      label: 'Gear × 10',
      data: lap.track.map((pt, i) => ({ x: i / Math.max(lap.track.length - 1, 1) * 100, y: pt.gear * 10 })),
      borderColor: '#eab308', backgroundColor: 'transparent',
      borderWidth: 1.5, pointRadius: 0, tension: 0,
      borderDash: [4, 2],
    },
  ];

  inputsChart = new Chart(ctx, {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true,
      animation: false,
      scales: {
        x: { type: 'linear', min: 0, max: 100, grid: { color: '#1e2028' }, ticks: { color: '#6b7280', callback: v => v + '%' } },
        y: { min: 0, max: 100, grid: { color: '#1e2028' }, ticks: { color: '#6b7280', callback: v => v + '%' } },
      },
      plugins: { legend: { labels: { color: '#e0e2ea', boxWidth: 12 } } }
    }
  });
}

// ─── Init ─────────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  renderStats();

  // Lap select for map
  const sel = document.getElementById('map-lap-select');
  DATA.laps.forEach(l => {
    const opt = document.createElement('option');
    opt.value = l.lap_num;
    opt.textContent = `Lap ${l.lap_num}${l.lap_num===DATA.best_lap_num?'*':''} (${l.lap_time_str})`;
    sel.appendChild(opt);
  });

  makeLapFilters('speed-lap-filters', () => { syncFilterButtons(); buildSpeedChart(); buildInputsChart(); });
  makeLapFilters('inputs-lap-filters', () => { syncFilterButtons(); buildSpeedChart(); buildInputsChart(); });

  drawTrackMap();
  buildSpeedChart();
  buildCornerChart();
  buildCornerTable();
  buildInputsChart();
});
</script>
</body>
</html>
"""

def render_html(data, output_path):
    """Serialize the analysis data to JSON and inject into the HTML template."""
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
            }
            for pt in lap["track"]
        ]
        corners_json = []
        for c in lap["corners"]:
            corners_json.append({
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
            })
        laps_json.append({
            "lap_num": lap["lap_num"],
            "start_frame": lap["start_frame"],
            "end_frame": lap["end_frame"],
            "lap_time_s": round(lap["lap_time_s"], 2),
            "lap_time_str": lap["lap_time_str"],
            "max_speed": round(lap["max_speed"], 1),
            "avg_speed": round(lap["avg_speed"], 1),
            "track": track_slim,
            "corners": corners_json,
        })

    ref_corners_json = [
        {"id": c["id"], "name": c.get("name"), "lap_pos": round(c["lap_pos"], 4)}
        for c in data["ref_corners"]
    ]

    corner_data_json = {}
    for cid, lap_dict in data["corner_data"].items():
        corner_data_json[str(cid)] = {
            str(int(ln)): snapshot for ln, snapshot in lap_dict.items()
        }

    corner_speeds_json = {}
    for cid, lap_dict in data["corner_speeds"].items():
        corner_speeds_json[str(cid)] = {
            str(int(ln)): round(float(spd), 1) for ln, spd in lap_dict.items()
        }

    payload = {
        "track_name": data.get("track_name"),
        "config_name": data.get("config_name"),
        "track_label": data.get("track_label"),
        "best_lap_num": data.get("best_lap_num"),
        "laps": laps_json,
        "ref_corners": ref_corners_json,
        "corner_data": corner_data_json,
        "corner_speeds": corner_speeds_json,
    }

    import json as _json
    json_str = _json.dumps(payload)
    html = HTML_TEMPLATE.replace("__DATA__", json_str)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nSaved -> {output_path}")

# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze AC Evo JSONL telemetry into an HTML report")
    parser.add_argument("input_path", help="Input JSONL path")
    parser.add_argument("output_path", nargs="?", help="Output HTML path")
    parser.add_argument("--track", dest="track_name", help="Track key/name override, e.g. spa or laguna_seca")
    parser.add_argument("--config", dest="config_name", help="Track configuration override, e.g. current, gp, indy")
    args = parser.parse_args()

    input_path = args.input_path
    if not os.path.exists(input_path):
        print(f"ERROR: File not found: {input_path}")
        sys.exit(1)

    base = os.path.splitext(input_path)[0]
    output_path = args.output_path if args.output_path else f"{base}_analysis.html"

    try:
        data = analyze(input_path, track_name=args.track_name, config_name=args.config_name)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    render_html(data, output_path)
