"""The user: the Shell window stood still 2.5 s on every new wall thickness (a Loft on an imported gear). The wall is
computed in a process of its own: what shows stays, "Computing…" in grey, a new value stops the job running, Esc
stops it (the window stays open, "Stopped"), OK pressed meanwhile waits for the wall and then closes the window."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
import Part  # noqa: E402
from incad.qt import QtCore  # noqa: E402
from incad.core import commands, document, import_step, prefs, shell_solid  # noqa: E402
from incad.ui import shell  # noqa: E402

ST = {}


def doc():
    return App.getDocument(ST["doc"])


def fs():
    return shell.instance().feature_session


def note():
    return fs().dialog.warn.text() if fs() is not None else None


def wait_job(seconds=90):
    end = time.time() + seconds
    while shell_solid.running():
        assert time.time() < end, "the background job never ended"
        harness.pump(100)
    harness.pump(400)


def thickness(text):
    d = fs().dialog
    d.set_values({"thickness": text})
    d.changed.emit(d.values())
    harness.pump(500)  # past the window's pause after a value: the preview starts the job


def test_0_setup():
    prefs.set_remember_new_choice(True)
    prefs.set_default_units("mm")
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    d = App.newDocument("ShellBackground")
    ST["doc"] = d.Name
    body = document.make_part(d)
    src = d.addObject("Part::Feature", "Imported")
    src.Shape = Part.makeBox(40, 30, 20)
    src.addProperty("App::PropertyString", import_step.MARK)
    setattr(src, import_step.MARK, "/somewhere/gear.step")
    body.BaseFeature = src
    box = body.newObject("PartDesign::AdditiveBox", "Box")
    box.Length, box.Width, box.Height = 20, 10, 15
    box.Placement = App.Placement(App.Vector(10, 10, 20), App.Rotation())
    d.recompute()
    ST["box"] = box.Name
    # the test's block is computed in a fraction of a second: each job waits 2 s first, so the test sees it run
    shell_solid._JOB_CHILD = "import time\ntime.sleep(2)\n" + shell_solid._JOB_CHILD
    harness.pump(500)


def test_1_the_window_computes_in_the_background():
    box = doc().getObject(ST["box"])
    top = next(i for i, f in enumerate(box.Shape.Faces, 1) if abs(f.CenterOfMass.z - 35) < 1e-6)
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("feature.shell")
    harness.pump(1000)
    assert fs() is not None, "the Shell window opens"
    Gui.Selection.addSelection(doc().Name, box.Name, "Face%d" % top)
    harness.pump(800)
    wait_job()
    f = fs().feature
    assert f is not None and "Invalid" not in f.State, fs().dialog.error_text()
    assert abs(f.Shape.Volume - (24000 + 3000 - 18 * 8 * 14)) < 1e-3, f.Shape.Volume

    thickness("1.5 mm")
    assert shell_solid.running(), "a new thickness is computed in the background"
    assert note() == "Computing…", note()
    assert abs(f.Shape.Volume - (24000 + 3000 - 18 * 8 * 14)) < 1e-3, "what shows stays meanwhile"

    thickness("2 mm")  # typed while 1.5 mm computes: that job stops, this one runs
    assert shell_solid.running() and shell_solid._JOB["job"].key[-2] == 2.0
    wait_job()
    assert abs(f.Shape.Volume - (24000 + 3000 - 16 * 6 * 13)) < 1e-3, f.Shape.Volume
    assert note() != "Computing…", note()


def test_2_esc_stops_the_job_and_the_window_stays():
    f = fs().feature
    shown = f.Shape.Volume
    thickness("1.5 mm")
    assert shell_solid.running()
    QTest.keyClick(fs().dialog, QtCore.Qt.Key_Escape)
    harness.pump(300)
    assert not shell_solid.running(), "Esc stops the job"
    assert fs() is not None, "the window stays open"
    assert note() == "Stopped", note()
    assert abs(f.Shape.Volume - shown) < 1e-6, "what showed stays"


def test_3_ok_waits_for_the_wall():
    QTest.mouseClick(fs().dialog.ok_button, QtCore.Qt.LeftButton)  # 1.5 mm, stopped by Esc: computed again
    harness.pump(300)
    assert fs() is not None and shell_solid.running(), "OK waits while the wall computes"
    wait_job()
    harness.pump(800)
    assert fs() is None, "the window closed once the wall came"
    tip = document.primary_body(doc()).Tip
    assert abs(tip.Shape.Volume - (24000 + 3000 - 17 * 7 * 13.5)) < 1e-3, tip.Shape.Volume


def test_9_cleanup():
    shell_solid.stop()
    if ST.get("doc") in App.listDocuments():
        App.closeDocument(ST["doc"])
        harness.pump(400)


harness.run([test_0_setup, test_1_the_window_computes_in_the_background, test_2_esc_stops_the_job_and_the_window_stays,
             test_3_ok_waits_for_the_wall, test_9_cleanup])
