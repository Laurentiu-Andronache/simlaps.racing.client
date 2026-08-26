"""
Comprehensive tests for telemetry capture with mock shared memory.

Tests shared memory region reading, capture loop, error handling, and metadata.
"""

import ctypes
import struct
from pathlib import Path

import pytest
from unittest.mock import Mock, MagicMock, patch
from src.core.telemetry_capture import (
    RegionReader,
    TelemetryCapture,
    REGIONS,
    FrameData,
    CaptureMetadata,
    MAX_DEBUG_MAPPING_BYTES,
    MEM_COMMIT,
    PAGE_NOACCESS,
    _MemoryBasicInformation,
    _discover_readable_mapping_size,
)
from src.models import SharedSessionManager
from datetime import datetime, timezone


def _graphics_lap_buffer(*, current_lap_time_ms: int, total_lap_count: int,
                         last_laptime_ms: int, is_valid_lap: bool) -> bytes:
    """Build a graphics mapping with the stable live-lap fields populated."""
    from src.core.telemetry_decoder import (
        _PEEK_CURRENT_LAP_TIME,
        _PEEK_TOTAL_LAP_COUNT,
        _PEEK_LAST_LAPTIME,
        _PEEK_IS_VALID_LAP,
    )

    data = bytearray(b"\x00" * REGIONS["graphics"][1])
    struct.pack_into("<i", data, 4, 2)  # AC_LIVE, not mapping teardown
    struct.pack_into("<i", data, _PEEK_CURRENT_LAP_TIME, current_lap_time_ms)
    struct.pack_into("<i", data, _PEEK_TOTAL_LAP_COUNT, total_lap_count)
    struct.pack_into("<i", data, _PEEK_LAST_LAPTIME, last_laptime_ms)
    data[_PEEK_IS_VALID_LAP] = int(is_valid_lap)
    return bytes(data)


