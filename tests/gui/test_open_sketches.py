"""Unused open sketches stay visible in the part view, with NO key points drawn there (the user's law: points are
for the building commands, whose dialogs mark them); visibility follows the user's Hide / Show, sketch edit mode,
deletion, and the real part. The Sweep / Loft dialogs still mark the picked sketches' points."""
import os
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
import Part  # noqa: E402
from incad.core import commands, prefs  # noqa: E402
from incad.ui import shell  # noqa: E402
from incad.ui.browser import STATE_ROLE  # noqa: E402
import headless.util as util  # noqa: E402


def _body():
    from incad.core import document
    return document.primary_body(App.ActiveDocument)


def _markers():
    entry = shell.instance().sketch_markers.get(App.ActiveDocument.Name)
    return entry[1] if entry else None


def _count():
    m = _markers()
    return m.count if m is not None else 0


def _spline(poles):
    c = Part.BSplineCurve()
    c.buildFromPoles(poles)
    return c


def _sketch_on(body, role, name):
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(next(o for o in body.Origin.OriginFeatures if o.Role == role), "")]
    sk.MapMode = "FlatFace"
    return sk


def test_0_part_with_a_path():
    prefs.set_remember_new_choice(True)
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    from incad.core import document
    body = _body()
    sk = _sketch_on(body, "XY_Plane", "Path")
    sk.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(30, 0, 0)))
    sk.addGeometry(_spline([App.Vector(30, 0, 0), App.Vector(40, 10, 0), App.Vector(50, 0, 0)]))
    App.ActiveDocument.recompute()
    document.show_result(body)
    shell.instance().schedule_refresh()
    harness.pump(600)
    assert sk.Visibility, "an open contour stays visible"
    assert _count() == 0, _count()  # no points in the plain view


def test_1_hidden_path_has_no_markers():
    from incad.core import document
    sk = App.ActiveDocument.getObject("Path")
    document.set_visibility([sk], False)
    shell.instance().schedule_refresh()
    harness.pump(500)
    assert not sk.Visibility
    assert _count() == 0, _count()
    document.set_visibility([sk], True)
    shell.instance().schedule_refresh()
    harness.pump(500)
    assert sk.Visibility
    assert _count() == 0, _count()


def test_2_sketch_edit_mode_hides_the_markers():
    sh = shell.instance()
    sh.sketches.edit(App.ActiveDocument.getObject("Path"))  # InCad's route (a raw setEdit switches workbenches)
    sh.schedule_refresh()
    harness.pump(800)
    assert sh.sketches.editing() is not None, "the sketch is being edited"
    assert _count() == 0, _count()
    commands.REGISTRY.run("sketch.finish")
    harness.pump(800)
    assert sh.sketches.editing() is None
    assert App.ActiveDocument.getObject("Path").Visibility, "the open path stays shown after editing"
    assert _count() == 0, _count()


def test_3_deleted_sketch_drops_its_markers():
    App.ActiveDocument.removeObject("Path")
    App.ActiveDocument.recompute()
    shell.instance().schedule_refresh()
    harness.pump(500)
    assert _count() == 0, _count()


def test_4_real_part_sketch7_is_shown_with_its_ends():
    path = os.environ.get("INCAD_REAL_PART",
                          os.path.join(os.environ["INCAD_REPO"], "tests", "gui", "data", "p2_test_reordered.FCStd"))
    App.openDocument(path)
    harness.pump(1500)
    shell.instance().schedule_refresh()
    harness.pump(800)
    sk = App.ActiveDocument.getObject("Sketch006")
    assert sk.Visibility, "Sketch7 (two open chains) is shown"
    assert _count() == 0, _count()  # shown, but no points in the plain view
    others = [o.Name for o in App.ActiveDocument.Objects if o.TypeId == "Sketcher::SketchObject" and o.Visibility
              and o is not sk]
    assert others == [], others


