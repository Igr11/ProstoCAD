"""3D Model ▸ Create ▸ Emboss (Inventor's Emboss / Engrave, with Wrap to face): a profile — a Text or a sketch — raised
from or cut into a face of the part. On a round face (a cylinder or a cone) the profile is wrapped round it, its height along
the radius; on a flat face it goes straight; on a sphere it is projected along the sketch's normal (Evenly round is
greyed out there). A step at the end of the part's history (a PartDesign::FeaturePython built on the part before
it), with its profile under it in the browser like an Extrude's; it finds its face again by geometry (the same
cylinder, sphere or plane), so editing earlier features keeps it working. Geometry: incad.core.wrap."""
import json

import FreeCAD as App

from incad.features.common import Field, format_picks, parse_picks, resolve, value_param, value_text

TITLE = "Emboss"
ICON = "emboss"
TYPES = ("PartDesign::FeaturePython",)
MARK = "InCadEmboss"  # its settings (JSON): what tells an Emboss apart from the other Python features
PROPS = {"depth": "Depth"}
DEPTH_STEP = 0.05  # mm: the arrows' step of the depth (the user 2026-11-08; Shift ×10)

FIELDS = (
    Field("operation", "Operation", "choice", (("raise", "Emboss"), ("engrave", "Engrave")), "raise"),
    Field("depth", "Depth", "value_length", default="1 mm", step=lambda values: DEPTH_STEP),
    Field("face", "Face", "pick", pick="emboss_face"),
    Field("around", "Around", "choice", (("drawn", "As drawn"), ("even", "Evenly round")), "drawn"),
)
PRESELECT = {"face": "emboss_face"}
BODY_FROM = "face"  # it is made on the picked face's Solid; a profile sketched in another Solid comes along
SOLID_PICKS = True  # a click on the profile's own lines over the part means the part's face under them


def claims(f):
    return MARK in f.PropertiesList


def _settings(f):
    try:
        return json.loads(getattr(f, MARK, "") or "{}")
    except ValueError:
        return {}


def _vec(v):
    return [v.x, v.y, v.z]


def signature(face):
    """What the face is, without its number: the cylinder (axis, centre, radius) or the plane (normal, a point)."""
    from incad.core import wrap
    s = face.Surface
    if wrap.is_round(face):
        sig = {"kind": "round", "centre": _vec(s.Center), "axis": _vec(s.Axis), "radius": s.Radius}
        if s.TypeId == "Part::GeomCone":
            sig["angle"] = s.SemiAngle
        return sig
    if wrap.is_flat(face):
        n = face.normalAt(*s.parameter(face.CenterOfMass)[:2])
        return {"kind": "flat", "normal": _vec(n), "point": _vec(face.CenterOfMass)}
    if wrap.is_sphere(face):
        return {"kind": "sphere", "centre": _vec(s.Center), "radius": s.Radius}
    raise ValueError("Pick a round (cylinder, cone or sphere) or a flat face of the part")


def find(shape, sig):
    """The face of `shape` that is the signature's cylinder (coaxial; the nearest radius, as an earlier edit may
    have changed it) or plane (the same direction; the nearest one), or None."""
    from incad.core import wrap
    best, best_d = None, None
    for f in shape.Faces:
        try:
            if sig["kind"] == "round" and wrap.is_round(f) \
                    and (f.Surface.TypeId == "Part::GeomCone") == ("angle" in sig):
                s = f.Surface
                a, b = App.Vector(*sig["axis"]), App.Vector(s.Axis)
                if abs(abs(a.normalize().dot(b.normalize())) - 1) > 1e-6:
                    continue
                c = App.Vector(*sig["centre"])
                off = App.Vector(s.Center) - c
                if (off - a * off.dot(a)).Length > 1e-5:  # not on the same axis
                    continue
                d = abs(s.Radius - sig["radius"]) + (abs(s.SemiAngle - sig["angle"]) if "angle" in sig else 0)
            elif sig["kind"] == "sphere" and wrap.is_sphere(f):
                if (App.Vector(f.Surface.Center) - App.Vector(*sig["centre"])).Length > 1e-5:
                    continue
                d = abs(f.Surface.Radius - sig["radius"])
            elif sig["kind"] == "flat" and wrap.is_flat(f):
                n = f.normalAt(*f.Surface.parameter(f.CenterOfMass)[:2])
                want = App.Vector(*sig["normal"])
                if n.dot(want) < 1 - 1e-6:
                    continue
                d = abs((f.CenterOfMass - App.Vector(*sig["point"])).dot(want)) + \
                    (f.CenterOfMass - App.Vector(*sig["point"])).Length * 1e-3
            else:
                continue
        except Exception:
            continue
        if best_d is None or d < best_d:
            best, best_d = f, d
    return best


def unavailable(values):
    """'Evenly round' is greyed out while a sphere is picked: a projection has no way round to spread along."""
    from incad.core import wrap
    doc = App.ActiveDocument
    if doc is None:
        return {}
    for obj, sub in parse_picks(doc, values.get("face")):
        obj, sub = resolve(obj, sub)
        if obj is not None and sub.startswith("Face"):
            try:
                if wrap.is_sphere(obj.Shape.getElement(sub)):
                    return {"around": {"even"}}
            except Exception:
                pass
    return {}


