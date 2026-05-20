"""POST handlers for radio plugins: D75 / KV4P / CAT / SDR / Darkice / Packet / Link."""

"""POST route handlers extracted from web_server.py."""

import json as json_mod
import os
import time
import subprocess
import threading as _thr

from audio_sources import generate_cw_pcm
from cat_client import RadioCATClient


# IC-7100 HF / DSP / FM-squelch panel commands (Phase C). The /ic7100 page
# sends a well-formed JSON payload for each, so handle_ic7100cmd forwards
# them verbatim to the endpoint, which validates in IC7100Plugin.execute().
_IC7100_HF_CMDS = frozenset({
    'split', 'rit', 'xit', 'agc', 'nb', 'nr', 'preamp', 'atten',
    'if_shift', 'filter', 'squelch', 'squelch_type', 'dtcs',
})


def handle_d75cmd(handler, parent):
    """POST /d75cmd"""
    length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(length).decode('utf-8')
    result = {'ok': False}
    try:
        data = json_mod.loads(body)
        cmd = data.get('cmd', '')
        args = data.get('args', '')
        gw = parent.gateway

        # Check for D75 link endpoint first (new path)
        _link = getattr(gw, 'link_server', None) if gw else None
        _d75_ep = None
        if _link:
            for ep_name, ep_src in gw.link_endpoints.items():
                if getattr(ep_src, 'plugin_type', None) == 'd75':
                    _d75_ep = ep_name
                    break

        if _d75_ep and _link:
            # Route through Gateway Link endpoint
            if cmd == 'cat':
                _link.send_command_to(_d75_ep, {'cmd': 'cat', 'raw': args})
                result = {'ok': True, 'response': f'sent via link endpoint'}
            elif cmd == 'ptt':
                # Toggle: check current state from cached endpoint status
                _ptt_now = getattr(gw, '_link_ptt_active', {}).get(_d75_ep, False)
                _link.send_command_to(_d75_ep, {'cmd': 'ptt', 'state': not _ptt_now})
                result = {'ok': True, 'response': f'PTT {"off" if _ptt_now else "on"} via link'}
            elif cmd == 'freq':
                _link.send_command_to(_d75_ep, {'cmd': 'frequency', 'freq': args})
                result = {'ok': True, 'response': f'freq set via link'}
            elif cmd == 'status':
                _link.send_command_to(_d75_ep, {'cmd': 'status'})
                result = {'ok': True, 'response': 'status requested'}
            elif cmd in ('btstart', 'btstop', 'reconnect', 'start_service'):
                # BT lifecycle managed by the remote endpoint — not applicable
                result = {'ok': True, 'response': f'{cmd}: managed by link endpoint'}
            elif cmd == 'mute':
                # Mute the link audio source on the gateway side
                _link_src = None
                for _src_name, _src in getattr(gw, 'link_endpoints', {}).items():
                    if getattr(_src, 'plugin_type', None) == 'd75':
                        _link_src = _src
                        break
                if _link_src:
                    _link_src.muted = not _link_src.muted
                    result = {'ok': True, 'muted': _link_src.muted}
                else:
                    result = {'ok': True, 'response': 'mute toggled'}
            elif cmd == 'vol':
                try:
                    pct = int(args)
                    pct = max(0, min(500, pct))
                    _link.send_command_to(_d75_ep, {'cmd': 'rx_gain', 'gain': pct / 100.0})
                    result = {'ok': True, 'response': f'boost={pct}%'}
                except (ValueError, TypeError):
                    result = {'ok': False, 'error': 'usage: vol 0-500'}
            elif cmd in ('tone', 'shift', 'offset'):
                # High-level FO-modify commands
                _link.send_command_to(_d75_ep, {'cmd': cmd, 'raw': args})
                result = {'ok': True, 'response': f'{cmd} sent via link'}
            else:
                # Pass through as raw CAT
                _link.send_command_to(_d75_ep, {'cmd': 'cat', 'raw': f'{cmd} {args}'.strip()})
                result = {'ok': True, 'response': f'sent via link'}

        else:
            result = {'ok': False, 'error': 'D75 not connected (no link endpoint or plugin)'}
    except Exception as e:
        result = {'ok': False, 'error': str(e)}
    try:
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json_mod.dumps(result).encode('utf-8'))
    except BrokenPipeError:
        pass
    return

