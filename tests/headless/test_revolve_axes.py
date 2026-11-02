"""Revolve around: the sketch's vertical/horizontal axis, a construction line of the sketch, a picked line / axis /
edge, or two points (a hidden Work Axis owned by the revolve)."""
import unittest

import FreeCAD as App
import Part
from incad.core import delete, document, safe
from incad.features import revolve
from headless import util


def profile(body, support, sub="", pts=((10, 0), (18, 0), (18, 10), (10, 10)), construction=()):
    sk = body.newObject("Sketcher::SketchObject", "Prof")
    sk.AttachmentSupport = [(support, sub)]
    sk.MapMode = "FlatFace"
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        sk.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)))
    for a, b in construction:
        sk.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)), True)
    body.Document.recompute()
    return sk


class RevolveAxesTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Rev")
        document.make_part(self.doc, "mm")
        self.body = document.primary_body(self.doc)
        self.xz = [o for o in self.body.Origin.OriginFeatures if o.Role == "XZ_Plane"][0]

    def make(self, sk, **over):
        values = revolve.defaults(self.doc, self.body, sk)
        values.update(over)
        f = revolve.create(self.doc, self.body, sk, values)
        self.doc.recompute()
        return f

    def test_sketch_axes_as_before(self):
        sk = profile(self.body, self.xz)
        f = self.make(sk, axis_mode="V_Axis")
        self.assertEqual(f.ReferenceAxis[1], ["V_Axis"])
        self.assertAlmostEqual(f.Shape.Volume, 3.14159265 * (18 ** 2 - 10 ** 2) * 10, places=1)
        self.assertEqual(revolve.read(self.doc, f)["axis_mode"], "V_Axis")

    def test_construction_lines_are_listed_and_one_is_the_default(self):
        sk = profile(self.body, self.xz, construction=[((5, -5), (5, 20))])
        self.assertEqual(revolve.construction_lines(sk), [("Axis0", "Line 5 · 25 mm, vertical (construction)")])
        values = revolve.defaults(self.doc, self.body, sk)
        self.assertEqual((values["axis_mode"], values["axis_construction"]), ("construction", "Axis0"))
        f = self.make(sk)
        self.assertEqual(revolve.axis_name(f.ReferenceAxis[1][0]), "Axis0")  # FreeCAD stores it as 'Axis'
        self.assertAlmostEqual(f.Shape.Volume, 3.14159265 * (13 ** 2 - 5 ** 2) * 10, places=1)
        back = revolve.read(self.doc, f)
        self.assertEqual((back["axis_mode"], back["axis_construction"]), ("construction", "Axis0"))

    def test_several_construction_lines_keep_the_sketch_axis_default_and_list_all(self):
        sk = profile(self.body, self.xz, construction=[((5, -5), (5, 20)), ((0, 30), (40, 30))])
        lines = revolve.construction_lines(sk)
        self.assertEqual([v for v, _ in lines], ["Axis0", "Axis1"])
        self.assertIn("horizontal", lines[1][1])
        self.assertEqual(revolve.defaults(self.doc, self.body, sk)["axis_mode"], "V_Axis")
        self.assertEqual(revolve.options("axis_construction", {}, sk)[:2], lines)  # the profile's lines follow
        f = self.make(sk, axis_mode="construction", axis_construction="Axis1")
        self.assertEqual(f.ReferenceAxis[1], ["Axis1"])

    def test_two_angles_past_a_full_turn_are_refused(self):
        """200 + 200 gave a 40 deg wedge without a word (OCC turns the total round past 360)."""
        sk = profile(self.body, self.xz)
        with self.assertRaises(ValueError) as cm:
            self.make(sk, extent="two_angles", angle="200 °", angle2="200 °")
        self.assertIn("more than a full turn", str(cm.exception))

    def test_a_side_of_the_profile_is_an_axis_too(self):
        """The user's law: Revolve works about any line of the sketch, a side of the profile itself included."""
        sk = profile(self.body, self.xz)
        self.assertIn(("Line4", "Line 4 · 10 mm"), revolve.axis_line_options(sk))
        f = self.make(sk, axis_mode="construction", axis_construction="Line4")  # the side x = 10
        self.assertAlmostEqual(f.Shape.Volume, 3.14159265 * 8 ** 2 * 10, places=1)
        back = revolve.read(self.doc, f)  # read back as the line picked: the same axis
        self.assertEqual((back["axis_mode"], back["axis_ref"]), ("reference", "%s:Edge4" % sk.Name))

    def test_an_area_copy_offers_the_drawn_sketchs_lines(self):
        """A sketch whose lines cross is extruded through an area copy: its lines (construction ones too) are still
        the axes, held on the sketch the user drew."""
        from incad.core import regions
        sk = profile(self.body, self.xz, construction=[((5, -5), (5, 20))])
        sk.addGeometry(Part.LineSegment(App.Vector(0, -5, 0), App.Vector(0, 25, 0)))  # a normal line beside it
        self.doc.recompute()
        area = regions.make_profile(self.doc, self.body, sk, regions.regions(sk)[0])
        self.assertIn("Axis0", [v for v, _ in revolve.axis_line_options(area)])
        f = self.make(area, axis_mode="construction", axis_construction="Line6")  # the normal line x = 0 (Edge5: construction left out)
        self.assertIs(f.ReferenceAxis[0], sk)
        self.assertAlmostEqual(f.Shape.Volume, 3.14159265 * (18 ** 2 - 10 ** 2) * 10, places=1)
        g = revolve.update(self.doc, f, dict(revolve.read(self.doc, f), axis_mode="construction",
                                             axis_construction="Axis0"))  # its construction line x = 5
        self.doc.recompute()
        self.assertNotIn("Invalid", g.State, g.getStatusString())
        self.assertAlmostEqual(g.Shape.Volume, 3.14159265 * (13 ** 2 - 5 ** 2) * 10, places=1)

    def test_sketch_line_origin_axis_and_solid_edge_as_reference(self):
        sk = profile(self.body, self.xz)
        f = self.make(sk, axis_mode="reference", axis_ref="%s:Edge4" % sk.Name)  # the profile's own left side
        self.assertEqual(f.ReferenceAxis[0], sk)
        self.assertAlmostEqual(f.Shape.Volume, 3.14159265 * 8 ** 2 * 10, places=1)
        back = revolve.read(self.doc, f)
        self.assertEqual((back["axis_mode"], back["axis_ref"]), ("reference", "%s:Edge4" % sk.Name))
        z = [o for o in self.body.Origin.OriginFeatures if o.Role == "Z_Axis"][0]
        values = back
        values.update(axis_ref=z.Name)
        f = revolve.update(self.doc, f, values)
        self.doc.recompute()
        self.assertEqual(f.ReferenceAxis[0], z)
        self.assertEqual(revolve.read(self.doc, f)["axis_ref"], z.Name)
        pad = util.add_pad(self.body, util.add_rect_sketch(self.body, 30, 20, name="Base"), 10)
        self.doc.recompute()
        edge = next(i + 1 for i, e in enumerate(pad.Shape.Edges)
                    if e.Curve.TypeId == "Part::GeomLine" and abs(e.Vertexes[0].Point.x - 30) < 1e-6
                    and abs(e.Vertexes[1].Point.x - 30) < 1e-6 and abs(e.Vertexes[0].Point.y - e.Vertexes[1].Point.y) < 1e-6)
        values.update(axis_ref="%s:Edge%d" % (pad.Name, edge), operation="cut")
        sk2 = profile(self.body, self.xz, pts=((5, 2), (8, 2), (8, 8), (5, 8)))
        g = self.make(sk2, axis_mode="reference", axis_ref="%s:Edge%d" % (pad.Name, edge), operation="cut")
        self.assertEqual(g.TypeId, "PartDesign::Groove")
        self.assertIn("Edge%d" % edge, g.ReferenceAxis[1])

    def test_reference_mode_without_a_pick_says_so(self):
        sk = profile(self.body, self.xz)
        with self.assertRaises(ValueError) as cm:
            self.make(sk, axis_mode="reference", axis_ref="")
        self.assertIn("Pick", str(cm.exception))

    def test_two_points_make_a_hidden_work_axis_owned_by_the_revolve(self):
        sk = profile(self.body, self.xz)
        f = self.make(sk, axis_mode="two_points", axis_points="0 0 -5;0 0 30")
        line = f.ReferenceAxis[0]
        self.assertEqual(line.TypeId, "PartDesign::Line")
        self.assertEqual(getattr(line, "InCadHelperOf"), f.Name)
        self.assertFalse(line.Visibility)
        self.assertLess(self.body.Group.index(line), self.body.Group.index(f))  # before the revolve
        self.assertIs(self.body.Tip, f)
        self.assertAlmostEqual(f.Shape.Volume, 3.14159265 * (18 ** 2 - 10 ** 2) * 10, places=1)
        back = revolve.read(self.doc, f)
        self.assertEqual((back["axis_mode"], back["axis_points"]), ("two_points", "0 0 -5;0 0 30"))
        # moved points: the same helper is re-placed, not duplicated
        back["axis_points"] = "5 0 -5;5 0 30"
        f = revolve.update(self.doc, f, back)
        self.doc.recompute()
        self.assertEqual(len([o for o in self.body.Group if o.TypeId == "PartDesign::Line"]), 1)
        self.assertAlmostEqual(f.Shape.Volume, 3.14159265 * (13 ** 2 - 5 ** 2) * 10, places=1)
        # back to a sketch axis: the helper goes away
        back = revolve.read(self.doc, f)
        back.update(axis_mode="V_Axis")
        f = revolve.update(self.doc, f, back)
        self.doc.recompute()
        self.assertEqual([o for o in self.body.Group if o.TypeId == "PartDesign::Line"], [])
        self.assertEqual(f.ReferenceAxis[1], ["V_Axis"])

    def test_two_points_off_the_profile_plane_are_projected_onto_it(self):
        """Picked points can be anywhere on the solid; a revolve axis must lie in the profile's plane, so the two
        points are projected onto it. An axis beside the profile then revolves (it used to fail as out-of-plane)."""
        sk = profile(self.body, self.xz)  # profile in the XZ plane (y = 0), x from 10 to 18
        # two points well OFF the plane (y = 7) to the LEFT of the profile; their projection is the axis
        f = self.make(sk, operation="cut",
                      axis_points="2 7 -5;2 7 30", axis_mode="two_points")
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        helper = f.ReferenceAxis[0]
        pts = revolve.parse_points(getattr(helper, revolve.POINTS))
        self.assertTrue(all(abs(p.y) < 1e-6 for p in pts), pts)  # projected onto y = 0
        pad = util.add_pad(self.body, util.add_rect_sketch(self.body, 30, 20, name="B"), 6)  # give it something to cut
        self.doc.recompute()

    def test_two_points_need_two_different_points(self):
        sk = profile(self.body, self.xz)
        with self.assertRaises(ValueError):
            self.make(sk, axis_mode="two_points", axis_points="0 0 0")
        with self.assertRaises(ValueError):
            self.make(sk, axis_mode="two_points", axis_points="1 2 3;1 2 3")

    def test_delete_removes_the_axis_helper_with_the_revolve(self):
        sk = profile(self.body, self.xz)
        f = self.make(sk, axis_mode="two_points", axis_points="0 0 -5;0 0 30")
        name, fname = f.ReferenceAxis[0].Name, f.Name
        safe.run_safely("Delete", lambda: delete.delete(f), self.doc)
        self.assertIsNone(self.doc.getObject(fname))
        self.assertIsNone(self.doc.getObject(name))

    def test_pick_label_for_two_points(self):
        from incad.features.common import pick_label
        self.assertEqual(pick_label("two_points", ""), "Select…")
        self.assertEqual(pick_label("two_points", "0 0 0"), "1 of 2 points")
        self.assertEqual(pick_label("two_points", "0 0 0;1 1 1"), "2 points")

    def test_a_revolve_that_cannot_be_built_is_said_plainly(self):
        from incad.core import model
        for raw in ("BRep_API: command not done", "Standard_NullObject"):
            self.assertIn("axis must lie IN the profile's plane", model.friendly(raw))

    def test_axis_crossing_the_profile_is_said_plainly(self):
        from incad.core import model
        self.assertIn("crosses the profile", model.friendly("Revolve axis intersects the sketch"))

    def test_cut_that_removes_everything_is_said_plainly(self):
        xy = [o for o in self.body.Origin.OriginFeatures if o.Role == "XY_Plane"][0]
        base = profile(self.body, xy, pts=((50, 50), (70, 50), (70, 70), (50, 70)))  # a pad away from the Z axis
        util.add_pad(self.body, base, 10)
        self.doc.recompute()
        sk = profile(self.body, self.xz, pts=((2, -5), (150, -5), (150, 15), (2, 15)))  # beside the Z axis, huge
        broken = safe.snapshot_broken(self.doc)
        z = [o for o in self.body.Origin.OriginFeatures if o.Role == "Z_Axis"][0]
        g = self.make(sk, axis_mode="reference", axis_ref=z.Name, operation="cut")
        found = safe.problems(self.doc, broken, {o.Name for o in self.doc.Objects})
        self.assertTrue(any("whole part" in p for p in found), found)


