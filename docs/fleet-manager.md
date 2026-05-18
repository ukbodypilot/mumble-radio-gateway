# Fleet Manager

A **document-driven autonomous monitoring and maintenance system**. Plain-English task lists are handed to a Claude Code session running on the gateway on a schedule. Claude does the checks (and the repairs), writes a structured JSON report back, and the engine reads it, surfaces it to the UI, and escalates to Telegram when it's bad.

The entire behaviour of the monitoring system is text you can read and edit in a browser. No code changes, no restarts.

## Why not hard-coded checks?

Traditional monitoring platforms — Nagios, Zabbix, custom cron scripts — are rigid. You define checks in code or YAML. Adding a new check means editing a config file, redeploying, and hoping the schema supports what you need. Thresholds are constants. Remediation steps are `if/else` branches. Edge cases need code.

The Fleet Manager inverts this. The "check" is a sentence:

> "Ping each known node. If one doesn't respond, scan the subnet, try to identify it by SSH fingerprint, and update the manifest with its new address."

Because the agent is Claude, it can:
- **Reason about ambiguous output** — a partial SSH response, an unexpected hostname, a service in a degraded-but-running state
- **Take corrective action** — restart a non-critical service, update a config file, amend a document
- **Escalate intelligently** — decide whether something is worth waking you up for, not just whether a threshold was crossed
- **Self-update its own task files** — if a node moves to a new IP, the daily task list and manifest are amended in place for next time

## Tasks are English. Let that sink in.

Most of the power of this system is hiding in how short the task lists are. Each "check" is a sentence or two of plain English; the agent figures out the steps, runs the commands, parses the output, and reports back. Examples taken verbatim from the live `daily.md`:

---

**English (1 line in the task doc):**
> Ping each known node. For any that doesn't respond, scan the whole subnet, identify each responding host by `hostname` and `uname -m`, and if you find the missing one update SYSTEM_MANIFEST.md with the new IP.

**What actually runs at 6 AM**: 4 parallel pings; a parallel sweep of `192.168.2.1-254` if any miss; SSH probes to candidates with `ConnectTimeout=3`; pattern matching on the responses (`DietPi` vs `RPI` vs `mx`); finding the right line in `SYSTEM_MANIFEST.md`; editing it with a note like `(updated 2026-05-17: was 192.168.2.141)`; saving the file. Cost in code: zero lines.

---

**English:**
> Pull the live transcription pool state from `/transcriptions`. Flag elevated if any worker is unreachable, the queue has more than 30 items or 60 seconds of audio, the CPU temp is above 90°C, or the average ratio is above 3x.

**What runs**: a `curl` against the gateway's status endpoint, JSON parsing, threshold checks on six fields, and a JSON report appended to `manager_reports.jsonl` with the right severity. If a threshold trips, a Telegram alert is fired and a red badge appears in the UI. The next time you want to add a threshold — say, swap RAM watch — you literally write the new sentence into the doc.

---

**English:**
> If `radio-gateway-backup.timer` is inactive, the last service run wasn't `success`, or the last `ExecMainExitTimestamp` is more than 24 hours old, flag elevated.

**What runs**: `systemctl is-active radio-gateway-backup.timer`, `systemctl show radio-gateway-backup.service -p Result -p ExecMainExitTimestamp --value`, parses both, applies the staleness rule, decides severity. The agent picked the right systemctl flags from the prose description.

---

**English (real anecdote from last week):**
> radio-gateway.py has grown to 6.9 GB RSS (42% of RAM) — well past the 4 GB alert threshold in the service contract. CPU load spiked to 2.83 at midnight before normalizing; the gateway requires a manual restart to reclaim memory.

That's not a check we wrote. That's a *finding* the agent produced after noticing the discrepancy with the manifest. It correlated the live `ps` output with the "≤4 GB RSS" service contract written in `SYSTEM_MANIFEST.md`, decided this was elevated, and explained the situation in English. A hard-coded threshold script would have only said `WARN: process RSS=6912 MB`.

---

### Adding a new check

To start monitoring a new condition, you open `hourly.md` in the browser and add a numbered line. That's it.

```
8. **Telegram bot health** — `systemctl --user is-active telegram-bot`; flag warning if not active. If `/tmp/telegram_bot_last_msg` is more than 24h old, flag elevated.
```

Save. Next run, the agent does it.

### Changing remediation policy

The hourly task list includes a `## Remediation` block. To make the agent attempt a Mumble restart when it's down (instead of just alerting):

Edit `hourly.md`. Find this:
```
Available actions:
- "restart-darkice" — Broadcastify stream encoder is down
- "restart-mumble" — Mumble server is down
```

