"""InCad's own slot tools (Inventor's Overall and Center Point slots, which FreeCAD's Sketcher doesn't draw).

Three clicks on the sketch plane — two points along the slot, then one for the width — with a live outline;
continues for the next slot until Esc. The slot is added as one undo step (incad.core.slots). A click near a point
or a line of the sketch snaps to it (the user's law: normal, construction or projected alike) and the slot is tied
there: an end's centre on the point or line, the slot's centre symmetric between its arcs' centres, an overall end on
the arc's rim."""
import math

import FreeCAD as App
import Sketcher

from incad.core import log, safe, slots, sketch_symmetric as sym

SNAP_PX = 8  # logical px around the pointer that snap to a point or a line of the sketch

PROMPTS = {"overall": ("Click the first end of the slot", "Click the other end", "Click to set the width"),
           "c2c": ("Click one end's center", "Click the other end's center", "Click to set the width"),
           "center": ("Click the slot's center", "Click one end's center", "Click to set the width")}


class SlotTool:
    def __init__(self, view, sketch, mode, on_finish=None):
        self.view, self.sketch, self.mode = view, sketch, mode
        self.points, self.picks = [], []
        self._on_finish = on_finish
        self._callbacks = []
        self._preview = None
        self._coords = None
        self._widened = False  # the pointer has taken the width past the starting 2 mm: it may narrow freely now
        self._typed = {}  # "length" / "radius" typed into their fields (they hold, the pointer gives the rest)
        self._last = None  # the pointer's last place on the sketch: where Enter finishes
        self._fields = {}

    # ----- lifecycle -----
    def start(self):
        for kind in ("SoMouseButtonEvent", "SoLocation2Event"):
            self._callbacks.append((kind, self.view.addEventCallback(kind, self._event)))
        self._make_preview()
        log.report("info", PROMPTS[self.mode][0] + " (Esc finishes)")

    def stop(self):
        for kind, cb in self._callbacks:
            try:
                self.view.removeEventCallback(kind, cb)
            except Exception:
                pass
        self._callbacks = []
        if self._preview is not None:
            try:
                self.view.getSceneGraph().removeChild(self._preview)
            except Exception:
                pass
            self._preview = None
        for f in self._fields.values():
            try:
                f.deleteLater()
            except Exception:
                pass
        self._fields = {}
        _give_view_focus(self.view)
        if self._on_finish is not None:
            self._on_finish(self)

    # ----- input -----
    def to_sketch(self, x, y):
        """Screen position -> point on the sketch plane, in sketch coordinates."""
        pl = self.sketch.getGlobalPlacement()
        normal = pl.Rotation.multVec(App.Vector(0, 0, 1))
        p = self.view.getPoint(x, y)
        d = self.view.getViewDirection()
        denom = d.dot(normal)
        if abs(denom) < 1e-9:
            return None
        hit = p + d * ((pl.Base - p).dot(normal) / denom)
        local = pl.inverse().multVec(hit)
        return App.Vector(local.x, local.y, 0)

    def _event(self, info):
        pos = info.get("Position")
        if pos is None:
            return
        pt = self.to_sketch(*pos)
        if pt is None:
            return
        pt, pick = self.snap(pt, self._tolerance(pos))
        if info.get("Type") == "SoMouseButtonEvent":
            if info.get("Button") == "BUTTON1" and info.get("State") == "DOWN":
                self.click(pt, pick)
        else:
            self.hover(pt)

    def _tolerance(self, pos):
        try:
            from incad.qt import viewport_of
            dpr = viewport_of(self.view).devicePixelRatioF()
        except Exception:
            dpr = 1.0
        a, b = self.to_sketch(*pos), self.to_sketch(int(round(pos[0] + SNAP_PX * dpr)), pos[1])
        return (b - a).Length if a is not None and b is not None else 0.0

    def snap(self, pt, tol):
        """(point, pick): pt moved onto a point or line of the sketch within tol (sketch_symmetric.snap_at)."""
        if tol <= 0:
            return pt, None
        return sym.snap_at(self.sketch, pt, tol)

    def click(self, pt, pick=None):
        if len(self.points) == 1 and pick is None:
            pt = _level(self.points[0], pt)  # nearly level or upright: exactly (and tied so)
        pt = self._held(pt)
        self.points.append(pt)
        self.picks.append(pick)
        if len(self.points) < 3:
            log.report("info", PROMPTS[self.mode][len(self.points)] + " (Esc finishes)")
            self._show_fields()
            return None
        a, b, c = self.points
        picks, self.points, self.picks = self.picks, [], []
        self._typed = {}
        self._draw([])
        self._show_fields()
        least, self._widened = self._least(), False
        try:
            c1, c2, r = _MAKE[self.mode](a, b, c, least)
        except ValueError as e:
            log.report("warning", str(e))
            return None
        doc = self.sketch.Document
        def go():
            ids = slots.add(self.sketch, c1, c2, r)
            self._tie(ids, picks)
            return ids
        result = safe.run_safely("Slot", go, doc)
        log.report("info", PROMPTS[self.mode][0] + " for the next slot (Esc finishes)")
        return result.value if result.ok else None

    def _tie(self, ids, picks):
        """The slot tied where its clicks snapped (only ties the sketch still solves with are kept)."""
        sk = self.sketch
        a1, a2 = ids[0], ids[1]
        if self.mode == "center":
            centre, end = picks[0], picks[1]
            if centre is not None and centre[0] == "point":  # the slot's centre: halfway between its arcs' centres
                _keep(sk, Sketcher.Constraint("Symmetric", a1, 3, a2, 3, centre[1], centre[2]))
            sym.tie_point(sk, a2, 3, end)
            return
        if self.mode == "c2c":  # the arcs' centres where they snapped
            sym.tie_point(sk, a1, 3, picks[0])
            sym.tie_point(sk, a2, 3, picks[1])
            return
        for arc, pick in ((a1, picks[0]), (a2, picks[1])):  # the outer ends: the clicked point on each arc's rim
            if pick is not None and pick[0] == "point":
                _keep(sk, Sketcher.Constraint("PointOnObject", pick[1], pick[2], arc))

    def hover(self, pt):
        if not self.points:
            return
        self._last = pt
        if len(self.points) == 1:  # the slot itself from the first move: as thin as can be seen, or the typed radius
            a = self.points[0]
            pt = self._held(_level(a, pt))
            if (pt - a).Length < 1e-9:
                return
            r = self._typed.get("radius") or self._least()
            u = pt - a
            u.normalize()
            if self.mode == "center":
                c1, c2, ends = a - (pt - a), pt, (a - (pt - a), pt)
            elif self.mode == "overall":
                half = min(r, (pt - a).Length / 2.0 - 1e-6)
                c1, c2, ends = a + u * half, pt - u * half, (a, pt)
            else:
                c1, c2, ends = a, pt, (a, pt)
            self._draw(_outline(c1, c2, r), {"length": ((ends[1] - ends[0]).Length, (ends[0] + ends[1]) * 0.5,
                                                        ends[0], ends[1], r),
                                             "radius": (r, c2 + u * r, None, None, 0.0)})
            return
        try:
            if slots._width_from(self.points[0], self.points[1], pt) > slots.START_RADIUS:
                self._widened = True
            c1, c2, r = _MAKE[self.mode](self.points[0], self.points[1], self._held(pt), self._least())
        except ValueError:
            return
        a, b = (self.points[0], self.points[1]) if self.mode == "overall" else (c1, c2)
        d = c2 - c1
        u = App.Vector(d) if d.Length > 1e-9 else App.Vector(1, 0, 0)
        u.normalize()
        self._draw(_outline(c1, c2, r), {"length": ((b - a).Length, (a + b) * 0.5, a, b, r),
                                         "radius": (r, c2 + u * r, None, None, 0.0)})

    def _held(self, pt):
        """The pointer's point with the typed values held: the other end at the typed length along the pointer's
        direction (before the width), the width at the typed radius (after)."""
        if not self.points:
            return pt
        a = self.points[0]
        if len(self.points) == 1:
            length = self._typed.get("length")
            if length is None:
                return pt
            d = pt - a
            if d.Length < 1e-9:
                d = App.Vector(1, 0, 0)
            d.normalize()
            reach = length / 2.0 if self.mode == "center" else length
            return a + d * reach
        r = self._typed.get("radius")
        if r is None:
            return pt
        b = self.points[1]
        d = b - a
        n = App.Vector(-d.y, d.x, 0) if d.Length > 1e-9 else App.Vector(0, 1, 0)
        n.normalize()
        side = 1.0 if (pt - a).dot(n) >= 0 else -1.0
        return a + n * (r * side)

    # ----- the value fields (as every CAD's sizes at the pointer: type, Tab, Enter) -----
    def _show_fields(self):
        """Length after the first click, length and radius after the second; the field to type in is active."""
        want = ("length", "radius") if self.points else ()  # both from the start, as a rectangle's two sides
        for key, f in self._fields.items():
            if key not in want:
                f.hide()
        if not want:
            _give_view_focus(self.view)
            return
        for key in want:
            if key not in self._fields:
                self._fields[key] = ValueField(self.view, "R" if key == "radius" else "", key, self)
        self._fields["radius" if len(self.points) == 2 else "length"].activate()

    def typed(self, key, value):
        """A field's value typed (None: cleared): the slot follows it at once."""
        if value is None:
            self._typed.pop(key, None)
        else:
            self._typed[key] = value
        if key == "length" and value is not None and len(self.points) == 2:  # the length changed after its click:
            a, b = self.points  # the other end moves along the slot
            d = b - a
            if d.Length > 1e-9:
                d.normalize()
                self.points[1] = a + d * (value / 2.0 if self.mode == "center" else value)
        if self._last is not None:
            self.hover(self._last)

    def tab(self, key):
        """Tab: the next field (length <-> radius)."""
        keys = [k for k in ("length", "radius") if k in self._fields and self._fields[k].isVisible()]
        if len(keys) > 1:
            self._fields[keys[(keys.index(key) + 1) % len(keys)]].activate()

    def enter(self, key):
        """Enter: the step finishes where the typed values (and the pointer) say."""
        if self._last is None and self.points:
            self._last = self.points[-1] + App.Vector(1, 0, 0)
        if self._last is not None:
            self.click(self._last)

    def _pt(self):
        """Model units in a point of the screen at the zoom the slot is drawn at."""
        try:
            from incad.core import camera
            from incad.qt import viewport_of
            return camera.units_per_px(self.view.getCameraNode(), self.view.getSize()[1]) * \
                viewport_of(self.view).devicePixelRatio()
        except Exception:
            return 0.0

    def _least(self):
        """As wide as the pointer says from the first moment (a 2 mm start jumped), never so narrow it can't be
        seen: at least LEAST_PT points of the screen each side of the line at the zoom the slot is drawn at (the
        view's size is in device pixels: two to a point on a Retina screen)."""
        try:
            from incad.core import camera
            from incad.qt import viewport_of
            px = camera.units_per_px(self.view.getCameraNode(), self.view.getSize()[1])
            px *= viewport_of(self.view).devicePixelRatio()
        except Exception:
            px = 0.0
        return max(slots.LEAST_RADIUS, LEAST_PT * px)

    # ----- preview (Coin overlay on the sketch plane) -----
    def _make_preview(self):
        from pivy import coin
        sep = coin.SoSeparator()
        tr = coin.SoTransform()
        pl = self.sketch.getGlobalPlacement()
        tr.translation.setValue(*pl.Base)
        q = pl.Rotation.Q
        tr.rotation.setValue(q[0], q[1], q[2], q[3])
        color = coin.SoBaseColor()
        from incad.ui import theme  # the colour of the sketch's lines, as every tool draws (orange is construction)
        hex_color = theme.SKETCH_COLORS[theme.current()]["EditedEdgeColor"].lstrip("#")
        color.rgb.setValue(*(int(hex_color[i:i + 2], 16) / 255.0 for i in (0, 2, 4)))
        style = coin.SoDrawStyle()
        style.lineWidth = 2
        self._coords = coin.SoCoordinate3()
        self._lines = coin.SoLineSet()
        for node in (tr, color, style, self._coords, self._lines):
            sep.addChild(node)
        self.view.getSceneGraph().addChild(sep)
        self._preview = sep
        self._draw([])

    def _draw(self, pts, values=None):
        """The outline, and the value fields: {key: (value, where, from, to, radius)} — a length beside the line
        from-to, out past the slot's side; a radius beyond the arc's end."""
        if self._coords is None:
            return
        self._coords.point.setValues(0, len(pts), [(p.x, p.y, 0.01) for p in pts])
        self._coords.point.setNum(len(pts))
        self._lines.numVertices.setValues(0, 1, [len(pts)]) if pts else self._lines.numVertices.setNum(0)
        gap = 18 * self._pt()
        for key, (value, at, a, b, r) in (values or {}).items():
            f = self._fields.get(key)
            if f is None:
                continue
            base = App.Vector(at)
            if a is not None and b is not None:
                d = b - a
                n = App.Vector(-d.y, d.x, 0) if d.Length > 1e-9 else App.Vector(0, 1, 0)
                n.normalize()
                at = at + n * (r + gap)
            elif key == "radius" and self.points:
                d = at - self.points[0]
                if d.Length > 1e-9:
                    d.normalize()
                    at = at + d * gap
            pl = self.sketch.getGlobalPlacement()
            beside = pl.multVec(App.Vector(at.x, at.y, 0))
            end = pl.multVec(App.Vector(base.x, base.y, 0)) if key == "radius" else None
            f.show_value(value, beside, end)  # a radius stands off the arc's end, never over it


