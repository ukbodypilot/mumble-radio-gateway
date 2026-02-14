# Mumble Radio Gateway via AIOC

A production-ready bidirectional audio gateway that connects Mumble VoIP to amateur radio transceivers using the AIOC (All-In-One-Cable) USB sound card interface, with optional Broadcastify streaming integration.

## Features

### Core Gateway
- **Bidirectional Audio**: Mumble ↔ Radio with automatic PTT control
- **Voice Activity Detection (VAD)**: Only transmits when radio signal is detected
- **Advanced Audio Processing**: Noise gate, AGC, high-pass filter, spectral noise suppression
- **File Playback System**: Trigger announcements and station ID with keyboard (keys 0-9)
- **Real-time Monitoring**: Compact status display with audio levels, dB readings, and processing indicators
- **EchoLink Integration**: Optional integration via TheLinkBox IPC (Phase 3B)

### Phase 3: Broadcastify Streaming
- **Live Internet Streaming**: Stream radio audio to Broadcastify in real-time
- **Robust Process Management**: Automated startup script handles Darkice + FFmpeg subprocesses
- **Automatic Recovery**: Auto-restarts crashed processes, no manual intervention needed
- **Realistic Audio Flow**: Announcements go to radio TX, stream hears them naturally via radio RX
- **Continuous Streaming**: Silence keepalive prevents stream disconnection
- **Production Ready**: Handles cleanup, prevents stale handles, works reliably

### Phase 3B: EchoLink Integration (TheLinkBox)
- **EchoLink Network Access**: Connect to worldwide EchoLink network
- **NAT-Friendly**: Uses TheLinkBox for proxy-based connection (no port forwarding!)
- **Bidirectional Audio**: EchoLink ↔ Mumble and EchoLink ↔ Radio
- **IPC via Named Pipes**: Clean integration with TheLinkBox
- **Flexible Routing**: Configure exactly where EchoLink audio goes

## Hardware Requirements

- **Raspberry Pi** (tested on CM5, works on Pi 3/4/5)
- **AIOC (All-In-One-Cable)** USB sound card (VID:PID 1209:7388)
- **Amateur Radio Transceiver** with audio input/output
- **Mumble Server** (self-hosted or public)
- **Internet Connection** (for Broadcastify streaming, optional)

## Software Requirements

### Core Gateway
```bash
# Python packages
pip3 install hidapi pymumble-py3 pyaudio numpy scipy soundfile --break-system-packages

# System packages
sudo apt-get install python3-pyaudio portaudio19-dev
```

### Broadcastify Streaming (Optional)
```bash
# Additional packages for streaming
sudo apt-get install darkice lame ffmpeg
```

## Quick Start

### Basic Setup (Mumble ↔ Radio Only)

1. **Install Dependencies**:
   ```bash
   pip3 install hidapi pymumble-py3 pyaudio numpy scipy soundfile --break-system-packages
   sudo apt-get install python3-pyaudio portaudio19-dev
   ```

2. **Configure Settings**:
   Edit `gateway_config.txt`:
   ```ini
   MUMBLE_SERVER = your.server.ip
   MUMBLE_USERNAME = RadioGateway
   ENABLE_STREAM_OUTPUT = false  # Streaming disabled
   ```

3. **Connect Hardware**:
   - Plug AIOC into Raspberry Pi USB port
   - Connect AIOC to radio (audio in/out and PTT)

4. **Run the Gateway**:
   ```bash
   python3 mumble_radio_gateway.py
   ```

### Advanced Setup (+ Broadcastify Streaming)

1. **Install Streaming Dependencies**:
   ```bash
   sudo apt-get install darkice lame ffmpeg
   ```

2. **Configure Darkice**:
   Edit `/etc/darkice.cfg`:
   ```ini
   [input]
   device          = hw:Loopback,1,0
   sampleRate      = 48000
   bitsPerSample   = 16
   channel         = 1

   [icecast2-0]
   bitrate         = 16              # Match your Broadcastify feed
   sampleRate      = 22050
   server          = audio9.broadcastify.com
   port            = 80
   password        = your_password
   mountPoint      = your_mount_id   # NO leading slash!
   ```

