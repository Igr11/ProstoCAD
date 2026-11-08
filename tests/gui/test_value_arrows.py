"""The ± arrows of a dialog's value fields in the real app: every press previews at once (the Pad's length
follows), Shift steps by ten, Undo inside the dialog walks the presses back, a formula greys the arrows."""
import os
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from incad.qt import QtCore  # noqa: E402
from incad.core import commands, document, prefs  # noqa: E402
from incad.ui import shell  # noqa: E402
import headless.util as util  # noqa: E402


def dialog():
    fs = shell.instance().feature_session
    return fs.dialog if fs is not None else None


def feature():
    return shell.instance().feature_session.feature


def test_0_setup_and_open_extrude():
    prefs.set_remember_new_choice(True)
    prefs.set_default_units("mm")
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    util.add_rect_sketch(document.primary_body(App.ActiveDocument))
    App.ActiveDocument.recompute()
    commands.REGISTRY.run("feature.extrude")
    harness.pump(900)
    assert dialog() is not None and dialog().field("distance").text() == "10 mm", dialog().field("distance").text()
    assert abs(feature().Length.Value - 10) < 1e-6


def test_1_each_press_previews_at_once():
    up, down = dialog().step_buttons("distance")
    assert up.isVisible() and up.isEnabled()
    QTest.mouseClick(up, QtCore.Qt.LeftButton)
    harness.pump(400)
    assert dialog().field("distance").text() == "11 mm"
    assert abs(feature().Length.Value - 11) < 1e-6, feature().Length.Value
    assert abs(feature().Shape.Volume - 20 * 10 * 11) < 1e-3
    QTest.mouseClick(up, QtCore.Qt.LeftButton, QtCore.Qt.ShiftModifier)
    harness.pump(400)
    assert dialog().field("distance").text() == "21 mm"
    assert abs(feature().Length.Value - 21) < 1e-6
    QTest.mouseClick(down, QtCore.Qt.LeftButton)
    harness.pump(400)
    assert dialog().field("distance").text() == "20 mm" and abs(feature().Length.Value - 20) < 1e-6


def test_1b_a_held_arrow_previews_once_let_go():
    """Held, the arrow steps the number only; the preview comes once it is let go (the user 2026-11-08: a slow
    preview on every repeat held the window up, the release came late and the number ran on by itself)."""
    up, _down = dialog().step_buttons("distance")
    w = dialog().field("distance")
    start = float(w.text().split()[0])
    length = feature().Length.Value
    import time
    up.big = False  # (no Shift: the Shift-click before left it set)
    up.setDown(True)  # held: Qt's own auto-repeat clicks it now, and so do these
    for _ in range(3):
        up._last_step = time.monotonic()  # (repeats come quickly: no stuck button)
        dialog()._arrow(up, "distance", 1)
    harness.pump(900)
    up.setDown(False)
    held = float(w.text().split()[0])
    assert held >= start + 3, w.text()
    assert abs(feature().Length.Value - length) < 1e-6, "no preview while held"
    up.released.emit()
    harness.pump(600)
    assert abs(feature().Length.Value - held) < 1e-6, (feature().Length.Value, held)
    w.setText("%g mm" % (start + 1))  # back to where test_2's undo expects the presses (21 mm, then 20 mm)
    harness.pump(900)
    QTest.mouseClick(_down, QtCore.Qt.LeftButton)
    harness.pump(400)
    assert w.text() == "%g mm" % start and abs(feature().Length.Value - start) < 1e-6, w.text()


def test_2_undo_in_the_dialog_walks_the_presses_back():
    commands.REGISTRY.run("edit.undo")
    harness.pump(500)
    assert dialog() is not None, "undo inside the dialog steps its values, it does not close it"
    assert dialog().field("distance").text() == "21 mm" and abs(feature().Length.Value - 21) < 1e-6


def test_3_a_formula_greys_the_arrows_and_ok_keeps_the_value():
    up, down = dialog().step_buttons("distance")
    w = dialog().field("distance")
    w.setText("d0 * 2")
    harness.pump(900)
    assert not up.isEnabled() and not down.isEnabled()
    w.setText("15 mm")
    harness.pump(900)
    assert up.isEnabled()
    w.setFocus()
    QTest.keyClick(w, QtCore.Qt.Key_Up)
    harness.pump(400)
    assert w.text() == "16 mm" and abs(feature().Length.Value - 16) < 1e-6
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(800)
    assert dialog() is None
    pad = document.primary_body(App.ActiveDocument).Tip
    assert abs(pad.Length.Value - 16) < 1e-6, pad.Length.Value


harness.run([test_0_setup_and_open_extrude, test_1_each_press_previews_at_once, test_1b_a_held_arrow_previews_once_let_go,
             test_2_undo_in_the_dialog_walks_the_presses_back, test_3_a_formula_greys_the_arrows_and_ok_keeps_the_value])
