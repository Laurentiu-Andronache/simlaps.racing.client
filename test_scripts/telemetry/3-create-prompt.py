#!/usr/bin/env python3
"""
AC Evo AI Coaching Prompt Generator
Generates a rich, data-dense coaching prompt from a JSONL telemetry session.

Usage:
    python3 create_ai_prompt.py <telemetry.jsonl> [options]

Options:
    --car CAR           Car name/class (e.g. "Porsche 911 GT3", "GT4")
    --driver LEVEL      Driver level: beginner / intermediate / advanced
    --goal GOAL         Session goal: "reduce lap time" / "improve consistency" / "learn track"
    --notes NOTES       Free-text context, e.g. "new to this track, struggling with Corkscrew"
    --best-ref TIME     Optional known reference lap time to benchmark against (e.g. 2:38.50)
    --track TRACK       Track override passed to analyzer
    --config CONFIG     Config override passed to analyzer
"""

import argparse
import json
import os
import sys
import importlib.util

# ─── Load analyzer ────────────────────────────────────────────────────────────

def load_analyzer():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for candidate in ["2-analyze.py", "ac_lap_analyzer.py", "analyzer.py"]:
        p = os.path.join(script_dir, candidate)
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location("analyzer", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    print("ERROR: Could not find analyzer script (2-analyze.py / ac_lap_analyzer.py)")
    sys.exit(1)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def fmt_time(seconds):
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m}:{s:05.2f}"

def find_brake_start(window, apex_idx, threshold=0.05):
    """Walk backwards from apex to find where braking began. Returns lap_pos or None."""
    for i in range(apex_idx, max(0, apex_idx - len(window)), -1):
        if i < len(window) and window[i].get("brake", 0) < threshold:
            return window[i].get("lap_pos")
    return None

def find_throttle_on(window, apex_idx, threshold=0.15):
    """Walk forwards from apex to find first sustained throttle application. Returns lap_pos or None."""
    for i in range(apex_idx, min(len(window), apex_idx + len(window))):
        if window[i].get("gas", 0) > threshold:
            return window[i].get("lap_pos")
    return None

def get_corner_detail(lap, corner_id):
    """Return the full corner dict from a lap's corners list, or None."""
    return next((c for c in lap["corners"] if c["id"] == corner_id), None)

def corner_segment_time(corner, hz):
    """Seconds elapsed from corner start_frame to end_frame."""
    return (corner["end_frame"] - corner["start_frame"]) / hz

def variation_label(delta_kmh):
    if delta_kmh >= 25:
        return "🔴 HIGH"
    if delta_kmh >= 15:
        return "🟠 MEDIUM"
    return "🟢 LOW"

def classify_corner_issue(entry_delta, apex_delta, exit_delta):
    """
    Heuristic: given speed deltas (best - worst) at entry/apex/exit,
    suggest the most likely root cause.
    """
    if entry_delta > apex_delta and entry_delta > exit_delta:
        return "Braking inconsistency — arriving at different speeds"
    if exit_delta > entry_delta and exit_delta > apex_delta:
        return "Throttle application point varies — losing drive on exit"
    if apex_delta > entry_delta and apex_delta > exit_delta:
        return "Line variation — mid-corner speed differs despite similar entry"
    return "Mixed — entry and exit both vary"

# ─── Core prompt builder ──────────────────────────────────────────────────────

