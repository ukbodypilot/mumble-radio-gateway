# Packet Radio + Winlink

The gateway uses **Direwolf** as a software TNC and **[Pat](https://getpat.io)** as the Winlink (B2F) client. The TNC runs on a remote AIOC-equipped radio (the FTM-150 endpoint), and the gateway switches that endpoint between **audio mode** (normal RX/TX) and **data mode** (Direwolf owns the AIOC for packet decode/encode).

## Architecture

```
Gateway                              FTM-150 Endpoint (mx machine, 192.168.2.134)
─────────                            ─────────────────────────────────────────
packet_radio.py                      AIOCPlugin (link_endpoint.py)
  ├ KISS TCP client                    ├ audio mode  → forwards RX audio to gateway
  ├ AGW client (port 8010)             ├ data  mode  → starts Direwolf subprocess
  ├ APRS decoder                       │                Direwolf reads audio, decodes
  ├ Pat CLI (compose / connect)        │                packets, exposes KISS:8001
  ├ Web UI (packet.html)               └ CM108 PTT via HID GPIO
  └ Winlink CMS directory fetcher
```

The gateway sends `{"cmd": "mode", "value": "data"}` via the link command channel when packet operations need to begin; the endpoint starts Direwolf, exposes the KISS port back via the link tunnel. When packet is idle, the endpoint goes back to audio mode and the radio returns to the normal listening path.

## APRS

Decodes the full APRS format zoo:

- Uncompressed and compressed position reports
- MIC-E
- Timestamped positions (`@`, `/`)
- Weather reports
- Status messages
- Objects
- Telemetry
- Digipeater paths (including `WIDE1-1`, `WIDE2-2`, `RELAY`, callsign-via lists)

Stations show up on a Leaflet map (`/packet` → APRS tab) with relay lines drawn from received packets. Station-info popovers include the raw packet text + decoded fields. Position is held for an hour (configurable) before fading.

You can also **send** APRS — position beacons + free-text messages — from the same tab.

## Winlink Email

Compose / receive email over VHF packet radio through Winlink CMS gateways. Uses [Pat](https://getpat.io) as the B2F protocol engine, controlled via CLI from `/packet` → Winlink tab.

- **Compose** — To / CC / Subject / Body form. Messages queued locally via `pat compose`.
- **Connect & Sync** — runs `pat connect ax25+agwpe:///CALLSIGN`, sending queued outbound and receiving inbound. The live connection log shows the B2F protocol exchange in real time.
- **Inbox / Outbox / Sent** — reads Pat's local mailbox at `~/.local/share/pat/mailbox`. Click a message for full body view.
- **Gateway proximity map** — fetched from Winlink CMS directory, filtered by your GPS location.

Tested gateway: **KM6RTE-12** on 144.970 MHz (Loma Ridge, Orange County, CA) at 1200 baud.

### Pat configuration

Pat config (with your Winlink password) lives at `~/.config/pat/config.json`. Not committed to the repo — provision it once on the gateway host.

## BBS terminal (planned)

A terminal UI for AX.25 BBS connect. Status: scaffolded; not finished.

## Configuration

```ini
[packet]
ENABLE_PACKET = true
PACKET_CALLSIGN = YOURCALL
PACKET_SSID = 1
PACKET_MODEM = 1200
PACKET_REMOTE_TNC = 192.168.2.134      # The FTM-150 endpoint host
PACKET_DIREWOLF_PATH = /usr/bin/direwolf
PACKET_KISS_PORT = 8001
PACKET_AGW_PORT = 8000
PACKET_APRS_COMMENT = Radio Gateway
PACKET_APRS_SYMBOL = /
PACKET_APRS_BEACON_INTERVAL = 600
PACKET_DIGIPEAT = true
```

See [config-reference.md](config-reference.md) for the full `[packet]` section.

## Web page

`/packet` has four tabs:

- **Status** — Direwolf log tail, KISS connection state, packet count, current mode (audio / data), AGW connection
- **APRS** — Leaflet map with station markers and relay paths; APRS send form
- **Winlink** — compose, inbox/outbox/sent, connect & sync with live B2F log
- **BBS** — terminal placeholder

## Source pointers

- [`packet_radio.py`](../packet_radio.py) — gateway-side packet manager (KISS client, APRS decoder, Pat wrapper, mode switching)
- [`web_routes_radio.py`](../web_routes_radio.py) — `handle_packet_cmd` + `_winlink_compose` / `_winlink_connect` helpers
- [`web_pages/packet.html`](../web_pages/packet.html) — `/packet` page
- [`tools/link_endpoint.py`](../tools/link_endpoint.py) — endpoint-side mode switching + Direwolf launcher
