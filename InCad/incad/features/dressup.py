"""Fillet, Chamfer and Shell (Inventor) = PartDesign Fillet, Chamfer, Thickness on picked edges/faces."""
from types import SimpleNamespace

import FreeCAD as App
import Part

from incad.features.common import Field, fine_step, link_sub, parse_picks, resolve, value_param, value_text


def _base(doc, text, expected):
    """(obj, subs) of the picks; they must lie on `expected` — the shape the feature starts from."""
    pairs = [resolve(o, s) for o, s in parse_picks(doc, text)]
    obj, subs = link_sub(pairs)
    if expected is not None and obj is not expected:
        raise ValueError("Pick on the current shape (%s), not on %s" % (expected.Label, obj.Label))
    return obj, subs


def _past_helpers(f, obj):
    """The part's own feature under f's steps before it (a chamfer's Fit passes the part on unchanged)."""
    while obj is not None and f is not None and getattr(obj, "InCadHelperOf", None) == f.Name:
        obj = getattr(obj, "BaseFeature", None)
    return obj


def _expected(f, body=None):
    base = _past_helpers(f, getattr(f, "BaseFeature", None) if f is not None else None)
    return base if base is not None else (body.Tip if body is not None else None)


def _picks_text(f, prop="Base"):
    link = getattr(f, prop)
    if not link or link[0] is None:
        return ""
    obj, subs = _past_helpers(f, link[0]), link[1]
    return ",".join("%s:%s" % (obj.Name, s) for s in subs)


# ----- Fillet -----
def _fillet_create(doc, body, profile, values):
    if values.get("full"):  # Full: a full round between the two picked edges (incad.features.full_round)
        from incad.features import full_round
        return full_round.create(doc, body, None, {"edges": values.get("edges")})
    obj, subs = _base(doc, values.get("edges"), body.Tip)
    f = body.newObject("PartDesign::Fillet", "Fillet")
    f.Base = (obj, subs)
    value_param(doc, f, "Radius", values.get("radius") or "0.5 mm", "length")
    return f


def _fillet_read(doc, f):
    from incad.features import full_round
    if full_round.claims(f):
        return {"edges": full_round.read(doc, f)["edges"], "radius": "0.5 mm", "full": True}
    return {"edges": _picks_text(f), "radius": value_text(doc, f, "Radius") or "0.5 mm", "full": False}


def _fillet_claims(f):
    """A Fillet, or a Full Round (edited here with Full on)."""
    from incad.features import full_round
    return f.TypeId == "PartDesign::Fillet" or full_round.claims(f)


def _fillet_pending(values):
    from incad.features import full_round
    return full_round.pending(values) if values.get("full") else ""


def _fillet_switch(doc, f, values):
    """Full switched on or off on an existing feature: the Fillet and the Full Round swap places (the last step only:
    nothing may be built on it)."""
    body = f.getParentGeoFeatureGroup()
    users = [o.Label for o in f.InList if o is not body and getattr(o, "BaseFeature", None) is f]
    if users or body.Tip is not f:
        raise ValueError("Full can't be switched here: %s is built on this fillet" % (", ".join(users) or "the part"))
    label_was = f.Label
    body.removeObject(f)
    f.Document.removeObject(f.Name)
    new = _fillet_create(doc, body, None, values)
    if not values.get("full"):
        new.Label = label_was if "Full" not in label_was else new.Label
    return new


def _fillet_update(doc, f, values):
    from incad.features import full_round
    if bool(values.get("full")) != full_round.claims(f):
        return _fillet_switch(doc, f, values)
    if values.get("full"):
        return full_round.update(doc, f, {"edges": values.get("edges")})
    obj, subs = _base(doc, values.get("edges"), _expected(f))
    f.Base = (obj, subs)
    value_param(doc, f, "Radius", values.get("radius") or "0.5 mm", "length")
    return f


FILLET = SimpleNamespace(
    __name__="incad.features.fillet", TITLE="Fillet", ICON="fillet",
    TYPES=("PartDesign::Fillet", "PartDesign::FeaturePython"), claims=_fillet_claims,
    NEEDS_PROFILE=False, PROPS={"radius": "Radius"}, PRESELECT={"edges": "edge"}, SOLID_PICKS=True,
    RECREATE_ON=("full",), LOCKED_WHEN_EDITING=(), pending=_fillet_pending,
    PREVIEW_WHILE_PICKING=True,  # the rounded part shows while edges are picked; a click on it means the part's edge
    TANGENT_CHAIN=True,  # a click takes the edge's smooth chain (Inventor / Fusion)
    FACE_EDGES=True,  # a click on a face takes all its edges
    FIELDS=(Field("edges", "Edges", "picks", pick="edge"),
            Field("radius", "Radius", "value_length", default="0.5 mm", toggle=("full", "Full"), step=fine_step)),
    defaults=lambda doc, body, profile: {"edges": "", "radius": "0.5 mm", "full": False},
    create=_fillet_create, read=_fillet_read, update=_fillet_update, _pad_of=lambda f: f)