class RevolveProjectedAxisTest(unittest.TestCase):
    """Revolve around a PROJECTED line. A projection lies IN the sketch plane, so it is a valid in-plane axis; it
    resolves to a helper line through the projection's own endpoints, never the 3D source edge (whose direction
    can be perpendicular to the plane — such an edge projects to a point and is not offered)."""

    def setUp(self):
        self.doc = util.new_doc("RP")
        document.make_part(self.doc, "mm")
        self.body = document.primary_body(self.doc)
        self.pad = util.add_pad(self.body, util.add_rect_sketch(self.body, 20, 20, name="Base"), 30)
        self.doc.recompute()
        xz = [o for o in self.body.Origin.OriginFeatures if o.Role == "XZ_Plane"][0]
        self.sk = self.body.newObject("Sketcher::SketchObject", "Prof")
        self.sk.AttachmentSupport = [(xz, "")]
        self.sk.MapMode = "FlatFace"
        self.doc.recompute()
        for i in range(4):
            a, b = [(30, 5), (38, 5), (38, 15), (30, 15)][i], [(30, 5), (38, 5), (38, 15), (30, 15)][(i + 1) % 4]
            self.sk.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)))
        # a vertical edge of the pad runs along Z, which lies IN the XZ sketch plane, so its projection is a line
        vedge = next(i + 1 for i, e in enumerate(self.pad.Shape.Edges)
                     if e.Curve.TypeId == "Part::GeomLine" and abs(e.Vertexes[0].Point.z - e.Vertexes[1].Point.z) > 1)
        self.sk.addExternal(self.pad.Name, "Edge%d" % vedge)
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_projected_line_is_listed_and_revolves_around_the_in_plane_projection(self):
        projected = revolve.projected_lines(self.sk)
        self.assertEqual(len(projected), 1, projected)
        value, label, ends = projected[0]
        self.assertTrue(value.startswith("ext") and "Projected" in label, (value, label))
        self.assertEqual(len(ends), 2)  # the projection's two global endpoints, not a (obj, sub) source ref
        self.assertIn((value, label), [(v, l) for v, l in revolve.axis_line_options(self.sk)])

        values = revolve.defaults(self.doc, self.body, self.sk)
        values.update(axis_mode="construction", axis_construction=value)
        f = revolve.create(self.doc, self.body, self.sk, values)
        self.doc.recompute()
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        line = f.ReferenceAxis[0]
        self.assertEqual(line.TypeId, "PartDesign::Line")                 # a helper through the projection
        self.assertEqual(getattr(line, "InCadHelperOf"), f.Name)
        self.assertFalse(line.Visibility)
        self.assertGreater(f.Shape.Volume, 1)
        back = revolve.read(self.doc, f)
        self.assertEqual(back["axis_mode"], "two_points")                 # the helper reads back as its axis line

    def test_an_edge_perpendicular_to_the_sketch_projects_to_a_point_and_is_not_offered(self):
        """A projection cannot be perpendicular to its own plane (the user's point). An edge whose direction IS
        perpendicular projects to a point, so it is simply not listed — never offered as a broken axis."""
        self.doc = util.new_doc("RP2")
        document.make_part(self.doc, "mm")
        body = document.primary_body(self.doc)
        pad = util.add_pad(body, util.add_rect_sketch(body, 40, 30, name="Base"), 20)
        self.doc.recompute()
        top = next(i + 1 for i, f in enumerate(pad.Shape.Faces) if abs(f.CenterOfMass.z - 20) < 1e-6)
        sk = body.newObject("Sketcher::SketchObject", "Top")
        sk.AttachmentSupport = [(pad, "Face%d" % top)]
        sk.MapMode = "FlatFace"
        self.doc.recompute()
        for i in range(4):
            a, b = [(10, 8), (14, 8), (14, 14), (10, 14)][i], [(10, 8), (14, 8), (14, 14), (10, 14)][(i + 1) % 4]
            sk.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)))
        vedge = next(i + 1 for i, e in enumerate(pad.Shape.Edges)
                     if e.Curve.TypeId == "Part::GeomLine" and abs(e.Vertexes[0].Point.z - e.Vertexes[1].Point.z) > 1)
        hedge = next(i + 1 for i, e in enumerate(pad.Shape.Edges) if e.Curve.TypeId == "Part::GeomLine"
                     and abs(e.Vertexes[0].Point.z - 20) < 1e-6 and abs(e.Vertexes[1].Point.z - 20) < 1e-6)
        sk.addExternal(pad.Name, "Edge%d" % vedge)   # perpendicular to the top face -> projects to a point
        sk.addExternal(pad.Name, "Edge%d" % hedge)   # in the top face -> projects to a line
        self.doc.recompute()
        projected = revolve.projected_lines(sk)
        self.assertEqual(len(projected), 1, projected)  # only the in-plane edge, not the perpendicular one
        values = revolve.defaults(self.doc, body, sk)
        values.update(operation="cut", axis_mode="construction", axis_construction=projected[0][0])
        v0 = body.Shape.Volume
        f = revolve.create(self.doc, body, sk, values)
        self.doc.recompute()
        self.assertNotIn("Invalid", f.State, f.getStatusString())  # the projected axis cuts, no "perpendicular"
        self.assertLess(body.Shape.Volume, v0 - 1)

    def test_a_projected_line_that_is_normal_geometry_still_revolves(self):
        """Projected edges are normal geometry by default (Inventor): the axis line is then part of the sketch's
        shape, an open wire beside the profile. Revolving around it still works."""
        from incad.core import sketch
        self.assertTrue(sketch.adopt_projections(self.sk))
        ext = sketch.projected(self.sk)[0]
        self.assertTrue(sketch.is_defining(self.sk, ext))
        value = revolve.projected_lines(self.sk)[0][0]
        values = revolve.defaults(self.doc, self.body, self.sk)
        values.update(axis_mode="construction", axis_construction=value)
        f = revolve.create(self.doc, self.body, self.sk, values)
        self.doc.recompute()
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        self.assertGreater(f.Shape.Volume, 1)
        self.assertFalse(sketch.is_defining(self.sk, ext), "the lone axis line became construction")
        self.assertFalse(sketch.adopt_projections(self.sk), "and stays so: it is recorded as the user's choice")

    def test_a_projected_line_that_closes_the_profile_stays_normal_and_revolves(self):
        """A profile drawn up to the projected axis line is closed BY it: that line stays normal geometry."""
        from incad.core import sketch
        xz = [o for o in self.body.Origin.OriginFeatures if o.Role == "XZ_Plane"][0]
        sk = self.body.newObject("Sketcher::SketchObject", "U")
        sk.AttachmentSupport = [(xz, "")]
        sk.MapMode = "FlatFace"
        self.doc.recompute()
        vedge = self.sk.ExternalGeometry[0][1][0]
        sk.addExternal(self.pad.Name, vedge)
        self.doc.recompute()
        sketch.adopt_projections(sk)
        ext = sketch.projected(sk)[0]
        g = sk.ExternalGeo[-ext - 1]
        a, b = g.StartPoint, g.EndPoint
        side = App.Vector(8, 0, 0) if a.x > 10 else App.Vector(-8, 0, 0)  # away from the block
        pts = [a, a + side, b + side, b]
        for i in range(3):
            sk.addGeometry(Part.LineSegment(pts[i], pts[i + 1]))
        self.doc.recompute()
        self.assertTrue(any(w.isClosed() for w in sk.Shape.Wires))
        value = revolve.projected_lines(sk)[0][0]
        values = revolve.defaults(self.doc, self.body, sk)
        values.update(axis_mode="construction", axis_construction=value)
        f = revolve.create(self.doc, self.body, sk, values)
        self.doc.recompute()
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        self.assertTrue(sketch.is_defining(sk, ext))
        self.assertGreater(f.Shape.Volume, 20 * 20 * 30 + 1)

    def test_a_single_projected_line_is_the_default_axis(self):
        values = revolve.defaults(self.doc, self.body, self.sk)
        self.assertEqual(values["axis_mode"], "construction")
        self.assertTrue(values["axis_construction"].startswith("ext"))
