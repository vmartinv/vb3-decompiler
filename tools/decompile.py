#!/usr/bin/env python3
"""
Decompiles a VB3 executable to project source: one .frm/.bas per module
plus a .mak, from the p-code and the modules' data images.

  python3 tools/decompile.py app.exe --runtime VBRUN300.DLL [--vbx-dir DIR] --out DIR
          [--layout-from SRC_DIR]

--layout-from copies each form's description block (Begin Form ... End)
from the original .frm files; form layouts aren't decoded yet.

Recovery rules (see ../OPCODES.md, "Source recovery"):
  - variables, parameters, return values and controls share one slot
    numbering per module, assigned in source text order; a local's or
    parameter's slot holds its BP offset (the word 2 bytes past the slot):
    > 0 parameter, < 0 local (sizes from the gaps), 1 String;
  - Functions get the first slots (their record offsets), then the module
    declarations, then each procedure in text order;
  - code layout is the procedures sorted by name (case-insensitive), so
    general procedures (whose names aren't stored) get names that keep it.
"""
from __future__ import annotations

import argparse
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pcode_disasm as P  # noqa: E402
from lift import lift  # noqa: E402
from opcodes import NAMES  # noqa: E402

SUFFIX = {"I": "%", "L": "&", "S": "!", "D": "#", "C": "@", "T": "$", "V": ""}
TYPE_NAME = {"I": "Integer", "L": "Long", "S": "Single", "D": "Double", "C": "Currency", "T": "String",
             "V": "Variant"}
RET_TYPE = {1: "I", 2: "L", 3: "S", 4: "D", 5: "C", 6: "V", 7: "T"}  # procedure record +13
EVENT_TYPE = {1: "Integer", 2: "Long", 3: "Single", 4: "Double", 5: "Currency", 6: "String", 8: "Control"}
EVENT_PARAMS = {  # conventional parameter names (the IDE's templates)
    "DragDrop": "Source X Y", "DragOver": "Source X Y State", "KeyDown": "KeyCode Shift",
    "KeyUp": "KeyCode Shift", "KeyPress": "KeyAscii", "MouseDown": "Button Shift X Y",
    "MouseUp": "Button Shift X Y", "MouseMove": "Button Shift X Y", "LinkError": "LinkErr",
    "LinkOpen": "Cancel", "Unload": "Cancel", "QueryUnload": "Cancel UnloadMode",
    "LinkExecute": "CmdStr Cancel", "Error": "DataErr Response", "Validate": "Action Save",
    "Collapse": "ListIndex", "Expand": "ListIndex", "PictureClick": "ListIndex",
    "PictureDblClick": "ListIndex",
}
LABEL = 0x4965
VAR_FAMILIES = ("LOAD", "STORE", "ADDR_LOC", "ALOAD", "ASTORE", "ADDR", "AADDR")
SIZE_TYPES = {2: "I", 4: "L", 8: "D", 16: "V"}  # filler declarations for unused slots


@dataclass
class Var:
    slot: int
    scope: str  # MOD / LOC / REF / GLB
    votes: dict = field(default_factory=dict)  # type letter -> count
    array: bool = False
    procs: list = field(default_factory=list)  # procedures (layout index) referencing it

    def type(self) -> str:
        if not self.votes:
            return "V"
        return max(self.votes, key=lambda t: (self.votes[t], t != "L"))


@dataclass
class ProcInfo:
    proc: P.Proc
    insns: list
    notes: list
    name: str = ""
    event: bool = False
    function: bool = False
    ret: str = "V"
    argwords: int = 0
    params: list = field(default_factory=list)  # (slot, text)
    ret_slot: int | None = None


