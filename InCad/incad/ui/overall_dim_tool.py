"""Sketch ▸ Overall Dimension (incad.core.overall_dims): the distance between the OUTER edges of two arcs or circles —
a slot's overall length. A click on one edge of a slot takes the slot (its two arcs); otherwise two clicks on arcs or
circles — normal, construction or projected ones alike (the user's law). One undo step; the tool ends (as Offset does) and the value box opens on a driving dimension. Arcs or circles
selected before the command are taken at once."""
import FreeCAD as App
import Part

from incad.core import log, overall_dims, safe
from incad.qt import viewport_of

PICK_PX = 8  # logical px around the pointer that count as a click on an edge
PROMPT_FIRST = "Click a slot, or the first arc or circle (Esc cancels)"
PROMPT_SECOND = "Click the second arc or circle (Esc cancels)"


class OverallDimTool:
    def __init__(self, view, sketch, on_finish=None, on_placed=None):
        self.view, self.sketch = view, sketch
        self.picked = []
        self._on_finish, self._on_placed = on_finish, on_placed
        self._input = None

    def start(self, preselected=()):
        from incad.ui.view_clicks import ViewClicks
        self._input = ViewClicks(self.view, self._qt_press, lambda x, y: None)
        self._input.start()
        for g in preselected:
            if self.take(g):
                return
        log.report("info", PROMPT_SECOND if self.picked else PROMPT_FIRST)

    def stop(self):
        if self._input is not None:
            self._input.stop()
            self._input = None
        self._highlight([])
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

    def nearest(self, pt, tol):
        """The sketch's geometry nearest the point (construction and projected edges too), within tol, or None."""
        probe = Part.Vertex(pt)
        best, best_d = None, tol
        sk = self.sketch
        for g in list(range(sk.GeometryCount)) + [-1 - k for k in range(2, len(sk.ExternalGeo))]:
            try:
                d = overall_dims.geometry(sk, g).toShape().distToShape(probe)[0]
            except Exception:
                continue
            if d <= best_d:
                best, best_d = g, d
        return best

    def click(self, pt, tol=1.0):
        g = self.nearest(pt, tol)
        if g is None:
            log.report("info", PROMPT_SECOND if self.picked else PROMPT_FIRST)
            return None
        return self.take(g)

    def take(self, g):
        """Take geometry g: a slot's edge (nothing picked yet) is the whole slot; an arc or circle is one end.
        Returns the new dimension's name once two are taken (or '' for a reference), else None."""
        sk = self.sketch
        if not self.picked and g >= 0:
            slot = overall_dims.slot_of(sk, g)
            if slot is not None:
                return self._place(*slot)
        if overall_dims.geometry(sk, g).TypeId not in overall_dims.ROUND:
            log.report("warning", "Pick an arc or a circle (or one edge of a slot)")
            return None
        if g in self.picked:
            return None
        self.picked.append(g)
        if len(self.picked) < 2:
            self._highlight(self.picked)
            log.report("info", PROMPT_SECOND)
            return None
        return self._place(*self.picked)

    def _place(self, a, b):
        from incad.core import sketch as sketches
        sk, doc = self.sketch, self.sketch.Document
        self.picked = []
        self._highlight([])

        def go():
            index = overall_dims.overall(sk, a, b)
            sketches.name_new_dimensions(doc)  # a driving one gets its dN
            return sk.Constraints[index].Name if sk.Constraints[index].Driving else ""

        result = safe.run_safely("Overall Dimension", go, doc, only=[sk])
        if not result.ok:
            log.report("info", PROMPT_FIRST)
            return None
        self.stop()
        if result.value:
            if self._on_placed is not None:
                self._on_placed(sk, result.value)
        else:
            log.report("info", "The length is already fixed: the overall dimension is a reference (it follows)")
        return result.value

    def _highlight(self, ids):
        """The first pick shows selected while the second is awaited."""
        try:
            import FreeCADGui as Gui
            Gui.Selection.clearSelection()
            for g in ids:
                Gui.Selection.addSelection(self.sketch.Document.Name, self.sketch.Name,
                                           "Edge%d" % (g + 1) if g >= 0 else "ExternalEdge%d" % (-g - 2))
        except Exception:
            pass
