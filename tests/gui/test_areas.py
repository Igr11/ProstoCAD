"""Inventor profiles: sketch lines may cross; the areas between them are extruded (one automatically, several by
clicking the shown area); the user's sketch stays editable under the feature and the feature follows it."""
import os
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
import Part  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from incad.qt import QtCore  # noqa: E402
from incad.core import commands, document, prefs, regions, safe  # noqa: E402
from incad.ui import shell  # noqa: E402
import headless.util as util  # noqa: E402
from headless.test_regions import crossing_sketch  # noqa: E402

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


def test_0_setup():
    prefs.set_remember_new_choice(True)
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)


def test_1_one_area_extrudes_at_once():
    sk = util.add_rect_sketch(body(), name="RectWithStray")
    sk.addGeometry(Part.LineSegment(App.Vector(2, 2, 0), App.Vector(8, 2, 0)))  # a stray line inside: still one area
    doc().recompute()
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, sk.Name)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(1000)
    assert dialog() is not None, "the Extrude dialog opens on the one area"
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(800)
    pad = body().Tip
    assert pad.TypeId == "PartDesign::FeatureAdditivePython" and abs(pad.Shape.Volume - 2000) < 1e-3, pad.Shape.Volume
    ST["pad1"] = pad.Name


def test_2_several_areas_are_shown_and_clicked():
    sk = crossing_sketch(body(), name="Cross")
    # on top of the first pad: its areas extrude onto the part (a sketch beside it would make its own Solid)
    sk.AttachmentOffset = App.Placement(App.Vector(0, 0, 10), App.Rotation())
    doc().recompute()
    harness.pump(300)
    v_before = body().Shape.Volume
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, sk.Name)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(1000)
    areas = [o for o in doc().Objects if o.Name.startswith("Area")]
    assert len(areas) == 3 and dialog() is None, ([o.Name for o in areas], dialog())
    assert "area" in s().status.texts()["prompt"].lower(), s().status.texts()
    circle = min(areas, key=lambda o: o.Shape.Area)
    Gui.Selection.addSelection(doc().Name, circle.Name)  # the click on the shown area
    harness.pump(1000)
    assert dialog() is not None, "the dialog opens on the clicked area"
    assert not [o for o in doc().Objects if o.Name.startswith("Area")], "the shown areas are gone"
    fs = s().feature_session
    assert regions.is_profile(fs.profile) and regions.source_of(fs.profile) is sk
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(800)
    pad = body().Tip
    import math
    assert abs(body().Shape.Volume - v_before - math.pi * 9 * 10) < 0.5, (v_before, body().Shape.Volume)
    assert safe.busy() is None and "Area" not in " ".join(doc().UndoNames[:2]), doc().UndoNames[:3]
    ST["pad2"], ST["sk"] = pad.Name, sk.Name


def test_3_tree_shows_the_sketch_under_the_feature_and_edits_follow():
    from incad.core import context
    pad, sk = doc().getObject(ST["pad2"]), doc().getObject(ST["sk"])
    rows = s().browser._items()
    names = [i.data(0, QtCore.Qt.UserRole) for i in rows]
    assert sk.Name in names and not any(n and n.endswith("Area") for n in names), names
    context.set_target(sk.Name)
    commands.REGISTRY.run("browser.edit")
    harness.pump(1000)
    assert Gui.ActiveDocument.getInEdit() is not None and Gui.ActiveDocument.getInEdit().Object is sk
    circle = [i for i, g in enumerate(sk.Geometry) if g.TypeId == "Part::GeomCircle"][0]
    sk.delGeometry(circle)
    sk.addGeometry(Part.Circle(App.Vector(15, 5, 0), App.Vector(0, 0, 1), 4))  # a bigger circle
    doc().recompute()
    v_before = body().Shape.Volume
    commands.REGISTRY.run("sketch.finish")
    harness.pump(1500)
    import math
    assert abs(body().Shape.Volume - v_before - math.pi * (16 - 9) * 10) < 0.5, (v_before, body().Shape.Volume)


def test_3b_a_crossed_sketch_clicked_in_the_tree_is_the_profile():
    """Click the sketch row (no 3D selection), then Extrude: its areas are offered."""
    sk = crossing_sketch(body(), name="Cross3")
    sk.AttachmentOffset = App.Placement(App.Vector(80, 0, 0), App.Rotation())
    doc().recompute()
    harness.pump(500)
    Gui.Selection.clearSelection()
    assert s().browser.select_row(sk.Name)
    harness.pump(300)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(1000)
    assert [o for o in doc().Objects if o.Name.startswith("Area")], "the areas are shown"
    s()._on_escape()
    harness.pump(500)


