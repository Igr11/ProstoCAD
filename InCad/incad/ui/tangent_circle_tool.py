"""Sketch ▸ Tangent Circle (incad.core.tangent_circle) as a tool, the way the user draws it: the command first, then
clicks on the lines, arcs or circles it touches — normal, construction or projected ones, or an axis. From the first
click the circle follows the pointer; a third line makes it touch three, a click away from any line places it
touching the two (the user: "two lines or three"). One undo step, then the tool ends, as Offset does. Two or three
of them selected before the command are taken at once."""
import FreeCAD as App
import Part

from incad.core import log, safe, tangent_circle
from incad.qt import viewport_of

PICK_PX = 8  # logical px around the pointer that count as a click on an edge
PROMPTS = ("Click the first line, arc or circle the circle touches (Esc cancels)",
           "Click the second line, arc or circle (Esc cancels)",
           "Click a third line, arc or circle, or click away to place it touching these two (Esc cancels)")
SEGMENTS = 96  # of the circle drawn while picking


def subname(g):
    """The selection name of a Sketcher id."""
    return "Edge%d" % (g + 1) if g >= 0 else {-1: "H_Axis", -2: "V_Axis"}.get(g, "ExternalEdge%d" % (-g - 2))


def from_subname(name):
    """The Sketcher id of a selection name in a sketch, or None for a point or a constraint."""
    if name.startswith("Edge") and name[4:].isdigit():
        return int(name[4:]) - 1
    if name.startswith("ExternalEdge") and name[12:].isdigit():
        return -int(name[12:]) - 2
    return {"H_Axis": -1, "V_Axis": -2}.get(name)


