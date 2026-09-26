# Plan: complete the VB3 decompiler

## Goal
The decompiled source recompiles to the **same p-code and form
resources** as the original executable. Byte-identical executables are
not a goal: they depend on the original identifier lengths (name-table
sizes, hash orders, compile-time pool offsets), which the exe doesn't
store; fitting them made the code large and the names ugly.

## Status
Every VB3 language feature has a battery; all 969 cases (20 batteries)
and all 22 samples (483/483 procedures, all forms) round-trip to
identical p-code and form resources. The decompiler lives in `src/`,
split by pass (README.md).

## Remaining items
None required. Optional polish: variable names from usage (loop counters,
what a variable is assigned from) instead of kind + type + counter
(`int1`, `mStr2`); general procedures are named by sort key + kind
(`aProc`, `getsuFunc`, `naming.sort_name`), since their names only need to
keep the code layout's and Function slots' sort order.

## Verification
Before committing any change: `pytest` (unit tests), then the full
regression, `DISPLAY=:99 pytest -m ide` (equivalently `tests/battery.py
--chunk 48` and `tests/roundtrip.py`), looking for
CODE/FORM/CRASH/DECOFAIL and any procedure or form mismatch. Zero p-code
regressions is non-negotiable. For a pure refactor, also diff the
decompiled text of every battery/sample exe before and after.
