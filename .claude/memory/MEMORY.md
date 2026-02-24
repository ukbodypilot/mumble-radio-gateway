# Mumble Radio Gateway — Project Memory

## Update this file
Update MEMORY.md and detail files at the end of every session and whenever a significant bug or pattern is discovered. Keep this file under 200 lines.

## Project Overview
Radio-to-Mumble gateway. AIOC USB device handles radio RX/TX audio and PTT. Optional SDR input via ALSA loopback. Optional Broadcastify streaming via DarkIce. Python 3, runs on Raspberry Pi and Debian amd64.

**Current machine:** PC, Debian 13 (Trixie), amd64 — moved from Raspberry Pi as of 2026-02-21.

**Main file:** `mumble_radio_gateway.py` (~4800 lines)
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
- `AUDIO_CHUNK_SIZE = 2400` (50ms; 8× ALSA period applied internally = 400ms buffer)
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

## AIOC / AIOCRadioSource Architecture (key fix 2026-02-21, refined 2026-02-24)
- Large ALSA period: `frames_per_buffer = AUDIO_CHUNK_SIZE * 8` (8×2400=19200 frames, 400ms buffer)
- PortAudio callback stores full 400ms blob in `_chunk_queue` (maxsize=8)
- `get_audio()` slices into 50ms sub-chunks; non-blocking (get_nowait) since main loop self-clocks
- rx_muted check BEFORE blob fetch — flushes stale data so buffer is fresh when unmuted
- PTT click suppression: `_ptt_change_time` monotonic timestamp; gain envelope 0 for 30ms, ramps 0→1 from 30ms→130ms
- `_ptt_change_time` set in 4 places: Mumble RX handler, keyboard pending PTT apply, status_monitor PTT release, announcement PTT activation

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

## Known Bugs Fixed (details in bugs.md)
- SDR burst audio (frames_per_buffer was 153600, fixed to 9600)
- Mumble encoder starvation (send silence not None)
- duck-out regression (sdr_active_at_transition used check_signal_instant on noise)
- Config parser crash on decimal in int-defaulted field (VAD_RELEASE = 0.3)
- global_muted UnboundLocalError in status_monitor_loop when SDR1 absent
- DarkIce hidraw udev missing; WirePlumber AIOC grab
- AUDIO_CHUNK_SIZE=9600 caused complete silence (queue timeout too short)
- VAD units mismatch (values in seconds divided by 1000, treated as ms)
- SDR buffer_multiplier computed but never used as frames_per_buffer
- Duplicate SDR_BUFFER_MULTIPLIER key in config
- AIOC USB timing jitter dropouts: fixed by 8× ALSA period
- Speaker/Mumble desync after PTT: fixed by force-transmitting during 130ms suppression window
- PTT click on manual toggle: fixed with time-based envelope
- Announcement PTT missing click suppression: fixed by setting _ptt_change_time at announcement activation
- announcement_delay_active stuck True after stop_playback: fixed by safety clear before mixer call
- AIOC periodic drops (~5s): fixed by incremental pacing + queue maxsize 4→8
- SDR stuttering 750ms on/off: fixed by replacing deque with bytearray ring buffer
- SP bar frozen on R-key mute: fixed by using speaker_audio_level not tx_audio_level
- Status bar freeze after ~7-10s: mixer path didn't update last_audio_capture_time; STOP branch triggered restart loop
- Speaker GIL contention drops: converted from blocking-write thread to PortAudio callback mode
- Config parser quote stripping: string values with quotes weren't matched against device names
- Duck-OUT transition silencing Radio for 1s: removed mixed_audio = None during padding
- SDR breakthrough during AIOC blob gaps: hold timer too short (500ms→1000ms)
- SDR not resuming after duck release: ring buffer drained during ducking; now always drain (real-time, no stale audio)
- SDR bleeding at Radio start: SIGNAL_ATTACK_TIME too slow (0.5→0.15s)

## Config Defaults (verified 2026-02-24)
- All code defaults in `Config.load_config()` dict match `examples/gateway_config.txt`
- Key alignments fixed this session: NOISE_SUPPRESSION_METHOD, STREAM_BITRATE, SDR_DEVICE_NAME,
  ECHOLINK_TO_RADIO, MUMBLE_TO_ECHOLINK, SDR_BUFFER_MULTIPLIER comment, PLAYBACK_ANNOUNCEMENT_INTERVAL
- `gateway_config.txt` IS committed — settings sync between machines via git
- `STREAM_PASSWORD` is blanked by git clean filter before hitting the index (local file keeps real value)
- On each new machine after clone: `git config filter.redact-config.clean 'sed "s/^STREAM_PASSWORD = .*/STREAM_PASSWORD = /"'`
- Filter defined in `.gitattributes`; all machines use same absolute path so Claude auto-memory works

## User Preferences
- CBR Opus (not VBR) — cares about quality not bandwidth
- Commits requested explicitly — never auto-commit
- Concise responses, no emojis
- bak/ is local — never commit it
