---
name: UI redesign stage plan (branch ui-redesign)
description: Multi-stage web UI redesign on ui-redesign branch — phosphor/instrument aesthetic, CSS-var driven, commit per stage
type: project
---

Staged redesign of web_pages/ on branch `ui-redesign`, phosphor/instrument-panel aesthetic.
Rule: one stage = one commit; user browser-tests between stages.

**Why:** user asked to "do everything but do it in stages so we don't compound issues"; code must stay human-maintainable (no "millions of lines of manual CSS / AI insanity").

**How to apply:** keep each stage focused + committable; prefer extending common.css + semantic classes over per-page styling; convert every hardcoded hex to `--t-*` CSS vars so themes actually propagate.

Stages:
1. ✅ Foundation: common.css palette, JetBrainsMono woff2, THEMES['blue'] retune, shell.html rewrite
2. ✅ Shell/audio bars: semantic sb-* classes, class-based button state transitions
3. ✅ Dashboard: `.panel`/`.hero` classes, 8 inline-styled divs swapped, ~280 lines dead audio JS removed
4. ⏳ Routing page (Drawflow node editor — worst density issue, 0.45–0.61rem fonts)
5. ⏳ SDR + controls + radio pages (sdr/controls/radio/d75/kv4p)
6. ⏳ Remaining pages (monitor, recordings, recorder, transcribe, packet, logs, gps, repeaters, aircraft, telegram, gdrive, config)
7. ⏳ Theme picker: extend THEMES dict in web_server.py with ok/warn/err/text/text-dim/panel-hi/border-hi vars; retune legacy themes (red/green/amber/teal/pink/purple) to phosphor sensibility; add UI picker
