import math
import unittest

import FreeCAD as App
import Part
from incad.core import document, model
from incad.features import extrude, kind_for
from incad.features.common import closed_profile, unconsumed_closed_sketches
from headless import util


class ExtrudeTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("E")
        self.body = document.make_part(self.doc)
        self.sk = util.add_rect_sketch(self.body)  # 20 x 10 on XY
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def make(self, **changes):
        values = extrude.defaults(self.doc, self.body, self.sk)
        values.update(changes)
        f = extrude.create(self.doc, self.body, self.sk, values)
        self.doc.recompute()
        return f

    def test_profile_helpers(self):
        self.assertTrue(closed_profile(self.sk))
        self.assertEqual(unconsumed_closed_sketches(self.body), [self.sk])

    def test_defaults_and_join(self):
        v = extrude.defaults(self.doc, self.body, self.sk)
        self.assertEqual((v["operation"], v["extent"], v["direction"], v["distance"]), ("join", "distance", "default", "10 mm"))
        f = self.make()
        self.assertEqual(f.TypeId, extrude.JOIN)
        self.assertAlmostEqual(f.Shape.Volume, 20 * 10 * 10, places=3)
        self.assertEqual(model.bound_parameter(f, "Length"), "d0")
        self.assertIs(kind_for(f), extrude)
        self.assertEqual(unconsumed_closed_sketches(self.body), [])

    def test_read_back(self):
        f = self.make(distance="25 mm", direction="symmetric", taper="5")
        v = extrude.read(self.doc, f)
        self.assertEqual((v["distance"], v["direction"], v["taper"], v["operation"]), ("25 mm", "symmetric", "5°", "join"))

    def test_read_back_a_pad_made_outside_the_dialog(self):
        """The user's jar: a Pad made by a script showed Distance 10 mm (the dialog's default) for its 78 mm."""
        pad = util.add_pad(self.body, self.sk, 78, "Plain")
        pad.TaperAngle = 3
        self.doc.recompute()
        v = extrude.read(self.doc, pad)
        self.assertEqual((v["distance"], v["taper"]), ("78 mm", "3 °"))

    def test_update_values_and_formulas(self):
        f = self.make()
        document.set_parameter(self.doc, "Width", "40 mm")
        f = extrude.update(self.doc, f, dict(extrude.read(self.doc, f), distance="Width / 2"))
        self.doc.recompute()
        self.assertAlmostEqual(f.Length.Value, 20)
        self.assertEqual(extrude.read(self.doc, f)["distance"], "Width / 2")

    def test_join_to_cut_keeps_parameter_and_place(self):
        base = self.make(distance="10 mm")
        top = [i + 1 for i, fc in enumerate(base.Shape.Faces) if abs(fc.CenterOfMass.z - 10) < 1e-6][0]
        sk2 = self.body.newObject("Sketcher::SketchObject", "Hole")
        sk2.AttachmentSupport = [(base, "Face%d" % top)]
        sk2.MapMode = "FlatFace"
        sk2.addGeometry(Part.Circle(App.Vector(10, 5, 0), App.Vector(0, 0, 1), 2))
        self.doc.recompute()
        f = extrude.create(self.doc, self.body, sk2, dict(extrude.defaults(self.doc, self.body, sk2), distance="3 mm"))
        self.doc.recompute()
        name = model.bound_parameter(f, "Length")
        g = extrude.update(self.doc, f, dict(extrude.read(self.doc, f), operation="cut"))
        self.doc.recompute()
        self.assertEqual(g.TypeId, extrude.CUT)
        self.assertEqual(model.bound_parameter(g, "Length"), name)
        self.assertIs(self.body.Tip, g)
        self.assertLess(g.Shape.Volume, base.Shape.Volume)

    def test_join_cut_switch_mid_history_keeps_later_features(self):
        base = self.make(distance="10 mm")
        later_sk = util.add_rect_sketch(self.body, w=4, h=4, name="Later")
        later = extrude.create(self.doc, self.body, later_sk,
                               dict(extrude.defaults(self.doc, self.body, later_sk), distance="20 mm"))
        self.doc.recompute()
        g = extrude.update(self.doc, base, dict(extrude.read(self.doc, base), operation="cut"))
        self.doc.recompute()
        self.assertIs(later.BaseFeature, g)
        self.assertIs(self.body.Tip, later)
        self.assertFalse(g.Visibility)

    def test_join_cut_switch_refused_when_a_sketch_sits_on_its_face(self):
        base = self.make(distance="10 mm")
        top = [i + 1 for i, fc in enumerate(base.Shape.Faces) if abs(fc.CenterOfMass.z - 10) < 1e-6][0]
        sk2 = self.body.newObject("Sketcher::SketchObject", "OnTop")
        sk2.AttachmentSupport = [(base, "Face%d" % top)]
        sk2.MapMode = "FlatFace"
        sk2.addGeometry(Part.Circle(App.Vector(10, 5, 0), App.Vector(0, 0, 1), 2))
        self.doc.recompute()
        with self.assertRaises(ValueError) as ctx:
            extrude.update(self.doc, base, dict(extrude.read(self.doc, base), operation="cut"))
        self.assertIn("OnTop", str(ctx.exception))
        self.assertEqual(sk2.AttachmentSupport[0][0], base)
        self.assertEqual(base.TypeId, extrude.JOIN)

    def test_up_to_a_work_plane(self):
        plane = self.body.newObject("PartDesign::Plane", "Limit")
        plane.AttachmentSupport = [(util.xy_plane(self.body), "")]
        plane.MapMode = "FlatFace"
        plane.AttachmentOffset = App.Placement(App.Vector(0, 0, 7), App.Rotation())
        self.doc.recompute()
        f = self.make(extent="to", to_face=plane.Name)
        self.assertAlmostEqual(f.Shape.Volume, 20 * 10 * 7, places=3)
        self.assertEqual(extrude.read(self.doc, f)["to_face"], plane.Name)

    def test_through_all_cut(self):
        base = self.make(distance="10 mm")
        sk2 = util.add_rect_sketch(self.body, w=4, h=4, name="Cutter")
        self.doc.recompute()
        f = extrude.create(self.doc, self.body, sk2, dict(extrude.defaults(self.doc, self.body, sk2),
                                                          operation="cut", extent="through_all", direction="symmetric"))
        self.doc.recompute()
        self.assertEqual((f.TypeId, f.Type), (extrude.CUT, "ThroughAll"))
        self.assertAlmostEqual(f.Shape.Volume, 20 * 10 * 10 - 4 * 4 * 10, places=3)

    def test_two_sides(self):
        f = self.make(direction="two_sides", distance="10 mm", distance2="5 mm")
        self.assertEqual(f.SideType, "Two sides")
        self.assertAlmostEqual(f.Shape.Volume, 20 * 10 * 15, places=3)

    def test_a_join_that_does_not_touch_the_part_becomes_its_own_solid(self):
        """A box drawn away from the part and extruded as Join would leave the body with two separate solids
        (nothing after it can be shelled): it goes into a new Solid by itself, and reads back as New solid."""
        base = self.make()
        far = util.add_rect_sketch(self.body, name="Far")
        far.AttachmentOffset = App.Placement(App.Vector(50, 50, 0), App.Rotation())
        self.doc.recompute()
        f = extrude.create(self.doc, self.body, far, extrude.defaults(self.doc, self.body, far))
        self.doc.recompute()
        other = f.getParentGeoFeatureGroup()
        self.assertIsNot(other, self.body, "its own Solid")
        self.assertIs(document.primary_body(self.doc), self.body)
        self.assertEqual(len(document.bodies(self.doc)), 2)
        self.assertIs(far.getParentGeoFeatureGroup(), other, "the sketch moved with it")
        self.assertEqual(len(self.body.Tip.Shape.Solids), 1)
        self.assertIs(self.body.Tip, base)
        self.assertEqual(extrude.read(self.doc, f)["operation"], "new_solid")
        touching = util.add_rect_sketch(self.body, name="Near")
        touching.AttachmentOffset = App.Placement(App.Vector(10, 5, 10), App.Rotation())  # on the pad's top
        self.doc.recompute()
        g = extrude.create(self.doc, self.body, touching, extrude.defaults(self.doc, self.body, touching))
        self.doc.recompute()
        self.assertIs(g.getParentGeoFeatureGroup(), self.body, "a join that touches stays in the part")

    def test_a_disc_resting_on_the_top_joins_sound(self):
        """A circle extruded so it only touches the block's top along a line (the user's air: a disc on a block)
        made a faulty solid, and every later feature failed on it. The own Extrude keeps it exact and sound: the disc
        and the block touch only along a line, so they stay two solids of the part (the user is told), each sound."""
        import Sketcher
        from incad.features.common import profile_of
        self.make()
        disc = self.body.newObject("Sketcher::SketchObject", "Disc")
        disc.Placement = App.Placement(App.Vector(0, 5, 0), App.Rotation(App.Vector(1, 0, 0), 90))
        disc.addGeometry(Part.Circle(App.Vector(10, 15, 0), App.Vector(0, 0, 1), 5))
        disc.addConstraint(Sketcher.Constraint("Radius", 0, 5))
        self.doc.recompute()
        f = extrude.create(self.doc, self.body, disc, dict(
            extrude.defaults(self.doc, self.body, disc), operation="join", distance="4 mm", direction="symmetric"))
        self.doc.recompute()
        self.assertTrue(f.Shape.isValid())
        f.Shape.check(True)  # raises on a faulty solid
        self.assertAlmostEqual(f.Shape.Volume, 2000 + 3.141592653589793 * 25 * 4, places=6)
        self.assertIs(profile_of(f), disc)
        disc.setDatum(0, App.Units.Quantity("6 mm"))  # a bigger disc reaches into the block: one solid, still sound
        self.doc.recompute()
        self.assertAlmostEqual(f.Shape.BoundBox.ZMax, 15 + 6, delta=0.01)
        self.assertEqual(len(f.Shape.Solids), 1)
        f.Shape.check(True)

    def _on_top_away(self, name="Away", project=False):
        """A sketch on the part's top face whose 4 x 4 square lies off the part (the user's D-shape on the hex)."""
        base = self.body.Tip
        top = [i + 1 for i, f in enumerate(base.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6][0]
        sk = self.body.newObject("Sketcher::SketchObject", name)
        sk.AttachmentSupport = [(base, "Face%d" % top)]
        sk.MapMode = "FlatFace"
        self.doc.recompute()
        c = sk.getGlobalPlacement().inverse().multVec(App.Vector(40, 3, 10))  # the face's own frame
        pts = [c + App.Vector(dx, dy, 0) for dx, dy in ((0, 0), (4, 0), (4, 4), (0, 4))]
        for i in range(4):
            sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
        if project:
            edge = [i + 1 for i, e in enumerate(base.Shape.Edges)
                    if all(abs(v.Z - 10) < 1e-6 for v in e.Vertexes)][0]
            sk.addExternal(base.Name, "Edge%d" % edge)
        self.doc.recompute()
        return sk

    def test_through_all_with_a_taper_goes_through_and_follows(self):
        """Through All runs the whole box diagonal: an inward taper closed the profile ("values too large")."""
        base = self.make()  # 20 x 10 x 10
        hole = self.body.newObject("Sketcher::SketchObject", "Sq")
        hole.AttachmentSupport = [(util.xy_plane(self.body), "")]
        hole.MapMode = "FlatFace"
        hole.AttachmentOffset = App.Placement(App.Vector(0, 0, 10), App.Rotation())
        p = [App.Vector(6, 1, 0), App.Vector(14, 1, 0), App.Vector(14, 9, 0), App.Vector(6, 9, 0)]
        for i in range(4):
            hole.addGeometry(Part.LineSegment(p[i], p[(i + 1) % 4]))
        self.doc.recompute()
        f = extrude.create(self.doc, self.body, hole, dict(extrude.defaults(self.doc, self.body, hole),
                                                            operation="cut", extent="through_all", taper="-5 °"))
        self.doc.recompute()
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        self.assertEqual(extrude.read(self.doc, f)["extent"], "through_all")
        self.assertLess(self.body.Tip.Shape.Volume, 2000 - 60)
        base.Length = 20
        hole.AttachmentOffset = App.Placement(App.Vector(0, 0, 20), App.Rotation())
        self.doc.recompute()
        self.assertEqual(extrude.follow_reach(self.doc), [f.Label])
        self.doc.recompute()
        self.assertAlmostEqual(f.Length.Value, 21, places=6)

    def test_a_join_partly_on_the_part_stays_in_the_part(self):
        """One square on the part and one beside it: the whole extrude went into a new Solid of two lumps."""
        self.make()
        two = self.body.newObject("Sketcher::SketchObject", "Two")
        two.AttachmentSupport = [(util.xy_plane(self.body), "")]
        two.MapMode = "FlatFace"
        two.AttachmentOffset = App.Placement(App.Vector(0, 0, 10), App.Rotation())
        for x0 in (2, 40):
            p = [App.Vector(x0, 2, 0), App.Vector(x0 + 4, 2, 0), App.Vector(x0 + 4, 6, 0), App.Vector(x0, 6, 0)]
            for i in range(4):
                two.addGeometry(Part.LineSegment(p[i], p[(i + 1) % 4]))
        self.doc.recompute()
        f = extrude.create(self.doc, self.body, two, dict(extrude.defaults(self.doc, self.body, two),
                                                           operation="join", distance="3 mm"))
        self.doc.recompute()
        self.assertIs(f.getParentGeoFeatureGroup(), self.body)
        self.assertEqual(len(document.bodies(self.doc)), 1)

    def test_a_sketch_on_the_parts_face_extruded_off_it_is_an_independent_solid(self):
        """The user: a D-shape sketched on the hex's face, off the hex, extruded: the new Solid held the hex too
        (a Pad first in a Solid takes its sketch's support as its base), Make Assembly then failed. Nothing
        projected from the part: the face was only a plane, the new Solid is independent."""
        base = self.make()
        sk = self._on_top_away()
        at = App.Placement(sk.getGlobalPlacement())
        f = extrude.create(self.doc, self.body, sk, dict(extrude.defaults(self.doc, self.body, sk), distance="5 mm"))
        self.doc.recompute()
        other = f.getParentGeoFeatureGroup()
        self.assertIsNot(other, self.body)
        self.assertEqual(len(f.Shape.Solids), 1)
        self.assertAlmostEqual(f.Shape.Volume, 4 * 4 * 5, places=3)
        self.assertTrue(sk.getGlobalPlacement().isSame(at, 1e-7), "the sketch stays where it was drawn")
        self.assertFalse(sk.AttachmentSupport, "no link to the other Solid")
        self.assertTrue(base.Visibility, "the part stays shown")
        model.set_model_parameter(self.doc, model.bound_parameter(base, "Length"), "30 mm")
        self.doc.recompute()
        self.assertTrue(sk.getGlobalPlacement().isSame(at, 1e-7), "independent: the part's change doesn't move it")
        self.assertAlmostEqual(f.Shape.Volume, 4 * 4 * 5, places=3)

    def test_with_a_projection_from_the_part_it_follows_the_face(self):
        base = self.make()
        sk = self._on_top_away(project=True)
        f = extrude.create(self.doc, self.body, sk, dict(extrude.defaults(self.doc, self.body, sk), distance="5 mm"))
        self.doc.recompute()
        other = f.getParentGeoFeatureGroup()
        self.assertIsNot(other, self.body)
        self.assertEqual(len(f.Shape.Solids), 1)
        self.assertAlmostEqual(f.Shape.Volume, 4 * 4 * 5, places=3)
        z0 = sk.getGlobalPlacement().Base.z
        model.set_model_parameter(self.doc, model.bound_parameter(base, "Length"), "30 mm")
        self.doc.recompute()
        self.assertAlmostEqual(sk.getGlobalPlacement().Base.z, z0 + 20, places=6)  # follows the face
        self.assertAlmostEqual(f.Shape.Volume, 4 * 4 * 5, places=3)
        self.assertEqual(len(f.Shape.Solids), 1)

    def test_new_solid_on_the_parts_face_holds_only_its_own_piece(self):
        self.make()
        sk = self._on_top_away()
        f = extrude.create(self.doc, self.body, sk, dict(extrude.defaults(self.doc, self.body, sk),
                                                         operation="new_solid", distance="5 mm"))
        self.doc.recompute()
        self.assertAlmostEqual(f.Shape.Volume, 4 * 4 * 5, places=3)

    def test_an_old_file_with_a_solid_on_another_solids_face_is_repaired(self):
        """What older InCad made: the sketch moved into the new Solid still attached to the part's face, so the
        new Solid held the whole part as well."""
        base = self.make()
        sk = self._on_top_away()
        other = document.new_solid(self.doc)
        self.body.removeObject(sk)
        other.addObject(sk)
        pad = util.add_pad(other, sk, 5, name="OldPad")
        other.Tip = pad
        self.doc.recompute()
        self.assertGreater(pad.Shape.Volume, 4 * 4 * 5 + 1, "the bug: the part is in the new Solid too")
        # the user's file: a cut sketched on a face of that piece — its face number changes once the part goes
        far_side = [i + 1 for i, f in enumerate(pad.Shape.Faces)
                    if f.Surface.TypeId == "Part::GeomPlane" and abs(f.CenterOfMass.x - 44) < 1e-6][0]
        cut_sk = other.newObject("Sketcher::SketchObject", "CutSk")
        cut_sk.AttachmentSupport = [(pad, "Face%d" % far_side)]
        cut_sk.MapMode = "FlatFace"
        self.doc.recompute()
        c = cut_sk.getGlobalPlacement().inverse().multVec(App.Vector(44, 5, 12))
        pts = [c + App.Vector(dx, dy, 0) for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        for i in range(4):
            cut_sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
        cut = other.newObject("PartDesign::Pocket", "Cut")
        cut.Profile = cut_sk
        cut.Length = 1
        other.Tip = cut
        self.doc.recompute()
        self.assertEqual([o.Label for o in self.doc.Objects if "Invalid" in o.State], [])
        base.Visibility = False  # FreeCAD hid the part's step: the old Pad took it as its base
        self.assertTrue(document.settle_cross_solid_sketches(self.doc))
        self.doc.recompute()
        self.assertAlmostEqual(pad.Shape.Volume, 4 * 4 * 5, places=3)
        self.assertEqual([o.Label for o in self.doc.Objects if "Invalid" in o.State], [], "the cut's face found again")
        self.assertAlmostEqual(other.Shape.Volume, 4 * 4 * 5 - 2 * 2 * 1, places=3)
        self.assertAlmostEqual(base.Shape.Volume, 20 * 10 * 10, places=3)
        self.assertTrue(base.Visibility, "the part is shown again (the user saw only the new Solid)")
        self.assertTrue(cut.Visibility)
        self.assertFalse(document.settle_cross_solid_sketches(self.doc), "once")

    def test_new_solid_moves_the_sketch(self):
        f = self.make(operation="new_solid")
        self.assertEqual(len(document.bodies(self.doc)), 2)
        self.assertIsNot(f.getParentGeoFeatureGroup(), self.body)
        self.assertIs(self.sk.getParentGeoFeatureGroup(), f.getParentGeoFeatureGroup())

    def test_intersect(self):
        self.make(distance="10 mm")
        sk2 = util.add_rect_sketch(self.body, w=5, h=5, name="Cross")
        self.doc.recompute()
        f = extrude.create(self.doc, self.body, sk2, dict(extrude.defaults(self.doc, self.body, sk2),
                                                          operation="intersect", distance="30 mm"))
        self.doc.recompute()
        self.assertEqual(f.TypeId, "PartDesign::Boolean")
        self.assertAlmostEqual(self.body.Shape.Volume, 5 * 5 * 10, places=3)

    def test_default_operation_is_cut_inside_material(self):
        self.make(distance="10 mm")
        mid = self.body.newObject("Sketcher::SketchObject", "Mid")
        mid.AttachmentSupport = [(util.xy_plane(self.body), "")]
        mid.MapMode = "FlatFace"
        mid.AttachmentOffset = App.Placement(App.Vector(0, 0, 5), App.Rotation())
        mid.addGeometry(Part.Circle(App.Vector(10, 5, 0), App.Vector(0, 0, 1), 2))
        self.doc.recompute()
        self.assertEqual(extrude.defaults(self.doc, self.body, mid)["operation"], "cut")


class ToNextAndToFaceTest(unittest.TestCase):
    """The user: a new sketch would not extrude To / To Next. To Next searched away from the part (FreeCAD: 'No
    faces found in this direction'); To took only flat faces."""

    def setUp(self):
        self.doc = util.new_doc("Tn")
        self.body, self.pad = util.padded_part(self.doc, 10, incad=True)  # 20 x 10 x 10
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def sketch_at(self, z):
        sk = util.add_rect_sketch(self.body, w=4, h=4, name="S")
        sk.AttachmentOffset = App.Placement(App.Vector(2, 2, z), App.Rotation())
        self.doc.recompute()
        return sk

    def make(self, sk, **changes):
        values = extrude.defaults(self.doc, self.body, sk)
        values.update(changes)
        f = extrude.create(self.doc, self.body, sk, values)
        self.doc.recompute()
        return f

    def test_to_next_goes_toward_the_part(self):
        f = self.make(self.sketch_at(20), extent="to_next")
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        self.assertAlmostEqual(self.body.Shape.Volume, 2000 + 4 * 4 * 10, places=6)

    def test_to_next_keeps_a_chosen_direction(self):
        f = self.make(self.sketch_at(20), extent="to_next", direction="flip")
        self.assertTrue(f.Reversed)
        self.assertNotIn("Invalid", f.State, f.getStatusString())

    def test_to_takes_a_curved_face(self):
        from incad.features import common
        cyl = self.body.newObject("PartDesign::AdditiveCylinder", "Post")
        cyl.Radius, cyl.Height = 30, 5
        cyl.Placement = App.Placement(App.Vector(10, 5, -30), App.Rotation(App.Vector(1, 0, 0), 90))
        self.body.Tip = cyl
        self.doc.recompute()
        face = next(i + 1 for i, fc in enumerate(cyl.Shape.Faces) if fc.Surface.TypeId == "Part::GeomCylinder")
        self.assertTrue(common.accepts("to_face", cyl, "Face%d" % face))
        self.assertTrue(common.accepts("to_face", [o for o in self.body.Origin.OriginFeatures
                                                   if o.Role == "XY_Plane"][0], ""))


class SketchOnAnotherSolidTest(unittest.TestCase):
    """A sketch left in one Solid but drawn on a face of another (a piece moved after the sketch was made, or the
    sketch stayed behind when its feature was deleted): the feature goes to the Solid that owns the face, the
    sketch with it, and a Solid emptied this way is removed. On 2026-10-10 a Cut on such a sketch became a
    fourth, detached Solid instead of cutting the box."""

    def setUp(self):
        self.doc = util.new_doc("E2")
        self.body = document.make_part(self.doc)
        self.pad = util.add_pad(self.body, util.add_rect_sketch(self.body), 10)
        self.body.Tip = self.pad
        far = util.add_rect_sketch(self.body, name="Far")
        far.AttachmentOffset = App.Placement(App.Vector(50, 50, 0), App.Rotation())
        self.doc.recompute()
        self.box = extrude.create(self.doc, self.body, far, extrude.defaults(self.doc, self.body, far))
        self.doc.recompute()
        self.other = self.box.getParentGeoFeatureGroup()
        self.stray = document.new_solid(self.doc)  # the Solid the sketch was left in
        top = [i + 1 for i, f in enumerate(self.box.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6][0]
        self.sk = self.stray.newObject("Sketcher::SketchObject", "OnBox")
        self.sk.AttachmentSupport = [(self.box, "Face%d" % top)]
        self.sk.MapMode = "FlatFace"
        self.doc.recompute()
        centre = self.sk.Placement.inverse().multVec(self.box.Shape.Faces[top - 1].CenterOfMass)
        self.sk.addGeometry(Part.Circle(App.Vector(centre.x, centre.y, 0), App.Vector(0, 0, 1), 2))
        self.doc.recompute()
        self.assertEqual(len(document.bodies(self.doc)), 3)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_target_body_is_the_solid_owning_the_face(self):
        self.assertIs(document.target_body(self.sk), self.other)
        plain = self.stray.newObject("Sketcher::SketchObject", "Plain")
        plain.AttachmentSupport = [(util.xy_plane(self.stray), "")]
        plain.MapMode = "FlatFace"
        self.doc.recompute()
        self.assertIs(document.target_body(plain), self.stray, "a sketch on a plane stays where it is")
        self.assertIs(document.target_body(self.pad), self.body)

    def test_adopt_moves_the_sketch_and_removes_the_emptied_solid(self):
        name = self.stray.Name
        document.adopt(self.other, self.sk)
        self.assertIs(self.sk.getParentGeoFeatureGroup(), self.other)
        self.assertEqual(len(document.bodies(self.doc)), 2)
        self.assertIsNone(self.doc.getObject(name), "the emptied Solid is gone")
        self.assertEqual([o.Name for o in self.doc.Objects if o.TypeId == "App::Origin" and not o.InList], [],
                         "and its origin with it")
        document.adopt(self.other, self.sk)  # already there: nothing happens
        self.assertEqual(len(document.bodies(self.doc)), 2)

    def test_adopt_keeps_a_solid_that_still_holds_something(self):
        keep = self.stray.newObject("Sketcher::SketchObject", "Keep")
        document.adopt(self.other, self.sk)
        self.assertIs(keep.getParentGeoFeatureGroup(), self.stray)
        self.assertEqual(len(document.bodies(self.doc)), 3)

    def test_a_cut_on_it_cuts_the_box(self):
        body = document.target_body(self.sk)
        document.adopt(body, self.sk)
        values = extrude.defaults(self.doc, body, self.sk)
        values.update(operation="cut", distance="5 mm")
        f = extrude.create(self.doc, body, self.sk, values)
        self.doc.recompute()
        self.assertIs(f.getParentGeoFeatureGroup(), self.other)
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        self.assertLess(f.Shape.Volume, self.box.Shape.Volume - 1)
        self.assertEqual(len(document.bodies(self.doc)), 2)
        self.assertAlmostEqual(self.body.Tip.Shape.Volume, 2000, places=6, msg="the part is untouched")


class OwnExtrudeTest(unittest.TestCase):
    """ProstoCAD's own Extrude on OpenCASCADE (the user's law: "the extrude just needs to work, period"). A circle
    sketched on a block's face, inscribed in it, extruded out as a Join: OCC's fuse lost half the volume (the user's
    air2); sewn on the face it is exact and sound."""

    def setUp(self):
        self.doc = util.new_doc("Own")
        self.body = document.make_part(self.doc)
        self.sk = util.add_rect_sketch(self.body, w=40, h=30)
        self.doc.recompute()
        self.block = extrude.create(self.doc, self.body, self.sk, dict(
            extrude.defaults(self.doc, self.body, self.sk), operation="join", distance="10 mm"))
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def on_face(self, test, name="On"):
        """A sketch on the block's face `test` picks, its frame's origin at the face's own placement."""
        base = self.body.Tip
        i = next(i for i, f in enumerate(base.Shape.Faces) if f.Surface.TypeId == "Part::GeomPlane" and test(f))
        sk = self.body.newObject("Sketcher::SketchObject", name)
        sk.AttachmentSupport = [(base, "Face%d" % (i + 1))]
        sk.MapMode = "FlatFace"
        self.doc.recompute()
        return sk

    def circle(self, sk, centre, r):
        local = sk.getGlobalPlacement().inverse().multVec(centre)
        sk.addGeometry(Part.Circle(App.Vector(local.x, local.y, 0), App.Vector(0, 0, 1), r))
        self.doc.recompute()

    def extrude(self, sk, **values):
        f = extrude.create(self.doc, self.body, sk, dict(extrude.defaults(self.doc, self.body, sk), **values))
        self.doc.recompute()
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        return f

    def sound(self, shape, volume, solids=1):
        self.assertTrue(shape.isValid())
        shape.check(True)  # raises on a faulty solid (faces crossing themselves)
        self.assertEqual(len(shape.Solids), solids)
        self.assertAlmostEqual(shape.Volume, volume, delta=1e-6 * volume)

    def shell_on(self, f):
        from incad.features import dressup
        bottom = next(i + 1 for i, g in enumerate(f.Shape.Faces)
                      if g.Surface.TypeId == "Part::GeomPlane" and abs(g.CenterOfMass.z) < 1e-6)
        k = dressup.SHELL
        s = k.create(self.doc, self.body, None, dict(k.defaults(self.doc, self.body, None),
                                                     faces="%s:Face%d" % (f.Name, bottom), thickness="1 mm"))
        self.doc.recompute()
        self.assertNotIn("Invalid", s.State, s.getStatusString())
        self.assertTrue(s.Shape.isValid())
        self.assertEqual(len(s.Shape.Solids), 1)
        return s

    def test_an_inscribed_disc_joins_exactly(self):
        """The user's air2: the circle on the block's end face touching its top and bottom edges, 10 mm out."""
        sk = self.on_face(lambda g: abs(g.CenterOfMass.x - 40) < 1e-6)
        self.circle(sk, App.Vector(40, 15, 5), 5)
        f = self.extrude(sk, operation="join", distance="10 mm")
        self.assertEqual(f.TypeId, extrude.JOIN)
        self.assertEqual(f.InCadExtrude, "sewn on the face")
        self.sound(f.Shape, 12000 + math.pi * 25 * 10)
        self.assertGreater(self.shell_on(f).Shape.Volume, 100)

    def test_a_disc_inscribed_in_the_top_joins_and_cuts_soundly(self):
        sk = self.on_face(lambda g: abs(g.CenterOfMass.z - 10) < 1e-6)
        self.circle(sk, App.Vector(20, 15, 10), 15)  # touches both long sides of the top
        f = self.extrude(sk, operation="join", distance="10 mm")
        self.sound(f.Shape, 12000 + math.pi * 225 * 10)
        f = extrude.update(self.doc, f, dict(extrude.read(self.doc, f), operation="cut", distance="5 mm"))
        self.doc.recompute()  # the finder: a cut part-way into it was faulty, and the Shell after it
        self.assertEqual(f.TypeId, extrude.CUT)
        self.sound(f.Shape, 12000 - math.pi * 225 * 5)
        s = self.shell_on(f)
        s.Shape.check(True)

    def test_a_join_against_a_taller_wall_and_a_symmetric_cut(self):
        """A block with a step on half its top: a rectangle on the low half against the step's wall, joined higher
        than the step, came out faulty (the tool's wall and the step's lay on one another, both kept), and the Shell
        after it too. A disc inscribed in the top, cut Symmetric: exact, but Refine merged its round wall past the
        lines where it touches the sides (faulty)."""
        def rect(sk, x0, y0, x1, y1, z):
            local = sk.getGlobalPlacement().inverse()
            pts = [local.multVec(App.Vector(x, y, z)) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
            for i in range(4):
                sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
            self.doc.recompute()
        sk = self.on_face(lambda g: abs(g.CenterOfMass.z - 10) < 1e-6, "Step")
        rect(sk, 20, 0, 40, 30, 10)
        self.extrude(sk, operation="join", distance="10 mm")
        sk = self.on_face(lambda g: abs(g.CenterOfMass.z - 10) < 1e-6 and g.BoundBox.ZMax < 10 + 1e-6, "Post")
        rect(sk, 10, 10, 20, 20, 10)
        f = self.extrude(sk, operation="join", distance="15 mm")
        self.sound(f.Shape, 12000 + 6000 + 1500)
        self.shell_on(f).Shape.check(True)
        App.closeDocument(self.doc.Name)
        self.setUp()
        sk = self.on_face(lambda g: abs(g.CenterOfMass.z - 10) < 1e-6)
        self.circle(sk, App.Vector(20, 15, 10), 15)
        f = self.extrude(sk, operation="cut", distance="6 mm", direction="symmetric")
        self.sound(f.Shape, 12000 - math.pi * 225 * 3)

    def test_a_faulty_part_is_healed_for_a_symmetric_join_and_a_cut(self):
        """The user's air2 (a STEP's arcs written as splines that stray off their faces): a disc inscribed in its
        face, Join 5 and Cut 1 came out faulty and the Shell after them too, Join Symmetric 10 was refused. The part
        is healed first; Symmetric drops its half inside the part and is sewn on the face."""
        from incad.core import extrude_solid, heal
        box = Part.makeBox(40, 30, 10)  # its top edge at y = 30 a spline sagging 0.5 µm, its tolerance as written
        pts = [App.Vector(40 * k / 8.0, 30, 10 - 5e-4 * math.sin(math.pi * k / 8.0)) for k in range(9)]
        spline = Part.BSplineCurve()
        spline.interpolate(pts)
        faces = []
        for g in box.Faces:
            hit = [e for e in g.Edges if abs(e.BoundBox.YMin - 30) < 1e-9 and abs(e.BoundBox.ZMin - 10) < 1e-9]
            edges = [spline.toShape() if hit and e.isSame(hit[0]) else e for e in g.OuterWire.Edges]
            faces.append(Part.Face(g.Surface, Part.Wire(Part.__sortEdges__(edges))) if hit else g.copy())
        sewn = Part.Compound(faces)
        sewn.sewShape(1e-3)
        base = Part.Solid(sewn.Shells[0])
        for e in base.Edges:
            if type(e.Curve).__name__ == "BSplineCurve":
                e.fixTolerance(1e-7, Part.Edge)
        self.assertTrue(heal.faulty(base))
        disc = [Part.Face(Part.Wire(Part.makeCircle(15, App.Vector(20, 15, 10))))]
        up, down, at = App.Vector(0, 0, 1), App.Vector(0, 0, -1), App.Vector(20, 15, 10)
        sym = extrude_solid.tool(disc, at, [(up, "length", 5.0, 0.0, base, None), (down, "length", 5.0, 0.0, base, None)])
        shape, how = extrude_solid.combine(base, sym, "join", disc, at, up, True)
        self.assertEqual(how, "sewn on the face")
        self.sound(shape, 12000 + math.pi * 225 * 5)
        shape, how = extrude_solid.combine(base, extrude_solid.prism(disc, down, 1.0), "cut", disc, at, down, True)
        self.sound(shape, 12000 - math.pi * 225 * 1)

    def test_a_plain_pad_and_pocket(self):
        sk = self.on_face(lambda g: abs(g.CenterOfMass.z - 10) < 1e-6)
        local = sk.getGlobalPlacement().inverse()
        pts = [local.multVec(App.Vector(x, y, 10)) for x, y in ((5, 5), (15, 5), (15, 15), (5, 15))]
        for i in range(4):
            sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
        self.doc.recompute()
        f = self.extrude(sk, operation="join", distance="5 mm")
        self.sound(f.Shape, 12000 + 500)
        g = extrude.update(self.doc, f, dict(extrude.read(self.doc, f), operation="cut"))
        self.doc.recompute()  # (a Cut goes into the part unless flipped, as the add-on's Pocket always did)
        self.sound(g.Shape, 12000 - 500)
        self.assertEqual(extrude.read(self.doc, g)["direction"], "default")

    def test_a_cut_tangent_to_a_side_is_exact_and_said(self):
        """A circle tangent to the block's end, cut 5 deep, leaves a wall of no thickness there: the result is
        exact and valid, and the user is told (the finder: kept without a word)."""
        from incad.core import safe
        sk = self.on_face(lambda g: abs(g.CenterOfMass.z - 10) < 1e-6)
        self.circle(sk, App.Vector(35, 15, 10), 5)
        f = self.extrude(sk, operation="cut", distance="5 mm")
        self.assertTrue(f.Shape.isValid())
        self.assertAlmostEqual(f.Shape.Volume, 12000 - math.pi * 25 * 5, delta=1e-6 * 12000)
        try:
            f.Shape.check(True)
        except Exception:
            self.assertIn("no thickness", safe.unsound(f))

    def test_taper_as_freecad_makes_it(self):
        """Above zero the outline grows along the extrusion (FreeCAD's Pad): a 40 x 30 profile 10 high at 5°."""
        sk = self.on_face(lambda g: abs(g.CenterOfMass.z - 10) < 1e-6)
        local = sk.getGlobalPlacement().inverse()
        pts = [local.multVec(App.Vector(x, y, 10)) for x, y in ((10, 10), (30, 10), (30, 20), (10, 20))]
        for i in range(4):
            sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
        self.doc.recompute()
        f = self.extrude(sk, operation="join", distance="10 mm", taper="5 °")
        x = 10 * math.tan(math.radians(5))
        frustum = 10 / 6 * (200 + 4 * (20 + x) * (10 + x) + (20 + 2 * x) * (10 + 2 * x))
        self.sound(f.Shape, 12000 + frustum)

    def test_to_next_onto_a_curved_face(self):
        """To Next ends on the part's round face (a half cylinder lying on the block), not on its box."""
        cyl = self.body.newObject("PartDesign::AdditiveCylinder", "Roof")
        cyl.Radius, cyl.Height = 15, 40
        cyl.Placement = App.Placement(App.Vector(0, 15, 10), App.Rotation(App.Vector(0, 1, 0), 90))
        self.doc.recompute()
        sk = util.add_rect_sketch(self.body, w=4, h=4, name="Under")
        sk.AttachmentOffset = App.Placement(App.Vector(18, 13, 40), App.Rotation())
        self.doc.recompute()
        f = self.extrude(sk, operation="join", extent="to_next", direction="flip")
        tool = f.AddSubShape
        self.assertAlmostEqual(tool.BoundBox.ZMin, 10 + math.sqrt(15 ** 2 - 2 ** 2), places=4)
        self.assertTrue(f.Shape.isValid())

    def test_through_all_with_a_taper_and_two_sides(self):
        sk = util.add_rect_sketch(self.body, w=10, h=10, name="Mid")
        sk.AttachmentOffset = App.Placement(App.Vector(15, 10, 5), App.Rotation())
        self.doc.recompute()
        f = self.extrude(sk, operation="cut", extent="through_all", direction="symmetric", taper="-2 °")
        self.assertTrue(f.Shape.isValid())
        self.assertEqual(len(f.Shape.Solids), 1)
        self.assertFalse(f.Shape.isInside(App.Vector(20, 15, 0.5), 1e-6, True), "through to the bottom")
        self.assertFalse(f.Shape.isInside(App.Vector(20, 15, 9.5), 1e-6, True), "and the top")
        g = extrude.update(self.doc, f, dict(extrude.read(self.doc, f), extent="distance", taper="0 °",
                                             direction="two_sides", distance="3 mm", distance2="2 mm"))
        self.doc.recompute()
        self.sound(g.Shape, 12000 - 10 * 10 * 5)

    def test_a_pattern_repeats_it(self):
        sk = self.on_face(lambda g: abs(g.CenterOfMass.z - 10) < 1e-6)
        self.circle(sk, App.Vector(5, 15, 10), 2)
        f = self.extrude(sk, operation="cut", distance="4 mm")
        pat = self.body.newObject("PartDesign::LinearPattern", "Row")
        pat.Originals = [f]
        pat.Direction = ([o for o in self.body.Origin.OriginFeatures if o.Role == "X_Axis"][0], [""])
        pat.Length, pat.Occurrences = 20, 3
        self.doc.recompute()
        self.assertAlmostEqual(pat.Shape.Volume, 12000 - 3 * math.pi * 4 * 4, places=4)

    def test_an_old_pad_stays_a_pad(self):
        """A file made before: its Pad edits as a Pad, and a switch to Cut makes it FreeCAD's Pocket."""
        old = util.add_pad(self.body, util.add_rect_sketch(self.body, w=4, h=4, name="Old"), 5, name="OldPad")
        old.Placement = App.Placement()
        self.doc.recompute()
        g = extrude.update(self.doc, old, dict(extrude.read(self.doc, old), distance="6 mm"))
        self.doc.recompute()
        self.assertEqual(g.TypeId, "PartDesign::Pad")
        g = extrude.update(self.doc, g, dict(extrude.read(self.doc, g), operation="cut", direction="flip"))
        self.doc.recompute()  # (the sketch on the bottom: a Pocket goes up into the block when flipped)
        self.assertEqual(g.TypeId, "PartDesign::Pocket")
        self.assertAlmostEqual(g.Shape.Volume, 12000 - 4 * 4 * 6, places=4)
