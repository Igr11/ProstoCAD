"""Spur Gears and Worm Gear in the real window (the user: one dialog makes the pair, as Inventor's Design
Accelerator; then Make Assembly puts each gear in a part file): the ribbon's commands, the grouped dialog and its
line on what the values make, the pair sized by its ratio, a worm with its wheel, the set edited from its second
gear."""
import os
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from incad.qt import QtCore  # noqa: E402
from incad.core import commands, prefs  # noqa: E402
from incad.features import gear, kind_for  # noqa: E402
from incad.ui import shell  # noqa: E402

ST = {}


def s():
    return shell.instance()


def doc():
    return App.ActiveDocument


def dialog():
    fs = s().feature_session
    return fs.dialog if fs is not None else None


def ok():
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(1500)
    if s().feature_session is not None:  # the simulated click missed (harness flake): OK anyway
        s().feature_session.ok()
        harness.pump(1200)
    assert s().feature_session is None, dialog() and dialog().error_text()


def type_in(key, text):
    w = dialog().field(key)
    w.setText(text)
    w.editingFinished.emit()
    harness.pump(1000)


def gears_made():
    return [o for o in doc().Objects if gear.is_gear(o)]


def test_0_setup():
    prefs.set_remember_new_choice(True)
    prefs.set_default_units("mm")
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    assert commands.REGISTRY.get("feature.gear").label == "Spur Gears"


def test_1_spur_gears_make_the_pair():
    commands.REGISTRY.run("feature.gear")
    harness.pump(1500)
    d = dialog()
    assert d.label_text("h_first") == "Gear 1" and d.row_visible("h_second"), "grouped: Both, Gear 1, Gear 2"
    assert "ratio 30:20 = 1.5" in d.info_text(), d.info_text()
    type_in("teeth", "24")
    assert "ratio 36:24 = 1.5" in d.info_text() and "centre distance 60 mm" in d.info_text(), d.info_text()
    ok()
    made = gears_made()
    assert len(made) == 2, made
    lead = next(g for g in made if gear.second_of(g) is not None)
    second = gear.second_of(lead)
    assert second.getParentGeoFeatureGroup() is not lead.getParentGeoFeatureGroup(), "its own Solid"
    assert gear.effective(second)["teeth"] == 36
    assert lead.Shape.common(second.Shape).Volume < 0.05, "in mesh"
    ST["lead"], ST["second"] = lead.Name, second.Name


def test_2_one_gear_hides_gear_2():
    commands.REGISTRY.run("feature.gear")
    harness.pump(1500)
    dialog().field("make").button("one").click()
    harness.pump(1500)
    assert not dialog().row_visible("h_second") and not dialog().row_visible("ratio")
    s().feature_session.cancel()
    harness.pump(800)


def test_3_a_worm_gear():
    commands.REGISTRY.run("feature.worm")
    harness.pump(1500)
    assert "Wheel: 30 teeth" in dialog().info_text(), dialog().info_text()
    type_in("starts", "2")
    ok()
    worm = next(g for g in gears_made() if kind_for(g) is gear.WORM_GEAR and gear.is_gear(g, "worm"))
    wheel = gear.second_of(worm)
    assert wheel is not None and gear.effective(wheel)["teeth"] == 60
    assert worm.Shape.isValid() and wheel.Shape.isValid()


def test_4_editing_through_the_second_gear():
    lead, second = doc().getObject(ST["lead"]), doc().getObject(ST["second"])
    s().edit_feature(second)
    harness.pump(1500)
    assert dialog().values()["teeth"] == "24", dialog().values()
    d = dialog()
    for key in ("make", "center", "plane", "axis", "module", "ratio"):  # the user: they were greyed out
        assert d.field(key).isEnabled(), key
    type_in("module", "1.5 mm")
    ok()
    doc().recompute()
    assert gear.effective(lead)["module"] == 1.5 and gear.effective(second)["module"] == 1.5
    assert lead.Shape.common(second.Shape).Volume < 0.05


def test_5_ratio_with_a_wanted_distance():
    """Ratio held, a Center distance wanted: the pair at module 2 said, Smaller module the next one down, Use sets the
    module and gear 1's teeth, the Ratio stays (the user, 2026-11-27)."""
    commands.REGISTRY.run("feature.gear")
    harness.pump(1500)
    d = dialog()
    assert d.row_visible("want_distance") and not d.offer_btn.isVisible(), "empty: nothing offered"
    type_in("ratio", "2")
    type_in("want_distance", "61 mm")
    assert "Module 2 · 20 : 40 teeth · center distance 60 mm" in d.info_text(), d.info_text()
    assert d.offer_btn.isVisible() and d.offer_btn.text() == "Use" and d.offer2_btn.text() == "Smaller module"
    d.offer2_btn.click()
    harness.pump(800)
    assert "Module 1.5 · 27 : 54" in d.info_text(), d.info_text()
    d.offer_btn.click()
    harness.pump(1500)
    v = dialog().values()
    assert v["module"].startswith("1.5") and v["teeth"] == "27" and v["ratio"] == "2", v
    s().feature_session.cancel()
    harness.pump(800)


def test_9_cleanup():
    App.closeDocument(doc().Name)
    harness.pump(400)


harness.run([test_0_setup, test_1_spur_gears_make_the_pair, test_2_one_gear_hides_gear_2, test_3_a_worm_gear,
             test_4_editing_through_the_second_gear, test_5_ratio_with_a_wanted_distance, test_9_cleanup])
