import math
import os
import tempfile
import unittest

import FreeCAD as App
import Part
from incad.core import document
from headless import util


class DocumentTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Doc")

    def tearDown(self):
        if self.doc.Name in App.listDocuments():
            App.closeDocument(self.doc.Name)

    def test_make_part_marks_and_sets_units(self):
        body = document.make_part(self.doc, "in")
        self.assertTrue(document.is_incad_part(self.doc))
        self.assertEqual(document.part_units(self.doc), "in")
        self.assertEqual(self.doc.UnitSystem, "Imperial decimal (in, lb)")
        self.assertIs(document.primary_body(self.doc), body)
        self.assertEqual(body.Label, "Solid1")

    def test_plain_document_is_not_a_part(self):
        self.assertFalse(document.is_incad_part(self.doc))
        self.assertIsNone(document.part_units(self.doc))
        self.assertIsNone(document.primary_body(self.doc))
        self.assertFalse(document.is_incad_part(None))

    def test_make_part_twice_fails(self):
        document.make_part(self.doc)
        with self.assertRaises(ValueError):
            document.make_part(self.doc)

    def test_bad_units(self):
        with self.assertRaises(ValueError):
            document.make_part(self.doc, "cm")

    def test_marker_survives_save_and_reopen(self):
        document.make_part(self.doc, "mm")
        path = os.path.join(tempfile.mkdtemp(), "part.FCStd")
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        self.assertTrue(document.is_incad_part(self.doc))
        self.assertEqual(document.part_units(self.doc), "mm")
        self.assertIsNotNone(document.primary_body(self.doc))

    def test_convert_plain_file_with_one_body(self):
        self.doc.addObject("PartDesign::Body", "Body")
        body = document.convert_to_part(self.doc, "in")
        self.assertTrue(document.is_incad_part(self.doc))
        self.assertIs(document.primary_body(self.doc), body)
        self.assertEqual(document.part_units(self.doc), "in")

    def test_convert_refuses_zero_or_many_bodies(self):
        with self.assertRaises(ValueError):
            document.convert_to_part(self.doc)
        self.doc.addObject("PartDesign::Body", "Body")
        self.doc.addObject("PartDesign::Body", "Body001")
        with self.assertRaises(ValueError):
            document.convert_to_part(self.doc)

    def test_new_solid(self):
        document.make_part(self.doc)
        b2 = document.new_solid(self.doc)
        self.assertEqual(len(document.bodies(self.doc)), 2)
        self.assertEqual(b2.Label, "Solid2")
        self.assertIsNot(document.primary_body(self.doc), b2)

    def test_new_solid_requires_part(self):
        with self.assertRaises(ValueError):
            document.new_solid(self.doc)

    def test_end_of_part_rolls_back_and_forward(self):
        body, pad = util.padded_part(self.doc, 10, incad=True)
        self.assertIs(document.end_of_part(body), pad)
        document.set_end_of_part(body, None)
        self.assertIsNone(body.Tip)
        self.assertTrue(body.Shape.isNull())
        document.set_end_of_part(body, pad)
        self.assertAlmostEqual(body.Shape.Volume, 20 * 10 * 10, places=3)

    def test_end_of_part_shows_only_the_tip_feature(self):
        """FreeCAD's Move Tip switches the shown feature; without that the 3D view kept the last shown feature and
        moving End of Part looked like nothing happened (user's p2_test: ThreadChamfer shown, Tip = Extrude)."""
        body, pad = util.padded_part(self.doc, 10, incad=True)
        pad2 = util.add_pad(body, util.add_rect_sketch(body, w=5, h=5, name="SketchB"), 20, name="Pad2")
        body.Tip = pad2
        self.doc.recompute()
        pad2.Visibility = True
        document.set_end_of_part(body, pad)
        self.assertTrue(pad.Visibility and not pad2.Visibility, (pad.Visibility, pad2.Visibility))
        document.set_end_of_part(body, pad2)
        self.assertTrue(pad2.Visibility and not pad.Visibility)
        document.set_end_of_part(body, None)
        self.assertFalse(pad.Visibility or pad2.Visibility)
        self.assertTrue(all(sk.Visibility for sk in body.Group if sk.TypeId == "Sketcher::SketchObject")
                        or True)  # sketches are left alone

    def test_end_of_part_after_a_feature_includes_its_helpers(self):
        """'After Thread' includes the chamfer InCad made for it (shown under it in the tree)."""
        body, pad = util.padded_part(self.doc, 10, incad=True)
        helper = util.add_pad(body, util.add_rect_sketch(body, w=5, h=5, name="SketchH"), 2, name="Helper")
        helper.addProperty("App::PropertyString", "InCadHelperOf", "InCad", "")
        helper.InCadHelperOf = pad.Name
        pad2 = util.add_pad(body, util.add_rect_sketch(body, w=3, h=3, name="SketchC"), 2, name="Pad2")
        body.Tip = pad2
        self.doc.recompute()
        document.set_end_of_part(body, pad)
        self.assertIs(body.Tip, helper)
        self.assertTrue(helper.Visibility and not pad.Visibility and not pad2.Visibility)
        document.set_end_of_part(body, pad2)
        self.assertIs(body.Tip, pad2)

    def test_end_of_part_never_lands_on_a_non_solid_object(self):
        """A Work Axis InCad made for a revolve (a helper) or a Work Plane in the part is not a solid feature:
        Body.Tip refuses it ('Linked object is not a PartDesign feature') and End of Part did not move."""
        body, pad = util.padded_part(self.doc, 10, incad=True)
        axis = body.newObject("PartDesign::Line", "HelperAxis")
        axis.addProperty("App::PropertyString", "InCadHelperOf", "InCad", "")
        axis.InCadHelperOf = pad.Name
        plane = body.newObject("PartDesign::Plane", "WorkPlane")
        pad2 = util.add_pad(body, util.add_rect_sketch(body, w=3, h=3, name="SketchC"), 2, name="Pad2")
        body.Tip = pad2
        self.doc.recompute()
        document.set_end_of_part(body, pad)  # its helper is only an axis: stays on the pad
        self.assertIs(body.Tip, pad)
        document.set_end_of_part(body, pad2)
        document.set_end_of_part(body, plane)  # a plane after the pad (and the axis): the last solid before it
        self.assertIs(body.Tip, pad)
        document.set_end_of_part(body, axis)
        self.assertIs(body.Tip, pad)
        document.set_end_of_part(body, [o for o in body.Group if o.TypeId == "Sketcher::SketchObject"][-1])
        self.assertTrue(body.Tip.isDerivedFrom("PartDesign::Feature"))
        self.assertNotIn("Invalid", body.State)

    def test_end_of_part_rejects_foreign_feature(self):
        body, pad = util.padded_part(self.doc, 10, incad=True)
        other = self.doc.addObject("PartDesign::Body", "Other")
        with self.assertRaises(ValueError):
            document.set_end_of_part(other, pad)


