"""Reproduce the 2026-08-21 dead-uplink stall and prove the fix.

The uplink stalled for ~10 minutes. Ten reconnect attempts each completed
their Icecast SOURCE handshake, and each logged "Reconnected successfully",
while rg_stream_bytes_sent_total sat at a flat +0 for 2.5 of those minutes.
TCP connected, Icecast accepted, not one payload byte moved. The 30s health
check agreed and printed "Stream recovered" five times, emailing each one.

The bug was never in the recovery machinery -- that worked. It was that
`connected` means "the handshake was accepted" and three callers read it as
"the stream is up". This pins the difference.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import audio_sources  # noqa: E402

S = audio_sources.StreamOutputSource
FAIL = []


def check(name, cond, detail=''):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{' — ' + detail if detail else ''}")
    if not cond:
        FAIL.append(name)


def new_src(first_byte_after=None):
    """A source whose handshake always succeeds.

    `first_byte_after` is when the reader thread manages to push its first
    chunk, in seconds -- None models the dead uplink: connected, pushing
    nothing, for ever.
    """
    s = object.__new__(S)
    s.connected = True
    s._shutdown = False
    s._bytes_sent = 0
    s._last_bytes_time = time.monotonic()
    s._connect_confirm = 0.5          # scaled down from 5s
    s._flow_stale_after = 1.0         # scaled down from 15s
    if first_byte_after is not None:
        def _push():
            s._bytes_sent += 4096
            s._last_bytes_time = time.monotonic()
        threading.Timer(first_byte_after, _push).start()
    return s


print("\n1. the stall itself: handshake accepted, nothing on the wire")
src = new_src(first_byte_after=None)
t0 = time.monotonic()
ok = src._confirm_bytes_moving(1)
el = time.monotonic() - t0
check("NOT reported as a successful reconnect", ok is False)
check("connected still says the handshake was fine", src.connected is True,
      "which is exactly why it could not be trusted alone")
check("waited the full confirmation window", 0.45 <= el <= 0.9, f"{el:.2f}s")
check("no bytes were credited to this attempt", src._bytes_sent == 0)

print("\n2. a working link is still reported as recovered")
src = new_src(first_byte_after=0.1)
t0 = time.monotonic()
ok = src._confirm_bytes_moving(2)
el = time.monotonic() - t0
check("reported as a successful reconnect", ok is True)
check("returned as soon as bytes moved, not at the deadline", el < 0.4,
      f"{el:.2f}s")

print("\n3. a quiet radio channel must not read as a dead uplink")
# The keepalive feeds the encoder every 50ms whether or not anyone is
# talking, so MP3 frames flow on a silent channel exactly as they do on a
# busy one. If this ever fails, the confirmation window is too tight.
src = new_src(first_byte_after=0.25)     # ~1 chunk/sec, scaled
check("silence still confirms", src._confirm_bytes_moving(3) is True)

print("\n4. bytes cannot be inherited from the previous connection")
# _connect() zeroes _bytes_sent; if it ever stops doing so, a dead uplink
# would be confirmed by the PREVIOUS connection's traffic and the bug is
# back, silently.
src = new_src(first_byte_after=None)
src._bytes_sent = 999999                 # as if left over from last time
check("a stale counter would confirm a dead link",
      src._confirm_bytes_moving(4) is True,
      "so the zeroing in _connect() is load-bearing")
src_zeroed = new_src(first_byte_after=None)
check("zeroed per connection, the dead link is caught",
      src_zeroed._confirm_bytes_moving(4) is False)

print("\n5. mid-confirmation drop bails out instead of waiting")
src = new_src(first_byte_after=None)
threading.Timer(0.1, lambda: setattr(src, 'connected', False)).start()
t0 = time.monotonic()
ok = src._confirm_bytes_moving(5)
el = time.monotonic() - t0
check("not reported as recovered", ok is False)
check("returned early", el < 0.4, f"{el:.2f}s")

print("\n6. shutdown is not something to confirm")
src = new_src(first_byte_after=0.05)
src._shutdown = True
check("returns immediately, no success claimed",
      src._confirm_bytes_moving(6) is False)

print("\n7. data_flowing: what the 30s health check now asks")
src = new_src(first_byte_after=None)
src._last_bytes_time = time.monotonic()
check("fresh connection counts as flowing", src.data_flowing is True)
src._last_bytes_time = time.monotonic() - 5.0     # > _flow_stale_after
check("stalled past the staleness window is DOWN", src.data_flowing is False,
      "a mount pushing nothing is down, and now alerts as down")
src._last_bytes_time = time.monotonic()
src.connected = False
check("disconnected is down regardless of recent bytes",
      src.data_flowing is False)
src = new_src(first_byte_after=None)
src._last_bytes_time = 0.0
check("never sent a byte is not 'flowing'", src.data_flowing is False)

print(f"\n{'ALL PASS' if not FAIL else 'FAILURES: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
