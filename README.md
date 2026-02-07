# Mumble Radio Gateway via AIOC

A bidirectional audio gateway that connects Mumble VoIP to amateur radio transceivers using the AIOC (All-In-One-Cable) USB sound card interface.

## Features

- **Bidirectional Audio**: Mumble ↔ Radio with automatic PTT control
- **Voice Activity Detection (VAD)**: Only transmits when radio signal is detected, eliminating buffer buildup
- **Advanced Audio Processing**: Noise gate, high-pass filter, and spectral noise suppression
- **Automatic Stream Health Management**: Proactive restarts prevent USB/ALSA driver issues
- **Real-time Monitoring**: Compact status display with audio levels and dB readings
- **Highly Configurable**: 50+ parameters for audio quality, PTT timing, and processing

## Hardware Requirements

- **Raspberry Pi** (tested on CM5, should work on Pi 3/4/5)
- **AIOC (All-In-One-Cable)** USB sound card (VID:PID 1209:7388)
- **Amateur Radio Transceiver** with audio input/output
- **Mumble Server** (self-hosted or public)

## Software Requirements

```bash
# Python 3.x with packages:
pip3 install hidapi pymumble-py3 pyaudio numpy --break-system-packages

# System packages:
sudo apt-get install python3-pyaudio portaudio19-dev
```

## Quick Start

1. **Install Dependencies**:
   ```bash
   pip3 install hidapi pymumble-py3 pyaudio numpy --break-system-packages
   ```

2. **Configure Settings**:
   Edit `gateway_config.txt` and set your Mumble server details:
   ```ini
   MUMBLE_SERVER = your.server.ip
   MUMBLE_USERNAME = RadioGateway
   ```

3. **Connect Hardware**:
   - Plug AIOC into Raspberry Pi USB port
   - Connect AIOC to radio (audio in/out and PTT)

4. **Run the Gateway**:
   ```bash
   python3 mumble_radio_gateway.py
   ```

5. **Verify Operation**:
   You should see:
   ```
   ✓ AIOC: All-In-One-Cable
   ✓ Audio configured
   ✓ Connected as 'RadioGateway'
   Gateway Active!
   ```

## Configuration Highlights

### Essential Settings

```ini
# Mumble Connection
MUMBLE_SERVER = 192.168.1.100
MUMBLE_PORT = 64738
MUMBLE_USERNAME = RadioGateway

# Audio Stability (critical for Raspberry Pi)
AUDIO_CHUNK_SIZE = 2400  # 50ms buffers for USB stability

# Voice Activity Detection (prevents buffer issues)
ENABLE_VAD = true
VAD_THRESHOLD = -33  # Tune based on your radio's noise floor

# Stream Health (prevents -9999 errors)
STREAM_RESTART_INTERVAL = 60  # Restart every 60s when idle
```

### Audio Processing

```ini
# Clean up noisy radio audio before sending to Mumble
ENABLE_NOISE_GATE = true
NOISE_GATE_THRESHOLD = -32

ENABLE_HIGHPASS_FILTER = true
HIGHPASS_CUTOFF_FREQ = 120

ENABLE_NOISE_SUPPRESSION = true
NOISE_SUPPRESSION_METHOD = spectral
NOISE_SUPPRESSION_STRENGTH = 0.6
```

## Status Display

The gateway shows a compact, real-time status line:

```
[✓ ACTIVE] M:✓ PTT:-- VAD:🔊 -28dB TX:[████------] 45% RX:[██--------] 20%
```

- `[✓/⚠/✗]` = Audio capture status (ACTIVE/IDLE/STOPPED)
- `M:✓/✗` = Mumble connected/disconnected
- `PTT:ON/--` = Push-to-talk active/inactive
- `VAD:🔊/--` = Voice detection active/silent
- `-XXdB` = Current audio level (tune VAD_THRESHOLD based on this)
- `TX:[bar]` = Mumble → Radio audio level
- `RX:[bar]` = Radio → Mumble audio level

