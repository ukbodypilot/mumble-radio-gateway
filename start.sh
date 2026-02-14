#!/bin/bash
# Start Broadcastify streaming - ROBUST VERSION

echo "=========================================="
echo "Starting Broadcastify Stream"
echo "=========================================="
echo ""

# Cleanup function
cleanup() {
    echo ""
    echo "Cleaning up..."
    if [ ! -z "$DARKICE_PID" ]; then
        kill $DARKICE_PID 2>/dev/null
        echo "  Stopped Darkice"
    fi
    if [ ! -z "$FFMPEG_PID" ]; then
        kill $FFMPEG_PID 2>/dev/null
        echo "  Stopped FFmpeg"
    fi
    rm -f /tmp/darkice_audio 2>/dev/null
    sudo modprobe -r snd-aloop 2>/dev/null
    echo "Done"
    exit
}

trap cleanup INT TERM

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "⚠ Warning: Running as root may cause permission issues"
    echo ""
fi

# 1. Kill any existing processes
echo "[1/6] Checking for existing processes..."
pkill -9 darkice 2>/dev/null && echo "  Killed existing Darkice"
pkill -9 ffmpeg 2>/dev/null && echo "  Killed existing FFmpeg"
sleep 1

# Also kill any Python gateway processes (just in case)
pkill -9 -f "mumble_radio_gateway" 2>/dev/null && echo "  Killed existing gateway"
sleep 1

# 2. Unload and reload ALSA loopback (fresh start)
echo "[2/6] Resetting ALSA loopback..."
sudo modprobe -r snd-aloop 2>/dev/null
sleep 1
sudo modprobe snd-aloop
if [ $? -ne 0 ]; then
    echo "  ✗ Failed to load ALSA loopback"
    exit 1
fi
sleep 2  # Wait for device to be ready
echo "  ✓ ALSA loopback loaded"

# Verify device exists
if ! aplay -l 2>/dev/null | grep -q "Loopback"; then
    echo "  ⚠ Warning: Loopback device not visible in aplay -l"
fi

# 3. Create named pipe
echo "[3/6] Creating named pipe..."
# Force remove old pipe (even if busy)
rm -f /tmp/darkice_audio 2>/dev/null
# Kill any processes using it
fuser -k /tmp/darkice_audio 2>/dev/null
sleep 1
# Create fresh pipe
mkfifo /tmp/darkice_audio
chmod 666 /tmp/darkice_audio
echo "  ✓ Pipe created: /tmp/darkice_audio"

# 4. Start Darkice with visible output
echo "[4/6] Starting Darkice..."
echo "  (Darkice output will be shown below)"
echo "  ----------------------------------------"

# Start Darkice in background but capture output
darkice -c /etc/darkice.cfg > /tmp/darkice.log 2>&1 &
DARKICE_PID=$!

# Wait and check if it started successfully
sleep 4

if ! ps -p $DARKICE_PID > /dev/null 2>&1; then
    echo "  ----------------------------------------"
    echo "  ✗ Darkice FAILED to start!"
    echo ""
    echo "Error output:"
    cat /tmp/darkice.log
    echo ""
    echo "Common fixes:"
    echo "  1. Check /etc/darkice.cfg has: device = hw:Loopback,1,0"
    echo "  2. Check bitrate matches Broadcastify (usually 16)"
    echo "  3. Check Broadcastify password is correct"
    echo "  4. Run: sudo modprobe -r snd-aloop && sudo modprobe snd-aloop"
    cleanup
fi

# Show first few lines of Darkice output
head -n 10 /tmp/darkice.log
echo "  ----------------------------------------"
echo "  ✓ Darkice running (PID: $DARKICE_PID)"
echo "  Full log: /tmp/darkice.log"

# 5. Start FFmpeg bridge with auto-restart
echo "[5/6] Starting FFmpeg bridge..."
(
    while true; do
        ffmpeg -loglevel error -f s16le -ar 48000 -ac 1 -i /tmp/darkice_audio \
               -f alsa hw:Loopback,0,0 2>&1
        sleep 1
    done
) > /tmp/ffmpeg.log 2>&1 &
FFMPEG_PID=$!
sleep 2

if ! ps -p $FFMPEG_PID > /dev/null; then
    echo "  ✗ FFmpeg failed to start!"
    cat /tmp/ffmpeg.log
    cleanup
fi

echo "  ✓ FFmpeg bridge running (PID: $FFMPEG_PID)"

# 6. Start Gateway
echo "[6/6] Starting gateway..."
echo ""

# Find the gateway file
GATEWAY_FILE=""
for location in \
    "mumble_radio_gateway_phase3.py" \
    "./mumble_radio_gateway.py" \
    "$HOME/mumble_radio_gateway.py" \
    "$HOME/Downloads/mumble_radio_gateway.py" \
    "/home/*/Downloads/mumble_radio_gateway.py"
do
    if [ -f "$location" ]; then
        GATEWAY_FILE="$location"
        break
    fi
done

if [ -z "$GATEWAY_FILE" ]; then
    echo "✗ Gateway file not found!"
    echo "  Looking for: mumble_radio_gateway.py"
    echo "  Searched:"
    echo "    - Current directory"
    echo "    - $HOME"
    echo "    - $HOME/Downloads"
    cleanup
fi

echo "Using: $GATEWAY_FILE"
echo ""
echo "=========================================="
echo "All components started successfully!"
echo "=========================================="
echo "  Darkice:  PID $DARKICE_PID (log: /tmp/darkice.log)"
echo "  FFmpeg:   PID $FFMPEG_PID (log: /tmp/ffmpeg.log)"
echo "  Gateway:  Starting now..."
echo ""
echo "Press Ctrl+C to stop everything"
echo ""
sleep 2

# Start gateway (this will block)
python3 "$GATEWAY_FILE"

# If gateway exits, cleanup
cleanup
