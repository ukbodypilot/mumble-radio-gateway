# Mumble Radio Gateway — Project Memory

## Update this file
Update MEMORY.md and detail files at the end of every session and whenever a significant bug or pattern is discovered. Keep this file under 200 lines.

## Project Overview
Radio-to-Mumble gateway. AIOC USB device handles radio RX/TX audio and PTT. Optional SDR input via ALSA loopback. Optional Broadcastify streaming via DarkIce. Python 3, runs on Raspberry Pi and Debian amd64.

**Current machine:** PC, Debian 13 (Trixie), amd64 — moved from Raspberry Pi as of 2026-02-21.

**Main file:** `mumble_radio_gateway.py` (~4900 lines)
**Installer:** `scripts/install.sh` (8 steps, targets Debian/Ubuntu/RPi)
**Config:** `gateway_config.txt` (copied from `examples/gateway_config.txt` on install)
**Start script:** `start.sh` (launches DarkIce + FFmpeg + gateway)
**Git remote:** https://github.com/ukbodypilot/mumble-radio-gateway (branch: main)

## Key Architecture
- `AIOCRadioSource` — reads from AIOC ALSA device (radio RX audio)
- `SDRSource` — reads from ALSA loopback via background reader thread (non-blocking)
- `AudioMixer` — mixes SDR + AIOC with duck-out logic and fade in/out
- `audio_transmit_loop()` — feeds Mumble encoder; sends silence (not None) to keep Opus encoder fed continuously
- pymumble/pymumble_py3 — Mumble protocol; SSL shim applied before import for Python 3.12+

## Critical Settings (current defaults)
- `MUMBLE_BITRATE = 96000`, `MUMBLE_VBR = false` (CBR)
- `VAD_THRESHOLD = -45`, `VAD_ATTACK = 0.05`, `VAD_RELEASE = 2.0`, `VAD_MIN_DURATION = 0.25`
- `AUDIO_CHUNK_SIZE = 2400` (50ms; 4× ALSA period = 200ms blobs, 2-blob pre-buffer = 400ms cushion)
- `SIGNAL_ATTACK_TIME = 0.15`, `SIGNAL_RELEASE_TIME = 3.0` (total hold 4.0s with 1.0s internal timer)
- `ENABLE_SDR2 = false`
- `STREAM_BITRATE = 16` (kbps, Broadcastify/scanner standard)
- `NOISE_SUPPRESSION_METHOD = spectral`
- SDR loopback default: `hw:6,1` (capture side); installer pins loopback to hw:4,5,6

## ALSA Loopback Setup
- 3 cards pinned to hw:4, hw:5, hw:6 via `enable=1,1,1 index=4,5,6`
- `numlids` is Raspberry Pi-only; standard Debian uses `enable` array
- Config: `/etc/modprobe.d/snd-aloop.conf` → `options snd-aloop enable=1,1,1 index=4,5,6`
- Each card: hw:N,0 (SDR app writes) / hw:N,1 (gateway reads)

## Python / pymumble
- Install `hid` (not `hidapi`) — gateway uses `hid.Device`
- pymumble: try `pymumble-py3` first, fall back to `pymumble` (both in code + installer)
- SSL shim patches `ssl.wrap_socket` and `ssl.PROTOCOL_TLSv1_2` before import (Python 3.12+)

## WirePlumber Issues (Debian with PipeWire)
- WirePlumber grabs ALSA loopback (locks to S32_LE, blocks DarkIce S16_LE)
- WirePlumber grabs AIOC (hides it from PyAudio)
- Fix: `~/.config/wireplumber/wireplumber.conf.d/99-disable-loopback.conf`
  excludes `alsa_card.platform-snd_aloop.*` and `alsa_card.usb-AIOC_*`
- Installer copies `scripts/99-disable-loopback.conf` and restarts wireplumber

## DarkIce Notes
- DarkIce 1.5 parser bug: crashes with "no current section" if the word "password"
  appears in a comment before the first `[section]` header
- Config template: `scripts/darkice.cfg.example` (NOT examples/)
- Needs audio group + realtime limits (`/etc/security/limits.d/audio-realtime.conf`)
- udev: needs BOTH `SUBSYSTEM=="usb"` AND `SUBSYSTEM=="hidraw"` rules for AIOC

## Speaker Output Feature (added 2026-02-21, callback mode 2026-02-24)
- Config keys: `ENABLE_SPEAKER_OUTPUT`, `SPEAKER_OUTPUT_DEVICE`, `SPEAKER_VOLUME`
- `find_speaker_device()` resolves device string → PyAudio index (empty=default, numeric=index, text=name match)
- Config parser strips quotes from string values (fix 2026-02-24)
- `open_speaker_output()` uses PortAudio callback mode (`_speaker_callback`)
- PortAudio internal buffer (2-3 periods) absorbs GIL delays from status bar thread
- `_speaker_enqueue()` applies volume, level metering, and clock drift drain (threshold 6→2)
- Key `o` toggles `self.speaker_muted`; status bar shows `SP:[bar]` when enabled
- `SP:[bar]` uses `speaker_audio_level` (updated in `_speaker_enqueue()`), NOT `tx_audio_level`
- Speaker drops when terminal loses focus: Linux desktop deprioritizes background processes → transmit loop starved → speaker callback underruns

