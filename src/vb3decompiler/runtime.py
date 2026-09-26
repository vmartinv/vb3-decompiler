"""
The VBRUN300.DLL interpreter model: p-code is threaded code (each opcode
is the near address of its handler in segment 25), so operand lengths are
derived by statically exploring each x86 handler. Also the control models
(properties, events) from the runtime's data segment, the decoder
(`decode`, `Insn`) and the per-exe state tables the symbol resolver fills.
See OPCODES.md "Threaded code".
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

from capstone import CS_ARCH_X86, CS_MODE_16, Cs
from capstone.x86 import X86_OP_IMM

from .ne import Proc, parse_ne, vbx_entries
from .opcodes import NAMES

INTERPRETER_SEGMENT = 25  # 1-based, in the stock VBRUN300.DLL
RUNTIME_DATA_SEGMENT = 100  # VBRUN300's data segment (control models)

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


def parse_models(rt: Runtime, ds: bytes, mprops: list, mevents: list, min_ptr: int) -> dict[str, tuple[list, list]]:
    """Control class MODELs in a data segment (VBRUN300's or a VBX's):
    ... default name, class name, parent class, property list, event list.
    List entries: 0xFFxx = standard property/event ~w (master tables),
    else a PROPINFO/EVENTINFO pointer whose first word is the name; 0 ends.
    Fills rt's event/property type tables."""
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
        if w(r - 26) < 0x200 and "DragDrop" in evs and "MouseDown" not in evs:
            # a VB1 model (usVersion 1.00): VB adds the mouse events after its own
            for e in ("MouseDown", "MouseMove", "MouseUp"):
                evs.append(e)
                if e in rt.master_event_types:
                    rt.event_types.setdefault((cls, e), rt.master_event_types[e])
        q = el
        while el and w(q) and (q - el) // 2 < len(evs):  # EVENTINFO: name, cParms, cwParms, npParmTypes
            v, k = w(q), (q - el) // 2
            if v >= 0xFF80:
                types = rt.master_event_types.get(mevents[0xFFFF - v] if 0xFFFF - v < len(mevents) else None)
            else:
                types = tuple(w(w(v + 6) + 2 * j) for j in range(min(w(v + 2), 16)))
            if types is not None and evs[k]:
                rt.event_types.setdefault((cls, evs[k]), types)
            q += 2
        # A misaligned read of another MODEL can yield this class name with no
        # events (e.g. DirListBox's parent "ListBox"); prefer one with events.
        if cls not in found or (not found[cls][1] and evs):
            found[cls] = (props, evs)
            rt.model_flags[cls] = int.from_bytes(ds[r - 24:r - 20], "little")  # MODEL.fl
            rt.model_version[cls] = int.from_bytes(ds[r - 26:r - 24], "little")  # MODEL.usVersion
            q, types, std = pl, [], []
            while w(q) and len(types) < len(props):  # PROPINFO: name, fl (low byte = DT_ data type)
                v = w(q)
                types.append((rt.master_prop_types[0xFFFF - v] if 0xFFFF - v < len(rt.master_prop_types) else 0)
                             if v >= 0xFF80 else ds[v + 2] if v + 2 < len(ds) else 0)
                std.append(v >= 0xFF80)
                q += 2
            rt.prop_types[cls] = types
            rt.prop_std[cls] = std
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
        # control class MODELs (parse_models) and the master tables (_masters)
        self.event_types: dict[tuple[str, str], tuple[int, ...]] = {}  # (class, event) -> parameter types
        self.prop_types: dict[str, list[int]] = {}  # class -> PROPINFO data type per property-list entry
        self.prop_std: dict[str, list[bool]] = {}  # class -> property-list entry is a standard (master) property
        self.model_flags: dict[str, int] = {}  # class -> MODEL.fl
        self.model_version: dict[str, int] = {}  # class -> MODEL.usVersion
        self.master_prop_types: list[int] = []  # standard property -> data type
        self.master_event_types: dict[str, tuple[int, ...]] = {}  # standard event -> parameter types

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
                        self.master_prop_types.append(self.data[w(k) + 2] if w(k) + 2 < len(self.data) else 0)
                        k += 2
                if not events and name(w(w(m))) == "Click" and name(w(w(m + 2))) == "DblClick" \
                        and name(w(w(m + 4))) == "DragDrop":
                    k = m
                    while name(w(w(k))):
                        events.append(name(w(w(k))))
                        v = w(k)  # EVENTINFO: name, cParms, cwParms, npParmTypes
                        self.master_event_types[events[-1]] = tuple(w(w(v + 6) + 2 * j)
                                                                     for j in range(min(w(v + 2), 16)))
                        k += 2
            self._mst = (props, events)
        return self._mst

    def _models(self) -> dict[str, tuple[list, list]]:
        if not hasattr(self, "_mdl"):
            self._mdl = parse_models(self, self.data, *self._masters(), min_ptr=0x1000)
        return self._mdl

    def load_vbx(self, path: Path) -> list[str]:
        """Adds a VBX's control classes (from its data segment's MODELs).
        Returns the class names found."""
        raw = path.read_bytes()
        (ne,) = struct.unpack_from("<H", raw, 0x3C)
        (auto,) = struct.unpack_from("<H", raw, ne + 0x0E)
        segs = parse_ne(path)
        found = parse_models(self, segs[auto - 1].data, *self._masters(), min_ptr=2)
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
            if pc + 2 > min(p.end, len(data)):
                return None  # an odd candidate length ran past the end
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
