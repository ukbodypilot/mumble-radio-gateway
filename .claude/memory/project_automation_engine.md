---
name: Automation Engine + D75 TX
description: Automation engine fully working with D75 FO tuning, MP3 recording, BT audio TX, TX_RADIO routing
type: project
---

## Automation Engine — Status (2026-03-17)

**All features implemented and tested on Pi with D75 via Bluetooth.**

### What's working
- `radio_automation.py` — 5 classes: RepeaterDatabase, RadioController, AudioRecorder, SchemeParser, AutomationEngine
- `automation_scheme.txt` — task definitions with schedule, radio, action, options
- D75 FO command tuning: atomic freq/step/mode/tone/offset (step forced to 5kHz for universal compatibility)
- MP3 recording via streaming lame encoder (no WAV intermediate)
- Recording filenames: `RADIO_FREQ_DATE_TIME_LABEL.mp3` (sortable, identifiable)
- `max_runs` option — tasks stop after N executions
- `start_now` option — tasks fire immediately on first load
- Dashboard automation panel — live task status, current task, recording indicator, history
- Recordings web page (`/recordings`) — filter by radio/date/freq, download, delete, in-browser playback
- Web endpoints: `/automationstatus`, `/automationhistory`, `/automationcmd`, `/recordingslist`, `/recordingsdownload`, `/recordingsdelete`
- 115 repeaters loaded from RepeaterBook CSV

### D75 TX via Bluetooth (2026-03-17, verified working)
- `TX_RADIO` config: 'th9800' (default) or 'd75' — routes PTT and audio
- D75 PTT: `!ptt on`/`!ptt off` via CAT (explicit, NOT toggle like TH-9800)
- Audio TX: gateway 48kHz → downsample to 8kHz → TCP port 9751 → D75_CAT.py → SCO → radio
- RTS save/restore skipped for D75 (not applicable, TH-9800 specific)
- Smart announce also routes through TX_RADIO

### D75 FO Tuning Details
- Reads current FO as template, modifies fields, writes back atomically
- FO response parsing: strips "FO " prefix, handles multiline responses
- Step size forced to index 0 (5kHz) — works for all amateur frequencies
- CTCSS tone lookup: 39-tone table, closest-match search
- Auto offset: 600kHz for 2m (shift direction by freq), 5MHz for 70cm (negative)

### Config keys
```
ENABLE_AUTOMATION = true
TX_RADIO = d75                          # or th9800
AUTOMATION_SCHEME_FILE = automation_scheme.txt
AUTOMATION_REPEATER_FILE = RB_2603161801.csv
```

### Bug fixed: ENABLE_AUTOMATION duplicate
Was listed in both `automation` and `advanced` sections of `_CONFIG_LAYOUT`. Web UI Save wrote it twice — second `false` under `[advanced]` always overwrote the `true` set in `[automation]`. Removed from `advanced`.

### Architecture
```
Text scheme file → AutomationEngine → Actions (tune, record, announce)
Future:  English mission → AI Planner → scheme → AutomationEngine → Actions
```

**Why:** Autonomous repeater scanning, recording, and announcement playback via D75 BT.
**How to apply:** Enable automation in config, set TX_RADIO=d75 for D75 TX. D75_CAT service auto-connects BT on startup.
