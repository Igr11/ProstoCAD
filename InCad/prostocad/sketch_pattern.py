"""Sketch patterns. A pattern is one thing: its window stays open while it is made and shows the copies at once, Edit Pattern
opens it again, Delete Pattern takes the copies and leaves the original. A copy is tied to its original point by point and
brings no dimensions; the spacing is a parameter of the sketch, so formulas can use it. A record tie (pat_rec) on the original's
points keeps what the window said, a pat_copy tie each copy's points and number; deleting a copy leaves a pattern with a gap,
and the next Edit fills it."""
import math

from PySide6 import QtCore, QtGui

from prostocad import log, units

P = QtCore.QPointF
ASK = {"geometry": "Pattern  Click the shapes to repeat  ·  Cmd+click takes one off, a box takes all inside",
       "dir1": "Pattern  Click a line or an axis: the first direction", "dir2": "Pattern  Click a line or an axis: the second direction",
       "from1": "Pattern  Click the first point", "to1": "Pattern  Click the last point",
       "path": "Pattern  Click the curve: lines, arcs or a spline joined end to end", "start": "Pattern  Click on the curve where the copies start from",
       "base": "Pattern  Click the point of the shape that sits on the curve",
       "axis": "Pattern  Click the centre: a point, a circle, or an arc for copies along it",
       "excluded": "Pattern  Click a copy to leave it out, a dashed one to bring it back"}


def _len(p):
    return math.hypot(p.x(), p.y())


def _dot(a, b):
    return a.x() * b.x() + a.y() * b.y()