def _give_view_focus(view):
    try:
        from incad.qt import viewport_of
        viewport_of(view).setFocus()
    except Exception:
        pass


def _parse(text):
    t = (text or "").strip().lower().replace(",", ".").replace("mm", "").replace("r", "").strip()
    try:
        v = float(t)
    except ValueError:
        return None
    return v if v > 0 else None


class ValueField:
    """A size at the pointer, in the look every sketch tool's sizes have (incad.ui.value_fields): typing holds it
    (the slot follows), Tab goes to the next, Enter finishes the step, Esc ends the tool. Shows what the pointer
    gives until something is typed."""

    def __init__(self, view, prefix, key, tool):
        from incad.qt import QtCore, QtWidgets, viewport_of
        from incad.ui import value_fields
        self.view, self.key, self.tool = view, key, tool
        self.edit = QtWidgets.QLineEdit(viewport_of(view))
        self.edit.setStyleSheet(value_fields.style())
        self.edit.setAlignment(QtCore.Qt.AlignCenter)
        self.edit.textEdited.connect(lambda t: self.tool.typed(self.key, _parse(t)))
        self._filter = _Keys(self)
        self.edit.installEventFilter(self._filter)
        self.edit.hide()

    def activate(self):
        self._want_focus = True  # taken when it shows (a hidden field can't hold the keys)
        if self.edit.isVisible():
            self._take_keys()

    def _take_keys(self):
        self._want_focus = False
        self.edit.setFocus()
        self.edit.selectAll()  # typing replaces what the pointer gave

    def show_value(self, value, point, away_from=None):
        from incad.ui.nav import from_coin
        from incad.qt import viewport_of
        if self.key not in self.tool._typed and not self.edit.isModified():
            self.edit.setText(("%.2f" % value).rstrip("0").rstrip(".") + " mm")
            if self.edit.hasFocus():
                self.edit.selectAll()
        try:
            x, y = from_coin(viewport_of(self.view), *self.view.getPointOnViewport(point))
        except Exception:
            return
        fm = self.edit.fontMetrics()
        self.edit.resize(max(80, fm.horizontalAdvance(self.edit.text()) + 30), 24)
        left = x - self.edit.width() / 2
        if away_from is not None:  # its near edge at the point, the box reaching away from `away_from`
            try:
                ex, _ey = from_coin(viewport_of(self.view), *self.view.getPointOnViewport(away_from))
                left = x if x >= ex else x - self.edit.width()
            except Exception:
                pass
        self.edit.move(int(left), int(y - self.edit.height() / 2))
        self.edit.show()
        self.edit.raise_()
        if getattr(self, "_want_focus", False):
            self._take_keys()

    def settle(self):
        """A typed value written as the others are ("8" -> "8 mm") once Tab or Enter takes it."""
        v = _parse(self.edit.text())
        if v is not None:
            self.edit.setText(_mm(v))

    def hide(self):
        self._want_focus = False
        self.edit.setModified(False)
        self.edit.hide()

    def isVisible(self):
        return self.edit.isVisible()

    def deleteLater(self):
        self.edit.deleteLater()


