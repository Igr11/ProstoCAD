"""InCad's sketch Offset tool (incad.core.sketch_offset): a click on an edge takes its whole outline (Cmd/Ctrl-click
adds another), the copy follows the pointer as a preview, a click places it (one undo step) and InCad's value box
opens on the offset dimension. Edges selected before the command are taken at once."""
import FreeCAD as App
import Part

from incad import host
from incad.core import log, safe, sketch_offset as so
from incad.qt import viewport_of

PICK_PX = 8  # logical px around the pointer that count as a click on an edge
PROMPT_PICK = "Click a shape to offset (%s-click adds another; Esc cancels)" % host.ADD_KEY
PROMPT_PLACE = "Move to the side and click to place the offset (Esc cancels)"


class OffsetTool:
    def __init__(self, view, sketch, on_finish=None, on_placed=None):
        self.view, self.sketch = view, sketch
        self.chains = []  # [(geometry indexes, closed)]
        self._on_finish, self._on_placed = on_finish, on_placed
        self._callbacks = []
        self._preview = self._coords = self._lines = None

    # ----- lifecycle -----
    def start(self, preselected=()):
        for g in preselected:
            self._take(g)
        from incad.ui.view_clicks import ViewClicks
        self._input = ViewClicks(self.view, self._qt_press, self._qt_move)
        self._input.start()
        self._make_preview()
        log.report("info", PROMPT_PLACE if self.chains else PROMPT_PICK)

    def stop(self):
        if getattr(self, "_input", None) is not None:
            self._input.stop()
            self._input = None
        if self._preview is not None:
            try:
                self.view.getSceneGraph().removeChild(self._preview)
            except Exception:
                pass
            self._preview = None
        if self.chains:
            self.chains = []
            self._show_taken()  # the taken outlines' selection goes with the tool
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
        hit = p + d * ((pl.Base - p).dot(normal) / denom)
        local = pl.inverse().multVec(hit)
        return App.Vector(local.x, local.y, 0)

    def _tolerance(self, x, y):
        """PICK_PX in sketch mm at this spot."""
        try:
            dpr = viewport_of(self.view).devicePixelRatioF()
        except Exception:
            dpr = 1.0
        a, b = self.to_sketch(x, y), self.to_sketch(x + PICK_PX * dpr, y)
        return (b - a).Length if a is not None and b is not None else 1.0

    def _qt_press(self, x, y, modifiers):
        from incad.qt import QtCore
        pt = self.to_sketch(x, y)
        if pt is not None:
            adding = bool(modifiers & (QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier | QtCore.Qt.ShiftModifier))
            self.click(pt, self._tolerance(x, y), adding)

    def _qt_move(self, x, y):
        pt = self.to_sketch(x, y)
        if pt is not None:
            self.hover(pt)

    def _event(self, info):
        pos = info.get("Position")
        if pos is None:
            return
        pt = self.to_sketch(*pos)
        if pt is None:
            return
        if info.get("Type") == "SoMouseButtonEvent":
            if info.get("Button") == "BUTTON1" and info.get("State") == "DOWN":
                adding = bool(info.get("CtrlDown") or info.get("ShiftDown"))
                self.click(pt, self._tolerance(*pos), adding)
        else:
            self.hover(pt)

    def nearest(self, pt, tol):
        """The sketch's geometry (construction too) nearest the point, within tol, or None."""
        probe = Part.Vertex(pt)
        best, best_d = None, tol
        for g in so._ids(self.sketch):  # projected edges too (the user's law)
            if not so._usable(self.sketch, g):
                continue
            try:
                d = so._geo(self.sketch, g).toShape().distToShape(probe)[0]
            except Exception:
                continue
            if d <= best_d:
                best, best_d = g, d
        return best

    def _take(self, g):
        try:
            chain = so.outline(self.sketch, g)
        except ValueError as e:
            log.report("warning", str(e))
            return False
        if not any(set(chain[0]) & set(c[0]) for c in self.chains):
            self.chains.append(chain)
        self._show_taken()
        return True

    def _show_taken(self):
        """The whole outlines taken show selected, not only the line clicked (the user: the colour should show the
        chain was caught)."""
        try:
            import FreeCADGui as Gui
            Gui.Selection.clearSelection()
            for chain, _closed in self.chains:
                for g in chain:
                    Gui.Selection.addSelection(self.sketch.Document.Name, self.sketch.Name, "Edge%d" % (g + 1))
        except Exception:
            pass

    def click(self, pt, tol=1.0, adding=False):
        """A click: picking an outline (nothing picked yet, or Cmd/Ctrl held), else placing the offset there."""
        if self.chains and not adding:
            try:  # a click on the shape itself (or right next to it) places nothing: away from it sets the distance
                near = min(so._wire(self.sketch, c).distToShape(Part.Vertex(pt))[0] for c, _ in self.chains)
            except Exception:
                near = tol + 1
            if near <= tol:
                log.report("info", PROMPT_PLACE)
                return None
        if not self.chains or adding:
            g = self.nearest(pt, tol)
            if g is None:
                if not self.chains:
                    log.report("info", PROMPT_PICK)
                return None
            if self._take(g):
                log.report("info", PROMPT_PLACE)
            return None
        return self.place(pt)

    def place(self, pt):
        doc = self.sketch.Document
        chains = list(self.chains)

        def go():
            sk = self.sketch
            d = so._offset(sk, chains[0][0], chains[0][1], pt)[1]  # the distance from the first outline
            made, dims = [], []
            for chain, closed in chains:  # every outline by that distance, each on the pointer's side
                new, dim = so.add(sk, chain, closed, pt, d)
                made.extend(new)
                if dim:
                    dims.append(dim)
            for other in dims[1:]:  # one dimension drives them all
                index = [c.Name for c in sk.Constraints].index(other)
                sk.setExpression("Constraints[%d]" % index, "Constraints.%s" % dims[0])
                sk.setVirtualSpace(index, True)
            safe.recompute(doc)
            return made, dims

        result = safe.run_safely("Offset", go, doc)
        self._draw([])
        if not result.ok:
            return None
        made, dims = result.value
        self.stop()
        if dims and self._on_placed is not None:
            self._on_placed(self.sketch, dims[0])
        return made

    def hover(self, pt):
        if not self.chains:
            return
        lines = []
        for chain, closed in self.chains:
            try:
                curves = so.offset_curves(self.sketch, chain, closed, pt)
            except ValueError:
                continue
            for c in curves:
                lines.append(c.toShape().discretize(48 if type(c).__name__ != "LineSegment" else 2))
        self._draw(lines)

    # ----- preview (Coin lines on the sketch plane) -----
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

    def _draw(self, polylines):
        if self._coords is None:
            return
        pts = [p for line in polylines for p in line]
        self._coords.point.setValues(0, len(pts), [(p.x, p.y, 0.01) for p in pts])
        self._coords.point.setNum(len(pts))
        if polylines:
            self._lines.numVertices.setValues(0, len(polylines), [len(line) for line in polylines])
            self._lines.numVertices.setNum(len(polylines))
        else:
            self._lines.numVertices.setNum(0)
