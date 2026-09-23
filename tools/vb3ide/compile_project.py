#!/usr/bin/env python3
"""
Compiles existing VB3 projects (.mak) to EXE through the real IDE, via
xdotool: opens the project, File > Make EXE File..., accepts the default
output name, then renames the result to <project>.exe next to the .mak.

Prerequisites: same as compile_snippet.py (Xvfb + openbox on $DISPLAY,
working $WINEPREFIX, VB.EXE in $IDE_DIR). For the VB3 sample projects,
first rebuild the install tree with restore_install.py.

Usage:
    python3 compile_project.py path/to/a.mak [more.mak ...]
    python3 compile_project.py --all <samples-root>
"""
from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORK_DIR = Path(os.environ.get("WORK_DIR", REPO_ROOT / "work"))
DISPLAY = os.environ.get("DISPLAY", ":99")
WINEPREFIX = os.environ.get("WINEPREFIX", str(WORK_DIR / ".wineprefix"))
IDE_DIR = Path(os.environ.get("IDE_DIR", WORK_DIR / "ide"))
ENV = {**os.environ, "DISPLAY": DISPLAY, "WINEPREFIX": WINEPREFIX}


def to_winpath(path: Path) -> str:
    return "Z:" + str(path.resolve()).replace("/", "\\")


def xdo(*args: str) -> None:
    subprocess.run(["xdotool", *args], env=ENV, check=True)


def compile_mak(mak: Path, load_wait: float = 8, build_wait: float = 10) -> bool:
    exe = mak.with_suffix(".exe")
    exe.unlink(missing_ok=True)
    before = {p: p.stat().st_mtime for p in mak.parent.glob("*.[eE][xX][eE]")}
    subprocess.run(["wineserver", "-k"], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    subprocess.Popen(["wine", "VB.EXE", to_winpath(mak)], cwd=IDE_DIR, env=ENV,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(load_wait)
    xdo("mousemove", "--sync", "17", "41", "click", "1")      # File menu
    time.sleep(1.5)
    xdo("mousemove", "--sync", "60", "343", "click", "1")     # Make EXE File...
    time.sleep(2)
    xdo("mousemove", "--sync", "696", "221", "click", "1")    # OK (default name)
    for _ in range(int(build_wait)):
        time.sleep(1)
        new = [p for p in mak.parent.glob("*.[eE][xX][eE]") if before.get(p) != p.stat().st_mtime]
        if new:
            time.sleep(1)
            new[0].rename(exe)  # several projects can share a directory
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", type=Path)
    ap.add_argument("--all", type=Path, help="compile every .mak under this directory")
    args = ap.parse_args()
    maks = list(args.paths) + (sorted(args.all.rglob("*.mak")) if args.all else [])
    for mak in maks:
        ok = compile_mak(mak) or compile_mak(mak, load_wait=15, build_wait=20)
        print(f"{'OK  ' if ok else 'FAIL'} {mak}", flush=True)
    subprocess.run(["wineserver", "-k"], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
