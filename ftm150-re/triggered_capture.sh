#!/bin/bash
# Triggered capture: press Enter then immediately do the action
# Usage: ./triggered_capture.sh <name> [seconds]
NAME="${1:?Usage: $0 <name> [seconds]}"
SECS="${2:-3}"
DIR="/home/user/ftm150-re/captures"
mkdir -p "$DIR"

echo ""
echo "=== Capture: $NAME (${SECS}s) ==="
echo "Press ENTER then IMMEDIATELY do the action..."
read -r

sigrok-cli --driver fx2lafw --config samplerate=1m \
    --time "${SECS}s" --channels D0,D1 \
    -o "$DIR/${NAME}.sr" 2>/dev/null

sigrok-cli --input-file "$DIR/${NAME}.sr" \
    --protocol-decoders i2c:scl=D0:sda=D1 \
    --protocol-decoder-samplenum \
    --protocol-decoder-annotations i2c \
    2>&1 > "$DIR/${NAME}_raw.txt"

echo "Done: $(wc -l < "$DIR/${NAME}_raw.txt") lines"
echo "Saved: $DIR/${NAME}.sr"
