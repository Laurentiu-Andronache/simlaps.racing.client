# AC Evo Telemetry Analysis Tools

## 🚀 Complete Workflow

**Step 1: Capture Telemetry**
```bash
python 1-capture.py --wait --hz 1
```
Records your driving session to JSONL format.

**Step 2: Analyze & Visualize**
```bash
python 2-analyze.py session.jsonl
```
Creates interactive HTML report with lap comparison.

**Step 3: Get AI Coaching**
```bash
python 3-create-prompt.py session.jsonl
```
Generates AI coaching prompt for ChatGPT/Claude.

## 📁 Files

| File | Purpose |
|------|----------|
| `1-capture.py` | Record telemetry from AC Evo |
| `2-analyze.py` | Interactive HTML analysis |
| `3-create-prompt.py` | Generate AI coaching prompts |

## 🎯 Usage

1. **Start AC Evo** and drive a session
2. **Capture data** with the recording script
3. **Analyze results** with HTML dashboard
4. **Get coaching** by pasting AI prompt into ChatGPT/Claude

## 📊 What You Get

- **Track maps** with speed visualization
- **Lap comparison** across multiple sessions  
- **Corner analysis** with entry/apex/exit speeds
- **AI coaching** based on your actual telemetry data

## ⚙️ Options

- `--hz 1.0`: Capture frequency (Hz)
- `--wait`: Wait for AC Evo to start
- `--out filename.jsonl`: Custom output file

**Capture → Analyze → Coach → Improve**
