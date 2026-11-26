"""The new sketch open in the old program's window (ProstoCAD Try — 0.18 New Sketch): the new program's SketchView as a
see-through layer over the 3D view, turned exactly as the camera sees the sketch's plane (a perspective map from four
points of the plane), the 3D view's camera following the sketch's own zoom and pan (the user, 2026-11-07: the new
program's way). Open: the camera turns to face the sketch as Sketcher turned it; Finish: the sketch is written back
(convert.write, one undo step) and the camera turns back to where it was, as the old program does.
Ribbon commands of the Sketch tab come here while a sketch is open (commands.Registry._run)."""
import json
import math

from incad.core import log
from incad.qt import QtCore, QtGui, QtWidgets

_CURRENT = None  # the open session

# the new program's Sketch-tab commands (prostocad/main.py RECT_COMMANDS, CONSTRAINT_COMMANDS)
RECT_COMMANDS = {"sketch.rectangle": "rect", "sketch.rectangle_center": "rect_center",
                 "sketch.rectangle_3pt": "rect_3pt", "sketch.rectangle_center_3pt": "rect_center_3pt",
                 "sketch.rectangle_rounded": "rect_rounded", "sketch.arc": "arc", "sketch.arc_center": "arc_center",
                 "sketch.arc_tangent": "arc_tangent", "sketch.circle": "circle", "sketch.circle_3pt": "circle_3pt",
                 "sketch.circle_tangent": "circle_tangent",
                 "sketch.ellipse_3pt": "ellipse_3pt", "sketch.arc_ellipse": "arc_ellipse", "sketch.slot": "slot_c2c",
                 "sketch.slot_center": "slot_center", "sketch.slot_arc": "slot_arc", "sketch.polygon": "polygon",
                 "sketch.point": "point", "sketch.text": "text"}
CONSTRAINT_COMMANDS = {"sketch.coincident": "Coincident", "sketch.concentric": "Concentric",
                       "sketch.horizontal": "Horizontal", "sketch.vertical": "Vertical", "sketch.parallel": "Parallel",
                       "sketch.collinear": "Collinear", "sketch.perpendicular": "Perpendicular",
                       "sketch.tangent": "Tangent", "sketch.equal": "Equal", "sketch.symmetric": "Symmetric",
                       "sketch.fix": "Fix"}
ROUTED = {"edit.undo", "edit.redo", "edit.delete", "view.look_at"}  # the old program's own commands the open sketch
# answers (Look At: straight at the sketch again, as the new program's Look At in a sketch)
CUBE = 170  # px: the 3D view's navigation cube, top right; its clicks go to the 3D view (the layer leaves it out)
STILL_MS = 150  # the camera still this long (and no button held for a turn): the turn is over, the grid is drawn again
RIGHT_HOLD_MS = 120  # a lone right press waits this long for the left one (a turn) before the ring takes it (the user)
RIGHT_HOLD_PX = 4  # or until the pointer moves this far (the ring's flick)
TURN_MS = 250  # the camera's turn to the sketch and back, as FreeCAD's own turn in the old program (its default)


def current():
    return _CURRENT


def editing():
    """The sketch object open in the new sketch, or None."""
    return _CURRENT.sk if _CURRENT is not None else None


def handles(cmd_id):
    return _CURRENT is not None and (cmd_id.startswith("sketch.") or cmd_id in ROUTED)


def prompt():
    """What the new sketch said last (its tool's words), or None."""
    return getattr(_CURRENT, "last_prompt", None) if _CURRENT is not None else None


def command(cmd_id):
    return _CURRENT.command(cmd_id) if _CURRENT is not None else None


_LAYERS = []  # every layer made: taken apart before the program quits


def _before_quit():
    """The new sketch's widgets taken apart while Qt is still there: left to Python's own exit they were taken apart
    after Qt, and the program crashed on Quit (shiboken's recursive_invalidate, seen twice on 2026-11-07)."""
    global _CURRENT
    try:
        import shiboken6
    except ImportError:
        return
    _CURRENT = None
    for lay in _LAYERS:
        try:
            if shiboken6.isValid(lay):
                shiboken6.delete(lay)
        except Exception:
            pass
    _LAYERS.clear()
    for w in QtWidgets.QApplication.topLevelWidgets():  # the new sketch's own windows (boxes, lists) made without a parent
        try:
            if type(w).__module__.startswith("prostocad") and shiboken6.isValid(w):
                shiboken6.delete(w)
        except Exception:
            pass
    import gc
    gc.collect()


_HOOKED = []


def _hook_quit():
    if not _HOOKED:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(_before_quit)
            _HOOKED.append(True)


def open_sketch(sk, on_change=None, fresh=False):
    """The new sketch on `sk` in the active 3D view. fresh: the sketch was made for this opening (its "New Sketch" step
    the last one): Cancel, or Undo past its first step, takes it away again."""
    global _CURRENT
    _hook_quit()
    if _CURRENT is not None:
        _CURRENT.finish()
    _CURRENT = Session(sk, on_change, fresh)
    return _CURRENT


