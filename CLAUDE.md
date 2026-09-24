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

## Key files

- `OPCODES.md`: p-code format; `tools/pcode_disasm.py` implements it.
- `RESOURCE_FORMAT.md`: NE resources / forms.

## Local data (not committed)

All under `~/qrace/vb3_install/`:

- `root/vb/samples/<name>/`: VB3 sample projects (`.mak`/`.frm`/`.bas`
  source + compiled `.exe`, e.g. `calc/calc.exe`, `mdi/mdinote.exe`).
  Ground truth for every rule.
- `tests/<name>/`: generated test projects (source + exe).
- `ide/`: `VB.EXE` + VBX/DLLs (pass as `--vbx-dir`).
- `sweep/`, `sweep_struct/`: snippet builds from `tools/vb3ide/`.
- Runtime: `~/qrace/original/VBRUN300.DLL`.
- Aligned corpus: `work/corpus/` (this repo, gitignored).
- Wine: `WINEPREFIX=/mnt/extra_ssd/qrace-wine/.winevb3`, `DISPLAY=:99`,
  `IDE_DIR=~/qrace/vb3_install/ide`.

```sh
python3 tools/validate.py ~/qrace/vb3_install/root/vb/samples \
    --runtime ~/qrace/original/VBRUN300.DLL --vbx-dir ~/qrace/vb3_install/ide -v
python3 tools/lift.py score work/corpus --runtime ~/qrace/original/VBRUN300.DLL
```
