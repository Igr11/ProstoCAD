"""The user's own workflow end to end, through the same commands the ribbon, the model tree and its RMB menu use:
block → slot cut → point sketch → edit a sketch → hole from the tree → delete a sketch → move End of Part."""
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
from incad.core import commands, context, document, prefs, safe  # noqa: E402
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
    harness.pump(800)
    assert s().feature_session is None and safe.busy() is None, "dialog closed and nothing left busy"


def rmb(name, command):
    """RMB on a model-tree row, then a menu item."""
    context.set_target(name)
    commands.REGISTRY.run(command)
    harness.pump(800)


def top_face(feature):
    zmax = feature.Shape.BoundBox.ZMax
    return [i + 1 for i, fc in enumerate(feature.Shape.Faces) if abs(fc.CenterOfMass.z - zmax) < 1e-6][0]


def sketch_on_top(name, geos):
    tip = body().Tip
    sk = body().newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(tip, "Face%d" % top_face(tip))]
    sk.MapMode = "FlatFace"
    for g in geos:
        sk.addGeometry(g)
    doc().recompute()
    harness.pump(300)
    return sk


def test_0_block_and_slot_cut():
    prefs.set_remember_new_choice(True)
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    util.add_rect_sketch(body())
    doc().recompute()
    commands.REGISTRY.run("feature.extrude")
    harness.pump(800)
    ok()
    ST["block"] = body().Tip.Name
    ST["v_block"] = body().Shape.Volume
    slot = sketch_on_top("Slot", [])
    from incad.core import slots
    slots.add(slot, App.Vector(4, 3, 0), App.Vector(12, 3, 0), 1.5)
    doc().recompute()
    s().browser.select_row(slot.Name)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(800)
    dialog().field("operation").button("cut").click()
    harness.pump(600)
    ok()
    assert body().Tip.TypeId == "PartDesign::FeatureSubtractivePython" and body().Shape.Volume < ST["v_block"] - 1


def test_1_edit_a_sketch_from_the_tree_menu():
    sk = doc().getObject(ST["block"]).Profile[0]
    rmb(sk.Name, "browser.edit")
    gd = Gui.ActiveDocument
    assert gd.getInEdit() is not None and gd.getInEdit().Object is sk, "the sketch opens for editing"
    commands.REGISTRY.run("sketch.finish")
    harness.pump(800)
    assert gd.getInEdit() is None


def test_2_edit_a_feature_from_the_tree_menu():
    rmb(ST["block"], "browser.edit")
    assert dialog() is not None and s().feature_session.feature is doc().getObject(ST["block"])
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(600)
    assert safe.busy() is None


def test_3_hole_from_a_point_sketch_picked_in_the_tree():
    pts = sketch_on_top("HolePoints", [Part.Point(App.Vector(15, 6, 0))])
    v = body().Shape.Volume
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("feature.hole")
    harness.pump(800)
    assert dialog() is not None and dialog().active_pick() == "sketch"
    s().browser.select_row(pts.Name)
    harness.pump(800)
    assert dialog().values().get("sketch") == pts.Name, dialog().values().get("sketch")
    ok()
    assert body().Tip.TypeId == "PartDesign::Hole" and body().Shape.Volume < v - 1, body().Tip.TypeId


def test_4_delete_a_sketch_from_the_tree_menu():
    spare = sketch_on_top("Spare", [Part.LineSegment(App.Vector(0, 0, 0), App.Vector(3, 0, 0))])
    name = spare.Name
    rmb(name, "browser.delete")
    assert doc().getObject(name) is None, "the sketch is deleted"


def test_5_move_end_of_part_up_and_back():
    tip = body().Tip
    rmb(ST["block"], "browser.move_end_of_part")
    assert body().Tip.Name == ST["block"], body().Tip.Name
    assert abs(body().Shape.Volume - ST["v_block"]) < 1e-6, "only the block is computed"
    s().browser.endOfPartMoved.emit(tip.Name)  # dragged back down in the tree
    harness.pump(800)
    assert body().Tip.Name == tip.Name, body().Tip.Name


def test_6_update_button_recomputes_and_refreshes():
    """Inventor's Update next to Redo: a change made without a recompute is computed and the tree follows."""
    block = doc().getObject(ST["block"])
    v0 = body().Shape.Volume
    block.setExpression("Length", None)  # its parameter would win on recompute: a plain value for the test
    block.Length = block.Length.Value + 5  # no recompute: the shape is stale
    assert "Touched" in block.State, block.State  # stale before Update
    s().title.button("edit.update").click()
    harness.pump(1200)
    assert "Touched" not in block.State and "Invalid" not in block.State, block.State  # computed by Update
    assert body().Shape.Volume > v0 + 1, (v0, body().Shape.Volume)
    names = [s().browser.tree.topLevelItem(0).child(i).text(0)
             for i in range(s().browser.tree.topLevelItem(0).childCount())]
    assert "End of Part" in names, names


harness.run([test_0_block_and_slot_cut, test_1_edit_a_sketch_from_the_tree_menu,
             test_2_edit_a_feature_from_the_tree_menu, test_3_hole_from_a_point_sketch_picked_in_the_tree,
             test_4_delete_a_sketch_from_the_tree_menu, test_5_move_end_of_part_up_and_back,
             test_6_update_button_recomputes_and_refreshes])
