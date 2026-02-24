#!/usr/bin/env python3
"""
Real ALSA loopback test for SDR audio chain.

Writes a continuous sine wave to hw:6,0 (loopback write side),
reads from hw:6,1 (loopback read side) using the same reader thread +
queue + self-clock pattern as the gateway, and measures gaps/integrity.
"""

import threading
import queue
import time
import struct
import math
import sys
import statistics
import subprocess
import signal

# ── Match gateway defaults ───────────────────────────────────────────────
AUDIO_RATE = 48000
AUDIO_CHUNK_SIZE = 2400          # 50ms
BUFFER_MULTIPLIER = 4            # reader reads 4x = 200ms ALSA periods
QUEUE_MAXSIZE = 20
TICK = AUDIO_CHUNK_SIZE / AUDIO_RATE  # 0.05s
CHANNELS = 1
BYTES_PER_SAMPLE = 2

WRITE_DEVICE = "hw:6,0"
READ_DEVICE = "hw:6,1"
TEST_DURATION = 30.0  # seconds

# We'll use a distinctive pattern: each 50ms chunk starts with a
# 4-byte sequence number (replacing the first 2 samples), so we can
# detect drops and reordering on the read side.


def write_sine_thread(device, duration, chunk_size, rate, channels):
    """Write continuous sine wave with embedded sequence markers to ALSA."""
    import pyaudio
    pa = pyaudio.PyAudio()
    try:
        # Parse device
        parts = device.replace("hw:", "").split(",")
        card = int(parts[0])
        dev = int(parts[1]) if len(parts) > 1 else 0

        # Find PyAudio device index for this ALSA hw device
        dev_index = None
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            name = info.get('name', '')
            if f"hw:{card},{dev}" in name and info['maxOutputChannels'] >= channels:
                dev_index = i
                break

        if dev_index is None:
            print(f"[Writer] Could not find output device {device}")
            return

        stream = pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            output=True,
            output_device_index=dev_index,
            frames_per_buffer=chunk_size,
        )

        freq = 1000.0  # 1kHz test tone
        seq = 0
        samples_written = 0
        t0 = time.monotonic()

        while time.monotonic() - t0 < duration + 1.0:
            # Generate sine wave chunk
            samples = []
            for i in range(chunk_size):
                t = (samples_written + i) / rate
                val = int(16000 * math.sin(2 * math.pi * freq * t))
                samples.append(val)
            samples_written += chunk_size

            # Embed sequence number in first 2 samples (4 bytes)
            samples[0] = seq & 0xFFFF
            if samples[0] > 32767:
                samples[0] -= 65536
            samples[1] = (seq >> 16) & 0xFFFF
            if samples[1] > 32767:
                samples[1] -= 65536
            seq += 1

            data = struct.pack(f'<{chunk_size}h', *samples)
            try:
                stream.write(data)
            except Exception as e:
                print(f"[Writer] Write error: {e}")
                break

        stream.stop_stream()
        stream.close()
    finally:
        pa.terminate()


def reader_thread_func(stream, chunk_queue, chunk_bytes, read_frames, running_flag):
    """Exact copy of gateway SDRSource._reader_thread_func logic."""
    while running_flag[0]:
        if not stream:
            time.sleep(0.01)
            continue
        try:
            raw = stream.read(read_frames, exception_on_overflow=False)
            # Slice into consumer-sized chunks
            for i in range(0, len(raw), chunk_bytes):
                chunk = raw[i:i + chunk_bytes]
                if len(chunk) < chunk_bytes:
                    break
                try:
                    chunk_queue.put_nowait(chunk)
                except queue.Full:
                    pass  # drop newest
        except IOError:
            time.sleep(0.01)
        except Exception:
            time.sleep(0.01)


