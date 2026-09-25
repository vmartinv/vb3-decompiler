# Function return types: call and return-value store per type.
T = {"I": "Integer", "L": "Long", "S": "Single", "D": "Double", "C": "Currency", "T": "String", "V": "Variant"}
V = {"I": "1", "L": "1", "S": "1", "D": "1", "C": "1", "T": '"a"', "V": "1"}
header = "Dim a, i%, l&, x!, d#, c@, s$\n" + "".join(
    f"\nFunction Q{k} () As {t}\n    Q{k} = {V[k]}\nEnd Function\n" for k, t in T.items()) + \
    "\nFunction G% ()\n    G% = 1\nEnd Function\n\nFunction H$ (p)\n    H$ = p\nEnd Function\n"
lines = [(f"call {k}", f"a = Q{k}()") for k in T] + [("call G%", "a = G%()"), ("call H$", "a = H$(1)"),
         ("call I into i%", "i% = QI()")]
