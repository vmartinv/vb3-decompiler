"""
Lifts p-code statements (one statement marker to the next) back to BASIC
source by evaluating them on a symbolic expression stack.

Semantics come from handler names (opcodes.py): LOAD./ALOAD. push a
variable, STORE./ASTORE. assign, operator families (ADD, EQ, ...) combine,
CVT.* are transparent, builtins apply to their arguments.
"""
from __future__ import annotations

import re
import struct

from .opcodes import METHODS, NAMES, SEM

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
RT_FUNCS = {0x44: "SavePicture", 0x8043: "LoadPicture", 0x8050: "Choose", 0x8051: "Switch", 0x805C: "Partition",
            0x805D: "IIf"}  # op 0DFA ids
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
    if fam == "AUDT":
        return "&"  # an array element's address
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


def open_mode(m: int) -> str:
    """OPEN operand: low byte the mode, high byte Access (bits 0-1) and the lock (bits 4-6)."""
    mode = {1: "Input", 2: "Output", 4: "Random", 8: "Append", 0x20: "Binary"}.get(m & 0xFF, f"Mode{m:x}")
    mode += {0x100: " Access Read", 0x200: " Access Write", 0x300: " Access Read Write"}.get(m & 0x300, "")
    return mode + {0x4000: " Shared", 0x3000: " Lock Read", 0x2000: " Lock Write",
                   0x1000: " Lock Read Write"}.get(m & 0x7000, "")


def lift(code: list[tuple[int, bytes]], ids: dict[int, int] | None = None,
         extra: dict[int, tuple] | None = None, names: list[str | None] | None = None,
         calls: list | None = None) -> str:
    """code: [(handler, operand)] for one statement (marker excluded).
    ids: handler -> interpreter opcode ID, used to classify unnamed handlers.
    names: per instruction, the recovered name of its variable/control/
    procedure/member ('' = default property); None keeps a placeholder.
    calls: if given, receives (handler, operand, argument types) per call."""
    return Lifter(ids, extra, names, calls).lift(code)


# Handlers that only mark the statement's structure (argument conversions,
# call brackets, labels): nothing to evaluate.
SKIPPED = {"ARGS", "ARGS_FREE", "END_CALL", "TRAP", "LABEL", "LABEL_WIDE", "NARGS", "ARG_STR", "ARG_V", "ARG_S",
           "ARG_D", "ARGS_DLL", "ARG_T_BYREF", "ARG_PAREN", "ARG_TEMP", "ARG_FIX", "ARG_FIX_BACK"}

# Statements without operands: handler -> text.
FIXED_STATEMENTS = {"RETURN": "Return", "Randomize": "Randomize", "DO": "Do", "EXIT_DO": "Exit Do",
                    "EXIT_FOR": "Exit For", "RESUME_NEXT": "Resume Next", "RESUME": "Resume",
                    "CASE_ELSE": "Case Else", "JMP": "Else", "LOOP": "Loop", "WEND": "Wend", "STOP": "Stop",
                    "ENDIF": "End If", "END_SELECT": "End Select", "EXIT": "Exit Sub", "END": "End"}

# Statements `<keyword> <the value on the stack>`: handler -> text before it.
VALUE_STATEMENTS = {"LOOP_WHILE_JT": "Loop While", "DO_UNTIL_JT": "Do Until",
                    "DO_WHILE_JF": "Do While", "LOOP_UNTIL_JF": "Loop Until", "ERR_SET": "Err =",
                    "WHILE": "While", "RANDOMIZE_N": "Randomize", "ERASE": "Erase", "UNLOAD": "Unload",
                    "LOAD": "Load"}


