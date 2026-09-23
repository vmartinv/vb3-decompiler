#!/usr/bin/env python3
"""
Builds and queries the source-aligned corpus used to name p-code handlers.

  build:    align every compiled project (<name>.mak + <name>.exe side by
            side) under a directory; writes one JSON per project.
  examples: for each handler, the shortest source lines it appears in.

Usage:
    python3 tools/corpus.py build <projects-root> <corpus-dir> --runtime VBRUN300.DLL
    python3 tools/corpus.py examples <corpus-dir> --runtime VBRUN300.DLL [--exe X.exe] [HANDLER ...]

With --exe, `examples` lists the handlers that executable uses, most
frequent first (default: every handler in the corpus).
"""
from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import pcode_disasm as P  # noqa: E402


def build(root: Path, out: Path, runtime: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for mak in sorted(root.rglob("*.mak")):
        exe = mak.with_suffix(".exe")
        if not exe.is_file():
            continue
        r = subprocess.run([sys.executable, str(TOOLS / "align_source.py"), str(mak), str(exe),
                            "--runtime", str(runtime), "-v", "--json", str(out / f"{mak.stem}.json")],
                           capture_output=True, text=True)
        print(r.stdout.strip())
        if r.stderr.strip():
            print("   " + r.stderr.strip().replace("\n", "\n   "))


def examples(corpus: Path, runtime: Path, exe: Path | None, wanted: list[int], per: int = 4) -> None:
    rt = P.Runtime(runtime)
    ex = collections.defaultdict(set)
    for f in corpus.glob("*.json"):
        for e in json.loads(f.read_text()):
            code = " ".join(f"{op:04x}" + (":" + x if x else "") for op, x in e["code"])
            for op, _ in e["code"]:
                ex[op].add((len(e["src"]), e["src"], code))
    counts: collections.Counter = collections.Counter()
    if exe:
        segs = P.parse_ne(exe)
        for p in P.find_procs(segs):
            for i in P.decode(rt, segs[p.segment - 1].data, p)[0]:
                counts[i.op] += 1
    ops = wanted or ([op for op, _ in counts.most_common()] if exe else sorted(ex))
    for op in ops:
        if rt.is_stmt(op):
            continue
        name = P.NAMES.get(op, "")
        oid = rt.opcode_id(op)
        print(f"== {op:04x} {name} id={oid if oid is None else hex(oid)} uses={counts[op]} corpus={len(ex[op])}")
        for _, src, code in sorted(ex[op])[:per]:
            print(f"     {src[:68]:<68s} || {code[:100]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("root", type=Path)
    b.add_argument("out", type=Path)
    b.add_argument("--runtime", type=Path, required=True)
    e = sub.add_parser("examples")
    e.add_argument("corpus", type=Path)
    e.add_argument("handlers", nargs="*")
    e.add_argument("--runtime", type=Path, required=True)
    e.add_argument("--exe", type=Path)
    args = ap.parse_args()
    if args.cmd == "build":
        build(args.root, args.out, args.runtime)
    else:
        examples(args.corpus, args.runtime, args.exe, [int(h, 16) for h in args.handlers])


if __name__ == "__main__":
    main()
