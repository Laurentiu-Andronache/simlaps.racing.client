"""
Telemetry Analyzer Module

Analyzes captured telemetry data and generates HTML reports and AI coaching prompts.
Based on test_scripts/telemetry/2-analyze.py
"""

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.core.analyzer.ai_prompt import generate_ai_prompt
from src.core.analyzer.html_renderer import render_html
from src.core.telemetry_capture import CaptureMetadata, FrameData
from src.core.track_catalog import select_track_profile
from src.models import SharedSessionManager
from src.utils.structured_logger import log_debug, log_info, log_warning, log_error, log_exception, Component

# ── Constants used directly in TelemetryAnalyzer.analyze()
from src.core.analyzer._util import (
    _PLAUSIBLE_FRAME_THRESHOLD,
)
# ── Functions
from src.core.analyzer._util import (
    _safe_4, _sanitize_slip, _optional_float, _fraction,
    _median, _interpolate_value, _median3, _local_average,
    _confidence_label, _decide_analysis_mode,
    _profile_corner_sanity_notes,
    _corner_measurement_window, _find_frame_index,
    _trend_direction, _avg,
    _select_track_profile_for_analysis,
    get_physics, get_graphics,
    extract_car_state,
    variation_label, classify_corner_issue,
    format_car_state, balance_hint,
)
from src.core.analyzer.canonical import _build_canonical_lap, _canonical_bins_for_profile
from src.core.analyzer.corner_detection import (
    _detect_profiled_corners_canonical,
    detect_corners, detect_profiled_corners,
    match_profiled_corners, match_corners,
    corner_segment_time,
)
from src.core.analyzer.lap_detection import (
    _detect_laps_by_timing_state,
    detect_laps,
)
from src.core.analyzer.metrics import (
    analyze_corner_phases,
    analyze_grip_utilization,
    analyze_lap_tyre_state,
    analyze_tyre_grip_degradation,
    analyze_electronics_per_lap,
    analyze_brake_thermals,
    analyze_suspension,
)
from src.core.analyzer.build_track import build_track
from src.core.analyzer.analysis_result import AnalysisResult
from src.core.analyzer.session_summary import _session_summary_path, _write_session_summary, _load_previous_summary


_LAP_TIME_ALIGNMENT_TOLERANCE_MS = 2.0


def _nearest_lap_marker_by_time(markers: List, timing_lap_time: Any):
    """Return the nearest unused log marker within rounding tolerance."""
    if (
        not isinstance(timing_lap_time, (int, float))
        or isinstance(timing_lap_time, bool)
    ):
        return None

    candidates = []
    for order, marker in enumerate(markers):
        marker_lap_time = marker[1]
        if (
            not isinstance(marker_lap_time, (int, float))
            or isinstance(marker_lap_time, bool)
        ):
            continue
        delta_ms = abs(float(marker_lap_time) - float(timing_lap_time))
        if delta_ms <= _LAP_TIME_ALIGNMENT_TOLERANCE_MS:
            candidates.append((delta_ms, order, marker))

    if not candidates:
        return None
    return min(candidates, key=lambda candidate: (candidate[0], candidate[1]))[2]


def _read_static_track_config(frames: List[FrameData]) -> tuple[Optional[str], Optional[str]]:
    """Extract authoritative track/config names from the static SHM region.

    AC Evo publishes ``track`` / ``track_configuration`` in the static region;
    these are the reliable layout selectors (graphics lap-length reads garbage
    in 0.8.x). The static payload is constant across frames, so the first
    populated values win.
    """
    track = config = None
    for frame in frames:
        static = frame.static or {}
        track = track or static.get("track") or None
        config = config or static.get("track_configuration") or None
        if track and config:
            break
    return track, config


