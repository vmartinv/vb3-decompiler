"""The VB3 sample projects through the real IDE (roundtrip.py): decompile
the original build, rebuild, same p-code and form resources.
`pytest -m ide -k samples`."""
from __future__ import annotations

import pytest

import roundtrip

IDE = roundtrip.REPO / "work" / "ide"


@pytest.mark.ide
@pytest.mark.parametrize("mak", roundtrip.sample_maks(), ids=lambda m: m.stem.lower())
def test_sample(mak):
    r = roundtrip.run(mak, IDE / "VBRUN300.DLL", [IDE], True, False)
    assert r is not None, f"{mak.stem}: build failed"
    assert r["count_ok"] and r["same"] == r["procs"], f"p-code: {r['same']}/{r['procs']} procedures identical"
    assert r["forms_same"] == r["forms"], f"forms: {r['forms_same']}/{r['forms']} identical"
