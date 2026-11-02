"""Shell on a simple part through InCad's dialog: pick the top face, OK, a hollow box; and a shell that the
kernel refuses (a modeled thread earlier in the tree) tells the user the way around."""
import os
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from incad.qt import QtCore  # noqa: E402
from incad.core import commands, prefs  # noqa: E402
from incad.ui import shell  # noqa: E402
from incad.features import dressup  # noqa: E402
import headless.util as util  # noqa: E402


def _body():
    from incad.core import document
    return document.primary_body(App.ActiveDocument)


def test_0_part():
    prefs.set_remember_new_choice(True)
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    body = _body()
    sk = util.add_rect_sketch(body)
    pad = util.add_pad(body, sk, 10)
    body.Tip = pad
    App.ActiveDocument.recompute()
    from incad.core import document
    document.show_result(body)
    shell.instance().schedule_refresh()
    harness.pump(600)


def test_1_shell_the_box_open_at_the_top():
    sh = shell.instance()
    pad = App.ActiveDocument.getObject("Pad")
    top = [i + 1 for i, f in enumerate(pad.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6][0]
    full = pad.Shape.Volume
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(App.ActiveDocument.Name, pad.Name, "Face%d" % top)  # preselected face
    commands.REGISTRY.run("feature.shell")
    harness.pump(900)
    fs = sh.feature_session
    assert fs is not None, "the Shell dialog is open"
    assert fs.dialog.values().get("faces") == "Pad:Face%d" % top, fs.dialog.values()
    fs.dialog.set_values({"thickness": "2 mm"})
    harness.pump(900)
    QTest.mouseClick(fs.dialog.ok_button, QtCore.Qt.LeftButton)
    harness.pump(900)
    assert sh.feature_session is None
    tip = _body().Tip
    assert dressup.SHELL.claims(tip) and "InCadShell" in tip.PropertiesList, tip.TypeId  # InCad's own Shell
    assert "Invalid" not in tip.State, tip.getStatusString()
    expect = full - 16 * 6 * 8  # 20x10x10 box, 2 mm walls, open top: inner 16 x 6 x 8
    assert abs(tip.Shape.Volume - expect) < 1.0, (tip.Shape.Volume, expect)


def test_1b_the_dialog_shows_the_shell_while_it_is_open():
    """The user: no preview for Shell. With a face removed the view shows the hollow part, not the box."""
    sh = shell.instance()
    commands.REGISTRY.run("edit.undo")  # back to the box
    harness.pump(900)
    pad = App.ActiveDocument.getObject("Pad")
    top = [i + 1 for i, f in enumerate(pad.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6][0]
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(App.ActiveDocument.Name, pad.Name, "Face%d" % top)
    commands.REGISTRY.run("feature.shell")
    harness.pump(1200)
    fs = sh.feature_session
    assert fs is not None and fs.feature is not None, "the Shell is previewed"
    shown = {o.Name: bool(o.ViewObject.Visibility) for o in (pad, fs.feature)}
    assert shown[fs.feature.Name] and not shown["Pad"], (shown, fs.dialog.active_pick(), fs.feature.State)
    QTest.mouseClick(fs.dialog.cancel_button, QtCore.Qt.LeftButton)
    harness.pump(900)


def test_1c_a_face_clicked_in_the_dialog_shows_the_shell():
    """Shell with nothing selected, then a click on the top face: the hollow part shows (the face pick stays on
    for more faces)."""
    sh = shell.instance()
    pad = App.ActiveDocument.getObject("Pad")
    top = [i + 1 for i, f in enumerate(pad.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6][0]
    Gui.Selection.clearSelection()
    harness.pump(300)
    commands.REGISTRY.run("feature.shell")
    harness.pump(1000)
    fs = sh.feature_session
    assert fs is not None and fs.dialog.active_pick() == "faces", fs and fs.dialog.active_pick()
    Gui.Selection.addSelection(App.ActiveDocument.Name, pad.Name, "Face%d" % top)  # the click on the face
    harness.pump(1500)
    assert fs.feature is not None and "Invalid" not in fs.feature.State, fs.dialog.error_text()
    assert pad.ViewObject.Visibility and fs._ghost is not None, "the part, the shell drawn over it as outlines"
    front = [i + 1 for i, f in enumerate(fs.feature.Shape.Faces)
             if abs(f.CenterOfMass.y) < 1e-6 and abs(f.Area - 200) < 1e-6][0]  # the outer front wall
    pad_front = [i + 1 for i, f in enumerate(pad.Shape.Faces) if abs(f.CenterOfMass.y) < 1e-6][0]
    Gui.Selection.addSelection(App.ActiveDocument.Name, pad.Name, "Face%d" % pad_front)  # a click on the part
    harness.pump(1500)
    picked = fs.dialog.values()["faces"]
    assert picked == "Pad:Face%d, Pad:Face%d" % (top, pad_front) or set(picked.replace(" ", "").split(",")) == {
        "Pad:Face%d" % top, "Pad:Face%d" % pad_front}, (picked, fs.dialog.error_text())
    assert pad.ViewObject.Visibility and fs._ghost is not None, "still the part with the outlines"
    QTest.mouseClick(fs.dialog.cancel_button, QtCore.Qt.LeftButton)
    harness.pump(900)


def test_2_the_real_part_offers_a_shell_before_the_thread():
    """The user's part: the kernel refuses the shell; the dialog explains and offers 'Shell before Thread';
    the button makes it with the thickest wall that works and says so."""
    from incad.core import document
    sh = shell.instance()
    path = os.path.join(os.environ["INCAD_REPO"], "tests", "gui", "data", "p2_test_reordered.FCStd")
    App.openDocument(path)
    harness.pump(2000)
    sh.schedule_refresh()
    harness.pump(800)
    body = document.primary_body(App.ActiveDocument)
    tip = body.Tip
    top = max((i for i, f in enumerate(tip.Shape.Faces) if abs(f.normalAt(0, 0).z - 1) < 1e-6),
              key=lambda i: tip.Shape.Faces[i].Area) + 1
    n0 = App.ActiveDocument.UndoCount  # before the dialog: its own pending step would count
    commands.REGISTRY.run("feature.shell")
    harness.pump(800)
    fs = sh.feature_session
    assert fs is not None
    fs.dialog.set_pick("faces", "%s:Face%d" % (tip.Name, top))
    fs.dialog.set_values({"thickness": "1.5 mm"})
    harness.pump(9000)
    assert "End of Part above the thread" in fs.dialog.error_text(), fs.dialog.error_text()
    assert fs.dialog.rescue_button.isVisible(), "the way around is offered"
    # the first modeled thread in the part: the Thread (the Hole before it is suppressed, and a suppressed tapped
    # hole keeps its thread off since 0.5.2)
    assert fs.dialog.rescue_button.text() == "Shell before Thread", fs.dialog.rescue_button.text()
    QTest.mouseClick(fs.dialog.rescue_button, QtCore.Qt.LeftButton)
    harness.pump(500)  # the rescue ran inside the click (three thread recomputes); the message is fresh
    assert sh.feature_session is None, "the dialog is gone"
    assert not [o for o in body.Group if o.TypeId == "PartDesign::Thickness"], "no wall works on this part"
    assert App.ActiveDocument.UndoCount == n0, (n0, App.ActiveDocument.UndoCount)
    assert body.Tip is tip and tip.Shape.isValid()
    msg = sh.status.texts()["message"]
    assert "and so do walls of 0.75 mm and 0.375 mm" in msg, msg


def test_3_shell_a_box_drawn_away_from_the_part():
    """The user's case: a second box extruded (Join) away from the part lands in its own Solid; Shell started
    from the ribbon with the box's top face picked in the dialog hollows the box, not the part."""
    from incad.core import document
    from incad.features import extrude
    sh = shell.instance()
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
    other = box.getParentGeoFeatureGroup()
    assert other is not body and len(document.bodies(App.ActiveDocument)) == 2
    for b in document.bodies(App.ActiveDocument):
        document.show_result(b)
    sh.schedule_refresh()
    harness.pump(600)
    top = [i + 1 for i, f in enumerate(box.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6][0]
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("feature.shell")
    harness.pump(800)
    fs = sh.feature_session
    assert fs is not None
    fs.dialog.set_pick("faces", "%s:Face%d" % (box.Name, top))
    harness.pump(1200)
    assert fs.dialog.error_text() == "", fs.dialog.error_text()
    QTest.mouseClick(fs.dialog.ok_button, QtCore.Qt.LeftButton)
    harness.pump(900)
    assert sh.feature_session is None
    shells = [o for o in App.ActiveDocument.Objects if "InCadShell" in o.PropertiesList]
    assert len(shells) == 1 and shells[0].getParentGeoFeatureGroup() is other, shells
    assert other.Tip is shells[0] and "Invalid" not in shells[0].State
    assert abs(shells[0].Shape.Volume - (2000 - 18 * 8 * 9)) < 2.0, shells[0].Shape.Volume
    assert body.Tip is pad and abs(pad.Shape.Volume - 2000) < 1e-6, "the part is untouched"


def test_4_shell_a_box_left_in_the_parts_body_by_an_older_file():
    """The user's file: the box was extruded into the part's body before InCad split such pieces off. Picking
    its face in the Shell dialog moves it to its own Solid and hollows it."""
    from incad.core import document
    sh = shell.instance()
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    body = _body()
    pad = util.add_pad(body, util.add_rect_sketch(body), 10)
    far = util.add_rect_sketch(body, name="Far")
    far.AttachmentOffset = App.Placement(App.Vector(50, 50, 0), App.Rotation())
    box = util.add_pad(body, far, 10, name="OldBox")  # raw, as an older InCad did
    body.Tip = box
    App.ActiveDocument.recompute()
    document.show_result(body)
    sh.schedule_refresh()
    harness.pump(600)
    assert len(body.Tip.Shape.Solids) == 2
    top = [i + 1 for i, f in enumerate(box.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6 and f.CenterOfMass.x > 40][0]
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("feature.shell")
    harness.pump(800)
    fs = sh.feature_session
    fs.dialog.set_pick("faces", "%s:Face%d" % (box.Name, top))
    harness.pump(1500)
    assert fs.dialog.error_text() == "", fs.dialog.error_text()
    QTest.mouseClick(fs.dialog.ok_button, QtCore.Qt.LeftButton)
    harness.pump(900)
    assert sh.feature_session is None
    other = box.getParentGeoFeatureGroup()
    assert other is not body and len(document.bodies(App.ActiveDocument)) == 2
    shells = [o for o in App.ActiveDocument.Objects if "InCadShell" in o.PropertiesList]
    assert len(shells) == 1 and shells[0].getParentGeoFeatureGroup() is other and "Invalid" not in shells[0].State
    assert abs(shells[0].Shape.Volume - (2000 - 18 * 8 * 9)) < 2.0, shells[0].Shape.Volume
    assert body.Tip is pad and len(pad.Shape.Solids) == 1
    commands.REGISTRY.run("edit.undo")
    harness.pump(900)
    assert box.getParentGeoFeatureGroup() is body and body.Tip is box, "one undo step puts the box back"


harness.run([test_0_part, test_1_shell_the_box_open_at_the_top, test_1b_the_dialog_shows_the_shell_while_it_is_open,
             test_1c_a_face_clicked_in_the_dialog_shows_the_shell, test_2_the_real_part_offers_a_shell_before_the_thread,
             test_3_shell_a_box_drawn_away_from_the_part, test_4_shell_a_box_left_in_the_parts_body_by_an_older_file])
