"""Fleet Manager Engine — scheduled Claude-driven fleet health checks."""

import json
import os
import subprocess
import threading
import time
from datetime import datetime, date, timedelta


_BASE = os.path.dirname(os.path.abspath(__file__))
_STATE_FILE   = os.path.join(_BASE, 'manager_state.json')
_REPORTS_FILE = os.path.join(_BASE, 'manager_reports.jsonl')
_HOURLY_FILE  = os.path.join(_BASE, 'hourly.md')
_DAILY_FILE   = os.path.join(_BASE, 'daily.md')
_CONST_FILE   = os.path.join(_BASE, 'SYSTEM_MANIFEST.md')

_DEFAULT_STATE = {
    'enabled':              False,
    'daily_time':           '06:00',
    'check_interval_hours': 1,      # 1, 2, 4, 8, or 12
    'last_check':           None,   # "YYYY-MM-DD-HH" (slot-aligned)
    'last_daily':           None,   # "YYYY-MM-DD"
    'unread_alerts':        False,
    'running':              False,
    'last_run_type':        None,
    'last_run_ts':          None,
}

_MAX_WAIT_SECS   = 600   # 10 min timeout waiting for Claude's report
_POLL_INTERVAL   = 5     # seconds between polls
_LOOP_INTERVAL   = 30    # seconds between schedule checks
_REPORTS_RETAIN_DAYS = 7 # rotation horizon for manager_reports.jsonl


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
        # Prune reports older than the retention horizon on every startup,
        # so the file stays bounded even if the gateway runs for months.
        self._prune_reports()
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
                now = datetime.now()
                self._state['last_check'] = self._check_slot_key(now)
                self._state['last_daily'] = now.strftime('%Y-%m-%d')
            self._save_state()

    def set_daily_time(self, t: str):
        with self._lock:
            self._state['daily_time'] = t
            self._save_state()

    def set_check_interval(self, hours: int):
        valid = (1, 2, 4, 6, 8, 12)
        hours = hours if hours in valid else 1
        with self._lock:
            self._state['check_interval_hours'] = hours
            # Recompute last_check under the new interval so the loop doesn't
            # immediately fire due to a slot-key mismatch after the change.
            self._state['last_check'] = self._check_slot_key(datetime.now())
            self._save_state()

    def acknowledge(self):
        with self._lock:
            self._state['unread_alerts'] = False
            self._save_state()

    def run_now(self, task_type: str):
        """Trigger an immediate run (non-blocking — runs in background thread)."""
        def _run_and_mark():
            self._run_task(task_type)
            now = datetime.now()
            with self._lock:
                if task_type == 'daily':
                    self._state['last_daily'] = now.strftime('%Y-%m-%d')
                    self._state['last_check'] = self._check_slot_key(now)
                else:
                    self._state['last_check'] = self._check_slot_key(now)
                self._save_state()
        threading.Thread(target=_run_and_mark, daemon=True).start()

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

    def _check_slot_key(self, now):
        """Return a slot key aligned to the check interval (e.g. '2026-05-09-04' for a 4h window)."""
        interval = self._state.get('check_interval_hours', 1)
        slot_hour = (now.hour // interval) * interval
        return now.strftime('%Y-%m-%d-') + f'{slot_hour:02d}'

    def _loop(self):
        last_prune_hour = -1
        while not self._stop.wait(_LOOP_INTERVAL):
            # Prune older-than-7-day reports once per clock hour, regardless
            # of whether the manager is enabled — file stays bounded even on
            # long-running gateways where the manager isn't actively running.
            cur_hour = datetime.now().hour
            if cur_hour != last_prune_hour:
                self._prune_reports()
                last_prune_hour = cur_hour
            with self._lock:
                enabled = self._state.get('enabled')
                running = self._state.get('running')
            if not enabled or running:
                continue
            now        = datetime.now()
            day_key    = now.strftime('%Y-%m-%d')
            check_slot = self._check_slot_key(now)
            daily_time = self._state.get('daily_time', '06:00')
            with self._lock:
                last_check = self._state.get('last_check')
                last_daily = self._state.get('last_daily')

            # Daily fires first; if it fires, also mark the check slot so the
            # smaller check is skipped when both would coincide.
            if now.strftime('%H:%M') >= daily_time and last_daily != day_key:
                self._run_task('daily')
                with self._lock:
                    self._state['last_daily'] = day_key
                    self._state['last_check'] = check_slot  # suppress coinciding check
                    self._save_state()
            elif last_check != check_slot:
                self._run_task('hourly')
                with self._lock:
                    self._state['last_check'] = check_slot
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
            fix = entry.get('fix', '').strip()
            if fix:
                self._apply_fix(fix, entry)

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

    def _prune_reports(self):
        """Keep only reports newer than _REPORTS_RETAIN_DAYS. Atomic rewrite.

        Entries with unparseable timestamps are kept (never silently delete
        data we can't classify). Cheap — runs on a small JSONL file.
        """
        try:
            cutoff = datetime.now() - timedelta(days=_REPORTS_RETAIN_DAYS)
        except Exception:
            return
        kept = []
        dropped = 0
        try:
            with open(_REPORTS_FILE) as f:
                for line in f:
                    line = line.rstrip('\n')
                    if not line.strip():
                        continue
                    keep = True
                    try:
                        e = json.loads(line)
                        ts_str = str(e.get('ts', '')).replace('Z', '')
                        ts = datetime.fromisoformat(ts_str)
                        if ts < cutoff:
                            keep = False
                    except Exception:
                        pass  # unparseable → keep
                    if keep:
                        kept.append(line)
                    else:
                        dropped += 1
        except FileNotFoundError:
            return
        except Exception as e:
            print(f"  [Manager] Prune read error: {e}")
            return
        if dropped == 0:
            return
        try:
            tmp = _REPORTS_FILE + '.tmp'
            with open(tmp, 'w') as f:
                for line in kept:
                    f.write(line + '\n')
            os.replace(tmp, _REPORTS_FILE)
            print(f"  [Manager] Pruned {dropped} report(s) older than {_REPORTS_RETAIN_DAYS}d "
                  f"(kept {len(kept)})", flush=True)
        except Exception as e:
            print(f"  [Manager] Prune write error: {e}")

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
        self._prune_reports()
        with self._lock:
            self._state['unread_alerts'] = True
            self._save_state()

    _FIX_ACTIONS = {
        'restart-darkice': ['systemctl', 'restart', 'darkice'],
        'restart-mumble':  ['systemctl', 'restart', 'mumble-server-gw1'],
        'restart-sdrplay': ['systemctl', 'restart', 'sdrplay'],
        'restart-gateway': ['systemctl', 'restart', 'radio-gateway'],
    }

    def _apply_fix(self, fix: str, entry: dict):
        cmd = self._FIX_ACTIONS.get(fix)
        if not cmd:
            print(f"  [Manager] Unknown fix '{fix}' — ignored")
            return
        print(f"  [Manager] Applying fix: {fix}")
        self._send_fix_telegram(fix, entry)  # send before restart-gateway kills us
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=30)
            result = 'ok' if r.returncode == 0 else f'exit {r.returncode}'
            print(f"  [Manager] Fix '{fix}': {result}")
        except Exception as e:
            print(f"  [Manager] Fix '{fix}' error: {e}")

    def _send_fix_telegram(self, fix: str, entry: dict):
        bot_token = str(getattr(self.config, 'TELEGRAM_BOT_TOKEN', '') or '').strip()
        chat_id   = str(getattr(self.config, 'TELEGRAM_CHAT_ID',   '') or '').strip()
        if not bot_token or not chat_id:
            return
        text = (
            f"[Manager — auto-fix] {entry.get('ts','')}\n"
            f"Action: {fix}\n"
            f"{entry.get('summary','')}"
        )
        try:
            import urllib.request
            url  = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            data = json.dumps({'chat_id': chat_id, 'text': text}).encode()
            req  = urllib.request.Request(url, data=data,
                                          headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(req, timeout=10)
            print(f"  [Manager] Fix Telegram sent: {fix}")
        except Exception as e:
            print(f"  [Manager] Fix Telegram failed: {e}")

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
