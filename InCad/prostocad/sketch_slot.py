"""The slots, as a part of the sketch view: straight ones by two points and a width, with a dashed centre line and a
point in its middle; arc slots by a centre, an arc and a width, round-ended or flat-ended. All of it stays held: the two
ends equal and square to the centre line, or to the arc."""
import math

from PySide6 import QtCore, QtGui

from prostocad import log, sketch_solve as sk

P = QtCore.QPointF
LEAST = 0.05  # half a width: never nothing
SNAP_PX = 8.0
SLOT_LINE = ("slot_c2c", "slot_center")
SLOT_ARC = ("slot_arc", "slot_arc_flat")
SLOTS = (*SLOT_LINE, *SLOT_ARC)
SLOT_NEXT = {"slot_c2c": "slot_center", "slot_center": "slot_c2c",
             "slot_arc": "slot_arc_flat", "slot_arc_flat": "slot_arc"}  # Space, before the first click
SLOT_COMMANDS = {"slot_c2c": "sketch.slot", "slot_center": "sketch.slot_center",
                 "slot_arc": "sketch.slot_arc", "slot_arc_flat": "sketch.slot_arc"}
SLOT_NAMES = {"slot_c2c": "Center-to-Center Slot", "slot_center": "Center Point Slot",
              "slot_arc": "Arc Slot", "slot_arc_flat": "Flat-End Arc Slot"}
_ARC_PROMPTS = ("Click the arc's centre  ·  Space: round or flat ends",
                "Click where its middle line starts, or type the radius  Tab  angle  Enter",
                "Click where it ends, or type its sweep  Enter", "Click the width, or type it  Enter  ·  Space: round or flat ends")
SLOT_PROMPTS = {
    "slot_c2c": ("Click one end's centre  ·  Space: from the slot's centre", "Click the other end's centre, or type the length  Tab  angle  Enter",
                 "Click the width, or type it  Enter"),
    "slot_center": ("Click the slot's centre  ·  Space: from an end", "Click one end's centre, or type the length  Tab  angle  Enter",
                    "Click the width, or type it  Enter"),
    "slot_arc": _ARC_PROMPTS, "slot_arc_flat": _ARC_PROMPTS}


def _len(p):
    return math.hypot(p.x(), p.y())


def _round(c, r, a):
    return c + P(math.cos(a), math.sin(a)) * r


