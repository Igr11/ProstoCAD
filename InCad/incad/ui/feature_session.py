"""One feature dialog: one undo transaction around create/edit + live previews; OK commits, Cancel aborts."""
import time
import traceback

from incad.core import document, faceted, log, model, perf, safe
from incad.qt import dispose, QtCore, QtWidgets
from incad.ui.feature_dialog import FeatureDialog


def _edge_under(shape, edge, tol=1e-6):
    """The number of `shape`'s edge that `edge` lies on (same kind of curve; its ends and middle on it), or None."""
    import Part

    def kind_of(e):
        try:
            return type(e.Curve).__name__
        except Exception:  # FreeCAD has no Python type for some OCC curves ("undefined curve type")
            return None

    a, b = edge.FirstParameter, edge.LastParameter
    probes = [Part.Vertex(edge.valueAt(t)) for t in (a, (a + b) / 2, b)]
    kind = kind_of(edge)
    if kind is None:
        return None
    box = edge.BoundBox
    for i, e in enumerate(shape.Edges, 1):
        near = e.BoundBox
        near.enlarge(tol * 10)
        if not near.intersect(box) or e.Length + tol < edge.Length or kind_of(e) != kind:
            continue
        if all(e.distToShape(v)[0] < tol for v in probes):
            return i
    return None


def _edge_key(e):
    """An edge by its length and centre of mass (rounded): equal for an edge a feature left as it was."""
    c = e.CenterOfMass
    return (round(e.Length, 4), round(c.x, 4), round(c.y, 4), round(c.z, 4))


def _new_faces(shape, base):
    """The faces of `shape` that are not on a face of `base` (a point inside each tested): what a feature made."""
    from incad.core import feature_faces as ff
    if base is None:
        return list(shape.Faces)
    prints = ff._prints(base, "Face")
    return [face for face, (t, p, fp) in zip(shape.Faces, ff._inner_points(shape, "Face"))
            if fp not in prints and not ff._on_any(p, t, base, "Face")]


def _is_seam(edge, face):
    try:
        return edge.isSeam(face)
    except Exception:
        return False


_LIFTABLE = ("Part::GeomPlane", "Part::GeomCylinder", "Part::GeomCone", "Part::GeomSphere", "Part::GeomToroid")


def _lifted(face, pts, d):
    """The points moved `d` out of `face` (along its normal there): lines drawn on a surface flicker into it. Not on
    a swept (spline) face: finding a point on it took 6 ms, 11 s for a grown thread's outline at every value."""
    if face.Surface.TypeId not in _LIFTABLE:
        return pts
    out = []
    for p in pts:
        try:
            u, v = face.Surface.parameter(p)
            out.append(p + face.normalAt(u, v) * d)
        except Exception:
            out.append(p)
    return out


def _recompute_sound(doc):
    """doc.recompute(), but a feature that failed is left for OK: every full recompute retries it (the user's jar: an
    Emboss off its face after the End of Part, 8 s each time a Work Plane dialog opened or previewed)."""
    from incad.core import safe
    sk = safe.open_sketch(doc)
    if sk is not None:  # Text, sketch patterns: the sketch only, the part follows on Finish Sketch
        doc.recompute([sk])
        return
    # nor what lies below End of Part: rolled back, not shown (a Chamfer dialog with End of Part moved up took 16 s
    # on each value, recomputing the letters and the lid below it)
    todo = [o for o in doc.Objects if "Touched" in o.State and "Invalid" not in o.State and not safe.rolled_back(o)]
    if todo:
        doc.recompute(todo)


def _feature_alive(obj):
    try:
        obj.TypeId
        return True
    except Exception:
        return False


class _Recomputed:
    """Document observer: the names of the objects a recompute computed."""

    def __init__(self):
        self.names = set()

    def slotRecomputedObject(self, obj):
        try:
            self.names.add(obj.Name)
        except Exception:
            pass


_AGAIN = {}  # kind name -> the values Apply + carries into the next dialog of that kind (picks left out)


def _carried(kind, values):
    """What Apply + keeps for the next one: every value but the picks (the user: the settings are remembered)."""
    return {f.key: values[f.key] for f in kind.FIELDS
            if f.key in values and f.kind not in ("pick", "picks", "heading")}


