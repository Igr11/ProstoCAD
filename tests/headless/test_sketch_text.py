"""Sketch text geometry (incad.core.sketch_text): Size is the capital height, alignment and angle about the anchor,
the Mac's fonts with their Bold / Italic files, Cyrillic, and text along a curve (a circle's top, outside or flipped
inside; a line too short)."""
import math
import unittest

import FreeCAD as App
import Part
from incad.core import sketch_text as st


def box(faces):
    return Part.makeCompound(faces).BoundBox


class SketchTextTest(unittest.TestCase):
    def setUp(self):
        self.font = st.font_path("Arial")

    def test_fonts_have_families_and_styles(self):
        fonts = st.fonts()
        self.assertIn("Arial", fonts)
        self.assertTrue({"Regular", "Bold", "Italic", "Bold Italic"} <= set(fonts["Arial"]), fonts["Arial"])
        self.assertTrue(st.font_path("Arial", bold=True).endswith("Arial Bold.ttf"))

    def test_size_is_the_capital_height(self):
        for family in ("Arial", "Times New Roman"):
            b = box(st.at_point("HELIX", st.font_path(family), 5.0))  # flat-topped capitals (O, C overshoot)
            self.assertAlmostEqual(b.YMax - b.YMin, 5.0, delta=0.02, msg=family)
            self.assertAlmostEqual(b.YMin, 0.0, delta=0.02)

    def test_letters_keep_their_holes_and_cyrillic_works(self):
        faces = st.at_point("OЖ", self.font, 10.0)
        self.assertEqual(len(faces), 2)
        self.assertEqual(len(faces[0].Wires), 2, "O has its hole")
        self.assertTrue(all(f.isValid() for f in faces))

    def test_alignment_and_angle_about_the_anchor(self):
        left = box(st.at_point("ABC", self.font, 5.0, anchor=(10, 20)))
        self.assertAlmostEqual(left.XMin, 10.0, delta=0.6)
        centre = box(st.at_point("ABC", self.font, 5.0, anchor=(10, 20), align="center"))
        self.assertAlmostEqual((centre.XMin + centre.XMax) / 2, 10.0, delta=0.6)
        right = box(st.at_point("ABC", self.font, 5.0, anchor=(10, 20), align="right"))
        self.assertAlmostEqual(right.XMax, 10.0, delta=0.6)
        up = box(st.at_point("ABC", self.font, 5.0, anchor=(0, 0), angle=90))
        self.assertGreater(up.YMax - up.YMin, up.XMax - up.XMin, "turned 90°: the text runs up")

    def test_two_lines(self):
        b = box(st.at_point("AB\nEF", self.font, 5.0, line_spacing=1.5))
        self.assertAlmostEqual(b.YMin, -7.5, delta=0.05)

    BOX = [App.Vector(10, 10, 0), App.Vector(50, 10, 0), App.Vector(50, 30, 0), App.Vector(10, 30, 0)]

    def test_in_a_box_centred_in_the_middle(self):
        b = box(st.in_box("HEX", self.font, 6.0, self.BOX))
        self.assertAlmostEqual((b.XMin + b.XMax) / 2, 30.0, delta=0.3)
        self.assertAlmostEqual((b.YMin + b.YMax) / 2, 20.0, delta=0.05)
        self.assertAlmostEqual(b.YMax - b.YMin, 6.0, delta=0.05)

    def test_in_a_box_top_left_and_bottom_right(self):
        b = box(st.in_box("HEX", self.font, 6.0, self.BOX, halign="left", valign="top"))
        self.assertAlmostEqual(b.XMin, 10.0, delta=0.8)
        self.assertAlmostEqual(b.YMax, 30.0, delta=0.05)
        b = box(st.in_box("HEX", self.font, 6.0, self.BOX, halign="right", valign="bottom"))
        self.assertAlmostEqual(b.XMax, 50.0, delta=0.8)
        self.assertAlmostEqual(b.YMin, 10.0, delta=0.05)

    def test_fit_fills_the_width_and_never_the_height_over(self):
        b = box(st.in_box("HEX", self.font, 1.0, self.BOX, fit=True))
        self.assertAlmostEqual(b.XMax - b.XMin, 40.0, delta=1.0)
        self.assertLessEqual(b.YMax - b.YMin, 20.0 + 1e-6)
        tall = [App.Vector(0, 0, 0), App.Vector(40, 0, 0), App.Vector(40, 4, 0), App.Vector(0, 4, 0)]
        t = box(st.in_box("HEX", self.font, 1.0, tall, fit=True))
        self.assertAlmostEqual(t.YMax - t.YMin, 4.0, delta=0.05)  # a flat box: the height limits

    def test_a_tilted_box_tilts_the_text(self):
        import math as m
        a = m.radians(30)
        u, v = App.Vector(m.cos(a), m.sin(a), 0), App.Vector(-m.sin(a), m.cos(a), 0)
        corners = [App.Vector(), u * 40, u * 40 + v * 12, v * 12]
        faces = st.in_box("HHHH", self.font, 5.0, corners)
        first, last = faces[0].CenterOfMass, faces[-1].CenterOfMass
        self.assertAlmostEqual(m.degrees(m.atan2(last.y - first.y, last.x - first.x)), 30.0, delta=0.5)

    def test_along_a_circle_centred_on_its_top_outside(self):
        circle = Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 20).toShape()
        faces = st.along("INCAD", self.font, 4.0, circle)
        self.assertEqual(len(faces), 5)
        area = sum(f.Area for f in faces)
        c = sum((f.CenterOfMass * f.Area for f in faces), App.Vector()) * (1.0 / area)
        self.assertAlmostEqual(c.x, 0.0, delta=0.5, msg="centred on the top")
        self.assertGreater(c.y, 20.0, "outside the ring")
        xs = [f.CenterOfMass.x for f in faces]
        self.assertEqual(xs, sorted(xs), "reads left to right across the top")
        for f in faces:
            r = math.hypot(f.CenterOfMass.x, f.CenterOfMass.y)
            self.assertTrue(20.0 < r < 24.5, r)

    def test_flipped_reads_inside_at_the_bottom(self):
        circle = Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 20).toShape()
        faces = st.along("INCAD", self.font, 4.0, circle, flip=True)
        for f in faces:
            r = math.hypot(f.CenterOfMass.x, f.CenterOfMass.y)
            self.assertTrue(15.0 < r < 20.0, r)

    def test_along_a_line_with_distance_and_too_long(self):
        line = Part.LineSegment(App.Vector(0, 0, 0), App.Vector(60, 0, 0)).toShape()
        b = box(st.along("TEXT", self.font, 5.0, line, position="start", distance=2.0))
        self.assertAlmostEqual(b.YMin, 2.0, delta=0.05)
        self.assertAlmostEqual(b.XMin, 0.0, delta=0.6)
        short = Part.LineSegment(App.Vector(0, 0, 0), App.Vector(5, 0, 0)).toShape()
        with self.assertRaises(ValueError):
            st.along("A LONG TEXT", self.font, 5.0, short)

    def test_a_distance_below_zero_puts_the_text_under_the_line(self):
        line = Part.LineSegment(App.Vector(0, 0, 0), App.Vector(60, 0, 0)).toShape()
        b = box(st.along("TEXT", self.font, 5.0, line, distance=-8.0))
        self.assertAlmostEqual(b.YMin, -8.0, delta=0.05)

    def test_letters_over_each_other_are_one_outline(self):
        apart = st.at_point("HH", self.font, 5.0)
        self.assertEqual(len(apart), 2)
        tight = st.at_point("HH", self.font, 5.0, spacing=-2.0)
        self.assertEqual(len(tight), 1, "no wall inside where they join")
        self.assertTrue(tight[0].isValid())
        self.assertLess(tight[0].Area, sum(f.Area for f in apart))

    def test_letters_are_smooth_walls_with_sharp_corners_only(self):
        """The user: the text must be smooth. A letter's outline is one smooth curve between two of its corners (the
        font gives many short pieces: each was a wall of its own, with a seam and a kink between)."""
        def joins(faces):
            out = []
            for f in faces:
                for w in f.Wires:
                    es = w.OrderedEdges
                    for a, b in zip(es, es[1:] + es[:1]):
                        pa, pb = [v.Point for v in a.Vertexes], [v.Point for v in b.Vertexes]
                        i, j = min(((i, j) for i in range(len(pa)) for j in range(len(pb))),
                                   key=lambda ij: (pa[ij[0]] - pb[ij[1]]).Length)
                        ta = a.tangentAt(a.FirstParameter if i == 0 else a.LastParameter)
                        tb = b.tangentAt(b.FirstParameter if j == 0 else b.LastParameter)
                        ang = math.degrees(ta.getAngle(tb))
                        out.append(min(ang, 180 - ang))
            return out
        raw = sum(len(f.Edges) for ch in "CaO" for f in st._faces(st._wires(ch, self.font, 1.0)[0]))
        faces = st.at_point("CaO", self.font, 5.0)
        self.assertTrue(all(f.isValid() for f in faces))
        self.assertLess(sum(len(f.Edges) for f in faces), raw / 2, "the pieces were not joined")
        self.assertFalse([a for a in joins(faces) if 0.5 < a < 10.0], "a kink inside a smooth stretch")
        o = [f for f in faces if len(f.Wires) == 2][-1]  # the O: two closed round outlines, one curve each
        self.assertEqual([len(w.Edges) for w in o.Wires], [1, 1])
        BOX = [App.Vector(0, 0, 0), App.Vector(40, 0, 0), App.Vector(40, 40, 0), App.Vector(0, 40, 0)]
        fit = st.in_box("CaO", self.font, 5.0, BOX, frame="circle", fit=True)
        self.assertFalse([a for a in joins(fit) if 0.5 < a < 5.0], "fitted in a circle: a kink")

    def test_an_ellipse_frame_holds_the_text_in_the_rectangle_inside_it(self):
        b = box(st.in_box("HEX", self.font, 6.0, self.BOX, halign="left", valign="top", frame="ellipse"))
        self.assertAlmostEqual(b.XMin, 30.0 - 20.0 / math.sqrt(2), delta=0.8)
        self.assertAlmostEqual(b.YMax, 20.0 + 10.0 / math.sqrt(2), delta=0.05)

    def test_fit_in_a_circle_takes_its_shape(self):
        faces = st.in_box("HHHHH", self.font, 1.0, self.BOX, fit=True, frame="circle")
        self.assertTrue(faces and all(f.isValid() for f in faces))
        b = box(faces)
        self.assertAlmostEqual(b.XMax - b.XMin, 20.0 * 0.97, delta=0.3, msg="as wide as the circle (the box's short side)")
        for f in faces:
            for v in f.Vertexes:
                self.assertLessEqual(math.hypot(v.X - 30.0, v.Y - 20.0), 10.0 + 1e-3)
        mid = min(faces, key=lambda f: abs(f.CenterOfMass.x - 30.0)).BoundBox
        end = min(faces, key=lambda f: f.CenterOfMass.x).BoundBox
        self.assertAlmostEqual(mid.YMax - mid.YMin, 20.0, delta=1.0, msg="in the middle the letter is the circle's height")
        self.assertLess(end.YMax - end.YMin, mid.YMax - mid.YMin, "at the side lower")


