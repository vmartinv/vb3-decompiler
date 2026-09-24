#!/usr/bin/env python3
"""
Disassembles the p-code of a VB3-compiled executable, procedure by procedure.

Two findings make this possible (see ../OPCODES.md, "Threaded code" and
"Procedure -> segment resolution"):

1. VB3 p-code is *threaded code*. Every 2-byte "opcode" is the near address
   of its handler inside VBRUN300.DLL's interpreter segment (segment 25 of
   the stock VBRUN300.DLL); each handler ends by fetching the next word
   (`es:lodsw; jmp ax`). So each opcode's operand length is derived here by
   statically exploring its x86 handler and counting how far it advances
   SI before dispatching. The word just before each handler is the
   interpreter's own opcode ID, shared by type-specialized variants.

2. Procedure records live in segment 3, and each record's owning code
   segment is given by an NE INTREF relocation at record+38 (on disk the
   bytes there are fixup-chain links). Record+24 / +36 are the procedure's
   [start, end) offsets within that code segment.

Needs your own copy of VBRUN300.DLL (not included) and `capstone`
(`pip install capstone`).

Usage:
    python3 tools/pcode_disasm.py <exe> --runtime <VBRUN300.DLL> [--out FILE]
    python3 tools/pcode_disasm.py <exe> --runtime <VBRUN300.DLL> --check
"""
from __future__ import annotations

import argparse
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

from capstone import CS_ARCH_X86, CS_MODE_16, Cs
from capstone.x86 import X86_OP_IMM

INTERPRETER_SEGMENT = 25  # 1-based, in the stock VBRUN300.DLL
RUNTIME_DATA_SEGMENT = 100  # VBRUN300's data segment (control models)
PROC_TABLE_SEGMENT = 3    # 1-based, in every VB3 exe tested

# ---------------------------------------------------------------------------
# NE parsing (segments + relocations)
# ---------------------------------------------------------------------------


@dataclass
class Reloc:
    src_type: int
    flags: int
    offset: int  # head of the fixup chain (non-additive) or the single site
    target1: int
    target2: int

    @property
    def kind(self) -> int:
        return self.flags & 3  # 0 INTREF, 1 IMPORD, 2 IMPNAME, 3 OSFIXUP

    @property
    def additive(self) -> bool:
        return bool(self.flags & 4)


@dataclass
class Segment:
    index: int
    length: int
    flags: int
    data: bytes
    relocs: list[Reloc] = field(default_factory=list)

    def fixup_sites(self, r: Reloc) -> list[int]:
        """All patched offsets for a relocation (walks the in-segment chain)."""
        if r.additive:
            return [r.offset]
        sites, o = [], r.offset
        while o != 0xFFFF and len(sites) < 0x10000:
            sites.append(o)
            (o,) = struct.unpack_from("<H", self.data, o)
        return sites


def parse_ne(path: Path) -> list[Segment]:
    raw = path.read_bytes()
    (ne,) = struct.unpack_from("<H", raw, 0x3C)
    segtab = ne + struct.unpack_from("<H", raw, ne + 0x22)[0]
    (shift,) = struct.unpack_from("<H", raw, ne + 0x32)
    (count,) = struct.unpack_from("<H", raw, ne + 0x1C)
    segs = []
    for i in range(count):
        off, length, flags, _ = struct.unpack_from("<HHHH", raw, segtab + 8 * i)
        length = length or 0x10000
        start = off << shift
        data = raw[start:start + length] if off else b""
        seg = Segment(i + 1, length, flags, data)
        if flags & 0x100 and off:
            p = start + length
            (n,) = struct.unpack_from("<H", raw, p)
            for k in range(n):
                seg.relocs.append(Reloc(*struct.unpack_from("<BBHHH", raw, p + 2 + 8 * k)))
        segs.append(seg)
    return segs


# ---------------------------------------------------------------------------
# Procedure table
# ---------------------------------------------------------------------------


@dataclass
class Proc:
    record: int  # offset of the procedure record in segment 3
    tag: int
    segment: int
    start: int
    end: int


def find_procs(segs: list[Segment]) -> list[Proc]:
    table = segs[PROC_TABLE_SEGMENT - 1]
    procs = []
    for r in table.relocs:
        if r.kind != 0 or r.additive:
            continue
        for site in table.fixup_sites(r):
            rec = site - 38
            (tag,) = struct.unpack_from("<H", table.data, rec)
            (start,) = struct.unpack_from("<H", table.data, rec + 24)
            (end,) = struct.unpack_from("<H", table.data, rec + 36)
            procs.append(Proc(rec, tag, r.target1, start, end))
    return sorted(procs, key=lambda p: (p.segment, p.start))


# ---------------------------------------------------------------------------
# Handler analysis: operand lengths straight from the interpreter
# ---------------------------------------------------------------------------

_TABLE = re.compile(r"word ptr (?:cs:)?\[(?:bx|di|si|bp) \+ (0x[0-9a-f]+)\]")
# Near-call trampolines into the out-of-segment builtin library: `call X`
# followed by a 1-byte builtin index; control comes back to dispatch with SI
# untouched. The far jumps are the same thing reached by `jmp` instead.
_BUILTIN_CALLS = {0x792B, 0x7930, 0x7935, 0x793A, 0x793F, 0x7944, 0x7958, 0x795D, 0x7962, 0x796C}
_BUILTIN_FARJMPS = {0x79A1, 0x79A6, 0x79B0}
# Shared procedure-call core: every call-family handler has read all of its
# operands (u16 0 + u16 record, or u16 record) by the time it gets here.
_CALL_CORE = 0x62E4
# Shared Resume code: `Resume <label>` reads its target, then jumps here.
_RESUME_CORE = 0x7E63
# Handlers whose operand reads happen in ways the static explorer can't
# follow (frame setup / peeking ahead). Each value was confirmed by the
# whole-program check (every procedure decodes to exactly its end offset).
_MANUAL = {
    0x36DF: 2,  # builtin on one path, dispatch on the other
    0x28FF: 2,  # TextHeight: same layout as TextWidth (0x296C, derived: 2)
    0x7582: 2,  # Len(variable): solved as 2 wherever unambiguous
}


def _word(d: bytes, o: int) -> int:
    return struct.unpack_from("<H", d, o)[0] if 0 <= o <= len(d) - 2 else 0


def _cstr(d: bytes, o: int) -> str | None:
    if not 0 < o < len(d):
        return None
    e = d.find(b"\0", o)
    t = d[o:e]
    return t.decode("latin-1") if 0 < len(t) < 40 and t.isascii() and t[:1].isalpha() else None