Change it to:
```
Available actions:
- "restart-darkice" — Broadcastify stream encoder is down. Use this without hesitation.
- "restart-mumble" — Mumble server is down. Restart on the first miss; the service is idempotent.
```

The agent re-reads its instructions every run. No deploy, no restart.

### What this replaces

| If you used to do this... | You can now do this |
|---------------------------|---------------------|
| Write a 200-line monitoring script for a new service | Add one sentence to `hourly.md` |
| Maintain a list of fleet IPs in a shell script *and* in your head | Update the manifest; let the daily run discover misses |
| Cron a `systemctl restart` job and hope it doesn't loop forever | Write the policy as English; the agent decides when to restart |
| Read a 50-entry JSON log to figure out what changed overnight | Read one paragraph summary in the daily report |
| File a ticket when prod looks weird | The agent has already flagged it; the Telegram message is in your phone |

Nothing in the above section requires editing Python, restarting the gateway, or learning a DSL. The whole monitoring layer is hand-editable text.

## Architecture

```
   ┌─────────────────────── Gateway ───────────────────────┐
   │                                                        │
   │  manager_engine.py (scheduler thread)                  │
   │    │                                                    │
   │    ▼ every hour / once a day                            │
   │  Read hourly.md or daily.md                             │
   │    │                                                    │
   │    ▼ tmux send-keys → claude-gateway session            │
   │  Claude reads the task doc + SYSTEM_MANIFEST.md         │
   │    │                                                    │
   │    ▼ does the checks, optionally fixes things           │
   │  Appends one JSON line to manager_reports.jsonl         │
   │    │                                                    │
   │    ▼ matched by run_id                                  │
   │  Engine reads back the report                           │
   │    │                                                    │
   │    ├─▶ Severity elevated? → Telegram alert              │
   │    ├─▶ UI: badge on System nav, report list             │
   │    └─▶ fix field present? → execute the action          │
   │                                                          │
   └────────────────────────────────────────────────────────┘
```

## Files

| File | Role | Editable in UI |
|------|------|----------------|
| `SYSTEM_MANIFEST.md` | Authoritative fleet reference — hardware, roles, LAN/Tailscale IPs, service contracts, known quirks | ✓ |
| `hourly.md` | Tasks Claude runs every hour and reports on | ✓ |
| `daily.md` | Tasks Claude runs once a day (broader fleet sweep + housekeeping) | ✓ |
| `manager_state.json` | Engine state: enabled flag, last-run timestamps, daily run time, check interval, unread-alert flag | — |
| `manager_reports.jsonl` | Append-only history of every report Claude has written | — |

