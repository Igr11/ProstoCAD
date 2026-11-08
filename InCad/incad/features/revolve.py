"""Revolve (Inventor): ProstoCAD's own feature on OpenCASCADE (RevolveFeature, the geometry in
incad.core.revolve_solid), a Join or a Cut, around an axis chosen one of five ways: the sketch's vertical or
horizontal axis, a line of the sketch (construction, projected or normal: it keeps its kind and is no part of the
profile), a line / axis / edge picked in the view (a line of the sketch, an origin or work axis, a straight edge of
the solid), or two points clicked on the part — a hidden Work Axis through them, owned by the revolve (shown under it,
deleted with it). New Solid: the revolve in a Solid of its own, for a Boolean later (as Extrude's). Files made before
it hold FreeCAD's Revolution (Join) / Groove (Cut): they open and compute as they are, and become ours when the
dialog changes them (`convert`)."""
import json

import FreeCAD as App

from incad.features.common import Field, format_picks, parse_picks, profile_of, value_param, value_text

TITLE = "Revolve"
ICON = "revolve"
JOIN, CUT = "PartDesign::FeatureAdditivePython", "PartDesign::FeatureSubtractivePython"  # ProstoCAD's own Revolve
LEGACY = ("PartDesign::Revolution", "PartDesign::Groove")  # FreeCAD's, in files made before it
TYPES = LEGACY + (JOIN, CUT)
MARK = "InCadRevolve"  # on ProstoCAD's own Revolve: how its last result was made
PROPS = {"angle": "Angle", "angle2": "Angle2"}
HELPER = "InCadHelperOf"      # on the Work Axis InCad makes for two points: the revolve it belongs to
POINTS = "InCadAxisPoints"    # on that axis: the two points as typed into the field ("x y z;x y z")
AXIS_MODES = (("reference", "Line, axis or edge"), ("construction", "Line of the sketch"),
              ("two_points", "Two points"), ("V_Axis", "Sketch vertical"), ("H_Axis", "Sketch horizontal"))
FIELDS = (
    Field("operation", "Operation", "choice", (("join", "Join"), ("cut", "Cut"), ("new_solid", "New Solid")), "join"),
    Field("axis_mode", "Axis", "combo", AXIS_MODES, "reference"),
    Field("axis_construction", "Line", "combo", (), "", show_if=("axis_mode", ("construction",))),
    Field("axis_ref", "Pick", "pick", pick="axis", show_if=("axis_mode", ("reference",))),
    Field("axis_points", "Points", "pick", pick="two_points", show_if=("axis_mode", ("two_points",))),
    Field("extent", "Extents", "choice", (("full", "Full"), ("angle", "Angle"), ("two_angles", "Two angles")), "full"),
    Field("angle", "Angle", "value_angle", default="90 °", show_if=("extent", ("angle", "two_angles"))),
    Field("angle2", "Angle 2", "value_angle", default="90 °", show_if=("extent", ("two_angles",))),
    Field("flip", "Flip", "bool", default=False, show_if=("extent", ("angle",))),
)



def claims(f):
    return f.TypeId in LEGACY or MARK in getattr(f, "PropertiesList", ())


def is_own(f):
    """ProstoCAD's own Revolve (not a Revolution / Groove of an older file)."""
    return f is not None and MARK in getattr(f, "PropertiesList", ())


def is_cut(f):
    return f is not None and f.TypeId in ("PartDesign::Groove", CUT)


