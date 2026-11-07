"""Sketch constraints listed for the Constraints window: what each one is, what it ties, filtered by a pick."""
import unittest

import FreeCAD as App
import Part
import Sketcher
from incad.core import document, sketch_constraints as sc
from headless import util


def rect(sk):
    """A 60 x 45 rectangle: Lines 1-4, four corner coincidents, H/V, two dimensions d1, d2."""
    p = [App.Vector(0, 0, 0), App.Vector(60, 0, 0), App.Vector(60, 45, 0), App.Vector(0, 45, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(p[i], p[(i + 1) % 4]))
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    sk.addConstraint(Sketcher.Constraint("Horizontal", 0))
    sk.addConstraint(Sketcher.Constraint("Horizontal", 2))
    sk.addConstraint(Sketcher.Constraint("Vertical", 1))
    sk.addConstraint(Sketcher.Constraint("Vertical", 3))
    i = sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 60.0))
    sk.renameConstraint(i, "d1")
    i = sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 45.0))
    sk.renameConstraint(i, "d2")


class RowsTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Sc")
        self.body = document.make_part(self.doc)
        self.sk = self.body.newObject("Sketcher::SketchObject", "Sk")
        self.sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        self.sk.MapMode = "FlatFace"
        rect(self.sk)
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_every_constraint_is_a_row_in_order(self):
        rs = sc.rows(self.sk)
        self.assertEqual([r.index for r in rs], list(range(10)))
        self.assertEqual([r.type for r in rs[:6]], ["Coincident"] * 4 + ["Horizontal"] * 2)
        self.assertEqual(rs[0].text, "Line 1 end ↔ Line 2 start")
        self.assertEqual(rs[4].text, "Line 1")
        self.assertEqual((rs[8].type, rs[8].name, rs[8].value), ("Horizontal Distance", "d1", "60 mm"))

    def test_a_formula_shows_as_written(self):
        self.sk.setExpression("Constraints.d2", "d1 / 2")
        self.doc.recompute()
        self.assertEqual(sc.rows(self.sk)[9].value, "d1 / 2")

    def test_axes_origin_projected_and_construction_are_named(self):
        sk = self.sk
        sk.addGeometry(Part.Point(App.Vector(10, 10, 0)), True)  # Point 5 (construction)
        sk.addConstraint(Sketcher.Constraint("PointOnObject", 4, 1, -1))
        sk.addConstraint(Sketcher.Constraint("Coincident", 0, 1, -1, 1))
        self.assertEqual(sc.element_name(sk, 4, 1), "Point 5 (construction)")
        self.assertEqual(sc.element_name(sk, -1, 0), "X axis")
        self.assertEqual(sc.element_name(sk, -1, 1), "Origin")
        self.assertEqual(sc.element_name(sk, -2, 0), "Y axis")
        self.assertEqual(sc.element_name(sk, -3, 0), "Projected edge 1")
        rs = sc.rows(sk)
        self.assertEqual(rs[-2].text, "Point 5 (construction) ↔ X axis")
        self.assertEqual(rs[-1].text, "Line 1 start ↔ Origin")

    def test_filter_by_an_edge_and_by_a_vertex(self):
        self.assertEqual([r.index for r in sc.for_elements(self.sk, ["Edge1"])], [0, 3, 4, 8])
        v = self.sk.getGeoVertexIndex  # which Vertex is Line 2's start: find it, FreeCAD numbers vertices itself
        name = next("Vertex%d" % (i + 1) for i in range(8) if v(i) == (1, 1))
        self.assertEqual([r.index for r in sc.for_elements(self.sk, [name])], [0, 9])
        self.assertEqual(len(sc.for_elements(self.sk, [])), 10)

    def test_a_pattern_tie_names_its_pattern(self):
        i = self.sk.addConstraint(Sketcher.Constraint("Equal", 0, 2))
        self.sk.renameConstraint(i, "pat3_0")
        self.assertEqual(sc.rows(self.sk)[i].owner, "Pattern3")

    def test_delete_several_highest_first(self):
        n = sc.delete(self.sk, [4, 1])  # Horizontal (Line 1) and the Line 2/3 coincident
        self.doc.recompute()
        self.assertEqual(n, 2)
        left = [(r.type, r.text) for r in sc.rows(self.sk)]
        self.assertNotIn(("Horizontal", "Line 1"), left)
        self.assertNotIn(("Coincident", "Line 2 end ↔ Line 3 start"), left)
        self.assertEqual(len(left), 8)

    def test_a_pattern_tie_is_refused(self):
        i = self.sk.addConstraint(Sketcher.Constraint("Equal", 0, 2))
        self.sk.renameConstraint(i, "pat3_0")
        with self.assertRaises(ValueError) as ctx:
            sc.delete(self.sk, [0, i])
        self.assertIn("Part of Pattern3", str(ctx.exception))
        self.assertEqual(len(self.sk.Constraints), 11)  # nothing deleted

    def test_a_dimension_used_by_a_formula_is_refused_a_free_one_deleted(self):
        self.sk.setExpression("Constraints.d2", "Sk.Constraints.d1 / 2")
        self.doc.recompute()
        self.assertEqual(sc.refusal(self.sk, [8]), "d1 is used by d2: change that first")
        self.assertIsNone(sc.refusal(self.sk, [9]))
        sc.delete(self.sk, [9])
        self.assertIsNone(sc.refusal(self.sk, [8]))

    def test_used_by_a_feature_value(self):
        pad = util.add_pad(self.body, self.sk, 5.0)
        pad.setExpression("Length", "Sk.Constraints.d1 / 10")
        self.doc.recompute()
        self.assertEqual(sc.refusal(self.sk, [8]), "d1 is used by %s: change that first" % pad.Label)

    def test_delete_works_on_a_conflicting_sketch(self):
        self.sk.addConstraint(Sketcher.Constraint("Vertical", 0))  # Line 1 horizontal and vertical: conflict
        self.doc.recompute()
        self.assertEqual(sc.delete(self.sk, [10]), 1)
        self.doc.recompute()
        self.assertFalse(self.sk.ConflictingConstraints)

    def test_undo_restores_a_delete(self):
        self.doc.UndoMode = 1
        self.doc.openTransaction("Delete Constraints")
        sc.delete(self.sk, [0, 5])
        self.doc.commitTransaction()
        self.doc.undo()
        self.assertEqual(len(self.sk.Constraints), 10)

    def test_a_delete_is_not_read_as_a_replaced_dimension(self):
        from incad.core import sketch
        known = sketch.dimension_snapshot(self.sk)
        sc.delete(self.sk, [9])
        self.assertIsNone(sketch.reconcile_replaced(self.sk, known))
        self.assertNotIn("d2", [c.Name for c in self.sk.Constraints])


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Sr")
        self.body = document.make_part(self.doc)
        self.sk = self.body.newObject("Sketcher::SketchObject", "Sk")
        self.sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        self.sk.MapMode = "FlatFace"

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_delete_all_leaves_out_pattern_ties(self):
        rect(self.sk)
        i = self.sk.addConstraint(Sketcher.Constraint("Equal", 0, 2))
        self.sk.renameConstraint(i, "pat3_0")
        rows = sc.for_elements(self.sk, ["Edge1"])
        self.assertIn(i, [r.index for r in rows])
        self.assertEqual(sc.deletable(rows), [r.index for r in rows if r.index != i])

    def test_rows_of_a_big_sketch_are_quick(self):
        """Review: element_name copied the whole geometry list per tie (0.6 s for 1000 constraints)."""
        import time
        sk = self.sk
        for k in range(100):
            p = [App.Vector(k * 70, 0, 0), App.Vector(k * 70 + 60, 0, 0), App.Vector(k * 70 + 60, 45, 0),
                 App.Vector(k * 70, 45, 0)]
            base = sk.GeometryCount
            sk.addGeometry([Part.LineSegment(p[i], p[(i + 1) % 4]) for i in range(4)])
            sk.addConstraint([Sketcher.Constraint("Coincident", base + i, 2, base + (i + 1) % 4, 1) for i in range(4)]
                             + [Sketcher.Constraint("Horizontal", base), Sketcher.Constraint("Vertical", base + 1)])
        self.assertEqual(len(sk.Constraints), 600)
        t = time.time()
        rows = sc.rows(sk)
        self.assertEqual(len(rows), 600)
        self.assertLess(time.time() - t, 0.12)


