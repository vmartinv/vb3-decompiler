"""
Names: variables by slot, general procedures (names that keep the code
layout's and Function slots' sort order), and the one length-dependent
p-code effect, the object-local free order (fit_frees).
"""
from __future__ import annotations

import re
import struct

from .dataimage import word
from .model import Module, Var, mod_name
from .opcodes import NAMES
from .records import PROC_POOL_NAME
from .runtime import decode


def grown(old: str, d: int):
    """Candidate names d characters longer (or shorter) than old."""
    import itertools

    from .nametable import BUILTINS, KEYWORDS
    if d > 0:
        for x in "xyzqwjk":
            yield old + x * d
    n = len(old) + d
    if d < 0 and n >= len(old[0]):
        yield old[:n]
    rest = "abcdefghijklmnopqrstuvwxyz0123456789"
    for c in "vabcdefghijklmnopqrstuwxyz":
        for t in itertools.product(rest, repeat=n - 1):
            if (s := c + "".join(t)) not in KEYWORDS and s not in BUILTINS and s not in ("b", "bf"):
                yield s  # (`B` also enters `BF` in the name table)


TYPE_TAG = {"integer": "Int", "long": "Lng", "single": "Sng", "double": "Dbl", "currency": "Cur",
            "string": "Str", "variant": "Var"}
SUFFIX_TAG = {"%": "Int", "&": "Lng", "!": "Sng", "#": "Dbl", "@": "Cur", "$": "Str"}


def code_part(line: str) -> str:
    """The line without string literals' contents and without its comment."""
    out, q = [], False
    for ch in line:
        if ch == '"':
            q = not q
            out.append(ch)
        elif q:
            out.append(" ")
        elif ch == "'":
            break
        else:
            out.append(ch)
    return "".join(out)


def on_code(line: str, fn) -> str:
    """Apply fn to the code parts of a line (outside strings and the comment)."""
    parts, cur, q = [], "", False
    for i, ch in enumerate(line):
        if ch == '"':
            if not q:
                parts.append((True, cur))
                cur = ch
            else:
                parts.append((False, cur + ch))
                cur = ""
            q = not q
            continue
        if not q and ch == "'":
            parts.append((True, cur))
            return "".join(fn(t) if code else t for code, t in parts) + line[i:]
        cur += ch
    parts.append((not q, cur))
    return "".join(fn(t) if code else t for code, t in parts)


def proc_ranges(lines: list[str]) -> list[tuple[int, int]]:
    """[start, end) line ranges of the procedures."""
    out, start = [], None
    for i, ln in enumerate(lines):
        if re.match(r"(Static\s+)?(Sub|Function)\s", ln):
            start = i
        elif start is not None and re.match(r"End (Sub|Function)\b", ln):
            out.append((start, i + 1))
            start = None
    return out


def sort_name(lo: str, hi: str | None, kind: str, taken: set[str]) -> str:
    """A general procedure's name: a key of letters and digits + its kind
    (`aProc`, `b0Func`), sorting (case-insensitively) strictly between its
    neighbours lo and hi, as code layout and Function slots require. The key
    is the shortest prefix of lo or hi followed by one character (0-9, a-z,
    in that order) that fits: `aProc` after nothing, `getsuFunc` between
    `GetSubMenu` and `GetSystemMenu`, `a0Proc` before `A_Click`. An
    identifier starts with a letter."""
    low, high = lo.lower(), (hi or "").lower()
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    for i in range(max(len(low), len(high)) + 1):
        for prefix in dict.fromkeys((low[:i], high[:i])):  # a prefix of either neighbour
            if len(prefix) < i:
                continue
            for c in chars if i else chars[10:]:  # (an identifier starts with a letter)
                name = f"{prefix}{c}{kind}"
                key = name.lower()
                if key > low and (hi is None or key < high) and key not in taken and len(name) <= 40:
                    return name
    # the original had a name here, so this means hi is lo + "0..." (`X`, `X0`)
    raise ValueError(f"no procedure name sorts between {lo!r} and {hi!r}")