def _keys_filter_base():
    from incad.qt import QtCore
    return QtCore.QObject


class _Keys(_keys_filter_base()):
    """A value field's keys: Tab the next field, Enter finish the step, Esc end the tool."""

    def __init__(self, field):
        super().__init__(field.edit)
        self.field = field

    def eventFilter(self, obj, event):
        from incad.qt import QtCore
        if event.type() != QtCore.QEvent.KeyPress:
            return False
        k = event.key()
        if k in (QtCore.Qt.Key_Tab, QtCore.Qt.Key_Backtab):
            self.field.settle()
            self.field.tool.tab(self.field.key)
            return True
        if k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.field.settle()
            self.field.tool.enter(self.field.key)
            return True
        if k == QtCore.Qt.Key_Escape:
            from incad.ui import shell
            sh = shell.instance()
            if sh is not None and sh.slot_tool is self.field.tool:
                sh.stop_slot_tool()
            else:
                self.field.tool.stop()
            return True
        return False


def _mm(v):
    return ("%.2f" % v).rstrip("0").rstrip(".") + " mm"


LEAST_PT = 10  # half the narrowest slot width while it is drawn, in points of the screen (seen as a slot, not a line)
LEVEL_DEG = 3.0  # a slot this close to level or upright is drawn exactly so (as the Sketcher's own tools snap)


