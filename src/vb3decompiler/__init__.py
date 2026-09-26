"""
vb3decompiler: Visual Basic 3.0 executables back to project source
(.mak/.frm/.bas/.frx) that recompiles to the same p-code and form
resources.

    from vb3decompiler import Decompiler, write_project
    d = Decompiler(Path("app.exe"), Path("VBRUN300.DLL"), [Path("vbx_dir")])
    write_project(d, Path("out"), None, "app")

The modules, by pass: decompiler (Decompiler, run(), write_project), layout,
analyze, declarations, naming, localvars, emit; lift (statements), forms
(.frm text), dataimage, nametable, model; ne / runtime / symbols (the exe,
the VBRUN300 interpreter model, names); opcodes.
"""
from .decompiler import Decompiler, write_project

__all__ = ["Decompiler", "write_project"]
__version__ = "0.1.0"
