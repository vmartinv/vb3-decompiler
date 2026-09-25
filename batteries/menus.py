# Menus: nesting, shortcuts, checked / disabled / invisible items,
# separators, menu control arrays, and code using them.
def menu(name, caption, props="", children="", index=None, depth=1):
    ind = "   " * depth
    idx = f"{ind}   Index = {index}\n" if index is not None else ""
    body = "".join(f"{ind}   {ln}\n" for ln in props.split("\n") if ln)
    return f"{ind}Begin Menu {name}\n{ind}   Caption = \"{caption}\"\n{idx}{body}{children}{ind}End\n"


def S(body):
    return f"Sub Form_Load ()\n{body}\nEnd Sub\n"


CLICK = "Sub {n}_Click ()\n    Debug.Print 1\nEnd Sub\n\n"
FILE = menu("mFile", "&File", children=menu("mOpen", "&Open", depth=2) + menu("mExit", "E&xit", depth=2))
cases = [
    dict(name="one menu", controls=menu("mFile", "&File"), code=CLICK.format(n="mFile")),
    dict(name="nested", controls=FILE, code=CLICK.format(n="mOpen") + CLICK.format(n="mExit")),
    dict(name="three levels", controls=menu("mA", "A", children=menu("mB", "B", depth=2, children=menu(
        "mC", "C", depth=3))), code=CLICK.format(n="mC")),
    dict(name="shortcuts", controls=menu("mFile", "&File", children="".join(
        menu(f"m{k}", f"i{k}", f"Shortcut = {sc}", depth=2)
        for k, sc in enumerate(["^A", "^Z", "{F1}", "^{F12}", "+{F5}", "^+{F2}", "{DEL}", "%{BKSP}"]))),
         code=S("    Debug.Print 1")),
    dict(name="checked disabled invisible", controls=menu("mFile", "&File", children=
        menu("m1", "a", "Checked = -1", depth=2) + menu("m2", "b", "Enabled = 0", depth=2)
        + menu("m3", "c", "Visible = 0", depth=2) + menu("m4", "d", "HelpContextID = 7", depth=2)),
         code=S("    Debug.Print 1")),
    dict(name="separator", controls=menu("mFile", "&File", children=
        menu("mOpen", "&Open", depth=2) + menu("mSep", "-", depth=2) + menu("mExit", "E&xit", depth=2)),
         code=CLICK.format(n="mExit")),
    dict(name="menu array", controls=menu("mFile", "&File", children="".join(
        menu("mRecent", f"r{i}", index=i, depth=2) for i in range(3))),
         code="Sub mRecent_Click (Index As Integer)\n    Debug.Print mRecent(Index).Caption\nEnd Sub\n"),
    dict(name="menu array Load", controls=menu("mFile", "&File", children=menu("mRecent", "r0", index=0, depth=2)),
         code=S("    Load mRecent(1)\n    mRecent(1).Caption = \"r1\"\n    Unload mRecent(1)")),
    dict(name="menu properties in code", controls=FILE,
         code=S("    mOpen.Checked = Not mOpen.Checked\n    mExit.Enabled = False\n    mFile.Visible = True\n"
                "    mOpen.Caption = \"x\"")),
    dict(name="PopupMenu", controls=menu("mPop", "pop", "Visible = 0", children=menu("mP1", "p1", depth=2)),
         code="Sub Form_MouseUp (Button As Integer, Shift As Integer, X As Single, Y As Single)\n"
              "    If Button = 2 Then PopupMenu mPop\nEnd Sub\n"),
    dict(name="WindowList", controls=menu("mWin", "&Window", "WindowList = -1"), code=S("    Debug.Print 1")),
    dict(name="two top menus", controls=FILE + menu("mEdit", "&Edit", children=menu("mCopy", "&Copy", "Shortcut = ^C",
                                                                                  depth=2)),
         code=CLICK.format(n="mCopy") + CLICK.format(n="mOpen")),
]
