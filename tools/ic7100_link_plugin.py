#!/usr/bin/env python3
"""
IC-7100 Link Plugin — ICOM IC-7100 HF/VHF/UHF radio as a Gateway Link endpoint.

The IC-7100 connects to the host (CM5) via a single USB cable that provides:
  - CI-V CAT serial port  (ttyUSB0, 19200 baud, address 0x88)
  - USB audio codec        (UAC2, appears as ALSA capture/playback device)

Audio is 48 kHz 16-bit mono in both directions (no resampling needed).

Usage:
    python3 link_endpoint.py --name IC7100 --plugin ic7100 \\
        --device /dev/ttyUSB0 --server 192.168.2.140:9700

Optional args passed through config dict:
    device      serial port (default /dev/ttyUSB0)
    audio_in    ALSA capture device name or card index (default: auto-detect)
    audio_out   ALSA playback device name or card index (default: same as audio_in)
    civ_addr    CI-V address in hex string or int (default: 0x88)
"""

import os
import sys
import struct
import threading
import time
import queue as _queue_mod
import subprocess
import json

_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_dir = os.path.dirname(_script_dir)
sys.path.insert(0, _project_dir)

from gateway_link import RadioPlugin


def _pcm_peak_pct(pcm: bytes, prev: int = 0) -> int:
    """Cheap S16_LE peak level estimator (0-100) with a fast-attack /
    slow-decay smoothing. Inline so the endpoint side doesn't need the
    gateway's audio_util module."""
    if not pcm:
        return max(0, int(prev * 0.85))   # decay to 0 when silent
    try:
        # Sample every Nth frame to keep CPU negligible
        step = 16
        peak = 0
        for i in range(0, len(pcm) - 1, 2 * step):
            v = (pcm[i] | (pcm[i + 1] << 8))
            if v & 0x8000:
                v -= 0x10000
            if v < 0:
                v = -v
            if v > peak:
                peak = v
        pct = int(peak * 100 / 32768)
    except Exception:
        return prev
    # Fast attack, slow decay
    return pct if pct > prev else int(prev * 0.85 + pct * 0.15)

# ---------------------------------------------------------------------------
# CI-V protocol constants
# ---------------------------------------------------------------------------

_PREAMBLE  = b'\xfe\xfe'
_END       = b'\xfd'
_OK        = b'\xfb'
_NG        = b'\xfa'
_CTRL_ADDR = 0xe0      # controller (us)

# IC-7100 operating modes (command 0x04/0x06, data byte 0)
_MODES = {
    0x00: 'LSB', 0x01: 'USB', 0x02: 'AM',  0x03: 'CW',
    0x04: 'RTTY', 0x05: 'FM', 0x06: 'WFM', 0x07: 'CW-R',
    0x08: 'RTTY-R', 0x17: 'DV',
}
_MODE_BY_NAME = {v: k for k, v in _MODES.items()}

# CTCSS tones supported by IC-7100 (Hz), indices 0–49
_CTCSS = [
    67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5,
    94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3,
    131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 162.2, 167.9, 173.8, 179.9,
    186.2, 192.8, 203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8,
    250.3, 254.1,
]


# ---------------------------------------------------------------------------
# BCD helpers
# ---------------------------------------------------------------------------

def _freq_to_bcd(hz: int) -> bytes:
    """Encode frequency in Hz as 5-byte IC-7100 BCD (LSB first, 2 digits/byte)."""
    digits = []
    for _ in range(10):
        digits.append(hz % 10)
        hz //= 10
    return bytes([digits[i] | (digits[i+1] << 4) for i in range(0, 10, 2)])


def _bcd_to_freq(data: bytes) -> int:
    """Decode 5-byte IC-7100 BCD frequency to Hz."""
    hz = 0
    mult = 1
    for byte in data:
        hz += (byte & 0x0f) * mult
        mult *= 10
        hz += ((byte >> 4) & 0x0f) * mult
        mult *= 10
    return hz


def _tone_to_bcd(hz: float) -> bytes:
    """Encode CTCSS tone (Hz) as 2-byte BCD (tenths of Hz, e.g. 88.5→0x08,0x85)."""
    tenths = round(hz * 10)
    d3 = tenths // 1000
    d2 = (tenths % 1000) // 100
    d1 = (tenths % 100) // 10
    d0 = tenths % 10
    return bytes([(d3 << 4) | d2, (d1 << 4) | d0])


def _bcd_to_tone(data: bytes) -> float:
    """Decode 2-byte BCD tone to Hz."""
    hi = (data[0] >> 4) * 1000 + (data[0] & 0x0f) * 100
    lo = (data[1] >> 4) * 10  + (data[1] & 0x0f)
    return (hi + lo) / 10.0


def _rit_offset_to_bcd(hz: int) -> bytes:
    """Encode RIT/XIT offset in Hz as 3-byte signed BCD: 2 bytes of 4-digit Hz
    magnitude (LSB first) + 1 byte sign (0x00 positive, 0x01 negative).

    IC-7100 RIT range is ±9.999 kHz with 1 Hz steps."""
    sign = 0x00 if hz >= 0 else 0x01
    mag = abs(int(hz))
    digits = []
    for _ in range(4):
        digits.append(mag % 10)
        mag //= 10
    return bytes([digits[0] | (digits[1] << 4),
                  digits[2] | (digits[3] << 4),
                  sign])


def _pct_to_bcd3(value: int) -> bytes:
    """Encode 0..255 as 2-byte BCD (000–255 padded). Used for level controls
    on the IC-7100 (NB level, NR level, IF shift) which take 3-digit BCD."""
    value = max(0, min(255, int(value)))
    h = value // 100
    t = (value % 100) // 10
    u = value % 10
    return bytes([h, (t << 4) | u])


def _bcd3_to_pct(data: bytes) -> int:
    """Decode 2-byte BCD level (0..255) back to integer value."""
    if len(data) < 2:
        return 0
    return (data[0] & 0x0f) * 100 + ((data[1] >> 4) & 0x0f) * 10 + (data[1] & 0x0f)


def _bcd_to_rit_offset(data: bytes) -> int:
    """Decode 3-byte signed BCD RIT/XIT offset to signed Hz."""
    if len(data) < 3:
        return 0
    mag = (data[0] & 0x0f)        \
        + (data[0] >> 4)  * 10    \
        + (data[1] & 0x0f) * 100  \
        + (data[1] >> 4)  * 1000
    return -mag if data[2] == 0x01 else mag


# DTCS (digital tone code squelch).
# Standard 104-code list, octal-style numbers stored as plain ints.
_DTCS_CODES = [
    23, 25, 26, 31, 32, 36, 43, 47, 51, 53, 54, 65, 71, 72, 73, 74,
    114, 115, 116, 122, 125, 131, 132, 134, 143, 145, 152, 155, 156, 162,
    165, 172, 174, 205, 212, 223, 225, 226, 243, 244, 245, 246, 251, 252,
    255, 261, 263, 265, 266, 271, 274, 306, 311, 315, 325, 331, 332, 343,
    346, 351, 356, 364, 365, 371, 411, 412, 413, 423, 431, 432, 445, 446,
    452, 454, 455, 462, 464, 465, 466, 503, 506, 516, 523, 526, 532, 546,
    565, 606, 612, 624, 627, 631, 632, 654, 662, 664, 703, 712, 723, 731,
    732, 734, 743, 754,
]

# DTCS polarity — IC-7100 0x1B 0x07 polarity byte. Index 0..3 maps to:
#   0 = TX normal / RX normal
#   1 = TX normal / RX reverse
#   2 = TX reverse / RX normal
#   3 = TX reverse / RX reverse
_DTCS_POLARITY = [0x00, 0x01, 0x10, 0x11]


def _dtcs_to_bytes(code: int, polarity: int) -> bytes:
    """Encode DTCS as 3 bytes: polarity + 2 BCD code bytes (e.g. 754 → 07 54)."""
    pol = _DTCS_POLARITY[max(0, min(3, int(polarity)))]
    code = max(0, min(999, int(code)))
    h = code // 100
    t = (code % 100) // 10
    u = code % 10
    return bytes([pol, h, (t << 4) | u])


def _bytes_to_dtcs(data: bytes):
    """Decode 3-byte DTCS payload → (code:int, polarity:int 0..3)."""
    if len(data) < 3:
        return (23, 0)
    try:
        polarity = _DTCS_POLARITY.index(data[0])
    except ValueError:
        polarity = 0
    code = (data[1] & 0x0f) * 100 + ((data[2] >> 4) & 0x0f) * 10 + (data[2] & 0x0f)
    return (code, polarity)


# ---------------------------------------------------------------------------
# TX antenna-port safety interlock
# ---------------------------------------------------------------------------

# The IC-7100 has two antenna connectors: [HF/50MHz] (HF + 6 m) and
# [144/430MHz] (2 m / 70 cm). Which one carries TX is fixed by the operating
# band. The interlock is a software safety catch — the operator enables the
# port whose antenna is physically connected, and TX is refused on the other
# port, preventing transmits into a disconnected/wrong antenna (high SWR,
# PA damage).
#
# 73 MHz is the HF/VU split point. No amateur TX band falls between 54 and
# 144 MHz, so the exact threshold only matters for deliberate out-of-band
# raw-CAT use.
_VU_PORT_MIN_HZ = 73_000_000


def _antenna_port(hz: int) -> str:
    """Return which antenna connector a frequency uses: 'hf' or 'vu'."""
    return 'vu' if hz >= _VU_PORT_MIN_HZ else 'hf'


# ---------------------------------------------------------------------------
# CIVController
# ---------------------------------------------------------------------------

