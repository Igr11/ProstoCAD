"""Edit a part from the assembly: its tab shows the other parts around it, semi-transparent, never picked, never in
the document (ui/assembly_context); View > Assembly Context hides and shows them."""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from pivy import coin  # noqa: E402
from incad import actions  # noqa: E402
from incad.core import assembly, commands, prefs  # noqa: E402
from incad.ui import shell  # noqa: E402
from incad.ui.assembly_context import NAME  # noqa: E402
from headless.test_assembly import save_part  # noqa: E402

ST = {}
TRAIN = "/Users/igor/Plasticity_projects"


def s():
    return shell.instance()


def asm_doc():
    return App.getDocument(ST["asm"])


def view():
    return Gui.ActiveDocument.ActiveView


def context_node(v=None):
    sa = coin.SoSearchAction()
    sa.setName(NAME)
    sa.setInterest(coin.SoSearchAction.ALL)
    sa.apply((v or view()).getSceneGraph())
    paths = sa.getPaths()
    return [paths[i].getTail() for i in range(paths.getLength())]


def viewport(v=None):
    return (v or view()).getViewer().getSoRenderManager().getViewportRegion()


def bbox(node, v=None):
    """The box of the points Coin draws (each mesh's points through the transforms above it)."""
    sa = coin.SoSearchAction()
    sa.setType(coin.SoCoordinate3.getClassTypeId())
    sa.setInterest(coin.SoSearchAction.ALL)
    sa.apply(node)
    paths, pts = sa.getPaths(), []
    for i in range(paths.getLength()):
        path = paths[i]
        ma = coin.SoGetMatrixAction(viewport(v))
        ma.apply(path)
        m = ma.getMatrix()
        for p in path.getTail().point.getValues():
            pts.append(m.multVecMatrix(p).getValue())
    return tuple(min(p[k] for p in pts) for k in range(3)) + tuple(max(p[k] for p in pts) for k in range(3))


def moved_box(shape, placement):
    sh = shape.copy()
    sh.Placement = placement.multiply(sh.Placement)
    bb = sh.BoundBox
    return (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)


def close_enough(a, b, tol=1e-3):
    return all(abs(x - y) < tol for x, y in zip(a, b))


def test_0_setup():
    prefs.set_remember_new_choice(True)
    prefs.set_default_units("mm")
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    shell.UPDATE_ASK = lambda label: True  # back in the assembly's tab after a part tab closed: no modal question
    shell.CONFIRM = lambda title, text: False
    ST["dir"] = tempfile.mkdtemp(prefix="incad_gui_ctx_")
    for name, w, h, t in (("Plate", 40, 30, 10), ("Block", 10, 10, 10)):
        d, ST[name] = save_part(ST["dir"], name, w, h, t)
        ST["n" + name] = len(d.Objects)
        App.closeDocument(d.Name)
    doc = actions.new_assembly("mm")
    doc.saveAs(os.path.join(ST["dir"], "Asm.FCStd"))
    ST["asm"] = doc.Name
    a = s().assembly_place(ST["Plate"])
    b = s().assembly_place(ST["Block"])
    b.Placement = App.Placement(App.Vector(30, 20, 10), App.Rotation(App.Vector(0, 0, 1), 30))  # on the plate
    doc.recompute()
    harness.pump(800)
    ST["a"], ST["b"] = a.Name, b.Name


def test_1_edit_shows_the_other_part_around_this_one():
    doc = asm_doc()
    a, b = doc.getObject(ST["a"]), doc.getObject(ST["b"])
    s().assembly_edit(b)
    harness.pump(1200)
    part = App.ActiveDocument
    assert part is b.LinkedObject.Document, App.ActiveDocument
    nodes = context_node()
    assert len(nodes) == 1, nodes
    ctx = s()._contexts[part.Name]
    assert ctx.count == 1 and ctx.names == [a.Name], ctx.names
    want = moved_box(a.Shape, b.Placement.inverse())  # the plate where the assembly has it, in the block's frame
    got = bbox(nodes[0])
    assert close_enough(got, want), (got, want)
    msg = s().status.texts()["message"]
    assert "Editing Block in %s: the other parts are shown for reference" % doc.Label in msg, msg
    assert actions.assembly_context_shown()
    # opening the part file in a tab marks it modified by itself (seen with no context drawn: False, then True after
    # the first refresh); the picture adds nothing: cleared, then the context drawn again, it stays unmodified
    Gui.getDocument(part.Name).Modified = False
    s()._show_context(b, part)
    harness.pump(600)
    assert len(context_node()) == 1, "drawn again: the earlier picture went first"
    assert not Gui.getDocument(part.Name).Modified, "the picture never marks the part modified"
    assert len(part.Objects) == ST["nBlock"], [o.Name for o in part.Objects]
    ST["part"] = part.Name


