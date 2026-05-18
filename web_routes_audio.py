"""POST handlers for audio plumbing: routing, mixer, loop recorder, traces, processing."""

"""POST route handlers extracted from web_server.py."""

import json as json_mod
import os
import time
import subprocess
import threading as _thr

from audio_sources import generate_cw_pcm
from cat_client import RadioCATClient


def _resolve_source(gw, source_id):
    """Resolve a source ID to the source object, checking plugins then link endpoints."""
    _plugin_map = {
        'sdr': 'sdr_plugin', 'kv4p': 'kv4p_plugin',
        'remote': 'remote_audio_source', 'announce': 'announce_input_source',
    }
    if source_id in _plugin_map:
        return getattr(gw, _plugin_map[source_id], None)
    # Link endpoint lookup by source_id
    for name, src in getattr(gw, 'link_endpoints', {}).items():
        if getattr(src, 'source_id', None) == source_id:
            return src
    return None


def handle_testloop(handler, parent):
    """POST /testloop"""
    result = {'ok': False, 'error': 'playback not available'}
    if parent.gateway and parent.gateway.playback_source:
        result = parent.gateway.playback_source.toggle_test_loop()
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.end_headers()
    handler.wfile.write(json_mod.dumps(result).encode('utf-8'))
    return

def handle_mixer(handler, parent):
    """POST /mixer"""
    length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(length).decode('utf-8')
    result = {'ok': False, 'error': 'no gateway'}
    try:
        data = json_mod.loads(body)
        action = data.get('action', '')
        source = data.get('source', '')
        gw = parent.gateway
        if not gw:
            pass
        elif action == 'status':
            # Return full mixer state
            s = gw.get_status_dict()
            result = {'ok': True, 'mutes': {
                'tx': s.get('tx_muted', False),
                'rx': s.get('rx_muted', False),
                'sdr1': s.get('sdr1_muted', False),
                'sdr2': s.get('sdr2_muted', False),
                'd75': s.get('d75_muted', False),
                'kv4p': s.get('kv4p_muted', False),
                'remote': s.get('remote_muted', False),
                'announce': s.get('announce_muted', False),
                'speaker': s.get('speaker_muted', False),
            }, 'levels': {
                'radio_rx': s.get('radio_rx', 0),
                'radio_tx': s.get('radio_tx', 0),
                'sdr1': s.get('sdr1_level', 0),
                'sdr2': s.get('sdr2_level', 0),
                'd75': s.get('d75_level', 0),
                'kv4p': s.get('kv4p_level', 0),
                'remote': s.get('remote_level', 0),
                'announce': s.get('an_level', 0),
                'speaker': s.get('speaker_level', 0),
            }, 'volume': s.get('volume', 1.0),
            'duck': {
                'sdr1': s.get('sdr1_duck', False),
                'sdr2': getattr(_resolve_source(gw, 'sdr'), 'duck', False),
                'd75': getattr(_resolve_source(gw, 'd75'), 'duck', False),
                'kv4p': getattr(_resolve_source(gw, 'kv4p'), 'duck', False),
                'remote': getattr(_resolve_source(gw, 'remote'), 'duck', False),
            }, 'ducked': {
                'sdr1': s.get('sdr1_ducked', False),
                'sdr2': s.get('sdr2_ducked', False),
                'remote': s.get('cl_ducked', False),
            }, 'flags': {
                'vad': s.get('vad_enabled', False),
                'agc': getattr(gw.config, 'ENABLE_AGC', False),
                'echo_cancel': getattr(gw.config, 'ENABLE_ECHO_CANCELLATION', False),
                'rebroadcast': s.get('sdr_rebroadcast', False),
                'talkback': getattr(gw, 'tx_talkback', False),
                'manual_ptt': s.get('manual_ptt', False),
            }, 'boost': {
                'd75': int(getattr(_resolve_source(gw, 'd75'), 'audio_boost', 1.0) * 100),
                'kv4p': int(getattr(_resolve_source(gw, 'kv4p'), 'audio_boost', 1.0) * 100),
                'remote': int(getattr(_resolve_source(gw, 'remote'), 'audio_boost', 1.0) * 100),
            }, 'processing': {
                'radio': gw.radio_processor.get_active_list() if hasattr(gw, 'radio_processor') else [],
                'sdr': gw.sdr_processor.get_active_list() if hasattr(gw, 'sdr_processor') else [],
                'd75': gw.d75_processor.get_active_list() if hasattr(gw, 'd75_processor') else [],
                'kv4p': gw.kv4p_processor.get_active_list() if hasattr(gw, 'kv4p_processor') else [],
            }}

        elif action in ('mute', 'unmute', 'toggle'):
            # Mute control for a specific source
            _mute_map = {
                'tx':       ('tx_muted', None),
                'rx':       ('rx_muted', None),
                'sdr1':     ('sdr_muted', 'sdr_plugin'),
                'sdr2':     ('sdr2_muted', 'sdr_plugin'),
                'kv4p':     ('kv4p_muted', 'kv4p_plugin'),
                'remote':   ('remote_audio_muted', 'remote_audio_source'),
                'announce': ('announce_input_muted', 'announce_input_source'),
                'speaker':  ('speaker_muted', None),
            }
            if source == 'global':
                current = gw.tx_muted and gw.rx_muted
                if action == 'toggle':
                    want = not current
                elif action == 'mute':
                    want = True
                else:
                    want = False
                gw.tx_muted = want
                gw.rx_muted = want
                result = {'ok': True, 'muted': want}
            elif source in _mute_map:
                attr, src_attr = _mute_map[source]
                current = getattr(gw, attr, False)
                if action == 'toggle':
                    want = not current
                elif action == 'mute':
                    want = True
                else:
                    want = False
                setattr(gw, attr, want)
                # Sync to source object if it has .muted
                if src_attr:
                    src_obj = getattr(gw, src_attr, None)
                    if src_obj:
                        src_obj.muted = want
                result = {'ok': True, 'source': source, 'muted': want}
            elif source.startswith('link_rx:') or source.startswith('link_tx:'):
                parts = source.split(':', 1)
                direction = parts[0]  # 'link_rx' or 'link_tx'
                ep_name = parts[1] if len(parts) > 1 else ''
                if not ep_name:
                    result = {'ok': False, 'error': 'missing endpoint name'}
                else:
                    settings = gw.link_endpoint_settings.setdefault(ep_name, {})
                    mute_key = 'rx_muted' if direction == 'link_rx' else 'tx_muted'
                    current = settings.get(mute_key, False)
                    want = not current if action == 'toggle' else (action == 'mute')
                    settings[mute_key] = want
                    if direction == 'link_rx':
                        src = gw.link_endpoints.get(ep_name)
                        if src:
                            src.muted = want
                    gw._save_link_settings()
                    result = {'ok': True, 'muted': want}
            else:
                # Try generic link endpoint by sanitised name
                _ep_src = None
                _ep_name = None
                for _n, _s in gw.link_endpoints.items():
                    if getattr(_s, 'source_id', None) == source:
                        _ep_src = _s
                        _ep_name = _n
                        break
                if _ep_src:
                    current = getattr(_ep_src, 'muted', False)
                    want = not current if action == 'toggle' else (action == 'mute')
                    _ep_src.muted = want
                    settings = gw.link_endpoint_settings.setdefault(_ep_name, {})
                    settings['rx_muted'] = want
                    gw._save_link_settings()
                    result = {'ok': True, 'source': source, 'muted': want}
                else:
                    result = {'ok': False, 'error': f'unknown source: {source}'}

        elif action == 'volume':
            # Set absolute INPUT_VOLUME
            val = data.get('value')
            if val is not None:
                gw.config.INPUT_VOLUME = max(0.1, min(3.0, float(val)))
                result = {'ok': True, 'volume': round(gw.config.INPUT_VOLUME, 2)}
            else:
                result = {'ok': True, 'volume': round(gw.config.INPUT_VOLUME, 2)}

        elif action == 'duck':
            # Enable/disable duck on a source
            state = data.get('state')  # true/false or omit for toggle
            src_obj = _resolve_source(gw, source)
            if src_obj and hasattr(src_obj, 'duck'):
                if state is None:
                    src_obj.duck = not src_obj.duck
                else:
                    src_obj.duck = bool(state)
                result = {'ok': True, 'source': source, 'duck': src_obj.duck}
            else:
                result = {'ok': False, 'error': f'duck not supported for: {source}'}

        elif action == 'boost':
            # Set per-source audio boost (percentage 0-500)
            pct = data.get('value', 100)
            src_obj = _resolve_source(gw, source)
            if src_obj and hasattr(src_obj, 'audio_boost'):
                src_obj.audio_boost = max(0, min(5.0, float(pct) / 100.0))
                result = {'ok': True, 'source': source, 'boost_pct': int(src_obj.audio_boost * 100)}
            else:
                result = {'ok': False, 'error': f'boost not supported for: {source}'}

        elif action == 'flag':
            # Toggle or set a mixer flag (vad, agc, echo_cancel, rebroadcast)
            flag = data.get('flag', '')
            state = data.get('state')  # true/false or omit for toggle
            if flag == 'vad':
                if state is None:
                    gw.config.ENABLE_VAD = not gw.config.ENABLE_VAD
                else:
                    gw.config.ENABLE_VAD = bool(state)
                result = {'ok': True, 'flag': 'vad', 'enabled': gw.config.ENABLE_VAD}
            elif flag == 'agc':
                if state is None:
                    gw.config.ENABLE_AGC = not gw.config.ENABLE_AGC
                else:
                    gw.config.ENABLE_AGC = bool(state)
                result = {'ok': True, 'flag': 'agc', 'enabled': gw.config.ENABLE_AGC}
            elif flag == 'echo_cancel':
                if state is None:
                    gw.config.ENABLE_ECHO_CANCELLATION = not gw.config.ENABLE_ECHO_CANCELLATION
                else:
                    gw.config.ENABLE_ECHO_CANCELLATION = bool(state)
                result = {'ok': True, 'flag': 'echo_cancel', 'enabled': gw.config.ENABLE_ECHO_CANCELLATION}
            elif flag == 'rebroadcast':
                if state is None:
                    new_state = not gw.sdr_rebroadcast
                else:
                    new_state = bool(state)
                gw.sdr_rebroadcast = new_state
                if not new_state:
                    # Clean up PTT if disabling rebroadcast
                    if getattr(gw, '_rebroadcast_ptt_active', False):
                        gw._rebroadcast_ptt_active = False
                    if gw.radio_source:
                        gw.radio_source.enabled = True
                result = {'ok': True, 'flag': 'rebroadcast', 'enabled': gw.sdr_rebroadcast}
            elif flag == 'talkback':
                if state is None:
                    gw.tx_talkback = not gw.tx_talkback
                else:
                    gw.tx_talkback = bool(state)
                result = {'ok': True, 'flag': 'talkback', 'enabled': gw.tx_talkback}
            else:
                result = {'ok': False, 'error': f'unknown flag: {flag}'}

        elif action == 'processing':
            # Toggle or set audio processing filter
            # source: radio, sdr, d75, kv4p
            # filter: gate, hpf, lpf, notch
            filt = data.get('filter', '')
            proc_state = data.get('state')  # true/false or omit for toggle
            valid_sources = ('radio', 'sdr', 'd75', 'kv4p')
            valid_filters = ('gate', 'hpf', 'lpf', 'notch')
            if source not in valid_sources:
                result = {'ok': False, 'error': f'source must be one of: {", ".join(valid_sources)}'}
            elif filt not in valid_filters:
                result = {'ok': False, 'error': f'filter must be one of: {", ".join(valid_filters)}'}
            else:
                gw.handle_proc_toggle(source, filt, state=proc_state)
                # Read back the current state
                _proc_map = {
                    'radio': gw.radio_processor,
                    'sdr': gw.sdr_processor,
                    'd75': gw.d75_processor,
                    'kv4p': gw.kv4p_processor,
                }
                proc_obj = _proc_map.get(source)
                active = proc_obj.get_active_list() if proc_obj else []
                result = {'ok': True, 'source': source, 'active': active}

        else:
            result = {'ok': False, 'error': f'unknown action: {action}'}

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

