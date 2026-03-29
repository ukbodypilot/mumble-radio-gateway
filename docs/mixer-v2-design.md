# Mixer v2.0 — Bus Architecture Design

## Status: DRAFT — under discussion

## Problem

The v1 mixer was designed for one radio (AIOC) and one or two SDR receivers.
It grew to handle D75, KV4P, Gateway Link endpoints, web mic, announcements,
room monitor, and file playback — but the architecture never changed. The
result:

- Duck rules are hardcoded by source name (`startswith("SDR")`, `"Radio"`)
- Routing is implicit, scattered across gateway_core.py's main loop
- Adding a new source means touching mixer internals
- `get_mixed_audio` returns an 8-tuple leaking implementation details
- SDR-specific logic dominates half the mixer code

## Core Concept: Audio Busses

A **bus** is a named audio path with a defined behaviour. Sources and sinks
are assigned to busses. The bus type determines what happens to the audio.

Each source does its own processing (gate, HPF, LPF, notch, gain) before
handing clean PCM to the bus. The bus only handles routing, mixing, ducking,
and PTT coordination.

A source can be assigned to multiple busses. A sink can receive from multiple
busses.

## Bus Types

### 1. Duplex Repeater Bus

**Purpose:** Full duplex cross-link between two radios.

- Exactly 2 radio endpoints assigned: one as Side A, one as Side B
- A's RX audio routes to B's TX simultaneously, and B's RX routes to A's TX
- Both directions active at the same time (full duplex)
- Each side has independent PTT control
- Processing happens on each source before audio enters the bus

**Example:** D75 on 2m ↔ TH-9800 on 70cm. Someone talks on 2m, it goes out
on 70cm. Someone talks on 70cm, it goes out on 2m. Both can happen at once.

**Requirements:**
- Both radios must be on different frequencies (or hardware that supports
  simultaneous RX+TX)
- PTT on each side is controlled by the bus when the other side has RX audio
- Echo prevention: audio sent TO a radio's TX must not loop back from its RX
  (hardware frequency split handles this, but the bus should also gate it)

### 2. Simplex Repeater Bus

**Purpose:** Half-duplex store-and-forward between two radios.

- 2 radio endpoints assigned
- When Side A receives, audio is buffered
- When A's RX ends (signal release), the buffered audio is transmitted on Side B
- Then the link reverses: B's RX buffers, sent to A when B finishes
- Only one direction active at a time
- Needs configurable tail timer (how long to wait after RX before TX)
- Needs configurable courtesy tone (optional beep between RX and TX)

**Lower priority than duplex repeater.**

### 3. Listen Bus

**Purpose:** Monitor one or more sources, optionally route to sinks.

- Any number of sources tagged in
- Audio from all sources is mixed together (additive with soft limiter)
- When multiple sources are active simultaneously, ducking rules apply:
  - Each source on the bus has a priority
  - Higher priority source ducks lower priority sources
  - Signal detection with hysteresis (attack/release) prevents chatter
  - Fade-in/fade-out at transitions prevents clicks
- No duck rules reference source names — only priority numbers
- Zero or more sinks tagged: Broadcastify, recording, AI processing, speaker,
  Mumble, Gateway Link endpoints
- This replaces the current "simultaneous" mixing mode

**Ducking within a listen bus:**
- Sources are ordered by priority (lower number = higher)
- When a higher-priority source has signal, lower-priority sources are ducked
- Transition padding (configurable) silences both briefly during duck-out
- Fade-in on duck-in (SDR resumes)
- No global "AIOC ducks SDRs" rule — it's just priority numbers on a bus

### 4. Solo Bus

**Purpose:** Standalone control of a single radio with its own source/sink
pipeline.

- One primary radio endpoint
- Additional sources can be attached (web mic, file playback, announcements)
- Additional sinks can be attached (speaker, recording)
- Sources route to the radio's TX
- Radio's RX routes to the sinks
- This is where you set up "web mic → D75" or "announcement → TH-9800"
- PTT control comes from the attached sources that have ptt_control=True

**This is the building block.** Every radio starts as a solo bus. You then
optionally connect it to other busses (listen, repeater) for cross-radio
functionality.

## Source Model

A source is anything that produces audio:

```
Source {
    name: str                  # "AIOC", "SDR1", "D75", "WebMic", etc.
    type: enum                 # radio, sdr, network, file, mic
    capabilities: set          # {rx, tx, ptt, frequency, ctcss, ...}
    processing: ProcessChain   # gate → HPF → LPF → notch → gain (per-source)
    bus_assignments: list      # which busses this source is on
    priority: int              # used for ducking within listen busses
    enabled: bool
}
```

Processing is owned by the source. The bus receives clean, processed PCM.

## Sink Model

A sink is anything that consumes audio:

```
Sink {
    name: str                  # "Broadcastify", "Mumble", "Speaker", "Recording"
    type: enum                 # stream, voip, local, file, ai
    bus_assignments: list      # which busses feed this sink
    enabled: bool
}
```

## Routing

The routing table is the single source of truth for audio flow:

