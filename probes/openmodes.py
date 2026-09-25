# Open statement mode/access/lock combinations, one Sub each.
header = ""
lines = [(m, f'Open "c:\\t" For {m} As #1') for m in [
    "Binary", "Binary Access Read", "Binary Access Write", "Binary Access Read Write",
    "Binary Shared", "Binary Lock Read", "Binary Lock Write", "Binary Lock Read Write",
    "Binary Access Read Lock Write", "Binary Access Read Write Lock Read Write",
    "Random Access Read Shared", "Input Lock Write", "Output Lock Read",
]] + [("Len Lock", 'Open "c:\\t" For Random Access Read Lock Write As #1 Len = 8')]