# ----- Chamfer -----
_CHAMFER = {"equal": "Equal distance", "two": "Two distances", "angle": "Distance and Angle"}


def _chamfer_apply(doc, f, values):
    from incad.features import chamfer_fit
    obj, subs = _base(doc, values.get("edges"), _expected(f))
    kind = values.get("type", "equal")
    f.ChamferType = _CHAMFER.get(kind, "Equal distance")
    f.FlipDirection = bool(values.get("flip"))
    value_param(doc, f, "Size", values.get("size") or "0.5 mm", "length")
    if kind == "two":
        value_param(doc, f, "Size2", values.get("size2") or "0.5 mm", "length")
    if kind == "angle":
        value_param(doc, f, "Angle", values.get("angle") or "45 °", "angle")
    f.Base = (chamfer_fit.sync(doc, f, obj, subs, values), subs)  # past a face: stops at its far edge


def _chamfer_create(doc, body, profile, values):
    f = body.newObject("PartDesign::Chamfer", "Chamfer")
    _chamfer_apply(doc, f, values)
    return f


def _chamfer_read(doc, f):
    kind = {v: k for k, v in _CHAMFER.items()}.get(f.ChamferType, "equal")
    return {"edges": _picks_text(f), "type": kind, "size": value_text(doc, f, "Size") or "0.5 mm",
            "size2": value_text(doc, f, "Size2") or "0.5 mm", "angle": value_text(doc, f, "Angle") or "45°",
            "flip": bool(f.FlipDirection)}


def _chamfer_update(doc, f, values):
    _chamfer_apply(doc, f, values)
    return f


CHAMFER = SimpleNamespace(
    __name__="incad.features.chamfer", TITLE="Chamfer", ICON="chamfer", TYPES=("PartDesign::Chamfer",),
    NEEDS_PROFILE=False, PROPS={"size": "Size", "size2": "Size2", "angle": "Angle"}, PRESELECT={"edges": "edge"},
    SOLID_PICKS=True, PREVIEW_WHILE_PICKING=True, TANGENT_CHAIN=True, FACE_EDGES=True,
    FIELDS=(Field("edges", "Edges", "picks", pick="edge"),
            Field("type", "Type", "choice", (("equal", "Distance"), ("two", "Two distances"),
                                             ("angle", "Distance + angle")), "equal"),
            Field("size", "Distance", "value_length", default="0.5 mm", step=fine_step),
            Field("size2", "Distance 2", "value_length", default="0.5 mm", show_if=("type", ("two",)), step=fine_step),
            Field("angle", "Angle", "value_angle", default="45 °", show_if=("type", ("angle",))),
            Field("flip", "Flip", "bool", default=False, show_if=("type", ("two", "angle")))),
    defaults=lambda doc, body, profile: {"edges": "", "type": "equal", "size": "0.5 mm", "size2": "0.5 mm",
                                         "angle": "45 °", "flip": False},
    create=_chamfer_create, read=_chamfer_read, update=_chamfer_update, _pad_of=lambda f: f)


# ----- Shell -----
def _shell_apply(doc, f, values):
    if values.get("closed"):  # no face removed: a closed hollow (InCad's own Shell; FreeCAD's Thickness can't)
        if SHELL_MARK not in f.PropertiesList:
            raise ValueError("This older Shell can't be made closed: delete it and make a new one")
        f.Base = None
    else:
        obj, subs = _base(doc, values.get("faces"), _expected(f))
        f.Base = (obj, subs)
    f.Reversed = values.get("direction", "inside") == "inside"  # probed: Reversed=True keeps the outer size
    value_param(doc, f, "Value", values.get("thickness") or "1 mm", "length")


SHELL_MARK = "InCadShell"  # on InCad's own Shell (a PartDesign::FeaturePython): how its last wall was made


