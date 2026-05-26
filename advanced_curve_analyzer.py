#!/usr/bin/env python3
import struct
import sys
import json
import numpy as np

def analyze_suspension_curve(filepath):
    """Analyze suspension damper curves with proper interpretation"""
    
    with open(filepath, 'rb') as f:
        data = f.read()
    
    print(f"Analyzing suspension curve: {filepath}")
    print(f"File size: {len(data)} bytes")
    
    # Try different parsing approaches for suspension data
    approaches = []
    
    # Approach 1: Standard 16-byte entries
    offset = 8
    points_1 = []
    while offset + 16 <= len(data):
        entry = data[offset:offset+16]
        # Try different float positions
        for i in range(0, 16, 4):
            for j in range(i+4, 16, 4):
                try:
                    x = struct.unpack('<f', entry[i:i+4])[0]
                    y = struct.unpack('<f', entry[j:j+4])[0]
                    # Filter out extreme values that are likely corrupted
                    if abs(x) < 1e6 and abs(y) < 1e6:
                        points_1.append((x, y))
                except:
                    pass
        offset += 16
    
    approaches.append(("Standard 16-byte", points_1))
    
    # Approach 2: Look for velocity vs force patterns
    offset = 8
    points_2 = []
    while offset + 16 <= len(data):
        entry = data[offset:offset+16]
        # Damper curves are typically velocity (m/s) vs force (N)
        # Try to find reasonable velocity ranges (-0.5 to 0.5 m/s)
        for i in range(0, 16, 4):
            for j in range(i+4, 16, 4):
                try:
                    velocity = struct.unpack('<f', entry[i:i+4])[0]
                    force = struct.unpack('<f', entry[j:j+4])[0]
                    # Typical damper velocity range
                    if -1.0 <= velocity <= 1.0 and -10000 <= force <= 10000:
                        points_2.append((velocity, force))
                except:
                    pass
        offset += 16
    
    approaches.append(("Velocity-Force pattern", points_2))
    
    # Approach 3: Direct float reading with different offsets
    points_3 = []
    for i in range(8, len(data) - 8, 8):
        try:
            x = struct.unpack('<f', data[i:i+4])[0]
            y = struct.unpack('<f', data[i+4:i+8])[0]
            if abs(x) < 1e6 and abs(y) < 1e6:
                points_3.append((x, y))
        except:
            pass
    
    approaches.append(("Direct 8-byte pairs", points_3))
    
    # Find the best approach
    best_approach = None
    best_points = []
    
    for name, points in approaches:
        if len(points) > len(best_points):
            best_approach = name
            best_points = points
    
    print(f"\nBest parsing method: {best_approach}")
    print(f"Found {len(best_points)} valid data points")
    
    if best_points:
        velocities = [p[0] for p in best_points]
        forces = [p[1] for p in best_points]
        
        print(f"Velocity range: {min(velocities):.6f} to {max(velocities):.6f} m/s")
        print(f"Force range: {min(forces):.2f} to {max(forces):.2f} N")
        
        # Separate compression and rebound
        compression_points = [(v, f) for v, f in best_points if v < 0]
        rebound_points = [(v, f) for v, f in best_points if v > 0]
        
        print(f"\nCompression points (vel < 0): {len(compression_points)}")
        print(f"Rebound points (vel > 0): {len(rebound_points)}")
        
        if compression_points:
            comp_velocities = [p[0] for p in compression_points]
            comp_forces = [p[1] for p in compression_points]
            print(f"Compression: {min(comp_velocities):.3f} to {max(comp_velocities):.3f} m/s, "
                  f"{min(comp_forces):.1f} to {max(comp_forces):.1f} N")
        
        if rebound_points:
            reb_velocities = [p[0] for p in rebound_points]
            reb_forces = [p[1] for p in rebound_points]
            print(f"Rebound: {min(reb_velocities):.3f} to {max(reb_velocities):.3f} m/s, "
                  f"{min(reb_forces):.1f} to {max(reb_forces):.1f} N")
        
        # Display detailed data
        print(f"\nDetailed data points:")
        for i, (x, y) in enumerate(best_points[:20]):
            direction = "Compression" if x < 0 else "Rebound" if x > 0 else "Zero"
            print(f"  {i:2d}: {direction:11s} | Velocity: {x:7.4f} m/s | Force: {y:8.1f} N")
        
        if len(best_points) > 20:
            print(f"  ... and {len(best_points) - 20} more points")
        
        return best_points
    else:
        print("No valid data points found")
        return []

def analyze_multiple_damper_files():
    """Analyze multiple damper files to show tuning ranges"""
    
    cars_dir = "C:/Storage/my documents/sim-laps-client/extracted/content/cars"
    
    # Find damper files from high-end cars
    damper_files = [
        "ks_porsche_992_gt3_rs/data/dampers/damperfrontcup.curve",
        "ks_porsche_992_gt3_rs/data/dampers/damperfrontcup_1.curve", 
        "ks_porsche_992_gt3_rs/data/dampers/damperfrontcup_2.curve",
        "ks_dallara_exp/data/ks_dallara_exp_front_coil.coilover",
        "ks_ferrari_296_gt3/data/dampers/damperfront.curve"
    ]
    
    results = {}
    
    for file_path in damper_files:
        full_path = f"{cars_dir}/{file_path}"
        if os.path.exists(full_path):
            print(f"\n{'='*80}")
            print(f"ANALYZING: {file_path}")
            print('='*80)
            
            points = analyze_suspension_curve(full_path)
            results[file_path] = points
            
            print(f"\n")
    
    return results

def main():
    if len(sys.argv) > 1:
        # Analyze single file
        analyze_suspension_curve(sys.argv[1])
    else:
        # Analyze multiple damper files
        results = analyze_multiple_damper_files()
        
        # Save results
        with open("suspension_analysis.json", 'w') as f:
            json.dump(results, f, indent=2)
        
        print(f"\nDetailed suspension analysis saved to suspension_analysis.json")

if __name__ == "__main__":
    import os
    main()
