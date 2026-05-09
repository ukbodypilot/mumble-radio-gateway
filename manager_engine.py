"""Fleet Manager Engine — scheduled Claude-driven fleet health checks."""

import json
import os
import subprocess
import threading
import time
from datetime import datetime, date


_BASE = os.path.dirname(os.path.abspath(__file__))
_STATE_FILE   = os.path.join(_BASE, 'manager_state.json')
_REPORTS_FILE = os.path.join(_BASE, 'manager_reports.jsonl')
_HOURLY_FILE  = os.path.join(_BASE, 'hourly.md')
_DAILY_FILE   = os.path.join(_BASE, 'daily.md')
_CONST_FILE   = os.path.join(_BASE, 'SYSTEM_MANIFEST.md')

_DEFAULT_STATE = {
    'enabled':       False,
    'daily_time':    '06:00',
    'last_hourly':   None,   # "YYYY-MM-DD-HH"
    'last_daily':    None,   # "YYYY-MM-DD"
    'unread_alerts': False,
    'running':       False,
    'last_run_type': None,
    'last_run_ts':   None,
}

_MAX_WAIT_SECS   = 600   # 10 min timeout waiting for Claude's report
_POLL_INTERVAL   = 5     # seconds between polls
_LOOP_INTERVAL   = 30    # seconds between schedule checks


