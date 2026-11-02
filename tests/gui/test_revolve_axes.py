"""Revolve dialog: the axis chosen five ways — sketch axes, a listed construction line, a picked line / axis /
edge, two points clicked on the markers — and the hidden axis helper shown under the revolve and deleted with it."""
import os
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
import Part  # noqa: E402
import Sketcher  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from incad.qt import QtCore, QtWidgets, viewport_of  # noqa: E402
from incad.core import commands, document, prefs  # noqa: E402
from incad.features import revolve  # noqa: E402
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


def feature():
    return s().feature_session.feature


def rect(support, sub, pts, name, construction=()):
    sk = body().newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(support, sub)]
    sk.MapMode = "FlatFace"
    for i in range(4):
        a, b = pts[i], pts[(i + 1) % 4]
        sk.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)))
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    for a, b in construction:
        sk.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)), True)
    doc().recompute()
    return sk


def choose(key, value):
    w = dialog().field(key)
    w.setCurrentIndex(w.findData(value))
    harness.pump(1200)


def open_revolve(sk):
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, sk.Name)
    commands.REGISTRY.run("feature.revolve")
    harness.pump(1000)
    assert dialog() is not None and dialog().isVisible()


def error():
    return dialog()._error.text()


def test_0_setup():
    prefs.set_remember_new_choice(True)
    prefs.set_default_units("mm")
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(800)
    ST["pad"] = util.add_pad(body(), util.add_rect_sketch(body(), 40, 30, name="Base"), 20)
    doc().recompute()
    ST["v0"] = body().Shape.Volume
    xz = [o for o in body().Origin.OriginFeatures if o.Role == "XZ_Plane"][0]
    ST["xz"] = xz
    ST["sk"] = rect(xz, "", [(10, 5), (18, 5), (18, 15), (10, 15)], "Prof",
                    construction=[((5, 0), (5, 20)), ((0, 25), (30, 25))])
    harness.pump(300)


def test_1_construction_lines_listed_sketch_axis_default_with_two():
    open_revolve(ST["sk"])
    w = dialog().field("axis_construction")
    items = [w.itemData(i) for i in range(w.count())]  # construction lines first, then the profile's own lines
    assert items[:2] == ["Axis0", "Axis1"] and all(x.startswith("Line") for x in items[2:]), items  # (the law)
    assert "vertical" in w.itemText(0) and "horizontal" in w.itemText(1), (w.itemText(0), w.itemText(1))
    assert dialog().values()["axis_mode"] == "V_Axis"
    assert not dialog().field("axis_construction").isVisible()
    choose("axis_mode", "construction")
    assert dialog().field("axis_construction").isVisible()
    f = feature()
    assert revolve.axis_name(f.ReferenceAxis[1][0]) == "Axis0", f.ReferenceAxis
    assert error() == "", error()
    choose("axis_construction", "Axis1")
    assert feature().ReferenceAxis[1] == ["Axis1"], feature().ReferenceAxis


def test_2_reference_pick_takes_a_sketch_line_then_an_origin_axis():
    choose("axis_mode", "reference")
    btn = dialog().field("axis_ref")
    assert btn.isVisible() and btn.text() == "Select…", btn.text()
    QTest.mouseClick(btn, QtCore.Qt.LeftButton)
    harness.pump(400)
    assert dialog().active_pick() == "axis_ref"
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, ST["sk"].Name, "Edge4")  # the profile's left side
    harness.pump(1200)
    assert dialog().values()["axis_ref"] == "%s:Edge4" % ST["sk"].Name, dialog().values()["axis_ref"]
    assert feature().ReferenceAxis[0] is ST["sk"] and feature().ReferenceAxis[1] == ["Edge4"], feature().ReferenceAxis
    QTest.mouseClick(btn, QtCore.Qt.LeftButton)  # pick again: an origin axis from the browser
    harness.pump(400)
    z = [o for o in body().Origin.OriginFeatures if o.Role == "Z_Axis"][0]
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc().Name, z.Name)
    harness.pump(1200)
    assert feature().ReferenceAxis[0] is z, feature().ReferenceAxis
    assert error() == "", error()


def test_3_two_points_on_the_markers_make_a_hidden_axis_under_the_revolve():
    choose("axis_mode", "two_points")
    btn = dialog().field("axis_points")
    assert btn.isVisible() and btn.text() == "Select…", btn.text()
    QTest.mouseClick(btn, QtCore.Qt.LeftButton)
    harness.pump(600)
    pick = s()._point_pick
    assert pick is not None and len(pick.points) >= 8, pick and len(pick.points)
    # two corners of the pad on its x = 40 end, one above the other: a vertical axis beside the profile
    view = Gui.ActiveDocument.ActiveView
    vp = viewport_of(view)
    dpr = vp.devicePixelRatioF()
    wanted = [App.Vector(40, 0, 0), App.Vector(40, 0, 20)]
    for p in wanted:
        i = next(i for i, q in enumerate(pick.points) if (q - p).Length < 1e-6)
        sx, sy = view.getPointOnViewport(pick.points[i])
        s()._point_clicked(vp, sx / dpr, vp.height() - sy / dpr)
        harness.pump(400)
    harness.pump(1500)
    assert s()._point_pick is None
    assert dialog().values()["axis_points"] == "40 0 0;40 0 20", dialog().values()["axis_points"]
    assert btn.text() == "2 points", btn.text()
    f = feature()
    line = f.ReferenceAxis[0]
    assert line.TypeId == "PartDesign::Line" and getattr(line, "InCadHelperOf") == f.Name, f.ReferenceAxis
    assert error() == "", error()
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(1000)
    assert s().feature_session is None
    rev = body().Tip
    assert rev.TypeId == "PartDesign::Revolution", rev.TypeId
    ST["rev"], ST["line"] = rev.Name, line.Name
    rows = s().browser.tree_rows()
    labels = [(d, label) for d, label, kind, state in rows]
    assert (1, rev.Label) in labels and (2, "Revolve axis") in labels, labels
    assert body().Shape.Volume > ST["v0"] + 1


