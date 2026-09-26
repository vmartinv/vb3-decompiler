# Declare statements: Sub and Function forms, every parameter kind (ByVal,
# ByRef, As Any, String, arrays of user types, typed-suffix names), Alias,
# ordinals, and calls to them from .bas and form modules.
def S(body):
    return f"Sub Form_Load ()\n{body}\nEnd Sub\n"


def D(decl, call="    Debug.Print 1"):
    return f"{decl}\n\n" + S(call)


cases = [
    dict(name="Sub no args", code=D("Declare Sub MessageBeep Lib \"User\" (ByVal N As Integer)", "    MessageBeep 0")),
    dict(name="Function ByVal", code=D("Declare Function GetTickCount Lib \"User\" () As Long",
                                       "    Debug.Print GetTickCount()")),
    dict(name="ByVal String", code=D("Declare Function FindWindow Lib \"User\" (ByVal C As Any, ByVal T As Any) As Integer",
                                     "    h = FindWindow(0&, \"x\")")),
    dict(name="As Any ByRef", code=D("Declare Sub hmemcpy Lib \"Kernel\" (D As Any, S As Any, ByVal N As Long)",
                                     "    Dim a As Integer, b As Integer\n    hmemcpy a, b, 2")),
    dict(name="ByRef String", code=D("Declare Function GetWindowsDirectory Lib \"Kernel\" (ByVal B As String, "
                                     "ByVal N As Integer) As Integer",
                                     "    s$ = Space$(144)\n    n = GetWindowsDirectory(s$, 144)")),
    dict(name="Alias", code=D("Declare Function GetIni Lib \"Kernel\" Alias \"GetProfileInt\" (ByVal A As String, "
                              "ByVal K As String, ByVal D As Integer) As Integer",
                              "    Debug.Print GetIni(\"a\", \"b\", 0)")),
    dict(name="ordinal Alias", code=D("Declare Function Ord5 Lib \"User\" Alias \"#5\" () As Integer",
                                      "    Debug.Print Ord5()")),
    dict(name="suffix name", code=D("Declare Function GetVersion& Lib \"Kernel\" ()", "    v& = GetVersion&()")),
    dict(name="Function types", code=D("Declare Function F1% Lib \"X.DLL\" ()\nDeclare Function F2! Lib \"X.DLL\" ()\n"
                                       "Declare Function F3# Lib \"X.DLL\" ()\nDeclare Function F4$ Lib \"X.DLL\" ()\n"
                                       "Declare Function F5 Lib \"X.DLL\" () As Double",
                                       "    Debug.Print F1%(); F2!(); F3#(); F4$(); F5()")),
    dict(name="param types", code=D("Declare Sub P1 Lib \"X.DLL\" (ByVal a As Integer, ByVal b As Long, "
                                    "ByVal c As Single, ByVal d As Double, e As Integer, f As Long, g As Single, "
                                    "h As Double, i As String)",
                                    "    Dim a%, b&, c!, d#, s$\n    P1 1, 2, 3, 4, a%, b&, c!, d#, s$")),
    dict(name="suffixed params", code=D("Declare Sub P2 Lib \"X.DLL\" (ByVal a%, ByVal b&, c!, d#, ByVal e$)",
                                        "    P2 1, 2, x!, y#, \"s\"")),
    dict(name="user type param", bas=True,
         code="Type R\n    L As Integer\n    T As Integer\n    Rr As Integer\n    B As Integer\nEnd Type\n\n"
              "Declare Sub GetClientRect Lib \"User\" (ByVal H As Integer, Rc As R)\n",
         extra=[dict(code=S("    Dim r1 As R\n    GetClientRect hWnd, r1\n    Debug.Print r1.Rr"))]),
    dict(name="array element arg", code=D("Declare Sub P3 Lib \"X.DLL\" (a As Integer)",
                                          "    Static arr(10) As Integer\n    P3 arr(0)")),
    dict(name="ByVal at call", code=D("Declare Sub P4 Lib \"X.DLL\" (a As Any)", "    P4 ByVal 0&\n    P4 ByVal \"s\"")),
    dict(name="unused declare", code=D("Declare Sub Unused Lib \"X.DLL\" ()")),
    dict(name="declare in bas", bas=True, code="Declare Function GetTickCount Lib \"User\" () As Long\n",
         extra=[dict(code=S("    Debug.Print GetTickCount()"))]),
    dict(name="many declares", code=D("\n".join(f"Declare Sub Q{i} Lib \"X{i % 3}.DLL\" (ByVal a As Integer)"
                                               for i in range(8)), "    Q3 1\n    Q7 2")),
]
