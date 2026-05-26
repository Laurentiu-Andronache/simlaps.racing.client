#!/usr/bin/env python3
import os
import json
from pathlib import Path

def analyze_car_tuning_details(cars_dir):
    """Get extremely detailed tuning options for every car"""
    
    all_cars = {}
    
    for car_dir in Path(cars_dir).iterdir():
        if not car_dir.is_dir() or car_dir.name == 'dummycar':
            continue
            
        car_name = car_dir.name
        car_details = {
            'name': car_name,
            'tuning_categories': {},
            'specific_files': {},
            'total_tunable_items': 0
        }
        
        # Analyze all curve files
        curve_files = []
        for root, dirs, files in os.walk(car_dir):
            for file_name in files:
                if file_name.endswith('.curve'):
                    rel_path = os.path.relpath(root, car_dir)
                    full_path = os.path.join(rel_path, file_name)
                    curve_files.append((full_path, file_name.lower()))
        
        # Categorize each curve file
        for file_path, file_lower in curve_files:
            category = categorize_curve_file(file_path, file_lower)
            if category not in car_details['tuning_categories']:
                car_details['tuning_categories'][category] = []
            car_details['tuning_categories'][category].append(file_path)
            
            # Also store in specific_files for easy lookup
            car_details['specific_files'][file_path] = category
            car_details['total_tunable_items'] += 1
        
        # Check for setup files and other tuning-related files
        setup_files = []
        for root, dirs, files in os.walk(car_dir):
            for file_name in files:
                file_lower = file_name.lower()
                if (file_lower.startswith('setup') or 
                    file_lower.endswith('.ini') or
                    'tuning' in file_lower):
                    rel_path = os.path.relpath(root, car_dir)
                    full_path = os.path.join(rel_path, file_name)
                    setup_files.append(full_path)
        
        if setup_files:
            car_details['tuning_categories']['setup_files'] = setup_files
            car_details['total_tunable_items'] += len(setup_files)
        
        all_cars[car_name] = car_details
    
    return all_cars

def categorize_curve_file(file_path, file_name):
    """Categorize a curve file based on its name and path"""
    
    # Check for specific keywords in order of priority
    if 'wing' in file_name and ('cl' in file_name or 'cd' in file_name):
        if 'front' in file_name:
            return 'aero_front_wing_coefficients'
        elif 'rear' in file_name:
            return 'aero_rear_wing_coefficients'
        elif 'diffuser' in file_name:
            return 'aero_diffuser_coefficients'
        elif 'body' in file_name:
            return 'aero_body_coefficients'
        else:
            return 'aero_wing_coefficients'
    
    elif 'aero' in file_name:
        if 'height' in file_name:
            if 'diffuser' in file_name:
                return 'aero_ride_height_diffuser'
            elif 'front' in file_name:
                return 'aero_ride_height_front'
            else:
                return 'aero_ride_height_general'
        elif 'controller' in file_name and 'speed' in file_name:
            return 'aero_active_speed_controller'
        else:
            return 'aero_general'
    
    elif 'damper' in file_name:
        if 'front' in file_name:
            return 'suspension_front_dampers'
        elif 'rear' in file_name:
            return 'suspension_rear_dampers'
        else:
            return 'suspension_dampers_general'
    
    elif 'coil' in file_name or 'coilover' in file_name:
        return 'suspension_coilovers'
    
    elif 'spring' in file_name:
        return 'suspension_springs'
    
    elif 'arb' in file_name or 'antiroll' in file_name:
        return 'suspension_anti_roll_bars'
    
    elif 'power' in file_name or 'torque' in file_name:
        if 'unrestricted' in file_name:
            return 'engine_power_unrestricted'
        elif 'drift' in file_name:
            return 'engine_power_drift'
        else:
            return 'engine_power_standard'
    
    elif 'throttle' in file_name:
        return 'engine_throttle_response'
    
    elif 'turbo' in file_name:
        return 'engine_turbo_control'
    
    elif 'gearbox' in file_name or 'gear' in file_name:
        return 'drivetrain_gearbox'
    
    elif 'diff' in file_name:
        return 'drivetrain_differential'
    
    elif 'brake' in file_name:
        return 'brakes_system'
    
    elif 'abd' in file_name:
        if 'donut' in file_name:
            return 'electronics_abd_donut'
        elif 'latg' in file_name:
            return 'electronics_abd_lateral_g'
        elif 'oversteer' in file_name:
            return 'electronics_abd_oversteer'
        elif 'understeer' in file_name:
            return 'electronics_abd_understeer'
        elif 'slip' in file_name:
            return 'electronics_abd_slip'
        elif 'throttle' in file_name:
            return 'electronics_abd_throttle'
        else:
            return 'electronics_abd_general'
    
    elif 'tc' in file_name or 'traction' in file_name:
        return 'electronics_traction_control'
    
    elif 'abs' in file_name:
        return 'electronics_abs'
    
    elif 'controller' in file_name:
        if 'ebb' in file_name:
            return 'electronics_engine_brake_controller'
        elif 'lock' in file_name:
            return 'electronics_lock_controller'
        elif 'single' in file_name:
            return 'electronics_single_controller'
        elif '4ws' in file_name:
            return 'electronics_four_wheel_steering'
        elif 'turbo' in file_name:
            return 'electronics_turbo_controller'
        else:
            return 'electronics_general_controller'
    
    elif 'shift' in file_name:
        if 'up' in file_name:
            return 'driving_upshift_profile'
        elif 'down' in file_name:
            return 'driving_downshift_profile'
        else:
            return 'driving_shift_profile'
    
    elif 'blip' in file_name:
        return 'driving_auto_blip'
    
    elif 'coast' in file_name:
        return 'driving_engine_brake_coast'
    
    elif 'profile' in file_name:
        return 'driving_general_profile'
    
    # Check path-based categorization
    path_parts = file_path.lower().split('/')
    if 'aero' in path_parts:
        return 'aero_folder_general'
    elif 'dampers' in path_parts:
        return 'suspension_folder_general'
    elif 'curves' in path_parts:
        return 'engine_folder_general'
    elif 'abd' in path_parts:
        return 'electronics_folder_general'
    elif 'controllers' in path_parts:
        return 'electronics_folder_controllers'
    elif '4ws' in path_parts:
        return 'electronics_four_wheel_steering'
    
    return 'general_tuning'

