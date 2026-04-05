#!/usr/bin/env python3
"""Parse sigrok I2C decoded text into transactions and summarize/diff them."""

import re
import sys
import json
from collections import Counter
from pathlib import Path

CAPTURE_DIR = Path("/home/user/ftm150-re/captures")

def parse_transactions(filepath):
    with open(filepath) as f:
        lines = f.readlines()

    transactions = []
    current_addrs = []
    current_data = []
    in_transaction = False

    for line in lines:
        line = line.strip()
        if 'Start' in line and 'repeat' not in line:
            if in_transaction and (current_addrs or current_data):
                transactions.append({'addrs': current_addrs, 'data': current_data})
            current_addrs = []
            current_data = []
            in_transaction = True
        m = re.search(r'Address (read|write): ([0-9A-Fa-f]+)', line)
        if m:
            current_addrs.append(f"{'R' if m.group(1)=='read' else 'W'}@0x{m.group(2).upper()}")
        m = re.search(r'Data (read|write): ([0-9A-Fa-f]+)', line)
        if m:
            current_data.append(f"{'r' if m.group(1)=='read' else 'w'}0x{m.group(2).upper()}")
        if 'Stop' in line:
            if in_transaction:
                transactions.append({'addrs': current_addrs, 'data': current_data})
            current_addrs = []
            current_data = []
            in_transaction = False

    if in_transaction and (current_addrs or current_data):
        transactions.append({'addrs': current_addrs, 'data': current_data})

    return transactions

def summarize(transactions):
    print(f"Total transactions: {len(transactions)}")
    addr_patterns = Counter()
    for t in transactions:
        key = ' '.join(t['addrs'])
        addr_patterns[key] += 1
    print("\nAddress patterns:")
    for p, c in addr_patterns.most_common(20):
        print(f"  {c:5d}x  {p}")

    print("\nFirst 20 transactions:")
    for i, t in enumerate(transactions[:20]):
        addrs = ' '.join(t['addrs'])
        data = ' '.join(t['data'][:16])
        extra = f" ...+{len(t['data'])-16}" if len(t['data']) > 16 else ""
        print(f"  T{i:03d}: [{addrs}] {data}{extra}")

def diff_captures(file_a, file_b):
    ta = parse_transactions(file_a)
    tb = parse_transactions(file_b)

    print(f"Capture A ({Path(file_a).stem}): {len(ta)} transactions")
    print(f"Capture B ({Path(file_b).stem}): {len(tb)} transactions")

    # Compare address pattern frequencies
    def addr_freq(txns):
        c = Counter()
        for t in txns:
            c[' '.join(t['addrs'])] += 1
        return c

    fa, fb = addr_freq(ta), addr_freq(tb)
    all_keys = sorted(set(fa.keys()) | set(fb.keys()))
    print("\nAddress pattern diff (A vs B):")
    for k in all_keys:
        a, b = fa.get(k, 0), fb.get(k, 0)
        if a != b:
            print(f"  {k:40s}  {a:5d} -> {b:5d}  ({b-a:+d})")

    # Find data bytes that differ in matching transaction types
    print("\nData differences in common transaction types:")
    def group_by_addr(txns):
        groups = {}
        for t in txns:
            key = ' '.join(t['addrs'])
            groups.setdefault(key, []).append(t['data'])
        return groups

    ga, gb = group_by_addr(ta), group_by_addr(tb)
    for key in sorted(set(ga.keys()) & set(gb.keys())):
        da, db = ga[key], gb[key]
        # Compare byte-by-byte across all transactions of this type
        unique_a = set(tuple(d) for d in da)
        unique_b = set(tuple(d) for d in db)
        only_b = unique_b - unique_a
        if only_b and len(only_b) <= 20:
            print(f"\n  [{key}] new data patterns in B:")
            for d in sorted(only_b)[:10]:
                print(f"    {' '.join(d)}")

def export_json(transactions, outfile):
    with open(outfile, 'w') as f:
        json.dump(transactions, f, indent=2)
    print(f"Exported {len(transactions)} transactions to {outfile}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage:")
        print("  decode.py summary <capture.txt>")
        print("  decode.py diff <baseline.txt> <action.txt>")
        print("  decode.py export <capture.txt> <output.json>")
        print(f"\nAvailable captures in {CAPTURE_DIR}:")
        for f in sorted(CAPTURE_DIR.glob("*.txt")):
            print(f"  {f.name}")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == 'summary':
        txns = parse_transactions(sys.argv[2])
        summarize(txns)
    elif cmd == 'diff':
        diff_captures(sys.argv[2], sys.argv[3])
    elif cmd == 'export':
        txns = parse_transactions(sys.argv[2])
        export_json(txns, sys.argv[3])
    else:
        print(f"Unknown command: {cmd}")
