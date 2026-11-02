import unittest

import FreeCAD as App
import Part
from incad.core import document, model
from incad.features import kind_for, shapes, thread
from headless import util


def plane(body, role):
    return [o for o in body.Origin.OriginFeatures if o.Role == role][0]


def sketch(body, role, name, geos, z=0.0):
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(plane(body, role), "")]
    sk.MapMode = "FlatFace"
    sk.AttachmentOffset = App.Placement(App.Vector(0, 0, z), App.Rotation())
    for g in geos:
        sk.addGeometry(g)
    return sk


class ShapesTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Sh")
        self.body = document.make_part(self.doc)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def make(self, kind, **values):
        v = dict(kind.defaults(self.doc, self.body, None), **values)
        f = kind.create(self.doc, self.body, None, v)
        self.doc.recompute()
        return f

    def circle(self, r, z=0.0, role="XY_Plane", name="C", x=0.0):
        return sketch(self.body, role, name, [Part.Circle(App.Vector(x, 0, 0), App.Vector(0, 0, 1), r)], z)

    def test_sweep(self):
        prof = self.circle(2, name="Prof")
        path = sketch(self.body, "XZ_Plane", "Path", [Part.LineSegment(App.Vector(0, 0, 0), App.Vector(0, 20, 0))])
        self.doc.recompute()
        f = self.make(shapes.SWEEP, profile=prof.Name, path=path.Name)
        self.assertEqual(f.TypeId, "PartDesign::AdditivePipe")
        self.assertAlmostEqual(f.Shape.Volume, 3.14159265 * 4 * 20, places=1)
        self.assertIs(self.body.Tip, f)
        self.assertIs(kind_for(f), shapes.SWEEP)
        r = shapes.SWEEP.read(self.doc, f)
        self.assertEqual((r["profile"], r["path"], r["operation"]), (prof.Name, path.Name, "join"))

    def test_loft_and_its_checks(self):
        a, b = self.circle(2, name="A"), self.circle(5, z=10, name="B")
        self.doc.recompute()
        f = self.make(shapes.LOFT, sections="%s,%s" % (a.Name, b.Name))
        self.assertEqual(f.TypeId, "PartDesign::AdditiveLoft")
        self.assertNotIn("Invalid", f.State)
        self.assertGreater(f.Shape.Volume, 3.14159265 * 4 * 10)
        self.assertEqual(shapes.LOFT.read(self.doc, f)["sections"], "%s,%s" % (a.Name, b.Name))
        with self.assertRaises(ValueError) as ctx:
            shapes.LOFT.create(self.doc, self.body, None, dict(shapes.LOFT.defaults(self.doc, self.body, None),
                                                             sections=a.Name))
        self.assertIn("two", str(ctx.exception))

    def test_coil_with_parameters_and_threads_stay_threads(self):
        prof = sketch(self.body, "XZ_Plane", "CoilP", [Part.Circle(App.Vector(20, 0, 0), App.Vector(0, 0, 1), 1)])
        self.doc.recompute()
        f = self.make(shapes.COIL, profile=prof.Name, pitch="5 mm", height="20 mm")
        self.assertEqual(f.TypeId, "PartDesign::AdditiveHelix")
        self.assertTrue(f.Shape.isValid())
        self.assertIs(kind_for(f), shapes.COIL)
        model.set_model_parameter(self.doc, model.bound_parameter(f, "Height"), "10 mm")
        self.assertAlmostEqual(f.Height.Value, 10, places=6)
        self.assertFalse(shapes.COIL.claims(type("T", (), {"InCadThread": "{}"})()))
        self.assertIn(shapes.COIL, [k for k in [kind_for(f)]])

    def test_draft(self):
        body, pad = self.body, util.add_pad(self.body, util.add_rect_sketch(self.body), 10)
        body.Tip = pad
        self.doc.recompute()
        sides = ",".join("%s:Face%d" % (pad.Name, i + 1) for i, fc in enumerate(pad.Shape.Faces)
                         if abs(fc.normalAt(0, 0).z) < 1e-6)
        top = "%s:Face%d" % (pad.Name, [i + 1 for i, fc in enumerate(pad.Shape.Faces)
                                        if abs(fc.CenterOfMass.z - 10) < 1e-6][0])
        f = self.make(shapes.DRAFT, faces=sides, neutral=top, angle="5 °")
        self.assertEqual(f.TypeId, "PartDesign::Draft")
        self.assertNotIn("Invalid", f.State)
        self.assertNotAlmostEqual(f.Shape.Volume, pad.Shape.Volume, places=1)
        self.assertEqual(shapes.DRAFT.read(self.doc, f)["angle"], "5°")

    def test_loft_sections_out_of_order_are_refused(self):
        a, c, b = self.circle(2, name="A"), self.circle(3, z=20, name="C"), self.circle(5, z=10, name="B")
        self.doc.recompute()
        with self.assertRaises(ValueError) as ctx:
            self.make(shapes.LOFT, sections="%s,%s,%s" % (a.Name, c.Name, b.Name))
        self.assertIn("order", str(ctx.exception))

    def test_equal_sections_out_of_order_are_refused_too(self):
        """Equal sections at heights 0, 20, 10: the loft folded back with a positive volume and was kept."""
        a, c, b = self.circle(3, name="A"), self.circle(3, z=20, name="C"), self.circle(3, z=10, name="B")
        self.doc.recompute()
        with self.assertRaises(ValueError) as ctx:
            self.make(shapes.LOFT, sections="%s,%s,%s" % (a.Name, c.Name, b.Name))
        self.assertIn("order", str(ctx.exception))

    def test_a_loft_that_cant_be_made_is_not_called_out_of_order(self):
        """Two sections in one plane: FreeCAD can't loft them; the message said "out of order"."""
        a, b = self.circle(2, name="A"), self.circle(3, name="B", x=20)
        self.doc.recompute()
        with self.assertRaises(ValueError) as ctx:
            self.make(shapes.LOFT, sections="%s,%s" % (a.Name, b.Name))
        self.assertNotIn("order", str(ctx.exception))

    def test_a_path_in_two_pieces_is_said_plainly(self):
        prof = self.circle(2, name="Prof")
        path = sketch(self.body, "XZ_Plane", "Path", [Part.LineSegment(App.Vector(0, 0, 0), App.Vector(0, 20, 0)),
                                                       Part.LineSegment(App.Vector(10, 30, 0), App.Vector(10, 50, 0))])
        self.doc.recompute()
        with self.assertRaises(ValueError) as ctx:
            self.make(shapes.SWEEP, profile=prof.Name, path=path.Name)
        self.assertIn("2 separate pieces", str(ctx.exception))

    def test_sweep_follows_a_changed_path(self):
        prof = self.circle(2, name="Prof")
        path = sketch(self.body, "XZ_Plane", "Path", [Part.LineSegment(App.Vector(0, 0, 0), App.Vector(0, 20, 0))])
        self.doc.recompute()
        f = self.make(shapes.SWEEP, profile=prof.Name, path=path.Name)
        v1 = f.Shape.Volume
        path.addGeometry(Part.LineSegment(App.Vector(0, 20, 0), App.Vector(10, 30, 0)))
        self.doc.recompute()
        self.assertGreater(f.Shape.Volume, v1 + 1)


