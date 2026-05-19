---
name: No self-scheduling hourly checks
description: Do not use ScheduleWakeup or CronCreate to schedule periodic fleet health checks
type: feedback
originSessionId: 072606d4-59dd-41e8-893d-0242b5623b3a
---
Do not self-schedule hourly (or any periodic) fleet health checks via ScheduleWakeup or CronCreate.

**Why:** manager_engine in the radio-gateway repo already handles scheduled fleet checks on a 6h cadence. Self-scheduling creates duplicate runs.

**How to apply:** When completing a fleet check, do NOT call ScheduleWakeup for the next check. Only run a check when explicitly invoked by the user or manager_engine.
