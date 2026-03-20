#!/usr/bin/env python3
"""PTT / relay controller classes for radio-gateway."""

import sys
import os
import time
import signal
import threading
import threading as _thr
import subprocess
import shutil
import json as json_mod
import collections
import queue as _queue_mod
from struct import Struct
import socket
import select
import array as _array_mod
import math as _math_mod
import re
import numpy as np

class RelayController:
    """Controls a CH340 USB relay module via serial (4-byte commands)."""

    CMD_ON  = bytes([0xA0, 0x01, 0x01, 0xA2])
    CMD_OFF = bytes([0xA0, 0x01, 0x00, 0xA1])

    def __init__(self, device, baud=9600):
        self._device = device
        self._baud = baud
        self._port = None
        self._state = None  # None=unknown, True=on, False=off

    def open(self):
        try:
            import serial
            self._port = serial.Serial(self._device, self._baud, timeout=1)
            return True
        except Exception as e:
            print(f"  [Relay] Failed to open {self._device}: {e}")
            return False

    def close(self):
        if self._port:
            try:
                self._port.close()
            except Exception:
                pass
            self._port = None

    def set_state(self, on):
        """Set relay on (True) or off (False). Returns True on success."""
        if not self._port:
            return False
        try:
            self._port.write(self.CMD_ON if on else self.CMD_OFF)
            self._state = on
            return True
        except Exception as e:
            print(f"  [Relay] Write error on {self._device}: {e}")
            return False

    @property
    def state(self):
        return self._state


class GPIORelayController:
    """Controls a relay via Raspberry Pi GPIO pin (BCM numbering)."""

    def __init__(self, gpio_pin):
        self._pin = gpio_pin
        self._state = None
        self._gpio = None

    def open(self):
        try:
            import RPi.GPIO as GPIO
            self._gpio = GPIO
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self._pin, GPIO.OUT, initial=GPIO.LOW)
            self._state = False
            return True
        except Exception as e:
            print(f"  [GPIORelay] Failed to setup GPIO {self._pin}: {e}")
            return False

    def close(self):
        if self._gpio:
            try:
                self._gpio.cleanup(self._pin)
            except Exception:
                pass

    def set_state(self, on):
        """Set relay on (True) or off (False). Returns True on success."""
        if not self._gpio:
            return False
        try:
            self._gpio.output(self._pin, self._gpio.HIGH if on else self._gpio.LOW)
            self._state = on
            return True
        except Exception as e:
            print(f"  [GPIORelay] Error setting GPIO {self._pin}: {e}")
            return False

    @property
    def state(self):
        return self._state


# ============================================================================
# RADIO PTT CONTROLLER CLASSES
# ============================================================================

class RadioPTT:
    """Abstract base class for per-radio PTT controllers."""

    def __init__(self):
        self._keyed = False

    def key(self):
        raise NotImplementedError

    def unkey(self):
        raise NotImplementedError

    def is_keyed(self):
        return self._keyed

    def __repr__(self):
        return f"{self.__class__.__name__}(keyed={self._keyed})"


