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
_BUILTIN_CALLS = {0x793F, 0x7944, 0x7958, 0x795D, 0x796C}
_BUILTIN_FARJMPS = {0x79A1, 0x79A6, 0x79B0}
# Handlers whose operand reads happen in ways the static explorer can't
# follow (frame setup / peeking ahead). Each value was confirmed by the
# whole-program check (every procedure decodes to exactly its end offset).
_MANUAL = {
    0x62E0: 4,  # Call: 2 reserved + u16 proc-record offset; builds a frame
    0x62DD: 4,  # Call variant (falls into 0x62E0)
    0x4EB0: 4,  # helper peeks es:[si+2]
    0x36DF: 2,  # builtin on one path, dispatch on the other
}


class Runtime:
    def __init__(self, dll: Path):
        segs = parse_ne(dll)
        self.code = segs[INTERPRETER_SEGMENT - 1].data
        self.md = Cs(CS_ARCH_X86, CS_MODE_16)
        self.md.detail = True
        self._insn: dict[int, object] = {}
        self._helper: dict[int, int] = {}
        self._len: dict[int, object] = {}

    def opcode_id(self, op: int) -> int | None:
        if 2 <= op < len(self.code):
            return struct.unpack_from("<H", self.code, op - 2)[0]
        return None

    def insn(self, a: int):
        if a not in self._insn:
            ok = 0 <= a < len(self.code)
            self._insn[a] = next(self.md.disasm(self.code[a:a + 16], a), None) if ok else None
        return self._insn[a]

    def _table_targets(self, t: int):
        for k in range(12):
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
        work, seen, steps, regtab = [(start, 0)], set(), 0, {}
        while work:
            a, delta = work.pop()
            while True:
                steps += 1
                if steps > maxsteps or (a, delta) in seen:
                    break
                seen.add((a, delta))
                i = self.insn(a)
                if i is None:
                    break
                m, o, nxt = i.mnemonic, i.op_str, a + i.size
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
                if m == "jmp" and o in regtab:
                    work.extend((e, delta) for e in self._table_targets(regtab[o])); break
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
                        work.extend((e, delta) for e in self._table_targets(int(mm.group(1), 16)))
                    break
                if m == "ljmp":
                    if a in _BUILTIN_FARJMPS:
                        results.add(("builtin", delta))
                    break
                if m.startswith("j") or m in ("loop", "jcxz", "loope", "loopne"):
                    work.append((i.operands[0].imm, delta)); a = nxt; continue
                a = nxt
        return results

    def operand_len(self, op: int):
        """Operand byte count, 'var' (u16 length + blob), or None if unknown."""
        if op in _MANUAL:
            return _MANUAL[op]
        if op not in self._len:
            r = self._explore(op) if op < len(self.code) else set()
            if any(isinstance(x, tuple) and x[0] == "varlen" for x in r):
                self._len[op] = "var"
            else:
                disp = {x for x in r if not isinstance(x, tuple)}
                disp |= {x[1] for x in r if isinstance(x, tuple) and x[0] == "builtin"}
                jumps = {x[1] for x in r if isinstance(x, tuple) and x[0] == "jump"}
                vals = {max(disp | jumps)} if jumps else (disp or {x[1] for x in r})
                self._len[op] = vals.pop() if len(vals) == 1 else None
        return self._len[op]

    def is_branch(self, op: int) -> bool:
        self.operand_len(op)
        return op not in _MANUAL and any(
            isinstance(x, tuple) and x[0] == "jump" for x in self._explore(op))


# ---------------------------------------------------------------------------
# Names for handlers whose meaning is confirmed (see OPCODES.md). Everything
# else prints as op_XXXX with its interpreter opcode ID.
# ---------------------------------------------------------------------------

NAMES = {
    0x494B: "STMT", 0x65D9: "RET",
    **{a: f"PUSH_I2 {n}" for n, a in enumerate(
        [0x37E5, 0x37ED, 0x37F8, 0x37FE, 0x3804, 0x380A, 0x3810, 0x3816, 0x381C, 0x3822, 0x3828])},
    0x3834: "PUSH_I2", 0x389A: "PUSH_STR",
    0x2D21: "LOAD", 0x2FD4: "STORE", 0x0EB0: "CVT_LIT",
    0x38D3: "ADD_I2", 0x38E1: "SUB_I2", 0x38EF: "MUL_I2", 0x40DF: "ADD", 0x390B: "NEG",
    0x3B89: "DIV", 0x10F1: "CVT_R8",
    0x4468: "EQ", 0x447A: "NE", 0x448C: "LE", 0x449E: "LT", 0x44B0: "GE", 0x44C2: "GT",
    0x49CE: "CVT_BOOL",
    0x34B7: "JMP_FALSE", 0x35FE: "JMP", 0x35EC: "ENDIF",
    0x1B37: "FOR", 0x1B3E: "FOR_STEP", 0x1E08: "NEXT",
    0x62E0: "CALL", 0x4A15: "PRINT",
}


@dataclass
class Insn:
    pc: int
    op: int
    operand: bytes
    length: int | None


def decode(rt: Runtime, data: bytes, p: Proc) -> tuple[list[Insn], str | None]:
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
    return out, None if pc == p.end else f"overran end ({pc} > {p.end})"


def fmt(rt: Runtime, ins: Insn) -> str:
    name = NAMES.get(ins.op)
    if name is None:
        oid = rt.opcode_id(ins.op)
        name = f"op_{ins.op:04X}" + (f" [id {oid:#x}]" if oid is not None else "")
    text = ""
    if ins.op == 0x389A and len(ins.operand) >= 6:
        (slen,) = struct.unpack_from("<H", ins.operand, 4)
        text = "  " + repr(ins.operand[6:6 + slen].decode("latin-1"))
    return f"  {ins.pc:5d}: {ins.op:04x} {ins.operand.hex(' '):<24s} {name}{text}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("exe", type=Path)
    ap.add_argument("--runtime", type=Path, required=True, help="path to your VBRUN300.DLL")
    ap.add_argument("--out", type=Path, help="write listing here instead of stdout")
    ap.add_argument("--check", action="store_true", help="only report decode coverage")
    args = ap.parse_args()

    rt = Runtime(args.runtime)
    segs = parse_ne(args.exe)
    procs = find_procs(segs)

    lines, clean, failures = [], 0, []
    for p in procs:
        data = segs[p.segment - 1].data
        insns, err = decode(rt, data, p)
        clean += err is None
        if err:
            failures.append((p, err))
        lines.append(f"proc seg{p.segment}[{p.start}:{p.end}) record@{p.record} tag={p.tag:#x}"
                     + (f"   !! {err}" if err else ""))
        lines += [fmt(rt, i) for i in insns]
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
