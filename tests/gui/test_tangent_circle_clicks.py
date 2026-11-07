"""Sketch ▸ Tangent Circle in a real window, the way the user draws it: the command first, then a click on each of
three lines — one of them a construction line — and the circle is there, inside their triangle."""
import os
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
import Part  # noqa: E402
from incad.qt import QtCore, QtGui, QtWidgets, viewport_of  # noqa: E402
from incad.core import commands, prefs  # noqa: E402
from incad.ui import shell  # noqa: E402

V = App.Vector


def sketch():
    return Gui.ActiveDocument.getInEdit().Object


def click(sk, x, y):
    from incad.ui import dof_handles
    view = Gui.ActiveDocument.ActiveView
    vp = viewport_of(view)
    pos = QtCore.QPointF(*dof_handles.screen(view, vp, sk.getGlobalPlacement().multVec(V(x, y, 0))))
    L, N = QtCore.Qt.LeftButton, QtCore.Qt.NoButton
    for kind, button, buttons, ms in ((QtCore.QEvent.MouseMove, N, N, 250), (QtCore.QEvent.MouseButtonPress, L, L, 60),
                                      (QtCore.QEvent.MouseButtonRelease, L, N, 600)):
        QtWidgets.QApplication.sendEvent(vp, QtGui.QMouseEvent(kind, pos, QtCore.QPointF(vp.mapToGlobal(pos.toPoint())),
                                                                button, buttons, QtCore.Qt.NoModifier))
        harness.pump(ms)


def test_0_three_clicks_make_the_circle():
    prefs.set_remember_new_choice(True)
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    commands.REGISTRY.run("sketch.start")
    harness.pump(600)
    shell.instance().plane_panel.button("XY_Plane").click()
    harness.pump(1200)
    sk = sketch()
    lines = [sk.addGeometry(Part.LineSegment(V(*a, 0), V(*b, 0)))
             for a, b in (((0, 0), (30, 0)), ((30, 0), (0, 40)), ((0, 40), (0, 0)))]
    sk.setConstruction(lines[1], True)
    sk.recompute()
    Gui.SendMsgToActiveView("ViewFit")
    harness.pump(600)
    res = commands.REGISTRY.run("sketch.circle_tangent")
    assert res.ok, res.error
    harness.pump(300)
    for x, y in ((15, 0), (15, 20), (0, 20)):  # the middles of the three lines
        click(sk, x, y)
    circles = [g for g in sk.Geometry if g.TypeId == "Part::GeomCircle"]
    assert len(circles) == 1, [g.TypeId for g in sk.Geometry]
    c = circles[0]
    assert abs(c.Radius - 10) < 1e-4 and abs(c.Center.x - 10) < 1e-4 and abs(c.Center.y - 10) < 1e-4, \
        (c.Center, c.Radius)
    assert shell.instance().slot_tool is None, "the tool ends with the circle"


def test_1_two_lines_and_a_click_away():
    """Two lines clicked, the circle shown following the pointer, then a click away from them places it touching the
    two (the user: "two lines or three")."""
    sk = sketch()
    before = len(sk.Geometry)
    a = sk.addGeometry(Part.LineSegment(V(60, 0, 0), V(100, 0, 0)))
    b = sk.addGeometry(Part.LineSegment(V(60, 0, 0), V(60, 40, 0)))
    sk.recompute()
    harness.pump(300)
    res = commands.REGISTRY.run("sketch.circle_tangent")
    assert res.ok, res.error
    harness.pump(300)
    click(sk, 80, 0)
    tool = shell.instance().slot_tool
    assert tool is not None and tool._ghost is not None, "the circle shows from the first click"
    click(sk, 60, 20)
    click(sk, 70, 10)  # away from both: the circle touching the two, radius about 10
    circles = [g for g in sk.Geometry[before:] if g.TypeId == "Part::GeomCircle"]
    assert len(circles) == 1, [g.TypeId for g in sk.Geometry]
    c = circles[0]
    assert abs(c.Center.x - 60 - c.Radius) < 1e-4 and abs(c.Center.y - c.Radius) < 1e-4, (c.Center, c.Radius)
    assert 7 < c.Radius < 13, c.Radius
    assert shell.instance().slot_tool is None


harness.run([test_0_three_clicks_make_the_circle, test_1_two_lines_and_a_click_away])
