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
- `SIGNAL_RELEASE_TIME = 2.0`, `ENABLE_SDR2 = false`
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

## Speaker Output Feature (added 2026-02-21)
- Config keys: `ENABLE_SPEAKER_OUTPUT`, `SPEAKER_OUTPUT_DEVICE`, `SPEAKER_VOLUME`
- `find_speaker_device()` resolves device string → PyAudio index (empty=default, numeric=index, text=name match)
- `open_speaker_output()` called from `setup_audio()` after radio source init
- Audio written in both normal RX path and PTT/playback path in `audio_transmit_loop()`
- Key `o` toggles `self.speaker_muted`; status bar shows `SP:[bar]` when enabled
- `SP:[bar]` uses `speaker_audio_level` (updated in `_speaker_enqueue()`), NOT `tx_audio_level`
- Cleanup in `cleanup()` before `output_stream` close

## AIOC / AIOCRadioSource Architecture (key fix 2026-02-21, refined 2026-02-22)
- Large ALSA period: `frames_per_buffer = AUDIO_CHUNK_SIZE * 8` (8×2400=19200 frames, 400ms buffer)
- PortAudio callback stores full 400ms blob in `_chunk_queue` (maxsize=8)
- `get_audio()` slices into 50ms sub-chunks with incremental pacing (`_next_delivery += chunk_secs`)
- Pacing uses incremental timing (NOT `= now + chunk_secs`) to prevent sleep-overshoot drift accumulating
- Snap-forward logic: if `_next_delivery` > 1 chunk behind, snap to now (prevents catch-up bursts)
- Queue timeout = dynamic: `(AUDIO_CHUNK_SIZE * 8 / AUDIO_RATE) * 2` — adapts to any chunk size
- PTT click suppression: `_ptt_change_time` monotonic timestamp; gain envelope 0 for 30ms, ramps 0→1 from 30ms→130ms
- `_ptt_change_time` set in 4 places: Mumble RX handler, keyboard pending PTT apply, status_monitor PTT release, announcement PTT activation

## SDRSource Architecture (ring buffer, 2026-02-22)
- Reader thread uses `bytearray` ring buffer + `threading.Lock` (NOT deque)
- Ring buffer replaces `deque(maxlen=4)` which silently dropped chunks causing 750ms on/off stutter
- Reader reads full ALSA periods: `read_frames = AUDIO_CHUNK_SIZE * SDR_BUFFER_MULTIPLIER`
- Ring buffer capped at 2 seconds; trims oldest data if exceeded
- `get_audio()` slices `chunk_size * channels * 2` bytes from front of ring buffer (non-blocking)
- Returns None immediately if ring buffer has insufficient data

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

## Config Defaults (verified 2026-02-22)
- All code defaults in `Config.load_config()` dict match `examples/gateway_config.txt`
- Key alignments fixed this session: NOISE_SUPPRESSION_METHOD, STREAM_BITRATE, SDR_DEVICE_NAME,
  ECHOLINK_TO_RADIO, MUMBLE_TO_ECHOLINK, SDR_BUFFER_MULTIPLIER comment, PLAYBACK_ANNOUNCEMENT_INTERVAL
- `gateway_config.txt` is in `.gitignore` — never commit it

## User Preferences
- CBR Opus (not VBR) — cares about quality not bandwidth
- Commits requested explicitly — never auto-commit
- Concise responses, no emojis
- gateway_config.txt and bak/ are local — never commit them
