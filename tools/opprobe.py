#!/usr/bin/env python3
"""
Opcode discovery: compiles statements one per line and reports, per
statement, the opcodes the disassembler doesn't name yet.

  DISPLAY=:99 python3 tools/opprobe.py <probe> [-a]

A probe is `probes/<name>.py` defining `header` (module-level lines) and
`lines`, a list of (label, statement); a statement may span lines (a
whole construct). Each is compiled as its own Sub. Output: per unknown
opcode, the labels it appeared in (a single consistent label names it). -a prints every statement's op sequence;
--sem prints, for unknown ops seen with one builtin-call label
(`Name(a, b)`), a proposed opcodes.SEM entry.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import battery as B  # noqa: E402
import pcode_disasm as P  # noqa: E402
from roundtrip import compile_mak  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
WORK = REPO / "work" / "probe"


def op_name(rt, i) -> str:
    t = P.fmt(rt, i)[39:].split(" [")[0].strip()
    return t.split()[0] if t else f"op_{i.op:04X}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("probe")
    ap.add_argument("-a", action="store_true", help="print every statement's op sequence")
    ap.add_argument("--sem", action="store_true", help="propose SEM entries for builtin calls")
    a = ap.parse_args()
    g: dict = {}
    g["REPO"] = REPO  # probes may load battery lists
    exec(compile((REPO / "probes" / f"{a.probe}.py").read_text(), a.probe, "exec"), g)
    header, lines = g.get("header", ""), g["lines"]
    code = header + "\n" + "".join(
        f"\nSub P{j:04d} ()\n" + "".join(f"    {re.sub(r'\bL(\d)\b', rf'L\1x{j}', ln)}\n" for ln in st.split("\n"))
        + "End Sub\n"  # labels are module-wide: L1 -> L1x<j>
        for j, (_, st) in enumerate(lines)) + g.get("footer", "")
    mak = B.write_case_project(WORK / a.probe, a.probe.upper()[:8], [(0, dict(code=code, bas=g.get("bas", False)))])
    if not compile_mak(mak):
        sys.exit(f"compile failed: {mak.with_suffix('.fail.png')}")
    rt = P.Runtime(REPO / "work" / "ide" / "VBRUN300.DLL")
    segs = P.parse_ne(mak.with_suffix(".exe"))
    procs = sorted(P.find_procs(segs), key=lambda p: (p.segment, p.start))  # layout: sorted by name
    seen: dict[str, list[str]] = {}
    for (label, _), p in zip(lines, procs):
        ins, _ = P.decode(rt, segs[p.segment - 1].data, p)
        ops = [(n, i.op, i.operand) for i in ins if (n := op_name(rt, i)) not in ("STMT", "RET", "TRAP")]
        if a.a:
            print(f"{label:24s} " + " ".join(n + (f"({o.hex()})" if o and n.startswith("op_") else "") for n, _, o in ops))
        for n, op, _ in ops:
            if n.startswith("op_"):
                seen.setdefault(n, []).append(label)
    for n, labels in sorted(seen.items()):
        uniq = sorted(set(labels))
        call = re.fullmatch(r"([A-Za-z]\w*\$?)(?:\((.*)\))?", uniq[0]) if len(uniq) == 1 else None
        if a.sem and call:
            print(f"    0x{n[3:]}: (\"fn\", \"{call[1]}\", {nargs(call[2])}),")
        else:
            print(f"{n}  {len(labels):3d}x  {', '.join(uniq)[:150]}")


def nargs(args: str | None) -> int:
    """Top-level argument count of a call's argument text."""
    if not args or not args.strip():
        return 0
    depth, n, quoted = 0, 1, False
    for ch in args:
        quoted ^= ch == '"'
        if not quoted:
            depth += ch == "("
            depth -= ch == ")"
            n += ch == "," and depth == 0
    return n


if __name__ == "__main__":
    main()
