"""
Reconstructs a VB3 project from a compiled executable.

  vb3decompile some.exe outdir/ [--runtime VBRUN300.DLL] [--vbx-dir DIR] [--layout-from SRC_DIR]

Writes the .mak, .frm/.bas source and .frx resources (icons and pictures
come from each control's binary properties) to outdir/. The source
compiles back to the same p-code and form resources.

--runtime: your VBRUN300.DLL (the p-code interpreter the decoding reads
its opcode table from); default $VB3_RUNTIME, else work/ide/VBRUN300.DLL.
--vbx-dir: where the VBX custom controls the forms use are (default
$VB3_VBX_DIR, else the runtime's directory). --layout-from copies each
form's description block (Begin Form ... End) from the original .frm
files instead of decoding it.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .decompiler import Decompiler, write_project


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="vb3decompile", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("exe", type=Path)
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--runtime", type=Path,
                    default=Path(os.environ.get("VB3_RUNTIME", "work/ide/VBRUN300.DLL")))
    ap.add_argument("--vbx-dir", type=Path, action="append", default=[])
    ap.add_argument("--layout-from", type=Path, help="copy form description blocks from these .frm files")
    args = ap.parse_args(argv)
    if not args.runtime.exists():
        sys.exit(f"error: {args.runtime}: VBRUN300.DLL not found (--runtime or $VB3_RUNTIME)")
    vbx = args.vbx_dir or [Path(os.environ.get("VB3_VBX_DIR", args.runtime.parent))]
    try:
        d = Decompiler(args.exe, args.runtime, vbx)
        print(f"wrote {write_project(d, args.outdir, args.layout_from, args.exe.stem)}")
    except ValueError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
