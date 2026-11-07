"""Pattern dialogs: the copies are drawn as outlines while the dialog is open (the user: like the fillet and chamfer
preview — virtual, at once, even for many copies of a big part); the pattern is built once, on OK. One outline of
the shape repeated (its geometry shared, a transform per copy), so 100 copies cost no more than one."""
import FreeCAD as App

MAX_EDGES = 4000  # more (an STL's triangles): its box is drawn instead
REAL_PICKS = ("excluded", "from1", "to1")  # clicks on the copies themselves (or faces between): built while active


def _color():
    from incad.ui import theme
    return (0.35, 0.64, 1.0) if theme.current() == "dark" else (0.04, 0.39, 0.84)


def _outline(shape):
    """[[points]] of the shape's edges (its own placement applied)."""
    edges = shape.Edges
    if len(edges) > MAX_EDGES:
        b = shape.BoundBox
        c = [App.Vector(x, y, z) for x in (b.XMin, b.XMax) for y in (b.YMin, b.YMax) for z in (b.ZMin, b.ZMax)]
        pairs = ((0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7))
        return [[c[a], c[b]] for a, b in pairs]
    out = []
    for e in edges:
        try:
            pts = e.discretize(Deflection=0.05) if len(e.Vertexes) > 1 else e.discretize(24)
        except Exception:
            continue
        if len(pts) > 1:
            out.append(pts)
    return out


def _lines(lines):
    from pivy import coin
    sep = coin.SoSeparator()
    flat = [p for line in lines for p in line]
    coords = coin.SoCoordinate3()
    coords.point.setValues(0, len(flat), [(p.x, p.y, p.z) for p in flat])
    ls = coin.SoLineSet()
    ls.numVertices.setValues(0, len(lines), [len(line) for line in lines])
    sep.addChild(coords)
    sep.addChild(ls)
    return sep


def _transform(pl):
    from pivy import coin
    t = coin.SoTransform()
    t.translation.setValue(pl.Base.x, pl.Base.y, pl.Base.z)
    q = pl.Rotation.Q
    t.rotation.setValue(q[0], q[1], q[2], q[3])
    return t


def copies(f):
    """[(shape, [global placement per copy])] for pattern f: what to draw where, the copies only (not the
    original, already in the part)."""
    from incad.features import own_pattern
    places = own_pattern.transforms(f)
    numbers = own_pattern.kept(f)
    moved = [places[n - 1] for n in numbers if n > 1]
    if own_pattern.is_solid(f):
        result = own_pattern.result_of(f)
        holder = f.Source if result in ("new", "each") and f.Source is not None else f.getParentGeoFeatureGroup()
        shapes = [own_pattern.source_shape(f)]
    else:
        holder = f.getParentGeoFeatureGroup()
        shapes = [t for _o, t in own_pattern.tools(f)]
    frame = holder.getGlobalPlacement() if holder is not None else App.Placement()
    out = []
    for shape in shapes:
        plain = [frame.multiply(pl) for pl in moved if not isinstance(pl, tuple)]
        mirrored = [pl for pl in moved if isinstance(pl, tuple)]
        if plain:
            out.append((shape, plain))
        for _m, point, normal in mirrored:
            out.append((shape.mirror(point, normal), [frame]))
    return out


class PatternGhost:
    """The view handle of a pattern dialog (the shell's overlay): no drag, only the outlines."""

    def __init__(self, shell, session):
        from incad.features import own_pattern
        self.shell = shell
        self.session = session
        self._node = None
        self._view = None
        self._lead = None
        try:
            self._real = session.dialog.active_pick() in REAL_PICKS  # (the dialog may open on such a pick)
        except Exception:
            self._real = False
        f = session.feature
        if f is not None and own_pattern.is_own(f):
            self._lead = f.Name
            if not self._real:
                own_pattern.GHOST.add(f.Name)
        try:
            session.dialog.activePickChanged.connect(self._pick_changed)
        except Exception:
            pass
        self.refresh()

    def _pick_changed(self, key):
        """Excluding copies or picking the faces to space between: the copies are built (clicked on); after it,
        outlines again."""
        from incad.features import own_pattern
        real = key in REAL_PICKS
        f = self.session.feature
        if real == self._real or f is None or not own_pattern.is_own(f):
            self._real = real
            return
        self._real = real
        if real:
            own_pattern.GHOST.discard(f.Name)
        else:
            own_pattern.GHOST.add(f.Name)
        try:
            f.touch()
            self.session._recompute()
        except Exception:
            pass
        self.refresh()

    # the navigator's handle hook: nothing to drag
    def dragging(self):
        return False

    def hover(self, *a):
        return False

    def press(self, *a):
        return False

    def move(self, *a):
        return False

    def release(self):
        return False

    def finish(self, *a, **k):
        return None

    def _drop_node(self):
        if self._node is not None and self._view is not None:
            try:
                self._view.getSceneGraph().removeChild(self._node)
            except Exception:
                pass
        self._node = None

    def refresh(self):
        from pivy import coin
        import FreeCADGui as Gui
        from incad.features import own_pattern
        self._drop_node()
        f = self.session.feature
        if f is None or not own_pattern.is_own(f):
            return
        if self._lead != f.Name:  # made by the first preview, or made again (another Result): drawn, not built
            if self._lead is not None:
                own_pattern.GHOST.discard(self._lead)
            self._lead = f.Name
            if not self._real:
                own_pattern.GHOST.add(f.Name)
        if f.Name not in own_pattern.GHOST:
            return  # built for the clicks: the copies are there
        try:
            what = copies(f)
        except Exception:
            return  # the dialog says why (count, spacing, nothing picked)
        root = coin.SoSeparator()
        pick = coin.SoPickStyle()
        pick.style = coin.SoPickStyle.UNPICKABLE
        color = coin.SoBaseColor()
        color.rgb.setValue(*_color())
        style = coin.SoDrawStyle()
        style.lineWidth = 2
        for n in (pick, color, style):
            root.addChild(n)
        for shape, places in what:
            geo = _lines(_outline(shape))
            for pl in places:
                one = coin.SoSeparator()
                one.addChild(_transform(pl))
                one.addChild(geo)  # shared: one outline, a transform per copy
                root.addChild(one)
        try:
            self._view = Gui.getDocument(f.Document.Name).ActiveView
            self._view.getSceneGraph().addChild(root)
            self._node = root
        except Exception:
            self._node = None

    def remove(self):
        from incad.features import own_pattern
        self._drop_node()
        if self._lead is not None:
            own_pattern.GHOST.discard(self._lead)


def overlay(shell, session):
    return PatternGhost(shell, session)
