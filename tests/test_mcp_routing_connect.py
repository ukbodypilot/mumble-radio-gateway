"""routing_connect / routing_disconnect send the payload the handler reads.

Both tools posted {'from': ..., 'to': ...}. _routing_cmd_connect reads
data.get('source') / ('bus') / ('sink'), so every call fell through to its
final `return {'ok': False, 'error': 'specify source+bus or bus+sink'}`.
They are the ONLY callers of the connect/disconnect commands -- the routing UI
saves via save_all -- so neither the tools nor those two handler branches had
ever run.

Also covers the auto-detect, which was a hardcoded sink-id list that had
already drifted: it named kv4p_tx (no longer a sink) and omitted
transcription, so `routing_connect(<bus>, 'transcription')` was classified
source-bus.
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

FAIL = []


def check(name, cond, detail=''):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{' — ' + detail if detail else ''}")
    if not cond:
        FAIL.append(name)


# The module registers tools on import via @mcp.tool(); stub the server module
# so importing it here does not need a live MCP instance.
posted = []
GRAPH = {
    'sources': [{'id': i} for i in ('sdr1', 'sdr2', 'aioc', 'webmic', 'mumble_rx')],
    'busses': [{'id': i} for i in ('sdr2', 'allstar', 'listener', 'webmic')],
    'sinks': [{'id': i} for i in ('mumble', 'broadcastify_l', 'speaker',
                                  'transcription', 'nul', 'ic7100_tx')],
}

fake = types.ModuleType('mcp_server.server')
fake.mcp = types.SimpleNamespace(tool=lambda *a, **k: (lambda f: f))
fake._get = lambda path: GRAPH if path == '/routing/status' else {}
fake._post = lambda path, body: (posted.append(body) or {'ok': True})
sys.modules['mcp_server.server'] = fake

import mcp_server.tools.routing as R  # noqa: E402

print("\n1. the payload the handler actually reads")
posted.clear()
R.routing_connect('listener', 'speaker')
check("bus→sink sends bus/sink, not from/to",
      posted[-1] == {'cmd': 'connect', 'bus': 'listener', 'sink': 'speaker'},
      str(posted[-1]))
posted.clear()
R.routing_connect('sdr1', 'allstar')
check("source→bus sends source/bus",
      posted[-1] == {'cmd': 'connect', 'source': 'sdr1', 'bus': 'allstar'},
      str(posted[-1]))
posted.clear()
R.routing_disconnect('listener', 'speaker')
check("disconnect uses the same shape",
      posted[-1] == {'cmd': 'disconnect', 'bus': 'listener', 'sink': 'speaker'},
      str(posted[-1]))
check("no payload still carries the old from/to keys",
      all('from' not in p and 'to' not in p for p in posted), str(posted))

print("\n2. auto-detect reads the live graph, not a hardcoded list")
posted.clear()
R.routing_connect('listener', 'transcription')
check("transcription is recognised as a sink (the old list missed it)",
      posted[-1] == {'cmd': 'connect', 'bus': 'listener', 'sink': 'transcription'},
      str(posted[-1]))
posted.clear()
R.routing_connect('listener', 'ic7100_tx')
check("a _tx sink still classifies as bus-sink",
      posted[-1]['sink'] == 'ic7100_tx', str(posted[-1]))

print("\n3. ids that are BOTH a source and a bus")
# 'sdr2' and 'webmic' each name a source AND a bus in the real config, so the
# classifier cannot key off the left-hand id alone.
posted.clear()
R.routing_connect('sdr2', 'broadcastify_l')
check("sdr2 → sink is bus-sink",
      posted[-1] == {'cmd': 'connect', 'bus': 'sdr2', 'sink': 'broadcastify_l'},
      str(posted[-1]))
posted.clear()
R.routing_connect('sdr2', 'allstar')
check("sdr2 → bus is source-bus",
      posted[-1] == {'cmd': 'connect', 'source': 'sdr2', 'bus': 'allstar'},
      str(posted[-1]))

print("\n4. an explicit connection_type is honoured")
posted.clear()
R.routing_connect('listener', 'speaker', connection_type='bus-sink')
check("explicit bus-sink skips detection", posted[-1]['sink'] == 'speaker')

print("\n5. bad edges are named, not guessed at")
posted.clear()
out = R.routing_connect('nope', 'speaker')
check("unknown left id is reported", 'not a known source or bus' in out, out)
check("nothing was posted", posted == [], str(posted))
out = R.routing_connect('sdr1', 'nope')
check("unknown right id is reported", 'not a known bus or sink' in out, out)
out = R.routing_connect('sdr1', 'aioc')          # source → source
# 'aioc' is a source, so it fails the right-hand test first and gets the more
# specific message. Either wording is a refusal; what matters is that it names
# the offending id and posts nothing.
check("source→source is refused", 'not a known bus or sink' in out, out)
out = R.routing_connect('listener', 'allstar')   # bus → bus
check("bus→bus is refused", 'not a legal edge' in out, out)

print("\n6. a server refusal reaches the caller")
# The module did `from mcp_server.server import _post`, so it holds its own
# reference — patch the name ON the module under test, not on the stub.
R._post = lambda path, body: {'ok': False, 'error': 'A sink can only be fed by one bus: speaker ← a + b'}
out = R.routing_connect('listener', 'speaker')
check("the one-bus-per-sink message is surfaced verbatim",
      'only be fed by one bus' in out, out)

print(f"\n{'ALL PASS' if not FAIL else 'FAILURES: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
