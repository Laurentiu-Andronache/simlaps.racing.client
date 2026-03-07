"""
ac_evo_capture2.py — Capture all available AC Evo shared memory at 60 Hz
=========================================================================
Reads every known region using the EXACT session-namespaced paths found
by shm_tracer:  \Sessions\1\BaseNamedObjects\acpmf_*

Background
----------
Python's mmap tagname uses the "Local\" prefix which normally resolves to
\Sessions\<current>\BaseNamedObjects\<name>.  If AC Evo is running in a
DIFFERENT Windows session (e.g. session 1 while our script is in session 0
under an elevated command prompt), "Local\" won't find it.

This script tries both the standard "Local\" path AND a direct kernel path
via CreateFileMapping with a full object name, falling back gracefully.

Usage
-----
    python ac_evo_capture2.py
    python ac_evo_capture2.py --hz 60 --out session.jsonl
    python ac_evo_capture2.py --raw          # also save a raw .bin snapshot

Output
------
    <timestamp>.jsonl — one JSON object per frame, all fields from every
                        region that is actually present and readable.
    Line 0 is a special "meta" record listing which regions were found.
"""

import ctypes
import ctypes.wintypes
import mmap
import json
import struct
import time
import sys
import argparse
import signal
import os
from datetime import datetime, timezone

kernel32 = ctypes.windll.kernel32
kernel32.CreateFileMappingW.restype  = ctypes.wintypes.HANDLE
kernel32.OpenFileMappingW.restype    = ctypes.wintypes.HANDLE
kernel32.MapViewOfFile.restype       = ctypes.c_void_p
kernel32.UnmapViewOfFile.restype     = ctypes.wintypes.BOOL
kernel32.CloseHandle.restype         = ctypes.wintypes.BOOL

# ─────────────────────────────────────────────────────────────────────────────
# Shared memory structs (same as previous scripts)
# ─────────────────────────────────────────────────────────────────────────────

class Physics(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("packetId",              ctypes.c_int),
        ("gas",                   ctypes.c_float),
        ("brake",                 ctypes.c_float),
        ("fuel",                  ctypes.c_float),
        ("gear",                  ctypes.c_int),
        ("rpms",                  ctypes.c_int),
        ("steerAngle",            ctypes.c_float),
        ("speedKmh",              ctypes.c_float),
        ("velocity",              ctypes.c_float * 3),
        ("accG",                  ctypes.c_float * 3),
        ("wheelSlip",             ctypes.c_float * 4),
        ("wheelLoad",             ctypes.c_float * 4),
        ("wheelPressure",         ctypes.c_float * 4),
        ("wheelAngularSpeed",     ctypes.c_float * 4),
        ("tyreWear",              ctypes.c_float * 4),
        ("tyreDirtyLevel",        ctypes.c_float * 4),
        ("tyreCoreTemp",          ctypes.c_float * 4),
        ("camberRAD",             ctypes.c_float * 4),
        ("suspensionTravel",      ctypes.c_float * 4),
        ("drs",                   ctypes.c_float),
        ("tc",                    ctypes.c_float),
        ("heading",               ctypes.c_float),
        ("pitch",                 ctypes.c_float),
        ("roll",                  ctypes.c_float),
        ("cgHeight",              ctypes.c_float),
        ("carDamage",             ctypes.c_float * 5),
        ("numberOfTyresOut",      ctypes.c_int),
        ("pitLimiterOn",          ctypes.c_int),
        ("abs",                   ctypes.c_float),
        ("kersCharge",            ctypes.c_float),
        ("kersInput",             ctypes.c_float),
        ("autoShifterOn",         ctypes.c_int),
        ("rideHeight",            ctypes.c_float * 2),
        ("turboBoost",            ctypes.c_float),
        ("ballast",               ctypes.c_float),
        ("airDensity",            ctypes.c_float),
        ("airTemp",               ctypes.c_float),
        ("roadTemp",              ctypes.c_float),
        ("localVelocity",         ctypes.c_float * 3),
        ("accStatus",             ctypes.c_int),
        ("lastFF",                ctypes.c_float),
        ("performanceMeter",      ctypes.c_float),
        ("engineBrake",           ctypes.c_int),
        ("ersRecoveryLevel",      ctypes.c_int),
        ("ersPowerLevel",         ctypes.c_int),
        ("ersHeatCharging",       ctypes.c_int),
        ("ersIsCharging",         ctypes.c_int),
        ("kersCurrentKJ",         ctypes.c_float),
        ("drsAvailable",          ctypes.c_int),
        ("drsEnabled",            ctypes.c_int),
        ("brakeTemp",             ctypes.c_float * 4),
        ("clutch",                ctypes.c_float),
        ("tyreTempI",             ctypes.c_float * 4),
        ("tyreTempM",             ctypes.c_float * 4),
        ("tyreTempO",             ctypes.c_float * 4),
        ("isAIControlled",        ctypes.c_int),
        ("tyreContactPoint",      ctypes.c_float * 4 * 3),
        ("tyreContactNormal",     ctypes.c_float * 4 * 3),
        ("tyreContactHeading",    ctypes.c_float * 4 * 3),
        ("brakeBias",             ctypes.c_float),
        ("localAngularVelocity",  ctypes.c_float * 3),
        ("finalFF",               ctypes.c_float),
        ("performanceMeter2",     ctypes.c_float),
        ("engineLifeLeft",        ctypes.c_int),
        ("tripXspeedometer",      ctypes.c_float),
        ("waterTemp",             ctypes.c_float),
        ("brakePressure",         ctypes.c_float * 4),
        ("frontBrakeCompound",    ctypes.c_int),
        ("rearBrakeCompound",     ctypes.c_int),
        ("padLife",               ctypes.c_float * 4),
        ("discLife",              ctypes.c_float * 4),
        ("ignitionOn",            ctypes.c_int),
        ("starterEngineOn",       ctypes.c_int),
        ("isEngineRunning",       ctypes.c_int),
        ("kerbVibration",         ctypes.c_float),
        ("slipVibrations",        ctypes.c_float),
        ("gVibrations",           ctypes.c_float),
        ("absVibrations",         ctypes.c_float),
    ]


