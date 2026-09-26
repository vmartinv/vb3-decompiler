# Input # / Line Input # into Global arrays (a .BAS module), Variant/Integer index.
bas = True
header = ("Global gv, gi%\n"
          "Global zv(3), zs$(3), zi%(3), zl&(3), zx!(3), zd#(3), zc@(3)\n"
          "Global zs2(3) As String, zv2(3) As Variant\n")
lines = [
 ("aV", "Input #1, zv(gv)"), ("aS", "Input #1, zs$(gv)"), ("aS%", "Input #1, zs$(gi%)"),
 ("aI", "Input #1, zi%(gv)"), ("aL", "Input #1, zl&(gv)"), ("aX", "Input #1, zx!(gv)"),
 ("aD", "Input #1, zd#(gv)"), ("aC", "Input #1, zc@(gv)"), ("aS2", "Input #1, zs2(gv)"),
 ("aV2", "Input #1, zv2(gv)"), ("S", "Input #1, gv"), ("aS,aS", "Input #1, zs2(1), zs2(2)"),
 ("LI aV", "Line Input #1, zv(gv)"), ("LI V", "Line Input #1, gv"), ("LI aS", "Line Input #1, zs$(gv)"),
 ("LI aS2", "Line Input #1, zs2(gv)"),
]
