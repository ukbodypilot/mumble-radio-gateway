#!/usr/bin/env python3
"""
Full-chain test: real ALSA loopback → reader thread → queue →
signal detection → output.

Tests whether the mixer's check_signal_instant() or the self-clock
timing causes audio gaps after the queue delivers perfect data.
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
SIGNAL_THRESHOLD_DB = -50.0  # same as gateway check_signal_instant


def check_signal_instant(audio_data):
    """Same as gateway mixer's check_signal_instant."""
    if not audio_data:
        return False
    arr = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
    if len(arr) == 0:
        return False
    rms = float(np.sqrt(np.mean(arr * arr)))
    if rms > 0:
        db = 20 * math.log10(rms / 32767.0)
        return db > SIGNAL_THRESHOLD_DB
    return False


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
            print("[Writer] Device not found")
            return

        stream = pa.open(format=pyaudio.paInt16, channels=channels, rate=rate,
                        output=True, output_device_index=dev_index,
                        frames_per_buffer=chunk_size)

        freq = 1000.0
        samples_written = 0
        t0 = time.monotonic()
        while time.monotonic() - t0 < duration + 1.0:
            samples = []
            for i in range(chunk_size):
                t = (samples_written + i) / rate
                val = int(16000 * math.sin(2 * math.pi * freq * t))
                samples.append(val)
            samples_written += chunk_size
            data = struct.pack(f'<{chunk_size}h', *samples)
            stream.write(data)

        stream.stop_stream()
        stream.close()
    finally:
        pa.terminate()


def reader_thread_func(stream, chunk_queue, chunk_bytes, read_frames, running):
    """Same as gateway SDRSource._reader_thread_func."""
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


