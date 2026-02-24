# Claude Instructions — Mumble Radio Gateway

## Memory
At the end of every session, and whenever a significant bug or pattern is found, update the memory files:
- `/home/user/.claude/projects/-home-user-Downloads-mumble-radio-gateway/memory/MEMORY.md` — concise project overview (keep under 200 lines)
- `/home/user/.claude/projects/-home-user-Downloads-mumble-radio-gateway/memory/bugs.md` — bug history

Also mirror the updated files into `.claude/memory/` inside this project directory so they travel with the repo.

Read MEMORY.md at the start of each session to restore context.

### Moving to a new machine (same path)
All machines use the same absolute path (`/home/user/Downloads/mumble-radio-gateway-main`), so Claude Code's auto-memory path is identical. After cloning, run the two commands in the sync section below and memory works automatically.

If the path ever differs, copy `.claude/memory/` to `~/.claude/projects/$(echo "$PWD" | sed 's|/|-|g; s|^-||')/memory/`.

## Project Rules
- `gateway_config.txt` IS committed — repo is private, full config including passwords syncs between machines
- Never commit the `bak/` directory
- Only commit when the user explicitly asks
- Never auto-push
