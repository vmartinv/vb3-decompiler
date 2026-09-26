# Forms: form properties, MDI parent/child, startup form vs Sub Main, a
# .bas-only project, form events and methods.
def S(body):
    return f"Sub Form_Load ()\n{body}\nEnd Sub\n"


LOAD = S("    Debug.Print 1")
PROPS = ["BorderStyle = 1", "BorderStyle = 3", "BorderStyle = 0", "ControlBox = 0", "MaxButton = 0",
         "MinButton = 0", "WindowState = 2", "BackColor = &H00FF0000&", "ForeColor = &H000000FF&",
         "KeyPreview = -1", "Enabled = 0", "MousePointer = 11", "Tag = \"t\"", "AutoRedraw = -1",
         "FontTransparent = 0", "DrawWidth = 3", "DrawStyle = 2", "DrawMode = 7", "FillStyle = 0",
         "FillColor = &H0000FF00&", "ScaleMode = 3", "Visible = 0", "LinkMode = 1", "FontName = \"Arial\"",
         "FontSize = 12", "FontBold = 0", "ClipControls = 0"]

FORM_EVENTS = {
    "Load": "", "Unload": "Cancel As Integer", "QueryUnload": "Cancel As Integer, UnloadMode As Integer",
    "Resize": "", "Activate": "", "Deactivate": "", "Paint": "", "Click": "", "DblClick": "",
    "GotFocus": "", "LostFocus": "", "KeyDown": "KeyCode As Integer, Shift As Integer",
    "KeyPress": "KeyAscii As Integer", "KeyUp": "KeyCode As Integer, Shift As Integer",
    "MouseDown": "Button As Integer, Shift As Integer, X As Single, Y As Single",
    "MouseMove": "Button As Integer, Shift As Integer, X As Single, Y As Single",
    "MouseUp": "Button As Integer, Shift As Integer, X As Single, Y As Single",
    "DragDrop": "Source As Control, X As Single, Y As Single",
    "DragOver": "Source As Control, X As Single, Y As Single, State As Integer",
    "LinkOpen": "Cancel As Integer", "LinkClose": "", "LinkError": "LinkErr As Integer",
    "LinkExecute": "CmdStr As String, Cancel As Integer",
}


def events(names):
    return "".join(f"Sub Form_{e} ({FORM_EVENTS[e]})\n    Debug.Print 1\nEnd Sub\n\n" for e in names)


cases = [dict(name=f"prop {p}", props=[p], code=LOAD) for p in PROPS] + [
    dict(name="all form events", code=events(FORM_EVENTS)),
    dict(name="form methods", code=S("    Me.Show\n    Me.Hide\n    Me.Refresh\n    Me.Cls\n    Me.Move 0, 0, 3000\n"
                                     "    Me.ZOrder 0\n    Me.SetFocus")),
    dict(name="other form by name", code=S("    @SELF@.Caption = \"x\"\n    Unload Me"),),
    dict(name="Show modal", code=S("    Dim f As Form\n    Set f = Me\n    f.Show 1")),
    dict(name="MDI parent and child", nostart=True, mdi=True,
         code="Sub MDIForm_Load ()\n    Debug.Print 1\nEnd Sub\n\nSub MDIForm_Unload (Cancel As Integer)\n"
              "    Debug.Print 2\nEnd Sub\n",
         extra=[dict(props=["MDIChild = -1"], code=S("    Debug.Print 3"))]),
    dict(name="MDIForm events", nostart=True, mdi=True,
         code="".join(f"Sub MDIForm_{e} ({FORM_EVENTS[e]})\n    Debug.Print 1\nEnd Sub\n\n"
                      for e in ("Load", "Unload", "QueryUnload", "Resize", "Activate", "Deactivate",
                                "LinkOpen", "LinkClose", "LinkError", "LinkExecute", "DragDrop", "DragOver"))),
    dict(name="MDI arrange", nostart=True, mdi=True,
         code="Sub MDIForm_Load ()\n    Arrange 1\n    ActiveForm.Caption = \"x\"\nEnd Sub\n",
         extra=[dict(props=["MDIChild = -1"], code=LOAD)]),
    dict(name="Sub Main only", nostart=True, bas=True, code="Sub Main ()\n    Debug.Print 1\nEnd Sub\n"),
    dict(name="Sub Main shows form", nostart=True, bas=True,
         code="Sub Main ()\n    Load @M1@\n    @M1@.Show\nEnd Sub\n", extra=[dict(code=LOAD)]),
]