class Lifter:
    """Evaluates one statement's handlers on a symbolic expression stack.

    Each handler goes to the method of the first rule in RULES that matches
    it (rule order matters: prefix and family rules shadow later ones); the
    methods are grouped by handler family."""

    def __init__(self, ids: dict[int, int] | None, extra: dict[int, tuple] | None,
                 names: list[str | None] | None, calls: list | None):
        self.ids, self.extra, self.names, self.calls = ids, extra, names, calls
        self.st: list[E] = []
        self.out: list[str] = []
        self.prefix = ""  # a single-line If's `If c Then `
        self.local = ""  # `On Local Error`
        self.obj_at: list[int] = []  # stack depth just after a method's object was pushed
        self.call_at: list[int] = []  # len(obj_at) at each open call (ARGS)
        self.ret_value: list[int] = []  # a pending method call is used as a value
        self.gfx: list[tuple[str, int]] = []  # (object prefix, stack mark) for graphics/Print methods
        self.print_items: list[str] = []
        self.let_at: int | None = None  # `Let`: prefixes the next statement
        self.redim_as: str | None = None  # `ReDim a(n) As T`
        self.write_next = False  # `Write #`: the next Print # statement is a Write
        self.k = 0  # the current instruction's index (into names)

    # --- helpers ----------------------------------------------------------

    def pop(self) -> E:
        return self.st.pop() if self.st else E("?")

    def pop_n(self, n: int) -> list[E]:
        """The top n values, deepest first."""
        return [self.pop() for _ in range(n)][::-1]

    def indexes(self, operand: bytes) -> str:
        """An array access's indexes (their count: the operand's first word)."""
        return ", ".join(i.text for i in self.pop_n(struct.unpack_from("<H", operand)[0]))

    def nm(self, default: str) -> str:
        names, k = self.names, self.k
        return names[k] if names and k < len(names) and names[k] is not None else default

    def member(self, o: str, default: str) -> str:  # property access; '' = default property
        t = self.nm(default)
        return o if t == "" else (f"{o}.{t}" if o else t)

    @staticmethod
    def arg_list(args: list[E]) -> str:
        """Arguments with trailing omitted ones dropped (inner ones left empty)."""
        while args and args[-1].text == MISSING_TEXT:
            args.pop()
        return ", ".join("" if a.text == MISSING_TEXT else a.text for a in args)

    def family(self, op: int, name: str) -> str:
        if op in NAMES:
            return name
        ids = self.ids
        oid = (ids or {}).get(op, 0)
        low = oid & 0xFF
        if oid >> 10:  # a suffixed entry falling through to the plain handler 3, 6, ... bytes on
            plain = next((NAMES[op + k] for k in range(3, 22, 3)
                          if op + k in NAMES and ids.get(op + k) == oid & 0x3FF), None)
            if plain and plain.split(".")[0] in ("LOAD", "STORE", "ADDR", "ADDR_LOC", "ALOAD", "ASTORE", "AADDR"):
                return plain
        if low == 0x0E and oid >> 10 and any(NAMES.get(op + k) == "CALL_FN" and ids.get(op + k) == oid & 0x3FF
                                             for k in range(1, 17)):
            return "CALL_FN"  # a function call written with a type suffix: `F%(1)`
        return {0x0B: "LOAD.X", 0x0C: "STORE.X", 0x0E: "ALOAD.X", 0x0F: "ASTORE.X",
                0x13: "FIELD_ALOAD", 0x14: "FIELD_ASTORE"}.get(low, name)  # 13/14: per element type

    # --- the statement ----------------------------------------------------

    def lift(self, code: list[tuple[int, bytes]]) -> str:
        st, out = self.st, self.out
        prev_name, prev_top = "", None
        for self.k, (op, operand) in enumerate(code):
            if self.let_at is not None and len(out) > self.let_at:
                out[self.let_at] = "Let " + out[self.let_at]
                self.let_at = None
            if st and st[-1] is not prev_top and not st[-1].t:  # type what the previous instruction pushed
                st[-1].t = result_type(prev_name)
            prev_top = st[-1] if st else None
            prev_name = NAMES.get(op, "")
            sem = (self.extra or {}).get(op) or SEM.get(op)
            if sem:
                self.builtin(*sem)
                continue
            name = self.family(op, NAMES.get(op, f"op_{op:04X}"))
            prev_name = name
            fam = name.split(".")[0].split(" ")[0].rstrip("?")
            if name == "PAREN":  # explicit parentheses in the source (kept for the IDE's listing)
                if st:
                    e = self.pop()
                    st.append(E(f"({e.text})", 99, e.t))
                continue
            if fam.startswith("CVT") and st:
                st[-1].t = result_type(name)
            if name == "CVT.Ttmp>V" and st and is_call(st[-1].text):
                st[-1].text = re.sub(r"^(\w+)\$", r"\1", st[-1].text)  # Variant form: Left(...), not Left$(...)
            if name == "ARGS":  # a call's start: its method object is the first object marked after it
                self.call_at.append(len(self.obj_at))
            if name in ("ARG_S", "ARG_D", "ARG_T_BYREF") and st:  # DLL argument conversions: the declared type
                st[-1].t = {"ARG_S": "S", "ARG_D": "D", "ARG_T_BYREF": "T"}[name]  # (ARG_T_BYREF: ByVal String)
            if fam.startswith("CVT") or name in SKIPPED or fam in STATEMENT_PREFIX:
                continue
            if name in ("OBJ", "OBJ_SELF"):
                if name == "OBJ_SELF" or not self.obj_at or self.obj_at[-1] != len(st):
                    self.obj_at.append(len(st))
                continue
            route(op, name, fam)(self, name, op, operand)
        if st and st[-1] is not prev_top and not st[-1].t:
            st[-1].t = result_type(prev_name)
        if self.let_at is not None and len(out) > self.let_at:
            out[self.let_at] = "Let " + out[self.let_at]
        merged: list[str] = []  # one source line: `If c Then a Else b`, `Next j, i`
        for t in out:
            if merged and merged[-1] == "Else":
                merged[-1] = "Else " + t
            elif merged and t.startswith("Else "):  # only single-line Ifs have Else inside a line
                merged[-1] += " " + t
            elif merged and t == "Else":
                merged[-1] += " Else"
            elif merged and merged[-1].endswith(" Else"):
                merged[-1] += " " + t
            elif merged and t.startswith("Next ") and merged[-1].startswith("Next ") and len(t) > 5:
                merged[-1] += ", " + t[5:]
            else:
                merged.append(t)
        text = "; ".join(merged) if merged else (st[-1].text if st else "")
        return self.prefix + text

    def builtin(self, kind: str, fn: str, n: int) -> None:
        """A handler with a SEM entry (opcodes.py): builtin function, keyword statement or constant."""
        if kind == "pass":
            return
        text = self.arg_list(self.pop_n(n))
        if kind == "fn":
            self.st.append(E(fn + (f"({text})" if n else "")))
        elif kind == "kw":  # keyword statement with bare args: `Kill f`
            self.out.append(f"{fn} {text}".rstrip())
        elif kind == "push":
            self.st.append(E(fn))

    # --- variables, literals, arrays --------------------------------------

    def var(self, name: str, op: int, operand: bytes) -> None:
        self.st.append(E(self.nm(var_name(name, operand))))

    def store(self, name: str, op: int, operand: bytes) -> None:
        self.out.append(f"{self.nm(var_name(name, operand))} = {self.pop().text}")

    def array(self, name: str, op: int, operand: bytes) -> None:
        if name == "AADDR.GLB":
            idx = self.indexes(operand)
            self.st.append(E(f"{self.nm(f'glb{slot_of(operand):x}')}({idx})"))
        elif name.startswith("ALOAD"):
            idx = self.indexes(operand)
            self.st.append(E(f"{self.nm(var_name(name, operand))}({idx})"))
        else:  # ASTORE
            idx = self.indexes(operand)
            val = self.pop()
            self.out.append(f"{self.nm(var_name(name, operand))}({idx}) = {val.text}")

    def push(self, name: str, op: int, operand: bytes) -> None:
        st = self.st
        parts = name.split(" ", 1)
        if name == "PUSH_NOTHING":
            st.append(E("Nothing"))
        elif name == "PUSH.L":  # the entry point keeps the literal's radix: 388A hex, 388D decimal
            v = struct.unpack_from("<i", operand)[0]
            st.append(E(f"&H{v & 0xFFFFFFFF:X}&" if op == 0x388A else f"&O{v & 0xFFFFFFFF:o}&" if op == 0x3887 else
                        f"{v}&" if -32768 <= v <= 32767 else str(v), t="L"))
        elif len(parts) == 2:
            st.append(E(parts[1].rstrip("?")))
        elif name == "PUSH.T":
            ln = struct.unpack_from("<H", operand, 4)[0]
            st.append(E('"' + operand[6:6 + ln].decode("latin-1").replace('"', '""') + '"'))
        elif name == "PUSH.R8":
            st.append(E(repr(struct.unpack_from("<d", operand)[0])))
        elif name == "PUSH.S":  # shortest text that reads back as the same Single
            f = struct.unpack_from("<f", operand)[0]
            txt = next(t for d in range(1, 10) if struct.pack("<f", float(t := f"{f:.{d}g}")) == operand[:4])
            st.append(E(txt + "!", t="S"))
        elif name == "PUSH.C":
            v = struct.unpack_from("<q", operand)[0]
            txt = f"{v // 10000}" + (f".{v % 10000:04d}".rstrip("0") if v % 10000 else "")
            st.append(E(txt + "@", t="C"))
        else:
            v = struct.unpack_from("<h", operand)[0]
            st.append(E(f"&H{v & 0xFFFF:X}" if op == 0x3831 else f"&O{v & 0xFFFF:o}" if op == 0x382E
                        else str(v)))  # 3831: hex literal, 382E: octal

    def dims(self, name: str, op: int, operand: bytes) -> None:
        """Array declarations: ReDim targets and their bounds, UBound."""
        st = self.st
        if name == "DIM_BOUND":
            st.append(E(DIM_MARK))  # next value is a dimension's only (upper) bound
        elif name == "ARRAY_REF":
            n, slot = struct.unpack_from("<HH", operand)
            if n & 0x8000:
                st.append(E(self.nm(f"a{slot:x}")))
                return
            vals, need = [], n // 2
            while need and st:
                v = self.pop()
                if v.text == DIM_MARK:
                    vals[-1] = (vals[-1][0], True) if vals else vals
                    continue
                vals.append((v.text, False))
                need -= 1
            while st and st[-1].text == DIM_MARK:  # marker for the first value
                self.pop()
                vals[-1] = (vals[-1][0], True)
            vals.reverse()
            dims, d = [], 0
            while d < len(vals):
                if vals[d][1] or d + 1 >= len(vals):
                    dims.append(vals[d][0]); d += 1
                else:
                    dims.append(f"{vals[d][0]} To {vals[d + 1][0]}"); d += 2
            st.append(E(f"{self.nm(f'a{slot:x}')}({', '.join(dims)})"))
        elif name == "ARRAY_REF_LB":
            n, slot = struct.unpack_from("<HH", operand)
            vals = [self.pop().text for _ in range(n)][::-1]
            dims = [f"{vals[d]} To {vals[d + 1]}" for d in range(0, len(vals) - 1, 2)]
            st.append(E(f"{self.nm(f'a{slot:x}').rstrip('$')}$({', '.join(dims)})"))  # 0768: `ReDim a$(...)`
        elif name == "REDIM_AS":  # u16 type, u16 the text column of `As` (marked for the caller to check)
            t, at = struct.unpack_from("<HH", operand)
            self.redim_as = f"\x01{at}\x01As " + {1: "Integer", 2: "Long", 3: "Single", 4: "Double", 5: "Currency",
                                                   7: "String"}.get(t, "Variant")
        elif name in ("REDIM", "REDIM_PRESERVE"):
            self.out.append(("ReDim Preserve " if name == "REDIM_PRESERVE" else "ReDim ") + self.pop().text
                            + (f" {self.redim_as}" if self.redim_as else ""))
            self.redim_as = None
        else:  # UBOUND
            st.append(E(f"UBound({self.pop().text})"))

    def record(self, name: str, op: int, operand: bytes) -> None:
        """Type variables and their fields."""
        field = f"f{slot_of(operand):x}"
        if name == "AUDT":
            idx = self.indexes(operand)
            self.st.append(E(f"{self.nm(f'u{slot_of(operand):x}')}({idx})"))
        elif name in ("FIELD_ALOAD", "FIELD_ASTORE"):
            rec = self.pop()
            ref = f"{rec.text}.{self.nm(field)}({self.indexes(operand)})"
            if name == "FIELD_ALOAD":
                self.st.append(E(ref))
            else:
                self.out.append(f"{ref} = {self.pop().text}")
        elif name == "STORE.UDT":
            self.out.append(f"{self.nm(f'u{slot_of(operand):x}')} = {self.pop().text}")
        elif name.startswith("FIELD_SET"):
            rec, v = self.pop(), self.pop()
            self.out.append(f"{rec.text}.{self.nm(field)} = {v.text}")
        else:  # FIELD_GET, FIELD_ADDR
            self.st.append(E(f"{self.pop().text}.{self.nm(field)}"))

    # --- operators and functions ------------------------------------------

    def operator(self, name: str, op: int, operand: bytes) -> None:
        fam = name.split(".")[0].split(" ")[0].rstrip("?")
        if name == "TYPEOF_IS":
            self.st.append(E(f"TypeOf {self.pop().text} Is {self.nm(f'c{slot_of(operand):x}')}", 3))
        elif fam in BINOPS:
            b, a = self.pop(), self.pop()
            t, p = BINOPS[fam]
            self.st.append(E(f"{a.at(p)} {t} {b.at(p + 0.01)}", p))
        else:  # UNOPS
            a = self.pop()
            t, p = UNOPS[fam]
            self.st.append(E(f"{t}{a.at(p)}", p))

    def function(self, name: str, op: int, operand: bytes) -> None:
        if name == "Len.T":
            self.st.append(E(f"Len({self.pop().text})"))
        elif name in FUNCTION_FORMS or name.split(".")[0] in STATEMENT_FUNCS:
            fn, n = FUNCTION_FORMS.get(name, (name.split(".")[0], STATEMENT_FUNCS.get(name.split(".")[0], 0)))
            text = self.arg_list(self.pop_n(n))
            if name in FUNCTION_FORMS:
                self.st.append(E(f"{fn}({text})"))
            else:
                st = self.st
                o = self.pop().text if n == 0 and st and st[-1].text != MISSING_TEXT else ""
                obj = o + "." if o else ""
                self.out.append(f"{obj}{fn} {text}".rstrip())
        else:  # FUNCS
            fn = name if name in FUNCS else name.split(".")[0]
            n = FUNCS[fn]
            fn = fn.split(".")[0]
            text = self.arg_list(self.pop_n(n))
            self.st.append(E(fn + (f"({text})" if n else "")))

    def runtime_function(self, name: str, op: int, operand: bytes) -> None:
        """0DFA: u16 id, u16 argument count (a trailing missing marker aside)."""
        st = self.st
        fid, n = struct.unpack_from("<HH", operand)
        if st and st[-1].text == MISSING_TEXT and len(st) > n:
            self.pop()
        args = ", ".join(a.text for a in self.pop_n(n) if a.text != MISSING_TEXT)
        fn = RT_FUNCS.get(fid, f"RTFN{fid:X}")
        if not fid & 0x8000:  # a statement
            self.out.append(f"{fn} {args}")
        else:
            st.append(E(f"{fn}({args})"))

    # --- objects, properties, calls ---------------------------------------

    def obj(self, name: str, op: int, operand: bytes) -> None:
        """Forms, controls and their members."""
        st = self.st
        if name == "ME":
            st.append(E("Me"))
        elif name == "NEW_FORM":
            st.append(E(f"New {self.nm('?')}"))
        elif name == "ME_IMPLICIT":
            st.append(E(""))
            self.obj_at.append(len(st))
        elif name in ("CTLARRAY", "CTLARRAY_GET"):
            st.append(E(f"{self.nm(var_name(name, operand))}({self.pop().text})"))
        elif name == "CTLARRAY_SET":
            i, v = self.pop(), self.pop()
            self.out.append(f"{self.nm(var_name(name, operand))}({i.text}) = {v.text}")
        elif name == "CTLARRAY_OF":
            o, i = self.pop(), self.pop()
            sep = "!" if op == 0x4EA9 else "."  # 4EA9 `a!b(i)`, 4EB0 `a.b(i)`
            st.append(E(f"{o.text}{sep}{self.nm(f'c{slot_of(operand) & 0x3FFF:x}')}({i.text})"))
        elif name == "SUBOBJ":
            o, sub = self.pop(), slot_of(operand)
            sep = "!" if op == 0x4A57 else "."  # 4A57 `a!b`, 4A63 `a.b`
            st.append(E(self.member(o.text, f"p{sub & 0xFF:x}") if sub & 0xC000 == 0xC000
                        else f"{o.text}{sep}{self.nm(f'c{sub & 0x3FFF:x}')}"))
        elif name == "SET_OBJ":
            target, value = self.pop(), self.pop()
            self.out.append(f"Set {target.text} = {value.text}")

    def prop(self, name: str, op: int, operand: bytes) -> None:
        default = f"p{slot_of(operand) & 0xFF:x}"
        if name == "PGET":
            o = self.pop()
            self.st.append(E(self.member(o.text, default)))
        elif name == "PSET":
            o, v = self.pop(), self.pop()
            self.out.append(f"{self.member(o.text, default)} = {v.text}")
        elif name == "PGET_IDX":
            o = self.pop()
            idx = self.indexes(operand)
            self.st.append(E(f"{self.member(o.text, default)}({idx})"))
        else:  # PSET_IDX
            o = self.pop()
            idx = self.indexes(operand)
            v = self.pop()
            self.out.append(f"{self.member(o.text, default)}({idx}) = {v.text}")

    def call(self, name: str, op: int, operand: bytes) -> None:
        st, out = self.st, self.out
        if name == "METHOD":
            mark = self.call_at.pop() if self.call_at else None
            obj_at = self.obj_at
            if mark is not None and len(obj_at) > mark + 1:  # object arguments marked too (`PopupMenu mPop`)
                base = obj_at[mark]
                del obj_at[mark:]
            else:
                base = obj_at.pop() if obj_at else len(st)
            args = st[base:]
            del st[base:]
            o = self.pop()
            m = METHODS.get(operand[6] if len(operand) > 6 else -1, f"Method{operand[6]:x}")
            call = (f"{o.text}." if o.text else "") + m
            argtext = self.arg_list(args)
            if self.ret_value:
                self.ret_value.pop()
                st.append(E(f"{call}({argtext})"))
            else:
                out.append((call + " " + argtext).rstrip())
        elif name == "OLE_CALL":  # obj.Name args (late-bound OLE Automation)
            o = self.pop()
            args = self.pop_n(struct.unpack_from("<H", operand)[0])
            out.append(f"{o.text}.{self.nm(f'm{slot_of(operand):x}')} {', '.join(a.text for a in args)}".rstrip())
        elif name in ("CALL", "CALL_FN"):
            if self.call_at:
                self.call_at.pop()
            n, rec = struct.unpack_from("<HH", operand)
            args = self.pop_n(n)
            if self.calls is not None:
                self.calls.append((name, operand, [a.t for a in args], [a.text for a in args]))
            fn = self.nm(f"proc{rec & 0xFFF8:x}")
            if name == "CALL_FN":
                st.append(E(f"{fn}({', '.join(a.text for a in args)})"))
            elif op == 0x62E0:  # the Call keyword
                out.append(f"Call {fn}" + (f"({', '.join(a.text for a in args)})" if args else ""))
            else:
                out.append((fn + " " + ", ".join(a.text for a in args)).rstrip())
        elif name == "ARG_MISSING":
            st.append(E(MISSING_TEXT))
        elif name == "RET_SLOT":
            self.ret_value.append(len(st))
        else:  # BYVAL
            st.append(E(f"ByVal {self.pop().text}"))

    # --- Print and graphics methods ---------------------------------------

    def graphics(self, name: str, op: int, operand: bytes) -> None:
        st, out, gfx = self.st, self.out, self.gfx
        if name in ("GFX", "GFX_FN", "PRINT_BEGIN"):
            if name == "PRINT_BEGIN" and gfx:
                return  # Debug/file target already opened the method
            o = self.pop().text if st else ""
            gfx.append(((o + ".") if o else "", len(st)))
        elif name == "DEBUG":
            st.append(E("Debug"))
        elif name in ("PT", "PT_STEP", "PT_TO", "PT_STEP_TO"):
            y, x = self.pop(), self.pop()
            pre = {"PT": "", "PT_STEP": "Step", "PT_TO": "-", "PT_STEP_TO": "-Step"}[name]
            st.append(E(f"{pre}({x.text}, {y.text})"))
        elif name in ("CIRCLE_C", "CIRCLE_START", "CIRCLE_END", "CIRCLE_ASPECT"):  # tag the optional argument
            st[-1] = E(f"\0{name[7:] or 'C'}\0{st[-1].text}")
        elif name in ("TEXTWIDTH", "TEXTHEIGHT", "POINT"):
            o, mark = gfx.pop() if gfx else ("", len(st))
            args = [e.text for e in st[mark:]]
            del st[mark:]
            fn = {"TEXTWIDTH": "TextWidth", "TEXTHEIGHT": "TextHeight", "POINT": "Point"}[name]
            st.append(E(f"{o}{fn}({', '.join(args)})"))
        else:  # LINE, LINE_C, CIRCLE, PSET_C, PSET_P, SCALE, SCALE_PTS
            o, mark = gfx.pop() if gfx else ("", len(st))
            parts = [e.text for e in st[mark:]]
            del st[mark:]
            if name.startswith("LINE"):
                pts = "".join(t for t in parts if t.startswith(("(", "-", "Step(")))
                rest = [t for t in parts if not t.startswith(("(", "-", "Step("))]
                flag = {1: "B", 2: "BF"}.get(slot_of(operand), "")
                args = [pts] + (rest if name == "LINE_C" else ([""] if flag else [])) + ([flag] if flag else [])
                out.append(f"{o}Line " + ", ".join(args))
            elif name == "CIRCLE":  # point, radius, then the tagged optional arguments
                opt = dict(t[1:].split("\0", 1) for t in parts[2:])
                args = parts[:2] + [opt.get(k, "") for k in ("C", "START", "END", "ASPECT")]
                while args[-1] == "":
                    args.pop()
                out.append(f"{o}Circle " + ", ".join(args))
            elif name in ("PSET_C", "PSET_P"):
                out.append(f"{o}PSet " + ", ".join(parts))
            elif name == "SCALE_PTS":
                out.append(f"{o}Scale ({parts[0]}, {parts[1]})-({parts[2]}, {parts[3]})")
            else:
                out.append(f"{o}Scale")

    def print_(self, name: str, op: int, operand: bytes) -> None:
        st, gfx = self.st, self.gfx
        if name in ("PRINT_TAB", "PRINT_SPC"):
            st.append(E(f"{'Tab' if name == 'PRINT_TAB' else 'Spc'}({self.pop().text})"))
        elif name in ("PRINT_SEMI", "PRINT_COMMA"):  # untyped ones also stand alone (`Print , x`)
            item = self.pop().text if len(st) > (gfx[-1][1] if gfx else 0) else ""
            self.print_items.append(item + ("; " if name == "PRINT_SEMI" else ", "))
        else:  # PRINT_NL, PRINT_END
            o, mark = gfx.pop() if gfx else ("", len(st))
            if len(st) > mark:  # the last item (PRINT_END: a trailing Tab/Spc)
                self.print_items.append(self.pop().text)
            del st[mark:]
            items = "".join(self.print_items).rstrip()
            if o.startswith("\0file"):
                self.out.append(f"{'Write' if self.write_next else 'Print'} {o[5:]}," + (f" {items}" if items else ""))
                self.write_next = False
            else:
                self.out.append(f"{o}Print" + (f" {items}" if items else ""))
            self.print_items.clear()

    # --- file I/O ---------------------------------------------------------

    def file(self, name: str, op: int, operand: bytes) -> None:
        st, out, gfx = self.st, self.out, self.gfx
        pop = self.pop
        if name == "PRINT#":
            num = pop().text
            gfx.append((f"\0file{num}", len(st)))
        elif name == "WRITE#":
            self.write_next = True
        elif name == "INPUT#":
            gfx.append((pop().text, len(st)))
        elif name.startswith("INPUT_ITEM"):
            self.print_items.append(pop().text)
        elif name == "INPUT_END":
            num, mark = gfx.pop() if gfx else ("#?", len(st))
            out.append(f"Input {num}, " + ", ".join(self.print_items))
            self.print_items.clear()
        elif name in ("LINE_INPUT#", "LINE_INPUT#.V"):  # .V: into a Variant
            var = pop()
            num, mark = gfx.pop() if gfx else ("#?", len(st))
            out.append(f"Line Input {num}, {var.text}")
        elif name in ("GET#", "PUT#"):
            var, rec, num = pop(), pop(), pop()
            out.append(f"{'Get' if name == 'GET#' else 'Put'} {num.text}, {rec.text}, {var.text}")
        elif name in ("GET#_NOREC", "PUT#_NOREC"):
            var, num = pop(), pop()
            out.append(f"{'Get' if name.startswith('GET') else 'Put'} {num.text}, , {var.text}")
        elif name == "FILENUM":
            st.append(E("#" + pop().text))
        elif name in ("OPEN", "OPEN_LEN"):
            ln = pop() if name == "OPEN_LEN" else None
            num, fname = pop(), pop()
            out.append(f"Open {fname.text} For {open_mode(slot_of(operand))} As {num.text}"
                       + (f" Len = {ln.text}" if ln else ""))
        elif name == "CLOSE":
            args = self.pop_n(slot_of(operand))
            out.append(("Close " + ", ".join(a.text for a in args)).rstrip())
        elif name == "SEEK":
            pos, num = pop(), pop()
            out.append(f"Seek {num.text}, {pos.text}")
        elif name == "NAME":
            b, a = pop(), pop()
            out.append(f"Name {a.text} As {b.text}")
        elif name == "WIDTH#":
            w, num = pop(), pop()
            out.append(f"Width {num.text}, {w.text}")
        else:  # LOCK
            w = struct.unpack_from("<H", operand)[0]
            rec = ""  # bit 1: records given; 0x8000 one record, 0x4000 `To n` (lower bound pushed as 1)
            if w & 0x8000:
                rec = f", {pop().text}"
            elif w & 2:
                hi, lo = pop(), pop()
                rec = f", To {hi.text}" if w & 0x4000 else f", {lo.text} To {hi.text}"
            out.append(f"{'Unlock' if w & 1 else 'Lock'} {pop().text}{rec}")

    # --- control flow and other statements ------------------------------

    def fixed(self, name: str, op: int, operand: bytes) -> None:
        self.out.append(FIXED_STATEMENTS[name])

    def keyword_value(self, name: str, op: int, operand: bytes) -> None:
        self.out.append(f"{VALUE_STATEMENTS[name]} {self.pop().text}")

    def flow(self, name: str, op: int, operand: bytes) -> None:
        st, out, pop = self.st, self.out, self.pop
        if name in ("JF", "JF.I"):
            out.append(f"If {pop().text} Then")
        elif name in ("IF1_JF", "IF1_JF.I"):
            self.prefix = f"If {pop().text} Then "
        elif name == "ELSEIF_JF":
            out.append(f"ElseIf {pop().text} Then")
        elif name.startswith("SELECT."):
            out.append(f"Select Case {pop().text}")
        elif name.startswith("CASE_IS."):
            op_text = {"GT": ">", "LT": "<", "GE": ">=", "LE": "<=", "NE": "<>", "EQ": "="}[name.split(".")[1]]
            st.append(E(f"Is {op_text} {pop().text}"))
        elif name.startswith("CASE_EQ."):
            if out and out[-1].startswith("Case ") and out[-1] != "Case Else":
                out[-1] += f", {pop().text}"  # Case a, b
            else:
                out.append(f"Case {pop().text}")
        elif name in ("CASE_TO", "CASE_TO_HI"):
            hi, lo = pop(), pop()
            st.append(E(f"{lo.text} To {hi.text}"))
        elif name in ("FOR", "FOR_STEP", "FOR.I", "FOR.L"):
            step = pop().text if name.startswith("FOR_STEP") else None
            b, a, v = pop(), pop(), pop()
            out.append(f"For {v.text} = {a.text} To {b.text}" + (f" Step {step}" if step else ""))
        elif name in ("NEXT", "NEXT.I", "NEXT.L"):
            out.append(f"Next {pop().text}")
        elif name == "NEXT_NOVAR":
            pop()
            out.append("Next")
        elif name in ("ON_GOTO", "ON_GOSUB"):
            n = struct.unpack_from("<H", operand)[0] // 2
            targets = struct.unpack_from(f"<{n}H", operand, 2)
            out.append(f"On {pop().text} {'GoTo' if name == 'ON_GOTO' else 'GoSub'} "
                       + ", ".join(f"L{t:x}" for t in targets))
        elif name == "GOTO":
            out.append(f"GoTo L{slot_of(operand):x}")
        elif name == "GOSUB":
            out.append(f"GoSub L{slot_of(operand):x}")
        elif name == "LOCAL":
            self.local = "Local "
        elif name == "ON_ERROR_GOTO":
            t, local = slot_of(operand), self.local
            out.append({0xFFFF: f"On {local}Error GoTo 0", 0xFFFE: f"On {local}Error Resume Next"}.get(
                t, f"On {local}Error GoTo L{t:x}"))
        else:  # RESUME_LABEL
            t = slot_of(operand)
            out.append("Resume 0" if t == 0xFFFF else f"Resume L{t:x}")

    def statement(self, name: str, op: int, operand: bytes) -> None:
        out, pop = self.out, self.pop
        if name == "LET":
            self.let_at = len(out)
        elif name == "MID_STMT":
            target, value = pop(), pop()
            args = [pop().text for _ in range(2 if op == 0x7731 else 1)][::-1]
            out.append(f"Mid$({target.text}, {', '.join(args)}) = {value.text}")
        elif name in ("LSET", "RSET"):
            target, value = pop(), pop()
            out.append(f"{'LSet' if name == 'LSET' else 'RSet'} {target.text} = {value.text}")
        else:  # DATE$=, TIME$=, DATE=, TIME=
            out.append(f"{name[:-1].title()} = {pop().text}")

    def nothing(self, name: str, op: int, operand: bytes) -> None:
        pass

    def unknown(self, name: str, op: int, operand: bytes) -> None:
        self.out.append(f"<{name}>")


