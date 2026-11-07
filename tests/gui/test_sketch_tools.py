import os
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from incad.qt import QtCore, QtGui, QtWidgets  # noqa: E402
from incad import actions  # noqa: E402
from incad.core import commands, prefs  # noqa: E402
from incad.ui import shell  # noqa: E402


def method():
    for w in QtWidgets.QApplication.allWidgets():
        if isinstance(w, QtWidgets.QComboBox) and w.objectName() == "comboBox1" and w.count():
            return w.currentText()
    return None


def test_0_sketch():
    prefs.set_remember_new_choice(True)
    # a dimension the sketch fixes already asks (a modal question that would wait forever here): keep it as a reference
    shell.REFERENCE_ASK = lambda text: True
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    commands.REGISTRY.run("sketch.start")
    harness.pump(600)
    shell.instance().plane_panel.button("XY_Plane").click()
    harness.pump(1200)
    assert Gui.ActiveDocument.getInEdit() is not None


def test_1_every_variant_starts_with_its_method():
    want = {"sketch.rectangle": "Corner, width, height", "sketch.rectangle_center": "Center, width, height",
            "sketch.rectangle_3pt": "3 corners", "sketch.rectangle_center_3pt": "Center, 2 corners",
            "sketch.circle": "Center", "sketch.circle_3pt": "3 rim points", "sketch.arc": "3 rim points",
            "sketch.arc_center": "Center", "sketch.slot_arc": "Arc ends", "sketch.slot_arc_flat": "Flat ends"}
    for cid in list(actions.SKETCH_VARIANTS) + list(want):
        if cid == "sketch.polygon_n":  # asks for the number of sides in a dialog: answer it
            from incad.qt import QtCore
            QtCore.QTimer.singleShot(600, lambda: QtWidgets.QApplication.activeModalWidget() and
                                     QtWidgets.QApplication.activeModalWidget().accept())
        res = commands.REGISTRY.run(cid)
        harness.pump(500)
        assert res.ok, (cid, res.error)
        if cid in want:
            assert method() == want[cid], (cid, method())
        commands.REGISTRY.run("sketch.stop")
        harness.pump(300)


def _click_sketch(sk, x, y):
    """A real left click at sketch point (x, y)."""
    from incad.qt import QtCore, QtGui
    from incad.ui import dof_handles
    view = Gui.ActiveDocument.ActiveView
    vp = view.graphicsView().viewport()
    pos = QtCore.QPointF(*dof_handles.screen(view, vp, sk.getGlobalPlacement().multVec(App.Vector(x, y, 0))))
    L, N = QtCore.Qt.LeftButton, QtCore.Qt.NoButton
    for kind, button, buttons, ms in ((QtCore.QEvent.MouseMove, N, N, 250), (QtCore.QEvent.MouseButtonPress, L, L, 60),
                                      (QtCore.QEvent.MouseButtonRelease, L, N, 600)):
        QtWidgets.QApplication.sendEvent(vp, QtGui.QMouseEvent(kind, pos, QtCore.QPointF(vp.mapToGlobal(pos.toPoint())),
                                                                button, buttons, QtCore.Qt.NoModifier))
        harness.pump(ms)