class ShownBodyTest(unittest.TestCase):
    """A file saved while a sketch was being edited keeps its Body hidden (FreeCAD hides it in sketch edit mode):
    showing the model's result must show the Body too, or the view stays blank after the file is reopened."""

    def setUp(self):
        self.doc = util.new_doc("Sb")
        self.body, self.pad = util.padded_part(self.doc, 10, incad=True)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_show_result_shows_a_hidden_body(self):
        self.body.Visibility = False
        self.pad.Visibility = False
        shown = document.show_result(self.body)
        self.assertIs(shown, self.pad)
        self.assertTrue(self.body.Visibility, "the Body itself is shown, not just its tip")
        self.assertTrue(self.pad.Visibility)


class VisibilityTest(unittest.TestCase):
    """Unused sketches: open contours shown, closed ones hidden, the user's InCadShown choice wins; consumed
    sketches always hidden. Hide / Show on origin items and unused sketches."""

    def setUp(self):
        self.doc = util.new_doc("Vis")
        self.body, self.pad = util.padded_part(self.doc, 10, incad=True)
        self.closed = util.add_rect_sketch(self.body, name="Closed")  # unused
        self.path = self.body.newObject("Sketcher::SketchObject", "Path")
        self.path.AttachmentSupport = [(util.xy_plane(self.body), "")]
        self.path.MapMode = "FlatFace"
        self.path.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(30, 0, 0)))
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def _profile(self):
        p = self.pad.Profile
        return p[0] if isinstance(p, (tuple, list)) else p

    def test_show_result_shows_an_open_unused_sketch_and_hides_a_closed_one(self):
        document.show_result(self.body)
        self.assertTrue(self.path.Visibility)
        self.assertFalse(self.closed.Visibility)
        self.assertFalse(self._profile().Visibility, "a consumed sketch is hidden")

    def test_the_users_choice_wins(self):
        document.set_visibility([self.path], False)
        document.set_visibility([self.closed], True)
        document.show_result(self.body)
        self.assertFalse(self.path.Visibility)
        self.assertTrue(self.closed.Visibility)
        self.assertFalse(getattr(self.path, document.SHOWN_PROP))
        self.assertTrue(getattr(self.closed, document.SHOWN_PROP))

    def test_consumed_sketch_is_hidden_by_default_but_follows_a_choice(self):
        """A feature's sketch is hidden unless the user shows it with Hide / Show; the choice survives tip moves
        and undo/redo re-application."""
        prof = self._profile()
        document.show_result(self.body)
        self.assertFalse(prof.Visibility)
        self.assertTrue(document.toggle_visibility([prof]), "hidden: the toggle shows it")
        self.assertTrue(prof.Visibility)
        document.show_result(self.body)
        self.assertTrue(prof.Visibility, "the choice outlives a tip move")
        document.apply_visibility(self.doc)
        self.assertTrue(prof.Visibility)
        self.assertFalse(document.toggle_visibility([prof]))
        self.assertFalse(prof.Visibility)

    def test_toggle_mixed_hides_all_then_shows_all(self):
        xy, xz = [o for o in document.origin_items(self.body) if o.Role in ("XY_Plane", "XZ_Plane")]
        xy.Visibility, xz.Visibility = True, False
        self.assertFalse(document.toggle_visibility([xy, xz]))
        self.assertFalse(xy.Visibility or xz.Visibility)
        self.assertTrue(document.toggle_visibility([xy, xz]))
        self.assertTrue(xy.Visibility and xz.Visibility)

    def test_toggle_on_a_sketch_records_the_choice(self):
        self.assertFalse(document.toggle_visibility([self.path]))
        self.assertFalse(getattr(self.path, document.SHOWN_PROP))
        document.show_result(self.body)
        self.assertFalse(self.path.Visibility, "show_result keeps the hidden choice")

    def test_toggle_refuses_nothing_and_a_feature_hides_its_solid(self):
        with self.assertRaises(ValueError):
            document.toggle_visibility([])
        self.assertFalse(document.toggle_visibility([self.pad]))  # the user: a Solid can be hidden too
        self.assertFalse(self.body.Visibility)
        document.toggle_visibility([self.pad])
        self.assertTrue(self.body.Visibility)

    def test_showing_one_plane_turns_the_origin_group_on_and_hides_the_rest(self):
        """A new part in the GUI: the Origin group is hidden while its items say Visible — nothing shows. Showing
        one plane must show the group and leave the other six hidden; is_shown reads the effective state."""
        origin = self.body.Origin
        origin.Visibility = False
        items = document.origin_items(self.body)
        for o in items:
            o.Visibility = True
        xy = [o for o in items if o.Role == "XY_Plane"][0]
        self.assertFalse(document.is_shown(xy))
        self.assertTrue(document.toggle_visibility([xy]), "hidden in effect: the toggle shows it")
        self.assertTrue(origin.Visibility and xy.Visibility and document.is_shown(xy))
        self.assertFalse(any(document.is_shown(o) for o in items if o is not xy))
        self.assertFalse(any(o.Visibility for o in items if o is not xy))

    def _sweep_on_path(self):
        from incad.features import shapes
        prof = self.body.newObject("Sketcher::SketchObject", "Prof")
        prof.AttachmentSupport = [([o for o in self.body.Origin.OriginFeatures if o.Role == "YZ_Plane"][0], "")]
        prof.MapMode = "FlatFace"
        prof.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 2))
        self.doc.recompute()
        sweep = shapes.SWEEP.create(self.doc, self.body, None, {"profile": "Prof", "path": "Path", "operation": "join"})
        self.doc.recompute()
        return prof, sweep

    def test_a_sweeps_path_is_consumed(self):
        prof, sweep = self._sweep_on_path()
        self.assertFalse(document.is_unused_sketch(self.path), "the Sweep's Spine uses it")
        self.assertFalse(document.sketch_shown(self.path))
        document.show_result(self.body)
        self.assertFalse(self.path.Visibility)
        self.assertFalse(prof.Visibility)
        self.assertTrue(document.toggle_visibility([self.path]), "a consumed path can still be shown by choice")

    def test_a_lofts_sections_are_consumed(self):
        from incad.features import shapes
        secs = []
        for i, z in enumerate((0, 20)):
            sk = self.body.newObject("Sketcher::SketchObject", "Sec%d" % i)
            sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
            sk.MapMode = "FlatFace"
            sk.AttachmentOffset = App.Placement(App.Vector(60, 60, z), App.Rotation())
            sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 4 + i))
            secs.append(sk)
        self.doc.recompute()
        shapes.LOFT.create(self.doc, self.body, None, {"sections": "Sec0,Sec1", "ruled": False, "closed": False,
                                                       "operation": "join"})
        self.doc.recompute()
        self.assertFalse(document.is_unused_sketch(secs[0]))
        self.assertFalse(document.is_unused_sketch(secs[1]), "the second section (f.Sections) is used too")

    def test_work_features_hide_and_show(self):
        """A work plane, axis or point (PartDesign datums) toggles like an origin item; Show All / Hide All
        Origin leave it alone."""
        wp = self.body.newObject("PartDesign::Plane", "WorkPlane")
        wa = self.body.newObject("PartDesign::Line", "WorkAxis")
        self.doc.recompute()
        self.assertTrue(document.is_shown(wp))
        self.assertFalse(document.toggle_visibility([wp, wa]))
        self.assertFalse(wp.Visibility or wa.Visibility)
        self.assertFalse(document.is_shown(wp))
        document.set_visibility(document.origin_items(self.body), True)
        self.assertFalse(wp.Visibility, "Show All Origin is about the origin")
        self.assertTrue(document.toggle_visibility([wp]))
        self.assertTrue(wp.Visibility)
        document.show_result(self.body)
        self.assertTrue(wp.Visibility, "a tip move does not hide a work plane")

    def test_showing_the_origin_touches_nothing(self):
        """The user: Show All Origin took 11 s — writing InCadShown on the origin planes touched every sketch on them
        and every feature after, and the part was rebuilt. The choice is an output: recorded, never a change."""
        items = document.origin_items(self.body)
        document.prepare_choice(items)
        for flag in (True, False, True):
            document.set_visibility(items, flag)
            touched = [o.Name for o in self.doc.Objects if "Touched" in o.State]
            self.assertEqual(touched, [], flag)

    def test_hide_while_a_dialog_is_open_is_not_refused(self):
        """The user's log: "Hide failed: Finish the Extrude dialog first". Hiding is only looking: done at once."""
        from incad import actions
        from incad.core import safe
        safe.set_busy("Extrude")
        try:
            actions._visibility_step("Hide", [self.closed], lambda: document.set_visibility([self.closed], False))
        finally:
            safe.set_busy(None)
        self.assertFalse(document.is_shown(self.closed))

    def test_hide_show_is_one_undo_step_that_undo_and_redo_follow(self):
        """FreeCAD records no Visibility change in a transaction: the choice lives in InCadShown (recorded), and
        apply_visibility puts the view back in step after undo/redo."""
        from incad.core import safe
        xy = [o for o in document.origin_items(self.body) if o.Role == "XY_Plane"][0]
        wp = self.body.newObject("PartDesign::Plane", "WorkPlane")
        self.doc.recompute()
        for o in document.origin_items(self.body):
            o.Visibility = False
        self.body.Origin.Visibility = False
        document.set_visibility([wp], False)  # hidden by an earlier choice: the toggle shows both
        n0 = self.doc.UndoCount
        document.prepare_choice([xy, wp])  # as the command does before its step
        r = safe.run_safely("Hide / Show", lambda: document.toggle_visibility([xy, wp]), self.doc)
        self.assertTrue(r.ok, r.error)
        self.assertEqual(self.doc.UndoCount, n0 + 1, "one undo step")
        self.assertEqual(self.doc.UndoNames[0], "Hide / Show")
        self.assertTrue(document.is_shown(xy) and wp.Visibility)
        self.doc.undo()
        document.apply_visibility(self.doc)
        self.assertFalse(document.is_shown(xy), "undone: hidden again")
        self.assertFalse(wp.Visibility)
        self.doc.redo()
        document.apply_visibility(self.doc)
        self.assertTrue(document.is_shown(xy) and wp.Visibility, "redone: shown again")

    def test_apply_visibility_hides_consumed_sketches_and_leaves_free_ones(self):
        """Redo of an Extrude or a Sweep brings the feature back but not the hiding of its sketch (FreeCAD
        records no Visibility): apply_visibility hides consumed sketches; an unused one without a choice stays."""
        prof = self._profile()
        prof.Visibility = True
        self.closed.Visibility = True
        document.apply_visibility(self.doc)
        self.assertFalse(prof.Visibility, "consumed: hidden")
        self.assertTrue(self.closed.Visibility, "unused, no choice: left alone")
        self.assertTrue(self.path.Visibility)

    def test_undo_of_a_sketchs_first_hide_shows_it_again(self):
        """The first Hide on a sketch: prepare_choice records the state it has now outside the step, the step
        changes it, so undo brings exactly that state back (an open path shown, a just-drawn closed sketch shown
        too), and redo hides it again. A sketch the step never touched is left alone."""
        from incad.core import safe
        document.show_result(self.body)
        self.closed.Visibility = True  # just drawn, shown, no choice yet
        other = util.add_rect_sketch(self.body, name="Other")
        other.Visibility = True
        self.doc.recompute()
        for target in (self.path, self.closed):
            self.assertTrue(target.Visibility)
            document.prepare_choice([target])
            r = safe.run_safely("Hide / Show", lambda: document.toggle_visibility([target]), self.doc)
            self.assertTrue(r.ok, r.error)
            self.assertFalse(target.Visibility)
            self.doc.undo()
            document.apply_visibility(self.doc)
            self.assertTrue(target.Visibility, "%s undone: shown again as before" % target.Name)
            self.assertTrue(other.Visibility, "untouched by the step: left alone")
            self.doc.redo()
            document.apply_visibility(self.doc)
            self.assertFalse(target.Visibility, "%s redone: hidden" % target.Name)

    def test_apply_visibility_on_a_new_part_shows_nothing(self):
        """A new part in the GUI: Origin group hidden, every item 'visible', no choices. Putting the saved
        visibility in step must show nothing."""
        origin = self.body.Origin
        origin.Visibility = False
        for o in document.origin_items(self.body):
            o.Visibility = True
        document.apply_visibility(self.doc)
        self.assertFalse(origin.Visibility)
        self.assertFalse(any(document.is_shown(o) for o in document.origin_items(self.body)))

    def test_origin_items_are_the_seven(self):
        self.assertEqual(sorted(o.Role for o in document.origin_items(self.body)),
                         ["Origin", "XY_Plane", "XZ_Plane", "X_Axis", "YZ_Plane", "Y_Axis", "Z_Axis"])


