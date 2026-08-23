"""TX audio dropped before it reaches the radio must be countable.

put_audio() feeds a deque(maxlen=16) that silently discards its oldest element
when the writer thread falls behind, and it bypasses
BusManager._enqueue_sink -- so aioc_tx never appears in /sinkstats and a stall
or an overflow was completely invisible. On 2026-08-22 a 50/50 mark-space
stutter on TX could not be attributed to queue starvation vs USB contention
with the RX reader, because there was no number for either.
"""
import collections
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from plugins.th9800 import TH9800Plugin  # noqa: E402

FAIL = []


def check(name, cond, detail=''):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{' — ' + detail if detail else ''}")
    if not cond:
        FAIL.append(name)


class Cfg:
    OUTPUT_VOLUME = 1.0
    VERBOSE_LOGGING = False


class Stream:
    """Output stream whose write() can be made slow or throw."""
    def __init__(self, delay=0.0, raises=False):
        self.delay = delay
        self.raises = raises
        self.writes = []

    def write(self, pcm, exception_on_overflow=None):
        if self.delay:
            time.sleep(self.delay)
        if self.raises:
            raise IOError('device busy')
        self.writes.append(pcm)


def new_plugin(stream=None, maxlen=16):
    p = object.__new__(TH9800Plugin)
    p._config = Cfg
    p._output_stream = stream if stream is not None else Stream()
    p._tx_queue = collections.deque(maxlen=maxlen)
    p._stream_trace = None
    p.tx_muted = False
    p.tx_audio_boost = 1.0
    p.tx_audio_level = 0
    p._tx_enqueued = 0
    p._tx_drops = 0
    p._tx_written = 0
    p._tx_depth_max = 0
    p._tx_write_ms_max = 0.0
    p._tx_write_ms_total = 0.0
    p._tx_write_errors = 0
    p._tx_drops_logged = False
    return p


PCM = b'\x01\x00' * 1200


print("\n1. normal enqueue is counted, nothing dropped")
p = new_plugin()
for _ in range(5):
    p.put_audio(PCM)
check("enqueued counted", p._tx_enqueued == 5, f"n={p._tx_enqueued}")
check("no drops", p._tx_drops == 0)
check("queue holds them", len(p._tx_queue) == 5)

print("\n2. overflow is COUNTED, not silent")
# The deque displaces its oldest element on append without telling anyone,
# so the drop must be detected before the append.
p = new_plugin(maxlen=4)
for _ in range(10):
    p.put_audio(PCM)
check("drops counted", p._tx_drops == 6, f"drops={p._tx_drops} (10 sent, 4 fit)")
check("all enqueue attempts counted", p._tx_enqueued == 10, f"n={p._tx_enqueued}")
check("queue still bounded", len(p._tx_queue) == 4)
check("peak depth recorded", p._tx_depth_max == 4, f"max={p._tx_depth_max}")

print("\n3. the first drop is logged exactly once")
p = new_plugin(maxlen=2)
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    for _ in range(8):
        p.put_audio(PCM)
out = buf.getvalue()
check("logged once", out.count('TX queue full') == 1, f"count={out.count('TX queue full')}")
check("names the cause", 'not keeping up' in out, out.strip()[:80])
check("later drops still counted", p._tx_drops == 6, f"drops={p._tx_drops}")

print("\n4. a slow hardware write is visible")
st = Stream(delay=0.03)          # 30ms, against a 50ms bus tick
p = new_plugin(st)
p.put_audio(PCM)
t = threading.Thread(target=p._tx_writer_loop, daemon=True)
t.start()
time.sleep(0.25)
p._output_stream = None          # ends the loop
t.join(timeout=1.0)
check("write counted", p._tx_written >= 1, f"n={p._tx_written}")
check("slow write shows in ms_max", p._tx_write_ms_max >= 25,
      f"ms_max={p._tx_write_ms_max:.1f}")

print("\n5. a failing write is counted, and still timed")
st = Stream(delay=0.02, raises=True)
p = new_plugin(st)
p.put_audio(PCM)
t = threading.Thread(target=p._tx_writer_loop, daemon=True)
t.start()
time.sleep(0.2)
p._output_stream = None
t.join(timeout=1.0)
check("error counted", p._tx_write_errors >= 1, f"n={p._tx_write_errors}")
check("not counted as written", p._tx_written == 0, f"n={p._tx_written}")
check("a write that blocks then raises is still timed", p._tx_write_ms_max >= 15,
      f"ms_max={p._tx_write_ms_max:.1f} — this is the USB-contention case")

print("\n6. counters reach get_status()")
p = new_plugin(maxlen=2)
for _ in range(5):
    p.put_audio(PCM)
p.name = 'th9800'
p._aioc_available = True
p._ptt_active = False
p._ptt_ok = True
p._ptt_failures = 0
p._ptt_last_error = ''
p._ptt_method = 'software'
p.audio_level = 0
p.muted = False
p._stream_restart_count = 0
p._cat_client = None
d = p.get_status()
for k in ('tx_enqueued', 'tx_drops', 'tx_written', 'tx_depth_max',
          'tx_depth_now', 'tx_write_ms_max', 'tx_write_ms_avg',
          'tx_write_errors'):
    check(f"status exposes {k}", k in d)
check("status drops match", d['tx_drops'] == 3, f"{d['tx_drops']}")

print("\n7. muted TX still costs nothing and counts nothing")
p = new_plugin()
p.tx_muted = True
p.put_audio(PCM)
check("muted: not enqueued", p._tx_enqueued == 0 and len(p._tx_queue) == 0)

print(f"\n{'ALL PASS' if not FAIL else 'FAILURES: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
