"""The user's real part in a real FreeCAD window: a threaded shaft near one end of a block, with a pocket, a hole
and a groove elsewhere. A click on the thread picks the shaft with its thread; 'Equal spacing' from the block's
near end to the far one spaces three shafts; Flip puts the copies on the other side; From at the far end says so.
The part is a frozen copy of the user's file as it was on 2026-10-09 15:20 (tests/gui/data), so the test does
not drift with the user's later work on the live file; INCAD_REAL_PART points it at another file."""
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from pivy import coin  # noqa: E402,F401
from PySide6.QtTest import QTest  # noqa: E402
from incad.qt import QtCore  # noqa: E402
from incad.core import commands, document, prefs  # noqa: E402
from incad.features import own_pattern  # noqa: E402
from incad.ui import shell  # noqa: E402

PART = os.environ.get("INCAD_REAL_PART", os.path.join(os.environ["INCAD_REPO"], "tests", "gui", "data",
                                                        "p2_test_reordered.FCStd"))
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
    harness.pump(900)


def cancel():
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(700)


def tip():
    return body().Tip


def end_face(shape_obj, x):
    """The block's end face at x: a plane whose normal is X."""
    return "Face%d" % ([i + 1 for i, f in enumerate(shape_obj.Shape.Faces) if f.Surface.TypeId == "Part::GeomPlane"
                        and abs(f.CenterOfMass.x - x) < 0.05 and abs(f.Surface.Axis.x) > 0.99][0])


def thread_face(shape_obj):
    """A flank of the thread cut into the shaft (the shaft hangs below y = -7.2): neither a plane nor a cylinder."""
    return "Face%d" % ([i + 1 for i, f in enumerate(shape_obj.Shape.Faces)
                        if f.Surface.TypeId not in ("Part::GeomPlane", "Part::GeomCylinder")
                        and f.CenterOfMass.y < -8][0])


def pick(key, obj, sub):
    if dialog().active_pick() != key:
        dialog().field(key).click()
        harness.pump(500)
    assert dialog().active_pick() == key, dialog().active_pick()
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, obj.Name, sub)
    harness.pump(900)


def test_0_open_a_copy_of_the_real_part():
    assert os.path.exists(PART), "real part missing: " + PART
    prefs.set_remember_new_choice(True)
    prefs.set_default_units("mm")
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    copy = os.path.join(os.environ["FREECAD_USER_TEMP"], "real_part.FCStd")
    shutil.copyfile(PART, copy)
    App.openDocument(copy)  # Gui.open answers "File type 'fcstd' not supported" when run as a script
    harness.pump(2500)
    Gui.SendMsgToActiveView("ViewFit")
    harness.pump(500)
    assert doc() is not None and doc().FileName == copy, getattr(doc(), "FileName", None)
    shaft, thread = doc().getObject("Extrude002"), doc().getObject("Thread")
    assert shaft is not None and thread is not None and tip().Name == "Chamfer", tip().Name
    b = tip().Shape.BoundBox
    assert abs(b.XMin + 12.26) < 0.05 and abs(b.XMax - 17.74) < 0.05, b
    ST["base_volume"] = tip().Shape.Volume
    ST["base"] = tip().Name


def test_1_clicking_the_thread_spaces_three_shafts_equally_from_the_near_end():
    base = doc().getObject(ST["base"])
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("feature.rect_pattern")
    harness.pump(1000)
    assert dialog() is not None
    pick("features", base, thread_face(base))
    vals = dialog().values()
    assert vals["features"] == "Extrude002", vals["features"]
    assert vals["children"] is True, "the thread comes along with the shaft"
    dialog().set_values({"fit1": "faces", "count1": "3"})
    harness.pump(600)
    pick("from1", base, end_face(base, 17.74))  # the end the shaft is near: the start
    pick("to1", base, end_face(base, -12.26))
    assert not dialog().error_text(), dialog().error_text()
    shown = dialog().field("features").text()
    assert "dependent" in shown, shown
    ok()
    p = tip()
    assert own_pattern.is_own(p) and own_pattern.kind_of(p) == "rectangular", p.TypeId  # our own pattern
    assert "Invalid" not in p.State, p.getStatusString()
    assert [o.Name for o in p.Originals] == ["Extrude002", "Thread", "ThreadChamfer"], [o.Name for o in p.Originals]
    assert abs(p.Length.Value - 14.0) < 0.05 and p.Reversed, (p.Length.Value, p.Reversed)  # 7 mm steps, leftward
    b = p.Shape.BoundBox
    assert abs(b.XMin + 12.26) < 0.05 and abs(b.XMax - 17.74) < 0.05, b  # the copies stay on the block
    assert p.Shape.Volume > ST["base_volume"] + 100, (p.Shape.Volume, ST["base_volume"])  # two more shafts
    ST["pattern"] = p.Name


