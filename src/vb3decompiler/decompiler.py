"""
The decompiler: a VB3 executable to project source, one .frm/.bas per
module plus a .mak, from the p-code and the modules' data images. The
work is split over mixins (layout, analyze, declarations, naming,
localvars, emit); `run()` sequences them.
"""
from __future__ import annotations

import re
from pathlib import Path

from .analyze import AnalyzeMixin
from .dataimage import word
from .declarations import DeclarationsMixin
from .emit import EmitMixin
from .forms import form_text, forms
from .layout import LayoutMixin
from .localvars import LocalsMixin
from .naming import NamingMixin
from .ne import PROC_TABLE_SEGMENT, find_procs, form_names, parse_ne, rcdata, vbx_entries
from .runtime import Runtime
from .symbols import Symbols, proc_names


class Decompiler(LayoutMixin, AnalyzeMixin, DeclarationsMixin, NamingMixin, LocalsMixin, EmitMixin):
    """One executable's decompilation. Modules are dicts (`module_list`)
    that the passes fill in: vars/infos (analyze), items (declarations),
    names (naming), lines (emit)."""

    def __init__(self, exe: Path, runtime: Path, vbx_dirs: list[Path]):
        self.exe = exe
        self.rt = rt = Runtime(runtime)
        rt.vbx_dirs = [exe.parent, *vbx_dirs]
        self.segs = parse_ne(exe)
        self.res = rcdata(exe)
        self.sym = Symbols(rt, self.segs, self.res)
        self.events = proc_names(self.segs, rt, self.res)
        rt.event_lists()  # fills EVENT_TYPES
        self.ids = {op: rt.opcode_id(op) or 0 for op in range(len(rt.code))}
        self.col_fixes: list[tuple[str, int]] = []  # (ReDim target, characters too many before a compiled column)
        self.table = self.segs[PROC_TABLE_SEGMENT - 1].data
        self.image = self.res.get(2, b"")
        self.forms = form_names(self.res)
        # entries `FF 01, u16 heap address (varies), u8, file name, 0`: the address
        # bytes can look like text, so the name starts after them
        self.form_files = [m.decode("latin-1") for m in
                           re.findall(rb"(?s)\xff\x01...([\x21-\x7e]+?\.FRM)\x00", self.res.get(1, b""), re.I)]
        self.procs = find_procs(self.segs)
        self.by_record = {p.record: p for p in self.procs}
        self.proc_name: dict[int, str] = {}  # record -> emitted procedure name
        self.cur_base: int | None = None
        self.pool: int | None = None
        self.call_types: dict[int, list] = {}

    def run(self) -> list[dict]:
        """Modules with their source lines (m['lines']). Passes, in order:
        analyze each module's p-code; recover the module-level declarations;
        name variables and procedures; record calls (argument types for
        parameters); emit the text, re-emitted once when the unused locals'
        split doesn't give the module's item count (declarations +12); give
        the slot-numbered names readable ones; last, pick local name lengths
        that keep the object-local free order."""
        if hasattr(self, "_mods"):
            return self._mods
        mods = self.module_list()
        for m in mods:
            self.analyze_module(m)
        self.declarations(mods)
        self.all_mods = mods
        self.decl_home = next((m for m in mods if m["kind"] == "bas"), mods[0])
        for m in mods:
            self.name_module(m)
        self.collect_calls(mods)
        self.slotted = {r for m in mods for _, r in m["funcs"]}
        for m in mods:
            m["lines"] = self.emit_module(m)
            want = (word(self.table, word(self.image, m["image"] - 2) + 4 + 12) - word(self.image, m["image"])) // 2
            if d := self.item_count(m) - want:  # unused Variants (2 slots, 1 item) vs other locals
                m["nv_pick"], m["nv_delta"] = max(0, -d), d
                m["lines"] = self.emit_module(m)
        self.prettify(mods)
        for m in mods:  # (object locals' free order depends on name lengths)
            self.fit_frees(m)
        self._mods = mods
        return mods


def write_project(d: Decompiler, out: Path, layout_from: Path | None, name: str) -> Path:
    """Writes the module files and a .mak named after the executable; returns the .mak."""
    out.mkdir(parents=True, exist_ok=True)
    mods = d.run()
    code = {m["form"]: m["lines"] for m in mods if m["form"]}
    modules = [m["lines"] for m in mods if not m["form"]]
    originals = {f.name.upper(): f for f in layout_from.iterdir()} if layout_from else {}
    files = []
    decoded = [] if layout_from else forms(d.exe, d.rt)
    for k, form in enumerate(d.forms):
        fname = d.form_files[k] if k < len(d.form_files) else f"FORM{k + 1}.FRM"
        layout = ["VERSION 2.00", f"Begin Form {form[0]}", "End"]
        if k < len(decoded):
            frx = bytearray()
            frx_name = Path(fname).with_suffix(".FRX").name
            layout = ["VERSION 2.00"] + form_text(decoded[k], frx, frx_name)
            if frx:
                (out / frx_name).write_bytes(frx)
        src = originals.get(fname.upper())
        if src:
            text = src.read_bytes().decode("latin-1").replace("\r", "").split("\n")
            layout, depth = [], 0
            for line in text:
                layout.append(line)
                t = line.strip()
                if t.startswith("Begin "):
                    depth += 1
                elif t == "End":
                    depth -= 1
                    if depth == 0:
                        break
            frx = src.with_suffix(".frx")
            for f in layout_from.iterdir():
                if f.name.upper() == frx.name.upper():
                    (out / f.name).write_bytes(f.read_bytes())
        body = code.get(form[0], [])
        (out / fname).write_bytes(("\r\n".join(layout + body) + "\r\n").encode("latin-1"))
        files.append(fname)
    used = {Path(f).stem.upper() for f in files}
    stems = [s for s in (f"MODULE{j}" for j in range(1, len(modules) + len(used) + 2)) if s not in used]
    for k, lines in enumerate(modules):
        fname = f"{stems[k]}.BAS"
        (out / fname).write_bytes(("\r\n".join(lines) + "\r\n").encode("latin-1"))
        files.append(fname)
    # project directory (RT_RCDATA 1): 9-byte executable name, u16, u16, title
    r1 = d.res.get(1, b"")
    exe_name = r1[6:15].split(b"\0")[0].decode("latin-1") or name
    title = r1[19:].split(b"\0")[0].decode("latin-1")
    settings = ["ProjWinSize=152,402,248,215", "ProjWinShow=2", f'Title="{title}"']
    files += [v for v in vbx_entries(r1) if v.upper().endswith(".VBX")]  # custom controls
    mak = out / f"{exe_name}.mak"
    mak.write_bytes(("\r\n".join(files + settings) + "\r\n").encode("latin-1"))
    return mak
