"""A source that never asserts PTT must still key a PLUGIN radio.

RemoteAudioSource ends every path with `return raw, False`. That source could
already key a LINK endpoint, because bus_manager's deliver loop keys those
from audio level alone (LINK_AUTO_PTT_THRESHOLD). A plugin radio like the
TH-9800 is keyed ONLY by SoloBus, from the source's PTT flag -- so the same
source, on the same bus type, produced audio and moved every meter while the
radio never keyed. Which sink kind you wired decided whether PTT worked.

SoloBus now keys on TX audio level as a fallback. This pins that, and pins
the cases that must NOT key.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import audio_bus  # noqa: E402
from audio_util import rms_to_level, pcm_rms  # noqa: E402

FAIL = []


def check(name, cond, detail=''):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{' — ' + detail if detail else ''}")
    if not cond:
        FAIL.append(name)


class Cfg:
    PTT_RELEASE_DELAY = 0.2
    LINK_AUTO_PTT_THRESHOLD = 10
    PTT_PREKEY_BUFFER_MS = 500
    AUDIO_RATE = 48000
    AUDIO_CHUNK_SIZE = 2400


class Radio:
    """Stand-in for a plugin radio (TH-9800) or a link endpoint."""
    def __init__(self, name='TESTRADIO', tx_muted=False):
        self.name = name
        self.tx_muted = tx_muted
        self.keys = []
        self.audio = []

    def execute(self, cmd):
        if cmd.get('cmd') == 'ptt':
            self.keys.append(bool(cmd.get('state')))
        return {'ok': True}

    def put_audio(self, pcm):
        self.audio.append(pcm)

    def get_audio(self, n):
        # An RX+TX radio is read for RX audio when the bus is not tx_only.
        # Returning its own received audio is exactly the feedback path case 4
        # exists to guard against.
        return None, False


class Src:
    """A TX source. `asserts_ptt` mirrors the flag get_audio returns."""
    def __init__(self, pcm, asserts_ptt=False, name='SDRSV'):
        self.name = name
        self.enabled = True
        self.muted = False
        self.ptt_control = True      # capability, NOT the returned flag
        self.audio_boost = 1.0
        self.priority = 2
        self.duck = False
        self._pcm = pcm
        self._ptt = asserts_ptt

    def get_audio(self, n):
        return self._pcm, self._ptt


def loud(level_target=60, n=2400):
    """PCM whose 0-100 level is comfortably above the threshold."""
    import numpy as np
    amp = int(32767 * level_target / 100)
    return (np.ones(n, dtype='int16') * amp).tobytes()


def quiet(n=2400):
    import numpy as np
    return (np.ones(n, dtype='int16') * 8).tobytes()


def make_bus(src, tx_only=True, radio=None):
    b = audio_bus.SoloBus('tester', Cfg)
    radio = radio or Radio()
    b.set_radio(radio, routing_id='aioc_tx')
    b._tx_only = tx_only
    b.add_tx_source(src, routing_id='remote_audio')
    return b, radio


def run_ticks(bus, n=3, dt=0.02):
    for _ in range(n):
        bus.tick(2400)
        time.sleep(dt)


print("\n0. sanity: the test tone really is above/below the threshold")
check("loud tone clears threshold", rms_to_level(pcm_rms(loud())) >= 10,
      f"level={rms_to_level(pcm_rms(loud()))}")
check("quiet tone is below threshold", rms_to_level(pcm_rms(quiet())) < 10,
      f"level={rms_to_level(pcm_rms(quiet()))}")

print("\n1. the bug: source returns ptt=False, plugin radio must still key")
bus, radio = make_bus(Src(loud(), asserts_ptt=False))
run_ticks(bus)
check("radio was keyed", True in radio.keys, f"keys={radio.keys}")
check("bus reports PTT active", bus._ptt_active is True)
bus.shutdown()

print("\n2. a quiet source must NOT key")
bus, radio = make_bus(Src(quiet(), asserts_ptt=False))
run_ticks(bus)
check("radio never keyed", True not in radio.keys, f"keys={radio.keys}")
bus.shutdown()

print("\n3. flag-asserting sources are unchanged")
bus, radio = make_bus(Src(loud(), asserts_ptt=True))
run_ticks(bus)
check("still keys (flag path intact)", True in radio.keys)
bus.shutdown()
# ...and a flag-asserting source keys even when quiet, as it always did
bus, radio = make_bus(Src(quiet(), asserts_ptt=True))
run_ticks(bus)
check("flag beats level: quiet flagged source still keys", True in radio.keys,
      "the level trigger must not become a gate on the flag path")
bus.shutdown()

print("\n4. an RX+TX radio must not key itself from its own receiver")
# _tx_only False means the radio came from a SOURCE and is RX+TX. Keying it
# on the level of audio it just received is a feedback loop.
bus, radio = make_bus(Src(loud(), asserts_ptt=False), tx_only=False)
run_ticks(bus)
check("no self-keying when radio is RX+TX", True not in radio.keys,
      f"keys={radio.keys}")
bus.shutdown()

print("\n5. a tx_muted radio must not be keyed by the level trigger")
bus, radio = make_bus(Src(loud(), asserts_ptt=False), radio=Radio(tx_muted=True))
run_ticks(bus)
check("muted radio stays unkeyed", True not in radio.keys, f"keys={radio.keys}")
bus.shutdown()

print("\n6. AUTO_PTT_THRESHOLD=0 restores flag-only keying")
class Cfg0(Cfg):
    AUTO_PTT_THRESHOLD = 0
_saved, audio_bus_cfg = Cfg, None
bus = audio_bus.SoloBus('tester', Cfg0)
r = Radio(); bus.set_radio(r, routing_id='aioc_tx'); bus._tx_only = True
bus.add_tx_source(Src(loud(), asserts_ptt=False), routing_id='remote_audio')
run_ticks(bus)
check("disabled: no level keying", True not in r.keys, f"keys={r.keys}")
bus.shutdown()

print("\n7. key releases after the hold expires")
bus, radio = make_bus(Src(loud(), asserts_ptt=False))
run_ticks(bus, n=2)
check("keyed", True in radio.keys)
bus._tx_sources[0].source._pcm = quiet()      # source goes quiet
time.sleep(Cfg.PTT_RELEASE_DELAY + 0.05)
run_ticks(bus, n=2)
check("unkeyed after PTT_RELEASE_DELAY", False in radio.keys, f"keys={radio.keys}")
bus.shutdown()

print(f"\n{'ALL PASS' if not FAIL else 'FAILURES: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
