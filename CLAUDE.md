# vb3-decompiler

Reusable VB3 reverse-engineering tools + findings. Companion to
`~/qrace` (Quibble Race port), not yet published.

## Rules

- `.md` files hold **current findings and next steps only**, kept
  succinct. When something is superseded, rewrite it in place. Don't keep
  retractions, dead ends or history (git has that).
- Commit after each validated milestone.
- Never commit VB3, `VBRUN300.DLL`, analyzed executables or anything
  extracted from them (see `.gitignore`). Findings about them (offsets,
  opcode meanings) are fine.
- Tokens are expensive, wall-clock time is not. Prefer controlled
  experiments (batch many cases into one generated project, compile, read
  the result) over long manual analysis; runs may take as long as needed.
  Keep tool output small (filter/summarize before printing).
- Validate with **feature batteries** (`batteries/<feature>.py`, run by
  `tools/battery.py`): one battery per language feature, sweeping its
  whole range, round-tripped case by case. The VB3 samples are only a
  sanity check (`tools/roundtrip.py`): they cover a narrow slice of the
  language. `battery.py --check` validates a new battery's source first.
- Name unknown opcodes with **probes** (`probes/<name>.py`, run by
  `tools/opprobe.py`): one statement per line, unknown ops reported per
  statement; `--sem` proposes builtin entries.
- Builds use `VB.EXE /MAKE` (~2 s, no GUI clicks). Its output differs
  from a GUI "Make EXE" build in one word of RT_RCDATA 1, so compare
  /MAKE builds only with /MAKE builds.
- Whole-exe identity work (Phase 3, see PLAN.md): before touching
  `decompile.py`, isolate the field in question by hand-writing two
  minimal `.frm`s that differ only in the one thing being tested and
  compiling them directly with `tools/vb3ide/compile_project.py`
  (bypassing the decompiler entirely) — this gives ground truth about
  what a field actually encodes before any decompiler logic is written
  against it.
- A fix must be validated against the *whole* battery (`battery.py --exe`
  with no `-k`) and the full sample roundtrip, not just the isolated
  case: several modules share one project's global image/name pool, and
  a change that's correct alone can still cascade into an unrelated
  module's p-code once bundled. Zero p-code regressions (CODE/CRASH/
  DECOFAIL) is non-negotiable — revert rather than trade a p-code
  regression for an exe-byte win.
- An exact arithmetic constraint derived from one exe field (e.g. a
  module's own decl+50 line count) is only trustworthy when it's the
  *sole* discrepancy source for that module. This corpus has several
  independently-guessed, entangled sources of exe mismatch already
  (name lengths, DefType's own line-counting); a derived "this must be
  the missing piece" value can be misattributed when another source is
  also active in the same module, and converting on a wrong attribution
  can itself introduce a p-code regression, not just a wrong guess.

## Key files

- `OPCODES.md`: p-code format; `tools/pcode_disasm.py` implements it.
- `RESOURCE_FORMAT.md`: NE resources / forms.

## Local data (`work/`, gitignored)

- `root/vb/samples/<name>/`: VB3 sample projects (`.mak`/`.frm`/`.bas`
  source + compiled `.exe`, e.g. `calc/calc.exe`, `mdi/mdinote.exe`).
  Sanity check only.
- `battery/<battery>/<project>/{orig,deco}`: battery builds;
  `probe/<probe>/`: probe builds.
- `tests/<name>/`: generated test projects (source + exe).
- `ide/`: `VB.EXE`, `VBRUN300.DLL`, VBX/DLLs.
- `.wineprefix/` (C: = `vdrive_mnt/`, loop-mounted `vdrive.img`),
  `proj/`, `sweep/`, `sweep_struct/`: used by `tools/vb3ide/` (defaults
  need no env vars; X display `:99`).
- `corpus/`: aligned sample corpus.

```sh
python3 tools/validate.py work/root/vb/samples \
    --runtime work/ide/VBRUN300.DLL --vbx-dir work/ide -v
python3 tools/lift.py score work/corpus --runtime work/ide/VBRUN300.DLL
```
