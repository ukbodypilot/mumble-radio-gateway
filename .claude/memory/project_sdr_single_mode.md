---
name: SDR single-tuner mode
description: RSPduo single-tuner mode with multi-channel demodulation, 57% CPU reduction
type: project
---

SDR single-tuner mode added 2026-04-09. Runs RSPduo in rspduo_mode=1 with one rtl_airband process demodulating multiple channels from a single ADC stream.

**Why:** Dual-tuner mode locked at 2 MS/s burns 31% CPU for two channels only 120 kHz apart. Single mode at 1 MHz uses 13% CPU.

**How to apply:**
- Mode selector on `/sdr` page or `sdr_set_mode` MCP tool
- Config persists in `sdr_channels.json` (mode, single.channels, single.centerfreq, single.sample_rate)
- Per-channel PipeWire sinks enable independent routing (sdr1/sdr2 as separate source nodes)
- 500 kHz sample rate has parec jitter issues — use 1 MHz minimum
- Max 2 channels (maps cleanly to sdr1/sdr2 ducking)
- Dual-tuner mode code untouched — separate config file (rspduo_single.conf)
