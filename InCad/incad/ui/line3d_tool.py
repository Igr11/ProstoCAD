"""3D Line's cube in the view (the user's idea): the cube's points and dotted construction lines round the origin,
the step and the size written grey along its edges (as the gizmos' labels), and under the pointer what a click
takes — a cube point, or a free place on a construction line with the grey distances to the cube points either
side; a rubber line runs from the last point. A click adds the point to the dialog's Points (a click elsewhere is
FreeCAD's). A point of the line already made is dragged along the cube the same way (the user: to edit the line).
The navigator's handle hook, as the dialogs' drag arrows."""
import FreeCAD as App

from incad.core import line3d, log
from incad.qt import QtCore, QtWidgets

NAME = "InCadLine3DCube"


def _rgb(hex_color):
    from incad.ui.move_face_arrow import _rgb as rgb
    return rgb(hex_color)


class LatticeOverlay:
    # the view turns all the time while a 3D line is drawn (LMB+RMB): a left press waits nav.HOLD_LEFT_MS for the right
    # one before it adds a point (the user: the left button took the turn's press and put a point where none was meant)
    hold_for_turn = True

    def __init__(self, shell, session):
        self.shell, self.session = shell, session
        self.root = self._hover_sep = self._view = None
        self._hover = None  # the snap under the pointer ({"kind": "grab"}: one of the line's points)
        self._drag = None   # [index of the line's point, view, viewport, moved] while one is dragged
        self._cursor = None  # the viewport we set a hand cursor on
        self._built = None  # (size, step, points text, theme) the cube was drawn for
        self._depth_sep, self._depth_dir = None, None  # the cube's lines and points, by depth; the view they suit
        self._sensor, self._camera, self._turn_pending = None, None, False
        self._keys = _LayerKeys(self)  # Tab / Shift+Tab: the next / previous layer (the user)
        QtWidgets.QApplication.instance().installEventFilter(self._keys)
        self.refresh()

    # ----- what the dialog says -----
    def _values(self):
        d = self.session.dialog
        return d.values() if d is not None else {}

    def _cube(self):
        from incad.features.line3d import length
        v = self._values()
        try:
            size, step = length(v.get("size"), "Cube"), length(v.get("step"), "Step")
            line3d.coords(size, step)
            return size, step
        except ValueError:
            return None

    def _to_global(self):
        body = self.session.body
        try:
            return body.getGlobalPlacement() if body is not None else App.Placement()
        except Exception:
            return App.Placement()

    def _active_view(self):
        import FreeCADGui as Gui
        gdoc = Gui.getDocument(self.session.doc_name)
        return gdoc.ActiveView if gdoc is not None else None

    # ----- drawing -----
    def refresh(self):
        cube, view = self._cube(), self._active_view()
        v = self._values()
        f = self.session.feature
        try:
            shape = (round(f.Shape.Length, 6), str(f.Shape.BoundBox)) if f is not None and not f.Shape.isNull() else None
        except Exception:
            shape = None
        self._follow_layer(v)
        v = self._values()
        key = (cube, v.get("points"), v.get("shape"), bool(v.get("curvature")), shape, self.shell.theme,
               v.get("layer"), v.get("at"))
        if self.root is not None and key == self._built and view is self._view:
            return
        self._remove_node()
        if cube is None or view is None:
            return
        self._built = key
        try:
            self.root = self._draw(cube, view)
            view.getSceneGraph().addChild(self.root)
            self._view = view
            self._watch_camera(view)
        except Exception:
            log.logger().exception("3D Line cube")
            self.root = None

    def _draw(self, cube, view):
        from pivy import coin
        from incad.ui import theme as themes
        size, step = cube
        palette = themes.PALETTES[self.shell.theme]
        g = self._to_global()
        try:
            dpr = viewport_of(view).devicePixelRatioF()
        except Exception:
            dpr = 1.0
        root = coin.SoSeparator()
        root.setName(NAME)
        pick = coin.SoPickStyle()
        pick.style = coin.SoPickStyle.UNPICKABLE
        root.addChild(pick)
        xf = coin.SoTransform()
        xf.translation.setValue(*g.Base)
        q = g.Rotation.Q
        xf.rotation.setValue(q[0], q[1], q[2], q[3])
        root.addChild(xf)
        self._dpr = dpr
        self._depth_sep = coin.SoSeparator()  # the construction lines and points, nearer ones thicker
        root.addChild(self._depth_sep)
        self._fill_depth(cube, view)
        # the line's curvature comb (the user: for editing, switched off when not wanted)
        f = self.session.feature
        if self._values().get("curvature") and f is not None and not f.Shape.isNull():
            spikes = line3d.comb(f.Shape.Edges)
            if spikes:
                root.addChild(self._comb_node(spikes, palette))
        # the step and the size, grey, along the three edges from the near corner
        cs = line3d.coords(size, step)
        lo, hi = float(cs[0]), float(cs[-1])
        for a in range(3):
            first = [lo, lo, lo]
            first[a] = lo + step / 2.0
            root.addChild(self._label(first, "%g mm" % step, palette["text_dim"], dpr))
            mid = [lo, lo, lo]
            mid[a] = (lo + hi) / 2.0
            root.addChild(self._label(mid, "%g mm" % (hi - lo), palette["text_dim"], dpr))
        # the line's points
        mine = line3d.parse_points(self._values().get("points"))
        for size, color in ((8, (0.08, 0.08, 0.1)), (5, _rgb(palette["accent"]))):  # small: a dark rim, a bright core
            if not mine:
                break
            ms = coin.SoSeparator()
            c = coin.SoBaseColor()
            c.rgb.setValue(*color)
            st = coin.SoDrawStyle()
            st.pointSize = size * dpr
            coords = coin.SoCoordinate3()
            coords.point.setValues(0, len(mine), mine)
            for n in (c, st, coords, coin.SoPointSet()):
                ms.addChild(n)
            root.addChild(ms)
        self._hover_sep = coin.SoAnnotation()
        root.addChild(self._hover_sep)
        self._dpr = dpr
        return root

    def _layer(self):
        from incad.features.line3d import working_layer
        return working_layer(self._values())

    def _follow_layer(self, values):
        """A new layer direction goes through the last point (XY at its height, XZ at its depth…)."""
        name = values.get("layer")
        was, self._layer_name = getattr(self, "_layer_name", name), name
        if name == was or name not in line3d.LAYERS:
            return
        pts = line3d.parse_points(values.get("points"))
        dialog = self.session.dialog
        if pts and dialog is not None:
            dialog.set_values({"at": "%g mm" % round(pts[-1][line3d.LAYERS[name]], 6)})

    def _fill_depth(self, cube, view):
        """The cube's construction lines and points in three depths: near ones thicker, brighter and solid, far
        ones thin, dim and dotted (the user: so the cube reads in space, with perspective)."""
        from pivy import coin
        from incad.ui import theme as themes
        sep = self._depth_sep
        if sep is None:
            return
        sep.removeAllChildren()
        size, step = cube
        palette = themes.PALETTES[self.shell.theme]
        d = self._to_global().Rotation.inverted().multVec(App.Vector(*view.getViewDirection()))
        self._depth_dir = d
        layer = self._layer()
        if layer is not None:
            return self._fill_layer(sep, cube, layer, palette)  # it does not change with the view: drawn once
        cs = line3d.coords(size, step)
        half = float(cs[-1])
        segs = line3d.unit_segments(size, step) if len(cs) <= 25 else line3d.segments(size, step)
        seg_bins = line3d.depth_bins(segs.mean(axis=1), tuple(d), half)
        pts = line3d.lattice(size, step)
        pt_bins = line3d.depth_bins(pts, tuple(d), half)
        bright, dim = _rgb(palette["text"]), _rgb(palette["text_dim"])
        looks = (  # (line width, pattern, colour, point size)
            (2.0, 0xFFFF, tuple(0.5 * a + 0.5 * b for a, b in zip(bright, dim)), 4.0),
            (1.2, 0x7777, dim, 3.0),
            (1.0, 0x3333, tuple(0.65 * a for a in dim), 2.0))
        for b, (width, pattern, color, psize) in enumerate(looks):
            mine = segs[seg_bins == b]
            if len(mine):
                ls_sep = coin.SoSeparator()
                c = coin.SoBaseColor()
                c.rgb.setValue(*color)
                st = coin.SoDrawStyle()
                st.lineWidth = width
                st.linePattern = pattern
                coords = coin.SoCoordinate3()
                flat = mine.reshape(-1, 3)
                coords.point.setValues(0, len(flat), [tuple(float(v) for v in p) for p in flat])
                ls = coin.SoLineSet()
                ls.numVertices.setValues(0, len(mine), [2] * len(mine))
                for n in (c, st, coords, ls):
                    ls_sep.addChild(n)
                sep.addChild(ls_sep)
            mp = pts[pt_bins == b]
            if len(mp):
                ps = coin.SoSeparator()
                c = coin.SoBaseColor()
                c.rgb.setValue(*color)
                st = coin.SoDrawStyle()
                st.pointSize = psize * self._dpr
                coords = coin.SoCoordinate3()
                coords.point.setValues(0, len(mp), [tuple(float(v) for v in p) for p in mp])
                for n in (c, st, coords, coin.SoPointSet()):
                    ps.addChild(n)
                sep.addChild(ps)

    def _fill_layer(self, sep, cube, layer, palette):
        """The working layer bright — its lines, its points, its frame and name — the rest of the cube a faint dotted
        cage around it (the user: in the whole cube it was hard to see where a point goes)."""
        from pivy import coin
        size, step = cube
        axis, level = layer
        bright, dim = _rgb(palette["text"]), _rgb(palette["text_dim"])

        def lines(segs, color, width, pattern):
            n = coin.SoSeparator()
            c = coin.SoBaseColor()
            c.rgb.setValue(*color)
            st = coin.SoDrawStyle()
            st.lineWidth = width
            st.linePattern = pattern
            coords = coin.SoCoordinate3()
            flat = segs.reshape(-1, 3)
            coords.point.setValues(0, len(flat), [tuple(float(v) for v in p) for p in flat])
            ls = coin.SoLineSet()
            ls.numVertices.setValues(0, len(segs), [2] * len(segs))
            for node in (c, st, coords, ls):
                n.addChild(node)
            sep.addChild(n)

        cage = line3d.segments(size, step)
        lines(cage, tuple(0.45 * a for a in dim), 1.0, 0x1111)
        lines(line3d.layer_segments(size, step, axis, level), tuple(0.55 * a + 0.45 * b for a, b in zip(bright, dim)),
              1.4, 0xFFFF)
        cs = line3d.coords(size, step)
        lo, hi = float(cs[0]), float(cs[-1])
        b, d = [k for k in range(3) if k != axis]
        corners = []
        for u, v in ((lo, lo), (hi, lo), (hi, hi), (lo, hi), (lo, lo)):
            p = [0.0, 0.0, 0.0]
            p[b], p[d], p[axis] = u, v, level
            corners.append(p)
        frame = coin.SoSeparator()
        c = coin.SoBaseColor()
        c.rgb.setValue(*_rgb(palette["accent"]))
        st = coin.SoDrawStyle()
        st.lineWidth = 2.2
        coords = coin.SoCoordinate3()
        coords.point.setValues(0, 5, corners)
        for node in (c, st, coords, coin.SoLineSet()):
            frame.addChild(node)
        sep.addChild(frame)
        pts = line3d.layer_points(size, step, axis, level)
        ps = coin.SoSeparator()
        c = coin.SoBaseColor()
        c.rgb.setValue(*bright)
        st = coin.SoDrawStyle()
        st.pointSize = 4 * self._dpr
        coords = coin.SoCoordinate3()
        coords.point.setValues(0, len(pts), [tuple(float(v) for v in p) for p in pts])
        for node in (c, st, coords, coin.SoPointSet()):
            ps.addChild(node)
        sep.addChild(ps)
        name = {2: "XY", 1: "XZ", 0: "YZ"}[axis]
        sep.addChild(self._label(corners[2], "%s · %g mm" % (name, round(level, 3)), palette["accent"], self._dpr))

    def _comb_node(self, spikes, palette):
        from pivy import coin
        sep = coin.SoSeparator()
        c = coin.SoBaseColor()
        c.rgb.setValue(0.80, 0.45, 0.95)  # violet: apart from the line (orange) and the cube (grey)
        st = coin.SoDrawStyle()
        st.lineWidth = 1
        coords = coin.SoCoordinate3()
        pts = [tuple(p) for p, _ in spikes] + [tuple(t) for _, t in spikes]
        coords.point.setValues(0, len(pts), pts)
        n = len(spikes)
        ls = coin.SoIndexedLineSet()
        idx = []
        for i in range(n):
            idx += [i, n + i, -1]  # the spikes
        idx += list(range(n, 2 * n)) + [-1]  # the line through their tips
        ls.coordIndex.setValues(0, len(idx), idx)
        for node in (c, st, coords, ls):
            sep.addChild(node)
        return sep

    def _watch_camera(self, view):
        """A turned view redraws the cube's depths (once the event loop is free)."""
        try:
            from pivy import coin
            cam = view.getCameraNode()
            if self._sensor is not None and self._camera is cam:
                return
            self._unwatch_camera()
            self._sensor = coin.SoNodeSensor(lambda *_: self._camera_moved(), None)
            self._sensor.attach(cam)
            self._camera = cam
        except Exception:
            log.logger().exception("3D Line camera watcher")

    def _unwatch_camera(self):
        if self._sensor is not None:
            try:
                self._sensor.detach()
            except Exception:
                pass
        self._sensor, self._camera = None, None

    def _camera_moved(self):
        if self._turn_pending:
            return
        self._turn_pending = True
        from incad.qt import QtCore
        QtCore.QTimer.singleShot(30, self._camera_turned)

    def _camera_turned(self):
        self._turn_pending = False
        view, cube = self._view, self._cube()
        if view is None or cube is None or self._depth_sep is None:
            return
        try:
            d = self._to_global().Rotation.inverted().multVec(App.Vector(*view.getViewDirection()))
        except Exception:
            return
        if self._depth_dir is None or d.getAngle(self._depth_dir) > 0.03:
            self._fill_depth(cube, view)

    def _label(self, at, text, color, dpr):
        from pivy import coin
        sep = coin.SoSeparator()
        c = coin.SoBaseColor()
        c.rgb.setValue(*_rgb(color))
        t = coin.SoTranslation()
        t.translation.setValue(*at)
        font = coin.SoFont()
        font.size = 12 * dpr
        label = coin.SoText2()
        label.string = text
        for n in (c, t, font, label):
            sep.addChild(n)
        return sep

    def _draw_hover(self):
        from pivy import coin
        from incad.ui import theme as themes
        if self._hover_sep is None:
            return
        self._hover_sep.removeAllChildren()
        h = self._hover
        if h is None:
            return
        palette = themes.PALETTES[self.shell.theme]
        p = h["point"]
        c = coin.SoBaseColor()
        c.rgb.setValue(*_rgb(palette["accent"]))
        st = coin.SoDrawStyle()
        st.pointSize = 11 * self._dpr
        coords = coin.SoCoordinate3()
        coords.point.setValues(0, 1, [p])
        for n in (c, st, coords, coin.SoPointSet()):
            self._hover_sep.addChild(n)
        if h["kind"] == "grab" or self._drag is not None:  # a point of the line to drag: a ring round it
            c = coin.SoBaseColor()
            c.rgb.setValue(*_rgb(palette["text"]))
            st = coin.SoDrawStyle()
            st.pointSize = 16 * self._dpr
            coords = coin.SoCoordinate3()
            coords.point.setValues(0, 1, [p])
            ring = coin.SoSeparator()
            for n in (c, st, coords, coin.SoPointSet()):
                ring.addChild(n)
            self._hover_sep.insertChild(ring, 0)  # under the accent dot: a light rim
        mine = line3d.parse_points(self._values().get("points"))
        if mine and h["kind"] != "grab" and self._drag is None:  # the rubber line from the last point
            sep = coin.SoSeparator()
            c = coin.SoBaseColor()
            c.rgb.setValue(*_rgb(palette["accent"]))
            st = coin.SoDrawStyle()
            st.lineWidth = 1.5
            st.linePattern = 0x0F0F
            coords = coin.SoCoordinate3()
            coords.point.setValues(0, 2, [mine[-1], p])
            for n in (c, st, coords, coin.SoLineSet()):
                sep.addChild(n)
            self._hover_sep.addChild(sep)
        if h["kind"] == "line":  # free on a construction line: the distances to the cube points either side
            a = h["axis"]
            for end in h["ends"]:
                gap = abs(p[a] - end)
                if gap < 1e-6:
                    continue
                at = list(p)
                at[a] = (p[a] + end) / 2.0
                self._hover_sep.addChild(self._label(at, "%g mm" % round(gap, 3), palette["text_dim"], self._dpr))

    def _remove_node(self):
        if self.root is not None and self._view is not None:
            try:
                self._view.getSceneGraph().removeChild(self.root)
            except Exception:
                pass
        self.root = self._hover_sep = self._view = self._depth_sep = None
        self._built = None

    # ----- the pointer -----
    def _ray(self, view, viewport, x, y):
        """The pointer's ray (origin, unit direction) in the Solid's coordinates, and mm per screen px there."""
        from pivy import coin  # noqa: F401  (getCameraNode needs it loaded)
        from incad.ui import nav
        from incad.ui import dof_handles as dh
        cx, cy = nav.to_coin(viewport, x, y)
        p = App.Vector(view.getPoint(cx, cy))
        cam = view.getCameraNode()
        if cam.getTypeId().getName() == "PerspectiveCamera":
            o = App.Vector(*cam.position.getValue().getValue())
            d = p - o
        else:
            d = App.Vector(*view.getViewDirection())
            o = p - d * 1e6
        d.normalize()
        a, b = dh.screen(view, viewport, p), dh.screen(view, viewport, p + _square(d))
        per_px = 1.0 / max(1e-9, ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5)
        inv = self._to_global().inverse()
        return inv.multVec(o), inv.Rotation.multVec(d), per_px

    def _snap(self, view, viewport, x, y):
        from incad.ui import dof_handles as dh
        cube = self._cube()
        if cube is None or self.session.dialog is None or getattr(self.session, "_closed", False):
            return None
        try:
            if viewport_of(view) != viewport:
                return None
            o, d, per_px = self._ray(view, viewport, x, y)
            return line3d.snap(tuple(o), tuple(d), cube[0], cube[1], (dh.HANDLE_PX - 2) * per_px, self._layer())
        except Exception:
            log.logger().exception("3D Line snap")
            return None

    def _grabbed(self, view, viewport, x, y):
        """The index of the line's point under the pointer (the nearest within reach), else None."""
        from incad.ui import dof_handles as dh
        try:
            if self.session.dialog is None or viewport_of(view) != viewport:
                return None
            g = self._to_global()
            best = None
            for i, p in enumerate(line3d.parse_points(self._values().get("points"))):
                sx, sy = dh.screen(view, viewport, g.multVec(App.Vector(*p)))
                d = ((sx - x) ** 2 + (sy - y) ** 2) ** 0.5
                if d <= dh.HANDLE_PX + 2 and (best is None or d < best[1]):
                    best = (i, d)
            return best[0] if best is not None else None
        except Exception:
            log.logger().exception("3D Line grab")
            return None

    def hover(self, view, viewport, x, y):
        if self._drag is not None:
            return False
        self.refresh()
        i = self._grabbed(view, viewport, x, y)
        if i is not None:
            h = {"kind": "grab", "point": line3d.parse_points(self._values().get("points"))[i]}
        else:
            h = self._snap(view, viewport, x, y)
        self._set_cursor(viewport if i is not None else None)
        if h != self._hover:
            self._hover = h
            self._draw_hover()
        return False

    def press(self, view, viewport, x, y):
        dialog = self.session.dialog
        if dialog is None or "points" not in dialog._widgets:
            return False
        i = self._grabbed(view, viewport, x, y)
        if i is not None:  # one of the line's points: dragged (a click without a move changes nothing)
            self._drag = [i, view, viewport, False]
            self._set_cursor(viewport, closed=True)
            return True
        h = self._snap(view, viewport, x, y)
        if h is None:
            return False  # not on the cube: FreeCAD's click
        dialog.field("points").add("%.9g %.9g %.9g" % h["point"])
        self._hover = None
        self.refresh()
        return True

    def move(self, x, y):
        drag, dialog = self._drag, self.session.dialog
        if drag is None or dialog is None:
            return
        i, view, viewport, _ = drag
        h = self._snap(view, viewport, x, y)
        if h is None:
            return  # off the cube: the point stays where it got to
        pts = line3d.parse_points(self._values().get("points"))
        if i >= len(pts) or tuple(pts[i]) == tuple(h["point"]):
            return
        pts[i] = h["point"]
        dialog.field("points").set_text(line3d.format_points(pts))
        drag[3] = True
        self._hover = h
        self._draw_hover()
        try:
            self.session._changed(dialog.values(), throttle=True)  # the line follows while it moves
        except Exception:
            log.logger().exception("3D Line drag")

    def release(self):
        drag, self._drag = self._drag, None
        self._set_cursor(None)
        if drag is not None and drag[3] and self.session.dialog is not None:
            self.session.dialog._emit()  # the dragged place: one step of the dialog's Undo, previewed
        self._hover = None
        self.refresh()
        return False  # the click added or moved a point: never FreeCAD's too

    def dragging(self):
        return self._drag is not None

    def finish(self, moved_ok=True, closing=False):
        if self._drag is not None:
            self.release()

    def _set_cursor(self, viewport, closed=False):
        """An open hand over one of the line's points, closed while it is dragged; a Sketcher tool's cursor stays."""
        from incad.qt import QtCore, QtGui
        ours = (QtCore.Qt.OpenHandCursor, QtCore.Qt.ClosedHandCursor)
        try:
            if self._cursor is not None and self._cursor is not viewport:
                if self._cursor.cursor().shape() in ours:
                    self._cursor.unsetCursor()
                self._cursor = None
            if viewport is not None and viewport.cursor().shape() != QtCore.Qt.BitmapCursor:
                viewport.setCursor(QtGui.QCursor(ours[1] if closed else ours[0]))
                self._cursor = viewport
        except RuntimeError:
            self._cursor = None

    def pointer_over_view(self):
        from incad.qt import QtGui
        view = self._view or self._active_view()
        try:
            vp = viewport_of(view)
            return vp.rect().contains(vp.mapFromGlobal(QtGui.QCursor.pos()))
        except Exception:
            return False

    def step_layer(self, delta):
        """The working layer one step of the cube up (+1) or down (-1)."""
        from incad.features.line3d import _step_of, working_layer
        dialog = self.session.dialog
        if dialog is None or getattr(self.session, "_closed", False):
            return False
        values = dialog.values()
        layer, step = working_layer(values), _step_of(values)
        if layer is None or not step:
            return False
        cube = self._cube()
        level = layer[1] + delta * step
        if cube is not None:
            half = float(line3d.coords(*cube)[-1])
            level = max(-half, min(half, level))  # never out of the cube
        dialog.set_values({"at": "%g mm" % round(level, 6)})
        dialog._emit()
        self.refresh()
        return True

    def remove(self):
        if self._keys is not None:
            QtWidgets.QApplication.instance().removeEventFilter(self._keys)
            self._keys = None
        self._set_cursor(None)
        self._unwatch_camera()
        self._remove_node()


