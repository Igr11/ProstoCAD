"""Thread (Inventor) = a hidden groove-profile sketch on the face's start edge (SectionOfRevolution: a plane
through the axis, x radial, y along the axis) + one PartDesign SubtractiveHelix. Real geometry for printing.

Standard, size, clearance, hand, start end and offset live on the helix (InCadThread, JSON) and regenerate the
profile on edit; the length (Distance mode) is a model parameter bound to Height."""
import dataclasses
import math
import json

import FreeCAD as App
import Part

from incad.features.common import Field, fine_step, parse_picks, resolve, value_param, value_text
from incad.threads import profile, standards
from incad.core import safe

TITLE = "Thread"
ICON = "thread"
# cut into the surface; grown onto it (GrownThread; 0.14 grew an AdditiveHelix)
TYPES = ("PartDesign::SubtractiveHelix", "PartDesign::FeaturePython", "PartDesign::AdditiveHelix")
RECREATE_ON = ("build",)  # cut <-> grow is another kind of feature
NEEDS_PROFILE = False
PROPS = {"length": "Height"}
PRESELECT = {"face": "cylinder"}
SOLID_PICKS = True  # its picks are on the part: a sketch line over an edge stands for that edge
# the next hole is clicked on this thread's own preview (it is the part on screen): taken, then named on the part below
PICKS_ON_SELF = ("face", "start_face")
_STORE = "InCadThread"

FIELDS = (
    Field("face", "Face", "picks", pick="cylinder"),  # several: a thread on each, the same settings (the user)
    Field("start_face", "Start face", "pick", pick="plane"),
    Field("offset", "Offset", "value_length", default="0 mm"),
    Field("length_mode", "Length", "choice", (("full", "Full length"), ("distance", "Distance")), "full"),
    Field("length", "Distance", "value_length", default="10 mm", show_if=("length_mode", ("distance",))),
    Field("flip", "Flip direction", "bool", default=False),
    Field("standard", "Standard", "combo", tuple(standards.STANDARDS), "iso"),
    Field("size", "Size", "combo", (), ""),
    Field("custom_profile", "Profile", "choice", standards.CUSTOM_PROFILES, "triangle",
          show_if=("standard", ("custom",))),  # the user: a triangle (its angle), a trapezoid, a semicircle
    Field("custom_pitch", "Pitch", "value_length", default="1.5 mm", show_if=("standard", ("custom",))),
    Field("custom_angle", "Flank angle", "value_angle", default="60 °",
          show_if=(("standard", ("custom",)), ("custom_profile", ("triangle", "trapezoid")))),
    Field("custom_depth", "Depth", "value_length", default="0.9 mm", show_if=("standard", ("custom",))),
    Field("hand", "Hand", "choice", (("right", "Right hand"), ("left", "Left hand")), "right"),
    Field("build", "Thread", "choice", (("cut", "Cut into"), ("grow", "Grow onto")), "cut"),
    Field("chamfer", "Chamfer", "choice", (("none", "None"), ("start", "Start"), ("both", "Both ends")), "none"),
    Field("chamfer_size", "Chamfer size", "value_length", default="0.5 mm", show_if=("chamfer", ("start", "both")),
          step=fine_step),
    Field("clearance", "Print clearance (on diameter)", "value_length", default="0.2 mm"),
    Field("remember", "Save for the next part", "bool", default=False),  # the user: not choosing it all again
)
PRESET = ("standard", "size", "custom_profile", "custom_pitch", "custom_angle", "custom_depth", "hand", "build",
          "chamfer", "chamfer_size", "clearance")  # what Save for the next part keeps (never the faces)


def claims(feature):
    return hasattr(feature, _STORE)


def default_clearance(std):
    """The print clearance a thread of this standard starts with (mm): more for print-friendly threads."""
    from incad.core import prefs
    return standards.default_clearance(std, prefs.thread_clearance())


def defaults(doc, body, profile_=None):
    values = {f.key: f.default for f in FIELDS}
    values.update(face="", start_face="", size="", internal=False)
    values["clearance"] = "%g mm" % default_clearance(values.get("standard"))
    from incad.core import prefs
    saved = prefs.thread_preset()  # Save for the next part: a new thread starts as the saved one
    values.update({k: v for k, v in saved.items() if k in PRESET})
    return values


def keep_preview(values):
    """Grown teeth stay on screen while faces are picked: shown as the part's outline over it, a helix's edges came
    out as a scatter of dots (the user: "where did the picture go?"); a click on them means the part's face."""
    return values.get("build") == "grow"


def after_ok(doc, f, values):
    """Save for the next part ticked: this thread's settings are where the next one starts (in this file or the
    next); the box itself starts unticked again. Grown threads previewed in the dialog are joined to the part."""
    finish_previews(doc)
    if values.get("remember"):
        from incad.core import log, prefs
        prefs.set_thread_preset({k: values.get(k) for k in PRESET if values.get(k) not in (None, "")})
        log.report("info", "Thread settings saved: the next thread starts with %s" % (
            values.get("size") or dict(standards.STANDARDS).get(values.get("standard"), "them")))


def follow(before, values):
    """Dialog values that follow a change: a new standard brings its own print clearance, unless the clearance was
    typed (it is no longer the old standard's default)."""
    old, new = before.get("standard"), values.get("standard")
    if old == new or old is None:
        return {}
    was = _mm(before.get("clearance"), None)
    if was is None or abs(was - default_clearance(old)) > 1e-9:
        return {}
    return {"clearance": "%g mm" % default_clearance(new)}


def _mm(text, default=0.0):
    try:
        q = App.Units.Quantity(text or "")
        return q.Value if str(q.Unit) != str(App.Units.Quantity("1").Unit) else float(text)
    except Exception:
        return default


def _deg(text, default=60.0):
    try:
        return App.Units.Quantity((text or "").replace("°", " deg")).Value
    except Exception:
        return default


# ----- geometry of the picked face -----
def _face(doc, values):
    from incad.features.common import accepts
    picks = parse_picks(doc, values.get("face"))
    if not picks:
        raise ValueError("Pick the cylindrical face to thread (a shaft or a hole)")
    obj, sub = resolve(*picks[0])
    face = obj.getSubObject(sub) if sub.startswith("Face") else None
    if face is None or not accepts("cylinder", obj, sub):
        raise ValueError("Pick a full round cylindrical face (the outside of a shaft or the inside of a hole)")
    return obj, sub, face


def _lead_in_faces(solid, edge):
    """The chamfer, countersink or round that meets the face at this end: the entrance a bolt or a nut goes in from."""
    if isinstance(edge, _CutEnd):  # the end that runs out into another face has none
        return []
    return [f for f in solid.Faces
            if f.Surface.TypeId in ("Part::GeomCone", "Part::GeomToroid") and any(e.isSame(edge) for e in f.Edges)]


def _is_lead_in(solid, edge):
    return bool(_lead_in_faces(solid, edge))