class NamingMixin:
    def name_module(self, m: Module) -> None:
        vars_, refs, infos = m.vars, m.refs, m.infos
        names: dict[int, str] = {}
        for it in m.items:
            if it[0] in ("dim", "const"):
                names[it[1]] = f"K{it[1]:X}" if it[0] == "const" else mod_name(it[1])
            elif it[0] == "newobj":
                names[it[1]] = f"G{it[3]:X}" if m.kind == "bas" else mod_name(it[1])
                self.global_name[it[3]] = names[it[1]]
        gconst = {}  # (type, literal) -> Global Const names (each copy slot needs its own)
        for mm in self.all_mods:
            for it in mm.items:
                if it[0] == "global" and it[2]:
                    gconst.setdefault((it[4], it[2]), []).append(it[5])
        used_g: set = set()
        for s, v in sorted(vars_.items()):
            if s in names:
                continue
            if v.glob is not None and v.glob in self.global_name:
                names[s] = self.global_name[v.glob]
                continue
            lit, gname = None, None
            if v.scope == "MOD" and s >= m.first_owned and not v.stored and not v.array:
                for t in ([v.type()] if v.votes else []) + ["L", "I"]:  # untyped: shared Long/String handler
                    lit = self.inline_const(m.image, s, t, 16, zero=True)
                    free = [g for g in gconst.get((t, lit), []) if g not in used_g] if lit else []
                    if free:
                        gname = free[0]
                        used_g.add(gname)
                        v.copy_type = t
                        break
                if not gname:
                    lit = self.inline_const(m.image, s, v.type(), 16)
            if gname:
                # a Global Const used here: a slot with a copy of its value, at first use
                names[s] = gname
            elif lit and len(v.procs) > 1:
                names[s] = f"K{s:X}"
            elif lit:
                names[s] = f"K{s:X}"  # Const inside a procedure
            elif v.scope == "GLB":
                names[s] = self.global_name.get(self.value(m.image, s, False), f"g{s:X}")
            elif s not in names:
                pre = "s" if v.scope == "MOD" else "p" if v.scope == "REF" else "v"
                names[s] = f"{pre}{s:X}"
        for s, (_, n) in refs.items():
            names[s] = n
        start = word(self.res.get(1, b""), 4)  # startup: 0xFFFF a form, else Sub Main's record | 1
        for info in infos:
            ev = self.events.get(info.proc.record)
            if ev:
                info.name, info.event = ev, True
            elif start != 0xFFFF and info.proc.record == start & ~1:
                info.name = "Main"
                self.proc_name[info.proc.record] = "Main"
        self.fit_names(m)
        base = m.image
        owned_all = {s for s, v in vars_.items() if v.scope in ("LOC", "REF")} | {r - 2 for r in refs} | set(refs)
        for k, info in enumerate(infos):
            top = 6 + 2 * info.argwords  # parameters lie in [6, top)
            is_param = lambda s, top=top: 6 <= self.value(base, s) < top and self.value(base, s) % 2 == 0  # noqa: E731
            mine = sorted(s for s, v in vars_.items() if v.procs and v.procs[0] == k and v.scope in ("LOC", "REF"))
            # parameters: even BP offsets >= 6 (String locals are numbered 1, 3, ...),
            # consecutive after the return value; unused ones are found by walking the slots
            ps = [s for s in mine if is_param(s)]
            if ps or info.argwords:
                lo = min(ps) if ps else (min(mine) if mine else None)
                if lo is not None:
                    while lo - 2 >= m.first_owned and lo - 2 not in owned_all and is_param(lo - 2):
                        lo -= 2
                    hi = max(ps) if ps else lo - 2
                    s2 = hi + 2
                    while s2 not in owned_all and is_param(s2) and self.value(base, s2) < self.value(base, hi):
                        hi, s2 = s2, s2 + 2
                    ps = [x for x in range(lo, hi + 2, 2) if is_param(x)]
                    for x in ps:
                        vars_.setdefault(x, Var(x, "REF", procs=[k]))
                        names.setdefault(x, f"p{x:X}")
            if info.function:
                first = min(ps) if ps else (mine[0] if mine else None)
                cand = [x for x in mine if x < first] if first is not None and ps else mine[:1]
                r = (first - 2) if ps and not cand else (cand[0] if cand else None)
                if r is not None:
                    info.ret_slot = r
                    names[r] = info.name
            info.params = ps
        for info in infos:
            self.proc_name[info.proc.record] = info.name
        for _, r in m.funcs:
            if r not in self.by_record:
                self.proc_name[r] = self.declare_name(r)
        for p in self.procs:  # calls to Declare Subs (operand: the record)
            for i in decode(self.rt, self.segs[p.segment - 1].data, p)[0] if p.segment == m.seg else []:
                if NAMES.get(i.op) == "CALL" and len(i.operand) >= 4:
                    r = struct.unpack_from("<H", i.operand, 2)[0] & 0xFFF8
                    if r not in self.by_record and self.is_declare(r):
                        self.proc_name[r] = self.declare_name(r)
        m.names = names

    def fit_names(self, m: Module) -> None:
        """Names for general procedures (not stored) that keep both orders
        the compiler derives from names: code layout (procedures sorted by
        name, case-insensitive) and Function/Declare slots (sorted too):
        see sort_name."""
        infos = m.infos
        bounds = lambda k: self.name_bounds(m, k)

        taken = {n.lower() for n in self.proc_name.values()} | {x.name.lower() for x in infos if x.name}

        self.pool_names = getattr(self, "pool_names", {})  # pool offset -> name (one entry per name)
        for k, info in enumerate(infos):  # a name another module already entered in the pool
            shared = self.pool_names.get(word(self.table, info.proc.record + PROC_POOL_NAME))
            if not info.name and shared:
                lo, hi = bounds(k)
                if shared.lower() > lo.lower() and (hi is None or shared.lower() < hi.lower()):
                    info.name = shared
                    self.proc_name[info.proc.record] = shared
        from .nametable import BUILTINS, KEYWORDS
        taken |= KEYWORDS | BUILTINS | {x.lower() for x in self.global_name.values()} | \
            {n.lower() for f in self.forms for n in f if n}
        for k, info in enumerate(infos):  # in layout order: each name fits between its neighbours
            if not info.name:
                info.name = sort_name(*bounds(k), "Func" if info.function else "Proc", taken)
                self.proc_name[info.proc.record] = info.name
                taken.add(info.name.lower())
        for info in infos:
            if info.name and info.proc.record not in self.events:
                self.pool_names.setdefault(word(self.table, info.proc.record + PROC_POOL_NAME), info.name)

    def name_bounds(self, m: Module, k: int) -> tuple[str, str | None]:
        """(lo, hi): the names procedure k of module m must sort between (code
        layout and Function/Declare slots are both sorted by name)."""
        infos = m.infos
        slot_order = [r for _, r in m.funcs]
        fixed = {r: self.declare_name(r) for r in slot_order if r not in self.by_record}
        los = [x.name for x in infos[:k] if x.name]
        his = [x.name for x in infos[k + 1:] if x.name]
        r = infos[k].proc.record
        if r in slot_order:
            j = slot_order.index(r)
            los += [fixed.get(x) or self.proc_name.get(x, "") for x in slot_order[:j]]
            his += [fixed[x] for x in slot_order[j + 1:] if x in fixed]
        return max(los, key=str.lower, default=""), min(his, key=str.lower, default=None)

    def fit_frees(self, m: Module) -> None:
        """Pad local names so that each procedure frees its object/Type locals
        in the original order. The epilogue walks the IDE's local symbol
        table: 8 buckets in order, each in declaration order; the bucket is
        (name-table offset >> 1) & 7, and offsets follow from the lengths
        and first-appearance order of all earlier names (nametable.py)."""
        from .nametable import FIRST, identifiers
        base, vars_, names = m.image, m.vars, m.names
        from .nametable import BUILTINS, KEYWORDS
        taken = KEYWORDS | BUILTINS | self.project_names()
        for info in self.text_order(m):
            frees = [struct.unpack_from("<h", i.operand)[0] for i in info.insns if NAMES.get(i.op) == "OBJ_FREE"]
            if len(frees) < 2:
                continue
            k = m.infos.index(info)
            local = {s for s, v in vars_.items() if v.procs and v.procs[0] == k and v.scope == "LOC"
                     and s not in info.params and s != info.ret_slot and s in names and self.generated(names[s])}
            bp = {self.value(base, s): names[s].lower() for s in local}
            if not all(f in bp for f in frees):
                continue
            target = [bp[f] for f in frees]
            ids = list(identifiers("\r\n".join(m.lines)).items())
            pos = {low: j for j, (low, _) in enumerate(ids)}
            if not all(t in pos for t in target):
                continue
            adj = sorted(pos[names[s].lower()] for s in local if names[s].lower() in pos)
            starts = [FIRST]
            for _, sp in ids:
                starts.append(starts[-1] + len(sp) + 4)

            def ok(offs: dict) -> bool:
                have = [t for t in target if t in offs]
                return sorted(have, key=lambda t: ((offs[t] >> 1) & 7, pos[t])) == have

            def dfs(j: int, pads: list) -> list | None:
                # offsets are known up to the next adjustable name (or the end)
                hi = adj[j] if j < len(adj) else len(ids)
                offs = {t: starts[pos[t]] + sum(p for a, p in zip(adj, pads) if a < pos[t])
                        for t in target if pos[t] <= hi}
                if not ok(offs):
                    return None
                if j == len(adj):
                    return pads
                cur = len(ids[adj[j]][1])  # try lengths 1.., nearest to the current one first
                for p in sorted(range(1 - cur, 17), key=lambda p: (abs(p), p)):
                    r = dfs(j + 1, pads + [p])
                    if r is not None:
                        return r
                return None

            pads = dfs(0, [])
            if not pads or not any(pads):
                continue
            taken |= {low for low, _ in ids}
            for j, p in zip(adj, pads):
                if p:
                    old = ids[j][1]
                    new = next(c for c in grown(old, p) if c.lower() not in taken)
                    taken.add(new.lower())
                    self.rename(m, next(s for s in local if names[s].lower() == old.lower()), new)

    def project_names(self) -> set[str]:
        """Names visible in every module: globals, procedures, forms."""
        return {x.lower() for x in self.global_name.values()} | \
            {x.lower() for x in self.proc_name.values() if x} | {f[0].lower() for f in self.forms}

    def generated(self, name: str) -> bool:
        """A name the decompiler made up (free to resize)."""
        return bool(re.fullmatch(r"[vmgfsKGL][0-9A-Fa-f]+", name)) or name.lower() in getattr(self, "pretty", ())

    def prettify(self, mods: list[Module]) -> None:
        """Readable names for the slot-numbered ones the passes use (`v1C`,
        `m2A`, `G6`, `T10`, ...): kind + type + a counter, e.g. `int1`,
        `mStr2`, `gVar1`, `Type1`. Names on a `ReDim ... As` line keep their
        length (the `As` column is compiled), so those stay."""
        words = {w.lower() for mm in mods for ln in mm.lines for w in re.findall(r"[A-Za-z_]\w*", code_part(ln))}
        taken = set(words)
        keep = {w.lower() for mm in mods for ln in mm.lines if re.search(r"\bReDim\b.*\bAs\b", ln)
                for w in re.findall(r"[A-Za-z_]\w*", code_part(ln))}
        self.pretty = set()
        udts = {mt.group(1).lower() for mm in mods for ln in mm.lines if (mt := re.match(r"Type\s+(\w+)", ln))}

        def fresh(base: str, counters: dict, scope_taken: set) -> str:
            while True:
                counters[base] = counters.get(base, 0) + 1
                c = f"{base}{counters[base]}"
                if c.lower() not in taken and c.lower() not in scope_taken:
                    scope_taken.add(c.lower())
                    self.pretty.add(c.lower())
                    return c

        def kind_of(name: str, lines: list[str]) -> tuple[str, bool]:
            """(type tag, array) from the name's declaration or suffix."""
            text = "\n".join(code_part(ln) for ln in lines)
            d = re.search(rf"\b{name}\b(\()?[^,\n=]*?\bAs\s+(?:New\s+)?(\w+)", text)
            if d:
                t = d.group(2)
                rec = re.fullmatch(r"Type\d+|T[0-9A-F]+", t) or t.lower() in udts
                return TYPE_TAG.get(t.lower(), "Rec" if rec else "Obj"), bool(d.group(1))
            sfx = re.search(rf"\b{name}([%&!#@$])", text)
            return (SUFFIX_TAG[sfx.group(1)] if sfx else "Var"), bool(re.search(rf"\b{name}\(", text))

        def rename_all(mapping: dict, targets: list[tuple[dict, int, int]]) -> None:
            if not mapping:
                return
            pat = re.compile(r"(?<![\w.])(" + "|".join(map(re.escape, mapping)) + r")\b|(?<=\.)(F[0-9A-F]+)\b")
            sub = lambda mt: mapping.get(mt.group(0), mt.group(0))
            for mm, a, b in targets:
                mm.lines[a:b] = [on_code(ln, lambda t: pat.sub(sub, t)) for ln in mm.lines[a:b]]

        # project-wide: Globals, Global Consts, Types and fields (declared in .bas modules)
        gmap, counters, seen = {}, {}, set()
        for mm in mods:
            in_type = False
            for ln in mm.lines:
                c = code_part(ln)
                if mt := re.match(r"Type\s+(T[0-9A-F]+)\b", c):
                    in_type, fcount = True, {}
                    if mt.group(1).lower() not in keep:
                        gmap[mt.group(1)] = fresh("Type", counters, seen)
                    continue
                if in_type:
                    if c.strip().startswith("End Type"):
                        in_type = False
                    elif (mt := re.match(r"\s*(F[0-9A-F]+)\b", c)) and mt.group(1).lower() not in keep:
                        gmap[mt.group(1)] = fresh("f", fcount, set())
                    continue
                if mt := re.match(r"Global\s+(Const\s+)?(.*)", c):
                    for n in re.findall(r"(?:^|,)\s*([Gg][0-9A-F]+)\b", mt.group(2)):
                        if n not in gmap and n.lower() not in keep:
                            t, arr = kind_of(n, mm.lines)
                            base = "GCONST" if mt.group(1) else "g" + ("Arr" if arr else "") + t
                            gmap[n] = fresh(base, counters, seen)
        rename_all(gmap, [(mm, 0, len(mm.lines)) for mm in mods])
        for g, n in list(self.global_name.items()):
            self.global_name[g] = gmap.get(n, n)
        for mm in mods:
            for sl, n in list(mm.names.items()):
                mm.names[sl] = gmap.get(n, n)
        # per module: module variables and Consts; per procedure: locals, parameters, Statics
        for mm in mods:
            mmap, counters, seen = {}, {}, set()
            procs = proc_ranges(mm.lines)
            head = mm.lines[:procs[0][0]] if procs else mm.lines
            code = "\n".join(map(code_part, mm.lines))
            for n in dict.fromkeys(re.findall(r"\b(m[0-9A-F]+|m0E|K[0-9A-F]+)\b", code)):
                if n.lower() in keep:
                    continue
                if n.startswith("K"):
                    mmap[n] = fresh("CONST", counters, seen)
                else:
                    t, arr = kind_of(n, head)
                    mmap[n] = fresh("m" + ("Arr" if arr else "") + t, counters, seen)
            rename_all(mmap, [(mm, 0, len(mm.lines))])
            lmap_all = dict(mmap)
            for a, b in procs:
                lmap, counters, seen = {}, {}, set()
                body = mm.lines[a:b]
                for n in dict.fromkeys(re.findall(r"\b([vpsf][0-9A-F]+)\b", "\n".join(map(code_part, body)))):
                    if n.lower() in keep:
                        continue
                    t, arr = kind_of(n, body)
                    base = {"v": "", "p": "arg", "s": "st", "f": "unused"}[n[0]]
                    if n[0] == "f":
                        lmap[n] = fresh("unused", counters, seen)
                    elif n[0] == "v":
                        lmap[n] = fresh(("arr" + t) if arr else t.lower(), counters, seen)
                    else:
                        lmap[n] = fresh(base + ("Arr" if arr else "") + t, counters, seen)
                rename_all(lmap, [(mm, a, b)])
                lmap_all.update(lmap)
            for sl, n in list(mm.names.items()):
                mm.names[sl] = lmap_all.get(n, n)

    def rename(self, m: Module, slot: int, new: str) -> None:
        old = m.names[slot]
        m.names[slot] = new
        pat = re.compile(rf"(?<![\w.]){re.escape(old)}\b", re.I)
        m.lines = [pat.sub(new, ln) for ln in m.lines]

    def fit_columns(self, m: Module) -> None:
        """Rename arrays whose `ReDim ... As` lands in the wrong column to a
        name of the length that puts it right."""
        by_name = {n.lower(): x for x, n in m.names.items()}
        for target, extra in self.col_fixes:
            # the variables in `a(i, j)`, the array first; each keeps at least 1 character
            words = list(dict.fromkeys(w for w in re.findall(r"[A-Za-z]\w*", target) if w.lower() in by_name))
            sizes = {w: len(w) for w in words}
            for w in words:
                cut = extra if extra < 0 else min(extra, sizes[w] - 1)
                sizes[w] -= cut
                extra -= cut
                if extra == 0:
                    break
            if extra:
                continue
            taken = {n.lower() for mm in self.all_mods for n in mm.names.values()} | \
                {n.lower() for n in self.proc_name.values() if n}
            for w, size in sizes.items():
                if size == len(w):
                    continue
                new = next(n for c in "abcdefghijklmnopqrstuvwxyz" for k in range(10 ** (size - 1))
                           if (n := c + (str(k).zfill(size - 1) if size > 1 else "")) not in taken)
                taken.add(new)
                m.names[by_name[w.lower()]] = new
                by_name[new] = by_name[w.lower()]
        self.col_fixes = []
