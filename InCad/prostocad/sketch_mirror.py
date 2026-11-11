"""Sketch Mirror. The first click takes a shape's whole outline, Cmd, Ctrl or Shift takes more or lets one go; the next plain
click picks the mirror: a line (one of the shapes' own too), the X or Y axis or the origin; or two clicks, on points or anywhere,
draw a new construction line that is the mirror. The copy is made, chosen, and the tool ends. What was chosen before the tool comes along. The copy is held by symmetry alone: it follows the original
and brings none of its dimensions. Break Mirror Link, on the copy, frees it and gives it the original's own relations."""
import math

from PySide6 import QtCore, QtGui

from prostocad import log

P = QtCore.QPointF
TINT, SHARE = "#20c0a8", 0.35  # a mirror copy's lines lean this way
ASK_SHAPES = "Mirror  Click the shapes  ·  Cmd+click: more, or let one go"
ASK_LINE = "Mirror  Click the mirror line or an axis, or two points for a new line  ·  Cmd+click: more shapes"
ASK_END = "Mirror  Click the new line's second point  ·  Esc: not this line"


class MirrorTools:
    """Mixed into SketchView."""

    def start_mirror(self):
        items = [("line", i) for i in sorted(self.sel_lines)] + [("arc", i) for i in sorted(self.sel_arcs)] \
            + [("ell", i) for i in sorted(self.sel_ells)] + [("spl", i) for i in sorted(self.sel_spls)] \
            + [("point", k) for k in sorted(self.sel_pts & {k for t in self.ties if t["kind"] == "sk_point" for k in t["pts"]})]
        self._end_chain()
        self._rect_reset()
        self.tool = "sk_mirror"
        self.just_done = False
        self.mir = dict(items=items, hover=None, axis=None, start=None, end=None, snap=None)
        self._clear_choice()
        self.setFocus()
        self.prompt.emit(ASK_LINE if items else ASK_SHAPES)
        self._refresh()

    def _mir_shape(self, s):
        """The shapes a click here takes: a side stands for its outline; None."""
        what = self._pick(s, ("point", "line", "arc", "ell", "spl"))
        if what is None:
            return None
        if what[0] == "point":
            points = {k for t in self.ties if t["kind"] == "sk_point" for k in t["pts"]}
            if what[1] in points:
                return [("point", what[1])]
            what = self._pick(s, ("line", "arc", "ell", "spl"))
            if what is None:
                return None
        return list(self._off_outline(tuple(what))["items"])

    def _mir_axis_at(self, s):
        """The mirror at the screen point s: a line, the origin, an axis; None. A point starts a new line instead."""
        at = self._dim_place_at(s)
        if at is not None and at[0] == "origin":
            return tuple(at)
        if self._pick(s, ("point",)) is not None:
            return None
        what = self._pick(s, ("line",))
        if what is not None:  # on a line it is that line, its midpoint too
            return tuple(what)
        hint = self._candidates(self.to_model(s))
        if hint is not None and hint["kind"] != "on":
            return None  # a magnet here starts a new line
        return tuple(at) if at is not None else None

    def _mir_place(self, s):
        """(a point under the pointer or None, where it lands): the new line takes the magnets, as any line does. Its second
        end also goes level or upright with the first."""
        m = self.to_model(s)
        what = self._pick(s, ("point",))
        if what is not None:
            return what[1], self.pts[what[1]]
        hint = self._candidates(m)
        if hint is not None and hint["kind"] != "on":
            return (hint["ref"] if hint["kind"] == "point" and isinstance(hint["ref"], int) else None), P(hint["at"])
        o = self.mir
        if o["start"] is not None:
            tol, a = 10.0 / self.scale, o["start"][1]
            if abs(m.x() - a.x()) <= tol:
                return None, P(a.x(), m.y())
            if abs(m.y() - a.y()) <= tol:
                return None, P(m.x(), a.y())
        return None, m

    def _mir_points(self):
        """(the points of mirror copies, those of their originals)."""
        copies, originals = set(), set()
        for t in self.ties:
            if t.get("mirror"):
                pair = (t["pts"][0], t["pts"][1]) if t["kind"] == "con_eq_r" else (t["pts"][0],)
                twin = (t["pts"][2], t["pts"][3]) if t["kind"] == "con_eq_r" else (t["pts"][1],)
                originals.update(pair)
                copies.update(twin)
        return copies, originals

    def _mir_ink(self, ink):
        tint = QtGui.QColor(TINT)
        mix = lambda a, b: round(a * (1 - SHARE) + b * SHARE)
        return QtGui.QColor(mix(ink.red(), tint.red()), mix(ink.green(), tint.green()), mix(ink.blue(), tint.blue()), ink.alpha())

    def _mir_group(self, start):
        """The points that move together with these, mirror links left out."""
        keep, self.ties = self.ties, [t for t in self.ties if not t.get("mirror")]
        try:
            rels = self._rels()
        finally:
            self.ties = keep
        groups = [set(r.points) for r in rels if isinstance(r.points, tuple)]
        groups += [set(q) for q in (*self.lines, *self.arcs, *self.ells)] + [set(q[1]) for q in self.spls]
        got, grown = set(start), True
        while grown:
            grown = False
            for g in groups:
                if g & got and not g <= got:
                    got |= g
                    grown = True
        return got

    def _mir_bound(self, pts, rels, stay):
        """The points a pull cannot reach, with a mirror line standing as a wall: what is only joined through it stays
        exactly where it is. Without this a mirror link let the whole sketch shift a little on every pull."""
        if not any(t.get("mirror") for t in self.ties):
            return set()
        walls = set(stay)
        groups = [set(q.points) for q in rels] + [set(q) for q in (*self.lines, *self.arcs, *self.ells)] + [set(q[1]) for q in self.spls]
        got, grown = set(pts), True
        while grown:
            grown = False
            for g in groups:
                if g & (got - walls) and not g <= got:
                    got |= g
                    grown = True
        return (set(range(len(self.pts))) - got) | (walls - set(pts))

    def _mir_pair_cut(self, pts):
        """A mirror copy pulled: it and its original go; every other mirror link of the two is cut, so the chain stays
        where it is. Returns what was cut (put back if the pull comes to nothing)."""
        links = [t for t in self.ties if t.get("mirror")]
        if not links or not pts:
            return None
        twin_of = lambda t: t["pts"][2:] if t["kind"] == "con_eq_r" else t["pts"][1:2]
        source = lambda t: t["pts"][:2] if t["kind"] == "con_eq_r" else t["pts"][:1]
        mine = self._mir_group(pts)
        pair = [t for t in links if set(twin_of(t)) & mine]
        if not pair:
            return None
        other = self._mir_group({k for t in pair for k in source(t)})
        both = mine | other
        cut = [t for t in links if not any(t is q for q in pair) and (set(twin_of(t)) | set(source(t))) & both]
        if cut:
            self.ties = [t for t in self.ties if not any(t is q for q in cut)]
            log.say("mirror pair pulled:", len(cut), "other links cut")
        return cut or None

    def _mir_stay(self, pts):
        """A mirror line stays while what it mirrors is pulled; pulled itself, it takes the copy along and the original stays."""
        links = [t for t in self.ties if t.get("mirror") and t["kind"] == "con_sym"]
        axes = {k for t in links for k in t["pts"][2:]}
        stay = axes - (self._mir_group(pts) if links else set())  # an axis drawn from a shape's corner goes with that shape
        stay |= {t["pts"][0] for t in links if set(t["pts"][2:]) & set(pts)} - set(pts)
        return {k: P(self.pts[k]) for k in stay}

    def _mir_pulled(self, d, step):
        """A mirror line pulled: each copy stands where the line now throws it, exactly; None if this line mirrors nothing."""
        pair = set(d["pts"])
        links = [t for t in self.ties if t.get("mirror") and t["kind"] == "con_sym" and set(t["pts"][2:]) == pair]
        if not links:
            return None
        was = d["was"]
        a, b = [was[k] + step for k in d["pts"]]
        u = (b - a) / (math.hypot((b - a).x(), (b - a).y()) or 1.0)
        over = lambda p: (a + u * QtCore.QPointF.dotProduct(p - a, u)) * 2 - p
        out = {k: was[k] + step for k in d["pts"]}
        for t in links:
            out[t["pts"][1]] = over(was[t["pts"][0]])
        for t in self.ties:
            if t.get("mirror") and t["kind"] == "con_eq_r" and t["pts"][2] in out:
                out[t["pts"][3]] = over(was[t["pts"][1]])
        return out

    def _mir_map(self, axis):
        """(what mirrors a point, whether it turns the sketch over: a line does, a point does not)."""
        kind, i = axis
        if kind in ("line", "ends"):
            a, b = [self.pts[k] for k in self.lines[i]] if kind == "line" else i
            u = (b - a) / (math.hypot((b - a).x(), (b - a).y()) or 1.0)

            def over(p):
                foot = a + u * QtCore.QPointF.dotProduct(p - a, u)
                return foot * 2 - p
            return over, True
        if kind == "axis":
            return (lambda p: P(p.x(), -p.y())) if i == "x" else (lambda p: P(-p.x(), p.y())), True
        return (lambda p: P(-p.x(), -p.y())), False

    def _mir_tie(self, axis, k, q):
        kind, i = axis
        if kind == "line":
            self._add_tie("con_sym", [k, q, *self.lines[i]])
        elif kind == "axis":
            self._add_tie("con_sym_ax", [k, q], i)
        else:
            self._add_tie("con_sym_o", [k, q])
        self.ties[-1]["mirror"] = True

    def _mir_new_line(self, end):
        """The two clicks' construction line, and the mirror about it. One Cmd+Z takes both."""
        st = self._state()
        ends = []
        for k, at in (self.mir["start"], end):
            if k is None:
                self.pts.append(P(at))
                k = len(self.pts) - 1
            ends.append(k)
        self.constr = (self.constr + [False] * len(self.lines))[:len(self.lines)] + [True]
        self.lines.append(tuple(ends))
        self._mir_make(("line", len(self.lines) - 1), st)

    def _mir_make(self, axis, st=None):
        over, turns = self._mir_map(axis)
        items = [q for q in self.mir["items"] if q != axis]
        if not items:
            self.prompt.emit("Mirror  The mirror line alone: click the shapes first")
            return
        st = st or self._state()
        made = {}
        spare = set()  # a circle's rim has no point of its own to hold

        def twin(k, tie=True):
            """The copy of point k; on the mirror it is the point itself."""
            if k in made:
                return made[k]
            q = over(self.pts[k])
            if math.hypot((q - self.pts[k]).x(), (q - self.pts[k]).y()) < 1e-7:
                made[k] = k
                return k
            self.pts.append(q)
            made[k] = len(self.pts) - 1
            if tie:
                self._mir_tie(axis, k, made[k])
            return made[k]

        lines, arcs, ells, spls = [], [], [], []
        for kind, i in items:
            if kind == "line":
                a, b = self.lines[i]
                self.constr = (self.constr + [False] * len(self.lines))[:len(self.lines)] + [self.is_construction(i)]
                self.lines.append((twin(a), twin(b)))
                lines.append(len(self.lines) - 1)
            elif kind == "arc":
                c, a, b = self.arcs[i]
                faint = self.is_arc_construction(i)
                if a == b:
                    rim = twin(a, tie=False)
                    arc = (twin(c), rim, rim)
                    self._add_tie("con_eq_r", [c, a, arc[0], rim])
                    self.ties[-1]["mirror"] = True
                else:
                    arc = (twin(c), twin(b), twin(a)) if turns else (twin(c), twin(a), twin(b))
                self.arc_constr = (self.arc_constr + [False] * len(self.arcs))[:len(self.arcs)] + [faint]
                self.arcs.append(arc)
                arcs.append(len(self.arcs) - 1)
            elif kind == "ell":
                c, a, b = self.ells[i]
                cut, plain = self._ell_cut(i), self._ell_plain(i)
                self.ells.append((twin(c), twin(a), twin(b)))
                self.ell_constr = (self.ell_constr + [False] * len(self.ells))[:len(self.ells) - 1] + [bool(self.ell_constr[i:i + 1] == [True])]
                ells.append(len(self.ells) - 1)
                if plain:
                    self._add_tie("con_perp", [made[c], made[a], made[c], made[b]])
                if cut:
                    s_, e = twin(cut[0]), twin(cut[1])
                    self._add_tie("ell_cut", [e, s_, made[c], made[a], made[b]] if turns else [s_, e, made[c], made[a], made[b]])
            elif kind == "spl":
                form, pts, closed = self.spls[i]
                self.spls.append((form, tuple(twin(k) for k in pts), closed))
                self.spl_constr = (self.spl_constr + [False] * len(self.spls))[:len(self.spls) - 1] + [bool(self.spl_constr[i:i + 1] == [True])]
                self.spl_comb = (self.spl_comb + [False] * len(self.spls))[:len(self.spls)]
                spls.append(len(self.spls) - 1)
            elif kind == "point":
                self._add_tie("sk_point", [twin(i)])
        self.steps.append(("state", st, len(self.lines), len(self.arcs)))
        log.say("mirror", len(items), "shapes about", axis)
        self.stop_tool()
        self.just_done = True
        self.sel_lines, self.sel_arcs, self.sel_ells, self.sel_spls = set(lines), set(arcs), set(ells), set(spls)
        self.changed.emit()
        self._refresh()

    def mirror_press(self, s, adding):
        o = self.mir
        if adding or not o["items"]:
            got = self._mir_shape(s)
            if got is None:
                return
            if adding and all(q in o["items"] for q in got):
                o["items"] = [q for q in o["items"] if q not in got]
            else:
                o["items"] += [q for q in got if q not in o["items"]]
            self.prompt.emit(ASK_LINE if o["items"] else ASK_SHAPES)
            return
        if o["start"] is not None:
            end = self._mir_place(s)
            if end[0] == o["start"][0] and end[0] is not None or abs((end[1] - o["start"][1]).x()) + abs((end[1] - o["start"][1]).y()) < 1e-6:
                return
            self._mir_new_line(end)
            return
        axis = self._mir_axis_at(s)
        if axis is None:
            o["start"] = self._mir_place(s)
            self.prompt.emit(ASK_END)
            return
        self._mir_make(axis)

    def mirror_back(self):
        """Esc on the new line: only that goes; True if there was one."""
        if self.mir is None or self.mir["start"] is None:
            return False
        self.mir["start"], self.mir["end"] = None, None
        self.prompt.emit(ASK_LINE)
        self.update()
        return True

    def _mir_track(self, s):
        o = self.mir
        o["hover"], o["axis"], o["end"], o["snap"] = None, None, None, None
        if o["start"] is not None:
            o["end"] = o["snap"] = self._mir_place(s)[1]
        elif o["items"]:
            o["axis"] = self._mir_axis_at(s)
            if o["axis"] is None:
                o["snap"] = self._mir_place(s)[1]
        else:
            o["hover"] = self._mir_shape(s)

    def _mir_lines(self, item):
        if item[0] == "point":
            return [self.pts[item[1]]]
        return self._off_seg(item, True)["poly"]

    def _paint_mirror(self, g):
        o = self.mir
        if o is None:
            return
        g.setBrush(QtCore.Qt.NoBrush)
        g.setPen(QtGui.QPen(QtGui.QColor(self.p["snap"]), 2.6))
        for item in o["items"] + (o["hover"] or []):
            g.drawPolyline(QtGui.QPolygonF([self.to_screen(q) for q in self._mir_lines(item)]))
        if o["snap"] is not None:
            g.drawEllipse(self.to_screen(o["snap"]), 5, 5)
        axis = o["axis"]
        if o["start"] is not None and o["end"] is not None and (o["end"] - o["start"][1]).manhattanLength() > 1e-6:
            axis = ("ends", (o["start"][1], o["end"]))
            g.drawLine(self.to_screen(axis[1][0]), self.to_screen(axis[1][1]))
        if axis is None:
            return
        if axis[0] == "line":
            a, b = self.lines[axis[1]]
            g.drawLine(self.to_screen(self.pts[a]), self.to_screen(self.pts[b]))
        over = self._mir_map(axis)[0]
        g.setPen(QtGui.QPen(QtGui.QColor(self.p["snap"]), 1.6, QtCore.Qt.DashLine))
        for item in o["items"]:
            if item != axis:
                g.drawPolyline(QtGui.QPolygonF([self.to_screen(over(q)) for q in self._mir_lines(item)]))

    # ---- Break Mirror Link

    def _mir_chosen_links(self):
        """The mirror ties that hold the chosen copy."""
        points = {k for i in self.sel_lines for k in self.lines[i]} | {k for i in self.sel_arcs for k in self.arcs[i]} \
            | {k for i in self.sel_ells for k in self.ells[i]} | {k for i in self.sel_spls for k in self.spls[i][1]} | set(self.sel_pts)
        return [t for t in self.ties if t.get("mirror") and t["pts"][1 if t["kind"] != "con_eq_r" else 2] in points]

    def can_unmirror(self):
        return bool(self._mir_chosen_links())

    def break_mirror(self):
        """The copy is free where it stands, and takes the original's own relations (never its dimensions)."""
        links = self._mir_chosen_links()
        if not links:
            return
        st = self._state()
        pairs = {t["pts"][0]: t["pts"][1] for t in links if t["kind"] != "con_eq_r"}
        rims = {t["pts"][1]: t["pts"][3] for t in links if t["kind"] == "con_eq_r"}
        centres = {t["pts"][0]: t["pts"][2] for t in links if t["kind"] == "con_eq_r"}
        pairs.update(centres)
        pairs.update(rims)
        self.ties = [t for t in self.ties if not any(t is q for q in links)]
        for t in list(self.ties):
            if t["kind"].startswith("con_") and not t.get("mirror") and all(k in pairs for k in t["pts"]):
                self._add_tie(t["kind"], [pairs[k] for k in t["pts"]], t["ref"])
        lines = {frozenset(ln): i for i, ln in enumerate(self.lines)}
        twin = {i: lines.get(frozenset(pairs[k] for k in ln)) for i, ln in enumerate(self.lines) if all(k in pairs for k in ln)}
        for kind, line, end, ref in list(self.relations):
            new = twin.get(line)
            if new is None:
                continue
            if kind in ("par", "perp") and ref is not None:
                if ref in twin and twin[ref] is not None:
                    self.relations.append((kind, new, end, twin[ref]))
            elif kind in ("h", "v"):
                a, b = [self.pts[k] for k in self.lines[new]]
                if abs((b - a).y() if kind == "h" else (b - a).x()) < 1e-9:
                    self.relations.append((kind, new, end, ref))
        self.steps.append(("state", st, len(self.lines), len(self.arcs)))
        log.say("break mirror link", len(links))
        self.changed.emit()
        self._refresh()
