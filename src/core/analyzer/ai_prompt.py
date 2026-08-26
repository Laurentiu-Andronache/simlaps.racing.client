"""AI coaching prompt generation entry point."""

import os
from datetime import datetime
from typing import Any, Dict, Optional

from src.core.analyzer.prompt.car import build_car_sections
from src.core.analyzer.prompt.context import PromptContext
from src.core.analyzer.prompt.driving import build_driving_sections
from src.core.analyzer.prompt.response import render_response_contract
from src.core.analyzer.prompt.session import (
    build_diagnostic_sections,
    build_session_sections,
    build_single_lap_sections,
)
from src.utils.structured_logger import Component, log_debug


async def generate_ai_prompt(
    data: Dict[str, Any],
    output_dir: str,
    output_prefix: Optional[str] = None,
) -> str:
    """Create the normalized context, render sections, and write the prompt."""
    prefix = output_prefix or datetime.now().strftime("%m-%d-%H-%M-%S")
    ai_prompt_path = os.path.join(output_dir, f"telemetry_{prefix}_ai_prompt.txt")
    os.makedirs(output_dir, exist_ok=True)

    context = PromptContext.from_data(data)
    if not context.all_laps:
        with open(ai_prompt_path, "w", encoding="utf-8") as file_handle:
            file_handle.write("No telemetry data available for coaching.\n")
        return ai_prompt_path

    if context.analysis_mode != "full" or not context.ref_corners:
        lines = build_diagnostic_sections(context)
        prompt = "\n".join(lines) + "\n"
    elif not context.comparison_available:
        lines = build_single_lap_sections(context)
        prompt = "\n".join(lines) + "\n"
    else:
        lines, lap_corner_map = build_session_sections(context)
        driving_lines, lap_corner_map = build_driving_sections(
            context,
            lap_corner_map,
        )
        lines.extend(driving_lines)
        lines.extend(build_car_sections(context, lap_corner_map))
        lines.extend(render_response_contract(context))
        prompt = "\n".join(lines)

    with open(ai_prompt_path, "w", encoding="utf-8") as file_handle:
        file_handle.write(prompt)

    log_debug(
        Component.ANALYZER,
        "Generated AI prompt",
        path=ai_prompt_path,
        chars=len(prompt),
    )
    return ai_prompt_path