def handle_ic7100cmd(handler, parent):
    """POST /ic7100cmd — dispatch IC-7100 panel commands to the link endpoint."""
    length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(length).decode('utf-8')
    result = {'ok': False}
    try:
        data = json_mod.loads(body)
        cmd = data.get('cmd', '')
        args = data.get('args', '')
        gw = parent.gateway
        _link = getattr(gw, 'link_server', None) if gw else None

        # Locate the IC-7100 endpoint
        _ep = None
        if _link:
            for ep_name, ep_src in gw.link_endpoints.items():
                if getattr(ep_src, 'plugin_type', None) == 'ic7100':
                    _ep = ep_name
                    break

        if not (_ep and _link):
            result = {'ok': False, 'error': 'IC-7100 not connected'}
        elif cmd == 'ptt':
            # Caller may send explicit state, else toggle from cached
            if 'state' in data:
                _new = bool(data.get('state'))
            else:
                _ptt_now = getattr(gw, '_link_ptt_active', {}).get(_ep, False)
                _new = not _ptt_now
            _link.send_command_to(_ep, {'cmd': 'ptt', 'state': _new})
            result = {'ok': True, 'ptt': _new}
        elif cmd == 'freq':
            # Accept MHz as string or float
            try:
                _mhz = float(args)
            except (TypeError, ValueError):
                result = {'ok': False, 'error': 'freq must be a number (MHz)'}
            else:
                _link.send_command_to(_ep, {'cmd': 'frequency', 'freq': _mhz})
                result = {'ok': True, 'freq': _mhz}
        elif cmd == 'mode':
            _mode = data.get('mode', args)
            _filter = data.get('filter')
            _payload = {'cmd': 'mode', 'mode': _mode}
            if _filter is not None:
                _payload['filter'] = int(_filter)
            _link.send_command_to(_ep, _payload)
            result = {'ok': True, 'mode': _mode}
        elif cmd == 'ctcss':
            _payload = {'cmd': 'ctcss'}
            for _k in ('tx_hz', 'rx_hz', 'tx_on', 'rx_on'):
                if _k in data:
                    _payload[_k] = data[_k]
            _link.send_command_to(_ep, _payload)
            result = {'ok': True}
        elif cmd == 'tx_interlock':
            # TX antenna-port safety interlock — enable/disable per port.
            _payload = {'cmd': 'tx_interlock'}
            for _k in ('hf', 'vu'):
                if _k in data:
                    _payload[_k] = bool(data[_k])
            _link.send_command_to(_ep, _payload)
            result = {'ok': True}
        elif cmd in ('rx_gain', 'tx_gain'):
            try:
                _db = float(data.get('db', args))
            except (TypeError, ValueError):
                result = {'ok': False, 'error': f'{cmd} db must be a number'}
            else:
                _link.send_command_to(_ep, {'cmd': cmd, 'db': _db})
                result = {'ok': True, 'db': _db}
        elif cmd == 'mute':
            _src = gw.link_endpoints.get(_ep)
            if _src is not None:
                _src.muted = not _src.muted
                result = {'ok': True, 'muted': _src.muted}
            else:
                result = {'ok': False, 'error': 'audio source missing'}
        elif cmd == 'vol':
            # RX listening volume — gateway-side audio boost on the link
            # source (0-500%, 100 = unity). Applied in audio_sources.py.
            _src = gw.link_endpoints.get(_ep)
            if _src is None:
                result = {'ok': False, 'error': 'audio source missing'}
            else:
                try:
                    pct = max(0, min(500, int(data.get('value', 100))))
                except (ValueError, TypeError):
                    result = {'ok': False, 'error': 'vol must be 0-500'}
                else:
                    _src.audio_boost = pct / 100.0
                    result = {'ok': True, 'audio_boost': pct}
        elif cmd == 'cat':
            # Raw CI-V passthrough — args expected as hex string
            _link.send_command_to(_ep, {'cmd': 'cat', 'raw': args})
            result = {'ok': True, 'response': 'cat sent'}
        elif cmd == 'status':
            _link.send_command_to(_ep, {'cmd': 'status'})
            result = {'ok': True, 'response': 'status requested'}
        elif cmd in _IC7100_HF_CMDS:
            # HF / DSP / FM-squelch panel — the GUI payload already carries
            # the right keys (on/hz/mode/level/stage/idx/pct/type/code/...);
            # forward it verbatim. IC7100Plugin.execute() reads what it needs.
            _link.send_command_to(_ep, data)
            result = {'ok': True}
        else:
            result = {'ok': False, 'error': f'unknown cmd: {cmd}'}
    except Exception as e:
        result = {'ok': False, 'error': str(e)}
    try:
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json_mod.dumps(result).encode('utf-8'))
    except BrokenPipeError:
        pass
    return


