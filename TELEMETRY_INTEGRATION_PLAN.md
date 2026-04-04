# Telemetry Integration Plan

## Overview

Integrate the `test_scripts/telemetry` functionality into the main SimLaps Client application as a unified feature. The telemetry system will capture high-frequency (20Hz) physics data from AC Evo's shared memory during game sessions and automatically generate analysis reports.

---

## User Requirements (Confirmed)

| Requirement | Decision |
|-------------|----------|
| **Capture Mode** | Auto-trigger on session start, stop when session ends |
| **Data Storage** | In-memory during session, optional export for debugging |
| **Analysis Output** | Auto-generate HTML + AI prompt after each session |
| **Output Location** | Configurable directory in settings |
| **UI Integration** | Settings card, status indicator, logs integration |
| **Report Viewing** | "Open Telemetry Location" button opens folder |

---

## Architecture

### Data Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                        GAME SESSION START                           │
│                     (detected by LogParser)                         │
└─────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    TelemetryCapture (new)                            │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  Shared Memory Reader (from 1-capture.py)                   │    │
│  │  - physics region (1024 bytes @ 20Hz)                       │    │
│  │  - graphics region (2048 bytes)                             │    │
│  │  - static region (2048 bytes)                               │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                          │                                          │
│                          ▼                                          │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  In-Memory Buffer                                           │    │
│  │  - List[FrameData] (JSON-serializable)                      │    │
│  │  - Session metadata (track, car, hz, timestamps)            │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      GAME SESSION END                                │
│                   (detected by LogParser)                            │
└─────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    TelemetryAnalyzer (new)                           │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  Analysis Pipeline (from 2-analyze.py)                      │    │
│  │  1. Build track map (dead reckoning + normalized pos)       │    │
│  │  2. Detect lap boundaries                                   │    │
│  │  3. Detect/identify corners                                 │    │
│  │  4. Calculate per-corner speeds                             │    │
│  │  5. Generate HTML report                                    │    │
│  │  6. Generate AI coaching prompt                             │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                          │                                          │
│                          ▼                                          │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  Output Files                                               │    │
│  │  - telemetry_YYYYMMDD_HHMMSS.html                           │    │
│  │  - telemetry_YYYYMMDD_HHMMSS_ai_prompt.txt                  │    │
│  │  (saved to configured output directory)                      │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      In-Memory Buffer                               │
│                      (discarded after analysis)                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## File Changes

### New Files to Create

| File | Purpose |
|------|---------|
| `src/core/telemetry_capture.py` | Shared memory reader, frame capture, session management |
| `src/core/telemetry_analyzer.py` | Lap detection, corner analysis, report generation |
| `src/core/telemetry_decoder.py` | Binary decoder for physics/graphics/static regions |
| `src/core/track_catalog.py` | Track profiles and corner definitions (move from test_scripts) |
| `src/ui/components/telemetry_status.py` | Telemetry status indicator component |

### Files to Modify

| File | Changes |
|------|---------|
| `src/utils/config.py` | Add telemetry settings (enabled, output_path) |
| `src/ui/pages/settings.py` | Add Telemetry settings card |
| `src/ui/pages/home.py` | Add telemetry status indicator, "Open Telemetry Location" button |
| `src/ui/components/debug_logs.py` | Include telemetry-related logs |
| `src/core/log_parser.py` | Add session start/end hooks for telemetry trigger |
| `src/ui/app.py` | Initialize TelemetryCapture, wire up session events |

---

## Detailed Component Specifications

### 1. Configuration (`src/utils/config.py`)

Add to `AppConfig` dataclass:

```python
# Telemetry
telemetry_enabled: bool = True
telemetry_output_path: str = field(
    default_factory=lambda: str(Path.home() / "Documents" / "SimLaps" / "Telemetry")
)
```

---

### 2. TelemetryCapture (`src/core/telemetry_capture.py`)

**Responsibilities:**
- Connect to AC Evo shared memory regions (physics, graphics, static)
- Capture frames at configurable Hz (default 20)
- Store frames in memory during session
- Provide export-to-file capability for debugging
- Signal session start/end to trigger analysis

**Key Classes:**

