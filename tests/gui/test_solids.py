"""A box drawn away from the part is its own Solid: a sketch on its face lands in that Solid, and the feature
made from it (a Cut) works on the box, not on the part."""
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
from incad.core import commands, prefs  # noqa: E402
from incad.ui import shell  # noqa: E402
import headless.util as util  # noqa: E402


def _body():
    from incad.core import document
    return document.primary_body(App.ActiveDocument)


def test_0_part_with_a_box_aside():
    from incad.core import document
    from incad.features import extrude
    prefs.set_remember_new_choice(True)
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    body = _body()
    pad = util.add_pad(body, util.add_rect_sketch(body), 10)
    body.Tip = pad
    far = util.add_rect_sketch(body, name="Far")
    far.AttachmentOffset = App.Placement(App.Vector(50, 50, 0), App.Rotation())
    App.ActiveDocument.recompute()
    box = extrude.create(App.ActiveDocument, body, far, extrude.defaults(App.ActiveDocument, body, far))
    App.ActiveDocument.recompute()
    assert box.getParentGeoFeatureGroup() is not body
    for b in document.bodies(App.ActiveDocument):
        document.show_result(b)
    shell.instance().schedule_refresh()
    harness.pump(600)
    rows = shell.instance().browser.tree_rows()
    assert sum(1 for _, label, kind, _ in rows if kind == "end_of_part") == 1, rows


