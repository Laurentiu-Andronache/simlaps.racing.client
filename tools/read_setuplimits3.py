#!/usr/bin/env python3
"""
Decode .carsetuplimits protobuf files without a schema.
Reads field-by-field: varint, 32-bit float, 64-bit, and length-delimited.
Prints every numeric value found so we can match against screenshot ground truth.
"""
import struct, math, os, sys


def read_varint(data, pos):
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if not (b & 0x80):
            return result, pos
    return result, pos


def decode_proto_raw(data, indent=0, path=""):
    pos = 0
    results = []
    prefix = "  " * indent

    while pos < len(data):
        if pos >= len(data):
            break
        try:
            tag, pos = read_varint(data, pos)
        except Exception:
            break

        field_num = tag >> 3
        wire_type = tag & 0x07

        if wire_type == 0:  # varint
            val, pos = read_varint(data, pos)
            label = f"{path}.f{field_num}[varint]"
            results.append((label, val))
            print(f"{prefix}field {field_num} (varint): {val}")

        elif wire_type == 1:  # 64-bit
            if pos + 8 > len(data): break
            val_bytes = data[pos:pos+8]
            pos += 8
            f64 = struct.unpack('<d', val_bytes)[0]
            label = f"{path}.f{field_num}[f64]"
            results.append((label, f64))
            if math.isfinite(f64) and abs(f64) < 1e7:
                print(f"{prefix}field {field_num} (f64): {f64}")

        elif wire_type == 2:  # length-delimited
            ln, pos = read_varint(data, pos)
            if pos + ln > len(data): break
            sub = data[pos:pos+ln]
            pos += ln
            print(f"{prefix}field {field_num} (msg, {ln} bytes):")
            decode_proto_raw(sub, indent+1, path=f"{path}.f{field_num}")

        elif wire_type == 5:  # 32-bit float
            if pos + 4 > len(data): break
            val_bytes = data[pos:pos+4]
            pos += 4
            f32 = struct.unpack('<f', val_bytes)[0]
            label = f"{path}.f{field_num}[f32]"
            results.append((label, f32))
            if math.isfinite(f32) and abs(f32) < 1e7:
                print(f"{prefix}field {field_num} (f32): {f32:.6f}")
        else:
            # Unknown wire type - skip (can't reliably continue)
            print(f"{prefix}!! unknown wire_type={wire_type} field={field_num} pos={pos}")
            break

    return results


def analyse_file(filepath, title):
    data = open(filepath, 'rb').read()
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"  {filepath}")
    print(f"  {len(data)} bytes")
    print(f"{'='*70}")
    decode_proto_raw(data)


BASE = r"c:\Storage\my documents\sim-laps-client\extracted\content\cars"

targets = [
    ("ks_porsche_992_gt3_rs",  r"data\setup\limitsporsche992gt3rs.carsetuplimits",  "Porsche 992 GT3 RS"),
    ("ks_ferrari_sf_25",       r"data\setup\ferrarisf25limits.carsetuplimits",      "Ferrari SF25"),
    ("ks_audi_rs_3_sportback", r"data\setup\ks_audi_rs3_limits.carsetuplimits",     "Audi RS3 Sportback"),
]

for car, rel, title in targets:
    path = os.path.join(BASE, car, rel)
    if os.path.exists(path):
        analyse_file(path, title)
    else:
        print(f"NOT FOUND: {path}")
