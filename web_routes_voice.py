"""POST handlers for the /voice page (talk-to-Claude tmux session)."""

"""POST route handlers extracted from web_server.py."""

import json as json_mod
import os
import time
import subprocess
import threading as _thr

from audio_sources import generate_cw_pcm
from cat_client import RadioCATClient


def handle_voice_send(handler, parent):
    """POST /voice/send"""
    import json as json_mod
    length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(length).decode('utf-8')
    try:
        data = json_mod.loads(body)
    except Exception:
        data = {}
    text = data.get('text', '').strip()
    tmux_target = os.environ.get('TMUX_TARGET', 'claude-voice')
    if not text:
        handler.send_response(400)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(b'{"error":"empty text"}')
        return
    chk = subprocess.run(['tmux', 'has-session', '-t', tmux_target], capture_output=True)
    if chk.returncode != 0:
        handler.send_response(503)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json_mod.dumps({'error': f"tmux session '{tmux_target}' not found"}).encode())
        return
    subprocess.run(['tmux', 'send-keys', '-t', tmux_target, '-l', text])
    subprocess.run(['tmux', 'send-keys', '-t', tmux_target, 'Enter'])
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.end_headers()
    handler.wfile.write(json_mod.dumps({'ok': True, 'sent': text}).encode())
    return

def handle_voice_session(handler, parent):
    """POST /voice/session"""
    import json as json_mod
    length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(length).decode('utf-8')
    try:
        data = json_mod.loads(body)
    except Exception:
        data = {}
    action = data.get('action', '')
    tmux_target = 'claude-voice'
    result = {'ok': False}

    if action == 'start':
        # Create session if it doesn't exist, then launch claude
        has = subprocess.run(['tmux', 'has-session', '-t', tmux_target], capture_output=True)
        if has.returncode == 0:
            result = {'ok': True, 'msg': 'session already exists'}
        else:
            subprocess.run(['tmux', 'new-session', '-d', '-s', tmux_target, '-c', '/home/user'])
            subprocess.run(['tmux', 'send-keys', '-t', tmux_target, '-l', 'claude --dangerously-skip-permissions'])
            subprocess.run(['tmux', 'send-keys', '-t', tmux_target, 'Enter'])
            # Auto-confirm workspace trust prompt after startup
            import time; time.sleep(3)
            subprocess.run(['tmux', 'send-keys', '-t', tmux_target, 'Enter'])
            result = {'ok': True, 'msg': 'session created, claude started'}

    elif action == 'restart':
        # Send Ctrl+C to stop current process, wait, then start claude again
        has = subprocess.run(['tmux', 'has-session', '-t', tmux_target], capture_output=True)
        if has.returncode != 0:
            subprocess.run(['tmux', 'new-session', '-d', '-s', tmux_target, '-c', '/home/user'])
        else:
            # Send Ctrl+C twice to kill any running process
            subprocess.run(['tmux', 'send-keys', '-t', tmux_target, 'C-c', ''])
            import time; time.sleep(0.5)
            subprocess.run(['tmux', 'send-keys', '-t', tmux_target, 'C-c', ''])
            import time; time.sleep(1)
            # Clear the screen before starting fresh
            subprocess.run(['tmux', 'send-keys', '-t', tmux_target, '-l', 'clear'])
            subprocess.run(['tmux', 'send-keys', '-t', tmux_target, 'Enter'])
            import time; time.sleep(0.3)
        subprocess.run(['tmux', 'send-keys', '-t', tmux_target, '-l', 'claude --dangerously-skip-permissions'])
        subprocess.run(['tmux', 'send-keys', '-t', tmux_target, 'Enter'])
        # Auto-confirm workspace trust prompt after startup
        import time; time.sleep(3)
        subprocess.run(['tmux', 'send-keys', '-t', tmux_target, 'Enter'])
        result = {'ok': True, 'msg': 'claude restarted'}

    elif action == 'stop':
        has = subprocess.run(['tmux', 'has-session', '-t', tmux_target], capture_output=True)
        if has.returncode == 0:
            # Send Ctrl+C to stop Claude, clear screen, leave the shell running
            subprocess.run(['tmux', 'send-keys', '-t', tmux_target, 'C-c', ''])
            import time; time.sleep(0.5)
            subprocess.run(['tmux', 'send-keys', '-t', tmux_target, 'C-c', ''])
            import time; time.sleep(0.5)
            subprocess.run(['tmux', 'send-keys', '-t', tmux_target, '-l', 'clear'])
            subprocess.run(['tmux', 'send-keys', '-t', tmux_target, 'Enter'])
            result = {'ok': True, 'msg': 'claude stopped'}
        else:
            result = {'ok': True, 'msg': 'session not running'}
    else:
        result = {'ok': False, 'error': 'unknown action'}

    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.end_headers()
    handler.wfile.write(json_mod.dumps(result).encode())
    return
