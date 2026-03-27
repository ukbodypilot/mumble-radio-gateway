---
name: Research before guessing field layouts
description: Always check existing codebase and documentation before guessing binary/protocol field layouts
type: feedback
---

When working with protocol field layouts (like D75 FO 21-field format), ALWAYS check existing code in the same codebase first — radio_automation.py already had the correct field indices. Also search for manufacturer documentation or reverse-engineering repos (LA3QMA, Hamlib) immediately rather than iterating on assumptions.

**Why:** Guessing field indices for the D75 FO command caused 4 layered bugs (wrong field count, wrong flags, wrong mode/shift, gateway crash) across 5 commits before the correct layout was found. The correct mapping existed in radio_automation.py the whole time.

**How to apply:** Before writing any protocol parsing code, (1) grep the codebase for existing parsers of the same format, (2) search the web for the protocol spec/reference, (3) instrument and verify with real data on the first attempt. Never iterate on guesses.
