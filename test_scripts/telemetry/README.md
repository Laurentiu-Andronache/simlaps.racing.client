# AC Evo Telemetry Analysis Tools

## 🚀 Complete Workflow

**Step 1: Capture Telemetry**
```bash
python 1-capture.py --wait --hz 1
```
Records your driving session to JSONL format.

**Step 2: Analyze, Visualize & Get AI Coaching**
```bash
python 2-analyze.py session.jsonl
```
Creates interactive HTML report with lap comparison.

**Get AI Coaching (optional)**
```bash
python 2-analyze.py session.jsonl --ai-prompt --car "Porsche 911 GT3" --notes "struggling with consistency"
```
Generates AI coaching prompt for ChatGPT/Claude.

## 📁 Files

| File | Purpose |
|------|----------|
| `1-capture.py` | Record telemetry from AC Evo |
| `2-analyze.py` | Interactive HTML analysis + AI coaching prompts |

## 🎯 Usage

1. **Start AC Evo** and drive a session
2. **Capture data** with the recording script
3. **Analyze results** with HTML dashboard
4. **Get coaching** (optional) by adding `--ai-prompt` flag

## 📊 What You Get

- **Track maps** with speed visualization
- **Lap comparison** across multiple sessions  
- **Corner analysis** with entry/apex/exit speeds
- **Input traces** (brake/throttle/gear) per lap
- **AI coaching** based on your actual telemetry data (optional)

## ⚙️ Options

### 1-capture.py
- `--hz 1.0`: Capture frequency (Hz)
- `--wait`: Wait for AC Evo to start
- `--out filename.jsonl`: Custom output file

### 2-analyze.py
- `--track spa`: Override track detection
- `--config gp`: Track configuration (e.g., gp, indy)
- `--ai-prompt`: Also generate AI coaching prompt
- `--car "Car Name"`: Car name for AI context
- `--driver beginner|intermediate|advanced`: Driver level
- `--goal "reduce lap time"`: Session goal
- `--notes "free text"`: Additional context for AI
- `--best-ref 2:38.50`: Reference lap time to benchmark

**Capture → Analyze → Coach → Improve**
