#!/usr/bin/env python3
import struct
import sys
import json

def analyze_curve_properly(filepath):
    """Final comprehensive curve analyzer with correct data interpretation"""
    
    with open(filepath, 'rb') as f:
        data = f.read()
    
    print(f"Analyzing: {filepath}")
    print(f"File size: {len(data)} bytes")
    
    # Parse 16-byte entries
    offset = 8
    points = []
    
    while offset + 16 <= len(data):
        entry = data[offset:offset+16]
        
        # Extract the main data value from bytes 4-7
        try:
            main_value = struct.unpack('<f', entry[4:8])[0]
            
            # Use entry index as X coordinate (since we don't have explicit X values)
            x_index = (offset - 8) // 16
            points.append((x_index, main_value))
            
        except:
            pass
        
        offset += 16
    
    print(f"Found {len(points)} data points")
    
    if points:
        x_values = [p[0] for p in points]
        y_values = [p[1] for p in points]
        
        print(f"X range: {min(x_values)} to {max(x_values)}")
        print(f"Y range: {min(y_values):.6f} to {max(y_values):.6f}")
        
        # Determine curve type based on values
        curve_type = detect_curve_type(y_values)
        print(f"Detected curve type: {curve_type}")
        
        # Show detailed analysis
        print(f"\nDetailed data:")
        print(f"{'#':4s} {'X':6s} {'Y':12s} {'Interpretation':20s}")
        print("-" * 50)
        
        for i, (x, y) in enumerate(points[:20]):  # Show first 20
            interpretation = interpret_value(y, curve_type)
            print(f"{i:4d} {x:6d} {y:12.6f} {interpretation:20s}")
        
        if len(points) > 20:
            print(f"... and {len(points) - 20} more points")
        
        # Special analysis for specific curve types
        if curve_type == "Aerodynamic Coefficient":
            analyze_aero_curve(points)
        elif curve_type == "Damper Force":
            analyze_damper_curve(points)
        elif curve_type == "Engine Power":
            analyze_power_curve(points)
        
        return points
    
    return []

def detect_curve_type(values):
    """Detect the type of curve based on value ranges"""
    min_val = min(values)
    max_val = max(values)
    
    # Aerodynamic coefficients (CL, CD) typically range -2 to 2
    if -2 <= min_val <= 2 and -2 <= max_val <= 2:
        return "Aerodynamic Coefficient"
    
    # Damper forces can range widely but typically -10000 to 10000 N
    elif -10000 <= min_val <= 10000 and -10000 <= max_val <= 10000:
        return "Damper Force"
    
    # Engine power/torque values
    elif max_val > 100:
        return "Engine Power"
    
    # Throttle response (0-1)
    elif 0 <= min_val and max_val <= 1.5:
        return "Throttle Response"
    
    # Generic
    else:
        return "Generic Curve"

def interpret_value(value, curve_type):
    """Interpret a value based on curve type"""
    if curve_type == "Aerodynamic Coefficient":
        if value < -0.5:
            return "Strong Downforce"
        elif value < 0:
            return "Mild Downforce"
        elif value < 0.5:
            return "Mild Lift/Neutral"
        else:
            return "Strong Lift"
    
    elif curve_type == "Damper Force":
        if value < -1000:
            return "High Compression"
        elif value < -100:
            return "Medium Compression"
        elif value < 0:
            return "Low Compression"
        elif value < 100:
            return "Low Rebound"
        elif value < 1000:
            return "Medium Rebound"
        else:
            return "High Rebound"
    
    elif curve_type == "Engine Power":
        if value < 100:
            return "Low Power"
        elif value < 300:
            return "Medium Power"
        elif value < 500:
            return "High Power"
        else:
            return "Very High Power"
    
    else:
        return f"Value: {value:.3f}"

def analyze_aero_curve(points):
    """Specific analysis for aerodynamic curves"""
    print(f"\nAerodynamic Analysis:")
    
    # Find min/max CL values
    cl_values = [p[1] for p in points]
    min_cl = min(cl_values)
    max_cl = max(cl_values)
    
    print(f"  CL range: {min_cl:.4f} to {max_cl:.4f}")
    print(f"  CL variation: {(max_cl - min_cl):.4f}")
    
    # Find zero lift angle (approximate)
    zero_crossings = []
    for i in range(len(points) - 1):
        if points[i][1] <= 0 <= points[i+1][1] or points[i][1] >= 0 >= points[i+1][1]:
            zero_crossings.append(i)
    
    if zero_crossings:
        print(f"  Zero lift near point: {zero_crossings[0]}")
    
    # Find maximum downforce
    max_downforce_point = min(points, key=lambda p: p[1])
    print(f"  Max downforce: {max_downforce_point[1]:.4f} at point {max_downforce_point[0]}")

def analyze_damper_curve(points):
    """Specific analysis for damper curves"""
    print(f"\nDamper Analysis:")
    
    # Separate compression and rebound (if we can determine direction)
    # Since we don't have explicit velocities, we'll analyze the pattern
    
    force_values = [p[1] for p in points]
    min_force = min(force_values)
    max_force = max(force_values)
    
    print(f"  Force range: {min_force:.1f} to {max_force:.1f} N")
    print(f"  Force variation: {(max_force - min_force):.1f} N")
    
    # Count positive vs negative forces
    positive_forces = sum(1 for f in force_values if f > 0)
    negative_forces = sum(1 for f in force_values if f < 0)
    
    print(f"  Positive forces: {positive_forces} points")
    print(f"  Negative forces: {negative_forces} points")
    
    if negative_forces > 0 and positive_forces > 0:
        print(f"  This appears to be a full damper curve (compression + rebound)")
    elif negative_forces > 0:
        print(f"  This appears to be compression-only")
    else:
        print(f"  This appears to be rebound-only")

def analyze_power_curve(points):
    """Specific analysis for engine power curves"""
    print(f"\nEngine Power Analysis:")
    
    power_values = [p[1] for p in points]
    max_power = max(power_values)
    min_power = min(power_values)
    
    print(f"  Power range: {min_power:.1f} to {max_power:.1f}")
    print(f"  Power band: {(max_power - min_power):.1f}")
    
    # Find peak power
    peak_power_point = max(points, key=lambda p: p[1])
    print(f"  Peak power: {peak_power_point[1]:.1f} at point {peak_power_point[0]}")
    
    # Calculate power curve characteristics
    if len(points) > 10:
        # Low end power (first 25%)
        low_end = points[:len(points)//4]
        low_end_power = sum(p[1] for p in low_end) / len(low_end)
        
        # High end power (last 25%)
        high_end = points[-len(points)//4:]
        high_end_power = sum(p[1] for p in high_end) / len(high_end)
        
        print(f"  Low end avg: {low_end_power:.1f}")
        print(f"  High end avg: {high_end_power:.1f}")
        print(f"  Power delivery: {'Linear' if abs(high_end_power - low_end_power) < max_power * 0.1 else 'Progressive'}")

def main():
    if len(sys.argv) != 2:
        print("Usage: python final_curve_analyzer.py <curve_file>")
        print("Example: python final_curve_analyzer.py wing_front_aoa_cl.curve")
        sys.exit(1)
    
    analyze_curve_properly(sys.argv[1])

if __name__ == "__main__":
    main()
