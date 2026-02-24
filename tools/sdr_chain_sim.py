#!/usr/bin/env python3
"""
Simulate SDR audio chain with realistic clock drift between
ALSA loopback and system monotonic clock.

Key insight: the self-clock (time.monotonic) and ALSA device clock
are independent. Even ±50ppm drift means the pre-fill cushion
eventually drains or overflows.
"""

import threading
import queue
import time
import statistics
import random

AUDIO_RATE = 48000
AUDIO_CHUNK_SIZE = 2400
BUFFER_MULTIPLIER = 4
QUEUE_MAXSIZE = 20
TICK = AUDIO_CHUNK_SIZE / AUDIO_RATE  # 0.05s

ALSA_PERIOD_FRAMES = AUDIO_CHUNK_SIZE * BUFFER_MULTIPLIER
ALSA_PERIOD_SECS = ALSA_PERIOD_FRAMES / AUDIO_RATE
BYTES_PER_FRAME = 2
CHUNK_BYTES = AUDIO_CHUNK_SIZE * BYTES_PER_FRAME


class FakeALSAStream:
    """ALSA loopback with configurable clock drift."""
    def __init__(self, drift_ppm=0):
        self._seq = 0
        self._drift_factor = 1.0 + drift_ppm / 1_000_000.0
        self._next_delivery = time.monotonic() + ALSA_PERIOD_SECS * self._drift_factor

    def read(self, num_frames, **kwargs):
        now = time.monotonic()
        wait = self._next_delivery - now
        if wait > 0:
            time.sleep(wait)
        jitter = random.gauss(0, 0.002)  # 2ms σ
        self._next_delivery += ALSA_PERIOD_SECS * self._drift_factor + jitter

        chunks_per_period = num_frames // AUDIO_CHUNK_SIZE
        data = bytearray()
        for _ in range(chunks_per_period):
            chunk = bytearray(CHUNK_BYTES)
            chunk[0:4] = self._seq.to_bytes(4, 'little')
            self._seq += 1
            data.extend(chunk)
        return bytes(data)


class ReaderThread:
    def __init__(self, stream, q, chunk_bytes, read_frames):
        self.stream = stream
        self.queue = q
        self.chunk_bytes = chunk_bytes
        self.read_frames = read_frames
        self.running = False
        self.thread = None
        self.drops = 0

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)

    def _run(self):
        while self.running:
            try:
                raw = self.stream.read(self.read_frames, exception_on_overflow=False)
            except Exception:
                time.sleep(0.01)
                continue
            for i in range(0, len(raw), self.chunk_bytes):
                chunk = raw[i:i + self.chunk_bytes]
                if len(chunk) < self.chunk_bytes:
                    break
                try:
                    self.queue.put_nowait(chunk)
                except queue.Full:
                    self.drops += 1


