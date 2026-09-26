# Input # targets: every type, scalar and array element, one and two items.
header = ("Dim v, s$, i%, l&, x!, d#, c@\nDim f As String * 8\n"
          "Dim zv(3), zs$(3), zi%(3), zl&(3), zx!(3), zd#(3), zc@(3)\n")
lines = [
 ("V", "Input #1, v"), ("S", "Input #1, s$"), ("I", "Input #1, i%"), ("L", "Input #1, l&"),
 ("X", "Input #1, x!"), ("D", "Input #1, d#"), ("C", "Input #1, c@"), ("F", "Input #1, f"),
 ("aV", "Input #1, zv(i%)"), ("aS", "Input #1, zs$(i%)"), ("aI", "Input #1, zi%(i%)"),
 ("aL", "Input #1, zl&(i%)"), ("aX", "Input #1, zx!(i%)"), ("aD", "Input #1, zd#(i%)"),
 ("aC", "Input #1, zc@(i%)"),
 ("S,V", "Input #1, s$, v"), ("aS,aS", "Input #1, zs$(1), zs$(2)"), ("V,aS", "Input #1, v, zs$(1)"),
]
