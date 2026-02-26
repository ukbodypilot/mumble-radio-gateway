# Claude Instructions — Mumble Radio Gateway

## Memory
At the end of every session, and whenever a significant bug or pattern is found, update the memory files:
- `/home/user/.claude/projects/-home-user-Downloads-mumble-radio-gateway/memory/MEMORY.md` — concise project overview (keep under 200 lines)
- `/home/user/.claude/projects/-home-user-Downloads-mumble-radio-gateway/memory/bugs.md` — bug history

Also mirror the updated files into `.claude/memory/` inside this project directory so they travel with the repo.

Read MEMORY.md at the start of each session to restore context.

### Moving to a new machine
Clone to `/home/user/Downloads/mumble-radio-gateway` (git clone, not zip download).
After cloning, sync memory with:
```
mkdir -p ~/.claude/projects/-home-user-Downloads-mumble-radio-gateway/memory/
cp .claude/memory/* ~/.claude/projects/-home-user-Downloads-mumble-radio-gateway/memory/
```
Claude Code's auto-memory path: `~/.claude/projects/-home-user-Downloads-mumble-radio-gateway/memory/`

## Project Rules
- `gateway_config.txt` is in `.gitignore` — NEVER commit it (repo is public; it contains stream keys and passwords)
- NEVER commit Broadcastify credentials (STREAM_PASSWORD, STREAM_MOUNT) or any other secrets
- To sync config between machines: copy the file manually (scp/rsync) — do NOT commit it
- Never commit the `bak/` directory
- Only commit when the user explicitly asks
- Never auto-push