def _level(a, b):
    """b moved so a-b is exactly horizontal or vertical when it is within LEVEL_DEG of it."""
    d = b - a
    if d.Length < 1e-9:
        return b
    ang = math.degrees(math.atan2(abs(d.y), abs(d.x)))
    if ang < LEVEL_DEG:
        return App.Vector(b.x, a.y, b.z)
    if ang > 90.0 - LEVEL_DEG:
        return App.Vector(a.x, b.y, b.z)
    return b


_MAKE = {"overall": slots.overall, "center": slots.center_point, "c2c": slots.center_to_center}


def _keep(sk, c):
    """Add constraint c when the sketch still solves with it."""
    try:
        i = sk.addConstraint(c)
    except Exception:
        return False
    if sk.solve() != 0:
        sk.delConstraint(i)
        sk.solve()
        return False
    return True


def _outline(c1, c2, r, steps=16):
    th = math.atan2(c2.y - c1.y, c2.x - c1.x)
    pts = []
    for c, start in ((c2, th - math.pi / 2), (c1, th + math.pi / 2)):
        for i in range(steps + 1):
            a = start + math.pi * i / steps
            pts.append(App.Vector(c.x + r * math.cos(a), c.y + r * math.sin(a), 0))
    pts.append(pts[0])
    return pts