def handle_kv4pcmd(handler, parent):
    """POST /kv4pcmd"""
    length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(length).decode('utf-8')
    result = {'ok': False, 'error': 'KV4P plugin not available'}
    try:
        data = json_mod.loads(body)
        _kv4p_p = getattr(parent.gateway, 'kv4p_plugin', None) if parent.gateway else None
        if _kv4p_p:
            cmd = data.get('cmd', '')
            args = data.get('args', '')
            # Map web UI command format to plugin execute format
            if cmd == 'freq':
                result = _kv4p_p.execute({'cmd': 'freq', 'frequency': float(args)})
            elif cmd == 'txfreq':
                result = _kv4p_p.execute({'cmd': 'freq', 'frequency': _kv4p_p._frequency, 'tx_frequency': float(args)})
            elif cmd == 'squelch':
                result = _kv4p_p.execute({'cmd': 'squelch', 'level': int(args)})
            elif cmd == 'ctcss':
                _ctcss_hz = ["67.0","71.9","74.4","77.0","79.7","82.5","85.4","88.5",
                    "91.5","94.8","97.4","100.0","103.5","107.2","110.9","114.8","118.8","123.0",
                    "127.3","131.8","136.5","141.3","146.2","151.4","156.7","162.2","167.9",
                    "173.8","179.9","186.2","192.8","203.5","210.7","218.1","225.7","233.6","241.8","250.3"]
                def _hz_to_code(s):
                    s = str(s).strip()
                    if s == '0' or s.lower() in ('none', ''):
                        return 0
                    try:
                        return _ctcss_hz.index(s) + 1
                    except ValueError:
                        return int(float(s))
                parts = str(args).split()
                tx = _hz_to_code(parts[0]) if len(parts) > 0 else 0
                rx = _hz_to_code(parts[1]) if len(parts) > 1 else tx
                result = _kv4p_p.execute({'cmd': 'ctcss', 'tx': tx, 'rx': rx})
            elif cmd == 'bandwidth':
                result = _kv4p_p.execute({'cmd': 'bandwidth', 'wide': str(args).lower() in ('1', 'wide', 'true')})
            elif cmd == 'power':
                result = _kv4p_p.execute({'cmd': 'power', 'high': str(args).lower() in ('1', 'high', 'true', 'h')})
            elif cmd == 'ptt':
                result = _kv4p_p.execute({'cmd': 'ptt', 'state': not _kv4p_p._transmitting})
            elif cmd == 'smeter':
                if _kv4p_p._radio:
                    _kv4p_p._radio.enable_smeter(str(args).lower() in ('1', 'true', 'on', ''))
                result = {'ok': True}
            elif cmd == 'vol':
                result = _kv4p_p.execute({'cmd': 'boost', 'value': int(args) / 100.0})
            elif cmd == 'testtone':
                result = _kv4p_p.execute({'cmd': 'testtone', 'frequency': float(args) if args else 440})
            elif cmd == 'record':
                result = _kv4p_p.execute({'cmd': 'capture'})
            elif cmd == 'reconnect':
                result = _kv4p_p.execute({'cmd': 'reconnect'})
            else:
                result = _kv4p_p.execute(data)
        elif data.get('cmd') == 'reconnect' and parent.gateway:
            # Reconnect even when plugin is None — recreate it
            try:
                from kv4p_plugin import KV4PPlugin
                parent.gateway.kv4p_plugin = KV4PPlugin()
                if parent.gateway.kv4p_plugin.setup(parent.gateway.config):
                    result = {'ok': True, 'response': 'Reconnected'}
                else:
                    parent.gateway.kv4p_plugin = None
                    result = {'ok': False, 'error': 'Reconnect failed'}
            except Exception as e:
                result = {'ok': False, 'error': str(e)}
    except Exception as e:
        result = {'ok': False, 'error': str(e)}
    try:
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json_mod.dumps(result).encode('utf-8'))
    except BrokenPipeError:
        pass
    return