class Graphics(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("packetId",                ctypes.c_int),
        ("status",                  ctypes.c_int),
        ("session",                 ctypes.c_int),
        ("currentTime",             ctypes.c_wchar * 15),
        ("lastTime",                ctypes.c_wchar * 15),
        ("bestTime",                ctypes.c_wchar * 15),
        ("split",                   ctypes.c_wchar * 15),
        ("completedLaps",           ctypes.c_int),
        ("position",                ctypes.c_int),
        ("iCurrentTime",            ctypes.c_int),
        ("iLastTime",               ctypes.c_int),
        ("iBestTime",               ctypes.c_int),
        ("sessionTimeLeft",         ctypes.c_float),
        ("distanceTraveled",        ctypes.c_float),
        ("isInPit",                 ctypes.c_int),
        ("currentSectorIndex",      ctypes.c_int),
        ("lastSectorTime",          ctypes.c_int),
        ("numberOfLaps",            ctypes.c_int),
        ("tyreCompound",            ctypes.c_wchar * 33),
        ("replayTimeMultiplier",    ctypes.c_float),
        ("normalizedCarPosition",   ctypes.c_float),
        ("activeCars",              ctypes.c_int),
        ("carCoordinates",          ctypes.c_float * 60 * 3),
        ("carID",                   ctypes.c_int * 60),
        ("playerCarID",             ctypes.c_int),
        ("penaltyTime",             ctypes.c_float),
        ("flag",                    ctypes.c_int),
        ("penalty",                 ctypes.c_int),
        ("idealLineOn",             ctypes.c_int),
        ("isInPitLane",             ctypes.c_int),
        ("surfaceGrip",             ctypes.c_float),
        ("mandatoryPitDone",        ctypes.c_int),
        ("windSpeed",               ctypes.c_float),
        ("windDirection",           ctypes.c_float),
        ("isSetupMenuVisible",      ctypes.c_int),
        ("mainDisplayIndex",        ctypes.c_int),
        ("secondaryDisplayIndex",   ctypes.c_int),
        ("TC",                      ctypes.c_int),
        ("TCCut",                   ctypes.c_int),
        ("EngineMap",               ctypes.c_int),
        ("ABS",                     ctypes.c_int),
        ("fuelXLap",                ctypes.c_float),
        ("rainLights",              ctypes.c_int),
        ("flashingLights",          ctypes.c_int),
        ("lightsStage",             ctypes.c_int),
        ("exhaustTemperature",      ctypes.c_float),
        ("wiperLV",                 ctypes.c_int),
        ("driverStintTotalTimeLeft",ctypes.c_int),
        ("driverStintTimeLeft",     ctypes.c_int),
        ("rainTyres",               ctypes.c_int),
        ("sessionIndex",            ctypes.c_int),
        ("usedFuel",                ctypes.c_float),
        ("deltaLapTime",            ctypes.c_wchar * 15),
        ("iDeltaLapTime",           ctypes.c_int),
        ("estimatedLapTime",        ctypes.c_wchar * 15),
        ("iEstimatedLapTime",       ctypes.c_int),
        ("isDeltaPositive",         ctypes.c_int),
        ("iSplit",                  ctypes.c_int),
        ("isValidLap",              ctypes.c_int),
        ("fuelEstimatedLaps",       ctypes.c_float),
        ("trackStatus",             ctypes.c_wchar * 33),
        ("missingMandatoryPits",    ctypes.c_int),
        ("Clock",                   ctypes.c_float),
        ("directionLightsLeft",     ctypes.c_int),
        ("directionLightsRight",    ctypes.c_int),
        ("globalYellow",            ctypes.c_int),
        ("globalYellow1",           ctypes.c_int),
        ("globalYellow2",           ctypes.c_int),
        ("globalWhite",             ctypes.c_int),
        ("globalGreen",             ctypes.c_int),
        ("globalChequered",         ctypes.c_int),
        ("globalRed",               ctypes.c_int),
        ("mfdTyreSet",              ctypes.c_int),
        ("mfdFuelToAdd",            ctypes.c_float),
        ("mfdTyrePressureLF",       ctypes.c_float),
        ("mfdTyrePressureRF",       ctypes.c_float),
        ("mfdTyrePressureLR",       ctypes.c_float),
        ("mfdTyrePressureRR",       ctypes.c_float),
        ("trackGripStatus",         ctypes.c_int),
        ("rainIntensity",           ctypes.c_int),
        ("rainIntensityIn10min",    ctypes.c_int),
        ("rainIntensityIn30min",    ctypes.c_int),
        ("currentTyreSet",          ctypes.c_int),
        ("strategyTyreSet",         ctypes.c_int),
        ("gapAhead",                ctypes.c_int),
        ("gapBehind",               ctypes.c_int),
    ]


