"""Sketch ▸ Tangent Circle (incad.core.tangent_circle): a circle tangent to two or three lines, arcs or circles."""
import math
import unittest

import FreeCAD as App
import Part

from incad.core import tangent_circle

V = App.Vector


class TangentCircleTest(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("Tc")
        self.sk = self.doc.addObject("Sketcher::SketchObject", "Sketch")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def line(self, a, b):
        return self.sk.addGeometry(Part.LineSegment(V(*a, 0), V(*b, 0)))

    def test_three_lines_give_the_circle_inside_their_triangle(self):
        picks = [self.line((0, 0), (30, 0)), self.line((30, 0), (0, 40)), self.line((0, 40), (0, 0))]
        k = tangent_circle.add(self.sk, picks)
        c = self.sk.Geometry[k]
        self.assertAlmostEqual(c.Radius, (30 + 40 - 50) / 2.0, places=5)  # a right triangle's inradius
        self.assertAlmostEqual(c.Center.x, 10, places=5)
        self.assertAlmostEqual(c.Center.y, 10, places=5)

    def test_two_lines_leave_the_radius_free(self):
        picks = [self.line((0, 0), (40, 0)), self.line((0, 0), (0, 40))]
        k = tangent_circle.add(self.sk, picks)
        c = self.sk.Geometry[k]
        self.assertAlmostEqual(c.Center.x, c.Radius, places=5)
        self.assertAlmostEqual(c.Center.y, c.Radius, places=5)
        self.assertGreater(self.sk.solve(), -1)

    def test_two_lines_placed_where_the_pointer_shows_it(self):
        """Two lines picked, then a click away from them: the circle touches both, as big as shown at the pointer
        (the user: "two lines or three"; the circle follows the pointer from the first touch)."""
        picks = [self.line((0, 0), (40, 0)), self.line((0, 0), (0, 40))]
        geos = [tangent_circle.geometry(self.sk, g) for g in picks]
        centre, r = tangent_circle.preview(geos[:1], V(5, 8, 0))  # one pick: centred on the pointer, touching it
        self.assertAlmostEqual(r, 8, places=6)
        centre, r = tangent_circle.preview(geos, V(12, 12, 0))
        self.assertAlmostEqual(centre.x, r, places=5)
        self.assertAlmostEqual(centre.y, r, places=5)
        self.assertAlmostEqual(r, 12, places=4)
        k = tangent_circle.add(self.sk, picks, V(12, 12, 0))
        c = self.sk.Geometry[k]
        self.assertAlmostEqual(c.Radius, 12, places=4)
        self.assertAlmostEqual(c.Center.x, 12, places=4)

    def test_a_line_and_a_circle(self):
        a = self.line((-30, 0), (30, 0))
        b = self.sk.addGeometry(Part.Circle(V(0, 20, 0), V(0, 0, 1), 8))
        k = tangent_circle.add(self.sk, [a, b])
        g = self.sk.Geometry  # (the solver may have moved the picks a little too: measured on them as they are now)
        c, ln, other = g[k], g[a], g[b]
        self.assertAlmostEqual(tangent_circle._distance(ln, V(c.Center)), c.Radius, places=4, msg="touching the line")
        d = (V(c.Center) - V(other.Center)).Length
        self.assertTrue(abs(d - (other.Radius + c.Radius)) < 1e-4 or abs(d - abs(other.Radius - c.Radius)) < 1e-4,
                        "touching the circle")

    def test_the_picks_stay_where_they_are(self):
        a = self.line((-30, 0), (30, 0))
        b = self.sk.addGeometry(Part.Circle(V(5, 20, 0), V(0, 0, 1), 8))
        tangent_circle.add(self.sk, [a, b])
        g = self.sk.Geometry
        self.assertAlmostEqual(g[a].StartPoint.y, 0, places=6)
        self.assertAlmostEqual(g[a].EndPoint.y, 0, places=6)
        self.assertAlmostEqual((V(g[b].Center) - V(5, 20, 0)).Length, 0, places=6)

    def test_projected_edges_count_like_its_own_lines(self):
        box = self.doc.addObject("Part::Box", "Box")
        box.Length, box.Width = 30, 40
        self.doc.recompute()
        for i, e in enumerate(box.Shape.Edges, 1):  # the bottom face's edges along x at y = 0 and along y at x = 0
            a, b = e.Vertexes[0].Point, e.Vertexes[1].Point
            if a.z == b.z == 0 and (a.y == b.y == 0 or a.x == b.x == 0):
                self.sk.addExternal("Box", "Edge%d" % i)
        self.doc.recompute()
        own = self.line((30, 0), (0, 40))
        k = tangent_circle.add(self.sk, [-3, -4, own])
        c = self.sk.Geometry[k]
        self.assertAlmostEqual(c.Radius, 10, places=5)
        self.assertAlmostEqual(c.Center.x, 10, places=5)
        self.assertAlmostEqual(c.Center.y, 10, places=5)

    def test_refused(self):
        a = self.line((0, 0), (10, 0))
        with self.assertRaises(ValueError):
            tangent_circle.add(self.sk, [a])


if __name__ == "__main__":
    unittest.main()
