#!/usr/bin/env python3
"""
Create AI prompt from ac_lap_analyzer-good.py analysis
"""

import json
import sys
import os

# Import the analysis functions from 2-analyze.py
import importlib.util
script_dir = os.path.dirname(os.path.abspath(__file__))
analyzer_path = os.path.join(script_dir, "2-analyze.py")
spec = importlib.util.spec_from_file_location("analyzer", analyzer_path)
analyzer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyzer)

def create_ai_prompt(jsonl_file):
    """Use the existing analyzer to create AI coaching prompt"""
    
    print(f"Analyzing {jsonl_file}...")
    
    # Run the existing analysis
    data = analyzer.analyze(jsonl_file)
    
    if not data or not data.get('laps'):
        print("No valid laps found in the data!")
        return
    
    # Extract key insights from the analysis
    laps = data['laps']
    ref_corners = data['ref_corners']
    corner_speeds = data['corner_speeds']
    
    # Build the AI prompt
    prompt = f"""You are an expert motorsport race engineer and driving coach analysing telemetry data from an Assetto Corsa Evo session.

SESSION OVERVIEW:
- Track: Laguna Seca
- Total laps analyzed: {len(laps)}
- Best lap time: {min(lap['lap_time_str'] for lap in laps)}
- Worst lap time: {max(lap['lap_time_str'] for lap in laps)}
- Top speed achieved: {max(lap['max_speed'] for lap in laps):.1f} km/h

LAP-BY-LAP PERFORMANCE:
"""
    
    for lap in laps:
        prompt += f"Lap {lap['lap_num']}: {lap['lap_time_str']} - Max speed: {lap['max_speed']:.1f} km/h - {len(lap['corners'])} corners detected\n"
    
    prompt += f"\nCORNER-BY-CORNER ANALYSIS:\n"
    
    # Add corner analysis
    for corner in ref_corners:
        corner_id = corner['id']
        if corner_id in corner_speeds:
            speeds = corner_speeds[corner_id]
            if speeds:
                lap_nums = list(speeds.keys())
                apex_speeds = list(speeds.values())
                
                prompt += f"\nCorner {corner_id}:\n"
                prompt += f"  Apex speeds: {', '.join([f'Lap {lap}: {speed:.1f} km/h' for lap, speed in speeds.items()])}\n"
                prompt += f"  Best apex: {max(apex_speeds):.1f} km/h\n"
                prompt += f"  Worst apex: {min(apex_speeds):.1f} km/h\n"
                
                if len(apex_speeds) > 1:
                    consistency = (max(apex_speeds) - min(apex_speeds))
                    prompt += f"  Consistency: {consistency:.1f} km/h variation\n"
    
    # Find biggest time loss areas
    if len(laps) >= 2:
        best_lap = min(laps, key=lambda x: x['lap_time_s'])
        worst_lap = max(laps, key=lambda x: x['lap_time_s'])
        time_diff = worst_lap['lap_time_s'] - best_lap['lap_time_s']
        
        prompt += f"\nTIME ANALYSIS:\n"
        prompt += f"Time difference between best and worst lap: {time_diff:.2f} seconds\n"
        prompt += f"Best lap: #{best_lap['lap_num']} ({best_lap['lap_time_str']})\n"
        prompt += f"Worst lap: #{worst_lap['lap_num']} ({worst_lap['lap_time_str']})\n"
    
    prompt += f"""
COACHING REQUEST:
Based on this telemetry data, provide specific, actionable feedback for:

1. **Where am I losing the most time?** Identify the corners with the biggest speed variations between my best and worst laps.

2. **Corner-specific techniques** - For each corner where there's significant variation, provide concrete tips on brake points, turn-in, and exit speed.

3. **Consistency issues** - Which corners show the most variation and what does this indicate about my driving?

4. **Priority areas** - What single aspect of my driving should I focus on to make the biggest improvement?

Be specific with data points and provide actionable techniques for Laguna Seca.
"""
    
    return prompt

def main():
    if len(sys.argv) != 2:
        print("Usage: python create_ai_prompt.py telemetry.jsonl")
        sys.exit(1)
    
    jsonl_file = sys.argv[1]
    
    if not os.path.exists(jsonl_file):
        print(f"File not found: {jsonl_file}")
        sys.exit(1)
    
    # Create the AI prompt
    prompt = create_ai_prompt(jsonl_file)
    
    # Save to file
    output_file = jsonl_file.replace('.jsonl', '_ai_coaching_prompt.md')
    
    with open(output_file, 'w') as f:
        f.write("# AC Evo AI Coaching Prompt\n\n")
        f.write("Copy and paste this into ChatGPT, Claude, or any AI assistant:\n\n")
        f.write("```\n")
        f.write(prompt)
        f.write("\n```\n")
    
    print(f"✅ AI coaching prompt saved to: {output_file}")
    print(f"📊 Prompt length: {len(prompt)} characters")
    print(f"🎯 Ready to paste into any AI assistant!")

if __name__ == "__main__":
    main()