def main():
    import pyaudio

    print(f"=== Full Chain Test (ALSA + signal detection) ===")
    print(f"Duration: {TEST_DURATION}s  |  Threshold: {SIGNAL_THRESHOLD_DB}dB")
    print()

    pa = pyaudio.PyAudio()

    # Find read device
    parts = READ_DEVICE.replace("hw:", "").split(",")
    card, dev = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    read_index = None
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if f"hw:{card},{dev}" in info.get('name', '') and info['maxInputChannels'] >= CHANNELS:
            read_index = i
            break
    if read_index is None:
        print(f"ERROR: Device {READ_DEVICE} not found")
        pa.terminate()
        sys.exit(1)

    # Start writer
    writer = threading.Thread(target=writer_thread,
                             args=(WRITE_DEVICE, TEST_DURATION, AUDIO_CHUNK_SIZE, AUDIO_RATE, CHANNELS),
                             daemon=True)
    writer.start()
    time.sleep(0.5)

    # Open reader stream
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
    print(f"Pre-fill: {chunk_queue.qsize()} chunks")

    # ── Consumer with detailed logging ──────────────────────────────
    tick_data = []  # per-tick: (time, got_data, signal_ok, qsize, rms_db, iter_ms)

    t0 = time.monotonic()
    _next_tick = time.monotonic()
    _prev_tick = time.monotonic()

    while time.monotonic() - t0 < TEST_DURATION:
        # Self-clock
        _now = time.monotonic()
        if _next_tick > _now:
            time.sleep(_next_tick - _now)
        elif _now - _next_tick > TICK:
            _next_tick = _now
        _next_tick += TICK

        iter_start = time.monotonic()
        tick_time = iter_start - t0
        qsize = chunk_queue.qsize()

        # Get audio (non-blocking)
        got_data = False
        signal_ok = False
        rms_db = -999.0

        try:
            raw = chunk_queue.get_nowait()
            got_data = True

            # Signal detection (same as mixer)
            arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(arr * arr)))
            if rms > 0:
                rms_db = 20 * math.log10(rms / 32767.0)
            signal_ok = check_signal_instant(raw)

            # Simulate some processing work (numpy ops like mixer does)
            _ = np.clip(arr * 1.0, -32768, 32767).astype(np.int16).tobytes()

        except queue.Empty:
            pass

        iter_ms = (time.monotonic() - iter_start) * 1000
        tick_ms = (iter_start - _prev_tick) * 1000
        _prev_tick = iter_start

        tick_data.append({
            'time': tick_time,
            'got_data': got_data,
            'signal_ok': signal_ok,
            'qsize': qsize,
            'rms_db': rms_db,
            'iter_ms': iter_ms,
            'tick_ms': tick_ms,
        })

    running[0] = False
    reader.join(timeout=1.0)
    read_stream.stop_stream()
    read_stream.close()
    pa.terminate()
    writer.join(timeout=2.0)

    # ── Analysis ─────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"RESULTS ({len(tick_data)} ticks)")
    print(f"{'='*65}")

    total = len(tick_data)
    got_data = sum(1 for d in tick_data if d['got_data'])
    signal_ok = sum(1 for d in tick_data if d['signal_ok'])
    empty = total - got_data
    signal_fail = got_data - signal_ok

    print(f"\n1. Queue delivery:")
    print(f"   Got data:    {got_data}/{total} ({100*got_data/total:.2f}%)")
    print(f"   Empty queue: {empty}/{total} ({100*empty/total:.2f}%)")

    print(f"\n2. Signal detection (of chunks that had data):")
    print(f"   Passed:  {signal_ok}/{got_data} ({100*signal_ok/got_data:.2f}%)" if got_data else "   N/A")
    print(f"   Failed:  {signal_fail}/{got_data} ({100*signal_fail/got_data:.2f}%)" if got_data else "   N/A")

    if signal_fail > 0:
        fail_dbs = [d['rms_db'] for d in tick_data if d['got_data'] and not d['signal_ok']]
        print(f"   Failed dB values: {[f'{x:.1f}' for x in fail_dbs[:20]]}")

    # RMS levels
    dbs = [d['rms_db'] for d in tick_data if d['got_data']]
    if dbs:
        print(f"\n3. Audio levels:")
        print(f"   RMS dB: min={min(dbs):.1f}  mean={statistics.mean(dbs):.1f}  max={max(dbs):.1f}")

    # Tick timing
    tick_intervals = [d['tick_ms'] for d in tick_data]
    if tick_intervals:
        print(f"\n4. Tick intervals (target: {TICK*1000:.0f}ms):")
        print(f"   Mean:  {statistics.mean(tick_intervals):.1f}ms")
        print(f"   Stdev: {statistics.stdev(tick_intervals):.1f}ms")
        print(f"   Min:   {min(tick_intervals):.1f}ms")
        print(f"   Max:   {max(tick_intervals):.1f}ms")
        over_80 = sum(1 for x in tick_intervals if x > 80)
        over_100 = sum(1 for x in tick_intervals if x > 100)
        over_200 = sum(1 for x in tick_intervals if x > 200)
        print(f"   >80ms: {over_80}  |  >100ms: {over_100}  |  >200ms: {over_200}")

    # Processing time
    iter_times = [d['iter_ms'] for d in tick_data]
    if iter_times:
        print(f"\n5. Processing time per tick:")
        print(f"   Mean:  {statistics.mean(iter_times):.2f}ms")
        print(f"   Max:   {max(iter_times):.2f}ms")

    # Queue depth
    qdepths = [d['qsize'] for d in tick_data]
    print(f"\n6. Queue depth:")
    print(f"   Min: {min(qdepths)}  Mean: {statistics.mean(qdepths):.1f}  Max: {max(qdepths)}")

    # Effective delivery after signal gate
    would_play = sum(1 for d in tick_data if d['signal_ok'])
    would_silence = total - would_play
    print(f"\n7. EFFECTIVE OUTPUT (what Mumble/speaker would get):")
    print(f"   Audio frames:   {would_play}/{total} ({100*would_play/total:.2f}%)")
    print(f"   Silence frames: {would_silence}/{total} ({100*would_silence/total:.2f}%)")

    # Gap analysis on effective output
    gaps = []
    g = 0
    for d in tick_data:
        if not d['signal_ok']:
            g += 1
        else:
            if g > 0:
                gaps.append(g)
            g = 0
    if g > 0:
        gaps.append(g)
    if gaps:
        gap_ms = [x * TICK * 1000 for x in gaps]
        print(f"   Gaps: {len(gaps)}  |  Max: {max(gap_ms):.0f}ms  |  Sizes: {gaps[:30]}")
    else:
        print(f"   Gaps: NONE")

    # First 2s timeline
    print(f"\n{'='*65}")
    print(f"TIMELINE (first 2s)")
    print(f"{'='*65}")
    for d in tick_data:
        if d['time'] > 2.0:
            break
        status = "██" if d['signal_ok'] else ("░░" if d['got_data'] else "  ")
        db_str = f"{d['rms_db']:6.1f}dB" if d['got_data'] else "    N/A"
        print(f"  {d['time']*1000:7.1f}ms  {status}  q={d['qsize']:2d}  {db_str}  tick={d['tick_ms']:5.1f}ms")


if __name__ == '__main__':
    main()
