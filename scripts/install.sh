#!/bin/bash
# ============================================================
# Mumble Radio Gateway — Installation Script
# Supports: Raspberry Pi, Debian/Ubuntu amd64, any Debian-based Linux
# ============================================================

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
GATEWAY_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"

echo "============================================================"
echo "Mumble Radio Gateway - Installation"
echo "============================================================"
echo "Gateway directory: $GATEWAY_DIR"
echo

# ── Detect platform ──────────────────────────────────────────
ARCH=$(uname -m)
IS_PI=false
if [ -f /proc/device-tree/model ] && grep -qi "raspberry" /proc/device-tree/model 2>/dev/null; then
    IS_PI=true
fi

echo "Platform: $ARCH"
if $IS_PI; then
    echo "Detected: Raspberry Pi"
else
    echo "Detected: Standard Linux PC"
fi
echo

# ── 1. System packages ───────────────────────────────────────
echo "[ 1/7 ] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-pyaudio \
    portaudio19-dev \
    libhidapi-libusb0 \
    libhidapi-dev \
    libsndfile1 \
    ffmpeg \
    git

echo "  ✓ System packages installed"
echo

# ── 2. ALSA loopback module ──────────────────────────────────
echo "[ 2/7 ] Setting up ALSA loopback (for SDR input)..."

# Write modprobe options first (numlids=3 → 3 independent loopback cards)
echo "options snd-aloop numlids=3" | sudo tee /etc/modprobe.d/snd-aloop.conf > /dev/null
echo "  ✓ /etc/modprobe.d/snd-aloop.conf → numlids=3"

# Always unload and reload so the numlids setting takes effect
if lsmod | grep -q snd_aloop; then
    sudo modprobe -r snd-aloop 2>/dev/null || true
    sleep 1
fi
sudo modprobe snd-aloop numlids=3
if [ $? -ne 0 ]; then
    echo "  ✗ Failed to load snd-aloop — check kernel headers are installed"
    exit 1
fi
echo "  ✓ snd-aloop loaded (3 loopback cards)"

# Make it load on boot
if ! grep -q "snd-aloop" /etc/modules 2>/dev/null; then
    echo "snd-aloop" | sudo tee -a /etc/modules > /dev/null
    echo "  ✓ Added snd-aloop to /etc/modules (auto-load on boot)"
else
    echo "  ✓ snd-aloop already in /etc/modules"
fi

# Verify
LOOPBACK_COUNT=$(aplay -l 2>/dev/null | grep -c "Loopback" || true)
echo "  Loopback cards visible: $LOOPBACK_COUNT (expected 3)"
echo "    Each card: hw:N,0 (SDR app writes here) / hw:N,1 (gateway reads here)"
echo "    Find card numbers: aplay -l | grep Loopback"
echo

# ── 3. Python packages ───────────────────────────────────────
echo "[ 3/7 ] Installing Python packages..."

# Helper: try --break-system-packages (Debian 12+), then plain pip
_pip() {
    pip3 install "$@" --break-system-packages 2>/dev/null \
        || pip3 install "$@" 2>/dev/null
}

# Core packages (excluding pymumble — handled separately due to PyPI name variants)
set +e
_pip hidapi numpy pyaudio soundfile resampy psutil gtts
CORE_STATUS=$?
set -e
if [ $CORE_STATUS -eq 0 ]; then
    echo "  ✓ Core Python packages installed"
else
    echo "  ⚠ Some core packages may have failed — check output above"
fi

# pymumble: try pymumble-py3 first (Python-3 fork), fall back to pymumble
set +e
MUMBLE_OK=false
if _pip "pymumble-py3>=1.0.0" 2>/dev/null; then
    echo "  ✓ pymumble-py3 installed"
    MUMBLE_OK=true
elif _pip pymumble 2>/dev/null; then
    echo "  ✓ pymumble installed (fallback package name)"
    MUMBLE_OK=true
fi
set -e
if ! $MUMBLE_OK; then
    echo "  ✗ Could not install pymumble automatically"
    echo "    Try manually: pip3 install pymumble --break-system-packages"
    echo "              or: pip3 install pymumble-py3 --break-system-packages"
fi
echo