def _sketch_view_class():
    from prostocad import feature_window  # noqa: F401 (first: it and sketch import each other)
    from prostocad import sketch as nsk

    class Layer(nsk.SketchView):
        """The SketchView drawn see-through over the 3D view; what was drawn before is cleared each time."""

        hide_grid = False  # the view turning: no grid (the user: it is not needed then, and the turn is smooth)
        show_grid = True  # Settings ▸ Sketch ▸ Grid (the new program's; the snap works either way)

        def _paint_grid(self, g, w, h):
            if self.hide_grid or not self.show_grid:
                return None
            return super()._paint_grid(g, w, h)

        def update(self, *args):
            """Drawn in the 3D view's frame (frame_item): the 3D view is asked to draw again, the layer itself (empty,
            see-through) is not; both drawn on every move of the pointer, they flickered (the user, 2026-11-11; the new
            program has no layer over its view)."""
            item = getattr(self, "frame_item", None)
            if item is not None and not args:
                item.update()
                return
            super().update(*args)

        after_pan = None  # the session's camera push: the part moves with the sketch on every move of a pan
        part_targets = None  # the part's edges and corners in the sketch's plane (project.auto_targets)
        PART_REST_MS = 1000  # the part's magnets light only once the pointer rests this long over the sketch (the
        # user, 2026-11-11: lit on every move they flickered under the pointer)
        PART_REST_PX = 3

        def _rest_timer(self):
            t = getattr(self, "_part_rest", None)
            if t is None:
                t = self._part_rest = QtCore.QTimer(self)
                t.setSingleShot(True)
                t.setInterval(self.PART_REST_MS)
                t.timeout.connect(self._rested)
            return t

        def _rested(self):
            """The pointer has rested: the part's magnets are offered, and the spot under it looked at again."""
            if self.part_targets is None or self.host_targets is not None:
                return
            self.host_targets = self.part_targets
            try:
                at = self.mapFromGlobal(QtGui.QCursor.pos())
                if self.rect().contains(at):
                    ev = QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(at), QtCore.QPointF(QtGui.QCursor.pos()),
                                           QtCore.Qt.NoButton, QtWidgets.QApplication.mouseButtons(),
                                           QtWidgets.QApplication.keyboardModifiers())
                    nsk.SketchView.mouseMoveEvent(self, ev)
            except Exception:
                pass

        def mouseMoveEvent(self, e):
            if self.part_targets is not None:
                was = getattr(self, "_rest_at", None)
                p = e.position()
                if was is None or abs(p.x() - was.x()) + abs(p.y() - was.y()) > self.PART_REST_PX:
                    self._rest_at = QtCore.QPointF(p)
                    self.host_targets = None  # moving: no part magnets until it rests again
                    self._rest_timer().start()
            super().mouseMoveEvent(e)
            if self._pan_from is not None and self.after_pan is not None:
                self.after_pan()  # (on the 30 ms timer alone the part lagged behind the sketch and jerked: the user)

        frame_item = None  # the sketch drawn in the 3D view's own frame (_FrameItem): the layer itself draws nothing

        def paintEvent(self, _e):
            g = QtGui.QPainter(self)
            g.setCompositionMode(QtGui.QPainter.CompositionMode_Source)
            g.fillRect(self.rect(), QtCore.Qt.transparent)
            g.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
            if self.frame_item is None:
                self.paint_into(g)
            g.end()
            if self.frame_item is not None:
                self.frame_item.update()  # the 3D view draws it again, with the part, in one frame
    return Layer


def _gl_unpack_reset(g):
    """OpenGL's pixel-store state back to its defaults before Qt draws the sketch: Coin leaves its own (row length,
    alignment) after the part, and Qt's letters, uploaded as textures under them, came out as stripes (the user's
    dimension values)."""
    g.beginNativePainting()
    try:
        ctx = QtGui.QOpenGLContext.currentContext()
        f = ctx.functions() if ctx is not None else None
        if f is not None:
            for name, value in ((0x0CF2, 0), (0x0CF3, 0), (0x0CF4, 0), (0x0CF5, 4),  # UNPACK row length, skips, align
                                (0x0D02, 0), (0x0D03, 0), (0x0D04, 0), (0x0D05, 4)):  # PACK row length, skips, align
                f.glPixelStorei(name, value)
    except Exception:
        log.logger().exception("new sketch: OpenGL pixel store")
    finally:
        g.endNativePainting()


class _FrameItem(QtWidgets.QGraphicsItem):
    """The open sketch drawn by the 3D view itself, in the same frame as the part, right after it (the new program's
    view3d.paintGL draws it so: sketch_view.paint_into, its camera matched first by _sync_sketch_camera). FreeCAD's
    view is a QGraphicsView over OpenGL: an item of its scene is painted in each of its frames, in the viewport's own
    coordinates. Drawn as a see-through layer of its own the sketch and the part came in different frames (the user:
    panning lagged; in the new program it is perfect)."""

    def __init__(self, session):
        super().__init__()
        self.session = session
        self.setZValue(1e6)

    def boundingRect(self):
        return QtCore.QRectF(-1e7, -1e7, 2e7, 2e7)

    def paint(self, g, _option, _widget=None):
        s = self.session
        try:
            import shiboken6
            if s.layer is None or not shiboken6.isValid(s.layer) or not s.layer.isVisible():
                return
            _gl_unpack_reset(g)
            g.save()
            g.resetTransform()
            s._tilt(update=False)  # the camera as this frame draws the part
            s.layer.paint_into(g)
            g.restore()
        except Exception:
            log.logger().exception("new sketch: drawn in the 3D view")


