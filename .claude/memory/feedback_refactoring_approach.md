---
name: Refactoring approach that works
description: Lessons from successful v2.0 mixer refactoring — what worked, what to avoid
type: feedback
---

Plugin refactoring pattern that works:
1. Build plugin file absorbing existing classes (don't wrap — refactor)
2. Wire into gateway_core with backward compat aliases (self.old_name = self.plugin)
3. Update web_server endpoints to route through plugin
4. Test with gateway restart
5. Remove dead code only after confirming plugin works
6. Commit at each step

**Why:** Incremental approach with testing between each step catches issues early. The SIGSEGV from SDRPlugin was caught because we tested before cleanup.

**How to apply:**
- Never remove old code until new code is proven working
- Backward compat aliases (thin views, property proxies) let existing code work during transition
- Watch for: missing attributes on proxy objects, fork-safety with PyAudio, stale TCP connections
- Start hardware init BEFORE PyAudio (Pa_Initialize is not fork-safe)
- Add ptt_on()/ptt_off() convenience methods on plugins for gateway_core backward compat
