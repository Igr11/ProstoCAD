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
from incad.qt import QtCore, QtWidgets  # noqa: E402
from incad.core import commands, document, prefs  # noqa: E402
from incad.features import own_pattern  # noqa: E402
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
    harness.pump(700)


def top_edges(f, z):
    return ["Edge%d" % (i + 1) for i, e in enumerate(f.Shape.Edges)
            if abs(e.BoundBox.ZMin - z) < 1e-6 and abs(e.BoundBox.ZMax - z) < 1e-6]


def test_0_setup():
    prefs.set_remember_new_choice(True)
    prefs.set_default_units("mm")
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    from headless.test_thread import shaft, cyl_face
    from incad.features import thread
    pad = shaft(doc(), body(), radius=5.0, length=20.0, name="Shaft")
    v = dict(thread.defaults(doc(), body(), None), face=cyl_face(pad, 5), size="M10", clearance="0 mm")
    th = thread.create(doc(), body(), None, v)
    doc().recompute()
    ST["shaft"], ST["thread"] = pad.Name, th.Name
    harness.pump(300)


def test_1_picking_the_thread_means_the_shaft_with_its_thread():
    shaft, th = doc().getObject(ST["shaft"]), doc().getObject(ST["thread"])
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, th.Name)
    commands.REGISTRY.run("feature.rect_pattern")
    harness.pump(1000)
    vals = dialog().values()
    assert vals["features"] == shaft.Name, vals["features"]
    assert vals["children"] is True, "the thread comes along with the shaft"
    dialog().set_values({"dir1": "Y_Axis", "spacing1": "30 mm"})
    harness.pump(1200)
    assert not dialog().error_text(), dialog().error_text()
    shown = dialog().field("features").text()
    assert shaft.Label in shown and "dependent" in shown, shown
    ok()
    pattern = body().Tip
    assert own_pattern.is_own(pattern) and own_pattern.kind_of(pattern) == "rectangular", pattern.TypeId  # our own pattern
    assert [o.Name for o in pattern.Originals] == [shaft.Name, th.Name], [o.Name for o in pattern.Originals]
    assert pattern.Shape.BoundBox.YLength > 30, pattern.Shape.BoundBox


harness.run([test_0_setup, test_1_picking_the_thread_means_the_shaft_with_its_thread])
