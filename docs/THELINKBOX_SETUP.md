# TheLinkBox EchoLink Setup Guide

## Quick Start (5 Minutes)

### Step 1: Get EchoLink Account (Free)

1. Visit **echolink.org**
2. Click "Register for EchoLink"
3. Provide:
   - Valid amateur radio callsign
   - Email address
   - Password
4. Verify email
5. Wait for callsign validation (~24-48 hours)

**Note:** You need a **valid FCC-issued** amateur radio license!

---

### Step 2: Install TheLinkBox

**Debian/Ubuntu/Raspberry Pi:**
```bash
sudo apt-get update
sudo apt-get install thelinkbox
```

**Check Installation:**
```bash
which thelinkbox
# Should show: /usr/bin/thelinkbox

thelinkbox --version
# Should show version info
```

---

### Step 3: Configure TheLinkBox

**Edit Config File:**
```bash
sudo nano /etc/thelinkbox/thelinkbox.conf
```

**Minimal Configuration:**
```ini
[GLOBAL]
# Your callsign with -L suffix (for Link)
CALLSIGN=W1XYZ-L

# Your EchoLink password
PASSWORD=your_echolink_password

# Your location (City, State)
LOCATION=San Francisco, CA

# Your name
SYSOPNAME=John Doe

# Audio device (we'll use named pipes instead)
# Leave commented out for now
#AUDIO_DEVICE=/dev/dsp
```

**Save and Exit:** Ctrl+O, Enter, Ctrl+X

---

### Step 4: Start TheLinkBox

```bash
# Start service
sudo systemctl start thelinkbox

# Enable on boot
sudo systemctl enable thelinkbox

# Check status
sudo systemctl status thelinkbox
```

**Expected Output:**
```
● thelinkbox.service - TheLinkBox EchoLink Gateway
   Loaded: loaded (/lib/systemd/system/thelinkbox.service; enabled)
   Active: active (running) since ...
```

**Check Logs:**
```bash
sudo journalctl -u thelinkbox -f
```

Look for:
```
Connected to EchoLink proxy
Logged in as W1XYZ-L
Ready for connections
```

---

### Step 5: Test Connection

**Option 1: Check via Web**
Visit **echolink.org** → "Nodes Online" → Search for your callsign

**Option 2: Use qtel (GUI client)**
```bash
sudo apt-get install qtel
qtel
```

Login with your callsign, connect to test server (9999), verify audio.

**Option 3: Check TheLinkBox Logs**
```bash
sudo tail -f /var/log/thelinkbox/thelinkbox.log
```

---

### Step 6: Enable in Gateway

**Edit gateway_config.txt:**
```ini
# Enable EchoLink integration
ENABLE_ECHOLINK = true

# Named pipe paths (default, gateway creates these)
ECHOLINK_RX_PIPE = /tmp/echolink_rx
ECHOLINK_TX_PIPE = /tmp/echolink_tx

# Audio routing
ECHOLINK_TO_MUMBLE = true   # EchoLink → Mumble
RADIO_TO_ECHOLINK = true    # Radio → EchoLink
ECHOLINK_TO_RADIO = false   # Safety: don't let EchoLink TX on radio
MUMBLE_TO_ECHOLINK = false  # Mumble users don't go to EchoLink
```

**Start Gateway:**
```bash
python3 mumble_radio_gateway_phase3.py
```

**Expected Output:**
```
Initializing EchoLink integration...
  Created FIFO: /tmp/echolink_rx
  Created FIFO: /tmp/echolink_tx
  ✓ EchoLink IPC connected via named pipes
    RX: /tmp/echolink_rx
    TX: /tmp/echolink_tx
✓ EchoLink source added to mixer
  Audio routing:
    EchoLink → Mumble: ON
    Radio RX → EchoLink: ON
```

---

## Complete Configuration Reference

### TheLinkBox Config (/etc/thelinkbox/thelinkbox.conf)