class CIVController:
    """Manages the CI-V serial connection to an IC-7100.

    All public methods are safe to call from a single thread.
    The read loop runs internally only during _transact().
    """

    def __init__(self, port: str, baud: int = 19200, civ_addr: int = 0x88,
                 timeout: float = 1.0):
        self._port = port
        self._baud = baud
        self._addr = civ_addr
        self._timeout = timeout
        # Shorter timeout used by the periodic poll thread so a single NG/no-
        # response opcode (which can hold the lock for the full default
        # timeout while a user freq/PTT cmd is waiting) doesn't pin the GUI.
        # 0.25 s is long enough for any real CI-V response from the IC-7100
        # (typical 50–150 ms) but short enough that the user feels snappy.
        self._poll_timeout = 0.25
        self._tls = threading.local()    # set by poll thread, read by _transact
        self._serial = None
        self._lock = threading.Lock()
        self.connected = False

        # Cached radio state (updated by periodic poll + command responses)
        self.freq_hz: int = 0
        self.mode: str = 'FM'
        self.filter_idx: int = 1
        self.transmitting: bool = False
        self.smeter: int = 0
        self.ctcss_tx_hz: float = 88.5
        self.ctcss_rx_hz: float = 88.5
        self.ctcss_tx_on: bool = False
        self.ctcss_rx_on: bool = False
        # HF feature state (Phase C)
        self.split: bool = False
        self.rit_hz: int = 0      # RIT offset in Hz, -9999..+9999
        self.rit_on: bool = False
        self.xit_on: bool = False
        self.agc: str = 'mid'     # fast / mid / slow
        self.nb_on: bool = False
        self.nb_level: int = 50   # 0..100 percent
        self.nr_on: bool = False
        self.nr_level: int = 50
        self.preamp: int = 0      # 0=off, 1=pre1, 2=pre2
        self.atten: bool = False  # 20 dB attenuator
        self.if_shift: int = 50   # 0..100, 50=center (no shift)
        self.squelch: int = 0     # 0..100 percent (0=open)
        self.rf_power: int = 0    # 0..100 percent — TX RF output power
        self.mic_gain: int = 50   # 0..100 percent — TX mic gain
        self.af_level: int = 50   # 0..100 percent — radio AF (volume) knob
        # DATA mode (USB-D / FM-D / etc.) flag — controls which MOD INPUT
        # the radio uses on TX. With DATA OFF MOD = MIC and DATA MOD = USB
        # on the radio, toggling this swaps between the operator's manual
        # mic (data_mode=False) and the gateway-fed USB audio (True).
        self.data_mode: bool = False
        self.data_mode_filter: int = 1   # 1/2/3, only meaningful when on
        # Tracks whether WE engaged data mode for a gateway-initiated TX
        # cycle. set_ptt auto-engages data_mode on the way up and undoes
        # it on the way down, but only if WE engaged it — never disturbs
        # a user-engaged data mode (e.g., they're running RTTY manually).
        self._we_set_data_mode: bool = False
        self.po: int = 0          # TX power meter — raw 0..255
        self.swr: int = 0         # SWR meter — raw 0..255
        self.alc: int = 0         # ALC meter — raw 0..255
        self.squelch_open: bool = True   # current squelch condition
        self.dtcs_on: bool = False       # digital tone code squelch enabled
        self.dtcs_code: int = 23         # DTCS code (octal-style int)
        self.dtcs_polarity: int = 0      # 0..3 — see _DTCS_POLARITY

        # VFO / memory state. UNVERIFIED against hardware as of 2026-05-23 —
        # opcodes taken from the IC-7100 CI-V reference but not bench-tested.
        # The radio doesn't currently echo VFO/band/memory state in any of
        # the periodically-polled commands, so we track what we set rather
        # than reading it back; first hardware verification should confirm
        # whether reads work and we can poll for changes the operator made
        # from the front panel.
        self.active_vfo: str = 'A'       # 'A' or 'B'
        # Main/Sub band concept removed — IC-7100 is single-receiver, no
        # dual-watch. The 0x07 0xD0/0xD1 opcodes belong to the IC-9100.
        self.memory_mode: bool = False
        self.memory_channel: int = 1     # 1..99 (per band)

    # -- Connection management --

    def connect(self) -> bool:
        try:
            import serial
            self._serial = serial.Serial(
                self._port, self._baud, timeout=self._timeout)
            self._serial.reset_input_buffer()
            self.connected = True
            print(f"[IC7100-CIV] Connected to {self._port} @ {self._baud}", flush=True)
            self._poll_state()
            return True
        except Exception as e:
            print(f"[IC7100-CIV] Connect failed: {e}", flush=True)
            self.connected = False
            return False

    def disconnect(self):
        self.connected = False
        try:
            if self._serial:
                self._serial.close()
        except Exception:
            pass
        self._serial = None

    # -- Low-level framing --

    def _build_frame(self, cmd: int, subcmd: int | None = None,
                     data: bytes = b'') -> bytes:
        body = bytes([self._addr, _CTRL_ADDR, cmd])
        if subcmd is not None:
            body += bytes([subcmd])
        body += data
        return _PREAMBLE + body + _END

    def _transact(self, frame: bytes, timeout: float | None = None) -> bytes | None:
        """Send *frame*, read until FD, return response body or None on error.

        *timeout* overrides the controller's default per-call. Poll reads
        pass a short (e.g. 250 ms) timeout so an NG opcode doesn't pin the
        serial lock for a full second while a user command (knob spin, PTT)
        is waiting. User-initiated commands keep the longer default for
        cases where the radio is genuinely slow."""
        if not self._serial or not self.connected:
            return None
        if timeout is not None:
            _to = float(timeout)
        elif getattr(self._tls, 'in_poll', False):
            # Poll thread — use the short timeout so an NG opcode doesn't
            # hold the lock for a full second while a user cmd is waiting.
            _to = self._poll_timeout
        else:
            _to = self._timeout
        with self._lock:
            try:
                self._serial.reset_input_buffer()
                self._serial.write(frame)
                # Read response. Old code did self._serial.read(64) with the
                # serial-level timeout, which made pyserial wait for the
                # FULL timeout even after the real ~13-byte response arrived
                # (because it kept waiting for the remaining 51 bytes of the
                # 64 requested). That floored every CI-V cmd at the full
                # timeout. Now: poll in_waiting, read available bytes,
                # return the moment a valid response frame is parsed.
                buf = b''
                deadline = time.monotonic() + _to
                while time.monotonic() < deadline:
                    avail = self._serial.in_waiting
                    if avail:
                        buf += self._serial.read(avail)
                        # Response pattern: FE FE E0 <addr> <data...> FD
                        idx = 0
                        while idx < len(buf) - 5:
                            if buf[idx:idx+2] == b'\xfe\xfe' and \
                               buf[idx+2] == _CTRL_ADDR:
                                end = buf.find(b'\xfd', idx + 4)
                                if end != -1:
                                    return buf[idx+4:end]
                            idx += 1
                    else:
                        time.sleep(0.002)   # 2 ms idle wait — cheap
                print(f"[IC7100-CIV] Timeout on cmd 0x{frame[4]:02x}", flush=True)
                return None
            except Exception as e:
                print(f"[IC7100-CIV] Transact error: {e}", flush=True)
                self.connected = False
                return None

    # -- Radio commands --

    def get_freq(self) -> float | None:
        resp = self._transact(self._build_frame(0x03))
        # Response: [0x03, bcd0..bcd4] — skip the echoed cmd byte
        if resp and len(resp) >= 6 and resp[0] == 0x03:
            self.freq_hz = _bcd_to_freq(resp[1:6])
            return self.freq_hz / 1e6
        return None

    def set_freq(self, mhz: float) -> bool:
        hz = round(mhz * 1e6)
        resp = self._transact(self._build_frame(0x05, data=_freq_to_bcd(hz)))
        ok = resp is not None and len(resp) >= 1 and resp[0:1] == _OK
        if ok:
            self.freq_hz = hz
        return ok

    def get_mode(self) -> str | None:
        resp = self._transact(self._build_frame(0x04))
        # Response: [0x04, mode, filter] — skip the echoed cmd byte
        if resp and len(resp) >= 2 and resp[0] == 0x04:
            self.mode = _MODES.get(resp[1], f'?{resp[1]:02x}')
            if len(resp) >= 3:
                self.filter_idx = resp[2]
            return self.mode
        return None

    def set_mode(self, mode: str, filter_idx: int = 1) -> bool:
        m = _MODE_BY_NAME.get(mode.upper())
        if m is None:
            return False
        resp = self._transact(self._build_frame(0x06, data=bytes([m, filter_idx])))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.mode = mode.upper()
        return ok

    def get_smeter(self) -> int | None:
        resp = self._transact(self._build_frame(0x15, subcmd=0x02))
        if resp and len(resp) >= 3 and resp[0] == 0x15 and resp[1] == 0x02:
            # 2 BCD bytes → 0000–0255
            val = (resp[2] & 0x0f) * 100 + ((resp[3] >> 4) & 0x0f) * 10 + (resp[3] & 0x0f) if len(resp) >= 4 else 0
            self.smeter = val
            return val
        return None

    # TX meters — all 0x15 reads, 2-BCD 0000–0255, same decode as the
    # S-meter. Meaningful only while transmitting.
    def _read_meter(self, subcmd: int) -> int | None:
        resp = self._transact(self._build_frame(0x15, subcmd=subcmd))
        if resp and len(resp) >= 4 and resp[0] == 0x15 and resp[1] == subcmd:
            return ((resp[2] & 0x0f) * 100
                    + ((resp[3] >> 4) & 0x0f) * 10 + (resp[3] & 0x0f))
        return None

    def get_po(self) -> int | None:
        v = self._read_meter(0x11)        # RF power meter
        if v is not None:
            self.po = v
        return v

    def get_swr(self) -> int | None:
        v = self._read_meter(0x12)        # SWR meter
        if v is not None:
            self.swr = v
        return v

    def get_alc(self) -> int | None:
        v = self._read_meter(0x13)        # ALC meter
        if v is not None:
            self.alc = v
        return v

    def set_ptt(self, on: bool) -> bool:
        # Auto-engage DATA mode around gateway-initiated PTT cycles so the
        # USB codec is the modulation source on TX (per the operator's
        # MOD INPUT config: DATA OFF MOD=MIC, DATA MOD=USB). Restore on
        # release so the manual hand mic works again for the operator's
        # own non-gateway TX. In SPLIT mode the radio's TX side is the
        # inactive VFO — data mode is a per-VFO/mode attribute so we have
        # to set it on BOTH sides before keying, and undo on BOTH after,
        # else the swap-on-PTT exposes the still-non-data VFO and we end
        # up with carrier+MIC. Only undo if WE engaged it.
        if on and not self.data_mode:
            self.set_data_mode(True)
            if self.data_mode:           # only proceed if first set actually took
                self._we_set_data_mode = True
                if self.split and self.swap_vfo():
                    self.set_data_mode(True)
                    self.swap_vfo()      # back to original selection
        resp = self._transact(
            self._build_frame(0x1c, subcmd=0x00, data=bytes([0x01 if on else 0x00])))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.transmitting = on
        if not on and self._we_set_data_mode:
            self.set_data_mode(False)
            if self.split and self.swap_vfo():
                self.set_data_mode(False)
                self.swap_vfo()
            self._we_set_data_mode = False
        return ok

    def get_ptt(self) -> bool | None:
        resp = self._transact(self._build_frame(0x1c, subcmd=0x00))
        if resp and len(resp) >= 3 and resp[0] == 0x1c and resp[1] == 0x00:
            self.transmitting = bool(resp[2])
            return self.transmitting
        return None

    def get_ctcss(self):
        """Read full CTCSS state. Returns (tx_hz, rx_hz, tx_on, rx_on) or None."""
        # TX tone frequency (0x1B 0x00)
        r_tx = self._transact(self._build_frame(0x1b, subcmd=0x00))
        if r_tx and len(r_tx) >= 4 and r_tx[0] == 0x1b and r_tx[1] == 0x00:
            self.ctcss_tx_hz = _bcd_to_tone(r_tx[2:4])

        # RX tone squelch frequency (0x1B 0x01)
        r_rx = self._transact(self._build_frame(0x1b, subcmd=0x01))
        if r_rx and len(r_rx) >= 4 and r_rx[0] == 0x1b and r_rx[1] == 0x01:
            self.ctcss_rx_hz = _bcd_to_tone(r_rx[2:4])

        # TX tone encode enable (0x16 0x43)
        r_ton = self._transact(self._build_frame(0x16, subcmd=0x43))
        if r_ton and len(r_ton) >= 3 and r_ton[0] == 0x16 and r_ton[1] == 0x43:
            self.ctcss_tx_on = bool(r_ton[2])

        # RX tone squelch enable (0x16 0x42)
        r_tsq = self._transact(self._build_frame(0x16, subcmd=0x42))
        if r_tsq and len(r_tsq) >= 3 and r_tsq[0] == 0x16 and r_tsq[1] == 0x42:
            self.ctcss_rx_on = bool(r_tsq[2])

        return (self.ctcss_tx_hz, self.ctcss_rx_hz,
                self.ctcss_tx_on, self.ctcss_rx_on)

    def set_ctcss(self, tx_hz: float | None = None, rx_hz: float | None = None,
                  tx_on: bool | None = None, rx_on: bool | None = None) -> bool:
        """Set any combination of CTCSS parameters. None = leave unchanged."""
        ok = True
        if tx_hz is not None:
            r = self._transact(self._build_frame(
                0x1b, subcmd=0x00, data=_tone_to_bcd(tx_hz)))
            if r and r[0:1] == _OK:
                self.ctcss_tx_hz = tx_hz
            else:
                ok = False
        if rx_hz is not None:
            r = self._transact(self._build_frame(
                0x1b, subcmd=0x01, data=_tone_to_bcd(rx_hz)))
            if r and r[0:1] == _OK:
                self.ctcss_rx_hz = rx_hz
            else:
                ok = False
        if tx_on is not None:
            r = self._transact(self._build_frame(
                0x16, subcmd=0x43, data=bytes([0x01 if tx_on else 0x00])))
            if r and r[0:1] == _OK:
                self.ctcss_tx_on = tx_on
            else:
                ok = False
        if rx_on is not None:
            r = self._transact(self._build_frame(
                0x16, subcmd=0x42, data=bytes([0x01 if rx_on else 0x00])))
            if r and r[0:1] == _OK:
                self.ctcss_rx_on = rx_on
            else:
                ok = False
        return ok

    # -- HF feature commands (Phase C) ---------------------------------
    # Byte values below were taken from the IC-7100 CI-V reference; opcodes
    # are correct against the official manual, but should be verified end-
    # to-end against the radio on first use.

    # Split VFO — 0x0F (0x00=simplex, 0x01=split).
    def get_split(self) -> bool | None:
        resp = self._transact(self._build_frame(0x0f))
        if resp and len(resp) >= 2 and resp[0] == 0x0f:
            self.split = bool(resp[1])
            return self.split
        return None

    def set_split(self, on: bool) -> bool:
        resp = self._transact(
            self._build_frame(0x0f, data=bytes([0x01 if on else 0x00])))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.split = on
        return ok

    # RIT offset — 0x21 0x00 (signed BCD, 3 bytes).
    def get_rit_offset(self) -> int | None:
        resp = self._transact(self._build_frame(0x21, subcmd=0x00))
        if resp and len(resp) >= 5 and resp[0] == 0x21 and resp[1] == 0x00:
            self.rit_hz = _bcd_to_rit_offset(resp[2:5])
            return self.rit_hz
        return None

    def set_rit_offset(self, hz: int) -> bool:
        hz = max(-9999, min(9999, int(hz)))
        resp = self._transact(self._build_frame(
            0x21, subcmd=0x00, data=_rit_offset_to_bcd(hz)))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.rit_hz = hz
        return ok

    # RIT enable — 0x21 0x01.
    def set_rit_on(self, on: bool) -> bool:
        resp = self._transact(self._build_frame(
            0x21, subcmd=0x01, data=bytes([0x01 if on else 0x00])))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.rit_on = on
        return ok

    # XIT enable — 0x21 0x02.
    def set_xit_on(self, on: bool) -> bool:
        resp = self._transact(self._build_frame(
            0x21, subcmd=0x02, data=bytes([0x01 if on else 0x00])))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.xit_on = on
        return ok

    # AGC — 0x16 0x12 (0x01 fast, 0x02 mid, 0x03 slow).
    _AGC_MAP   = {'fast': 0x01, 'mid': 0x02, 'slow': 0x03}
    _AGC_RMAP  = {v: k for k, v in _AGC_MAP.items()}

    def set_agc(self, mode: str) -> bool:
        m = mode.lower()
        if m not in self._AGC_MAP:
            return False
        resp = self._transact(self._build_frame(
            0x16, subcmd=0x12, data=bytes([self._AGC_MAP[m]])))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.agc = m
        return ok

    # Noise Blanker — on/off: 0x16 0x22; level: 0x14 0x12 (0..255).
    def set_nb_on(self, on: bool) -> bool:
        resp = self._transact(self._build_frame(
            0x16, subcmd=0x22, data=bytes([0x01 if on else 0x00])))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.nb_on = on
        return ok

    def set_nb_level(self, pct: int) -> bool:
        pct = max(0, min(100, int(pct)))
        scaled = round(pct * 255 / 100)
        resp = self._transact(self._build_frame(
            0x14, subcmd=0x12, data=_pct_to_bcd3(scaled)))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.nb_level = pct
        return ok

    # Noise Reduction — on/off: 0x16 0x40; level: 0x14 0x06.
    def set_nr_on(self, on: bool) -> bool:
        resp = self._transact(self._build_frame(
            0x16, subcmd=0x40, data=bytes([0x01 if on else 0x00])))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.nr_on = on
        return ok

    def set_nr_level(self, pct: int) -> bool:
        pct = max(0, min(100, int(pct)))
        scaled = round(pct * 255 / 100)
        resp = self._transact(self._build_frame(
            0x14, subcmd=0x06, data=_pct_to_bcd3(scaled)))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.nr_level = pct
        return ok

    # Preamp — 0x16 0x02 (0=off, 1=pre1, 2=pre2).
    def set_preamp(self, stage: int) -> bool:
        stage = max(0, min(2, int(stage)))
        resp = self._transact(self._build_frame(
            0x16, subcmd=0x02, data=bytes([stage])))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.preamp = stage
        return ok

    # Attenuator — 0x11 (0x00=off, 0x12=12 dB). The IC-7100 attenuator is
    # a fixed 12 dB pad (NOT 20 dB like the IC-7300/IC-7610 — the value
    # byte is the dB amount in BCD). Confirmed against the IC-7100 manual
    # CI-V table; earlier guesses of 0x01 and 0x20 both NG'd the radio.
    def set_atten(self, on: bool) -> bool:
        resp = self._transact(self._build_frame(
            0x11, data=bytes([0x12 if on else 0x00])))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.atten = on
        return ok

    # IF shift — 0x14 0x07 (0..255, 0x80 = center).
    def set_if_shift(self, pct: int) -> bool:
        pct = max(0, min(100, int(pct)))
        scaled = round(pct * 255 / 100)
        resp = self._transact(self._build_frame(
            0x14, subcmd=0x07, data=_pct_to_bcd3(scaled)))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.if_shift = pct
        return ok

    # Filter selection — uses 0x06 with mode + filter (1/2/3).
    def set_filter(self, idx: int) -> bool:
        idx = max(1, min(3, int(idx)))
        m = _MODE_BY_NAME.get(self.mode.upper())
        if m is None:
            return False
        resp = self._transact(self._build_frame(0x06, data=bytes([m, idx])))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.filter_idx = idx
        return ok

    # Squelch level — 0x14 0x03 (0..255). Mostly used on FM; on SSB/CW
    # the squelch is normally run wide open (level 0).
    def set_squelch(self, pct: int) -> bool:
        pct = max(0, min(100, int(pct)))
        scaled = round(pct * 255 / 100)
        resp = self._transact(self._build_frame(
            0x14, subcmd=0x03, data=_pct_to_bcd3(scaled)))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.squelch = pct
        return ok

    # RF output power — 0x14 0x0A (0..255). Maps 0..100% TX power. Same
    # level-control family as squelch/NB/NR/IF-shift above.
    def set_rf_power(self, pct: int) -> bool:
        pct = max(0, min(100, int(pct)))
        scaled = round(pct * 255 / 100)
        resp = self._transact(self._build_frame(
            0x14, subcmd=0x0A, data=_pct_to_bcd3(scaled)))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.rf_power = pct
        return ok

    # AF level (front-panel volume / AF GAIN knob) — 0x14 0x01 (0..255).
    # Lets the GUI drive the physical radio speaker volume. Independent of
    # the gateway-side audio_boost (which scales the captured RX stream
    # AFTER it leaves the radio).
    def set_af_level(self, pct: int) -> bool:
        pct = max(0, min(100, int(pct)))
        scaled = round(pct * 255 / 100)
        resp = self._transact(self._build_frame(
            0x14, subcmd=0x01, data=_pct_to_bcd3(scaled)))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.af_level = pct
        return ok

    # DATA mode (USB-D / FM-D / etc) — 0x1A 0x06 [on] [filter].
    # Per IC-7100 manual p20-14:
    #   q (1 byte): 00 = data mode OFF, 01 = data mode ON
    #   w (1 byte): 00 when off, else 01=FIL1 / 02=FIL2 / 03=FIL3
    # With the operator's radio set to DATA OFF MOD=MIC and DATA MOD=USB,
    # toggling this picks which audio source feeds the modulator: mic when
    # off, USB codec (gateway audio) when on.
    def set_data_mode(self, on: bool, filt: int = 1) -> bool:
        flag = 0x01 if on else 0x00
        filt = max(1, min(3, int(filt))) if on else 0
        resp = self._transact(self._build_frame(
            0x1a, subcmd=0x06, data=bytes([flag, filt])))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.data_mode = bool(on)
            self.data_mode_filter = filt if on else self.data_mode_filter
        return ok

    def get_data_mode(self) -> bool | None:
        resp = self._transact(self._build_frame(0x1a, subcmd=0x06))
        if resp and len(resp) >= 4 and resp[0] == 0x1a and resp[1] == 0x06:
            self.data_mode = bool(resp[2])
            if resp[2]:
                self.data_mode_filter = int(resp[3]) or self.data_mode_filter
            return self.data_mode
        return None

    # Mic gain — 0x14 0x0B (0..255). Maps 0..100% TX microphone gain.
    def set_mic_gain(self, pct: int) -> bool:
        pct = max(0, min(100, int(pct)))
        scaled = round(pct * 255 / 100)
        resp = self._transact(self._build_frame(
            0x14, subcmd=0x0B, data=_pct_to_bcd3(scaled)))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.mic_gain = pct
        return ok

    # Squelch condition — 0x15 0x01 (0=closed/muted, 1=open/audio passing).
    def get_squelch_status(self) -> bool | None:
        resp = self._transact(self._build_frame(0x15, subcmd=0x01))
        if resp and len(resp) >= 3 and resp[0] == 0x15 and resp[1] == 0x01:
            self.squelch_open = bool(resp[2])
            return self.squelch_open
        return None

    # DTCS enable — 0x16 0x4A. DTCS gates RX (and encodes TX) with a
    # digital code instead of a CTCSS tone — the FM "digital squelch".
    def set_dtcs_on(self, on: bool) -> bool:
        resp = self._transact(self._build_frame(
            0x16, subcmd=0x4a, data=bytes([0x01 if on else 0x00])))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.dtcs_on = on
        return ok

    def get_dtcs_on(self) -> bool | None:
        resp = self._transact(self._build_frame(0x16, subcmd=0x4a))
        if resp and len(resp) >= 3 and resp[0] == 0x16 and resp[1] == 0x4a:
            self.dtcs_on = bool(resp[2])
            return self.dtcs_on
        return None

    # DTCS code + polarity — 0x1B 0x07.
    def set_dtcs_code(self, code: int, polarity: int = 0) -> bool:
        resp = self._transact(self._build_frame(
            0x1b, subcmd=0x07, data=_dtcs_to_bytes(code, polarity)))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.dtcs_code = max(0, min(999, int(code)))
            self.dtcs_polarity = max(0, min(3, int(polarity)))
        return ok

    def get_dtcs_code(self):
        """Read DTCS code/polarity. Returns (code, polarity) or None."""
        resp = self._transact(self._build_frame(0x1b, subcmd=0x07))
        if resp and len(resp) >= 5 and resp[0] == 0x1b and resp[1] == 0x07:
            self.dtcs_code, self.dtcs_polarity = _bytes_to_dtcs(resp[2:5])
            return (self.dtcs_code, self.dtcs_polarity)
        return None

    def send_raw(self, cmd_bytes: bytes) -> bytes | None:
        """Send a raw CI-V frame (for CAT passthrough). Returns response body."""
        return self._transact(cmd_bytes)

    # -- VFO / memory commands -----------------------------------------
    # IC-7100 CI-V family — opcodes from the published manual but not yet
    # bench-validated against the radio. Update / correct as we test.

    @staticmethod
    def _ch_to_bcd(ch: int) -> bytes:
        """Encode memory channel 1..99 (or larger banks) as 2-byte BCD."""
        ch = max(0, min(999, int(ch)))
        hi = ch // 100
        rest = ch % 100
        b1 = ((hi & 0x0F) << 4) | ((rest // 10) & 0x0F)
        b2 = ((rest % 10) & 0x0F)
        # Actually IC-7100 uses straight 2-byte BCD: high nybbles + low.
        # ch 23  -> 0x00 0x23 ; ch 99 -> 0x00 0x99 ; ch 100 -> 0x01 0x00.
        hi_b = (ch // 100) & 0xFF
        lo = ch % 100
        lo_b = ((lo // 10) << 4) | (lo % 10)
        return bytes([hi_b, lo_b])

    def select_vfo(self, vfo: str) -> bool:
        """Select VFO A or B (CI-V 0x07 0x00 / 0x01)."""
        v = (vfo or '').strip().upper()
        if v not in ('A', 'B'):
            return False
        sub = 0x00 if v == 'A' else 0x01
        resp = self._transact(self._build_frame(0x07, subcmd=sub))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.active_vfo = v
            self.memory_mode = False
        return ok

    def swap_vfo(self) -> bool:
        """Exchange VFO A <-> B (CI-V 0x07 0xB0)."""
        resp = self._transact(self._build_frame(0x07, subcmd=0xB0))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.active_vfo = 'B' if self.active_vfo == 'A' else 'A'
        return ok

    def equalize_vfo(self) -> bool:
        """Copy active VFO to inactive (A=B). CI-V 0x07 0xA0."""
        resp = self._transact(self._build_frame(0x07, subcmd=0xA0))
        return resp is not None and resp[0:1] == _OK

    # NOTE: select_band(Main/Sub) was removed. The 0x07 0xD0/0xD1 opcodes
    # belong to dual-receiver radios (IC-9100 etc.); the IC-7100 is a
    # single-receiver radio with two antenna ports and has no Main/Sub
    # concept. Confirmed against the IC-7100 CI-V table — opcodes absent.

    def enter_vfo_mode(self) -> bool:
        """Leave memory mode, return to VFO (CI-V 0x07 with no subcmd
        re-selects current VFO and switches to VFO mode)."""
        # 0x07 0x00 also leaves memory mode by selecting VFO A.
        resp = self._transact(self._build_frame(0x07, subcmd=0x00))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.memory_mode = False
        return ok

    def enter_memory_mode(self) -> bool:
        """Switch to memory mode (CI-V 0x08, no data — selects last channel)."""
        resp = self._transact(self._build_frame(0x08))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.memory_mode = True
        return ok

    def select_memory(self, ch: int) -> bool:
        """Switch to memory mode and select channel ch (1..99 typical).
        CI-V 0x08 [hi_bcd] [lo_bcd]."""
        ch = int(ch)
        if ch < 1:
            return False
        resp = self._transact(
            self._build_frame(0x08, data=self._ch_to_bcd(ch)))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.memory_mode = True
            self.memory_channel = ch
        return ok

    # IC-7100 has FOUR CALL channels, addressed as memory channels 106-109:
    #   106 = 144-C1, 107 = 144-C2, 108 = 430-C1, 109 = 430-C2.
    # The earlier 0x08 0xA0 was selecting Memory Bank A — wrong opcode.
    _CALL_CHANNELS = {'144-C1': 106, '144-C2': 107, '430-C1': 108, '430-C2': 109}

    def select_call_channel(self, which: str = '') -> bool:
        """Select a CALL channel by name ('144-C1', '144-C2', '430-C1',
        '430-C2'). With no argument, picks the right band-1 call channel
        based on the current frequency: <300 MHz → 144-C1, else 430-C1."""
        if not which:
            which = '144-C1' if (self.freq_hz or 0) < 300_000_000 else '430-C1'
        ch = self._CALL_CHANNELS.get(which)
        if ch is None:
            return False
        resp = self._transact(
            self._build_frame(0x08, data=self._ch_to_bcd(ch)))
        ok = resp is not None and resp[0:1] == _OK
        if ok:
            self.memory_mode = True
            self.memory_channel = ch
        return ok

    def write_memory(self) -> bool:
        """Write current VFO contents into the currently-selected memory
        channel (CI-V 0x09). Destructive — overwrites the channel."""
        resp = self._transact(self._build_frame(0x09))
        return resp is not None and resp[0:1] == _OK

    def memory_to_vfo(self) -> bool:
        """Copy current memory contents into the VFO (CI-V 0x0A).
        NOTE: earlier code had 0x0A and 0x0B swapped — the IC-7100 manual
        says 0x0A = Memory copy to VFO, 0x0B = Memory clear."""
        resp = self._transact(self._build_frame(0x0a))
        return resp is not None and resp[0:1] == _OK

    def clear_memory(self) -> bool:
        """Clear the currently-selected memory channel (CI-V 0x0B). See
        memory_to_vfo() note — opcodes were swapped in earlier versions."""
        resp = self._transact(self._build_frame(0x0b))
        return resp is not None and resp[0:1] == _OK

    def read_memory(self, ch: int) -> bytes | None:
        """Read raw memory channel contents (CI-V 0x1A 0x00 [hi] [lo]).
        Returns the raw payload bytes for the caller to decode. Channel
        format is radio-model-specific; on the IC-7100 the response
        includes split flag, mode, filter, freq, tone settings, name."""
        ch = int(ch)
        if ch < 1:
            return None
        resp = self._transact(
            self._build_frame(0x1a, subcmd=0x00, data=self._ch_to_bcd(ch)))
        if not resp:
            return None
        # Expect: 0x1a 0x00 [hi_bcd] [lo_bcd] <channel-data...>
        if len(resp) >= 4 and resp[0] == 0x1a and resp[1] == 0x00:
            return resp[4:]
        return None

    # -- State readback (radio → cache sync) ---------------------------
    # The IC-7100 keeps its own setup in NVRAM and has a full front panel —
    # the operator can change anything on the radio directly. These reads
    # pull the radio's current state into the cache so the GUI slaves to
    # the radio. Read opcodes mirror the setters above (a read is the same
    # command with no data byte). Unverified against hardware — see the
    # Phase C note above.

    def _read_flag(self, subcmd: int) -> bool | None:
        """Read a 0x16-family on/off setting. Returns bool or None."""
        resp = self._transact(self._build_frame(0x16, subcmd=subcmd))
        if resp and len(resp) >= 3 and resp[0] == 0x16 and resp[1] == subcmd:
            return bool(resp[2])
        return None

    def _read_level_pct(self, subcmd: int) -> int | None:
        """Read a 0x14-family level control (3-BCD 0..255) as 0..100%."""
        resp = self._transact(self._build_frame(0x14, subcmd=subcmd))
        if resp and len(resp) >= 4 and resp[0] == 0x14 and resp[1] == subcmd:
            raw = _bcd3_to_pct(resp[2:4])
            return max(0, min(100, round(raw * 100 / 255)))
        return None

    def get_rit_on(self) -> bool | None:
        resp = self._transact(self._build_frame(0x21, subcmd=0x01))
        if resp and len(resp) >= 3 and resp[0] == 0x21 and resp[1] == 0x01:
            self.rit_on = bool(resp[2])
            return self.rit_on
        return None

    def get_xit_on(self) -> bool | None:
        resp = self._transact(self._build_frame(0x21, subcmd=0x02))
        if resp and len(resp) >= 3 and resp[0] == 0x21 and resp[1] == 0x02:
            self.xit_on = bool(resp[2])
            return self.xit_on
        return None

    def get_agc(self) -> str | None:
        resp = self._transact(self._build_frame(0x16, subcmd=0x12))
        if resp and len(resp) >= 3 and resp[0] == 0x16 and resp[1] == 0x12:
            self.agc = self._AGC_RMAP.get(resp[2], self.agc)
            return self.agc
        return None

    def get_nb(self):
        """Read Noise Blanker on/off + level. Returns (on, level)."""
        on = self._read_flag(0x22)
        if on is not None:
            self.nb_on = on
        lvl = self._read_level_pct(0x12)
        if lvl is not None:
            self.nb_level = lvl
        return (self.nb_on, self.nb_level)

    def get_nr(self):
        """Read Noise Reduction on/off + level. Returns (on, level)."""
        on = self._read_flag(0x40)
        if on is not None:
            self.nr_on = on
        lvl = self._read_level_pct(0x06)
        if lvl is not None:
            self.nr_level = lvl
        return (self.nr_on, self.nr_level)

    def get_preamp(self) -> int | None:
        resp = self._transact(self._build_frame(0x16, subcmd=0x02))
        if resp and len(resp) >= 3 and resp[0] == 0x16 and resp[1] == 0x02:
            self.preamp = max(0, min(2, resp[2]))
            return self.preamp
        return None

    def get_atten(self) -> bool | None:
        # 0x11 has no subcommand; data byte 0x00 = off, non-zero = on.
        resp = self._transact(self._build_frame(0x11))
        if resp and len(resp) >= 2 and resp[0] == 0x11:
            self.atten = resp[1] != 0x00
            return self.atten
        return None

    def get_if_shift(self) -> int | None:
        v = self._read_level_pct(0x07)
        if v is not None:
            self.if_shift = v
        return v

    def get_squelch(self) -> int | None:
        v = self._read_level_pct(0x03)
        if v is not None:
            self.squelch = v
        return v

    def get_rf_power(self) -> int | None:
        v = self._read_level_pct(0x0A)
        if v is not None:
            self.rf_power = v
        return v

    def get_af_level(self) -> int | None:
        v = self._read_level_pct(0x01)
        if v is not None:
            self.af_level = v
        return v

    def get_mic_gain(self) -> int | None:
        v = self._read_level_pct(0x0B)
        if v is not None:
            self.mic_gain = v
        return v

    def poll_settings(self, abort=None):
        """Read every front-panel-adjustable setting into the cache. Run on
        connect and periodically so a change the operator makes on the radio
        itself propagates back to the GUI.

        Each read sleeps 2 ms after releasing the lock so a user command
        (e.g. PTT) waiting on _transact can preempt — otherwise this thread
        re-grabs the lock instantly and a 5 s settings poll blocks the user
        cmd for the full poll duration (worst case on a mode where several
        opcodes hit 1 s CI-V timeouts).

        If *abort* (a threading.Event) is set partway through, return
        immediately so the user command isn't blocked behind the remaining
        reads. The partial cache is fine — the next settings poll will fill
        it in completely."""
        # DTCS code & polarity excluded: rarely changed by the operator and
        # cost a CI-V round-trip each cycle. Read once at connect via
        # _poll_state(); subsequent changes come back through the regular
        # set/get path when the user touches them.
        for _get in (self.get_split, self.get_rit_offset, self.get_rit_on,
                     self.get_xit_on, self.get_agc, self.get_nb, self.get_nr,
                     self.get_preamp, self.get_atten, self.get_if_shift,
                     self.get_squelch, self.get_rf_power, self.get_mic_gain,
                     self.get_af_level, self.get_ctcss, self.get_dtcs_on):
            if abort is not None and abort.is_set():
                return
            _get()
            time.sleep(0.002)

    def _poll_state(self):
        """Full state snapshot, run on every (re)connect. The IC-7100 is the
        source of truth — it remembers its own setup — so on connect we read
        the radio and let the GUI slave to it. If the first read fails the
        radio isn't actually responding (off, or wrong port); bail rather
        than burn a 1 s timeout on every remaining command."""
        if self.get_freq() is None:
            print("[IC7100-CIV] No response to connect snapshot — "
                  "radio off or wrong port?", flush=True)
            return
        self.get_mode()
        self.get_ptt()
        self.get_smeter()
        self.get_squelch_status()
        self.poll_settings()
        # DTCS code/polarity excluded from periodic poll for latency reasons;
        # grab them once here so the GUI shows the right values on connect.
        # (get_dtcs_code returns both code and polarity in one frame.)
        self.get_dtcs_code()


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

# IC-7100's built-in USB audio is a TI PCM2901 (VID:PID 08bb:2901). The
# chip is generic — ALSA only sees "USB Audio CODEC", no IC-7100/ICOM
# string — so keyword-matching against arecord output finds nothing.
# Match on USB VID:PID instead so we're specific to the radio's codec.
_IC7100_AUDIO_USBIDS = ('08bb:2901',)


def _find_alsa_card(keywords=('IC-7100', 'ICOM', 'icom', 'IC7100')) -> str | None:
    """Return hw:N card spec for the IC-7100 USB audio device, or None.

    Tries USB VID:PID match first (specific to the IC-7100's PCM2901
    codec). Falls back to keyword matching for radios whose codec
    actually does identify itself as IC-7100/ICOM."""
    # USB VID:PID via /proc/asound/cardN/usbid
    try:
        for card_dir in sorted(os.listdir('/proc/asound')):
            if not card_dir.startswith('card'):
                continue
            try:
                with open(f'/proc/asound/{card_dir}/usbid') as f:
                    usbid = f.read().strip().lower()
            except (FileNotFoundError, OSError):
                continue
            if usbid in _IC7100_AUDIO_USBIDS:
                return f'hw:{card_dir[4:]},0'
    except (FileNotFoundError, OSError):
        pass
    # Keyword fallback (for radios that DO have a recognisable name)
    try:
        out = subprocess.check_output(
            ['arecord', '-l'], stderr=subprocess.DEVNULL, timeout=5).decode()
        for line in out.split('\n'):
            for kw in keywords:
                if kw.lower() in line.lower():
                    # line like: "card 2: IC7100 [IC-7100], device 0: ..."
                    parts = line.split()
                    if parts and parts[0] == 'card':
                        card_num = parts[1].rstrip(':')
                        return f'hw:{card_num},0'
    except Exception:
        pass
    return None


class IC7100AudioCapture:
    """Captures RX audio from the IC-7100 USB codec via arecord subprocess."""

    CHUNK = 4800  # 50 ms at 48 kHz 16-bit mono

    def __init__(self, device: str, rx_queue: _queue_mod.Queue):
        self._device = device
        self._queue = rx_queue
        self._thread = None
        self._running = False
        self._proc = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name='IC7100-RXAudio')
        self._thread.start()

    def stop(self):
        self._running = False
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3)

    def _loop(self):
        backoff = 1.0
        while self._running:
            try:
                cmd = [
                    'arecord', '-D', self._device,
                    '-f', 'S16_LE', '-r', '48000', '-c', '1', '-t', 'raw',
                ]
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                backoff = 1.0
                print(f"[IC7100-RXAudio] Started arecord on {self._device}", flush=True)
                while self._running:
                    chunk = self._proc.stdout.read(self.CHUNK)
                    if not chunk:
                        break
                    try:
                        self._queue.put_nowait(chunk)
                    except _queue_mod.Full:
                        try:
                            self._queue.get_nowait()
                        except _queue_mod.Empty:
                            pass
                        try:
                            self._queue.put_nowait(chunk)
                        except _queue_mod.Full:
                            pass
            except Exception as e:
                if self._running:
                    print(f"[IC7100-RXAudio] Error: {e}", flush=True)
            finally:
                try:
                    if self._proc:
                        self._proc.terminate()
                        self._proc.wait(timeout=2)
                except Exception:
                    pass
                self._proc = None
            if self._running:
                print(f"[IC7100-RXAudio] Restarting in {backoff:.0f}s", flush=True)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)


class IC7100AudioPlayback:
    """Sends TX audio to the IC-7100 USB codec via aplay subprocess."""

    def __init__(self, device: str):
        self._device = device
        self._queue: _queue_mod.Queue = _queue_mod.Queue(maxsize=64)
        self._thread = None
        self._running = False
        self._proc = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name='IC7100-TXAudio')
        self._thread.start()

    def stop(self):
        self._running = False
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3)

    def write(self, pcm: bytes):
        """Non-blocking write; drops if buffer is full (>~200ms)."""
        # 64 slots × 4800 bytes = 307 200 bytes ≈ 3.2 s; cap at ~200ms = 4 chunks
        while self._queue.qsize() > 4:
            try:
                self._queue.get_nowait()
            except _queue_mod.Empty:
                break
        try:
            self._queue.put_nowait(pcm)
        except _queue_mod.Full:
            pass

    def _loop(self):
        backoff = 1.0
        while self._running:
            try:
                cmd = [
                    'aplay', '-D', self._device,
                    '-f', 'S16_LE', '-r', '48000', '-c', '1', '-t', 'raw',
                ]
                self._proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
                backoff = 1.0
                print(f"[IC7100-TXAudio] Started aplay on {self._device}", flush=True)
                while self._running:
                    try:
                        chunk = self._queue.get(timeout=0.5)
                        self._proc.stdin.write(chunk)
                        self._proc.stdin.flush()
                    except _queue_mod.Empty:
                        continue
                    except BrokenPipeError:
                        break
            except Exception as e:
                if self._running:
                    print(f"[IC7100-TXAudio] Error: {e}", flush=True)
            finally:
                try:
                    if self._proc:
                        self._proc.terminate()
                        self._proc.wait(timeout=2)
                except Exception:
                    pass
                self._proc = None
            if self._running:
                print(f"[IC7100-TXAudio] Restarting in {backoff:.0f}s", flush=True)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)


