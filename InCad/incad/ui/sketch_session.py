"""Starting, editing and finishing sketches the Inventor way (GUI side of incad.core.sketch)."""
from incad.core import document, log, safe, sketch
from incad.qt import QtCore


def close_edit(gdoc):
    """Leave the sketch (or other edit) open in `gdoc`. A Sketcher tool still running is stopped first: a resetEdit
    under FreeCAD's Dimension tool crashed FreeCAD every time (DrawSketchHandlerDimension::deactivated solves the
    sketch and ViewProviderSketch::slotSolverUpdate reads a view that is gone)."""
    import FreeCADGui as Gui
    from incad.ui import rectangle_preview
    rectangle_preview.stop()
    vp = gdoc.getInEdit()
    if sketch.is_sketch(getattr(vp, "Object", None)):
        try:
            Gui.runCommand("Sketcher_StopOperation")  # ends the running tool; the sketch stays open
        except Exception as e:
            log.logger().warning("Could not stop the Sketcher tool: %s", e)
    gdoc.resetEdit()


class SketchSession:
    def __init__(self, on_change=None, on_edit=None):
        self.picking = False
        self.pick_doc = None
        self._on_change = on_change
        self._on_edit = on_edit      # called with the sketch when editing starts (baseline for the value-box rule)
        self._saved_vp = {}          # (doc, sketch) -> view-provider settings InCad changed, restored when done
        self._last_edited = None
        self._origin = None  # (view object, was visible) while picking
        self._datum_scale = None  # FreeCAD's DatumScale before the pick enlarged the planes (None: not changed)
        self._solid = False  # the part had a solid when the pick began (its faces win over the planes drawn)

    def _changed(self):
        if self._on_change is not None:
            self._on_change()

    @staticmethod
    def _resolve(obj, sub):
        """A pick on a Body means its Tip feature; 'Pad.Face6' style paths name the feature and the face."""
        if "." in sub:
            parts = sub.split(".")
            child = obj.Document.getObject(parts[-2]) if len(parts) > 1 else None
            return (child or obj), parts[-1]
        if obj is not None and obj.TypeId == "PartDesign::Body" and sub.startswith("Face") and obj.Tip is not None:
            return obj.Tip, sub
        return obj, sub

    def start(self):
        import FreeCAD as App
        import FreeCADGui as Gui
        doc = App.ActiveDocument
        if not document.is_incad_part(doc):
            raise ValueError("Create or open an InCad part first")
        for sel in Gui.Selection.getSelectionEx(doc.Name):
            for sub in (sel.SubElementNames or ("",)):
                support, face = self._resolve(sel.Object, sub)
                if sketch.valid_support(support, face):
                    Gui.Selection.clearSelection()
                    return self._create(support, face)
        self._begin_pick(doc)
        return None

    def _begin_pick(self, doc):
        import FreeCADGui as Gui
        self.cancel_pick()
        body = document.primary_body(doc)
        self._solid = body is not None and _has_solid(body)
        if body is not None:
            # The origin planes are offered to click, big enough to see. On an empty part the view is framed on
            # them; with a solid the view stays where the user had it and a face under the cursor wins over a
            # plane drawn in front of it (addSelection), so the planes never take a click meant for the model.
            vos = [body.Origin.ViewObject] + [f.ViewObject for f in body.Origin.OriginFeatures]
            self._origin = [(v, v.Visibility) for v in vos]  # the planes themselves too (a saved file may hide them)
            gdoc = Gui.getDocument(doc.Name)
            view = gdoc.ActiveView if gdoc is not None else None
            if view is not None:
                # the size first: planes switched on below use it
                self._datum_scale = _set_datum_scale(PICK_DATUM_SCALE) if self._solid else _frame_planes(body, view)
            # FreeCAD applies a new datum size only when a plane is shown again (clicking Origin did that for the
            # user): switch them on fresh now, and once more when the size has surely arrived
            _show_fresh(vos, view)
            QtCore.QTimer.singleShot(300, lambda: self.picking and _show_fresh(vos[1:], view))
        self.picking = True
        self.pick_doc = doc.Name  # another document in front ends the pick (shell.refresh)
        Gui.Selection.clearSelection()
        Gui.Selection.addObserver(self)
        log.report("info", "Pick a plane or a flat face for the new sketch (Esc cancels)")
        self._changed()

    def cancel_pick(self):
        import FreeCADGui as Gui
        if not self.picking:
            return
        self.picking = False
        try:
            Gui.Selection.removeObserver(self)
        except Exception:
            pass
        if self._origin is not None:
            for vo, was in self._origin:
                try:
                    vo.Visibility = was
                except Exception:
                    pass
            self._origin = None
        if self._datum_scale is not None:
            _restore_datums(self._datum_scale)
            self._datum_scale = None
        self._changed()

    def addSelection(self, doc_name, obj_name, sub, pnt):
        import FreeCAD as App
        if not self.picking:
            return
        obj = App.getDocument(doc_name).getObject(obj_name)
        support, face = self._resolve(obj, sub)
        if self._solid and _is_origin_plane(support):
            hit = _face_under(doc_name, pnt)  # the planes are drawn over the model: a face there is what was meant
            if hit is not None:
                support, face = hit
        if sketch.valid_support(support, face):
            QtCore.QTimer.singleShot(0, lambda: self._picked(support, face))
        else:
            log.report("warning", "That is not flat: pick a plane or a flat face")

    def start_on(self, support, face=""):
        """New sketch straight on a plane or a flat face (browser double-click, right-click New Sketch)."""
        if not sketch.valid_support(support, face):
            raise ValueError("%s is not flat: pick a plane or a flat face" % support.Label)
        self.cancel_pick()
        return self._create(support, face)

    def pick_origin(self, role):
        """The plane panel's buttons: sketch on an origin plane of the main solid."""
        import FreeCAD as App
        body = document.primary_body(App.ActiveDocument)
        plane = next(o for o in body.Origin.OriginFeatures if o.Role == role)
        self._picked(plane, "")

    def _picked(self, support, face):
        import FreeCADGui as Gui
        self.cancel_pick()
        Gui.Selection.clearSelection()
        self._create(support, face)

    def _create(self, support, face):
        import FreeCAD as App
        doc = App.ActiveDocument
        body = document.body_of(support) or document.primary_body(doc)  # a face of a second Solid: its sketch
        view_dir = view_up = None
        try:
            import FreeCADGui as Gui
            view = Gui.ActiveDocument.ActiveView
            view_dir = view.getViewDirection()
            view_up = view.getCameraOrientation().multVec(App.Vector(0, 1, 0))  # the sketch's axes turn the shortest way
        except Exception:
            pass
        r = safe.run_safely("New Sketch", lambda: sketch.make_sketch(body, support, face, view_dir, view_up), doc,
                            only=lambda sk: [sk])
        if r.ok:
            self.edit(r.value)
            return r.value
        return None

    def edit(self, sk):
        import FreeCADGui as Gui
        vo = sk.ViewObject
        key = (sk.Document.Name, sk.Name)
        if key not in self._saved_vp:
            self._saved_vp[key] = (vo.EditingWorkbench,
                                   vo.Autoconstraints if "Autoconstraints" in vo.PropertiesList else None)
        vo.EditingWorkbench = ""  # stay in InCad while sketching (restored when the sketch is finished)
        from incad.ui import shell as shells
        inst = shells.instance()
        if inst is not None:
            inst._unpeek()  # shown for its tree row: FreeCAD restores what it sees at setEdit, so end that first
        if "Autoconstraints" in vo.PropertiesList:
            vo.Autoconstraints = True
        if self._on_edit is not None:
            self._on_edit(sk)
        body = document.body_of(sk)
        shown = bool(body is not None and body.Tip is not None and body.Tip.Visibility)
        visible = [o for o in sk.Document.Objects if o is not sk and o.isDerivedFrom("Part::Feature") and o.Visibility]
        Gui.getDocument(sk.Document.Name).setEdit(sk.Name)
        self._show_unrelated(sk, visible)
        if shown:
            self._show_part_before(sk, body)
        self._last_edited = key
        self._changed()

    @staticmethod
    def _uses(obj, sk):
        """Is `obj` built on the sketch? Through a Solid it uses only that Solid's result (its Tip), never every
        object in it — FreeCAD's own rule counts a jar's lid, cut by the jar, as built on any sketch of the jar."""
        seen, todo = set(), [obj]
        while todo:
            o = todo.pop()
            if o is sk:
                return True
            if o.Name in seen:
                continue
            seen.add(o.Name)
            if o.TypeId == "PartDesign::Body":
                if o.Tip is not None:
                    todo.append(o.Tip)
                continue
            todo.extend(o.OutList)
        return False

    def _show_unrelated(self, sk, visible):
        """What FreeCAD hid on opening the sketch though it is not built on it (the user: the lid went with the
        jar on every new sketch) is shown again, through the sketch's TempoVis: as it was once the sketch closes."""
        try:
            back = [o for o in visible if not o.Visibility and not self._uses(o, sk)]
            tv = getattr(sk.ViewObject, "TempoVis", None) if back else None
            if tv is not None:
                tv.show(back)
        except Exception:
            log.logger().exception("show what %s does not use", sk.Name)

    @staticmethod
    def _part_before(sk):
        """The Solid's last solid feature before `sk` in its tree: the part as it is where the sketch sits."""
        body = document.body_of(sk)
        group = list(body.Group) if body is not None else []
        if sk not in group:
            return None
        for o in reversed(group[:group.index(sk)]):
            try:
                if o.isDerivedFrom("PartDesign::Feature") and not o.Shape.isNull() and o.Shape.Solids:
                    return o
            except Exception:
                pass
        return None

    def _show_part_before(self, sk, body):
        """FreeCAD hides everything built on the open sketch, the finished part with it, so a sketch in the middle
        of a part opened on an empty screen (the user: "I need to know what I'm doing and where I am"). The part as
        it is before the sketch is shown instead, through the sketch's own TempoVis: hidden again when it closes,
        like FreeCAD's own hiding, nothing to undo. Only when the part was shown before the sketch opened."""
        try:
            if body.Tip.Visibility:
                return  # still shown: nothing in the part uses the sketch
            before = self._part_before(sk)
            if before is None or before.Visibility:
                return
            tv = getattr(sk.ViewObject, "TempoVis", None)
            if tv is None:
                return
            tv.show(before)
        except Exception:
            log.logger().exception("show the part before %s", sk.Name)

    def restore(self, key):
        """Put back the view-provider settings of a sketch InCad edited (they are saved in the user's file)."""
        import FreeCAD as App
        saved = self._saved_vp.pop(key, None)
        if saved is None:
            return
        try:
            obj = App.getDocument(key[0]).getObject(key[1])
        except Exception:
            obj = None
        if obj is None:
            return
        vo = obj.ViewObject
        vo.EditingWorkbench = saved[0]
        if saved[1] is not None and "Autoconstraints" in vo.PropertiesList:
            vo.Autoconstraints = saved[1]

    def sync(self):
        """Called on every refresh: a sketch left by any route (✓, Esc, undo) gets its settings back."""
        editing = self.editing()
        key = (editing.Document.Name, editing.Name) if editing is not None else None
        for done in [k for k in self._saved_vp if k != key]:
            self.restore(done)
        self._sync_dimmed(editing)
        self._sync_text_lines(editing)

    def _sync_text_lines(self, editing):
        """The open sketch's Text drawn as outlines on top (like the sketch's own lines): letters written on a face
        lie in it and flickered into it. Never pickable; gone when the sketch closes."""
        old = getattr(self, "_text_lines", None)
        if old is not None:
            try:
                old[0].getSceneGraph().removeChild(old[1])
            except Exception:
                pass
            self._text_lines = None
        if editing is None:
            return
        try:
            import FreeCADGui as Gui
            from pivy import coin
            from incad.features import sketch_text
            from incad.ui import theme
            texts = [t for t in sketch_text.texts_of(editing) if not t.Shape.isNull()]
            if not texts:
                return
            group = editing.getParentGeoFeatureGroup()
            pl = group.getGlobalPlacement() if group is not None else None
            lines = []
            for t in texts:
                for e in t.Shape.Edges:
                    pts = e.discretize(Deflection=0.02) if len(e.Vertexes) else e.discretize(24)
                    lines.append([pl.multVec(p) if pl is not None else p for p in pts])
            if not lines:
                return
            node = coin.SoSeparator()
            style = coin.SoPickStyle()
            style.style = coin.SoPickStyle.UNPICKABLE
            color = coin.SoBaseColor()
            hex_color = theme.SKETCH_COLORS[theme.current()]["EditedEdgeColor"].lstrip("#")
            color.rgb.setValue(*(int(hex_color[i:i + 2], 16) / 255.0 for i in (0, 2, 4)))
            draw = coin.SoDrawStyle()
            draw.lineWidth = 1.5
            coords = coin.SoCoordinate3()
            flat = [p for line in lines for p in line]
            coords.point.setValues(0, len(flat), [(p.x, p.y, p.z) for p in flat])
            lineset = coin.SoLineSet()
            lineset.numVertices.setValues(0, len(lines), [len(line) for line in lines])
            for n in (style, color, draw, coords, lineset):
                node.addChild(n)
            top = coin.SoAnnotation()
            top.addChild(node)
            view = Gui.getDocument(editing.Document.Name).ActiveView
            view.getSceneGraph().addChild(top)
            self._text_lines = (view, top)
        except Exception:
            log.logger().exception("text outlines while sketching")

    DIM = 0.62  # the part's colour while a sketch is open (dark theme): its lines and shapes stand out on it
    SEE_THROUGH = 0.7  # a solid the open sketch's plane cuts through: drawn see-through, the sketch inside visible

    @staticmethod
    def _cut_by(shape, editing):
        """Does the open sketch's plane run through the solid (a work plane inside the part)?"""
        import FreeCAD as App
        group = editing.getParentGeoFeatureGroup()
        pl = group.getGlobalPlacement().multiply(editing.Placement) if group is not None else editing.Placement
        n = pl.Rotation.multVec(App.Vector(0, 0, 1))
        d = pl.Base.dot(n)
        bb = shape.BoundBox
        sides = {(App.Vector(x, y, z).dot(n) - d) > 1e-6 for x in (bb.XMin, bb.XMax) for y in (bb.YMin, bb.YMax)
                 for z in (bb.ZMin, bb.ZMax)}
        if len(sides) < 2:
            return False  # the box lies on one side: the plane misses it
        try:
            return bool(shape.slice(n, d))
        except Exception:
            return True

    def _sync_dimmed(self, editing):
        """While a sketch is open the shown solids get an override material on top of each one's scene, never the
        object's own colour (nothing saved, nothing to undo), off again after: darker in the dark theme, and
        see-through where the sketch's plane runs through the solid (the user: a sketch on a work plane inside the
        part was hidden by it)."""
        dimmed = getattr(self, "_dimmed", None)
        if dimmed is None:
            dimmed = self._dimmed = {}
        cuts = getattr(self, "_cuts", None)
        if cuts is None:
            cuts = self._cuts = {}
        want = {}
        try:
            from incad.ui import theme
            dark = theme.current() == "dark"
            if editing is not None:
                where = (editing.Document.Name, editing.Name, str(editing.Placement))
                for o in editing.Document.Objects:
                    vo = getattr(o, "ViewObject", None)
                    if o is editing or vo is None or not o.Visibility or not hasattr(o, "Shape") \
                            or o.TypeId.startswith(("Sketcher::", "App::", "PartDesign::Plane", "PartDesign::Line",
                                                    "PartDesign::Point", "PartDesign::Body")):
                        continue
                    try:
                        if o.Shape.isNull() or not o.Shape.Faces:
                            continue
                    except Exception:
                        continue
                    ck = where + (o.Name,)
                    if ck not in cuts:
                        cuts.clear() if len(cuts) > 200 else None
                        cuts[ck] = self._cut_by(o.Shape, editing)
                    if dark or cuts[ck]:
                        want[(o.Document.Name, o.Name)] = (o, dark, cuts[ck])
            else:
                cuts.clear()
        except Exception:
            log.logger().exception("dim the part while sketching")
        for k in [k for k in dimmed if k not in want or dimmed[k][2] != want[k][1:]]:
            root, node, _ = dimmed.pop(k)
            try:
                root.removeChild(node)
            except Exception:
                pass
        if not want:
            return
        from pivy import coin
        for k, (o, dark, cut) in want.items():
            if k in dimmed:
                continue
            try:
                vo = o.ViewObject
                c = tuple(vo.ShapeAppearance[0].DiffuseColor[:3]) if hasattr(vo, "ShapeAppearance") \
                    else tuple(vo.ShapeColor[:3])
                mat = coin.SoMaterial()
                mat.diffuseColor.setValue(*(x * (self.DIM if dark else 1.0) for x in c))
                if cut:
                    mat.transparency.setValue(self.SEE_THROUGH)
                mat.setOverride(True)
                root = vo.RootNode
                root.insertChild(mat, 0)
                dimmed[k] = (root, mat, (dark, cut))
            except Exception:
                log.logger().exception("dim %s while sketching", o.Name)

    def close_all(self):
        """Leaving InCad: finish any sketch edit and restore every sketch's settings."""
        import FreeCADGui as Gui
        self.cancel_pick()
        gdoc = Gui.ActiveDocument
        if self.editing() is not None and gdoc is not None:
            close_edit(gdoc)
        for key in list(self._saved_vp):
            self.restore(key)
        self._sync_dimmed(None)
        self._sync_text_lines(None)

    def finish(self):
        import FreeCAD as App
        import FreeCADGui as Gui
        from incad.ui import shell as shells
        sh = shells.instance()
        fs = sh.feature_session if sh is not None else None
        if fs is not None and getattr(fs.kind, "IN_SKETCH", False):
            fs.cancel()  # the pattern dialog cannot outlive its sketch
        gdoc = Gui.ActiveDocument
        if gdoc is not None and gdoc.getInEdit() is not None:
            close_edit(gdoc)
        if App.ActiveDocument is not None:
            App.ActiveDocument.recompute()
        self._changed()

    @staticmethod
    def editing():
        """The sketch being edited, or None."""
        import FreeCADGui as Gui
        gdoc = Gui.ActiveDocument
        vp = gdoc.getInEdit() if gdoc is not None else None
        obj = getattr(vp, "Object", None)
        return obj if sketch.is_sketch(obj) else None



