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
from incad.qt import QtCore  # noqa: E402
from incad.core import commands, document, model, prefs, safe  # noqa: E402
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


def type_into(key, text):
    w = dialog().field(key)
    w.setText(text)
    w.editingFinished.emit()
    harness.pump(500)


def test_0_setup():
    prefs.set_remember_new_choice(True)
    prefs.set_default_units("mm")
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    ST["sk"] = util.add_rect_sketch(body())
    doc().recompute()
    harness.pump(300)


def test_1_extrude_picks_the_only_profile_and_previews():
    commands.REGISTRY.run("feature.extrude")
    harness.pump(800)
    assert dialog() is not None and dialog().isVisible()
    f = s().feature_session.feature
    assert f is not None and f.TypeId == "PartDesign::FeatureAdditivePython" and abs(f.Shape.Volume - 2000) < 1e-6
    assert safe.busy() == "Extrude"


def test_2_ok_commits_one_undo_step():
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(600)
    assert s().feature_session is None and safe.busy() is None
    assert "Extrude" in doc().UndoNames[:1], doc().UndoNames
    pad = body().Tip
    ST["pad"] = pad.Name
    assert model.bound_parameter(pad, "Length") is not None


def test_3_edit_feature_changes_the_distance():
    pad = doc().getObject(ST["pad"])
    shell.edit_feature(pad)
    harness.pump(600)
    type_into("distance", "25 mm")
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(600)
    assert abs(doc().getObject(ST["pad"]).Length.Value - 25) < 1e-6


def test_4_cancel_restores_after_previews():
    names = list(doc().UndoNames)
    shell.edit_feature(doc().getObject(ST["pad"]))
    harness.pump(600)
    type_into("distance", "50 mm")
    dialog().field("operation").button("cut").click()
    harness.pump(600)
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(600)
    pad = doc().getObject(ST["pad"])
    assert pad is not None and pad.TypeId == "PartDesign::FeatureAdditivePython" and abs(pad.Length.Value - 25) < 1e-6
    assert list(doc().UndoNames) == names, (doc().UndoNames, names)


def test_5_invalid_value_disables_ok():
    shell.edit_feature(doc().getObject(ST["pad"]))
    harness.pump(600)
    type_into("distance", "Nope * 2")
    assert not dialog().ok_button.isEnabled() and dialog().error_text()
    type_into("distance", "0")
    assert not dialog().ok_button.isEnabled()
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(600)


def test_6_cut_through_all_on_top():
    pad = doc().getObject(ST["pad"])
    top = [i + 1 for i, fc in enumerate(pad.Shape.Faces) if abs(fc.CenterOfMass.z - 25) < 1e-6][0]
    sk = body().newObject("Sketcher::SketchObject", "Cutter")
    sk.AttachmentSupport = [(pad, "Face%d" % top)]
    sk.MapMode = "FlatFace"
    sk.addGeometry(Part.Circle(App.Vector(10, 5, 0), App.Vector(0, 0, 1), 2))
    doc().recompute()
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, sk.Name)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(800)
    dialog().field("operation").button("cut").click()
    dialog().field("extent").button("through_all").click()
    harness.pump(600)
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(600)
    tip = body().Tip
    assert tip.TypeId == "PartDesign::FeatureSubtractivePython", tip.TypeId
    assert body().Shape.Volume < 20 * 10 * 25 - 1


def test_7_new_solid_and_cancel_moves_sketch_back():
    sk = util.add_rect_sketch(body(), w=5, h=5, name="Other")
    doc().recompute()
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, sk.Name)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(800)
    dialog().field("operation").button("new_solid").click()
    harness.pump(600)
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(600)
    assert len(document.bodies(doc())) == 1
    assert doc().getObject(sk.Name).getParentGeoFeatureGroup() is body()


def test_8_revolve():
    sk = body().newObject("Sketcher::SketchObject", "RevProfile")
    sk.AttachmentSupport = [(util.xy_plane(body()), "")]
    sk.MapMode = "FlatFace"
    pts = [(30, 0), (34, 0), (34, 4), (30, 4)]
    for i in range(4):
        a, b = pts[i], pts[(i + 1) % 4]
        sk.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)))
    doc().recompute()
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, sk.Name)
    commands.REGISTRY.run("feature.revolve")
    harness.pump(800)
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(600)
    assert body().Tip.TypeId == "PartDesign::Revolution"


