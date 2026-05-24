# KV4P endpoint refactor + ProcessSupervisor (2026-05-23)

Major restructuring committed in `d2b3714` ("gateway: kv4p as endpoint plugin
+ ProcessSupervisor for daemons"). Verified live on this gateway through
three consecutive restart cycles.

## What moved

- **`kv4p_plugin.py` (root) → `tools/kv4p_link_plugin.py`** (git rename, 51%
  similarity preserved — use `git log --follow` to trace pre-refactor
  history). KV4P is now an *endpoint-hosted* plugin, joining
  d75_link_plugin / ic7100_link_plugin / aioc.
- Local kv4p connects via a **loopback endpoint** the gateway supervises
  (`link_endpoint.py --server 127.0.0.1:9700 --name kv4p-vhf --plugin kv4p`).
  Same code path as remote endpoints — no special-case for local.
- **Direwolf** is no longer spawned by `AIOCPlugin` (in
  `gateway_link.py`). It's now owned by a new top-level **`packet_tnc.py`**
  on the gateway. AIOCPlugin's only job in data mode is to release its
  ALSA + HID so direwolf can claim the AIOC device. `packet_radio.py`
  orchestrates the handoff: send `mode=data` to the endpoint, then
  `gw.packet_tnc.start(callsign=..., ...)` locally.

## Config schema (sectioned per instance)

Replaces the flat `[kv4p]` block:

```ini
[kv4p.vhf]
enable = true
port = /dev/kv4p
default_freq = 147.435000
ctcss_tx = 13
high_power = true
# ...
```

Legacy `[kv4p]` is auto-migrated to `[kv4p.vhf]` on first start.
One-shot, idempotent. Backup written to
`gateway_config.txt.legacy_kv4p.bak`. To add UHF: write a second
`[kv4p.uhf]` section with its own `port`/`default_freq` (likely needs a
udev rule for stable `/dev/kv4p-uhf` naming since both kv4ps share
VID/PID — distinguish by USB path).

## Runtime state — `endpoints_state.json`

Gateway-side JSON keyed by endpoint name (e.g. `kv4p-vhf`). On endpoint
connect, gateway reads saved freq/squelch/ctcss/bandwidth/power/boost
and replays them as commands so settings survive restarts. Updated
automatically from the endpoint's status pushes. **Gitignored** —
it's per-machine state, never commit.

## ProcessSupervisor (`process_supervisor.py`)

New shared infrastructure for managing long-running children. One
supervisor thread per entry; exponential backoff respawn; features:

- `persist_across_restart=True` — leave the child alive on
  `shutdown_all()` (used by cloudflared).
- `adopt_existing=callable` — pre-spawn hook that returns a PID to
  adopt instead of spawning fresh (also cloudflared).
- `stdout_handler=callable` — line-by-line callback (direwolf log
  forwarding, cloudflared URL capture).
- `run_as_user=<account>` — drops privs via setuid/setgid in
  preexec_fn (for opt-in mumble supervision).
- Env automatically gets `PYTHONUNBUFFERED=1` so Python-based children's
  prints reach the log file promptly (block-buffering bug otherwise
  means `logs/<name>.log` stays empty until 4 KB accumulates).

Migrated onto it in the same PR:

| Process | Notes |
|---------|-------|
| kv4p loopback endpoints | One supervised child per `[kv4p.*]` section |
| cloudflared | `systemd-run --user --scope --unit=cloudflared-tunnel` wrapper so the scope outlives the gateway cgroup — public URL persists across restarts (verified: same `wave-flights-designers-fire.trycloudflare.com` survived 3 restarts) |
| pat | `[pat, http]`, restart on death |
| mDNS publisher | `avahi-publish-service RadioGateway _radiogateway._tcp 9700` |
| direwolf | Spawned by `packet_tnc.py` when packet enters data mode |

**Opt-in** (default off — existing systemd path stays):
`SUPERVISE_DARKICE`, `SUPERVISE_MUMBLE`. When flipped on, supervisor
takes those services over (mumble uses `run_as_user='_mumble-server'`
with fallback to `'mumble-server'`).

## /processes page + /api/processes

New web page (`web_pages/processes.html`) lists every supervised
child: name, state, PID, uptime, restart count, adopted flag,
last_exit. Auto-refresh 5s. JSON at `/api/processes`. Single dashboard
for everything supervised.

## Back-compat proxy — `kv4p_endpoints.py`

`gw.kv4p_plugin` is now a **`_KV4PProxy`** that quacks like the old
in-core plugin and delegates to the first connected kv4p endpoint
(audio_level, tx_audio_level, execute, muted, ptt_active, ...). This
keeps ~400 legacy callers in web_routes_*, email_notifier,
text_commands, gateway_mcp working unchanged.

For per-instance access, use `kv4p_endpoints.list_endpoints(gw)` /
`find_endpoint(gw, instance)` / `execute(gw, cmd, instance=...)` /
`aggregate_status(gw)`. Status dict gains `kv4p_endpoints` mapping
`name → {audio_level, tx_audio_level, muted, connected, status}` for
new multi-instance consumers.

MCP `kv4p_command(cmd, args, instance='')` and `kv4p_status(instance='')`
gained an `instance` arg (empty → first connected).

## TX_RADIO values

`TX_RADIO` config accepts:
- `'kv4p'` — first connected kv4p endpoint (back-compat)
- `'kv4p-vhf'` / `'kv4p-uhf'` — specific instance
- `'th9800'`, `'d75'` — unchanged

## Routing UI — legacy node cleanup

Old routing graphs may carry stale `kv4p` source and `kv4p_tx` sink
nodes from before the refactor. After the commit, `web_server.py` no
longer injects those into the routing graph payload, so the legacy
nodes won't be auto-re-added. Delete them from the canvas and save —
they stay gone. New nodes are `kv4p_vhf` / `kv4p_vhf_tx` (and
`kv4p_uhf` / `kv4p_uhf_tx` when UHF arrives).

## Files deleted
- `kv4p_plugin.py` (renamed to `tools/`)
- `_ptt_kv4p()` in gateway_core
- `_sync_kv4p_plugin_processor()` (was a setter for in-core processor)
- AIOCPlugin's `_start_direwolf` / `_stop_direwolf` / `_direwolf_log_reader`
- bus_manager's hardcoded `source_id == 'kv4p'` / `sink_id == 'kv4p_tx'` branches

## Files added
- `process_supervisor.py`, `endpoints_state.py`, `kv4p_endpoints.py`,
  `packet_tnc.py`, `tools/kv4p_link_plugin.py`, `web_pages/processes.html`

## What's NOT verified yet (gated on hardware/opt-ins)

- **Dual kv4p (UHF)** — needs second hardware unit + udev rule for stable port
- **direwolf relocation** — needs a packet `data` mode switch with real
  packet round-trip via the new gateway-side direwolf
- **SUPERVISE_DARKICE / SUPERVISE_MUMBLE** — flags exist, both default off

When the UHF kv4p shows up, the path is: udev rule for `/dev/kv4p-uhf`,
add a `[kv4p.uhf]` section, restart. It'll appear as a sibling
endpoint in routing UI and `/processes`.