class RevolveFeature:
    """ProstoCAD's Revolve on OpenCASCADE (incad.core.revolve_solid): Revolution's properties (Profile,
    ReferenceAxis, Type, Angle, Angle2, Midplane, Reversed; Base and Axis: the axis as last computed), so the dialog
    reads and writes it as it did a Revolution or Groove; a Join is a PartDesign additive feature, a Cut a
    subtractive one (patterns repeat them). The proxy; FreeCAD saves its module path and class name in the file."""
    KINDS = ["Angle", "TwoAngles"]

    def __init__(self, obj):
        for name, kind, doc in (("Profile", "App::PropertyLinkSub", "The sketch revolved"),
                                ("ReferenceAxis", "App::PropertyLinkSub", "The axis it turns about"),
                                ("Type", "App::PropertyEnumeration", "One angle, or two (one each way)"),
                                ("Angle", "App::PropertyAngle", "How far it turns"),
                                ("Angle2", "App::PropertyAngle", "How far it turns the other way (Two angles)"),
                                ("Midplane", "App::PropertyBool", "Half the angle each way"),
                                ("Reversed", "App::PropertyBool", "Turns the other way"),
                                ("Base", "App::PropertyVector", "A point on the axis (computed)"),
                                ("Axis", "App::PropertyVector", "The axis' direction (computed)"),
                                (MARK, "App::PropertyString", "How the result was made")):
            if name not in obj.PropertiesList:
                obj.addProperty(kind, name, "Revolve", doc)
        obj.Type = self.KINDS
        obj.Type = "Angle"
        obj.Angle, obj.Angle2 = 360.0, 90.0
        for name in ("Base", "Axis", MARK):
            obj.setPropertyStatus(name, "Hidden")
        obj.Proxy = self

    def execute(self, obj):
        from incad.core import revolve_solid
        sk = profile_of(obj)
        if sk is None:
            raise ValueError("the Revolve has no profile")
        from incad.core import regions
        point, d = axis_line(obj)
        faces = regions.faces_of(sk, lambda shape: revolve_solid.profile_faces(shape, point, d))
        obj.Base, obj.Axis = point, d
        turn = -d if obj.Reversed else d
        angle, start = obj.Angle.Value, 0.0
        if obj.Type == "TwoAngles":
            angle, start = angle + obj.Angle2.Value, -obj.Angle2.Value
            if angle > 360 + 1e-9:
                raise ValueError("the two angles add up to %g°, more than a full turn: make them 360° together at "
                                 "most (or choose Full)" % angle)
        elif obj.Midplane:
            start = -angle / 2
        tool = revolve_solid.tool(faces, point, turn, angle, start)
        obj.AddSubShape = tool  # (first: a thread it crosses is found by it, incad.core.thread_order)
        base = obj.BaseFeature.Shape if obj.BaseFeature is not None else None
        base = base if base is not None and not base.isNull() and base.Solids else None
        from incad.core import thread_order
        thread = thread_order.crossed_thread(obj) if base is not None else None
        if thread is not None:  # the Boolean through its helical faces fails, slowly (17 s on the user's p2_test)
            raise ValueError("the revolve crosses %s's modeled thread: it goes before the thread in the tree"
                             % thread.Label)
        shape, how = revolve_solid.combine(base, tool, "cut" if is_cut(obj) else "join",
                                           bool(getattr(obj, "Refine", False)))
        setattr(obj, MARK, how)
        obj.Shape = shape

    def dumps(self):
        return None

    def loads(self, state):
        return None


def axis_line(f):
    """(point, unit direction) of f's ReferenceAxis in its Solid's coordinates, as FreeCAD's Revolution takes it:
    a sketch's V / H axis (its Y / X through its origin), its construction line N (AxisN, start to end), an edge
    (a line of a sketch, a straight edge of the part), a work axis (its Z) or an origin axis (its X)."""
    from incad.core import revolve_solid
    link = f.ReferenceAxis
    if not link or link[0] is None:
        raise ValueError("Pick the axis to revolve about")
    obj, subs = link[0], [s for s in (link[1] or []) if s]
    sub = subs[0] if subs else ""
    if obj.TypeId == "Sketcher::SketchObject" and (sub in ("V_Axis", "H_Axis") or sub.startswith("Axis")):
        pl = obj.Placement
        if sub in ("V_Axis", "H_Axis"):
            a, b = App.Vector(), App.Vector(0, 1, 0) if sub == "V_Axis" else App.Vector(1, 0, 0)
        else:
            n = int(sub[4:] or 0)
            lines = [g for i, g in enumerate(obj.Geometry)
                     if g.TypeId == "Part::GeomLineSegment" and obj.getConstruction(i)]
            if n >= len(lines):
                raise ValueError("That line is no longer in the sketch: pick another axis")
            a, b = lines[n].StartPoint, lines[n].EndPoint
        return revolve_solid.axis_of_line(pl.multVec(a), pl.multVec(b))
    if sub.startswith("Edge"):
        try:
            edge = obj.Shape.getElement(sub)
        except Exception:
            raise ValueError("The axis (%s of %s) is no longer there: pick it again" % (sub, obj.Label))
        return revolve_solid.axis_of_edge(edge)
    if obj.TypeId in ("App::Line", "PartDesign::Line"):
        return revolve_solid.axis_of_object(obj)
    raise ValueError("Pick a straight line, an edge or an axis for the axis")


