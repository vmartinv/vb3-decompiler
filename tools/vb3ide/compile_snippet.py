#!/usr/bin/env python3
"""
Compiles a minimal VB3 project through the real VB3 IDE via GUI automation
and saves the resulting EXE. Empirical p-code opcode research tool -- see
../../OPCODES.md for the methodology and findings this has produced so
far.

Two modes:
  - A literal integer sweep: `x = <value>` for each value given.
  - Arbitrary code snippets: any Form_Load body, read from a text file.

Prerequisites (see ../../README.md "Setup" section):
  - Xvfb running, with openbox (or any window manager) providing window
    focus -- Wine windows never receive input without one.
  - A WINEPREFIX with VB.EXE + VBRUN300.DLL already working (confirmed by
    running the IDE manually once). Defaults to work/.wineprefix and
    work/ide/ under this repo; override with $WINEPREFIX / $IDE_DIR.
  - work/proj/{Project1.mak,Form1.frm} already saved as TEXT (File > Save
    Project As, "Save as Text" checked) with a `Sub Form_Load ()` /
    `End Sub` block ready to be rewritten.

Usage:
    DISPLAY=:99 python3 compile_snippet.py --sweep 0 1 2 3 10 100 32767
    DISPLAY=:99 python3 compile_snippet.py --name add_yz --code-file snippets/add_yz.bas

Output: work/sweep/x_eq_<value>.exe (sweep mode) or work/sweep/<name>.exe
(snippet mode).
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
PROJ_DIR = WORK_DIR / "proj"
OUT_DIR = WORK_DIR / "sweep"
FRM = PROJ_DIR / "Form1.frm"
MAK = PROJ_DIR / "Project1.mak"

FORM_HEADER = [
    "VERSION 2.00",
    "Begin Form Form1 ",
    '   Caption         =   "Form1"',
    "   ClientHeight    =   8145",
    "   ClientLeft      =   1095",
    "   ClientTop       =   1695",
    "   ClientWidth     =   7350",
    "   Height          =   8655",
    "   Left            =   1035",
    '   LinkTopic       =   "Form1"',
    "   ScaleHeight     =   8145",
    "   ScaleWidth      =   7350",
    "   Top             =   1245",
    "   Width           =   7470",
    "End",
    "Sub Form_Load ()",
]


def to_winpath(path: Path) -> str:
    """Absolute Unix path -> Windows path via Wine's default Z: -> / drive."""
    return "Z:" + str(path).replace("/", "\\")


def xdo(*args: str) -> None:
    subprocess.run(["xdotool", *args], env={**os.environ, "DISPLAY": DISPLAY}, check=True)


def write_frm(body_lines: list[str]) -> None:
    lines = FORM_HEADER + body_lines + ["End Sub", ""]
    FRM.write_bytes(("\r\n".join(lines) + "\r\n").encode("ascii"))


def wineserver_kill() -> None:
    subprocess.run(
        ["wineserver", "-k"],
        env={**os.environ, "DISPLAY": DISPLAY, "WINEPREFIX": WINEPREFIX},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def compile_one_attempt(body_lines: list[str], outname: str) -> bool:
    outpath = OUT_DIR / outname
    outpath.unlink(missing_ok=True)

    write_frm(body_lines)

    wineserver_kill()
    time.sleep(2)
    subprocess.Popen(
        ["wine", "VB.EXE", to_winpath(MAK)],
        cwd=IDE_DIR,
        env={**os.environ, "DISPLAY": DISPLAY, "WINEPREFIX": WINEPREFIX},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(6)

    # File > Make EXE File...
    xdo("key", "--clearmodifiers", "alt+f")
    time.sleep(1.5)
    xdo("mousemove", "--sync", "60", "343")
    xdo("click", "1")
    time.sleep(1.5)

    # Clear filename field, type absolute output path
    xdo("mousemove", "--sync", "355", "236")
    xdo("click", "1")
    xdo("key", "Home")
    xdo("key", "shift+End")
    xdo("key", "Delete")
    xdo("type", to_winpath(outpath))
    time.sleep(1.5)
    xdo("mousemove", "--sync", "696", "221")
    xdo("click", "1")
    time.sleep(3)

    return outpath.is_file()


def compile_one(body_lines: list[str], outname: str) -> None:
    if compile_one_attempt(body_lines, outname):
        print(f"OK  {outname}")
        return
    print(f"retry {outname} ...")
    if compile_one_attempt(body_lines, outname):
        print(f"OK  {outname} (2nd try)")
        return
    print(f"FAIL {outname} (no output file after retry)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sweep", nargs="+", type=int, help="Integer literals to sweep: compiles 'x = N' per value")
    ap.add_argument("--name", help="Output name (without .exe) for --code-file mode")
    ap.add_argument("--code-file", type=Path, help="File with the Form_Load body to compile")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.sweep:
        for value in args.sweep:
            compile_one([f"x = {value}"], f"x_eq_{value}.exe")
    elif args.name and args.code_file:
        body_lines = args.code_file.read_text().splitlines()
        compile_one(body_lines, f"{args.name}.exe")
    else:
        ap.error("either --sweep <values...> or --name <n> --code-file <path> is required")

    wineserver_kill()


if __name__ == "__main__":
    main()