class PatternTools:
    """Mixed into SketchView."""

    # ----- the window's run -----
    def start_pattern(self, kind, values, edit=None):
        items = [("line", i) for i in sorted(self.sel_lines)] + [("arc", i) for i in sorted(self.sel_arcs)]
        items += [("point", k) for k in sorted(self.sel_pts & self._pat_lone())]
        self._end_chain()
        self._rect_reset()
        self.tool = "sk_pattern"
        self.just_done = False
        self.pat = dict(kind=kind, st=self._state(), was=self._state(), items=[q for q in items if not self._pat_is_copy(q)], v=dict(values),
                        dir1=("axis", "x"), dir2=("axis", "y"), from1=None, to1=None, axis=None if kind == "circ" else ("origin", None), path=[], base=None, start=None, excluded=[], edit=edit, active="geometry", hover=None,
                        slots={}, say=None)
        self._clear_choice()
        self.setFocus()
        self.prompt.emit(ASK["geometry"])
        if self.pat["items"]:
            self._pat_redo()
        self._refresh()

    def pat_set(self, values):
        self.pat["v"].update(values)
        self._pat_redo()

    def pat_active(self, key):
        self.pat["active"] = key
        self.prompt.emit(ASK.get(key, ""))
        self.update()

    def _pat_lone(self):
        return {k for t in self.ties if t["kind"] == "sk_point" for k in t["pts"]}

    def _pat_is_copy(self, item):
        points = set(self._pat_points(item))
        return any(t["kind"] == "pat_copy" and points <= set(t["pts"]) for t in self.ties)

    def _pat_points(self, item):
        kind, i = item
        return self.lines[i] if kind == "line" else self.arcs[i] if kind == "arc" else (i,)

    def _pat_way(self, pick):
        """The unit way of a direction pick; None if its line is gone."""
        if pick[0] == "axis":
            return P(1, 0) if pick[1] == "x" else P(0, 1)
        a, b = [self.pts[k] for k in self.lines[pick[1]]]
        return (b - a) / (_len(b - a) or 1.0)

    def _pat_limit(self, pick):
        """(a point of it, its normal or None)."""
        kind, i = pick
        if kind == "axis":
            return P(0, 0), P(0, 1) if i == "x" else P(1, 0)
        if kind == "origin":
            return P(0, 0), None
        if kind == "point":
            return self.pts[i], None
        a, b = [self.pts[k] for k in self.lines[i]]
        d = b - a
        return a, P(-d.y(), d.x()) / (_len(d) or 1.0)

    def _pat_extent(self, items, way):
        lo = hi = None
        for item in items:
            kind, i = item
            if kind == "arc":
                c, r = self.arc_shape(i)[:2]
                reach = [_dot(c, way) - r, _dot(c, way) + r]
            else:
                reach = [_dot(self.pts[k], way) for k in self._pat_points(item)]
            lo = min(reach) if lo is None else min(lo, *reach)
            hi = max(reach) if hi is None else max(hi, *reach)
        return lo, hi

    def _pat_equal(self, items, count):
        """(spacing, way) that puts the first copy as far from From as the last is from To; or a refusal as text."""
        r = self.pat
        if r["from1"] is None or r["to1"] is None:
            return "Pick the From and To limits: where the first and last copies are spaced from"
        p0, n0 = self._pat_limit(r["from1"])
        p1, n1 = self._pat_limit(r["to1"])
        way = n0 if n0 is not None else n1 if n1 is not None else p1 - p0
        if _len(way) < 1e-9:
            return "From and To coincide: pick two different limits"
        way = way / _len(way)
        t0, t1 = _dot(p0, way), _dot(p1, way)
        if abs(t1 - t0) < 1e-9:
            return "From and To coincide: pick two different limits"
        if t1 < t0:
            way, t0, t1 = way * -1, -t0, -t1
        lo, hi = self._pat_extent(items, way)
        width, margin, room = hi - lo, lo - t0, t1 - t0
        if margin < -1e-6:
            return "The shapes lie %s behind From: pick the other end as From, or move the limit" % units.short(-margin)
        spacing = (t1 - margin - hi) / (count - 1)
        if spacing < width - 1e-6:
            return "There is no room: %d copies need %s, only %s lie between the limits" % (count, units.short(count * width + 2 * margin), units.short(room))
        return spacing, way

    def _pat_centre(self):
        a = self.pat["axis"]
        return P(0, 0) if a is None or a[0] == "origin" else P(self.pts[a[1]])

    def _pat_samples(self, items):
        out = []
        for item in items:
            out += [self.pts[item[1]]] if item[0] == "point" else self._off_seg(item, True)["poly"]
        return out

    def _pat_limit_point(self, pick):
        """A From or To pick round the axis: the point, or the middle of a line."""
        kind, i = pick
        if kind == "point":
            return self.pts[i]
        if kind == "origin":
            return P(0, 0)
        if kind == "axis":
            return P(1, 0) if i == "x" else P(0, 1)
        a, b = [self.pts[k] for k in self.lines[i]]
        return (a + b) * 0.5

    def _pat_middle(self):
        """The middle of the shapes' box."""
        points = self._pat_samples(self.pat["items"])
        xs, ys = [q.x() for q in points], [q.y() for q in points]
        return P((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)

    def _pat_between(self, count):
        """(step in degrees, the first copy's turn): the middles of the copies go evenly from From to To, along the arc if one
        was clicked, else the short way round the centre; where a copy would stand on the original, the original is it."""
        r = self.pat
        if r["from1"] is None:
            return "Click the first point"
        if r["to1"] is None:
            return "Click the last point"
        c = self._pat_centre()
        ang = lambda q: math.atan2((q - c).y(), (q - c).x())
        a0, a1, mid = ang(self._pat_limit_point(r["from1"])), ang(self._pat_limit_point(r["to1"])), ang(self._pat_middle())
        if r.get("along"):
            ia = self.arcs[r["along"][1]][1]
            start = ang(self.pts[ia])
            place = lambda t: (t - start) % (2 * math.pi)
            sweep = place(a1) - place(a0)
            base = place(a0) - place(mid)
        else:
            sweep = math.remainder(a1 - a0, 2 * math.pi)
            base = a0 - mid
        if abs(sweep) < 1e-6:
            return "From and To are the same place: click two different points"
        return math.degrees(sweep / (count - 1)), base

    def _pat_round(self):
        """Circular: [(number, turn in radians, k, 0)] and what the record needs, or a refusal as text."""
        r = self.pat
        v = r["v"]
        count = int(round(v["count1"]))
        if count < 2:
            return "Use a count of 2 or more"
        if r["axis"] is None:
            return "Click the centre"
        c = self._pat_centre()
        if any(_len(self.pts[k] - c) < 1e-7 for q in r["items"] for k in self._pat_points(q)):
            return "Pick the axis point away from the shapes being repeated"
        flip = bool(v.get("flip1"))
        base = 0.0
        if v.get("mode") == "between":
            got = self._pat_between(count)
            if isinstance(got, str):
                return got
            total, base = got
            step, flip = total, False
        else:
            total = v["sweep"]  # from the start: plus is counterclockwise, minus clockwise
            if v.get("fit") == "between":
                step = total
            else:
                step = total / count if abs(abs(total) - 360.0) < 1e-9 else total / (count - 1)
        if abs(step) < 1e-9:
            return "The copies would land on the original: give the pattern an angle"
        sign = -1.0 if flip else 1.0
        r["info"] = dict(total=total, divisor=total / step, sign=sign, two=False, base=base)
        turns = [(k + 1, base + math.radians(sign * k * step), k, 0) for k in range(count)]
        return [q for q in turns if abs(math.remainder(q[1], 2 * math.pi)) > 1e-6]

    # ----- along a path -----
    def _pat_chain(self, item=None):
        """The curve from one of its shapes, without the shapes being repeated."""
        r = self.pat
        apart = set(r["items"])
        return self._off_outline(item or r["path"][0], lambda q: q not in apart)

    def _pat_line(self):
        """The path as (points, the length up to each), walked the way the pattern runs."""
        r = self.pat
        out = self._pat_chain()
        poly = []
        for seg in out["segs"]:
            poly += seg["poly"] if not poly else seg["poly"][1:]
        if r["v"].get("flip1") and r["v"].get("mode") != "both":
            poly = poly[::-1]
        run = [0.0]
        for a, b in zip(poly, poly[1:]):
            run.append(run[-1] + _len(b - a))
        return poly, run

    @staticmethod
    def _pat_at_length(poly, run, s):
        """(the point, the unit way) at the length s along it."""
        s = max(0.0, min(s, run[-1]))
        k = next((i for i in range(1, len(run)) if run[i] >= s - 1e-12), len(run) - 1)
        span = run[k] - run[k - 1]
        u = (poly[k] - poly[k - 1]) / (span or 1.0)
        return poly[k - 1] + u * (s - run[k - 1]), u

    @staticmethod
    def _pat_station(poly, run, q):
        """The length along it of its point nearest q."""
        best = None
        for k in range(1, len(poly)):
            a, u = poly[k - 1], poly[k] - poly[k - 1]
            n2 = _dot(u, u) or 1.0
            t = max(0.0, min(1.0, _dot(q - a, u) / n2))
            d = _len(a + u * t - q)
            if best is None or d < best[0]:
                best = (d, run[k - 1] + t * (run[k] - run[k - 1]))
        return best[1]

    def _pat_base_point(self):
        r = self.pat
        if r["base"] is not None:
            return self.pts[r["base"]]
        return self.pts[self._pat_points(r["items"][0])[0]]

    def _pat_along(self):
        """[(number, (turn, tx, ty), k, 0)] or a refusal as text."""
        r = self.pat
        v = r["v"]
        if not r["path"]:
            return "Click the curve"
        if self._pat_chain()["closed"]:
            return "The path is closed: pick an open chain"
        count = int(round(v["count1"]))
        if count < 2:
            return "Use a count of 2 or more"
        poly, run = self._pat_line()
        total = run[-1]
        both = v.get("mode") == "both"
        fit = v.get("fit") == "fit"
        spacing = v.get("spacing1", 10.0)
        if not fit and spacing <= 0:
            return "Give the pattern a spacing"
        bp = self._pat_base_point()
        samples = self._pat_samples(r["items"])
        touches = min(_len(q - self._pat_at_length(poly, run, self._pat_station(poly, run, q))[0]) for q in samples) < 0.01
        if not both and not touches:  # the shape stands off the curve: the copies start at the curve's end nearest it; Flip: the far end
            near_end = _len(bp - poly[0]) <= _len(bp - poly[-1])
            if near_end == bool(v.get("flip1")):
                poly = poly[::-1]
                run = [run[-1] - t for t in reversed(run)]
        s0 = self._pat_station(poly, run, bp)
        if both:
            if fit:
                stations = [total * k / (count - 1) for k in range(count)]
            else:
                mid = total / 2 if r["start"] is None else self._pat_station(poly, run, r["start"])  # the curve's middle until one is clicked
                stations = [mid + (k - (count - 1) / 2) * spacing for k in range(count)]
                if stations[0] < -1e-6 or stations[-1] > total + 1e-6:
                    return "No room: %d copies at %s need %s, the path has %s each side" % (
                        count, units.short(spacing), units.short((count - 1) * spacing / 2), units.short(min(mid, total - mid)))
            numbered = list(enumerate(stations))
        elif not touches:
            step = total / (count - 1) if fit else spacing
            if (count - 1) * step > total + 1e-6:
                return "No room: %d copies at %s need %s, the path has %s" % (count, units.short(step), units.short((count - 1) * step), units.short(total))
            numbered = [(k, k * step) for k in range(count)]
            s0 = 0.0
        else:
            room = total - s0
            if fit:
                if room < 1e-6:
                    return "No room: the shape sits at the end of the path (flip the path)"
                step = room / (count - 1)
            else:
                step = spacing
                if (count - 1) * step > room + 1e-6:
                    return "No room: %d copies at %s need %s, the path has %s after the shape" % (count, units.short(step), units.short((count - 1) * step), units.short(room))
            numbered = [(k, s0 + k * step) for k in range(1, count)]
        seat = both or not touches or r["base"] is not None
        near, way0 = self._pat_at_length(poly, run, s0)
        anchor = bp if seat else near
        follow = v.get("orient") == "follow"
        out = []
        for k, s_ in numbered:
            at, way = self._pat_at_length(poly, run, s_)
            a = 0.0
            if follow:
                a = math.remainder(math.atan2(way.y(), way.x()) - math.atan2(way0.y(), way0.x()), 2 * math.pi)
            co, si = math.cos(a), math.sin(a)
            place = (a, at.x() - (co * anchor.x() - si * anchor.y()), at.y() - (si * anchor.x() + co * anchor.y()))
            if abs(a) < 1e-12 and math.hypot(place[1], place[2]) < 1e-9:
                continue  # it would stand on the original
            out.append((k + 1, place, k, 0))
        r["info"] = dict(two=False, sign=1.0)
        return out

    def _pat_move(self, place):
        """What a copy's place does to a point."""
        if self.pat["kind"] == "path":
            co, si = math.cos(place[0]), math.sin(place[0])
            return lambda q: P(co * q.x() - si * q.y() + place[1], si * q.x() + co * q.y() + place[2])
        if self.pat["kind"] == "circ":
            c, co, si = self._pat_centre(), math.cos(place), math.sin(place)
            return lambda q: c + P(co * (q - c).x() - si * (q - c).y(), si * (q - c).x() + co * (q - c).y())
        return lambda q: q + place

    def _pat_places(self):
        """[(number, offset, k, j)] and what the record needs, or a refusal as text."""
        r = self.pat
        v = r["v"]
        if r["kind"] == "circ":
            return self._pat_round()
        if r["kind"] == "path":
            return self._pat_along()
        count = int(round(v["count1"]))
        if count < 2:
            return "Use a count of 2 or more"
        sign = -1.0 if v.get("flip1") else 1.0
        two = False
        count2, s2, way2 = 1, 0.0, P(0, 1)
        if v.get("fit1") == "equal":
            got = self._pat_equal(r["items"], count)
            if isinstance(got, str):
                return got
            s1, way = got
            sign = 1.0
            r["v"]["spacing1"] = s1
        else:
            s1, way = v["spacing1"], self._pat_way(r["dir1"])
            two = bool(v.get("two"))
            if two:
                count2, s2, way2 = max(1, int(round(v["count2"]))), v["spacing2"], self._pat_way(r["dir2"])
        out = []
        for j in range(count2):
            for k in range(count):
                if k == 0 and j == 0:
                    continue
                n = j * count + k + 1
                off = way * (sign * k * s1) + way2 * (j * s2)
                if _len(off) < 1e-9:
                    return "The copies would land on the original: give the pattern a spacing"
                out.append((n, off, k, j))
        r["info"] = dict(s1=s1, s2=s2, a1=math.atan2(way.y(), way.x()), a2=math.atan2(way2.y(), way2.x()), sign=sign, two=two)
        return out

    def _pat_redo(self):
        """The copies again from the sketch as the run began."""
        r = self.pat
        self._restore(r["st"])
        r["st"] = self._state()
        r["hover"], r["slots"] = None, {}
        if r["items"]:
            places = self._pat_places()
            if isinstance(places, str):
                self.prompt.emit("Pattern  " + places)
                r["say"] = places
                log.say("pattern refused:", places)
            else:
                r["say"] = None
                name = r["edit"] or self._pat_new_name()
                r["name"] = name
                for n, off, k, j in places:
                    if n in r["excluded"]:
                        continue
                    self._pat_copy(r["items"], off, n, k, j, name)
        self.pattern_changed.emit()
        self.changed.emit()
        self._refresh()

    def _pat_copy(self, items, off, number, k, j, name):
        made = {}
        move, round_ = self._pat_move(off), self.pat["kind"] == "circ"
        axis = self.pat["axis"] or ("origin", None)

        def twin(p, tie=True):
            if p not in made:
                self.pts.append(move(self.pts[p]))
                made[p] = len(self.pts) - 1
                if tie and round_:
                    self._add_tie("pat_rot", ([] if axis[0] == "origin" else [axis[1]]) + [p, made[p]], off)
                    self.ties[-1].update(pat=name, pat_k=(k, j))
                elif tie and self.pat["kind"] == "path":
                    self._add_tie("pat_rig", [p, made[p]], tuple(off))
                    self.ties[-1].update(pat=name, pat_k=(k, j))
                elif tie:
                    self._add_tie("pat_xy", [p, made[p]], (off.x(), off.y()))
                    self.ties[-1].update(pat=name, pat_k=(k, j))
            return made[p]

        for kind, i in items:
            if kind == "line":
                a, b = self.lines[i]
                self.constr = (self.constr + [False] * len(self.lines))[:len(self.lines)] + [self.is_construction(i)]
                self.lines.append((twin(a), twin(b)))
            elif kind == "arc":
                c, a, b = self.arcs[i]
                faint = self.is_arc_construction(i)
                if a == b:
                    rim = twin(a, tie=False)
                    arc = (twin(c), rim, rim)
                    self._add_tie("con_eq_r", [c, a, arc[0], rim])
                    self.ties[-1]["pat"] = name
                else:
                    arc = (twin(c), twin(a), twin(b))
                self.arc_constr = (self.arc_constr + [False] * len(self.arcs))[:len(self.arcs)] + [faint]
                self.arcs.append(arc)
            else:
                self._add_tie("sk_point", [twin(i)])
        self._add_tie("pat_copy", list(made.values()), dict(name=name, number=number))

    def _pat_new_name(self):
        used = {t["ref"]["name"] for t in self.ties if t["kind"] == "pat_rec"} | {t["ref"]["name"] for t in self.ties if t["kind"] == "pat_copy"}
        n = 1
        while "Pattern%d" % n in used:
            n += 1
        return "Pattern%d" % n

    # ----- clicks -----
    def _pat_shape_at(self, s):
        """The original shapes a click here takes; None."""
        r = self.pat
        what = self._pick(s, ("point", "line", "arc"))
        if what is None:
            return None
        if what[0] == "point":
            if what[1] in self._pat_lone() and what[1] < len(r["st"]["pts"]):
                return [("point", what[1])]
            what = self._pick(s, ("line", "arc"))
            if what is None:
                return None
        known = len(r["st"]["lines"]) if what[0] == "line" else len(r["st"]["arcs"])
        if what[1] >= known:
            return None  # a copy of this run
        if self._pat_is_copy(tuple(what)):
            self.prompt.emit("Pattern  Pattern the original, not a copy")
            return None
        return list(self._off_outline(tuple(what))["items"]) if what[0] in ("line", "arc") else None

    def _pat_mark(self):
        """A click's step, for Cmd+Z while the window is open."""
        r = self.pat
        r.setdefault("back", []).append({k: (list(r[k]) if isinstance(r[k], list) else r[k]) for k in ("items", "excluded", "dir1", "dir2", "from1", "to1", "axis")})

    def pattern_undo(self):
        """Cmd+Z with the window open: its last click goes."""
        r = self.pat
        if r.get("back"):
            r.update(r["back"].pop())
            self._pat_redo()

    def pattern_press(self, s, adding):
        r = self.pat
        key = "geometry" if adding else r["active"]
        self._pat_mark()
        if key == "geometry":
            got = self._pat_shape_at(s)
            if got is None:
                if self._pick(s, ("line", "arc")) is None:
                    at = self.to_model(s)
                    self.boxing = dict(at=at, to=P(at), touch=False, add=False, lines=set(), arcs=set())
                return
            got = [q for q in got if q[0] in ("line", "arc", "point")]
            if all(q in r["items"] for q in got):
                r["items"] = [q for q in r["items"] if q not in got]
            else:
                r["items"] += [q for q in got if q not in r["items"]]
            self._pat_redo()
            if r["kind"] == "circ" and r["items"] and r["axis"] is None and not adding:
                self.pattern_step.emit("axis")
            if r["kind"] == "path" and r["items"] and not r["path"] and not adding:
                self.pattern_step.emit("path")
        elif key == "path":
            what = self._pick(s, ("line", "arc", "ell", "spl"))
            if what is None:
                return
            if self._pat_past(tuple(what)):
                return
            if tuple(what) in r["items"]:
                self.prompt.emit("Pattern  The path can't be part of the shapes to repeat")
                return
            r["path"] = list(self._pat_chain(tuple(what))["items"])
            self._pat_redo()
            if r["v"].get("mode") == "both" and r["v"].get("fit") != "fit":
                self.pattern_step.emit("start")
        elif key == "start":
            what = self._pick(s, ("point",))
            mine = what is not None and what[1] < len(r["st"]["pts"])  # a copy's point of this run is no place to start from
            r["start"] = P(self.pts[what[1]]) if mine else P(self.to_model(s))
            self._pat_redo()
        elif key == "base":
            what = self._pick(s, ("point",))
            if what is None or what[1] >= len(r["st"]["pts"]):  # any point: a corner, a rectangle's centre, a point of one's own
                self.prompt.emit("Pattern  Click a point")
                return
            r["base"] = what[1]
            self._pat_redo()
        elif key == "axis":
            go = "from1" if r["v"].get("mode") == "between" else None
            r["along"] = None
            at = self._dim_place_at(s)
            if at is not None and at[0] == "origin":
                r["axis"] = ("origin", None)
            else:
                what = self._pick(s, ("point", "arc"))
                if what is None:
                    return
                if what[0] == "arc":  # a circle gives its centre; an arc its centre and its ends: the copies lie along it
                    if what[1] >= len(r["st"]["arcs"]):
                        return
                    c, a, b = self.arcs[what[1]]
                    r["axis"] = ("point", c)
                    if a != b:
                        middle = self._pat_middle()
                        r["along"] = ("arc", what[1])
                        if _len(middle - self.pts[b]) < _len(middle - self.pts[a]):  # they run from the end the shapes stand nearer
                            a, b = b, a
                        r["from1"], r["to1"] = ("point", a), ("point", b)
                        r["v"]["mode"] = "between"
                        go = "from1"
                elif what[1] >= len(r["st"]["pts"]):
                    return
                else:
                    r["axis"] = tuple(what)
            self._pat_redo()
            if go:
                self.pattern_step.emit(go)
        elif key in ("dir1", "dir2", "from1", "to1"):
            at = self._dim_place_at(s)
            if at is not None and at[0] == "axis":
                r[key] = tuple(at)
            elif at is not None and key in ("from1", "to1"):
                r[key] = ("origin", None)
            else:
                what = self._pick(s, ("point", "line") if key in ("from1", "to1") else ("line",))
                if what is None or (what[0] == "line" and what[1] >= len(r["st"]["lines"])):
                    return
                r[key] = tuple(what)
            self._pat_redo()
            if key == "from1" and r["kind"] == "circ":
                self.pattern_step.emit("to1")
        elif key == "excluded":
            n = self._pat_number_at(s)
            if n is None:
                return
            if n in r["excluded"]:
                r["excluded"].remove(n)
            else:
                r["excluded"].append(n)
            self._pat_redo()

    def pattern_box_end(self, b):
        r = self.pat
        x0, x1 = sorted((b["at"].x(), b["to"].x()))
        y0, y1 = sorted((b["at"].y(), b["to"].y()))
        inside = lambda k: x0 <= self.pts[k].x() <= x1 and y0 <= self.pts[k].y() <= y1
        n_l, n_a = len(r["st"]["lines"]), len(r["st"]["arcs"])
        got = [("line", i) for i in range(n_l) if all(map(inside, self.lines[i])) and not self.is_construction(i)]
        got += [("arc", i) for i in range(n_a) if all(map(inside, self.arcs[i]))]
        got = [q for q in got if not self._pat_is_copy(q)]
        if got:
            r["items"] += [q for q in got if q not in r["items"]]
            self._pat_redo()

    def _pat_ghost(self, n):
        """Where left-out copy n would stand, as lines."""
        r = self.pat
        places = self._pat_places()
        if isinstance(places, str):
            return []
        off = next((o for m, o, _k, _j in places if m == n), None)
        if off is None:
            return []
        move = self._pat_move(off)
        return [[move(q) for q in self._off_seg(item, True)["poly"]] for item in r["items"] if item[0] != "point"]

    def _pat_number_at(self, s):
        """The copy at the screen point s: its number; None."""
        r = self.pat
        what = self._pick(s, ("point", "line", "arc"))
        if what is not None and what[0] != "point":
            points = set(self._pat_points(tuple(what)))
            for t in self.ties:
                if t["kind"] == "pat_copy" and t["ref"]["name"] == r.get("name") and points <= set(t["pts"]):
                    return t["ref"]["number"]
        for n in r["excluded"]:
            for poly in self._pat_ghost(n):
                if any(_len(self.to_screen(a) - s) <= 8 for a in poly):
                    return n
                for a, b in zip(poly, poly[1:]):
                    u = self.to_screen(b) - self.to_screen(a)
                    d2 = _dot(u, u) or 1.0
                    t_ = max(0.0, min(1.0, _dot(s - self.to_screen(a), u) / d2))
                    if _len(self.to_screen(a) + u * t_ - s) <= 8:
                        return n
        return None

    def _pat_past(self, item):
        """A shape this run made."""
        kind, i = item
        known = {"line": "lines", "arc": "arcs", "ell": "ells", "spl": "spls"}.get(kind)
        return known is not None and i >= len(self.pat["st"][known])

    def _pat_track(self, s):
        r = self.pat
        r["hover"] = self._pat_shape_at(s) if r["active"] == "geometry" else None
        want = {"axis": ("point", "arc"), "dir1": ("line",), "dir2": ("line",), "from1": ("point", "line"), "to1": ("point", "line"),
                "path": ("line", "arc", "ell", "spl"), "base": ("point",), "start": ("point",)}.get(r["active"])
        r["under"] = self._pick(s, want) if want else None  # what a click would take lights first
        if r["under"] is not None and self._pat_past(tuple(r["under"])):
            r["under"] = None

    # ----- done -----
    def pattern_done(self, what):
        """OK: the record and the parameters; Cancel: as it stood."""
        r = self.pat
        if what == "cancel" or not r["items"] or r.get("say"):
            self._restore(r["was"])
            self.pat = None
            self.changed.emit()
            self.stop_tool()
            return
        name = r.get("name") or self._pat_new_name()
        info = r["info"]
        wanted = (("angle", info["total"]),) if r["kind"] == "circ" else () if r["kind"] == "path" else (("spacing", info["s1"]), ("spacing2", info["s2"] if info["two"] else None))
        for key, value in wanted:
            pname = "%s_%s" % (name, key)
            q = next((q for q in self.params if q["name"] == pname), None)
            if value is None:
                self.params = [p for p in self.params if p is not q]
            elif q is None:
                self.params.append(dict(name=pname, expr=None, value=value))
            elif abs(q["value"] - value) > 1e-9:
                q.update(value=value, expr=None)
        points, places = [], {}

        def spot(k):
            if k not in places:
                places[k] = len(points)
                points.append(k)
            return places[k]

        shapes = [(q[0], *[spot(k) for k in self._pat_points(q)]) for q in r["items"]]
        pick = lambda p: None if p is None else ("line", *[spot(k) for k in self.lines[p[1]]]) if p[0] == "line" else ("arc", *[spot(k) for k in self.arcs[p[1]]]) \
            if p[0] == "arc" else ("point", spot(p[1])) if p[0] == "point" else (p[0], *[spot(k) for k in self._shape_points(p)]) if p[0] in ("ell", "spl") else p
        ref = dict(name=name, kind=r["kind"], shapes=shapes, values={k: v for k, v in r["v"].items() if not k.endswith("_expr")},
                   dir1=pick(r["dir1"]), dir2=pick(r["dir2"]), from1=pick(r["from1"]), to1=pick(r["to1"]), axis=pick(r["axis"]),
                   path=[pick(p) for p in r["path"]], base_pt=spot(r["base"]) if r["base"] is not None else None,
                   start=(r["start"].x(), r["start"].y()) if r["start"] is not None else None,
                   excluded=list(r["excluded"]), along=pick(r.get("along")), base=info.get("base", 0.0), a1=info.get("a1"), a2=info.get("a2"), sign=info["sign"], two=info["two"], divisor=info.get("divisor"))
        self._add_tie("pat_rec", points, ref)
        self.steps = list(r["was"]["steps"]) + [("state", r["was"], len(self.lines), len(self.arcs))]
        log.say("pattern", name, len(r["items"]), "shapes")
        self.pat = None
        self.changed.emit()
        self.stop_tool()

    # ----- afterwards -----
    def _pat_recs(self):
        return [t for t in self.ties if t["kind"] == "pat_rec"]

    def pat_follow(self):
        """The parameters changed: each copy stands where its spacing puts it now; a circle's rim goes with its centre."""
        rims = [(t["pts"][2], t["pts"][3], self.pts[t["pts"][3]] - self.pts[t["pts"][2]]) for t in self.ties if t["kind"] == "con_eq_r" and t.get("pat")]
        for rec in self._pat_recs():
            q = rec["ref"]
            if q["kind"] == "path":  # its copies stand by the curve, no parameter moves them
                continue
            if q["kind"] == "circ":
                total = next((p["value"] for p in self.params if p["name"] == q["name"] + "_angle"), None)
                if total is None:
                    continue
                self.ties_turned(q, total)
                continue
            s1 = next((p["value"] for p in self.params if p["name"] == q["name"] + "_spacing"), None)
            s2 = next((p["value"] for p in self.params if p["name"] == q["name"] + "_spacing2"), 0.0)
            if s1 is None:
                continue
            way, way2 = P(math.cos(q["a1"]), math.sin(q["a1"])), P(math.cos(q["a2"]), math.sin(q["a2"]))
            for t in self.ties:
                if t["kind"] == "pat_xy" and t.get("pat") == q["name"]:
                    k, j = t["pat_k"]
                    off = way * (q["sign"] * k * s1) + way2 * (j * s2)
                    t["ref"] = (off.x(), off.y())
                    self.pts[t["pts"][1]] = self.pts[t["pts"][0]] + off
        for c, rim, way in rims:
            self.pts[rim] = self.pts[c] + way

    def ties_turned(self, q, total):
        step = math.radians(total / q["divisor"]) * q["sign"]
        for t in self.ties:
            if t["kind"] == "pat_rot" and t.get("pat") == q["name"]:
                a = q.get("base", 0.0) + t["pat_k"][0] * step
                t["ref"] = a
                o, c_ = t["pts"][-2:]
                ax = self.pts[t["pts"][0]] if len(t["pts"]) == 3 else P(0, 0)
                co, si = math.cos(a), math.sin(a)
                d = self.pts[o] - ax
                self.pts[c_] = ax + P(co * d.x() - si * d.y(), si * d.x() + co * d.y())

    def pat_uses(self, name):
        return [t["ref"]["name"] for t in self._pat_recs() if name.startswith(t["ref"]["name"] + "_")]

    def _pat_at(self, s):
        """The pattern of what is at the screen point s, or of what is chosen."""
        what = self._pick(s, ("line", "arc"))
        items = [tuple(what)] if what is not None else [("line", i) for i in self.sel_lines] + [("arc", i) for i in self.sel_arcs]
        for item in items:
            points = set(self._pat_points(item))
            for t in self.ties:
                if t["kind"] in ("pat_copy", "pat_rec") and points <= set(t["pts"]):
                    return t["ref"]["name"]
        return None

    def pat_remove(self, name, keep_params=False):
        """The copies, their ties and the record go; the original stays."""
        copies = [t for t in self.ties if t["kind"] == "pat_copy" and t["ref"]["name"] == name]
        points = {k for t in copies for k in t["pts"]}
        self.ties = [t for t in self.ties if not (t.get("pat") == name or (t["kind"] in ("pat_copy", "pat_rec") and t["ref"]["name"] == name))]
        keep_l = [i for i, ln in enumerate(self.lines) if not set(ln) <= points]
        keep_a = [i for i, arc in enumerate(self.arcs) if not set(arc) <= points]
        self.constr = [c for i, c in enumerate((self.constr + [False] * len(self.lines))[:len(self.lines)]) if i in keep_l]
        self.arc_constr = [c for i, c in enumerate((self.arc_constr + [False] * len(self.arcs))[:len(self.arcs)]) if i in keep_a]
        ids = self._fit_ids()
        ids["line"] = [ids["line"][i] for i in keep_l]
        ids["arc"] = [ids["arc"][i] for i in keep_a]
        self.lines = [self.lines[i] for i in keep_l]
        self.arcs = [self.arcs[i] for i in keep_a]
        self.ties = [t for t in self.ties if not (t["kind"] == "sk_point" and set(t["pts"]) & points)]
        self._drop_lone_points()
        if not keep_params:
            self.params = [p for p in self.params if not p["name"].startswith(name + "_")]

    def delete_pattern(self, name):
        st = self._state()
        self.pat_remove(name)
        self.steps.append(("state", st, len(self.lines), len(self.arcs)))
        self._clear_choice()
        self.changed.emit()
        self._refresh()

    def pat_reopen(self, name, values):
        """Edit Pattern: the run again, from the record; a copy deleted by hand comes back. The original's points may be
        numbered anew when the copies go, so they are found again by where they stand."""
        rec = next(t for t in self._pat_recs() if t["ref"]["name"] == name)
        q = rec["ref"]
        was = self._state()
        spots = [P(self.pts[k]) for k in rec["pts"]]
        self.pat_remove(name, keep_params=True)
        find = lambda n: min(range(len(self.pts)), key=lambda k: _len(self.pts[k] - spots[n]))

        def shape(s_):
            kind, now = s_[0], [find(n) for n in s_[1:]]
            if kind == "line":
                return "line", next(i for i, ln in enumerate(self.lines) if set(ln) == set(now))
            if kind == "arc":
                return "arc", next(i for i, arc in enumerate(self.arcs) if arc[0] == now[0] and set(arc) == set(now))
            if kind == "ell":
                return "ell", next(i for i in range(len(self.ells)) if set(self._shape_points(("ell", i))) == set(now))
            if kind == "spl":
                return "spl", next(i for i in range(len(self.spls)) if set(self.spls[i][1]) == set(now))
            return "point", now[0]

        def back(p):
            if p is None or p[0] not in ("line", "point", "arc", "ell", "spl"):
                return p
            return shape(p) if p[0] != "point" else ("point", find(p[1]))

        items = [shape(s_) for s_ in q["shapes"]]
        picks = {k: back(q.get(k)) for k in ("dir1", "dir2", "from1", "to1", "axis")}
        self.sel_lines, self.sel_arcs, self.sel_pts = set(), set(), set()
        self.start_pattern(q["kind"], dict(q["values"], **values), edit=name)
        r = self.pat
        r["items"], r["was"], r["excluded"] = items, was, list(q["excluded"])
        if q.get("along"):
            r["along"] = shape(q["along"])
        r["path"] = [shape(p) for p in q.get("path") or []]
        r["base"] = find(q["base_pt"]) if q.get("base_pt") is not None else None
        r["start"] = P(*q["start"]) if q.get("start") else None
        for key, p in picks.items():
            if p is not None or key in ("from1", "to1"):
                r[key] = p
        self._pat_redo()

    def _paint_pattern(self, g):
        r = self.pat
        if r is None:
            return
        g.setBrush(QtCore.Qt.NoBrush)
        g.setPen(QtGui.QPen(QtGui.QColor(self.p["snap"]), 2.6))
        for item in r["items"] + (r["hover"] or []):
            if item[0] != "point":
                g.drawPolyline(QtGui.QPolygonF([self.to_screen(q) for q in self._off_seg(item, True)["poly"]]))
        under = r.get("under")
        if under is not None and under[0] == "point":
            g.drawEllipse(self.to_screen(self.pts[under[1]]), 6, 6)
        elif under is not None:
            g.drawPolyline(QtGui.QPolygonF([self.to_screen(q) for q in self._off_seg(tuple(under), True)["poly"]]))
        if r["active"] == "path":
            for item in r["path"]:
                g.drawPolyline(QtGui.QPolygonF([self.to_screen(q) for q in self._off_seg(item, True)["poly"]]))
        if r["start"] is not None:
            g.drawEllipse(self.to_screen(r["start"]), 5, 5)
        if r["base"] is not None:
            g.drawEllipse(self.to_screen(self.pts[r["base"]]), 5, 5)
        for key in ("dir1", "dir2", "from1", "to1"):
            p = r[key]
            if p is not None and p[0] == "line" and r["active"] == key:
                a, b = self.lines[p[1]]
                g.drawLine(self.to_screen(self.pts[a]), self.to_screen(self.pts[b]))
        if r["excluded"]:
            g.setPen(QtGui.QPen(QtGui.QColor(self.p["snap"]), 1.2, QtCore.Qt.DashLine))
            for n in r["excluded"]:
                for poly in self._pat_ghost(n):
                    g.drawPolyline(QtGui.QPolygonF([self.to_screen(q) for q in poly]))