3. **Configure Gateway**:
   Edit `gateway_config.txt`:
   ```ini
   MUMBLE_SERVER = your.server.ip
   ENABLE_STREAM_OUTPUT = true
   STREAM_SERVER = audio9.broadcastify.com
   STREAM_PASSWORD = your_password
   STREAM_MOUNT = /your_mount_id
   STREAM_BITRATE = 16
   ```

4. **Run with Startup Script** (Recommended):
   ```bash
   ./start.sh
   ```

   The startup script automatically:
   - Loads ALSA loopback module
   - Starts Darkice (streams to Broadcastify)
   - Starts FFmpeg bridge (audio routing)
   - Starts Gateway (audio mixing)
   - Monitors and auto-restarts crashed processes
   - Cleans up everything on exit

5. **Or Run Manually** (3 terminals):
   ```bash
   # Terminal 1: Darkice
   darkice -c /etc/darkice.cfg

   # Terminal 2: FFmpeg Bridge
   ffmpeg -f s16le -ar 48000 -ac 1 -i /tmp/darkice_audio -f alsa hw:Loopback,0,0

   # Terminal 3: Gateway
   python3 mumble_radio_gateway.py
   ```

## Configuration Highlights

### Essential Settings

```ini
# Mumble Connection
MUMBLE_SERVER = 192.168.1.100
MUMBLE_PORT = 64738
MUMBLE_USERNAME = RadioGateway

# Audio Stability
AUDIO_CHUNK_SIZE = 9600  # 200ms buffers for smooth file playback

# Voice Activity Detection
ENABLE_VAD = true
VAD_THRESHOLD = -33  # Adjust based on your radio's noise floor

# File Playback
ENABLE_PLAYBACK = true
PLAYBACK_DIRECTORY = audio_files  # Contains 0.wav through 9.wav

# Broadcastify Streaming
ENABLE_STREAM_OUTPUT = true
STREAM_SERVER = audio9.broadcastify.com
STREAM_BITRATE = 16  # kbps
```

### Audio Processing

```ini
# Noise Gate - removes background noise
ENABLE_NOISE_GATE = true
NOISE_GATE_THRESHOLD = -40

# High-Pass Filter - removes rumble
ENABLE_HIGHPASS_FILTER = true
HIGHPASS_CUTOFF_FREQ = 300

# AGC - normalizes levels
ENABLE_AGC = true

# Noise Suppression - advanced noise reduction
ENABLE_NOISE_SUPPRESSION = true
NOISE_SUPPRESSION_METHOD = spectral
NOISE_SUPPRESSION_STRENGTH = 0.5
```

## Keyboard Controls

**While gateway is running**, press these keys for instant control:

### Muting
- `t` = Toggle TX mute (blocks Mumble → Radio)
- `r` = Toggle RX mute (blocks Radio → Mumble)
- `m` = Toggle global mute (blocks both directions)

### Audio
- `v` = Toggle VAD on/off
- `,` = Decrease volume (Radio → Mumble)
- `.` = Increase volume (Radio → Mumble)

### Processing
- `n` = Toggle Noise Gate
- `f` = Toggle High-Pass Filter
- `a` = Toggle AGC
- `s` = Toggle Spectral Noise Suppression
- `w` = Toggle Wiener Noise Suppression
- `e` = Toggle Echo Cancellation
- `x` = Toggle Stream Health Monitoring

### PTT & Playback
- `p` = Manual PTT toggle
- `1-9` = Play announcement files (1.wav through 9.wav)
- `0` = Play station ID (0.wav)
- `-` = Stop current playback

## Status Display

Real-time status line shows everything at a glance:

```
ACTIVE: ✓ M:✓ PTT:-- VAD:🔊 -28dB TX:[████------] 45% RX:[██--------] 20% Vol:1.0x [N,F,A,S]
```

