"""localvars: the solver for runs of unused locals."""
from __future__ import annotations

from vb3decompiler.localvars import numerics_fit, run_options, run_solutions


def test_numerics_fit():
    assert numerics_fit(0, 0) and not numerics_fit(0, 2)
    assert numerics_fit(1, 8) and numerics_fit(2, 6) and not numerics_fit(1, 6)
    assert not numerics_fit(2, 18)


def test_run_options_most_variants_first():
    opts = list(run_options([0x20, 2, None, None]))  # a run of 2 slots
    assert opts[0] == (1, 0, 0, 0)  # one Variant (2 slots)
    assert (0, 2, 0, 0) in opts and (0, 0, 2, 16) in opts and (0, 0, 2, 4) in opts


def test_run_solutions_totals():
    runs = [[0x20, 2, None, None]]
    # 16 frame bytes, 1 numbered: a Variant
    assert run_solutions(runs, {}, {}, 16, 1) == [[(1, 0, 0, 0)]]
    # no frame, 2 numbered: two Strings
    assert run_solutions(runs, {}, {}, 0, 2) == [[(0, 2, 0, 0)]]
    # 6 frame bytes, none numbered: two numerics (4 + 2)
    assert run_solutions(runs, {}, {}, 6, 0) == [[(0, 0, 2, 6)]]
    assert run_solutions(runs, {}, {}, 20, 0) == []
