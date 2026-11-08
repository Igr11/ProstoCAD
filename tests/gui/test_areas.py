"""Inventor profiles: sketch lines may cross; the areas between them are extruded (one automatically, several by
clicking the shown areas in the feature's window); the user's sketch stays editable under the feature and the feature follows it."""
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


def shown_areas():
    return [o for o in doc().Objects if o.Name.startswith("Area") and "InCadAreaPick" in o.PropertiesList]


def start_on(sk):
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, sk.Name)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(1000)


def click(which):
    """A click on the shown area `which(areas)` picks (found again each time: a feature made again with its undo
    step rolled back shows the areas anew)."""
    area = which(shown_areas())
    Gui.Selection.addSelection(doc().Name, area.Name)  # the click on the shown area
    harness.pump(1200)


def smallest(areas):
    return min(areas, key=lambda o: o.Shape.Area)


def leftmost(areas):
    return min(areas, key=lambda o: o.Shape.BoundBox.Center.x)


def ring(areas):
    return next(o for o in areas if len(o.Shape.Faces[0].Wires) == 2)


def test_2_several_areas_open_the_window_at_once():
    """The user 2026-11-08: the window opens at once (no click before it), the areas shown in it, none taken."""
    sk = crossing_sketch(body(), name="Cross")
    # on top of the first pad: its areas extrude onto the part (a sketch beside it would make its own Solid)
    sk.AttachmentOffset = App.Placement(App.Vector(0, 0, 10), App.Rotation())
    doc().recompute()
    harness.pump(300)
    v_before = body().Shape.Volume
    undo_before = doc().UndoCount
    start_on(sk)
    areas = shown_areas()
    assert len(areas) == 3 and dialog() is not None, ([o.Name for o in areas], dialog())
    fs = s().feature_session
    assert fs.feature is None, "nothing made before an area is taken"
    assert "area" in s().status.texts()["prompt"].lower(), s().status.texts()
    assert regions.is_profile(fs.profile) and regions.source_of(fs.profile) is sk
    click(smallest)
    assert fs.feature is not None, "the click makes the feature"
    assert fs.feature.ViewObject.Transparency == 60 and not fs.feature.ViewObject.Selectable, "see-through preview"
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(800)
    assert not shown_areas(), "the shown areas are gone"
    pad = body().Tip
    assert pad.ViewObject.Selectable, "the preview is picked again"
    import math
    assert abs(body().Shape.Volume - v_before - math.pi * 9 * 10) < 0.5, (v_before, body().Shape.Volume)
    assert safe.busy() is None and doc().UndoCount == undo_before + 1, (doc().UndoNames[:3], undo_before)
    assert "Area" not in " ".join(doc().UndoNames[:2]), doc().UndoNames[:3]
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
    """Click the sketch row (no 3D selection), then Extrude: its areas are offered in the window."""
    sk = crossing_sketch(body(), name="Cross3")
    sk.AttachmentOffset = App.Placement(App.Vector(80, 0, 0), App.Rotation())
    doc().recompute()
    harness.pump(500)
    Gui.Selection.clearSelection()
    assert s().browser.select_row(sk.Name)
    harness.pump(300)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(1000)
    assert shown_areas() and dialog() is not None, "the areas are shown in the window"
    s()._on_escape()
    harness.pump(500)


def test_4_esc_cancels_the_window_and_its_areas():
    sk = crossing_sketch(body(), name="Cross2")
    sk.AttachmentOffset = App.Placement(App.Vector(40, 0, 0), App.Rotation())
    doc().recompute()
    start_on(sk)
    assert shown_areas()
    click(lambda a: a[0])
    s()._on_escape()
    harness.pump(600)
    assert not shown_areas() and dialog() is None
    assert not doc().HasPendingTransaction