# ── 4. UDEV rules for AIOC ──────────────────────────────────
echo "[ 4/7 ] Setting up UDEV rules for AIOC USB device..."
UDEV_RULE='SUBSYSTEM=="usb", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="7388", MODE="0666", GROUP="audio"'

if [ ! -f /etc/udev/rules.d/99-aioc.rules ]; then
    echo "$UDEV_RULE" | sudo tee /etc/udev/rules.d/99-aioc.rules > /dev/null
    sudo udevadm control --reload-rules
    sudo udevadm trigger
    echo "  ✓ UDEV rules installed — AIOC accessible without sudo"
else
    echo "  ✓ UDEV rules already exist"
fi
echo

# ── 5. Darkice (optional — for Broadcastify/Icecast streaming) ──
echo "[ 5/7 ] Darkice streaming (optional)..."
set +e
sudo apt-get install -y darkice lame 2>/dev/null
DARKICE_STATUS=$?
set -e
if [ $DARKICE_STATUS -eq 0 ]; then
    echo "  ✓ Darkice installed"
    echo "  ℹ  To use streaming: configure /etc/darkice.cfg and set"
    echo "     ENABLE_STREAM_OUTPUT = true in gateway_config.txt"
else
    echo "  ⚠ darkice could not be installed from apt — skipping"
    echo "    This is optional: streaming to Broadcastify requires darkice,"
    echo "    but all other gateway features work without it."
    echo "    To install manually: sudo apt-get install darkice lame"
fi
echo

# ── 6. Gateway configuration ─────────────────────────────────
echo "[ 6/7 ] Setting up configuration..."

CONFIG_DEST="$GATEWAY_DIR/gateway_config.txt"
CONFIG_SRC="$GATEWAY_DIR/examples/gateway_config.txt"

if [ ! -f "$CONFIG_DEST" ]; then
    if [ -f "$CONFIG_SRC" ]; then
        cp "$CONFIG_SRC" "$CONFIG_DEST"
        echo "  ✓ Created gateway_config.txt from example"
    else
        echo "  ⚠ Example config not found — you will need to create gateway_config.txt manually"
    fi
else
    echo "  ✓ gateway_config.txt already exists (not overwritten)"
fi

# Create audio directory for announcements
mkdir -p "$GATEWAY_DIR/audio"
echo "  ✓ audio/ directory ready (place announcement files here)"
echo

# ── 7. Make scripts executable ───────────────────────────────
echo "[ 7/7 ] Setting permissions..."
chmod +x "$GATEWAY_DIR/mumble_radio_gateway.py" 2>/dev/null || true
chmod +x "$GATEWAY_DIR/scripts/"*.sh 2>/dev/null || true
chmod +x "$GATEWAY_DIR/start.sh" 2>/dev/null || true
echo "  ✓ Scripts are executable"
echo

# ── Summary ──────────────────────────────────────────────────
echo "============================================================"
echo "Installation complete!"
echo "============================================================"
echo
echo "NEXT STEPS:"
echo
echo "  1. Edit gateway_config.txt:"
echo "       MUMBLE_SERVER   = your.mumble.server"
echo "       MUMBLE_PORT     = 64738"
echo "       MUMBLE_USERNAME = RadioGateway"
echo
echo "  2. Connect your AIOC USB device"
echo "     (unplug and replug after install so udev rules take effect)"
echo
echo "  3. Run the gateway:"
echo "       python3 $GATEWAY_DIR/mumble_radio_gateway.py"
echo
echo "SDR INPUT (optional):"
echo "  Route SDR software audio output to ALSA loopback hw:X,0"
echo "  Gateway reads from the capture side: hw:X,1"
echo "  Set SDR_DEVICE_NAME in gateway_config.txt"
echo "  Verify loopback devices: aplay -l | grep Loopback"
echo
echo "STREAMING (optional):"
echo "  Configure /etc/darkice.cfg with your Broadcastify credentials"
echo "  Set ENABLE_STREAM_OUTPUT = true in gateway_config.txt"
echo "  Use start.sh to launch gateway + Darkice together"
echo
echo "DOCS:"
echo "  README.md                       — full documentation"
echo "  docs/MANUAL.txt                 — user guide"
echo "  docs/TTS_TEXT_COMMANDS_GUIDE.md — Mumble text commands"
echo
