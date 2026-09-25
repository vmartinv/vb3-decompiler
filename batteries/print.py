# Print method / Debug.Print / Print #: every separator form with each item type.
def S(body, decl=""):
    return f"{decl}\nSub Form_Load ()\n    Dim i%, l&, s!, d#, c@, t$, v\n{body}\nEnd Sub\n"


ITEMS = ["i%", "l&", "s!", "d#", "c@", "t$", "v", '"lit"', "1", "i% + 1"]
cases = []
for tgt, pre in [("Debug", "Debug.Print "), ("form", "Print "), ("Me", "Me.Print "), ("file", "Print #1, ")]:
    for it in ITEMS:
        cases.append(dict(name=f"{tgt} {it}", code=S(f"    {pre}{it}")))
        cases.append(dict(name=f"{tgt} {it};", code=S(f"    {pre}{it};")))
        cases.append(dict(name=f"{tgt} {it},", code=S(f"    {pre}{it},")))
        cases.append(dict(name=f"{tgt} {it}; x", code=S(f"    {pre}{it}; v")))
        cases.append(dict(name=f"{tgt} {it}, x", code=S(f"    {pre}{it}, v")))
    cases.append(dict(name=f"{tgt} empty", code=S(f"    {pre.rstrip(' ')}".rstrip(" ,") + ("," if tgt == "file" else ""))))
    cases.append(dict(name=f"{tgt} Tab Spc", code=S(f"    {pre}Tab(5); i%; Spc(2); t$; Tab(1); v")))
    cases.append(dict(name=f"{tgt} Tab,", code=S(f"    {pre}Tab(5), i%")))
    cases.append(dict(name=f"{tgt} Tab end", code=S(f"    {pre}i%; Tab(5)")))
    cases.append(dict(name=f"{tgt} Spc end", code=S(f"    {pre}Spc(3)")))
    cases.append(dict(name=f"{tgt} Spc,", code=S(f"    {pre}Spc(3), t$")))
    cases.append(dict(name=f"{tgt} lead comma", code=S(f"    {pre}, i%")))
    cases.append(dict(name=f"{tgt} double semi", code=S(f"    {pre}i%;; t$,, v")))
    cases.append(dict(name=f"{tgt} mixed", code=S(f"    {pre}i%, t$; v;")))
cases.append(dict(name="Picture.Print",
                  controls="   Begin PictureBox Pic1\n      Height = 500\n      Left = 0\n      Top = 0\n      Width = 500\n   End\n",
                  code=S("    Pic1.Print i%; t$")))
cases.append(dict(name="Printer.Print", code=S("    Printer.Print i%, t$;")))
