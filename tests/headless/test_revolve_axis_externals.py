"""A Revolve about a projected line of its sketch: the axis follows that projection when the sketch's other
projections are deleted (renumbered), and stays where it is when its own is deleted (by hand, Break Link): its
formula pointed at nothing ("list index out of range") and the revolve failed."""
import unittest

import FreeCAD as App
import Part
from incad.core import document
from incad.features import revolve
from headless import util


class AxisFollowsExternals(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("RX")
        body = self.body = document.make_part(self.doc)
        pad = util.add_pad(body, util.add_rect_sketch(body, 20, 10, "Base"), 5)  # x 0..20, y 0..10, z 0..5
        body.Tip = pad
        self.doc.recompute()
        sk = self.sk = body.newObject("Sketcher::SketchObject", "Profile")
        sk.AttachmentSupport = [(util.xy_plane(body), "")]
        sk.MapMode = "FlatFace"
        bottom = {round(e.CenterOfMass.y, 6): "Edge%d" % i for i, e in enumerate(pad.Shape.Edges, 1)
                  if abs(e.CenterOfMass.z) < 1e-6 and abs(e.BoundBox.YLength) < 1e-6}
        sk.addExternal(pad.Name, bottom[10.0])  # n = 0: the far edge
        sk.addExternal(pad.Name, bottom[0.0])   # n = 1: the axis, y = 0
        pts = [(5, -4), (8, -4), (8, -2), (5, -2)]  # beside the axis (off the block)
        for a, b in zip(pts, pts[1:] + pts[:1]):
            sk.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)))
        self.doc.recompute()
        axis = next(v for v, _l, ends, _g in revolve._projected_line_items(sk) if abs(ends[0].y) < 1e-6)
        values = dict(revolve.defaults(self.doc, body, sk), axis_mode="construction", axis_construction=axis)
        self.f = revolve.create(self.doc, body, sk, values)
        body.Tip = self.f
        self.doc.recompute()
        self.assertNotIn("Invalid", self.f.State, self.f.getStatusString())
        self.volume = self.f.Shape.Volume

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def check(self):
        self.doc.recompute()
        bad = [o.Name for o in self.body.Group if "Invalid" in o.State]
        self.assertEqual(bad, [], [o.getStatusString() for o in self.body.Group if "Invalid" in o.State])
        self.assertAlmostEqual(self.f.Shape.Volume, self.volume, delta=1e-6)

    def test_another_projection_deleted_by_hand(self):
        line = revolve.axis_helper(self.f)
        self.sk.delExternal(0)  # as the sketch's Delete does
        revolve.follow_externals(self.sk)  # (the window's observer calls it)
        self.assertEqual(getattr(line, revolve.LINE), -3)
        self.assertIn("Profile.ExternalGeo[2]", dict(line.ExpressionEngine)["Placement"])
        self.check()

    def test_its_own_projection_deleted_by_hand(self):
        line = revolve.axis_helper(self.f)
        self.sk.delExternal(1)
        revolve.follow_externals(self.sk)
        self.assertEqual(getattr(line, revolve.LINE), 0)
        self.assertNotIn("Placement", dict(line.ExpressionEngine))
        self.check()

    def test_break_link_on_its_projection(self):
        from incad.core import sketch_trim
        sketch_trim.break_link(self.sk, [-4])  # the axis' projection becomes the sketch's own line
        self.assertEqual(getattr(revolve.axis_helper(self.f), revolve.LINE), 0)
        self.check()


if __name__ == "__main__":
    unittest.main()
