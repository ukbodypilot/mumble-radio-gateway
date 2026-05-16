#!/usr/bin/env python3
"""
Remote transcription worker.

Loads Moonshine or Whisper, serves inference over HTTP so the gateway can
offload ASR to a separate machine.  VAD always runs on the gateway; only
the ASR inference step is remote.

Endpoints:
  POST /transcribe   body: raw float32 LE bytes at 16 kHz
                     response: {"text": "...", "proc_time": 1.23}
  GET  /status       response: model info + health stats

Usage:
  python3 transcribe_worker.py --model moonshine/base --port 9800
  python3 transcribe_worker.py --model whisper/medium.en --port 9800
"""

import argparse
import json
import os
import sys
import threading
import time

import numpy as np

# Locate transcribe_engine.py — works both when this file lives in tools/
# alongside a gateway checkout, and when deployed standalone in the same dir.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _search in (_HERE, _PARENT):
    if os.path.exists(os.path.join(_search, 'transcribe_engine.py')):
        sys.path.insert(0, _search)
        break

from transcribe_engine import LocalInferenceEngine, _VALID_MODELS  # noqa: E402

# ---------------------------------------------------------------------------
# Global state (set up in main, read-only after model loaded)
# ---------------------------------------------------------------------------

_engine: LocalInferenceEngine | None = None
_engine_lock = threading.Lock()   # guards _engine during load
_stats_lock = threading.Lock()
_stats = {
    'total': 0,
    'errors': 0,
    'total_proc_secs': 0.0,
    'total_audio_secs': 0.0,
    'start_time': time.time(),
}


def _get_rss_mb():
    try:
        with open('/proc/self/status') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return round(int(line.split()[1]) / 1024, 1)
    except Exception:
        pass
    return 0.0


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

from http.server import BaseHTTPRequestHandler, HTTPServer
import socketserver


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress default access log; errors still go to stderr

    # -- GET /status --

    def do_GET(self):
        if self.path.rstrip('/') != '/status':
            self._send(404, {'error': 'not found'})
            return
        with _stats_lock:
            st = dict(_stats)
        with _engine_lock:
            eng = _engine
        avg_ratio = (
            round(st['total_proc_secs'] / st['total_audio_secs'], 3)
            if st['total_audio_secs'] > 0 else None
        )
        payload = {
            'model_loaded': eng is not None and eng.is_loaded,
            'model_key': eng.model_key if eng else None,
            'engine': eng.engine if eng else None,
            'total_transcriptions': st['total'],
            'errors': st['errors'],
            'avg_ratio': avg_ratio,
            'uptime_secs': round(time.time() - st['start_time']),
            'ram_mb': _get_rss_mb(),
        }
        self._send(200, payload)

    # -- POST /transcribe --

    def do_POST(self):
        if self.path.rstrip('/') != '/transcribe':
            self._send(404, {'error': 'not found'})
            return

        with _engine_lock:
            eng = _engine
        if eng is None or not eng.is_loaded:
            self._send(503, {'error': 'model not loaded'})
            return

        length = int(self.headers.get('Content-Length', 0))
        if length == 0:
            self._send(400, {'error': 'empty body'})
            return

        raw = self.rfile.read(length)
        audio_16k = np.frombuffer(raw, dtype=np.float32)
        audio_secs = len(audio_16k) / 16000.0

        t0 = time.monotonic()
        try:
            text = eng.transcribe(audio_16k)
            proc_time = round(time.monotonic() - t0, 3)
            with _stats_lock:
                _stats['total'] += 1
                _stats['total_proc_secs'] += proc_time
                _stats['total_audio_secs'] += audio_secs
            self._send(200, {'text': text, 'proc_time': proc_time})
        except Exception as e:
            with _stats_lock:
                _stats['errors'] += 1
            self._send(500, {'error': str(e)})

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


# ---------------------------------------------------------------------------
# Model loader thread
# ---------------------------------------------------------------------------

def _load_model(model_key):
    global _engine
    eng = LocalInferenceEngine(model_key)
    print(f'[worker] Loading {eng.model_key}...', flush=True)
    try:
        eng.load()
        print(f'[worker] Model ready', flush=True)
    except Exception as e:
        print(f'[worker] Failed to load model: {e}', flush=True)
        return
    with _engine_lock:
        _engine = eng


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Radio gateway transcription worker')
    parser.add_argument('--model', default='moonshine/base',
                        help=f'Model key: {", ".join(sorted(_VALID_MODELS))}')
    parser.add_argument('--port', type=int, default=9800,
                        help='HTTP port to listen on (default 9800)')
    parser.add_argument('--host', default='0.0.0.0',
                        help='Bind address (default 0.0.0.0)')
    args = parser.parse_args()

    model_key = args.model
    if model_key not in _VALID_MODELS:
        print(f'[worker] Unknown model {model_key!r}. '
              f'Valid: {", ".join(sorted(_VALID_MODELS))}', flush=True)
        sys.exit(1)

    loader = threading.Thread(target=_load_model, args=(model_key,), daemon=True)
    loader.start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'[worker] Listening on {args.host}:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('[worker] Stopping', flush=True)


if __name__ == '__main__':
    main()
