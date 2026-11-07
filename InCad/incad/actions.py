"""InCad's stage-1a commands and their handlers. FreeCAD is imported inside handlers, so this module also
loads in widget tests."""
from incad.core import prefs
from incad.core.commands import REGISTRY, Command

STD_COMMANDS = {
    "file.open": "Std_Open", "file.save": "Std_Save", "file.save_as": "Std_SaveAs",
    "file.close": "Std_CloseActiveWindow", "edit.undo": "Std_Undo", "edit.redo": "Std_Redo",
    "view.zoom_all": "Std_ViewFitAll", "view.home": "Std_ViewHome",
    "view.style_shaded": "Std_DrawStyle", "view.style_wireframe": "Std_DrawStyle",
    "view.look_at": "Std_AlignToSelection", "view.zoom_selection": "Std_ViewFitSelection",
}
STD_INDEX = {"view.style_shaded": 0, "view.style_wireframe": 2}  # Std_DrawStyle items: 0 As is (each Solid its own: Hide Edges), 2 Wireframe
SKETCHER = {  # InCad id: (label, icon, FreeCAD Sketcher command, shortcut)
    "sketch.line": ("Line", "line", "Sketcher_CreatePolyline", "L"),  # polyline: chains, arcs with M
    "sketch.circle": ("Circle", "circle", "Sketcher_CreateCircle", "C"),
    "sketch.arc": ("3-Point Arc", "arc", "Sketcher_CreateArc", "A"),
    "sketch.rectangle": ("Rectangle", "rectangle", "Sketcher_CreateRectangle", "R"),
    "sketch.slot": ("Center-to-Center Slot", "slot", "Sketcher_CreateSlot", ""),
    "sketch.polygon": ("Hexagon", "polygon", "Sketcher_CreateHexagon", ""),
    "sketch.spline": ("Spline", "spline", "Sketcher_CreateBSplineByInterpolation", ""),
    "sketch.point": ("Point", "sk_point", "Sketcher_CreatePoint", ""),
    "sketch.fillet": ("Fillet", "sk_fillet", "Sketcher_CreateFillet", ""),
    "sketch.chamfer": ("Chamfer", "sk_chamfer", "Sketcher_CreateChamfer", ""),
    "sketch.trim": ("Trim", "trim", "Sketcher_Trimming", "X"),
    "sketch.extend": ("Extend", "extend", "Sketcher_Extend", ""),
    "sketch.project": ("Project Geometry", "project", "Sketcher_Projection", ""),
    "sketch.dimension": ("Dimension", "dimension", "Sketcher_Dimension", "D"),
    "sketch.coincident": ("Coincident", "coincident", "Sketcher_ConstrainCoincidentUnified", ""),
    "sketch.concentric": ("Concentric", "concentric", "Sketcher_ConstrainCoincident", ""),  # two circles / arcs /
    # ellipses: their centres tied (FreeCAD's coincident tool on conics; Coincident did it too, unknown to anyone)
    "sketch.horizontal": ("Horizontal", "horizontal", "Sketcher_ConstrainHorizontal", ""),
    "sketch.vertical": ("Vertical", "vertical", "Sketcher_ConstrainVertical", ""),
    "sketch.parallel": ("Parallel", "parallel", "Sketcher_ConstrainParallel", ""),
    "sketch.perpendicular": ("Perpendicular", "perpendicular", "Sketcher_ConstrainPerpendicular", ""),
    "sketch.tangent": ("Tangent", "tangent", "Sketcher_ConstrainTangent", ""),
    "sketch.equal": ("Equal", "equal", "Sketcher_ConstrainEqual", ""),
    "sketch.fix": ("Fix", "fix", "Sketcher_ConstrainLock", ""),
    "sketch.show_dof": ("Show Free Geometry", "show_dof", "Sketcher_SelectElementsWithDoFs", ""),
    "sketch.stop": ("Stop Tool", "stop", "Sketcher_StopOperation", ""),
    "sketch.slice": ("Slice Graphics", "slice", "Sketcher_ViewSection", "F7"),
}

# Variants (Inventor split buttons). Modes are FreeCAD's construction methods, set in its tool widget after start
# (FreeCAD remembers the last method, so every variant sets its own).
SKETCH_VARIANTS = {  # InCad id: (label, icon, FreeCAD command, construction method or None)
    "sketch.rectangle_center": ("Center Rectangle", "rect_center", "Sketcher_CreateRectangle", "Center, width, height"),
    "sketch.rectangle_3pt": ("3-Point Rectangle", "rect_3pt", "Sketcher_CreateRectangle", "3 corners"),
    "sketch.rectangle_center_3pt": ("3-Point Center Rect.", "rect_center_3pt", "Sketcher_CreateRectangle",
                                    "Center, 2 corners"),
    "sketch.rectangle_rounded": ("Rounded Rectangle", "rect_rounded", "Sketcher_CreateOblong", None),
    "sketch.circle_3pt": ("3-Point Circle", "circle_3pt", "Sketcher_CreateCircle", "3 rim points"),
    "sketch.ellipse": ("Ellipse", "ellipse", "Sketcher_CreateEllipseByCenter", None),
    "sketch.ellipse_3pt": ("3-Point Ellipse", "ellipse_3pt", "Sketcher_CreateEllipseBy3Points", None),
    "sketch.arc_center": ("Center Arc", "arc_center", "Sketcher_CreateArc", "Center"),
    "sketch.arc_ellipse": ("Elliptical Arc", "arc_ellipse", "Sketcher_CreateArcOfEllipse", None),
    "sketch.line_single": ("Single Line", "line_single", "Sketcher_CreateLine", None),
    "sketch.triangle": ("Triangle", "triangle", "Sketcher_CreateTriangle", None),
    "sketch.square": ("Square", "square", "Sketcher_CreateSquare", None),
    "sketch.pentagon": ("Pentagon", "pentagon", "Sketcher_CreatePentagon", None),
    "sketch.octagon": ("Octagon", "octagon", "Sketcher_CreateOctagon", None),
    "sketch.polygon_n": ("Polygon (N sides)", "polygon_n", "Sketcher_CreateRegularPolygon", None),
    "sketch.slot_arc": ("Arc Slot", "slot_arc", "Sketcher_CreateArcSlot", "Arc ends"),
    "sketch.slot_arc_flat": ("Flat-End Arc Slot", "slot_arc_flat", "Sketcher_CreateArcSlot", "Flat ends"),
    "sketch.spline_cv": ("Control-Vertex Spline", "spline_cv", "Sketcher_CreateBSpline", None),
}
# The base tools pin their own method too (FreeCAD would reuse the last one chosen).
_BASE_MODES = {"sketch.rectangle": "Corner, width, height", "sketch.circle": "Center", "sketch.arc": "3 rim points"}


def set_tool_mode(text):
    """Pick a construction method in the running Sketcher tool's widget (hidden while InCad is active)."""
    from incad.qt import QtWidgets
    for w in QtWidgets.QApplication.allWidgets():
        if isinstance(w, QtWidgets.QComboBox) and w.objectName() == "comboBox1" and w.findText(text) >= 0:
            w.setCurrentIndex(w.findText(text))
            return True
    raise ValueError("This FreeCAD version has no '%s' option for this tool" % text)


def set_tool_check(text, on):
    """Tick or clear a check box of the running Sketcher tool's widget by its label (FreeCAD adds the key:
    "Rounded corners (U)"); False when it has none."""
    from incad.qt import QtWidgets
    for w in QtWidgets.QApplication.allWidgets():
        if isinstance(w, QtWidgets.QCheckBox) and w.text().replace("&", "").startswith(text):
            if w.isChecked() != bool(on):
                w.setChecked(bool(on))
            return True
    return False


# FreeCAD's Rectangle and Rounded Rectangle are one tool whose "Rounded corners" and "Frame" boxes it remembers: after
# one Rounded Rectangle every plain Rectangle came out rounded (the user's air: "everything has to be sharp")
_RECT_CHECKS = {"Sketcher_CreateRectangle": {"Rounded corners": False, "Frame": False},
                "Sketcher_CreateOblong": {"Rounded corners": True, "Frame": False}}


def run_sketch_tool(name, mode=None):
    from incad.ui import shell
    if shell.instance() is not None:  # Project Geometry: a pick on another Solid is copied (FreeCAD can't link it)
        shell.instance().projecting = name == "Sketcher_Projection"
    run_std(name)
    if mode:
        set_tool_mode(mode)
    for text, on in _RECT_CHECKS.get(name, {}).items():
        set_tool_check(text, on)
    from incad.ui import rectangle_preview
    rectangle_preview.start(name)  # a rectangle's diagonals while it is drawn


def _gui():
    import FreeCADGui
    return FreeCADGui


def break_link():
    """Sketch ▸ Break Link: the projected edges picked (or under the right-click) become the sketch's own geometry of
    their kind, no longer following the part (then Trim cuts them)."""
    from incad.core import log, safe, sketch_trim
    from incad.ui import shell
    from incad.ui.tangent_circle_tool import from_subname
    inst = shell.instance()
    sk = inst.sketches.editing() if inst is not None else None
    if sk is None:
        raise ValueError("Open a sketch first")
    picked = [sub for sel in _gui().Selection.getSelectionEx(sk.Document.Name) if sel.Object is sk
              for sub in sel.SubElementNames]
    geos = [g for g in (from_subname(s) for s in picked) if g is not None and g <= -3]
    if not geos:
        raise ValueError("Break Link: right-click a projected edge (one taken from the part)")
    r = safe.run_safely("Break Link", lambda: sketch_trim.break_link(sk, geos), sk.Document, only=[sk])
    if not r.ok:
        raise ValueError(r.error)
    _gui().Selection.clearSelection()
    log.report("info", "%d projected edge%s now the sketch's own: it no longer follows the part" % (
        len(geos), "" if len(geos) == 1 else "s"))


def tangent_circle():
    """Sketch ▸ Tangent Circle: the command, then clicks on the three lines, arcs or circles the circle touches
    (incad.ui.tangent_circle_tool); two or three selected before it make the circle at once."""
    from incad.ui import shell
    inst = shell.instance()
    if inst is None:
        raise ValueError("InCad is not active")
    inst.start_tangent_circle_tool()


def run_std(name, index=0):
    if name.startswith("Sketcher_"):
        from incad.ui import rectangle_preview
        rectangle_preview.stop()  # another Sketcher tool: no rectangle's diagonals over its edit curve
        from incad.ui import trim_watch
        trim_watch.stop()
    _gui().runCommand(name, index)
    if name == "Sketcher_Trimming":  # a projected edge is trimmed too (FreeCAD's Trim leaves it alone)
        from incad.ui import shell, trim_watch
        inst = shell.instance()
        sk = inst.sketches.editing() if inst is not None else None
        if sk is not None:
            trim_watch.start(sk)


def ask_new():
    """Show the New dialog; (kind, units, remember) or None when cancelled."""
    from incad.ui import theme
    from incad.ui.new_dialog import NewDialog
    dlg = NewDialog(units=prefs.default_units(), remember=prefs.remember_new_choice(),
                    theme=theme.current(), parent=_gui().getMainWindow())
    return dlg.choice() if dlg.exec() else None


def _toggle_ask_on_new():
    from incad.core import log
    prefs.set_remember_new_choice(not prefs.remember_new_choice())
    log.report("info", "New asks Part or Assembly" if not prefs.remember_new_choice()
               else "New makes a %s without asking" % prefs.new_kind())


def new_part(ask=None, part_only=False):
    """Create an InCad Part in a new document. ask() -> (kind, units, remember) | None (default: the New
    dialog). With 'Remember my choice' on, nothing is asked and the saved units are used. part_only: a part even
    when an Assembly is the remembered kind (Start 2D Sketch with no part open)."""
    import FreeCAD as App
    from incad.core import document
    kind = "part"
    if prefs.remember_new_choice():
        units = prefs.default_units()
        kind = "part" if part_only else prefs.new_kind()  # Part or Assembly, as chosen with 'Remember my choice'
    else:
        answer = (ask or ask_new)()
        if answer is None:
            return None
        kind, units, remember = answer
        prefs.set_default_units(units)
        prefs.set_new_kind(kind)
        prefs.set_remember_new_choice(remember)
    if kind == "assembly":
        return new_assembly(units)
    doc = App.newDocument("Part")
    doc.UndoMode = 1
    body = document.make_part(doc, units)
    doc.recompute()
    doc.clearUndos()
    if App.GuiUp:
        view = _gui().getDocument(doc.Name).ActiveView
        view.setActiveObject("pdbody", body)
        view.viewIsometric()
        from incad.core import camera
        camera.frame_origin(view.getCameraNode())
    return doc


