"""InCad's sketch Offset (incad.core.sketch_offset): the whole outline from one edge, the offset one-sided for open
chains with sharp corners, ellipses as B-splines, the copy tied to the original by one offset dimension."""
import math
import unittest

import FreeCAD as App
import Part
import Sketcher

from incad.core import sketch_offset as so
from headless import util

V = App.Vector


def polyline(sk, pts, closed=True):
    """Lines through pts, joined by Coincident (as FreeCAD's rectangle / polygon tools make them)."""
    n = len(pts)
    ids = [sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % n])) for i in range(n if closed else n - 1)]
    for k in range(len(ids) - (0 if closed else 1)):
        sk.addConstraint(Sketcher.Constraint("Coincident", ids[k], 2, ids[(k + 1) % len(ids)], 1))
    return ids


class Base(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("SkOffset")
        self.body, _ = util.padded_part(self.doc, 5)
        body = self.body
        self.sk = body.newObject("Sketcher::SketchObject", "Sk")
        self.sk.AttachmentSupport = [(util.xy_plane(body), "")]
        self.sk.MapMode = "FlatFace"
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def new_geo(self, before):
        return [self.sk.Geometry[i] for i in range(before, self.sk.GeometryCount)]


class Outline(Base):
    def test_one_edge_of_a_rectangle_gives_all_four(self):
        ids = polyline(self.sk, [V(0, 0, 0), V(20, 0, 0), V(20, 10, 0), V(0, 10, 0)])
        chain, closed = so.outline(self.sk, ids[2])
        self.assertEqual(sorted(chain), sorted(ids))
        self.assertTrue(closed)

    def test_an_open_chain_and_a_lone_line(self):
        ids = polyline(self.sk, [V(0, 0, 0), V(10, 0, 0), V(10, 10, 0), V(20, 10, 0)], closed=False)
        lone = self.sk.addGeometry(Part.LineSegment(V(0, 30, 0), V(10, 40, 0)))
        chain, closed = so.outline(self.sk, ids[1])
        self.assertEqual(chain, ids)
        self.assertFalse(closed)
        self.assertEqual(so.outline(self.sk, lone), ([lone], False))

    def test_a_circle_and_an_ellipse_are_closed_outlines_of_one(self):
        c = self.sk.addGeometry(Part.Circle(V(50, 0, 0), V(0, 0, 1), 5))
        e = self.sk.addGeometry(Part.Ellipse(V(20, 50, 0), V(0, 55, 0), V(0, 50, 0)))
        self.assertEqual(so.outline(self.sk, c), ([c], True))
        self.assertEqual(so.outline(self.sk, e), ([e], True))

    def test_normal_and_construction_lines_make_one_outline(self):
        """The user's law: normal and construction lines have equal rights; each copy keeps its original's kind.
        A point never takes part in Offset (another law), not even one on a corner."""
        box = polyline(self.sk, [V(0, 0, 0), V(10, 0, 0), V(10, 6, 0), V(0, 6, 0)])
        for g in box[:2]:
            self.sk.setConstruction(g, True)
        dot = self.sk.addGeometry(Part.Point(V(10, 0, 0)))
        chain, closed = so.outline(self.sk, box[3])
        self.assertEqual((sorted(chain), closed), (sorted(box), True))
        with self.assertRaises(ValueError):
            so.outline(self.sk, dot)
        ids, _ = so.add(self.sk, chain, closed, V(5, -2, 0))
        self.assertEqual(len(ids), 4)
        kinds = {o: self.sk.getConstruction(o) for o in chain}
        self.assertEqual(sorted(self.sk.getConstruction(c) for c in ids), sorted(kinds.values()))

    def test_a_small_circle_is_offset_inside_without_crashing(self):
        """OCC's 2D offset brought the whole program down for a circle of radius 1 mm or less offset inward."""
        c = self.sk.addGeometry(Part.Circle(V(0, 0, 0), V(0, 0, 1), 1))
        ids, _ = so.add(self.sk, [c], True, V(0.1, 0, 0), 0.3)
        self.assertAlmostEqual(self.sk.Geometry[ids[0]].Radius, 0.7, 6)

    def test_a_construction_diagonal_does_not_bend_the_outline(self):
        """A construction diagonal drawn before the rectangle: a click on a side gave a triangle."""
        self.sk.addGeometry(Part.LineSegment(V(0, 0, 0), V(10, 6, 0)), True)
        box = polyline(self.sk, [V(0, 0, 0), V(10, 0, 0), V(10, 6, 0), V(0, 6, 0)])
        chain, closed = so.outline(self.sk, box[0])
        self.assertEqual((sorted(chain), closed), (sorted(box), True))

    def test_a_projected_outline_is_taken_too(self):
        """The user's law: projected edges have equal rights - the part's top outline, projected, is offset."""
        top = [i + 1 for i, e in enumerate(self.body.Tip.Shape.Edges) if abs(e.BoundBox.ZMin - 5) < 1e-6
               and abs(e.BoundBox.ZMax - 5) < 1e-6]
        for i in top:
            self.sk.addExternal(self.body.Tip.Name, "Edge%d" % i)
        self.sk.Document.recompute()
        chain, closed = so.outline(self.sk, -3)
        self.assertEqual((len(chain), closed), (4, True))


class Offsets(Base):
    def test_a_line_gives_one_parallel_line_on_the_pointers_side(self):
        """The user: a line became a slot round it (FreeCAD)."""
        line = self.sk.addGeometry(Part.LineSegment(V(0, 0, 0), V(20, 0, 0)))
        for pointer, y in ((V(10, 3, 0), 3.0), (V(10, -3, 0), -3.0)):
            curves = so.offset_curves(self.sk, [line], False, pointer)
            self.assertEqual(len(curves), 1)
            c = curves[0]
            self.assertEqual(type(c).__name__, "LineSegment")
            self.assertAlmostEqual(c.StartPoint.y, y, 6)
            self.assertAlmostEqual(c.EndPoint.y, y, 6)

    def test_a_rectangle_out_and_in_keeps_sharp_corners(self):
        """The user: offsets got rounded corners (FreeCAD's Arc mode)."""
        ids = polyline(self.sk, [V(0, 0, 0), V(20, 0, 0), V(20, 10, 0), V(0, 10, 0)])
        out = so.offset_curves(self.sk, ids, True, V(25, 5, 0))  # 5 outside the right side
        self.assertEqual([type(c).__name__ for c in out], ["LineSegment"] * 4)
        corners = sorted({(round(p.x, 6), round(p.y, 6)) for c in out for p in (c.StartPoint, c.EndPoint)})
        self.assertEqual(corners, [(-5, -5), (-5, 15), (25, -5), (25, 15)])
        inside = so.offset_curves(self.sk, ids, True, V(18, 5, 0))  # 2 inside the right side
        corners = sorted({(round(p.x, 6), round(p.y, 6)) for c in inside for p in (c.StartPoint, c.EndPoint)})
        self.assertEqual(corners, [(2, 2), (2, 8), (18, 2), (18, 8)])

    def test_a_triangle_outward_is_three_lines(self):
        ids = polyline(self.sk, [V(0, 0, 0), V(30, 0, 0), V(15, 25, 0)])
        out = so.offset_curves(self.sk, ids, True, V(15, -2, 0))
        self.assertEqual([type(c).__name__ for c in out], ["LineSegment"] * 3)

    def test_a_circle_grows_by_d(self):
        c = self.sk.addGeometry(Part.Circle(V(0, 0, 0), V(0, 0, 1), 5))
        out = so.offset_curves(self.sk, [c], True, V(8, 0, 0))
        self.assertEqual(type(out[0]).__name__, "Circle")
        self.assertAlmostEqual(out[0].Radius, 8, 6)

    def test_an_ellipse_gives_its_exact_offset_as_a_bspline(self):
        """The user: an ellipse could not be offset (FreeCAD drops the B-spline)."""
        e = self.sk.addGeometry(Part.Ellipse(V(20, 0, 0), V(0, 10, 0), V(0, 0, 0)))
        out = so.offset_curves(self.sk, [e], True, V(22, 0, 0))
        self.assertEqual(len(out), 1)
        self.assertEqual(type(out[0]).__name__, "BSplineCurve")
        ellipse = self.sk.Geometry[e].toShape()
        for p in out[0].toShape().discretize(40):
            self.assertAlmostEqual(ellipse.distToShape(Part.Vertex(p))[0], 2, 3)

    def test_an_inward_offset_that_folds_is_refused(self):
        ids = polyline(self.sk, [V(0, 0, 0), V(10, 0, 0), V(10, 4, 0), V(0, 4, 0)])
        with self.assertRaises(ValueError) as e:
            so.offset_curves(self.sk, ids, True, V(5, 2, 0) + V(0, 0, 0), distance=3)
        self.assertEqual(str(e.exception), so.TOO_LARGE_MSG)


class MoreShapes(Base):
    def test_an_open_l_chain_has_one_sharp_corner_on_either_side(self):
        ids = polyline(self.sk, [V(0, 0, 0), V(20, 0, 0), V(20, 10, 0)], closed=False)
        above = so.offset_curves(self.sk, ids, False, V(10, 2, 0))  # inside the L's bend: the corner at (18, 2)
        self.assertEqual([type(c).__name__ for c in above], ["LineSegment"] * 2)
        pts = {(round(p.x, 6), round(p.y, 6)) for c in above for p in (c.StartPoint, c.EndPoint)}
        self.assertEqual(pts, {(0, 2), (18, 2), (18, 10)})
        below = so.offset_curves(self.sk, ids, False, V(10, -2, 0))
        pts = {(round(p.x, 6), round(p.y, 6)) for c in below for p in (c.StartPoint, c.EndPoint)}
        self.assertEqual(pts, {(0, -2), (22, -2), (22, 10)})

    def test_a_slot_of_lines_and_arcs_offsets_out_and_in(self):
        a = self.sk.addGeometry(Part.LineSegment(V(0, -5, 0), V(20, -5, 0)))
        b = self.sk.addGeometry(Part.ArcOfCircle(Part.Circle(V(20, 0, 0), V(0, 0, 1), 5), -1.5707963267948966,
                                                 1.5707963267948966))
        c = self.sk.addGeometry(Part.LineSegment(V(20, 5, 0), V(0, 5, 0)))
        d = self.sk.addGeometry(Part.ArcOfCircle(Part.Circle(V(0, 0, 0), V(0, 0, 1), 5), 1.5707963267948966,
                                                 4.71238898038469))
        chain, closed = so.outline(self.sk, a)
        self.assertTrue(closed)
        self.assertEqual(sorted(chain), sorted([a, b, c, d]))
        out = so.offset_curves(self.sk, chain, True, V(10, 7, 0))
        radii = sorted(round(g.Radius, 6) for g in out if type(g).__name__ == "ArcOfCircle")
        self.assertEqual(radii, [7, 7])
        inside = so.offset_curves(self.sk, chain, True, V(10, 3, 0))
        radii = sorted(round(g.Radius, 6) for g in inside if type(g).__name__ == "ArcOfCircle")
        self.assertEqual(radii, [3, 3])

    def test_a_triangle_inward(self):
        ids = polyline(self.sk, [V(0, 0, 0), V(30, 0, 0), V(15, 25, 0)])
        inside = so.offset_curves(self.sk, ids, True, V(15, 1, 0))
        self.assertEqual(len(inside), 3)
        tri = Part.Face(Part.Wire([g.toShape() for g in inside]))
        self.assertLess(tri.Area, 375)

    def test_an_ellipse_inward_past_its_curvature_is_refused(self):
        e = self.sk.addGeometry(Part.Ellipse(V(20, 0, 0), V(0, 10, 0), V(0, 0, 0)))
        with self.assertRaises(ValueError) as err:
            so.offset_curves(self.sk, [e], True, V(0, 0, 0), distance=6)  # smallest radius of curvature 5
        self.assertEqual(str(err.exception), so.TOO_LARGE_MSG)


class Tie(Base):
    def test_the_rectangle_copy_follows_its_original_and_its_dimension(self):
        ids = polyline(self.sk, [V(0, 0, 0), V(20, 0, 0), V(20, 10, 0), V(0, 10, 0)])
        self.sk.addConstraint(Sketcher.Constraint("Horizontal", ids[0]))
        self.sk.addConstraint(Sketcher.Constraint("Horizontal", ids[2]))
        self.sk.addConstraint(Sketcher.Constraint("Vertical", ids[1]))
        self.sk.addConstraint(Sketcher.Constraint("Vertical", ids[3]))
        width = self.sk.addConstraint(Sketcher.Constraint("DistanceX", ids[0], 1, ids[0], 2, 20))
        self.doc.recompute()
        before = self.sk.GeometryCount
        new, dim = so.add(self.sk, ids, True, V(25, 5, 0))
        self.doc.recompute()
        self.assertNotIn("Invalid", self.sk.State, self.sk.getStatusString())
        self.assertEqual(len(new), 4)
        xs = lambda geos: sorted({round(p.x, 6) for c in geos for p in (c.StartPoint, c.EndPoint)})
        orig = lambda: xs([self.sk.Geometry[g] for g in ids])
        copy = lambda: xs(self.new_geo(before))
        self.assertEqual(copy(), [-5, 25])
        shown = [i for i, c in enumerate(self.sk.Constraints) if c.Type == "Distance" and not self.sk.getVirtualSpace(i)]
        self.assertEqual(len(shown), 1, "one offset dimension on view, the others hidden")
        self.sk.setDatum(width, App.Units.Quantity("30 mm"))  # the original gets wider: the copy follows
        self.doc.recompute()
        o = orig()  # (the original is not fixed in place: the solver may move it as a whole)
        self.assertAlmostEqual(o[1] - o[0], 30, 6)
        self.assertEqual(copy(), [round(o[0] - 5, 6), round(o[1] + 5, 6)])
        self.sk.setDatum(self.sk.Constraints[[c.Name for c in self.sk.Constraints].index(dim)].Name,
                         App.Units.Quantity("2 mm"))  # the offset dimension drives the copy
        self.doc.recompute()
        o = orig()
        self.assertEqual(copy(), [round(o[0] - 2, 6), round(o[1] + 2, 6)])

    def test_a_circle_copy_stays_concentric(self):
        c = self.sk.addGeometry(Part.Circle(V(0, 0, 0), V(0, 0, 1), 5))
        new, dim = so.add(self.sk, [c], True, V(8, 0, 0))
        self.doc.recompute()
        self.assertNotIn("Invalid", self.sk.State, self.sk.getStatusString())
        self.assertAlmostEqual(self.sk.Geometry[new[0]].Radius, 8, 6)
        self.sk.moveGeometry(c, 3, V(4, 1, 0))  # the original's centre moves
        self.doc.recompute()
        ctr = self.sk.Geometry[new[0]].Center
        self.assertAlmostEqual((ctr - V(4, 1, 0)).Length, 0, 6)


if __name__ == "__main__":
    unittest.main()
