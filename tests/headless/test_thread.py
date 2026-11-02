import math
import unittest

import FreeCAD as App
import Part
from incad.core import document, model
from incad.features import common, kind_for, thread
from incad.threads import profile, standards as st
from headless import util


def shaft(doc, body, radius=5.0, length=20.0, name="Shaft"):
    sk = body.newObject("Sketcher::SketchObject", name + "Sk")
    sk.AttachmentSupport = [(util.xy_plane(body), "")]
    sk.MapMode = "FlatFace"
    sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), radius))
    pad = util.add_pad(body, sk, length, name=name)
    body.Tip = pad
    doc.recompute()
    return pad


def cyl_face(feature, radius):
    return "%s:Face%d" % (feature.Name, [i + 1 for i, f in enumerate(feature.Shape.Faces)
                                         if f.Surface.TypeId == "Part::GeomCylinder"
                                         and abs(f.Surface.Radius - radius) < 1e-6][0])


def estimate(spec, internal, clearance, length):
    """Groove area inside the material × mean circumference × turns."""
    g = profile.groove(spec, internal, clearance)
    R = spec.major / 2.0
    inside = [(r, z) for r, z in g]
    lo, hi = (min(r for r, _ in g), R) if not internal else (R - spec.depth_int, max(r for r, _ in g))
    n = 40
    area = sum(profile.width_at(inside, lo + (hi - lo) * (i + 0.5) / n) * (hi - lo) / n for i in range(n))
    rc = (lo + hi) / 2.0
    return area * 2 * math.pi * rc * length / spec.pitch


class ClearanceFollowsTheStandardTest(unittest.TestCase):
    """The user: print-friendly threads get 0.3 mm print clearance (0.2 mm for the others), set when the standard is
    chosen in the dialog; a clearance the user typed is never overwritten."""

    def test_choosing_print_friendly_sets_0_3(self):
        self.assertEqual(thread.follow({"standard": "iso", "clearance": "0.2 mm"},
                                       {"standard": "print", "clearance": "0.2 mm"}), {"clearance": "0.3 mm"})

    def test_leaving_print_friendly_puts_the_usual_default_back(self):
        self.assertEqual(thread.follow({"standard": "print", "clearance": "0.3 mm"},
                                       {"standard": "unc", "clearance": "0.3 mm"}), {"clearance": "0.2 mm"})

    def test_a_typed_clearance_is_kept(self):
        self.assertEqual(thread.follow({"standard": "iso", "clearance": "0.25 mm"},
                                       {"standard": "print", "clearance": "0.25 mm"}), {})
        self.assertEqual(thread.follow({"standard": "iso", "clearance": "d3 / 2"},
                                       {"standard": "print", "clearance": "d3 / 2"}), {})

    def test_nothing_changes_without_a_new_standard(self):
        self.assertEqual(thread.follow({"standard": "print", "clearance": "0.3 mm"},
                                       {"standard": "print", "clearance": "0.2 mm"}), {})

    def test_a_new_print_friendly_thread_starts_at_0_3(self):
        doc = util.new_doc("ThP")
        try:
            body = document.make_part(doc)
            self.assertEqual(thread.default_clearance("print"), 0.3)
            self.assertEqual(thread.default_clearance("iso"), 0.2)
            self.assertEqual(thread.defaults(doc, body, None)["clearance"], "0.2 mm")  # ISO metric first
        finally:
            App.closeDocument(doc.Name)


class ThreadTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Th")
        self.body = document.make_part(self.doc)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def make(self, **values):
        v = dict(thread.defaults(self.doc, self.body, None), **values)
        f = thread.create(self.doc, self.body, None, v)
        self.doc.recompute()
        return f

    def test_external_m10(self):
        pad = shaft(self.doc, self.body)
        f = self.make(face=cyl_face(pad, 5), standard="iso", size="M10", clearance="0 mm")
        self.assertEqual(f.State, ["Up-to-date"], f.getStatusString())
        self.assertTrue(f.Shape.isValid())
        removed = pad.Shape.Volume - f.Shape.Volume
        want = estimate(st.spec("iso", "M10"), False, 0.0, 20.0)
        self.assertAlmostEqual(removed / want, 1.0, delta=0.15)
        self.assertIs(self.body.Tip, f)
        self.assertIs(kind_for(f), thread)

    def test_full_length_and_an_auto_size_follow_the_shaft(self):
        """Full length stopped at the old end of a longer shaft; an M10 chosen for 10 mm stayed on a 12 mm shaft."""
        pad = shaft(self.doc, self.body)
        f = self.make(face=cyl_face(pad, 5), standard="iso", size="", clearance="0 mm")
        self.assertEqual(thread._load(f)["size"], "M10")
        pad.Length = 30
        self.doc.recompute()
        self.assertEqual(thread.follow_part(self.doc), [f.Label])
        self.doc.recompute()
        self.assertGreater(f.Shape.BoundBox.ZMax, 29.9)  # the groove reaches the new end
        sk = pad.Profile[0] if isinstance(pad.Profile, (tuple, list)) else pad.Profile
        sk.delGeometries([0])
        sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 6))
        self.doc.recompute()
        thread.follow_part(self.doc)
        self.doc.recompute()
        self.assertEqual(thread._load(f)["size"], "M12")
        self.assertEqual(thread.follow_part(self.doc), [], "in step now")

    def test_size_is_suggested_from_the_face(self):
        pad = shaft(self.doc, self.body, radius=4.0)
        f = self.make(face=cyl_face(pad, 4), standard="iso", size="")
        self.assertEqual(thread.read(self.doc, f)["size"], "M8")

    def test_internal_m10_in_a_bore(self):
        block = shaft(self.doc, self.body, radius=10.0, name="Block")
        s = st.spec("iso", "M10")
        bore_r = st.bore_diameter(s, 0.2) / 2
        sk = self.body.newObject("Sketcher::SketchObject", "BoreSk")
        sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        sk.MapMode = "FlatFace"
        sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), bore_r))
        bore = self.body.newObject("PartDesign::Pocket", "Bore")
        bore.Profile = sk
        bore.Type = "ThroughAll"
        bore.Reversed = True
        self.body.Tip = bore
        self.doc.recompute()
        self.assertLess(bore.Shape.Volume, block.Shape.Volume)
        f = self.make(face=cyl_face(bore, bore_r), standard="iso", size="M10", clearance="0.2 mm")
        self.assertEqual(f.State, ["Up-to-date"], f.getStatusString())
        self.assertTrue(thread.read(self.doc, f)["internal"])
        removed = bore.Shape.Volume - f.Shape.Volume
        self.assertGreater(removed, 0.5 * estimate(s, True, 0.2, 20.0))

    def test_left_hand_and_taper(self):
        pad = shaft(self.doc, self.body, radius=6.6)
        f = self.make(face=cyl_face(pad, 6.6), standard="npt", size="1/4", hand="left")
        self.assertTrue(f.LeftHanded)
        self.assertAlmostEqual(abs(f.Angle.Value), st.spec("npt", "1/4").taper, places=4)
        self.assertNotIn("Invalid", f.State)

    def test_length_parameter_drives_the_helix(self):
        pad = shaft(self.doc, self.body)
        f = self.make(face=cyl_face(pad, 5), size="M10", length_mode="distance", length="8 mm")
        name = model.bound_parameter(f, "Height")
        self.assertIsNotNone(name)
        model.set_model_parameter(self.doc, name, "12 mm")
        self.assertAlmostEqual(f.Height.Value, 12, places=6)

    def test_edit_size_keeps_the_feature(self):
        pad = shaft(self.doc, self.body)
        f = self.make(face=cyl_face(pad, 5), size="M10")
        v1 = f.Shape.Volume
        g = thread.update(self.doc, f, dict(thread.read(self.doc, f), standard="iso_fine", size="M10x1.25"))
        self.doc.recompute()
        self.assertIs(g, f)
        self.assertAlmostEqual(f.Pitch.Value, 1.25, places=6)
        self.assertNotAlmostEqual(f.Shape.Volume, v1, places=1)
        self.assertEqual(thread.read(self.doc, f)["size"], "M10x1.25")

    def test_flat_face_is_refused(self):
        pad = shaft(self.doc, self.body)
        top = [i + 1 for i, fc in enumerate(pad.Shape.Faces) if fc.Surface.TypeId == "Part::GeomPlane"][0]
        with self.assertRaises(ValueError) as ctx:
            self.make(face="%s:Face%d" % (pad.Name, top), size="M10")
        self.assertIn("cylindrical", str(ctx.exception))

    def test_big_clearance_on_a_fine_pitch_is_still_valid(self):
        pad = shaft(self.doc, self.body, radius=1.5, length=6)
        f = self.make(face=cyl_face(pad, 1.5), size="M3", clearance="0.6 mm")
        self.assertEqual(f.State, ["Up-to-date"], f.getStatusString())
        self.assertTrue(f.Shape.isValid())

    def test_cylinder_pick_kind(self):
        pad = shaft(self.doc, self.body)
        obj, sub = common.parse_picks(self.doc, cyl_face(pad, 5))[0]
        self.assertTrue(common.accepts("cylinder", obj, sub))
        self.assertFalse(common.accepts("cylinder", obj, "Face1" if sub != "Face1" else "Face2"))