def toggle_theme():
    from incad.ui import shell, theme
    prefs.set_theme(theme.toggled(theme.current()))
    shell.apply_theme()


def return_to_freecad():
    _gui().activateWorkbench(prefs.last_workbench())


def open_parameters():
    from incad.ui import shell
    shell.open_parameters()


def _target_object():
    import FreeCAD as App
    from incad.core import context
    doc = App.ActiveDocument
    name = context.target()
    obj = doc.getObject(name) if doc is not None and name else None
    if obj is None:
        raise ValueError("Pick a feature in the Model Browser first")
    return obj


def toggle_suppress():
    """The picked feature on or off, with the pieces InCad made for it (a thread's fill and chamfer); its profile
    sketch or one of those pieces means the feature."""
    from incad.core import suppress
    suppress.toggle(_target_object())


def break_mirror_link():
    """A mirrored Solid stops following its original (it keeps its shape); a feature Mirror keeps what it made."""
    from incad.core import log
    from incad.features import solid_mirror
    f = _target_object()
    if not solid_mirror.is_linked(f):
        raise ValueError("Break Mirror Link: pick a Mirror Solid or a Mirror row")
    kept = solid_mirror.break_link(f)
    log.report("info", "%s keeps its shape and no longer follows the original" % kept.Label)


def toggle_constraint_suppressed():
    """A constraint row's Suppress / Unsuppress (Inventor): the solver ignores it, the parts stay."""
    from incad.core import log
    from incad.features import constraints
    j = _target_object()
    on = constraints.toggle_suppressed(j)
    log.report("info", "%s %s" % (j.Label, "suppressed" if on else "back on"))


def _threads_off():
    import FreeCAD as App
    from incad.core import suppress
    return App.ActiveDocument is not None and suppress.threads_off(App.ActiveDocument)


def toggle_assembly_threads():
    """Threads Off / On from an assembly: in every part file it links to (a part made into an assembly with its
    threads off gets them back here)."""
    import FreeCAD as App
    from incad.core import assembly, log
    _refuse_while_busy()
    state = assembly.toggle_threads(App.ActiveDocument)
    log.report("info", ("Threads off" if state == "off" else "Threads on") + " in the parts (save them with the assembly)")


def _assembly_threads_off():
    import FreeCAD as App
    from incad.core import assembly
    return App.ActiveDocument is not None and assembly.is_assembly(App.ActiveDocument) and \
        assembly.threads_off(App.ActiveDocument)


def toggle_threads():
    """Every thread off for faster work, or back on (a thread that was already off stays off)."""
    import FreeCAD as App
    from incad.core import log, suppress
    if App.ActiveDocument is None:
        raise ValueError("Open a part first")
    state = suppress.toggle_threads(App.ActiveDocument)
    log.report("info", "Threads off" if state == "off" else "Threads on")


def _visibility_targets():
    """What Hide / Show acts on: the model's selection (a tree click and a 3D pick both set it), else the row
    the menu was opened on, else the tree's selected rows."""
    import FreeCAD as App
    import FreeCADGui as Gui
    from incad.core import context
    from incad.ui import shell
    doc = App.ActiveDocument
    if doc is None:
        raise ValueError("Open a part first")
    from incad.features import common
    objs = []
    for s in common.selection_ex(doc.Name):  # in an assembly a pick is 'Link.Body.Face5' on the assembly
        for sub in (s.SubElementNames or ("",)):
            obj = common.resolve(s.Object, sub)[0] if sub else s.Object
            if obj is not None and obj not in objs:
                objs.append(obj)
    if not objs:
        inst = shell.instance()
        names = [context.target()] if context.target() else []
        if not names and inst is not None:
            names = inst.browser.selected_names()
        objs = [o for o in (doc.getObject(n) for n in names) if o is not None]
    return objs


def _visibility_step(label, objects, change):
    """One undo step for a visibility change: the targets get their InCadShown first (outside the step, holding
    the state they have now), so undo restores exactly that state."""
    import FreeCAD as App
    from incad.core import document, safe
    from incad.ui import shell
    inst = shell.instance()
    if inst is not None:
        inst.end_peek()  # the temporary "shown while selected" ends: the change acts on the real state
    document.prepare_choice(objects)
    if safe.busy() or safe.edit_open():  # a dialog or an edit is open: showing and hiding is only looking, never
        value = change()                 # refused (it was: "Finish the Extrude dialog first"); no undo step of its
        if inst is not None:             # own then, the open step owns the transaction
            inst.schedule_refresh()
        return value
    r = safe.run_safely(label, change, App.ActiveDocument)
    if not r.ok:
        raise ValueError(r.error)
    if inst is not None:
        inst.schedule_refresh()
    return r.value


def toggle_dof():
    from incad.ui import shell
    shell.toggle_dof()


def dof_shown():
    from incad.ui import shell
    return shell.dof_shown()


def toggle_assembly_context():
    from incad.ui import shell
    shell.toggle_assembly_context()


def return_to_assembly():
    from incad.ui import shell
    shell.return_to_assembly()


def assembly_context_shown():
    from incad.ui import shell
    return shell.assembly_context_shown()


def toggle_visibility():
    from incad.core import document, log
    objects = _visibility_targets()
    flag = _visibility_step("Hide / Show", objects, lambda: document.toggle_visibility(objects))
    log.report("info", "Shown" if flag else "Hidden")


def _component_docs(doc):
    """The part files an assembly's components come from (each once)."""
    from incad.core import assembly
    out = []
    for link in assembly.components(doc):
        part = getattr(getattr(link, "LinkedObject", None), "Document", None)
        if part is not None and part not in out:
            out.append(part)
    return out


def set_shown(flag):
    """Hide / Show, said outright (the user: Undo is no way back — a Hide button and a Show button): the picked
    objects (a part's Solid, an assembly's component, a sketch, a plane…) hidden or shown, one undo step."""
    from incad.core import document, log
    targets = document.visibility_targets(_visibility_targets())
    _visibility_step("Show" if flag else "Hide", targets, lambda: document.set_visibility(targets, flag))
    log.report("info", "%s %s" % ("Shown:" if flag else "Hidden:", ", ".join(o.Label for o in targets[:4])))


def show_all_parts():
    """Show All: every Solid of the part, every component of the assembly, back on the view — nothing to pick (a
    hidden part can't be clicked)."""
    import FreeCAD as App
    from incad.core import document, log
    doc = App.ActiveDocument
    if doc is None:
        raise ValueError("Open a part or an assembly first")
    items = [o for o in document.all_parts(doc) if not document.is_shown(o)]
    if not items:
        log.report("info", "Everything is shown already")
        return
    _visibility_step("Show All", items, lambda: document.set_visibility(items, True))
    log.report("info", "Shown: %s" % ", ".join(o.Label for o in items[:6]) + ("…" if len(items) > 6 else ""))


def origin_visibility(flag):
    import FreeCAD as App
    from incad.core import assembly, document
    doc = App.ActiveDocument
    is_asm = doc is not None and assembly.is_assembly(doc)
    if not is_asm and (doc is None or document.primary_body(doc) is None):
        raise ValueError("Open a part first")
    # every Solid's origin (a part with Solid1 and Solid2: only the first one's ever changed); in an assembly its own
    # origin and its parts' (the user: Hide / Show in assemblies too) — a part's origin is hidden in that part's file
    docs = [doc] + (_component_docs(doc) if is_asm else [])
    items = [f for d in docs for o in d.Objects if o.TypeId == "App::Origin" for f in o.OriginFeatures]
    _visibility_step("Show All Origin" if flag else "Hide All Origin", items,
                     lambda: document.set_visibility(items, flag))
    if flag:
        _bring_into_view(items)


def sketches_visibility(flag):
    """Show All / Hide All Sketches: every sketch, Text and 3D Line of every Solid (not the ones InCad keeps for itself: a
    Hole's centre, an area's copy, a Sweep wall's profile), the user's choice for each, one undo step."""
    import FreeCAD as App
    from incad.core import document, regions
    from incad.core import assembly
    doc = App.ActiveDocument
    is_asm = doc is not None and assembly.is_assembly(doc)
    if not is_asm and (doc is None or document.primary_body(doc) is None):
        raise ValueError("Open a part first")
    docs = _component_docs(doc) if is_asm else [doc]  # an assembly: its parts' sketches, set in their files
    items = [o for d in docs for o in d.Objects
             if document.is_sketch_like(o) and document.body_of(o) is not None
             and not getattr(o, "InCadHelperOf", None) and not regions.is_profile(o)
             and not hasattr(o, "InCadPicks")]  # a Hole's centre sketch
    if not items:
        raise ValueError("The assembly's parts have no sketches" if is_asm else "The part has no sketches")
    _visibility_step("Show All Sketches" if flag else "Hide All Sketches", items,
                     lambda: document.set_visibility(items, flag))


def axes_visibility(flag):
    """Show / Hide All Axes in an assembly (the user: the white axis lines can be hidden); remembered."""
    from incad.core import log
    from incad.ui import shell
    prefs.set_assembly_axes(flag)
    inst = shell.instance()
    if inst is not None:
        inst.schedule_refresh()
        inst.dof_display_changed()
    log.report("info", "Axes shown" if flag else "Axes hidden")


def _bring_into_view(items):
    """The origin is at 0,0,0 however far the part was drawn from it: when it is off screen (the view on the part),
    the view zooms out to show both — Show All looked like it did nothing."""
    try:
        Gui = _gui()
        view = Gui.ActiveDocument.ActiveView
        vp = view.graphicsView().viewport()
        w, h = vp.width() * vp.devicePixelRatioF(), vp.height() * vp.devicePixelRatioF()
        for o in items:
            if o.Role != "Origin":
                continue
            x, y = view.getPointOnViewport(o.getGlobalPlacement().Base)
            if not (0 <= x <= w and 0 <= y <= h):
                Gui.SendMsgToActiveView("ViewFit")
                return
    except Exception:
        from incad.core import log
        log.logger().exception("bring the origin into view")


def _ask_name(current):
    from incad.qt import QtWidgets
    text, ok = QtWidgets.QInputDialog.getText(_gui().getMainWindow(), "Rename", "New name:", text=current)
    return text.strip() if ok else ""


def rename(ask=None):
    obj = _target_object()
    name = (ask or _ask_name)(obj.Label)
    if name:
        obj.Label = name


def move_end_of_part_here():
    from incad.core import document
    obj = document.owning_feature(_target_object())
    if document.body_of(obj) is None:
        raise ValueError("%s is not part of a solid" % obj.Label)
    document.set_end_of_part_at(obj.Document, obj)


def show_in_browser():
    from incad.ui import shell
    shell.instance().show_in_browser()


def show_in_window():
    from incad.ui import shell
    shell.instance().show_in_window(_target_object().Name)


def edit_solid():
    from incad.ui import shell
    shell.instance().edit_solid(_target_object().Name)


def edit_picked_feature():
    """Inventor's Edit Feature on a face or edge picked in the view: the feature that made it opens for editing (the
    user: a right-click on the lid had no Edit)."""
    import FreeCAD as App
    import FreeCADGui as Gui
    from incad.core import context, feature_faces
    doc = App.ActiveDocument
    for sel in Gui.Selection.getSelectionEx(doc.Name) if doc is not None else []:
        for sub in sel.SubElementNames:
            obj, name = sel.Object, sub
            if "." in sub:  # 'Body' + 'Hollow.Face3'
                parts = sub.split(".")
                obj, name = doc.getObject(parts[-2]) or obj, parts[-1]
            if name.startswith(("Face", "Edge")) and obj.isDerivedFrom("PartDesign::Feature"):
                context.set_target(feature_faces.creator(obj, name).Name)
                return edit_feature()
    raise ValueError("Right-click a face or an edge of the part")


def edit_feature():
    from incad.core import log, sketch
    obj = _target_object()
    owner = getattr(obj, "InCadHelperOf", None)  # a sweep's wall, a thread's chamfer: the owner's dialog
    if owner and obj.Document.getObject(owner) is not None:
        obj = obj.Document.getObject(owner)
    if obj.TypeId in ("App::Line", "App::Point"):
        from incad.core import log
        log.report("info", "Double-click a plane to start a sketch on it")
        return
    if obj.TypeId == "App::Plane":  # double-click an origin plane: a new sketch on it (Inventor); a work plane
        # opens its dialog (the user: only the first plane of a row could be turned), New Sketch is on right-click
        from incad.ui import shell
        shell.sketch_session().start_on(obj)
        return
    if obj.TypeId == "App::Link":
        from incad.ui import shell
        shell.open_component(obj)
        return
    if sketch.is_sketch(obj):
        _refuse_while_busy()
        from incad.ui import shell
        shell.sketch_session().edit(obj)
    else:
        from incad import features
        from incad.ui import shell
        if features.kind_for(obj) is None:
            log.report("info", "Editing this feature arrives with the next feature tools (plan 1d-2)")
        else:
            shell.edit_feature(obj)


