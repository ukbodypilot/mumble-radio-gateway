# Mumble Radio Gateway — Project Memory

## Update this file
Update MEMORY.md and detail files at the end of every session and whenever a significant bug or pattern is discovered. Keep this file under 200 lines.

## Project Overview
Radio-to-Mumble gateway. AIOC USB device handles radio RX/TX audio and PTT. Optional SDR input via ALSA loopback. Optional Broadcastify streaming via DarkIce. Python 3, runs on Raspberry Pi and Debian amd64.

**Main file:** `mumble_radio_gateway.py` (~4500+ lines)
**Installer:** `scripts/install.sh` (8 steps, targets Debian/Ubuntu/RPi)
**Config:** `gateway_config.txt` (copied from `examples/gateway_config.txt` on install)
**Start script:** `start.sh` (launches DarkIce + FFmpeg + gateway)

## Key Architecture
- `AIOCRadioSource` — reads from AIOC ALSA device (radio RX audio)
- `SDRSource` — reads from ALSA loopback via background reader thread (non-blocking)
- `RemoteAudioServer` — TCP server, sends mixed audio to one connected client (length-prefixed PCM)
- `RemoteAudioSource` — TCP client, receives audio from RemoteAudioServer; name="SDRSV" (auto-participates in duck system)
- `AudioMixer` — mixes SDR + AIOC with duck-out logic and fade in/out; returns 7-tuple including sdrsv_was_ducked
- `audio_transmit_loop()` — feeds Mumble encoder; sends silence (not None) to keep Opus encoder fed continuously
- pymumble/pymumble_py3 — Mumble protocol; SSL shim applied before import for Python 3.12+

## Critical Settings (current defaults)
- `MUMBLE_BITRATE = 72000`, `MUMBLE_VBR = false` (CBR)
- `VAD_THRESHOLD = -45`, `VAD_ATTACK = 0.05`, `VAD_RELEASE = 1.0`, `VAD_MIN_DURATION = 0.25`
- `AUDIO_CHUNK_SIZE = 9600` (200ms at 48kHz)
- SDR loopback: `hw:4,1` / `hw:5,1` / `hw:6,1` (capture side)

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

## Known Bugs Fixed (details in bugs.md)
- SDR burst audio (frames_per_buffer was 153600, fixed to 9600)
- Mumble encoder starvation (send silence not None)
- duck-out regression (sdr_active_at_transition used check_signal_instant on noise)
- Config parser crash on decimal in int-defaulted field (VAD_RELEASE = 0.3)
- global_muted UnboundLocalError in status_monitor_loop when SDR1 absent
- DarkIce hidraw udev missing
- WirePlumber AIOC grab
- SDR2 duck-through on SDR1 buffer gaps (duck check only looked at sdrs_to_include)
- WirePlumber grabs loopback on reboot if config not deployed

## Deployment Notes
- WirePlumber config (`scripts/99-disable-loopback.conf`) must be copied to
  `~/.config/wireplumber/wireplumber.conf.d/` — installer does this but fresh
  machines / re-images need it manually. Without it, WirePlumber grabs loopback
  cards on boot (constrains channels, blocks gateway).
- Local Mumble server (mumble-server.service) can interfere — if present on the
  gateway machine, disable it (`systemctl disable mumble-server`).
- pymumble sends voice via TCP tunnel (UDPTUNNEL), not actual UDP.
  `set_receive_sound(True)` must be called before `start()` or `sound_output` is None.

## User Preferences
- CBR Opus (not VBR) — cares about quality not bandwidth
- Commits requested explicitly — never auto-commit
- Concise responses, no emojis
- gateway_config.txt IS committed (repo is private); bak/ is not
