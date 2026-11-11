"""Trim and Extend, as a part of the sketch view. Trim: the piece under the pointer between its nearest crossings is lit,
a click takes it away; with nothing crossing, the whole shape goes. Extend: a click on a shape near an end, the shapes
under the pointer light up, a click on one takes the end there, or to its continuation. Space goes from one to the other."""
import math

from PySide6 import QtCore, QtGui

from prostocad import log, sketch_solve as sk

P = QtCore.QPointF
TAU = 2 * math.pi
EPS = 1e-6
TRIM_TOOLS = ("trim", "extend")
FAR = 1e5  # how far a line is carried on
SHAPES = ("line", "arc", "ell", "spl")
RIM_TIES = ("dim_r", "dim_d", "con_eq_r", "con_circles", "con_touch", "con_dist", "con_apart", "con_axis", "con_ell_arc")  # about a radius


def _len(p):
    return math.hypot(p.x(), p.y())


def _ang(p):
    return math.atan2(p.y(), p.x())


def _dot(a, b):
    return a.x() * b.x() + a.y() * b.y()


def _cross(a, b):
    return a.x() * b.y() - a.y() * b.x()


def _at(c, u, v, t):
    return c + u * math.cos(t) + v * math.sin(t)


def _back(c, u, v, p):
    """p in the ellipse's frame, where it is the unit circle."""
    det = _cross(u, v)
    d = p - c
    return P(_cross(d, v) / det, _cross(u, d) / det)


def _in_sweep(a, a0, sw, eps=1e-7):
    return (a - a0) % TAU <= sw + eps or (a - a0) % TAU >= TAU - eps


def _seg_seg(a, b, c, d, eps=1e-7):
    """Where the segments a-b and c-d meet; ends touching count."""
    u, w = b - a, d - c
    den = _cross(u, w)
    if abs(den) < 1e-12:
        return []
    t, s = _cross(c - a, w) / den, _cross(c - a, u) / den
    lt, ls = eps / max(_len(u), 1e-9), eps / max(_len(w), 1e-9)
    return [a + u * t] if -lt <= t <= 1 + lt and -ls <= s <= 1 + ls else []


def _seg_round(a, b, c, r):
    """Where the segment a-b meets the circle (c, r)."""
    d, f = b - a, a - c
    A, B, C = _dot(d, d), 2 * _dot(f, d), _dot(f, f) - r * r
    disc = B * B - 4 * A * C
    if A < 1e-18 or disc < -1e-9:
        return []
    root = math.sqrt(max(disc, 0.0))
    lt = 1e-7 / math.sqrt(A)
    return [a + d * t for t in ((-B - root) / (2 * A), (-B + root) / (2 * A)) if -lt <= t <= 1 + lt]


def _round_round(c1, r1, c2, r2):
    d = _len(c2 - c1)
    if d < 1e-12 or d > r1 + r2 + 1e-7 or d < abs(r1 - r2) - 1e-7:
        return []
    a = (d * d + r1 * r1 - r2 * r2) / (2 * d)
    h = math.sqrt(max(r1 * r1 - a * a, 0.0))
    w = (c2 - c1) / d
    mid = c1 + w * a
    n = P(-w.y(), w.x())
    return [mid + n * h, mid - n * h]


def _poly(g, n=180):
    """Places along a shape's geometry."""
    if g[0] == "seg":
        return [g[1], g[2]]
    if g[0] == "arc":
        _k, c, r, a0, sw = g
        return [c + P(math.cos(a0 + sw * k / n), math.sin(a0 + sw * k / n)) * r for k in range(n + 1)]
    if g[0] == "ell":
        _k, c, u, v, t0, sw = g
        return [_at(c, u, v, t0 + sw * k / n) for k in range(n + 1)]
    return g[1]


