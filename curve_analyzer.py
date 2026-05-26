#!/usr/bin/env python3
import struct
import sys

def analyze_curve_structure(filepath):
    """Deep analysis of curve file structure"""
    with open(filepath, 'rb') as f:
        data = f.read()
    
    print(f"Analyzing {filepath}")
    print(f"File size: {len(data)} bytes")
    print("\nHex dump (first 64 bytes):")
    hex_dump = data[:64].hex()
    for i in range(0, len(hex_dump), 32):
        print(f"{i:08x}  {hex_dump[i:i+32]}")
    
    print(f"\nAnalyzing 16-byte entries:")
    
    # Skip first 8 bytes, analyze 16-byte entries
    offset = 8
    entry_num = 0
    
    while offset + 16 <= len(data) and entry_num < 5:
        entry_data = data[offset:offset+16]
        print(f"\nEntry {entry_num} (offset {offset:08x}):")
        print(f"Raw: {entry_data.hex()}")
        
        # Try all possible float positions
        float_positions = []
        for i in range(0, 16, 4):
            for j in range(i+4, 16, 4):
                try:
                    val1 = struct.unpack('<f', entry_data[i:i+4])[0]
                    val2 = struct.unpack('<f', entry_data[j:j+4])[0]
                    float_positions.append((i, j, val1, val2))
                except:
                    pass
        
        print("Possible float pairs:")
        for i, j, val1, val2 in float_positions:
            print(f"  bytes {i}-{i+3}, {j}-{j+3}: {val1:.6f}, {val2:.6f}")
        
        offset += 16
        entry_num += 1
    
    print(f"\nTotal entries: {(len(data) - 8) // 16}")
    print(f"Remaining bytes: {len(data) - 8 - ((len(data) - 8) // 16) * 16}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python curve_analyzer.py <curve_file>")
        sys.exit(1)
    
    analyze_curve_structure(sys.argv[1])
