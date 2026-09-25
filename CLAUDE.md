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
- VB.EXE is a deterministic compiler, so every original exe has *some*
  exact-match source; a remaining mismatch means the right source-level
  detail hasn't been found yet, not that it's unrecoverable. But this
  corpus has several independent, still-unresolved sources of mismatch
  active at once (name lengths, per-field line-counting quirks, ...), so
  a value read off or derived from a single field can be misattributed
  when more than one source is active in the same module — check it
  against another independent field, or another way, before applying it
  broadly; a wrong attribution can cost a p-code match, not just a byte.
- For a *sample* with known original source (unlike a foreign target
  exe), localize a mismatch by splicing hybrid `.frm`/`.bas` files —
  original text with one aspect swapped in from the reconstruction
  (comments, identifier names, explicit types, control/property order,
  half the procedures, ...) — recompiling each, and diffing against the
  true original with `exediff.py`. This narrows *which kind* of
  difference matters fast. Get the true slot/record <-> name
  correspondence from `tools/align_source.py` (matches by first-mention
  order + p-code), not by guessing from file position: procedure record
  order can legitimately differ from a renamed reconstruction's text
  order even though each procedure's own p-code matches. This technique
  has a real limit, though: once it shows the mismatch depends on exact
  string content (a hash-bucket-style structure, not a length sum),
  further bisection just keeps reconfirming "it's naming" without
  producing a fix, since a foreign target never gives you true names to
  substitute — stop there and record it as the same open problem as the
  name-length items, not chase the single "culprit" name.

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