def parse_models(ds: bytes, mprops: list, mevents: list, min_ptr: int) -> dict[str, tuple[list, list]]:
    """Control class MODELs in a data segment (VBRUN300's or a VBX's):
    ... default name, class name, parent class, property list, event list.
    List entries: 0xFFxx = standard property/event ~w (master tables),
    else a PROPINFO/EVENTINFO pointer whose first word is the name; 0 ends."""
    w, name = (lambda o: _word(ds, o)), (lambda o: _cstr(ds, o))

    def entries(lst: int, master: list) -> list:
        out, q = [], lst
        while w(q) and len(out) < 100:
            v = w(q)
            out.append((master[0xFFFF - v] if 0xFFFF - v < len(master) else None) if v >= 0xFF80 else name(w(v)))
            q += 2
        return out

    found: dict[str, tuple[list, list]] = {}
    for r in range(8, len(ds) - 4, 2):
        cls, pl, el = name(w(r - 4)), w(r), w(r + 2)
        if not (name(w(r - 6)) and cls and min_ptr <= pl < len(ds) and pl != el
                and (el == 0 or min_ptr <= el < len(ds))):
            continue
        props = entries(pl, mprops)
        if len(props) < 5 or not ("Name" in props or "Left" in props):
            continue
        evs = entries(el, mevents) if el else []
        q = el
        while el and w(q) and (q - el) // 2 < len(evs):  # EVENTINFO: name, cParms, cwParms, npParmTypes
            v, k = w(q), (q - el) // 2
            if v >= 0xFF80:
                types = MASTER_EVENT_TYPES.get(mevents[0xFFFF - v] if 0xFFFF - v < len(mevents) else None)
            else:
                types = tuple(w(w(v + 6) + 2 * j) for j in range(min(w(v + 2), 16)))
            if types is not None and evs[k]:
                EVENT_TYPES.setdefault((cls, evs[k]), types)
            q += 2
        # A misaligned read of another MODEL can yield this class name with no
        # events (e.g. DirListBox's parent "ListBox"); prefer one with events.
        if cls not in found or (not found[cls][1] and evs):
            found[cls] = (props, evs)
    return found


def is_imm_op(i, k: int) -> bool:
    return len(i.operands) > k and i.operands[k].type == X86_OP_IMM


