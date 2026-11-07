"""Sketch ▸ Symmetric, InCad's own (incad.core.sketch_symmetric): picks by point / edge / axis, never a dimension;
two points, two lines (ends paired by their mirror images) or two circles / arcs (centres, equal radii) mirrored about
a line, an axis or a point; older ties the new ones repeat go."""
import math
import unittest

import FreeCAD as App
import Part
import Sketcher
from incad.core import document, safe, sketch_symmetric as sym
from incad.core import sketch as sketches
from headless import util

V = App.Vector


class SymmetricTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Sym")
        self.body = document.make_part(self.doc)
        sk = self.body.newObject("Sketcher::SketchObject", "S")
        sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        sk.MapMode = "FlatFace"
        self.sk = sk

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def add(self, geom, construction=False):
        g = self.sk.addGeometry(geom, construction)
        self.doc.recompute()
        return g

    def apply(self, a, b, s):
        r = safe.run_safely("Symmetric", lambda: sym.constrain(self.sk, a, b, s), self.doc)
        self.doc.recompute()
        return r

    def projected(self, *geoms):
        """Projected edges in the sketch (ids -3, -4…): another sketch's geometry, as Project gives it."""
        ref = self.body.newObject("Sketcher::SketchObject", "Ref")
        ref.AttachmentSupport = [(util.xy_plane(self.body), "")]
        ref.MapMode = "FlatFace"
        for g in geoms:
            ref.addGeometry(g)
        self.doc.recompute()
        for i in range(len(geoms)):
            self.sk.addExternal(ref.Name, "Edge%d" % (i + 1))
        self.doc.recompute()
        return [-3 - i for i in range(len(geoms))]

    def test_a_projected_line_is_picked_and_mirrors(self):
        """The user's law: projected geometry counts like the sketch's own."""
        (ext,) = self.projected(Part.LineSegment(V(5, -10, 0), V(5, 10, 0)))
        self.assertEqual(sym.pick_at(self.sk, V(5.1, 3, 0), 0.5, 0.5), ("edge", ext))
        self.assertEqual(sym.from_subname(self.sk, "ExternalEdge1"), ("edge", ext))
        self.assertEqual(sym.subname(self.sk, ("edge", ext)), "ExternalEdge1")
        p = self.add(Part.Point(V(2, 1, 0)))
        q = self.add(Part.Point(V(9, 3, 0)))
        r = self.apply(("point", p, 1), ("point", q, 1), ("edge", ext))
        self.assertTrue(r.ok, r.error)
        a, b = self.sk.getPoint(p, 1), self.sk.getPoint(q, 1)
        self.assertAlmostEqual(a.x + b.x, 10, places=6)
        self.assertAlmostEqual(a.y, b.y, places=6)

    def test_snap_to_points_and_lines_of_every_kind(self):
        """The user's law: a drawn point snaps to normal, construction and projected geometry alike."""
        (ext,) = self.projected(Part.LineSegment(V(20, -10, 0), V(20, 10, 0)))
        cons = self.add(Part.LineSegment(V(0, 5, 0), V(10, 5, 0)), construction=True)
        p, pick = sym.snap_at(self.sk, V(10.2, 5.1, 0), 0.5)
        self.assertEqual((pick, round(p.x, 6), round(p.y, 6)), (("point", cons, 2), 10.0, 5.0))
        p, pick = sym.snap_at(self.sk, V(4, 5.3, 0), 0.5)
        self.assertEqual((pick, round(p.y, 6)), (("edge", cons), 5.0))
        p, pick = sym.snap_at(self.sk, V(19.8, 10.2, 0), 0.5)
        self.assertEqual((pick, round(p.x, 6), round(p.y, 6)), (("point", ext, 2), 20.0, 10.0))
        p, pick = sym.snap_at(self.sk, V(30, 30, 0), 0.5)
        self.assertIsNone(pick)

    def test_a_center_slot_is_tied_where_it_snapped(self):
        from incad.ui.slot_tool import SlotTool
        (ext,) = self.projected(Part.LineSegment(V(20, -10, 0), V(20, 10, 0)))
        dot = self.add(Part.Point(V(0, 0, 0)))
        tool = SlotTool(None, self.sk, "center")
        tool.click(*sym.snap_at(self.sk, V(0.1, 0.1, 0), 0.5))      # the centre on the point
        tool.click(*sym.snap_at(self.sk, V(20.1, 3, 0), 0.5))       # an end's centre on the projected line
        ids = tool.click(V(10, 4, 0))
        self.assertIsNotNone(ids)
        types = [c.Type for c in self.sk.Constraints]
        self.assertIn("Symmetric", types)
        self.assertIn("PointOnObject", types)
        c1, c2 = self.sk.getPoint(ids[0], 3), self.sk.getPoint(ids[1], 3)
        self.assertAlmostEqual(c2.x, 20, places=6)
        self.assertAlmostEqual(((c1 + c2) * 0.5 - self.sk.getPoint(dot, 1)).Length, 0, places=6)

    def test_two_points_about_the_y_axis(self):
        p = self.add(Part.Point(V(-7, 2, 0)))
        q = self.add(Part.Point(V(5, 6, 0)))
        r = self.apply(("point", p, 1), ("point", q, 1), ("edge", sym.V_AXIS))
        self.assertTrue(r.ok, r.error)
        a, b = self.sk.getPoint(p, 1), self.sk.getPoint(q, 1)
        self.assertAlmostEqual(a.x, -b.x, places=6)
        self.assertAlmostEqual(a.y, b.y, places=6)

    def test_a_point_on_the_mirror_line_takes_the_other_onto_it(self):
        """A point on the mirror line is its own mirror image: Symmetric was refused ("other constraints hold them",
        the finder); the other point goes onto it."""
        p = self.add(Part.Point(V(0, 30, 0)))
        self.sk.addConstraint(Sketcher.Constraint("PointOnObject", p, 1, -2))
        self.sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, p, 1, 30))
        q = self.add(Part.Point(V(5, 6, 0)))
        r = self.apply(("point", p, 1), ("point", q, 1), ("edge", sym.V_AXIS))
        self.assertTrue(r.ok, r.error)
        b = self.sk.getPoint(q, 1)
        self.assertAlmostEqual(b.x, 0, places=6)
        self.assertAlmostEqual(b.y, 30, places=6)

    def test_two_lines_ends_paired_by_their_mirror(self):
        axis = self.add(Part.LineSegment(V(0, -10, 0), V(0, 10, 0)), construction=True)
        left = self.add(Part.LineSegment(V(-8, -3, 0), V(-4, 5, 0)))
        right = self.add(Part.LineSegment(V(5, 4, 0), V(9, -2, 0)))  # drawn the other way round
        r = self.apply(("edge", left), ("edge", right), ("edge", axis))
        self.assertTrue(r.ok, r.error)
        self.assertEqual([c.Type for c in self.sk.Constraints].count("Symmetric"), 2)
        for pa, pb in ((1, 2), (2, 1)):  # left's start mirrors right's end (nearest), not a crossed pair
            a, b = self.sk.getPoint(left, pa), self.sk.getPoint(right, pb)
            m = sym._mirror(self.sk, ("edge", axis), a)  # about the centreline where the solver left it (it is free)
            self.assertAlmostEqual((m - b).Length, 0, places=6)

    def test_two_circles_and_two_arcs(self):
        c1 = self.add(Part.Circle(V(-6, 3, 0), V(0, 0, 1), 2))
        c2 = self.add(Part.Circle(V(7, 1, 0), V(0, 0, 1), 3))
        r = self.apply(("edge", c1), ("edge", c2), ("edge", sym.V_AXIS))
        self.assertTrue(r.ok, r.error)
        self.assertAlmostEqual(self.sk.Geometry[c1].Radius, self.sk.Geometry[c2].Radius, places=6)
        a, b = self.sk.getPoint(c1, 3), self.sk.getPoint(c2, 3)
        self.assertAlmostEqual(a.x, -b.x, places=6)
        a1 = self.add(Part.ArcOfCircle(Part.Circle(V(2, 9, 0), V(0, 0, 1), 2), 0, math.pi))
        a2 = self.add(Part.ArcOfCircle(Part.Circle(V(3, -8, 0), V(0, 0, 1), 4), math.pi, 2 * math.pi))
        r = self.apply(("edge", a1), ("edge", a2), ("edge", sym.H_AXIS))
        self.assertTrue(r.ok, r.error)
        self.assertAlmostEqual(self.sk.getPoint(a1, 3).y, -self.sk.getPoint(a2, 3).y, places=6)

    def test_about_a_point(self):
        p = self.add(Part.Point(V(-3, 1, 0)))
        q = self.add(Part.Point(V(4, 4, 0)))
        r = self.apply(("point", p, 1), ("point", q, 1), sym.ROOT)
        self.assertTrue(r.ok, r.error)
        self.assertAlmostEqual((self.sk.getPoint(p, 1) + self.sk.getPoint(q, 1)).Length, 0, places=6)

    def test_refusals_in_plain_words(self):
        line = self.add(Part.LineSegment(V(-5, 0, 0), V(-1, 4, 0)))
        circle = self.add(Part.Circle(V(5, 2, 0), V(0, 0, 1), 1))
        arc = self.add(Part.ArcOfCircle(Part.Circle(V(0, 8, 0), V(0, 0, 1), 2), 0, 1))
        for a, b, s, word in ((("edge", line), ("edge", circle), ("edge", sym.V_AXIS), "two of a kind"),
                              (("edge", line), ("point", line, 1), ("edge", sym.V_AXIS), "two of a kind"),
                              (("point", line, 1), ("point", line, 2), ("edge", arc), "straight line"),
                              (("edge", line), ("edge", line), ("edge", sym.V_AXIS), "two different")):
            r = self.apply(a, b, s)
            self.assertFalse(r.ok)
            self.assertIn(word, r.error)
        self.assertEqual(self.sk.ConstraintCount, 0)  # each refusal left nothing behind

    def test_picks_points_first_then_edges_then_axes_never_dimensions(self):
        line = self.add(Part.LineSegment(V(-10, 5, 0), V(10, 5, 0)))
        self.sk.addConstraint(Sketcher.Constraint("DistanceX", line, 1, line, 2, 20))
        self.doc.recompute()
        self.assertEqual(sym.pick_at(self.sk, V(-9.6, 5.3, 0), 1.0, 0.5), ("point", line, 1))
        self.assertEqual(sym.pick_at(self.sk, V(2, 5.3, 0), 1.0, 0.5), ("edge", line))
        self.assertEqual(sym.pick_at(self.sk, V(0.2, -7, 0), 1.0, 0.5), ("edge", sym.V_AXIS))
        self.assertEqual(sym.pick_at(self.sk, V(0.2, 0.3, 0), 1.0, 0.5), sym.ROOT)
        label = sketches.label_point(self.sk, self.sk.Constraints[0].Name) if self.sk.Constraints[0].Name else None
        if label is not None:  # a click on the dimension's label takes no dimension
            pick = sym.pick_at(self.sk, self.sk.getGlobalPlacement().inverse().multVec(label), 1.0, 0.5)
            self.assertTrue(pick is None or pick[0] in ("point", "edge"))
        self.assertIsNone(sym.pick_at(self.sk, V(30, 30, 0), 1.0, 0.5))

    def test_selection_names_round_trip(self):
        line = self.add(Part.LineSegment(V(-10, 5, 0), V(10, 5, 0)))
        for pick in (("edge", line), ("point", line, 2), ("edge", sym.H_AXIS), ("edge", sym.V_AXIS), sym.ROOT):
            self.assertEqual(sym.from_subname(self.sk, sym.subname(self.sk, pick)), pick)

    def test_a_slots_horizontal_goes(self):
        from incad.core import slots
        ids = slots.add(self.sk, V(-12, 3, 0), V(14, 3, 0), 4)
        self.doc.recompute()
        self.assertIn("Horizontal", [c.Type for c in self.sk.Constraints])
        r = self.apply(("point", ids[0], 3), ("point", ids[1], 3), ("edge", sym.V_AXIS))
        self.assertTrue(r.ok, r.error)
        self.assertNotIn("Horizontal", [c.Type for c in self.sk.Constraints])
        self.assertEqual(self.sk.solve(), 0)


if __name__ == "__main__":
    unittest.main()
