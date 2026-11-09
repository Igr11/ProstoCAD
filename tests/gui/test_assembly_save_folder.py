"""Saving an assembly (the user: "when I save an assembly, a folder with its name is made at the place chosen, and
every part of the assembly goes into it"): Save As Gear.FCStd in a folder makes Gear/Gear.FCStd with copies of both
part files beside it, the links point at the copies, the originals stay where they were; a later Save keeps it so;
opened again, the assembly finds its parts in its folder."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from incad import actions  # noqa: E402
from incad.core import assembly, commands, document, pack, prefs  # noqa: E402
from incad.ui import shell  # noqa: E402
import headless.util as util  # noqa: E402

ST = {}


def s():
    return shell.instance()


def save_block(folder, name, w):
    d = util.new_doc(name)
    b = document.make_part(d)
    b.Tip = util.add_pad(b, util.add_rect_sketch(b, w, 10, "S"), 5)
    d.recompute()
    path = os.path.join(folder, name + ".FCStd")
    d.saveAs(path)
    App.closeDocument(d.Name)
    return path


def test_0_an_assembly_of_parts_from_elsewhere():
    prefs.set_remember_new_choice(True)
    prefs.set_default_units("mm")
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    root = tempfile.mkdtemp(prefix="incad_save_folder_")
    src = os.path.join(root, "library")
    os.makedirs(src)
    ST.update(root=root, a=save_block(src, "Plate", 40), b=save_block(src, "Pin", 8))
    doc = actions.new_assembly("mm")
    doc.saveAs(os.path.join(src, "Gear.FCStd"))  # saved once beside its parts, as the user's reducer was: the links
    s().assembly_place(ST["a"])                  # were then written from that old place and every part was lost
    s().assembly_place(ST["b"])
    harness.pump(800)
    doc.save()
    ST["asm"] = doc.Name


def test_1_save_as_makes_the_folder_with_every_part():
    target = os.path.join(ST["root"], "projects", "Gear.FCStd")
    os.makedirs(os.path.dirname(target))
    actions.SAVE_ASM_ASK = lambda doc, default: target
    try:
        r = commands.REGISTRY.run("file.save_as")
    finally:
        actions.SAVE_ASM_ASK = None
    harness.pump(800)
    assert getattr(r, "ok", True), getattr(r, "error", "")
    folder = os.path.join(ST["root"], "projects", "Gear")
    files = sorted(f for f in os.listdir(folder) if f.endswith(".FCStd"))  # FreeCAD's .FCBak backups aside
    assert files == ["Gear.FCStd", "Pin.FCStd", "Plate.FCStd"], files
    doc = App.getDocument(ST["asm"])
    assert os.path.abspath(doc.FileName) == os.path.join(folder, "Gear.FCStd"), doc.FileName
    for link in assembly.components(doc):
        assert os.path.dirname(link.LinkedObject.Document.FileName) == folder, link.LinkedObject.Document.FileName
    assert os.path.exists(ST["a"]) and os.path.exists(ST["b"]), "the originals must stay"
    assert pack.packed(doc)
    assert not Gui.getDocument(doc.Name).Modified
    ST["folder"] = folder


def test_2_save_again_keeps_it_there():
    doc = App.getDocument(ST["asm"])
    Gui.getDocument(doc.Name).Modified = True
    r = commands.REGISTRY.run("file.save")
    harness.pump(600)
    assert getattr(r, "ok", True), getattr(r, "error", "")
    files = sorted(f for f in os.listdir(ST["folder"]) if f.endswith(".FCStd"))  # FreeCAD's own .FCBak backups aside
    assert files == ["Gear.FCStd", "Pin.FCStd", "Plate.FCStd"], os.listdir(ST["folder"])
    assert not os.path.exists(os.path.join(ST["folder"], "Gear")), "a folder in the folder"


def test_2b_a_part_file_gone_from_the_folder_is_written_again():
    """The user: "Save failed: No such file or directory: …/Solid1.FCStd" — a part counted in the folder whose file
    is not there: it is written there anew, the save goes through."""
    g = App.ParamGet("User parameter:BaseApp/Preferences/Document")  # backups on, as the user has them (.FCBak):
    g.SetBool("CreateBackupFiles", True)  # FreeCAD's save backs the old file up first, and failed when it was gone
    g.SetBool("UseFCBakExtension", True)
    g.SetInt("CountBackupFiles", 1)
    doc = App.getDocument(ST["asm"])
    pin = os.path.join(ST["folder"], "Pin.FCStd")
    os.remove(pin)
    Gui.getDocument(doc.Name).Modified = True
    r = commands.REGISTRY.run("file.save")
    harness.pump(600)
    assert getattr(r, "ok", True), getattr(r, "error", "")
    assert os.path.exists(pin), os.listdir(ST["folder"])


def test_3_opened_again_it_finds_its_parts_there():
    for n in list(App.listDocuments()):
        App.closeDocument(n)
    harness.pump(500)
    doc = App.openDocument(os.path.join(ST["folder"], "Gear.FCStd"))
    harness.pump(1500)
    parts = [l.LinkedObject.Document.FileName for l in assembly.components(doc)]
    real = os.path.realpath(ST["folder"])  # /var is /private/var on the Mac
    assert len(parts) == 2 and all(os.path.realpath(os.path.dirname(p)) == real for p in parts), parts
    assert all(not l.Shape.isNull() for l in assembly.components(doc))


def test_4_a_lost_part_is_said_on_open_and_no_save_drops_it():
    """The user: an assembly whose part file is gone opened with the part silently missing, and a save kept it so.
    Now opening says which part is lost, and Save refuses with the same words."""
    import shutil
    from incad.core import log
    for n in list(App.listDocuments()):
        App.closeDocument(n)
    harness.pump(500)
    pin = os.path.join(ST["folder"], "Pin.FCStd")
    away = pin + ".away"
    shutil.move(pin, away)
    said = []
    real = log.report
    log.report = lambda level, text, *a, **k: (said.append((level, text)), real(level, text, *a, **k))[1]
    try:
        from incad.qt import QtCore
        closer = QtCore.QTimer()  # a window FreeCAD may put up about the missing file: closed, as a user would
        closer.timeout.connect(harness._close_modals)
        closer.start(300)
        doc = App.openDocument(os.path.join(ST["folder"], "Gear.FCStd"))
        harness.pump(2000)
        closer.stop()
        assert any(lv == "warning" and "Pin" in t and "missing" in t for lv, t in said), said
        Gui.getDocument(doc.Name).Modified = True
        r = commands.REGISTRY.run("file.save")
        harness.pump(600)
        assert not getattr(r, "ok", True) and "missing" in getattr(r, "error", ""), (getattr(r, "ok", None),
                                                                                      getattr(r, "error", ""))
        assert not os.path.exists(pin), "the assembly was saved without its part"
    finally:
        log.report = real
        for n in list(App.listDocuments()):
            App.closeDocument(n)
        harness.pump(300)
        shutil.move(away, pin)


def test_9_cleanup():
    for n in list(App.listDocuments()):
        App.closeDocument(n)
    harness.pump(300)


harness.run([test_0_an_assembly_of_parts_from_elsewhere, test_1_save_as_makes_the_folder_with_every_part,
             test_2_save_again_keeps_it_there, test_2b_a_part_file_gone_from_the_folder_is_written_again,
             test_3_opened_again_it_finds_its_parts_there,
             test_4_a_lost_part_is_said_on_open_and_no_save_drops_it, test_9_cleanup])
