# Miscellaneous runtime statements and functions: DoEvents, Shell, Beep,
# Environ$, Command$, SendKeys, AppActivate, Format$ masks, file system
# statements, binary/random file I/O (Get/Put/Seek/EOF/LOF/Loc), Date/Time.
def S(body):
    return f"Sub Form_Load ()\n{body}\nEnd Sub\n"


FMT = ["\"0.00\"", "\"#,##0\"", "\"0%\"", "\"hh:mm:ss\"", "\"dd/mm/yy\"", "\"Currency\"", "\"Fixed\"",
       "\"@@@-@@\"", "\"&&&\"", "\">\"", "\"0.0E+00\"", "\"yes/no\""]
cases = [
    dict(name="DoEvents", code=S("    DoEvents\n    n = DoEvents()")),
    dict(name="Shell", code=S("    t = Shell(\"notepad.exe\", 1)\n    t = Shell(\"calc\")")),
    dict(name="Beep", code=S("    Beep")),
    dict(name="Environ", code=S("    Debug.Print Environ$(\"PATH\"); Environ(\"TEMP\"); Environ$(1)")),
    dict(name="Command", code=S("    Debug.Print Command$; Command")),
    dict(name="SendKeys AppActivate", code=S("    AppActivate \"Notepad\"\n    SendKeys \"abc{ENTER}\", True\n"
                                             "    SendKeys \"x\"")),
    dict(name="Format masks", code=S("\n".join(f"    Debug.Print Format$(1234.5, {f}); Format(1, {f})" for f in FMT))),
    dict(name="Format date", code=S("    Debug.Print Format$(Now, \"dddd, mmmm d, yyyy\")")),
    dict(name="file system", code=S("    MkDir \"c:\\t\"\n    ChDir \"c:\\t\"\n    ChDrive \"c\"\n    Kill \"*.tmp\"\n"
                                    "    Name \"a.txt\" As \"b.txt\"\n    RmDir \"c:\\t\"\n    Debug.Print CurDir$; CurDir$(\"d\")")),
    dict(name="Dir", code=S("    f$ = Dir$(\"*.*\")\n    Do While f$ <> \"\"\n        f$ = Dir$\n    Loop\n"
                            "    Debug.Print Dir(\"c:\\\", 16)")),
    dict(name="Dir forms", code=S("    Debug.Print Dir$(\"a\", 2)\n    Debug.Print Dir(\"a\")\n    Debug.Print Dir\n"
                                  "    Debug.Print Dir(\"a\", 4)")),
    dict(name="FileLen FileDateTime", code=S("    Debug.Print FileLen(\"a\"); FileDateTime(\"a\"); GetAttr(\"a\")\n"
                                             "    SetAttr \"a\", 1")),
    dict(name="binary Get Put", code=S("    Dim n As Integer, s As String * 4\n    Open \"f\" For Binary As #1\n"
                                       "    Put #1, 1, n\n    Get #1, , s\n    Get #1, 5, n\n    Seek #1, 10\n"
                                       "    Debug.Print Seek(1); Loc(1); LOF(1); EOF(1)\n    Close #1")),
    dict(name="random Get Put", code=S("    Dim r As String * 10\n    Open \"f\" For Random As #2 Len = 10\n"
                                       "    Put #2, 3, r\n    Get #2, 3, r\n    Close")),
    dict(name="Input$ binary", code=S("    Open \"f\" For Binary As #1\n    s$ = Input$(5, #1)\n    s$ = Input$(3, 1)\n"
                                      "    Close #1")),
    dict(name="FreeFile Reset", code=S("    n = FreeFile\n    Open \"f\" For Output As n\n    Print #n, 1\n"
                                       "    Width #n, 40\n    Reset")),
    dict(name="Lock Unlock", code=S("    Open \"f\" For Binary Shared As #1\n    Lock #1, 1 To 5\n    Unlock #1, 1 To 5\n"
                                    "    Lock #1\n    Unlock #1\n    Close #1")),
    dict(name="Lock record forms", code=S("    Open \"f\" For Random As #1\n    Lock #1, 3\n    Unlock #1, 3\n"
                                          "    Lock #1, To 4\n    Unlock 1, 2 To 6\n    Close #1")),
    dict(name="Date Time statements", code=S("    Date$ = \"01-01-94\"\n    Time$ = \"12:00:00\"\n"
                                             "    Debug.Print Date$; Time$; Timer; Now")),
    dict(name="Randomize", code=S("    Randomize\n    Randomize 5\n    Randomize Timer\n    Debug.Print Rnd; Rnd(1)")),
    dict(name="Stop End", code=S("    If x Then Stop\n    If y Then End")),
    dict(name="InputBox MsgBox", code=S("    s$ = InputBox$(\"p\", \"t\", \"d\")\n    r = MsgBox(\"m\", 36, \"t\")\n"
                                        "    MsgBox \"x\"")),
    dict(name="QBColor RGB", code=S("    BackColor = QBColor(3)\n    ForeColor = RGB(1, 2, 3)")),
]