class Static(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("smVersion",               ctypes.c_wchar * 15),
        ("acVersion",               ctypes.c_wchar * 15),
        ("numberOfSessions",        ctypes.c_int),
        ("numCars",                 ctypes.c_int),
        ("carModel",                ctypes.c_wchar * 33),
        ("track",                   ctypes.c_wchar * 33),
        ("playerName",              ctypes.c_wchar * 33),
        ("playerSurname",           ctypes.c_wchar * 33),
        ("playerNick",              ctypes.c_wchar * 33),
        ("sectorCount",             ctypes.c_int),
        ("maxTorque",               ctypes.c_float),
        ("maxPower",                ctypes.c_float),
        ("maxRpm",                  ctypes.c_int),
        ("maxFuel",                 ctypes.c_float),
        ("suspensionMaxTravel",     ctypes.c_float * 4),
        ("tyreRadius",              ctypes.c_float * 4),
        ("maxTurboBoost",           ctypes.c_float),
        ("deprecated_1",            ctypes.c_float),
        ("deprecated_2",            ctypes.c_float),
        ("penaltiesEnabled",        ctypes.c_int),
        ("aidFuelRate",             ctypes.c_float),
        ("aidTireRate",             ctypes.c_float),
        ("aidMechanicalDamage",     ctypes.c_float),
        ("aidAllowTyreBlankets",    ctypes.c_int),
        ("aidStability",            ctypes.c_float),
        ("aidAutoClutch",           ctypes.c_int),
        ("aidAutoBlip",             ctypes.c_int),
        ("hasDRS",                  ctypes.c_int),
        ("hasERS",                  ctypes.c_int),
        ("hasKERS",                 ctypes.c_int),
        ("kersMaxJ",                ctypes.c_float),
        ("engineBrakeSettingsCount",ctypes.c_int),
        ("ersPowerControllerCount", ctypes.c_int),
        ("trackSPlineLength",       ctypes.c_float),
        ("trackConfiguration",      ctypes.c_wchar * 33),
        ("ersMaxJ",                 ctypes.c_float),
        ("isTimedRace",             ctypes.c_int),
        ("hasFuelManagement",       ctypes.c_int),
        ("tireWearRate",            ctypes.c_float),
    ]


REGIONS = {
    "physics":  ("acpmf_physics",  Physics),
    "graphics": ("acpmf_graphics", Graphics),
    "static":   ("acpmf_static",   Static),
}

# Sessions to probe — shm_tracer found Session 1; we try common values
SESSIONS_TO_TRY = [None, 1, 2, 3, 0]   # None = use Local\ (current session)


# ─────────────────────────────────────────────────────────────────────────────
# Multi-path shared memory opener
# ─────────────────────────────────────────────────────────────────────────────