PICK_DATUM_SCALE = 350.0  # percent; FreeCAD's 100 draws the origin planes a few mm on a new part
_VIEW = "User parameter:BaseApp/Preferences/View"


def _set_datum_scale(scale):
    """Draw datums (the origin planes) at `scale` percent, never smaller than the user's own setting; returns
    the user's value to restore ("" when they had none)."""
    import FreeCAD as App
    g = App.ParamGet(_VIEW)
    old = g.GetFloat("DatumScale", -1.0)
    g.SetFloat("DatumScale", max(scale, old))
    return "" if old < 0 else old


def _restore_datums(old):
    import FreeCAD as App
    g = App.ParamGet(_VIEW)
    if old == "":
        g.RemFloat("DatumScale")
    else:
        g.SetFloat("DatumScale", old)


def screen_box(view, view_objects):
    """World bounding box of view objects as drawn now (origin planes keep a fixed size on screen, so their
    ViewObject.getBoundingBox, taken without the real viewport, is wrong)."""
    import FreeCAD as App
    from pivy import coin
    viewer = view.getViewer()
    manager = viewer.getSoRenderManager()
    box = App.BoundBox()
    for vo in view_objects:
        sa = coin.SoSearchAction()
        sa.setNode(vo.RootNode)
        sa.setInterest(coin.SoSearchAction.FIRST)
        sa.apply(manager.getSceneGraph())
        if sa.getPath() is None:
            continue
        ba = coin.SoGetBoundingBoxAction(manager.getViewportRegion())
        ba.apply(sa.getPath())
        bb = ba.getBoundingBox()
        if not bb.isEmpty():
            box.add(App.BoundBox(App.Vector(*bb.getMin().getValue()), App.Vector(*bb.getMax().getValue())))
    return box


