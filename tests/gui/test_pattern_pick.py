import os
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
import Part  # noqa: E402
from pivy import coin  # noqa: E402,F401
from PySide6.QtTest import QTest  # noqa: E402
from incad.qt import QtCore, QtWidgets  # noqa: E402
from incad.core import commands, document, prefs  # noqa: E402
from incad.features import own_pattern  # noqa: E402
from incad.ui import shell  # noqa: E402
import headless.util as util  # noqa: E402

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


def ok():
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(700)


def top_edges(f, z):
    return ["Edge%d" % (i + 1) for i, e in enumerate(f.Shape.Edges)
            if abs(e.BoundBox.ZMin - z) < 1e-6 and abs(e.BoundBox.ZMax - z) < 1e-6]


def test_0_setup():
    prefs.set_remember_new_choice(True)
    prefs.set_default_units("mm")
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    pad = util.add_pad(body(), util.add_rect_sketch(body(), w=40, h=20), 10)
    body().Tip = pad
    doc().recompute()
    top = [i + 1 for i, f in enumerate(pad.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6][0]
    sk = body().newObject("Sketcher::SketchObject", "HoleSk")
    sk.AttachmentSupport = [(pad, "Face%d" % top)]
    sk.MapMode = "FlatFace"
    sk.addGeometry(Part.Circle(App.Vector(8, 10, 0), App.Vector(0, 0, 1), 2))
    doc().recompute()
    pocket = body().newObject("PartDesign::Pocket", "Pocket")
    pocket.Profile, pocket.Type = sk, 1
    body().Tip = pocket
    doc().recompute()
    ST["pad"], ST["pocket"] = pad.Name, pocket.Name
    harness.pump(300)


def test_1_a_face_clicked_in_the_view_picks_the_feature_that_made_it():
    pocket = doc().getObject(ST["pocket"])
    wall = [i + 1 for i, f in enumerate(pocket.Shape.Faces) if f.Surface.TypeId == "Part::GeomCylinder"][0]
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("feature.rect_pattern")
    harness.pump(900)
    assert dialog() is not None
    if dialog().active_pick() != "features":
        dialog().field("features").click()
        harness.pump(500)
    assert dialog().active_pick() == "features"
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, pocket.Name, "Face%d" % wall)
    harness.pump(700)
    assert dialog().values()["features"] == ST["pocket"], dialog().values()["features"]
    assert not dialog().error_text(), dialog().error_text()
    w = dialog().field("count1")
    w.setText("3")
    w.editingFinished.emit()
    harness.pump(800)
    ok()
    pattern = body().Tip
    assert own_pattern.is_own(pattern) and own_pattern.kind_of(pattern) == "rectangular", pattern.TypeId  # our own pattern
    assert [o.Name for o in pattern.Originals] == [ST["pocket"]], [o.Name for o in pattern.Originals]
    assert "Invalid" not in pattern.State, pattern.getStatusString()


def test_2_include_dependent_features_takes_the_hole_cut_into_the_picked_feature():
    pad, pocket = doc().getObject(ST["pad"]), doc().getObject(ST["pocket"])
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, pad.Name)
    commands.REGISTRY.run("feature.rect_pattern")
    harness.pump(900)
    assert dialog().values()["features"] == pad.Name, dialog().values()
    box = dialog().field("children")
    box.setChecked(True)
    harness.pump(300)
    w = dialog().field("count1")
    w.setText("2")
    w.editingFinished.emit()
    harness.pump(900)
    assert not dialog().error_text(), dialog().error_text()
    shown = dialog().field("features").text()
    assert pad.Label in shown and "+1 dependent" in shown, shown  # names, and what the switch added
    ok()
    pattern = body().Tip
    assert own_pattern.is_own(pattern) and own_pattern.kind_of(pattern) == "rectangular", pattern.TypeId  # our own pattern
    assert [o.Name for o in pattern.Originals] == [pad.Name, pocket.Name], [o.Name for o in pattern.Originals]
    assert "Invalid" not in pattern.State, pattern.getStatusString()
    shell.edit_feature(pattern)  # editing shows the pad with the toggle on, not the pocket as a second pick
    harness.pump(900)
    assert dialog().values()["features"] == pad.Name and dialog().values()["children"] is True, dialog().values()
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(700)


