#!/usr/bin/env python3
import struct
import sys
import json

def parse_curve_file(filepath):
    """Parse .curve file with proper structure detection"""
    with open(filepath, 'rb') as f:
        data = f.read()
    
    print(f"Parsing {filepath}")
    print(f"File size: {len(data)} bytes")
    
    # Most curve files seem to follow this pattern:
    # [8-byte header][16-byte entry] repeated
    # Where each 16-byte entry contains: [8-byte unknown][4-byte x][4-byte y]
    
    if len(data) < 8:
        print("File too small")
        return
    
    # Skip first 8 bytes, then parse 16-byte entries
    offset = 8
    points = []
    
    while offset + 16 <= len(data):
        # Skip 8 bytes (unknown purpose)
        # Read x and y as floats
        x = struct.unpack('<f', data[offset+8:offset+12])[0]
        y = struct.unpack('<f', data[offset+12:offset+16])[0]
        points.append((x, y))
        offset += 16
    
    print(f"Found {len(points)} data points")
    
    if points:
        # Analyze the data to infer what x and y represent
        x_values = [p[0] for p in points]
        y_values = [p[1] for p in points]
        
        print(f"X range: {min(x_values):.2f} to {max(x_values):.2f}")
        print(f"Y range: {min(y_values):.2f} to {max(y_values):.2f}")
        
        # Try to determine if this is sorted
        x_sorted = all(x_values[i] <= x_values[i+1] for i in range(len(x_values)-1))
        y_sorted = all(y_values[i] <= y_values[i+1] for i in range(len(y_values)-1))
        
        print(f"X values sorted: {x_sorted}")
        print(f"Y values sorted: {y_sorted}")
        
        # Display first few points
        print("\nFirst 10 points:")
        for i, (x, y) in enumerate(points[:10]):
            print(f"  {i}: x={x:.6f}, y={y:.6f}")
        
        # Generate a simple CSV for external analysis
        csv_file = f"{filepath}.csv"
        with open(csv_file, 'w') as f:
            f.write("x,y\n")
            for x, y in points:
                f.write(f"{x},{y}\n")
        print(f"\nSaved CSV to {csv_file}")
        
        # Save as JSON too
        json_file = f"{filepath}.json"
        with open(json_file, 'w') as f:
            json.dump({
                "file": filepath,
                "num_points": len(points),
                "x_range": [min(x_values), max(x_values)],
                "y_range": [min(y_values), max(y_values)],
                "x_sorted": x_sorted,
                "y_sorted": y_sorted,
                "points": points
            }, f, indent=2)
        print(f"Saved JSON to {json_file}")
        
        return points
    else:
        print("No valid data points found")
        return None

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python curve_parser.py <curve_file>")
        sys.exit(1)
    
    parse_curve_file(sys.argv[1])
