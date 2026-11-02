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
        self.assertEqual(f.TypeId, "PartDesign::Pad")
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
        self.assertEqual(g.TypeId, "PartDesign::Pocket")
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
        self.assertEqual(base.TypeId, "PartDesign::Pad")

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
        self.assertEqual((f.TypeId, f.Type), ("PartDesign::Pocket", "ThroughAll"))
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
