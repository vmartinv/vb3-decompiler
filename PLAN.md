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
1. **Form layouts with VBX controls, decoded (no `--layout-from`)**: the
   `vbx` battery's projects raise `IndexError` in `forms.py` when their
   layouts are decoded instead of copied (the battery copies them, so it
   passes). A foreign exe using VBX controls would hit this.
2. **Readable names**: generated names are slot-based (`v1C`, `m1A`,
   `G6`); procedure names are constrained by sort order (`A01`). Better
   names (from usage: loop counters, control events, types) are free
   to choose as long as the sort order and the object-local free order
   (`naming.py`, `fit_frees`) are kept.

## Verification
Before committing any change: full `tests/battery.py --chunk 48` (all
batteries) and full `tests/roundtrip.py` (all samples), looking for
CODE/FORM/CRASH/DECOFAIL and any procedure or form mismatch. Zero p-code
regressions is non-negotiable. For a pure refactor, also diff the
decompiled text of every battery/sample exe before and after.