class RegionReader:
    """
    Opens a named shared memory region, trying multiple session paths.
    Falls back from Local\name → Global\name → \Sessions\N\BaseNamedObjects\name
    because AC Evo may run in a different Windows session from our script.
    """

    def __init__(self, base_name: str, struct_type):
        self.base_name   = base_name
        self.struct_type = struct_type
        self.size        = ctypes.sizeof(struct_type)
        self._mm         = None
        self._path_used  = None
        self._raw_handle = None

    def connect(self) -> bool:
        # Try the standard mmap paths first (same session)
        for prefix in ("Local\\", "Global\\"):
            tag = prefix + self.base_name
            try:
                mm = mmap.mmap(-1, self.size, tagname=tag, access=mmap.ACCESS_READ)
                self._mm        = mm
                self._path_used = tag
                return True
            except Exception:
                pass

        # Try explicit session paths via OpenFileMappingW with full NT name
        # This works when AC Evo is in session 1 but our script runs in session 0
        FILE_MAP_READ = 4
        for session in SESSIONS_TO_TRY:
            if session is None:
                continue
            full_name = f"\\Sessions\\{session}\\BaseNamedObjects\\{self.base_name}"
            handle = kernel32.OpenFileMappingW(FILE_MAP_READ, False, full_name)
            if not handle:
                continue
            addr = kernel32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, self.size)
            if not addr:
                kernel32.CloseHandle(handle)
                continue
            # Wrap in a mmap-like object using ctypes directly
            self._raw_handle = handle
            self._raw_addr   = addr
            self._path_used  = full_name
            return True

        return False

    def read_raw(self) -> bytes:
        if self._mm is not None:
            self._mm.seek(0)
            return self._mm.read(self.size)
        elif hasattr(self, "_raw_addr") and self._raw_addr:
            return (ctypes.c_char * self.size).from_address(self._raw_addr).raw
        raise RuntimeError(f"Region {self.base_name} not connected")

    def read_struct(self):
        raw = self.read_raw()
        return self.struct_type.from_buffer_copy(raw)

    def close(self):
        if self._mm:
            try:
                self._mm.close()
            except Exception:
                pass
        if hasattr(self, "_raw_addr") and self._raw_addr:
            kernel32.UnmapViewOfFile(self._raw_addr)
        if self._raw_handle:
            kernel32.CloseHandle(self._raw_handle)


# ─────────────────────────────────────────────────────────────────────────────
# Struct → dict (handles arrays, nested arrays, strings)
# ─────────────────────────────────────────────────────────────────────────────

def _to_python(val):
    if isinstance(val, float):
        return round(val, 6)
    if isinstance(val, ctypes.Array):
        items = [_to_python(val[i]) for i in range(len(val))]
        # Flatten nested arrays one level (e.g. tyreContactPoint[4][3])
        if items and isinstance(items[0], list):
            return items
        return items
    return val


def struct_to_dict(s) -> dict:
    out = {}
    for name, *_ in s._fields_:
        val = getattr(s, name)
        out[name] = _to_python(val)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Raw scan: also dump the region as raw floats to catch unknown new fields
# ─────────────────────────────────────────────────────────────────────────────

def raw_floats(data: bytes) -> list:
    """Return every 4-byte aligned value as float32. Useful for unknown fields."""
    out = []
    for i in range(0, len(data) - 3, 4):
        val = struct.unpack_from("<f", data, i)[0]
        out.append(round(val, 6))
    return out


