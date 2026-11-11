"""A brighter highlight for an edge under the pointer (the user: a part's edge lit in the same orange as a face was
hard to see; the edges are not to be thicker). FreeCAD lights faces and edges in one colour (the view's
colorHighlight), and lights the element before its selection observers hear of it, so when the pointer passes
between a face and an edge the colour is switched and the same element is lit once more, now in its own colour."""

EDGE_COLOR = (1.0, 0.27, 0.0)  # #FF4500, chosen with the user in the real window


def _same(a, b):
    return max(abs(x - y) for x, y in zip(a, b)) <= 1e-3


class EdgeHighlight:
    """Selection observer: the view's highlight colour follows the kind of element under the pointer."""

    def __init__(self):
        self.base = {}  # scene root -> the view's own highlight colour (for faces and the rest)

    @staticmethod
    def _root(doc):
        import FreeCADGui as Gui
        try:
            return Gui.getDocument(doc).ActiveView.getViewer().getSceneGraph()
        except Exception:  # no 3D view (a drawing page), or the document is closing
            return None

    def setPreselection(self, doc, obj, sub):
        root = self._root(doc)
        field = root.getField("colorHighlight") if root is not None else None
        if field is None:
            return
        now = tuple(field.getValue().getValue())
        key = id(root)
        if key not in self.base:
            self.base[key] = now if not _same(now, EDGE_COLOR) else (1.0, 0.6, 0.0)
        want = EDGE_COLOR if sub.split(".")[-1].startswith("Edge") else self.base[key]
        if _same(now, want):
            return
        field.setValue(want)
        from incad.qt import QtCore
        QtCore.QTimer.singleShot(0, lambda: self._light_again(doc, obj, sub))

    @staticmethod
    def _light_again(doc, obj, sub):
        """The element is still under the pointer: light it once more, in the colour just set."""
        import FreeCAD as App
        import FreeCADGui as Gui
        try:
            pre = Gui.Selection.getPreselection()
            if pre.DocumentName != doc or pre.ObjectName != obj or tuple(pre.SubElementNames) != (sub,):
                return
            p = pre.PickedPoints[0] if pre.PickedPoints else App.Vector()
            Gui.Selection.clearPreselection()
            Gui.Selection.setPreselection(App.getDocument(doc).getObject(obj), sub, p.x, p.y, p.z)
        except Exception:  # the element went away meanwhile
            pass

    def removePreselection(self, doc, obj, sub):
        pass