class FeatureSession(QtCore.QObject):
    def __init__(self, kind, doc, body, profile=None, feature=None, theme="light", parent=None, on_close=None):
        super().__init__(parent)
        self.kind, self.doc, self.body, self.profile = kind, doc, body, profile
        self.doc_name = doc.Name
        self.feature = feature
        self._feature_name = feature.Name if feature is not None else None
        self.creating = feature is None
        self._theme, self._parent, self._on_close = theme, parent, on_close
        self.dialog = None
        self._started = False  # the dialog was made: from then on a session without one is dead (alive)
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._preview)
        self._previewing = False
        self._after_preview = None
        self._pending = None
        self._applied = None
        self._closed = False
        self.finishing = False
        self._again = False
        self._then = None  # what opens once this dialog is committed (another plane of the row clicked)
        self._committed = False
        self._base_shown = None  # the base feature shown instead of the preview while picking geometry
        self._extra = []  # other documents this dialog changes (assembly cuts): one undo step in each
        self._observing = False
        self._seeding = 0  # selection writes of InCad's own still to come back as events (one per reselect)
        self._saved_visibility = []
        self._rolled = None  # while the part is rolled back to this feature: [(feature, was visible)]
        self._recomputed = None  # names the last preview computed: only their shapes are checked
        self._markers = None       # key points of the sketches picked for Sweep/Loft, on the view
        self._saved_sketches = []  # picked sketches shown for the dialog, hidden again when it closes
        self.on_previewed = None  # callable() after each preview that worked (the shell's overlay follows)

    def start(self):
        doc = self.doc
        if not doc.UndoMode:
            doc.UndoMode = 1
        if doc.HasPendingTransaction:
            doc.commitTransaction()
        self._broken = safe.snapshot_broken(doc)
        self._links = safe.link_snapshot(doc)  # the edges / faces fillets etc. are on (found again after a size edit)
        self._computed = _Recomputed()  # every object a recompute computed while the dialog is open (_abort_step)
        try:
            import FreeCAD as App
            App.addDocumentObserver(self._computed)
        except Exception:
            self._computed = None
        self._undo_mark = doc.UndoCount  # Cancel undoes back to here: a Sketcher command run meanwhile (Delete
        if not self.creating:  # every document's undo step opens before anything changes   on a copy) makes
            self._join_documents(self.kind.read(doc, self.feature))  # FreeCAD commit the dialog's own pending step
        doc.openTransaction(self.kind.TITLE)
        safe.set_busy(self.kind.TITLE)
        try:
            if self.creating:
                values = self.kind.defaults(doc, self.body, self.profile)
                again = _AGAIN.pop(getattr(self.kind, "__name__", ""), None)  # Apply +: the last one's settings
                if again:
                    values.update({k: v for k, v in again.items() if k in values})
                if getattr(self.kind, "NEEDS_PROFILE", True):
                    document.adopt(self.body, self.profile)  # a sketch left in another Solid comes along
                    try:
                        self.feature = self.kind.create(doc, self.body, self.profile, values)
                    except Exception:
                        if not self._picks_to_come(values):
                            raise
                        self.feature = None  # (Revolve with no axis yet) the dialog opens and waits for the
                        self._drop_partial()  # click; the first preview after it makes the feature
                    else:
                        self._created_with = values.get("operation")
                        self._created_values = dict(values)
            else:
                values = self.kind.read(doc, self.feature)
            _recompute_sound(doc)
        except Exception as e:
            log.report("error", "%s could not start: %s" % (self.kind.TITLE, e))
            self._finish(commit=False)
            return False
        self.dialog = FeatureDialog(self.kind.TITLE, self.kind.ICON, self.kind.FIELDS, values, self._theme, self._parent)
        self._started = True
        self._applied = self.dialog.values()
        if not self.creating:
            for value in getattr(self.kind, "STRUCTURAL", ("intersect", "new_solid")):
                if "operation" in self.dialog._widgets and value in self.dialog.field("operation")._buttons:
                    self.dialog.set_choice_enabled("operation", value, False)
            locked = getattr(self.kind, "LOCKED_WHEN_EDITING", getattr(self.kind, "RECREATE_ON", ()))
            for key in locked:  # can't change on an existing feature (a gear set's update copes with them all)
                if key in self.dialog._widgets:
                    self.dialog.field(key).setEnabled(False)
        self.dialog.changed.connect(self._changed)
        self._sync_choices(self.dialog.values())
        self.dialog.accepted.connect(self.ok)
        self.dialog.applied.connect(self.apply)
        self.dialog.cancelled.connect(self.cancel)
        self.dialog.activePickChanged.connect(lambda _key: self._sync_pick_view(seed=True))
        self.dialog.picksEdited.connect(self._picks_edited)
        self._refresh_options()
        self._show_info()
        if self.feature is not None:
            self.dialog.set_values({f.key: values.get(f.key) for f in self.kind.FIELDS if f.kind == "combo"})
        self._applied = self.dialog.values()
        self._seen = self.dialog.values()  # what kind.follow compares a change with
        self._label_params()
        self.dialog.place()  # where the user left the last one
        self.dialog.show()
        self.dialog.raise_()
        if self.feature is None:
            self._apply_preselection()
            first_empty = next(iter(self._waiting_picks()), None)
            self._pending = self.dialog.values()
            self._preview()  # first try: defaults (+ preselection) may already be enough
            if self._closed:  # Esc or OK while that first preview computed (seconds for a thread)
                return True
            if first_empty is not None:
                self.dialog.set_active_pick(first_empty)
        else:
            self._check()
            if self.creating and self.dialog is not None and self._waiting_picks():  # made from its profile
                self._apply_preselection()  # (Emboss) and still waiting for a pick: a face selected first fills it,
                waiting = self._waiting_picks()  # else that pick is active (Extrude, Revolve wait for none)
                if waiting:
                    self.dialog.set_active_pick(waiting[0])
        if self.dialog is not None:
            self.dialog.reset_history()  # seeding is not the user's change: Undo stops (and drops the dialog) here
        self._seen_waiting = self._waiting_picks() if self.dialog is not None else []
        self._observe(True)
        self._sync_sketch_markers()  # an edited Sweep/Loft shows its sketches' points from the start
        self._sync_axis_line()  # an edited pattern / revolve on two points shows its axis from the start
        return True

    def _picks_to_come(self, values):
        """Pick fields the dialog will wait for (shown, empty, not optional) before it opens."""
        from incad.features.common import shown
        return [f.key for f in self.kind.FIELDS if f.kind in ("pick", "picks") and not f.optional
                and shown(f, values) and not values.get(f.key)]

    def _shown(self, field):
        from incad.features.common import shown
        return shown(field, self.dialog.values())

    @property
    def alive(self):
        """Open and with its dialog (before start: not yet made, still alive). The shell lets go of a dead one: a
        Thread dialog vanished and every command after said 'Finish the Thread dialog first'."""
        return not self._closed and (not self._started or self.dialog is not None)

    def _waiting_picks(self):
        """The pick fields still to fill, in dialog order: shown, empty and without a default (not optional)."""
        if self.dialog is None:
            return []
        values = self.dialog.values()
        return [f.key for f in self.kind.FIELDS if f.kind in ("pick", "picks") and not f.optional
                and self._shown(f) and not values.get(f.key)]

    def _refresh_options(self):
        """Combos whose items depend on other values (e.g. thread sizes of the chosen standard)."""
        options = getattr(self.kind, "options", None)
        if options is None or self.dialog is None:
            return
        values = self.dialog.values()
        for f in self.kind.FIELDS:
            if f.kind == "combo":
                try:
                    profile = self.profile if self.profile is not None else (
                        __import__("incad.features.common", fromlist=["profile_of"]).profile_of(self.feature)
                        if self.feature is not None else None)
                    items = options(f.key, values, profile) if options.__code__.co_argcount >= 3 \
                        else options(f.key, values)
                except Exception as e:
                    log.report("warning", "Could not list %s: %s" % (f.label, e))
                    items = None
                if items is not None:
                    self.dialog.set_options(f.key, items)

    # ----- picking from the 3D view -----
    _GEOMETRY = ("edge", "edge_axis", "face", "vertex", "axis", "rot_axis", "plane", "circular_edge", "round", "hole_face", "limit",
                 "to_face", "direction", "emboss_face")
    _PICK_WORDS = {"direction": "a flat face or a straight edge",  # 'Select … for First'; else the pick's own name
                   "edge_axis": "an edge or an axis",
                   "to_face": "a face, a plane or a point",
                   "rot_axis": "an axis, a straight or circular edge, or a round face",
                   "emboss_face": "a round (cylinder, cone or sphere) or a flat face",
                   "move_to": "a face, plane, edge or point",
                   "round": "round edges or round faces (a hole at the centre of each)",
                   "hole_face": "the flat face the holes start on",
                   "gear": "another Gear or a Worm", "gear_center": "a circle, a corner or a point",
                   "gear_plane": "a plane, a flat face or a sketch"}
    # picks made on the shape: the base is shown while they are active (Move Face To also takes other Solids' edges,
    # so it is no _GEOMETRY pick that must lie on the base)
    _ON_BASE = _GEOMETRY + ("two_points", "three_points", "axis_points", "marked_point", "move_to")
    # clicks on the part's markers, not the selection (a work plane's and a work axis's points: every shown Solid's)
    _MARKER_PICKS = {"two_points": 2, "three_points": 3, "axis_points": 2, "marked_point": 1}
    _CORNER_PICKS = ("move_to", "vertex")  # picks that take a point: a click near a corner picks the corner
    _ANYWHERE = ("to_face",)  # Extrude To: a face or point of any Solid, not only the one being built on

    def _observe(self, on):
        try:
            import FreeCADGui as Gui
        except ImportError:
            return
        if on and not self._observing and not self._closed:
            Gui.Selection.addObserver(self)
            self._observing = True
        elif not on and self._observing:
            Gui.Selection.removeObserver(self)
            self._observing = False

    def _apply_preselection(self):
        from incad.features import common
        hook = getattr(self.kind, "preselect", None)
        if hook is not None:
            for key, text in hook(self._selection()).items():
                self.dialog.set_pick(key, text)
            return
        wanted = getattr(self.kind, "PRESELECT", {})
        if not wanted:
            return
        pairs = self._selection()
        if getattr(self.kind, "SOLID_PICKS", False):
            pairs = self._onto_solid(pairs)  # edges selected beforehand on a sketch / area: the part's own
        if getattr(self.kind, "FACE_EDGES", False):
            pairs = self._face_edges(pairs)
        if getattr(self.kind, "TANGENT_CHAIN", False):
            pairs = self._tangent_chains(pairs)
        for key, pick in wanted.items():
            ok = common.whole(pick, [p for p in self._as_picks(pick, pairs) if common.accepts(pick, *p)])
            if ok:
                field = self.dialog.field_def(key)
                self.dialog.set_pick(key, common.format_picks(ok[-1:] if field.kind == "pick" else ok))
                if pick == "feature" and self._picked_a_thread(pairs) and any(
                        g.key == "children" for g in self.kind.FIELDS):
                    self.dialog.set_values({"children": True})

    @staticmethod
    def _as_picks(pick, pairs):
        """A feature pick also takes a face/edge clicked in the view: it stands for the feature that created it. A
        solid pick (Boolean's base and tools) takes a click anywhere on the solid: it stands for the whole solid."""
        from incad.features import common
        if pick == "body":
            from incad.features.combine import body_of
            out = []
            for o, el in pairs:
                b = body_of(o, el)
                if b is not None and (b, "") not in out:
                    out.append((b, ""))
            return out
        if pick == "gear":  # a click on its teeth means the Gear or Worm itself
            from incad.features import gear
            return [(gear.gear_of(o, el) or o, "" if gear.gear_of(o, el) else el) for o, el in pairs]
        if pick != "feature":
            return pairs
        out = []
        for o, el in pairs:
            made = common.feature_of(o, el)
            out.append((made, "") if made is not None else (o, el))
        return out

    def _picked_a_thread(self, pairs):
        """True if any raw pick is a thread (its row, or a face its helix made): the thread comes along with the
        shaft it stands for, so the pattern's 'Include dependent features' is switched on."""
        from incad.features import common
        for o, el in pairs:
            raw = common.raw_feature_of(o, el)
            if raw is not None and raw.TypeId.endswith("Helix"):
                return True
        return False

    CORNER_PX = 8  # logical px: a click this near a corner of the clicked edge / face picks the corner

    def _corner_near(self, sel, i, sub):
        """The clicked element's corner the click landed near (on screen), as the sub name to use instead; else
        `sub`. FreeCAD hands a vertex only to a click within about a pixel of it, the edge or face otherwise."""
        import FreeCADGui as Gui
        import Part
        from incad.ui import dof_handles
        if not sub.split(".")[-1].startswith(("Edge", "Face")):
            return sub
        try:
            el, picked = sel.SubObjects[i], sel.PickedPoints[i]
            view = Gui.getDocument(self.doc_name).ActiveView
            viewport = view.graphicsView().viewport()
            at = dof_handles.screen(view, viewport, picked)
            best, best_d = None, self.CORNER_PX
            for v in el.Vertexes:
                x, y = dof_handles.screen(view, viewport, v.Point)
                d = ((x - at[0]) ** 2 + (y - at[1]) ** 2) ** 0.5
                if d <= best_d:
                    best, best_d = v.Point, d
            if best is None:
                return sub
            prefix = sub[:sub.rfind(".") + 1]  # 'Body.Pad.' in a path, '' for a plain 'Edge3'
            whole = Part.getShape(sel.Object, prefix, needSubElement=bool(prefix), transform=True)
            n = next((k for k, w in enumerate(whole.Vertexes, 1) if (w.Point - best).Length < 1e-6), None)
        except Exception:
            log.logger().exception("corner near a pick")
            return sub
        return sub if n is None else "%sVertex%d" % (prefix, n)

    def _previewing_picks(self, key):
        """Shell, Fillet and Chamfer keep their preview on screen while faces / edges are picked (the result is
        what the user judges): a click on the preview stands for the part's face or edge it lies on
        (_onto_base_faces)."""
        if not key or not getattr(self.kind, "PREVIEW_WHILE_PICKING", False) or self.dialog is None:
            return False
        if self.dialog.field_def(key).pick not in ("face", "edge") or self.feature is None \
                or not _feature_alive(self.feature):
            return False
        return "Invalid" not in self.feature.State

    def _onto_base_faces(self, pairs):
        """A face of the preview -> the face of the part it lies on (same plane or cylinder, overlapping); an edge
        -> the part's edge it lies on (a fillet shortens its neighbours); what the feature made (an inner wall, a
        round, a chamfer's edges) is no pick on the part."""
        base = self._expected_base()
        shape = base.Shape if base is not None and not base.Shape.isNull() else None
        out = []
        for obj, el in pairs:
            if obj is not self.feature:
                out.append((obj, el))
                continue
            try:
                mine = obj.Shape.getElement(el) if el.startswith(("Face", "Edge")) else None
            except Exception:
                mine = None
            if mine is None or shape is None:
                continue
            if el.startswith("Face"):
                from incad.features import common
                match = next((i for i, g in enumerate(shape.Faces, 1) if common._carries_face(g, mine)), None)
                name = "Face%d" % match if match is not None else None
            else:
                match = _edge_under(shape, mine)
                name = "Edge%d" % match if match is not None else None
            if name is not None:
                out.append((base, name))
        return out

    @staticmethod
    def _face_edges(pairs):
        """A click on a face where edges are wanted takes all its edges (Fillet / Chamfer round the whole face, as in
        Inventor); a cylinder's seam is no edge of the part's outline and stays out."""
        import Part
        out, seen = [], set()
        for obj, el in pairs:
            names = [el]
            if el.startswith("Face") and not obj.Shape.isNull():
                shape = obj.Shape
                try:
                    face = shape.getElement(el)
                except Exception:
                    face = None
                if face is not None:
                    number = {}
                    for k, x in enumerate(shape.Edges, 1):
                        number.setdefault(x.hashCode(), []).append((k, x))
                    names = []
                    for e in face.Edges:
                        if e.Degenerated or len(shape.ancestorsOfType(e, Part.Face)) < 2:
                            continue  # a seam: the face meets itself there
                        i = next((k for k, x in number.get(e.hashCode(), ()) if x.isSame(e)), None)
                        if i is not None:
                            names.append("Edge%d" % i)
            for name in names:
                if (obj.Name, name) not in seen:
                    seen.add((obj.Name, name))
                    out.append((obj, name))
        return out

    @staticmethod
    def _tangent_chains(pairs):
        """A click on an edge takes the smooth chain it belongs to: the edges running on from it without a corner
        (a slot's lines and arcs, a ring in pieces), as Inventor / Fusion pick for a fillet or chamfer."""
        import Part
        out, seen = [], set()
        for obj, el in pairs:
            chain = [el]
            if el.startswith("Edge") and not obj.Shape.isNull():
                shape = obj.Shape
                try:
                    start = shape.getElement(el)
                except Exception:
                    start = None
                if start is not None:
                    edges = shape.Edges  # once: each .Edges builds every edge of the part (hung on a big one)
                    number = {}
                    for k, x in enumerate(edges, 1):
                        number.setdefault(x.hashCode(), []).append((k, x))

                    def known(x, seen_):
                        return any(x.isSame(y) for y in seen_.get(x.hashCode(), ()))

                    chain, todo, picked, seen_ = [], [start], [start], {start.hashCode(): [start]}
                    while todo:
                        e = todo.pop()
                        for v in e.Vertexes:
                            for nb in shape.ancestorsOfType(v, Part.Edge):
                                if nb.Degenerated or known(nb, seen_):
                                    continue
                                try:
                                    t1 = e.tangentAt(e.parameterAt(v))
                                    t2 = nb.tangentAt(nb.parameterAt(v))
                                except Exception:
                                    continue
                                if t1.Length > 1e-12 and t2.Length > 1e-12 and \
                                        t1.normalize().cross(t2.normalize()).Length < 1e-6:
                                    picked.append(nb)
                                    seen_.setdefault(nb.hashCode(), []).append(nb)
                                    todo.append(nb)
                    for e in picked:
                        i = next((k for k, x in number.get(e.hashCode(), ()) if x.isSame(e)), None)
                        if i is not None:
                            chain.append("Edge%d" % i)
            for name in chain:
                if (obj.Name, name) not in seen:
                    seen.add((obj.Name, name))
                    out.append((obj, name))
        return out

    @staticmethod
    def _whole_outlines(pairs):
        """A click on one line of a shape (a polygon, a rectangle, a chain drawn line by line) takes its whole
        outline — the lines joined to it end to end (Inventor; one click per side before)."""
        from incad.core import sketch_offset as so
        out, seen = [], set()
        for obj, el in pairs:
            if obj.TypeId == "Sketcher::SketchObject" and el.startswith("Edge"):
                try:
                    chain, _ = so.outline(obj, int(el[4:]) - 1)
                except (ValueError, IndexError):
                    chain = None
                if chain:
                    for g in chain:
                        key = (obj.Name, "Edge%d" % (g + 1))
                        if key not in seen:
                            seen.add(key)
                            out.append((obj, key[1]))
                    continue
            key = (obj.Name, el)
            if key not in seen:
                seen.add(key)
                out.append((obj, el))
        return out

    def _onto_solid(self, pairs):
        """A click on a shown sketch's line or point lying on the part's edge or corner stands for that edge or
        corner (a fillet on the edge a profile line made); any other sketch element is no pick on the part."""
        import Part
        base = self._expected_base()
        shape = base.Shape if base is not None and not base.Shape.isNull() else None
        out = []
        for obj, el in pairs:
            # a sketch line, or an "Area n" face shown for a region pick, over the part's edge: that edge
            if obj.TypeId.startswith("PartDesign::") or obj is base:
                out.append((obj, el))
                continue
            try:
                mine = obj.Shape.getElement(el) if el.startswith(("Edge", "Vertex")) else None
            except Exception:
                mine = None
            if mine is None or shape is None:
                continue
            if el.startswith("Vertex"):
                match = next((i for i, v in enumerate(shape.Vertexes, 1) if (v.Point - mine.Point).Length < 1e-6), None)
                name = "Vertex%d" % match if match else None
            else:
                # the part's edge the line lies on: the same curve through the line's middle (the part may have it
                # split or longer, where a pad meets a face — whole-length matches only missed those)
                mid = mine.valueAt((mine.FirstParameter + mine.LastParameter) / 2)
                quarter = mine.valueAt(mine.FirstParameter + (mine.LastParameter - mine.FirstParameter) * 0.3)
                match = None
                for i, e in enumerate(shape.Edges, 1):
                    try:
                        near = e.BoundBox
                        near.enlarge(1e-5)
                        if not near.isInside(mid):
                            continue
                        if e.distToShape(Part.Vertex(mid))[0] < 1e-6 and \
                                (e.distToShape(Part.Vertex(quarter))[0] < 1e-6 or e.Length < mine.Length):
                            match = i
                            break
                    except Exception:
                        continue
                name = "Edge%d" % match if match else None
            if name is not None:
                out.append((base, name))
        return out

    def _selection(self, corners=False):
        import FreeCADGui as Gui
        from incad.features import common
        pairs = []
        for sel in common.selection_ex(self.doc_name):  # in an assembly: the path through the component
            for i, sub in enumerate(sel.SubElementNames or ("",)):
                if corners:
                    sub = self._corner_near(sel, i, sub)
                obj, el = common.resolve(sel.Object, sub)
                own = self.dialog is not None and (self.dialog.active_pick() in getattr(self.kind, "PICKS_ON_SELF", ())
                                                   or self._previewing_picks(self.dialog.active_pick()))
                if obj is not None and (obj is not self.feature or own or getattr(self.kind, "IN_SKETCH", False)):
                    # the feature being made can't be its own input (a sketch pattern's picks ARE on the sketch)
                    pairs.append((obj, el))
        return pairs

    _DATUMS = ("PartDesign::Plane", "PartDesign::Line", "PartDesign::Point", "PartDesign::CoordinateSystem")

    def _expected_base(self):
        """The shape geometry picks must lie on: what the feature starts from."""
        if getattr(self.kind, "IN_SKETCH", False):
            return None  # picks are the sketch's own elements: nothing to show or check
        if self.feature is not None:
            base = getattr(self.feature, "BaseFeature", None)
            while base is not None and getattr(base, "InCadHelperOf", None) == self.feature.Name:
                base = getattr(base, "BaseFeature", None)  # a thread's own fill / bore / trim: picks go on the part
            return base
        return self.body.Tip if self.body is not None else None

    def _pick_base(self):
        """What picks are named on and must lie on: the expected base; for a work feature being edited (it has no
        BaseFeature) its Solid as it stands at the feature: corners re-clicked on its Edit were fixed points, the
        plane no longer followed the part, and another Solid's picks were taken."""
        if self.feature is not None and self.feature.TypeId in self._DATUMS:
            return self._solid_before(self.feature)
        return self._expected_base()

    def _solid_before(self, f):
        """The Solid's shape as it stands at f: the last solid feature before f in its Solid (the Tip if none)."""
        body = f.getParentGeoFeatureGroup()
        if body is None or body.TypeId != "PartDesign::Body":
            return None
        before = None
        for o in body.Group:
            if o is f:
                break
            if hasattr(o, "BaseFeature") and o.TypeId not in self._DATUMS and not o.Shape.isNull():
                before = o
        return before or body.Tip

    def _retarget_body(self, pairs):
        """A pick on another Solid of the part before the feature exists: the feature goes to that Solid (a box
        drawn away from the part is its own Solid; Shell on its face hollows the box). A box left in the part's
        body by an older file is split off first; its elements are renumbered by that, so the pairs come back
        remapped (and the dialog's pick texts with them)."""
        if self.feature is not None or getattr(self.kind, "IN_SKETCH", False):
            return pairs
        from incad.core import document
        for obj, _ in pairs:
            owner = obj.getParentGeoFeatureGroup() if hasattr(obj, "getParentGeoFeatureGroup") else None
            if owner is None or owner.TypeId != "PartDesign::Body":
                continue
            tried = self.__dict__.setdefault("_detach_tried", set())
            if obj.Name not in tried and document.is_detached_lump(obj):
                tried.add(obj.Name)  # once per dialog: a move that cannot be made is not tried on every pick
                old_shape = obj.Shape.copy()
                try:
                    owner = document.detach_lump(obj)
                except Exception as e:
                    log.report("warning", "%s does not touch the part, but stays where it is: %s" % (obj.Label, e))
                    return pairs
                log.report("info", "%s does not touch the part: made its own Solid (%s)" % (obj.Label, owner.Label))
                pairs = self._remap_pairs(pairs, obj, old_shape)
                self._remap_dialog(obj, old_shape)
            if owner is not self.body:
                self.body = owner
            return pairs
        return pairs

    @staticmethod
    def _remap_pairs(pairs, obj, old_shape):
        from incad.core import document
        out = []
        for o, sub in pairs:
            if o is obj and sub:
                sub = document.remap_sub(old_shape, obj.Shape, sub) or sub
            out.append((o, sub))
        return out

    def _remap_dialog(self, obj, old_shape):
        from incad.features import common
        if self.dialog is None:
            return
        for f in self.kind.FIELDS:
            if f.kind in ("pick", "picks") and self.dialog.values().get(f.key):
                pairs = common.parse_picks(self.doc, self.dialog.values().get(f.key))
                if any(o is obj for o, _ in pairs):
                    self.dialog.set_pick(f.key, common.format_picks(self._remap_pairs(pairs, obj, old_shape)))

    def _retarget_from_values(self, values):
        """The picks typed or seeded into the dialog may belong to another Solid: same rule as a 3D pick; a
        split-off piece renumbers its elements, so the values are rewritten too."""
        from incad.features import common
        pairs = []
        keys = {}
        for f in self.kind.FIELDS:
            if f.kind in ("pick", "picks") and values.get(f.key):
                parsed = common.parse_picks(self.doc, values.get(f.key))
                keys[f.key] = parsed
                pairs += parsed
        before = [(o, sub) for o, sub in pairs]
        after = self._retarget_body(pairs)
        if after != before:
            changed = dict(zip(before, after))
            for key, parsed in keys.items():
                values[key] = common.format_picks([changed.get(pr, pr) for pr in parsed])

    def _off_base(self, pairs):
        base = self._pick_base()
        if base is None:
            return []
        return [o for o, el in pairs if el and o is not base and o.TypeId.startswith("PartDesign::")
                and o.TypeId not in self._DATUMS]

    def addSelection(self, doc, obj, sub, pnt):
        QtCore.QTimer.singleShot(0, self._selection_changed)

    def removeSelection(self, doc, obj, sub):
        QtCore.QTimer.singleShot(0, self._selection_changed)

    def setSelection(self, doc):
        QtCore.QTimer.singleShot(0, self._selection_changed)

    def _switch_to_picked(self):
        """A click on another feature of the same set (a plane of the row): this one is kept as the dialog shows it
        (OK) and that one's dialog opens, its rings on it."""
        hook = getattr(self.kind, "switch_to", None)
        if hook is None or self.feature is None or self._then is not None:
            return False
        other = None
        for obj, _ in self._selection():
            other = hook(self.doc, self.feature, obj)
            if other is not None:
                break
        if other is None:
            return False
        name, doc_name = other.Name, self.doc_name

        def reopen():
            import FreeCAD as App
            from incad.ui import shell
            doc = App.getDocument(doc_name) if doc_name in App.listDocuments() else None
            obj = doc.getObject(name) if doc is not None else None
            if obj is not None:
                try:
                    import FreeCADGui as Gui
                    Gui.Selection.clearSelection()
                    shell.edit_feature(obj)
                except Exception as e:
                    log.report("warning", "%s: %s" % (self.kind.TITLE, e))
        self._then = reopen
        self.ok()
        if not self._closed and not self._previewing:  # OK refused (an error shows): stay on this one
            self._then = None
        return True

    def _selection_changed(self):
        from incad.features import common
        if self._closed or self.dialog is None or self._seeding:
            return
        if self._switch_to_picked():
            return
        key = self.dialog.active_pick()
        if key is None:
            return
        f = self.dialog.field_def(key)
        if f.pick in self._MARKER_PICKS:
            return  # taken by clicks on the markers, not by the selection
        raw_pairs = self._selection(corners=f.pick in self._CORNER_PICKS)
        if getattr(self.kind, "SOLID_PICKS", False) and f.pick in self._GEOMETRY + ("move_to",):
            raw_pairs = self._onto_solid(raw_pairs)
        if self._previewing_picks(key):
            raw_pairs = self._onto_base_faces(raw_pairs)
        outlines = f.pick in ("sketch_geometry", "sketch_path")
        if outlines:
            raw_pairs = self._whole_outlines(raw_pairs)
        elif f.pick == "edge" and getattr(self.kind, "TANGENT_CHAIN", False):
            if getattr(self.kind, "FACE_EDGES", False):
                raw_pairs = self._face_edges(raw_pairs)
            raw_pairs = self._tangent_chains(raw_pairs)
            outlines = True  # the whole chain shows picked
        pairs = self._as_picks(f.pick, raw_pairs)
        if f.pick == "body":  # a solid already picked in another field (the base) is not a tool too
            taken = {o.Name for g in self.kind.FIELDS if g.pick == "body" and g.key != key
                     for o, _ in common.parse_picks(self.doc, self.dialog.values().get(g.key))}
            pairs = [(o, el) for o, el in pairs if o.Name not in taken]
        pairs = self._retarget_body(pairs)
        if f.pick in self._GEOMETRY and f.pick not in self._ANYWHERE and self._off_base(pairs):
            self.dialog.set_error("Pick on the current shape (%s)" % self._pick_base().Label)
            return
        ok = common.whole(f.pick, [p for p in pairs if common.accepts(f.pick, *p)])
        if pairs and not ok:
            if f.pick in self._GEOMETRY and not any(el for _, el in pairs):
                return  # a whole object (a tree row) while a face/edge is wanted: just looking, not an error
            self.dialog.set_error("Select %s for %s" % (self._PICK_WORDS.get(f.pick, f.pick.replace("_", " ")), f.label))
            self._pick_error = True
            return
        if not pairs and getattr(self, "_pick_error", False):
            self.dialog.set_error("")
            self._pick_error = False
        if not ok:
            return
        if f.kind == "picks":
            ok = self._collect_picks(key, ok)
            if outlines:
                self._reselect(ok)  # the whole outline shows picked, not just the line clicked
        elif f.pick in self._CORNER_PICKS and ok[-1][1].startswith("Vertex"):
            self._reselect(ok[-1:])  # a corner taken from an edge / face click: the corner shows selected
        self.dialog.set_pick(key, common.format_picks(ok[-1:] if f.kind == "pick" else ok))
        if f.pick == "feature" and self._picked_a_thread(raw_pairs) and any(
                g.key == "children" for g in self.kind.FIELDS) and not self.dialog.values().get("children"):
            self.dialog.set_values({"children": True})
        if f.kind == "pick":  # a single pick is done: go on to the next empty one, or stop picking
            nxt = next((k for k in self._waiting_picks() if k != key), None)
            self.dialog.set_active_pick(nxt)

    def _sync_pick_view(self, seed=False):
        """While a geometry pick is active, show the base shape (picks go on it) instead of the preview and of
        any later features; put everything back exactly as it was afterwards."""
        want = None
        key = self.dialog.active_pick() if self.dialog is not None else None
        if getattr(self.kind, "IN_SKETCH", False):
            if seed and key:
                self._seed_selection(key)
            return
        keep = getattr(self.kind, "keep_preview", None)  # a kind whose result stays on screen while picking
        if keep is not None and keep(self.dialog.values()):
            key_on_base = False
        else:
            key_on_base = bool(key) and self.dialog.field_def(key).pick in self._ON_BASE
        if key_on_base and self.feature is not None:
            tip = self.body.Tip if self.body is not None else None
            # the part as it is: every edge can be picked and the picked ones show selected (on the preview they were
            # hidden, cut away); Fillet / Chamfer / Shell draw their result over it as outlines (_sync_ghost)
            want = self._expected_base()
        if want is not self._base_shown:
            self._show_base(False)
            if want is not None:
                tip = self.body.Tip if self.body is not None else None
                shown = [o for o in (want, self.feature, tip) if o is not None]
                self._saved_visibility = [(o, bool(o.Visibility)) for o in dict.fromkeys(shown)]
                self._base_shown = want
                for o in dict.fromkeys(shown):
                    self._set_visible(o, o is want)
        # the result drawn over the part as outlines whenever the part is shown for a pick (Fillet / Chamfer's
        # preview, and Move Face, Draft… whose result was not on screen at all while faces were picked)
        self._sync_ghost(bool(key) and (self._previewing_picks(key) or self._base_shown is not None))
        self._peek_axes(bool(key) and self.dialog.field_def(key).pick in ("axis", "rot_axis", "edge_axis"))
        if seed and key:
            self._seed_selection(key)
        self._sync_point_pick(key)
        hook = getattr(self.kind, "picking", None)  # Split ▸ Keep picked: every piece shows while they are picked
        if hook is not None and self.feature is not None and _feature_alive(self.feature):
            active = bool(key) and key in getattr(self.kind, "PICKS_ON_SELF", ())
            if bool(getattr(self.feature, "ShowAll", False)) != active:
                hook(self.feature, active)
                self.doc.recompute()

    def _sync_axis_line(self, on=True):
        """The hidden Work Axis InCad made through two clicked points (Revolve, Circular Pattern) drawn while the
        dialog is open: a dashed line through the points, a little past them, and the two points; never pickable."""
        try:
            import FreeCADGui as Gui
            from pivy import coin
        except ImportError:
            return
        old = getattr(self, "_axis_line", None)
        if old is not None:
            try:
                old[0].getSceneGraph().removeChild(old[1])
            except Exception:
                pass
            self._axis_line = None
        f = self.feature
        if not on or self._closed or f is None or not _feature_alive(f):
            return
        body = f.getParentGeoFeatureGroup()
        line = next((o for o in (body.Group if body is not None else []) if o.TypeId == "PartDesign::Line"
                     and getattr(o, "InCadHelperOf", None) == f.Name and getattr(o, "InCadAxisPoints", "")), None)
        if line is None:
            return
        try:
            from incad.features import revolve
            pts = revolve.parse_points(line.InCadAxisPoints)
            if len(pts) != 2 or (pts[1] - pts[0]).Length < 1e-9:
                return
            pl = body.getGlobalPlacement()
            a, b = pl.multVec(pts[0]), pl.multVec(pts[1])
            u = b - a
            reach = max(u.Length * 0.25, 5.0)
            u.normalize()
            ends = [a - u * reach, b + u * reach]
            node = coin.SoSeparator()
            style = coin.SoPickStyle()
            style.style = coin.SoPickStyle.UNPICKABLE
            color = coin.SoBaseColor()
            color.rgb.setValue(0.95, 0.62, 0.29)
            draw = coin.SoDrawStyle()
            draw.lineWidth = 2
            draw.linePattern = 0xF0F0  # dashed: an axis, not an edge of the part
            draw.pointSize = 7
            coords = coin.SoCoordinate3()
            coords.point.setValues(0, 4, [(p.x, p.y, p.z) for p in ends + [a, b]])
            lines = coin.SoLineSet()
            lines.numVertices.setValues(0, 1, [2])
            dots = coin.SoPointSet()
            dots.startIndex = 2
            dots.numPoints = 2
            for n in (style, color, draw, coords, lines, dots):
                node.addChild(n)
            top = coin.SoAnnotation()
            top.addChild(node)
            view = Gui.getDocument(self.doc_name).ActiveView
            view.getSceneGraph().addChild(top)
            self._axis_line = (view, top)
        except Exception:
            log.logger().exception("%s axis line", self.kind.TITLE)

    def _peek_axes(self, on):
        """While an axis is picked, the origin's X, Y and Z axes show (the Z default was the only one to be had
        with the origin hidden); put back exactly as they were afterwards. Not recorded: no choice is changed."""
        saved = getattr(self, "_axes_shown", None)
        if on and saved is None and self.body is not None:
            origin = getattr(self.body, "Origin", None)
            if origin is None:
                return
            items = list(origin.OriginFeatures)
            self._axes_shown = [(o, bool(o.Visibility)) for o in [origin] + items]
            for o in items:
                self._set_visible(o, o.Role in ("X_Axis", "Y_Axis", "Z_Axis"))
            self._set_visible(origin, True)
        elif not on and saved is not None:
            self._axes_shown = None
            for o, visible in saved:
                if _feature_alive(o):
                    self._set_visible(o, visible)

    def _sync_point_pick(self, key):
        """A 'two points' / 'three points' field that becomes active shows the part's markers (corners, edge
        middles, circle centres, the profile's points too) and takes two / three clicks on them; any other active
        field, or none, ends that pick."""
        from incad.ui import shell as shells
        sh = shells.instance()
        if sh is None:
            return
        field = self.dialog.field_def(key) if key else None
        if field is None or field.pick not in self._MARKER_PICKS:
            if getattr(self, "_point_key", None) is not None:
                self._point_key = None
                sh.cancel_point_pick(quiet=True)
            return
        if getattr(self, "_point_key", None) == key and sh._point_pick is not None:
            return  # already showing the markers for this field
        self._point_key = key
        points = self._marker_points(field.pick)
        if not points:
            self.dialog.set_error("Nothing to click: draw the profile first")
            self._point_key = None
            self.dialog.set_active_pick(None)
            return
        count = self._MARKER_PICKS[field.pick]
        sh.start_point_pick(points, count, "%s: click %s — corners, edge middles, circle centres (Esc cancels)"
                            % (self.kind.TITLE, "a point" if count == 1 else "%d points" % count), on_done=lambda pts, k=key: self._points_picked(k, pts),
                            on_cancel=lambda k=key: self._points_cancelled(k))

    def _marker_points(self, pick):
        """[(kind, point)] to click: the base and the profile for an axis; for a work plane, as in Section, every
        Solid of the part that is shown, and the shown sketches."""
        import FreeCAD as App
        from incad.core import document, section
        base = self._expected_base()
        if pick in ("three_points", "axis_points", "marked_point") and self.body is not None:  # global, as drawn
            shapes = [b.Shape for b in document.bodies(self.doc) if not b.Shape.isNull() and document.is_shown(b)]
            for o in self.doc.Objects:
                if o.TypeId == "Sketcher::SketchObject" and document.is_shown(o) and not o.Shape.isNull():
                    group = o.getParentGeoFeatureGroup()
                    sh = o.Shape.copy()
                    if group is not None:
                        sh.Placement = group.getGlobalPlacement().multiply(sh.Placement)
                    shapes.append(sh)
        else:
            shapes = [base.Shape if base is not None else None,
                      self.profile.Shape if self.profile is not None else None]
        found = []
        for shape in shapes:
            if shape is not None and not shape.isNull():
                found += section.snap_points(shape)
        if pick in ("three_points", "axis_points", "marked_point"):
            from incad.features import line3d  # a 3D line's ends and the points it passes through (the user)
            for o in self.doc.Objects:
                if line3d.claims(o) and document.is_shown(o):
                    found += line3d.marker_points(o)
                elif o.TypeId == "PartDesign::Point" and o is not self.feature and document.is_shown(o) \
                        and "Invalid" not in o.State:  # a work point (an axis through two of them)
                    group = o.getParentGeoFeatureGroup()
                    g = group.getGlobalPlacement() if group is not None else App.Placement()
                    found.append(("corner", g.multVec(o.Placement.Base)))
        points = []
        for kind, p in found:
            if not any((p - q).Length < 1e-6 for _, q in points):
                points.append((kind, p))
        return points

    def _points_picked(self, key, points):
        from incad.features import revolve
        self._point_key = None
        if self._closed or self.dialog is None:
            return
        if self.dialog.field_def(key).pick in ("three_points", "axis_points", "marked_point"):
            from incad.features import work
            text = work.format_point_picks(self.body, self._pick_base(), points)
        else:
            text = revolve.format_points(points)
        self.dialog.set_pick(key, text)  # emits changed: the preview follows
        self.dialog.set_active_pick(None)

    def _points_cancelled(self, key):
        self._point_key = None
        if self._closed or self.dialog is None:
            return
        if self.dialog.active_pick() == key:
            self.dialog.set_active_pick(None)

    def _sync_ghost(self, on):
        """The result drawn over the part while its edges / faces are picked: the feature's edges as outlines in
        the accent colour, never pickable (clicks go to the part underneath)."""
        try:
            import FreeCADGui as Gui
            from pivy import coin
        except ImportError:
            return
        old = getattr(self, "_ghost", None)
        if old is not None:
            view, node = old
            try:
                view.getSceneGraph().removeChild(node)
            except Exception:
                pass
            self._ghost = None
        f = self.feature
        if not on or f is None or not _feature_alive(f) or "Invalid" in f.State or f.Shape.isNull():
            return
        try:
            view = Gui.getDocument(self.doc_name).ActiveView
            group = f.getParentGeoFeatureGroup()
            pl = group.getGlobalPlacement() if group is not None else None
            base = self._expected_base()
            same = base.Shape if base is not None and not base.Shape.isNull() else None
            lines = []
            seen = set()
            lift = max(1e-3, f.Shape.BoundBox.DiagonalLength * 5e-4)  # off the surface it lies on: no dashes
            for face in _new_faces(f.Shape, same):  # the round, the chamfer, the moved face — not the edges they
                for e in face.Edges:                  # only trimmed (an ellipse's seam on its side face)
                    key = _edge_key(e)
                    if key in seen or _is_seam(e, face):  # a closed surface's own seam is no edge to show
                        continue
                    seen.add(key)
                    try:
                        straight = type(e.Curve).__name__ == "Line"
                    except Exception:  # "undefined curve type" (some OCC curves have no Python type)
                        straight = False
                    try:
                        pts = e.discretize(2 if straight else 48)
                    except Exception:  # "Discretization of edge failed" (seen on Windows): its ends at least
                        pts = [v.Point for v in e.Vertexes]
                    if len(pts) > 1:
                        pts = _lifted(face, pts, lift)
                        lines.append([pl.multVec(p) if pl is not None else p for p in pts])
            if not lines:
                return
            node = coin.SoSeparator()
            style = coin.SoPickStyle()
            style.style = coin.SoPickStyle.UNPICKABLE
            color = coin.SoBaseColor()
            color.rgb.setValue(0.95, 0.62, 0.29)
            draw = coin.SoDrawStyle()
            draw.lineWidth = 2
            coords = coin.SoCoordinate3()
            flat = [p for line in lines for p in line]
            coords.point.setValues(0, len(flat), [(p.x, p.y, p.z) for p in flat])
            lineset = coin.SoLineSet()
            lineset.numVertices.setValues(0, len(lines), [len(line) for line in lines])
            for n in (style, color, draw, coords, lineset):
                node.addChild(n)
            top = coin.SoSeparator()  # depth-tested: the far side of a ring stays hidden behind the part (the
            top.addChild(node)        # lines lie on its faces, which FreeCAD draws pushed back: the near side shows)
            view.getSceneGraph().addChild(top)
            self._ghost = (view, top)
        except Exception:
            log.logger().exception("%s preview outline", self.kind.TITLE)

    def _show_base(self, on):
        if not on:
            self._sync_ghost(False)
            for o, visible in getattr(self, "_saved_visibility", []):
                self._set_visible(o, visible)
            self._saved_visibility = []
            self._base_shown = None

    def _picks_edited(self, key):
        """A section removed or moved in the dialog's list: the 3D selection follows, so the next click merges
        with what the list shows."""
        from incad.features import common
        try:
            self._reselect(common.parse_picks(self.doc, self.dialog.values().get(key)))
        except Exception:
            log.logger().exception("reselect after a list edit")

    def _collect_picks(self, key, clicked):
        """A plain click adds the edge/face to the ones already picked, and a plain click on a picked one takes it
        off again (Inventor); with Cmd/Ctrl/Shift held, or the first pick, the selection is the whole set (a Cmd-click
        already adds or removes in FreeCAD's own selection). The 3D selection is kept equal to the picks."""
        from incad.features import common
        held = QtWidgets.QApplication.keyboardModifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier
                                                             | QtCore.Qt.MetaModifier)
        current = common.parse_picks(self.doc, self.dialog.values().get(key))
        auto = getattr(self, "_auto_picks", set())
        if key in auto:  # what the dialog put there by default is replaced by the first pick, never kept beside it
            auto.discard(key)
            clicked = [p for p in clicked if (p[0].Name, p[1]) not in {(o.Name, s) for o, s in current}] or clicked
            self._reselect(clicked)
            return clicked
        if held or not current:
            return clicked
        mine = {(o.Name, s) for o, s in current}
        if len(clicked) == 1 and (clicked[0][0].Name, clicked[0][1]) in mine:
            merged = [p for p in current if (p[0].Name, p[1]) != (clicked[0][0].Name, clicked[0][1])]
        else:
            merged = current + [p for p in clicked if (p[0].Name, p[1]) not in mine]
        self._reselect(merged)
        return merged

    def unpick_under_cursor(self):
        """A plain click on an edge/face that is already picked takes it off. FreeCAD sends no selection event
        for that click (it keeps the selected set), so the shell asks here first. True = the click is used."""
        import FreeCADGui as Gui
        from incad.features import common
        key = self.dialog.active_pick() if self.dialog is not None else None
        if not key or self.dialog.field_def(key).kind != "picks" or key in getattr(self, "_auto_picks", set()):
            return False
        if QtWidgets.QApplication.keyboardModifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier
                                                         | QtCore.Qt.MetaModifier):
            return False
        try:
            pre = Gui.Selection.getPreselection()
            subs = [x for x in (getattr(pre, "SubElementNames", ()) or ()) if x]
            under = common.resolve(pre.Object, subs[0]) if pre.Object is not None and subs else (None, "")
            if self.dialog.field_def(key).pick == "feature" and under[0] is not None:
                under = (common.feature_of(*under), "")
        except Exception as e:
            log.logger().info("unpick: no preselection (%s)", e)
            return False
        current = common.parse_picks(self.doc, self.dialog.values().get(key))
        mine = (getattr(under[0], "Name", None), under[1])
        if mine[0] is not None and mine[1].startswith("Face") and self.dialog.field_def(key).pick == "edge" \
                and getattr(self.kind, "FACE_EDGES", False):
            gone = {(o.Name, s) for o, s in self._tangent_chains(self._face_edges([under]))}
            if not gone or not gone <= {(o.Name, s) for o, s in current}:
                return False  # not all picked yet: the click picks them (the selection event)
            merged = [p for p in current if (p[0].Name, p[1]) not in gone]
            self.dialog.set_pick(key, common.format_picks(merged))
            self._reselect(merged)
            return True
        if mine[0] is None or not mine[1] or mine not in {(o.Name, s) for o, s in current}:
            return False
        gone = {mine}
        if self.dialog.field_def(key).pick in ("sketch_geometry", "sketch_path"):  # the whole outline comes off
            gone = {(o.Name, s) for o, s in self._whole_outlines([under])}
        elif self.dialog.field_def(key).pick == "edge" and getattr(self.kind, "TANGENT_CHAIN", False):
            gone = {(o.Name, s) for o, s in self._tangent_chains([under])}
        merged = [p for p in current if (p[0].Name, p[1]) not in gone]
        self.dialog.set_pick(key, common.format_picks(merged))
        self._reselect(merged)
        return True

    def _reselect(self, pairs):
        """The 3D selection set to `pairs`. Its events come back later (timers) and are no click: each reselect holds
        the guard until its own events are through — two in one pass ended it early and the second's events picked
        again, reselecting forever (a hang on a big part)."""
        import FreeCADGui as Gui
        want = {(obj.Name, sub) for obj, sub in pairs}
        try:
            have = {(x.ObjectName, s) for x in Gui.Selection.getSelectionEx(self.doc_name)
                    for s in (x.SubElementNames or ("",))}
        except Exception:
            have = None
        if have == want:
            return
        self._seeding += 1
        try:
            Gui.Selection.clearSelection()
            for obj, sub in pairs:
                Gui.Selection.addSelection(self.doc_name, obj.Name, sub)
        finally:
            QtCore.QTimer.singleShot(0, self._end_seeding)

    def _seed_selection(self, key):
        """A picks field that becomes active shows its current items selected, so a click adds or removes one."""
        import FreeCADGui as Gui
        from incad.features import common
        field = self.dialog.field_def(key)
        if field.kind != "picks":
            return
        self._seeding += 1
        try:
            Gui.Selection.clearSelection()
            for obj, sub in common.parse_picks(self.doc, self.dialog.values().get(key)):
                Gui.Selection.addSelection(self.doc_name, obj.Name, sub)
        finally:
            QtCore.QTimer.singleShot(0, self._end_seeding)

    def _end_seeding(self):
        self._seeding = max(0, self._seeding - 1)

    @staticmethod
    def _set_visible(obj, visible):
        try:
            obj.ViewObject.Visibility = visible
        except Exception:
            pass

    def _label_params(self):
        target = self.feature if self.feature is not None else (
            self.profile if getattr(self.kind, "IN_SKETCH", False) else None)
        extra = self.kind.param_names(target) if hasattr(self.kind, "param_names") and target is not None else {}
        for key, name in extra.items():
            self.dialog.set_param_name(key, name)
        for f in self.kind.FIELDS:
            if f.kind.startswith("value"):
                prop = getattr(self.kind, "PROPS", {}).get(f.key)
                if prop and self.feature is not None and f.key not in extra:  # the kind's own answer stands
                    target = self.kind._pad_of(self.feature) if hasattr(self.kind, "_pad_of") else self.feature
                    self.dialog.set_param_name(f.key, model.bound_parameter(target, prop) if target else None)

    def _sync_choices(self, values):
        """Choices the kind says are not available with these values are greyed out (kind.unavailable)."""
        fn = getattr(self.kind, "unavailable", None)
        if fn is None or self.dialog is None:
            return
        try:
            off = fn(values)
        except Exception:
            return
        for key, values_off in off.items():
            w = self.dialog._widgets.get(key)
            choices = list(getattr(w, "_buttons", None) or {})
            for value in choices:
                self.dialog.set_choice_enabled(key, value, value not in values_off)
            current = values.get(key)
            if current in values_off:  # never a greyed-out choice: the next one that is available
                later = choices[choices.index(current) + 1:] + choices[:choices.index(current)]
                ok = [v for v in later if v not in values_off]
                if ok:
                    self.dialog.set_values({key: ok[0]})

    def _follow(self, values):
        """Values that follow a change the user made (kind.follow: Thread's clearance follows its standard)."""
        hook = getattr(self.kind, "follow", None)
        before = getattr(self, "_seen", None)
        self._seen = dict(values)  # first: setting the followers below may report a change again
        if hook is not None and before is not None and self.dialog is not None:
            try:
                more = hook(before, values)
            except Exception as e:
                log.logger().warning("%s follow: %s", getattr(self.kind, "NAME", "dialog"), e)
                more = {}
            if more:
                self.dialog.set_values(more)
                values = self.dialog.values()
        self._seen = dict(values)
        return values

    def _changed(self, values, throttle=False):
        """throttle: a value that keeps changing (a drag) is previewed every interval, not only once it rests."""
        if self._closed or self.dialog is None:
            return  # a change reported while (or after) the dialog closed: nothing to preview
        values = self._follow(values)
        self._sync_choices(values)
        if self.dialog is None:
            return
        self._activate_revealed_pick()
        if self.dialog is None:
            return
        self._refresh_options()
        self._show_info()
        values = self.dialog.values() if self.dialog is not None else values
        if self._live_only(values):
            return  # only how it looks (an image's transparency): set at once, no preview, no recompute
        self._pending = values
        if not (throttle and self._timer.isActive()):
            self._timer.start()
        self._sync_sketch_markers()

    def _live_only(self, values):
        """A change of the kind's LIVE values alone (Scale / Transparency's slider): kind.live sets it on the feature
        at once — a preview recomputed the image, which reloaded its file at every step of the slider (the user: it
        jerked). True when that was all."""
        live = getattr(self.kind, "LIVE", ())
        applied = self._applied or {}
        if not live or self.feature is None or self._pending is not None or not _feature_alive(self.feature):
            return False
        changed = {k for k in set(values) | set(applied) if values.get(k) != applied.get(k)}
        if not changed or not changed <= set(live):
            return False
        try:
            self.kind.live(self.doc, self.feature, values)
        except Exception:
            log.logger().exception("%s live change", self.kind.TITLE)
            return False
        self._applied = dict(applied, **{k: values.get(k) for k in live})
        return True

    def _activate_revealed_pick(self):
        """A choice that brings pick fields into view (Extrude's To face, Work Axis ▸ Two points) makes the first of
        them the active pick, so the next click fills it — markers to click light up at once (the user: Two points
        showed no points). A pick the choice hid is never left active: its clicks went to a field out of sight."""
        waiting = self._waiting_picks()
        revealed = [k for k in waiting if k not in getattr(self, "_seen_waiting", waiting)]
        self._seen_waiting = waiting
        active = self.dialog.active_pick()
        hidden = active is not None and not self._shown(self.dialog.field_def(active))
        if revealed and (active not in waiting or hidden):
            self.dialog.set_active_pick(revealed[0])
        elif hidden:
            self.dialog.set_active_pick(None)
        hook = getattr(self.kind, "next_pick", None)  # a choice that makes a pick with a default the next click's
        if hook is not None and self.dialog is not None:  # (a pattern's Solid: its axis, as in a professional CAD)
            key = hook(self.dialog.values())
            if key != getattr(self, "_next_pick_seen", None):
                self._next_pick_seen = key
                if key and self.dialog.active_pick() is None and self._shown(self.dialog.field_def(key)):
                    self.dialog.set_active_pick(key)

    def _picked_sketches(self):
        """The sketches named in the dialog's `sketch` fields (Sweep's profile and path, Loft's sections)."""
        from incad.features import common
        picked = []
        if self.dialog is None:
            return picked
        for f in self.kind.FIELDS:
            if getattr(f, "pick", None) in ("sketch", "path"):
                for obj, _ in common.parse_picks(self.doc, self.dialog.values().get(f.key)):
                    if obj.TypeId == "Sketcher::SketchObject" and obj not in picked:
                        picked.append(obj)
        return picked

    def _sync_sketch_markers(self):
        """The sketches picked in `sketch` fields are shown with their key points while the dialog is open."""
        import FreeCADGui as Gui
        from incad.core import sketch as sketches
        from incad.ui.point_markers import PointMarkers
        if self._closed or self.dialog is None:
            return
        picked = self._picked_sketches()
        for sk in picked:  # FreeCAD hides a sketch once a Pipe/Loft takes it (its view provider does): show it
            if not sk.Visibility:  # again after every preview, hidden back when the dialog closes
                if sk not in self._saved_sketches:
                    self._saved_sketches.append(sk)
                self._set_visible(sk, True)
        points = []
        for sk in picked:
            try:
                points.extend(sketches.key_points(sk, projected=True))  # (building commands pick on them)
            except Exception:
                pass
        if not points:
            if self._markers is not None:
                self._markers.clear()
            return
        try:
            if self._markers is None:
                gdoc = Gui.getDocument(self.doc_name)
                view = gdoc.ActiveView if gdoc is not None else None
                if view is None:
                    return
                self._markers = PointMarkers(view, self._theme)
            self._markers.set(points)
        except Exception as e:
            log.logger().warning("Dialog markers: %s", e)

    @perf.timed("feature preview")
    def _preview(self):
        if self._pending is None or self._closed:
            return
        if self._previewing:
            self._timer.start()  # FreeCAD runs the event loop while it computes: a value changed meanwhile is
            return               # previewed after this one, never re-entered
        self._previewing = True
        start = time.perf_counter()
        try:
            self._preview_now()
            took = time.perf_counter() - start
            if took > 1.0:  # which dialog and feature (the log said only "feature preview took 21012 ms")
                try:
                    label = self.feature.Label if self.feature is not None else "-"
                except Exception:
                    label = "-"
                log.logger().info("Slow preview: %s %s, %.1f s", self.kind.TITLE, label, took)
        except Exception as e:  # nothing escapes into the timer slot; the dialog says what went wrong
            log.report("warning", "%s preview: %s" % (self.kind.TITLE, e), traceback.format_exc())
            if self.dialog is not None:
                self.dialog.set_error(str(e))
        finally:
            self._previewing = False
            after, self._after_preview = self._after_preview, None  # never kept for a later preview
        if after and not self._closed:  # OK / Apply / Cancel pressed while the preview computed
            getattr(self, after)()

    def _preview_now(self):
        safe.watch_esc()  # Esc from now on stops a long preview (the user: Escape must cancel any operation)
        values, self._pending = self._pending, None
        self._applied = values
        try:
            if self.creating and (self.feature is None or self._needs_recreate(values)):
                # roll back every document, then open all the undo steps again before changing anything
                # (opening a step in one more document commits what is pending in the others)
                self._abort_step()
                self.feature = None  # the abort removed it (before anything below can raise)
                self._abort_extras(reopen=False)
                _recompute_sound(self.doc)
                model.relink(self.doc)
                self._join_documents(values)
                self.doc.openTransaction(self.kind.TITLE)
                self.feature = None  # the abort removed it; stays None if the new one fails
            if self.feature is None:
                self._retarget_from_values(values)
                if self.profile is not None:
                    document.adopt(self.body, self.profile)
                self.feature = self.kind.create(self.doc, self.body, self.profile, values)
                self._created_with = values.get("operation")
                self._created_values = dict(values)
            else:
                self.feature = self.kind.update(self.doc, self.feature, values)
            self._recompute()
            self._before_thread()
        except Exception as e:
            self._recover_deleted_feature()  # a type-swapping update() may have deleted it then failed
            if self.creating and self.feature is None:
                self._drop_partial()
            # nothing made yet while the user is still picking: the reason is a hint, not an error
            values = self.dialog.values()
            empty = bool(self._waiting_picks())
            waiting = self.creating and self.feature is None and (self.dialog.active_pick() is not None or empty)
            self.dialog.set_error(str(e), waiting=waiting)
            return
        self._check()
        if self.feature is not None and _feature_alive(self.feature):
            document.show_finished(self.doc, [self.feature])  # the hole's chamfer step shows, not the bare hole
        self._label_params()
        self._fill_empty_picks()
        self._show_adjusted()
        self._show_summary()
        self._show_info()  # what the values made, once they made it
        self._sync_sketch_markers()
        self._sync_axis_line()
        if getattr(self.kind, "IN_SKETCH", False):  # the sketch's Text outlines follow the words as they are typed
            try:
                from incad.ui import shell as shells
                sh = shells.instance()
                if sh is not None:
                    sh.sketches._sync_text_lines(sh.sketches.editing())
            except Exception:
                log.logger().exception("text outlines after a preview")
        key = self.dialog.active_pick()
        if self._base_shown is not None:  # re-apply: the recompute (or a re-create) may have shown the preview
            self._show_base(False)
            self._sync_pick_view()
        elif key and self.dialog.field_def(key).pick in self._ON_BASE:
            self._sync_pick_view()  # the first pick just made the feature: show the base again for the next pick
        if self.on_previewed is not None:
            try:
                self.on_previewed()
            except Exception:
                log.logger().exception("%s overlay", self.kind.TITLE)

    def _before_thread(self):
        """A cut or join through a modeled thread breaks in the kernel: it goes before the thread, which is made after
        it (incad.core.thread_order; the user's slot revolved over a tapped Hole)."""
        if self.feature is None or not _feature_alive(self.feature) or self._extra:
            return
        from incad.core import thread_order
        thread = thread_order.settle(self.feature)
        if thread:
            self._moved_before_thread = True
            log.report("info", "%s crosses %s's thread, which FreeCAD can't cut or join through: placed before %s, "
                       "the thread is made after it" % (self.feature.Label, thread, thread))
            self._recompute()

    def _later(self):
        """The solid features after this one in its body, its own helpers aside (a new feature at the end: none).
        A sketch's own dialog and a change in an assembly's context compute everything as before."""
        f, body = self.feature, self.body
        if f is None or body is None or self._extra or getattr(self.kind, "IN_SKETCH", False):
            return []
        try:
            group = list(body.Group)
            if f not in group:
                return []
            mine = set(document.helpers_of(body, f)) | set(self._companions())
            for c in self._companions():
                mine |= set(document.helpers_of(body, c))
            return [o for o in group[group.index(f) + 1:] if o not in mine and o.isDerivedFrom("PartDesign::Feature")]
        except Exception:
            return []

    def _recompute(self):
        """The preview. Inventor rolls the part back to the feature being edited: only it (with its helpers and what
        it is built on) is computed while its dialog is open, and the part is shown as of this feature; the features
        after it are computed once, on OK (an early feature of a big part recomputed the whole part, 16 s, for every
        value typed)."""
        if getattr(self.kind, "IN_SKETCH", False) and not self._extra:
            # Text, sketch patterns: the sketch and the object only; the part follows on Finish Sketch (or on OK when
            # the sketch is not open) — a whole recompute per value took 20-27 s on a slow PC
            own = [o for o in (self.feature, self.profile) if o is not None and _feature_alive(o)]
            if own:
                if self._rolled is None:
                    self._rolled = []
                watch = _Recomputed()
                import FreeCAD as App
                App.addDocumentObserver(watch)
                try:
                    self.doc.recompute(own)
                finally:
                    App.removeDocumentObserver(watch)
                self._recomputed = watch.names | {o.Name for o in own}
                return
        later = self._later()
        own = self._own()
        if not later and own and not self._extra and self._rolled is None:
            # a new feature (or the last one): it alone, the rest once on OK — a full recompute per preview also redid
            # every broken feature after the End of Part (the user's jar: 8 s a Work Plane preview)
            self._rolled = []
        if later and self._rolled is None:
            self._rolled = [(o, bool(o.Visibility)) for o in own + later]
            for o in later:
                if o.Visibility:
                    self._set_visible(o, False)
            self._set_visible(self.feature, True)  # show_finished then shows its last helper instead, if any
        faceted.skip_refine(own or [self.feature])
        watch = _Recomputed()
        import FreeCAD as App
        App.addDocumentObserver(watch)
        try:
            self.doc.recompute(own) if own and not self._extra else self.doc.recompute()
        finally:
            App.removeDocumentObserver(watch)
        if self._extra:
            self._recomputed = {o.Name for o in self.doc.Objects}  # as before: every shape checked
        else:
            self._recomputed = watch.names | {o.Name for o in own}

    def _sketch_open(self):
        """The dialog's sketch is open for editing: its part follows on Finish Sketch."""
        try:
            import FreeCADGui as Gui
            edit = Gui.ActiveDocument.getInEdit() if Gui.ActiveDocument is not None else None
            return edit is not None and self.profile is not None and edit.Object is self.profile
        except Exception:
            return False

    def _own(self):
        """The feature with its helpers (a hole's chamfer, a thread's fill)."""
        if self.feature is None or not _feature_alive(self.feature):
            return []
        out = [self.feature] + (document.helpers_of(self.body, self.feature) if self.body is not None else [])
        for c in self._companions():  # Thread on several faces: the others are made and shown with it
            out += [c] + (document.helpers_of(self.body, c) if self.body is not None else [])
        return out

    def _companions(self):
        hook = getattr(self.kind, "companions", None)
        if hook is None or self.feature is None or not _feature_alive(self.feature):
            return []
        try:
            return hook(self.doc, self.feature)
        except Exception:
            log.logger().exception("companions")
            return []

    def _roll_forward(self, commit):
        """The dialog closes: on OK the features after this one are computed (once), and whatever that broke is
        said; the view is put back as it was."""
        saved, self._rolled = self._rolled, None
        if saved is None:
            return
        if commit and not self._sketch_open():
            self.doc.recompute()
            if safe.repair_links(self.doc, getattr(self, "_links", {})):  # a fillet lost its edges to the new size
                self.doc.recompute()
            # every feature's state, but only the finished part's geometry: checking each later shape took 11 s
            tip = getattr(self.body, "Tip", None)
            found = safe.problems(self.doc, self._broken, {tip.Name} if tip is not None else set())
            if found:
                log.report("warning", "After %s: %s" % (self.kind.TITLE, "; ".join(found)))
        for o, visible in saved:
            if _feature_alive(o):
                self._set_visible(o, visible)
        if commit and getattr(self, "_moved_before_thread", False) and self.body is not None:
            document.show_result(self.body)  # it was the last step when the view was saved: the part is now

    def _show_adjusted(self):
        """A kind may change a value itself (Move Face: a chamfer picked with its face means Together): the dialog
        shows what the feature now uses."""
        hook = getattr(self.kind, "adjusted", None)
        if hook is None or self.feature is None or not _feature_alive(self.feature):
            return
        try:
            current = self.dialog.values()
            diff = {k: v for k, v in hook(self.doc, self.feature).items() if current.get(k) != v}
            if diff:
                self.dialog.set_values(diff)
        except Exception:
            log.logger().exception("adjusted values")

    def _show_info(self):
        """A kind may say what the values make (Gear: its teeth and diameters) under the fields."""
        hook = getattr(self.kind, "info", None)
        if hook is None or self.dialog is None:
            return
        try:
            self.dialog.set_info(hook(self.dialog.values()) or "")
        except Exception:
            log.logger().exception("dialog info")

    def _show_summary(self):
        """A kind may describe what its pick fields now stand for (names, dependents) beyond the picks themselves."""
        summary = getattr(self.kind, "summary", None)
        if summary is None or self.feature is None or not _feature_alive(self.feature):
            return
        try:
            for key, text in summary(self.doc, self.feature).items():
                if self.dialog.values().get(key):
                    self.dialog.set_pick_text(key, text)
        except Exception:
            log.logger().exception("pick summary")

    def _fill_empty_picks(self):
        """Show what a default picked for an empty field (e.g. the feature a pattern repeats)."""
        if self.feature is None or getattr(self.kind, "IN_SKETCH", False):
            return
        try:
            read = self.kind.read(self.doc, self.feature)
        except Exception:
            return
        current = self.dialog.values()
        fill = {f.key: read[f.key] for f in self.kind.FIELDS
                if f.kind in ("pick", "picks") and not current.get(f.key) and read.get(f.key)}
        if fill:
            self.dialog.set_values(fill)
            self._auto_picks = getattr(self, "_auto_picks", set()) | {k for k in fill
                                                                       if self.dialog.field_def(k).kind == "picks"}

    def _abort_step(self):
        """Roll the dialog's step back. The abort takes the dialog's new value out of the model's values, and FreeCAD
        then touches every object that uses ANY value: a part with a modeled thread was recomputed whole, 6 s, at
        a change of Join to Cut and at Cancel (the user: "Extrude Cut lags"). An object the dialog neither changed
        nor computed has its shape of before the dialog, which the abort's inputs are again: it is left untouched, and
        the values are recomputed by themselves (touched, they took every object using one with them).
        A feature put before a thread moved the thread: then everything is computed as before."""
        before = {o.Name for o in self.doc.Objects if "Touched" in o.State}
        bases = safe.dressup_bases(self.doc, [self.feature.Name]) if _feature_alive(self.feature) else set()
        values = model._model_obj(self.doc)
        if values is not None and not getattr(self, "_moved_before_thread", False):
            with model.changing_values(self.doc, values):  # the values recomputed alone, their users left as they are
                self.doc.abortTransaction()
        else:
            self.doc.abortTransaction()
        safe.heal_bases(self.doc, bases)  # a refused fillet left the shape it was built on invalid
        computed = getattr(self, "_computed", None)
        if computed is None or getattr(self, "_moved_before_thread", False):
            return
        for o in self.doc.Objects:
            if "Touched" in o.State and o.Name not in before and o.Name not in computed.names:
                try:
                    o.purgeTouched()
                except Exception:
                    pass

    def _recover_deleted_feature(self):
        """An update() that swaps a feature's type (Join<->Cut) deletes the old one, then may fail before it
        returns the new one — self.feature is left pointing at a deleted object and the next preview would crash.
        Put it back: None while creating (the next preview recreates), or the restored original while editing."""
        if self.feature is None or _feature_alive(self.feature):
            return
        if self.creating:
            self.feature = None
            return
        try:
            self.doc.abortTransaction()
            self.doc.recompute()
            model.relink(self.doc)
            self.doc.openTransaction(self.kind.TITLE)
            self._abort_extras(reopen=True)
        except Exception as ex:
            log.report("warning", "Could not roll back the unfinished %s: %s" % (self.kind.TITLE, ex))
        self.feature = self.doc.getObject(self._feature_name) if self._feature_name else None

    def _drop_partial(self):
        """A create that failed half-way may have left objects behind: roll them back, keep the step open."""
        try:
            self.doc.abortTransaction()
            self.doc.recompute()
            model.relink(self.doc)
            self.doc.openTransaction(self.kind.TITLE)
            self._abort_extras(reopen=True)
        except Exception as e:
            log.report("warning", "Could not roll back the unfinished %s: %s" % (self.kind.TITLE, e))

    def _join_documents(self, values):
        """Open this dialog's undo step in the other documents it changes (e.g. the part an assembly cut edits)."""
        hook = getattr(self.kind, "documents", None)
        if hook is None:
            return
        for d in hook(self.doc, values):
            if d is self.doc or d in self._extra:
                continue
            if not d.UndoMode:
                d.UndoMode = 1
            if d.HasPendingTransaction:
                d.abortTransaction()  # an empty step FreeCAD pre-opened for linked documents
            d.openTransaction(self.kind.TITLE)
            self._extra.append(d)

    def _abort_extras(self, reopen):
        for d in list(self._extra):
            try:
                d.abortTransaction()
                d.recompute()
                model.relink(d)
                if reopen:
                    d.openTransaction(self.kind.TITLE)
            except Exception as e:
                log.report("warning", "Could not roll back %s: %s" % (d.Label, e))
        if not reopen:
            self._extra = []

    def _needs_recreate(self, values):
        """While creating, Join/Cut switch in place; New Solid and Intersect build a different structure."""
        made = getattr(self, "_created_values", {})
        if any(values.get(k) != made.get(k) for k in getattr(self.kind, "RECREATE_ON", ())):
            return True
        if self._pick_in_another_solid(values):
            return True
        before, after = getattr(self, "_created_with", None), values.get("operation")
        if after in ("join", "auto") and values != made and _feature_alive(self.feature) \
                and self.feature.getParentGeoFeatureGroup() is not self.body:
            # a Join first made off the part (its default length did not reach) went into a Solid of its own: with
            # any change (To a face of the part) it is made again, and joins the part when it reaches it now (the
            # user: "extruded To, the part is not a single part; a Boolean was needed")
            return True
        if before == after:
            return False
        structural = getattr(self.kind, "STRUCTURAL", ("new_solid", "intersect"))
        if before in structural or after in structural:
            return True
        # a Join that did not touch the part was made as its own Solid: swapped in place, Cut cut only that empty
        # Solid (the user: Cut did nothing the first time); and a Join from a Cut must be able to go there
        owner = self.feature.getParentGeoFeatureGroup() if _feature_alive(self.feature) else None
        return owner is not self.body or after == "join"

    def _pick_in_another_solid(self, values):
        """The kind's feature belongs where its pick is (kind.BODY_FROM: Emboss on the picked face's Solid, its
        profile coming along): made before that pick, in the profile's own Solid, it is made again there."""
        from incad.features import common
        key = getattr(self.kind, "BODY_FROM", None)
        if not key or not values.get(key):
            return False
        for obj, _ in common.parse_picks(self.doc, values.get(key)):
            owner = obj.getParentGeoFeatureGroup() if hasattr(obj, "getParentGeoFeatureGroup") else None
            if owner is not None and owner.TypeId == "PartDesign::Body":
                return owner is not self.body
        return False

    def _check(self):
        """What is wrong now. Shapes are checked only where the last preview computed (the feature when the dialog
        opens): every shape of a big part took 11 s, after every value typed."""
        touched = self._recomputed if self._recomputed is not None else {o.Name for o in self._own()}
        found = safe.problems(self.doc, self._broken, touched, deep=False)  # the full check once, on OK
        if found and _feature_alive(self.feature):  # the next value tried must not start from a spoilt base
            safe.heal_bases(self.doc, safe.dressup_bases(self.doc, [self.feature.Name]))
        text = "; ".join(found)
        rescue = None
        offer = getattr(self.kind, "rescue", None)
        if text and offer is not None:
            try:
                plan = offer(self.doc, self.body, self.dialog.values(), text)  # (label, callable) or None
            except Exception:
                plan = None
            if plan is not None:
                rescue = (plan[0], lambda fn=plan[1], label=plan[0]: self._rescue(label, fn))
        hint = getattr(self.kind, "pending", None)
        hint = hint(self.dialog.values()) if hint is not None and not text else ""
        self.dialog.set_error(text or hint, rescue, waiting=bool(hint))
        self._show_base_while_broken(bool(found))

    def _rescue(self, label, fn):
        """The kind's way around the failure: the failed attempt is dropped (as Cancel), the rescue runs as its
        own undo step, and the status line says what was done or why it could not be."""
        doc = self.doc
        self.cancel()
        r = safe.run_safely(self.kind.TITLE, fn, doc)
        if r.ok:
            note = getattr(r.value, "InCadRescueNote", None) if r.value is not None else None
            log.report("info", note or "%s: done (%s)" % (self.kind.TITLE, label))
        else:
            log.report("warning", r.error)
        from incad.ui import shell as shells
        inst = shells.instance()
        if inst is not None:
            inst.schedule_refresh()

    def _show_base_while_broken(self, broken):
        """A preview that failed shows nothing: keep the part (the shape the feature starts from) on screen until the
        values work again, then show the preview. A geometry pick already shows the base itself."""
        if self._base_shown is not None or self.feature is None or not _feature_alive(self.feature):
            return
        if getattr(self.kind, "IN_SKETCH", False):
            return
        base = getattr(self.feature, "BaseFeature", None)
        if broken and base is not None and base is not getattr(self, "_error_base", None):
            self._error_base = base
            self._set_visible(base, True)
            self._set_visible(self.feature, False)
        elif not broken and getattr(self, "_error_base", None) is not None:
            base, self._error_base = self._error_base, None
            self._set_visible(self.feature, True)
            self._set_visible(base, False)

    def undo_in_dialog(self, redo=False):
        """Undo/Redo pressed while this dialog is open: step through the changes made in the dialog (the model's
        own undo waits until the dialog is finished or cancelled)."""
        if self.dialog is None:
            return
        done = self.dialog.redo_change() if redo else self.dialog.undo_change()
        if done or redo:
            return
        log.report("info", "%s dropped: Undo went past the values it opened with" % self.kind.TITLE)
        self.cancel()  # Undo never refuses: past the floor it drops the dialog, as Cancel would; the next Undo
        #                is the model's own

    def insert(self, name):
        field = self.dialog.focused_value_field() if self.dialog is not None else None
        if field is not None:
            field.insert(name)
            return True
        return False

    def ok(self):
        """Commit what the dialog shows now: a value typed but not yet previewed (Enter, or a click on OK that
        doesn't take focus on macOS) is applied first."""
        if self._previewing:
            self._after_preview = "ok"  # taken when the preview (seconds for a thread) is done
            return
        self._timer.stop()
        if self.dialog is not None and self.dialog.active_pick() is not None:
            self.dialog.set_active_pick(None)  # picking ends (Split's Keep shows only the kept pieces again)
        if self.dialog is not None:
            current = self.dialog.values()
            if self._pending is not None or current != self._applied:
                self._pending = current
                self._preview()
        if self.feature is None and self.dialog is not None and not self.dialog.error_text():
            self.dialog.set_error("Nothing to create yet")
        if self.dialog is not None and self.dialog.error_text():
            return
        built = getattr(self.kind, "before_ok", None)  # a preview drawn, not built (a pattern's outlines): built now
        if built is not None and self.feature is not None and _feature_alive(self.feature):
            try:
                built(self.doc, self.feature)
                self._recompute()
            except Exception as e:
                self.dialog.set_error(str(e))
                return
        if self._recomputed is not None and self.dialog is not None:  # the full geometry check the previews left out
            found = safe.problems(self.doc, self._broken, self._recomputed)
            if found:
                self.dialog.set_error("; ".join(found))
                return
        try:
            weak = safe.unsound(self.feature) if _feature_alive(self.feature) else ""
        except Exception:
            weak = ""
        if weak:
            log.report("warning", weak)  # kept (the user may mean it), but said: it fails later otherwise
        self._finish(commit=True)

    def apply(self):
        if self._previewing:
            self._after_preview = "apply"
            return
        kind = self.kind
        carried = _carried(kind, self.dialog.values()) if self.dialog is not None else {}
        self.ok()
        # once, and only after a commit: a second Apply that landed during the preview is folded in, and an Esc
        # that landed there means stop (Inventor), not "and now the next one"
        if self._closed and self._committed and not self._again:
            self._again = True
            from incad.ui import shell
            try:
                import FreeCADGui as Gui
                Gui.Selection.clearSelection()  # the next round must not pick the profile just used
            except Exception:
                pass
            def again():
                _AGAIN[getattr(kind, "__name__", "")] = carried
                try:
                    shell.start_feature(kind)
                except Exception as e:  # e.g. no closed sketch left: say so instead of a silent traceback
                    log.report("warning", "%s: %s" % (kind.TITLE, e))
                finally:
                    _AGAIN.pop(getattr(kind, "__name__", ""), None)  # never into a later, fresh dialog
            QtCore.QTimer.singleShot(0, again)

    def cancel(self):
        if self._previewing:
            self._after_preview = "cancel"
            return
        self._timer.stop()
        self._finish(commit=False)

    def _hide_used_sketches(self):
        """Inventor: a sketch the new feature uses is hidden, even one the user showed by hand before (they may
        show it again afterwards). Inside the feature's undo step, so undo gives the shown sketch back."""
        from incad.core import document
        from incad.features import common
        if self.feature is None or not _feature_alive(self.feature):
            return
        for sk in self.doc.Objects:
            text = getattr(sk, "InCadText", None) is not None  # a Text an Extrude used: its letters hidden too
            if (not document.is_sketch_like(sk) and not text) or not sk.Visibility and \
                    not getattr(sk, document.SHOWN_PROP, False):
                continue
            try:
                if self.feature in common.consumers(sk):  # shown by hand or never chosen: hidden either way (its
                    if getattr(sk, document.SHOWN_PROP, False):  # lines lay on the new edges and took the clicks)
                        setattr(sk, document.SHOWN_PROP, False)
                    self._set_visible(sk, False)
            except Exception:
                log.logger().exception("hide %s", sk.Name)

    def _finish(self, commit):
        if self._closed:
            return
        self._closed = True
        self.finishing = True  # until the dialog is gone: the commit's recompute (seconds on an STL's triangles)
        try:                   # lets the panel refresh, which took this session for a lost one and freed "busy"
            self._finish_steps(commit)
        except Exception as e:  # whatever failed, the dialog goes: left open it ignored every button (closed)
            log.report("warning", "%s: closing the dialog: %s" % (self.kind.TITLE, e), traceback.format_exc())
        finally:
            safe.set_busy(None)
            if getattr(self, "_computed", None) is not None:
                try:
                    import FreeCAD as App
                    App.removeDocumentObserver(self._computed)
                except Exception:
                    pass
                self._computed = None
            if self.dialog is not None:
                try:
                    self.dialog.close()
                    dispose(self.dialog)
                except Exception:
                    log.logger().exception("close the %s dialog", self.kind.TITLE)
                self.dialog = None
            if self._on_close is not None:
                self._on_close(self)
            then, self._then = self._then, None
            if then is not None and self._committed:
                QtCore.QTimer.singleShot(0, then)
            self.finishing = False
            after = getattr(self.kind, "after_ok", None)  # what the kind does next (Split ▸ Make Assembly)
            if after is not None and self._committed and _feature_alive(self.feature):
                values, feature = dict(self._applied or {}), self.feature
                QtCore.QTimer.singleShot(0, lambda: after(self.doc, feature, values))

    def _finish_steps(self, commit):
        self._committed = commit
        self._peek_axes(False)
        self._sync_axis_line(False)
        self._timer.stop()  # a value edited during the last preview must not preview after the close
        self._observe(False)
        try:
            self._show_base(False)
        except Exception:
            pass
        if commit and self.creating:
            self._hide_used_sketches()
        for sk in self._picked_sketches() + self._saved_sketches:  # back to the part view's policy: a sketch
            if _feature_alive(sk):  # the feature took is hidden, an unused one follows its (or the user's) rule
                try:  # (no local import of document here: it made the name local to the whole method)
                    self._set_visible(sk, document.sketch_shown(sk))
                except Exception:
                    pass
        self._saved_sketches = []
        if self._markers is not None:
            self._markers.remove()
            self._markers = None
        try:
            if commit:
                self._roll_forward(True)  # inside the step: the later features' new shapes are part of it
                self.doc.commitTransaction()
                for d in self._extra:
                    d.commitTransaction()
                self._extra = []
            else:
                self._abort_step()
                mark = getattr(self, "_undo_mark", None)
                while mark is not None and self.doc.UndoCount > mark:  # steps committed under the dialog
                    self.doc.undo()
                self.doc.recompute()
                model.relink(self.doc)  # objects restored by the abort lose their formula links (FreeCAD quirk)
                self._abort_extras(reopen=False)
                self._roll_forward(False)
        except Exception as e:
            log.report("warning", "Could not close the %s step cleanly: %s" % (self.kind.TITLE, e))
        base, self._error_base = getattr(self, "_error_base", None), None
        if base is not None and not commit and not self.creating and self.feature is not None \
                and _feature_alive(self.feature):  # an edit cancelled while it was failing: the feature shows again
            self._set_visible(self.feature, True)
            self._set_visible(base, False)
        if self.feature is not None and _feature_alive(self.feature):
            document.show_finished(self.doc, [self.feature])
        if getattr(self.kind, "IN_SKETCH", False) and self.profile is not None and _feature_alive(self.profile):
            try:
                self.profile.InCadEditing = ""  # the pattern is no longer open in a dialog
            except Exception:
                pass
            try:
                import FreeCADGui as Gui
                Gui.Selection.clearSelection()  # the copies were rebuilt: a selection by name would point past them
            except Exception:
                pass
        elif not getattr(self.kind, "IN_SKETCH", False):
            try:  # the dialog's picks were kept selected for it: the next command (a Chamfer after a Fillet) must
                import FreeCADGui as Gui  # not start on them, named on the shape before this feature
                Gui.Selection.clearSelection(self.doc_name)
            except Exception:
                pass
