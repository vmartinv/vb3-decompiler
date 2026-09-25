#!/usr/bin/env python3
"""
Feature test batteries: generated projects round-tripped case by case.

  DISPLAY=:99 python3 tools/battery.py [battery ...] [--chunk N] [-k CASE]

A battery is `batteries/<name>.py` defining `cases`, a list of dicts:

  name      short label (reported)
  code      module code (a form's code by default)
  bas       True: a .bas module instead of a form
  controls  form description lines between `Begin Form` and its `End`
  props     extra form property lines
  extra     more modules: a list of dicts with code/bas/controls/props
            (mdi: True makes a form an MDIForm)
  solo      True: in a project of its own
  vbx       custom control files the case uses (["GRID.VBX"])
  nostart   True: no START form (the case's first module starts the program)

`@SELF@` in code stands for the module's own (form) name, `@M<j>@` for the
case's j-th module's.

Cases are packed into projects of up to --chunk modules (one IDE run
each) under work/battery/<battery>/<id>/{orig,deco}. Each project is
compiled, decompiled, recompiled and compared per module. A project that
fails to compile, before or after decompiling, is split in half until
the failing case is found. Results per case:

  ok         p-code and form resource identical
  CODE       some procedure differs    FORM  form resource differs
  BADSRC     the case itself doesn't compile (fix the battery)
  DECOFAIL   the decompiled source doesn't compile
  CRASH      the decompiler raised

--check only compiles the cases (a failing chunk case by case), to
validate a new battery's source quickly.

A summary line per battery; details for failures only.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pcode_disasm as P  # noqa: E402
from decompile import Decompiler, write_project  # noqa: E402
from roundtrip import compile_mak, procs_code  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
BATTERIES = REPO / "batteries"
WORK = REPO / "work" / "battery"
RUNTIME = REPO / "work" / "ide" / "VBRUN300.DLL"

FORM = """VERSION 2.00
Begin Form {name}
   Caption         =   "{name}"
   ClientHeight    =   3000
   ClientLeft      =   1000
   ClientTop       =   1000
   ClientWidth     =   4000
   Height          =   3400
   Left            =   985
   LinkTopic       =   "{name}"
   ScaleHeight     =   3000
   ScaleWidth      =   4000
   Top             =   700
   Width           =   4030
{props}{controls}End
"""


def modules(case: dict) -> list[dict]:
    return [case] + list(case.get("extra", []))


def write_case_project(d: Path, stem: str, cases: list[tuple[int, dict]]) -> Path:
    """One project: a startup form, then per case its modules (F<i>x / M<i>x)."""
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    files = []
    if not any(c.get("nostart") for _, c in cases):  # else the case's first module starts the program
        start = "START"
        (d / f"{start}.FRM").write_bytes(FORM.format(name=start, props="", controls="").replace("\n", "\r\n").encode())
        files.append(f"{start}.FRM")
    for i, c in cases:
        mods = modules(c)
        mnames = [f"{'M' if m.get('bas') else 'F'}{i:03d}{chr(97 + j)}" for j, m in enumerate(mods)]
        for j, m in enumerate(mods):
            n = mnames[j]
            code = m.get("code", "").strip("\n").replace("@SELF@", n)
            for jj, nn in enumerate(mnames):
                code = code.replace(f"@M{jj}@", nn)
            code += "\n"
            if m.get("bas"):
                text = code
                fn = n + ".BAS"
            else:
                props = "".join(f"   {ln}\n" for ln in m.get("props", []))
                text = FORM.format(name=n, props=props, controls=m.get("controls", "")) + code
                if m.get("mdi"):  # an MDIForm: no Scale* properties
                    text = re.sub(r"   Scale(Height|Width) .*\n", "", text.replace("Begin Form", "Begin MDIForm", 1))
                fn = n + ".FRM"
            (d / fn).write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("latin-1"))
            files.append(fn)
    files += sorted({x for _, c in cases for x in c.get("vbx", [])})  # custom controls (from the IDE directory)
    mak = d / f"{stem}.MAK"
    mak.write_bytes(("\r\n".join(files) + "\r\nProjWinSize=152,402,248,215\r\nProjWinShow=2\r\n").encode())
    return mak


def module_files(mak: Path) -> list[str]:
    return [ln.strip() for ln in mak.read_text("latin-1").splitlines() if ln.strip().lower().endswith((".frm", ".bas"))]


def case_of(fn: str) -> int | None:
    s = Path(fn).stem
    return int(s[1:4]) if s[0] in "FM" and s[1:4].isdigit() else None


def per_case(orig_exe: Path, deco_exe: Path, mak: Path) -> dict[int, str]:
    """Compare two builds module by module; code segments follow the
    modules with code (.bas first, then forms, each in project order)."""
    out: dict[int, str] = {}
    files = module_files(mak)
    order = [f for f in files if f.lower().endswith(".bas")] + [f for f in files if f.lower().endswith(".frm")]

    def by_seg(exe):
        segs: dict[int, list] = {}
        for s, _, code in procs_code(exe):
            segs.setdefault(s, []).append(code)
        return [segs[k] for k in sorted(segs)]

    a, b = by_seg(orig_exe), by_seg(deco_exe)
    with_code = []
    for f in order:
        src = (mak.parent / f).read_bytes().decode("latin-1")
        body = src.split("\r\nEnd\r\n", 1)[1] if f.lower().endswith(".frm") else src
        if any(ln.strip().lower().startswith(("sub ", "function ", "static sub", "static function"))
               for ln in body.splitlines()):
            with_code.append(f)
    for k, f in enumerate(with_code):
        i = case_of(f)
        if i is not None and (k >= len(a) or k >= len(b) or a[k] != b[k]):
            out[i] = "CODE"
    ra, rb = P.rcdata(orig_exe), P.rcdata(deco_exe)
    fa = [ra[k] for k in sorted(ra) if ra[k][:2] == b"\xff\xcc"]
    fb = [rb[k] for k in sorted(rb) if rb[k][:2] == b"\xff\xcc"]
    forms = [f for f in files if f.lower().endswith(".frm")]
    for k, f in enumerate(forms):
        i = case_of(f)
        if i is not None and (k >= len(fa) or k >= len(fb) or fa[k] != fb[k]):
            out.setdefault(i, "FORM")
    return out


class Runner:
    def __init__(self, battery: str, cases: list[dict], chunk: int):
        self.battery, self.cases, self.chunk = battery, cases, chunk
        self.result: dict[int, str] = {}
        self.detail: dict[int, str] = {}
        self.nproj = 0

    def run(self, idx: list[int]) -> None:
        if not idx:
            return
        self.nproj += 1
        stem = f"B{self.nproj:03d}"
        root = WORK / self.battery / stem
        orig = write_case_project(root / "orig", stem, [(i, self.cases[i]) for i in idx])
        if not compile_mak(orig) or any(f.suffix.lower() == ".log" for f in orig.parent.iterdir()):
            return self.split(idx, "BADSRC", orig)
        try:
            d = Decompiler(orig.with_suffix(".exe") if orig.with_suffix(".exe").exists()
                           else next(p for p in orig.parent.iterdir() if p.suffix.lower() == ".exe"),
                           RUNTIME, [REPO / "work" / "ide"])
            dmak = write_project(d, root / "deco", None, stem.lower())
        except Exception:
            return self.split(idx, "CRASH", orig, traceback.format_exc(limit=3))
        for attempt in range(2):  # a second try: the OLE 2 control can fail to load right after a build
            for f in dmak.parent.iterdir():
                if f.suffix.lower() == ".log":
                    f.unlink()
            if compile_mak(dmak) and not any(f.suffix.lower() == ".log" for f in dmak.parent.iterdir()):
                break
        else:
            return self.split(idx, "DECOFAIL", dmak)
        oexe = next(p for p in orig.parent.iterdir() if p.suffix.lower() == ".exe")
        res = per_case(oexe, dmak.with_suffix(".exe"), orig)
        for i in idx:
            self.result[i] = res.get(i, "ok")

    def check(self, idx: list[int]) -> None:
        """Compile only; a failing chunk is retried case by case."""
        self.nproj += 1
        stem = f"B{self.nproj:03d}"
        orig = write_case_project(WORK / self.battery / stem / "orig", stem, [(i, self.cases[i]) for i in idx])
        if compile_mak(orig) and not any(f.suffix.lower() == ".log" for f in orig.parent.iterdir()):
            for i in idx:
                self.result[i] = "ok"
        elif len(idx) == 1:
            self.result[idx[0]] = "BADSRC"
            self.detail[idx[0]] = str(orig.with_suffix(".fail.png"))
        else:
            for i in idx:
                self.check([i])

    def split(self, idx: list[int], why: str, mak: Path, info: str = "") -> None:
        if len(idx) == 1:
            i = idx[0]
            self.result[i] = why
            logs = [f for f in mak.parent.iterdir() if f.suffix.lower() == ".log"]
            self.detail[i] = info or "; ".join(ln for f in logs for ln in f.read_text("latin-1").splitlines()[:2]) \
                or str(mak.with_suffix(".fail.png"))
            return
        h = len(idx) // 2
        self.run(idx[:h])
        self.run(idx[h:])


def load(name: str) -> list[dict]:
    g: dict = {}
    exec(compile((BATTERIES / f"{name}.py").read_text(), f"{name}.py", "exec"), g)
    return g["cases"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("battery", nargs="*")
    ap.add_argument("--chunk", type=int, default=24, help="modules per project")
    ap.add_argument("-k", help="only cases whose name contains this")
    ap.add_argument("--check", action="store_true", help="only compile the cases (validate the battery)")
    a = ap.parse_args()
    names = a.battery or sorted(p.stem for p in BATTERIES.glob("*.py"))
    for name in names:
        cases = load(name)
        sel = [i for i, c in enumerate(cases) if not a.k or a.k in c["name"]]
        r = Runner(name, cases, a.chunk)
        chunk: list[int] = []
        size = 0
        for i in sel:
            n = len(modules(cases[i]))
            if cases[i].get("solo") or cases[i].get("nostart"):  # a project of its own
                (r.check if a.check else r.run)([i])
                continue
            if chunk and size + n > a.chunk:
                (r.check if a.check else r.run)(chunk)
                chunk, size = [], 0
            chunk.append(i)
            size += n
        (r.check if a.check else r.run)(chunk)
        tally: dict[str, int] = {}
        for i in sel:
            tally[r.result.get(i, "?")] = tally.get(r.result.get(i, "?"), 0) + 1
        print(f"{name}: {tally.get('ok', 0)}/{len(sel)} ok  " +
              "  ".join(f"{k} {v}" for k, v in sorted(tally.items()) if k != "ok"), flush=True)
        for i in sel:
            if r.result.get(i) != "ok":
                print(f"  {i:03d} {r.result.get(i)} {cases[i]['name']}" +
                      (f"  ({r.detail[i][:150]})" if i in r.detail else ""), flush=True)


if __name__ == "__main__":
    main()
