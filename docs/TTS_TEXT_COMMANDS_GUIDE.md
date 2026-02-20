# Text Commands & TTS - Quick Start Guide

## Installation

```bash
pip3 install gtts --break-system-packages
```

## Configuration

Edit `gateway_config.txt`:

```ini
ENABLE_TTS = true
ENABLE_TEXT_COMMANDS = true
```

## How It Works

1. **User sends text command in Mumble chat**
2. **Gateway receives command**
3. **Gateway processes command**:
   - `!speak` → Generates TTS audio → Keys PTT → Broadcasts on radio
   - `!play` → Plays file → Keys PTT → Broadcasts on radio
   - `!status` → Sends text reply back to Mumble
4. **Radio listeners hear the TTS or announcement**

## Commands

### !speak \<text\>
Generate text-to-speech and broadcast on radio

**Examples:**
```
!speak Emergency traffic - all stations stand by
!speak Net will start in 5 minutes
!speak This is an automated weather update
```

**What happens:**
1. Gateway generates MP3 using Google TTS
2. Queues audio for playback
3. Keys PTT and transmits on radio
4. Sends confirmation to Mumble: "Speaking: Emergency traffic..."

### !play \<0-9\>
Play announcement file on radio by slot number

**Examples:**
```
!play 0     # Play station ID
!play 1     # Play announcement 1
!play 5     # Play announcement 5
```

**What happens:**
1. Gateway finds file assigned to that slot
2. Queues audio for playback
3. Keys PTT and transmits on radio
4. Sends confirmation to Mumble: "Playing: 1_welcome.mp3"

### !files
List all loaded announcement files with their slot numbers

**Example:**
```
!files
```

**Response:**
```
=== Announcement Files ===
  Station ID: station_id.mp3
  Slot 1: 1_welcome.mp3
  Slot 2: 2_net_open.mp3  [PLAYING]
  Slot 5: 5_emergency.wav
```

Use this when you can't remember what file is in each slot.

### !stop
Stop current playback immediately and clear the queue

**Example:**
```
!stop
```

**Response:**
```
Playback stopped
```

Use this if the wrong file is playing or you need to abort queued audio.

### !mute
Mute TX — stops Mumble audio from reaching the radio

**Example:**
```
!mute
```

**Response:**
```
TX muted (Mumble → Radio)
```

Use `!unmute` to restore. Equivalent to pressing `t` on the keyboard.

### !unmute
Restore TX after `!mute`

**Example:**
```
!unmute
```

**Response:**
```
TX unmuted
```

### !id
Play the station ID — shortcut for `!play 0`

**Example:**
```
!id
```

**Response:**
```
Playing station ID: station_id.mp3
```

### !restart
Restart the gateway process cleanly

**Example:**
```
!restart
```

**Response:**
```
Gateway restarting...
```

**What happens:**
1. Gateway sends confirmation to Mumble
2. Cleanly shuts down all audio streams and Mumble connection
3. Replaces itself with a fresh process (same PID — Darkice and FFmpeg are unaffected)
4. Gateway reconnects to Mumble and resumes normal operation within a few seconds

Use this if audio stops working, Mumble connection drops, or the gateway gets into a bad state.

### !status
Show current gateway status

**Example:**
```
!status
```

**Response:**
```
╔════════════════════════════════════╗
║     GATEWAY STATUS REPORT          ║
╚════════════════════════════════════╝

📊 SYSTEM:
  CPU Load: 12.3%
  Memory: 45.2% (732 MB / 1024 MB)
  Uptime: 42 minutes

📻 RADIO:
  PTT: 🟢 Idle
  TX Muted: NO
  ...
```

### !help
Show available commands

**Example:**
```
!help
```

**Response:**
```
=== Gateway Commands ===
!speak <text> - TTS broadcast on radio
!play <0-9>   - Play announcement by slot
!files        - List loaded announcement files
!stop         - Stop playback and clear queue
!mute         - Mute TX (Mumble → Radio)
!unmute       - Unmute TX
!id           - Play station ID (slot 0)
!restart      - Restart the gateway
!status       - Show gateway status
!help         - Show this help
```

## Use Cases

### 1. Emergency Announcements
Net control types:
```
!speak Emergency traffic - all stations clear frequency immediately
```
All radio users hear: "Emergency traffic - all stations clear frequency immediately"

### 2. Remote Station ID
Anyone in Mumble types:
```
!id
```
Radio transmits station ID (or `!play 0` for the same result)

### 3. Net Management
Net control assistant types:
```
!speak Check-ins are now open. Please state your call sign and location.
```

### 4. Weather Updates
Weather watcher types:
```
!speak Severe thunderstorm warning issued for our area until 9 PM
```

