#!/usr/bin/env python3
"""
Lifts p-code statements (one statement marker to the next) back to BASIC
source by evaluating them on a symbolic expression stack.

Semantics come from handler names (tools/opcodes.py): LOAD./ALOAD. push a
variable, STORE./ASTORE. assign, operator families (ADD, EQ, ...) combine,
CVT.* are transparent, builtins apply to their arguments.

  python3 tools/lift.py score <corpus-dir> --runtime VBRUN300.DLL [-v]

`score` lifts every aligned corpus statement and compares it with the real
source line, identifiers normalised (variable names aren't stored).
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from opcodes import METHODS, NAMES, SEM  # noqa: E402

BINOPS = {  # family -> (text, precedence; higher binds tighter)
    "IS": ("Is", 3), "LIKE": ("Like", 3),
    "POW": ("^", 10), "MUL": ("*", 8), "DIV": ("/", 8), "IDIV": ("\\", 7), "MOD": ("Mod", 6),
    "ADD": ("+", 5), "SUB": ("-", 5), "CONCAT": ("&", 4),
    "EQ": ("=", 3), "NE": ("<>", 3), "LT": ("<", 3), "LE": ("<=", 3), "GT": (">", 3), "GE": (">=", 3),
    "AND": ("And", 1.5), "OR": ("Or", 1.2), "XOR": ("Xor", 1.1), "EQV": ("Eqv", 1.05), "IMP": ("Imp", 1.0),
}
UNOPS = {"NEG": ("-", 9), "NOT": ("Not ", 2)}
FUNCS = {  # builtin -> arity
    "Rnd": 0, "Timer": 0, "Now": 0, "Err": 0, "Error$": 0,
    "Int": 1, "Fix": 1, "Abs": 1, "CInt": 1, "Str$": 1, "Val": 1, "Len": 1, "Chr$": 1, "Asc": 1,
    "UCase$": 1, "QBColor": 1, "IsDate": 1, "CVDate": 1, "CStr": 1, "Minute": 1, "Time": 0,
    "Left$": 2, "Shell": 2, "Format$": 2, "InStr": 2, "Mid$": 3, "InputBox$": 3,
    "RGB": 3, "Trim$": 1, "Format$.1": 1,
}
STATEMENT_FUNCS = {"MsgBox": 3, "DoEvents": 0, "Cls": 0, "Beep": 0, "ChDir": 1, "ChDrive": 1}
FUNCTION_FORMS = {"MsgBox.fn": ("MsgBox", 3)}
MISSING_TEXT = "\0missing"
DIM_MARK = "\0dim"


class E:
    """Expression with a precedence (for parenthesising)."""
    def __init__(self, text: str, prec: float = 99, t: str = ""):
        self.text, self.prec, self.t = text, prec, t  # t: result type letter ('&' prefix: by reference)

    def at(self, prec: float) -> str:
        return self.text if self.prec >= prec else f"({self.text})"


CMP_FAMILIES = {"EQ", "NE", "LT", "LE", "GT", "GE", "LIKE", "IS", "TYPEOF_IS"}


def result_type(name: str) -> str:
    """Type of the value an instruction leaves on the stack (V I L S D C T,
    'L/T' when the handler is shared; '&x' for an address)."""
    fam, _, suf = name.partition(".")
    suf = suf.rsplit(".", 1)[-1] if suf else ""
    if fam.startswith("CVT"):
        tgt = name.rsplit(">", 1)[-1]
        return {"R8": "D", "Ttmp": "T", "": ""}.get(tgt, tgt)
    if fam in CMP_FAMILIES:
        return "I"
    if fam.startswith(("ADDR", "AADDR", "FIELD_ADDR")):
        return "&" + (suf if suf in ("V", "T") else "")
    if fam == "PUSH":
        return {"B": "I", "R8": "D"}.get(suf.split(" ")[0], suf.split(" ")[0])
    if name.startswith("LOAD.UDT"):
        return "&"  # a Type variable: passed by reference (DLL: `As Any`)
    if fam == "BYVAL":
        return "*"  # `ByVal x` passed to an `As Any` parameter
    if fam.startswith(("PGET", "PGET_ME")):
        return "V"
    if fam.endswith("$"):
        return "T"
    return {"R8": "D"}.get(suf, suf) if suf in ("I", "L", "S", "D", "C", "T", "V", "R8", "L/T") else ""


def is_call(text: str) -> bool:
    """`Name$` or `Name$(...)` spanning the whole text."""
    m = re.match(r"^\w+\$(\()?", text)
    if not m:
        return False
    if not m.group(1):
        return m.end() == len(text)
    depth, quoted = 0, False
    for k in range(m.end() - 1, len(text)):
        c = text[k]
        if c == '"':
            quoted = not quoted
        elif not quoted and c in "()":
            depth += 1 if c == "(" else -1
            if depth == 0:
                return k == len(text) - 1
    return False


def slot_of(operand: bytes) -> int:
    return struct.unpack_from("<H", operand, len(operand) - 2)[0] if len(operand) >= 2 else 0


def var_name(name: str, operand: bytes) -> str:
    scope = name.split(".")[1] if "." in name else "v"
    return f"{scope.lower()}{slot_of(operand):x}"


STATEMENT_PREFIX = {"CASE"}  # block-end jumps opening ElseIf/Case lines


def lift(code: list[tuple[int, bytes]], ids: dict[int, int] | None = None,
         extra: dict[int, tuple] | None = None, names: list[str | None] | None = None,
         calls: list | None = None) -> str:
    """code: [(handler, operand)] for one statement (marker excluded).
    ids: handler -> interpreter opcode ID, used to classify unnamed handlers.
    names: per instruction, the recovered name of its variable/control/
    procedure/member ('' = default property); None keeps a placeholder.
    calls: if given, receives (handler, operand, argument types) per call."""
    st: list[E] = []
    out: list[str] = []
    prefix = ""
    local = ""
    obj_at: list[int] = []  # stack depth just after a method's object was pushed
    ret_value: list[int] = []  # a pending method call is used as a value
    gfx: list[tuple[str, int]] = []  # (object prefix, stack mark) for graphics/Print methods
    print_items: list[str] = []

    def pop() -> E:
        return st.pop() if st else E("?")

    def nm(default: str) -> str:
        return names[k] if names and k < len(names) and names[k] is not None else default

    def member(o: str, default: str) -> str:  # property access; '' = default property
        t = nm(default)
        return o if t == "" else (f"{o}.{t}" if o else t)

    def family(op: int, name: str) -> str:
        if op in NAMES:
            return name
        low = (ids or {}).get(op, 0) & 0xFF
        return {0x0B: "LOAD.X", 0x0C: "STORE.X", 0x0E: "ALOAD.X", 0x0F: "ASTORE.X"}.get(low, name)

    prev_name, prev_top = "", None

    def settle():  # type the value the previous instruction pushed
        if st and st[-1] is not prev_top and not st[-1].t:
            st[-1].t = result_type(prev_name)

    for k, (op, operand) in enumerate(code):
        settle()
        prev_top = st[-1] if st else None
        prev_name = NAMES.get(op, "")
        sem = (extra or {}).get(op) or SEM.get(op)
        if sem:
            kind, fn, n = sem
            if kind == "pass":
                continue
            args = [pop() for _ in range(n)][::-1]
            while args and args[-1].text == MISSING_TEXT:
                args.pop()
            text = ", ".join("" if a.text == MISSING_TEXT else a.text for a in args)
            if kind == "fn":
                st.append(E(fn + (f"({text})" if n else "")))
            elif kind == "kw":  # keyword statement with bare args: `Kill f`
                out.append(f"{fn} {text}".rstrip())
            elif kind == "push":
                st.append(E(fn))
            continue
        name = family(op, NAMES.get(op, f"op_{op:04X}"))
        fam = name.split(".")[0].split(" ")[0].rstrip("?")
        if name == "PAREN":  # explicit parentheses in the source (kept for the IDE's listing)
            if st:
                e = pop()
                st.append(E(f"({e.text})", 99, e.t))
            continue
        if fam.startswith("CVT") and st:
            st[-1].t = result_type(name)
        if name == "CVT.Ttmp>V" and st and is_call(st[-1].text):
            st[-1].text = re.sub(r"^(\w+)\$", r"\1", st[-1].text)  # Variant form: Left(...), not Left$(...)
        if fam.startswith("CVT") or name in ("ARGS", "ARGS_FREE", "END_CALL", "TRAP", "LABEL", "NARGS",
                                             "ARG_STR", "ARG_V", "ARG_S", "ARG_D", "ARGS_DLL",
                                             "ARG_T_BYREF", "ARG_PAREN", "ARG_TEMP") or fam in STATEMENT_PREFIX:
            continue
        if name in ("OBJ", "OBJ_SELF"):
            if name == "OBJ_SELF" or not obj_at or obj_at[-1] != len(st):
                obj_at.append(len(st))
            continue
        if name.startswith(("LOAD.", "PGET_ME", "ADDR")):
            st.append(E(nm(var_name(name, operand))))
        elif name in ("CONTROL", "FORM", "OBJVAR"):
            st.append(E(nm(var_name(name, operand))))
        elif name == "ME":
            st.append(E("Me"))
        elif name == "ME_IMPLICIT":
            st.append(E(""))
            obj_at.append(len(st))
        elif name == "METHOD":
            base = obj_at.pop() if obj_at else len(st)
            args = st[base:]
            del st[base:]
            o = pop()
            while args and args[-1].text == MISSING_TEXT:
                args.pop()
            m = METHODS.get(operand[6] if len(operand) > 6 else -1, f"Method{operand[6]:x}")
            call = (f"{o.text}." if o.text else "") + m
            argtext = ", ".join("" if a.text == MISSING_TEXT else a.text for a in args)
            if ret_value:
                ret_value.pop()
                st.append(E(f"{call}({argtext})"))
            else:
                out.append((call + " " + argtext).rstrip())
        elif name == "OLE_CALL":  # obj.Name args (late-bound OLE Automation)
            o = pop()
            n = struct.unpack_from("<H", operand)[0]
            args = [pop() for _ in range(n)][::-1]
            out.append(f"{o.text}.{nm(f'm{slot_of(operand):x}')} {', '.join(a.text for a in args)}".rstrip())
        elif name in ("CALL", "CALL_FN"):
            n, rec = struct.unpack_from("<HH", operand)
            args = [pop() for _ in range(n)][::-1]
            if calls is not None:
                calls.append((name, operand, [a.t for a in args], [a.text for a in args]))
            fn = nm(f"proc{rec & 0xFFF8:x}")
            if name == "CALL_FN":
                st.append(E(f"{fn}({', '.join(a.text for a in args)})"))
            else:
                out.append((fn + " " + ", ".join(a.text for a in args)).rstrip())
        elif name == "CTLARRAY":
            st.append(E(f"{nm(var_name(name, operand))}({pop().text})"))
        elif name == "CTLARRAY_OF":
            o, i = pop(), pop()
            sep = "!" if op == 0x4EA9 else "."  # 4EA9 `a!b(i)`, 4EB0 `a.b(i)`
            st.append(E(f"{o.text}{sep}{nm(f'c{slot_of(operand) & 0x3FFF:x}')}({i.text})"))
        elif name == "SUBOBJ":
            o, sub = pop(), slot_of(operand)
            sep = "!" if op == 0x4A57 else "."  # 4A57 `a!b`, 4A63 `a.b`
            st.append(E(member(o.text, f"p{sub & 0xFF:x}") if sub & 0xC000 == 0xC000
                        else f"{o.text}{sep}{nm(f'c{sub & 0x3FFF:x}')}"))
        elif name == "ARG_MISSING":
            st.append(E(MISSING_TEXT))
        elif name in ("GFX", "GFX_FN", "PRINT_BEGIN"):
            if name == "PRINT_BEGIN" and gfx:
                continue  # Debug/file target already opened the method
            o = pop().text if st else ""
            gfx.append(((o + ".") if o else "", len(st)))
        elif name == "DEBUG":
            st.append(E("Debug"))
        elif name == "PRINT#":
            num = pop().text
            gfx.append((f"\0file{num}", len(st)))
        elif name in ("PT", "PT_TO", "PT_STEP_TO"):
            y, x = pop(), pop()
            pre = {"PT": "", "PT_TO": "-", "PT_STEP_TO": "-Step"}[name]
            st.append(E(f"{pre}({x.text}, {y.text})"))
        elif name == "CIRCLE_C":
            st.append(E("\0color"))
        elif name in ("LINE", "LINE_C", "CIRCLE", "PSET_C", "PSET_P", "SCALE"):
            o, mark = gfx.pop() if gfx else ("", len(st))
            parts = [e.text for e in st[mark:]]
            del st[mark:]
            if name.startswith("LINE"):
                pts = "".join(t for t in parts if t.startswith(("(", "-")))
                rest = [t for t in parts if not t.startswith(("(", "-"))]
                flag = {1: "B", 2: "BF"}.get(slot_of(operand), "")
                args = [pts] + (rest if name == "LINE_C" else ([""] if flag else [])) + ([flag] if flag else [])
                out.append(f"{o}Line " + ", ".join(args))
            elif name == "CIRCLE":
                out.append(f"{o}Circle " + ", ".join(t for t in parts if t != "\0color"))
            elif name in ("PSET_C", "PSET_P"):
                out.append(f"{o}PSet " + ", ".join(parts))
            else:
                out.append(f"{o}Scale")
        elif name in ("PRINT_NL", "PRINT_COMMA"):
            print_items.append(pop().text)
            if name == "PRINT_COMMA":
                continue
            o, mark = gfx.pop() if gfx else ("", len(st))
            del st[mark:]
            if o.startswith("\0file"):
                out.append(f"Print {o[5:]}, " + ", ".join(print_items))
            else:
                out.append(f"{o}Print " + ", ".join(print_items))
            print_items.clear()
        elif name in ("TEXTWIDTH", "TEXTHEIGHT", "POINT"):
            o, mark = gfx.pop() if gfx else ("", len(st))
            args = [e.text for e in st[mark:]]
            del st[mark:]
            fn = {"TEXTWIDTH": "TextWidth", "TEXTHEIGHT": "TextHeight", "POINT": "Point"}[name]
            st.append(E(f"{o}{fn}({', '.join(args)})"))
        elif name == "INPUT#":
            gfx.append((pop().text, len(st)))
        elif name.startswith("INPUT_ITEM"):
            print_items.append(pop().text)
        elif name == "INPUT_END":
            num, mark = gfx.pop() if gfx else ("#?", len(st))
            out.append(f"Input {num}, " + ", ".join(print_items))
            print_items.clear()
        elif name in ("GET#", "PUT#"):
            var, rec, num = pop(), pop(), pop()
            out.append(f"{'Get' if name == 'GET#' else 'Put'} {num.text}, {rec.text}, {var.text}")
        elif name == "FILENUM":
            st.append(E("#" + pop().text))
        elif name == "OPEN_LEN":
            ln, num, fname = pop(), pop(), pop()
            mode = {1: "Input", 2: "Output", 4: "Random", 8: "Append", 0x20: "Binary"}.get(slot_of(operand) & 0xFF, "?")
            out.append(f"Open {fname.text} For {mode} As {num.text} Len = {ln.text}")
        elif name == "OPEN":
            m = slot_of(operand)
            mode = {1: "Input", 2: "Output", 4: "Random", 8: "Append", 0x20: "Binary"}.get(m & 0xFF, f"Mode{m:x}")
            mode += {0x100: " Access Read", 0x200: " Access Write", 0x300: " Access Read Write"}.get(m & 0x300, "")
            num, fname = pop(), pop()
            out.append(f"Open {fname.text} For {mode} As {num.text}")
        elif name == "CLOSE":
            n = slot_of(operand)
            args = [pop() for _ in range(n)][::-1]
            out.append(("Close " + ", ".join(a.text for a in args)).rstrip())
        elif name == "AADDR.GLB":
            n = struct.unpack_from("<H", operand)[0]
            idx = [pop() for _ in range(n)][::-1]
            st.append(E(f"{nm(f'glb{slot_of(operand):x}')}({', '.join(i.text for i in idx)})"))
        elif name in ("LOAD.UDT", "LOAD.UDT_LOC"):
            st.append(E(nm(f"u{slot_of(operand):x}")))
        elif name.startswith("FIELD_SET"):
            rec, v = pop(), pop()
            out.append(f"{rec.text}.{nm(f'f{slot_of(operand):x}')} = {v.text}")
        elif name == "GOSUB":
            out.append(f"GoSub L{slot_of(operand):x}")
        elif name == "RETURN":
            out.append("Return")
        elif name == "Randomize":
            out.append("Randomize")
        elif op == 0x0DFA:  # LoadPicture: u16 0x8043, u16 argument count
            n = struct.unpack_from("<H", operand, 2)[0]
            args = [pop() for _ in range(n)][::-1]
            st.append(E(f"LoadPicture({', '.join(a.text for a in args if a.text != MISSING_TEXT)})"))
        elif name == "PGET_IDX":
            o = pop()
            n = struct.unpack_from("<H", operand)[0]
            idx = [pop() for _ in range(n)][::-1]
            st.append(E(f"{member(o.text, f'p{slot_of(operand) & 0xFF:x}')}({', '.join(i.text for i in idx)})"))
        elif name == "PSET_IDX":
            o = pop()
            n = struct.unpack_from("<H", operand)[0]
            idx = [pop() for _ in range(n)][::-1]
            v = pop()
            out.append(f"{member(o.text, f'p{slot_of(operand) & 0xFF:x}')}({', '.join(i.text for i in idx)}) = {v.text}")
        elif name == "Len.T":
            st.append(E(f"Len({pop().text})"))
        elif name == "FIELD_GET.T" or name.startswith("FIELD_GET"):
            st.append(E(f"{pop().text}.{nm(f'f{slot_of(operand):x}')}"))
        elif name == "PUSH.L":  # the entry point keeps the literal's radix: 388A hex, 388D decimal
            v = struct.unpack_from("<i", operand)[0]
            st.append(E(f"&H{v & 0xFFFFFFFF:X}&" if op == 0x388A else
                        f"{v}&" if -32768 <= v <= 32767 else str(v), t="L"))
        elif name == "DO":
            out.append("Do")
        elif name == "LOCAL":
            local = "Local "
        elif name == "EXIT_DO":
            out.append("Exit Do")
        elif name == "EXIT_FOR":
            out.append("Exit For")
        elif name == "LOOP_WHILE_JT":
            out.append(f"Loop While {pop().text}")
        elif name == "DO_UNTIL_JT":
            out.append(f"Do Until {pop().text}")
        elif name == "RESUME_LABEL":
            out.append(f"Resume L{slot_of(operand):x}")
        elif name == "RESUME_NEXT":
            out.append("Resume Next")
        elif name == "RESUME":
            out.append("Resume")
        elif name == "DIM_BOUND":
            st.append(E(DIM_MARK))  # next value is a dimension's only (upper) bound
        elif name == "ARRAY_REF":
            n, slot = struct.unpack_from("<HH", operand)
            if n & 0x8000:
                st.append(E(nm(f"a{slot:x}")))
            else:
                vals, need = [], n // 2
                while need and st:
                    v = pop()
                    if v.text == DIM_MARK:
                        vals[-1] = (vals[-1][0], True) if vals else vals
                        continue
                    vals.append((v.text, False))
                    need -= 1
                while st and st[-1].text == DIM_MARK:  # marker for the first value
                    pop()
                    vals[-1] = (vals[-1][0], True)
                vals.reverse()
                dims, d = [], 0
                while d < len(vals):
                    if vals[d][1] or d + 1 >= len(vals):
                        dims.append(vals[d][0]); d += 1
                    else:
                        dims.append(f"{vals[d][0]} To {vals[d + 1][0]}"); d += 2
                st.append(E(f"{nm(f'a{slot:x}')}({', '.join(dims)})"))
        elif name == "ARRAY_REF_LB":
            n, slot = struct.unpack_from("<HH", operand)
            vals = [pop().text for _ in range(n)][::-1]
            dims = [f"{vals[d]} To {vals[d + 1]}" for d in range(0, len(vals) - 1, 2)]
            st.append(E(f"{nm(f'a{slot:x}')}({', '.join(dims)})"))
        elif name == "RET_SLOT":
            ret_value.append(len(st))
        elif name in ("REDIM", "REDIM_PRESERVE"):
            out.append(("ReDim Preserve " if name == "REDIM_PRESERVE" else "ReDim ") + pop().text)
        elif name == "UBOUND":
            st.append(E(f"UBound({pop().text})"))
        elif name == "PUSH_NOTHING":
            st.append(E("Nothing"))
        elif name == "TYPEOF_IS":
            st.append(E(f"TypeOf {pop().text} Is {nm(f'c{slot_of(operand):x}')}", 3))
        elif name == "SET_OBJ":
            target, value = pop(), pop()
            out.append(f"Set {target.text} = {value.text}")
        elif name == "CASE_ELSE":
            out.append("Case Else")
        elif name == "NEXT_NOVAR":
            pop()
            out.append("Next")
        elif name == "PGET":
            o = pop()
            st.append(E(member(o.text, f"p{slot_of(operand) & 0xFF:x}")))
        elif name == "PSET":
            o, v = pop(), pop()
            out.append(f"{member(o.text, f'p{slot_of(operand) & 0xFF:x}')} = {v.text}")
        elif name.startswith(("STORE.", "PSET_ME")):
            out.append(f"{nm(var_name(name, operand))} = {pop().text}")
        elif fam == "ALOAD":
            n = struct.unpack_from("<H", operand)[0]
            idx = [pop() for _ in range(n)][::-1]
            st.append(E(f"{nm(var_name(name, operand))}({', '.join(i.text for i in idx)})"))
        elif fam == "ASTORE":
            n = struct.unpack_from("<H", operand)[0]
            idx = [pop() for _ in range(n)][::-1]
            val = pop()
            out.append(f"{nm(var_name(name, operand))}({', '.join(i.text for i in idx)}) = {val.text}")
        elif fam == "PUSH":
            parts = name.split(" ", 1)
            if len(parts) == 2:
                st.append(E(parts[1].rstrip("?")))
            elif name == "PUSH.T":
                ln = struct.unpack_from("<H", operand, 4)[0]
                st.append(E('"' + operand[6:6 + ln].decode("latin-1") + '"'))
            elif name == "PUSH.R8":
                st.append(E(repr(struct.unpack_from("<d", operand)[0])))
            else:
                v = struct.unpack_from("<h", operand)[0]
                st.append(E(f"&H{v & 0xFFFF:X}" if op == 0x3831 else str(v)))  # 3831: hex literal
        elif fam in BINOPS:
            b, a = pop(), pop()
            t, p = BINOPS[fam]
            st.append(E(f"{a.at(p)} {t} {b.at(p + 0.01)}", p))
        elif fam in UNOPS:
            a = pop()
            t, p = UNOPS[fam]
            st.append(E(f"{t}{a.at(p)}", p))
        elif name in FUNCTION_FORMS or name.split(".")[0] in STATEMENT_FUNCS:
            fn, n = FUNCTION_FORMS.get(name, (name.split(".")[0], STATEMENT_FUNCS.get(name.split(".")[0], 0)))
            args = [pop() for _ in range(n)][::-1]
            while args and args[-1].text == MISSING_TEXT:
                args.pop()
            text = ", ".join("" if a.text == MISSING_TEXT else a.text for a in args)
            if name in FUNCTION_FORMS:
                st.append(E(f"{fn}({text})"))
            else:
                o = pop().text if n == 0 and st and st[-1].text != MISSING_TEXT else ""
                obj = o + "." if o else ""
                out.append(f"{obj}{fn} {text}".rstrip())
        elif name in FUNCS or name.split(".")[0] in FUNCS:
            fn = name if name in FUNCS else name.split(".")[0]
            n = FUNCS[fn]
            fn = fn.split(".")[0]
            args = [pop() for _ in range(n)][::-1]
            while args and args[-1].text == MISSING_TEXT:
                args.pop()
            text = ", ".join("" if a.text == MISSING_TEXT else a.text for a in args)
            st.append(E(fn + (f"({text})" if n else "")))
        elif False:
            n = FUNCS[fn]
            args = [pop() for _ in range(n)][::-1]
            st.append(E(fn + (f"({', '.join(a.text for a in args)})" if n else "")))
        # --- statements ---------------------------------------------------
        elif name in ("JF", "JF.I"):
            out.append(f"If {pop().text} Then")
        elif name in ("IF1_JF", "IF1_JF.I"):
            prefix = f"If {pop().text} Then "
        elif name == "ELSEIF_JF":
            out.append(f"ElseIf {pop().text} Then")
        elif name == "JMP":
            out.append("Else")
        elif name == "DO_WHILE_JF":
            out.append(f"Do While {pop().text}")
        elif name == "LOOP_UNTIL_JF":
            out.append(f"Loop Until {pop().text}")
        elif name == "LOOP":
            out.append("Loop")
        elif name.startswith("SELECT."):
            out.append(f"Select Case {pop().text}")
        elif name.startswith("CASE_VAL."):
            pass
        elif name.startswith("CASE_IS."):
            op_text = {"GT": ">", "LT": "<", "GE": ">=", "LE": "<=", "NE": "<>"}[name.split(".")[1]]
            st.append(E(f"Is {op_text} {pop().text}"))
        elif name == "BYVAL":
            st.append(E(f"ByVal {pop().text}"))
        elif name == "SEEK":
            pos, num = pop(), pop()
            out.append(f"Seek {num.text}, {pos.text}")
        elif name == "ERR_SET":
            out.append(f"Err = {pop().text}")
        elif name in ("GET#_NOREC", "PUT#_NOREC"):
            var, num = pop(), pop()
            out.append(f"{'Get' if name.startswith('GET') else 'Put'} {num.text}, , {var.text}")
        elif name.startswith("FIELD_ADDR"):
            st.append(E(f"{pop().text}.{nm(f'f{slot_of(operand):x}')}"))
        elif name.startswith("CASE_EQ."):
            if out and out[-1].startswith("Case ") and out[-1] != "Case Else":
                out[-1] += f", {pop().text}"  # Case a, b
            else:
                out.append(f"Case {pop().text}")
        elif name == "ENDIF":
            out.append("End If")
        elif name in ("END_SELECT",):
            out.append("End Select")
        elif name in ("FOR", "FOR_STEP", "FOR.I", "FOR.L"):
            step = pop().text if name == "FOR_STEP" else None
            b, a, v = pop(), pop(), pop()
            out.append(f"For {v.text} = {a.text} To {b.text}" + (f" Step {step}" if step else ""))
        elif name in ("NEXT", "NEXT.I", "NEXT.L"):
            out.append(f"Next {pop().text}")
        elif name == "EXIT":
            out.append("Exit Sub")
        elif name == "END":
            out.append("End")
        elif name == "GOTO":
            out.append(f"GoTo L{slot_of(operand):x}")
        elif name == "ON_ERROR_GOTO":
            t = slot_of(operand)
            out.append({0xFFFF: f"On {local}Error GoTo 0", 0xFFFE: f"On {local}Error Resume Next"}.get(
                t, f"On {local}Error GoTo L{t:x}"))
        elif name == "UNLOAD":
            out.append(f"Unload {pop().text}")
        elif name == "LOAD":
            out.append(f"Load {pop().text}")
        else:
            out.append(f"<{name}>")
    settle()
    if len(out) > 1 and out[0] == "Else":  # single-line Else: `Else stmt`
        out = ["Else " + out[1]] + out[2:]
    text = "; ".join(out) if out else (st[-1].text if st else "")
    return prefix + text


# --- scoring against the corpus -------------------------------------------

KEYWORDS = {"and", "or", "not", "mod", "xor", "eqv", "imp", "if", "then", "else", "elseif", "true", "false",
            "do", "loop", "while", "until", "wend", "redim", "preserve", "set", "is", "nothing", "typeof",
            "local", "ubound", "to", "open", "input", "output", "append", "random", "binary", "as", "close",
            "gosub", "return", "randomize", "loadpicture", "access", "read", "write",
            "line", "circle", "pset", "scale", "print", "step", "debug", "textwidth", "textheight", "point",
            "get", "put", "like", "len", "seek", "err", "byval", "eof",
            "for", "to", "step", "next", "end", "exit", "sub", "function", "on", "error", "goto", "resume",
            "unload", "load", "select", "case"}


def norm(text: str) -> list[str]:
    def hexval(m):  # VB hex literals are signed: 16-bit, or 32-bit with & / more digits
        v, wide = int(m.group(1), 16), m.group(2) or len(m.group(1)) > 4
        bits = 32 if wide else 16
        return str(v - (1 << bits) if v >= 1 << (bits - 1) else v)
    text = re.sub(r"&H([0-9A-Fa-f]+)(&)?", hexval, text)
    text = text.replace("!", ".")  # a!b == a.b
    text = re.sub(r"(?i)\bexit\s+function\b", "Exit Sub", text)  # kind is the emitter's job
    toks = re.findall(r'"[^"]*"|[A-Za-z_]\w*[$%&!#]?|\d*\.?\d+(?:[eE][-+]?\d+)?[&%!#@]?|<>|<=|>=|\S', text)
    out = []
    for k, t in enumerate(toks):
        if k and toks[k - 1] in (".", "!") and re.match(r"[A-Za-z_]", t):
            out.append("ID")  # member name, even if it matches a builtin (.Left)
            continue
        low = t.lower()
        if t in "()":
            continue  # parentheses aren't encoded in p-code
        if t.startswith('"'):
            out.append(t)
        elif re.fullmatch(r"\d*\.?\d+(?:[eE][-+]?\d+)?[&%!#@]?", t):
            out.append(repr(float(t.rstrip("&%!#@"))))
        elif low in KEYWORDS or (low.rstrip("$") in {f.lower().split(".")[0].rstrip("$") for f in FUNCS}
                                   and (t.endswith("$") or toks[k + 1:k + 2] == ["("])):
            out.append(low.rstrip("$"))
        elif re.match(r"[A-Za-z_]", t):
            out.append("ID")
        else:
            out.append(t)
    return out


def score(corpus: Path, verbose: bool, runtime: Path | None = None) -> None:
    ids = {}
    if runtime:
        import pcode_disasm as P
        rt = P.Runtime(runtime)
        ids = {op: rt.opcode_id(op) or 0 for op in range(0, len(rt.code), 1)}
    c: Counter = Counter()
    for f in sorted(corpus.glob("*.json")):
        for e in json.loads(f.read_text()):
            src = e["src"]
            code = [(op, bytes.fromhex(x)) for op, x in e["code"][1:]]
            if not code or src.lower().startswith(("end sub", "end function")):
                continue
            got = lift(code, ids)
            c["total"] += 1
            if re.search(r"<[A-Za-z_][^>]*>", got):
                c["unsupported"] += 1
                c["op " + re.search(r"<([^>]+)>", got).group(1)] += 1
                continue
            if norm(got) == norm(src):
                c["ok"] += 1
            else:
                c["wrong"] += 1
                if verbose and c["wrong"] <= 80:
                    print(f"  {src[:60]:<60s} | {got[:60]}")
    print(f"statements: {c['ok']}/{c['total']} match, {c['wrong']} differ, {c['unsupported']} unsupported")
    for k, v in c.most_common(200):
        if k.startswith("op "):
            print(f"   {v:5d} {k[3:]}")


def infer(corpus: Path, runtime: Path | None) -> None:
    """Propose semantics for unsupported handlers: try (fn|kw|pass, name from
    the source line, arity 0-3) and keep what makes most lines match."""
    ids = {}
    if runtime:
        import pcode_disasm as P
        rt = P.Runtime(runtime)
        ids = {op: rt.opcode_id(op) or 0 for op in range(len(rt.code))}
    lines = []
    for f in sorted(corpus.glob("*.json")):
        for e in json.loads(f.read_text()):
            code = [(op, bytes.fromhex(x)) for op, x in e["code"][1:]]
            if code:
                lines.append((e["src"], code))
    unknown = Counter()
    for src, code in lines:
        got = lift(code, ids)
        for m in re.finditer(r"<op_([0-9A-F]{4})>", got):
            unknown[int(m.group(1), 16)] += 1
    for op, cnt in unknown.most_common():
        mine = [(src, code) for src, code in lines if any(o == op for o, _ in code)]
        cands = {("pass", "", 0)}
        for src, _ in mine:
            words = re.findall(r"[A-Za-z_]\w*\$?", A_strip(src))
            for w in words[:6]:
                for n in range(4):
                    cands.add(("fn", w, n))
                    cands.add(("kw", w, n))
        best = []
        for cand in cands:
            ok = sum(norm(lift(code, ids, {op: cand})) == norm(src) for src, code in mine)
            best.append((ok, cand))
        best.sort(key=lambda x: (-x[0], x[1][0] != "fn", x[1][2]))
        ok, cand = best[0]
        print(f"0x{op:04X}: {cand!r:34s} {ok}/{len(mine)}   e.g. {mine[0][0][:60]}")


def A_strip(src: str) -> str:
    return re.sub(r'"[^"]*"', "", src.split("'")[0])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score")
    s.add_argument("corpus", type=Path)
    s.add_argument("--runtime", type=Path)
    s.add_argument("-v", action="store_true")
    i = sub.add_parser("infer")
    i.add_argument("corpus", type=Path)
    i.add_argument("--runtime", type=Path)
    args = ap.parse_args()
    if args.cmd == "infer":
        infer(args.corpus, args.runtime)
    else:
        score(args.corpus, args.v, args.runtime)


if __name__ == "__main__":
    main()