def toggle_nav_mode(mode):
    from incad.ui import shell
    shell.toggle_nav_mode(mode)


def nav_mode_is(mode):
    from incad.ui import shell
    return shell.nav_mode() == mode


_PLANES = ("App::Plane", "PartDesign::Plane")


def start_sketch():
    _refuse_while_busy()
    import FreeCAD as App
    from incad.core import context, document
    from incad.ui import shell
    if not document.is_incad_part(App.ActiveDocument):
        made = new_part(part_only=True)  # nothing open (or a plain FreeCAD file in front): a new part first
        if made is None or not document.is_incad_part(made):  # the New dialog was cancelled, or made an assembly
            return None
        return shell.sketch_session().start()
    name = context.target()
    target = App.ActiveDocument.getObject(name) if App.ActiveDocument is not None and name else None
    if target is not None and target.TypeId in _PLANES and not _gui().Selection.getSelection():
        context.set_target(None)  # right-click New Sketch on a plane in the browser
        return shell.sketch_session().start_on(target)
    return shell.sketch_session().start()


def finish_sketch():
    from incad.ui import shell
    shell.sketch_session().finish()


def finish_dialog():
    from incad.ui import shell
    shell.instance().finish_dialog()


def start_line3d():
    """3D Line, from the 3D Model tab or from inside a sketch (the Sketch tab has it too, the user: a 3D Line is a kind
    of sketch): an open sketch is finished first."""
    from incad.ui import shell
    session = shell.sketch_session()
    if session is not None and session.editing() is not None:
        finish_sketch()
    start_feature("line3d")


def start_feature(kind_name):
    from incad import features
    from incad.ui import shell
    kind = {k.__name__.rsplit(".", 1)[-1]: k for k in features.kinds()}[kind_name]
    shell.start_feature(kind)


def make_assembly():
    """The part's pieces become part files and an assembly (names asked first); the assembly opens."""
    import FreeCAD as App
    import FreeCADGui as Gui
    import Part
    from incad.qt import QtWidgets
    from incad.core import make_assembly as ma
    from incad.ui.make_assembly_dialog import MakeAssemblyDialog
    from incad.ui.piece_marker import PieceMarker
    _refuse_while_busy()  # an open dialog's preview would be written into every copy
    doc = App.ActiveDocument
    found = ma.pieces_or_refuse(doc)
    asm, names = ma.default_names(doc, found)
    rows = [(n, "%.0f mm³" % p.volume) for n, p in zip(names, found)]
    dlg = MakeAssemblyDialog(asm, rows, lambda a, ns, replace=False: ma.check_names(doc, a, ns, replace),
                             Gui.getMainWindow(), existing=lambda a, ns: ma.existing(doc, a, ns))
    view = Gui.getDocument(doc.Name).ActiveView
    marker = PieceMarker(view)
    solids = []
    for p in found:
        body = doc.getObject(p.body_name)
        probe = Part.Vertex(p.point)
        solids.append(min(body.Shape.Solids, key=lambda s: s.distToShape(probe)[0]))
    dlg.hovered.connect(lambda i: marker.show(solids[i] if i >= 0 else None))
    try:
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return None
    finally:
        marker.remove()
    asm_name, part_names = dlg.names()
    replace = dlg.replace()
    if replace:  # a file about to be replaced may be open with changes: ask before it is closed unsaved
        import os
        from incad.ui import shell
        paths = {os.path.abspath(os.path.join(os.path.dirname(doc.FileName), n + ".FCStd"))
                 for n in ma.existing(doc, asm_name, part_names)}
        changed = [d.Label for d in App.listDocuments().values() if d.FileName and os.path.abspath(d.FileName) in paths
                   and Gui.getDocument(d.Name) is not None and Gui.getDocument(d.Name).Modified]
        if changed and not shell.confirm("Make Assembly", "%s %s open with unsaved changes. Close without saving and "
                                         "replace?" % (", ".join(changed), "is" if len(changed) == 1 else "are")):
            return None
    gdoc = Gui.getDocument(doc.Name)
    was_modified = gdoc.Modified
    try:
        path = ma.make(doc, asm_name, part_names, replace)
    finally:
        Gui.ActiveDocument = gdoc  # the hidden copies took the active document while they were open
    if not was_modified:
        gdoc.Modified = False  # writing the copies (saveCopy) marks the part changed; it is not
    App.openDocument(path)
    return path


def _edit_sketch_pattern():
    from incad.ui import shell
    sk, name = shell.instance().selected_sketch_pattern()
    if name is None:
        raise ValueError("Select a copy of a pattern first")
    shell.instance().edit_sketch_pattern(sk, name)


def _delete_sketch_pattern():
    from incad.ui import shell
    sk, name = shell.instance().selected_sketch_pattern()
    if name is None:
        raise ValueError("Select a copy of a pattern first")
    shell.instance().delete_sketch_pattern(sk, name)


def _inspect(what):
    from incad.ui import shell
    shell.inspect(what)


def inspect_measure():
    from incad.ui import shell
    shell.inspect("measure")


def inspect_mass():
    from incad.ui import shell
    shell.inspect("mass")


def look_at():
    """Inventor Look At: the selection; while sketching with nothing selected, the sketch itself."""
    gdoc = _gui().ActiveDocument
    editing = gdoc.getInEdit() if gdoc is not None else None
    if editing is not None and not _gui().Selection.getSelection():
        run_std("Sketcher_ViewSketch")
    else:
        run_std("Std_AlignToSelection")


def _editing_sketch():
    gdoc = _gui().ActiveDocument
    obj = gdoc.getInEdit() if gdoc is not None else None
    obj = getattr(obj, "Object", None)
    if obj is None or obj.TypeId != "Sketcher::SketchObject":
        raise ValueError("Open a sketch first")
    return obj


def _construction():
    from incad.ui import shell
    shell.toggle_construction()


def _construction_on():
    from incad.ui import shell
    inst = shell.instance()
    return bool(inst is not None and getattr(inst, "construction_mode", False))


def mirror_sketch():
    """Sketch ▸ Mirror (incad.core.sketch_mirror, incad.ui.mirror_tool): select the shapes, Mirror, click the mirror
    line (as FreeCAD's tool had it); with the mirror line already selected last (an axis, a point, a line apart from
    the shapes) the copy is made at once."""
    from incad.ui import shell
    inst = shell.instance()
    if inst is None:
        raise ValueError("InCad is not active")
    inst.start_mirror_tool()


def _text_tool():
    from incad.ui import shell
    inst = shell.instance()
    if inst is None:
        raise ValueError("InCad is not active")
    inst.start_text_tool()


def _offset_tool():
    from incad.ui import shell
    inst = shell.instance()
    if inst is None:
        raise ValueError("InCad is not active")
    inst.start_offset_tool()


def _symmetric_tool():
    from incad.ui import shell
    inst = shell.instance()
    if inst is None:
        raise ValueError("InCad is not active")
    inst.start_symmetric_tool()


def _overall_dim_tool():
    from incad.ui import shell
    inst = shell.instance()
    if inst is None:
        raise ValueError("InCad is not active")
    inst.start_overall_dim_tool()


def _slot(mode):
    from incad.ui import shell
    shell.start_slot_tool(mode)


def open_constraints_window():
    from incad.ui import shell
    _editing_sketch()  # "Open a sketch first"
    shell.open_constraints_window()


def constraints_visible(show=None):
    """Inventor F8/F9: show or hide the constraint symbols of the sketch being edited (dimensions stay)."""
    from incad.core import safe, sketch
    sk = _editing_sketch()
    hide = sketch.constraints_hidden(sk) is False if show is None else not show
    safe.run_safely("Constraints", lambda: sketch.set_constraints_hidden(sk, hide), sk.Document)


def quit_freecad():
    """Quit FreeCAD: open dialogs are cancelled and InCad asks about unsaved files (in the main window's close)."""
    run_std("Std_Quit")


EXPORT_FILTERS = (("step", "STEP (*.step *.stp)"), ("stl_normal", "STL, Normal (*.stl)"),
                  ("stl_fine", "STL, Fine (*.stl)"))
EXPORT_ASK = None  # tests: callable(doc) -> (path, export type) or (None, None)
THREADS_ASK = None  # tests: callable() -> "on", "without" or "cancel"


def _ask_export_path(doc):
    """The Save dialog: the file type (STEP, STL Normal / Fine) picks the format; the last folder and type again."""
    import os
    import FreeCADGui as Gui
    from incad.qt import QtWidgets
    kind = prefs.export_type()
    folder = prefs.export_folder()
    if not os.path.isdir(folder):
        folder = os.path.dirname(doc.FileName) if doc.FileName else os.path.expanduser("~")
    filters = dict(EXPORT_FILTERS)
    path, chosen = QtWidgets.QFileDialog.getSaveFileName(
        Gui.getMainWindow(), "Export %s" % doc.Label,
        os.path.join(folder, doc.Label + (".step" if kind == "step" else ".stl")),
        ";;".join(f for _, f in EXPORT_FILTERS), filters[kind])
    if not path:
        return None, None
    kind = next((k for k, f in EXPORT_FILTERS if f == chosen), kind)
    if not os.path.splitext(path)[1]:
        path += ".step" if kind == "step" else ".stl"
    return path, kind


def _ask_threads():
    import FreeCADGui as Gui
    from incad.qt import QtWidgets
    box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Question, "Export",
                                "Threads Off is holding the threads off. Turn them on for the export? "
                                "(They go back off afterwards.)", QtWidgets.QMessageBox.NoButton, Gui.getMainWindow())
    on = box.addButton("Turn On for Export", QtWidgets.QMessageBox.AcceptRole)
    without = box.addButton("Export Without Threads", QtWidgets.QMessageBox.ActionRole)
    box.addButton(QtWidgets.QMessageBox.Cancel)
    box.setDefaultButton(on)
    box.exec()
    return "on" if box.clickedButton() is on else "without" if box.clickedButton() is without else "cancel"


SCREENSHOT_ASK = None  # tests: callable() -> (path, transparent), instead of the Save dialog
SHOT_TYPES = (".png", ".jpg", ".jpeg", ".bmp")


def screenshot(path=None, transparent=False, window=False):
    """View ▸ Screenshot (the user: the part on screen saved as a picture, for a web site): the 3D view as it is now —
    the part, its colours, the view's background (or none: a PNG with a transparent background) — at the screen's
    full resolution (twice the window's size on a Retina screen). window: the whole window with its ribbon, browser
    and status bar (the user: with the toolbars too). PNG, JPEG or BMP; returns the file's path."""
    import os
    import FreeCAD as App
    from incad.core import log
    gdoc = _gui().ActiveDocument
    if gdoc is None or App.ActiveDocument is None:
        raise ValueError("Open a part or an assembly first")
    view = gdoc.ActiveView
    if path is None:
        path, transparent = (SCREENSHOT_ASK or _ask_screenshot_path)(App.ActiveDocument, window)
        if not path:
            return None
    if os.path.splitext(path)[1].lower() not in SHOT_TYPES:
        path += ".png"
    if transparent and not path.lower().endswith(".png"):
        transparent = False  # only a PNG keeps a transparent background
    vp = view.graphicsView().viewport()
    dpr = vp.devicePixelRatioF()
    w, h = int(round(vp.width() * dpr)), int(round(vp.height() * dpr))
    if window:
        w, h = _window_shot(path, view, vp, dpr)
    else:
        view.saveImage(path, w, h, "Transparent" if transparent else "Current")
    if hasattr(prefs, "set_last_folder"):
        prefs.set_last_folder(path)
    log.report("info", "Saved the %s as %s (%d × %d)" % ("window" if window else "view", os.path.basename(path), w, h))
    return path


def screenshot_window():
    return screenshot(window=True)