**Indicators:**
- `ACTIVE/IDLE` = Audio capture status
- `M:✓/✗` = Mumble connected/disconnected
- `PTT:ON/--` = Push-to-talk active/inactive
- `VAD:🔊/--` = Voice Activity Detection active/silent
- `-XXdB` = Current audio level (use to tune VAD_THRESHOLD)
- `TX:[bar] XX%` = Mumble → Radio audio level
- `RX:[bar] XX%` = Radio → Mumble audio level
- `Vol:X.Xx` = RX volume multiplier
- `[N,F,A,S,W,E,X]` = Active processing (N=NoiseGate, F=HPF, A=AGC, etc.)

## Audio Flow Architecture

### Basic (Mumble ↔ Radio Only)
```
Radio RX → Mixer → Mumble
Mumble → Radio TX
Files (0-9) → Radio TX
```

### With Broadcastify Streaming
```
Radio RX → Mixer → Mumble
                 └→ Pipe → FFmpeg → ALSA Loopback → Darkice → Broadcastify

Mumble → Radio TX
Files (0-9) → Radio TX → Air → Radio RX → Stream

(Announcements heard on stream naturally, not direct feed)
```

### With EchoLink (TheLinkBox)
```
Radio RX ──┐
EchoLink ─┼──> Mixer ──┬──> Mumble
Files ────┘            ├──> Radio TX
                       ├──> EchoLink TX
                       └──> Stream (if enabled)
```

**EchoLink Routing Options** (configurable):
- `ECHOLINK_TO_MUMBLE` - Send EchoLink audio to Mumble users
- `ECHOLINK_TO_RADIO` - Send EchoLink audio to radio TX
- `RADIO_TO_ECHOLINK` - Send radio RX to EchoLink
- `MUMBLE_TO_ECHOLINK` - Send Mumble audio to EchoLink

## File Playback System

### Setup
1. Create directory: `mkdir audio_files`
2. Add WAV files named `0.wav` through `9.wav`
3. Files should be 48000 Hz, 16-bit, mono for best quality
4. Press keys 0-9 while gateway is running to play

### Audio File Requirements
- Format: WAV (PCM)
- Sample rate: 48000 Hz (matches gateway)
- Channels: 1 (mono)
- Bit depth: 16-bit

### Converting Files
```bash
# Convert any audio file to correct format
ffmpeg -i input.mp3 -ar 48000 -ac 1 -sample_fmt s16 output.wav
```

## Broadcastify Streaming

### How It Works

The streaming system uses 3 processes:

1. **Gateway** - Mixes audio, writes PCM to named pipe `/tmp/darkice_audio`
2. **FFmpeg** - Reads pipe, writes to ALSA loopback device `hw:Loopback,0,0`
3. **Darkice** - Reads loopback `hw:Loopback,1,0`, encodes MP3, streams to Broadcastify

The startup script (`start.sh`) manages all 3 processes automatically.

### Setup Checklist

- [ ] Broadcastify account approved
- [ ] Feed created and credentials obtained
- [ ] Darkice installed: `sudo apt-get install darkice lame`
- [ ] FFmpeg installed: `sudo apt-get install ffmpeg`
- [ ] `/etc/darkice.cfg` configured with your credentials
- [ ] `gateway_config.txt` has `ENABLE_STREAM_OUTPUT = true`
- [ ] ALSA loopback can be loaded: `sudo modprobe snd-aload`

### Troubleshooting Streaming

**Stream shows offline on Broadcastify:**
- Check Darkice is running: `ps aux | grep darkice`
- Check FFmpeg is running: `ps aux | grep ffmpeg`
- Check `/tmp/darkice.log` for errors
- Verify credentials in `/etc/darkice.cfg`

**No audio on stream:**
- Key your radio - do you hear it on Broadcastify after a few seconds?
- Check gateway is writing to pipe: `ls -l /tmp/darkice_audio` (should exist)
- Press `1` to play an announcement - hear it on stream when it comes back through radio RX

**Need reboot to restart:**
- Use the updated startup script `start.sh`
- It cleans up stale processes and pipes automatically

**Darkice won't start:**
- Check `/etc/darkice.cfg` has `device = hw:Loopback,1,0` (NOT `Loopback` alone)
- Verify bitrate matches your feed requirements (usually 16)
- Check mount point has NO leading slash in darkice.cfg
- Run: `sudo modprobe -r snd-aloop && sudo modprobe snd-aloop`

## EchoLink Integration (TheLinkBox)

