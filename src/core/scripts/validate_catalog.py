"""Validate the track catalog for hard data errors and advisory warnings."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(__file__).parent.parent / "data" / "track_catalog.json"


@dataclass(frozen=True)
class ValidationResult:
    """Results from validating one track catalog."""

    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    corner_count: int

    @property
    def is_valid(self) -> bool:
        """Return whether the catalog contains no hard validation errors."""

        return not self.errors


def _is_number(value: Any) -> bool:
    """Return whether a value is a finite JSON number (not a boolean)."""

    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def validate_catalog(catalog: Any) -> ValidationResult:
    """Validate catalog data and separate errors from advisory warnings.

    Empty profiles and gaps, overlaps, or wrap-around space between corner
    windows are warnings.  They are useful review signals but are valid for
    layouts whose profile does not cover the entire lap or whose adjacent
    corner windows intentionally overlap.  Schema violations, invalid ranges,
    out-of-bounds positions, and non-sequential corner IDs are errors because
    they make a profile unusable by the catalog consumer.
    """

    errors: list[str] = []
    warnings: list[str] = []
    corner_count = 0

    if not isinstance(catalog, dict):
        return ValidationResult(("SCHEMA: catalog must be a dictionary",), (), 0)
    if not catalog:
        return ValidationResult(("EMPTY: catalog contains no tracks",), (), 0)

    for track_key, track in catalog.items():
        if not isinstance(track, dict):
            errors.append(f"SCHEMA: track '{track_key}' must be a dictionary")
            continue

        for field in ("name", "aliases", "default_config", "configs"):
            if field not in track:
                errors.append(
                    f"SCHEMA: track '{track_key}' missing required field: {field}"
                )

        configs = track.get("configs")
        if not isinstance(configs, dict):
            errors.append(f"SCHEMA: track '{track_key}' configs must be a dictionary")
            continue

        default_config = track.get("default_config")
        if not isinstance(default_config, str) or default_config not in configs:
            errors.append(
                f"SCHEMA: track '{track_key}' default config '{default_config}' "
                "is not defined"
            )

        for config_key, config in configs.items():
            if not isinstance(config, dict):
                errors.append(
                    f"SCHEMA: config '{config_key}' in track '{track_key}' "
                    "must be a dictionary"
                )
                continue

            corners = config.get("corners", [])
            if not isinstance(corners, list):
                errors.append(
                    f"SCHEMA: corners in config '{config_key}' of track "
                    f"'{track_key}' must be a list"
                )
                continue
            if not corners:
                warnings.append(f"EMPTY: {track_key}/{config_key} has no corners")
                continue

            previous_id = 0
            previous_end: float | None = None
            for corner_index, corner in enumerate(corners):
                corner_count += 1
                if not isinstance(corner, dict):
                    errors.append(
                        f"SCHEMA: {track_key}/{config_key} corner {corner_index + 1} "
                        "must be a dictionary"
                    )
                    previous_end = None
                    continue

                missing = [
                    field
                    for field in ("id", "name", "start", "end")
                    if field not in corner
                ]
                if missing:
                    errors.append(
                        f"SCHEMA: {track_key}/{config_key} corner "
                        f"{corner.get('id', corner_index + 1)} missing required field: "
                        f"{', '.join(missing)}"
                    )
                    previous_end = None
                    continue

                corner_id = corner["id"]
                name = corner["name"]
                start = corner["start"]
                end = corner["end"]

                if not isinstance(corner_id, int) or isinstance(corner_id, bool):
                    errors.append(
                        f"SCHEMA: {track_key}/{config_key} corner id {corner_id!r} "
                        "must be an integer"
                    )
                elif corner_id != previous_id + 1:
                    errors.append(
                        f"ID GAP: {track_key}/{config_key} corner id {corner_id} "
                        f"(expected {previous_id + 1})"
                    )

                if not isinstance(name, str) or not name:
                    errors.append(
                        f"SCHEMA: {track_key}/{config_key} corner {corner_id} "
                        "name must be a non-empty string"
                    )

                if not _is_number(start) or not _is_number(end):
                    errors.append(
                        f"SCHEMA: {track_key}/{config_key} corner {corner_id} "
                        "start and end must be finite numbers"
                    )
                    previous_end = None
                    if isinstance(corner_id, int) and not isinstance(corner_id, bool):
                        previous_id = corner_id
                    continue

                start = float(start)
                end = float(end)
                if start >= end:
                    errors.append(
                        f"RANGE: {track_key}/{config_key} corner {corner_id} "
                        f"({name}): start={start:g} >= end={end:g}"
                    )

                if previous_end is not None and start < previous_end:
                    warnings.append(
                        f"OVERLAP: {track_key}/{config_key} corner {corner_id} "
                        f"starts at {start:g} before prev corner ends at "
                        f"{previous_end:g}"
                    )

                if previous_end is not None and start - previous_end > 0.06:
                    warnings.append(
                        f"GAP: {track_key}/{config_key} gap of "
                        f"{start - previous_end:.3f} between corner "
                        f"{corner_id - 1} and {corner_id}"
                    )

                if start < 0 or end > 1:
                    errors.append(
                        f"BOUNDS: {track_key}/{config_key} corner {corner_id}: "
                        f"start={start:g}, end={end:g} out of [0,1]"
                    )

                if isinstance(corner_id, int) and not isinstance(corner_id, bool):
                    previous_id = corner_id
                previous_end = end

            last_corner = corners[-1]
            if isinstance(last_corner, dict) and _is_number(last_corner.get("end")):
                last_end = float(last_corner["end"])
                if last_end < 1.0 and 1.0 - last_end > 0.02:
                    warnings.append(
                        f"WRAP: {track_key}/{config_key} last corner "
                        f"{last_corner.get('name', '<unnamed>')!r} ends at "
                        f"{last_end:g}, gap of {1.0 - last_end:.3f} to finish line"
                    )

    return ValidationResult(tuple(errors), tuple(warnings), corner_count)


def _load_catalog(path: Path) -> Any:
    """Load JSON catalog data, letting the CLI report read/parse failures."""

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def main(catalog_path: Path = CATALOG_PATH) -> int:
    """Validate a catalog, print diagnostics, and return a process status."""

    try:
        catalog = _load_catalog(catalog_path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: unable to load catalog '{catalog_path}': {exc}")
        return 1

    result = validate_catalog(catalog)
    print(
        f"Validated {len(catalog) if isinstance(catalog, dict) else 0} tracks, "
        f"{result.corner_count} corners total"
    )

    if result.errors:
        print(f"\nFound {len(result.errors)} errors:")
        for issue in result.errors:
            print(f"  {issue}")
    if result.warnings:
        print(f"\nFound {len(result.warnings)} warnings:")
        for issue in result.warnings:
            print(f"  {issue}")
    if not result.errors and not result.warnings:
        print("No issues found.")
    elif not result.errors:
        print("No errors found.")

    return 0 if result.is_valid else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "catalog",
        nargs="?",
        type=Path,
        default=CATALOG_PATH,
        help=f"catalog JSON path (default: {CATALOG_PATH})",
    )
    sys.exit(main(parser.parse_args().catalog))
