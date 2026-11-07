"""While FreeCAD's Trim runs: a click on a projected edge says how to trim it (it follows the part: right-click it ▸
Break Link first, as in the user's other CAD). Every other click goes to FreeCAD's Trim as before. It stops with
Esc, a right click, or when another tool starts."""
from incad.qt import QtCore, viewport_of

_watch = None


class TrimWatch(QtCore.QObject):
    def __init__(self, view, sk):
        super().__init__()
        self.sk = sk
        self._viewport = viewport_of(view)
        self._swallow_release = False
        self._viewport.installEventFilter(self)

    def stop(self):
        try:
            self._viewport.removeEventFilter(self)
        except RuntimeError:
            pass

    def eventFilter(self, obj, event):
        if obj is not self._viewport:
            return False
        t = event.type()
        if t == QtCore.QEvent.KeyPress and event.key() == QtCore.Qt.Key_Escape:
            stop()
            return False
        if t == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.RightButton:
            stop()
            return False
        if t == QtCore.QEvent.MouseButtonRelease and self._swallow_release:
            self._swallow_release = False
            return True
        if t == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
            try:
                return self._trim_projected()
            except Exception:
                from incad.core import log
                log.logger().exception("InCad trim on a projected edge")
        return False

    def _trim_projected(self):
        import FreeCADGui as Gui
        from incad.core import log
        pre = Gui.Selection.getPreselection()
        if getattr(pre, "Object", None) is not self.sk:
            return False
        subs = [s for s in (getattr(pre, "SubElementNames", ()) or ()) if s.startswith("ExternalEdge")]
        if not subs:
            return False
        log.report("info", "That edge is projected from the part and follows it: right-click it ▸ Break Link, "
                           "then Trim")
        self._swallow_release = True
        return True


def start(sk):
    """Watch the view while FreeCAD's Trim runs on sketch `sk`."""
    global _watch
    stop()
    try:
        import FreeCADGui as Gui
        _watch = TrimWatch(Gui.ActiveDocument.ActiveView, sk)
    except Exception:
        _watch = None


def stop():
    global _watch
    if _watch is not None:
        _watch.stop()
    _watch = None