### What is TheLinkBox?

TheLinkBox is a FREE EchoLink proxy client that allows you to connect to the EchoLink network without:
- Port forwarding
- Firewall configuration
- Fixed IP address
- Running your own proxy

It connects through EchoLink's free public proxy servers, making it perfect for NAT environments like home networks.

### How It Works

```
Gateway ←→ Named Pipes (IPC) ←→ TheLinkBox ←→ EchoLink Proxy ←→ EchoLink Network
```

**Audio Flow:**
1. **Radio → EchoLink**: Radio RX → Gateway → TX Pipe → TheLinkBox → EchoLink
2. **EchoLink → Radio**: EchoLink → TheLinkBox → RX Pipe → Gateway → Radio TX
3. **EchoLink → Mumble**: EchoLink → TheLinkBox → RX Pipe → Gateway → Mumble
4. **Mumble → EchoLink**: Mumble → Gateway → TX Pipe → TheLinkBox → EchoLink

### Installation

**1. Install TheLinkBox:**
```bash
sudo apt-get install thelinkbox
```

**2. Register for EchoLink:**
- Go to echolink.org
- Create account with valid amateur radio callsign
- Validate your license (required for EchoLink access)

**3. Configure TheLinkBox** (`/etc/thelinkbox/thelinkbox.conf`):
```ini
[GLOBAL]
CALLSIGN = W1XYZ-L          # Your callsign with -L or -R suffix
PASSWORD = your_echolink_password
LOCATION = Your City, ST
SYSOPNAME = Your Name
EMAIL = your@email.com

# IPC Configuration (match gateway config)
[AUDIO]
AUDIO_IN_DEVICE = /tmp/echolink_tx    # Gateway writes here
AUDIO_OUT_DEVICE = /tmp/echolink_rx   # Gateway reads here
```

**4. Configure Gateway** (`gateway_config.txt`):
```ini
# Enable EchoLink
ENABLE_ECHOLINK = true

# Pipe paths (must match TheLinkBox config)
ECHOLINK_RX_PIPE = /tmp/echolink_rx   # Read FROM TheLinkBox
ECHOLINK_TX_PIPE = /tmp/echolink_tx   # Write TO TheLinkBox

# Audio Routing
ECHOLINK_TO_MUMBLE = true    # Send EchoLink RX to Mumble
ECHOLINK_TO_RADIO = true     # Send EchoLink RX to Radio TX
MUMBLE_TO_ECHOLINK = true    # Send Mumble to EchoLink
RADIO_TO_ECHOLINK = true     # Send Radio RX to EchoLink
```

**5. Start Everything:**
```bash
# Terminal 1: Start TheLinkBox
sudo thelinkbox -c /etc/thelinkbox/thelinkbox.conf

# Terminal 2: Start Gateway (will create named pipes)
python3 mumble_radio_gateway.py
```

### Audio Routing Explained

The four routing options give complete control:

**`ECHOLINK_TO_MUMBLE = true`**
- EchoLink users heard on Mumble
- Use case: EchoLink and Mumble users talk to each other

**`ECHOLINK_TO_RADIO = true`**
- EchoLink users heard on radio
- Use case: Bring EchoLink into local radio net
- Gateway will PTT and transmit EchoLink audio

**`RADIO_TO_ECHOLINK = true`**
- Radio transmissions sent to EchoLink
- Use case: Local radio users talk to EchoLink
- Radio RX goes to EchoLink network

**`MUMBLE_TO_ECHOLINK = true`**
- Mumble users heard on EchoLink
- Use case: Mumble and EchoLink users talk to each other

**Common Configurations:**

*Full Bridge (All interconnected):*
```ini
ECHOLINK_TO_MUMBLE = true
ECHOLINK_TO_RADIO = true
RADIO_TO_ECHOLINK = true
MUMBLE_TO_ECHOLINK = true
```
Result: Radio ↔ Mumble ↔ EchoLink (everyone hears everyone)

*Radio + EchoLink Only:*
```ini
ECHOLINK_TO_MUMBLE = false
ECHOLINK_TO_RADIO = true
RADIO_TO_ECHOLINK = true
MUMBLE_TO_ECHOLINK = false
```
Result: Radio ↔ EchoLink (Mumble separate)

