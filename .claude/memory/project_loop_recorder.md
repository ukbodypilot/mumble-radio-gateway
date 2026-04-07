---
name: Loop Recorder (3.0 feature)
description: Per-bus continuous recording with visual waveform review, export, configurable retention
type: project
---

## Loop Recorder — Shipped in 3.0 (2026-04-07)

**What it does:** Any bus can loop-record audio as segmented MP3 files with real-time waveform visualization, playback, and export.

**Key files:**
- `loop_recorder.py` — LoopSegment (lame encoder + waveform), LoopRecorder (lifecycle, queries, export)
- `web_pages/recorder.html` — stacked multi-bus canvas waveform viewer
- `web_routes_loop.py` — API handlers (/loop/buses, /loop/waveform, /loop/play, /loop/export)
- `bus_manager.py` — feed hook in `_deliver_audio()` for `loop` config flag

**Storage:** `recordings/loop/<bus_id>/YYYYMMDD_HHMM.mp3` + `.wfm` (2 bytes/sec waveform)

**Bugs fixed during development:**
- Live waveform: `get_waveform()` only read `.wfm` files from disk, missed active segment in memory. Fixed by checking `_active` dict.
- Click offset: canvas CSS width vs pixel width mismatch. Fixed with `getBoundingClientRect` scaling.
- Play button: `togglePlay()` checked `player.src` which was empty until waveform click. Fixed fallback to play from viewStart.
- Seek bar: `/loop/play` didn't support HTTP Range headers. Added 206 Partial Content.

**Config:** `routing_config.json` per-bus `processing.loop` flag + `processing.loop_hours` retention.
