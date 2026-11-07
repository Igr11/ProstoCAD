"""Constraints window in a real FreeCAD window: ribbon, filter by pick, highlight, delete (window, right-click,
Delete key with the window open), closes with the sketch."""
import os
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from incad.core import commands, prefs  # noqa: E402
from incad.core import sketch_constraints as sc  # noqa: E402
from incad.ui import shell  # noqa: E402
from headless.test_sketch_constraints import rect  # noqa: E402

ST = {}


def sk():
    return Gui.ActiveDocument.getInEdit().Object


def panel():
    return shell.instance().constraints_panel


def test_0_sketch_with_a_rectangle():
    prefs.set_remember_new_choice(True)
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    commands.REGISTRY.run("sketch.start")
    harness.pump(600)
    shell.instance().plane_panel.button("XY_Plane").click()
    harness.pump(1200)
    rect(sk())
    App.ActiveDocument.recompute()
    harness.pump(1000)
    # a new rectangle gets its construction centre point, Symmetric between two corners (the user: one-constraint
    # symmetry): 10 constraints of the rectangle + that one
    assert sk().GeometryCount == 5 and sk().Constraints[-1].Type == "Symmetric", [c.Type for c in sk().Constraints]


def test_1_ribbon_opens_the_window_with_every_constraint():
    b = shell.instance().ribbon.button("sketch.constraints_window")
    assert b is not None and b.isVisible()
    b.click()
    harness.pump(500)
    assert panel() is not None and panel().isVisible()
    assert panel().title_text() == "Constraints — %s" % sk().Label, panel().title_text()
    assert len(panel().row_texts()) == len(sk().Constraints) == 11, (panel().row_texts(), [c.Type for c in sk().Constraints], sk().GeometryCount)


def test_2_picking_a_line_filters_and_a_row_highlights():
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(App.ActiveDocument.Name, sk().Name, "Edge1")
    harness.pump(500)
    assert panel().header_text().startswith("Picked: Line 1"), panel().header_text()
    assert [t for t, _, _ in panel().row_texts()] == ["Coincident", "Coincident", "Horizontal", "Horizontal Distance",
                                                    "Symmetric"], panel().row_texts()
    panel().select_index(4)
    harness.pump(300)
    subs = Gui.Selection.getSelectionEx(App.ActiveDocument.Name)[0].SubElementNames
    assert "Constraint5" in subs and "Edge1" in subs, subs


def test_3_delete_from_the_window_is_one_undo_step():
    doc = App.ActiveDocument
    before = len(doc.UndoNames)
    panel().set_checked(4, True)
    panel().delete_button.click()
    harness.pump(600)
    assert len(sk().Constraints) == 10, [c.Type for c in sk().Constraints]
    assert doc.UndoNames[0] == "Delete Constraints" and len(doc.UndoNames) == before + 1, doc.UndoNames
    assert ("Horizontal", "Line 1", "") not in panel().row_texts()


def test_4_a_refused_delete_says_why_and_keeps_everything():
    sk().setExpression("Constraints.d2", "%s.Constraints.d1 / 2" % sk().Name)
    App.ActiveDocument.recompute()
    harness.pump(300)
    Gui.Selection.clearSelection()
    harness.pump(400)
    d1 = next(r.index for r in sc.rows(sk()) if r.name == "d1")
    panel().select_index(d1)
    panel().delete_button.click()
    harness.pump(400)
    assert "d1 is used by d2" in panel().message_text(), panel().message_text()
    assert any(r.name == "d1" for r in sc.rows(sk()))


def test_5_right_click_on_a_line_lists_its_constraints():
    s = shell.instance()
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(App.ActiveDocument.Name, sk().Name, "Edge2")
    harness.pump(300)
    menu = s.open_sketch_menu_for_test()
    labels = menu.list_labels()
    # one entry for the line's constraints (the user: a Delete row per constraint made the menu too long)
    counted = [lb for lb in labels if lb.startswith("Constraints (")]
    assert counted, labels
    assert not [lb for lb in labels if lb.startswith("Delete ") and lb != "Delete"], labels
    assert "Delete" in labels and "Construction" in labels, labels
    for gone in ("Look At", "Project Geometry", "Constraint List", "Edit Pattern", "Delete Pattern", "Stop Tool"):
        assert gone not in labels, (gone, labels)  # elsewhere, or not for this line / without a tool
    menu.extra_button(counted[0]).click()
    harness.pump(600)
    assert panel() is not None and panel().header_text().startswith("Picked: Line"), panel().header_text()
    Gui.Selection.clearSelection()
    harness.pump(300)
    empty = s.open_sketch_menu_for_test()
    labels = empty.list_labels()
    assert not [lb for lb in labels if lb.startswith("Constraints (")], labels
    assert "Constraint List" in labels and "Delete" not in labels, labels  # on empty space: the whole list
    empty.close()


