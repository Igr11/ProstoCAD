import unittest

import FreeCAD as App
from incad.core import section
from headless import util


class SectionTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Sc")
        self.body, self.pad = util.padded_part(self.doc, 10, incad=True)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def normal(self, plc):
        return plc.Rotation.multVec(App.Vector(0, 0, 1))

    def test_three_points(self):
        vs = self.pad.Shape.Vertexes
        top = [i + 1 for i, v in enumerate(vs) if abs(v.Point.z - 10) < 1e-6][:3]
        plc = section.placement([(self.pad, "Vertex%d" % i) for i in top])
        self.assertAlmostEqual(abs(self.normal(plc).z), 1, places=6)
        self.assertAlmostEqual(plc.Base.z, 10, places=6)

    def test_flat_face_and_work_plane(self):
        side = [i + 1 for i, f in enumerate(self.pad.Shape.Faces) if abs(f.CenterOfMass.x) < 1e-6][0]
        plc = section.placement([(self.pad, "Face%d" % side)])
        self.assertAlmostEqual(abs(self.normal(plc).x), 1, places=6)
        xz = [o for o in self.body.Origin.OriginFeatures if o.Role == "XZ_Plane"][0]
        plc = section.placement([(xz, "")])
        self.assertAlmostEqual(abs(self.normal(plc).y), 1, places=6)

    def test_origin_planes_cut_where_they_are(self):
        """An origin plane's face is endless (its centre at -5e99): the section went there and cut nothing."""
        for plane in self.body.Origin.OriginFeatures:
            if plane.Role not in ("XY_Plane", "XZ_Plane", "YZ_Plane"):
                continue
            plc = section.placement([(plane, "")])
            n = self.normal(plc)
            self.assertLess(plc.Base.Length, 1e-6, plane.Role)  # through the origin
            self.assertAlmostEqual(abs(n.dot(App.Vector(*{"XY_Plane": (0, 0, 1), "XZ_Plane": (0, 1, 0),
                                                          "YZ_Plane": (1, 0, 0)}[plane.Role]))), 1, places=6)

    def test_default_and_refusals(self):
        self.assertAlmostEqual(abs(self.normal(section.placement([])).y), 1, places=6)  # XZ
        # what can't define a plane (an edge, a whole solid) is ignored: XZ
        self.assertAlmostEqual(abs(self.normal(section.placement([(self.pad, "Edge1")])).y), 1, places=6)
        self.assertAlmostEqual(abs(self.normal(section.placement([(self.body, "")])).y), 1, places=6)
        vs = [(self.pad, "Vertex1"), (self.pad, "Vertex1"), (self.pad, "Vertex2")]
        with self.assertRaises(ValueError):
            section.placement(vs)  # not three different points


class AdjustedTest(unittest.TestCase):
    def test_offset_moves_along_the_normal_and_flip_turns_it(self):
        from incad.core import section
        base = App.Placement(App.Vector(1, 2, 3), App.Rotation(App.Vector(1, 0, 0), 90))  # normal +Z -> -Y
        n = base.Rotation.multVec(App.Vector(0, 0, 1))
        moved = section.adjusted(base, 5.0)
        self.assertAlmostEqual((moved.Base - base.Base - n * 5.0).Length, 0, places=9)
        flipped = section.adjusted(base, 0.0, True)
        self.assertAlmostEqual((flipped.Rotation.multVec(App.Vector(0, 0, 1)) + n).Length, 0, places=9)


class SnapPointsTest(unittest.TestCase):
    def test_corners_middles_and_centres(self):
        from incad.core import section
        import Part
        box = Part.makeBox(20, 10, 10)
        pts = section.snap_points(box)
        kinds = [k for k, _ in pts]
        self.assertEqual(kinds.count("corner"), 8)
        self.assertEqual(kinds.count("middle"), 12)
        cyl = Part.makeCylinder(5, 10)
        kinds = [k for k, _ in section.snap_points(cyl)]
        self.assertEqual(kinds.count("centre"), 2)  # top and bottom circle centres
        a, b, c = App.Vector(0, 0, 0), App.Vector(10, 0, 0), App.Vector(0, 10, 0)
        pl = section.through_points(a, b, c)
        self.assertAlmostEqual(abs(pl.Rotation.multVec(App.Vector(0, 0, 1)).z), 1, places=9)
        with self.assertRaises(ValueError):
            section.through_points(a, b, App.Vector(20, 0, 0))