class SweepWallTest(ShapesTest):
    """Sweep's Wall option: the tube is hollowed by a second, subtractive sweep of the profile offset inward
    along the same path (helpers under the Sweep); a closed end is a cap padded from the inner profile."""

    def tube(self, **extra):
        prof = self.circle(5, role="YZ_Plane", name="Prof")
        path = sketch(self.body, "XY_Plane", "Path", [Part.LineSegment(App.Vector(0, 0, 0), App.Vector(40, 0, 0))])
        self.doc.recompute()
        v = dict(profile="Prof", path="Path", hollow=True, wall="1 mm", ends="both_open")
        v.update(extra)
        return self.make(shapes.SWEEP, **v)

    def helpers(self, f):
        return [o for o in self.body.Group if getattr(o, "InCadHelperOf", None) == f.Name]

    def test_both_ends_open_is_a_tube(self):
        import math
        f = self.tube()
        wall = shapes.wall_of(f)
        self.assertIsNotNone(wall)
        self.assertEqual(wall.TypeId, "PartDesign::SubtractivePipe")
        self.assertEqual(wall.InCadHelperOf, f.Name)
        self.assertIs(self.body.Tip, wall)
        self.assertNotIn("Invalid", wall.State, wall.getStatusString())
        expect = math.pi * (25 - 16) * 40
        self.assertAlmostEqual(wall.Shape.Volume, expect, delta=0.02 * expect)
        kinds = sorted(o.TypeId for o in self.helpers(f))
        self.assertEqual(kinds, ["PartDesign::SubtractivePipe", "Sketcher::SketchObject"])

    def test_the_wall_follows_a_changed_profile(self):
        """The inner outline was a one-time copy: a 1 mm wall on r5 became 2 mm when the profile was made r6."""
        import math
        f = self.tube()
        prof = self.doc.getObject("Prof")
        prof.delGeometries([0])
        prof.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 6))
        self.doc.recompute()
        self.assertEqual(shapes.follow_walls(self.doc), [f.Label])
        self.doc.recompute()
        self.assertAlmostEqual(self.body.Tip.Shape.Volume, math.pi * (36 - 25) * 40, delta=1.0)
        self.assertEqual(shapes.follow_walls(self.doc), [])

    def test_a_two_mm_tube_does_not_crash(self):
        """A circle of r 1 offset inward for the wall brought the whole program down (OCC's 2D offset)."""
        prof = sketch(self.body, "YZ_Plane", "Prof", [Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 1)])
        sketch(self.body, "XY_Plane", "Path", [Part.LineSegment(App.Vector(0, 0, 0), App.Vector(40, 0, 0))])
        self.doc.recompute()
        f = self.make(shapes.SWEEP, profile="Prof", path="Path", hollow=True, wall="0.3 mm", ends="both_open")
        self.assertNotIn("Invalid", f.State)
        self.assertEqual(shapes.follow_walls(self.doc), [])

    def test_a_closed_end_keeps_its_cap(self):
        import math
        f = self.tube(ends="start_closed")
        expect = math.pi * 25 * 40 - math.pi * 16 * 39  # a 1 mm cap at the start
        self.assertAlmostEqual(self.body.Tip.Shape.Volume, expect, delta=0.02 * expect)
        self.assertEqual(shapes.SWEEP.read(self.doc, f)["ends"], "start_closed")
        f2 = shapes.SWEEP.update(self.doc, f, dict(shapes.SWEEP.read(self.doc, f), ends="end_closed"))
        self.doc.recompute()
        self.assertAlmostEqual(self.body.Tip.Shape.Volume, expect, delta=0.02 * expect)
        self.assertEqual(shapes.SWEEP.read(self.doc, f2)["ends"], "end_closed")
        bb = self.body.Tip.Shape.BoundBox
        self.assertAlmostEqual(bb.XMax, 40, places=3, msg="the end cap sits inside the tube's end")
        self.assertEqual([v for v, _ in shapes._ENDS], ["both_open", "start_closed", "end_closed"])

    def test_the_wall_hollows_the_tube_only(self):
        """A tube swept off the end face of a block: the block keeps its full volume."""
        import math
        block = util.add_pad(self.body, util.add_rect_sketch(self.body, name="Blk"), 10, name="Block")
        self.body.Tip = block
        prof = sketch(self.body, "YZ_Plane", "Prof", [Part.Circle(App.Vector(5, 5, 0), App.Vector(0, 0, 1), 3)], 20)
        path = sketch(self.body, "XY_Plane", "Path", [Part.LineSegment(App.Vector(20, 5, 0), App.Vector(60, 5, 0))], 5)
        self.doc.recompute()
        f = self.make(shapes.SWEEP, profile="Prof", path="Path", hollow=True, wall="1 mm", ends="both_open")
        expect = 2000 + math.pi * (9 - 4) * 40
        self.assertAlmostEqual(self.body.Tip.Shape.Volume, expect, delta=0.02 * expect)
        self.assertEqual(len(self.body.Tip.Shape.Solids), 1)

    def test_a_profile_flipped_on_its_plane_keeps_an_even_wall(self):
        """The user's hexagon on a work plane, its sketch flipped (MapReversed) and off the plane's origin: the wall's
        inner sketch copied the attachment without the flip, its outline mirrored off the outer one (uneven wall)."""
        import math
        r, c = 6.0, App.Vector(4, 3, 0)
        pts = [c + App.Vector(r * math.cos(math.pi / 3 * k), r * math.sin(math.pi / 3 * k), 0) for k in range(6)]
        prof = sketch(self.body, "YZ_Plane", "Hex", [Part.LineSegment(pts[k], pts[(k + 1) % 6]) for k in range(6)], 0)
        prof.MapReversed = True
        path = sketch(self.body, "XY_Plane", "Line", [Part.LineSegment(App.Vector(0, -c.x, 0),
                                                                      App.Vector(40, -c.x, 0))], 5)
        self.doc.recompute()
        self.make(shapes.SWEEP, profile="Hex", path="Line", hollow=True, wall="1 mm", ends="both_open")
        inner = [o for o in self.body.Group if o.Label.startswith("SweepWallProfile")][0]
        self.assertTrue(inner.Placement.isSame(prof.Placement, 1e-9), (inner.Placement, prof.Placement))
        outer = Part.Wire(Part.__sortEdges__(list(prof.Shape.Edges)))
        for v in inner.Shape.Vertexes:
            self.assertAlmostEqual(outer.distToShape(v)[0], 1.0, places=6)

    def test_read_round_trips_and_clearing_the_wall_removes_the_helpers(self):
        f = self.tube()
        v = shapes.SWEEP.read(self.doc, f)
        self.assertTrue(v["hollow"])
        self.assertEqual((v["wall"], v["ends"]), ("1 mm", "both_open"))
        self.assertIs(kind_for(shapes.wall_of(f)), None, "a helper row opens no dialog of its own")
        shapes.SWEEP.update(self.doc, f, dict(v, hollow=False))
        self.doc.recompute()
        self.assertIsNone(shapes.wall_of(f))
        self.assertEqual(self.helpers(f), [])
        self.assertIs(self.body.Tip, f)

    def test_a_cut_sweep_takes_no_wall(self):
        with self.assertRaises(ValueError) as cm:
            self.tube(operation="cut")
        self.assertIn("Join", str(cm.exception))

    def test_solid_sweep_has_no_helper(self):
        f = self.tube(hollow=False)
        self.assertIsNone(shapes.wall_of(f))
        self.assertIs(self.body.Tip, f)


