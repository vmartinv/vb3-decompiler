# Error handling: On Error GoTo / Resume Next / GoTo 0, Resume forms,
# Err / Erl / Error$, Error statement, handlers in Functions, line numbers.
def S(body):
    return f"Sub Form_Load ()\n{body}\nEnd Sub\n"


cases = [
    dict(name="GoTo handler", code=S("    On Error GoTo Eh\n    x = 1 / 0\n    Exit Sub\nEh:\n    Debug.Print Err\n"
                                     "    Resume Next")),
    dict(name="Resume Next", code=S("    On Error Resume Next\n    x = 1 / 0\n    If Err Then Debug.Print Error$")),
    dict(name="GoTo 0", code=S("    On Error Resume Next\n    x = 1\n    On Error GoTo 0\n    x = 2")),
    dict(name="Resume", code=S("    On Error GoTo Eh\n    x = 1 / y\n    Exit Sub\nEh:\n    y = 1\n    Resume")),
    dict(name="Resume 0", code=S("    On Error GoTo Eh\n    x = 1 / y\n    Exit Sub\nEh:\n    y = 1\n    Resume 0")),
    dict(name="Resume label", code=S("    On Error GoTo Eh\n    x = 1 / 0\nDone:\n    Exit Sub\nEh:\n    Resume Done")),
    dict(name="Resume line number", code=S("    On Error GoTo 900\n    x = 1 / 0\n800 Exit Sub\n900 Resume 800")),
    dict(name="Erl", code=S("10  On Error GoTo Eh\n20  x = 1 / 0\n    Exit Sub\nEh:\n    Debug.Print Erl; Err\n"
                            "    Resume Next")),
    dict(name="Error$ of code", code=S("    Debug.Print Error$(11); Error(5)")),
    dict(name="Error statement", code=S("    On Error Resume Next\n    Error 1000\n    Debug.Print Err")),
    dict(name="Err assign", code=S("    Err = 0\n    Err = 5")),
    dict(name="handler in Function", code="Function Fz (a)\n    On Error GoTo Fe\n    Fz = 1 / a\n"
                                          "    Exit Function\nFe:\n    Fz = 0\n    Resume Next\nEnd Function\n\n"
                                          + S("    Debug.Print Fz(0)")),
    dict(name="nested handlers", code=S("    On Error GoTo E1\n    x = 1 / 0\n    On Error GoTo E2\n    x = 1 / 0\n"
                                        "    Exit Sub\nE1:\n    Resume Next\nE2:\n    Resume Next")),
    dict(name="Select Case Err", code=S("    On Error GoTo Eh\n    x = 1 / 0\n    Exit Sub\nEh:\n    Select Case Err\n"
                                        "    Case 11\n        Resume Next\n    Case Else\n        Error Err\n"
                                        "    End Select")),
]
