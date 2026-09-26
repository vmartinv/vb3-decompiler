#!/usr/bin/env python3
"""
Disassembles the p-code of a VB3-compiled executable, procedure by procedure.

The decoding lives in src/ (ne.py, runtime.py, symbols.py); this module
re-exports it for the tools (`import pcode_disasm as P`) and is the
disassembler's command line. Two findings make this possible (see ../OPCODES.md, "Threaded code" and
"Procedure -> segment resolution"):

1. VB3 p-code is *threaded code*. Every 2-byte "opcode" is the near address
   of its handler inside VBRUN300.DLL's interpreter segment (segment 25 of
   the stock VBRUN300.DLL); each handler ends by fetching the next word
   (`es:lodsw; jmp ax`). So each opcode's operand length is derived here by
   statically exploring its x86 handler and counting how far it advances
   SI before dispatching. The word just before each handler is the
   interpreter's own opcode ID, shared by type-specialized variants.

2. Procedure records live in segment 3, and each record's owning code
   segment is given by an NE INTREF relocation at record+38 (on disk the
   bytes there are fixup-chain links). Record+24 / +36 are the procedure's
   [start, end) offsets within that code segment.

Needs your own copy of VBRUN300.DLL (not included) and `capstone`
(`pip install capstone`).

Usage:
    python3 tools/pcode_disasm.py <exe> --runtime <VBRUN300.DLL> [--out FILE]
    python3 tools/pcode_disasm.py <exe> --runtime <VBRUN300.DLL> --check
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ne import *  # noqa: E402,F401,F403
from opcodes import NAMES  # noqa: E402,F401
from runtime import *  # noqa: E402,F401,F403
from symbols import *  # noqa: E402,F401,F403


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("exe", type=Path)
    ap.add_argument("--runtime", type=Path, required=True, help="path to your VBRUN300.DLL")
    ap.add_argument("--vbx-dir", type=Path, action="append", default=[],
                    help="directory with the project's VBX files (default: next to the EXE)")
    ap.add_argument("--out", type=Path, help="write listing here instead of stdout")
    ap.add_argument("--check", action="store_true", help="only report decode coverage")
    args = ap.parse_args()

    rt = Runtime(args.runtime)
    rt.vbx_dirs = [args.exe.parent] + args.vbx_dir
    segs = parse_ne(args.exe)
    procs = find_procs(segs)
    res = rcdata(args.exe)
    symbols = Symbols(rt, segs, res)

    lines, clean, failures = [], 0, []
    for p in procs:
        data = segs[p.segment - 1].data
        insns, err = decode(rt, data, p)
        clean += err is None
        if err:
            failures.append((p, err))
        lines.append(f"proc seg{p.segment}[{p.start}:{p.end}) record@{p.record} tag={p.tag:#x}"
                     + (f"   !! {err}" if err else ""))
        notes = symbols.annotate(p.segment, insns)
        lines += [fmt(rt, i, t) for i, t in zip(insns, notes)]
        lines.append("")

    # Coverage of each code segment by procedure records (should be exact).
    for seg in segs[PROC_TABLE_SEGMENT:]:
        ranges = sorted((p.start, p.end) for p in procs if p.segment == seg.index)
        pos = 0
        for s, e in ranges:
            if s != pos:
                failures.append((None, f"seg{seg.index}: gap/overlap at {pos}..{s}"))
            pos = e
        if ranges and pos != seg.length:
            failures.append((None, f"seg{seg.index}: records cover {pos} of {seg.length} bytes"))

    summary = f"{clean}/{len(procs)} procedures decoded exactly to their end offset"
    if args.check:
        print(summary)
        for p, err in failures:
            print(f"  {'seg%d[%d:%d)' % (p.segment, p.start, p.end) if p else ''} {err}")
        sys.exit(0 if not failures else 1)
    text = "\n".join(lines) + f"\n; {summary}\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
        print(summary, "->", args.out)
    else:
        print(text)


if __name__ == "__main__":
    main()
