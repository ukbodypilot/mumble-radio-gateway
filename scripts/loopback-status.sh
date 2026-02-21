#!/bin/bash

printf "%-12s %-8s %-10s %-20s %-18s\n" "Card Name" "HW Card" "Device" "Playback Avail" "Capture Avail"
printf "%-12s %-8s %-10s %-20s %-18s\n" "----------" "-------" "--------" "--------------" "-------------"

for card_dir in /proc/asound/card*/; do
    card_num=$(basename "$card_dir" | tr -d 'card')

    grep -q "Loopback" "$card_dir/id" 2>/dev/null || continue

    # Get friendly name e.g. "Loopback 1" from second line of card entry
    card_name=$(awk -v c="$card_num" '
        $1==c { getline; gsub(/^[ \t]+/, ""); print; exit }
    ' /proc/asound/cards)

    for dev_num in 0 1; do
        pb_info="$card_dir/pcm${dev_num}p/info"
        cap_info="$card_dir/pcm${dev_num}c/info"

        [ -f "$pb_info" ] || continue

        pb_avail=$(awk '/subdevices_avail/{print $2}' "$pb_info")
        pb_count=$(awk '/subdevices_count/{print $2}' "$pb_info")
        cap_avail=$(awk '/subdevices_avail/{print $2}' "$cap_info")

        in_use=$((pb_count - pb_avail))
        if [ "$in_use" -gt 0 ]; then
            pb_str="$pb_avail (${in_use} in use)"
        else
            pb_str="$pb_avail"
        fi

        printf "%-12s %-8s %-10s %-20s %-18s\n" "$card_name" "hw:$card_num" "hw:$card_num,$dev_num" "$pb_str" "$cap_avail"
    done
done
