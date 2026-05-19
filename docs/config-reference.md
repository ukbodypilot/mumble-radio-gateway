# Configuration Reference

Every config key the gateway reads from `gateway_config.txt`. **The authoritative source is [`examples/gateway_config.txt`](../examples/gateway_config.txt)** — that file has the latest keys, default values, and per-key comments. This page is an index pointing at it and grouping related keys by feature.

## Where the file lives

```
<gateway-dir>/gateway_config.txt        # your local config (gitignored — has secrets)
<gateway-dir>/examples/gateway_config.txt   # template / docs (in the repo)
```

The installer copies the example to `gateway_config.txt` if one doesn't exist. The example is heavily commented — read it top-to-bottom once, then come back when you need to change something.

## Fresh-install minimums

**Required** (gateway won't connect):

| Section | Key | Purpose |
|---------|-----|---------|
| `[mumble]` | `MUMBLE_SERVER` | Hostname/IP of your Mumble server |
| `[mumble]` | `MUMBLE_PORT` | Default 64738 |
| `[mumble]` | `MUMBLE_USERNAME` | Display name on Mumble |
| `[mumble]` | `MUMBLE_PASSWORD` | Empty if server has no password |

**Recommended** (key optional features):

| Section | Key | Purpose |
|---------|-----|---------|
| `[streaming]` | `STREAM_*` | Broadcastify feed key |
| `[telegram]` | `TELEGRAM_BOT_TOKEN` + `_CHAT_ID` | Telegram bot control |

## Section index

| Section | Covers |
|---------|--------|
| `[startup]` | Startup verbosity + logging |
| `[mumble]` | Mumble client connection + servers |
| `[radio]` | AIOC USB radio (TH-9800 default) |
| `[audio]` | Sample rate, chunk size, channel count |
| `[levels]` | Per-source gain, master volumes |
| `[ptt]` | PTT method, hold times, talkback |
| `[vad]` | Silero VAD threshold, hold, hysteresis |
| `[vox]` | Legacy dB-envelope VOX (most paths use VAD now) |
| `[processing]` | High-pass / low-pass / notch / noise gate / RNNoise |
| `[switching]` | SDR ducking, priority, signal threshold |
| `[remote]` | Remote audio TX/RX link (full duplex) |
| `[announce]` | Network announcement input (port 9601) |
| `[playback]` | Playback source — file announcements, soundboard |
| `[tts]` | Text-to-speech (Edge TTS / gTTS) |
| `[speaker]` | Local speaker output mode (virtual/auto/real) |
| `[streaming]` | Broadcastify / Icecast feed |
| `[echolink]` | EchoLink integration (legacy, TheLinkBox) |
| `[relay]` | USB relay control (radio power, antenna switches) |
| `[smart]` | AI-generated smart announcements |
| `[telegram]` | Telegram bot for remote control |
| `[web]` | Web UI port, theme, auth |
| `[ddns]` | DDNS updater (No-IP, Dynu) |
| `[cat]` | TH-9800 CAT control startup commands |
| `[advanced]` | Tunable thresholds, watchdogs, debug |

## Feature-specific keys not in the .ini

Some features have keys that live OUTSIDE `gateway_config.txt` because they're host-specific or operationally tuned and shouldn't follow the user across machines. See the per-feature docs:

- **Transcription pool** — `TRANSCRIBE_MODE`, `TRANSCRIBE_REMOTE_URLS`, `TRANSCRIBE_SPLIT_THRESHOLD_SECS` plus the live UI settings in `.transcribe_settings.json`. See [transcription-pool.md](transcription-pool.md).
- **Fleet Manager** — task docs are `hourly.md` / `daily.md` / `SYSTEM_MANIFEST.md`; engine state in `manager_state.json`; runtime reports in `manager_reports.jsonl`. See [fleet-manager.md](fleet-manager.md).
- **Routing config** — bus topology lives in `routing_config.json`, edited via the visual editor at `/routing`. Not in the .ini.
- **Loop recorder retention** — per-bus, edited in the routing UI.

## Defaults & types

The example file has the canonical default for every key as its initial value, and a comment explaining what the key does. When in doubt: read the example. It's the only place that won't drift out of sync with the parser.

Where a key is intentionally blank (e.g. `MUMBLE_PASSWORD =`), the gateway falls back to a sensible default — see the loader in [`gateway_config.py`](../gateway_config.py) for the resolution order.

## Adding a new config key

The convention used across the codebase:

```python
my_value = getattr(self.config, 'MY_NEW_KEY', 'fallback_default')
```

So a new key works the moment you read it; the example file documents it for users. Steps:

1. Use the key in code with a `getattr(...)` + default.
2. Add the key to `examples/gateway_config.txt` in the appropriate section with a comment.
3. If it's a feature-specific tunable that isn't safe to ship as a default (host-specific path, sensitive setting), document its presence here and in the relevant feature doc.

That's the whole pattern. No schema, no migrations.
