#!/usr/bin/env python3
"""Config value parsing: inline comments, quoting, and the '#' that isn't one.

The load-bearing case is PACKET_APRS_SYMBOL. Its default '/#' was unreachable
from gateway_config.txt for the life of the file: the parser stripped
surrounding quotes BEFORE splitting on '#', so quoting could not protect the
value, and it treated every '#' as a comment start, so the bare symbol was
silently truncated to '/'. Setting the key did something other than what it
said, with no error anywhere.

Case "bare hash symbol" is the regression. Cases "spaced comment" and
"unspaced hash" pin the two directions of the whitespace rule against each
other, because a rule that fixes one by breaking the other is not a fix.
"""

import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _tmpdirs import mkdtemp   # noqa: E402  (registers cleanup on import)


from config_format import parse_config_value as parse, format_config_value as fmt

CASES = [
    # (name,                     raw value,                 expected)
    ("bare hash symbol",         "/#",                      "/#"),
    ("quoted hash symbol",       '"/#"',                    "/#"),
    ("single-quoted hash",       "'/#'",                    "/#"),
    ("spaced comment",           "foo # a remark",          "foo"),
    ("tab-spaced comment",       "foo\t# a remark",         "foo"),
    ("unspaced hash kept",       "foo#bar",                 "foo#bar"),
    ("comment only",             "# just a remark",         ""),
    ("plain value",              "146.520",                 "146.520"),
    ("quoted with trailing note", '"/#"  # the APRS symbol', "/#"),
    ("quoted empty",             '""',                      ""),
    ("brace exempt",             "{say #1 now}",            "{say #1 now}"),
    ("brace then comment",       "{say #1} # a remark",     "{say #1}"),
    ("nested braces",            "{a {b #c} d}",            "{a {b #c} d}"),
    ("unterminated quote",       '"foo',                    '"foo'),
    ("value with spaces",        "Radio Gateway",           "Radio Gateway"),
    ("path value",               "/dev/ttyUSB0",            "/dev/ttyUSB0"),
    ("url with fragment",        "http://h/p#frag",         "http://h/p#frag"),
    ("trailing whitespace",      "  foo   ",                "foo"),
]


def main():
    failed = 0
    for name, raw, expected in CASES:
        got = parse(raw)
        ok = got == expected
        if not ok:
            failed += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {name:24} {raw!r:26} -> {got!r}"
              + ("" if ok else f"   expected {expected!r}"))

    # End-to-end: the real loader must produce '/#' from the example template,
    # where the key is commented out, AND from a file that sets it explicitly.
    print("\n  end-to-end through Config.load_config:")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, 'radio_gateway.py')).read()
    # Locate the class by content, not by line number: importing
    # radio_gateway outright needs pyaudio/pymumble, and a hardcoded line
    # range silently execs the wrong region the moment anything above it
    # moves -- which is exactly what this change did.
    tree = ast.parse(src)
    node = next(n for n in tree.body
                if isinstance(n, ast.ClassDef) and n.name == 'Config')
    body = '\n'.join(src.split('\n')[node.lineno - 1:node.end_lineno])
    ns = {}
    exec("import os\nfrom config_format import parse_config_value\n" + body, ns)
    Config = ns['Config']

    d = mkdtemp(prefix='cfgparse-')
    path = os.path.join(d, 'gateway_config.txt')
    with open(path, 'w') as f:
        f.write("[packet]\n")
        f.write("PACKET_APRS_SYMBOL = /#\n")
        f.write("PACKET_CALLSIGN = N0CALL   # a real comment\n")
        f.write('PACKET_APRS_COMMENT = "Radio # Gateway"\n')
    c = Config(path)

    for key, expected in [('PACKET_APRS_SYMBOL', '/#'),
                          ('PACKET_CALLSIGN', 'N0CALL'),
                          ('PACKET_APRS_COMMENT', 'Radio # Gateway')]:
        got = getattr(c, key, '<<MISSING>>')
        ok = got == expected
        if not ok:
            failed += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {key:24} -> {got!r}"
              + ("" if ok else f"   expected {expected!r}"))

    # Round trip: whatever the web config form writes must read back as the
    # same value. This is the property that matters -- a reader fix alone still
    # loses data if _save_config serialises a value the reader will truncate.
    print("\n  round trip through format_config_value -> parse_config_value:")
    for value in ["/#", "N0CALL", "Radio # Gateway", "trailing # note", "",
                  "146.520", "/dev/ttyUSB0", "a 'quoted' word", 'say "hi"',
                  "{say #1 now}", "http://h/p#frag", "  padded  "]:
        got = parse(fmt(value))
        # Exact equality, padding included: format_config_value quotes anything
        # the reader would alter, and a quoted value is literal, so the round
        # trip is lossless even for leading/trailing whitespace.
        expected = value
        ok = got == expected
        if not ok:
            failed += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {value!r:22} -> wrote {fmt(value)!r:26} -> {got!r}"
              + ("" if ok else f"   expected {expected!r}"))

    print("\nALL PASS" if not failed else f"\n{failed} FAILED")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
