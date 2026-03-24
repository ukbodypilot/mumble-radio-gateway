#!/bin/bash
# claude-gateway.sh — Start claude --dangerously-skip-permissions in a tmux session.
# Run by claude-gateway.service. Blocks until the session ends so systemd can
# track and restart it.

SESSION="claude-gateway"
WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"
CLAUDE_BIN="${CLAUDE_BIN:-/home/user/.local/bin/claude}"
export TERM=xterm-256color
export PATH="/home/user/.local/bin:$PATH"

# Kill any stale session from a previous run
tmux kill-session -t "$SESSION" 2>/dev/null || true

# Create the session with claude running inside it
tmux new-session -d -s "$SESSION" -c "$WORKDIR" \
    -e TERM=xterm-256color -e HOME=/home/user -e PATH="/home/user/.local/bin:/usr/local/bin:/usr/bin:/bin" \
    "$CLAUDE_BIN --dangerously-skip-permissions"
if [ $? -ne 0 ]; then
    echo "Failed to create tmux session" >&2
    exit 1
fi

echo "tmux session '$SESSION' started"

# Block until the session exits so systemd sees us as running
while tmux has-session -t "$SESSION" 2>/dev/null; do
    sleep 5
done

echo "tmux session '$SESSION' ended"
