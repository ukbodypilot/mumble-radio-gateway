---
name: Config file safety
description: gateway_config.txt gets damaged easily — never use replace_all or cat append carelessly
type: feedback
---

gateway_config.txt is fragile and not in git. It has been damaged multiple times:
- Edit tool `replace_all` on boolean values changed unrelated settings
- `cat >>` append to add sections can interact badly with restarts
- Process crashes during write can truncate the file

**Why:** Config is the single source of truth for 40+ boolean settings. Damage is silent — gateway starts with wrong defaults and things break subtly (Mumble GUI opens, SDRs don't start, etc.)

**How to apply:**
- NEVER use `replace_all=true` on config file values
- Use `sed -i` with `^KEY = value` anchored patterns for targeted changes
- Keep a backup of known-good config: `cp gateway_config.txt gateway_config.txt.bak`
- After any config edit, verify critical values: HEADLESS_MODE, ENABLE_WEB_CONFIG, ENABLE_D75, etc.
- The user-approved defaults list is in memory — reference it when restoring
