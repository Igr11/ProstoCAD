"""Revolve (Inventor) = PartDesign Revolution (Join) / Groove (Cut) around an axis chosen one of five ways: the
sketch's vertical or horizontal axis, a construction line of the sketch (listed), a line / axis / edge picked in
the view (a line of the sketch, an origin or work axis, a straight edge of the solid), or two points clicked on
the part — a hidden Work Axis through them, owned by the revolve (shown under it, deleted with it). New Solid: the
revolve in a Solid of its own, for a Boolean later (as Extrude's)."""
import json

import FreeCAD as App

from incad.features.common import Field, format_picks, parse_picks, profile_of, value_param, value_text

TITLE = "Revolve"
ICON = "revolve"
TYPES = ("PartDesign::Revolution", "PartDesign::Groove")
PROPS = {"angle": "Angle", "angle2": "Angle2"}
HELPER = "InCadHelperOf"      # on the Work Axis InCad makes for two points: the revolve it belongs to
POINTS = "InCadAxisPoints"    # on that axis: the two points as typed into the field ("x y z;x y z")
AXIS_MODES = (("V_Axis", "Sketch vertical"), ("H_Axis", "Sketch horizontal"),
              ("construction", "Line of the sketch"), ("reference", "Line, axis or edge"),
              ("two_points", "Two points"))
FIELDS = (
    Field("operation", "Operation", "choice", (("join", "Join"), ("cut", "Cut"), ("new_solid", "New Solid")), "join"),
    Field("axis_mode", "Axis", "combo", AXIS_MODES, "V_Axis"),
    Field("axis_construction", "Line", "combo", (), "", show_if=("axis_mode", ("construction",))),
    Field("axis_ref", "Pick", "pick", pick="axis", show_if=("axis_mode", ("reference",))),
    Field("axis_points", "Points", "pick", pick="two_points", show_if=("axis_mode", ("two_points",))),
    Field("extent", "Extents", "choice", (("full", "Full"), ("angle", "Angle"), ("two_angles", "Two angles")), "full"),
    Field("angle", "Angle", "value_angle", default="90 °", show_if=("extent", ("angle", "two_angles"))),
    Field("angle2", "Angle 2", "value_angle", default="90 °", show_if=("extent", ("two_angles",))),
    Field("flip", "Flip", "bool", default=False, show_if=("extent", ("angle",))),
)


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


def _keep_axis_out_of_profile(sk, value):
    """Projected edges are normal geometry by default (Inventor). A projected axis line that closes no profile is an
    open wire beside it, and FreeCAD refuses the revolve ('Wire is not closed'): it becomes construction, recorded
    as the user's choice (Construction switches it back). One that closes the profile (drawn up to the axis) stays."""
    from incad.core import sketch
    geo = next((item[3] for item in _projected_line_items(sk) if item[0] == value), None)
    if geo is None or not sketch.is_defining(sk, geo):
        return
    g = sk.ExternalGeo[-geo - 1]
    ends = [sk.Placement.multVec(g.StartPoint), sk.Placement.multVec(g.EndPoint)]
    for w in sk.Shape.Wires:
        if not w.isClosed():
            continue
        for e in w.Edges:
            pts = [v.Point for v in e.Vertexes]
            if len(pts) == 2 and any(all((p - q).Length < 1e-6 for p, q in zip(pts, order))
                                     for order in (ends, ends[::-1])):
                return
    sketch.set_projected_construction(sk, [geo], True)


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


def _projected_ends(sk, value):
    for v, _lbl, ends in projected_lines(sk):
        if v == value:
            return ends
    return None


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
    values = {f.key: f.default for f in FIELDS}
    lines = axis_line_options(profile)
    if lines:
        values["axis_construction"] = lines[0][0]
        if len(lines) - len(normal_lines(lines_sketch(profile))) == 1:  # the one construction / projected line
            values["axis_mode"] = "construction"                         # is what it was drawn or projected for
    return values


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


def _place_helper(doc, f, points):
    a, b = points
    d = b - a
    if d.Length < 1e-6:
        raise ValueError("The two points must be different")
    d.normalize()
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
    line.Placement = App.Placement(a, App.Rotation(App.Vector(0, 0, 1), d))  # a datum line runs along its Z
    setattr(line, POINTS, format_points(points))
    line.Visibility = False
    return line


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
        if wanted not in [v for v, _ in lines]:
            wanted = lines[0][0]
        drawn = lines_sketch(profile)
        if wanted.startswith("ext"):  # a projected line lies IN the sketch plane: a helper through its endpoints
            ends = _projected_ends(drawn, wanted)
            if ends is None:
                raise ValueError("That projected line is no longer in the sketch: pick another axis")
            _keep_axis_out_of_profile(drawn, wanted)
            return _place_helper(doc, f, [ends[0], ends[1]]), [""]
        if wanted.startswith("Line"):
            return drawn, [_shape_edge(drawn, wanted)]
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
    return "PartDesign::Groove" if operation == "cut" else "PartDesign::Revolution"


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
    f = target.newObject(_type(values.get("operation", "join")), "Revolve")
    f.Profile = profile
    _apply(doc, f, values)
    return f


def read(doc, f):
    angle = value_text(doc, f, "Angle") or "90 °"
    extent = "two_angles" if f.Type == "TwoAngles" else ("full" if abs(f.Angle.Value - 360) < 1e-9 else "angle")
    from incad.core import document
    operation = "cut" if f.TypeId == "PartDesign::Groove" else "join"
    body = f.getParentGeoFeatureGroup()
    if operation == "join" and body is not None and document.primary_body(doc) is not body:
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
        values.update(axis_mode="two_points", axis_points=getattr(obj, POINTS, ""))
    else:
        values.update(axis_mode="reference", axis_ref=format_picks([(obj, sub)]))
    return values


def update(doc, f, values):
    want = _type(values.get("operation", "join"))
    if f.TypeId != want:
        validate_axis(values, profile_of(f))  # before _replace deletes the old feature: a bad axis stays non-fatal
        from incad.features import extrude
        helper = axis_helper(f)
        f = extrude._replace(doc, f, want)
        if helper is not None:
            setattr(helper, HELPER, f.Name)  # the axis follows the re-made feature
    _apply(doc, f, values)
    return f


def _pad_of(f):
    return f