class TestRegionReader:
    """Test shared memory region reader."""

    def test_region_reader_initialization(self):
        """Test region reader initialization."""
        reader = RegionReader("test_region", 1024)
        
        assert reader.name == "test_region"
        assert reader.size == 1024
        assert reader._handle is None
        assert reader._view is None
        assert reader._path_used is None

    def test_mapping_size_discovery_is_bounded_by_hard_limit(self):
        """A large mapped allocation must never enlarge a debug read past 64 KiB."""
        view = 0x100000
        calls = []

        def virtual_query(address, info_pointer, _info_size):
            calls.append(address.value)
            info = ctypes.cast(
                info_pointer, ctypes.POINTER(_MemoryBasicInformation)
            ).contents
            info.BaseAddress = view
            info.AllocationBase = view
            info.RegionSize = MAX_DEBUG_MAPPING_BYTES * 2
            info.State = MEM_COMMIT
            info.Protect = 0x02  # PAGE_READONLY
            return ctypes.sizeof(_MemoryBasicInformation)

        assert _discover_readable_mapping_size(
            view,
            virtual_query=virtual_query,
        ) == MAX_DEBUG_MAPPING_BYTES
        assert calls == [view]

    def test_mapping_size_discovery_never_allows_a_limit_above_safety_ceiling(self):
        """The probe's 64 KiB ceiling cannot be bypassed by its argument."""
        view = 0x180000

        def virtual_query(address, info_pointer, _info_size):
            info = ctypes.cast(
                info_pointer, ctypes.POINTER(_MemoryBasicInformation)
            ).contents
            info.BaseAddress = address.value
            info.AllocationBase = view
            info.RegionSize = MAX_DEBUG_MAPPING_BYTES * 2
            info.State = MEM_COMMIT
            info.Protect = 0x02  # PAGE_READONLY
            return ctypes.sizeof(_MemoryBasicInformation)

        assert _discover_readable_mapping_size(
            view,
            hard_limit=MAX_DEBUG_MAPPING_BYTES * 2,
            virtual_query=virtual_query,
        ) == MAX_DEBUG_MAPPING_BYTES

    def test_mapping_size_discovery_stops_at_unreadable_region(self):
        """Discovery only includes contiguous committed readable pages."""
        view = 0x200000

        def virtual_query(address, info_pointer, _info_size):
            info = ctypes.cast(
                info_pointer, ctypes.POINTER(_MemoryBasicInformation)
            ).contents
            info.BaseAddress = address.value
            info.AllocationBase = view
            info.RegionSize = 4096
            info.State = MEM_COMMIT
            info.Protect = 0x02 if address.value == view else PAGE_NOACCESS
            return ctypes.sizeof(_MemoryBasicInformation)

        assert _discover_readable_mapping_size(
            view,
            virtual_query=virtual_query,
        ) == 4096

    @patch('src.core.telemetry_capture.kernel32')
    def test_region_reader_open_success(self, mock_kernel32):
        """Test successful region reader open."""
        # Mock successful open
        mock_handle = MagicMock()
        mock_kernel32.OpenFileMappingW.return_value = mock_handle
        mock_kernel32.MapViewOfFile.return_value = MagicMock()
        
        reader = RegionReader("test_region", 1024)
        result = reader.open()
        
        assert result is True
        assert reader._handle == mock_handle
        assert reader._view is not None

    @patch('src.core.telemetry_capture.kernel32')
    def test_debug_reader_maps_whole_section_and_uses_discovered_size(
        self, mock_kernel32
    ):
        """Opt-in probing maps length zero and retains the bounded result."""
        mock_kernel32.OpenFileMappingW.return_value = 123
        mock_kernel32.MapViewOfFile.return_value = 0x300000
        discover = Mock(return_value=8192)

        reader = RegionReader(
            "test_region",
            1024,
            probe_full_mapping=True,
            size_discoverer=discover,
        )

        assert reader.open() is True
        mock_kernel32.MapViewOfFile.assert_called_once_with(
            123, 0x0004, 0, 0, 0
        )
        discover.assert_called_once_with(
            0x300000,
            hard_limit=MAX_DEBUG_MAPPING_BYTES,
        )
        assert reader.configured_size == 1024
        assert reader.discovered_size == 8192
        assert reader.size == 8192
        with patch(
            "src.core.telemetry_capture.ctypes.string_at",
            return_value=b"\x00" * 8192,
        ) as read:
            assert len(reader.read_raw()) == 8192
        read.assert_called_once_with(0x300000, 8192)

    @patch('src.core.telemetry_capture.kernel32')
    def test_debug_reader_falls_back_when_size_discovery_fails(
        self, mock_kernel32
    ):
        """A failed probe reopens the established fixed-size read window."""
        mock_kernel32.OpenFileMappingW.return_value = 123
        mock_kernel32.MapViewOfFile.side_effect = [0x300000, 0x310000]
        discover = Mock(return_value=None)

        reader = RegionReader(
            "test_region",
            1024,
            probe_full_mapping=True,
            size_discoverer=discover,
        )

        assert reader.open() is True
        requested_sizes = [
            call.args[-1]
            for call in mock_kernel32.MapViewOfFile.call_args_list
        ]
        assert requested_sizes == [0, 1024]
        mock_kernel32.UnmapViewOfFile.assert_called_once_with(0x300000)
        assert reader.discovered_size is None
        assert reader.size == 1024

    @patch('src.core.telemetry_capture.kernel32')
    def test_debug_reader_rejects_discovery_below_decoder_minimum(
        self, mock_kernel32
    ):
        """A short discovered view cannot replace the known decoder window."""
        mock_kernel32.OpenFileMappingW.return_value = 123
        mock_kernel32.MapViewOfFile.side_effect = [0x300000, 0x310000]

        reader = RegionReader(
            "test_region",
            1024,
            probe_full_mapping=True,
            size_discoverer=Mock(return_value=512),
        )

        assert reader.open() is True
        assert reader.discovered_size is None
        assert reader.size == 1024
        mock_kernel32.UnmapViewOfFile.assert_called_once_with(0x300000)

    @patch('src.core.telemetry_capture.kernel32')
    def test_debug_reader_clamps_untrusted_discovery_result(
        self, mock_kernel32
    ):
        """The reader enforces its ceiling even with an injected bad probe."""
        mock_kernel32.OpenFileMappingW.return_value = 123
        mock_kernel32.MapViewOfFile.return_value = 0x300000

        reader = RegionReader(
            "test_region",
            1024,
            probe_full_mapping=True,
            size_discoverer=Mock(return_value=MAX_DEBUG_MAPPING_BYTES * 2),
        )

        assert reader.open() is True
        assert reader.discovered_size == MAX_DEBUG_MAPPING_BYTES
        assert reader.size == MAX_DEBUG_MAPPING_BYTES

    @patch('src.core.telemetry_capture.kernel32')
    def test_debug_reader_falls_back_when_full_view_cannot_be_mapped(
        self, mock_kernel32
    ):
        """A zero-length map failure must not break ordinary capture."""
        mock_kernel32.OpenFileMappingW.return_value = 123
        mock_kernel32.MapViewOfFile.side_effect = [0, 0x310000]
        discover = Mock(side_effect=AssertionError("fixed fallback is not probed"))

        reader = RegionReader(
            "test_region",
            1024,
            probe_full_mapping=True,
            size_discoverer=discover,
        )

        assert reader.open() is True
        requested_sizes = [
            call.args[-1]
            for call in mock_kernel32.MapViewOfFile.call_args_list
        ]
        assert requested_sizes == [0, 1024]
        discover.assert_not_called()
        assert reader.discovered_size is None
        assert reader.size == 1024

    @patch('src.core.telemetry_capture.kernel32')
    def test_normal_reader_keeps_fixed_mapping_without_probe(self, mock_kernel32):
        """Normal capture preserves its fixed-size map and has no query overhead."""
        mock_kernel32.OpenFileMappingW.return_value = 123
        mock_kernel32.MapViewOfFile.return_value = 0x310000
        discover = Mock(side_effect=AssertionError("normal capture must not probe"))

        reader = RegionReader(
            "test_region",
            1024,
            size_discoverer=discover,
        )

        assert reader.open() is True
        mock_kernel32.MapViewOfFile.assert_called_once_with(
            123, 0x0004, 0, 0, 1024
        )
        discover.assert_not_called()
        assert reader.discovered_size is None
        assert reader.size == 1024

    @patch('src.core.telemetry_capture.kernel32')
    def test_region_reader_open_failure(self, mock_kernel32):
        """Test region reader open failure."""
        # Mock failed open
        mock_kernel32.OpenFileMappingW.return_value = 0
        
        reader = RegionReader("test_region", 1024)
        result = reader.open()
        
        assert result is False
        assert reader._handle is None

    @patch('src.core.telemetry_capture.kernel32')
    @patch('src.core.telemetry_capture.ctypes')
    def test_region_reader_read_raw(self, mock_ctypes, mock_kernel32):
        """Test reading raw bytes from region."""
        # Mock successful open and read
        mock_handle = MagicMock()
        mock_view = MagicMock()
        mock_kernel32.OpenFileMappingW.return_value = mock_handle
        mock_kernel32.MapViewOfFile.return_value = mock_view
        
        test_data = b'\x00\x01\x02\x03' * 256  # 1024 bytes
        mock_ctypes.string_at.return_value = test_data
        
        reader = RegionReader("test_region", 1024)
        reader.open()
        result = reader.read_raw()
        
        assert result == test_data
        assert len(result) == 1024

    @patch('src.core.telemetry_capture.kernel32')
    def test_region_reader_read_raw_not_open(self, mock_kernel32):
        """Test reading from unopened region raises error."""
        reader = RegionReader("test_region", 1024)
        
        with pytest.raises(RuntimeError):
            reader.read_raw()

    @patch('src.core.telemetry_capture.kernel32')
    def test_region_reader_close(self, mock_kernel32):
        """Test closing region reader."""
        mock_handle = MagicMock()
        mock_view = MagicMock()
        mock_kernel32.OpenFileMappingW.return_value = mock_handle
        mock_kernel32.MapViewOfFile.return_value = mock_view
        
        reader = RegionReader("test_region", 1024)
        reader.open()
        reader.close()
        
        assert reader._handle is None
        assert reader._view is None
        mock_kernel32.UnmapViewOfFile.assert_called_once()
        mock_kernel32.CloseHandle.assert_called_once()