class ShellFeature:
    """InCad's Shell on OpenCASCADE (incad.core.shell_solid): FreeCAD's Thickness properties (Base, Value, Reversed)
    so the dialog reads and writes it the same way, and the ways OCC can hollow a part tried in turn. The proxy;
    FreeCAD saves its module path and class name in the file."""
    def __init__(self, obj):
        for name, kind, doc in (("Base", "App::PropertyLinkSub", "The faces removed (none: a closed hollow)"),
                                ("Value", "App::PropertyLength", "The wall thickness"),
                                ("Reversed", "App::PropertyBool", "Inside: the outer size stays"),
                                (SHELL_MARK, "App::PropertyString", "How the wall was made")):
            if name not in obj.PropertiesList:
                obj.addProperty(kind, name, "Shell", doc)
        obj.setPropertyStatus(SHELL_MARK, "Hidden")
        obj.Proxy = self

    def execute(self, obj):
        from incad.core import shell_solid
        base = obj.BaseFeature
        if base is None or base.Shape.isNull():
            return
        link = obj.Base
        faces = []
        if link and link[0] is not None:
            src = link[0].Shape
            for sub in link[1]:
                try:
                    faces.append(src.getElement(sub))
                except Exception:
                    raise ValueError("A removed face (%s) is no longer on the part: pick the faces again" % sub)
        try:
            shape, how = shell_solid.make(base.Shape, faces, obj.Value.Value, bool(obj.Reversed))
        except ValueError:
            if _has_thread(base):
                raise ValueError("ProstoCAD can't hollow a part with a modeled thread in it: make the Shell before the "
                                 "thread (drag End of Part above the thread, make the Shell there, then move End of "
                                 "Part back)")
            raise
        setattr(obj, SHELL_MARK, how)
        obj.Shape = shape

    def dumps(self):
        return None

    def loads(self, state):
        return None


def _has_thread(feature):
    from incad.core import safe
    return safe._has_modeled_thread(feature)


def _shell_create(doc, body, profile, values):
    from incad.core import document
    tip = body.Tip
    f = body.newObject("PartDesign::FeaturePython", "Shell")
    f.Label = "Shell"
    ShellFeature(f)
    f.BaseFeature = tip
    body.Tip = f
    document.show_like(f, tip)
    _shell_apply(doc, f, values)
    return f


def _shell_read(doc, f):
    faces = _picks_text(f)
    return {"faces": faces, "thickness": value_text(doc, f, "Value") or "1 mm",
            "direction": "inside" if f.Reversed else "outside",
            "closed": SHELL_MARK in f.PropertiesList and not faces}


def _shell_update(doc, f, values):
    _shell_apply(doc, f, values)
    return f


def _first_thread(body):
    """The first modeled thread of the body (a helix feature, or a Hole with a modeled thread), or None."""
    for o in body.Group:
        if o.TypeId.endswith("Helix") or (o.TypeId == "PartDesign::Hole" and getattr(o, "ModelThread", False)):
            return o
    return None


def _solid_before(body, feature):
    from incad.core import document
    group = list(body.Group)
    before = [o for o in group[:group.index(feature)] if document._is_solid_feature(o)]
    return before[-1] if before else None


def _same_face(base, face):
    """'FaceN' of `base` that is the same face as `face` (of a later feature): same kind of surface, the
    picked face's centre on it; None when the earlier feature does not have it yet."""
    centre = face.CenterOfMass
    best, best_d = None, 1e-3
    for i, f in enumerate(base.Shape.Faces):
        if f.Surface.TypeId != face.Surface.TypeId:
            continue
        if face.Surface.TypeId == "Part::GeomPlane":
            n, m = f.normalAt(0, 0), face.normalAt(0, 0)
            if abs(n.dot(m)) < 1 - 1e-6 or abs((centre - f.CenterOfMass).dot(n)) > 1e-6:
                continue
            if f.isInside(centre, 1e-6, True):
                return "Face%d" % (i + 1)
        else:
            d = f.distToShape(Part.Vertex(centre))[0] if hasattr(Part, "Vertex") else 1.0
            if d < best_d:
                best, best_d = "Face%d" % (i + 1), d
    return best


def _chain_failure(body, f, old_tip, thread, thickness):
    """Reason text if a feature built on the hollowed part is unsound, else ''."""
    from incad.core import document
    group = list(body.Group)
    for o in group[group.index(f) + 1:]:
        if not o.isDerivedFrom("PartDesign::Feature"):
            continue
        shape = getattr(o, "Shape", None)
        if "Invalid" in o.State or shape is None or shape.isNull() or not shape.isValid():
            return "%s then fails on the hollowed part (wall %s)" % (o.Label, thickness)
        if o is old_tip:
            break
    if body.Tip is not old_tip:
        return "the part after it could not be rebuilt"
    return ""


