# Plan: code quality

Temporary: deleted when done (the decompiler itself is complete, see
CLAUDE.md "Status").

## Context
The decompiler is complete (all 969 battery cases + 22 samples round-trip to
identical p-code and forms) and packaged as `vb3decompiler`
(`src/vb3decompiler/`). What's left is maintainability. Measured problems:
- 7 functions over 100 lines, 21 over 50: `lift()` 591 lines (141
  if/elif branches in one loop over closure state), `local_dims` 294,
  `declarations` 233, `analyze_module` 187, `resolve_symbols` 115,
  `solve_runs` 112, `prettify` 101.
- Module state is an untyped dict: 191 `m["..."]` accesses over ~25 keys
  (`image`, `vars`, `infos`, `items`, `names`, `first_owned`, `nv_pick`, ...),
  no record of which pass fills which key.
- 15 module-level mutable dicts in `runtime.py` (`KINDS`, `CLASSES`,
  `SEG_FORM`, `RECORD_FORM`, `OBJVAR_TYPES`, `SEG_IMAGE`, `MEPROPS`,
  `FORM_CLASS`; `EVENT_TYPES`, `PROP_TYPES`, `MASTER_*`, `MODEL_*`,
  `PROP_STD`) cleared by `reset_state()`: not re-entrant, a problem for
  an importable package.
- Raw record offsets everywhere (`rec + 46`, `r + 24`, `record + 50`, ...;
  ~36 uses over 9 modules).
- No linter, no CI; unit tests cover only naming and the name table.

Rule for every step: **no behaviour change**. The decompiled text of every
battery/sample exe stays byte-identical, and the full IDE suite stays green.

## Step 1: tooling (lint, CI, snapshot tool)
- `ruff` config in `pyproject.toml` (`line-length = 120`; rules E, F, W, I,
  B, UP); fix what it reports in `src/`, `tests/`, `tools/` (unused names,
  imports, shadowing). Add `ruff` to the `test` extra.
- `.github/workflows/ci.yml`: Python 3.12, `pip install -e '.[test]'`,
  `ruff check .`, `pytest` (unit tests only; IDE tests auto-skip without
  `work/ide`).
- `tests/snapshot.py`: the scratch script used for the refactors so far,
  made a tool. It decompiles every battery/sample exe under `work/` into a
  directory, and `--diff A B` compares two snapshots. Every later step uses it.

## Step 2: named record offsets
- New `src/vb3decompiler/records.py`: constants per record, each with a
  one-line meaning and its OPCODES.md section:
  - procedure record: +0 frame, +4 pool offset, +10 numbered locals, +12
    kind, +13 return type, +14 flags, +15 argument words, +18 variable
    table, +24/+36 code start/end, +38 relocation, +50 line count;
  - Declare record: +24 parameter table, +40 DLL name, +46 entry name;
  - declarations record: +0 path, +12 item count, +18 flags, +20
    Option Compare, +30 name-table size, +44 DefType, +46 Types start,
    +50 line count; project record +30.
- Replace the raw offsets (`word(self.table, rec + 46)` becomes
  `word(self.table, rec + DECL_TYPES_START)`) in `declarations.py`,
  `localvars.py`, `layout.py`, `emit.py`, `analyze.py`, `naming.py`,
  `ne.py`, `symbols.py`, `runtime.py`.

## Step 3: per-exe state instead of module globals
- Runtime-model tables (`EVENT_TYPES`, `PROP_TYPES`, `MASTER_PROP_TYPES`,
  `MASTER_EVENT_TYPES`, `MODEL_FLAGS`, `MODEL_VERSION`, `PROP_STD`) become
  attributes of `Runtime` (runtime.py), filled where they're filled now.
- Per-exe tables (`KINDS`, `CLASSES`, `SEG_FORM`, `RECORD_FORM`,
  `OBJVAR_TYPES`, `SEG_IMAGE`, `MEPROPS`, `FORM_CLASS`) become attributes of
  `Symbols` (symbols.py), which the `Decompiler` owns; `reset_state()` goes.
- Callers switch to `self.rt.X` / `self.sym.X` (decompiler mixins, forms.py,
  symbols.py), and the tools that read them through `pcode_disasm`
  (validate.py, pcode_disasm.py, formdump.py) too.

## Step 4: a `Module` dataclass
- In `model.py`: `@dataclass class Module` with the ~25 keys as typed
  fields, grouped and commented by the pass that sets them (layout: kind,
  image, form, seg, start, explicit, defint, funcs, decl_start; analyze:
  vars, infos, refs, udt, first_owned, call_slots; declarations: items,
  types, static_arrays, decl_offs; naming: names; emit: lines, nv_pick,
  nv_delta, tail_owner, ...).
- `module_list()` (layout.py) builds them; `m["x"]` / `m.get("x")` become
  `m.x` everywhere (mechanical; defaults replace `.get`).

## Step 5: unit tests for the pure parts
- `tests/test_lift.py`: `lift()` on hand-built statements (a reverse
  `NAMES` lookup gives the handler for a name): assignment, operator
  precedence and parentheses, calls/Call, Print separators, If/ElseIf,
  property access. This is the safety net for splitting `lift()`.
- `tests/test_dataimage.py`: `const_literal`, `GlobalImage` on small
  synthetic images; `tests/test_model.py`: `var_access`, `stmt_column`,
  `TYPE_NAME` for `String * n`.

## Step 6: split the long functions
One function per commit, each checked by the snapshot diff before the next:
- `lift()` → a `Lifter` class: the stack/output state as attributes, one
  method per handler family (loads/stores, operators, calls and methods,
  Print/graphics, control flow, file I/O), a dispatch table instead of the
  if/elif chain.
- `local_dims`, `declarations`, `analyze_module`, `resolve_symbols`,
  `solve_runs`, `prettify`, `name_module`: cut at their existing
  comment-marked phases into named helper methods.

## Step 7: wrap up
- CLAUDE.md: ruff/CI/snapshot tool in the rules, `records.py` and `Module`
  in the layout notes; README tree updated; delete PLAN.md. Push.

## Verification (every step)
- `ruff check .` and `pytest` (unit) pass.
- `python3 tests/snapshot.py work/snap-after` then `--diff` against the
  snapshot taken before the step: **no differences** (all ~190
  battery/sample projects).
- `DISPLAY=:99 pytest -m ide` (run detached under the memory cap): 991/991.
- Steps 3 and 4 also: tools still run (`tools/pcode_disasm.py`,
  `tools/validate.py`, `tools/formdump.py` on a sample).
- Commit per step (Step 6: per function), push at the end.
