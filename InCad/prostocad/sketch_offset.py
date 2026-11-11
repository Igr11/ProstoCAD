"""Sketch Offset. A click takes a shape's whole outline (shapes joined end to end), Cmd, Ctrl or Shift takes another, and what
was chosen before the tool comes along. The copy follows the pointer on its side, a click places it and the size box opens
on the offset dimension. Sharp corners stay sharp; lines stay parallel and arcs concentric at one size. An ellipse or a
spline comes out as a spline through its offset points, not tied to the original."""
import math

from PySide6 import QtCore, QtGui

from prostocad import log

P = QtCore.QPointF
TOO_LARGE = "The offset is too large for this shape"
NEAR = "Move the pointer away from the shape to set the distance"
PICK = "Click a line, arc, circle, ellipse or spline of the sketch"
ROUND = 48  # points on a curve
BEND = 0.01  # radians: the shapes run on smoothly through their shared point


def _len(p):
    return math.hypot(p.x(), p.y())


def _dot(a, b):
    return a.x() * b.x() + a.y() * b.y()


def _cross(a, b):
    return a.x() * b.y() - a.y() * b.x()


def _left(u):
    return P(-u.y(), u.x()) / (_len(u) or 1.0)


def _line_cross(a, u, b, w):
    den = _cross(u, w)
    return None if abs(den) < 1e-12 * max(_len(u) * _len(w), 1e-12) else a + u * (_cross(b - a, w) / den)


def _circle_line(c, r, a, u):
    u = u / _len(u)
    foot = a + u * _dot(c - a, u)
    h = r * r - _dot(foot - c, foot - c)
    if h < 0:
        return []
    h = math.sqrt(h)
    return [foot - u * h, foot + u * h]


def _circles(c1, r1, c2, r2):
    d = _len(c2 - c1)
    if d < 1e-12:
        return []
    a = (d * d + r1 * r1 - r2 * r2) / (2 * d)
    h = r1 * r1 - a * a
    if h < 0:
        return []
    u = (c2 - c1) / d
    n = _left(u)
    m = c1 + u * a
    return [m - n * math.sqrt(h), m + n * math.sqrt(h)]


def _inside(ring, m):
    """m inside the closed points ring. QPolygonF says yes to a point level with a repeated corner."""
    hit = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        if (a.y() > m.y()) != (b.y() > m.y()) and m.x() < a.x() + (b.x() - a.x()) * (m.y() - a.y()) / (b.y() - a.y()):
            hit = not hit
    return hit


def _seg_cross(a, b, c, d):
    u, w = b - a, d - c
    den = _cross(u, w)
    if abs(den) < 1e-14:
        return None
    t, s = _cross(c - a, w) / den, _cross(c - a, u) / den
    return a + u * t if 0 <= t <= 1 and 0 <= s <= 1 else None


