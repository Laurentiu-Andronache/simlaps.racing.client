#!/usr/bin/env python3
"""Generate compact car_tuning_catalog.json for use in AI prompt generation."""
import json
import os
import re
import struct

EXTRACTED_CARS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extracted", "content", "cars")
PREFERRED_SETUP_TOKENS = ("stock", "safe", "base")
AVOID_SETUP_TOKENS = ("wet",)
PREFERRED_LIMITS_TOKENS = ("limits", "lim")
IGNORED_FILE_TOKENS = {
    "carsetup",
    "carsetuplimits",
    "setup",
    "limits",
    "limit",
    "lim",
    "stock",
    "safe",
    "base",
    "wet",
}

PARAMETER_PATTERNS = [
    {"label": "Front tyre pressure", "top_field": 4, "occurrence_kind": "front_axle", "setup_field": 1, "limits_child_fields": [1], "validator": "pressure"},
    {"label": "Rear tyre pressure", "top_field": 4, "occurrence_kind": "rear_axle", "setup_field": 1, "limits_child_fields": [1], "validator": "pressure"},
    {"label": "Front camber", "top_field": 4, "occurrence_kind": "front_axle", "setup_field": 2, "limits_child_fields": [2, 5], "validator": "camber"},
    {"label": "Rear camber", "top_field": 4, "occurrence_kind": "rear_axle", "setup_field": 2, "limits_child_fields": [2, 5], "validator": "camber"},
    {"label": "Front toe", "top_field": 4, "occurrence_kind": "front_axle", "setup_field": 3, "limits_child_fields": [3], "validator": "toe"},
    {"label": "Rear toe", "top_field": 4, "occurrence_kind": "rear_axle", "setup_field": 3, "limits_child_fields": [3], "validator": "toe"},
    {"label": "Front ride height", "top_field": 6, "occurrence_kind": "first", "setup_field": 2, "limits_child_fields": [2], "validator": "ride_height"},
    {"label": "Rear ride height", "top_field": 6, "occurrence_kind": "first", "setup_field": 3, "limits_child_fields": [3], "validator": "ride_height"},
    {"label": "Fuel load", "top_field": 7, "occurrence_kind": "first", "setup_field": 1, "limits_child_fields": [1], "validator": "fuel"},
]


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7
    raise ValueError("unterminated varint")


def decode_message(data: bytes, start: int = 0, end: int | None = None) -> list[dict]:
    if end is None:
        end = len(data)
    pos = start
    items = []
    while pos < end:
        tag_pos = pos
        tag, pos = read_varint(data, pos)
        field = tag >> 3
        wire = tag & 7
        entry = {"field": field, "wire": wire, "offset": tag_pos}
        if wire == 0:
            value, pos = read_varint(data, pos)
            entry["value"] = value
        elif wire == 2:
            size, pos = read_varint(data, pos)
            raw = data[pos:pos + size]
            if len(raw) < size:
                raise ValueError("truncated length-delimited field")
            pos += size
            entry["size"] = size
            try:
                entry["children"] = decode_message(raw, 0, len(raw))
            except ValueError:
                entry["raw_hex"] = raw.hex()
        elif wire == 5:
            raw = data[pos:pos + 4]
            if len(raw) < 4:
                raise ValueError("truncated 32-bit field")
            pos += 4
            entry["value"] = struct.unpack("<f", raw)[0]
            entry["raw_hex"] = raw.hex()
        else:
            break
        items.append(entry)
    return items


def decode_file(path: str) -> list[dict]:
    with open(path, "rb") as f:
        return decode_message(f.read())


def tokenize_filename(name: str) -> list[str]:
    stem = os.path.splitext(name)[0].lower()
    parts = re.split(r"[^a-z0-9]+", stem)
    return [part for part in parts if part and part not in IGNORED_FILE_TOKENS]


def choose_setup_file(files: list[str]) -> str:
    dry_files = [name for name in files if not any(token in name.lower() for token in AVOID_SETUP_TOKENS)]
    candidates = dry_files or files

    def score(name: str) -> tuple[int, int, int]:
        lowered = name.lower()
        preferred = sum(token in lowered for token in PREFERRED_SETUP_TOKENS)
        avoided = sum(token in lowered for token in AVOID_SETUP_TOKENS)
        return preferred, -avoided, -len(lowered)

    return sorted(candidates, key=score, reverse=True)[0]


