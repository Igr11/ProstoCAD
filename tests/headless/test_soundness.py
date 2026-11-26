"""The full check of a step's own faces finds what the check of the whole part finds (incad.core.soundness)."""
import random
import unittest

import FreeCAD as App
import Part

from headless import util  # noqa: F401 (the path to incad)
from incad.core import soundness

V = App.Vector


class SoundnessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        holes = [Part.makeCylinder(2, 20, V(10 + 12 * i, 10 + 16 * j, 0)) for i in range(15) for j in range(5)]
        cls.base = Part.makeBox(200, 100, 20).cut(Part.makeCompound(holes))

    def same(self, shape):
        whole = soundness._whole(shape)
        part = soundness._changed(shape, self.base)
        self.assertEqual(soundness.faulty(shape, self.base), whole)
        return whole, part is not None

    def test_faulty_ones_are_found(self):
        b = self.base
        for shape in (b.cut(Part.makeCylinder(3, 20, V(100, 3, 0))), b.cut(Part.makeCylinder(1.5, 20, V(13.5, 10, 0))),
                      b.fuse(Part.makeCylinder(4, 30, V(20, 50, 24), V(1, 0, 0)))):
            self.assertEqual(self.same(shape.removeSplitter()), (True, True))

    def test_many(self):
        random.seed(3)
        looked = 0
        for _ in range(40):
            r = random.choice([1, 1.5, 2, 3])
            x, y = 10 + 12 * random.randint(0, 14), 10 + 16 * random.randint(0, 4)
            c = Part.makeCylinder(r, random.choice([5, 20]), V(x + random.choice([2 + r, r, abs(2 - r), 0, 4]), y,
                                                              random.choice([0, 15, 20])))
            shape = self.base.cut(c) if random.random() < 0.6 else self.base.fuse(c)
            if shape.isValid():
                looked += self.same(shape)[1]
        self.assertGreater(looked, 10)


if __name__ == "__main__":
    unittest.main()
