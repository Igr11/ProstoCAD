"""Key points of a sketch for the view markers: spline poles, segment ends, circle centres."""
import math
import unittest

import FreeCAD as App
import Part
from incad.core import sketch
from headless import util


def _sketch(doc, name="S"):
    body = doc.addObject("PartDesign::Body", "Body")
    s = body.newObject("Sketcher::SketchObject", name)
    s.AttachmentSupport = [(util.xy_plane(body), "")]
    s.MapMode = "FlatFace"
    return s


def _spline(poles):
    c = Part.BSplineCurve()
    c.buildFromPoles(poles)
    return c


class OpenContourTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Kp")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_chain_of_lines_is_open(self):
        s = _sketch(self.doc)
        s.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)))
        s.addGeometry(Part.LineSegment(App.Vector(10, 0, 0), App.Vector(10, 5, 0)))
        self.doc.recompute()
        self.assertTrue(sketch.is_open_contour(s))

    def test_rectangle_is_not_open(self):
        body = self.doc.addObject("PartDesign::Body", "Body")
        s = util.add_rect_sketch(body)
        self.doc.recompute()
        self.assertFalse(sketch.is_open_contour(s))

    def test_empty_sketch_is_not_open(self):
        s = _sketch(self.doc)
        self.doc.recompute()
        self.assertFalse(sketch.is_open_contour(s))

    def test_construction_only_is_not_open(self):
        s = _sketch(self.doc)
        s.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)), True)
        self.doc.recompute()
        self.assertFalse(sketch.is_open_contour(s))


class KeyPointsTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Kp2")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    @staticmethod
    def _xy(points):
        return sorted((k, round(p.x, 3), round(p.y, 3), round(p.z, 3)) for k, p in points)

    def test_line_chain_merges_the_shared_vertex(self):
        s = _sketch(self.doc)
        s.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)))
        s.addGeometry(Part.LineSegment(App.Vector(10, 0, 0), App.Vector(10, 5, 0)))
        self.doc.recompute()
        self.assertEqual(self._xy(sketch.key_points(s)),
                         [("end", 0.0, 0.0, 0.0), ("end", 10.0, 0.0, 0.0), ("end", 10.0, 5.0, 0.0)])

    def test_arc_gives_its_ends_and_circle_its_centre(self):
        s = _sketch(self.doc)
        s.addGeometry(Part.ArcOfCircle(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 10), 0, math.pi / 2))
        s.addGeometry(Part.Circle(App.Vector(30, 0, 0), App.Vector(0, 0, 1), 3))
        self.doc.recompute()
        self.assertEqual(self._xy(sketch.key_points(s)),
                         [("centre", 30.0, 0.0, 0.0), ("end", 0.0, 10.0, 0.0), ("end", 10.0, 0.0, 0.0)])

    def test_spline_gives_its_poles_not_its_ends(self):
        s = _sketch(self.doc)
        s.addGeometry(_spline([App.Vector(0, 0, 0), App.Vector(5, 8, 0), App.Vector(10, 0, 0)]))
        self.doc.recompute()
        self.assertEqual(self._xy(sketch.key_points(s)),
                         [("pole", 0.0, 0.0, 0.0), ("pole", 5.0, 8.0, 0.0), ("pole", 10.0, 0.0, 0.0)])

    def test_spline_through_points_marks_the_points_it_passes(self):
        """A spline drawn through points (FreeCAD's B-spline by knots, a path for a sweep): the points on the
        line are marked as well as its poles."""
        s = _sketch(self.doc)
        c = Part.BSplineCurve()
        c.interpolate([App.Vector(0, 0, 0), App.Vector(5, 8, 0), App.Vector(10, 0, 0), App.Vector(15, 4, 0)])
        s.addGeometry(c)
        self.doc.recompute()
        marks = self._xy(sketch.key_points(s))
        ends = [(x, y) for k, x, y, _ in marks if k == "end"]
        for p in ((5.0, 8.0), (10.0, 0.0)):
            self.assertIn(p, ends, marks)
        everything = [(x, y) for _, x, y, _ in marks]
        for p in ((0.0, 0.0), (15.0, 4.0)):  # the ends sit on the first and last pole: marked once
            self.assertIn(p, everything, marks)

    def test_construction_geometry_is_marked_too(self):
        """The user's law: normal and construction geometry have equal rights."""
        s = _sketch(self.doc)
        s.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)))
        s.addGeometry(Part.Circle(App.Vector(50, 50, 0), App.Vector(0, 0, 1), 2), True)
        self.doc.recompute()
        self.assertEqual([k for k, _ in sketch.key_points(s)], ["end", "end", "centre"])

    def test_points_are_global(self):
        s = _sketch(self.doc)
        s.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)))
        s.AttachmentOffset = App.Placement(App.Vector(0, 0, 7), App.Rotation())
        self.doc.recompute()
        self.assertEqual(self._xy(sketch.key_points(s)), [("end", 0.0, 0.0, 7.0), ("end", 10.0, 0.0, 7.0)])

    def test_projected_edges_are_marked_only_for_building_commands(self):
        """Projected edges are the part's own edges: no points on them in the plain view (the user's air2: a ring of
        dots round a projected disc), their ends in a feature dialog (building commands pick on them)."""
        src = _sketch(self.doc, "Src")
        src.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)))
        self.doc.recompute()
        s = src.getParentGeoFeatureGroup().newObject("Sketcher::SketchObject", "S")
        s.AttachmentSupport = [(util.xy_plane(src.getParentGeoFeatureGroup()), "")]
        s.MapMode = "FlatFace"
        s.addExternal(src.Name, "Edge1")
        self.doc.recompute()
        self.assertEqual(sketch.key_points(s), [])
        self.assertEqual(self._xy(sketch.key_points(s, projected=True)), [("end", 0.0, 0.0, 0.0), ("end", 10.0, 0.0, 0.0)])

    def test_point_geometry_is_an_end(self):
        s = _sketch(self.doc)
        s.addGeometry(Part.Point(App.Vector(3, 4, 0)))
        self.doc.recompute()
        self.assertEqual(self._xy(sketch.key_points(s)), [("end", 3.0, 4.0, 0.0)])