class TestTelemetryCapture:
    """Test telemetry capture system."""

    def test_capture_initialization(self):
        """Test telemetry capture initialization."""
        capture = TelemetryCapture(hz=10.0)
        
        assert capture._hz == 10.0
        assert capture._frames == []
        assert capture._running is False
        assert capture._readers == {}
        assert capture._lap_boundaries == []  # Empty list is fine, tuples only added when recording

    def test_capture_frame_count(self):
        """Test getting frame count."""
        capture = TelemetryCapture(hz=10.0)
        
        assert capture.get_frame_count() == 0

    def test_capture_is_capturing(self):
        """Test checking if capturing."""
        capture = TelemetryCapture(hz=10.0)
        
        assert capture.is_capturing() is False

    def test_capture_get_stop_reason(self):
        """Test getting stop reason."""
        capture = TelemetryCapture(hz=10.0)
        
        assert capture.get_stop_reason() is None

    def test_capture_get_output_prefix(self):
        """Test getting output prefix."""
        capture = TelemetryCapture(hz=10.0)
        
        assert capture.get_output_prefix() is None

    @patch('src.core.telemetry_capture.kernel32')
    def test_capture_frame_decoding(self, mock_kernel32):
        """Test single frame capture and decoding."""
        # Mock region reader
        mock_reader = MagicMock()
        mock_reader.size = 1024
        mock_reader.read_raw.return_value = b'\x00' * 1024
        
        capture = TelemetryCapture(hz=10.0)
        capture._readers = {"physics": mock_reader}
        
        frame = capture._capture_frame(0)
        
        assert frame is not None
        assert frame.frame_number == 0
        assert frame.physics is not None or frame.physics == {}

    @patch('src.core.telemetry_capture.kernel32')
    def test_capture_frame_prunes_raw_when_debug_logs_off(self, mock_kernel32):
        """Raw hex blobs are dropped from in-memory frames when debug logs disabled."""
        mock_reader = MagicMock()
        mock_reader.size = 1024
        mock_reader.read_raw.return_value = b'\x01' * 1024

        capture = TelemetryCapture(hz=10.0, debug_logs=False)
        capture._readers = {"physics": mock_reader}

        frame = capture._capture_frame(0)

        assert frame is not None
        assert frame.physics_raw is None
        assert frame.graphics_raw is None
        assert frame.static_raw is None

    @patch('src.core.telemetry_capture.kernel32')
    def test_capture_frame_keeps_raw_when_debug_logs_on(self, mock_kernel32):
        """Raw hex blobs are retained when debug logs are enabled."""
        mock_reader = MagicMock()
        mock_reader.size = 1024
        mock_reader.read_raw.return_value = b'\x01' * 1024

        capture = TelemetryCapture(hz=10.0, debug_logs=True)
        capture._readers = {"physics": mock_reader}

        frame = capture._capture_frame(0)

        assert frame is not None
        assert frame.physics_raw == (b'\x01' * 1024).hex()

    @patch('src.core.telemetry_capture.decode_static')
    @patch('src.core.telemetry_capture.decode_graphics')
    @patch('src.core.telemetry_capture.decode_physics')
    def test_capture_frame_updates_shared_session_manager(
        self,
        mock_decode_physics,
        mock_decode_graphics,
        mock_decode_static,
    ):
        """Decoded SHM frame data is forwarded into the shared session manager."""
        mock_decode_physics.return_value = {"speed_kmh": 255.0}
        mock_decode_graphics.return_value = {
            "status_name": "AC_LIVE",
            "session_current_lap": 4,
            "current_lap_time_ms": 70000,
            "last_laptime_ms": 121111,
            "best_laptime_ms": 120000,
            "ideal_laptime_ms": 119900,
            "delta_time_ms": -50,
            "is_invalid": True,
            "fuel_liter_current_quantity": 18.5,
            "fuel_liter_per_km": 2.2,
            "km_per_fuel_liter": 0.45,
            "total_lap_count": 10,
            "session_phase": "RACE",
            "session_time_left_ms": 300000,
            "current_pos": 2,
            "total_drivers": 18,
        }
        mock_decode_static.return_value = {
            "ac_evo_version": "0.9.3",
            "session": 2,
            "track": "spa_francorchamps",
            "is_online": True,
        }

        manager = SharedSessionManager()
        capture = TelemetryCapture(hz=10.0, session_manager=manager)

        physics_reader = MagicMock()
        physics_reader.size = 1024
        physics_reader.read_raw.return_value = b"\x00" * 1024
        graphics_reader = MagicMock()
        graphics_reader.size = 4096
        graphics_reader.read_raw.return_value = b"\x00" * 4096
        static_reader = MagicMock()
        static_reader.size = 2048
        static_reader.read_raw.return_value = b"\x00" * 2048

        capture._readers = {
            "physics": physics_reader,
            "graphics": graphics_reader,
            "static": static_reader,
        }

        frame = capture._capture_frame(1)
        assert frame is not None

        # SHM validity flags are now wired into shared session.
        validity = manager.get_lap_validity_data(4)
        assert validity is not None
        assert validity.is_valid is False
        assert validity.lap_state == "INVALID_GAME"
        assert validity.source == "shm_graphics"

        timing = manager.get_lap_timing_data(4)
        assert timing is not None
        assert timing.last_lap_time_ms == 121111

        fuel = manager.get_fuel_data()
        assert fuel.current_fuel == 18.5

        metadata = manager.get_session_metadata_data()
        assert metadata.game_version == "0.9.3"
        assert metadata.track == "spa_francorchamps"

    def test_capture_frame_fallback_preserves_peek_lap_state(self):
        """A rejected full decode must not erase the live peek state."""
        manager = SharedSessionManager()
        capture = TelemetryCapture(hz=10.0, session_manager=manager)
        graphics_reader = MagicMock()
        graphics_reader.size = REGIONS["graphics"][1]
        graphics_reader.read_raw.side_effect = [
            _graphics_lap_buffer(
                current_lap_time_ms=70000,
                total_lap_count=0,
                last_laptime_ms=0,
                is_valid_lap=False,
            ),
            _graphics_lap_buffer(
                current_lap_time_ms=100,
                total_lap_count=1,
                last_laptime_ms=70000,
                is_valid_lap=True,
            ),
        ]
        capture._readers = {"graphics": graphics_reader}

        first = capture._capture_frame(0)
        second = capture._capture_frame(1)

        assert first.graphics["_decoder"] == "fallback"
        assert second.graphics["_decoder"] == "fallback"
        assert manager.get_current_lap_time() == 100
        assert manager.get_lap_validity_data(1).is_valid is False

        completions = manager.get_lap_completions_after(0.0)
        assert len(completions) == 1
        assert completions[0].lap_time_ms == 70000
        assert completions[0].is_valid is False

    def test_capture_frame_full_graphics_decode_still_updates_rich_fields(self):
        """A valid full graphics decode retains its non-peek fields."""
        fixture = Path(__file__).parent / "fixtures" / "ac_evo_graphics_frame.txt"
        if not fixture.exists():
            pytest.skip(f"fixture {fixture} not present")

        manager = SharedSessionManager()
        capture = TelemetryCapture(hz=10.0, session_manager=manager)
        graphics_reader = MagicMock()
        graphics_reader.size = REGIONS["graphics"][1]
        graphics_reader.read_raw.return_value = bytes.fromhex(fixture.read_text().strip())
        capture._readers = {"graphics": graphics_reader}

        frame = capture._capture_frame(0)

        assert frame.graphics["_decoder"] == "ac_evo_graphics"
        assert frame.graphics["npos"] > 0.001
        timing = manager.get_lap_timing_data(1)
        assert timing is not None
        assert timing.current_lap_time_ms == frame.graphics["current_lap_time_ms"]
        assert manager.get_fuel_data().current_fuel == frame.graphics[
            "fuel_liter_current_quantity"
        ]

    def test_capture_lap_boundary_recording(self):
        """Test recording lap boundaries."""
        capture = TelemetryCapture(hz=10.0)
        
        # Add some frames
        capture._frames = [
            FrameData(timestamp="2024-01-01T00:00:00Z", frame_number=i, physics={})
            for i in range(10)
        ]
        
        capture.record_lap_boundary(123456, 7)
        
        assert len(capture.get_lap_boundaries()) == 1
        assert capture.get_lap_boundaries()[0][0] == 9  # Last frame index
        assert capture.get_lap_boundaries()[0][1:] == (123456, 7, "VALID")

    def test_capture_get_lap_boundaries(self):
        """Test getting lap boundaries."""
        capture = TelemetryCapture(hz=10.0)
        capture._lap_boundaries = [(10, None, None), (20, None, None), (30, None, None)]
        
        boundaries = capture.get_lap_boundaries()
        
        assert boundaries == [(10, None, None), (20, None, None), (30, None, None)]

    def test_capture_metadata_creation(self):
        """Test capture metadata creation."""
        mock_reader = MagicMock()
        mock_reader.size = 1024
        
        capture = TelemetryCapture(hz=10.0)
        capture._readers = {"physics": mock_reader}
        capture._region_paths = {"physics": "Local\\acevo_pmf_physics"}
        capture._session_start_time = datetime.now(timezone.utc)
        
        meta = capture._build_compat_meta_record()
        
        assert meta["_record_type"] == "meta"
        assert meta["_hz"] == 10.0
        assert "physics" in meta["_regions_known"]

    def test_capture_frame_record_creation(self):
        """Test frame record creation for compatibility."""
        frame = FrameData(
            timestamp="2024-01-01T00:00:00Z",
            frame_number=0,
            physics={"speed_kmh": 100.0},
            physics_raw="aabbccdd"
        )
        
        capture = TelemetryCapture(hz=10.0)
        capture._output_prefix = "test"
        
        record = capture._build_compat_frame_record(frame)
        
        assert record["_record_type"] == "frame"
        assert record["_frame"] == 1
        assert "physics" in record["regions"]
        assert record["regions"]["physics"]["size"] == 1024

    def test_capture_get_frames(self):
        """Test getting captured frames."""
        capture = TelemetryCapture(hz=10.0)
        frame = FrameData(
            timestamp="2024-01-01T00:00:00Z",
            frame_number=0,
            physics={}
        )
        capture._frames = [frame]
        
        frames = capture.get_frames()
        
        assert len(frames) == 1
        assert frames[0] == frame

    def test_capture_get_metadata(self):
        """Test getting capture metadata."""
        capture = TelemetryCapture(hz=10.0)
        meta = CaptureMetadata(
            captured_at="2024-01-01T00:00:00Z",
            hz=10.0,
            regions_found=["physics"],
            region_names={"physics": "acevo_pmf_physics"},
            region_sizes={"physics": 1024}
        )
        capture._metadata = meta
        
        result = capture.get_metadata()
        
        assert result == meta


