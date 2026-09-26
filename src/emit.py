"""
Source text emission: module headers, procedures in record-allocation
order, statements (lifted p-code) with their original columns.
"""
from __future__ import annotations

import re
import struct

from dataimage import word
from lift import lift
from model import (EVENT_PARAMS, EVENT_TYPE, LABEL, LABEL_WIDE, OBJ_KINDS, ProcInfo, STMT_SAME_LINE, SUFFIX,
                   TYPE_NAME, label_number, mod_name, plain_handler, stmt_column)
from ne import vbx_entries
from opcodes import NAMES
from runtime import EVENT_TYPES, MASTER_EVENT_TYPES
from symbols import CLASS_BY_KIND


class EmitMixin:
    def emit_module(self, m: dict) -> list[str]:
        self.cur_base = m["image"]
        out = []
        types = {td.g: td for td in m.get("types", [])}
        pending = sorted(types)
        gtypes = self.gimg.types
        decl_lines = self.declare_lines(m)
        for it in m["items"]:
            if it[0] == "global":
                while pending and pending[0] < it[3]:
                    out += types[pending.pop(0)].lines(gtypes)
                _, s, lit, g, t, name, arr = it
                dims = self.array_dims(0, 0, self.gimg.base + 2 + g)[0] if arr else ""
                out.append(f"Global Const {name} = {lit}" if lit else
                           f"Global {name}" + (f"({dims})" if arr else "") +
                           (f" As String * {t[1:]}" if re.fullmatch(r"F\d+", t) else f" As {TYPE_NAME.get(t, t)}"))
            elif it[0] == "typeref":
                continue
            elif it[0] == "newobj":
                _, r, form, g, arr = it
                glob = m["kind"] == "bas" and 6 <= g < self.globals_end
                out.append(f"{'Global' if glob else 'Dim'} {m['names'][r]}{'()' if arr else ''} As New {form}")
            elif it[0] == "const":
                out.append(f"Const {m['names'].get(it[1], f'K{it[1]:X}')} = {it[2]}")
            else:
                out.append(f"Dim {m['names'].get(it[1], mod_name(it[1]))}{it[2] if it[2][0] in '( ' else ' ' + it[2]}")
        for g in pending:
            out += types[g].lines(gtypes)
        # declarations record +46: where the module's Types start in the table that
        # Declare records' +24 also index (Types first: they come before the Declares)
        ends = [j for j, x in enumerate(out) if x == "End Type"]
        if ends and decl_lines and word(self.table, word(self.image, m["image"] - 2) + 4 + 46) \
                < min(m.get("decl_offs", [0])):
            out = out[:ends[-1] + 1] + decl_lines + out[ends[-1] + 1:]
        else:
            out = decl_lines + out
        # declarations record (the word before the module's image + 4): +18 flags
        # (1 Option Base 1, 0x40 Option Explicit, 0x800 Option Compare; +20: 1 Text, 0 Binary)
        rec = word(self.image, m["image"] - 2) + 4
        flags = word(self.table, rec + 18)
        head = ["Option Explicit"] if flags & 0x40 else []
        if m["defint"]:
            head.append("DefInt A-Z")
        if flags & 0x0001:
            head.append("Option Base 1")
        if flags & 0x0800:
            head.append("Option Compare Text" if word(self.table, rec + 20) else "Option Compare Binary")
        out = head + out
        if out and m["infos"]:
            out.append("")
        self.cur_mod = m
        for k, info in enumerate(self.text_order(m)):
            out += ([""] if k else []) + self.emit_proc(info, m["form"], m["vars"], m["names"], m["image"])
        return out

    def text_order(self, m: dict) -> list:
        """Procedures in an order that allocates their records as the original
        text did: a record is allocated at the first mention of its name (a
        definition or a call), so the module's records must be mentioned in
        ascending order. Depth-first search, lowest record first."""
        infos = m["infos"]
        own = sorted({i.proc.record for i in infos})
        mine = set(own)
        mentions = [[x for x in [i.proc.record] + i.callees if x in mine] for i in infos]

        def dfs(done: tuple, seen: frozenset, k: int, budget: list) -> list | None:
            if len(done) == len(infos):
                return list(done)
            budget[0] -= 1
            if budget[0] < 0:
                return None
            for j in sorted(set(range(len(infos))) - set(done), key=lambda j: infos[j].proc.record):
                s2, k2, ok = set(seen), k, True
                for x in mentions[j]:
                    if x not in s2:
                        if own[k2] != x:
                            ok = False
                            break
                        s2.add(x)
                        k2 += 1
                if ok and (r := dfs(done + (j,), frozenset(s2), k2, budget)):
                    return r
            return None

        order = dfs((), frozenset(), 0, [5000])
        return [infos[j] for j in order] if order else sorted(infos, key=lambda i: i.proc.record)

    def emit_proc(self, info: ProcInfo, form: str | None, vars_: dict, names: dict, base: int | None) -> list[str]:
        kind = "Function" if info.function else "Sub"
        params = []
        if info.event:
            ctl, _, ev = info.name.rpartition("_")
            cls = self.sym.form_class.get(form, "Form") if ctl in ("Form", "MDIForm") else \
                self.sym.classes.get((form, ctl), "")
            types = EVENT_TYPES.get((cls, ev), MASTER_EVENT_TYPES.get(ev, ()))
            pnames = EVENT_PARAMS.get(ev, "").split() or [f"P{j + 1}" for j in range(len(types))]
            decl = [f"{pn} As {EVENT_TYPE.get(t, 'Integer')}" for pn, t in zip(pnames, types)]
            if info.argwords > 2 * len(types):  # control array element
                decl = ["Index As Integer"] + decl
            # names by BP offset: ByRef parameters, 4 bytes each, the last one at +6
            bp_name = {6 + 4 * (len(decl) - 1 - j): d.split()[0] for j, d in enumerate(decl)}
            k = self.cur_mod["infos"].index(info)
            for s, v in vars_.items():
                if v.procs and v.procs[0] == k and v.scope in ("LOC", "REF") and self.value(base, s) in bp_name:
                    names[s] = bp_name[self.value(base, s)]
            params = decl
        else:
            # sizes from the BP offsets: ByRef 4 (far pointer), ByVal by type
            offs = sorted(self.value(base, s) for s in info.params)
            top = 6 + 2 * info.argwords
            psize = {o: (offs[j + 1] if j + 1 < len(offs) else top) - o for j, o in enumerate(offs)}
            for s in info.params:
                v = vars_[s]
                if v.obj:
                    params.append(f"{names[s]} As {v.obj}")
                    continue
                size = psize.get(self.value(base, s), 4)
                j = info.params.index(s)
                seen = [c[j].lstrip("&") for c in self.call_types.get(info.proc.record, []) if j < len(c) and c[j]]
                t = v.type() if v.votes else {2: "I", 8: "D", 16: "V"}.get(size) or \
                    (seen[0] if seen and seen[0] in TYPE_NAME else "V")  # unused: as the callers pass it
                byval = size != 4 or (v.scope == "LOC" and t in "LS" and t != "")
                if size == 2:
                    t = "I"
                pn = names[s]
                params.append(("ByVal " if byval else "") + pn + ("()" if v.array else "") + f" As {TYPE_NAME[t]}")
            params = self.unused_params(info, base, params)
        if info.function and info.proc.record in self.suffixed:
            head = f"{kind} {info.name}{SUFFIX[info.ret]} ({', '.join(params)})"
        else:
            head = f"{kind} {info.name} ({', '.join(params)})" + (f" As {TYPE_NAME[info.ret]}" if info.function else "")
        if self.table[info.proc.record + 14] & 0x80:  # record +14 bit 7: Static Sub/Function
            head = "Static " + head
        body = self.statements(info, names)
        dims = self.local_dims(info, vars_, names, base, body)
        lines = [head]
        for k, (col, text) in enumerate(body + [(0, None)]):
            lines += ["    " + d for d in dims.get(k, [])]
            if text is None:
                break
            if info.function:
                text = re.sub(r"\bExit Sub\b", "Exit Function", text)
            lines.append(" " * col + text)
        lines += [f"End {kind}"]
        return lines

    def statements(self, info: ProcInfo, names: dict, calls: list | None = None) -> list[tuple[int, str]]:
        """(indentation column, lifted text) per statement (marker to the
        next marker), with label lines."""
        out, cur, curn = [], [], []
        col, marked, join = [0], [False], [None]

        def flush():
            if cur and not all(NAMES.get(op, "") in ("RET", "TRAP", "OBJ_FREE") for op, _ in cur):
                text = lift(cur, self.ids, names=curn, calls=calls)
                if join[0] is not None and out:
                    out[-1] = (out[-1][0], out[-1][1] + join[0] + text)
                    join[0] = None
                elif col[0] == -1 and out:
                    out[-1] = (out[-1][0], out[-1][1] + ": " + text)
                else:
                    out.append((max(col[0], 0), text))
            cur.clear()
            curn.clear()

        for i, note in zip(info.insns, info.notes):
            if self.rt.is_stmt(i.op):
                flush()
                join[0] = None
                if i.op == STMT_SAME_LINE:
                    col[0] = -1  # joins the previous line with `:`
                else:
                    c = stmt_column(self.rt, i.op, i.operand)
                    col[0] = 4 if c is None else c
                marked[0] = True
                continue
            if i.op in (LABEL, LABEL_WIDE):
                if not cur and marked[0]:  # an empty statement: a blank line kept before a label
                    out.append((0, ""))
                flush()
                num = label_number(i.operand)
                out.append((0, f"L{i.pc:x}:" if num == 0xFFFFFFFF else f"{num}"))
                # no marker before the next code: the statement shares the label's line
                join[0] = " " * (struct.unpack_from("<H", i.operand, 4)[0] if i.op == LABEL_WIDE else 1)
                marked[0] = False
                continue
            marked[0] = False
            cur.append((i.op, i.operand))
            curn.append(self.name_for(i, note, names))
        flush()
        out = [(c, self.check_columns(c, t)) for c, t in out]
        numbered = {i.pc: label_number(i.operand) for i in info.insns
                    if i.op in (LABEL, LABEL_WIDE) and label_number(i.operand) != 0xFFFFFFFF}
        if numbered:  # line-number labels: jumps name them by number
            out = [(c, re.sub(r"\bL([0-9a-f]+)\b", lambda x: str(numbered.get(int(x.group(1), 16), x.group(0))), t))
                   for c, t in out]
        return out

    def check_columns(self, c: int, text: str) -> str:
        """Strip the lifter's `\x01col\x01` marks (text columns compiled into
        the p-code: `ReDim a(n) As T`) and note, per array name before the
        mark, how many characters too long the line is there."""
        while (k := text.find("\x01")) >= 0:
            e = text.index("\x01", k + 1)
            want = int(text[k + 1:e])
            text = text[:k] + text[e + 1:]
            target = re.search(r"\w+\([^()]*(?:\([^()]*\)[^()]*)*\) $", text[:k])
            if target and c + k != want:
                self.col_fixes.append((target.group(0), c + k - want))
        return text

    def name_for(self, i, note: str, names: dict) -> str | None:
        n = NAMES.get(i.op) or {0x13: "FIELD_ALOAD", 0x14: "FIELD_ASTORE"}.get(self.ids.get(i.op, 0) & 0xFF, "")
        sfx = ""
        if not n and plain_handler(self.rt, i.op)[0] == "CALL_FN":
            n, sfx = plain_handler(self.rt, i.op)
        slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0] if len(i.operand) >= 2 else None
        if n in ("PGET", "PSET", "PGET_IDX", "PSET_IDX"):
            if "!" in note:  # operand 0x80nn: control nn of the form object, default property
                return note.rpartition("!")[2]
            return note.rpartition(".")[2] or None
        if n in ("PGET_ME", "PSET_ME"):
            return note.rpartition(".")[2] if note else None
        if n in ("SUBOBJ", "CTLARRAY_OF"):
            return note.rpartition("!")[2].rpartition(".")[2] or None
        if n.startswith(("FIELD_GET", "FIELD_SET", "FIELD_ADDR", "FIELD_ALOAD", "FIELD_ASTORE")) and slot is not None:
            return f"F{slot:X}"  # field record offset, as in the Type declaration
        if n in ("TYPEOF_IS", "NEW_FORM") and slot is not None:
            nforms = self.forms
            base = 0x46 + len(vbx_entries(self.res.get(1, b"")))
            if 0 <= slot - base < len(nforms):
                return nforms[slot - base][0]
            vbx = [c for c in vbx_entries(self.res.get(1, b"")) if not c.upper().endswith(".VBX")]
            if 0 <= slot - 0x46 < len(vbx):  # custom control classes (in use) come first
                return vbx[slot - 0x46]
            return OBJ_KINDS.get(slot) or CLASS_BY_KIND.get(slot)
        if n == "OLE_CALL":
            return note.rpartition(".")[2] or None
        if n in ("CALL", "CALL_FN"):
            (rec,) = struct.unpack_from("<H", i.operand, 2)
            if n == "CALL_FN" and self.cur_base is not None:  # operand: the function's slot
                rec = self.value(self.cur_base, rec)
            name = self.proc_name.get(rec & 0xFFF8)
            return name and name + sfx
        if slot is not None and slot in names:
            return names[slot] + plain_handler(self.rt, i.op)[1]
        return note or None
