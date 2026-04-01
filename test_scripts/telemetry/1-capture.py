"""
Raw AC Evo shared memory capture.

Captures mapped shared-memory regions as opaque bytes per frame, with no
schema-based field decoding in the frame payload. This is intended for
debugging region contents when struct mappings may be wrong or outdated.

Output format:
  JSONL with:
    - one meta record on line 1
    - one frame record per poll

Each frame contains:
  - timestamp metadata
  - one base64-encoded byte blob per connected region
  - optional per-region SHA1 for quick diffing

Usage:
  venv-sim-laps-client/Scripts/python test_scripts/ac_evo_capture_raw.py --wait
  venv-sim-laps-client/Scripts/python test_scripts/ac_evo_capture_raw.py --hz 20 --out raw_session.jsonl
"""

import argparse
import ctypes.wintypes
import hashlib
import json
import signal
import struct
import sys
import time
from datetime import datetime, timezone

kernel32 = ctypes.windll.kernel32
kernel32.OpenFileMappingW.restype = ctypes.wintypes.HANDLE
kernel32.MapViewOfFile.restype = ctypes.c_void_p
kernel32.UnmapViewOfFile.restype = ctypes.wintypes.BOOL
kernel32.CloseHandle.restype = ctypes.wintypes.BOOL

FILE_MAP_READ = 0x0004

SESSIONS = [None, 1, 2, 3, 0]

# Known AC Evo region sizes from current repo mappings.
REGIONS = {
    "physics": ("acpmf_physics", 1024),
    "graphics": ("acpmf_graphics", 2048),
    "static": ("acpmf_static", 2048),
}


class RegionReader:
    def __init__(self, name: str, size: int):
        self.name = name
        self.size = size
        self._handle = None
        self._addr = None
        self._path_used = None

    def open(self):
        candidates = [f"Local\\{self.name}"]
        for sess in SESSIONS:
            if sess is not None:
                candidates.append(f"\\Sessions\\{sess}\\BaseNamedObjects\\{self.name}")

        for path in candidates:
            handle = kernel32.OpenFileMappingW(FILE_MAP_READ, False, path)
            if not handle:
                continue
            addr = kernel32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, self.size)
            if not addr:
                kernel32.CloseHandle(handle)
                continue
            self._handle = handle
            self._addr = addr
            self._path_used = path
            return True
        return False

    def read_raw(self) -> bytes:
        if not self._addr:
            raise RuntimeError(f"Region not open: {self.name}")
        return (ctypes.c_char * self.size).from_address(self._addr).raw

    def close(self):
        if self._addr:
            try:
                kernel32.UnmapViewOfFile(self._addr)
            except:
                pass
        if self._handle:
            try:
                kernel32.CloseHandle(self._handle)
            except:
                pass
        self._addr = None
        self._handle = None


def _convert_to_dict(obj):
    """Recursively convert dataclasses and objects to dict"""
    if hasattr(obj, "__dataclass_fields__"):
        result = {}
        for field, value in vars(obj).items():
            result[field] = _convert_to_dict(value)
        return result
    elif isinstance(obj, list):
        return [_convert_to_dict(item) for item in obj]
    elif isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    else:
        return str(obj)

def decode_physics(data: bytes) -> dict:
    """Decode physics with AC/ACC structure fallback"""
    from ac_evo_decoder import decode_physics as hybrid_decode
    result = hybrid_decode(data)
    
    # Convert dataclass to dict if needed
    if hasattr(result, "__dataclass_fields__"):
        return {"_decoder": "ac_structure", **_convert_to_dict(result)}
    return result

def decode_graphics(data: bytes) -> dict:
    """Decode graphics with fallback"""
    from ac_evo_decoder import decode_graphics as hybrid_decode
    result = hybrid_decode(data)
    
    # Convert dataclass to dict if needed
    if hasattr(result, "__dataclass_fields__"):
        return {"_decoder": "ac_structure", **_convert_to_dict(result)}
    return result

def decode_static(data: bytes) -> dict:
    """Decode static with fallback"""
    from ac_evo_decoder import decode_static as hybrid_decode
    result = hybrid_decode(data)
    
    # Convert dataclass to dict if needed
    if hasattr(result, "__dataclass_fields__"):
        return {"_decoder": "ac_structure", **_convert_to_dict(result)}
    return result

