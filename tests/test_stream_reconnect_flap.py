"""Reproduce the 2026-08-19 Broadcastify reconnect flap.

Models the real failure: a DNS outage makes _connect() stall past the
watchdog's patience, the watchdog releases the in-flight flag, and more
workers pile in. A worker that connects while a peer is mid-flight has its
fresh connection closed by that peer, whose own connect is then refused
"403 Mountpoint in use".
"""
import threading
import time
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import audio_sources

S = audio_sources.StreamOutputSource

class Fake:
    """Minimal stand-in wired to the real reconnect methods under test."""
    _trigger_reconnect = S._trigger_reconnect
    # The worker now confirms a connect by watching bytes leave, so the real
    # method has to come along too — stubbing it would test a path the
    # gateway does not run. Note the AttributeError from omitting it is
    # raised inside a daemon worker and SWALLOWED: the suite still exited 0
    # while the code under test was dead. See [[feedback_silent_attribute_misses]].
    _confirm_bytes_moving = S._confirm_bytes_moving

    def __init__(self, dns_down_for, legacy=False):
        self.legacy = legacy
        self.connected = False
        self._encoder = None
        self._shutdown = False
        self._was_connected = True
        self._reconnecting = False
        self._reconnect_lock = threading.Lock()
        self._connect_lock = threading.Lock()
        self._connect_lock_wait = 45.0
        self._reconnect_epoch = 0
        self._reconnect_superseded = 0
        self._reconnect_wedged = 0
        self._reconnect_wedge_timeout = 1.0   # scaled down from 30s
        self._reconnect_count = 0
        self._reconnect_backoff = 0.2         # scaled down from 5s
        self._mount_wait = 0.5                # scaled down from 15s
        self._mount_in_use = False
        self._connect_confirm = 0.5           # scaled down from 5s
        self._bytes_sent = 0
        self._start = time.monotonic()
        self._dns_down_for = dns_down_for
        # server-side mount state: who currently holds it
        self._mount_holder = None
        self._mount_lock = threading.Lock()
        self.log = []
        self.mount_403 = 0
        self.successes = 0

    def _note_error(self, e): pass

    def close(self):
        self.connected = False
        with self._mount_lock:
            # releasing the mount is not instantaneous server-side
            if self._mount_holder is not None:
                self._mount_holder = 'draining'
                threading.Timer(0.4, self._clear_mount).start()

    def _clear_mount(self):
        with self._mount_lock:
            if self._mount_holder == 'draining':
                self._mount_holder = None

    def _connect(self):
        # DNS stall — unbounded by socket timeouts, exactly as getaddrinfo is
        if time.monotonic() - self._start < self._dns_down_for:
            time.sleep(self._dns_down_for - (time.monotonic() - self._start))
        with self._mount_lock:
            if self._mount_holder is not None:
                self.mount_403 += 1
                self._mount_in_use = True
                self.connected = False
                return
            self._mount_holder = 'me'
        self._mount_in_use = False
        # Zeroed per connection exactly as the real _connect does, so
        # _confirm_bytes_moving can never credit this attempt with the
        # previous connection's traffic.
        self._bytes_sent = 0
        self.connected = True
        self.successes += 1
        # Stand in for the reader thread: on a healthy link the first MP3
        # chunk reaches the socket well inside the confirmation window. This
        # models the LINK working, which is the scenario this test is about —
        # a link that connects but pushes nothing is test_stream_dead_uplink.
        threading.Timer(0.05, self._push_bytes).start()

    def _push_bytes(self):
        if self.connected:
            self._bytes_sent += 4096


def legacy_trigger(self):
    """The pre-fix code path, transcribed, for an A/B baseline."""
    if not self._was_connected or self._shutdown:
        return
    with self._reconnect_lock:
        if self._reconnecting:
            return
        self._reconnecting = True
        self._reconnect_count += 1
        count = self._reconnect_count

    def _auto():
        try:
            delay = self._mount_wait if self._mount_in_use else self._reconnect_backoff
            time.sleep(delay)
            try: self.close()
            except Exception: pass
            try: self._connect()
            except Exception: pass
        finally:
            self._reconnecting = False

    w = threading.Thread(target=_auto, daemon=True); w.start()
    def _wd():
        w.join(timeout=self._reconnect_wedge_timeout)
        if w.is_alive():
            self._reconnect_wedged += 1
            self._reconnecting = False
    threading.Thread(target=_wd, daemon=True).start()


def run(legacy, dns_down_for=6.0, duration=12.0):
    f = Fake(dns_down_for, legacy=legacy)
    if legacy:
        f._trigger_reconnect = legacy_trigger.__get__(f, Fake)
    stop = time.monotonic() + duration
    # send_audio hammers the trigger whenever the stream is down
    while time.monotonic() < stop:
        if not f.connected:
            f._trigger_reconnect()
        time.sleep(0.05)
    time.sleep(1.5)
    return f

for legacy in (True, False):
    f = run(legacy)
    label = "LEGACY (pre-fix)" if legacy else "FIXED"
    print(f"{label:>18}: attempts={f._reconnect_count:5d}  "
          f"403_mount_in_use={f.mount_403:4d}  connects={f.successes:4d}  "
          f"wedged={f._reconnect_wedged:3d}  superseded={f._reconnect_superseded:4d}  "
          f"final_connected={f.connected}")
