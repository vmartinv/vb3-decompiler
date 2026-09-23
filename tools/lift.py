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
from opcodes import METHODS, NAMES  # noqa: E402

BINOPS = {  # family -> (text, precedence; higher binds tighter)
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
    "Left$": 2, "Shell": 2, "Format$": 2, "InStr": 2, "Mid$": 3, "InputBox": 3,
    "RGB": 3, "Trim$": 1, "Format$.1": 1,
}
STATEMENT_FUNCS = {"MsgBox": 3, "DoEvents": 0, "Cls": 0, "Beep": 0, "ChDir": 1, "ChDrive": 1}
FUNCTION_FORMS = {"MsgBox.fn": ("MsgBox", 3)}
MISSING_TEXT = "\0missing"


class E:
    """Expression with a precedence (for parenthesising)."""
    def __init__(self, text: str, prec: float = 99):
        self.text, self.prec = text, prec

    def at(self, prec: float) -> str:
        return self.text if self.prec >= prec else f"({self.text})"


def slot_of(operand: bytes) -> int:
    return struct.unpack_from("<H", operand, len(operand) - 2)[0] if len(operand) >= 2 else 0


def var_name(name: str, operand: bytes) -> str:
    scope = name.split(".")[1] if "." in name else "v"
    return f"{scope.lower()}{slot_of(operand):x}"


STATEMENT_PREFIX = {"CASE"}  # block-end jumps opening ElseIf/Case lines


def lift(code: list[tuple[int, bytes]], ids: dict[int, int] | None = None) -> str:
    """code: [(handler, operand)] for one statement (marker excluded).
    ids: handler -> interpreter opcode ID, used to classify unnamed handlers."""
    st: list[E] = []
    out: list[str] = []
    prefix = ""
    obj_at: list[int] = []  # stack depth just after a method's object was pushed

    def pop() -> E:
        return st.pop() if st else E("?")

    def family(op: int, name: str) -> str:
        if op in NAMES:
            return name
        low = (ids or {}).get(op, 0) & 0xFF
        return {0x0B: "LOAD.X", 0x0C: "STORE.X", 0x0E: "ALOAD.X", 0x0F: "ASTORE.X"}.get(low, name)

    for k, (op, operand) in enumerate(code):
        name = family(op, NAMES.get(op, f"op_{op:04X}"))
        fam = name.split(".")[0].split(" ")[0].rstrip("?")
        if fam.startswith("CVT") or name in ("ARGS", "ARGS_FREE", "END_CALL", "TRAP", "LABEL", "NARGS",
                                             "ARG_STR", "ARG_V", "ARG_S", "ARG_D", "ARGS_DLL",
                                             "ARG_T_BYREF", "ARG_PAREN", "ARG_TEMP") or fam in STATEMENT_PREFIX:
            continue
        if name in ("OBJ", "OBJ_SELF"):
            if name == "OBJ_SELF" or not obj_at or obj_at[-1] != len(st):
                obj_at.append(len(st))
            continue
        if name.startswith(("LOAD.", "PGET_ME", "ADDR")):
            st.append(E(var_name(name, operand)))
        elif name in ("CONTROL", "FORM", "OBJVAR"):
            st.append(E(var_name(name, operand)))
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
            out.append((call + " " + ", ".join("" if a.text == MISSING_TEXT else a.text for a in args)).rstrip())
        elif name in ("CALL", "CALL_FN"):
            n, rec = struct.unpack_from("<HH", operand)
            args = [pop() for _ in range(n)][::-1]
            fn = f"proc{rec & 0xFFF8:x}"
            if name == "CALL_FN":
                st.append(E(f"{fn}({', '.join(a.text for a in args)})"))
            else:
                out.append((fn + " " + ", ".join(a.text for a in args)).rstrip())
        elif name == "CTLARRAY":
            st.append(E(f"{var_name(name, operand)}({pop().text})"))
        elif name == "CTLARRAY_OF":
            o, i = pop(), pop()
            st.append(E(f"{o.text}!c{slot_of(operand) & 0x3FFF:x}({i.text})"))
        elif name == "SUBOBJ":
            o, sub = pop(), slot_of(operand)
            st.append(E(f"{o.text}.p{sub & 0xFF:x}" if sub & 0xC000 == 0xC000 else f"{o.text}!c{sub & 0x3FFF:x}"))
        elif name == "ARG_MISSING":
            st.append(E(MISSING_TEXT))
        elif name == "PGET_IDX":
            o = pop()
            n = struct.unpack_from("<H", operand)[0]
            idx = [pop() for _ in range(n)][::-1]
            st.append(E(f"{o.text}.p{slot_of(operand) & 0xFF:x}({', '.join(i.text for i in idx)})"))
        elif name == "PSET_IDX":
            o = pop()
            n = struct.unpack_from("<H", operand)[0]
            idx = [pop() for _ in range(n)][::-1]
            v = pop()
            out.append(f"{o.text}.p{slot_of(operand) & 0xFF:x}({', '.join(i.text for i in idx)}) = {v.text}")
        elif name == "Len.T":
            st.append(E(f"Len({pop().text})"))
        elif name == "FIELD_GET.T" or name.startswith("FIELD_GET"):
            st.append(E(f"{pop().text}.f{slot_of(operand):x}"))
        elif name == "PUSH.L":
            st.append(E(str(struct.unpack_from("<i", operand)[0])))
        elif name == "DO":
            out.append("Do")
        elif name == "CASE_ELSE":
            out.append("Case Else")
        elif name == "NEXT_NOVAR":
            pop()
            out.append("Next")
        elif name == "PGET":
            o = pop()
            st.append(E(f"{o.text}.p{slot_of(operand) & 0xFF:x}"))
        elif name == "PSET":
            o, v = pop(), pop()
            out.append(f"{o.text}.p{slot_of(operand) & 0xFF:x} = {v.text}")
        elif name.startswith(("STORE.", "PSET_ME")):
            out.append(f"{var_name(name, operand)} = {pop().text}")
        elif fam == "ALOAD":
            n = struct.unpack_from("<H", operand)[0]
            idx = [pop() for _ in range(n)][::-1]
            st.append(E(f"{var_name(name, operand)}({', '.join(i.text for i in idx)})"))
        elif fam == "ASTORE":
            n = struct.unpack_from("<H", operand)[0]
            idx = [pop() for _ in range(n)][::-1]
            val = pop()
            out.append(f"{var_name(name, operand)}({', '.join(i.text for i in idx)}) = {val.text}")
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
                st.append(E(str(struct.unpack_from("<h", operand)[0])))
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
            st.append(E(fn + (f"({', '.join(a.text for a in args)})" if n else "")))
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
        elif name.startswith("CASE_EQ."):
            if out and out[-1].startswith("Case ") and out[-1] != "Case Else":
                out[-1] += f", {pop().text}"  # Case a, b
            else:
                out.append(f"Case {pop().text}")
        elif name == "ENDIF":
            out.append("End If")
        elif name in ("END_SELECT",):
            out.append("End Select")
        elif name in ("FOR", "FOR_STEP", "FOR.I"):
            step = pop().text if name == "FOR_STEP" else None
            b, a, v = pop(), pop(), pop()
            out.append(f"For {v.text} = {a.text} To {b.text}" + (f" Step {step}" if step else ""))
        elif name in ("NEXT", "NEXT.I"):
            out.append(f"Next {pop().text}")
        elif name == "EXIT":
            out.append("Exit Sub")
        elif name == "END":
            out.append("End")
        elif name == "GOTO":
            out.append(f"GoTo L{slot_of(operand):x}")
        elif name == "ON_ERROR_GOTO":
            t = slot_of(operand)
            out.append({0xFFFF: "On Error GoTo 0", 0xFFFE: "On Error Resume Next"}.get(t, f"On Error GoTo L{t:x}"))
        elif name == "UNLOAD":
            out.append(f"Unload {pop().text}")
        elif name == "LOAD":
            out.append(f"Load {pop().text}")
        else:
            out.append(f"<{name}>")
    if len(out) > 1 and out[0] == "Else":  # single-line Else: `Else stmt`
        out = ["Else " + out[1]] + out[2:]
    text = "; ".join(out) if out else (st[-1].text if st else "")
    return prefix + text


