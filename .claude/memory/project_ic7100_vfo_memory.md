# IC-7100 VFO + memory channel support (added 2026-05-23, UNVERIFIED)

The IC-7100 link plugin originally only knew about *the active* frequency —
no concept of VFO A/B, Main/Sub band, memory mode, or the 99 per-band
memory channels. Added the full surface, but the radio is **not wired to
MX yet** so none of these CI-V opcodes are bench-tested.

## What was added

### `tools/ic7100_link_plugin.py` — `CIVController` (the on-MX CI-V client)

State (set-only — tracked locally because the radio doesn't echo VFO/memory
in any periodically-polled command):

- `active_vfo: str` — 'A' or 'B'
- `active_band: str` — 'Main' or 'Sub'
- `memory_mode: bool`
- `memory_channel: int` — 1..99 typical

New methods (CI-V opcodes from the published IC-7100 manual; *not bench-
tested*, may need correction on first use):

| Method | CI-V |
|---|---|
| `select_vfo('A'/'B')` | `0x07 0x00` / `0x07 0x01` |
| `swap_vfo()` | `0x07 0xB0` |
| `equalize_vfo()` | `0x07 0xA0` |
| `select_band('Main'/'Sub')` | `0x07 0xD0` / `0x07 0xD1` |
| `enter_vfo_mode()` | `0x07 0x00` (re-selects VFO A, leaves memory) |
| `enter_memory_mode()` | `0x08` (no data — last channel) |
| `select_memory(ch)` | `0x08 [hi_bcd] [lo_bcd]` |
| `select_call_channel()` | `0x08 0xA0` |
| `write_memory()` | `0x09` — DESTRUCTIVE (overwrites current channel from VFO) |
| `clear_memory()` | `0x0A` — DESTRUCTIVE |
| `memory_to_vfo()` | `0x0B` |
| `read_memory(ch)` | `0x1A 0x00 [hi_bcd] [lo_bcd]` — returns raw channel data |

Status emission now includes `active_vfo`, `active_band`, `memory_mode`,
`memory_channel`.

### `IC7100Plugin.execute()` — new command actions

`vfo`, `vfo_swap`, `vfo_equalize`, `band`, `memory_mode`, `memory_select`,
`call_channel`, `memory_write`, `memory_clear`, `memory_to_vfo`,
`memory_read` — each forwards to the matching CIVController method.

### `web_routes_radio.handle_ic7100cmd` (gateway)

Forwards all 11 new cmds verbatim to the IC-7100 link endpoint.

### `gateway_mcp.py` — 9 new MCP tools

- `ic7100_vfo(action, vfo)` — action='select'/'swap'/'equalize'
- `ic7100_band(band)` — 'Main' / 'Sub'
- `ic7100_memory_recall(channel)` — switch to memory mode + select channel
- `ic7100_memory_mode(on)` — toggle memory vs VFO mode
- `ic7100_call_channel()`
- `ic7100_memory_store()` — DESTRUCTIVE write
- `ic7100_memory_clear()` — DESTRUCTIVE clear
- `ic7100_memory_to_vfo()` — copy current memory into VFO for editing
- `ic7100_memory_read(channel)` — read raw channel data without recalling

## What's NOT done (deferred until hardware verification)

1. **Read-back of VFO/band/memory state from the radio** — currently we
   only track what we set. If the operator changes VFO/memory via the
   front panel, the gateway has no idea. Once we know which CI-V reads
   actually return useful data, add periodic polling in `poll_settings()`.
2. **Memory channel content decoding** — `memory_read()` returns the raw
   hex blob. Need to decode into freq/mode/CTCSS/etc. per the IC-7100
   memory-data format in the manual.
3. **Web UI panel additions** — no buttons yet on `/ic7100`. MCP is the
   only way to drive these for now.
4. **TX-side verification** — the radio still has no 100 W dummy load.
   Don't TX-test until that arrives.

## Verification checklist (do once radio is wired to MX)

- [ ] `ic7100_vfo('select', 'B')` — radio switches to VFO B
- [ ] `ic7100_vfo('swap')` — A↔B exchange visible on front panel
- [ ] `ic7100_band('Sub')` — Sub band becomes active
- [ ] `ic7100_memory_recall(5)` — recalls channel 5
- [ ] `ic7100_memory_mode(False)` — returns to VFO mode
- [ ] `ic7100_memory_read(5)` — returns non-empty hex blob
- [ ] Operator changes VFO from front panel → cached `active_vfo` stays
      stale (confirms read-back gap)

If any of the opcodes return `FA` (NG) instead of `FB` (OK), the manual
reference was wrong — check the IC-7100 CI-V table again for that opcode.