def _is_free(solid, edge, outward, radial, radius):
    """No material just beyond this end (inside the thread's radius): a free end, not a shoulder or a floor.
    A chamfered end is probed past its chamfer (next to the chamfer the part is still solid)."""
    centre = edge.Curve.Center
    eps = max(0.05, 0.01 * radius)
    past = max([0.0] + [(p - centre).dot(outward) for f in _lead_in_faces(solid, edge) for e in f.Edges
                        for p in e.discretize(8)])
    probe = centre + outward * (past + eps) + radial * (0.5 * radius)
    return not solid.isInside(probe, 1e-7, True)


def _plane_of(doc, text):
    """(point, normal) of a picked flat face or plane, or None."""
    picks = parse_picks(doc, text)
    if not picks:
        return None
    o, sub = resolve(*picks[0])
    shape = o.getSubObject(sub) if sub else (o.Shape.Faces[0] if getattr(o, "Shape", None) and o.Shape.Faces else None)
    if shape is None or shape.ShapeType != "Face" or shape.Surface.TypeId != "Part::GeomPlane":
        raise ValueError("The start face must be flat (a face or a work plane)")
    return shape.Surface.Position, shape.normalAt(0, 0)


def _geometry(obj, face, flip, start_plane=None):
    """Facts about a cylindrical face: internal?, radius, start edge name, length along the axis, vector to the
    far end, and whether the start / far end are free. The start is a free end (the tip of a shaft, the mouth of
    a hole); with both ends free, the chamfered (or rounded) one, else the one farther from the origin; "flip"
    takes the other end."""
    surf = face.Surface
    axis, centre = App.Vector(surf.Axis).normalize(), surf.Center
    u0, u1, v0, v1 = face.ParameterRange
    u, v = (u0 + u1) / 2.0, (v0 + v1) / 2.0
    p, n = face.valueAt(u, v), face.normalAt(u, v)
    radial = (p - centre) - axis * (p - centre).dot(axis)
    internal = n.dot(radial) < 0
    radial.normalize()
    circles = [e for e in face.Edges if safe.curve_type(e) == "Part::GeomCircle"]
    if not circles:
        raise ValueError("The face needs a round end to start the thread")
    circles.sort(key=lambda e: (e.Curve.Center - centre).dot(axis))
    ends = [circles[0], circles[-1]]
    if len(circles) < 2 or abs((circles[-1].Curve.Center - circles[0].Curve.Center).dot(axis)) < 1e-6:
        # the other end runs out into another face (a hole breaking into a curved wall); round edges all at one
        # end (a seam split it in two) are that one end
        ends = _with_cut_end(face, circles[0], centre, axis)
    free = []
    for e, other in ((ends[0], ends[1]), (ends[1], ends[0])):
        out = e.Curve.Center - other.Curve.Center
        out.normalize()
        free.append(_is_free(obj.Shape, e, out, radial, surf.Radius))
    if start_plane is not None:  # the picked start face names the end
        point, normal = start_plane
        def gap(e):  # a chamfered mouth: the face's end edge lies behind the end face, by the chamfer's depth
            out = e.Curve.Center - (ends[1] if e is ends[0] else ends[0]).Curve.Center
            out.normalize()
            depth = max([0.0] + [(p - e.Curve.Center).dot(out) for f in _lead_in_faces(obj.Shape, e)
                                 for x in f.Edges for p in x.discretize(8)])
            return abs(abs((e.Curve.Center - point).dot(normal)) - depth)
        on = [i for i, e in enumerate(ends) if gap(e) < 1e-4]
        if not on:
            raise ValueError("The start face must be at one end of the threaded face")
        pick = on[0]
    elif free[0] != free[1]:
        pick = 0 if free[0] else 1
    elif free[0] and _is_lead_in(obj.Shape, ends[0]) != _is_lead_in(obj.Shape, ends[1]):
        pick = 0 if _is_lead_in(obj.Shape, ends[0]) else 1  # both open (a through hole, a loose rod)
    else:
        pick = 0 if ends[0].Curve.Center.Length > ends[1].Curve.Center.Length else 1
    if flip:
        pick = 1 - pick
    if isinstance(ends[pick], _CutEnd):
        if flip or start_plane is not None:
            raise ValueError("The thread starts at the round end of the face (its other end runs into another face)")
        pick = 1 - pick
    start, other = ends[pick], ends[1 - pick]
    name = next(("Edge%d" % (i + 1) for i, e in enumerate(obj.Shape.Edges) if e.isSame(start)), None)
    if name is None:
        raise ValueError("Could not find the end edge of that face")
    length = abs((other.Curve.Center - start.Curve.Center).dot(axis))
    return (internal, surf.Radius, name, length, other.Curve.Center - start.Curve.Center, free[pick], free[1 - pick],
            not isinstance(other, _CutEnd))


class _CutEnd:
    """The end of a face that runs out into another face (no round edge there): the point of the axis level with
    the face's farthest reach, for the length and the free-end probe; it has no chamfer or lead-in of its own."""
    class Curve:
        pass

    def __init__(self, point):
        self.Curve = _CutEnd.Curve()
        self.Curve.Center = point

    def isSame(self, other):
        return False


def _with_cut_end(face, circle, centre, axis):
    """The two ends of a face with one round end: [near, far] along the axis, the far one a _CutEnd."""
    at = (circle.Curve.Center - centre).dot(axis)
    reach = [(p - centre).dot(axis) for e in face.Edges for p in e.discretize(48)]
    lo, hi = min(reach), max(reach)
    far = _CutEnd(centre + axis * (hi if hi - at >= at - lo else lo))
    return [circle, far] if hi - at >= at - lo else [far, circle]


_PREP_TOL = 0.05  # mm (radial): a hole this close to the tap size is threaded as it is
_FIN_TOL = 0.005  # mm (radial): any fin taller than this is turned (bored) away


def _fit(spec, internal, radius, clearance):
    """Check the size and clearance against the face. Returns the run-out that opens the groove at the face and
    the radius the thread works on: a hole off the size's tap diameter is filled or bored to it first (M3 or M4
    in a Ø3 hole), so the thread is always full and has no hair-thin rings."""
    R = spec.major / 2.0
    run = max(0.5, 0.3 * spec.pitch)
    if clearance < 0:
        raise ValueError("Print clearance can't be negative")
    if internal:
        r_min = R - spec.depth_int
        tap = standards.bore_diameter(spec, clearance) / 2.0
        if spec.taper:  # a tapered pipe thread is cut into the hole as it is
            if radius > R + clearance / 2.0 - 0.5 * spec.depth_int:
                raise ValueError("This size is too small for the hole (Ø%.2f): pick a larger size" % (2 * radius))
            if radius < r_min - max(0.5, 0.5 * spec.depth_int):
                raise ValueError("This size is too large for the hole (Ø%.2f): pick a smaller size" % (2 * radius))
            return run + max(0.0, r_min - radius), radius
        if abs(radius - tap) > max(1.0, 0.5 * radius):
            raise ValueError("This size is too %s for the hole (Ø%.2f): pick a %s size" % (
                ("small", 2 * radius, "larger") if tap < radius else ("large", 2 * radius, "smaller")))
        hole = tap if abs(radius - tap) > _PREP_TOL else radius
        crest = profile.knee(spec, True, clearance)  # below it a hair-thin fin would stay on every crest
        if crest > hole + _FIN_TOL:
            hole = crest
        return run + max(0.0, r_min - hole), hole
    root = R - spec.crest_drop - spec.depth - clearance / 2.0
    if root < 0.3 * R:
        raise ValueError("Print clearance is too large for this thread size")
    if radius <= root + 0.5 * spec.depth:
        raise ValueError("This size is too large for the shaft (Ø%.2f): pick a smaller size" % (2 * radius))
    if radius > R + max(0.5, 0.5 * spec.depth):
        raise ValueError("This size is too small for the shaft (Ø%.2f): pick a larger size" % (2 * radius))
    crest = profile.knee(spec, False, clearance)  # above it a hair-thin fin would stay on every crest
    rod = crest if radius > crest + _FIN_TOL and not spec.taper else radius
    return run + max(0.0, rod - R), rod