```ini
[GLOBAL]
# ============================================================================
# BASIC SETTINGS (Required)
# ============================================================================

# Callsign with -L suffix (Link station)
# -R for repeater, -L for link
CALLSIGN=W1XYZ-L

# EchoLink account password
PASSWORD=your_password

# Station location (shown to other users)
LOCATION=San Francisco, CA

# Sysop name
SYSOPNAME=John Doe

# ============================================================================
# PROXY SETTINGS (Automatic - No Configuration Needed!)
# ============================================================================
#
# TheLinkBox automatically:
# - Contacts EchoLink directory server
# - Gets list of free public proxies
# - Selects best proxy based on latency
# - Handles NAT traversal
# - Reconnects if proxy fails
#
# YOU DON'T NEED TO CONFIGURE PROXIES!
#
# ============================================================================

# ============================================================================
# AUDIO SETTINGS (For Direct Audio - Not Used with Gateway)
# ============================================================================
#
# When using with our gateway, audio goes through named pipes instead
# of sound card. These settings are ignored but left for reference.

# Audio device (commented out - we use named pipes)
#AUDIO_DEVICE=/dev/dsp

# Sample rate (ignored when using pipes)
#SAMPLE_RATE=8000

# ============================================================================
# ADVANCED SETTINGS (Optional)
# ============================================================================

# Enable/disable features
ENABLE_LINKING=yes
ENABLE_CONFERENCE=yes

# Connection timeout (seconds)
TIMEOUT=300

# Reconnect on disconnect
AUTO_RECONNECT=yes

# Log file location
LOG_FILE=/var/log/thelinkbox/thelinkbox.log

# Log level (0=quiet, 3=verbose)
LOG_LEVEL=1

# ============================================================================
# SECURITY (Optional but Recommended)
# ============================================================================

# Allowed/blocked callsigns (comma-separated)
#ALLOW_CALLSIGNS=W1ABC,K2DEF
#BLOCK_CALLSIGNS=W9BAD,K3EVIL

# Require password for connections
#REQUIRE_PASSWORD=yes
#CONNECTION_PASSWORD=mysecret

# IP whitelist/blacklist
#ALLOW_IPS=192.168.1.0/24
#BLOCK_IPS=10.0.0.0/8
```

---

## Troubleshooting

### Issue: TheLinkBox won't start

**Check systemd status:**
```bash
sudo systemctl status thelinkbox
```

**Check configuration syntax:**
```bash
sudo thelinkbox --check-config
```

**Common Issues:**
- CALLSIGN missing -L suffix
- PASSWORD incorrect (check EchoLink account)
- Invalid characters in LOCATION

### Issue: "Connection refused" to EchoLink

**Verify internet connection:**
```bash
ping echolink.org
```

**Check firewall:**
```bash
# Allow outbound UDP port 5200
sudo ufw allow out 5200/udp

# Or disable firewall for testing
sudo ufw disable
```

**Check TheLinkBox logs:**
```bash
sudo journalctl -u thelinkbox -n 100
```

Look for:
- DNS resolution failures
- Network timeouts
- Authentication errors

### Issue: Gateway says "EchoLink IPC setup failed"

**Check TheLinkBox is running:**
```bash
sudo systemctl status thelinkbox
```

**Check named pipes exist:**
```bash
ls -l /tmp/echolink_*
```

Should show:
```
prw-rw-rw- 1 user user 0 Feb 11 10:00 /tmp/echolink_rx
prw-rw-rw- 1 user user 0 Feb 11 10:00 /tmp/echolink_tx
```

**If pipes don't exist, gateway creates them automatically**

**Fix permissions if needed:**
```bash
sudo chmod 666 /tmp/echolink_rx /tmp/echolink_tx
```

### Issue: No audio from EchoLink

**Test pipes manually:**

**Terminal 1 (simulate gateway reading):**
```bash
cat /tmp/echolink_rx | hexdump -C
```

**Terminal 2 (simulate TheLinkBox writing):**
```bash
# Use qtel to connect to a station
# You should see hex data in Terminal 1
```

**If no data flows:**
- TheLinkBox may not be configured to use pipes
- Check TheLinkBox audio settings
- Verify connection is established

**Check TheLinkBox connection:**
```bash
sudo tail -f /var/log/thelinkbox/thelinkbox.log
```

Look for:
```
Connection established to W9XYZ-L
Audio streaming active
```

### Issue: Audio stutters or drops

**Increase pipe buffer size:**

Edit `/etc/thelinkbox/thelinkbox.conf`:
```ini
PIPE_BUFFER_SIZE=65536  # 64KB buffer
```

**Check system load:**
```bash
top
# Look for high CPU usage
```

**Increase gateway chunk size:**

Edit `gateway_config.txt`:
```ini
AUDIO_CHUNK_SIZE = 9600  # Already at recommended value
```

---

## Testing Your Setup

### Test 1: TheLinkBox Standalone

**Use qtel to verify TheLinkBox works:**
```bash
sudo apt-get install qtel
qtel
```

1. Login with your callsign
2. Connect to echo test server (9999)
3. Speak and listen for echo
4. If echo works, TheLinkBox is good!

### Test 2: Named Pipe Data Flow

**Terminal 1 (Read RX pipe):**
```bash
while true; do
  dd if=/tmp/echolink_rx bs=1024 count=1 2>/dev/null | hexdump -C | head -5
  sleep 1
done
```

**Terminal 2 (Write TX pipe):**
```bash
# Play audio file into pipe
cat test_audio.raw > /tmp/echolink_tx
```

**You should see hex data flowing in Terminal 1**