def build_prompt(data, hz, args):
    laps      = data["laps"]
    ref_corners = data["ref_corners"]
    corner_speeds = data["corner_speeds"]

    best_lap  = min(laps, key=lambda l: l["lap_time_s"])
    worst_lap = max(laps, key=lambda l: l["lap_time_s"])
    time_diff = worst_lap["lap_time_s"] - best_lap["lap_time_s"]

    track_label = (
        data.get("track_label")
        or data.get("track_name")
        or "Unknown Track"
    )

    # ── Preamble / persona ────────────────────────────────────────────────────
    lines = [
        "You are an expert motorsport race engineer and driving coach with deep knowledge of",
        f"{track_label}. Analyse the telemetry data below from an Assetto Corsa Evo session",
        "and give specific, actionable coaching feedback grounded in the numbers provided.",
        "",
    ]

    # ── Session context (optional user-supplied fields) ───────────────────────
    lines.append("SESSION CONTEXT:")
    lines.append(f"- Track:          {track_label}")
    if args.car:
        lines.append(f"- Car:            {args.car}")
    if args.driver:
        lines.append(f"- Driver level:   {args.driver}")
    if args.goal:
        lines.append(f"- Session goal:   {args.goal}")
    if args.best_ref:
        lines.append(f"- Reference time: {args.best_ref} (benchmark / world-class for this car/track)")
    if args.notes:
        lines.append(f"- Driver notes:   {args.notes}")
    lines.append("")

    # ── Session overview ──────────────────────────────────────────────────────
    lines.append("SESSION OVERVIEW:")
    lines.append(f"- Total laps analysed: {len(laps)}")
    lines.append(f"- Best lap:   #{best_lap['lap_num']}  {best_lap['lap_time_str']}")
    lines.append(f"- Worst lap:  #{worst_lap['lap_num']}  {worst_lap['lap_time_str']}")
    lines.append(f"- Delta best/worst: {time_diff:.2f}s")
    lines.append(f"- Top speed: {max(l['max_speed'] for l in laps):.1f} km/h")
    if args.best_ref:
        try:
            ref_s = sum(float(x) * 60**i for i, x in enumerate(reversed(args.best_ref.split(":"))))
            gap = best_lap["lap_time_s"] - ref_s
            lines.append(f"- Gap to reference: +{gap:.2f}s")
        except Exception:
            pass
    lines.append("")

    # ── Lap-by-lap summary ────────────────────────────────────────────────────
    lines.append("LAP-BY-LAP SUMMARY:")
    for lap in laps:
        marker = " ← BEST" if lap["lap_num"] == best_lap["lap_num"] else \
                 " ← WORST" if lap["lap_num"] == worst_lap["lap_num"] else ""
        lines.append(
            f"  Lap {lap['lap_num']}: {lap['lap_time_str']}  "
            f"max {lap['max_speed']:.1f} km/h  "
            f"avg {lap['avg_speed']:.1f} km/h{marker}"
        )
    lines.append("")

    # ── Corner-by-corner analysis ─────────────────────────────────────────────
    lines.append("CORNER-BY-CORNER ANALYSIS:")
    lines.append("(entry/apex/exit speeds in km/h; segment time = frames in corner / hz)")
    lines.append("")

    best_corners  = {c["id"]: c for c in best_lap["corners"]}
    worst_corners = {c["id"]: c for c in worst_lap["corners"]}

    for spec in ref_corners:
        cid  = spec["id"]
        name = spec.get("name") or f"Corner {cid}"
        speeds = corner_speeds.get(cid, {})
        if not speeds:
            continue

        apex_vals = list(speeds.values())
        best_apex  = max(apex_vals)
        worst_apex = min(apex_vals)
        variation  = best_apex - worst_apex

        bc = best_corners.get(cid)
        wc = worst_corners.get(cid)

        lines.append(f"--- {name} (Corner {cid}) ---")
        lines.append(f"  Variation: {variation:.1f} km/h  {variation_label(variation)}")

        # Apex speeds across all laps
        lap_speed_strs = [f"Lap {ln}: {spd:.1f}" for ln, spd in sorted(speeds.items())]
        lines.append(f"  Apex speeds:  {',  '.join(lap_speed_strs)}")
        lines.append(f"  Best apex:    {best_apex:.1f} km/h (Lap {max(speeds, key=speeds.get)})")
        lines.append(f"  Worst apex:   {worst_apex:.1f} km/h (Lap {min(speeds, key=speeds.get)})")

        # Entry / exit comparison between best and worst lap
        if bc and wc:
            entry_delta = bc["entry_speed"] - wc["entry_speed"]
            apex_delta  = bc["apex_speed"]  - wc["apex_speed"]
            exit_delta  = bc["exit_speed"]  - wc["exit_speed"]

            lines.append(
                f"  Entry  — best lap: {bc['entry_speed']:.1f}  |  "
                f"worst lap: {wc['entry_speed']:.1f}  |  Δ {entry_delta:+.1f} km/h"
            )
            lines.append(
                f"  Apex   — best lap: {bc['apex_speed']:.1f}  |  "
                f"worst lap: {wc['apex_speed']:.1f}  |  Δ {apex_delta:+.1f} km/h"
            )
            lines.append(
                f"  Exit   — best lap: {bc['exit_speed']:.1f}  |  "
                f"worst lap: {wc['exit_speed']:.1f}  |  Δ {exit_delta:+.1f} km/h"
            )

            # Segment time delta
            best_seg  = corner_segment_time(bc, hz)
            worst_seg = corner_segment_time(wc, hz)
            seg_delta = worst_seg - best_seg
            lines.append(
                f"  Segment time — best: {best_seg:.2f}s  |  "
                f"worst: {worst_seg:.2f}s  |  Δ +{seg_delta:.2f}s"
            )

            # Diagnosed likely cause
            issue = classify_corner_issue(entry_delta, apex_delta, exit_delta)
            lines.append(f"  Likely issue: {issue}")

        lines.append("")

    # ── Ranked time-loss summary ──────────────────────────────────────────────
    lines.append("TIME LOSS RANKING (worst → best, by segment time delta):")
    ranked = []
    for spec in ref_corners:
        cid = spec["id"]
        bc  = best_corners.get(cid)
        wc  = worst_corners.get(cid)
        if bc and wc:
            delta = corner_segment_time(wc, hz) - corner_segment_time(bc, hz)
            ranked.append((delta, spec.get("name") or f"Corner {cid}", cid))
    ranked.sort(reverse=True)
    for delta, name, cid in ranked:
        lines.append(f"  {name:<30} +{delta:.2f}s")
    lines.append("")

    # ── Time analysis ─────────────────────────────────────────────────────────
    lines.append("OVERALL TIME ANALYSIS:")
    lines.append(f"  Best lap:  #{best_lap['lap_num']}  {best_lap['lap_time_str']}")
    lines.append(f"  Worst lap: #{worst_lap['lap_num']}  {worst_lap['lap_time_str']}")
    lines.append(f"  Delta: {time_diff:.2f}s")
    lines.append("")

    # ── Coaching request ──────────────────────────────────────────────────────
    lines.append("=" * 60)
    lines.append("COACHING REQUEST:")
    lines.append("")
    lines.append("Using the telemetry data above, provide specific, actionable coaching feedback:")
    lines.append("")
    lines.append("1. TIME LOSS PRIORITIES")
    lines.append("   Which corners are costing the most time and why?")
    lines.append("   Use the segment time deltas and entry/exit speed data, not just apex speed.")
    lines.append("")
    lines.append("2. CORNER TECHNIQUE — for each high/medium variation corner:")
    lines.append("   - Brake point and release")
    lines.append("   - Turn-in and apex")
    lines.append("   - Throttle pickup point and exit")
    lines.append("   - What the entry/exit delta pattern tells you about the driver's habit")
    lines.append("")
    lines.append("3. CONSISTENCY DIAGNOSIS")
    lines.append("   For corners with HIGH variation, diagnose whether this is a")
    lines.append("   reference-point problem, confidence problem, or technique problem.")
    lines.append("")
    lines.append("4. SINGLE BIGGEST IMPROVEMENT")
    lines.append("   What one change would yield the most lap time?")
    lines.append("   Be specific: not 'brake later' but 'at the Corkscrew, your entry speed")
    lines.append("   varies by X km/h — pick the 150m board as a fixed brake reference.'")
    lines.append("")
    if args.best_ref:
        lines.append("5. PATH TO REFERENCE TIME")
        lines.append(f"   The driver is {time_diff:.2f}s off their own best and further from the")
        lines.append(f"   {args.best_ref} reference. Map out where the remaining time is.")
        lines.append("")
    lines.append("Ground every recommendation in the specific km/h and time figures above.")
    lines.append("=" * 60)

    return "\n".join(lines)

# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate AI coaching prompt from AC Evo telemetry")
    parser.add_argument("input_path",           help="Input JSONL telemetry file")
    parser.add_argument("--car",                help="Car name/class")
    parser.add_argument("--driver",             help="Driver level: beginner/intermediate/advanced")
    parser.add_argument("--goal",               help="Session goal")
    parser.add_argument("--notes",              help="Free-text driver notes / context")
    parser.add_argument("--best-ref",           dest="best_ref", help="Reference lap time e.g. 2:38.50")
    parser.add_argument("--track",              dest="track_name", help="Track override")
    parser.add_argument("--config",             dest="config_name", help="Config override")
    args = parser.parse_args()

    if not os.path.exists(args.input_path):
        print(f"ERROR: File not found: {args.input_path}")
        sys.exit(1)

    analyzer = load_analyzer()

    print(f"Analysing {args.input_path}...")
    data = analyzer.analyze(
        args.input_path,
        track_name=args.track_name,
        config_name=args.config_name,
    )

    if not data or not data.get("laps"):
        print("ERROR: No valid laps found.")
        sys.exit(1)

    # Pull hz out of meta so we can compute segment times
    meta = data.get("meta") or {}
    hz   = float(meta.get("_hz", 1.0))

    prompt = build_prompt(data, hz, args)

    # Save
    base        = os.path.splitext(args.input_path)[0]
    output_path = f"{base}_ai_coaching_prompt.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# AC Evo AI Coaching Prompt\n\n")
        f.write("Copy and paste this into ChatGPT, Claude, or any AI assistant:\n\n")
        f.write("```\n")
        f.write(prompt)
        f.write("\n```\n")

    print(f"\nSaved -> {output_path}")
    print(f"Prompt length: {len(prompt):,} characters")
    print("Ready to paste into any AI assistant.")

if __name__ == "__main__":
    main()
