# Claude Instructions — Mumble Radio Gateway

## Memory
At the end of every session, and whenever a significant bug or pattern is found, update the memory files:
- `/home/user/.claude/projects/-home-user-Downloads-mumble-radio-gateway/memory/MEMORY.md` — concise project overview (keep under 200 lines)
- `/home/user/.claude/projects/-home-user-Downloads-mumble-radio-gateway/memory/bugs.md` — bug history

Read MEMORY.md at the start of each session to restore context.

## Project Rules
- Never commit `gateway_config.txt` — it contains local machine-specific settings
- Never commit the `bak/` directory
- Only commit when the user explicitly asks
- Never auto-push