def var_access(name: str) -> tuple[str, str, bool] | None:
    """Handler name -> (scope, type letters, array) for variable accesses."""
    parts = name.split(".")
    fam = parts[0]
    if fam not in VAR_FAMILIES:
        return None
    if fam == "ADDR_LOC":
        return "LOC", parts[1] if len(parts) > 1 else "", False
    if fam in ("ADDR", "AADDR"):
        return (parts[1] if len(parts) > 1 else "GLB"), "", fam == "AADDR"
    if len(parts) < 2:
        return None
    return parts[1], parts[2] if len(parts) > 2 else "", fam in ("ALOAD", "ASTORE")


SUFFIX_OF_ID = {1: "%", 2: "&", 3: "!", 4: "#", 5: "@", 7: "$"}  # interpreter ID >> 10
TYPE_OF_SUFFIX = {"%": "I", "&": "L", "!": "S", "#": "D", "@": "C", "$": "T"}


def plain_handler(rt: P.Runtime, op: int) -> tuple[str | None, str]:
    """A variable access written with a type suffix (`b% = 3`) uses another
    entry point of the plain handler (usually 3 bytes before it) whose
    interpreter ID carries the suffix type: ID = plain ID | type << 10.
    Returns (plain handler name, suffix)."""
    if op in NAMES:
        return NAMES[op], ""
    oid = rt.opcode_id(op)
    if oid is None or oid >> 10 not in SUFFIX_OF_ID:
        return None, ""
    for k in sorted(range(-16, 17), key=abs):
        n = NAMES.get(op + k)
        if n and n.split(".")[0] in VAR_FAMILIES and rt.opcode_id(op + k) == oid & 0x3FF:
            return n, SUFFIX_OF_ID[oid >> 10]
    return None, ""


STMT_GROUPS = [0x494B, 0x4935, 0x491F, 0x4906, 0x48F0, 0x48D7, 0x48C1]  # columns 0, 4, 8, ...


def stmt_column(rt: P.Runtime, op: int) -> int | None:
    """A statement marker's entry point encodes the line's indentation
    (the IDE regenerates source text from p-code): the countdown entries
    above are columns 0, 4, 8, ...; the `mov ax, NN00` entries just before
    each are columns (NN >> 2) + 1."""
    if op in STMT_GROUPS:
        return 4 * STMT_GROUPS.index(op)
    c = rt.code
    if c[op] == 0xB8 and c[op + 1] == 0:
        return (c[op + 2] >> 2) + 1
    return None