### Test 3: Gateway Integration

**Start gateway with verbose logging:**
```ini
VERBOSE_LOGGING = true
```

**Watch for:**
```
[EchoLink] Read error: ...
[EchoLink] Write error: ...
```

**Status line should show audio activity:**
```
RX:[████] ← Audio from EchoLink/Radio
```

### Test 4: End-to-End

1. **Start gateway**
2. **Use qtel to connect to your station** (YOURCALL-L)
3. **Speak into qtel** → Should appear in Mumble
4. **Key radio** → Should go to qtel

---

## Configuration Examples

### Example 1: Receive Only (Safe)

```ini
# Gateway config
ENABLE_ECHOLINK = true
ECHOLINK_TO_MUMBLE = true   # EchoLink → Mumble ✓
RADIO_TO_ECHOLINK = true    # Radio → EchoLink ✓
ECHOLINK_TO_RADIO = false   # EchoLink → Radio ✗ (blocked)
MUMBLE_TO_ECHOLINK = false  # Mumble → EchoLink ✗ (blocked)
```

**Result:**
- EchoLink users hear Mumble and Radio
- Mumble users hear EchoLink
- EchoLink cannot transmit on radio (safe!)

### Example 2: Full Duplex (Advanced)

```ini
ENABLE_ECHOLINK = true
ECHOLINK_TO_MUMBLE = true
ECHOLINK_TO_RADIO = true    # ⚠️ EchoLink can TX on radio!
RADIO_TO_ECHOLINK = true
MUMBLE_TO_ECHOLINK = true
```

**Result:**
- Full interconnectivity
- ⚠️ **Warning:** EchoLink can transmit on your radio!
- Only use if you understand the risks

### Example 3: Conference Bridge

```ini
ENABLE_ECHOLINK = true
ECHOLINK_TO_MUMBLE = true
MUMBLE_TO_ECHOLINK = true   # Mumble ↔ EchoLink
RADIO_TO_ECHOLINK = false   # Radio not in conference
ECHOLINK_TO_RADIO = false
```

**Result:**
- Mumble and EchoLink users can talk
- Radio is separate (local repeater use)

---

## Advanced Topics

### Running TheLinkBox in Docker

```dockerfile
FROM debian:bookworm
RUN apt-get update && apt-get install -y thelinkbox
COPY thelinkbox.conf /etc/thelinkbox/
CMD ["thelinkbox"]
```

### Multiple Instances

Run multiple TheLinkBox instances with different callsigns:

```bash
# Instance 1: W1XYZ-L
thelinkbox --config /etc/thelinkbox/instance1.conf

# Instance 2: W1XYZ-R  
thelinkbox --config /etc/thelinkbox/instance2.conf
```

Use different pipe paths for each:
```ini
# Instance 1
ECHOLINK_RX_PIPE = /tmp/echolink1_rx
ECHOLINK_TX_PIPE = /tmp/echolink1_tx

# Instance 2
ECHOLINK_RX_PIPE = /tmp/echolink2_rx
ECHOLINK_TX_PIPE = /tmp/echolink2_tx
```

### Security Best Practices

1. **Limit connections:**
```ini
ALLOW_CALLSIGNS=W1ABC,K2DEF,N3GHI
```

2. **Block known troublemakers:**
```ini
BLOCK_CALLSIGNS=BADCALL,SPAMMER
```

3. **Require password:**
```ini
REQUIRE_PASSWORD=yes
CONNECTION_PASSWORD=mysecretpass
```

4. **Firewall rules:**
```bash
# Allow only EchoLink proxies (example IPs)
sudo ufw allow from 45.79.xxx.xxx to any port 5200 proto udp
```

---

## Resources

**Official Links:**
- EchoLink: https://echolink.org
- TheLinkBox: https://www.svxlink.org
- Documentation: https://github.com/sm0svx/svxlink

**Community:**
- EchoLink Forums
- Reddit: /r/amateurradio
- QRZ.com forums

**Testing:**
- Echo Test Server: Node 9999
- Conference Servers: Search "conference" on EchoLink

---

## Summary

**Setup Steps:**
1. ✅ Register EchoLink account (free)
2. ✅ Install TheLinkBox (`apt-get install`)
3. ✅ Configure callsign + password
4. ✅ Start TheLinkBox service
5. ✅ Enable in gateway config
6. ✅ Test with qtel
7. ✅ Verify named pipes work
8. ✅ Enjoy EchoLink connectivity!

**Total Time:** ~15 minutes (plus callsign validation wait)

**Cost:** $0 (free public proxies!)

**Complexity:** Low (automatic NAT traversal)

**Reliability:** High (global proxy network)

You're now ready to connect your radio gateway to the worldwide EchoLink network! 🌍📻
