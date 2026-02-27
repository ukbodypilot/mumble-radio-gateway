# Mumble Radio Gateway — Project Memory

## Update this file
Update MEMORY.md and detail files at the end of every session and whenever a significant bug or pattern is discovered. Keep this file under 200 lines.

## Project Overview
Radio-to-Mumble gateway. AIOC USB device handles radio RX/TX audio and PTT. Optional SDR input via ALSA loopback. Optional Broadcastify streaming via DarkIce. Python 3, runs on Raspberry Pi and Debian amd64.

**Main file:** `mumble_radio_gateway.py` (~5000+ lines)
**Installer:** `scripts/install.sh` (8 steps, targets Debian/Ubuntu/RPi)
**Config:** `gateway_config.txt` (copied from `examples/gateway_config.txt` on install)
**Start script:** `start.sh` (7 steps: kill procs, loopback, AIOC USB reset, pipe, DarkIce, FFmpeg, gateway)
**Windows client:** `windows_audio_client.py` (SDR input on 9600 or Announcement on 9601)

## Announcement Input (port 9601)
- `NetworkAnnouncementSource` — listens on 9601, inbound TCP, length-prefixed PCM
- `ptt_control=True`, `priority=0` — mixer routes audio to radio TX and activates PTT
- Audio-gated PTT: discards silence below `ANNOUNCE_INPUT_THRESHOLD` (-45 dBFS)
- 2s PTT hold (`_ptt_hold_time`) — returns silence+PTT=True through speech pauses
- `ANNOUNCE_INPUT_VOLUME = 4.0` — volume multiplier (clipped to int16)
- Mute key: `a` (mute toggle), status bar shows muted state on AN bar
- Config: `ENABLE_ANNOUNCE_INPUT`, `ANNOUNCE_INPUT_PORT`, `ANNOUNCE_INPUT_HOST`, `ANNOUNCE_INPUT_THRESHOLD`, `ANNOUNCE_INPUT_VOLUME`

## Windows Audio Client
- `windows_audio_client.py` — captures from local input device, sends length-prefixed PCM
- Mode selection on first run: SDR input (port 9600) or Announcement (port 9601)
- Config saved to `windows_audio_client.json` (in .gitignore)
- Same wire format as RemoteAudioSource (4-byte BE length + PCM payload)

## Key Architecture
- `AIOCRadioSource` — reads from AIOC ALSA device (radio RX audio)
- `SDRSource` — reads from ALSA loopback via background reader thread (non-blocking)
- `RemoteAudioServer` — TCP server, sends mixed audio to one connected client (length-prefixed PCM)
- `RemoteAudioSource` — TCP client, receives audio from RemoteAudioServer; name="SDRSV"
- `AudioMixer` — mixes SDR + AIOC with duck-out logic and fade in/out; returns 7-tuple
- `audio_transmit_loop()` — feeds Mumble encoder; sends silence to keep Opus encoder fed
- pymumble/pymumble_py3 — Mumble protocol; SSL shim applied before import for Python 3.12+

## Critical Settings (current defaults)
- `MUMBLE_BITRATE = 72000`, `MUMBLE_VBR = false` (CBR)
- `VAD_THRESHOLD = -45`, `VAD_ATTACK = 0.05`, `VAD_RELEASE = 1.0`, `VAD_MIN_DURATION = 0.25`
- `AUDIO_CHUNK_SIZE = 9600` (200ms at 48kHz)
- SDR loopback: `hw:4,1` / `hw:5,1` / `hw:6,1` (capture side)
- `SDR_BUFFER_MULTIPLIER = 4`
- AIOC pre-buffer: 3 blobs / 600ms
- `PLAYBACK_VOLUME = 4.0`, `ANNOUNCE_INPUT_VOLUME = 4.0`

## Keyboard Controls
- MUTE: `t`=TX `r`=RX `m`=Global `s`=SDR1 `x`=SDR2 `c`=Remote `a`=Announce
- AUDIO: `v`=VAD `,`=Vol- `.`=Vol+
- PROCESS: `n`=Gate `f`=HPF `g`=AGC `w`=Wiener `e`=Echo
- SDR: `d`=SDR1 Duck toggle
- PTT: `p`=Manual PTT toggle
- PLAY: `1-9`=Announcements `0`=StationID `-`=Stop
- TRACE: `i`=Start/stop audio trace
- NOTE: AGC moved from 'a' to 'g'; proc flag changed from A to G

