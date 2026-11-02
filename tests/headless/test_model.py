import unittest

import FreeCAD as App
from incad.core import document, model, sketch
from headless import util


class ModelTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("M")
        self.body, self.pad = util.padded_part(self.doc, 10, incad=True)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_feature_parameter_drives_the_feature(self):
        name = model.new_feature_parameter(self.doc, "25 mm")
        self.assertEqual(name, "d0")
        model.bind(self.pad, "Length", name)
        self.doc.recompute()
        self.assertAlmostEqual(self.pad.Length.Value, 25)
        self.assertEqual(model.bound_parameter(self.pad, "Length"), "d0")
        self.assertEqual(model.parameter_text(self.doc, "d0"), "25 mm")

    def test_plain_numbers_use_part_units_and_degrees(self):
        a = model.new_feature_parameter(self.doc, "12")
        b = model.new_feature_parameter(self.doc, "30", unit_kind="angle")
        self.assertEqual(model.parameter_text(self.doc, a), "12 mm")
        self.assertEqual(model.parameter_text(self.doc, b), "30°")

    def test_units_for_plain_numbers_in_formulas(self):
        u = model.with_units
        self.assertEqual(u("d16+2", "mm"), "d16+2 mm")
        self.assertEqual(u("d3*2", "mm"), "d3*2")
        self.assertEqual(u("(d1+2)*3", "mm"), "(d1+2 mm)*3")
        self.assertEqual(u("d1/2-1", "mm"), "d1/2-1 mm")
        self.assertEqual(u("2 * d1 + 0.5", "in"), "2 * d1 + 0.5 in")
        self.assertEqual(u("d1 + 2 mm", "mm"), "d1 + 2 mm")
        self.assertEqual(u("d1 + 2mm - 1.5e1", "mm"), "d1 + 2mm - 1.5e1 mm")
        self.assertEqual(u("d2 - 15", "deg"), "d2 - 15 deg")
        self.assertEqual(u("d1 + 2", None), "d1 + 2")
        self.assertEqual(u("Width2 + 3", "mm"), "Width2 + 3 mm")  # digits inside a name are not numbers

    def test_feature_formula_with_a_plain_number(self):
        a = model.new_feature_parameter(self.doc, "20 mm")
        b = model.new_feature_parameter(self.doc, "%s+5" % a)
        self.assertAlmostEqual(model.parameter_value(self.doc, b) if hasattr(model, "parameter_value")
                               else getattr(self.doc.getObject(model.MODEL_NAME), b).Value, 25)

    def test_names_shared_with_dimensions_and_user_parameters(self):
        document.set_parameter(self.doc, "d2", "1 mm")
        self.assertEqual(model.new_feature_parameter(self.doc, "5 mm"), "d3")
        sk = self.doc.getObject("Sketch")
        import Sketcher
        sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 20))
        self.doc.recompute()
        self.assertEqual(sketch.name_new_dimensions(self.doc), ["d4"])
        self.assertEqual([n for n, _, _ in model.model_parameters(self.doc)], ["d4", "d3"])

    def test_formulas_and_rename(self):
        n = model.new_feature_parameter(self.doc, "20 mm")
        m = model.new_feature_parameter(self.doc, "1 mm")
        model.set_model_parameter(self.doc, m, "%s / 4" % n)
        self.assertEqual(model.parameter_text(self.doc, m), "%s / 4" % n)
        model.rename_model_parameter(self.doc, n, "Depth")
        self.assertEqual(model.parameter_text(self.doc, m), "Depth / 4")
        self.assertTrue(model.is_model_parameter(self.doc, "Depth"))
        self.assertFalse(model.is_model_parameter(self.doc, n))
        with self.assertRaises(ValueError):
            model.rename_model_parameter(self.doc, "Depth", "mm")
        with self.assertRaises(ValueError):
            model.set_model_parameter(self.doc, "Depth", "Nope * 2")

    def test_model_object_is_hidden_from_the_browser(self):
        model.new_feature_parameter(self.doc, "1 mm")
        from incad.core import nodes, tree
        self.assertNotIn("InCadModel", [n.label for _, n in nodes.flatten(tree.build(self.doc))])

    def test_new_parameter_with_unknown_name_is_refused_plainly(self):
        before = model.feature_parameters(self.doc)
        with self.assertRaises(ValueError) as ctx:
            model.new_feature_parameter(self.doc, "Nope * 2")
        self.assertIn("Unknown name 'Nope'", str(ctx.exception))
        self.assertNotIn("property binding", str(ctx.exception))
        self.assertEqual(model.feature_parameters(self.doc), before)

    def test_unused_feature_parameter_can_be_removed(self):
        used = model.new_feature_parameter(self.doc, "25 mm")
        model.bind(self.pad, "Length", used)
        spare = model.new_feature_parameter(self.doc, "3 mm")
        self.doc.recompute()
        model.remove_model_parameter(self.doc, spare)
        self.assertNotIn(spare, [p[0] for p in model.feature_parameters(self.doc)])
        with self.assertRaises(ValueError) as ctx:
            model.remove_model_parameter(self.doc, used)
        self.assertIn(self.pad.Label, str(ctx.exception))