class OffsetTools:
    """Mixed into SketchView."""

    def start_offset(self):
        items = [("line", i) for i in sorted(self.sel_lines)] + [("arc", i) for i in sorted(self.sel_arcs)] \
            + [("ell", i) for i in sorted(self.sel_ells)] + [("spl", i) for i in sorted(self.sel_spls)]
        solid = [q for q in items if not self._off_faint(q)]
        items = solid or items  # a box round a rectangle takes its diagonal too: that is no outline to copy
        self._end_chain()
        self._rect_reset()
        self.tool = "sk_offset"
        self.just_done = False
        self.off = dict(taken=[], hover=None, copy=None, d=0.0)
        for item in items:
            if not any(item in out["items"] for out in self.off["taken"]):
                self.off["taken"].append(self._off_outline(item))
        self._clear_choice()
        self.setFocus()
        self._off_prompt()
        self._refresh()

    def _off_prompt(self, say=None):
        self.prompt.emit("Offset  " + (say or ("Click where the copy goes  ·  Cmd+click: one more outline" if self.off["taken"]
                                               else PICK)))

    def _off_ends(self, item):
        """The two points a shape ends on, None when it closes on itself."""
        kind, i = item
        if kind == "line":
            return self.lines[i]
        if kind == "arc":
            return None if self.is_circle(i) else self.arcs[i][1:3]
        if kind == "ell":
            return self._ell_cut(i)
        return None if self.spls[i][2] else (self.spls[i][1][0], self.spls[i][1][-1])

    def _off_faint(self, item):
        kind, i = item
        return bool({"line": self.constr, "arc": self.arc_constr, "ell": self.ell_constr, "spl": self.spl_constr}[kind][i:i + 1] == [True])

    def _off_walk(self, item, take):
        """(the shapes in order, each with the way it is walked, closed)."""
        start, end = self._off_ends(item)
        others = [q for q in self._all_shapes(everything=True) if q != item and self._off_ends(q) is not None and take(q)]
        forward, used, at = [], {item}, end
        closed = False
        while not closed:
            nxt = next((q for q in others if q not in used and at in self._off_ends(q)), None)
            if nxt is None:
                break
            ends = self._off_ends(nxt)
            way = ends[0] == at
            forward.append((nxt, way))
            used.add(nxt)
            at = ends[1] if way else ends[0]
            closed = at == start
        back, at = [], start
        while not closed:
            nxt = next((q for q in others if q not in used and at in self._off_ends(q)), None)
            if nxt is None:
                break
            ends = self._off_ends(nxt)
            way = ends[1] == at
            back.append((nxt, way))
            used.add(nxt)
            at = ends[0] if way else ends[1]
        return list(reversed(back)) + [(item, True)] + forward, closed

    def _off_loop(self, item, take):
        """The loop through this shape, when a branch lets it close: a line ending on a rectangle's corner is no side of it."""
        start, end = self._off_ends(item)
        shapes = [q for q in self._all_shapes(everything=True) if q != item and self._off_ends(q) is not None and take(q)]
        def on(at, used, chain):
            if len(chain) > 200:
                return None
            for q in shapes:
                if q in used or at not in self._off_ends(q):
                    continue
                ends = self._off_ends(q)
                way = ends[0] == at
                nxt = ends[1] if way else ends[0]
                if nxt == start:
                    return chain + [(q, way)]
                got = on(nxt, used | {q}, chain + [(q, way)])
                if got:
                    return got
            return None
        return on(end, {item}, [(item, True)])

    def _off_outline(self, item, take=None):
        """The outline a shape belongs to. Its own kind first: a construction diagonal is no side of a rectangle.
        take: only these shapes may be part of it."""
        if self._off_ends(item) is None:
            return dict(items=[item], segs=[self._off_seg(item, True)], closed=True, alone=True)
        faint = self._off_faint(item)
        own = (lambda q: self._off_faint(q) == faint and take(q)) if take else (lambda q: self._off_faint(q) == faint)
        loop = self._off_loop(item, own)
        if loop:
            return dict(items=[q for q, _ in loop], segs=[self._off_seg(q, way) for q, way in loop], closed=True, alone=False)
        chain, closed = self._off_walk(item, own)
        if not closed and take is None:
            mixed, shut = self._off_walk(item, lambda q: True)
            others = [q for q, _ in mixed if self._off_faint(q) != faint and self._off_ends(q) is not None]
            # both kinds only where that alone closes it; a side of a closed outline of its own kind is that outline's
            if shut and not any(self._off_walk(q, lambda h: self._off_faint(h) != faint)[1] for q in others):
                chain, closed = mixed, shut
        return dict(items=[q for q, _ in chain], segs=[self._off_seg(q, way) for q, way in chain], closed=closed, alone=False)

    def _off_seg(self, item, way):
        """A shape as a walked curve: its points along the way, what it is, and how it ends."""
        kind, i = item
        out = dict(item=item, way=way, kind=kind)
        if kind == "line":
            out["poly"] = [self.pts[k] for k in (self.lines[i] if way else self.lines[i][::-1])]
        elif kind == "arc":
            c, r, a0, sw = self.arc_shape(i)
            out.update(c=c, r=r, ring=True)
            out["poly"] = [c + P(math.cos(a0 + sw * k / ROUND), math.sin(a0 + sw * k / ROUND)) * r for k in range(ROUND + 1)]
            if not way:
                out["poly"].reverse()
        else:
            if kind == "ell":
                from prostocad.sketch import _ell_at
                c, u, v = self.ell_shape(i)
                t0, sw = self._ell_range(i)
                poly = [_ell_at(c, u, v, t0 + sw * k / ROUND) for k in range(ROUND + 1)]
            else:
                poly = list(self.spl_poly(i))
                if self.spls[i][2] and _len(poly[0] - poly[-1]) > 1e-9:
                    poly.append(poly[0])
            out["kind"], out["poly"] = "curve", poly if way else poly[::-1]
        return out

    @staticmethod
    def _off_end_ways(seg):
        """Its direction where it starts and where it ends, walked; exact on an arc."""
        p = seg["poly"]
        if seg["kind"] == "arc":
            turn = 1 if seg["way"] else -1
            a, b = p[0] - seg["c"], p[-1] - seg["c"]
            return P(-a.y(), a.x()) * turn, P(-b.y(), b.x()) * turn
        return p[1] - p[0], p[-1] - p[-2]

    def _off_side(self, out, m):
        """(+1 left of the way walked or -1 right, the distance) of the model point m."""
        best = None
        for seg in out["segs"]:
            p = seg["poly"]
            for a, b in zip(p, p[1:]):
                u = b - a
                n2 = _dot(u, u)
                if n2 < 1e-18:
                    continue
                foot = a + u * max(0.0, min(1.0, _dot(m - a, u) / n2))
                d = _len(m - foot)
                if best is None or d < best[0]:
                    best = (d, u, foot, seg)
        d, u, foot, seg = best
        if seg["kind"] == "arc":
            q = m - seg["c"]
            if abs(math.atan2(_cross(foot - seg["c"], q), _dot(foot - seg["c"], q))) < 0.2:
                d = abs(_len(q) - seg["r"])
        if not out["closed"]:
            return (1 if _cross(u, m - foot) >= 0 else -1), d
        ring = [q for seg in out["segs"] for q in seg["poly"]]
        area = sum(_cross(a, b) for a, b in zip(ring, ring[1:] + ring[:1]))
        inside = _inside(ring, m)
        return (1 if inside == (area > 0) else -1), d

    def _off_shift(self, seg, sign, d):
        """The curve the seg's copy lies on; None when it has no room."""
        if seg["kind"] == "line":
            a, b = seg["poly"]
            n = _left(b - a) * (sign * d)
            return dict(kind="line", a=a + n, u=b - a)
        if seg["kind"] == "arc":
            r = seg["r"] + (-sign * d if seg["way"] else sign * d)
            return dict(kind="arc", c=seg["c"], r=r) if r > 1e-6 else None
        p = seg["poly"]
        shut = _len(p[0] - p[-1]) < 1e-9
        out = []
        for k, q in enumerate(p):
            u = p[(k + 1) % (len(p) - 1)] - p[k - 1 if k else len(p) - 2] if shut else p[min(k + 1, len(p) - 1)] - p[max(k - 1, 0)]
            out.append(q + _left(u) * (sign * d))
        for q in out:  # past its curvature it folds: some point of the copy comes nearer than d
            if min(_len(q - w) for w in p) < d * 0.97:
                return None
        return dict(kind="curve", poly=out)

    def _off_meet(self, a, b, near):
        """Where two curves of the copy meet, the place nearest the original corner; None."""
        if a["kind"] == "line" and b["kind"] == "line":
            return _line_cross(a["a"], a["u"], b["a"], b["u"])
        if "curve" in (a["kind"], b["kind"]):
            pa, pb = self._off_as_poly(a, near), self._off_as_poly(b, near)
            hits = [h for p, q in zip(pa, pa[1:]) for r, s in zip(pb, pb[1:]) if (h := _seg_cross(p, q, r, s)) is not None]
        elif a["kind"] == "arc" and b["kind"] == "arc":
            hits = _circles(a["c"], a["r"], b["c"], b["r"])
        else:
            arc, line = (a, b) if a["kind"] == "arc" else (b, a)
            hits = _circle_line(arc["c"], arc["r"], line["a"], line["u"])
        return min(hits, key=lambda h: _len(h - near)) if hits else None

    @staticmethod
    def _off_as_poly(curve, near):
        if curve["kind"] == "curve":
            return curve["poly"]
        if curve["kind"] == "line":
            u = curve["u"] / _len(curve["u"])
            foot = curve["a"] + u * _dot(near - curve["a"], u)
            return [foot - u * 1e4, foot + u * 1e4]
        c, r = curve["c"], curve["r"]
        return [c + P(math.cos(k / 180 * math.pi), math.sin(k / 180 * math.pi)) * r for k in range(361)]

    def _off_copy(self, out, sign, d):
        """The copy's parts, or a refusal as text."""
        segs = out["segs"]
        curves = [self._off_shift(q, sign, d) for q in segs]
        if None in curves:
            return TOO_LARGE
        if out["alone"]:
            return dict(out=out, sign=sign, d=d, joints=[], curves=curves, smooth=[])
        n = len(segs)
        joints, smooth = [], []
        for k in range(n + (0 if out["closed"] else 1)):
            one, two = segs[k - 1] if k or out["closed"] else None, segs[k % n] if k < n or out["closed"] else None
            if one is None or two is None:
                q = segs[0]["poly"][0] if one is None else segs[-1]["poly"][-1]
                way = self._off_end_ways(two)[0] if one is None else self._off_end_ways(one)[1]
                joints.append(q + _left(way) * (sign * d))
                smooth.append(False)
                continue
            u, w = self._off_end_ways(one)[1], self._off_end_ways(two)[0]
            at = two["poly"][0]
            if abs(math.atan2(_cross(u, w), _dot(u, w))) < BEND:
                joints.append(at + _left(u) * (sign * d))
                smooth.append(True)
                continue
            hit = self._off_meet(curves[k - 1], curves[k % n], at)
            if hit is None:
                return TOO_LARGE
            joints.append(hit)
            smooth.append(False)
        for k, seg in enumerate(segs):
            a, b = joints[k], joints[(k + 1) % len(joints)]
            if seg["kind"] == "line" and _dot(b - a, seg["poly"][-1] - seg["poly"][0]) <= 1e-9:
                return TOO_LARGE
            if seg["kind"] == "arc":
                c = seg["c"]
                was = math.atan2(_cross(seg["poly"][0] - c, seg["poly"][-1] - c), _dot(seg["poly"][0] - c, seg["poly"][-1] - c))
                now = math.atan2(_cross(a - c, b - c), _dot(a - c, b - c))
                if was * now < 0 and abs(was - now) < math.pi:
                    return TOO_LARGE
        return dict(out=out, sign=sign, d=d, joints=joints, curves=curves, smooth=smooth)

    def _off_trimmed(self, poly, a, b):
        """A curve's copy between its two joints, thinned to the points a spline is drawn through."""
        i = min(range(len(poly)), key=lambda k: _len(poly[k] - a))
        j = min(range(len(poly)), key=lambda k: _len(poly[k] - b))
        part = poly[i:j + 1] if i <= j else poly[j:i + 1][::-1]
        step = max(1, len(part) // 24)
        return part[step::step][:-1] if len(part) > 2 * step else []

    def _off_track(self, s):
        o = self.off
        m = self.to_model(s)
        o["hover"] = None
        o["copy"] = None
        o["say"] = None
        what = self._pick(s, ("line", "arc", "ell", "spl"))
        if what is not None and not any(tuple(what) in out["items"] for out in o["taken"]):
            o["hover"] = self._off_outline(tuple(what))
        if not o["taken"]:
            return
        d = min(self._off_side(out, m)[1] for out in o["taken"])
        if d < 1e-4:
            o["say"] = NEAR
            return
        made = [self._off_copy(out, self._off_side(out, m)[0], d) for out in o["taken"]]
        if any(isinstance(q, str) for q in made):
            o["say"] = TOO_LARGE
            return
        o["copy"], o["d"] = made, d

    def offset_press(self, s, adding):
        o = self.off
        what = self._pick(s, ("line", "arc", "ell", "spl"))
        if what is not None and (adding or not o["taken"]):
            if not any(tuple(what) in out["items"] for out in o["taken"]):
                o["taken"].append(self._off_outline(tuple(what)))
            self._off_prompt()
            self._off_track(s)
            return
        if not o["taken"]:
            self._off_prompt()
            return
        self._off_track(s)
        if o["copy"] is None:
            self._off_prompt(o["say"] or NEAR)
            return
        self._off_place(o["copy"])

    def _off_place(self, made):
        st = self._state()
        parts = []  # ("line" or "ring", three points, what it measures now)
        for copy in made:
            parts += self._off_add(copy)
        self.constr = (self.constr + [False] * len(self.lines))[:len(self.lines)]
        self.arc_constr = (self.arc_constr + [False] * len(self.arcs))[:len(self.arcs)]
        lead = next((q for q in parts if q[0] == "line"), parts[0] if parts else None)
        log.say("offset", len(made), "outlines", len(parts), "tied")
        dim = None
        self.steps.append(("state", st, len(self.lines), len(self.arcs)))
        if lead is not None:
            for q in parts:
                if q is lead:
                    continue
                self._add_tie("off_" + lead[0][0] + q[0][0], [*lead[1], *q[1]], 1.0 if lead[2] * q[2] > 0 else -1.0)
            self.dim_st, self.dim_fresh = None, False  # Esc in the box keeps the size: it is the offset itself
            kind = "dim_par" if lead[0] == "line" else "dim_ring"
            self._add_tie(kind, lead[1], lead[2])
            dim = self.ties[-1]
            dim.update(off=self._off_room(kind, lead[1]), far=None, name=self._dim_claim(), expr=None, neg=False, axis=None, base=None, first=None)
        self.stop_tool()
        self.just_done = True
        self.changed.emit()
        self._refresh()
        if dim is not None:
            self._dim_open(dim)

    def _off_add(self, copy):
        """Puts one outline's copy in; what its size can be tied by."""
        out, sign, d = copy["out"], copy["sign"], copy["d"]
        parts = []
        if out["alone"]:
            item, curve = out["items"][0], copy["curves"][0]
            kind, i = item
            if kind == "arc":
                c, a = self.arcs[i][:2]
                self.pts.append(self.pts[c] + (self.pts[a] - self.pts[c]) * (curve["r"] / _len(self.pts[a] - self.pts[c])))
                rim = len(self.pts) - 1
                self.arcs.append((c, rim, rim))
                self._off_kind("arc", self._off_faint(item))
                return [("ring", [c, a, rim], curve["r"] - _len(self.pts[a] - self.pts[c]))]
            self._off_spline(curve["poly"][:-1][::max(1, (len(curve["poly"]) - 1) // 32)], True, self._off_faint(item))
            return []
        first = len(self.pts)
        self.pts += [P(q) for q in copy["joints"]]
        idx = list(range(first, len(self.pts)))
        n = len(out["segs"])
        made = []
        for k, seg in enumerate(out["segs"]):
            a, b = idx[k], idx[(k + 1) % len(idx)]
            item = seg["item"]
            faint = self._off_faint(item)
            if seg["kind"] == "line":
                oa, ob = self.lines[item[1]] if seg["way"] else self.lines[item[1]][::-1]
                self.constr = (self.constr + [False] * len(self.lines))[:len(self.lines)] + [faint]
                self.lines.append((a, b))
                self._add_tie("con_par", [a, b, oa, ob])
                value = _cross(self.pts[ob] - self.pts[oa], self.pts[a] - self.pts[oa]) / _len(self.pts[ob] - self.pts[oa])
                parts.append(("line", [a, oa, ob], value))
                made.append(("line", a, b, None))
            elif seg["kind"] == "arc":
                c, ra, _ = self.arcs[item[1]]
                self.arc_constr = (self.arc_constr + [False] * len(self.arcs))[:len(self.arcs)] + [faint]
                self.arcs.append((c, a, b) if seg["way"] else (c, b, a))
                parts.append(("ring", [c, ra, a], _len(self.pts[a] - self.pts[c]) - _len(self.pts[ra] - self.pts[c])))
                made.append(("arc", a, b, c))
            else:
                inner = self._off_trimmed(copy["curves"][k]["poly"], self.pts[a], self.pts[b])
                self._off_spline([self.pts[a], *inner, self.pts[b]], False, faint, a, b)
                made.append(("curve", a, b, None))
        for k in range(len(copy["smooth"])):  # where the outline runs on smoothly, its copy touches too
            if not copy["smooth"][k]:
                continue
            one, two = made[k - 1], made[k % n]
            t = one[2]
            if one[0] == "line" and two[0] == "arc":
                self._add_tie("con_touch", [t, two[3], one[1], one[2]])
            elif one[0] == "arc" and two[0] == "line":
                self._add_tie("con_touch", [t, one[3], two[1], two[2]])
            elif one[0] == "arc" and two[0] == "arc":
                self._add_tie("con_circles", [t, one[3], two[3]])
        return parts

    def _off_room(self, kind, pts):
        """Where the size stands: past the end of the lines, in the open, not on them."""
        room = 60.0 / self.scale
        if kind == "dim_ring":
            return room
        p, a, b = [self.pts[k] for k in pts]
        foot = self._dim_ends(kind, pts)[1]
        d = foot - p
        n = P(-d.y(), d.x()) / (_len(d) or 1.0)
        reach = [_dot(q - p, n) for q in (a, b)]
        low, high = min(reach + [0.0]), max(reach + [0.0])
        return low - room if abs(low) <= abs(high) else high + room

    def _off_of(self, pts):
        """For an offset dimension on pts: (the original's points, which stay; the copy's arcs, whose radius changes)."""
        stays, rims = set(), set()
        for t in self.ties:
            if t["kind"].startswith("off_") and t["pts"][:3] == list(pts):
                stays |= set(t["pts"][4:6] if t["kind"][-1] == "l" else t["pts"][3:5])
                if t["kind"][-1] == "r":
                    rims.add(t["pts"][5])
        if len(pts) == 3:  # the ring dimension's own copy
            rims.add(pts[2])
        return stays, [i for i, arc in enumerate(self.arcs) if rims & set(arc[1:])]

    def _off_spline(self, points, closed, faint, a=None, b=None):
        idx = []
        for k, q in enumerate(points):
            ends = {0: a, len(points) - 1: b}
            if ends.get(k) is not None:
                idx.append(ends[k])
            else:
                self.pts.append(P(q))
                idx.append(len(self.pts) - 1)
        self.spls.append(("fit", tuple(idx), closed))
        self.spl_constr = (self.spl_constr + [False] * len(self.spls))[:len(self.spls) - 1] + [faint]
        self.spl_comb = (self.spl_comb + [False] * len(self.spls))[:len(self.spls)]

    def _off_kind(self, kind, faint):
        self.arc_constr = (self.arc_constr + [False] * len(self.arcs))[:len(self.arcs) - 1] + [faint]

    def _paint_offset(self, g):
        o = self.off
        if o is None:
            return
        g.setBrush(QtCore.Qt.NoBrush)
        lit = QtGui.QPen(QtGui.QColor(self.p["snap"]), 2.6)
        g.setPen(lit)
        for out in o["taken"] + ([o["hover"]] if o["hover"] else []):
            for seg in out["segs"]:
                g.drawPolyline(QtGui.QPolygonF([self.to_screen(q) for q in seg["poly"]]))
        pen = QtGui.QPen(QtGui.QColor(self.p["snap"]), 1.6, QtCore.Qt.DashLine)
        g.setPen(pen)
        for copy in o["copy"] or ():
            for points in self._off_preview(copy):
                g.drawPolyline(QtGui.QPolygonF([self.to_screen(q) for q in points]))

    def _off_preview(self, copy):
        if copy["out"]["alone"]:
            c = copy["curves"][0]
            return [c["poly"] if c["kind"] == "curve" else self._off_as_poly(c, c["c"])]
        out = []
        n = len(copy["joints"])
        for k, (seg, curve) in enumerate(zip(copy["out"]["segs"], copy["curves"])):
            a, b = copy["joints"][k], copy["joints"][(k + 1) % n]
            if curve["kind"] == "line":
                out.append([a, b])
            elif curve["kind"] == "curve":
                out.append([a, *self._off_trimmed(curve["poly"], a, b), b])
            else:
                c = curve["c"]
                sw = math.atan2(_cross(a - c, b - c), _dot(a - c, b - c))
                turn = lambda t: c + P((a - c).x() * math.cos(t) - (a - c).y() * math.sin(t), (a - c).x() * math.sin(t) + (a - c).y() * math.cos(t))
                bent = seg["poly"][len(seg["poly"]) // 2] - c
                if _cross(a - c, bent) * sw < 0:
                    sw += -math.copysign(2 * math.pi, sw)
                out.append([turn(sw * j / ROUND) for j in range(ROUND + 1)])
        return out