def shell_before_thread(doc, body, values, search=True):
    """The way around a kernel that can't hollow a threaded part: End of Part goes above the first modeled
    thread, the Shell is made there (the picked faces found again on that earlier feature), and End of Part
    comes back to where it was. When the thread then fails on the hollowed part and `search` is on, half and a
    quarter of the wall are tried and the first that works is kept. Returns the Thickness (its Value is the
    wall used); raises when nothing works, naming the feature that fails."""
    from incad.core import document
    thread = _first_thread(body)
    if thread is None:
        raise ValueError("The part has no modeled thread to shell before")
    base = _solid_before(body, thread)
    if base is None:
        raise ValueError("Nothing to shell before %s" % thread.Label)
    old_tip = body.Tip
    picked = []
    for obj, sub in parse_picks(doc, values.get("faces")):
        if not sub.startswith("Face"):
            continue
        name = _same_face(base, obj.getSubObject(sub))
        if name is None:
            raise ValueError("%s of %s does not exist yet before %s: pick a face of the part's body"
                             % (sub, obj.Label, thread.Label))
        if name not in picked:
            picked.append(name)
    faces = ",".join("%s:%s" % (base.Name, n) for n in picked)
    units = doc.getObject("InCadModel") is None  # (the text keeps the user's unit; only the number varies)
    asked = App.Units.Quantity(values.get("thickness") or "1 mm")
    mm = asked.getValueAs("mm")

    def attempt(wall_mm):
        text = "%g mm" % wall_mm
        document.set_end_of_part(body, base)
        try:
            f = _shell_create(doc, body, None, dict(values, faces=faces, thickness=text))
            doc.recompute()
            if "Invalid" in f.State:
                raise ValueError("%s: %s" % (f.Label, f.getStatusString()))
        except Exception:
            document.set_end_of_part(body, old_tip)
            raise
        document.set_end_of_part(body, old_tip)
        doc.recompute()
        why = _chain_failure(body, f, old_tip, thread, text)
        if why:
            doc.removeObject(f.Name)
            document.set_end_of_part(body, old_tip)
            doc.recompute()
            return None, why
        return f, ""

    f, why = attempt(mm)
    if f is not None or not search:
        if f is None:
            raise ValueError("Shell before %s: %s. Use a thinner wall, or a Shell later in the design"
                             % (thread.Label, why))
        return f
    first_why, wall = why, mm
    for _ in range(2):  # a thinner wall, twice (every attempt recomputes the thread: seconds each)
        wall = wall / 2.0
        f, why = attempt(wall)
        if f is not None:
            return f
    raise ValueError("Shell before %s: %s, and so do walls of %g mm and %g mm"
                     % (thread.Label, first_why, mm / 2.0, mm / 4.0))


def _shell_rescue(doc, body, values, error):
    """The dialog's way around a kernel that can't hollow a threaded part: shell before the thread."""
    if "above the thread" not in error or _first_thread(body) is None:
        return None
    thread = _first_thread(body)

    def go():
        f = shell_before_thread(doc, body, values)
        asked = values.get("thickness") or "1 mm"
        used = "%g mm" % f.Value.Value
        f.InCadRescueNote = ("Shell made before %s with %s walls" % (thread.Label, used)
                             + ("" if App.Units.Quantity(asked).getValueAs("mm") <= f.Value.Value + 1e-9
                                else " (%s fails on the thread there)" % asked))
        return f
    return ("Shell before %s" % thread.Label, go)


SHELL = SimpleNamespace(
    __name__="incad.features.shell", TITLE="Shell", ICON="shell", TYPES=("PartDesign::Thickness", "PartDesign::FeaturePython"),
    rescue=_shell_rescue,
    claims=lambda f: not getattr(f, "InCadHelperOf", None) and (  # a Sweep's wall is edited from its Sweep
        f.TypeId == "PartDesign::Thickness" or SHELL_MARK in f.PropertiesList),
    NEEDS_PROFILE=False, PROPS={"thickness": "Value"}, PRESELECT={"faces": "face"}, SOLID_PICKS=True,
    PREVIEW_WHILE_PICKING=True,  # the hollow part shows while faces are picked; a click on it means the part's face
    FIELDS=(Field("closed", "No opening", "bool", default=False),  # a closed hollow (Inventor; Thickness can't)
            Field("faces", "Remove faces", "picks", pick="face", show_if=("closed", (False,))),
            Field("thickness", "Thickness", "value_length", default="1 mm"),
            Field("direction", "Direction", "choice", (("inside", "Inside"), ("outside", "Outside")), "inside")),
    defaults=lambda doc, body, profile: {"closed": False, "faces": "", "thickness": "1 mm", "direction": "inside"},
    create=_shell_create, read=_shell_read, update=_shell_update, _pad_of=lambda f: f)
