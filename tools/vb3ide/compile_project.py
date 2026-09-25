#!/usr/bin/env python3
"""
Compiles existing VB3 projects (.mak) to EXE through the real IDE's
command line, `VB.EXE /MAKE <project>`, then renames the result to
<project>.exe next to the .mak.

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


def crashed() -> bool:
    """Wine's crash dialog: the IDE died (e.g. while building a large project)."""
    r = subprocess.run(["xdotool", "search", "--name", "Wine Debugger|Program Error"],
                       env=ENV, capture_output=True, text=True)
    return bool(r.stdout.strip())


def dialog_open() -> bool:
    """A compile error: a message box titled plainly `Microsoft Visual Basic`."""
    r = subprocess.run(["xdotool", "search", "--name", "^Microsoft Visual Basic$"],
                       env=ENV, capture_output=True, text=True)
    return bool(r.stdout.strip())


def compile_mak(mak: Path, timeout: float = 120) -> bool:
    """`VB.EXE /MAKE`: builds and exits (about 2 s); on a compile error it
    stays open on the error dialog, which is screenshotted to <mak>.fail.png.
    (Builds via the Make EXE dialog differ in one word of resource 1, so
    compare /MAKE builds with /MAKE builds.)"""
    exe = mak.with_suffix(".exe")
    exe.unlink(missing_ok=True)
    mak.with_suffix(".fail.png").unlink(missing_ok=True)
    before = {p: p.stat().st_mtime for p in mak.parent.glob("*.[eE][xX][eE]")}
    subprocess.run(["wineserver", "-k"], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    proc = subprocess.Popen(["wine", "VB.EXE", "/MAKE", to_winpath(mak)], cwd=IDE_DIR, env=ENV,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0, seen = time.time(), 0
    while proc.poll() is None and time.time() - t0 < timeout:
        time.sleep(0.5)
        seen = seen + 1 if time.time() - t0 > 3 and (dialog_open() or crashed()) else 0
        if seen >= 4:
            break
    ok = proc.poll() is not None
    if not ok:
        subprocess.run(["import", "-window", "root", str(mak.with_suffix(".fail.png"))], env=ENV,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["wineserver", "-k"], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.wait()
        return False
    new = [p for p in mak.parent.glob("*.[eE][xX][eE]") if before.get(p) != p.stat().st_mtime]
    if not new:
        return False
    new[0].rename(exe)  # several projects can share a directory
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", type=Path)
    ap.add_argument("--all", type=Path, help="compile every .mak under this directory")
    args = ap.parse_args()
    maks = list(args.paths) + (sorted(args.all.rglob("*.mak")) if args.all else [])
    for mak in maks:
        ok = compile_mak(mak)
        print(f"{'OK  ' if ok else 'FAIL'} {mak}", flush=True)
    subprocess.run(["wineserver", "-k"], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