class Runtime:
    def __init__(self, dll: Path):
        segs = parse_ne(dll)
        self.code = segs[INTERPRETER_SEGMENT - 1].data
        self.data = segs[RUNTIME_DATA_SEGMENT - 1].data
        self.md = Cs(CS_ARCH_X86, CS_MODE_16)
        self.md.detail = True
        self._insn: dict[int, object] = {}
        self._helper: dict[int, int] = {}
        self._len: dict[int, object] = {}
        self._stmt: dict[int, bool] = {}
        self.solved: dict[int, set] = {}  # op -> lengths found by constraint
        self.vbx_dirs: list[Path] = []  # where to find VBX files (custom controls)
        self._vbx_loaded: set[str] = set()

    def plausible(self, op: int) -> bool:
        """Could `op` be a handler address? (Handlers start at 0x60 or above,
        at a decodable instruction.)"""
        return 0x60 <= op < len(self.code) and self.insn(op) is not None

    def opcode_id(self, op: int) -> int | None:
        if 2 <= op < len(self.code):
            return struct.unpack_from("<H", self.code, op - 2)[0]
        return None

    def insn(self, a: int):
        if a not in self._insn:
            ok = 0 <= a < len(self.code)
            self._insn[a] = next(self.md.disasm(self.code[a:a + 16], a), None) if ok else None
        return self._insn[a]

    def _table_targets(self, t: int, n: int = 12):
        for k in range(n):
            e = int.from_bytes(self.code[t + 2 * k:t + 2 * k + 2], "little")
            if abs(e - t) < 0x600 and self.insn(e):
                yield e

    def _helper_delta(self, a: int, depth: int) -> int:
        if a not in self._helper:
            self._helper[a] = 0  # recursion guard
            res = self._explore(a, 1500, depth + 1, helper=True)
            rets = {x[1] for x in res if isinstance(x, tuple) and x[0] == "ret"}
            self._helper[a] = rets.pop() if len(rets) == 1 else 0
        return self._helper[a]

    def _explore(self, start: int, maxsteps=4000, depth=0, helper=False) -> set:
        results: set = set()
        # sv: 0 normal; 1 SI pushed; 2 SI pushed and reused for other data
        # (`push si` ... `mov si, x` ... `pop si`): ignore SI changes meanwhile.
        work, seen, steps, regtab = [(start, 0, 0)], set(), 0, {}
        while work:
            a, delta, sv = work.pop()
            while True:
                steps += 1
                if steps > maxsteps or (a, delta, sv) in seen:
                    break
                seen.add((a, delta, sv))
                if a in (_CALL_CORE, _RESUME_CORE) and a != start and not helper:
                    results.add(delta); break
                i = self.insn(a)
                if i is None:
                    break
                m, o, nxt = i.mnemonic, i.op_str, a + i.size
                if m == "push" and o == "si":
                    sv = max(sv, 1); a = nxt; continue
                if m == "pop" and o == "si" and sv:
                    sv = 0; a = nxt; continue
                if sv == 2 and ("si" in o.split(",")[0] or m.startswith("lods")):
                    a = nxt; continue  # SI holds foreign data here
                if m == "lodsw" and "es:[si]" in o:
                    j = self.insn(nxt)
                    if j and j.mnemonic == "jmp" and j.op_str == "ax":
                        results.add(delta)
                        break
                    delta += 2; a = nxt; continue
                if m == "lodsb" and "es:[si]" in o:
                    delta += 1; a = nxt; continue
                if m == "mov" and o == "si, word ptr es:[si]":
                    results.add(("jump", delta + 2)); break
                if m == "add" and o == "si, ax" and delta == 2 and not helper:
                    results.add(("varlen", 2)); break
                if (o.startswith("si,") or o == "si") and sv == 1 and m in ("mov", "xchg", "lea"):
                    sv = 2; a = nxt; continue
                if o.startswith("si,") or o == "si":
                    if m in ("add", "sub"):
                        try:
                            v = int(o.split(",")[1], 0)
                            delta += v if m == "add" else -v; a = nxt; continue
                        except ValueError:
                            pass
                    if m == "inc": delta += 1; a = nxt; continue
                    if m == "dec": delta -= 1; a = nxt; continue
                    results.add(("term", delta)); break
                if m in ("ret", "retf", "iret"):
                    if helper and m == "ret":
                        results.add(("ret", delta))
                    break
                is_imm = i.operands and i.operands[0].type == X86_OP_IMM
                if m == "call" and is_imm and i.operands[0].imm in _BUILTIN_CALLS:
                    results.add(("builtin", delta)); break
                if m == "mov" and _TABLE.search(o) and o.split(",")[0] in ("dx", "ax", "bx", "di", "cx"):
                    regtab[o.split(",")[0]] = int(_TABLE.search(o).group(1), 16)
                if m == "mov" and o.startswith("di, ") and is_imm_op(i, 1):
                    regtab["cs:[di]"] = i.operands[1].imm  # table base; index added later
                if m == "jmp" and o == "word ptr cs:[di]" and "cs:[di]" in regtab:
                    work.extend((e, delta, sv) for e in self._table_targets(regtab["cs:[di]"], 24)); break
                if m == "jmp" and o in regtab:
                    work.extend((e, delta, sv) for e in self._table_targets(regtab[o])); break
                if m == "call":
                    if is_imm and depth < 4:
                        delta += self._helper_delta(i.operands[0].imm, depth)
                    a = nxt; continue
                if m == "lcall":
                    a = nxt; continue
                if m == "jmp":
                    if is_imm:
                        a = i.operands[0].imm; continue
                    mm = _TABLE.fullmatch(o)
                    if mm:
                        work.extend((e, delta, sv) for e in self._table_targets(int(mm.group(1), 16)))
                    elif helper and o in ("ax", "bx", "cx", "dx", "di"):
                        results.add(("ret", delta))  # returns via popped address
                    break
                if m == "ljmp":
                    if a in _BUILTIN_FARJMPS:
                        results.add(("builtin", delta))
                    break
                if m.startswith("j") or m in ("loop", "jcxz", "loope", "loopne"):
                    work.append((i.operands[0].imm, delta, sv)); a = nxt; continue
                a = nxt
        return results

    def operand_len(self, op: int):
        """Operand byte count, 'var' (u16 length + blob), or None if unknown."""
        if op in _MANUAL:
            return _MANUAL[op]
        if op not in self._len:
            r = self._explore(op) if op < len(self.code) else set()
            if any(isinstance(x, tuple) and x[0] == "varlen" for x in r) and \
                    not any(isinstance(x, int) for x in r):  # a path dispatching normally wins
                self._len[op] = "var"
            else:
                disp = {x for x in r if not isinstance(x, tuple)}
                disp |= {x[1] for x in r if isinstance(x, tuple) and x[0] == "builtin"}
                jumps = {x[1] for x in r if isinstance(x, tuple) and x[0] == "jump"}
                vals = {max(disp | jumps)} if jumps else (disp or {x[1] for x in r})
                self._len[op] = vals.pop() if len(vals) == 1 else None
        return self._len[op]

    def is_stmt(self, op: int) -> bool:
        """Statement markers: `mov ax, imm` / `inc si` stubs (possibly none) falling
        into `dec ss:[0x278]; js yield`. Several such entry points exist;
        the IDE tells source-line kinds apart by the entry address."""
        if op not in self._stmt:
            a, ok = op, False
            while 0 <= a < len(self.code):
                i = self.insn(a)
                if i is None:
                    break
                if i.mnemonic == "dec" and i.op_str == "word ptr ss:[0x278]":
                    ok = True
                    break
                if not (i.mnemonic == "mov" and i.op_str.startswith("ax, ")
                        or i.mnemonic == "inc" and i.op_str == "si"):  # 0x48AF skips a u16
                    break
                a += i.size
            self._stmt[op] = ok
        return self._stmt[op]

    def _masters(self) -> tuple[list, list]:
        """VBRUN300's master standard-property and standard-event tables
        (runs of PROPINFO/EVENTINFO pointers: Name, Index, hWnd, BackColor,
        ... / Click, DblClick, DragDrop, ...). Standard entries in any class
        list (0xFFxx = ~index) refer to these, VBX lists included."""
        if not hasattr(self, "_mst"):
            w, name = (lambda o: _word(self.data, o)), (lambda o: _cstr(self.data, o))
            props, events = [], []
            for m in range(0, len(self.data) - 40, 2):
                if not props and name(w(w(m))) == "Name" and name(w(w(m + 2))) == "Index" \
                        and name(w(w(m + 6))) == "BackColor":
                    k = m
                    while w(k) > 0x400 or (w(k) and name(w(w(k))) is None and k < m + 200):
                        props.append(name(w(w(k))))
                        k += 2
                if not events and name(w(w(m))) == "Click" and name(w(w(m + 2))) == "DblClick" \
                        and name(w(w(m + 4))) == "DragDrop":
                    k = m
                    while name(w(w(k))):
                        events.append(name(w(w(k))))
                        v = w(k)  # EVENTINFO: name, cParms, cwParms, npParmTypes
                        MASTER_EVENT_TYPES[events[-1]] = tuple(w(w(v + 6) + 2 * j) for j in range(min(w(v + 2), 16)))
                        k += 2
            self._mst = (props, events)
        return self._mst

    def _models(self) -> dict[str, tuple[list, list]]:
        if not hasattr(self, "_mdl"):
            self._mdl = parse_models(self.data, *self._masters(), min_ptr=0x1000)
        return self._mdl

    def load_vbx(self, path: Path) -> list[str]:
        """Adds a VBX's control classes (from its data segment's MODELs).
        Returns the class names found."""
        raw = path.read_bytes()
        (ne,) = struct.unpack_from("<H", raw, 0x3C)
        (auto,) = struct.unpack_from("<H", raw, ne + 0x0E)
        segs = parse_ne(path)
        found = parse_models(segs[auto - 1].data, *self._masters(), min_ptr=2)
        for cls, v in found.items():
            self._models().setdefault(cls, v)
        return list(found)

    def load_project_vbx(self, res: dict[int, bytes]) -> None:
        """Loads the VBX files listed in the project directory (RT_RCDATA 1),
        found in self.vbx_dirs (case-insensitive)."""
        for entry in vbx_entries(res.get(1, b"")):
            if not entry.upper().endswith(".VBX") or entry.upper() in self._vbx_loaded:
                continue
            for d in self.vbx_dirs:
                hits = [f for f in Path(d).glob("*") if f.name.upper() == entry.upper()]
                if hits:
                    self.load_vbx(hits[0])
                    self._vbx_loaded.add(entry.upper())
                    break

    def property_lists(self) -> dict[str, list[str]]:
        """Class -> property names (MODEL property lists; see parse_models)."""
        return {c: v[0] for c, v in self._models().items() if v[0]}

    def event_lists(self) -> dict[str, list[str]]:
        """Class -> event names (MODEL event lists; see parse_models)."""
        return {c: v[1] for c, v in self._models().items() if v[1] and all(v[1])}

    def is_branch(self, op: int) -> bool:
        self.operand_len(op)
        return op not in _MANUAL and any(
            isinstance(x, tuple) and x[0] == "jump" for x in self._explore(op))


