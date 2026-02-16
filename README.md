# Mumble Radio Gateway

A bidirectional audio bridge connecting Mumble VoIP to amateur radio with multi-source audio mixing, real-time processing, and extensive features.

![Architecture](gateway_flowchart.jpg)

## Features

### Core Functionality
- **Bidirectional Audio Bridge**: Seamless communication between Mumble VoIP and radio
- **Multi-Source Audio Mixing**: Simultaneous mixing of 5 audio sources with priority control
- **Auto-PTT Control**: Automatic push-to-talk with configurable delays and tail
- **Voice Activity Detection (VAD)**: Smart audio gate prevents noise transmission
- **Real-Time Audio Processing**: Noise gate, AGC, filters, echo cancellation
- **Live Status Display**: Real-time bars showing TX/RX/SDR levels with color coding

### Audio Sources (Priority-Based Mixing)
1. **File Playback** (Priority 0, PTT enabled)
   - 10 announcement slots (keys 1-9 + Station ID on 0)
   - WAV, MP3, FLAC support with automatic resampling
   - Volume normalization and per-file controls
   
2. **Radio RX** (Priority 1)
   - AIOC USB audio interface
   - GPIO PTT control
   - Automatic audio processing pipeline

3. **SDR Receiver** (Priority 2)
   - ALSA loopback device input
   - Second simultaneous receiver
   - Independent volume and mute controls
   - Real-time level display (cyan bar)

4. **Mumble RX** (Priority 1)
   - VoIP audio from Mumble server
   - Opus codec, low latency
   - Routes to radio TX

5. **EchoLink** (Priority 3)
   - Named pipe integration
   - TheLinkBox compatible

### Text-to-Speech
- Google TTS (gTTS) integration
- Mumble text command: `!speak <text>`
- Automatic MP3 generation
- Format validation (detects API errors)
- Rate limiting detection

### Audio Processing
- **VAD**: Voice Activity Detection with configurable threshold
- **Noise Gate**: Removes background noise
- **AGC**: Automatic Gain Control for consistent levels
- **HPF**: High-pass filter (removes low-frequency rumble)
- **Wiener Filter**: Spectral noise suppression
- **Echo Cancellation**: Reduces feedback

### Streaming
- **Darkice Integration**: Stream to Icecast server
- **Broadcastify Support**: Live scanner feed
- Mixed audio output via named pipe

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
AUDIO_INPUT_DEVICE = AIOC
AUDIO_OUTPUT_DEVICE = AIOC
PTT_GPIO_PIN = 17

# Enable features
ENABLE_VAD = true
ENABLE_TTS = true
ENABLE_SDR = true
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

ALSA loopback devices work in **pairs**:
- `hw:X,0` (playback) ↔ `hw:X,1` (capture)

**Example:**
- SDRconnect outputs to `hw:2,0`
- Gateway reads from `hw:2,1`

### Configuration

```ini
ENABLE_SDR = true
SDR_DEVICE_NAME = hw:2,1
SDR_DISPLAY_GAIN = 1.0
SDR_AUDIO_BOOST = 1.0
SDR_BUFFER_MULTIPLIER = 8
```

See `SETUP_SDR_LOOPBACK.txt` for detailed setup guide.

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
- `s` = SDR Mute (independent)
- `m` = Global Mute (all audio)

### Audio Controls
- `v` = Toggle VAD
- `,` = Volume Down
- `.` = Volume Up
- `p` = Manual PTT (hold)

### Processing
- `n` = Noise Gate
- `f` = High-Pass Filter
- `a` = AGC
- `w` = Wiener Filter
- `e` = Echo Cancellation
- `x` = Stream Health

### File Playback
- `1-9` = Play announcements
- `0` = Station ID
- `-` = Stop playback

## Status Bar

```
ACTIVE: ✓ M:✓ PTT:-- VAD:✗ -48dB TX:[█--] 12% RX:[███] 36% SDR:[██] 25% Vol:1.0x 1234567890 [N,F,A]
```

- **ACTIVE**: Audio transmit status
- **M**: Mumble connection (✓ = connected)
- **PTT**: PTT state (== = active, -- = inactive)
- **VAD**: Voice Activity Detection (🔊 = active, ✗ = disabled)
- **TX Bar**: Mumble → Radio (red)
- **RX Bar**: Radio → Mumble (green)
- **SDR Bar**: SDR audio level (cyan)
- **Vol**: Input volume multiplier
- **Numbers**: File status (green = loaded, red = playing, white = empty)
- **Flags**: Active processing (N=noise gate, F=filter, A=AGC, etc.)

## Audio Level Calculation

All bars (TX, RX, SDR) use the same method:

```
RMS = sqrt(sum(samples²) / count)
dB = 20 * log10(RMS / 32767)
Level = (dB + 60) * (100/60)  # Map -60dB to 0dB → 0-100%
```

**Smoothing:**
- Fast attack: Immediate on increase
- Slow decay: 70% old + 30% new

## Architecture

