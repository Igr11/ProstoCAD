"""Parts made of an STL's triangles (the user, 2026-11-24: 0.18 is very slow on one): the see-through test at an opened
sketch answers from the corners, with the whole section's answer; Emboss on such a part does not merge its faces."""
import unittest
from unittest import mock

import FreeCAD as App
import Mesh
import Part

from incad.core import faceted, sketch, wrap

V = App.Vector


def ball():
    m = Mesh.createSphere(10.0, 60)
    sh = Part.Shape()
    sh.makeShapeFromMesh(m.Topology, 0.05)
    return Part.makeSolid(sh)


class CutTest(unittest.TestCase):
    def shapes(self):
        box = Part.makeBox(20, 20, 20)
        hollow = box.cut(Part.makeBox(16, 16, 16, V(2, 2, 2)))
        two = Part.makeCompound([Part.makeBox(5, 5, 5), Part.makeBox(5, 5, 5, V(0, 0, 10))])
        return dict(box=box, hollow=hollow, two=two, ball=Part.makeSphere(10), round=box.makeFillet(3, box.Edges),
                    ring=Part.makeTorus(10, 3), step=box.fuse(Part.makeBox(10, 10, 10, V(0, 0, 20))), tri=ball())

    def test_yes_only_where_the_section_says_yes(self):
        ways = (V(0, 0, 1), V(1, 0, 0), V(1, 2, 3).normalize())
        yes = 0
        for name, s in self.shapes().items():
            for n in ways:
                for k in range(-6, 33, 3):
                    d = k * 1.0 + (0.0 if k % 2 else 0.37)
                    if sketch.plane_between_vertices(s, n, d):
                        yes += 1
                        if name == "tri" and not s.slice(n, d):  # the kernel's section of the triangles comes out
                            self.assertLess(abs(d), 9.9)  # empty at some planes: the ball is cut there all the same
                            continue
                        self.assertTrue(s.slice(n, d), (name, tuple(n), d))
        self.assertGreater(yes, 60)

    def test_the_gap_between_two_solids_is_no_cut(self):
        self.assertFalse(sketch.plane_between_vertices(self.shapes()["two"], V(0, 0, 1), 7.5))

    def test_the_session_answers_as_before(self):
        """_cut_by whole, the quick way on and off."""
        from incad.ui.sketch_session import SketchSession as Sessions

        class Editing:
            def __init__(self, pl):
                self.Placement = pl

            def getParentGeoFeatureGroup(self):
                return None
        for name, s in self.shapes().items():
            if name == "tri":
                continue
            for z in (-5.0, 0.0, 2.0, 5.0, 7.5, 10.0, 19.0, 20.0, 25.0, 31.0):
                for rot in (App.Rotation(), App.Rotation(V(1, 0, 0), 90), App.Rotation(V(1, 1, 0), 33)):
                    e = Editing(App.Placement(V(0, 0, z), rot))
                    with mock.patch.object(sketch, "plane_between_vertices", lambda *a: False):
                        was = Sessions._cut_by(s, e)
                    self.assertEqual(Sessions._cut_by(s, e), was, (name, z, rot))


class EmbossTest(unittest.TestCase):
    """Two boxes side by side: their tops are one face once merged."""

    def emboss(self, triangles):
        a = Part.makeBox(10, 10, 5).fuse(Part.makeBox(10, 10, 5, V(10, 0, 0)))
        top = next(f for f in a.Faces if f.normalAt(0, 0).z > 0.9 and f.BoundBox.XMin < 1 and abs(f.BoundBox.ZMax - 5) < 1e-6)
        square = Part.Face(Part.makePolygon([V(x, y, 5) for x, y in ((2, 2), (4, 2), (4, 4), (2, 4), (2, 2))]))
        with mock.patch.object(faceted, "is_faceted", lambda *_a, **_k: triangles):
            r = wrap.apply(a, [square], top, 1.0, False, False, V(0, 0, 1))
        self.assertTrue(r.isValid())
        self.assertEqual(len(r.Solids), 1)
        self.assertAlmostEqual(r.Volume, 1004.0, places=6)
        return sum(abs(f.BoundBox.ZMin - 5) < 1e-6 and abs(f.BoundBox.ZMax - 5) < 1e-6 for f in r.Faces)

    def test_an_ordinary_part_is_merged(self):
        self.assertEqual(self.emboss(False), 1)

    def test_a_part_of_triangles_is_not(self):
        self.assertEqual(self.emboss(True), 2)

    def test_the_triangles_are_told(self):
        self.assertTrue(faceted.is_faceted(ball()))
