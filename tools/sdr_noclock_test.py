#!/usr/bin/env python3
"""
Test removing the self-clock: let SDR data drive the loop timing.

Strategy: SDR.get_audio() uses blocking get(timeout=0.06).
When queue has data → returns instantly.
When queue empty → blocks up to 60ms (next ALSA batch arrives within ~50ms).
No self-clock. Loop runs as fast as data arrives.
"""

import threading
import queue
import time
import struct
import math
import sys
import statistics
import numpy as np

AUDIO_RATE = 48000
AUDIO_CHUNK_SIZE = 2400
BUFFER_MULTIPLIER = 4
QUEUE_MAXSIZE = 20
TICK = AUDIO_CHUNK_SIZE / AUDIO_RATE
CHANNELS = 1
BYTES_PER_SAMPLE = 2

WRITE_DEVICE = "hw:6,0"
READ_DEVICE = "hw:6,1"
TEST_DURATION = 30.0


def writer_thread(device, duration, chunk_size, rate, channels):
    import pyaudio
    pa = pyaudio.PyAudio()
    try:
        parts = device.replace("hw:", "").split(",")
        card, dev = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        dev_index = None
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if f"hw:{card},{dev}" in info.get('name', '') and info['maxOutputChannels'] >= channels:
                dev_index = i
                break
        if dev_index is None:
            print("[Writer] Device not found"); return

        stream = pa.open(format=pyaudio.paInt16, channels=channels, rate=rate,
                        output=True, output_device_index=dev_index,
                        frames_per_buffer=chunk_size)
        freq = 1000.0
        samples_written = 0
        t0 = time.monotonic()
        while time.monotonic() - t0 < duration + 1.0:
            samples = [int(16000 * math.sin(2 * math.pi * freq * (samples_written + i) / rate))
                      for i in range(chunk_size)]
            samples_written += chunk_size
            stream.write(struct.pack(f'<{chunk_size}h', *samples))
        stream.stop_stream(); stream.close()
    finally:
        pa.terminate()


def reader_thread_func(stream, chunk_queue, chunk_bytes, read_frames, running):
    while running[0]:
        try:
            raw = stream.read(read_frames, exception_on_overflow=False)
            for i in range(0, len(raw), chunk_bytes):
                chunk = raw[i:i + chunk_bytes]
                if len(chunk) < chunk_bytes:
                    break
                try:
                    chunk_queue.put_nowait(chunk)
                except queue.Full:
                    pass
        except IOError:
            time.sleep(0.01)
        except Exception:
            time.sleep(0.01)


