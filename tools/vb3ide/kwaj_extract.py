#!/usr/bin/env python3
"""
Batch-decompresses the KWAJ-compressed installer files (*.EX_, *.DL_,
etc.) found on old Microsoft setup disks -- this is how VB3's IDE
(VB.EXE) itself was obtained for the empirical p-code opcode research in
OPCODES.md. No installer run is needed at all: VB.EXE only needs
VBRUN300.DLL alongside it to run directly under Wine.

Calls libmspack (the same library `cabextract` is built on) directly via
ctypes -- no C compiler needed, just the shared library, which is packaged
on most distros (Arch: `extra/libmspack`; Debian/Ubuntu: `libmspack0`).

These KWAJ files don't carry an embedded real filename (no HASFILENAME
header), so output names are just the input name with the trailing '_'
dropped (e.g. VB.EX_ -> VB.EX) -- rename manually as needed (VB.EX ->
VB.EXE, VBRUN300.DL -> VBRUN300.DLL, etc).

Usage: python3 kwaj_extract.py <dir-of-compressed-disk-files> <output-dir>
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
from pathlib import Path

MSPACK_ERR_OK = 0


class MSKwajDecompressor(ctypes.Structure):
    pass


MSKwajDecompressor._fields_ = [
    ("open", ctypes.c_void_p),
    ("close", ctypes.c_void_p),
    ("extract", ctypes.c_void_p),
    (
        "decompress",
        ctypes.CFUNCTYPE(
            ctypes.c_int,
            ctypes.POINTER(MSKwajDecompressor),
            ctypes.c_char_p,
            ctypes.c_char_p,
        ),
    ),
    ("last_error", ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(MSKwajDecompressor))),
]


def load_libmspack() -> ctypes.CDLL:
    name = ctypes.util.find_library("mspack") or "libmspack.so.0"
    lib = ctypes.CDLL(name)
    lib.mspack_create_kwaj_decompressor.restype = ctypes.POINTER(MSKwajDecompressor)
    lib.mspack_create_kwaj_decompressor.argtypes = [ctypes.c_void_p]
    lib.mspack_destroy_kwaj_decompressor.argtypes = [ctypes.POINTER(MSKwajDecompressor)]
    return lib


def decompress_dir(indir: Path, outdir: Path) -> tuple[int, int, int]:
    outdir.mkdir(parents=True, exist_ok=True)
    lib = load_libmspack()
    kwajd = lib.mspack_create_kwaj_decompressor(None)
    if not kwajd:
        raise RuntimeError("could not create kwaj decompressor")

    ok = fail = skip = 0
    try:
        for entry in sorted(indir.iterdir()):
            if not entry.is_file() or not entry.name.endswith("_"):
                skip += 1
                continue
            outname = entry.name[:-1]  # best-effort: drop trailing '_'
            outpath = outdir / outname
            rc = kwajd.contents.decompress(
                kwajd, str(entry).encode(), str(outpath).encode()
            )
            if rc != MSPACK_ERR_OK:
                print(f"extract failed: {entry.name} -> {outname} (err {rc})")
                fail += 1
            else:
                print(f"{entry.name} -> {outname}")
                ok += 1
    finally:
        lib.mspack_destroy_kwaj_decompressor(kwajd)
    return ok, fail, skip


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("indir", type=Path)
    ap.add_argument("outdir", type=Path)
    args = ap.parse_args()

    ok, fail, skip = decompress_dir(args.indir, args.outdir)
    print(f"\nok={ok} fail={fail} skip={skip}")


if __name__ == "__main__":
    main()
