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
import pcode_disasm as P
import formblob as FB  # noqa: E402
from lift import lift  # noqa: E402
from opcodes import NAMES  # noqa: E402
from vbdecl import MOD_SIZE, GlobalImage, const_literal, word  # noqa: E402

SUFFIX = {"I": "%", "L": "&", "S": "!", "D": "#", "C": "@", "T": "$", "V": ""}
class _TypeNames(dict):
    def __missing__(self, t: str) -> str:  # "F<n>": fixed-length String
        if t.startswith("F") and t[1:].isdigit():
            return f"String * {t[1:]}"
        raise KeyError(t)


TYPE_NAME = _TypeNames({"I": "Integer", "L": "Long", "S": "Single", "D": "Double", "C": "Currency", "T": "String",
                        "V": "Variant"})
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
OBJ_KINDS = {1: "Form", 2: "MDIForm", 4: "Control", 0x14: "Object"}  # object variable kinds besides control classes
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
    fixed: bool = False  # String * n (length in the slot before)
    obj: str | None = None  # object variable's class (As Control, As frmX, ...)
    glob: int | None = None  # global offset, for a reference to a global object array
    copy_type: str | None = None  # a Global Const's copy: its type (its size in the slots)

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
    callees: list = field(default_factory=list)  # called records, in text order


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
    if oid is None:
        return None, ""
    if oid >> 10 in SUFFIX_OF_ID:
        for k in sorted(range(-16, 17), key=abs):
            n = NAMES.get(op + k)
            if n and n.split(".")[0] in VAR_FAMILIES and rt.opcode_id(op + k) == oid & 0x3FF:
                return n, SUFFIX_OF_ID[oid >> 10]
    fam = ID_CLASS.get(oid & 0xFF)  # otherwise by the ID's class; scope from the slot (".X")
    return (f"{fam}.X", SUFFIX_OF_ID.get(oid >> 10, "")) if fam else (None, "")


ID_CLASS = {0x0B: "LOAD", 0x0C: "STORE", 0x0E: "ALOAD", 0x0F: "ASTORE"}  # interpreter ID low byte


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


NAME_CHARS = "0123456789abcdefghijklmnopqrstuvwxyz"  # ASCII order, case-folded
RESERVED = None


def mod_name(slot: int) -> str:
    """Synthetic module variable name; `mE` would be the keyword Me."""
    return f"m{slot:X}" if slot != 0xE else "m0E"


def name_between(lo: str, hi: str | None, n: int, taken: set[str]) -> str | None:
    """The smallest name of exactly n characters with lo < name < hi
    (case-insensitive), not taken, not a keyword/builtin and not shaped
    like the decompiler's synthetic variable names (letter + hex)."""
    global RESERVED
    if RESERVED is None:
        import namesize
        RESERVED = namesize.KEYWORDS | namesize.BUILTINS
    lo = lo.lower()
    base = "".join(c for c in lo if c in NAME_CHARS or c == "_")  # `_` only kept from lo: its order vs letters is unknown
    if len(base) < n:
        cur = list(base + "0" * (n - len(base)))  # the smallest longer name with lo as prefix
    else:
        cur = _next_name(list(base[:n]))
    if cur and not cur[0].isalpha():
        cur = ["a"] + ["0"] * (n - 1)
    for _ in range(200000):
        if cur is None:
            return None
        c = "".join(cur)
        if hi is not None and c >= hi.lower():
            return None
        if (c[0].isalpha() and c > lo and c not in taken and c not in RESERVED
                and not re.fullmatch(r"[a-z][0-9a-f]+", c)):
            return c[0].upper() + c[1:]
        cur = _next_name(cur)
    return None


def _next_name(cur: list[str]) -> list[str] | None:
    cur = cur[:]
    k = len(cur) - 1
    while k >= 0:
        if cur[k] == "_":  # (from lo) next in ASCII order: `a`
            cur[k] = "a"
            return cur
        i = NAME_CHARS.index(cur[k])
        if i + 1 < len(NAME_CHARS):
            cur[k] = NAME_CHARS[i + 1]
            if k == 0 and not cur[0].isalpha():
                cur[0] = "a"
            return cur
        cur[k] = NAME_CHARS[0]
        k -= 1
    return None


def pool_name(image: bytes, pool: int, off: int) -> str:
    p = pool + 2 + off
    return image[p + 4:p + 4 + image[p + 3]].decode("latin-1")


# Statement markers encode the line's indentation (the IDE regenerates the
# text from p-code): entry point -> column, from compiling lines at columns
# 0..40; 48AF takes the column as a u16 operand (25 and up); 4958 is a
# statement after `:` on the same line.
STMT_COLUMN = {op: c for c, op in enumerate([
    0x494B, 0x4948, 0x4945, 0x4942, 0x4935, 0x4932, 0x492F, 0x492C, 0x491F, 0x491C, 0x4919, 0x4916,
    0x4906, 0x4903, 0x4900, 0x48FD, 0x48F0, 0x48EA, 0x48E7, 0x48E4, 0x48D7, 0x48D4, 0x48D1, 0x48CE,
    0x48ED])}
STMT_WIDE, STMT_SAME_LINE = 0x48AF, 0x4958