def _new(container, operation, name="Revolve"):
    """A new own Revolve in the Solid `container`."""
    from incad.core import document
    tip = getattr(container, "Tip", None)
    tip = tip if getattr(tip, "TypeId", "").startswith("PartDesign::") else None  # a solid step's look, not a sketch's
    f = container.newObject(_type(operation), name)
    RevolveFeature(f)
    document.show_like(f, tip)  # (a Python feature made from code draws nothing without a view provider)
    return f

# ----- construction lines of the sketch (FreeCAD names them Axis0, Axis1 … in sketch order) -----
def construction_lines(sk):
    """[(\"AxisN\", label)] for the sketch's construction lines."""
    out = []
    if sk is None:
        return out
    for i, g in enumerate(sk.Geometry):
        if g.TypeId != "Part::GeomLineSegment" or not sk.getConstruction(i):
            continue
        d = g.EndPoint - g.StartPoint
        length = d.Length
        if abs(d.x) < 1e-6 * max(1.0, length):
            direction = "vertical"
        elif abs(d.y) < 1e-6 * max(1.0, length):
            direction = "horizontal"
        else:
            import math
            direction = "at %.0f°" % (math.degrees(math.atan2(abs(d.y), abs(d.x))))
        n = len(out)  # (the value: FreeCAD's construction-line number; the label: the line's own number, as below)
        out.append(("Axis%d" % n, "Line %d · %s, %s (construction)" % (i + 1, _mm(length), direction)))
    return out


def _projected_line_items(sk):
    """[("extK", label, (global start, global end))] for straight PROJECTED edges that lie IN the sketch plane.
    A projection is in the plane by definition, so it is a valid in-plane revolve axis — each resolves to a helper
    line through the projection's own endpoints, NOT the 3D source edge (whose direction can be perpendicular to
    the plane; such an edge projects to a point and is skipped). Each item also carries its geo id."""
    out = []
    if sk is None:
        return out
    try:
        geos = list(sk.ExternalGeo)       # the external geometry AS PROJECTED, in the sketch's local frame
        pl = sk.getGlobalPlacement()
    except Exception:
        return out
    k = 0
    for i, g in enumerate(geos):
        if i < 2:                          # ExternalGeo[0], [1] are the sketch's own H and V axes
            continue
        if getattr(g, "TypeId", "") != "Part::GeomLineSegment":
            continue
        try:
            length = (g.EndPoint - g.StartPoint).Length
            if length < 1e-6:              # a perpendicular edge projects to a point: no axis
                continue
            g0, g1 = pl.multVec(g.StartPoint), pl.multVec(g.EndPoint)
        except Exception:
            continue
        out.append(("ext%d" % k, "Projected line · %s" % _mm(length), (g0, g1), -i - 1))
        k += 1
    return out


def projected_lines(sk):
    """[("extK", label, (global start, global end))] for straight projected edges in the sketch plane (see
    _projected_line_items)."""
    return [item[:3] for item in _projected_line_items(sk)]


def _construction_value(sk, i):
    """"AxisK" of construction line geometry i (FreeCAD numbers the construction lines in sketch order)."""
    k = sum(1 for j, g in enumerate(sk.Geometry[:i])
            if g.TypeId == "Part::GeomLineSegment" and sk.getConstruction(j))
    return "Axis%d" % k


def _normal_axis(sk, wanted):
    """Normal line "LineN" as the axis, referenced by its edge: it stays normal (the user's law: Revolve about any
    line of the sketch, whatever its kind). One that closes no outline is no part of the profile
    (incad.core.revolve_solid.profile_faces). A line made construction before is found again under its old name."""
    i = int(wanted[4:]) - 1
    if not 0 <= i < len(sk.Geometry) or sk.Geometry[i].TypeId != "Part::GeomLineSegment":
        raise ValueError("That line is no longer in the sketch: pick another axis")
    if sk.getConstruction(i):
        return [_construction_value(sk, i)]
    return [_shape_edge(sk, wanted)]


def lines_sketch(profile):
    """The sketch the user drew: an area copy (a sketch whose lines cross, incad.core.regions) holds only the area's
    outline, its source has the lines to revolve about."""
    from incad.core import regions
    if profile is not None and regions.is_profile(profile):
        return regions.source_of(profile) or profile
    return profile


