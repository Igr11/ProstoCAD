import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from pivy import coin  # noqa: E402,F401
from PySide6.QtTest import QTest  # noqa: E402
from incad.qt import QtCore  # noqa: E402
from incad import actions  # noqa: E402
from incad.core import assembly, commands, prefs, tree  # noqa: E402
from incad.ui import shell  # noqa: E402
from headless.test_assembly import save_part  # noqa: E402

ST = {}


def s():
    return shell.instance()


def dialog():
    fs = s().feature_session
    return fs.dialog if fs is not None else None


def asm_doc():
    return App.getDocument(ST["asm"])


def face(link, z):
    return [i + 1 for i, f in enumerate(link.LinkedObject.Shape.Faces) if abs(f.CenterOfMass.z - z) < 1e-6][0]


def test_0_setup():
    prefs.set_remember_new_choice(True)
    prefs.set_default_units("mm")
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    ST["dir"] = tempfile.mkdtemp(prefix="incad_gui_asm_")
    for name, w, h, t in (("Plate", 40, 30, 10), ("Block", 10, 10, 10)):
        d, ST[name] = save_part(ST["dir"], name, w, h, t)
        App.closeDocument(d.Name)
    doc = actions.new_assembly("mm")
    doc.saveAs(os.path.join(ST["dir"], "Asm.FCStd"))
    ST["asm"] = doc.Name
    harness.pump(800)
    assert s().ribbon.mode() == "assembly" if hasattr(s().ribbon, "mode") else True


def test_1_place_two_parts_first_grounded():
    a = s().assembly_place(ST["Plate"])
    b = s().assembly_place(ST["Block"])
    harness.pump(800)
    assert App.ActiveDocument is asm_doc(), App.ActiveDocument
    assert assembly.is_grounded(a) and not assembly.is_grounded(b)
    labels = [n.label for n in tree.build(asm_doc())[0].children]
    assert labels[:2] == ["Plate", "Block"], labels
    assert "Could not" not in s().status.texts()["message"], s().status.texts()  # closed part files are no error
    ST["a"], ST["b"] = a.Name, b.Name


def test_1b_padlock_is_small_and_calm():
    s().schedule_refresh()
    harness.pump(600)
    grounds = [j for j in assembly.joints(asm_doc()) if hasattr(j, "ObjectToGround")]
    assert grounds
    proxy = grounds[0].ViewObject.Proxy
    assert proxy.scale.scaleFactor.getValue() <= 2.0, proxy.scale.scaleFactor.getValue()
    r, g, b = proxy.lockpadColor.rgb[0].getValue()
    assert not (r > 0.7 and g < 0.3 and b < 0.3), (r, g, b)  # no longer loud red
    # the word Grounded on the grounded part itself (the user: two parts in, which one the padlock belongs to?)
    from pivy import coin
    sa = coin.SoSearchAction()
    sa.setName("InCadGroundMarks")
    sa.setInterest(coin.SoSearchAction.ALL)
    sa.apply(Gui.getDocument(asm_doc().Name).ActiveView.getSceneGraph())
    assert sa.getPaths().getLength() == 1, "one Grounded mark"
    node = sa.getPaths()[0].getTail()
    moves = [node.getChild(i) for i in range(node.getNumChildren()) if node.getChild(i).isOfType(
        coin.SoSeparator.getClassTypeId())]
    assert len(moves) == 1, "one grounded part"
    at = moves[0].getChild(0).translation.getValue().getValue()
    grounded = [c for c in assembly.components(asm_doc()) if assembly.is_grounded(c)][0]
    bb = grounded.Shape.BoundBox
    assert abs(at[0] - bb.Center.x) < 1e-3 and abs(at[1] - bb.Center.y) < 1e-3 and at[2] > bb.ZMax, (at, bb)
    # FreeCAD's padlock on the part too, on the middle of its top (the user: it stood by the free part)
    grounded.Placement = App.Placement(grounded.Placement.Base + App.Vector(15, 0, 0), grounded.Placement.Rotation)
    asm_doc().recompute()
    s().schedule_refresh()
    harness.pump(600)
    bb = grounded.Shape.BoundBox
    lock = proxy.transform.translation.getValue().getValue()
    assert abs(lock[0] - bb.Center.x) < 1e-3 and abs(lock[1] - bb.Center.y) < 1e-3 and abs(lock[2] - bb.ZMax) < 1e-3, \
        (lock, bb)
    grounded.Placement = App.Placement(grounded.Placement.Base - App.Vector(15, 0, 0), grounded.Placement.Rotation)
    asm_doc().recompute()
    harness.pump(300)


def test_2_mate_from_3d_picks_is_one_undo_step_and_undo_restores():
    doc = asm_doc()
    a, b = doc.getObject(ST["a"]), doc.getObject(ST["b"])
    before = App.Placement(b.Placement)
    undo = len(doc.UndoNames)
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("constrain.mate")
    harness.pump(800)
    fs = s().feature_session
    assert fs is not None and not fs.dialog.apply_button.isVisible(), "no Apply + in the assembly's windows (the user)"
    asm = assembly.assembly_of(doc)
    Gui.Selection.addSelection(doc.Name, asm.Name, "%s.Face%d" % (a.Name, face(a, 10)))
    harness.pump(600)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, asm.Name, "%s.Face%d" % (b.Name, face(b, 0)))
    harness.pump(1200)
    v = dialog().values()
    assert v["first"] == "%s:Face%d" % (a.Name, face(a, 10)) and v["second"].startswith(b.Name), v
    assert not dialog().error_text(), dialog().error_text()
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(1000)
    assert abs(b.Shape.BoundBox.ZMin - 10) < 1e-4, b.Shape.BoundBox
    assert doc.UndoNames[0] == "Mate" and len(doc.UndoNames) == undo + 1, doc.UndoNames
    commands.REGISTRY.run("edit.undo")
    harness.pump(1000)
    assert b.Placement.isSame(before, 1e-6), (b.Placement, before)