def _window_shot(path, view, vp, dpr):
    """The whole window into `path`, the 3D view drawn into it: a grab of the window has no picture where an OpenGL
    view is (black), so the view is rendered on its own and painted in its place. Returns (width, height)."""
    import os
    import tempfile
    from incad.qt import QtCore, QtGui
    mw = _gui().getMainWindow()
    shot = mw.grab().toImage()
    tmp = os.path.join(tempfile.gettempdir(), "incad_window_view_%d.png" % os.getpid())
    try:
        view.saveImage(tmp, int(round(vp.width() * dpr)), int(round(vp.height() * dpr)), "Current")
        part = QtGui.QImage(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    at = vp.mapTo(mw, QtCore.QPoint(0, 0))
    p = QtGui.QPainter(shot)  # the grab keeps the screen's pixel ratio: the painter works in window px
    p.drawImage(QtCore.QRectF(at.x(), at.y(), vp.width(), vp.height()), part)
    for w in _floating_over(mw, vp):  # what floats over the view (its buttons, an open dialog) back on top
        p.drawPixmap(w.mapTo(mw, QtCore.QPoint(0, 0)), w.grab())
    p.end()
    if not shot.save(path):
        raise ValueError("Could not save %s" % os.path.basename(path))
    return shot.width(), shot.height()


def _floating_over(mw, vp):
    """The visible widgets laid over the 3D view (children of it or of the widgets holding it), outermost ones only."""
    from incad.qt import QtCore, QtWidgets
    holders = []
    w = vp
    while w is not None and w is not mw:
        holders.append(w)
        w = w.parentWidget()
    area = vp.rect().translated(vp.mapTo(mw, vp.rect().topLeft()))
    out = []
    for holder in holders:
        for child in holder.findChildren(QtWidgets.QWidget, options=QtCore.Qt.FindDirectChildrenOnly):
            if child in holders or not child.isVisible() or child.isWindow() or isinstance(child, QtWidgets.QMdiSubWindow):
                continue  # (another file's window over this one: its 3D view grabs black — the user's crank shot)
            box = child.rect().translated(child.mapTo(mw, child.rect().topLeft()))
            if box.intersects(area) and not any(o.isAncestorOf(child) for o in out):
                out.append(child)
    return out


def _ask_screenshot_path(doc, window=False):
    import os
    from incad.qt import QtWidgets
    folder = prefs.last_folder() if hasattr(prefs, "last_folder") else os.path.expanduser("~")
    name = os.path.join(folder, (doc.Label or "view") + ".png")
    clear = "" if window else "PNG, transparent background (*.png);;"  # a whole window has no background to leave
    filters = "PNG (*.png);;" + clear + "JPEG (*.jpg *.jpeg);;BMP (*.bmp)"
    path, chosen = QtWidgets.QFileDialog.getSaveFileName(_gui().getMainWindow(),
                                                         "Screenshot (Window)" if window else "Screenshot", name, filters)
    return path, chosen.startswith("PNG, transparent")


def export_file():
    """File ▸ Export: the part (every visible Solid) or the assembly (its visible components) to STEP or STL."""
    import os
    import FreeCAD as App
    from incad.core import export, log
    _refuse_while_busy()
    doc = App.ActiveDocument
    if doc is None:
        raise ValueError("Open a part first")
    export.shapes(doc)  # nothing to export: said before any dialog
    threads = "without"
    if export.threads_held_off(doc):
        threads = (THREADS_ASK or _ask_threads)()
        if threads == "cancel":
            return None
    path, kind = (EXPORT_ASK or _ask_export_path)(doc)
    if not path:
        return None
    if export.file_format(path) == "step":
        fineness = "normal"
    else:
        fineness = "fine" if kind == "stl_fine" else "normal"
    prefs.set_export_folder(os.path.dirname(path))
    prefs.set_export_type(kind if kind in prefs.EXPORT_TYPES else "step")
    with _waiting():
        if threads != "on":
            facets = export.export(doc, path, fineness)
        else:
            facets = _with_threads_on(doc, lambda: export.export(doc, path, fineness))
    log.report("info", "Exported %s to %s%s" % (doc.Label, path, " (%d triangles)" % facets if facets else ""))
    return path


def _with_threads_on(doc, fn):
    """fn() with the threads Threads Off holds off turned on, then off again: the files end unchanged."""
    import FreeCADGui as Gui
    from incad.core import export
    docs = export.changed_documents(doc)
    modified = {d.Name: Gui.getDocument(d.Name).Modified for d in docs if Gui.getDocument(d.Name) is not None}
    export.toggle_threads(doc)
    try:
        return fn()
    finally:
        export.toggle_threads(doc)
        for d in docs:
            gdoc = Gui.getDocument(d.Name)
            if gdoc is not None and d.Name in modified:
                gdoc.Modified = modified[d.Name]
        from incad.ui import shell
        if shell.instance() is not None:
            shell.instance().refresh()


class _waiting:
    def __enter__(self):
        from incad.qt import QtCore, QtGui, QtWidgets
        QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(QtCore.Qt.WaitCursor))

    def __exit__(self, *exc):
        from incad.qt import QtWidgets
        QtWidgets.QApplication.restoreOverrideCursor()


def delete_target():
    """Delete with one undo step in every document it changes (opened before anything changes)."""
    from incad.core import delete, safe
    obj = delete.target_for(_target_object())  # a Split piece's base means its Solid
    doc = obj.Document
    from incad.ui import shell
    if shell.sketch_session().editing() is obj:
        shell.sketch_session().finish()  # the edit would be left half-reset on a deleted object
    kw = {}
    from incad.features import split
    if split.claims(obj) and split.piece_bodies(obj):
        names = ", ".join(b.Label for b in split.piece_bodies(obj))
        if not shell.confirm("Delete", "Deleting %s joins its pieces again: %s go (a Solid with work on it, or one a "
                                       "Boolean uses, stays with its piece). Delete?" % (obj.Label, names)):
            return
    if obj.TypeId == "PartDesign::Body":  # Inventor asks instead of refusing
        from incad.core import document
        users = delete.users_of(obj)
        if users:
            names = ", ".join(f.Label for f in users)
            if not shell.confirm("Delete", "%s is used by %s (Boolean). Delete %s too?" % (obj.Label, names, names)):
                return
            kw["with_users"] = True
        nxt = delete.main_successor(obj) if obj is document.primary_body(doc) else None
        if nxt is not None:
            if not shell.confirm("Delete", "%s is the main solid. Delete it? %s becomes the main solid; solids split "
                                           "from it keep their shape." % (obj.Label, nxt.Label)):
                return
            kw["replace_main"] = True
    extras = delete.documents(obj)
    for d in extras:
        if not d.UndoMode:
            d.UndoMode = 1
        if d.HasPendingTransaction:
            d.abortTransaction()
        d.openTransaction("Delete")
    res = safe.run_safely("Delete", lambda: delete.delete(obj, **kw), doc, quiet_broken=not extras)
    from incad.core import log
    victims, told = [obj], False
    for _round in range(5):  # what was built on it broke: ask, then delete it too (Inventor) — as one undo step
        if res.ok or not res.broken or extras:
            break
        more = [doc.getObject(n) for n in res.broken]
        more = [o for o in more if o is not None and o not in victims and o.TypeId != "PartDesign::Body"]
        if not more:
            break
        names = ", ".join(o.Label for o in more)
        if not shell.confirm("Delete", "%s is built on %s: without it, it fails. Delete %s too?"
                                       % (names, ", ".join(o.Label for o in victims), names)):
            log.report("info", "Nothing deleted")
            told = True
            break
        victims += more
        res = safe.run_safely("Delete", lambda: delete.delete_all(victims), doc, quiet_broken=True)
    if not res.ok and res.broken and not extras and not told:
        log.report("error", res.error)  # undone, and nothing more to offer
    for d in extras:
        if res.ok:
            d.commitTransaction()
        else:
            d.abortTransaction()
            d.recompute()


def _sketch_selection(sk):
    """[(geo, pos, sub)] of the sketch elements selected now, dropping names that point past the geometry the
    sketch has (a stale selection after a pattern rebuild made FreeCAD's own delete crash)."""
    from incad.core import sketch_patterns as sp
    out = []
    for sel in _gui().Selection.getSelectionEx(sk.Document.Name):
        if sel.Object is not sk:
            continue
        for sub in sel.SubElementNames or ():
            try:
                geo, pos = sp.element(sk, sub)
            except (ValueError, IndexError):
                out.append((None, 0, sub))  # a dimension ('Constraint7'): the Sketcher's delete handles it
                continue
            if geo >= sk.GeometryCount or geo < -2 - len(sk.ExternalGeo):
                continue
            out.append((geo, pos, sub))
    return out


def _delete_in_sketch(sk):
    """Delete in a sketch: a pattern's copies and spokes go through InCad (the pattern is then broken and rebuilt
    on its next edit); everything else through the Sketcher's own delete, with a selection cleaned of stale names."""
    from incad.core import safe, sketch_patterns as sp
    picked = _sketch_selection(sk)
    mine = sorted({geo for geo, _, _ in picked if geo is not None and geo >= 0 and sp.pattern_of(sk, geo)
                   and sp.index_of(sk, sp.getGeometryId(sk, geo)) is not None and not sp.is_original(sk, geo)}, reverse=True)
    rest = [sub for geo, _, sub in picked if geo not in mine]
    if mine:
        r = safe.run_safely("Delete", lambda: [sk.delGeometry(g) for g in mine], sk.Document)
        if not r.ok:
            raise ValueError(r.error)
        sk.Document.recompute()
    _gui().Selection.clearSelection()
    if rest:
        for sub in rest:
            _gui().Selection.addSelection(sk.Document.Name, sk.Name, sub)
        run_std("Std_Delete")


def delete_selection():
    """Delete / Backspace: in a sketch the selected geometry and dimensions (Sketcher's own delete, whose key
    died with FreeCAD's hidden Edit menu); otherwise the selected objects, or the model-tree row in focus."""
    from incad.core import context
    from incad.ui import shell
    sk = shell.sketch_session().editing()
    if sk is not None:
        _delete_in_sketch(sk)
        return
    from incad.features import common
    names = []
    for sel in common.selection_ex():
        for sub in (sel.SubElementNames or ("",)):
            obj, _ = common.resolve(sel.Object, sub)  # a face of the body names its feature ("Body.Thread.Face5")
            if obj is not None and obj.Name not in names:
                names.append(obj.Name)
    if not names:
        row = shell.current_tree_object()
        names = [row] if row else []
    if not names:
        raise ValueError("Select what to delete (in the model or the Model Browser)")
    for name in names:
        context.set_target(name)
        delete_target()


def _appearance_targets(doc):
    """{Solid: [face indices] (none: the whole Solid)} — the picked faces, else the picked Solids (a feature, a row
    the menu was opened on), else every Solid of the part."""
    from incad.core import context, document
    from incad.features import common
    targets = {}
    for s in common.selection_ex(doc.Name):
        for sub in (s.SubElementNames or ("",)):
            obj, elt = common.resolve(s.Object, sub) if sub else (s.Object, "")
            body = document._solid_of(obj) if obj is not None else None
            if body is None or body.TypeId != "PartDesign::Body":
                continue
            faces = targets.setdefault(body, [])
            if elt.startswith("Face") and elt[4:].isdigit() and faces is not None:
                faces.append(int(elt[4:]) - 1)
            else:
                targets[body] = None  # the whole Solid
    if not targets and context.target():
        body = doc.getObject(context.target())
        body = document._solid_of(body) if body is not None else None
        if body is not None and body.TypeId == "PartDesign::Body":
            targets[body] = None
    if not targets:
        targets = {b: None for b in document.bodies(doc) if not b.Shape.isNull()}
    return {b: (f or []) for b, f in targets.items()}