class TextFeatureTest(unittest.TestCase):
    """The Text feature in a part: a box from two corners (construction, square), read back, following the box when it
    is moved, a rectangle already in the sketch picked as the box, and Extrude raising it."""
    def setUp(self):
        from headless import util
        self.doc = util.new_doc("Tf")
        self.body, self.pad = util.padded_part(self.doc, 10, incad=True)   # 20 x 10 x 10
        top = [i + 1 for i, f in enumerate(self.pad.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-6][0]
        self.sk = self.body.newObject("Sketcher::SketchObject", "Lbl")
        self.sk.AttachmentSupport = [(self.pad, "Face%d" % top)]
        self.sk.MapMode = "FlatFace"
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def make(self, **values):
        from incad.features import sketch_text as T
        return T.create(self.doc, self.body, self.sk, dict(T.defaults(self.doc, self.body, self.sk), **values))

    def test_a_box_from_two_corners_holds_the_text_in_its_middle(self):
        from incad.features import sketch_text as T
        T._pending_box = (App.Vector(2, 2, 0), App.Vector(18, 8, 0))
        f = self.make(text="AB", size="3 mm")
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        self.assertEqual(sum(1 for i in range(self.sk.GeometryCount) if self.sk.getConstruction(i)), 4)
        b = f.Shape.BoundBox
        self.assertAlmostEqual((b.XMin + b.XMax) / 2, 10.0, delta=0.05)
        self.assertAlmostEqual((b.YMin + b.YMax) / 2, 5.0, delta=0.05)
        self.assertAlmostEqual(b.ZMin, 10.0, delta=1e-6, msg="on the face")
        r = T.read(self.doc, f)
        self.assertTrue(r["box"].startswith(self.sk.Name + ":Edge"), r)
        self.assertEqual(T.adjusted(self.doc, f), {"box": r["box"]})
        self.sk.moveGeometry(0, 1, App.Vector(-2, -2, 0))  # the box's corner dragged: the text stays in its middle
        self.doc.recompute()
        b = f.Shape.BoundBox
        self.assertAlmostEqual((b.XMin + b.XMax) / 2, 8.0, delta=0.05)

    def rounds(self, kind):
        return [i for i in range(self.sk.GeometryCount) if self.sk.Geometry[i].TypeId == kind]

    def test_an_ellipse_or_circle_frame_is_drawn_in_the_sketch_and_follows_the_box(self):
        """The user: choosing Ellipse or Circle shows it dashed in the sketch at once; it stays, follows the box's
        corners, and goes again with Rectangle."""
        from incad.features import sketch_text as T
        T._pending_box = (App.Vector(2, 2, 0), App.Vector(18, 8, 0))
        f = self.make(text="AB", size="2 mm", frame="ellipse")
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        e = self.rounds("Part::GeomEllipse")
        self.assertEqual(len(e), 1, "no ellipse in the sketch")
        g = self.sk.Geometry[e[0]]
        self.assertTrue(self.sk.getConstruction(e[0]), "the ellipse is not dashed")
        self.assertAlmostEqual(g.MajorRadius, 8.0, delta=1e-6)
        self.assertAlmostEqual(g.MinorRadius, 3.0, delta=1e-6)
        self.assertAlmostEqual((g.Center - App.Vector(10, 5, 0)).Length, 0.0, delta=1e-6)
        self.sk.moveGeometry(0, 1, App.Vector(0, 0, 0))  # a corner dragged: the ellipse follows, touching the box
        self.doc.recompute()
        g = self.sk.Geometry[self.rounds("Part::GeomEllipse")[0]]
        lo, hi = self.sk.Geometry[0].StartPoint, self.sk.Geometry[2].StartPoint  # the box as the drag left it
        self.assertGreater(hi.x - lo.x, 16.1, "the box did not grow")
        self.assertAlmostEqual(g.MajorRadius, (hi.x - lo.x) / 2, delta=1e-4)
        self.assertAlmostEqual(g.MinorRadius, (hi.y - lo.y) / 2, delta=1e-4)
        self.assertAlmostEqual((g.Center - (lo + hi) * 0.5).Length, 0.0, delta=1e-4)
        T.update(self.doc, f, dict(T.read(self.doc, f), frame="circle"))
        self.assertEqual(self.rounds("Part::GeomEllipse"), [], "the ellipse stayed")
        c = self.rounds("Part::GeomCircle")
        self.assertEqual(len(c), 1, "no circle")
        self.assertAlmostEqual(self.sk.Geometry[c[0]].Radius, (hi.y - lo.y) / 2, delta=1e-4)
        T.update(self.doc, f, dict(T.read(self.doc, f), frame="rect"))
        self.assertEqual(self.rounds("Part::GeomCircle") + self.rounds("Part::GeomEllipse"), [])
        self.assertEqual(self.sk.GeometryCount, 4, "only the box is left")
        self.assertNotIn("Invalid", f.State, f.getStatusString())

    def test_a_tilted_box_of_lines_gets_its_ellipse_tilted(self):
        import Part
        a = math.radians(30)
        u, v = App.Vector(math.cos(a), math.sin(a), 0), App.Vector(-math.sin(a), math.cos(a), 0)
        o = App.Vector(3, 1, 0)
        pts = [o, o + u * 12, o + u * 12 + v * 4, o + v * 4]
        for i in range(4):
            self.sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), True)
        self.doc.recompute()
        f = self.make(text="HE", size="1 mm", box="%s:Edge1" % self.sk.Name, frame="ellipse")
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        g = self.sk.Geometry[self.rounds("Part::GeomEllipse")[0]]
        self.assertAlmostEqual(g.MajorRadius, 6.0, delta=1e-4)
        self.assertAlmostEqual(g.MinorRadius, 2.0, delta=1e-4)
        self.assertAlmostEqual(abs(App.Vector(g.XAxis).normalize().dot(u)), 1.0, delta=1e-6)
        self.assertAlmostEqual((g.Center - (o + u * 6 + v * 2)).Length, 0.0, delta=1e-4)

    def test_a_rectangle_in_the_sketch_is_picked_as_the_box_and_extruded(self):
        import Part
        from incad.features import extrude
        pts = [App.Vector(1, 1, 0), App.Vector(19, 1, 0), App.Vector(19, 9, 0), App.Vector(1, 9, 0)]
        for i in range(4):
            self.sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), True)
        self.doc.recompute()
        f = self.make(text="HE", fit=True, box="%s:Edge2" % self.sk.Name, halign="left", valign="top")
        b = f.Shape.BoundBox
        self.assertNotIn("Invalid", f.State, f.getStatusString())
        self.assertAlmostEqual(b.YMax, 9.0, delta=0.05)
        self.assertAlmostEqual(b.XMin, 1.0, delta=0.05)
        pad = extrude.create(self.doc, self.body, f, dict(extrude.defaults(self.doc, self.body, f), distance="1 mm"))
        self.doc.recompute()
        self.assertNotIn("Invalid", pad.State, pad.getStatusString())
        self.assertGreater(pad.Shape.Volume, self.pad.Shape.Volume + 10)


if __name__ == "__main__":
    unittest.main()
