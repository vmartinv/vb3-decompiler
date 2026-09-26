#!/usr/bin/env python3
"""
Round-trip check: decompile a compiled sample, recompile the result in the
VB3 IDE (under Wine) and compare it with the original source recompiled
the same way.

  python3 tests/roundtrip.py [project.mak ...] [--runtime VBRUN300.DLL] [--vbx-dir DIR]
          [--no-compile] [-v]

Defaults: every sample under work/root/vb/samples that has a compiled exe,
work/ide/VBRUN300.DLL and work/ide for VBX files.

Both builds go to work/rt/<project>/{orig,deco} (equal-length paths: the
source path is embedded in the executable). Reported per project:
procedures whose p-code is byte-identical, data images (RT_RCDATA 2) and
form blobs, procedure table (segment 3) equality. Form layouts are decoded
from the executable (tools/formblob.py); --source-layout copies them from the
original .frm files instead.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import exediff  # noqa: E402
import pcode_disasm as P  # noqa: E402

from vb3decompiler.decompiler import Decompiler, write_project  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
WORK = REPO / "work" / "rt"


def find_exe(mak: Path) -> Path | None:
    hits = [f for f in mak.parent.iterdir() if f.suffix.lower() == ".exe" and f.stem.lower() == mak.stem.lower()]
    return hits[0] if hits else None


def compile_mak(mak: Path) -> bool:
    env = {**os.environ, "DISPLAY": os.environ.get("RT_DISPLAY", ":99")}
    r = subprocess.run([sys.executable, str(REPO / "tools/vb3ide/compile_project.py"), str(mak)],
                       env=env, capture_output=True, text=True, timeout=300)
    return r.returncode == 0 and mak.with_suffix(".exe").exists()


def procs_code(exe: Path) -> list[tuple[int, int, bytes]]:
    segs = P.parse_ne(exe)
    return [(p.segment, p.start, segs[p.segment - 1].data[p.start:p.end]) for p in P.find_procs(segs)]


def compare(a: Path, b: Path, rt: P.Runtime, verbose: bool) -> dict:
    pa, pb = procs_code(a), procs_code(b)
    same = sum(x == y for x, y in zip(pa, pb))
    ra, rb = P.rcdata(a), P.rcdata(b)
    fa = [ra[k] for k in sorted(ra) if ra[k][:2] == b"\xff\xcc"]
    fb = [rb[k] for k in sorted(rb) if rb[k][:2] == b"\xff\xcc"]
    sa, sb = P.parse_ne(a), P.parse_ne(b)
    out = dict(procs=len(pa), same=same, count_ok=len(pa) == len(pb),
               image=ra.get(2) == rb.get(2), table=sa[2].data == sb[2].data,
               identical=a.read_bytes() == b.read_bytes(),
               forms=len(fa), forms_same=sum(x == y for x, y in zip(fa, fb)))
    if verbose:
        for x, y in zip(pa, pb):
            if x != y:
                print(f"    seg{x[0]}@{x[1]}: {len(x[2])} vs {len(y[2])} bytes")
    return out


def run(mak: Path, runtime: Path, vbx_dirs: list[Path], do_compile: bool, verbose: bool,
        source_layout: bool = False) -> dict | None:
    exe = find_exe(mak)
    if exe is None:
        print(f"{mak}: no compiled exe")
        return None
    root = WORK / mak.stem.lower()
    orig, deco = root / "orig", root / "deco"
    a = orig / (mak.stem + ".exe")
    build_orig = do_compile and not a.exists()  # the original build is kept between runs
    if build_orig:
        shutil.rmtree(orig, ignore_errors=True)
        shutil.copytree(mak.parent, orig)
        for f in orig.iterdir():
            if f.suffix.lower() == ".exe":
                f.unlink()
    if build_orig and not compile_mak(orig / mak.name):
        print(f"{mak.stem}: compile failed: {mak.name}", flush=True)
        return None
    # decompile our own /MAKE build: the shipped exe differs in build-machine paths and project name
    d = Decompiler(a if a.exists() else exe, runtime, vbx_dirs)
    shutil.rmtree(deco, ignore_errors=True)
    dmak = write_project(d, deco, mak.parent if source_layout else None, mak.stem.lower())
    if do_compile:
        for m in [dmak]:
            ok = compile_mak(m) or compile_mak(m)  # a second try: the first build after another can fail
            logs = [f for f in m.parent.iterdir() if f.suffix.lower() == ".log"]  # load errors
            if not ok or logs:
                why = "; ".join(line for f in logs for line in f.read_text("latin-1").splitlines()[:3])
                print(f"{mak.stem}: compile failed: {m.name}" + (f" ({why})" if why else
                      f" (see {m.with_suffix('.fail.png')})"), flush=True)
                return None
    b = dmak.with_suffix(".exe")
    if not a.exists() or not b.exists():
        print(f"{mak.stem}: missing build")
        return None
    rt = P.Runtime(runtime)
    res = compare(a, b, rt, verbose)
    dd = {k: v for k, v in exediff.diff(a, b).items() if k != "rc1.volatile"}  # (heap addresses: not source)
    res["exe"] = sum(len(v) for v in dd.values())
    res["identical"] = res["identical"] or not dd
    print(f"{mak.stem:10s} procs {res['same']}/{res['procs']}" + ("" if res["count_ok"] else " (count differs)")
          + f"  forms {res['forms_same']}/{res['forms']}"
          + f"  image {'=' if res['image'] else '≠'}  table {'=' if res['table'] else '≠'}"
          + ("  EXE IDENTICAL" if res["identical"] else f"  exe {res['exe']} bytes"), flush=True)
    return res


def sample_maks() -> list[Path]:
    """The VB3 sample projects that come with a compiled exe."""
    return sorted((m for m in (REPO / "work/root/vb/samples").rglob("*")
                   if m.suffix.lower() == ".mak" and find_exe(m)), key=lambda m: m.stem.lower())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mak", type=Path, nargs="*")
    ap.add_argument("--runtime", type=Path, default=REPO / "work/ide/VBRUN300.DLL")
    ap.add_argument("--vbx-dir", type=Path, action="append", default=[])
    ap.add_argument("--no-compile", action="store_true", help="compare existing builds only")
    ap.add_argument("-v", action="store_true")
    ap.add_argument("--source-layout", action="store_true",
                    help="copy form descriptions from the original .frm files instead of decoding them")
    args = ap.parse_args()
    args.vbx_dir = args.vbx_dir or [REPO / "work/ide"]
    args.mak = args.mak or sample_maks()
    tot = [0, 0, 0, 0]
    for mak in args.mak:
        r = run(mak, args.runtime, args.vbx_dir, not args.no_compile, args.v, args.source_layout)
        if r:
            tot[0] += r["same"]
            tot[1] += r["procs"]
            tot[2] += r["identical"]
            tot[3] += 1
    print(f"TOTAL procs {tot[0]}/{tot[1]}  exe identical {tot[2]}/{tot[3]}")


if __name__ == "__main__":
    main()
