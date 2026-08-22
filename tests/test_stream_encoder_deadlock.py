"""Reproduce the 2026-07-30 encoder-pipe deadlock and prove the fix.

Simulates the exact failure: a child process that stops draining its stdin
(as ffmpeg did once nobody drained its stdout), then hammers PCM at it.
Old code blocked forever in stdin.write(); new code must fail fast.
"""
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import audio_sources  # noqa: E402

S = audio_sources.StreamOutputSource
PCM = b'\x00' * 4800          # one bus tick
FAIL = []


def make_stalled_encoder():
    """Child that never reads stdin — same end state as a wedged ffmpeg."""
    p = subprocess.Popen(['sleep', '3600'],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    fd = p.stdin.fileno()
    import fcntl
    fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) | os.O_NONBLOCK)
    return p


def new_src(enc):
    """A StreamOutputSource with just the attributes these paths touch."""
    s = object.__new__(S)
    s._encoder = enc
    s._encoder_write_timeout = 1.0
    s._encoder_lock = threading.Lock()
    s._reconnect_lock = threading.Lock()
    s._reconnecting = False
    # Added with the 2026-08-19 stale-reader/flap fix. This stub bypasses
    # __init__, so every new instance attribute the reconnect paths touch has
    # to be mirrored here or the whole suite fails on AttributeError inside a
    # caught handler -- which reads as "the fix regressed", not "the stub is
    # stale". Keep in sync with StreamOutputSource.__init__.
    s._connect_lock = threading.Lock()
    s._connect_lock_wait = 45.0
    s._reconnect_epoch = 0
    s._reconnect_superseded = 0
    s._reconnect_wedged = 0
    s._reconnect_wedge_timeout = 30.0
    s._was_connected = True
    s.connected = True
    s._reconnect_count = 0
    s._last_audio_time = 0
    s._last_drop_time = 0
    s._mount_in_use = False
    s._last_error = ''
    s._last_error_time = 0.0
    s._chunk_bytes = 4800
    s._shutdown = False
    s._teardown_intentional = False
    s._supervisor_thread = None
    s._icecast_sock = None
    # Added with the 2026-08-21 dead-uplink fix -- same rule as above.
    s._connect_confirm = 0.5
    s._flow_stale_after = 15.0
    s._bytes_sent = 0
    s._last_bytes_time = 0.0
    return s


def check(name, cond, detail=''):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{' — ' + detail if detail else ''}")
    if not cond:
        FAIL.append(name)


print("\n1. _encoder_write must NOT block forever on a stalled encoder")
enc = make_stalled_encoder()
src = new_src(enc)
t0 = time.monotonic()
ok = True
# Fill the pipe (64 KiB) and keep pushing — old code parks here for ever.
for _ in range(200):
    ok = src._encoder_write(PCM)
    if not ok:
        break
el = time.monotonic() - t0
check("returns False instead of blocking", ok is False, f"returned {ok}")
check("respects the deadline", el < 3.0, f"took {el:.2f}s")
enc.kill()

print("\n2. _teardown_encoder must release a writer stuck in the lock")
enc = make_stalled_encoder()
src = new_src(enc)
released = threading.Event()


def writer():
    # Mimics the sink-drain thread: takes the lock, then writes.
    with src._encoder_lock:
        for _ in range(200):
            if not src._encoder_write(PCM):
                break
    released.set()


w = threading.Thread(target=writer, daemon=True)
w.start()
time.sleep(0.3)
# THE regression check. Under the old code the writer held _encoder_lock
# forever (blocking write into a stalled pipe), so the keepalive thread parked
# here for 15h and never re-checked `self.connected`. Now the writer's own
# deadline makes it let go, so the lock must become available quickly.
t0 = time.monotonic()
got = src._encoder_lock.acquire(timeout=3.0)
el = time.monotonic() - t0
check("lock does not stay held by a wedged writer", got is True,
      f"still locked after {el:.2f}s" if not got else f"freed in {el:.2f}s")
