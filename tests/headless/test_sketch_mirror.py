"""Sketch ▸ Mirror, InCad's own (incad.core.sketch_mirror): the copy follows its original through Symmetric ties
alone, the original's dimensions never come along; Break Mirror Link frees the copy with its own joins kept."""
import math
import unittest

import FreeCAD as App
import Part
import Sketcher
from incad.core import document, safe, sketch_mirror as mir, sketch_symmetric as sym
from incad.core import sketch as sketches
from headless import util

V = App.Vector
DIMS = ("Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle")


class MirrorTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Mir")
        self.body = document.make_part(self.doc)
        sk = self.body.newObject("Sketcher::SketchObject", "S")
        sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        sk.MapMode = "FlatFace"
        self.sk = sk

    def tearDown(self):
        App.closeDocument(self.doc.Name)

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

    def test_about_a_projected_line(self):
        (ext,) = self.projected(Part.LineSegment(V(5, -10, 0), V(5, 10, 0)))
        ids = self.rectangle(-10, 0, 4, 3)
        r = safe.run_safely("Mirror", lambda: mir.mirror(self.sk, ids, ("edge", ext)), self.doc)
        self.assertTrue(r.ok, r.error)
        self.doc.recompute()
        xs = sorted(round(self.sk.getPoint(g, k).x, 6) for g in r.value for k in (1, 2))
        self.assertEqual((xs[0], xs[-1]), (16.0, 20.0))

    def rectangle(self, x0, y0, w, h):
        sk = self.sk
        pts = [V(x0, y0, 0), V(x0 + w, y0, 0), V(x0 + w, y0 + h, 0), V(x0, y0 + h, 0)]
        ids = [sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4])) for i in range(4)]
        for i in range(4):
            sk.addConstraint(Sketcher.Constraint("Coincident", ids[i], 2, ids[(i + 1) % 4], 1))
        sk.addConstraint(Sketcher.Constraint("Horizontal", ids[0]))
        sk.addConstraint(Sketcher.Constraint("Horizontal", ids[2]))
        sk.addConstraint(Sketcher.Constraint("Vertical", ids[1]))
        sk.addConstraint(Sketcher.Constraint("Vertical", ids[3]))
        self.w = sk.addConstraint(Sketcher.Constraint("DistanceX", ids[0], 1, ids[0], 2, w))
        sk.addConstraint(Sketcher.Constraint("DistanceY", ids[1], 1, ids[1], 2, h))
        self.doc.recompute()
        return ids

    def run_(self, fn):
        r = safe.run_safely("Mirror", fn, self.doc)
        self.doc.recompute()
        return r

    def dims(self):
        return sum(1 for c in self.sk.Constraints if c.Type in DIMS)

    def test_copy_follows_without_copied_dimensions(self):
        ids = self.rectangle(-30, 5, 10, 6)
        before = self.dims()
        r = self.run_(lambda: mir.mirror(self.sk, ids, ("edge", sym.V_AXIS)))
        self.assertTrue(r.ok, r.error)
        copies = r.value
        self.assertEqual(len(copies), 4)
        self.assertEqual(self.dims(), before, "the original's dimensions came along")
        self.assertEqual(self.sk.solve(), 0)
        for o, c in zip(ids, copies):
            self.assertAlmostEqual(self.sk.getPoint(o, 1).x, -self.sk.getPoint(c, 1).x, places=6)
        self.sk.setDatum(self.w, App.Units.Quantity("14 mm"))
        self.doc.recompute()
        bb = Part.Compound([self.sk.Geometry[c].toShape() for c in copies]).BoundBox
        self.assertAlmostEqual(bb.XLength, 14, places=6)  # the copy follows the original's width
        self.assertTrue(mir.is_linked(self.sk, copies))

    def test_break_link_keeps_the_shape_and_frees_it(self):
        ids = self.rectangle(-30, 5, 10, 6)
        copies = self.run_(lambda: mir.mirror(self.sk, ids, ("edge", sym.V_AXIS))).value
        self.assertFalse(mir.is_linked(self.sk, ids), "the original was taken for a copy")
        r = self.run_(lambda: mir.break_link(self.sk, ids))
        self.assertFalse(r.ok)  # from the original: nothing to break (it would strip the copy)
        r = self.run_(lambda: mir.break_link(self.sk, copies[:1] + copies[1:]))
        self.assertTrue(r.ok, r.error)
        self.assertFalse(mir.is_linked(self.sk, copies))
        types = [c.Type for c in self.sk.Constraints]
        self.assertEqual(types.count("Symmetric"), 0)
        self.assertEqual(types.count("Coincident"), 8)  # the copy's own corners joined
        self.assertEqual(types.count("Horizontal") + types.count("Vertical"), 8)
        self.assertEqual(self.dims(), 2)
        self.sk.setDatum(self.w, App.Units.Quantity("16 mm"))
        self.doc.recompute()
        bb = Part.Compound([self.sk.Geometry[c].toShape() for c in copies]).BoundBox
        self.assertAlmostEqual(bb.XLength, 10, places=6)  # no longer follows
        self.assertEqual(self.sk.solve(), 0)
        with self.assertRaises(ValueError):
            mir.break_link(self.sk, copies)  # nothing left to break

    def test_slanted_mirror_line_gives_no_false_horizontal(self):
        ids = self.rectangle(-30, 5, 10, 6)
        axis = self.sk.addGeometry(Part.LineSegment(V(0, -20, 0), V(10, 20, 0)), True)
        self.sk.addConstraint(Sketcher.Constraint("Block", axis))
        self.doc.recompute()
        copies = self.run_(lambda: mir.mirror(self.sk, ids, ("edge", axis))).value
        shape_before = [V(self.sk.getPoint(c, 1)) for c in copies]
        r = self.run_(lambda: mir.break_link(self.sk, copies))
        self.assertTrue(r.ok, r.error)
        for c, p in zip(copies, shape_before):
            self.assertAlmostEqual((V(self.sk.getPoint(c, 1)) - p).Length, 0, places=6)  # not turned by a Horizontal
        types = [self.sk.Constraints[i].Type for i in range(self.sk.ConstraintCount)]
        self.assertEqual(types.count("Horizontal") + types.count("Vertical"), 4)  # only the original's

    def test_circle_and_arc_follow_their_radius(self):
        c = self.sk.addGeometry(Part.Circle(V(-12, 3, 0), V(0, 0, 1), 4))
        a = self.sk.addGeometry(Part.ArcOfCircle(Part.Circle(V(-8, -10, 0), V(0, 0, 1), 3), 0.3, 2.0))
        rad = self.sk.addConstraint(Sketcher.Constraint("Radius", c, 4))
        self.doc.recompute()
        r = self.run_(lambda: mir.mirror(self.sk, [c, a], ("edge", sym.V_AXIS)))
        self.assertTrue(r.ok, r.error)
        cc, ca = r.value
        self.assertEqual(self.sk.Geometry[ca].Axis.z, 1.0)  # built facing up
        oa, cp = self.sk.Geometry[a], self.sk.Geometry[ca]
        self.assertAlmostEqual((V(-oa.StartPoint.x, oa.StartPoint.y, 0) - cp.EndPoint).Length, 0, places=6)
        self.sk.setDatum(rad, App.Units.Quantity("6 mm"))
        self.doc.recompute()
        self.assertAlmostEqual(self.sk.Geometry[cc].Radius, 6, places=6)

    def test_about_a_point(self):
        ids = self.rectangle(4, 4, 6, 3)
        r = self.run_(lambda: mir.mirror(self.sk, ids, sym.ROOT))
        self.assertTrue(r.ok, r.error)
        for o, c in zip(ids, r.value):
            self.assertAlmostEqual((self.sk.getPoint(o, 1) + self.sk.getPoint(c, 1)).Length, 0, places=6)

    def test_refusals(self):
        line = self.sk.addGeometry(Part.LineSegment(V(-5, 0, 0), V(-1, 4, 0)))
        arc = self.sk.addGeometry(Part.ArcOfCircle(Part.Circle(V(0, 8, 0), V(0, 0, 1), 2), 0, 1))
        self.doc.recompute()
        for geos, axis, word in (([line], ("edge", arc), "straight line"), ([line], ("edge", line), "shapes")):
            r = self.run_(lambda: mir.mirror(self.sk, geos, axis))
            self.assertFalse(r.ok)
            self.assertIn(word, r.error)
        r = self.run_(lambda: mir.break_link(self.sk, [line]))
        self.assertFalse(r.ok)
        self.assertIn("No mirror link", r.error)

    def test_about_its_own_closing_line_the_copy_joins_up(self):
        """The user's case: a chain mirrored about its own last segment — the shared end lies on the mirror line, a
        Symmetric tie there is partially redundant (FreeCAD); the copy shares that point instead."""
        P = [V(3.109, -9.109, 0), V(6.043, -6.309, 0), V(11.043, -10.443, 0), V(17.709, -8.976, 0),
             V(26.376, -11.976, 0), V(28.909, -7.909, 0)]
        ch = [self.sk.addGeometry(Part.LineSegment(P[i], P[i + 1])) for i in range(5)]
        for i in range(4):
            self.sk.addConstraint(Sketcher.Constraint("Coincident", ch[i], 2, ch[i + 1], 1))
        for g in ch:
            self.sk.addConstraint(Sketcher.Constraint("Distance", g, self.sk.Geometry[g].length()))
        self.doc.recompute()
        r = self.run_(lambda: mir.mirror(self.sk, ch, ("edge", ch[4])))  # the mirror line among the shapes
        self.assertTrue(r.ok, r.error)
        self.assertEqual(len(r.value), 4)
        self.assertEqual(self.sk.solve(), 0)
        last_copy = r.value[-1]
        self.assertAlmostEqual((self.sk.getPoint(last_copy, 2) - self.sk.getPoint(ch[3], 2)).Length, 0, places=6)

    def test_undo_takes_the_whole_mirror(self):
        ids = self.rectangle(-30, 5, 10, 6)
        count = self.sk.GeometryCount
        self.run_(lambda: mir.mirror(self.sk, ids, ("edge", sym.V_AXIS)))
        self.doc.undo()
        self.doc.recompute()
        self.assertEqual(self.sk.GeometryCount, count)


if __name__ == "__main__":
    unittest.main()
