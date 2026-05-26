#!/usr/bin/env python3
import struct
import sys

def analyze_curve_file(filepath):
    """Analyze the structure of a .curve file"""
    with open(filepath, 'rb') as f:
        data = f.read()
    
    print(f"File size: {len(data)} bytes")
    print(f"Hex dump (first 128 bytes):")
    print(data[:128].hex())
    print()
    
    # Try to detect pattern - look for repeating structures
    print("Analyzing structure...")
    
    # Look for 16-byte patterns (common in game data)
    num_entries = len(data) // 16
    remainder = len(data) % 16
    
    print(f"Number of 16-byte entries: {num_entries}")
    print(f"Remaining bytes: {remainder}")
    print()
    
    if num_entries > 0:
        # Parse as float pairs (8 bytes each) after 8-byte header
        print("Parsing 16-byte entries as [8-byte header][float x][float y]:")
        for i in range(min(num_entries, 10)):  # Show first 10 entries
            offset = i * 16
            header = data[offset:offset+8]
            x = struct.unpack('<f', data[offset+8:offset+12])[0]
            y = struct.unpack('<f', data[offset+12:offset+16])[0]
            print(f"Entry {i}: header={header.hex()}, x={x:.6f}, y={y:.6f}")
        
        if num_entries > 10:
            print(f"... and {num_entries - 10} more entries")
    
    print()
    # Look for patterns in the first 8 bytes of each entry
    if num_entries > 0:
        print("First 8 bytes of each entry (headers):")
        for i in range(min(num_entries, 5)):
            offset = i * 16
            header = data[offset:offset+8]
            print(f"Entry {i}: {header.hex()}")
    
    # Show remaining bytes if any
    if remainder > 0:
        print(f"Remaining {remainder} bytes: {data[-remainder:].hex()}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python analyze_curve.py <curve_file>")
        sys.exit(1)
    
    analyze_curve_file(sys.argv[1])
