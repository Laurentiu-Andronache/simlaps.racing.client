#!/usr/bin/env python3
import struct
import sys
import numpy as np

def detect_curve_format(filepath):
    """Comprehensive format detection for curve files"""
    
    with open(filepath, 'rb') as f:
        data = f.read()
    
    print(f"Detecting format for: {filepath}")
    print(f"File size: {len(data)} bytes")
    
    # Show hex dump
    print(f"\nHex dump:")
    hex_data = data.hex()
    for i in range(0, min(len(hex_data), 128), 32):
        chunk = hex_data[i:i+32]
        formatted = ' '.join([chunk[j:j+2] for j in range(0, len(chunk), 2)])
        print(f"{i:08x}  {formatted}")
    
    # Try different interpretations
    print(f"\nTrying different data interpretations:")
    
    # Method 1: Standard 16-byte entries, try all float positions
    print(f"\n1. Standard 16-byte entries - all float combinations:")
    offset = 8
    entry_count = 0
    
    while offset + 16 <= len(data) and entry_count < 3:
        entry = data[offset:offset+16]
        print(f"  Entry {entry_count} (offset {offset:08x}): {entry.hex()}")
        
        valid_combinations = []
        for i in range(0, 16, 4):
            for j in range(i+4, 16, 4):
                try:
                    val1 = struct.unpack('<f', entry[i:i+4])[0]
                    val2 = struct.unpack('<f', entry[j:j+4])[0]
                    
                    # Check if values look reasonable
                    if (abs(val1) < 1000 and abs(val2) < 10000) or \
                       (abs(val1) < 10 and abs(val2) < 100) or \
                       (abs(val1) < 1 and abs(val2) < 1000):
                        valid_combinations.append((i, j, val1, val2))
                except:
                    pass
        
        for i, j, val1, val2 in valid_combinations:
            print(f"    bytes {i:2d}-{i+3:2d}, {j:2d}-{j+3:2d}: {val1:12.6f}, {val2:12.6f}")
        
        if not valid_combinations:
            print("    No reasonable float combinations found")
        
        entry_count += 1
        offset += 16
    
    # Method 2: Try integer interpretations
    print(f"\n2. Integer interpretations:")
    offset = 8
    entry_count = 0
    
    while offset + 16 <= len(data) and entry_count < 2:
        entry = data[offset:offset+16]
        print(f"  Entry {entry_count}:")
        
        for i in range(0, 16, 4):
            try:
                int_val = struct.unpack('<i', entry[i:i+4])[0]
                uint_val = struct.unpack('<I', entry[i:i+4])[0]
                print(f"    bytes {i:2d}-{i+3:2d}: int={int_val:12d}, uint={uint_val:12u}")
            except:
                pass
        
        entry_count += 1
        offset += 16
    
    # Method 3: Try to find patterns
    print(f"\n3. Pattern analysis:")
    
    # Look for repeating patterns
    if len(data) >= 32:
        chunk1 = data[8:24]
        chunk2 = data[24:40]
        chunk3 = data[40:56]
        
        print(f"  First 3 entries:")
        print(f"    Entry 0: {chunk1.hex()}")
        print(f"    Entry 1: {chunk2.hex()}")
        print(f"    Entry 2: {chunk3.hex()}")
        
        # Check for similarities
        if chunk1[:8] == chunk2[:8] == chunk3[:8]:
            print(f"    First 8 bytes are identical across entries")
        if chunk1[8:] == chunk2[8:] == chunk3[8:]:
            print(f"    Last 8 bytes are identical across entries")
    
    # Method 4: Try to extract meaningful data using known patterns
    print(f"\n4. Known pattern extraction:")
    
    # Try angle of attack vs CL pattern for aero
    offset = 8
    aero_points = []
    
    while offset + 16 <= len(data):
        entry = data[offset:offset+16]
        
        # Try to extract angle (degrees) and coefficient
        for i in range(0, 16, 4):
            for j in range(i+4, 16, 4):
                try:
                    angle = struct.unpack('<f', entry[i:i+4])[0]
                    coeff = struct.unpack('<f', entry[j:j+4])[0]
                    
                    # Check for reasonable angle ranges (-20 to +20 degrees)
                    if -20 <= angle <= 20 and -2 <= coeff <= 2:
                        aero_points.append((angle, coeff))
                except:
                    pass
        
        offset += 16
    
    if aero_points:
        print(f"  Found {len(aero_points)} potential aero points:")
        for angle, coeff in aero_points[:10]:
            print(f"    Angle: {angle:6.2f}°, Coefficient: {coeff:6.4f}")
    
    # Method 5: Try damper velocity-force pattern
    offset = 8
    damper_points = []
    
    while offset + 16 <= len(data):
        entry = data[offset:offset+16]
        
        # Try to extract velocity (m/s) and force (N)
        for i in range(0, 16, 4):
            for j in range(i+4, 16, 4):
                try:
                    velocity = struct.unpack('<f', entry[i:i+4])[0]
                    force = struct.unpack('<f', entry[j:j+4])[0]
                    
                    # Check for reasonable damper ranges
                    if -1 <= velocity <= 1 and -10000 <= force <= 10000:
                        damper_points.append((velocity, force))
                except:
                    pass
        
        offset += 16
    
    if damper_points:
        print(f"  Found {len(damper_points)} potential damper points:")
        for vel, force in damper_points[:10]:
            direction = "Compression" if vel < 0 else "Rebound" if vel > 0 else "Zero"
            print(f"    {direction:11s}: Velocity {vel:6.4f} m/s, Force {force:8.1f} N")

def main():
    if len(sys.argv) != 2:
        print("Usage: python curve_format_detector.py <curve_file>")
        sys.exit(1)
    
    detect_curve_format(sys.argv[1])

if __name__ == "__main__":
    main()