def handle_proc_toggle(handler, parent):
    """POST /proc_toggle"""
    length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(length).decode('utf-8')
    try:
        data = json_mod.loads(body)
        source = data.get('source', '')  # "radio" or "sdr"
        filt = data.get('filter', '')    # "gate", "hpf", "lpf", "notch"
        if source and filt and parent.gateway:
            parent.gateway.handle_proc_toggle(source, filt)
    except Exception:
        pass
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.end_headers()
    handler.wfile.write(b'{"ok":true}')
    return

def handle_tracecmd(handler, parent):
    """POST /tracecmd"""
    content_length = int(handler.headers.get('Content-Length', 0))
    post_data = handler.rfile.read(content_length).decode('utf-8')
    import urllib.parse as _up
    params = _up.parse_qs(post_data)
    trace_type = params.get('type', [''])[0]
    _gw = parent.gateway
    result = {'ok': False}
    if _gw and trace_type == 'audio':
        _gw._trace_recording = not _gw._trace_recording
        if _gw._trace_recording:
            _gw._audio_trace.clear()
            _gw._spk_trace.clear()
            _gw._trace_events.clear()
            import time as _trace_time
            _gw._audio_trace_t0 = _trace_time.monotonic()
            # Start stream-level trace
            if hasattr(_gw, '_stream_trace'):
                _gw._stream_trace.start()
            print(f"\n[Trace] Recording STARTED (via web UI)")
        else:
            # Stop stream-level trace and dump
            if hasattr(_gw, '_stream_trace'):
                _gw._stream_trace.stop()
            print(f"\n[Trace] Recording STOPPED ({len(_gw._audio_trace)} ticks captured)")
            _gw._dump_audio_trace()
            # Dump stream trace alongside, with matching timestamp so the pair is easy to find.
            if hasattr(_gw, '_stream_trace'):
                import os as _os, datetime as _dt
                _ts = _dt.datetime.now().strftime('%Y%m%d_%H%M%S')
                _st_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                         'tools', f'stream_trace_{_ts}.txt')
                _gw._stream_trace.dump(_st_path)
        import time as _trace_time2
        _gw._trace_events.append((_trace_time2.monotonic(), 'trace', 'on' if _gw._trace_recording else 'off'))
        result = {'ok': True, 'active': _gw._trace_recording}
    elif _gw and trace_type == 'watchdog':
        _gw._watchdog_active = not _gw._watchdog_active
        if _gw._watchdog_active:
            _gw._watchdog_t0 = time.monotonic()
            _gw._watchdog_thread = _thr.Thread(
                target=_gw._watchdog_trace_loop, daemon=True)
            _gw._watchdog_thread.start()
            print(f"\n[Watchdog] Trace STARTED (via web UI)")
        else:
            print(f"\n[Watchdog] Trace STOPPED (via web UI)")
        result = {'ok': True, 'active': _gw._watchdog_active}
    try:
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json_mod.dumps(result).encode('utf-8'))
    except BrokenPipeError:
        pass
    return