class ManagerEngine:
    def __init__(self, config, gateway=None):
        self.config  = config
        self.gateway = gateway
        self._state  = dict(_DEFAULT_STATE)
        self._lock   = threading.Lock()
        self._stop   = threading.Event()
        self._thread = None
        self._load_state()

    # ── State persistence ─────────────────────────────────────────────────

    def _load_state(self):
        try:
            with open(_STATE_FILE) as f:
                loaded = json.load(f)
            for k, v in _DEFAULT_STATE.items():
                self._state[k] = loaded.get(k, v)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"  [Manager] State load error: {e}")

    def _save_state(self):
        try:
            with open(_STATE_FILE, 'w') as f:
                json.dump(self._state, f, indent=2)
        except Exception as e:
            print(f"  [Manager] State save error: {e}")

    # ── Public API ────────────────────────────────────────────────────────

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name='manager-engine')
        self._thread.start()

    def stop(self):
        self._stop.set()

    def get_status(self):
        with self._lock:
            return dict(self._state)

    def get_reports(self, limit=50):
        reports = []
        try:
            with open(_REPORTS_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            reports.append(json.loads(line))
                        except Exception:
                            pass
        except FileNotFoundError:
            pass
        return reports[-limit:]

    def set_enabled(self, enabled: bool):
        with self._lock:
            self._state['enabled'] = bool(enabled)
            if enabled:
                # Seed last-run keys so the first fire is at the next scheduled
                # time, not immediately on the current (already-passed) hour/day.
                now = datetime.now()
                self._state['last_hourly'] = now.strftime('%Y-%m-%d-%H')
                self._state['last_daily']  = now.strftime('%Y-%m-%d')
            self._save_state()

    def set_daily_time(self, t: str):
        with self._lock:
            self._state['daily_time'] = t
            self._save_state()

    def acknowledge(self):
        with self._lock:
            self._state['unread_alerts'] = False
            self._save_state()

    def run_now(self, task_type: str):
        """Trigger an immediate run (non-blocking — runs in background thread)."""
        t = threading.Thread(target=self._run_task, args=(task_type,), daemon=True)
        t.start()

    def read_doc(self, name: str):
        path = self._doc_path(name)
        if not path or not os.path.exists(path):
            return None
        with open(path) as f:
            return f.read()

    def save_doc(self, name: str, content: str):
        path = self._doc_path(name)
        if not path:
            return False
        with open(path, 'w') as f:
            f.write(content)
        return True

    # ── Scheduler loop ────────────────────────────────────────────────────

    def _loop(self):
        while not self._stop.wait(_LOOP_INTERVAL):
            with self._lock:
                enabled = self._state.get('enabled')
                running = self._state.get('running')
            if not enabled or running:
                continue
            now = datetime.now()
            hour_key = now.strftime('%Y-%m-%d-%H')
            day_key  = now.strftime('%Y-%m-%d')
            daily_time = self._state.get('daily_time', '06:00')
            with self._lock:
                last_hourly = self._state.get('last_hourly')
                last_daily  = self._state.get('last_daily')
            # Daily fires first (once per day at configured time)
            if now.strftime('%H:%M') >= daily_time and last_daily != day_key:
                self._run_task('daily')
                with self._lock:
                    self._state['last_daily'] = day_key
                    self._save_state()
            elif last_hourly != hour_key:
                self._run_task('hourly')
                with self._lock:
                    self._state['last_hourly'] = hour_key
                    self._save_state()

    # ── Task execution ────────────────────────────────────────────────────

    def _run_task(self, task_type: str):
        run_id = datetime.now().strftime('%Y%m%d-%H%M%S')
        print(f"  [Manager] Starting {task_type} run {run_id}")
        with self._lock:
            self._state['running']       = True
            self._state['last_run_type'] = task_type
            self._state['last_run_ts']   = datetime.now().isoformat(timespec='seconds')
            self._save_state()
        try:
            task_file = _DAILY_FILE if task_type == 'daily' else _HOURLY_FILE
            try:
                with open(task_file) as f:
                    task_content = f.read()
            except Exception as e:
                print(f"  [Manager] Cannot read {task_file}: {e}")
                self._write_error_report(run_id, task_type, f"task file unreadable: {e}")
                return

            prompt = self._build_prompt(task_type, run_id, task_content)
            session = self._tmux_session()

            if not self._session_alive(session):
                print(f"  [Manager] tmux session '{session}' not found — skipping run")
                self._write_error_report(run_id, task_type, f"tmux session '{session}' not found")
                return

            pre_count = self._report_count()
            self._send_to_tmux(session, prompt)

            # Poll for a new report entry matching this run_id
            deadline = time.time() + _MAX_WAIT_SECS
            entry = None
            while time.time() < deadline:
                time.sleep(_POLL_INTERVAL)
                entry = self._find_report(run_id)
                if entry:
                    break

            if not entry:
                print(f"  [Manager] Run {run_id} timed out after {_MAX_WAIT_SECS}s")
                self._write_error_report(run_id, task_type, "timed out waiting for Claude response")
                return

            print(f"  [Manager] Run {run_id} complete — severity: {entry.get('severity','?')}")
            severity = entry.get('severity', 'ok')
            if severity in ('elevated', 'warning'):
                with self._lock:
                    self._state['unread_alerts'] = True
                    self._save_state()
            if severity in ('elevated', 'warning'):
                self._send_telegram_alert(task_type, entry)

        finally:
            with self._lock:
                self._state['running'] = False
                self._save_state()

    def _build_prompt(self, task_type: str, run_id: str, task_content: str) -> str:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return (
            f"[MANAGER TASK — {task_type.upper()}] Run ID: {run_id} | Time: {now}\n\n"
            f"{task_content}\n\n"
            f"Your run_id for the report is: {run_id}"
        )

    def _send_to_tmux(self, session: str, text: str):
        try:
            subprocess.run(['tmux', 'send-keys', '-t', session, '-l', text], check=True)
            subprocess.run(['tmux', 'send-keys', '-t', session, 'Enter'], check=True)
        except Exception as e:
            print(f"  [Manager] tmux send error: {e}")

    def _session_alive(self, session: str) -> bool:
        try:
            r = subprocess.run(['tmux', 'has-session', '-t', session],
                               capture_output=True, timeout=3)
            return r.returncode == 0
        except Exception:
            return False

    def _tmux_session(self) -> str:
        return str(getattr(self.config, 'TELEGRAM_TMUX_SESSION', 'claude-gateway') or 'claude-gateway')

    def _report_count(self) -> int:
        try:
            with open(_REPORTS_FILE) as f:
                return sum(1 for l in f if l.strip())
        except FileNotFoundError:
            return 0

    def _find_report(self, run_id: str):
        try:
            with open(_REPORTS_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        if e.get('run_id') == run_id:
                            return e
                    except Exception:
                        pass
        except FileNotFoundError:
            pass
        return None

    def _write_error_report(self, run_id: str, task_type: str, reason: str):
        entry = {
            'ts':       datetime.now().isoformat(timespec='seconds'),
            'task':     task_type,
            'run_id':   run_id,
            'severity': 'elevated',
            'summary':  f"Manager run failed: {reason}",
            'findings': [reason],
        }
        try:
            with open(_REPORTS_FILE, 'a') as f:
                f.write(json.dumps(entry) + '\n')
        except Exception as e:
            print(f"  [Manager] Failed to write error report: {e}")
        with self._lock:
            self._state['unread_alerts'] = True
            self._save_state()

    def _send_telegram_alert(self, task_type: str, entry: dict):
        bot_token = str(getattr(self.config, 'TELEGRAM_BOT_TOKEN', '') or '').strip()
        chat_id   = str(getattr(self.config, 'TELEGRAM_CHAT_ID',   '') or '').strip()
        if not bot_token or not chat_id:
            return
        summary  = entry.get('summary', '')
        findings = entry.get('findings', [])
        text = (
            f"[Manager — {task_type}] {entry.get('ts','')}\n"
            f"{summary}"
        )
        if findings:
            text += "\n\n" + "\n".join(f"• {f}" for f in findings[:10])
        try:
            import urllib.request
            url  = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            data = json.dumps({'chat_id': chat_id, 'text': text}).encode()
            req  = urllib.request.Request(url, data=data,
                                          headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(req, timeout=10)
            print(f"  [Manager] Telegram alert sent for {task_type} run")
        except Exception as e:
            print(f"  [Manager] Telegram send failed: {e}")

    def _doc_path(self, name: str):
        return {
            'constitution': _CONST_FILE,
            'hourly':       _HOURLY_FILE,
            'daily':        _DAILY_FILE,
        }.get(name)