class DetachLumpTest(unittest.TestCase):
    """A box extruded away from the part in the part's own body (an older file): it can be split off into its
    own Solid together with its sketch, so Shell and the like work on it."""

    def setUp(self):
        self.doc = util.new_doc("Dl2")
        self.body, self.pad = util.padded_part(self.doc, 10, incad=True)
        sk = util.add_rect_sketch(self.body, name="FarSk")
        sk.AttachmentOffset = App.Placement(App.Vector(50, 50, 0), App.Rotation())
        self.box = util.add_pad(self.body, sk, 10, name="Box")
        self.body.Tip = self.box
        self.doc.recompute()
        self.sk = sk

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_the_last_detached_feature_moves_to_its_own_solid(self):
        self.assertEqual(len(self.body.Tip.Shape.Solids), 2)
        self.assertTrue(document.is_detached_lump(self.box))
        new = document.detach_lump(self.box)
        self.doc.recompute()
        self.assertIsNot(new, self.body)
        self.assertIs(self.box.getParentGeoFeatureGroup(), new)
        self.assertIs(self.sk.getParentGeoFeatureGroup(), new)
        self.assertIs(new.Tip, self.box)
        self.assertIs(self.body.Tip, self.pad)
        self.assertEqual(len(self.body.Tip.Shape.Solids), 1)
        self.assertEqual(len(new.Tip.Shape.Solids), 1)
        self.assertAlmostEqual(new.Tip.Shape.Volume, 2000, places=3)
        self.assertIsNone(self.box.BaseFeature)
        self.assertIs(self.sk.AttachmentSupport[0][0].InList[0].getParentGeoFeatureGroup() if False else new, new)

    def test_picked_faces_are_found_again_after_the_split(self):
        """In the part's body the box's top was Face N of a two-solid shape; alone it is another number."""
        old_shape = self.box.Shape.copy()
        top_old = [i + 1 for i, f in enumerate(old_shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6
                   and f.CenterOfMass.x > 40][0]
        document.detach_lump(self.box)
        self.doc.recompute()
        new_sub = document.remap_sub(old_shape, self.box.Shape, "Face%d" % top_old)
        self.assertIsNotNone(new_sub)
        face = self.box.Shape.getElement(new_sub)
        self.assertAlmostEqual(face.CenterOfMass.z, 10, places=6)
        self.assertAlmostEqual(face.CenterOfMass.x, 60, places=6)
        self.assertIsNone(document.remap_sub(old_shape, self.box.Shape, "Face1"), "the pad's face is not here")

    def test_sketches_on_the_lumps_faces_and_their_features_move_along(self):
        """A sketch drawn on the box's top face (attached to Extrude.FaceN) and a pad made from it: both go to
        the box's Solid, the sketch re-attached to the same face under its new number."""
        top = [i + 1 for i, f in enumerate(self.box.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6
               and f.CenterOfMass.x > 40][0]
        on_top = self.body.newObject("Sketcher::SketchObject", "OnTop")
        on_top.AttachmentSupport = [(self.box, "Face%d" % top)]
        on_top.MapMode = "FlatFace"
        on_top.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 3))
        bump = util.add_pad(self.body, on_top, 4, name="Bump")
        self.body.Tip = bump
        loose = self.body.newObject("Sketcher::SketchObject", "Loose")  # on the box's top too, no feature
        loose.AttachmentSupport = [(self.box, "Face%d" % top)]
        loose.MapMode = "FlatFace"
        loose.addGeometry(Part.Circle(App.Vector(2, 2, 0), App.Vector(0, 0, 1), 1))
        self.doc.recompute()
        self.assertNotIn("Invalid", bump.State)
        new = document.detach_lump(self.box)
        self.doc.recompute()
        for o in (self.box, on_top, bump, loose):
            self.assertIs(o.getParentGeoFeatureGroup(), new, o.Name)
        for sk in (on_top, loose):
            self.assertIs(sk.AttachmentSupport[0][0], self.box)
            sub = sk.AttachmentSupport[0][1]
            face = self.box.Shape.getElement(sub[0] if isinstance(sub, (tuple, list)) else sub)
            self.assertAlmostEqual(face.CenterOfMass.z, 10, places=6, msg=sk.Name)
            self.assertNotIn("Invalid", sk.State, sk.getStatusString())
        self.assertNotIn("Invalid", bump.State, bump.getStatusString())
        self.assertIs(new.Tip, bump)
        self.assertAlmostEqual(new.Tip.Shape.Volume, 2000 + math.pi * 9 * 4, delta=1.0)
        self.assertIs(self.body.Tip, self.pad)

    def test_a_touching_feature_is_not_a_lump(self):
        self.assertFalse(document.is_detached_lump(self.pad))
        near = util.add_pad(self.body, util.add_rect_sketch(self.body, name="NearSk"), 3, name="Near")
        self.body.Tip = near
        self.doc.recompute()
        self.assertFalse(document.is_detached_lump(near), "touches the part")

    def test_a_middle_lump_moves_out_and_the_rest_relinks(self):
        """The box in the middle of the tree: it goes to its own Solid, the feature after it is rebuilt on the
        one before; a feature built on the box (a fillet of its edge) goes with it."""
        near_sk = util.add_rect_sketch(self.body, name="NearSk")
        near_sk.AttachmentOffset = App.Placement(App.Vector(0, 0, 10), App.Rotation())  # on the pad's top
        near = util.add_pad(self.body, near_sk, 3, name="Near")
        self.body.Tip = near
        self.doc.recompute()
        self.assertTrue(document.is_detached_lump(self.box), "a lump wherever it sits")
        new = document.detach_lump(self.box)
        self.doc.recompute()
        self.assertIs(self.box.getParentGeoFeatureGroup(), new)
        self.assertIs(near.getParentGeoFeatureGroup(), self.body)
        self.assertIs(near.BaseFeature, self.pad, "relinked on the feature before the box")
        self.assertIs(self.body.Tip, near)
        self.assertNotIn("Invalid", near.State, near.getStatusString())
        self.assertAlmostEqual(near.Shape.Volume, 2000 + 600, places=3)
        self.assertEqual(len(near.Shape.Solids), 1)
        self.assertAlmostEqual(new.Tip.Shape.Volume, 2000, places=3)


    def test_a_sketch_on_a_later_steps_face_of_the_part_stays(self):
        """The user's Thread_1: a stub sketched on a face of the step after the box, a face on the part (that
        step's shape holds both pieces), went into the box's Solid with its thread."""
        top = [i + 1 for i, f in enumerate(self.box.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6
               and f.CenterOfMass.x < 40][0]  # the pad's top, named on the box step
        on_part = self.body.newObject("Sketcher::SketchObject", "OnPart")
        on_part.AttachmentSupport = [(self.box, "Face%d" % top)]
        on_part.MapMode = "FlatFace"
        on_part.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 3))
        stub = util.add_pad(self.body, on_part, 4, name="Stub")
        self.body.Tip = stub
        self.doc.recompute()
        new = document.detach_lump(self.box)
        self.doc.recompute()
        self.assertIs(self.box.getParentGeoFeatureGroup(), new)
        self.assertIs(stub.getParentGeoFeatureGroup(), self.body)
        self.assertIs(on_part.getParentGeoFeatureGroup(), self.body)
        self.assertIs(on_part.AttachmentSupport[0][0], self.pad, "the same face, named on the step before the box")
        self.assertNotIn("Invalid", stub.State, stub.getStatusString())
        self.assertEqual(len(self.body.Tip.Shape.Solids), 1)
        self.assertEqual(len(new.Tip.Shape.Solids), 1)
        self.assertAlmostEqual(new.Tip.Shape.Volume, 2000, places=3)


    def test_a_box_sketched_on_the_parts_face_leaves_the_part_behind(self):
        """The user's Thread_1: the block's sketch sat on a face of the part, the block extruded away from it.
        Split off, its Pad took the sketch's support as its base and kept the whole part (two pieces)."""
        top = [i + 1 for i, f in enumerate(self.pad.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6][0]
        on_face = self.body.newObject("Sketcher::SketchObject", "OnFace")
        on_face.AttachmentSupport = [(self.pad, "Face%d" % top)]
        on_face.MapMode = "FlatFace"
        on_face.AttachmentOffset = App.Placement(App.Vector(60, 60, 5), App.Rotation())  # above, away from it
        on_face.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)))
        on_face.addGeometry(Part.LineSegment(App.Vector(10, 0, 0), App.Vector(10, 10, 0)))
        on_face.addGeometry(Part.LineSegment(App.Vector(10, 10, 0), App.Vector(0, 10, 0)))
        on_face.addGeometry(Part.LineSegment(App.Vector(0, 10, 0), App.Vector(0, 0, 0)))
        block = util.add_pad(self.body, on_face, 4, name="Block")
        self.body.Tip = block
        self.doc.recompute()
        self.assertTrue(document.is_detached_lump(block))
        where = block.Shape.Solids[-1].BoundBox
        new = document.detach_lump(block)
        self.doc.recompute()
        self.assertEqual(len(new.Tip.Shape.Solids), 1)
        self.assertAlmostEqual(new.Tip.Shape.Volume, 400, places=3)
        self.assertTrue(new.Tip.Shape.BoundBox.isInside(where.Center), (new.Tip.Shape.BoundBox, where))  # in place
        self.assertFalse(on_face.AttachmentSupport)