class PurgeMarkersTest(unittest.TestCase):
    def test_old_markers_go_and_other_objects_stay(self):
        from incad.core import section
        import Part
        doc = App.newDocument("Pm")
        try:
            m = doc.addObject("Part::Feature", "Mark3")
            m.Label = "Corner 3"
            m.Shape = Part.makeSphere(1)
            keep = doc.addObject("Part::Feature", "Mark4")  # a user's own object with another label
            keep.Label = "My ball"
            keep.Shape = Part.makeSphere(1)
            self.assertEqual(section.purge_markers(doc), 1)
            self.assertIsNone(doc.getObject("Mark3"))
            self.assertIsNotNone(doc.getObject("Mark4"))
        finally:
            App.closeDocument(doc.Name)


class CutShapeTest(unittest.TestCase):
    def test_half_of_a_box_and_markers_skip_thread_edges(self):
        from incad.core import section
        import Part
        box = Part.makeBox(20, 10, 10)
        plane = App.Placement(App.Vector(10, 0, 0), App.Rotation(App.Vector(0, 0, 1), App.Vector(1, 0, 0)))  # x = 10
        half = section.cut_shape(box, plane)
        self.assertAlmostEqual(half.Volume, 1000, places=3)
        self.assertAlmostEqual(half.BoundBox.XMax, 10, places=6)
        other = section.cut_shape(box, plane, flip=True)
        self.assertAlmostEqual(other.BoundBox.XMin, 10, places=6)
        helix = Part.makeHelix(1.0, 10.0, 4.0)
        comp = Part.makeCompound([box] + helix.Edges)
        kinds = [k for k, _ in section.snap_points(comp)]
        self.assertEqual(kinds.count("corner"), 8)  # the helix adds nothing


class CapFacesTest(unittest.TestCase):
    def test_cross_section_of_a_bored_box(self):
        from incad.core import section
        import Part
        box = Part.makeBox(20, 10, 10).cut(Part.makeCylinder(2, 10, App.Vector(10, 5, 0)))
        plane = App.Placement(App.Vector(10, 0, 0), App.Rotation(App.Vector(0, 0, 1), App.Vector(1, 0, 0)))  # x = 10
        faces = section.cap_faces(box, plane)
        self.assertEqual(len(faces), 2)  # the bore's slot splits the 10 x 10 section in two
        self.assertAlmostEqual(sum(f.Area for f in faces), 100 - 4 * 10, places=3)
        self.assertEqual(section.cap_faces(box, section.adjusted(plane, 50.0)), [])  # misses the part


class CapsTest(unittest.TestCase):
    def test_mesh_caps_match_the_solid_and_fall_back(self):
        from incad.core import section
        import Part
        box = Part.makeBox(20, 10, 10).cut(Part.makeCylinder(2, 10, App.Vector(10, 5, 0)))
        caps = section.Caps(box)
        plane = App.Placement(App.Vector(10, 0, 0), App.Rotation(App.Vector(0, 0, 1), App.Vector(1, 0, 0)))
        faces = caps.faces(plane)
        self.assertEqual(len(faces), 2)  # two rectangles beside the bore
        for f in faces:
            self.assertAlmostEqual(f.Area, 30.0, delta=0.3)
        ring = App.Placement(App.Vector(0, 5, 0), App.Rotation(App.Vector(1, 0, 0), -90))  # through the bore's axis? no: y = 5
        faces = caps.faces(App.Placement(App.Vector(0, 0, 5), App.Rotation()))  # z = 5: the box minus the bore's circle
        self.assertEqual(len(faces), 1)
        self.assertAlmostEqual(faces[0].Area, 200 - 3.14159 * 4, delta=0.5)
        self.assertEqual(len(faces[0].Wires), 2)  # the hole is a hole
        self.assertEqual(caps.faces(section.adjusted(plane, 50.0)), [])
        caps.mesh = None  # no mesh: the solid answers
        self.assertAlmostEqual(sum(f.Area for f in caps.faces(plane)), 60, places=3)


class GearCapsTest(unittest.TestCase):
    """The user (Windows): a gear's section was sometimes empty, as if made of zero-thickness surfaces. A plane
    through the mesh's vertices (a gear's middle) left holes in the loop; a worm's axial section came out in bits."""
    def test_gear_and_worm_cuts_are_whole(self):
        import Part
        from incad.core import gears
        V = App.Vector
        for name, solid in (("spur", gears.gear_solid(2, 20, width=10, bore=8)),
                            ("helical", gears.gear_solid(2, 20, helix=20, width=10, bore=8)),
                            ("worm", gears.worm_solid(2, 1, 20, 40, bore=8))):
            caps = section.Caps(solid)
            mid = solid.BoundBox.Center
            for rot in (App.Rotation(), App.Rotation(V(1, 0, 0), 90), App.Rotation(V(0, 1, 0), 90)):
                pl = App.Placement(mid, rot)
                exact = sum(f.Area for f in section.cap_faces(solid, pl))
                got = sum(f.Area for f in caps.faces(pl))
                self.assertAlmostEqual(got, exact, delta=0.01 * exact, msg="%s %s" % (name, rot.toEuler()))