class _LayerKeys(QtCore.QObject):
    """Tab / Shift+Tab while 3D Line is open: the working layer a step up / down — not while typing in a field."""

    def __init__(self, tool):
        super().__init__()
        self.tool = tool

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind not in (QtCore.QEvent.KeyPress, QtCore.QEvent.ShortcutOverride) \
                or event.key() not in (QtCore.Qt.Key_Tab, QtCore.Qt.Key_Backtab):
            return False
        typing = isinstance(QtWidgets.QApplication.focusWidget(), (QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit))
        if typing and not self.tool.pointer_over_view():
            return False  # in the dialog, typing: Tab goes on to the next field
        if kind == QtCore.QEvent.ShortcutOverride:
            event.accept()  # ours, not a shortcut's (InCad's Tab for typed sizes): the key press comes through
            return True
        up = event.key() == QtCore.Qt.Key_Tab and not event.modifiers() & QtCore.Qt.ShiftModifier
        try:
            return self.tool.step_layer(1 if up else -1)
        except Exception:
            log.logger().exception("3D Line layer keys")
            return False


def viewport_of(view):
    return view.graphicsView().viewport()


def _square(d):
    """A unit direction square to d (to measure mm per px across the view)."""
    u = d.cross(App.Vector(0, 0, 1))
    if u.Length < 1e-6:
        u = d.cross(App.Vector(1, 0, 0))
    u.normalize()
    return u
