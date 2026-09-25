#!/usr/bin/env python3
"""
Single entry point: reconstruct a VB3 project from a compiled exe.

  python3 tools/vb3decompile.py some.exe outdir/ [--verify]

Writes the .mak, .frm/.bas source and .frx resources (icons and pictures
are embedded through each control's binary properties) to outdir/.
--verify then rebuilds outdir/'s .mak with `VB.EXE /MAKE` (under Wine) and
compares the result with the input exe (tools/exediff.py), reporting
whether they're identical and, if not, where they differ.

Defaults: work/ide/VBRUN300.DLL for the runtime, work/ide for VBX/OCX
files (override with --runtime/--vbx-dir for a different IDE install).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exediff  # noqa: E402
import pcode_disasm as P  # noqa: E402
from decompile import Decompiler, write_project  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def verify(mak: Path, exe: Path) -> bool:
    """Rebuilds `mak` with VB.EXE /MAKE and compares it with `exe`. Returns
    whether the rebuild succeeded (not whether it's identical: exact
    identity needs unrecoverable details like the original identifier
    spellings, so a close-but-not-identical rebuild is still a success)."""
    from vb3ide.compile_project import compile_mak
    if not compile_mak(mak):
        print(f"verify: rebuild failed (see {mak.with_suffix('.fail.png')})")
        return False
    built = mak.with_suffix(".exe")
    sa, sb = P.parse_ne(exe), P.parse_ne(built)
    pa, pb = P.find_procs(sa), P.find_procs(sb)
    same = sum(x.segment == y.segment and sa[x.segment - 1].data[x.start:x.end] ==
               sb[y.segment - 1].data[y.start:y.end] for x, y in zip(pa, pb))
    d = {k: v for k, v in exediff.diff(exe, built).items() if k != "rc1.volatile"}
    print(f"verify: procedures {same}/{len(pa)} p-code identical"
          + ("" if len(pa) == len(pb) else f" (count {len(pa)} vs {len(pb)})"))
    if not d:
        print("verify: EXE IDENTICAL")
        return True
    n = sum(len(v) for v in d.values())
    print(f"verify: exe differs, {n} bytes in {len(d)} place(s):")
    for place, bs in d.items():
        print(f"  {place:24s} {len(bs):5d}  " + " ".join(f"{o}:{u:02x}/{v:02x}" for o, u, v in bs[:8]))
    if same == len(pa) == len(pb) and n < 20:
        print("verify: p-code is identical; small differences like this are usually explained by\n"
              "        the original build directory's path length (not recoverable from the exe,\n"
              "        and generally different from outdir's) shifting compile-time name-pool offsets")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("exe", type=Path)
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--runtime", type=Path, default=REPO / "work/ide/VBRUN300.DLL")
    ap.add_argument("--vbx-dir", type=Path, action="append", default=[])
    ap.add_argument("--verify", action="store_true", help="rebuild with VB.EXE /MAKE and compare")
    args = ap.parse_args()
    vbx_dirs = args.vbx_dir or [REPO / "work/ide"]
    d = Decompiler(args.exe, args.runtime, vbx_dirs)
    mak = write_project(d, args.outdir, None, args.exe.stem)
    print(f"wrote {mak}")
    if args.verify:
        if not verify(mak, args.exe):
            sys.exit(1)


if __name__ == "__main__":
    main()