class LoftHangGuardTest(unittest.TestCase):
    """OpenCascade hangs (minutes, never returns) lofting a section of 1-2 edges into one of many (probed
    2026-10-10: a circle into a 42-gon or more, two arcs into a 44-gon); the user's FreeCAD froze in a Loft
    preview. Such sections are refused at once with the way out."""

    def setUp(self):
        import math
        self.doc = util.new_doc("LG")
        self.body = document.make_part(self.doc)

        def section(name, z, n, turn=0.3):
            sk = self.body.newObject("Sketcher::SketchObject", name)
            sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
            sk.MapMode = "FlatFace"
            sk.AttachmentOffset = App.Placement(App.Vector(0, 0, z), App.Rotation())
            if n == 1:
                sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 10))
            else:
                pts = [App.Vector(8 * math.cos(2 * math.pi * i / n + turn), 8 * math.sin(2 * math.pi * i / n + turn), 0)
                       for i in range(n)]
                for i in range(n):
                    sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % n]))
            return sk
        self.circle = section("Round", 0, 1)
        self.many = section("Many", 20, 48)
        self.few = section("Few", 40, 6)
        self.aligned = section("Aligned", 20, 48, turn=0.0)  # the same count, vertices aligned: OpenCascade copes
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def values(self, names):
        return {"sections": names, "ruled": False, "closed": False, "operation": "join"}

    def test_a_loft_that_hangs_in_the_trial_is_refused(self):
        """A trial that does not finish in time stands for the hang (the limit set below any real loft)."""
        import time
        from incad.core import loft_guard
        old, loft_guard.LOFT_SECONDS = loft_guard.LOFT_SECONDS, 1.0
        script = loft_guard._SCRIPT
        loft_guard._SCRIPT = script.replace("f.recompute()", "import time; time.sleep(30); f.recompute()")
        loft_guard._cache.clear()
        try:
            t = time.time()
            with self.assertRaises(ValueError) as e:
                shapes.LOFT.create(self.doc, self.body, None, self.values("Round,Aligned"))
            self.assertLess(time.time() - t, 60, "a bounded trial, never a freeze")
        finally:
            loft_guard.LOFT_SECONDS, loft_guard._SCRIPT = old, script
            loft_guard._cache.clear()
        self.assertFalse([o for o in self.doc.Objects if o.TypeId == "PartDesign::AdditiveLoft"], "nothing made")
        text = str(e.exception)
        self.assertIn("Round", text)
        self.assertIn("Aligned", text)
        self.assertIn("48", text)

    def test_the_trial_tells_finishing_failing_and_plain_cases(self):
        from incad.core import loft_guard
        loft_guard._cache.clear()
        self.assertEqual(loft_guard.trial(self.body, [self.circle, self.aligned]), "ok")
        self.assertEqual(loft_guard.trial(self.body, [self.circle, self.many]), "fails")
        self.assertFalse(loft_guard.risky([1, 6]))
        self.assertTrue(loft_guard.risky([1, 48]))
        self.assertEqual(self.doc.FileName, "", "the user's document is never saved by the trial")

    def test_the_same_counts_that_do_not_hang_still_loft(self):
        f = shapes.LOFT.create(self.doc, self.body, None, self.values("Round,Aligned"))
        self.doc.recompute()
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        self.assertGreater(f.Shape.Volume, 0)

    def test_few_edges_into_many_still_lofts(self):
        f = shapes.LOFT.create(self.doc, self.body, None, self.values("Few,Many"))
        self.doc.recompute()
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        self.assertGreater(f.Shape.Volume, 0)

    def test_a_circle_into_a_hexagon_still_lofts(self):
        f = shapes.LOFT.create(self.doc, self.body, None, self.values("Round,Few"))
        self.doc.recompute()
        self.assertNotIn("Invalid", f.State, f.getStatusString())
