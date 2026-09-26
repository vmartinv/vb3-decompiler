#!/usr/bin/env python3
"""
Decompiles one executable, rebuilds the result with the real IDE
(`VB.EXE /MAKE` under Wine) and checks that the rebuild has the same p-code
and form resources.

  python3 tests/verify.py some.exe outdir/ [--runtime VBRUN300.DLL] [--vbx-dir DIR]

Keep outdir's path short (e.g. under work/): built from a long directory
path, the IDE compiles some form properties differently (seen with a
~90-character path), so the forms would compare different.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from roundtrip import compare, compile_mak  # noqa: E402

from vb3decompiler.decompiler import Decompiler, write_project  # noqa: E402
from vb3decompiler.runtime import Runtime  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("exe", type=Path)
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--runtime", type=Path, default=REPO / "work/ide/VBRUN300.DLL")
    ap.add_argument("--vbx-dir", type=Path, action="append", default=[])
    a = ap.parse_args()
    d = Decompiler(a.exe, a.runtime, a.vbx_dir or [REPO / "work/ide"])
    mak = write_project(d, a.outdir, None, a.exe.stem)
    print(f"wrote {mak}")
    if not compile_mak(mak):
        print(f"rebuild failed (see {mak.with_suffix('.fail.png')})")
        sys.exit(1)
    r = compare(a.exe, mak.with_suffix(".exe"), Runtime(a.runtime), False)
    print(f"procedures {r['same']}/{r['procs']} p-code identical"
          + ("" if r["count_ok"] else " (procedure count differs)")
          + f", forms {r['forms_same']}/{r['forms']} identical")
    sys.exit(0 if r["same"] == r["procs"] and r["count_ok"] and r["forms_same"] == r["forms"] else 1)


if __name__ == "__main__":
    main()