def _spec(values, diameter, internal, clearance, grow=False):
    std = values.get("standard") or "iso"
    if grow and std != "custom":  # the teeth stand on the surface: a rod is the bolt's root, a bore the nut's root
        size = values.get("size") or ""
        names = standards.sizes(std)
        if size not in names and names:
            def miss(name):
                s = standards.spec(std, name)
                return abs(s.major - 2 * s.depth - diameter) if not internal else abs(s.major - diameter)
            size = min(names, key=miss)
        s = standards.spec(std, size)
        return dataclasses.replace(s, major=diameter + 2 * s.depth if not internal else diameter)
    if std == "custom":
        return standards.spec("custom", "Custom", custom={
            "major": (diameter + 2 * _mm(values.get("custom_depth"), 0.9) if grow else diameter) if not internal
            else (diameter if grow else diameter + 2 * _mm(values.get("custom_depth"), 0.9) - clearance),
            "pitch": _mm(values.get("custom_pitch"), 1.5), "angle": _deg(values.get("custom_angle"), 60.0),
            "depth": _mm(values.get("custom_depth"), 0.9), "profile": values.get("custom_profile") or "triangle"})
    size = values.get("size") or ""
    if size not in standards.sizes(std):
        size = standards.suggest(std, diameter, internal, clearance)
    spec = standards.spec(std, size)
    slack = 2 * max(0.5, 0.5 * spec.depth)  # the same allowance _fit gives (a bigger gap is 'pick a larger size')
    if not internal and not spec.taper and spec.major + 0.02 < diameter <= spec.major + slack:
        # a shaft a little larger than the nominal (Ø4.5 with an M4): the groove cut for the nominal diameter would
        # leave hair-thin ridges above it, so the profile follows the real diameter (same pitch, depth and angle)
        spec = dataclasses.replace(spec, major=diameter)
    return spec


def options(key, values):
    if key == "size":
        std = values.get("standard") or "iso"
        return [("", "Auto (from the face)")] + [(s, s) for s in standards.sizes(std)]
    return None


# ----- build -----
def _store(f, data):
    if not hasattr(f, _STORE):
        f.addProperty("App::PropertyString", _STORE, "InCad", "InCad thread settings")
        f.setPropertyStatus(_STORE, "Hidden")
    setattr(f, _STORE, json.dumps(data, sort_keys=True))


def _load(f):
    try:
        return json.loads(getattr(f, _STORE, "") or "{}")
    except ValueError:
        return {}


def _start_face_on(doc, f, obj, text):
    """A start face picked on the thread or on one of its helpers (ThreadFill is what the view shows) named on
    the threaded feature instead: the helpers come and go with the size."""
    picks = parse_picks(doc, text)
    if not picks:
        return text
    o, sub = resolve(*picks[0])
    if o is not f and getattr(o, HELPER, None) != f.Name or not sub.startswith("Face"):
        return text
    plane = o.getSubObject(sub)
    if plane.Surface.TypeId != "Part::GeomPlane":
        return text
    n = plane.normalAt(0, 0)
    for i, fc in enumerate(obj.Shape.Faces):
        if fc.Surface.TypeId == "Part::GeomPlane" and abs(abs(fc.normalAt(0, 0).dot(n)) - 1) < 1e-7 \
                and abs((fc.Surface.Position - plane.Surface.Position).dot(n)) < 1e-6:
            return "%s:Face%d" % (obj.Name, i + 1)
    return text


def _is_piece(doc, o):
    """A thread or something InCad made for one (its fill, bore, chamfer)."""
    if o is None:
        return False
    if claims(o):
        return True
    owner = getattr(o, HELPER, None)
    return bool(owner) and doc.getObject(owner) is not None and claims(doc.getObject(owner))


def _same_face(base, face):
    """The name of the face of `base` that `face` is part of (same surface, a point of it lies on it), or None."""
    u0, u1, v0, v1 = face.ParameterRange
    point = Part.Vertex(face.valueAt((u0 + u1) / 2.0, (v0 + v1) / 2.0))
    for i, f in enumerate(base.Shape.Faces):
        if f.Surface.TypeId != face.Surface.TypeId:
            continue
        if f.Surface.TypeId == "Part::GeomCylinder":
            a, b = App.Vector(f.Surface.Axis), App.Vector(face.Surface.Axis)
            if abs(f.Surface.Radius - face.Surface.Radius) > 1e-6 or abs(abs(a.normalize().dot(b.normalize())) - 1) > 1e-7:
                continue
        if f.distToShape(point)[0] < 1e-6:
            return "Face%d" % (i + 1)
    return None


def rehome(doc, values):
    """Faces picked on a thread or on its fill / chamfer (the tip when the next hole is threaded) named on the
    feature below the threads: a thread hung on another thread's edges broke when that one was suppressed."""
    out = dict(values)
    for key in ("face", "start_face"):
        picks = parse_picks(doc, out.get(key))
        if not picks:
            continue
        o, sub = resolve(*picks[0])
        if not sub.startswith("Face") or not _is_piece(doc, o):
            continue
        base = o
        while base is not None and _is_piece(doc, base):
            base = base.BaseFeature
        name = _same_face(base, o.getSubObject(sub)) if base is not None else None
        if name:
            out[key] = "%s:%s" % (base.Name, name)
    return out


def repair(doc):
    """Threads an older InCad hung on another thread (their sketches attached to it) are rebuilt on the feature
    below the threads. Returns their names."""
    fixed = []
    for t in [o for o in doc.Objects if claims(o) and not getattr(o, HELPER, None)]:
        sketches = [x.Profile[0] for x in [t] + helpers_of(t)
                    if isinstance(getattr(x, "Profile", None), (tuple, list)) and x.Profile]
        if any(_is_piece(doc, a) and a is not t for sk in sketches for a, _ in sk.AttachmentSupport):
            update(doc, t, read(doc, t))
            fixed.append(t.Name)
    return fixed