class _Orbit(QtCore.QObject):
    """The new program's turning in a sketch (its main.py eventFilter and view3d mouse*Event), on the layer: the left
    and right buttons held together turn the part round the spot of the sketch's plane under the pointer; with Orbit on
    (the strip) the left button turns it and the sketch gets no clicks. Everything else goes on to the sketch."""
    MOUSE = (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease, QtCore.QEvent.MouseMove,
             QtCore.QEvent.MouseButtonDblClick)

    def __init__(self, session):
        super().__init__(session.layer)
        self.session = session
        self.at = None
        self.pivot = None
        self.held = None  # a lone right press held back RIGHT_HOLD_MS: the left one may follow (a turn), not the ring
        self._replaying = False
        self.hold_timer = QtCore.QTimer(self)
        self.hold_timer.setSingleShot(True)
        self.hold_timer.setInterval(RIGHT_HOLD_MS)
        self.hold_timer.timeout.connect(self._let_go)

    def _let_go(self):
        """The held right press handed on to the sketch (its ring opens as before)."""
        held, self.held = self.held, None
        self.hold_timer.stop()
        if held is None:
            return
        pos, glob, mods = held
        lay = self.session.layer
        self._replaying = True
        try:
            QtWidgets.QApplication.sendEvent(lay, QtGui.QMouseEvent(
                QtCore.QEvent.MouseButtonPress, pos, glob, QtCore.Qt.RightButton, QtCore.Qt.RightButton, mods))
        finally:
            self._replaying = False

    @staticmethod
    def _orbit_on():
        try:
            from incad.ui import shell
            return shell.nav_mode() == "orbit"
        except Exception:
            return False

    def eventFilter(self, obj, e):
        """As the old program's gestures (incad.core.gestures): once both buttons are down (or the left one with Orbit
        on) the drag turns the view on every move until all buttons are up, whatever buttons a move says are held
        (on a Mac a move with both held came as one button: most moves went to the sketch, the view turned in jerks)."""
        if e.type() not in self.MOUSE or self._replaying:
            return False
        L, R = QtCore.Qt.LeftButton, QtCore.Qt.RightButton
        try:
            if self.held is not None:  # the right press waits: the left one now is a turn (the user: both pressed
                # together, the ring took the right one)
                if e.type() == QtCore.QEvent.MouseButtonPress and e.button() == L:
                    self.hold_timer.stop()
                    self.held = None
                    self.at, self.pivot = e.position(), self.session._pivot(e.position())
                    return True
                if e.type() == QtCore.QEvent.MouseButtonRelease or (
                        e.type() == QtCore.QEvent.MouseMove and
                        (e.position() - self.held[0]).manhattanLength() > RIGHT_HOLD_PX):
                    self._let_go()  # a right click, or a flick: the sketch's, as before
                return False
            if self.at is None and e.type() == QtCore.QEvent.MouseButtonPress and e.button() == R \
                    and not (e.buttons() & L):
                self.held = (QtCore.QPointF(e.position()), QtCore.QPointF(e.globalPosition()), e.modifiers())
                self.hold_timer.start()
                return True
            if self.at is not None:  # turning
                if e.type() == QtCore.QEvent.MouseMove and e.buttons() == QtCore.Qt.NoButton \
                        and not QtWidgets.QApplication.mouseButtons():
                    self.at = None  # a release got lost (Cmd-Tab, a dialog): the turn ends, as the old gestures end it
                    self.session._still.start()
                    return False
                if e.type() == QtCore.QEvent.MouseMove:
                    dx, dy = e.position().x() - self.at.x(), e.position().y() - self.at.y()
                    if dx or dy:
                        self.at = e.position()
                        self.session._orbit(self.pivot, dx, dy)
                elif e.type() == QtCore.QEvent.MouseButtonRelease:
                    if not (e.buttons() & (L | R)):
                        self.at = None
                        self.session._still.start()  # the grid back once the view rests
                    return False  # the releases go on to the sketch, as in the new program
                return True
            both = bool(e.buttons() & L) and bool(e.buttons() & R)
            starts = e.type() == QtCore.QEvent.MouseButtonPress and (
                both or (e.button() == L and self._orbit_on()))
            if starts:  # each press: the spot it turns about (view3d)
                self.at, self.pivot = e.position(), self.session._pivot(e.position())
                return True
        except Exception:
            self.at = None
            log.logger().exception("new sketch: turning the part")
        return False


from incad.sketch_next.menu import RING_TOOLS, Menu  # noqa: E402
from incad.sketch_next.windows import Windows  # noqa: E402
from incad.sketch_next.project import Project  # noqa: E402


