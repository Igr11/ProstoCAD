"""Fillet and Chamfer on sketch corners. The window stays open while the tool runs: its size goes for every corner of this run,
and changing it changes them all (the run is drawn again from where it began). A corner is two lines: the point they
share, two clicked lines even if they do not meet, or a box round several corners. One Cmd+Z takes the whole run back."""
import math

from PySide6 import QtCore, QtGui

from prostocad import log
from prostocad import sketch_solve as sk

P = QtCore.QPointF
CORNER_TOOLS = ("sk_fillet", "sk_chamfer")
NEAR_PX = 12


def _len(p):
    return math.hypot(p.x(), p.y())


def _dot(a, b):
    return a.x() * b.x() + a.y() * b.y()


def _cross(a, b):
    return a.x() * b.y() - a.y() * b.x()


class CornerTools:
    """Mixed into SketchView."""

    def start_corner(self, kind):
        self._end_chain()
        self._rect_reset()
        self.tool = kind
        self.just_done = False
        self.run = dict(st=self._state(), specs=[], first=None, hover=None, why=None)
        self.setFocus()
        self._corner_prompt()
        self._refresh()

    def _corner_prompt(self, say=None):
        word = "Fillet" if self.tool == "sk_fillet" else "Chamfer"
        self.prompt.emit(say or word + ("  Click the second line" if self.run["first"] is not None else
                                        "  Click a corner, or two lines, or draw a box round corners  ·  Space: Fillet or Chamfer"))

    def corner_swap(self):
        """Space: the other one; what this run made stands."""
        self._corner_close()
        kind = "sk_chamfer" if self.tool == "sk_fillet" else "sk_fillet"
        self.start_corner(kind)
        self.tool_changed.emit("sketch." + kind[3:])
        self.corner_swapped.emit()

    def _corner_close(self):
        self.run = None

    def _meet(self, a, b, c, d):
        """Where the lines through a-b and c-d meet; None if parallel."""
        u, w = b - a, d - c
        den = _cross(u, w)
        if abs(den) < 1e-9 * max(_len(u) * _len(w), 1e-12):
            return None
        return a + u * (_cross(c - a, w) / den)

    def _corner_geo(self, spec, tool=None, sizes=None):
        """dict of the new places for a corner, or a refusal as text. spec: (line, its end at the corner, line, its end)."""
        tool, sizes = tool or self.tool, sizes or self.corner_size
        la, ea, lb, eb = spec
        a_near, a_far = self.lines[la][ea], self.lines[la][1 - ea]
        b_near, b_far = self.lines[lb][eb], self.lines[lb][1 - eb]
        x = self._meet(self.pts[a_far], self.pts[a_near], self.pts[b_far], self.pts[b_near])
        if x is None:
            return "These lines are parallel"
        lena, lenb = _len(self.pts[a_far] - x), _len(self.pts[b_far] - x)
        if lena < 1e-9 or lenb < 1e-9:
            return "No corner here"
        ua, ub = (self.pts[a_far] - x) / lena, (self.pts[b_far] - x) / lenb
        th = math.atan2(abs(_cross(ua, ub)), _dot(ua, ub))
        if th < 1e-6 or th > math.pi - 1e-6:
            return "These lines are in one line"
        if tool == "sk_fillet":
            r = sizes["radius"]
            d = r / math.tan(th / 2)
            if d >= min(lena, lenb) - 1e-6:
                return "The radius is too big for this corner"
            ta, tb = x + ua * d, x + ub * d
            way = ua + ub
            c = x + way / _len(way) * (r / math.sin(th / 2))
            return dict(ta=ta, tb=tb, c=c, r=r, th=th)
        s = sizes
        d1 = s["size"]
        d2 = s["size2"] if s["type"] == "two" else d1
        if s["type"] == "angle":
            a = math.radians(s["angle"])
            if a <= 0 or a + th >= math.pi:
                return "This angle does not fit this corner"
            d2 = d1 * math.sin(a) / math.sin(a + th)
        if d1 >= lena - 1e-6 or d2 >= lenb - 1e-6:
            return "The size is too big for this corner"
        return dict(ta=x + ua * d1, tb=x + ub * d2, th=th)

    def _corner_apply(self, spec):
        """Put one corner in; False if it does not fit."""
        got = self._corner_geo(spec)
        if isinstance(got, str):
            return False
        la, ea, lb, eb = spec
        a_near, b_near = self.lines[la][ea], self.lines[lb][eb]
        self.pts.append(P(got["ta"]))
        ia = len(self.pts) - 1
        self.pts.append(P(got["tb"]))
        ib = len(self.pts) - 1
        a_far, b_far = self.lines[la][1 - ea], self.lines[lb][1 - eb]
        self._corner_follow(a_near, a_far, ia)
        self._corner_follow(b_near, b_far, ib)
        tied = {k for t in self.ties if t["kind"] != "sk_corner" for k in t["pts"]}
        self.lines[la] = (ia, a_far) if ea == 0 else (a_far, ia)
        self.lines[lb] = (ib, b_far) if eb == 0 else (b_far, ib)
        others = {k for i, ln in enumerate(self.lines) if i not in (la, lb) for k in ln} | {k for arc in self.arcs for k in arc} \
            | {k for e in self.ells for k in e} | {k for s_ in self.spls for k in s_[1]}
        for k, ln in ((a_near, (ia, a_far)), (b_near, (ib, b_far))):  # what stood on the corner stays on it: it still is where the walls meet
            if k in tied or k in others:
                if k not in others and not any(t["kind"] == "sk_point" and k in t["pts"] for t in self.ties):
                    self._add_tie("sk_point", [k])
                self._add_tie("con_on_line", [k, *ln])
        faint = self.is_construction(la) and self.is_construction(lb)
        if "c" in got:
            self.pts.append(P(got["c"]))
            ic = len(self.pts) - 1
            first = (ia, ib) if _cross(got["ta"] - got["c"], got["tb"] - got["c"]) > 0 else (ib, ia)
            self.arc_constr = (self.arc_constr + [False] * len(self.arcs))[:len(self.arcs)] + [faint]
            self.arcs.append((ic, *first))
            self._add_tie("con_touch", [ia, ic, a_far, ia])
            self._add_tie("con_touch", [ib, ic, b_far, ib])
            if self.run["made"]:
                c0, r0 = self.run["made"][0]
                self._add_tie("con_eq_r", [c0, r0, ic, first[0]])
            self.run["made"].append((ic, first[0]))
            self._add_tie("sk_corner", [ia, ib, ic], dict(self.corner_size, tool=self.tool))
        else:
            self.constr = (self.constr + [False] * len(self.lines))[:len(self.lines)] + [faint]
            self.lines.append((ia, ib))
            self._add_tie("sk_corner", [ia, ib], dict(self.corner_size, tool=self.tool))
        return True

    def _corner_holds(self):
        """A fillet keeps its radius and a chamfer its sides when the lines round them move; a dimension on a fillet, or
        pulling either by hand, goes first."""
        sized = {t["pts"][0] for t in self.ties if t["kind"] in ("dim_r", "dim_d")}
        out = []
        for t in self.ties:
            if t["kind"] == "sk_corner" and len(t["pts"]) == 3 and t["pts"][2] not in sized and t["pts"][2] not in self.corner_loose:
                out.append(sk.radius_is(t["pts"][2], t["pts"][0], _len(self.pts[t["pts"][0]] - self.pts[t["pts"][2]])))
            if t["kind"] == "sk_corner" and len(t["pts"]) == 2 and t["pts"][0] not in self.corner_loose:
                spec = self._corner_spec(t)
                if spec is None:
                    continue
                ia, ib = t["pts"]
                la, ea, lb, eb = spec
                af, bf = self.lines[la][1 - ea], self.lines[lb][1 - eb]
                x = self._meet(self.pts[af], self.pts[ia], self.pts[bf], self.pts[ib])
                if x is not None:
                    out.append(sk.corner_cut(ia, af, ib, bf, _len(self.pts[ia] - x)))
                    out.append(sk.corner_cut(ib, bf, ia, af, _len(self.pts[ib] - x)))
        return out

    def _corner_walls(self, i):
        """The corners on the line i, when it is a wall (not a chamfer itself): where its neighbour's line is, as points."""
        out = []
        for t in self.ties:
            if t["kind"] != "sk_corner" or set(self.lines[i]) == set(t["pts"][:2]):
                continue
            spec = self._corner_spec(t)
            if spec is None or i not in (spec[0], spec[2]):
                continue
            la, ea, lb, eb = spec
            mine, other = (la, ea, lb, eb)[:2], (la, ea, lb, eb)[2:]
            if i != la:
                mine, other = other, mine
            out.append(dict(near=self.lines[mine[0]][mine[1]], far=self.lines[mine[0]][1 - mine[1]], n_near=self.lines[other[0]][other[1]],
                            n_far=self.lines[other[0]][1 - other[1]], c=t["pts"][2:]))
        return out

    def _wall_targets(self, d, step):
        """A wall pulled: it alone moves. A corner on it goes along the next wall as it was built; that wall ends sooner or
        later, the rest stay. The wall stops where a corner runs out of the next wall."""
        was, k = d["was"], 1.0
        lines = []
        for w in d["walls"]:
            x0 = self._meet(was[w["n_far"]], was[w["n_near"]], was[w["far"]], was[w["near"]])
            x1 = self._meet(was[w["n_far"]], was[w["n_near"]], was[w["far"]] + step, was[w["near"]] + step)
            if x0 is None or x1 is None:
                continue
            u = (was[w["n_far"]] - x0) / _len(was[w["n_far"]] - x0)
            q, room = _dot(x1 - x0, u), _len(was[w["n_far"]] - x0) - _len(was[w["n_near"]] - x0) - 1e-3
            if q > room:
                k = min(k, max(room, 0.0) / q)
            lines.append((w, x0, u, q))
        out = {p: was[p] + step * k for p in d["pts"]}
        for w, x0, u, q in lines:
            delta = u * (q * k)
            near_corner = [p for p, at in enumerate(was) if _len(at - x0) < 1e-6]
            for p in (w["near"], w["n_near"], *w["c"], *near_corner):
                out[p] = was[p] + delta
        for item in self._connected(("line", d["index"])):
            for p in self._shape_points(item):
                out.setdefault(p, was[p])
        return out

    def _corner_pulled(self, kind, i):
        """The fillets a drag of this arc or point takes by the hand, with those tied equal to them."""
        mine = [t for t in self.ties if t["kind"] == "sk_corner"]
        key = lambda t: t["pts"][-1] if len(t["pts"]) == 3 else t["pts"][0]
        if kind == "arc":
            loose = {key(t) for t in mine if len(t["pts"]) == 3 and t["pts"][2] == self.arcs[i][0]}
        elif kind == "point":
            loose = {key(t) for t in mine if i in t["pts"]}
        elif kind == "line":
            loose = {key(t) for t in mine if len(t["pts"]) == 2 and set(self.lines[i]) == set(t["pts"])}
        else:
            return set()
        grown = True
        while grown:
            grown = False
            for t in self.ties:
                if t["kind"] == "con_eq_r" and {t["pts"][0], t["pts"][2]} & loose and not {t["pts"][0], t["pts"][2]} <= loose:
                    loose |= {t["pts"][0], t["pts"][2]}
                    grown = True
        return loose

    def _corner_follow(self, near, far, new):
        """A tie that speaks of this line by its two ends goes on with the line's new end."""
        for t in self.ties:
            for at in {"con_touch": (2,), "con_on_line": (1,), "con_perp": (0, 2), "con_par": (0, 2), "con_col": (0, 2)}.get(t["kind"], ()):
                pair = t["pts"][at:at + 2]
                if set(pair) == {near, far}:
                    t["pts"][at:at + 2] = [new if k == near else k for k in pair]

    def _corner_redo(self):
        """The whole run again from where it began, with the size as it stands now."""
        r = self.run
        self._restore(r["st"])  # the kept copy is now the live sketch: a new one is kept
        r["st"] = self._state()
        r["made"], r["hover"], r["first"] = [], None, None
        kept = []
        for spec in r["specs"]:
            if self._corner_apply(spec):
                kept.append(spec)
        self._corner_tidy()
        self.steps = list(r["st"]["steps"])
        if kept:
            self.steps.append(("state", r["st"], len(self.lines), len(self.arcs)))
        self.changed.emit()
        self._refresh()
        return len(kept) == len(r["specs"])

    def corner_undo(self):
        """Cmd+Z while the tool runs: its last corner; True if it took one."""
        if self.run is None or not self.run["specs"]:
            return False
        self.run["specs"].pop()
        self._corner_redo()
        return True

    def _corner_tidy(self):
        """Points no shape uses go, and what was tied to them."""
        used = {k for ln in self.lines for k in ln} | {k for arc in self.arcs for k in arc} | {k for e in self.ells for k in e} \
            | {k for s_ in self.spls for k in s_[1]} | {k for t in self.ties if t["kind"] in ("ell_cut", "sk_point") for k in t["pts"]}
        self.ties = [t for t in self.ties if all(k in used for k in t["pts"])]
        self._drop_lone_points()

    def corner_set(self, values):
        """The window changed: every corner of this run follows; one that no longer fits says so."""
        self.corner_size = dict(self.corner_size, **values)
        if self.run is not None and self.run["specs"] and not self._corner_redo():
            self._corner_prompt("Some corner is too small for this size: it is left out")

    def _corner_add(self, specs):
        r = self.run
        if not r["specs"]:  # Cmd+Z may have taken earlier work
            r["st"] = self._state()
        had = list(r["specs"])
        r["specs"] = had + [q for q in specs if q not in had]
        for q in specs:
            got = self._corner_geo(q)
            if isinstance(got, str):
                self._corner_prompt("Fillet: " + got if self.tool == "sk_fillet" else "Chamfer: " + got)
                if q in r["specs"]:
                    r["specs"].remove(q)
        if r["specs"] != had:
            log.say("corner", self.tool, len(r["specs"]))
            self._corner_redo()

    def _corners_of(self, k):
        """The corner at point k: the two drawn lines ending there. The ends of a chamfer or fillet are no corners: it is cut once."""
        if any(t["kind"] == "sk_corner" and k in t["pts"][:2] for t in self.ties):
            return None
        drawn = [(i, 0 if ln[0] == k else 1) for i, ln in enumerate(self.lines) if k in ln and not self.is_construction(i)]
        if len(drawn) != 2 or any(self.is_projected(("line", i)) for i, _end in drawn):
            return None  # a projected line is the part's: no fillet or chamfer on it (as the old program)

        def flat(item):  # Distance goes on the more level line, the same at every corner of a shape, whatever order it was drawn in
            ln = self.lines[item[0]]
            d = self.pts[ln[1]] - self.pts[ln[0]]
            return -round(abs(d.x()) / max(_len(d), 1e-12), 6)

        one, two = sorted(drawn, key=flat)
        return (one[0], one[1], two[0], two[1])

    def _corner_at(self, s):
        """The corner near the screen point s; None."""
        best = None
        for k, p in enumerate(self.pts):
            d = _len(self.to_screen(p) - s)
            if d <= NEAR_PX and (best is None or d < best[0]) and self._corners_of(k):
                best = (d, k)
        return self._corners_of(best[1]) if best else None

    def _two_lines(self, one, two):
        """Two clicked lines: the ends nearest to where they meet."""
        a, b = self.lines[one], self.lines[two]
        x = self._meet(self.pts[a[0]], self.pts[a[1]], self.pts[b[0]], self.pts[b[1]])
        if x is None:
            return None
        ea = 0 if _len(self.pts[a[0]] - x) < _len(self.pts[a[1]] - x) else 1
        eb = 0 if _len(self.pts[b[0]] - x) < _len(self.pts[b[1]] - x) else 1
        return (one, ea, two, eb)

    def corner_press(self, s):
        """A click: a corner, or a line, or the start of a box."""
        r = self.run
        spec = self._corner_at(s)
        if spec is not None and r["first"] is None:
            self._corner_add([spec])
            return
        cut = {k for t in self.ties if t["kind"] == "sk_corner" for k in t["pts"][:2]}
        if any(_len(self.to_screen(self.pts[k]) - s) <= NEAR_PX for k in cut):
            return  # a corner is cut once
        what = self._pick(s, ("line",))
        if what is not None and any(t["kind"] == "sk_corner" and set(self.lines[what[1]]) == set(t["pts"][:2]) for t in self.ties):
            return  # a chamfer's own line is no wall
        if what is not None and r["first"] is None:
            r["first"] = what[1]
            self._corner_prompt()
            self.update()
            return
        if what is not None and what[1] != r["first"]:
            spec = self._two_lines(r["first"], what[1])
            r["first"] = None
            if spec is None:
                self._corner_prompt("These lines are parallel")
                return
            if self.lines[spec[0]][spec[1]] in cut and self.lines[spec[2]][spec[3]] in cut:
                self._corner_prompt("This corner is already cut")
                return
            self._corner_prompt()
            self._corner_add([spec])
            return
        if what is None and r["first"] is None:
            at = self.to_model(s)
            self.boxing = dict(at=at, to=P(at), touch=False, add=False, lines=set(), arcs=set())

    def corner_box_end(self, b):
        x0, x1 = sorted((b["at"].x(), b["to"].x()))
        y0, y1 = sorted((b["at"].y(), b["to"].y()))
        inside = [k for k, p in enumerate(self.pts) if x0 <= p.x() <= x1 and y0 <= p.y() <= y1]
        self._corner_add([q for q in map(self._corners_of, inside) if q])

    def _corner_spec(self, t):
        """The corner of a sk_corner tie as a spec; None if one of its lines is gone."""
        ia, ib = t["pts"][:2]
        out = []
        for k in (ia, ib):
            hit = next((i for i, ln in enumerate(self.lines) if k in ln and set(ln) != {ia, ib}), None)
            if hit is None:
                return None
            out += [hit, 0 if self.lines[hit][0] == k else 1]
        return tuple(out)

    @staticmethod
    def _corner_same(t):
        """Corners with the same key are made the same way."""
        q = t["ref"]
        if q["tool"] == "sk_fillet":
            return (q["tool"], q["radius"])
        return (q["tool"], q["type"], q["size"], q["size2"] if q["type"] == "two" else None, q["angle"] if q["type"] == "angle" else None)

    def corner_groups(self, s):
        """The corners of the figure at the screen point s, or of the chosen one, as groups that change together. One corner
        under the pointer: it alone, then the others one by one. The whole figure: those made alike stand together.
        None if it has none."""
        what = self._pick(s, ("line", "arc"))
        seeds = [what] if what is not None else [("line", i) for i in self.sel_lines] + [("arc", i) for i in self.sel_arcs]
        points = set()
        for q in seeds:
            for item in self._connected(tuple(q)):
                points |= set(self._shape_points(item))
        corners = [t for t in self.ties if t["kind"] == "sk_corner" and set(t["pts"]) & points and self._corner_spec(t)]
        if not corners:
            return None
        if what is not None and len(self.sel_lines) + len(self.sel_arcs) <= 1:
            here = next((t for t in corners if self._corner_is(t, tuple(what))), None)
            if here is not None:
                return [[here]] + [[t] for t in corners if t is not here]
        groups = {}
        for t in corners:
            groups.setdefault(self._corner_same(t), []).append(t)
        return list(groups.values())

    def _corner_is(self, t, item):
        """This arc or chamfer line is the corner of the tie."""
        kind, i = item
        return self.arcs[i][0] == t["pts"][2] if kind == "arc" and len(t["pts"]) == 3 else \
            kind == "line" and len(t["pts"]) == 2 and set(self.lines[i]) == set(t["pts"])

    def _corner_part(self, t):
        """The two points the equal-size tie of this corner stands on."""
        return (t["pts"][2], t["pts"][0]) if len(t["pts"]) == 3 else (t["pts"][0], t["pts"][1])

    def _corner_free(self, group):
        """These corners take a size of their own: the equal ties to the others go, and the others stay equal among themselves."""
        mine = [self._corner_part(t) for t in group]
        rims = {t["pts"][1] for t in group if len(t["pts"]) == 3}  # an arc's tie may stand on either of its ends
        def is_mine(q):
            return q in mine or (q[1] in rims and any(q[0] == c for c, _ in mine))
        for kind in ("con_eq_r",):
            going = [t for t in self.ties if t["kind"] == kind and is_mine(tuple(t["pts"][:2])) != is_mine(tuple(t["pts"][2:]))]
            others = []
            for t in going:
                for q in (tuple(t["pts"][:2]), tuple(t["pts"][2:])):
                    if not is_mine(q) and q not in others:
                        others.append(q)
            self.ties = [t for t in self.ties if not any(t is g for g in going)]
            for q in others[1:]:
                self._add_tie(kind, [*others[0], *q])

    def corner_edit_set(self, values):
        """The window changed: the corners of its group go to the new size, all of them or none."""
        e = self.corner_edit
        group = e["groups"][e["at"]]
        size = dict(group[0]["ref"], **values)
        saved = [P(p) for p in self.pts]
        for t in group:
            got = self._corner_geo(self._corner_spec(t), size["tool"], size)
            if isinstance(got, str):
                self.pts = saved
                self.prompt.emit(got)
                self._refresh()
                return
            self.pts[t["pts"][0]], self.pts[t["pts"][1]] = P(got["ta"]), P(got["tb"])
            if "c" in got:
                self.pts[t["pts"][2]] = P(got["c"])
        self._corner_free(group)
        for t in group:
            t["ref"] = dict(size)
        self.changed.emit()
        self._refresh()

    def corner_next(self):
        """Space while editing: the next corners that are made another way."""
        e = self.corner_edit
        if e is not None and len(e["groups"]) > 1:
            e["at"] = (e["at"] + 1) % len(e["groups"])
            self.corner_moved.emit()
            self.update()

    def _paint_corner_edit(self, g):
        e = self.corner_edit
        g.setPen(QtGui.QPen(QtGui.QColor(self.p["snap"]), 2.6))
        g.setBrush(QtCore.Qt.NoBrush)
        for t in e["groups"][e["at"]]:
            if len(t["pts"]) == 2:
                g.drawLine(self.to_screen(self.pts[t["pts"][0]]), self.to_screen(self.pts[t["pts"][1]]))
                continue
            ta, tb, c = (self.pts[k] for k in t["pts"])
            a0, a1 = math.atan2(ta.y() - c.y(), ta.x() - c.x()), math.atan2(tb.y() - c.y(), tb.x() - c.x())
            sw = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
            g.drawPolyline(self._arc_poly(c, _len(ta - c), a0 if sw > 0 else a1, abs(sw)))

    def _paint_corner(self, g):
        r = self.run
        if r is None or self.boxing is not None:
            return
        snap = QtGui.QPen(QtGui.QColor(self.p["snap"]), 2.2)
        g.setPen(snap)
        g.setBrush(QtCore.Qt.NoBrush)
        if r["first"] is not None:
            a, b = self.lines[r["first"]]
            g.drawLine(self.to_screen(self.pts[a]), self.to_screen(self.pts[b]))
        spec = r["hover"]
        if spec is None:
            return
        got = self._corner_geo(spec)
        if isinstance(got, str):
            return
        if "c" in got:
            ang = lambda q: math.atan2(q.y(), q.x())
            a0, a1 = ang(got["ta"] - got["c"]), ang(got["tb"] - got["c"])
            sw = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
            g.drawPolyline(self._arc_poly(got["c"], got["r"], a0 if sw > 0 else a1, abs(sw)))
        else:
            g.drawLine(self.to_screen(got["ta"]), self.to_screen(got["tb"]))
