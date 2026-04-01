# Mixer v2.0 — Bus Architecture Reference

## Status: COMPLETE — v2.0 released 2026-03-31

## Overview

The v2.0 architecture replaced the monolithic AudioMixer with a bus-based
routing system. All radios are plugins with a standard interface. All audio
flow is controlled by routing connections in a visual node editor.

## Bus Types

### 1. Listen Bus

**Purpose:** Monitor one or more sources, optionally route to sinks.

- Any number of sources, each with a priority number
- Audio from all sources is mixed (additive with soft tanh limiter)
- Ducking is priority-based: higher priority source ducks lower priority
- Signal detection with hysteresis (attack/release) prevents chatter
- Fade-in/fade-out at transitions prevents clicks
- Zero or more sinks: Broadcastify, Mumble, Speaker, Recording, etc.
- Replaces the old "simultaneous" mixing mode

### 2. Solo Bus

**Purpose:** Standalone control of a single radio with source/sink routing.

- One primary radio endpoint
- Additional sources can be attached (Web Mic, File Playback, Announcements)
- Additional sinks can be attached (Speaker, Recording)
- Sources route to the radio's TX
- Radio's RX routes to the sinks
- PTT control comes from attached sources with ptt_control=True
- Building block: every radio starts as a solo bus, then connects to other
  busses for cross-radio functionality

### 3. Duplex Repeater Bus

**Purpose:** Full duplex cross-link between two radios.

- Two radio endpoints assigned: Side A and Side B
- A's RX routes to B's TX and B's RX routes to A's TX simultaneously
- Both directions active at the same time (full duplex)
- Each side has independent PTT control
- Radios must be on different frequencies

### 4. Simplex Repeater Bus

**Purpose:** Half-duplex store-and-forward between two radios.

- Two radio endpoints assigned
- When Side A receives, audio is buffered
- When A's RX ends, the buffered audio is transmitted on Side B
- Then the link reverses: B's RX buffers, sent to A when B finishes
- Only one direction active at a time
- Configurable tail timer and courtesy tone

## Plugin Model

All radios are plugins with a standard interface. Existing source classes
were refactored into plugins (not wrapped).

**Standard interface** (bus talks to these):
```python
class RadioPlugin:
    name: str
    capabilities: set          # {rx, tx, ptt, frequency, ctcss, ...}
    processing: ProcessChain   # gate -> HPF -> LPF -> notch -> gain
    enabled: bool

    def setup(self) -> bool
    def teardown(self)
    def get_audio(self, chunk_size) -> (bytes, bool)  # (pcm, ptt_needed)
    def put_audio(self, pcm: bytes)                   # TX audio in
    def get_status(self) -> dict
```

**Implemented plugins:**
- `SDRPlugin` — RSPduo dual tuner with internal master/slave ducking.
  Bus sees one source. Each tuner has its own frequency, processing chain,
  and UI controls within the plugin.
- `TH9800Plugin` — AIOC sound card + CAT serial + relay PTT
- `D75Plugin` — Bluetooth audio + TCP CAT proxy
- `KV4PPlugin` — CP2102 USB serial + Opus codec

Processing is owned by the plugin. The bus receives clean, processed PCM.

## Sink Model

A sink is anything that consumes audio. All sinks are gated by routing
connections -- no audio flows to a sink unless it is wired in the routing UI.

Implemented sinks: Broadcastify (Icecast direct), Mumble, Speaker, Recording,
Radio TX (KV4P/D75/TH-9800), Remote Audio.

## Routing

The routing table is the single source of truth for audio flow. It is
configured via the Drawflow visual node editor on the Routing page.

Sources, busses, and sinks appear as nodes in a 3-column layout. Connections
between nodes define audio flow. Live level bars show audio activity in
each node. Mute buttons and gain sliders are available per-node.

Routing configuration is saved/loaded as JSON.

A source can appear on multiple busses (e.g., D75 on both a solo bus and
a repeater bus). TX audio from multiple busses is mixed before delivery.

## Bus API

```python
class AudioBus:
    name: str
    bus_type: str  # 'listen', 'duplex_repeater', 'simplex_repeater', 'solo'

    def tick(self, chunk_size) -> BusOutput:
        """Called once per audio cycle."""

class BusOutput:
    audio: dict          # {sink_name: pcm_bytes}
    ptt: dict            # {radio_name: bool}
    active_sources: list
    status: dict         # ducking state, levels, etc.
```

The BusManager runs all routing-configured busses alongside the main loop.

## Key Files

- `audio_bus.py` — Bus classes, DuckGroup, SourceSlot, mixing utilities
- `sdr_plugin.py` — SDRPlugin (RSPduo dual tuner)
- `th9800_plugin.py` — TH9800Plugin
- `d75_plugin.py` — D75Plugin
- `kv4p_plugin.py` — KV4PPlugin
- `gateway_core.py` — Bus instantiation, main loop, sink delivery
- `gateway_utils.py` — Extracted utility classes
- `web_pages/` — 13 static HTML pages including routing UI