class RelinkAfterUndoTest(unittest.TestCase):
    """FreeCAD quirk (probed): an object restored by undo/abort loses its formula dependency links."""

    def setUp(self):
        from incad.features import extrude
        self.extrude = extrude
        self.doc = util.new_doc("RL")
        self.body = document.make_part(self.doc)
        sk = util.add_rect_sketch(self.body)
        self.doc.recompute()
        self.doc.openTransaction("create")
        self.f = extrude.create(self.doc, self.body, sk, extrude.defaults(self.doc, self.body, sk))
        self.doc.recompute()
        self.doc.commitTransaction()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_relink_restores_live_formulas(self):
        self.doc.openTransaction("edit")
        self.extrude.update(self.doc, self.f, dict(self.extrude.read(self.doc, self.f), operation="cut"))
        self.doc.recompute()
        self.doc.abortTransaction()
        self.doc.recompute()
        self.assertEqual(model.relink(self.doc), ["Extrude"])
        model.set_model_parameter(self.doc, "d0", "4 mm")
        self.assertAlmostEqual(self.body.Tip.Length.Value, 4)

    def test_rollback_relinks_by_itself(self):
        from incad.core import safe

        def replace_then_fail():
            self.extrude.update(self.doc, self.f, dict(self.extrude.read(self.doc, self.f), operation="cut"))
            raise RuntimeError("boom")

        self.assertFalse(safe.run_safely("Bad", replace_then_fail, self.doc).ok)
        model.set_model_parameter(self.doc, "d0", "6 mm")
        self.assertAlmostEqual(self.body.Tip.Length.Value, 6)


class UserParameterNamesTest(unittest.TestCase):
    def test_user_parameter_cannot_take_a_feature_parameter_name(self):
        doc = util.new_doc("UP")
        try:
            document.make_part(doc)
            name = model.new_feature_parameter(doc, "5 mm")
            with self.assertRaises(ValueError):
                document.set_parameter(doc, name, "1 mm")
        finally:
            App.closeDocument(doc.Name)


class FriendlyErrorTest(unittest.TestCase):
    def test_unknown_name_is_said_plainly(self):
        doc = util.new_doc("FE")
        try:
            document.make_part(doc)
            name = model.new_feature_parameter(doc, "5 mm")
            with self.assertRaises(ValueError) as ctx:
                model.set_model_parameter(doc, name, "Nope * 2")
            self.assertIn("Unknown name 'Nope'", str(ctx.exception))
            self.assertNotIn("property binding", str(ctx.exception))
        finally:
            App.closeDocument(doc.Name)


class FriendlyEverywhereTest(unittest.TestCase):
    def test_dimensions_and_user_parameters_say_it_plainly(self):
        import Sketcher
        doc = util.new_doc("FE2")
        try:
            body = document.make_part(doc)
            sk = util.add_rect_sketch(body)
            sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 20))
            doc.recompute()
            name = sketch.name_new_dimensions(doc)[0]
            for fn in (lambda: sketch.set_dimension(doc, name, "Nope * 2"),
                       lambda: document.set_parameter(doc, "Gap", "Nope * 2")):
                with self.assertRaises(ValueError) as ctx:
                    fn()
                self.assertIn("Unknown name 'Nope'", str(ctx.exception))
        finally:
            App.closeDocument(doc.Name)


