#!/usr/bin/env python3
"""
Aligns a compiled VB3 project with its text source: procedure records to
source procedures, then statements (STMT markers) to source lines.
Emits a JSON corpus of (source line, instructions) used to name handlers.

Mapping rules (see ../OPCODES.md):
  - code segments: modules (.bas/.gbl) first, then forms, each group in
    .mak order; files without code get no segment (checked by counts);
  - within a segment, records in table order = the file's non-empty
    procedures in order of first mention of their name (definition or
    call); code layout order differs;
  - each statement starts with a statement marker (Runtime.is_stmt).

Usage:
    python3 tools/align_source.py <project.mak> <project.exe> --runtime VBRUN300.DLL [--json OUT]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pcode_disasm import Runtime, decode, find_procs, parse_ne  # noqa: E402

LABEL = 0x4965  # label definition, u32 operand
PROC_START = re.compile(r"^\s*(?:(?:Static|Private|Public)\s+)*(Sub|Function)\s+(\w+)", re.I)
PROC_END = re.compile(r"^\s*End\s+(Sub|Function)\b", re.I)


def read_lines(path: Path) -> list[str]:
    return path.read_bytes().decode("latin-1").replace("\r", "").split("\n")


def code_files(mak: Path) -> list[Path]:
    out = []
    for line in read_lines(mak):
        line = line.strip()
        if "=" in line or not line:
            continue
        if line.lower().endswith((".frm", ".bas", ".gbl")):
            p = mak.parent / line
            if not p.exists():  # case-insensitive lookup
                hits = [q for q in mak.parent.iterdir() if q.name.lower() == line.lower()]
                p = hits[0] if hits else p
            out.append(p)
    # Standard modules get the lowest code segments, then forms.
    return sorted(out, key=lambda p: p.suffix.lower() == ".frm")


def source_procs(path: Path) -> list[dict]:
    """Procedures with their body lines (header excluded, End line included)."""
    lines = read_lines(path)
    # Skip the form description block (Begin ... End, nested).
    i, depth = 0, 0
    if lines and lines[0].startswith("VERSION"):
        i = 1
        while i < len(lines):
            s = lines[i].strip()
            if s.startswith("Begin "):
                depth += 1
            elif s == "End":
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            i += 1
    code_start = i
    procs, cur = [], None
    for n in range(i, len(lines)):
        s = lines[n]
        if cur is None:
            m = PROC_START.match(s)
            if m:
                cur = {"name": m.group(2), "kind": m.group(1), "file": path.name, "line": n + 1, "body": []}
        else:
            cur["body"].append((n + 1, s))
            if PROC_END.match(s):
                procs.append(cur)
                cur = None
    # Records are created in order of the name's first mention (definition
    # or call), not definition order.
    text = "\n".join(strip_comment(l) for l in lines[code_start:])
    def first_mention(p):
        m = re.search(r"(?<![\w.])" + re.escape(p["name"]) + r"\b", text, re.I)
        return m.start() if m else 1 << 30
    return sorted(procs, key=first_mention)


def strip_comment(line: str) -> str:
    out, in_str = "", False
    for ch in line:
        if ch == '"':
            in_str = not in_str
        elif ch == "'" and not in_str:
            break
        out += ch if not in_str else " "
    return out


def executable(line: str) -> bool:
    s = line.strip()
    if not s or s.startswith("'") or s.lower().startswith("rem "):
        return False
    if re.match(r"^\w+:\s*('.*)?$", s):  # label
        return False
    return not re.match(r"(?i)(dim|const|static|redim\s+shared)\b", s) or s.lower().startswith("redim")


def split_statements(line: str) -> list[str]:
    """Split on ':' outside string literals and comments."""
    parts, cur, in_str = [], "", False
    for ch in line:
        if ch == '"':
            in_str = not in_str
        elif ch == "'" and not in_str:
            break
        elif ch == ":" and not in_str:
            parts.append(cur); cur = ""; continue
        cur += ch
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mak", type=Path)
    ap.add_argument("exe", type=Path)
    ap.add_argument("--runtime", type=Path, required=True)
    ap.add_argument("--json", type=Path)
    ap.add_argument("-v", action="store_true", help="report misaligned procedures")
    ap.add_argument("-vv", action="store_true", help="also dump them side by side")
    args = ap.parse_args()

    rt = Runtime(args.runtime)
    segs = parse_ne(args.exe)
    recs = find_procs(segs)
    by_seg: dict[int, list] = {}
    for r in recs:
        by_seg.setdefault(r.segment, []).append(r)

    files = [(f, source_procs(f)) for f in code_files(args.mak)]
    # Files with no non-empty procedure contribute no segment.
    nonempty = [(f, [p for p in ps if any(executable(l) for _, l in p["body"][:-1])]) for f, ps in files]
    nonempty = [(f, ps) for f, ps in nonempty if ps]

    corpus, stats = [], {"procs": 0, "procs_aligned": 0, "lines": 0}
    seg_ids = sorted(by_seg)
    if len(seg_ids) != len(nonempty):
        print(f"!! {len(seg_ids)} code segments vs {len(nonempty)} code files", file=sys.stderr)
    for seg_id, (f, sprocs) in zip(seg_ids, nonempty):
        rprocs = sorted(by_seg[seg_id], key=lambda r: r.record)  # records = source order
        if len(rprocs) != len(sprocs):
            print(f"!! seg{seg_id} {f.name}: {len(rprocs)} records vs {len(sprocs)} procedures", file=sys.stderr)
            continue
        for r, sp in zip(rprocs, sprocs):
            stats["procs"] += 1
            insns, err = decode(rt, segs[seg_id - 1].data, r)
            if err:
                print(f"!! {f.name}:{sp['name']}: {err}", file=sys.stderr)
                continue
            groups, cur = [], None
            for ins in insns:
                if rt.is_stmt(ins.op):
                    cur = [ins]
                    groups.append(cur)
                elif cur is not None:
                    cur.append(ins)
            # A label emits [marker, LABEL] (or LABEL alone, folded into the
            # previous statement); labels aren't in `lines`, so drop those.
            groups = [g for g in groups if not (len(g) == 2 and g[1].op == LABEL)]
            lines = [(n, st) for n, l in sp["body"] if executable(l) for st in split_statements(l)]
            if len(groups) != len(lines):
                if args.v:
                    print(f"?? {f.name}:{sp['name']}: {len(groups)} stmts vs {len(lines)} lines", file=sys.stderr)
                if args.vv:
                    for k in range(max(len(groups), len(lines))):
                        g = " ".join(f"{i.op:04x}" for i in groups[k]) if k < len(groups) else ""
                        print(f"     {g[:60]:<60s} | {lines[k][1] if k < len(lines) else ''}", file=sys.stderr)
                continue
            stats["procs_aligned"] += 1
            for (n, text), g in zip(lines, groups):
                stats["lines"] += 1
                corpus.append({
                    "file": f.name, "proc": sp["name"], "line": n, "src": text,
                    "code": [[i.op, i.operand.hex()] for i in g],
                })
    print(f"{args.mak.name}: {stats['procs_aligned']}/{stats['procs']} procedures aligned, {stats['lines']} lines")
    if args.json:
        args.json.write_text(json.dumps(corpus, indent=1))


if __name__ == "__main__":
    main()
