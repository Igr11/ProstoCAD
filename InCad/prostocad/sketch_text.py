"""Text in a sketch, by a box of two clicks and a window, as the old program had it. The words are one thing held by a tie on
the box's corners: dimension or tilt the box and they follow. Letters come from the computer's fonts through Qt; here
they are only drawn, the solid's outlines come with the geometry engine. Along a curve each letter turns with the curve: the
words read left to right where they stand, Flip side puts them on the other side (inside a ring they read at the bottom,
as on a coin)."""
import math

from PySide6 import QtCore, QtGui

from prostocad.sketch_trim import _poly

P = QtCore.QPointF
DEFAULTS = dict(text="Text", font="Arial", bold=False, italic=False, size=5.0, halign="center", valign="middle", fit=False,
                spacing=0.0, placement="box", position="center", shift=0.0, distance=0.0, flip=False, nbox=4, frame="rect", mirror=False)
FRAMES = ("rect", "ellipse", "circle")


def _len(p):
    return (p.x() ** 2 + p.y() ** 2) ** 0.5


def _seg_gap(p, a, b):
    d = b - a
    n = d.x() ** 2 + d.y() ** 2
    t = max(0.0, min(1.0, ((p.x() - a.x()) * d.x() + (p.y() - a.y()) * d.y()) / n)) if n > 1e-12 else 0.0
    return _len(a + d * t - p)


def _merged(polys):
    """The letters' outlines as one: where joined letters lie over each other there is no inner line, and the solid will get
    no wall there (the user). Letters keep their holes. Worked out enlarged: Qt's merging is coarse at millimetres."""
    if not polys:
        return polys
    path = QtGui.QPainterPath()
    path.setFillRule(QtCore.Qt.WindingFill)
    for poly in polys:
        path.addPolygon(QtGui.QPolygonF([q * 100 for q in poly]))
        path.closeSubpath()
    return [[q / 100 for q in poly] for poly in path.simplified().toSubpathPolygons()]


