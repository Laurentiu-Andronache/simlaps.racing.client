# AC Evo AI Coaching Prompt

Copy and paste this into ChatGPT, Claude, or any AI assistant:

```
You are an expert motorsport race engineer and driving coach with deep knowledge of
Laguna Seca (11-Corner Layout). Analyse the telemetry data below from an Assetto Corsa Evo session
and give specific, actionable coaching feedback grounded in the numbers provided.

SESSION CONTEXT:
- Track:          Laguna Seca (11-Corner Layout)

SESSION OVERVIEW:
- Total laps analysed: 5
- Best lap:   #2  1:41.60
- Worst lap:  #1  1:46.20
- Delta best/worst: 4.60s
- Top speed: 212.7 km/h

LAP-BY-LAP SUMMARY:
  Lap 1: 1:46.20  max 212.7 km/h  avg 121.5 km/h ← WORST
  Lap 2: 1:41.60  max 210.3 km/h  avg 126.5 km/h ← BEST
  Lap 3: 1:41.80  max 209.0 km/h  avg 126.6 km/h
  Lap 4: 1:42.20  max 211.0 km/h  avg 125.9 km/h
  Lap 5: 1:42.60  max 206.1 km/h  avg 125.3 km/h

CORNER-BY-CORNER ANALYSIS:
(entry/apex/exit speeds in km/h; segment time = frames in corner / hz)

--- Andretti Hairpin 1 (Corner 1) ---
  Apex speed range: 16.5 km/h  🟠 MEDIUM
  Apex speeds:  Lap 1: 108.5,  Lap 2: 92.0,  Lap 3: 107.5,  Lap 4: 96.1,  Lap 5: 92.1
  Highest apex: 108.5 km/h (Lap 1)
  Lowest apex:  92.0 km/h (Lap 2)
  Fastest segment: Lap 3  2.20s
  Slowest segment: Lap 1  2.40s
  Entry  — Lap 3: 115.0  |  Lap 1: 168.3  |  Δ -53.3 km/h
  Apex   — Lap 3: 107.5  |  Lap 1: 108.5  |  Δ -1.0 km/h
  Exit   — Lap 3: 108.2  |  Lap 1: 108.5  |  Δ -0.3 km/h
  Segment delta: +0.20s
  Likely issue: Throttle application point varies — losing drive on exit

--- Andretti Hairpin 2 (Corner 2) ---
  Apex speed range: 44.5 km/h  🔴 HIGH
  Apex speeds:  Lap 1: 60.6,  Lap 2: 93.5,  Lap 3: 105.1,  Lap 4: 99.8,  Lap 5: 98.0
  Highest apex: 105.1 km/h (Lap 3)
  Lowest apex:  60.6 km/h (Lap 1)
  Fastest segment: Lap 2  2.80s
  Slowest segment: Lap 1  3.00s
  Entry  — Lap 2: 93.5  |  Lap 1: 104.2  |  Δ -10.7 km/h
  Apex   — Lap 2: 93.5  |  Lap 1: 60.6  |  Δ +32.9 km/h
  Exit   — Lap 2: 118.7  |  Lap 1: 60.6  |  Δ +58.1 km/h
  Segment delta: +0.20s
  Likely issue: Throttle application point varies — losing drive on exit

--- Turn 3 (Corner 3) ---
  Apex speed range: 6.0 km/h  🟢 LOW
  Apex speeds:  Lap 1: 115.4,  Lap 2: 118.4,  Lap 3: 115.2,  Lap 4: 112.8,  Lap 5: 118.8
  Highest apex: 118.8 km/h (Lap 5)
  Lowest apex:  112.8 km/h (Lap 4)
  Fastest segment: Lap 4  3.80s
  Slowest segment: Lap 1  4.00s
  Entry  — Lap 4: 113.5  |  Lap 1: 147.2  |  Δ -33.7 km/h
  Apex   — Lap 4: 112.8  |  Lap 1: 115.4  |  Δ -2.6 km/h
  Exit   — Lap 4: 143.6  |  Lap 1: 115.4  |  Δ +28.3 km/h
  Segment delta: +0.20s
  Likely issue: Throttle application point varies — losing drive on exit

--- Turn 4 (Corner 4) ---
  Apex speed range: 60.8 km/h  🔴 HIGH
  Apex speeds:  Lap 1: 129.8,  Lap 2: 69.1,  Lap 3: 78.3,  Lap 4: 69.6,  Lap 5: 76.6
  Highest apex: 129.8 km/h (Lap 1)
  Lowest apex:  69.1 km/h (Lap 2)
  Fastest segment: Lap 2  3.80s
  Slowest segment: Lap 1  4.00s
  Entry  — Lap 2: 171.1  |  Lap 1: 164.2  |  Δ +6.9 km/h
  Apex   — Lap 2: 69.1  |  Lap 1: 129.8  |  Δ -60.8 km/h
  Exit   — Lap 2: 69.1  |  Lap 1: 129.8  |  Δ -60.8 km/h
  Segment delta: +0.20s
  Likely issue: Braking inconsistency — arriving at different speeds

--- Turn 5 (Corner 5) ---
  Apex speed range: 24.0 km/h  🟠 MEDIUM
  Apex speeds:  Lap 1: 87.1,  Lap 2: 111.1,  Lap 3: 100.4,  Lap 4: 93.6,  Lap 5: 106.7
  Highest apex: 111.1 km/h (Lap 2)
  Lowest apex:  87.1 km/h (Lap 1)
  Fastest segment: Lap 3  4.20s
  Slowest segment: Lap 1  4.60s
  Entry  — Lap 3: 117.5  |  Lap 1: 87.1  |  Δ +30.4 km/h
  Apex   — Lap 3: 100.4  |  Lap 1: 87.1  |  Δ +13.3 km/h
  Exit   — Lap 3: 100.4  |  Lap 1: 117.5  |  Δ -17.1 km/h
  Segment delta: +0.40s
  Likely issue: Braking inconsistency — arriving at different speeds

--- Turn 6 (Corner 6) ---
  Apex speed range: 9.0 km/h  🟢 LOW
  Apex speeds:  Lap 1: 108.1,  Lap 2: 106.8,  Lap 3: 104.8,  Lap 4: 99.1,  Lap 5: 101.2
  Highest apex: 108.1 km/h (Lap 1)
  Lowest apex:  99.1 km/h (Lap 4)
  Fastest segment: Lap 2  4.40s
  Slowest segment: Lap 1  4.60s
  Entry  — Lap 2: 111.2  |  Lap 1: 152.2  |  Δ -41.1 km/h
  Apex   — Lap 2: 106.8  |  Lap 1: 108.1  |  Δ -1.4 km/h
  Exit   — Lap 2: 145.0  |  Lap 1: 109.0  |  Δ +36.0 km/h
  Segment delta: +0.20s
  Likely issue: Throttle application point varies — losing drive on exit

--- Corkscrew Left (Corner 7) ---
  Apex speed range: 9.6 km/h  🟢 LOW
  Apex speeds:  Lap 1: 62.0,  Lap 2: 71.0,  Lap 3: 66.0,  Lap 4: 61.9,  Lap 5: 71.5
  Highest apex: 71.5 km/h (Lap 5)
  Lowest apex:  61.9 km/h (Lap 4)
  Fastest segment: Lap 2  2.20s
  Slowest segment: Lap 1  2.40s
  Entry  — Lap 2: 71.0  |  Lap 1: 62.0  |  Δ +9.0 km/h
  Apex   — Lap 2: 71.0  |  Lap 1: 62.0  |  Δ +9.0 km/h
  Exit   — Lap 2: 105.7  |  Lap 1: 79.1  |  Δ +26.5 km/h
  Segment delta: +0.20s
  Likely issue: Throttle application point varies — losing drive on exit

--- Corkscrew Right (Corner 8) ---
  Apex speed range: 24.4 km/h  🟠 MEDIUM
  Apex speeds:  Lap 1: 82.1,  Lap 2: 106.5,  Lap 3: 100.0,  Lap 4: 93.1,  Lap 5: 101.3
  Highest apex: 106.5 km/h (Lap 2)
  Lowest apex:  82.1 km/h (Lap 1)
  Fastest segment: Lap 3  2.80s
  Slowest segment: Lap 1  3.00s
  Entry  — Lap 3: 100.0  |  Lap 1: 82.1  |  Δ +17.9 km/h
  Apex   — Lap 3: 100.0  |  Lap 1: 82.1  |  Δ +17.9 km/h
  Exit   — Lap 3: 138.3  |  Lap 1: 125.4  |  Δ +12.9 km/h
  Segment delta: +0.20s
  Likely issue: Mixed — entry and exit both vary

--- Rainey Curve (Corner 9) ---
  Apex speed range: 52.6 km/h  🔴 HIGH
  Apex speeds:  Lap 1: 185.3,  Lap 2: 132.7,  Lap 3: 156.0,  Lap 4: 173.5,  Lap 5: 138.7
  Highest apex: 185.3 km/h (Lap 1)
  Lowest apex:  132.7 km/h (Lap 2)
  Fastest segment: Lap 2  4.40s
  Slowest segment: Lap 1  4.60s
  Entry  — Lap 2: 194.6  |  Lap 1: 185.3  |  Δ +9.2 km/h
  Apex   — Lap 2: 132.7  |  Lap 1: 185.3  |  Δ -52.6 km/h
  Exit   — Lap 2: 132.7  |  Lap 1: 191.5  |  Δ -58.7 km/h
  Segment delta: +0.20s
  Likely issue: Braking inconsistency — arriving at different speeds

--- Turn 10 (Corner 10) ---
  Apex speed range: 6.6 km/h  🟢 LOW
  Apex speeds:  Lap 1: 78.8,  Lap 2: 81.0,  Lap 3: 77.6,  Lap 4: 78.4,  Lap 5: 74.4
  Highest apex: 81.0 km/h (Lap 2)
  Lowest apex:  74.4 km/h (Lap 5)
  Fastest segment: Lap 2  3.80s
  Slowest segment: Lap 1  4.00s
  Entry  — Lap 2: 81.5  |  Lap 1: 78.8  |  Δ +2.7 km/h
  Apex   — Lap 2: 81.0  |  Lap 1: 78.8  |  Δ +2.2 km/h
  Exit   — Lap 2: 113.6  |  Lap 1: 126.1  |  Δ -12.5 km/h
  Segment delta: +0.20s
  Likely issue: Braking inconsistency — arriving at different speeds

--- Turn 11 (Corner 11) ---
  Apex speed range: 14.4 km/h  🟢 LOW
  Apex speeds:  Lap 1: 87.6,  Lap 2: 87.0,  Lap 3: 101.0,  Lap 4: 101.5,  Lap 5: 101.2
  Highest apex: 101.5 km/h (Lap 4)
  Lowest apex:  87.0 km/h (Lap 2)
  Fastest segment: Lap 2  5.80s
  Slowest segment: Lap 1  6.20s
  Entry  — Lap 2: 87.0  |  Lap 1: 87.6  |  Δ -0.5 km/h
  Apex   — Lap 2: 87.0  |  Lap 1: 87.6  |  Δ -0.5 km/h
  Exit   — Lap 2: 128.9  |  Lap 1: 115.9  |  Δ +13.0 km/h
  Segment delta: +0.40s
  Likely issue: Throttle application point varies — losing drive on exit

TIME LOSS RANKING (worst → best, by segment time delta):
  Turn 11                        +0.40s
  Turn 5                         +0.40s
  Turn 4                         +0.20s
  Turn 3                         +0.20s
  Turn 10                        +0.20s
  Corkscrew Right                +0.20s
  Andretti Hairpin 2             +0.20s
  Corkscrew Left                 +0.20s
  Andretti Hairpin 1             +0.20s
  Turn 6                         +0.20s
  Rainey Curve                   +0.20s

OVERALL TIME ANALYSIS:
  Best lap:  #2  1:41.60
  Worst lap: #1  1:46.20
  Delta: 4.60s

============================================================
COACHING REQUEST:

Using the telemetry data above, provide specific, actionable coaching feedback:

1. TIME LOSS PRIORITIES
   Which corners are costing the most time and why?
   Use the segment time deltas and entry/exit speed data, not just apex speed.

2. CORNER TECHNIQUE — for each high/medium variation corner:
   - Brake point and release
   - Turn-in and apex
   - Throttle pickup point and exit
   - What the entry/exit delta pattern tells you about the driver's habit

3. CONSISTENCY DIAGNOSIS
   For corners with HIGH variation, diagnose whether this is a
   reference-point problem, confidence problem, or technique problem.

4. SINGLE BIGGEST IMPROVEMENT
   What one change would yield the most lap time?
   Be specific: not 'brake later' but 'at the Corkscrew, your entry speed
   varies by X km/h — pick the 150m board as a fixed brake reference.'

Ground every recommendation in the specific km/h and time figures above.
============================================================
```