```
Bus: "monitor" (listen)
  Sources: SDR1 (pri=1), SDR2 (pri=2), AIOC (pri=0)
  Sinks: Broadcastify, Mumble, Speaker
  Ducking: enabled

Bus: "repeater-link" (duplex)
  Side A: D75
  Side B: TH-9800

Bus: "d75-control" (solo)
  Radio: D75
  Sources: WebMic, Announcements
  Sinks: Speaker, Recording
```

A source appearing on multiple busses (e.g., D75 on both "repeater-link" and
"d75-control") means:
- D75's RX audio is delivered to both busses
- TX audio from both busses is mixed before going to D75's TX
- Priority/conflict resolution: repeater bus TX takes precedence over solo bus TX

## What Moves Where

### Stays in source classes (audio_sources.py):
- `get_audio()` — produces processed PCM
- Per-source processing chain (gate, HPF, LPF, notch, gain, boost)
- Source-specific buffer management (ring buffers, blob handling)
- Level metering

### New module: audio_bus.py
- Bus base class + DuplexRepeaterBus, SimplexRepeaterBus, ListenBus, SoloBus
- Routing table (source→bus, bus→sink assignments)
- Ducking state machine (moved from AudioMixer, generalized)
- Signal detection with hysteresis (moved from AudioMixer)
- Fade-in/fade-out (moved from AudioMixer)
- Transition padding
- `_mix_audio_streams` (additive mixing with soft limiter)

### Stays in gateway_core.py:
- Bus instantiation and wiring (replaces current mixer setup)
- Main loop calls bus.tick() instead of mixer.get_mixed_audio()
- PTT coordination (bus tells gateway when to key which radio)
- Sink delivery (bus outputs go to Mumble, Broadcastify, etc.)

### Changes in gateway_core.py:
- Remove the 8-tuple consumption pattern
- Remove hardcoded duck rules
- Remove SDR rebroadcast special case (becomes a duplex repeater bus)
- Routing becomes declarative (bus config) instead of imperative (if/else chains)

## Bus API

```python
class AudioBus:
    """Base class for all bus types."""
    name: str
    bus_type: str  # 'listen', 'duplex_repeater', 'simplex_repeater', 'solo'

    def tick(self, chunk_size) -> BusOutput:
        """Called once per audio cycle. Returns mixed/routed audio."""
        ...

class BusOutput:
    """What a bus produces each tick."""
    audio: dict          # {sink_name: pcm_bytes}  — per-sink output
    ptt: dict            # {radio_name: bool}       — PTT state per radio
    active_sources: list # which sources contributed this tick
    status: dict         # ducking state, levels, etc. for UI
```

Each bus type implements `tick()` differently:
- **ListenBus.tick():** collects audio from all sources, applies ducking,
  mixes, delivers to all sinks
- **DuplexRepeaterBus.tick():** gets RX from both sides, routes A→B and B→A,
  manages PTT on both
- **SoloBus.tick():** gets audio from attached sources, routes to radio TX;
  gets radio RX, routes to sinks

The main loop becomes:
```python
for bus in self.busses:
    output = bus.tick(chunk_size)
    # deliver output.audio to sinks
    # apply output.ptt to radios
```

## Configuration

Bus configuration in gateway_config.txt:

```ini
[busses]
# Listen bus for scanner/monitoring
LISTEN_BUS_SOURCES = SDR1:1, SDR2:2, AIOC:0
LISTEN_BUS_SINKS = Broadcastify, Mumble, Speaker
LISTEN_BUS_DUCKING = true

# Duplex repeater
DUPLEX_BUS_SIDE_A = D75
DUPLEX_BUS_SIDE_B = TH-9800

# Solo control for D75
D75_SOLO_SOURCES = WebMic, Announcements
D75_SOLO_SINKS = Speaker, Recording
```

Or: bus configuration could be a separate JSON file for more flexibility,
since the INI format is limiting for nested structures.

## Migration Path

1. **Branch v2.0-mixer from main**
2. **Build audio_bus.py** with ListenBus first (it covers the current use case)
3. **Wire ListenBus into gateway_core.py** replacing AudioMixer
4. **Verify parity** — the gateway works exactly as before with the new code
5. **Add SoloBus** — extract per-radio source/sink wiring
6. **Add DuplexRepeaterBus** — new capability
7. **Add SimplexRepeaterBus** — when needed
8. **Update web UI** — bus configuration page, routing visualization
9. **Merge to main** when stable

Step 3 is the critical one. If the ListenBus can replace AudioMixer with
identical behaviour, the rest is additive.

## Open Questions

1. **Bus config format:** INI in gateway_config.txt or separate JSON file?
2. **Web UI:** Do busses get their own page, or live on the existing Controls page?
3. **Multiple listen busses:** Can there be more than one? (e.g., one for
   Broadcastify with SDRs only, one for Mumble with everything)
4. **Link endpoints:** Does each Gateway Link endpoint auto-create a solo bus,
   or are they manually assigned?
5. **Announcement routing:** Do announcements go to a specific bus, or broadcast
   to all busses that have a TX radio?
6. **SDR rebroadcast:** Currently a special case. Becomes a duplex repeater bus
   with SDR as Side A and AIOC as Side B? Or stays special?