class SeveralFacesTest(unittest.TestCase):
    """The user: threads in several holes at once, as Hole does — a thread on each face picked, one dialog."""

    def setUp(self):
        self.doc = util.new_doc("Tm")
        self.body = document.make_part(self.doc)
        self.pad = shaft(self.doc, self.body)
        sk = self.body.newObject("Sketcher::SketchObject", "TwoSk")
        sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        sk.MapMode = "FlatFace"
        for x in (20, 40):
            sk.addGeometry(Part.Circle(App.Vector(x, 0, 0), App.Vector(0, 0, 1), 5))
        self.more = util.add_pad(self.body, sk, 20, name="Shafts")
        self.body.Tip = self.more
        self.doc.recompute()
        self.faces = ["%s:Face%d" % (self.more.Name, i + 1) for i, f in enumerate(self.more.Shape.Faces)
                      if f.Surface.TypeId == "Part::GeomCylinder" and abs(f.Surface.Radius - 5) < 1e-6]
        self.assertEqual(len(self.faces), 3)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def threads(self):
        return [o for o in self.doc.Objects if thread.claims(o) and not getattr(o, thread.HELPER, None)]

    def test_a_thread_on_each_face_then_edited_alone(self):
        v = dict(thread.defaults(self.doc, self.body, None), face=",".join(self.faces), size="M10")
        self.doc.UndoMode = 1
        self.doc.openTransaction("Thread")  # the dialog's step
        f = thread.create(self.doc, self.body, None, v)
        self.doc.recompute()
        self.assertEqual(len(self.threads()), 3)
        self.assertEqual([c.Name for c in thread.companions(self.doc, f)], [t.Name for t in self.threads()][1:])
        f = thread.update(self.doc, f, dict(v, face=",".join(self.faces[:2])))  # one face taken off: its thread goes
        self.doc.recompute()
        self.assertEqual(len(self.threads()), 2)
        self.assertTrue(all("Invalid" not in t.State for t in self.threads()))
        r = thread.read(self.doc, f)
        self.assertEqual(r["face"], self.faces[0])
        self.assertEqual(len(thread.companions(self.doc, f)), 1, "read (after every preview) keeps them")
        self.doc.commitTransaction()  # OK
        self.assertEqual(thread.companions(self.doc, f), [], "an existing thread is edited alone")


class SeveralHolesTest(unittest.TestCase):
    """The user: several threads worked on shafts, not in holes — the second hole was clicked on the first thread's
    preview (its fill, made again at every preview), and that name died: the faces are named on the part."""

    def test_the_second_hole_clicked_on_the_first_threads_preview(self):
        from incad.features import hole
        doc = util.new_doc("Th")
        try:
            body, pad = util.padded_part(doc, 10, incad=True)
            top = [i + 1 for i, f in enumerate(pad.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6][0]
            sk = body.newObject("Sketcher::SketchObject", "Holes")
            sk.AttachmentSupport = [(pad, "Face%d" % top)]
            sk.MapMode = "FlatFace"
            for x in (5, 15):
                sk.addGeometry(Part.Circle(App.Vector(x, 5, 0), App.Vector(0, 0, 1), 2.5))
            doc.recompute()
            h = hole.create(doc, body, None, dict(hole.defaults(doc, body, None), sketch=sk.Name, diameter="5 mm",
                                                  depth_type="through_all"))
            doc.recompute()

            def bore(obj, x):
                return "%s:Face%d" % (obj.Name, [i + 1 for i, f in enumerate(obj.Shape.Faces)
                                                 if f.Surface.TypeId == "Part::GeomCylinder"
                                                 and abs(f.Surface.Center.x - x) < 1e-6][0])
            v = dict(thread.defaults(doc, body, None), face=bore(h, 5))
            doc.UndoMode = 1
            doc.openTransaction("Thread")  # the dialog's step: the threads made in it belong together
            f = thread.create(doc, body, None, v)
            doc.recompute()
            on_preview = bore(body.Tip, 15)  # what a click on the second hole names now
            self.assertFalse(on_preview.startswith(h.Name + ":"), on_preview)
            f = thread.update(doc, f, dict(v, face=v["face"] + "," + on_preview))
            doc.recompute()
            shown = thread.adjusted(doc, f)["face"].split(",")
            self.assertEqual([x.split(":")[0] for x in shown], [h.Name, h.Name], shown)
            f = thread.update(doc, f, dict(v, face=",".join(shown)))  # the next preview, with what the dialog shows
            doc.recompute()
            made = [o for o in doc.Objects if thread.claims(o) and not getattr(o, thread.HELPER, None)]
            self.assertEqual(len(made), 2)
            self.assertTrue(all("Invalid" not in t.State for t in made), [(t.Name, t.getStatusString()) for t in made])
        finally:
            App.closeDocument(doc.Name)


class ThreadReviewTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Tr")
        self.body = document.make_part(self.doc)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def make(self, **values):
        v = dict(thread.defaults(self.doc, self.body, None), **values)
        f = thread.create(self.doc, self.body, None, v)
        self.doc.recompute()
        return f

    def add_cyl(self, radius, z0, length, name):
        sk = self.body.newObject("Sketcher::SketchObject", name + "Sk")
        sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        sk.MapMode = "FlatFace"
        sk.AttachmentOffset = App.Placement(App.Vector(0, 0, z0), App.Rotation())
        sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), radius))
        pad = util.add_pad(self.body, sk, length, name=name)
        self.body.Tip = pad
        self.doc.recompute()
        return pad

    def test_starts_at_the_free_end_of_a_shaft_on_a_shoulder(self):
        self.add_cyl(8, -5, 5, "Shoulder")           # z -5..0, Ø16
        shaft_ = self.add_cyl(5, 0, 20, "Shaft")     # z 0..20, Ø10
        before = shaft_.Shape.Volume
        f = self.make(face=cyl_face(shaft_, 5), size="M10", length_mode="distance", length="6 mm", clearance="0 mm")
        cut = shaft_.Shape.cut(f.Shape)
        self.assertGreater(cut.BoundBox.ZMin, 12, cut.BoundBox)  # the 6 mm thread is at the free (top) end
        self.assertLess(before - f.Shape.Volume, before)

    def test_full_thread_does_not_cut_the_shoulder(self):
        shoulder = self.add_cyl(8, -5, 5, "Shoulder")
        shaft_ = self.add_cyl(5, 0, 20, "Shaft")
        f = self.make(face=cyl_face(shaft_, 5), size="M10", clearance="0 mm")
        below = Part.makeCylinder(8, 5, App.Vector(0, 0, -5))
        self.assertAlmostEqual(below.common(f.Shape).Volume, below.common(shaft_.Shape).Volume, places=3)

    def test_npt_external_is_small_at_the_free_end(self):
        self.add_cyl(9, -5, 5, "Shoulder")
        shaft_ = self.add_cyl(6.6, 0, 15, "Pipe")
        f = self.make(face=cyl_face(shaft_, 6.6), standard="npt", size="1/4", clearance="0 mm")
        def root(z):
            sl = f.Shape.slice(App.Vector(0, 0, 1), z)
            return min(min(abs(v.Point.x) for v in w.Vertexes) for w in sl if w.Vertexes) if sl else None
        lows = [min(e.distToShape(Part.Vertex(App.Vector(0, 0, z)))[0] for e in f.Shape.slice(App.Vector(0, 0, 1), z))
                for z in (2.0, 13.0)]
        self.assertGreater(lows[0], lows[1])  # thread root larger near the shoulder, smaller at the free end

    def test_mismatched_size_is_refused_or_cuts_through(self):
        shaft_ = self.add_cyl(5, 0, 10, "Shaft")
        with self.assertRaises(ValueError) as ctx:
            self.make(face=cyl_face(shaft_, 5), size="M20")
        self.assertIn("size", str(ctx.exception).lower())
        with self.assertRaises(ValueError) as ctx:
            self.make(face=cyl_face(shaft_, 5), size="M6")  # would only make a buried helical tunnel
        self.assertIn("size", str(ctx.exception).lower())
        wide = self.add_cyl(5.2, 10, 10, "Wide")  # Ø10.4 with an M10: the groove still opens at the surface
        f = self.make(face=cyl_face(wide, 5.2), size="M10", clearance="0 mm")
        ring = Part.makeCylinder(5.2, 10, App.Vector(0, 0, 10)).cut(Part.makeCylinder(4.9, 10, App.Vector(0, 0, 10)))
        self.assertLess(ring.common(f.Shape).Volume, ring.Volume * 0.7)

    def test_a_slightly_oversize_shaft_still_gets_a_real_thread(self):
        """Ø4.5 with an M4 (auto size picks the nearest): the profile used to be built for Ø4.0, so the groove left
        only ~0.04 mm hairs above it — no thread at all. The thread now follows the shaft's real diameter."""
        shaft_ = self.add_cyl(2.25, 0, 10, "Small")
        f = self.make(face=cyl_face(shaft_, 2.25), size="M4", clearance="0 mm")
        self.assertEqual(f.State, ["Up-to-date"], f.getStatusString())
        ring = Part.makeCylinder(2.25, 6, App.Vector(0, 0, 2)).cut(Part.makeCylinder(2.1, 6, App.Vector(0, 0, 2)))
        kept = ring.common(f.Shape).Volume / ring.Volume
        self.assertGreater(kept, 0.15, "the ridges at the surface are %.0f%% of the ring: hairs" % (100 * kept))
        self.assertLess(kept, 0.6)  # and the groove is really cut

    def test_partial_cylinder_is_refused(self):
        pad = self.add_cyl(5, 0, 10, "Shaft")
        from incad.features import dressup
        edges = [(pad, "Edge%d" % (i + 1)) for i, e in enumerate(pad.Shape.Edges)
                 if e.Curve.TypeId == "Part::GeomCircle" and e.BoundBox.ZMax > 9]
        fil = dressup.FILLET.create(self.doc, self.body, None, {"edges": common.format_picks(edges),
                                                                "radius": "2 mm"})
        self.doc.recompute()
        torus_or_cyl = [i + 1 for i, fc in enumerate(fil.Shape.Faces) if fc.Surface.TypeId == "Part::GeomCylinder"]
        sub = "Face%d" % torus_or_cyl[0]
        self.assertTrue(common.accepts("cylinder", fil, sub))  # the full shaft face is still fine
        box = self.doc.addObject("Part::Box", "Box")
        self.doc.recompute()
        cube = Part.makeBox(10, 10, 10)
        f2 = cube.makeFillet(2, [cube.Edges[0]])
        part = self.doc.addObject("Part::Feature", "Rounded")
        part.Shape = f2
        quarter = [i + 1 for i, fc in enumerate(f2.Faces) if fc.Surface.TypeId == "Part::GeomCylinder"][0]
        self.assertFalse(common.accepts("cylinder", part, "Face%d" % quarter))

    def test_clearance_limits(self):
        shaft_ = self.add_cyl(1.5, 0, 6, "Shaft")
        for bad in ("-0.1 mm", "3 mm"):
            with self.assertRaises(ValueError) as ctx:
                self.make(face=cyl_face(shaft_, 1.5), size="M3", clearance=bad)
            self.assertIn("clearance", str(ctx.exception).lower())

    def test_print_friendly_matches_iso_pitch_diameter(self):
        for size in ("M6", "M10", "M24"):
            iso, pr = st.spec("iso", size), st.spec("print", size)
            d2 = iso.major - 0.6495 * iso.pitch
            for spec in (iso, pr):
                g = profile.groove(spec, internal=False, clearance=0.0)
                self.assertAlmostEqual(profile.width_at(g, d2 / 2), spec.pitch / 2, places=2, msg=(size, spec.std))
                gi = profile.groove(spec, internal=True, clearance=0.0)
                self.assertAlmostEqual(profile.width_at(gi, d2 / 2), spec.pitch / 2, places=2, msg=(size, spec.std))


class ThreadRunOutTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Ro")
        self.body = document.make_part(self.doc)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_full_thread_runs_out_past_both_free_ends(self):
        pad = shaft(self.doc, self.body)  # Ø10 × 20, both ends free
        f = thread.create(self.doc, self.body, None, dict(thread.defaults(self.doc, self.body, None),
                                                          face=cyl_face(pad, 5), size="M10", clearance="0 mm"))
        self.doc.recompute()

        def ring_cut(z0):  # material removed in a 1.5 mm (one pitch) slab
            slab = Part.makeCylinder(5, 1.5, App.Vector(0, 0, z0))
            return slab.Volume - slab.common(f.Shape).Volume
        middle = ring_cut(9.25)
        self.assertAlmostEqual(ring_cut(0.0) / middle, 1.0, delta=0.1)   # full thread right to the end
        self.assertAlmostEqual(ring_cut(18.5) / middle, 1.0, delta=0.1)


class ThreadStartFaceTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Sf")
        self.body = document.make_part(self.doc)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_start_face_picks_the_end_and_offset_counts_from_it(self):
        pad = shaft(self.doc, self.body)  # Ø10 × 20
        bottom = [i + 1 for i, fc in enumerate(pad.Shape.Faces)
                  if fc.Surface.TypeId == "Part::GeomPlane" and abs(fc.CenterOfMass.z) < 1e-6][0]
        f = thread.create(self.doc, self.body, None, dict(
            thread.defaults(self.doc, self.body, None), face=cyl_face(pad, 5), size="M10", clearance="0 mm",
            start_face="%s:Face%d" % (pad.Name, bottom), offset="2 mm", length_mode="distance", length="6 mm"))
        self.doc.recompute()
        cut = pad.Shape.cut(f.Shape)
        self.assertGreater(cut.BoundBox.ZMin, 1.0, cut.BoundBox)   # starts 2 mm above the bottom face
        self.assertLess(cut.BoundBox.ZMax, 9.5, cut.BoundBox)      # and runs ~6 mm up
        self.assertEqual(thread.read(self.doc, f)["start_face"], "%s:Face%d" % (pad.Name, bottom))

    def test_a_hole_running_into_a_curved_wall_is_threaded_from_its_round_end(self):
        """The user's clamp: a hole drilled from the end face breaks out into the curved wall of a saddle, so its
        far end is no circle ("The face needs two round ends to thread"). It is threaded from its round mouth to
        the far end of its wall."""
        box = self.body.newObject("PartDesign::AdditiveBox", "Block")
        box.Length, box.Width, box.Height = 30.0, 20.0, 10.0
        box.Placement = App.Placement(App.Vector(-15, -10, 0), App.Rotation())
        saddle = self.body.newObject("PartDesign::SubtractiveCylinder", "Saddle")
        saddle.Radius, saddle.Height = 8.0, 10.0
        hole = self.body.newObject("PartDesign::SubtractiveCylinder", "Hole")
        hole.Radius, hole.Height = 2.5, 15.0  # ends inside the saddle: its far end runs into the curved wall
        hole.Placement = App.Placement(App.Vector(15, 5, 5), App.Rotation(App.Vector(0, 1, 0), -90))
        self.body.Tip = hole
        self.doc.recompute()
        face = cyl_face(hole, 2.5)
        edges = hole.Shape.getElement(face.split(":")[1]).Edges
        self.assertEqual(len([e for e in edges if e.Curve.TypeId == "Part::GeomCircle"]), 1)
        mouth = [i + 1 for i, fc in enumerate(hole.Shape.Faces)
                 if fc.Surface.TypeId == "Part::GeomPlane" and abs(fc.CenterOfMass.x - 15) < 1e-6][0]
        for start in ("", "%s:Face%d" % (hole.Name, mouth)):
            f = thread.create(self.doc, self.body, None, dict(thread.defaults(self.doc, self.body, None), face=face,
                                                               start_face=start, size="M6"))
            self.doc.recompute()
            self.assertEqual(f.State, ["Up-to-date"], f.getStatusString())
            self.assertTrue(self.body.Shape.isValid())
            cut = hole.Shape.cut(self.body.Shape)
            self.assertGreater(cut.Volume, 1.0)
            self.assertGreater(cut.BoundBox.XMax, 14.0, cut.BoundBox)  # from the mouth
            self.assertLess(cut.BoundBox.XMin, 9.0, cut.BoundBox)      # to where the wall ends
            thread._discard(self.doc, self.body, f)
            self.doc.recompute()
        with self.assertRaises(ValueError):  # the far end has no edge to start from
            thread.create(self.doc, self.body, None, dict(thread.defaults(self.doc, self.body, None), face=face,
                                                           flip=True, size="M6"))

    def test_start_face_must_touch_the_threaded_face(self):
        pad = shaft(self.doc, self.body)
        other = self.body.newObject("PartDesign::Plane", "Far")
        other.AttachmentSupport = [(util.xy_plane(self.body), "")]
        other.MapMode = "FlatFace"
        other.AttachmentOffset = App.Placement(App.Vector(0, 0, 50), App.Rotation())
        self.doc.recompute()
        with self.assertRaises(ValueError) as ctx:
            thread.create(self.doc, self.body, None, dict(thread.defaults(self.doc, self.body, None),
                                                          face=cyl_face(pad, 5), size="M10", start_face=other.Name))
        self.assertIn("start", str(ctx.exception).lower())


