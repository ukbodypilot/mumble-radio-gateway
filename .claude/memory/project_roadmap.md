---
name: v2.1 Roadmap
description: Post-2.0 roadmap items agreed with user on 2026-03-31
type: project
---

v2.0 shipped 2026-03-31. Next priorities:

1. **Clean up start.sh** — absorb startup logic into Python, reduce shell script complexity
2. **Installer / deployment** — proper install script, systemd service setup, multi-machine deployment
3. **More plugins** — extend the radio plugin system to additional hardware

**Why:** start.sh is a legacy shell script that manages process startup, USB resets, and service checks — most of this belongs in Python. The installer needs work for fresh deployments. Plugin system is proven (4 radios) and ready for expansion.

**How to apply:** These are the next development priorities after v2.0 stabilization.
