"""
Per-module analysis of the p-code: procedures, the variables each slot
holds (scope, type votes, arrays, UDTs), control references, calls.
"""
from __future__ import annotations

import struct

from .dataimage import word
from .model import (
    OBJ_KINDS,
    PRINT_TYPE,
    RET_TYPE,
    SUFFIX,
    TYPE_OF_SUFFIX,
    ProcInfo,
    Var,
    lt_hint,
    plain_handler,
    var_access,
)
from .opcodes import NAMES
from .records import PROC_ARG_WORDS, PROC_KIND, PROC_RET_TYPE
from .runtime import decode
from .symbols import CLASS_BY_KIND, is_objarr


class AnalyzeMixin:
    def analyze_module(self, m: dict) -> None:
        seg, base = m["seg"], m["image"]
        procs = [p for p in self.procs if p.segment == seg] if seg else []  # layout order
        infos = []
        for p in procs:
            insns, err = decode(self.rt, self.segs[seg - 1].data, p)
            notes = self.sym.annotate(seg, insns)
            info = ProcInfo(p, insns, notes)
            info.function = self.table[p.record + PROC_KIND] == 2
            info.ret = RET_TYPE.get(self.table[p.record + PROC_RET_TYPE], "V")
            info.argwords = self.table[p.record + PROC_ARG_WORDS]
            infos.append(info)

        # variables: scope/type per slot; globals referenced through this module's slots
        vars_: dict[int, Var] = {}
        udt: dict[int, int] = {}  # UDT variable slot -> Type offset
        for k, info in enumerate(infos):
            last_udt = None
            for j, i in enumerate(info.insns):
                n, sfx = plain_handler(self.rt, i.op)
                if (n is None or n.endswith(".X")) and is_objarr(self.rt, i):
                    n = "ALOAD.MOD.V"  # object array element (typed separately)
                    x = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                    if word(self.image, base + x) >> 8 == 0x80:  # `0x80NN, global offset`: a global As New array
                        vars_.setdefault(x, Var(x, "MOD")).glob = word(self.image, base + x + 2)
                if n in ("LOAD.UDT", "LOAD.UDT_LOC", "LOAD.UDT_GLB", "AUDT", "STORE.UDT") and i.operand:
                    last_udt = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                    scope = "LOC" if n.endswith("LOC") or (n == "STORE.UDT" and self.value(base, last_udt) < 0) \
                        else "GLB" if n.endswith("GLB") else "MOD"
                    v = vars_.setdefault(last_udt, Var(last_udt, scope))
                    v.array |= n == "AUDT"
                    if k not in v.procs:
                        v.procs.append(k)
                    nxt_n = NAMES.get(info.insns[j + 1].op, "") if j + 1 < len(info.insns) else ""
                    if n == "AUDT" and not nxt_n.startswith("FIELD_"):  # an element's address (ByRef argument)
                        t = {2: "I", 4: "L", 8: "D"}.get(word(self.image, base + last_udt + 16))  # descriptor + 14
                        if t and scope == "MOD":
                            v.votes[t] = v.votes.get(t, 0) + 1
                        last_udt = None
                        continue
                    v.udt = True
                    continue
                if n and n.startswith(("FIELD_", )) and last_udt is not None and i.operand:
                    td = self.gimg.field_type.get(struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0])
                    if td:
                        udt[last_udt] = td.g
                        if vars_[last_udt].array or vars_[last_udt].scope == "GLB":
                            vars_[last_udt].udt_type = td.g
                    last_udt = None
                if n == "ARRAY_REF" and i.operand:  # a whole array (LBound, Erase, argument `a()`)
                    slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                    if slot not in vars_ and (self.is_global_slot(base, slot) or slot in {x for x, _ in m["funcs"]}):
                        continue  # a global array (through this module's slot) or a function slot
                    bp = self.value(base, slot)  # a descriptor (module/Static) or a BP offset
                    where = "LOC" if -0x1000 < bp < 0 else "REF" if 0 < bp < 0x100 else "MOD"
                    v = vars_.setdefault(slot, Var(slot, where))
                    v.array = True
                    if k not in v.procs:
                        v.procs.append(k)
                    continue
                if n == "OBJVAR" and i.operand:  # object variable: record `kind, BP offset / 0`
                    slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                    bp = self.value(base, slot)
                    if bp != 0:  # parameter or local (module-level ones are declarations)
                        v = vars_.setdefault(slot, Var(slot, "LOC"))
                        kind = word(self.image, base + slot)  # record: kind, BP offset
                        v.obj = self.sym.objvar_types.get(seg, {}).get(slot) or OBJ_KINDS.get(kind) \
                            or CLASS_BY_KIND.get(kind) or "Control"
                        if k not in v.procs:
                            v.procs.append(k)
                    continue
                acc = var_access(n or "")
                if not acc or not i.operand:
                    continue
                scope, t, arr = acc
                slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                if scope == "X":  # the slot holds 0 (module) or a BP offset < 0 (local); else unsure: skip
                    bp = self.value(base, slot)
                    if bp > 0:
                        continue
                    scope = "MOD" if bp == 0 else "LOC"
                v = vars_.setdefault(slot, Var(slot, scope))
                if scope == "REF":
                    v.scope = "REF"
                if n.startswith(("STORE", "ASTORE", "ADDR", "AADDR")):
                    v.stored = True
                if n.startswith("ADDR") and not v.obj and j + 1 < len(info.insns) \
                        and NAMES.get(info.insns[j + 1].op) == "SET_OBJ":  # `Set x = ...`: an object variable
                    kind = word(self.image, base + slot)  # its record: kind, BP offset
                    v.obj = self.sym.objvar_types.get(seg, {}).get(slot) or OBJ_KINDS.get(kind) \
                        or CLASS_BY_KIND.get(kind) or "Object"
                v.array |= arr
                if arr and j + 1 < len(info.insns):  # array of a Type: `a(i).field`
                    nn = NAMES.get(info.insns[j + 1].op, "")
                    if nn.startswith("FIELD_") and info.insns[j + 1].operand:
                        td = self.gimg.field_type.get(struct.unpack_from("<H", info.insns[j + 1].operand)[0])
                        if td:
                            v.udt_type = td.g
                if k not in v.procs:
                    v.procs.append(k)
                if t == "F":  # fixed-length String: its length is in the slot before
                    t = f"F{self.value(base, slot - 2, False)}"
                    v.fixed = True
                elif sfx:
                    t = TYPE_OF_SUFFIX[sfx]
                elif t == "L/T":
                    nxt_op = info.insns[j + 1].op if j + 1 < len(info.insns) else None
                    t = PRINT_TYPE.get(nxt_op) or lt_hint(NAMES.get(nxt_op, ""))
                if t in SUFFIX or t.startswith("F"):
                    v.votes[t] = v.votes.get(t, 0) + 1

        # object variables' classes (from their records) name their properties: annotate again
        typed = {x: v.obj for x, v in vars_.items() if v.obj}
        if seg and any(self.sym.objvar_types.get(seg, {}).get(x) != c for x, c in typed.items()):
            known = self.sym.objvar_types.setdefault(seg, {})
            for x, c in typed.items():
                known.setdefault(x, c)
            for info in infos:
                info.notes = self.sym.annotate(seg, info.insns)

        # control/form slots referenced by each procedure
        refs: dict[int, tuple[int, str]] = {}  # slot -> (first proc, name)
        for k, info in enumerate(infos):
            for i, note in zip(info.insns, info.notes):
                n = NAMES.get(i.op)
                if n in ("CONTROL", "CTLARRAY", "CTLARRAY_GET", "CTLARRAY_SET", "FORM") and i.operand and note:
                    slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                    refs.setdefault(slot, (k, note))
                elif n in ("PGET_ME", "PSET_ME") and i.operand and note:  # `Readout`, `Left`
                    slot = struct.unpack_from("<H", i.operand)[0]
                    refs.setdefault(slot, (k, note.rpartition(".")[2]))

        owned = {s for s, v in vars_.items() if v.scope in ("LOC", "REF")}
        # other slots procedures allocate at first use: calls to functions of other
        # modules, object variables, and (in forms) references to globals
        func_slots = {x for x, _ in m["funcs"]}
        call_slots: dict[int, int] = {}  # slot -> first procedure (calls into other modules)
        for k, info in enumerate(infos):
            for i in info.insns:
                n = plain_handler(self.rt, i.op)[0] or ""  # suffixed accesses resolve to their plain handler
                if n == "CALL_FN" and len(i.operand) >= 4:
                    x = struct.unpack_from("<H", i.operand, 2)[0]
                    if x not in func_slots:
                        owned.add(x)
                        call_slots.setdefault(x, k)
                elif m["kind"] == "frm" and i.operand and (
                        n in ("OBJVAR", "FORM", "CONTROL", "CTLARRAY", "CTLARRAY_GET", "CTLARRAY_SET")
                        or ((not n or n.endswith(".X")) and is_objarr(self.rt, i))):
                    # records start at the operand; a form declares no global objects
                    owned.add(struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0] - 2)
        if m["kind"] == "frm":
            owned |= {s for s, v in vars_.items() if v.scope == "GLB"}
        # control/property operands point at their record, 2 bytes past a variable's slot
        owned |= {s - 2 for s, v in vars_.items() if getattr(v, "fixed", False) and v.scope != "MOD"
                  and not (v.scope == "GLB" and m["kind"] == "bas")}  # a .bas's Global String * n: declared
        owned |= {s - 2 for s, v in vars_.items() if v.obj and v.scope == "LOC"}  # local object: `kind, BP` record
        first_owned = min(owned | {r - 2 for r in refs} | {s for s, v in vars_.items() if v.scope == "GLB"
                                               and s >= m["decl_start"] and not self.is_global_slot(base, s)},
                          default=word(self.image, base) - 1 if not infos else 1 << 16)
        # unused leading parameters also hold 0: the first procedure's parameters
        # start before its first used one (ByRef: 4 argument bytes, 2 slot bytes each)
        for k, info in enumerate(infos):
            ps = [(x, self.value(base, x)) for x, v in vars_.items() if v.procs and v.procs[0] == k
                  and v.scope in ("LOC", "REF") and 6 <= self.value(base, x) < 6 + 2 * info.argwords
                  and self.value(base, x) % 2 == 0]
            if ps:
                x, bp = min(ps)
                j = (6 + 2 * info.argwords - bp - 4) // 4  # index among ByRef parameters
                if j > 0 and info.proc.record in self.events:
                    first_owned = min(first_owned, x - 2 * j)
                break
            if any(v.procs and v.procs[0] == k for v in vars_.values()) or any(kk == k for kk, _ in refs.values()):
                break
        first_owned = min(first_owned, word(self.image, base) - 1)  # nothing owned: the image's end
        # procedures (record order) before the first one owning a used slot: their
        # parameters, used or not, have slots (unused ByRef 2 bytes, Control 4: kind, 0)
        m.update(vars=vars_, infos=infos)
        pb = 0
        for k in sorted(range(len(infos)), key=lambda k: infos[k].proc.record):
            if any(v.procs and v.procs[0] == k for v in vars_.values()) or any(kk == k for kk, _ in refs.values()) \
                    or k in call_slots.values():
                break
            pb += self.param_slot_bytes(m, infos[k])
        top = first_owned + (first_owned & 1)  # (odd: the image's end - 1)
        if pb and top - pb >= m["decl_start"] and all(
                self.value(base, z, False) in (0, 1, 4) for z in range(top - pb, top, 2)):
            first_owned = top - pb
        m.update(infos=infos, vars=vars_, refs=refs, udt=udt, first_owned=first_owned, call_slots=call_slots)

    def collect_calls(self, mods: list[dict]) -> None:
        """Argument types per called record (for Declare parameters)."""
        self.call_types: dict[int, list] = {}
        self.call_modules: dict[int, set] = {}
        self.call_texts: dict[int, list] = {}
        self.suffixed: set[int] = set()  # Functions whose name is written with its type suffix somewhere
        for m in mods:
            self.cur_base = m["image"]
            for info in m["infos"]:
                for i in info.insns:
                    n, sfx = plain_handler(self.rt, i.op)
                    if sfx and n == "CALL_FN":
                        at = struct.unpack_from("<H", i.operand, 2)[0]
                        self.suffixed.add(self.value(m["image"], at, False) & 0xFFF8)
                    elif sfx and info.function and (n or "").startswith("STORE") and \
                            struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0] == info.ret_slot:
                        self.suffixed.add(info.proc.record)
                calls, info.callees = [], []
                self.col_fixes = []
                self.statements(info, m["names"], calls)
                self.fit_columns(m)
                by_name = {n.lower(): x for x, n in m["names"].items()}
                for name, operand, types, texts in calls:
                    for j, (t, tx) in enumerate(zip(types, texts)):
                        v = m["vars"].get(by_name.get(tx.lower(), -1))
                        if t == "&" and v is not None and v.votes:  # ByRef argument: the variable's type
                            types[j] = "&" + v.type()
                    (rec,) = struct.unpack_from("<H", operand, 2)
                    if name == "CALL_FN":
                        rec = self.value(m["image"], rec, False)
                    if name != "CALL_FN":  # a Sub call statement allocates the record; a
                        info.callees.append(rec & 0xFFF8)  # function call in an expression doesn't
                    self.call_types.setdefault(rec & 0xFFF8, []).append(types)
                    self.call_texts.setdefault(rec & 0xFFF8, []).append(texts)
                    self.call_modules.setdefault(rec & 0xFFF8, set()).add(id(m))