class TH9800PTT(RadioPTT):
    """PTT for TH-9800: AIOC HID GPIO / USB relay / software CAT toggle.

    method:       'aioc' | 'relay' | 'software'
    get_aioc:     callable → HID device or None
    get_cat:      callable → RadioCATClient or None
    relay:        RelayController instance or None
    aioc_channel: AIOC GPIO channel (1-based, usually 3)
    config:       Config object
    notify:       callable(msg) for user-visible notifications
    """

    def __init__(self, method, get_aioc, get_cat, relay, aioc_channel, config, notify=None):
        super().__init__()
        self._method = method.lower()
        self._get_aioc = get_aioc
        self._get_cat = get_cat
        self._relay = relay
        self._aioc_channel = aioc_channel
        self._config = config
        self._notify = notify or (lambda msg: None)

    def key(self):
        if self._keyed:
            return
        if self._ptt(True):
            self._keyed = True

    def unkey(self):
        if not self._keyed:
            return
        self._ptt(False)
        self._keyed = False  # best-effort: always clear so we don't get stuck keyed

    def _ptt(self, state_on):
        """Dispatch to method-specific implementation. Returns True on success."""
        if self._method == 'relay':
            return self._ptt_relay(state_on)
        elif self._method == 'software':
            return self._ptt_software(state_on)
        else:
            return self._ptt_aioc(state_on)

    def _ptt_aioc(self, state_on):
        from struct import Struct
        aioc = self._get_aioc()
        if not aioc:
            if state_on:
                self._notify("PTT failed: AIOC device not found")
            return False
        cat = self._get_cat()
        try:
            if state_on:
                if cat:
                    cat._pause_drain()
                    try:
                        cat.set_rts(False)  # Radio Controlled
                    except Exception as e:
                        print(f"\n[PTT] RTS switch failed: {e}")
                        # drain stays paused — will be resumed on unkey
            state = 1 if state_on else 0
            iomask = 1 << (self._aioc_channel - 1)
            iodata  = state << (self._aioc_channel - 1)
            data = Struct("<BBBBB").pack(0, 0, iodata, iomask, 0)
            if self._config.VERBOSE_LOGGING:
                print(f"\n[PTT] {'KEYING' if state_on else 'UNKEYING'} TH-9800 (AIOC GPIO{self._aioc_channel})")
            aioc.write(bytes(data))
            if not state_on:
                if cat:
                    try:
                        cat.set_rts(True)  # USB Controlled
                    except Exception as e:
                        print(f"\n[PTT] RTS restore failed: {e}")
                    finally:
                        cat._drain_paused = False
            return True
        except Exception as e:
            print(f"\n[PTT] AIOC error: {e}")
            self._notify(f"PTT error: {e}")
            if cat and cat._drain_paused:
                cat._drain_paused = False
            return False

    def _ptt_relay(self, state_on):
        if not self._relay:
            return False
        self._relay.set_state(state_on)
        if self._config.VERBOSE_LOGGING:
            print(f"\n[PTT] {'KEYING' if state_on else 'UNKEYING'} TH-9800 (relay)")
        return True

    def _ptt_software(self, state_on):
        cat = self._get_cat()
        if not cat:
            if state_on:
                self._notify("PTT failed: CAT not connected")
            return False
        try:
            cat._pause_drain()
            try:
                resp = cat._send_cmd("!ptt")
            finally:
                cat._drain_paused = False
            if resp and 'serial not connected' in resp.lower():
                self._notify("PTT failed: radio serial not connected")
                return False
            if resp is None:
                self._notify("PTT failed: no response from CAT server")
                return False
            if self._config.VERBOSE_LOGGING:
                print(f"\n[PTT] {'KEYING' if state_on else 'UNKEYING'} TH-9800 (software/CAT)")
            return True
        except Exception as e:
            print(f"\n[PTT] CAT !ptt error: {e}")
            self._notify(f"PTT failed: {e}")
            return False


class D75PTT(RadioPTT):
    """PTT for Kenwood D75/D74 via CAT !ptt on / !ptt off.

    D75 uses explicit on/off (not a toggle like TH-9800 software PTT).
    No RTS switching needed.
    """

    def __init__(self, get_d75, config, notify=None):
        super().__init__()
        self._get_d75 = get_d75
        self._config = config
        self._notify = notify or (lambda msg: None)

    def key(self):
        if self._keyed:
            return
        d75 = self._get_d75()
        if not d75:
            self._notify("PTT failed: D75 not connected")
            return
        try:
            resp = d75.send_command("!ptt on")
            if resp and 'serial not connected' in str(resp).lower():
                self._notify("PTT failed: D75 serial not connected")
                return
            if resp is None:
                self._notify("PTT failed: no response from D75")
                return
            self._keyed = True
            if self._config.VERBOSE_LOGGING:
                print(f"\n[PTT] KEYING D75 (CAT)")
        except Exception as e:
            print(f"\n[PTT] D75 !ptt error: {e}")
            self._notify(f"PTT failed: {e}")

    def unkey(self):
        if not self._keyed:
            return
        d75 = self._get_d75()
        if not d75:
            self._keyed = False  # device gone — assume unkeyed
            return
        try:
            d75.send_command("!ptt off")
            self._keyed = False
            if self._config.VERBOSE_LOGGING:
                print(f"\n[PTT] UNKEYING D75 (CAT)")
        except Exception as e:
            print(f"\n[PTT] D75 !ptt error: {e}")
            self._notify(f"PTT failed: {e}")


class KV4PPTT(RadioPTT):
    """PTT for KV4P HT via serial ptt_on / ptt_off."""

    def __init__(self, get_cat, get_audio_source, config, notify=None):
        super().__init__()
        self._get_cat = get_cat
        self._get_audio = get_audio_source
        self._config = config
        self._notify = notify or (lambda msg: None)

    def key(self):
        if self._keyed:
            return
        cat = self._get_cat()
        if not cat:
            self._notify("PTT failed: KV4P not connected")
            return
        try:
            cat.ptt_on()
            self._keyed = True
            if self._config.VERBOSE_LOGGING:
                print(f"\n[PTT] KEYING KV4P")
        except Exception as e:
            print(f"\n[PTT] KV4P ptt error: {e}")
            self._notify(f"PTT failed: {e}")

    def unkey(self):
        if not self._keyed:
            return
        cat = self._get_cat()
        if not cat:
            self._keyed = False
            return
        try:
            cat.ptt_off()
            # Discard partial Opus frame so it doesn't bleed into next TX
            audio_src = self._get_audio()
            if audio_src:
                audio_src._tx_buf = b''
            self._keyed = False
            if self._config.VERBOSE_LOGGING:
                print(f"\n[PTT] UNKEYING KV4P")
        except Exception as e:
            print(f"\n[PTT] KV4P ptt error: {e}")
            self._notify(f"PTT failed: {e}")
