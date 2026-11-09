"""A sketch point (here a rectangle's centre point) is easy to catch on a Retina screen (the user: one time in five): FreeCAD's pick radius
counts device pixels, so InCad sets it to REACH points times the pixel ratio (incad.ui.pick_radius) — the point is
preselected a few points off its centre, where the radius of 5 gave a diagonal, and on it, where its Symmetric
constraint's icon was (now hidden); a radius the user chose stays."""
import os
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
import Part  # noqa: E402
import Sketcher  # noqa: E402
from incad.core import commands, document, prefs, sketch as sketches  # noqa: E402
from incad.ui import pick_radius, shell  # noqa: E402

V = App.Vector
ST = {}


def ratio():
    return Gui.getMainWindow().devicePixelRatio()


def test_0_widened_on_activation():
    prefs.set_remember_new_choice(True)
    prefs.set_default_units("mm")
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    r = App.ParamGet(pick_radius.VIEW).GetFloat("PickRadius", 5.0)
    want = pick_radius.REACH * max(ratio(), 1.0)  # REACH points of the screen on any screen (since 2026-11-07)
    assert abs(r - want) < 1e-6, (r, ratio())


def test_1_centre_point_caught_off_centre():
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    doc = App.ActiveDocument
    body = document.primary_body(doc)
    xy = [o for o in body.Origin.OriginFeatures if o.Role == "XY_Plane"][0]
    Gui.Selection.addSelection(doc.Name, xy.Name)
    commands.REGISTRY.run("sketch.start")
    harness.pump(1000)
    k = shell.instance().sketches.editing()
    pts = [V(10, 10, 0), V(70, 10, 0), V(70, 50, 0), V(10, 50, 0)]
    ids = [k.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4])) for i in range(4)]
    for i in range(4):
        k.addConstraint(Sketcher.Constraint("Coincident", ids[i], 2, ids[(i + 1) % 4], 1))
    sketches.add_rectangle_centres(k, 0, set())
    doc.recompute()
    Gui.SendMsgToActiveView("ViewFit")
    harness.pump(1500)
    if ratio() <= 1:
        return  # an ordinary screen: FreeCAD's radius is unchanged
    from PySide6.QtTest import QTest
    from incad.qt import QtCore
    from incad.ui import dof_handles as dh
    view = Gui.ActiveDocument.ActiveView
    vp = view.graphicsView().viewport()
    cx, cy = dh.screen(view, vp, V(40, 30, 0))
    for dx, dy in ((0, 0), (1, 1), (-1, 2), (2, -3), (-3, 2)):  # on it the Symmetric icon's; off it a line's (probed)
        QTest.mouseMove(vp, QtCore.QPoint(int(round(cx)) + dx, int(round(cy)) + dy))
        harness.pump(150)
        pre = Gui.Selection.getPreselection()
        names = list(pre.SubElementNames)
        assert names and names[0].startswith("Vertex"), ((dx, dy), names)
    # around it: its Symmetric constraint's icon sat just below the point and took the clicks (hidden now)
    icon = []
    for dx in range(-8, 9, 2):
        for dy in range(-8, 9, 2):
            QTest.mouseMove(vp, QtCore.QPoint(int(round(cx)) + dx, int(round(cy)) + dy))
            harness.pump(60)
            pre = Gui.Selection.getPreselection()
            if any(n.startswith("Constraint") for n in pre.SubElementNames):
                icon.append((dx, dy))
    assert not icon, ("the constraint's icon is picked near the point", icon)


def test_2_a_chosen_radius_stays():
    p = App.ParamGet(pick_radius.VIEW)
    p.SetFloat("PickRadius", 7.0)
    prefs.set_pick_radius_widened(False)
    pick_radius.widen()
    assert p.GetFloat("PickRadius", 5.0) == 7.0


def test_9_cleanup():
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("sketch.finish")
    harness.pump(800)
    App.closeDocument(App.ActiveDocument.Name)


harness.run([test_0_widened_on_activation, test_1_centre_point_caught_off_centre, test_2_a_chosen_radius_stays,
             test_9_cleanup])
