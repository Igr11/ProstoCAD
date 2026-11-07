"""Editing a sketch from the model tree and its dimension box, through real clicks and keys:
RMB → Edit; the box takes the keyboard at once; Enter applies, Esc cancels; a clicked dimension is inserted."""
import os
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
import Sketcher  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from incad.qt import QtCore, QtWidgets  # noqa: E402
from incad.core import commands, document, prefs, sketch as sketches  # noqa: E402
from incad.ui import shell  # noqa: E402
import headless.util as util  # noqa: E402

ST = {}


def s():
    return shell.instance()


def doc():
    return App.ActiveDocument


def editing():
    v = Gui.ActiveDocument.getInEdit()
    return v.Object if v is not None else None


def rect(name, x0):
    body = document.primary_body(doc())
    sk = util.add_rect_sketch(body, name=name)
    sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 20))
    sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 10))
    doc().recompute()
    sketches.name_new_dimensions(doc())
    harness.pump(300)
    return sk


def rmb_edit(name):
    """A real right-click on the model-tree row, then a real click on Edit in the menu."""
    from PySide6 import QtGui
    tree = s().browser.tree
    item = [i for i in s().browser._items() if i.data(0, QtCore.Qt.UserRole) == name][0]
    tree.scrollToItem(item)
    pos = tree.visualItemRect(item).center()
    QTest.mouseClick(tree.viewport(), QtCore.Qt.RightButton, QtCore.Qt.NoModifier, pos)
    ev = QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Mouse, pos, tree.viewport().mapToGlobal(pos))
    QtWidgets.QApplication.sendEvent(tree.viewport(), ev)  # what macOS sends after a real right-click
    harness.pump(500)
    menu = s().marking_menu
    if menu is None or not menu.isVisible():  # the simulated right-click is flaky: once more
        QTest.mouseClick(tree.viewport(), QtCore.Qt.RightButton, QtCore.Qt.NoModifier, pos)
        QtWidgets.QApplication.sendEvent(tree.viewport(), QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Mouse, pos, tree.viewport().mapToGlobal(pos)))
        harness.pump(500)
        menu = s().marking_menu
    assert menu is not None and menu.isVisible(), "the RMB menu opens"
    edit = [b for b in menu._list if b.text() == "Edit"][0]
    QTest.mouseClick(edit, QtCore.Qt.LeftButton)
    harness.pump(1200)


def box():
    return s().value_box


def focused():
    return QtWidgets.QApplication.focusWidget()


def keyboard():
    """The widget to type into: the box's field. With the screen locked nothing has focus and QtTest would abort
    the whole file on a None widget — a clean, environmental FAIL instead."""
    assert box() is not None and focused() is box().edit, ("the keyboard must be in the box", focused(),
                                                            "(screen locked?)")
    return box().edit


def test_0_setup():
    prefs.set_remember_new_choice(True)
    # a dimension the sketch fixes already asks (a modal question): answered here, and none expected in this file
    shell.REFERENCE_ASK = lambda text: ST.setdefault("asked", []).append(text) or True
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    a = rect("SketchA", 0)
    b = rect("SketchB", 30)
    ST["a"], ST["b"] = a.Name, b.Name
    ST["a_dims"] = [c.Name for c in a.Constraints if c.Name]
    harness.pump(300)


def test_1_rmb_edit_opens_the_sketch():
    rmb_edit(ST["a"])
    assert editing() is not None and editing().Name == ST["a"], editing()


def test_2_rmb_edit_another_sketch_while_one_is_open():
    rmb_edit(ST["b"])
    assert editing() is not None and editing().Name == ST["b"], editing()
    rmb_edit(ST["a"])
    assert editing() is not None and editing().Name == ST["a"], editing()


def test_3_the_dimension_box_has_the_keyboard_and_enter_applies():
    sk = doc().getObject(ST["a"])
    d0, d1 = ST["a_dims"][:2]
    s().open_value_box(sk, d0)
    harness.pump(500)
    QTest.keyClicks(keyboard(), "15")
    QTest.keyClick(keyboard(), QtCore.Qt.Key_Return)
    harness.pump(600)
    assert box() is None, "Enter closes the box"
    assert abs(sk.getDatum(d0).Value - 15) < 1e-6, sk.getDatum(d0)


def test_4_esc_cancels():
    sk = doc().getObject(ST["a"])
    d0 = ST["a_dims"][0]
    s().open_value_box(sk, d0)
    harness.pump(500)
    QTest.keyClicks(keyboard(), "99")
    QTest.keyClick(keyboard(), QtCore.Qt.Key_Escape)
    harness.pump(500)
    assert box() is None, "Esc closes the box"
    assert abs(sk.getDatum(d0).Value - 15) < 1e-6, "nothing changed"
    assert editing() is not None, "and the sketch stays open"


