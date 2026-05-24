"""Cloudflare quick tunnel manager (ProcessSupervisor-backed).

Launches `cloudflared tunnel --url http://localhost:PORT` via ProcessSupervisor
wrapped in `systemd-run --user --scope`. The transient scope sits outside the
gateway's cgroup, so the tunnel survives gateway restarts:

  gateway --(spawns)--> systemd-run --(creates scope)--> cloudflared
                            ↑                                ↑
                       in gateway cgroup              in sibling scope cgroup
                       (dies with gateway)            (survives gateway death)

On startup:
  - If cloudflared is already running, ProcessSupervisor adopts it.
  - Otherwise systemd-run launches a fresh one.
  - URL is captured from cloudflared's stdout and cached to URL_FILE.

On gateway restart:
  - persist_across_restart=True: supervisor unhooks without killing the wrapper.
  - The wrapper dies with the gateway cgroup; the scope (and cloudflared) survive.
  - Next gateway start re-adopts the running cloudflared.

The 15-minute health-check loop is unchanged — it stays here because tunnel
liveness (URL still routes) is a tunnel concern, not a generic process concern.
"""

import os
import re
import threading
import time

from process_supervisor import pgrep_first


class CloudflareTunnel:
    """Cloudflare quick tunnel — free public HTTPS access with no port forwarding."""

    URL_FILE = '/tmp/cloudflare_tunnel_url'
    LOG_FILE = '/tmp/cloudflared_output.log'
    SUPERVISOR_NAME = 'cloudflared'
    HEALTH_CHECK_INTERVAL = 900  # seconds between liveness checks (15 min)

    _URL_RE = re.compile(r'(https://[a-zA-Z0-9-]+\.trycloudflare\.com)')

    def __init__(self, config, on_url_changed=None, supervisor=None):
        self.config = config
        self._url = None
        self._on_url_changed = on_url_changed
        self._supervisor = supervisor          # set via attach_supervisor() if None
        self._health_thread = None

    def attach_supervisor(self, supervisor):
        """Set the ProcessSupervisor after construction (gateway init order)."""
        self._supervisor = supervisor

    # ── Lifecycle ───────────────────────────────────────────────────

    def start(self):
        if self._supervisor is None:
            print("  [Tunnel] No ProcessSupervisor attached — cannot start")
            return
        port = int(getattr(self.config, 'WEB_CONFIG_PORT', 8080))

        # Seed the cached URL from the file/log so callers see something
        # immediately, even before adoption settles. But only if cloudflared
        # is actually running — otherwise the cache is from a previous, dead
        # process and is stale; using it races the fresh URL we're about to
        # capture from stdout and can clobber GDrive with the old value.
        if pgrep_first('cloudflared'):
            self._url = self._read_cached_url() or self._scan_log_for_url()
            if self._url:
                print(f"  [Tunnel] Cached URL: {self._url}")
        else:
            self._url = None
            try:
                os.unlink(self.URL_FILE)
            except OSError:
                pass

        # systemd-run --user --scope puts cloudflared in a sibling cgroup
        # so it survives gateway restarts. Fall back to plain cloudflared
        # if systemd-run is missing.
        import shutil
        if shutil.which('systemd-run'):
            argv = ['systemd-run', '--user', '--scope',
                    '--unit=cloudflared-tunnel',
                    'cloudflared', 'tunnel',
                    '--url', f'http://localhost:{port}']
        else:
            argv = ['cloudflared', 'tunnel',
                    '--url', f'http://localhost:{port}']

        try:
            self._supervisor.add(
                self.SUPERVISOR_NAME, argv,
                restart=True,
                backoff=(5, 60),
                persist_across_restart=True,
                adopt_existing=lambda: pgrep_first('cloudflared'),
                stdout_handler=self._on_log_line,
                log_file=self.LOG_FILE,
            )
            print(f"  [Tunnel] Cloudflare tunnel supervised for port {port}")
        except ValueError:
            # Already registered (start() called twice) — restart instead
            self._supervisor.restart(self.SUPERVISOR_NAME)

        self._start_health_check()

    def stop(self):
        # Intentional no-op: persist_across_restart=True keeps the tunnel
        # alive across gateway restarts. supervisor.shutdown_all() honours
        # the flag and only detaches its watcher thread.
        pass

    def get_url(self):
        if self._url:
            return self._url
        self._url = self._read_cached_url()
        return self._url

    # ── stdout handler — URL capture ────────────────────────────────

    def _on_log_line(self, line):
        m = self._URL_RE.search(line)
        if not m:
            return
        url = m.group(1)
        if url == self._url:
            return
        old_url = self._url
        self._url = url
        try:
            with open(self.URL_FILE, 'w') as f:
                f.write(url)
        except Exception:
            pass
        print(f"  [Tunnel] Public URL: {url}")
        if old_url and self._on_url_changed:
            try:
                self._on_url_changed(url)
            except Exception as e:
                print(f"  [Tunnel] on_url_changed callback error: {e}")

    # ── Cached-URL helpers ─────────────────────────────────────────

    def _read_cached_url(self):
        try:
            with open(self.URL_FILE, 'r') as f:
                u = f.read().strip()
            return u or None
        except FileNotFoundError:
            return None

    def _scan_log_for_url(self):
        try:
            with open(self.LOG_FILE, 'r') as f:
                m = self._URL_RE.search(f.read())
            if m:
                url = m.group(1)
                try:
                    with open(self.URL_FILE, 'w') as f:
                        f.write(url)
                except Exception:
                    pass
                return url
        except Exception:
            pass
        return None

    # ── Health check (tunnel liveness, not process liveness) ───────

    def _start_health_check(self):
        if self._health_thread and self._health_thread.is_alive():
            return
        self._health_thread = threading.Thread(
            target=self._health_check_loop, daemon=True, name="cf-health")
        self._health_thread.start()

    def _probe_url(self, url):
        """HTTP HEAD probe — any HTTP response means the tunnel itself is alive."""
        import urllib.error
        import urllib.request
        try:
            req = urllib.request.Request(url, method='HEAD')
            urllib.request.urlopen(req, timeout=10)
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            return False

    def _health_check_loop(self):
        """Periodically probe the URL. On expiry, kill cloudflared so the
        supervisor respawns it and a fresh URL is captured from stdout."""
        consecutive_failures = 0
        FAIL_THRESHOLD = 2

        while True:
            time.sleep(self.HEALTH_CHECK_INTERVAL)
            try:
                pid = pgrep_first('cloudflared')
                if pid and self._url:
                    if self._probe_url(self._url):
                        consecutive_failures = 0
                        continue
                    consecutive_failures += 1
                    if consecutive_failures < FAIL_THRESHOLD:
                        print(f"  [Tunnel] URL probe failed "
                              f"({consecutive_failures}/{FAIL_THRESHOLD}), retrying...")
                        continue
                    print(f"  [Tunnel] URL expired — killing cloudflared "
                          f"(supervisor will respawn)")
                    self._kill_cloudflared()
                    consecutive_failures = 0
                    # Clear cached URL so a stale value isn't reported between
                    # the kill and the supervisor's fresh URL capture.
                    self._url = None
                    try:
                        os.unlink(self.URL_FILE)
                    except OSError:
                        pass
                elif not pid:
                    # Not running — supervisor will respawn on its own.
                    consecutive_failures = 0
                    continue
            except Exception as e:
                print(f"  [Tunnel] Health check error: {e}")

    def _kill_cloudflared(self):
        import signal
        import subprocess
        try:
            r = subprocess.run(['pgrep', '-x', 'cloudflared'],
                               capture_output=True, text=True, timeout=5)
            for pid in r.stdout.strip().split('\n'):
                pid = pid.strip()
                if pid:
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            time.sleep(2)
        except Exception:
            pass
