"""AI coaching prompt generation — extracted from TelemetryAnalyzer._generate_ai_prompt."""

import math
import os
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.core.car_tuning_catalog import format_tuning_block
from src.core.analyzer.corner_detection import corner_segment_time
from src.core.analyzer.metrics import (
    analyze_corner_phases,
    analyze_grip_utilization,
    analyze_tyre_grip_degradation,
    analyze_electronics_per_lap,
    analyze_brake_thermals,
    analyze_suspension,
    analyze_steering_smoothness,
    analyze_throttle_exit,
)
from src.core.analyzer._util import (
    variation_label,
    classify_corner_issue,
    format_car_state,
    balance_hint,
    _trend_direction,
)
from src.utils.structured_logger import log_debug, Component


async def generate_ai_prompt(
    data: Dict[str, Any],
    output_dir: str,
    output_prefix: Optional[str] = None,
) -> str:
    """Generate detailed AI coaching prompt with per-corner analysis and setup recommendations."""
    prefix = output_prefix or datetime.now().strftime("%m-%d-%H-%M-%S")
    ai_prompt_path = os.path.join(output_dir, f"telemetry_{prefix}_ai_prompt.txt")

    os.makedirs(output_dir, exist_ok=True)

    laps = data.get("laps", [])
    if not laps:
        with open(ai_prompt_path, "w", encoding="utf-8") as f:
            f.write("No telemetry data available for coaching.\n")
        return ai_prompt_path

    hz = data.get("hz", 10.0)
    valid_laps = [lap for lap in laps if lap.get("is_valid", True)]
    requested_best_lap_num = data.get("best_lap_num")
    best_lap = next(
        (
            lap
            for lap in valid_laps
            if lap.get("lap_num") == requested_best_lap_num
        ),
        None,
    )
    if best_lap is None and valid_laps:
        best_lap = min(valid_laps, key=lambda lap: lap["lap_time_s"])
    no_valid_laps = best_lap is None
    if best_lap is None:
        # The analyzer forces diagnostic mode when no valid lap exists. Keep
        # a local fallback only so legacy/direct callers still get a useful
        # diagnostic prompt without labelling the lap as a valid session best.
        best_lap = min(laps, key=lambda lap: lap["lap_time_s"])
    worst_lap = max(laps, key=lambda l: l["lap_time_s"])
    time_diff = worst_lap["lap_time_s"] - best_lap["lap_time_s"]
    track_label = data.get("track_label") or data.get("track_name") or "Unknown Track"
    ref_corners = data.get("ref_corners", [])
    corner_speeds = data.get("corner_speeds", {})
    analysis_mode = data.get("analysis_mode", "diagnostic")
    analysis_confidence = data.get("analysis_confidence", "low")
    analysis_notes = list(data.get("analysis_notes", []))
    if no_valid_laps:
        analysis_mode = "diagnostic"
        ref_corners = []
        note = "No valid completed laps were available; invalid laps are shown for diagnostics only."
        if note not in analysis_notes:
            analysis_notes.append(note)
    authoritative_progress_ratio = float(data.get("authoritative_progress_ratio", 0.0) or 0.0)
    plausible_frame_ratio = float(data.get("plausible_frame_ratio", 0.0) or 0.0)
    reference_lap_num = data.get("reference_lap_num", best_lap["lap_num"])
    comparison_lap_num = data.get("comparison_lap_num", best_lap["lap_num"])

    # ── Car name from shared session data
    car_model: str = data.get("car") or "Unknown Car"

    lines: List[str] = []

    if analysis_mode != "full" or not ref_corners:
        lines.append("Telemetry coaching is running in DIAGNOSTIC mode.")
        lines.append("")
        lines.append(f"Track: {track_label}")
        lines.append(f"Car: {car_model}")
        lines.append(f"Laps available: {len(laps)}")
        lines.append(f"Analysis confidence: {analysis_confidence}")
        lines.append(f"Authoritative progress coverage: {authoritative_progress_ratio:.0%}")
        lines.append(f"Plausible physics coverage: {plausible_frame_ratio:.0%}")
        lines.append("")
        lines.append("Detailed corner coaching has been suppressed because the lap alignment is not trustworthy enough.")
        if analysis_notes:
            lines.append("")
            lines.append("Reasons:")
            for note in analysis_notes:
                lines.append(f"- {note}")
        lines.append("")
        lines.append("Use the session only for diagnostics until graphics-based progress coverage is reliable.")

        with open(ai_prompt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return ai_prompt_path

    # ── Car name from shared session data
    car_model: str = data.get("car") or "Unknown Car"
    car_known = car_model != "Unknown Car"

    # ── Preamble / persona
    lines.append(
        f"You are an expert Assetto Corsa Evo race engineer. "
        f"Analyse telemetry for the {car_model} at {track_label}."
    )
    lines.append(
        "Your entire response must be CONCISE. "
        "Use bullet points. No padding, no repetition. "
        "Every claim must reference a specific number from the telemetry data below."
    )
    tuning_block = format_tuning_block(car_model) if car_known else ""
    if car_known and tuning_block:
        lines.append(
            f"The available setup parameters for the {car_model} in AC Evo are listed "
            f"in the CAR SETUP PARAMETERS section below. "
            f"Only recommend changes from that list."
        )
    elif car_known:
        lines.append(
            f"The {car_model} setup parameters are not in the catalog. "
            f"Only recommend setup changes for parameters adjustable in the car's setup screen. "
            f"Do NOT assume brake bias is adjustable -- many road cars have fixed brake bias. "
            f"Limit advice to tyre pressures, alignment, and parameters you are certain this car exposes."
        )
    else:
        lines.append(
            "Car identity was not captured from shared memory. "
            "Do NOT guess the car or fabricate setup parameters. "
            "Do NOT recommend brake bias changes -- many cars have fixed brake bias. "
            "Limit setup advice to tyre pressures only. Focus on driving technique."
        )
    lines.append("")
    lines.append(f"NOTE: Telemetry sampled at {hz}Hz. Timing values resolve to {1/hz:.2f}s — differences below this are noise.")
    lines.append("")

    # ── Session context
    lines.append("SESSION CONTEXT:")
    lines.append(f"- Track:          {track_label}")
    if car_known:
        lines.append(f"- Car:            {car_model}")
    else:
        lines.append("- Car:            Unknown (not captured from SHM)")
    lines.append(f"- Analysis mode:  {analysis_mode}")
    lines.append(f"- Confidence:     {analysis_confidence}")
    lines.append(f"- Reference lap:  #{reference_lap_num}")
    lines.append(f"- Compare lap:    #{comparison_lap_num}")
    # Compute average track and air temps across all laps
    _all_road_temps: List[float] = []
    _all_air_temps: List[float] = []
    for lap in laps:
        for pt in lap.get("track", []):
            rt = pt.get("road_temp")
            at = pt.get("air_temp")
            if isinstance(rt, (int, float)) and rt > 0:
                _all_road_temps.append(float(rt))
            if isinstance(at, (int, float)) and at > 0:
                _all_air_temps.append(float(at))
    if _all_road_temps:
        lines.append(f"- Track temp:     {sum(_all_road_temps)/len(_all_road_temps):.0f}°C")
    if _all_air_temps:
        lines.append(f"- Air temp:       {sum(_all_air_temps)/len(_all_air_temps):.0f}°C")
    if analysis_notes:
        lines.append("")
        lines.append("ANALYSIS NOTES:")
        for note in analysis_notes:
            lines.append(f"- {note}")
    lines.append("")

    # ── Car setup parameters
    if tuning_block:
        lines.append(tuning_block)
        lines.append("")

    # ── Session overview
    lines.append("SESSION OVERVIEW:")
    lines.append(f"- Total laps analysed: {len(laps)}")
    lines.append(f"- Best lap:   #{best_lap['lap_num']}  {best_lap['lap_time_str']}")
    lines.append(f"- Worst lap:  #{worst_lap['lap_num']}  {worst_lap['lap_time_str']}")
    lines.append(f"- Delta best/worst: {time_diff:.2f}s")
    lines.append(f"- Top speed: {max(l['max_speed'] for l in laps):.1f} km/h")
    lines.append(f"- Authoritative progress coverage: {authoritative_progress_ratio:.0%}")
    lines.append(f"- Plausible physics coverage:      {plausible_frame_ratio:.0%}")

    # ── Lap time progression trend
    _lap_times = [lap["lap_time_s"] for lap in laps if lap.get("lap_time_s")]
    if len(_lap_times) >= 3:
        _trend_raw = _trend_direction(_lap_times, threshold=0.15)
        # Map _trend_direction output: FALLING lap times = improving, RISING = degrading, FLAT = consistent
        _trend_label = {"FALLING": "improving", "RISING": "degrading", "FLAT": "consistent"}.get(_trend_raw, _trend_raw.lower())
        _time_strs = " → ".join(f"{t:.1f}" if isinstance(t, (int, float)) else str(t) for t in _lap_times[:8])
        if len(_lap_times) > 8:
            _time_strs += " …"
        lines.append(f"- Lap times: {_time_strs}  (trend: {_trend_label})")

    # ── Fuel consumption summary (from telemetry)
    laps_with_fuel = [lap for lap in laps if lap.get('fuel_used') is not None]
    if laps_with_fuel:
        fuel_values = [lap['fuel_used'] for lap in laps_with_fuel]
        avg_fuel = sum(fuel_values) / len(fuel_values)
        total_fuel = sum(fuel_values)
        lines.append(f"- Fuel per lap (avg): {avg_fuel:.3f}L")
        lines.append(f"- Total fuel used: {total_fuel:.3f}L ({len(laps_with_fuel)} laps)")
        # ── Estimate laps remaining from current fuel level
        _last_lap = laps[-1]
        _last_track = _last_lap.get("track", [])
        if _last_track:
            _last_fuel = _last_track[-1].get("fuel")
            if isinstance(_last_fuel, (int, float)) and _last_fuel > 0 and avg_fuel > 0:
                _laps_remaining = int(_last_fuel / avg_fuel)
                lines.append(f"- Est. laps remaining: ~{_laps_remaining} (current fuel {_last_fuel:.1f}L)")

    lines.append("")

    # ── Outlier detection
    outliers: List[tuple] = []
    if len(laps) >= 2 and laps[0]["lap_num"] == 1:
        lap1_time = laps[0]["lap_time_s"]
        lap2_time = laps[1]["lap_time_s"]
        if lap1_time > lap2_time * 1.03:
            outliers.append((1, "First lap - likely cold tires or traffic"))

    for lap in laps:
        if lap["lap_num"] == best_lap["lap_num"]:
            continue
        delta_pct = (lap["lap_time_s"] - best_lap["lap_time_s"]) / best_lap["lap_time_s"]
        if delta_pct > 0.05:
            outliers.append((lap["lap_num"], f"{delta_pct * 100:.1f}% slower than best lap"))

    if outliers:
        lines.append("OUTLIER LAPS (may not represent true performance):")
        for lap_num, reason in outliers:
            lines.append(f"  Lap {lap_num}: {reason}")
        lines.append("  -> When analyzing, focus on the representative laps, not outliers")
        lines.append("")

    # ── Lap-by-lap summary
    lines.append("LAP-BY-LAP SUMMARY:")
    for lap in laps:
        marker = " <- BEST" if lap["lap_num"] == best_lap["lap_num"] else \
                 " <- WORST" if lap["lap_num"] == worst_lap["lap_num"] else ""
        valid_str = "" if lap.get("is_valid", True) else " [INVALID]"
        fuel_str = f"  fuel {lap['fuel_used']:.3f}L" if lap.get('fuel_used') is not None else ""
        lines.append(
            f"  Lap {lap['lap_num']}: {lap['lap_time_str']}  "
            f"max {lap['max_speed']:.1f} km/h  "
            f"avg {lap['avg_speed']:.1f} km/h{fuel_str}{valid_str}{marker}"
        )
    lines.append("")

    # ── Lap time decomposition: corner time vs straight time
    _decomp_lines: List[str] = []
    for lap in laps:
        _corner_total = 0.0
        for corner in lap.get("corners", []):
            _st = corner_segment_time(corner, hz)
            if _st is not None and _st > 0.0:
                _corner_total += _st
        _straight_total = lap["lap_time_s"] - _corner_total
        _corner_pct = (_corner_total / lap["lap_time_s"] * 100) if lap["lap_time_s"] > 0 else 0
        _straight_pct = 100 - _corner_pct
        _marker = " <- BEST" if lap["lap_num"] == best_lap["lap_num"] else ""
        _decomp_lines.append(
            f"  Lap {lap['lap_num']}: corners {_corner_total:.1f}s ({_corner_pct:.0f}%)  "
            f"straights {_straight_total:.1f}s ({_straight_pct:.0f}%){_marker}"
        )
    if _decomp_lines:
        lines.append("LAP TIME DECOMPOSITION (corner vs straight):")
        lines.extend(_decomp_lines)
        lines.append("")

    # ── Electronics / aids summary
    elec_per_lap = analyze_electronics_per_lap(laps)
    has_elec_data = any(
        e["tc_level"] is not None or e["abs_level"] is not None
        for e in elec_per_lap
    )
    if has_elec_data:
        lines.append("CAR ELECTRONICS / AIDS (start-of-lap SHM snapshot):")
        lines.append("(TC/ABS: 0=off, higher=more aggressive; EngMap=engine power mode;")
        lines.append(" DiffP=differential lock % under power; DiffC=differential lock % on coast)")
        lines.append("")

        # ── Show modifiable parameters and their limits (from first lap)
        first_elec = elec_per_lap[0] if elec_per_lap else {}
        has_limit_data = any(
            first_elec.get(f"{param}_min") is not None or first_elec.get(f"{param}_max") is not None
            for param in ["tc_level", "abs_level", "brake_bias", "engine_map", "diff_power", "diff_coast", "perf_mode"]
        )
        has_modifiable_data = any(
            first_elec.get(f"{param}_modifiable") is not None
            for param in ["tc_level", "abs_level", "brake_bias", "engine_map", "diff_power", "diff_coast",
                         "front_bump_damper", "front_rebound_damper", "rear_bump_damper", "rear_rebound_damper",
                         "pitlimiter", "perf_mode"]
        )

        if has_modifiable_data or has_limit_data:
            lines.append("ADJUSTABLE PARAMETERS (from shared memory limits/flags):")
            if has_modifiable_data:
                modifiable_params = []
                if first_elec.get("tc_level_modifiable"):
                    modifiable_params.append("TC")
                if first_elec.get("abs_level_modifiable"):
                    modifiable_params.append("ABS")
                if first_elec.get("brake_bias_modifiable"):
                    modifiable_params.append("BrakeBias")
                if first_elec.get("engine_map_modifiable"):
                    modifiable_params.append("EngMap")
                if first_elec.get("diff_power_modifiable"):
                    modifiable_params.append("DiffP")
                if first_elec.get("diff_coast_modifiable"):
                    modifiable_params.append("DiffC")
                if first_elec.get("front_bump_damper_modifiable"):
                    modifiable_params.append("FrontBump")
                if first_elec.get("front_rebound_damper_modifiable"):
                    modifiable_params.append("FrontRebound")
                if first_elec.get("rear_bump_damper_modifiable"):
                    modifiable_params.append("RearBump")
                if first_elec.get("rear_rebound_damper_modifiable"):
                    modifiable_params.append("RearRebound")
                if first_elec.get("perf_mode_modifiable"):
                    modifiable_params.append("PerfMode")
                if modifiable_params:
                    lines.append(f"  Modifiable in-session: {', '.join(modifiable_params)}")
                else:
                    lines.append("  Modifiable in-session: None (all parameters locked)")
            else:
                lines.append("  Modifiable in-session: Unknown (limits not available)")

            if has_limit_data:
                # Show limits for key parameters
                limit_lines = []
                if first_elec.get("tc_level_min") is not None and first_elec.get("tc_level_max") is not None:
                    limit_lines.append(f"TC: {first_elec['tc_level_min']}-{first_elec['tc_level_max']}")
                if first_elec.get("abs_level_min") is not None and first_elec.get("abs_level_max") is not None:
                    limit_lines.append(f"ABS: {first_elec['abs_level_min']}-{first_elec['abs_level_max']}")
                if first_elec.get("brake_bias_min") is not None and first_elec.get("brake_bias_max") is not None:
                    limit_lines.append(f"BrakeBias: {first_elec['brake_bias_min']:.2f}-{first_elec['brake_bias_max']:.2f}")
                if first_elec.get("engine_map_min") is not None and first_elec.get("engine_map_max") is not None:
                    limit_lines.append(f"EngMap: {first_elec['engine_map_min']}-{first_elec['engine_map_max']}")
                if first_elec.get("diff_power_min") is not None and first_elec.get("diff_power_max") is not None:
                    limit_lines.append(f"DiffP: {first_elec['diff_power_min']}-{first_elec['diff_power_max']}")
                if first_elec.get("diff_coast_min") is not None and first_elec.get("diff_coast_max") is not None:
                    limit_lines.append(f"DiffC: {first_elec['diff_coast_min']}-{first_elec['diff_coast_max']}")
                if first_elec.get("front_bump_damper_min") is not None and first_elec.get("front_bump_damper_max") is not None:
                    limit_lines.append(f"FrontBump: {first_elec['front_bump_damper_min']}-{first_elec['front_bump_damper_max']}")
                if first_elec.get("front_rebound_damper_min") is not None and first_elec.get("front_rebound_damper_max") is not None:
                    limit_lines.append(f"FrontRebound: {first_elec['front_rebound_damper_min']}-{first_elec['front_rebound_damper_max']}")
                if first_elec.get("rear_bump_damper_min") is not None and first_elec.get("rear_bump_damper_max") is not None:
                    limit_lines.append(f"RearBump: {first_elec['rear_bump_damper_min']}-{first_elec['rear_bump_damper_max']}")
                if first_elec.get("rear_rebound_damper_min") is not None and first_elec.get("rear_rebound_damper_max") is not None:
                    limit_lines.append(f"RearRebound: {first_elec['rear_rebound_damper_min']}-{first_elec['rear_rebound_damper_max']}")
                if limit_lines:
                    lines.append(f"  Valid ranges: {' | '.join(limit_lines)}")
            lines.append("")

        adjustments: List[str] = []
        diff_looks_invalid = False
        for e in elec_per_lap:
            tc_str = str(e["tc_level"]) if e["tc_level"] is not None else "?"
            abs_str = str(e["abs_level"]) if e["abs_level"] is not None else "?"
            map_str = str(e["engine_map"]) if e["engine_map"] is not None else "?"
            # Validate diff values: negative lock % is physically nonsensical
            dp_raw = e["diff_power"]
            dc_raw = e["diff_coast"]
            if dp_raw is not None and dp_raw < 0:
                dp_str = "N/A"
                diff_looks_invalid = True
            else:
                dp_str = str(dp_raw) if dp_raw is not None else "?"
            if dc_raw is not None and dc_raw < 0:
                dc_str = "N/A"
                diff_looks_invalid = True
            else:
                dc_str = str(dc_raw) if dc_raw is not None else "?"
            lines.append(
                f"  Lap {e['lap_num']}: TC={tc_str}  ABS={abs_str}  "
                f"EngMap={map_str}  DiffP={dp_str}  DiffC={dc_str}"
            )
            changes: List[str] = []
            if e["tc_changed"]:
                changes.append("TC")
            if e["abs_changed"]:
                changes.append("ABS")
            if e["engine_map_changed"]:
                changes.append("EngMap")
            if changes:
                adjustments.append(f"Lap {e['lap_num']}: {', '.join(changes)} adjusted mid-lap")
        if diff_looks_invalid:
            lines.append("  >> Note: DiffP/DiffC values appear uninitialized (negative). Ignore diff setup advice.")
        if adjustments:
            lines.append("")
            lines.append("  Mid-lap adjustments detected:")
            for adj in adjustments:
                lines.append(f"  -> {adj}")
        lines.append("")

    # ── Corner-by-corner analysis
    lap_corner_map: Dict[int, Dict[int, Dict]] = {
        lap["lap_num"]: {c["id"]: c for c in lap["corners"]}
        for lap in laps
    }

    lines.append("CORNER-BY-CORNER ANALYSIS:")
    lines.append("(entry/apex/exit speeds in km/h; comparison model = reference lap vs comparison lap)")
    # ── Confidence-contradiction note: when session says high but all corners are low,
    # explain to the LLM that corner-level confidence is limited by sampling density.
    _all_corner_labels = []
    for spec in ref_corners:
        cid = spec["id"]
        rc = lap_corner_map.get(reference_lap_num, {}).get(cid)
        cc = lap_corner_map.get(comparison_lap_num, {}).get(cid)
        if rc:
            _all_corner_labels.append(rc.get("confidence_label", "low"))
        if cc:
            _all_corner_labels.append(cc.get("confidence_label", "low"))
    if analysis_confidence == "high" and _all_corner_labels and all(lbl == "low" for lbl in _all_corner_labels):
        lines.append("NOTE: Session-level confidence is high (good progress/physics coverage) but")
        lines.append("      per-corner confidence is low because the track-profile windows are small.")
        lines.append("      Treat corner speed deltas as directional only, not exact comparisons.")
    lines.append("")

    # Rank corners by time lost (compare - ref segment delta) so the prompt
    # only carries full breakdowns for the biggest opportunities. SUSPECT
    # deltas (low confidence and >3s) are excluded from the ranking.
    _corner_seg_deltas: Dict[int, float] = {}
    for spec in ref_corners:
        cid = spec["id"]
        rc = lap_corner_map.get(reference_lap_num, {}).get(cid)
        cc = lap_corner_map.get(comparison_lap_num, {}).get(cid)
        if not rc or not cc:
            continue
        seg_delta = corner_segment_time(cc, hz) - corner_segment_time(rc, hz)
        is_low_conf = (
            rc.get("confidence_label") == "low" or
            cc.get("confidence_label") == "low"
        )
        if is_low_conf and abs(seg_delta) > 3.0:
            continue
        _corner_seg_deltas[cid] = seg_delta
    _TOP_CORNER_COUNT = 5
    top_corner_ids = {
        cid for cid, _ in sorted(
            _corner_seg_deltas.items(), key=lambda item: item[1], reverse=True
        )[:_TOP_CORNER_COUNT]
    }
    # Only truncate when ranking produced data and there are more corners
    # than the cutoff; otherwise emit full breakdowns for everything.
    truncate_corners = bool(top_corner_ids) and len(ref_corners) > _TOP_CORNER_COUNT
    if truncate_corners:
        lines.append(f"NOTE: Full breakdowns below cover only the {_TOP_CORNER_COUNT} corners with the")
        lines.append("      largest time loss (compare - ref). Remaining corners are summarized in one line each.")
        lines.append("")

    compact_corner_lines: List[str] = []

    for spec in ref_corners:
        cid = spec["id"]
        name = spec.get("name") or f"Corner {cid}"
        speeds = corner_speeds.get(cid, {})
        if not speeds:
            continue

        apex_vals = list(speeds.values())
        best_apex = max(apex_vals)
        worst_apex = min(apex_vals)
        variation = best_apex - worst_apex

        corners_for_lap = []
        for lap in laps:
            corner = lap_corner_map[lap["lap_num"]].get(cid)
            if corner:
                corners_for_lap.append((lap["lap_num"], corner))

        if not corners_for_lap:
            continue

        reference_corner = lap_corner_map.get(reference_lap_num, {}).get(cid)
        comparison_corner = lap_corner_map.get(comparison_lap_num, {}).get(cid)
        if not reference_corner or not comparison_corner:
            continue

        if truncate_corners and cid not in top_corner_ids:
            _seg = (
                corner_segment_time(comparison_corner, hz) -
                corner_segment_time(reference_corner, hz)
            )
            _apex_d = comparison_corner["apex_speed"] - reference_corner["apex_speed"]
            compact_corner_lines.append(
                f"  C{cid} {name}: apex {reference_corner['apex_speed']:.1f} vs "
                f"{comparison_corner['apex_speed']:.1f} (D {_apex_d:+.1f} km/h), "
                f"seg D {_seg:+.2f}s"
            )
            continue

        best_apex_lap_num, _ = max(corners_for_lap, key=lambda item: item[1]["apex_speed"])
        worst_apex_lap_num, _ = min(corners_for_lap, key=lambda item: item[1]["apex_speed"])

        lines.append(f"--- {name} (Corner {cid}) ---")
        lines.append(f"  Apex speed range: {variation:.1f} km/h (spread: {variation:.1f} km/h)  {variation_label(variation)}")
        if variation > 5.0:
            lines.append(f"  >> INCONSISTENT APEX SPEED: {variation:.1f} km/h spread across laps")

        lap_speed_strs = [f"Lap {ln}: {spd:.1f}" for ln, spd in sorted(speeds.items())]
        lines.append(f"  Apex speeds:  {',  '.join(lap_speed_strs)}")
        lines.append(f"  Highest apex: {best_apex:.1f} km/h (Lap {best_apex_lap_num})")
        lines.append(f"  Lowest apex:  {worst_apex:.1f} km/h (Lap {worst_apex_lap_num})")

        entry_delta = comparison_corner["entry_speed"] - reference_corner["entry_speed"]
        apex_delta = comparison_corner["apex_speed"] - reference_corner["apex_speed"]
        exit_delta = comparison_corner["exit_speed"] - reference_corner["exit_speed"]

        lines.append(
            f"  Reference lap: Lap {reference_lap_num}  "
            f"{corner_segment_time(reference_corner, hz):.2f}s"
        )
        lines.append(
            f"  Compare lap:   Lap {comparison_lap_num}  "
            f"{corner_segment_time(comparison_corner, hz):.2f}s"
        )
        lines.append(
            f"  Entry  -- Lap {reference_lap_num}: {reference_corner['entry_speed']:.1f}  |  "
            f"Lap {comparison_lap_num}: {comparison_corner['entry_speed']:.1f}  |  "
            f"D {entry_delta:+.1f} km/h"
        )
        lines.append(
            f"  Apex   -- Lap {reference_lap_num}: {reference_corner['apex_speed']:.1f}  |  "
            f"Lap {comparison_lap_num}: {comparison_corner['apex_speed']:.1f}  |  "
            f"D {apex_delta:+.1f} km/h"
        )
        lines.append(
            f"  Exit   -- Lap {reference_lap_num}: {reference_corner['exit_speed']:.1f}  |  "
            f"Lap {comparison_lap_num}: {comparison_corner['exit_speed']:.1f}  |  "
            f"D {exit_delta:+.1f} km/h"
        )

        seg_delta = (
            corner_segment_time(comparison_corner, hz) -
            corner_segment_time(reference_corner, hz)
        )
        is_low_conf = (
            reference_corner.get("confidence_label") == "low" or
            comparison_corner.get("confidence_label") == "low"
        )
        if is_low_conf and abs(seg_delta) > 3.0:
            lines.append(f"  Segment delta (compare - ref): {seg_delta:+.2f}s  >> SUSPECT")
            lines.append("    (LOW-confidence corner with >3.0s delta — do not treat as actionable time loss)")
        else:
            lines.append(f"  Segment delta (compare - ref): {seg_delta:+.2f}s")
        lines.append(
            f"  Confidence: ref={reference_corner.get('confidence_label', 'low')}  "
            f"compare={comparison_corner.get('confidence_label', 'low')}"
        )

        issue = classify_corner_issue(entry_delta, apex_delta, exit_delta)
        lines.append(f"  Likely issue: {issue}")

        # Car state at entry/apex/exit for fastest vs slowest lap
        lines.append("  Car state (Entry | Apex | Exit):")

        fastest_entry = reference_corner.get("entry_state")
        fastest_apex_st = reference_corner.get("apex_state")
        fastest_exit = reference_corner.get("exit_state")
        if fastest_entry and fastest_apex_st and fastest_exit:
            lines.append(
                f"    Lap {reference_lap_num} (reference): "
                f"{format_car_state(fastest_entry)} | "
                f"{format_car_state(fastest_apex_st)} | "
                f"{format_car_state(fastest_exit)}"
            )
            lines.append(
                f"    Balance hint @apex (Lap {reference_lap_num}): "
                f"{balance_hint(fastest_apex_st)}"
            )

        slowest_entry = comparison_corner.get("entry_state")
        slowest_apex_st = comparison_corner.get("apex_state")
        slowest_exit = comparison_corner.get("exit_state")
        if slowest_entry and slowest_apex_st and slowest_exit:
            lines.append(
                f"    Lap {comparison_lap_num} (compare): "
                f"{format_car_state(slowest_entry)} | "
                f"{format_car_state(slowest_apex_st)} | "
                f"{format_car_state(slowest_exit)}"
            )
            lines.append(
                f"    Balance hint @apex (Lap {comparison_lap_num}): "
                f"{balance_hint(slowest_apex_st)}"
            )

        # ── Steering smoothness per corner (1.4)
        _steer_data: Dict[int, List[Dict[str, Any]]] = {}
        for lap in laps:
            corner = lap_corner_map[lap["lap_num"]].get(cid)
            if corner:
                _ss = analyze_steering_smoothness(lap["track"], corner, hz)
                if _ss:
                    _steer_data.setdefault(lap["lap_num"], []).append(_ss)
        if _steer_data:
            lines.append("  Steering smoothness:")
            for ln, ss_list in sorted(_steer_data.items()):
                for _ss in ss_list:
                    _jerk_flag = "  >> JERKY STEERING" if _ss["reversals"] > 3 else ""
                    lines.append(
                        f"    Lap {ln}: reversals={_ss['reversals']}  "
                        f"peak_rate={_ss['peak_steer_rate']:.2f} rad/s  "
                        f"avg_rate={_ss['avg_steer_rate']:.2f} rad/s  "
                        f"smoothness={_ss['smoothness_score']:.2f}{_jerk_flag}"
                    )

        # ── Throttle exit profile per corner (1.5)
        _throttle_data: Dict[int, List[Dict[str, Any]]] = {}
        for lap in laps:
            corner = lap_corner_map[lap["lap_num"]].get(cid)
            if corner:
                _te = analyze_throttle_exit(lap["track"], corner, hz)
                if _te:
                    _throttle_data.setdefault(lap["lap_num"], []).append(_te)
        if _throttle_data:
            lines.append("  Throttle exit profile:")
            for ln, te_list in sorted(_throttle_data.items()):
                for _te in te_list:
                    _tfull = f"{_te['time_to_full_throttle']:.2f}s" if _te["time_to_full_throttle"] is not None else "never"
                    _mod_flag = ""
                    if _te["modulation_count"] > 3:
                        _mod_flag = "  >> MODULATED THROTTLE"
                    if _te["time_to_full_throttle"] is not None and _te["time_to_full_throttle"] > 1.5:
                        _mod_flag = "  >> SLOW TO FULL THROTTLE"
                    lines.append(
                        f"    Lap {ln}: full_throttle={_tfull}  "
                        f"variance={_te['throttle_variance']:.4f}  "
                        f"modulation={_te['modulation_count']}  "
                        f"profile={_te['exit_profile']}{_mod_flag}"
                    )

        lines.append("")

    if compact_corner_lines:
        lines.append("OTHER CORNERS (compact, ref vs compare):")
        lines.extend(compact_corner_lines)
        lines.append("")

    # ── Straight/sector analysis: time between consecutive corners
    _straight_lines: List[str] = []
    for i in range(len(ref_corners) - 1):
        spec_a = ref_corners[i]
        spec_b = ref_corners[i + 1]
        cid_a = spec_a["id"]
        cid_b = spec_b["id"]
        name_a = spec_a.get("name") or f"Corner {cid_a}"
        name_b = spec_b.get("name") or f"Corner {cid_b}"
        _straight_times: Dict[int, float] = {}
        for lap in laps:
            corner_a = lap_corner_map.get(lap["lap_num"], {}).get(cid_a)
            corner_b = lap_corner_map.get(lap["lap_num"], {}).get(cid_b)
            if corner_a and corner_b:
                _t_a = corner_segment_time(corner_a, hz)
                _t_b = corner_segment_time(corner_b, hz)
                if _t_a is not None and _t_b is not None and _t_a > 0 and _t_b > 0:
                    _straight_time = _t_b - _t_a
                    if _straight_time > 0:
                        _straight_times[lap["lap_num"]] = _straight_time
        if len(_straight_times) >= 2:
            _best_straight = min(_straight_times.values())
            _worst_straight = max(_straight_times.values())
            _best_lap_num = min(_straight_times, key=lambda k: _straight_times[k])
            _straight_lines.append(
                f"  {name_a} → {name_b}: "
                f"best {_best_straight:.2f}s (Lap {_best_lap_num})  "
                f"worst {_worst_straight:.2f}s  "
                f"spread {_worst_straight - _best_straight:.2f}s"
            )
    if _straight_lines:
        lines.append("STRAIGHT/SECTOR ANALYSIS (time between consecutive corner segments):")
        lines.extend(_straight_lines)
        lines.append("")

    # ── Exit-to-entry correlation: link corner exit speed to next corner entry speed
    _correlation_lines: List[str] = []
    for i in range(len(ref_corners) - 1):
        spec_a = ref_corners[i]
        spec_b = ref_corners[i + 1]
        cid_a = spec_a["id"]
        cid_b = spec_b["id"]
        name_a = spec_a.get("name") or f"Corner {cid_a}"
        name_b = spec_b.get("name") or f"Corner {cid_b}"
        _ref_exit = None
        _ref_entry_next = None
        _cmp_exit = None
        _cmp_entry_next = None
        ref_corner_a = lap_corner_map.get(reference_lap_num, {}).get(cid_a)
        ref_corner_b = lap_corner_map.get(reference_lap_num, {}).get(cid_b)
        cmp_corner_a = lap_corner_map.get(comparison_lap_num, {}).get(cid_a)
        cmp_corner_b = lap_corner_map.get(comparison_lap_num, {}).get(cid_b)
        if ref_corner_a and ref_corner_b:
            _ref_exit = ref_corner_a.get("exit_speed")
            _ref_entry_next = ref_corner_b.get("entry_speed")
        if cmp_corner_a and cmp_corner_b:
            _cmp_exit = cmp_corner_a.get("exit_speed")
            _cmp_entry_next = cmp_corner_b.get("entry_speed")
        if _ref_exit is not None and _ref_entry_next is not None:
            _exit_d = (_cmp_exit - _ref_exit) if _cmp_exit is not None else 0.0
            _entry_d = (_cmp_entry_next - _ref_entry_next) if _cmp_entry_next is not None else 0.0
            if abs(_exit_d) > 2.0 or abs(_entry_d) > 2.0:
                _correlation_lines.append(
                    f"  {name_a} → {name_b}: "
                    f"exit D {_exit_d:+.1f} km/h → entry D {_entry_d:+.1f} km/h"
                )
    if _correlation_lines:
        lines.append("EXIT-TO-ENTRY CORRELATION (corner exit impact on next corner):")
        lines.append("(D = compare lap minus reference lap; large exit delta cascades to next entry)")
        lines.extend(_correlation_lines)
        lines.append("")

    # ── Coast time aggregation: total coasting per lap
    _coast_lines: List[str] = []
    for lap in laps:
        _total_coast_frames = 0
        for corner in lap.get("corners", []):
            _phases = analyze_corner_phases(
                lap["track"], corner, lap["start_frame"], hz
            )
            if _phases:
                _total_coast_frames += _phases["coast_frames"]
        _total_coast_s = _total_coast_frames / hz if hz > 0 else 0.0
        if _total_coast_s > 0.1:
            _marker = " <- BEST" if lap["lap_num"] == best_lap["lap_num"] else ""
            _coast_lines.append(
                f"  Lap {lap['lap_num']}: {_total_coast_s:.1f}s coasting "
                f"({_total_coast_frames} frames across all corners){_marker}"
            )
    if _coast_lines:
        lines.append("COAST TIME AGGREGATION (total time coasting per lap, all corners):")
        lines.append("(coasting = neither brake nor gas near apex; pure time loss)")
        lines.extend(_coast_lines)
        _best_coast = min(
            (float(l.split(":")[1].split("s")[0].strip()) for l in _coast_lines),
            default=0.0,
        )
        _worst_coast = max(
            (float(l.split(":")[1].split("s")[0].strip()) for l in _coast_lines),
            default=0.0,
        )
        if _worst_coast - _best_coast > 0.3:
            lines.append(
                f"  >> COASTING SPREAD: {_worst_coast - _best_coast:.1f}s "
                f"between best and worst lap — reducing coast time is free lap time."
            )
        lines.append("")

    # ── Braking, turn-in, and throttle timing analysis
    lines.append("BRAKING & TIMING ANALYSIS:")
    lines.append("(brake_onset = seconds before corner entry; turn_in = seconds before entry;")
    lines.append(" gas_on = seconds after apex; trail_brake% = % of entry-to-apex with brake applied;")
    lines.append(" coast = frames near apex with neither gas nor brake; peak_brake_g = peak decel G)")
    lines.append("")
    # Compute coverage stats for brake_onset and gas_on so we can warn the LLM
    _total_phase_rows = 0
    _brake_onset_na_count = 0
    _gas_on_zero_count = 0
    for spec in ref_corners:
        cid = spec["id"]
        for lap in laps:
            corner = lap_corner_map[lap["lap_num"]].get(cid)
            if not corner:
                continue
            phases = analyze_corner_phases(
                lap["track"], corner, lap["start_frame"], hz
            )
            if phases:
                _total_phase_rows += 1
                if phases["brake_onset_dt"] is None:
                    _brake_onset_na_count += 1
                if phases["gas_on_dt"] == 0.0:
                    _gas_on_zero_count += 1
    if _total_phase_rows > 0:
        _brake_na_pct = (_brake_onset_na_count / _total_phase_rows) * 100
        _gas_zero_pct = (_gas_on_zero_count / _total_phase_rows) * 100
        if _brake_na_pct > 50 or _gas_zero_pct > 50:
            lines.append("DATA QUALITY NOTE:")
            if _brake_na_pct > 50:
                lines.append(f"  brake_onset is N/A for {_brake_na_pct:.0f}% of corners — the approach zone may")
                lines.append("  not capture enough frames before entry, or braking threshold was not crossed.")
            if _gas_zero_pct > 50:
                lines.append(f"  gas_on reports 0.00s for {_gas_zero_pct:.0f}% of corners — this usually means")
                lines.append("  the driver was already on throttle at the apex frame, not that pickup is instant.")
            lines.append("  Treat these metrics as directional only, not absolute timing.")
            lines.append("")

    for spec in ref_corners:
        cid = spec["id"]
        name = spec.get("name") or f"Corner {cid}"

        phase_data_per_lap = []
        for lap in laps:
            corner = lap_corner_map[lap["lap_num"]].get(cid)
            if not corner:
                continue
            phases = analyze_corner_phases(
                lap["track"], corner, lap["start_frame"], hz
            )
            if phases:
                phase_data_per_lap.append((lap["lap_num"], phases))

        if not phase_data_per_lap:
            continue

        lines.append(f"  {name}:")
        for ln, ph in phase_data_per_lap:
            brake_str = f"{ph['brake_onset_dt']:.2f}s" if ph["brake_onset_dt"] is not None else "N/A"
            turnin_str = f"{ph['turn_in_dt']:.2f}s" if ph["turn_in_dt"] is not None else "N/A"
            gas_str = f"{ph['gas_on_dt']:.2f}s" if ph["gas_on_dt"] is not None else "N/A"
            lines.append(
                f"    Lap {ln}: brake_onset={brake_str}  turn_in={turnin_str}  "
                f"gas_on={gas_str}  trail_brake={ph['trail_brake_pct']:.0%}  "
                f"coast={ph['coast_frames']}fr  peak_brake_g={ph['peak_brake_g']:.2f}"
            )

        # Compute deltas between fastest and slowest segment laps
        if len(phase_data_per_lap) >= 2:
            phase_map = dict(phase_data_per_lap)
            fast_ph = phase_map.get(reference_lap_num)
            slow_ph = phase_map.get(comparison_lap_num)

            if fast_ph and slow_ph:
                hints = []
                # Braking timing comparison
                if fast_ph["brake_onset_dt"] is not None and slow_ph["brake_onset_dt"] is not None:
                    diff = slow_ph["brake_onset_dt"] - fast_ph["brake_onset_dt"]
                    if abs(diff) > 0.05:
                        if diff > 0:
                            hints.append(f"compare lap brakes {diff:.2f}s EARLIER")
                        else:
                            hints.append(f"compare lap brakes {abs(diff):.2f}s LATER")
                # Turn-in comparison
                if fast_ph["turn_in_dt"] is not None and slow_ph["turn_in_dt"] is not None:
                    diff = slow_ph["turn_in_dt"] - fast_ph["turn_in_dt"]
                    if abs(diff) > 0.05:
                        if diff > 0:
                            hints.append(f"compare lap turns in {diff:.2f}s EARLIER")
                        else:
                            hints.append(f"compare lap turns in {abs(diff):.2f}s LATER")
                # Gas-on comparison
                if fast_ph["gas_on_dt"] is not None and slow_ph["gas_on_dt"] is not None:
                    diff = slow_ph["gas_on_dt"] - fast_ph["gas_on_dt"]
                    if abs(diff) > 0.05:
                        hints.append(f"compare lap gets on gas {abs(diff):.2f}s {'LATER' if diff > 0 else 'EARLIER'}")
                # Trail braking comparison
                tb_diff = fast_ph["trail_brake_pct"] - slow_ph["trail_brake_pct"]
                if abs(tb_diff) > 0.10:
                    if tb_diff > 0:
                        hints.append(f"reference lap trail brakes {tb_diff:.0%} MORE into corner")
                    else:
                        hints.append(f"compare lap trail brakes {abs(tb_diff):.0%} MORE into corner")
                # Coasting comparison
                if slow_ph["coast_frames"] > fast_ph["coast_frames"] + 2:
                    hints.append(f"compare lap coasts {slow_ph['coast_frames'] - fast_ph['coast_frames']} more frames near apex")
                # Peak brake G comparison
                g_diff = fast_ph["peak_brake_g"] - slow_ph["peak_brake_g"]
                if abs(g_diff) > 0.15:
                    if g_diff > 0:
                        hints.append(f"reference lap brakes {g_diff:.2f}G harder")
                    else:
                        hints.append(f"compare lap brakes {abs(g_diff):.2f}G harder")

                if hints:
                    lines.append(f"    >> Lap {reference_lap_num} vs Lap {comparison_lap_num}: {'; '.join(hints)}")

        lines.append("")

    # ── Tyre grip-degradation across the stint
    tyre_deg = analyze_tyre_grip_degradation(laps)
    deg_per_lap = tyre_deg.get("per_lap") or []
    if deg_per_lap:
        lines.append("TYRE GRIP DEGRADATION OVER STINT:")
        lines.append("(per-lap tyre summary — watch for monotonic trends as the stint progresses;")
        lines.append(" avg_temp = avg core temp across all 4 corners; peak_lat_g & peak_slip are")
        lines.append(" computed only on cornering frames so they reflect grip-limited driving;")
        lines.append(" wear_delta = % wear consumed on this lap; end_dirty = dirt pickup at lap end)")
        lines.append("")
        for lap_num, s in deg_per_lap:
            lines.append(
                f"  Lap {lap_num}: avg_temp={s['avg_core_temp_c']:.1f}C  "
                f"peak_temp={s['peak_core_temp_c']:.1f}C  "
                f"peak_lat_g={s['peak_lat_g']:.2f}  "
                f"peak_slip={s['peak_slip_angle_deg']:.1f}deg  "
                f"wear_delta={s['wear_delta_pct']:.2f}%  "
                f"end_wear={s['end_wear_pct']:.2f}%  "
                f"end_dirty={s['end_dirty_pct']:.1f}%"
            )

        trends = tyre_deg.get("trends") or {}
        if trends:
            lines.append("")
            lines.append(
                f"  Trends across stint: core_temp={trends.get('core_temp', 'FLAT')}  "
                f"peak_lat_g={trends.get('peak_lat_g', 'FLAT')}  "
                f"peak_slip_angle={trends.get('peak_slip_angle', 'FLAT')}  "
                f"wear={trends.get('wear', 'FLAT')}"
            )

        for flag in tyre_deg.get("flags") or []:
            lines.append(f"  >> {flag}")

        if len(deg_per_lap) < 3:
            lines.append(
                "  (Need at least 3 laps to detect a stint trend; current sample is short.)"
            )

        lines.append("")

    # ── Grip utilization analysis
    lines.append("GRIP UTILIZATION ANALYSIS (friction circle):")
    lines.append("(peak_total_g = grip envelope; grip_fill% = avg/peak = how much grip is used on average;")
    lines.append(" combined_brake% = % of braking frames with lat_g > 0.3 = trail braking effectiveness)")
    lines.append("")

    # Compute session-wide peak G as the reference grip envelope
    session_peak_g = 0.0
    all_grip_data: Dict[int, List[tuple]] = {}  # cid -> [(lap_num, grip_dict)]

    for spec in ref_corners:
        cid = spec["id"]
        name = spec.get("name") or f"Corner {cid}"
        grip_per_lap = []

        for lap in laps:
            corner = lap_corner_map[lap["lap_num"]].get(cid)
            if not corner:
                continue
            grip = analyze_grip_utilization(lap["track"], corner, hz)
            if grip:
                grip_per_lap.append((lap["lap_num"], grip))
                if grip["peak_total_g"] > session_peak_g:
                    session_peak_g = grip["peak_total_g"]

        all_grip_data[cid] = grip_per_lap

    lines.append(f"  Session peak combined G: {session_peak_g:.2f}G")
    lines.append("")

    for spec in ref_corners:
        cid = spec["id"]
        name = spec.get("name") or f"Corner {cid}"
        grip_per_lap = all_grip_data.get(cid, [])
        if not grip_per_lap:
            continue

        lines.append(f"  {name}:")
        for ln, g in grip_per_lap:
            # Compare to session peak to flag underutilized grip
            headroom = session_peak_g - g["peak_total_g"] if session_peak_g > 0.1 else 0
            headroom_str = f"  headroom={headroom:.2f}G" if headroom > 0.15 else ""
            lines.append(
                f"    Lap {ln}: peak_g={g['peak_total_g']:.2f}  "
                f"avg_g={g['avg_total_g']:.2f}  "
                f"grip_fill={g['grip_fill_pct']:.0f}%  "
                f"lat={g['peak_lat_g']:.2f}  long={g['peak_long_g']:.2f}  "
                f"combined_brake={g['combined_braking_pct']:.0f}%{headroom_str}"
            )

        # Flag corners where grip is consistently low vs session peak
        avg_peak = sum(g["peak_total_g"] for _, g in grip_per_lap) / len(grip_per_lap)
        avg_fill = sum(g["grip_fill_pct"] for _, g in grip_per_lap) / len(grip_per_lap)
        avg_combined = sum(g["combined_braking_pct"] for _, g in grip_per_lap) / len(grip_per_lap)

        flags = []
        if session_peak_g > 0.5 and avg_peak < session_peak_g * 0.75:
            flags.append(f"UNDERUTILIZED: peak G only {avg_peak:.2f} vs session max {session_peak_g:.2f} -- driver has more grip available")
        if avg_fill < 55:
            flags.append(f"LOW GRIP FILL ({avg_fill:.0f}%) -- coasting or not loading tires through the corner")
        if avg_combined < 30 and any(g["peak_long_g"] > 0.5 for _, g in grip_per_lap):
            flags.append(f"LOW COMBINED BRAKING ({avg_combined:.0f}%) -- not trail braking effectively into this corner")

        for flag in flags:
            lines.append(f"    >> {flag}")
        lines.append("")

    # ── Theoretical best lap — assemble best segment time per corner across all laps
    # Compute per-corner gap (best_lap_segment - best_segment_across_laps), then
    # theoretical_best = actual_best_lap_time - sum(gaps).  This avoids the
    # nonsensical "sum of corner segments vs full lap" comparison that ignored
    # straights and produced inflated potential-gain figures.
    _best_segments: Dict[int, float] = {}
    _best_lap_segments: Dict[int, float] = {}
    _corner_gaps: Dict[int, float] = {}
    for spec in ref_corners:
        cid = spec["id"]
        _best_seg = None
        for lap in laps:
            corner = lap_corner_map.get(lap["lap_num"], {}).get(cid)
            if corner:
                seg = corner_segment_time(corner, hz)
                if seg is not None and seg > 0.0 and (_best_seg is None or seg < _best_seg):
                    _best_seg = seg
        if _best_seg is not None:
            _bl_corner = lap_corner_map.get(best_lap["lap_num"], {}).get(cid)
            if _bl_corner:
                _bl_seg = corner_segment_time(_bl_corner, hz)
                if _bl_seg is not None and _bl_seg > 0.0:
                    if _best_seg < _bl_seg * 0.5:
                        continue
                    _best_segments[cid] = _best_seg
                    _best_lap_segments[cid] = _bl_seg
                    _corner_gaps[cid] = _bl_seg - _best_seg
    if _corner_gaps:
        _actual_best_time = best_lap["lap_time_s"]
        _total_gap = sum(_corner_gaps.values())
        _theoretical_best = _actual_best_time - _total_gap
        lines.append("THEORETICAL BEST LAP:")
        lines.append(f"  Theoretical best: {_theoretical_best:.2f}s (actual best: {_actual_best_time:.2f}s)")
        lines.append(f"  Potential gain: {_total_gap:.2f}s across {len(_corner_gaps)} corners")
        lines.append("  Per-corner best segments vs best lap segments:")
        for spec in ref_corners:
            cid = spec["id"]
            name = spec.get("name") or f"Corner {cid}"
            _best_seg = _best_segments.get(cid)
            _bl_seg = _best_lap_segments.get(cid)
            if _best_seg is None or _bl_seg is None:
                continue
            _gap = _corner_gaps.get(cid, 0.0)
            lines.append(f"    C{cid} {name}: best segment {_best_seg:.2f}s  |  best lap segment {_bl_seg:.2f}s  |  gap {_gap:+.2f}s")
        lines.append("")

    # ── Time-loss ranking (cap implausible deltas that are capture artifacts)
    _MAX_PLAUSIBLE_SEGMENT_DELTA = 10.0  # seconds; anything larger is almost certainly corrupt
    _SUSPECT_SEGMENT_DELTA = 3.0  # seconds; LOW-confidence corners above this are suspect
    lines.append("TIME LOSS RANKING (worst -> best, by segment time delta):")
    lines.append(
        f"  (Deltas capped at ±{_MAX_PLAUSIBLE_SEGMENT_DELTA:.0f}s; "
        f"LOW-confidence deltas > ±{_SUSPECT_SEGMENT_DELTA:.0f}s flagged as suspect.)"
    )
    ranked = []
    corrupt_corners: List[str] = []
    suspect_corners: List[str] = []
    for spec in ref_corners:
        cid = spec["id"]
        ref_corner = lap_corner_map.get(reference_lap_num, {}).get(cid)
        cmp_corner = lap_corner_map.get(comparison_lap_num, {}).get(cid)
        if ref_corner and cmp_corner:
            delta = corner_segment_time(cmp_corner, hz) - corner_segment_time(ref_corner, hz)
            is_low_conf = (
                ref_corner.get("confidence_label") == "low" or
                cmp_corner.get("confidence_label") == "low"
            )
            if is_low_conf and abs(delta) > _SUSPECT_SEGMENT_DELTA:
                suspect_corners.append(spec.get("name") or f"Corner {cid}")
                delta = max(-_SUSPECT_SEGMENT_DELTA, min(_SUSPECT_SEGMENT_DELTA, delta))
            elif abs(delta) > _MAX_PLAUSIBLE_SEGMENT_DELTA:
                corrupt_corners.append(spec.get("name") or f"Corner {cid}")
                delta = max(-_MAX_PLAUSIBLE_SEGMENT_DELTA, min(_MAX_PLAUSIBLE_SEGMENT_DELTA, delta))
            ranked.append((delta, spec.get("name") or f"Corner {cid}", cid))
    ranked.sort(reverse=True)
    for delta, name, cid in ranked:
        lines.append(f"  {name:<30} {delta:+.2f}s")
    if suspect_corners:
        lines.append(
            f"  >> NOTE: {', '.join(suspect_corners)} had suspect deltas "
            f"(>±{_SUSPECT_SEGMENT_DELTA:.0f}s with LOW confidence)."
        )
        lines.append("     Do not recommend specific time gains for these corners — the data is unreliable.")
    if corrupt_corners:
        lines.append(f"  >> NOTE: {', '.join(corrupt_corners)} had implausible deltas and were capped.")
        lines.append("     Do not recommend specific time gains for these corners — the data is unreliable.")
    lines.append("")

    # ── DRS/Aerodynamics analysis — gate on data presence
    _any_drs_available = any(
        any(pt.get("drs_available", False) for pt in lap.get("track", []))
        for lap in laps
    )
    if _any_drs_available:
        lines.append("AERODYNAMICS & DRS ANALYSIS:")
        lines.append("(drs_state = DRS flap position; drs_available = activation permitted; drs_enabled = currently active)")
        lines.append("")

        # Analyze DRS usage patterns
        drs_usage_per_lap = {}
        for lap in laps:
            lap_num = lap["lap_num"]
            lap_track = lap.get("track", [])
            drs_active_frames = 0
            drs_available_frames = 0

            for pt in lap_track:
                if pt.get("drs_enabled", False):
                    drs_active_frames += 1
                if pt.get("drs_available", False):
                    drs_available_frames += 1

            if drs_available_frames > 0:
                drs_usage_pct = (drs_active_frames / drs_available_frames) * 100
                drs_usage_per_lap[lap_num] = {
                    "active_frames": drs_active_frames,
                    "available_frames": drs_available_frames,
                    "usage_pct": drs_usage_pct
                }

                lines.append(f"  Lap {lap_num}: DRS used {drs_usage_pct:.1f}% of available time "
                           f"({drs_active_frames}/{drs_available_frames} frames)")

        if not drs_usage_per_lap:
            lines.append("  No DRS usage detected or DRS not available in this session")
        else:
            # Check for consistent DRS usage
            usage_values = [data["usage_pct"] for data in drs_usage_per_lap.values()]
            if len(usage_values) > 1:
                avg_usage = sum(usage_values) / len(usage_values)
                usage_variance = max(usage_values) - min(usage_values)
                if usage_variance > 20:  # Significant variation
                    lines.append(f"  >> INCONSISTENT DRS USAGE: varies by {usage_variance:.1f}% between laps")
                elif avg_usage < 50:
                    lines.append(f"  >> LOW DRS USAGE: only {avg_usage:.1f}% of available DRS zones utilized")

        lines.append("")

    # ── Aerodynamics setup analysis — gate on data presence
    _any_aero_data = any(
        (pt.get("ride_height_front", 0) or 0) > 1.0 or (pt.get("pitch", 0) or 0) != 0
        for lap in laps for pt in lap.get("track", [])
    )
    if _any_aero_data:
        lines.append("AERODYNAMICS SETUP ANALYSIS:")
        lines.append("(pitch = chassis angle; ride_height = ground clearance; air_density affects downforce)")
        lines.append("")

        # Analyze ride height and pitch dynamics
        ride_height_data = []
        pitch_data = []
        air_density_data = []

        for lap in laps:
            lap_num = lap["lap_num"]
            lap_track = lap.get("track", [])

            # Collect ride height and pitch data (filter <1mm — likely uninitialized SHM in AC Evo EA)
            front_heights = [pt.get("ride_height_front", 0) for pt in lap_track if (pt.get("ride_height_front", 0) or 0) > 1.0]
            rear_heights = [pt.get("ride_height_rear", 0) for pt in lap_track if (pt.get("ride_height_rear", 0) or 0) > 1.0]
            pitch_values = [pt.get("pitch", 0) for pt in lap_track if (pt.get("pitch", 0) or 0) != 0]
            air_densities = [pt.get("air_density", 0) for pt in lap_track if (pt.get("air_density", 0) or 0) > 0]

            if front_heights and rear_heights:
                avg_front = sum(front_heights) / len(front_heights)
                avg_rear = sum(rear_heights) / len(rear_heights)
                ride_height_data.append((lap_num, avg_front, avg_rear, avg_rear - avg_front))

                lines.append(f"  Lap {lap_num}: Ride Height F={avg_front:.1f}mm R={avg_rear:.1f}mm "
                           f"Rake={(avg_rear - avg_front):.1f}mm")

            if pitch_values:
                avg_pitch = sum(pitch_values) / len(pitch_values)
                pitch_deg = avg_pitch * (180.0 / math.pi)
                # Store pitch range along with avg for later sensitivity check
                min_pitch = min(pitch_values)
                max_pitch = max(pitch_values)
                pitch_range = max_pitch - min_pitch
                pitch_data.append((lap_num, avg_pitch, pitch_deg, pitch_range))

                # Show pitch range (important for aero balance)
                min_pitch_deg = min_pitch * (180.0 / math.pi)
                max_pitch_deg = max_pitch * (180.0 / math.pi)
                lines.append(f"    Pitch: avg={pitch_deg:+.2f}° range={min_pitch_deg:+.2f}° to {max_pitch_deg:+.2f}°")

            if air_densities:
                avg_density = sum(air_densities) / len(air_densities)
                air_density_data.append((lap_num, avg_density))
                lines.append(f"    Air Density: {avg_density:.3f} kg/m³")

        # Setup recommendations based on aero data
        if ride_height_data:
            lines.append("")
            lines.append("  AERO SETUP INSIGHTS:")

            # Analyze rake angle
            rakes = [data[3] for data in ride_height_data]
            avg_rake = sum(rakes) / len(rakes)
            rake_variance = max(rakes) - min(rakes)

            if avg_rake < 10.0:  # Less than 10mm rake
                lines.append(f"    >> LOW RAKE: {avg_rake:.1f}mm average - consider increasing rear ride height "
                           f"or lowering front for more rear downforce")
            elif avg_rake > 50.0:  # More than 50mm rake
                lines.append(f"    >> HIGH RAKE: {avg_rake:.1f}mm average - may be excessive drag, "
                           f"consider reducing rake for better top speed")

            if rake_variance > 15.0:  # More than 15mm variation
                lines.append(f"    >> INCONSISTENT RAKE: varies by {rake_variance:.1f}mm - "
                           f"suspension compliance issue or inconsistent ride heights")

            # Check for pitch sensitivity
            if pitch_data:
                # pitch_data now stores (lap_num, avg_pitch, pitch_deg, pitch_range)
                pitch_ranges = [p[3] for p in pitch_data]  # Use stored pitch_range
                avg_pitch_range = sum(pitch_ranges) / len(pitch_ranges) if pitch_ranges else 0
                avg_pitch_range_deg = avg_pitch_range * (180.0 / math.pi)

                if avg_pitch_range_deg > 2.0:  # More than 2 degrees pitch variation
                    lines.append(f"    >> HIGH PITCH SENSITIVITY: {avg_pitch_range_deg:.1f}° variation - "
                           f"consider stiffer springs or more aero balance")

        lines.append("")
        lines.append("")

    # ── Gear optimization analysis (if data available)
    gear_rpm_available = any(
        lap.get("track", [{}])[0].get("gear_rpm_window") is not None
        for lap in laps if lap.get("track")
    )

    if gear_rpm_available:
        lines.append("GEAR OPTIMIZATION ANALYSIS:")
        lines.append("(gear_rpm_window: 1.0 = perfect gear, <0.8 = too high gear, >1.0 = too low gear)")
        lines.append("")

        for spec in ref_corners:
            cid = spec["id"]
            name = spec.get("name") or f"Corner {cid}"

            gear_data = []
            for lap in laps:
                corner = lap_corner_map[lap["lap_num"]].get(cid)
                if not corner:
                    continue

                corner_track = [
                    pt for pt in lap["track"]
                    if corner["start_frame"] <= pt["frame"] <= corner["end_frame"]
                ]

                if corner_track:
                    # Sample gear/RPM at entry (25%), apex (50%), and exit (75%)
                    _n = len(corner_track)
                    _idx25 = max(0, _n // 4)
                    _idx50 = _n // 2
                    _idx75 = min(_n - 1, (3 * _n) // 4)
                    for _label, _idx in [("entry", _idx25), ("apex", _idx50), ("exit", _idx75)]:
                        _pt = corner_track[_idx]
                        gear_window = _pt.get("gear_rpm_window")
                        gear = _pt.get("gear", 0)
                        rpm_pct = _pt.get("rpm_percent")
                        if gear_window is not None:
                            gear_data.append((lap["lap_num"], gear, gear_window, rpm_pct, _label))

            if gear_data:
                lines.append(f"  {name}:")
                for item in gear_data:
                    ln, gear, gw, rpm_pct, label = item
                    rpm_str = f" RPM:{rpm_pct:.0%}" if rpm_pct else ""
                    gear_hint = ""
                    if gw < 0.80:
                        gear_hint = " <- GEAR TOO HIGH, shift down"
                    elif gw < 0.90:
                        gear_hint = " <- suboptimal, consider lower gear"
                    lines.append(f"    Lap {ln} ({label}): Gear {gear}  GearOpt={gw:.2f}{rpm_str}{gear_hint}")
                # Flag gear changes mid-corner
                _by_lap: Dict[int, List[int]] = {}
                for ln, gear, gw, rpm_pct, label in gear_data:
                    _by_lap.setdefault(ln, []).append(gear)
                for ln, gears in _by_lap.items():
                    if len(set(gears)) > 1:
                        lines.append(f"    >> Lap {ln}: Gear changes mid-corner ({' → '.join(str(g) for g in gears)}) — consider earlier downshift")
                lines.append("")

    # ── Brake bias analysis (if data available)
    brake_bias_available = any(
        pt.get("brake_bias") is not None and pt.get("brake_bias") > 0
        for lap in laps
        for pt in lap.get("track", [])
    )

    if brake_bias_available:
        lines.append("BRAKE BIAS ANALYSIS:")
        lines.append("(brake_bias: ratio of front brake pressure, e.g. 0.56 = 56% front)")
        lines.append("")

        for spec in ref_corners:
            cid = spec["id"]
            name = spec.get("name") or f"Corner {cid}"

            bias_data = []
            for lap in laps:
                corner = lap_corner_map[lap["lap_num"]].get(cid)
                if not corner:
                    continue

                corner_track = [
                    pt for pt in lap["track"]
                    if corner["start_frame"] <= pt["frame"] <= corner["end_frame"]
                ]

                # Sample brake bias during braking phase
                braking_pts = [pt for pt in corner_track if (pt.get("brake", 0) or 0) > 0.3]
                if braking_pts:
                    avg_bias = sum(pt.get("brake_bias", 0) or 0 for pt in braking_pts) / len(braking_pts)
                    if avg_bias > 0:
                        bias_data.append((lap["lap_num"], avg_bias))

            if bias_data:
                lines.append(f"  {name}:")
                for ln, bias in bias_data:
                    bias_hint = ""
                    if bias > 0.65:
                        bias_hint = " <- front-heavy, risk of front lock"
                    elif bias < 0.45:
                        bias_hint = " <- rear-heavy, risk of rear lock"
                    lines.append(f"    Lap {ln}: {bias:.2f} ({bias*100:.0f}% front){bias_hint}")
                lines.append("")

    # ── Brake thermal analysis (front/rear imbalance, fade, extremes)
    _brake_thermals = analyze_brake_thermals(laps)
    _bt_rows = [
        e for e in _brake_thermals["per_lap"]
        if e["front_avg"] is not None or e["rear_avg"] is not None
    ]
    if _bt_rows:
        lines.append("BRAKE THERMAL ANALYSIS:")
        lines.append("(averages over heavy-braking frames, brake > 40%)")
        for e in _bt_rows:
            _f = f"{e['front_avg']:.0f}C" if e["front_avg"] is not None else "n/a"
            _r = f"{e['rear_avg']:.0f}C" if e["rear_avg"] is not None else "n/a"
            _p = f"  peak front {e['peak_front']:.0f}C" if e["peak_front"] is not None else ""
            lines.append(f"  Lap {e['lap_num']}: front avg {_f}  rear avg {_r}{_p}")
        if _brake_thermals["imbalance_note"]:
            lines.append(f"  >> IMBALANCE: {_brake_thermals['imbalance_note']}")
        if _brake_thermals["fade_note"]:
            lines.append(f"  >> FADE RISK: {_brake_thermals['fade_note']}")
        # ── Cross-reference brake bias with brake thermals
        if brake_bias_available and _brake_thermals["imbalance_note"]:
            _session_bias: Optional[float] = None
            for spec in ref_corners:
                cid = spec["id"]
                for lap in laps:
                    corner = lap_corner_map[lap["lap_num"]].get(cid)
                    if not corner:
                        continue
                    corner_track = [
                        pt for pt in lap["track"]
                        if corner["start_frame"] <= pt["frame"] <= corner["end_frame"]
                    ]
                    braking_pts = [pt for pt in corner_track if (pt.get("brake", 0) or 0) > 0.3]
                    if braking_pts:
                        _biases = [pt.get("brake_bias", 0) or 0 for pt in braking_pts if (pt.get("brake_bias", 0) or 0) > 0]
                        if _biases:
                            _session_bias = sum(_biases) / len(_biases)
                            break
                if _session_bias is not None:
                    break
            if _session_bias is not None and _session_bias > 0.60:
                _front_avgs = [e["front_avg"] for e in _bt_rows if e["front_avg"] is not None]
                _rear_avgs = [e["rear_avg"] for e in _bt_rows if e["rear_avg"] is not None]
                if _front_avgs and _rear_avgs:
                    _fmean = sum(_front_avgs) / len(_front_avgs)
                    _rmean = sum(_rear_avgs) / len(_rear_avgs)
                    if _fmean > _rmean * 1.3:
                        lines.append(f"  >> COMBINED: Front-heavy bias ({_session_bias:.2f}) with elevated front brake temps ({_fmean:.0f}C vs {_rmean:.0f}C rear) — consider reducing front bias.")
        lines.append("")

    # ── Brake temperature extreme flag
    _max_brake_temps: List[float] = []
    for lap in laps:
        for pt in lap.get("track", []):
            for corner in ["fl", "fr", "rl", "rr"]:
                bt = pt.get(f"brake_temp_{corner}", 0)
                if isinstance(bt, (int, float)) and bt > 0:
                    _max_brake_temps.append(bt)
    if _max_brake_temps:
        _peak_bt = max(_max_brake_temps)
        if _peak_bt > 500:
            lines.append("")
            lines.append(f"BRAKE THERMAL WARNING: Peak brake temperature {_peak_bt:.0f}C detected.")
            lines.append("  Consider shorter braking zones or adjusting brake bias to avoid fade.")

    # ── Suspension / alignment analysis
    profile_corners = data.get("profile_corners", [])
    _suspension = analyze_suspension(laps, profile_corners, lap_corner_map=lap_corner_map)
    _has_sus = any(
        _suspension[k] for k in ("bottoming_notes", "travel_delta_notes", "camber_notes")
    )
    if _has_sus:
        lines.append("SUSPENSION & ALIGNMENT ANALYSIS:")
        for note in _suspension["bottoming_notes"]:
            lines.append(f"  >> BOTTOMING: {note}")
        for note in _suspension["travel_delta_notes"]:
            lines.append(f"  >> TRAVEL: {note}")
        for note in _suspension["camber_notes"]:
            lines.append(f"  >> CAMBER: {note}")
        lines.append("")

    lines.append("=" * 60)
    lines.append("RESPONSE FORMAT — FOLLOW EXACTLY. NO DEVIATION.")
    lines.append("")
    lines.append("CRITICAL STYLE RULE: Be extremely concise. Each bullet is ONE short actionable sentence.")
    lines.append("The driver reads this at a glance between sessions. Information overload = useless.")
    lines.append("Good example: 'Brake 1s sooner and turn in later to setup for exit (+0.7s).'")
    lines.append("Bad example: 'Lap 4 applies 1.02G peak braking with 25% trail brake into a corner where Lap 2 peaks at 0.35G — remove the brake input entirely.'")
    lines.append("One supporting number per bullet maximum. No multi-stat comparisons. No lap-vs-lap narration.")
    lines.append("")
    lines.append("Output in clean Markdown. Use ## for section headers.")
    lines.append("Use bullet points inside sections. Never use numbered lists inside bullets.")
    lines.append("No padding. No preamble.")
    lines.append("Start your response directly with '## [Car] — [Track Name] Debrief'")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 1. TOP 3 TIME-LOSS CORNERS")
    lines.append("")
    lines.append("Exclude any corners flagged as corrupt/capped in the data above.")
    lines.append("Format each corner EXACTLY like this:")
    lines.append("")
    lines.append("- **[Corner Name]** | -[delta]s | [short action to fix it]")
    lines.append("")
    lines.append("The action must be a direct instruction (e.g. 'lift instead of braking', 'brake 0.5s earlier', 'carry 15 km/h more apex speed').")
    lines.append("Do NOT explain the cause in detail — just state what to change.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 2. DRIVING TECHNIQUE")
    lines.append("")
    lines.append("5 bullets maximum. Each bullet is ONE short instruction the driver can act on immediately.")
    lines.append("Format: **[Corner]:** [do X] ([one supporting number]).")
    lines.append("")
    lines.append("Examples of correct brevity:")
    lines.append("  - **Turn 3:** Brake 0.5s earlier and trail deeper to hold 94 km/h apex.")
    lines.append("  - **Rainey Curve:** Commit to turn-in 0.3s sooner — no braking after initial lift.")
    lines.append("  - **Turn 10:** Get on throttle at apex — coasting loses 17 km/h exit speed.")
    lines.append("")
    lines.append("Do NOT say 'consider' or 'try'. Do NOT cite multiple numbers or compare laps inline.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 3. CONSISTENCY")
    lines.append("")
    lines.append("3 bullets maximum. One line each.")
    lines.append("Format: **[Corner]:** [apex range] km/h spread — [one-phrase cause: 'no braking marker' or 'commitment varies'].")
    lines.append("")
    lines.append("---")
    lines.append("")
    if car_known:
        lines.append(f"## 4. CAR SETUP — {car_model}")
        lines.append("")
        lines.append("Output a Markdown table with exactly these columns:")
        lines.append("| Parameter | Signal | Change |")
        lines.append("| --- | --- | --- |")
        lines.append("")
        lines.append("Rules:")
        lines.append("- Maximum 4 rows. Only where telemetry gives a CLEAR signal.")
        lines.append("- 'Signal' = one data point (e.g. '28.4 psi hot', 'peak brake temp 620C').")
        lines.append("- 'Change' = short directional action (e.g. 'reduce 0.5 psi', 'raise rear 1 step').")
        lines.append("- Parameter and Signal MUST describe the same subsystem. Never mix evidence across systems.")
        lines.append("- Tyre pressure rows MUST use tyre pressure evidence in psi only -- never brake temperature, tyre temperature, or wear.")
        lines.append("- Brake temperature evidence may only support brake-related parameters, and only if that brake-related parameter is listed as adjustable for this car.")
        lines.append("- If you do not have a matching telemetry signal for a parameter, omit that row entirely.")
        if tuning_block:
            lines.append("- ONLY recommend parameters listed in the CAR SETUP PARAMETERS section above.")
            lines.append("- If a parameter is NOT in that list, the car cannot adjust it -- do NOT suggest it.")
        else:
            lines.append("- Do NOT assume brake bias is adjustable -- many cars have fixed brake bias.")
            lines.append("- Only recommend parameters you are certain this car can adjust in AC Evo.")
    else:
        lines.append("## 4. CAR SETUP — SKIPPED")
        lines.append("")
        lines.append("Car identity unknown. Skip.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 5. STRAIGHTS & SECTORS")
    lines.append("")
    lines.append("3 bullets maximum. One line each.")
    lines.append("Use the STRAIGHT/SECTOR ANALYSIS and EXIT-TO-ENTRY CORRELATION data above.")
    lines.append("Format: **[Corner A → Corner B]:** [insight] ([one number]).")
    lines.append("Only include straights where there is meaningful time spread between laps or exit speed is compromising the next corner.")
    lines.append("Example: '**T2 → T3:** Poor T2 exit costs 0.4s on straight — get on throttle 0.3s earlier.'")
    lines.append("Example: '**T4 → T5:** 0.5s spread on straight — carry more speed through T4 exit.'")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"## 6. TRACK NOTES — {track_label}")
    lines.append("")
    lines.append("3 bullets maximum. One line each.")
    lines.append("Format: **[Corner/Section]:** [short insight] ([one number]).")
    lines.append("Only include observations where the car has significant unused grip or the corner can be taken differently than expected.")
    lines.append("Example: '**Blanchimont:** Can be taken flat — only 0.77G used vs 2.26G available.'")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 7. SINGLE BIGGEST GAIN")
    lines.append("")
    lines.append("Exactly one sentence: [corner] + [what to change] + [expected delta].")
    lines.append("Example: 'Lift instead of braking into Turn 6 to gain ~0.7s.'")
    lines.append("No hedging. No 'this could' or 'potentially'.")
    lines.append("")
    lines.append("=" * 60)
    lines.append("REMEMBER: Brevity is paramount. The driver needs quick, actionable cues — not a data thesis.")
    lines.append("Each bullet = one action + one number. If a section has no actionable data, skip it entirely.")
    lines.append("=" * 60)

    prompt = "\n".join(lines)

    with open(ai_prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt)

    log_debug(Component.ANALYZER, "Generated AI prompt", path=ai_prompt_path, chars=len(prompt))
    return ai_prompt_path
