# DDE (Link* properties, methods and events on forms, text boxes, labels
# and picture boxes) and the OLE 2 control (MSOLE2.VBX).
TXT = "   Begin TextBox T\n      Height = 500\n      Left = 100\n      Top = 100\n      Width = 1500\n   End\n"
LBL = "   Begin Label L\n      Height = 500\n      Left = 100\n      Top = 700\n      Width = 1500\n   End\n"
PIC = "   Begin PictureBox P\n      Height = 500\n      Left = 100\n      Top = 1300\n      Width = 1500\n   End\n"
OLE = "   Begin OLE O\n      Height = 1500\n      Left = 100\n      Top = 100\n      Width = 2500\n   End\n"


def S(body, ev="Form_Load ()"):
    return f"Sub {ev}\n{body}\nEnd Sub\n"


cases = [
    dict(name="text link", controls=TXT,
         code=S("    T.LinkTopic = \"Excel|Sheet1\"\n    T.LinkItem = \"R1C1\"\n    T.LinkMode = 1\n"
                "    T.LinkTimeout = 100\n    T.LinkRequest\n    T.LinkPoke\n    T.LinkExecute \"[Beep]\"")),
    dict(name="label link", controls=LBL, code=S("    L.LinkTopic = \"a|b\"\n    L.LinkMode = 2\n    L.LinkRequest")),
    dict(name="picture LinkSend", controls=PIC, code=S("    P.LinkMode = 1\n    P.LinkSend")),
    dict(name="design link props", controls=TXT.replace("      Height", "      LinkItem = \"R1C1\"\n      "
                                                                     "LinkTimeout = 75\n      Height"),
         code=S("    Debug.Print T.LinkItem")),
    dict(name="form LinkMode source", props=["LinkMode = 1", "LinkTopic = \"Srv\""],
         code=S("    Debug.Print LinkMode") + "\n" + S("    Debug.Print CmdStr: Cancel = 0",
                                                     "Form_LinkExecute (CmdStr As String, Cancel As Integer)")),
    dict(name="control link events", controls=TXT,
         code=S("    Debug.Print 1", "T_LinkOpen (Cancel As Integer)") + "\n" +
              S("    Debug.Print 2", "T_LinkClose ()") + "\n" +
              S("    Debug.Print LinkErr", "T_LinkError (LinkErr As Integer)") + "\n" +
              S("    Debug.Print 3", "T_LinkNotify ()") + "\n" + S("    Debug.Print 4", "T_Change ()")),
    dict(name="OLE control", vbx=["MSOLE2.VBX"], controls=OLE,
         code=S("    O.Class = \"Paint.Picture\"\n    O.SourceDoc = \"a.bmp\"\n    O.Action = 1\n"
                "    Debug.Print O.AppIsRunning; O.OLEType\n    O.Verb = 0\n    O.Action = 7")),
    dict(name="OLE events", vbx=["MSOLE2.VBX"], controls=OLE,
         code=S("    Debug.Print 1", "O_Updated (Code As Integer)")),
]
