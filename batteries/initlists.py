# Init lists (the chunk `u16 len, u16 count, 1E 00, count x u16` after a
# module image and after the global image): fixed-size arrays and String
# constants, in the order of VB.EXE's 16-bucket name hash (bucket =
# (name-table offset >> 1) & 15), then Static arrays by each procedure's
# 8-bucket table. The first name's length `d` shifts every later name's
# offset; the last one's shrinks by as much, so the name-table sizes stay
# the same and only the lists' order changes.
FORM_MIX = """Dim {P} As Integer
Dim s1 As String, x1 As Long
Dim a1(5) As String, av(3), ab(2) As Integer
Const K1 = 5, K2 = "k"

Sub Form_Load ()
    {P} = 1: s1 = "a": x1 = 3: a1(1) = "x": av(1) = 5: ab(1) = 2
    {Q} = K1
End Sub

Sub Form_Click ()
    Debug.Print s1; x1; a1(1); av(1); ab(1); K2
End Sub
"""

FORM_BUCKET = """Dim {P} As Integer
Dim aa(3) As Integer
Dim Ffffffffffffffffffffff As Integer
Dim bb(2) As String
Const CS = "s", CN = 3
Dim cc(2) As Long, dd(1 To 3, 2) As Long

Sub Form_Load ()
    {P} = 1: aa(1) = 2: Ffffffffffffffffffffff = 3: bb(1) = CS: cc(1) = CN: dd(1, 1) = 3
    {Q} = 1
End Sub

Sub Form_Click ()
    Debug.Print aa(1); bb(1); cc(1); dd(1, 1); CS; CN; Ffffffffffffffffffffff
End Sub
"""

FORM_STATIC = """Sub Form_Load ()
    Dim {P} As Integer
    Static sa(3) As String, sb(2) As Integer, sc(1) As Long
    {P} = 1: sa(1) = "x": sb(1) = 2: sc(1) = 3
    {Q} = 1
End Sub
"""

BAS_GLOBALS = """Global {P} As Integer
Global gs{i} As String, ga{i}(5) As String, gx{i} As Long
Global gav{i}(3), gac{i}(2) As Integer
Global Const GK1{i} = 5, GK2{i} = "k", GK3{i} = "zz"
Dim ma(4) As String, mv(2)
Const MK = "m"
Global {Q} As Integer

Sub Foo{i} ()
    {P} = 1: gs{i} = "a": ga{i}(1) = "x": gx{i} = 3: gav{i}(1) = 5: gac{i}(1) = 2
    ma(1) = MK: mv(1) = GK2{i} & GK3{i}
    {Q} = GK1{i}
End Sub

Sub Bar{i} ()
    Debug.Print ma(1); mv(1); MK
End Sub
"""


def names(d, tag=""):
    return dict(P=f"P{tag}" + "p" * d, Q=f"Q{tag}" + "q" * (32 - d))


cases = []
for d in [0, 6, 22, 24]:
    cases.append(dict(name=f"form arrays+consts d={d}", code=FORM_MIX.format(**names(d))))
for d in [0, 1, 2, 12, 15, 18, 27]:
    cases.append(dict(name=f"same bucket d={d}", code=FORM_BUCKET.format(**names(d))))
for d in [0, 2, 4, 6, 8, 10, 12, 14]:
    cases.append(dict(name=f"Static arrays d={d}", code=FORM_STATIC.format(**names(d))))
for i, d in enumerate([0, 1, 2, 3, 9, 11, 15, 16, 27]):
    cases.append(dict(name=f"Globals d={d}", solo=True, bas=True, code=BAS_GLOBALS.format(i=i, **names(d, i)),
                      extra=[dict(code=f"Sub Form_Load ()\n    Foo{i}\n    Debug.Print GK2{i}\nEnd Sub\n")]))
