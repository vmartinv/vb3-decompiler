# Name-dependent layout: procedure name lengths (compile-time name pool),
# first-mention order (call statements vs function calls in expressions),
# Static/Function/Declare names, names shared across modules, .bas stems.
def sub(n, body="    x = 1"):
    return f"Sub {n} ()\n{body}\nEnd Sub\n"

def fn(n, body=None):
    return f"Function {n} ()\n    {n} = {body or 1}\nEnd Function\n"

cases = []
for L in [1, 2, 3, 4, 5, 8, 13, 21, 31, 40]:
    n = ("Q" + "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ")[:L]
    cases.append(dict(name=f"sub len {L}", code=sub("Form_Load", f"    {n}") + sub(n)))
    cases.append(dict(name=f"function len {L}", code=sub("Form_Load", f"    x = {n}()") + fn(n)))
cases += [
    dict(name="call before def, 3 subs", code=sub("Form_Load", "    Zed\n    Alpha\n    Mid1") + sub("Alpha") + sub("Mid1") + sub("Zed")),
    dict(name="function in expr then call stmt", code=sub("Form_Load", "    x = Fa() + 1\n    Sb") + fn("Fa") + sub("Sb")),
    dict(name="Call with parens", code=sub("Form_Load", "    Call Pq(1)") + "Sub Pq (a)\n    x = a\nEnd Sub\n"),
    dict(name="Static Sub", code="Static Sub Stat1 ()\n    c = c + 1\nEnd Sub\n" + sub("Form_Load", "    Stat1")),
    dict(name="Declare", code='Declare Function GetTickCount Lib "User" () As Long\n' + sub("Form_Load", "    x = GetTickCount()")),
    dict(name="Declare Alias", code='Declare Function Gtc Lib "User" Alias "GetTickCount" () As Long\n' + sub("Form_Load", "    x = Gtc()")),
    dict(name="Declare Sub unused", code='Declare Sub MessageBeep Lib "User" (ByVal n As Integer)\n' + sub("Form_Load")),
    dict(name="unused general sub", code=sub("Form_Load") + sub("Unused12")),
    dict(name="events only", code=sub("Form_Load") + sub("Form_Click") + sub("Form_Resize")),
    dict(name="bas proc called from form", code=sub("Form_Load", "    BasProc1"),
         extra=[dict(bas=True, code=sub("BasProc1"))]),
    dict(name="bas function called from form", code=sub("Form_Load", "    x = BasFn2()"),
         extra=[dict(bas=True, code=fn("BasFn2"))]),
    dict(name="two bas modules", code=sub("Form_Load", "    Pa1\n    Pb2"),
         extra=[dict(bas=True, code=sub("Pa1")), dict(bas=True, code=sub("Pb2", "    Pa1"))]),
    dict(name="same general name in 2 forms", code=sub("Form_Load", "    Shared1") + sub("Shared1"),
         extra=[dict(code=sub("Form_Load", "    Shared1") + sub("Shared1"))]),
    dict(name="global var in bas", code=sub("Form_Load", "    Gv = 1"),
         extra=[dict(bas=True, code="Global Gv As Integer\n")]),
    dict(name="long local names", code=sub("Form_Load", "    Dim LocalVariableNameThatIsLong\n    LocalVariableNameThatIsLong = 1")),
]
