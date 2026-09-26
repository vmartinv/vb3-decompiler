# Conversions between every pair of types, every binary operator per type, unary operators.
T = {"%": "i", "&": "l", "!": "s", "#": "d", "@": "c", "$": "t", "V": "v"}
header = "Dim i%, j%, l&, m&, s!, r!, d#, e#, c@, b@, t$, u$, v, w\n"
twin = {"i": "j", "l": "m", "s": "r", "d": "e", "c": "b", "t": "u", "v": "w"}
NUM = "%&!#@"
lines = []
for a in T:
    for b in T:
        if a != b and not (("$" in (a, b)) and "V" not in (a, b)):
            lines.append((f"cvt {b}>{a}", f"{T[a]} = {T[b]}"))
for t in T:
    x, y = T[t], twin[T[t]]
    ops = ["+", "&", "=", "<>", "<", ">", "<=", ">=", "Like"] if t == "$" else \
        ["+", "-", "*", "/", "\\", "Mod", "^", "=", "<>", "<", ">", "<=", ">=", "And", "Or", "Xor", "Eqv", "Imp"] + (["&", "Like"] if t == "V" else [])
    for o in ops:
        lines.append((f"{o} {t}", f"v = {x} {o} {y}"))
    if t != "$":
        lines.append((f"neg {t}", f"v = -{x}"))
        lines.append((f"not {t}", f"v = Not {x}"))
