#!/usr/bin/env python3
"""
Systematically varies VB3 PROJECT STRUCTURE (how many .bas/.frm files exist,
how many Subs each has) to crack segment 3's Call-target-to-segment mapping
-- see ../../OPCODES.md's "Multi-segment projects" section. Unlike
compile_snippet.py (which varies code within one Form_Load body), this
varies the project shape itself, writing real multi-file VB3 projects
directly to disk (bypassing the IDE's Add File dialog, which isn't
automated here).

Each generated project has Form1 (always present) with a Form_Load that
Call()s every Sub in every other file, in file-declaration order. Extra
files are either standard modules (.bas) or extra forms (.frm), each with
N trivial one-statement Subs.

Usage:
    DISPLAY=:99 python3 sweep_project_structure.py
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORK_DIR = Path(os.environ.get("WORK_DIR", REPO_ROOT / "work"))
DISPLAY = os.environ.get("DISPLAY", ":99")
WINEPREFIX = os.environ.get("WINEPREFIX", str(WORK_DIR / ".wineprefix"))
IDE_DIR = Path(os.environ.get("IDE_DIR", WORK_DIR / "ide"))
PROJ_DIR = WORK_DIR / "proj"
OUT_DIR = WORK_DIR / "sweep_struct"
MAK = PROJ_DIR / "Project1.mak"

FORM_TEMPLATE = [
    "VERSION 2.00",
    "Begin Form {name} ",
    '   Caption         =   "{name}"',
    "   ClientHeight    =   8145",
    "   ClientLeft      =   1095",
    "   ClientTop       =   1695",
    "   ClientWidth     =   7350",
    "   Height          =   8655",
    "   Left            =   1035",
    '   LinkTopic       =   "{name}"',
    "   ScaleHeight     =   8145",
    "   ScaleWidth      =   7350",
    "   Top             =   1245",
    "   Width           =   7470",
    "End",
]


def to_winpath(path: Path) -> str:
    return "Z:" + str(path).replace("/", "\\")


def xdo(*args: str) -> None:
    subprocess.run(["xdotool", *args], env={**os.environ, "DISPLAY": DISPLAY}, check=True)


def write_crlf(path: Path, lines: list[str]) -> None:
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("ascii"))


@dataclass
class FileSpec:
    kind: str  # "module" or "form"
    name: str  # e.g. "Module1" or "Form2"
    subs: list[str]  # sub names, each gets body "x = 1"


def sub_block(name: str) -> list[str]:
    return [f"Sub {name} ()", "x = 1", "End Sub", ""]


def write_project(extra_files: list[FileSpec]) -> list[str]:
    """Writes Form1.frm (with a Call to every extra sub) + all extra files
    + Project1.mak. Returns the ordered list of .mak-relative filenames
    (Form1.frm first, then extras in the given order) for bookkeeping."""
    PROJ_DIR.mkdir(parents=True, exist_ok=True)

    call_lines = []
    for f in extra_files:
        for s in f.subs:
            call_lines.append(f"Call {s}")

    form1_lines = FORM_TEMPLATE[:1] + [FORM_TEMPLATE[1].format(name="Form1")] + \
        [l.format(name="Form1") for l in FORM_TEMPLATE[2:]] + \
        ["Sub Form_Load ()"] + call_lines + ["End Sub", ""]
    write_crlf(PROJ_DIR / "Form1.frm", form1_lines)

    mak_lines = [to_winpath(PROJ_DIR / "Form1.frm").replace("Z:\\", "Z:\\")]
    # correct: use backslash windows path as-is
    mak_lines = [to_winpath(PROJ_DIR / "Form1.frm")]

    for f in extra_files:
        if f.kind == "module":
            fname = f"{f.name}.bas"
            lines = []
            for s in f.subs:
                lines += sub_block(s)
            write_crlf(PROJ_DIR / fname, lines)
        else:
            fname = f"{f.name}.frm"
            lines = FORM_TEMPLATE[:1] + [FORM_TEMPLATE[1].format(name=f.name)] + \
                [l.format(name=f.name) for l in FORM_TEMPLATE[2:]]
            first_sub = f.subs[0] if f.subs else None
            if first_sub:
                lines += sub_block(first_sub)
                for s in f.subs[1:]:
                    lines += sub_block(s)
            else:
                lines += [""]
            write_crlf(PROJ_DIR / fname, lines)
        mak_lines.append(to_winpath(PROJ_DIR / fname))

    mak_lines += [
        "ProjWinSize=87,830,194,128",
        "ProjWinShow=2",
        'Title="Project1"',
        'ExeName="struct_test.exe"',
        f'Path="{to_winpath(IDE_DIR / "out")}"',
    ]
    write_crlf(MAK, mak_lines)

    return ["Form1.frm"] + [
        f"{f.name}.{'bas' if f.kind == 'module' else 'frm'}" for f in extra_files
    ]


def wineserver_kill() -> None:
    subprocess.run(
        ["wineserver", "-k"],
        env={**os.environ, "DISPLAY": DISPLAY, "WINEPREFIX": WINEPREFIX},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def compile_one_attempt(outname: str) -> bool:
    outpath = OUT_DIR / outname
    outpath.unlink(missing_ok=True)

    wineserver_kill()
    time.sleep(2)
    subprocess.Popen(
        ["wine", "VB.EXE", to_winpath(MAK)],
        cwd=IDE_DIR,
        env={**os.environ, "DISPLAY": DISPLAY, "WINEPREFIX": WINEPREFIX},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(6)

    xdo("key", "--clearmodifiers", "alt+f")
    time.sleep(1.5)
    xdo("mousemove", "--sync", "60", "343")
    xdo("click", "1")
    time.sleep(1.5)

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


def compile_one(outname: str) -> bool:
    if compile_one_attempt(outname):
        print(f"OK  {outname}")
        return True
    print(f"retry {outname} ...")
    if compile_one_attempt(outname):
        print(f"OK  {outname} (2nd try)")
        return True
    print(f"FAIL {outname} (no output file after retry)")
    return False


# ---- analysis: parse NE segment table + segment 3 records ----

def read_segments(data: bytes):
    ne_off = struct.unpack_from("<H", data, 0x3c)[0]
    seg_tab_off = struct.unpack_from("<H", data, ne_off + 0x22)[0]
    seg_count = struct.unpack_from("<H", data, ne_off + 0x1c)[0]
    align = struct.unpack_from("<H", data, ne_off + 0x32)[0]
    shift = align if align else 9
    segs = []
    for i in range(seg_count):
        off = ne_off + seg_tab_off + i * 8
        sec_off, sec_len, flags, min_alloc = struct.unpack_from("<HHHH", data, off)
        segs.append({"index": i + 1, "file_off": sec_off << shift, "len": sec_len, "flags": flags})
    return segs


def dump_seg3_records(seg3: bytes):
    records = []
    for marker_hex, tag in [("26000000", "SUB"), ("16000000", "CONTAINER")]:
        marker = bytes.fromhex(marker_hex)
        idx = 0
        while True:
            idx = seg3.find(marker, idx)
            if idx == -1:
                break
            rec = seg3[idx:idx + 56]
            vals = [struct.unpack_from("<H", rec, i)[0] for i in range(0, min(56, len(rec)), 2)]
            records.append({"tag": tag, "offset": idx, "vals": vals})
            idx += 1
    records.sort(key=lambda r: r["offset"])
    return records


def analyze(exe_path: Path, file_order: list[str]) -> dict:
    data = exe_path.read_bytes()
    segs = read_segments(data)
    code_segs = [s for s in segs if s["index"] not in (1, 2, 3)]
    seg3 = next(s for s in segs if s["index"] == 3)
    seg3_bytes = data[seg3["file_off"]:seg3["file_off"] + seg3["len"]]
    records = dump_seg3_records(seg3_bytes)
    code_segs_content = {
        s["index"]: data[s["file_off"]:s["file_off"] + s["len"]].hex(" ")
        for s in code_segs
    }
    return {
        "file_order": file_order,
        "code_segments": [{"index": s["index"], "len": s["len"]} for s in code_segs],
        "code_segments_content": code_segs_content,
        "seg3_records": records,
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = {}

    cases = {
        "1mod_1sub": [FileSpec("module", "Module1", ["S1"])],
        "2mod_1sub": [FileSpec("module", "Module1", ["S1"]), FileSpec("module", "Module2", ["S2"])],
        "3mod_1sub": [
            FileSpec("module", "Module1", ["S1"]),
            FileSpec("module", "Module2", ["S2"]),
            FileSpec("module", "Module3", ["S3"]),
        ],
        "1mod_2form": [
            FileSpec("module", "Module1", ["S1"]),
            FileSpec("form", "Form2", ["S2"]),
            FileSpec("form", "Form3", ["S3"]),
        ],
    }

    for case_name, files in cases.items():
        print(f"=== {case_name} ===")
        file_order = write_project(files)
        outname = f"{case_name}.exe"
        if compile_one(outname):
            results[case_name] = analyze(OUT_DIR / outname, file_order)

    wineserver_kill()

    out_json = OUT_DIR / "results.json"
    out_json.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_json}")


if __name__ == "__main__":
    main()
