import math
import unittest

import FreeCAD as App
import Part
from incad.core import document, model
from incad.features import combine, common, kind_for
from headless import util

BLOCK = 40 * 30 * 10


class CombineTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Cb")
        self.base = document.make_part(self.doc)
        sk = util.add_rect_sketch(self.base, 40, 30, "BlockSk")
        self.block = util.add_pad(self.base, sk, 10, "Block")
        self.base.Tip = self.block
        self.tool = self.doc.addObject("PartDesign::Body", "Pin")
        sk2 = self.tool.newObject("Sketcher::SketchObject", "PinSk")
        sk2.AttachmentSupport = [(util.xy_plane(self.tool), "")]
        sk2.MapMode = "FlatFace"
        sk2.addGeometry(Part.Circle(App.Vector(20, 15, 0), App.Vector(0, 0, 1), 4))
        self.pin = util.add_pad(self.tool, sk2, 20, "PinPad")
        self.tool.Tip = self.pin
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def make(self, **values):
        v = dict(combine.defaults(self.doc, self.base, None), base=self.base.Name, tools=self.tool.Name)
        v.update(values)
        f = combine.create(self.doc, self.base, None, v)
        self.doc.recompute()
        return f

    def fit(self):
        return [b for b in document.bodies(self.doc) if b.Label == "Pin fit"]

    def test_cut_with_clearance_on_the_cavity(self):
        f = self.make(operation="cut", clearance="0.2 mm", apply_to="cavity")
        self.assertEqual(f.TypeId, "PartDesign::Boolean")
        self.assertAlmostEqual(f.Shape.Volume, BLOCK - math.pi * 4.2 ** 2 * 10, places=1)
        self.assertAlmostEqual(self.tool.Shape.Volume, math.pi * 16 * 20, places=1)  # tool kept, unchanged
        self.assertFalse(self.tool.Visibility)
        self.assertIs(self.base.Tip, f)
        self.assertIs(kind_for(f), combine)
        self.assertEqual(self.fit(), [])

    def test_only_the_tools_piece_near_the_base_is_grown_and_kept(self):
        """The user's jar: a Part::Offset of the whole jar ran 8 s on every change, a label far from the lid
        included. Only the piece near the base is grown, and again only when that piece changed."""
        f = self.make(operation="cut", clearance="0.2 mm", apply_to="cavity")
        grown = f.Group[0]
        self.assertTrue(combine._is_grown(grown), grown.TypeId)
        self.assertLess(grown.Proxy.piece_volume, self.tool.Shape.Volume - 1)  # the pin past the block left out
        self.assertAlmostEqual(f.Shape.Volume, BLOCK - math.pi * 4.2 ** 2 * 10, places=1)
        runs = grown.Proxy.offsets
        self.pin.Length = 25  # far from the block: the piece near it is the same
        self.doc.recompute()
        self.assertEqual(grown.Proxy.offsets, runs, "grown again for a change far from the base")
        self.assertAlmostEqual(f.Shape.Volume, BLOCK - math.pi * 4.2 ** 2 * 10, places=1)
        self.doc.getObject("PinSk").Geometry = [Part.Circle(App.Vector(20, 15, 0), App.Vector(0, 0, 1), 5)]
        self.doc.recompute()
        self.assertEqual(grown.Proxy.offsets, runs + 1)
        self.assertAlmostEqual(f.Shape.Volume, BLOCK - math.pi * 5.2 ** 2 * 10, places=1)
        name = grown.Name
        combine.delete(self.doc, f)
        self.doc.recompute()
        self.assertIsNone(self.doc.getObject(name))

    def test_an_older_part_offset_helper_is_upgraded_on_open(self):
        f = self.make(operation="cut", clearance="0.2 mm", apply_to="cavity")
        new = f.Group[0]
        old = self.doc.addObject("Part::Offset", "CombineOffset")  # as 0.15.0 made it: the whole tool grown
        old.Join = "Intersection"
        old.Source = self.tool
        old.setExpression("Value", new.ExpressionEngine[0][1])
        data = combine._load(f)
        data["helpers"][self.tool.Name]["grown"] = old.Name
        combine._store(f, data)
        f.Group = [old]
        self.doc.removeObject(new.Name)
        self.doc.recompute()
        volume = f.Shape.Volume
        old_name = old.Name
        self.assertEqual(combine.upgrade(self.doc), [f.Label])
        self.assertTrue(combine._is_grown(f.Group[0]))
        self.assertIsNone(self.doc.getObject(old_name))
        self.assertAlmostEqual(f.Shape.Volume, volume, places=3)
        self.assertEqual(combine.upgrade(self.doc), [])

    def test_clearance_on_a_fit_copy_of_the_tool(self):
        f = self.make(operation="cut", clearance="0.2 mm", apply_to="tool")
        self.assertAlmostEqual(f.Shape.Volume, BLOCK - math.pi * 16 * 10, places=1)
        fit = self.fit()
        self.assertEqual(len(fit), 1)
        self.assertAlmostEqual(fit[0].Shape.Volume, math.pi * 3.8 ** 2 * 19.6, places=1)

    def test_a_tool_that_cant_be_grown_is_cut_without_clearance(self):
        """The user's Cut 'did not work': the mirrored Solid (an Emboss on it) can't be offset. The cut is made with
        Clearance 0 mm, the dialog is told, nothing of the failed clearance stays."""
        real = combine._check_helpers

        def failing(doc, helpers, tools):
            if any(mine for mine in helpers.values()):
                raise ValueError("Couldn't grow Pin by the clearance (threads and other complex shapes can't be "
                                 "offset): use Clearance 0 mm")
            return real(doc, helpers, tools)
        combine._check_helpers = failing
        try:
            names = {o.Name for o in self.doc.Objects}
            params = set(model.feature_parameters(self.doc))
            for apply_to in ("cavity", "tool", "split"):
                f = self.make(operation="cut", clearance="0.2 mm", apply_to=apply_to)
                self.assertNotIn("Invalid", f.State)
                self.assertAlmostEqual(f.Shape.Volume, BLOCK - math.pi * 16 * 10, places=1)
                self.assertEqual(combine.adjusted(self.doc, f), {"clearance": "0 mm"})
                self.assertEqual(self.fit(), [])
                left = {o.Name for o in self.doc.Objects} - names - {f.Name, "InCadModel"}
                self.assertEqual(left, set(), apply_to)  # no offset or fit copy left behind
                self.assertEqual(set(model.feature_parameters(self.doc)), params)
                combine.delete(self.doc, f)
                self.doc.recompute()
        finally:
            combine._check_helpers = real
        f = self.make(operation="cut", clearance="0.2 mm")
        self.assertEqual(combine.adjusted(self.doc, f), {})  # a clearance that works is kept

    def test_split(self):
        f = self.make(operation="cut", clearance="0.2 mm", apply_to="split")
        self.assertAlmostEqual(f.Shape.Volume, BLOCK - math.pi * 4.1 ** 2 * 10, places=1)
        self.assertAlmostEqual(self.fit()[0].Shape.Volume, math.pi * 3.9 ** 2 * 19.8, places=1)

    def test_parameter_drives_cavity_and_fit(self):
        f = self.make(operation="cut", clearance="0.2 mm", apply_to="split")
        name = combine.clearance_parameter(f)
        model.set_model_parameter(self.doc, name, "0.4 mm")
        self.assertAlmostEqual(f.Shape.Volume, BLOCK - math.pi * 4.2 ** 2 * 10, places=1)
        self.assertAlmostEqual(self.fit()[0].Shape.Volume, math.pi * 3.8 ** 2 * 19.6, places=1)

    def test_join_and_intersect(self):
        f = self.make(operation="join")
        self.assertAlmostEqual(f.Shape.Volume, BLOCK + math.pi * 16 * 10, places=1)
        f = combine.update(self.doc, f, dict(combine.read(self.doc, f), operation="intersect"))
        self.doc.recompute()
        self.assertAlmostEqual(f.Shape.Volume, math.pi * 16 * 10, places=1)

    def test_edit_removes_unused_helpers(self):
        f = self.make(operation="cut", clearance="0.2 mm", apply_to="split")
        before = {o.Name for o in self.doc.Objects}
        f = combine.update(self.doc, f, dict(combine.read(self.doc, f), apply_to="cavity"))
        self.doc.recompute()
        self.assertEqual(self.fit(), [])
        self.assertTrue({o.Name for o in self.doc.Objects} < before)
        self.assertAlmostEqual(f.Shape.Volume, BLOCK - math.pi * 4.2 ** 2 * 10, places=1)
        r = combine.read(self.doc, f)
        self.assertEqual((r["operation"], r["apply_to"], r["clearance"], r["tools"]),
                         ("cut", "cavity", "0.2 mm", self.tool.Name))

    def test_an_empty_result_is_refused_not_kept(self):
        """Intersect with a tool that misses the part left an empty Solid, marked fine: now it is undone, with why."""
        from incad.core import safe
        self.tool.Placement = App.Placement(App.Vector(200, 0, 0), App.Rotation())
        self.doc.recompute()
        r = safe.run_safely("Boolean", lambda: self.make(operation="intersect"), self.doc)
        self.assertFalse(r.ok)
        self.assertIn("don't overlap", r.error)
        self.assertAlmostEqual(self.base.Shape.Volume, BLOCK, places=3)

    def test_a_tool_a_hair_short_leaves_no_skin(self):
        """The kernel left a 0.0005 mm skin over a hole whose tool fell that short of the top; the Boolean's fuzzy
        tolerance takes such a tool as reaching it."""
        self.pin.Length = 10 - 5e-4
        self.doc.recompute()
        f = self.make(operation="cut", clearance="0 mm")
        self.assertEqual(len(f.Shape.Faces), 7, "the block's 6 and the hole's wall: no skin's floor at the top")
        self.assertAlmostEqual(f.Shape.Volume, BLOCK - math.pi * 16 * 10, delta=0.1)  # the hole goes through

    def test_solids_touching_along_a_line_are_refused(self):
        """A round tool touching the block's side along a line: the kernel calls it valid, its full check fails."""
        from incad.core import safe
        sk = self.doc.getObject("PinSk")
        sk.delGeometry(0)
        sk.addGeometry(Part.Circle(App.Vector(45, 15, 0), App.Vector(0, 0, 1), 5))  # the block's side is x = 40
        self.pin.Length = 10
        self.doc.recompute()
        r = safe.run_safely("Boolean", lambda: self.make(operation="join"), self.doc)
        self.assertFalse(r.ok)
        self.assertIn("only touch", r.error)

    def test_a_refused_fillet_leaves_the_boolean_sound(self):
        """A join with a 0.0005 mm step on top: the fillet the kernel refuses had made the Boolean "Unorientable",
        and every next feature failed on it."""
        from incad.core import safe
        from incad.features import dressup
        sk = self.doc.getObject("PinSk")
        sk.delGeometry(0)
        for a, b in (((30, 10), (60, 10)), ((60, 10), (60, 30)), ((60, 30), (30, 30)), ((30, 30), (30, 10))):
            sk.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)))
        self.pin.Length = 10.0005
        self.doc.recompute()
        f = self.make(operation="join")
        edges = ",".join("%s:Edge%d" % (f.Name, i + 1) for i, e in enumerate(f.Shape.Edges)
                         if e.Curve.TypeId == "Part::GeomLine" and abs(e.tangentAt(e.FirstParameter).z) > 0.99)
        k = dressup.FILLET
        safe.run_safely("Fillet", lambda: k.create(self.doc, self.base, None,
                                                   dict(k.defaults(self.doc, self.base, None), edges=edges,
                                                        radius="1 mm")), self.doc)
        self.assertTrue(f.Shape.isValid(), "the Boolean is left as it was")

    def test_base_cannot_be_a_tool(self):
        with self.assertRaises(ValueError) as ctx:
            self.make(tools=self.base.Name)
        self.assertIn("other solid", str(ctx.exception))

    def test_face_pick_means_its_body(self):
        top = [i + 1 for i, fc in enumerate(self.pin.Shape.Faces) if abs(fc.CenterOfMass.z - 20) < 1e-6][0]
        pair = (self.tool, "%s.Face%d" % (self.pin.Name, top))
        self.assertTrue(common.accepts("body", *common.resolve(*pair)))
        self.assertEqual(combine.body_of(*common.resolve(*pair)), self.tool)

    def test_tool_edit_updates_the_result(self):
        f = self.make(operation="cut", clearance="0 mm", apply_to="cavity")
        sk = self.pin.Profile[0]
        sk.delGeometry(0)
        sk.addGeometry(Part.Circle(App.Vector(20, 15, 0), App.Vector(0, 0, 1), 5))
        self.doc.recompute()
        self.assertAlmostEqual(f.Shape.Volume, BLOCK - math.pi * 25 * 10, places=1)

    def test_a_link_can_be_a_tool(self):
        link = self.doc.addObject("App::Link", "PinCopy")
        link.LinkedObject = self.tool
        link.Placement = App.Placement(App.Vector(5, 0, 0), App.Rotation())
        self.doc.recompute()
        f = self.make(tools=link.Name, operation="cut", clearance="0.2 mm", apply_to="cavity")
        self.assertAlmostEqual(f.Shape.Volume, BLOCK - math.pi * 4.2 ** 2 * 10, places=1)
        f = combine.update(self.doc, f, dict(combine.read(self.doc, f), clearance="0 mm"))
        self.doc.recompute()
        self.assertAlmostEqual(f.Shape.Volume, BLOCK - math.pi * 16 * 10, places=1)
        self.assertEqual(combine.read(self.doc, f)["tools"], link.Name)


