"""Project Geometry in the new sketch (ProstoCAD 0.18 New Sketch): the old program's tool, unchanged in what it does to
the file, with the new sketch's look. While it runs, the layer lets the pointer through to the part: the edge under
it (or every edge of the face under it) lights up as it lands on the sketch; a click projects it the old program's
way (incad.ui.shell._project_other_at: own part linked, another Solid copied, a plane's line, through a see-through
plane), then the sketch is read back into the layer as one step its Undo takes back. Esc or a right click ends it;
another tool ends it too."""
from incad.core import log
from incad.qt import QtCore

PROMPT = "Project  Click an edge, a face or a plane of the part  ·  Esc ends"


def edge_first(hits, tol):
    """hits with a vertex, else an edge, lying within tol (mm) of the nearest hit moved to the front."""
    if not hits:
        return hits
    first = hits[0]
    near = lambda h: ((h.get("x", 0) - first.get("x", 0)) ** 2 + (h.get("y", 0) - first.get("y", 0)) ** 2
                      + (h.get("z", 0) - first.get("z", 0)) ** 2) ** 0.5 <= tol
    for word in ("Vertex", "Edge"):
        h = next((h for h in hits if str(h.get("Component", "")).split(".")[-1].startswith(word) and near(h)), None)
        if h is not None:
            return [h] + [x for x in hits if x is not h]
    return hits


def auto_targets(sk):
    """{'points': [QPointF], 'lines': [[QPointF, …]]}: the vertices and edges of the solid the sketch projects from
    (incad.core.sketch_autoproject._base) lying in the sketch's plane, in sketch coordinates."""
    from incad.core import sketch_autoproject as ap
    base = ap._base(sk)
    if base is None:
        return None
    inv = sk.Placement.inverse()
    shape = base.Shape
    pts = []
    for v in shape.Vertexes:
        q = inv.multVec(v.Point)
        if abs(q.z) < ap.PLANE_TOL:
            pts.append(QtCore.QPointF(q.x, q.y))
    lines = []
    for e in shape.Edges:
        try:
            if not all(abs(inv.multVec(p).z) < ap.PLANE_TOL for p in e.discretize(7)):
                continue
            lines.append([QtCore.QPointF(q.x, q.y) for q in (inv.multVec(p) for p in e.discretize(64))])
        except Exception:
            continue
    return dict(points=pts, lines=lines) if pts or lines else None


def _lists(lay):
    """The layer's shapes by kind, each list only ever grown at its end by drawing: lines, arcs, ellipses (their key
    points), splines, lone points."""
    return [("line", lay.lines), ("arc", lay.arcs), ("ell", [lay._ell_keys(i) for i in range(len(lay.ells))]),
            ("spl", lay.spls), ("pt", [t["pts"][0] for t in lay.ties if t["kind"] == "sk_point"])]


def _counts(lay):
    return [len(shapes) for _k, shapes in _lists(lay)]


def _on_target(p, targets, tol=1e-6):
    """Does point p stand on one of the part's vertices or edges (as a magnet put it there)?"""
    d = lambda a, b: ((a.x() - b.x()) ** 2 + (a.y() - b.y()) ** 2) ** 0.5
    if any(d(p, q) <= tol for q in targets.get("points", [])):
        return True
    for poly in targets.get("lines", []):
        for a, b in zip(poly, poly[1:]):
            ux, uy = b.x() - a.x(), b.y() - a.y()
            n2 = ux * ux + uy * uy
            if n2 < 1e-18:
                continue
            t = max(0.0, min(1.0, ((p.x() - a.x()) * ux + (p.y() - a.y()) * uy) / n2))
            if d(p, QtCore.QPointF(a.x() + ux * t, a.y() + uy * t)) <= tol:
                return True
    return False


class _Filter(QtCore.QObject):
    """The layer's events seen first by the session (a session is no QObject)."""

    def __init__(self, session, parent):
        super().__init__(parent)
        self.session = session

    def eventFilter(self, obj, event):
        return self.session._project_event(obj, event)


