# Operators, precedence, parentheses, literals, comparisons, typed operands.
def S(body):
    return f"Sub Form_Load ()\n{body}\nEnd Sub\n"

E = [
 "a + b", "a - b", "a * b", "a / b", "a \\ b", "a Mod b", "a ^ b", "-a", "a & b", 'a + "s"',
 "a = b", "a <> b", "a < b", "a > b", "a <= b", "a >= b", 'a Like "x*"', "Me Is Screen.ActiveForm",
 "a And b", "a Or b", "Not a", "a Xor b", "a Eqv b", "a Imp b",
 "a + b * c", "(a + b) * c", "a * (b + c)", "a - (b - c)", "a / (b * c)", "-(a + b)", "-a ^ 2", "(-a) ^ 2",
 "a = 1 And b = 2 Or c = 3", "a = 1 And (b = 2 Or c = 3)", "Not (a And b)", "Not a = b", "a Mod b * c", "a \\ b \\ c",
 "a ^ b ^ c", "a ^ (b ^ c)", "a & b + c", "(a & b) + c", "a < b = c",
 "1", "0", "-1", "32767", "-32768", "32768", "70000", "2147483647", "-2147483648", "1.5", ".5", "1.5E+10", "1E-5",
 "1.5#", "1.5!", "1@", "1%", "1&", "&H10", "&HFFFF", "&HFFFF&", "&H7FFFFFFF", "&O17", "&O177777", '""', '"a""b"',
 "a% + b%", "a& + b%", "a! + b%", "a# + b!", "a@ * b%", "a$ + b$", "a% / b%", "a% \\ b&", "a% = b!", "CInt(a) + b&",
 "a% + 1", "a& + 1", "a! + 1", "a# + 1.5", "a@ + 1", 'a$ + "x"', "a% + 70000", "a! + 1.5#",
]
cases = [dict(name=e, code=S(f"    x = {e}")) for e in E]
cases += [
    dict(name="typed assignment conversions", code=S("    a% = b!\n    c& = d#\n    e! = f@\n    g$ = h\n    i = j%")),
    dict(name="string compare ops", code=S('    x = (a$ = b$)\n    y = (a$ < b$)\n    z = a$ Like "?b*"')),
    dict(name="deep nesting", code=S("    x = ((((a + 1) * 2) - 3) / 4) ^ ((b Mod 5) + 1)")),
    dict(name="long expression", code=S("    x = a + b + c + d + e + f + g + h + i + j + k + l + m + n + o + p")),
]