## AIOC / AIOCRadioSource Architecture (refactored 2026-02-25)
- ALSA period: `frames_per_buffer = AUDIO_CHUNK_SIZE * 4` (4×2400=9600 frames, 200ms blobs)
- PortAudio callback stores 200ms blob in `_chunk_queue` (maxsize=16)
- **Pre-buffer gate**: `_prebuffering=True` at init/mute/depletion; won't serve until sub_buffer >= 2 blobs (400ms cushion)
- `get_audio()` eagerly drains ALL queued blobs into sub_buffer every tick (critical: old loop only fetched when sub_buffer < chunk_bytes, which starved the pre-buffer check)
- Once pre-buffer satisfied, slices into 50ms sub-chunks normally
- rx_muted check BEFORE blob fetch — flushes stale data + resets prebuffering
- PTT click suppression: `_ptt_change_time` monotonic timestamp; gain envelope 0 for 30ms, ramps 0→1 from 30ms→130ms
- `_ptt_change_time` set in 4 places: Mumble RX handler, keyboard pending PTT apply, status_monitor PTT release, announcement PTT activation
- **Previous 8× approach** had 400ms blobs but no cushion — USB delivery jitter caused 400-450ms silence gaps at start of reception

## Audio Thread Realtime Priority (added 2026-02-25)
- `audio_transmit_loop()` sets `SCHED_RR` priority 10 on its own thread at startup
- Falls back to `nice -10` if no root/CAP_SYS_NICE, else silent
- Only the audio transmit thread is elevated — status bar, keyboard, etc. stay normal
- Fixes speaker drops when terminal window loses desktop focus

## SDRSource Architecture (queue + sub-buffer, 2026-02-22/24)
- Reader thread reads full ALSA periods into `_chunk_queue` (maxsize=8, ~1.6s at 200ms each)
- `get_audio()` uses sub-buffer pattern (same as AIOC): slices blobs into 50ms chunks
- Non-blocking: get_nowait() in sub-buffer fill loop, return None if no data
- Stereo auto-detection with fallback to mono

## Mixer / Duck State Machine (refined 2026-02-24)
- Source loop split: Phase 1 (non-SDR) → duck state → Phase 2 (SDR)
- Phase 2 always calls get_audio() to drain ring buffer; ducked audio discarded (real-time: no stale data)
- `has_actual_audio("Radio")` hysteresis: SIGNAL_ATTACK_TIME (0.15s), SIGNAL_RELEASE_TIME (3.0s)
- Radio hold timer: 1.0s after `has_actual_audio` releases (bridges AIOC blob gaps up to 850ms)
- Total hold = SIGNAL_RELEASE_TIME + hold timer = 4.0s from signal drop to SDR resume
- `check_signal_instant()`: -50dB threshold (hardcoded, separate from VAD_THRESHOLD -45dB)
- Duck-OUT transition: no longer silences mixed_audio (was throwing away 1s of Radio audio)
- Trace instrumentation: 18-field tuple per tick, MXST dict captures full duck/signal/mute state

## Sub-Chunk Trace Instrumentation (added 2026-02-25)
- Activated for first 2s after 'i' press (`_trace_detailed_until` deadline)
- Per-tick: zero_count, max_zero_run, min/max sample, boundary jump
- Flags: ZERO_RUN (>2ms), BOUNDARY (>5000), MOSTLY_ZERO (>50%)
- Used to prove mixer output data was clean — drops were in speaker output path

## Known Bugs Fixed (details in bugs.md)
See bugs.md for full list. Key recent fixes:
- AIOC 400-450ms silence gaps at reception start: reduced period 8×→4× with 2-blob pre-buffer
- Speaker drops on window defocus: SCHED_RR on audio transmit thread
- FFT resampling for speaker 48k→44.1k: planned but reverted with other changes (not yet re-applied)
- FilePlayback: cache, staging, fade, PTT leak fixes (all 2026-02-24)

## Config Defaults (verified 2026-02-24)
- All code defaults in `Config.load_config()` dict match `examples/gateway_config.txt`
- `gateway_config.txt` IS committed — repo is private, passwords sync between machines

## User Preferences
- CBR Opus (not VBR) — cares about quality not bandwidth
- Commits requested explicitly — never auto-commit
- Concise responses, no emojis
- bak/ is local — never commit it (gateway_config.txt IS committed, repo is private)