def test_5_sweep_dialog_marks_the_picked_path_and_profile():
    from incad.core import document
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    body = _body()
    path = _sketch_on(body, "XY_Plane", "Path")
    path.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(40, 0, 0)))
    prof = _sketch_on(body, "YZ_Plane", "Prof")
    prof.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 3))
    App.ActiveDocument.recompute()
    document.show_result(body)
    sh = shell.instance()
    sh.schedule_refresh()
    harness.pump(500)
    assert not prof.Visibility, "a closed unused sketch starts hidden"
    commands.REGISTRY.run("feature.sweep")
    harness.pump(600)
    fs = sh.feature_session
    assert fs is not None, "the Sweep dialog is open"
    fs.dialog.set_pick("profile", "Prof")
    fs.dialog.set_pick("path", "Path")
    harness.pump(900)
    assert prof.Visibility and path.Visibility, "picked sketches are shown for the dialog"
    assert fs._markers is not None and fs._markers.count == 3, fs._markers and fs._markers.count  # centre + 2 ends
    assert _count() == 0, "the part-view markers wait while the dialog shows its own"
    fs.cancel()
    harness.pump(600)
    assert sh.feature_session is None
    assert fs._markers is None, "the dialog's markers are gone"
    assert not prof.Visibility, "the closed sketch is hidden again"
    assert path.Visibility, "the open path stays shown"
    assert _count() == 0, _count()  # no points in the plain view


def test_6_loft_dialog_marks_every_section():
    body = _body()
    sh = shell.instance()
    top = _sketch_on(body, "YZ_Plane", "Top")
    top.AttachmentOffset = App.Placement(App.Vector(0, 0, 20), App.Rotation())
    top.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 5))
    App.ActiveDocument.recompute()
    commands.REGISTRY.run("feature.loft")
    harness.pump(600)
    fs = sh.feature_session
    assert fs is not None, "the Loft dialog is open"
    assert fs.kind.TITLE == "Loft" and "sections" in fs.dialog._widgets, (fs.kind.TITLE, list(fs.dialog._widgets))
    fs.dialog.set_pick("sections", "Prof,Top")
    harness.pump(900)
    assert fs._markers is not None and fs._markers.count == 2, fs._markers and fs._markers.count
    fs.cancel()
    harness.pump(600)


def _send(kind, pos, button, buttons):
    from incad.qt import QtCore, QtGui, QtWidgets
    vp = Gui.ActiveDocument.ActiveView.graphicsView().viewport()
    types = {"press": QtCore.QEvent.MouseButtonPress, "release": QtCore.QEvent.MouseButtonRelease}
    ev = QtGui.QMouseEvent(types[kind], pos, QtCore.QPointF(vp.mapToGlobal(pos.toPoint())), button, buttons,
                           QtCore.Qt.NoModifier)
    QtWidgets.QApplication.sendEvent(vp, ev)
    harness.pump(80)


def _right_click_at(point):
    from incad.qt import QtCore
    view = Gui.ActiveDocument.ActiveView
    vp = view.graphicsView().viewport()
    sx, sy = view.getPointOnViewport(point)
    dpr = vp.devicePixelRatioF()
    pos = QtCore.QPointF(sx / dpr, vp.height() - sy / dpr)
    _send("press", pos, QtCore.Qt.RightButton, QtCore.Qt.RightButton)
    _send("release", pos, QtCore.Qt.RightButton, QtCore.Qt.NoButton)
    harness.pump(300)


def test_7_cancel_keeps_a_sketch_the_user_showed():
    """A closed unused sketch shown by choice (InCadShown) is picked for a Sweep, FreeCAD's preview hides it, the
    dialog shows it again; Cancel must leave it shown, as the choice says."""
    from incad.core import document
    sh = shell.instance()
    prof = App.ActiveDocument.getObject("Prof")
    document.set_visibility([prof], True)
    sh.schedule_refresh()
    harness.pump(400)
    commands.REGISTRY.run("feature.sweep")
    harness.pump(600)
    fs = sh.feature_session
    fs.dialog.set_pick("profile", "Prof")
    harness.pump(900)
    prof.ViewObject.Visibility = False  # what FreeCAD's Pipe view provider does once it takes the profile
    fs.dialog.set_pick("path", "Path")  # the dialog shows it again (and remembers to restore it)
    harness.pump(900)
    assert prof.Visibility
    fs.cancel()
    harness.pump(600)
    assert prof.Visibility, "shown by choice: Cancel leaves it shown"
    assert sh.browser.item_by_label("Prof").data(0, STATE_ROLE) == "", "not dimmed either"
    document.set_visibility([prof], False)
    sh.schedule_refresh()
    harness.pump(400)