def test_3_even_distribution_between_two_faces_in_the_dialog():
    pad, pocket = doc().getObject(ST["pad"]), doc().getObject(ST["pocket"])  # block x 0..40, hole x 6..10
    for o in [o for o in body().Group if own_pattern.kind_of(o) == "rectangular"]:  # earlier tests' patterns
        doc().removeObject(o.Name)
    body().Tip = pocket
    doc().recompute()
    base = body().Tip
    assert base is pocket
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, pocket.Name)
    commands.REGISTRY.run("feature.rect_pattern")
    harness.pump(900)
    assert dialog().values()["features"] == pocket.Name, dialog().values()
    dialog().set_values({"fit1": "faces", "count1": "3"})
    harness.pump(500)
    assert dialog().field("from1").isVisible() and dialog().field("to1").isVisible()
    assert not dialog().field("spacing1").isVisible()
    assert not dialog().field("dir1").isVisible(), "the direction follows the faces in Equal spacing"
    assert dialog().field("flip1").isVisible()

    def face_at(x):
        return "Face%d" % ([i + 1 for i, f in enumerate(base.Shape.Faces) if f.Surface.TypeId == "Part::GeomPlane"
                            and abs(f.CenterOfMass.x - x) < 1e-6 and abs(f.Surface.Axis.x) > 0.99][0])

    def edge_at(x):  # a vertical block edge on the far end face
        return "Edge%d" % ([i + 1 for i, e in enumerate(base.Shape.Edges) if e.Curve.TypeId == "Part::GeomLine"
                            and abs(e.BoundBox.XMin - x) < 1e-6 and abs(e.BoundBox.XMax - x) < 1e-6
                            and e.BoundBox.ZLength > 1][0])

    for key, x in (("from1", 0), ("to1", 40)):
        if dialog().active_pick() != key:  # the first pick hands over to the next empty field by itself
            dialog().field(key).click()
            harness.pump(500)
        assert dialog().active_pick() == key, dialog().active_pick()
        Gui.Selection.clearSelection()
        # the start is a face, the end an edge: both are accepted
        Gui.Selection.addSelection(doc().Name, base.Name, face_at(x) if key == "from1" else edge_at(x))
        harness.pump(700)
    assert not dialog().error_text(), dialog().error_text()
    # Flip direction turns the copies the other way round the original: the holes then land outside the block
    dialog().field("flip1").setChecked(True)  # the real switch, as a click on it
    harness.pump(1500)
    assert "changes nothing" in dialog().error_text(), (dialog().error_text(), dialog().values().get("flip1"))
    dialog().field("flip1").setChecked(False)
    harness.pump(1500)
    assert not dialog().error_text(), dialog().error_text()
    ok()
    pattern = body().Tip
    assert own_pattern.is_own(pattern) and own_pattern.kind_of(pattern) == "rectangular", pattern.TypeId  # our own pattern
    # hole x 6..10 (margin 6 from the left face): last far edge at 40 - 6 = 34 -> spacing (34 - 10) / 2 = 12
    assert abs(pattern.Length.Value - 24.0) < 1e-3, pattern.Length.Value
    assert "Invalid" not in pattern.State, pattern.getStatusString()


def test_4_the_field_starts_empty_and_asks_for_a_feature_then_picks_add_up():
    pad, pocket = doc().getObject(ST["pad"]), doc().getObject(ST["pocket"])
    for o in [o for o in body().Group if own_pattern.kind_of(o) == "rectangular"]:
        doc().removeObject(o.Name)
    body().Tip = pocket
    doc().recompute()
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("feature.rect_pattern")
    harness.pump(1200)
    assert dialog().values()["features"] == "", "nothing picked, nothing guessed: %s" % dialog().values()
    assert "Click a feature" in dialog().error_text(), dialog().error_text()
    assert not dialog().ok_button.isEnabled()
    assert dialog().active_pick() == "features"

    def click_row(obj):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(doc().Name, obj.Name)
        harness.pump(700)

    click_row(pad)
    assert dialog().values()["features"] == pad.Name, dialog().values()["features"]
    assert not dialog().error_text(), dialog().error_text()
    assert dialog().ok_button.isEnabled()
    click_row(pocket)
    assert sorted(dialog().values()["features"].split(",")) == sorted([pad.Name, pocket.Name]), \
        dialog().values()["features"]
    click_row(pocket)  # a plain click on a picked one takes it off again
    assert dialog().values()["features"] == pad.Name, dialog().values()["features"]
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(700)


harness.run([test_0_setup, test_1_a_face_clicked_in_the_view_picks_the_feature_that_made_it,
             test_2_include_dependent_features_takes_the_hole_cut_into_the_picked_feature,
             test_3_even_distribution_between_two_faces_in_the_dialog,
             test_4_the_field_starts_empty_and_asks_for_a_feature_then_picks_add_up])