class TextTools:
    """Mixed into SketchView."""

    def _text_finish(self):
        """The box is drawn: dashed, held as a rectangle; the window opens on it."""
        st = self._state()
        n = len(self.lines)
        self._rect_finish()
        if len(self.lines) == n:
            return
        for k in range(n, n + 4):
            self.constr[k] = True
        corners = [self.lines[n + k][0] for k in range(4)]
        self._add_tie("sk_text", corners, dict(DEFAULTS, font=self.text_font))
        tie = self.ties[-1]
        if self.text_frame != "rect":
            self.text_set_frame(tie, self.text_frame)
        del self.steps[len(st["steps"]):]
        self.stop_tool()
        self.just_done = False
        self.text_open.emit(tie, st)

    def _text_ell(self, t):
        """The index of the dashed ellipse in this text's box; None."""
        corners = t["pts"][:4]
        for i, e in enumerate(self.ells):
            if any(q["kind"] == "ell_frame" and q["pts"][:3] == list(e) and q["pts"][3:] == corners for q in self.ties):
                return i
        return None

    def text_set_frame(self, t, frame):
        """The box stays the frame; a dashed ellipse or circle stands in it. A circle makes the box square, round its middle."""
        corners = t["pts"][:4]
        was = self._text_ell(t)
        if was is not None:
            self.ties = [q for q in self.ties if not (q["kind"] == "ell_frame" and q["pts"][3:] == corners)
                         and not (q["kind"] == "con_eq" and q["pts"] == [corners[0], corners[1], corners[1], corners[2]])]
            self._fit_ids()["ell"].pop(was)
            self.ells.pop(was)
            self.ell_constr = self.ell_constr[:was] + self.ell_constr[was + 1:]
            self._drop_lone_points()
            corners = t["pts"][:4]
        before = t["ref"].get("frame", "rect")
        wide = t["ref"].get("wide")
        p = [self.pts[k] for k in corners]
        if frame == "circle" and before != "circle":  # the box goes square; it comes back from the circle as it was
            t["ref"] = dict(t["ref"], wide=(_len(p[1] - p[0]), _len(p[2] - p[1])))
            side = min(t["ref"]["wide"])
            self._text_box_size(corners, side, side)
        elif before == "circle" and frame != "circle" and wide:
            self._text_box_size(corners, *wide)
        t["ref"] = dict(t["ref"], frame=frame)
        if frame == "rect":
            self.update()
            return
        c = (self.pts[corners[0]] + self.pts[corners[2]]) * 0.5
        centre = next((tie["pts"][0] for tie in self.ties if tie["kind"] == "mid" and tie["ref"] is not None
                       and tie["ref"] < len(self.lines) and set(self.lines[tie["ref"]]) <= set(corners)), None)
        if centre is None:
            self.pts.append(P(c))
            centre = len(self.pts) - 1
        self.pts.append((self.pts[corners[0]] + self.pts[corners[1]]) * 0.5)
        a = len(self.pts) - 1
        self.pts.append((self.pts[corners[1]] + self.pts[corners[2]]) * 0.5)
        b = len(self.pts) - 1
        self.ells.append((centre, a, b))
        self.ell_constr = (self.ell_constr + [False] * len(self.ells))[:len(self.ells) - 1] + [True]
        self._add_tie("ell_frame", [centre, a, b, *corners])
        if frame == "circle":
            self._add_tie("con_eq", [corners[0], corners[1], corners[1], corners[2]])
        self.free_count()
        self.update()

    def _text_box_size(self, corners, w, h):
        """The box to these sizes round its middle."""
        p = [self.pts[k] for k in corners]
        c = (p[0] + p[2]) * 0.5
        ex, ey = (p[1] - p[0]) / _len(p[1] - p[0]), (p[2] - p[1]) / _len(p[2] - p[1])
        for k, (sx, sy) in zip(corners, ((-1, -1), (1, -1), (1, 1), (-1, 1))):
            q = c + ex * (sx * w / 2) + ey * (sy * h / 2)
            self.pts[k].setX(q.x())
            self.pts[k].setY(q.y())

    def text_on_selection(self):
        """Text pressed with one curve chosen: it runs along that curve at once."""
        chosen = [("line", i) for i in self.sel_lines] + [("arc", i) for i in self.sel_arcs] + [("ell", i) for i in self.sel_ells] \
            + [("spl", i) for i in self.sel_spls]
        if len(chosen) != 1:
            return False
        st = self._state()
        self._add_tie("sk_text", list(self._shape_points(chosen[0])), dict(DEFAULTS, font=self.text_font, placement="curve", nbox=0))
        self._clear_choice()
        self.text_open.emit(self.ties[-1], st)
        return True

    def text_take_curve(self, t, what):
        """The curve clicked in the view while its window is open."""
        t["pts"] = t["pts"][:t["ref"]["nbox"]] + list(self._shape_points(what))
        self.update()

    def text_drop_box(self, t):
        """Along a curve the box of the two clicks has no more use."""
        n = t["ref"]["nbox"]
        if not n:
            return
        if self._text_ell(t) is not None:
            self.text_set_frame(t, "rect")
        corners = set(t["pts"][:n])
        t["pts"] = t["pts"][n:]
        t["ref"] = dict(t["ref"], nbox=0)
        self.sel_lines = {i for i, ln in enumerate(self.lines) if set(ln) <= corners}
        steps = len(self.steps)
        self._delete()
        del self.steps[steps:]  # one with the text

    def _text_curve(self, t):
        """The curve's places, in order, and whether it closes."""
        have = tuple(t["pts"][t["ref"].get("nbox", 4):])
        for item in self._all_shapes(everything=True):
            if tuple(self._shape_points(item)) == have:
                g = self._geom(item)
                full = (g[0] == "arc" and g[4] >= 2 * math.pi - 1e-9) or (g[0] == "ell" and g[5] >= 2 * math.pi - 1e-9) \
                    or (item[0] == "spl" and self.spls[item[1]][2])
                return [P(q) for q in _poly(g, 240)], full
        return None, False

    def _text_along(self, t, font, k):
        s = t["ref"]
        path, closed = self._text_curve(t)
        if not path or len(path) < 2:
            return []
        start = 0
        if closed:  # it starts at the top, or at the bottom on the other side
            pick = min if s["flip"] else max
            start = pick(range(len(path) - 1), key=lambda j: path[j].y())
            path = path[start:-1] + path[:start + 1]
        at = 0 if closed else min(len(path) // 2, len(path) - 2)
        leftward = (path[at + 1] - path[at]).x() < 0
        if leftward if closed else leftward != bool(s["flip"]):  # a ring always reads left to right, top or bottom
            path = path[::-1]
        run = [0.0]
        for a, b in zip(path, path[1:]):
            run.append(run[-1] + _len(b - a))
        whole = run[-1]
        font.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, float(s["spacing"]) / k)
        layout = QtGui.QTextLayout(str(s["text"]).replace("\n", " "), font)  # as a line of text first: right to left, letters joined
        layout.beginLayout()
        line = layout.createLine()
        layout.endLayout()
        long = line.naturalTextWidth() * k
        first = {"start": 0.0, "center": (whole - long) / 2, "end": whole - long}[s["position"]] + float(s["shift"])
        if closed:  # centred on the place it starts from
            first = -long / 2 + float(s["shift"])

        def place(at):
            at = at % whole if closed else at
            if at <= 0 and not closed:
                j = 0
            elif at >= whole and not closed:
                j = len(run) - 2
            else:
                j = next((n for n in range(len(run) - 1) if run[n + 1] >= at), len(run) - 2)
            a, b = path[j], path[j + 1]
            u = (b - a) / (_len(b - a) or 1.0)
            return a + u * (at - run[j]), u

        out = []
        for glyphs in layout.glyphRuns():
            raw = glyphs.rawFont()
            ids = glyphs.glyphIndexes()
            for g, x, wide in zip(ids, glyphs.positions(), raw.advancesForGlyphIndexes(ids)):
                half = wide.x() / 2
                along = (x.x() + half) * k
                back = -1.0 if s.get("mirror") else 1.0  # mirrored: the last letter first, each letter turned over
                pos, u = place(first + (long - along if s.get("mirror") else along))
                n = P(-u.y(), u.x())
                lift = line.ascent() - x.y()
                for poly in raw.pathForGlyph(g).toSubpathPolygons():
                    out.append([pos + u * (back * (q.x() - half) * k) + n * ((lift - q.y()) * k + float(s["distance"])) for q in poly])
        return out

    def texts(self):
        return [t for t in self.ties if t["kind"] == "sk_text"]

    def _text_frame(self, t):
        """(corner, x way, y way, width, height): the box as words read in it, upright as far as it can be."""
        c = [self.pts[k] for k in t["pts"][:4]]
        o, ex, ey = P(c[0]), c[1] - c[0], c[3] - c[0]
        if ex.x() < -1e-9 or (abs(ex.x()) < 1e-9 and ex.y() < 0):
            o, ex = o + ex, ex * -1
        if ex.x() * ey.y() - ex.y() * ey.x() < 0:
            o, ey = o + ey, ey * -1
        w, h = _len(ex), _len(ey)
        return o, ex / (w or 1.0), ey / (h or 1.0), w, h

    def text_outlines(self, t):
        """The letters' outlines in sketch coordinates, one outline where letters join. Kept until the sketch or the text
        changes: every repaint asked for them, and a tool's rubber line repaints on each move of the mouse."""
        key = (repr(t["ref"]), tuple(t["pts"]), tuple((p.x(), p.y()) for p in self.pts), len(self.lines), len(self.arcs), len(self.ells), len(self.spls))
        kept = self.text_kept.get(id(t))
        if kept is not None and kept[0] == key:
            return kept[1]
        if len(self.text_kept) > 200:
            self.text_kept.clear()
        made = None
        if self.text_source is not None:  # a host that builds the letters itself (the part's own Text): its outlines
            try:
                made = self.text_source(self, t)
            except Exception:
                made = None
        if made is None:
            made = _merged(self._text_raw(t))
        self.text_kept[id(t)] = (key, made)
        return made

    def _text_raw(self, t):
        s = t["ref"]
        if t["ref"].get("nbox", 4):
            o, ux, uy, w, h = self._text_frame(t)
        elif not self._text_curve(t)[0]:
            return []
        font = QtGui.QFont(s["font"])
        font.setBold(bool(s["bold"]))
        font.setItalic(bool(s["italic"]))
        font.setPixelSize(100)
        metrics = QtGui.QFontMetricsF(font)
        cap = metrics.capHeight() or metrics.ascent() * 0.72
        k = float(s["size"]) / cap
        if s.get("placement") == "curve" and self._text_curve(t)[0]:
            return self._text_along(t, font, k)
        font.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, float(s["spacing"]) / k)
        lines = str(s["text"]).split("\n")
        widths = [QtGui.QPainterPath() for _line in lines]
        for path, line in zip(widths, lines):
            path.addText(0, 0, font, line)
        wide = max((p.boundingRect().width() for p in widths), default=0.0)
        whole = QtGui.QPainterPath()
        for row, path in enumerate(widths):
            r = path.boundingRect()
            room = wide - r.width()
            shift = {"left": 0.0, "center": room / 2, "right": room}[s["halign"]] - r.left()
            whole.addPath(path.translated(shift, row * metrics.lineSpacing()))
        ink = whole.boundingRect()
        if ink.isEmpty():
            return []
        round_box = s.get("frame", "rect") != "rect"
        if round_box and s["fit"] and w > 0 and h > 0:
            return self._text_filled(whole, ink, o, ux, uy, w, h, bool(s.get("mirror")))
        if s["fit"] and w > 0 and h > 0:
            k = min(w / ink.width(), h / ink.height())
        tw, th = ink.width() * k, ink.height() * k
        inset = (1 - 1 / math.sqrt(2)) / 2 if round_box and not s["fit"] else 0.0  # the square inside the round frame
        x0, y0, room_w, room_h = w * inset, h * inset, w * (1 - 2 * inset), h * (1 - 2 * inset)
        left = x0 + {"left": 0.0, "center": (room_w - tw) / 2, "right": room_w - tw}[s["halign"]]
        top = y0 + {"top": room_h, "middle": (room_h + th) / 2, "bottom": th}[s["valign"]]
        flip = bool(s.get("mirror"))
        return [[o + ux * (left + ((ink.right() - q.x()) if flip else (q.x() - ink.left())) * k) + uy * (top - (q.y() - ink.top()) * k) for q in poly]
                for poly in whole.toSubpathPolygons()]

    def _text_filled(self, whole, ink, o, ux, uy, w, h, flip):
        """Fit in an ellipse or a circle: the letters take the frame's shape, each as high as the frame is at its place,
        top and bottom on the curve. Long straight strokes are cut up first, so that they bend with it."""
        most = ink.width() / 80
        wide = 0.97  # at the very ends the frame has no height left
        polys = [list(p) for p in whole.toSubpathPolygons()]
        spans = sorted(((min(q.x() for q in p), max(q.x() for q in p), p) for p in polys), key=lambda one: one[0])
        letters = []  # outlines over each other from left to right are one letter: its body, its hole, the dot on an i
        for lo, hi, p in spans:
            if letters and lo < letters[-1][0]:
                letters[-1][0] = max(letters[-1][0], hi)
                letters[-1][1].append(p)
            else:
                letters.append([hi, [p]])
        out = []
        for _hi, group in letters:
            bottom, top = max(q.y() for p in group for q in p), min(q.y() for p in group for q in p)
            middle, half = (top + bottom) / 2, ((bottom - top) / 2) or 1.0
            for poly in group:
                places = []
                for a, b in zip(poly, poly[1:]):
                    n = max(1, math.ceil(_len(b - a) / most))
                    places += [a + (b - a) * (j / n) for j in range(n)]
                one = []
                for q in places:
                    x = (q.x() - ink.center().x()) / (ink.width() / 2) * wide
                    y = (middle - q.y()) / half  # each letter from its own top to its own foot
                    x = -x if flip else x
                    one.append(o + ux * (w / 2 * (1 + x)) + uy * (h / 2 * (1 + y * math.sqrt(max(0.0, 1 - x * x)))))
                out.append(one)
        return out

    def _paint_texts(self, g):
        g.setPen(QtGui.QPen(QtGui.QColor(self.p["geometry"]), 1.2))
        g.setBrush(QtCore.Qt.NoBrush)
        for t in self.texts():
            for poly in self.text_outlines(t):
                g.drawPolygon(QtGui.QPolygonF([self.to_screen(q) for q in poly]))

    def edit_at(self, s):
        """What opens what is at the screen point s: a dimension's box, a text's window; None if it has none."""
        k = self._dim_hit(s)
        if k is not None:
            return lambda t=self.ties[k]: self._dim_open(t)
        t = self.text_at(s)
        if t is not None:
            return lambda: self.text_open.emit(t, None)
        name = self._pat_at(s)
        if name is not None:
            return lambda: self.pattern_open.emit(name)
        c = self.corner_groups(s)
        if c is not None:
            return lambda: self.corner_open.emit(c)
        p = self.poly_edit_at(s)
        return (lambda: self.poly_open.emit(p)) if p is not None else None

    def text_at(self, s):
        """The text whose box is at the screen point s."""
        for t in reversed(self.texts()):
            if t["ref"].get("placement") == "curve":
                places = [self.to_screen(q) for poly in self.text_outlines(t) for q in poly]
                if places and QtCore.QRectF(QtGui.QPolygonF(places).boundingRect()).adjusted(-4, -4, 4, 4).contains(s):
                    return t
                continue
            corners = [self.to_screen(self.pts[k]) for k in t["pts"][:4]]
            if QtGui.QPolygonF(corners).containsPoint(s, QtCore.Qt.OddEvenFill) or \
                    min(_seg_gap(s, a, b) for a, b in zip(corners, corners[1:] + corners[:1])) <= 8:  # on its edge too
                return t
        return None
