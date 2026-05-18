"""POST handlers for host/system: keypress, reboot, restart, tmux, config form, etc."""

"""POST route handlers extracted from web_server.py."""

import json as json_mod
import os
import time
import subprocess
import threading as _thr

from audio_sources import generate_cw_pcm
from cat_client import RadioCATClient


def handle_key(handler, parent):
    """POST /key"""
    length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(length).decode('utf-8')
    try:
        data = json_mod.loads(body)
        key_char = data.get('key', '')
        if key_char and parent.gateway:
            parent.gateway.handle_key(key_char)
    except Exception:
        pass
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.end_headers()
    handler.wfile.write(b'{"ok":true}')
    return

def handle_gpscmd(handler, parent):
    """POST /gpscmd"""
    length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(length).decode('utf-8')
    result = {'ok': False, 'error': 'GPS manager not available'}
    try:
        data = json_mod.loads(body)
        gps = getattr(parent.gateway, 'gps_manager', None) if parent.gateway else None
        if gps:
            cmd = data.get('cmd', '')
            if cmd == 'set_position':
                ok = gps.set_simulated_position(
                    lat=data.get('lat'), lon=data.get('lon'),
                    alt=data.get('alt'), speed=data.get('speed'),
                    heading=data.get('heading'))
                result = {'ok': ok, 'error': '' if ok else 'Not in simulate mode'}
            elif cmd == 'switch_mode':
                mode = data.get('mode', '')
                ok, msg = gps.switch_mode(mode)
                result = {'ok': ok, 'message': msg}
            elif cmd == 'status':
                result = {'ok': True, 'status': gps.get_status()}
            else:
                result = {'ok': False, 'error': f'Unknown command: {cmd}'}
    except Exception as e:
        result = {'ok': False, 'error': str(e)}
    try:
        resp = json_mod.dumps(result).encode('utf-8')
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.send_header('Content-Length', str(len(resp)))
        handler.end_headers()
        handler.wfile.write(resp)
    except BrokenPipeError:
        pass

def handle_reboothost(handler, parent):
    """POST /reboothost"""
    import subprocess as _sp
    result = {'ok': False}
    try:
        _sp.Popen(['sudo', 'reboot'])
        result = {'ok': True}
    except Exception as _e:
        result = {'ok': False, 'error': str(_e)}
    try:
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json_mod.dumps(result).encode('utf-8'))
    except BrokenPipeError:
        pass
    return

def handle_restartgateway(handler, parent):
    """POST /restartgateway — restart the radio-gateway systemd service."""
    import subprocess as _sp
    result = {'ok': False}
    try:
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json_mod.dumps({'ok': True}).encode('utf-8'))
        try:
            handler.wfile.flush()
        except Exception:
            pass
    except BrokenPipeError:
        pass
    try:
        _sp.Popen(['sudo', 'systemctl', 'restart', 'radio-gateway.service'])
    except Exception as _e:
        print(f"  [restart] failed: {_e}")
    return

def handle_telegramcmd(handler, parent):
    """POST /telegramcmd"""
    length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(length).decode('utf-8')
    result = {'ok': False, 'error': 'unknown command'}
    try:
        data = json_mod.loads(body)
        cmd = data.get('cmd', '')
        if cmd in ('start', 'stop', 'restart'):
            _r = subprocess.run(['sudo', 'systemctl', cmd, 'telegram-bot'],
                                capture_output=True, text=True, timeout=10)
            result = {'ok': _r.returncode == 0,
                      'output': (_r.stdout + _r.stderr).strip()}
        elif cmd == 'enable':
            _r = subprocess.run(['sudo', 'systemctl', 'enable', 'telegram-bot'],
                                capture_output=True, text=True, timeout=10)
            result = {'ok': _r.returncode == 0}
        elif cmd == 'disable':
            _r = subprocess.run(['sudo', 'systemctl', 'disable', 'telegram-bot'],
                                capture_output=True, text=True, timeout=10)
            result = {'ok': _r.returncode == 0}
        elif cmd == 'logs':
            _r = subprocess.run(['journalctl', '-u', 'telegram-bot', '--no-pager', '-n', '50'],
                                capture_output=True, text=True, timeout=5)
            result = {'ok': True, 'logs': _r.stdout}
        else:
            result = {'ok': False, 'error': f'unknown command: {cmd}'}
    except Exception as e:
        result = {'ok': False, 'error': str(e)}
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.end_headers()
    handler.wfile.write(json_mod.dumps(result).encode('utf-8'))
    return

def handle_open_tmux(handler, parent):
    """POST /open_tmux"""
    session = getattr(parent.config, 'TELEGRAM_TMUX_SESSION', 'claude-gateway') if parent.config else 'claude-gateway'
    try:
        subprocess.Popen(
            ['xfce4-terminal', '-e', f'tmux attach-session -t {session}'],
            env={**os.environ, 'DISPLAY': ':0'},
            start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        ok = True
    except Exception:
        ok = False
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.end_headers()
    handler.wfile.write(json_mod.dumps({'ok': ok}).encode('utf-8'))
    return

def handle_exit(handler, parent):
    """POST /exit"""
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.end_headers()
    handler.wfile.write(b'{"ok":true}')
    if parent.gateway:
        parent.gateway.restart_requested = False
        parent.gateway.running = False
    return

def handle_config_form(handler, parent):
    """POST fallback -- config form save"""
    import urllib.parse
    length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(length).decode('utf-8')
    form = urllib.parse.parse_qs(body, keep_blank_values=True)
    # Flatten: parse_qs returns lists; for checkboxes with hidden fallback,
    # take the LAST value (checkbox 'true' comes after hidden 'false')
    values = {k: v[-1] for k, v in form.items() if k != '_action'}
    action = form.get('_action', ['save'])[0]

    # Checkboxes: the hidden fallback field ensures unchecked boxes
    # submit 'false'. If a boolean key is completely absent from the
    # form (page not fully loaded, truncated POST), do NOT force it
    # to false — use the current running value instead.
    # Only force false if we received a reasonable number of keys
    # (full form submission has 200+ keys).
    if len(values) > 100:
        for key, default_val in parent._defaults.items():
            if isinstance(default_val, bool) and key not in values:
                values[key] = 'false'
    else:
        print(f"  [Config] WARNING: partial form ({len(values)} keys) — merging with current config")

    parent._save_config(values)
    # Reload config from file so the config page reflects saved values
    parent.config.load_config()
    handler.send_response(303)
    handler.send_header('Location', '/config?saved=1')
    handler.end_headers()
