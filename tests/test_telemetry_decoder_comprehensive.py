"""
Comprehensive tests for telemetry decoder.

Tests all decoder functions including fallback decoders and data conversion utilities.
"""

import pytest
import struct
from src.core.telemetry_decoder import (
    decode_physics,
    decode_physics_ac,
    decode_physics_fallback,
    decode_graphics_evo,
    decode_graphics_fallback,
    decode_static_fallback,
    physics_to_dict,
    R,
    Coords,
    Physics,
    AC_STATUS,
    AC_FLAG_TYPE,
    peek_graphics_validity,
)


GRAPHICS_FLAG_FIELDS = (
    (42, "is_rpm_limiter_on"),
    (43, "is_change_up_rpm"),
    (44, "is_change_down_rpm"),
    (45, "tc_active"),
    (46, "abs_active"),
    (47, "esc_active"),
    (48, "launch_active"),
    (49, "is_ignition_on"),
    (50, "is_engine_running"),
    (51, "kers_is_charging"),
    (52, "is_wrong_way"),
    (53, "is_drs_available"),
    (54, "battery_is_charging"),
    (55, "is_max_kj_per_lap_reached"),
    (56, "is_max_charge_kj_per_lap_reached"),
)


def _valid_graphics_buffer() -> bytearray:
    data = bytearray(4096)
    struct.pack_into("<f", data, 1244, 0.5)
    struct.pack_into("<I", data, 2392, 1)
    struct.pack_into("<I", data, 2412, 6)
    return data


@pytest.mark.parametrize(("offset", "field_name"), GRAPHICS_FLAG_FIELDS)
def test_graphics_decoder_exposes_each_documented_flag(offset, field_name) -> None:
    data = _valid_graphics_buffer()
    data[offset] = 1

    result = decode_graphics_evo(bytes(data))

    assert result is not None
    assert result[field_name] is True
    for _, other_field in GRAPHICS_FLAG_FIELDS:
        if other_field != field_name:
            assert result[other_field] is False


def test_graphics_decoder_exposes_flags_observed_in_ace_090() -> None:
    data = _valid_graphics_buffer()
    for offset in (42, 44, 46, 49):
        data[offset] = 1

    result = decode_graphics_evo(bytes(data))

    assert result is not None
    assert result["is_rpm_limiter_on"] is True
    assert result["is_change_down_rpm"] is True
    assert result["abs_active"] is True
    assert result["is_ignition_on"] is True


def test_lightweight_graphics_peek_includes_completed_lap_time() -> None:
    data = bytearray(4096)
    struct.pack_into("<i", data, 188, 125)
    struct.pack_into("<i", data, 2384, 2)
    struct.pack_into("<i", data, 2396, 75147)
    data[3121] = 1

    result = peek_graphics_validity(bytes(data))

    assert result == {
        "total_lap_count": 2,
        "completed_laps": 2,
        "is_valid_lap": True,
        "current_lap_time_ms": 125,
        "last_laptime_ms": 75147,
    }


class TestBinaryReader:
    """Test BinaryReader (R) utility class."""

    def test_binary_reader_initialization(self):
        """Test BinaryReader initialization."""
        data = b'\x00\x01\x02\x03'
        reader = R(data)
        
        assert reader._b is not None
        assert reader._pos == 0

    def test_binary_reader_read_float(self):
        """Test reading float from binary data."""
        # Pack a float: 3.14
        data = struct.pack('<f', 3.14)
        reader = R(data)
        
        result = reader.f()
        
        assert abs(result - 3.14) < 0.01

    def test_binary_reader_read_int(self):
        """Test reading int from binary data."""
        data = struct.pack('<i', 42)
        reader = R(data)
        
        result = reader.i()
        
        assert result == 42

    def test_binary_reader_read_coords(self):
        """Test reading Coords from binary data."""
        # Pack three floats for x, y, z
        data = struct.pack('<fff', 1.0, 2.0, 3.0)
        reader = R(data)
        
        result = reader.coords()
        
        assert result.x == 1.0
        assert result.y == 2.0
        assert result.z == 3.0

    def test_binary_reader_read_float_array(self):
        """Test reading float array from binary data."""
        data = struct.pack('<ffff', 1.0, 2.0, 3.0, 4.0)
        reader = R(data)
        
        result = reader.fa(4)
        
        assert len(result) == 4
        assert result[0] == 1.0
        assert result[3] == 4.0

    def test_binary_reader_read_string(self):
        """Test reading string from binary data."""
        test_str = "Hello"
        # R class uses utf-16-le encoding
        data = test_str.encode('utf-16-le') + b'\x00' * (100 - len(test_str.encode('utf-16-le')))
        reader = R(data)
        
        result = reader.s(5)  # Read 5 characters
        
        assert test_str in result or result.strip('\x00') == test_str

    def test_binary_reader_skip(self):
        """Test skipping bytes in binary data."""
        data = b'\x00\x01\x02\x03\x04\x05'
        reader = R(data)
        
        reader.skip(2)
        
        assert reader._pos == 2

    def test_binary_reader_coords_list(self):
        """Test reading list of Coords."""
        # Pack 4 sets of coords (12 floats total)
        data = struct.pack('<' + 'f' * 12, *[float(i) for i in range(12)])
        reader = R(data)
        
        result = reader.coords_list(4)
        
        assert len(result) == 4
        assert result[0].x == 0.0
        assert result[3].z == 11.0