def normal_lines(sk):
    """[("LineN", label)] for the sketch's normal straight lines (N: the geometry's number): a side of the profile is
    an axis too (the user's law: Revolve works about any line of the sketch; normal and construction lines have
    equal rights)."""
    out = []
    if sk is None:
        return out
    for i, g in enumerate(sk.Geometry):
        if g.TypeId != "Part::GeomLineSegment" or sk.getConstruction(i):
            continue
        out.append(("Line%d" % (i + 1), "Line %d · %s" % (i + 1, _mm((g.EndPoint - g.StartPoint).Length))))
    return out


def _shape_edge(sk, value):
    """The sketch Shape's "EdgeK" for normal line "LineN": FreeCAD numbers a sketch's edges over its normal
    geometry only (construction left out), so the geometry's number is no edge name. Found by the line's ends."""
    g = sk.Geometry[int(value[4:]) - 1]
    pl = sk.Placement
    a, b = pl.multVec(g.StartPoint), pl.multVec(g.EndPoint)
    for k, e in enumerate(sk.Shape.Edges):
        ends = [v.Point for v in e.Vertexes]
        if len(ends) == 2 and any((ends[0] - p).Length < 1e-6 and (ends[1] - q).Length < 1e-6
                                  for p, q in ((a, b), (b, a))):
            return "Edge%d" % (k + 1)
    raise ValueError("That line is no longer in the sketch: pick another axis")


def axis_line_options(profile):
    """Choices for the Line of the sketch drop-down: the sketch's construction lines, its projected straight edges,
    then its normal lines (of the sketch drawn, also when an area of it is the profile)."""
    sk = lines_sketch(profile)
    return [(v, lbl) for v, lbl in construction_lines(sk)] + [(v, lbl) for v, lbl, _ in projected_lines(sk)] + \
        normal_lines(sk)


def axis_name(sub):
    """FreeCAD stores the first construction line as 'Axis' (index 0 dropped): the listed name is 'Axis0'."""
    return "Axis0" if sub == "Axis" else sub


def _mm(length):
    text = "%.2f" % length
    text = text.rstrip("0").rstrip(".") if "." in text else text
    return text + " mm"


def options(key, values, profile=None):
    if key == "axis_construction":
        return axis_line_options(profile)
    return None


def defaults(doc, body, profile):
    """The axis: the one construction / projected line the sketch has (drawn or projected for it), else a sketch
    axis the profile lies beside (vertical first), else none: the dialog waits for a click on the axis instead of
    starting with an axis through the profile."""
    values = {f.key: f.default for f in FIELDS}
    lines = axis_line_options(profile)
    if lines:
        values["axis_construction"] = lines[0][0]
        if len(lines) - len(normal_lines(lines_sketch(profile))) == 1:
            values["axis_mode"] = "construction"
            return values
    side = _beside_sketch_axis(profile)
    if side is not None:
        values["axis_mode"] = side
    return values


def _beside_sketch_axis(profile):
    """"V_Axis" or "H_Axis" when the profile lies on one side of it (touching it is fine), else None."""
    sk = lines_sketch(profile)
    shape = getattr(sk, "Shape", None)
    if shape is None or shape.isNull() or not shape.Edges:
        return None
    flat = shape.copy()
    flat.transformShape(sk.Placement.inverse().toMatrix())  # (the sketch's shape sits at its placement)
    box = flat.BoundBox
    eps = 1e-6
    if box.XMin > -eps or box.XMax < eps:
        return "V_Axis"
    if box.YMin > -eps or box.YMax < eps:
        return "H_Axis"
    return None


# ----- the axis -----
def parse_points(text):
    pts = []
    for item in (text or "").split(";"):
        parts = item.replace(",", " ").split()
        if len(parts) == 3:
            pts.append(App.Vector(*(float(v) for v in parts)))
    return pts


def format_points(points):
    return ";".join("%g %g %g" % (p.x, p.y, p.z) for p in points)


def axis_helper(f):
    body = f.getParentGeoFeatureGroup()
    for o in (body.Group if body is not None else []):
        if o.TypeId == "PartDesign::Line" and getattr(o, HELPER, None) == f.Name:
            return o
    return None