def appearance(choose=None):
    """Tools ▸ Material ▸ Appearance (the user: "rendering, or at least colour"; "Hide Edges"): colour, finish and
    edges of the picked Solids or faces (nothing picked: every Solid), shown as chosen, one undo step on OK.
    choose(dialog) -> True / False: the test's (or a caller's) choices instead of the user's."""
    import FreeCAD as App
    from incad.core import appearance as looks, document, log, safe
    from incad.ui import shell
    from incad.ui.appearance_dialog import AppearanceDialog
    _refuse_while_busy()
    doc = App.ActiveDocument
    if doc is None or not document.is_incad_part(doc):
        raise ValueError("Open a part first (a part's Solids and faces get a colour)")
    targets = _appearance_targets(doc)
    if not targets:
        raise ValueError("The part has no Solid to colour yet")
    nfaces = sum(len(f) for f in targets.values())
    names = ", ".join(b.Label for b in targets)
    title = ("%d face%s of %s" % (nfaces, "" if nfaces == 1 else "s", names)) if nfaces else names
    first = next(iter(targets))
    rec = looks.record(first) or {}
    color, finish = rec.get("color") or looks.material_color(doc), rec.get("finish") or looks.DEFAULT_FINISH
    if nfaces:
        faces = first.Shape.Faces
        e = looks.entry_at(rec, faces, targets[first][0], looks._tolerance(first.Shape)) if targets[first] else None
        if e:
            color, finish = e["color"], e.get("finish") or finish
    edges = [(looks.record(b) or {}).get("edges", True) for b in targets]
    from incad.core import textures
    before = {b: (list(b.ViewObject.ShapeAppearance), b.ViewObject.DisplayMode, textures.name_on(b)) for b in targets}
    s = shell.instance()
    dlg = AppearanceDialog(title, color, finish, edges[0], len(set(edges)) > 1,
                           s.theme if s is not None else "light", _gui().getMainWindow(), rec.get("texture") or "None")

    def records():
        hide = dlg.hide_edges()
        out = {}
        for b, faces in targets.items():
            out[b] = looks.change(b, faces, dlg.color if dlg.color_chosen else None,
                                  dlg.finish if dlg.finish_chosen else None,
                                  None if hide is None else not hide, reset=dlg.reset_asked,
                                  texture=dlg.texture if dlg.texture_chosen else None)
        return out

    def preview():
        for b, r in records().items():
            mats, edges_on = looks.wanted(b, r or {})
            looks.draw(b, mats, edges_on, (r or {}).get("texture"))

    def put_back():
        for b, (mats, mode, texture) in before.items():
            try:
                b.ViewObject.ShapeAppearance = mats
                b.ViewObject.DisplayMode = mode
                textures.put(b, texture)
            except Exception:
                pass

    dlg.changed.connect(preview)
    ok = choose(dlg) if choose is not None else dlg.exec() == AppearanceDialog.Accepted
    final = records() if ok else None
    put_back()
    if not ok:
        return None

    def run():
        for b, r in final.items():
            looks.set_record(b, r)
            looks.apply(b, force=True)
    r = safe.run_safely("Appearance", run, doc)
    if not r.ok:
        raise ValueError(r.error)
    log.report("info", "Appearance: %s" % title)
    return final


def choose_material(ask=None):
    """Pick the part's material (density for Mass Properties, colour). ask() -> name | None."""
    import FreeCAD as App
    from incad.core import document, materials, safe
    doc = App.ActiveDocument
    if not document.is_incad_part(doc):
        raise ValueError("Open an InCad part first")
    if ask is None:
        from incad.qt import QtWidgets
        names = materials.names()
        name, ok = QtWidgets.QInputDialog.getItem(_gui().getMainWindow(), "Material", "Part material:", names,
                                                  names.index(materials.material(doc)), False)
        ask = (lambda: name if ok else None)
    name = ask()
    if name:
        safe.run_safely("Material", lambda: materials.set_material(doc, name), doc)




def toggle_section():
    """Section view at the selection — three points, a flat face or a plane (XZ with nothing selected);
    clicking Section again switches it off."""
    from incad.core import section
    if _gui().ActiveDocument is None:
        raise ValueError("Open a part to see a section")
    from incad.ui import shell
    pairs = _section_picks()
    if section_on() and not pairs:  # a plane picked while sectioned: the section moves there
        shell.stop_section()
        return
    base = section.placement(pairs)  # raises when the picks make no plane
    _gui().Selection.clearSelection()
    shell.start_section(base)


def _section_picks():
    """The selected points, flat faces and planes that can make a section plane."""
    import FreeCAD as App
    from incad.core import section
    from incad.features import common
    return section.picked([common.resolve(sel.Object, sub)
                           for sel in _gui().Selection.getSelectionEx(App.ActiveDocument.Name)
                           for sub in (sel.SubElementNames or ("",))])


def _section_points():
    """Section by 3 Points; with a face, a plane or three points selected it cuts there at once (the split button
    shows "Section" for both: the user's picked plane was passed over for the markers)."""
    from incad.ui import shell
    if _gui().ActiveDocument is not None and _section_picks():
        toggle_section()
        return
    shell.start_section_points()


def section_on():
    """Is the part shown sectioned (the cut solid drawn instead of it)?"""
    from incad.ui import shell
    return shell.section_on()


def _refuse_while_busy():
    """An open feature dialog owns the model: undo, save, close or sketch editing would break its Cancel."""
    from incad.core import safe
    if safe.busy():
        safe.refused()
        raise ValueError("Finish the %s dialog first" % safe.busy())


_GUARDED = {"file.save", "file.save_as"}


def close_document():
    """⌘W: InCad's Save dialog for this file, then close it."""
    import FreeCAD as App
    from incad.ui import shell
    doc = App.ActiveDocument
    if doc is None:
        return
    shell._finish_open_tools()  # an open dialog is cancelled, as in Inventor when its file closes
    if shell.ask_save([doc], "closing"):
        App.closeDocument(doc.Name)


def new_assembly(units=None):
    """Create an InCad Assembly in a new document (saved when the first part is placed)."""
    import FreeCAD as App
    from incad.core import assembly
    doc = App.newDocument("Assembly")
    doc.UndoMode = 1
    assembly.make_assembly(doc, units or prefs.default_units())
    doc.recompute()
    doc.clearUndos()
    if App.GuiUp:
        view = _gui().getDocument(doc.Name).ActiveView
        view.viewIsometric()
        from incad.core import camera
        camera.frame_origin(view.getCameraNode())
    return doc


def _assembly(action):
    from incad.ui import shell
    return getattr(shell, "assembly_" + action)()


SAVE_ASM_ASK = None  # tests: (doc, default path) -> the path chosen, instead of the Save dialog


def save_assembly(doc, as_new=False):
    """Save / Save As of an assembly (the user: a folder named after it at the place chosen, the assembly and all its
    parts inside): a new assembly or Save As asks where and under what name; an assembly saved elsewhere or with parts
    outside its folder is gathered into a folder of its name beside it; one already gathered just saves."""
    import os
    from incad import host
    from incad.core import log, pack
    from incad.qt import QtWidgets
    if not as_new and pack.packed(doc):
        pack.save(doc, doc.FileName)
        _gui().getDocument(doc.Name).Modified = False
        log.report("info", "Saved %s and its parts" % doc.Label)
        return doc.FileName
    path = doc.FileName if (doc.FileName and not as_new) else None
    if path is None:
        default = os.path.join(prefs.export_folder() or os.path.expanduser("~"), doc.Label + ".FCStd")
        if SAVE_ASM_ASK is not None:
            path = SAVE_ASM_ASK(doc, default)
        else:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                _gui().getMainWindow(), "Save Assembly %s — a folder of this name holds it and its parts" % doc.Label,
                default, "FreeCAD files (*.FCStd)")
        if not path:
            return None
        if not path.lower().endswith(".fcstd"):
            path += ".FCStd"
        prefs.set_export_folder(os.path.dirname(path))
    folder, copied = pack.save(doc, path)
    for d in [doc] + pack.part_docs(doc):
        g = _gui().getDocument(d.Name)
        if g is not None:
            g.Modified = False
    log.report("info", "Saved %s in the folder %s with its %d parts%s" % (
        doc.Label, folder, len(pack.part_docs(doc)),
        (" (copied there: %s)" % ", ".join(label for label, _ in copied[:5])) if copied else ""))
    return doc.FileName


def _std(cmd_id, label, icon, shortcut=""):
    name, index = STD_COMMANDS[cmd_id], STD_INDEX.get(cmd_id, 0)

    def handler():
        if cmd_id in _GUARDED:
            _refuse_while_busy()
        if cmd_id in ("file.save", "file.save_as"):
            import FreeCAD as App
            from incad.core import assembly
            doc = App.ActiveDocument
            if doc is not None and assembly.is_assembly(doc):
                return save_assembly(doc, as_new=cmd_id == "file.save_as")
        result = run_std(name, index)
        if cmd_id in ("view.style_shaded", "view.style_wireframe"):
            import FreeCAD as App
            from incad.ui import theme, wire_style
            wire_style.set_wireframe(App.ActiveDocument, cmd_id == "view.style_wireframe", theme.current())
        return result
    return Command(id=cmd_id, label=label, icon=icon, handler=handler, shortcut=shortcut)


def _linked_steps(doc, step, redo):
    """A dialog that changed several files (assembly cuts) left one step in each, which FreeCAD marks "-> name":
    undo/redo them together."""
    import FreeCAD as App
    from incad.core import model
    base = step[3:] if step.startswith("-> ") else step
    for other in App.listDocuments().values():
        if other is doc:
            continue
        names = other.RedoNames if redo else other.UndoNames
        if names and (names[0] == "-> " + base or (step.startswith("-> ") and names[0] == base)):
            other.redo() if redo else other.undo()
            other.recompute()
            model.relink(other)


def update_model():
    """Inventor's Update: recompute the model and redraw InCad's panels and the view (a model that got out of
    step with the tree, or a view that stopped following the document, is put right)."""
    import FreeCAD as App
    from incad.core import log
    from incad.ui import shell
    _refuse_while_busy()
    doc = App.ActiveDocument
    if doc is not None:
        from incad.core import assembly, document
        if assembly.is_assembly(doc):  # parts edited in their tabs come in; the 'needs update' sign goes
            assembly.bring_parts_in(doc)
            if shell.instance() is not None:
                shell.instance().assembly_refreshed(doc)
        doc.recompute()
        for b in document.bodies(doc):  # the display follows the tip again (a view that drifted is put right)
            document.show_result(b)
        bad = [o.Label for o in doc.Objects if "Invalid" in o.State]
        log.report("warning" if bad else "info", "Updated %s: %s failed" % (doc.Label, ", ".join(bad)) if bad
                   else "Updated %s" % doc.Label)
    else:
        log.report("warning", "Nothing to update: no document is active")
    inst = shell.instance()
    if inst is not None:
        inst.refresh()
    gdoc = _gui().ActiveDocument
    view = gdoc.ActiveView if gdoc is not None else None
    if view is not None:
        try:
            view.redraw()
        except Exception:
            pass


def _undo_redo(redo):
    """Undo/Redo the active document. Sketcher's own command while a sketch is edited; otherwise straight on the
    document, so it works whatever tab is active (FreeCAD's Std_Undo talks to the active view)."""
    import FreeCAD as App
    from incad.ui import shell
    sh = shell.instance()
    fs = sh.feature_session if sh is not None else None
    if fs is not None and fs.dialog is not None:  # inside a dialog: step through its own changes
        fs.undo_in_dialog(redo)
        return
    _refuse_while_busy()
    gdoc = _gui().ActiveDocument
    if gdoc is not None and gdoc.getInEdit() is not None:
        run_std("Std_Redo" if redo else "Std_Undo")
        return
    doc = App.ActiveDocument
    if doc is None:
        return
    names = doc.RedoNames if redo else doc.UndoNames
    step = names[0] if names else None
    if step is not None:
        doc.redo() if redo else doc.undo()
        _linked_steps(doc, step, redo)
    doc.recompute()
    from incad.core import document, log
    try:
        document.apply_visibility(doc)  # Hide / Show choices restored by the step show again (Visibility is not recorded)
    except Exception as e:
        log.logger().warning("Visibility after undo/redo: %s", e)
    from incad.core import model
    repaired = model.relink(doc)  # objects brought back by undo lose their formula links (FreeCAD quirk)
    log.logger().info("undo/redo relinked %s", repaired)


OPEN_ASK = None  # tests: callable() -> path, instead of the dialog


def _openable():
    """[(name, patterns)]: FreeCAD files, STEP and STL — not FreeCAD's long list of formats (the user: only what can be
    opened)."""
    return [("FreeCAD files", "*.FCStd *.fcstd"), ("STEP", "*.step *.stp *.STEP *.STP"), ("STL", "*.stl *.STL")]


def open_filters():
    kinds = _openable()
    every = " ".join(p for _, p in kinds)
    names = ", ".join(n for n, _ in kinds)
    return ";;".join(["%s (%s)" % (names, every)] + ["%s (%s)" % k for k in kinds])

def open_file():
    """File ▸ Open with a picture of the file beside the list (incad.ui.open_dialog); opened as FreeCAD's Open does
    (Recent Files, other types by their import module)."""
    import os
    import FreeCAD as App
    if OPEN_ASK is not None:
        path = OPEN_ASK()
    else:
        from incad.ui import open_dialog
        path, _ = open_dialog.get_open_file(_gui().getMainWindow(), "Open", App.ParamGet(
            "User parameter:BaseApp/Preferences/General").GetString("FileOpenSavePath", ""), open_filters())
    if not path:
        return None
    App.ParamGet("User parameter:BaseApp/Preferences/General").SetString("FileOpenSavePath", os.path.dirname(path))
    return open_path(path)