def test_1b_offset_takes_the_whole_rectangle_and_keeps_sharp_corners():
    """InCad's own Offset: one click on an edge takes the whole rectangle, a click outside places a copy with sharp
    corners, the value box opens on its dimension; a lone line gives one parallel line (no slot)."""
    import Part
    import Sketcher
    from PySide6.QtTest import QTest
    from incad.qt import QtCore
    sk = Gui.ActiveDocument.getInEdit().Object
    pts = [App.Vector(40, 0, 0), App.Vector(60, 0, 0), App.Vector(60, 10, 0), App.Vector(40, 10, 0)]
    first = sk.GeometryCount
    ids = [sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4])) for i in range(4)]
    for k in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", ids[k], 2, ids[(k + 1) % 4], 1))
    for k, kind in enumerate(("Horizontal", "Vertical", "Horizontal", "Vertical")):  # as FreeCAD's rectangle tool
        sk.addConstraint(Sketcher.Constraint(kind, ids[k]))
    App.ActiveDocument.recompute()
    Gui.SendMsgToActiveView("ViewFit")
    harness.pump(600)
    res = commands.REGISTRY.run("sketch.offset")
    harness.pump(400)
    assert res.ok, res.error
    _click_sketch(sk, 50, 0)  # the bottom edge
    tool = shell.instance().slot_tool
    assert tool is not None and len(tool.chains) == 1, ("the outline is picked", tool, tool and tool.chains,
                                                        tool and len(tool._callbacks),
                                                        open(os.environ["INCAD_LOG"]).read()[-500:])
    assert sorted(shell.instance().slot_tool.chains[0][0]) == sorted(ids)
    lit = sorted(n for sel in Gui.Selection.getSelectionEx(App.ActiveDocument.Name) if sel.Object is sk
                 for n in sel.SubElementNames)
    assert lit == sorted("Edge%d" % (g + 1) for g in ids), ("the whole outline shows caught", lit)  # the user
    _click_sketch(sk, 50, -2)  # 2 below it: the copy outside
    harness.pump(600)
    new = [g for g in range(first + 4, sk.GeometryCount)  # not the rectangle's construction centre point
           if type(sk.Geometry[g]).__name__ != "Point"]
    assert len(new) == 4 and all(type(sk.Geometry[g]).__name__ == "LineSegment" for g in new), \
        [type(sk.Geometry[g]).__name__ for g in new]
    corners = sorted({(round(p.x, 4), round(p.y, 4)) for g in new for p in (sk.Geometry[g].StartPoint,
                                                                             sk.Geometry[g].EndPoint)})
    want = [(38, -2), (38, 12), (62, -2), (62, 12)]  # sharp; a real click lands on a whole pixel: ~0.01 mm off
    assert len(corners) == 4 and all(abs(a - c) < 0.05 and abs(b - e) < 0.05 for (a, b), (c, e) in zip(corners, want)), \
        corners
    box = shell.instance().value_box
    assert box is not None, "the value box opens on the offset dimension"
    QTest.keyClicks(box.edit, "3")
    QTest.keyClick(box.edit, QtCore.Qt.Key_Return)
    harness.pump(800)
    corners = sorted({(round(p.x, 4), round(p.y, 4)) for g in new for p in (sk.Geometry[g].StartPoint,
                                                                             sk.Geometry[g].EndPoint)})
    xs = sorted({c[0] for c in corners})
    orig = sorted({round(p.x, 4) for g in ids for p in (sk.Geometry[g].StartPoint, sk.Geometry[g].EndPoint)})
    assert abs((xs[0] - orig[0]) + 3) < 1e-4 and abs((xs[1] - orig[1]) - 3) < 1e-4, (xs, orig)
    # an ellipse: its offset is a B-spline, shown without FreeCAD's green curvature comb or degree label
    general = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Sketcher/General")
    shown = {p: general.GetBool(p, True) for p in ("BSplineCombVisible", "BSplineDegreeVisible")}
    assert not any(shown.values()), shown
    # a lone line: one parallel line on the pointer's side
    line = sk.addGeometry(Part.LineSegment(App.Vector(40, 30, 0), App.Vector(60, 30, 0)))
    App.ActiveDocument.recompute()
    harness.pump(400)
    count = sk.GeometryCount
    commands.REGISTRY.run("sketch.offset")
    harness.pump(400)
    _click_sketch(sk, 50, 30)
    _click_sketch(sk, 50, 33)
    harness.pump(600)
    added = list(range(count, sk.GeometryCount))
    assert len(added) == 1 and abs(sk.Geometry[added[0]].StartPoint.y - 33) < 0.05, \
        [(type(sk.Geometry[g]).__name__, sk.Geometry[g].StartPoint) for g in added]
    if shell.instance().value_box is not None:
        shell.instance().value_box.close()
        harness.pump(300)
    sk.delGeometries(list(range(first, sk.GeometryCount)))  # the later tests click where the sketch is empty
    App.ActiveDocument.recompute()
    harness.pump(300)


def test_2_ribbon_split_button_remembers():
    rb = shell.instance().ribbon
    b = rb.button("sketch.rectangle")
    act = [a for a in b.menu().actions() if a.text() == "3-Point Rectangle"][0]
    act.trigger()
    harness.pump(500)
    assert method() == "3 corners", method()
    commands.REGISTRY.run("sketch.stop")
    harness.pump(300)
    assert b.property("incad_cmd") == "sketch.rectangle_3pt", b.property("incad_cmd")  # remembered (label "Rectangle")


