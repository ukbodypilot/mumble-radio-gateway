#!/bin/bash
# FTM-150 I2C capture helper
# Usage: ./capture.sh <action_name> [seconds]
# Example: ./capture.sh freq_up_one 5

ACTION="${1:?Usage: $0 <action_name> [seconds]}"
SECONDS="${2:-5}"
OUTDIR="/home/user/ftm150-re/captures"
mkdir -p "$OUTDIR"

SR_FILE="$OUTDIR/${ACTION}.sr"
TXT_FILE="$OUTDIR/${ACTION}.txt"

echo ">>> Ready to capture '$ACTION' for ${SECONDS}s"
echo ">>> Press ENTER, then perform the action..."
read -r

echo ">>> Capturing..."
sigrok-cli --driver fx2lafw --config samplerate=1m --time "${SECONDS}s" --channels D0,D1 -o "$SR_FILE" 2>&1

echo ">>> Decoding I2C..."
sigrok-cli --input-file "$SR_FILE" \
    --protocol-decoders i2c:scl=D0:sda=D1 \
    --protocol-decoder-annotations i2c 2>&1 > "$TXT_FILE"

echo ">>> Done: $SR_FILE ($(wc -l < "$TXT_FILE") lines decoded)"
echo ">>> Decode saved to: $TXT_FILE"