def test_8_ok_on_a_sweep_hides_the_path_and_its_markers():
    sh = shell.instance()
    path = App.ActiveDocument.getObject("Path")
    assert path.Visibility
    assert _count() == 0, _count()  # no points in the plain view
    commands.REGISTRY.run("feature.sweep")
    harness.pump(600)
    fs = sh.feature_session
    fs.dialog.set_pick("profile", "Prof")
    fs.dialog.set_pick("path", "Path")
    harness.pump(900)
    fs.ok()
    harness.pump(900)
    assert sh.feature_session is None
    sweeps = [o for o in App.ActiveDocument.Objects if o.TypeId == "PartDesign::AdditivePipe"]
    assert sweeps and "Invalid" not in sweeps[0].State, [o.State for o in sweeps]
    assert not path.Visibility, "the Sweep consumed its path"
    assert _count() == 0, _count()
    assert sh.browser.item_by_label("Path") is None or \
        sh.browser.item_by_label("Path").data(0, STATE_ROLE) != "hidden", "a consumed sketch is not 'hidden by choice'"
    commands.REGISTRY.run("edit.undo")
    harness.pump(900)
    assert not [o for o in App.ActiveDocument.Objects if o.TypeId == "PartDesign::AdditivePipe"]
    assert path.Visibility and _count() == 0, (path.Visibility, _count())


def test_9_right_click_on_a_shown_sketch_in_the_view_opens_the_browser_menu():
    from incad.core import context
    from incad.core import document
    sh = shell.instance()
    context.set_target(None)
    body = _body()
    guide = _sketch_on(body, "XY_Plane", "Guide")  # an open contour away from the swept solid
    guide.addGeometry(Part.LineSegment(App.Vector(0, 60, 0), App.Vector(40, 60, 0)))
    App.ActiveDocument.recompute()
    document.show_result(body)
    sh.schedule_refresh()
    harness.pump(500)
    assert guide.Visibility
    Gui.SendMsgToActiveView("ViewFit")
    harness.pump(400)
    _right_click_at(App.Vector(20, 60, 0))  # on the guide's line
    assert sh.menu_kind == "browser", sh.menu_kind
    assert context.target() == "Guide", context.target()
    sh.marking_menu.close()
    harness.pump(200)


def test_10_a_stale_tree_selection_does_not_beat_the_plane_under_the_cursor():
    from incad.core import context, document
    sh = shell.instance()
    body = _body()
    xy = [o for o in document.origin_items(body) if o.Role == "XY_Plane"][0]
    document.set_visibility([xy], True)
    sh.schedule_refresh()
    harness.pump(400)
    row = sh.browser.item_by_label("Prof")
    row.setSelected(True)  # a row clicked earlier in the tree
    view = Gui.ActiveDocument.ActiveView
    hit = None
    for x, y in ((1.2, 1.2), (1.5, 0.8), (0.8, 1.5), (2.0, 2.0)):
        sx, sy = view.getPointOnViewport(App.Vector(x, y, 0))
        info = view.getObjectInfo((int(sx), int(sy)))
        if info and info["Object"] == xy.Name:
            hit = App.Vector(x, y, 0)
            break
    assert hit is not None, "the shown XY plane is under a probe point"
    if sh.marking_menu is not None:
        sh.marking_menu.close()
        harness.pump(200)
    sh.menu_kind = None
    _right_click_at(hit)
    assert sh.menu_kind == "origin", sh.menu_kind
    assert context.target() == xy.Name, context.target()
    if sh.marking_menu is not None:
        sh.marking_menu.close()
        harness.pump(200)
    r = commands.REGISTRY.run("view.toggle_visibility")
    assert r.ok, r.error
    harness.pump(400)
    assert not document.is_shown(xy), "Hide / Show acted on the plane under the cursor, not on the old tree row"