def handle_linkcmd(handler, parent):
    """POST /linkcmd"""
    length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(length).decode('utf-8')
    result = {'ok': False}
    try:
        data = json_mod.loads(body)
        gw = parent.gateway
        endpoint_name = data.get('endpoint', '')
        if not endpoint_name:
            result = {'ok': False, 'error': 'missing endpoint name'}
        elif not gw or not gw.link_server:
            result = {'ok': False, 'error': 'link server not running'}
        elif endpoint_name not in gw.link_endpoints:
            result = {'ok': False, 'error': f'endpoint not connected: {endpoint_name}'}
        else:
            cmd_name = data.get('cmd', '')
            # Mark that we're waiting for a fresh status (don't clear existing)
            _prev_status = gw._link_last_status.get(endpoint_name, {})
            _prev_uptime = _prev_status.get('uptime', -1)
            gw.link_server.send_command_to(endpoint_name, data)
            if cmd_name == 'status':
                import time as _time
                for _ in range(10):  # 1 second max
                    _time.sleep(0.1)
                    _cur = gw._link_last_status.get(endpoint_name, {})
                    if _cur and _cur.get('uptime', -1) != _prev_uptime:
                        break  # new status arrived
                result = {'ok': True, 'status': gw._link_last_status.get(endpoint_name, {})}
            else:
                result = {'ok': True, 'sent': data}
    except Exception as e:
        result = {'ok': False, 'error': str(e)}
    try:
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json_mod.dumps(result).encode('utf-8'))
    except BrokenPipeError:
        pass
    return

