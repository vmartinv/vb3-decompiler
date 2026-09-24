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
    even >= 6 parameter, < 0 local (sizes from the gaps), odd String local;
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
from vbdecl import MOD_SIZE, GlobalImage, const_literal, word  # noqa: E402

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
    stored: bool = False
    udt: bool = False
    udt_type: int | None = None  # Type of an array's elements
    obj: str | None = None  # object variable's class (As Control, As frmX, ...)

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


def image_layout(image: bytes, nforms: int) -> dict:
    """RT_RCDATA 2 structure: chunks `u16 len, 00 00, 1E 00, ...`:
    global image, 4-byte end; name pool (u16 size, 32-bucket hash table,
    entries `u16 link, u8 flag, u8 len, name`; offsets from its start + 2);
    then the .bas module images, then per form its image and control list
    (4-byte end chunks in between, not always)."""
    first = re.search(rb"..\x00\x00\x1e\x00", image, re.S)
    out = dict(global_=first.start() if first else None, pool=None, modules=[], forms=[])
    if not first:
        return out
    # walk: chunks are `u16 len, u16, u16 0x1E`; the name pool (`u16 size, 0,
    # 0x1A`) sits between the global image's chunks and the modules
    chunks, c = [], first.start()
    while c + 6 <= len(image):
        n, tag = struct.unpack_from("<H", image, c)[0], struct.unpack_from("<H", image, c + 4)[0]
        if tag == 0x1A and out["pool"] is None:
            out["pool"] = c
            c += 2  # the pool's size word doesn't cover its last 2 bytes
        elif tag != 0x1E:
            if c + 8 <= len(image) and struct.unpack_from("<H", image, c + 6)[0] in (0x1E, 0x1A):
                c += 2  # a 2-byte prefix before some module chunks
                continue
            break
        else:
            chunks.append((c, n))
        c += 2 + n
    rest = [c for c, n in chunks[1:] if n != 4 and (out["pool"] is None or c > out["pool"])]
    # a form image starts with 16 zero bytes, then the form's own record at
    # 0x16; the chunk after it (if not another form) is its control list
    is_form = [struct.unpack_from("<H", image, c)[0] >= 0x1A and not any(image[c + 6:c + 0x16])
               and image[c + 0x16] != 0 for c in rest]
    idx = [j for j, f in enumerate(is_form) if f][-nforms:] if nforms else []
    first = idx[0] if idx else len(rest)
    out["modules"] = rest[:first]
    out["forms"] = [(rest[j], rest[j + 1] if j + 1 < len(rest) and j + 1 not in idx else None) for j in idx]
    return out


def pool_name(image: bytes, pool: int, off: int) -> str:
    p = pool + 2 + off
    return image[p + 4:p + 4 + image[p + 3]].decode("latin-1")


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


