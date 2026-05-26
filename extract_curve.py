#!/usr/bin/env python3
import struct
import sys
import json
import csv

def extract_curve_data(filepath):
    """Extract meaningful curve data from .curve files"""
    with open(filepath, 'rb') as f:
        data = f.read()
    
    print(f"Extracting curve data from {filepath}")
    print(f"File size: {len(data)} bytes")
    
    # Skip first 8 bytes, parse 16-byte entries
    offset = 8
    points = []
    
    while offset + 16 <= len(data):
        entry_data = data[offset:offset+16]
        
        # Extract Y value from bytes 4-7 (this seems to contain the actual data)
        y = struct.unpack('<f', entry_data[4:8])[0]
        
        # For X, we'll use incremental values since the original X seems to be missing
        # or encoded in the header. Start from 0 and increment by 1 for each point.
        x = len(points)  # Simple incremental index
        
        # Only add if Y is not zero (to filter out empty entries)
        if abs(y) > 1e-10:  # Filter out very small values that are essentially zero
            points.append((x, y))
        
        offset += 16
    
    print(f"Found {len(points)} non-zero data points")
    
    if points:
        y_values = [p[1] for p in points]
        print(f"Y range: {min(y_values):.6f} to {max(y_values):.6f}")
        
        # Display data
        print("\nCurve data points:")
        for i, (x, y) in enumerate(points[:20]):  # Show first 20
            print(f"  Point {i}: x={x}, y={y:.6f}")
        
        if len(points) > 20:
            print(f"  ... and {len(points) - 20} more points")
        
        # Save to multiple formats
        base_name = filepath.rsplit('.', 1)[0]
        
        # CSV format
        csv_file = f"{base_name}_extracted.csv"
        with open(csv_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['index', 'value'])
            for i, (x, y) in enumerate(points):
                writer.writerow([x, y])
        print(f"\nSaved CSV to {csv_file}")
        
        # JSON format
        json_file = f"{base_name}_extracted.json"
        with open(json_file, 'w') as f:
            json.dump({
                "source_file": filepath,
                "num_points": len(points),
                "y_range": [min(y_values), max(y_values)],
                "points": [{"index": x, "value": y} for x, y in points]
            }, f, indent=2)
        print(f"Saved JSON to {json_file}")
        
        # Simple text format for easy reading
        txt_file = f"{base_name}_extracted.txt"
        with open(txt_file, 'w') as f:
            f.write(f"# Curve data extracted from {filepath}\n")
            f.write(f"# Points: {len(points)}, Y range: [{min(y_values):.6f}, {max(y_values):.6f}]\n")
            f.write("# Index, Value\n")
            for x, y in points:
                f.write(f"{x}, {y:.6f}\n")
        print(f"Saved text format to {txt_file}")
        
        return points
    else:
        print("No non-zero data points found")
        return []

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python extract_curve.py <curve_file>")
        print("Example: python extract_curve.py header_power.curve")
        sys.exit(1)
    
    extract_curve_data(sys.argv[1])