class TestCaptureEdgeCases:
    """Test capture edge cases and error handling."""

    def test_capture_with_no_regions(self):
        """Test capture when no regions are available."""
        capture = TelemetryCapture(hz=10.0)
        capture._readers = {}
        
        frame = capture._capture_frame(0)
        
        assert frame is not None
        assert frame.frame_number == 0
        assert frame.physics == {}

    @patch('src.core.telemetry_capture.kernel32')
    def test_capture_with_read_error(self, mock_kernel32):
        """Test capture when read fails."""
        mock_reader = MagicMock()
        mock_reader.size = 1024
        mock_reader.read_raw.side_effect = Exception("Read error")
        
        capture = TelemetryCapture(hz=10.0)
        capture._readers = {"physics": mock_reader}
        
        frame = capture._capture_frame(0)
        
        assert frame is not None
        # Reader should be removed on error
        assert "physics" not in capture._readers

    @patch('src.core.telemetry_capture.kernel32')
    def test_capture_with_incomplete_read(self, mock_kernel32):
        """Test capture when read returns incomplete data."""
        mock_reader = MagicMock()
        mock_reader.size = 1024
        mock_reader.read_raw.return_value = b'\x00' * 500  # Incomplete
        
        capture = TelemetryCapture(hz=10.0)
        capture._readers = {"physics": mock_reader}
        
        frame = capture._capture_frame(0)
        
        assert frame is not None
        # Reader should be removed on incomplete read
        assert "physics" not in capture._readers

    @patch('src.core.telemetry_capture.decode_physics', side_effect=Exception("Decode error"))
    def test_capture_with_decode_error(self, mock_decode):
        """Test capture when decoding fails."""
        mock_reader = MagicMock()
        mock_reader.size = 1024
        mock_reader.read_raw.return_value = b'\x00' * 1024
        
        capture = TelemetryCapture(hz=10.0)
        capture._readers = {"physics": mock_reader}
        
        frame = capture._capture_frame(0)
        
        assert frame is not None
        assert "error" in frame.physics
        # Reader should NOT be removed on decode error (temporary corruption)
        assert "physics" in capture._readers


