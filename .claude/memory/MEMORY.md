# Radio Gateway — Project Memory

Radio-to-Mumble gateway with SDR, multiple radios, web UI, AI features. Python 3 on Arch Linux. Public repo at github.com/ukbodypilot/radio-gateway.

**Config:** `gateway_config.txt` (INI, .gitignored — contains secrets, NEVER commit). The repo's example template lives at `examples/gateway_config.txt`.
**Start:** `sudo systemctl restart radio-gateway.service`. NEVER restart it yourself — the user does this.
**Docs:** `docs/index.md` is the audience-grouped front door; see also `CHANGELOG.md` for version history.

## Codebase orientation

For full structure see `README.md`'s "Repository layout" section and `docs/index.md`. Only load specific module memory files when working on that module.

- `gateway_core.py` orchestrates `setup_audio()` via `gateway_setup.py` (extracted v3.7).
- `bus_manager.py` + `audio_bus.py` — bus mixer (Listen/Solo/Duplex/Simplex).
- Radio plugins: `th9800_plugin.py`, `sdr_plugin.py`.
- Link endpoint plugins (run on remote Pis OR on the gateway as loopback): `tools/d75_link_plugin.py`, `tools/ic7100_link_plugin.py`, `tools/kv4p_link_plugin.py`. Generic `tools/link_endpoint.py` is the runner. **CRITICAL** (see bugs_2026_05_19): sys.path must put `run/` before parent dir or stale copies of plugins shadow new deploys.
- **`process_supervisor.py`** owns every long-running child (kv4p loopbacks, cloudflared, pat, mDNS, direwolf via `packet_tnc.py`). `/processes` page is the dashboard. See [project_kv4p_endpoints.md](project_kv4p_endpoints.md).
- **`packet_tnc.py`** owns direwolf (moved out of AIOCPlugin in 2026-05-23 refactor).
- Multi-instance kv4p: `[kv4p.vhf]` / `[kv4p.uhf]` config sections; live state in gitignored `endpoints_state.json`.
- Transcription: `transcriber.py` (VAD + dispatcher) + `transcribe_engine.py` (Local + Remote engines) + `tools/transcribe_worker.py` (remote HTTP worker).
- Web: `web_server.py` + `web_routes_post.py` (shim re-exporting 8 per-domain modules: web_routes_transcribe/radio/audio/text/system/voice/manager/automation).

## Link endpoints fleet

All endpoints run as systemd **user** services on remote machines with `Restart=always`, code at `/home/user/link/`, deployed via scp from gateway.

| Name | IP | Plugin | Notes |
|------|----|--------|-------|
| celeron-ftm150 | .134 (mx) | aioc | FTM-150 + packet/Winlink TNC |
| d75-pi | .123 (DietPi Pi Zero 2W) | d75 | BT serial + SCO. **Heavy instrumentation as of 2026-05-19** — `[D75-WD]` per-tick + `[Serial-DROP]` on every disconnect. See D75 BT serial drop pattern below. |
| cm5-aioc | .144 (DHCP, may change) | aioc | Mobile HT endpoint |

To find current IPs: `grep "Endpoint registered" /home/user/Downloads/radio-gateway/logs/gateway-$(date +%Y-%m-%d).log` — the gateway log is authoritative.

### D75 BT serial drop pattern (characterised 2026-05-24 to 2026-05-26)

BT serial (CAT control) on d75-pi drops silently after 2-3+ hours of uptime. Observed across multiple code versions:

- **On reconnect**: BT serial connects reliably every time (gateway restart, code deploy, or endpoint restart).
- **Drop**: Silent — no log event in gateway logs. Only detectable via `endpoint_status`: `serial_connected:false`, `bluetooth:false`, `model:""` (empty string).
- **TCP audio unaffected**: Port unchanged when serial drops. `audio_connected:true` remains. Radio audio continues.
- **No auto-recovery**: Serial stays down indefinitely until the next reconnect event (TCP port change is the indicator of a true reconnect).
- **Duration by endpoint code version**:
  - `442f7407815769fa` — serial dropped at ~2h
  - `22e41770622f6bdf` — held 3h17m+ (longest session before restart)
  - `fb9897833f4ef6b4` — **14h35m+ CONFIRMED FIXED** — serial still up when gateway restarted 2026-05-19 ~08:18 (survived overnight quiet + active morning use with PTT bursts)
  - `a9099125f428ba48` — deployed 2026-05-19 ~08:36; serial up on new session (port :37898, ~46m at 16:00 check)
