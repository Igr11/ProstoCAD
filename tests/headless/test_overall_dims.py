import unittest

import FreeCAD as App
import Part
import Sketcher
from incad.core import document, overall_dims, slots
from headless import util

V = App.Vector


class OverallDimsTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Od")
        self.body = document.make_part(self.doc)
        self.sk = self.body.newObject("Sketcher::SketchObject", "S")
        self.sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        self.sk.MapMode = "FlatFace"
        self.doc.recompute()

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

    def test_a_projected_circle_and_a_construction_one(self):
        """The user's law: projected and construction circles count like normal ones."""
        (ext,) = self.projected(Part.Circle(V(0, 0, 0), V(0, 0, 1), 5))
        own = self.sk.addGeometry(Part.Circle(V(20, 0, 0), V(0, 0, 1), 3), True)
        i = overall_dims.overall(self.sk, ext, own)
        self.assertAlmostEqual(self.sk.Constraints[i].Value, 28, places=6)
        self.assertEqual(overall_dims.kind_of(self.sk, i), ("overall", ext, own))

    def slot(self):
        c1, c2, r = slots.overall(V(0, 0, 0), V(40, 0, 0), V(20, 5, 0))  # 40 long, 10 wide
        return slots.add(self.sk, c1, c2, r)

    def test_a_slot_found_from_any_of_its_edges(self):
        a1, a2, l1, l2 = self.slot()
        for g in (a1, a2, l1, l2):
            self.assertEqual(set(overall_dims.slot_of(self.sk, g)), {a1, a2})
        line = self.sk.addGeometry(Part.LineSegment(V(0, 20, 0), V(10, 20, 0)))
        self.assertIsNone(overall_dims.slot_of(self.sk, line))

    def test_overall_length_of_a_slot_drives_it(self):
        a1, a2, l1, l2 = self.slot()
        dof = self.sk.solve() or self.sk.DoF
        i = overall_dims.overall(self.sk, a1, a2)
        c = self.sk.Constraints[i]
        self.assertTrue(c.Driving)
        self.assertAlmostEqual(c.Value, 40.0, places=6)
        self.assertEqual(self.sk.DoF, dof - 1)
        self.sk.setDatum(i, App.Units.Quantity("60 mm"))
        self.assertEqual(self.sk.solve(), 0)
        ends = [self.sk.Geometry[g].Center for g in (a1, a2)]
        self.assertAlmostEqual((ends[1] - ends[0]).Length + 2 * self.sk.Geometry[a1].Radius, 60.0, places=6)

    def test_a_new_overall_value_makes_the_slot_longer_not_wider(self):
        from incad.core import sketch
        a1, a2, l1, l2 = self.slot()
        i = overall_dims.overall(self.sk, a1, a2)
        sketch.name_new_dimensions(self.doc)
        count = self.sk.ConstraintCount
        sketch.set_dimension(self.doc, self.sk.Constraints[i].Name, "50")
        self.assertAlmostEqual(self.sk.Geometry[a1].Radius, 5.0, places=6)
        ends = [self.sk.Geometry[g].Center for g in (a1, a2)]
        self.assertAlmostEqual((ends[1] - ends[0]).Length, 40.0, places=6)
        self.assertEqual(self.sk.ConstraintCount, count)  # the radius was held only while it solved

    def test_with_a_centre_distance_it_is_a_reference_that_follows(self):
        a1, a2, l1, l2 = self.slot()
        cc = self.sk.addConstraint(Sketcher.Constraint("Distance", l1, 30.0))  # FreeCAD's centre to centre
        self.sk.addConstraint(Sketcher.Constraint("Radius", a1, 5.0))  # and the width: the length is fixed
        i = overall_dims.overall(self.sk, a1, a2)
        self.assertFalse(self.sk.Constraints[i].Driving)
        self.assertFalse(self.sk.RedundantConstraints)
        self.sk.setDatum(cc, App.Units.Quantity("50 mm"))
        self.sk.solve()
        self.assertAlmostEqual(self.sk.Constraints[i].Value, 60.0, places=6)

    def test_two_circles_by_their_outer_edges(self):
        a = self.sk.addGeometry(Part.Circle(V(0, 0, 0), V(0, 0, 1), 3))
        b = self.sk.addGeometry(Part.Circle(V(20, 10, 0), V(0, 0, 1), 5))
        i = overall_dims.overall(self.sk, a, b)
        self.assertAlmostEqual(self.sk.Constraints[i].Value, (V(20, 10, 0)).Length + 8, places=6)

    def test_refuses_lines_and_concentric_circles(self):
        a = self.sk.addGeometry(Part.Circle(V(0, 0, 0), V(0, 0, 1), 3))
        b = self.sk.addGeometry(Part.Circle(V(0, 0, 0), V(0, 0, 1), 5))
        line = self.sk.addGeometry(Part.LineSegment(V(0, 20, 0), V(10, 20, 0)))
        with self.assertRaises(ValueError):
            overall_dims.overall(self.sk, a, b)
        with self.assertRaises(ValueError):
            overall_dims.overall(self.sk, a, line)
        self.assertEqual(self.sk.GeometryCount, 3)  # nothing left behind