class TestCaptureIntegration:
    """Test capture integration scenarios."""

    def test_make_output_prefix(self):
        """Test output prefix generation."""
        capture = TelemetryCapture(hz=10.0)
        
        prefix = capture._make_output_prefix()
        
        assert prefix is not None
        assert len(prefix) > 0
        # Format should be MM-DD-HH-MM-SS
        assert len(prefix.split("-")) == 5

    def test_set_on_stop_callback(self):
        """Test setting stop callback."""
        capture = TelemetryCapture(hz=10.0)
        
        callback = Mock()
        capture.set_on_stop_callback(callback)
        
        assert capture._on_stop_callback == callback

    def test_regions_config(self):
        """All three AC Evo SHM regions are wired up for capture.

        Physics has a typed decoder; graphics and static are captured as raw
        bytes for offline reverse-engineering. Names follow the AC Evo
        ``acevo_pmf_*`` convention from SharedFileOut.h.
        """
        assert REGIONS["physics"] == ("acevo_pmf_physics", 1024)
        assert REGIONS["graphics"][0] == "acevo_pmf_graphics"
        assert REGIONS["graphics"][1] >= 2048, "graphics buffer must fit SPageFileGraphicEvo"
        assert REGIONS["static"][0] == "acevo_pmf_static"
        assert REGIONS["static"][1] >= 1024