# (handler name, family, handler) -> Lifter method; the first match wins.
RULES: list[tuple] = [
    (lambda n, f, op: n.startswith(("LOAD.", "PGET_ME", "ADDR")) or n in ("CONTROL", "FORM", "OBJVAR"), Lifter.var),
    (lambda n, f, op: n in ("ME", "NEW_FORM", "ME_IMPLICIT"), Lifter.obj),
    (lambda n, f, op: n in ("METHOD", "OLE_CALL", "CALL", "CALL_FN"), Lifter.call),
    (lambda n, f, op: n in ("CTLARRAY", "CTLARRAY_GET", "CTLARRAY_SET", "CTLARRAY_OF", "SUBOBJ"), Lifter.obj),
    (lambda n, f, op: n == "ARG_MISSING", Lifter.call),
    (lambda n, f, op: n in ("GFX", "GFX_FN", "PRINT_BEGIN", "DEBUG"), Lifter.graphics),
    (lambda n, f, op: n == "PRINT#", Lifter.file),
    (lambda n, f, op: n in ("PT", "PT_STEP", "PT_TO", "PT_STEP_TO", "CIRCLE_C", "CIRCLE_START", "CIRCLE_END",
                            "CIRCLE_ASPECT", "LINE", "LINE_C", "CIRCLE", "PSET_C", "PSET_P", "SCALE", "SCALE_PTS"),
     Lifter.graphics),
    (lambda n, f, op: n in ("PRINT_TAB", "PRINT_SPC", "PRINT_SEMI", "PRINT_COMMA", "PRINT_NL", "PRINT_END"),
     Lifter.print_),
    (lambda n, f, op: n in ("TEXTWIDTH", "TEXTHEIGHT", "POINT"), Lifter.graphics),
    (lambda n, f, op: n in ("INPUT#", "INPUT_END", "GET#", "PUT#", "FILENUM", "OPEN", "OPEN_LEN", "CLOSE")
     or n.startswith("INPUT_ITEM"), Lifter.file),
    (lambda n, f, op: n == "AADDR.GLB", Lifter.array),
    (lambda n, f, op: n in ("AUDT", "FIELD_ALOAD", "FIELD_ASTORE", "STORE.UDT") or n.startswith("FIELD_SET"),
     Lifter.record),
    (lambda n, f, op: n == "GOSUB", Lifter.flow),
    (lambda n, f, op: n in ("RETURN", "Randomize"), Lifter.fixed),
    (lambda n, f, op: op == 0x0DFA, Lifter.runtime_function),
    (lambda n, f, op: n in ("PGET_IDX", "PSET_IDX"), Lifter.prop),
    (lambda n, f, op: n == "Len.T", Lifter.function),
    (lambda n, f, op: n.startswith("FIELD_GET"), Lifter.record),
    (lambda n, f, op: n == "PUSH.L", Lifter.push),
    (lambda n, f, op: n in ("DO", "EXIT_DO", "EXIT_FOR", "RESUME_NEXT", "RESUME"), Lifter.fixed),
    (lambda n, f, op: n in ("LOOP_WHILE_JT", "DO_UNTIL_JT"), Lifter.keyword_value),
    (lambda n, f, op: n in ("LOCAL", "RESUME_LABEL"), Lifter.flow),
    (lambda n, f, op: n in ("DIM_BOUND", "ARRAY_REF", "ARRAY_REF_LB"), Lifter.dims),
    (lambda n, f, op: n == "RET_SLOT", Lifter.call),
    (lambda n, f, op: n in ("REDIM_AS", "REDIM", "REDIM_PRESERVE", "UBOUND"), Lifter.dims),
    (lambda n, f, op: n == "PUSH_NOTHING", Lifter.push),
    (lambda n, f, op: n == "TYPEOF_IS", Lifter.operator),
    (lambda n, f, op: n == "SET_OBJ", Lifter.obj),
    (lambda n, f, op: n == "CASE_ELSE", Lifter.fixed),
    (lambda n, f, op: n == "NEXT_NOVAR", Lifter.flow),
    (lambda n, f, op: n in ("PGET", "PSET"), Lifter.prop),
    (lambda n, f, op: n.startswith(("STORE.", "PSET_ME")), Lifter.store),
    (lambda n, f, op: f in ("ALOAD", "ASTORE"), Lifter.array),
    (lambda n, f, op: f == "PUSH", Lifter.push),
    (lambda n, f, op: f in BINOPS or f in UNOPS, Lifter.operator),
    (lambda n, f, op: n in FUNCTION_FORMS or n.split(".")[0] in STATEMENT_FUNCS
     or n in FUNCS or n.split(".")[0] in FUNCS, Lifter.function),
    # statements
    (lambda n, f, op: n in ("JF", "JF.I", "IF1_JF", "IF1_JF.I"), Lifter.flow),
    (lambda n, f, op: n == "ELSEIF_JF", Lifter.flow),
    (lambda n, f, op: n == "JMP", Lifter.fixed),
    (lambda n, f, op: n in ("DO_WHILE_JF", "LOOP_UNTIL_JF"), Lifter.keyword_value),
    (lambda n, f, op: n == "LOOP", Lifter.fixed),
    (lambda n, f, op: n.startswith("SELECT."), Lifter.flow),
    (lambda n, f, op: n.startswith("CASE_VAL."), Lifter.nothing),
    (lambda n, f, op: n.startswith("CASE_IS."), Lifter.flow),
    (lambda n, f, op: n == "BYVAL", Lifter.call),
    (lambda n, f, op: n == "SEEK", Lifter.file),
    (lambda n, f, op: n == "ERR_SET", Lifter.keyword_value),
    (lambda n, f, op: n in ("GET#_NOREC", "PUT#_NOREC"), Lifter.file),
    (lambda n, f, op: n.startswith("FIELD_ADDR"), Lifter.record),
    (lambda n, f, op: n.startswith("CASE_EQ.") or n in ("CASE_TO", "CASE_TO_HI"), Lifter.flow),
    (lambda n, f, op: n in ("CASE_TO_LO", "CASE_TO_JMP"), Lifter.nothing),
    (lambda n, f, op: n == "WHILE", Lifter.keyword_value),
    (lambda n, f, op: n == "WEND", Lifter.fixed),
    (lambda n, f, op: n in ("ON_GOTO", "ON_GOSUB"), Lifter.flow),
    (lambda n, f, op: n in ("LET", "MID_STMT", "LSET", "RSET"), Lifter.statement),
    (lambda n, f, op: n == "STOP", Lifter.fixed),
    (lambda n, f, op: n == "RANDOMIZE_N", Lifter.keyword_value),
    (lambda n, f, op: n in ("WRITE#", "LINE_INPUT#", "LINE_INPUT#.V", "NAME", "WIDTH#"), Lifter.file),
    (lambda n, f, op: n in ("DATE$=", "TIME$=", "DATE=", "TIME="), Lifter.statement),
    (lambda n, f, op: n == "LOCK", Lifter.file),
    (lambda n, f, op: n == "ERASE", Lifter.keyword_value),
    (lambda n, f, op: n in ("ENDIF", "END_SELECT"), Lifter.fixed),
    (lambda n, f, op: n in ("FOR", "FOR_STEP", "FOR.I", "FOR.L", "NEXT", "NEXT.I", "NEXT.L"), Lifter.flow),
    (lambda n, f, op: n in ("EXIT", "END"), Lifter.fixed),
    (lambda n, f, op: n in ("GOTO", "ON_ERROR_GOTO"), Lifter.flow),
    (lambda n, f, op: n in ("UNLOAD", "LOAD"), Lifter.keyword_value),
]
_ROUTES: dict[tuple[int, str], object] = {}


def route(op: int, name: str, fam: str):
    """The Lifter method for a handler (first matching rule; cached)."""
    key = (op, name)
    if key not in _ROUTES:
        _ROUTES[key] = next((m for rule, m in RULES if rule(name, fam, op)), Lifter.unknown)
    return _ROUTES[key]
