# FTM-150 Serial Protocol Reverse Engineering

## Goal
Reverse engineer the Yaesu FTM-150 faceplate-to-body serial protocol to build CAT-style control. No documented protocol exists. The faceplate communicates with the radio body via a ribbon cable carrying serial data for button commands and display updates.

The end result is a standalone Python library (community release) and a gateway plugin (like the TH-9800).

## Reference: TH-9800 Protocol (known working pattern)

The TH-9800 uses a similar faceplate-body serial link that was reverse engineered:
- **Baud:** 19,200 bps
- **Packet format:** `[0xAA 0xFD] [length] [payload...] [XOR checksum]`
- **TX commands (face->body):** button presses, dial turns, volume/squelch
- **RX commands (body->face):** display text, channel info, icons, signal strength
- **Startup handshake:** 4-step sequence (0x80, 0x72, 0x52, 0x41)

Reference implementation:
- `TH9800_CAT.py` — standalone async serial protocol handler + TCP server
- `TH9800_Enums.py` — command/response enum tables
- `th9800_plugin.py` — gateway plugin (thin TCP client to the CAT server)

The FTM-150 may use a completely different protocol but the architecture pattern applies.

## Tools

### Logic Analyzer
- Generic 8-channel 24MHz USB logic analyzer
- Software: **sigrok** (sigrok-cli + PulseView) on Linux
- Install: `sudo pacman -S sigrok-cli pulseview sigrok-firmware-fx2lafw`
- The analyzer is likely FX2-based — sigrok auto-detects via `fx2lafw` driver

### Verify Setup
```bash
sigrok-cli --scan
# Should show: fx2lafw - ... with 8 channels: D0 D1 D2 D3 D4 D5 D6 D7
```

## Phase 1: Hardware — Signal Identification

### Physical Probing
1. Disconnect the faceplate ribbon cable from the radio body
2. Count pins, identify connector type
3. Use multimeter (continuity + voltage) to find:
   - **VCC** (likely 5V or 3.3V — measure with radio powered on)
   - **GND** (continuity to chassis ground)
   - **Signal lines** (remaining pins — expect 2-4 data lines)
4. Sketch pinout diagram

### What to Expect
- Likely UART: 2 data lines (TX face->body, RX body->face), possibly a clock
- Could be SPI: clock + MOSI + MISO + CS
- Could be I2C: SDA + SCL
- Power lines: VCC, GND, possibly backlight/LED power

### Connect Logic Analyzer
- Attach LA channels 0-7 to each signal pin
- Connect LA GND to cable GND
- Do NOT connect LA to VCC

## Phase 2: Capture & Analysis

### Capture Commands

All captures save as CSV for Claude to parse directly.

**Initial scan — all channels, power-on:**
```bash
sigrok-cli -d fx2lafw --channels 0-7 --config samplerate=1m -O csv --time 10s -o captures/startup.csv
```

**Higher sample rate for baud detection:**
```bash
sigrok-cli -d fx2lafw --channels 0-7 --config samplerate=4m -O csv --time 2s -o captures/baud_detect.csv
```

**Protocol decode attempt (once baud is known, e.g. 9600):**
```bash
# Try UART decode on channel 0 at 9600 baud
sigrok-cli -d fx2lafw --channels 0-7 --config samplerate=1m \
  -P uart:baudrate=9600:rx=0 -A uart --time 5s -o captures/uart_ch0_9600.txt

# Try all common bauds
for baud in 9600 19200 38400 57600 115200; do
  sigrok-cli -d fx2lafw --channels 0-7 --config samplerate=4m \
    -P uart:baudrate=$baud:rx=0 -A uart --time 3s \
    -o captures/uart_ch0_${baud}.txt 2>&1 | head -20
done
```