class CombineReviewTest(CombineTest):
    def test_a_fit_copy_with_user_work_is_kept(self):
        f = self.make(operation="cut", clearance="0.2 mm", apply_to="tool")
        fit = self.fit()[0]
        sk = util.add_rect_sketch(fit, 2, 2, "UserSk")
        util.add_pad(fit, sk, 2, "UserPad")
        self.doc.recompute()
        combine.update(self.doc, f, dict(combine.read(self.doc, f), apply_to="cavity"))
        self.doc.recompute()
        self.assertIsNotNone(self.doc.getObject(fit.Name))
        self.assertIsNotNone(self.doc.getObject("UserPad"))

    def test_untouched_fit_copy_is_removed(self):
        f = self.make(operation="cut", clearance="0.2 mm", apply_to="tool")
        combine.update(self.doc, f, dict(combine.read(self.doc, f), apply_to="cavity"))
        self.doc.recompute()
        self.assertEqual(self.fit(), [])

    def test_a_threaded_tool_gets_its_clearance_too(self):
        """It was cut with Clearance 0 mm (the grown copy failed with sharp joins); the user's jar lid then would not
        go on. Round joins grow a threaded tool: the clearance is kept all over."""
        from incad.features import thread
        top = [i + 1 for i, fc in enumerate(self.pin.Shape.Faces) if fc.Surface.TypeId == "Part::GeomCylinder"][0]
        t = thread.create(self.doc, self.tool, None, dict(thread.defaults(self.doc, self.tool, None),
                                                          face="%s:Face%d" % (self.pin.Name, top), size="M8"))
        self.doc.recompute()
        self.assertIs(self.tool.Tip, t)
        f = self.make(operation="cut", clearance="0.2 mm", apply_to="cavity")
        self.assertNotIn("Invalid", f.State)
        self.assertEqual(combine.adjusted(self.doc, f), {})
        self.assertEqual(combine.read(self.doc, f)["clearance"], "0.2 mm")
        self.assertAlmostEqual(self.tool.Shape.distToShape(self.base.Shape)[0], 0.2, places=2)

    def test_dependency_loop_is_refused(self):
        self.make(operation="cut", clearance="0 mm")
        with self.assertRaises(ValueError) as ctx:
            v = dict(combine.defaults(self.doc, self.tool, None), base=self.tool.Name, tools=self.base.Name,
                     operation="cut", clearance="0 mm")
            combine.create(self.doc, self.tool, None, v)
        self.assertIn("depends on", str(ctx.exception))

    def test_changing_the_base_on_edit_is_refused_plainly(self):
        other = self.doc.addObject("PartDesign::Body", "Other")
        other.Tip = util.add_pad(other, util.add_rect_sketch(other, 5, 5, "OS"), 5, "OP")
        self.doc.recompute()
        f = self.make(operation="cut", clearance="0 mm")
        with self.assertRaises(ValueError) as ctx:
            combine.update(self.doc, f, dict(combine.read(self.doc, f), base=other.Name))
        self.assertIn("base", str(ctx.exception).lower())

    def test_renamed_clearance_parameter_is_followed(self):
        f = self.make(operation="cut", clearance="0.2 mm", apply_to="split")
        model.rename_model_parameter(self.doc, combine.clearance_parameter(f), "fitgap")
        self.assertEqual(combine.clearance_parameter(f), "fitgap")
        combine.update(self.doc, f, dict(combine.read(self.doc, f), clearance="0.3 mm"))
        self.doc.recompute()
        self.assertEqual(model.parameter_text(self.doc, "fitgap"), "0.3 mm")
        self.assertAlmostEqual(f.Shape.Volume, BLOCK - math.pi * 4.15 ** 2 * 10, places=1)


