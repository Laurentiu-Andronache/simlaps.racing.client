#!/usr/bin/env python3
import os
import json
from pathlib import Path

def get_detailed_tuning_options(car_dir):
    """Get detailed tuning options for a specific car"""
    options = {
        'aero_wings': [],
        'aero_body': [],
        'suspension_dampers': [],
        'suspension_coilovers': [],
        'engine_power': [],
        'engine_throttle': [],
        'engine_turbo': [],
        'electronics_abd': [],
        'electronics_tc': [],
        'electronics_controllers': [],
        'drivetrain': [],
        'brakes': [],
        'general_curves': [],
        'setup_files': []
    }
    
    # Analyze curve files
    for root, dirs, files in os.walk(car_dir):
        for file_name in files:
            if file_name.endswith('.curve'):
                file_lower = file_name.lower()
                rel_path = os.path.relpath(root, car_dir)
                
                # Categorize curve files
                if 'wing' in file_lower and ('cl' in file_lower or 'cd' in file_lower):
                    options['aero_wings'].append(f"{rel_path}/{file_name}")
                elif 'aero' in file_lower or ('cl' in file_lower and 'wing' not in file_lower):
                    options['aero_body'].append(f"{rel_path}/{file_name}")
                elif 'damper' in file_lower:
                    options['suspension_dampers'].append(f"{rel_path}/{file_name}")
                elif 'coil' in file_lower:
                    options['suspension_coilovers'].append(f"{rel_path}/{file_name}")
                elif 'power' in file_lower or 'torque' in file_lower:
                    options['engine_power'].append(f"{rel_path}/{file_name}")
                elif 'throttle' in file_lower:
                    options['engine_throttle'].append(f"{rel_path}/{file_name}")
                elif 'turbo' in file_lower:
                    options['engine_turbo'].append(f"{rel_path}/{file_name}")
                elif 'abd' in file_lower:
                    options['electronics_abd'].append(f"{rel_path}/{file_name}")
                elif any(keyword in file_lower for keyword in ['tc', 'slip', 'traction']):
                    options['electronics_tc'].append(f"{rel_path}/{file_name}")
                elif 'controller' in file_lower:
                    options['electronics_controllers'].append(f"{rel_path}/{file_name}")
                elif 'brake' in file_lower:
                    options['brakes'].append(f"{rel_path}/{file_name}")
                else:
                    options['general_curves'].append(f"{rel_path}/{file_name}")
    
    # Check for setup files
    for root, dirs, files in os.walk(car_dir):
        for file_name in files:
            if file_name.lower().startswith('setup') or file_name.lower().endswith('.ini'):
                rel_path = os.path.relpath(root, car_dir)
                options['setup_files'].append(f"{rel_path}/{file_name}")
    
    return options

def main():
    cars_dir = "C:/Storage/my documents/sim-laps-client/extracted/content/cars"
    
    print("Generating detailed tuning analysis...")
    
    # Focus on a few representative cars from different categories
    sample_cars = [
        'ks_dallara_exp',  # High tuning
        'ks_abarth_695_biposto',  # Medium tuning  
        'ks_porsche_992_gt3_rs',  # High tuning
        'ks_toyota_gr86',  # Medium tuning
        'ks_mazda_mx5_na'  # Medium tuning
    ]
    
    detailed_analysis = {}
    
    for car_name in sample_cars:
        car_path = os.path.join(cars_dir, car_name)
        if os.path.exists(car_path):
            print(f"\n=== {car_name.upper()} ===")
            options = get_detailed_tuning_options(car_path)
            detailed_analysis[car_name] = options
            
            for category, files in options.items():
                if files:
                    print(f"\n{category.replace('_', ' ').title()}:")
                    for file_path in files[:5]:  # Show first 5 files
                        print(f"  - {file_path}")
                    if len(files) > 5:
                        print(f"  ... and {len(files) - 5} more files")
    
    # Create a summary table
    print(f"\n=== TUNING CAPABILITY SUMMARY ===")
    print(f"{'Car':<25} {'Aero':<6} {'Susp':<6} {'Engine':<8} {'Elect':<8} {'Total':<6}")
    print("-" * 70)
    
    for car_name in sample_cars:
        if car_name in detailed_analysis:
            options = detailed_analysis[car_name]
            aero_count = len(options['aero_wings'] + options['aero_body'])
            susp_count = len(options['suspension_dampers'] + options['suspension_coilovers'])
            engine_count = len(options['engine_power'] + options['engine_throttle'] + options['engine_turbo'])
            elect_count = len(options['electronics_abd'] + options['electronics_tc'] + options['electronics_controllers'])
            total = aero_count + susp_count + engine_count + elect_count
            
            print(f"{car_name:<25} {aero_count:<6} {susp_count:<6} {engine_count:<8} {elect_count:<8} {total:<6}")
    
    # Save detailed analysis
    with open("detailed_tuning_analysis.json", 'w') as f:
        json.dump(detailed_analysis, f, indent=2)
    
    print(f"\nDetailed analysis saved to detailed_tuning_analysis.json")

if __name__ == "__main__":
    main()