def _meets(g1, g2):
    """Where two shapes' geometries meet."""
    if g2[0] == "seg" and g1[0] != "seg":
        g1, g2 = g2, g1
    if g1[0] == "arc" and g2[0] == "ell":
        g1, g2 = g2, g1
    k1, k2 = g1[0], g2[0]
    if k1 == "seg" and k2 == "seg":
        return _seg_seg(g1[1], g1[2], g2[1], g2[2])
    if k1 == "seg" and k2 == "arc":
        _k, c, r, a0, sw = g2
        return [q for q in _seg_round(g1[1], g1[2], c, r) if _in_sweep(_ang(q - c), a0, sw)]
    if k1 == "seg" and k2 == "ell":
        _k, c, u, v, t0, sw = g2
        out = []
        for q in _seg_round(_back(c, u, v, g1[1]), _back(c, u, v, g1[2]), P(0, 0), 1.0):
            if _in_sweep(_ang(q), t0, sw):
                out.append(c + u * q.x() + v * q.y())
        return out
    if k1 == "arc" and k2 == "arc":
        return [q for q in _round_round(g1[1], g1[2], g2[1], g2[2])
                if _in_sweep(_ang(q - g1[1]), g1[3], g1[4]) and _in_sweep(_ang(q - g2[1]), g2[3], g2[4])]
    one, two, out = _poly(g1), _poly(g2), []
    boxes = [(min(p.x(), q.x()), max(p.x(), q.x()), min(p.y(), q.y()), max(p.y(), q.y()), p, q)
             for p, q in zip(two, two[1:])]
    for a, b in zip(one, one[1:]):
        x0, x1, y0, y1 = min(a.x(), b.x()), max(a.x(), b.x()), min(a.y(), b.y()), max(a.y(), b.y())
        for bx0, bx1, by0, by1, p, q in boxes:
            if bx0 <= x1 and x0 <= bx1 and by0 <= y1 and y0 <= by1:
                out += _seg_seg(a, b, p, q)
    return out


