import math
import unittest

import FreeCAD as App
import Part
from incad.core import document, model
from incad.features import hole, kind_for
from headless import util


class HoleTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Ho")
        self.body, self.pad = util.padded_part(self.doc, 10, incad=True)
        self.top = [i + 1 for i, f in enumerate(self.pad.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6][0]

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def sketch_with_circle(self):
        sk = self.body.newObject("Sketcher::SketchObject", "Pts")
        sk.AttachmentSupport = [(self.pad, "Face%d" % self.top)]
        sk.MapMode = "FlatFace"
        sk.addGeometry(Part.Circle(App.Vector(10, 5, 0), App.Vector(0, 0, 1), 1))
        self.doc.recompute()
        return sk

    def test_from_sketch_through_all(self):
        sk = self.sketch_with_circle()
        v = dict(hole.defaults(self.doc, self.body, None), sketch=sk.Name, depth_type="through_all", diameter="4 mm")
        f = hole.create(self.doc, self.body, None, v)
        self.doc.recompute()
        self.assertEqual(f.TypeId, "PartDesign::Hole")
        self.assertIs(self.body.Tip, f)
        self.assertAlmostEqual(self.body.Shape.Volume, 2000 - math.pi * 4 * 10, places=2)
        self.assertIs(kind_for(f), hole)
        self.assertEqual(hole.read(self.doc, f)["diameter"], "4 mm")

    def test_to_a_face_follows_the_part_made_thicker(self):
        """The depth to the bottom face was measured once: the part made 20 mm left the hole blind."""
        sk = self.body.newObject("Sketcher::SketchObject", "Pt")
        sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        sk.MapMode = "FlatFace"
        sk.AttachmentOffset = App.Placement(App.Vector(0, 0, 10), App.Rotation())
        sk.addGeometry(Part.Point(App.Vector(10, 5, 0)))
        self.doc.recompute()
        bottom = [i + 1 for i, f in enumerate(self.pad.Shape.Faces) if abs(f.CenterOfMass.z) < 1e-6][0]
        v = dict(hole.defaults(self.doc, self.body, None), sketch=sk.Name, depth_type="to", diameter="4 mm",
                 to_face="%s:Face%d" % (self.pad.Name, bottom), tip="flat")
        f = hole.create(self.doc, self.body, None, v)
        self.doc.recompute()
        self.pad.Length = 20
        sk.AttachmentOffset = App.Placement(App.Vector(0, 0, 20), App.Rotation())
        self.doc.recompute()
        self.assertEqual(hole.follow_part(self.doc), [f.Label])
        self.doc.recompute()
        self.assertAlmostEqual(f.Depth.Value, 20, places=6)
        self.assertAlmostEqual(self.body.Shape.Volume, 4000 - math.pi * 4 * 20, places=2)
        self.assertEqual(hole.follow_part(self.doc), [], "in step now: nothing more to do")

    def test_counterbore(self):
        sk = self.sketch_with_circle()
        v = dict(hole.defaults(self.doc, self.body, None), sketch=sk.Name, seat="counterbore", depth_type="through_all",
                 diameter="3 mm", cbore_diameter="6 mm", cbore_depth="2 mm")
        f = hole.create(self.doc, self.body, None, v)
        self.doc.recompute()
        self.assertEqual(f.HoleCutType, "Counterbore")
        self.assertAlmostEqual(2000 - self.body.Shape.Volume, math.pi * 1.5 ** 2 * 8 + math.pi * 9 * 2, places=1)

    def test_concentric_to_a_circular_edge(self):
        cyl_sk = self.body.newObject("Sketcher::SketchObject", "Boss")
        cyl_sk.AttachmentSupport = [(self.pad, "Face%d" % self.top)]
        cyl_sk.MapMode = "FlatFace"
        cyl_sk.addGeometry(Part.Circle(App.Vector(10, 5, 0), App.Vector(0, 0, 1), 3))
        boss = util.add_pad(self.body, cyl_sk, 5, name="BossPad")
        self.body.Tip = boss
        self.doc.recompute()
        top2 = [i + 1 for i, f in enumerate(boss.Shape.Faces) if abs(f.CenterOfMass.z - 15) < 1e-6][0]
        circle = [i + 1 for i, e in enumerate(boss.Shape.Edges)
                  if e.Curve.TypeId == "Part::GeomCircle" and abs(e.BoundBox.ZMax - 15) < 1e-6][0]
        v = dict(hole.defaults(self.doc, self.body, None), placement="concentric",
                 face="%s:Face%d" % (boss.Name, top2), edge="%s:Edge%d" % (boss.Name, circle),
                 diameter="2 mm", depth_type="distance", depth="4 mm")
        f = hole.create(self.doc, self.body, None, v)
        self.doc.recompute()
        self.assertEqual(f.State, ["Up-to-date"])
        c = f.Shape.BoundBox.Center
        self.assertLess(self.body.Shape.Volume, boss.Shape.Volume)
        self.assertAlmostEqual(hole.read(self.doc, f)["placement"], "concentric")

    def test_concentric_follows_its_round_edge(self):
        """The helper sketch held fixed points: a boss moved left its hole off-centre."""
        sk = self.body.newObject("Sketcher::SketchObject", "Boss")
        sk.AttachmentSupport = [(self.pad, "Face%d" % self.top)]
        sk.MapMode = "FlatFace"
        sk.addGeometry(Part.Circle(App.Vector(10, 5, 0), App.Vector(0, 0, 1), 3))
        self.doc.recompute()
        boss = util.add_pad(self.body, sk, 5, "BossPad")
        self.body.Tip = boss
        self.doc.recompute()
        rim = [i + 1 for i, e in enumerate(boss.Shape.Edges)
               if e.Curve.TypeId == "Part::GeomCircle" and abs(e.CenterOfMass.z - 15) < 1e-6][0]
        v = dict(hole.defaults(self.doc, self.body, None), placement="concentric", edge="%s:Edge%d" % (boss.Name, rim),
                 diameter="2 mm", depth_type="distance", depth="3 mm", tip="flat")
        f = hole.create(self.doc, self.body, None, v)
        self.doc.recompute()
        sk.delGeometries([0])
        sk.addGeometry(Part.Circle(App.Vector(6, 5, 0), App.Vector(0, 0, 1), 3))
        self.doc.recompute()
        self.assertEqual(hole.follow_part(self.doc), [f.Label])
        self.doc.recompute()
        prof = f.Profile[0] if isinstance(f.Profile, (tuple, list)) else f.Profile
        centre = prof.getGlobalPlacement().multVec(App.Vector(prof.Geometry[0].X, prof.Geometry[0].Y, 0))
        self.assertAlmostEqual(centre.x, 6, places=5)

    def test_concentric_to_several_round_edges_and_faces_at_once(self):
        """The user: several holes in one go, and a round face (easier to click than its edge); no Face picked —
        the flat face beside the first round edge."""
        sk = self.body.newObject("Sketcher::SketchObject", "Bosses")
        sk.AttachmentSupport = [(self.pad, "Face%d" % self.top)]
        sk.MapMode = "FlatFace"
        for x in (4, 10, 16):
            sk.addGeometry(Part.Circle(App.Vector(x, 5, 0), App.Vector(0, 0, 1), 2))
        boss = util.add_pad(self.body, sk, 3, name="Bosses")
        self.body.Tip = boss
        self.doc.recompute()
        S = boss.Shape
        circle = [i + 1 for i, e in enumerate(S.Edges) if e.Curve.TypeId == "Part::GeomCircle"
                  and abs(e.BoundBox.ZMax - 13) < 1e-6 and abs(e.Curve.Center.x - 4) < 1e-6][0]
        sides = {round(f.Surface.Center.x): i + 1 for i, f in enumerate(S.Faces) if f.Surface.TypeId == "Part::GeomCylinder"}
        picks = "%s:Edge%d,%s:Face%d,%s:Face%d" % (boss.Name, circle, boss.Name, sides[10], boss.Name, sides[16])
        f = hole.create(self.doc, self.body, None, dict(hole.defaults(self.doc, self.body, None), placement="concentric",
                                                        edge=picks, diameter="1 mm", depth_type="distance", depth="2 mm"))
        self.doc.recompute()
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        bores = sorted(round(fc.Surface.Center.x, 6) for fc in f.Shape.Faces
                       if fc.Surface.TypeId == "Part::GeomCylinder" and abs(fc.Surface.Radius - 0.5) < 1e-6)
        self.assertEqual(sorted(set(bores)), [4, 10, 16])
        self.assertAlmostEqual(self.doc.getObject(f.Profile[0].Name).getGlobalPlacement().Base.z, 13, places=6)
        r = hole.read(self.doc, f)
        self.assertEqual((r["placement"], r["edge"], r["face"]), ("concentric", picks, ""))

    def test_parameters(self):
        sk = self.sketch_with_circle()
        f = hole.create(self.doc, self.body, None, dict(hole.defaults(self.doc, self.body, None), sketch=sk.Name))
        self.doc.recompute()
        name = model.bound_parameter(f, "Diameter")
        model.set_model_parameter(self.doc, name, "5 mm")
        self.assertAlmostEqual(f.Diameter.Value, 5)

    def test_concentric_edit_moves_to_another_edge(self):
        boss_sk = self.body.newObject("Sketcher::SketchObject", "Bosses")
        boss_sk.AttachmentSupport = [(self.pad, "Face%d" % self.top)]
        boss_sk.MapMode = "FlatFace"
        boss_sk.addGeometry(Part.Circle(App.Vector(5, 5, 0), App.Vector(0, 0, 1), 2))
        boss_sk.addGeometry(Part.Circle(App.Vector(15, 5, 0), App.Vector(0, 0, 1), 2))
        boss = util.add_pad(self.body, boss_sk, 3, name="Bosses")
        self.body.Tip = boss
        self.doc.recompute()
        top2 = [i + 1 for i, f in enumerate(boss.Shape.Faces)
                if abs(f.CenterOfMass.z - 13) < 1e-6 and abs(f.CenterOfMass.x - 5) < 1e-6][0]
        top3 = [i + 1 for i, f in enumerate(boss.Shape.Faces)
                if abs(f.CenterOfMass.z - 13) < 1e-6 and abs(f.CenterOfMass.x - 15) < 1e-6][0]
        def circle(x):
            return [i + 1 for i, e in enumerate(boss.Shape.Edges) if e.Curve.TypeId == "Part::GeomCircle"
                    and abs(e.BoundBox.ZMax - 13) < 1e-6 and abs(e.Curve.Center.x - x) < 1e-6][0]
        v = dict(hole.defaults(self.doc, self.body, None), placement="concentric", diameter="1 mm",
                 face="%s:Face%d" % (boss.Name, top2), edge="%s:Edge%d" % (boss.Name, circle(5)),
                 depth_type="distance", depth="2 mm")
        f = hole.create(self.doc, self.body, None, v)
        self.doc.recompute()
        r = hole.read(self.doc, f)
        self.assertEqual((r["face"], r["edge"]), (v["face"], v["edge"]))
        f = hole.update(self.doc, f, dict(r, face="%s:Face%d" % (boss.Name, top3),
                                          edge="%s:Edge%d" % (boss.Name, circle(15))))
        self.doc.recompute()
        bores = [fc for fc in f.Shape.Faces if fc.Surface.TypeId == "Part::GeomCylinder"
                 and abs(fc.Surface.Radius - 0.5) < 1e-6]
        self.assertTrue(bores and all(abs(fc.Surface.Center.x - 15) < 1e-6 for fc in bores),
                        [fc.Surface.Center for fc in bores])

    def test_threaded_m8_with_clearance_and_back_to_plain(self):
        sk = self.sketch_with_circle()
        f = hole.create(self.doc, self.body, None, dict(hole.defaults(self.doc, self.body, None), sketch=sk.Name,
                                                        diameter="4 mm"))
        self.doc.recompute()
        plain_param = model.bound_parameter(f, "Diameter")
        v = dict(hole.read(self.doc, f), hole_type="tapped", thread_standard="iso", thread_size="M8x1.25",
                 clearance="0.2 mm", depth_type="through_all")
        f = hole.update(self.doc, f, v)
        self.doc.recompute()
        self.assertTrue(f.Threaded and f.ModelThread)
        self.assertEqual(f.ThreadSize, "M8x1.25")
        self.assertTrue(f.UseCustomThreadClearance)
        self.assertAlmostEqual(f.CustomThreadClearance.Value, 0.2, places=6)
        self.assertNotIn("Invalid", f.State)
        r = hole.read(self.doc, f)
        self.assertEqual((r["hole_type"], r["thread_standard"], r["thread_size"], r["clearance"]),
                         ("tapped", "iso", "M8x1.25", "0.2 mm"))
        f = hole.update(self.doc, f, dict(r, hole_type="simple"))
        self.doc.recompute()
        self.assertFalse(f.Threaded)
        self.assertEqual(model.bound_parameter(f, "Diameter"), plain_param)
        self.assertAlmostEqual(f.Diameter.Value, 4, places=6)

    def test_thread_size_options_follow_the_standard(self):
        sizes = [v for v, _ in hole.options("thread_size", {"thread_standard": "unc"})]
        self.assertIn("1/4", sizes)
        self.assertEqual(sizes[0], "")  # Auto
        self.assertIs(App.ActiveDocument, self.doc)  # the hidden probe document leaves the active one alone
        self.assertIn("M8x1.25", [v for v, _ in hole.options("thread_size", {"thread_standard": "iso"})])

    def test_clearance_hole_for_a_bolt_with_fit(self):
        sk = self.sketch_with_circle()
        for fit, dia in (("close", 5.3), ("normal", 5.5), ("loose", 5.8)):
            f = hole.create(self.doc, self.body, None, dict(hole.defaults(self.doc, self.body, None), sketch=sk.Name,
                                                            hole_type="clearance", thread_standard="iso",
                                                            thread_size="M5x0.8", fit=fit, depth_type="through_all"))
            self.doc.recompute()
            self.assertFalse(f.Threaded)
            self.assertAlmostEqual(f.Diameter.Value, dia, places=3, msg=fit)
            r = hole.read(self.doc, f)
            self.assertEqual((r["hole_type"], r["fit"]), ("clearance", fit))
            self.body.removeObject(f)
            self.doc.removeObject(f.Name)
            self.body.Tip = self.pad
            self.doc.recompute()

    def test_spotface_flip_and_drill_angle(self):
        sk = self.sketch_with_circle()
        f = hole.create(self.doc, self.body, None, dict(hole.defaults(self.doc, self.body, None), sketch=sk.Name,
                                                        seat="spotface", spot_diameter="9 mm", spot_depth="0.5 mm",
                                                        depth_type="distance", depth="4 mm", tip="angled",
                                                        tip_angle="120 °", flip=False))
        self.doc.recompute()
        self.assertEqual(f.HoleCutType, "Counterbore")
        self.assertAlmostEqual(f.HoleCutDepth.Value, 0.5, places=6)
        self.assertAlmostEqual(f.DrillPointAngle.Value, 120, places=6)
        r = hole.read(self.doc, f)
        self.assertEqual((r["seat"], r["spot_depth"]), ("spotface", "0.5 mm"))
        hole.update(self.doc, f, dict(r, flip=True))
        self.doc.recompute()
        self.assertTrue(f.Reversed)
        self.assertTrue(hole.read(self.doc, f)["flip"])

    def test_up_to_a_face(self):
        sk = self.sketch_with_circle()
        plane = self.body.newObject("PartDesign::Plane", "Stop")
        plane.AttachmentSupport = [(util.xy_plane(self.body), "")]
        plane.MapMode = "FlatFace"
        plane.AttachmentOffset = App.Placement(App.Vector(0, 0, 3), App.Rotation())
        self.doc.recompute()
        f = hole.create(self.doc, self.body, None, dict(hole.defaults(self.doc, self.body, None), sketch=sk.Name,
                                                        depth_type="to", to_face=plane.Name, tip="flat"))
        self.doc.recompute()
        self.assertAlmostEqual(f.Depth.Value, 7, places=4)  # from the top (z 10) down to the plane at z 3
        self.assertEqual(hole.read(self.doc, f)["depth_type"], "to")


def cone_cut(r, s):
    """Material a 45° chamfer of size s takes off the mouth of a hole of radius r."""
    R = r + s
    return math.pi * s / 3.0 * (R * R + R * r + r * r) - math.pi * r * r * s


class HoleChamferTest(unittest.TestCase):
    """User request: a Chamfer option in Hole for every hole (None / Start / Both ends), like Thread's."""
    setUp, tearDown, sketch_with_circle = HoleTest.setUp, HoleTest.tearDown, HoleTest.sketch_with_circle

    def make(self, sk=None, **kw):
        sk = sk or self.sketch_with_circle()
        v = dict(hole.defaults(self.doc, self.body, None), sketch=sk.Name, depth_type="through_all", diameter="4 mm")
        v.update(kw)
        f = hole.create(self.doc, self.body, None, v)
        self.doc.recompute()
        return f

    def helper(self, f):
        return [o for o in self.body.Group if getattr(o, "InCadHelperOf", None) == f.Name]

    def mouth_radii(self, z):
        return sorted(round(e.Curve.Radius, 4) for e in self.body.Shape.Edges
                      if e.Curve.TypeId == "Part::GeomCircle" and abs(e.Curve.Center.z - z) < 1e-6)

    def test_start_chamfer_on_a_through_hole(self):
        f = self.make(chamfer="start", chamfer_size="0.5 mm")
        self.assertEqual(len(self.helper(f)), 1)
        self.assertIs(self.body.Tip, self.helper(f)[0])
        self.assertTrue(self.body.Shape.isValid())
        self.assertAlmostEqual(self.body.Shape.Volume, 2000 - math.pi * 4 * 10 - cone_cut(2, 0.5), places=2)
        self.assertIn(2.5, self.mouth_radii(10))
        v = hole.read(self.doc, f)
        self.assertEqual((v["chamfer"], v["chamfer_size"]), ("start", "0.5 mm"))

    def test_both_ends_of_a_through_hole(self):
        self.make(chamfer="both", chamfer_size="0.5 mm")
        self.assertAlmostEqual(self.body.Shape.Volume, 2000 - math.pi * 4 * 10 - 2 * cone_cut(2, 0.5), places=2)
        self.assertIn(2.5, self.mouth_radii(0))

    def test_every_hole_of_the_sketch(self):
        sk = self.sketch_with_circle()
        sk.addGeometry(Part.Circle(App.Vector(4, 5, 0), App.Vector(0, 0, 1), 1))
        self.doc.recompute()
        self.make(sk, chamfer="start", chamfer_size="0.5 mm")
        self.assertAlmostEqual(self.body.Shape.Volume, 2000 - 2 * (math.pi * 4 * 10 + cone_cut(2, 0.5)), places=2)

    def test_a_blind_hole_gets_only_its_mouth(self):
        self.make(depth_type="distance", depth="5 mm", tip="flat", chamfer="both", chamfer_size="0.5 mm")
        self.assertAlmostEqual(self.body.Shape.Volume, 2000 - math.pi * 4 * 5 - cone_cut(2, 0.5), places=2)

    def test_a_tapped_hole_is_chamfered_from_the_thread_diameter(self):
        f = self.make(hole_type="tapped", thread_standard="iso", thread_size="M6x1.0", chamfer="start",
                      chamfer_size="0.5 mm")
        self.assertTrue(self.body.Shape.isValid())
        self.assertIn(3.5, self.mouth_radii(10))  # M6: Ø6 + 2 × 0.5

    def test_with_a_seat_only_the_exit(self):
        f = self.make(seat="counterbore", diameter="3 mm", cbore_diameter="6 mm", cbore_depth="2 mm",
                      chamfer="both", chamfer_size="0.5 mm")
        self.assertIn(2.0, self.mouth_radii(0))  # Ø3 + 2 × 0.5 at the exit
        self.assertNotIn(2.0, self.mouth_radii(8))  # nothing at the counterbore floor

    def test_the_chamfer_follows_the_hole_and_goes_with_it(self):
        from incad.core import delete
        f = self.make(chamfer="start", chamfer_size="0.5 mm")
        hole.update(self.doc, f, dict(hole.read(self.doc, f), diameter="5 mm"))
        self.doc.recompute()
        self.assertIn(3.0, self.mouth_radii(10))
        hole.update(self.doc, f, dict(hole.read(self.doc, f), chamfer="none"))
        self.doc.recompute()
        self.assertEqual(self.helper(f), [])
        self.assertIs(self.body.Tip, f)
        hole.update(self.doc, f, dict(hole.read(self.doc, f), chamfer="both"))
        self.doc.recompute()
        delete.delete(f)
        self.doc.recompute()
        self.assertFalse([o for o in self.body.Group if getattr(o, "InCadHelperOf", None)])
        self.assertAlmostEqual(self.body.Shape.Volume, 2000, places=3)

    def test_start_is_unavailable_with_a_seat(self):
        self.assertEqual(hole.unavailable(dict(hole.defaults(self.doc, self.body, None), seat="counterbore")),
                         {"chamfer": {"start"}})
        self.assertEqual(hole.unavailable(dict(hole.defaults(self.doc, self.body, None), seat="none")),
                         {"chamfer": set()})

    def no_material(self, r, z, centre=(10, 5)):
        pts = [App.Vector(centre[0] + r * math.cos(a), centre[1] + r * math.sin(a), z) for a in (0.0, 1.6, 3.1, 4.7)]
        return not [p for p in pts if self.body.Shape.isInside(p, 1e-7, True)]

    def test_a_tapped_hole_chamfer_has_no_ledge(self):
        """Review: the cone ended at the thread's diameter while the hole is drilled smaller: a flat ring at the
        bottom of the chamfer (seen with the modeled thread off, as Threads Off does)."""
        f = self.make(hole_type="tapped", thread_standard="iso", thread_size="M6x1.0", chamfer="start",
                      chamfer_size="0.5 mm")
        f.ModelThread = False
        self.doc.recompute()
        self.assertTrue(self.no_material(2.75, 9.45))

    def test_an_exit_past_the_thread_is_chamfered_from_the_drill(self):
        self.make(hole_type="tapped", thread_standard="iso", thread_size="M6x1.0", thread_depth="distance",
                  thread_length="4 mm", chamfer="both", chamfer_size="0.5 mm")
        radii = self.mouth_radii(0)
        self.assertNotIn(3.5, radii)
        self.assertTrue(self.no_material(2.75, 0.3))

    def test_a_moved_body_is_chamfered_at_its_holes(self):
        """Review: the hole centres were taken in the document's frame, the shapes in the body's."""
        self.body.Placement = App.Placement(App.Vector(30, 0, 0), App.Rotation())
        self.doc.recompute()
        self.make(chamfer="start", chamfer_size="0.5 mm")
        self.assertAlmostEqual(self.body.Shape.Volume, 2000 - math.pi * 4 * 10 - cone_cut(2, 0.5), places=2)

    def test_a_hole_starting_inside_the_part_gets_no_start_cone(self):
        sk = self.body.newObject("Sketcher::SketchObject", "Inside")
        sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        sk.MapMode = "FlatFace"
        sk.AttachmentOffset = App.Placement(App.Vector(0, 0, 5), App.Rotation())
        sk.addGeometry(Part.Circle(App.Vector(10, 5, 0), App.Vector(0, 0, 1), 1))
        self.doc.recompute()
        self.make(sk, chamfer="start", chamfer_size="0.5 mm")
        self.assertAlmostEqual(self.body.Shape.Volume, 2000 - math.pi * 4 * 5, places=2)

    def test_a_sketch_far_above_the_part_still_chamfers_the_mouth(self):
        sk = self.body.newObject("Sketcher::SketchObject", "Far")
        sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        sk.MapMode = "FlatFace"
        sk.AttachmentOffset = App.Placement(App.Vector(0, 0, 60), App.Rotation())
        sk.addGeometry(Part.Circle(App.Vector(10, 5, 0), App.Vector(0, 0, 1), 1))
        self.doc.recompute()
        self.make(sk, chamfer="start", chamfer_size="0.5 mm")
        self.assertIn(2.5, self.mouth_radii(10))
