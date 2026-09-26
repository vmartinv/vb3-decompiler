# ReDim ... As: the operand's second word (text column of `As`?).
header = "Dim n As Integer\nDim a() As Integer, abcdef() As Integer, b() As Long\n"
lines = [
 ("a(n)", "ReDim a(n) As Integer"), ("abcdef(n)", "ReDim abcdef(n) As Integer"),
 ("a(n + 1)", "ReDim a(n + 1) As Integer"), ("indent", "    ReDim a(n) As Integer"),
 ("two", "ReDim a(n) As Integer, b(3) As Long"), ("To", "ReDim a(1 To n) As Integer"),
]
