#!/usr/bin/env python3
import struct
import sys
import json

def extract_curve_data(filepath):
    """Extract curve data from .curve file"""
    with open(filepath, 'rb') as f:
        data = f.read()
    
    print(f"Analyzing {filepath}")
    print(f"File size: {len(data)} bytes")
    
    # Try different parsing approaches
    approaches = []
    
    # Approach 1: Direct float pairs throughout the file
    if len(data) % 8 == 0:
        num_floats = len(data) // 8
        pairs = []
        for i in range(0, num_floats, 2):
            if i + 1 < num_floats:
                x = struct.unpack('<f', data[i*4:(i+1)*4])[0]
                y = struct.unpack('<f', data[(i+1)*4:(i+2)*4])[0]
                pairs.append((x, y))
        
        approaches.append(("Direct float pairs", pairs))
    
    # Approach 2: Skip 8-byte header, then float pairs
    if len(data) >= 16:
        num_entries = (len(data) - 8) // 16
        pairs = []
        for i in range(num_entries):
            offset = 8 + i * 16
            if offset + 8 <= len(data):
                x = struct.unpack('<f', data[offset:offset+4])[0]
                y = struct.unpack('<f', data[offset+4:offset+8])[0]
                pairs.append((x, y))
        
        approaches.append(("8-byte header + 16-byte entries", pairs))
    
    # Approach 3: Look for RPM-like values in headers, extract as curve points
    if len(data) >= 16:
        num_entries = len(data) // 16
        pairs = []
        for i in range(num_entries):
            offset = i * 16
            if offset + 16 <= len(data):
                # Try to extract RPM from header (first 4 bytes as int)
                rpm = struct.unpack('<I', data[offset:offset+4])[0]
                # Extract power value (last 4 bytes as float)
                power = struct.unpack('<f', data[offset+12:offset+16])[0]
                pairs.append((rpm, power))
        
        approaches.append(("Header as RPM, last 4 bytes as power", pairs))
    
    # Approach 4: Try to find valid RPM ranges (1000-10000) and power ranges
    if len(data) >= 16:
        num_entries = len(data) // 16
        pairs = []
        for i in range(num_entries):
            offset = i * 16
            if offset + 16 <= len(data):
                # Try different positions for RPM and power
                for rpm_pos in [0, 4, 8, 12]:
                    for power_pos in [0, 4, 8, 12]:
                        if rpm_pos != power_pos:
                            rpm = struct.unpack('<f', data[offset+rpm_pos:offset+rpm_pos+4])[0]
                            power = struct.unpack('<f', data[offset+power_pos:offset+power_pos+4])[0]
                            # Check if RPM looks reasonable (1000-10000) and power reasonable (0-1000)
                            if 500 <= rpm <= 15000 and 0 <= power <= 2000:
                                pairs.append((rpm, power))
                                break
        
        approaches.append(("Search for reasonable RPM/power ranges", pairs))
    
    # Display results
    for name, pairs in approaches:
        print(f"\n=== {name} ===")
        print(f"Found {len(pairs)} data points")
        if pairs:
            print("First 10 points:")
            for i, (x, y) in enumerate(pairs[:10]):
                print(f"  {i}: x={x:.2f}, y={y:.2f}")
            
            # Save to JSON for further analysis
            output_file = f"{filepath}.curve_data.json"
            with open(output_file, 'w') as f:
                json.dump({"method": name, "points": pairs}, f, indent=2)
            print(f"Saved to {output_file}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python curve_extractor.py <curve_file>")
        sys.exit(1)
    
    extract_curve_data(sys.argv[1])