def test_4_edit_reads_two_points_back_and_delete_removes_the_axis():
    shell.edit_feature(doc().getObject(ST["rev"]))
    harness.pump(1000)
    assert dialog().values()["axis_mode"] == "two_points" and dialog().values()["axis_points"] == "40 0 0;40 0 20", dialog().values()
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(800)
    from incad.core import context
    context.set_target(ST["rev"])
    commands.REGISTRY.run("browser.delete")
    harness.pump(1000)
    assert doc().getObject(ST["rev"]) is None and doc().getObject(ST["line"]) is None


def test_5_cut_messages_are_plain():
    sk = rect(ST["xz"], "", [(-5, 5), (5, 5), (5, 15), (-5, 15)], "Cross")
    open_revolve(sk)
    QTest.mouseClick(dialog().field("operation").button("cut"), QtCore.Qt.LeftButton)
    harness.pump(1500)
    assert "crosses the profile" in error(), error()
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(800)




def test_6_toggle_cut_with_an_invalid_axis_does_not_leave_a_deleted_feature():
    """Join->Cut while the axis is not yet valid (Two points, none picked) deletes the old feature in the type
    swap; update() used to fail before returning the new one, leaving a deleted reference that crashed the next
    preview with 'TypeId of deleted object'. The axis is validated before the swap now; every preview is clean."""
    sk = rect(ST["xz"], "", [(10, 5), (18, 5), (18, 15), (10, 15)], "Prof2")
    open_revolve(sk)

    def mode(v):
        w = dialog().field("axis_mode")
        w.setCurrentIndex(w.findData(v))
        harness.pump(1000)

    def op(v):
        QTest.mouseClick(dialog().field("operation").button(v), QtCore.Qt.LeftButton)
        harness.pump(1000)

    mode("two_points")
    assert "two points" in error().lower(), error()
    assert feature() is not None and feature().TypeId == "PartDesign::Revolution"  # alive, not deleted
    op("cut")
    assert "two points" in error().lower(), error()
    assert feature() is not None and feature().TypeId == "PartDesign::Revolution", feature()  # swap didn't run
    op("join")
    assert "two points" in error().lower(), error()
    assert feature() is not None and feature().TypeId == "PartDesign::Revolution"
    mode("V_Axis")  # a valid axis: the preview recovers with no crash
    harness.pump(1000)
    assert error() == "", error()
    assert feature() is not None and feature().Shape.Volume > 1
    QTest.mouseClick(dialog().ok_button, QtCore.Qt.LeftButton)
    harness.pump(800)
    assert s().feature_session is None
    assert body().Tip.TypeId == "PartDesign::Revolution"


def test_7_projected_line_axis_through_the_dialog():
    """Project an edge of the solid into the profile sketch, then revolve around it: the Construction / projected
    drop-down lists the in-plane projection and the revolve spins around it (a helper through the projection)."""
    body_ = body()
    pad = util.add_pad(body_, util.add_rect_sketch(body_, 16, 16, name="Boss"), 24, name="Boss")
    doc().recompute()
    xz = ST["xz"]
    sk = body_.newObject("Sketcher::SketchObject", "ProjProf")
    sk.AttachmentSupport = [(xz, "")]
    sk.MapMode = "FlatFace"
    doc().recompute()
    for i in range(4):
        a, b = [(40, 4), (46, 4), (46, 12), (40, 12)][i], [(40, 4), (46, 4), (46, 12), (40, 12)][(i + 1) % 4]
        sk.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)))
    vedge = next(i + 1 for i, e in enumerate(pad.Shape.Edges)
                 if e.Curve.TypeId == "Part::GeomLine" and abs(e.Vertexes[0].Point.z - e.Vertexes[1].Point.z) > 1)
    sk.addExternal(pad.Name, "Edge%d" % vedge)
    doc().recompute()
    open_revolve(sk)
    choose("axis_mode", "construction")
    w = dialog().field("axis_construction")
    datas = [w.itemData(i) for i in range(w.count())]
    assert any(d.startswith("ext") for d in datas), datas
    ext = next(d for d in datas if d.startswith("ext"))
    w.setCurrentIndex(w.findData(ext))
    harness.pump(1200)
    assert error() == "", error()
    f = feature()
    line = f.ReferenceAxis[0]
    assert line.TypeId == "PartDesign::Line" and getattr(line, "InCadHelperOf") == f.Name, f.ReferenceAxis
    assert f.Shape.Volume > 1
    QTest.mouseClick(dialog().cancel_button, QtCore.Qt.LeftButton)
    harness.pump(600)



harness.run([test_0_setup, test_1_construction_lines_listed_sketch_axis_default_with_two,
             test_2_reference_pick_takes_a_sketch_line_then_an_origin_axis,
             test_3_two_points_on_the_markers_make_a_hidden_axis_under_the_revolve,
             test_4_edit_reads_two_points_back_and_delete_removes_the_axis, test_5_cut_messages_are_plain,
             test_6_toggle_cut_with_an_invalid_axis_does_not_leave_a_deleted_feature,
             test_7_projected_line_axis_through_the_dialog])
