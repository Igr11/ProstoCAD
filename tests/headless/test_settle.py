"""Undo, redo and a rolled-back step leave what came back as it is (incad.core.settle): nothing proven right is computed
again, and the shapes are those a whole recompute gives. Run: see util."""
import unittest

import FreeCAD as App

from headless import util  # noqa: F401 (the path to incad)
from incad.core import settle


class Computed:
    def __init__(self):
        self.names = []

    def slotRecomputedObject(self, obj):
        self.names.append(obj.Name)


class SettleTest(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("Settle")
        self.doc.UndoMode = 1
        self.body = self.doc.addObject("PartDesign::Body", "Body")
        sk = self.sk = self.body.newObject("Sketcher::SketchObject", "Sketch")
        import Part
        pts = [App.Vector(*p) for p in ((0, 0, 0), (20, 0, 0), (20, 10, 0), (0, 10, 0))]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            sk.addGeometry(Part.LineSegment(a, b))
        self.pad = self.body.newObject("PartDesign::Pad", "Pad")
        self.pad.Profile = sk
        self.pad.Length = 10
        self.doc.recompute()
        self.fillet = self.body.newObject("PartDesign::Fillet", "Fillet")
        bottom = next(i for i, e in enumerate(self.pad.Shape.Edges) if e.BoundBox.ZMax < 1e-6)
        self.fillet.Base = (self.pad, ["Edge%d" % (bottom + 1)])
        self.fillet.Radius = 2
        self.doc.recompute()
        self.seen = Computed()
        App.addDocumentObserver(self.seen)

    def tearDown(self):
        App.removeDocumentObserver(self.seen)
        App.closeDocument(self.doc.Name)

    def volumes(self):
        return [round(o.Shape.Volume, 6) for o in (self.pad, self.fillet, self.body)]

    def change(self, value):
        self.doc.openTransaction("change")
        self.pad.Length = value
        self.doc.recompute()
        self.doc.commitTransaction()

    def test_undo_and_redo_compute_nothing(self):
        before = self.volumes()
        self.change(14)
        after = self.volumes()
        self.seen.names = []
        settle.take_back(self.doc, self.doc.undo)
        self.assertEqual(self.volumes(), before)
        settle.take_back(self.doc, self.doc.redo)
        self.assertEqual(self.volumes(), after)
        self.assertEqual(self.seen.names, [])
        self.assertFalse(settle.touched(self.doc))

    def test_a_value_changed_without_its_shape_is_computed(self):
        before = self.volumes()
        self.doc.openTransaction("value only")
        self.pad.Length = 30  # not computed inside the step
        self.doc.commitTransaction()
        self.doc.recompute()  # off the record
        settle.take_back(self.doc, self.doc.undo)
        self.assertEqual(self.volumes(), before)
        self.assertEqual(self.pad.Length.Value, 10)

    def test_a_step_that_failed_is_tried_again(self):
        before = self.volumes()
        self.doc.openTransaction("too big")
        self.fillet.Radius = 500
        self.doc.recompute()
        self.doc.commitTransaction()
        self.assertFalse(self.fillet.isValid())
        settle.take_back(self.doc, self.doc.undo)
        self.assertTrue(self.fillet.isValid())
        self.assertEqual(self.volumes(), before)

    def test_abort_leaves_what_was_settled(self):
        before, was = self.volumes(), settle.touched(self.doc)
        self.doc.openTransaction("window")
        self.pad.Length = 25
        self.doc.recompute([self.pad])
        self.seen.names = []
        settle.aborted(self.doc, self.doc.abortTransaction, was)
        self.doc.recompute()
        self.assertEqual(self.volumes(), before)
        self.assertEqual(self.seen.names, [])

    def test_the_shapes_are_a_whole_recomputes(self):
        self.change(17)
        settle.take_back(self.doc, self.doc.undo)
        got = self.volumes()
        for o in self.doc.Objects:
            o.touch()
        self.doc.recompute()
        self.assertEqual(self.volumes(), got)


if __name__ == "__main__":
    unittest.main()
