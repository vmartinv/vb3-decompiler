# Custom controls (the VBX files shipped with VB3 Professional): each class
# on a form, its custom properties at design time and in code, its methods
# and events.
INVISIBLE = {"CommonDialog", "MSComm"}  # no size at design time


def ctl(cls, name, props=""):
    body = "".join(f"      {ln}\n" for ln in props.split("\n") if ln)
    size = "" if cls in INVISIBLE else "      Height = 500\n      Width = 1500\n"
    return f"   Begin {cls} {name}\n{body}{size}      Left = 100\n      Top = 100\n   End\n"


def S(body, ev="Form_Load ()"):
    return f"Sub {ev}\n{body}\nEnd Sub\n"


def V(name, vbx, cls, code, props="", **kw):
    return dict(name=name, vbx=[vbx], controls=ctl(cls, "X", props), code=code, **kw)


cases = [
    V("Grid", "GRID.VBX", "Grid", S("    X.Rows = 5: X.Cols = 4\n    X.Row = 1: X.Col = 1: X.Text = \"a\"\n"
                                    "    X.ColWidth(1) = 900\n    X.AddItem \"x\" & Chr$(9) & \"y\"\n    X.RemoveItem 2")),
    V("Grid design", "GRID.VBX", "Grid", S("    Debug.Print X.FixedRows"), "Rows = 3\nCols = 6\nFixedCols = 0"),
    V("Grid events", "GRID.VBX", "Grid", S("    Debug.Print 1", "X_RowColChange ()") + "\n" +
      S("    Debug.Print 2", "X_SelChange ()")),
    V("Gauge", "GAUGE.VBX", "Gauge", S("    X.Value = 50\n    Debug.Print X.Max"), "Max = 200"),
    V("SpinButton", "SPIN.VBX", "SpinButton", S("    Debug.Print 1", "X_SpinUp ()") + "\n" +
      S("    Debug.Print 2", "X_SpinDown ()")),
    V("SSCommand", "THREED.VBX", "SSCommand", S("    X.Caption = \"c\"\n    X.Font3D = 2", "X_Click ()"),
      "Caption = \"b\"\nBevelWidth = 3"),
    V("SSPanel", "THREED.VBX", "SSPanel", S("    X.FloodPercent = 40\n    X.Caption = \"p\""), "FloodType = 1"),
    V("SSCheck SSOption", "THREED.VBX", "SSCheck", S("    X.Value = True", "X_Click (Value As Integer)")),
    V("MaskEdBox", "MSMASKED.VBX", "MaskEdBox", S("    X.Mask = \"##-##\"\n    Debug.Print X.Text; X.ClipText"),
      "MaxLength = 5\nMask = \"#####\""),
    V("CommonDialog", "CMDIALOG.VBX", "CommonDialog",
      S("    X.Filter = \"Text|*.txt\"\n    X.Action = 1\n    Debug.Print X.Filename")),
    V("Outline", "MSOUTLIN.VBX", "Outline", S("    X.AddItem \"a\"\n    X.AddItem \"b\"\n    X.Indent(1) = 2\n"
                                              "    X.Expand(0) = True", ) + "\n" +
      S("    Debug.Print ListIndex", "X_Expand (ListIndex As Integer)")),
    V("MhState", "KEYSTAT.VBX", "MhState", S("    X.Value = True\n    Debug.Print X.Style"), "Style = 1"),
    V("MMControl", "MCI.VBX", "MMControl", S("    X.DeviceType = \"WaveAudio\"\n    X.Command = \"Open\"")),
    V("MSComm", "MSCOMM.VBX", "MSComm", S("    X.CommPort = 1\n    X.Settings = \"9600,n,8,1\"\n"
                                          "    X.PortOpen = True\n    X.Output = \"AT\"\n    s$ = X.Input") + "\n" +
      S("    Debug.Print X.CommEvent", "X_OnComm ()")),
    V("control var of VBX class", "GRID.VBX", "Grid",
      S("    Dim g As Control\n    Set g = X\n    If TypeOf g Is Grid Then g.Rows = 2")),
    dict(name="two VBX files", vbx=["GRID.VBX", "SPIN.VBX"],
         controls=ctl("Grid", "G1") + ctl("SpinButton", "S1"), code=S("    G1.Rows = 3", "S1_SpinUp ()")),
]