# ---------------------------------------------------------------------------
# Control slots (RT_RCDATA 2: per-module initial data images)
# ---------------------------------------------------------------------------

def rcdata(path: Path) -> dict[int, bytes]:
    from ne_parser import NEFile
    return {r.res_id: r.data for r in NEFile(path).iter_resources() if r.type_name == "RT_RCDATA"}


def vbx_entries(res1: bytes) -> list[str]:
    """VBX files and the control classes they add, from the project directory:
    printable strings after the first `*.VBX` that aren't `*.FRM`."""
    strs = [m.group(0).decode("latin-1") for m in re.finditer(rb"[\x20-\x7e]{3,}", res1)]
    first = next((i for i, t in enumerate(strs) if t.upper().endswith(".VBX")), None)
    return [] if first is None else [t for t in strs[first:] if not t.upper().endswith((".FRM", ".BAS"))]


def form_names(res: dict[int, bytes]) -> list[list[str]]:
    """Name tables (form name, then one entry per control name, indexed by the
    controls' name index; empty entries are deleted controls), in project
    order: each form is a data blob (FF CC) followed by its name table."""
    out, ids = [], sorted(res)
    for a, b in zip(ids, ids[1:]):
        if res[a][:2] == b"\xff\xcc" and res[b][:2] != b"\xff\xcc":
            names, pos, d = [], 0, res[b]
            while pos < len(d):  # zero-length entries = deleted controls
                names.append(d[pos + 1:pos + 1 + d[pos]].decode("latin-1"))
                pos += 1 + d[pos]
            out.append(names)
    return out


KINDS: dict[str, int] = {}  # control name -> slot kind byte (last resolved)
CLASSES: dict[tuple[str, str], str] = {}  # (form, control) -> class, as resolved
SEG_FORM: dict[int, str] = {}  # code segment -> its form, as resolved
RECORD_FORM: dict[int, str] = {}  # procedure record -> form (from event tables)
OBJVAR_TYPES: dict[int, dict[int, str]] = {}  # code segment -> {slot: declared class}
SEG_IMAGE: dict[int, int] = {}  # code segment -> offset of its data image chunk in RT_RCDATA 2
EVENT_TYPES: dict[tuple[str, str], tuple[int, ...]] = {}
MASTER_EVENT_TYPES: dict[str, tuple[int, ...]] = {}  # standard event -> parameter types
MEPROPS: dict[int, dict[int, int]] = {}  # code segment -> {PGET_ME slot: property index}
FORM_CLASS: dict[str, str] = {}  # form -> Form | MDIForm (from its own event table)


def reset_state() -> None:
    """Forget what was resolved for the previous executable."""
    for d in (KINDS, CLASSES, SEG_FORM, RECORD_FORM, OBJVAR_TYPES, SEG_IMAGE, MEPROPS, FORM_CLASS):
        d.clear()

# Class byte in a form blob's control record (confirmed values only).
CLASS_BY_BLOB = {0x00: "PictureBox", 0x01: "Label", 0x02: "TextBox", 0x04: "CommandButton",
                 0x05: "CheckBox", 0x06: "OptionButton", 0x07: "ComboBox", 0x08: "ListBox", 0x09: "HScrollBar",
                 0x0B: "Timer", 0x10: "DriveListBox", 0x11: "DirListBox", 0x12: "FileListBox",
                 0x13: "Menu", 0x18: "Image"}  # 0xFF: VBX custom control
_CTL_RECORD = re.compile(rb"[\x01\x03](..)\x00\x00(.)\x00(.)\xff", re.S)
_CTL_RECORD_ANY = re.compile(rb"(?=[\x01-\x03]..(?:\x00\x00.\x00.|\x00\x80..\x00\x00.)"
                             rb"(?:[\x00-\x2f\xff]?\xff|(?<=\xff)[\x01-\x20][A-Za-z]))", re.S)


def blob_classes(res: dict[int, bytes]) -> dict[tuple[str, str], str]:
    """(form, control) -> class from each form blob's control records:
    `u8 flag 1-3, u16 length, u16 flags, u8 name index, u8 element, ...,
    class` with the class byte at +7 (+9 for control-array elements,
    flags & 0x8000); VBX controls (0xFF) name their class next."""
    out, ids = {}, sorted(res)
    for a, b in zip(ids, ids[1:]):
        if not (res[a][:2] == b"\xff\xcc" and res[b][:2] != b"\xff\xcc"):
            continue
        d, names = res[a], form_names({a: res[a], b: res[b]})[0]
        starts, todo = set(), [m.start() for m in _CTL_RECORD_ANY.finditer(d)]
        while todo:  # follow the record chain (start + 1 + length, 00 separators)
            q = todo.pop()
            if q in starts or q + 8 > len(d) or d[q] not in (1, 2, 3):
                continue
            starts.add(q)
            nxt = q + 1 + struct.unpack_from("<H", d, q + 1)[0]  # length counts from after the flag
            while nxt < len(d) and d[nxt] == 0:
                nxt += 1
            todo.append(nxt)
        for q in sorted(starts):
            flags, idx = struct.unpack_from("<H", d, q + 3)[0], d[q + 5]
            if flags & ~0x8000 or not 0 < idx < len(names) or not names[idx]:
                continue
            at = q + (9 if flags & 0x8000 else 7)
            if at + 1 >= len(d):
                continue
            cb = d[at]
            cls = CLASS_BY_BLOB.get(cb)
            if cb == 0xFF:
                cls = d[at + 2:at + 2 + d[at + 1]].decode("latin-1")
            if cls:
                out.setdefault((names[0], names[idx]), cls)
    return out


def _slot_refs(segs: list[Segment], rt: "Runtime") -> dict[int, dict[int, str]]:
    """code segment -> {slot: 'control' | 'form'} for the references its code makes."""
    out: dict[int, dict[int, str]] = {}
    for p in find_procs(segs):
        for i in decode(rt, segs[p.segment - 1].data, p)[0]:
            kind = {"CONTROL": "control", "CTLARRAY": "control", "FORM": "form",
                    "OBJVAR": "objvar", "PGET_ME": "meprop", "PSET_ME": "meprop"}.get(NAMES.get(i.op))
            if kind is None and is_objarr(rt, i):
                kind = "objarr"
            if kind and not (kind == "objarr" and p.segment in out
                             and out[p.segment].get(struct.unpack_from("<H", i.operand, 2)[0]) not in (None, "objarr")):
                slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                out.setdefault(p.segment, {})[slot] = kind
    return out