def connect_regions():
    readers = {}
    for key, (region_name, size) in REGIONS.items():
        reader = RegionReader(region_name, size)
        if reader.open():
            readers[key] = reader
    return readers


def reconnect_missing(readers):
    for key, (region_name, size) in REGIONS.items():
        if key in readers:
            continue
        reader = RegionReader(region_name, size)
        if reader.open():
            readers[key] = reader


def main():
    parser = argparse.ArgumentParser(description="Capture AC Evo shared memory as raw bytes")
    parser.add_argument("--hz", type=float, default=20.0, help="Poll rate in Hz")
    parser.add_argument("--out", type=str, default="", help="Output JSONL path")
    parser.add_argument("--wait", action="store_true", help="Wait for regions to appear")
    parser.add_argument("--reconnect-secs", type=float, default=2.0, help="Reconnect retry interval")
    parser.add_argument("--sha1", action="store_true", help="Include SHA1 per region for quick comparisons")
    args = parser.parse_args()

    interval = 1.0 / max(args.hz, 1.0)
    outpath = args.out or f"ac_evo_raw_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"

    print("Connecting to AC Evo shared memory regions...\n")
    readers = connect_regions()

    if not readers:
        if not args.wait:
            print("No shared memory regions found.")
            print("Start AC Evo first, or pass --wait.")
            sys.exit(1)
        print("No regions found. Waiting for game to start (Ctrl+C to abort)...")
        while not readers:
            time.sleep(2.0)
            readers = connect_regions()

    print(f"Capturing {len(readers)} region(s) at {args.hz:.0f} Hz -> {outpath}")
    print("Ctrl+C to stop.\n")

    running = True

    def _stop(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    frames = 0
    next_reconnect = time.monotonic()
    t_start = time.perf_counter()

    with open(outpath, "w", encoding="utf-8") as f:
        meta = {
            "_record_type": "meta",
            "_captured_at": datetime.now(timezone.utc).isoformat(),
            "_hz": args.hz,
            "_regions_known": list(REGIONS.keys()),
            "_regions_found": list(readers.keys()),
            "_region_names": {k: REGIONS[k][0] for k in readers},
            "_region_paths": {k: r._path_used for k, r in readers.items()},
            "_region_sizes": {k: r.size for k, r in readers.items()},
            "_payload_encoding": "json",
            "_payload_type": "decoded_region_data",
        }
        f.write(json.dumps(meta) + "\n")

        deadline = time.perf_counter()
        while running:
            now_mono = time.monotonic()
            if now_mono >= next_reconnect:
                reconnect_missing(readers)
                next_reconnect = now_mono + max(0.2, args.reconnect_secs)

            frame = {
                "_record_type": "frame",
                "_ts": datetime.now(timezone.utc).isoformat(),
                "_wall_ns": time.time_ns(),
                "_frame": frames + 1,
                "regions": {},
            }

            disconnected = []
            for key, reader in list(readers.items()):
                try:
                    raw = reader.read_raw()
                except Exception as exc:
                    frame["regions"][key] = {"error": str(exc)}
                    disconnected.append(key)
                    continue

                region_payload = {
                    "size": len(raw),
                }
                
                # Decode based on region type
                if key == "physics":
                    region_payload.update(decode_physics(raw))
                elif key == "graphics":
                    region_payload.update(decode_graphics(raw))
                elif key == "static":
                    region_payload.update(decode_static(raw))
                    
                if args.sha1:
                    region_payload["sha1"] = hashlib.sha1(raw).hexdigest()
                frame["regions"][key] = region_payload

            for key in disconnected:
                try:
                    readers[key].close()
                except Exception:
                    pass
                readers.pop(key, None)

            f.write(json.dumps(frame) + "\n")
            frames += 1

            if frames % max(1, int(args.hz * 5)) == 0:
                f.flush()
                elapsed = time.perf_counter() - t_start
                actual_hz = frames / elapsed if elapsed > 0 else 0.0
                print(f"  {frames:>7} frames  {actual_hz:.1f} Hz  connected={','.join(sorted(readers.keys()))}")

            deadline += interval
            sleep_for = deadline - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                deadline = time.perf_counter()

    for reader in readers.values():
        reader.close()

    print(f"\nDone. {frames} frames written to {outpath}")


if __name__ == "__main__":
    main()