def project_to_plane(points, profile):
    """Project the picked points onto the profile's plane, so the axis always lies IN the plane (a revolve axis
    must). The points can be clicked anywhere on the solid; what matters is the line they mark in the plane."""
    try:
        pl = profile.getGlobalPlacement()
        origin = pl.Base
        normal = pl.Rotation.multVec(App.Vector(0, 0, 1))
        normal.normalize()
    except Exception:
        return points
    out = []
    for p in points:
        v = App.Vector(p)
        offset = (v - origin).dot(normal)
        out.append(v if abs(offset) < 1e-7 else v - normal * offset)  # already in the plane: leave it exactly
    return out


LINE = "InCadAxisLine"       # on that axis when it runs along a projected line: the line's geometry id


def _new_helper(doc, f):
    line = axis_helper(f)
    if line is None:
        body = f.getParentGeoFeatureGroup()
        line = doc.addObject("PartDesign::Line", "RevolveAxis")
        body.insertObject(line, f, False)  # before the revolve: a feature never references a later object
        line.Label = "Revolve axis"
        line.addProperty("App::PropertyString", HELPER, "InCad", "The revolve this axis belongs to")
        line.setPropertyStatus(HELPER, "Hidden")
        line.addProperty("App::PropertyString", POINTS, "InCad", "The two points the axis goes through")
        line.setPropertyStatus(POINTS, "Hidden")
        setattr(line, HELPER, f.Name)
    line.MapMode = "Deactivated"
    line.Visibility = False
    return line


def _place_helper(doc, f, points):
    a, b = points
    d = b - a
    if d.Length < 1e-6:
        raise ValueError("The two points must be different")
    d.normalize()
    line = _new_helper(doc, f)
    if "Placement" in dict(line.ExpressionEngine):
        line.setExpression("Placement", None)   # was along a projected line: now fixed at the two points
    if hasattr(line, LINE):
        setattr(line, LINE, 0)
    line.Placement = App.Placement(a, App.Rotation(App.Vector(0, 0, 1), d))  # a datum line runs along its Z
    setattr(line, POINTS, format_points(points))
    line.Visibility = False
    return line


def _line_helper(doc, f, sk, geo):
    """The axis along projected line `geo` (a negative geometry id) of sketch `sk`: a hidden datum line whose
    Placement is a FORMULA of the projection's own ends, so it moves with the projected edge on every recompute
    (the part grows, the edge moves, the revolve follows), as an axis on a drawn line does. FreeCAD can't attach a
    datum or a revolve to a sketch's external edge directly ("Invalid shape name ExternalEdge1")."""
    line = _new_helper(doc, f)
    if not hasattr(line, LINE):
        line.addProperty("App::PropertyInteger", LINE, "InCad", "The projected line of the sketch the axis runs along")
        line.setPropertyStatus(LINE, "Hidden")
    setattr(line, LINE, geo)
    g = "%s.ExternalGeo[%d]" % (sk.Name, -geo - 1)
    turn = "atan2(%s.EndPoint.y - %s.StartPoint.y; %s.EndPoint.x - %s.StartPoint.x)" % (g, g, g, g)
    # a datum line runs along its Z: turn Z onto the sketch's X, then about the sketch normal onto the line
    line.setExpression("Placement", "%s.Placement * placement(%s.StartPoint; rotation(vector(0; 0; 1); %s) * "
                                    "rotation(vector(0; 1; 0); 90))" % (sk.Name, g, turn))
    ends = next((item[2] for item in _projected_line_items(sk) if item[3] == geo), None)
    setattr(line, POINTS, format_points(ends) if ends else "")  # (where the dialog draws the axis)
    _remember(line, _externals(sk))  # (follow_externals: which element it is, when others come and go)
    return line


SEEN = "InCadAxisExternals"  # on that axis: the sketch's projected elements as they were ('Obj:Edge3|...')


def _externals(sk):
    """The sketch's projected elements, one 'Obj:Sub' each, in their order (their index n is geometry id -3 - n)."""
    out = []
    for ref, subs in sk.ExternalGeometry:
        for sub in (subs if isinstance(subs, (tuple, list)) else (subs,)):
            out.append("%s:%s" % (ref.Name if ref is not None else "", sub))
    return out


def _remember(line, names):
    if not hasattr(line, SEEN):
        line.addProperty("App::PropertyString", SEEN, "InCad", "The sketch's projected elements the axis last saw")
        line.setPropertyStatus(SEEN, "Hidden")
    text = "|".join(names)
    if getattr(line, SEEN) != text:
        setattr(line, SEEN, text)


