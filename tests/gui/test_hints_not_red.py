"""Red is for errors only (the user): a hint in a feature window — a click on the wrong thing, OK with nothing made
yet — is shown as a hint (the theme's accent colour), an error that stops the feature stays red."""
import os
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
import Part  # noqa: E402
from incad.core import commands, document, prefs  # noqa: E402
from incad.ui import shell  # noqa: E402
import headless.util as util  # noqa: E402

ST = {}


def dialog():
    fs = shell.instance().feature_session
    return fs.dialog if fs is not None else None


def test_0_part_and_hole_window():
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
    ST["plate"] = plate.Name
    doc.recompute()
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("feature.hole")
    harness.pump(1000)
    assert dialog() is not None, "no Hole window"


def test_1_ok_with_nothing_is_a_hint():
    dialog().ok_button.setEnabled(True)
    shell.instance().feature_session.ok()
    harness.pump(500)
    text = dialog().error_text()
    assert text, "nothing said"
    assert dialog().error_waiting(), ("shown as an error (red)", text)


def test_2_a_wrong_click_is_a_hint():
    doc = App.ActiveDocument
    plate = doc.getObject(ST["plate"])
    dialog().set_values({"placement": "concentric"})
    dialog()._emit()
    harness.pump(800)
    dialog().set_active_pick("edge")
    harness.pump(300)
    line = [i + 1 for i, e in enumerate(plate.Shape.Edges) if isinstance(e.Curve, Part.Line)][0]
    Gui.Selection.addSelection(doc.Name, plate.Name, "Edge%d" % line)
    harness.pump(800)
    text = dialog().error_text()
    assert text.startswith("Select "), text
    assert dialog().error_waiting(), ("shown as an error (red)", text)


def test_3_an_error_stays_red():
    dialog().set_error("The hole does not cut the part")
    harness.pump(200)
    assert not dialog().error_waiting()
    shell.instance().feature_session.cancel()
    harness.pump(800)


def test_9_cleanup():
    for name in list(App.listDocuments()):
        App.closeDocument(name)
        harness.pump(300)


harness.run([test_0_part_and_hole_window, test_1_ok_with_nothing_is_a_hint, test_2_a_wrong_click_is_a_hint,
             test_3_an_error_stays_red, test_9_cleanup])