def test_4_esc_cancels_the_area_pick():
    sk = crossing_sketch(body(), name="Cross2")
    sk.AttachmentOffset = App.Placement(App.Vector(40, 0, 0), App.Rotation())
    doc().recompute()
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, sk.Name)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(1000)
    assert [o for o in doc().Objects if o.Name.startswith("Area")]
    s()._on_escape()
    harness.pump(600)
    assert not [o for o in doc().Objects if o.Name.startswith("Area")] and dialog() is None
    assert not doc().HasPendingTransaction


def test_5_shift_click_takes_several_areas():
    """The user: Shift-click several areas for one Extrude (a plain click then starts it with them all)."""
    import math
    from incad.qt import QtWidgets
    sk = crossing_sketch(body(), name="Cross5")
    sk.AttachmentOffset = App.Placement(App.Vector(0, 0, 10), App.Rotation())
    doc().recompute()
    harness.pump(300)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, sk.Name)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(1000)
    areas = [o for o in doc().Objects if o.Name.startswith("Area") and "InCadAreaPick" in o.PropertiesList]
    assert len(areas) == 3 and dialog() is None, [o.Name for o in areas]
    left = min(areas, key=lambda o: o.Shape.BoundBox.Center.x)
    ring = next(o for o in areas if o is not left and len(o.Shape.Faces[0].Wires) == 2)
    real = QtWidgets.QApplication.keyboardModifiers
    QtWidgets.QApplication.keyboardModifiers = staticmethod(lambda: QtCore.Qt.ShiftModifier)
    try:
        Gui.Selection.addSelection(doc().Name, left.Name)
        harness.pump(800)
    finally:
        QtWidgets.QApplication.keyboardModifiers = real
    assert dialog() is None, "Shift-click waits for more"
    assert "1 areas taken" in s().status.texts()["prompt"], s().status.texts()
    Gui.Selection.addSelection(doc().Name, ring.Name)  # a plain click: start with both
    harness.pump(1500)
    assert dialog() is not None, "the dialog opens on the areas taken"
    prof = s().feature_session.profile
    assert len(prof.Shape.Wires) == 2, "the two halves as one outline, the circle a hole"
    tool = s().feature_session.feature.AddSubShape
    assert abs(tool.Volume - (200 - math.pi * 9) * 10) < 1e-3, tool.Volume
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(800)


def _two_circles(name, x):
    """A clean sketch: two separate circles, nothing crossing (2026-11-07: its areas are picked too)."""
    sk = body().newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(body().Origin.OriginFeatures[3], "")]
    sk.MapMode = "FlatFace"
    sk.AttachmentOffset = App.Placement(App.Vector(x, 0, 0), App.Rotation())
    sk.addGeometry([Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 3),
                    Part.Circle(App.Vector(20, 0, 0), App.Vector(0, 0, 1), 5)])
    doc().recompute()
    harness.pump(300)
    return sk


def test_6_a_clean_sketch_with_several_areas_is_picked_too():
    """Two separate circles: no longer extruded whole at once; both areas are shown and the clicked one is used."""
    import math
    sk = _two_circles("Circles6", 200)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, sk.Name)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(1000)
    areas = [o for o in doc().Objects if o.Name.startswith("Area") and "InCadAreaPick" in o.PropertiesList]
    assert len(areas) == 2 and dialog() is None, [o.Name for o in areas]
    assert "⌘A all" in s().status.texts()["prompt"] or "Ctrl+A all" in s().status.texts()["prompt"], s().status.texts()
    small = min(areas, key=lambda o: o.Shape.Area)
    Gui.Selection.addSelection(doc().Name, small.Name)
    harness.pump(1500)
    assert dialog() is not None, "the dialog opens on the clicked circle"
    tool = s().feature_session.feature.AddSubShape
    assert abs(tool.Volume - math.pi * 9 * 10) < 1e-3, tool.Volume
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(800)


def test_7_all_areas_key_takes_every_area():
    """⌘A (Ctrl+A) while the areas are shown: the feature on all of them at once."""
    import math
    sk = _two_circles("Circles7", 260)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, sk.Name)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(1000)
    assert dialog() is None and s()._all_areas.isEnabled(), "the areas are shown and ⌘A is on"
    s()._all_areas.trigger()
    harness.pump(1500)
    assert dialog() is not None, "the dialog opens on every area"
    tool = s().feature_session.feature.AddSubShape
    assert abs(tool.Volume - math.pi * (9 + 25) * 10) < 1e-3, tool.Volume
    assert not s()._all_areas.isEnabled(), "⌘A is off again once the areas are gone"
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(800)


harness.run([test_0_setup, test_1_one_area_extrudes_at_once, test_2_several_areas_are_shown_and_clicked,
             test_3_tree_shows_the_sketch_under_the_feature_and_edits_follow,
             test_3b_a_crossed_sketch_clicked_in_the_tree_is_the_profile, test_4_esc_cancels_the_area_pick,
             test_5_shift_click_takes_several_areas, test_6_a_clean_sketch_with_several_areas_is_picked_too,
             test_7_all_areas_key_takes_every_area])