def handle_catcmd(handler, parent):
    """POST /catcmd"""
    length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(length).decode('utf-8')
    result = {'ok': False}
    try:
        data = json_mod.loads(body)
        cmd = data.get('cmd', '')
        gw = parent.gateway
        if cmd == 'SET_TX_RADIO' and gw:
            radio = data.get('radio', '').lower()
            if radio in ('th9800', 'd75', 'kv4p'):
                gw.config.TX_RADIO = radio
                result = {'ok': True, 'radio': radio}
            else:
                result = {'ok': False, 'error': 'unknown radio'}
        elif cmd == 'GET_TX_RADIO' and gw:
            result = {'ok': True, 'radio': str(getattr(gw.config, 'TX_RADIO', 'th9800')).lower()}
        elif cmd == 'CAT_DISCONNECT' and gw and gw.cat_client:
            gw.cat_client._stop = True
            gw.cat_client.close()
            gw.cat_client = None
            print("\n  [CAT] Disconnected via web")
            result = {'ok': True}
        elif cmd == 'SERIAL_DISCONNECT' and gw and gw.cat_client:
            gw.cat_client._pause_drain()
            try:
                resp = gw.cat_client._send_cmd("!serial disconnect")
            finally:
                gw.cat_client._drain_paused = False
            if resp and 'disconnected' in resp:
                gw.cat_client._serial_connected = False
            print(f"\n  [CAT] Serial disconnect: {resp}")
            result = {'ok': resp and 'disconnected' in resp, 'status': resp or ''}
        elif cmd == 'SERIAL_CONNECT' and gw and gw.cat_client:
            # serial connect takes ~4s (startup sequence with sleeps)
            cat = gw.cat_client
            cat._pause_drain()
            try:
                with cat._sock_lock:
                    cat._sock.sendall(b"!serial connect\n")
                    cat._last_activity = time.monotonic()
                    resp = cat._recv_line(timeout=10.0)
            finally:
                cat._drain_paused = False
            ok = resp and 'connected' in resp
            already = ok and 'already' in resp
            if ok:
                cat._serial_connected = True
                try:
                    cat.set_rts(True)
                except Exception:
                    pass

            print(f"\n  [CAT] Serial connect: {resp}")
            result = {'ok': ok, 'status': resp or ''}
        elif cmd == 'SETUP_RADIO' and gw and gw.cat_client:
            # Run setup_radio (channels, volume, power) from config
            cat = gw.cat_client
            try:
                cat.setup_radio(gw.config)
                result = {'ok': True, 'status': 'setup complete'}
            except Exception as e:
                print(f"\n  [CAT] Setup error: {e}")
                result = {'ok': False, 'status': str(e)}
        elif cmd == 'SERIAL_STATUS' and gw and gw.cat_client:
            resp = gw.cat_client._send_cmd("!serial status")
            result = {'ok': True, 'status': resp or 'unknown'}
        elif cmd == 'MIC_PTT' and gw:
            # Key/unkey TH-9800 via configured PTT_METHOD, regardless of TX_RADIO
            gw._web_th9800_ptt = not getattr(gw, '_web_th9800_ptt', False)
            state = gw._web_th9800_ptt
            method = str(getattr(gw.config, 'PTT_METHOD', 'aioc')).lower()
            if method == 'relay':
                gw._ptt_relay(state)
            elif method == 'software':
                gw._ptt_software(state)
            else:
                gw._ptt_aioc(state)
            result = {'ok': True}
        elif cmd == 'CAT_RECONNECT' and gw:
            if gw.cat_client:
                ok = gw.cat_client.reconnect()
                print(f"\n  [CAT] Reconnected via web: {'ok' if ok else 'failed'}")
                result = {'ok': ok}
            else:
                # Create fresh client
                host = str(getattr(gw.config, 'CAT_HOST', '127.0.0.1'))
                port = int(getattr(gw.config, 'CAT_PORT', 9800))
                pw = str(getattr(gw.config, 'CAT_PASSWORD', '') or '')
                cat = RadioCATClient(host, port, pw)
                if cat.connect():
                    cat.start_background_drain()
                    gw.cat_client = cat
                    print("\n  [CAT] Connected via web")
                    result = {'ok': True}
                else:
                    print("\n  [CAT] Connect failed via web")
                    result = {'ok': False, 'error': 'Connection failed'}
        elif cmd and gw and gw.cat_client:
            cat = gw.cat_client
            if cmd == 'VOL_LEFT':
                ret = cat.send_web_volume(cat.LEFT, data.get('value', 50))
                result = {'ok': False, 'error': ret} if ret == 'serial not connected' else {'ok': True}
            elif cmd == 'VOL_RIGHT':
                ret = cat.send_web_volume(cat.RIGHT, data.get('value', 50))
                result = {'ok': False, 'error': ret} if ret == 'serial not connected' else {'ok': True}
            elif cmd == 'SQ_LEFT':
                ret = cat.send_web_squelch(cat.LEFT, data.get('value', 25))
                result = {'ok': False, 'error': ret} if ret == 'serial not connected' else {'ok': True}
            elif cmd == 'SQ_RIGHT':
                ret = cat.send_web_squelch(cat.RIGHT, data.get('value', 25))
                result = {'ok': False, 'error': ret} if ret == 'serial not connected' else {'ok': True}
            else:
                ret = cat.send_web_command(cmd)
                if isinstance(ret, str):
                    if 'serial not connected' in ret:
                        cat._serial_connected = False
                    result = {'ok': False, 'error': ret}
                else:
                    result = {'ok': bool(ret)}
    except Exception as e:
        result = {'ok': False, 'error': str(e)}
    try:
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json_mod.dumps(result).encode('utf-8'))
    except BrokenPipeError:
        pass
    return

