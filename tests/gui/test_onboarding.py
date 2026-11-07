import os
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
import harness  # noqa: E402
import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from pivy import coin  # noqa: E402
from incad.core import commands, document, prefs  # noqa: E402
from incad.ui import marking_layout, shell, sketch_session  # noqa: E402


def s():
    return shell.instance()


def view_size():
    cam = Gui.ActiveDocument.ActiveView.getCameraNode()
    if isinstance(cam, coin.SoOrthographicCamera):
        return cam.height.getValue()
    return 2 * cam.focalDistance.getValue()


def test_0_new_part_starts_near_the_origin():
    prefs.set_remember_new_choice(True)
    prefs.set_default_units("mm")
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    commands.REGISTRY.run("file.new")
    harness.pump(1000)
    assert 20 < view_size() < 1000, view_size()  # the origin in view: not a few mm, not kilometres


def test_1_plane_panel_picks_a_plane():
    commands.REGISTRY.run("sketch.start")
    harness.pump(800)
    panel = s().plane_panel
    assert panel is not None and panel.isVisible()
    assert 20 < view_size() < 1000, view_size()
    panel.button("XZ_Plane").click()
    harness.pump(1200)
    sk = s().sketch_session().editing() if callable(getattr(s(), "sketch_session", None)) else s().sketches.editing()
    assert sk is not None
    assert sk.AttachmentSupport[0][0].Role == "XZ_Plane", sk.AttachmentSupport
    assert not s().plane_panel.isVisible()


def test_2_look_at_faces_the_sketch_while_sketching():
    view = Gui.ActiveDocument.ActiveView
    view.viewIsometric()
    harness.pump(300)
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("view.look_at")
    harness.pump(1500)
    d = view.getViewDirection()
    assert abs(abs(d.y) - 1) < 1e-3, d  # looking along the XZ plane's normal
    # (no longer in the sketch's marking menu: the user cut it to what applies under the cursor; the view strip has it)


def test_3_double_click_a_plane_in_the_browser_starts_a_sketch():
    from incad.core import context
    from incad.ui import marking_layout
    commands.REGISTRY.run("sketch.finish")
    harness.pump(800)
    body = document.primary_body(App.ActiveDocument)
    yz = [o for o in body.Origin.OriginFeatures if o.Role == "YZ_Plane"][0]
    s().browser.featureActivated.emit(yz.Name)
    harness.pump(1200)
    sk = s().sketches.editing()
    assert sk is not None and sk.AttachmentSupport[0][0] is yz, sk and sk.AttachmentSupport
    commands.REGISTRY.run("sketch.finish")
    harness.pump(800)
    browser = marking_layout.LAYOUT["browser"]
    assert "sketch.start" in list(browser.values()) + browser["list"]
    xy = [o for o in body.Origin.OriginFeatures if o.Role == "XY_Plane"][0]
    context.set_target(xy.Name)
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("sketch.start")
    harness.pump(1200)
    sk = s().sketches.editing()
    assert sk is not None and sk.AttachmentSupport[0][0] is xy, sk and sk.AttachmentSupport
    commands.REGISTRY.run("sketch.finish")
    harness.pump(800)


