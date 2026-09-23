#!/usr/bin/env python3
"""
Rebuilds the VB3 install tree from KWAJ-expanded disk files, using the
disks' PACKING.LST (disk name -> install path). This restores the sample
projects (vb\\samples\\...) with their real names and directories. The
disks flatten and rename them (e.g. main2.fr_ = samples\\controls\\main.frm).

Usage:
    python3 restore_install.py <PACKING.LST> <expanded-dir> <out-root> [--system DIR]

<out-root> receives vb\\... and windows\\... as vb/..., windows/... .
--system additionally copies windows\\system files (VBX controls, DLLs)
there, e.g. <WINEPREFIX>/drive_c/windows/system, so the IDE finds them.
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

LINE = re.compile(r"^(\S+\.\S+)\s{2,}.*?\s{2,}(\S+)\s*$")


def expanded_name(disk_name: str) -> str:
    """kwaj_extract output name: upper-case, trailing '_' dropped."""
    name = disk_name.upper()
    return name[:-1] if name.endswith("_") else name


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("packing", type=Path)
    ap.add_argument("expanded", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--system", type=Path)
    args = ap.parse_args()

    copied = missing = 0
    for line in args.packing.read_text(errors="replace").splitlines():
        m = LINE.match(line)
        if not m or "\\" not in m.group(2):
            continue
        src = args.expanded / expanded_name(m.group(1))
        if not src.is_file():
            missing += 1
            continue
        rel = Path(*m.group(2).lower().split("\\"))
        for dest in [args.out / rel] + (
            [args.system / rel.name] if args.system and rel.parts[:2] == ("windows", "system") else []
        ):
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
        copied += 1
    print(f"restored {copied} files ({missing} listed but not expanded, e.g. .PA icon packs)")


if __name__ == "__main__":
    main()