class ReferenceTest(unittest.TestCase):
    def test_a_size_the_sketch_fixes_already_becomes_a_reference(self):
        """A circle between two parallel lines (tangent to both) given a diameter: the sketch stopped solving
        ("redundant constraints"); as a reference dimension it solves and shows the size (the user's air part)."""
        from incad.core import sketch
        doc = util.new_doc("Ref")
        try:
            body = document.make_part(doc)
            src = body.newObject("Sketcher::SketchObject", "Src")  # the lines projected, as in the user's sketch
            src.AttachmentSupport = [(util.xy_plane(body), "")]
            src.MapMode = "FlatFace"
            src.addGeometry(Part.LineSegment(App.Vector(-80, 0, 0), App.Vector(80, 0, 0)))
            src.addGeometry(Part.LineSegment(App.Vector(-80, 153, 0), App.Vector(80, 153, 0)))
            doc.recompute()
            sk = body.newObject("Sketcher::SketchObject", "S")
            sk.AttachmentSupport = [(util.xy_plane(body), "")]
            sk.MapMode = "FlatFace"
            sk.addExternal(src.Name, "Edge1")
            sk.addExternal(src.Name, "Edge2")
            doc.recompute()
            sk.addGeometry(Part.Circle(App.Vector(0, 76.5, 0), App.Vector(0, 0, 1), 76.5))
            sk.addConstraint(Sketcher.Constraint("Tangent", 0, -3))
            sk.addConstraint(Sketcher.Constraint("Tangent", 0, -4))
            i = sk.addConstraint(Sketcher.Constraint("Diameter", 0, 153.0))
            self.assertTrue(sketch.over_defines(sk, i))
            sketch.as_reference(sk, i)
            self.assertEqual(sk.solve(), 0)
            self.assertFalse(sk.Constraints[i].Driving)
            self.assertAlmostEqual(sk.getDatum(i).Value, 153.0, places=6)
            j = sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 3, 0.0))
            self.assertFalse(sketch.over_defines(sk, j), "a size the sketch needs stays driving")
        finally:
            App.closeDocument(doc.Name)