- **Detection in fleet checks**: If `serial_connected:false` and TCP port is UNCHANGED from last check → silent drop (not a reconnect). If port changed → reconnect event (serial will be up briefly).
- **Root cause**: Unknown. Likely BlueZ RFCOMM link timeout or D75 CAT serial idle disconnect. **fb9897833+ appears to have fixed it.**

## Transcription pool (current state)

Multi-machine via `transcribe_engine.py`. Mode is `pool`: local Moonshine engine on the gateway + remote Whisper worker on macmini (192.168.2.109:9800, EndeavourOS Linux). Length-based routing — clips under 10s go local, longer go remote. Worker has `WHISPER_CPU_THREADS=4` env override for thermal cap. Per-engine telemetry visible on `/transcribe` Workers row.

## Operational

- **Fleet Manager** scheduled Claude-driven monitoring — `manager_engine.py` reads `hourly.md`/`daily.md`/`SYSTEM_MANIFEST.md`, posts to `manager_reports.jsonl`. See [fleet-manager.md](../docs/fleet-manager.md) in repo.
- **Backup timer** every 6h: `scripts/radio-gateway-backup.timer` pushes operational docs + manager state to `gdrive:radio-gateway/manager/`. Secrets excluded.
- **Powersave service** — `radio-gateway-powersave.service` matches USB devices by VID:PID (was hardcoded path, broke when devices moved buses).

## Config safety (CRITICAL)

- `_CONFIG_LAYOUT` in web_server.py is the master list — Save wipes any key not listed there.
- NEVER use `replace_all=true` on `gateway_config.txt`.
- `gateway_config.txt` is gitignored. Repo is PUBLIC. NEVER commit.

## User preferences (active)

- Concise responses, no emojis (only if explicitly requested).
- Commits are explicit — never auto-commit, never auto-push. Branch operations need confirmation.
- Don't claim a fix "works" until something proves the new code path executed. A `print()` at function entry beats more guesses.
- Instrument before fixing audio/connection issues — measure, don't guess.
- Separate files for new features rather than expanding monoliths.
- Closed-loop on every control — confirm success/failure.
- NEVER restart radio-gateway.service — user does it themselves.

## Machine — user-optiplex3020 (Arch Linux)

- Intel i5-4590 4-core, 16 GB RAM. Python 3.14. sudo password: `user` (also useful for fleet hosts which use same).
- AIOC `/dev/ttyACM0`, KV4P `/dev/kv4p`, Relay `/dev/relay_radio`, GPS `/dev/gps`.
- Git: ukbodypilot.
- `dell-smm-hwmon` kernel module loaded for fan RPM telemetry.

## See also (load only when relevant)

**Recent (last few weeks):**
- [bugs_2026_05_19.md](bugs_2026_05_19.md) — D75 watchdog silent for 6 weeks: sys.path bug shadowed deploys. Read BEFORE touching the D75 plugin.
- [feedback_verify_deploys.md](feedback_verify_deploys.md) — prove new code is running before claiming "fixed".

**Bug history:** bugs.md, bugs_2026_03_30.md, bugs_2026_04_01.md, bugs_2026_04_05.md, bugs_2026_04_13.md
**Feedback (active rules):** feedback_config_safety.md, feedback_single_source_config.md, feedback_no_gateway_restart.md, feedback_instrument_not_guess.md, feedback_host_cpu_traps.md, feedback_no_self_scheduling.md
**References:** reference_host_tweaks.md, reference_gdrive_backup.md
**Project notes (load when touching that area):** project_kv4p_endpoints.md (2026-05-23 kv4p→endpoint refactor + ProcessSupervisor), project_ic7100_vfo_memory.md (2026-05-23 IC-7100 VFO+memory CI-V surface, UNVERIFIED against hardware), project_audio_quality.md, project_ftm150_endpoint.md, project_packet_radio.md, project_listen_bus_unify.md, project_loop_recorder.md, project_sdr_single_mode.md, project_internet_endpoints.md, project_d75_cleanup.md, project_rust_audio_core.md (deferred)
