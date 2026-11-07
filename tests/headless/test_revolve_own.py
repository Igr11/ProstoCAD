"""ProstoCAD's own Revolve on OpenCASCADE (incad.features.revolve.RevolveFeature, incad.core.revolve_solid): exact
volumes, one sound solid where the profile touches the axis or the part, the axis line keeping its kind, FreeCAD's
Revolution / Groove of an older file taken over on edit, a pattern of it, undo."""
import math
import unittest

import FreeCAD as App
import Part
from incad.core import document, model
from incad.features import common, kind_for, own_pattern, patterns, revolve
from headless import util


def _sketch(body, support, pts, construction=(), name="Prof", lines=()):
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(support, "")]
    sk.MapMode = "FlatFace"
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        sk.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)))
    for a, b in lines:
        sk.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)))
    for a, b in construction:
        sk.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)), True)
    body.Document.recompute()
    return sk


class OwnRevolveTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Own")
        document.make_part(self.doc, "mm")
        self.body = document.primary_body(self.doc)
        self.xz = self._origin("XZ_Plane")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def _origin(self, role):
        return [o for o in self.body.Origin.OriginFeatures if o.Role == role][0]

    def make(self, sk, **over):
        values = revolve.defaults(self.doc, self.body, sk)
        values.update(over)
        f = revolve.create(self.doc, self.body, sk, values)
        self.doc.recompute()
        return f

    def _sound(self, f, volume, places=6):
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        self.assertEqual(len(f.Shape.Solids), 1)
        self.assertTrue(f.Shape.isValid())
        f.Shape.check(True)  # raises when faulty
        self.assertAlmostEqual(f.Shape.Volume, volume, places=places)

    def _block(self, half=10, height=10):
        """A block centred on the Z axis, our own Extrude."""
        from incad.features import extrude
        sk = _sketch(self.body, util.xy_plane(self.body), ((-half, -half), (half, -half), (half, half), (-half, half)),
                     name="Base")
        f = extrude.create(self.doc, self.body, sk, dict(extrude.defaults(self.doc, self.body, sk), operation="join",
                                                         distance="%g mm" % height))
        self.doc.recompute()
        return f

    def test_own_feature_and_a_profile_touching_the_axis_full_turn(self):
        sk = _sketch(self.body, self.xz, ((0, 0), (5, 0), (5, 10), (0, 10)))  # a side on the sketch's V axis
        f = self.make(sk, axis_mode="V_Axis")
        self.assertEqual(f.TypeId, revolve.JOIN)
        self.assertTrue(revolve.is_own(f))
        self.assertIs(kind_for(f), revolve)
        self._sound(f, math.pi * 25 * 10)

    def test_a_partial_angle_is_exact(self):
        sk = _sketch(self.body, self.xz, ((10, 0), (18, 0), (18, 10), (10, 10)))
        f = self.make(sk, axis_mode="V_Axis", extent="angle", angle="90 °")
        self._sound(f, math.pi * (18 ** 2 - 10 ** 2) * 10 / 4)
        g = revolve.update(self.doc, f, dict(revolve.read(self.doc, f), extent="two_angles", angle="90 °",
                                             angle2="45 °"))
        self.doc.recompute()
        self._sound(g, math.pi * (18 ** 2 - 10 ** 2) * 10 * 135 / 360)

    def test_a_normal_line_as_the_axis_stays_normal(self):
        sk = _sketch(self.body, self.xz, ((0, 0), (10, 0), (10, 20), (0, 20)), lines=[((-5, -5), (-5, 25))])
        f = self.make(sk, axis_mode="construction", axis_construction="Line5")
        self.assertFalse(sk.getConstruction(4))
        self._sound(f, math.pi * (15 ** 2 - 5 ** 2) * 20)

    def test_a_revolve_cut_through_a_block_is_exact(self):
        block = self._block()
        sk = _sketch(self.body, self.xz, ((6, 3), (12, 3), (12, 7), (6, 7)))  # a ring groove round Z, out past the side
        f = self.make(sk, axis_mode="V_Axis", operation="cut")
        self.assertEqual(f.TypeId, revolve.CUT)
        ring = Part.makeCylinder(12, 4, App.Vector(0, 0, 3)).cut(Part.makeCylinder(6, 4, App.Vector(0, 0, 3)))
        expected = block.Shape.Volume - block.Shape.common(ring).Volume
        self._sound(f, expected, places=5)

    def test_a_join_resting_on_a_face_inscribed_in_it_is_one_sound_solid(self):
        """A disc on the block's top, its rim touching the top's four edges (the user's air2 case, by Revolve)."""
        self._block()
        sk = _sketch(self.body, self.xz, ((0, 10), (10, 10), (10, 15), (0, 15)))
        f = self.make(sk, axis_mode="V_Axis")
        self._sound(f, 20 * 20 * 10 + math.pi * 100 * 5)

    def test_a_freecad_revolution_becomes_ours_on_edit(self):
        """A Revolution of an older file: computed as it is, ours when edited, with its label, its parameter, the
        sketch on its face and the feature after it."""
        sk = _sketch(self.body, self.xz, ((10, 0), (18, 0), (18, 10), (10, 10)))
        old = self.body.newObject("PartDesign::Revolution", "Revolution")
        old.Profile = sk
        old.ReferenceAxis = (sk, ["V_Axis"])
        old.Reversed = True
        old.Label = "My turn"
        name = common.value_param(self.doc, old, "Angle", "90 °", "angle")
        self.doc.recompute()
        self.assertIs(kind_for(old), revolve)
        before = old.Shape.copy()
        top = next(i + 1 for i, g in enumerate(old.Shape.Faces)
                   if g.Surface.TypeId == "Part::GeomPlane" and abs(g.CenterOfMass.z - 10) < 1e-6)
        on_top = self.body.newObject("Sketcher::SketchObject", "OnTop")
        on_top.AttachmentSupport = [(old, "Face%d" % top)]
        on_top.MapMode = "FlatFace"
        self.doc.recompute()
        centre = old.Shape.Faces[top - 1].CenterOfMass
        local = on_top.Placement.inverse().multVec(centre)
        on_top.addGeometry(Part.Circle(App.Vector(local.x, local.y, 0), App.Vector(0, 0, 1), 1))
        pad = util.add_pad(self.body, on_top, 2, name="Boss")
        self.doc.recompute()
        v_pad = pad.Shape.Volume

        values = revolve.read(self.doc, old)
        self.assertEqual((values["extent"], values["angle"], values["flip"]), ("angle", "90°", True))
        self.doc.openTransaction("Revolve")
        f = revolve.update(self.doc, old, dict(values, angle="90 °"))
        self.doc.commitTransaction()
        self.doc.recompute()
        self.assertTrue(revolve.is_own(f))
        self.assertEqual(f.Label, "My turn")
        self.assertEqual(model.bound_parameter(f, "Angle"), name)
        self.assertTrue(f.Reversed)
        self.assertAlmostEqual(f.Shape.common(before).Volume, before.Volume, places=6)  # the same place
        self.assertIs(pad.BaseFeature, f)
        self.assertIs(on_top.AttachmentSupport[0][0], f)
        self.assertNotIn("Invalid", pad.State, pad.getStatusString())
        self.assertAlmostEqual(pad.Shape.Volume, v_pad, places=6)
        self.assertIs(self.body.Tip, pad)
        self.assertEqual(self.doc.getObject("Revolution"), None)

        self.doc.undo()  # the conversion is one step: undo brings the Revolution back
        self.doc.recompute()
        back = self.doc.getObject("Revolution")
        self.assertIsNotNone(back)
        self.assertEqual(back.TypeId, "PartDesign::Revolution")
        self.assertIs(pad.BaseFeature, back)
        self.assertAlmostEqual(pad.Shape.Volume, v_pad, places=6)

    def test_a_freecad_groove_becomes_our_cut_in_the_same_place(self):
        self._block()
        sk = _sketch(self.body, self.xz, ((4, 2), (8, 2), (8, 6), (4, 6)))
        old = self.body.newObject("PartDesign::Groove", "Groove")
        old.Profile = sk
        old.ReferenceAxis = (sk, ["V_Axis"])
        old.Type = "TwoAngles"
        old.Angle, old.Angle2 = 60, 30
        self.doc.recompute()
        before = old.Shape.Volume
        f = revolve.update(self.doc, old, revolve.read(self.doc, old))
        self.doc.recompute()
        self.assertEqual(f.TypeId, revolve.CUT)
        self._sound(f, before)

    def test_a_pattern_of_our_revolve(self):
        """Three half holes in the block's front face: the revolve cut's tool repeated."""
        from incad.features import extrude
        base = _sketch(self.body, util.xy_plane(self.body), ((0, 0), (20, 0), (20, 10), (0, 10)), name="Base")
        extrude.create(self.doc, self.body, base, dict(extrude.defaults(self.doc, self.body, base), operation="join",
                                                       distance="10 mm"))
        self.doc.recompute()
        sk = _sketch(self.body, self.xz, ((3, 7), (4, 7), (4, 10), (3, 10)), construction=[((3, 0), (3, 12))])
        cut = self.make(sk, axis_mode="construction", axis_construction="Axis0", operation="cut")
        self._sound(cut, 2000 - math.pi * 3 / 2)
        k = patterns.RECT
        f = k.create(self.doc, self.body, None, dict(k.defaults(self.doc, self.body, None),
                                                     features=common.format_picks([(cut, "")]), count1="3",
                                                     spacing1="6 mm"))
        self.doc.recompute()
        self.assertTrue(own_pattern.is_own(f))
        self._sound(f, 2000 - 3 * math.pi * 3 / 2)

    def test_the_axis_crossing_the_profile_is_said_plainly(self):
        sk = _sketch(self.body, self.xz, ((-5, 0), (5, 0), (5, 10), (-5, 10)))
        f = self.make(sk, axis_mode="V_Axis")
        self.assertIn("Invalid", f.State)
        self.assertIn("crosses the profile", f.getStatusString())


