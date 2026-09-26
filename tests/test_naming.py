"""Procedure names (sort order) and readable variable names."""
from __future__ import annotations

import random
import re

import pytest

from naming import NamingMixin, sort_name

IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


@pytest.mark.parametrize("lo,hi,want", [
    ("", None, "aProc"),
    ("aProc", "Form_Load", "bProc"),
    ("", "A_Click", "a0Proc"),                  # digits sort before `_` and letters
    ("", "A1", "a0Proc"),
    ("GetInfo", "GetInfo2", "getinfo0Proc"),
    ("GetSubMenu", "GetSystemMenu", "getsuProc"),
])
def test_sort_name(lo, hi, want):
    assert sort_name(lo, hi, "Proc", set()) == want


def test_sort_name_kind_and_taken():
    assert sort_name("", None, "Func", set()) == "aFunc"
    assert sort_name("", None, "Proc", {"aproc"}) == "bProc"


def test_sort_name_impossible_gap():
    with pytest.raises(ValueError):
        sort_name("x", "x0", "Proc", set())  # no identifier sorts between these


def test_sort_name_always_between():
    rnd = random.Random(1)
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789_"
    for _ in range(2000):
        a, b = sorted(rnd.choice("abcdefgh") + "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 6)))
                      for _ in range(2))
        if a == b:
            continue
        try:
            name = sort_name(a, b, "Proc", set())
        except ValueError:
            # only where no identifier fits at all: b is a followed by zeros
            assert re.fullmatch(re.escape(a) + "0+", b)
            continue
        assert IDENT.fullmatch(name) and a < name.lower() < b and len(name) <= 40


class _Names(NamingMixin):
    def __init__(self):
        self.global_name = {}
        self.forms = []


def test_prettify():
    lines = [
        "Dim m1A As Integer",
        "Dim m1C As String",
        "",
        "Sub Form_Load ()",
        "    Dim v20 As Integer, v22 As String",
        "    v20 = 1: v22 = \"v20 m1A\"  ' v22 in a comment",
        "    m1A = v20",
        "    ReDim v24(3) As Integer",
        "End Sub",
    ]
    mod = dict(kind="frm", lines=list(lines), names={0x1A: "m1A", 0x1C: "m1C", 0x20: "v20", 0x22: "v22", 0x24: "v24"})
    n = _Names()
    n.prettify([mod])
    out = mod["lines"]
    assert out[0] == "Dim mInt1 As Integer" and out[1] == "Dim mStr1 As String"
    assert out[4] == "    Dim int1 As Integer, str1 As String"
    assert out[5] == "    int1 = 1: str1 = \"v20 m1A\"  ' v22 in a comment"  # strings and comments untouched
    assert out[6] == "    mInt1 = int1"
    assert out[7] == "    ReDim v24(3) As Integer"  # the `As` column is compiled: kept
    assert mod["names"][0x20] == "int1" and mod["names"][0x24] == "v24"
    assert n.generated("int1") and n.generated("v24")