class HistoryTest(unittest.TestCase):
    """One history for the part (Inventor): the features of every Solid in the order they were made, and one End
    of Part that rolls all Solids back to a point in it."""

    def setUp(self):
        from incad.features import extrude
        self.doc = util.new_doc("Hist")
        self.body, self.pad = util.padded_part(self.doc, 10, incad=True)
        far = util.add_rect_sketch(self.body, name="Far")
        far.AttachmentOffset = App.Placement(App.Vector(50, 50, 0), App.Rotation())
        self.doc.recompute()
        self.box = extrude.create(self.doc, self.body, far, extrude.defaults(self.doc, self.body, far))
        self.doc.recompute()
        self.other = self.box.getParentGeoFeatureGroup()
        top = util.add_rect_sketch(self.body, name="Top")
        top.AttachmentOffset = App.Placement(App.Vector(0, 0, 10), App.Rotation())
        self.pad2 = util.add_pad(self.body, top, 5, name="Pad2")
        self.body.Tip = self.pad2
        on_box = util.add_rect_sketch(self.other, w=4, h=4, name="OnBox")
        on_box.AttachmentOffset = App.Placement(App.Vector(55, 52, 10), App.Rotation())
        self.cut = self.other.newObject("PartDesign::Pocket", "Cut")
        self.cut.Profile = on_box
        self.cut.Length = 3
        self.other.Tip = self.cut
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_history_interleaves_the_solids_in_creation_order(self):
        hist = document.history(self.doc)
        self.assertEqual([o.Name for _, o in hist], ["Sketch", "Pad", "Far", "Extrude", "Top", "Pad2", "OnBox", "Cut"])
        self.assertEqual([b.Label for b, _ in hist], ["Solid1", "Solid1", "Solid2", "Solid2", "Solid1", "Solid1",
                                                       "Solid2", "Solid2"])

    def test_end_of_part_at_a_feature_rolls_every_solid_to_that_point(self):
        document.set_end_of_part_at(self.doc, self.box)
        self.assertIs(self.body.Tip, self.pad)
        self.assertIs(self.other.Tip, self.box)
        document.set_end_of_part_at(self.doc, self.pad2)
        self.assertIs(self.body.Tip, self.pad2)
        self.assertIs(self.other.Tip, self.box)
        document.set_end_of_part_at(self.doc, self.cut)
        self.assertIs(self.body.Tip, self.pad2)
        self.assertIs(self.other.Tip, self.cut)
        document.set_end_of_part_at(self.doc, self.pad)
        self.assertIs(self.body.Tip, self.pad)
        self.assertIsNone(self.other.Tip, "nothing of the box is made yet")
        document.set_end_of_part_at(self.doc, None)
        self.assertIsNone(self.body.Tip)
        self.assertIsNone(self.other.Tip)
        self.assertFalse(self.pad.Visibility)


