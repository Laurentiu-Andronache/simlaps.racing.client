#!/usr/bin/env python3
import struct
import sys
import json

def analyze_damper_curve(filepath):
    """Properly analyze damper curves with correct data interpretation"""
    
    with open(filepath, 'rb') as f:
        data = f.read()
    
    print(f"Analyzing damper curve: {filepath}")
    print(f"File size: {len(data)} bytes")
    
    # Parse the damper curve data
    offset = 8
    points = []
    
    while offset + 16 <= len(data):
        entry = data[offset:offset+16]
        
        # Try to extract velocity and force
        # Based on the hex analysis, it looks like:
        # bytes 4-7: velocity (m/s)
        # bytes 12-15: force (N) or some other parameter
        
        try:
            # Extract velocity from bytes 4-7
            velocity = struct.unpack('<f', entry[4:8])[0]
            
            # Try different positions for force
            force_options = []
            for pos in [8, 12]:
                if pos + 4 <= 16:
                    force = struct.unpack('<f', entry[pos:pos+4])[0]
                    if abs(force) < 1e6:  # Filter out extreme values
                        force_options.append((pos, force))
            
            # Use the most reasonable force value
            if force_options:
                best_force = min(force_options, key=lambda x: abs(x[1]))[1]
                points.append((velocity, best_force))
            
        except:
            pass
        
        offset += 16
    
    # Filter and clean the data
    clean_points = []
    for vel, force in points:
        # Filter out unrealistic velocities (> 10 m/s)
        if abs(vel) <= 10.0 and abs(force) <= 10000:
            clean_points.append((vel, force))
    
    print(f"Found {len(clean_points)} valid data points")
    
    if clean_points:
        velocities = [p[0] for p in clean_points]
        forces = [p[1] for p in clean_points]
        
        print(f"Velocity range: {min(velocities):.6f} to {max(velocities):.6f} m/s")
        print(f"Force range: {min(forces):.2f} to {max(forces):.2f} N")
        
        # Separate compression and rebound
        compression_points = [(v, f) for v, f in clean_points if v < -0.001]
        rebound_points = [(v, f) for v, f in clean_points if v > 0.001]
        
        print(f"\nCompression points: {len(compression_points)}")
        print(f"Rebound points: {len(rebound_points)}")
        
        # Analyze compression characteristics
        if compression_points:
            comp_velocities = sorted([p[0] for p in compression_points])
            comp_forces = [p[1] for p in compression_points]
            print(f"\nCompression characteristics:")
            print(f"  Velocity range: {comp_velocities[0]:.4f} to {comp_velocities[-1]:.4f} m/s")
            print(f"  Force range: {min(comp_forces):.1f} to {max(comp_forces):.1f} N")
            
            # Calculate damping coefficients
            if len(compression_points) >= 2:
                # Find slow and fast compression
                slow_comp = min(compression_points, key=lambda x: abs(x[0]))
                fast_comp = max(compression_points, key=lambda x: abs(x[0]))
                
                slow_coeff = abs(slow_comp[1] / slow_comp[0]) if slow_comp[0] != 0 else 0
                fast_coeff = abs(fast_comp[1] / fast_comp[0]) if fast_comp[0] != 0 else 0
                
                print(f"  Slow compression coefficient: {slow_coeff:.1f} N/(m/s)")
                print(f"  Fast compression coefficient: {fast_coeff:.1f} N/(m/s)")
                print(f"  Compression progression: {fast_coeff/slow_coeff:.2f}x")
        
        # Analyze rebound characteristics
        if rebound_points:
            reb_velocities = sorted([p[0] for p in rebound_points])
            reb_forces = [p[1] for p in rebound_points]
            print(f"\nRebound characteristics:")
            print(f"  Velocity range: {reb_velocities[0]:.4f} to {reb_velocities[-1]:.4f} m/s")
            print(f"  Force range: {min(reb_forces):.1f} to {max(reb_forces):.1f} N")
            
            # Calculate damping coefficients
            if len(rebound_points) >= 2:
                # Find slow and fast rebound
                slow_reb = min(rebound_points, key=lambda x: x[0])
                fast_reb = max(rebound_points, key=lambda x: x[0])
                
                slow_coeff = abs(slow_reb[1] / slow_reb[0]) if slow_reb[0] != 0 else 0
                fast_coeff = abs(fast_reb[1] / fast_reb[0]) if fast_reb[0] != 0 else 0
                
                print(f"  Slow rebound coefficient: {slow_coeff:.1f} N/(m/s)")
                print(f"  Fast rebound coefficient: {fast_coeff:.1f} N/(m/s)")
                print(f"  Rebound progression: {fast_coeff/slow_coeff:.2f}x")
        
        # Display all data points
        print(f"\nAll data points:")
        print(f"{'#':3s} {'Direction':11s} {'Velocity (m/s)':>15s} {'Force (N)':>12s}")
        print("-" * 45)
        
        for i, (vel, force) in enumerate(clean_points):
            if vel < -0.001:
                direction = "Compression"
            elif vel > 0.001:
                direction = "Rebound"
            else:
                direction = "Zero"
            
            print(f"{i:3d} {direction:11s} {vel:15.4f} {force:12.1f}")
        
        return clean_points
    
    return []

def compare_damper_settings():
    """Compare different damper settings for the same car"""
    
    cars_dir = "C:/Storage/my documents/sim-laps-client/extracted/content/cars"
    
    damper_files = [
        ("Porsche 992 GT3 RS - Base", "ks_porsche_992_gt3_rs/data/dampers/damperfrontcup.curve"),
        ("Porsche 992 GT3 RS - Setup 1", "ks_porsche_992_gt3_rs/data/dampers/damperfrontcup_1.curve"), 
        ("Porsche 992 GT3 RS - Setup 2", "ks_porsche_992_gt3_rs/data/dampers/damperfrontcup_2.curve")
    ]
    
    results = {}
    
    for name, file_path in damper_files:
        full_path = f"{cars_dir}/{file_path}"
        print(f"\n{'='*80}")
        print(f"ANALYZING: {name}")
        print('='*80)
        
        points = analyze_damper_curve(full_path)
        results[name] = points
    
    # Summary comparison
    print(f"\n{'='*80}")
    print("DAMPER SETTING COMPARISON")
    print('='*80)
    
    for name, points in results.items():
        if points:
            velocities = [p[0] for p in points]
            forces = [p[1] for p in points]
            compression = [(v, f) for v, f in points if v < -0.001]
            rebound = [(v, f) for v, f in points if v > 0.001]
            
            print(f"\n{name}:")
            print(f"  Total points: {len(points)}")
            print(f"  Compression: {len(compression)} points")
            print(f"  Rebound: {len(rebound)} points")
            print(f"  Velocity range: {min(velocities):.4f} to {max(velocities):.4f} m/s")
            print(f"  Force range: {min(forces):.1f} to {max(forces):.1f} N")
    
    return results

if __name__ == "__main__":
    import os
    if len(sys.argv) > 1:
        analyze_damper_curve(sys.argv[1])
    else:
        compare_damper_settings()
