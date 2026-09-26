# Literals, array arguments, ReDim As, LBound with a dimension.
header = "Dim x, a(3, 4), n%\nDim d() As Integer\n"
lines = [
 ("1.5!", "x = 1.5!"), ("2.25!", "x = 2.25!"), ("1@", "x = 1@"), ("12.3456@", "x = 12.3456@"), ("-7@", "x = -7@"),
 ("&O17", "x = &O17"), ("&O177777", "x = &O177777"), ("&O17&", "x = &O17&"), ("&O7777777&", "x = &O7777777&"),
 ("1.5 plain", "x = 1.5"), ("1.5#", "x = 1.5#"), ("100000", "x = 100000"), ("&H10&", "x = &H10&"),
 ("LBound(a, 1)", "x = LBound(a, 1)"), ("LBound(a, 2)", "x = LBound(a, 2)"), ("UBound(a, 2)", "x = UBound(a, 2)"),
 ("ReDim As Integer", "ReDim d(n) As Integer"),
 ("arg arr()", "Q2 d()"),
]
footer = "Sub Q2 (z() As Integer)\nEnd Sub\n"