def choose_limits_file(setup_name: str, files: list[str]) -> str:
    setup_tokens = set(tokenize_filename(setup_name))

    def score(name: str) -> tuple[int, int, int, int]:
        lowered = name.lower()
        file_tokens = set(tokenize_filename(name))
        overlap = len(setup_tokens & file_tokens)
        preferred = sum(token in lowered for token in PREFERRED_LIMITS_TOKENS)
        avoided = sum(token in lowered for token in AVOID_SETUP_TOKENS)
        return overlap, preferred, -avoided, -len(lowered)

    return sorted(files, key=score, reverse=True)[0]


def discover_car_files() -> list[tuple[str, str, str]]:
    discovered = []
    for car_key in sorted(os.listdir(EXTRACTED_CARS_DIR)):
        setup_dir = os.path.join(EXTRACTED_CARS_DIR, car_key, "data", "setup")
        if not os.path.isdir(setup_dir):
            continue

        setup_files = sorted(name for name in os.listdir(setup_dir) if name.endswith(".carsetup"))
        limits_files = sorted(name for name in os.listdir(setup_dir) if name.endswith(".carsetuplimits"))
        if not setup_files or not limits_files:
            continue

        setup_name = choose_setup_file(setup_files)
        limits_name = choose_limits_file(setup_name, limits_files)
        discovered.append(
            (
                car_key,
                os.path.join(setup_dir, setup_name),
                os.path.join(setup_dir, limits_name),
            )
        )
    return discovered


def get_top_level_entry(decoded: list[dict], field: int, occurrence: int) -> dict | None:
    matches = [entry for entry in decoded if entry.get("field") == field]
    if occurrence >= len(matches):
        return None
    return matches[occurrence]


def get_top_level_entries(decoded: list[dict], field: int) -> list[dict]:
    return [entry for entry in decoded if entry.get("field") == field]


def get_child_entry(entry: dict | None, field: int) -> dict | None:
    if not entry:
        return None
    for child in entry.get("children", []):
        if child.get("field") == field:
            return child
    return None


def get_float_value(entry: dict | None, field: int) -> float | None:
    child = get_child_entry(entry, field)
    if child is None:
        return None
    value = child.get("value")
    return float(value) if value is not None else None


def get_range(entry: dict | None) -> tuple[float, float, float] | None:
    if not entry:
        return None
    step = get_float_value(entry, 1)
    minimum = get_float_value(entry, 2)
    maximum = get_float_value(entry, 3)
    if step is None:
        return None
    if minimum is None and maximum is not None:
        minimum = step
    if minimum is None:
        return None
    if maximum is None:
        maximum = minimum
    return step, minimum, maximum


def settings_count(step: float, minimum: float, maximum: float) -> int:
    if step <= 0:
        return 1
    span = maximum - minimum
    if span <= 0:
        return 1
    return int(round(span / step)) + 1


def value_in_range(value: float, minimum: float, maximum: float, step: float) -> bool:
    tolerance = max(abs(step) * 0.51, 1e-5)
    return (minimum - tolerance) <= value <= (maximum + tolerance)


def is_pressure_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return 15.0 <= value <= 40.0 and 10.0 <= minimum <= 40.0 and minimum <= maximum <= 45.0 and 0.009 <= step <= 5.0 and 1 <= count <= 200


def is_camber_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return -8.0 <= value <= 1.0 and -10.0 <= minimum <= 1.0 and minimum <= maximum <= 2.0 and 0.001 <= step <= 1.0 and 1 <= count <= 100


def is_toe_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return -2.0 <= value <= 2.0 and -2.5 <= minimum <= 2.0 and -2.5 <= maximum <= 2.5 and 0.00001 <= step <= 0.5 and 1 <= count <= 200