class Project:
    """Mixed into sketch_next.session.Session."""

    def _project_start_up(self):
        self.projecting = False
        self._proj_key = None
        self._proj_filter = _Filter(self, self.layer)
        self.layer.installEventFilter(self._proj_filter)

    # ----- on and off -----
    def project_start(self):
        lay = self.layer
        if lay.tool:
            lay.stop_tool()
        self.projecting = True
        lay.setMouseTracking(True)
        self._prompt(PROMPT)

    def project_stop(self, say=True):
        if not self.projecting:
            return
        self.projecting = False
        self._proj_key = None
        self._lit(None)
        if say:
            self._prompt("Sketch: draw and dimension, then Finish Sketch (Esc)")

    # ----- the pointer through the layer -----
    def _project_event(self, obj, event):
        if obj is self.layer and not getattr(self, "projecting", False) and self.layer.tool == "dimension" \
                and event.type() == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
            try:
                return self._dim_to_part(event.position())
            except Exception:
                log.logger().exception("new sketch: dimension to the part")
                return False
        if obj is not self.layer or not getattr(self, "projecting", False):
            return False
        t = event.type()
        try:
            if t == QtCore.QEvent.MouseMove:
                self._project_hover(event.position())
                return False  # the view and the cube still follow the pointer
            if t == QtCore.QEvent.MouseButtonPress:
                if event.button() == QtCore.Qt.LeftButton:
                    self._project_click(event.position())
                    return True
                if event.button() == QtCore.Qt.RightButton:
                    self.project_stop()
                    return True
            if t == QtCore.QEvent.MouseButtonRelease and event.button() in (QtCore.Qt.LeftButton, QtCore.Qt.RightButton):
                return True
            if t == QtCore.QEvent.KeyPress and event.key() == QtCore.Qt.Key_Escape:
                self.project_stop()
                return True
        except Exception:
            log.logger().exception("new sketch: Project Geometry")
        return False

    def _hits(self, pos):
        """What lies under the pointer, nearest first; an edge or a vertex the pointer is on comes before the face
        it bounds (FreeCAD's own pick does so: the old program's tool took the edge)."""
        from incad.ui.nav import to_coin
        try:
            hits = self.view.getObjectsInfo(to_coin(self.vp, pos.x(), pos.y())) or []
        except Exception:
            return []
        return edge_first(hits, 6.0 / max(self.layer.scale, 1e-9))

    def _element(self, info):
        """(object, element name) of a hit: 'Body' + 'Pad.Edge3' is the Pad's Edge3."""
        doc = self.sk.Document
        obj = doc.getObject(info.get("Object", "") or "")
        sub = str(info.get("Component", "") or "")
        if "." in sub:
            parts = sub.split(".")
            obj, sub = doc.getObject(parts[-2]) or obj, parts[-1]
        return obj, sub

    def _project_hover(self, pos):
        """What the click would take, lit (chosen as the click chooses, shell._project_other_at): an edge, every edge
        of a face, a vertex; a work plane: the line where it meets the sketch; an edge seen through a see-through
        plane wins over the plane; the plane the sketch stands on lets the pointer through."""
        what = self._project_target(self._hits(pos))
        key = what[:2] if what else None
        if key == self._proj_key and (key is not None or not getattr(self.layer, "host_lit", None)):
            return
        self._proj_key = key
        if what is None:
            return self._lit(None)
        if what[0] == "plane":
            from incad.core import plane_cut
            seg = plane_cut._segment(self.sk, what[2])
            if seg is None:
                return self._lit(None)
            from incad.qt import QtCore as Qc
            return self._lit(dict(lines=[[Qc.QPointF(v.x, v.y) for v in seg]], points=[]))
        obj, sub = self._element(what[2])
        try:
            shape = obj.Shape.getElement(sub) if obj is not None and sub else None
        except Exception:
            shape = None
        if shape is None:
            return self._lit(None)
        if sub.startswith("Vertex"):
            return self._lit(dict(lines=[], points=[self._on_sketch(shape.Point)]))
        edges = shape.Edges
        return self._lit(dict(lines=[[self._on_sketch(p) for p in e.discretize(48)] for e in edges], points=[]))

    def _project_target(self, hits):
        """("plane", name, plane) or ("hit", object/component, hit) — what a click there projects; None."""
        from incad.ui import shell
        inst = shell.instance()
        if inst is None or not hits:
            return None
        sk = self.sk
        own = {s[0].Name for s in (getattr(sk, "AttachmentSupport", None) or [])
               if s and s[0] is not None and s[0].TypeId in ("PartDesign::Plane", "App::Plane", "App::Origin")}
        solid = [h for h in hits if inst._plane_hit(sk, h) is None and h.get("Object") not in own]
        edge = next((h for h in solid if str(h.get("Component", "")).split(".")[-1].startswith(("Edge", "Vertex"))), None)
        planes_first = solid[:1] != hits[:1]
        if edge is not None and (planes_first or edge is hits[0]):
            info = edge
        elif not planes_first:
            info = hits[0]
        else:
            plane = inst._plane_hit(sk, hits[0])
            if plane is not None and plane.Name not in own:
                return ("plane", plane.Name, plane)
            if not solid:
                return None
            info = solid[0]
        return ("hit", (info.get("Object"), info.get("Component")), info)

    def _on_sketch(self, p):
        """A point of the part, square onto the sketch's plane, in sketch coordinates."""
        from incad.qt import QtCore as Qc
        q = self.sk.getGlobalPlacement().inverse().multVec(p)
        return Qc.QPointF(q.x, q.y)

    def _lit(self, what):
        self.layer.host_lit = what
        self.layer.update()

    # ----- the click -----
    def _project_click(self, pos):
        """The old program's Project Geometry on what is under the pointer: the layer is written into the sketch
        first, the projection made, the projected edges made normal as ⚙ ▸ Projected Edges says, and the sketch read
        back into the layer (one Undo of the layer takes it back; Finish then takes the projection away again)."""
        from incad.core import safe
        from incad.core import sketch as sketches
        from incad.sketch_next import convert
        from incad.ui import shell
        inst = shell.instance()
        if inst is None:
            return
        lay, sk, doc = self.layer, self.sk, self.sk.Document
        before = len(sk.ExternalGeo), sk.GeometryCount
        st, steps = lay._state(), list(lay.steps)
        r = safe.run_safely("Edit Sketch", lambda: convert.write(sk, lay.dump()), doc, only=[sk])
        if not r.ok:
            return self._prompt("Project: the sketch could not be written first: %s" % r.error)
        done = inst._project_other_at(self.view, self.vp, pos.x(), pos.y(), direct=True, hits=self._hits(pos))
        if not done or (len(sk.ExternalGeo), sk.GeometryCount) == before:
            self._reload(st, steps, changed=False)
            return self._prompt(PROMPT)
        try:
            sketches.adopt_projections(sk)
        except Exception as e:
            log.report("warning", "Could not make the projected edges normal geometry: %s" % e)
        self._reload(st, steps, changed=True)
        self._proj_key = None
        self._project_hover(pos)  # the lit edge is the projection now: lit again only as the pointer says
        self._prompt(PROMPT)

    # ----- Dimension to the part (the old program's core/sketch_model_dims, shell._dimension_to_model) -----
    def _dim_to_part(self, pos):
        """A Dimension click that misses the sketch but lands on an edge or corner of the part (the magnet's reach):
        that element is projected as construction (whatever ⚙ Projected Edges says, its own Undo step), and the
        click goes on to the tool, which takes the projection as its first or second pick. A dimension from it that
        would over-constrain the sketch goes in as a reference at once (the user, 2026-11-10: as the old program).
        True when the click was taken."""
        import FreeCAD as App
        from incad.ui import shell
        lay = self.layer
        inst = shell.instance()
        if inst is None or lay.box_dim.isVisible():
            return False
        if lay._pick(pos, ("point", "line", "arc")) is not None or lay._dim_place_at(pos) is not None:
            return False  # the sketch's own: the tool's
        what = self._project_target(self._hits(pos))
        if what is None or what[0] != "hit":
            return False
        info = what[2]
        owner, comp = self._element(info)
        if owner is None or owner is self.sk or owner.TypeId == "Sketcher::SketchObject" or \
                not hasattr(owner, "Shape") or owner.Shape.isNull():
            return False
        tol = shell.SNAP_PX / max(lay.scale, 1e-9)
        sub = inst._model_element_at(owner, App.Vector(info["x"], info["y"], info["z"]), comp or "", tol)
        if sub is None:
            return False  # a face: the click places the dimension, as on empty paper
        picks = [self._pick_place(p) for p in lay.dim_picks]
        st, steps = lay._state(), list(lay.steps)
        from incad.core import safe
        from incad.sketch_next import convert
        sk, doc = self.sk, self.sk.Document
        r = safe.run_safely("Edit Sketch", lambda: convert.write(sk, lay.dump()), doc, only=[sk])
        if not r.ok:
            self._prompt("Dimension: the sketch could not be written first: %s" % r.error)
            return True
        try:
            inst._project_for_dimension(sk, owner, sub)
        except Exception as e:
            self._reload(st, steps, changed=False)
            log.report("warning", "Cannot dimension to %s %s: %s" % (owner.Label, sub, e))
            self._dim_again([p for p in picks if p is not None])
            return True
        self._reload(st, steps, changed=True)
        self._dim_again([p for p in picks if p is not None])
        before = list(lay.dim_picks)
        lay._dim_click(pos)  # on the projection now under the pointer
        lay.part_picks = [p for p in lay.dim_picks if p not in before] or \
            ([lay.dim_picks[-1]] if lay.dim_new is not None and lay.dim_picks else [])
        if lay.part_picks:
            lay.part_picks = [p for p in lay.part_picks if self._is_projection(p)]
        lay.on_part_reference = lambda: log.report("info", "The sketch has that size already: the dimension is a "
                                                           "reference")
        return True

    def _pick_place(self, what):
        """A Dimension pick as where it lies (indices change when the sketch is read back): ('point', (x, y)) or
        ('line'/'arc', ((x, y), …)); the origin and the axes as they are."""
        lay = self.layer
        kind, i = what
        try:
            if kind == "point":
                q = lay.pts[i]
                return ("point", (q.x(), q.y()))
            if kind == "line":
                return ("line", tuple((lay.pts[k].x(), lay.pts[k].y()) for k in lay.lines[i]))
            if kind == "arc":
                return ("arc", tuple((lay.pts[k].x(), lay.pts[k].y()) for k in lay.arcs[i]))
        except (IndexError, TypeError):
            return None
        return what

    def _dim_again(self, places):
        """The Dimension tool on again after the sketch was read back, its picks taken again where they lie."""
        lay = self.layer
        lay.sel_lines, lay.sel_arcs, lay.sel_pts = set(), set(), set()
        lay.start_dimension()
        self.last_cmd = "sketch.dimension"
        near = lambda a, b: abs(a[0] - b[0]) < 1e-7 and abs(a[1] - b[1]) < 1e-7
        for kind, where in places:
            found = None
            if kind == "point":
                found = next((("point", k) for k, q in enumerate(lay.pts) if near((q.x(), q.y()), where)), None)
            elif kind in ("line", "arc"):
                shapes = lay.lines if kind == "line" else lay.arcs
                for i, keys in enumerate(shapes):
                    pts = tuple((lay.pts[k].x(), lay.pts[k].y()) for k in keys)
                    if len(pts) == len(where) and all(near(a, b) for a, b in zip(pts, where)):
                        found = (kind, i)
                        break
            else:
                found = (kind, where)
            if found is not None:
                lay._dim_take(found)
        lay._refresh()

    def _is_projection(self, what):
        lay = self.layer
        kind, i = what
        proj = lay._proj_points()
        if kind == "point":
            return i in proj
        if kind == "line":
            return all(k in proj for k in lay.lines[i])
        if kind == "arc":
            return all(k in proj for k in lay.arcs[i])
        return False

    # ----- Break Link (right-click on a projected edge) -----
    def break_link(self, item):
        """The old program's Break Link (incad.core.sketch_trim.break_link) on the projected shape `item` (kind, i):
        the layer written first, the edge made the sketch's own (same kind, its ties moved onto it), the sketch read
        back as one step the layer's Undo takes back."""
        from incad.core import safe, sketch_trim
        from incad.sketch_next import convert
        lay, sk, doc = self.layer, self.sk, self.sk.Document
        ref = lay._fit_ids()[item[0]][item[1]]
        if not lay._is_ext(ref):
            return
        st, steps = lay._state(), list(lay.steps)
        r = safe.run_safely("Edit Sketch", lambda: convert.write(sk, lay.dump()), doc, only=[sk])
        if not r.ok:
            return self._prompt("Break Link: the sketch could not be written first: %s" % r.error)
        g = (r.value or {}).get("ext_map", {}).get(int(ref[4:]), int(ref[4:]))
        r = safe.run_safely("Break Link", lambda: sketch_trim.break_link(sk, [g]), doc, only=[sk])
        if not r.ok:
            self._reload(st, steps, changed=False)
            return log.report("warning", r.error)
        self._reload(st, steps, changed=True)
        log.report("info", "1 projected edge now the sketch's own: it no longer follows the part")

    # ----- auto-projection (⚙ ▸ Projected Edges ▸ Project What a Drawn Point Touches) -----
    def _auto_start_up(self):
        """The part's edges and vertices in the sketch's plane become magnets of the layer (the old program's
        incad.core.sketch_autoproject finds the same ones: the last solid feature before the sketch)."""
        self.layer.host_targets = None
        self.layer.part_targets = None  # the part's magnets; the layer offers them once the pointer rests (session.py)
        self._auto_counts = _counts(self.layer)
        from incad.core import prefs
        if not prefs.auto_project():
            return
        try:
            self.layer.part_targets = auto_targets(self.sk)
        except Exception:
            log.logger().exception("new sketch: the part's edges for auto-projection")

    def _auto_check(self):
        """After a drawing step (the layer at rest, no figure under way): a point standing on the part's edge or
        vertex gets it projected and is tied to it — the old program's sketch_autoproject.snap, run on the sketch
        as written, then read back. A tie the sketch can't take is taken off there, as before."""
        lay = self.layer
        targets = getattr(lay, "part_targets", None) or getattr(lay, "host_targets", None)
        if not targets or lay.busy() or getattr(self, "projecting", False):
            return
        # as the old program: only what a drawing step added (new shapes, new points), never a pulled point
        counts = _counts(lay)
        before = getattr(self, "_auto_counts", counts)
        self._auto_counts = counts
        if all(n <= b for n, b in zip(counts, before)):
            return
        fresh = set()
        for (kind, shapes), b in zip(_lists(lay), before):
            for item in shapes[b:]:
                fresh |= set(item if kind != "spl" else item[1]) if kind != "pt" else {item}
        proj = lay._proj_points()
        on = [k for k in sorted(fresh) if k not in proj and k < len(lay.pts) and _on_target(lay.pts[k], targets)]
        if not on:
            return
        from incad.core import safe, sketch_autoproject
        from incad.core import prefs
        from incad.sketch_next import convert
        from incad.ui import shell
        sk, doc = self.sk, self.sk.Document
        tool = lay.tool
        st, steps = lay._state(), list(lay.steps)
        had = {sk.getGeometryId(i) for i in range(sk.GeometryCount)}
        r = safe.run_safely("Edit Sketch", lambda: convert.write(sk, lay.dump()), doc, only=[sk])
        if not r.ok:
            return
        first = next((i for i in range(sk.GeometryCount) if sk.getGeometryId(i) not in had), sk.GeometryCount)
        tol = shell.SNAP_PX / max(lay.scale, 1e-9)
        r = safe.run_safely("Project What a Drawn Point Touches",
                            lambda: sketch_autoproject.snap(sk, first, tol, prefs.projected_edges() == "normal"),
                            doc, only=[sk])
        made = bool(r.ok and r.value)
        if made:
            try:
                from incad.core import sketch as sketches
                sketches.adopt_projections(sk)
            except Exception as e:
                log.report("warning", "Could not make the projected edges normal geometry: %s" % e)
        self._reload(st, steps, changed=made)
        if tool and self.last_cmd:
            self.command(self.last_cmd)  # the tool goes on, as it was

    def _reload(self, st, steps, changed):
        """The sketch read back into the layer, the view kept; with a change, one step Undo takes back."""
        from incad.sketch_next import convert
        lay = self.layer
        centre, scale = lay.center, lay.scale
        data, _rep = convert.read(self.sk)
        lay.load(data)
        lay.editing = True
        lay.part_picks = []  # indices of the old reading: gone
        lay.center, lay.scale = centre, scale
        lay.steps = steps + ([("state", st, len(lay.lines), len(lay.arcs))] if changed else [])
        if changed:
            lay.redo_steps = []
        lay.changed.emit()
        lay.update()
