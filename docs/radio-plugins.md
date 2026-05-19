# Radio Plugins

Every radio the gateway controls is a plugin — same interface, same lifecycle, same place in the routing graph as any other source/sink. The set today is TH-9800, TH-D75, KV4P, RSPduo SDR (built-in plugins) plus FTM-150 and IC-7100 (link endpoints — same plugin model, different transport).

For the plugin contract and building a new one, see [plugin-development.md](plugin-development.md).

## TH-9800

Yaesu FTM-9800 dual-band quad-receiver. Connected via **AIOC** (All-In-One-Cable) over USB for audio + PTT, and **CAT** over serial (currently bridged via TCP from a separate Celeron host running USB/IP).

**Capabilities**
- Full CAT control: dual VFO, all memories, every front-panel button replicated in the web UI
- HID PTT via AIOC
- Browser-mic PTT (push the on-screen mic button or hold spacebar on `/radio`)
- Soft-clip on TX path (no hard clipping under audio boost)
- USB reset recovery — if AIOC vanishes from `/dev/snd`, plugin attempts USB rebind before giving up

**Web UI**: `/radio` — full front-panel emulation plus signal meters and per-VFO frequency display.

**Source**: [`th9800_plugin.py`](../th9800_plugin.py) · **Config**: `[radio]`, `[ptt]`, `[cat]` in `gateway_config.txt`.

## TH-D75

Kenwood TH-D75 handheld with built-in BT serial + audio. Connects over Bluetooth using RFCOMM channel 2 for CAT, RFCOMM channel 1 + SCO for audio.

**Capabilities**
- Band A / B status (frequency, mode, S-meter, tone/CTCSS/DCS, shift, offset)
- D-STAR mode awareness
- Memory load with tone/mode/shift/offset
- Live battery percentage
- TNC mode (APRS / DV-DATA / OFF)

**Bridge architecture** — the D75 plugin doesn't run on the gateway directly. It runs on a small headless Pi (typically a Pi Zero 2 W, called `d75-pi` in our fleet) co-located with the radio, and connects to the gateway over the [Link endpoint protocol](gateway_link.md). The Pi handles BT pairing + SCO audio + RFCOMM CAT and tunnels everything over a single TCP connection.

**Watchdog** — RFCOMM ch2 (CAT) can die while SCO (audio) keeps going. The plugin's watchdog tries a serial-only reconnect first (up to 3 attempts) before escalating to a full BT teardown + reconnect. Wrapped in try/except so an unexpected exception doesn't kill the watchdog thread silently. 60s heartbeat log so you can verify the watchdog is alive.

**Web UI**: `/d75` — band A/B panels, memory loader, S-meter, battery, TNC controls.

**Source**: [`tools/d75_link_plugin.py`](../tools/d75_link_plugin.py) on the Pi · [`audio_sources.py`](../audio_sources.py) `LinkAudioSource` on the gateway.

## KV4P

KV4P-HT — open-source SA818/DRA818-based 2 m / 70 cm handheld interface board. CP2102 USB serial bridge to an Opus-capable audio path.

**Capabilities**
- Squelch (0-8), CTCSS tone (38 tones) on RX and TX
- High / low power (5 W / 1 W)
- Narrow / wide deviation
- S-meter (raw 0-255 normalised to S0-S9)
- Audio level meter
- Configurable volume boost (0-500 %)
- Channel scan + memory store

**Web UI**: `/kv4p` — frequency display, all controls in a single panel, signal + audio meters.

**Source**: [`kv4p_plugin.py`](../kv4p_plugin.py) · **Config**: `[kv4p]` in `gateway_config.txt`.

## RSPduo SDR

SDRplay RSPduo via the SDRplay API + `rtl_airband` for AM/NBFM/WBFM demod and audio output. Two runtime modes selectable at the `/sdr` page:

| Mode | Behaviour | CPU |
|------|-----------|-----|
| **Single tuner, multi-channel** | One tuner up to 10.66 MHz wide bandwidth; up to 2 demod channels within that range. CTCSS gating per channel. | -57 % vs dual |
| **Dual tuner, master/slave** | Two independent tuners (e.g. one VHF, one UHF). Independent center freq, gain, AGC. | higher |

**Channel processing** — each demodulated channel has its own AudioProcessor (gate / HPF / LPF / notch / boost) inside the SDR plugin before it hits the routing graph. Add or remove channels from the UI; the rtl_airband config is rewritten and the process restarted automatically.

**Web UI**: `/sdr` — mode selector, per-tuner controls, per-channel signal meters and config. Frequency entry accepts plain decimal MHz (`146.520`).

**Source**: [`sdr_plugin.py`](../sdr_plugin.py) · **Config**: `[sdr]` in `gateway_config.txt`.

## Link endpoints (FTM-150, IC-7100, future)

Two radios in our fleet talk to the gateway over the [Gateway Link protocol](gateway_link.md):

- **FTM-150** — runs on the `mx` machine (Intel Celeron). AIOC over USB locally; the link endpoint tunnels audio + PTT + status over TCP.
- **IC-7100** — runs on a CM5 (when deployed). CI-V CAT + USB Audio Class 2 audio via a single USB connection; link endpoint plugin (`tools/ic7100_link_plugin.py`) speaks the gateway link protocol.

A new radio of any kind can become a link endpoint by writing a plugin against the link protocol's interface — no gateway code changes. See [gateway_link.md](gateway_link.md) for the spec and [plugin-development.md](plugin-development.md) for the plugin contract.

## Common plugin contract

Every plugin (built-in or link endpoint) exposes:

- `setup(config, gateway=None) → bool` — wire up hardware, start I/O threads, return success
- `get_status() → dict` — JSON-serialisable status snapshot (frequency, mode, S-meter, etc.)
- `get_audio() → (bytes, bool)` — pull one chunk of RX audio (PCM 48 kHz mono, plus an `is_squelch_open` flag)
- `put_audio(chunk)` — push one chunk of TX audio
- `execute(cmd) → dict` — handle a command (`ptt`, `frequency`, `ctcss`, `mode`, ...)
- `teardown()` — clean shutdown

The gateway's routing engine treats every plugin as both a source and a sink (some are RX-only; they just don't expose a sink). See [plugin-development.md](plugin-development.md) for the full interface.

## Source pointers

- Built-in plugins live in the project root: `th9800_plugin.py`, `kv4p_plugin.py`, `sdr_plugin.py`
- Link endpoint plugins live in `tools/` and are deployed to the endpoint host
- Plugin auto-discovery from `plugins/` directory — see [plugin-development.md](plugin-development.md)
