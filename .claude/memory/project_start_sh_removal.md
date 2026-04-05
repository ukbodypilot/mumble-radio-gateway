---
name: start.sh removal plan
description: User wants to eliminate start.sh by absorbing remaining pre-flight steps into the gateway Python code
type: project
---

start.sh is mostly dead weight after DarkIce/FFmpeg/loopback removal. User wants it gone long term.

**Why:** Messy, hard to maintain, splits startup logic between bash and Python.

**How to apply:** When working on startup code, absorb remaining start.sh steps into gateway Python:
- Step 1 (kill stale procs): `os.kill` / `subprocess.run(['pkill', ...])` in gateway startup
- Step 3 (TH-9800 CAT systemd): already handled by th9800_plugin.py
- Step 5 (CPU governor): `os.nice(-10)` + write to sysfs in Python
- Step 7 (AIOC USB reset): already in th9800_plugin.py
- Step 4 (Claude Code tmux): optional, could be a config flag
- Step 2 (Mumble GUI): headless=skip, irrelevant
- Goal: start.sh becomes `exec python3 radio_gateway.py` or eliminated entirely