def _axis_lines(sk):
    """The revolve axes running along a projected line of `sk` (their Placement a formula of it)."""
    prefix = "%s.ExternalGeo[" % sk.Name
    return [line for line in sk.Document.Objects if getattr(line, LINE, 0)
            and prefix in dict(line.ExpressionEngine).get("Placement", "")]


def _renumber(sk, line, n):
    """The axis to projected element n (None: that line is gone — the axis stays where it is, at its two points:
    its formula would point at nothing, "list index out of range", and the revolve failed)."""
    geo = getattr(line, LINE)
    if n is None:
        pl = line.Placement
        line.setExpression("Placement", None)
        line.Placement = pl
        setattr(line, LINE, 0)
        return
    if -n - 3 == geo:
        return
    prefix = "%s.ExternalGeo[" % sk.Name
    expr = dict(line.ExpressionEngine)["Placement"]
    setattr(line, LINE, -n - 3)
    line.setExpression("Placement", expr.replace("%s%d]" % (prefix, -geo - 1), "%s%d]" % (prefix, n + 2)))


def externals_going(sk, gone):
    """Before the projected elements `gone` of sketch `sk` are deleted (their places in its ExternalGeometry, as
    delExternal takes them: the thread's edges a revolve moved before the thread lets go, Break Link, an unused
    projection dropped): an axis along one of them stays where it is, one along a later one follows the
    renumbering."""
    gone = set(gone)
    if not gone:
        return
    names = _externals(sk)
    after = [x for k, x in enumerate(names) if k not in gone]
    for line in _axis_lines(sk):
        n = -getattr(line, LINE) - 3
        _renumber(sk, line, None if n in gone else n - sum(1 for g in gone if g < n))
        if getattr(line, LINE, 0):
            _remember(line, after)


def follow_externals(sk):
    """After the sketch's projected elements changed in any way (one deleted by hand in the sketch, a link broken):
    the axes along them follow by name — renumbered when others went, left where they are when theirs went. A list
    of the same length is the same elements (renamed as the part changed)."""
    import difflib
    lines = _axis_lines(sk)
    if not lines:
        return
    now = _externals(sk)
    for line in lines:
        seen = getattr(line, SEEN, "")
        before = seen.split("|") if seen else None
        if before is None or len(before) == len(now) or before == now:
            _remember(line, now)  # (a revolve made before this: its number taken as it is)
            continue
        n = -getattr(line, LINE) - 3
        moved = None
        for a, b, size in difflib.SequenceMatcher(None, before, now, autojunk=False).get_matching_blocks():
            if a <= n < a + size:
                moved = b + n - a
        _renumber(sk, line, moved)
        if getattr(line, LINE, 0):
            _remember(line, now)


def _drop_helper(doc, f):
    line = axis_helper(f)
    if line is not None:
        body = line.getParentGeoFeatureGroup()
        if body is not None:
            body.removeObject(line)
        doc.removeObject(line.Name)


def validate_axis(values, profile):
    """Raise a plain error if the axis choice can't be resolved, WITHOUT creating anything — called before a
    Join<->Cut type swap so a bad axis never deletes the feature half-way (it left a dangling reference that
    crashed the next preview with 'TypeId of deleted object')."""
    mode = values.get("axis_mode") or "V_Axis"
    if mode == "construction":
        if not axis_line_options(profile):
            raise ValueError("No line in the sketch: draw one, project an edge, or choose another axis")
    elif mode == "reference":
        if not (values.get("axis_ref") or "").strip():
            raise ValueError("Pick a line of the sketch, an axis or a straight edge for the axis")
    elif mode == "two_points":
        points = parse_points(values.get("axis_points"))
        if len(points) != 2:
            raise ValueError("Click two points for the axis")
        if (points[1] - points[0]).Length < 1e-6:
            raise ValueError("The two points must be different")


