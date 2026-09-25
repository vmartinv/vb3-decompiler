# Type fields of every type: store, load, nested, array fields, arrays of Types (in a .bas).
bas = True
header = """Type TIn
    q As Integer
End Type
Type TA
    i As Integer
    l As Long
    s As Single
    d As Double
    c As Currency
    t As String
    f As String * 5
    v As Variant
    n As TIn
    a(3) As Integer
End Type
Dim r As TA
Dim ra(4) As TA
Dim x
"""
lines = []
for f in "ilsdctfv":
    lines.append((f"store .{f}", f"r.{f} = x"))
    lines.append((f"load .{f}", f"x = r.{f}"))
    lines.append((f"store ra().{f}", f"ra(1).{f} = x"))
    lines.append((f"load ra().{f}", f"x = ra(1).{f}"))
lines += [("store .n.q", "r.n.q = 1"), ("load .n.q", "x = r.n.q"), ("store .a()", "r.a(2) = 1"), ("load .a()", "x = r.a(2)"),
          ("local Type", "Dim w As TA\nw.i = 1\nx = w.l"), ("local .t", "Dim w As TA\nw.t = \"a\"\nx = w.t"),
          ("local .v", "Dim w As TA\nw.v = 1\nx = w.v"), ("copy Type", "Dim w As TA\nw = r")]
