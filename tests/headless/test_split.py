"""Split by sketch: sketch lines extruded into zero-thickness knives cut the part."""
import os
import tempfile
import unittest

import FreeCAD as App
import Part

from incad.core import document
from incad.features import split
from headless import util


def line_sketch(body, *segments, name="Knife"):
    """A sketch on XY with the given segments ((x1, y1), (x2, y2)); arcs as (centre, r, a0, a1)."""
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(util.xy_plane(body), "")]
    sk.MapMode = "FlatFace"
    for seg in segments:
        if len(seg) == 2:
            (x1, y1), (x2, y2) = seg
            sk.addGeometry(Part.LineSegment(App.Vector(x1, y1, 0), App.Vector(x2, y2, 0)))
        else:
            (cx, cy), r, a0, a1 = seg
            sk.addGeometry(Part.ArcOfCircle(Part.Circle(App.Vector(cx, cy, 0), App.Vector(0, 0, 1), r), a0, a1))
    return sk


class SplitTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Sp")
        self.body, self.pad = util.padded_part(self.doc, 10, incad=True)  # 20 x 10 x 10
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def make(self, sk, **kw):
        values = dict(split.defaults(self.doc, self.body, None), sketch=sk.Name)
        values.update(kw)
        f = split.create(self.doc, self.body, None, values)
        self.doc.recompute()
        return f

    def pieces(self, f):
        """The Split's own piece and the Solids it made, as volumes (largest first)."""
        return sorted([round(s.Volume, 6) for s in f.Shape.Solids] +
                      [round(b.Shape.Volume, 6) for b in split.piece_bodies(f)], reverse=True)

    def test_keep_all_puts_every_other_piece_in_its_own_solid(self):
        """The user: with both pieces in one Solid, Hide / Show could not hide one of them (Inventor's Split Solid
        makes separate bodies)."""
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15))))
        self.assertEqual(len(f.Shape.Solids), 1)
        self.assertAlmostEqual(f.Shape.Volume, 1500, places=6)  # the largest stays in this Solid
        others = split.piece_bodies(f)
        self.assertEqual(len(others), 1)
        self.assertIsNot(others[0], self.body)
        self.assertAlmostEqual(others[0].Shape.Volume, 500, places=6)
        self.assertIs(self.body.Tip, f)
        self.assertFalse([o.Name for o in self.doc.Objects if "Invalid" in o.State])

    def test_a_sketch_in_the_empty_first_solid_splits_the_solid_it_cuts(self):
        """The user: the sketch in the empty Solid1, the part (a rebuilt STL) in another Solid — Split said
        "'NoneType' object has no attribute 'Shape'". It splits the Solid its lines cut, the sketch going there."""
        doc = util.new_doc("SpEmpty")
        try:
            first = document.make_part(doc)
            other = document.new_solid(doc)
            pad = util.add_pad(other, util.add_rect_sketch(other, 20, 10, "Base"), 10)
            other.Tip = pad
            doc.recompute()
            sk = line_sketch(first, ((5, -5), (5, 15)))
            doc.recompute()
            f = split.create(doc, first, None, dict(split.defaults(doc, first, None), sketch=sk.Name))
            doc.recompute()
            self.assertIs(f.getParentGeoFeatureGroup(), other)
            self.assertIs(sk.getParentGeoFeatureGroup(), other)
            self.assertNotIn("Invalid", f.State, f.getStatusString())
            self.assertAlmostEqual(f.Shape.Volume, 1500, places=4)
            self.assertEqual(len(split.piece_bodies(f)), 1)
        finally:
            App.closeDocument(doc.Name)

    def test_make_assembly_after_ok_only_when_ticked(self):
        """The user: after a Split, an option to make an assembly. Ticked (Keep all), OK opens Make Assembly; not
        ticked, or Keep picked, it does not; an edit opens with it off (a second assembly is never made by itself)."""
        from incad import actions
        calls = []
        saved = actions.make_assembly
        actions.make_assembly = lambda: calls.append(1)
        try:
            f = self.make(line_sketch(self.body, ((5, -5), (5, 15))))
            split.after_ok(self.doc, f, dict(split.read(self.doc, f), assembly=True))
            split.after_ok(self.doc, f, dict(split.read(self.doc, f), assembly=False))
            split.after_ok(self.doc, f, dict(split.read(self.doc, f), assembly=True, mode="cut"))
        finally:
            actions.make_assembly = saved
        self.assertEqual(calls, [1])
        self.assertFalse(split.read(self.doc, f)["assembly"])
        self.assertIn("assembly", [fd.key for fd in split.FIELDS])

    def test_gap_from_both_this_solid_or_the_new_ones(self):
        """The user: a gap between the pieces, taken from one of them or from both."""
        for side, want in (("both", [1480.0, 480.0]), ("this", [1460.0, 500.0]), ("new", [1500.0, 460.0])):
            f = self.make(line_sketch(self.body, ((5, -5), (5, 15)), name="Knife_" + side), gap="0.4 mm",
                          gap_from=side)
            self.assertEqual(self.pieces(f), want, side)
            self.assertAlmostEqual(f.Shape.Volume, want[0], places=6)  # this Solid keeps the largest piece
            for b in split.piece_bodies(f):
                split._drop_piece_body(self.doc, b)
            self.body.removeObject(f)
            self.doc.removeObject(f.Name)
            self.body.Tip = self.pad
            self.doc.recompute()

    def test_gap_is_a_parameter_read_back_and_edited(self):
        from incad.core import model
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15))), gap="0.3 mm", gap_from="new")
        name = split.param_names(f)["gap"]
        self.assertIn(name, [p[0] for p in model.feature_parameters(self.doc)])
        values = split.read(self.doc, f)
        self.assertEqual((values["gap"], values["gap_from"]), ("0.3 mm", "new"))
        split.update(self.doc, f, dict(values, gap="0.5 mm", gap_from="both"))
        self.assertEqual(self.pieces(f), [1475.0, 475.0])
        self.assertEqual(split.param_names(f)["gap"], name)  # the same parameter, a new value
        model.set_model_parameter(self.doc, name, "0.1 mm")
        self.doc.recompute()
        self.assertEqual(self.pieces(f), [1495.0, 495.0])  # the Parameters list drives it

    def test_gap_steps_by_five_hundredths(self):
        from incad.ui.feature_dialog import step_text
        field = next(x for x in split.FIELDS if x.key == "gap")
        self.assertEqual(field.step({}), 0.05)
        self.assertEqual(step_text("0.2 mm", 1, "value_length", False, field.step({})), "0.25 mm")
        self.assertEqual(step_text("0 mm", -1, "value_length", False, field.step({})), "0 mm")  # never below 0

    def test_no_gap_is_no_parameter_and_a_bad_gap_refuses(self):
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15))))
        self.assertEqual(split.param_names(f), {})
        self.assertEqual(self.pieces(f), [1500.0, 500.0])
        with self.assertRaises(ValueError):
            split.update(self.doc, f, dict(split.read(self.doc, f), gap="-1 mm"))
        with self.assertRaises(ValueError) as ctx:
            split.update(self.doc, f, dict(split.read(self.doc, f), gap="12 mm", gap_from="new"))
        self.assertIn("gap", str(ctx.exception).lower())

    def test_gap_along_an_arc_and_with_keep_picked(self):
        import math
        f = self.make(line_sketch(self.body, ((0, 0), 6, 0.0, math.pi / 2)), gap="0.4 mm")
        self.assertEqual(len(split.piece_bodies(f)), 1)
        quarter = math.pi * 36 / 4 * 10
        self.assertAlmostEqual(sum(self.pieces(f)), 2000 - math.pi * 6 / 2 * 0.4 * 10, places=2)
        self.assertLess(min(self.pieces(f)), quarter)

    def test_gap_round_a_bend_leaves_no_sliver(self):
        """The user (Windows): a strange sliver where an L-shaped cut line bends — each knife's slab alone left a
        wedge outside the bend. Every piece now stands its share of the gap off the whole line, bend included."""
        sk = line_sketch(self.body, ((8, 15), (8, 4)), ((8, 4), (25, 1)))
        f = self.make(sk, gap="1 mm")
        line = Part.Wire(Part.__sortEdges__(sk.Shape.Edges)[0])
        solids = list(f.Shape.Solids) + [s for b in split.piece_bodies(f) for s in b.Shape.Solids]
        self.assertEqual(len(solids), 2)
        for s in solids:
            for v in s.Vertexes:
                p = App.Vector(v.Point.x, v.Point.y, 0)
                self.assertGreater(line.distToShape(Part.Vertex(p))[0], 0.5 - 1e-4, (v.Point, s.Volume))

    def test_gap_with_keep_picked_takes_half_from_each_side(self):
        g = self.make(line_sketch(self.body, ((15, -5), (15, 15))), mode="cut", keep="2 5 5", gap="0.4 mm",
                      gap_from="this")  # the choice is hidden in Keep picked: half from each side
        self.assertAlmostEqual(g.Shape.Volume, 1500 - 20, places=4)

    def test_gap_saved_and_reopened(self):
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15))), gap="0.4 mm", gap_from="this")
        path = os.path.join(tempfile.mkdtemp(), "gap.FCStd")
        name = f.Name
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        self.doc.recompute()
        f = self.doc.getObject(name)
        self.assertEqual(self.pieces(f), [1460.0, 500.0])
        self.assertEqual(split.read(self.doc, f)["gap_from"], "this")

    def test_a_piece_solid_follows_the_sketch(self):
        sk = line_sketch(self.body, ((5, -5), (5, 15)))
        f = self.make(sk)
        sk.delGeometry(0)
        sk.addGeometry(Part.LineSegment(App.Vector(4, -5, 0), App.Vector(4, 15, 0)))
        self.doc.recompute()
        self.assertEqual(self.pieces(f), [1600.0, 400.0])

    def test_the_knife_edited_outside_the_dialog_keeps_each_piece_in_one_solid(self):
        """A line deleted from the knife made a piece Solid copy the part's own piece (material twice); lines added
        lost a piece. Shared out again right after the recompute (as the window does)."""
        sk = line_sketch(self.body, ((5, -5), (5, 15)), ((15, -5), (15, 15)))
        f = self.make(sk)
        self.assertEqual(len(split.piece_bodies(f)), 2)
        sk.delGeometries([1])
        self.doc.recompute()
        split.sync_pending()
        self.doc.recompute()
        vols = sorted(round(b.Shape.Volume, 3) for b in document.bodies(self.doc))
        self.assertEqual(vols, [500.0, 1500.0])  # 20 x 10 x 10 cut at x = 5 only
        sk.addGeometry(Part.LineSegment(App.Vector(10, -5, 0), App.Vector(10, 15, 0)))
        sk.addGeometry(Part.LineSegment(App.Vector(15, -5, 0), App.Vector(15, 15, 0)))
        self.doc.recompute()
        split.sync_pending()
        self.doc.recompute()
        vols = sorted(round(b.Shape.Volume, 3) for b in document.bodies(self.doc))
        self.assertEqual(vols, [500.0, 500.0, 500.0, 500.0])

    def test_a_pad_on_a_piece_solid_stays_in_it(self):
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15))))
        other = split.piece_bodies(f)[0]
        top = util.add_rect_sketch(other, w=2, h=2, name="Bump")
        top.AttachmentOffset = App.Placement(App.Vector(1, 1, 10), App.Rotation())
        bump = util.add_pad(other, top, 3, name="BumpPad")
        other.Tip = bump
        self.doc.recompute()
        self.assertAlmostEqual(other.Shape.Volume, 500 + 12, places=6)
        self.assertAlmostEqual(f.Shape.Volume, 1500, places=6)  # nothing fused back

    def test_keep_all_reads_no_picks(self):
        """GUI: the Split's own piece point came back as a Keep pick, so switching to Keep picked did not start
        picking."""
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15))))
        self.assertEqual(split.read(self.doc, f)["keep"], "")

    def test_switching_to_keep_picked_removes_the_piece_solids(self):
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15))))
        split.update(self.doc, f, dict(split.read(self.doc, f), mode="cut", keep="2 1 5"))
        self.doc.recompute()
        self.assertEqual(split.piece_bodies(f), [])
        self.assertEqual(len(document.bodies(self.doc)), 1)

    def test_an_arc_cuts_a_curved_face(self):
        import math
        f = self.make(line_sketch(self.body, ((0, 5), 8, -math.pi / 2, math.pi / 2)))
        self.assertEqual(len(self.pieces(f)), 2)
        self.assertTrue(any(face.Surface.TypeId == "Part::GeomCylinder" for face in f.Shape.Faces))
        self.assertAlmostEqual(sum(self.pieces(f)), 2000, places=6)

    def test_two_lines_give_three_pieces(self):
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15)), ((15, -5), (15, 15))))
        self.assertEqual(self.pieces(f), [1000.0, 500.0, 500.0])
        self.assertEqual(len(split.piece_bodies(f)), 2)

    def test_cut_keeps_the_picked_piece(self):
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15))), mode="cut", keep="2 1 5")
        self.assertEqual(len(f.Shape.Solids), 1)
        self.assertAlmostEqual(f.Shape.Volume, 500, places=6)

    def test_cut_follows_a_changed_sketch(self):
        sk = line_sketch(self.body, ((5, -5), (5, 15)))
        f = self.make(sk, mode="cut", keep="4.9 1 5")  # near the knife
        sk.delGeometry(0)  # the knife moves to x = 4: the kept spot (4.9) is now in the other piece's place
        sk.addGeometry(Part.LineSegment(App.Vector(4, -5, 0), App.Vector(4, 15, 0)))
        self.doc.recompute()
        self.assertEqual(len(f.Shape.Solids), 1)  # the piece holding the kept spot now, never nothing
        self.assertAlmostEqual(f.Shape.Volume, 1600, places=6)

    def test_a_sketch_missing_the_part_refuses(self):
        with self.assertRaises(ValueError) as ctx:
            self.make(line_sketch(self.body, ((50, -5), (50, 15))))
        self.assertIn("does not cut", str(ctx.exception))
        self.assertIs(self.body.Tip, self.pad)

    def test_a_line_ending_inside_refuses(self):
        with self.assertRaises(ValueError):
            self.make(line_sketch(self.body, ((5, -5), (5, 5))))  # stops in the middle

    def test_keep_piece_switches_to_cut(self):
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15))))
        split.keep_piece(f, App.Vector(2, 5, 5))
        self.doc.recompute()
        self.assertEqual(f.Mode, "cut")
        self.assertAlmostEqual(f.Shape.Volume, 500, places=6)

    def test_save_and_reopen(self):
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15))))
        path = os.path.join(tempfile.mkdtemp(), "split.FCStd")
        self.doc.saveAs(path)
        name = f.Name
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        g = self.doc.getObject(name)
        g.touch()
        self.doc.recompute()
        self.assertEqual(self.pieces(g), [1500.0, 500.0])  # the piece Solid's proxy is found too
        self.assertFalse([o.Name for o in self.doc.Objects if "Invalid" in o.State])
        self.assertTrue(split.claims(g))


    def test_the_knife_sketch_is_used_by_the_split(self):
        """Review: the knife counted as an unused sketch, so it was shown again after OK."""
        from incad.features import common
        sk = line_sketch(self.body, ((5, -5), (5, 15)))
        f = self.make(sk)
        self.assertIn(f, common.consumers(sk))

    def test_edit_reads_the_keep_picks_back(self):
        """Review: read() gave 'x y z' numbers, so the first click while editing replaced every kept piece."""
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15))))
        big = max(f.Shape.Solids, key=lambda s: s.Volume)
        face = next(i + 1 for i, fc in enumerate(f.Shape.Faces) if big.isInside(fc.CenterOfMass, 1e-6, True))
        pick = "%s:Face%d" % (f.Name, face)
        split.update(self.doc, f, dict(split.read(self.doc, f), mode="cut", keep=pick))
        self.assertEqual(split.read(self.doc, f)["keep"], pick)


    def test_deleting_the_split_takes_its_empty_piece_solids_and_freezes_worked_ones(self):
        from incad.core import delete
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15)), ((15, -5), (15, 15))))
        empty, worked = split.piece_bodies(f)
        top = util.add_rect_sketch(worked, w=2, h=2, name="Bump")
        top.AttachmentOffset = App.Placement(App.Vector(16, 1, 10), App.Rotation())
        worked.Tip = util.add_pad(worked, top, 3, name="BumpPad")
        self.doc.recompute()
        before = worked.Shape.Volume
        empty_name, worked_name = empty.Name, worked.Name
        delete.delete(f)
        self.doc.recompute()
        self.assertIsNone(self.doc.getObject(empty_name))
        self.assertIsNotNone(self.doc.getObject(worked_name))
        self.assertAlmostEqual(worked.Shape.Volume, before, places=6)  # its piece kept, frozen
        self.assertFalse([o.Name for o in self.doc.Objects if "Invalid" in o.State or o.TypeId == "Part::FeaturePython"])

    def test_deleting_a_piece_solid_takes_its_piece(self):
        from incad.core import delete
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15))))
        other = split.piece_bodies(f)[0]
        delete.delete(other)
        self.doc.recompute()
        self.assertFalse([o.Name for o in self.doc.Objects if o.TypeId == "Part::FeaturePython"])
        self.assertFalse([o.Name for o in self.doc.Objects if "Invalid" in o.State])


    def test_an_old_split_gets_its_piece_solids(self):
        """A file from before Keep all made piece Solids: both pieces in the Split's Solid until it is upgraded."""
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15))))
        for b in split.piece_bodies(f):  # back to the old state: no piece Solids, no kept point
            split._drop_piece_body(self.doc, b)
        f.Keep = []
        self.doc.recompute()
        self.assertEqual(len(f.Shape.Solids), 2)
        self.assertEqual(split.upgrade(self.doc), [f])
        self.assertEqual(len(f.Shape.Solids), 1)
        self.assertEqual(len(split.piece_bodies(f)), 1)
        self.assertEqual(split.upgrade(self.doc), [])


    def test_a_piece_solid_used_by_a_boolean_is_not_deleted(self):
        """The user's Thread_1: Solid2 was the tool of a Cut; deleting it broke the Cut ('No source shape linked')."""
        from incad.core import delete
        from incad.features import combine
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15))))
        tool = split.piece_bodies(f)[0]
        cut = combine.create(self.doc, self.body, None, dict(combine.defaults(self.doc, self.body, None),
                                                             base=self.body.Name, tools=tool.Name, keep_tool=True))
        self.doc.recompute()
        with self.assertRaises(ValueError) as ctx:
            delete.delete(tool)
        self.assertIn(cut.Label, str(ctx.exception))
        self.assertIsNotNone(self.doc.getObject(tool.Name))
        self.assertFalse([o.Name for o in self.doc.Objects if "Invalid" in o.State])


    def test_deleting_a_tool_solid_with_its_boolean(self):
        from incad.core import delete
        from incad.features import combine
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15))))
        tool = split.piece_bodies(f)[0]
        cut = combine.create(self.doc, self.body, None, dict(combine.defaults(self.doc, self.body, None),
                                                             base=self.body.Name, tools=tool.Name, keep_tool=True))
        self.doc.recompute()
        self.assertEqual(delete.users_of(tool), [cut])
        tool_name, cut_name = tool.Name, cut.Name
        delete.delete(tool, with_users=True)
        self.doc.recompute()
        self.assertTrue(self.body.Tip.Visibility, "the user: Solid1 vanished too — its new End of Part was hidden")
        self.assertIsNone(self.doc.getObject(tool_name))
        self.assertIsNone(self.doc.getObject(cut_name))
        self.assertFalse([o.Name for o in self.doc.Objects if "Invalid" in o.State])

    def test_deleting_the_main_solid_makes_the_piece_the_main_one(self):
        """The user: 'The main solid can't be deleted' — keep only the second piece."""
        from incad.core import delete
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15))))
        piece = split.piece_bodies(f)[0]
        self.assertEqual(delete.main_successor(self.body), piece)
        main_name = self.body.Name
        delete.delete(self.body, replace_main=True)
        self.doc.recompute()
        self.assertIsNone(self.doc.getObject(main_name))
        self.assertIs(document.primary_body(self.doc), piece)
        self.assertAlmostEqual(piece.Shape.Volume, 500, places=6)  # frozen: its Split went with the main solid
        self.assertFalse([o.Name for o in self.doc.Objects if "Invalid" in o.State or o.TypeId == "Part::FeaturePython"])


    def test_deleting_the_split_keeps_a_piece_solid_that_a_boolean_uses(self):
        from incad.core import delete
        from incad.features import combine
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15))))
        tool = split.piece_bodies(f)[0]
        combine.create(self.doc, self.body, None, dict(combine.defaults(self.doc, self.body, None),
                                                       base=self.body.Name, tools=tool.Name, keep_tool=True))
        self.doc.recompute()
        tool_name = tool.Name
        delete.delete(f)
        self.doc.recompute()
        self.assertIsNotNone(self.doc.getObject(tool_name), "frozen, not dropped: its Boolean still needs it")
        self.assertFalse([o.Name for o in self.doc.Objects if "Invalid" in o.State])

    def test_a_pick_on_a_piece_base_means_its_solid(self):
        from incad.core import delete
        f = self.make(line_sketch(self.body, ((5, -5), (5, 15))))
        piece = split.piece_bodies(f)[0]
        base = next(o for o in piece.Group if o.TypeId == "PartDesign::FeatureBase")
        self.assertIs(delete.target_for(base), piece)
        self.assertIs(delete.target_for(piece.BaseFeature), piece)
        self.assertIs(delete.target_for(f), f)


if __name__ == "__main__":
    unittest.main()
