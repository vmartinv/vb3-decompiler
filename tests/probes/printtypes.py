# Print item ops per value type (`;` and end of line).
header = "Dim v, i%, l&, x!, d#, c@, s$\n"
lines = [(f"{n}", f"Debug.Print {n};\nDebug.Print {n}") for n in ("v", "i%", "l&", "x!", "d#", "c@", "s$")]
