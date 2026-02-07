# Hardware Setup Guide

## AIOC Connections

### To Raspberry Pi
- Connect AIOC to any USB port on Raspberry Pi
- No drivers needed (uses standard USB audio + HID)

### To Radio

**Audio Connections:**
- AIOC Audio Out (green) → Radio Mic/Audio In
- AIOC Audio In (pink) → Radio Speaker/Audio Out
- Adjust levels using radio's volume controls

**PTT Connection:**
- AIOC PTT (GPIO pin) → Radio PTT input
- Check your radio's manual for PTT pinout
- Usually: Tip = PTT, Ring = Audio, Sleeve = Ground

**Common Radio Connectors:**
- Kenwood/Baofeng: 2.5mm + 3.5mm jack
- Yaesu: 6-pin mini-DIN
- Icom: 8-pin modular
- See AIOC documentation for cable options

## Raspberry Pi Setup

**Recommended Models:**
- Raspberry Pi 4 (4GB RAM minimum)
- Raspberry Pi 5
- Raspberry Pi CM5 with IO board

**Power Requirements:**
- Official power supply (5V/3A minimum)
- Stable power critical for USB audio

**Operating System:**
- Raspberry Pi OS (Debian-based)
- Ubuntu Server 22.04+ LTS
- 64-bit recommended for best performance

## Testing

1. **Test AIOC USB Detection:**
   ```bash
   lsusb | grep 1209:7388
   # Should show: "1209:7388 Generic All-In-One-Cable"
   ```

2. **Test Audio Devices:**
   ```bash
   arecord -l  # List capture devices
   aplay -l    # List playback devices
   # Should show AIOC as USB Audio device
   ```

3. **Test PTT:**
   ```bash
   python3 scripts/aioc_ptt_test.py
   # Press space to test PTT activation
   ```

## Troubleshooting

**AIOC Not Detected:**
- Try different USB port
- Check USB cable
- Verify UDEV rules installed
- Reboot Raspberry Pi

**No Audio:**
- Check volume levels on radio
- Verify AIOC audio connections
- Run `alsamixer` to check mute status
- Check AIOC_INPUT_DEVICE / AIOC_OUTPUT_DEVICE in config

**PTT Not Working:**
- Verify AIOC_PTT_CHANNEL (3 or 4)
- Check radio PTT wiring
- Test with aioc_ptt_test.py
- Some radios require PTT pulldown resistor