def test_9_parameter_click_inserts_into_the_focused_field():
    document.set_parameter(doc(), "Width", "30 mm")
    shell.edit_feature(doc().getObject(ST["pad"]))
    harness.pump(600)
    f = dialog().field("distance")
    f.setFocus()
    f.selectAll()
    harness.pump(100)
    s().browser.parameterClicked.emit("Width")
    harness.pump(100)
    assert f.text() == "Width", f.text()
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(600)


def test_10_busy_blocks_other_changes_and_is_cleared():
    shell.edit_feature(doc().getObject(ST["pad"]))
    harness.pump(600)
    s().browser.parameterSet.emit("Gap", "1 mm")
    harness.pump(400)
    assert "Gap" not in [p[0] for p in document.list_parameters(doc())]
    assert "dialog" in s().status.texts()["message"].lower(), s().status.texts()
    QTest.keyClick(dialog().field("distance"), QtCore.Qt.Key_Escape)
    harness.pump(600)
    assert safe.busy() is None and s().feature_session is None


def test_11_leaving_incad_closes_the_dialog():
    shell.edit_feature(doc().getObject(ST["pad"]))
    harness.pump(600)
    Gui.activateWorkbench("PartDesignWorkbench")
    harness.pump(1000)
    assert safe.busy() is None
    assert abs(doc().getObject(ST["pad"]).Length.Value - 25) < 1e-6


def test_12_user_undo_keeps_features_following_their_parameters():
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    sk = util.add_rect_sketch(body(), w=4, h=4, name="UndoSk")
    doc().recompute()
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, sk.Name)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(800)
    dialog().field("operation").button("join").click()
    harness.pump(600)
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(600)
    ST["undo_pad"] = body().Tip.Name
    shell.edit_feature(body().Tip)
    harness.pump(600)
    dialog().field("operation").button("cut").click()
    harness.pump(600)
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(600)
    before = list(doc().UndoNames)[:4]
    commands.REGISTRY.run("edit.undo")  # brings the Pad back (FreeCAD drops its formula link; InCad repairs it)
    harness.pump(800)
    pad = doc().getObject(ST["undo_pad"])
    assert pad is not None and pad.TypeId == "PartDesign::FeatureAdditivePython", (pad, before, list(doc().UndoNames)[:4])
    name = model.bound_parameter(pad, "Length")
    s().browser.parameterSet.emit(name, "13 mm")
    harness.pump(600)
    assert abs(doc().getObject(ST["undo_pad"]).Length.Value - 13) < 1e-6, doc().getObject(ST["undo_pad"]).Length


def edit_pad():
    shell.edit_feature(doc().getObject(ST["pad"]))
    harness.pump(600)


def test_13_enter_commits_the_typed_value():
    edit_pad()
    f = dialog().field("distance")
    f.setFocus()
    f.selectAll()
    QTest.keyClicks(f, "30")
    QTest.keyClick(f, QtCore.Qt.Key_Return)
    harness.pump(600)
    assert s().feature_session is None
    assert abs(doc().getObject(ST["pad"]).Length.Value - 30) < 1e-6, doc().getObject(ST["pad"]).Length


def test_14_clicking_ok_commits_the_typed_value():
    edit_pad()
    f = dialog().field("distance")
    f.setFocus()
    f.selectAll()
    QTest.keyClicks(f, "31")
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(600)
    assert s().feature_session is None
    assert abs(doc().getObject(ST["pad"]).Length.Value - 31) < 1e-6, doc().getObject(ST["pad"]).Length


def test_15_undo_while_a_dialog_is_open_steps_its_own_change_back():
    """Undo in a dialog never refuses: it takes back the change typed in the dialog (the model's undo waits
    for OK or Cancel)."""
    names = list(doc().UndoNames)
    edit_pad()
    type_into("distance", "40 mm")
    harness.pump(600)
    assert abs(doc().getObject(ST["pad"]).Length.Value - 40) < 1e-6, doc().getObject(ST["pad"]).Length
    commands.REGISTRY.run("edit.undo")
    harness.pump(600)
    assert s().feature_session is not None, "the dialog stays open"
    assert abs(doc().getObject(ST["pad"]).Length.Value - 31) < 1e-6, doc().getObject(ST["pad"]).Length
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(600)
    assert abs(doc().getObject(ST["pad"]).Length.Value - 31) < 1e-6, doc().getObject(ST["pad"]).Length
    assert list(doc().UndoNames) == names, (doc().UndoNames, names)