class Session(Windows, Menu, Project):
    def __init__(self, sk, on_change=None, fresh=False):
        import FreeCADGui as Gui
        from incad.qt import viewport_of
        from incad.sketch_next import convert
        self.sk, self.on_change = sk, on_change
        self.fresh = bool(fresh) and sk.Document.UndoNames[:1] == ["New Sketch"]
        from incad.core import settle
        self._quiet = settle.Quiet(sk.Document)
        self._undo_at_open = len(sk.Document.UndoNames)  # the document's steps while the sketch is open (an edge
        # projected, a point tied to the part) are taken back by Cancel
        self.view = Gui.getDocument(sk.Document.Name).ActiveView
        self.vp = viewport_of(self.view)
        self.gv = self.vp.parentWidget()
        self.camera_before = self.view.getCamera()  # put back on Finish, as Sketcher does
        self.tv = self._hide_later(sk)  # what is built on the sketch hidden meanwhile, as the old sketch did
        self.was_shown = bool(sk.ViewObject.Visibility)  # the sketch's own drawing hidden under the layer meanwhile
        sk.ViewObject.Visibility = False
        from incad.features import sketch_text as T
        self.texts_shown = {}  # its Text objects too: the layer draws their letters as they move
        for t in T.texts_of(sk):
            try:
                self.texts_shown[t.Name] = bool(t.ViewObject.Visibility)
                t.ViewObject.Visibility = False
            except Exception:
                pass
        data, self.report = convert.read(sk)
        self.layer = _sketch_view_class()(parent=self.gv)
        lay = self.layer
        _LAYERS.append(lay)
        lay.under3d = True
        lay.left_inset = 0
        lay.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        lay.setAutoFillBackground(False)
        lay.setGeometry(self.vp.geometry())
        from incad.sketch_next import texts
        from prostocad import feature_window
        feature_window.FONTS = texts.fonts  # the Text window lists the fonts the part can build letters from
        from prostocad import theme as next_theme
        # the sketch's windows (Text, Polygon, Fillet, patterns, the Constraint List) are drawn by the new program's
        # window style, which its main window sets on the whole application; here it is set on the layer they stand
        # in (without it they had no background: the grid showed through them)
        lay.setStyleSheet(next_theme.qss(next_theme.cur()) + feature_window.qss(next_theme.cur()))  # (and the size
        # boxes: the app's own style of the new program, main.py, set there on the whole application; here on the layer)
        lay.text_source = texts.outlines  # and the sketch draws the letters the Text object gets
        try:
            lay.foreign = texts.names(sk)  # formulas see the part's other named values (a Text's d12)
        except Exception:
            log.logger().exception("new sketch: the part's named values")
        lay.load(data)
        self._data_open = self._drawn()  # as opened: Finish without a change
        # writes nothing, Undo back to it in a new sketch takes the sketch away
        lay.editing = True
        lay.prompt.connect(self._prompt)
        # as the new program (main.py _count / _on_prompt -> _strip_recount): once things are at rest, reference
        # dimensions follow the sketch and each line's state (free, part, held) is worked out again
        self._recount_timer = QtCore.QTimer(lay)
        self._recount_timer.setSingleShot(True)
        self._recount_timer.setInterval(120)
        self._recount_timer.timeout.connect(self._recount)
        lay.changed.connect(self._recount_timer.start)
        lay.prompt.connect(lambda _t: self._recount_timer.start())
        self._windows_start()
        from incad.ui import settings_panel
        settings_panel.apply_to(lay)  # ⚙ Settings: marks, dimensions, colours, grid, angles, the drawing's text size
        lay.after_pan = self._push
        self._menu_start()
        self._project_start_up()
        self.last_cmd = None
        self._auto_start_up()
        self._frame = None
        try:
            scene = self.view.graphicsView().scene()
            if scene is not None:
                self._frame = _FrameItem(self)
                scene.addItem(self._frame)
                self._frame_scene = scene
                lay.frame_item = self._frame
        except Exception:
            log.logger().exception("new sketch: drawn in the 3D view (the layer draws it instead)")
            self._frame = None
        lay.show()
        lay.raise_()
        for w in self.gv.children():  # the view's own buttons (the strip on the right) stay above the layer
            if isinstance(w, QtWidgets.QWidget) and w is not lay and w is not self.vp and w.isVisible():
                w.raise_()
        self._mask()
        lay.setFocus()
        self._keep_view()  # the old program (Sketcher): the view only turns, its middle and zoom stay
        self._pushed = None
        self._seen = None
        self._orbiter = _Orbit(self)  # the part turned in the sketch, as in the new program
        lay.installEventFilter(self._orbiter)
        self.timer = QtCore.QTimer(lay)
        self.timer.timeout.connect(self._follow)
        self.timer.start(30)
        self._cam_sensor = None
        self._still = QtCore.QTimer(lay)  # the view at rest this long: its grid back
        self._still.setSingleShot(True)
        self._still.setInterval(STILL_MS)
        self._still.timeout.connect(self._at_rest)
        self._watch_camera()
        try:
            self.view.stopAnimating()  # a turn of the view on its way (the cube, a standard view) ends first: it
            # went on after the sketch's own turn and took the view back (the sketch was left seen at a slant)
        except Exception:
            pass
        self._turn_to(self._facing())
        self._recount()

    def _recount(self):
        import shiboken6
        lay = self.layer
        try:
            if not shiboken6.isValid(lay):
                return
        except Exception:
            return
        lay.dim_follow_reference()
        lay.free_count()
        lay.update()
        try:
            self._auto_check()
        except Exception:
            log.logger().exception("new sketch: auto-projection")

    # ----- the camera and the layer -----
    def _placement(self):
        return self.sk.getGlobalPlacement()

    def _facing(self):
        """The camera turned to face the sketch: its x to the right, its y up (as Sketcher turns it)."""
        return self._placement().Rotation

    def _cam(self):
        return self.view.getCameraNode()

    def _turn_to(self, rot, then=None):
        """The camera turned to `rot` (a FreeCAD Rotation) in TURN_MS round the point it looks at, as FreeCAD's own turn
        to a sketch goes (round the sketch's middle it swung sideways at the first frame and rocked on the way; the
        user: awkward). Its zoom stays. Timed by the clock from the first frame drawn, once the opening's own work is
        done (started at once, the opening ate the first part of the turn and it jumped); eased in and out."""
        import FreeCAD as App
        from incad.core import camera
        cam = self._cam()
        q = cam.orientation.getValue().getValue()  # (x, y, z, w)
        start = App.Rotation(q[0], q[1], q[2], q[3])
        f = camera.focal_point(cam).getValue()
        focal = App.Vector(f[0], f[1], f[2])
        dist = cam.focalDistance.getValue()
        clock = QtCore.QElapsedTimer()
        timer = QtCore.QTimer(self.layer)

        def put(r):
            x, y, z, w = r.Q
            cam.orientation.setValue(x, y, z, w)
            p = focal - r.multVec(App.Vector(0, 0, -1)) * dist
            cam.position.setValue(p.x, p.y, p.z)

        def step():
            if not clock.isValid():
                clock.start()
            t = min(1.0, clock.elapsed() / float(TURN_MS))
            e = t * t * (3 - 2 * t)  # eased: no jolt at the start or the end
            put(start.slerp(rot, e) if hasattr(start, "slerp") else rot)
            if t >= 1.0:
                timer.stop()
                self._push(force=True)  # the sketch's middle and zoom, exact (the same line of sight)
                if then is not None:
                    then()
        timer.timeout.connect(step)
        vp = self.vp

        class _FirstFrame(QtCore.QObject):  # the turn starts with the view's first frame after the opening's work
            def eventFilter(self, obj, e):
                if e.type() == QtCore.QEvent.Paint:
                    vp.removeEventFilter(self)
                    if not timer.isActive() and not clock.isValid():
                        timer.start(15)
                return False
        self._first_frame = _FirstFrame(self.layer)
        vp.installEventFilter(self._first_frame)
        vp.update()
        QtCore.QTimer.singleShot(400, timer, lambda: timer.isActive() or clock.isValid() or timer.start(15))  # no
        # frame came (a hidden view): the turn goes anyway; tied to the timer, nothing runs once the sketch is gone

    def _push(self, force=False):
        """The camera put where the sketch's middle and zoom say (the new program's _sync_sketch_camera)."""
        import FreeCAD as App
        lay = self.layer
        key = (lay.center.x(), lay.center.y(), lay.scale, lay.height())
        if not force and key == self._pushed:
            return
        self._pushed = key
        cam = self._cam()
        c = self._placement().multVec(App.Vector(lay.center.x(), lay.center.y(), 0))
        x, y, z, w = cam.orientation.getValue().getValue()
        look = App.Rotation(x, y, z, w).multVec(App.Vector(0, 0, -1))
        h_mm = max(1e-6, lay.height() / lay.scale)
        if hasattr(cam, "heightAngle"):  # perspective
            d = (h_mm / 2) / math.tan(cam.heightAngle.getValue() / 2)
        else:
            cam.height.setValue(h_mm)
            d = max(cam.focalDistance.getValue(), 10.0)
        p = c - look * d
        cam.position.setValue(p.x, p.y, p.z)
        cam.focalDistance.setValue(d)

    def _watch_camera(self):
        """The layer turned with the camera in the same pass as the 3D view's redraw (as 3D Line watches it, a Coin
        sensor on the camera): from the 30 ms timer alone the part and the grid were drawn in different frames, one
        after the other (the user: the part lags the grid and the other way round)."""
        try:
            from pivy import coin
            cam = self._cam()
            if self._cam_sensor is not None and getattr(self, "_watched", None) is cam:
                return
            self._unwatch_camera()
            self._cam_sensor = coin.SoNodeSensor(lambda *_: self._camera_moved(), None)
            # (the default priority: once per change of the camera, before the view's redraw sensor, which runs last)
            self._cam_sensor.attach(cam)
            self._watched = cam
        except Exception:
            log.logger().exception("new sketch: camera watcher")

    def _unwatch_camera(self):
        if getattr(self, "_cam_sensor", None) is not None:
            try:
                self._cam_sensor.detach()
            except Exception:
                pass
        self._cam_sensor, self._watched = None, None

    def turning(self):
        """The view turns or is moved. The grid stays, turning with the part, as in the new program (the user,
        2026-11-11: drawn in the 3D view's own frame it no longer holds the turn back; hidden it came off the part)."""
        self._still.start()

    def _at_rest(self):
        if getattr(self, "_orbiter", None) is not None and self._orbiter.at is not None:
            return  # the buttons still held, the mouse resting: the turn goes on (its release brings the grid back)
        if QtWidgets.QApplication.mouseButtons() != QtCore.Qt.NoButton:
            self._still.start()  # a button still held (a turn or pan of the view's own): not over yet
            return
        self.layer.hide_grid = False
        self.layer.update()

    def _camera_moved(self):
        try:
            cam = self._cam()
            seen = (cam.orientation.getValue().getValue(), tuple(cam.position.getValue().getValue()))
            if seen != self._seen:
                if self._seen is not None and self._moves(self._seen, seen):
                    self.turning()  # turned or moved sideways (a zoom only: the grid stays)
                self._seen = seen
                self._zoomed_outside()
                self._tilt()  # its update() and the view's own are painted in the same pass of the window
        except RuntimeError:
            self._unwatch_camera()
        except Exception:
            log.logger().exception("new sketch: following the camera")

    def _zoomed_outside(self):
        """The view zoomed by FreeCAD itself (Zoom All, the 3D view's own zoom): the sketch takes its zoom and middle
        from the view, as on opening (_keep_view). Kept at its own, its sizes, points and letters were blown up with
        the part (the user, 2026-11-11)."""
        import math as _m
        lay = self.layer
        cam = self._cam()
        try:
            h_cam = (2.0 * cam.focalDistance.getValue() * _m.tan(cam.heightAngle.getValue() / 2.0)
                     if hasattr(cam, "heightAngle") else cam.height.getValue())
        except Exception:
            return
        if lay.height() <= 0 or lay.scale <= 0 or h_cam <= 1e-9:
            return
        h_lay = lay.height() / lay.scale
        if abs(h_cam - h_lay) > 1e-3 * h_lay:
            self._keep_view()
            self._pushed = None

    @staticmethod
    def _moves(was, now):
        """Camera (orientation, position) was -> now: turned, or moved sideways (a pan); a zoom (along the view only)
        is no move."""
        if was[0] != now[0]:
            return True
        from pivy import coin
        d = [a - b for a, b in zip(now[1], was[1])]
        if not any(abs(x) > 1e-12 for x in d):
            return False
        look = coin.SbRotation(*now[0]).multVec(coin.SbVec3f(0, 0, -1)).getValue()
        along = sum(a * b for a, b in zip(d, look))
        side = sum((a - along * b) ** 2 for a, b in zip(d, look)) ** 0.5
        return side > 1e-9 * max(1.0, sum(x * x for x in d) ** 0.5) and side > 1e-6

    def _follow(self):
        """Every 30 ms: the sketch's own zoom or pan moves the camera; a camera turned (the cube) turns the sketch."""
        try:
            lay = self.layer
            if lay.geometry() != self.vp.geometry():
                lay.setGeometry(self.vp.geometry())
                self._mask()
            self._push()
            self._zoomed_outside()  # an orthographic zoom moves no camera position: looked at on every tick
            self._watch_camera()  # a new camera node (a perspective / orthographic switch): watched again
            cam = self._cam()
            seen = (cam.orientation.getValue().getValue(), tuple(cam.position.getValue().getValue()))
            if seen != self._seen:
                if self._seen is not None and self._moves(self._seen, seen):
                    self.turning()
                self._seen = seen
                self._tilt()
        except RuntimeError:  # the view closed under the sketch
            self.timer.stop()

    def _tilt(self, update=True):
        """The layer's straight-on drawing mapped onto the 3D view: four corners of a square of the sketch plane, as
        drawn straight on and as the camera sees them (exact with perspective). update=False: from the 3D view's own
        frame (_FrameItem), drawn there at once."""
        import FreeCAD as App
        lay, view = self.layer, self.view
        pl = self._placement()
        dpr = lay.devicePixelRatioF()

        def seen(x, y):
            px, py = view.getPointOnViewport(pl.multVec(App.Vector(x, y, 0)))
            return QtCore.QPointF(px / dpr, lay.height() - py / dpr)
        c = lay.center
        d = 50.0 / lay.scale
        corners = [(c.x() - d, c.y() - d), (c.x() + d, c.y() - d), (c.x() + d, c.y() + d), (c.x() - d, c.y() + d)]
        flat = QtGui.QPolygonF([lay.to_screen(QtCore.QPointF(x, y)) for x, y in corners])
        there = QtGui.QPolygonF([seen(x, y) for x, y in corners])
        t = QtGui.QTransform()
        if not QtGui.QTransform.quadToQuad(flat, there, t):
            return
        if all(abs(a - b) < 1e-6 for a, b in zip((t.m11(), t.m12(), t.m13(), t.m21(), t.m22(), t.m23(), t.m31(), t.m32(), t.m33()),
                                                  (1, 0, 0, 0, 1, 0, 0, 0, 1))):
            lay.tilt = lay.tilt_back = None  # straight on: drawn exactly as always
        else:
            back, ok = t.inverted()
            lay.tilt, lay.tilt_back = t, (back if ok and abs(t.determinant()) > 1e-9 else None)
        if update:
            lay.update()

    def _pivot(self, pos):
        """The spot of the sketch's plane under the pointer (a world point), as the new program turns about the plane
        under it; seen edge on: None (the middle of the view)."""
        import FreeCAD as App
        lay = self.layer
        if lay.tilt is not None and lay.tilt_back is None:
            return None
        s = lay.tilt_back.map(pos) if lay.tilt_back is not None else pos
        m = lay.to_model(s)
        p = self._placement().multVec(App.Vector(m.x(), m.y(), 0))
        return (p.x, p.y, p.z)

    def _orbit(self, pivot, dx, dy):
        """The camera turned by a drag of dx, dy px round pivot (the old program's own turn: 0.008 rad a pixel, the
        new program's too); the layer follows on its timer (_follow / _tilt)."""
        from incad.core import camera
        cam = self._cam()
        camera.orbit(cam, pivot if pivot is not None else camera.focal_point(cam), dx, dy)

    def _mask(self):
        """The cube's corner left to the 3D view (its arrows turn the view round the sketch)."""
        lay = self.layer
        r = QtGui.QRegion(lay.rect()).subtracted(QtGui.QRegion(QtCore.QRect(lay.width() - CUBE, 0, CUBE, CUBE)))
        lay.setMask(r)

    def _keep_view(self):
        """The sketch's middle and zoom taken from the view as it stands (FreeCAD's Sketcher turns the camera round
        the point it looks at and keeps its zoom): the point looked at, brought onto the sketch's plane square to it,
        and the height of the view in mm. A fit here moved the view onto the sketch (an empty one: onto its origin)."""
        import FreeCAD as App
        from incad.core import camera
        lay = self.layer
        cam = self._cam()
        f = camera.focal_point(cam).getValue()
        q = self._placement().inverse().multVec(App.Vector(f[0], f[1], f[2]))
        if hasattr(cam, "heightAngle"):  # perspective: the height seen at the point looked at
            h_mm = 2.0 * cam.focalDistance.getValue() * math.tan(cam.heightAngle.getValue() / 2.0)
        else:
            h_mm = cam.height.getValue()
        if h_mm <= 1e-9 or lay.height() <= 0:
            return self._fit()
        lay.center = QtCore.QPointF(q.x, q.y)
        lay.scale = lay.height() / h_mm
        lay.dim_ref_scale = lay.scale
        lay._dim_grow()
        lay._refresh()

    def _fit(self):
        try:
            if self.layer.pts:
                self.layer.fit()
        except Exception:
            log.logger().exception("fit the new sketch")

    def _prompt(self, text):
        self.last_prompt = text  # the panel's refresh says it again (it would say the plain sketch words)
        try:
            from incad.ui import shell
            inst = shell.instance()
            if inst is not None:
                inst.status.set_prompt(text)
        except Exception:
            pass

    # ----- the Sketch tab's commands -----
    def command(self, cmd):
        v = self.layer
        if cmd in RING_TOOLS and cmd != "sketch.finish":
            self.last_tool = cmd  # the ring offers it again
        if cmd == "sketch.finish":
            return self.finish()
        if cmd == "sketch.cancel":
            return self.cancel()
        if cmd == "view.look_at":  # the new program's Look At in a sketch: straight at it again
            return self._turn_to(self._facing())
        if cmd == "edit.undo":
            if self.fresh and self._unchanged() and not v.tool:  # a new sketch back as it was made: Undo takes the
                return self.cancel()  # sketch away too, as the old program's Undo takes back its "New Sketch"
            return v.undo()
        if cmd == "edit.redo":
            return v.redo()
        if cmd == "edit.delete":
            return v.delete_chosen() if v.can_delete() else None
        if cmd == "sketch.project":
            return self.project_start()
        if cmd in RECT_COMMANDS or cmd in ("sketch.line", "sketch.line_single", "sketch.spline", "sketch.spline_cv"):
            self.last_cmd = cmd  # a drawing tool: started again after the sketch is read back (auto-projection)
        self.project_stop(say=False)  # another command ends Project Geometry
        v.end_copying()
        self._poly_draw_end()  # another command: a window drawing with the last tool goes (the new program's rule)
        v.setFocus()
        if cmd in ("sketch.fillet", "sketch.chamfer"):
            v.start_corner("sk_" + cmd[7:])
            return self._corner_window()
        if cmd in ("sketch.rect_pattern", "sketch.circ_pattern", "sketch.path_pattern"):
            return self._pattern_window(cmd[7:11], None)
        if cmd == "sketch.constraints_window":
            return self._constraint_list()
        if cmd == "sketch.text" and v.text_on_selection():
            return None  # along the curve that was chosen
        if cmd == "sketch.polygon":
            v.start_rect("polygon")
            return self._poly_draw_start()
        if cmd == "sketch.line":
            return v.start_line()
        if cmd == "sketch.line_single":
            return v.start_line(single=True)
        if cmd in ("sketch.spline", "sketch.spline_cv"):
            return v.start_spline("spline" if cmd == "sketch.spline" else "spline_cv")
        if cmd in RECT_COMMANDS:
            return v.start_rect(RECT_COMMANDS[cmd])
        if cmd in CONSTRAINT_COMMANDS:
            return v.start_constraint(CONSTRAINT_COMMANDS[cmd])
        if cmd == "sketch.dimension":
            return v.start_dimension()
        if cmd in ("sketch.trim", "sketch.extend"):
            return v.start_trim(cmd[7:])
        if cmd == "sketch.offset":
            return v.start_offset()
        if cmd == "sketch.mirror":
            return v.start_mirror()
        if cmd == "sketch.rotate":
            return v.start_rotate()
        if cmd == "sketch.construction":
            if v.has_chosen():
                n = v.flip_construction()
                v.construction = False
                return self._prompt("Construction: %d turned over" % n)
            v.construction = not v.construction
            return self._prompt("Construction " + ("on: new lines are construction lines" if v.construction
                                                    else "off: new lines are normal lines"))
        if cmd == "sketch.dim_construction":
            return v.set_dim(not v.dim_constr)
        if cmd == "sketch.constraints_toggle":
            v.marks_hidden = not v.marks_hidden
            return v.update()
        if cmd == "sketch.show_dof":
            v.stop_tool()
            if not v.choose_free():
                self._prompt("Nothing is free: the sketch is fully constrained")
            return None
        self._prompt("%s: not in this trial yet" % cmd)
        return None

    # ----- the end -----
    VIEW_KEYS = ("scale", "centre")  # where the sketch is looked at, not what is drawn

    def _drawn(self):
        d = {k: v for k, v in self.layer.dump().items() if k not in self.VIEW_KEYS}
        return json.dumps(d, sort_keys=True, default=str)

    def _unchanged(self):
        return self._drawn() == self._data_open

    @staticmethod
    def _hide_later(sk):
        """As the old sketch (Sketcher's own opening, then InCad's two steps after it): everything built on the sketch
        hidden, the finished part with it; its support and the parts its outside lines come from shown; what was
        hidden though it does not use the sketch shown again; and a sketch in the middle of a part shows the part as
        it is before the sketch. All through one TempoVis: put back as it was when the sketch closes."""
        try:
            from Show import TempoVis
            from incad.core import document
            from incad.ui import shell as shells
            inst = shells.instance()
            mgr = getattr(inst, "sketches", None)
            body = document.body_of(sk)
            shown = bool(body is not None and body.Tip is not None and body.Tip.Visibility)
            visible = [o for o in sk.Document.Objects
                       if o is not sk and o.isDerivedFrom("Part::Feature") and o.Visibility]
            vo = sk.ViewObject
            tv = TempoVis(sk.Document, tag=vo.TypeId)
            if getattr(vo, "HideDependent", True):
                tv.hide_all_dependent(sk)
            if getattr(vo, "ShowSupport", True):
                tv.show([ref[0] for ref in (sk.AttachmentSupport or [])
                         if not ref[0].isDerivedFrom("PartDesign::Plane")])
            if getattr(vo, "ShowLinks", True):
                tv.show([ref[0] for ref in (sk.ExternalGeometry or [])])
            try:
                vo.TempoVis = tv  # the old sketch's two steps find it there
            except Exception:
                pass
            if mgr is not None:
                mgr._show_unrelated(sk, visible)
                if shown:
                    mgr._show_part_before(sk, body)
            return tv
        except Exception:
            log.logger().exception("new sketch: hide what is built on it")
            return None

    def _show_later(self):
        """What _hide_later hid or showed, put back as it was."""
        tv, self.tv = self.tv, None
        if tv is None:
            return
        try:
            tv.restore()
        except Exception:
            log.logger().exception("new sketch: show what is built on it again")
        try:
            self.sk.ViewObject.TempoVis = None
        except Exception:
            pass

    def cancel(self):
        """Cancel Sketch: nothing kept. The layer goes as on Finish without writing; what the document took while the
        sketch was open is taken back; a sketch made for this opening goes too (its "New Sketch" undone: Redo brings
        it back)."""
        doc = self.sk.Document
        fresh = self.fresh
        from incad.core import settle
        r = self.finish(write=False, compute=False)
        try:
            for _ in range(max(0, len(doc.UndoNames) - self._undo_at_open)):
                settle.take_back(doc, doc.undo, self._quiet.failed)
            if fresh and doc.UndoNames[:1] == ["New Sketch"]:
                import FreeCADGui as Gui
                Gui.Selection.clearSelection()
                settle.take_back(doc, doc.undo, self._quiet.failed)
            settle.recompute(doc, self._quiet.failed)
        except Exception:
            log.logger().exception("new sketch: Cancel")
        if self.on_change is not None:
            self.on_change()
        return r

    def finish(self, write=True, compute=True):
        """The sketch written back (one undo step; nothing when nothing changed), the layer gone, the camera turned
        back as it was. write=False: Cancel."""
        global _CURRENT
        import FreeCAD as App
        from incad.core import safe, settle
        from incad.sketch_next import convert
        lay = self.layer
        self._poly_draw_end()
        self._close_feature()
        if self.con_window is not None:
            self.con_window.close_it()
        if lay.tool:
            lay.stop_tool()
        data = lay.dump()
        doc = self.sk.Document
        unchanged = not write or self._unchanged()
        if unchanged:  # Cancel, or nothing changed: no step of its own in the history
            r = safe.SafeResult(True)
        else:
            r = safe.run_safely("Edit Sketch", lambda: convert.write(self.sk, data), doc)
        if not r.ok:
            log.report("error", "The sketch could not be written: %s" % r.error)
        self.timer.stop()
        self._unwatch_camera()
        if getattr(self, "_frame", None) is not None:
            try:
                lay.frame_item = None
                self._frame_scene.removeItem(self._frame)
                self._frame_scene.update()
            except Exception:
                log.logger().exception("new sketch: off the 3D view")
            self._frame = None
        lay.hide()
        _CURRENT = None
        try:
            self.sk.ViewObject.Visibility = self.was_shown
        except Exception:
            pass
        self._show_later()
        from incad.features import sketch_text as T
        for t in T.texts_of(self.sk):  # shown again; a Text made in the sketch shows like the sketch's other ones
            try:
                t.ViewObject.Visibility = self.texts_shown.get(t.Name, True)
            except Exception:
                pass
        self._quiet.untouch()
        if compute:  # (Cancel does it, when it has taken the steps back)
            settle.recompute(doc, self._quiet.failed if unchanged else ())

        def back():
            try:
                self.view.setCamera(self.camera_before)  # exactly as it was (the turn below goes most of the way)
            except Exception:
                log.logger().exception("camera back after the sketch")
            try:
                import shiboken6
                if lay in _LAYERS:
                    _LAYERS.remove(lay)
                if shiboken6.isValid(lay):
                    shiboken6.delete(lay)  # at once, with its own windows: nothing of it left for the exit
            except Exception:
                lay.deleteLater()
        try:
            import FreeCAD as App
            q = [float(v) for v in self.camera_before.split("orientation")[1].split()[:4]]
            self._turn_to(App.Rotation(App.Vector(q[0], q[1], q[2]), math.degrees(q[3])), then=back)
        except Exception:
            back()
        if self.on_change is not None:
            self.on_change()
        return r
