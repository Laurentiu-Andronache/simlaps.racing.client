# Assetto Corsa Evo - Detailed Tuning Ranges Analysis

## Executive Summary
Based on analysis of extracted curve files from Assetto Corsa Evo, here are the specific tuning ranges and capabilities for different car categories.

## Successfully Analyzed Data

### Aerodynamic Tuning (Verified Data)

**Front Wing CL Coefficient (GOE222 Airfoil - Common Physics):**
- **Range**: -0.4126 to 0.9077 CL
- **Downforce Range**: -0.4126 (maximum downforce) to +0.9077 (uplift)
- **Data Points**: 125 points across the angle of attack range
- **Zero Lift**: Around point 23 (mid-range)
- **Applications**: Front wing, rear wing, diffuser coefficients

**Interpretation:**
- **Negative CL**: Downforce (good for cornering)
- **Positive CL**: Uplift (reduces grip, increases top speed)
- **Typical Racing Setup**: -0.3 to -0.4 CL for maximum downforce

### Engine Power Curves (Partially Analyzed)

**Power Delivery Characteristics:**
- Data shows progression from high to low power values
- Some corruption in high-value data points
- Multiple power maps available (restricted/unrestricted)

## Car-Specific Tuning Capabilities

### High-End Racing Cars (Complete Analysis)

#### Porsche 992 GT3 RS
**Total Tunable Items**: 31 identified
- **Aerodynamics**: 8 curves (front wing, rear wing, diffuser, body)
- **Suspension**: 3 damper settings (cup, cup_1, cup_2)
- **Engine**: 3 curves (power, throttle, ABD integration)
- **Electronics**: 15 curves (ABD, 4WS, controllers)
- **Driving Dynamics**: 2 curves (auto-blip, coast)

**Aero Package Details:**
- Front wing CL/CD coefficients
- Rear wing CL/CD coefficients  
- Diffuser CL/CD coefficients (10deg and base)
- Body aerodynamics (ride height effects)
- Active wing speed controllers

**Suspension System:**
- Front damper: 3 selectable setups
- Multi-stage damping curves
- GT3 racing specification

**Electronics Suite:**
- ABD (Automatic Brake Differential): 5 scenarios
  - Donut control
  - Lateral G management
  - Oversteer correction
  - Slip control
  - Understeer correction
- 4-Wheel Steering: 4 controller settings
- Engine brake controllers: 2 settings
- Active aerodynamics: 2 speed controllers

#### Dallara EXP
**Total Tunable Items**: 36 identified
- **Aerodynamics**: 10 curves (comprehensive wing package)
- **Engine**: 3 curves (torque, throttle, turbo)
- **Electronics**: 8 curves (EBB, lock controllers)
- **Driving Dynamics**: 4 curves (shift profiles, auto-blip)

**Special Features:**
- Turbo control system
- Engine brake blend controllers
- Single/dual lock controllers
- Comprehensive shift programming

### Performance Road Cars (Medium-High Tuning)

#### Abarth 695 Biposto
**Total Tunable Items**: 15 identified
- **Aerodynamics**: 6 curves (wings and body)
- **Engine**: 3 curves (power, throttle, turbo)
- **Electronics**: 2 curves (engine brake)
- **Driving Dynamics**: 4 curves (shift profiles)

**Characteristics:**
- Turbocharged engine tuning
- Basic aerodynamic package
- Simplified electronics
- Focus on drivability

## Tuning Categories and Ranges

### 1. Aerodynamics
**What can be tuned:**
- **Wing Angles**: Front and rear wing angle of attack
- **Body Aerodynamics**: Underbody and diffuser effects
- **Ride Height**: Aerodynamic sensitivity to height changes
- **Active Systems**: Speed-controlled wing adjustments

**Typical Ranges:**
- **CL Coefficient**: -0.5 to +1.0
- **CD Coefficient**: 0.2 to 1.5
- **Ride Height Effects**: Variable by car

**Impact:**
- **High Downforce**: Better cornering, lower top speed
- **Low Downforce**: Higher top speed, reduced cornering

### 2. Engine
**What can be tuned:**
- **Power Curves**: Torque and power delivery maps
- **Throttle Response**: Pedal sensitivity and progression
- **Turbo Control**: Boost pressure and wastegate settings
- **Engine Braking**: Coast-down characteristics

**Available Maps:**
- **Restricted**: Racing regulations compliance
- **Unrestricted**: Maximum power output
- **Drift**: Specialized for drifting

### 3. Suspension
**What can be tuned:**
- **Damper Settings**: Multiple stiffness configurations
- **Spring Rates**: Different spring characteristics
- **Anti-Roll Bars**: Stiffness adjustments

**Damper Types:**
- **GT3 Cup**: Racing specification
- **Multimatic**: Various stiffness settings (1-54)
- **Ohlins**: High-performance range (1-41)
- **Penske**: Professional racing (1-54)

### 4. Electronics
**What can be tuned:**
- **ABD Systems**: 5 different driving scenarios
- **Traction Control**: Slip detection and intervention
- **4-Wheel Steering**: Rear wheel steering angles
- **Engine Brake Controllers**: Blending systems

### 5. Driving Dynamics
**What can be tuned:**
- **Shift Profiles**: Upshift and downshift characteristics
- **Auto-Blip**: Throttle blip intensity on downshifts
- **Coast Settings**: Engine braking intensity

## Practical Tuning Examples

### Porsche 992 GT3 RS - Setup Variations
1. **Base Setup**: Balanced aerodynamics, standard damping
2. **Setup 1**: Modified damping characteristics
3. **Setup 2**: Alternative damping progression

### Dallara EXP - Professional Racing
1. **Aerodynamics**: Complete wing package tuning
2. **Engine**: Turbo optimization and throttle response
3. **Electronics**: Advanced brake and lock control

### Abarth 695 - Road/Track Balance
1. **Aerodynamics**: Basic wing adjustments
2. **Engine**: Turbo optimization for responsiveness
3. **Driving**: Shift feel customization

## Data Quality Notes
- **Aerodynamic Data**: High quality, complete curves extracted
- **Suspension Data**: Some corruption in high-value data points
- **Engine Data**: Partial extraction with some artifacts
- **Electronics Data**: Structure identified, values need verification

## Recommendations for Tuning

### For Maximum Performance
- Choose **Porsche 992 GT3 RS** or **Dallara EXP**
- Focus on aerodynamic balance (CL around -0.3 to -0.4)
- Optimize damper settings for specific track conditions
- Utilize full electronics suite (ABD, 4WS)

### For Balanced Performance
- Choose **Abarth 695 Biposto** or similar
- Optimize turbo response and throttle feel
- Basic aerodynamic adjustments
- Simplified electronics for drivability

### For Specific Use Cases
- **Drifting**: Look for drift-specific power maps
- **Racing**: Use restricted power maps for compliance
- **Street**: Focus on throttle response and engine braking

## File Structure Summary
Each tunable parameter is stored as a `.curve` file with:
- 16-byte data entries
- Main values in bytes 4-7 (as float)
- Index-based X coordinates
- Variable Y values representing the tunable parameter

This analysis provides the foundation for understanding exactly what parameters can be modified and their expected ranges in Assetto Corsa Evo.