def open_path(path):
    """Open a file as File ▸ Open does (the start page's recent files too): shown if it is open already."""
    import os
    import FreeCAD as App
    if not os.path.isfile(path):
        raise ValueError("%s is no longer there" % os.path.basename(path))
    for d in App.listDocuments().values():
        if d.FileName and os.path.abspath(d.FileName) == os.path.abspath(path):
            _gui().setActiveDocument(d.Name)  # already open: shown, not opened twice
            return d
    if path.lower().endswith(".fcstd"):
        App.openDocument(path)
        _remember_recent(path)
    else:
        _gui().open(path)  # another type: by its import module
    return App.ActiveDocument


def _remember_recent(path):
    """First in FreeCAD's Recent Files (its Open adds the file there; App.openDocument does not)."""
    import os
    import FreeCAD as App
    p = App.ParamGet("User parameter:BaseApp/Preferences/RecentFiles")
    from incad.ui import start_page
    most = max(p.GetInt("RecentFiles", 4) or 4, start_page.MOST)  # the start page shows a dozen
    if p.GetInt("RecentFiles", 4) < most:
        p.SetInt("RecentFiles", most)
    old = [p.GetString("MRU%d" % i, "") for i in range(most)]
    files = [path] + [f for f in old if f and os.path.abspath(f) != os.path.abspath(path)]
    for i, f in enumerate(files[:most]):
        p.SetString("MRU%d" % i, f)


# ----- Picture tab (the user: a tab of its own for the reference picture and its scaling) -----
def _picture():
    """The image the Image tab acts on: the selected one, else the last one inserted."""
    import FreeCAD as App
    from incad.core import image_reference
    try:
        obj = _target_object()
    except Exception:
        obj = None
    if image_reference.is_picture(obj):
        return obj
    found = image_reference.pictures(App.ActiveDocument)
    if not found:
        raise ValueError("Insert an image first (Image ▸ Insert Image)")
    return found[-1]


def insert_picture():
    import os
    import FreeCAD as App
    import FreeCADGui as Gui
    from incad.core import image_reference
    from incad.ui import open_dialog
    doc = App.ActiveDocument
    folder = prefs.import_folder() if hasattr(prefs, "import_folder") else ""
    if not os.path.isdir(folder):
        folder = os.path.dirname(doc.FileName) if doc is not None and doc.FileName else os.path.expanduser("~")
    pats = " ".join("*%s *%s" % (e, e.upper()) for e in image_reference.IMAGES)
    path, _ = open_dialog.get_open_file(Gui.getMainWindow(), "Insert Image", folder, "Images (%s)" % pats)
    return insert_picture_at(path) if path else None


def insert_picture_at(path):
    """The picture into the open part (a new part, named after it, when none is open)."""
    import os
    import FreeCAD as App
    from incad.core import document, image_reference, log, safe
    _refuse_while_busy()
    if hasattr(prefs, "set_import_folder"):
        prefs.set_import_folder(os.path.dirname(path))
    doc = App.ActiveDocument
    if doc is None or not document.is_incad_part(doc):
        doc = new_part(part_only=True)
        if doc is None:
            return None  # the New dialog was cancelled
        doc.Label = os.path.splitext(os.path.basename(path))[0]
    r = safe.run_safely("Insert Image", lambda: image_reference.add(doc, path), doc)
    if not r.ok:
        raise ValueError(r.error)
    if App.GuiUp:
        _gui().SendMsgToActiveView("ViewFit")
    log.report("info", "%s: an image on the XY plane, %g × %g mm. Next: Image ▸ Scale / Transparency"
               % (os.path.basename(path), round(r.value.XSize.Value, 1), round(r.value.YSize.Value, 1)))
    return r.value


def scale_picture():
    from incad.ui import shell
    _refuse_while_busy()
    shell.edit_feature(_picture())


def _picture_step(label, change):
    from incad.core import safe
    pic = _picture()
    r = safe.run_safely(label, lambda: (change(pic), pic.Document.recompute()), pic.Document)
    if not r.ok:
        raise ValueError(r.error)


def picture_on(plane):
    from incad.core import image_reference
    _picture_step("Image on %s" % plane.upper(), lambda pic: image_reference.put_on(pic, plane))


def picture_turn():
    from incad.core import image_reference
    _picture_step("Turn Image", lambda pic: image_reference.turn(pic, 90))


def picture_mirror():
    from incad.core import image_reference
    _picture_step("Mirror Image", image_reference.mirror)


def pictures_show_hide():
    import FreeCAD as App
    from incad.core import document, image_reference
    found = image_reference.pictures(App.ActiveDocument)
    if not found:
        raise ValueError("Insert an image first (Image ▸ Insert Image)")
    show = not any(p.Visibility for p in found)
    for p in found:
        document._set_visible(p, show)


