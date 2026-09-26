#!/usr/bin/env python3
"""
Scores the statement lifter (src/vb3decompiler/lift.py) against the source-aligned sample
corpus (tools/corpus.py), and proposes names for unnamed handlers.

  python3 tools/lift_score.py score <corpus-dir> --runtime VBRUN300.DLL [-v]
  python3 tools/lift_score.py infer <corpus-dir> --runtime VBRUN300.DLL

`score` lifts every aligned corpus statement and compares it with the real
source line, identifiers normalised (variable names aren't stored).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from vb3decompiler.lift import FUNCS, lift  # noqa: E402

KEYWORDS = {"and", "or", "not", "mod", "xor", "eqv", "imp", "if", "then", "else", "elseif", "true", "false",
            "do", "loop", "while", "until", "wend", "redim", "preserve", "set", "is", "nothing", "typeof",
            "local", "ubound", "to", "open", "input", "output", "append", "random", "binary", "as", "close",
            "gosub", "return", "randomize", "loadpicture", "access", "read", "write",
            "line", "circle", "pset", "scale", "print", "step", "debug", "textwidth", "textheight", "point",
            "get", "put", "like", "len", "seek", "err", "byval", "eof",
            "for", "next", "end", "exit", "sub", "function", "on", "error", "goto", "resume",
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
    for _, code in lines:
        got = lift(code, ids)
        for m in re.finditer(r"<op_([0-9A-F]{4})>", got):
            unknown[int(m.group(1), 16)] += 1
    for op, _ in unknown.most_common():
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
