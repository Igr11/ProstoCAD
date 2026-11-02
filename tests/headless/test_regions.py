import math
import unittest

import FreeCAD as App
import Part
from incad.core import document, regions
from headless import util


def crossing_sketch(body, name="Cross"):
    """A 20 x 10 rectangle cut in two by a line, a circle inside the right half."""
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(util.xy_plane(body), "")]
    sk.MapMode = "FlatFace"
    pts = [App.Vector(0, 0, 0), App.Vector(20, 0, 0), App.Vector(20, 10, 0), App.Vector(0, 10, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
    sk.addGeometry(Part.LineSegment(App.Vector(10, -5, 0), App.Vector(10, 15, 0)))
    sk.addGeometry(Part.Circle(App.Vector(15, 5, 0), App.Vector(0, 0, 1), 3))
    return sk


class RegionsTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Rg")
        self.body = document.make_part(self.doc)
        self.sk = crossing_sketch(self.body)
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_crossings_are_detected_and_a_plain_rectangle_is_not(self):
        self.assertTrue(regions.needs_regions(self.sk))
        plain = util.add_rect_sketch(self.body, name="Plain")
        self.doc.recompute()
        self.assertFalse(regions.needs_regions(plain))

    def test_closed_shapes_that_cross_or_touch_go_to_areas(self):
        """Two overlapping rectangles were padded as a wrong solid (1000 for 2750 mm3), two sharing a side failed
        ("invalid geometry"), a circle touching the inside of a side was dropped: closed shapes that cross or touch
        are areas to click, as lines that cross; nested or apart ones stay a plain profile."""
        def sketch(name, shapes):
            sk = self.body.newObject("Sketcher::SketchObject", name)
            sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
            sk.MapMode = "FlatFace"
            for kind, a, b, c, d in shapes:
                if kind == "r":
                    p = [App.Vector(a, b, 0), App.Vector(a + c, b, 0), App.Vector(a + c, b + d, 0),
                         App.Vector(a, b + d, 0)]
                    for i in range(4):
                        sk.addGeometry(Part.LineSegment(p[i], p[(i + 1) % 4]))
                else:
                    sk.addGeometry(Part.Circle(App.Vector(a, b, 0), App.Vector(0, 0, 1), c))
            self.doc.recompute()
            return sk
        for name, shapes in (("Overlap", [("r", 0, 0, 20, 10), ("r", 15, 5, 10, 10)]),
                             ("SharedSide", [("r", 0, 0, 20, 10), ("r", 20, 0, 10, 10)]),
                             ("TouchInside", [("r", 0, 0, 20, 10), ("c", 16, 5, 4, 0)])):
            self.assertTrue(regions.needs_regions(sketch(name, shapes)), name)
        for name, shapes in (("Hole", [("r", 0, 0, 20, 10), ("c", 10, 5, 3, 0)]),
                             ("Apart", [("r", 0, 0, 20, 10), ("r", 30, 0, 10, 10)])):
            self.assertFalse(regions.needs_regions(sketch(name, shapes)), name)

    def test_areas_between_the_lines(self):
        areas = [round(f.Area, 2) for f in regions.regions(self.sk)]
        self.assertEqual(areas, [100.0, round(100 - math.pi * 9, 2), round(math.pi * 9, 2)])

    def test_area_under_a_point(self):
        self.assertAlmostEqual(regions.region_at(self.sk, App.Vector(5, 5, 0)).Area, 100.0, places=4)
        self.assertAlmostEqual(regions.region_at(self.sk, App.Vector(15, 5, 0)).Area, math.pi * 9, places=4)
        self.assertIsNone(regions.region_at(self.sk, App.Vector(50, 50, 0)))

    def test_profile_sketch_extrudes_the_chosen_area_with_its_hole(self):
        right = regions.region_at(self.sk, App.Vector(12, 2, 0))
        prof = regions.make_profile(self.doc, self.body, self.sk, right)
        self.assertTrue(regions.is_profile(prof) and regions.source_of(prof) is self.sk)
        self.assertEqual(regions.profiles_of(self.sk), [prof])
        kinds = sorted(g.TypeId.split("::")[1] for g in prof.Geometry)
        self.assertEqual(kinds, ["GeomCircle", "GeomLineSegment", "GeomLineSegment", "GeomLineSegment", "GeomLineSegment"])
        pad = util.add_pad(self.body, prof, 5)
        self.body.Tip = pad
        self.doc.recompute()
        self.assertEqual(pad.State, ["Up-to-date"], pad.getStatusString())
        self.assertAlmostEqual(pad.Shape.Volume, (100 - math.pi * 9) * 5, places=3)

    def test_profile_follows_the_source_sketch(self):
        left = regions.region_at(self.sk, App.Vector(5, 5, 0))
        prof = regions.make_profile(self.doc, self.body, self.sk, left)
        pad = util.add_pad(self.body, prof, 5)
        self.body.Tip = pad
        self.doc.recompute()
        self.assertAlmostEqual(pad.Shape.Volume, 500, places=3)
        line = [i for i, g in enumerate(self.sk.Geometry) if g.TypeId == "Part::GeomLineSegment"
                and abs(g.StartPoint.x - 10) < 1e-9][0]
        self.sk.delGeometry(line)  # the dividing line redrawn further right
        self.sk.addGeometry(Part.LineSegment(App.Vector(11, -5, 0), App.Vector(11, 15, 0)))
        self.doc.recompute()
        self.assertTrue(regions.update(prof))
        self.doc.recompute()
        self.assertAlmostEqual(pad.Shape.Volume, 11 * 10 * 5, places=2)

    def test_a_stray_line_inside_is_not_part_of_the_outline(self):
        sk = util.add_rect_sketch(self.body, name="Stray")
        sk.addGeometry(Part.LineSegment(App.Vector(2, 2, 0), App.Vector(8, 2, 0)))
        self.doc.recompute()
        self.assertTrue(regions.needs_regions(sk))
        faces = regions.regions(sk)
        self.assertEqual(len(faces), 1)
        prof = regions.make_profile(self.doc, self.body, sk, faces[0])
        self.assertEqual(len(prof.Geometry), 4)
        pad = util.add_pad(self.body, prof, 10)
        self.doc.recompute()
        self.assertTrue(pad.Shape.isValid())
        self.assertAlmostEqual(pad.Shape.Volume, 2000, places=3)

    def test_arc_outline(self):
        sk = self.body.newObject("Sketcher::SketchObject", "Arcs")
        sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        sk.MapMode = "FlatFace"
        sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 10))
        sk.addGeometry(Part.LineSegment(App.Vector(-15, 0, 0), App.Vector(15, 0, 0)))  # a circle halved
        self.doc.recompute()
        self.assertTrue(regions.needs_regions(sk))
        upper = regions.region_at(sk, App.Vector(0, 5, 0))
        prof = regions.make_profile(self.doc, self.body, sk, upper)
        self.assertEqual(sorted(g.TypeId.split("::")[1] for g in prof.Geometry), ["GeomArcOfCircle", "GeomLineSegment"])
        pad = util.add_pad(self.body, prof, 2)
        self.doc.recompute()
        self.assertAlmostEqual(pad.Shape.Volume, math.pi * 100 / 2 * 2, places=2)


class RegionsIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Ri")
        self.body = document.make_part(self.doc)
        self.sk = crossing_sketch(self.body)
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_crossing_sketch_is_not_a_ready_profile_but_its_source_counts_as_used(self):
        from incad.features import common
        self.assertFalse(common.closed_profile(self.sk))
        prof = regions.make_profile(self.doc, self.body, self.sk, regions.region_at(self.sk, App.Vector(5, 5, 0)))
        pad = util.add_pad(self.body, prof, 5)
        self.body.Tip = pad
        self.doc.recompute()
        self.assertEqual(common.consumers(self.sk), [pad])
        self.assertEqual(common.unconsumed_closed_sketches(self.body), [])

    def test_tree_shows_the_users_sketch_under_the_feature_and_hides_the_area_copy(self):
        from incad.core import tree
        prof = regions.make_profile(self.doc, self.body, self.sk, regions.region_at(self.sk, App.Vector(5, 5, 0)))
        pad = util.add_pad(self.body, prof, 5)
        self.body.Tip = pad
        self.doc.recompute()
        top = tree.build(self.doc)[0].children
        labels = [n.label for n in top]
        self.assertNotIn(prof.Label, labels)
        self.assertNotIn(self.sk.Label, labels)
        pad_node = [n for n in top if n.name == pad.Name][0]
        self.assertEqual([c.name for c in pad_node.children], [self.sk.Name])
        self.assertEqual(labels[-1], "End of Part")

    def test_delete_feature_removes_the_area_copy_and_keeps_the_sketch(self):
        from incad.core import delete
        prof = regions.make_profile(self.doc, self.body, self.sk, regions.region_at(self.sk, App.Vector(5, 5, 0)))
        pad = util.add_pad(self.body, prof, 5)
        self.body.Tip = pad
        self.doc.recompute()
        prof_name, sk_name = prof.Name, self.sk.Name
        with self.assertRaises(ValueError):
            delete.delete(self.sk)  # in use
        delete.delete(pad)
        self.doc.recompute()
        self.assertIsNone(self.doc.getObject(prof_name))
        self.assertIsNotNone(self.doc.getObject(sk_name))