def _is_origin_plane(obj):
    return getattr(obj, "TypeId", "") in ("App::Plane", "App::Origin")


def _face_under(doc_name, pnt):
    """(feature, 'FaceN') of the model under the screen spot of a 3D pick point, or None. A pick made from code
    (no point) is taken as it is."""
    import FreeCAD as App
    import FreeCADGui as Gui
    p = App.Vector(*pnt) if pnt is not None else App.Vector()
    if p.Length < 1e-9:
        return None
    try:
        view = Gui.getDocument(doc_name).ActiveView
        x, y = view.getPointOnViewport(p)
        hits = view.getObjectsInfo((int(round(x)), int(round(y)))) or []
    except Exception:
        return None
    doc = App.getDocument(doc_name)
    for h in hits:  # nearest first
        comp = h.get("Component", "") or ""
        obj = doc.getObject(h.get("Object", "") or "")
        if obj is None or _is_origin_plane(obj):
            continue
        support, face = SketchSession._resolve(obj, comp)
        if face.startswith("Face") and not _is_origin_plane(support) and sketch.valid_support(support, face):
            return support, face
    return None


def _has_solid(body):
    try:
        shape = body.Shape
        return not shape.isNull() and bool(shape.Solids)
    except Exception:
        return False


def _frame_planes(body, view):
    """Origin in the middle of the view, the origin planes big enough to see and click and the whole part in view;
    returns the user's DatumScale to restore after the pick.

    FreeCAD draws datums a fixed share of the view whatever the zoom (at PICK_DATUM_SCALE they reach about half
    the view height from the origin, measured on 1.1), so the zoom only has to fit the part."""
    import FreeCAD as App
    from incad.core import camera
    radius = 0.0
    if not body.Shape.isNull():
        bb = body.Shape.BoundBox
        radius = max(App.Vector(x, y, z).Length for x in (bb.XMin, bb.XMax) for y in (bb.YMin, bb.YMax)
                     for z in (bb.ZMin, bb.ZMax))
    old = _set_datum_scale(PICK_DATUM_SCALE)
    camera.frame(view.getCameraNode(), (0, 0, 0), max(40.0, radius * 2.3))
    return old


def _show_fresh(vos, view):
    for v in vos:
        try:
            v.Visibility = False
            v.Visibility = True
        except Exception:
            pass
    _redraw(view)


def _redraw(view):
    try:
        view.redraw()
    except Exception:
        pass