class SlotTools:
    """Mixed into SketchView."""

    def _slot_widening(self):
        """The width is what the pointer sets now."""
        return (self.tool in SLOT_LINE and self._rect_stage() == 2) or (self.tool in SLOT_ARC and self._rect_stage() == 3)

    def _slot_half(self, far):
        typed = self.box_h.typed
        return max(abs(typed) / 2 if typed is not None else far, LEAST)

    def _slot_now(self):
        """A straight slot as it stands: (centre 1, centre 2, half width), or None."""
        if self.tool not in SLOT_LINE or self._rect_stage() != 2:
            return None
        p, q = self.rect_pts
        m = self._rect_target()
        whole = _len(q - p)
        if whole < 1e-6 or m is None:
            return None
        u = (q - p) / whole
        d = m - p
        r = self._slot_half(abs(u.x() * d.y() - u.y() * d.x()))
        return (p * 2 - q, P(q), r) if self.tool == "slot_center" else (P(p), P(q), r)

    def _slot_arc_now(self):
        """An arc slot as it stands: (centre, radius, start, sweep, half width), or None."""
        if self.tool not in SLOT_ARC or self._rect_stage() != 3:
            return None
        c, r, a0, sw = self.slot_arc
        m = self._rect_target()
        if m is None:
            return None
        return c, r, a0, sw, min(self._slot_half(abs(_len(m - c) - r)), r - 0.01)

    def _slot_lines(self, c1, c2, r):
        """(a, b, d, e): the corners where the sides meet the ends: a…b on one side, d…e back on the other."""
        u = (c2 - c1) / _len(c2 - c1)
        n = P(-u.y(), u.x()) * r
        return c1 + n, c2 + n, c2 - n, c1 - n

    def _slot_box(self):
        line, curve = self._slot_now(), self._slot_arc_now()
        r = line[2] if line else curve[4] if curve else None
        if r is None:
            return []
        self.box_h.show_value(2 * r)
        at = self.to_screen(self._rect_target())
        self.box_h.move(int(at.x() + 12), int(at.y() + 12))
        return [self.box_h]

    def _slot_next_click(self):
        """Arc slots: the arc is settled, the width comes."""
        arc = self._arc_now()
        if arc is None:
            return
        self.slot_arc = (P(arc[0]), arc[1], arc[2], arc[3])
        self.rect_pts.append(_round(arc[0], arc[1], arc[2] + arc[3]))
        for box in self.shape_boxes():
            box.reset()
            box.hide()
        self.prompt.emit(self._tool_name() + "  " + SLOT_PROMPTS[self.tool][3] + "  ·  right-click gives it up")
        self._refresh()

    def _slot_flags(self):
        """The new lines' and arcs' construction flags fit the lists again."""
        self.constr = (self.constr + [self.construction] * len(self.lines))[:len(self.lines)]
        self.arc_constr = (self.arc_constr + [self.construction] * len(self.arcs))[:len(self.arcs)]

    def _new_point(self, p):
        self.pts.append(P(p))
        return len(self.pts) - 1

    def _slot_finish(self):
        now = self._slot_now()
        curve = self._slot_arc_now()
        if now is None and curve is None:
            return
        st = self._state()
        self.constr = (self.constr + [False] * len(self.lines))[:len(self.lines)]
        self.arc_constr = (self.arc_constr + [False] * len(self.arcs))[:len(self.arcs)]
        if now is not None:
            self._slot_line_finish(*now)
        else:
            self._slot_arc_finish(*curve)
        self.steps.append(("state", st, len(self.lines), len(self.arcs)))
        self._shape_done("slot " + self.tool)

    def _slot_line_finish(self, c1, c2, r):
        ic1, ic2 = self._point_index(c1), self._point_index(c2)
        ia, ib, id_, ie = [self._new_point(q) for q in self._slot_lines(c1, c2, r)]
        self.lines += [(ia, ib), (id_, ie)]
        self.arcs += [(ic1, ia, ie), (ic2, id_, ib)]
        self._slot_flags()
        self.lines.append((ic1, ic2))  # the centre line, dashed, with a point in its middle: something to tie to (the user)
        self.constr.append(True)
        centre = len(self.lines) - 1
        im = self._new_point((c1 + c2) * 0.5)
        self._add_tie("mid", [im], centre)
        self._add_tie("slot_end", [ic1, ia, ie])
        self._add_tie("slot_end", [ic2, ib, id_])
        self._add_tie("con_perp", [ia, ie, ic1, ic2])
        self._add_tie("con_perp", [ib, id_, ic1, ic2])
        self._add_tie("con_eq_r", [ic1, ia, ic2, ib])
        d = c2 - c1
        if abs(d.y()) < 1e-9:
            self.relations.append(("h", centre, None, None))
        elif abs(d.x()) < 1e-9:
            self.relations.append(("v", centre, None, None))
        self._tie_shape((ic1, ic2, im))

    def _slot_arc_finish(self, c, r, a0, sw, w):
        ic = self._point_index(c)
        a1 = a0 + sw
        s_, e_ = self._point_index(_round(c, r, a0)), self._point_index(_round(c, r, a1))
        os_, oe = self._new_point(_round(c, r + w, a0)), self._new_point(_round(c, r + w, a1))
        is_, ie = self._new_point(_round(c, r - w, a0)), self._new_point(_round(c, r - w, a1))
        self.arcs += [(ic, os_, oe), (ic, is_, ie)]
        if self.tool == "slot_arc":
            self.arcs += [(e_, oe, ie), (s_, is_, os_)]
        else:
            self.lines += [(oe, ie), (is_, os_)]
        self._slot_flags()
        self.arcs.append((ic, s_, e_))  # the middle line, dashed
        self.arc_constr.append(True)
        self._add_tie("slot_end", [s_, os_, is_])
        self._add_tie("slot_end", [e_, oe, ie])
        self._add_tie("slot_ray", [ic, os_, is_])
        self._add_tie("slot_ray", [ic, oe, ie])
        self._add_tie("con_eq_r", [s_, os_, e_, oe])
        self._tie_shape((ic, s_, e_))
        log.say("arc slot", self.tool, "radius", r, "sweep", sw, "half width", w)

    def _slot_rounds(self, line):
        """A straight slot's side or centre line: [centre, rim, centre, rim] of its two ends; None for any other line."""
        ends = [t for t in self.ties if t["kind"] == "slot_end" and set(self.lines[line]) & set(t["pts"])]
        if len(ends) != 2:
            return None
        return [ends[0]["pts"][0], ends[0]["pts"][1], ends[1]["pts"][0], ends[1]["pts"][1]]

    def _slot_dim_holds(self, pts):
        """A radius set on an arc slot: (points that stay, what else holds). The ring's centre never moves (the user); an
        end's radius changes the width round the middle arc; the outer or inner one leaves the other as it is."""
        rays = [t["pts"] for t in self.ties if t["kind"] == "slot_ray"]
        for first, second in zip(rays[::2], rays[1::2]):
            ic, os_, is_ = first
            oe, ie = second[1:]
            ends = [t["pts"][0] for t in self.ties if t["kind"] == "slot_end" and t["pts"][1] in (os_, oe)]
            c, rim = pts[0], pts[1]
            if c in ends:
                return [ic, *ends], []
            if c != ic:
                continue
            hold = lambda k: [sk.radius_is(ic, k, _len(self.pts[k] - self.pts[ic]))]
            if rim in (os_, oe):
                return [ic], hold(is_)
            if rim in (is_, ie):
                return [ic], hold(os_)
            if rim in ends:
                return [ic], self._hold_slots(())
        return [], []

    def _slot_end_at(self, k):
        """The arc slot end this point belongs to: dict(ic, points of that end, the other end's middle, first); or None."""
        rays = [t["pts"] for t in self.ties if t["kind"] == "slot_ray"]
        for at, (ic, o, i) in enumerate(rays):
            mid = next((t["pts"][0] for t in self.ties if t["kind"] == "slot_end" and t["pts"][1:] == [o, i]), None)
            if mid is not None and k in (o, i, mid):
                other = rays[at + 1 if at % 2 == 0 else at - 1]
                far = next(t["pts"][0] for t in self.ties if t["kind"] == "slot_end" and t["pts"][1:] == other[1:])
                return dict(ic=ic, end=(mid, o, i), far=far, first=at % 2 == 0)
        return None

    def _slot_carried(self, k):
        """An arc slot's centre takes the whole slot along; any point of an end takes that end."""
        got = self._slot_end_at(k)
        if got is not None:
            return got["end"]
        corners = {q for t in self.ties if t["kind"] == "slot_ray" and t["pts"][0] == k for q in t["pts"][1:]}
        ends = {t["pts"][0] for t in self.ties if t["kind"] == "slot_end" and set(t["pts"][1:]) & corners}
        return tuple(corners | ends)

    def _slot_turn(self, k, m, was):
        """A point of an arc slot's end pulled: that end goes round the ring, the radii and the width stay."""
        got = self._slot_end_at(k)
        if got is None:
            return None
        c = was[got["ic"]]
        th = math.atan2((m - c).y(), (m - c).x())
        far = math.atan2((was[got["far"]] - c).y(), (was[got["far"]] - c).x())
        sweep = (far - th if got["first"] else th - far) % (2 * math.pi)
        if not 0.05 < sweep < 2 * math.pi - 0.05:
            return {q: P(self.pts[q]) for q in got["end"]}  # it does not go past the other end
        radius = _len(was[got["end"][0]] - c)
        half = _len(was[got["end"][1]] - was[got["end"][0]])
        here = _round(c, radius, th)
        if _len(here - was[got["far"]]) < 2 * half + 0.1:  # the ends do not run into each other
            return {q: P(self.pts[q]) for q in got["end"]}
        mark = round(math.degrees(sweep) / 15) * 15
        off = math.radians(mark) - sweep
        if 0 < mark < 360 and abs(off) * radius * self.scale <= SNAP_PX:  # every 15°; between them it stops anywhere (the user)
            th += -off if got["first"] else off
            self.hint = dict(kind="sweep", label="%d°" % mark, at=_round(c, radius, th), ref=None, guides=[])
        elif self.hint is not None and self.hint["kind"] == "sweep":
            self.hint = None
        return {q: _round(c, _len(was[q] - c), th) for q in got["end"]}

    def slot_widest(self, end_point):
        """The biggest half width of the arc slot this end belongs to: the hole in its middle and the room between its two
        ends stay; None: no arc slot."""
        got = self._slot_end_at(end_point)
        if got is None:
            return None
        c, s_, e_ = self.pts[got["ic"]], self.pts[got["end"][0]], self.pts[got["far"]]
        return max(LEAST, min(_len(s_ - c) - 0.1, _len(s_ - e_) / 2 - 0.05))

    def _slot_long(self, i):
        """Arc i as a long arc of an arc slot: dict(which: outer, inner, middle; ic; outer, inner, middle points); or None."""
        arc = self.arcs[i]
        rays = [t["pts"] for t in self.ties if t["kind"] == "slot_ray"]
        for first, second in zip(rays[::2], rays[1::2]):
            ic, os_, is_ = first
            oe, ie = second[1:]
            mids = [next(t["pts"][0] for t in self.ties if t["kind"] == "slot_end" and t["pts"][1:] == ray[1:]) for ray in (first, second)]
            if arc[0] != ic:
                continue
            which = "outer" if arc[1:] == (os_, oe) else "inner" if arc[1:] == (is_, ie) else "middle" if arc[1:] == tuple(mids) else None
            if which:
                return dict(which=which, ic=ic, outer=(os_, oe), inner=(is_, ie), middle=tuple(mids))
        return None

    def _slot_long_targets(self, d, m, how):
        """A long arc pulled. how 0: only it (the width changes, the other long arc stays); 1: the width stays, all goes along;
        2: the slot moves."""
        long, was = d["long"], d["was"]
        if how == 2:
            step = m - d["at"]
            return {k: was[k] + step for k in self._slot_group(long["outer"])}
        c = was[long["ic"]]
        out_r, in_r = _len(was[long["outer"][0]] - c), _len(was[long["inner"][0]] - c)
        pulled = long["which"]
        to = _len(m - c)
        chord = _len(was[long["middle"][0]] - was[long["middle"][1]])  # the two ends stay apart
        if how == 0 and pulled == "outer":
            out_r = min(max(to, in_r + 0.2), in_r + chord * 0.98)
        elif how == 0 and pulled == "inner":
            in_r = max(min(max(to, 0.1), out_r - 0.2), out_r - chord * 0.98)
        else:
            half = (out_r - in_r) / 2
            mid = {"outer": to - half, "inner": to + half, "middle": to}[pulled]
            mid = max(mid, half + 0.1)
            out_r, in_r = mid + half, mid - half
        def round_on(k, radius):
            return c + (was[k] - c) / _len(was[k] - c) * radius
        out = {long["ic"]: P(c)}
        for k in long["outer"]:
            out[k] = round_on(k, out_r)
        for k in long["inner"]:
            out[k] = round_on(k, in_r)
        for k in long["middle"]:
            out[k] = round_on(k, (out_r + in_r) / 2)
        return out

    def _slot_group(self, pts):
        """All the points of the slot these belong to."""
        group = set(pts)
        grown = True
        while grown:
            grown = False
            for t in self.ties:
                if t["kind"] in ("slot_end", "slot_ray", "con_eq_r", "poly_of") and group & set(t["pts"]) and not set(t["pts"]) <= group:
                    group |= set(t["pts"])
                    grown = True
        return group

    def _slot_flat_end(self, pts):
        """This line is a flat end of an arc slot."""
        got = self._slot_end_at(pts[0]) if len(pts) == 2 else None
        return got is not None and pts[1] in got["end"]

    def _hold_slots(self, pulled, round_end=False):
        """A slot keeps its width unless a side or a corner of it is what is pulled; a round end takes both ends."""
        out = []
        if round_end:
            pulled = self._slot_group(pulled)
        for t in self.ties:
            if t["kind"] == "slot_end" and not set(t["pts"][1:]) & set(pulled):
                s_, a, _b = t["pts"]
                out.append(sk.radius_is(s_, a, _len(self.pts[a] - self.pts[s_])))
        return out

    def _slot_stay(self, pulled, line=True):
        """A side pulled: the slot widens round its centre line, which stays where it is."""
        if line and self._slot_flat_end(tuple(pulled)):
            return {}
        return {t["pts"][0]: P(self.pts[t["pts"][0]]) for t in self.ties
                if t["kind"] == "slot_end" and set(t["pts"][1:]) & set(pulled)}

    def _paint_slot(self, g, pen):
        """The slot being drawn."""
        g.setBrush(QtCore.Qt.NoBrush)
        now, curve = self._slot_now(), self._slot_arc_now()
        if self.tool in SLOT_LINE and self._rect_stage() == 1:
            p, end = self.rect_pts[0], self._rect_side_end()  # from the centre it goes both ways from the start (the user)
            g.drawLine(self.to_screen(p * 2 - end if self.tool == "slot_center" else p), self.to_screen(end))
        if now is not None:
            c1, c2, r = now
            a, b, d, e = self._slot_lines(c1, c2, r)
            th = math.atan2((c2 - c1).y(), (c2 - c1).x())
            g.drawLine(self.to_screen(a), self.to_screen(b))
            g.drawLine(self.to_screen(d), self.to_screen(e))
            g.drawPolyline(self._arc_poly(c1, r, th + math.pi / 2, math.pi))
            g.drawPolyline(self._arc_poly(c2, r, th - math.pi / 2, math.pi))
            g.setPen(QtGui.QPen(pen.color(), 1, QtCore.Qt.DashLine))
            g.drawLine(self.to_screen(c1), self.to_screen(c2))
        if curve is not None:
            c, r, a0, sw, w = curve
            a1 = a0 + sw
            g.drawPolyline(self._arc_poly(c, r + w, a0, sw))
            g.drawPolyline(self._arc_poly(c, r - w, a0, sw))
            if self.tool == "slot_arc":
                g.drawPolyline(self._arc_poly(_round(c, r, a1), w, a1, math.pi))
                g.drawPolyline(self._arc_poly(_round(c, r, a0), w, a0 + math.pi, math.pi))
            else:
                g.drawLine(self.to_screen(_round(c, r + w, a1)), self.to_screen(_round(c, r - w, a1)))
                g.drawLine(self.to_screen(_round(c, r + w, a0)), self.to_screen(_round(c, r - w, a0)))