class TestFrameData:
    """Test FrameData dataclass."""

    def test_frame_data_creation(self):
        """Test creating FrameData."""
        frame = FrameData(
            timestamp="2024-01-01T00:00:00Z",
            frame_number=0,
            physics={"speed_kmh": 100.0}
        )
        
        assert frame.timestamp == "2024-01-01T00:00:00Z"
        assert frame.frame_number == 0
        assert frame.physics == {"speed_kmh": 100.0}
        assert frame.physics_raw is None

    def test_frame_data_to_dict(self):
        """Test converting FrameData to dict."""
        frame = FrameData(
            timestamp="2024-01-01T00:00:00Z",
            frame_number=0,
            physics={"speed_kmh": 100.0}
        )
        
        result = frame.to_dict()
        
        assert isinstance(result, dict)
        assert result["timestamp"] == "2024-01-01T00:00:00Z"
        assert result["frame_number"] == 0


class TestCaptureMetadata:
    """Test CaptureMetadata dataclass."""

    def test_metadata_creation(self):
        """Test creating CaptureMetadata."""
        meta = CaptureMetadata(
            captured_at="2024-01-01T00:00:00Z",
            hz=10.0,
            regions_found=["physics"],
            region_names={"physics": "acevo_pmf_physics"},
            region_sizes={"physics": 1024}
        )
        
        assert meta.captured_at == "2024-01-01T00:00:00Z"
        assert meta.hz == 10.0
        assert meta.regions_found == ["physics"]

    def test_metadata_to_dict(self):
        """Test converting CaptureMetadata to dict."""
        meta = CaptureMetadata(
            captured_at="2024-01-01T00:00:00Z",
            hz=10.0,
            regions_found=["physics"],
            region_names={"physics": "acevo_pmf_physics"},
            region_sizes={"physics": 1024}
        )
        
        result = meta.to_dict()
        
        assert isinstance(result, dict)
        assert result["captured_at"] == "2024-01-01T00:00:00Z"
        assert result["hz"] == 10.0

    def test_debug_metadata_records_minimum_discovered_and_read_sizes(self):
        """Late-connected probed regions remain visible in exported metadata."""
        capture = TelemetryCapture(hz=10.0, debug_logs=True)
        capture._metadata = CaptureMetadata(
            captured_at="2024-01-01T00:00:00Z",
            hz=10.0,
            regions_found=[],
            region_names={},
            region_sizes={},
        )
        reader = MagicMock()
        reader._path_used = r"Local\acevo_pmf_physics"
        reader.size = 8192
        reader.discovered_size = 8192

        capture._remember_reader("physics", reader)

        assert capture._metadata.regions_found == ["physics"]
        assert capture._metadata.region_minimum_sizes == {"physics": 1024}
        assert capture._metadata.region_discovered_sizes == {"physics": 8192}
        assert capture._metadata.region_sizes == {"physics": 8192}
        assert (
            capture._metadata.mapping_probe_limit_bytes
            == MAX_DEBUG_MAPPING_BYTES
        )

        compat = capture._build_compat_meta_record()
        assert compat["_region_minimum_sizes"] == {"physics": 1024}
        assert compat["_region_discovered_sizes"] == {"physics": 8192}
        assert compat["_region_read_sizes"] == {"physics": 8192}
        assert compat["_mapping_probe_limit_bytes"] == MAX_DEBUG_MAPPING_BYTES

    def test_debug_metadata_records_fixed_fallback_without_claiming_discovery(self):
        """Probe failure is explicit while the fixed minimum remains usable."""
        capture = TelemetryCapture(hz=10.0, debug_logs=True)
        reader = MagicMock()
        reader._path_used = r"Local\acevo_pmf_graphics"
        reader.size = 4096
        reader.discovered_size = None

        capture._remember_reader("graphics", reader)
        compat = capture._build_compat_meta_record()

        assert compat["_region_minimum_sizes"] == {"graphics": 4096}
        assert compat["_region_read_sizes"] == {"graphics": 4096}
        assert compat["_region_discovered_sizes"] == {"graphics": None}

    def test_live_debug_toggle_reopens_readers_with_new_probe_mode(self, tmp_path):
        """An active capture applies debug probe changes without a restart."""
        capture = TelemetryCapture(hz=10.0, debug_logs=False)
        capture._running = True
        reader = MagicMock()
        capture._readers = {"physics": reader}

        with patch.object(capture, "_reconnect_missing") as reconnect:
            capture.configure(output_dir=str(tmp_path), debug_logs=True)

        reader.close.assert_called_once()
        reconnect.assert_called_once_with(capture._readers)
        assert capture._readers == {}
        if capture._diag_file:
            capture._diag_file.close()


