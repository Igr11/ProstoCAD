"""Sketch ▸ Mirror in the window (the user: the copy should keep the original's size and follow it, without bringing
its dimensions; breaking the link afterwards): one side of a rectangle and the Y axis picked, Mirror -> a copy of the
whole rectangle, no new dimension; the width changed -> the copy follows; the copy's right-click menu has Break Mirror
Link (the original's has not); after it the copy keeps its shape and stays put when the width changes again."""
import os
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
import Part  # noqa: E402
import Sketcher  # noqa: E402
from incad.core import commands, document, prefs  # noqa: E402
from incad.ui import shell  # noqa: E402

V = App.Vector
ST = {}
DIMS = ("Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle")


def s():
    return shell.instance()


def sk():
    return App.ActiveDocument.getObject(ST["sk"])


def dims():
    return sum(1 for c in sk().Constraints if c.Type in DIMS)


def width(geos):
    return round(Part.Compound([sk().Geometry[g].toShape() for g in geos]).BoundBox.XLength, 6)


def menu_labels(geo):
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(App.ActiveDocument.Name, sk().Name, "Edge%d" % (geo + 1))
    harness.pump(200)
    return [label for label, _icon, _fn in s()._constraint_menu_items()], s()._constraint_menu_items()


def test_0_rectangle():
    prefs.set_remember_new_choice(True)
    prefs.set_default_units("mm")
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    doc = App.ActiveDocument
    body = document.primary_body(doc)
    xy = [o for o in body.Origin.OriginFeatures if o.Role == "XY_Plane"][0]
    Gui.Selection.addSelection(doc.Name, xy.Name)
    commands.REGISTRY.run("sketch.start")
    harness.pump(1000)
    k = s().sketches.editing()
    pts = [V(-30, 5, 0), V(-20, 5, 0), V(-20, 11, 0), V(-30, 11, 0)]
    ids = [k.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4])) for i in range(4)]
    for i in range(4):
        k.addConstraint(Sketcher.Constraint("Coincident", ids[i], 2, ids[(i + 1) % 4], 1))
    for i, t in enumerate(("Horizontal", "Vertical", "Horizontal", "Vertical")):
        k.addConstraint(Sketcher.Constraint(t, ids[i]))
    ST["w"] = k.addConstraint(Sketcher.Constraint("DistanceX", ids[0], 1, ids[0], 2, 10))
    k.addConstraint(Sketcher.Constraint("DistanceY", ids[1], 1, ids[1], 2, 6))
    doc.recompute()
    Gui.SendMsgToActiveView("ViewFit")
    harness.pump(600)
    ST["sk"], ST["ids"] = k.Name, ids


def test_1_mirror_without_dimensions():
    before, count = dims(), sk().GeometryCount
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(App.ActiveDocument.Name, sk().Name, "Edge%d" % (ST["ids"][2] + 1))  # one side
    Gui.Selection.addSelection(App.ActiveDocument.Name, sk().Name, "V_Axis")
    harness.pump(300)
    r = commands.REGISTRY.run("sketch.mirror")
    harness.pump(1000)
    assert r.ok, r.error
    copies = list(range(count, sk().GeometryCount))
    assert len(copies) == 4, copies  # the whole rectangle from one side
    assert dims() == before, "the original's dimensions came along"
    assert sk().solve() == 0
    sk().setDatum(ST["w"], App.Units.Quantity("14 mm"))
    App.ActiveDocument.recompute()
    harness.pump(500)
    assert width(copies) == 14.0, width(copies)  # the copy follows
    ST["copies"] = copies


def test_2_break_mirror_link_on_the_copy_only():
    labels, _ = menu_labels(ST["ids"][0])
    assert "Break Mirror Link" not in labels, ("offered on the original", labels)
    labels, items = menu_labels(ST["copies"][0])
    assert "Break Mirror Link" in labels, labels
    dict((label, fn) for label, _i, fn in items)["Break Mirror Link"]()
    harness.pump(800)
    # no mirror tie (Symmetric about the Y axis) is left; a rectangle's own centre point keeps its Symmetric
    assert not [c for c in sk().Constraints if c.Type == "Symmetric" and c.Third == -2]
    sk().setDatum(ST["w"], App.Units.Quantity("18 mm"))
    App.ActiveDocument.recompute()
    harness.pump(500)
    assert width(ST["copies"]) == 14.0, width(ST["copies"])  # free now: it stays as it was
    assert width(ST["ids"]) == 18.0
    assert sk().solve() == 0


def test_3_tool_first_then_the_shape_then_the_line():
    """The user's law: the tool first, then the clicks. Nothing selected: Mirror, a click on one side of the rectangle
    takes it whole, a click on the Y axis makes the copy."""
    k = sk()
    count = k.GeometryCount
    Gui.Selection.clearSelection()
    r = commands.REGISTRY.run("sketch.mirror")
    harness.pump(400)
    assert r.ok and s().slot_tool is not None, r.error
    side = k.Geometry[ST["ids"][0]]
    _click_at((side.StartPoint + side.EndPoint) * 0.5)
    assert k.GeometryCount == count, "a click on a shape only takes it"
    assert sorted(s().slot_tool.geos) == sorted(ST["ids"]), s().slot_tool.geos
    _click_at(V(0, 40, 0))  # the Y axis, clear of everything
    assert k.GeometryCount == count + 4, k.GeometryCount
    assert k.solve() == 0
    assert s().slot_tool is None