class UnitsAfterReopenTest(unittest.TestCase):
    """A parameter is an App::PropertyQuantity, whose unit FreeCAD does not save: after a reopen '90°' read '90' and
    '12 mm' read '12' until the parameter was recomputed. Formulas using it still compute (checked here)."""
    def setUp(self):
        import os
        import tempfile
        self.path = os.path.join(tempfile.mkdtemp(), "units.FCStd")
        self.doc = util.new_doc("Un")
        self.body, self.pad = util.padded_part(self.doc, 10, incad=True)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def reopen(self):
        self.doc.saveAs(self.path)
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(self.path)
        model.restore_units(self.doc)  # what InCad does when it first shows an opened document

    def test_angle_and_length_keep_their_units(self):
        a = model.new_feature_parameter(self.doc, "90 °", "angle")
        l_ = model.new_feature_parameter(self.doc, "12 mm", "length")
        self.reopen()
        self.assertEqual(model.parameter_text(self.doc, a), "90°")
        self.assertEqual(model.parameter_text(self.doc, l_), "12 mm")

    def test_a_formula_using_a_length_still_computes(self):
        l_ = model.new_feature_parameter(self.doc, "12 mm", "length")
        f = model.new_feature_parameter(self.doc, "%s + 5 mm" % l_, "length")
        model.bind(self.pad, "Length", f)
        self.doc.recompute()
        pad_name = self.pad.Name
        self.reopen()
        pad = self.doc.getObject(pad_name)
        pad.touch()
        self.doc.recompute()
        self.assertFalse([o.Name for o in self.doc.Objects if "Invalid" in o.State])
        self.assertAlmostEqual(pad.Length.Value, 17.0)
        self.assertEqual(model.parameter_text(self.doc, f), "%s + 5 mm" % l_)

    def test_the_parameter_lists_keep_units(self):
        """The Parameters window: the value column of user parameters and of feature values."""
        document.set_parameter(self.doc, "width", "40 mm")
        document.set_parameter(self.doc, "tilt", "15 °")
        a = model.new_feature_parameter(self.doc, "90 °", "angle")
        f = model.new_feature_parameter(self.doc, "width / 2", "length")
        self.reopen()
        users = {n: v for n, _, v in document.list_parameters(self.doc)}
        self.assertEqual((users["width"], users["tilt"]), ("40 mm", "15°"))
        values = {n: v for n, _, v in model.feature_parameters(self.doc)}
        self.assertEqual((values[a], values[f]), ("90°", "20 mm"))

    def test_restoring_units_leaves_nothing_to_recompute(self):
        model.new_feature_parameter(self.doc, "12 mm", "length")
        self.reopen()
        self.assertFalse([o.Name for o in self.doc.Objects if "Touched" in o.State])


class ChangingValuesTest(unittest.TestCase):
    """A new or changed model value leaves touched only what uses it: FreeCAD touches every user of the holder,
    which recomputed the whole part for one value (the user's Text: 65 s)."""

    def setUp(self):
        from incad.core import document
        from headless import util
        self.doc = util.new_doc("Cv")
        body = document.make_part(self.doc)
        s1 = util.add_rect_sketch(body, name="S1")
        self.p1 = util.add_pad(body, s1, 5, "P1")
        s2 = util.add_rect_sketch(body, name="S2")
        self.p2 = util.add_pad(body, s2, 3, "P2")
        self.p2.Type = "Length"
        self.a = model.new_feature_parameter(self.doc, "5 mm")
        self.b = model.new_feature_parameter(self.doc, "3 mm")
        model.bind(self.p1, "Length", self.a)
        model.bind(self.p2, "Length", self.b)
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def touched(self):
        return {o.Name for o in self.doc.Objects if "Touched" in o.State}

    def test_a_new_value_touches_nothing(self):
        model.new_feature_parameter(self.doc, "7 mm")
        self.assertEqual(self.touched(), set())

    def test_a_changed_value_touches_only_its_user(self):
        model.set_model_parameter(self.doc, self.a, "6 mm", everything=False)
        self.assertIn("P1", self.touched())
        self.assertNotIn("P2", self.touched())
        self.doc.recompute()
        self.assertAlmostEqual(self.p1.Length.Value, 6.0)
        self.assertAlmostEqual(self.p2.Length.Value, 3.0)

    def test_a_value_through_a_formula_touches_the_formula_s_user(self):
        model.set_model_parameter(self.doc, self.b, "%s / 2" % self.a, everything=False)
        self.doc.recompute()
        model.set_model_parameter(self.doc, self.a, "8 mm", everything=False)
        self.assertIn("P2", self.touched())  # b follows a: P2 uses b
        self.doc.recompute()
        self.assertAlmostEqual(self.p2.Length.Value, 4.0)


class RawMessagesTest(unittest.TestCase):
    """FreeCAD's and OpenCASCADE's own words were shown as they are (the finder's list)."""

    def test_raw_kernel_words_come_out_plain(self):
        for raw, part in (("Extrusion: end face of tapered extrusion is empty", "taper closes"),
                          ("Linked shape object is empty", "nothing to use"),
                          ("SketchBased: No faces found in this direction", "To Next"),
                          ("Sweep: Geom_RectangularTrimmedSurface::V1==V2", "closed outline"),
                          ("Error: height too small!", "height above zero")):
            self.assertIn(part, model.friendly(raw), raw)
        self.assertIn("couldn't be made", model.friendly("BRepOffsetAPI_MakeOffsetShape not done",
                                                         "PartDesign::Fillet"))
