# Every builtin function of the builtins battery, as `x = <call>`.
b = {}
exec((REPO / "batteries" / "builtins.py").read_text(), b)  # noqa: F821 (REPO given by opprobe)
header = "Dim a, v, x\nDim arr(3)\n"
lines = [(e, f"x = {e}") for e in b["E"]]
