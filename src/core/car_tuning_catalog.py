"""Car tuning catalog - maps car identifiers to their available setup parameters."""
from __future__ import annotations

import json
import os
from typing import List, Optional

_CATALOG: dict | None = None
_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "data", "car_tuning_catalog.json")


def _load_catalog() -> dict:
    global _CATALOG
    if _CATALOG is None:
        try:
            with open(_CATALOG_PATH, "r", encoding="utf-8") as f:
                _CATALOG = json.load(f)
        except (OSError, json.JSONDecodeError):
            _CATALOG = {}
    return _CATALOG


def _normalise_car_key(car_model: str) -> str:
    """Normalise a car model string to the catalog key format (lowercase, spaces->underscores)."""
    return car_model.strip().lower().replace(" ", "_").replace("-", "_")


def get_tuning_params(car_model: str) -> Optional[List[dict]]:
    """Return a list of tunable parameter dicts for the given car, or None if unknown.

    Each dict has:
        label          – human-readable parameter name
        settings_count – number of discrete settings available
    """
    if not car_model or car_model == "Unknown Car":
        return None

    catalog = _load_catalog()
    key = _normalise_car_key(car_model)

    # Direct match
    if key in catalog:
        return catalog[key]

    # Partial match: car_model may be a display name substring of the key
    # e.g. "Porsche 992 GT3 RS" -> "ks_porsche_992_gt3_rs"
    for catalog_key, params in catalog.items():
        if key in catalog_key or catalog_key in key:
            return params

    return None


def format_tuning_block(car_model: str) -> str:
    """Return a formatted multi-line string listing all tunable parameters.

    Returns an empty string if the car is unknown.
    """
    params = get_tuning_params(car_model)
    if not params:
        return ""

    lines = ["CAR SETUP PARAMETERS (available on this car in AC Evo):"]
    for p in params:
        count = p.get("settings_count", 0)
        label = p.get("label", "")
        suffix = f"  [{count} selectable settings]" if count > 1 else ""
        lines.append(f"- {label}{suffix}")
    lines.append(
        "When recommending setup changes, only suggest adjustments from the above list."
    )
    return "\n".join(lines)