All five files are gitignored (LAN topology, credentials, history). They live only on the gateway. The [6-hourly gdrive backup timer](#backup) protects against disk loss.

## Scheduling

Two cadences, both running in the manager engine background thread.

**Hourly checks** fire on the top of each interval (configurable: 1, 2, 4, 6, 8, or 12 hours). Lightweight — services alive, stream connected, SDR processes, disk, memory, CPU, transcription pool.

**Daily checks** fire once per day at a configurable time (default 06:00). Heavier — fleet-wide ping sweep, SSH probes to each known node, subnet rescan when an IP is unreachable, log error rates, housekeeping (old log files, /tmp usage), gdrive backup health, transcription pool deep stats.

Each run embeds a `run_id` (e.g. `20260518-061500`) in the prompt. Claude is told to include that id in its JSONL report so the engine can match the response even if other activity occurred in the session between the send and the read.

## Report format

Each report is one JSON line appended to `manager_reports.jsonl`:

```json
{
  "ts": "2026-05-18T06:00:18Z",
  "task": "daily",
  "run_id": "20260518-060000",
  "severity": "ok",
  "summary": "Fleet nominal. All endpoints connected, stream OK, transcription pool healthy.",
  "findings": [
    "radio-gateway: active",
    "stream: connected",
    "transcription: pool mode, 2 workers ready, avg ratio 0.78x"
  ]
}
```

`findings` can be a list of strings (older format) **or** a nested dict (newer format with structured per-node data). The UI handles both — dicts render as pretty-printed JSON inside the expandable body.

| Severity | Meaning | Side effect |
|----------|---------|-------------|
| `ok` | Healthy | — |
| `warning` | Non-critical issue worth noting | UI only |
| `elevated` | Something is wrong and you probably want to know now | Telegram alert + red `●` badge on System menu |

## Remediation

Reports can include a `fix` field that the engine will execute automatically. Currently supported:

| Fix action | Effect |
|------------|--------|
| `restart-darkice` | Restart Broadcastify encoder |
| `restart-mumble` | Restart Mumble server |
| `restart-sdrplay` | Restart SDR API daemon |
| `restart-gateway` | Restart the gateway service itself (last resort) |

The task documents tell Claude when it's appropriate to include a fix. Only included when severity is `elevated` and the issue is unambiguous.

## Web UI

Lives at **System → Manager** in the web nav. Shows:

- **ON/OFF toggle** and daily run time
- **Check interval** (1-12 hours for hourly cadence)
- **Run Hourly Now / Run Daily Now** buttons
- **View** and **Edit** links for `SYSTEM_MANIFEST.md`, `hourly.md`, `daily.md` (Ctrl+S in the editor saves)
- **Persistent scrollable report list**, newest first, with expandable findings per entry
- **Red `●` indicator** on the System menu label when there are unacknowledged elevated reports — clears when the Manager page is opened

The report list reads from `manager_reports.jsonl` on disk and survives gateway restarts. The latest 100 entries are returned by the `/manager/reports` endpoint.

## Backup

A systemd timer rsyncs the operational docs + state to Google Drive every 6 hours, so a disk-loss event doesn't take the manifest, task docs, and report history with it.

- **Service**: `scripts/radio-gateway-backup.service` (oneshot, runs `rclone copy` for each file that exists)
- **Timer**: `scripts/radio-gateway-backup.timer` (every 6h after a 10-min boot delay, `Persistent=true` catches missed runs)
- **Files pushed**: `hourly.md`, `daily.md`, `SYSTEM_MANIFEST.md`, `manager_state.json`, `manager_reports.jsonl`, `.transcribe_settings.json`
- **NOT pushed**: `gateway_config.txt` (secrets — deliberately excluded)
- **Target**: `gdrive:radio-gateway/manager/`
- **Installed** by `scripts/install.sh` automatically when an `rclone gdrive:` remote is configured

**Restore on a fresh host:**
```bash
rclone copy gdrive:radio-gateway/manager/ /home/user/Downloads/radio-gateway/
```

The daily task list itself includes a backup-health check (timer active, last run succeeded, files present in gdrive) so the backup can't quietly stop working without you noticing.

## Use cases

**Fleet health at a glance** — every hour, Claude checks that all radio services are alive, the SDR is receiving, the stream is connected, transcription is keeping up, disk and memory are healthy. One-line summary in the report list; click to expand findings. If anything is wrong you get a Telegram message within minutes.

**Automatic node discovery** — if a DHCP node moves to a new address, the daily run detects the miss, sweeps the subnet, identifies the node by SSH fingerprint (or hostname/uname), and updates `SYSTEM_MANIFEST.md` in place. Next time you SSH in, the address in the manifest is correct.

**Self-healing services** — the task list instructs the agent to issue a `restart-...` fix action for non-critical services found down. The gateway doesn't need a watchdog for every individual service; the agent handles it.

**Docker stack monitoring** — the daily run SSHes to fleet nodes that run Docker, lists container states, and flags anything stopped. No Portainer webhook configuration needed.

**Transcription pool health** — the hourly check pulls the live pool snapshot and flags elevated state on unloaded engines, unreachable remotes, backlog (>30 items / >60s queued audio), CPU temp above 90°C, or avg ratio above 3x. Reports surface in the same feed as everything else.

**Extensible without code** — add a new monitored node by writing a section in the manifest. Add a new check by writing it as a sentence in `hourly.md`. Change what counts as elevated by editing the reporting instructions. None of this requires a gateway restart.

## Notes from operating it

A few things that came up while running this in production:

- **Findings shape drift** — older reports had `findings` as a flat list of strings; newer Claude task templates nest a dict. The UI renderer used to `.map()` unconditionally, which threw on dicts and silently blanked the whole report window. Now handled — list → bullets, dict → pretty JSON, per-report try/catch so one malformed entry can't blank the rest.

- **Watchdog needs a heartbeat** — silent watchdogs are indistinguishable from healthy ones. The transcription worker has a 60s heartbeat; the D75 plugin's BT watchdog now does too. The fleet manager's own scheduler thread is more visible — if it's broken, no reports get written, which the user will notice.

- **Don't fight the manifest** — if the agent updates the manifest with a new IP, that's authoritative. The agent saw the live state; you didn't. Diff the change before reverting if it looks suspicious.

- **Telegram for severity, UI for everything** — every report appears in the UI feed. Only elevated reports page you. Warning is the "I want to know later" band.
