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
import fcntl
import os
import struct
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


def complete(exe: Path) -> bool:
    """The NE header lists resources (VB fills its resource table last) and
    every segment (with its relocations) and resource lies within the file
    (the last resource's length is rounded up to its alignment unit)."""
    b = exe.read_bytes()
    try:
        ne = struct.unpack_from("<H", b, 0x3C)[0]
        nseg, seg_tab, res_tab, shift = struct.unpack_from("<H", b, ne + 0x1C)[0], *struct.unpack_from(
            "<HH", b, ne + 0x22), struct.unpack_from("<H", b, ne + 0x32)[0]
        end = 0
        for k in range(nseg):
            sector, length, flags, _ = struct.unpack_from("<4H", b, ne + seg_tab + 8 * k)
            if sector:
                e = (sector << shift) + (length or 0x10000)
                if flags & 0x100:  # relocations follow
                    e += 2 + 8 * struct.unpack_from("<H", b, e)[0]
                end = max(end, e)
        p = ne + res_tab
        rshift, res_end = struct.unpack_from("<H", b, p)[0], 0
        p += 2
        while struct.unpack_from("<H", b, p)[0] != 0:
            count = struct.unpack_from("<H", b, p + 2)[0]
            for k in range(count):
                off, length = struct.unpack_from("<HH", b, p + 8 + 12 * k)
                res_end = max(res_end, (off + length) << rshift)
            p += 8 + 12 * count
        return res_end > 0 and end <= len(b) and res_end - (1 << rshift) < len(b)
    except struct.error:
        return False


def compile_mak(mak: Path, timeout: float = 120, tries: int = 4) -> bool:
    """`VB.EXE /MAKE`: builds and exits (about 2 s); on a compile error it
    stays open on the error dialog, which is screenshotted to <mak>.fail.png.
    (Builds via the Make EXE dialog differ in one word of resource 1, so
    compare /MAKE builds with /MAKE builds.)
    Each build starts with `wineserver -k`, which kills any other build in
    the prefix (leaving its exe cut short), so builds from separate
    processes take turns on a lock. A build that still exits early (no
    exe, or one without its resources) is retried; a compile error is not."""
    with open(Path(WINEPREFIX) / "build.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for _ in range(tries):
            ok = _make(mak, timeout)
            if ok is not None:
                return ok
    return False


def _make(mak: Path, timeout: float) -> bool | None:
    """One /MAKE run: True built, False compile error, None flaky exit."""
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
    if proc.returncode != 0 or not new or not complete(new[0]):
        for p in new:
            p.unlink()
        return None
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
