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
           "center": ("Click the slot's center", "Click one end's center", "Click to set the width")}


class SlotTool:
    def __init__(self, view, sketch, mode, on_finish=None):
        self.view, self.sketch, self.mode = view, sketch, mode
        self.points, self.picks = [], []
        self._on_finish = on_finish
        self._callbacks = []
        self._preview = None
        self._coords = None

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
        self.points.append(pt)
        self.picks.append(pick)
        if len(self.points) < 3:
            log.report("info", PROMPTS[self.mode][len(self.points)] + " (Esc finishes)")
            return None
        a, b, c = self.points
        picks, self.points, self.picks = self.picks, [], []
        self._draw([])
        try:
            c1, c2, r = (slots.overall if self.mode == "overall" else slots.center_point)(a, b, c)
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
        for arc, pick in ((a1, picks[0]), (a2, picks[1])):  # the outer ends: the clicked point on each arc's rim
            if pick is not None and pick[0] == "point":
                _keep(sk, Sketcher.Constraint("PointOnObject", pick[1], pick[2], arc))

    def hover(self, pt):
        if not self.points:
            return
        if len(self.points) == 1:
            a = self.points[0]
            ref = a - (pt - a) if self.mode == "center" else a
            self._draw([ref, pt])
            return
        try:
            c1, c2, r = (slots.overall if self.mode == "overall" else slots.center_point)(self.points[0],
                                                                                         self.points[1], pt)
        except ValueError:
            return
        self._draw(_outline(c1, c2, r))

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
        color.rgb.setValue(0.95, 0.62, 0.29)
        style = coin.SoDrawStyle()
        style.lineWidth = 2
        self._coords = coin.SoCoordinate3()
        self._lines = coin.SoLineSet()
        for node in (tr, color, style, self._coords, self._lines):
            sep.addChild(node)
        self.view.getSceneGraph().addChild(sep)
        self._preview = sep
        self._draw([])

    def _draw(self, pts):
        if self._coords is None:
            return
        self._coords.point.setValues(0, len(pts), [(p.x, p.y, 0.01) for p in pts])
        self._coords.point.setNum(len(pts))
        self._lines.numVertices.setValues(0, 1, [len(pts)]) if pts else self._lines.numVertices.setNum(0)


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