def test_2_clicks_go_through_the_context():
    v = view()
    Gui.SendMsgToActiveView("ViewFit")
    harness.pump(600)
    part = App.getDocument(ST["part"])
    asm = asm_doc()
    a, b = asm.getObject(ST["a"]), asm.getObject(ST["b"])
    corner = b.Placement.inverse().multVec(App.Vector(2, 2, 10))  # the plate's top, far from the block
    sx, sy = v.getPointOnViewport(corner)
    info = v.getObjectInfo((int(sx), int(sy)))
    assert info is None or info.get("Document") == part.Name, info
    node = context_node()[0]
    pick = node.getChild(0)
    assert pick.style.getValue() == coin.SoPickStyle.UNPICKABLE

    def ray_hits_context():
        rp = coin.SoRayPickAction(viewport())
        rp.setPoint(coin.SbVec2s(int(sx), int(sy)))
        rp.apply(v.getViewer().getSoRenderManager().getSceneGraph())  # the whole graph, its camera with it
        pp = rp.getPickedPoint()
        return pp is not None and pp.getPath().containsNode(node)

    assert not ray_hits_context(), "the context is never picked"
    pick.style = coin.SoPickStyle.SHAPE  # the same ray, pickable: it hits the plate (the check above means something)
    try:
        assert ray_hits_context(), "the point is on the context plate"
    finally:
        pick.style = coin.SoPickStyle.UNPICKABLE
    assert not Gui.getDocument(part.Name).Modified


def test_3_view_assembly_context_hides_and_shows_it():
    commands.REGISTRY.run("view.assembly_context")
    harness.pump(400)
    assert context_node() == [] and not actions.assembly_context_shown()
    commands.REGISTRY.run("view.assembly_context")
    harness.pump(400)
    assert len(context_node()) == 1 and actions.assembly_context_shown()
    assert not Gui.getDocument(ST["part"]).Modified


def test_4_theme_switch_recolours_it():
    from incad.ui import theme as themes
    before = s().theme
    commands.REGISTRY.run("view.theme_toggle")
    harness.pump(800)
    try:
        ctx = s()._contexts[ST["part"]]
        r, g, bl = ctx.material.diffuseColor[0].getValue()
        h = themes.PALETTES[s().theme]["context"].lstrip("#")
        want = tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        assert close_enough((r, g, bl), want, 1e-2), ((r, g, bl), want)
        assert abs(ctx.material.transparency[0] - 0.7) < 1e-6
    finally:
        if s().theme != before:
            commands.REGISTRY.run("view.theme_toggle")
            harness.pump(800)


def test_4b_return_to_assembly():
    """The user: "editing the part opens in a new window and there is no option to close the editing and return to
    the assembly". A bar on the part's view says what is edited; Return to Assembly saves the part, brings the
    assembly up to date and shows it; the picture of the parts around it goes."""
    from incad.qt import QtWidgets
    from incad.ui import ribbon_layout
    part = App.getDocument(ST["part"])
    assert s().ribbon.button("assembly.return") is not None, "Return on the ribbon of a part opened with Edit"
    assert ribbon_layout.tabs_of("part_edit")[0][1][0] == ["Return", ["assembly.return"]], "first: never cut off"
    vp = Gui.getDocument(part.Name).ActiveView.graphicsView().viewport()
    button = vp.findChild(QtWidgets.QPushButton, "InCadReturnToAssembly")
    assert button is not None and button.isVisible(), "no Return to Assembly on the part's view"
    bar = button.parentWidget()
    assert "Editing Block in" in bar.findChild(QtWidgets.QLabel).text(), bar.findChild(QtWidgets.QLabel).text()
    from incad.qt import QtCore
    menu = s()._show_marking_menu("part", QtCore.QPoint(300, 300))  # the right-click menu has Return first too
    try:
        first = menu._list[0]
        assert first.text() == "Return", [b.text() for b in menu._list]
    finally:
        menu.close()
        harness.pump(200)
    box = [o for o in part.Objects if o.TypeId == "PartDesign::Pad"][0]
    box.Length = 14  # the part changed in its tab
    part.recompute()
    button.click()
    harness.pump(1200)
    assert App.ActiveDocument.Name == ST["asm"], App.ActiveDocument.Name
    assert s().ribbon.button("assembly.return") is None, "no Return in the assembly"
    assert ST["part"] in App.listDocuments(), "the part's tab stays"
    assert ST["part"] not in s()._contexts and context_node(Gui.getDocument(part.Name).ActiveView) == []
    assert not Gui.getDocument(part.Name).Modified, "saved"
    b = asm_doc().getObject(ST["b"])
    assert abs(b.Shape.BoundBox.ZLength - 14) < 1e-6, b.Shape.BoundBox
    App.setActiveDocument(part.Name)
    Gui.ActiveDocument = Gui.getDocument(part.Name)
    harness.pump(800)
    assert s().ribbon.button("assembly.return") is None, "nor on the part once it is back to itself"