class OwnRevolveThreadTest(unittest.TestCase):
    """The user's p2_test: a slot over a tapped Hole's rim revolved about the sketch's V axis goes before the thread
    (incad.core.thread_order), our own Revolve as FreeCAD's did."""

    def test_a_revolve_through_a_tapped_hole_goes_before_it(self):
        import os
        import shutil
        import tempfile
        from incad.core import thread_order
        data = os.path.join(os.path.dirname(__file__), "..", "gui", "data", "p2_test.FCStd")
        for operation, more in (("cut", False), ("join", True)):
            tmp = tempfile.mkdtemp()
            path = os.path.join(tmp, "p2_test.FCStd")
            shutil.copy(os.path.abspath(data), path)
            doc = App.openDocument(path)
            try:
                body, sk = doc.Body, doc.Sketch004
                before = body.Tip.Shape.Volume
                f = revolve._new(body, operation)
                f.Profile = sk
                f.ReferenceAxis = (sk, ["V_Axis"])
                doc.recompute()
                self.assertIs(thread_order.crossed_thread(f), doc.Hole)
                self.assertEqual(thread_order.settle(f), "Hole")
                doc.recompute()
                group = list(body.Group)
                self.assertEqual(group.index(f) + 1, group.index(doc.Hole))
                self.assertFalse([o.Name for o in body.Group if "Invalid" in o.State])
                tip = body.Tip.Shape
                self.assertTrue(tip.isValid())
                if more:
                    self.assertGreater(tip.Volume, before + 100)
                else:
                    self.assertLess(tip.Volume, before - 100)
            finally:
                App.closeDocument(doc.Name)
                shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