class ThreadChamferTest(unittest.TestCase):
    def setUp(self):
        self.doc = util.new_doc("Tc")
        self.body = document.make_part(self.doc)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def make(self, **values):
        v = dict(thread.defaults(self.doc, self.body, None), **values)
        f = thread.create(self.doc, self.body, None, v)
        self.doc.recompute()
        return f

    def test_start_chamfer_cuts_the_corner_and_sits_under_the_thread(self):
        from incad.core import delete, tree
        pad = shaft(self.doc, self.body)
        plain = self.make(face=cyl_face(pad, 5.0), standard="iso", size="M10")
        v_plain = self.body.Shape.Volume
        delete.delete(plain)
        self.doc.recompute()
        f = self.make(face=cyl_face(pad, 5.0), standard="iso", size="M10", chamfer="start", chamfer_size="1 mm")
        helpers = thread.helpers_of(f)
        self.assertEqual([h.TypeId for h in helpers], ["PartDesign::Groove"])
        self.assertIs(self.body.Tip, helpers[0])
        self.assertTrue(self.body.Shape.isValid())
        ring = math.pi * 1.0 ** 2 * (5.0 - 1.0 / 3)  # the whole 45° ring; the thread already took part of it
        self.assertGreater(v_plain - self.body.Shape.Volume, 0.2 * ring)
        self.assertLess(v_plain - self.body.Shape.Volume, ring + 1e-6)
        r = thread.read(self.doc, f)
        self.assertEqual((r["chamfer"], r["chamfer_size"]), ("start", "1 mm"))
        names = [n.label for n in tree.build(self.doc)[0].children]  # top level of the part
        self.assertNotIn("ThreadChamfer", names)
        thread_node = [n for n in tree.build(self.doc)[0].children if n.name == f.Name][0]
        self.assertIn("ThreadChamfer", [c.label for c in thread_node.children])

    def test_both_ends_on_a_through_bore_and_removal(self):
        from incad.core import delete
        block = shaft(self.doc, self.body, radius=10.0, name="Block")
        s = st.spec("iso", "M10")
        bore_r = st.bore_diameter(s, 0.2) / 2
        sk = self.body.newObject("Sketcher::SketchObject", "BoreSk")
        sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        sk.MapMode = "FlatFace"
        sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), bore_r))
        bore = self.body.newObject("PartDesign::Pocket", "Bore")
        bore.Profile = sk
        bore.Type = "ThroughAll"
        bore.Reversed = True
        self.body.Tip = bore
        self.doc.recompute()
        f = self.make(face=cyl_face(bore, bore_r), standard="iso", size="M10", chamfer="both", chamfer_size="0.8 mm")
        g = thread.helpers_of(f)[0]
        self.assertEqual(len(g.Profile[0].Geometry), 6)  # two triangles
        self.assertTrue(self.body.Shape.isValid())
        v = self.body.Shape.Volume
        thread.update(self.doc, f, dict(thread.read(self.doc, f), chamfer="none"))
        self.doc.recompute()
        self.assertEqual(thread.helpers_of(f), [])
        self.assertGreater(self.body.Shape.Volume, v)
        self.assertIs(self.body.Tip, f)
        thread.update(self.doc, f, dict(thread.read(self.doc, f), chamfer="start"))
        self.doc.recompute()
        self.assertEqual(len(thread.helpers_of(f)), 1)
        delete.delete(f)
        self.doc.recompute()
        self.assertFalse([o for o in self.body.Group if o.TypeId == "PartDesign::Groove"])
        self.assertIs(self.body.Tip, bore)

    def test_no_chamfer_on_a_shoulder(self):
        big = shaft(self.doc, self.body, radius=8.0, length=5.0, name="Base")
        small_sk = self.body.newObject("Sketcher::SketchObject", "SmallSk")
        small_sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        small_sk.MapMode = "FlatFace"
        small_sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 5.0))
        small = util.add_pad(self.body, small_sk, 25.0, name="Small")  # the free tip is at z = 25
        self.body.Tip = small
        self.doc.recompute()
        f = self.make(face=cyl_face(small, 5.0), standard="iso", size="M10", chamfer="both", chamfer_size="1 mm")
        g = thread.helpers_of(f)[0]
        self.assertEqual(len(g.Profile[0].Geometry), 3)  # only the free end gets a chamfer, never the shoulder

    def chamfered(self, base, z):
        """A Chamfer feature on the round edge of `base` at height z (radius < 8: the bore or the rod, not a block)."""
        edge = [i + 1 for i, e in enumerate(base.Shape.Edges) if e.Curve.TypeId == "Part::GeomCircle"
                and abs(e.Curve.Center.z - z) < 1e-6 and e.Curve.Radius < 7.9][0]
        ch = self.body.newObject("PartDesign::Chamfer", "Chamfer")
        ch.Base = (base, ["Edge%d" % edge])
        ch.Size = 0.5
        self.body.Tip = ch
        self.doc.recompute()
        self.assertTrue(ch.Shape.isValid())
        return ch

    def test_through_bore_starts_at_its_chamfered_mouth(self):
        """User report: Ø3 through hole chamfered at one end, Thread (Distance) cut from the plain end, so the
        chamfered mouth had no thread. With both ends open, the chamfered one is the entrance."""
        shaft(self.doc, self.body, radius=8.0, length=20.0, name="Block")
        sk = self.body.newObject("Sketcher::SketchObject", "BoreSk")
        sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        sk.MapMode = "FlatFace"
        sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 1.5))
        bore = self.body.newObject("PartDesign::Pocket", "Bore")
        bore.Profile = sk
        bore.Type = "ThroughAll"
        bore.Reversed = True
        self.body.Tip = bore
        self.doc.recompute()
        ch = self.chamfered(bore, 0.0)  # the end nearer the origin: the old rule started at z = 20
        f = self.make(face=cyl_face(ch, 1.5), standard="iso", size="M3.5", length_mode="distance", length="6 mm")
        cut = ch.Shape.cut(self.body.Shape)
        self.assertLess(cut.BoundBox.ZMax, 8.0, cut.BoundBox)
        self.assertLess(cut.BoundBox.ZMin, 0.5, cut.BoundBox)

    def test_rod_starts_at_its_chamfered_tip(self):
        pad = shaft(self.doc, self.body, radius=5.0, length=20.0)  # both ends free: the old rule took z = 20
        ch = self.chamfered(pad, 0.0)
        f = self.make(face=cyl_face(ch, 5.0), standard="iso", size="M10", length_mode="distance", length="6 mm")
        cut = ch.Shape.cut(self.body.Shape)
        self.assertLess(cut.BoundBox.ZMax, 9.0, cut.BoundBox)


