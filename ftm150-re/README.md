# FTM-150 Control Head Bus Reverse Engineering

## Project Summary

Attempted to reverse engineer the Yaesu FTM-150 control head ↔ radio body communication bus
for CAT (Computer Aided Transceiver) control. The goal was to inject commands from a computer
to control the radio programmatically.

**Outcome:** The bus uses a proprietary modulated protocol that makes simple CAT injection
impractical. The project was shelved, but the tooling and findings are documented here for
future reference.

## Hardware Setup

- **Radio:** Yaesu FTM-150R VHF/UHF FM transceiver
- **Control head cable:** Multi-wire cable between head unit and radio body
- **Logic analyzer:** Saleae Logic clone (CY7C68013A/FX2LP), 8ch, 24MHz max
- **Oscilloscope:** OWON VDS1022 USB scope (via owon-vds-tiny)
- **Probes:** Two data lines connected to LA channels D0 and D1

## Bus Findings

### Physical Layer
- **Two active data lines** — no separate clock wire, no other data-carrying wires found
- **~50kHz carrier signal** on both lines (~5V peak, 2μs high / 18μs low pulses)
- **Data encoded in the carrier envelope** — bursts of ~1ms with gaps of ~4ms (5ms frame period, ~200 frames/sec)
- Pulse width varies (0.6ms, 0.7ms, 0.8ms, 0.9ms, 1.0ms) suggesting pulse-width modulation

### Protocol Analysis
- Not standard I2C, SPI, or UART — initially misidentified as I2C, then SPI, then UART
- The 50kHz carrier makes logic analyzer decoding unreliable — the LA sees carrier cycles as data transitions
- UART decode at 500kbaud produced structured-looking frames but these are artifacts of carrier demodulation:
  - D0: `FF 00 08 00 00...` and `F1 00 00 02 04 04 40 0A 09 09 0C 0C 02 02...`
  - D1: `80 00 00 00 00 00 00 00 00 00 00 00 00 7C 7B 20 00 00 00 0F...`
- Could not isolate knob/button events from the data stream
- Bus is constantly active even at idle — continuous carrier with data envelope

### Data Rate Estimate
- ~200 frames/sec × estimated 20-40 bits/frame = 4000-8000 bits/sec
- Insufficient for digital audio — this is a control/display bus only
- Audio is handled separately (likely analog or on a different bus)

### What Was Tried
1. I2C decode (sigrok) — produced garbage, wrong protocol
2. SPI decode (sigrok) — produced structured data but turned out to be display bitmap
3. UART decode at 50kbaud — partially worked but noisy
4. UART decode at 500kbaud — structured frames but couldn't correlate with knob events
5. Raw carrier demodulation — confirmed PWM-like envelope encoding
6. Oscilloscope verification — confirmed ~50kHz carrier with envelope modulation

## Installed Tools

### Oscilloscope Software
- **owon-vds-tiny 1.1.5-cf19** — `/usr/bin/owon-vds-tiny`
- Desktop entry patched with `-Dsun.java2d.opengl=true` for lower CPU usage
- Renice to priority 10 recommended (still uses significant CPU)
- USB serial driver blacklisted: `/etc/modprobe.d/owon-blacklist.conf` (blacklists `usb_serial_simple`)
- udev rule: `/etc/udev/rules.d/70-owon-vds-tiny.rules`

### Logic Analyzer Software
- **sigrok-cli** — command-line capture and decode
- **PulseView** — GUI waveform viewer
- **sigrok-firmware-fx2lafw** — firmware for Saleae clone LA
- LA device: `0925:3881` Saleae Logic, 8 channels (D0-D7)

### Capture Scripts
- `cap.sh` — interactive capture with audio beeps (high=start, low=done)
- `capture.sh` — basic capture helper
- `decode.py` — I2C transaction parser and diff tool (less useful now we know it's not I2C)

## Controls Reference

### Rotary Knobs (rotate + push)
- UL: Upper Left
- LL: Lower Left
- UR: Upper Right
- LR: Lower Right

### Buttons
- SDX, BAND, PMG, VM, F, PWR

## Capture Files

Captures stored in `captures/` directory as sigrok `.sr` files.
Key captures:
- `idle*.sr` — baseline with no input
- `ul_cw*.sr`, `ul_rotate_cw*.sr` — upper left knob clockwise rotation attempts
- `ll_rotate_cw*.sr` — lower left knob clockwise rotation attempts
- Various sample rates tried: 1MHz, 4MHz, 8MHz, 24MHz

## Conclusions

1. The FTM-150 uses a proprietary modulated serial protocol between head and body
2. No public documentation exists for this protocol
3. Simple USB-to-serial CAT control is not feasible without full protocol reverse engineering
4. A hardware demodulator (envelope detector/low-pass filter) would be needed before the LA can properly capture the data
5. **Recommendation:** For CAT control, use a radio with a standard serial CAT port (e.g., FTM-300/400/500, Icom CI-V, Kenwood)