def commands():
    return [
        Command(id="file.new", label="New", icon="new", handler=new_part, shortcut="Ctrl+N",
                tooltip="New part (asks for mm or inch)"),
        Command(id="file.open", label="Open", icon="open", handler=open_file, shortcut="Ctrl+O"),
        _std("file.save", "Save", "save", "Ctrl+S"),
        _std("file.save_as", "Save As", "save", "Ctrl+Shift+S"),
        Command(id="file.export", label="Export", icon="export", handler=export_file, shortcut="Ctrl+E",
                tooltip="Export the part or assembly to STEP (other CAD) or STL (3D printing)"),
        Command(id="file.close", label="Close", icon="close", handler=close_document, shortcut="Ctrl+W",
                tooltip="Close this file (asks to save changes)"),
        Command(id="file.quit", label="Quit", icon="close", handler=lambda: quit_freecad(), shortcut="Ctrl+Q",
                tooltip="Quit FreeCAD (asks to save changed files)"),
        Command(id="edit.undo", label="Undo", icon="undo", handler=lambda: _undo_redo(False), shortcut="Ctrl+Z"),
        Command(id="edit.redo", label="Redo", icon="redo", handler=lambda: _undo_redo(True), shortcut="Ctrl+Shift+Z"),
        Command(id="edit.update", label="Update", icon="update", handler=update_model,
                tooltip="Recompute the model and refresh the browser and the view"),
        Command(id="view.theme_toggle", label="Light / Dark", icon="theme_toggle", handler=toggle_theme,
                tooltip="Switch between light and dark"),
        Command(id="view.freecad", label="Return to FreeCAD", icon="freecad", handler=return_to_freecad,
                tooltip="Show FreeCAD's own interface"),
        _std("view.zoom_all", "Zoom All", "zoom_all"),
        _std("view.home", "Home View", "home_view"),
        _std("view.style_shaded", "Shaded", "style_shaded"),
        _std("view.style_wireframe", "Wireframe", "style_wireframe"),
        Command(id="view.parameters", label="Parameters", icon="parameters", handler=open_parameters,
                tooltip="Every parameter in one window: values, formulas, what uses them, Delete Unused"),
        Command(id="view.look_at", label="Look At", icon="look_at", handler=look_at,
                tooltip="Look straight at the selection, or at the sketch being edited"),
        _std("view.zoom_selection", "Zoom Selection", "zoom_selection"),
        Command(id="view.toggle_visibility", label="Hide / Show", icon="visibility", handler=toggle_visibility,
                shortcut="Space", contexts=frozenset({"part", "origin", "browser", "assembly"}),
                tooltip="Hide or show the selected solids (or assembly parts), origin planes, axes, centre point "
                        "and sketches"),
        Command(id="view.dof", label="Degrees of Freedom", icon="rotational", handler=toggle_dof,
                checkable=True, is_checked=dof_shown, contexts=frozenset({"assembly"}),
                tooltip="Show how each part can still move: arrows for slides, arcs for turns"),
        Command(id="view.assembly_context", label="Assembly Context", icon="assembly", handler=toggle_assembly_context,
                checkable=True, is_checked=assembly_context_shown, contexts=frozenset({"part"}),
                tooltip="A part opened with Edit from its assembly: show or hide the other parts around it"),
        Command(id="view.hide", label="Hide", icon="hide_all", handler=lambda: set_shown(False),
                contexts=frozenset({"part", "assembly"}),
                tooltip="Hide the selected Solid, component, sketch or plane (Show or Show All brings it back)"),
        Command(id="view.show", label="Show", icon="visibility", handler=lambda: set_shown(True),
                contexts=frozenset({"part", "assembly"}),
                tooltip="Show the selected Solid, component, sketch or plane (pick a hidden one in the browser)"),
        Command(id="view.show_all_parts", label="Show All", icon="show_all", handler=show_all_parts,
                contexts=frozenset({"part", "assembly"}),
                tooltip="Show every hidden Solid of the part, every hidden component of the assembly"),
        Command(id="view.axes_show_all", label="Show All Axes", icon="show_all",
                handler=lambda: axes_visibility(True), contexts=frozenset({"assembly"}),
                tooltip="Show the axis of every turned body and hole of the assembly's parts"),
        Command(id="view.axes_hide_all", label="Hide All Axes", icon="hide_all",
                handler=lambda: axes_visibility(False), contexts=frozenset({"assembly"}),
                tooltip="Hide the parts' axes (a constraint dialog still shows them while it waits for a pick)"),
        Command(id="view.origin_show_all", label="Show All Origin", icon="show_all",
                handler=lambda: origin_visibility(True), contexts=frozenset({"part", "assembly"}),
                tooltip="Show every origin plane, axis and the centre point"),
        Command(id="view.origin_hide_all", label="Hide All Origin", icon="hide_all",
                handler=lambda: origin_visibility(False), contexts=frozenset({"part", "assembly"}),
                tooltip="Hide every origin plane, axis and the centre point"),
        Command(id="view.sketches_show_all", label="Show All Sketches", icon="show_all",
                handler=lambda: sketches_visibility(True), contexts=frozenset({"part", "assembly"}),
                tooltip="Show every sketch and Text of the part, the ones its features use too"),
        Command(id="view.sketches_hide_all", label="Hide All Sketches", icon="hide_all",
                handler=lambda: sketches_visibility(False), contexts=frozenset({"part", "assembly"}),
                tooltip="Hide every sketch and Text of the part"),
        Command(id="browser.edit", label="Edit", icon="edit", handler=edit_feature, contexts=frozenset({"browser"})),
        Command(id="browser.edit_solid", label="Edit", icon="edit", handler=edit_solid, contexts=frozenset({"browser"}),
                tooltip="Work on this Solid with the others see-through (Esc ends)"),
        Command(id="view.edit_feature", label="Edit Feature", icon="edit", handler=edit_picked_feature,
                contexts=frozenset({"part"}), tooltip="Edit the feature that made this face or edge"),
        Command(id="view.show_in_browser", label="Show in Browser", icon="zoom_selection", handler=show_in_browser,
                tooltip="Open the model tree at what is selected (a face: the feature that made it)"),
        Command(id="browser.show_in_window", label="Show in Window", icon="zoom_selection", handler=show_in_window,
                contexts=frozenset({"browser"}), tooltip="Light up this feature's faces and zoom the view to them"),
        Command(id="browser.move_end_of_part", label="Move End of Part Here", icon="end_of_part",
                handler=move_end_of_part_here, transactional=True, contexts=frozenset({"browser"})),
        Command(id="browser.suppress", label="Suppress / Unsuppress", icon="suppress", handler=toggle_suppress,
                transactional=True, contexts=frozenset({"browser"})),
        Command(id="constraint.suppress", label="Suppress / Unsuppress", icon="suppress",
                handler=toggle_constraint_suppressed, transactional=True, contexts=frozenset({"browser"}),
                tooltip="Turn the constraint off (the parts stay where they are) or back on"),
        Command(id="browser.rename", label="Rename", icon="rename", handler=rename, transactional=True,
                contexts=frozenset({"browser"})),
        Command(id="browser.break_mirror", label="Break Mirror Link", icon="mirror_solid", handler=break_mirror_link,
                transactional=True, contexts=frozenset({"browser"}),
                tooltip="The mirrored copy keeps its shape and no longer follows the original"),
        Command(id="nav.orbit", label="Orbit", icon="orbit", handler=lambda: toggle_nav_mode("orbit"),
                checkable=True, is_checked=lambda: nav_mode_is("orbit"), tooltip="Left-drag orbits; click again to stop"),
        Command(id="nav.pan", label="Pan", icon="pan", handler=lambda: toggle_nav_mode("pan"),
                checkable=True, is_checked=lambda: nav_mode_is("pan"), tooltip="Left-drag pans; click again to stop"),
        Command(id="sketch.start", label="Start 2D Sketch", icon="start_sketch", handler=start_sketch, shortcut="S",
                contexts=frozenset({"part", "empty"}),
                tooltip="Pick a plane or a flat face to sketch on (no part open: a new part first)"),
        Command(id="sketch.finish", label="Finish Sketch", icon="finish", handler=finish_sketch,
                contexts=frozenset({"sketch"}), tooltip="Back to the 3D model (Esc)"),
        Command(id="feature.extrude", label="Extrude", icon="extrude", handler=lambda: start_feature("extrude"),
                shortcut="E", contexts=frozenset({"part"}), tooltip="Extrude a sketch profile"),
        Command(id="feature.revolve", label="Revolve", icon="revolve", handler=lambda: start_feature("revolve"),
                shortcut="R", contexts=frozenset({"part"}), tooltip="Revolve a sketch profile around an axis"),
        Command(id="feature.emboss", label="Emboss", icon="emboss", handler=lambda: start_feature("emboss"),
                contexts=frozenset({"part"}),
                tooltip="Emboss or engrave a Text or a sketch on a face: wrapped round a cylinder, or straight on a "
                        "flat face"),
        Command(id="feature.hole", label="Hole", icon="hole", handler=lambda: start_feature("hole"), shortcut="H",
                contexts=frozenset({"part"})),
        Command(id="feature.fillet", label="Fillet", icon="fillet", handler=lambda: start_feature("fillet"),
                shortcut="F", contexts=frozenset({"part"})),
        Command(id="feature.full_round", label="Full Round Fillet", icon="full_round",
                handler=lambda: start_feature("full_round"), contexts=frozenset({"part"}),
                tooltip="Pick two parallel edges of a face: it becomes one half cylinder, two fillets meeting on it "
                        "(the end of a bar, a plate's side)"),
        Command(id="feature.chamfer", label="Chamfer", icon="chamfer", handler=lambda: start_feature("chamfer"),
                contexts=frozenset({"part"})),
        Command(id="file.new_assembly", label="New Assembly", icon="assembly", handler=lambda: new_assembly(),
                tooltip="New assembly of part files"),
        Command(id="tools.ask_on_new", label="Ask on New", icon="new", handler=lambda: _toggle_ask_on_new(),
                checkable=True, is_checked=lambda: not prefs.remember_new_choice(),
                tooltip="On: New asks Part or Assembly (and the units); off: it makes what you chose last time"),
        Command(id="assembly.place", label="Place", icon="place", handler=lambda: _assembly("place"), shortcut="P",
                contexts=frozenset({"assembly"}), tooltip="Place a part or assembly file"),
        Command(id="assembly.create", label="Create", icon="create_component", handler=lambda: _assembly("create"),
                contexts=frozenset({"assembly"}), tooltip="Create a new part file in this assembly"),
        Command(id="assembly.ground", label="Ground", icon="ground", handler=lambda: _assembly("ground"),
                contexts=frozenset({"assembly"}), tooltip="Ground or unground the selected component"),
        Command(id="assembly.suppress", label="Suppress", icon="suppress", handler=lambda: _assembly("suppress"),
                contexts=frozenset({"assembly"}),
                tooltip="Suppress the selected component (hidden, its constraints off) or bring it back"),
        Command(id="assembly.return", label="Return", icon="finish", handler=return_to_assembly,
                contexts=frozenset({"part"}), tooltip="Save this part and go back to the assembly it was opened from (Edit)"),
        Command(id="assembly.edit", label="Edit", icon="part", handler=lambda: _assembly("edit"),
                contexts=frozenset({"assembly"}),
                tooltip="Edit the selected component's part in its tab, the other parts shown around it; Return "
                        "goes back"),
        Command(id="assembly.interference", label="Interference", icon="interference",
                handler=lambda: __import__("incad.ui.shell", fromlist=["toggle_interference"]).toggle_interference(),
                checkable=True,
                is_checked=lambda: __import__("incad.ui.shell", fromlist=["interference_shown"]).interference_shown(),
                contexts=frozenset({"assembly", "part"}),
                tooltip="Show where parts go into each other: each overlap in bright red over the parts, the pairs and "
                        "their volumes listed; redrawn as parts move. Click again to turn it off"),
        Command(id="assembly.open", label="Open", icon="open", handler=lambda: _assembly("open"),
                contexts=frozenset({"assembly"}),
                tooltip="Open the selected component's part in its own window, as a part of its own (Inventor: Open)"),
        Command(id="assembly.remove", label="Remove from Assembly", icon="delete", handler=lambda: _assembly("remove"),
                contexts=frozenset({"assembly"}),
                tooltip="Take the selected component out of the assembly (its part file stays)"),
        Command(id="assembly.combine", label="Boolean", icon="combine", handler=lambda: start_feature("asm_combine"),
                contexts=frozenset({"assembly"}), tooltip="Cut a component by others (written into its part file)"),
        Command(id="constrain.mate", label="Mate", icon="mate", handler=lambda: start_feature("mate"), shortcut="C",
                contexts=frozenset({"assembly"}), tooltip="Faces against each other (with an offset)"),
        Command(id="constrain.flush", label="Flush", icon="flush", handler=lambda: start_feature("flush"),
                contexts=frozenset({"assembly"}), tooltip="Faces side by side, same direction"),
        Command(id="constrain.angle", label="Angle", icon="angle_c", handler=lambda: start_feature("angle_c"),
                contexts=frozenset({"assembly"})),
        Command(id="constrain.parallel", label="Parallel", icon="parallel_c",
                handler=lambda: start_feature("parallel"),
                contexts=frozenset({"assembly"}), tooltip="Two faces or edges stay parallel; the distance stays free"),
        Command(id="constrain.perpendicular", label="Perpendicular", icon="perpendicular_c",
                handler=lambda: start_feature("perpendicular"), contexts=frozenset({"assembly"}),
                tooltip="Two faces or edges stay at 90°"),
        Command(id="constrain.tangent", label="Tangent", icon="tangent", handler=lambda: start_feature("tangent"),
                contexts=frozenset({"assembly"})),
        Command(id="constrain.insert", label="Insert", icon="insert", handler=lambda: start_feature("insert"),
                contexts=frozenset({"assembly"}), tooltip="Pick two round edges: axes aligned, faces together"),
        Command(id="joint.rigid", label="Rigid", icon="rigid", handler=lambda: start_feature("rigid"), shortcut="J",
                contexts=frozenset({"assembly"})),
        Command(id="joint.rotational", label="Rotational", icon="rotational",
                handler=lambda: start_feature("rotational"), contexts=frozenset({"assembly"})),
        Command(id="joint.slider", label="Slider", icon="slider", handler=lambda: start_feature("slider"),
                contexts=frozenset({"assembly"})),
        Command(id="joint.cylindrical", label="Cylindrical", icon="cylindrical",
                handler=lambda: start_feature("cylindrical"), contexts=frozenset({"assembly"})),
        Command(id="joint.ball", label="Ball", icon="ball", handler=lambda: start_feature("ball"),
                contexts=frozenset({"assembly"})),
        Command(id="feature.combine", label="Boolean", icon="combine", handler=lambda: start_feature("combine"),
                contexts=frozenset({"part"}), tooltip="Join, cut or intersect solids; tools are kept; print clearance"),
        Command(id="file.make_assembly", label="Make Assembly", icon="make_assembly", handler=make_assembly,
                contexts=frozenset({"part"}),
                tooltip="Each separate piece of this part becomes a part file (with its history), linked by a new assembly"),
        Command(id="feature.finish_move", label="Finish move", icon="finish", handler=finish_dialog,
                contexts=frozenset({"part"}), tooltip="Finish the Move Face dialog: the same as OK"),
        Command(id="feature.split", label="Split", icon="split", handler=lambda: start_feature("split"),
                contexts=frozenset({"part"}),
                tooltip="Cut the part along a sketch's lines (made into thin surfaces): keep every piece, or the ones you click"),
        Command(id="feature.move_face", label="Move Face", icon="move_face",
                handler=lambda: start_feature("move_face"), contexts=frozenset({"part"}),
                tooltip="Move flat faces by a distance or up to a face; the neighbours follow (Inventor), stay "
                        "(Step) or move with them (Together)"),
        Command(id="sketch.circle_tangent", label="Tangent Circle", icon="circle_tangent",
                handler=lambda: tangent_circle(), contexts=frozenset({"sketch"}),
                tooltip="A circle tangent to two or three lines, arcs or circles: click them one after another (construction and "
                        "projected ones too); after two, a click away places it touching those two"),
        Command(id="sketch.break_link", label="Break Link", icon="construction", handler=lambda: break_link(),
                contexts=frozenset({"sketch"}),
                tooltip="A projected edge becomes the sketch's own line, no longer following the part (then Trim cuts it)"),
        Command(id="sketch.construction", label="Construction", icon="construction",
                handler=lambda: _construction(), contexts=frozenset({"sketch"}), checkable=True,
                is_checked=lambda: _construction_on(),
                tooltip="On: new geometry is construction; click again for normal geometry. "
                        "With geometry selected: switches that geometry"),
        Command(id="sketch.mirror", label="Mirror", icon="mirror", handler=mirror_sketch, contexts=frozenset({"sketch"}),
                tooltip="Pick the shapes (one click takes a whole outline), then the mirror line last"),
        Command(id="sketch.text", label="Text", icon="text", handler=_text_tool, contexts=frozenset({"sketch"}),
                tooltip="Text: click where it goes, or pick a point or a curve of the sketch first (the text runs "
                        "along it); Extrude raises or engraves it"),
        Command(id="sketch.symmetric", label="Symmetric", icon="symmetric", handler=_symmetric_tool,
                contexts=frozenset({"sketch"}),
                tooltip="Click two points, two lines or two circles / arcs, then the mirror line (an axis, a point); "
                        "the next pair follows, Esc ends"),
        Command(id="sketch.dimension_overall", label="Overall Dimension", icon="dimension_overall",
                handler=_overall_dim_tool, contexts=frozenset({"sketch"}),
                tooltip="Between the outer edges of two arcs or circles: click a slot (its overall length), or two "
                        "arcs or circles; type the value"),
        Command(id="sketch.offset", label="Offset", icon="offset", handler=_offset_tool, contexts=frozenset({"sketch"}),
                tooltip="Click a shape (its whole outline), move to the side, click; type the distance"),
        Command(id="sketch.slot_overall", label="Overall Slot", icon="slot_overall",
                handler=lambda: _slot("overall"), contexts=frozenset({"sketch"}),
                tooltip="Click the two outer ends, then the width"),
        Command(id="sketch.slot_center", label="Center Point Slot", icon="slot_center",
                handler=lambda: _slot("center"), contexts=frozenset({"sketch"}),
                tooltip="Click the center, one end's center, then the width"),
        Command(id="sketch.constraints_window", label="Constraint List", icon="constraints_window",
                handler=open_constraints_window, contexts=frozenset({"sketch"}),
                tooltip="List the sketch's constraints: pick a line or point to see its own, delete the ones you check"),
        Command(id="sketch.constraints_toggle", label="Show / Hide Constraints", icon="constraints_toggle",
                handler=lambda: constraints_visible(), contexts=frozenset({"sketch"}),
                tooltip="Hide or show the constraint symbols (dimensions stay)"),
        Command(id="sketch.constraints_show", label="Show Constraints", icon="constraints_toggle",
                handler=lambda: constraints_visible(True), shortcut="F8", contexts=frozenset({"sketch"})),
        Command(id="sketch.constraints_hide", label="Hide Constraints", icon="constraints_toggle",
                handler=lambda: constraints_visible(False), shortcut="F9", contexts=frozenset({"sketch"})),
        Command(id="view.screenshot", label="Screenshot", icon="camera", handler=screenshot,
                tooltip="Save the 3D view as a picture (PNG, JPEG or BMP; a PNG can have a transparent background)"),
        Command(id="view.screenshot_window", label="Screenshot (Window)", icon="camera", handler=screenshot_window,
                tooltip="Save the whole window — ribbon, browser, 3D view and status bar — as a picture"),
        Command(id="picture.insert", label="Insert Image", icon="picture", handler=insert_picture,
                contexts=frozenset({"part"}),
                tooltip="A JPG or PNG on the XY plane to sketch over (100 mm across; Scale / Transparency sizes it)"),
        Command(id="picture.scale", label="Scale / Transparency", icon="scale", handler=scale_picture,
                contexts=frozenset({"part"}),
                tooltip="Click two points of the image and type the real length between them: the whole image "
                        "scales so it is; a slider sets how see-through it is"),
        Command(id="picture.on_xy", label="On XY", icon="plane", handler=lambda: picture_on("xy"),
                contexts=frozenset({"part"}), tooltip="The image onto the XY plane (seen from the top)"),
        Command(id="picture.on_xz", label="On XZ", icon="plane", handler=lambda: picture_on("xz"),
                contexts=frozenset({"part"}), tooltip="The image onto the XZ plane (seen from the front)"),
        Command(id="picture.on_yz", label="On YZ", icon="plane", handler=lambda: picture_on("yz"),
                contexts=frozenset({"part"}), tooltip="The image onto the YZ plane (seen from the right)"),
        Command(id="picture.turn", label="Turn 90°", icon="rotational", handler=picture_turn,
                contexts=frozenset({"part"}), tooltip="Turn the image a quarter turn in its plane"),
        Command(id="picture.mirror", label="Mirror", icon="mirror", handler=picture_mirror,
                contexts=frozenset({"part"}), tooltip="Mirror the image left to right"),
        Command(id="picture.show_hide", label="Show / Hide", icon="hide_all", handler=pictures_show_hide,
                contexts=frozenset({"part"}), tooltip="Show or hide every image of the part"),
        Command(id="feature.line3d", label="3D Line", icon="line3d", handler=start_line3d,
                contexts=frozenset({"part", "sketch"}),
                tooltip="A line in space: click points of a virtual cube round the origin, or anywhere on its lines "
                        "(lines, a spline, or a spline through the points) — a path for Sweep"),
        Command(id="feature.sweep", label="Sweep", icon="sweep", handler=lambda: start_feature("sweep"),
                contexts=frozenset({"part"}), tooltip="Sweep a profile sketch along a path sketch"),
        Command(id="feature.loft", label="Loft", icon="loft", handler=lambda: start_feature("loft"),
                contexts=frozenset({"part"}), tooltip="Loft through two or more section sketches"),
        Command(id="feature.coil", label="Coil", icon="coil", handler=lambda: start_feature("coil"),
                contexts=frozenset({"part"}), tooltip="Sweep a profile sketch along a helix (springs)"),
        Command(id="feature.gear", label="Spur Gears", icon="gear", handler=lambda: start_feature("gear"),
                contexts=frozenset({"part"}),
                tooltip="A pair of spur or helical gears in mesh (or one gear): module, ratio or centre distance, "
                        "teeth; Make Assembly puts each in a part file"),
        Command(id="feature.worm", label="Worm Gear", icon="worm", handler=lambda: start_feature("worm"),
                contexts=frozenset({"part"}), tooltip="A worm and its wheel in mesh (or the worm alone)"),
        Command(id="feature.boss", label="Boss", icon="boss", handler=lambda: start_feature("boss"),
                contexts=frozenset({"part"}),
                tooltip="A post for a screw or a heat-set insert at each point or circle of a sketch: height, hole, "
                        "a fillet at its foot, ribs"),
        Command(id="feature.lip", label="Lip / Groove", icon="lip", handler=lambda: start_feature("lip"),
                contexts=frozenset({"part"}),
                tooltip="On the rim of a shelled box: a raised band along its inner or outer edge (Lip), or the "
                        "groove the other half's lip goes into"),
        Command(id="feature.snap_fit", label="Snap Fit", icon="snap_fit", handler=lambda: start_feature("snap_fit"),
                contexts=frozenset({"part"}),
                tooltip="A cantilever hook on each line of a sketch: length, thickness, the catch's depth and length"),
        Command(id="feature.draft", label="Draft", icon="draft", handler=lambda: start_feature("draft"),
                contexts=frozenset({"part"}), tooltip="Tilt faces by an angle (for moulding)"),
        Command(id="tools.appearance", label="Appearance", icon="appearance", handler=lambda: appearance(),
                tooltip="Colour and finish (matte, glossy, metal, glass…) of the picked Solids or faces — every Solid "
                        "with nothing picked —, a texture (brushed, wood, carbon, knurl, sand) and Hide Edges"),
        Command(id="tools.material", label="Material", icon="material", handler=lambda: choose_material(),
                contexts=frozenset({"part"}), tooltip="Part material: density for Mass Properties, colour"),
        Command(id="view.section_points", label="Section by 3 Points", icon="section",
                handler=lambda: _section_points(), contexts=frozenset({"part", "assembly"}),
                tooltip="Click three marked points (corners, edge middles, circle centres) for the section plane"),
        Command(id="view.section", label="Section", icon="section", handler=toggle_section,
                contexts=frozenset({"part", "assembly"}), checkable=True, is_checked=lambda: section_on(), tooltip="Cut the view open at the selection: 3 points, a flat face or a plane (XZ if nothing); "
                        "click again to close"),
        Command(id="browser.delete", label="Delete", icon="delete", handler=delete_target,
                contexts=frozenset({"browser"}), tooltip="Delete with everything InCad made for it"),
        Command(id="edit.delete", label="Delete", icon="delete", handler=delete_selection, shortcut="Backspace",
                tooltip="Delete the selection (sketch geometry and dimensions, or features)"),
        Command(id="edit.delete_key", label="Delete", icon="delete", handler=delete_selection, shortcut="Del",
                tooltip="Delete the selection"),
        Command(id="assembly.threads_toggle", label="Threads Off / On", icon="suppress",
                handler=toggle_assembly_threads, contexts=frozenset({"assembly"}), checkable=True,
                is_checked=_assembly_threads_off,
                tooltip="Every thread of the parts off (faster redraws), or back on; changes the part files"),
        Command(id="feature.threads_toggle", label="Threads Off / On", icon="suppress", handler=toggle_threads,
                transactional=True, checkable=True, is_checked=_threads_off,
                tooltip="Suppress every thread while you work (faster redraws); click again to bring them back"),
        Command(id="feature.thread", label="Thread", icon="thread", handler=lambda: start_feature("thread"),
                contexts=frozenset({"part"}), tooltip="Real thread on a shaft or in a hole (select its face)"),
        Command(id="feature.shell", label="Shell", icon="shell", handler=lambda: start_feature("shell"),
                contexts=frozenset({"part"})),
        Command(id="sketch.rect_pattern", label="Rectangular", icon="rect_pattern",
                handler=lambda: start_feature("sketch_rect"), contexts=frozenset({"sketch"}),
                tooltip="Repeat sketch geometry along a direction, or equally between two limits"),
        Command(id="sketch.circ_pattern", label="Circular", icon="circ_pattern",
                handler=lambda: start_feature("sketch_circ"), contexts=frozenset({"sketch"}),
                tooltip="Repeat sketch geometry around a point"),
        Command(id="sketch.path_pattern", label="Along Path", icon="path_pattern",
                handler=lambda: start_feature("sketch_path"), contexts=frozenset({"sketch"}),
                tooltip="Repeat sketch geometry along a path of lines, arcs and splines"),
        Command(id="sketch.edit_pattern", label="Edit Pattern", icon="rect_pattern", handler=_edit_sketch_pattern,
                contexts=frozenset({"sketch"}), tooltip="Change the selected pattern (or double-click a copy)"),
        Command(id="sketch.delete_pattern", label="Delete Pattern", icon="delete", handler=_delete_sketch_pattern,
                contexts=frozenset({"sketch"}), tooltip="Remove the copies; the original stays"),
        Command(id="feature.rect_pattern", label="Rectangular", icon="rect_pattern3d",
                handler=lambda: start_feature("rect_pattern"), contexts=frozenset({"part"})),
        Command(id="feature.circ_pattern", label="Circular", icon="circ_pattern",
                handler=lambda: start_feature("circ_pattern"), contexts=frozenset({"part"})),
        Command(id="feature.mirror", label="Mirror", icon="mirror3d", handler=lambda: start_feature("mirror"),
                contexts=frozenset({"part"})),
        Command(id="feature.mirror_solid", label="Mirror Solid", icon="mirror_solid",
                handler=lambda: start_feature("solid_mirror"), contexts=frozenset({"part"}),
                tooltip="A mirrored copy of a whole Solid as a new Solid: it follows the original until you "
                        "right-click it ▸ Break Mirror Link"),
        Command(id="work.plane", label="Plane", icon="work_plane", handler=lambda: start_feature("work_plane"),
                contexts=frozenset({"part"})),
        Command(id="work.axis", label="Axis", icon="work_axis", handler=lambda: start_feature("work_axis"),
                contexts=frozenset({"part"})),
        Command(id="work.point", label="Point", icon="work_point", handler=lambda: start_feature("work_point"),
                contexts=frozenset({"part"})),
        Command(id="inspect.measure", label="Measure", icon="measure", handler=inspect_measure,
                contexts=frozenset({"part"}), tooltip="Select a face, an edge, or two items"),
        Command(id="inspect.distance", label="Distance", icon="measure", handler=lambda: _inspect("distance"),
                contexts=frozenset({"part"}), tooltip="Distance between two faces, edges or points"),
        Command(id="inspect.angle", label="Angle", icon="measure", handler=lambda: _inspect("angle"),
                contexts=frozenset({"part"}), tooltip="Angle between two flat faces or straight edges"),
        Command(id="inspect.length", label="Length", icon="measure", handler=lambda: _inspect("length"),
                contexts=frozenset({"part"}), tooltip="Length of the selected edges (a loop)"),
        Command(id="inspect.area", label="Area", icon="measure", handler=lambda: _inspect("area"),
                contexts=frozenset({"part"}), tooltip="Area of the selected faces"),
        Command(id="inspect.mass", label="Mass Properties", icon="mass", handler=inspect_mass,
                contexts=frozenset({"part"})),
    ] + [Command(id=i, label=lbl, icon=icon, handler=(lambda n=cmd, m=_BASE_MODES.get(i): run_sketch_tool(n, m))
                 if i != "sketch.slot" else (lambda: _slot("c2c")),  # our own: it starts 2 mm wide, never 0
                 shortcut=sc, contexts=frozenset({"sketch"})) for i, (lbl, icon, cmd, sc) in SKETCHER.items()] + \
        [Command(id=i, label=lbl, icon=icon, handler=(lambda n=cmd, m=mode: run_sketch_tool(n, m)),
                 contexts=frozenset({"sketch"})) for i, (lbl, icon, cmd, mode) in SKETCH_VARIANTS.items()]


