# Graphics methods on forms, picture boxes and the printer: Line (B, BF,
# Step), Circle (all optional arguments), PSet, Point, Cls, Scale,
# TextWidth/TextHeight, CurrentX/Y, Print on objects.
PIC = "   Begin PictureBox P\n      Height = 1500\n      Left = 100\n      Top = 100\n      Width = 2000\n   End\n"


def S(body):
    return f"Sub Form_Click ()\n{body}\nEnd Sub\n"


STMTS = [
    ("Line", "Line (0, 0)-(100, 100)"),
    ("Line color", "Line (0, 0)-(100, 100), RGB(255, 0, 0)"),
    ("Line B", "Line (0, 0)-(100, 100), , B"),
    ("Line BF", "Line (0, 0)-(100, 100), QBColor(2), BF"),
    ("Line Step", "Line Step(10, 10)-Step(50, 50)"),
    ("Line from current", "Line -(200, 200)"),
    ("Line Step to", "Line -Step(20, 20), 0"),
    ("Circle", "Circle (500, 500), 100"),
    ("Circle color", "Circle (500, 500), 100, RGB(0, 0, 255)"),
    ("Circle arc", "Circle (500, 500), 100, , 0, 3.14"),
    ("Circle aspect", "Circle (500, 500), 100, , , , .5"),
    ("Circle all", "Circle Step(5, 5), 100, 0, -1, -2, 2"),
    ("PSet", "PSet (10, 10)"),
    ("PSet color Step", "PSet Step(1, 1), RGB(1, 2, 3)"),
    ("Point", "x = Point(10, 10)"),
    ("Cls", "Cls"),
    ("Scale", "Scale (0, 0)-(100, 100)"),
    ("Scale reset", "Scale"),
    ("TextWidth Height", "x = TextWidth(\"abc\") + TextHeight(\"abc\")"),
    ("CurrentX Y", "CurrentX = 10: CurrentY = 20"),
    ("Refresh", "Refresh"),
]
cases = [dict(name=f"form {n}", code=S(f"    {s}")) for n, s in STMTS] + \
    [dict(name=f"pic {n}", controls=PIC, code=S(f"    P.{s}")) for n, s in STMTS if not s.startswith(("x = ", "CurrentX"))] + [
    dict(name="pic Point", controls=PIC, code=S("    x = P.Point(10, 10)")),
    dict(name="pic TextWidth", controls=PIC, code=S("    x = P.TextWidth(\"abc\") + P.TextHeight(\"abc\")")),
    dict(name="pic CurrentX Y", controls=PIC, code=S("    P.CurrentX = 10: P.CurrentY = 20")),
    dict(name="pic Print", controls=PIC, code=S("    P.Print \"a\"; 1, 2\n    P.Print")),
    dict(name="Printer", code=S("    Printer.Print \"a\"\n    Printer.Line (0, 0)-(100, 100)\n"
                                "    Printer.Circle (500, 500), 100\n    Printer.NewPage\n    Printer.EndDoc")),
    dict(name="graphics properties", controls=PIC,
         code=S("    P.DrawWidth = 2: P.DrawStyle = 1: P.DrawMode = 13: P.FillStyle = 0\n"
                "    P.FillColor = QBColor(4): P.ForeColor = &HFF&: P.AutoRedraw = True\n"
                "    P.ScaleMode = 3: P.ScaleLeft = 0: P.ScaleTop = 0: P.ScaleWidth = 10: P.ScaleHeight = 10")),
    dict(name="LoadPicture SavePicture", controls=PIC,
         code=S("    P.Picture = LoadPicture(\"c:\\a.bmp\")\n    SavePicture P.Image, \"c:\\b.bmp\"\n"
                "    P.Picture = LoadPicture()")),
]