def _profile_faces(profile):
    """The profile's closed shapes as faces, where the profile is (the Solid's frame): a Text's letters, a sketch's
    regions."""
    import Part
    from incad.core import regions
    if regions.is_profile(profile):  # an area copy: its source's own areas (two touching at a corner stay two)
        def made(sh):
            wires = [w for w in sh.Wires if w.isClosed()]
            return list(Part.makeFace(wires, "Part::FaceMakerBullseye").Faces) if wires else []
        return regions.faces_of(profile, made)
    sh = profile.Shape
    if sh.Faces:
        return list(sh.Faces)
    wires = [w for w in sh.Wires if w.isClosed()]
    if not wires:
        return []
    return list(Part.makeFace(wires, "Part::FaceMakerBullseye").Faces)


class EmbossFeature:
    """The proxy. FreeCAD saves its module path (incad.features.emboss) and class name in the file."""
    def __init__(self, obj):
        for prop, kind in (("Profile", "App::PropertyLink"), ("Depth", "App::PropertyLength")):
            if prop not in obj.PropertiesList:
                obj.addProperty(kind, prop, "Emboss", "")
        if MARK not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", MARK, "InCad", "The emboss's settings")
            obj.setPropertyStatus(MARK, "Hidden")
        obj.Proxy = self

    def execute(self, obj):
        from incad.core import wrap
        base = obj.BaseFeature
        if base is None or base.Shape.isNull():
            return
        s = _settings(obj)
        if obj.Depth.Value <= 0 or not s.get("face"):
            obj.Shape = base.Shape  # just made, its values not set yet
            return
        if obj.Profile is None:
            raise ValueError("The emboss has no profile (a Text or a sketch)")
        face = find(base.Shape, s.get("face") or {}) if s.get("face") else None
        if face is None:
            raise ValueError("The emboss's face is gone: pick another one")
        normal = obj.Profile.Placement.Rotation.multVec(App.Vector(0, 0, 1))  # the sketch's / Text's own side
        obj.Shape = wrap.apply(base.Shape, _profile_faces(obj.Profile), face, obj.Depth.Value,
                               s.get("operation") == "engrave", s.get("around") == "even", normal)

    def dumps(self):
        return None

    def loads(self, state):
        return None


def defaults(doc, body, profile=None):
    values = {f.key: f.default for f in FIELDS}
    values["face"] = ""
    return values


def _apply(doc, f, values):
    s = _settings(f)
    picks = parse_picks(doc, values.get("face"))
    if picks:
        obj, sub = resolve(*picks[0])
        el = obj.getSubObject(sub) if sub else None
        if el is None or el.ShapeType != "Face":
            raise ValueError("Pick a round (cylinder, cone or sphere) or a flat face of the part")
        s["face"] = signature(el)
        s["face_pick"] = values.get("face")
    s["operation"] = values.get("operation") or "raise"
    s["around"] = values.get("around") or "drawn"
    setattr(f, MARK, json.dumps(s))
    value_param(doc, f, "Depth", values.get("depth") or "1 mm", "length")


def pending(values):
    """Waiting for the face (the dialog opens before it is picked): a hint, OK off until then."""
    return "" if values.get("face") else "Click the face to emboss on (round or flat)"


def create(doc, body, profile, values):
    if profile is None:
        raise ValueError("Pick a Text or a sketch to emboss")
    tip = body.Tip
    f = body.newObject("PartDesign::FeaturePython", "Emboss")
    f.Label = TITLE
    EmbossFeature(f)
    f.BaseFeature = tip
    f.Profile = profile
    from incad.core import document
    document.show_like(f, tip)
    try:
        _apply(doc, f, values)
        body.Tip = f
        doc.recompute()
        document.show_like(f, tip)
        if "Invalid" in f.State:
            raise ValueError(f.getStatusString() or "The profile could not be embossed")
    except Exception:
        body.removeObject(f)
        doc.removeObject(f.Name)
        body.Tip = tip
        doc.recompute()
        raise
    return f


def update(doc, f, values):
    _apply(doc, f, values)
    doc.recompute()
    return f


def read(doc, f):
    s = _settings(f)
    values = defaults(doc, f.getParentGeoFeatureGroup())
    face = ""
    base = f.BaseFeature
    if base is not None and not base.Shape.isNull() and s.get("face"):
        found = find(base.Shape, s["face"])
        i = next((k for k, g in enumerate(base.Shape.Faces) if found is not None and g.isSame(found)), None)
        face = format_picks([(base, "Face%d" % (i + 1))]) if i is not None else s.get("face_pick", "")
    values.update(operation=s.get("operation") or "raise", face=face, around=s.get("around") or "drawn",
                  depth=value_text(doc, f, "Depth") or "%g mm" % f.Depth.Value)
    return values