```python
@dataclass
class FrameData:
    """Single telemetry frame."""
    timestamp: datetime
    frame_number: int
    physics: dict  # Decoded physics data
    graphics: dict  # Decoded graphics data
    static: dict  # Decoded static data

class TelemetryCapture:
    """Manages shared memory capture during game sessions."""
    
    def __init__(self, hz: float = 20.0):
        self._hz = hz
        self._frames: list[FrameData] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None
    
    async def start_capture(self) -> None:
        """Begin capturing telemetry frames."""
    
    async def stop_capture(self) -> list[FrameData]:
        """Stop capture and return captured frames."""
    
    def export_to_jsonl(self, path: str) -> None:
        """Export frames to JSONL for debugging."""
    
    def is_capturing(self) -> bool:
        """Check if currently capturing."""
    
    def get_frame_count(self) -> int:
        """Get number of captured frames."""
```

**Integration Points:**
- Called from `SimLapsApp._on_game_status_change()` when game starts/stops
- Uses `RegionReader` pattern from `1-capture.py`
- Decodes using `telemetry_decoder.py`

---

### 3. TelemetryDecoder (`src/core/telemetry_decoder.py`)

**Responsibilities:**
- Decode binary shared memory regions into Python dicts
- Based on `ac_evo_decoder.py` with AC/ACC structure support
- Handle fallback for unknown structures

**Key Functions:**

```python
def decode_physics(data: bytes) -> dict:
    """Decode physics region bytes to dict."""

def decode_graphics(data: bytes) -> dict:
    """Decode graphics region bytes to dict."""

def decode_static(data: bytes) -> dict:
    """Decode static region bytes to dict."""
```

---

### 4. TelemetryAnalyzer (`src/core/telemetry_analyzer.py`)

**Responsibilities:**
- Process captured frames into analysis data
- Detect laps from normalized position or dead reckoning
- Identify corners (using track catalog or auto-detection)
- Generate HTML report
- Generate AI coaching prompt

**Key Classes:**

```python
@dataclass
class AnalysisResult:
    """Result of telemetry analysis."""
    html_path: str
    ai_prompt_path: str
    laps_detected: int
    best_lap_time: float
    track_name: Optional[str]

class TelemetryAnalyzer:
    """Analyzes telemetry data and generates reports."""
    
    def __init__(self, output_dir: str, track_catalog: TrackCatalog):
        self._output_dir = output_dir
        self._track_catalog = track_catalog
    
    async def analyze(self, frames: list[FrameData], hz: float) -> AnalysisResult:
        """Run full analysis pipeline and generate outputs."""
    
    def _build_track(self, frames: list[FrameData], hz: float) -> list[dict]:
        """Build track map from frames."""
    
    def _detect_laps(self, track: list[dict], hz: float) -> list[int]:
        """Detect lap boundary frames."""
    
    def _detect_corners(self, track: list[dict], lap_start: int, lap_end: int) -> list[dict]:
        """Identify corners within a lap."""
    
    def _generate_html(self, data: dict) -> str:
        """Generate HTML report, return file path."""
    
    def _generate_ai_prompt(self, data: dict) -> str:
        """Generate AI coaching prompt, return file path."""
```

**Code Reuse:**
- Port analysis logic from `2-analyze.py`
- Use HTML template from `2-analyze.py`
- Use AI prompt generation from `2-analyze.py`

---

### 5. TrackCatalog (`src/core/track_catalog.py`)

**Responsibilities:**
- Provide track profiles with corner definitions
- Support track detection from file path or telemetry data

**Action:** Move `test_scripts/telemetry/track_catalog.py` to `src/core/` with minimal changes.

---

### 6. Settings Page (`src/ui/pages/settings.py`)

Add new settings card:

