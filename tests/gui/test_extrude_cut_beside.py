"""Extrude ▸ Cut of a profile that lies off the part (the user, bug2.FCStd: Cut did nothing the first time; Intersect
and then Cut worked): the dialog opens with Join, and a Join that does not touch the part is made as its own Solid —
switching to Cut swapped it in place, so it cut that empty Solid. Now a change of operation makes it again: Cut cuts
the part, the extra Solid goes, and back to Join it is its own Solid again."""
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
from incad.core import commands, document, prefs  # noqa: E402
from incad.ui import shell  # noqa: E402
import headless.util as util  # noqa: E402

V = App.Vector
ST = {}


def s():
    return shell.instance()


def doc():
    return App.ActiveDocument


def dialog():
    fs = s().feature_session
    return fs.dialog if fs is not None else None


def solids():
    return [o for o in doc().Objects if o.TypeId == "PartDesign::Body"]


def test_0_part_and_a_profile_above_it():
    prefs.set_remember_new_choice(True)
    prefs.set_default_units("mm")
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    body = document.primary_body(doc())
    util.add_pad(body, util.add_rect_sketch(body, 20, 10, "Base"), 5)
    sk = body.newObject("Sketcher::SketchObject", "Hole")
    sk.AttachmentSupport = [(util.xy_plane(body), "")]
    sk.MapMode = "FlatFace"
    sk.AttachmentOffset = App.Placement(V(0, 0, 8), App.Rotation())  # 3 mm above the part: touches nothing
    sk.addGeometry(Part.Circle(V(10, 5, 0), V(0, 0, 1), 2))
    doc().recompute()
    harness.pump(500)
    ST.update(body=body, sk=sk, volume=body.Shape.Volume, solids=len(solids()))


def test_1_join_then_cut_cuts_the_part():
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, ST["sk"].Name)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(1000)
    if dialog().values()["operation"] == "auto":  # ProstoCAD opens with Auto: this is about Join
        dialog().field("operation").button("join").click()
        harness.pump(800)
    assert dialog().values()["operation"] == "join", dialog().values()
    assert len(solids()) == ST["solids"] + 1, "a Join off the part is its own Solid"
    dialog().field("operation").button("cut").click()
    harness.pump(800)
    w = dialog().field("distance")
    w.setText("6 mm")  # from 8 down to 2: into the part
    w.editingFinished.emit()
    harness.pump(1000)
    assert not dialog().error_text(), dialog().error_text()
    f = s().feature_session.feature
    assert f.getParentGeoFeatureGroup() is ST["body"], "the cut belongs in the part"
    assert len(solids()) == ST["solids"], "the empty Solid goes"
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(1200)
    assert s().feature_session is None
    body = ST["body"]
    assert "Invalid" not in body.Tip.State, body.Tip.getStatusString()
    cut = 3.141592653589793 * 2 * 2 * 3  # the hole, 3 mm deep (z 5 down to 2)
    assert abs(ST["volume"] - body.Shape.Volume - cut) < 0.05, (ST["volume"], body.Shape.Volume)


def test_2_back_to_join_is_its_own_solid_again():
    from incad.features import extrude
    commands.REGISTRY.run("edit.undo")
    harness.pump(800)
    if hasattr(extrude, "_turn_if_idle"):
        return  # ProstoCAD: a Join that would touch nothing turns toward the part instead
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, ST["sk"].Name)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(1000)
    dialog().field("operation").button("cut").click()
    harness.pump(800)
    dialog().field("operation").button("join").click()
    harness.pump(800)
    f = s().feature_session.feature
    assert f.getParentGeoFeatureGroup() is not ST["body"], "a Join off the part: its own Solid, not a second lump"
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(800)


def test_3_join_to_a_face_of_the_part_joins_it():
    """The user: "a sketch on a plane extruded To: the part is not a single part, a Boolean was needed". The Join
    (ProstoCAD: Auto) first went into its own Solid (its default length did not reach the part); To the part's top
    face it is made again, in the part, and the part is one solid."""
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, ST["sk"].Name)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(1000)
    pad = [o for o in ST["body"].Group if o.TypeId == "PartDesign::FeatureAdditivePython"][0]
    top = [i + 1 for i, f in enumerate(pad.Shape.Faces) if abs(f.CenterOfMass.z - 5) < 1e-6
           and f.Surface.TypeId == "Part::GeomPlane"][0]
    d = dialog()
    d.set_values({"extent": "to", "to_face": "%s:Face%d" % (pad.Name, top)})
    d._emit()
    harness.pump(1500)
    assert not d.error_text(), d.error_text()
    f = s().feature_session.feature
    assert f.getParentGeoFeatureGroup() is ST["body"], "To the part's face: it joins the part"
    assert len(solids()) == ST["solids"], "no Solid of its own left"
    QTest.mouseClick(d.ok_button, QtCore.Qt.LeftButton)
    harness.pump(1200)
    body = ST["body"]
    assert len(body.Shape.Solids) == 1, len(body.Shape.Solids)
    peg = 3.141592653589793 * 2 * 2 * 3  # from z 8 down to the top at z 5
    assert abs(body.Shape.Volume - ST["volume"] - peg) < 0.05, (ST["volume"], body.Shape.Volume)


class _Computed:
    def __init__(self):
        self.names = set()

    def slotRecomputedObject(self, obj):
        self.names.add(obj.Name)


def test_4_join_to_cut_and_cancel_leave_the_part_alone():
    """The user: "Extrude Cut lags, even the simplest". A change of Join to Cut and Cancel roll the dialog's step
    back; the abort took its value out of the model's values, and FreeCAD then recomputed every feature using any
    value: on a part with a modeled thread 6 s. The part's features that use a value are not computed again."""
    from incad.core import model
    pad = [o for o in ST["body"].Group if o.TypeId == "PartDesign::FeatureAdditivePython"][0]
    name = model.new_feature_parameter(doc(), "5 mm")
    pad.setExpression("Length", "InCadModel.%s" % name)
    doc().recompute()
    harness.pump(500)
    volume = ST["body"].Shape.Volume
    watch = _Computed()
    App.addDocumentObserver(watch)
    try:
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(doc().Name, ST["sk"].Name)
        commands.REGISTRY.run("feature.extrude")
        harness.pump(1000)
        watch.names.clear()
        dialog().field("operation").button("join").click()
        harness.pump(800)
        dialog().field("operation").button("cut").click()
        harness.pump(1000)
        assert pad.Name not in watch.names, ("Join to Cut computed the part again", sorted(watch.names))
        QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
        harness.pump(1000)
        assert pad.Name not in watch.names, ("Cancel computed the part again", sorted(watch.names))
    finally:
        App.removeDocumentObserver(watch)
    assert s().feature_session is None
    assert "Touched" not in pad.State and "Invalid" not in ST["body"].Tip.State
    assert abs(ST["body"].Shape.Volume - volume) < 1e-6
    pad.setExpression("Length", None)


def test_9_cleanup():
    App.closeDocument(doc().Name)


harness.run([test_0_part_and_a_profile_above_it, test_1_join_then_cut_cuts_the_part,
             test_2_back_to_join_is_its_own_solid_again, test_3_join_to_a_face_of_the_part_joins_it,
             test_4_join_to_cut_and_cancel_leave_the_part_alone, test_9_cleanup])