class ThreadEditAndDeleteTest(unittest.TestCase):
    """User reports: deleting a thread deleted the part; editing kept the previous start; the thread must start
    at the given distance from the end, not at the rod's beginning."""
    def setUp(self):
        self.doc = util.new_doc("Te")
        self.body = document.make_part(self.doc)
        self.pad = shaft(self.doc, self.body)  # Ø10 × 20, free tip at z = 20, bottom on the XY plane

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def values(self, **kw):
        return dict(thread.defaults(self.doc, self.body, None), face=cyl_face(self.pad, 5), size="M10",
                    clearance="0 mm", **kw)

    def cut_z(self, f):
        cut = self.pad.Shape.cut(self.body.Shape)
        return cut.BoundBox.ZMin, cut.BoundBox.ZMax

    def test_offset_from_the_free_tip_without_a_start_face(self):
        f = thread.create(self.doc, self.body, None, self.values(offset="5 mm", length_mode="distance", length="6 mm"))
        self.doc.recompute()
        zmin, zmax = self.cut_z(f)
        self.assertLess(zmax, 15.05, (zmin, zmax))  # starts exactly 5 mm below the tip (z = 20)
        self.assertGreater(zmin, 7.0, (zmin, zmax))  # and runs 6 mm down (plus the groove's own width)

    def test_editing_the_start_moves_the_thread(self):
        f = thread.create(self.doc, self.body, None, self.values(length_mode="distance", length="6 mm"))
        self.doc.recompute()
        zmin1, zmax1 = self.cut_z(f)
        self.assertGreater(zmin1, 12.0)  # from the tip
        bottom = [i + 1 for i, fc in enumerate(self.pad.Shape.Faces)
                  if fc.Surface.TypeId == "Part::GeomPlane" and abs(fc.CenterOfMass.z) < 1e-6][0]
        v = thread.read(self.doc, f)
        v.update(start_face="%s:Face%d" % (self.pad.Name, bottom), offset="2 mm")
        thread.update(self.doc, f, v)
        self.doc.recompute()
        zmin2, zmax2 = self.cut_z(f)
        self.assertGreater(zmin2, 1.0, (zmin2, zmax2))   # now 2 mm above the bottom
        self.assertLess(zmax2, 9.5, (zmin2, zmax2))
        self.assertEqual(thread.read(self.doc, f)["offset"], "2 mm")
        v = thread.read(self.doc, f)
        v.update(start_face="", offset="0 mm")  # and back to the tip
        thread.update(self.doc, f, v)
        self.doc.recompute()
        zmin3, zmax3 = self.cut_z(f)
        self.assertGreater(zmin3, 12.0, (zmin3, zmax3))

    def test_delete_keeps_the_part_with_features_after_the_thread_and_a_chamfer(self):
        from incad.core import delete
        f = thread.create(self.doc, self.body, None, self.values(chamfer="start", chamfer_size="1 mm"))
        self.doc.recompute()
        top_sk = self.body.newObject("Sketcher::SketchObject", "TopSk")
        top_sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        top_sk.MapMode = "FlatFace"
        top_sk.AttachmentOffset = App.Placement(App.Vector(0, 0, 20), App.Rotation())
        top_sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 3))
        knob = util.add_pad(self.body, top_sk, 5, name="Knob")
        self.body.Tip = knob
        self.doc.recompute()
        v_with = self.body.Shape.Volume
        delete.delete(f)
        self.doc.recompute()
        self.assertFalse([o for o in self.body.Group if o.TypeId in ("PartDesign::SubtractiveHelix", "PartDesign::Groove")])
        self.assertIs(self.body.Tip, knob)
        self.assertTrue(self.body.Shape.isValid())
        expected = math.pi * 25 * 20 + math.pi * 9 * 5
        self.assertAlmostEqual(self.body.Shape.Volume, expected, delta=1.0)
        self.assertGreater(self.body.Shape.Volume, v_with)


class ThreadHoleFitTest(unittest.TestCase):
    """User request: M3 or M4 must work in a Ø3 hole. The thread first fills or bores the hole to the size's tap
    diameter (a helper before the thread), so the result is a full thread with no thin rings."""
    def setUp(self):
        self.doc = util.new_doc("Tf")
        self.body = document.make_part(self.doc)
        shaft(self.doc, self.body, radius=8.0, length=20.0, name="Block")
        sk = self.body.newObject("Sketcher::SketchObject", "BoreSk")
        sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        sk.MapMode = "FlatFace"
        sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 1.5))
        self.bore = self.body.newObject("PartDesign::Pocket", "Bore")
        self.bore.Profile = sk
        self.bore.Type = "ThroughAll"
        self.bore.Reversed = True
        self.body.Tip = self.bore
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def make(self, **values):
        v = dict(thread.defaults(self.doc, self.body, None), face=cyl_face(self.bore, 1.5), **values)
        f = thread.create(self.doc, self.body, None, v)
        self.doc.recompute()
        return f

    def solid_at(self, r, z0=1.0, z1=19.0, n=150):
        """How many of n points on a helix-free sampling line at radius r (z0..z1, a few angles) are in material."""
        shape = self.body.Shape
        pts = [App.Vector(r * math.cos(a), r * math.sin(a), z0 + (z1 - z0) * i / (n - 1))
               for i in range(n) for a in (0.0, 2.1)]
        return sum(1 for p in pts if shape.isInside(p, 1e-6, True)), len(pts)

    def test_auto_size_is_the_matching_bolt(self):
        f = self.make(size="")
        self.assertEqual(thread.read(self.doc, f)["size"], "M3")

    def test_m3_fills_the_hole_to_its_tap_size(self):
        f = self.make(size="M3")
        self.assertTrue(self.body.Shape.isValid())
        prep = [h for h in thread.helpers_of(f) if h.TypeId == "PartDesign::Revolution"]
        self.assertEqual(len(prep), 1)
        self.assertIs(f.BaseFeature, prep[0])  # the fill comes before the thread, which cuts it
        rb = st.bore_diameter(st.spec("iso", "M3"), 0.2) / 2.0
        inside, total = self.solid_at((rb + 1.5) / 2.0)  # between the tap size and the old wall: the teeth
        self.assertGreater(inside, total * 0.2)
        self.assertLess(inside, total * 0.8)
        self.assertEqual(self.solid_at(rb - 0.05)[0], 0)  # the bore itself stays open

    def test_m4_bores_the_hole_out_without_thin_rings(self):
        f = self.make(size="M4")
        self.assertTrue(self.body.Shape.isValid())
        prep = [h for h in thread.helpers_of(f) if h.TypeId == "PartDesign::Groove" and h.BaseFeature is self.bore]
        self.assertEqual(len(prep), 1)
        self.assertIs(f.BaseFeature, prep[0])
        rb = st.bore_diameter(st.spec("iso", "M4"), 0.2) / 2.0
        self.assertEqual(self.solid_at((rb + 1.5) / 2.0)[0], 0)  # old wall .. tap size: all bored away
        inside, total = self.solid_at(rb + 0.15)
        self.assertGreater(inside, total * 0.2)  # the teeth start at the tap size
        self.assertLess(inside, total * 0.8)

    def test_editing_to_a_size_that_fits_removes_the_prep_and_delete_cleans_up(self):
        from incad.core import delete
        f = self.make(size="M3")
        thread.update(self.doc, f, dict(thread.read(self.doc, f), size="M4"))
        self.doc.recompute()
        self.assertEqual([h.TypeId for h in thread.helpers_of(f)], ["PartDesign::Groove"])
        thread.update(self.doc, f, dict(thread.read(self.doc, f), size="M3.5"))  # tap Ø3.05 in Ø3: no prep
        self.doc.recompute()
        self.assertEqual(thread.helpers_of(f), [])
        self.assertIs(f.BaseFeature, self.bore)
        thread.update(self.doc, f, dict(thread.read(self.doc, f), size="M3", chamfer="both", chamfer_size="0.3 mm"))
        self.doc.recompute()
        self.assertTrue(self.body.Shape.isValid())
        self.assertEqual(sorted(h.TypeId for h in thread.helpers_of(f)),
                         ["PartDesign::Groove", "PartDesign::Revolution"])
        delete.delete(f)
        self.doc.recompute()
        self.assertFalse([o for o in self.body.Group if o.TypeId in ("PartDesign::Groove", "PartDesign::Revolution")])
        self.assertIs(self.body.Tip, self.bore)
        self.assertAlmostEqual(self.body.Shape.Volume, self.bore.Shape.Volume, places=3)

    def test_fill_never_grows_into_a_crossing_bore(self):
        """User's part: a big bore crosses beside the Ø3 hole and opens a window in its wall; the fill put rings of
        teeth into the big bore there. The hole is filled only where it has a wall all round."""
        cross = self.body.newObject("PartDesign::SubtractiveCylinder", "Cross")
        cross.Radius, cross.Height = 7.0, 40.0
        cross.Placement = App.Placement(App.Vector(-20, 7.5, 10), App.Rotation(App.Vector(0, 1, 0), 90))
        self.body.Tip = cross
        self.doc.recompute()
        f = thread.create(self.doc, self.body, None, dict(thread.defaults(self.doc, self.body, None),
                                                           face=cyl_face(cross, 1.5), size="M3"))
        self.doc.recompute()
        self.assertTrue(self.body.Shape.isValid())
        self.assertEqual(len(self.body.Shape.Solids), 1)
        bore_space = Part.makeCylinder(7.0, 40.0, App.Vector(-20, 7.5, 10), App.Vector(1, 0, 0))
        self.assertAlmostEqual(self.body.Shape.common(bore_space).Volume, 0, 3)
        rb = st.bore_diameter(st.spec("iso", "M3"), 0.2) / 2.0
        inside, total = self.solid_at((rb + 1.5) / 2.0, 1.0, 4.0)  # below the window: still a full thread
        self.assertGreater(inside, total * 0.2)

    def test_start_face_at_a_chamfered_mouth_and_on_a_helper(self):
        """User report: picking the front face of a hole whose mouth is chamfered said 'The start face must be at one
        end of the threaded face' (the hole's end edge is behind the chamfer), and the pick named ThreadFill."""
        ch = self.body.newObject("PartDesign::Chamfer", "Chamfer")
        edge = [i + 1 for i, e in enumerate(self.bore.Shape.Edges) if e.Curve.TypeId == "Part::GeomCircle"
                and abs(e.Curve.Radius - 1.5) < 1e-6 and abs(e.Curve.Center.z) < 1e-6][0]
        ch.Base = (self.bore, ["Edge%d" % edge])
        ch.Size = 0.5
        self.body.Tip = ch
        self.doc.recompute()
        bottom = [i + 1 for i, fc in enumerate(ch.Shape.Faces) if fc.Surface.TypeId == "Part::GeomPlane"
                  and abs(fc.BoundBox.ZMax) < 1e-6][0]
        values = dict(thread.defaults(self.doc, self.body, None), face=cyl_face(ch, 1.5), size="M3",
                      length_mode="distance", length="6 mm", start_face="%s:Face%d" % (ch.Name, bottom))
        f = thread.create(self.doc, self.body, None, values)
        self.doc.recompute()
        cut = ch.Shape.cut(self.body.Shape)
        self.assertLess(cut.BoundBox.ZMax, 8.0, cut.BoundBox)  # from the bottom (chamfered) mouth
        fill = [h for h in thread.helpers_of(f) if h.TypeId == "PartDesign::Revolution"][0]
        top = [i + 1 for i, fc in enumerate(fill.Shape.Faces) if fc.Surface.TypeId == "Part::GeomPlane"
               and abs(fc.BoundBox.ZMin - 20) < 1e-6][0]
        thread.update(self.doc, f, dict(thread.read(self.doc, f), start_face="%s:Face%d" % (fill.Name, top)))
        self.doc.recompute()
        self.assertTrue(thread.read(self.doc, f)["start_face"].startswith(ch.Name + ":"))  # never the helper
        cut = ch.Shape.cut(self.body.Shape)
        self.assertGreater(cut.BoundBox.ZMin, 12.0, cut.BoundBox)  # now from the top

    def test_a_far_off_size_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            self.make(size="M8")
        self.assertIn("size", str(ctx.exception).lower())
        self.assertIs(self.body.Tip, self.bore)
        self.assertFalse([o for o in self.body.Group if getattr(o, "InCadHelperOf", None)])