# --- scoring against the corpus -------------------------------------------

KEYWORDS = {"and", "or", "not", "mod", "xor", "eqv", "imp", "if", "then", "else", "elseif", "true", "false",
            "for", "to", "step", "next", "end", "exit", "sub", "function", "on", "error", "goto", "resume",
            "unload", "load", "select", "case"}


def norm(text: str) -> list[str]:
    text = re.sub(r"&H([0-9A-Fa-f]+)&?", lambda m: str(int(m.group(1), 16)), text)
    text = text.replace("!", ".")  # a!b == a.b
    text = re.sub(r"(?i)\bexit\s+function\b", "Exit Sub", text)  # kind is the emitter's job
    toks = re.findall(r'"[^"]*"|[A-Za-z_]\w*[$%&!#]?|\d*\.?\d+(?:[eE][-+]?\d+)?#?|<>|<=|>=|\S', text)
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
        elif re.fullmatch(r"\d*\.?\d+(?:[eE][-+]?\d+)?#?", t):
            out.append(repr(float(t.rstrip("#"))))
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
    for k, v in c.most_common(25):
        if k.startswith("op "):
            print(f"   {v:5d} {k[3:]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score")
    s.add_argument("corpus", type=Path)
    s.add_argument("--runtime", type=Path)
    s.add_argument("-v", action="store_true")
    args = ap.parse_args()
    score(args.corpus, args.v, args.runtime)


if __name__ == "__main__":
    main()