## ALSA Loopback Setup
- 3 cards pinned to hw:4, hw:5, hw:6 via `enable=1,1,1 index=4,5,6`
- Config: `/etc/modprobe.d/snd-aloop.conf` → `options snd-aloop enable=1,1,1 index=4,5,6`
- Each card: hw:N,0 (SDR app writes) / hw:N,1 (gateway reads)

## Python / pymumble
- Install `hid` (not `hidapi`) — gateway uses `hid.Device`
- pymumble: try `pymumble-py3` first, fall back to `pymumble`
- SSL shim patches `ssl.wrap_socket` and `ssl.PROTOCOL_TLSv1_2` before import (Python 3.12+)

## WirePlumber Issues (Debian with PipeWire)
- WirePlumber grabs ALSA loopback (locks to S32_LE, blocks DarkIce S16_LE)
- WirePlumber grabs AIOC (hides it from PyAudio)
- Fix: `~/.config/wireplumber/wireplumber.conf.d/99-disable-loopback.conf`
- **PyAudio uses PipeWire backend** — disabled devices don't appear in PyAudio enumeration
  even though `aplay -l` sees them via raw ALSA. Gateway must open AIOC before
  WirePlumber disables it, or use manual device index.

## AIOC USB Issues
- AIOC audio output can get stuck in stale state — PTT keys radio but no audio transmitted
- Symptom: `aplay -l` shows AIOC, `/proc/asound/cardN/stream0` Playback shows `Stop`,
  `speaker-test -D hw:N,0` produces no audio on radio
- Fix: USB reset (unplug/replug or sysfs authorized cycle)
- `start.sh` now does AIOC USB reset at step 3 before gateway launch
- Reset method: `echo 0 > /sys/bus/usb/devices/X-Y/authorized; sleep 1; echo 1 > ...`

## DarkIce Notes
- DarkIce 1.5 parser bug: crashes if "password" appears before first `[section]` header
- Config template: `scripts/darkice.cfg.example` (NOT examples/)
- Needs audio group + realtime limits
- udev: needs BOTH `SUBSYSTEM=="usb"` AND `SUBSYSTEM=="hidraw"` rules for AIOC

## Audio Trace Instrumentation
- PTT branch now has its own RMS measurement (was blind before — RMS always showed 0)
- PTT outcomes: `ptt_ok` (wrote to AIOC), `ptt_nostr` (output_stream None),
  `ptt_txm` (TX muted), `ptt_err` (write failed)
- Previous traces showing RMS=0 for all PTT ticks were misleading — the measurement
  point was after `continue` so it never ran for PTT

## Known Bugs Fixed (details in bugs.md)
- SDR burst audio, Mumble encoder starvation, duck-out regression
- Config parser crash on decimal, global_muted UnboundLocalError
- DarkIce hidraw udev, WirePlumber AIOC/loopback grab
- SDR2 duck-through on SDR1 buffer gaps
- Announcement/PTT keys spam errors without AIOC
- Status bar width shift on mute/duck (fixed-width padding)
- AIOC audio output stale state (USB reset fix)

## Deployment Notes
- WirePlumber config must be in `~/.config/wireplumber/wireplumber.conf.d/`
- Local Mumble server can interfere — disable if present
- pymumble sends voice via TCP tunnel (UDPTUNNEL), not actual UDP

## SDR Loopback Watchdog
- Config: `SDR_WATCHDOG_TIMEOUT` (10s), `SDR_WATCHDOG_MAX_RESTARTS` (5), `SDR_WATCHDOG_MODPROBE` (false)
- Staged recovery: stage 1=reopen, stage 2=reinit PyAudio, stage 3=reload snd-aloop

## Status Bar
- format_level_bar() returns fixed-width (11 visible chars: 6-char bar + space + 4-char suffix)
- Status icon only (no ACTIVE/IDLE/STOP text label)
- Bar display order: TX → RX → SP → SDR1 → SDR2 → SV/CL → AN

## User Preferences
- CBR Opus (not VBR), commits requested explicitly, concise responses, no emojis
- **gateway_config.txt is NOT committed** (in .gitignore)
- **NEVER commit Broadcastify stream key/password**
- bak/ is not committed, fixed-width status bar is important
