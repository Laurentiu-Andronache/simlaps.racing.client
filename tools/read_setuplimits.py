#!/usr/bin/env python3
"""
Read .carsetuplimits files by scanning for float values that match known
in-game setup values visible in the screenshots.

Ground truth from screenshots:
  GT3 RS:  ride_height_front=110mm, ride_height_rear=120mm, pressure=28psi,
           arb=2, diff_preload=10Nm, diff_coast=0.35, diff_power=0.35,
           slow_bump=5, slow_rebound=5, tc=3, esc=0, fuel=30L
  SF25:    ride_height_front=50mm, ride_height_rear=65mm,
           front_wing_angle=4deg, rear_wing_angle=12deg,
           pressure=22psi, arb_front=8, arb_rear=4,
           brake_bias=55%, diff_preload=45Nm, diff_coast=0.25, diff_power=0.2
           slow_bump_front=9/10, slow_bump_rear=11/12,
           engine_map=1, ers_deploy=1, ers_recharge=0, fuel=60L
  RS3:     pressure_front=26psi, pressure_rear=28psi, tc=1, esc=0, fuel=30L
"""
import struct
import os
import sys


def read_all_floats(data: bytes, step: int = 4) -> list:
    """Read every float at every aligned offset."""
    floats = []
    for offset in range(0, len(data) - 3, step):
        val = struct.unpack_from('<f', data, offset)[0]
        floats.append((offset, val))
    return floats


def is_finite_reasonable(v: float, lo=-1e6, hi=1e6) -> bool:
    import math
    return math.isfinite(v) and lo <= v <= hi


def scan_known_values(filepath: str, known_values: list):
    """Scan the file for all known setup values and report which offsets contain them."""
    data = open(filepath, 'rb').read()
    print(f"\n{'='*70}")
    print(f"FILE: {os.path.basename(filepath)}  ({len(data)} bytes)")
    print(f"{'='*70}")

    # Print ALL reasonable floats first (the full picture)
    print("\nAll reasonable floats (offset, value):") 
    floats = read_all_floats(data)
    reasonable = [(off, v) for off, v in floats if is_finite_reasonable(v, -10000, 100000)]
    for off, v in reasonable:
        marker = ""
        for label, target, tol in known_values:
            if abs(v - target) <= tol:
                marker += f"  <-- {label}"
        print(f"  {off:4d} (0x{off:04x}): {v:12.4f}{marker}")

    print()


def main():
    base = r'c:\Storage\my documents\sim-laps-client\extracted\content\cars'

    cars = {
        'ks_porsche_992_gt3_rs': {
            'file': r'data\setup\limitsporsche992gt3rs.carsetuplimits',
            'known': [
                ("ride_height_front_mm",  110.0, 1.0),
                ("ride_height_rear_mm",   120.0, 1.0),
                ("pressure_psi",           28.0, 0.5),
                ("arb",                     2.0, 0.1),
                ("diff_preload_nm",        10.0, 0.5),
                ("diff_coast",              0.35, 0.01),
                ("diff_power",              0.35, 0.01),
                ("slow_bump",               5.0, 0.1),
                ("slow_rebound",            5.0, 0.1),
                ("tc_level",                3.0, 0.1),
                ("esc_level",               0.0, 0.05),
                ("fuel_L",                 30.0, 0.5),
            ],
        },
        'ks_ferrari_sf_25': {
            'file': r'data\setup\ferrarisf25limits.carsetuplimits',
            'known': [
                ("ride_height_front_mm",   50.0, 1.0),
                ("ride_height_rear_mm",    65.0, 1.0),
                ("front_wing_angle_deg",    4.0, 0.2),
                ("rear_wing_angle_deg",    12.0, 0.2),
                ("pressure_psi",           22.0, 0.5),
                ("arb_front",               8.0, 0.1),
                ("arb_rear",                4.0, 0.1),
                ("brake_bias_pct",         55.0, 0.5),
                ("diff_preload_nm",        45.0, 1.0),
                ("diff_coast",              0.25, 0.01),
                ("diff_power",              0.20, 0.01),
                ("slow_bump_front",         9.0, 0.1),
                ("slow_rebound_front",     10.0, 0.1),
                ("slow_bump_rear",         11.0, 0.1),
                ("slow_rebound_rear",      12.0, 0.1),
                ("engine_map",              1.0, 0.05),
                ("fuel_L",                 60.0, 1.0),
            ],
        },
        'ks_audi_rs_3_sportback': {
            'file': r'data\setup\ks_audi_rs3_limits.carsetuplimits',
            'known': [
                ("pressure_front_psi",     26.0, 0.5),
                ("pressure_rear_psi",      28.0, 0.5),
                ("tc_level",                1.0, 0.1),
                ("esc_level",               0.0, 0.05),
                ("fuel_L",                 30.0, 0.5),
            ],
        },
    }

    for car_key, car_cfg in cars.items():
        path = os.path.join(base, car_key, car_cfg['file'])
        if os.path.exists(path):
            scan_known_values(path, car_cfg['known'])
        else:
            print(f"NOT FOUND: {path}")


if __name__ == '__main__':
    if len(sys.argv) > 1:
        # Single file mode - just dump all reasonable floats
        data = open(sys.argv[1], 'rb').read()
        print(f"File: {sys.argv[1]}  ({len(data)} bytes)")
        floats = read_all_floats(data)
        for off, v in floats:
            if is_finite_reasonable(v, -10000, 100000):
                print(f"  {off:4d} (0x{off:04x}): {v:12.6f}")
    else:
        main()