def _click_at(p):
    from PySide6.QtTest import QTest
    from incad.qt import QtCore
    from incad.ui import dof_handles as dh
    view = Gui.ActiveDocument.ActiveView
    vp = view.graphicsView().viewport()
    x, y = dh.screen(view, vp, p)
    at = QtCore.QPoint(int(round(x)), int(round(y)))
    QTest.mouseMove(vp, at)
    harness.pump(100)
    QTest.mouseClick(vp, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, at)
    harness.pump(1000)


def test_4_select_mirror_then_click_the_line():
    """The user's way (it failed): a chain selected as a box gives it — its ends and its lines — then Mirror, then a
    click on the chain's own last segment: the rest is mirrored about it and joins up."""
    k = sk()
    P = [V(3, -9, 0), V(6, -6, 0), V(11, -10, 0), V(17.7, -9, 0), V(26.4, -12, 0), V(28.9, -7.9, 0)]
    ch = [k.addGeometry(Part.LineSegment(P[i], P[i + 1])) for i in range(5)]
    for i in range(4):
        k.addConstraint(Sketcher.Constraint("Coincident", ch[i], 2, ch[i + 1], 1))
    for g in ch:
        k.addConstraint(Sketcher.Constraint("Distance", g, k.Geometry[g].length()))
    App.ActiveDocument.recompute()
    Gui.SendMsgToActiveView("ViewFit")
    harness.pump(500)
    from incad.core import sketch_symmetric as sym
    Gui.Selection.clearSelection()
    for g in ch:
        for pos in (1, 2):
            Gui.Selection.addSelection(App.ActiveDocument.Name, k.Name, sym.subname(k, ("point", g, pos)))
        Gui.Selection.addSelection(App.ActiveDocument.Name, k.Name, "Edge%d" % (g + 1))
    harness.pump(300)
    count = k.GeometryCount
    r = commands.REGISTRY.run("sketch.mirror")
    harness.pump(500)
    assert r.ok, r.error
    assert k.GeometryCount == count, "made at once, without the mirror line clicked"
    tool = s().slot_tool
    assert tool is not None and type(tool).__name__ == "MirrorTool", tool
    seg = k.Geometry[ch[4]]
    _click_at((seg.StartPoint + seg.EndPoint) * 0.5)  # the chain's own last segment
    assert k.GeometryCount == count + 4, k.GeometryCount
    assert k.solve() == 0, (list(k.RedundantConstraints), list(k.ConflictingConstraints))
    assert s().slot_tool is None, "the tool should end once the copy is made"


def test_5_select_mirror_then_click_the_y_axis():
    k = sk()
    count = k.GeometryCount
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(App.ActiveDocument.Name, k.Name, "Edge%d" % (ST["ids"][0] + 1))
    r = commands.REGISTRY.run("sketch.mirror")
    harness.pump(500)
    assert r.ok and s().slot_tool is not None, r.error
    lit = sorted(n for sel in Gui.Selection.getSelectionEx(App.ActiveDocument.Name) if sel.ObjectName == k.Name
                 for n in sel.SubElementNames)
    assert lit == sorted("Edge%d" % (g + 1) for g in ST["ids"]), ("the whole rectangle shows caught", lit)
    _click_at(V(0, 40, 0))  # the Y axis, clear of everything
    assert k.GeometryCount == count + 4, k.GeometryCount
    assert k.solve() == 0


def test_6_shift_click_selects_the_whole_shape_at_once():
    """The user: the shape should take the selection colour right when it is selected, not only once a tool takes it.
    Shift-click on one side selects the whole rectangle, before any tool."""
    from PySide6.QtTest import QTest
    from incad.qt import QtCore
    from incad.ui import dof_handles as dh
    k = sk()
    side = k.Geometry[ST["ids"][0]]
    p = (side.StartPoint + side.EndPoint) * 0.5
    view = Gui.ActiveDocument.ActiveView
    vp = view.graphicsView().viewport()
    x, y = dh.screen(view, vp, p)
    at = QtCore.QPoint(int(round(x)), int(round(y)))
    Gui.Selection.clearSelection()
    QTest.mouseMove(vp, at)
    harness.pump(200)
    QTest.mouseClick(vp, QtCore.Qt.LeftButton, QtCore.Qt.ShiftModifier, at)  # the user: Shift, not a double-click
    harness.pump(600)
    lit = sorted(n for sel in Gui.Selection.getSelectionEx(App.ActiveDocument.Name) if sel.ObjectName == k.Name
                 for n in sel.SubElementNames)
    assert lit == sorted("Edge%d" % (g + 1) for g in ST["ids"]), ("Shift-click: the whole rectangle, at once", lit)
    count = k.GeometryCount
    r = commands.REGISTRY.run("sketch.mirror")
    harness.pump(400)
    assert r.ok and s().slot_tool is not None, r.error
    _click_at(V(0, -45, 0))  # the Y axis
    assert k.GeometryCount == count + 4, k.GeometryCount


def test_9_cleanup():
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("sketch.finish")
    harness.pump(800)
    App.closeDocument(App.ActiveDocument.Name)


harness.run([test_0_rectangle, test_1_mirror_without_dimensions, test_2_break_mirror_link_on_the_copy_only,
             test_3_tool_first_then_the_shape_then_the_line, test_4_select_mirror_then_click_the_line,
             test_5_select_mirror_then_click_the_y_axis, test_6_shift_click_selects_the_whole_shape_at_once,
             test_9_cleanup])