def is_objarr(rt: "Runtime", i: "Insn") -> bool:
    """Unnamed array-element load/store (`argc, slot`) — e.g. `Forms(i)`,
    `Document(i)` of `Global Document() As New frmNotePad`."""
    return i.op not in NAMES and len(i.operand) == 4 and (rt.opcode_id(i.op) or 0) & 0xFF in (0x0E, 0x0F)


def objvar_kind(kind: str, w0: int, w1: int, w2: int) -> int | None:
    """Declared class kind of an object variable's data-image record, or None.
    Module-level `kind, 0, 0`; local `kind, frame offset, frame offset`
    (negative); parameter `kind, bp offset` (positive); a global used from
    another module (loaded by FORM) `kind, global offset`. Kind 1 = Form,
    4 = Control (generic, late-bound), else a control class kind."""
    ok = lambda k: k in (1, 4) or k in CLASS_BY_KIND
    if kind == "form" and 0 < w1 < 0x8000 and not w1 & 1 and ok(w0):
        return w0
    if kind not in ("objvar", "control"):
        return None
    if w1 == w2 == 0 and ok(w0):
        return w0
    if kind == "objvar" and w1 >= 0xFF00 and w2 >= 0xFF00 and ok(w0):
        return w0
    if kind == "objvar" and 6 <= w1 < 0x100 and not w1 & 1 and ok(w0):
        return w0
    return None


def resolve_symbols(segs: list[Segment], rt: "Runtime", res: dict[int, bytes]) -> dict[int, dict[int, str]]:
    """code segment -> {slot: name} for control and form references.

    RT_RCDATA 2 holds each module's initial data image as a chunk
    `u16 length, 00 00, 1E 00, ...`; slot offsets count from the chunk
    start. A control slot holds `u16 kind, u16 0x8000|name index, u16 0`
    (name index into the form's name table); a form slot holds
    `u16 0x8000|NN, u16 global offset`, NN = base + the form's project
    index, base = the smallest NN in the global per-form run
    (`NN 80 00 00 00 00` x forms). Each segment's image is the first chunk
    (after the previous segment's) where every referenced slot is valid."""
    forms = form_names(res)
    d = res.get(2, b"")
    form_base, global_at = None, None
    for m in re.finditer(rb"(?:[\x00-\xff]\x80\x00\x00\x00\x00)+", d):
        nn = [m.group(0)[i] for i in range(0, len(m.group(0)), 6)]
        if global_at is None and any(x >= 0x40 for x in nn):
            global_at = m.start()  # global object table: lives in the global data block
        for k in range(len(nn) - len(forms) + 1):
            w = nn[k:k + len(forms)]
            if forms and min(w) >= 0x40 and sorted(w) == list(range(min(w), min(w) + len(forms))):
                form_base = min(w)
                break
        if form_base is not None:
            break
    if form_base is None:
        # Global object numbers: forms follow 0x46 + one per VBX file and one
        # per VBX control class (both listed in the project directory, RT_RCDATA 1).
        form_base = 0x46 + len(vbx_entries(res.get(1, b"")))
    chunks = [m.start() for m in re.finditer(rb"(?=..\x00\x00\x1e\x00)", d, re.S)]
    # The global data block (holding the global object table) is no module's image.
    chunks = [c for c in chunks
              if global_at is None or not c <= global_at < c + 2 + struct.unpack_from("<H", d, c)[0]]
    refs = _slot_refs(segs, rt)
    code_segs = sorted({p.segment for p in find_procs(segs)})

    def names_at(base: int, seg: int, fi: int) -> dict[int, str] | None:
        out, types, mep = {}, {}, {}
        for slot, kind in refs[seg].items():
            if base + slot + 6 > len(d):
                return None
            w0, w1, w2 = struct.unpack_from("<HHH", d, base + slot)
            if kind in ("objvar", "objarr") and w0 >> 8 == 0x80 and form_base is not None:
                # `As New frmX` / form-typed: `0x8000|NN, frame or global offset`
                k = (w0 & 0xFF) - form_base
                if 0 <= k < len(forms):
                    types[slot] = forms[k][0]
                elif kind == "objarr" and (w0 & 0xFF) in BUILTIN_OBJECTS:
                    types[slot] = BUILTIN_OBJECTS[w0 & 0xFF]
                continue
            if kind == "objarr":
                continue
            if kind == "meprop":  # PGET_ME/PSET_ME: form property `u16 0x40xx, u16 0xC0nn`,
                if w0 >> 8 == 0x40 and w1 >> 8 == 0xC0:  # or a control (its default property)
                    mep[slot] = w1 & 0xFF
                    continue
                kind = "control"
            tk = objvar_kind(kind, w0, w1, w2)
            if tk is not None:
                types[slot] = {1: "Form", 4: "Control"}.get(tk) or CLASS_BY_KIND[tk]
                continue
            if kind == "objvar":
                continue  # untyped (As Control/Form generic) or not in this image
            if kind == "control":
                idx = w1 & 0x7FFF
                if not (w1 & 0x8000 and w2 == 0 and w0 >> 8 == 0x40) or fi < 0 \
                        or idx >= len(forms[fi]) or not forms[fi][idx]:
                    return None
                out[slot] = forms[fi][idx]
                KINDS[forms[fi][idx]] = w0 & 0xFF
                CLASSES[(forms[fi][0], forms[fi][idx])] = CLASS_BY_KIND.get(w0 & 0xFF, "?")
            else:
                if w0 >> 8 != 0x80:
                    continue  # object variable (Dim x As Control/Form), not a form
                k = (w0 & 0xFF) - form_base if form_base is not None else -1
                out[slot] = forms[k][0] if 0 <= k < len(forms) else BUILTIN_OBJECTS.get(w0 & 0xFF, f"obj#{w0 & 0xFF:#x}")
        OBJVAR_TYPES[seg] = types  # last evaluated candidate; the chosen one is re-evaluated
        MEPROPS[seg] = mep
        return {**out, **{-k - 1: t for k, t in types.items()}} if out or types or mep else None

    # Segments are modules (no controls) then forms with code, in project
    # order; forms without code have no segment, so each segment's form is
    # the next one whose name table fits its control references.
    proc_names(segs, rt, res)  # fills RECORD_FORM
    form_index = {t[0]: k for k, t in enumerate(forms)}
    seg_known = {}
    for p in find_procs(segs):
        if p.record in RECORD_FORM:
            seg_known[p.segment] = form_index.get(RECORD_FORM[p.record])
    result, ci, fi = {}, 0, 0
    for seg in code_segs:
        if seg in seg_known and seg_known[seg] is not None:
            fi = seg_known[seg]
        if seg not in refs:
            continue
        uses_controls = "control" in refs[seg].values()
        cands = [seg_known[seg]] if seg_known.get(seg) is not None else range(fi, len(forms))
        for f in (cands if uses_controls else [-1]):
            scored = [(sum(k >= 0 for k in g), len(g), -j, j, g) for j in range(ci, len(chunks))
                      if (g := names_at(chunks[j], seg, f)) is not None]
            got = max(scored)[3:] if scored else None  # most names, then typed variables, earliest
            if got:
                names_at(chunks[got[0]], seg, f)  # leave OBJVAR_TYPES for the chosen image
                got = (got[0], {k: v for k, v in got[1].items() if k >= 0})
            if got:
                ci, result[seg] = got[0] + 1, got[1]
                SEG_IMAGE[seg] = chunks[got[0]]
                if f >= 0:
                    SEG_FORM[seg] = forms[f][0]
                    fi = f + 1
                break
    return result