def test_5_clicks_take_areas_in_and_out():
    """Each click takes an area in, a click again leaves it out; the preview follows."""
    import math
    sk = crossing_sketch(body(), name="Cross5")
    sk.AttachmentOffset = App.Placement(App.Vector(0, 0, 10), App.Rotation())
    doc().recompute()
    harness.pump(300)
    start_on(sk)
    click(leftmost)
    click(ring)
    fs = s().feature_session
    assert len(fs.profile.Shape.Wires) == 2, "the two halves as one outline, the circle a hole"
    tool = fs.feature.AddSubShape
    assert abs(tool.Volume - (200 - math.pi * 9) * 10) < 1e-3, tool.Volume
    click(leftmost)  # out again
    tool = fs.feature.AddSubShape
    assert abs(tool.Volume - (100 - math.pi * 9) * 10) < 1e-3, tool.Volume
    click(ring)  # nothing taken: nothing made, the window waits
    assert fs.feature is None and dialog() is not None
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(800)
    assert not shown_areas()


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
    """Two separate circles: both areas are shown in the window and the clicked one is used."""
    import math
    sk = _two_circles("Circles6", 200)
    start_on(sk)
    areas = shown_areas()
    assert len(areas) == 2 and dialog() is not None, [o.Name for o in areas]
    assert "⌘A all" in s().status.texts()["prompt"] or "Ctrl+A all" in s().status.texts()["prompt"], s().status.texts()
    click(smallest)
    tool = s().feature_session.feature.AddSubShape
    assert abs(tool.Volume - math.pi * 9 * 10) < 1e-3, tool.Volume
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(800)


def test_7_all_areas_key_takes_every_area():
    """⌘A (Ctrl+A) while the window shows the areas: all of them."""
    import math
    sk = _two_circles("Circles7", 260)
    start_on(sk)
    assert dialog() is not None and s()._all_areas.isEnabled(), "the areas are shown and ⌘A is on"
    s()._all_areas.trigger()
    harness.pump(1500)
    tool = s().feature_session.feature.AddSubShape
    assert abs(tool.Volume - math.pi * (9 + 25) * 10) < 1e-3, tool.Volume
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(800)
    s().refresh()
    harness.pump(300)
    assert not s()._all_areas.isEnabled(), "⌘A is off again once the window is closed"


def test_8_two_areas_touching_at_corners_extrude():
    """Two crossed rectangles, both L-shaped halves taken (they touch at two corners): they were read back as the
    two whole rectangles and the Join failed (the user 2026-11-08)."""
    sk = body().newObject("Sketcher::SketchObject", "Ls")
    sk.AttachmentSupport = [(body().Origin.OriginFeatures[3], "")]
    sk.MapMode = "FlatFace"
    sk.AttachmentOffset = App.Placement(App.Vector(400, 0, 0), App.Rotation())
    for x, y, w, h in ((0, 0, 40, 20), (20, 10, 40, 20)):
        p = [App.Vector(x, y, 0), App.Vector(x + w, y, 0), App.Vector(x + w, y + h, 0), App.Vector(x, y + h, 0)]
        sk.addGeometry([Part.LineSegment(p[i], p[(i + 1) % 4]) for i in range(4)])
    doc().recompute()
    harness.pump(300)
    start_on(sk)
    for i in range(2):  # the two L's (600 each), not the square
        click(lambda a, i=i: sorted(a, key=lambda o: (-round(o.Shape.Area, 3), o.Shape.BoundBox.Center.x))[i])
    fs = s().feature_session
    assert fs.feature is not None and fs.feature.isValid(), "the two L's extrude"
    assert abs(fs.feature.AddSubShape.Volume - 1200 * 10) < 1e-3, fs.feature.AddSubShape.Volume
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(800)
    ST["ls"] = body().Tip.Name if body().Tip.Name.startswith("Extrude") else None


def test_9_editing_shows_the_areas_taken():
    """Editing an Extrude on areas: its sketch's areas show again, the taken one picked."""
    from incad.ui import feature_session
    pad = doc().getObject(ST["pad2"])
    shell.edit_feature(pad)
    harness.pump(1200)
    areas = shown_areas()
    assert dialog() is not None and len(areas) == 3, [o.Name for o in areas]
    picked = [o for o in areas if tuple(o.ViewObject.ShapeColor[:3]) ==
              tuple(round(c, 6) for c in feature_session.AREA_PICKED) or
              all(abs(a - b) < 1e-2 for a, b in zip(o.ViewObject.ShapeColor[:3], feature_session.AREA_PICKED))]
    assert len(picked) == 1, [o.ViewObject.ShapeColor for o in areas]
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(800)
    assert not shown_areas() and dialog() is None


harness.run([test_0_setup, test_1_one_area_extrudes_at_once, test_2_several_areas_open_the_window_at_once,
             test_3_tree_shows_the_sketch_under_the_feature_and_edits_follow,
             test_3b_a_crossed_sketch_clicked_in_the_tree_is_the_profile, test_4_esc_cancels_the_window_and_its_areas,
             test_5_clicks_take_areas_in_and_out, test_6_a_clean_sketch_with_several_areas_is_picked_too,
             test_7_all_areas_key_takes_every_area, test_8_two_areas_touching_at_corners_extrude,
             test_9_editing_shows_the_areas_taken])