class UsableProfileTest(unittest.TestCase):
    def test_a_crossed_sketch_is_a_candidate_profile(self):
        from incad.features import common
        doc = util.new_doc("Up")
        try:
            body = document.make_part(doc)
            sk = crossing_sketch(body)
            doc.recompute()
            self.assertTrue(common.usable_profile(sk))
            self.assertEqual(common.unconsumed_closed_sketches(body), [sk])
        finally:
            App.closeDocument(doc.Name)


class FollowAndOrphansTest(unittest.TestCase):
    """Area copies follow their sketch quietly: a lost area is reported once, naming the features that use it; a
    copy no feature uses (an orphan) is never reported and is removed when the file opens."""

    def setUp(self):
        self.doc = util.new_doc("Rf")
        self.body = document.make_part(self.doc)
        self.sk = crossing_sketch(self.body)
        self.doc.recompute()
        self.used = regions.make_profile(self.doc, self.body, self.sk, regions.region_at(self.sk, App.Vector(5, 5, 0)))
        self.cut = self.body.newObject("PartDesign::Pad", "Pad")
        self.cut.Profile = self.used
        self.cut.Length = 3
        self.orphan = regions.make_profile(self.doc, self.body, self.sk,
                                           regions.region_at(self.sk, App.Vector(15, 5, 0)))
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_users(self):
        self.assertEqual(regions.users(self.used), [self.cut])
        self.assertEqual(regions.users(self.orphan), [])

    def test_a_lost_area_is_reported_once_with_its_users(self):
        warned = set()
        for i in reversed(range(self.sk.GeometryCount)):  # everything redrawn far away: both areas are gone
            self.sk.delGeometry(i)
        self.sk.addGeometry(Part.Circle(App.Vector(60, 60, 0), App.Vector(0, 0, 1), 3))
        self.doc.recompute()
        first = regions.follow(self.sk, warned)
        second = regions.follow(self.sk, warned)
        self.assertEqual(len(first), 1, first)
        self.assertIn("Pad", first[0])
        self.assertNotIn(self.orphan.Label, " ".join(first))
        self.assertEqual(second, [], "not again while the area stays lost")

    def test_purge_orphans_keeps_the_used_copy(self):
        name = self.orphan.Name
        self.assertEqual(regions.purge_orphans(self.doc), 1)
        self.assertIsNone(self.doc.getObject(name))
        self.assertIsNotNone(self.doc.getObject(self.used.Name))
        self.assertIsNotNone(self.doc.getObject(self.sk.Name), "the user's sketch stays")
        self.assertEqual(regions.purge_orphans(self.doc), 0)