```
┌─────────────────────────────────────────────────────────────┐
│  📊 Telemetry                                                │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  [Toggle] Enable Telemetry Capture                          │
│                                                              │
│  Output Directory:                                          │
│  ┌─────────────────────────────────────┐ [Browse]          │
│  │ C:\Users\...\Documents\SimLaps\...   │                   │
│  └─────────────────────────────────────┘                   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**Implementation:**
- Add `TelemetrySettingsCard` component
- Wire to `AppConfig.telemetry_enabled` and `AppConfig.telemetry_output_path`
- Add directory picker dialog

---

### 7. Home Page (`src/ui/pages/home.py`)

**Additions:**

1. **Telemetry Status Indicator** (below game status):
```
┌─────────────────────────────────────────────────────────────┐
│  ● Recording Telemetry (1,234 frames)                       │
└─────────────────────────────────────────────────────────────┘
```

2. **"Open Telemetry Location" Button** (at bottom):
```
┌─────────────────────────────────────────────────────────────┐
│  [📁 Open Telemetry Location]                                │
└─────────────────────────────────────────────────────────────┘
```

**Status States:**
- `IDLE` - Telemetry disabled or game not running
- `CAPTURING` - Actively recording frames (show frame count)
- `ANALYZING` - Processing captured data
- `COMPLETE` - Analysis finished, report generated

---

### 8. App Controller (`src/ui/app.py`)

**Integration Points:**

```python
class SimLapsApp:
    def __init__(self, page: ft.Page):
        # ... existing init ...
        self._telemetry_capture: Optional[TelemetryCapture] = None
        self._telemetry_analyzer: Optional[TelemetryAnalyzer] = None
    
    def _init_services(self):
        # ... existing services ...
        if self._config.telemetry_enabled:
            self._telemetry_capture = TelemetryCapture(hz=20.0)
            self._telemetry_analyzer = TelemetryAnalyzer(
                output_dir=self._config.telemetry_output_path,
                track_catalog=get_track_catalog(),
            )
    
    async def _on_game_status_change(self, is_running: bool):
        # ... existing logic ...
        
        if self._config.telemetry_enabled and self._telemetry_capture:
            if is_running:
                # Start telemetry capture
                self._home_page.set_telemetry_status(TelemetryStatus.CAPTURING)
                await self._telemetry_capture.start_capture()
            else:
                # Stop capture and analyze
                self._home_page.set_telemetry_status(TelemetryStatus.ANALYZING)
                frames = await self._telemetry_capture.stop_capture()
                
                if frames:
                    result = await self._telemetry_analyzer.analyze(frames, hz=20.0)
                    self._home_page.set_telemetry_status(TelemetryStatus.COMPLETE, result)
```

---

### 9. Debug Logs (`src/ui/components/debug_logs.py`)

Add telemetry log capture:

```python
# Add telemetry-specific log patterns
TELEMETRY_PATTERNS = [
    r"\[TELEMETRY\]",
    r"\[CAPTURE\]",
    r"\[ANALYZER\]",
]
```

---

## Implementation Order

### Phase 1: Core Infrastructure (No UI)
1. Create `src/core/telemetry_decoder.py` - port `ac_evo_decoder.py`
2. Create `src/core/track_catalog.py` - move from test_scripts
3. Create `src/core/telemetry_capture.py` - shared memory reader
4. Create `src/core/telemetry_analyzer.py` - analysis pipeline

### Phase 2: Configuration
5. Modify `src/utils/config.py` - add telemetry settings
6. Modify `src/ui/pages/settings.py` - add Telemetry card

### Phase 3: UI Integration
7. Create `src/ui/components/telemetry_status.py` - status indicator
8. Modify `src/ui/pages/home.py` - add status + button
9. Modify `src/ui/components/debug_logs.py` - include telemetry logs

### Phase 4: App Wiring
10. Modify `src/core/log_parser.py` - ensure session hooks work
11. Modify `src/ui/app.py` - initialize and wire telemetry

### Phase 5: Testing & Polish
12. Test with real game sessions
13. Handle edge cases (session crashes, missing regions, etc.)
14. Add error handling and user feedback

---

## Error Handling

| Scenario | Handling |
|----------|----------|
| Shared memory not found | Log warning, skip capture, continue app normally |
| Capture fails mid-session | Log error, stop capture, continue app normally |
| Analysis fails | Log error, show error in telemetry status, offer "Export for Debug" |
| Output directory not writable | Show error in settings, prompt user to fix |
| No laps detected in telemetry | Generate report anyway with message "No complete laps detected" |

---

## Testing Strategy

1. **Unit Tests**: Decoder functions, lap detection, corner detection
2. **Integration Tests**: Capture → Analyze → Output pipeline
3. **Manual Tests**: Run with real game session, verify HTML output

---

## Dependencies

No new external dependencies required. All functionality uses existing:
- `ctypes` (Windows shared memory)
- `struct` (binary decoding)
- `asyncio` (async capture)
- `flet` (UI)

---

## Output File Naming

```
{output_dir}/
├── telemetry_20260404_143022.html
├── telemetry_20260404_143022_ai_prompt.txt
├── telemetry_20260404_160155.html
├── telemetry_20260404_160155_ai_prompt.txt
└── debug/
    └── telemetry_debug_20260404_143022.jsonl  (optional export)
```

---

## Success Criteria

- [ ] Telemetry automatically captures when game session starts
- [ ] HTML report auto-generates when session ends
- [ ] AI coaching prompt generates alongside HTML
- [ ] Settings card allows enabling/disabling telemetry
- [ ] Settings card allows configuring output directory
- [ ] Home page shows telemetry capture status
- [ ] "Open Telemetry Location" button opens output folder
- [ ] Debug logs include telemetry-related messages
- [ ] App remains stable if telemetry fails
- [ ] No separate windows or parallel entry points