def test_16_apply_plus_does_not_reuse_the_extruded_sketch():
    sk = util.add_rect_sketch(body(), w=3, h=3, name="ApplySk")
    doc().recompute()
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, sk.Name)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(800)
    QTest.mouseClick(dialog().apply_button, QtCore.Qt.LeftButton)
    harness.pump(900)
    fs = s().feature_session
    assert fs is None or fs.profile is not sk, "Apply + extruded ApplySk again"
    if fs is not None:
        fs.cancel()
    s()._cancel_profile_pick()
    harness.pump(400)


def test_17_failed_recreate_recovers():
    sk = util.add_rect_sketch(body(), w=2, h=2, name="RecreateSk")
    doc().recompute()
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, sk.Name)
    commands.REGISTRY.run("feature.extrude")
    harness.pump(800)
    type_into("distance", "Nope * 2")
    dialog().field("operation").button("new_solid").click()
    harness.pump(600)
    dialog().field("operation").button("join").click()
    harness.pump(600)
    type_into("distance", "5 mm")
    assert not dialog().error_text(), dialog().error_text()
    f = s().feature_session.feature
    assert f is not None and f.Document is doc() and abs(f.Length.Value - 5) < 1e-6
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(600)


def test_18_closing_the_document_ends_the_dialog():
    edit_pad()
    App.closeDocument(doc().Name)
    harness.pump(1000)
    assert s().feature_session is None and safe.busy() is None, (s().feature_session, safe.busy())



def test_19_rmb_edit_in_the_model_tree_opens_sketch_and_feature():
    from incad.core import context
    pad = doc().getObject(ST["pad"])
    before = (s().feature_session and s().feature_session.feature and s().feature_session.feature.Name,
              [(x.ObjectName, x.SubElementNames) for x in Gui.Selection.getSelectionEx()],
              getattr(s(), "_profile_pick", None) is not None)
    context.set_target(pad.Name)
    commands.REGISTRY.run("browser.edit")
    harness.pump(800)
    fs = s().feature_session
    assert dialog() is not None and fs.feature is pad, ("the feature's dialog opens", fs and fs.kind.TITLE,
                                                         fs and fs.feature and fs.feature.Name, pad.Name,
                                                         before, open(os.environ["INCAD_LOG"]).read()[-600:])
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(600)
    sk = pad.Profile[0]
    context.set_target(sk.Name)
    commands.REGISTRY.run("browser.edit")
    harness.pump(1000)
    gdoc = Gui.ActiveDocument
    assert gdoc.getInEdit() is not None and gdoc.getInEdit().Object is sk, "the sketch opens for editing"
    commands.REGISTRY.run("sketch.finish")
    harness.pump(800)


harness.run([test_0_setup, test_1_extrude_picks_the_only_profile_and_previews, test_2_ok_commits_one_undo_step,
             test_3_edit_feature_changes_the_distance, test_4_cancel_restores_after_previews,
             test_5_invalid_value_disables_ok, test_6_cut_through_all_on_top,
             test_7_new_solid_and_cancel_moves_sketch_back, test_8_revolve,
             test_9_parameter_click_inserts_into_the_focused_field, test_10_busy_blocks_other_changes_and_is_cleared,
             test_11_leaving_incad_closes_the_dialog,
             test_12_user_undo_keeps_features_following_their_parameters, test_13_enter_commits_the_typed_value,
             test_14_clicking_ok_commits_the_typed_value, test_15_undo_while_a_dialog_is_open_steps_its_own_change_back,
             test_16_apply_plus_does_not_reuse_the_extruded_sketch, test_17_failed_recreate_recovers,
             test_19_rmb_edit_in_the_model_tree_opens_sketch_and_feature,
             test_18_closing_the_document_ends_the_dialog])
