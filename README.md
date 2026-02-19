# Mumble Radio Gateway

A bidirectional audio bridge connecting Mumble VoIP to amateur radio with multi-source audio mixing, SDR integration, real-time processing, and extensive features.

```
╔══════════════════════════════════════════════════════════════════════════════════════╗
║                       MUMBLE RADIO GATEWAY — AUDIO FLOW                              ║
╚══════════════════════════════════════════════════════════════════════════════════════╝

  ┌─────────────────┐  PTT audio (direct)                           ┌──────────────────┐
  │  Mumble RX      │──────────────────────────────────────────────►│  Radio TX        │
  │  (Opus VoIP)    │  Mumble users heard → keyed to radio          │  AIOC USB        │
  └─────────────────┘  auto-PTT, bypasses mixer                     │  GPIO PTT        │
                                                                     │                  │
  SOURCES                                  MIXER                    │  ↑ also receives ┤
  ───────                       ╔══════════════════════╗            │  File Playback   │
                                ║                      ║            └──────────────────┘
  ┌─────────────────┐           ║                      ║   PTT audio        ▲
  │  File Playback  │──────────►║   P R I O R I T Y    ╠────────────────────┘
  │  Priority 0     │           ║      M I X E R       ║
  │  WAV·MP3·FLAC   │           ║                      ║  Radio RX  ┌──────────────────┐
  │  10 slots (0–9) │           ║  Priority-based      ╠───────────►│  Mumble TX       │
  └─────────────────┘           ║  source selection    ║            │  Opus VoIP       │
                                ║                      ║            └──────────────────┘
  ┌─────────────────┐           ║  SDR ducking:        ║
  │  Radio RX (P1)  │──────────►║  Radio RX            ║  Mixed     ┌──────────────────┐
  │  AIOC USB       │           ║    > SDR1 (P1)       ╠───────────►│  Stream Output   │
  └─────────────────┘           ║      > SDR2 (P2)     ║            │  Darkice /       │
                                ║                      ║            │  Broadcastify    │
  ┌─────────────────┐           ║  Attack/Release/     ║            └──────────────────┘
  │  SDR1 (P2)      │──────────►║  Padding transitions ║
  │  ALSA Loopback  │  [DUCK]   ║                      ║  EchoLink  ┌──────────────────┐
  │  ■ cyan bar     │           ║  Audio Processing:   ╠───────────►│  EchoLink TX     │
  └─────────────────┘           ║  VAD · Noise Gate    ║            │  Named Pipes     │
                                ║  AGC · HPF           ║            └──────────────────┘
  ┌─────────────────┐           ║  Wiener · Echo Canc  ║
  │  SDR2 (P2)      │──────────►║                      ║
  │  ALSA Loopback  │  [DUCK]   ╚══════════════════════╝
  │  ■ magenta bar  │
  └─────────────────┘           Duck priority:  Radio RX  >  SDR1 (P1)  >  SDR2 (P2)

  ┌─────────────────┐           Each duck transition uses attack / release / padding:
  │  EchoLink (P3)  │──────────►  [source active] → silence gap → [audio switches]
  │  Named Pipes    │             [source silent ] → silence gap → [audio restores]
  └─────────────────┘
```

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [SDR Integration](#sdr-integration)
- [Keyboard Controls](#keyboard-controls)
- [Status Bar](#status-bar)
- [Architecture](#architecture)
- [Configuration Reference](#configuration-reference)
- [Troubleshooting](#troubleshooting)
- [Advanced Features](#advanced-features)

## Features

### Core Functionality
- **Bidirectional Audio Bridge**: Seamless communication between Mumble VoIP and radio
- **Multi-Source Audio Mixing**: Simultaneous mixing of 5 audio sources with priority control
- **Auto-PTT Control**: Automatic push-to-talk with configurable delays and tail
- **Voice Activity Detection (VAD)**: Smart audio gate prevents noise transmission (enabled by default)
- **Real-Time Audio Processing**: Noise gate, AGC, filters, echo cancellation
- **Live Status Display**: Real-time bars showing TX/RX/SDR levels with color coding

### Audio Sources (Priority-Based Mixing)

```
Priority 0 (Highest) → File Playback    [PTT → Radio TX]
Priority 1           → Mumble RX        [PTT → Radio TX, direct path]
Priority 1           → Radio RX         [→ Mumble TX, no PTT]
Priority 2           → SDR1 Receiver    [→ Mumble TX, with ducking]
Priority 2           → SDR2 Receiver    [→ Mumble TX, with ducking]
Priority 3 (Lowest)  → EchoLink        [→ Mumble TX]
```

#### 1. **File Playback** (Priority 0, PTT enabled)
   - 10 announcement slots (keys 1-9 + Station ID on 0)
   - WAV, MP3, FLAC support with automatic resampling
   - Volume normalization and per-file controls
   - **Triggers PTT** when playing; Radio RX still forwarded to Mumble

#### 2. **Radio RX** (Priority 1)
   - AIOC USB audio interface
   - GPIO PTT control
   - Automatic audio processing pipeline
   - Independent mute control

#### 3. **SDR1 Receiver** (Priority 2, with ducking)
   - ALSA loopback device input
   - Independent volume, mute, and duck controls
   - Real-time level display (cyan bar)
   - Ducked by Radio RX and higher-priority SDR

#### 4. **SDR2 Receiver** (Priority 2, with ducking)
   - Second independent ALSA loopback input
   - Same controls as SDR1 but independent (`x` to mute)
   - Real-time level display (magenta bar)
   - Priority-based ducking vs SDR1 (configurable)

#### 5. **Mumble RX** (Priority 1, PTT enabled — direct path)
   - Audio from Mumble users is captured via a callback, **bypassing the mixer**
   - Immediately keys PTT and transmits through the AIOC to the radio
   - Output volume controlled by `OUTPUT_VOLUME`
   - Suppressed when TX is muted (`t` key) or manual PTT mode is active
   - This is the primary gateway path: **Mumble users speak → radio transmits**

#### 6. **EchoLink** (Priority 3)
   - Named pipe integration
   - TheLinkBox compatible
   - Optional routing to/from Mumble and Radio

### SDR Audio Ducking

The SDR source features **audio ducking** to prevent interference with primary communications:

**Ducking Enabled (default):**
```
┌─────────────────────────────────────────┐
│ Radio RX active                         │
│ ├─ Radio audio → Mumble ✓              │
│ └─ SDR audio   → DUCKED (silenced)     │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│ No other audio active                   │
│ └─ SDR audio   → Mumble ✓              │
└─────────────────────────────────────────┘
```

**Ducking Disabled (mixing mode):**
```
┌─────────────────────────────────────────┐
│ Radio RX + SDR both active              │
│ ├─ Radio audio → Mumble (50% mix)      │
│ └─ SDR audio   → Mumble (50% mix)      │
└─────────────────────────────────────────┘
```

**Controls:**
- Press `d` to toggle SDR1 ducking on/off at runtime
- Config: `SDR_DUCK = true` / `SDR2_DUCK = true` (default)
- Status indicator: `[D]` flag when SDR1 ducking enabled

### Dual SDR with Priority Ducking

Two SDR inputs can run simultaneously with configurable priority:

```
┌──────────────────────────────────────────────────────────────┐
│  PRIORITY HIERARCHY                                          │
│                                                              │
│  1. Radio RX (AIOC)  ← always top priority, ducks all SDRs  │
│  2. SDR1 (priority=1) ← ducks SDR2 if both have signal      │
│  3. SDR2 (priority=2) ← lowest, ducked by SDR1 and radio    │
└──────────────────────────────────────────────────────────────┘
```

**Example use cases:**
- **Scanner + web SDR**: SDR1 = local scanner (priority 1), SDR2 = websdr.org feed (priority 2) — scanner ducks the web SDR when active
- **Two frequencies**: Monitor VHF and UHF simultaneously; higher-priority channel interrupts the other
- **Remote + local**: Local SDR1 takes priority over a remote SDR2 pipe

**Configuration:**
```ini
ENABLE_SDR  = true      # SDR1 enabled
ENABLE_SDR2 = true      # SDR2 enabled

SDR_PRIORITY  = 1       # SDR1: higher priority (ducks SDR2)
SDR2_PRIORITY = 2       # SDR2: lower priority (ducked by SDR1)

SDR_DUCK  = true        # SDR1: ducked by radio RX
SDR2_DUCK = true        # SDR2: ducked by radio RX and SDR1
```

**Status bar during dual SDR operation:**
```
SDR1:[███----] 45%  SDR2:[--DUCK--]  D    ← SDR2 ducked by SDR1
SDR1:[--DUCK-]  D   SDR2:[--DUCK--]  D    ← both ducked by radio RX
SDR1:[███----] 45%  SDR2:[██-----] 30%    ← both playing (mixed)
```

### Source Switching — Attack, Release & Transition Padding

SDR ducking transitions use a three-stage gate to avoid jarring cuts.
**Note:** These timers apply only to SDR ducking (Radio RX signal detection).
File playback is deterministic — PTT fires immediately when a file starts,
with no attack delay, and the `PTT_ANNOUNCEMENT_DELAY` window handles key-up timing separately.

```
┌──────────────────────────────────────────────────────────┐
│  ATTACK: radio signal must be CONTINUOUSLY present for   │
│  SIGNAL_ATTACK_TIME before the SDR is ducked.            │
│  Any silence resets the timer — transient noise can      │
│  never trigger a duck.                                   │
├──────────────────────────────────────────────────────────┤
│  TRANSITION (duck-out):                                  │
│  [SDR playing] → [silence gap] → [radio takes over]     │
│                   SWITCH_PADDING_TIME                    │
├──────────────────────────────────────────────────────────┤
│  RELEASE: radio must be continuously silent for          │
│  SIGNAL_RELEASE_TIME before the SDR resumes.             │
│  Brief pauses in speech don't un-duck prematurely.       │
├──────────────────────────────────────────────────────────┤
│  TRANSITION (duck-in):                                   │
│  [radio ends] → [silence gap] → [SDR resumes]           │
│                 SWITCH_PADDING_TIME                      │
└──────────────────────────────────────────────────────────┘
```

**Configuration:**
```ini
SIGNAL_ATTACK_TIME  = 0.5   # seconds of continuous signal before duck
SIGNAL_RELEASE_TIME = 1.0   # seconds of silence before SDR resumes
SWITCH_PADDING_TIME = 1.0   # silence gap inserted at each transition
```

### Text-to-Speech
- Google TTS (gTTS) integration
- Mumble text command: `!speak <text>`
- Automatic MP3 generation
- Format validation (detects API errors)
- Rate limiting detection
- Volume boost control (default: 1.0x)

### Audio Processing
- **VAD**: Voice Activity Detection with configurable threshold (enabled by default)
- **Noise Gate**: Removes background noise
- **AGC**: Automatic Gain Control for consistent levels
- **HPF**: High-pass filter (removes low-frequency rumble)
- **Spectral/Wiener Filter**: Advanced noise suppression
- **Echo Cancellation**: Reduces feedback

### Streaming
- **Darkice Integration**: Stream to Icecast server
- **Broadcastify Support**: Live scanner feed
- Mixed audio output via named pipe
- Configurable bitrate and format

## Quick Start

### Requirements
- Raspberry Pi 4 (or similar Linux system)
- Python 3.7+
- AIOC USB audio interface
- Mumble server access

### Installation

```bash
# Install system dependencies
sudo apt-get update
sudo apt-get install -y python3 python3-pip portaudio19-dev libsndfile1

# Install Python packages
pip3 install pymumble pyaudio soundfile resampy gtts --break-system-packages

# Clone repository
git clone <your-repo-url>
cd mumble-radio-gateway

# Configure
nano gateway_config.txt
# Edit Mumble server settings, radio device, etc.

# Run
python3 mumble_radio_gateway.py
```

### Basic Configuration

Edit `gateway_config.txt`:

```ini
# Mumble Server
MUMBLE_SERVER = your.mumble.server
MUMBLE_PORT = 64738
MUMBLE_USERNAME = RadioGateway
MUMBLE_PASSWORD = yourpassword

# Radio Interface (AIOC)
AIOC_INPUT_DEVICE =    # Auto-detected
AIOC_OUTPUT_DEVICE =   # Auto-detected
AIOC_PTT_CHANNEL = 3

# Enable features (these are the defaults)
ENABLE_VAD = true      # Voice Activity Detection
ENABLE_TTS = true      # Text-to-Speech
ENABLE_SDR = true      # SDR1 receiver
SDR_DUCK = true        # SDR1 ducking (silence when higher priority audio active)

# Optional second SDR receiver
ENABLE_SDR2 = false    # SDR2 disabled by default
SDR2_DEVICE_NAME = hw:3,1
SDR2_PRIORITY = 2      # Ducked by SDR1 and radio RX
```

## SDR Integration

### Setup ALSA Loopback

SDR audio uses ALSA loopback devices for piping audio from SDR software (like SDRconnect) into the gateway.

```bash
# Load loopback module
sudo modprobe snd-aloop

# Make permanent
echo "snd-aloop" | sudo tee -a /etc/modules

# Verify
aplay -l | grep Loopback
```

### Loopback Device Pairing

ALSA loopback devices work in **pairs**. For dual SDR you need two separate loopback card numbers:

```
┌──────────────────────────────────────────────┐
│  ALSA Loopback Device Pairing                │
├──────────────────────────────────────────────┤
│  hw:X,0 (playback) ↔ hw:X,1 (capture)       │
│                                              │
│  SDR1 software → hw:2,0                     │
│  Gateway SDR1  ← hw:2,1                     │
│                                              │
│  SDR2 software → hw:3,0  (different card)   │
│  Gateway SDR2  ← hw:3,1                     │
└──────────────────────────────────────────────┘
```

To get two loopback cards, load the module with `numlids=2`:
```bash
sudo modprobe snd-aloop numlids=2

# Make permanent
echo "options snd-aloop numlids=2" | sudo tee /etc/modprobe.d/snd-aloop.conf
echo "snd-aloop" | sudo tee -a /etc/modules
```

### Configuration

```ini
# SDR1 (cyan bar)
ENABLE_SDR = true
SDR_DEVICE_NAME = hw:2,1
SDR_PRIORITY = 1           # Higher priority (ducks SDR2)
SDR_DUCK = true            # Ducked by radio RX
SDR_MIX_RATIO = 1.0
SDR_DISPLAY_GAIN = 1.0
SDR_AUDIO_BOOST = 1.0
SDR_BUFFER_MULTIPLIER = 8

# SDR2 (magenta bar) — disabled by default
ENABLE_SDR2 = false
SDR2_DEVICE_NAME = hw:3,1
SDR2_PRIORITY = 2          # Lower priority (ducked by SDR1)
SDR2_DUCK = true           # Ducked by radio RX and SDR1
SDR2_MIX_RATIO = 1.0
SDR2_DISPLAY_GAIN = 1.0
SDR2_AUDIO_BOOST = 1.0
SDR2_BUFFER_MULTIPLIER = 8
```

### Testing SDR Audio

```bash
# Test SDR input
python3 test_sdr_loopback.py

# Test loopback pairing
python3 test_loopback_bidirectional.py
```

## Keyboard Controls

Press keys during operation to control the gateway:

### Mute Controls
- `t` = TX Mute (Mumble → Radio)
- `r` = RX Mute (Radio → Mumble)
- `s` = SDR1 Mute
- `x` = SDR2 Mute
- `m` = Global Mute (mutes all audio)

### SDR Controls
- `d` = **Toggle SDR1 Ducking** (duck vs. mix mode)
- `s` = Toggle SDR1 Mute
- `x` = Toggle SDR2 Mute

### Audio Controls
- `v` = Toggle VAD on/off
- `,` = Volume Down (Radio → Mumble)
- `.` = Volume Up (Radio → Mumble)
- `p` = Manual PTT Toggle (override auto-PTT)

### Processing Controls
- `n` = Toggle Noise Gate
- `f` = Toggle High-Pass Filter
- `a` = Toggle AGC
- `w` = Toggle Wiener Filter (spectral noise suppression)
- `e` = Toggle Echo Cancellation
- `x` = Toggle Stream Health Management

### File Playback Controls
- `1-9` = Play announcement files
- `0` = Play Station ID
- `-` = Stop playback

## Status Bar

```
ACTIVE: ✓ M:✓ PTT:-- VAD:✗ -48dB TX:[███--] 32% RX:[██---] 24% SDR:[███--] 30% Vol:1.0x 1234567890 [D]
```

### Status Indicators

| Indicator | Meaning |
|-----------|---------|
| **ACTIVE/IDLE/STOP** | Audio capture status (✓/⚠/✗) |
| **M:✓/✗** | Mumble connected/disconnected |
| **PTT:ON/--** | Push-to-talk active/inactive |
| **PTT:M-ON** | Manual PTT mode active |
| **VAD:✗** | VAD disabled (red X) |
| **VAD:🔊** | VAD active (green speaker) |
| **VAD:--** | VAD silent (gray) |
| **-48dB** | Current VAD level (when enabled) |

### Audio Level Bars

| Bar | Color | Meaning |
|-----|-------|---------|
| **TX:[bar]** | Red | Mumble → Radio (radio TX) |
| **RX:[bar]** | Green | Radio → Mumble (radio RX) |
| **SDR1:[bar]** | Cyan | SDR1 receiver audio level |
| **SDR2:[bar]** | Magenta | SDR2 receiver audio level (if enabled) |

**Bar States:**
```
Normal:  [███-------] 30%   ← Active audio
Muted:   [---MUTE---]  M    ← Channel muted
Ducked:  [---DUCK---]  D    ← SDR being ducked (SDR only)
```

**All bars have fixed width** (17 characters) to prevent line length changes.

### File Status (0-9)
- **Green number** = File loaded
- **Red number** = File currently playing
- **White number** = No file assigned

### Processing Flags

Flags appear in yellow brackets at the end: `[N,F,A,D]`

| Flag | Meaning |
|------|---------|
| **N** | Noise Gate enabled |
| **F** | High-Pass Filter enabled |
| **A** | AGC enabled |
| **W** | Wiener Filter enabled |
| **S** | Spectral Suppression enabled |
| **E** | Echo Cancellation enabled |
| **D** | SDR Ducking enabled |
| **X** | Stream Health DISABLED |

### Diagnostics

| Indicator | Meaning |
|-----------|---------|
| **Vol:1.0x** | RX volume multiplier |
| **R:5** | Stream restart count (only if >0) |

## Audio Level Calculation

All bars (TX, RX, SDR) use the same RMS calculation method:

```python
# Calculate RMS (Root Mean Square)
RMS = sqrt(sum(samples²) / count)

# Convert to dB
dB = 20 * log10(RMS / 32767)

# Map -60dB to 0dB → 0-100%
Level = (dB + 60) * (100/60)
```

**Smoothing:**
- **Fast attack**: Immediate response on level increase
- **Slow decay**: `new_level = old * 0.7 + current * 0.3`

This creates responsive bars that show peaks immediately but decay smoothly.

## Architecture

### Audio Flow Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                         AUDIO INPUTS                              │
├──────────────────────────────────────────────────────────────────┤
│  Priority 0:  File Playback (10 slots) ───────┐                  │
│  Priority 1:  Radio RX (AIOC)         ────────┼──→ MIXER         │
│  Priority 2:  SDR (ALSA Loopback)     ────────┤                  │
│  Priority 3:  EchoLink (Named Pipes)  ────────┘                  │
│                                                                   │
│  Mumble RX  ──────────────────────────────────→ Radio TX (PTT)   │
│             (direct callback path, bypasses mixer entirely)       │
└──────────────────────────────────────────────────────────────────┘
                                                  │
                                                  ↓
┌──────────────────────────────────────────────────────────────────┐
│                      AUDIO PROCESSING                             │
├──────────────────────────────────────────────────────────────────┤
│  • Voice Activity Detection (VAD)                                │
│  • Noise Gate / Spectral Suppression                             │
│  • High-Pass Filter (HPF)                                        │
│  • Automatic Gain Control (AGC)                                  │
│  • Echo Cancellation                                              │
└──────────────────────────────────────────────────────────────────┘
                                                  │
                                                  ↓
┌──────────────────────────────────────────────────────────────────┐
│                        AUDIO OUTPUTS                              │
├──────────────────────────────────────────────────────────────────┤
│  ├─→ Radio TX (AIOC + Auto-PTT)                                  │
│  ├─→ Mumble TX                                                    │
│  ├─→ Darkice Stream (Icecast/Broadcastify)                       │
│  └─→ EchoLink TX (if enabled)                                    │
└──────────────────────────────────────────────────────────────────┘
```

### Priority System

When multiple sources provide audio simultaneously:

```
Mumble RX (direct path — bypasses mixer):
  ├─ Audio captured via callback, PTT keyed immediately
  ├─ Written directly to AIOC output → Radio TX
  └─ Suppressed only when TX muted or manual PTT mode active

Priority 0 (File Playback):
  ├─ Triggers PTT → announcement sent to Radio TX
  ├─ Concurrent Radio RX still forwarded to Mumble
  └─ Highest priority within mixer

Priority 1 (Radio RX / AIOC):
  ├─ Radio RX → Mumble TX (and stream if enabled)
  └─ Forwarded to all listeners

Priority 2 (SDR):
  ├─ DUCKED when Priority 0 or 1 active (default)
  ├─ OR mixed at SDR_MIX_RATIO (if ducking disabled)
  └─ Goes to Mumble TX only

Priority 3 (EchoLink):
  └─ Lowest priority
```

### SDR Ducking Logic

```
┌─────────────────────────────────────────────────────────────┐
│              SDR Ducking Decision Tree (per SDR)             │
└─────────────────────────────────────────────────────────────┘

Is this SDR enabled?
  └─ NO  → not in mix
  └─ YES ↓

Is this SDR muted (s/x key)?
  └─ YES → stream drained silently, not in mix
  └─ NO  ↓

Is SDR ducking enabled (SDR_DUCK / SDR2_DUCK)?
  └─ NO  → always mixed at SDR_MIX_RATIO
  └─ YES ↓

Is Radio RX (AIOC) active? [after attack/release/padding]
  └─ YES → ducked
  └─ NO  ↓

Is a higher-priority SDR active (lower priority number)?
  └─ YES → ducked
  └─ NO  → passes through normally
```

Both SDRs go through the same pipeline independently, so SDR1 can be playing while SDR2 is ducked, or vice versa.

## Configuration Reference

### Core Audio Settings

```ini
AUDIO_RATE = 48000           # Sample rate (Hz) - 48kHz recommended
AUDIO_CHANNELS = 1           # Mono (1) recommended for radio
AUDIO_BITS = 16              # Bit depth - standard
AUDIO_CHUNK_SIZE = 9600      # Buffer size (samples)
                             # Larger = more stable, more latency
                             # 9600 = 200ms at 48kHz (recommended)

INPUT_VOLUME = 1.0           # Radio RX → Mumble volume (0.1 - 3.0)
OUTPUT_VOLUME = 1.0          # Mumble → Radio TX volume (0.1 - 3.0)
```

### Mumble Quality Settings

```ini
# Opus encoder outgoing bitrate (bits/second)
# Applied via set_bandwidth() on connection. Previous versions never applied
# this — the library defaulted to 50kbps regardless of the config value.
# 40000 = good  |  72000 = high (recommended)  |  96000 = maximum
MUMBLE_BITRATE = 72000

# Variable Bit Rate — Opus adapts bitrate to content
# true = lower bitrate during silence, higher during speech (recommended)
MUMBLE_VBR = true
```

> **Note:** The Opus encoder is also configured at startup with `complexity=10` (maximum quality, negligible CPU cost for mono voice) and `signal=voice` (voice-specific optimisation). These are not config file options — they are always applied.

### VAD (Voice Activity Detection) Settings

```ini
ENABLE_VAD = true            # Enable VAD (default: true)
VAD_THRESHOLD = -40          # Threshold in dBFS (-50 to -20)
                             # More negative = more sensitive
VAD_ATTACK = 0.02            # How fast to activate (seconds)
VAD_RELEASE = 0.3            # Hold time after silence (seconds)
VAD_MIN_DURATION = 0.1       # Minimum transmission length (seconds)
```

### PTT (Push-to-Talk) Settings

```ini
AIOC_PTT_CHANNEL = 3         # GPIO channel (1, 2, or 3)
PTT_ACTIVATION_DELAY = 0.1   # Pre-PTT delay (seconds) — squelch tail settle time
PTT_RELEASE_DELAY = 0.5      # Post-PTT tail (seconds)

# Announcement / file playback PTT key-up delay
# After PTT is keyed, audio is held for this many seconds while the radio
# transmitter fully activates.  File position does not advance during this
# window, so no audio is lost.
PTT_ANNOUNCEMENT_DELAY = 0.5 # seconds (default 0.5 — increase if first syllable is clipped)
```

### SDR Integration Settings

Both SDR inputs share the same set of parameters; SDR2 uses the `SDR2_` prefix.

```ini
# ── SDR1 (cyan bar) ──────────────────────────────────
ENABLE_SDR = true                # Enable SDR1
SDR_DEVICE_NAME = hw:2,1         # ALSA capture device
SDR_PRIORITY = 1                 # Duck priority (lower = higher priority)
SDR_DUCK = true                  # Duck when radio RX or higher-priority SDR active
SDR_MIX_RATIO = 1.0              # Volume when ducking disabled (0.0-1.0)
SDR_DISPLAY_GAIN = 1.0           # Status bar sensitivity (1.0-10.0)
SDR_AUDIO_BOOST = 1.0            # Actual volume boost (1.0-10.0)
SDR_BUFFER_MULTIPLIER = 8        # Buffer size multiplier (1-16)

# ── SDR2 (magenta bar) ───────────────────────────────
ENABLE_SDR2 = false              # Enable SDR2 (disabled by default)
SDR2_DEVICE_NAME = hw:3,1        # Must be a different device from SDR1
SDR2_PRIORITY = 2                # Higher number = lower priority (ducked by SDR1)
SDR2_DUCK = true                 # Duck when radio RX or SDR1 active
SDR2_MIX_RATIO = 1.0
SDR2_DISPLAY_GAIN = 1.0
SDR2_AUDIO_BOOST = 1.0
SDR2_BUFFER_MULTIPLIER = 8
```

**Priority rules:**
- Radio RX (AIOC) always ducks **all** SDRs, regardless of priority settings
- Between SDRs: lower `SDR_PRIORITY` number ducks the higher number
- Set both to the same priority to mix them equally with no inter-SDR ducking

### Source Switching Settings

```ini
# Attack: continuous signal required before a duck switch is triggered.
# Any silence resets this timer — prevents transient noises causing a switch.
SIGNAL_ATTACK_TIME = 0.5         # seconds (default 0.5)

# Release: continuous silence required before SDR resumes after a duck.
# Prevents SDR popping back on during natural speech pauses.
SIGNAL_RELEASE_TIME = 2.0        # seconds (default 2.0)

# Padding: silence inserted at each transition (duck-out AND duck-in).
# Creates a clean audible break so changeovers are never jarring.
#   duck-out: [SDR] → silence → [radio takes over]
#   duck-in:  [radio ends] → silence → [SDR resumes]
SWITCH_PADDING_TIME = 0.2        # seconds (default 0.2)
```

### File Playback Settings

```ini
ENABLE_PLAYBACK = true               # Enable file playback
PLAYBACK_DIRECTORY = audio           # Directory for audio files
PLAYBACK_ANNOUNCEMENT_INTERVAL = 0   # Auto-play interval (0 = disabled)

PTT_ANNOUNCEMENT_DELAY = 0.5         # Seconds after PTT key-up before audio starts
                                     # Radio TX must be keyed before audio begins
                                     # File position held during this window (no audio lost)
```

**File Naming:**
- Station ID: `station_id.mp3` or `station_id.wav` → Key 0
- Announcements: `1_welcome.mp3` → Key 1, `2_emergency.wav` → Key 2, etc.
- Auto-assigned alphabetically if no number prefix

### Text-to-Speech Settings

```ini
ENABLE_TTS = true            # Enable TTS (requires gtts)
ENABLE_TEXT_COMMANDS = true  # Allow Mumble text commands
TTS_VOLUME = 1.0             # TTS volume boost (1.0-3.0)
PTT_TTS_DELAY = 1.0          # Silence padding before TTS (seconds)
```

### Audio Processing Settings

```ini
# Noise Gate
ENABLE_NOISE_GATE = false
NOISE_GATE_THRESHOLD = -40        # dBFS threshold
NOISE_GATE_ATTACK = 0.01          # Attack time (seconds)
NOISE_GATE_RELEASE = 0.1          # Release time (seconds)

# High-Pass Filter
ENABLE_HIGHPASS_FILTER = false
HIGHPASS_CUTOFF_FREQ = 300        # Hz (300 = good for voice)

# AGC
ENABLE_AGC = false

# Noise Suppression
ENABLE_NOISE_SUPPRESSION = false
NOISE_SUPPRESSION_METHOD = spectral   # spectral or wiener
NOISE_SUPPRESSION_STRENGTH = 0.5      # 0.0 to 1.0

# Echo Cancellation
ENABLE_ECHO_CANCELLATION = false
```

### Streaming Settings

```ini
ENABLE_STREAM_OUTPUT = true       # Enable Broadcastify/Icecast
STREAM_SERVER = audio9.broadcastify.com
STREAM_PORT = 80
STREAM_PASSWORD = yourpassword
STREAM_MOUNT = /yourmount
STREAM_NAME = Radio Gateway
STREAM_BITRATE = 16               # kbps (16 typical for scanner)
STREAM_FORMAT = mp3               # mp3 or ogg
```

### EchoLink Settings

```ini
ENABLE_ECHOLINK = false
ECHOLINK_RX_PIPE = /tmp/echolink_rx
ECHOLINK_TX_PIPE = /tmp/echolink_tx
ECHOLINK_TO_MUMBLE = true
ECHOLINK_TO_RADIO = false
RADIO_TO_ECHOLINK = true
MUMBLE_TO_ECHOLINK = false
```

### Advanced Settings

```ini
# Stream Health
ENABLE_STREAM_HEALTH = false        # Auto-restart on errors
STREAM_RESTART_INTERVAL = 60        # Restart every N seconds
STREAM_RESTART_IDLE_TIME = 3        # When idle for N seconds

# Diagnostics
VERBOSE_LOGGING = false             # Detailed debug output
STATUS_UPDATE_INTERVAL = 1          # Status bar update rate (seconds)
```

## Troubleshooting

### SDR Audio Issues

**Problem: No audio from SDR**
```bash
# Check if snd-aloop is loaded
lsmod | grep snd_aloop

# Load module if needed
sudo modprobe snd-aloop

# Verify device exists
arecord -l | grep Loopback
```

**Problem: SDR bar frozen**
- Ensure RX is not muted (press `r` to unmute)
- Check that SDRconnect or other SDR software is running
- Verify correct device pairing (hw:X,0 ↔ hw:X,1)

**Problem: Stuttering SDR audio**
- Increase `SDR_BUFFER_MULTIPLIER` (try 16)
- Check CPU usage (`htop`)
- Ensure SDR software is outputting to correct device

**Problem: SDR always silent**
- Press `d` to check if ducking is enabled (SDR1), check `SDR2_DUCK` in config for SDR2
- Press `s` (SDR1) or `x` (SDR2) to ensure the SDR is not muted
- Check `SDR_AUDIO_BOOST` / `SDR2_AUDIO_BOOST` (try 2.0)

**Problem: SDR2 never plays (always ducked by SDR1)**
- Check `SDR_PRIORITY` and `SDR2_PRIORITY` — if SDR1 has a lower number it will always duck SDR2 when both have signal
- Set `SDR2_DUCK = false` to mix SDR2 alongside SDR1 regardless of priority
- Or set both priorities equal (e.g. both = 1) to disable inter-SDR ducking

### TTS Issues

**Problem: "gTTS returned HTML error page"**
- Rate limited by Google
- Wait 1-2 minutes and try again
- Check internet connection

**Problem: TTS audio distorted**
- Reduce `TTS_VOLUME` (try 0.5)
- Check network quality

### Audio Quality Issues

**Problem: Choppy audio**
- Increase `AUDIO_CHUNK_SIZE` (try 19200)
- Enable `ENABLE_STREAM_HEALTH = true`

**Problem: Background noise**
- Enable `ENABLE_NOISE_GATE = true`
- Adjust `VAD_THRESHOLD` (more negative = more sensitive)

**Problem: Low volume**
- Increase `INPUT_VOLUME` or `OUTPUT_VOLUME`
- For SDR: increase `SDR_AUDIO_BOOST`

**Problem: -9999 ALSA errors**
- Increase `AUDIO_CHUNK_SIZE` (try 9600 or 19200)
- Enable `ENABLE_STREAM_HEALTH = true`
- Move AIOC to different USB port

### PTT Issues

**Problem: PTT doesn't activate**
- Check `AIOC_PTT_CHANNEL` (try 1, 2, or 3)
- Verify AIOC device is detected
- Check `PTT_ACTIVATION_DELAY` (try 0.1)

**Problem: PTT releases too quickly**
- Increase `PTT_RELEASE_DELAY` (try 0.8)

**Problem: PTT stuck on**
- Check manual PTT mode (press `p` to toggle off)
- Disable file playback if files are looping
- Restart gateway

**Problem: Announcement first syllable clipped**
- Increase `PTT_ANNOUNCEMENT_DELAY` (try 1.0 or 1.5)
- Radio needs more time to key up before audio begins

**Problem: Announcement sounds like it starts mid-sentence**
- Ensure `PTT_ANNOUNCEMENT_DELAY` is set (default 0.5)
- This window holds the file position frozen — no audio is lost during key-up

## Advanced Features

### Darkice Streaming

Stream mixed audio to Icecast/Broadcastify:

```ini
ENABLE_STREAM_OUTPUT = true
STREAM_OUTPUT_PIPE = /tmp/gateway_stream.pcm
```

Configure Darkice to read from the pipe. See `start.sh` for automatic setup.

### EchoLink Integration

Connect to EchoLink via TheLinkBox:

```ini
ENABLE_ECHOLINK = true
ECHOLINK_INPUT_PIPE = /tmp/echolink_in.pcm
ECHOLINK_OUTPUT_PIPE = /tmp/echolink_out.pcm
```

### Periodic Station ID

Automatic station identification:

```ini
PLAYBACK_ANNOUNCEMENT_INTERVAL = 3600  # Every hour
```

Place `station_id.wav` or `station_id.mp3` in the audio directory.

### Mumble Text Commands

Send commands via Mumble text chat:

- `!speak <text>` - Generate TTS and broadcast on radio
- `!play <0-9>` - Play announcement file
- `!status` - Show gateway status report
- `!help` - Show available commands

## Development

### Project Structure

```
mumble-radio-gateway/
├── mumble_radio_gateway.py     # Main application
├── gateway_config.txt           # Configuration file
├── start.sh                     # Startup script (with Darkice)
├── README.md                    # This file
├── gateway_flowchart.jpg        # Architecture diagram
└── audio/                       # Announcement files directory
    ├── station_id.mp3
    ├── 1_welcome.mp3
    └── ...
```

### Adding Audio Sources

Extend the `AudioSource` class:

```python
class MySource(AudioSource):
    def __init__(self, name, config):
        super().__init__(name, config)
        self.priority = 2  # Set priority
        self.ptt_control = False  # Trigger PTT?
    
    def get_audio(self, chunk_size):
        # Return (audio_data, ptt_required)
        return data, False
    
    def is_active(self):
        return True  # Is source active?
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## Credits

- pymumble: Mumble Python library
- gTTS: Google Text-to-Speech
- PyAudio: Python audio interface

## Changelog

### Recent Fixes & Improvements

**Audio quality and CPU performance improvements**
- All RMS calculations replaced with numpy vectorized operations (10–100× faster on Pi) — reduces per-loop CPU usage and the risk of AIOC buffer overflows causing silent sample drops
- AIOC input stream buffer increased from 1× to 4× chunk size (matching output), giving 800 ms of hardware headroom vs 200 ms previously — absorbs OS/GIL scheduling pauses
- `MUMBLE_BITRATE` and `MUMBLE_VBR` are now actually applied to the Mumble client via `set_bandwidth()` — previously the library always defaulted to 50 kbps regardless of the config value
- Opus encoder configured with `complexity=10` (max quality) and `signal=voice` on startup
- Code defaults corrected to match config: `PTT_RELEASE_DELAY` 0.3→0.5, `ENABLE_VOX` true→false, `VOX_THRESHOLD` −40→−30, `NOISE_GATE_THRESHOLD` −32→−40, `HIGHPASS_CUTOFF_FREQ` 120→300

**Announcement playback — PTT and stuttering fixes**
- File playback audio is now treated as deterministically active; it no longer goes through the SDR attack hysteresis timer that was causing 0.5 s of the real audio to be discarded before PTT triggered, followed by a 1.0 s silence gap that dropped PTT altogether
- Duck-out transition padding no longer suppresses file playback audio — it applies only to SDR/radio transitions as intended; SDRs are still ducked immediately
- New `PTT_ANNOUNCEMENT_DELAY` (default 0.5 s): after PTT is keyed, file position is held frozen for this window while the radio transmitter activates; no audio is consumed or lost during key-up
- Previously the delay was applied by discarding already-read chunks; now the file source returns silence without advancing position so the start of every announcement is preserved

**Default parameter updates**
- `PTT_ACTIVATION_DELAY` 0.0 → 0.1, `VAD_THRESHOLD` −33 → −40, `SIGNAL_ATTACK_TIME` 0.1 → 0.5, `SIGNAL_RELEASE_TIME` 0.5 → 1.0, `SWITCH_PADDING_TIME` 0.2 → 1.0, `PTT_TTS_DELAY` 0.25 → 1.0, `SDR_DEVICE_NAME` hw:5,1 → hw:6,1

**Source switching rework**
- Attack timer now requires CONTINUOUSLY unbroken signal; any silence resets it so transient noise can never trigger a duck
- New `SWITCH_PADDING_TIME`: brief silence inserted at both transition points (duck-out and duck-in) for clean, non-jarring changeovers
- Duck state machine tracks transitions correctly and extends mute window through padding

**Radio RX during announcement playback**
- AIOC radio receive audio now continues flowing to Mumble and the streaming output while an announcement is being transmitted on the radio
- Previously Mumble listeners were cut off for the duration of any file playback

**SDR audio buffer fix (mute/unmute)**
- SDRSource now always drains the PyAudio input stream even when muted, preventing a burst of stale buffered audio on unmute — mirrors the existing fix in AIOCRadioSource

### Previous - SDR Ducking & Status Improvements
- **Added**: SDR audio ducking (silence SDR when other audio active)
- **Added**: Runtime ducking toggle (`d` key)
- **Added**: Status bar duck indicator (`[D]` flag)
- **Added**: Fixed-width status bars (17 chars all states)
- **Changed**: VAD now enabled by default
- **Fixed**: SDR bar freeze when RX muted (buffer drain issue)
- **Fixed**: Status line length changes between states

### Phase 5 - SDR Integration
- Added SDR receiver input via ALSA loopback
- Multi-source audio mixer with priority system
- Independent mute controls per source
- RMS + dB level calculation for all sources
- TTS format validation and error detection
- Clean shutdown without ALSA errors

### Phase 4 - TTS & Commands
- Text-to-Speech integration
- Mumble text command processing
- File playback system

### Phase 3 - EchoLink & Streaming
- EchoLink bridge support
- Darkice/Icecast streaming

### Phase 2 - Audio Processing
- VAD, noise gate, AGC
- Multiple filter types

### Phase 1 - Core Gateway
- Bidirectional Mumble ↔ Radio
- AIOC interface support
- PTT control
