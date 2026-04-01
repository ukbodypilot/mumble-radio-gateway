---
name: json_mod gotcha
description: gateway_core.py imports json as json_mod — using bare json causes silent NameError
type: feedback
---

In gateway_core.py, `json` is imported as `json_mod`. Using bare `json.load()` or `json.dumps()` causes a NameError that gets silently caught by broad except blocks. This caused ALL sources to fall through to BusManager during v2.0 development.

**Why:** Python 3.14's stricter scoping made this worse — local imports can shadow module-level ones. The bug was extremely hard to find because the except clause swallowed the error silently.

**How to apply:** Always use `json_mod` in gateway_core.py. When writing new code in that file, grep for the import alias first. Same pattern applies to other aliased imports in the codebase.