class Decompiler:
    def __init__(self, exe: Path, runtime: Path, vbx_dirs: list[Path]):
        self.rt = rt = P.Runtime(runtime)
        rt.vbx_dirs = [exe.parent, *vbx_dirs]
        self.segs = P.parse_ne(exe)
        self.res = P.rcdata(exe)
        self.sym = P.Symbols(rt, self.segs, self.res)
        self.events = P.proc_names(self.segs, rt, self.res)
        rt.event_lists()  # fills P.EVENT_TYPES
        self.ids = {op: rt.opcode_id(op) or 0 for op in range(len(rt.code))}
        self.table = self.segs[P.PROC_TABLE_SEGMENT - 1].data
        self.image = self.res.get(2, b"")
        self.forms = P.form_names(self.res)
        self.form_files = [m.decode("latin-1") for m in
                           re.findall(rb"([\x21-\x7e]+\.FRM)\x00", self.res.get(1, b""), re.I)]
        self.procs = P.find_procs(self.segs)
        self.by_record = {p.record: p for p in self.procs}
        self.proc_name: dict[int, str] = {}  # record -> emitted procedure name

    # --- per module -----------------------------------------------------
    def modules(self) -> list[tuple[int, str | None]]:
        """(code segment, form name or None) in segment order."""
        segs = sorted({p.segment for p in self.procs})
        return [(s, self.sym.seg_form.get(s)) for s in segs]

    def image_base(self, seg: int, frame_slots: list[int], top: int) -> int | None:
        """The segment's data image: known from its control references, else
        the first free chunk (after the previous segment's) that covers its
        slots and gives every local/parameter slot a BP offset."""
        if seg in P.SEG_IMAGE:
            return P.SEG_IMAGE[seg]
        chunks = [m.start() for m in re.finditer(rb"(?=..\x00\x00\x1e\x00)", self.image, re.S)]
        used = set(P.SEG_IMAGE.values())
        after = max([c for s, c in P.SEG_IMAGE.items() if s < seg], default=chunks[0] if chunks else -1)
        for c in chunks:
            if c in used or c <= after or struct.unpack_from("<H", self.image, c)[0] < top + 2:
                continue
            if all(self.value(c, s) for s in frame_slots):
                P.SEG_IMAGE[seg] = c
                return c
        return None

    def value(self, base: int, slot: int) -> int:
        o = base + slot + 2
        return struct.unpack_from("<h", self.image, o)[0] if o + 2 <= len(self.image) else 0

    def analyze_module(self, seg: int, form: str | None) -> dict:
        procs = [p for p in self.procs if p.segment == seg]  # layout order
        infos = []
        for p in procs:
            insns, err = P.decode(self.rt, self.segs[seg - 1].data, p)
            notes = self.sym.annotate(seg, insns)
            rec = self.table[p.record:p.record + 20]
            info = ProcInfo(p, insns, notes)
            info.function = rec[12] == 2
            info.ret = RET_TYPE.get(rec[13], "V")
            info.argwords = rec[15]
            infos.append(info)

        # variables: scope/type per slot
        vars_: dict[int, Var] = {}
        for k, info in enumerate(infos):
            for j, i in enumerate(info.insns):
                n, sfx = plain_handler(self.rt, i.op)
                if n is None and P.is_objarr(self.rt, i):
                    n = "ALOAD.MOD.V"  # object array element (typed separately)
                acc = var_access(n or "")
                if not acc or not i.operand:
                    continue
                scope, t, arr = acc
                if scope == "GLB":
                    continue
                slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                v = vars_.setdefault(slot, Var(slot, scope))
                if scope == "REF":
                    v.scope = "REF"
                v.array |= arr
                if k not in v.procs:
                    v.procs.append(k)
                if sfx:
                    t = TYPE_OF_SUFFIX[sfx]
                elif t == "L/T":
                    nxt = NAMES.get(info.insns[j + 1].op, "") if j + 1 < len(info.insns) else ""
                    t = "T" if (".T" in nxt or "T>" in nxt or nxt in ("ARG_STR", "CONCAT")) else \
                        "L" if (".L" in nxt or "L>" in nxt) else ""
                if t in SUFFIX:
                    v.votes[t] = v.votes.get(t, 0) + 1
        base = self.image_base(seg, [s for s, v in vars_.items() if v.scope in ("LOC", "REF")], max(vars_, default=0))

        # control/form slots referenced by each procedure
        refs: dict[int, tuple[int, str]] = {}  # slot -> (first proc, name)
        for k, info in enumerate(infos):
            for i, note in zip(info.insns, info.notes):
                n = NAMES.get(i.op)
                if n in ("CONTROL", "CTLARRAY", "FORM") and i.operand and note:
                    slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                    refs.setdefault(slot, (k, note))

        # procedure-owned slots: parameters (> 0), locals (< 0), strings (1)
        owned = {s for s, v in vars_.items() if v.scope in ("LOC", "REF")}
        first_owned = min(owned | set(refs), default=1 << 16)
        module_vars = sorted(s for s, v in vars_.items() if v.scope == "MOD" and s < first_owned)

        names: dict[int, str] = {}
        for s in module_vars:
            v = vars_[s]
            names[s] = f"m{s:X}"
        for s, v in vars_.items():
            if s not in names:
                pre = "s" if v.scope == "MOD" else "p" if v.scope == "REF" else "v"
                names[s] = f"{pre}{s:X}"
        for s, (_, n) in refs.items():
            names[s] = n

        # procedure names: events as stored, others fitted into the layout order
        for info in infos:
            ev = self.events.get(info.proc.record)
            if ev:
                info.name, info.event = ev, True
        self.fit_names(infos)

        # parameters and return values: slots with BP offsets > 0 per procedure
        for k, info in enumerate(infos):
            mine = sorted(s for s, v in vars_.items() if v.procs and v.procs[0] == k and v.scope in ("LOC", "REF"))
            if info.function and mine:
                info.ret_slot = mine[0]
                names[mine[0]] = info.name
                mine = mine[1:]
            params = [s for s in mine if base is not None and self.value(base, s) > 1]
            info.params = params
        for info in infos:
            self.proc_name[info.proc.record] = info.name
        return dict(seg=seg, form=form, infos=infos, vars=vars_, names=names, base=base, module_vars=module_vars)

    def emit_module(self, m: dict) -> list[str]:
        out = []
        for s in m["module_vars"]:
            v = m["vars"][s]
            out.append(f"Dim {m['names'][s]}" + ("()" if v.array else "") + f" As {TYPE_NAME[v.type()]}")
        if out:
            out.append("")
        for info in m["infos"]:
            out += self.emit_proc(info, m["form"], m["vars"], m["names"], m["base"])
        return out

    def run(self) -> list[tuple[str | None, list[str]]]:
        """(form name or None for a module, source lines) per code segment."""
        mods = [self.analyze_module(seg, form) for seg, form in self.modules()]
        return [(m["form"], self.emit_module(m)) for m in mods]

    def fit_names(self, infos: list[ProcInfo]) -> None:
        """Names for general procedures that keep the code layout order
        (procedures sorted by name, case-insensitive)."""
        n = 0
        for k, info in enumerate(infos):
            if info.name:
                continue
            lo = next((x.name for x in reversed(infos[:k]) if x.name), "")
            hi = next((x.name for x in infos[k + 1:] if x.name and x.event), None)
            while True:
                n += 1
                for cand in (f"{'Func' if info.function else 'Proc'}{n}", f"{lo}_{n}" if lo else f"A{n}"):
                    if cand.lower() > lo.lower() and (hi is None or cand.lower() < hi.lower()):
                        info.name = cand
                        break
                if info.name:
                    break

    def emit_proc(self, info: ProcInfo, form: str | None, vars_: dict, names: dict, base: int | None) -> list[str]:
        kind = "Function" if info.function else "Sub"
        params = []
        if info.event:
            ctl, _, ev = info.name.rpartition("_")
            cls = self.sym.form_class.get(form, "Form") if ctl in ("Form", "MDIForm") else \
                self.sym.classes.get((form, ctl), "")
            types = P.EVENT_TYPES.get((cls, ev), P.MASTER_EVENT_TYPES.get(ev, ()))
            pnames = EVENT_PARAMS.get(ev, "").split() or [f"P{j + 1}" for j in range(len(types))]
            decl = [f"{pn} As {EVENT_TYPE.get(t, 'Integer')}" for pn, t in zip(pnames, types)]
            if info.argwords > 2 * len(types):  # control array element
                decl = ["Index As Integer"] + decl
            for s, d in zip(info.params, decl):
                names[s] = d.split()[0]
            params = decl
        else:
            for s in info.params:
                v = vars_[s]
                t = v.type()
                byval = v.scope == "LOC" and t != "T"
                pn = names[s]
                params.append(("ByVal " if byval else "") + pn + ("()" if v.array else "") + f" As {TYPE_NAME[t]}")
        head = f"{kind} {info.name} ({', '.join(params)})"
        if info.function:
            head += f" As {TYPE_NAME[info.ret]}"
        body = self.statements(info, names)
        # record +50: the procedure's line count, including the comment block
        # above it (comments aren't compiled): pad with empty comments
        (count,) = struct.unpack_from("<H", self.table, info.proc.record + 50)
        lines = ["'"] * max(0, count - len(body) - 2) + [head]
        for col, text in body:
            if info.function:
                text = re.sub(r"\bExit Sub\b", "Exit Function", text)
            lines.append(" " * col + text)
        lines += [f"End {kind}", ""]
        return lines

    def statements(self, info: ProcInfo, names: dict) -> list[tuple[int, str]]:
        """(indentation column, lifted text) per statement (marker to the
        next marker), with label lines."""
        out, cur, curn = [], [], []
        col = [0]

        def flush():
            if cur and not all(NAMES.get(op, "") in ("RET", "TRAP") for op, _ in cur):
                out.append((col[0], lift(cur, self.ids, names=curn)))
            cur.clear()
            curn.clear()

        for i, note in zip(info.insns, info.notes):
            if self.rt.is_stmt(i.op):
                flush()
                c = stmt_column(self.rt, i.op)
                col[0] = 4 if c is None else c
                continue
            if i.op == LABEL:
                flush()
                (num,) = struct.unpack_from("<I", i.operand)
                out.append((0, f"L{i.pc}:" if num == 0xFFFFFFFF else f"{num}"))
                continue
            cur.append((i.op, i.operand))
            curn.append(self.name_for(i, note, names))
        flush()
        return out

    def name_for(self, i, note: str, names: dict) -> str | None:
        n = NAMES.get(i.op) or ""
        slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0] if len(i.operand) >= 2 else None
        if n in ("PGET", "PSET", "PGET_IDX", "PSET_IDX"):
            if "!" in note:
                return ""  # default property of a control
            return note.rpartition(".")[2] or None
        if n in ("PGET_ME", "PSET_ME"):
            return note.rpartition(".")[2] if note else None
        if n in ("SUBOBJ", "CTLARRAY_OF"):
            return note.rpartition("!")[2].rpartition(".")[2] or None
        if n == "OLE_CALL":
            return note.rpartition(".")[2] or None
        if n in ("CALL", "CALL_FN"):
            (rec,) = struct.unpack_from("<H", i.operand, 2)
            return self.proc_name.get(rec & 0xFFF8)
        if slot is not None and slot in names:
            return names[slot] + plain_handler(self.rt, i.op)[1]
        return note or None