def handle_sdrcmd(handler, parent):
    """POST /sdrcmd"""
    length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(length).decode('utf-8')
    result = {'ok': False, 'error': 'SDR plugin not available'}
    try:
        data = json_mod.loads(body)
        _sdr_p = getattr(parent.gateway, 'sdr_plugin', None) if parent.gateway else None
        if _sdr_p:
            result = _sdr_p.execute(data)
    except Exception as e:
        result = {'ok': False, 'error': str(e)}
    try:
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json_mod.dumps(result).encode('utf-8'))
    except BrokenPipeError:
        pass
    return

def handle_darkicecmd(handler, parent):
    """POST /darkicecmd"""
    length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(length).decode('utf-8')
    result = {'ok': False}
    try:
        data = json_mod.loads(body)
        cmd = data.get('cmd', '')
        gw = parent.gateway
        if gw:
            if cmd == 'start':
                if not gw._find_darkice_pid():
                    gw._restart_darkice()
                    result = {'ok': True, 'msg': 'DarkIce started'}
                else:
                    result = {'ok': True, 'msg': 'DarkIce already running'}
            elif cmd == 'stop':
                pid = gw._find_darkice_pid()
                if pid:
                    import signal as sig_mod
                    try:
                        os.kill(pid, sig_mod.SIGTERM)
                        time.sleep(1)
                        # Check if still alive
                        if gw._find_darkice_pid():
                            os.kill(pid, sig_mod.SIGKILL)
                    except ProcessLookupError:
                        pass
                    gw._darkice_pid = None
                    gw._darkice_was_running = False  # Prevent auto-restart
                    result = {'ok': True, 'msg': 'DarkIce stopped'}
                else:
                    result = {'ok': True, 'msg': 'DarkIce not running'}
            elif cmd == 'restart':
                pid = gw._find_darkice_pid()
                if pid:
                    import signal as sig_mod
                    try:
                        os.kill(pid, sig_mod.SIGTERM)
                        time.sleep(1)
                        if gw._find_darkice_pid():
                            os.kill(pid, sig_mod.SIGKILL)
                    except ProcessLookupError:
                        pass
                    gw._darkice_pid = None
                    time.sleep(1)
                gw._restart_darkice()
                gw._darkice_was_running = True  # Re-enable auto-restart
                result = {'ok': True, 'msg': 'DarkIce restarted'}
    except Exception as e:
        result = {'ok': False, 'msg': str(e)}
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.end_headers()
    handler.wfile.write(json_mod.dumps(result).encode('utf-8'))
    return