## Troubleshooting

### Audio Dropouts / -9999 Errors
- Increase `AUDIO_CHUNK_SIZE` to 4800
- Decrease `STREAM_RESTART_INTERVAL` to 30
- Try a different USB port
- Check USB power supply

### Delay Building Up
- Ensure `ENABLE_VAD = true`
- Reduce `MAX_MUMBLE_BUFFER_SECONDS`
- Check `VAD_THRESHOLD` is appropriate

### Missing Weak Signals
- Lower `VAD_THRESHOLD` (try -38)
- Increase `INPUT_VOLUME`
- Disable or reduce `NOISE_GATE_THRESHOLD`

### Too Much Noise to Mumble
- Raise `VAD_THRESHOLD` (try -30)
- Enable noise gate and increase threshold
- Enable high-pass filter

See `gateway_config.txt` for comprehensive troubleshooting guide.

## Project Structure

```
mumble-radio-gateway/
├── mumble_radio_gateway.py  # Main gateway application
├── gateway_config.txt        # Configuration file (50+ parameters)
├── README.md                 # This file
├── LICENSE                   # MIT License
├── requirements.txt          # Python dependencies
├── docs/
│   ├── VAD_FEATURE.md       # Voice Activity Detection details
│   ├── CONFIGURATION.md     # Complete configuration guide
│   └── HARDWARE_SETUP.md    # Hardware connection guide
├── examples/
│   └── gateway_config.example.txt  # Example configuration
└── scripts/
    ├── aioc_ptt_test.py     # Test AIOC PTT functionality
    └── install.sh           # Automated installation script
```

## How It Works

### Signal Flow

**Mumble → Radio (TX)**:
1. Receive audio from Mumble server
2. Activate PTT via AIOC GPIO
3. Play audio to radio via AIOC audio output
4. Release PTT after configurable delay

**Radio → Mumble (RX)**:
1. Capture audio from radio via AIOC audio input
2. Apply audio processing (noise gate, HPF, noise suppression)
3. VAD detects if signal is present
4. If signal detected, send to Mumble server
5. If silent, skip to save bandwidth and prevent buffer buildup

### Key Technologies

- **PyAudio**: Cross-platform audio I/O
- **pymumble**: Python Mumble protocol implementation
- **HID API**: Direct control of AIOC PTT via USB HID
- **Voice Activity Detection**: Prevents sending silence to Mumble
- **Proactive Stream Management**: Restarts audio streams before failures occur

## Performance

- **Latency**: ~50ms typical (Radio → Mumble → Radio round trip ~100ms)
- **CPU Usage**: <10% on Raspberry Pi CM5
- **Bandwidth**: ~15 kbps average with VAD, ~150 kbps continuous without VAD
- **Reliability**: Runs continuously for days with proactive stream restarts

## Known Issues

- **USB/ALSA -9999 errors**: The CM108 chipset (used in AIOC) has known ALSA driver issues. Enable `STREAM_RESTART_INTERVAL` to work around this.
- **Initial PTT delay**: Some radios require `PTT_ACTIVATION_DELAY` of 50-100ms for proper keying.

## Contributing

Contributions welcome! Areas for improvement:
- Support for other USB sound cards
- Web-based configuration interface
- Multiple radio support
- DTMF control
- Logging and statistics

## License

MIT License - See LICENSE file for details

## Credits

Developed for amateur radio operators who want to bridge Mumble VoIP with their radio networks.

Special thanks to:
- AIOC project for the excellent USB interface
- pymumble developers
- The amateur radio community

## Support

For issues, questions, or contributions:
- GitHub Issues: [Your repo URL]
- Email: [Your email]
- Amateur Radio: [Your callsign]

## Version History

- **v1.0.0** (2025-02-07)
  - Initial release
  - VAD implementation
  - Stream health management
  - Comprehensive audio processing
  - 50+ configuration parameters
