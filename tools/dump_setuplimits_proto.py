import json
import math
import os
import struct
import sys


def read_varint(data, pos):
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
    raise ValueError("unterminated varint")


def decode_message(data, start=0, end=None):
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
        elif wire == 1:
            raw = data[pos:pos + 8]
            if len(raw) < 8:
                raise ValueError("truncated 64-bit field")
            pos += 8
            value = struct.unpack("<d", raw)[0]
            entry["value"] = value if math.isfinite(value) else None
            entry["raw_hex"] = raw.hex()
        elif wire == 2:
            size, pos = read_varint(data, pos)
            raw = data[pos:pos + size]
            if len(raw) < size:
                raise ValueError("truncated length-delimited field")
            pos += size
            entry["size"] = size
            try:
                entry["children"] = decode_message(raw, 0, len(raw))
            except Exception:
                entry["raw_hex"] = raw.hex()
        elif wire == 5:
            raw = data[pos:pos + 4]
            if len(raw) < 4:
                raise ValueError("truncated 32-bit field")
            pos += 4
            value = struct.unpack("<f", raw)[0]
            entry["value"] = value if math.isfinite(value) else None
            entry["raw_hex"] = raw.hex()
        else:
            entry["unsupported"] = True
            items.append(entry)
            break
        items.append(entry)
    return items


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: python tools/dump_setuplimits_proto.py <file> [out.json]")
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else src + ".proto.json"
    with open(src, "rb") as f:
        data = f.read()
    decoded = decode_message(data)
    payload = {
        "file": os.path.abspath(src),
        "size": len(data),
        "decoded": decoded,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(out)


if __name__ == "__main__":
    main()
