import math
import unittest

import FreeCAD as App
import Part
from incad.core import document, model
from incad.features import kind_for, revolve
from headless import util


class RevolveTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("R")
        self.body = document.make_part(self.doc)
        sk = self.body.newObject("Sketcher::SketchObject", "Profile")
        sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        sk.MapMode = "FlatFace"
        pts = [(2, 0), (6, 0), (6, 4), (2, 4)]
        for i in range(4):
            a, b = pts[i], pts[(i + 1) % 4]
            sk.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)))
        self.sk = sk
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_full_revolve(self):
        v = revolve.defaults(self.doc, self.body, self.sk)
        self.assertEqual((v["operation"], v["axis_mode"], v["extent"]), ("join", "V_Axis", "full"))
        f = revolve.create(self.doc, self.body, self.sk, v)
        self.doc.recompute()
        self.assertEqual(f.TypeId, revolve.JOIN)
        self.assertAlmostEqual(f.Shape.Volume, math.pi * (6 ** 2 - 2 ** 2) * 4, places=2)
        self.assertIs(kind_for(f), revolve)

    def test_angle_parameter(self):
        f = revolve.create(self.doc, self.body, self.sk,
                           dict(revolve.defaults(self.doc, self.body, self.sk), extent="angle", angle="90"))
        self.doc.recompute()
        self.assertAlmostEqual(f.Angle.Value, 90)
        self.assertEqual(revolve.read(self.doc, f)["angle"], "90°")
        name = model.bound_parameter(f, "Angle")
        self.assertIsNotNone(name)
        f = revolve.update(self.doc, f, dict(revolve.read(self.doc, f), angle="180"))
        self.doc.recompute()
        self.assertAlmostEqual(f.Angle.Value, 180)

    def test_new_solid_is_a_revolve_in_a_solid_of_its_own(self):
        """New Solid (the user: instead of Auto, for a Boolean later): the profile and its revolve in a new Solid."""
        from incad.core import document
        f = revolve.create(self.doc, self.body, self.sk,
                           dict(revolve.defaults(self.doc, self.body, self.sk), operation="new_solid"))
        self.doc.recompute()
        owner = f.getParentGeoFeatureGroup()
        self.assertEqual(f.TypeId, revolve.JOIN)
        self.assertIsNot(owner, self.body)
        self.assertIs(self.sk.getParentGeoFeatureGroup(), owner)
        self.assertIs(document.primary_body(self.doc), self.body)
        self.assertAlmostEqual(f.Shape.Volume, math.pi * (6 ** 2 - 2 ** 2) * 4, places=2)
        self.assertEqual(revolve.read(self.doc, f)["operation"], "new_solid")
        self.assertNotIn("auto", [c[0] for c in revolve.FIELDS[0].choices])
