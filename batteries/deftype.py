# DefType statements: every keyword, ranges, single letters, several
# statements, form vs .bas; variables of affected/unaffected letters used.
KW = ["DefInt", "DefLng", "DefSng", "DefDbl", "DefCur", "DefStr", "DefVar"]

def is_str(decl, letter):
    """Whether DefStr in decl covers letter (ranges `A-M`, lists `A, M, X`)."""
    for ln in decl.splitlines():
        if ln.startswith("DefStr"):
            for part in ln[6:].split(","):
                lo, _, hi = part.strip().upper().partition("-")
                if lo <= letter.upper() <= (hi or lo):
                    return True
    return False


def body(decl):
    v = lambda name, x: f'"{x}"' if is_str(decl, name[0]) else str(x)  # String variables get strings
    return (decl + "\n"
            "Dim abc, mno, xyz\n\n"
            "Sub Test1 ()\n"
            "    Dim k, q\n"
            f"    abc = {v('abc', 1)}: mno = {v('mno', 2)}: xyz = {v('xyz', 3)}\n"
            f"    k = abc + {v('abc', 1)}: q = xyz\n"
            "    Debug.Print k; q; mno\n"
            "End Sub\n")


cases = []
for kw in KW:
    for rng in ["A-Z", "A-M", "N-Z", "K", "X-Z", "A, M, X"]:
        cases.append(dict(name=f"{kw} {rng}", code=body(f"{kw} {rng}")))
    cases.append(dict(name=f"{kw} A-Z bas", bas=True, code=body(f"{kw} A-Z")))
cases += [
    dict(name="DefInt A-K + DefStr L-Z", code=body("DefInt A-K\nDefStr L-Z")),
    dict(name="DefLng A + DefDbl M + DefCur X", code=body("DefLng A\nDefDbl M\nDefCur X")),
    dict(name="DefInt I-N (fortran)", code=body("DefInt I-N")),
    dict(name="DefStr S + suffix override", code="DefStr S\nDim s1, s2%\n\nSub Test1 ()\n    s1 = \"a\": s2% = 2\n    Debug.Print s1; s2%\nEnd Sub\n"),
    dict(name="DefInt a-z lowercase", code=body("DefInt a-z")),
    dict(name="DefInt + Option Explicit", code="Option Explicit\n" + body("DefInt A-Z")),
    dict(name="DefDbl function return", code="DefDbl F\n\nFunction Fx (a)\n    Fx = a * 2\nEnd Function\n\nSub Test1 ()\n    Debug.Print Fx(3)\nEnd Sub\n"),
]