### 5. Scheduled Announcements
Someone monitoring Mumble types:
```
!speak The weekly net will begin in 10 minutes on this frequency
```

### 6. Status Checking
User wants to know if gateway is working:
```
!status
```
Gets immediate text response without disturbing radio

### 7. Finding What's Loaded
Before playing an announcement, check what's in each slot:
```
!files
```
Lists all loaded files so you don't have to remember slot numbers

### 8. Aborting Playback
Wrong file queued or need to stop immediately:
```
!stop
```
Stops current audio and clears any queued files

### 9. Remote Mute
Gateway operator is away from the keyboard but needs to stop TX:
```
!mute
```
Stops Mumble audio from reaching the radio without physical access

### 10. Recovering a Stuck Gateway
Audio stops working or Mumble connection drops:
```
!restart
```
Gateway restarts cleanly — Darkice and FFmpeg keep running

## Audio Flow

**Text Commands:**
```
Mumble User → Text Message → Gateway
                              ↓
                         Text Response (for !status, !help)
```

**TTS Broadcast:**
```
Mumble User → !speak → Gateway → Generate TTS → Queue Audio
                                                     ↓
                                                  PTT ON
                                                     ↓
                                              Radio Broadcast
                                                     ↓
                                              All Radio Users Hear
```

**File Playback:**
```
Mumble User → !play → Gateway → Load File → Queue Audio
                                                 ↓
                                              PTT ON
                                                 ↓
                                          Radio Broadcast
```

## TTS Voice & Quality

- **Voice**: Google TTS (natural sounding)
- **Language**: English (US)
- **Format**: MP3
- **Sample Rate**: Converted to match gateway (48kHz default)
- **Quality**: High (Google's neural TTS)

## Limitations

- **Internet required** for TTS generation (uses Google API)
- **Rate limiting** by Google (usually not a problem for casual use)
- **No authentication** - any Mumble user can use commands
- **No queue management** - commands execute immediately

## Troubleshooting

**"TTS not available"**
- Check `ENABLE_TTS = true` in config
- Verify gtts installed: `pip3 list | grep gtts`
- Check internet connection

**Commands not responding:**
- Check `ENABLE_TEXT_COMMANDS = true` in config
- Verify you're in the same Mumble channel as gateway
- Check verbose logs for errors

**TTS sounds robotic:**
- Google TTS should sound natural
- If using older version of gtts, update: `pip3 install --upgrade gtts`

**Can't hear TTS on radio:**
- Verify file playback works (press keys 1-9)
- Check PTT is activating (LED on AIOC should light)
- Verify radio audio output levels

## Security Notes

**Current implementation has NO authentication:**
- Any Mumble user can trigger TTS
- Any Mumble user can play files
- Could be used to spam radio frequency

**For public servers, consider:**
- Whitelist authorized callsigns
- Add rate limiting
- Log all commands
- Require admin role in Mumble

## Advanced: Adding Authentication

Edit the `on_text_message` method:

```python
# Add this at the start of on_text_message
AUTHORIZED_USERS = ['W1XYZ', 'K2ABC', 'N3DEF']

if not any(call in sender_name for call in AUTHORIZED_USERS):
    self.send_text_message("Unauthorized")
    return
```

## Tips

1. **Keep TTS messages short** - easier to understand on radio
2. **Test volume** - TTS may be louder/quieter than normal audio
3. **Use phonetics** - "Alpha Bravo" instead of "A B"
4. **Avoid special characters** - stick to letters, numbers, basic punctuation
5. **Monitor before transmitting** - make sure frequency is clear

## Example Session

```
User1: !status
Gateway: [full status report]

User1: !files
Gateway: === Announcement Files ===
Gateway:   Station ID: station_id.mp3
Gateway:   Slot 1: 1_welcome.mp3
Gateway:   Slot 2: 2_net_open.mp3

User1: !id
Gateway: Playing station ID: station_id.mp3
[PTT keys, station ID broadcasts]

User2: !play 2
Gateway: Playing: 2_net_open.mp3
[PTT keys, announcement broadcasts]

User2: !stop
Gateway: Playback stopped

User1: !speak This is the weekly check-in net. All stations please stand by.
Gateway: Speaking: This is the weekly check-in net. All stations...
[PTT keys, message broadcasts on radio]

User1: !mute
Gateway: TX muted (Mumble → Radio)

User1: !unmute
Gateway: TX unmuted

User1: !restart
Gateway: Gateway restarting...
[Gateway disconnects, restarts, reconnects to Mumble within seconds]
```

## Future Enhancements

Possible additions:
- Save TTS to file slots: `!save 5 Emergency message text`
- Scheduled TTS: `!schedule 19:00 !speak Net time`
- Voice selection: `!voice male` or `!voice female`
- Language selection: `!lang es !speak Hola`
