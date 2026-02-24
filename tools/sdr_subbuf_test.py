#!/usr/bin/env python3
"""
Test sub-buffer approach: reader queues entire ALSA periods (200ms blobs),
consumer uses sub-buffer to slice into 50ms chunks with blocking get
at blob boundaries — same pattern as AIOC.

Uses external writer (speaker-test) for realistic conditions.
"""

import threading
import queue
import time
import sys
import statistics

AUDIO_RATE = 48000
AUDIO_CHUNK_SIZE = 2400
BUFFER_MULTIPLIER = 4
QUEUE_MAXSIZE = 8  # blobs, not chunks (8 × 200ms = 1.6s)
TICK = AUDIO_CHUNK_SIZE / AUDIO_RATE
CHANNELS = 1
BYTES_PER_SAMPLE = 2
CHUNK_BYTES = AUDIO_CHUNK_SIZE * CHANNELS * BYTES_PER_SAMPLE

WRITE_DEVICE = "hw:6,0"
READ_DEVICE = "hw:6,1"
TEST_DURATION = 30.0


def reader_thread_blobs(stream, blob_queue, read_frames, running):
    """Reader queues entire ALSA periods as blobs (NOT sliced)."""
    while running[0]:
        try:
            raw = stream.read(read_frames, exception_on_overflow=False)
            if raw:
                try:
                    blob_queue.put_nowait(raw)
                except queue.Full:
                    pass  # drop newest blob
        except IOError:
            time.sleep(0.01)
        except Exception:
            time.sleep(0.01)


def reader_thread_chunks(stream, chunk_queue, chunk_bytes, read_frames, running):
    """Reader slices into consumer-sized chunks (current gateway approach)."""
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