def run_test(name, duration, drift_ppm, prefill_chunks):
    stream = FakeALSAStream(drift_ppm=drift_ppm)
    q = queue.Queue(maxsize=QUEUE_MAXSIZE)
    reader = ReaderThread(stream, q, CHUNK_BYTES, ALSA_PERIOD_FRAMES)
    reader.start()

    # Pre-fill
    if prefill_chunks > 0:
        deadline = time.monotonic() + 2.0
        while q.qsize() < prefill_chunks and time.monotonic() < deadline:
            time.sleep(0.01)

    empty_count = 0
    total_count = 0
    gaps = []
    current_gap = 0
    qdepth_samples = []  # sample every 100 ticks for reporting

    t0 = time.monotonic()
    _next_tick = time.monotonic()

    while time.monotonic() - t0 < duration:
        _now = time.monotonic()
        if _next_tick > _now:
            time.sleep(_next_tick - _now)
        elif _now - _next_tick > TICK:
            _next_tick = _now
        _next_tick += TICK

        total_count += 1

        try:
            q.get_nowait()
            if current_gap > 0:
                gaps.append(current_gap)
            current_gap = 0
        except queue.Empty:
            empty_count += 1
            current_gap += 1

        if total_count % 100 == 0:
            qdepth_samples.append(q.qsize())

    if current_gap > 0:
        gaps.append(current_gap)
    reader.stop()

    audio = total_count - empty_count
    gap_ms = [g * TICK * 1000 for g in gaps] if gaps else [0]

    # Queue depth trend: first vs last quarter
    q1 = qdepth_samples[:len(qdepth_samples)//4] if qdepth_samples else [0]
    q4 = qdepth_samples[-len(qdepth_samples)//4:] if qdepth_samples else [0]

    print(f"\n{'─'*70}")
    print(f"  {name}")
    print(f"  Drift: {drift_ppm:+d} ppm | Prefill: {prefill_chunks} | Duration: {duration}s")
    print(f"{'─'*70}")
    print(f"  Audio: {audio}/{total_count} ({100*audio/total_count:.2f}%)")
    print(f"  Empty: {empty_count}  |  Gaps: {len(gaps)}  |  Max gap: {max(gap_ms):.0f}ms")
    print(f"  Drops: {reader.drops}")
    print(f"  Queue depth first quarter: mean={statistics.mean(q1):.1f}")
    print(f"  Queue depth last quarter:  mean={statistics.mean(q4):.1f}")
    drift_chunks_per_min = drift_ppm / 1_000_000.0 * AUDIO_RATE / AUDIO_CHUNK_SIZE * 60
    print(f"  Theory: drift = {drift_chunks_per_min:+.1f} chunks/minute")


def main():
    print("=== Clock Drift Analysis ===")
    print(f"ALSA period: {ALSA_PERIOD_SECS*1000:.0f}ms | Chunk: {TICK*1000:.0f}ms | Queue: {QUEUE_MAXSIZE}")

    # Typical crystal oscillator drift is ±20-100 ppm
    # ALSA loopback is software-timed, so drift depends on what's driving it

    for drift in [0, +50, +100, +200, -50, -100, -200]:
        run_test(
            f"Prefill 8, drift {drift:+d}ppm",
            duration=120.0,  # 2 minutes
            drift_ppm=drift,
            prefill_chunks=8,
        )

    # Now test: what if the consumer is slaved to the queue instead of self-clocked?
    print(f"\n{'='*70}")
    print(f"  ALTERNATIVE: Consumer slaved to producer (blocking get)")
    print(f"{'='*70}")

    for drift in [0, +100, -100]:
        stream = FakeALSAStream(drift_ppm=drift)
        q = queue.Queue(maxsize=QUEUE_MAXSIZE)
        reader = ReaderThread(stream, q, CHUNK_BYTES, ALSA_PERIOD_FRAMES)
        reader.start()

        # Pre-fill
        deadline = time.monotonic() + 2.0
        while q.qsize() < 8 and time.monotonic() < deadline:
            time.sleep(0.01)

        empty_count = 0
        total_count = 0
        gaps = []
        current_gap = 0

        t0 = time.monotonic()
        while time.monotonic() - t0 < 120.0:
            total_count += 1
            try:
                q.get(timeout=ALSA_PERIOD_SECS + 0.05)
                if current_gap > 0:
                    gaps.append(current_gap)
                current_gap = 0
            except queue.Empty:
                empty_count += 1
                current_gap += 1

        if current_gap > 0:
            gaps.append(current_gap)
        reader.stop()

        audio = total_count - empty_count
        gap_ms = [g * TICK * 1000 for g in gaps] if gaps else [0]
        elapsed = time.monotonic() - t0
        print(f"\n  Drift {drift:+d}ppm: {audio}/{total_count} ({100*audio/total_count:.2f}%)  "
              f"Gaps: {len(gaps)}  Max: {max(gap_ms):.0f}ms  "
              f"Rate: {total_count/elapsed:.1f} chunks/s (target: {1/TICK:.0f})")

    print()


if __name__ == '__main__':
    main()
