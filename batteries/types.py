# Data types, declarations, arrays, user-defined Types, constants.
T = ["Integer", "Long", "Single", "Double", "Currency", "String", "Variant"]
SUF = {"Integer": "%", "Long": "&", "Single": "!", "Double": "#", "Currency": "@", "String": "$"}
VAL = {"Integer": "3", "Long": "70000", "Single": "1.5", "Double": "2.25", "Currency": "3.5", "String": '"s"', "Variant": "4"}

def use(body, decl=""):
    return f"{decl}\nSub Form_Load ()\n{body}\nEnd Sub\n"

cases = []
for t in T:
    v = VAL[t]
    cases.append(dict(name=f"local Dim As {t}", code=use(f"    Dim a As {t}\n    a = {v}\n    Debug.Print a")))
    cases.append(dict(name=f"module Dim As {t}", code=use(f"    a = {v}\n    Debug.Print a", f"Dim a As {t}")))
    cases.append(dict(name=f"Static As {t}", code=use(f"    Static a As {t}\n    a = {v}\n    Debug.Print a")))
    cases.append(dict(name=f"Global As {t}", code=use(f"    ga = {v}\n    Debug.Print ga"),
                      extra=[dict(bas=True, code=f"Global ga As {t}\n")]))
    cases.append(dict(name=f"array 1D As {t}", code=use(f"    Static a(10) As {t}\n    a(3) = {v}\n    Debug.Print a(3)")))
    if t in SUF:
        s = SUF[t]
        cases.append(dict(name=f"suffix {s}", code=use(f"    b{s} = {v}\n    Debug.Print b{s}")))
cases += [
    dict(name="fixed string local", code=use('    Dim s As String * 12\n    s = "abc"\n    Debug.Print s')),
    dict(name="fixed string module", code=use('    s = "abc"\n    Debug.Print s', "Dim s As String * 7")),
    dict(name="array bounds To", code=use("    Static a(1 To 5) As Integer\n    a(2) = 1\n    Debug.Print a(2)")),
    dict(name="array negative bounds", code=use("    Static a(-3 To 3) As Long\n    a(-1) = 1\n    Debug.Print a(-1)")),
    dict(name="array 2D", code=use("    Static a(3, 4) As Integer\n    a(1, 2) = 1\n    Debug.Print a(1, 2)")),
    dict(name="array 3D mixed bounds", code=use("    Static a(1 To 2, 0 To 3, -1 To 1) As Double\n    a(1, 2, 0) = 1\n    Debug.Print a(1, 2, 0)")),
    dict(name="dynamic ReDim", code=use("    ReDim a(n) As Integer\n    a(0) = 1", "Dim n As Integer\nDim a() As Integer")),
    dict(name="ReDim Preserve", code=use("    ReDim a(5)\n    ReDim Preserve a(10)\n    Debug.Print UBound(a)", "Dim a()")),
    dict(name="local dynamic array", code=use("    Dim a() As String\n    ReDim a(3)\n    a(1) = \"x\"\n    Erase a")),
    dict(name="Erase fixed", code=use("    Erase a", "Dim a(5) As Integer")),
    dict(name="LBound UBound dims", code=use("    Static a(2 To 4, 5)\n    Debug.Print LBound(a, 1); UBound(a, 2)")),
    dict(name="Type all fields", code=use("    Dim r As Rec\n    r.i = 1: r.l = 2: r.s = 3: r.d = 4: r.c = 5: r.st = \"a\": r.fs = \"b\": r.v = 6\n    Debug.Print r.i"),
         extra=[dict(bas=True, code="Type Rec\n    i As Integer\n    l As Long\n    s As Single\n    d As Double\n    c As Currency\n    st As String\n    fs As String * 10\n    v As Variant\nEnd Type\n")]),
    dict(name="Type nested + array field", code=use("    Dim o As Outer\n    o.inn.x = 1\n    o.arr(2) = 3\n    Debug.Print o.inn.x"),
         extra=[dict(bas=True, code="Type Inner\n    x As Integer\nEnd Type\nType Outer\n    inn As Inner\n    arr(5) As Integer\nEnd Type\n")]),
] + [dict(name=f"Type array field {k}", code=use(f"    Dim r As A{k}\n    r.a({idx}) = {'\"x\"' if 'String' in t else 1}\n    Debug.Print r.b"),
             extra=[dict(bas=True, code=f"Type A{k}\n    b As Integer\n    a({dims}) As {t}\n    c As Long\nEnd Type\n")])
    for k, (dims, idx, t) in enumerate([("5", "2", "Integer"), ("2 To 7", "3", "Integer"), ("3, 4", "1, 2", "Integer"),
                                        ("1 To 2, -1 To 3", "1, 0", "Long"), ("9", "0", "String"),
                                        ("4", "1", "String * 3"), ("2", "1", "Double")])] + [
    dict(name="array of Type", code=use("    Static a(3) As P2\n    a(1).x = 1\n    Debug.Print a(1).x"),
         extra=[dict(bas=True, code="Type P2\n    x As Long\n    y As Long\nEnd Type\n")]),
    dict(name="Const kinds", code=use("    Debug.Print C1; C2; C3; C4; C5; C6; C7",
         'Const C1 = 5\nConst C2 = &H1F\nConst C3 = &O17\nConst C4 = 1.25\nConst C5 = "txt"\nConst C6 = 70000\nConst C7 = &HFFFF&')),
    dict(name="Const expression", code=use("    Debug.Print C2", "Const C1 = 4\nConst C2 = C1 * 2 + 1")),
    dict(name="Global Const", code=use("    Debug.Print GC1; GC2"),
         extra=[dict(bas=True, code='Global Const GC1 = 7\nGlobal Const GC2 = "g"\n')]),
    dict(name="Dim multiple one line", code=use("    Dim a, b As Integer, c$\n    a = 1: b = 2: c$ = \"3\"\n    Debug.Print a; b; c$")),
    dict(name="Dim Shared-ish module arrays", code=use("    a(1) = 1: b(2, 2) = 2", "Dim a(10)\nDim b(3, 3) As Single")),
    dict(name="object vars", code=use("    Dim f As Form, c As Control\n    Set f = Me\n    Set c = Nothing\n    Debug.Print f.Caption")),
    dict(name="As New form", code=use("    Dim f As New @SELF@\n    f.Caption = \"x\"")),
]
