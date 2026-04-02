---
name: Single source of truth for config
description: GUI changes must write back to gateway_config.txt, not to separate JSON settings files
type: feedback
---

When radio plugins or features persist user-set values (frequency, tone, etc.), write them back to gateway_config.txt using targeted sed replacements — NOT to separate JSON settings files.

**Why:** The user flagged that having a separate persistence store (like ~/.config/radio-gateway/kv4p_settings.json) creates two sources of truth, which is the same divergence problem that caused the _CONFIG_LAYOUT wipe bug. Config file is the single master.

**How to apply:** Use `sed -i 's/^KEY = .*/KEY = VALUE/' gateway_config.txt` for targeted single-key updates. This is safe (anchored pattern, one key at a time). Never use replace_all or bulk rewrites. At startup, just read from config — no fallback to a settings file.
