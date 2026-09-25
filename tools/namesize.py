#!/usr/bin/env python3
"""
Module name-table size model (declarations record +30).

  +30 = 349 + sum(len(name) + 4) over the module's unique identifiers
        (case-insensitive)

See OPCODES.md "Name-table size" for what counts. `name_size(code)` computes
it from module source; the CLI checks the model against round-trip builds:

  python3 tools/namesize.py [work/rt/<project> ...]    # default: all

prints one line per module whose predicted size differs from the exe.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = 349

# Methods: never a name-table entry, even after a dot.
METHODS = {
    "findfirst", "findnext", "findlast", "findprevious", "movefirst", "movelast", "movenext",
    "moveprevious", "addnew", "edit", "update", "delete", "updatecontrols", "updaterecord",
    "enddoc", "newpage", "killdoc", "gettext", "settext", "getdata", "setdata", "getformat",
    "arrange", "line", "print", "show", "hide", "cls", "move", "refresh", "setfocus", "additem",
    "removeitem", "clear", "zorder", "drag", "pset", "circle", "scale", "printform", "textwidth",
    "textheight", "point", "linkexecute", "linkpoke", "linkrequest", "linksend", "unload", "load",
    "msgbox",
}
KEYWORDS = METHODS | {
    "like", "and", "or", "not", "mod", "xor", "eqv", "imp", "if", "then", "else", "elseif",
    "true", "false", "do", "loop", "while", "until", "wend", "redim", "preserve", "set", "is",
    "nothing", "typeof", "local", "to", "open", "input", "output", "append", "random", "binary",
    "as", "close", "gosub", "return", "for", "step", "next", "end", "exit", "sub", "function",
    "on", "error", "goto", "resume", "select", "case", "dim", "integer", "long", "single",
    "double", "currency", "string", "variant", "global", "const", "static", "type", "declare",
    "lib", "byval", "any", "option", "explicit", "new", "defint", "debug", "text", "me",
    "shared", "call", "let", "get", "put", "seek", "access", "read", "write", "lock", "len",
    "base",
}
BUILTINS = {
    "chr", "left", "right", "mid", "str", "val", "instr", "msgbox", "inputbox", "format", "ucase",
    "lcase", "ltrim", "rtrim", "trim", "int", "fix", "abs", "sgn", "sqr", "rnd", "randomize",
    "timer", "now", "date", "time", "dir", "curdir", "chdir", "chdrive", "kill", "filecopy",
    "freefile", "eof", "lof", "loc", "shell", "doevents", "beep", "rgb", "qbcolor", "true",
    "false", "loadpicture", "savepicture", "cint", "clng", "cdbl", "csng", "ccur", "cstr", "cvar",
    "cvdate", "isdate", "isnumeric", "isnull", "isempty", "asc", "hex", "oct", "space", "string",
    "ubound", "lbound", "err", "error", "erl", "minute", "hour", "second", "day", "month", "year",
    "weekday", "dateserial", "datevalue", "timeserial", "timevalue", "sendkeys", "appactivate",
    "environ", "command", "tab", "spc", "log", "exp", "sin", "cos", "tan", "atn", "createobject",
    "getobject", "name", "mkdir", "rmdir", "end", "stop", "width", "reset", "lset", "rset",
    "erase",
}
# Builtin names that are properties (and so counted) when used bare, without $ or "(".
PROPS = {"left", "width"}

_TOKEN = re.compile(r"(?<![\w.])([A-Za-z_]\w*)([%&!#@$]?)(\s*\(?)|\.([A-Za-z_]\w*)")


def identifiers(code: str) -> dict[str, str]:
    """Unique counted identifiers of module code (lowercase -> spelling)."""
    code = re.sub(r'"[^"]*"', "", code)
    code = re.sub(r"&[HhOo][0-9A-Fa-f]+&?", "", code)
    code = re.sub(r"'.*", "", code)
    out: dict[str, str] = {}  # in first-appearance order (name-table offsets)
    for m in re.finditer(r"(?m)^(\d+)\b|" + _TOKEN.pattern, code):
        if m.group(1):  # numeric label
            out.setdefault(m.group(1), m.group(1))
            continue
        m = _TOKEN.match(code, m.start())
        if m.group(4):
            if m.group(4).lower() not in METHODS:
                out.setdefault(m.group(4).lower(), m.group(4))
            continue
        tk, low = m.group(1), m.group(1).lower()
        if low in KEYWORDS:
            continue
        if low in BUILTINS and (m.group(2) == "$" or "(" in m.group(3) or low not in PROPS):
            continue
        out.setdefault(low, tk)
    if "b" in out:  # Line ..., B also registers BF
        out.setdefault("bf", "BF")
    return out


FIRST = 10  # name-table offset of a module's first identifier, mod 16


def name_offsets(code: str) -> dict[str, int]:
    """Name-table offset (mod 16 exact) of each identifier (lowercase)."""
    out, o = {}, FIRST
    for low, sp in identifiers(code).items():
        out[low] = o
        o += len(sp) + 4
    return out


def name_size(code: str) -> int:
    return BASE + sum(len(x) + 4 for x in identifiers(code).values())


def module_code(src: str) -> str:
    """Code part of a .bas/.frm source (form layout stripped)."""
    i = src.find("\r\nEnd\r\n") if src.lstrip().startswith(("VERSION", "Begin")) else -1
    return src[i + 7:] if i >= 0 else src


def check(project_dir: Path) -> list[tuple[str, int, int]]:
    """(file, exe +30, model) for every module of the deco build in project_dir."""
    import pcode_disasm as P
    from decompile import image_layout, word
    deco = project_dir / "deco"
    exes = list(deco.glob("*.exe"))
    if not exes:
        return []
    exe = exes[0]
    r = P.rcdata(exe)
    img, tab = r[2], P.parse_ne(exe)[2].data
    lay = image_layout(img, len(P.form_names(r)))
    mak = next(deco.glob("*.mak"))
    files = [ln.strip() for ln in mak.read_text("latin-1").splitlines()
             if ln.strip().lower().endswith((".bas", ".frm"))]
    bas = [f for f in files if f.lower().endswith(".bas")]
    byname = {}
    for f in files:
        if f.lower().endswith(".frm"):
            m = re.search(r"Begin \w+ (\w+)", (deco / f).read_bytes().decode("latin-1"))
            byname[m.group(1).lower()] = f
    frm = [byname[n[0].lower()] for n in P.form_names(r)]
    out = []
    for f, c in list(zip(bas, lay["modules"])) + list(zip(frm, [c for c, _ in lay["forms"]])):
        got = word(tab, word(img, c - 2) + 4 + 30)
        out.append((f, got, name_size(module_code((deco / f).read_bytes().decode("latin-1")))))
    return out


def main():
    root = Path(__file__).resolve().parent.parent / "work" / "rt"
    dirs = [Path(a) for a in sys.argv[1:]] or sorted(d for d in root.iterdir() if d.is_dir())
    n = bad = 0
    for d in dirs:
        for f, got, want in check(d):
            n += 1
            if got != want:
                bad += 1
                print(f"{d.name:10s} {f:14s} exe {got}  model {want}  ({got - want:+d})")
    print(f"{n - bad}/{n} modules match")


if __name__ == "__main__":
    main()
