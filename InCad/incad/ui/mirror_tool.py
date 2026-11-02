"""Sketch ▸ Mirror (incad.core.sketch_mirror), tool first (the user's law: the tool, then clicks on the sketch). The
first click takes the shape under it (a side stands for its whole outline), Cmd/Ctrl/Shift-clicks take more shapes
(or let one go); the next plain click picks the mirror line — a line under the pointer first (normal, construction
or projected; one of the shapes' own lines too: a half profile mirrored about its closing line), else the X / Y axis,
else a point — and the copy is made (one undo step, the copy selected), the tool ends. Shapes selected before the
command are taken at once, and the first click is the mirror line then. Esc cancels."""
from incad.core import log, safe, sketch_mirror, sketch_symmetric as sym
from incad.ui.overall_dim_tool import OverallDimTool

EDGE_PX = 8
POINT_PX = 10
PROMPT = "Mirror: click the mirror line — a line, the X or Y axis — or a point (Esc cancels)"
PROMPT_SHAPES = "Mirror: click the shape to mirror; Cmd/Ctrl-click takes more (Esc cancels)"


def outlines(sk, picks):
    """The geometry of the shapes picked (sketch_symmetric picks): an edge stands for its whole outline; the
    sketch's own geometry only (the axes and projected edges are mirror lines, not shapes)."""
    from incad.core import sketch_offset as so
    geos = []
    for pick in picks:
        if pick[1] in (sym.H_AXIS, sym.V_AXIS) or (pick[0] == "point" and pick[1] < 0):
            continue  # the axes and the origin are mirror lines, never shapes; projected edges are shapes too
        whole = [pick[1]]
        if pick[0] == "edge":
            try:
                whole = so.outline(sk, pick[1])[0]
            except (ValueError, IndexError):
                pass
        geos += [g for g in whole if g not in geos]
    return geos


def mirror_pick_at(sk, pt, vertex_tol, edge_tol):
    """The mirror for a click: a line or axis first (a click near a line's end means the line), else a point."""
    return sym.pick_at(sk, pt, 0.0, edge_tol) or sym.pick_at(sk, pt, vertex_tol, 0.0)


class MirrorTool:
    to_sketch = OverallDimTool.to_sketch

    def __init__(self, view, sketch, geos, on_finish=None, on_done=None):
        self.view, self.sketch, self.geos = view, sketch, list(geos)
        self._on_finish, self._on_done = on_finish, on_done
        self._input = None
        self.copies = None

    def start(self):
        from incad.ui.view_clicks import ViewClicks
        self._input = ViewClicks(self.view, self._qt_press, lambda x, y: None)
        self._input.start()
        self._select(self.geos)
        log.report("info", PROMPT if self.geos else PROMPT_SHAPES)

    def stop(self):
        if self._input is not None:
            self._input.stop()
            self._input = None
        if self.copies is None:
            self._select([])
        if self._on_finish is not None:
            self._on_finish(self)

    def _qt_press(self, x, y, modifiers):
        pt = self.to_sketch(x, y)
        if pt is None:
            return
        try:
            dpr = self.view.graphicsView().viewport().devicePixelRatioF()
        except Exception:
            dpr = 1.0
        far = self.to_sketch(x + EDGE_PX * dpr, y)
        edge_tol = (far - pt).Length if far is not None else 1.0
        from incad.qt import QtCore
        adding = bool(modifiers & (QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier | QtCore.Qt.ShiftModifier))
        self.click(pt, edge_tol, adding)

    def click(self, pt, edge_tol=1.0, adding=False):
        """A click: a shape while none is taken or with a modifier (adding), else the mirror line."""
        if adding or not self.geos:
            return self.take_shape(sym.pick_at(self.sketch, pt, 0.0, edge_tol))
        return self.click_mirror(pt, edge_tol)

    def take_shape(self, pick):
        """The shape of a pick taken (or let go again, if it was): returns the shapes' geometry now."""
        if pick is None or pick[0] != "edge" or pick[1] in (sym.H_AXIS, sym.V_AXIS):
            log.report("info", PROMPT if self.geos else PROMPT_SHAPES)
            return self.geos
        whole = outlines(self.sketch, [pick])
        if whole and all(g in self.geos for g in whole):
            self.geos = [g for g in self.geos if g not in whole]
        else:
            self.geos += [g for g in whole if g not in self.geos]
        self._select(self.geos)
        log.report("info", PROMPT if self.geos else PROMPT_SHAPES)
        return self.geos

    def click_mirror(self, pt, edge_tol=1.0):
        pick = mirror_pick_at(self.sketch, pt, edge_tol * POINT_PX / EDGE_PX, edge_tol)
        if pick is None:
            log.report("info", PROMPT)
            return None
        return self.take(pick)

    def take(self, pick):
        sk = self.sketch
        r = safe.run_safely("Mirror", lambda: sketch_mirror.mirror(sk, self.geos, pick), sk.Document, only=[sk])
        if not r.ok:
            log.report("warning", "Mirror: %s" % r.error)  # the shapes stay picked: click another mirror line
            return None
        self.copies = r.value
        self._select(self.copies)
        log.report("info", "Mirrored %d: the copy follows its original (right-click it ▸ Break Mirror Link frees it)"
                   % len(self.copies))
        if self._on_done is not None:
            self._on_done(self.copies)
        self.stop()
        return self.copies

    def _select(self, geos):
        try:
            import FreeCADGui as Gui
            Gui.Selection.clearSelection()
            for g in geos:
                Gui.Selection.addSelection(self.sketch.Document.Name, self.sketch.Name,
                                           "Edge%d" % (g + 1) if g >= 0 else "ExternalEdge%d" % (-g - 2))
        except Exception:
            pass