def test_2_flip_puts_the_copies_on_the_other_side_of_the_shaft():
    p = doc().getObject(ST["pattern"])
    shell.edit_feature(p)
    harness.pump(1000)
    assert dialog() is not None and dialog().values()["fit1"] == "faces", dialog().values()
    dialog().field("flip1").setChecked(True)
    harness.pump(1500)
    assert not dialog().error_text(), dialog().error_text()
    ok()
    assert "Invalid" not in p.State, p.getStatusString()
    assert not p.Reversed and abs(p.Length.Value - 14.0) < 0.05, (p.Reversed, p.Length.Value)
    assert p.Shape.BoundBox.XMax > 25, p.Shape.BoundBox  # the copies hang past the right end, in the air
    shell.edit_feature(p)
    harness.pump(1000)
    dialog().field("flip1").setChecked(False)
    harness.pump(1500)
    ok()
    assert p.Reversed and abs(p.Shape.BoundBox.XMax - 17.74) < 0.05, p.Shape.BoundBox


def test_3_from_the_far_end_says_to_pick_the_other_end():
    base = doc().getObject(ST["base"])
    p = doc().getObject(ST["pattern"])
    doc().removeObject(p.Name)
    body().Tip = base
    doc().recompute()
    harness.pump(500)
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("feature.rect_pattern")
    harness.pump(1000)
    pick("features", base, thread_face(base))
    dialog().set_values({"fit1": "faces", "count1": "3"})
    harness.pump(600)
    pick("from1", base, end_face(base, -12.26))  # the far end first
    pick("to1", base, end_face(base, 17.74))
    err = dialog().error_text()
    assert "pick the other end as From" in err, err
    cancel()
    assert tip() is base, tip().Name


def top_face(shape_obj):
    return "Face%d" % ([i + 1 for i, f in enumerate(shape_obj.Shape.Faces) if f.Surface.TypeId == "Part::GeomPlane"
                        and abs(f.CenterOfMass.z - 10) < 0.05 and f.Area > 100][0])


def test_4_a_hole_sketched_on_the_top_is_patterned_four_times_in_the_sketch():
    import Part
    import Sketcher
    from incad.core import sketch_patterns as sp
    base = doc().getObject(ST["base"])
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, base.Name, top_face(base))
    commands.REGISTRY.run("sketch.start")
    harness.pump(1500)
    sk = Gui.ActiveDocument.getInEdit().Object
    hole = sk.addGeometry(Part.Circle(App.Vector(-1, 1, 0), App.Vector(0, 0, 1), 1.0))  # a row clear of the
    sk.addConstraint(Sketcher.Constraint("Radius", hole, 1.0))                            # part's other features
    doc().recompute()
    harness.pump(300)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, sk.Name, "Edge%d" % (hole + 1))
    commands.REGISTRY.run("sketch.rect_pattern")
    harness.pump(1200)
    assert dialog() is not None and dialog().values()["geometry"].endswith("Edge%d" % (hole + 1)), dialog().values()
    dialog().set_values({"count1": "4", "spacing1": "3.5 mm"})
    harness.pump(1200)
    assert not dialog().error_text(), dialog().error_text()
    ok()
    assert len(sp.records(sk)) == 1 and len(sp.records(sk)[0]["copies"]) == 3
    ST["sketch"], ST["hole"] = sk.Name, hole


def test_5_edit_to_five_then_cut_gives_five_holes_in_the_solid():
    import math
    from incad.core import sketch_patterns as sp
    sk = doc().getObject(ST["sketch"])
    name = sp.records(sk)[0]["name"]
    shell.instance().edit_sketch_pattern(sk, name)
    harness.pump(1200)
    w = dialog().field("count1")
    w.setText("5")
    w.editingFinished.emit()
    harness.pump(1200)
    assert not dialog().error_text(), dialog().error_text()
    ok()
    assert len(sp.find(sk, name)["copies"]) == 4
    commands.REGISTRY.run("sketch.finish")
    harness.pump(1000)
    before = tip().Shape.Volume
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, sk.Name)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(1500)
    dialog().set_values({"operation": "cut", "extent": "through_all"})  # incad/features/extrude.py FIELDS
    harness.pump(1500)
    assert not dialog().error_text(), dialog().error_text()
    ok()
    cut = tip()
    assert cut.TypeId == "PartDesign::FeatureSubtractivePython", cut.TypeId
    assert "Invalid" not in cut.State, cut.getStatusString()
    removed = before - cut.Shape.Volume
    assert abs(removed - 5 * math.pi * 1.0 ** 2 * 10) < 5.0, removed  # five Ø2 holes through 10 mm


harness.run([test_0_open_a_copy_of_the_real_part,
             test_1_clicking_the_thread_spaces_three_shafts_equally_from_the_near_end,
             test_2_flip_puts_the_copies_on_the_other_side_of_the_shaft,
             test_3_from_the_far_end_says_to_pick_the_other_end,
             test_4_a_hole_sketched_on_the_top_is_patterned_four_times_in_the_sketch,
             test_5_edit_to_five_then_cut_gives_five_holes_in_the_solid])