def plane_on_screen(role):
    """(fraction of the view height the plane covers, all its corners inside the view) while picking."""
    view = Gui.ActiveDocument.ActiveView
    body = document.primary_body(App.ActiveDocument)
    plane = [o for o in body.Origin.OriginFeatures if o.Role == role][0]
    bb = sketch_session.screen_box(view, [plane.ViewObject])
    w, h = view.getSize()
    pts = [view.getPointOnViewport(App.Vector(x, y, z))
           for x in (bb.XMin, bb.XMax) for y in (bb.YMin, bb.YMax) for z in (bb.ZMin, bb.ZMax)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    inside = all(0 <= x <= w for x in xs) and all(0 <= y <= h for y in ys)
    return max(max(xs) - min(xs), max(ys) - min(ys)) / float(h), inside


def test_4_origin_planes_fill_the_view_while_picking():
    commands.REGISTRY.run("file.new")
    harness.pump(1000)
    commands.REGISTRY.run("sketch.start")
    harness.pump(800)
    w, h = Gui.ActiveDocument.ActiveView.getSize()
    cx, cy = Gui.ActiveDocument.ActiveView.getPointOnViewport(App.Vector(0, 0, 0))
    assert abs(cx - w / 2.0) < 3 and abs(cy - h / 2.0) < 3, (cx, cy, w, h)  # the origin in the middle
    size, inside = plane_on_screen("XY_Plane")
    assert inside and size > 0.25, (size, inside)  # big enough to see and click, not a speck
    s().sketches.cancel_pick()
    harness.pump(300)
    assert App.ParamGet(sketch_session._VIEW).GetFloat("DatumScale", -1.0) == -1.0  # user's setting back
    doc = App.ActiveDocument
    box = document.primary_body(doc).newObject("PartDesign::AdditiveBox", "Box")
    box.Length = box.Width = box.Height = 80
    doc.recompute()
    view = Gui.ActiveDocument.ActiveView
    cam = view.getCameraNode()
    cam.position.setValue(cam.position.getValue() + coin.SbVec3f(3000, 0, 0))  # looking somewhere else
    harness.pump(300)
    before = tuple(cam.position.getValue())
    commands.REGISTRY.run("sketch.start")
    harness.pump(800)
    # with a solid in the part the origin planes are still offered (big enough to see), the view is left where
    # the user had it, and the plane panel is there too
    assert tuple(cam.position.getValue()) == before, (before, tuple(cam.position.getValue()))
    body = document.primary_body(doc)
    assert body.Origin.ViewObject.Visibility and all(o.ViewObject.Visibility for o in body.Origin.OriginFeatures)
    assert App.ParamGet(sketch_session._VIEW).GetFloat("DatumScale", -1.0) >= sketch_session.PICK_DATUM_SCALE
    assert s().plane_panel is not None and s().plane_panel.isVisible()  # origin planes still one click away
    s().sketches.cancel_pick()
    harness.pump(300)
    assert not body.Origin.ViewObject.Visibility
    assert App.ParamGet(sketch_session._VIEW).GetFloat("DatumScale", -1.0) == -1.0  # user's setting back


def test_4b_a_face_under_the_cursor_wins_over_a_plane():
    """A plane drawn in front of the model must not take the click meant for a face (a real click on the top
    face landed on the XZ plane): the pick is resolved by what is under the cursor."""
    doc = App.ActiveDocument
    body = document.primary_body(doc)
    box = doc.getObject("Box")
    view = Gui.ActiveDocument.ActiveView
    view.viewIsometric()
    view.fitAll()
    harness.pump(500)
    xz = [o for o in body.Origin.OriginFeatures if o.Role == "XZ_Plane"][0]
    top = [i + 1 for i, f in enumerate(box.Shape.Faces) if abs(f.CenterOfMass.z - 80) < 1e-6][0]
    commands.REGISTRY.run("sketch.start")
    harness.pump(800)
    on_face = box.Shape.Faces[top - 1].CenterOfMass
    Gui.Selection.addSelection(doc.Name, xz.Name, "", on_face.x, on_face.y, on_face.z)  # FreeCAD picked the plane
    harness.pump(1200)
    sk = s().sketches.editing()
    assert sk is not None, "no sketch opened"
    support = sk.AttachmentSupport[0]
    assert support[0] is box and support[1][0] == "Face%d" % top, (support[0].Name, support[1])
    commands.REGISTRY.run("sketch.finish")
    harness.pump(800)
    # the same plane picked beside the model (nothing else under the cursor) is the plane
    commands.REGISTRY.run("sketch.start")
    harness.pump(800)
    Gui.Selection.addSelection(doc.Name, xz.Name, "", 300.0, 0.0, 300.0)
    harness.pump(1200)
    sk = s().sketches.editing()
    assert sk is not None and sk.AttachmentSupport[0][0] is xz, sk and sk.AttachmentSupport
    commands.REGISTRY.run("sketch.finish")
    harness.pump(800)


def sketch_up(sk):
    """(camera up · sketch Y, view direction · sketch normal): (1, -1) = the sketch upright, seen from the front."""
    cam = Gui.ActiveDocument.ActiveView.getCameraNode()
    up = App.Vector(*cam.orientation.getValue().multVec(coin.SbVec3f(0, 1, 0)).getValue())
    look = App.Vector(*cam.orientation.getValue().multVec(coin.SbVec3f(0, 0, -1)).getValue())
    r = sk.getGlobalPlacement().Rotation
    return round(up.dot(r.multVec(App.Vector(0, 1, 0))), 3), round(look.dot(r.multVec(App.Vector(0, 0, 1))), 3)


def test_5_look_at_puts_an_upside_down_sketch_upright():
    """The user's dimension text read upside down: the view was turned 180° in the sketch's plane."""
    commands.REGISTRY.run("sketch.start")
    harness.pump(800)
    s().plane_panel.button("XZ_Plane").click()
    harness.pump(1500)
    sk = Gui.ActiveDocument.getInEdit().Object
    cam = Gui.ActiveDocument.ActiveView.getCameraNode()
    d = cam.orientation.getValue().multVec(coin.SbVec3f(0, 0, -1))
    cam.orientation.setValue(cam.orientation.getValue() * coin.SbRotation(d, 3.14159265))  # turned upside down
    harness.pump(300)
    assert sketch_up(sk)[0] < 0, sketch_up(sk)
    Gui.Selection.clearSelection()
    commands.REGISTRY.run("view.look_at")
    harness.pump(1500)
    assert sketch_up(sk) == (1.0, -1.0), sketch_up(sk)
    commands.REGISTRY.run("sketch.finish")
    harness.pump(800)


harness.run([test_0_new_part_starts_near_the_origin, test_1_plane_panel_picks_a_plane,
             test_2_look_at_faces_the_sketch_while_sketching,
             test_3_double_click_a_plane_in_the_browser_starts_a_sketch,
             test_4_origin_planes_fill_the_view_while_picking, test_4b_a_face_under_the_cursor_wins_over_a_plane,
             test_5_look_at_puts_an_upside_down_sketch_upright])
