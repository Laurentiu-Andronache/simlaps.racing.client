#!/usr/bin/env python3
"""
Focused scanner: find exactly which float offsets hold the known in-game
setup values from the screenshots, across all three unit conventions.
"""
import struct, math, os

PSI_TO_KPA  = 6.89476
PSI_TO_BAR  = 0.0689476
PSI_TO_PA   = 1000 * PSI_TO_KPA

def all_floats(data):
    for off in range(0, len(data) - 3, 4):
        v = struct.unpack_from('<f', data, off)[0]
        if math.isfinite(v):
            yield off, v

def find_near(data, target, tol=0.05):
    return [(off, v) for off, v in all_floats(data) if abs(v - target) <= tol]

def print_hits(label, hits):
    if hits:
        locs = ", ".join(f"0x{off:03x}={v:.4f}" for off, v in hits[:8])
        print(f"  {label:<35}: {locs}")

# ── known values per car, with alternative unit forms ──────────────────────
CARS = {
    "ks_porsche_992_gt3_rs": {
        "path": r"data\setup\limitsporsche992gt3rs.carsetuplimits",
        "params": {
            "ride_ht_front 110mm":   [110.0, 0.110],
            "ride_ht_rear  120mm":   [120.0, 0.120],
            "pressure 28psi":        [28.0, 28.0*PSI_TO_KPA, 28.0*PSI_TO_BAR],
            "arb 2":                 [2.0],
            "diff_preload 10Nm":     [10.0],
            "diff_coast 0.35":       [0.35],
            "diff_power 0.35":       [0.35],
            "slow_bump 5":           [5.0],
            "slow_rebound 5":        [5.0],
            "tc 3":                  [3.0],
            "esc 0":                 [0.0],
            "fuel 30L":              [30.0],
        },
    },
    "ks_ferrari_sf_25": {
        "path": r"data\setup\ferrarisf25limits.carsetuplimits",
        "params": {
            "ride_ht_front 50mm":    [50.0, 0.050],
            "ride_ht_rear  65mm":    [65.0, 0.065],
            "fw_angle 4deg":         [4.0],
            "rw_angle 12deg":        [12.0],
            "pressure 22psi":        [22.0, 22.0*PSI_TO_KPA, 22.0*PSI_TO_BAR],
            "arb_front 8":           [8.0],
            "arb_rear 4":            [4.0],
            "brake_bias 55pct":      [55.0, 0.55],
            "diff_preload 45Nm":     [45.0],
            "diff_coast 0.25":       [0.25],
            "diff_power 0.20":       [0.20],
            "slow_bump_f 9":         [9.0],
            "slow_rebound_f 10":     [10.0],
            "slow_bump_r 11":        [11.0],
            "slow_rebound_r 12":     [12.0],
            "engine_map 1":          [1.0],
            "fuel 60L":              [60.0],
        },
    },
    "ks_audi_rs_3_sportback": {
        "path": r"data\setup\ks_audi_rs3_limits.carsetuplimits",
        "params": {
            "pressure_front 26psi":  [26.0, 26.0*PSI_TO_KPA],
            "pressure_rear  28psi":  [28.0, 28.0*PSI_TO_KPA],
            "tc 1":                  [1.0],
            "esc 0":                 [0.0],
            "fuel 30L":              [30.0],
        },
    },
}

BASE = r"c:\Storage\my documents\sim-laps-client\extracted\content\cars"

for car, cfg in CARS.items():
    fpath = os.path.join(BASE, car, cfg["path"])
    if not os.path.exists(fpath):
        print(f"\n{car}: FILE NOT FOUND"); continue
    data = open(fpath, "rb").read()
    print(f"\n{'='*65}")
    print(f"  {car}  ({len(data)} bytes)")
    print(f"{'='*65}")
    print("  All reasonable floats in range [0.1, 200]:")
    for off, v in all_floats(data):
        if 0.1 <= abs(v) <= 200:
            print(f"    0x{off:03x}  {v:.5f}")
    print()
    print("  Known-value matches:")
    for label, targets in cfg["params"].items():
        hits = []
        for t in targets:
            hits += find_near(data, t, tol=0.05)
        print_hits(label, hits)