def run_test(name, get_func_factory, use_self_clock):
    import pyaudio

    pa = pyaudio.PyAudio()
    parts = READ_DEVICE.replace("hw:", "").split(",")
    card, dev = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    read_index = None
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if f"hw:{card},{dev}" in info.get('name', '') and info['maxInputChannels'] >= CHANNELS:
            read_index = i; break
    if read_index is None:
        print(f"ERROR: {READ_DEVICE} not found"); pa.terminate(); return

    writer = threading.Thread(target=writer_thread,
                             args=(WRITE_DEVICE, TEST_DURATION, AUDIO_CHUNK_SIZE, AUDIO_RATE, CHANNELS),
                             daemon=True)
    writer.start()
    time.sleep(0.5)

    buffer_size = AUDIO_CHUNK_SIZE * BUFFER_MULTIPLIER
    read_stream = pa.open(format=pyaudio.paInt16, channels=CHANNELS, rate=AUDIO_RATE,
                         input=True, input_device_index=read_index,
                         frames_per_buffer=buffer_size, stream_callback=None)

    chunk_queue = queue.Queue(maxsize=QUEUE_MAXSIZE)
    chunk_bytes = AUDIO_CHUNK_SIZE * CHANNELS * BYTES_PER_SAMPLE
    running = [True]
    reader = threading.Thread(target=reader_thread_func,
                             args=(read_stream, chunk_queue, chunk_bytes,
                                   AUDIO_CHUNK_SIZE * BUFFER_MULTIPLIER, running),
                             daemon=True)
    reader.start()

    # Pre-fill
    prefill_target = BUFFER_MULTIPLIER * 2
    deadline = time.monotonic() + 1.0
    while chunk_queue.qsize() < prefill_target and time.monotonic() < deadline:
        time.sleep(0.01)

    get_func = get_func_factory(chunk_queue)
    results = []
    t0 = time.monotonic()
    _next_tick = time.monotonic()

    while time.monotonic() - t0 < TEST_DURATION:
        if use_self_clock:
            _now = time.monotonic()
            if _next_tick > _now:
                time.sleep(_next_tick - _now)
            elif _now - _next_tick > TICK:
                _next_tick = _now
            _next_tick += TICK

        iter_start = time.monotonic()
        qsize = chunk_queue.qsize()
        raw = get_func()
        got_data = raw is not None
        iter_ms = (time.monotonic() - iter_start) * 1000
        results.append({'time': time.monotonic() - t0, 'got_data': got_data,
                       'qsize': qsize, 'iter_ms': iter_ms})

    running[0] = False
    reader.join(timeout=1.0)
    read_stream.stop_stream(); read_stream.close()
    pa.terminate(); writer.join(timeout=2.0)

    # Analysis
    total = len(results)
    got = sum(1 for r in results if r['got_data'])
    empty = total - got
    gaps = []; g = 0
    for r in results:
        if not r['got_data']: g += 1
        else:
            if g > 0: gaps.append(g)
            g = 0
    if g > 0: gaps.append(g)
    gap_ms = [x * TICK * 1000 for x in gaps] if gaps else [0]
    qdepths = [r['qsize'] for r in results]
    times = [r['time'] for r in results]
    intervals = [times[i+1]-times[i] for i in range(len(times)-1)] if len(times) > 1 else [0.05]

    print(f"\n{'─'*65}")
    print(f"  {name}")
    print(f"{'─'*65}")
    print(f"  Ticks: {total}  |  Audio: {got} ({100*got/total:.2f}%)  |  Empty: {empty}")
    print(f"  Gaps:  {len(gaps)}  |  Max: {max(gap_ms):.0f}ms")
    print(f"  Queue: min={min(qdepths)} mean={statistics.mean(qdepths):.1f} max={max(qdepths)}")
    print(f"  Iter interval: mean={statistics.mean(intervals)*1000:.1f}ms  "
          f"stdev={statistics.stdev(intervals)*1000:.1f}ms  max={max(intervals)*1000:.1f}ms")
    print(f"  Process time: mean={statistics.mean([r['iter_ms'] for r in results]):.2f}ms")
    # Queue trend
    n = len(qdepths)
    q1 = statistics.mean(qdepths[:n//4])
    q4 = statistics.mean(qdepths[-n//4:])
    print(f"  Queue trend: first¼={q1:.1f}  last¼={q4:.1f}  ({'growing' if q4 > q1+1 else 'stable' if abs(q4-q1) <= 1 else 'draining'})")


def main():
    print(f"=== Self-Clock vs Data-Driven Timing ===")
    print(f"Duration: {TEST_DURATION}s  |  Pre-fill: {BUFFER_MULTIPLIER*2} chunks")

    # A: Self-clock + get_nowait (current gateway code)
    def make_nowait(q):
        def get():
            try: return q.get_nowait()
            except queue.Empty: return None
        return get
    run_test("A: Self-clock + get_nowait (current)", make_nowait, use_self_clock=True)

    # B: Self-clock + blocking get 60ms
    def make_blocking60(q):
        def get():
            try: return q.get(timeout=0.06)
            except queue.Empty: return None
        return get
    run_test("B: Self-clock + get(60ms)", make_blocking60, use_self_clock=True)

    # C: NO self-clock + blocking get 60ms (data-driven)
    run_test("C: No clock + get(60ms) [data-driven]", make_blocking60, use_self_clock=False)

    # D: NO self-clock + blocking get 55ms
    def make_blocking55(q):
        def get():
            try: return q.get(timeout=0.055)
            except queue.Empty: return None
        return get
    run_test("D: No clock + get(55ms)", make_blocking55, use_self_clock=False)

    # E: NO self-clock + get_nowait + sleep(0.01) on empty
    def make_poll10(q):
        def get():
            try: return q.get_nowait()
            except queue.Empty:
                time.sleep(0.01)
                return None
        return get
    run_test("E: No clock + poll 10ms", make_poll10, use_self_clock=False)

    print()


if __name__ == '__main__':
    main()