def _axis(doc, f, values):
    mode = values.get("axis_mode") or "V_Axis"
    profile = profile_of(f)
    if mode in ("V_Axis", "H_Axis"):
        return profile, [mode]
    if mode == "construction":
        lines = axis_line_options(profile)
        if not lines:
            raise ValueError("No line in the sketch: draw one, project an edge, or choose another axis")
        wanted = values.get("axis_construction") or lines[0][0]
        drawn = lines_sketch(profile)
        if wanted.startswith("Line"):  # (also one made construction as the axis before: still "Line N")
            return drawn, _normal_axis(drawn, wanted)
        if wanted not in [v for v, _ in lines]:
            wanted = lines[0][0]
        if wanted.startswith("ext"):  # a projected line lies IN the sketch plane: a helper through its endpoints
            geo = next((item[3] for item in _projected_line_items(drawn) if item[0] == wanted), None)
            if geo is None:
                raise ValueError("That projected line is no longer in the sketch: pick another axis")
            return _line_helper(doc, f, drawn, geo), [""]
        if drawn is not profile:  # an area copy carries its sketch's construction lines, in the same order
            return profile, [wanted]
        return drawn, [wanted]
    if mode == "reference":
        pairs = parse_picks(doc, values.get("axis_ref"))
        if not pairs:
            raise ValueError("Pick a line of the sketch, an axis or a straight edge for the axis")
        obj, sub = pairs[0]
        return obj, [sub]
    if mode == "two_points":
        points = parse_points(values.get("axis_points"))
        if len(points) != 2:
            raise ValueError("Click two points for the axis")
        flat = project_to_plane(points, profile)  # the axis must lie in the profile's plane
        if (flat[1] - flat[0]).Length < 1e-6:
            raise ValueError("Those two points are one above the other on the profile's plane: pick points that "
                             "are apart within the plane")
        return _place_helper(doc, f, flat), [""]
    raise ValueError("Unknown axis choice %r" % mode)


def _type(operation):
    return CUT if operation == "cut" else JOIN


def _uses_helper(values, profile=None):
    mode = values.get("axis_mode") or "V_Axis"
    wanted = values.get("axis_construction") or ""
    return mode == "two_points" or (mode == "construction" and wanted.startswith("ext"))


def _apply(doc, f, values):
    f.ReferenceAxis = _axis(doc, f, values)
    if not _uses_helper(values, profile_of(f)):
        _drop_helper(doc, f)
    extent = values.get("extent", "full")
    f.Type = "TwoAngles" if extent == "two_angles" else "Angle"
    f.Reversed = bool(values.get("flip")) and extent == "angle"
    if extent == "full":
        value_param(doc, f, "Angle", "360 °", "angle")
    else:
        value_param(doc, f, "Angle", values.get("angle") or "90 °", "angle")
    if extent == "two_angles":
        value_param(doc, f, "Angle2", values.get("angle2") or "90 °", "angle")
        engine = dict(f.ExpressionEngine)  # (a value is a formula until the recompute: read through it)
        total = sum(f.evalExpression(engine[p]).Value if p in engine else getattr(f, p).Value
                    for p in ("Angle", "Angle2"))  # OCC turns the total round past 360: 200 + 200 gave 40 deg
        if total > 360 + 1e-9:
            raise ValueError("The two angles add up to %g°, more than a full turn: make them 360° together at most "
                             "(or choose Full)" % total)


def create(doc, body, profile, values):
    target = body
    if values.get("operation") == "new_solid":  # the profile goes along into the new Solid (as Extrude's)
        from incad.core import document
        target = document.new_solid(doc)
        body.removeObject(profile)
        target.addObject(profile)
        document.settle_support(profile, target)  # on a face of the part: not the whole part in the new Solid
    f = _new(target, values.get("operation", "join"))
    f.Profile = profile
    _apply(doc, f, values)
    return f


def read(doc, f):
    angle = value_text(doc, f, "Angle") or "90 °"
    extent = "two_angles" if f.Type == "TwoAngles" else ("full" if abs(f.Angle.Value - 360) < 1e-9 else "angle")
    from incad.features.extrude import _starts_own_solid
    operation = "cut" if is_cut(f) else "join"
    if operation == "join" and _starts_own_solid(doc, f):  # (a later Join in that Solid is a Join of it)
        operation = "new_solid"
    values = {"operation": operation, "extent": extent,
              "angle": angle, "angle2": value_text(doc, f, "Angle2") or "90 °", "flip": bool(f.Reversed),
              "axis_mode": "V_Axis", "axis_construction": "", "axis_ref": "", "axis_points": ""}
    ref = f.ReferenceAxis
    obj, sub = (ref[0], (ref[1][0] if ref[1] else "")) if ref else (None, "")
    profile = profile_of(f)
    lines = axis_line_options(profile)
    if lines:
        values["axis_construction"] = lines[0][0]
    if obj is None:
        pass
    elif obj is profile and sub in ("V_Axis", "H_Axis"):
        values["axis_mode"] = sub
    elif obj in (profile, lines_sketch(profile)) and sub.startswith("Axis"):
        values.update(axis_mode="construction", axis_construction=axis_name(sub))
    elif getattr(obj, HELPER, None) == f.Name:  # a two-point or projected-line axis, both held by a helper line
        ext = _helper_line_value(obj, lines_sketch(profile))
        if ext:
            values.update(axis_mode="construction", axis_construction=ext)
        else:
            values.update(axis_mode="two_points", axis_points=getattr(obj, POINTS, ""))
    else:
        values.update(axis_mode="reference", axis_ref=format_picks([(obj, sub)]))
    return values