def test_6_delete_key_with_the_window_open_refreshes_the_list():
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(App.ActiveDocument.Name, sk().Name, "Constraint1")
    commands.REGISTRY.run("edit.delete")
    harness.pump(600)
    Gui.Selection.clearSelection()
    harness.pump(400)
    assert len(panel().row_texts()) == len(sk().Constraints), (panel().row_texts(), len(sk().Constraints))


def _click_checkbox(index):
    from PySide6.QtTest import QTest
    from incad.qt import QtCore
    t = panel().table
    item = panel()._item(index)
    rect = t.visualItemRect(item)
    QTest.mouseClick(t.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
                     QtCore.QPoint(rect.left() + 12, rect.center().y()))
    harness.pump(400)


def test_6b_two_checkboxes_clicked_then_deleted():
    """Review: clicking a checkbox highlights the row in the sketch, the refresh wiped every check."""
    commands.REGISTRY.run("sketch.constraints_window")
    Gui.Selection.clearSelection()
    harness.pump(400)
    rows = [r for r in sc.rows(sk()) if not r.owner and not r.name]
    a, b = rows[0], rows[1]
    _click_checkbox(a.index)
    _click_checkbox(b.index)
    assert panel().checked() == sorted([a.index, b.index]), panel().checked()
    n = len(sk().Constraints)
    panel().delete_button.click()
    harness.pump(600)
    assert len(sk().Constraints) == n - 2, (len(sk().Constraints), n)
    assert panel().checked() == [] and panel().selected() is None, (panel().checked(), panel().selected())


def test_6c_constraints_entry_opens_filtered_to_the_element_under_the_cursor():
    s = shell.instance()
    s.close_constraints_window()
    Gui.Selection.clearSelection()
    harness.pump(300)
    edge = next("Edge%d" % (g + 1) for r in sc.rows(sk()) for g, _ in r.ties if g >= 0)
    Gui.Selection.setPreselection(sk(), edge)
    harness.pump(200)
    menu = s.open_sketch_menu_for_test()
    menu.extra_button(next(lb for lb in menu.list_labels() if lb.startswith("Constraints ("))).click()
    harness.pump(600)
    assert panel() is not None and panel().header_text().startswith("Picked: Line"), panel().header_text()


def test_6d_highlighting_a_point_constraint_selects_the_point():
    import Part
    import Sketcher
    g = sk().addGeometry(Part.Point(App.Vector(5, 5, 0)))
    i = sk().addConstraint(Sketcher.Constraint("PointOnObject", g, 1, -1))
    App.ActiveDocument.recompute()
    harness.pump(400)
    Gui.Selection.clearSelection()
    harness.pump(300)
    panel().select_index(i)
    harness.pump(300)
    subs = Gui.Selection.getSelectionEx(App.ActiveDocument.Name)[0].SubElementNames
    assert "Constraint%d" % (i + 1) in subs and any(n.startswith("Vertex") for n in subs), subs


def test_7_finish_sketch_closes_the_window():
    commands.REGISTRY.run("sketch.finish")
    harness.pump(900)
    assert shell.instance().constraints_panel is None


harness.run([test_0_sketch_with_a_rectangle, test_1_ribbon_opens_the_window_with_every_constraint,
             test_2_picking_a_line_filters_and_a_row_highlights, test_3_delete_from_the_window_is_one_undo_step,
             test_4_a_refused_delete_says_why_and_keeps_everything, test_5_right_click_on_a_line_lists_its_constraints,
             test_6_delete_key_with_the_window_open_refreshes_the_list, test_6b_two_checkboxes_clicked_then_deleted,
             test_6c_constraints_entry_opens_filtered_to_the_element_under_the_cursor,
             test_6d_highlighting_a_point_constraint_selects_the_point, test_7_finish_sketch_closes_the_window])
