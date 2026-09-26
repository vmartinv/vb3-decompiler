"""The feature batteries (tests/batteries/*.py) through the real IDE: each
battery runs once (cases packed into projects, see battery.py) and each
case is reported as its own test. `pytest -m ide [-k <battery or case>]`."""
from __future__ import annotations

import re

import pytest

import battery


def _params():
    out = []
    for name in battery.battery_names():
        try:
            cases = battery.load(name)
        except Exception as e:  # e.g. a battery that reads VBRUN300.DLL without an IDE install
            out.append(pytest.param(name, -1, marks=pytest.mark.skip(reason=f"{name}: {e}"), id=name))
            continue
        for i, c in enumerate(cases):
            out.append(pytest.param(name, i, id=f"{name}-{i:03d}-{re.sub(r'[^A-Za-z0-9]+', '_', c['name'])}"))
    return out


@pytest.fixture(scope="session")
def results(request):
    """Runs a battery (only its selected cases) on first use; Runner by battery."""
    cache = {}

    def get(name: str):
        if name not in cache:
            sel = sorted(it.callspec.params["i"] for it in request.session.items
                         if getattr(it, "callspec", None) and it.callspec.params.get("name") == name)
            cache[name] = battery.run_battery(name, battery.load(name), sel, request.config.getoption("--chunk"))
        return cache[name]
    return get


@pytest.mark.ide
@pytest.mark.parametrize("name,i", _params())
def test_case(results, name, i):
    r = results(name)
    got = r.result.get(i)
    assert got == "ok", f"{got}: {r.detail.get(i, '')}"