def test_2b_parallel_from_3d_picks_is_one_undo_step():
    doc = asm_doc()
    a, b = doc.getObject(ST["a"]), doc.getObject(ST["b"])
    b.Placement = App.Placement(App.Vector(50, 40, 25), App.Rotation(App.Vector(1, 1, 0.3), 134))
    doc.recompute()
    undo = len(doc.UndoNames)
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("constrain.parallel")
    harness.pump(800)
    asm = assembly.assembly_of(doc)
    Gui.Selection.addSelection(doc.Name, asm.Name, "%s.Face%d" % (a.Name, face(a, 10)))
    harness.pump(600)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, asm.Name, "%s.Face%d" % (b.Name, face(b, 0)))
    harness.pump(1200)
    v = dialog().values()
    assert v["first"] == "%s:Face%d" % (a.Name, face(a, 10)) and v["second"] == "%s:Face%d" % (b.Name, face(b, 0)), v
    assert not dialog().error_text(), dialog().error_text()
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(1000)
    n1 = a.Shape.getElement("Face%d" % face(a, 10)).normalAt(0, 0)
    n2 = b.Shape.getElement("Face%d" % face(b, 0)).normalAt(0, 0)
    assert abs(abs(n1.dot(n2)) - 1) < 1e-6, (n1, n2)
    assert doc.UndoNames[0] == "Parallel" and len(doc.UndoNames) == undo + 1, doc.UndoNames


def face_normal(link, n):
    f = link.Shape.getElement("Face%d" % n)
    return f.normalAt(*f.Surface.parameter(f.CenterOfMass))


def test_2c_mate_flip_previews_at_once_and_ok_is_one_undo_step():
    """The user: 'Yes, and Flip on Mate too' — ticking Flip in the Mate dialog turns the block over at once (the
    faces face the same way); OK keeps it as one 'Mate' step, Undo puts the block back."""
    from incad.features import constraints as C
    doc = asm_doc()
    a, b = doc.getObject(ST["a"]), doc.getObject(ST["b"])
    b.Placement = App.Placement(App.Vector(50, 40, 25), App.Rotation(10, 20, 30))
    doc.recompute()
    before = App.Placement(b.Placement)
    undo = len(doc.UndoNames)
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("constrain.mate")
    harness.pump(800)
    asm = assembly.assembly_of(doc)
    Gui.Selection.addSelection(doc.Name, asm.Name, "%s.Face%d" % (a.Name, face(a, 10)))
    harness.pump(600)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, asm.Name, "%s.Face%d" % (b.Name, face(b, 0)))
    harness.pump(1200)
    assert not dialog().error_text(), dialog().error_text()
    assert dialog().label_text("flip").startswith("Flip"), dialog().label_text("flip")
    assert face_normal(a, face(a, 10)).dot(face_normal(b, face(b, 0))) < -0.999999  # facing each other
    unflipped = App.Placement(b.Placement)
    dialog().field("flip").click()  # as a click on the box (a mouse click at the stretched row's centre misses it)
    harness.pump(1500)
    assert dialog().values()["flip"] is True, dialog().values()
    assert not dialog().error_text(), dialog().error_text()
    assert not b.Placement.isSame(unflipped, 1e-6), "the preview shows the block turned over"
    assert face_normal(a, face(a, 10)).dot(face_normal(b, face(b, 0))) > 0.999999  # the same way
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(1000)
    assert doc.UndoNames[0] == "Mate" and len(doc.UndoNames) == undo + 1, doc.UndoNames
    mates = [j for j in assembly.joints(doc) if C.kind_of(j) is C.MATE]
    assert mates and C.MATE.read(doc, mates[-1])["flip"] is True and mates[-1].Label.startswith("Mate"), mates
    assert face_normal(a, face(a, 10)).dot(face_normal(b, face(b, 0))) > 0.999999
    commands.REGISTRY.run("edit.undo")
    harness.pump(1000)
    assert b.Placement.isSame(before, 1e-6), (b.Placement, before)
    assert not [j for j in assembly.joints(doc) if C.kind_of(j) is C.MATE]


def test_3_cleanup():
    for name in list(App.listDocuments()):
        App.closeDocument(name)
    shutil.rmtree(ST["dir"], ignore_errors=True)


harness.run([test_0_setup, test_1_place_two_parts_first_grounded, test_1b_padlock_is_small_and_calm,
             test_2_mate_from_3d_picks_is_one_undo_step_and_undo_restores,
             test_2b_parallel_from_3d_picks_is_one_undo_step,
             test_2c_mate_flip_previews_at_once_and_ok_is_one_undo_step, test_3_cleanup])