class TangentCircleTool:
    def __init__(self, view, sketch, on_finish=None):
        self.view, self.sketch = view, sketch
        self.picked = []
        self._on_finish = on_finish
        self._input = None
        self._pointer = None  # the last pointer position on the sketch plane
        self._ghost = None    # the circle drawn while picking (coin nodes)

    def start(self, preselected=()):
        from incad.ui.view_clicks import ViewClicks
        self._input = ViewClicks(self.view, self._qt_press, self._qt_move)
        self._input.start()
        picks = [g for g in preselected if self._fits(g)]
        if len(picks) in (2, 3):  # selected first, the old way: the circle at once (two: its radius stays free)
            self._place(picks)
            return
        for g in picks:
            self.take(g)
        log.report("info", PROMPTS[len(self.picked)])

    def stop(self):
        if self._input is not None:
            self._input.stop()
            self._input = None
        self._highlight([])
        self._draw(None)
        if self._on_finish is not None:
            self._on_finish(self)

    # ----- input -----
    def to_sketch(self, x, y):
        """Coin position (device px, bottom-left) -> point on the sketch plane, in sketch coordinates."""
        pl = self.sketch.getGlobalPlacement()
        normal = pl.Rotation.multVec(App.Vector(0, 0, 1))
        p = self.view.getPoint(int(round(x)), int(round(y)))
        d = self.view.getViewDirection()
        denom = d.dot(normal)
        if abs(denom) < 1e-9:
            return None
        local = pl.inverse().multVec(p + d * ((pl.Base - p).dot(normal) / denom))
        return App.Vector(local.x, local.y, 0)

    def _qt_move(self, x, y):
        pt = self.to_sketch(x, y)
        if pt is None:
            return
        self._pointer = pt
        self._draw(self.ghost(pt))

    def ghost(self, pt):
        """(centre, radius) of the circle shown for the pointer at pt, or None (nothing picked yet)."""
        if not self.picked:
            return None
        try:
            return tangent_circle.preview([tangent_circle.geometry(self.sketch, g) for g in self.picked], pt)
        except Exception:
            return None

    def _draw(self, circle):
        """The circle while picking, on the sketch plane (None: hide it)."""
        try:
            from pivy import coin
            if self._ghost is None:
                if circle is None:
                    return
                sep, coords, lines = coin.SoAnnotation(), coin.SoCoordinate3(), coin.SoLineSet()
                colour = coin.SoBaseColor()
                colour.rgb = (0.95, 0.55, 0.15)
                style = coin.SoDrawStyle()
                style.lineWidth = 2
                for n in (colour, style, coords, lines):
                    sep.addChild(n)
                self.view.getSceneGraph().addChild(sep)
                self._ghost = (sep, coords, lines)
            sep, coords, lines = self._ghost
            if circle is None:
                self.view.getSceneGraph().removeChild(sep)
                self._ghost = None
                return
            centre, r = circle
            pl = self.sketch.getGlobalPlacement()
            import math
            pts = []
            for k in range(SEGMENTS + 1):
                a = 2 * math.pi * k / SEGMENTS
                p = pl.multVec(App.Vector(centre.x + r * math.cos(a), centre.y + r * math.sin(a), 0))
                pts.append((p.x, p.y, p.z))
            coords.point.setValues(0, len(pts), pts)
            coords.point.setNum(len(pts))
            lines.numVertices.setValue(len(pts))
        except Exception:
            log.logger().exception("Tangent Circle preview")

    def _qt_press(self, x, y, modifiers):
        pt = self.to_sketch(x, y)
        if pt is None:
            return
        try:
            dpr = viewport_of(self.view).devicePixelRatioF()
        except Exception:
            dpr = 1.0
        far = self.to_sketch(x + PICK_PX * dpr, y)
        self.click(pt, (far - pt).Length if far is not None else 1.0)

    def _ids(self):
        """Every Sketcher id in the sketch: its own geometry, the axes (-1, -2), the projected edges (-3 down)."""
        return list(range(self.sketch.GeometryCount)) + [-1 - k for k in range(len(self.sketch.ExternalGeo))]

    def _fits(self, g):
        try:
            return g in self._ids() and tangent_circle.geometry(self.sketch, g).TypeId in tangent_circle.KINDS
        except Exception:
            return False

    def nearest(self, pt, tol):
        """The sketch's line, arc or circle (construction and projected ones, the axes too) nearest the point, within
        tol, or None; a spline there is said to be one, as it can't be touched."""
        probe = Part.Vertex(pt)
        best, best_d, spline = None, tol, False
        for g in self._ids():
            if g in self.picked:
                continue
            try:
                geo = tangent_circle.geometry(self.sketch, g)
                d = geo.toShape().distToShape(probe)[0]
                if geo.TypeId not in tangent_circle.KINDS:
                    spline = spline or d <= tol
                    continue
            except Exception:
                continue
            if d <= best_d:
                best, best_d = g, d
        if best is None and spline:
            log.report("warning", "Tangent Circle: that is a spline, a circle can't be made to touch it")
        return best

    def click(self, pt, tol=1.0):
        g = self.nearest(pt, tol)
        if g is None:
            if len(self.picked) == 2:  # away from any line: the circle touching the two, as shown
                return self._place(self.picked, near=pt)
            return None
        return self.take(g)

    def take(self, g):
        """Take geometry g; the third one makes the circle. Returns its index then, else None."""
        if g in self.picked or not self._fits(g):
            return None
        self.picked.append(g)
        if len(self.picked) < 3:
            self._highlight(self.picked)
            if self._pointer is not None:
                self._draw(self.ghost(self._pointer))
            log.report("info", PROMPTS[len(self.picked)])
            return None
        return self._place(self.picked)

    def _place(self, picks, near=None):
        sk, doc = self.sketch, self.sketch.Document
        picks, self.picked = list(picks), []
        self._highlight([])
        self._draw(None)
        result = safe.run_safely("Tangent Circle", lambda: tangent_circle.add(sk, picks, near), doc, only=[sk])
        if not result.ok:
            log.report("info", PROMPTS[0])
            return None
        self.stop()
        log.report("info", "Tangent Circle: %s" % ("fixed by the three it touches" if len(picks) == 3 else
                                                   "touching the two; its radius is free: give it a dimension"))
        return result.value

    def _highlight(self, ids):
        """The picks so far show selected while the next one is awaited."""
        try:
            import FreeCADGui as Gui
            Gui.Selection.clearSelection()
            for g in ids:
                Gui.Selection.addSelection(self.sketch.Document.Name, self.sketch.Name, subname(g))
        except Exception:
            pass
