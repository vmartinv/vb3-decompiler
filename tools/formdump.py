#!/usr/bin/env python3
"""
Prints each form's decoded description block (`Begin Form ... End`) from a
compiled executable (src/forms.py).

  python3 tools/formdump.py <exe> [--runtime VBRUN300.DLL] [--vbx-dir DIR]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from forms import dump, forms  # noqa: E402
from runtime import Runtime  # noqa: E402

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("exe", type=Path)
    ap.add_argument("--runtime", type=Path, default=Path(__file__).resolve().parent.parent / "work/ide/VBRUN300.DLL")
    ap.add_argument("--vbx-dir", type=Path, action="append", default=[])
    a = ap.parse_args()
    rt = Runtime(a.runtime)
    rt.vbx_dirs = a.vbx_dir or [a.runtime.parent]
    for f in forms(a.exe, rt):
        print("\n".join(dump(f)))


if __name__ == "__main__":
    main()
