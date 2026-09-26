"""pytest setup: src/, tools/ and tests/ on sys.path; IDE tests skipped
without an IDE install (work/ide/VB.EXE) or an X display."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
for d in ("src", "tools", "tests"):
    if str(REPO / d) not in sys.path:
        sys.path.insert(0, str(REPO / d))

IDE_READY = (REPO / "work" / "ide" / "VB.EXE").exists() and bool(os.environ.get("DISPLAY"))


def pytest_addoption(parser):
    parser.addoption("--chunk", type=int, default=48, help="battery modules per compiled project")


def pytest_collection_modifyitems(config, items):
    if IDE_READY:
        return
    skip = pytest.mark.skip(reason="needs the VB3 IDE (work/ide/VB.EXE) and an X display (DISPLAY)")
    for item in items:
        if "ide" in item.keywords:
            item.add_marker(skip)