class KeepToolTest(unittest.TestCase):
    setUp, tearDown, make = CombineTest.setUp, CombineTest.tearDown, CombineTest.make

    def test_off_by_default_the_tool_is_used_up(self):
        f = self.make(operation="cut", clearance="0 mm")
        self.assertFalse(combine.read(self.doc, f)["keep_tool"])
        self.assertFalse(self.tool.Visibility)
        self.assertEqual(combine.used_up_tools(f), [self.tool])
        self.assertTrue(combine.is_used_up(self.tool))

    def test_on_the_tool_stays_a_visible_solid_and_can_switch(self):
        f = self.make(operation="cut", clearance="0 mm", keep_tool=True)
        self.assertTrue(self.tool.Visibility)
        self.assertEqual(combine.used_up_tools(f), [])
        self.assertFalse(combine.is_used_up(self.tool))
        combine.update(self.doc, f, dict(combine.read(self.doc, f), keep_tool=False))
        self.doc.recompute()
        self.assertFalse(self.tool.Visibility)

    def test_the_fit_copy_stays_visible_when_the_tool_is_used_up(self):
        f = self.make(operation="cut", clearance="0.2 mm", apply_to="tool")
        fit = self.doc.getObject(combine._load(f)["helpers"][self.tool.Name]["fit"])
        self.assertTrue(fit.Visibility)
        self.assertFalse(self.tool.Visibility)

    def test_delete_brings_the_tool_back(self):
        from incad.core import delete
        f = self.make(operation="cut", clearance="0 mm")
        delete.delete(f)
        self.assertTrue(self.tool.Visibility)
        self.assertFalse(combine.is_used_up(self.tool))

    def test_an_old_boolean_reads_as_used_up(self):
        f = self.make(operation="cut", clearance="0 mm", keep_tool=True)
        data = combine._load(f)
        del data["keep_tool"]
        combine._store(f, data)
        self.assertFalse(combine.read(self.doc, f)["keep_tool"])
        self.assertTrue(combine.is_used_up(self.tool))

class ClearanceLabelTest(unittest.TestCase):
    def test_each_clearance_says_how_it_is_measured(self):
        from incad.features import hole, thread
        label = {k.__name__.rsplit(".", 1)[-1]: next(f.label for f in k.FIELDS if f.key == "clearance")
                 for k in (combine, hole, thread)}
        self.assertEqual(label, {"combine": "Clearance (per side)", "hole": "Print clearance (on diameter)",
                                 "thread": "Print clearance (on diameter)"})