# ---------------------------------------------------------------------------
# IC7100Plugin
# ---------------------------------------------------------------------------

class IC7100Plugin(RadioPlugin):
    """ICOM IC-7100 HF/VHF/UHF radio plugin for Gateway Link."""

    name = "ic7100"
    capabilities = {
        "audio_rx":  True,
        "audio_tx":  True,
        "ptt":       True,
        "frequency": True,
        "ctcss":     True,
        "power":     True,
        "rx_gain":   True,
        "tx_gain":   True,
        "smeter":    True,
        # IC-7100 USB audio + CI-V PTT can host packet (Direwolf); 'mode'
        # command dispatch is not yet implemented on this plugin, so until
        # then selecting it from the packet page will surface a clear error.
        "packet":    True,
        "status":    True,
    }

    _RECONNECT_DELAY = 10  # seconds between CI-V reconnect attempts
    _POLL_INTERVAL   = 5   # seconds between background state polls
    # The settings group (AGC/NB/NR/preamp/atten/IF-shift/levels/CTCSS/DTCS)
    # is read every Nth poll cycle — front-panel knob/menu changes are far
    # rarer than tuning, and the group costs ~20 CI-V round-trips. 2 → ~10 s.
    _SETTINGS_POLL_EVERY = 2

    def __init__(self):
        self._civ: CIVController | None = None
        self._capture: IC7100AudioCapture | None = None
        self._playback: IC7100AudioPlayback | None = None
        self._rx_queue: _queue_mod.Queue = _queue_mod.Queue(maxsize=32)
        self._running = False
        self._watchdog_thread = None
        self._poll_thread = None
        self._rx_gain_db = 0.0
        self._tx_gain_db = 0.0
        # TX antenna-port safety interlock. Defaults to both OFF; setup()
        # restores from the settings file if present (operator's previous
        # choice survives endpoint restart so PTT works immediately). The
        # restore prints a loud log line so the safety implication — a
        # stale "enabled" flag could outlive the physical antenna being
        # disconnected while we were down — isn't silent.
        self._tx_allow = {'hf': False, 'vu': False}
        self._status_dirty = False
        # Set whenever execute() is processing a user command. The poll loop
        # checks this between each get_*() so it can abort an in-progress
        # poll cycle and release the serial lock for the user. Without this,
        # rapid GUI knob spins waited up to ~3 s for the lock behind a
        # settings-poll-in-progress, even with the 2 ms yield in poll_settings.
        self._user_cmd_pending = threading.Event()
        self.status_interval = 2.0
        self._settings_file = os.path.expanduser(
            '~/.config/link-endpoint/ic7100-settings.json')
        self._audio_device = None

    # -- Lifecycle --

    def setup(self, config):
        port       = config.get('device', '/dev/ttyUSB0')
        civ_addr   = int(str(config.get('civ_addr', '0x88')), 16)
        # IC-7100 CI-V baud — radio MENU > Set > Connectors > CI-V > CI-V Baud.
        # Default is "Auto" which negotiates to 19200; the radio also supports
        # explicit 4800/9600/19200. Higher than 19200 is not documented on
        # IC-7100. Configurable here so the user can experiment with the
        # radio's "Auto" mode at the link layer if they set the menu to one.
        civ_baud   = int(config.get('civ_baud', 19200))
        audio_hint = config.get('audio_in') or config.get('audio_device')

        saved = self._load_settings()
        if saved:
            self._rx_gain_db = max(-20, min(20, float(saved.get('rx_gain_db', 0))))
            self._tx_gain_db = max(-20, min(20, float(saved.get('tx_gain_db', 0))))
            print(f"[IC7100] Restored gains RX={self._rx_gain_db:+.1f} dB "
                  f"TX={self._tx_gain_db:+.1f} dB", flush=True)
            # TX interlock persistence — re-apply the last known operator
            # choice so PTT works immediately after restart. Loud log so
            # it's obvious which ports are "live" after a service bounce
            # (and to flag the safety implication if the physical antenna
            # was disconnected while we were down).
            _hf = bool(saved.get('tx_allow_hf', False))
            _vu = bool(saved.get('tx_allow_vu', False))
            if _hf or _vu:
                self._tx_allow['hf'] = _hf
                self._tx_allow['vu'] = _vu
                print(f"[IC7100] Restored TX interlocks: "
                      f"HF={'ON' if _hf else 'off'} VU={'ON' if _vu else 'off'} "
                      f"— verify the antenna is still connected!", flush=True)

        # Resolve ALSA device
        if audio_hint:
            self._audio_device = str(audio_hint)
        else:
            found = _find_alsa_card()
            if found:
                print(f"[IC7100] Auto-detected audio device: {found}", flush=True)
                self._audio_device = found
            else:
                print("[IC7100] WARNING: IC-7100 USB audio not found — audio disabled",
                      flush=True)

        # CI-V connection
        self._civ = CIVController(port, baud=civ_baud, civ_addr=civ_addr)
        self._running = True
        if not self._civ.connect():
            print(f"[IC7100] Initial CI-V connect failed — watchdog will retry", flush=True)

        # Audio
        if self._audio_device:
            self._capture = IC7100AudioCapture(self._audio_device, self._rx_queue)
            self._playback = IC7100AudioPlayback(self._audio_device)
            self._capture.start()
            self._playback.start()

        # Background threads
        self._watchdog_thread = threading.Thread(
            target=self._watchdog, daemon=True, name='IC7100-Watchdog')
        self._watchdog_thread.start()

        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name='IC7100-Poll')
        self._poll_thread.start()

        self._meter_thread = threading.Thread(
            target=self._meter_loop, daemon=True, name='IC7100-Meter')
        self._meter_thread.start()

    def teardown(self):
        self._running = False
        if self._capture:
            self._capture.stop()
        if self._playback:
            self._playback.stop()
        if self._civ:
            self._civ.disconnect()
        print("[IC7100] Disconnected", flush=True)

    # -- Audio I/O --

    def get_audio(self, chunk_size=4800):
        try:
            data = self._rx_queue.get_nowait()
            if self._rx_gain_db != 0.0:
                data = _apply_volume(data, _db_to_linear(self._rx_gain_db))
            return data, False
        except _queue_mod.Empty:
            return None, False

    def put_audio(self, pcm):
        # Instrumentation: track each call so the GUI / diagnostics can
        # tell whether the bus is actually feeding TX audio to us (vs no
        # routing at all). Updated even when _playback is missing so a
        # mis-configured audio sink still shows "yes, calls arriving".
        self._tx_audio_calls = getattr(self, '_tx_audio_calls', 0) + 1
        self._tx_audio_last_mono = time.monotonic()
        self.tx_audio_level = _pcm_peak_pct(pcm, getattr(self, 'tx_audio_level', 0))
        if not self._playback:
            return
        if self._tx_gain_db != 0.0:
            pcm = _apply_volume(pcm, _db_to_linear(self._tx_gain_db))
        self._playback.write(pcm)

    # -- Command dispatch --

    def execute(self, cmd):
        if not isinstance(cmd, dict):
            return {"ok": False, "error": "invalid command"}
        # Signal the poll loop to abort any in-progress poll and yield the
        # serial lock. Cleared in finally so an exception in dispatch can't
        # strand the flag.
        self._user_cmd_pending.set()
        try:
            return self._execute_inner(cmd)
        finally:
            self._user_cmd_pending.clear()

    def _execute_inner(self, cmd):
        action = cmd.get('cmd', '')

        if action == 'ptt':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            # Explicit state if provided; otherwise toggle current TX state.
            # The web GUI button sends a bare {cmd:'ptt'} and expects toggle.
            if 'state' in cmd:
                state = bool(cmd['state'])
            else:
                state = not bool(self._civ.transmitting)
            if state:
                # Safety interlock — refuse TX if the antenna port for the
                # current frequency has not been enabled by the operator.
                port = _antenna_port(self._civ.freq_hz)
                if not self._tx_allow[port]:
                    label = 'HF' if port == 'hf' else 'VHF/UHF'
                    return {"ok": False,
                            "error": f"TX blocked — {label} antenna not "
                                     f"enabled (safety interlock)"}
            ok = self._civ.set_ptt(state)
            if ok:
                self._status_dirty = True
            return {"ok": ok, "ptt": state}

        elif action == 'tx_interlock':
            # Enable/disable TX per antenna port. Keys: 'hf', 'vu' (bool).
            if 'hf' in cmd:
                self._tx_allow['hf'] = bool(cmd['hf'])
            if 'vu' in cmd:
                self._tx_allow['vu'] = bool(cmd['vu'])
            # If the operator disables the port we are currently keyed on,
            # drop PTT immediately — the interlock must act, not just warn.
            if (self._civ and self._civ.connected and self._civ.transmitting
                    and not self._tx_allow[_antenna_port(self._civ.freq_hz)]):
                self._civ.set_ptt(False)
            self._status_dirty = True
            self._save_settings()    # persist so it survives endpoint restart
            return {"ok": True,
                    "tx_allow_hf": self._tx_allow['hf'],
                    "tx_allow_vu": self._tx_allow['vu']}

        elif action == 'frequency':
            freq = cmd.get('freq')
            if freq is None:
                return {"ok": False, "error": "missing freq"}
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            ok = self._civ.set_freq(float(freq))
            if ok:
                self._status_dirty = True
            return {"ok": ok, "freq": freq}

        elif action == 'mode':
            mode = cmd.get('mode', '')
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            ok = self._civ.set_mode(mode, int(cmd.get('filter', 1)))
            if ok:
                self._status_dirty = True
            return {"ok": ok, "mode": mode}

        elif action == 'ctcss':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            tx_hz  = float(cmd['tx_hz'])  if 'tx_hz'  in cmd else None
            rx_hz  = float(cmd['rx_hz'])  if 'rx_hz'  in cmd else None
            tx_on  = bool(cmd['tx_on'])   if 'tx_on'  in cmd else None
            rx_on  = bool(cmd['rx_on'])   if 'rx_on'  in cmd else None
            ok = self._civ.set_ctcss(tx_hz=tx_hz, rx_hz=rx_hz,
                                     tx_on=tx_on, rx_on=rx_on)
            if ok:
                self._status_dirty = True
            return {
                "ok": ok,
                "ctcss_tx_hz":  self._civ.ctcss_tx_hz,
                "ctcss_rx_hz":  self._civ.ctcss_rx_hz,
                "ctcss_tx_on":  self._civ.ctcss_tx_on,
                "ctcss_rx_on":  self._civ.ctcss_rx_on,
            }

        elif action == 'cat':
            raw = cmd.get('raw', b'')
            if isinstance(raw, str):
                raw = bytes.fromhex(raw.replace(' ', ''))
            if not raw:
                return {"ok": False, "error": "missing raw bytes"}
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            resp = self._civ.send_raw(raw)
            # CAT is opcode-probing only — caller already gets the response
            # in the ACK. Don't dump every byte to the journal (was useful
            # during initial bring-up; noise otherwise).
            return {"ok": True, "response": resp.hex() if resp else None}

        elif action == 'rx_gain':
            self._rx_gain_db = max(-20, min(20, float(cmd.get('db', 0))))
            self._save_settings()
            return {"ok": True, "rx_gain_db": self._rx_gain_db}

        elif action == 'tx_gain':
            self._tx_gain_db = max(-20, min(20, float(cmd.get('db', 0))))
            self._save_settings()
            return {"ok": True, "tx_gain_db": self._tx_gain_db}

        elif action == 'status':
            return {"ok": True, "status": self.get_status()}

        # -- VFO / memory commands -----------------------------------------
        elif action == 'vfo':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            which = cmd.get('vfo', cmd.get('which', ''))
            ok = self._civ.select_vfo(which)
            if ok:
                self._status_dirty = True
            return {"ok": ok, "active_vfo": self._civ.active_vfo,
                    "memory_mode": self._civ.memory_mode}

        elif action == 'vfo_swap':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            ok = self._civ.swap_vfo()
            if ok:
                self._status_dirty = True
            return {"ok": ok, "active_vfo": self._civ.active_vfo}

        elif action == 'vfo_equalize':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            ok = self._civ.equalize_vfo()
            return {"ok": ok}

        elif action == 'memory_mode':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            on = bool(cmd.get('on', True))
            ok = (self._civ.enter_memory_mode() if on
                  else self._civ.enter_vfo_mode())
            if ok:
                self._status_dirty = True
            return {"ok": ok, "memory_mode": self._civ.memory_mode}

        elif action == 'memory_select':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            try:
                ch = int(cmd.get('channel', cmd.get('ch', 0)))
            except (TypeError, ValueError):
                return {"ok": False, "error": "channel must be int"}
            ok = self._civ.select_memory(ch)
            if ok:
                self._status_dirty = True
            return {"ok": ok, "memory_channel": self._civ.memory_channel,
                    "memory_mode": self._civ.memory_mode}

        elif action == 'call_channel':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            which = str(cmd.get('which', '') or '')
            ok = self._civ.select_call_channel(which)
            if ok:
                self._status_dirty = True
            return {"ok": ok, "memory_mode": self._civ.memory_mode,
                    "memory_channel": self._civ.memory_channel}

        elif action == 'memory_write':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            return {"ok": self._civ.write_memory()}

        elif action == 'memory_clear':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            return {"ok": self._civ.clear_memory()}

        elif action == 'memory_to_vfo':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            return {"ok": self._civ.memory_to_vfo()}

        elif action == 'memory_read':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            try:
                ch = int(cmd.get('channel', cmd.get('ch', 0)))
            except (TypeError, ValueError):
                return {"ok": False, "error": "channel must be int"}
            raw = self._civ.read_memory(ch)
            return {"ok": raw is not None, "channel": ch,
                    "raw_hex": raw.hex() if raw else None}

        # -- HF feature commands (Phase C) ---------------------------------
        elif action == 'split':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            on = bool(cmd.get('on', False))
            ok = self._civ.set_split(on)
            return {"ok": ok, "split": self._civ.split}

        elif action == 'rit':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            ok = True
            if 'hz' in cmd:
                ok &= self._civ.set_rit_offset(int(cmd['hz']))
                # Auto-engage RIT when the GUI sets an offset — treats the
                # knob/display as a "use RIT" intent so the user doesn't
                # have to click the RIT checkbox first. Explicit on=False
                # below still wins.
                if 'on' not in cmd and not self._civ.rit_on:
                    ok &= self._civ.set_rit_on(True)
            if 'on' in cmd:
                ok &= self._civ.set_rit_on(bool(cmd['on']))
            return {"ok": ok, "rit_hz": self._civ.rit_hz, "rit_on": self._civ.rit_on}

        elif action == 'xit':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            on = bool(cmd.get('on', False))
            ok = self._civ.set_xit_on(on)
            return {"ok": ok, "xit_on": self._civ.xit_on}

        elif action == 'agc':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            mode = str(cmd.get('mode', 'mid')).lower()
            ok = self._civ.set_agc(mode)
            return {"ok": ok, "agc": self._civ.agc}

        elif action == 'nb':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            ok = True
            if 'on'    in cmd: ok &= self._civ.set_nb_on(bool(cmd['on']))
            if 'level' in cmd: ok &= self._civ.set_nb_level(int(cmd['level']))
            return {"ok": ok, "nb_on": self._civ.nb_on, "nb_level": self._civ.nb_level}

        elif action == 'nr':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            ok = True
            if 'on'    in cmd: ok &= self._civ.set_nr_on(bool(cmd['on']))
            if 'level' in cmd: ok &= self._civ.set_nr_level(int(cmd['level']))
            return {"ok": ok, "nr_on": self._civ.nr_on, "nr_level": self._civ.nr_level}

        elif action == 'preamp':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            stage = int(cmd.get('stage', 0))
            ok = self._civ.set_preamp(stage)
            return {"ok": ok, "preamp": self._civ.preamp}

        elif action == 'atten':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            on = bool(cmd.get('on', False))
            ok = self._civ.set_atten(on)
            return {"ok": ok, "atten": self._civ.atten}

        elif action == 'if_shift':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            pct = int(cmd.get('pct', 50))
            ok = self._civ.set_if_shift(pct)
            return {"ok": ok, "if_shift": self._civ.if_shift}

        elif action == 'filter':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            idx = int(cmd.get('idx', 1))
            ok = self._civ.set_filter(idx)
            return {"ok": ok, "filter": self._civ.filter_idx}

        elif action == 'squelch':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            pct = int(cmd.get('pct', 0))
            ok = self._civ.set_squelch(pct)
            return {"ok": ok, "squelch": self._civ.squelch}

        elif action == 'power':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            pct = int(cmd.get('pct', 0))
            ok = self._civ.set_rf_power(pct)
            return {"ok": ok, "rf_power": self._civ.rf_power}

        elif action == 'mic_gain':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            pct = int(cmd.get('pct', 0))
            ok = self._civ.set_mic_gain(pct)
            return {"ok": ok, "mic_gain": self._civ.mic_gain}

        elif action == 'af_level':
            # Radio front-panel AF (volume) knob. Separate from the
            # gateway-side audio_boost slider.
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            pct = int(cmd.get('pct', 0))
            ok = self._civ.set_af_level(pct)
            if ok:
                self._status_dirty = True
            return {"ok": ok, "af_level": self._civ.af_level}

        elif action == 'data_mode':
            # Toggle the radio's DATA mode (USB-D / FM-D / ...) so the
            # MOD INPUT chain swaps audio source per the operator's menu
            # configuration. Typical use: DATA OFF MOD = MIC for the
            # operator's hand mic, DATA MOD = USB for gateway-fed audio.
            # Caller: {cmd:'data_mode', on: true|false} (filt optional).
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            on = bool(cmd.get('on', not self._civ.data_mode))   # toggle if absent
            filt = int(cmd.get('filt', self._civ.data_mode_filter))
            ok = self._civ.set_data_mode(on, filt)
            if ok:
                self._status_dirty = True
            return {"ok": ok, "data_mode": self._civ.data_mode}

        elif action == 'squelch_type':
            # FM squelch gating: 'noise' (carrier), 'tsql' (CTCSS tone),
            # or 'dtcs' (digital code). Orchestrates the RX-tone and
            # DTCS enables so only one gating mode is active at a time.
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            stype = str(cmd.get('type', 'noise')).lower()
            if stype not in ('noise', 'tsql', 'dtcs'):
                return {"ok": False, "error": f"bad squelch type: {stype}"}
            ok = True
            ok &= self._civ.set_ctcss(rx_on=(stype == 'tsql'))
            ok &= self._civ.set_dtcs_on(stype == 'dtcs')
            return {"ok": ok, "squelch_type": stype}

        elif action == 'dtcs':
            if not self._civ or not self._civ.connected:
                return {"ok": False, "error": "CI-V not connected"}
            ok = True
            if 'on' in cmd:
                ok &= self._civ.set_dtcs_on(bool(cmd['on']))
            if 'code' in cmd or 'polarity' in cmd:
                code = int(cmd.get('code', self._civ.dtcs_code))
                pol  = int(cmd.get('polarity', self._civ.dtcs_polarity))
                ok &= self._civ.set_dtcs_code(code, pol)
            return {
                "ok": ok,
                "dtcs_on":       self._civ.dtcs_on,
                "dtcs_code":     self._civ.dtcs_code,
                "dtcs_polarity": self._civ.dtcs_polarity,
            }

        return {"ok": False, "error": f"unknown command: {action}"}

    # -- Status --

    def get_status(self):
        status = {"plugin": self.name}
        if self._civ:
            status.update({
                "serial_connected": self._civ.connected,
                "freq":             round(self._civ.freq_hz / 1e6, 6) if self._civ.freq_hz else 0,
                "mode":             self._civ.mode,
                "filter":           self._civ.filter_idx,
                "transmitting":     self._civ.transmitting,
                # Mirror to ptt_active so the gateway's status-push handler
                # (which keys on 'ptt_active') keeps _link_ptt_active fresh
                # for front-panel PTT changes too — not just ACK-driven ones.
                "ptt_active":       self._civ.transmitting,
                "smeter":           self._civ.smeter,
                "po":               self._civ.po,
                "swr":              self._civ.swr,
                "alc":              self._civ.alc,
                "ctcss_tx_hz":      self._civ.ctcss_tx_hz,
                "ctcss_rx_hz":      self._civ.ctcss_rx_hz,
                "ctcss_tx_on":      self._civ.ctcss_tx_on,
                "ctcss_rx_on":      self._civ.ctcss_rx_on,
                # HF feature state
                "split":            self._civ.split,
                "rit_hz":           self._civ.rit_hz,
                "rit_on":           self._civ.rit_on,
                "xit_on":           self._civ.xit_on,
                "agc":              self._civ.agc,
                "nb_on":            self._civ.nb_on,
                "nb_level":         self._civ.nb_level,
                "nr_on":            self._civ.nr_on,
                "nr_level":         self._civ.nr_level,
                "preamp":           self._civ.preamp,
                "atten":            self._civ.atten,
                "if_shift":         self._civ.if_shift,
                "squelch":          self._civ.squelch,
                "rf_power":         self._civ.rf_power,
                "mic_gain":         self._civ.mic_gain,
                "af_level":         self._civ.af_level,
                "data_mode":        self._civ.data_mode,
                "data_mode_filter": self._civ.data_mode_filter,
                # TX audio path instrumentation — confirms whether the
                # bus is actually feeding TX PCM to put_audio(). Zero =
                # no audio reaching us (bus / routing problem). Non-zero
                # but radio silent = downstream (USB MOD level, mode mod
                # input). Counter increments on every put_audio call.
                "tx_audio_calls":   getattr(self, '_tx_audio_calls', 0),
                "tx_audio_level":   int(getattr(self, 'tx_audio_level', 0)),
                "tx_audio_idle_s":  round(time.monotonic() -
                    getattr(self, '_tx_audio_last_mono', time.monotonic() - 999), 1),
                "squelch_open":     self._civ.squelch_open,
                "dtcs_on":          self._civ.dtcs_on,
                "dtcs_code":        self._civ.dtcs_code,
                "dtcs_polarity":    self._civ.dtcs_polarity,
                # Derived FM squelch gating mode for the GUI selector
                "squelch_type":     ('dtcs' if self._civ.dtcs_on
                                     else 'tsql' if self._civ.ctcss_rx_on
                                     else 'noise'),
                # Antenna port the current frequency would TX on
                "tx_port":          _antenna_port(self._civ.freq_hz),
                # VFO / memory — locally tracked (set-only, not read-back yet)
                "active_vfo":       self._civ.active_vfo,
                "memory_mode":      self._civ.memory_mode,
                "memory_channel":   self._civ.memory_channel,
            })
        status.update({
            "audio_device":  self._audio_device,
            # TX antenna-port safety interlock
            "tx_allow_hf":   self._tx_allow['hf'],
            "tx_allow_vu":   self._tx_allow['vu'],
            "audio_rx":      self._capture is not None,
            "audio_tx":      self._playback is not None,
            "input_active":  self._capture is not None,
            "output_active": self._playback is not None,
            "rx_gain_db":    self._rx_gain_db,
            "tx_gain_db":    self._tx_gain_db,
        })
        status.update(self._get_system_stats())
        return status

    # -- Watchdog --

    def _watchdog(self):
        while self._running:
            time.sleep(self._RECONNECT_DELAY)
            if not self._running:
                break
            if self._civ and not self._civ.connected:
                print("[IC7100] Watchdog: CI-V lost — reconnecting", flush=True)
                self._civ.disconnect()
                time.sleep(2)
                while self._running and not self._civ.connect():
                    print(f"[IC7100] Watchdog: retry in {self._RECONNECT_DELAY}s",
                          flush=True)
                    time.sleep(self._RECONNECT_DELAY)
                if self._civ.connected:
                    print("[IC7100] Watchdog: CI-V reconnected", flush=True)
                    # connect() re-ran the full snapshot — push it promptly
                    # so the GUI re-syncs to the radio without a poll wait.
                    self._status_dirty = True

    def _poll_loop(self):
        # Mark this thread as the poll thread so every _transact() it makes
        # uses the short timeout. User-command transacts run on the endpoint
        # reader thread and aren't tagged, so they keep the longer default.
        if self._civ:
            self._civ._tls.in_poll = True
        """Periodically read the radio so the cache — and therefore the GUI —
        tracks the radio, including changes the operator makes on the radio's
        own front panel. The fast group (freq/mode/PTT/meters) runs every
        cycle; the heavier settings group every _SETTINGS_POLL_EVERY cycles.
        Settings polling is skipped during TX so it never contends with the
        meter loop. A user command from the GUI still goes straight to the
        radio via execute() — this loop only pulls state the other way."""
        cycle = 0
        while self._running:
            time.sleep(self._POLL_INTERVAL)
            if not self._running:
                break
            if not (self._civ and self._civ.connected):
                continue
            try:
                before = self._poll_signature()
                # 2 ms yield between each read so user commands can preempt
                # this back-to-back read chain — see poll_settings() comment.
                # Additional abort: if execute() has set _user_cmd_pending,
                # bail out of the poll cycle entirely so the user gets the
                # serial lock now instead of waiting for ~16 reads to finish.
                for _get in (self._civ.get_freq, self._civ.get_mode,
                             self._civ.get_ptt, self._civ.get_smeter,
                             self._civ.get_squelch_status):
                    if self._user_cmd_pending.is_set():
                        break
                    _get()
                    time.sleep(0.002)
                if (cycle % self._SETTINGS_POLL_EVERY == 0
                        and not self._civ.transmitting
                        and not self._user_cmd_pending.is_set()):
                    self._civ.poll_settings(abort=self._user_cmd_pending)
                # A changed signature means the radio moved under us (front
                # panel) — push status now instead of waiting for the timer.
                if self._poll_signature() != before:
                    self._status_dirty = True
            except Exception as e:
                print(f"[IC7100] Poll error: {e}", flush=True)
            cycle += 1

    def _poll_signature(self):
        """Tuple of cached radio state used to detect a front-panel change.
        Fast-changing meters (S-meter, Po/SWR/ALC) are deliberately excluded
        — those ride the regular status interval, not the dirty flag."""
        c = self._civ
        if not c:
            return ()
        return (c.freq_hz, c.mode, c.filter_idx, c.transmitting,
                c.squelch_open, c.ctcss_tx_hz, c.ctcss_rx_hz,
                c.ctcss_tx_on, c.ctcss_rx_on, c.split, c.rit_hz, c.rit_on,
                c.xit_on, c.agc, c.nb_on, c.nb_level, c.nr_on, c.nr_level,
                c.preamp, c.atten, c.if_shift, c.squelch, c.rf_power,
                c.mic_gain, c.af_level, c.dtcs_on, c.dtcs_code, c.dtcs_polarity)

    def _meter_loop(self):
        """While transmitting, read the Po / SWR / ALC meters fast so the
        web TX meters track SSB drive in near-real-time. Idle on RX."""
        while self._running:
            tx = bool(self._civ and self._civ.connected
                      and self._civ.transmitting)
            if tx:
                try:
                    self._civ.get_po()
                    self._civ.get_swr()
                    self._civ.get_alc()
                    self._status_dirty = True
                except Exception as e:
                    print(f"[IC7100] Meter poll error: {e}", flush=True)
                time.sleep(0.08)
            else:
                # RX — the TX meters mean nothing; zero them once. Also
                # piggyback a squelch_status read so the GUI's squelch LED
                # tracks open/close in near-real time (~3 Hz) instead of
                # waiting for the 5 s fast-poll group. Cheap CI-V read.
                if self._civ and (self._civ.po or self._civ.swr
                                   or self._civ.alc):
                    self._civ.po = self._civ.swr = self._civ.alc = 0
                    self._status_dirty = True
                if self._civ and self._civ.connected \
                        and not self._user_cmd_pending.is_set():
                    prev = self._civ.squelch_open
                    try:
                        self._civ.get_squelch_status()
                    except Exception:
                        pass
                    if self._civ.squelch_open != prev:
                        self._status_dirty = True
                time.sleep(0.3)

    # -- Settings persistence --

    def _save_settings(self):
        try:
            d = os.path.dirname(self._settings_file)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(self._settings_file, 'w') as f:
                json.dump({
                    "rx_gain_db":   self._rx_gain_db,
                    "tx_gain_db":   self._tx_gain_db,
                    "tx_allow_hf":  self._tx_allow.get('hf', False),
                    "tx_allow_vu":  self._tx_allow.get('vu', False),
                }, f)
        except Exception as e:
            print(f"[IC7100] Failed to save settings: {e}", flush=True)

    def _load_settings(self):
        try:
            with open(self._settings_file) as f:
                return json.load(f)
        except (FileNotFoundError, ValueError, OSError):
            return None


# ---------------------------------------------------------------------------
# Shared audio utilities (mirrors d75_link_plugin equivalents)
# ---------------------------------------------------------------------------

def _apply_volume(pcm: bytes, gain: float) -> bytes:
    n = len(pcm) // 2
    samples = struct.unpack(f'<{n}h', pcm)
    out = []
    for s in samples:
        v = int(s * gain)
        if v > 32767:  v = 32767
        elif v < -32768: v = -32768
        out.append(v)
    return struct.pack(f'<{n}h', *out)


def _db_to_linear(db: float) -> float:
    return 10 ** (db / 20.0)
