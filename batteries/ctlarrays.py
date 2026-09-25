# Control arrays: indexed controls, Index parameters in events, Load and
# Unload of elements, element property access.
def ctl(cls, name, index=None, top=100, extra=""):
    idx = f"      Index = {index}\n" if index is not None else ""
    return (f"   Begin {cls} {name}\n      Height = 300\n{idx}      Left = 100\n      Top = {top}\n"
            f"      Width = 1200\n{extra}   End\n")


def arr(cls, name, idxs, extra=""):
    return "".join(ctl(cls, name, i, 100 + 400 * k, extra) for k, i in enumerate(idxs))


def S(body):
    return f"Sub Form_Load ()\n{body}\nEnd Sub\n"


BTN = arr("CommandButton", "B", [0, 1, 2])
cases = [
    dict(name="button array Click", controls=BTN,
         code="Sub B_Click (Index As Integer)\n    Debug.Print Index\nEnd Sub\n"),
    dict(name="single element array", controls=arr("CommandButton", "B", [0]),
         code="Sub B_Click (Index As Integer)\n    Debug.Print Index\nEnd Sub\n"),
    dict(name="array with gap", controls=arr("CommandButton", "B", [0, 2, 5]),
         code="Sub B_Click (Index As Integer)\n    Debug.Print Index\nEnd Sub\n"),
    dict(name="TextBox array KeyPress", controls=arr("TextBox", "T", [0, 1]),
         code="Sub T_KeyPress (Index As Integer, KeyAscii As Integer)\n    KeyAscii = 0\nEnd Sub\n\n"
              "Sub T_Change (Index As Integer)\n    Debug.Print T(Index).Text\nEnd Sub\n"),
    dict(name="MouseMove with Index", controls=arr("PictureBox", "P", [0, 1]),
         code="Sub P_MouseMove (Index As Integer, Button As Integer, Shift As Integer, X As Single, Y As Single)\n"
              "    Debug.Print Index; X\nEnd Sub\n"),
    dict(name="Load Unload element", controls=arr("CommandButton", "B", [0]),
         code=S("    Load B(1)\n    B(1).Top = 900\n    B(1).Visible = True\n    Unload B(1)")),
    dict(name="element property loop", controls=BTN,
         code=S("    Dim i As Integer\n    For i = 0 To 2\n        B(i).Caption = \"b\" & i\n    Next")),
    dict(name="element default property", controls=arr("TextBox", "T", [0, 1]),
         code=S("    T(0) = \"x\"\n    Debug.Print T(1)")),
    dict(name="element method", controls=arr("ListBox", "L", [0, 1]),
         code=S("    L(1).AddItem \"a\"\n    L(0).Clear\n    Debug.Print L(1).ListCount")),
    dict(name="array and plain control", controls=BTN + ctl("CommandButton", "C", None, 1400),
         code="Sub B_Click (Index As Integer)\n    C.Caption = B(Index).Caption\nEnd Sub\n\n"
              "Sub C_Click ()\n    B(0).SetFocus\nEnd Sub\n"),
    dict(name="Label array Caption", controls=arr("Label", "L", [0, 1], "      Caption = \"x\"\n"),
         code=S("    L(0).Caption = L(1).Caption")),
    dict(name="Timer array", controls="".join(
        f"   Begin Timer T\n      Index = {i}\n      Interval = 100\n      Left = 100\n      Top = {100 + 400 * i}\n   End\n"
        for i in (0, 1)),
         code="Sub T_Timer (Index As Integer)\n    T(Index).Enabled = False\nEnd Sub\n"),
    dict(name="control array passed to Sub", controls=BTN,
         code="Sub Fx (c As Control)\n    c.Caption = \"z\"\nEnd Sub\n\n" + S("    Fx B(1)")),
]
