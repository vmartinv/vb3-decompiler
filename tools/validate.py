#!/usr/bin/env python3
"""
Scores the decompiler's recovered names against projects with known
source (VB3's sample projects, generated test projects).

  procs:    event procedure names (from form blob event tables)
  refs:     CONTROL/FORM/CTLARRAY names vs identifiers on the source line
  props:    PGET/PSET Class.Prop vs `.Prop` on the source line

Usage:
    python3 tools/validate.py <projects-root> --runtime VBRUN300.DLL [-v]
"""
from __future__ import annotations

import argparse
import re
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import align_source as A  # noqa: E402
import pcode_disasm as P  # noqa: E402

VBX_DIRS: list[Path] = []


def project(mak: Path, exe: Path, rt: P.Runtime, verbose: bool) -> Counter:
    c: Counter = Counter()
    rt.vbx_dirs = [exe.parent] + VBX_DIRS
    segs = P.parse_ne(exe)
    res = P.rcdata(exe)
    names = P.proc_names(segs, rt, res)
    symbols = P.Symbols(rt, segs, res)
    syms = symbols.controls
    by_seg: dict[int, list] = {}
    for r in P.find_procs(segs):
        by_seg.setdefault(r.segment, []).append(r)
    files = [(f, A.source_procs(f)) for f in A.code_files(mak)]
    nonempty = [(f, [p for p in ps if any(A.executable(l) for _, l in p["body"][:-1])]) for f, ps in files]
    nonempty = [(f, ps) for f, ps in nonempty if ps]
    for seg, (f, sprocs) in zip(sorted(by_seg), nonempty):
        recs = sorted(by_seg[seg], key=lambda r: r.record)
        if len(recs) != len(sprocs):
            c["procs_unaligned"] += len(sprocs)
            continue
        for r, sp in zip(recs, sprocs):
            got, want = names.get(r.record), sp["name"]
            if "_" in want and not want.startswith(("Sub", "Function")):
                c["procs_total"] += 1
                if got and got.lower() == want.lower():
                    c["procs_ok"] += 1
                elif got:
                    c["procs_wrong"] += 1
                    if verbose:
                        print(f"   proc {f.name}: {want} != {got}")
            elif got:
                c["procs_false"] += 1
                if verbose:
                    print(f"   proc {f.name}: general {want} named {got}")
            # per statement: references and properties
            insns, err = P.decode(rt, segs[seg - 1].data, r)
            if err:
                continue
            groups, cur = [], None
            for i in insns:
                if rt.is_stmt(i.op):
                    cur = [i]
                    groups.append(cur)
                elif cur is not None:
                    cur.append(i)
            groups = [g for g in groups if not (len(g) == 2 and g[1].op == A.LABEL)]
            lines = [st for _, l in sp["body"] if A.executable(l) for st in A.split_statements(l)]
            if len(groups) != len(lines):
                continue
            sym = syms.get(seg, {})
            notes = dict(zip((i.pc for i in insns), symbols.annotate(seg, insns)))
            for g, src in zip(groups, lines):
                members = {t.lower() for t in re.findall(r"[.!]\s*([A-Za-z_]\w*)", A.strip_comment(src))}
                bare = {t.lower() for t in re.findall(r"(?<![.!\w])\s*([A-Za-z_]\w*)", A.strip_comment(src))}
                for i in g:
                    if P.NAMES.get(i.op) in ("PGET_ME", "PSET_ME"):  # implicit-form property
                        c["meprops_total"] += 1
                        note = notes.get(i.pc, "")
                        if note and note.split(".")[-1].lower() in bare:
                            c["meprops_ok"] += 1
                        elif verbose:
                            print(f"   meprop {f.name}: {note or '?'} in {src!r}")
                    if P.NAMES.get(i.op) in ("PGET", "PSET", "PGET_IDX", "PSET_IDX"):
                        c["props_total"] += 1
                        note = notes.get(i.pc, "")
                        if not note:
                            c["props_missing"] += 1
                            if verbose:
                                print(f"   noprop {f.name}: {src!r}")
                        elif note.split(".")[-1].lower() in members \
                                or "." not in note and note.split("!")[-1].lower() in members:  # default property
                            c["props_ok"] += 1
                        else:
                            c["props_wrong"] += 1
                            if verbose:
                                print(f"   prop {f.name}:{want}: {note} not in {src!r}")
                for i in g:  # member separator: SUBOBJ/CTLARRAY_OF 4A57/4EA9 = `!`, 4A63/4EB0 = `.`
                    ctl = notes.get(i.pc, "").partition("!")[2]
                    if i.op in (0x4A57, 0x4EA9, 0x4A63, 0x4EB0) and ctl:
                        found = re.findall(rf"([!.])\s*{re.escape(ctl)}\b", A.strip_comment(src), re.I)
                        if found:
                            c["sep_total"] += 1
                            if (found[0] == "!") == (i.op in (0x4A57, 0x4EA9)):
                                c["sep_ok"] += 1
                            elif verbose:
                                print(f"   sep {f.name}: {i.op:04x} {ctl} in {src!r}")
                idents = {t.lower() for t in re.findall(r"[A-Za-z_]\w*", A.strip_comment(src))}
                for i in g:
                    if P.NAMES.get(i.op) in ("CONTROL", "CTLARRAY", "FORM") and i.operand:
                        slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                        nm = sym.get(slot)
                        c["refs_total"] += 1
                        typed = symbols.objvar_types.get(seg, {}).get(slot)
                        if nm is None and (P.NAMES.get(i.op) == "FORM" or typed):
                            c["refs_objvar"] += 1  # object variable: named like any variable
                            continue
                        if nm is None:
                            c["refs_missing"] += 1
                            if verbose:
                                print(f"   missing {f.name}:{want}: slot {slot:#x} in {src!r}")
                        elif nm.lower() in idents or nm.lower() in ("screen", "app", "printer", "clipboard"):
                            c["refs_ok"] += 1
                        else:
                            c["refs_wrong"] += 1
                            if verbose:
                                print(f"   ref {f.name}:{want}: {nm} not in {src!r}")
    return c


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path)
    ap.add_argument("--runtime", type=Path, required=True)
    ap.add_argument("-v", action="store_true")
    ap.add_argument("--vbx-dir", type=Path, action="append", default=[])
    args = ap.parse_args()
    rt = P.Runtime(args.runtime)
    VBX_DIRS.extend(args.vbx_dir)
    total: Counter = Counter()
    for mak in sorted(args.root.rglob("*.mak")):
        exe = mak.with_suffix(".exe")
        if not exe.is_file():
            continue
        try:
            c = project(mak, exe, rt, args.v)
        except Exception as e:  # report and continue
            print(f"{mak.stem}: ERROR {e}")
            continue
        total += c
        print(f"{mak.stem:10s} procs {c['procs_ok']}/{c['procs_total']} (wrong {c['procs_wrong']}, false {c['procs_false']})"
              f"  refs {c['refs_ok']}/{c['refs_total'] - c['refs_objvar']} (missing {c['refs_missing']}, wrong {c['refs_wrong']}; +{c['refs_objvar']} object vars)")
    print(f"TOTAL      procs {total['procs_ok']}/{total['procs_total']} (wrong {total['procs_wrong']}, false {total['procs_false']})"
          f"  refs {total['refs_ok']}/{total['refs_total'] - total['refs_objvar']} (missing {total['refs_missing']}, wrong {total['refs_wrong']}; +{total['refs_objvar']} object vars)"
          f"  props {total['props_ok']}/{total['props_total']} (missing {total['props_missing']}, wrong {total['props_wrong']})"
          f"  ! vs . {total['sep_ok']}/{total['sep_total']}  Me props {total['meprops_ok']}/{total['meprops_total']}")


if __name__ == "__main__":
    main()