class TelemetryAnalyzer:
    """Analyzes telemetry data and generates reports."""

    def __init__(
        self,
        output_dir: str,
        track_catalog: Optional[dict] = None,
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

        static_track_name, static_config_name = _read_static_track_config(frames)
        track_key, track_profile = _select_track_profile_for_analysis(
            static_track_name or track_name, static_config_name
        )
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
        lap_types = None
        timing_bounds = _detect_laps_by_timing_state(track, hz=hz) or []

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
                            str(b[3]) if len(b) > 3 and b[3] is not None else "VALID",
                        )
                        for b in game_lap_boundaries
                    ),
                    key=lambda item: item[0],
                )
                start_frame = track[0]["frame"] if track else 0
                if timing_bounds:
                    # Callback frame indices can be late when ACE buffers its
                    # file log or the UI event loop stalls. SHM timer changes
                    # preserve the physical order and exact frame boundary;
                    # enrich those boundaries with matching log metadata by
                    # lap time instead of trusting callback arrival order.
                    track_by_frame = {point["frame"]: point for point in track}
                    unused_markers = list(sorted_markers)
                    reconciled_markers = []
                    callback_frame_deltas = []
                    for idx, frame in enumerate(timing_bounds):
                        point = track_by_frame.get(frame, {})
                        timing_lap_time = point.get("last_lap_time_ms")
                        match = _nearest_lap_marker_by_time(
                            unused_markers,
                            timing_lap_time,
                        )
                        if match is not None:
                            unused_markers.remove(match)
                            callback_frame_deltas.append(abs(match[0] - frame))
                        reconciled_markers.append(
                            (
                                frame,
                                timing_lap_time if timing_lap_time is not None else (match[1] if match else None),
                                initial_completed_laps + idx + 1,
                                match[3] if match else "VALID",
                            )
                        )

                    lap_bounds = [start_frame] + [marker[0] for marker in reconciled_markers]
                    lap_times_ms = [marker[1] for marker in reconciled_markers]
                    lap_numbers = [marker[2] for marker in reconciled_markers]
                    lap_types = [marker[3] for marker in reconciled_markers]
                    materially_delayed = any(
                        delta > max(2, int(round(hz * 2.0)))
                        for delta in callback_frame_deltas
                    )
                    if len(sorted_markers) != len(timing_bounds) or materially_delayed:
                        analysis_notes.append(
                            "Delayed or incomplete log callbacks were realigned to shared-memory timing boundaries."
                        )
                    log_info(
                        Component.ANALYZER,
                        "Lap detection successful",
                        method="shared-memory timing boundaries enriched by game logs",
                        laps=len(timing_bounds),
                    )
                else:
                    lap_bounds = [start_frame] + [marker[0] for marker in sorted_markers]
                    lap_times_ms = [marker[1] for marker in sorted_markers]
                    lap_numbers = [
                        marker[2] if marker[2] is not None else initial_completed_laps + idx + 1
                        for idx, marker in enumerate(sorted_markers)
                    ]
                    lap_types = [marker[3] for marker in sorted_markers]
                    log_info(Component.ANALYZER, "Lap detection successful", method="authoritative game log boundaries", laps=len(lap_bounds) - 1)
                if initial_completed_laps > 0 and (not lap_numbers or lap_numbers[0] > 1):
                    analysis_notes.append(
                        f"Capture started after {initial_completed_laps} completed game lap(s); earlier laps are omitted from telemetry."
                    )
            else:
                lap_bounds = game_lap_boundaries
                log_info(Component.ANALYZER, "Lap detection successful", method="authoritative game log boundaries", laps=len(lap_bounds) - 1)
        # 2nd priority: Shared memory timing state (last_laptime_ms updates)
        else:
            if timing_bounds and len(timing_bounds) >= 1:
                start_frame = track[0]["frame"] if track else 0
                lap_bounds = [start_frame] + timing_bounds
                log_info(Component.ANALYZER, "Lap detection successful", method="shared memory timing state", laps=len(lap_bounds))
            else:
                lap_bounds = []

        if not lap_bounds or len(lap_bounds) < 2:
            log_warning(Component.ANALYZER, "Lap detection failed", reason="no valid boundaries")
            analysis_mode = "diagnostic"
            analysis_notes.append("No reliable lap boundaries were found from any detection method.")
            lap_bounds = []

        laps = []
        for i in range(len(lap_bounds) - 1):
            s, e = lap_bounds[i], lap_bounds[i + 1]
            game_lap_num = lap_numbers[i] if lap_numbers and i < len(lap_numbers) else i + 1
            lap_type = lap_types[i] if lap_types and i < len(lap_types) else "VALID"
            if lap_type in {"OUTLAP", "INLAP", "ABORTED"}:
                continue
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
            canonical_lap = _build_canonical_lap(
                lap_track, lap_start_frame=s, hz=hz, bins=_canonical_bins_for_profile(track_profile),
            )
            uses_canonical_progress = canonical_lap is not None

            if (
                track_profile
                and track_profile.get("corners")
                and canonical_lap is not None
            ):
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
            game_lap_time_ms = (
                lap_times_ms[i]
                if lap_times_ms and i < len(lap_times_ms)
                else None
            )
            if game_lap_time_ms is not None:
                lap_time = game_lap_time_ms / 1000.0
            else:
                # Fall back to telemetry-derived duration so laps without
                # game-reported times (e.g. invalid/aborted laps) are still
                # included in the analysis rather than silently dropped.
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
                "is_valid": lap_type == "VALID",
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

        # Lap validity is advisory for coaching: invalid laps still reveal
        # what the car and driver are doing, so every completed lap feeds
        # the analysis. is_valid stays on each lap for display only.
        coached_laps = list(laps)
        profile_sanity_notes = _profile_corner_sanity_notes(
            coached_laps,
            profile_corners=track_profile.get("corners", []) if track_profile else None,
        )
        if profile_sanity_notes:
            analysis_mode = "diagnostic"
            analysis_notes.extend(profile_sanity_notes)

        best_lap = min(coached_laps, key=lambda lap: lap["lap_time_s"]) if coached_laps else None
        laps_with_corners = [lap for lap in coached_laps if lap.get("corners")]
        ref_lap = (
            min(laps_with_corners, key=lambda lap: lap["lap_time_s"])
            if laps_with_corners
            else best_lap
        )
        coachable_laps = [
            lap
            for lap in laps_with_corners
            if lap.get("confidence_label") != "low"
        ]
        comparison_pool = coachable_laps or laps_with_corners or ([best_lap] if best_lap else [])
        comparison_pool = sorted(comparison_pool, key=lambda lap: lap["lap_time_s"])
        comparison_lap = (
            comparison_pool[len(comparison_pool) // 2]
            if comparison_pool
            else None
        )
        ref_corners = ref_lap.get("corners", []) if ref_lap else []

        log_info(Component.ANALYZER, "Analysis complete", 
                laps=len(laps), 
                best_lap_time=(f"{best_lap['lap_time_s']:.1f}s" if best_lap else "none"),
                coachable_laps=len(coachable_laps))

        if not ref_corners:
            analysis_mode = "diagnostic"
            analysis_notes.append("No trustworthy canonical corners were available for comparison.")

        corner_data: Dict[Any, Dict[Any, Dict[str, Any]]] = defaultdict(dict)
        corner_speeds: Dict[Any, Dict[Any, float]] = defaultdict(dict)
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
            "best_lap_num": best_lap["lap_num"] if best_lap else None,
            "reference_lap_num": ref_lap["lap_num"] if ref_lap else None,
            "comparison_lap_num": comparison_lap["lap_num"] if comparison_lap else None,
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
        _prev = (
            _load_previous_summary(self._output_dir, _track_label, _car)
            if best_lap
            else None
        )
        if _prev and best_lap:
            _delta = best_lap["lap_time_s"] - _prev["best_lap_time_s"]
            _delta_str = f"+{_delta:.2f}s" if _delta > 0 else f"{_delta:.2f}s"
            analysis_notes.append(
                f"Last session best: {_prev['best_lap_time_str']} "
                f"(today {best_lap['lap_time_str']}, {_delta_str})."
            )
        if best_lap:
            _write_session_summary(
                self._output_dir,
                _track_label,
                _car,
                best_lap["lap_time_s"],
                max((lap.get("max_speed") or 0.0) for lap in laps),
                len(laps),
                _avg_fuel,
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
            best_lap_time=best_lap["lap_time_s"] if best_lap else 0.0,
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
