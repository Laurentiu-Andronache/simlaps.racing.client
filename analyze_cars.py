#!/usr/bin/env python3
import os
import json
from pathlib import Path

def get_car_tuning_options(cars_dir):
    """Analyze car folders to identify available tuning options"""
    cars_data = {}
    
    for car_dir in Path(cars_dir).iterdir():
        if not car_dir.is_dir():
            continue
            
        car_name = car_dir.name
        tuning_categories = set()
        
        # Recursively find all subdirectories
        for root, dirs, files in os.walk(car_dir):
            for dir_name in dirs:
                tuning_categories.add(dir_name.lower())
        
        # Also check for specific file patterns that indicate tuning options
        for root, dirs, files in os.walk(car_dir):
            for file_name in files:
                if file_name.endswith('.curve'):
                    # Extract category from file path
                    rel_path = os.path.relpath(root, car_dir)
                    category = rel_path.replace(os.sep, '/').split('/')[0] if rel_path != '.' else 'root'
                    tuning_categories.add(category.lower())
        
        cars_data[car_name] = sorted(list(tuning_categories))
    
    return cars_data

def analyze_tuning_patterns(cars_data):
    """Identify common tuning patterns across cars"""
    all_categories = set()
    for categories in cars_data.values():
        all_categories.update(categories)
    
    # Count frequency of each category
    category_counts = {}
    for categories in cars_data.values():
        for category in categories:
            category_counts[category] = category_counts.get(category, 0) + 1
    
    return sorted(all_categories), category_counts

def main():
    cars_dir = "C:/Storage/my documents/sim-laps-client/extracted/content/cars"
    
    print("Analyzing car tuning options...")
    cars_data = get_car_tuning_options(cars_dir)
    
    all_categories, category_counts = analyze_tuning_patterns(cars_data)
    
    # Print summary
    print(f"\nFound {len(cars_data)} cars")
    print(f"Total tuning categories: {len(all_categories)}")
    
    print("\n=== TUNING CATEGORIES (by frequency) ===")
    for category, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True):
        percentage = (count / len(cars_data)) * 100
        print(f"{category:20} : {count:3d} cars ({percentage:5.1f}%)")
    
    print("\n=== DETAILED CAR ANALYSIS ===")
    for car_name, categories in sorted(cars_data.items()):
        # Filter out common non-tuning folders
        tuning_categories = [cat for cat in categories if cat not in ['data', 'ui', 'sounds', 'textures', 'models', 'shaders', 'common']]
        
        if tuning_categories:
            print(f"\n{car_name}:")
            for category in sorted(tuning_categories):
                print(f"  - {category}")
        else:
            print(f"\n{car_name}: No obvious tuning options")
    
    # Save to JSON
    output_file = "car_tuning_analysis.json"
    with open(output_file, 'w') as f:
        json.dump({
            "summary": {
                "total_cars": len(cars_data),
                "total_categories": len(all_categories),
                "category_frequency": category_counts
            },
            "cars": cars_data
        }, f, indent=2)
    
    print(f"\nSaved detailed analysis to {output_file}")

if __name__ == "__main__":
    main()
