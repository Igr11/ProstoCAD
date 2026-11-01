"""InCad mouse navigation on FreeCAD's 3D views (rules in incad.core.gestures): LMB+RMB drag orbits around the
point under the cursor, right-click opens the marking menu (or finishes the running Sketcher tool: FreeCAD's spline
and polyline tools end on a right click), right-drag flicks it, left clicks still select.
Middle-drag pan and wheel zoom stay FreeCAD's own (the shell sets the CAD style with zoom-at-cursor)."""
from incad.core import camera, gestures, log, perf
from incad.qt import QtCore, QtGui, QtWidgets

QT_BUTTONS = ((QtCore.Qt.LeftButton, gestures.LEFT), (QtCore.Qt.RightButton, gestures.RIGHT),
              (QtCore.Qt.MiddleButton, gestures.MIDDLE))
KINDS = {QtCore.QEvent.MouseButtonPress: "press", QtCore.QEvent.MouseButtonRelease: "release",
         QtCore.QEvent.MouseMove: "move", QtCore.QEvent.MouseButtonDblClick: "dblclick"}


SELECT_MODIFIERS = QtCore.Qt.ShiftModifier | QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier  # add to the selection


def to_coin(viewport, x, y):
    """Qt viewport position (logical px, top-left origin) -> Coin position (device px, bottom-left origin)."""
    dpr = viewport.devicePixelRatioF()
    return (int(round(x * dpr)), int(round((viewport.height() - y) * dpr)))


def from_coin(viewport, x, y):
    """Coin position (device px, bottom-left origin) -> Qt viewport position (logical px, top-left origin)."""
    dpr = viewport.devicePixelRatioF()
    return (x / dpr, viewport.height() - y / dpr)


def _button(qt_button):
    for q, g in QT_BUTTONS:
        if qt_button == q:
            return g
    return 0


def _held(qt_buttons):
    held = 0
    for q, g in QT_BUTTONS:
        if qt_buttons & q:
            held |= g
    return held