*Mumble + EchoLink Only:*
```ini
ECHOLINK_TO_MUMBLE = true
ECHOLINK_TO_RADIO = false
RADIO_TO_ECHOLINK = false
MUMBLE_TO_ECHOLINK = true
```
Result: Mumble ↔ EchoLink (Radio separate)

### Connecting to EchoLink Nodes

Once TheLinkBox is running and connected:

**Via TheLinkBox Console:**
```
connect 123456         # Connect to node/conference
disconnect             # Disconnect
```

**Via DTMF (if radio supports):**
- Not implemented in gateway yet
- Future enhancement

### Troubleshooting EchoLink

**TheLinkBox won't connect:**
- Verify callsign is validated at echolink.org
- Check password is correct
- Ensure internet connection working
- TheLinkBox automatically finds proxies - no manual proxy config needed

**Gateway says "EchoLink IPC setup failed":**
- Make sure TheLinkBox is running first
- Verify pipe paths match in both configs
- Check permissions on /tmp directory
- Restart both TheLinkBox and Gateway

**No audio from EchoLink:**
- Check `ECHOLINK_TO_MUMBLE` or `ECHOLINK_TO_RADIO` is enabled
- Verify you're actually connected to a node/conference
- Check TheLinkBox audio levels

**Can't send audio to EchoLink:**
- Check `RADIO_TO_ECHOLINK` or `MUMBLE_TO_ECHOLINK` is enabled
- Verify pipe is open: `ls -l /tmp/echolink_tx`
- Check gateway is sending: enable `VERBOSE_LOGGING`

**EchoLink users can't hear me:**
- Verify your EchoLink account is fully validated
- Check you have SYSOP permissions
- Some conferences require you to be a controller

### Security & Legal

**EchoLink Requirements:**
- Valid amateur radio license
- Verified callsign at echolink.org
- Follow amateur radio regulations
- Proper station identification

**TheLinkBox Security:**
- Uses EchoLink's authentication system
- Encrypted connections via EchoLink proxies
- No ports need to be opened on your firewall
- Callsign is visible to all EchoLink users

## Troubleshooting

### Audio Issues

**No audio on Mumble:**
- Check `INPUT_VOLUME` (try 1.5 or 2.0)
- Verify `VAD_THRESHOLD` isn't too high (try -38)
- Disable VAD temporarily: `ENABLE_VAD = false`
- Check Mumble server is accessible

**No audio on radio:**
- Check `OUTPUT_VOLUME` (try 1.5 or 2.0)
- Verify AIOC connection to radio
- Test PTT: press `p` key, LED should light

**Choppy audio:**
- Increase `AUDIO_CHUNK_SIZE` to 9600
- Check USB power supply
- Try different USB port
- Disable processing temporarily

**High latency:**
- Decrease `AUDIO_CHUNK_SIZE` (but may cause dropouts)
- Reduce `PTT_RELEASE_DELAY`

### Connection Issues

**Won't connect to Mumble:**
- Verify `MUMBLE_SERVER` and `MUMBLE_PORT`
- Check network connectivity: `ping your.mumble.server`
- Try `MUMBLE_RECONNECT = true`

**Disconnects randomly:**
- Check network stability
- Enable `MUMBLE_RECONNECT = true`
- Check Mumble server logs

### PTT Issues

**PTT won't activate:**
- Try different `AIOC_PTT_CHANNEL` (1, 2, or 3)
- Check AIOC LED - should light when PTT active
- Test with manual PTT: press `p` key

**PTT activates too soon:**
- Increase `PTT_ACTIVATION_DELAY` (try 0.1)

**PTT releases too quickly:**
- Increase `PTT_RELEASE_DELAY` (try 1.0)

### File Playback Issues

**Files won't play:**
- Check `PLAYBACK_DIRECTORY` path exists
- Verify files named exactly `0.wav` through `9.wav`
- Check file format: `file audio_files/1.wav` (should say "WAVE audio")

**Poor audio quality:**
- Re-encode files to 48000 Hz: `ffmpeg -i input.mp3 -ar 48000 -ac 1 output.wav`
- Check files aren't corrupted