### Systematic Button Captures
One capture per action, clearly labeled:
```bash
# Template: start capture, press button, stop
sigrok-cli -d fx2lafw --channels 0-7 --config samplerate=1m \
  -P uart:baudrate=BAUD:rx=TX_CH -A uart --time 5s \
  -o captures/button_VOL_UP.csv

# Capture list needed:
# captures/startup.csv          — power on sequence
# captures/button_VOL_UP.csv    — volume up
# captures/button_VOL_DN.csv    — volume down
# captures/button_SQL.csv       — squelch
# captures/button_PTT.csv       — PTT press/release
# captures/button_FUNC.csv      — function button
# captures/button_BAND.csv      — band select
# captures/dial_LEFT.csv        — dial left (tune down)
# captures/dial_RIGHT.csv       — dial right (tune up)
# captures/display_freq.csv     — idle, watch display updates
# captures/display_smeter.csv   — receive signal, watch S-meter
```

### Analysis Workflow (Claude does this)
1. Read raw CSV — identify which channels carry data (look for transitions)
2. Measure shortest pulse width to calculate baud rate
3. Decode UART bytes on active channels
4. Correlate button presses to byte sequences
5. Identify packet framing: start bytes, length, command, payload, checksum
6. Build command enum table
7. Verify checksum algorithm (XOR, sum, CRC)

## Phase 3: Standalone Library

### Repository Structure
```
ftm150-cat/
  ftm150_cat/
    __init__.py
    protocol.py      # Packet framing, checksum, parse/build
    commands.py       # Command enums and payload definitions
    radio.py          # State machine, high-level API (frequency, volume, etc.)
    server.py         # TCP server for remote access (like TH9800_CAT.py)
  tools/
    capture.sh        # sigrok capture helper scripts
    analyze.py        # CSV parser for LA captures
    monitor.py        # Passive serial protocol monitor
  examples/
    control.py        # Interactive command sender
  README.md
  setup.py
```

### Key Classes
- **`FTM150Protocol`** — serial port handler, packet parser/builder, checksum
- **`FTM150Radio`** — state machine: parses RX packets into radio state dict (freq, volume, squelch, S-meter, display)
- **`FTM150Server`** — TCP multiplexer: multiple clients share one serial port, text commands + binary forwarding

### Interface (matches TH-9800 pattern)
```python
class FTM150Radio:
    def connect(port, baudrate) -> bool
    def get_frequency() -> str
    def set_frequency(freq_mhz: float) -> bool
    def get_volume() -> int
    def set_volume(level: int) -> bool
    def ptt(on: bool) -> bool
    def get_status() -> dict  # {freq, volume, squelch, smeter, ...}
    def send_button(button: ButtonEnum) -> bool
    def send_raw(payload: bytes) -> bool
```

## Phase 4: Gateway Plugin Integration

### File: `ftm150_plugin.py`
Thin TCP client to FTM150Server (same pattern as th9800_plugin.py):

```python
class FTM150Plugin(RadioPlugin):
    name = "ftm150"
    capabilities = {
        "audio_rx": True,    # AIOC audio (existing endpoint)
        "audio_tx": True,
        "ptt": True,         # via serial command (if protocol supports)
        "frequency": True,   # read and set
        "ctcss": False,      # TBD — may be discoverable
        "power": False,      # TBD
        "status": True,
    }

    def setup(self, config, gateway=None): ...
    def teardown(self): ...
    def get_audio(self, chunk_size=None): ...
    def put_audio(self, pcm): ...
    def execute(self, cmd: dict): ...
    def get_status(self): ...
```

### Integration Points
- Gateway core instantiates plugin based on config
- Web UI `/radio` page (or new `/ftm150` page) for frequency control
- MCP tools: `ftm150_status`, `ftm150_frequency`, `ftm150_command`
- Audio path unchanged — still uses AIOC endpoint on Pi (192.168.2.121)
- Serial connection: either local USB or TCP to remote FTM150Server on Pi

## Reference Files in This Repo
- `th9800_plugin.py` — gateway plugin pattern (TCP client to CAT server)
- `gateway_link.py` — RadioPlugin base class, endpoint protocol
- `docs/mixer-v2-design.md` — bus/plugin architecture reference

## Reference Files (TH-9800 standalone, separate directory)
- `/home/user/Downloads/TH9800_CAT/TH9800_CAT.py` — async serial + TCP server
- `/home/user/Downloads/TH9800_CAT/TH9800_Enums.py` — command/response enums
