"""A feature window opened with nothing picked leaves the part as it was and does not compute its Solid again (the
user, 2026-11-24: Work Plane stood 2 s on a part of 7000 faces each time it opened)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from incad.core import commands, document, prefs  # noqa: E402
from incad.ui import shell  # noqa: E402
import headless.util as util  # noqa: E402

COMMANDS = ("feature.extrude", "feature.revolve", "feature.hole", "feature.thread", "feature.fillet", "feature.chamfer",
            "feature.shell", "feature.combine", "feature.boss", "feature.circ_pattern", "feature.rect_pattern",
            "feature.coil", "feature.draft", "feature.emboss", "feature.full_round", "feature.gear", "feature.line3d",
            "feature.lip", "feature.loft", "feature.mirror", "feature.mirror_solid", "feature.move_face",
            "feature.snap_fit", "feature.split", "feature.sweep", "feature.worm", "work.plane", "work.axis",
            "work.point")
ST = {}


class Watch:
    def __init__(self):
        self.names = []

    def slotRecomputedObject(self, obj):
        self.names.append(obj.Name)


def doc():
    return App.ActiveDocument


def state():
    d = doc()
    return dict(objects=[o.Name for o in d.Objects], touched=sorted(o.Name for o in d.Objects if "Touched" in o.State),
                bad=sorted(o.Name for o in d.Objects if "Invalid" in o.State), volume=round(ST["body"].Shape.Volume, 6),
                faces=len(ST["body"].Shape.Faces), tip=ST["body"].Tip.Name)


def test_0_part():
    prefs.set_remember_new_choice(True)
    prefs.set_default_units("mm")
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    body = ST["body"] = document.primary_body(doc())
    ST["pad"] = util.add_pad(body, util.add_rect_sketch(body, 20, 10, "Base"), 8)
    doc().recompute()
    harness.pump(500)


def test_1_every_window_leaves_the_part():
    out, bad = {}, []
    for cmd in COMMANDS:
        Gui.Selection.clearSelection()
        harness.pump(150)
        was, w = state(), Watch()
        App.addDocumentObserver(w)
        try:
            commands.REGISTRY.run(cmd)
            harness.pump(900)
            fs = shell.instance().feature_session
            made = fs is not None and fs.feature is not None
            inside = state()
            if fs is not None:
                fs.cancel()
            harness.pump(300)
        finally:
            App.removeDocumentObserver(w)
        now = state()
        if now != was:
            bad.append("%s: %s -> %s" % (cmd, was, now))
        out[cmd] = dict(made=made, body=w.names.count(ST["body"].Name), touched=inside["touched"])
    if os.environ.get("INCAD_WINDOWS_OUT"):
        json.dump(out, open(os.environ["INCAD_WINDOWS_OUT"], "w"), indent=0, sort_keys=True)
    assert not bad, bad
    assert out["work.plane"]["body"] == 0, out["work.plane"]
    left = {c: o["touched"] for c, o in out.items() if not o["made"] and o["touched"]}
    assert not left, left


def test_2_made_after_an_empty_window():
    """Opened with nothing picked, then a plane really made, and an extrude: the Solid is computed as ever."""
    fs_run = commands.REGISTRY.run
    fs_run("work.plane")
    harness.pump(700)
    shell.instance().feature_session.cancel()
    harness.pump(300)
    top = util.add_rect_sketch(ST["body"], 6, 4, "Top")
    util.add_pad(ST["body"], top, 12)
    doc().recompute()
    assert abs(ST["body"].Shape.Volume - (20 * 10 * 8 + 6 * 4 * 4)) < 1e-6, ST["body"].Shape.Volume
    ST["pad"].Length = 9
    doc().recompute()
    assert abs(ST["body"].Shape.Volume - (20 * 10 * 9 + 6 * 4 * 3)) < 1e-6, ST["body"].Shape.Volume


def test_3_what_the_attempt_computed_is_computed_again():
    from incad.ui import feature_session
    fs = None
    commands.REGISTRY.run("work.plane")
    harness.pump(700)
    fs = shell.instance().feature_session
    d = doc()
    fs._note_bodies()
    ST["pad"].Length = 5  # an attempt that changed and computed the part, then failed
    d.recompute()
    fs._drop_partial()
    assert abs(ST["pad"].Length.Value - 9) < 1e-9
    assert abs(ST["body"].Shape.Volume - (20 * 10 * 9 + 6 * 4 * 3)) < 1e-6, ST["body"].Shape.Volume
    fs.cancel()
    harness.pump(300)


def test_9_cleanup():
    for name in list(App.listDocuments()):
        App.closeDocument(name)
        harness.pump(300)


harness.run([test_0_part, test_1_every_window_leaves_the_part, test_2_made_after_an_empty_window,
             test_3_what_the_attempt_computed_is_computed_again, test_9_cleanup])
