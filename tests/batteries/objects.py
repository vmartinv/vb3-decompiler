# Objects: Me, Screen, App, Clipboard, Printer, Debug; Set / Nothing / Is,
# TypeOf ... Is, form and control variables, New form instances.
BTN = "   Begin CommandButton B\n      Height = 500\n      Left = 100\n      Top = 100\n      Width = 1000\n   End\n"
TXT = "   Begin TextBox T\n      Height = 500\n      Left = 100\n      Top = 700\n      Width = 1000\n   End\n"


def S(body):
    return f"Sub Form_Load ()\n{body}\nEnd Sub\n"


cases = [
    dict(name="Me properties", code=S("    Me.Caption = \"x\"\n    Debug.Print Me.Width; Me.hWnd")),
    dict(name="Screen", code=S("    Screen.MousePointer = 11\n    Debug.Print Screen.Width; Screen.Height; "
                               "Screen.TwipsPerPixelX\n    Debug.Print Screen.FontCount; Screen.Fonts(0)")),
    dict(name="Screen active", controls=BTN, code=S("    Debug.Print Screen.ActiveForm.Caption\n"
                                                     "    Debug.Print Screen.ActiveControl.Tag")),
    dict(name="App", code=S("    Debug.Print App.Path; App.EXEName; App.Title; App.HelpFile\n"
                            "    App.Title = \"t\"\n    If App.PrevInstance Then End")),
    dict(name="Clipboard", code=S("    Clipboard.Clear\n    Clipboard.SetText \"a\"\n    x$ = Clipboard.GetText()\n"
                                  "    If Clipboard.GetFormat(1) Then Debug.Print x$")),
    dict(name="Clipboard picture", code=S("    Clipboard.SetData Me.Image, 2\n    Me.Picture = Clipboard.GetData(2)")),
    dict(name="Printer props", code=S("    Printer.FontSize = 10\n"
                                      "    Debug.Print Printer.Page; Printer.ScaleWidth")),
    dict(name="Debug", code=S("    Debug.Print\n    Debug.Print 1, 2; 3\n    Debug.Print Tab(5); Spc(2); \"x\"")),
    dict(name="control var", controls=BTN,
         code=S("    Dim c As Control\n    Set c = B\n    c.Caption = \"x\"\n    Debug.Print c.Left")),
    dict(name="specific control var", controls=BTN + TXT,
         code=S("    Dim b2 As CommandButton, t2 As TextBox\n    Set b2 = B\n    Set t2 = T\n"
                "    t2.Text = b2.Caption")),
    dict(name="form var", code=S("    Dim f As Form\n    Set f = Me\n    f.Caption = \"x\"\n    Set f = Nothing")),
    dict(name="Is Nothing", controls=BTN,
         code=S("    Dim c As Control\n    If c Is Nothing Then Set c = B\n    If c Is B Then Debug.Print 1")),
    dict(name="TypeOf", controls=BTN + TXT,
         code=S("    Dim c As Control\n    Set c = T\n    If TypeOf c Is TextBox Then Debug.Print 1\n"
                "    If TypeOf c Is CommandButton Then Else Debug.Print 2")),
    dict(name="TypeOf ElseIf", controls=BTN + TXT,
         code=S("    Dim c As Control\n    Set c = B\n    If TypeOf c Is TextBox Then\n        Debug.Print 1\n"
                "    ElseIf TypeOf c Is CommandButton Then\n        Debug.Print 2\n    End If")),
    dict(name="control param", controls=BTN,
         code="Sub Pc (c As Control)\n    c.Enabled = False\nEnd Sub\n\n" + S("    Pc B")),
    dict(name="form param", code="Sub Pf (f As Form)\n    f.Caption = \"y\"\nEnd Sub\n\n" + S("    Pf Me")),
    dict(name="New form", code=S("    Dim f As New @SELF@\n    f.Caption = \"x\"\n    f.Show")),
    dict(name="Set New", code=S("    Dim f As Form\n    Set f = New @SELF@\n    Load f\n    f.Show")),
    dict(name="Controls collection", controls=BTN + TXT,
         code=S("    For i = 0 To Controls.Count - 1\n        Debug.Print Controls(i).Tag\n    Next")),
    dict(name="Forms collection", code=S("    For i = 0 To Forms.Count - 1\n        Debug.Print Forms(i).Caption\n"
                                         "    Next")),
    dict(name="object Tag chain", controls=BTN, code=S("    Me.B.Caption = Me.Caption")),
    dict(name="global control var", controls=BTN, bas=True,
         code="Global gc As Control\n", extra=[dict(controls=BTN, code=S("    Set gc = B\n    gc.Caption = \"g\""))]),
]