### Audio Flow

```
INPUTS:
  ├─ Radio RX (AIOC) ────┐
  ├─ SDR (ALSA Loop) ────┤
  ├─ Files (10 slots) ───┤
  ├─ Mumble RX ──────────┼─→ MIXER → PROCESSING → OUTPUTS
  └─ EchoLink ───────────┘                          ├─ Radio TX (AIOC + PTT)
                                                     ├─ Mumble TX
                                                     ├─ Darkice (Icecast)
                                                     └─ Broadcastify
```

### Priority System

- **Priority 0**: File playback (highest, triggers PTT)
- **Priority 1**: Radio RX, Mumble RX
- **Priority 2**: SDR
- **Priority 3**: EchoLink (lowest)

## Configuration Reference

### Audio Settings
```ini
AUDIO_RATE = 48000
AUDIO_CHANNELS = 1
AUDIO_CHUNK_SIZE = 9600
INPUT_VOLUME = 1.0
OUTPUT_VOLUME = 1.0
```

### VAD Settings
```ini
ENABLE_VAD = true
VAD_THRESHOLD = -50.0
VAD_HANGTIME = 0.5
VAD_ACTIVATION_TIME = 0.1
```

### PTT Settings
```ini
PTT_GPIO_PIN = 17
PTT_ACTIVATION_DELAY = 0.1
PTT_RELEASE_DELAY = 0.5
```

### SDR Settings
```ini
ENABLE_SDR = true
SDR_DEVICE_NAME = hw:2,1
SDR_DISPLAY_GAIN = 1.0
SDR_AUDIO_BOOST = 1.0
SDR_MIX_RATIO = 1.0
SDR_BUFFER_MULTIPLIER = 8
```

### TTS Settings
```ini
ENABLE_TTS = true
TTS_VOLUME = 1.0
PTT_TTS_DELAY = 0.25
```

### File Playback
```ini
ANNOUNCEMENT_DIRECTORY = /home/pi/announcements
ANNOUNCEMENT_INTERVAL = 3600
PLAYBACK_VOLUME = 1.0
```

## Troubleshooting

### SDR Audio Issues

**No audio from SDR:**
```bash
# Check if snd-aloop is loaded
lsmod | grep snd_aloop

# Load module
sudo modprobe snd-aloop
```

**Stuttering SDR audio:**
- Increase `SDR_BUFFER_MULTIPLIER` (try 16)
- Check CPU usage (`htop`)

**SDR bar not updating:**
- Verify SDR device name: `arecord -l`
- Check SDRconnect output device matches
- Test with `test_sdr_loopback.py`

### TTS Issues

**"gTTS returned HTML error page":**
- Rate limited by Google
- Try again in 1-2 minutes
- Check internet connection

**TTS audio distorted:**
- Reduce `TTS_VOLUME` (try 0.5)
- Check network quality

### Audio Quality

**Choppy audio:**
- Increase `AUDIO_CHUNK_SIZE` (try 19200)
- Enable `ENABLE_STREAM_HEALTH`

**Background noise:**
- Enable `ENABLE_NOISE_GATE`
- Adjust `VAD_THRESHOLD`

**Low volume:**
- Increase `INPUT_VOLUME` or `OUTPUT_VOLUME`
- Check `SDR_AUDIO_BOOST` for SDR

## Advanced Features

### Darkice Streaming

Stream mixed audio to Icecast/Broadcastify:

```ini
ENABLE_STREAM_OUTPUT = true
STREAM_OUTPUT_PIPE = /tmp/gateway_stream.pcm
```

Configure Darkice to read from the pipe.

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
ANNOUNCEMENT_INTERVAL = 3600  # Every hour
# Put station_id.wav in announcements folder
```

## Development

### Project Structure
```
mumble-radio-gateway/
├── mumble_radio_gateway.py    # Main application
├── gateway_config.txt          # Configuration file
├── SETUP_SDR_LOOPBACK.txt      # SDR setup guide
├── test_sdr_loopback.py        # SDR test tool
├── gateway_flowchart.jpg       # Architecture diagram
└── README.md                   # This file
```

### Adding Audio Sources

Extend the `AudioSource` class:

```python
class MySource(AudioSource):
    def __init__(self, name, config):
        super().__init__(name, config)
        self.priority = 2  # Set priority
        self.ptt_control = False  # PTT trigger?
    
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

## License

[Your License Here]

## Credits

- pymumble: Mumble Python library
- gTTS: Google Text-to-Speech
- PyAudio: Python audio interface

## Support

For issues, questions, or contributions:
- GitHub Issues: [Your Repo URL]
- Documentation: See `*.txt` files in repo

## Changelog

### Phase 5 - SDR Integration
- Added SDR receiver input via ALSA loopback
- Multi-source audio mixer with priority system
- Independent mute controls per source
- RMS + dB level calculation for all sources
- TTS format validation and error detection
- Clean shutdown without ALSA errors
- Comprehensive documentation and diagrams

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