def _build(doc, body, f, values):
    values = rehome(doc, values)
    obj, sub, face = _face(doc, values)
    if values.get("start_face"):
        values = dict(values, start_face=_start_face_on(doc, f, obj, values.get("start_face")))
    internal, radius, edge, face_len, to_other, start_free, end_free, end_round = _geometry(
        obj, face, bool(values.get("flip")), _plane_of(doc, values.get("start_face")))
    clearance = _mm(values.get("clearance"), 0.2)
    grow = values.get("build") == "grow"
    spec = _spec(values, 2 * radius, internal, clearance, grow)
    if grow:
        return _build_grown(doc, body, f, values, obj, edge, internal, radius, face_len, to_other, start_free,
                            end_free and end_round, spec)
    runout, hole = _fit(spec, internal, radius, clearance)
    outline = profile.outline(spec, internal, clearance, runout)
    half = profile.outline_width(outline) / 2.0
    offset = _mm(values.get("offset"), 0.0)
    # free end: the helix starts/ends a full pitch outside the part, so the thread runs out cleanly (as a die
    # cuts it); closed end (shoulder, floor): stop half a groove short, never cutting into it
    lead = (-spec.pitch if offset == 0 else half) if start_free else half
    tail = spec.pitch if end_free else -half
    sk = f.Profile[0] if isinstance(f.Profile, (tuple, list)) and f.Profile else (f.Profile or None)
    fresh = sk is None
    if fresh:
        sk = body.newObject("Sketcher::SketchObject", "ThreadProfile")
    sk.AttachmentSupport = [(obj, edge)]
    sk.MapMode = "SectionOfRevolution"
    sk.deleteAllGeometry()
    sk.recompute()  # the sketch alone: the helix is never recomputed with an empty profile ("Linked shape is empty")
    ly = sk.getGlobalPlacement().Rotation.multVec(App.Vector(0, 1, 0))
    sign = 1.0 if to_other.dot(ly) > 0 else -1.0
    sk.addGeometry(_outline_geometry(outline, sign, offset + lead))
    sk.Visibility = False
    if fresh:
        f.Profile = sk
    f.Refine = False  # probed: refining a helical cut corrupts the solid (wrong volume, still "valid")
    f.ReferenceAxis = (sk, ["V_Axis"])
    f.Mode = "pitch-height-angle"
    f.Pitch = spec.pitch
    f.Reversed = sign < 0
    f.LeftHanded = values.get("hand") == "left"
    f.Angle = -spec.taper if internal else spec.taper
    if values.get("length_mode") == "distance":
        value_param(doc, f, "Height", values.get("length") or "10 mm", "length")
    else:
        f.setExpression("Height", None)
        f.Height = max(face_len - offset - lead + tail, spec.pitch)
    expr = dict(f.ExpressionEngine).get("Height")
    height = f.evalExpression(expr).Value if expr else f.Height.Value
    _remove_helpers(doc, body, f, "ends")  # (0.14 trimmed grown teeth at the ends)
    _, reaches = _prep(doc, body, f, obj, edge, radius, hole, sign, offset + lead, offset + lead + height,
                       face_len, start_free, end_free, internal, spec.pitch)
    radii = tuple(hole if r else radius for r in reaches)  # the part's radius at each end
    chamfer = _chamfer(doc, body, f, obj, edge, internal, radii, sign, face_len, start_free,
                       end_free and end_round, values.get("chamfer") or "none",
                       _mm(values.get("chamfer_size"), 0.5))
    _store(f, {"face": values.get("face"), "start_face": values.get("start_face") or "", "standard": spec.std, "size": spec.size, "flip": bool(values.get("flip")),
               "chamfer": values.get("chamfer") or "none", "chamfer_size": values.get("chamfer_size") or "0.5 mm",
               "chamfer_feature": chamfer.Name if chamfer is not None else "",
               "clearance": values.get("clearance") or "0.2 mm", "hand": values.get("hand") or "right",
               "offset": values.get("offset") or "0 mm", "length_mode": values.get("length_mode") or "full",
               "internal": internal, "custom_pitch": values.get("custom_pitch"),
               "custom_angle": values.get("custom_angle"), "custom_depth": values.get("custom_depth"),
               "custom_profile": values.get("custom_profile") or "triangle", "build": "cut",
               "size_auto": _size_auto(f, values),  # chosen from the shaft: chosen again when the shaft changes
               "measured": [round(2 * radius, 6), round(face_len, 6)]})  # see follow_part
    doc.recompute()
    if f.Shape.isNull() or not f.Shape.isValid():
        raise ValueError("The thread can't be built with these values (check the size and the clearance)")
    return f



def _outline_geometry(outline, sign, at):
    """The thread's outline (profile.outline) as sketch lines and arcs, its middle `at` along the axis."""
    v = lambda p: App.Vector(p[0], sign * (at + p[1]), 0)
    return [Part.LineSegment(v(s[1]), v(s[2])) if s[0] == "line" else Part.Arc(v(s[1]), v(s[2]), v(s[3]))
            for s in outline]


GROWN = "PartDesign::FeaturePython"
_PREVIEW = set()  # (document, thread) grown threads shown in their open dialog without the fuse
FADE = 0.25  # turns over which a grown tooth rises out of the surface, and sinks back into it at its end
_MARGIN = 0.1  # mm between a grown tooth's sunk end and the end of the face


def _build_grown(doc, body, f, values, obj, edge, internal, radius, face_len, to_other, start_free, end_free, spec):
    """Grown teeth (the user: "a grown round thread starts from nothing inside the face, grows, and runs back into
    the wall at its end — canister caps are made so"): GrownThread computes them at every recompute from the face;
    here the settings, the length and the lead-in chamfer."""
    for role in ("prep", "ends"):
        _remove_helpers(doc, body, f, role)
    if values.get("length_mode") == "distance":
        value_param(doc, f, "Height", values.get("length") or "10 mm", "length")
    else:
        f.setExpression("Height", None)
    def settings(chamfer):
        return {"face": values.get("face"), "start_face": values.get("start_face") or "", "standard": spec.std,
               "size": spec.size, "flip": bool(values.get("flip")), "chamfer": values.get("chamfer") or "none",
               "chamfer_size": values.get("chamfer_size") or "0.5 mm",
               "chamfer_feature": chamfer.Name if chamfer is not None else "",
               "clearance": values.get("clearance") or "0.2 mm", "hand": values.get("hand") or "right",
               "offset": values.get("offset") or "0 mm", "length_mode": values.get("length_mode") or "full",
               "internal": internal, "custom_pitch": values.get("custom_pitch"),
               "custom_angle": values.get("custom_angle"), "custom_depth": values.get("custom_depth"),
               "custom_profile": values.get("custom_profile") or "triangle", "build": "grow"}
    _store(f, settings(None))  # first: the chamfer's recompute computes the thread too
    tall = spec.depth + spec.pitch  # past the teeth's tips: what they leave past the end goes with the corner
    chamfer = _chamfer(doc, body, f, obj, edge, internal, (radius, radius), None, face_len, start_free, end_free,
                       values.get("chamfer") or "none", _mm(values.get("chamfer_size"), 0.5), to_other,
                       grown=tall)
    _store(f, settings(chamfer))
    key = (doc.Name, f.Name)
    if safe.busy():  # in its dialog: shown standing on the part, joined to it on OK (finish_previews)
        _PREVIEW.add(key)
    else:
        _PREVIEW.discard(key)
    f.touch()
    doc.recompute()
    if "Invalid" in f.State:
        raise ValueError(f.getStatusString() or "The thread can't be grown with these values")
    if f.Shape.isNull() or not f.Shape.isValid():
        raise ValueError("The thread can't be built with these values (check the size and the clearance)")
    return f


