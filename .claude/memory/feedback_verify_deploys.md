---
name: Prove new code is running before claiming a fix worked
description: User got tired of "Fixed!" claims about code that was never actually being executed due to a stale-copy deploy bug
type: feedback
---

When a fix doesn't change observable behaviour after deploying, do NOT propose another fix. **Prove the new code is running first.**

**Why:** Multiple weeks of "fixes" to the D75 watchdog were silently ignored because Python was loading a stale copy of the plugin from `/home/user/link/d75_link_plugin.py` while deploys went to `/home/user/link/run/d75_link_plugin.py`. The `sys.path` insert in `tools/link_endpoint.py` put the parent dir first, so the deployed file was shadowed. md5sums matched between local repo and the deploy target so the path looked correct — but Python wasn't loading from that path. Six weeks of confident "fixed!" claims with no actual change. The user finally demanded instrumentation and that's how we found it.

**How to apply:**

- A single `print("VERSION X reached", flush=True)` at function entry beats any number of confident-but-untested fixes.
- After deploying a change to a remote host, if the symptom recurs, the question is **not** "what else could be wrong with my fix?" — it's "is my code running at all?"
- For Python on a host you don't fully control, check: (a) all copies of the module name on disk (`find / -name foo.py`), (b) the `__pycache__/` directories that might hold stale .pyc, (c) `sys.path` order in the entry-point script.
- Heartbeat logs are cheap — every long-running watchdog / supervisor / scheduler thread should emit one every minute or so. A silent thread is indistinguishable from a healthy one until something fails.
- When you say "Fixed", you're claiming an observable behaviour change. If you haven't observed the change yourself (or seen a print/test confirm the new path executed), don't say it. Say "deployed, watching for proof."

Cross-ref: [bugs_2026_05_19.md](bugs_2026_05_19.md) is the full incident.