def test_11_a_file_with_a_visible_consumed_sketch_opens_clean():
    """A file saved with a consumed sketch left visible (an older InCad, FreeCAD's Space key) opens with it
    hidden; a peek still shows it while its row is selected."""
    import tempfile
    from incad.core import document
    sh = shell.instance()
    body = _body()
    sk = body.newObject("Sketcher::SketchObject", "PadSk")
    sk.AttachmentSupport = [(util.xy_plane(body), "")]
    sk.MapMode = "FlatFace"
    for a, b in [((70, 0), (80, 0)), ((80, 0), (80, 10)), ((80, 10), (70, 10)), ((70, 10), (70, 0))]:
        sk.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)))
    util.add_pad(body, sk, 5, name="Pad2")
    App.ActiveDocument.recompute()
    sk.ViewObject.Visibility = True
    path = os.path.join(tempfile.mkdtemp(), "visible_consumed.FCStd")
    App.ActiveDocument.saveAs(path)
    name = App.ActiveDocument.Name
    App.closeDocument(name)
    harness.pump(500)
    App.openDocument(path)
    harness.pump(1200)
    sh.schedule_refresh()
    harness.pump(600)
    sk = App.ActiveDocument.getObject("PadSk")
    assert not sk.Visibility, "consumed: hidden when the file opens"
    assert sh.browser.select_row("PadSk")
    harness.pump(500)
    assert sk.Visibility, "but shown while its row is selected"
    sh.schedule_refresh()
    harness.pump(500)
    assert sk.Visibility, "a refresh keeps the peek"
    Gui.Selection.clearSelection()
    harness.pump(400)
    assert not sk.Visibility


def test_12_undo_after_hiding_an_open_sketch_shows_it_again():
    from incad.core import document
    sh = shell.instance()
    body = _body()
    guide = body.newObject("Sketcher::SketchObject", "Guide2")
    guide.AttachmentSupport = [(util.xy_plane(body), "")]
    guide.MapMode = "FlatFace"
    guide.addGeometry(Part.LineSegment(App.Vector(0, 90, 0), App.Vector(40, 90, 0)))
    App.ActiveDocument.recompute()
    document.show_result(body)
    sh.schedule_refresh()
    harness.pump(500)
    assert guide.Visibility
    Gui.Selection.clearSelection()
    sh.browser.tree.clearSelection()
    from incad.core import context
    context.set_target("Guide2")
    r = commands.REGISTRY.run("view.toggle_visibility")
    assert r.ok, r.error
    harness.pump(400)
    assert not guide.Visibility
    commands.REGISTRY.run("edit.undo")
    harness.pump(600)
    assert guide.Visibility, "Undo of the hide shows it again"
    commands.REGISTRY.run("edit.redo")
    harness.pump(600)
    assert not guide.Visibility, "Redo hides it"
    commands.REGISTRY.run("edit.undo")
    harness.pump(600)
    assert guide.Visibility


harness.run([test_0_part_with_a_path, test_1_hidden_path_has_no_markers, test_2_sketch_edit_mode_hides_the_markers,
             test_3_deleted_sketch_drops_its_markers, test_4_real_part_sketch7_is_shown_with_its_ends,
             test_5_sweep_dialog_marks_the_picked_path_and_profile, test_6_loft_dialog_marks_every_section,
             test_7_cancel_keeps_a_sketch_the_user_showed, test_8_ok_on_a_sweep_hides_the_path_and_its_markers,
             test_9_right_click_on_a_shown_sketch_in_the_view_opens_the_browser_menu,
             test_10_a_stale_tree_selection_does_not_beat_the_plane_under_the_cursor,
             test_11_a_file_with_a_visible_consumed_sketch_opens_clean,
             test_12_undo_after_hiding_an_open_sketch_shows_it_again])