def run_subbuf_test(name, pa, read_index, blob_timeout):
    """Sub-buffer approach: reader queues blobs, consumer slices with blocking get."""
    import pyaudio
    buffer_size = AUDIO_CHUNK_SIZE * BUFFER_MULTIPLIER
    read_stream = pa.open(format=pyaudio.paInt16, channels=CHANNELS, rate=AUDIO_RATE,
                         input=True, input_device_index=read_index,
                         frames_per_buffer=buffer_size, stream_callback=None)

    blob_queue = queue.Queue(maxsize=QUEUE_MAXSIZE)
    running = [True]
    reader = threading.Thread(target=reader_thread_blobs,
                             args=(read_stream, blob_queue,
                                   AUDIO_CHUNK_SIZE * BUFFER_MULTIPLIER, running),
                             daemon=True)
    reader.start()

    # Pre-fill: wait for 2 blobs
    deadline = time.monotonic() + 2.0
    while blob_queue.qsize() < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    sub_buffer = b''
    got_count = 0
    empty_count = 0
    total_count = 0
    gaps = []
    current_gap = 0
    qdepths = []

    t0 = time.monotonic()
    _next_tick = time.monotonic()

    while time.monotonic() - t0 < TEST_DURATION:
        # Self-clock
        _now = time.monotonic()
        if _next_tick > _now:
            time.sleep(_next_tick - _now)
        elif _now - _next_tick > TICK:
            _next_tick = _now
        _next_tick += TICK

        total_count += 1
        qd = blob_queue.qsize()
        qdepths.append(qd)

        # Sub-buffer: slice one consumer chunk
        while len(sub_buffer) < CHUNK_BYTES:
            try:
                blob = blob_queue.get(timeout=blob_timeout)
                sub_buffer += blob
            except queue.Empty:
                break

        if len(sub_buffer) >= CHUNK_BYTES:
            chunk = sub_buffer[:CHUNK_BYTES]
            sub_buffer = sub_buffer[CHUNK_BYTES:]
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
    q1 = statistics.mean(qdepths[:n//4]) if n >= 4 else 0
    q4 = statistics.mean(qdepths[-n//4:]) if n >= 4 else 0

    print(f"\n{'─'*65}")
    print(f"  {name}")
    print(f"{'─'*65}")
    print(f"  Ticks: {total_count}  |  Audio: {got_count} ({100*got_count/total_count:.2f}%)  |  Empty: {empty_count}")
    print(f"  Gaps:  {len(gaps)}  |  Max: {max(gap_ms):.0f}ms")
    print(f"  Blob queue: min={min(qdepths)} mean={statistics.mean(qdepths):.1f} max={max(qdepths)}")
    print(f"  Queue trend: first¼={q1:.1f}  last¼={q4:.1f}")


def run_chunks_test(name, pa, read_index):
    """Current approach for comparison: reader slices, consumer get_nowait."""
    import pyaudio
    buffer_size = AUDIO_CHUNK_SIZE * BUFFER_MULTIPLIER
    read_stream = pa.open(format=pyaudio.paInt16, channels=CHANNELS, rate=AUDIO_RATE,
                         input=True, input_device_index=read_index,
                         frames_per_buffer=buffer_size, stream_callback=None)

    chunk_queue = queue.Queue(maxsize=20)
    running = [True]
    reader = threading.Thread(target=reader_thread_chunks,
                             args=(read_stream, chunk_queue, CHUNK_BYTES,
                                   AUDIO_CHUNK_SIZE * BUFFER_MULTIPLIER, running),
                             daemon=True)
    reader.start()

    # Pre-fill
    deadline = time.monotonic() + 2.0
    while chunk_queue.qsize() < 8 and time.monotonic() < deadline:
        time.sleep(0.01)

    got_count = 0
    empty_count = 0
    total_count = 0
    gaps = []
    current_gap = 0

    t0 = time.monotonic()
    _next_tick = time.monotonic()

    while time.monotonic() - t0 < TEST_DURATION:
        _now = time.monotonic()
        if _next_tick > _now:
            time.sleep(_next_tick - _now)
        elif _now - _next_tick > TICK:
            _next_tick = _now
        _next_tick += TICK

        total_count += 1
        try:
            chunk_queue.get_nowait()
            got_count += 1
            if current_gap > 0:
                gaps.append(current_gap)
            current_gap = 0
        except queue.Empty:
            empty_count += 1
            current_gap += 1

    if current_gap > 0:
        gaps.append(current_gap)

    running[0] = False
    reader.join(timeout=1.0)
    read_stream.stop_stream()
    read_stream.close()

    gap_ms = [g * TICK * 1000 for g in gaps] if gaps else [0]
    print(f"\n{'─'*65}")
    print(f"  {name}")
    print(f"{'─'*65}")
    print(f"  Ticks: {total_count}  |  Audio: {got_count} ({100*got_count/total_count:.2f}%)  |  Empty: {empty_count}")
    print(f"  Gaps:  {len(gaps)}  |  Max: {max(gap_ms):.0f}ms")


if __name__ == '__main__':
    import pyaudio
    import subprocess

    print(f"=== Sub-Buffer vs Chunk-Queue (External Writer) ===")

    writer = subprocess.Popen(
        ['speaker-test', '-D', WRITE_DEVICE, '-t', 'sine', '-f', '1000',
         '-r', str(AUDIO_RATE), '-c', str(CHANNELS), '-F', 'S16_LE'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(1.0)

    pa = pyaudio.PyAudio()
    parts = READ_DEVICE.replace("hw:", "").split(",")
    card, dev = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    read_index = None
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if f"hw:{card},{dev}" in info.get('name', '') and info['maxInputChannels'] >= CHANNELS:
            read_index = i; break
    if read_index is None:
        print(f"ERROR: {READ_DEVICE} not found")
        writer.kill(); pa.terminate(); sys.exit(1)

    # Current approach (baseline)
    run_chunks_test("Baseline: chunk-queue + get_nowait + prefill 8", pa, read_index)

    # Sub-buffer with 60ms blob timeout
    run_subbuf_test("Sub-buffer: blob-queue + get(60ms) + prefill 2 blobs", pa, read_index, 0.06)

    # Sub-buffer with 100ms blob timeout (generous)
    run_subbuf_test("Sub-buffer: blob-queue + get(100ms) + prefill 2 blobs", pa, read_index, 0.10)

    # Sub-buffer with 250ms blob timeout (full ALSA period)
    run_subbuf_test("Sub-buffer: blob-queue + get(250ms) + prefill 2 blobs", pa, read_index, 0.25)

    pa.terminate()
    writer.kill(); writer.wait()
    print()
