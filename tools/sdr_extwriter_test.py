#!/usr/bin/env python3
"""
Test with EXTERNAL writer (speaker-test subprocess) — no GIL contention.
This accurately models the real gateway where SDR software is a separate process.
"""

import threading
import queue
import time
import subprocess
import sys
import statistics
import os
import signal

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


def run_test(name, get_func_factory, use_self_clock, pa, read_index, writer_proc):
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
    deadline = time.monotonic() + 2.0
    while chunk_queue.qsize() < prefill_target and time.monotonic() < deadline:
        time.sleep(0.01)
    prefill_actual = chunk_queue.qsize()

    get_func = get_func_factory(chunk_queue)
    got_count = 0
    empty_count = 0
    total_count = 0
    gaps = []
    current_gap = 0
    qdepths = []

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

        total_count += 1
        qd = chunk_queue.qsize()
        qdepths.append(qd)

        raw = get_func()
        if raw is not None:
            got_count += 1
            if current_gap > 0:
                gaps.append(current_gap)
            current_gap = 0
        else:
            empty_count += 1
            current_gap += 1

    if current_gap > 0:
        gaps.append(current_gap)

    running[0] = False
    reader.join(timeout=1.0)
    read_stream.stop_stream()
    read_stream.close()

    gap_ms = [g * TICK * 1000 for g in gaps] if gaps else [0]
    n = len(qdepths)
    q1 = statistics.mean(qdepths[:n//4]) if n >= 4 else statistics.mean(qdepths)
    q4 = statistics.mean(qdepths[-n//4:]) if n >= 4 else statistics.mean(qdepths)

    print(f"\n{'─'*65}")
    print(f"  {name}  (prefill: {prefill_actual})")
    print(f"{'─'*65}")
    print(f"  Ticks: {total_count}  |  Audio: {got_count} ({100*got_count/total_count:.2f}%)  |  Empty: {empty_count}")
    print(f"  Gaps:  {len(gaps)}  |  Max: {max(gap_ms):.0f}ms")
    print(f"  Queue: min={min(qdepths)} mean={statistics.mean(qdepths):.1f} max={max(qdepths)}")
    print(f"  Queue trend: first¼={q1:.1f}  last¼={q4:.1f}  ({'growing' if q4 > q1+1 else 'stable' if abs(q4-q1) <= 1 else 'draining'})")
    return got_count, total_count


if __name__ == '__main__':
    import pyaudio

    print(f"=== External Writer Test (no GIL contention) ===")
    print(f"Duration: {TEST_DURATION}s  |  Pre-fill: {BUFFER_MULTIPLIER*2}")
    print()

    # Start external writer
    print(f"Starting external writer: speaker-test -D {WRITE_DEVICE} ...")
    writer_proc = subprocess.Popen(
        ['speaker-test', '-D', WRITE_DEVICE, '-t', 'sine', '-f', '1000',
         '-r', str(AUDIO_RATE), '-c', str(CHANNELS), '-F', 'S16_LE'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(1.0)  # let it establish

    pa = pyaudio.PyAudio()
    parts = READ_DEVICE.replace("hw:", "").split(",")
    card, dev = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    read_index = None
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if f"hw:{card},{dev}" in info.get('name', '') and info['maxInputChannels'] >= CHANNELS:
            read_index = i
            break
    if read_index is None:
        print(f"ERROR: {READ_DEVICE} not found")
        writer_proc.kill()
        pa.terminate()
        sys.exit(1)

    # A: Self-clock + get_nowait (current)
    def make_nowait(q):
        def get():
            try: return q.get_nowait()
            except queue.Empty: return None
        return get
    run_test("A: Self-clock + get_nowait", make_nowait, True, pa, read_index, writer_proc)

    # B: Self-clock + blocking get 60ms
    def make_blocking60(q):
        def get():
            try: return q.get(timeout=0.06)
            except queue.Empty: return None
        return get
    run_test("B: Self-clock + get(60ms)", make_blocking60, True, pa, read_index, writer_proc)

    # C: No clock + blocking get 60ms (data-driven)
    run_test("C: No clock + get(60ms)", make_blocking60, False, pa, read_index, writer_proc)

    # D: Self-clock + drain-all (consume all available per tick)
    def make_drain_all(q):
        last_chunk = [None]
        def get():
            """Drain all available chunks, return the latest one."""
            got = None
            while True:
                try:
                    got = q.get_nowait()
                except queue.Empty:
                    break
            return got
        return get
    run_test("D: Self-clock + drain-all", make_drain_all, True, pa, read_index, writer_proc)

    # E: Self-clock + drain to target depth
    def make_drain_to_target(q):
        target_depth = BUFFER_MULTIPLIER * 2  # 8
        def get():
            """Consume chunks until queue is at or below target depth."""
            got = None
            # Always consume at least 1
            try:
                got = q.get_nowait()
            except queue.Empty:
                return None
            # If queue above target, drain extras
            while q.qsize() > target_depth:
                try:
                    got = q.get_nowait()
                except queue.Empty:
                    break
            return got
        return get
    run_test("E: Self-clock + drain-to-target(8)", make_drain_to_target, True, pa, read_index, writer_proc)

    pa.terminate()
    writer_proc.kill()
    writer_proc.wait()
    print()