def handle_packet_cmd(handler, parent):
    """POST /packet/* — dispatch packet radio commands."""
    import json as _json
    path = handler.path.split('?')[0]
    action = path.replace('/packet/', '')

    try:
        length = int(handler.headers.get('Content-Length', 0))
        body = handler.rfile.read(length) if length > 0 else b'{}'
        data = _json.loads(body) if body else {}
    except Exception:
        data = {}

    gw = parent.gateway if parent else None
    result = {"ok": False, "error": "packet plugin not available"}

    if gw and gw.packet_plugin:
        pp = gw.packet_plugin
        if action == 'mode':
            result = pp.execute({'cmd': 'set_mode', 'mode': data.get('mode', 'idle')})
        elif action == 'aprs_beacon':
            result = pp.execute({'cmd': 'aprs_beacon'})
        elif action == 'aprs_send':
            result = pp.execute({'cmd': 'aprs_send', 'to': data.get('to', ''), 'message': data.get('message', '')})
        elif action == 'bbs_connect':
            result = pp.execute({'cmd': 'bbs_connect', 'callsign': data.get('callsign', '')})
        elif action == 'bbs_disconnect':
            result = pp.execute({'cmd': 'bbs_disconnect'})
        elif action == 'bbs_send':
            result = pp.execute({'cmd': 'bbs_send', 'text': data.get('text', '')})
        elif action == 'force_audio':
            result = pp.execute({'cmd': 'force_audio'})
        elif action == 'setendpoint':
            new_name = data.get('name', '')
            resolved = pp.set_endpoint_pref(new_name)
            result = {"ok": True, "endpoint_pref": pp._endpoint_pref, "endpoint_active": resolved}
        elif action == 'winlink/compose':
            result = _winlink_compose(data)
        elif action == 'winlink/connect':
            result = _winlink_connect(data)
        else:
            result = {"ok": False, "error": f"unknown action: {action}"}

    resp = _json.dumps(result).encode()
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', str(len(resp)))
    handler.end_headers()
    handler.wfile.write(resp)


# ── Helpers for handle_packet_cmd ─────────────────────────

def _winlink_compose(data):
    """Queue a Winlink message via Pat CLI."""
    import subprocess, shutil
    pat = shutil.which('pat')
    if not pat:
        return {"ok": False, "error": "pat not installed"}
    to = data.get('to', '').strip()
    cc = data.get('cc', '').strip()
    subject = data.get('subject', '').strip()
    body = data.get('body', '')
    if not to or not subject:
        return {"ok": False, "error": "to and subject required"}
    cmd = [pat, 'compose', '-s', subject]
    if cc:
        cmd += ['-c', cc]
    cmd.append(to)
    try:
        proc = subprocess.run(cmd, input=body.encode(), capture_output=True, timeout=10)
        if proc.returncode == 0:
            return {"ok": True}
        return {"ok": False, "error": proc.stderr.decode().strip() or f"exit code {proc.returncode}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _winlink_connect(data):
    """Connect to a Winlink gateway via Pat CLI + AGW."""
    global _winlink_log
    import subprocess, shutil, threading
    pat = shutil.which('pat')
    if not pat:
        return {"ok": False, "error": "pat not installed"}
    gateway = data.get('gateway', '').strip()
    if not gateway:
        return {"ok": False, "error": "gateway callsign required"}
    _winlink_log = f"Connecting to {gateway}...\n"
    try:
        proc = subprocess.Popen(
            [pat, 'connect', f'ax25+agwpe:///{gateway}'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1)
        # Read output line-by-line into shared buffer
        def _reader():
            global _winlink_log
            for line in proc.stdout:
                text = line.decode('utf-8', errors='replace').rstrip()
                if text:
                    _winlink_log += text + '\n'
            proc.wait()
        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        t.join(timeout=180)
        if proc.poll() is None:
            proc.kill()
            _winlink_log += '\n[TIMEOUT — killed after 180s]\n'
            return {"ok": False, "error": "timeout (180s)"}
        if proc.returncode == 0:
            _winlink_log += '\nSync complete.\n'
            return {"ok": True, "result": "sync complete"}
        _winlink_log += f'\nExit code: {proc.returncode}\n'
        return {"ok": False, "error": f"exit code {proc.returncode}"}
    except Exception as e:
        _winlink_log += f'\nError: {e}\n'
        return {"ok": False, "error": str(e)}