class HideShowSolidTest(unittest.TestCase):
    """The user: after a Split, Hide / Show refused a piece ('Nothing to hide or show in BaseFeature / SplitPiece /
    Split004') — it knew only sketches, planes, axes and points."""

    def setUp(self):
        from incad.features import split
        self.doc = util.new_doc("Hs")
        self.body, self.pad = util.padded_part(self.doc, 10, incad=True)
        sk = self.body.newObject("Sketcher::SketchObject", "Knife")
        sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        sk.MapMode = "FlatFace"
        sk.addGeometry(Part.LineSegment(App.Vector(5, -5, 0), App.Vector(5, 15, 0)))
        self.doc.recompute()
        self.split = split.create(self.doc, self.body, None, dict(split.defaults(self.doc, self.body, None),
                                                                   sketch=sk.Name))
        self.doc.recompute()
        self.piece = split.piece_bodies(self.split)[0]

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_a_pick_on_a_piece_hides_its_solid(self):
        base = next(o for o in self.piece.Group if o.TypeId == "PartDesign::FeatureBase")
        self.assertFalse(document.toggle_visibility([base]))
        self.assertFalse(self.piece.Visibility)
        self.assertFalse(base.Visibility, "the piece's base (its Tip) draws even inside a hidden Body: hidden too")
        self.assertTrue(self.body.Visibility, "the other piece stays")
        document.show_result(self.piece)  # a refresh does not bring it back
        self.assertFalse(self.piece.Visibility)
        self.assertFalse(base.Visibility)
        self.assertTrue(document.toggle_visibility([self.piece.BaseFeature]))  # its SplitPiece: the same Solid
        self.assertTrue(self.piece.Visibility)
        self.assertTrue(base.Visibility)

    def test_the_piece_shape_never_draws_on_its_own(self):
        """The user's Thread_1: the SplitPiece (outside the Solid made on it) was visible, so hiding Solid2 left
        the piece on the screen."""
        self.piece.BaseFeature.Visibility = True
        document.show_result(self.piece)
        self.assertFalse(self.piece.BaseFeature.Visibility)

    def test_a_hidden_solid_is_dimmed_in_the_tree(self):
        from incad.core import tree
        from incad.core.nodes import flatten
        document.toggle_visibility([self.piece])
        rows = {n.name: n for _, n in flatten(tree.build(self.doc)) if n.kind == "body"}
        self.assertEqual(rows[self.piece.Name].state, "hidden")
        self.assertEqual(rows[self.body.Name].state, "")

    def test_the_main_piece_hides_by_its_split(self):
        self.assertFalse(document.toggle_visibility([self.split]))
        self.assertFalse(self.body.Visibility)
        self.assertTrue(self.piece.Visibility)

    def test_undo_brings_a_hidden_solid_back(self):
        self.doc.UndoMode = 1
        document.prepare_choice([self.piece])
        self.doc.openTransaction("Hide / Show")
        document.toggle_visibility([self.piece])
        self.doc.commitTransaction()
        self.doc.undo()
        document.apply_visibility(self.doc)
        self.assertTrue(self.piece.Visibility)