def _section(outline, theta, h, shift, scale=1.0, base=0.0):
    """The tooth's outline (r, z) on the half plane at angle theta round the axis, h along it, moved out by shift,
    shrunk by scale towards its root on the surface (radius base)."""
    u = App.Vector(math.cos(theta), math.sin(theta), 0)
    p = lambda q: u * (base + (q[0] - base) * scale + shift) + App.Vector(0, 0, h + q[1] * scale)
    return Part.Wire([Part.LineSegment(p(s[1]), p(s[2])).toShape() if s[0] == "line"
                      else Part.Arc(p(s[1]), p(s[2]), p(s[3])).toShape() for s in outline])


def grown_teeth(f):
    """The teeth of a grown thread (a compound of its pieces), in the part's coordinates: the tooth swept along a
    helix on the face, at a plain end growing out of the surface over a quarter turn, lower and narrower to nothing
    (smoothly), so it starts and ends inside the face; at a chamfered end running out past it, for the chamfer to
    cut."""
    doc = f.Document
    values = _load(f)
    obj, _sub, face = _face(doc, values)
    internal, radius, edge, face_len, to_other, start_free, end_free, end_round = _geometry(
        obj, face, bool(values.get("flip")), _plane_of(doc, values.get("start_face")))
    clearance = _mm(values.get("clearance"), 0.2)
    spec = _spec(values, 2 * radius, internal, clearance, True)
    outline = profile.outline(spec, internal, clearance, grow=True, base=radius)
    rs = [pt[0] for s in outline for pt in s[1:]]
    tall = (radius - min(rs)) if internal else (max(rs) - radius)  # how far the tooth stands off the surface
    margin = profile.outline_width(outline) / 2.0 + _MARGIN
    offset = _mm(values.get("offset"), 0.0)
    # a chamfered free end: the teeth run full height out past it, and the chamfer (made after the thread) cuts
    # them with the part's corner, as on a bolt (the user: "the thread reaches the end of the chamfer"); a plain
    # end: they rise out of the surface and sink back into it inside the face
    chamfer = values.get("chamfer") or "none"
    out0 = chamfer in ("start", "both") and start_free and offset == 0
    out1 = chamfer == "both" and end_free and end_round and values.get("length_mode") != "distance"
    beyond = margin + 0.5 * spec.pitch
    a0 = -beyond if out0 else offset + margin
    a1 = face_len + beyond if out1 else face_len - margin
    if values.get("length_mode") == "distance":
        a1 = min(a1, max(a0, offset) + max(f.Height.Value - margin, 0.0))
    height = a1 - a0
    if height < 0.5 * spec.pitch:
        raise ValueError("The face is too short for a grown thread of this pitch")
    total = 2 * math.pi * height / spec.pitch
    fade = min(2 * math.pi * FADE, total / 3.0)
    fade0, fade1 = (0.0 if out0 else fade), (0.0 if out1 else fade)
    left = values.get("hand") == "left"
    into = 1.0 if internal else -1.0  # the surface's material: out from a bore, in towards a rod's axis

    def risen(a):
        near = [a / fade0] if fade0 else []
        near += [(total - a) / fade1] if fade1 else []
        k = min(max(min(near), 0.0), 1.0) if near else 1.0
        # 0 at a plain end, 1 at full height, along a quarter circle: the end is a round nose (an S-curve was slow
        # to the end and drew a long thin spike)
        return math.sqrt(max(k * (2.0 - k), 0.0))

    # a plain end: the tooth grows lower and narrower together, down to a crumb sunk into the surface (the user:
    # "it ends in a pointed tongue: lower and narrower smoothly, like on a moulded cap")
    smallest = 0.05

    def scale(a):
        return smallest + (1.0 - smallest) * risen(a)

    def shift(a):
        return into * (smallest * tall + 0.05) * (1.0 - risen(a))

    def sweep(a_from, a_to, sections):
        """The tooth swept along the helix from angle a_from to a_to, through sections at those angles."""
        rise = spec.pitch / (2 * math.pi)
        spine = Part.makeHelix(spec.pitch, (a_to - a_from) * rise, radius, 0.0, left)
        spine.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), math.degrees(-a_from if left else a_from))
        spine.translate(App.Vector(0, 0, a_from * rise))
        ps = Part.BRepOffsetAPI.MakePipeShell(Part.Wire(spine))
        ps.setBiNormalMode(App.Vector(0, 0, 1))
        for a in sections:
            ps.add(_section(outline, -a if left else a, a * rise, shift(a), scale(a), radius), False, False)
        ps.build()
        ps.makeSolid()
        piece = ps.shape()
        piece = piece.Solids[0] if piece.Solids else piece
        if piece.Volume < 0:
            piece.reverse()
        return piece

    # rising, full height, sinking: one sweep with many sections bulged far out, or would not close
    steps = 6
    pieces = [sweep(0.0, fade0, [fade0 * k / steps for k in range(steps + 1)])] if fade0 else []
    run = total - fade0 - fade1
    if run > 1e-6:  # a turn a piece: one sweep of 46 turns came out with half the teeth missing (the user's rod)
        n = max(1, int(math.ceil(run / (2 * math.pi) - 1e-9)))
        for k in range(n):
            a = fade0 + run * k / n
            pieces.append(sweep(a, fade0 + run * (k + 1) / n, [a]))
    if fade1:
        pieces.append(sweep(total - fade1, total, [total - fade1 + fade1 * k / steps for k in range(steps + 1)]))
    teeth = Part.makeCompound(pieces)  # joined to the part in one fuse: piece by piece took 13 s on a long bore
    start = obj.Shape.getElement(edge)
    along = App.Vector(to_other)
    along.normalize()
    place = App.Placement(start.Curve.Center + along * a0, App.Rotation(App.Vector(0, 0, 1), along))
    teeth.transformShape(place.toMatrix())
    return _inside_the_mouth(teeth, obj.Shape, face, along, face_len)


def _swept(face, way):
    """A face swept along `way` as a solid, or None."""
    try:
        prism = face.extrude(way)
        if prism.Volume < 0:
            prism.reverse()
        return prism if prism.isValid() and prism.Volume > 1e-9 else None
    except Exception:
        return None


