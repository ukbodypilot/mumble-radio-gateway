#!/bin/bash
# Interactive capture tool for FTM-150 reverse engineering
# Usage: ./cap.sh <name> [seconds]
# Example: ./cap.sh ll_cw 10
#
# - Press ENTER when ready
# - HIGH beep = capturing, do your action
# - LOW beep  = done

NAME="${1:?Usage: $0 <name> [seconds]}"
SECS="${2:-12}"
DIR="/home/user/ftm150-re/captures"
mkdir -p "$DIR"

beep() {
    local freq="${1:-1000}" dur="${2:-0.3}"
    python3 -c "
import wave, struct, math, subprocess, tempfile, os
sr=22050; dur=$dur; freq=$freq
f=tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
w=wave.open(f.name,'w'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
w.writeframes(b''.join(struct.pack('<h',int(30000*math.sin(2*math.pi*freq*i/sr))) for i in range(int(sr*dur))))
w.close(); subprocess.run(['paplay',f.name],stderr=subprocess.DEVNULL); os.unlink(f.name)
" 2>/dev/null
}

echo ""
echo "=== Capture: $NAME (${SECS}s) ==="
echo "Press ENTER when ready"
echo "  HIGH beep = go"
echo "  LOW beep  = done"
echo ""
read -r

beep 1000 0.15
sigrok-cli --driver fx2lafw --config samplerate=8m \
    --time "${SECS}s" --channels D0,D1 \
    -o "$DIR/${NAME}.sr" 2>/dev/null
beep 600 0.5

echo "Done. Saved: $DIR/${NAME}.sr"
