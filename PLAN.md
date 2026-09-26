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
- **Procedure names**: general procedures still get placeholder names
  (`A01`, `Proc01`) because code layout and Function slots are sorted by
  name, so each name must fall between its neighbours'. Names from usage
  (e.g. what a Function returns, which events call a Sub) would read
  better, within the same sort constraint.
- **Variable names** are kind + type + counter (`int1`, `mStr2`,
  `gVar1`, `Type1`); names from usage (loop counters, what a variable is
  assigned from) are possible refinements.

## Verification
Before committing any change: full `tests/battery.py --chunk 48` (all
batteries) and full `tests/roundtrip.py` (all samples), looking for
CODE/FORM/CRASH/DECOFAIL and any procedure or form mismatch. Zero p-code
regressions is non-negotiable. For a pure refactor, also diff the
decompiled text of every battery/sample exe before and after.
