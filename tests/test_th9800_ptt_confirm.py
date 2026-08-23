"""A PTT that never reached the radio must not report success.

_ptt_via_software threw the CAT server's reply away. A key that never happened
was therefore indistinguishable from one that did: _set_ptt marked the radio
keyed, execute() returned {"ok": True}, and the dashboard, /status and MCP all
reported a transmission that did not exist. The server says plenty -- "serial
not connected" when the FTDI link is down, which RF ingress on 2m does to this
radio -- and none of it was read.

Replies are echoed (`CMD{ptt[off]} False`), so the state is the LAST token; the
echo itself contains on/off and must not be matched on.
"""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from plugins.th9800 import TH9800Plugin  # noqa: E402

FAIL = []


def check(name, cond, detail=''):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{' — ' + detail if detail else ''}")
    if not cond:
        FAIL.append(name)


class FakeCat:
    """Stands in for RadioCATClient. `reply` may be a value or a callable."""
    def __init__(self, reply):
        self.reply = reply
        self.sent = []
        self._drain_paused = False

    def _pause_drain(self):
        self._drain_paused = True

    def _send_cmd(self, cmd):
        self.sent.append(cmd)
        r = self.reply
        if callable(r):
            return r(cmd)
        if isinstance(r, Exception):
            raise r
        return r


def new_plugin(reply, method='software'):
    p = object.__new__(TH9800Plugin)
    p._ptt_lock = threading.RLock()
    p._ptt_active = False
    p._ptt_ok = True
    p._ptt_failures = 0
    p._ptt_last_error = ''
    p._ptt_change_time = 0
    p._ptt_method = method
    p._cat_client = FakeCat(reply) if reply is not None or True else None
    p._relay_ptt = None
    p._aioc_device = None
    return p


def echo(cmd):
    """Mimic the real server: echoes the command, then the resulting state."""
    state = 'True' if cmd.endswith('on') else 'False'
    return f"CMD{{ptt[{'on' if cmd.endswith('on') else 'off'}]}} {state}"


print("\n1. a confirmed key reports success")
p = new_plugin(echo)
ok = p._set_ptt(True)
check("returns True", ok is True)
check("sent the explicit command", p._cat_client.sent == ['!ptt on'],
      str(p._cat_client.sent))
check("marked confirmed", p._ptt_ok is True and p._ptt_active is True)
check("no failures recorded", p._ptt_failures == 0)

print("\n2. 'serial not connected' is a FAILURE, not a success")
p = new_plugin("CMD{ptt[on]} serial not connected")
ok = p._set_ptt(True)
check("returns False", ok is False)
check("failure counted", p._ptt_failures == 1, f"n={p._ptt_failures}")
check("reason recorded", 'serial not connected' in p._ptt_last_error,
      p._ptt_last_error)
check("still believes it asked for a key", p._ptt_active is True,
      "so the following unkey is always attempted — never strand a keyed radio")

print("\n3. the echo must not be mistaken for the state")
# 'CMD{ptt[off]} False' contains 'off'; only the LAST token is the state.
p = new_plugin(echo)
p._set_ptt(True)
ok = p._set_ptt(False)
check("unkey confirmed from the trailing token", ok is True, str(p._ptt_last_error))
check("sent the off command", p._cat_client.sent[-1] == '!ptt off')

print("\n4. radio disagreeing with the request is a failure")
p = new_plugin("CMD{ptt[on]} False")      # asked to key, radio says unkeyed
ok = p._set_ptt(True)
check("returns False", ok is False)
check("reason names the disagreement", 'reports ptt=false' in p._ptt_last_error.lower(),
      p._ptt_last_error)

print("\n5. unusable replies are failures, not assumed success")
for reply, label in ((None, 'no reply'), ('', 'empty reply'),
                     ('CMD{ptt[on]} banana', 'unparsable')):
    p = new_plugin(reply)
    check(f"{label} -> failure", p._set_ptt(True) is False, p._ptt_last_error)
p = new_plugin(RuntimeError('socket died'))
check("exception -> failure", p._set_ptt(True) is False, p._ptt_last_error)

print("\n6. a failed key is RETRIED, not swallowed as a no-op")
# The old guard returned early whenever state matched, so a failed key could
# never be retried — the same stale-mirror trap the CAT server itself had.
calls = {'n': 0}
def flaky(cmd):
    calls['n'] += 1
    return "serial not connected" if calls['n'] == 1 else echo(cmd)
p = new_plugin(flaky)
check("first attempt fails", p._set_ptt(True) is False)
check("second attempt is actually sent", p._set_ptt(True) is True,
      f"sent={p._cat_client.sent}")
check("both commands went out", p._cat_client.sent == ['!ptt on', '!ptt on'],
      str(p._cat_client.sent))

print("\n7. a confirmed state IS a no-op (no needless traffic)")
p = new_plugin(echo)
p._set_ptt(True)
n = len(p._cat_client.sent)
p._set_ptt(True)
check("repeat of a confirmed state sends nothing", len(p._cat_client.sent) == n)

print("\n8. execute() reports the truth to callers")
p = new_plugin(echo)
check("success -> ok True", p.execute({'cmd': 'ptt', 'state': True}) ==
      {'ok': True, 'ptt': True})
p = new_plugin("CMD{ptt[on]} serial not connected")
r = p.execute({'cmd': 'ptt', 'state': True})
check("failure -> ok False with a reason", r['ok'] is False and r.get('error'),
      str(r))

print("\n9. relay / AIOC paths are unchanged (silence = success)")
p = new_plugin(echo, method='relay')
check("relay: no reply still counts as applied", p._set_ptt(True) is True)
check("relay: sent nothing over CAT", p._cat_client.sent == [])

print(f"\n{'ALL PASS' if not FAIL else 'FAILURES: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