def is_ride_height_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    metric_scaled = 0.02 <= value <= 0.2 and 0.02 <= minimum <= 0.2 and minimum <= maximum <= 0.25 and 0.0001 <= step <= 0.02
    millimetre_scaled = 20.0 <= value <= 250.0 and 20.0 <= minimum <= 250.0 and minimum <= maximum <= 250.0 and 1.0 <= step <= 20.0
    return (metric_scaled or millimetre_scaled) and 1 <= count <= 100


def is_fuel_candidate(value: float, minimum: float, maximum: float, step: float, count: int) -> bool:
    return 0.0 <= value <= 250.0 and 0.0 <= minimum <= 250.0 and 10.0 <= maximum <= 250.0 and 0.5 <= step <= 10.0 and 1 <= count <= 300


VALIDATORS = {
    "pressure": is_pressure_candidate,
    "camber": is_camber_candidate,
    "toe": is_toe_candidate,
    "ride_height": is_ride_height_candidate,
    "fuel": is_fuel_candidate,
}


def get_preferred_occurrence(decoded: list[dict], field: int, occurrence_kind: str) -> int:
    matches = get_top_level_entries(decoded, field)
    if occurrence_kind == "rear_axle":
        if len(matches) >= 4:
            return 2
        if len(matches) >= 2:
            return 1
    return 0


def find_matching_limits_child(
    limits_decoded: list[dict],
    top_field: int,
    preferred_occurrence: int,
    child_fields: list[int],
    default_value: float,
    validator_name: str,
) -> tuple[dict | None, int | None]:
    parents = get_top_level_entries(limits_decoded, top_field)
    if not parents:
        return None, None

    ordered_indices = [idx for idx in range(preferred_occurrence, len(parents))]
    ordered_indices.extend(idx for idx in range(0, preferred_occurrence))
    validator = VALIDATORS[validator_name]

    for idx in ordered_indices:
        for child_field in child_fields:
            child = get_child_entry(parents[idx], child_field)
            range_info = get_range(child)
            if range_info is None:
                continue
            step, minimum, maximum = range_info
            count = settings_count(step, minimum, maximum)
            if value_in_range(default_value, minimum, maximum, step) and validator(default_value, minimum, maximum, step, count):
                return child, count
    return None, None


def append_detected_param(params: list[dict], label: str, settings: int) -> None:
    if settings <= 1:
        return
    if any(existing["label"] == label for existing in params):
        return
    params.append({"label": label, "settings_count": settings})


def build_car_params(setup_decoded: list[dict], limits_decoded: list[dict]) -> list[dict]:
    params = []
    for pattern in PARAMETER_PATTERNS:
        preferred_occurrence = get_preferred_occurrence(setup_decoded, pattern["top_field"], pattern["occurrence_kind"])
        setup_parent = get_top_level_entry(setup_decoded, pattern["top_field"], preferred_occurrence)
        default_value = get_float_value(setup_parent, pattern["setup_field"])
        if default_value is None:
            continue

        limits_child, count = find_matching_limits_child(
            limits_decoded,
            pattern["top_field"],
            preferred_occurrence,
            pattern["limits_child_fields"],
            default_value,
            pattern["validator"],
        )
        if limits_child is None or count is None:
            continue

        append_detected_param(params, pattern["label"], count)

    return params


def build_catalog() -> dict:
    catalog = {}
    for car_key, setup_path, limits_path in discover_car_files():
        setup_decoded = decode_file(setup_path)
        limits_decoded = decode_file(limits_path)
        params = build_car_params(setup_decoded, limits_decoded)

        if params:
            catalog[car_key] = params

    return catalog


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    dest_dir = os.path.join(here, "..", "src", "core", "data")
    dest = os.path.join(dest_dir, "car_tuning_catalog.json")

    os.makedirs(dest_dir, exist_ok=True)

    catalog = build_catalog()

    with open(dest, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2)

    print(f"Generated {len(catalog)} car entries -> {dest}")
    sample_key = "ks_porsche_992_gt3_rs"
    if sample_key in catalog:
        print(f"\nSample ({sample_key}):")
        for p in catalog[sample_key]:
            print(f"  - {p['label']}  ({p['settings_count']} settings)")


if __name__ == "__main__":
    main()