class TestPhysicsFallbackDecoder:
    """Test physics fallback decoder."""

    def test_decode_physics_fallback_basic(self):
        """Test fallback decoder with basic data."""
        # Create some test data with recognizable float values
        data = struct.pack('<fff', 3.14, 2.71, 1.41)
        
        result = decode_physics_fallback(data)
        
        assert result["_decoder"] == "fallback"
        assert result["size"] == len(data)
        assert "floats" in result
        assert "ints" in result
        assert "raw_hex_start" in result

    def test_decode_physics_fallback_empty(self):
        """Test fallback decoder with empty data."""
        result = decode_physics_fallback(b'')
        
        assert result["_decoder"] == "fallback"
        assert result["size"] == 0

    def test_decode_physics_fallback_large_data(self):
        """Test fallback decoder with data larger than 200 bytes."""
        data = b'\x00' * 300
        
        result = decode_physics_fallback(data)
        
        assert result["size"] == 300
        # Should only process first 200 bytes
        assert len(result["floats"]) <= 20


class TestGraphicsFallbackDecoder:
    """Test graphics fallback decoder."""

    def test_decode_graphics_fallback_basic(self):
        """Test graphics fallback with basic data."""
        data = b'Hello World' + b'\x00' * 50
        
        result = decode_graphics_fallback(data)
        
        assert result["_decoder"] == "fallback"
        assert "ascii_start" in result
        assert "floats" in result
        assert "raw_hex_start" in result

    def test_decode_graphics_fallback_empty(self):
        """Test graphics fallback with empty data."""
        result = decode_graphics_fallback(b'')
        
        assert result["_decoder"] == "fallback"
        assert result["size"] == 0


class TestStaticFallbackDecoder:
    """Test static fallback decoder."""

    def test_decode_static_fallback_basic(self):
        """Test static fallback with basic data."""
        data = b'Hello World'
        
        result = decode_static_fallback(data)
        
        assert result["_decoder"] == "fallback"
        assert "bytes" in result
        assert "ascii" in result
        assert result["size"] == len(data)

    def test_decode_static_fallback_empty(self):
        """Test static fallback with empty data."""
        result = decode_static_fallback(b'')
        
        assert result["_decoder"] == "fallback"
        assert result["size"] == 0