class Navigator(QtCore.QObject):
    def __init__(self, open_menu, on_mode_changed=None, parent=None):
        super().__init__(parent)
        self._open_menu = open_menu
        self._on_mode_changed = on_mode_changed
        self._attached = {}  # viewport -> [view, Gestures]
        self._replaying = False
        self._pivot = None
        self.mode = None
        self.click_hook = None      # callable(view, viewport, x, y) -> True takes the left click; "drag": a drag
        self._click_off = False     # (orbit) goes on as usual, a plain click is taken
        self.dblclick_hook = None   # same for left double-clicks
        self.drag_hook = None       # begin(view, viewport, x, y) on a left drag, end() on its release
        # InCad's own drags (DOF handles): press(view, viewport, x, y) -> True takes a plain left press (or the
        # double-click event Qt sends for a quick second press); its moves go to move(x, y) and its release (or a
        # lost one) to release(), all consumed — release() -> True: it was a click for FreeCAD, which gets it
        # replayed; hover(view, viewport, x, y) on every move with no button (the event still reaches FreeCAD)
        self.handle_hook = None
        # the Dimension tool's dead zone (incad.ui.dimension_place): move(view, viewport, x, y) -> None or the
        # (x, y) FreeCAD gets instead for a plain move; press() on every press
        self.place_hook = None
        self.move_hook = None  # callable(view, viewport, x, y) on every plain move (no button): a point pick's plane
        self._handle_viewport = None  # the viewport of a handle drag going on
        self._handle_press = None     # (x, y, modifiers) of its press
        self._swallow_release = False

    def attached(self):
        return list(self._attached)

    def attach_all(self):
        import FreeCAD as App
        import FreeCADGui as Gui
        for name in App.listDocuments():
            gdoc = Gui.getDocument(name)
            if gdoc is None:
                continue
            for view in gdoc.mdiViewsOfType("Gui::View3DInventor"):
                try:
                    viewport = view.graphicsView().viewport()
                except Exception:
                    continue
                if viewport in self._attached:
                    continue
                machine = gestures.Gestures()
                machine.mode = self.mode
                self._attached[viewport] = [view, machine, QtCore.Qt.NoModifier]  # + modifiers at the last press
                viewport.installEventFilter(self)
                viewport.destroyed.connect(lambda _=None, v=viewport: self._attached.pop(v, None))

    def detach_all(self):
        self._end_handle()
        for viewport in list(self._attached):
            try:
                viewport.removeEventFilter(self)
            except RuntimeError:
                pass
        self._attached.clear()

    def set_mode(self, mode):
        self.mode = mode
        for entry in self._attached.values():
            entry[1].mode = mode
            entry[1].reset()
        if self._on_mode_changed is not None:
            self._on_mode_changed(mode)

    @perf.timed("view mouse")
    def eventFilter(self, obj, event):
        if self._replaying:
            return False
        entry = self._attached.get(obj)
        kind = KINDS.get(event.type())
        if entry is None or kind is None:
            return False
        view, machine = entry[0], entry[1]
        try:
            if kind == "press":
                entry[2] = event.modifiers()
            pos = event.position()
            if self._handle_viewport is not None and obj is self._handle_viewport:
                return self._handle_event(kind, event, pos)
            if self.place_hook is not None:
                if kind == "press":
                    self.place_hook.press()
                elif kind == "move" and event.buttons() == QtCore.Qt.NoButton and machine.state == "idle":
                    instead = self.place_hook.move(view, obj, pos.x(), pos.y())
                    if instead is not None:
                        self._move_instead(obj, instead, event.modifiers())
                        return True
            if self.move_hook is not None and kind == "move" and event.buttons() == QtCore.Qt.NoButton:
                self.move_hook(view, obj, pos.x(), pos.y())
            if self.handle_hook is not None and self.mode is None and machine.state == "idle":
                if kind in ("press", "dblclick") and event.button() == QtCore.Qt.LeftButton \
                        and event.buttons() == QtCore.Qt.LeftButton and not event.modifiers() & SELECT_MODIFIERS \
                        and self._call_handle("press", view, obj, pos.x(), pos.y()):
                    self._handle_viewport = obj
                    self._handle_press = (pos.x(), pos.y(), event.modifiers())
                    return True
                if kind == "move" and event.buttons() == QtCore.Qt.NoButton:
                    self._call_handle("hover", view, obj, pos.x(), pos.y())
            if event.button() == QtCore.Qt.LeftButton:
                if kind == "press":
                    self.press_modifiers = event.modifiers()  # the click hook's Shift-click (a chain in a sketch)
                taken = kind == "press" and machine.state == "idle" and self.click_hook is not None \
                    and self.click_hook(view, obj, pos.x(), pos.y())
                if taken == "drag":  # off the markers: the press goes on to the gesture rules (left drag orbits)
                    self._click_off = True
                elif taken:
                    self._swallow_release = True
                    return True
                if kind == "release" and self._swallow_release:
                    self._swallow_release = False
                    return True
                if kind == "dblclick" and self.dblclick_hook is not None \
                        and self.dblclick_hook(view, obj, pos.x(), pos.y()):
                    return True
            actions = machine.feed(kind, _button(event.button()), _held(event.buttons()), pos.x(), pos.y())
            if self._click_off and actions and actions[0][0] in ("replay_press", "drag_start", "orbit_start"):
                self._click_off = False
                if actions[0][0] == "replay_press":  # a plain click off the markers: nothing to select there
                    return True
            for action in actions:
                self._do(obj, view, action, entry[2])
            return gestures.PASS not in actions
        except Exception:
            log.logger().exception("InCad navigation")
            dragging = machine.state == "passthrough"
            machine.reset()
            if dragging and self.drag_hook is not None:  # the reset drops the drag's release: end it now
                try:
                    self.drag_hook.end()
                except Exception:
                    log.logger().exception("InCad drag")
            return False

    def _call_handle(self, name, *args):
        try:
            return bool(getattr(self.handle_hook, name)(*args))
        except Exception:
            log.logger().exception("InCad DOF handle")
            return False

    def _handle_event(self, kind, event, pos):
        """A handle drag owns the mouse: its moves and release go to the hook, never to FreeCAD."""
        if kind == "move" and not event.buttons() & QtCore.Qt.LeftButton:
            self._end_handle()  # its release got lost (Cmd-Tab, a dialog): the drag ends, the move is FreeCAD's
            return False
        if kind == "move":
            self._call_handle("move", pos.x(), pos.y())
        elif kind == "release" and event.button() == QtCore.Qt.LeftButton:
            self._end_handle(replay=True)
        return True

    def _end_handle(self, replay=False):
        """The handle drag's end; replay: a release that ended a click the hook gives back goes to FreeCAD as
        that click (it selects or clears the selection as usual)."""
        viewport, press = self._handle_viewport, self._handle_press
        if viewport is not None:
            self._handle_viewport = self._handle_press = None
            if self.handle_hook is not None and self._call_handle("release") and replay and press is not None:
                try:
                    self._replay(viewport, press[0], press[1], press[2], QtCore.Qt.LeftButton, release=True)
                except RuntimeError:
                    pass

    def _do(self, viewport, view, action, press_modifiers):
        name = action[0]
        if name in ("drag_start", "drag_end"):
            if self.drag_hook is not None:
                try:
                    if name == "drag_start":
                        self.drag_hook.begin(view, viewport, action[1], action[2])
                    else:
                        self.drag_hook.end()
                except Exception:
                    log.logger().exception("InCad drag")
            return
        if name == "replay_press":
            self._replay(viewport, action[1], action[2], press_modifiers)
        elif name == "orbit_start":
            self._pivot = self._pivot_at(view, viewport, action[1], action[2])
        elif name == "orbit":
            cam = view.getCameraNode()
            camera.orbit(cam, self._pivot if self._pivot is not None else camera.focal_point(cam), action[1], action[2])
        elif name == "orbit_end":
            self._pivot = None
        elif name == "pan":
            camera.pan_pixels(view.getCameraNode(), action[1], action[2], viewport.height())
        elif name == "menu" and self.tool_cursor(viewport):
            self._replay(viewport, action[1], action[2], press_modifiers, QtCore.Qt.RightButton, release=True)
        elif name in ("menu", "flick_start"):
            local = QtCore.QPointF(action[1], action[2])
            self._open_menu(view, viewport, local, viewport.mapToGlobal(local.toPoint()), name == "flick_start")

    @staticmethod
    def tool_cursor(viewport):
        """True while a Sketcher tool runs in this view: it shows its own bitmap cursor, and FreeCAD tells Python
        nothing else. A plain right click then belongs to the tool (it finishes a spline or a polyline, ends an
        empty tool), not to the marking menu; a right drag still flicks the menu."""
        try:
            return viewport.cursor().shape() == QtCore.Qt.BitmapCursor
        except RuntimeError:
            return False

    def _move_instead(self, viewport, at, modifiers):
        """FreeCAD gets a plain move to `at` (logical px) instead of the one the mouse made."""
        local = QtCore.QPointF(at[0], at[1])
        glob = QtCore.QPointF(viewport.mapToGlobal(local.toPoint()))
        self._replaying = True
        try:
            QtWidgets.QApplication.sendEvent(viewport, QtGui.QMouseEvent(
                QtCore.QEvent.MouseMove, local, glob, QtCore.Qt.NoButton, QtCore.Qt.NoButton, modifiers))
        finally:
            self._replaying = False

    def _replay(self, viewport, x, y, modifiers, button=QtCore.Qt.LeftButton, release=False):
        """Hand FreeCAD a press InCad held back (and its release, for a right click a Sketcher tool finishes on)."""
        local = QtCore.QPointF(x, y)
        glob = QtCore.QPointF(viewport.mapToGlobal(local.toPoint()))
        events = [QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, local, glob, button, button, modifiers)]
        if release:
            events.append(QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease, local, glob, button,
                                            QtCore.Qt.NoButton, modifiers))
        self._replaying = True
        try:
            for ev in events:
                QtWidgets.QApplication.sendEvent(viewport, ev)
        finally:
            self._replaying = False

    @staticmethod
    def _pivot_at(view, viewport, x, y):
        info = view.getObjectInfo(to_coin(viewport, x, y))
        if info:
            return (info["x"], info["y"], info["z"])
        return None
