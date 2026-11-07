"""Extrude ▸ To reaches any face of any Solid (flat or curved), a work or origin plane, or any point (the user): a face
of another Solid through a hidden binder (FreeCAD reads another Solid's features in its own coordinates and missed),
a point through a hidden plane parallel to the sketch; both follow when the target moves, go with the extrude, and
the dialog shows what was picked."""
import unittest

import FreeCAD as App
import Part

from incad.features import extrude
from headless import util

V = App.Vector


class ExtrudeTo(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("ExTo")
        from incad.core import document
        self.body = document.make_part(self.doc)
        self.sk = util.add_rect_sketch(self.body, 5, 5)
        self.other = self.doc.addObject("PartDesign::Body", "Other")
        self.box = self.other.newObject("PartDesign::AdditiveBox", "Box")
        self.box.Length = self.box.Width = 30
        self.box.Height = 5
        self.other.Placement = App.Placement(V(-10, -10, 20), App.Rotation())
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def make(self, to):
        k = extrude
        values = dict(k.defaults(self.doc, self.body, self.sk), operation="join", extent="to", to_face=to)
        f = k.create(self.doc, self.body, self.sk, values)
        self.doc.recompute()
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        return f

    def top(self, f):
        return round(f.Shape.BoundBox.ZMax, 6)

    def face(self, obj, test):
        return "%s:Face%d" % (obj.Name, [i + 1 for i, fc in enumerate(obj.Shape.Faces) if test(fc)][0])

    def helpers(self, f):
        return [o for o in self.body.Group if getattr(o, "InCadHelperOf", None) == f.Name]

    def test_face_of_another_solid_follows_it(self):
        f = self.make(self.face(self.box, lambda fc: fc.normalAt(0, 0).z < -0.5))  # its bottom, at z = 20
        self.assertEqual(self.top(f), 20)
        self.assertEqual([h.TypeId for h in self.helpers(f)], ["PartDesign::SubShapeBinder"])
        self.assertFalse(self.helpers(f)[0].Visibility)
        self.other.Placement = App.Placement(V(-10, -10, 32), App.Rotation())
        self.doc.recompute()
        self.assertEqual(self.top(f), 32)
        self.assertEqual(extrude.read(self.doc, f)["to_face"], self.face(self.box, lambda fc: fc.normalAt(0, 0).z < -0.5))

    def test_curved_face_of_another_solid(self):
        """A sphere over the sketch: the extrude stops on its round face."""
        ball = self.doc.addObject("PartDesign::Body", "Ball")
        sphere = ball.newObject("PartDesign::AdditiveSphere", "Sphere")
        sphere.Radius = 20
        ball.Placement = App.Placement(V(2.5, 2.5, 40), App.Rotation())
        self.doc.recompute()
        f = self.make("%s:Face1" % sphere.Name)
        import math
        self.assertAlmostEqual(f.Shape.BoundBox.ZMax, 40 - math.sqrt(400 - 2 * 2.5 ** 2), places=3)
        ball.Placement = App.Placement(V(2.5, 2.5, 50), App.Rotation())
        self.doc.recompute()
        self.assertAlmostEqual(f.Shape.BoundBox.ZMax, 50 - math.sqrt(400 - 2 * 2.5 ** 2), places=3)

    def test_point_of_another_solid_and_it_moves(self):
        v = [i + 1 for i, p in enumerate(self.box.Shape.Vertexes)
             if abs(self.other.Placement.multVec(p.Point).z - 25) < 1e-6][0]
        f = self.make("%s:Vertex%d" % (self.box.Name, v))
        self.assertEqual(self.top(f), 25)
        self.assertEqual(sorted(h.TypeId for h in self.helpers(f)), ["PartDesign::Plane", "PartDesign::SubShapeBinder"])
        self.other.Placement = App.Placement(V(-10, -10, 40), App.Rotation())
        self.doc.recompute()
        self.assertEqual(self.top(f), 45)

    def test_point_of_its_own_part_and_a_work_point(self):
        pad = util.add_pad(self.body, util.add_rect_sketch(self.body, 2, 2, "Base"), 12)
        self.body.Tip = pad
        self.doc.recompute()
        v = [i + 1 for i, p in enumerate(pad.Shape.Vertexes) if abs(p.Point.z - 12) < 1e-6][0]
        f = self.make("%s:Vertex%d" % (pad.Name, v))
        self.assertEqual(self.top(f), 12)
        self.assertEqual([h.TypeId for h in self.helpers(f)], ["PartDesign::Plane"])
        point = self.body.newObject("PartDesign::Point", "WP")
        point.MapMode = "Deactivated"
        point.Placement = App.Placement(V(50, 50, 7), App.Rotation())
        self.doc.recompute()
        extrude.update(self.doc, f, dict(extrude.read(self.doc, f), to_face=point.Name))
        self.doc.recompute()
        self.assertEqual(self.top(f), 12)  # the earlier pad is higher: the part's top stays
        self.assertEqual(round(f.AddSubShape.BoundBox.ZMax, 6), 7)

    def test_origin_plane_and_back_to_distance_drops_helpers(self):
        v = [i + 1 for i, p in enumerate(self.box.Shape.Vertexes)][0]
        f = self.make("%s:Vertex%d" % (self.box.Name, v))
        self.assertEqual(len(self.helpers(f)), 2)
        values = dict(extrude.read(self.doc, f), extent="distance", distance="4 mm")
        extrude.update(self.doc, f, values)
        self.doc.recompute()
        self.assertEqual(self.helpers(f), [])
        self.assertEqual(self.top(f), 4)
        self.assertEqual(extrude.read(self.doc, f)["to_face"], "")

    def test_join_to_cut_keeps_the_target(self):
        f = self.make(self.face(self.box, lambda fc: fc.normalAt(0, 0).z < -0.5))
        g = extrude.update(self.doc, f, dict(extrude.read(self.doc, f), operation="cut")) or f
        self.doc.recompute()
        g = [o for o in self.body.Group if o.TypeId == extrude.CUT][0]
        self.assertEqual([h.TypeId for h in self.helpers(g)], ["PartDesign::SubShapeBinder"])
        self.assertEqual(extrude.read(self.doc, g)["to_face"][:len(self.box.Name)], self.box.Name)

    def edge(self, obj, test):
        return "%s:Edge%d" % (obj.Name, [i + 1 for i, e in enumerate(obj.Shape.Edges) if test(e)][0])

    def test_edge_of_another_solid_and_it_moves(self):
        """To an edge: through its middle (a hidden work point on it), parallel to the sketch; follows the edge."""
        bottom = self.edge(self.box, lambda e: abs(e.BoundBox.ZMax) < 1e-6 and abs(e.BoundBox.ZMin) < 1e-6)  # z = 20
        f = self.make(bottom)
        self.assertEqual(self.top(f), 20)
        self.assertEqual(sorted(h.TypeId for h in self.helpers(f)),
                         ["PartDesign::Plane", "PartDesign::Point", "PartDesign::SubShapeBinder"])
        self.other.Placement = App.Placement(V(-10, -10, 28), App.Rotation())
        self.doc.recompute()
        self.assertEqual(self.top(f), 28)
        self.assertEqual(extrude.read(self.doc, f)["to_face"], bottom)

    def test_work_axis(self):
        axis = self.body.newObject("PartDesign::Line", "WA")
        axis.MapMode = "Deactivated"
        axis.Placement = App.Placement(V(0, 0, 9), App.Rotation(V(0, 1, 0), 90))  # along X, 9 mm up
        self.doc.recompute()
        f = self.make(axis.Name)
        self.assertEqual(self.top(f), 9)


class SecondSide(unittest.TestCase):
    """⇄ 2: the second side has its own extents — Distance, To Next, Through All, To (a face, an edge, a point)."""

    def setUp(self):
        self.doc = util.new_doc("Ex2")
        from incad.core import document
        self.body = document.make_part(self.doc)
        self.sk = util.add_rect_sketch(self.body, 5, 5)  # on XY at z = 0
        self.base = self.body.newObject("PartDesign::AdditiveBox", "Base")  # under the sketch: z -20 .. -10
        self.base.Length = self.base.Width = 20
        self.base.Height = 10
        self.base.Placement = App.Placement(V(-5, -5, -20), App.Rotation())
        self.below = self.doc.addObject("PartDesign::Body", "Below")  # another Solid: z -40 .. -35
        box = self.below.newObject("PartDesign::AdditiveBox", "Low")
        box.Length = box.Width = 30
        box.Height = 5
        self.below.Placement = App.Placement(V(-10, -10, -40), App.Rotation())
        self.low = box
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def make(self, **values):
        k = extrude
        v = dict(k.defaults(self.doc, self.body, self.sk), operation="join", distance="10 mm",
                 direction="two_sides", **values)
        f = k.create(self.doc, self.body, self.sk, v)
        self.doc.recompute()
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        return f

    def span(self, f):
        """Where the extrude added material (the part before it cut away)."""
        b = (f.Shape.cut(f.BaseFeature.Shape) if f.BaseFeature is not None else f.Shape).BoundBox
        return round(b.ZMin, 6), round(b.ZMax, 6)

    def test_distance(self):
        self.assertEqual(self.span(self.make(extent2="distance", distance2="3 mm")), (-3, 10))

    def test_to_next_and_through_all(self):
        f = self.make(extent2="to_next")
        self.assertEqual(round(f.AddSubShape.BoundBox.ZLength, 6), 20)  # 10 up, down to the base's top
        self.assertEqual(extrude.read(self.doc, f)["extent2"], "to_next")
        extrude.update(self.doc, f, dict(extrude.read(self.doc, f), extent2="through_all"))
        self.doc.recompute()
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        self.assertEqual(round(f.AddSubShape.BoundBox.ZLength, 6), 30)  # through the base, to its bottom
        self.assertEqual(extrude.read(self.doc, f)["extent2"], "through_all")

    def test_to_a_face_an_edge_a_point_of_another_solid(self):
        top = [i + 1 for i, fc in enumerate(self.low.Shape.Faces) if fc.normalAt(0, 0).z > 0.5][0]
        f = self.make(extent2="to", to_face2="%s:Face%d" % (self.low.Name, top))
        self.assertEqual(self.span(f), (-35, 10))
        self.assertEqual(extrude.read(self.doc, f)["to_face2"], "%s:Face%d" % (self.low.Name, top))
        low_edge = [i + 1 for i, e in enumerate(self.low.Shape.Edges)
                    if abs(e.BoundBox.ZMin) < 1e-6 and abs(e.BoundBox.ZMax) < 1e-6][0]  # its bottom: z = -40
        extrude.update(self.doc, f, dict(extrude.read(self.doc, f), to_face2="%s:Edge%d" % (self.low.Name, low_edge)))
        self.doc.recompute()
        self.assertEqual(self.span(f), (-40, 10))
        v = [i + 1 for i, p in enumerate(self.low.Shape.Vertexes) if abs(p.Point.z - 5) < 1e-6][0]
        extrude.update(self.doc, f, dict(extrude.read(self.doc, f), to_face2="%s:Vertex%d" % (self.low.Name, v)))
        self.doc.recompute()
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        self.assertEqual(self.span(f), (-35, 10))
        extrude.update(self.doc, f, dict(extrude.read(self.doc, f), extent="distance", extent2="distance",
                                         distance2="2 mm"))
        self.doc.recompute()
        self.assertEqual(self.span(f), (-2, 10))
        self.assertEqual([o for o in self.body.Group if getattr(o, "InCadHelperOf", None) == f.Name], [])

    def test_both_sides_to(self):
        """Side 1 To a work plane above, side 2 To the other Solid's face below: two sets of helpers, each kept."""
        plane = self.body.newObject("PartDesign::Plane", "Up")
        plane.MapMode = "Deactivated"
        plane.Placement = App.Placement(V(0, 0, 7), App.Rotation())
        self.doc.recompute()
        top = [i + 1 for i, fc in enumerate(self.low.Shape.Faces) if fc.normalAt(0, 0).z > 0.5][0]
        f = self.make(extent="to", to_face=plane.Name, extent2="to", to_face2="%s:Face%d" % (self.low.Name, top))
        self.assertEqual(self.span(f), (-35, 7))
        v = extrude.read(self.doc, f)
        self.assertEqual((v["to_face"], v["to_face2"]), (plane.Name, "%s:Face%d" % (self.low.Name, top)))
