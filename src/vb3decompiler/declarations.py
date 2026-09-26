"""
Module-level declarations from the data images: Global/Dim/Const items,
Types, String constants, Declares (and scalar Statics from record +18).
"""
from __future__ import annotations

import re
import struct

from .dataimage import MOD_SIZE, const_literal, word
from .model import OBJ_KINDS, RET_TYPE, TYPE_NAME, Module, pool_name
from .ne import vbx_entries
from .records import (
    DECL_LINES,
    DECL_TYPES_START,
    DECLARE_DLL,
    DECLARE_ENTRY,
    DECLARE_PARAMS,
    PROC_ARG_WORDS,
    PROC_KIND,
    PROC_RET_TYPE,
    PROC_VAR_TABLE,
    RECORD_SIZE,
    decl_record,
)
from .symbols import CLASS_BY_KIND


class DeclarationsMixin:
    def fit_entries(self, m: Module, items: list) -> list:
        """The IDE's per-module variable table: from 0x2c (a form; .bas 0x2a),
        8 + slot bytes per module-level item (Function/Declare slots, Dims,
        Consts; a Type variable's type reference isn't one), then each
        procedure's (by name). So the first procedure's record +18 gives where
        the module's items end: trailing items past that are the procedures'
        Statics (same slots and p-code as a module Dim)."""
        if not m.infos or not items:
            return items
        want = min(word(self.table, i.proc.record + PROC_VAR_TABLE) for i in m.infos)
        base = (0x2c if m.kind == "frm" else 0x2a) - m.start + 8 * len(m.funcs)
        size = lambda its, end: base + 8 * sum(it[0] != "typeref" for it in its) + end
        if size(items, m.first_owned & ~1) <= want:
            return items
        for k in range(len(items) - 1, -1, -1):
            it = items[k]
            v = m.vars.get(it[1])
            filler = len(it) == 4 and it[3] == "filler"  # unused: a procedure's unused Static
            if not filler and (v is None or not v.procs or len(it) > 3 or not (
                    it[0] == "const" or (it[0] == "dim" and len(v.procs) == 1))):  # a Const: a Global Const's copy
                return items
            if size(items[:k], it[1]) == want:
                m.first_owned = it[1]
                return items[:k]
            if size(items[:k], it[1]) < want:
                return items
        return items

    def declarations(self, mods: list[Module]) -> None:
        """Header items per module, in slot (= text) order: Global, Dim, Const,
        and Types placed by global offset."""
        uses = self.global_uses(mods)
        self.global_name = {}
        for m in mods:
            m.items = self.fit_entries(m, self.module_items(m, uses))
        self.type_globals(mods, uses)
        declared = set()  # a Global is declared once: other modules' slots for it are references
        for m in mods:
            if m.kind == "bas":
                m.items = [it for it in m.items if it[0] != "global" or it[3] not in declared]
                declared |= {it[3] for it in m.items if it[0] == "global"}
        self.string_constants(mods)
        self.place_types(mods)

    @staticmethod
    def new_use() -> dict:
        return dict(votes={}, stored=False, array=False, mods=set())

    def global_uses(self, mods: list[Module]) -> dict[int, dict]:
        """Global offset -> its uses over all modules: type votes, stored,
        array, Type, the modules using it."""
        uses: dict[int, dict] = {}
        for m in mods:
            for s, v in m.vars.items():
                if v.scope == "GLB":
                    g = self.value(m.image, s, False)
                    u = uses.setdefault(g, self.new_use())
                    u["mods"].add(id(m))
                    for t, c in v.votes.items():
                        u["votes"][t] = u["votes"].get(t, 0) + c
                    u["stored"] |= v.stored
                    u["array"] |= v.array
                    u["udt"] = u.get("udt") or v.udt_type
        return uses

    def module_items(self, m: Module, uses: dict[int, dict]) -> list:
        """The module's declaration items from its image, decl_start up to
        first_owned: (kind, slot, text-or-literal, ...)."""
        gl = self.gimg
        items, s = [], m.decl_start
        end = m.first_owned
        img_end = word(self.image, m.image)
        known = sorted(x for x in m.vars if x >= s)
        while s < end:
            v = m.vars.get(s)
            nxt = next((x for x in known if x > s), end)
            if nxt == end == img_end - 1:  # `first_owned`'s "nothing owned" fallback is one
                nxt = img_end              # less than the image end (kept odd for `top`, below)
            g = self.value(m.image, s, False)
            if v is None and (a := self.array_at(m.image, s + 2)) and a[2] in (8, 9):
                s += 2  # a String * n array's length / an object array's kind
                continue
            if (a := self.array_at(m.image, s)) and (v is None or v.array) and s not in m.udt:
                dims, size = self.array_dims(m.image, s)
                if a[1]:  # Static: a procedure's (the first one's if unused)
                    m.static_arrays.append(
                        (s, f"({dims}) As {a[0]}", v.procs[0] if v is not None and v.procs else None))
                else:
                    items.append(("dim", s, f"({dims}) As {a[0]}"))
                s += size
                continue
            if v is None and getattr(m.vars.get(s + 2), "fixed", False):  # a String * n's length
                s += 2
                continue
            # the length of a Global String * n (the global's slot follows)
            if v is None and m.kind == "bas" and f"F{g}" in uses.get(
                    self.value(m.image, s + 2, False), {}).get("votes", {}):
                s += 2
                continue
            if v is None and g in gl.types:  # reference to a Type (first `As T` in the module)
                items.append(("typeref", s, None))
                s += 2
                continue
            if v is not None and v.scope == "MOD":
                s = self.module_var_item(m, s, v, nxt, items)
                continue
            kind, g2 = self.value(m.image, s, False), self.value(m.image, s + 2, False)
            newobj = self.sym.objvar_types.get(m.seg, {}) if m.seg else {}
            r = next((x for x in range(s + 2, s + 8, 2) if newobj.get(x) in self.sym.tables), None)
            if r is None and newobj.get(s) in self.sym.tables:
                r = s
            if r is not None:  # `x() As New frmX`: (class ref,) record `0x80NN, global offset`
                g2 = word(self.image, m.image + r + 2)
                arr = r in m.vars and m.vars[r].array
                items.append(("newobj", r, newobj[r], g2, arr))
                s = r + 6
                continue
            prev_g = max((it[3] for it in items if it[0] == "global"), default=None)
            fbase = 0x46 + len(vbx_entries(self.res.get(1, b"")))  # form numbers (object kind of `As frmX`)
            formk = {fbase + j: f[0] for j, f in enumerate(self.forms)}
            if v is None and (kind in CLASS_BY_KIND or kind in OBJ_KINDS or kind in formk) \
                    and 6 <= g2 < self.globals_end and not any(gl.raw(g2, 4)) \
                    and (prev_g is None or kind <= prev_g or kind in OBJ_KINDS or kind in formk):
                cls = OBJ_KINDS.get(kind) or formk.get(kind) or CLASS_BY_KIND[kind]
                items.append(("global", s, None, g2, cls))  # `Global x As <class>`: kind, global offset
                s += 4 + self.global_desc(m.image, s + 4)
                continue
            if m.kind == "bas" and (v is not None and v.scope == "GLB") or (
                    m.kind == "bas" and v is None and self.is_global_slot(m.image, s)
                                                      and self.value(m.image, s, False) not in
                                                      {x[3] for x in items if x[0] == "global"}):
                g = self.value(m.image, s, False)
                items.append(("global", s, None, g))
                s += 2 + self.global_desc(m.image, s + 2)
                continue
            # unused: a constant (nonzero) or a variable filling the gap, 2 bytes
            # at a time so that a following declaration isn't swallowed
            val = self.image[m.image + s + 2:m.image + s + 4]
            if any(val):
                items.append(("const", s, const_literal("I", val)))
            else:
                items.append(("dim", s, "As Integer", "filler"))
            s += 2
        if all(len(it) == 4 and it[3] == "filler" for it in items):
            items = []  # only zeros before the procedures: not declarations
            fv = m.vars.get(m.first_owned)
            if fv is not None and fv.scope in ("LOC", "REF"):
                m.first_owned = m.decl_start  # leading unused locals of the first procedure
            elif fv is None and m.infos and m.first_owned >= word(self.image, m.image) - 1:
                m.first_owned = m.decl_start  # nothing used at all: the procedures' unused locals
        return items

    def module_var_item(self, m: Module, s: int, v, nxt: int, items: list) -> int:
        """A module-level variable or constant at slot s (the next known slot:
        nxt): appends its item; returns the slot after it."""
        gl = self.gimg
        t = v.type()
        if v.udt and s not in m.udt:
            td = next((t for t in gl.types.values() if 0 <= nxt - s - t.size <= 4), None)
            if td:
                m.udt[s] = td.g
        if s in m.udt and v.array:  # array of a Type
            dims, size = self.array_dims(m.image, s)
            td = gl.types.get(v.udt_type or m.udt[s])
            decl = f"({dims}) As {td.name if td else 'Variant'}"
            if word(self.image, m.image + s + 6) >> 8 == 0xC2 and v.procs:  # Static (flags 0xC200)
                m.static_arrays.append((s, decl, v.procs[0]))
            else:
                items.append(("dim", s, decl))
            return s + size
        if s in m.udt:
            td = gl.types.get(m.udt[s])
            items.append(("dim", s, f"As {td.name}" if td else "As Variant"))
            step = (td.size + 1) // 2 * 2 if td else 16
            return nxt if step <= nxt - s <= step + 4 else s + step + 2
        w1 = word(self.image, m.image + s + 4)
        if not v.stored and not v.array and not v.votes.keys() - {"T", "L"} and nxt - s == 4 \
                and w1 >= 0x100:  # a String constant: descriptor, text assigned below
            items.append(("const", s, "\0str"))
            return s + 4
        if not v.stored and not v.array and (lit := self.inline_const(m.image, s, t, nxt - s)):
            items.append(("const", s, lit))
        elif v.array:
            dims, size = self.array_dims(m.image, s)
            tn = gl.types[v.udt_type].name if v.udt_type in gl.types else TYPE_NAME[t]
            items.append(("dim", s, f"({dims}) As {tn}"))
            return s + size
        else:
            items.append(("dim", s, f" As {TYPE_NAME[t]}"))
        return s + (MOD_SIZE[t] if t in MOD_SIZE else max(nxt - s, 2))

    def type_globals(self, mods: list[Module], uses: dict[int, dict]) -> None:
        """Global declarations: sizes/types from the global image and the uses;
        names G<offset>."""
        gl = self.gimg
        gs = sorted({it[3] for m in mods for it in m.items if it[0] == "global"})
        for m in mods:
            for k, it in enumerate(m.items):
                if it[0] != "global":
                    continue
                g = it[3]
                if len(it) == 5:  # object variable
                    m.items[k] = ("global", it[1], None, g, it[4], f"G{g:X}", False)
                    self.global_name[g] = f"G{g:X}"
                    continue
                nxt = next((x for x in gs if x > g), self.globals_end)
                for a, _ in gl.type_extent:
                    if g < a < nxt:
                        nxt = a
                u = uses.get(g, self.new_use())
                size = nxt - g
                t = max(u["votes"], key=u["votes"].get) if u["votes"] else \
                    {2: "I", 4: "L", 8: "D"}.get(size, "V")
                name = f"G{g:X}"
                raw = gl.raw(g, 8)
                # a constant: never assigned, and other modules don't read it as a global
                # (they read a copy of a constant); zero values included
                lit = const_literal(t, raw) if not u["stored"] and not u["array"] and t in "ILSDC" \
                    and (any(raw[:MOD_SIZE[t]]) or not (u["mods"] - {id(m)})) else None
                if u.get("udt") in gl.types:
                    t, lit = gl.types[u["udt"]].name, None
                w1 = gl.w(g + 2)
                glist = self.init_list(self.gimg_chunk)
                if size == 4 and not u["stored"] and set(u["votes"]) <= {"T", "L"} and (
                        g | 1 in glist if glist is not None else w1 >= 0x100):
                    t, lit = "T", "\0str"  # string descriptor (in the init list), text assigned below
                if re.fullmatch(r"F\d+", t):  # String * n
                    lit = None
                m.items[k] = ("global", it[1], lit, g, t, name, u["array"])
                self.global_name[g] = name

    def string_constants(self, mods: list[Module]) -> None:
        """The texts of String constants (items marked "\\0str"): a descriptor
        in the image; the texts are records `u16 size, u16 length, text, 0`
        in RT_RCDATA 2 before the global image, in declaration order."""
        gl = self.gimg
        head = self.image[:gl.base or 0]
        texts = []  # records `u16 2 + padded length, u16 length, text` (padded to even with a 0)
        for x in re.finditer(rb"(?s)(?=(..)(..)([\x20-\x7e]+))", head):
            size, n, t = struct.unpack("<H", x.group(1))[0], struct.unpack("<H", x.group(2))[0], x.group(3)
            if n and size == 2 + n + (n & 1) and (len(t) == n if n & 1 else len(t) >= n):
                texts.append(t[:n].decode("latin-1"))
        pending = [(mi, k) for mi, m in enumerate(mods) for k, it in enumerate(m.items)
                   if it[0] in ("const", "global") and len(it) > 2 and it[2] == "\0str"]
        # a descriptor is `handle, segment`; the constants' handles are 0x2A, 0x2C, ...
        # in text order, and their texts are the header's last ones (form
        # properties' strings, e.g. a Data control's, come first)
        def handle(mi: int, k: int) -> int:
            it = mods[mi].items[k]
            return gl.w(it[3]) if it[0] == "global" else self.value(mods[mi].image, it[1], False)
        hs = {(mi, k): (handle(mi, k) - 0x2A) // 2 for mi, k in pending}
        n = max(hs.values(), default=-1) + 1
        ctexts = texts[len(texts) - n:] if 0 < n <= len(texts) and min(hs.values()) >= 0 else None
        for j, (mi, k) in enumerate(pending):
            text = ctexts[hs[(mi, k)]] if ctexts is not None else texts[j] if j < len(texts) else ""
            it = mods[mi].items[k]
            mods[mi].items[k] = it[:2] + ('"' + text + '"',) + it[3:]

    def place_types(self, mods: list[Module]) -> None:
        """Types: declarations record +46 is 0xFFFF in a module without Types.
        Within those that have some: the module whose globals surround a Type,
        else distributed greedily by each candidate's declarations line count
        (rec+50), as many Types (in chain order) as fit before the next one."""
        gl = self.gimg
        bas = [m for m in mods if m.kind == "bas"] or mods
        typed = [m for m in bas if word(self.table, decl_record(self.image, m.image) + DECL_TYPES_START) != 0xFFFF]
        unowned: list = []
        for td in gl.types.values():
            owner = None
            for m in bas:
                gg = [it[3] for it in m.items if it[0] == "global"]
                if gg and min(gg) < td.g < max(gg):
                    owner = m
            if owner is None:  # else the module whose globals follow it (declared at its top)
                after = [(min(gg), k) for k, m in enumerate(bas)
                         if (gg := [it[3] for it in m.items if it[0] == "global"]) and min(gg) > td.g]
                owner = bas[min(after)[1]] if after else None
            if typed and owner not in typed:
                owner = typed[0] if len(typed) == 1 else None
            if owner is None:
                unowned.append(td)
            else:
                owner.types.append(td)
        candidates = typed or [m for m in bas if not m.items] or [bas[0]]
        ci, budget = 0, word(self.table, decl_record(self.image, candidates[0].image) + DECL_LINES)
        for td in unowned:
            need = len(td.lines(gl.types))
            while budget <= 0 and ci + 1 < len(candidates):
                ci += 1
                budget = word(self.table, decl_record(self.image, candidates[ci].image) + DECL_LINES)
            candidates[ci].types.append(td)
            budget -= need

    def declare_lines(self, m: Module) -> list[str]:
        out = []
        recs = [r for _, r in m.funcs]
        # Declare Subs (and unused Declares) have no slot: found by scanning the
        # table; declared in the module whose records surround them
        for r in sorted(r for r in range(0, len(self.table) - RECORD_SIZE + 1, 8) if r not in self.by_record
                        and r not in self.slotted and self.is_declare(r)):
            if self.declare_home(r) is m:
                recs.append(r)
        recs.sort()  # records are allocated in order of first mention in the text
        for r in recs:
            if r in self.by_record or self.declare_home(r) is not m:  # a Function's slot in a calling module
                continue
            t = self.table
            dll = pool_name(self.image, self.pool, word(t, r + DECLARE_DLL)).rstrip(".")
            fn = self.declare_name(r)
            entry = self.declare_entry(r)
            alias = f' Alias "{entry}"' if entry.startswith("#") or entry.lower() != fn.lower() else ""
            kind = "Function" if t[r + PROC_KIND] == 2 else "Sub"
            params = self.declare_params(r)
            line = f'Declare {kind} {fn} Lib "{dll}"{alias} ({", ".join(params)})'
            if kind == "Function":
                line += f" As {TYPE_NAME[RET_TYPE.get(t[r + PROC_RET_TYPE], 'V')]}"
            out.append(line)
            m.decl_offs.append(word(t, r + DECLARE_PARAMS))
        return out

    def declare_entry(self, r: int) -> str:
        """A Declare's DLL entry name (`#n`: an ordinal)."""
        return pool_name(self.image, self.pool, word(self.table, r + DECLARE_ENTRY))

    def declare_name(self, r: int) -> str:
        """A Declare's name: its DLL entry name (an ordinal `#n`: Ord<n>, with an Alias)."""
        fn = self.declare_entry(r)
        return f"Ord{fn[1:]}" if fn.startswith("#") else fn

    def declare_home(self, r: int) -> Module:
        """The module a slotless Declare is written in: a module's records
        (procedures, Declares) follow its declarations record in the table."""
        starts = [(word(self.image, m.image - 2) + 4, k) for k, m in enumerate(self.all_mods)]
        k = max(((r0, k) for r0, k in starts if r0 < r), default=None)
        return self.all_mods[k[1]] if k else self.decl_home

    def declare_params(self, r: int) -> list[str]:
        """DLL parameters from the argument types at call sites (arguments are
        converted to the declared type), else from the argument size."""
        seen = self.call_types.get(r, [])
        words = self.table[r + PROC_ARG_WORDS]
        out = []
        taken = {self.declare_name(x).lower() for x in range(0, len(self.table) - RECORD_SIZE + 1, 8)
                 if x not in self.by_record and self.is_declare(x)}
        pn = "P" if not any(re.fullmatch(r"p\d+", x) for x in taken) else "Arg"
        if seen:
            n = max(len(x) for x in seen)
            for j in range(n):
                ts = [x[j] for x in seen if j < len(x) and x[j]]
                t = ts[0] if ts else ""
                if t == "*" or (t.startswith("&") and t != "&T"):
                    out.append(f"{pn}{j + 1} As Any")
                elif t == "T":
                    out.append(f"ByVal {pn}{j + 1} As String")
                elif t == "&T":
                    out.append(f"{pn}{j + 1} As String")
                else:
                    t = {"L/T": "L", "": "I", "V": "I"}.get(t, t)
                    out.append(f"ByVal {pn}{j + 1} As {TYPE_NAME.get(t, 'Integer')}")
            return out
        if not words:  # unused: record +24 grows by 20 + 8 per parameter to the next Declare's
            nxt = next((x for x in range(r + RECORD_SIZE, len(self.table) - RECORD_SIZE + 1, 8)
                        if x not in self.by_record and self.is_declare(x)), None)
            gap = word(self.table, nxt + DECLARE_PARAMS) - word(self.table, r + DECLARE_PARAMS) - 20 \
                if nxt is not None else -1
            if gap > 0 and gap % 8 == 0 and gap // 8 <= 30:
                return [f"ByVal {pn}{k + 1} As Integer" for k in range(gap // 8)]  # (types don't matter)
        k = 0
        while words > 0:
            k += 1
            out.append(f"ByVal {pn}{k} As {'Integer' if words == 1 else 'Long'}")
            words -= 1 if words == 1 else 2
        return out
