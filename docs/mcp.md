# MCP Server

`gateway_mcp.py` is a stdio-based [MCP](https://modelcontextprotocol.io) server. It gives Claude (or any MCP-compatible AI client) full control of the gateway via its HTTP API. 95+ tools across status, radios, routing, transcription, packet, fleet management, etc.

The Telegram bot, the Fleet Manager's hourly/daily Claude runs, and the voice control page all use these tools internally — the gateway itself reads its own state through this surface.

## How it's used

In Claude Code or any MCP client, the server is registered in `.mcp.json` at the project root. Enable in Claude Code settings:

```json
{ "enableAllProjectMcpServers": true }
```

The MCP server is launched as a child process of the MCP client (Claude Code, voice page, Telegram bot). Restarting the radio-gateway service does **not** restart the MCP server — they're separate processes connected by HTTP.

## Tool categories

| Category | Sample tools |
|----------|-------------|
| **Status** | `gateway_status`, `sdr_status`, `cat_status`, `system_info`, `d75_status`, `kv4p_status`, `telegram_status`, `gps_status`, `cloudflare_status` |
| **Radio control** | `radio_ptt`, `radio_tts`, `radio_cw`, `radio_ai_announce`, `radio_set_tx`, `radio_frequency`, `d75_command`, `d75_frequency`, `d75_memscan`, `kv4p_command` |
| **SDR** | `sdr_tune`, `sdr_single_tune`, `sdr_restart`, `sdr_set_mode`, `sdr_add_channel`, `sdr_remove_channel` |
| **Routing** | `routing_status`, `routing_levels`, `routing_connect`, `routing_disconnect`, `bus_create`, `bus_delete`, `bus_mute`, `bus_rename`, `sink_mute`, `bus_toggle_processing`, `set_gain`, `speaker_mode` |
| **Loop recorder** | `loop_recorder_status`, `loop_recorder_toggle`, `loop_recorder_activity`, `loop_recorder_export`, `loop_recorder_delete_all`, `loop_playback_control` |
| **Transcription** | `transcription_status`, `transcription_config`, `transcription_log_query`, `transcription_log_recent` |
| **Repeaters** | `nearby_repeaters`, `repeater_info`, `repeater_tune`, `repeater_refresh` |
| **Packet / Winlink** | `packet_status`, `packet_mode`, `packet_decoded`, `packet_aprs_stations`, `packet_send_aprs`, `winlink_compose`, `winlink_connect`, `winlink_gateways`, `winlink_messages`, `winlink_read` |
| **Gateway link** | `link_endpoint_status`, `link_endpoint_command`, `endpoint_ping`, `endpoint_reboot`, `endpoint_battery`, `endpoint_version`, `endpoint_ssh` |
| **Automation** | `automation_status`, `automation_history`, `automation_reload`, `automation_trigger`, `automation_scheme_read`, `automation_scheme_edit` |
| **Recordings** | `recordings_list`, `recordings_delete`, `recording_playback` |
| **GDrive** | `gdrive_status`, `gdrive_list_files`, `gdrive_publish_tunnel` |
| **System** | `gateway_logs`, `gateway_restart`, `gateway_key`, `audio_trace_toggle`, `stream_trace_toggle`, `config_read`, `process_control` |
| **Telegram** | `telegram_reply`, `telegram_status` |

Full canonical list: each tool is registered in `gateway_mcp.py` with its parameter schema. Search for `@mcp_tool(` to find them all.

## Architecture

The gateway exposes its HTTP API on `:8080` (web UI port). The MCP server is a thin shim that:

1. Receives tool calls from the MCP client over stdio
2. Translates each to an HTTP call against `localhost:8080`
3. Returns the parsed JSON response back over stdio

This split lets multiple MCP clients (Claude Code, Telegram bot, voice page, Fleet Manager) all talk to the same gateway concurrently — they each spawn their own `gateway_mcp.py` instance but all converge on the single HTTP API.

## Adding a tool

```python
@mcp_tool(
    name='my_new_tool',
    description='What this does',
    parameters={
        'arg1': {'type': 'string', 'description': '…'},
    }
)
def _impl(arg1: str):
    return http_post('/some/endpoint', {'arg1': arg1})
```

The tool's `description` becomes what the LLM reads when deciding whether to call it. Be specific about side effects ("turns on PTT — radio will transmit") vs read-only state queries.

## Source pointers

- [`gateway_mcp.py`](../gateway_mcp.py) — the server
- [`.mcp.json`](../.mcp.json) — Claude Code registration
- HTTP endpoints called by tools live in `web_routes_get.py` and `web_routes_*.py` (per-domain handlers)