class ThreadFinTest(unittest.TestCase):
    """User report: M2.5 on a Ø2.594 rod came out sloppy. With the print clearance the groove reaches its widest
    (pitch less a hair) below the rod's surface: a 0.04 mm fin was left on every crest (on Ø2.5 too). The rod is
    turned down (a hole bored out) to where the groove reaches its width, so the crests are clean."""
    def setUp(self):
        self.doc = util.new_doc("Fn")
        self.body = document.make_part(self.doc)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    @staticmethod
    def widest(spec, internal, clearance):
        """The radii where the groove profile is at its full width (the fin zone starts past them)."""
        g = profile.groove(spec, internal, clearance)
        half = profile.max_width(g) / 2.0
        return [r for r, z in g if abs(abs(z) - half) < 1e-9]

    def material(self, r, z0, z1, n=240):
        shape = self.body.Shape
        pts = [App.Vector(r * math.cos(a), r * math.sin(a), z0 + (z1 - z0) * i / (n - 1))
               for i in range(n) for a in (0.0, 1.3, 2.9, 4.4)]
        return sum(1 for p in pts if shape.isInside(p, 1e-7, True))

    def test_no_fins_on_a_slightly_oversize_rod_or_an_exact_one(self):
        for radius in (2.594 / 2, 2.5 / 2):
            pad = shaft(self.doc, self.body, radius=radius, length=8.0, name="Rod%d" % int(radius * 1000))
            f = thread.create(self.doc, self.body, None, dict(thread.defaults(self.doc, self.body, None),
                                                              face=cyl_face(pad, radius), size="M2.5",
                                                              clearance="0.2 mm"))
            self.doc.recompute()
            self.assertTrue(self.body.Shape.isValid())
            knee = min(self.widest(thread._spec(dict(standard="iso", size="M2.5"), 2 * radius, False, 0.2),
                                   False, 0.2))
            self.assertLess(knee, radius - 0.01)  # the fin zone exists for this size
            self.assertEqual(self.material((knee + radius) / 2.0, 2.0, 6.0), 0, radius)
            self.tearDown()
            self.setUp()

    def test_no_fins_with_a_small_clearance_either(self):
        radius = 2.594 / 2
        pad = shaft(self.doc, self.body, radius=radius, length=8.0, name="Rod")
        thread.create(self.doc, self.body, None, dict(thread.defaults(self.doc, self.body, None),
                                                      face=cyl_face(pad, radius), size="M2.5", clearance="0.1 mm"))
        self.doc.recompute()
        knee = min(self.widest(thread._spec(dict(standard="iso", size="M2.5"), 2 * radius, False, 0.1), False, 0.1))
        self.assertEqual(self.material((knee + radius) / 2.0, 2.0, 6.0), 0)

    def test_no_fins_next_to_a_shoulder(self):
        """Review: at a closed end the trim stopped half a groove short while the last turn reached the shoulder."""
        shaft(self.doc, self.body, radius=6.0, length=5.0, name="Base")
        sk = self.body.newObject("Sketcher::SketchObject", "RodSk")
        sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        sk.MapMode = "FlatFace"
        sk.AttachmentOffset = App.Placement(App.Vector(0, 0, 5), App.Rotation())
        radius = 2.5 / 2
        sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), radius))
        rod = util.add_pad(self.body, sk, 10.0, name="Rod")
        self.body.Tip = rod
        self.doc.recompute()
        thread.create(self.doc, self.body, None, dict(thread.defaults(self.doc, self.body, None),
                                                      face=cyl_face(rod, radius), size="M2.5", clearance="0.2 mm"))
        self.doc.recompute()
        self.assertTrue(self.body.Shape.isValid())
        knee = min(self.widest(thread._spec(dict(standard="iso", size="M2.5"), 2 * radius, False, 0.2), False, 0.2))
        self.assertEqual(self.material((knee + radius) / 2.0, 5.02, 6.0), 0)

    def test_no_fin_zone_inside_standard_tapped_holes(self):
        """Review: the old check here asserted nothing (it only ran 'if' a fin zone existed). For every standard
        size and print clearance the internal groove is at full width inside the tap bore already, so a hole never
        keeps a fin; the bore-to-knee rule in _fit only guards custom profiles."""
        for std in ("iso", "iso_fine", "unc", "unf", "tr", "acme", "print"):
            if std not in dict(st.STANDARDS):
                continue
            for size in st.sizes(std):
                s = st.spec(std, size)
                for c in (0.0, 0.2, 0.4):
                    self.assertLessEqual(profile.knee(s, True, c), st.bore_diameter(s, c) / 2 + 0.005, (std, size, c))

    def test_a_chamfer_at_an_end_the_trim_does_not_reach_leaves_no_sleeve(self):
        """Review: the chamfer used the trimmed radius at an end the ThreadTrim did not reach (a short thread, an
        offset), leaving a ~0.09 mm sleeve there."""
        radius = 2.5 / 2
        pad = shaft(self.doc, self.body, radius=radius, length=8.0, name="Rod")
        thread.create(self.doc, self.body, None, dict(thread.defaults(self.doc, self.body, None),
                                                      face=cyl_face(pad, radius), size="M2.5", clearance="0.2 mm",
                                                      length_mode="distance", length="4 mm", chamfer="both",
                                                      chamfer_size="0.3 mm"))
        self.doc.recompute()
        self.assertTrue(self.body.Shape.isValid())
        for z in (0.05, 0.1, 7.9, 7.95):  # both ends of the rod, inside the 0.3 mm chamfers
            pts = [App.Vector(1.2 * math.cos(a), 1.2 * math.sin(a), z) for a in (0.0, 1.6, 3.1, 4.7)]
            self.assertFalse([p for p in pts if self.body.Shape.isInside(p, 1e-7, True)], z)