class KindsTest(OverallDimsTest):
    def gap(self):
        """A slot with FreeCAD's own Dimension on its two arcs: the gap between them (inner edges), named."""
        from incad.core import sketch
        a1, a2, l1, l2 = self.slot()
        i = self.sk.addConstraint(Sketcher.Constraint("Distance", a1, 0, a2, 0, 20.0))
        sketch.name_new_dimensions(self.doc)
        return a1, a2, i, self.sk.Constraints[i].Name

    def test_kind_of_names_the_three_ways(self):
        a1, a2, i, name = self.gap()
        self.assertEqual(overall_dims.kind_of(self.sk, i), ("gap", a1, a2))
        j = overall_dims.set_kind(self.sk, i, "overall")
        self.assertEqual(overall_dims.kind_of(self.sk, j)[0], "overall")
        self.assertEqual(self.sk.Constraints[j].Name, name)
        self.assertAlmostEqual(self.sk.Constraints[j].Value, 40.0, places=6)
        k = overall_dims.set_kind(self.sk, j, "centers")
        self.assertEqual(overall_dims.kind_of(self.sk, k)[0], "centers")
        self.assertAlmostEqual(self.sk.Constraints[k].Value, 30.0, places=6)
        self.assertEqual(self.sk.GeometryCount, 4)  # the overall's construction went with it
        m = overall_dims.set_kind(self.sk, k, "gap")
        self.assertAlmostEqual(self.sk.Constraints[m].Value, 20.0, places=6)
        self.assertEqual(self.sk.Constraints[m].Name, name)
        self.assertFalse(self.sk.RedundantConstraints or self.sk.ConflictingConstraints)

    def test_a_value_for_each_kind(self):
        from incad.core import sketch
        a1, a2, i, name = self.gap()
        overall_dims.set_kind(self.sk, i, "overall")
        sketch.set_dimension(self.doc, name, "50")
        self.assertAlmostEqual(self.sk.Geometry[a1].Radius, 5.0, places=6)  # longer, not wider
        j = next(n for n, c in enumerate(self.sk.Constraints) if c.Name == name)
        overall_dims.set_kind(self.sk, j, "centers")
        self.assertAlmostEqual(self.sk.Constraints[next(n for n, c in enumerate(self.sk.Constraints)
                                                         if c.Name == name)].Value, 40.0, places=6)

    def test_a_dimension_in_a_formula_keeps_its_kind(self):
        a1, a2, i, name = self.gap()
        self.sk.setExpression("Constraints[%d]" % self.sk.addConstraint(Sketcher.Constraint("Radius", a1, 5.0)),
                              "%s / 4" % name)
        self.doc.recompute()
        with self.assertRaises(ValueError):
            overall_dims.set_kind(self.sk, i, "overall")
        self.assertEqual(overall_dims.kind_of(self.sk, i)[0], "gap")
