import unittest


class SmokeTest(unittest.TestCase):
    def test_import_incad(self):
        import incad
        self.assertEqual(incad.__version__, "0.17.5")

    def test_freecad_available(self):
        import FreeCAD
        self.assertEqual(FreeCAD.Version()[:2], ["1", "1"])