def _inside_the_mouth(teeth, base, face, axis, face_len):
    """The teeth cut where they run out of the round face past an end that is no circle square to its axis (the
    user's hole with its mouth in a curved face: the teeth ran on into the air): each face of the part met at such an
    end, swept along the axis away from the hole, is air the teeth may not reach."""
    ragged = []
    for e in face.Edges:
        c = e.Curve
        if c.TypeId == "Part::GeomLine" and abs(abs(c.Direction.dot(axis)) - 1.0) < 1e-6:
            continue  # the seam
        if c.TypeId == "Part::GeomCircle" and abs(abs(c.Axis.dot(axis)) - 1.0) < 1e-6:
            continue
        ragged.append(e)
    if not ragged:
        return teeth
    middle = face.CenterOfMass
    radius = face.Surface.Radius
    span = face_len + 4 * radius + 10.0
    air = []
    for e in ragged:
        out = axis if (e.CenterOfMass - middle).dot(axis) > 0 else axis * -1
        # the part's face round the mouth only, up to just past the edge: a whole round face swept went invalid,
        # and its far side (across a bore the hole breaks into) is no mouth
        ends = [(p - middle).dot(out) for p in e.discretize(24)]
        column = Part.makeCylinder(radius + 2.0, max(ends) + 1.0, middle, out)
        for other in base.Faces:
            if other.isSame(face) or not any(e.isSame(x) for x in other.Edges):
                continue
            try:  # its whole surface: the mouth itself is a hole in the face, and swept with it the air had a hole
                whole = other.Surface.toShape(*other.ParameterRange)  # just where the teeth stuck out
            except Exception:
                whole = other
            try:
                for piece in whole.common(column).Faces:
                    prism = _swept(piece, out * span)
                    if prism is not None:
                        air.append(prism)
            except Exception:
                pass
    if not air:
        return teeth
    tool = air[0] if len(air) == 1 else air[0].fuse(air[1:])
    pieces = []
    for piece in teeth.Solids:  # each by itself: the turns overlap where they meet, and a cut of them all at once
        try:                     # took only part of what lies out
            left = piece.cut(tool) if piece.BoundBox.intersect(tool.BoundBox) else piece
        except Exception:
            left = piece
        pieces += [s for s in left.Solids if s.Volume > 1e-6]
    return Part.makeCompound(pieces) if pieces else teeth


def _joined(a, b):
    """a fused with b (a compound's solids: all in one fuse), one solid: a plain fuse of a flat-flanked tooth into a
    rod came back invalid, in two pieces; a fuse with a tenth of a micron's tolerance joins them."""
    tools = b.Solids if isinstance(b, Part.Compound) else b

    def wrong(shape):  # (it even came back valid, one solid: the teeth alone, the rod lost)
        box = App.BoundBox(a.BoundBox)
        box.enlarge(-1e-4)
        return not shape.isValid() or len(shape.Solids) != 1 or not shape.BoundBox.isInside(box)
    shape = a.fuse(tools)
    if wrong(shape):
        shape = a.fuse(tools, 1e-4)
    return shape.Solids[0] if len(shape.Solids) == 1 else shape


def finish_previews(doc):
    """Grown threads shown in their dialog standing on the part (no fuse: 13 s at every value on a long bore — the
    fuse of FreeCAD's own helix takes as long) are joined to it now, once."""
    names = [n for d, n in list(_PREVIEW) if d == doc.Name]
    for n in names:
        _PREVIEW.discard((doc.Name, n))
        o = doc.getObject(n)
        if o is not None:
            o.touch()
    if names:
        doc.recompute()


class GrownThread:
    """The proxy of a grown thread. FreeCAD saves its module path (incad.features.thread) and class name in the
    file."""

    def __init__(self, obj):
        if "Height" not in obj.PropertiesList:
            obj.addProperty("App::PropertyLength", "Height", "Thread", "The thread's length (Distance)")
        obj.Proxy = self

    def execute(self, obj):
        teeth = grown_teeth(obj)
        base = obj.BaseFeature
        has_base = base is not None and not base.Shape.isNull() and base.Shape.Solids
        if has_base and (obj.Document.Name, obj.Name) in _PREVIEW and safe.busy():
            shape = Part.makeCompound([base.Shape] + teeth.Solids)  # the dialog's preview: the fuse comes on OK
        elif has_base:
            shape = _joined(base.Shape, teeth)
        else:
            shape = _joined(teeth.Solids[0], teeth.Solids[1:]) if len(teeth.Solids) > 1 else teeth
        if hasattr(obj, "AddSubShape"):
            try:
                obj.AddSubShape = teeth
            except Exception:
                pass
        obj.Shape = shape

    def dumps(self):
        return None

    def loads(self, state):
        return None

HELPER = "InCadHelperOf"  # on objects InCad makes for a feature (the thread's chamfer): shown under it, deleted with it
ROLE = "InCadHelperRole"  # "prep" (fill / bore before the thread); none = the chamfer after it


def helpers_of(f):
    body = f.getParentGeoFeatureGroup()
    return [o for o in (body.Group if body is not None else []) if getattr(o, HELPER, None) == f.Name]


def _role(g):
    return getattr(g, ROLE, "") or "chamfer"


def _remove_helpers(doc, body, f, role=None):
    for g in helpers_of(f):
        if role is not None and _role(g) != role:
            continue
        prof = g.Profile[0] if isinstance(g.Profile, (tuple, list)) and g.Profile else None
        if body.Tip is g:
            body.Tip = g.BaseFeature if g.BaseFeature is not None else f
        for o in (g, prof):
            if o is not None and doc.getObject(o.Name) is not None:
                body.removeObject(o)
                doc.removeObject(o.Name)


def _chamfer(doc, body, f, obj, edge, internal, radii, sign, face_len, start_free, end_free, mode, size,
             to_other=None, before=False, grown=None):
    """A 45° lead-in chamfer at the free end(s) of the threaded face: a revolve cut (Groove) of a triangle on the
    thread's own section plane, so it needs no face names and the thread cuts through it. None when there is
    nothing to chamfer (no free end asked for). before: right before the thread instead. grown (the teeth's height
    and a pitch): the cone goes on out past the teeth's tips and takes all a grown thread leaves past the end too."""
    ends = []
    if mode in ("start", "both") and start_free:
        ends.append((0.0, 1.0, radii[0]))
    if mode == "both" and end_free:
        ends.append((face_len, -1.0, radii[1]))
    if not ends or size <= 0:
        _remove_helpers(doc, body, f, "chamfer")
        return None
    existing = [h for h in helpers_of(f) if _role(h) == "chamfer"]
    if existing and before != (f.BaseFeature is existing[0]):  # on the other side of its thread: made again
        _remove_helpers(doc, body, f, "chamfer")
        existing = []
    g = existing[0] if existing else None
    sk = g.Profile[0] if g is not None and isinstance(g.Profile, (tuple, list)) and g.Profile else None
    old_tip = body.Tip
    if g is None:
        if not before:
            body.Tip = f  # the chamfer goes right after its thread
        sk = body.newObject("Sketcher::SketchObject", "ThreadChamferProfile")
    sk.AttachmentSupport = [(obj, edge)]
    sk.MapMode = "SectionOfRevolution"
    sk.deleteAllGeometry()
    sk.recompute()  # the sketch alone: no feature is recomputed with an empty profile
    if sign is None:  # (a grown thread has no profile sketch of its own to tell the way along the face)
        ly = sk.getGlobalPlacement().Rotation.multVec(App.Vector(0, 1, 0))
        sign = 1.0 if to_other.dot(ly) > 0 else -1.0
    inward = size if internal else -size  # the end face is inside the shaft, outside the hole
    for at, into, radius in ends:
        y0 = sign * at
        if grown:  # the 45° cone from the corner on out past the teeth, and everything past the end face
            out = -(grown + 1.0) if internal else grown + 1.0
            far = sign * into * (2 * grown + size + 2.0)
            pts = [App.Vector(radius + inward, y0 - far, 0), App.Vector(radius + out, y0 - far, 0),
                   App.Vector(radius + out, y0 + sign * into * (size + abs(out)), 0), App.Vector(radius + inward, y0, 0)]
        else:
            pts = [App.Vector(radius, y0, 0), App.Vector(radius + inward, y0, 0),
                   App.Vector(radius, y0 + sign * into * size, 0)]
        sk.addGeometry([Part.LineSegment(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))])
    sk.Visibility = False
    if g is None and before:
        g = doc.addObject("PartDesign::Groove", "ThreadChamfer")
        base = f.BaseFeature
        body.insertObject(g, f, False)  # right before the thread, which grows its teeth on the chamfered part
        g.BaseFeature = base
        f.BaseFeature = g
        g.addProperty("App::PropertyString", HELPER, "InCad", "The thread this chamfer belongs to")
        g.setPropertyStatus(HELPER, "Hidden")
        setattr(g, HELPER, f.Name)
        g.Profile = sk
        g.Angle = 360.0
        g.Visibility = False
        body.Tip = old_tip
    elif g is None:
        g = body.newObject("PartDesign::Groove", "ThreadChamfer")
        g.addProperty("App::PropertyString", HELPER, "InCad", "The thread this chamfer belongs to")
        g.setPropertyStatus(HELPER, "Hidden")
        setattr(g, HELPER, f.Name)
        g.Profile = sk
        g.Angle = 360.0
        body.Tip = g if old_tip is f else old_tip
    g.ReferenceAxis = (sk, ["V_Axis"])
    doc.recompute()
    if g.Shape.isNull() or not g.Shape.isValid():
        raise ValueError("The chamfer can't be built with that size")
    return g


