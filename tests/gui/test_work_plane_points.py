"""Work Plane ▸ Three points on Section's markers (the user: "the points lit up, as in Section"): the Points field
shows corners, edge middles and circle centres of the part; three clicks make the plane — through two corners
(followed) and an edge middle (fixed)."""
import os
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from incad.qt import QtCore  # noqa: E402
from incad.core import commands, document, prefs  # noqa: E402
from incad.ui import shell  # noqa: E402

V = App.Vector
ST = {}


def s():
    return shell.instance()


def doc():
    return App.ActiveDocument


def body():
    return document.primary_body(doc())


def dialog():
    fs = s().feature_session
    return fs.dialog if fs is not None else None


def test_0_box():
    prefs.set_remember_new_choice(True)
    prefs.set_default_units("mm")
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    box = body().newObject("PartDesign::AdditiveBox", "Box")
    box.Length, box.Width, box.Height = 20, 10, 6
    doc().recompute()
    Gui.SendMsgToActiveView("ViewFit")
    harness.pump(500)
    ST["box"] = box.Name


def test_1_points_light_up_and_three_clicks_make_the_plane():
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("work.plane")
    harness.pump(1000)
    d = dialog()
    assert d is not None
    w = d.field("mode")
    w.setCurrentIndex(w.findData("three_points"))
    harness.pump(800)
    btn = d.field("points")
    if d.active_pick() != "points":
        QTest.mouseClick(btn, QtCore.Qt.LeftButton)
        harness.pump(800)
    pick = s()._point_pick
    assert pick is not None and len(pick.points) >= 8, pick and len(pick.points)
    wanted = [V(0, 0, 6), V(20, 0, 6), V(10, 10, 6)]  # two top corners and the middle of the far top edge
    view = Gui.ActiveDocument.ActiveView
    vp = view.graphicsView().viewport()
    dpr = vp.devicePixelRatioF()
    for k, p in enumerate(wanted):
        i = next(i for i, q in enumerate(pick.points) if (q - p).Length < 1e-6)
        sx, sy = view.getPointOnViewport(pick.points[i])
        if k == 2:  # two picked, the pointer on the third: the faint plane through the three shows (the user)
            assert pick._plane is None
            s()._move_hook(view, vp, sx / dpr, vp.height() - sy / dpr)
            assert pick._plane is not None, "no plane while the third point is pointed at"
        s()._point_clicked(vp, sx / dpr, vp.height() - sy / dpr)
        harness.pump(400)
    harness.pump(1500)
    assert s()._point_pick is None
    text = d.values()["points"]
    items = text.split(",")
    assert len(items) == 3 and items[0].startswith(ST["box"] + ":Vertex") and items[2].startswith("@"), text
    assert btn.text() == "3 points", btn.text()
    QTest.mouseClick(d.ok_button, QtCore.Qt.LeftButton)
    harness.pump(1000)
    assert s().feature_session is None
    plane = [o for o in body().Group if o.TypeId == "PartDesign::Plane"][-1]
    assert "Invalid" not in plane.State, plane.getStatusString()
    n = plane.Placement.Rotation.multVec(V(0, 0, 1))
    assert abs(abs(n.z) - 1) < 1e-6 and abs(plane.Placement.Base.z - 6) < 1e-6, plane.Placement


def test_2_work_axis_two_points_light_up_too():
    """The user: Work Axis ▸ Two points showed no points to click (only a vertex picked blind)."""
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("work.axis")
    harness.pump(1000)
    d = dialog()
    assert d is not None
    w = d.field("mode")
    w.setCurrentIndex(w.findData("two_points"))  # from On edge: its Edge pick was active, then hidden
    harness.pump(800)
    btn = d.field("points")
    assert d.active_pick() == "points", d.active_pick()  # the points light up at once, no click on Points
    pick = s()._point_pick
    assert pick is not None and len(pick.points) >= 8, pick and len(pick.points)
    view = Gui.ActiveDocument.ActiveView
    vp = view.graphicsView().viewport()
    dpr = vp.devicePixelRatioF()
    for p in (V(20, 0, 0), V(20, 0, 6)):  # a vertical edge of the box, corner to corner
        i = next(i for i, q in enumerate(pick.points) if (q - p).Length < 1e-6)
        sx, sy = view.getPointOnViewport(pick.points[i])
        s()._point_clicked(vp, sx / dpr, vp.height() - sy / dpr)
        harness.pump(400)
    harness.pump(1500)
    assert s()._point_pick is None
    assert btn.text() == "2 points", btn.text()
    QTest.mouseClick(d.ok_button, QtCore.Qt.LeftButton)
    harness.pump(1000)
    assert s().feature_session is None
    axis = [o for o in body().Group if o.TypeId == "PartDesign::Line"][-1]
    assert "Invalid" not in axis.State, axis.getStatusString()
    assert axis.MapMode == "TwoPointLine", axis.MapMode  # on the corners: it follows them
    dz = axis.Placement.Rotation.multVec(V(0, 0, 1))
    assert abs(abs(dz.z) - 1) < 1e-6 and abs(axis.Placement.Base.x - 20) < 1e-6, axis.Placement


def test_3_a_work_point_lights_the_points_at_once():
    """The user: Parallel to edge through point (and every Point field) picked a vertex blind."""
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("work.point")
    harness.pump(1000)
    d = dialog()
    assert d is not None and d.active_pick() == "vertex", d and d.active_pick()
    pick = s()._point_pick
    assert pick is not None and len(pick.points) >= 8, pick and len(pick.points)
    d.cancel_button.click()
    harness.pump(800)


def test_9_cleanup():
    harness.pump(300)
    App.closeDocument(doc().Name)


harness.run([test_0_box, test_1_points_light_up_and_three_clicks_make_the_plane,
             test_2_work_axis_two_points_light_up_too, test_3_a_work_point_lights_the_points_at_once, test_9_cleanup])
