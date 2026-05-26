#!/usr/bin/env python3
import os
import json
from pathlib import Path

def analyze_tuning_capabilities(cars_dir):
    """Analyze specific tuning capabilities for each car"""
    
    # Define tuning-related folder patterns
    tuning_indicators = {
        'aero': ['aero', 'wing'],
        'suspension': ['dampers', 'coilover', 'suspension', 'spring'],
        'engine': ['engine', 'turbo', 'throttle', 'power'],
        'drivetrain': ['gearbox', 'diff', 'drivetrain'],
        'brakes': ['brake'],
        'electronics': ['abd', 'tc', 'abs', 'controller'],
        'general_tuning': ['tuning', 'tuningparts', 'curves', 'setup'],
        'aftermarket': ['aftermarket']
    }
    
    cars_capabilities = {}
    
    for car_dir in Path(cars_dir).iterdir():
        if not car_dir.is_dir():
            continue
            
        car_name = car_dir.name
        capabilities = {key: False for key in tuning_indicators.keys()}
        
        # Check for curve files which indicate tunable parameters
        curve_files = []
        for root, dirs, files in os.walk(car_dir):
            for file in files:
                if file.endswith('.curve'):
                    curve_files.append(file.lower())
                    rel_path = os.path.relpath(root, car_dir)
                    parent_folder = os.path.basename(rel_path)
                    
                    # Check curve file names for tuning indicators
                    file_lower = file.lower()
                    if any(keyword in file_lower for keyword in ['power', 'torque', 'throttle', 'turbo']):
                        capabilities['engine'] = True
                    if any(keyword in file_lower for keyword in ['damper', 'coil', 'spring']):
                        capabilities['suspension'] = True
                    if any(keyword in file_lower for keyword in ['wing', 'aero', 'cl', 'cd']):
                        capabilities['aero'] = True
                    if any(keyword in file_lower for keyword in ['abd', 'tc', 'slip']):
                        capabilities['electronics'] = True
        
        # Check folder structure
        for root, dirs, files in os.walk(car_dir):
            for dir_name in dirs:
                dir_lower = dir_name.lower()
                
                for capability, patterns in tuning_indicators.items():
                    if any(pattern in dir_lower for pattern in patterns):
                        capabilities[capability] = True
        
        # Special checks for specific files
        has_setup_ini = any('setup.ini' in f.lower() for root, dirs, files in os.walk(car_dir) for f in files)
        if has_setup_ini:
            capabilities['general_tuning'] = True
        
        cars_capabilities[car_name] = capabilities
    
    return cars_capabilities

def categorize_cars(cars_capabilities):
    """Categorize cars by their tuning level"""
    
    high_tuning = []
    medium_tuning = []
    low_tuning = []
    no_tuning = []
    
    for car_name, capabilities in cars_capabilities.items():
        tuning_score = sum(capabilities.values())
        
        # High tuning: 4+ capabilities
        if tuning_score >= 4:
            high_tuning.append((car_name, capabilities))
        # Medium tuning: 2-3 capabilities
        elif tuning_score >= 2:
            medium_tuning.append((car_name, capabilities))
        # Low tuning: 1 capability
        elif tuning_score == 1:
            low_tuning.append((car_name, capabilities))
        # No tuning: 0 capabilities
        else:
            no_tuning.append((car_name, capabilities))
    
    return high_tuning, medium_tuning, low_tuning, no_tuning

def main():
    cars_dir = "C:/Storage/my documents/sim-laps-client/extracted/content/cars"
    
    print("Analyzing car tuning capabilities...")
    cars_capabilities = analyze_tuning_capabilities(cars_dir)
    
    high_tuning, medium_tuning, low_tuning, no_tuning = categorize_cars(cars_capabilities)
    
    print(f"\n=== TUNING CAPABILITY SUMMARY ===")
    print(f"High tuning cars (4+ capabilities): {len(high_tuning)}")
    print(f"Medium tuning cars (2-3 capabilities): {len(medium_tuning)}")
    print(f"Low tuning cars (1 capability): {len(low_tuning)}")
    print(f"No tuning cars: {len(no_tuning)}")
    
    print(f"\n=== HIGH TUNING CARS ===")
    for car_name, capabilities in high_tuning:
        active_caps = [cap for cap, has in capabilities.items() if has]
        print(f"{car_name}: {', '.join(active_caps)}")
    
    print(f"\n=== MEDIUM TUNING CARS ===")
    for car_name, capabilities in medium_tuning:
        active_caps = [cap for cap, has in capabilities.items() if has]
        print(f"{car_name}: {', '.join(active_caps)}")
    
    print(f"\n=== LOW TUNING CARS ===")
    for car_name, capabilities in low_tuning:
        active_caps = [cap for cap, has in capabilities.items() if has]
        print(f"{car_name}: {', '.join(active_caps)}")
    
    print(f"\n=== NO TUNING CARS ===")
    for car_name, capabilities in no_tuning:
        print(f"{car_name}")
    
    # Save detailed analysis
    output = {
        "summary": {
            "high_tuning": len(high_tuning),
            "medium_tuning": len(medium_tuning),
            "low_tuning": len(low_tuning),
            "no_tuning": len(no_tuning)
        },
        "categories": {
            "high_tuning": [{"name": name, "capabilities": caps} for name, caps in high_tuning],
            "medium_tuning": [{"name": name, "capabilities": caps} for name, caps in medium_tuning],
            "low_tuning": [{"name": name, "capabilities": caps} for name, caps in low_tuning],
            "no_tuning": [{"name": name, "capabilities": caps} for name, caps in no_tuning]
        },
        "detailed": cars_capabilities
    }
    
    with open("car_tuning_capabilities.json", 'w') as f:
        json.dump(output, f, indent=2)
    
    print(f"\nDetailed analysis saved to car_tuning_capabilities.json")

if __name__ == "__main__":
    main()