class HatchTest(unittest.TestCase):
    def test_hatch_stays_inside_the_loops(self):
        from incad.core import section
        import Part
        box = Part.makeBox(20, 10, 10).cut(Part.makeCylinder(2, 10, App.Vector(10, 5, 0)))
        plane = App.Placement(App.Vector(0, 0, 5), App.Rotation())  # z = 5: the box minus the bore
        caps = section.Caps(box)
        loops = caps.loops(plane)
        segs = section.hatch(loops, plane, 1.0)
        self.assertGreater(len(segs), 20)
        face = caps.faces(plane)[0]
        for a, b in segs:
            mid = (a + b) * 0.5
            self.assertAlmostEqual(mid.z, 5.0, places=6)
            self.assertTrue(face.isInside(mid, 1e-4, True), mid)  # never across the bore
        self.assertEqual(section.hatch([], plane, 1.0), [])
        tilted = App.Placement(App.Vector(0, 5, 0), App.Rotation(App.Vector(1, 0, 0), 90))  # the XZ plane at y = 5
        loops = caps.loops(tilted)
        segs = section.hatch(loops, tilted, 1.0)
        faces = caps.faces(tilted)  # two: the bore splits this cut
        self.assertEqual(len(faces), 2)
        self.assertGreater(len(segs), 20)
        for a, b in segs:
            mid = (a + b) * 0.5
            self.assertAlmostEqual(mid.y, 5.0, places=6)
            self.assertTrue(any(f.isInside(mid, 1e-4, True) for f in faces), mid)

    def test_faces_keep_the_loops_they_were_built_from(self):
        """The slider redraws face and hatch on every move: the loops are sliced once, not twice."""
        from incad.core import section
        import Part
        caps = section.Caps(Part.makeBox(20, 10, 10))
        plane = App.Placement(App.Vector(0, 0, 5), App.Rotation())
        caps.faces(plane)
        again = caps.loops(plane)
        self.assertEqual(len(caps.last_loops), len(again))
        for a, b in zip(caps.last_loops, again):
            self.assertEqual(len(a), len(b))
            self.assertTrue(all((p - q).Length < 1e-9 for p, q in zip(a, b)))

    def test_hatch_of_a_fine_loop_is_quick(self):
        """A threaded shaft gives thousands of loop points; hatching must stay well under a slider tick."""
        import math
        import time
        from incad.core import section
        n = 20000
        loop = [App.Vector(50 * math.cos(2 * math.pi * i / n), 50 * math.sin(2 * math.pi * i / n), 0) for i in range(n)]
        plane = App.Placement()
        t0 = time.time()
        segs = section.hatch([loop], plane, 100.0 / 45.0)
        dt = time.time() - t0
        self.assertGreater(len(segs), 40)
        self.assertLess(dt, 0.4, "hatch took %.2f s" % dt)
        for a, b in segs:  # every segment inside the circle, lying along the 45° direction
            self.assertLess(max(a.Length, b.Length), 50.0 + 1e-6)
            d = b - a
            self.assertAlmostEqual(d.x, d.y, places=6)


class TouchingSolidsTest(unittest.TestCase):
    """A part of Solids that touch (a lid on its housing): cut as one, their outlines ran together into one wrong
    face and the section showed hollow (the user 2026-11-09). Each Solid is cut on its own now."""

    def setUp(self):
        from incad.core import document
        self.doc = util.new_doc("Touch")
        body, _pad = util.padded_part(self.doc, 10, incad=True)  # 20 x 10 x 10, z 0..10
        lid = document.new_solid(self.doc)
        sk = util.add_rect_sketch(lid)
        pad = util.add_pad(lid, sk, 4)
        lid.Tip = pad
        lid.Placement = App.Placement(App.Vector(0, 0, 10), App.Rotation())  # on top of the first: they touch
        self.doc.recompute()
        self.solids = (body, lid)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_each_solid_gets_its_own_cut(self):
        from incad.ui import shell
        targets = shell.Shell._section_targets(shell.Shell, self.doc)
        self.assertEqual(len(targets), 2)
        mid = self.solids[0].Shape.BoundBox.Center
        plane = App.Placement(mid, App.Rotation(App.Vector(1, 0, 0), 90))  # the XZ plane through the middle
        areas = sorted(sum(f.Area for f in section.Caps(sh).faces(plane)) for sh, _colour in targets)
        self.assertAlmostEqual(areas[0], 20 * 4, delta=0.5)
        self.assertAlmostEqual(areas[1], 20 * 10, delta=0.5)