def raw_ints(data: bytes) -> list:
    out = []
    for i in range(0, len(data) - 3, 4):
        val = struct.unpack_from("<i", data, i)[0]
        out.append(val)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Main capture loop
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AC Evo full shared memory capture")
    parser.add_argument("--hz",    type=float, default=60,  help="Poll rate (default 60)")
    parser.add_argument("--out",   type=str,   default="",  help="Output .jsonl path")
    parser.add_argument("--raw",   action="store_true",     help="Also include raw float32/int32 arrays in each frame")
    parser.add_argument("--wait",  action="store_true",     help="Wait for regions to appear instead of failing immediately")
    args = parser.parse_args()

    interval = 1.0 / max(1, args.hz)
    outpath  = args.out or f"ac_evo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"

    # ── Open regions ──────────────────────────────────────────────────────────
    readers = {}
    print("Connecting to AC Evo shared memory regions…\n")

    for key, (base_name, struct_type) in REGIONS.items():
        r = RegionReader(base_name, struct_type)
        if r.connect():
            readers[key] = r
            print(f"  ✅  {key:<10}  {r._path_used}")
        else:
            print(f"  ❌  {key:<10}  not found")

    if not readers:
        if args.wait:
            print("\nNo regions found. Waiting for game to start (Ctrl+C to abort)…")
            while not readers:
                time.sleep(2)
                for key, (base_name, struct_type) in REGIONS.items():
                    if key not in readers:
                        r = RegionReader(base_name, struct_type)
                        if r.connect():
                            readers[key] = r
                            print(f"  ✅  {key:<10}  {r._path_used}")
        else:
            print("\nNo shared memory regions found.")
            print("Make sure AC Evo is running and you are in a session.")
            print("Tip: run with --wait to poll until the game starts.")
            sys.exit(1)

    print(f"\n  NOTE: acpmf_physics {'found' if 'physics' in readers else 'NOT FOUND — AC Evo may not expose this region yet'}")
    print(f"\nCapturing {len(readers)} region(s) at {args.hz:.0f} Hz → {outpath}")
    print("Ctrl+C to stop.\n")

    # ── Read static once ──────────────────────────────────────────────────────
    static_dict = {}
    if "static" in readers:
        try:
            static_dict = struct_to_dict(readers["static"].read_struct())
        except Exception as e:
            print(f"  Warning: could not read static region: {e}")

    # ── Write meta record ─────────────────────────────────────────────────────
    running = True
    def _stop(sig, frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT,  _stop)
    signal.signal(signal.SIGTERM, _stop)

    frames = 0
    last_packet = {}

    with open(outpath, "w", encoding="utf-8") as f:

        meta = {
            "_record_type": "meta",
            "_captured_at": datetime.now(timezone.utc).isoformat(),
            "_hz":          args.hz,
            "_regions_found": list(readers.keys()),
            "_region_paths":  {k: r._path_used for k, r in readers.items()},
            "_region_sizes":  {k: r.size for k, r in readers.items()},
            "static": static_dict,
        }
        f.write(json.dumps(meta) + "\n")

        while running:
            t0 = time.perf_counter()

            frame_data = {
                "_record_type": "frame",
                "_ts":          datetime.now(timezone.utc).isoformat(),
                "_wall_ns":     time.time_ns(),
            }

            skip = True   # will be cleared if any region has new data

            for key, reader in readers.items():
                try:
                    raw = reader.read_raw()
                except Exception as e:
                    frame_data[key] = {"_error": str(e)}
                    continue

                s = reader.struct_type.from_buffer_copy(raw)

                # Dedup: skip if physics/graphics packetId hasn't changed
                if key in ("physics", "graphics"):
                    pid = getattr(s, "packetId", None)
                    if pid is not None:
                        if last_packet.get(key) == pid:
                            frame_data[key] = {"_unchanged": True, "packetId": pid}
                            continue
                        last_packet[key] = pid
                    skip = False
                else:
                    skip = False

                d = struct_to_dict(s)
                frame_data[key] = d

                # Optionally attach the raw interpretation alongside the struct
                if args.raw:
                    frame_data[f"{key}_raw_f32"] = raw_floats(raw)
                    frame_data[f"{key}_raw_i32"] = raw_ints(raw)

            # Always write the frame even if unchanged (so timeline is intact)
            f.write(json.dumps(frame_data) + "\n")
            frames += 1

            # Heartbeat every 5 seconds
            if frames % max(1, int(args.hz * 5)) == 0:
                f.flush()
                # Show most interesting live value we have
                status_str = ""
                if "graphics" in frame_data and isinstance(frame_data["graphics"], dict):
                    g = frame_data["graphics"]
                    status_map = {0: "OFF", 1: "REPLAY", 2: "LIVE", 3: "PAUSED"}
                    status_str = status_map.get(g.get("status", -1), "?")
                    lap    = g.get("completedLaps", "?")
                    pos    = g.get("normalizedCarPosition", 0)
                    status_str = f"[{status_str}] lap={lap} pos={pos:.2f}"
                if "physics" in frame_data and isinstance(frame_data["physics"], dict):
                    p = frame_data["physics"]
                    spd = p.get("speedKmh", 0)
                    rpm = p.get("rpms", 0)
                    status_str += f"  {spd:.1f}km/h  {rpm}rpm"
                print(f"  frame {frames:>7}  {status_str}")

            elapsed = time.perf_counter() - t0
            time.sleep(max(0, interval - elapsed))

    # Close all readers
    for r in readers.values():
        r.close()

    print(f"\nDone. {frames} frames written to {os.path.abspath(outpath)}")
    print(f"Tip: use ac_evo_inspect.py to explore the file:")
    print(f"  python ac_evo_inspect.py {outpath} --fields")
    print(f"  python ac_evo_inspect.py {outpath} --stats")


if __name__ == "__main__":
    main()