def write_project(d: Decompiler, out: Path, layout_from: Path | None, name: str) -> Path:
    """Writes the module files and a .mak named after the executable; returns the .mak."""
    out.mkdir(parents=True, exist_ok=True)
    code = {form: lines for form, lines in d.run() if form}
    modules = [lines for form, lines in d.run() if not form]
    originals = {f.name.upper(): f for f in layout_from.iterdir()} if layout_from else {}
    files = []
    for k, form in enumerate(d.forms):
        fname = d.form_files[k] if k < len(d.form_files) else f"FORM{k + 1}.FRM"
        layout = ["VERSION 2.00", f"Begin Form {form[0]}", "End"]
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
    for k, lines in enumerate(modules):
        fname = f"MODULE{k + 1}.BAS"
        (out / fname).write_bytes(("\r\n".join(lines) + "\r\n").encode("latin-1"))
        files.append(fname)
    # project directory (RT_RCDATA 1): 9-byte executable name, u16, u16, title
    r1 = d.res.get(1, b"")
    exe_name = r1[6:15].split(b"\0")[0].decode("latin-1") or name
    title = r1[19:].split(b"\0")[0].decode("latin-1")
    settings = ["ProjWinSize=152,402,248,215", "ProjWinShow=2", f'Title="{title}"']
    mak = out / f"{exe_name}.mak"
    mak.write_bytes(("\r\n".join(files + settings) + "\r\n").encode("latin-1"))
    return mak


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("exe", type=Path)
    ap.add_argument("--runtime", type=Path, required=True)
    ap.add_argument("--vbx-dir", type=Path, action="append", default=[])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--layout-from", type=Path, help="copy form description blocks from these .frm files")
    args = ap.parse_args()
    d = Decompiler(args.exe, args.runtime, args.vbx_dir)
    print(write_project(d, args.out, args.layout_from, args.exe.stem))


if __name__ == "__main__":
    main()