def _prep(doc, body, f, obj, edge, radius, hole, sign, a0, a1, face_len, start_free, end_free, internal=True,
          pitch=0.0):
    """Fill the hole (a Revolution) or bore it out (a Groove) to the thread's tap radius along the threaded span,
    right before the thread; removed when the hole already fits. (helper or None, (reaches the start, reaches
    the end)): a chamfer at an end the helper does not reach starts from the part's own radius."""
    existing = [h for h in helpers_of(f) if _role(h) == "prep"]
    adds = hole < radius if internal else hole > radius  # a hole filled in / a rod built up; else cut
    want = None if abs(hole - radius) < 1e-9 else ("PartDesign::Revolution" if adds else "PartDesign::Groove")
    if existing and (want is None or existing[0].TypeId != want):
        _remove_helpers(doc, body, f, "prep")
        existing = []
    if want is None:
        return None, (False, False)
    g = existing[0] if existing else None
    sk = (g.Profile[0] if isinstance(g.Profile, (tuple, list)) else g.Profile) if g is not None else \
        body.newObject("Sketcher::SketchObject", "ThreadPrepProfile")
    sk.AttachmentSupport = [(obj, edge)]
    sk.MapMode = "SectionOfRevolution"
    sk.deleteAllGeometry()
    sk.recompute()  # placed (the walls are sampled in its frame); no feature is recomputed with an empty profile
    lo, hi = (hole, radius + 0.02) if hole < radius else (radius - 0.02, hole)
    if want == "PartDesign::Revolution" and not internal:  # a rod built up: along it
        spans = [(max(a0, 0.0), min(a1, face_len))]
    elif want == "PartDesign::Revolution":  # new material only where the hole has a wall all round, joined to it
        spans = _walled(obj.Shape, sk.getGlobalPlacement(), sign, radius, max(a0, 0.0), min(a1, face_len))
    else:  # a cut may run past a free end, so the mouth opens cleanly; at a closed end (a shoulder) the thread's
        # last turn reaches it, so the cut does too (never into it)
        b0 = -0.2 if a0 <= 0.0 and start_free else 0.0 if a0 <= pitch else max(a0, 0.0)
        b1 = face_len + 0.2 if a1 >= face_len and end_free else face_len if a1 >= face_len - pitch else \
            min(a1, face_len)
        spans = [(b0, b1)]
    spans = [(b0, b1) for b0, b1 in spans if b1 - b0 > 1e-3]
    if not spans:
        if g is None:
            doc.removeObject(sk.Name)
        _remove_helpers(doc, body, f, "prep")
        return None, (False, False)
    if g is None:
        g = doc.addObject(want, "ThreadFill" if want == "PartDesign::Revolution" else
                          "ThreadBore" if internal else "ThreadTrim")
        base = f.BaseFeature
        body.insertObject(g, f, False)  # right before the thread, which cuts the filled / bored hole
        g.BaseFeature = base
        f.BaseFeature = g
        for prop, text in ((HELPER, "The thread this belongs to"), (ROLE, "What it does for the thread")):
            g.addProperty("App::PropertyString", prop, "InCad", text)
            g.setPropertyStatus(prop, "Hidden")
        setattr(g, HELPER, f.Name)
        setattr(g, ROLE, "prep")
        g.Profile = sk
        g.Angle = 360.0
        g.Refine = False
    for b0, b1 in spans:
        pts = [App.Vector(lo, sign * b0, 0), App.Vector(hi, sign * b0, 0), App.Vector(hi, sign * b1, 0),
               App.Vector(lo, sign * b1, 0)]
        sk.addGeometry([Part.LineSegment(pts[i], pts[(i + 1) % 4]) for i in range(4)])
    sk.Visibility = False
    g.ReferenceAxis = (sk, ["V_Axis"])
    g.Visibility = False
    return g, (any(b0 <= 1e-6 for b0, _ in spans), any(b1 >= face_len - 1e-6 for _, b1 in spans))


def _walled(solid, placement, sign, radius, a0, a1):
    """The stretches of [a0, a1] along the hole where it has material all round just outside its wall (a crossing
    bore or a slot opens a window: no fill there, it would grow into that space). In the section sketch's frame
    the hole's axis is the local y axis. One boolean of a thin tube round the wall: the points one by one took
    40 ms each on a part with a modeled thread, minutes on a long hole (the user: the program hung)."""
    if a1 - a0 < 0.3:
        return []
    step = 0.1
    tube = Part.makeCylinder(radius + 0.15, a1 - a0, App.Vector(0, sign * a0, 0), App.Vector(0, sign, 0)).cut(
        Part.makeCylinder(radius + 0.05, a1 - a0, App.Vector(0, sign * a0, 0), App.Vector(0, sign, 0)))
    tube.transformShape(placement.toMatrix())
    back = placement.inverse().toMatrix()
    windows = []
    for piece in tube.cut(solid).Solids:
        if piece.Volume < 1e-5:
            continue
        piece = piece.copy()
        piece.transformShape(back)
        bb = piece.BoundBox
        windows.append((min(sign * bb.YMin, sign * bb.YMax) - step, max(sign * bb.YMin, sign * bb.YMax) + step))
    spans, at = [], a0
    for w0, w1 in sorted(windows):
        if w0 > at:
            spans.append((at, min(w0, a1)))
        at = max(at, w1)
    if at < a1:
        spans.append((at, a1))
    return [(b0, b1) for b0, b1 in spans if b1 - b0 >= 0.3]


