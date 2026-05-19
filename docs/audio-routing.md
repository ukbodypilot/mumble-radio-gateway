# Audio Routing

Bus-based audio mixer with a visual drag-and-drop editor at `/routing`. Every audio path in the gateway — radio RX, SDR audio, file playback, browser mic, transcription input, Mumble TX, stream output, link endpoint audio — is wired by connecting sources to buses and buses to sinks. No hard-coded paths in Python.

## Overview

Pre-v2 the gateway had a hand-written routing matrix in `gateway_core.py`. Adding a new source or sink meant editing Python. The v2 mixer replaces that with **buses** — named lanes that accept any number of sources and feed any number of sinks. Routing is saved as JSON in `routing_config.json` and edited in the browser. Code change to add a route: zero.

## Bus types

| Type | Behaviour |
|------|-----------|
| **Listen** | One-way receive. Sources mix into the bus, bus drives sinks. The default and most common type. |
| **Solo** | One-way receive, but only one source is "active" at a time — the first to break squelch holds the bus until it falls silent + tail. Used when several sources share one sink and you don't want them stepping on each other. |
| **Duplex Repeater** | Two solo buses paired — input-side (RX) and output-side (TX-with-PTT). Audio crossing in triggers PTT on the output radio. |
| **Simplex Repeater** | Single bus that records-then-plays-back with mic-tail handling. Half-duplex repeat through one radio. |

## Routing UI

`/routing` page — Drawflow canvas, mouse-wheel zoom, drag nodes to position. Click a connection to delete; drag from a source's right port to a bus's left port to create. Active connections animate. Each node has a live VU-style level bar.

**NUL sink** — drops audio without playing it. Use when you want to record a bus (`R` button) without also routing it to anywhere audible.

## Per-bus processing

Every bus has a processing chain you can toggle from its node:

| Button | Effect |
|--------|--------|
| `G` | Noise gate (numba-jit, < 10µs/chunk) |
| `H` | High-pass filter (300 Hz default) |
| `L` | Low-pass filter (3 kHz default) |
| `N` | Notch filter (60 Hz default, Q=30) |
| **`D`** | Neural denoise — RNNoise *or* DeepFilterNet 3 (selectable per bus). Phase-aligned wet/dry mix; per-bus attenuation cap. Inference runs off the bus tick so a slow chunk doesn't stall audio. |

The `D` button has a sub-popover with the engine select (`rnnoise` / `deepfilternet`), wet/dry mix slider (0–100 %), and attenuation cap in dB (DFN only — caps how much the model can pull down; 18 dB default).

## Per-bus streaming

Each bus can independently expose:

- **PCM WebSocket** — raw 16-bit mono at 48 kHz to the browser, for the routing-page audio cues and monitor pages
- **MP3 stream** — lazy-started ffmpeg encoder, only launches when a browser first subscribes
- **VAD-gated transcription** — feeds the per-bus VAD + ASR worker (see [transcription-pool.md](transcription-pool.md))
- **Loop recording** — per-bus continuous capture (see [loop-recorder.md](loop-recorder.md))

Toggle from the bus's node. Disabled streams cost nothing — no encoder is started until at least one consumer.

## Speaker mode

Three modes — set in the routing UI:

- **virtual** — speaker is a metering-only sink. No audio actually plays on the host. Useful when the gateway runs headless and you don't want PortAudio holding a device handle.
- **auto** — try to open the default output device; fall back to virtual if it fails.
- **real** — try to open the default output device; warn if unavailable.

The mode persists across restarts (`SPEAKER_MODE` config key).

## Independent TX/RX muting

A radio plugin is one Python object but exposes itself to the router as **two** nodes: a source (RX, e.g. `th9800`) and a sink (TX, e.g. `th9800_tx`). Each has its own mute toggle. Muting the TX sink does NOT silence the RX source, and vice versa. Important for repeater chains where you want to keep listening even while transmit is muted.

## Bus tick architecture

A single bus tick thread (~960 samples / 20 ms cadence) drives every bus's mix step. Sinks that would block on the tick (Broadcastify encoder, automation recorder, Mumble TX, EchoLink) run **off-tick** on per-sink drain threads — `_enqueue_sink` stages a sink call into a bounded deque (maxlen 8, drop-oldest); a `SinkDrain-<id>` daemon thread drains it. This keeps the bus tick at sub-millisecond jitter even when one sink is slow.

The transcriber, speaker, link TX, loop recorder, and remote audio TX were off-tick by design from v2 onward.

## Configuration

The routing topology lives in **`routing_config.json`** (created on first save from the UI; not committed). Per-bus processing settings (filter toggles, denoise mix, denoise engine, loop hours) live inside the same file.

Static config keys that affect routing live in `[processing]` and `[switching]` sections of `gateway_config.txt`. See [config-reference.md](config-reference.md).

## Source pointers

- [`audio_bus.py`](../audio_bus.py) — `ListenBus`, `SoloBus`, `DuplexBus`, `SimplexBus` classes
- [`bus_manager.py`](../bus_manager.py) — bus lifecycle, routing reload, sink dispatch, per-bus AudioProcessor management
- [`audio_sources.py`](../audio_sources.py) — every source/sink class (FilePlayback, LoopPlayback, RemoteAudio*, NetworkAnnouncement, WebMic, WebMonitor, LinkAudio, EchoLink, StreamOutput)
- [`web_pages/routing.html`](../web_pages/routing.html) — Drawflow editor + node renderers
- [`web_server.py`](../web_server.py) — `_handle_routing_cmd` dispatcher and per-command handlers (now table-driven; one method per command)
