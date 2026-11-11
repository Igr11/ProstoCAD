"""Polygon, one tool: a centre, then a corner or the middle of a side; the sides typed in a box and remembered; Space: the
dashed circle inside it, touching the sides, or through its corners. It stays regular whatever is pulled or dimensioned."""
import math

from PySide6 import QtCore, QtGui

P = QtCore.QPointF
TAU = 2 * math.pi
LEAST_SIDES, MOST_SIDES = 3, 64


def _len(p):
    return math.hypot(p.x(), p.y())


class PolygonTools:
    """Mixed into SketchView."""

    def _poly_sides(self):
        return max(LEAST_SIDES, min(MOST_SIDES, int(self.poly_sides)))

    def _poly_prompt(self, stage):
        where = "the middle of a side" if self.poly_in else "a corner"
        return "Click the centre  ·  Space: the circle inside or through the corners" if stage == 0 else \
            "Click " + where + "  ·  Space: the circle"

    def _poly_now(self):
        """(centre, pointer's direction, the circle's radius, sides, corners) as it stands; None before the size."""
        if self.tool != "polygon" or self._rect_stage() != 1 or self.mouse is None:
            return None
        c, end = self.rect_pts[0], self._rect_side_end()
        r = _len(end - c)
        if r < 1e-6:
            return None
        th, n = math.atan2((end - c).y(), (end - c).x()), self._poly_sides()
        far, turn = (r / math.cos(math.pi / n), math.pi / n) if self.poly_in else (r, 0.0)
        corners = [c + P(math.cos(th + turn + TAU * k / n), math.sin(th + turn + TAU * k / n)) * far for k in range(n)]
        return c, th, r, n, corners

    def _poly_finish(self):
        now = self._poly_now()
        if now is None:
            return
        c, th, r, n, corners = now
        st = self._state()
        self.constr = (self.constr + [False] * len(self.lines))[:len(self.lines)]
        self.arc_constr = (self.arc_constr + [False] * len(self.arcs))[:len(self.arcs)]
        self.poly_sides = n
        ic = self._point_index(c)
        self.pts.append(c + P(math.cos(th), math.sin(th)) * r)
        rim = len(self.pts) - 1
        self.arcs.append((ic, rim, rim))  # the circle stays, dashed
        self.arc_constr.append(True)
        self._poly_sides_on(ic, rim, corners, self.poly_in)
        self._tie_shape((ic,))
        self.steps.append(("state", st, len(self.lines), len(self.arcs)))
        self._shape_done("polygon %d" % n)
        self.poly_set.emit(n, self.poly_in)

    def _poly_sides_on(self, ic, rim, corners, inside):
        """The sides round a circle that stands already."""
        n = len(corners)
        idx = []
        for q in corners:
            self.pts.append(P(q))
            idx.append(len(self.pts) - 1)
        for k in range(n):
            self.lines.append((idx[k], idx[(k + 1) % n]))
            self.constr.append(self.construction)
        for k in range(1, n):
            self._add_tie("poly_round", [ic, idx[0], idx[k]])
            self._add_tie("con_eq", [idx[0], idx[1], idx[k], idx[(k + 1) % n]])
        self._add_tie("con_dist", [ic, rim, idx[0], idx[1]]) if inside else self._add_tie("poly_round", [ic, rim, idx[0]])
        self._add_tie("poly_of", [ic, rim, *idx], dict(inside=inside))
        return self.ties[-1]

    def poly_edit_at(self, s):
        """The polygon at the screen point s; None."""
        what = self._pick(s, ("line", "arc"))
        if what is not None:
            points = self.lines[what[1]] if what[0] == "line" else self.arcs[what[1]]
            got = self._poly_of(points)
            hit = next((t for t in self.ties if t["kind"] == "poly_of" and t["pts"] is got), None)
            if hit is not None:
                return hit
        for t in reversed(self.ties):  # or anywhere inside it
            if t["kind"] == "poly_of" and QtGui.QPolygonF([self.to_screen(self.pts[k]) for k in t["pts"][2:]]).containsPoint(s, QtCore.Qt.OddEvenFill):
                return t
        return None

    def poly_rebuild(self, tie, n, inside):
        """Other sides or the other circle: the centre, the size and the way it faces stay. Sizes on the old sides go."""
        n = max(LEAST_SIDES, min(MOST_SIDES, int(round(n))))
        steps = len(self.steps)
        ic, rim, *old = tie["pts"]
        c, edge = P(self.pts[ic]), self.pts[rim]
        r, th = _len(edge - c), math.atan2((edge - c).y(), (edge - c).x())
        corners_set = set(old)
        self.ties = [t for t in self.ties if not (t["kind"] in ("poly_of", "poly_round", "con_dist", "con_eq") and set(t["pts"]) & corners_set)]
        self.sel_lines = {i for i, ln in enumerate(self.lines) if set(ln) <= corners_set}
        self._delete()
        del self.steps[steps:]
        ic = next(i for i, a in enumerate(self.arcs) if self.is_circle(i) and _len(self.pts[a[0]] - c) < 1e-9 and abs(_len(self.pts[a[1]] - c) - r) < 1e-9)
        ic, rim = self.arcs[ic][:2]
        far, turn = (r / math.cos(math.pi / n), math.pi / n) if inside else (r, 0.0)
        corners = [c + P(math.cos(th + turn + TAU * k / n), math.sin(th + turn + TAU * k / n)) * far for k in range(n)]
        self.constr = (self.constr + [False] * len(self.lines))[:len(self.lines)]
        new = self._poly_sides_on(ic, rim, corners, inside)
        self._clear_choice()
        self.changed.emit()
        self._refresh()
        return new

    def _poly_of(self, points):
        """The polygon these points belong to: [centre, rim, corners…]; None."""
        here = set(points)
        return next((t["pts"] for t in self.ties if t["kind"] == "poly_of" and here & set(t["pts"][1:])), None)

    def _poly_rims(self):
        return {t["pts"][1] for t in self.ties if t["kind"] == "poly_of"}

    def _poly_carried(self, k):
        """A polygon's centre takes all of it along."""
        return tuple(q for t in self.ties if t["kind"] == "poly_of" and t["pts"][0] == k for q in t["pts"][1:])

    def _poly_targets(self, d, m):
        """A polygon pulled stays regular: a corner turns and grows it round its centre, a side or its circle grows it."""
        got, was = d["poly"], d["was"]
        c = was[got[0]]
        if d["kind"] == "point":
            old, new = was[d["index"]] - c, m - c
            k, turn = _len(new) / (_len(old) or 1.0), math.atan2(new.y(), new.x()) - math.atan2(old.y(), old.x())
        elif d["kind"] == "line":
            a, b = [was[q] for q in d["pts"]]
            mid = (a + b) * 0.5 - c
            n = _len(mid) or 1.0
            k, turn = max(0.05, ((m - c).x() * mid.x() + (m - c).y() * mid.y()) / n / n), 0.0
        else:
            k, turn = _len(m - c) / (_len(was[got[1]] - c) or 1.0), 0.0
        co, si = math.cos(turn) * k, math.sin(turn) * k
        out = {got[0]: P(c)}
        for q in got[1:]:
            u = was[q] - c
            out[q] = c + P(u.x() * co - u.y() * si, u.x() * si + u.y() * co)
        return out

    def _poly_stay(self, pulled):
        """A corner, a side or the circle pulled: the centre stays, the polygon turns and grows round it."""
        got = self._poly_of(pulled)
        return {} if got is None else {got[0]: P(self.pts[got[0]])}

    def _paint_poly(self, g, pen):
        now = self._poly_now()
        if now is None:
            return
        c, th, r, n, corners = now
        g.setBrush(QtCore.Qt.NoBrush)
        g.drawPolygon(QtGui.QPolygonF([self.to_screen(q) for q in corners]))
        g.setPen(QtGui.QPen(pen.color(), 1, QtCore.Qt.DashLine))
        g.drawPolyline(self._arc_poly(c, r, th, TAU))