_MATES = {}  # (document, thread) -> (undo count, the threads made with it in its dialog, one per further face)


def _items(values, doc=None):
    """The faces picked, each named on the feature below the threads when a doc is given (a face clicked on the
    preview of another thread here, or on its fill: those are made again at every preview, the name would die)."""
    items = [i.strip() for i in (values.get("face") or "").split(",") if i.strip()]
    if doc is None:
        return items
    return [rehome(doc, dict(values, face=i, start_face=""))["face"] for i in items]


def adjusted(doc, f):
    """The dialog's Face list as the threads use it (faces on the part, not on a preview that is made again)."""
    faces = [_load(t).get("face") for t in [f] + companions(doc, f)]
    return {"face": ",".join(x for x in faces if x)} if all(faces) else {}


def _one(values, item):
    return dict(values, face=item)


def companions(doc, f):
    """The threads made with `f` in its open dialog (the further faces): shown and computed with it. Once that
    dialog's step is committed (OK) each is a thread of its own: a later edit of `f` is `f` alone."""
    count, names = _MATES.get((doc.Name, f.Name), (None, []))
    if count != doc.UndoCount or not doc.HasPendingTransaction:  # (the count moves on the step's first change)
        return []
    return [doc.getObject(n) for n in names if doc.getObject(n) is not None]


def _discard(doc, body, t):
    _remove_helpers(doc, body, t)
    prof = getattr(t, "Profile", None)
    sk = prof[0] if isinstance(prof, (tuple, list)) and prof else None
    if body.Tip is t:
        body.Tip = t.BaseFeature
    for obj in (t, sk):
        if obj is not None and doc.getObject(obj.Name) is not None:
            body.removeObject(obj)
            doc.removeObject(obj.Name)


def create(doc, body, profile_, values):
    """A thread on every face picked (one dialog for several holes or shafts); the first is the dialog's feature."""
    items = _items(values, doc) or [""]
    f = _create_one(doc, body, _one(values, items[0]))
    mates = []
    try:
        for item in items[1:]:
            mates.append(_create_one(doc, body, _one(values, item)).Name)
    except Exception:
        for t in reversed([doc.getObject(n) for n in mates] + [f]):
            _discard(doc, body, t)
        doc.recompute()
        raise
    _MATES[(doc.Name, f.Name)] = (doc.UndoCount, mates)
    return f


def _create_one(doc, body, values):
    values = rehome(doc, values)
    obj, _, face = _face(doc, values)  # refuse a wrong face or start face before anything is made
    _geometry(obj, face, bool(values.get("flip")), _plane_of(doc, values.get("start_face")))
    tip = body.Tip
    if values.get("build") == "grow":  # teeth that grow out of the surface and back into it: a feature of its own
        f = body.newObject(GROWN, "Thread")
        GrownThread(f)
        f.BaseFeature = tip
        from incad.core import document
        document.show_like(f, tip)  # (a new feature of its own shows nothing until it is given the part's look)
    else:
        f = body.newObject("PartDesign::SubtractiveHelix", "Thread")
    try:
        _build(doc, body, f, values)
    except Exception:
        prof = getattr(f, "Profile", None)
        sk = prof[0] if isinstance(prof, (tuple, list)) and prof else None
        _remove_helpers(doc, body, f)
        for obj in (f, sk):  # a refused thread leaves nothing behind
            if obj is not None and doc.getObject(obj.Name) is not None:
                body.removeObject(obj)
                doc.removeObject(obj.Name)
        body.Tip = tip
        doc.recompute()
        raise
    after = [h for h in helpers_of(f) if _role(h) == "chamfer"]
    body.Tip = after[-1] if after else f
    return f


def read(doc, f):
    data = _load(f)
    values = defaults(doc, None, None)
    values.update({k: v for k, v in data.items() if k in values or k == "internal"})
    if values.get("length_mode") == "distance":
        values["length"] = value_text(doc, f, "Height") or values["length"]
    return values


def update(doc, f, values):
    body = f.getParentGeoFeatureGroup()
    items = _items(values, doc) or [""]
    want = GROWN if values.get("build") == "grow" else "PartDesign::SubtractiveHelix"
    if f.TypeId != want:  # cut <-> grown, or grown by 0.14 (an AdditiveHelix trimmed at the ends): made again
        _discard(doc, body, f)
        doc.recompute()
        return create(doc, body, None, values)
    mates = companions(doc, f)
    first = _build(doc, body, f, _one(values, items[0]))
    kept = []
    for i, item in enumerate(items[1:]):
        if i < len(mates):
            kept.append(_build(doc, body, mates[i], _one(values, item)).Name)
        else:
            t = _create_one(doc, body, _one(values, item))
            kept.append(t.Name)
    for t in mates[len(items) - 1:]:  # a face taken off the list: its thread goes
        _discard(doc, body, t)
    _MATES[(doc.Name, f.Name)] = (doc.UndoCount, kept)
    return first


def _pad_of(f):
    return f


def _size_auto(f, values):
    """Was the size chosen from the shaft (nothing picked; or the dialog showing the size chosen before, kept)?"""
    prev = _load(f)
    return not values.get("size") or bool(prev.get("size_auto") and values.get("size") == prev.get("size"))


def _drifted(doc, f):
    """True when the shaft (or bore) under a cut thread is no longer what it was built on: Full length stopped at
    the old end of a longer shaft, and an M10 chosen for a 10 mm shaft stayed on a 12 mm one (the finder)."""
    data = _load(f)
    have = data.get("measured")
    if not have or data.get("build", "cut") != "cut" or f.TypeId != "PartDesign::SubtractiveHelix":
        return False
    try:
        values = read(doc, f)
        obj, _sub, face = _face(doc, values)
        _internal, radius, _edge, face_len = _geometry(obj, face, bool(values.get("flip")),
                                                       _plane_of(doc, values.get("start_face")))[:4]
    except Exception:
        return False
    length_matters = values.get("length_mode") != "distance"
    return abs(2 * radius - have[0]) > 1e-6 or (length_matters and abs(face_len - have[1]) > 1e-6)


def follow_part(doc):
    """After a recompute (incad.ui.shell): every cut thread whose shaft changed is built again from its own
    settings (a size chosen from the shaft chosen again; a size picked by hand that no longer fits is said).
    Returns the labels rebuilt."""
    from incad.core import safe
    todo = [o for o in doc.Objects if claims(o) and _drifted(doc, o)]
    done = []
    for f in todo:
        values = read(doc, f)
        if _load(f).get("size_auto"):
            values["size"] = ""  # chosen from the shaft: chosen again for the shaft as it is now
        r = safe.run_safely("%s follows the shaft" % f.Label, lambda t=f, v=values: update(doc, t, v), doc)
        if r.ok:
            done.append(f.Label)
    return done
