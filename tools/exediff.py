#!/usr/bin/env python3
"""
Whole-exe comparison of two VB3 builds, by structure.

  python3 tools/exediff.py orig.exe deco.exe [--max N]

Every differing byte is attributed to a place: a module's declarations
record or a procedure record in the table segment (`decl+30`,
`Form_Load+50`), a code segment, an RCDATA resource (resource 2 split into
the global image, name pool and module images), or the NE header. Prints
one line per place: the differing offsets with both values. `rc1.volatile`:
words that differ between two builds of the same source (heap addresses).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pcode_disasm as P  # noqa: E402
from decompile import image_layout, word  # noqa: E402

RUNTIME = Path(__file__).resolve().parent.parent / "work" / "ide" / "VBRUN300.DLL"


def places(exe: Path) -> dict:
    """Structure of a build: table records, module images, code segments."""
    segs = P.parse_ne(exe)
    res = P.rcdata(exe)
    img, tab = res.get(2, b""), segs[P.PROC_TABLE_SEGMENT - 1].data
    forms = P.form_names(res)
    lay = image_layout(img, len(forms))
    mods = [(f"bas{k}", c) for k, c in enumerate(lay["modules"])] + \
        [(forms[k][0], c) for k, (c, _) in enumerate(lay["forms"])]
    recs = {}  # table offset -> name
    for name, c in mods:
        recs[word(img, c - 2) + 4] = f"{name}.decl"
    procs = P.find_procs(segs)
    owner = sorted(recs)
    for p in procs:
        m = recs[max((r for r in owner if r < p.record), default=owner[0])].split(".")[0]
        recs.setdefault(p.record, f"{m}.rec{p.record:x}")
    seg_mod = {}
    for p in procs:
        seg_mod.setdefault(p.segment, recs[max((r for r in owner if r < p.record), default=owner[0])].split(".")[0])
    regions = sorted([(0, "rc2.header"), (lay["global_"], "rc2.global"), (lay["pool"], "rc2.pool")] +
                     [(c, f"rc2.{n}") for n, c in mods])
    return dict(segs=segs, res=res, recs=recs, seg_mod=seg_mod, regions=regions, exe=exe.read_bytes())


def volatile_rc1(r1: bytes) -> set[int]:
    """RT_RCDATA 1 bytes that differ between builds of the same source: in
    each VBX/form entry, the word after `FF 01` before the file name (a
    heap address in the IDE)."""
    import re
    out = set()
    for m in re.finditer(rb"\xff\x01(..).[\x21-\x7e]+?\.(?:VBX|FRM)\x00", r1, re.S | re.I):
        out |= {m.start(1), m.start(1) + 1}
    return out


def where_table(pl: dict, o: int) -> str:
    r = max((r for r in pl["recs"] if r <= o), default=None)
    return f"table+{o}" if r is None else f"{pl['recs'][r]}+{o - r}"


def diff(a: Path, b: Path) -> dict[str, list[tuple[int, int, int]]]:
    """place -> [(offset, orig byte, deco byte)]"""
    pa, pb = places(a), places(b)
    out: dict[str, list] = {}

    def add(place: str, x: bytes, y: bytes, name=lambda o: None):
        for o in range(max(len(x), len(y))):
            u, v = (x[o] if o < len(x) else -1), (y[o] if o < len(y) else -1)
            if u != v:
                out.setdefault(name(o) or place, []).append((o, u, v))

    for k, (x, y) in enumerate(zip(pa["segs"], pb["segs"]), 1):
        if k == P.PROC_TABLE_SEGMENT:
            add("table", x.data, y.data, lambda o: where_table(pa, o).split("+")[0])
        elif x.data != y.data:
            add(f"seg{k}" + (f"({pa['seg_mod'][k]})" if k in pa["seg_mod"] else ""), x.data, y.data)
    if len(pa["segs"]) != len(pb["segs"]):
        out["segcount"] = [(0, len(pa["segs"]), len(pb["segs"]))]
    for k in sorted(set(pa["res"]) | set(pb["res"])):
        x, y = pa["res"].get(k, b""), pb["res"].get(k, b"")
        if x == y:
            continue
        if k == 2:
            add("rc2", x, y, lambda o: max(r for r in pa["regions"] if r[0] <= o)[1])
        elif k == 1:
            vol = volatile_rc1(x)
            add("rc1", x, y, lambda o: "rc1.volatile" if o in vol else None)
        else:
            add(f"rc{k}", x, y)
    if not out and pa["exe"] != pb["exe"]:
        add("ne", pa["exe"], pb["exe"])
    # table offsets relative to their record
    for place in list(out):
        if place.endswith((".decl",)) or ".rec" in place:
            base = next(r for r, n in pa["recs"].items() if n == place)
            out[place] = [(o - base, u, v) for o, u, v in out[place]]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("orig")
    ap.add_argument("deco")
    ap.add_argument("--max", type=int, default=12)
    a = ap.parse_args()
    d = diff(Path(a.orig), Path(a.deco))
    for place, bs in d.items():
        print(f"{place:24s} {len(bs):5d}  " + " ".join(f"{o}:{u:02x}/{v:02x}" for o, u, v in bs[:a.max]))
    real = [p for p in d if p != "rc1.volatile"]
    print("identical" if not d else "identical but volatile rc1 words" if not real else f"{len(real)} places differ")


if __name__ == "__main__":
    main()
