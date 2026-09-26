"""
Model of a module's compile-time name table (the IDE's identifier list):
each unique identifier (case-insensitive) in first-appearance order, len + 4
bytes each, the first at FIRST (mod 32). The p-code depends on it in one
place: a procedure frees its object/Type locals in the order of the table's
8 hash buckets ((offset >> 1) & 7), see naming.py (fit_frees).
"""
from __future__ import annotations

import re


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
    if "b" in out and re.search(r"(?im)\bLine\b.*,\s*B\s*$", code):  # Line ..., B also registers BF
        out.setdefault("bf", "BF")
    return out


FIRST = 26  # name-table offset of a module's first identifier, mod 32


def name_offsets(code: str) -> dict[str, int]:
    """Name-table offset (mod 16 exact) of each identifier (lowercase)."""
    out, o = {}, FIRST
    for low, sp in identifiers(code).items():
        out[low] = o
        o += len(sp) + 4
    return out
