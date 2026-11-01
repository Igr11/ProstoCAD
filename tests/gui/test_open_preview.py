"""File ▸ Open shows the picture of the file under the cursor (the user: a preview of the file before opening it):
the FreeCAD file's saved thumbnail beside the list; a file without one says so; the opened file is a normal open
(in the window, in Recent Files)."""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from incad import actions  # noqa: E402
from incad.ui import open_dialog  # noqa: E402

ST = {}


def test_0_files():
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(800)
    d = tempfile.mkdtemp()
    doc = App.newDocument("Pic")
    box = doc.addObject("Part::Box", "Box")
    doc.recompute()
    Gui.SendMsgToActiveView("ViewFit")
    harness.pump(500)
    App.ParamGet("User parameter:BaseApp/Preferences/Document").SetBool("SaveThumbnail", True)
    path = os.path.join(d, "with_picture.FCStd")
    doc.saveAs(path)
    App.closeDocument(doc.Name)
    plain = os.path.join(d, "notes.txt")
    open(plain, "w").write("x")
    ST.update(dir=d, path=path, plain=plain)
    assert open_dialog.thumbnail(path), "no thumbnail saved in the file"


def test_1_preview_shows_the_picture():
    dlg = open_dialog.OpenDialog(Gui.getMainWindow(), "Open", ST["dir"], actions.open_filters())
    dlg.show()
    harness.pump(800)
    dlg.selectFile(ST["path"])
    harness.pump(800)
    assert dlg.preview.has_picture(), "no picture for %s" % dlg.preview.path
    assert dlg.preview.name.text() == "with_picture.FCStd", dlg.preview.name.text()
    if os.environ.get("INNCAD_SHOT"):
        dlg.grab().save(os.environ["INNCAD_SHOT"])
    dlg.preview.show_file(ST["plain"])
    assert not dlg.preview.has_picture() and dlg.preview.picture.text() == "No preview"
    dlg.reject()
    harness.pump(300)


def test_2_open_goes_through_freecad():
    actions.OPEN_ASK = lambda: ST["path"]
    try:
        doc = actions.open_file()
        harness.pump(1000)
        assert doc is not None and os.path.abspath(doc.FileName) == os.path.abspath(ST["path"]), doc
        again = actions.open_file()  # already open: the same document, not a second copy
        assert again is doc and len([d for d in App.listDocuments().values() if d.FileName == doc.FileName]) == 1
    finally:
        actions.OPEN_ASK = None


def test_2b_short_list_and_a_step_opens_as_a_new_part():
    """Only what can be opened is listed (the user: the list was far too long)."""
    filters = actions.open_filters().split(";;")
    assert len(filters) <= 4 and not any("IGES" in f or "OBJ" in f for f in filters), filters
    assert "*.FCStd" in filters[0] and "*.step" in filters[0], filters[0]


def test_3_a_full_folder_and_an_stl():
    """A folder with many FreeCAD files and STLs opens and changes without hanging (the user: it hung on a new
    folder); an STL shows its shaded picture."""
    import time
    folder = "/Users/igor/Plasticity_projects"
    if not os.path.isdir(folder):
        return
    dlg = open_dialog.OpenDialog(Gui.getMainWindow(), "Import", ST["dir"], "STEP or STL (*.step *.stp *.stl *.STL);;All files (*)")
    dlg.show()
    harness.pump(500)
    t = time.time()
    dlg.setDirectory(folder)
    harness.pump(2500)
    dlg.setDirectory(os.path.expanduser("~"))
    harness.pump(1000)
    dlg.setDirectory(folder)
    harness.pump(1500)
    assert time.time() - t < 15, "the folder took %.1f s" % (time.time() - t)
    stl = os.path.join(folder, "bolt_M10.stl")
    if os.path.isfile(stl):
        dlg.selectFile(stl)
        harness.pump(1500)
        assert dlg.preview.has_picture(), "no picture of the STL"
        if os.environ.get("INNCAD_SHOT"):
            dlg.grab().save(os.environ["INNCAD_SHOT"].replace(".png", "_stl.png"))
    step = os.path.join(folder, "ammer.step")
    if os.path.isfile(step):  # a STEP is read and meshed for its picture
        dlg.selectFile(step)
        harness.pump(1500)
        assert dlg.preview.has_picture(), "no picture of the STEP"
        if os.environ.get("INNCAD_SHOT"):
            dlg.grab().save(os.environ["INNCAD_SHOT"].replace(".png", "_step.png"))
    dlg.reject()
    harness.pump(300)


def test_9_cleanup():
    for d in list(App.listDocuments().values()):
        App.closeDocument(d.Name)
    shutil.rmtree(ST["dir"], ignore_errors=True)


harness.run([test_0_files, test_1_preview_shows_the_picture, test_2_open_goes_through_freecad,
             test_2b_short_list_and_a_step_opens_as_a_new_part, test_3_a_full_folder_and_an_stl,
             test_9_cleanup])