class ThreadProfilesTest(unittest.TestCase):
    """The user: Custom offers a triangle (its angle), a trapezoid (a triangle with its tip cut off) and a semicircle
    (jar lids); every thread is cut into the surface or grown onto it; DIN 405 round and DIN 513 buttress threads, and
    the DIN names on the ones that are the same."""

    def setUp(self):
        self.doc = util.new_doc("ThProf")
        self.body = document.make_part(self.doc)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def make(self, face, **values):
        f = thread.create(self.doc, self.body, None, dict(thread.defaults(self.doc, self.body, None), face=face,
                                                           **values))
        self.doc.recompute()
        return f

    def check(self, f, before, more):
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        shape = self.body.Shape
        self.assertTrue(shape.isValid())
        self.assertEqual(len(shape.Solids), 1)
        if more:
            self.assertGreater(shape.Volume, before + 1.0)
        else:
            self.assertLess(shape.Volume, before - 1.0)

    def test_save_for_the_next_part(self):
        """The user: after a custom thread, a way to keep it for the next part — not picking it all again."""
        from incad.core import prefs
        try:
            v = dict(thread.defaults(self.doc, self.body, None), standard="custom", custom_profile="semicircle",
                     custom_pitch="4 mm", custom_depth="1.2 mm", build="grow", clearance="0.1 mm", remember=True,
                     face="Shaft:Face1")
            thread.after_ok(self.doc, None, v)
            d = thread.defaults(self.doc, self.body, None)
            for k in ("standard", "custom_profile", "custom_pitch", "custom_depth", "build", "clearance"):
                self.assertEqual(d[k], v[k], k)
            self.assertEqual(d["face"], "", "the faces are never kept")
            self.assertFalse(d["remember"], "the box starts unticked")
            thread.after_ok(self.doc, None, dict(d, standard="iso", remember=False))  # not ticked: nothing saved
            self.assertEqual(thread.defaults(self.doc, self.body, None)["standard"], "custom")
        finally:
            prefs.set_thread_preset({})
        self.assertEqual(thread.defaults(self.doc, self.body, None)["standard"], "iso")

    def test_din_names(self):
        names = dict(st.STANDARDS)
        self.assertEqual(names["iso"], "ISO metric (DIN 13)")
        self.assertEqual(names["tr"], "Trapezoidal (DIN 103)")
        self.assertIn("rd", names)
        self.assertIn("s", names)
        self.assertIn("Rd20", st.sizes("rd"))
        self.assertIn("S20x4", st.sizes("s"))

    def test_din_405_round_and_din_513_buttress_cut(self):
        for std, size in (("rd", "Rd20"), ("s", "S20x4")):
            with self.subTest(size):
                pad = shaft(self.doc, self.body, radius=10.0, length=30.0, name="Shaft" + std)
                v = self.body.Shape.Volume
                f = self.make(cyl_face(pad, 10.0), standard=std, size=size)
                self.check(f, v, more=False)
                thread._discard(self.doc, self.body, f)
                self.body.removeObject(pad)
                self.doc.removeObject(pad.Name)
                self.doc.recompute()

    def test_custom_trapezoid_cut(self):
        pad = shaft(self.doc, self.body, radius=10.0, length=20.0)
        v = self.body.Shape.Volume
        f = self.make(cyl_face(pad, 10.0), standard="custom", custom_profile="trapezoid", custom_pitch="2 mm",
                      custom_angle="60 °", custom_depth="0.8 mm")
        self.check(f, v, more=False)
        self.assertEqual(thread.read(self.doc, f)["custom_profile"], "trapezoid")

    def test_jar_neck_grows_a_semicircle_bead_and_the_lid_has_its_groove(self):
        pad = shaft(self.doc, self.body, radius=15.0, length=20.0, name="Neck")
        v = self.body.Shape.Volume
        f = self.make(cyl_face(pad, 15.0), standard="custom", custom_profile="semicircle", custom_pitch="4 mm",
                      custom_depth="1.2 mm", build="grow", clearance="0 mm")
        self.check(f, v, more=True)
        self.assertEqual(f.TypeId, "PartDesign::FeaturePython", "grown teeth: a feature of their own")
        top = self.body.Shape.optimalBoundingBox().XMax  # (a sweep's BoundBox is its spline's loose box)
        self.assertLess(top, 15.0 + 1.2 + 1e-3)  # no higher than the bead
        self.assertGreater(top, 15.0 + 1.0)
        self.assertEqual(thread.read(self.doc, f)["build"], "grow")
        # the lid: a bore with the semicircle cut into its wall
        lid = util.new_doc("Lid")
        try:
            body = document.make_part(lid)
            block = shaft(lid, body, radius=20.0, length=20.0, name="Cap")
            sk = body.newObject("Sketcher::SketchObject", "BoreSk")
            sk.AttachmentSupport = [(util.xy_plane(body), "")]
            sk.MapMode = "FlatFace"
            sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 15.3))
            bore = body.newObject("PartDesign::Pocket", "Bore")
            bore.Profile = sk
            bore.Type = "ThroughAll"
            bore.Reversed = True
            body.Tip = bore
            lid.recompute()
            v2 = body.Shape.Volume
            g = thread.create(lid, body, None, dict(thread.defaults(lid, body, None), face=cyl_face(bore, 15.3),
                                                    standard="custom", custom_profile="semicircle",
                                                    custom_pitch="4 mm", custom_depth="1.3 mm"))
            lid.recompute()
            self.assertNotIn("Invalid", g.State, g.getStatusString())
            self.assertTrue(body.Shape.isValid())
            self.assertLess(body.Shape.Volume, v2 - 1.0)
        finally:
            App.closeDocument(lid.Name)

    def test_grown_teeth_rise_out_of_the_rod_and_sink_back_into_it(self):
        """The user: "a grown round thread starts from nothing inside the face, grows, and runs back smoothly into
        the wall at its end — canister caps are made so". The teeth stand full height in the middle, are sunk into
        the rod at both ends, never reach the end faces; a triangle grows as a triangle."""
        import math
        for kind in ("triangle", "semicircle"):
            with self.subTest(kind):
                pad = shaft(self.doc, self.body, radius=10.0, length=20.0, name="Rod" + kind)
                v = self.body.Shape.Volume
                f = self.make(cyl_face(pad, 10.0), standard="custom", custom_profile=kind, custom_pitch="1.5 mm",
                              custom_angle="60 °", custom_depth="0.7 mm", build="grow", clearance="0.2 mm")
                self.check(f, v, more=True)
                shape = self.body.Shape
                for z in (0.0, 20.0):
                    ends = [fc for fc in shape.Faces if fc.Surface.TypeId == "Part::GeomPlane"
                            and abs(fc.CenterOfMass.z - z) < 1e-6]
                    self.assertEqual(len(ends), 1, "the end faces as they were")
                    self.assertAlmostEqual(ends[0].Area, math.pi * 100, places=3)

                def tallest(z0, z1):
                    """The highest point of the teeth between z0 and z1, from the axis."""
                    slab = shape.common(Part.makeCylinder(12, z1 - z0, App.Vector(0, 0, z0)))
                    return max(math.hypot(p.x, p.y) for p in [vx.Point for vx in slab.Vertexes] +
                               [e.valueAt(e.FirstParameter + (e.LastParameter - e.FirstParameter) * k / 8)
                                for e in slab.Edges for k in range(9)])
                self.assertGreater(tallest(8.0, 12.0), 10.0 + 0.5, "full height in the middle")
                self.assertLess(tallest(0.0, 0.3), 10.0 + 1e-3, "nothing at the very ends: sunk into the rod")
                self.assertLess(tallest(19.7, 20.0), 10.0 + 1e-3)
                thread._discard(self.doc, self.body, f)
                self.body.removeObject(pad)
                self.doc.removeObject(pad.Name)
                self.doc.recompute()

    def test_grown_teeth_on_a_long_rod_stand_all_along_it(self):
        """The user's rod, 46 turns: one sweep of the middle came out with the first half of the teeth missing ("a
        complete mess"). Every turn of a long thread stands on the rod."""
        import math
        sk = self.body.newObject("Sketcher::SketchObject", "LongSk")  # the user's: along X, off the origin
        sk.AttachmentSupport = [([o for o in self.body.Origin.OriginFeatures if o.Role == "YZ_Plane"][0], "")]
        sk.MapMode = "FlatFace"
        sk.AttachmentOffset = App.Placement(App.Vector(0, 0, 88.0), App.Rotation())
        sk.addGeometry(Part.Circle(App.Vector(-40.5, 52.3, 0), App.Vector(0, 0, 1), 32.32))
        pad = util.add_pad(self.body, sk, 256.0, name="Long")
        self.body.Tip = pad
        self.doc.recompute()
        c = [fc for fc in pad.Shape.Faces if fc.Surface.TypeId == "Part::GeomCylinder"][0].Surface
        v = self.body.Shape.Volume
        f = self.make(cyl_face(pad, 32.32), standard="custom", custom_profile="semicircle", custom_pitch="5.5 mm",
                      custom_depth="1.9 mm", build="grow", clearance="0.2 mm")
        self.check(f, v, more=True)
        shape = self.body.Shape
        ax, at = App.Vector(c.Axis), c.Center
        u = ax.cross(App.Vector(0, 0, 1) if abs(ax.z) < 0.9 else App.Vector(1, 0, 0))
        u.normalize()
        w = ax.cross(u)
        lo = min((vx.Point - at).dot(ax) for vx in pad.Shape.Vertexes)
        r = 32.32 + 1.0
        for k in range(1, 12):
            a = lo + 256.0 * k / 12
            hits = sum(1 for j in range(24) if shape.isInside(
                at + ax * a + u * (r * math.cos(j * math.pi / 12)) + w * (r * math.sin(j * math.pi / 12)), 1e-6, True))
            self.assertGreater(hits, 6, ("teeth at", round(a, 1), hits))

    def test_a_grown_thread_made_with_its_chamfer_ends_with_it(self):
        """Made with the chamfer at once (not edited to it): the chamfer is the part's last step, so the teeth run
        out past the end face are cut — End of Part stayed on the thread, and they stuck out of the hole."""
        import math
        shaft(self.doc, self.body, radius=15.0, length=12.0, name="Cap")
        sk = self.body.newObject("Sketcher::SketchObject", "BoreSk")
        sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        sk.MapMode = "FlatFace"
        sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 8.0))
        bore = self.body.newObject("PartDesign::Pocket", "Bore")
        bore.Profile = sk
        bore.Type = "ThroughAll"
        bore.Reversed = True
        self.body.Tip = bore
        self.doc.recompute()
        f = self.make(cyl_face(bore, 8.0), standard="custom", custom_profile="semicircle", custom_pitch="1.5 mm",
                      custom_depth="0.7 mm", build="grow", chamfer="start", chamfer_size="0.8 mm")
        ch = [h for h in thread.helpers_of(f) if getattr(h, thread.ROLE, "") in ("", "chamfer")]
        self.assertEqual(len(ch), 1)
        self.assertIs(self.body.Tip, ch[0], "the chamfer is the last step")
        shape = self.body.Shape
        self.assertEqual(len(shape.Solids), 1)
        for z0 in (12.0, -10.0):
            self.assertLess(shape.common(Part.makeBox(60, 60, 10, App.Vector(-30, -30, z0))).Volume, 1e-6, z0)

    def test_a_grown_thread_can_be_deleted(self):
        """The user: "why can't I delete this?" — Delete looked for the thread's profile sketch, which a grown
        thread has none of."""
        from incad.core import delete
        pad = shaft(self.doc, self.body, radius=10.0, length=20.0, name="Del")
        f = self.make(cyl_face(pad, 10.0), standard="custom", custom_profile="semicircle", custom_pitch="2 mm",
                      custom_depth="0.8 mm", build="grow")
        name = f.Name
        delete.delete(f)
        self.doc.recompute()
        self.assertIsNone(self.doc.getObject(name))
        self.assertIs(self.body.Tip, pad)

    def test_grown_teeth_stay_on_the_wall_of_a_hole_into_a_curved_face(self):
        """The user's hole with its mouth in a curved face: the grown teeth ran on out of it into the air. Now they
        stand only where the wall is behind them."""
        box = self.body.newObject("PartDesign::AdditiveBox", "Block")
        box.Length, box.Width, box.Height = 30.0, 20.0, 10.0
        box.Placement = App.Placement(App.Vector(-15, -10, 0), App.Rotation())
        saddle = self.body.newObject("PartDesign::SubtractiveCylinder", "Saddle")
        saddle.Radius, saddle.Height = 8.0, 10.0
        hole = self.body.newObject("PartDesign::SubtractiveCylinder", "Hole")
        hole.Radius, hole.Height = 2.5, 15.0  # its far end in the saddle's curved wall
        hole.Placement = App.Placement(App.Vector(15, 5, 5), App.Rotation(App.Vector(0, 1, 0), -90))
        self.body.Tip = hole
        self.doc.recompute()
        v = self.body.Shape.Volume
        f = self.make(cyl_face(hole, 2.5), standard="custom", custom_profile="semicircle", custom_pitch="1.5 mm",
                      custom_depth="0.6 mm", build="grow", clearance="0.2 mm")
        self.check(f, v, more=True)
        out = self.body.Shape.cut(saddle.Shape)
        self.assertLess(out.Volume, 1e-3, "nothing out in the saddle's air: %g" % out.Volume)
        added = self.body.Shape.Volume - v
        self.assertGreater(added, 5.0, "the teeth are there")

    def test_grown_teeth_in_a_through_hole_leave_clean_end_faces(self):
        """The user's p2_test: a bead grown in a through hole, Start face picked: the end faces showed a seam ring
        round the hole ("when a thread is cut there is no such thing") — teeth grown past the faces and trimmed.
        Now they rise out of the wall inside the hole and sink back into it: each end face stays one face."""
        import math
        shaft(self.doc, self.body, radius=15.0, length=12.0, name="Cap")
        sk = self.body.newObject("Sketcher::SketchObject", "BoreSk")
        sk.AttachmentSupport = [(util.xy_plane(self.body), "")]
        sk.MapMode = "FlatFace"
        sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 8.0))
        bore = self.body.newObject("PartDesign::Pocket", "Bore")
        bore.Profile = sk
        bore.Type = "ThroughAll"
        bore.Reversed = True
        self.body.Tip = bore
        self.doc.recompute()
        v = self.body.Shape.Volume
        bottom = [i + 1 for i, fc in enumerate(bore.Shape.Faces)
                  if fc.Surface.TypeId == "Part::GeomPlane" and abs(fc.CenterOfMass.z) < 1e-6][0]
        f = self.make(cyl_face(bore, 8.0), start_face="%s:Face%d" % (bore.Name, bottom), standard="custom",
                      custom_profile="semicircle", custom_pitch="1.5 mm", custom_depth="0.7 mm", build="grow",
                      clearance="0.2 mm")
        self.check(f, v, more=True)
        shape = self.body.Shape
        for z in (0.0, 12.0):
            ends = [fc for fc in shape.Faces if fc.Surface.TypeId == "Part::GeomPlane"
                    and abs(fc.CenterOfMass.z - z) < 1e-6 and abs(fc.normalAt(0, 0).z) > 0.99]
            self.assertEqual(len(ends), 1, ("one end face, no seam ring", z, len(ends)))
        r = 8.0 - 0.35  # in the bead (it rises out of the wall a little way in from each end)
        for z in (3.0, 6.0, 9.0):
            hits = sum(1 for k in range(48)
                       if shape.isInside(App.Vector(r * math.cos(k * math.pi / 24), r * math.sin(k * math.pi / 24), z),
                                         1e-6, True))
            self.assertGreater(hits, 10, ("teeth there", z))
        for z0 in (12.0, -10.0):
            beyond = shape.common(Part.makeBox(60, 60, 10, App.Vector(-30, -30, z0))).Volume
            self.assertLess(beyond, 1e-6, "nothing past the end faces")
        # the user: "and there is no chamfer option either"; "past a certain size the chamfer leaves this crap";
        # "the thread reaches the end of the chamfer": the teeth run full height into the chamfer, which cuts them
        # with the corner (as on a bolt), one solid, no blades
        v2 = shape.Volume
        f = thread.update(self.doc, f, dict(thread.read(self.doc, f), chamfer="both", chamfer_size="2.5 mm"))
        self.doc.recompute()
        ch = [h for h in thread.helpers_of(f) if getattr(h, thread.ROLE, "") in ("", "chamfer")]
        self.assertEqual(len(ch), 1)
        self.assertIs(ch[0].BaseFeature, f, "the chamfer is made after the thread")
        self.assertIs(self.body.Tip, ch[0])
        shape = self.body.Shape
        self.assertTrue(shape.isValid())
        self.assertEqual(len(shape.Solids), 1, "no blades cut off the teeth")
        for z0 in (12.0, -10.0):
            self.assertLess(shape.common(Part.makeBox(60, 60, 10, App.Vector(-30, -30, z0))).Volume, 1e-6)
        for z in (0.1, 11.9):  # in the chamfered corner, at the teeth's radius: air all round
            self.assertFalse(any(shape.isInside(App.Vector(7.85 * math.cos(k * math.pi / 12),
                                                           7.85 * math.sin(k * math.pi / 12), z), 1e-6, True)
                                 for k in range(24)), z)
        r = 8.0 - 0.35  # and the teeth right up to it, full height
        for z in (3.0, 6.0, 9.0):
            self.assertGreater(sum(1 for k in range(48) if shape.isInside(App.Vector(
                r * math.cos(k * math.pi / 24), r * math.sin(k * math.pi / 24), z), 1e-6, True)), 10, z)

    def test_grown_iso_thread_on_a_rod_is_the_bolt(self):
        pad = shaft(self.doc, self.body, radius=4.0, length=20.0)  # about M10's minor diameter
        v = self.body.Shape.Volume
        f = self.make(cyl_face(pad, 4.0), standard="iso", size="M10", build="grow", clearance="0 mm")
        self.check(f, v, more=True)
        R = self.body.Shape.optimalBoundingBox().XMax
        self.assertLess(R, 4.0 + st.spec("iso", "M10").depth + 1e-3)
