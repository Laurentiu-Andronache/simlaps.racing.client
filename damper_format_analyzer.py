#!/usr/bin/env python3
import struct
import sys

def analyze_damper_format(filepath):
    """Deep analysis of damper curve format"""
    
    with open(filepath, 'rb') as f:
        data = f.read()
    
    print(f"Analyzing damper format: {filepath}")
    print(f"File size: {len(data)} bytes")
    
    # Show hex dump
    print(f"\nHex dump (first 128 bytes):")
    hex_data = data[:128].hex()
    for i in range(0, len(hex_data), 32):
        chunk = hex_data[i:i+32]
        formatted = ' '.join([chunk[j:j+2] for j in range(0, len(chunk), 2)])
        print(f"{i:08x}  {formatted}")
    
    print(f"\nAnalyzing 16-byte entries:")
    offset = 8
    entry_num = 0
    
    while offset + 16 <= len(data) and entry_num < 10:
        entry_data = data[offset:offset+16]
        print(f"\nEntry {entry_num} (offset {offset:08x}):")
        print(f"Raw: {entry_data.hex()}")
        
        # Try all possible float interpretations
        print("Float interpretations:")
        for i in range(0, 16, 4):
            for j in range(i+4, 16, 4):
                try:
                    val1 = struct.unpack('<f', entry_data[i:i+4])[0]
                    val2 = struct.unpack('<f', entry_data[j:j+4])[0]
                    print(f"  bytes {i:2d}-{i+3:2d}, {j:2d}-{j+3:2d}: {val1:12.6f}, {val2:12.6f}")
                except:
                    pass
        
        # Try integer interpretations
        print("Integer interpretations:")
        for i in range(0, 16, 4):
            try:
                val = struct.unpack('<i', entry_data[i:i+4])[0]
                print(f"  bytes {i:2d}-{i+3:2d}: {val:12d}")
            except:
                pass
        
        offset += 16
        entry_num += 1

def compare_damper_files():
    """Compare multiple damper files to understand patterns"""
    
    cars_dir = "C:/Storage/my documents/sim-laps-client/extracted/content/cars"
    
    damper_files = [
        "ks_porsche_992_gt3_rs/data/dampers/damperfrontcup.curve",
        "ks_porsche_992_gt3_rs/data/dampers/damperfrontcup_1.curve", 
        "ks_porsche_992_gt3_rs/data/dampers/damperfrontcup_2.curve"
    ]
    
    for file_path in damper_files:
        full_path = f"{cars_dir}/{file_path}"
        print(f"\n{'='*80}")
        print(f"FILE: {file_path}")
        print('='*80)
        
        analyze_damper_format(full_path)

if __name__ == "__main__":
    import os
    if len(sys.argv) > 1:
        analyze_damper_format(sys.argv[1])
    else:
        compare_damper_files()
