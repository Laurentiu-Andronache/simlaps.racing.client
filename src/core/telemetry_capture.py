"""
Telemetry Capture Module

Manages shared memory capture from AC Evo during game sessions.
Based on test_scripts/telemetry/1-capture.py
"""

import asyncio
import ctypes.wintypes
import json
import os
import signal
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

if sys.platform == "win32":
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenFileMappingW.restype = ctypes.wintypes.HANDLE
    kernel32.MapViewOfFile.restype = ctypes.c_void_p
    kernel32.UnmapViewOfFile.restype = ctypes.c_bool
    kernel32.CloseHandle.restype = ctypes.c_bool

FILE_MAP_READ = 0x0004

SESSIONS = [None, 1, 2, 3, 0]

REGIONS = {
    "physics": ("acpmf_physics", 1024),
    "graphics": ("acpmf_graphics", 2048),
    "static": ("acpmf_static", 2048),
}


@dataclass
class FrameData:
    """Single telemetry frame data."""
    timestamp: str
    frame_number: int
    physics: Dict[str, Any]
    graphics: Dict[str, Any]
    static: Dict[str, Any]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CaptureMetadata:
    """Metadata about the capture session."""
    captured_at: str
    hz: float
    regions_found: List[str]
    region_names: Dict[str, str]
    region_sizes: Dict[str, int]

    def to_dict(self) -> dict:
        return asdict(self)


class RegionReader:
    """Reads a single shared memory region."""

    def __init__(self, name: str, size: int):
        self.name = name
        self.size = size
        self._handle = None
        self._addr = None
        self._path_used = None

    def open(self) -> bool:
        if sys.platform != "win32":
            return False

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
            except Exception:
                pass
        if self._handle:
            try:
                kernel32.CloseHandle(self._handle)
            except Exception:
                pass
        self._addr = None
        self._handle = None


class TelemetryCapture:
    """Manages telemetry capture during game sessions."""

    def __init__(self, hz: float = 20.0):
        self._hz = hz
        self._frames: List[FrameData] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._readers: Dict[str, RegionReader] = {}
        self._interval = 1.0 / max(hz, 1.0)
        self._metadata: Optional[CaptureMetadata] = None
        self._session_start_time: Optional[datetime] = None
        self._region_paths: Dict[str, str] = {}

    def is_capturing(self) -> bool:
        """Check if currently capturing."""
        return self._running

    def get_frame_count(self) -> int:
        """Get number of captured frames."""
        return len(self._frames)

    def get_frames(self) -> List[FrameData]:
        """Get captured frames."""
        return self._frames.copy()

    def get_metadata(self) -> Optional[CaptureMetadata]:
        """Get capture metadata."""
        return self._metadata

    def _connect_regions(self) -> Dict[str, RegionReader]:
        """Connect to all shared memory regions."""
        readers = {}
        for key, (region_name, size) in REGIONS.items():
            reader = RegionReader(region_name, size)
            if reader.open():
                readers[key] = reader
                self._region_paths[key] = reader._path_used or ""
        return readers

    def _reconnect_missing(self, readers: Dict[str, RegionReader]):
        """Try to reconnect to missing regions."""
        for key, (region_name, size) in REGIONS.items():
            if key in readers:
                continue
            reader = RegionReader(region_name, size)
            if reader.open():
                readers[key] = reader
                self._region_paths[key] = reader._path_used or ""

    def _capture_frame(self, frame_num: int) -> Optional[FrameData]:
        """Capture a single frame from shared memory."""
        from .telemetry_decoder import decode_physics, decode_graphics, decode_static

        frame = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "frame_number": frame_num,
            "physics": {},
            "graphics": {},
            "static": {},
        }

        disconnected = []
        for key, reader in list(self._readers.items()):
            try:
                raw = reader.read_raw()
            except Exception:
                disconnected.append(key)
                continue

            if key == "physics":
                frame["physics"] = decode_physics(raw)
            elif key == "graphics":
                frame["graphics"] = decode_graphics(raw)
            elif key == "static":
                frame["static"] = decode_static(raw)

        for key in disconnected:
            try:
                self._readers[key].close()
            except Exception:
                pass
            self._readers.pop(key, None)

        return FrameData(**frame)

    async def start_capture(self) -> bool:
        """Start capturing telemetry frames.

        Returns:
            True if capture started successfully, False otherwise
        """
        if self._running:
            return True

        print("[TELEMETRY] Starting telemetry capture...")
        self._readers = self._connect_regions()

        if not self._readers:
            print("[TELEMETRY] No shared memory regions found. Is the game running?")
            return False

        self._running = True
        self._frames = []
        self._session_start_time = datetime.now(timezone.utc)

        self._metadata = CaptureMetadata(
            captured_at=self._session_start_time.isoformat(),
            hz=self._hz,
            regions_found=list(self._readers.keys()),
            region_names={k: REGIONS[k][0] for k in self._readers},
            region_sizes={k: v.size for k, v in self._readers.items()},
        )

        print(f"[TELEMETRY] Capturing {len(self._readers)} region(s) at {self._hz:.0f} Hz")
        return True

    async def _capture_loop(self):
        """Main capture loop."""
        frame_num = 0
        next_deadline = time.perf_counter()
        next_reconnect = time.perf_counter()

        while self._running:
            now_mono = time.perf_counter()

            if now_mono >= next_reconnect:
                self._reconnect_missing(self._readers)
                next_reconnect = now_mono + 0.2

            frame = self._capture_frame(frame_num)
            if frame:
                self._frames.append(frame)
                frame_num += 1

            if frame_num % int(self._hz * 5) == 0:
                print(f"[TELEMETRY] Captured {frame_num} frames...")

            next_deadline += self._interval
            sleep_for = next_deadline - time.perf_counter()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            else:
                next_deadline = time.perf_counter()

    async def stop_capture(self) -> List[FrameData]:
        """Stop capturing and return captured frames.

        Returns:
            List of captured frames
        """
        if not self._running:
            return self._frames.copy()

        print(f"[TELEMETRY] Stopping capture. Total frames: {len(self._frames)}")
        self._running = False

        for reader in self._readers.values():
            reader.close()
        self._readers = {}

        return self._frames.copy()

    def export_to_jsonl(self, path: str) -> bool:
        """Export captured frames to JSONL file for debugging.

        Args:
            path: Output file path

        Returns:
            True if export successful
        """
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                if self._metadata:
                    meta_dict = self._metadata.to_dict()
                    meta_dict["_record_type"] = "meta"
                    f.write(json.dumps(meta_dict) + "\n")

                for frame in self._frames:
                    frame_dict = frame.to_dict()
                    frame_dict["_record_type"] = "frame"
                    f.write(json.dumps(frame_dict) + "\n")

            print(f"[TELEMETRY] Exported {len(self._frames)} frames to {path}")
            return True
        except Exception as e:
            print(f"[TELEMETRY] Export failed: {e}")
            return False

    def clear(self):
        """Clear captured frames to free memory."""
        self._frames = []
        self._metadata = None


def get_default_output_dir() -> str:
    """Get the default telemetry output directory."""
    if sys.platform == "win32":
        base = os.environ.get("USERPROFILE", str(Path.home()))
        return os.path.join(base, "Documents", "SimLaps", "Telemetry")
    else:
        return str(Path.home() / "SimLaps" / "Telemetry")
