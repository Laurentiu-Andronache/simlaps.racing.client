# Assetto Corsa Evo Car Tuning Capabilities

## Summary
- **Total Cars Analyzed**: 64
- **High Tuning Cars (4+ capabilities)**: 30
- **Medium Tuning Cars (2-3 capabilities)**: 33  
- **Low Tuning Cars (1 capability)**: 0
- **No Tuning Cars**: 1 (dummycar)

## Tuning Categories

### High Tuning Cars (4+ capabilities)
These cars have extensive tuning options including aerodynamics, engine, electronics, and often suspension or aftermarket parts.

**Examples:**
- **ks_porsche_992_gt3_rs**: Aero, Suspension, Engine, Electronics, General Tuning
  - 10 aero curves (wings, body, diffuser)
  - 3 damper curves (front cup settings)
  - 3 engine curves (power, throttle)
  - 11 electronics curves (ABD, 4WS, controllers)
  
- **ks_dallara_exp**: Aero, Engine, Electronics, General Tuning
  - 10 aero curves (wings, body, diffuser)
  - 3 engine curves (torque, throttle, turbo)
  - 8 electronics curves (EBB, lock controllers)

- **ks_porsche_992_gt3_cup**: Aero, Suspension, Engine, Electronics, General Tuning, Aftermarket
  - Full GT3 racing car with comprehensive tuning

### Medium Tuning Cars (2-3 capabilities)
These cars have basic tuning options, typically aerodynamics and engine.

**Examples:**
- **ks_abarth_695_biposto**: Aero, Engine, General Tuning
  - 6 aero curves (wings)
  - 3 engine curves (power, throttle, turbo)
  - Basic electronics controllers

- **ks_toyota_gr86**: Aero, Engine, Electronics, General Tuning
  - 6 aero curves
  - 2 engine curves
  - 2 electronics controllers

- **ks_mazda_mx5_na**: Aero, Engine, General Tuning
  - 5 aero curves
  - 5 engine curves (multiple power options)
  - No advanced electronics

## Detailed Tuning Options

### Aerodynamics (Aero)
- **Wing Curves**: Front/rear wing angle of attack (CL/CD coefficients)
- **Body Curves**: Ride height effects on aerodynamics
- **Diffuser Curves**: Diffuser efficiency curves
- **Controller Curves**: Active aerodynamics speed controllers

### Suspension
- **Damper Curves**: Bump/rebound damping settings
- **Coilover Curves**: Spring rate and damping characteristics
- **ARB Curves**: Anti-roll bar settings

### Engine
- **Power Curves**: Engine torque/power delivery
- **Throttle Curves**: Throttle response mapping
- **Turbo Curves**: Turbo boost control and wastegate

### Electronics
- **ABD (Automatic Brake Differential)**: Various ABD mappings
- **TC (Traction Control)**: Slip and traction control
- **EBB (Engine Brake Blend)**: Engine braking control
- **Controllers**: Various electronic control units

### General Tuning
- **Shift Profiles**: Upshift/downshift characteristics
- **Auto-blip**: Throttle blip on downshifts
- **Coast**: Engine braking characteristics

## Car Categories by Tuning Level

### High Performance / Racing Cars
- Porsche GT3 series (992 GT3 RS, GT3 Cup, GT3 R)
- Ferrari racing variants (296 GT3, 488 Challenge Evo)
- Lamborghini racing variants (Huracan STO, ST EVO2)
- BMW M racing variants (M4 GT3, M2 CS Racing)
- Dallara EXP

### High-End Road Cars
- Ferrari road cars (296 GTB, Daytona SP3, SF-25)
- Lamborghini road cars
- Audi high-performance models
- BMW M series

### Sports Cars / Hot Hatches
- Toyota GR86
- Alpine A110 variants
- Mazda MX-5 variants
- Abarth 695
- Volkswagen Golf GTI

### Classic Cars
- Classic BMW M3
- Classic Porsche 911
- Classic Ford Escort
- Classic Lancia Delta

## Usage Recommendations

### For Maximum Tuning Flexibility
Choose from the **High Tuning** category:
- `ks_porsche_992_gt3_rs`
- `ks_porsche_992_gt3_cup` 
- `ks_dallara_exp`
- `ks_ferrari_296_gt3`

### For Moderate Tuning
Choose from **Medium Tuning** category:
- `ks_toyota_gr86`
- `ks_abarth_695_biposto`
- `ks_mazda_mx5_na`

### For Simple Setup Changes
Even medium tuning cars allow basic aerodynamic and engine adjustments.

## File Extraction
Use the provided tools to extract and analyze curve data:
```bash
# Extract all curve files from KSPKG
python extract.py -i "path/to/content.kspkg" -a

# Analyze specific curve file
python extract_curve.py "path/to/file.curve"
```

This analysis provides a comprehensive overview of which cars offer which tuning capabilities in Assetto Corsa Evo.
