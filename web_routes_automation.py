"""POST handlers for automation tasks and sound mapping refresh."""

"""POST route handlers extracted from web_server.py."""

import json as json_mod
import os
import time
import subprocess
import threading as _thr

from audio_sources import generate_cw_pcm
from cat_client import RadioCATClient


def handle_automationcmd(handler, parent):
    """POST /automationcmd"""
    length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(length).decode('utf-8')
    result = {'ok': False}
    try:
        data = json_mod.loads(body)
        cmd = data.get('cmd', '')
        engine = parent.gateway.automation_engine if parent.gateway else None
        if not engine:
            result = {'ok': False, 'error': 'Automation not enabled'}
        elif cmd == 'trigger':
            task_name = data.get('task', '')
            if engine.trigger(task_name):
                result = {'ok': True, 'triggered': task_name}
            else:
                result = {'ok': False, 'error': f'Task not found: {task_name}'}
        elif cmd == 'reload':
            engine.reload_scheme()
            result = {'ok': True, 'tasks': len(engine._tasks)}
        elif cmd == 'stop_recording':
            path = engine.recorder.stop()
            result = {'ok': True, 'path': path}
        else:
            result = {'ok': False, 'error': f'Unknown command: {cmd}'}
    except Exception as e:
        result = {'ok': False, 'error': str(e)}
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.end_headers()
    handler.wfile.write(json_mod.dumps(result).encode())
    return

def handle_refreshsounds(handler, parent):
    """POST /refreshsounds"""
    result = {'ok': False, 'count': 0}
    gw = parent.gateway
    if gw and gw.playback_source:
        try:
            # Clear cached soundboard files
            _cache_dir = os.path.join(gw.playback_source.announcement_directory, '.cache')
            if os.path.isdir(_cache_dir):
                import shutil
                shutil.rmtree(_cache_dir)
            # Re-scan files (local files stay, new random fills)
            gw.playback_source.check_file_availability()
            _count = sum(1 for k in '123456789' if gw.playback_source.file_status[k]['exists']
                         and gw.playback_source.file_status[k].get('path', '').find('.cache') >= 0)
            result = {'ok': True, 'count': _count}
        except Exception as _e:
            result = {'ok': False, 'error': str(_e)}
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.end_headers()
    try:
        handler.wfile.write(json_mod.dumps(result).encode())
    except BrokenPipeError:
        pass
    return