_CTL_HEADER = re.compile(rb"[\x01\x03](..)\x00\x00(.)(.)(.)\xff", re.S)


def proc_names(segs: list[Segment], rt: "Runtime", res: dict[int, bytes]) -> dict[int, str]:
    """Procedure record -> event procedure name (`control_Event`,
    `Form_Event`). Each form blob's control records end with an event table:
    `FF, u8 count (= the class's event count), count x u16` where a
    non-zero entry is the handler's procedure record offset | 1. The owning
    control is the record ending with the table (`u8 flag 1/2/3, u16
    length, u16 flags, u8 name index, ...`, class at +7, or +9 for a
    control-array element, flags & 0x8000); tables outside any control
    record are the form's own. Procedures not found here are general
    Sub/Function procedures (their names aren't stored)."""
    records = {p.record for p in find_procs(segs)}
    rt.load_project_vbx(res)
    events = rt.event_lists()
    counts = {len(v) for v in events.values()}
    out, ids = {}, sorted(res)
    for a, b in zip(ids, ids[1:]):
        if not (res[a][:2] == b"\xff\xcc" and res[b][:2] != b"\xff\xcc"):
            continue
        d, names = res[a], form_names({a: res[a], b: res[b]})[0]
        for p in range(1, len(d) - 2):
            n = d[p]
            if d[p - 1] != 0xFF or n not in counts or p + 1 + 2 * n > len(d):
                continue
            ents = struct.unpack_from(f"<{n}H", d, p + 1)
            if not any(ents) or not all(e == 0 or (e & 1 and e & ~1 in records) for e in ents):
                continue
            end = p + 1 + 2 * n
            hdr = next((q for q in range(p - 3, max(0, p - 1024), -1)
                        if d[q] in (1, 2, 3) and q + 2 + struct.unpack_from("<H", d, q + 1)[0] in (end, end + 1)), None)
            if hdr is not None:
                flags = struct.unpack_from("<H", d, hdr + 3)[0]
                idx = d[hdr + 5]
                at = hdr + (9 if flags & 0x8000 else 7)  # array element: + u8 elem, u16
                cb = d[at]
                ctl = names[idx] if idx < len(names) and names[idx] else f"ctl#{idx}"
                cls = CLASS_BY_BLOB.get(cb)
                if cb == 0xFF:  # VBX control: class name follows as a Pascal string
                    cls = d[at + 2:at + 2 + d[at + 1]].decode("latin-1")
            else:  # the form's own table: Form or MDIForm, by event count
                ctl = cls = "MDIForm" if len(events.get("MDIForm", [])) == n != len(events.get("Form", [])) else "Form"
                FORM_CLASS[names[0]] = cls
            evs = events.get(cls, [])
            if len(evs) != n:  # unknown class (e.g. a VBX control): don't guess names
                evs = []
            for k, e in enumerate(ents):
                if e:
                    out.setdefault(e & ~1, f"{ctl}_{evs[k] if k < len(evs) else f'Event{k}'}")
                    RECORD_FORM[e & ~1] = names[0]
    return out


# Handler names live in opcodes.py; unnamed ones print as op_XXXX [id].
from opcodes import NAMES  # noqa: E402


@dataclass
class Insn:
    pc: int
    op: int
    operand: bytes
    length: int | None


_CANDIDATES = (0, 2, 4, 6, 8, 1, 3, 5, 10, 12)


def decode(rt: Runtime, data: bytes, p: Proc) -> tuple[list[Insn], str | None]:
    """Decode [p.start, p.end). Opcodes whose length can't be derived
    statically are solved by constraint: the only candidate length that lets
    the rest of the procedure decode to exactly p.end (ties: the smallest
    even length;
    recorded in
    rt.solved, so conflicting solutions across procedures are visible)."""
    def run(pc: int, depth: int):
        out = []
        while pc < p.end:
            (op,) = struct.unpack_from("<H", data, pc)
            if depth and not rt.plausible(op):
                return None  # a candidate length that exposes a non-handler word is wrong
            n = rt.operand_len(op)
            if n is None and op in rt.solved and len(rt.solved[op]) == 1:
                n = next(iter(rt.solved[op]))
            if n == "var":
                n = 2 + struct.unpack_from("<H", data, pc + 2)[0]
            if n is None:
                if depth >= 3:
                    return None
                fits = []
                for c in _CANDIDATES:
                    rest = run(pc + 2 + c, depth + 1)
                    if rest is not None:
                        fits.append((c, rest))
                if len(fits) > 1:
                    # Tie-break: the smallest length (a too-long guess swallows
                    # real instructions; confirmed on Len and Resume).
                    fits = sorted(fits, key=lambda f: (f[0] % 2, f[0]))[:1]  # operands are word-sized
                if len(fits) != 1:
                    return None
                c, rest = fits[0]
                rt.solved.setdefault(op, set()).add(c)
                return out + [Insn(pc, op, data[pc + 2:pc + 2 + c], c)] + rest
            out.append(Insn(pc, op, data[pc + 2:pc + 2 + n], n))
            pc += 2 + n
        return out if pc == p.end else None

    res = run(p.start, 0)
    if res is not None:
        return res, None
    # Report where plain decoding stops.
    out, pc = [], p.start
    while pc < p.end:
        (op,) = struct.unpack_from("<H", data, pc)
        n = rt.operand_len(op)
        if n == "var":
            n = 2 + struct.unpack_from("<H", data, pc + 2)[0]
        out.append(Insn(pc, op, data[pc + 2:pc + 2 + (n or 0)], n))
        if n is None:
            return out, f"unknown operand length for {op:#06x} at {pc}"
        pc += 2 + n
    return out, f"overran end ({pc} > {p.end})"


