"""
Unit tests for IC-7100 CI-V plugin — no hardware required.

A CIVSimulator replaces the serial port with an in-process loopback that
speaks the IC-7100 CI-V protocol.  Tests exercise:
  - BCD encode/decode helpers
  - CIVController framing and command methods
  - IC7100Plugin.execute() dispatch
  - Watchdog reconnect logic

Run:
    python3 -m pytest tests/test_ic7100_civ.py -v
    # or without pytest:
    python3 tests/test_ic7100_civ.py
"""

import sys
import os
import struct
import threading
import time
import socket
import queue
import unittest

# Add project root and tools/ to path
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here)
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, 'tools'))

from ic7100_link_plugin import (
    CIVController,
    IC7100Plugin,
    _freq_to_bcd, _bcd_to_freq,
    _tone_to_bcd, _bcd_to_tone,
    _CTCSS,
)


# ---------------------------------------------------------------------------
# CI-V Simulator
# ---------------------------------------------------------------------------

class CIVSimulator:
    """
    In-process simulator of an IC-7100 CI-V port.

    Listens on a socket pair; CIVController connects to the fake serial port
    via a monkey-patched serial.Serial that wraps one end of a socketpair.

    The simulator maintains radio state and responds to recognised commands
    with correct CI-V frames.
    """

    PREAMBLE   = b'\xfe\xfe'
    END        = b'\xfd'
    OK         = b'\xfb'
    NG         = b'\xfa'
    CTRL_ADDR  = 0xe0
    RADIO_ADDR = 0x88

    def __init__(self):
        # Simulated radio state
        self.freq_hz  = 146_520_000
        self.mode     = 0x05  # FM
        self.filter   = 0x01
        self.ptt      = False
        self.smeter   = 120   # mid-scale
        self.ctcss_tx_hz  = 88.5
        self.ctcss_rx_hz  = 88.5
        self.ctcss_tx_on  = False
        self.ctcss_rx_on  = False

        # Set up socket pair; expose one end as _sock_ctrl for the controller
        self._sock_radio, self._sock_ctrl = socket.socketpair()
        self._thread = threading.Thread(
            target=self._serve, daemon=True, name='CIVSim')
        self._running = True
        self._thread.start()

    def stop(self):
        self._running = False
        try:
            self._sock_radio.close()
        except Exception:
            pass
        try:
            self._sock_ctrl.close()
        except Exception:
            pass

    # -- Fake serial.Serial --

    def make_fake_serial(self):
        """Return a duck-typed serial.Serial that talks to the simulator."""
        sock = self._sock_ctrl
        sim = self

        class FakeSerial:
            def __init__(self):
                self._buf = b''

            def reset_input_buffer(self):
                pass  # nothing pending on connect

            def write(self, data: bytes):
                sock.sendall(data)

            def read(self, n: int) -> bytes:
                # Return buffered data if available, else wait up to 0.1s
                try:
                    sock.settimeout(0.1)
                    chunk = sock.recv(n)
                    return chunk
                except Exception:
                    return b''

            def close(self):
                pass  # sim socket closed by stop()

        return FakeSerial()

    # -- Simulator server --

    def _serve(self):
        buf = b''
        while self._running:
            try:
                self._sock_radio.settimeout(0.2)
                data = self._sock_radio.recv(256)
                if not data:
                    break
                buf += data
                while True:
                    frame, buf = self._extract_frame(buf)
                    if frame is None:
                        break
                    resp = self._handle(frame)
                    if resp:
                        # Echo the frame back (IC-7100 echoes its own transmissions)
                        self._sock_radio.sendall(frame)
                        self._sock_radio.sendall(resp)
            except socket.timeout:
                continue
            except Exception:
                break

    def _extract_frame(self, buf):
        """Pull first complete FE FE … FD frame from buf. Returns (frame, remainder)."""
        idx = buf.find(b'\xfe\xfe')
        if idx == -1:
            return None, buf
        end = buf.find(b'\xfd', idx + 4)
        if end == -1:
            return None, buf
        return buf[idx:end+1], buf[end+1:]

    def _make_resp(self, data: bytes) -> bytes:
        return self.PREAMBLE + bytes([self.CTRL_ADDR, self.RADIO_ADDR]) + data + self.END

    def _ok(self) -> bytes:
        return self._make_resp(self.OK)

    def _handle(self, frame: bytes) -> bytes | None:
        # frame = FE FE [radio] [ctrl] [cmd] [subcmd?] [data...] FD
        if len(frame) < 6:
            return None
        # frame[2]=dest=0x88, frame[3]=src=0xe0, frame[4]=cmd
        cmd = frame[4]
        payload = frame[5:-1]  # everything between cmd and FD (may include subcmd+data)

        if cmd == 0x03:   # read freq
            return self._make_resp(bytes([0x03]) + _freq_to_bcd(self.freq_hz))

        elif cmd == 0x05:  # set freq
            if len(payload) >= 5:
                self.freq_hz = _bcd_to_freq(payload[:5])
            return self._ok()

        elif cmd == 0x04:  # read mode
            return self._make_resp(bytes([0x04, self.mode, self.filter]))

        elif cmd == 0x06:  # set mode
            if len(payload) >= 2:
                self.mode = payload[0]
                self.filter = payload[1]
            return self._ok()

        elif cmd == 0x15:  # read meter
            if payload and payload[0] == 0x02:
                # S-meter: encode smeter (0-255) as 2 BCD bytes (3 digits)
                v = min(255, max(0, self.smeter))
                b0 = (v // 100) | ((v % 100 // 10) << 4)
                b1 = (v % 10)
                return self._make_resp(bytes([0x15, 0x02, b0, b1]))

        elif cmd == 0x16:  # set/get func
            if not payload:
                return None
            sub = payload[0]
            if len(payload) == 1:  # read
                if sub == 0x42:
                    return self._make_resp(bytes([0x16, 0x42,
                                                  0x01 if self.ctcss_rx_on else 0x00]))
                elif sub == 0x43:
                    return self._make_resp(bytes([0x16, 0x43,
                                                  0x01 if self.ctcss_tx_on else 0x00]))
            elif len(payload) == 2:  # write
                if sub == 0x42:
                    self.ctcss_rx_on = bool(payload[1])
                    return self._ok()
                elif sub == 0x43:
                    self.ctcss_tx_on = bool(payload[1])
                    return self._ok()

        elif cmd == 0x1b:  # CTCSS tone freq
            if not payload:
                return None
            sub = payload[0]
            if len(payload) == 1:  # read
                if sub == 0x00:
                    return self._make_resp(
                        bytes([0x1b, 0x00]) + _tone_to_bcd(self.ctcss_tx_hz))
                elif sub == 0x01:
                    return self._make_resp(
                        bytes([0x1b, 0x00]) + _tone_to_bcd(self.ctcss_rx_hz))
            elif len(payload) >= 3:  # write
                hz = _bcd_to_tone(payload[1:3])
                if sub == 0x00:
                    self.ctcss_tx_hz = hz
                elif sub == 0x01:
                    self.ctcss_rx_hz = hz
                return self._ok()

        elif cmd == 0x1c:  # PTT
            if not payload:
                return None
            sub = payload[0]
            if sub == 0x00:
                if len(payload) == 1:  # read
                    return self._make_resp(
                        bytes([0x1c, 0x00, 0x01 if self.ptt else 0x00]))
                else:  # write
                    self.ptt = bool(payload[1])
                    return self._ok()

        return None  # unrecognised — no response


# ---------------------------------------------------------------------------
# Helper: patch CIVController to use FakeSerial
# ---------------------------------------------------------------------------

def _make_patched_controller(sim: CIVSimulator) -> CIVController:
    """Return a CIVController wired to the simulator instead of a real port."""
    ctrl = CIVController.__new__(CIVController)
    ctrl._port = '/dev/null (simulated)'
    ctrl._baud = 19200
    ctrl._addr = 0x88
    ctrl._timeout = 1.0
    ctrl._serial = sim.make_fake_serial()
    ctrl._lock = threading.Lock()
    ctrl.connected = True
    ctrl.freq_hz = 0
    ctrl.mode = 'FM'
    ctrl.filter_idx = 1
    ctrl.transmitting = False
    ctrl.smeter = 0
    ctrl.ctcss_tx_hz = 88.5
    ctrl.ctcss_rx_hz = 88.5
    ctrl.ctcss_tx_on = False
    ctrl.ctcss_rx_on = False
    ctrl._poll_state()
    return ctrl


# ---------------------------------------------------------------------------
# Tests: BCD helpers
# ---------------------------------------------------------------------------

class TestBCDHelpers(unittest.TestCase):

    def test_freq_roundtrip(self):
        for hz in [146_520_000, 14_225_000, 432_100_000, 1_296_000_000, 50_000]:
            self.assertEqual(_bcd_to_freq(_freq_to_bcd(hz)), hz,
                             f"Roundtrip failed for {hz} Hz")

    def test_freq_146mhz(self):
        bcd = _freq_to_bcd(146_520_000)
        self.assertEqual(len(bcd), 5)
        self.assertEqual(_bcd_to_freq(bcd), 146_520_000)

    def test_tone_roundtrip(self):
        for hz in _CTCSS:
            decoded = _bcd_to_tone(_tone_to_bcd(hz))
            self.assertAlmostEqual(decoded, hz, places=1,
                                   msg=f"Tone roundtrip failed for {hz} Hz")

    def test_tone_885(self):
        bcd = _tone_to_bcd(88.5)
        self.assertEqual(len(bcd), 2)
        self.assertAlmostEqual(_bcd_to_tone(bcd), 88.5, places=1)

    def test_tone_1000(self):
        # 100.0 Hz
        bcd = _tone_to_bcd(100.0)
        self.assertAlmostEqual(_bcd_to_tone(bcd), 100.0, places=1)


# ---------------------------------------------------------------------------
# Tests: CIVController ↔ simulator
# ---------------------------------------------------------------------------

class TestCIVController(unittest.TestCase):

    def setUp(self):
        self.sim = CIVSimulator()
        self.ctrl = _make_patched_controller(self.sim)

    def tearDown(self):
        self.ctrl.disconnect()
        self.sim.stop()

    def test_get_freq_initial(self):
        freq = self.ctrl.get_freq()
        self.assertIsNotNone(freq)
        self.assertAlmostEqual(freq, 146.52, places=3)

    def test_set_get_freq(self):
        ok = self.ctrl.set_freq(144.200)
        self.assertTrue(ok)
        freq = self.ctrl.get_freq()
        self.assertAlmostEqual(freq, 144.200, places=3)
        self.assertEqual(self.sim.freq_hz, 144_200_000)

    def test_set_freq_hf(self):
        ok = self.ctrl.set_freq(14.225)
        self.assertTrue(ok)
        self.assertEqual(self.sim.freq_hz, 14_225_000)

    def test_get_mode_initial(self):
        mode = self.ctrl.get_mode()
        self.assertEqual(mode, 'FM')

    def test_set_get_mode(self):
        ok = self.ctrl.set_mode('USB')
        self.assertTrue(ok)
        mode = self.ctrl.get_mode()
        self.assertEqual(mode, 'USB')
        self.assertEqual(self.sim.mode, 0x01)

    def test_set_mode_invalid(self):
        ok = self.ctrl.set_mode('INVALID')
        self.assertFalse(ok)

    def test_get_smeter(self):
        self.sim.smeter = 120
        val = self.ctrl.get_smeter()
        self.assertIsNotNone(val)

    def test_ptt_on_off(self):
        ok = self.ctrl.set_ptt(True)
        self.assertTrue(ok)
        self.assertTrue(self.sim.ptt)
        self.assertTrue(self.ctrl.transmitting)

        ok = self.ctrl.set_ptt(False)
        self.assertTrue(ok)
        self.assertFalse(self.sim.ptt)
        self.assertFalse(self.ctrl.transmitting)

    def test_get_ptt(self):
        self.sim.ptt = True
        ptt = self.ctrl.get_ptt()
        self.assertTrue(ptt)

    def test_ctcss_set_tx_freq(self):
        ok = self.ctrl.set_ctcss(tx_hz=103.5)
        self.assertTrue(ok)
        self.assertAlmostEqual(self.sim.ctcss_tx_hz, 103.5, places=1)

    def test_ctcss_set_rx_freq(self):
        ok = self.ctrl.set_ctcss(rx_hz=141.3)
        self.assertTrue(ok)
        self.assertAlmostEqual(self.sim.ctcss_rx_hz, 141.3, places=1)

    def test_ctcss_enable_tx(self):
        ok = self.ctrl.set_ctcss(tx_on=True)
        self.assertTrue(ok)
        self.assertTrue(self.sim.ctcss_tx_on)
        self.assertTrue(self.ctrl.ctcss_tx_on)

    def test_ctcss_enable_rx(self):
        ok = self.ctrl.set_ctcss(rx_on=True)
        self.assertTrue(ok)
        self.assertTrue(self.sim.ctcss_rx_on)
        self.assertTrue(self.ctrl.ctcss_rx_on)

    def test_ctcss_full_set(self):
        ok = self.ctrl.set_ctcss(
            tx_hz=88.5, rx_hz=88.5, tx_on=True, rx_on=True)
        self.assertTrue(ok)
        self.assertAlmostEqual(self.sim.ctcss_tx_hz, 88.5, places=1)
        self.assertAlmostEqual(self.sim.ctcss_rx_hz, 88.5, places=1)
        self.assertTrue(self.sim.ctcss_tx_on)
        self.assertTrue(self.sim.ctcss_rx_on)

    def test_ctcss_read_back(self):
        self.sim.ctcss_tx_hz = 136.5
        self.sim.ctcss_rx_hz = 136.5
        self.sim.ctcss_tx_on = True
        self.sim.ctcss_rx_on = False
        tx_hz, rx_hz, tx_on, rx_on = self.ctrl.get_ctcss()
        self.assertAlmostEqual(tx_hz, 136.5, places=1)
        self.assertAlmostEqual(rx_hz, 136.5, places=1)
        self.assertTrue(tx_on)
        self.assertFalse(rx_on)

    def test_ctcss_all_tones_roundtrip(self):
        for hz in _CTCSS:
            ok = self.ctrl.set_ctcss(tx_hz=hz)
            self.assertTrue(ok, f"set_ctcss failed for {hz} Hz")
            self.assertAlmostEqual(self.sim.ctcss_tx_hz, hz, places=1,
                                   msg=f"CTCSS tone mismatch for {hz} Hz")

    def test_poll_state_updates_cache(self):
        self.sim.freq_hz = 14_225_000
        self.sim.mode = 0x01  # USB
        self.ctrl._poll_state()
        self.assertEqual(self.ctrl.freq_hz, 14_225_000)
        self.assertEqual(self.ctrl.mode, 'USB')


# ---------------------------------------------------------------------------
# Tests: IC7100Plugin.execute() dispatch
# ---------------------------------------------------------------------------

class TestIC7100PluginExecute(unittest.TestCase):
    """Tests plugin command dispatch without hardware or audio threads."""

    def setUp(self):
        self.sim = CIVSimulator()
        self.plugin = IC7100Plugin.__new__(IC7100Plugin)
        # Minimal init matching __init__ but without threads
        self.plugin._civ = _make_patched_controller(self.sim)
        self.plugin._capture = None
        self.plugin._playback = None
        self.plugin._rx_queue = queue.Queue(maxsize=32)
        self.plugin._running = False
        self.plugin._watchdog_thread = None
        self.plugin._poll_thread = None
        self.plugin._rx_gain_db = 0.0
        self.plugin._tx_gain_db = 0.0
        self.plugin._status_dirty = False
        self.plugin.status_interval = 2.0
        self.plugin._settings_file = '/tmp/ic7100-test-settings.json'
        self.plugin._audio_device = None

    def tearDown(self):
        self.plugin._civ.disconnect()
        self.sim.stop()

    def test_ptt_on(self):
        r = self.plugin.execute({'cmd': 'ptt', 'state': True})
        self.assertTrue(r['ok'])
        self.assertTrue(self.sim.ptt)

    def test_ptt_off(self):
        self.sim.ptt = True
        r = self.plugin.execute({'cmd': 'ptt', 'state': False})
        self.assertTrue(r['ok'])
        self.assertFalse(self.sim.ptt)

    def test_frequency(self):
        r = self.plugin.execute({'cmd': 'frequency', 'freq': 144.200})
        self.assertTrue(r['ok'])
        self.assertEqual(self.sim.freq_hz, 144_200_000)

    def test_frequency_missing(self):
        r = self.plugin.execute({'cmd': 'frequency'})
        self.assertFalse(r['ok'])

    def test_mode(self):
        r = self.plugin.execute({'cmd': 'mode', 'mode': 'USB'})
        self.assertTrue(r['ok'])
        self.assertEqual(self.sim.mode, 0x01)

    def test_ctcss_set(self):
        r = self.plugin.execute({'cmd': 'ctcss', 'tx_hz': 88.5, 'tx_on': True})
        self.assertTrue(r['ok'])
        self.assertAlmostEqual(r['ctcss_tx_hz'], 88.5, places=1)
        self.assertTrue(r['ctcss_tx_on'])
        self.assertTrue(self.sim.ctcss_tx_on)

    def test_ctcss_all_params(self):
        r = self.plugin.execute({'cmd': 'ctcss',
                                 'tx_hz': 103.5, 'rx_hz': 103.5,
                                 'tx_on': True,  'rx_on': True})
        self.assertTrue(r['ok'])
        self.assertAlmostEqual(r['ctcss_tx_hz'], 103.5, places=1)
        self.assertAlmostEqual(r['ctcss_rx_hz'], 103.5, places=1)
        self.assertTrue(r['ctcss_tx_on'])
        self.assertTrue(r['ctcss_rx_on'])

    def test_rx_gain(self):
        r = self.plugin.execute({'cmd': 'rx_gain', 'db': 6.0})
        self.assertTrue(r['ok'])
        self.assertAlmostEqual(self.plugin._rx_gain_db, 6.0)

    def test_rx_gain_clamp(self):
        r = self.plugin.execute({'cmd': 'rx_gain', 'db': 99.0})
        self.assertTrue(r['ok'])
        self.assertAlmostEqual(self.plugin._rx_gain_db, 20.0)

    def test_tx_gain(self):
        r = self.plugin.execute({'cmd': 'tx_gain', 'db': -3.0})
        self.assertTrue(r['ok'])
        self.assertAlmostEqual(self.plugin._tx_gain_db, -3.0)

    def test_status(self):
        r = self.plugin.execute({'cmd': 'status'})
        self.assertTrue(r['ok'])
        s = r['status']
        self.assertEqual(s['plugin'], 'ic7100')
        self.assertIn('freq', s)
        self.assertIn('mode', s)
        self.assertIn('ctcss_tx_hz', s)

    def test_unknown_command(self):
        r = self.plugin.execute({'cmd': 'bananas'})
        self.assertFalse(r['ok'])

    def test_invalid_command(self):
        r = self.plugin.execute("not a dict")
        self.assertFalse(r['ok'])

    def test_cat_passthrough(self):
        # Raw read-freq frame: FE FE 88 E0 03 FD
        frame = bytes([0xfe, 0xfe, 0x88, 0xe0, 0x03, 0xfd])
        r = self.plugin.execute({'cmd': 'cat', 'raw': frame.hex()})
        self.assertTrue(r['ok'])
        self.assertIsNotNone(r['response'])


# ---------------------------------------------------------------------------
# Tests: watchdog reconnect logic (no audio)
# ---------------------------------------------------------------------------

class TestWatchdog(unittest.TestCase):

    def test_watchdog_reconnects_on_disconnect(self):
        """Watchdog should re-call connect() when connected goes False."""
        sim = CIVSimulator()
        plugin = IC7100Plugin.__new__(IC7100Plugin)
        plugin._civ = _make_patched_controller(sim)
        plugin._running = True
        plugin._watchdog_thread = None
        plugin._poll_thread = None
        plugin._RECONNECT_DELAY = 1  # fast for test

        connect_calls = []
        original_connect = plugin._civ.connect

        def fake_connect():
            connect_calls.append(1)
            plugin._civ.connected = True
            return True

        plugin._civ.connect = fake_connect

        # Mark disconnected — watchdog should detect and reconnect
        plugin._civ.connected = False

        wt = threading.Thread(target=plugin._watchdog, daemon=True)
        wt.start()
        # RECONNECT_DELAY=1 + watchdog's internal sleep(2) = 3s before connect();
        # wait 4.5s to be safe
        time.sleep(4.5)
        plugin._running = False
        wt.join(timeout=3)

        self.assertGreaterEqual(len(connect_calls), 1,
                                "Watchdog should have called connect()")
        sim.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    unittest.main(verbosity=2)