class TrimProjectedTest(unittest.TestCase):
    def test_break_link_then_trim_a_projected_circle(self):
        """A projected circle follows the part and is not trimmed; Break Link (as in the user's other CAD) makes it the
        sketch's own geometry of its kind (construction), the tangent tied to it kept, and then it trims."""
        from incad.core import sketch_trim
        doc = util.new_doc("Trim")
        try:
            src = doc.addObject("Sketcher::SketchObject", "Src")
            src.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 50))
            doc.recompute()
            sk = doc.addObject("Sketcher::SketchObject", "S")
            sk.addExternal("Src", "Edge1")
            doc.recompute()
            sk.addGeometry(Part.LineSegment(App.Vector(-80, 20, 0), App.Vector(80, 20, 0)))
            sk.addGeometry(Part.LineSegment(App.Vector(-80, 60, 0), App.Vector(80, 60, 0)))
            sk.addConstraint(Sketcher.Constraint("Tangent", 1, -3))
            new = sketch_trim.break_link(sk, [-3])[0]
            sk.trim(new, App.Vector(0, 49, 0))
            doc.recompute()
            self.assertEqual(sk.Geometry[new].TypeId, "Part::GeomArcOfCircle")
            self.assertTrue(sk.getConstruction(new))
            self.assertEqual(len(sk.ExternalGeo), 2, "the projection went: only the H / V axes left")
            self.assertIn(("Tangent", new, 1), [(c.Type, c.First, c.Second) for c in sk.Constraints])
            self.assertEqual(sk.solve(), 0)
        finally:
            App.closeDocument(doc.Name)
