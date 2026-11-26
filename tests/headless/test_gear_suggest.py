"""Spur Gears: Ratio held, a Center distance wanted (the user, 2026-11-27): the pair at one standard module of ISO 54's
first series, module 2 first and each step the next one down, the Ratio exact when whole teeth allow it, then the
distance nearest; fewer than 17 teeth warned of; the window's line and the values Use sets."""
import math
import unittest

from incad.core import gears
from incad.features import gear


class GearSuggestTest(unittest.TestCase):
    def test_ratio_and_distance_at_module_2(self):
        s = gears.suggest(1.5, 50)
        self.assertEqual((s["module"], s["teeth1"], s["teeth2"], s["distance"]), (2.0, 20, 30, 50.0))
        self.assertEqual(s["warn"], "")

    def test_smaller_module_steps_down(self):
        got = [gears.suggest(2, 61, step=k) for k in range(3)]
        self.assertEqual([g["module"] for g in got], [2.0, 1.5, 1.25])
        self.assertTrue(all(g["teeth2"] == 2 * g["teeth1"] for g in got))
        self.assertEqual(got[0]["distance"], 60.0)
        last = gears.suggest(2, 61, step=len(gears.SUGGEST_MODULES) - 1)
        self.assertTrue(last["last"])
        self.assertIsNone(gears.suggest(2, 61, step=len(gears.SUGGEST_MODULES)))

    def test_helical_and_few_teeth(self):
        h = gears.suggest(2, 50, helix=20)
        self.assertAlmostEqual(h["distance"], h["module"] * (h["teeth1"] + h["teeth2"]) / (2 * math.cos(math.radians(20))))
        low = gears.suggest(3, 20)
        self.assertLess(low["small"], 17)
        self.assertIn("undercut", low["warn"])
        self.assertAlmostEqual(gears.suggest(1.37, 40)["ratio"], 1.37)

    def test_window_line_and_use(self):
        v = dict(make="pair", size2="ratio", ratio="2", want_distance="61 mm", helix="0 °", mesh="")
        s = gear._pair_suggestion(v, 0)
        self.assertIn("Module 2 · 20 : 40 teeth · center distance 60 mm", s["text"])
        self.assertEqual(s["use"], {"module": "2 mm", "teeth": "20"})
        self.assertIsNone(gear._pair_suggestion(dict(v, want_distance=""), 0))
        self.assertIsNone(gear._pair_suggestion(dict(v, size2="teeth"), 0))
        keys = [f.key for f in gear.PAIR_FIELDS]
        self.assertLess(keys.index("ratio"), keys.index("want_distance"))
        self.assertNotIn("want_distance", [f.key for f in gear.WORMSET_FIELDS])


if __name__ == "__main__":
    unittest.main()