def test_5_clicking_another_dimension_inserts_its_name():
    sk = doc().getObject(ST["a"])
    d0, d1 = ST["a_dims"][:2]
    s().open_value_box(sk, d1)
    harness.pump(500)
    QTest.keyClick(keyboard(), QtCore.Qt.Key_Backspace)
    index = [i for i, c in enumerate(sk.Constraints) if c.Name == d0][0]
    real = Gui.Selection.getPreselection

    class Pre:  # the d0 dimension under the cursor
        Object = sk
        SubElementNames = ("Constraint%d" % (index + 1),)
    Gui.Selection.getPreselection = lambda: Pre()
    try:
        found = s()._preselected_dimension()
        assert s()._click_hook(None, None, 0, 0), (found, box(), s().feature_session, d0, index)
    finally:
        Gui.Selection.getPreselection = real
    harness.pump(300)
    QTest.keyClicks(keyboard(), "+2")  # the keyboard stays in the box
    QTest.keyClick(keyboard(), QtCore.Qt.Key_Return)
    harness.pump(600)
    assert box() is None, (box().edit.text(), box().error_text())
    assert abs(sk.getDatum(d1).Value - 17) < 1e-6, sk.getDatum(d1)



def test_6_a_dimension_replaced_while_placing_keeps_its_name_and_the_keyboard():
    """FreeCAD 1.1's Dimension tool makes the dimension at the first click and replaces it when it is placed:
    the box must keep its name (no dN lost) and get the keyboard back from the 3D view."""
    sk = doc().getObject(ST["a"])
    sk.addConstraint(Sketcher.Constraint("DistanceY", 3, 2, 3, 1, 10))  # the first click's dimension
    doc().recompute()
    harness.pump(600)
    name = box().name if box() is not None else None
    assert name, "a new dimension opens its box"
    index = [i for i, c in enumerate(sk.Constraints) if c.Name == name][0]
    sk.delConstraint(index)  # the placing click: replaced by a new one
    sk.addConstraint(Sketcher.Constraint("DistanceY", 3, 2, 3, 1, 10))
    doc().recompute()
    s().browser.tree.setFocus()  # the click put the keyboard elsewhere
    harness.pump(1200)
    assert box() is not None and box().name == name
    assert name in [c.Name for c in sk.Constraints], [c.Name for c in sk.Constraints]
    QTest.keyClicks(keyboard(), "12")
    QTest.keyClick(keyboard(), QtCore.Qt.Key_Return)
    harness.pump(600)
    assert abs(sk.getDatum(name).Value - 12) < 1e-6
    commands.REGISTRY.run("sketch.finish")
    harness.pump(800)



def label_screen_pos(sk, name):
    """Viewport (logical px, top-left origin) position of a dimension's label as FreeCAD draws it."""
    view = Gui.ActiveDocument.ActiveView
    vs = [w for w in Gui.getMainWindow().findChildren(QtWidgets.QGraphicsView) if w.isVisible()]
    vp = vs[0].viewport()
    pt = sketches.label_point(sk, name)  # global 3D point of the label's text
    x, y = view.getPointOnViewport(pt)
    r = vp.devicePixelRatioF()
    h = view.getSize()[1]
    return vp, x / r, (h - y) / r


def test_7_clicking_a_dimension_in_the_view_replaces_the_value_with_its_name():
    """Inventor: box open on d1 (9.54), click d0's label (30) -> 'd0', type '/2' -> Enter -> d1 = 15.
    Also while the Dimension tool is still running (it is, right after placing a dimension)."""
    sk = doc().getObject(ST["a"])
    d0, d1 = ST["a_dims"][:2]
    sketches.set_dimension(doc(), d0, "30 mm")
    sketches.set_dimension(doc(), d1, "9.54129 mm")
    rmb_edit(ST["a"])
    assert editing() is not None and editing().Name == ST["a"]
    Gui.SendMsgToActiveView("ViewFit")
    harness.pump(500)
    commands.REGISTRY.run("sketch.dimension")  # the tool runs, as after placing a dimension
    harness.pump(500)
    s().open_value_box(sk, d1)
    harness.pump(500)
    assert focused() is box().edit
    vp, x, y = label_screen_pos(sk, d0)
    assert s()._click_hook(Gui.ActiveDocument.ActiveView, vp, x, y), "the click on d0's label is taken"
    harness.pump(300)
    assert box() is not None and box().edit.text() == d0, box() and box().edit.text()  # replaced the selection
    QTest.keyClicks(keyboard(), "/2")
    QTest.keyClick(keyboard(), QtCore.Qt.Key_Return)
    harness.pump(800)
    assert box() is None
    assert abs(sk.getDatum(d1).Value - 15) < 1e-6, sk.getDatum(d1)
    QTest.keyClick(vp, QtCore.Qt.Key_Escape)  # end the tool
    harness.pump(300)
    commands.REGISTRY.run("sketch.finish")
    harness.pump(800)