def test_1_a_sketch_on_the_box_and_a_cut_through_it():
    from incad.core import document
    sh = shell.instance()
    box = App.ActiveDocument.getObject("Extrude")
    other = box.getParentGeoFeatureGroup()
    top = [i + 1 for i, f in enumerate(box.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6][0]
    sk = sh.sketches.start_on(box, "Face%d" % top)  # as a double-click on the face / New Sketch there
    harness.pump(1200)
    assert sk is not None and sk.getParentGeoFeatureGroup() is other, "the sketch belongs to the box's Solid"
    assert sh.sketches.editing() is sk
    centre = sk.Placement.inverse().multVec(App.Vector(60, 55, 10))  # the middle of the box's top, in sketch coords
    sk.addGeometry(Part.Circle(App.Vector(centre.x, centre.y, 0), App.Vector(0, 0, 1), 2))
    App.ActiveDocument.recompute()
    commands.REGISTRY.run("sketch.finish")
    harness.pump(900)
    assert sh.sketches.editing() is None
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(App.ActiveDocument.Name, sk.Name)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(1200)
    fs = sh.feature_session
    assert fs is not None and fs.body is other, (fs and fs.body.Label)
    fs.dialog.field("operation").button("cut").click()
    fs.dialog.field("extent").button("through_all").click()
    harness.pump(1200)
    assert fs.dialog.error_text() == "", fs.dialog.error_text()
    QTest.mouseClick(fs.dialog.ok_button, QtCore.Qt.LeftButton)
    harness.pump(900)
    assert sh.feature_session is None
    pocket = other.Tip
    where = [(o.Label, o.TypeId, o.getParentGeoFeatureGroup().Label if o.getParentGeoFeatureGroup() else None)
             for o in App.ActiveDocument.Objects if o.isDerivedFrom("PartDesign::Feature")]
    assert pocket.TypeId == "PartDesign::FeatureSubtractivePython" and pocket.getParentGeoFeatureGroup() is other, (pocket.Label, where)
    import math
    assert abs(pocket.Shape.Volume - (2000 - math.pi * 4 * 10)) < 2.0, pocket.Shape.Volume
    assert abs(_body().Tip.Shape.Volume - 2000) < 1e-6, "the part is untouched"
    assert len(document.bodies(App.ActiveDocument)) == 2, "no third Solid"


def test_2_end_of_part_rolls_every_solid_to_the_dropped_row():
    """One history, one End of Part (Inventor): dropped on the box's row (Solid2) it rolls the box's Cut back
    while the part keeps its Pad; the Solid Bodies row is no place in the history and moves nothing."""
    from incad.core import document
    body = _body()
    tip = body.Tip
    other = [b for b in document.bodies(App.ActiveDocument) if b is not body][0]
    cut = other.Tip
    box = cut.BaseFeature
    b = shell.instance().browser
    item = b.item_by_label(box.Label)
    assert item is not None and item.parent() is not None and item.parent().data(0, QtCore.Qt.UserRole + 1) != "body", \
        "the box's row is in the part's history, not under its Solid"
    b.drop_end_of_part_on(item)
    harness.pump(900)
    assert body.Tip is tip and other.Tip is box, (body.Tip, other.Tip)
    assert not cut.Visibility and box.Visibility
    rows = b.tree_rows()
    labels = [label for _, label, kind, _ in rows if kind in ("feature", "end_of_part")]
    assert labels.index("End of Part") == labels.index(box.Label) + 1, labels
    item = b.item_by_label("Solid Bodies (2)")
    b.drop_end_of_part_on(item)
    harness.pump(600)
    assert body.Tip is tip and other.Tip is box and b.tree_rows() == rows
    b.drop_end_of_part_on(b.item_by_label(cut.Label))
    harness.pump(900)
    assert other.Tip is cut and cut.Visibility
    log = open(os.environ["INCAD_LOG"]).read()
    assert "End of Part moved after %s" % box.Label in log, log[-1500:]


def test_3_a_sketch_left_in_an_empty_solid_cuts_the_box_its_face_belongs_to():
    """Sketch16 on 2026-10-10: drawn on the box's face but left in a Solid of its own (its first feature was
    deleted); a Cut on it made a fourth Solid. The Cut must go to the box's Solid, the sketch with it, and the
    emptied Solid must go."""
    from incad.core import document
    sh = shell.instance()
    box = App.ActiveDocument.getObject("Extrude")
    other = box.getParentGeoFeatureGroup()
    stray = document.new_solid(App.ActiveDocument)
    stray_name = stray.Name
    top = [i + 1 for i, f in enumerate(box.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6][0]
    sk = stray.newObject("Sketcher::SketchObject", "Stray")
    sk.AttachmentSupport = [(box, "Face%d" % top)]
    sk.MapMode = "FlatFace"
    App.ActiveDocument.recompute()
    centre = sk.Placement.inverse().multVec(App.Vector(55, 52, 10))
    sk.addGeometry(Part.Circle(App.Vector(centre.x, centre.y, 0), App.Vector(0, 0, 1), 1.5))
    App.ActiveDocument.recompute()
    sh.schedule_refresh()
    harness.pump(600)
    assert len(document.bodies(App.ActiveDocument)) == 3
    volume = other.Tip.Shape.Volume
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(App.ActiveDocument.Name, sk.Name)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(1200)
    fs = sh.feature_session
    assert fs is not None and fs.body is other, (fs and fs.body.Label)
    fs.dialog.field("operation").button("cut").click()
    fs.dialog.field("extent").button("through_all").click()
    harness.pump(1200)
    assert fs.dialog.error_text() == "", fs.dialog.error_text()
    QTest.mouseClick(fs.dialog.ok_button, QtCore.Qt.LeftButton)
    harness.pump(900)
    assert sh.feature_session is None
    cut = other.Tip
    assert cut.TypeId == "PartDesign::FeatureSubtractivePython" and cut.Profile[0] is sk, cut.Label
    assert cut.Shape.Volume < volume - 1, (cut.Shape.Volume, volume)
    assert sk.getParentGeoFeatureGroup() is other, "the sketch moved to the box's Solid"
    assert App.ActiveDocument.getObject(stray_name) is None, "the emptied Solid is gone"
    assert len(document.bodies(App.ActiveDocument)) == 2
    log = open(os.environ["INCAD_LOG"]).read()
    assert "its own Solid (Solid3)" not in log and "Solid4" not in log, log[-1500:]
    App.ActiveDocument.undo()
    harness.pump(900)
    assert App.ActiveDocument.getObject(stray_name) is not None and len(document.bodies(App.ActiveDocument)) == 3
    assert sk.getParentGeoFeatureGroup().Name == stray_name, "one undo step puts everything back"


harness.run([test_0_part_with_a_box_aside, test_1_a_sketch_on_the_box_and_a_cut_through_it,
             test_2_end_of_part_rolls_every_solid_to_the_dropped_row, test_3_a_sketch_left_in_an_empty_solid_cuts_the_box_its_face_belongs_to])
