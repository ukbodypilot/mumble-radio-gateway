---
name: Never restart radio-gateway
description: Do not restart the radio-gateway systemd service — user will do it themselves
type: feedback
---

Never restart the radio-gateway service (`sudo systemctl restart radio-gateway.service`). The user will handle gateway restarts themselves.

**Why:** Gateway restarts disrupt running radio/audio processes and take ~45s. Previous restarts during development caused issues.

**How to apply:** When making changes to gateway code (web_server.py, gateway_core.py, etc.), just make the edits and tell the user the changes require a restart. Do not run the restart command.
