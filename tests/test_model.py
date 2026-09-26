"""model: variable-access handler names, statement columns, type names, Module."""
from __future__ import annotations

import pytest

from vb3decompiler.model import STMT_COLUMN, STMT_WIDE, TYPE_NAME, Module, stmt_column, var_access


@pytest.mark.parametrize("name, access", [
    ("LOAD.LOC.V", ("LOC", "V", False)),
    ("STORE.GLB.I", ("GLB", "I", False)),
    ("LOAD.GLB.L/T", ("GLB", "L/T", False)),
    ("ALOAD.MOD.D", ("MOD", "D", True)),
    ("ASTORE.GLB.T", ("GLB", "T", True)),
    ("ADDR_LOC", ("LOC", "", False)),
    ("ADDR_LOC.V", ("LOC", "V", False)),
    ("ADDR.MOD.F", ("MOD", "F", False)),  # a fixed-length String
    ("ADDR.MOD.V", ("MOD", "", False)),
    ("ADDR", ("GLB", "", False)),
    ("AADDR.GLB", ("GLB", "", True)),
    ("LOAD", None),
    ("ADD.V", None),
])
def test_var_access(name, access):
    assert var_access(name) == access


def test_stmt_column():
    assert stmt_column(None, 0x494B) == 0
    assert stmt_column(None, 0x48ED) == len(STMT_COLUMN) - 1
    assert stmt_column(None, STMT_WIDE, b"\x28\x00") == 40  # wide: the column is its operand
    assert stmt_column(None, STMT_WIDE) is None
    assert stmt_column(None, 0x1234) is None


def test_type_name():
    assert TYPE_NAME["I"] == "Integer" and TYPE_NAME["V"] == "Variant"
    assert TYPE_NAME["F12"] == "String * 12"
    with pytest.raises(KeyError):
        TYPE_NAME["Q"]
    assert TYPE_NAME.get("Q", "Variant") == "Variant"


def test_module_defaults_are_per_instance():
    a, b = Module("bas", 0x10), Module("frm", 0x20, form="Form1")
    a.items.append(("dim", 2, "As Integer"))
    a.names[2] = "x"
    assert b.items == [] and b.names == {}
    assert a != Module("bas", 0x10)  # identity: a module is found in lists by `is`/`in`