def stmt_column(rt: P.Runtime, op: int, operand: bytes = b"") -> int | None:
    if op == STMT_WIDE and len(operand) >= 2:
        return struct.unpack_from("<H", operand)[0]
    return STMT_COLUMN.get(op)


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
        self.exe = exe
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
        for m in mods:  # declarations record: word before the image + 4 (+18 flags: 0x40 Option Explicit;
            rec = word(self.image, m["image"] - 2) + 4  # +44: DefType table, 0xFFFF if none)
            m["explicit"] = bool(word(self.table, rec + 18) & 0x40)
            m["tabs"] = bool(word(self.table, rec + 18) & 0x8000)  # the code contains tab characters
            m["defint"] = word(self.table, rec + 44) != 0xFFFF  # the samples' only DefType: DefInt A-Z
        decl_recs = sorted(word(self.image, m["image"] - 2) + 4 for m in mods)

        def owner(r: int) -> int:  # a module's records follow its declarations record
            return max((r0 for r0 in decl_recs if r0 < r), default=-1)

        for m in mods:  # Function/Declare slots: record offsets (sorted by name); a slot
            m["funcs"], s = [], m["start"]  # holding another module's procedure is a call's
            me = word(self.image, m["image"] - 2) + 4
            while self.is_record(r := self.value(m["image"], s, False)) and \
                    (r not in self.by_record or owner(r) == me):
                m["funcs"].append((s, self.value(m["image"], s, False)))
                s += 2
            m["decl_start"] = s
        segs = sorted({p.segment for p in self.procs})
        free = [m for m in mods if m["kind"] == "bas"]
        # a module's procedure records follow its declarations record in the table
        starts = sorted((word(self.image, m["image"] - 2) + 4, k) for k, m in enumerate(mods))
        for seg in segs:
            recs = {p.record for p in self.procs if p.segment == seg}
            own = {max((k for r0, k in starts if r0 < r), default=None) for r in recs}
            if len(own) == 1 and None not in own and mods[k := own.pop()]["seg"] is None:
                mods[k]["seg"] = seg
                P.SEG_IMAGE[seg] = mods[k]["image"]
                if mods[k] in free:
                    free.remove(mods[k])
                continue
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
                if (n is None or n.endswith(".X")) and P.is_objarr(self.rt, i):
                    n = "ALOAD.MOD.V"  # object array element (typed separately)
                    x = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                    if word(self.image, base + x) >> 8 == 0x80:  # `0x80NN, global offset`: a global As New array
                        vars_.setdefault(x, Var(x, "MOD")).glob = word(self.image, base + x + 2)
                if n in ("LOAD.UDT", "LOAD.UDT_LOC", "AUDT", "STORE.UDT") and i.operand:
                    last_udt = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                    scope = "LOC" if n.endswith("LOC") or (n == "STORE.UDT" and self.value(base, last_udt) < 0) \
                        else "MOD"
                    v = vars_.setdefault(last_udt, Var(last_udt, scope))
                    v.udt = True
                    v.array |= n == "AUDT"
                    if k not in v.procs:
                        v.procs.append(k)
                    continue
                if n and n.startswith(("FIELD_", )) and last_udt is not None and i.operand:
                    td = self.gimg.field_type.get(struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0])
                    if td:
                        udt[last_udt] = td.g
                        if vars_[last_udt].array:
                            vars_[last_udt].udt_type = td.g
                    last_udt = None
                if n == "ARRAY_REF" and i.operand:  # a whole array (LBound, Erase, argument `a()`)
                    slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                    if slot not in vars_ and (self.is_global_slot(base, slot) or slot in {x for x, _ in m["funcs"]}):
                        continue  # a global array (through this module's slot) or a function slot
                    bp = self.value(base, slot)  # a descriptor (module/Static) or a BP offset
                    v = vars_.setdefault(slot, Var(slot, "LOC" if -0x1000 < bp < 0 else "REF" if 0 < bp < 0x100 else "MOD"))
                    v.array = True
                    if k not in v.procs:
                        v.procs.append(k)
                    continue
                if n == "OBJVAR" and i.operand:  # object variable: record `kind, BP offset / 0`
                    slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                    bp = self.value(base, slot)
                    if bp != 0:  # parameter or local (module-level ones are declarations)
                        v = vars_.setdefault(slot, Var(slot, "LOC"))
                        kind = word(self.image, base + slot)  # record: kind, BP offset
                        v.obj = self.sym.objvar_types.get(seg, {}).get(slot) or OBJ_KINDS.get(kind) \
                            or P.CLASS_BY_KIND.get(kind) or "Control"
                        if k not in v.procs:
                            v.procs.append(k)
                    continue
                acc = var_access(n or "")
                if not acc or not i.operand:
                    continue
                scope, t, arr = acc
                slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                if scope == "X":  # the slot holds 0 (module) or a BP offset < 0 (local); else unsure: skip
                    bp = self.value(base, slot)
                    if bp > 0:
                        continue
                    scope = "MOD" if bp == 0 else "LOC"
                v = vars_.setdefault(slot, Var(slot, scope))
                if scope == "REF":
                    v.scope = "REF"
                if n.startswith(("STORE", "ASTORE", "ADDR", "AADDR")):
                    v.stored = True
                if n.startswith("ADDR") and not v.obj and j + 1 < len(info.insns) \
                        and NAMES.get(info.insns[j + 1].op) == "SET_OBJ":  # `Set x = ...`: an object variable
                    kind = word(self.image, base + slot)  # its record: kind, BP offset
                    v.obj = self.sym.objvar_types.get(seg, {}).get(slot) or OBJ_KINDS.get(kind) \
                        or P.CLASS_BY_KIND.get(kind) or "Object"
                v.array |= arr
                if arr and j + 1 < len(info.insns):  # array of a Type: `a(i).field`
                    nn = NAMES.get(info.insns[j + 1].op, "")
                    if nn.startswith("FIELD_") and info.insns[j + 1].operand:
                        td = self.gimg.field_type.get(struct.unpack_from("<H", info.insns[j + 1].operand)[0])
                        if td:
                            v.udt_type = td.g
                if k not in v.procs:
                    v.procs.append(k)
                if t == "F":  # fixed-length String: its length is in the slot before
                    t = f"F{self.value(base, slot - 2, False)}"
                    v.fixed = True
                elif sfx:
                    t = TYPE_OF_SUFFIX[sfx]
                elif t == "L/T":
                    nxt = NAMES.get(info.insns[j + 1].op, "") if j + 1 < len(info.insns) else ""
                    t = lt_hint(nxt)
                if t in SUFFIX or t.startswith("F"):
                    v.votes[t] = v.votes.get(t, 0) + 1

        # object variables' classes (from their records) name their properties: annotate again
        typed = {x: v.obj for x, v in vars_.items() if v.obj}
        if seg and any(self.sym.objvar_types.get(seg, {}).get(x) != c for x, c in typed.items()):
            known = self.sym.objvar_types.setdefault(seg, {})
            for x, c in typed.items():
                known.setdefault(x, c)
            for info in infos:
                info.notes = self.sym.annotate(seg, info.insns)

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
        # other slots procedures allocate at first use: calls to functions of other
        # modules, object variables, and (in forms) references to globals
        func_slots = {x for x, _ in m["funcs"]}
        call_slots: dict[int, int] = {}  # slot -> first procedure (calls into other modules)
        for k, info in enumerate(infos):
            for i in info.insns:
                n = plain_handler(self.rt, i.op)[0] or ""  # suffixed accesses resolve to their plain handler
                if n == "CALL_FN" and len(i.operand) >= 4:
                    x = struct.unpack_from("<H", i.operand, 2)[0]
                    if x not in func_slots:
                        owned.add(x)
                        call_slots.setdefault(x, k)
                elif m["kind"] == "frm" and i.operand and (n in ("OBJVAR", "FORM", "CONTROL", "CTLARRAY")
                                                          or ((not n or n.endswith(".X")) and P.is_objarr(self.rt, i))):
                    # records start at the operand; a form declares no global objects
                    owned.add(struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0] - 2)
        if m["kind"] == "frm":
            owned |= {s for s, v in vars_.items() if v.scope == "GLB"}
        # control/property operands point at their record, 2 bytes past a variable's slot
        owned |= {s - 2 for s, v in vars_.items() if getattr(v, "fixed", False) and v.scope != "MOD"}
        first_owned = min(owned | {r - 2 for r in refs} | {s for s, v in vars_.items() if v.scope == "GLB"
                                               and s >= m["decl_start"] and not self.is_global_slot(base, s)},
                          default=word(self.image, base) - 1 if not infos else 1 << 16)
        # unused leading parameters also hold 0: the first procedure's parameters
        # start before its first used one (ByRef: 4 argument bytes, 2 slot bytes each)
        for k, info in enumerate(infos):
            ps = [(x, self.value(base, x)) for x, v in vars_.items() if v.procs and v.procs[0] == k
                  and v.scope in ("LOC", "REF") and 6 <= self.value(base, x) < 6 + 2 * info.argwords
                  and self.value(base, x) % 2 == 0]
            if ps:
                x, bp = min(ps)
                j = (6 + 2 * info.argwords - bp - 4) // 4  # index among ByRef parameters
                if j > 0 and info.proc.record in self.events:
                    first_owned = min(first_owned, x - 2 * j)
                break
            if any(v.procs and v.procs[0] == k for v in vars_.values()) or any(kk == k for kk, _ in refs.values()):
                break
        first_owned = min(first_owned, word(self.image, base) - 1)  # nothing owned: the image's end
        m.update(infos=infos, vars=vars_, refs=refs, udt=udt, first_owned=first_owned, call_slots=call_slots)

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
                if v is None and getattr(m["vars"].get(s + 2), "fixed", False):  # a String * n's length
                    s += 2
                    continue
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
                    if s in m["udt"] and v.array:  # array of a Type
                        dims, size = self.array_dims(m["image"], s)
                        td = gl.types.get(v.udt_type or m["udt"][s])
                        items.append(("dim", s, f"({dims}) As {td.name if td else 'Variant'}"))
                        s += size
                        continue
                    if s in m["udt"]:
                        td = gl.types.get(m["udt"][s])
                        items.append(("dim", s, f"As {td.name}" if td else "As Variant"))
                        step = (td.size + 1) // 2 * 2 if td else 16
                        s = nxt if step <= nxt - s <= step + 4 else s + step + 2
                        continue
                    w1 = word(self.image, m["image"] + s + 4)
                    if not v.stored and not v.array and not v.votes.keys() - {"T", "L"} and nxt - s == 4 \
                            and w1 >= 0x100:  # a String constant: descriptor, text assigned below
                        items.append(("const", s, "\0str"))
                        s += 4
                        continue
                    if not v.stored and not v.array and (lit := self.inline_const(m["image"], s, t, nxt - s)):
                        items.append(("const", s, lit))
                    elif v.array:
                        dims, size = self.array_dims(m["image"], s)
                        tn = gl.types[v.udt_type].name if v.udt_type in gl.types else TYPE_NAME[t]
                        items.append(("dim", s, f"({dims}) As {tn}"))
                        s += size
                        continue
                    else:
                        items.append(("dim", s, f" As {TYPE_NAME[t]}"))
                    s += MOD_SIZE[t] if t in MOD_SIZE else max(nxt - s, 2)
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
                fbase = 0x46 + len(P.vbx_entries(self.res.get(1, b"")))  # form numbers (object kind of `As frmX`)
                formk = {fbase + j: f[0] for j, f in enumerate(self.forms)}
                if v is None and (kind in P.CLASS_BY_KIND or kind in OBJ_KINDS or kind in formk) \
                        and 6 <= g2 < self.globals_end and not any(gl.raw(g2, 4)) \
                        and (prev_g is None or kind <= prev_g or kind in OBJ_KINDS or kind in formk):
                    cls = OBJ_KINDS.get(kind) or formk.get(kind) or P.CLASS_BY_KIND[kind]
                    items.append(("global", s, None, g2, cls))  # `Global x As <class>`: kind, global offset
                    s += 4
                    continue
                if m["kind"] == "bas" and (v is not None and v.scope == "GLB") or (
                        m["kind"] == "bas" and v is None and self.is_global_slot(m["image"], s)
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
                    items.append(("dim", s, "As Integer", "filler"))
                s += 2
            if all(len(it) == 4 and it[3] == "filler" for it in items):
                items = []  # only zeros before the procedures: not declarations
                fv = m["vars"].get(m["first_owned"])
                if items is not None and fv is not None and fv.scope in ("LOC", "REF"):
                    m["first_owned"] = m["decl_start"]  # leading unused locals of the first procedure
            m["items"] = items
        # global declarations: sizes/types from the global image and the uses
        gs = sorted({it[3] for m in mods for it in m["items"] if it[0] == "global"})
        # String constants: a descriptor in the global image; the texts are records
        # `u16 size, u16 length, text, 0` in RT_RCDATA 2 before the global image,
        # in declaration order
        head = self.image[:gl.base or 0]
        texts = [x.group(3).decode("latin-1") for x in re.finditer(rb"(?s)(..)(..)([\x20-\x7e]+)\x00", head)
                 if struct.unpack("<H", x.group(2))[0] == len(x.group(3))]
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
                w0, w1 = gl.w(g), gl.w(g + 2)
                if size == 4 and not u["stored"] and w1 >= 0x100 and set(u["votes"]) <= {"T", "L"}:
                    t, lit = "T", "\0str"  # string descriptor, text assigned below
                m["items"][k] = ("global", it[1], lit, g, t, name, u["array"])
                self.global_name[g] = name
        pending = [(mi, k) for mi, m in enumerate(mods) for k, it in enumerate(m["items"])
                   if it[0] in ("const", "global") and len(it) > 2 and it[2] == "\0str"]
        for (mi, k), text in zip(pending, texts):
            it = mods[mi]["items"][k]
            mods[mi]["items"][k] = it[:2] + ('"' + text + '"',) + it[3:]
        for mi, k in pending[len(texts):]:  # no text found: keep a placeholder string
            it = mods[mi]["items"][k]
            mods[mi]["items"][k] = it[:2] + ('""',) + it[3:]
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

    def array_dims(self, base: int, slot: int, absolute: int | None = None) -> tuple[str, int]:
        """An array's descriptor (inline, from slot + 2): word +2 is 0x4000 |
        dimensions for a fixed-size array, whose last words are (count, lower
        bound) per dimension, last dimension first; size 18 + 4 per dimension.
        A dynamic array reserves 8 dimensions (50 bytes)."""
        d = base + slot + 2 if absolute is None else absolute
        w = word(self.image, d + 2)
        if not w & 0x4000:
            return "", 50
        n = w & 0xFF
        pairs = [(word(self.image, d + 18 + 4 * j), word(self.image, d + 20 + 4 * j, True)) for j in range(n)]
        dims = [f"{lo + cnt - 1}" if lo == 0 else f"{lo} To {lo + cnt - 1}" for cnt, lo in reversed(pairs)]
        return ", ".join(dims), 18 + 4 * n

    def inline_const(self, base: int, slot: int, t: str, room: int, zero: bool = False) -> str | None:
        n = MOD_SIZE.get(t, 0)
        raw = self.image[base + slot + 2:base + slot + 2 + n]
        return const_literal(t, raw) if n and n <= room and (zero or any(raw)) else None

    def declare_lines(self, m: dict) -> list[str]:
        out = []
        recs = [r for _, r in m["funcs"]]
        # Declare Subs (and unused Declares) have no slot: found by scanning the
        # table; declared in the module whose records surround them
        for r in sorted(r for r in range(0, len(self.table) - 55, 8) if r not in self.by_record
                        and r not in self.slotted and self.is_declare(r)):
            if self.declare_home(r) is m:
                recs.append(r)
        recs.sort()  # records are allocated in order of first mention in the text
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

    def declare_home(self, r: int) -> dict:
        """The module a slotless Declare is written in: a module's records
        (procedures, Declares) follow its declarations record in the table."""
        starts = [(word(self.image, m["image"] - 2) + 4, k) for k, m in enumerate(self.all_mods)]
        k = max(((r0, k) for r0, k in starts if r0 < r), default=None)
        return self.all_mods[k[1]] if k else self.decl_home

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
                dims = self.array_dims(0, 0, self.gimg.base + 2 + g)[0] if arr else ""
                out.append(f"Global Const {name} = {lit}" if lit else
                           f"Global {name}" + (f"({dims})" if arr else "") + f" As {TYPE_NAME.get(t, t)}")
            elif it[0] == "typeref":
                continue
            elif it[0] == "newobj":
                _, r, form, g, arr = it
                glob = m["kind"] == "bas" and 6 <= g < self.globals_end
                out.append(f"{'Global' if glob else 'Dim'} {m['names'][r]}{'()' if arr else ''} As New {form}")
            elif it[0] == "const":
                out.append(f"Const {m['names'].get(it[1], f'K{it[1]:X}')} = {it[2]}")
            else:
                out.append(f"Dim {m['names'].get(it[1], mod_name(it[1]))}{it[2] if it[2][0] in '( ' else ' ' + it[2]}")
        for g in pending:
            out += types[g].lines(gtypes)
        out = decl_lines + out
        # declarations record (the word before the module's image + 4):
        # +18 flags (0x40 Option Explicit, 0x800 Option Compare Text, 0x8000 tabs), +50 line count
        rec = word(self.image, m["image"] - 2) + 4
        flags, count = word(self.table, rec + 18), word(self.table, rec + 50)
        if flags & 0x0800:
            out.insert(0, "Option Compare Text")
        if m["defint"]:
            out.insert(0, "DefInt A-Z")
        if flags & 0x40:
            out.insert(0, "Option Explicit")
        out = ["'"] * max(0, count - len(out) - 1) + out + [""] if count else out
        self.cur_mod = m
        for info in self.text_order(m):
            out += self.emit_proc(info, m["form"], m["vars"], m["names"], m["image"])
        if m["tabs"] and out and not any("\t" in s for s in out):
            i = out.index("'") if "'" in out else 0  # flag 0x8000 needs a tab somewhere
            out[i] = out[i] + ("\t" if out[i] == "'" else "\t'")
        return out

    def text_order(self, m: dict) -> list:
        """Procedures in an order that allocates their records as the original
        text did: a record is allocated at the first mention of its name (a
        definition or a call), so the module's records must be mentioned in
        ascending order. Depth-first search, lowest record first."""
        infos = m["infos"]
        own = sorted({i.proc.record for i in infos})
        mine = set(own)
        mentions = [[x for x in [i.proc.record] + i.callees if x in mine] for i in infos]

        def dfs(done: tuple, seen: frozenset, k: int, budget: list) -> list | None:
            if len(done) == len(infos):
                return list(done)
            budget[0] -= 1
            if budget[0] < 0:
                return None
            for j in sorted(set(range(len(infos))) - set(done), key=lambda j: infos[j].proc.record):
                s2, k2, ok = set(seen), k, True
                for x in mentions[j]:
                    if x not in s2:
                        if own[k2] != x:
                            ok = False
                            break
                        s2.add(x)
                        k2 += 1
                if ok and (r := dfs(done + (j,), frozenset(s2), k2, budget)):
                    return r
            return None

        order = dfs((), frozenset(), 0, [5000])
        return [infos[j] for j in order] if order else sorted(infos, key=lambda i: i.proc.record)

    def run(self) -> list[dict]:
        """Modules with their source lines (m['lines'])."""
        if hasattr(self, "_mods"):
            return self._mods
        mods = self.module_list()
        for m in mods:
            self.analyze_module(m)
        self.declarations(mods)
        self.all_mods = mods
        self.decl_home = next((m for m in mods if m["kind"] == "bas"), mods[0])
        self.pool_lengths(mods)
        for m in mods:
            self.name_module(m)
        self.collect_calls(mods)
        self.slotted = {r for m in mods for _, r in m["funcs"]}
        self.decl_home = next((m for m in mods if m["kind"] == "bas"), mods[0])
        for m in mods:
            m["lines"] = self.emit_module(m)
        self._mods = mods
        return mods

    def pool_lengths(self, mods: list[dict]) -> None:
        """Procedure record +4 is an offset in the IDE's compile-time name
        pool: a 90-byte header, then per module (code modules, then forms,
        in project order) its file's full path, then each procedure/Declare
        name the module enters first (at its definition or a call
        statement), len + 4 each, shared project-wide. The gaps between the
        offsets give the unstored names' lengths (self.name_len) and the
        code modules' path lengths (self.bas_path_len), with D the length of
        the original build directory (form paths: D + 1 + file name)."""
        t = self.table
        recs = sorted(set(self.by_record) | {r for r in range(0, len(t) - 55, 8) if self.is_declare(r)})
        block_of = {}
        for b, m in enumerate(mods):
            for info in m["infos"]:
                block_of[info.proc.record] = b
        for r in recs:
            if r not in block_of:
                block_of[r] = mods.index(self.declare_home(r))
        at: dict[int, list[int]] = {}
        for r in recs:
            at.setdefault(word(t, r + 4), []).append(r)
        offs = sorted(at)
        blk = [min(block_of[r] for r in at[o]) for o in offs]
        length: list = []
        for o in offs:
            known = None
            for r in at[o]:
                if r in self.events:
                    known = len(self.events[r])
                elif r not in self.by_record:  # Declare: its DLL function name (no Alias assumed)
                    known = len(pool_name(self.image, self.pool, word(t, r + 46)))
            length.append(known)
        files = {b: self.form_files[k] if k < len(self.form_files) else None
                 for k, b in enumerate(b for b, m in enumerate(mods) if m["kind"] == "frm")}
        bas_len: dict[int, int | None] = {b: None for b, m in enumerate(mods) if m["kind"] == "bas"}
        D: list = [None]

        def path(b):  # (known length or None, which unknown)
            if b in bas_len:
                return (bas_len[b], ("bas", b))
            f = files.get(b)
            return (None if D[0] is None or f is None else D[0] + 1 + len(f), ("D", b))

        eqs = []  # (lhs, name-length index or None, blocks whose paths are summed)
        if offs:
            eqs.append((offs[0] - 90, None, list(range(0, blk[0] + 1))))
        for i in range(len(offs) - 1):
            eqs.append((offs[i + 1] - offs[i] - 4, i, list(range(blk[i] + 1, blk[i + 1] + 1))))
        for final in (False, True):
            if final and D[0] is None:
                D[0] = getattr(self, "build_dir_len", None)
            changed = True
            while changed:
                changed = False
                for lhs, i, blocks in eqs:
                    rest, unknown = lhs, []
                    if i is not None:
                        if length[i] is None:
                            unknown.append(("len", i))
                        else:
                            rest -= length[i]
                    for b in blocks:
                        v, u = path(b)
                        if v is None:
                            unknown.append(u)
                        else:
                            rest -= v + 4
                    if len(unknown) != 1:
                        if final and D[0] is not None and unknown and all(k in ("bas", "len") for k, _ in unknown):
                            # only the sum is observable: module paths get 7-character
                            # stems, a name the remainder (else the paths share it)
                            paths = [x for k, x in unknown if k == "bas"]
                            names = [x for k, x in unknown if k == "len"]
                            each = D[0] + 1 + 7 + 4
                            if names and 1 <= rest - len(paths) * (each + 4) <= 40:
                                length[names[0]] = rest - len(paths) * (each + 4)
                                for x in paths:
                                    bas_len[x] = each
                            elif not names:
                                q, r_ = divmod(rest - 4 * len(paths), len(paths))
                                for j_, x in enumerate(paths):
                                    bas_len[x] = q + (j_ < r_)
                            else:
                                continue
                            changed = True
                        continue
                    kind, x = unknown[0]
                    if kind == "len":
                        length[x] = rest
                    elif kind == "bas":
                        bas_len[x] = rest - 4
                    elif files.get(x):
                        D[0] = rest - 4 - 1 - len(files[x])
                    else:
                        continue
                    changed = True
        self.name_len = {r: length[k] for k, o in enumerate(offs) for r in at[o]
                         if length[k] is not None and 0 < length[k] <= 40}
        self.bas_path_len = {b: v for b, v in bas_len.items() if v is not None}
        self.orig_dir_len = D[0]

    def name_module(self, m: dict) -> None:
        vars_, refs, infos = m["vars"], m["refs"], m["infos"]
        names: dict[int, str] = {}
        for it in m["items"]:
            if it[0] in ("dim", "const"):
                names[it[1]] = f"K{it[1]:X}" if it[0] == "const" else mod_name(it[1])
            elif it[0] == "newobj":
                names[it[1]] = f"G{it[3]:X}" if m["kind"] == "bas" else mod_name(it[1])
                self.global_name[it[3]] = names[it[1]]
        gconst = {}  # (type, literal) -> Global Const names (each copy slot needs its own)
        for mm in self.all_mods:
            for it in mm["items"]:
                if it[0] == "global" and it[2]:
                    gconst.setdefault((it[4], it[2]), []).append(it[5])
        used_g: set = set()
        for s, v in sorted(vars_.items()):
            if s in names:
                continue
            if v.glob is not None and v.glob in self.global_name:
                names[s] = self.global_name[v.glob]
                continue
            lit, gname = None, None
            if v.scope == "MOD" and s >= m["first_owned"] and not v.stored and not v.array:
                for t in ([v.type()] if v.votes else []) + ["L", "I"]:  # untyped: shared Long/String handler
                    lit = self.inline_const(m["image"], s, t, 16, zero=True)
                    free = [g for g in gconst.get((t, lit), []) if g not in used_g] if lit else []
                    if free:
                        gname = free[0]
                        used_g.add(gname)
                        v.copy_type = t
                        break
                if not gname:
                    lit = self.inline_const(m["image"], s, v.type(), 16)
            if gname:
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
        owned_all = {s for s, v in vars_.items() if v.scope in ("LOC", "REF")} | {r - 2 for r in refs} | set(refs)
        for k, info in enumerate(infos):
            top = 6 + 2 * info.argwords  # parameters lie in [6, top)
            is_param = lambda s, top=top: 6 <= self.value(base, s) < top and self.value(base, s) % 2 == 0  # noqa: E731
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
        self.call_modules: dict[int, set] = {}
        for m in mods:
            self.cur_base = m["image"]
            for info in m["infos"]:
                calls, info.callees = [], []
                self.statements(info, m["names"], calls)
                by_name = {n.lower(): x for x, n in m["names"].items()}
                for name, operand, types, texts in calls:
                    for j, (t, tx) in enumerate(zip(types, texts)):
                        v = m["vars"].get(by_name.get(tx.lower(), -1))
                        if t == "&" and v is not None and v.votes:  # ByRef argument: the variable's type
                            types[j] = "&" + v.type()
                    (rec,) = struct.unpack_from("<H", operand, 2)
                    if name == "CALL_FN":
                        rec = self.value(m["image"], rec, False)
                    if name != "CALL_FN":  # a Sub call statement allocates the record; a
                        info.callees.append(rec & 0xFFF8)  # function call in an expression doesn't
                    self.call_types.setdefault(rec & 0xFFF8, []).append(types)
                    self.call_modules.setdefault(rec & 0xFFF8, set()).add(id(m))

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
            his = [x.name for x in infos[k + 1:] if x.name]
            r = info.proc.record
            if r in slot_order:
                j = slot_order.index(r)
                los += [fixed.get(x) or self.proc_name.get(x, "") for x in slot_order[:j]]
                his += [fixed[x] for x in slot_order[j + 1:] if x in fixed]
            return max(los, key=str.lower, default=""), min(his, key=str.lower, default=None)

        taken = {n.lower() for n in self.proc_name.values()} | {x.name.lower() for x in infos if x.name}

        def fits(c: str, lo: str, hi: str | None) -> bool:
            return c.lower() > lo.lower() and (hi is None or c.lower() < hi.lower()) and c.lower() not in taken

        lens = getattr(self, "name_len", {})
        for k, info in enumerate(infos):  # exact original lengths (compile-time name pool)
            if not info.name and info.proc.record in lens:
                c = name_between(*bounds(k), lens[info.proc.record], taken)
                if c:
                    info.name = c
                    self.proc_name[info.proc.record] = c
                    taken.add(c.lower())
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
            # names by BP offset: ByRef parameters, 4 bytes each, the last one at +6
            bp_name = {6 + 4 * (len(decl) - 1 - j): d.split()[0] for j, d in enumerate(decl)}
            k = self.cur_mod["infos"].index(info)
            for s, v in vars_.items():
                if v.procs and v.procs[0] == k and v.scope in ("LOC", "REF") and self.value(base, s) in bp_name:
                    names[s] = bp_name[self.value(base, s)]
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
                j = info.params.index(s)
                seen = [c[j].lstrip("&") for c in self.call_types.get(info.proc.record, []) if j < len(c) and c[j]]
                t = v.type() if v.votes else {2: "I", 8: "D", 16: "V"}.get(size) or \
                    (seen[0] if seen and seen[0] in TYPE_NAME else "V")  # unused: as the callers pass it
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
            lines.append(("\t" * (col // 8) + " " * (col % 8) if self.cur_mod.get("tabs") else " " * col) + text)
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
                td = self.gimg.types.get(m["udt"].get(s)) or next(
                    (t for t in self.gimg.types.values() if 0 <= size.get(s, 0) - t.size <= 2), None)
                decl = f"As {td.name}" if td else "As Variant"
            else:
                t = v.type() if v.votes else ("T" if self.value(base, s) % 2 == 1 else
                                              {2: "I", 4: "L", 8: "D"}.get(size.get(s), "V"))
                decl = ("()" if v.array else "") + f" As {TYPE_NAME[t]}"
            items.append((s, "dim", names[s], decl, v))
        for s, v in vars_.items():
            if v.scope == "MOD" and v.procs and v.procs[0] == k and s >= m["first_owned"] \
                    and (v.procs == [k] or not names[s].startswith(("s", "K"))):
                lit = None if v.stored or v.array else self.inline_const(base, s, v.type(), 16)
                if not names[s].startswith(("K", "s")):
                    items.append((s, "fixed", names[s], None, v))  # a Global Const's copy
                elif lit:  # a Const inside the procedure: stored inline like a module one
                    items.append((s, "const", names[s], f"= {lit}", v))
                else:
                    dims = f"({self.array_dims(base, s)[0]})" if v.array else ""
                    items.append((s, "static", names[s], dims + f" As {TYPE_NAME[v.type()]}", v))
            elif v.scope == "GLB" and v.procs and v.procs[0] == k and s >= m["first_owned"]:
                items.append((s, "fixed", names[s], None, v))
        for s, (kk, n) in m["refs"].items():
            if kk == k:
                items.append((s, "fixed", n, None, None))
        for s, kk in m.get("call_slots", {}).items():  # external functions: slot at the call
            if kk == k and (n := self.proc_name.get(self.value(base, s) & 0xFFF8)):
                items.append((s, "fixed", n, None, None))
        items.sort(key=lambda it: (it[0], it[1]))
        # unused locals leave gaps in the slot numbering: declare fillers
        # (odd value: String local; negative: BP offset, size from the frame)
        known = {it[0] for it in items} | skip
        for it in items:
            if it[1] == "dim" and it[3].endswith("As Variant"):
                known.add(it[0] + 2)  # a local Variant takes 4 bytes of slots
        for x, v in vars_.items():  # constants' copies / statics: inline, sized by type
            if v.scope == "MOD" and v.procs and v.procs[0] == k and x >= m["first_owned"]:
                n = self.array_dims(base, x)[1] if v.array else MOD_SIZE.get(v.copy_type or v.type(), 2)
                known.update(range(x, x + n, 2))
        for x, v in vars_.items():  # a local array takes 6 bytes of slots
            if v.array and v.scope == "LOC" and v.procs and v.procs[0] == k:
                known.update((x + 2, x + 4))
        for x in skip:  # Variant return value / parameters take 4 bytes of slots too
            v = vars_.get(x)
            t = info.ret if x == info.ret_slot else (v.type() if v and v.votes else "V")
            if t == "V":
                known.add(x + 2)
        mine = sorted(known)
        if mine:
            later = [x for kk2, other in enumerate(m["infos"]) if kk2 > k
                     for x in [min((ss for ss, vv in vars_.items() if vv.procs and vv.procs[0] == kk2), default=None)]
                     if x is not None]
            hi = min([x for x in later if x > mine[-1]] + [mine[-1] + 2])
            negs = sorted((x, self.value(base, x)) for x in range(mine[0], hi, 2) if self.value(base, x) < 0)
            fsize, prev = {}, -22
            for x, o in negs:
                fsize[x], prev = prev - o, o
            # locals only: from the first local (negative BP offset or String number) on
            frame_known = sorted(x for x in known if x in vars_ and vars_[x].scope in ("LOC", "REF")
                                 and (self.value(base, x) < 0 or self.value(base, x) % 2 == 1)) or [1 << 30]
            owned_k = [x for x, v in vars_.items() if v.procs and v.procs[0] == k] + \
                      [r - 2 for r, (kk2, _) in m["refs"].items() if kk2 == k] + \
                      [x for x, kk2 in m.get("call_slots", {}).items() if kk2 == k]
            hi = min(hi, max(owned_k + [0]))  # up to the procedure's last slot
            records = [r for r, (kk2, _) in m["refs"].items()] + \
                      [x2 for x2, v2 in vars_.items() if v2.obj or v2.glob is not None]
            calls_here = set(m.get("call_slots", {}))  # external function slots: `0, record`
            if frame_known == [1 << 30] and owned_k:
                # no used locals: unused ones are the zeros between the previous
                # procedure's slots and this one's first (String fillers: no frame)
                rest = [x for x in owned_k if x not in skip]
                fo, pe = (min(rest) if rest else 1 << 30), self.prev_end(m, k)
                # the procedure's own (unused) parameters come first: 2 slot bytes per
                # ByRef parameter (4 argument bytes)
                pe += self.param_slot_bytes(m, info)
                pe = max([pe] + [x + 2 for x in skip])
                if pe < fo and all(self.value(base, z) == 0 for z in range(pe, fo, 2)):
                    for z in range(pe, fo, 2):
                        items.append((z, "static", f"f{z:X}", " As Integer", Var(z, "MOD")))
            x = frame_known[0]
            # leading unused locals: zero slots from the end of the previous
            # procedure's allocations up to the first used local
            # only with evidence: the module's first procedure (zeros after the
            # declarations), or frame space the first local doesn't account for
            prev_end = self.prev_end(m, k)
            first_bp = self.value(base, x)
            v1 = vars_.get(x)
            fs1 = {"I": 2, "L": 4, "S": 4, "D": 8, "C": 8, "V": 16}.get(v1.type() if v1 and v1.votes else "V", 2)
            # Strings and Variants share one numbering (1, 3, ...): a first local numbered
            # above 1 means numbered locals were declared before it
            evidence = prev_end == m["first_owned"] or (first_bp < 0 and -22 - first_bp - fs1 >= 16) or \
                (first_bp > 1 and first_bp % 2 == 1)
            if evidence and prev_end < x and all(self.value(base, z) == 0 and z not in known
                                                 for z in range(prev_end, x, 2)):
                x = prev_end
            while x < hi:
                if any(r - 2 <= x < r + 6 for r in records) or x in calls_here:
                    x += 2  # inside a control/object record or an external function slot
                    continue
                if x in known:
                    x += 2
                    continue
                o = self.value(base, x)
                if o == 0:
                    # a run of unused locals (value 0): unused Variants still take 16 bytes of
                    # frame (2 slots), unused Strings none (1 slot); split by the frame gap
                    y = x
                    while y < hi and y not in known and self.value(base, y) == 0 \
                            and not any(r - 2 <= y < r + 6 for r in records) and y not in calls_here:
                        y += 2
                    prev_bp = min([self.value(base, z) for z in known if z < x and self.value(base, z) < 0] + [-22])
                    nxt_k = min((z for z in known if z >= y and self.value(base, z) < 0), default=None)
                    # frame gap (unused Variants take 16 bytes, numerics their size) and
                    # numbering (Variants and Strings share 1, 3, ...) of the next locals
                    nv, ns, extra, framed = 0, 0, 0, nxt_k is not None
                    if framed:
                        v2 = vars_.get(nxt_k)
                        t2 = v2.type() if v2 is not None and v2.votes else "V"
                        fs2 = {"I": 2, "L": 4, "S": 4, "D": 8, "C": 8, "V": 16}.get(t2, 2)
                        extra = max(0, prev_bp - self.value(base, nxt_k) - fs2)
                        nv = min(extra // 16, (y - x) // 4)
                        extra -= 16 * nv
                    nxt_s = min((z for z in known if z >= y and self.value(base, z) > 0
                                 and self.value(base, z) % 2 == 1 and z in vars_), default=None)
                    if nxt_s is not None:
                        prior = sum(1 for z in known if z < x and self.value(base, z) > 0
                                    and self.value(base, z) % 2 == 1 and self.value(base, z) < 200)
                        numbered = max(0, (self.value(base, nxt_s) - 1) // 2 - prior)
                        if framed:
                            ns = max(0, numbered - nv)  # the other numbered ones: Strings
                        else:
                            nv = min(numbered, (y - x) // 4)
                    while x < y:
                        if nv > 0:
                            vt, step, nv = "Variant", 4, nv - 1
                        elif ns > 0:
                            vt, step, ns = "String", 2, ns - 1
                        elif extra >= 2:  # the rest of the frame gap: numeric locals
                            size = 8 if extra >= 8 else 4 if extra >= 4 else 2
                            vt = {8: "Double", 4: "Long", 2: "Integer"}[size]
                            extra -= size
                            step = 2
                        else:  # no frame, no number: a Static (module storage) keeps both
                            vt, step = "Static", 2
                        if vt == "Static":
                            items.append((x, "static", f"f{x:X}", " As Integer", Var(x, "MOD")))
                        else:
                            items.append((x, "dim", f"f{x:X}", f" As {vt}", Var(x, "LOC")))
                        x += step
                    continue
                interior = x < frame_known[-1]  # nonzero values are locals only between known ones
                if o % 2 == 1 and 0 < o < 64 and interior:  # a String local
                    items.append((x, "dim", f"f{x:X}", " As String", Var(x, "LOC")))
                    x += 2
                elif o < 0 and interior:
                    t = {2: "I", 4: "L", 8: "D", 16: "V"}.get(fsize.get(x), "I")
                    items.append((x, "dim", f"f{x:X}", f" As {TYPE_NAME[t]}", Var(x, "LOC")))
                    x += 4 if t == "V" else 2
                else:
                    x += 2
        # zeros just before the next procedure's slots, after all earlier ones: unused
        # locals of a procedure that owns no slots (String fillers: no frame); they go
        # in the procedure right before it
        def own(kk: int) -> list:
            return [x for x, v in vars_.items() if v.procs and v.procs[0] == kk and x >= m["first_owned"]] + \
                   [r - 2 for r, (k2, _) in m["refs"].items() if k2 == kk] + \
                   [x for x, k2 in m.get("call_slots", {}).items() if k2 == kk]
        nxt_info = m["infos"][k + 1] if k + 1 < len(m["infos"]) else None
        # only when the next procedure starts with its first parameter: zeros before
        # it can't be its own locals (they follow its parameters)
        first_ok = nxt_info is not None and own(k + 1) and nxt_info.argwords and \
            self.value(base, min(own(k + 1))) == 6 + 2 * nxt_info.argwords - 4
        if first_ok and not own(k) and not info.argwords:
            end, start = self.prev_end(m, k + 1), min(own(k + 1))
            if end < start and all(self.value(base, z) == 0 for z in range(end, start, 2)):
                for z in range(end, start, 2):
                    items.append((z, "static", f"f{z:X}", " As Integer", Var(z, "MOD")))
        if k == len(m["infos"]) - 1:  # zeros after every procedure's slots: unused locals too
            end, n = self.prev_end(m, k + 1), word(self.image, base)
            if end < n - 1 and all(self.value(base, z) == 0 for z in range(end, n - 1, 2)):
                for z in range(end, n - 1, 2):
                    items.append((z, "static", f"f{z:X}", " As Integer", Var(z, "MOD")))
        items.sort(key=lambda it: (it[0], it[1]))

        texts = [re.sub(r'"[^"]*"', lambda x: " " * len(x.group(0)), t or "") for _, t in body]

        def compile_order(t: str) -> str:
            """An assignment's target gets its slot after the expression is compiled."""
            mt = re.match(r"^(\s*)(Set\s+|Let\s+)?([A-Za-z_][\w.!$%&#@]*(?:\([^=]*\))?)\s*=\s*(.*)$", t)
            if mt and not re.match(r"^\s*(If|ElseIf|For|Select|Case|Do|Loop|While)\b", t, re.I):
                return mt.group(1) + mt.group(4) + " " + mt.group(3)
            return t
        texts = [compile_order(t) for t in texts]

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
            implicit_type = "Integer" if m["defint"] else "Variant"
            if key is not None and key[0] < pos and kind == "dim" and not v.array and not v.udt \
                    and key[:2] > last and not m["explicit"] and (decl.strip() == f"As {implicit_type}" or key[2]):
                last = key[:2]
                continue
            if key is not None and key[0] < pos:
                pos = key[0]  # conflicting order: at least keep it compilable
            word_ = {"static": "Static", "const": "Const"}.get(kind, "Dim")
            dims.setdefault(pos, []).append(f"{word_} {name}{decl if decl[0] in '( ' else ' ' + decl}")
            last = (pos, -1)
        return dims

    def proc_spans(self, m: dict) -> dict:
        """Slot intervals each procedure allocates (text order: each one's
        after the previous one's): variables 2 bytes (Variants 4), control
        records 6, form/property/object records 4, constants' copies their
        size, external function slots 2. k -> (start, end) of its span, and
        "intervals": k -> [(a, b)]."""
        if "spans" in m:
            return m["spans"]
        base, vars_ = m["image"], m["vars"]
        iv: dict[int, list] = {}
        for x, v in vars_.items():
            if not v.procs or x < m["first_owned"]:
                continue
            k = v.procs[0]
            if v.scope in ("LOC", "REF"):
                info = m["infos"][k]
                t = info.ret if x == info.ret_slot else (v.type() if v.votes else None)
                nxtv = self.value(base, x + 2, False)
                wide = t == "V" or (t is None and nxtv in (0, 1) and x in info.params) or \
                    (x + 2 not in vars_ and nxtv % 2 == 1 and nxtv < 200 and self.value(base, x) < 0)
                if v.obj:
                    iv.setdefault(k, []).append((x - 2, x + 2))
                    continue
                iv.setdefault(k, []).append((x, x + (4 if wide else 2)))
            elif v.scope == "MOD":
                n = MOD_SIZE.get(v.copy_type or v.type(), 2)
                iv.setdefault(k, []).append((x, x + n))
            else:
                iv.setdefault(k, []).append((x, x + 2))
        for r, (k, _) in m["refs"].items():
            w0, w1 = word(self.image, base + r), word(self.image, base + r + 2)
            n = 4 if w0 >> 8 == 0x80 else 6  # form/object record 4; control and form property 6
            iv.setdefault(k, []).append((r - 2, r - 2 + n))
        for x, k in m.get("call_slots", {}).items():
            iv.setdefault(k, []).append((x, x + 2))
        spans, prev = {"intervals": iv}, m["first_owned"]
        for k in range(len(m["infos"])):
            ivs = iv.get(k, [])
            end = max([b for _, b in ivs] + [prev])
            spans[k] = (prev, end)
            prev = end
        m["spans"] = spans
        return spans

    def param_slot_bytes(self, m: dict, info: ProcInfo) -> int:
        """Slot bytes of a procedure's parameters and return value: 2 each,
        4 for object (Control/Form) and Variant ones."""
        n = 2 if info.function else 0
        ev = self.events.get(info.proc.record)
        if ev:
            ctl, _, e = ev.rpartition("_")
            cls = self.sym.form_class.get(m["form"], "Form") if ctl in ("Form", "MDIForm") else \
                self.sym.classes.get((m["form"], ctl), "")
            types = P.EVENT_TYPES.get((cls, e), P.MASTER_EVENT_TYPES.get(e, ()))
            n += sum(4 if t == 8 else 2 for t in types)
            if info.argwords > 2 * len(types):
                n += 2  # Index
            return n
        count = info.argwords // 2
        known = [m["vars"][x] for x in info.params if x in m["vars"]]
        return n + 2 * count + sum(2 for v in known if v.obj or (v.votes and v.type() == "V"))

    def prev_end(self, m: dict, k: int) -> int:
        """End of the slots allocated by the procedures before k (text order),
        each one's range starting with its parameters (2 slot bytes per ByRef
        parameter, used or not) and its return value."""
        if "ends" not in m:
            base, vars_, ends, end = m["image"], m["vars"], [], m["first_owned"]
            for kk, info in enumerate(m["infos"]):
                e = end + self.param_slot_bytes(m, info)
                for x, v in vars_.items():
                    if v.procs and v.procs[0] == kk and x >= m["first_owned"]:
                        if v.scope == "MOD":
                            n = self.array_dims(base, x)[1] if v.array else MOD_SIZE.get(v.copy_type or v.type(), 2)
                        else:
                            n = 4 if (v.votes and v.type() == "V") or (
                                x + 2 not in vars_ and self.value(base, x + 2, False) % 2 == 1) else 2
                        e = max(e, x + n)
                for r, (k2, _) in m["refs"].items():
                    if k2 == kk:
                        e = max(e, r + (2 if word(self.image, base + r) >> 8 == 0x80 else 4))
                for x, k2 in m.get("call_slots", {}).items():
                    if k2 == kk:
                        e = max(e, x + 2)
                ends.append(e)
                end = e
            m["ends"] = ends
        return m["ends"][k - 1] if k > 0 else m["first_owned"]

    def statements(self, info: ProcInfo, names: dict, calls: list | None = None) -> list[tuple[int, str]]:
        """(indentation column, lifted text) per statement (marker to the
        next marker), with label lines."""
        out, cur, curn = [], [], []
        col, marked = [0], [False]

        def flush():
            if cur and not all(NAMES.get(op, "") in ("RET", "TRAP", "OBJ_FREE") for op, _ in cur):
                text = lift(cur, self.ids, names=curn, calls=calls)
                if col[0] == -1 and out:
                    out[-1] = (out[-1][0], out[-1][1] + ": " + text)
                else:
                    out.append((max(col[0], 0), text))
            cur.clear()
            curn.clear()

        for i, note in zip(info.insns, info.notes):
            if self.rt.is_stmt(i.op):
                flush()
                if i.op == STMT_SAME_LINE:
                    col[0] = -1  # joins the previous line with `:`
                else:
                    c = stmt_column(self.rt, i.op, i.operand)
                    col[0] = 4 if c is None else c
                marked[0] = True
                continue
            if i.op == LABEL:
                if not cur and marked[0]:  # an empty statement: a blank line kept before a label
                    out.append((0, ""))
                flush()
                (num,) = struct.unpack_from("<I", i.operand)
                out.append((0, f"L{i.pc:x}:" if num == 0xFFFFFFFF else f"{num}"))
                marked[0] = False
                continue
            marked[0] = False
            cur.append((i.op, i.operand))
            curn.append(self.name_for(i, note, names))
        flush()
        numbered = {i.pc: struct.unpack_from("<I", i.operand)[0] for i in info.insns
                    if i.op == LABEL and struct.unpack_from("<I", i.operand)[0] != 0xFFFFFFFF}
        if numbered:  # line-number labels: jumps name them by number
            out = [(c, re.sub(r"\bL([0-9a-f]+)\b", lambda x: str(numbered.get(int(x.group(1), 16), x.group(0))), t))
                   for c, t in out]
        return out

    def name_for(self, i, note: str, names: dict) -> str | None:
        n = NAMES.get(i.op) or ""
        slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0] if len(i.operand) >= 2 else None
        if n in ("PGET", "PSET", "PGET_IDX", "PSET_IDX"):
            if "!" in note:  # operand 0x80nn: control nn of the form object, default property
                return note.rpartition("!")[2]
            return note.rpartition(".")[2] or None
        if n in ("PGET_ME", "PSET_ME"):
            return note.rpartition(".")[2] if note else None
        if n in ("SUBOBJ", "CTLARRAY_OF"):
            return note.rpartition("!")[2].rpartition(".")[2] or None
        if n.startswith(("FIELD_GET", "FIELD_SET", "FIELD_ADDR", "FIELD_ALOAD", "FIELD_ASTORE")) and slot is not None:
            return f"F{slot:X}"  # field record offset, as in the Type declaration
        if n == "TYPEOF_IS" and slot is not None:
            nforms = self.forms
            base = 0x46 + len(P.vbx_entries(self.res.get(1, b"")))
            if 0 <= slot - base < len(nforms):
                return nforms[slot - base][0]
            return OBJ_KINDS.get(slot) or P.CLASS_BY_KIND.get(slot)
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


def wine_path(p: Path) -> str:
    """How the IDE (under Wine, drive Z: = /) sees a directory."""
    return "Z:" + str(p.resolve()).replace("/", "\\")


def bas_stems(d: Decompiler, mods: list[dict], n: int, dir_len: int, used: set[str]) -> list[str]:
    """Code module file names: their full paths are compile-time name pool
    entries, so each stem keeps its original length (the original build
    directory's length when known, else dir_len, the output's)."""
    paths = getattr(d, "bas_path_len", {})
    bas = [b for b, m in enumerate(mods) if not m["form"]]
    out = []
    for k in range(n):
        want = paths.get(bas[k]) if k < len(bas) else None
        size = want - (getattr(d, "orig_dir_len", None) or dir_len) - 1 - 4 if want is not None else None
        stem = f"MODULE{k + 1}"
        if size is not None and 1 <= size <= 8:
            base = f"M{k + 1}" if size >= len(f"M{k + 1}") else ""
            stem = next((c for c in ([base.ljust(size, "X")] if base else []) +
                         [chr(65 + j) * size for j in range(26)] if c not in used), stem)
        used.add(stem)
        out.append(stem)
    return out


def write_project(d: Decompiler, out: Path, layout_from: Path | None, name: str) -> Path:
    """Writes the module files and a .mak named after the executable; returns the .mak."""
    out.mkdir(parents=True, exist_ok=True)
    d.build_dir_len = len(wine_path(out))
    mods = d.run()
    code = {m["form"]: m["lines"] for m in mods if m["form"]}
    modules = [m["lines"] for m in mods if not m["form"]]
    originals = {f.name.upper(): f for f in layout_from.iterdir()} if layout_from else {}
    files = []
    decoded = [] if layout_from else FB.forms(d.exe, d.rt)
    for k, form in enumerate(d.forms):
        fname = d.form_files[k] if k < len(d.form_files) else f"FORM{k + 1}.FRM"
        layout = ["VERSION 2.00", f"Begin Form {form[0]}", "End"]
        if k < len(decoded):
            frx = bytearray()
            frx_name = Path(fname).with_suffix(".FRX").name
            layout = ["VERSION 2.00"] + FB.form_text(decoded[k], frx, frx_name)
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
    stems = bas_stems(d, mods, len(modules), len(wine_path(out)), {Path(f).stem.upper() for f in files})
    for k, lines in enumerate(modules):
        fname = f"{stems[k]}.BAS"
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