def test_8_enter_before_placing_keeps_name_and_value():
    """Dimension → click line (box) → type 25, Enter → place: the placed dimension is still d? with 25 mm."""
    sk = doc().getObject(ST["a"])
    rmb_edit(ST["a"])
    before = {c.Name for c in sk.Constraints if c.Name}
    sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 1, 10))  # the first click's dimension (corner from the origin)
    doc().recompute()
    harness.pump(600)
    name = box().name
    QTest.keyClicks(keyboard(), "25")
    QTest.keyClick(keyboard(), QtCore.Qt.Key_Return)
    harness.pump(500)
    assert box() is None and abs(sk.getDatum(name).Value - 25) < 1e-6, (
        box() and (box().name, box().edit.text(), box().error_text()), sk.getDatum(name), focused(),
        open(os.environ["INCAD_LOG"]).read()[-400:])
    index = [i for i, c in enumerate(sk.Constraints) if c.Name == name][0]
    sk.delConstraint(index)  # the placing click: replaced by a new one with the measured value
    sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 1, 10))
    doc().recompute()
    harness.pump(1000)
    names = {c.Name for c in sk.Constraints if c.Name}
    assert names == before | {name}, (names, before, name)  # no number burned
    assert abs(sk.getDatum(name).Value - 25) < 1e-6, sk.getDatum(name)  # the typed value survived
    assert box() is None, "no box reopens for a dimension that already has its value"
    commands.REGISTRY.run("sketch.finish")
    harness.pump(800)


def test_9_box_closes_when_its_dimension_is_cancelled():
    sk = doc().getObject(ST["a"])
    rmb_edit(ST["a"])
    # a dimension the sketch leaves free (line 3's height is 12 since test 6: a second one would fight it and ask)
    sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 1, 5))
    doc().recompute()
    harness.pump(600)
    assert not ST.get("asked"), ST.get("asked")
    name = box().name
    index = [i for i, c in enumerate(sk.Constraints) if c.Name == name][0]
    sk.delConstraint(index)  # Esc in the view cancelled the tool: the dimension is gone, nothing replaces it
    doc().recompute()
    harness.pump(800)
    assert box() is None, "the box of a vanished dimension closes"
    commands.REGISTRY.run("sketch.finish")
    harness.pump(800)


def test_10_esc_never_leaves_the_sketch():
    rmb_edit(ST["a"])
    vp = [w for w in Gui.getMainWindow().findChildren(QtWidgets.QGraphicsView) if w.isVisible()][0].viewport()
    vp.setFocus()
    for _ in range(3):
        QTest.keyClick(vp, QtCore.Qt.Key_Escape)
        harness.pump(400)
    assert editing() is not None, "Esc cancels tools, Finish Sketch leaves"
    sk = doc().getObject(ST["a"])
    s().open_value_box(sk, ST["a_dims"][0])
    harness.pump(400)
    QTest.keyClick(box().edit, QtCore.Qt.Key_Escape)
    harness.pump(400)
    assert box() is None and editing() is not None
    commands.REGISTRY.run("sketch.finish")
    harness.pump(800)


def test_11_moving_on_applies_what_was_typed():
    sk = doc().getObject(ST["a"])
    d0 = ST["a_dims"][0]
    rmb_edit(ST["a"])
    s().open_value_box(sk, d0)
    harness.pump(400)
    QTest.keyClicks(box().edit, "33")
    typed = (box().edit.text(), box()._typed, focused() is box().edit)
    commands.REGISTRY.run("sketch.finish")  # moved on without Enter
    harness.pump(1000)
    assert editing() is None and box() is None, (editing(), box() and (box().edit.text(), box().error_text()),
                                                 open(os.environ["INCAD_LOG"]).read()[-400:])
    log_tail = open(os.environ["INCAD_LOG"]).read()[-500:] if os.path.exists(os.environ["INCAD_LOG"]) else ""
    assert abs(sk.getDatum(d0).Value - 33) < 1e-6, (sk.getDatum(d0), typed, log_tail)


harness.run([test_0_setup, test_1_rmb_edit_opens_the_sketch, test_2_rmb_edit_another_sketch_while_one_is_open,
             test_3_the_dimension_box_has_the_keyboard_and_enter_applies, test_4_esc_cancels,
             test_5_clicking_another_dimension_inserts_its_name,
             test_6_a_dimension_replaced_while_placing_keeps_its_name_and_the_keyboard,
             test_7_clicking_a_dimension_in_the_view_replaces_the_value_with_its_name,
             test_8_enter_before_placing_keeps_name_and_value, test_9_box_closes_when_its_dimension_is_cancelled,
             test_10_esc_never_leaves_the_sketch, test_11_moving_on_applies_what_was_typed])
