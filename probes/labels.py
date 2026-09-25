# Label/line-number layouts and Let, one Sub each (labels are module-wide).
header = "Dim a, b, s$\n"
lines = [
 ("num same line", "GoTo 100\n100 b = 2"),
 ("num own line", "GoTo 200\n200\n    b = 2"),
 ("num wide", "GoTo 300\n300     b = 2"),
 ("name same line", "GoTo L1\nL1: b = 2"),
 ("name own line", "GoTo L2\nL2:\n    b = 2"),
 ("Let", "Let a = 1"), ("no Let", "a = 1"),
]