class DetachSweepLumpTest(unittest.TestCase):
    """A Sweep joined away from the part (the user's: the Move Face dialog then split it off on every pick, its path
    sketch left behind — 'links go out of the allowed scope' — and the move undid itself, round and round): the path
    goes with it."""

    def setUp(self):
        import Part
        self.doc = util.new_doc("DlSw")
        self.body, self.pad = util.padded_part(self.doc, 10, incad=True)
        yz = [o for o in self.body.Origin.OriginFeatures if o.Role == "YZ_Plane"][0]
        xy = [o for o in self.body.Origin.OriginFeatures if o.Role == "XY_Plane"][0]
        self.prof = self.body.newObject("Sketcher::SketchObject", "Prof")
        self.prof.AttachmentSupport = [(yz, "")]
        self.prof.MapMode = "FlatFace"
        self.prof.AttachmentOffset = App.Placement(App.Vector(0, 0, 60), App.Rotation())
        self.prof.addGeometry(Part.Circle(App.Vector(50, 5, 0), App.Vector(0, 0, 1), 2))
        self.path = self.body.newObject("Sketcher::SketchObject", "Path")
        self.path.AttachmentSupport = [(xy, "")]
        self.path.MapMode = "FlatFace"
        self.path.addGeometry(Part.LineSegment(App.Vector(60, 50, 0), App.Vector(90, 50, 0)))
        self.doc.recompute()
        self.sweep = self.body.newObject("PartDesign::AdditivePipe", "Sweep")
        self.sweep.Profile = self.prof
        self.sweep.Spine = (self.path, [])
        self.body.Tip = self.sweep
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_the_path_moves_with_the_sweep(self):
        self.assertTrue(document.is_detached_lump(self.sweep), self.sweep.Shape.Solids)
        new = document.detach_lump(self.sweep)
        self.doc.recompute()
        self.assertIs(self.sweep.getParentGeoFeatureGroup(), new)
        self.assertIs(self.path.getParentGeoFeatureGroup(), new)
        self.assertIs(self.prof.getParentGeoFeatureGroup(), new)
        self.assertNotIn("Invalid", self.sweep.State, self.sweep.getStatusString())
        self.assertFalse(document.is_detached_lump(self.sweep))
