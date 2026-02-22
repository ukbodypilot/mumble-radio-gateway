#!/usr/bin/env python3
"""
AIOC loopback test — reads from the AIOC and plays to a local speaker.
Use this to isolate whether audio dropouts originate in the gateway code
or in the AIOC device / snd_usb_audio driver itself.

Usage:
    python3 tools/aioc_loopback_test.py [--chunk SIZE] [--out DEVICE_NAME]

    --chunk   frames per buffer (default 2400 = 50ms at 48kHz)
    --out     partial output device name to search for (default: system default)
    --list    list all audio devices and exit
    --mode    'blocking' (default) or 'callback'

Press Ctrl+C to stop.
"""

import argparse
import sys
import time
import pyaudio

RATE = 48000
CHANNELS = 1
FORMAT = pyaudio.paInt16
AIOC_KEYWORDS = ['usb audio', 'all-in-one', 'aioc']


def find_device(p, keywords, direction):
    """Return first device index whose name matches any keyword (case-insensitive).
    direction: 'input' or 'output'
    """
    cap_key = 'maxInputChannels' if direction == 'input' else 'maxOutputChannels'
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info[cap_key] < 1:
            continue
        name_lower = info['name'].lower()
        for kw in keywords:
            if kw in name_lower:
                return i, info['name']
    return None, None


def list_devices(p):
    print(f"{'Idx':>3}  {'In':>3}  {'Out':>3}  Name")
    print('-' * 60)
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        print(f"{i:>3}  {int(info['maxInputChannels']):>3}  {int(info['maxOutputChannels']):>3}  {info['name']}")


def run_blocking(p, in_idx, out_idx, chunk):
    in_stream = p.open(
        format=FORMAT, channels=CHANNELS, rate=RATE,
        input=True, input_device_index=in_idx,
        frames_per_buffer=chunk,
    )
    out_stream = p.open(
        format=FORMAT, channels=CHANNELS, rate=RATE,
        output=True, output_device_index=out_idx,
        frames_per_buffer=chunk * 4,
    )
    print(f"Blocking mode — chunk={chunk} frames ({chunk/RATE*1000:.0f} ms). Ctrl+C to stop.\n")
    t_start = time.monotonic()
    n = 0
    try:
        while True:
            data = in_stream.read(chunk, exception_on_overflow=False)
            out_stream.write(data, exception_on_underflow=False)
            n += 1
            elapsed = time.monotonic() - t_start
            expected = n * chunk / RATE
            drift_ms = (elapsed - expected) * 1000
            if n % 20 == 0:
                print(f"\r  chunks={n:6d}  elapsed={elapsed:7.2f}s  drift={drift_ms:+.1f}ms   ", end='', flush=True)
    except KeyboardInterrupt:
        print('\nStopped.')
    finally:
        in_stream.stop_stream(); in_stream.close()
        out_stream.stop_stream(); out_stream.close()


def run_callback(p, in_idx, out_idx, chunk):
    import queue
    q = queue.Queue(maxsize=8)

    def in_cb(in_data, frame_count, time_info, status):
        if status:
            print(f'\n[AIOC input status flags: {status}]', end='')
        if q.full():
            try: q.get_nowait()
            except queue.Empty: pass
        try: q.put_nowait(in_data)
        except queue.Full: pass
        return (None, pyaudio.paContinue)

    in_stream = p.open(
        format=FORMAT, channels=CHANNELS, rate=RATE,
        input=True, input_device_index=in_idx,
        frames_per_buffer=chunk,
        stream_callback=in_cb,
    )
    out_stream = p.open(
        format=FORMAT, channels=CHANNELS, rate=RATE,
        output=True, output_device_index=out_idx,
        frames_per_buffer=chunk * 4,
    )
    print(f"Callback mode — chunk={chunk} frames ({chunk/RATE*1000:.0f} ms). Ctrl+C to stop.\n")
    t_start = time.monotonic()
    n = 0
    try:
        while True:
            try:
                data = q.get(timeout=0.2)
            except queue.Empty:
                print('\n[WARNING: no audio from AIOC for 200ms]', end='')
                continue
            out_stream.write(data, exception_on_underflow=False)
            n += 1
            elapsed = time.monotonic() - t_start
            expected = n * chunk / RATE
            drift_ms = (elapsed - expected) * 1000
            if n % 20 == 0:
                print(f"\r  chunks={n:6d}  elapsed={elapsed:7.2f}s  drift={drift_ms:+.1f}ms   ", end='', flush=True)
    except KeyboardInterrupt:
        print('\nStopped.')
    finally:
        in_stream.stop_stream(); in_stream.close()
        out_stream.stop_stream(); out_stream.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--chunk', type=int, default=2400, help='frames per buffer (default 2400)')
    ap.add_argument('--out', default='', help='partial output device name (default: system default)')
    ap.add_argument('--list', action='store_true', help='list devices and exit')
    ap.add_argument('--mode', choices=['blocking', 'callback'], default='blocking', help='read mode (default: blocking)')
    args = ap.parse_args()

    p = pyaudio.PyAudio()

    if args.list:
        list_devices(p)
        p.terminate()
        return

    in_idx, in_name = find_device(p, AIOC_KEYWORDS, 'input')
    if in_idx is None:
        print('ERROR: AIOC input device not found. Use --list to see available devices.')
        p.terminate()
        sys.exit(1)
    print(f'AIOC input  : [{in_idx}] {in_name}')

    if args.out:
        out_idx, out_name = find_device(p, [args.out.lower()], 'output')
        if out_idx is None:
            print(f'WARNING: output device "{args.out}" not found — using system default')
            out_idx = None
            out_name = 'system default'
    else:
        out_idx = None
        out_name = 'system default'
    print(f'Speaker out : {out_name}')
    print()

    if args.mode == 'callback':
        run_callback(p, in_idx, out_idx, args.chunk)
    else:
        run_blocking(p, in_idx, out_idx, args.chunk)

    p.terminate()


if __name__ == '__main__':
    main()