def _helper_line_value(line, sk):
    """"extK" when the helper runs along a projected line of the sketch: the one it is tied to, or (a revolve made
    before the tie, with two fixed points) the projected line through both of its points."""
    items = _projected_line_items(sk)
    geo = getattr(line, LINE, 0)
    if geo:
        return next((item[0] for item in items if item[3] == geo), "")
    pts = parse_points(getattr(line, POINTS, ""))
    if len(pts) != 2:
        return ""
    for value, _lbl, ends, _geo in items:
        if any(all((p - q).Length < 1e-6 for p, q in zip(pts, order)) for order in (ends, ends[::-1])):
            return value
    return ""


def adjusted(doc, f):
    """Values the dialog shows after a preview: an axis along a projected line the sketch let go of (the revolve went
    before a thread, incad.core.thread_order) is fixed at that line's two points now, and stays there on Join / Cut."""
    values = read(doc, f)
    if values.get("axis_mode") == "two_points" and values.get("axis_points"):
        return {"axis_mode": "two_points", "axis_points": values["axis_points"]}
    return {}


def update(doc, f, values):
    """Apply dialog values. A Revolution / Groove of an older file becomes ProstoCAD's own Revolve (`convert`), and
    Join <-> Cut re-makes it as the other kind, in its place with its values."""
    want = _type(values.get("operation", "join"))
    if f.TypeId != want:
        validate_axis(values, profile_of(f))  # before the old feature goes: a bad axis stays non-fatal
        f = convert(doc, f, want)
    _apply(doc, f, values)
    return f


_COPIED = ("Angle", "Angle2", "Midplane", "Reversed", "Refine")


def convert(doc, old, type_id=None):
    """ProstoCAD's own Revolve (a Join, or a Cut: type_id; else as `old` is) in the place of `old` (FreeCAD's
    Revolution / Groove of an older file, or our own of the other kind): the same label, profile, axis, values and
    parameters; the features after it are built on the new one, sketches and picks on its faces stay on the same
    faces, its two-point axis follows it. Returns the new feature."""
    from incad.core import document
    from incad.features import own_pattern
    if type_id is None:
        type_id = CUT if is_cut(old) else JOIN
    body = old.getParentGeoFeatureGroup()
    old_shape = old.Shape.copy()
    label, visible, was_tip = old.Label, old.Visibility, body.Tip is old
    f = doc.addObject(type_id, "Revolve")
    RevolveFeature(f)
    body.insertObject(f, old, True)  # right after it, then in its place
    f.BaseFeature = old.BaseFeature
    f.Profile = old.Profile
    if old.ReferenceAxis and old.ReferenceAxis[0] is not None:
        f.ReferenceAxis = (old.ReferenceAxis[0], list(old.ReferenceAxis[1]) or [""])
    f.Type = old.Type if old.Type in RevolveFeature.KINDS else "Angle"
    for prop in _COPIED:
        if prop in old.PropertiesList and prop in f.PropertiesList:
            setattr(f, prop, getattr(old, prop))
    for path, expr in old.ExpressionEngine:
        if path.lstrip(".") in f.PropertiesList:
            f.setExpression(path.lstrip("."), expr)
    for h in [o for o in body.Group if getattr(o, HELPER, None) == old.Name]:  # a two-point / projected axis
        setattr(h, HELPER, f.Name)
    users = [o for o in old.InList if o is not body and o is not f]
    for o in users:
        if getattr(o, "BaseFeature", None) is old:
            o.BaseFeature = f
    if was_tip:
        body.Tip = f
    doc.recompute([f])
    for o in users:
        own_pattern._relink(o, old, f, old_shape)
    document.show_like(f, old)
    body.removeObject(old)
    doc.removeObject(old.Name)
    f.Label = label
    f.Visibility = visible
    return f


def _pad_of(f):
    return f