def test_5_closing_the_tab_and_opening_normally_shows_none_edit_rebuilds():
    path = App.getDocument(ST["part"]).FileName
    App.closeDocument(ST["part"])
    harness.pump(800)
    assert ST["part"] not in s()._contexts
    part = App.openDocument(path)
    App.setActiveDocument(part.Name)
    Gui.ActiveDocument = Gui.getDocument(part.Name)
    harness.pump(1000)
    assert context_node(Gui.getDocument(part.Name).ActiveView) == [], "opened normally: no context"
    assert not actions.assembly_context_shown()
    asm = asm_doc()
    b = asm.getObject(ST["b"])
    App.setActiveDocument(asm.Name)
    Gui.ActiveDocument = Gui.getDocument(asm.Name)
    harness.pump(600)
    b.Placement = App.Placement(App.Vector(-20, 0, 0), App.Rotation())  # moved beside the plate
    asm.recompute()
    s().assembly_edit(b)
    harness.pump(1000)
    assert App.ActiveDocument.Name == part.Name
    nodes = context_node()
    assert len(nodes) == 1, nodes
    want = moved_box(asm.getObject(ST["a"]).Shape, b.Placement.inverse())
    assert close_enough(bbox(nodes[0]), want), (bbox(nodes[0]), want)


def test_6_closing_the_assembly_removes_it():
    part = App.ActiveDocument.Name
    App.closeDocument(ST["asm"])
    harness.pump(800)
    assert part not in s()._contexts
    assert context_node(Gui.getDocument(part).ActiveView) == []
    for name in list(App.listDocuments()):
        App.closeDocument(name)
    harness.pump(500)
    shutil.rmtree(ST["dir"], ignore_errors=True)


def test_7_train_1_solid2_shows_the_hex_around_it():
    """The user's assembly (never saved): Edit the D-part, the hex around it at its place."""
    files = [os.path.join(TRAIN, n) for n in ("Train_1-Assembly.FCStd", "Train_1-Solid1.FCStd", "Train_1-Solid2.FCStd")]
    if not all(os.path.isfile(f) for f in files):
        return
    stamps = [os.path.getmtime(f) for f in files]
    try:
        part = App.openDocument(files[2])  # in its own tab first: Edit then never reopens (nor saves) a hidden file
        asm = App.openDocument(files[0])
        App.setActiveDocument(asm.Name)
        Gui.ActiveDocument = Gui.getDocument(asm.Name)
        harness.pump(1500)
        link = next(c for c in assembly.components(asm) if c.LinkedObject.Document is part)
        assert Gui.getDocument(part.Name).ActiveView is not None
        s().assembly_edit(link)
        harness.pump(1500)
        assert App.ActiveDocument is part
        ctx = s()._contexts[part.Name]
        assert ctx.count == 1, ctx.names
        other = asm.getObject(ctx.names[0])
        assert other.LinkedObject.Document.FileName.endswith("Train_1-Solid1.FCStd"), other.LinkedObject.Document
        frame = getattr(link.LinkedObject, "Placement", App.Placement()).multiply(assembly._frame(link).inverse())
        want = moved_box(other.Shape, frame)
        got = bbox(context_node()[0])
        assert close_enough(got, want, 1e-2), (got, want)
    finally:
        for name in list(App.listDocuments()):
            App.closeDocument(name)
        harness.pump(500)
        shell.UPDATE_ASK = shell.CONFIRM = None
    assert [os.path.getmtime(f) for f in files] == stamps, "the user's files are never saved"


harness.run([test_0_setup, test_1_edit_shows_the_other_part_around_this_one, test_2_clicks_go_through_the_context,
             test_3_view_assembly_context_hides_and_shows_it, test_4_theme_switch_recolours_it, test_4b_return_to_assembly,
             test_5_closing_the_tab_and_opening_normally_shows_none_edit_rebuilds,
             test_6_closing_the_assembly_removes_it, test_7_train_1_solid2_shows_the_hex_around_it])
