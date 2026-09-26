# Module level: Global / Dim / Const / Global Const / Type declarations and
# Option statements (Base, Compare, Explicit) across .bas and form modules.
def S(body):
    return f"Sub Form_Load ()\n{body}\nEnd Sub\n"


def BAS(decl, use="    Debug.Print 1"):
    """A .bas module with `decl`, and a form that uses it."""
    return dict(bas=True, code=decl + "\n", extra=[dict(code=S(use))])


cases = [
    dict(name="form Dim", code="Dim a As Integer, b As String\nDim c(5) As Long\n\n" + S("    a = 1: b = \"x\": c(2) = 3")),
    dict(name="form Const", code="Const A1 = 5, A2 = \"s\"\nConst A3 = 2.5\n\n" + S("    Debug.Print A1; A2; A3")),
    dict(name="bas Global", **BAS("Global G1 As Integer\nGlobal G2 As String, G3(10) As Double",
                                  "    G1 = 1: G2 = \"x\": G3(1) = 2")),
    dict(name="bas Global Const", **BAS("Global Const GK = 7\nGlobal Const GS = \"t\"", "    Debug.Print GK; GS")),
    dict(name="bas Dim private", **BAS("Dim P1 As Integer\n\nSub SetP ()\n    P1 = 3\nEnd Sub", "    SetP")),
    dict(name="bas Type Global", **BAS("Type Pt\n    X As Integer\n    Y As Integer\nEnd Type\n\nGlobal Gp As Pt",
                                       "    Gp.X = 1: Gp.Y = 2")),
    dict(name="Option Base 1", code="Option Base 1\nDim a(5) As Integer\n\n" + S("    a(1) = 2\n    Debug.Print LBound(a)")),
    dict(name="Option Compare Text", code="Option Compare Text\n\n" + S("    If \"a\" = \"A\" Then Debug.Print 1\n"
                                                                       "    Debug.Print InStr(\"ABC\", \"b\")")),
    dict(name="Option Compare Binary", code="Option Compare Binary\n\n" + S("    If \"a\" < \"B\" Then Debug.Print 1")),
    dict(name="Option Explicit", code="Option Explicit\n\n" + S("    Dim x As Integer\n    x = 1")),
    dict(name="bas Option Compare", **BAS("Option Compare Text\n\nFunction Same (a$, b$) As Integer\n"
                                          "    Same = a$ = b$\nEnd Function", "    Debug.Print Same(\"a\", \"A\")")),
    dict(name="Static local", code=S("    Static n As Integer\n    n = n + 1")),
    dict(name="Static array", code=S("    Static a(3) As String\n    a(1) = \"x\"")),
    dict(name="global array dynamic", **BAS("Global Da() As Integer", "    ReDim Da(5)\n    Da(1) = 2")),
    dict(name="global fixed string", **BAS("Global Fs As String * 5, Fn As String * 12", "    Fs = \"abc\": Fn = Fs")),
    dict(name="global in two modules", bas=True, code="Global Shared1 As Long\n",
         extra=[dict(bas=True, code="Sub Bump ()\n    Shared1 = Shared1 + 1\nEnd Sub\n"),
                dict(code=S("    Bump\n    Debug.Print Shared1"))]),
] + [dict(name=f"form unused {d}, global used", bas=True, code="Global Gu As Variant\n",
          extra=[dict(code=f"{d}\n\n" + S("    Gu = 1"))])
     for d in ("Dim u As Integer", "Dim u As Long", "Dim u As Integer, v As Integer")]
