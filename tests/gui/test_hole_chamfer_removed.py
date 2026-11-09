"""Hole ▸ Chamfer taken off while the Hole window is open (the user: Counterbore added, Chamfer None, and the part was
gone from the screen until OK): the chamfer step that showed the hole goes, the hole shows instead."""
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

ST = {}


def dialog():
    fs = shell.instance().feature_session
    return fs.dialog if fs is not None else None


def shown(body):
    """The solid features drawn now."""
    return [o.Name for o in body.Group if o.isDerivedFrom("PartDesign::Feature") and o.Visibility]


def test_0_plate_with_a_chamfered_hole_and_a_chamfer_after():
    prefs.set_remember_new_choice(True)
    prefs.set_default_units("mm")
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    doc = App.ActiveDocument
    body = document.primary_body(doc)
    plate = util.add_pad(body, util.add_rect_sketch(body, w=30, h=20, name="Plate"), 6, name="Plate")
    body.Tip = plate
    doc.recompute()
    top = [i + 1 for i, f in enumerate(plate.Shape.Faces) if abs(f.CenterOfMass.z - 6) < 1e-6 and f.Area > 400][0]
    hs = body.newObject("Sketcher::SketchObject", "HoleAt")
    hs.AttachmentSupport = [(plate, "Face%d" % top)]
    hs.MapMode = "FlatFace"
    hs.addGeometry(Part.Circle(App.Vector(20, 10, 0), App.Vector(0, 0, 1), 1))
    doc.recompute()
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("feature.hole")
    harness.pump(800)
    Gui.Selection.addSelection(doc.Name, hs.Name)
    harness.pump(800)
    dialog().set_values({"chamfer": "start"})
    dialog()._emit()
    harness.pump(1500)
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(1500)
    hole = [o for o in body.Group if o.TypeId == "PartDesign::Hole"][-1]
    helper = [o for o in body.Group if getattr(o, "InCadHelperOf", None) == hole.Name][0]
    edge = [i + 1 for i, e in enumerate(helper.Shape.Edges) if isinstance(e.Curve, Part.Line) and e.Length > 25
            and abs(e.Vertexes[0].Point.z - 6) < 1e-6 and abs(e.Vertexes[1].Point.z - 6) < 1e-6][0]
    ch = body.newObject("PartDesign::Chamfer", "Chamfer")
    ch.Base = (helper, ["Edge%d" % edge])
    ch.Size = 0.5
    body.Tip = ch
    helper.Visibility = False
    ch.Visibility = True
    doc.recompute()
    assert ch.Shape.isValid() and shown(body) == [ch.Name], shown(body)
    ST["hole"], ST["chamfer"] = hole.Name, ch.Name


def test_1_counterbore_and_no_chamfer_keep_the_part_on_screen():
    doc = App.ActiveDocument
    body = document.primary_body(doc)
    shell.edit_feature(doc.getObject(ST["hole"]))
    harness.pump(1200)
    dialog().set_values({"hole_type": "tapped"})  # a preview first: it shows the hole by its chamfer step
    dialog()._emit()
    harness.pump(1500)
    dialog().set_values({"seat": "counterbore", "chamfer": "none"})
    dialog()._emit()
    harness.pump(2000)
    assert not [o for o in body.Group if getattr(o, "InCadHelperOf", None) == ST["hole"]], "the chamfer step stayed"
    assert shown(body), "nothing of the part on screen while the Hole window is open"
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(2000)
    ch = doc.getObject(ST["chamfer"])
    assert ch.Shape.isValid() and shown(body) == [ch.Name], shown(body)


def test_9_cleanup():
    for name in list(App.listDocuments()):
        App.closeDocument(name)
        harness.pump(300)


harness.run([test_0_plate_with_a_chamfered_hole_and_a_chamfer_after,
             test_1_counterbore_and_no_chamfer_keep_the_part_on_screen, test_9_cleanup])
