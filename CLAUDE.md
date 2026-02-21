# Claude Instructions — Mumble Radio Gateway

## Memory
At the end of every session, and whenever a significant bug or pattern is found, update the memory files:
- `/home/user/.claude/projects/-home-user-Downloads-mumble-radio-gateway/memory/MEMORY.md` — concise project overview (keep under 200 lines)
- `/home/user/.claude/projects/-home-user-Downloads-mumble-radio-gateway/memory/bugs.md` — bug history

Also mirror the updated files into `.claude/memory/` inside this project directory so they travel with the repo.

Read MEMORY.md at the start of each session to restore context.

### Moving to a new machine
The memory files are included in `.claude/memory/` in this repo. On a new machine, after cloning/unzipping, restore them:

```bash
# Adjust <project-path> to match where the project lives on the new machine
DEST=~/.claude/projects/$(echo "$PWD" | sed 's|/|-|g; s|^-||')/memory
mkdir -p "$DEST"
cp .claude/memory/MEMORY.md "$DEST/"
cp .claude/memory/bugs.md "$DEST/"
```

Or manually copy `.claude/memory/` to the path that Claude Code derives from the project's absolute path.

## Project Rules
- Never commit `gateway_config.txt` — it contains local machine-specific settings
- Never commit the `bak/` directory
- Only commit when the user explicitly asks
- Never auto-push