def test_3_hide_and_show_constraints_from_the_menu():
    import Part
    import Sketcher
    from incad.core import sketch
    from incad.ui import marking_layout
    sk = Gui.ActiveDocument.getInEdit().Object
    a = sk.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(20, 0, 0)))
    sk.addConstraint(Sketcher.Constraint("Horizontal", a))
    sk.addConstraint(Sketcher.Constraint("DistanceX", a, 1, a, 2, 20))
    App.ActiveDocument.recompute()
    assert "sketch.constraints_toggle" in marking_layout.LAYOUT["sketch"]["list"]
    commands.REGISTRY.run("sketch.constraints_toggle")
    harness.pump(400)
    assert sketch.constraints_hidden(sk)
    Gui.ActiveDocument.ActiveView.saveImage(os.environ.get("INCAD_SHOT", "/tmp/incad_sketch.png"), 800, 500, "Current")
    commands.REGISTRY.run("sketch.constraints_show")
    harness.pump(400)
    assert not sketch.constraints_hidden(sk)


def test_4_overall_slot_by_three_clicks():
    sh = shell.instance()
    sk = Gui.ActiveDocument.getInEdit().Object
    before = sk.GeometryCount
    commands.REGISTRY.run("sketch.slot_overall")
    harness.pump(400)
    tool = sh.slot_tool
    assert tool is not None
    view = Gui.ActiveDocument.ActiveView
    pl = sk.getGlobalPlacement()
    for x, y in ((-30, 40), (0, 40), (-15, 44)):  # real clicks (the tool's handler was called directly before)
        _click_sketch(sk, x, y)
    App.ActiveDocument.recompute()
    assert sk.GeometryCount == before + 4, (before, sk.GeometryCount)
    import Part
    bb = Part.Compound([g.toShape() for g in sk.Geometry[before:]]).BoundBox
    assert abs(bb.XMin - (-30)) < 0.5 and abs(bb.XMax - 0) < 0.5 and abs(bb.YLength - 8) < 0.5, bb
    sh._on_escape()
    harness.pump(300)
    assert sh.slot_tool is None


def test_5_construction_button_toggles_the_drawing_mode():
    import Part
    sh = shell.instance()
    sk = Gui.ActiveDocument.getInEdit().Object
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("sketch.construction")
    harness.pump(300)
    assert sh.construction_mode and sh.ribbon.button("sketch.construction").isChecked()
    g = sk.addGeometry(Part.LineSegment(App.Vector(50, 0, 0), App.Vector(60, 0, 0)))
    App.ActiveDocument.recompute()
    harness.pump(500)
    assert sk.getConstruction(g), "drawn in construction mode"
    commands.REGISTRY.run("sketch.construction")
    harness.pump(300)
    assert not sh.construction_mode and not sh.ribbon.button("sketch.construction").isChecked()
    g2 = sk.addGeometry(Part.LineSegment(App.Vector(50, 5, 0), App.Vector(60, 5, 0)))
    App.ActiveDocument.recompute()
    harness.pump(500)
    assert not sk.getConstruction(g2), "back to normal"



def test_5b_one_click_on_a_picked_construction_line_makes_it_normal_and_ends_the_mode():
    import Part
    sh = shell.instance()
    sk = Gui.ActiveDocument.getInEdit().Object
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("sketch.construction")
    harness.pump(300)
    assert sh.construction_mode
    g = sk.addGeometry(Part.LineSegment(App.Vector(50, 10, 0), App.Vector(60, 10, 0)))
    App.ActiveDocument.recompute()
    harness.pump(500)
    assert sk.getConstruction(g)
    Gui.Selection.addSelection(App.ActiveDocument.Name, sk.Name, "Edge%d" % (g + 1))
    commands.REGISTRY.run("sketch.construction")  # ONE click
    harness.pump(500)
    assert not sk.getConstruction(g), "the picked line is normal"
    assert not sh.construction_mode and not sh.ribbon.button("sketch.construction").isChecked(), "mode ended"
    g2 = sk.addGeometry(Part.LineSegment(App.Vector(50, 15, 0), App.Vector(60, 15, 0)))
    App.ActiveDocument.recompute()
    harness.pump(500)
    assert not sk.getConstruction(g2), "new geometry is normal after the single click"


