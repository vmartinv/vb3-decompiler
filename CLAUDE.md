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
- Local VB3 IDE + test builds: `~/qrace/vb3_install/` (`sweep/`,
  `sweep_struct/`), driven by `tools/vb3ide/`.
