# Radio Gateway — Project Memory

## Project Overview
Radio-to-Mumble gateway with SDR, multiple radios, web UI, and AI features. Python 3, Arch Linux.

**Config:** `gateway_config.txt` (INI, `.gitignore` — NEVER commit, contains secrets)
**Start:** `sudo systemctl restart radio-gateway.service` (or start.sh)
**Version:** 3.0 (released 2026-04-07)

## Codebase Structure (post-3.0 refactor 2026-04-07)
- `gateway_core.py` (~3,200 lines) — RadioGateway class, main loop (simplified), audio setup, Mumble, status
- `bus_manager.py` (~810 lines) — BusManager: ALL bus ticks + sink delivery (listen, solo, duplex, simplex)
- `audio_bus.py` — ListenBus, SoloBus, DuplexRepeaterBus, SimplexRepeaterBus
- `audio_sources.py` — AudioSource subclasses, StreamOutputSource
- `loop_recorder.py` (~480 lines) — per-bus continuous recording, segmented MP3, waveform data
- `plugin_loader.py` (~80 lines) — auto-discovers plugins from `plugins/` directory
- `plugins/example_radio.py` — template for external radio plugins
- `web_server.py` (~2,050 lines) — WebConfigServer, Handler dispatch, _CONFIG_LAYOUT
- `web_routes_get.py` (~1,040 lines) — core GET route handlers
- `web_routes_post.py` (~1,390 lines) — POST route handlers
- `web_routes_stream.py` (379) — WebSocket/streaming handlers
- `web_routes_loop.py` (~120 lines) — Loop recorder API handlers
- `web_routes_packet.py` (~200 lines) — Packet radio + Winlink API handlers
- `text_commands.py` (718) — Mumble chat commands, key dispatch, TTS
- `audio_trace.py` (846) — watchdog trace loop + HTML trace dump
- `stream_stats.py` (117) — DarkIce/Icecast stats
- `sdr_plugin.py` — RSPduo dual tuner plugin
- `th9800_plugin.py` — TH-9800 AIOC plugin (audio_level computed post-gate)
- `kv4p_plugin.py` — KV4P HT radio plugin
- `gateway_link.py` — Link protocol, server, client, RadioPlugin base class
- `repeater_manager.py` — ARD repeater database, GPS proximity queries
- `transcriber.py` — Whisper voice-to-text (streaming + chunked modes)
- `smart_announce.py` — AI announcement engine (claude CLI backend)
- `radio_automation.py` — Automation engine (scheme parser, repeater DB, recorder)
- `ptt.py` — RelayController, GPIORelayController
- Utility modules: `ddns_updater.py`, `email_notifier.py`, `cloudflare_tunnel.py`, `mumble_server.py`, `usbip_manager.py`, `gps_manager.py`
- `gateway_utils.py` — re-export shim (backward compat for old imports)
- `gateway_mcp.py` — MCP server (stdio, 55+ tools, talks to HTTP API on port 8080)

## Web UI Pages
`/` shell, `/dashboard`, `/routing`, `/controls`, `/radio`, `/d75`, `/kv4p`, `/sdr`, `/gps`, `/repeaters`, `/aircraft`, `/telegram`, `/monitor`, `/recordings`, `/recorder`, `/transcribe`, `/packet`, `/config`, `/logs`, `/voice`

## Key Subsystems

### 3.0 Architecture (2026-04-07)
- ALL buses managed by BusManager in a daemon thread (listen, solo, duplex, simplex)
- Main loop simplified: drains BusManager queues, handles SDR rebroadcast TX, WebSocket push
- `sync_listen_bus()` manages source add/remove from routing config
- `self.mixer = bus_manager.listen_bus` for backward compat

### Loop Recorder (2026-04-07)
- Per-bus continuous recording with visual waveform review
- Enable via "R" button on any bus in routing UI
- Segmented MP3 files (5-min chunks), auto-cleanup by retention window (1h-7d)
- `.wfm` sidecar files: peak + RMS per second for waveform rendering
- Live waveform from active segment in memory (no wait for segment close)
- Canvas-based viewer: zoom/pan, click-to-play, right-click-drag selection
- Export: MP3 or WAV from any time range via ffmpeg
- Dashboard panel: per-bus stats (segments, disk, write rate, retention)
- API: `/loop/buses`, `/loop/waveform`, `/loop/play` (with Range support), `/loop/export`
- Storage: `recordings/loop/<bus_id>/YYYYMMDD_HHMM.mp3` + `.wfm`