def test_6_one_picked_side_switches_only_that_side():
    """The user: making one line construction changed all the lines joined to it. Only the picked edge switches."""
    from ProfileLib import RegularPolygon
    sk = Gui.ActiveDocument.getInEdit().Object
    first = sk.GeometryCount
    RegularPolygon.makeRegularPolygon(sk, 6, App.Vector(100, 0, 0), App.Vector(110, 0, 0), False)
    App.ActiveDocument.recompute()
    harness.pump(300)
    sides = [i for i in range(first, sk.GeometryCount) if sk.Geometry[i].TypeId == "Part::GeomLineSegment"]
    assert len(sides) == 6, sides
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(App.ActiveDocument.Name, sk.Name, "Edge%d" % (sides[2] + 1))
    commands.REGISTRY.run("sketch.construction")
    harness.pump(500)
    assert [sk.getConstruction(i) for i in sides] == [k == 2 for k in range(6)], [sk.getConstruction(i) for i in sides]
    assert not shell.instance().construction_mode  # a selection switches geometry, not the drawing mode
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(App.ActiveDocument.Name, sk.Name, "Edge%d" % (sides[2] + 1))
    commands.REGISTRY.run("sketch.construction")
    harness.pump(500)
    assert not any(sk.getConstruction(i) for i in sides), [sk.getConstruction(i) for i in sides]



def test_7_a_polygon_drawn_on_the_origin_keeps_its_centre_there():
    from ProfileLib import RegularPolygon
    sk = Gui.ActiveDocument.getInEdit().Object
    first = sk.GeometryCount
    RegularPolygon.makeRegularPolygon(sk, 6, App.Vector(0, 0, 0), App.Vector(9, 2, 0), False)
    App.ActiveDocument.recompute()
    harness.pump(800)
    circle = [i for i in range(first, sk.GeometryCount) if sk.Geometry[i].TypeId == "Part::GeomCircle"][0]
    tied = [c for c in sk.Constraints if c.Type == "Coincident" and c.First == circle and c.FirstPos == 3
            and c.Second == -1]
    assert len(tied) == 1, [(c.Type, c.First, c.FirstPos, c.Second) for c in sk.Constraints]



def test_8_double_click_with_a_deleted_preselection_is_ignored():
    """FreeCAD can hand back a preselection whose object was just deleted ('Object already deleted' in the log)."""
    from incad.ui import shell

    class Dead:
        def __getattr__(self, name):
            raise RuntimeError("Object already deleted")
    real = Gui.Selection.getPreselection
    Gui.Selection.getPreselection = lambda: Dead()
    try:
        assert shell.instance()._preselected_dimension() == (None, None)
    finally:
        Gui.Selection.getPreselection = real



def test_9_sketch_handlers_ignore_a_sketch_deleted_meanwhile():
    """They run just after a sketch changes; the sketch may be gone by then (an assembly placing parts did it)."""
    from incad.ui import shell
    from incad.core import log
    doc = App.ActiveDocument
    sk = doc.addObject("Sketcher::SketchObject", "Gone")
    doc.removeObject(sk.Name)
    seen = []
    log.set_reporter(lambda level, msg: seen.append(msg))
    try:
        shell.instance()._pin_new_polygons(sk)
        shell.instance()._make_construction(sk)
    finally:
        log.set_reporter(shell.instance()._report)
    assert not seen, seen


def test_10_delete_removes_selected_dimension_and_feature():
    """Backspace/Delete died with FreeCAD's hidden Edit menu: InCad's Delete works in a sketch and on features."""
    import Part
    import Sketcher
    sk = Gui.ActiveDocument.getInEdit().Object
    i = sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 20))
    App.ActiveDocument.recompute()
    harness.pump(400)
    assert i < len(sk.Constraints) and sk.Constraints[i].Type == "DistanceX"  # kept (as a reference if it repeats)
    n = len(sk.Constraints)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(App.ActiveDocument.Name, sk.Name, "Constraint%d" % (i + 1))
    commands.REGISTRY.run("edit.delete")
    harness.pump(600)
    assert len(sk.Constraints) == n - 1, (n, len(sk.Constraints))
    g = sk.addGeometry(Part.LineSegment(App.Vector(70, 0, 0), App.Vector(80, 0, 0)))
    App.ActiveDocument.recompute()
    harness.pump(300)
    ng = sk.GeometryCount
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(App.ActiveDocument.Name, sk.Name, "Edge%d" % (g + 1))
    commands.REGISTRY.run("edit.delete_key")
    harness.pump(600)
    assert sk.GeometryCount == ng - 1