def handle_routing_cmd(handler, parent):
    """POST /routing/cmd"""
    import json as json_mod
    length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(length).decode('utf-8')
    result = {'ok': False, 'error': 'invalid'}
    try:
        data = json_mod.loads(body)
        result = parent._handle_routing_cmd(data)
    except Exception as e:
        result = {'ok': False, 'error': str(e)}
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.end_headers()
    handler.wfile.write(json_mod.dumps(result).encode())
    return

def handle_loop_export(handler, parent):
    """POST /loop/export — export a time range as downloadable audio file."""
    try:
        length = int(handler.headers.get('Content-Length', 0))
        body = handler.rfile.read(length) if length > 0 else b'{}'
        data = json_mod.loads(body) if body else {}
    except Exception:
        handler.send_response(400)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(b'{"ok":false,"error":"invalid JSON body"}')
        return

    gw = parent.gateway if parent else None
    lr = getattr(gw, 'loop_recorder', None) if gw else None
    if not lr:
        handler.send_response(503)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(b'{"ok":false,"error":"loop recorder not available"}')
        return

    bus = data.get('bus', '')
    start = data.get('start')
    end = data.get('end')
    fmt = data.get('format', 'mp3')
    if not bus or start is None or end is None:
        handler.send_response(400)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(b'{"ok":false,"error":"missing bus, start, or end"}')
        return
    if fmt not in ('mp3', 'wav'):
        handler.send_response(400)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(b'{"ok":false,"error":"format must be mp3 or wav"}')
        return

    temp_path = None
    try:
        temp_path = lr.export_range(bus, float(start), float(end), fmt=fmt)
        if not temp_path or not os.path.isfile(temp_path):
            handler.send_response(404)
            handler.send_header('Content-Type', 'application/json')
            handler.end_headers()
            handler.wfile.write(b'{"ok":false,"error":"no audio found for range"}')
            return

        ctype = {'mp3': 'audio/mpeg', 'wav': 'audio/wav'}.get(fmt, 'application/octet-stream')
        fname = f"loop_{bus}_{int(float(start))}_{int(float(end))}.{fmt}"
        fsize = os.path.getsize(temp_path)
        handler.send_response(200)
        handler.send_header('Content-Type', ctype)
        handler.send_header('Content-Disposition', f'attachment; filename="{fname}"')
        handler.send_header('Content-Length', str(fsize))
        handler.end_headers()
        with open(temp_path, 'rb') as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                handler.wfile.write(chunk)
    except BrokenPipeError:
        pass
    except Exception as e:
        try:
            handler.send_response(500)
            handler.send_header('Content-Type', 'application/json')
            handler.end_headers()
            handler.wfile.write(json_mod.dumps({"ok": False, "error": str(e)}).encode('utf-8'))
        except Exception:
            pass
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass

def handle_recordingsdelete(handler, parent):
    """POST /recordingsdelete"""
    length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(length).decode('utf-8')
    data = json_mod.loads(body)
    filenames = data.get('files', [])
    delete_all = data.get('delete_all', False)
    rec_dir = ''
    if parent.gateway and parent.gateway.automation_engine:
        rec_dir = parent.gateway.automation_engine.recorder._dir
    deleted = 0
    if rec_dir and os.path.isdir(rec_dir):
        if delete_all:
            for fname in os.listdir(rec_dir):
                fpath = os.path.join(rec_dir, fname)
                if os.path.isfile(fpath):
                    try:
                        os.remove(fpath)
                        deleted += 1
                    except OSError:
                        pass
        else:
            for fname in filenames:
                fname = os.path.basename(fname)  # no path traversal
                fpath = os.path.join(rec_dir, fname)
                if os.path.isfile(fpath):
                    try:
                        os.remove(fpath)
                        deleted += 1
                    except OSError:
                        pass
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.end_headers()
    handler.wfile.write(json_mod.dumps({'deleted': deleted}).encode('utf-8'))
    return