class TrimTools:
    """Mixed into SketchView."""

    def start_trim(self, kind):
        self._end_chain()
        self._rect_reset()
        self._spl_reset()
        self.tool = kind
        self.just_done = False
        self.ext = self.trim_at = self.ext_at = None
        self.setFocus()
        self._trim_prompt()
        self._refresh()

    def _trim_prompt(self, say=None):
        if self.tool == "trim":
            words = "Click the piece to take away  ·  Space: Extend  ·  right-click stops"
        elif self.ext is None:
            words = "Click a shape near the end to extend  ·  Space: Trim  ·  right-click stops"
        else:
            words = "Click what it goes to, or that line's continuation  ·  right-click gives it up"
        self.prompt.emit(say if say else self._tool_name() + "  " + words)

    def trim_swap(self):
        self.tool = "extend" if self.tool == "trim" else "trim"
        self.ext = self.trim_at = self.ext_at = None
        self.tool_changed.emit("sketch." + self.tool)
        self._trim_prompt()
        self._refresh()

    def ext_cancel(self):
        self.ext = self.ext_at = None
        self._trim_prompt()
        self.update()

    # ----- shapes as geometry -----
    def _geom(self, item):
        kind, i = item
        if kind == "line":
            a, b = self.lines[i]
            return ("seg", self.pts[a], self.pts[b])
        if kind == "arc":
            c, r, a0, sw = self.arc_shape(i)
            return ("arc", c, r, a0, sw)
        if kind == "ell":
            return ("ell", *self.ell_shape(i), *self._ell_range(i))
        return ("poly", self.spl_poly(i))

    def _all_shapes(self, everything=False):
        """Dimmed construction shapes do not count, unless everything is asked for."""
        out = [("line", i) for i in range(len(self.lines))] + [("arc", i) for i in range(len(self.arcs))] \
            + [("ell", i) for i in range(len(self.ells))] + [("spl", i) for i in range(len(self.spls))]
        return out if everything else [q for q in out if not self._ghost(q)]

    def _on_geom(self, g, p, tol=1e-5):
        if g[0] == "seg":
            u = g[2] - g[1]
            n2 = _dot(u, u)
            t = max(0.0, min(1.0, _dot(p - g[1], u) / n2)) if n2 > 1e-18 else 0.0
            return _len(g[1] + u * t - p) < tol
        if g[0] == "arc":
            return abs(_len(p - g[1]) - g[2]) < tol and _in_sweep(_ang(p - g[1]), g[3], g[4])
        if g[0] == "ell":
            det = _cross(g[2], g[3])
            if abs(det) < 1e-12:
                return False
            q = _back(g[1], g[2], g[3], p)
            return abs(_len(q) - 1) < tol / max(min(_len(g[2]), _len(g[3])), 1e-9) and _in_sweep(_ang(q), g[4], g[5])
        return any(_len(a - p) < tol + _len(b - a) and abs(_cross(b - a, p - a)) / max(_len(b - a), 1e-9) < tol
                   for a, b in zip(g[1], g[1][1:]))

    def _place_on(self, item, g, p):
        """The place of p along the shape: 0…1 on a line, the angle from its start on the others."""
        if g[0] == "seg":
            u = g[2] - g[1]
            return _dot(p - g[1], u) / _dot(u, u)
        if g[0] == "arc":
            return (_ang(p - g[1]) - g[3]) % TAU
        if g[0] == "ell":
            q = _back(g[1], g[2], g[3], p)
            return (_ang(q) - g[4]) % TAU
        return 0.0

    def _at_place(self, g, u):
        if g[0] == "seg":
            return g[1] + (g[2] - g[1]) * u
        if g[0] == "arc":
            return g[1] + P(math.cos(g[3] + u), math.sin(g[3] + u)) * g[2]
        return _at(g[1], g[2], g[3], g[4] + u)

    def _nearest_place(self, g, m):
        if g[0] == "seg":
            return max(0.0, min(1.0, self._place_on(None, g, m)))
        if g[0] == "arc":
            u = (_ang(m - g[1]) - g[3]) % TAU
            return u if u <= g[4] else (0.0 if TAU - u < u - g[4] else g[4])
        u = (_ang(_back(g[1], g[2], g[3], m)) - g[4]) % TAU
        return u if u <= g[5] else (0.0 if TAU - u < u - g[5] else g[5])

    # ----- Trim -----
    def _trim_piece(self, item, m):
        """What a click at m takes away: dict(item, whole) or dict(item, lo, hi, closed, cuts, g), cuts {place: (p, what)}."""
        g = self._geom(item)
        if g[0] == "poly":
            hit = any(_meets(g, self._geom(o)) for o in self._all_shapes() if o != item)
            return dict(item=item, whole=not hit, spline=hit, g=g)
        closed = (g[0] == "arc" and g[4] >= TAU - 1e-9) or (g[0] == "ell" and g[5] >= TAU - 1e-9)
        whole_len = 1.0 if g[0] == "seg" else g[-1]
        cuts = {}
        for other in self._all_shapes():
            if other == item:
                continue
            for p in _meets(g, self._geom(other)):
                u = self._place_on(item, g, p)
                if not closed and g[0] != "seg" and u >= TAU - 1e-7:
                    continue  # the very start, come round
                if closed or 1e-7 < u < whole_len - 1e-7:
                    key = next((k for k in cuts if abs(k - u) < 1e-7), u)
                    cuts.setdefault(key, (p, other))
        us = sorted(cuts)
        um = self._nearest_place(g, m)
        if closed:
            if len(us) < 2:
                return dict(item=item, whole=True, g=g)
            lo = max((u for u in us if u <= um), default=us[-1])
            hi = min((u for u in us if u > um), default=us[0])
        else:
            lo = max((u for u in us if u <= um), default=0.0)
            hi = min((u for u in us if u > um), default=whole_len)
            if lo == 0.0 and hi == whole_len:
                return dict(item=item, whole=True, g=g)
        return dict(item=item, whole=False, lo=lo, hi=hi, closed=closed, cuts=cuts, g=g, length=whole_len)

    def _trim_track(self, s):
        what = self._pick(s, SHAPES)
        m = self.to_model(s)
        if self.tool == "trim":
            self.trim_at = self._trim_piece(what, m) if what is not None else None
        elif self.ext is None:
            self.trim_at = None
            self.ext_at = dict(item=what) if what is not None else None
        else:
            self.ext_at = self._ext_result(m, s)

    def _end_at(self, p, other):
        """The point a cut ends on: one standing there already, or a new one held on what cut it."""
        n = len(self.pts)
        k = self._point_index(p, 1e-6)
        if k == n:
            self._tie_on(k, other)
        return k

    def _tie_on(self, k, other):
        kind, i = other
        if kind == "line":
            self._add_tie("on_line", [k], i)
        elif kind == "arc":
            self._add_tie("on_arc", [k], i)
        elif kind == "ell":
            self._add_tie("con_on_ell", [k, *self.ells[i]])
        elif kind == "axis":
            self._add_tie("y0" if i == "x" else "x0", [k])

    def _trim_done(self, st):
        """Ties on points no shape uses now go with them; the rest of the sketch stays as it was."""
        used = {k for ln in self.lines for k in ln} | {k for arc in self.arcs for k in arc} | {k for e in self.ells for k in e} \
            | {k for s_ in self.spls for k in s_[1]} | {k for t in self.ties if t["kind"] in ("ell_cut", "sk_point") for k in t["pts"]}
        self.ties = [t for t in self.ties if all(k in used for k in t["pts"])]
        self._drop_lone_points()
        self.steps.append(("state", st, len(self.lines), len(self.arcs)))
        self.trim_at = None
        self.changed.emit()
        self._refresh()

    def _rim_moved(self, old, new):
        for t in self.ties:
            if t["kind"] in RIM_TIES:
                t["pts"] = [new if k == old else k for k in t["pts"]]

    def _trim_click(self, s):
        self._trim_track(s)
        got = self.trim_at
        if got is None:
            return
        kind, i = got["item"]
        if self.is_projected((kind, i)):  # as the old program: it follows the part
            self.prompt.emit("That edge is projected from the part and follows it: right-click it \u25b8 Break Link, then Trim")
            return
        if got.get("spline"):
            self.prompt.emit("Trim: a spline crossed by something cannot be cut yet")
            return
        if got["whole"]:
            self.sel_lines, self.sel_arcs, self.sel_ells, self.sel_spls = set(), set(), set(), set()
            {"line": self.sel_lines, "arc": self.sel_arcs, "ell": self.sel_ells, "spl": self.sel_spls}[kind].add(i)
            self._delete()
            self.trim_at = None
            self._refresh()
            log.say("trim: whole", kind, i)
            return
        st = self._state()
        lo, hi, g, cuts, closed, length = got["lo"], got["hi"], got["g"], got["cuts"], got["closed"], got["length"]
        log.say("trim", kind, i, "from", lo, "to", hi)
        start = None if not closed and lo == 0.0 else self._end_at(*cuts[lo])
        end = None if not closed and hi == length else self._end_at(*cuts[hi])
        {"line": self._trim_line, "arc": self._trim_arc, "ell": self._trim_ell}[kind](i, start, end, closed)
        self._trim_done(st)
        self._trim_track(s)

    def _trim_line(self, i, start, end, _closed):
        a, b = self.lines[i]
        for t in self.ties:
            if t["kind"] == "mid" and t["ref"] == i:
                t["kind"] = "on_line"  # the middle of what it was: it stays on the line
        if start is None:
            self.lines[i] = (end, b)
        elif end is None:
            self.lines[i] = (a, start)
        else:
            self.lines[i] = (a, start)
            self.lines.append((end, b))
            n = len(self.lines) - 1
            self.constr = (self.constr + [False] * len(self.lines))[:n] + [self.is_construction(i)]
            for kind, line, at_end, ref in list(self.relations):  # what held the line holds both its pieces
                if line == i:
                    self.relations.append((kind, n, at_end, line - 1 if kind == "perp" and ref is None else ref))
            self._add_tie("con_col", [a, start, end, b])

    def _trim_arc(self, i, start, end, closed):
        c, a, b = self.arcs[i]
        if closed:  # what stays runs from the end of the piece round to its start
            self.arcs[i] = (c, end, start)
            for t in self.ties:  # a circle's diameter: on the arc it reads as a radius; formulas go on using the diameter (the user)
                if t["kind"] == "dim_d" and t["pts"] == [c, a]:
                    t["kind"] = "dim_r"
                    t["as_d"] = True
                    if t.get("ref") is not None:
                        t["ref"] *= 0.5
                    if t.get("expr"):
                        t["expr"] = "(%s)/2" % t["expr"]
            self._rim_moved(a, end)
        elif start is None:
            self.arcs[i] = (c, end, b)
            self._rim_moved(a, end)
        elif end is None:
            self.arcs[i] = (c, a, start)
            self._rim_moved(b, start)
        else:
            self.arcs[i] = (c, a, start)
            self.arcs.append((c, end, b))
            n = len(self.arcs) - 1
            self.arc_constr = (self.arc_constr + [False] * len(self.arcs))[:n] + [self.is_arc_construction(i)]
            self._add_tie("con_eq_r", [c, a, c, end])
            self._rim_moved(b, start)

    def _trim_ell(self, i, start, end, closed):
        c, a, b = self.ells[i]
        tie = next((t for t in self.ties if t["kind"] == "ell_cut" and t["pts"][2:] == [c, a, b]), None)
        if closed:
            self._add_tie("ell_cut", [end, start, c, a, b])
        elif start is None:
            tie["pts"][0] = end
        elif end is None:
            tie["pts"][1] = start
        else:  # two arcs of one ellipse: the second has axes' ends of its own, held on the first's
            s_, e_ = tie["pts"][:2]
            tie["pts"] = [s_, start, c, a, b]
            self.pts.append(P(self.pts[a]))
            ia = len(self.pts) - 1
            self.pts.append(P(self.pts[b]))
            ib = len(self.pts) - 1
            self.ells.append((c, ia, ib))
            n = len(self.ells) - 1
            self.ell_constr = (self.ell_constr + [False] * len(self.ells))[:n] + [i < len(self.ell_constr) and self.ell_constr[i]]
            self._add_tie("con_same", [a, ia])
            self._add_tie("con_same", [b, ib])
            self._add_tie("ell_cut", [end, e_, c, ia, ib])

    # ----- Extend -----
    def _ext_pick(self, what, m):
        """The end to extend: of the shape at m, the nearer one. Returns a refusal as text, or None."""
        kind, i = what
        if self.is_projected(what):  # the part's: not extended (as the old program, which can't pick it)
            return None
        if kind == "spl":
            return "Extend: a spline cannot be extended yet"
        if kind == "arc" and self.is_circle(i):
            return "Extend: a circle has no end"
        if kind == "ell" and self._ell_cut(i) is None:
            return "Extend: an ellipse has no end"
        ends = self.lines[i] if kind == "line" else self.arcs[i][1:3] if kind == "arc" else self._ell_cut(i)
        k = min(ends, key=lambda q: _len(self.pts[q] - m))
        self.ext = dict(item=what, k=k, first=k == ends[0], g=self._geom(what))
        return None

    def ext_from(self, s):
        """Extend from the right-click menu: the shape there is taken at once."""
        what = self._pick(s, SHAPES)
        if what is not None:
            say = self._ext_pick(what, self.to_model(s))
            self._trim_prompt(say)

    def can_extend(self, s):
        what = self._pick(s, SHAPES)
        if what is None or what[0] == "spl" or self.is_projected(what):
            return False
        return what[0] == "line" or (what[0] == "arc" and not self.is_circle(what[1])) \
            or (what[0] == "ell" and self._ell_cut(what[1]) is not None)

    def _ext_click(self, s):
        m = self.to_model(s)
        if self.ext is None:
            what = self._pick(s, SHAPES)
            if what is not None:
                self._trim_prompt(self._ext_pick(what, m))
            return
        got = self._ext_result(m, s)
        if got is None or got.get("target") is None:
            return
        if got.get("at") is None:
            self.prompt.emit(got["why"])
            return
        self._ext_apply(got)

    def _ext_side(self, target):
        """How far a place is from the target: changes sign across it, the target carried on for ever."""
        if target[0] == "axis":
            return (lambda p: p.y()) if target[1] == "x" else (lambda p: p.x())
        g = self._geom(target)
        if g[0] == "seg":
            u = (g[2] - g[1]) / _len(g[2] - g[1])
            return lambda p: _cross(u, p - g[1])
        if g[0] == "arc":
            return lambda p: _len(p - g[1]) - g[2]
        if g[0] == "ell":
            return lambda p: _len(_back(g[1], g[2], g[3], p)) - 1.0
        return None

    def _ext_places(self, target):
        """Where the end can go on this target, nearest first along the way it goes; and the way as a function."""
        e = self.ext
        g = e["g"]
        kind = e["item"][0]
        side = self._ext_side(target)
        if kind == "line":
            far, near = (g[2], g[1]) if e["first"] else (g[1], g[2])  # near: the end extended
            d = (near - far) / _len(near - far)
            way = lambda u: near + d * u
            if target[0] == "axis" or target[0] == "line":
                a = P(0, 0) if target[0] == "axis" else self._geom(target)[1]
                u = P(1, 0) if target == ("axis", "x") else P(0, 1) if target[0] == "axis" else self._geom(target)[2] - a
                den = _cross(d, u)
                if abs(den) < 1e-9 * max(_len(u), 1e-9):
                    return None, way
                return [_cross(a - near, u) / den], way
            if target[0] == "arc":
                tg = self._geom(target)
                hits = _seg_round(near - d * FAR, near + d * FAR, tg[1], tg[2])
            elif target[0] == "ell":
                tg = self._geom(target)
                full = ("ell", tg[1], tg[2], tg[3], 0.0, TAU)
                hits = _meets(("seg", near - d * FAR, near + d * FAR), full)
            else:
                hits = _meets(("seg", near - d * FAR, near + d * FAR), self._geom(target))
            return [_dot(q - near, d) for q in hits], way
        # along its own curve: what is left of the circle or the ellipse, forward from the end
        room = TAU - g[-1]
        sign = -1.0 if e["first"] else 1.0
        at_end = 0.0 if e["first"] else g[-1]
        base = g
        way = lambda u: self._at_place(base, at_end + sign * u)
        if side is None:
            path = ("poly", [way(room * k / 240) for k in range(241)])
            hits = _meets(path, self._geom(target))
            step = [min(range(241), key=lambda k: _len(path[1][k] - q)) for q in hits]
            return [room * k / 240 for k in step], way
        out, n, was = [], 720, side(way(0.0))
        if abs(was) < 1e-9:
            was = side(way(room / n / 4))
        for k in range(1, n + 1):
            now = side(way(room * k / n))
            if abs(now) < 1e-12:  # a sample right on it
                out.append(room * k / n)
                continue
            if was * now < 0:
                lo, hi, flo = room * (k - 1) / n, room * k / n, was
                for _r in range(50):
                    mid = (lo + hi) / 2
                    fm = side(way(mid))
                    if flo * fm <= 0:
                        hi = mid
                    else:
                        lo, flo = mid, fm
                out.append((lo + hi) / 2)
            was = now
        return out, way

    def _ext_result(self, m, s):
        """What a click on the shape under the pointer would do."""
        e = self.ext
        target = self._pick(s, SHAPES)
        if target == e["item"]:
            target = None
        if target is None:
            target = self._tc_axis(s)
        if target is None:
            return dict(target=None, at=None, pointer=m)
        places, way = self._ext_places(target)
        if places is None:
            return dict(target=target, at=None, why="Extend: it runs parallel to that line, they never meet")
        fore = [u for u in places if u > 1e-7]
        if not fore:
            return dict(target=target, at=None, why="Extend: that lies behind the end; Trim shortens a line"
                        if places else "Extend: it never reaches that")
        best = min(fore, key=lambda u: _len(way(u) - m))
        return dict(target=target, at=way(best), way=way, reach=best)

    def _ext_shared(self, k, item):
        """Another shape uses this end: moving it would pull that shape along."""
        return any(k in pts for other, pts in
                   ((("line", i), ln) for i, ln in enumerate(self.lines)) if other != item) \
            or any(k in arc for i, arc in enumerate(self.arcs) if ("arc", i) != item) \
            or any(k in self._ell_keys(i) for i in range(len(self.ells)) if ("ell", i) != item) \
            or any(k in s_[1] for s_ in self.spls)

    def _ext_apply(self, got):
        e = self.ext
        k = e["k"]
        st = self._state()
        moved = False
        if not self._ext_shared(k, e["item"]) and k not in self._fixed():
            self._tie_on(k, got["target"])
            rels = self._rels() + self._hold_radii() + self._hold_ells()
            done = sk.solve([(p.x(), p.y()) for p in self.pts], rels, {k: (got["at"].x(), got["at"].y())}, self._fixed())
            if done is not None and math.hypot(done[k][0] - got["at"].x(), done[k][1] - got["at"].y()) <= 1e-4:
                for p, (x, y) in zip(self.pts, done):
                    p.setX(x)
                    p.setY(y)
                moved = True
            else:
                self._restore(st)
                st = self._state()
        if not moved:
            self._ext_new(got)  # the end is held: the shape stays, a new construction piece goes on from it (the user)
        log.say("extend", e["item"], "to", got["target"], "at", log.pt(got["at"]), "moved" if moved else "new construction")
        self.steps.append(("state", st, len(self.lines), len(self.arcs)))
        self.ext = self.ext_at = None
        self._trim_prompt("Extend: this end is held, a construction line goes on from it" if not moved else None)
        self.changed.emit()
        self._refresh()

    def _ext_new(self, got):
        """From the held end on to the target: a construction line along the line, a construction arc round the arc."""
        self.set_dim(False)  # what was just drawn is seen
        e = self.ext
        kind, i = e["item"]
        k, first = e["k"], e["first"]
        n = len(self.pts)
        j = self._point_index(got["at"], 1e-6)
        if j == n:
            self._tie_on(j, got["target"])
        if kind == "line":
            self.lines.append((k, j))
            self.constr = (self.constr + [False] * len(self.lines))[:len(self.lines) - 1] + [True]
            self._add_tie("on_line", [j], i)
        elif kind == "arc":
            self.arcs.append((self.arcs[i][0], *((j, k) if first else (k, j))))
            self.arc_constr = (self.arc_constr + [False] * len(self.arcs))[:len(self.arcs) - 1] + [True]
        else:
            c, a, b = self.ells[i]
            self.pts.append(P(self.pts[a]))
            ia = len(self.pts) - 1
            self.pts.append(P(self.pts[b]))
            ib = len(self.pts) - 1
            self.ells.append((c, ia, ib))
            self.ell_constr = (self.ell_constr + [False] * len(self.ells))[:len(self.ells) - 1] + [True]
            self._add_tie("con_same", [a, ia])
            self._add_tie("con_same", [b, ib])
            self._add_tie("ell_cut", [j, k, c, ia, ib] if first else [k, j, c, ia, ib])

    # ----- drawn -----
    def _lit(self, g, item):
        """A shape in the pen set."""
        if item[0] == "axis":
            self._paint_curve(g, item)
        else:
            g.drawPolyline(QtGui.QPolygonF([self.to_screen(q) for q in _poly(self._geom(item), 96)]))

    def _paint_trim(self, g):
        if self.tool == "trim":
            got = self.trim_at
            if got is None:
                return
            red = QtGui.QColor(self.p["end_of_part"])
            g.setPen(QtGui.QPen(red, 3.0, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            g.setBrush(QtCore.Qt.NoBrush)
            if got["whole"] or got.get("spline"):
                self._lit(g, got["item"])
                return
            geo, lo, hi = got["g"], got["lo"], got["hi"]
            span = (hi - lo) % TAU if geo[0] != "seg" else hi - lo
            n = 2 if geo[0] == "seg" else max(8, math.ceil(span / TAU * 96))
            g.drawPolyline(QtGui.QPolygonF([self.to_screen(self._at_place(geo, lo + span * k / (n - 1 if geo[0] == "seg" else n)))
                                            for k in range(n if geo[0] == "seg" else n + 1)]))
            return
        snap = QtGui.QPen(QtGui.QColor(self.p["snap"]), 2.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap)
        if self.ext is None:
            if self.ext_at is not None and self.ext_at["item"][0] != "spl":
                g.setPen(snap)
                g.setBrush(QtCore.Qt.NoBrush)
                self._lit(g, self.ext_at["item"])
            return
        g.setPen(QtGui.QPen(QtGui.QColor(self.p["accent"]), 2.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        g.setBrush(QtCore.Qt.NoBrush)
        self._lit(g, self.ext["item"])
        got = self.ext_at
        if got is None:
            return
        target = got.get("target")
        if target is not None and target[0] != "spl":
            g.setPen(snap)
            self._lit(g, target)
        if got.get("at") is None:
            return
        g.setPen(QtGui.QPen(QtGui.QColor(self.p["accent"]), 1.6, QtCore.Qt.DashLine))
        n = 2 if self.ext["item"][0] == "line" else 48
        g.drawPolyline(QtGui.QPolygonF([self.to_screen(got["way"](got["reach"] * k / (n - 1))) for k in range(n)]))
        if target is not None and target[0] == "line":  # the target carried on to the place, when it stops short
            ga = self._geom(target)
            if not -1e-7 <= self._place_on(None, ga, got["at"]) <= 1 + 1e-7:
                near = min(ga[1:3], key=lambda q: _len(q - got["at"]))
                g.setPen(QtGui.QPen(QtGui.QColor(self.p["snap"]), 1.4, QtCore.Qt.DashLine))
                g.drawLine(self.to_screen(near), self.to_screen(got["at"]))