# Short ribbon labels (the full name stays in the tooltip and in split-button menus)
SHORT = {"view.section_points": "Section", "feature.threads_toggle": "Threads", "assembly.threads_toggle": "Threads", "view.toggle_visibility": "Hide / Show", "view.show_all_parts": "Show All", "view.origin_show_all": "Show All",
         "view.origin_hide_all": "Hide All", "view.axes_show_all": "Show Axes", "view.axes_hide_all": "Hide Axes", "feature.full_round": "Full Round", "view.sketches_show_all": "Show Sketches", "view.sketches_hide_all": "Hide Sketches", "inspect.distance": "Measure", "inspect.angle": "Measure", "inspect.length": "Measure", "inspect.area": "Measure",
         "sketch.slot": "Slot", "sketch.slot_overall": "Slot", "sketch.slot_center": "Slot", "sketch.slot_arc": "Arc Slot",
         "sketch.slot_arc_flat": "Arc Slot", "sketch.arc": "Arc", "sketch.arc_center": "Arc", "sketch.arc_ellipse": "Arc",
         "sketch.circle_3pt": "Circle", "sketch.ellipse_3pt": "Ellipse", "sketch.rectangle_center": "Rectangle",
         "sketch.rectangle_3pt": "Rectangle", "sketch.rectangle_center_3pt": "Rectangle",
         "sketch.rectangle_rounded": "Rectangle", "sketch.line_single": "Line", "sketch.polygon": "Polygon",
         "sketch.triangle": "Polygon", "sketch.square": "Polygon", "sketch.pentagon": "Polygon", "sketch.octagon": "Polygon",
         "sketch.polygon_n": "Polygon", "sketch.spline_cv": "Spline", "sketch.rect_pattern": "Pattern",
         "sketch.circ_pattern": "Pattern", "sketch.path_pattern": "Pattern",
         "sketch.constraints_toggle": "Constraints", "sketch.constraints_window": "List", "sketch.show_dof": "Free DOF", "sketch.slice": "Slice",
         "sketch.project": "Project", "inspect.mass": "Mass", "feature.rect_pattern": "Pattern",
         "feature.circ_pattern": "Pattern"}


def register_all(registry=REGISTRY):
    from incad import host
    import dataclasses
    for cmd in commands():
        if cmd.id in SHORT and not cmd.short:
            cmd = dataclasses.replace(cmd, short=SHORT[cmd.id])
        cmd = dataclasses.replace(cmd, label=host.brand(cmd.label), tooltip=host.brand(cmd.tooltip))
        if not registry.has(cmd.id):
            registry.register(cmd)
