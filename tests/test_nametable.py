"""The model of a module's compile-time name table (nametable.py)."""
from __future__ import annotations

from nametable import FIRST, identifiers, name_offsets


def test_identifiers_first_appearance_case_insensitive():
    code = 'Dim Total As Integer\r\nSub Form_Load ()\r\n    total = 1: Debug.Print "x y"; Total\r\nEnd Sub'
    assert list(identifiers(code)) == ["total", "form_load"]  # keywords, strings, repeats don't count


def test_name_offsets():
    offs = name_offsets("Dim ab As Integer\r\nDim c As Integer")
    assert offs == {"ab": FIRST, "c": FIRST + len("ab") + 4}
