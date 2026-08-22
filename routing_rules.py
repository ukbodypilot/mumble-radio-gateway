"""Routing-graph rules shared by the UI, the HTTP API and BusManager.

One rule so far: a sink may be fed by at most one bus.

Nothing used to enforce it, and the graph editor happily draws the second
connection. What happens next depends entirely on the sink, and none of the
outcomes are "the two mix":

- Queue sinks (mumble, broadcastify, transcription, remote_audio_tx, speaker)
  share ONE bounded deque keyed by sink_id in BusManager._enqueue_sink. Two
  buses append into it and the drain thread sends each payload in turn, so the
  far end gets 50 ms fragments of the two sources ALTERNATING, at twice the
  real-time rate, until the queue backs up and starts dropping.
- broadcastify_l / broadcastify_r are a per-tick slot, not a queue. The second
  bus overwrites the first within the same tick and its audio is silently
  discarded. (The two SIDES are different sink ids, so the normal dual-channel
  feed is unaffected — that is two buses into two sinks.)
- Radio *_tx sinks are the worst. Every SoloBus builds its own _PttWorker
  (audio_bus.py), while _get_radio_plugin returns the SHARED plugin object, so
  two buses give two PTT threads driving one radio with private _desired /
  _applied state. When the first bus unkeys, the radio drops carrier; the
  second worker still believes it is keyed and its loop only acts on a CHANGE,
  so it never re-keys. The second bus then transmits into an unkeyed radio for
  the rest of its transmission, logging nothing, with every meter still moving.

Mixing several sources is a within-bus operation (mix_audio_streams, with the
ducking and priorities that go with it). Put the sources on one bus instead.
"""

# The null sink discards audio and exists to park a bus, so any number of
# buses may point at it.
UNCONSTRAINED_SINKS = frozenset({'nul'})


def find_sink_conflicts(connections):
    """Find sinks fed by more than one bus.

    `connections` is the routing config list of {'type','from','to'} dicts.
    Returns a list of {'sink': id, 'busses': [bus_id, ...]}, sorted, empty
    when the graph is legal. Duplicate identical connections are collapsed
    first: they are the same edge saved twice, not two buses.
    """
    by_sink = {}
    for c in connections or []:
        if c.get('type') != 'bus-sink':
            continue
        sink = c.get('to')
        bus = c.get('from')
        if not sink or not bus or sink in UNCONSTRAINED_SINKS:
            continue
        by_sink.setdefault(sink, [])
        if bus not in by_sink[sink]:
            by_sink[sink].append(bus)
    return [{'sink': s, 'busses': b}
            for s, b in sorted(by_sink.items()) if len(b) > 1]


def describe_conflicts(conflicts):
    """One-line human summary, for an API error or a startup warning."""
    parts = [f"{c['sink']} ← {' + '.join(c['busses'])}" for c in conflicts]
    return ("A sink can only be fed by one bus (put the sources on one bus "
            "and let it mix them): " + "; ".join(parts))