# Slot kind byte -> control class (even: single control, odd: control array).
CLASS_BY_KIND = {k: c for c, ks in {
    "PictureBox": (0x1A, 0x1B), "Label": (0x1C, 0x1D), "TextBox": (0x1E, 0x1F),
    "Frame": (0x20, 0x21), "CommandButton": (0x22, 0x23), "CheckBox": (0x24, 0x25),
    "OptionButton": (0x26, 0x27), "ComboBox": (0x28, 0x29), "ListBox": (0x2A, 0x2B),
    "HScrollBar": (0x2C, 0x2D), "VScrollBar": (0x2E, 0x2F), "Timer": (0x30, 0x31),
    "DriveListBox": (0x34, 0x35), "DirListBox": (0x36, 0x37), "FileListBox": (0x38, 0x39),
    "Menu": (0x3B, 0x3C), "Shape": (0x3E, 0x3F), "Line": (0x40, 0x41),
    "Image": (0x42, 0x43), "Data": (0x44, 0x45)}.items() for k in ks}

# Class of objects returned by object-valued properties.
OBJECT_PROPERTY_CLASS = {"Recordset": "Dynaset", "ActiveForm": "Form", "ActiveControl": None}

# FORM also pushes VB's built-in objects; their NN (outside the form range):
BUILTIN_OBJECTS: dict[int, str] = {0x08: "Forms", 0x32: "Printer", 0x33: "Screen", 0x34: "Clipboard", 0x3D: "App"}


def late_bound_names(res1: bytes, rt: "Runtime") -> dict[int, str]:
    """Late-bound property number -> name. Properties used through object
    variables (`Dim c As Control`) are numbered in first-use order, and the
    project directory (RT_RCDATA 1) stores, per class, that class's
    property-list index for each of them: `58 <class#> 00 00, kind, kind,
    47 00 00 | 47 03 00 <pstr VBX class>, u16 n, n x number, n x index`.
    The names follow from the classes' property lists."""
    props = rt.property_lists()
    out: dict[int, str] = {}
    for m in re.finditer(rb"\x58(.)\x00\x00(..)(..)\x47(\x00\x00|\x03\x00)", res1, re.S):
        q, cls = m.end(), None
        if m.group(4) == b"\x03\x00":  # VBX class: Pascal-ish name (length includes NUL)
            ln = res1[q]
            cls = res1[q + 1:q + ln].rstrip(b"\0").decode("latin-1")
            q += 1 + ln
        else:
            cls = CLASS_BY_KIND.get(struct.unpack_from("<H", m.group(2))[0])
        if q + 2 > len(res1) or cls not in props:
            continue
        (n,) = struct.unpack_from("<H", res1, q)
        if not 0 < n < 64 or q + 2 + 4 * n > len(res1):
            continue
        nums = struct.unpack_from(f"<{n}H", res1, q + 2)
        idxs = struct.unpack_from(f"<{n}H", res1, q + 2 + 2 * n)
        for num, idx in zip(nums, idxs):
            if idx < len(props[cls]) and props[cls][idx]:
                out.setdefault(num, props[cls][idx])
    return out


def late_bound_controls(res1: bytes, forms: list[list[str]]) -> dict[int, str]:
    """Late-bound control names (`frmMDI.ActiveForm.Text1`: SUBOBJ 0x00nn)
    share the late-bound numbering; each form's directory entry (`...
    NAME.FRM\0`, forms in project order) ends with `u16 n, n x number,
    n x index into the form's name table`."""
    out: dict[int, str] = {}
    for k, m in enumerate(re.finditer(rb"[\x20-\x7e]+\.FRM\x00", res1, re.I)):
        q = m.end()
        if k >= len(forms) or q + 2 > len(res1):
            break
        (n,) = struct.unpack_from("<H", res1, q)
        if not 0 < n < 64 or q + 2 + 4 * n > len(res1):
            continue
        nums = struct.unpack_from(f"<{n}H", res1, q + 2)
        idxs = struct.unpack_from(f"<{n}H", res1, q + 2 + 2 * n)
        for num, idx in zip(nums, idxs):
            if idx < len(forms[k]) and forms[k][idx]:
                out.setdefault(num, forms[k][idx])
    return out


def ole_names(res3: bytes) -> dict[int, str]:
    """OLE Automation member names (late-bound on `As Object` variables):
    RT_RCDATA 3 is `u16 length, NUL-terminated names`; PGET/PSET 0x00nn and
    PGET_IDX/PSET_IDX/OLE_CALL name operands are byte offsets into it."""
    out, q = {}, 2
    end = min(len(res3), 2 + struct.unpack_from("<H", res3)[0]) if len(res3) >= 2 else 0
    while q < end and res3[q]:
        z = res3.index(b"\0", q)
        out[q] = res3[q:z].decode("latin-1")
        q = z + 1
    return out


