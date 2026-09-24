#!/usr/bin/env python3
"""Instruction-level diff of two builds, procedure by procedure (layout order).

  python3 tools/pcode_diff.py a.exe b.exe --runtime VBRUN300.DLL [--max N]
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pcode_disasm as P  # noqa: E402


def listing(exe: Path, rt: P.Runtime) -> list[tuple[P.Proc, list[str]]]:
    segs = P.parse_ne(exe)
    out = []
    for p in P.find_procs(segs):
        insns, _ = P.decode(rt, segs[p.segment - 1].data, p)
        out.append((p, [P.fmt(rt, i).split(":", 1)[1].strip() for i in insns]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a", type=Path)
    ap.add_argument("b", type=Path)
    ap.add_argument("--runtime", type=Path, required=True)
    ap.add_argument("--max", type=int, default=12)
    args = ap.parse_args()
    rt = P.Runtime(args.runtime)
    la, lb = listing(args.a, rt), listing(args.b, rt)
    for (pa, xa), (pb, xb) in zip(la, lb):
        if xa == xb:
            continue
        print(f"== seg{pa.segment}@{pa.start} record@{pa.record}/{pb.record}")
        n = 0
        for line in difflib.unified_diff(xa, xb, lineterm="", n=1):
            if line.startswith(("---", "+++")):
                continue
            print("  " + line)
            n += 1
            if n >= args.max:
                break
    if len(la) != len(lb):
        print(f"procedure count {len(la)} vs {len(lb)}")


if __name__ == "__main__":
    main()