def generate_detailed_report(cars_data):
    """Generate a comprehensive human-readable report"""
    
    report = "# Comprehensive Assetto Corsa Evo Car Tuning Analysis\n\n"
    
    # Group cars by tuning level
    high_tuning = []
    medium_tuning = []
    low_tuning = []
    
    for car_name, details in cars_data.items():
        total_items = details['total_tunable_items']
        categories = len(details['tuning_categories'])
        
        if total_items >= 15 or categories >= 4:
            high_tuning.append((car_name, details))
        elif total_items >= 8 or categories >= 2:
            medium_tuning.append((car_name, details))
        else:
            low_tuning.append((car_name, details))
    
    report += f"## Summary\n"
    report += f"- **High Tuning Cars**: {len(high_tuning)} (15+ tunable items or 4+ categories)\n"
    report += f"- **Medium Tuning Cars**: {len(medium_tuning)} (8+ tunable items or 2+ categories)\n"
    report += f"- **Low Tuning Cars**: {len(low_tuning)} (fewer tunable items)\n\n"
    
    # High tuning cars detailed
    report += "## High Tuning Cars (Detailed)\n\n"
    for car_name, details in sorted(high_tuning):
        report += f"### {car_name.replace('ks_', '').replace('_', ' ').title()}\n"
        report += f"**Total Tunable Items**: {details['total_tunable_items']}\n\n"
        
        for category, files in sorted(details['tuning_categories'].items()):
            report += f"**{category.replace('_', ' ').title()}** ({len(files)} items):\n"
            for file_path in sorted(files):
                filename = file_path.split('/')[-1]
                report += f"  - `{filename}`\n"
            report += "\n"
        
        report += "---\n\n"
    
    # Medium tuning cars summary
    report += "## Medium Tuning Cars (Summary)\n\n"
    for car_name, details in sorted(medium_tuning):
        report += f"**{car_name.replace('ks_', '').replace('_', ' ').title()}**: "
        report += f"{details['total_tunable_items']} items - "
        categories = [cat.replace('_', ' ').title() for cat in details['tuning_categories'].keys()]
        report += f"{', '.join(categories)}\n"
    
    return report

def main():
    cars_dir = "C:/Storage/my documents/sim-laps-client/extracted/content/cars"
    
    print("Generating comprehensive tuning analysis...")
    cars_data = analyze_car_tuning_details(cars_dir)
    
    # Generate detailed report
    report = generate_detailed_report(cars_data)
    
    # Save report
    with open("comprehensive_tuning_report.md", 'w') as f:
        f.write(report)
    
    # Save raw data
    with open("comprehensive_tuning_data.json", 'w') as f:
        json.dump(cars_data, f, indent=2)
    
    print(f"Analysis complete!")
    print(f"Total cars analyzed: {len(cars_data)}")
    print(f"Detailed report saved to: comprehensive_tuning_report.md")
    print(f"Raw data saved to: comprehensive_tuning_data.json")
    
    # Show some stats
    total_items = sum(car['total_tunable_items'] for car in cars_data.values())
    print(f"Total tunable items across all cars: {total_items}")

if __name__ == "__main__":
    main()
