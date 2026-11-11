"""Sweep on a path that bends tighter than the profile reaches into the bend: refused as risky (the window asks to
save, then builds it anyway); measured from the end of the path the profile stands at (the user's Sweep, drawn at
the path's last point, was refused: "reaches 49.36 mm"); the save before the risk."""
import math
import os
import tempfile
import unittest

import FreeCAD as App
import Part
from incad.core import document
from incad.features import shapes, sweep_check
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


class SweepTightTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Tight")
        self.body = document.make_part(self.doc)
        # a quarter of a circle of radius 3 (XZ): from the origin (going up) to its other end (going sideways)
        arc = Part.ArcOfCircle(Part.Circle(App.Vector(3, 0, 0), App.Vector(0, 0, 1), 3), math.pi / 2, math.pi)
        self.path = sketch(self.body, "XZ_Plane", "Path", [arc])
        self.doc.recompute()

    def tearDown(self):
        sweep_check.FORCE = False
        sweep_check.clear()
        App.closeDocument(self.doc.Name)

    def make(self, **values):
        v = dict(shapes.SWEEP.defaults(self.doc, self.body, None), path="Path", **values)
        f = shapes.SWEEP.create(self.doc, self.body, None, v)
        self.doc.recompute()
        return f

    def test_too_tight_is_risky(self):
        sketch(self.body, "XY_Plane", "Prof", [Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 5)])
        self.doc.recompute()
        with self.assertRaises(sweep_check.TooTight) as e:
            self.make(profile="Prof")
        self.assertTrue(getattr(e.exception, "risky", False))
        self.assertIn("may crash", str(e.exception))
        self.assertIsNotNone(sweep_check.CAGE)

    def test_forced_goes_past_the_check(self):
        sketch(self.body, "XY_Plane", "Prof", [Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 5)])
        self.doc.recompute()
        sweep_check.FORCE = True
        try:
            self.make(profile="Prof")
        except sweep_check.TooTight:
            self.fail("refused although the user chose to build it")
        except Exception:
            pass  # what the kernel makes of the fold is not this test's business
        self.assertIsNone(sweep_check.CAGE)

    def test_profile_at_the_far_end(self):
        """A small circle at the path's last point: measured from that end it fits the bend (from the first point
        it lay 4.2 mm off the path and was refused)."""
        end = self.path.Shape.Wires[0].OrderedVertexes[-1].Point
        start = self.path.Shape.Wires[0].OrderedVertexes[0].Point
        far = end if (end - App.Vector(0, 0, 0)).Length > 1 else start
        sketch(self.body, "YZ_Plane", "Prof", [Part.Circle(App.Vector(far.y, far.z, 0), App.Vector(0, 0, 1), 1)],
               z=far.x)
        self.doc.recompute()
        try:
            self.make(profile="Prof")
        except sweep_check.TooTight as e:
            self.fail("refused: %s" % e)
        except Exception:
            pass

    def test_save_before_risk(self):
        from incad.core import prefs
        from incad.ui import feature_session
        folder = tempfile.mkdtemp()
        prefs.set_last_folder(folder)
        self.doc.Label = "Tight"
        open(os.path.join(folder, "Tight.FCStd"), "w").close()  # the name is taken
        self.doc.openTransaction("Sweep")  # the window's undo step is open while it saves
        path = feature_session.save_before_risk(self.doc)
        self.doc.abortTransaction()
        self.assertEqual(path, os.path.join(folder, "Tight 2.FCStd"))
        self.assertTrue(os.path.getsize(path) > 0)
        again = feature_session.save_before_risk(self.doc)  # now it has a file: saved there
        self.assertEqual(again, path)


if __name__ == "__main__":
    unittest.main()
