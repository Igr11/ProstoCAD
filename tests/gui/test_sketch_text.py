"""Sketch ▸ Text (user request): two clicks draw its box, the dialog takes the words, Extrude raises the
letters, editing the text changes the extrusion, and text runs around a circle picked first."""
import os
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
import Part  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from incad.qt import QtCore, QtWidgets  # noqa: E402
from incad.core import commands, document, prefs  # noqa: E402
from incad.features import sketch_text  # noqa: E402
from incad.ui import shell  # noqa: E402

ST = {}


def s():
    return shell.instance()


def fs():
    return s().feature_session


def _click_sketch(sk, x, y):
    from incad.qt import QtGui
    from incad.ui import dof_handles
    view = Gui.ActiveDocument.ActiveView
    vp = view.graphicsView().viewport()
    pos = QtCore.QPointF(*dof_handles.screen(view, vp, sk.getGlobalPlacement().multVec(App.Vector(x, y, 0))))
    L, N = QtCore.Qt.LeftButton, QtCore.Qt.NoButton
    for kind, button, buttons, ms in ((QtCore.QEvent.MouseMove, N, N, 250), (QtCore.QEvent.MouseButtonPress, L, L, 60),
                                      (QtCore.QEvent.MouseButtonRelease, L, N, 600)):
        QtWidgets.QApplication.sendEvent(vp, QtGui.QMouseEvent(kind, pos, QtCore.QPointF(vp.mapToGlobal(pos.toPoint())),
                                                                button, buttons, QtCore.Qt.NoModifier))
        harness.pump(ms)


def ok():
    QTest.mouseClick(fs().dialog.ok_button, QtCore.Qt.LeftButton)
    harness.pump(1500)


def test_0_sketch():
    prefs.set_remember_new_choice(True)
    prefs.set_default_units("mm")
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    ST["doc"] = App.ActiveDocument.Name
    commands.REGISTRY.run("sketch.start")
    harness.pump(600)
    s().plane_panel.button("XY_Plane").click()
    harness.pump(1200)
    sk = Gui.ActiveDocument.getInEdit().Object
    ST["sk"] = sk.Name
    sk.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(60, 30, 0)), True)  # room for the view to show
    App.ActiveDocument.recompute()
    Gui.SendMsgToActiveView("ViewFit")
    harness.pump(600)
    assert s().ribbon.button("sketch.text") is not None and s().ribbon.button("sketch.text").isVisible()


def test_1_two_clicks_draw_the_box_and_the_dialog_takes_the_words():
    sk = App.ActiveDocument.getObject(ST["sk"])
    Gui.Selection.clearSelection()
    r = commands.REGISTRY.run("sketch.text")
    harness.pump(400)
    assert r.ok, r.error
    _click_sketch(sk, 5, 10)    # the box's first corner
    assert fs() is None, "one corner: the dialog waits for the second"
    _click_sketch(sk, 45, 24)   # the opposite corner
    harness.pump(1500)
    assert fs() is not None and fs().kind is sketch_text.TEXT, "the Text dialog opens after the two corners"
    fs().dialog.set_values({"text": "InCad", "size": "6 mm"})
    harness.pump(1500)
    t = fs().feature
    assert t is not None and sketch_text.is_text(t) and "Invalid" not in t.State, (t and t.getStatusString(),
                                                                                  fs().dialog.error_text())
    assert fs().dialog.values()["box"], "the drawn box is the text's box"
    b = t.Shape.BoundBox
    assert abs((b.XMin + b.XMax) / 2 - 25) < 0.6 and abs((b.YMin + b.YMax) / 2 - 17) < 0.6, b
    ok()
    assert fs() is None
    ST["text"] = t.Name


def test_2_extrude_raises_the_letters():
    s().sketches.finish()
    harness.pump(1000)
    t = App.ActiveDocument.getObject(ST["text"])
    r = commands.REGISTRY.run("feature.extrude")
    harness.pump(1500)
    assert r.ok, r.error
    assert fs() is not None and fs().profile is t, ("the text is the profile", fs() and fs().profile)
    fs().dialog.set_values({"distance": "2 mm"})
    harness.pump(1500)
    ok()
    tip = document.primary_body(App.ActiveDocument).Tip
    assert tip.TypeId == "PartDesign::FeatureAdditivePython" and "Invalid" not in tip.State, tip.getStatusString()
    assert abs(tip.Shape.BoundBox.ZMax - 2.0) < 1e-6 and tip.Shape.Volume > 10, (tip.Shape.BoundBox, tip.Shape.Volume)
    assert not t.ViewObject.Visibility, "the used text is hidden"
    ST["pad"], ST["vol"] = tip.Name, tip.Shape.Volume


def test_3_editing_the_text_changes_the_extrusion():
    t = App.ActiveDocument.getObject(ST["text"])
    s().edit_feature(t)
    harness.pump(1500)
    assert fs() is not None and fs().kind is sketch_text.TEXT and s().sketches.editing() is not None
    assert fs().dialog.values()["text"] == "InCad", fs().dialog.values()
    fs().dialog.set_values({"text": "InCad 0.4"})
    harness.pump(1500)
    ok()
    s().sketches.finish()
    harness.pump(1200)
    pad = App.ActiveDocument.getObject(ST["pad"])
    assert "Invalid" not in pad.State and pad.Shape.Volume > ST["vol"] + 1, (pad.Shape.Volume, ST["vol"])