if got:
    src._encoder_lock.release()

# And the acquire itself is bounded, so keepalive can never park indefinitely
# even if some future writer does hold on.
held = threading.Lock()
held.acquire()
t0 = time.monotonic()
got2 = held.acquire(timeout=1.0)
el2 = time.monotonic() - t0
check("bounded acquire gives up rather than parking", got2 is False,
      f"took {el2:.2f}s")
check("writer thread escapes the wedged encoder", released.wait(timeout=5),
      "still stuck" if not released.is_set() else "")
src._teardown_encoder()
check("_teardown_encoder clears the handle", src._encoder is None)
check("encoder process is actually dead", enc.poll() is not None,
      f"poll={enc.poll()}")

print("\n3. _trigger_reconnect must fire exactly once for concurrent callers")
enc = make_stalled_encoder()
src = new_src(enc)
src.connected = False
src._mount_wait = 0.05
src._reconnect_backoff = 0.05
calls = []
src.close = lambda: calls.append('close')
src._connect = lambda: calls.append('connect')
src._note_error = lambda m: None
threads = [threading.Thread(target=src._trigger_reconnect) for _ in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("only one attempt counted", src._reconnect_count == 1,
      f"count={src._reconnect_count}")
time.sleep(1.0)
check("reconnect worker ran once", calls.count('connect') == 1, f"calls={calls}")
check("flag released after the attempt", src._reconnecting is False)
enc.kill()

print("\n4. supervisor must recover a stream nothing else is driving")
enc = make_stalled_encoder()
src = new_src(enc)
src.connected = False        # dropped
src._mount_wait = 0.05
src._reconnect_backoff = 0.05
src._note_error = lambda m: None
reconnected = threading.Event()
src.close = lambda: None
src._connect = lambda: reconnected.set()
S.SUPERVISOR_INTERVAL = 0.2
threading.Thread(target=src._supervisor_loop, daemon=True).start()
check("supervisor triggers reconnect unaided", reconnected.wait(timeout=5))
check("supervisor reaped the live encoder", src._encoder is None)
src._shutdown = True   # stop this supervisor leaking into later sections
time.sleep(0.4)
enc.kill()

print("\n5. recovery must not fight a deliberate shutdown")
enc = make_stalled_encoder()
src = new_src(enc)
src.connected = False
src._shutdown = True         # cleanup() has run
src._mount_wait = 0.05
src._reconnect_backoff = 0.05
src._note_error = lambda m: None
resurrect = []
src.close = lambda: None
src._connect = lambda: resurrect.append('connect')
src._trigger_reconnect()
time.sleep(0.5)
check("shutdown suppresses reconnect", resurrect == [], f"calls={resurrect}")
check("no attempt counted", src._reconnect_count == 0)

S.SUPERVISOR_INTERVAL = 0.2
t = threading.Thread(target=src._supervisor_loop, daemon=True)
t.start()
time.sleep(0.8)
check("supervisor exits on shutdown", not t.is_alive())
check("supervisor did not resurrect the stream", resurrect == [], f"calls={resurrect}")
enc.kill()

print("\n6. a deliberate close() must not queue a redundant reconnect")
enc = make_stalled_encoder()
src = new_src(enc)
src._mount_wait = 0.05
src._reconnect_backoff = 0.05
src._note_error = lambda m: None
extra = []
src._connect = lambda: extra.append('connect')
src.close()                  # sets _teardown_intentional
check("close() flags the teardown as ours", src._teardown_intentional is True)
# Reader-thread cleanup logic: it must stay quiet for an intentional teardown.
if not src._teardown_intentional:
    src._trigger_reconnect()
time.sleep(0.4)
check("no redundant reconnect queued", extra == [], f"calls={extra}")
check("no attempt counted", src._reconnect_count == 0)
enc.kill()

print(f"\n{'ALL PASS' if not FAIL else 'FAILURES: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