## Project Structure

```
mumble-radio-gateway/
├── mumble_radio_gateway.py      # Main gateway application (Phase 3)
├── start.sh                      # Startup script (manages all processes)
├── gateway_config.txt            # Configuration file (70+ parameters)
├── darkice.cfg                   # Darkice configuration for Broadcastify
├── README.md                     # This file
├── audio_files/                  # Announcement files (0.wav - 9.wav)
│   ├── 0.wav                     # Station ID
│   ├── 1.wav                     # Announcement 1
│   └── ...
├── docs/
│   ├── ERROR_HANDLING_GUIDE.md  # Complete error handling documentation
│   ├── INTEGRATED_README.md     # Unified version notes (deprecated)
│   └── GIT_COMMIT_NOTES.md      # Development history
└── logs/
    ├── /tmp/darkice.log         # Darkice output (if using start.sh)
    └── /tmp/ffmpeg.log          # FFmpeg output (if using start.sh)
```

## Performance

- **Latency**: ~50-100ms (Radio → Mumble → Radio round trip ~100-200ms)
- **CPU Usage**: <15% on Raspberry Pi CM5 (with streaming)
- **Bandwidth**: 
  - Mumble: ~15-30 kbps with VAD
  - Broadcastify: 16-32 kbps (configurable)
- **Reliability**: Runs continuously for weeks with automatic recovery

## Known Issues & Limitations

**USB/ALSA Stability:**
- CM108 chipset has occasional ALSA issues
- Startup script handles cleanup automatically
- First FFmpeg start may fail (auto-restarts, this is normal)

**Streaming:**
- Requires reboot if ALSA loopback gets stuck (rare with new cleanup)
- Darkice can be particular about configuration syntax

**Audio Processing:**
- Heavy processing may increase CPU usage
- Echo cancellation is experimental

## Roadmap / Future Enhancements

- [ ] Web-based configuration interface
- [ ] Multiple radio support
- [ ] DTMF control
- [ ] Logging and statistics dashboard
- [ ] Docker container deployment
- [ ] Automated installation script
- [ ] Support for other USB sound cards
- [ ] Remote control via Mumble text commands

## Contributing

Contributions welcome! Areas for improvement:
- Testing on different hardware platforms
- Documentation improvements
- Feature enhancements
- Bug fixes

## Version History

- **v3.0.0** (2025-02-14) - Phase 3 Complete
  - ✅ Broadcastify streaming integration (Darkice + FFmpeg)
  - ✅ EchoLink integration via TheLinkBox (NAT-friendly)
  - ✅ Robust startup script with process management
  - ✅ File playback system (announcements 0-9)
  - ✅ Flexible audio routing (Radio/Mumble/EchoLink/Stream)
  - ✅ Audio routing corrections (announcements TX only)
  - ✅ Improved cleanup and error handling
  - ✅ Production-ready reliability

- **v2.0.0** (2025-02-10) - Phase 2
  - ✅ File playback system
  - ✅ Advanced audio processing (AGC, spectral suppression)
  - ✅ Keyboard controls
  - ✅ Enhanced status display

- **v1.0.0** (2025-02-07) - Phase 1
  - ✅ Initial release
  - ✅ Voice Activity Detection
  - ✅ Stream health management
  - ✅ Basic audio processing

## License

MIT License - See LICENSE file for details

## Credits

Developed for amateur radio operators who want to bridge Mumble VoIP with radio networks and stream to Broadcastify.

**Special thanks to:**
- AIOC project for the excellent USB interface
- pymumble developers
- Darkice project
- The amateur radio community

## Support

For issues, questions, or contributions:
- **GitHub Issues**: [Your repo URL]
- **Documentation**: See `docs/` directory
- **Troubleshooting**: See configuration file troubleshooting section

## Disclaimer

This software is provided for amateur radio use. Ensure compliance with your local regulations regarding:
- Automatic control of radio transmitters
- Internet linking of radio systems
- Streaming of radio communications
- Station identification requirements

The authors assume no liability for improper use or regulatory violations.

---

**73!** 📻🌐
