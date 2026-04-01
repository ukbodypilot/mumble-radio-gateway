---
name: v2.0 shipped
description: Radio gateway v2.0 shipped 2026-03-31 — bus routing, plugins, visual UI, full duplex remote audio
type: project
---

v2.0.0 released 2026-03-31. Merged 119 commits from v2.0-mixer to main. Tagged and published as GitHub release.

Key architecture: bus-based audio routing with Drawflow visual node editor. 4 bus types (Listen, Solo, Duplex, Simplex). 4 radio plugins (SDR, TH9800, D75, KV4P). All sinks gated by routing connections.

Major features: full duplex remote audio (ports 9600/9602), direct Icecast streaming, Mumble as routable source/sink, 44+ MCP tools, bus/sink/source mute controls.

28 bugs fixed. 7,200+ lines dead code removed. Full bug list in docs/mixer-v2-progress.md.

**How to apply:** v2.0 is the current stable release. Future work on v2.1 roadmap: clean up start.sh, installer/deployment, more plugins.
