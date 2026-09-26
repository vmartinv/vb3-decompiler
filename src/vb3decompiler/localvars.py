"""
Local declarations of each procedure: where each Dim/Static goes so slots
are allocated in the original order, and the types of unused locals and
parameters recovered from the BP frame.
"""
from __future__ import annotations

import re

from .dataimage import MOD_SIZE, word
from .model import LABEL, LABEL_WIDE, TYPE_NAME, ProcInfo, Var
from .records import PROC_FLAGS, PROC_FRAME, PROC_NUMBERED, PROC_STATIC
from .runtime import EVENT_TYPES, MASTER_EVENT_TYPES


class LocalsMixin:
    def unused_params(self, info: ProcInfo, base: int, params: list[str]) -> list[str]:
        """Parameters the body never uses have no slot: when the callers pass
        more arguments, rebuild the list from the BP frame (from the top:
        a used parameter where its size fits exactly, else a ByRef filler,
        4 bytes, typed as the callers pass it)."""
        calls = self.call_types.get(info.proc.record, [])
        texts = self.call_texts.get(info.proc.record, [])
        nargs = max((len(c) for c in calls), default=0)
        if nargs <= len(info.params):
            return params
        used = {self.value(base, s): p for s, p in zip(info.params, params)}
        out, cur = [], 6 + 2 * info.argwords
        for j in range(nargs):
            below = [o for o in used if o < cur]
            if below:
                o = max(below)
                p = used[o]
                size = 4 if not p.startswith("ByVal ") else \
                    {"Integer": 2, "Double": 8, "Currency": 8, "Variant": 16}.get(p.rpartition(" ")[2], 4)
                if cur - o == size:
                    out.append(p)
                    cur = o
                    continue
            seen = [c[j].lstrip("&") for c in calls if j < len(c) and c[j]]
            t = seen[0] if seen and seen[0] in TYPE_NAME else "V"
            arr = any(j < len(x) and x[j].endswith("()") for x in texts)
            out.append(f"u{j}{'()' if arr else ''} As {TYPE_NAME[t]}")
            cur -= 4
        return out

    def local_dims(self, info: ProcInfo, vars_: dict, names: dict, base: int, body: list) -> dict[int, list[str]]:
        """Dim/Static lines for the procedure's locals, placed so that slots are
        allocated in the original order: slots follow text order, and a
        control/global reference or an undeclared variable gets its slot at
        its first appearance. Returns statement index -> lines before it."""
        m = self.cur_mod
        k = m["infos"].index(info)
        skip = set(info.params) | ({info.ret_slot} if info.ret_slot is not None else set())
        items = []  # (slot, kind, name, decl)
        frame = sorted((s for s, v in vars_.items() if v.procs and v.procs[0] == k and v.scope in ("LOC", "REF")
                        and s not in skip), key=lambda s: s)
        # frame sizes: BP offsets decrease in slot order from -22 (ret value first)
        offs = [(s, self.value(base, s)) for s in sorted(set(frame) | skip) if self.value(base, s) < 0]
        size, prev = {}, -22
        for s, o in offs:
            size[s], prev = prev - o, o
        objtypes = self.sym.objvar_types.get(m["seg"], {}) if m["seg"] else {}
        for s in frame:
            v = vars_[s]
            if objtypes.get(s) or v.obj:
                t = objtypes.get(s) or v.obj
                decl = ("()" if v.array else "") + (f" As New {t}" if t in self.sym.tables else f" As {t}")
            elif v.udt:
                td = self.gimg.types.get(m["udt"].get(s)) or next(
                    (t for t in self.gimg.types.values() if 0 <= size.get(s, 0) - t.size <= 2), None)
                decl = f"As {td.name}" if td else "As Variant"
            else:
                t = v.type() if v.votes else ("T" if self.value(base, s) % 2 == 1 else
                                              {2: "I", 4: "L", 8: "D"}.get(size.get(s), "V"))
                decl = ("()" if v.array else "") + f" As {TYPE_NAME[t]}"
            # a leading line label: the procedure's first line (a Dim before it would
            # take a statement marker), so Variants are left undeclared
            implicit = decl == " As Variant" and not m.get("defint") and info.insns \
                and info.insns[0].op in (LABEL, LABEL_WIDE)
            items.append((s, "fixed" if implicit else "dim", names[s], decl, v))
        sarr = {a[0] for a in m.get("static_arrays", [])}
        for s, v in vars_.items():
            if v.scope == "MOD" and v.procs and v.procs[0] == k and s >= m["first_owned"] and s not in sarr \
                    and (v.procs == [k] or not names[s].startswith(("s", "K"))):
                lit = None if v.stored or v.array else self.inline_const(base, s, v.type(), 16)
                if not names[s].startswith(("K", "s")):
                    items.append((s, "fixed", names[s], None, v))  # a Global Const's copy
                elif lit:  # a Const inside the procedure: stored inline like a module one
                    items.append((s, "const", names[s], f"= {lit}", v))
                else:
                    dims = f"({self.array_dims(base, s)[0]})" if v.array else ""
                    items.append((s, "static", names[s], dims + f" As {TYPE_NAME[v.type()]}", v))
            elif v.scope == "GLB" and v.procs and v.procs[0] == k and s >= m["first_owned"]:
                items.append((s, "fixed", names[s], None, v))
        first = self.text_order(m)[0] is info
        for s, decl, kk in m.get("static_arrays", []):
            if kk == k or (kk is None and first):
                names.setdefault(s, f"s{s:X}")
                items.append((s, "static", names[s], decl, Var(s, "MOD", array=True)))
        for s, (kk, n) in m["refs"].items():
            if kk == k:
                items.append((s, "fixed", n, None, None))
        for s, kk in m.get("call_slots", {}).items():  # external functions: slot at the call
            if kk == k and (n := self.proc_name.get(self.value(base, s) & 0xFFF8)):
                items.append((s, "fixed", n, None, None))
        items.sort(key=lambda it: (it[0], it[1]))
        # unused locals leave gaps in the slot numbering: declare fillers
        # (odd value: String local; negative: BP offset, size from the frame)
        known = {it[0] for it in items} | skip
        for it in items:
            if it[1] == "dim" and it[3].endswith("As Variant"):
                known.add(it[0] + 2)  # a local Variant takes 4 bytes of slots
        for x, v in vars_.items():  # constants' copies / statics: inline, sized by type
            if v.scope == "MOD" and v.procs and v.procs[0] == k and x >= m["first_owned"]:
                n = self.array_dims(base, x)[1] if v.array else MOD_SIZE.get(v.copy_type or v.type(), 2)
                known.update(range(x, x + n, 2))
        for x, v in vars_.items():  # a local array takes 6 bytes of slots
            if v.array and v.scope == "LOC" and v.procs and v.procs[0] == k:
                known.update((x + 2, x + 4))
        for x in skip:  # Variant return value / parameters take 4 bytes of slots too
            v = vars_.get(x)
            t = info.ret if x == info.ret_slot else (v.type() if v and v.votes else "V")
            if t == "V":
                known.add(x + 2)
        mine = sorted(known)
        if mine:
            later = [x for kk2, other in enumerate(m["infos"]) if kk2 > k
                     for x in [min((ss for ss, vv in vars_.items() if vv.procs and vv.procs[0] == kk2), default=None)]
                     if x is not None]
            hi = min([x for x in later if x > mine[-1]] + [mine[-1] + 2])
            negs = sorted((x, self.value(base, x)) for x in range(mine[0], hi, 2) if self.value(base, x) < 0)
            fsize, prev = {}, -22
            for x, o in negs:
                fsize[x], prev = prev - o, o
            # locals only: from the first local (negative BP offset or String number) on
            frame_known = sorted(x for x in known if x in vars_ and vars_[x].scope in ("LOC", "REF")
                                 and (self.value(base, x) < 0 or self.value(base, x) % 2 == 1)) or [1 << 30]
            owned_k = [x for x, v in vars_.items() if v.procs and v.procs[0] == k] + \
                      [r - 2 for r, (kk2, _) in m["refs"].items() if kk2 == k] + \
                      [x for x, kk2 in m.get("call_slots", {}).items() if kk2 == k]
            hi = min(hi, max(owned_k + [0]))  # up to the procedure's last slot
            records = [r for r, (kk2, _) in m["refs"].items()] + \
                      [x2 for x2, v2 in vars_.items() if v2.obj or v2.glob is not None]
            calls_here = set(m.get("call_slots", {}))  # external function slots: `0, record`
            if frame_known == [1 << 30] and owned_k:
                # no used locals: unused ones are the zeros between the previous
                # procedure's slots and this one's first (String fillers: no frame)
                rest = [x for x in owned_k if x not in skip]
                fo, pe = (min(rest) if rest else 1 << 30), self.prev_end(m, k)
                # the procedure's own (unused) parameters come first: 2 slot bytes per
                # ByRef parameter (4 argument bytes)
                pe += self.param_slot_bytes(m, info)
                pe = max([pe] + [x + 2 for x in skip])
                if pe < fo and all(self.value(base, z) == 0 for z in range(pe, fo, 2)):
                    # typed from the record (frame, numbered count) if they account for them
                    ts = self.trailing_locals(info, base, [], len(range(pe, fo, 2)), -22)
                    if ts and (word(self.table, info.proc.record + PROC_FRAME) > 22
                               or word(self.table, info.proc.record + PROC_NUMBERED)):
                        z = pe
                        for t in ts:
                            items.append((z, "dim", f"f{z:X}", f" As {t}", Var(z, "LOC")))
                            z += 4 if t == "Variant" else 2
                    else:
                        for z in range(pe, fo, 2):
                            items.append((z, "static", f"f{z:X}", " As Integer", Var(z, "MOD")))
            x = frame_known[0]
            # leading unused locals: zero slots from the end of the previous
            # procedure's allocations up to the first used local
            # only with evidence: the module's first procedure (zeros after the
            # declarations), or frame space the first local doesn't account for
            prev_end = self.prev_end(m, k)
            first_bp = self.value(base, x)
            v1 = vars_.get(x)
            fs1 = {"I": 2, "L": 4, "S": 4, "D": 8, "C": 8, "V": 16}.get(v1.type() if v1 and v1.votes else "V", 2)
            # Strings and Variants share one numbering (1, 3, ...): a first local numbered
            # above 1 means numbered locals were declared before it
            evidence = prev_end == m["first_owned"] or (first_bp < 0 and -22 - first_bp - fs1 >= 16) or \
                (first_bp > 1 and first_bp % 2 == 1)
            if evidence and prev_end < x and all(self.value(base, z) == 0 and z not in known
                                                 for z in range(prev_end, x, 2)):
                x = prev_end
            run_types = self.solve_runs(m, k, info, base, known, x, hi, records, calls_here)
            while x < hi:
                if any(r - 2 <= x < r + 6 for r in records) or x in calls_here:
                    x += 2  # inside a control/object record or an external function slot
                    continue
                if x in known:
                    x += 2
                    continue
                o = self.value(base, x)
                if o == 0:
                    # a run of unused locals (value 0): unused Variants still take 16 bytes of
                    # frame (2 slots), unused Strings none (1 slot); split by the frame gap
                    y = x
                    while y < hi and y not in known and self.value(base, y) == 0 \
                            and not any(r - 2 <= y < r + 6 for r in records) and y not in calls_here:
                        y += 2
                    prev_bp = min([self.value(base, z) for z in known if z < x and self.value(base, z) < 0] + [-22])
                    nxt_k = min((z for z in known if z >= y and self.value(base, z) < 0), default=None)
                    # frame gap (unused Variants take 16 bytes, numerics their size) and
                    # numbering (Variants and Strings share 1, 3, ...) of the next locals
                    nv, ns, extra, framed = 0, 0, 0, nxt_k is not None
                    if framed:
                        v2 = vars_.get(nxt_k)
                        t2 = v2.type() if v2 is not None and v2.votes else "V"
                        fs2 = {"I": 2, "L": 4, "S": 4, "D": 8, "C": 8, "V": 16}.get(t2, 2)
                        extra = max(0, prev_bp - self.value(base, nxt_k) - fs2)
                        nv = min(extra // 16, (y - x) // 4)
                        extra -= 16 * nv
                    nxt_s = min((z for z in known if z >= y and self.value(base, z) > 0  # (a Variant's
                                 and self.value(base, z) % 2 == 1 and (z in vars_ or z - 2 in vars_)),  # number
                                default=None)  # follows its BP offset)
                    if nxt_s is not None:
                        prior = sum(1 for z in known if z < x and self.value(base, z) > 0
                                    and self.value(base, z) % 2 == 1 and self.value(base, z) < 200)
                        numbered = max(0, (self.value(base, nxt_s) - 1) // 2 - prior)
                        if framed:
                            ns = max(0, numbered - nv)  # the other numbered ones: Strings
                        else:
                            nv = min(numbered, (y - x) // 4)
                    sizes, ts = None, run_types.get(x)
                    if ts is not None:
                        pass
                    elif framed and nxt_s is not None:  # frame gap and numbering both known
                        ts = self.split_unused((y - x) // 2, extra + 16 * nv, numbered)
                    elif not framed and nxt_s is None and y >= hi:
                        ts = self.trailing_locals(info, base, known, (y - x) // 2, prev_bp)
                    if ts is not None:
                        nv, ns = ts.count("Variant"), ts.count("String")
                        sizes = [{"Double": 8, "Long": 4, "Integer": 2}[t] for t in ts
                                 if t not in ("Variant", "String")]
                        extra = sum(sizes)
                    while x < y:
                        if nv > 0:
                            vt, step, nv = "Variant", 4, nv - 1
                        elif ns > 0:
                            vt, step, ns = "String", 2, ns - 1
                        elif extra >= 2:  # the rest of the frame gap: numeric locals
                            size = sizes.pop(0) if sizes else 8 if extra >= 8 else 4 if extra >= 4 else 2
                            vt = {8: "Double", 4: "Long", 2: "Integer"}[size]
                            extra -= size
                            step = 2
                        else:  # no frame, no number: a Static (module storage) keeps both
                            vt, step = "Static", 2
                        if vt == "Static":
                            items.append((x, "static", f"f{x:X}", " As Integer", Var(x, "MOD")))
                        else:
                            items.append((x, "dim", f"f{x:X}", f" As {vt}", Var(x, "LOC")))
                        x += step
                    continue
                interior = x < frame_known[-1]  # nonzero values are locals only between known ones
                if o % 2 == 1 and 0 < o < 64 and interior:  # a String local
                    items.append((x, "dim", f"f{x:X}", " As String", Var(x, "LOC")))
                    x += 2
                elif o < 0 and interior:
                    t = {2: "I", 4: "L", 8: "D", 16: "V"}.get(fsize.get(x), "I")
                    items.append((x, "dim", f"f{x:X}", f" As {TYPE_NAME[t]}", Var(x, "LOC")))
                    x += 4 if t == "V" else 2
                else:
                    x += 2
        # zeros just before the next procedure's slots, after all earlier ones: unused
        # locals of a procedure that owns no slots (String fillers: no frame); they go
        # in the procedure right before it
        def own(kk: int) -> list:
            return [x for x, v in vars_.items() if v.procs and v.procs[0] == kk and x >= m["first_owned"]] + \
                   [r - 2 for r, (k2, _) in m["refs"].items() if k2 == kk] + \
                   [x for x, k2 in m.get("call_slots", {}).items() if k2 == kk]
        nxt_info = m["infos"][k + 1] if k + 1 < len(m["infos"]) else None
        # only when the next procedure starts with its first parameter: zeros before
        # it can't be its own locals (they follow its parameters)
        first_ok = nxt_info is not None and own(k + 1) and nxt_info.argwords and \
            self.value(base, min(own(k + 1))) == 6 + 2 * nxt_info.argwords - 4
        if first_ok and not own(k) and not info.argwords:
            end, start = self.prev_end(m, k + 1), min(own(k + 1))
            if end < start and all(self.value(base, z) == 0 for z in range(end, start, 2)):
                for z in range(end, start, 2):
                    items.append((z, "static", f"f{z:X}", " As Integer", Var(z, "MOD")))
        if k == self.tail_owner(m):  # zeros after every procedure's slots: unused locals too
            end, n = self.prev_end(m, len(m["infos"])), word(self.image, base)
            end = max([end] + [it[0] + 2 for it in items])  # (not the ones declared already)
            if end < n - 1 and all(self.value(base, z) == 0 for z in range(end, n - 1, 2)):
                known_k = [x for x, v in vars_.items() if v.procs and v.procs[0] == k and v.scope in ("LOC", "REF")]
                prev_bp = min([self.value(base, z) for z in known_k if self.value(base, z) < 0] + [-22])
                ts = getattr(self, "run_solution", {}).get((base, k, end)) or \
                    self.trailing_locals(info, base, known_k, len(range(end, n - 1, 2)), prev_bp)
                if ts and any(t != "Integer" for t in ts) \
                        or ts and word(self.table, info.proc.record + PROC_FRAME) > -prev_bp:
                    z = end
                    for t in ts:
                        items.append((z, "dim", f"f{z:X}", f" As {t}", Var(z, "LOC")))
                        z += 4 if t == "Variant" else 2
                else:
                    for z in range(end, n - 1, 2):
                        items.append((z, "static", f"f{z:X}", " As Integer", Var(z, "MOD")))
        items.sort(key=lambda it: (it[0], it[1]))

        texts = [re.sub(r'"[^"]*"', lambda x: " " * len(x.group(0)), t or "") for _, t in body]

        def compile_order(t: str) -> str:
            """An assignment's target gets its slot after the expression is compiled."""
            mt = re.match(r"^(\s*)(Set\s+|Let\s+)?([A-Za-z_][\w.!$%&#@]*(?:\([^=]*\))?)\s*=\s*(.*)$", t)
            if mt and not re.match(r"^\s*(If|ElseIf|For|Select|Case|Do|Loop|While)\b", t, re.I):
                return mt.group(1) + mt.group(4) + " " + mt.group(3)
            return t
        texts = [":".join(compile_order(x) for x in t.split(":")) for t in texts]  # (strings are blanked)

        def appear(name: str):
            base_name = re.escape(name.rstrip("%&!#@$"))
            pat = re.compile(rf"(?<![\w.!]){base_name}(?![\w])", re.I)
            for i, t in enumerate(texts):
                mt = pat.search(t)
                if mt:
                    return (i, mt.start(), t[mt.end():mt.end() + 1] in "%&!#@$" and t[mt.end():mt.end() + 1] != "")
            return None

        keys = {it[0]: appear(it[2]) for it in items}
        dims: dict[int, list[str]] = {}
        last = (-1, 0)
        for s, kind, name, decl, v in items:
            key = keys[s]
            if kind == "fixed":
                if key is not None and key[:2] > last:
                    last = key[:2]
                continue
            # declare at the earliest point after the previous item; if that is
            # past the first use (same statement as a preceding control
            # reference), the variable was declared implicitly there
            pos = 0 if last == (-1, 0) else (last[0] if last[1] < 0 else last[0] + 1)
            implicit_type = "Integer" if m["defint"] else "Variant"
            # in a Static Sub/Function every local is static: those may be implicit too
            dimlike = kind == "dim" or (
                kind == "static" and self.table[info.proc.record + PROC_FLAGS] & PROC_STATIC and v is not None)
            if key is not None and key[0] < pos and dimlike and not v.array and not v.udt \
                    and key[:2] > last and not m["explicit"] and (decl.strip() == f"As {implicit_type}" or key[2]):
                last = key[:2]
                continue
            if key is not None and key[0] < pos:
                pos = key[0]  # conflicting order: at least keep it compilable
            word_ = {"static": "Static", "const": "Const"}.get(kind, "Dim")
            dims.setdefault(pos, []).append(f"{word_} {name}{decl if decl[0] in '( ' else ' ' + decl}")
            # could be implicit instead: allocated at its first use
            last = (pos, -1)
        return dims

    def item_count(self, m: dict) -> int:
        """Slot-holding items of the emitted module (variables, control/form
        references, external function slots, parameters used or not,
        fillers): the declarations record +12 is the image end + 2 each."""
        unused_p = 0
        for info in m["infos"]:
            ev = self.events.get(info.proc.record)
            if ev:
                ctl, _, e = ev.rpartition("_")
                cls = self.sym.form_class.get(m["form"], "Form") if ctl in ("Form", "MDIForm") else \
                    self.sym.classes.get((m["form"], ctl), "")
                types = EVENT_TYPES.get((cls, e), MASTER_EVENT_TYPES.get(e, ()))
                n = len(types) + (info.argwords > 2 * len(types))
            else:
                n = info.argwords // 2
            unused_p += max(0, n - len([x for x in info.params if x in m["vars"]]))
        fill = sum(len(re.findall(r"\b[fs][0-9A-F]+(?:\(.*?\))? As", ln)) for ln in m["lines"])
        mi = sum(1 for it in m["items"] if len(it) == 4 and it[3] == "filler")
        return len(m["vars"]) + len(m["refs"]) + len(m.get("call_slots", {})) + unused_p + fill + mi

    def tail_owner(self, m: dict) -> int:
        """The procedure owning the zeros after every procedure's slots: the
        one whose record (+0 frame, +10 numbered count) says it has locals
        its known ones don't account for; else the last one."""
        if "tail_owner" in m:
            return m["tail_owner"]
        size = {"I": 2, "L": 4, "S": 4, "D": 8, "C": 8, "V": 16, "T": 0}
        base, vars_ = m["image"], m["vars"]
        owner = len(m["infos"]) - 1
        for k, info in enumerate(m["infos"]):
            own = [q for q, v in vars_.items() if v.procs and v.procs[0] == k and v.scope in ("LOC", "REF")]
            frame = sum(size.get(info.ret if q == info.ret_slot else (vars_[q].type() if vars_[q].votes else "V"), 2)
                        for q in own if self.value(base, q) < 0)
            num = sum(1 for q in own for z in (q, q + 2) if 0 < self.value(base, z) < 200 and self.value(base, z) % 2)
            if word(self.table, info.proc.record + PROC_FRAME) - 22 > frame \
                    or word(self.table, info.proc.record + PROC_NUMBERED) > num:
                if not any(self.value(base, q) == 0 for q in own):  # (its own gaps don't explain it)
                    owner = k
        m["tail_owner"] = owner
        return owner

    def solve_runs(self, m: dict, k: int, info: ProcInfo, base: int, known, x: int, hi: int,
                   records: list, calls_here: set) -> dict:
        """Types for every run of unused locals (zero slots) of a procedure,
        solved together: each run's slot count is known, its frame bytes
        (the BP gap to the next local) and numbered count (from the next
        numbered local) only sometimes; the record gives the totals (+0: 22 +
        frame, +10: numbered). Run start -> types; also fills
        self.run_solution for the zeros after the last procedure."""
        vars_ = m["vars"]
        self.run_solution = getattr(self, "run_solution", {})
        size = {"I": 2, "L": 4, "S": 4, "D": 8, "C": 8, "V": 16, "T": 0}
        # runs: [start, slots, frame group, numbering group]; a group is the runs before
        # the same next framed / numbered local, whose gap / number they share
        runs, fgroups, ngroups = [], {}, {}
        z = x
        while z < hi:
            if any(r - 2 <= z < r + 6 for r in records) or z in calls_here or z in known or self.value(base, z):
                z += 2
                continue
            y = z
            while y < hi and y not in known and self.value(base, y) == 0 \
                    and not any(r - 2 <= y < r + 6 for r in records) and y not in calls_here:
                y += 2
            nk = min((q for q in known if q >= y and self.value(base, q) < 0), default=None)
            if nk is not None and nk not in fgroups:
                prev_bp = min([self.value(base, q) for q in known if q < z and self.value(base, q) < 0] + [-22])
                v2 = vars_.get(nk)
                fgroups[nk] = max(0, prev_bp - self.value(base, nk)
                                  - size.get(v2.type() if v2 is not None and v2.votes else "V", 2))
            ns_ = min((q for q in known if q >= y and 0 < self.value(base, q) < 200 and self.value(base, q) % 2
                       and (q in vars_ or q - 2 in vars_)), default=None)
            if ns_ is not None and ns_ not in ngroups:
                prior = sum(1 for q in known if q < z and 0 < self.value(base, q) < 200 and self.value(base, q) % 2)
                ngroups[ns_] = max(0, (self.value(base, ns_) - 1) // 2 - prior)
            runs.append([z, (y - z) // 2, nk, ns_])
            z = y
        if k == self.tail_owner(m):  # the zeros after every procedure's slots
            end, n = self.prev_end(m, len(m["infos"])), word(self.image, base)
            if end < n - 1 and all(self.value(base, q) == 0 for q in range(end, n - 1, 2)):
                runs.append([end, len(range(end, n - 1, 2)), None, None])
        if not runs:
            return {}
        own = [q for q, v in vars_.items() if v.procs and v.procs[0] == k and v.scope in ("LOC", "REF")
               and self.value(base, q) < 0]
        known_frame = sum(size.get(info.ret if q == info.ret_slot else (vars_[q].type() if vars_[q].votes else "V"), 2)
                          for q in own)
        known_num = sum(1 for q in known if 0 < self.value(base, q) < 200 and self.value(base, q) % 2)
        f_tot = word(self.table, info.proc.record + PROC_FRAME) - 22 - known_frame
        n_tot = word(self.table, info.proc.record + PROC_NUMBERED) - known_num

        def can(n: int, f: int) -> bool:
            return f == 0 if n == 0 else any(q <= f and can(n - 1, f - q) for q in (8, 4, 2))

        def options(r):  # (nv, ns, nn, frame) per run, most Variants first
            sl = r[1]
            for nv in range(sl // 2, -1, -1):
                for ns in range(sl - 2 * nv, -1, -1):
                    nn = sl - 2 * nv - ns
                    for fr in range(8 * nn, 2 * nn - 1, -2):
                        if can(nn, fr):
                            yield nv, ns, nn, fr

        sols = []

        def dfs(j: int, acc: list, fs: int, ns: int, fg: dict, ng: dict):
            if len(sols) > 400:
                return
            if j == len(runs):
                if fs == f_tot and ns == n_tot and fg == fgroups and ng == ngroups:
                    sols.append(list(acc))
                return
            r = runs[j]
            for o in options(r):
                df, dn = 16 * o[0] + o[3], o[0] + o[1]
                if fs + df > f_tot or ns + dn > n_tot:
                    continue
                fg2, ng2 = dict(fg), dict(ng)
                if r[2] is not None:
                    fg2[r[2]] = fg2.get(r[2], 0) + df
                    if fg2[r[2]] > fgroups[r[2]]:
                        continue
                if r[3] is not None:
                    ng2[r[3]] = ng2.get(r[3], 0) + dn
                    if ng2[r[3]] > ngroups[r[3]]:
                        continue
                # a group is complete after its last run
                if any(g is not None and g not in (x2[2] for x2 in runs[j + 1:]) and fg2.get(g, 0) != fgroups[g]
                       for g in [r[2]]) or \
                        any(g is not None and g not in (x2[3] for x2 in runs[j + 1:]) and ng2.get(g, 0) != ngroups[g]
                            for g in [r[3]]):
                    continue
                dfs(j + 1, acc + [o], fs + df, ns + dn, fg2, ng2)

        if sum(r[1] for r in runs) > 24:  # too many to search: the per-run guesses
            return {}
        dfs(0, [], 0, 0, {}, {})
        if not sols:
            return {}
        # the declarations record's item count fixes the Variants: each takes 2 slots as 1 item
        base_nv = sum(o[0] for o in sols[0])
        want = base_nv + m.get("nv_delta", 0)
        sol = next((x2 for x2 in sols if sum(o[0] for o in x2) == want), sols[0])
        out = {}
        for r, (nv, ns, nn, f) in zip(runs, sol):
            ts = ["Variant"] * nv + ["String"] * ns
            for q in range(nn, 0, -1):
                zz = next(zz for zz in (8, 4, 2) if zz <= f and can(q - 1, f - zz))
                ts.append({8: "Double", 4: "Long", 2: "Integer"}[zz])
                f -= zz
            out[r[0]] = ts
            self.run_solution[(base, k, r[0])] = ts
        return out

    def trailing_locals(self, info: ProcInfo, base: int, known, slots: int, prev_bp: int) -> list[str] | None:
        """Types of a procedure's last `slots` unused locals from its record:
        +0 is 22 + the frame size, +10 the count of numbered locals (Strings
        and Variants)."""
        rec = info.proc.record
        extra = max(0, prev_bp + word(self.table, rec + PROC_FRAME))
        have = sum(1 for z in known if 0 < self.value(base, z) < 200 and self.value(base, z) % 2)
        return self.split_unused(slots, extra, max(0, word(self.table, rec + PROC_NUMBERED) - have))

    def split_unused(self, slots: int, frame: int, numbered: int) -> list[str] | None:
        """Types for a run of unused locals: Variants take 2 slots, 16 frame
        bytes and a number, Strings 1 slot and a number, numbers 1 slot and
        2/4/8 bytes. Several splits fit; most Variants first, `nv_pick` (from
        the declarations record's item count) moves down. None: no split."""
        def can(n: int, f: int) -> bool:  # n numbers of 2/4/8 bytes summing to f
            return f == 0 if n == 0 else any(z <= f and can(n - 1, f - z) for z in (8, 4, 2))

        def ok(nv: int) -> bool:
            nn, f = slots - 2 * nv - (numbered - nv), frame - 16 * nv
            return nn >= 0 and numbered - nv >= 0 and 2 * nn <= f <= 8 * nn and can(nn, f)

        opts = [nv for nv in range(min(numbered, slots // 2, frame // 16), -1, -1) if ok(nv)]
        if not opts:
            return None
        nv = opts[min(self.cur_mod.get("nv_pick", 0), len(opts) - 1)]
        ns, nn, f = numbered - nv, slots - 2 * nv - (numbered - nv), frame - 16 * nv
        out = ["Variant"] * nv + ["String"] * ns
        for r in range(nn, 0, -1):
            z = next(z for z in (8, 4, 2) if z <= f and 2 * (r - 1) <= f - z <= 8 * (r - 1) and can(r - 1, f - z))
            out.append({8: "Double", 4: "Long", 2: "Integer"}[z])
            f -= z
        return out

    def param_slot_bytes(self, m: dict, info: ProcInfo) -> int:
        """Slot bytes of a procedure's parameters and return value: 2 each,
        4 for object (Control/Form) and Variant ones."""
        n = 2 if info.function else 0
        ev = self.events.get(info.proc.record)
        if ev:
            ctl, _, e = ev.rpartition("_")
            cls = self.sym.form_class.get(m["form"], "Form") if ctl in ("Form", "MDIForm") else \
                self.sym.classes.get((m["form"], ctl), "")
            types = EVENT_TYPES.get((cls, e), MASTER_EVENT_TYPES.get(e, ()))
            n += sum(4 if t == 8 else 2 for t in types)
            if info.argwords > 2 * len(types):
                n += 2  # Index
            return n
        count = info.argwords // 2
        known = [m["vars"][x] for x in info.params if x in m["vars"]]
        return n + 2 * count + sum(2 for v in known if v.obj or (v.votes and v.type() == "V"))

    def prev_end(self, m: dict, k: int) -> int:
        """End of the slots allocated by the procedures before k (text order),
        each one's range starting with its parameters (2 slot bytes per ByRef
        parameter, used or not) and its return value."""
        if "ends" not in m:
            base, vars_, ends, end = m["image"], m["vars"], [], m["first_owned"]
            for kk, info in enumerate(m["infos"]):
                e = end + self.param_slot_bytes(m, info)
                for x, v in vars_.items():
                    if v.procs and v.procs[0] == kk and x >= m["first_owned"]:
                        if v.scope == "MOD":
                            n = self.array_dims(base, x)[1] if v.array else MOD_SIZE.get(v.copy_type or v.type(), 2)
                        else:
                            n = 4 if (v.votes and v.type() == "V") or (
                                x + 2 not in vars_ and self.value(base, x + 2, False) % 2 == 1) else 2
                        e = max(e, x + n)
                for r, (k2, _) in m["refs"].items():
                    if k2 == kk:
                        e = max(e, r + (2 if word(self.image, base + r) >> 8 == 0x80 else 4))
                for x, k2 in m.get("call_slots", {}).items():
                    if k2 == kk:
                        e = max(e, x + 2)
                ends.append(e)
                end = e
            m["ends"] = ends
        return m["ends"][k - 1] if k > 0 else m["first_owned"]