class TestRegionReaderEdgeCases:
    """Test RegionReader edge cases."""

    @patch('src.core.telemetry_capture.sys')
    def test_region_reader_open_non_windows(self, mock_sys):
        """Test RegionReader.open on non-Windows platform."""
        mock_sys.platform = "linux"
        
        reader = RegionReader("test_region", 1024)
        result = reader.open()
        
        assert result is False

    @patch('src.core.telemetry_capture.kernel32')
    def test_region_reader_close_exception_handling(self, mock_kernel32):
        """Test RegionReader.close with exception handling."""
        mock_kernel32.UnmapViewOfFile.side_effect = OSError("Unmap failed")
        mock_kernel32.CloseHandle.side_effect = OSError("Close failed")
        
        reader = RegionReader("test_region", 1024)
        reader._handle = MagicMock()
        reader._view = MagicMock()
        
        # Should not raise exception
        reader.close()
        
        assert reader._view is None
        assert reader._handle is None


class TestTelemetryCaptureEdgeCases:
    """Test TelemetryCapture edge cases."""

    @patch('src.core.telemetry_capture.os')
    def test_save_raw_dump(self, mock_os):
        """Test saving raw dump to file."""
        mock_os.makedirs.return_value = None
        
        capture = TelemetryCapture(hz=10.0)
        capture._frames = [
            FrameData(
                timestamp="2024-01-01T00:00:00Z",
                frame_number=0,
                physics={"speed_kmh": 100.0},
                physics_raw="aabbccdd"
            )
        ]
        
        # Mock file write
        with patch('builtins.open', create=True) as mock_open:
            mock_open.return_value.__enter__ = Mock()
            mock_open.return_value.__exit__ = Mock()
            mock_open.return_value.write = Mock()
            
            result = capture.save_raw_dump("test_dump.jsonl")
            
            assert result is True

    @patch('src.core.telemetry_capture.os')
    def test_save_raw_dump_error(self, mock_os):
        """Test save_raw_dump with error."""
        mock_os.makedirs.side_effect = Exception("Dir error")
        
        capture = TelemetryCapture(hz=10.0)
        capture._frames = [FrameData(timestamp="2024-01-01T00:00:00Z", frame_number=0, physics={})]
        
        result = capture.save_raw_dump("test_dump.jsonl")
        
        assert result is False

    def test_build_compat_frame_record_non_dict_payload(self):
        """Test _build_compat_frame_record with non-dict payload."""
        frame = FrameData(
            timestamp="2024-01-01T00:00:00Z",
            frame_number=0,
            physics="string_value",  # Non-dict payload
            physics_raw="aabbccdd"
        )
        
        capture = TelemetryCapture(hz=10.0)
        capture._output_prefix = "test"
        
        record = capture._build_compat_frame_record(frame)
        
        assert record["_record_type"] == "frame"
        assert record["regions"]["physics"]["value"] == "string_value"

    def test_build_compat_frame_record_none_payload(self):
        """Test _build_compat_frame_record with None payload."""
        frame = FrameData(
            timestamp="2024-01-01T00:00:00Z",
            frame_number=0,
            physics=None,
            physics_raw="aabbccdd"
        )
        
        capture = TelemetryCapture(hz=10.0)
        capture._output_prefix = "test"
        
        record = capture._build_compat_frame_record(frame)
        
        assert record["_record_type"] == "frame"
        # Should handle None gracefully
        assert "physics" in record["regions"]

    def test_build_compat_meta_record_no_metadata(self):
        """Test _build_compat_meta_record without metadata."""
        capture = TelemetryCapture(hz=10.0)
        capture._readers = {}
        capture._metadata = None
        capture._region_paths = {}
        
        record = capture._build_compat_meta_record()
        
        assert record["_record_type"] == "meta"
        assert record["_regions_found"] == []

    @pytest.mark.asyncio
    async def test_start_capture_already_running(self):
        """Test start_capture when already running."""
        capture = TelemetryCapture(hz=10.0)
        capture._running = True
        
        result = await capture.start_capture()
        
        assert result is True

    @pytest.mark.asyncio
    async def test_start_capture_initialization(self):
        """Test start_capture initialization."""
        capture = TelemetryCapture(hz=10.0)
        
        result = await capture.start_capture()
        
        assert result is True
        assert capture._running is True
        assert capture._frames == []
        assert capture._session_start_time is not None
        assert capture._output_prefix is not None
        assert capture._task is not None

    def test_close_readers(self):
        """Test _close_readers method."""
        mock_reader = MagicMock()
        capture = TelemetryCapture(hz=10.0)
        capture._readers = {"physics": mock_reader}
        
        capture._close_readers()
        
        mock_reader.close.assert_called_once()
        assert capture._readers == {}

    def test_should_notify_stop_callback(self):
        """Test _should_notify_stop_callback logic."""
        capture = TelemetryCapture(hz=10.0)
        
        # Should notify for unexpected stops
        capture._stop_reason = "task_exception"
        assert capture._should_notify_stop_callback() is True
        
        # Should not notify for expected stops
        capture._stop_reason = "manual"
        assert capture._should_notify_stop_callback() is False
        capture._stop_reason = None
        assert capture._should_notify_stop_callback() is False
        capture._stop_reason = "session_end"
        assert capture._should_notify_stop_callback() is False

    @patch('src.core.telemetry_capture.RegionReader')
    def test_connect_regions(self, mock_reader_class):
        """Test _connect_regions method."""
        mock_reader = MagicMock()
        mock_reader.open.return_value = True
        mock_reader._path_used = "test_path"
        mock_reader_class.return_value = mock_reader
        
        capture = TelemetryCapture(hz=10.0)
        
        readers = capture._connect_regions()
        
        assert "physics" in readers
        assert capture._region_paths["physics"] == "test_path"

    @patch('src.core.telemetry_capture.RegionReader')
    def test_connect_regions_failure(self, mock_reader_class):
        """Test _connect_regions when region open fails."""
        mock_reader = MagicMock()
        mock_reader.open.return_value = False
        mock_reader_class.return_value = mock_reader
        
        capture = TelemetryCapture(hz=10.0)
        
        readers = capture._connect_regions()
        
        assert readers == {}

    @patch('src.core.telemetry_capture.RegionReader')
    def test_reconnect_missing(self, mock_reader_class):
        """Test _reconnect_missing method."""
        mock_reader = MagicMock()
        mock_reader.open.return_value = True
        mock_reader._path_used = "test_path"
        mock_reader_class.return_value = mock_reader
        
        capture = TelemetryCapture(hz=10.0)
        existing_readers = {}
        
        capture._reconnect_missing(existing_readers)
        
        assert "physics" in existing_readers
        assert capture._region_paths["physics"] == "test_path"

    @patch('src.core.telemetry_capture.RegionReader')
    def test_reconnect_missing_skips_existing(self, mock_reader_class):
        """Test _reconnect_missing skips regions already connected.

        All three SHM regions (physics, graphics, static) must be already
        present in ``existing_readers`` for this assertion to hold — the
        method's job is to fill in only the missing ones.
        """
        mock_reader = MagicMock()
        mock_reader.open.return_value = True
        mock_reader_class.return_value = mock_reader

        capture = TelemetryCapture(hz=10.0)
        existing_readers = {
            "physics": MagicMock(),
            "graphics": MagicMock(),
            "static": MagicMock(),
        }

        capture._reconnect_missing(existing_readers)

        # All regions already present → no new RegionReader instances
        mock_reader_class.assert_not_called()

    @patch('src.core.telemetry_capture.kernel32')
    def test_capture_frame_disconnected_reader(self, mock_kernel32):
        """Test _capture_frame with disconnected reader."""
        mock_reader = MagicMock()
        mock_reader.size = 1024
        mock_reader.read_raw.side_effect = Exception("Disconnected")
        
        capture = TelemetryCapture(hz=10.0)
        capture._readers = {"physics": mock_reader}
        
        frame = capture._capture_frame(0)
        
        assert frame is not None
        # Reader should be removed after disconnection
        assert "physics" not in capture._readers

    @patch('src.core.telemetry_capture.kernel32')
    def test_region_reader_open_duplicate_path(self, mock_kernel32):
        """Test RegionReader.open with duplicate path in candidates."""
        mock_handle = MagicMock()
        mock_view = MagicMock()
        
        # First call succeeds, second call would fail but shouldn't be attempted
        call_count = [0]
        def open_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_handle
            return 0
        
        mock_kernel32.OpenFileMappingW.side_effect = open_side_effect
        mock_kernel32.MapViewOfFile.return_value = mock_view
        
        reader = RegionReader("test_region", 1024)
        result = reader.open()
        
        assert result is True
        # Should only attempt open once for the successful path
        assert call_count[0] == 1