### Plugin Auto-Discovery (2026-04-07)
- Drop `.py` in `plugins/`, set `ENABLE_X = True` in config, restart
- `plugin_loader.py` scans for classes with `PLUGIN_ID` attribute
- No gateway code changes needed — auto-registered in BusManager
- Template: `plugins/example_radio.py` (heavily commented)
- Docs: `docs/plugin-development.md` (both local plugin and link endpoint paths)

### GPS Receiver (2026-04-02)
- `gps_manager.py`: USB serial NMEA or `GPS_PORT = simulate` for fake DM13do data
- `/gps` page: Leaflet map, DOP probability ring, satellite SNR chart, SIM/LIVE toggle

### Repeater Database (2026-04-02)
- `repeater_manager.py`: ARD per-state JSON, GPS proximity, 24h cache
- `/repeaters` page: map + table, MASTER/SLAVE SDR assignment + SET, KV4P Tune button

### Broadcastify Streaming
- `StreamOutputSource` in audio_sources.py: direct PCM→ffmpeg→Icecast
- Silence keepalive thread prevents idle disconnect
- Auto-reconnect in send_audio() when connection drops

### Packet Radio + Winlink (2026-04-04)
- Remote Direwolf TNC on FTM-150 Pi endpoint (192.168.2.121)
- Pat Winlink client on Pi, AGW connected-mode
- Web UI: compose, inbox/outbox/sent, connect & sync, live connection log

## Config Safety (CRITICAL)
- `_CONFIG_LAYOUT` in web_server.py is master list — Save wipes keys not listed
- NEVER use `replace_all=true` on config file; use anchored sed patterns
- `gateway_config.txt` is NOT in git — repo is PUBLIC

## User Preferences
- Commits requested explicitly, no auto-push, concise responses, no emojis
- Instrument code rather than guess at bugs
- Separate files for new features (not monolith)
- Config file is master for startup state; runtime controls reset on restart

## Machine — user-optiplex3020 (Arch Linux)
- Python 3.14, sudo password: `user`, Git user: ukbodypilot
- AIOC: `/dev/ttyACM0`, KV4P: `/dev/kv4p`, Relay: `/dev/relay_radio`
- D75: link endpoint on 192.168.2.134 via BT proxy
- FTM-150: AIOC link endpoint on 192.168.2.121
- GPS: u-blox GNSS on `/dev/gps` (udev rule)

## Bus Processing (2026-04-05)
- AudioProcessor has stateful IIR filters — MUST process once per bus tick, not per-sink
- ALL bus processors in `bus_manager._bus_processors` dict (including primary listen bus)
- TH9800: audio_level computed AFTER processing so noise gate squelches level bar

## Shell Nav Bar (2026-04-07)
- MP3/PCM/MIC buttons: fixed-width, timer inside button text, no indicator dots
- Play buttons turn red when active
- Default volume sliders at 50%

## See Also
- [bugs.md](bugs.md) — bug history
- [bugs_2026_03_30.md](bugs_2026_03_30.md) — v2.0 routing bugs
- [bugs_2026_04_01.md](bugs_2026_04_01.md) — marathon session bugs
- [bugs_2026_04_05.md](bugs_2026_04_05.md) — bus processing, SDR noise, endpoint mode bugs
- [feedback_config_safety.md](feedback_config_safety.md) — config damage prevention
- [feedback_single_source_config.md](feedback_single_source_config.md) — GUI changes write to config file
- [feedback_no_gateway_restart.md](feedback_no_gateway_restart.md) — Claude can restart gateway
- [feedback_instrument_not_guess.md](feedback_instrument_not_guess.md) — measure before fixing audio issues
- [project_audio_quality.md](project_audio_quality.md) — audio quality fixes, trace system
- [project_d75_cleanup.md](project_d75_cleanup.md) — legacy D75 removal target ~2026-04-08
- [reference_gdrive_backup.md](reference_gdrive_backup.md) — rclone backup to Google Drive
- [project_ftm150_endpoint.md](project_ftm150_endpoint.md) — FTM-150 AIOC endpoint
- [project_packet_radio.md](project_packet_radio.md) — Packet Radio + Winlink email
- [project_ftm150_reverse_eng.md](project_ftm150_reverse_eng.md) — FTM-150 control head RE (shelved)
- [project_listen_bus_unify.md](project_listen_bus_unify.md) — listen bus unification (COMPLETED)
- [project_rust_audio_core.md](project_rust_audio_core.md) — Rust audio core (future, deferred)
- [project_loop_recorder.md](project_loop_recorder.md) — loop recorder details