def _drag_sketch(sk, a, b, steps=8):
    """A real left-button drag in the open sketch from sketch point a to b."""
    from incad.qt import QtGui
    from incad.ui import dof_handles
    view = Gui.ActiveDocument.ActiveView
    vp = view.graphicsView().viewport()

    def at(p):
        return QtCore.QPointF(*dof_handles.screen(view, vp, sk.getGlobalPlacement().multVec(App.Vector(p[0], p[1], 0))))

    def send(kind, pos, button, buttons):
        QtWidgets.QApplication.sendEvent(vp, QtGui.QMouseEvent(kind, pos, QtCore.QPointF(vp.mapToGlobal(pos.toPoint())),
                                                                button, buttons, QtCore.Qt.NoModifier))
    L, N = QtCore.Qt.LeftButton, QtCore.Qt.NoButton
    send(QtCore.QEvent.MouseMove, at(a), N, N)
    harness.pump(300)
    send(QtCore.QEvent.MouseButtonPress, at(a), L, L)
    harness.pump(100)
    for i in range(1, steps + 1):
        t = i / float(steps)
        send(QtCore.QEvent.MouseMove, at((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)), N, L)
        harness.pump(60)
    send(QtCore.QEvent.MouseButtonRelease, at(b), L, N)
    harness.pump(800)


def test_3b_dragging_the_box_in_the_open_sketch_moves_the_text():
    """User: after the box (or the line it runs along) is moved, the text moves with it — at once, not after
    Finish Sketch."""
    sk = App.ActiveDocument.getObject(ST["sk"])
    t = App.ActiveDocument.getObject(ST["text"])
    s().sketches.edit(sk)
    harness.pump(1200)
    Gui.SendMsgToActiveView("ViewFit")
    harness.pump(500)
    before = t.Shape.BoundBox
    sides = [i for i in range(sk.GeometryCount) if sk.getConstruction(i) and sk.Geometry[i].TypeId ==
             "Part::GeomLineSegment" and abs(sk.Geometry[i].StartPoint.x - sk.Geometry[i].EndPoint.x) < 1e-6
             and abs(sk.Geometry[i].StartPoint.y - sk.Geometry[i].EndPoint.y) > 1]  # the box's vertical sides
    left = min(sides, key=lambda i: sk.Geometry[i].StartPoint.x)
    g = sk.Geometry[left]
    x0, ymid = g.StartPoint.x, (g.StartPoint.y + g.EndPoint.y) / 2
    right = max(sk.Geometry[i].StartPoint.x for i in sides)
    _drag_sketch(sk, (x0, ymid), (x0 - 10, ymid))
    moved = sk.Geometry[left].StartPoint.x
    assert moved < x0 - 5, ("the box's left side was dragged", x0, moved)
    after = t.Shape.BoundBox
    want = (moved + right) / 2.0
    assert abs((after.XMin + after.XMax) / 2 - want) < 0.6, ("the text sits in the moved box's middle at once",
                                                            before, after, moved)
    lines = s().sketches.__dict__.get("_text_lines")
    assert lines is not None, "its outline is drawn again"


def test_4_text_around_a_circle_picked_first():
    sk = App.ActiveDocument.getObject(ST["sk"])
    s().sketches.edit(sk)
    harness.pump(1200)
    c = sk.addGeometry(Part.Circle(App.Vector(30, 40, 0), App.Vector(0, 0, 1), 12), True)
    App.ActiveDocument.recompute()
    Gui.SendMsgToActiveView("ViewFit")
    harness.pump(500)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(App.ActiveDocument.Name, sk.Name, "Edge%d" % (c + 1))
    harness.pump(300)
    r = commands.REGISTRY.run("sketch.text")
    harness.pump(1500)
    assert r.ok, r.error
    assert fs() is not None and fs().dialog.values()["mode"] == "curve", fs() and fs().dialog.values()
    fs().dialog.set_values({"text": "AROUND", "size": "3 mm"})
    harness.pump(1500)
    t = fs().feature
    assert t is not None and "Invalid" not in t.State, (t and t.getStatusString(), fs().dialog.error_text())
    assert t.Shape.BoundBox.YMin > 40 + 8 and abs((t.Shape.BoundBox.XMin + t.Shape.BoundBox.XMax) / 2 - 30) < 1, ("centred on the circle's top, outside", t.Shape.BoundBox)
    ok()


def test_5_cleanup():
    s().sketches.finish()
    harness.pump(800)
    App.closeDocument(ST["doc"])
    harness.pump(400)


harness.run([test_0_sketch, test_1_two_clicks_draw_the_box_and_the_dialog_takes_the_words,
             test_2_extrude_raises_the_letters, test_3_editing_the_text_changes_the_extrusion,
             test_3b_dragging_the_box_in_the_open_sketch_moves_the_text,
             test_4_text_around_a_circle_picked_first, test_5_cleanup])
