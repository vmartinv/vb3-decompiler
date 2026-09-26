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
    st: list[E] = []
    out: list[str] = []
    prefix = ""
    local = ""
    obj_at: list[int] = []  # stack depth just after a method's object was pushed
    call_at: list[int] = []  # len(obj_at) at each open call (ARGS)
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

    prev_name, prev_top = "", None

    def settle():  # type the value the previous instruction pushed
        if st and st[-1] is not prev_top and not st[-1].t:
            st[-1].t = result_type(prev_name)

    let_at = None  # `Let`: prefixes the next statement
    redim_as = None  # `ReDim a(n) As T`
    write_next = False  # `Write #`: the next Print # statement is a Write
    for k, (op, operand) in enumerate(code):
        if let_at is not None and len(out) > let_at:
            out[let_at] = "Let " + out[let_at]
            let_at = None
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
        prev_name = name
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
        if name == "ARGS":  # a call's start: its method object is the first object marked after it
            call_at.append(len(obj_at))
        if name in ("ARG_S", "ARG_D", "ARG_T_BYREF") and st:  # DLL argument conversions: the declared type
            st[-1].t = {"ARG_S": "S", "ARG_D": "D", "ARG_T_BYREF": "T"}[name]  # (ARG_T_BYREF: ByVal String)
        if fam.startswith("CVT") or name in ("ARGS", "ARGS_FREE", "END_CALL", "TRAP", "LABEL", "LABEL_WIDE", "NARGS",
                                             "ARG_STR", "ARG_V", "ARG_S", "ARG_D", "ARGS_DLL",
                                             "ARG_T_BYREF", "ARG_PAREN", "ARG_TEMP", "ARG_FIX", "ARG_FIX_BACK") \
                or fam in STATEMENT_PREFIX:
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
        elif name == "NEW_FORM":
            st.append(E(f"New {nm('?')}"))
        elif name == "ME_IMPLICIT":
            st.append(E(""))
            obj_at.append(len(st))
        elif name == "METHOD":
            mark = call_at.pop() if call_at else None
            if mark is not None and len(obj_at) > mark + 1:  # object arguments marked too (`PopupMenu mPop`)
                base = obj_at[mark]
                del obj_at[mark:]
            else:
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
            if call_at:
                call_at.pop()
            n, rec = struct.unpack_from("<HH", operand)
            args = [pop() for _ in range(n)][::-1]
            if calls is not None:
                calls.append((name, operand, [a.t for a in args], [a.text for a in args]))
            fn = nm(f"proc{rec & 0xFFF8:x}")
            if name == "CALL_FN":
                st.append(E(f"{fn}({', '.join(a.text for a in args)})"))
            elif op == 0x62E0:  # the Call keyword
                out.append(f"Call {fn}" + (f"({', '.join(a.text for a in args)})" if args else ""))
            else:
                out.append((fn + " " + ", ".join(a.text for a in args)).rstrip())
        elif name == "CTLARRAY":
            st.append(E(f"{nm(var_name(name, operand))}({pop().text})"))
        elif name == "CTLARRAY_GET":
            st.append(E(f"{nm(var_name(name, operand))}({pop().text})"))
        elif name == "CTLARRAY_SET":
            i, v = pop(), pop()
            out.append(f"{nm(var_name(name, operand))}({i.text}) = {v.text}")
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
        elif name in ("PT", "PT_STEP", "PT_TO", "PT_STEP_TO"):
            y, x = pop(), pop()
            pre = {"PT": "", "PT_STEP": "Step", "PT_TO": "-", "PT_STEP_TO": "-Step"}[name]
            st.append(E(f"{pre}({x.text}, {y.text})"))
        elif name in ("CIRCLE_C", "CIRCLE_START", "CIRCLE_END", "CIRCLE_ASPECT"):  # tag the optional argument
            st[-1] = E(f"\0{name[7:] or 'C'}\0{st[-1].text}")
        elif name in ("LINE", "LINE_C", "CIRCLE", "PSET_C", "PSET_P", "SCALE", "SCALE_PTS"):
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
        elif name in ("PRINT_TAB", "PRINT_SPC"):
            st.append(E(f"{'Tab' if name == 'PRINT_TAB' else 'Spc'}({pop().text})"))
        elif name in ("PRINT_SEMI", "PRINT_COMMA"):  # untyped ones also stand alone (`Print , x`)
            item = pop().text if len(st) > (gfx[-1][1] if gfx else 0) else ""
            print_items.append(item + ("; " if name == "PRINT_SEMI" else ", "))
        elif name in ("PRINT_NL", "PRINT_END"):
            o, mark = gfx.pop() if gfx else ("", len(st))
            if len(st) > mark:  # the last item (PRINT_END: a trailing Tab/Spc)
                print_items.append(pop().text)
            del st[mark:]
            items = "".join(print_items).rstrip()
            if o.startswith("\0file"):
                out.append(f"{'Write' if write_next else 'Print'} {o[5:]}," + (f" {items}" if items else ""))
                write_next = False
            else:
                out.append(f"{o}Print" + (f" {items}" if items else ""))
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
        elif name in ("OPEN", "OPEN_LEN"):
            ln = pop() if name == "OPEN_LEN" else None
            num, fname = pop(), pop()
            out.append(f"Open {fname.text} For {open_mode(slot_of(operand))} As {num.text}"
                       + (f" Len = {ln.text}" if ln else ""))
        elif name == "CLOSE":
            n = slot_of(operand)
            args = [pop() for _ in range(n)][::-1]
            out.append(("Close " + ", ".join(a.text for a in args)).rstrip())
        elif name == "AADDR.GLB":
            n = struct.unpack_from("<H", operand)[0]
            idx = [pop() for _ in range(n)][::-1]
            st.append(E(f"{nm(f'glb{slot_of(operand):x}')}({', '.join(i.text for i in idx)})"))
        elif name in ("LOAD.UDT", "LOAD.UDT_LOC", "LOAD.UDT_GLB"):
            st.append(E(nm(f"u{slot_of(operand):x}")))
        elif name == "AUDT":
            n = struct.unpack_from("<H", operand)[0]
            idx = [pop() for _ in range(n)][::-1]
            st.append(E(f"{nm(f'u{slot_of(operand):x}')}({', '.join(i.text for i in idx)})"))
        elif name in ("FIELD_ALOAD", "FIELD_ASTORE"):
            rec = pop()
            n = struct.unpack_from("<H", operand)[0]
            idx = [pop() for _ in range(n)][::-1]
            ref = f"{rec.text}.{nm(f'f{slot_of(operand):x}')}({', '.join(i.text for i in idx)})"
            if name == "FIELD_ALOAD":
                st.append(E(ref))
            else:
                out.append(f"{ref} = {pop().text}")
        elif name == "STORE.UDT":
            out.append(f"{nm(f'u{slot_of(operand):x}')} = {pop().text}")
        elif name.startswith("FIELD_SET"):
            rec, v = pop(), pop()
            out.append(f"{rec.text}.{nm(f'f{slot_of(operand):x}')} = {v.text}")
        elif name == "GOSUB":
            out.append(f"GoSub L{slot_of(operand):x}")
        elif name == "RETURN":
            out.append("Return")
        elif name == "Randomize":
            out.append("Randomize")
        elif op == 0x0DFA:  # runtime function: u16 id, u16 argument count (a trailing missing marker aside)
            fid, n = struct.unpack_from("<HH", operand)
            if st and st[-1].text == MISSING_TEXT and len(st) > n:
                pop()
            args = [pop() for _ in range(n)][::-1]
            fn = RT_FUNCS.get(fid, f"RTFN{fid:X}")
            if not fid & 0x8000:  # a statement
                out.append(f"{fn} {', '.join(a.text for a in args if a.text != MISSING_TEXT)}")
                continue
            st.append(E(f"{fn}({', '.join(a.text for a in args if a.text != MISSING_TEXT)})"))
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
            prop = member(o.text, f"p{slot_of(operand) & 0xFF:x}")
            out.append(f"{prop}({', '.join(i.text for i in idx)}) = {v.text}")
        elif name == "Len.T":
            st.append(E(f"Len({pop().text})"))
        elif name == "FIELD_GET.T" or name.startswith("FIELD_GET"):
            st.append(E(f"{pop().text}.{nm(f'f{slot_of(operand):x}')}"))
        elif name == "PUSH.L":  # the entry point keeps the literal's radix: 388A hex, 388D decimal
            v = struct.unpack_from("<i", operand)[0]
            st.append(E(f"&H{v & 0xFFFFFFFF:X}&" if op == 0x388A else f"&O{v & 0xFFFFFFFF:o}&" if op == 0x3887 else
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
            t = slot_of(operand)
            out.append("Resume 0" if t == 0xFFFF else f"Resume L{t:x}")
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
            st.append(E(f"{nm(f'a{slot:x}').rstrip('$')}$({', '.join(dims)})"))  # 0768: `ReDim a$(...)`
        elif name == "RET_SLOT":
            ret_value.append(len(st))
        elif name == "REDIM_AS":  # u16 type, u16 the text column of `As` (marked for the caller to check)
            t, at = struct.unpack_from("<HH", operand)
            redim_as = f"\x01{at}\x01As " + {1: "Integer", 2: "Long", 3: "Single", 4: "Double", 5: "Currency",
                                              7: "String"}.get(t, "Variant")
        elif name in ("REDIM", "REDIM_PRESERVE"):
            out.append(("ReDim Preserve " if name == "REDIM_PRESERVE" else "ReDim ") + pop().text
                       + (f" {redim_as}" if redim_as else ""))
            redim_as = None
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
            op_text = {"GT": ">", "LT": "<", "GE": ">=", "LE": "<=", "NE": "<>", "EQ": "="}[name.split(".")[1]]
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
        elif name in ("CASE_TO", "CASE_TO_HI"):
            hi, lo = pop(), pop()
            st.append(E(f"{lo.text} To {hi.text}"))
        elif name in ("CASE_TO_LO", "CASE_TO_JMP"):
            pass
        elif name == "WHILE":
            out.append(f"While {pop().text}")
        elif name == "WEND":
            out.append("Wend")
        elif name in ("ON_GOTO", "ON_GOSUB"):
            n = struct.unpack_from("<H", operand)[0] // 2
            targets = struct.unpack_from(f"<{n}H", operand, 2)
            out.append(f"On {pop().text} {'GoTo' if name == 'ON_GOTO' else 'GoSub'} "
                       + ", ".join(f"L{t:x}" for t in targets))
        elif name == "LET":
            let_at = len(out)
        elif name == "MID_STMT":
            target, value = pop(), pop()
            args = [pop().text for _ in range(2 if op == 0x7731 else 1)][::-1]
            out.append(f"Mid$({target.text}, {', '.join(args)}) = {value.text}")
        elif name in ("LSET", "RSET"):
            target, value = pop(), pop()
            out.append(f"{'LSet' if name == 'LSET' else 'RSet'} {target.text} = {value.text}")
        elif name == "STOP":
            out.append("Stop")
        elif name == "RANDOMIZE_N":
            out.append(f"Randomize {pop().text}")
        elif name == "WRITE#":
            write_next = True
        elif name == "LINE_INPUT#":
            var = pop()
            num, mark = gfx.pop() if gfx else ("#?", len(st))
            out.append(f"Line Input {num}, {var.text}")
        elif name == "NAME":
            b, a = pop(), pop()
            out.append(f"Name {a.text} As {b.text}")
        elif name == "WIDTH#":
            w, num = pop(), pop()
            out.append(f"Width {num.text}, {w.text}")
        elif name in ("DATE$=", "TIME$=", "DATE=", "TIME="):
            out.append(f"{name[:-1].title()} = {pop().text}")
        elif name == "LOCK":
            w = struct.unpack_from("<H", operand)[0]
            rec = ""  # bit 1: records given; 0x8000 one record, 0x4000 `To n` (lower bound pushed as 1)
            if w & 0x8000:
                rec = f", {pop().text}"
            elif w & 2:
                hi, lo = pop(), pop()
                rec = f", To {hi.text}" if w & 0x4000 else f", {lo.text} To {hi.text}"
            out.append(f"{'Unlock' if w & 1 else 'Lock'} {pop().text}{rec}")
        elif name == "ERASE":
            out.append(f"Erase {pop().text}")
        elif name == "ENDIF":
            out.append("End If")
        elif name in ("END_SELECT",):
            out.append("End Select")
        elif name in ("FOR", "FOR_STEP", "FOR.I", "FOR.L"):
            step = pop().text if name.startswith("FOR_STEP") else None
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
    if let_at is not None and len(out) > let_at:
        out[let_at] = "Let " + out[let_at]
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
    out = merged
    text = "; ".join(out) if out else (st[-1].text if st else "")
    return prefix + text


# --- scoring against the corpus -------------------------------------------
