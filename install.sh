#!/bin/bash
# Installation script for Mumble Radio Gateway
# For Raspberry Pi / Debian-based systems

set -e

echo "============================================================"
echo "Mumble Radio Gateway - Installation Script"
echo "============================================================"
echo

# Check if running on Raspberry Pi
if [ ! -f /proc/device-tree/model ]; then
    echo "⚠ Warning: This script is designed for Raspberry Pi"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo "1. Installing system dependencies..."
sudo apt-get update
sudo apt-get install -y \
    python3-pip \
    python3-pyaudio \
    portaudio19-dev \
    libhidapi-libusb0 \
    git

echo
echo "2. Installing Python packages..."
pip3 install -r requirements.txt --break-system-packages

echo
echo "3. Setting up UDEV rules for AIOC..."
if [ ! -f /etc/udev/rules.d/99-aioc.rules ]; then
    echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="7388", MODE="0666"' | \
        sudo tee /etc/udev/rules.d/99-aioc.rules
    sudo udevadm control --reload-rules
    sudo udevadm trigger
    echo "✓ UDEV rules installed"
else
    echo "✓ UDEV rules already exist"
fi

echo
echo "4. Creating example configuration..."
if [ ! -f gateway_config.txt ]; then
    cp examples/gateway_config.example.txt gateway_config.txt
    echo "✓ Created gateway_config.txt - EDIT THIS FILE with your settings!"
else
    echo "✓ gateway_config.txt already exists (not overwriting)"
fi

echo
echo "5. Making scripts executable..."
chmod +x mumble_radio_gateway.py
chmod +x scripts/*.py 2>/dev/null || true

echo
echo "============================================================"
echo "Installation Complete!"
echo "============================================================"
echo
echo "Next steps:"
echo "  1. Edit gateway_config.txt with your Mumble server details"
echo "  2. Connect your AIOC to the Raspberry Pi"
echo "  3. Connect your AIOC to your radio"
echo "  4. Run: ./mumble_radio_gateway.py"
echo
echo "Test AIOC connection:"
echo "  python3 scripts/aioc_ptt_test.py"
echo
echo "For help: cat README.md"
echo