def lt_hint(nxt: str) -> str:
    """Long or String for a shared 4-byte load, from the handler consuming it."""
    if nxt.startswith("CVT."):
        src = nxt[4:].split(">")[0]
        return src if src in ("L", "T") else ""
    if nxt in ("ARG_STR", "CONCAT"):
        return "T"
    parts = nxt.split(".")
    if len(parts) == 2 and parts[0] not in ("LOAD", "STORE", "ALOAD", "ASTORE", "ADDR_LOC") and parts[1] in ("L", "T"):
        return parts[1]  # ADD.T, EQ.L, ...
    return ""


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
        self.cur_base: int | None = None
        self.pool: int | None = None
        self.call_types: dict[int, list] = {}

    # --- modules ----------------------------------------------------------
    def value(self, base: int, slot: int, signed: bool = True) -> int:
        o = base + slot + 2
        if o + 2 > len(self.image):
            return 0
        return struct.unpack_from("<h" if signed else "<H", self.image, o)[0]

    def is_record(self, r: int) -> bool:
        return r in self.by_record or self.is_declare(r)

    def is_declare(self, r: int) -> bool:
        t = self.table
        return 0 <= r <= len(t) - 56 and r % 8 == 0 and t[r + 14] == 0x0C and t[r + 12] in (1, 2) \
            and self.pool is not None and pool_name(self.image, self.pool, word(t, r + 46)).isprintable()

    def module_list(self) -> list[dict]:
        """Every module (from the data images) with its code segment, if any."""
        lay = image_layout(self.image, len(self.forms))
        self.pool = lay["pool"]
        self.gimg = GlobalImage(self.image, lay["global_"])
        mods = [dict(kind="bas", image=c, form=None, seg=None, start=0x06) for c in lay["modules"]]
        mods += [dict(kind="frm", image=c, form=self.forms[k][0], seg=None, start=0x1A, ctl=cl)
                 for k, (c, cl) in enumerate(lay["forms"])]
        for m in mods:  # declarations record: word before the image + 4 (+18 flags: 0x40 Option Explicit)
            m["explicit"] = bool(word(self.table, word(self.image, m["image"] - 2) + 4 + 18) & 0x40)
        for m in mods:  # Function/Declare slots: record offsets (sorted by name)
            m["funcs"], s = [], m["start"]
            while self.is_record(self.value(m["image"], s, False)):
                m["funcs"].append((s, self.value(m["image"], s, False)))
                s += 2
            m["decl_start"] = s
        segs = sorted({p.segment for p in self.procs})
        free = [m for m in mods if m["kind"] == "bas"]
        for seg in segs:
            recs = {p.record for p in self.procs if p.segment == seg}
            form = self.sym.seg_form.get(seg) or next((P.RECORD_FORM[r] for r in recs if r in P.RECORD_FORM), None)
            if form:
                m = next((m for m in mods if m["form"] == form), None)
            else:
                m = next((m for m in free if recs & {r for _, r in m["funcs"]}), None) or \
                    next((m for m in free if m["seg"] is None and not m["funcs"]), None)
            if m is not None:
                m["seg"] = seg
                P.SEG_IMAGE[seg] = m["image"]
                if m in free:
                    free.remove(m)
        return mods

    def analyze_module(self, m: dict) -> None:
        seg, base = m["seg"], m["image"]
        procs = [p for p in self.procs if p.segment == seg] if seg else []  # layout order
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

        # variables: scope/type per slot; globals referenced through this module's slots
        vars_: dict[int, Var] = {}
        udt: dict[int, int] = {}  # UDT variable slot -> Type offset
        for k, info in enumerate(infos):
            last_udt = None
            for j, i in enumerate(info.insns):
                n, sfx = plain_handler(self.rt, i.op)
                if n is None and P.is_objarr(self.rt, i):
                    n = "ALOAD.MOD.V"  # object array element (typed separately)
                if n == "LOAD.UDT" and i.operand:
                    last_udt = struct.unpack_from("<H", i.operand)[0]
                    v = vars_.setdefault(last_udt, Var(last_udt, "MOD"))
                    v.udt = True
                    if k not in v.procs:
                        v.procs.append(k)
                    continue
                if n and n.startswith(("FIELD_", )) and last_udt is not None and i.operand:
                    td = self.gimg.field_type.get(struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0])
                    if td:
                        udt[last_udt] = td.g
                    last_udt = None
                if n == "OBJVAR" and i.operand:  # object variable: record `kind, BP offset / 0`
                    slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                    bp = self.value(base, slot)
                    if bp != 0:  # parameter or local (module-level ones are declarations)
                        v = vars_.setdefault(slot, Var(slot, "LOC"))
                        v.obj = self.sym.objvar_types.get(seg, {}).get(slot) or "Control"
                        if k not in v.procs:
                            v.procs.append(k)
                    continue
                acc = var_access(n or "")
                if not acc or not i.operand:
                    continue
                scope, t, arr = acc
                slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                v = vars_.setdefault(slot, Var(slot, scope))
                if scope == "REF":
                    v.scope = "REF"
                if n.startswith(("STORE", "ASTORE", "ADDR", "AADDR")):
                    v.stored = True
                v.array |= arr
                if arr and j + 1 < len(info.insns):  # array of a Type: `a(i).field`
                    nn = NAMES.get(info.insns[j + 1].op, "")
                    if nn.startswith("FIELD_") and info.insns[j + 1].operand:
                        td = self.gimg.field_type.get(struct.unpack_from("<H", info.insns[j + 1].operand)[0])
                        if td:
                            v.udt_type = td.g
                if k not in v.procs:
                    v.procs.append(k)
                if sfx:
                    t = TYPE_OF_SUFFIX[sfx]
                elif t == "L/T":
                    nxt = NAMES.get(info.insns[j + 1].op, "") if j + 1 < len(info.insns) else ""
                    t = lt_hint(nxt)
                if t in SUFFIX:
                    v.votes[t] = v.votes.get(t, 0) + 1

        # control/form slots referenced by each procedure
        refs: dict[int, tuple[int, str]] = {}  # slot -> (first proc, name)
        for k, info in enumerate(infos):
            for i, note in zip(info.insns, info.notes):
                n = NAMES.get(i.op)
                if n in ("CONTROL", "CTLARRAY", "FORM") and i.operand and note:
                    slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                    refs.setdefault(slot, (k, note))
                elif n in ("PGET_ME", "PSET_ME") and i.operand and note:  # `Readout`, `Left`
                    slot = struct.unpack_from("<H", i.operand)[0]
                    refs.setdefault(slot, (k, note.rpartition(".")[2]))

        owned = {s for s, v in vars_.items() if v.scope in ("LOC", "REF")}
        # control/property operands point at their record, 2 bytes past a variable's slot
        first_owned = min(owned | {r - 2 for r in refs} | {s for s, v in vars_.items() if v.scope == "GLB"
                                               and s >= m["decl_start"] and not self.is_global_slot(base, s)},
                          default=word(self.image, base) - 2 if not infos else 1 << 16)
        m.update(infos=infos, vars=vars_, refs=refs, udt=udt, first_owned=first_owned)

    def is_global_slot(self, base: int, slot: int) -> bool:
        g = self.value(base, slot, False)
        return 6 <= g < self.globals_end and g % 2 == 0

    @property
    def globals_end(self) -> int:
        """End of the global variables: the global object table (`0x80NN, 0, 0`
        per form/object) follows them."""
        gl = self.gimg
        for g in range(6, gl.size, 2):
            if gl.w(g) >> 8 == 0x80 and gl.w(g + 2) == 0 and gl.w(g + 4) == 0 \
                    and all(gl.w(x) >> 8 == 0x80 for x in range(g, gl.size - 4, 6)):
                return g
        return gl.size

    def declarations(self, mods: list[dict]) -> None:
        """Header items per module, in slot (= text) order: Global, Dim, Const,
        and Types placed by global offset."""
        gl = self.gimg
        uses: dict[int, dict] = {}  # global offset -> {votes, stored}
        for m in mods:
            for s, v in m["vars"].items():
                if v.scope == "GLB":
                    g = self.value(m["image"], s, False)
                    u = uses.setdefault(g, dict(votes={}, stored=False, array=False, mods=set()))
                    u["mods"].add(id(m))
                    for t, c in v.votes.items():
                        u["votes"][t] = u["votes"].get(t, 0) + c
                    u["stored"] |= v.stored
                    u["array"] |= v.array
                    u["udt"] = u.get("udt") or v.udt_type
        self.global_name = {}
        for m in mods:
            items, s = [], m["decl_start"]
            end = m["first_owned"]
            known = sorted(x for x in m["vars"] if x >= s)
            while s < end:
                v = m["vars"].get(s)
                nxt = next((x for x in known if x > s), end)
                g = self.value(m["image"], s, False)
                if v is None and g in gl.types:  # reference to a Type (first `As T` in the module)
                    items.append(("typeref", s, None))
                    s += 2
                    continue
                if v is not None and v.scope == "MOD":
                    t = v.type()
                    if v.udt and s not in m["udt"]:
                        td = next((t for t in gl.types.values() if 0 <= nxt - s - t.size <= 4), None)
                        if td:
                            m["udt"][s] = td.g
                    if s in m["udt"]:
                        td = gl.types.get(m["udt"][s])
                        items.append(("dim", s, f"As {td.name}" if td else "As Variant"))
                        step = (td.size + 1) // 2 * 2 if td else 16
                        s = nxt if step <= nxt - s <= step + 4 else s + step + 2
                        continue
                    if not v.stored and not v.array and (lit := self.inline_const(m["image"], s, t, nxt - s)):
                        items.append(("const", s, lit))
                    elif v.udt_type in gl.types:
                        items.append(("dim", s, f"() As {gl.types[v.udt_type].name}"))
                    else:
                        items.append(("dim", s, ("()" if v.array else "") + f" As {TYPE_NAME[t]}"))
                    s += MOD_SIZE[t] if not v.array else max(2, nxt - s)
                    continue
                kind, g2 = self.value(m["image"], s, False), self.value(m["image"], s + 2, False)
                newobj = self.sym.objvar_types.get(m["seg"], {}) if m["seg"] else {}
                r = next((x for x in range(s + 2, s + 8, 2) if newobj.get(x) in self.sym.tables), None)
                if r is None and newobj.get(s) in self.sym.tables:
                    r = s
                if r is not None:  # `x() As New frmX`: (class ref,) record `0x80NN, global offset`
                    g2 = word(self.image, m["image"] + r + 2)
                    arr = r in m["vars"] and m["vars"][r].array
                    items.append(("newobj", r, newobj[r], g2, arr))
                    s = r + 6
                    continue
                prev_g = max((it[3] for it in items if it[0] == "global"), default=None)
                if v is None and (kind in P.CLASS_BY_KIND or kind in (1, 4)) and 6 <= g2 < self.globals_end \
                        and not any(gl.raw(g2, 4)) and (prev_g is None or kind <= prev_g or kind in (1, 4)):
                    cls = {1: "Form", 4: "Control"}.get(kind) or P.CLASS_BY_KIND[kind]
                    items.append(("global", s, None, g2, cls))  # `Global x As <class>`: kind, global offset
                    s += 4
                    continue
                if (v is not None and v.scope == "GLB") or (v is None and self.is_global_slot(m["image"], s)
                                                          and self.value(m["image"], s, False) not in
                                                          {x[3] for x in items if x[0] == "global"}):
                    g = self.value(m["image"], s, False)
                    items.append(("global", s, None, g))
                    s += 2
                    continue
                # unused: a constant (nonzero) or a variable filling the gap, 2 bytes
                # at a time so that a following declaration isn't swallowed
                val = self.image[m["image"] + s + 2:m["image"] + s + 4]
                if any(val):
                    items.append(("const", s, const_literal("I", val)))
                else:
                    items.append(("dim", s, "As Integer"))
                s += 2
            m["items"] = items
        # global declarations: sizes/types from the global image and the uses
        gs = sorted({it[3] for m in mods for it in m["items"] if it[0] == "global"})
        for m in mods:
            for k, it in enumerate(m["items"]):
                if it[0] != "global":
                    continue
                g = it[3]
                if len(it) == 5:  # object variable
                    m["items"][k] = ("global", it[1], None, g, it[4], f"G{g:X}", False)
                    self.global_name[g] = f"G{g:X}"
                    continue
                nxt = next((x for x in gs if x > g), self.globals_end)
                for a, b in gl.type_extent:
                    if g < a < nxt:
                        nxt = a
                u = uses.get(g, dict(votes={}, stored=False, array=False, mods=set()))
                size = nxt - g
                t = max(u["votes"], key=u["votes"].get) if u["votes"] else \
                    {2: "I", 4: "L", 8: "D"}.get(size, "V")
                name = f"G{g:X}"
                raw = gl.raw(g, 8)
                # a constant: never assigned, and other modules don't read it as a global
                # (they read a copy of a constant); zero values included
                lit = const_literal(t, raw) if not u["stored"] and not u["array"] and t in "ILSDC" \
                    and (any(raw[:MOD_SIZE[t]]) or not (u["mods"] - {id(m)})) else None
                if u.get("udt") in gl.types:
                    t, lit = gl.types[u["udt"]].name, None
                m["items"][k] = ("global", it[1], lit, g, t, name, u["array"])
                self.global_name[g] = name
        # Types: in the module whose globals surround them, else the first .bas
        bas = [m for m in mods if m["kind"] == "bas"] or mods
        for td in gl.types.values():
            owner = None
            for m in bas:
                gg = [it[3] for it in m["items"] if it[0] == "global"]
                if gg and min(gg) < td.g < max(gg):
                    owner = m
            owner = owner or next((m for m in bas if not m["items"]), bas[0])
            owner.setdefault("types", []).append(td)

    def inline_const(self, base: int, slot: int, t: str, room: int, zero: bool = False) -> str | None:
        n = MOD_SIZE.get(t, 0)
        raw = self.image[base + slot + 2:base + slot + 2 + n]
        return const_literal(t, raw) if n and n <= room and (zero or any(raw)) else None

    def declare_lines(self, m: dict) -> list[str]:
        out = []
        recs = [r for _, r in m["funcs"]]
        if m is self.decl_home:  # Declare Subs have no slot: found from their calls
            recs += sorted(r for r in self.call_types if r not in self.by_record and r not in self.slotted
                           and self.is_declare(r))
        for r in recs:
            if r in self.by_record:
                continue
            t = self.table
            dll = pool_name(self.image, self.pool, word(t, r + 40)).rstrip(".")
            fn = pool_name(self.image, self.pool, word(t, r + 46))
            kind = "Function" if t[r + 12] == 2 else "Sub"
            params = self.declare_params(r)
            line = f'Declare {kind} {fn} Lib "{dll}" ({", ".join(params)})'
            if kind == "Function":
                line += f" As {TYPE_NAME[RET_TYPE.get(t[r + 13], 'V')]}"
            out.append(line)
        return out

    def declare_params(self, r: int) -> list[str]:
        """DLL parameters from the argument types at call sites (arguments are
        converted to the declared type), else from the argument size."""
        seen = self.call_types.get(r, [])
        words = self.table[r + 15]
        out = []
        if seen:
            n = max(len(x) for x in seen)
            for j in range(n):
                ts = [x[j] for x in seen if j < len(x) and x[j]]
                t = ts[0] if ts else ""
                if t == "*" or (t.startswith("&") and t != "&T"):
                    out.append(f"P{j + 1} As Any")
                elif t in ("T", "&T"):
                    out.append(f"ByVal P{j + 1} As String")
                else:
                    t = {"L/T": "L", "": "I", "V": "I"}.get(t, t)
                    out.append(f"ByVal P{j + 1} As {TYPE_NAME.get(t, 'Integer')}")
            return out
        k = 0
        while words > 0:
            k += 1
            out.append(f"ByVal P{k} As {'Integer' if words == 1 else 'Long'}")
            words -= 1 if words == 1 else 2
        return out

    def emit_module(self, m: dict) -> list[str]:
        self.cur_base = m["image"]
        out = []
        types = {td.g: td for td in m.get("types", [])}
        pending = sorted(types)
        gtypes = self.gimg.types
        decl_lines = self.declare_lines(m)
        for it in m["items"]:
            if it[0] == "global":
                while pending and pending[0] < it[3]:
                    out += types[pending.pop(0)].lines(gtypes)
                _, s, lit, g, t, name, arr = it
                out.append(f"Global Const {name} = {lit}" if lit else
                           f"Global {name}" + ("()" if arr else "") + f" As {TYPE_NAME.get(t, t)}")
            elif it[0] == "typeref":
                continue
            elif it[0] == "newobj":
                _, r, form, g, arr = it
                glob = m["kind"] == "bas" and 6 <= g < self.globals_end
                out.append(f"{'Global' if glob else 'Dim'} {m['names'][r]}{'()' if arr else ''} As New {form}")
            elif it[0] == "const":
                out.append(f"Const {m['names'].get(it[1], f'K{it[1]:X}')} = {it[2]}")
            else:
                out.append(f"Dim {m['names'].get(it[1], f'm{it[1]:X}')}{it[2] if it[2][0] in '( ' else ' ' + it[2]}")
        for g in pending:
            out += types[g].lines(gtypes)
        out = decl_lines + out
        # declarations record (the word before the module's image + 4):
        # +18 flags (0x40 Option Explicit), +50 line count incl. comments
        rec = word(self.image, m["image"] - 2) + 4
        flags, count = word(self.table, rec + 18), word(self.table, rec + 50)
        if flags & 0x40:
            out.insert(0, "Option Explicit")
        out = ["'"] * max(0, count - len(out) - 1) + out + [""] if count else out
        self.cur_mod = m
        for info in m["infos"]:
            out += self.emit_proc(info, m["form"], m["vars"], m["names"], m["image"])
        return out

    def run(self) -> list[dict]:
        """Modules with their source lines (m['lines'])."""
        if hasattr(self, "_mods"):
            return self._mods
        mods = self.module_list()
        for m in mods:
            self.analyze_module(m)
        self.declarations(mods)
        self.all_mods = mods
        for m in mods:
            self.name_module(m)
        self.collect_calls(mods)
        self.slotted = {r for m in mods for _, r in m["funcs"]}
        self.decl_home = next((m for m in mods if m["kind"] == "bas"), mods[0])
        for m in mods:
            m["lines"] = self.emit_module(m)
        self._mods = mods
        return mods

    def name_module(self, m: dict) -> None:
        vars_, refs, infos = m["vars"], m["refs"], m["infos"]
        names: dict[int, str] = {}
        for it in m["items"]:
            if it[0] in ("dim", "const"):
                names[it[1]] = f"{'K' if it[0] == 'const' else 'm'}{it[1]:X}"
            elif it[0] == "newobj":
                names[it[1]] = f"G{it[3]:X}" if m["kind"] == "bas" else f"m{it[1]:X}"
                self.global_name[it[3]] = names[it[1]]
        gconst = {}  # (type, literal) -> Global Const name
        for mm in self.all_mods:
            for it in mm["items"]:
                if it[0] == "global" and it[2]:
                    gconst.setdefault((it[4], it[2]), it[5])
        for s, v in vars_.items():
            if s in names:
                continue
            lit, gname = None, None
            if v.scope == "MOD" and s > m["first_owned"] and not v.stored and not v.array:
                for t in ([v.type()] if v.votes else []) + ["L", "I"]:  # untyped: shared Long/String handler
                    lit = self.inline_const(m["image"], s, t, 16, zero=True)
                    if lit and (t, lit) in gconst:
                        gname = gconst[(t, lit)]
                        break
                if not gname:
                    lit = self.inline_const(m["image"], s, v.type(), 16)
            if gname and (m["kind"] == "frm" or len(v.procs) > 1):
                # a Global Const used here: a slot with a copy of its value, at first use
                names[s] = gname
            elif lit and len(v.procs) > 1:
                names[s] = f"K{s:X}"
            elif lit:
                names[s] = f"K{s:X}"  # Const inside a procedure
            elif v.scope == "GLB":
                names[s] = self.global_name.get(self.value(m["image"], s, False), f"g{s:X}")
            elif s not in names:
                pre = "s" if v.scope == "MOD" else "p" if v.scope == "REF" else "v"
                names[s] = f"{pre}{s:X}"
        for s, (_, n) in refs.items():
            names[s] = n
        for info in infos:
            ev = self.events.get(info.proc.record)
            if ev:
                info.name, info.event = ev, True
        self.fit_names(m)
        base = m["image"]
        owned_all = {s for s, v in vars_.items() if v.scope in ("LOC", "REF")} | {r - 2 for r in refs}
        is_param = lambda s: self.value(base, s) >= 6 and self.value(base, s) % 2 == 0  # noqa: E731
        for k, info in enumerate(infos):
            mine = sorted(s for s, v in vars_.items() if v.procs and v.procs[0] == k and v.scope in ("LOC", "REF"))
            # parameters: even BP offsets >= 6 (String locals are numbered 1, 3, ...),
            # consecutive after the return value; unused ones are found by walking the slots
            ps = [s for s in mine if is_param(s)]
            if ps or info.argwords:
                lo = min(ps) if ps else (min(mine) if mine else None)
                if lo is not None:
                    while lo - 2 >= m["first_owned"] and lo - 2 not in owned_all and is_param(lo - 2):
                        lo -= 2
                    hi = max(ps) if ps else lo - 2
                    s2 = hi + 2
                    while s2 not in owned_all and is_param(s2) and self.value(base, s2) < self.value(base, hi):
                        hi, s2 = s2, s2 + 2
                    ps = [x for x in range(lo, hi + 2, 2) if is_param(x)]
                    for x in ps:
                        vars_.setdefault(x, Var(x, "REF", procs=[k]))
                        names.setdefault(x, f"p{x:X}")
            if info.function:
                first = min(ps) if ps else (mine[0] if mine else None)
                cand = [x for x in mine if x < first] if first is not None and ps else mine[:1]
                r = (first - 2) if ps and not cand else (cand[0] if cand else None)
                if r is not None:
                    info.ret_slot = r
                    names[r] = info.name
            info.params = ps
        for info in infos:
            self.proc_name[info.proc.record] = info.name
        for _, r in m["funcs"]:
            if r not in self.by_record:
                self.proc_name[r] = pool_name(self.image, self.pool, word(self.table, r + 46))
        for p in self.procs:  # calls to Declare Subs (operand: the record)
            for i in P.decode(self.rt, self.segs[p.segment - 1].data, p)[0] if p.segment == m["seg"] else []:
                if NAMES.get(i.op) == "CALL" and len(i.operand) >= 4:
                    r = struct.unpack_from("<H", i.operand, 2)[0] & 0xFFF8
                    if r not in self.by_record and self.is_declare(r):
                        self.proc_name[r] = pool_name(self.image, self.pool, word(self.table, r + 46))
        m["names"] = names

    def collect_calls(self, mods: list[dict]) -> None:
        """Argument types per called record (for Declare parameters)."""
        self.call_types: dict[int, list] = {}
        for m in mods:
            self.cur_base = m["image"]
            for info in m["infos"]:
                calls = []
                self.statements(info, m["names"], calls)
                for name, operand, types in calls:
                    (rec,) = struct.unpack_from("<H", operand, 2)
                    if name == "CALL_FN":
                        rec = self.value(m["image"], rec, False)
                    self.call_types.setdefault(rec & 0xFFF8, []).append(types)

    def fit_names(self, m: dict) -> None:
        """Names for general procedures (not stored) that keep both orders
        the compiler derives from names: code layout (procedures sorted by
        name, case-insensitive) and Function/Declare slots (sorted too).
        Each run of unnamed procedures gets one prefix and counters."""
        infos = m["infos"]
        slot_order = [r for _, r in m["funcs"]]
        fixed = {r: pool_name(self.image, self.pool, word(self.table, r + 46))
                 for r in slot_order if r not in self.by_record}

        def bounds(k: int) -> tuple[str, str | None]:
            info = infos[k]
            los = [x.name for x in infos[:k] if x.name]
            his = [x.name for x in infos[k + 1:] if x.event]
            r = info.proc.record
            if r in slot_order:
                j = slot_order.index(r)
                los += [fixed.get(x) or self.proc_name.get(x, "") for x in slot_order[:j]]
                his += [fixed[x] for x in slot_order[j + 1:] if x in fixed]
            return max(los, key=str.lower, default=""), min(his, key=str.lower, default=None)

        taken = {n.lower() for n in self.proc_name.values()} | {x.name.lower() for x in infos if x.name}

        def fits(c: str, lo: str, hi: str | None) -> bool:
            return c.lower() > lo.lower() and (hi is None or c.lower() < hi.lower()) and c.lower() not in taken

        k = 0
        while k < len(infos):
            if infos[k].name:
                k += 1
                continue
            run = [k]
            while run[-1] + 1 < len(infos) and not infos[run[-1] + 1].name:
                run.append(run[-1] + 1)
            base = next((x.name for x in reversed(infos[:k]) if x.name), "")
            for prefix in ("Proc", "Sub", "Proc_", "Sub_", "ProcX", f"{base}_" if base else "A", f"{base}X"):
                names = [f"{prefix}{n + 1:02d}" for n in range(len(run))]
                ok = True
                for kk, c in zip(run, names):
                    infos[kk].name = c
                    if not fits(c, *bounds(kk)):
                        ok = False
                for kk in run:
                    infos[kk].name = "" if not ok else infos[kk].name
                if ok:
                    break
            for kk in run:  # fallback: fit one by one
                n = 0
                while not infos[kk].name:
                    n += 1
                    lo, hi = bounds(kk)
                    for c in (f"{lo}_{n:02d}", f"{lo}X{n:02d}", f"{lo}{n}"):
                        if fits(c, lo, hi):
                            infos[kk].name = c
                            break
                    if n > 999:
                        infos[kk].name = f"Proc{infos[kk].proc.record:X}"
            for kk in run:
                self.proc_name[infos[kk].proc.record] = infos[kk].name
                taken.add(infos[kk].name.lower())
            k = run[-1] + 1

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
            # sizes from the BP offsets: ByRef 4 (far pointer), ByVal by type
            offs = sorted(self.value(base, s) for s in info.params)
            top = 6 + 2 * info.argwords
            psize = {o: (offs[j + 1] if j + 1 < len(offs) else top) - o for j, o in enumerate(offs)}
            for s in info.params:
                v = vars_[s]
                if v.obj:
                    params.append(f"{names[s]} As {v.obj}")
                    continue
                size = psize.get(self.value(base, s), 4)
                t = v.type() if v.votes else {2: "I", 8: "D", 16: "V"}.get(size, "V")
                byval = size != 4 or (v.scope == "LOC" and t in "LS" and t != "")
                if size == 2:
                    t = "I"
                pn = names[s]
                params.append(("ByVal " if byval else "") + pn + ("()" if v.array else "") + f" As {TYPE_NAME[t]}")
        head = f"{kind} {info.name} ({', '.join(params)})"
        if info.function:
            head += f" As {TYPE_NAME[info.ret]}"
        body = self.statements(info, names)
        dims = self.local_dims(info, vars_, names, base, body)
        # record +50: the procedure's line count, including the comment block
        # above it (comments aren't compiled): pad with empty comments
        (count,) = struct.unpack_from("<H", self.table, info.proc.record + 50)
        ndims = sum(len(x) for x in dims.values())
        lines = ["'"] * max(0, count - len(body) - ndims - 2) + [head]
        for k, (col, text) in enumerate(body + [(0, None)]):
            lines += ["    " + d for d in dims.get(k, [])]
            if text is None:
                break
            if info.function:
                text = re.sub(r"\bExit Sub\b", "Exit Function", text)
            lines.append(" " * col + text)
        lines += [f"End {kind}", ""]
        return lines

    def local_dims(self, info: ProcInfo, vars_: dict, names: dict, base: int, body: list) -> dict[int, list[str]]:
        """Dim/Static lines for the procedure's locals, placed so that slots are
        allocated in the original order: slots follow text order, and a
        control/global reference or an undeclared variable gets its slot at
        its first appearance. Returns statement index -> lines before it."""
        m = self.cur_mod
        k = m["infos"].index(info)
        skip = set(info.params) | ({info.ret_slot} if info.ret_slot is not None else set())
        items = []  # (slot, kind, name, decl)
        frame = sorted((s for s, v in vars_.items() if v.procs and v.procs[0] == k and v.scope in ("LOC", "REF")
                        and s not in skip), key=lambda s: s)
        # frame sizes: BP offsets decrease in slot order from -22 (ret value first)
        offs = [(s, self.value(base, s)) for s in sorted(set(frame) | skip) if self.value(base, s) < 0]
        size, prev = {}, -22
        for s, o in offs:
            size[s], prev = prev - o, o
        objtypes = self.sym.objvar_types.get(m["seg"], {}) if m["seg"] else {}
        for s in frame:
            v = vars_[s]
            if objtypes.get(s) or v.obj:
                t = objtypes.get(s) or v.obj
                decl = ("()" if v.array else "") + (f" As New {t}" if t in self.sym.tables else f" As {t}")
            elif v.udt:
                td = self.gimg.by_size(size.get(s, 0))
                decl = f"As {td.name}" if td else "As Variant"
            else:
                t = v.type() if v.votes else ("T" if self.value(base, s) % 2 == 1 else
                                              {2: "I", 4: "L", 8: "D"}.get(size.get(s), "V"))
                decl = ("()" if v.array else "") + f" As {TYPE_NAME[t]}"
            items.append((s, "dim", names[s], decl, v))
        for s, v in vars_.items():
            if v.scope == "MOD" and v.procs and v.procs[0] == k and s > m["first_owned"] \
                    and (v.procs == [k] or not names[s].startswith(("s", "K"))):
                lit = None if v.stored or v.array else self.inline_const(base, s, v.type(), 16)
                if not names[s].startswith(("K", "s")):
                    items.append((s, "fixed", names[s], None, v))  # a Global Const's copy
                elif lit:  # a Const inside the procedure: stored inline like a module one
                    items.append((s, "const", names[s], f"= {lit}", v))
                else:
                    items.append((s, "static", names[s], ("()" if v.array else "") + f" As {TYPE_NAME[v.type()]}", v))
            elif v.scope == "GLB" and v.procs and v.procs[0] == k and s > m["first_owned"]:
                items.append((s, "fixed", names[s], None, v))
        for s, (kk, n) in m["refs"].items():
            if kk == k:
                items.append((s, "fixed", n, None, None))
        items.sort(key=lambda it: (it[0], it[1]))

        texts = [re.sub(r'"[^"]*"', lambda x: " " * len(x.group(0)), t or "") for _, t in body]

        def appear(name: str):
            base_name = re.escape(name.rstrip("%&!#@$"))
            pat = re.compile(rf"(?<![\w.!]){base_name}(?![\w])", re.I)
            for i, t in enumerate(texts):
                mt = pat.search(t)
                if mt:
                    return (i, mt.start(), t[mt.end():mt.end() + 1] in "%&!#@$" and t[mt.end():mt.end() + 1] != "")
            return None

        keys = {it[0]: appear(it[2]) for it in items}
        dims: dict[int, list[str]] = {}
        last = (-1, 0)
        for j, (s, kind, name, decl, v) in enumerate(items):
            key = keys[s]
            if kind == "fixed":
                if key is not None and key[:2] > last:
                    last = key[:2]
                continue
            # declare at the earliest point after the previous item; if that is
            # past the first use (same statement as a preceding control
            # reference), the variable was declared implicitly there
            pos = 0 if last == (-1, 0) else (last[0] if last[1] < 0 else last[0] + 1)
            if key is not None and key[0] < pos and kind == "dim" and not v.array and not v.udt \
                    and key[:2] > last and not m["explicit"]:
                last = key[:2]
                continue
            if key is not None and key[0] < pos:
                pos = key[0]  # conflicting order: at least keep it compilable
            word_ = {"static": "Static", "const": "Const"}.get(kind, "Dim")
            dims.setdefault(pos, []).append(f"{word_} {name}{decl if decl[0] in '( ' else ' ' + decl}")
            last = (pos, -1)
        return dims

    def statements(self, info: ProcInfo, names: dict, calls: list | None = None) -> list[tuple[int, str]]:
        """(indentation column, lifted text) per statement (marker to the
        next marker), with label lines."""
        out, cur, curn = [], [], []
        col = [0]

        def flush():
            if cur and not all(NAMES.get(op, "") in ("RET", "TRAP", "OBJ_FREE") for op, _ in cur):
                out.append((col[0], lift(cur, self.ids, names=curn, calls=calls)))
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
        if n.startswith(("FIELD_GET", "FIELD_SET", "FIELD_ADDR")) and slot is not None:
            return f"F{slot:X}"  # field record offset, as in the Type declaration
        if n == "TYPEOF_IS" and slot is not None:
            nforms = self.forms
            base = 0x46 + len(P.vbx_entries(self.res.get(1, b"")))
            if 0 <= slot - base < len(nforms):
                return nforms[slot - base][0]
            return {1: "Form", 4: "Control"}.get(slot) or P.CLASS_BY_KIND.get(slot)
        if n == "OLE_CALL":
            return note.rpartition(".")[2] or None
        if n in ("CALL", "CALL_FN"):
            (rec,) = struct.unpack_from("<H", i.operand, 2)
            if n == "CALL_FN" and self.cur_base is not None:  # operand: the function's slot
                rec = self.value(self.cur_base, rec)
            return self.proc_name.get(rec & 0xFFF8)
        if slot is not None and slot in names:
            return names[slot] + plain_handler(self.rt, i.op)[1]
        return note or None


def write_project(d: Decompiler, out: Path, layout_from: Path | None, name: str) -> Path:
    """Writes the module files and a .mak named after the executable; returns the .mak."""
    out.mkdir(parents=True, exist_ok=True)
    mods = d.run()
    code = {m["form"]: m["lines"] for m in mods if m["form"]}
    modules = [m["lines"] for m in mods if not m["form"]]
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
    files += [v for v in P.vbx_entries(r1) if v.upper().endswith(".VBX")]  # custom controls
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