class Symbols:
    """Names for one executable's control/form references and properties."""

    def __init__(self, rt: "Runtime", segs: list[Segment], res: dict[int, bytes]):
        reset_state()
        self.rt = rt
        self.controls = resolve_symbols(segs, rt, res)
        self.tables = {t[0]: t for t in form_names(res)}
        self.seg_form = dict(SEG_FORM)
        self.late = late_bound_names(res.get(1, b""), rt)
        self.ole = ole_names(res.get(3, b""))
        self.late_controls = late_bound_controls(res.get(1, b""), form_names(res))
        self.objvar_types = {k: dict(v) for k, v in OBJVAR_TYPES.items()}
        self.form_class = dict(FORM_CLASS)
        self.meprops = {k: dict(v) for k, v in MEPROPS.items()}
        self.classes = dict(blob_classes(res))  # blob records first (authoritative)
        for key, cls in CLASSES.items():
            if key[0] in self.tables:
                self.classes.setdefault(key, cls)

    def annotate(self, seg: int, insns: list["Insn"]) -> list[str]:
        """Per instruction: symbolic text ('' if none) — control/form names,
        `form!control`, `Class.Property` for PGET/PSET."""
        out, other, cls = [], None, None
        sym = self.controls.get(seg, {})
        form_of_seg = self.seg_form.get(seg)
        for i in insns:
            n = NAMES.get(i.op)
            text = ""
            if n in ("CONTROL", "CTLARRAY", "FORM") and i.operand:
                slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                text = sym.get(slot, "")
            elif n in ("PGET", "PSET", "PGET_IDX", "PSET_IDX") and len(i.operand) >= 2:
                nn = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                props = self.rt.property_lists().get(cls, []) if cls else []
                if nn == 0xC0FD:  # collection (Forms, Controls)
                    text = f"{cls or '?'}.Count"
                elif nn >> 8 == 0x80 and other and (nn & 0xFF) < len(other):
                    text = f"{other[0]}!{other[nn & 0xFF]}"  # control array element: default property
                elif nn >> 8 == 0xC0 and (nn & 0xFF) < len(props):
                    text = f"{cls}.{props[nn & 0xFF]}"
                elif nn >> 8 == 0 and nn in self.ole and cls != "Control":  # OLE Automation
                    text = f"Object.{self.ole[nn]}"
                elif nn >> 8 == 0 and nn in self.late:  # late-bound (object variable)
                    text = f"?.{self.late[nn]}"
            elif n in ("PGET_ME", "PSET_ME") and i.operand:  # implicit form: `Left`; control: `Label1`
                slot = struct.unpack_from("<H", i.operand)[0]
                idx = self.meprops.get(seg, {}).get(slot)
                text = sym.get(slot, "")
                fcls = self.form_class.get(form_of_seg, "Form")
                props = self.rt.property_lists().get(fcls, [])
                if idx is not None and idx < len(props) and props[idx]:
                    text = f"{fcls}.{props[idx]}"
            elif n == "OLE_CALL" and len(i.operand) >= 4:
                text = f"Object.{self.ole.get(struct.unpack_from('<H', i.operand, 2)[0], '?')}"
            elif n in ("CTLARRAY_OF", "SUBOBJ") and len(i.operand) >= 2:
                idx = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                if idx == 0xC0FE:
                    text = "Form.Controls"
                elif idx >> 8 == 0 and idx in self.late_controls:  # late-bound control
                    text = f"?!{self.late_controls[idx]}"
                elif other and idx & 0xC000 != 0xC000 and (idx & 0x3FFF) < len(other):
                    text = f"{other[0]}!{other[idx & 0x3FFF]}"
                elif idx & 0xC000 == 0xC000 and cls:
                    props = self.rt.property_lists().get(cls, [])
                    text = f"{cls}.{props[idx & 0xFF]}" if (idx & 0xFF) < len(props) else ""
            out.append(text)
            # object class for the next property access
            if n == "FORM":
                target = text
                other = self.tables.get(target)
                typed = self.objvar_types.get(seg, {}).get(
                    struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]) if i.operand else None
                cls = typed or (self.form_class.get(target, "Form") if target in self.tables else target)
            elif (n in ("CONTROL", "CTLARRAY", "OBJVAR") or n is None and is_objarr(self.rt, i)) and i.operand:
                slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                typed = self.objvar_types.get(seg, {}).get(slot)
                if typed in self.tables:  # As New frmX (variable or array element)
                    cls, other = self.form_class.get(typed, "Form"), self.tables[typed]
                elif n is None and (typed or sym.get(slot)) == "Forms":  # Forms(i)
                    cls, other = "Form", None
                else:
                    cls = typed or (self.classes.get((form_of_seg, text)) if text else None)
                    other = self.tables.get(form_of_seg) if typed == "Form" else None
            elif n in ("CTLARRAY_OF", "SUBOBJ"):
                f, _, c = text.partition("!")
                if c:
                    cls = self.classes.get((f, c))
                elif text == "Form.Controls" or text.startswith("?!"):  # .Controls / .Controls(i) / late-bound
                    cls = "Controls" if n == "SUBOBJ" and not text.startswith("?!") else "Control"
                else:  # object-valued property (e.g. Data.Recordset)
                    cls = OBJECT_PROPERTY_CLASS.get(text.split(".")[-1])
                other = None
            elif n in ("ME", "ME_IMPLICIT"):
                cls = self.form_class.get(form_of_seg, "Form")
                other = self.tables.get(form_of_seg)
            elif n in ("OBJ", "OBJ_SELF", "PGET", "PGET_IDX") or (n or "").startswith(("LOAD", "ALOAD")):
                if n not in ("OBJ", "OBJ_SELF"):
                    cls = None  # object of unknown class (variable, property result)
                    other = None
        return out


def fmt(rt: Runtime, ins: Insn, note: str = "") -> str:
    name = NAMES.get(ins.op) or ("STMT" if rt.is_stmt(ins.op) else None)
    if name is None:
        oid = rt.opcode_id(ins.op)
        name = f"op_{ins.op:04X}" + (f" [id {oid:#x}]" if oid is not None else "")
    text = f"  {note}" if note else ""
    if ins.op == 0x389A and len(ins.operand) >= 6:  # PUSH.T
        (slen,) = struct.unpack_from("<H", ins.operand, 4)
        text = "  " + repr(ins.operand[6:6 + slen].decode("latin-1"))
    return f"  {ins.pc:5d}: {ins.op:04x} {ins.operand.hex(' '):<24s} {name}{text}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("exe", type=Path)
    ap.add_argument("--runtime", type=Path, required=True, help="path to your VBRUN300.DLL")
    ap.add_argument("--vbx-dir", type=Path, action="append", default=[],
                    help="directory with the project's VBX files (default: next to the EXE)")
    ap.add_argument("--out", type=Path, help="write listing here instead of stdout")
    ap.add_argument("--check", action="store_true", help="only report decode coverage")
    args = ap.parse_args()

    rt = Runtime(args.runtime)
    rt.vbx_dirs = [args.exe.parent] + args.vbx_dir
    segs = parse_ne(args.exe)
    procs = find_procs(segs)
    res = rcdata(args.exe)
    symbols = Symbols(rt, segs, res)

    lines, clean, failures = [], 0, []
    for p in procs:
        data = segs[p.segment - 1].data
        insns, err = decode(rt, data, p)
        clean += err is None
        if err:
            failures.append((p, err))
        lines.append(f"proc seg{p.segment}[{p.start}:{p.end}) record@{p.record} tag={p.tag:#x}"
                     + (f"   !! {err}" if err else ""))
        notes = symbols.annotate(p.segment, insns)
        lines += [fmt(rt, i, t) for i, t in zip(insns, notes)]
        lines.append("")

    # Coverage of each code segment by procedure records (should be exact).
    for seg in segs[PROC_TABLE_SEGMENT:]:
        ranges = sorted((p.start, p.end) for p in procs if p.segment == seg.index)
        pos = 0
        for s, e in ranges:
            if s != pos:
                failures.append((None, f"seg{seg.index}: gap/overlap at {pos}..{s}"))
            pos = e
        if ranges and pos != seg.length:
            failures.append((None, f"seg{seg.index}: records cover {pos} of {seg.length} bytes"))

    summary = f"{clean}/{len(procs)} procedures decoded exactly to their end offset"
    if args.check:
        print(summary)
        for p, err in failures:
            print(f"  {'seg%d[%d:%d)' % (p.segment, p.start, p.end) if p else ''} {err}")
        sys.exit(0 if not failures else 1)
    text = "\n".join(lines) + f"\n; {summary}\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
        print(summary, "->", args.out)
    else:
        print(text)


if __name__ == "__main__":
    main()