def _send(kind, pos, button, buttons):
    """A mouse event on the 3D viewport, through InCad's navigation filter like a real click."""
    vp = Gui.ActiveDocument.ActiveView.graphicsView().viewport()
    types = {"press": QtCore.QEvent.MouseButtonPress, "release": QtCore.QEvent.MouseButtonRelease,
             "move": QtCore.QEvent.MouseMove}
    ev = QtGui.QMouseEvent(types[kind], pos, QtCore.QPointF(vp.mapToGlobal(pos.toPoint())), button, buttons,
                           QtCore.Qt.NoModifier)
    QtWidgets.QApplication.sendEvent(vp, ev)
    harness.pump(80)


def _click(x, y, button=QtCore.Qt.LeftButton):
    """Click the sketch point (x, y) in the view."""
    view = Gui.ActiveDocument.ActiveView
    vp = view.graphicsView().viewport()
    sk = Gui.ActiveDocument.getInEdit().Object
    sx, sy = view.getPointOnViewport(sk.getGlobalPlacement().multVec(App.Vector(x, y, 0)))
    dpr = vp.devicePixelRatioF()
    pos = QtCore.QPointF(sx / dpr, vp.height() - sy / dpr)
    _send("move", pos, QtCore.Qt.NoButton, QtCore.Qt.NoButton)
    _send("press", pos, button, button)
    _send("release", pos, button, QtCore.Qt.NoButton)
    harness.pump(150)


def test_11_right_click_finishes_a_spline():
    """FreeCAD's spline tools build the curve on a right click; InCad hands that click over instead of opening
    its marking menu while a Sketcher tool runs (the control points alone are what the user saw before)."""
    from incad.ui.marking_menu import MarkingMenu
    sk = Gui.ActiveDocument.getInEdit().Object
    before = sk.GeometryCount
    commands.REGISTRY.run("sketch.spline_cv")
    harness.pump(400)
    for x, y in ((-30, 60), (0, 80), (30, 60)):
        _click(x, y)
    assert sk.GeometryCount == before + 3, "three control points"
    _click(30, 60, QtCore.Qt.RightButton)
    harness.pump(400)
    assert not isinstance(QtWidgets.QApplication.activePopupWidget(), MarkingMenu), "no marking menu in a tool"
    splines = [g for g in sk.Geometry[before:] if g.TypeId == "Part::GeomBSplineCurve"]
    assert len(splines) == 1, [g.TypeId for g in sk.Geometry[before:]]
    poles = [(round(p.x), round(p.y)) for p in splines[0].getPoles()]
    assert poles == [(-30, 60), (0, 80), (30, 60)], poles
    shell.instance()._on_escape()  # the tool restarted (continuous mode): stop it
    harness.pump(300)


def test_12_right_click_without_a_tool_opens_the_marking_menu():
    from incad.ui.marking_menu import MarkingMenu
    sh = shell.instance()
    Gui.runCommand("Sketcher_StopOperation")
    harness.pump(300)
    assert not sh._sketch_tool_active()
    _click(-40, -60, QtCore.Qt.RightButton)
    harness.pump(300)
    menu = QtWidgets.QApplication.activePopupWidget()
    assert isinstance(menu, MarkingMenu), menu
    assert sh.menu_kind == "sketch", sh.menu_kind
    menu.close()
    harness.pump(200)


harness.run([test_0_sketch, test_1_every_variant_starts_with_its_method, test_1b_offset_takes_the_whole_rectangle_and_keeps_sharp_corners, test_2_ribbon_split_button_remembers,
             test_3_hide_and_show_constraints_from_the_menu, test_4_overall_slot_by_three_clicks,
             test_5_construction_button_toggles_the_drawing_mode,
             test_5b_one_click_on_a_picked_construction_line_makes_it_normal_and_ends_the_mode,
             test_6_one_picked_side_switches_only_that_side,
             test_7_a_polygon_drawn_on_the_origin_keeps_its_centre_there,
             test_8_double_click_with_a_deleted_preselection_is_ignored,
             test_9_sketch_handlers_ignore_a_sketch_deleted_meanwhile,
             test_10_delete_removes_selected_dimension_and_feature, test_11_right_click_finishes_a_spline,
             test_12_right_click_without_a_tool_opens_the_marking_menu])