def main():
    import pyaudio

    print(f"=== Real ALSA Loopback Test ===")
    print(f"Write: {WRITE_DEVICE}  |  Read: {READ_DEVICE}")
    print(f"Rate: {AUDIO_RATE}  |  Chunk: {AUDIO_CHUNK_SIZE} ({TICK*1000:.0f}ms)")
    print(f"Buffer multiplier: {BUFFER_MULTIPLIER}  |  Queue: {QUEUE_MAXSIZE}")
    print(f"Duration: {TEST_DURATION}s")
    print()

    pa = pyaudio.PyAudio()

    # Find read device
    read_parts = READ_DEVICE.replace("hw:", "").split(",")
    read_card = int(read_parts[0])
    read_dev = int(read_parts[1]) if len(read_parts) > 1 else 0
    read_index = None
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        name = info.get('name', '')
        if f"hw:{read_card},{read_dev}" in name and info['maxInputChannels'] >= CHANNELS:
            read_index = i
            break

    if read_index is None:
        print(f"ERROR: Could not find input device {READ_DEVICE}")
        pa.terminate()
        sys.exit(1)

    print(f"Read device index: {read_index}")

    # Start writer thread
    writer = threading.Thread(
        target=write_sine_thread,
        args=(WRITE_DEVICE, TEST_DURATION, AUDIO_CHUNK_SIZE, AUDIO_RATE, CHANNELS),
        daemon=True
    )
    writer.start()
    time.sleep(0.5)  # let writer establish the loopback

    # Open read stream (blocking mode, same as gateway)
    buffer_size = AUDIO_CHUNK_SIZE * BUFFER_MULTIPLIER
    read_stream = pa.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=AUDIO_RATE,
        input=True,
        input_device_index=read_index,
        frames_per_buffer=buffer_size,
        stream_callback=None
    )

    # Queue and reader thread (same as gateway)
    chunk_queue = queue.Queue(maxsize=QUEUE_MAXSIZE)
    chunk_bytes = AUDIO_CHUNK_SIZE * CHANNELS * BYTES_PER_SAMPLE
    read_frames = AUDIO_CHUNK_SIZE * BUFFER_MULTIPLIER
    running = [True]

    reader = threading.Thread(
        target=reader_thread_func,
        args=(read_stream, chunk_queue, chunk_bytes, read_frames, running),
        daemon=True
    )
    reader.start()

    # Pre-fill (same as gateway fix)
    prefill_target = BUFFER_MULTIPLIER * 2  # 8 chunks
    prefill_deadline = time.monotonic() + 1.0
    while chunk_queue.qsize() < prefill_target and time.monotonic() < prefill_deadline:
        time.sleep(0.01)
    print(f"Pre-fill complete: {chunk_queue.qsize()} chunks")

    # ── Consumer loop with self-clock (same as gateway) ──────────────
    results = []  # (time, 'audio'|'empty', qsize)
    seqs = []     # sequence numbers from audio chunks

    t0 = time.monotonic()
    _next_tick = time.monotonic()

    while time.monotonic() - t0 < TEST_DURATION:
        _now = time.monotonic()
        if _next_tick > _now:
            time.sleep(_next_tick - _now)
        elif _now - _next_tick > TICK:
            _next_tick = _now
        _next_tick += TICK

        tick_time = time.monotonic() - t0
        qsize = chunk_queue.qsize()

        try:
            raw = chunk_queue.get_nowait()
            # Extract sequence number from first 2 samples
            if len(raw) >= 4:
                s0 = struct.unpack_from('<h', raw, 0)[0]
                s1 = struct.unpack_from('<h', raw, 2)[0]
                seq = (s0 & 0xFFFF) | ((s1 & 0xFFFF) << 16)
                seqs.append(seq)
            results.append((tick_time, 'audio', qsize))
        except queue.Empty:
            results.append((tick_time, 'empty', qsize))

    # Shutdown
    running[0] = False
    reader.join(timeout=1.0)
    read_stream.stop_stream()
    read_stream.close()
    pa.terminate()
    writer.join(timeout=2.0)

    # ── Analysis ─────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"RESULTS")
    print(f"{'='*60}")

    total = len(results)
    empty = sum(1 for _, evt, _ in results if evt == 'empty')
    audio = total - empty

    print(f"\nTicks: {total}  |  Audio: {audio} ({100*audio/total:.2f}%)  |  Empty: {empty} ({100*empty/total:.2f}%)")

    # Gaps
    gaps = []
    g = 0
    for _, evt, _ in results:
        if evt == 'empty':
            g += 1
        else:
            if g > 0:
                gaps.append(g)
            g = 0
    if g > 0:
        gaps.append(g)

    if gaps:
        gap_ms = [x * TICK * 1000 for x in gaps]
        print(f"Gaps: {len(gaps)}  |  Max: {max(gap_ms):.0f}ms  |  Sizes: {gaps[:20]}")
    else:
        print(f"Gaps: NONE")

    # Queue depth over time (sampled every 10 ticks)
    qdepths = [q for _, _, q in results]
    print(f"\nQueue depth: min={min(qdepths)}  mean={statistics.mean(qdepths):.1f}  max={max(qdepths)}")

    # Queue depth trend
    n = len(qdepths)
    q1 = qdepths[:n//4]
    q4 = qdepths[-n//4:]
    print(f"  First quarter: mean={statistics.mean(q1):.1f}")
    print(f"  Last quarter:  mean={statistics.mean(q4):.1f}")

    # Sequence integrity
    if seqs:
        seq_diffs = [seqs[i+1] - seqs[i] for i in range(len(seqs)-1)]
        non_one = [d for d in seq_diffs if d != 1]
        print(f"\nSequence: {len(seqs)} chunks, range {seqs[0]}..{seqs[-1]}")
        if non_one:
            print(f"  Non-sequential steps: {len(non_one)} (values: {non_one[:20]})")
        else:
            print(f"  All sequential (perfect)")

    # Tick timing
    times = [t for t, _, _ in results]
    if len(times) > 1:
        intervals = [times[i+1] - times[i] for i in range(len(times)-1)]
        print(f"\nTick intervals: mean={statistics.mean(intervals)*1000:.1f}ms  "
              f"stdev={statistics.stdev(intervals)*1000:.1f}ms  "
              f"min={min(intervals)*1000:.1f}ms  max={max(intervals)*1000:.1f}ms")

    # Timeline of first 2 seconds
    print(f"\n{'='*60}")
    print(f"TIMELINE (first 2s)")
    print(f"{'='*60}")
    for t, evt, qsize in results:
        if t > 2.0:
            break
        marker = "██" if evt == 'audio' else "  "
        print(f"  {t*1000:7.1f}ms  {marker}  q={qsize}")


if __name__ == '__main__':
    main()
