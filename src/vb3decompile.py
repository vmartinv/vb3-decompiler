#!/usr/bin/env python3
"""
Reconstructs a VB3 project from a compiled executable.

  python3 src/vb3decompile.py some.exe outdir/ [--runtime VBRUN300.DLL] [--vbx-dir DIR]
                              [--layout-from SRC_DIR]

Writes the .mak, .frm/.bas source and .frx resources (icons and pictures
come from each control's binary properties) to outdir/. The source
compiles back to the same p-code and form resources (tools/verify.py
checks that with the real IDE).

--runtime defaults to work/ide/VBRUN300.DLL, --vbx-dir to work/ide (VBX
files the forms use). --layout-from copies each form's description block
(Begin Form ... End) from the original .frm files instead of decoding it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from decompiler import Decompiler, write_project  # noqa: E402


REPO = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("exe", type=Path)
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--runtime", type=Path, default=REPO / "work/ide/VBRUN300.DLL")
    ap.add_argument("--vbx-dir", type=Path, action="append", default=[])
    ap.add_argument("--layout-from", type=Path, help="copy form description blocks from these .frm files")
    args = ap.parse_args()
    d = Decompiler(args.exe, args.runtime, args.vbx_dir or [REPO / "work/ide"])
    try:
        print(f"wrote {write_project(d, args.outdir, args.layout_from, args.exe.stem)}")
    except ValueError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