class TestPhysicsToDict:
    """Test physics_to_dict conversion function."""

    def test_physics_to_dict_with_dict(self):
        """Test conversion when input is already a dict."""
        input_data = {"speed_kmh": 100.0, "gear": 3}
        
        result = physics_to_dict(input_data)
        
        assert result == input_data
        assert result is input_data  # Should return same object

    def test_physics_to_dict_with_dataclass(self):
        """Test conversion from Physics dataclass."""
        physics = Physics(
            packet_id=1,
            gas=0.5,
            brake=0.0,
            fuel=50.0,
            gear=3,
            rpms=5000,
            steer_angle=0.0,
            speed_kmh=100.0,
            velocity=Coords(x=0.0, y=0.0, z=10.0),
            acc_g=Coords(x=0.0, y=0.0, z=0.0),
            wheel_slip=[0.0, 0.0, 0.0, 0.0],
            wheel_load=[1000.0, 1000.0, 1000.0, 1000.0],
            wheels_pressure=[27.0, 27.0, 27.0, 27.0],
            wheel_angular_speed=[0.0, 0.0, 0.0, 0.0],
            tyre_wear=[0.0, 0.0, 0.0, 0.0],
            tyre_dirty_level=[0.0, 0.0, 0.0, 0.0],
            tyre_core_temp=[80.0, 80.0, 80.0, 80.0],
            camber_rad=[0.0, 0.0, 0.0, 0.0],
            suspension_travel=[0.0, 0.0, 0.0, 0.0],
            drs=0.0,
            tc=0.0,
            heading=0.0,
            pitch=0.0,
            roll=0.0,
            cg_height=0.0,
            car_damage=[0.0, 0.0, 0.0, 0.0, 0.0],
            number_of_tyres_out=0,
            pit_limiter_on=False,
            abs=0.0,
            kers_charge=0.0,
            kers_input=0.0,
            auto_shifter_on=False,
            ride_height=[0.0, 0.0],
            turbo_boost=0.0,
            ballast=0.0,
            air_density=1.0,
            air_temp=25.0,
            road_temp=30.0,
            local_angular_velocity=Coords(x=0.0, y=0.0, z=0.0),
            final_ff=0.0,
            performance_meter=0.0,
            engine_brake=0,
            ers_recovery_level=0,
            ers_power_level=0,
            ers_heat_charging=0,
            ers_is_charging=0,
            kers_current_kj=0.0,
            drs_available=False,
            drs_enabled=False,
            brake_temp=[100.0, 100.0, 100.0, 100.0],
            clutch=0.0,
            tyre_temp_i=[80.0, 80.0, 80.0, 80.0],
            tyre_temp_m=[80.0, 80.0, 80.0, 80.0],
            tyre_temp_o=[80.0, 80.0, 80.0, 80.0],
            is_ai_controlled=False,
            tyre_contact_point=[Coords(0,0,0), Coords(0,0,0), Coords(0,0,0), Coords(0,0,0)],
            tyre_contact_normal=[Coords(0,0,0), Coords(0,0,0), Coords(0,0,0), Coords(0,0,0)],
            tyre_contact_heading=[Coords(0,0,0), Coords(0,0,0), Coords(0,0,0), Coords(0,0,0)],
            brake_bias=0.5,
            local_velocity=Coords(x=0.0, y=0.0, z=0.0),
            # AC Evo precision fields
            p2p_activations=0,
            p2p_status=0,
            current_max_rpm=8000,
            mz=[0.0, 0.0, 0.0, 0.0],
            fx=[0.0, 0.0, 0.0, 0.0],
            fy=[0.0, 0.0, 0.0, 0.0],
            slip_ratio=[0.0, 0.0, 0.0, 0.0],
            slip_angle=[0.0, 0.0, 0.0, 0.0],
            tcin_action=False,
            absin_action=False,
            suspension_damage=[0.0, 0.0, 0.0, 0.0],
            tyre_temp=[80.0, 80.0, 80.0, 80.0],
            water_temp=90.0,
            brake_torque=[0.0, 0.0, 0.0, 0.0],
            front_brake_compound=0,
            rear_brake_compound=0,
            pad_life=[1.0, 1.0, 1.0, 1.0],
            disc_life=[1.0, 1.0, 1.0, 1.0],
            ignition_on=True,
            starter_engine_on=False,
            is_engine_running=True,
            kerb_vibration=0.0,
            slip_vibrations=0.0,
            groad_vibrations=0.0,
            abs_vibrations=0.0,
        )
        
        result = physics_to_dict(physics)
        
        assert isinstance(result, dict)
        assert result["speed_kmh"] == 100.0
        assert result["gear"] == 3
        assert "velocity" in result

    def test_physics_to_dict_with_unknown_type(self):
        """Test conversion with unknown type."""
        result = physics_to_dict("invalid")
        
        assert "error" in result




class TestACPhysicsDecoder:
    """Test AC/ACC physics structure decoder."""

    def test_decode_physics_ac_with_invalid_data(self):
        """Test AC decoder with invalid/short data."""
        data = b'\x00' * 100  # Too short for AC physics
        
        result = decode_physics_ac(data)
        
        assert result is None

    def test_decode_physics_ac_with_empty_data(self):
        """Test AC decoder with empty data."""
        result = decode_physics_ac(b'')
        
        assert result is None


class TestDecodePhysics:
    """Test main decode_physics function."""

    def test_decode_physics_with_valid_structure(self):
        """Test decode_physics returns properly formatted result."""
        # Create minimal data that will trigger fallback
        data = struct.pack('<fff', 100.0, 0.5, 3.0)  # speed, gas, brake
        
        result = decode_physics(data)
        
        assert isinstance(result, dict)
        assert "_decoder" in result

    def test_decode_physics_with_empty_data(self):
        """Test decode_physics with empty data."""
        result = decode_physics(b'')
        
        assert isinstance(result, dict)
        assert result["size"] == 0


class TestEnums:
    """Test enum classes."""

    def test_ac_status_values(self):
        """Test AC_STATUS enum values."""
        assert AC_STATUS.AC_OFF.value == 0
        assert AC_STATUS.AC_REPLAY.value == 1
        assert AC_STATUS.AC_LIVE.value == 2
        assert AC_STATUS.AC_PAUSE.value == 3

    def test_ac_flag_type_values(self):
        """Test AC_FLAG_TYPE enum values."""
        assert AC_FLAG_TYPE.AC_NO_FLAG.value == 0
        assert AC_FLAG_TYPE.AC_CHECKERED_FLAG.value == 5
