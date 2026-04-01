---
name: Trust user observations
description: User reports are accurate — don't dismiss hardware/audio issues as "no signal" or user error
type: feedback
---

When the user says something is broken (e.g. "TH-9800 RX is broken"), trust their observation. Don't assume "no signal" or dismiss it. The user knows their hardware and tests with live signals.

**Why:** During v2.0 development, I dismissed a TH-9800 RX crash as "just no signal on the frequency" when the user confirmed the radio was actively receiving. The reader thread had actually crashed due to cleanup changes. The user corrected me.

**How to apply:** When the user reports a problem, investigate the code first. Only suggest "no signal" as a last resort after confirming the code path is working with measurement, not assumption.
