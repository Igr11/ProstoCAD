"""The new sketch in the old program (incad.sketch_next.convert): a Sketcher sketch read into the new sketch's data and
written back. Unchanged: nothing written. Written back: the same geometry, geometry ids, dimension names and formulas,
and the part built on it the same. Changed in the new sketch's data: the part follows, its features keep their
edges (a fillet on the pad's edge stays valid)."""
import json
import math
import os
import unittest

import FreeCAD as App
import Part
import Sketcher

from incad.sketch_next import convert

V = App.Vector


def sound(test, sk, why=""):
    """Solved, and nothing in it redundant or conflicting (solve() alone gives 0 with redundant constraints)."""
    test.assertEqual(sk.getStatusString(), "Valid", why)
    test.assertEqual([sk.solve(), sk.solve()], [0, 0], why)  # (a hair off, the first finds nothing redundant)
    for name in ("RedundantConstraints", "ConflictingConstraints"):
        test.assertEqual(list(getattr(sk, name)), [], "%s %s" % (why, name))


class SketchNextConvertTest(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("Skn")
        self.body = self.doc.addObject("PartDesign::Body", "Body")
        sk = self.sk = self.body.newObject("Sketcher::SketchObject", "Sketch")
        sk.AttachmentSupport = [(self.body.Origin.OriginFeatures[3], "")]  # XY plane
        sk.MapMode = "FlatFace"
        g = sk.addGeometry([Part.LineSegment(V(0, 0, 0), V(40, 0, 0)), Part.LineSegment(V(40, 0, 0), V(40, 20, 0)),
                            Part.LineSegment(V(40, 20, 0), V(0, 20, 0)), Part.LineSegment(V(0, 20, 0), V(0, 0, 0)),
                            Part.Circle(V(20, 10, 0), V(0, 0, 1), 4)])
        C = Sketcher.Constraint
        sk.addConstraint([C("Coincident", 0, 2, 1, 1), C("Coincident", 1, 2, 2, 1), C("Coincident", 2, 2, 3, 1),
                          C("Coincident", 3, 2, 0, 1), C("Coincident", 0, 1, -1, 1), C("Horizontal", 0), C("Horizontal", 2),
                          C("Vertical", 1), C("Vertical", 3), C("Distance", 0, 40.0), C("Distance", 1, 20.0),
                          C("Diameter", g[4], 8.0), C("DistanceX", -1, 1, g[4], 3, 20.0), C("DistanceY", -1, 1, g[4], 3, 10.0)])
        for i, name in ((9, "d0"), (10, "d1"), (11, "d2"), (12, "d3"), (13, "d4")):
            sk.renameConstraint(i, name)
        sk.setExpression("Constraints.d4", "Sketch.Constraints.d1 / 2")
        self.pad = self.body.newObject("PartDesign::Pad", "Pad")
        self.pad.Profile = sk
        self.pad.Length = 5
        self.doc.recompute()
        top = max(range(len(self.pad.Shape.Edges)), key=lambda i: self.pad.Shape.Edges[i].BoundBox.ZMin
                  - abs(self.pad.Shape.Edges[i].BoundBox.XMax - 40))  # an edge on the right-hand top
        self.fillet = self.body.newObject("PartDesign::Fillet", "Fillet")
        self.fillet.Base = (self.pad, ["Edge%d" % (top + 1)])
        self.fillet.Radius = 1
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def state(self):
        sk = self.sk
        ids = [sk.getGeometryId(i) for i in range(sk.GeometryCount)]
        names = sorted((c.Name, c.Type) for c in sk.Constraints if c.Name)
        return ids, names, sorted(sk.ExpressionEngine)

    def test_unchanged_writes_nothing(self):
        data, _rep = convert.read(self.sk)
        before = self.sk.Content
        self.assertFalse(convert.write(self.sk, data)["changed"])
        self.assertEqual(self.sk.Content, before)

    def test_written_back_is_the_same(self):
        before = self.state()
        volume = self.fillet.Shape.Volume
        data, rep = convert.read(self.sk)
        self.assertEqual(rep.as_dict()["constraints"] and [c for c in rep.as_dict()["constraints"] if c["verdict"] == "missing"], [])
        convert.write(self.sk, data, force=True)
        self.doc.recompute()
        self.assertEqual(self.sk.solve(), 0)
        self.assertEqual(self.state(), before)
        self.assertTrue(self.fillet.Shape.isValid())
        self.assertAlmostEqual(self.fillet.Shape.Volume, volume, places=6)

    def test_a_wider_sketch_keeps_the_fillet_on_its_edge(self):
        data, _rep = convert.read(self.sk)
        for t in data["ties"]:
            if t.get("name") == "d0":
                t["ref"] = 50.0  # as if typed in the new sketch: its solver moved the right side out
        for k, (x, y) in enumerate(data["pts"]):
            if abs(x - 40) < 1e-9:
                data["pts"][k] = (50.0, y)
        convert.write(self.sk, data)
        self.doc.recompute()
        self.assertEqual(self.sk.solve(), 0)
        self.assertAlmostEqual(self.sk.getDatum("d0").Value, 50.0, places=6)
        self.assertEqual(self.state()[0], [self.sk.getGeometryId(i) for i in range(5)])
        self.assertTrue(self.fillet.isValid() and self.fillet.Shape.isValid(), self.fillet.State)
        self.assertAlmostEqual(self.pad.Shape.BoundBox.XMax, 50.0, places=6)
        from incad.core import model  # FreeCAD may write the path shorter (".Constraints.d1"): the same formula
        self.assertEqual([(p, model.to_display(self.doc, e)) for p, e in self.state()[2]], [(".Constraints.d4", "d1 / 2")])

    def test_axes_and_origin_ties(self):
        """The new sketch's constraints with the X / Y axes and the origin (2026-11-24, from the new program): a line on
        the X axis, a circle's centre in the origin, a circle touching the X axis, a line square to it: written as
        Sketcher constraints with H_Axis / V_Axis, read back as the same ties."""
        sk = self.body.newObject("Sketcher::SketchObject", "Axes")
        sk.AttachmentSupport = [(self.body.Origin.OriginFeatures[3], "")]
        sk.MapMode = "FlatFace"
        sk.addGeometry([Part.LineSegment(V(10, 0, 0), V(30, 0, 0)), Part.Circle(V(0, 0, 0), V(0, 0, 1), 4),
                        Part.Circle(V(50, 4, 0), V(0, 0, 1), 4), Part.LineSegment(V(60, 10, 0), V(60, 20, 0))])
        self.doc.recompute()
        data, _rep = convert.read(sk)
        P = lambda x, y: min(range(len(data["pts"])), key=lambda i: (data["pts"][i][0] - x) ** 2 + (data["pts"][i][1] - y) ** 2)
        tie = lambda kind, *pts, ref=None: dict(kind=kind, pts=list(pts), ref=ref)
        c2 = next(a for a in data["arcs"] if abs(data["pts"][a[0]][0] - 50) < 1e-6)
        data["ties"] += [tie("y0", P(10, 0)), tie("y0", P(30, 0)), tie("x0", P(0, 0)), tie("y0", P(0, 0)),
                         tie("con_axis", c2[0], c2[1], ref="x"), tie("same_x", P(60, 10), P(60, 20))]
        convert.write(sk, data)
        self.doc.recompute()
        sound(self, sk)
        on_axis = [(c.First, c.Second) for c in sk.Constraints if c.Type == "PointOnObject" and c.Second in (-1, -2)]
        self.assertEqual(len(on_axis), 4, sk.Constraints)
        self.assertTrue(any(c.Type == "Tangent" and -1 in (c.First, c.Second) for c in sk.Constraints))
        self.assertTrue(any(c.Type == "Vertical" for c in sk.Constraints))
        back, _rep = convert.read(sk)
        kinds = sorted(t["kind"] for t in back["ties"] if t["kind"] in ("x0", "y0", "con_axis", "same_x"))
        self.assertEqual(kinds, ["con_axis", "same_x", "x0", "y0", "y0", "y0"])


class ButtonsTest(unittest.TestCase):
    """What the new sketch's own constraint button made (data/sketch_next_buttons.json: rectangles and slots, 46 runs):
    all of it is written, sound, and read back (the user, 2026-11-24: "redundant constraints", the sketch not written)."""

    def make(self, data):
        self.doc = App.newDocument("Buttons")
        body = self.doc.addObject("PartDesign::Body", "Body")
        sk = body.newObject("Sketcher::SketchObject", "Sk")
        self.doc.recompute()
        convert.write(sk, data)
        self.doc.recompute()
        return sk

    @staticmethod
    def cases():
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sketch_next_buttons.json"), encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def places(data):
        return sorted((round(x, 5) + 0.0, round(y, 5) + 0.0) for x, y in data["pts"])

    def test_every_run_is_written_and_comes_back(self):
        for case in self.cases():
            with self.subTest(case["name"]):
                data = case["data"]
                sk = self.make(data)
                try:
                    sound(self, sk, case["name"])
                    back, _rep = convert.read(sk)
                    self.assertEqual(self.places(back), self.places(data))
                    at0 = lambda d, t: t["kind"] in ("x0", "y0") and math.hypot(*d["pts"][t["pts"][0]]) < 1e-6  # held there
                    for kind in ("mid", "con_col", "con_par", "con_eq", "x0", "y0"):
                        n = lambda d: sum(t["kind"] == kind and not at0(d, t) for t in d["ties"])
                        self.assertEqual(n(back), n(data), kind)
                    self.assertFalse(convert.write(sk, back)["changed"])
                    ids = [sk.getGeometryId(i) for i in range(sk.GeometryCount)]
                    convert.write(sk, back, force=True)
                    self.doc.recompute()
                    sound(self, sk, case["name"] + ", again")
                    self.assertEqual([sk.getGeometryId(i) for i in range(sk.GeometryCount)], ids)
                    again, _rep = convert.read(sk)
                    self.assertTrue(convert._same(again, back), case["name"])
                finally:
                    App.closeDocument(self.doc.Name)

    def case(self, name):
        return next(c["data"] for c in self.cases() if c["name"] == name)

    def test_a_constraint_left_out_holds_again(self):
        """Collinear with the axis beside Horizontal: one of them is kept beside the sketch; the other taken away, it
        is written again; and it is found after a size changed outside (by the vertices, not by the places)."""
        sk = self.make(self.case("Horizontal, Collinear with X axis"))
        try:
            data, _rep = convert.read(sk)
            n = sk.ConstraintCount
            data["ties"] = [t for t in data["ties"] if t["kind"] != "same_y"]
            convert.write(sk, data)
            self.doc.recompute()
            sound(self, sk)
            self.assertEqual(sum(c.Type == "PointOnObject" for c in sk.Constraints), 2, n)
        finally:
            App.closeDocument(self.doc.Name)
        sk = self.make(self.case("Parallel, then Collinear"))
        try:
            i = sk.addConstraint(Sketcher.Constraint("Distance", 0, 15.0))
            sk.delConstraint(i)
            geo = sk.Geometry
            sk.moveGeometry(0, 2, V(17, 6, 0)) if hasattr(sk, "moveGeometry") else sk.movePoint(0, 2, V(17, 6, 0))
            self.doc.recompute()
            back, _rep = convert.read(sk)
            self.assertEqual(sorted(t["kind"] for t in back["ties"] if t["kind"] in ("con_par", "con_col")), ["con_col", "con_par"])
        finally:
            App.closeDocument(self.doc.Name)

    def test_a_dimension_too_many_is_not_taken_out(self):
        data = self.case("control: rect")
        e = lambda a, b: dict(kind="dim_len", pts=[a, b], ref=abs(data["pts"][a][0] - data["pts"][b][0]), mark=None, off=0.0, name="d%d" % a)
        data["ties"] += [e(0, 1), e(2, 3)]
        sk = self.make(data)
        try:
            self.assertEqual(sorted(c.Name for c in sk.Constraints if c.Type == "Distance"), ["d0", "d2"])
            self.assertNotEqual(sk.getStatusString(), "Valid")
        finally:
            App.closeDocument(self.doc.Name)

    def test_the_middle_is_a_construction_point(self):
        """As 0.17.5's rectangle centre: a Point, Symmetric between the diagonal's ends; deleted with its tie."""
        sk = self.make(self.case("control: rect"))
        try:
            points = [i for i, g in enumerate(sk.Geometry) if g.TypeId == "Part::GeomPoint"]
            self.assertEqual(len(points), 1)
            self.assertTrue(sk.getConstruction(points[0]))
            data, _rep = convert.read(sk)
            self.assertFalse(any(t["kind"] in ("sk_point", "con_sym_pt") for t in data["ties"]))
            k = next(t["pts"][0] for t in data["ties"] if t["kind"] == "mid")
            data["ties"] = [t for t in data["ties"] if t["kind"] != "mid"]
            self.assertEqual(k, len(data["pts"]) - 1)
            data["pts"] = data["pts"][:-1]
            convert.write(sk, data)
            self.doc.recompute()
            sound(self, sk)
            self.assertFalse(any(g.TypeId == "Part::GeomPoint" for g in sk.Geometry))
        finally:
            App.closeDocument(self.doc.Name)

    def test_an_old_rectangle_centre_stays_a_point(self):
        """A file of 0.17.5: its centre point and its Symmetric are read as before."""
        self.doc = App.newDocument("Old")
        body = self.doc.addObject("PartDesign::Body", "Body")
        sk = body.newObject("Sketcher::SketchObject", "Sk")
        sk.addGeometry([Part.LineSegment(V(0, 1, 0), V(10, 7, 0))])
        sk.addGeometry(Part.Point(V(5, 4, 0)), True)
        sk.addConstraint(Sketcher.Constraint("Symmetric", 0, 1, 0, 2, 1, 1))
        self.doc.recompute()
        try:
            data, _rep = convert.read(sk)
            self.assertEqual(sorted(t["kind"] for t in data["ties"]), ["con_sym_pt", "sk_point"])
        finally:
            App.closeDocument(self.doc.Name)


class ProjectedEdgesTest(unittest.TestCase):
    """A sketch on the pad's top face with two of its edges projected (one normal, one construction) and its own line
    tied to a projected corner: read as projected shapes, written back as they were; a projection taken away in the
    new sketch goes (delExternal), one switched to construction is remembered as the user's choice."""

    def setUp(self):
        self.doc = App.newDocument("SknExt")
        body = self.doc.addObject("PartDesign::Body", "Body")
        base = body.newObject("Sketcher::SketchObject", "Base")
        base.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]
        base.MapMode = "FlatFace"
        base.addGeometry([Part.LineSegment(V(0, 0, 0), V(40, 0, 0)), Part.LineSegment(V(40, 0, 0), V(40, 20, 0)),
                          Part.LineSegment(V(40, 20, 0), V(0, 20, 0)), Part.LineSegment(V(0, 20, 0), V(0, 0, 0))])
        C = Sketcher.Constraint
        base.addConstraint([C("Coincident", 0, 2, 1, 1), C("Coincident", 1, 2, 2, 1), C("Coincident", 2, 2, 3, 1),
                            C("Coincident", 3, 2, 0, 1)])
        pad = body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = base
        pad.Length = 5
        self.doc.recompute()
        top = next(i for i, f in enumerate(pad.Shape.Faces) if abs(f.BoundBox.ZMin - 5) < 1e-9 and abs(f.BoundBox.ZMax - 5) < 1e-9)
        edges = [i for i, e in enumerate(pad.Shape.Edges) if abs(e.BoundBox.ZMin - 5) < 1e-9 and abs(e.BoundBox.ZMax - 5) < 1e-9]
        sk = self.sk = body.newObject("Sketcher::SketchObject", "Sketch")
        sk.AttachmentSupport = [(pad, "Face%d" % (top + 1))]
        sk.MapMode = "FlatFace"
        self.doc.recompute()
        sk.addExternal("Pad", "Edge%d" % (edges[0] + 1), True)   # normal (part of the profile)
        sk.addExternal("Pad", "Edge%d" % (edges[1] + 1), False)  # construction
        self.doc.recompute()
        corner = sk.getPoint(-3, 1)
        g = sk.addGeometry(Part.LineSegment(corner, corner + V(7, 3, 0)))
        sk.addConstraint([C("Coincident", g, 1, -3, 1), C("Distance", g, 7.5)])
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_read_and_written_back(self):
        data, rep = convert.read(self.sk)
        ids = data["shape_ids"]["line"]
        self.assertEqual(sorted(i for i in ids if isinstance(i, str)), ["ext:-3", "ext:-4"])
        self.assertEqual([c for c in rep.as_dict()["constraints"] if c["verdict"] != "mapped"], [])
        self.assertEqual([data["constr"][ids.index(r)] for r in ("ext:-3", "ext:-4")], [False, True])
        own = ids.index(self.sk.getGeometryId(0))
        ext = ids.index("ext:-3")
        self.assertIn(data["lines"][own][0], data["lines"][ext])  # the own line's start is the projected corner
        before = self.sk.Content
        self.assertFalse(convert.write(self.sk, data)["changed"])
        self.assertEqual(self.sk.Content, before)
        n_con = self.sk.ConstraintCount
        convert.write(self.sk, data, force=True)
        self.doc.recompute()
        self.assertEqual(self.sk.solve(), 0)
        self.assertEqual((self.sk.ConstraintCount, len(self.sk.ExternalGeo)), (n_con, 4))
        self.assertEqual(sorted((c.Type, c.First, c.Second) for c in self.sk.Constraints),
                         sorted([("Coincident", 0, -3), ("Distance", 0, -2000)]))

    def test_projection_taken_away(self):
        data, _rep = convert.read(self.sk)
        ids = data["shape_ids"]["line"]
        gone = ids.index("ext:-4")
        for key in ("lines", "constr"):
            data[key] = [x for i, x in enumerate(data[key]) if i != gone]
        data["shape_ids"]["line"] = [x for i, x in enumerate(ids) if i != gone]
        convert.write(self.sk, data)
        self.doc.recompute()
        self.assertEqual(len(self.sk.ExternalGeo), 3)
        self.assertEqual(self.sk.solve(), 0)
        self.assertIn(("Coincident", 0, -3), [(c.Type, c.First, c.Second) for c in self.sk.Constraints])

    def test_switched_to_construction(self):
        from incad.core import sketch as sketches
        data, _rep = convert.read(self.sk)
        data["constr"][data["shape_ids"]["line"].index("ext:-3")] = True
        convert.write(self.sk, data)
        self.assertEqual(sketches._projected_flags(self.sk)[-3][0], False)
        self.assertEqual(len(getattr(self.sk, sketches.CHOSEN_PROP, [])), 1)


if __name__ == "__main__":
    unittest.main()
