"""
Decompiled-text snapshots, to check a refactor changes no output.

  python3 tests/snapshot.py OUT            decompile every battery/sample exe into OUT/<key>/
  python3 tests/snapshot.py --diff A B     compare two snapshots (exit 1 on any difference)
  python3 tests/snapshot.py --freeze DIR   copy the exes' directories to DIR, to snapshot
                                           with --work DIR while builds under work/ go on

The exes are the original builds under work/: battery projects
(work/battery/<battery>/<project>/orig/) and samples (work/rt/<sample>/orig/),
as left by battery.py and roundtrip.py. A decompile that raises writes
ERROR.txt instead, so a new exception shows up as a difference too.
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from vb3decompiler import Decompiler, write_project  # noqa: E402


def exes(work: Path) -> list[Path]:
    return sorted(work.glob("battery/*/B*/orig/*.exe")) + sorted(work.glob("rt/*/orig/*.exe"))


def snapshot(work: Path, out: Path, runtime: Path) -> int:
    found = exes(work)
    for exe in found:
        key = "_".join(exe.parts[-4:-2])
        try:
            d = Decompiler(exe, runtime, [runtime.parent])
            write_project(d, out / key, None, exe.stem)
        except Exception as e:
            (out / key).mkdir(parents=True, exist_ok=True)
            (out / key / "ERROR.txt").write_text(repr(e))
    return len(found)


def freeze(work: Path, to: Path) -> int:
    found = exes(work)
    for exe in found:
        shutil.copytree(exe.parent, to / exe.parent.relative_to(work), dirs_exist_ok=True)
    return len(found)


def diff(a: Path, b: Path) -> list[str]:
    out = []

    def walk(c: filecmp.dircmp, rel: Path) -> None:
        out.extend(f"only in {a.name}: {rel / n}" for n in c.left_only)
        out.extend(f"only in {b.name}: {rel / n}" for n in c.right_only)
        _, mismatch, errors = filecmp.cmpfiles(c.left, c.right, c.common_files, shallow=False)
        out.extend(f"differs: {rel / n}" for n in mismatch + errors)
        for n, sub in sorted(c.subdirs.items()):
            walk(sub, rel / n)

    walk(filecmp.dircmp(a, b), Path())
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path, nargs="?")
    ap.add_argument("--diff", type=Path, nargs=2, metavar=("A", "B"))
    ap.add_argument("--freeze", type=Path, metavar="DIR")
    ap.add_argument("--work", type=Path, default=Path("work"), help="where the exes are (default work/)")
    ap.add_argument("--runtime", type=Path, default=Path("work/ide/VBRUN300.DLL"))
    args = ap.parse_args()
    if args.diff:
        d = diff(*args.diff)
        print("\n".join(d[:50]) + (f"\n... {len(d)} differences" if len(d) > 50 else ""))
        print("identical" if not d else f"{len(d)} differences")
        sys.exit(1 if d else 0)
    if args.freeze:
        print(f"{freeze(args.work, args.freeze)} exe directories copied to {args.freeze}")
        return
    if not args.out:
        ap.error("OUT or --diff A B required")
    print(f"{snapshot(args.work, args.out, args.runtime)} exes decompiled into {args.out}")


if __name__ == "__main__":
    main()
