"""Move Face (direct editing): flat faces moved by a distance or up to a parallel face / plane, the neighbours
following Inventor's way, as a straight Step, or Together with the chamfers / fillets next to them. A step at the tip
(a PartDesign::FeaturePython) that finds its faces again by geometry on every recompute (incad.core.move_face), so it
never depends on face names."""
import json

import FreeCAD as App

from incad.core import move_face as mf
from incad.core import round_wall as rw
from incad.features.common import Field, parse_picks, resolve, value_param, value_text

TITLE = "Move Face"
ICON = "move_face"
FINISH = "feature.finish_move"  # on top of the view's right-click menu while the dialog is open: the same as OK
TYPES = ("PartDesign::FeaturePython",)
NEEDS_PROFILE = False
STRUCTURAL = ()
PROPS = {"distance": "Distance"}
PRESELECT = {"faces": "face"}  # faces selected before the command fill the field (key: the pick kind, as Shell's)
_STORE = "InCadMoveFace"
SOLID_PICKS = True  # its edge / face picks are on the part: a sketch line over an edge stands for that edge
OTHER_SOLID_MSG = "The faces must be on this Solid"

FIELDS = (
    Field("faces", "Faces", "picks", pick="face"),
    Field("extent", "Extent", "choice", (("distance", "Distance"), ("to", "To")), "distance"),
    # starts at 0: nothing moves until a value is typed or the arrow dragged (5 mm opened refused on small parts)
    Field("distance", "Distance", "value_length", default="0 mm", show_if=("extent", ("distance",))),
    Field("to", "To", "pick", pick="move_to", show_if=("extent", ("to",))),
    Field("mode", "Neighbours", "choice", (("inventor", "Inventor"), ("step", "Step"), ("together", "Together")),
          "inventor"),
)


def _load_json(text, default):
    try:
        return json.loads(text or "")
    except ValueError:
        return default


class MoveFaceFeature:
    """The proxy. FreeCAD saves its module path (incad.features.move_face) and class name in the file."""
    def __init__(self, obj):
        for prop, kind in (("Distance", "App::PropertyDistance"), ("Mode", "App::PropertyString"),
                           ("Extent", "App::PropertyString"), ("Target", "App::PropertyLinkSubGlobal"),
                           ("Signatures", "App::PropertyString")):
            if prop not in obj.PropertiesList:
                obj.addProperty(kind, prop, "Move Face", "")
        obj.setPropertyStatus("Signatures", "Hidden")
        obj.Proxy = self

    def execute(self, obj):
        base = obj.BaseFeature
        if base is None or base.Shape.isNull():
            return
        shape = base.Shape
        walls = _walls(obj, shape)
        if walls:
            obj.Shape = rw.offset(shape, walls, obj.Distance.Value, obj.Mode or "inventor")
            return
        faces = _faces(obj, shape)
        if obj.Extent == "to":
            d = mf.gaps_to(faces, _target(obj))
        else:
            d = obj.Distance.Value
        obj.Shape = mf.apply(shape, faces, d, obj.Mode or "inventor")

    def dumps(self):
        return None

    def loads(self, state):
        return None


def _faces(obj, shape):
    """The faces to move, found again in `shape` (the part before this step) by their stored geometry."""
    faces = []
    for sig in _load_json(obj.Signatures, []):
        if _is_wall_sig(sig):
            continue
        f = mf.find(shape, sig)
        if f is None:
            raise ValueError(mf.GONE_MSG)
        faces.append(f)
    if not faces:
        raise ValueError(mf.FACES_MSG)
    return faces


def _frame(obj):
    """Global placement of the frame `obj`'s shape is in: its Solid's (a feature's shapes are in the body's frame)."""
    group = obj.getParentGeoFeatureGroup()
    return group.getGlobalPlacement() if group is not None else App.Placement()


def _target(obj):
    """The To target in this Solid's frame: (point, normal) for a work / origin plane or a flat face (of any Solid);
    the edge or vertex itself (moved into this frame) for an edge, a corner point or a work point."""
    link = obj.Target
    if not link or link[0] is None:
        raise ValueError(mf.PARALLEL_MSG)
    target, subs = link[0], link[1]
    to_body = _frame(obj).inverse()
    if subs and subs[0]:
        try:
            face = target.Shape.getElement(subs[0])
        except Exception:  # the face picked as To is no longer there
            raise ValueError(mf.PARALLEL_MSG)
        pl = to_body.multiply(_frame(target))  # the target's Solid frame -> this Solid's frame
        if subs[0].startswith(("Edge", "Vertex")):
            el = face.copy()
            el.Placement = pl.multiply(el.Placement)
            return el
        if not mf._is_plane(face):
            raise ValueError(mf.PARALLEL_MSG)
        return pl.multVec(face.CenterOfMass), pl.Rotation.multVec(face.normalAt(0, 0))
    pl = to_body.multiply(target.getGlobalPlacement())
    if target.TypeId in ("PartDesign::Point", "App::Point"):  # a work point: its place
        import Part
        return Part.Vertex(pl.Base)
    # a datum plane: its placement's Z axis
    return pl.Base, pl.Rotation.multVec(App.Vector(0, 0, 1))


def _is_wall_sig(sig):
    return isinstance(sig, dict) and sig.get("kind") == "wall"


def _walls(obj, shape):
    """The stored round walls found again in `shape` (one face of each)."""
    out = []
    for sig in _load_json(obj.Signatures, []):
        if _is_wall_sig(sig):
            f = rw.find(shape, sig)
            if f is None:
                raise ValueError(mf.GONE_MSG)
            out.append(f)
    return out


def unavailable(values):
    """'To' is greyed out while a round wall is picked (Distance only for round walls)."""
    doc = App.ActiveDocument
    if doc is None:
        return {}
    for obj, sub in parse_picks(doc, values.get("faces")):
        obj, sub = resolve(obj, sub)
        if obj is not None and sub.startswith("Face"):
            try:
                if rw.is_round_wall(obj.Shape.getElement(sub)):
                    return {"extent": {"to"}}
            except Exception:
                pass
    return {}


def adjusted(doc, f):
    """Values the dialog shows after a preview: Neighbours switches to Together when a chamfer / fillet was picked
    with its face."""
    return {"mode": f.Mode} if f.Mode else {}


def claims(feature):
    return hasattr(feature, _STORE)


def defaults(doc, body, profile=None):
    values = {f.key: f.default for f in FIELDS}
    values.update(faces="", to="")
    return values


def _picked_faces(doc, text, body):
    """The picked faces; one of another Solid is refused (its geometry, in that Solid's frame, would be matched to a
    face of this one)."""
    out = []
    for obj, sub in parse_picks(doc, text):
        obj, sub = resolve(obj, sub)
        if obj is None or not sub.startswith("Face"):
            continue
        if obj.getParentGeoFeatureGroup() != body:
            raise ValueError(OTHER_SOLID_MSG)
        out.append(obj.Shape.getElement(sub))
    return out


def _store(f, data):
    if not hasattr(f, _STORE):
        f.addProperty("App::PropertyString", _STORE, "InCad", "InCad move face settings")
        f.setPropertyStatus(_STORE, "Hidden")
    setattr(f, _STORE, json.dumps(data, sort_keys=True))


def _picked_shape(doc, text):
    """The shape the picked faces are elements of (their neighbours are looked up in it)."""
    for obj, sub in parse_picks(doc, text):
        obj, sub = resolve(obj, sub)
        if obj is not None and sub.startswith("Face"):
            return obj.Shape
    return None


def _build(doc, f, values):
    picked = _picked_faces(doc, values.get("faces"), f.getParentGeoFeatureGroup())
    shape = _picked_shape(doc, values.get("faces"))
    walls = [x for x in picked if rw.is_round_wall(x)]
    if walls:
        if any(mf._is_plane(x) for x in picked):
            raise ValueError(rw.MIXED_MSG)
        if len(walls) != len(picked):
            raise ValueError(rw.NOT_STRAIGHT_MSG)
        sigs, seen = [], []
        for w in walls:  # one signature per wall (two clicked pieces of one ring are one wall)
            faces, _ = rw.pieces(shape, w)
            if any(any(x.isSame(y) for y in seen) for x in faces):
                continue
            seen += faces
            sigs.append(rw.signature(shape, w))
        f.Signatures = json.dumps(sigs)
        f.Mode = values.get("mode") or "inventor"
        f.Extent = "distance"
        f.Target = None
        value_param(doc, f, "Distance", values.get("distance") or "0 mm", "length")
        _store(f, {"faces": values.get("faces") or "", "to": ""})
        return f
    if picked and not any(mf._is_plane(x) for x in picked):
        raise ValueError(rw.NOT_STRAIGHT_MSG)
    faces, blends = mf.split_blends(shape, picked)
    mf.check_faces(faces)
    f.Signatures = json.dumps([mf.signature(face) for face in faces])
    # a chamfer / fillet picked with its face goes with it: Together (it moves every blend around the face)
    f.Mode = "together" if blends else (values.get("mode") or "inventor")
    f.Extent = values.get("extent") or "distance"
    if f.Extent == "to":
        picks = parse_picks(doc, values.get("to"))
        if not picks:
            raise ValueError(mf.PARALLEL_MSG)
        obj, sub = resolve(*picks[0])
        f.Target = (obj, [sub] if sub else [])
    else:
        f.Target = None  # a To edited back to Distance: no dead link to the plane / another Solid
        value_param(doc, f, "Distance", values.get("distance") or "0 mm", "length")
    _store(f, {"faces": values.get("faces") or "", "to": values.get("to") or ""})
    return f


def create(doc, body, profile, values):
    tip = body.Tip
    f = body.newObject("PartDesign::FeaturePython", "MoveFace")
    f.Label = TITLE  # the browser row: "Move Face", "Move Face001" (FreeCAD makes it unique)
    MoveFaceFeature(f)
    f.BaseFeature = tip
    from incad.core import document
    document.show_like(f, tip)  # the view proxy first: FreeCAD then draws it in the base's display mode
    try:
        _build(doc, f, values)
        body.Tip = f
        doc.recompute([f, f.getParentGeoFeatureGroup()])  # (what is built on this Solid follows on OK)
        document.show_like(f, tip)
        if "Invalid" in f.State:
            raise ValueError(f.getStatusString() or "The faces could not be moved")
    except Exception:
        body.removeObject(f)
        doc.removeObject(f.Name)
        body.Tip = tip
        raise  # the step is taken back by the caller, its Solid with it
    return f


def _current_picks(f):
    """The faces as picks on the part before this step, named by where their geometry is now (an earlier feature
    edited since may have renumbered them); None when one is gone."""
    base = f.BaseFeature
    if base is None or base.Shape.isNull():
        return None
    try:
        walls = _walls(f, base.Shape)
    except ValueError:
        return None
    if walls:
        names = []
        for w in walls:
            i = next((k for k, g in enumerate(base.Shape.Faces) if g.isSame(w)), None)
            if i is None:
                return None
            names.append("%s:Face%d" % (base.Name, i + 1))
        return ",".join(names)
    try:
        faces = _faces(f, base.Shape)
    except ValueError:
        return None
    names = []
    for face in faces:
        i = next((k for k, g in enumerate(base.Shape.Faces) if g.isSame(face)), None)
        if i is None:
            return None
        names.append("%s:Face%d" % (base.Name, i + 1))
    return ",".join(names)


def read(doc, f):
    data = _load_json(getattr(f, _STORE, ""), {})
    values = defaults(doc, f.getParentGeoFeatureGroup(), None)
    # a face gone: the picks as last made, so the edit shows what was picked (and its error) instead of nothing
    values.update(faces=_current_picks(f) or data.get("faces", ""), to=data.get("to", ""), mode=f.Mode or "inventor",
                  extent=f.Extent or "distance")
    values["distance"] = value_text(doc, f, "Distance") or "%g mm" % f.Distance.Value
    return values


def update(doc, f, values):
    _build(doc, f, values)
    doc.recompute([f, f.getParentGeoFeatureGroup()])
    if "Invalid" in f.State:
        raise ValueError(f.getStatusString() or "The faces could not be moved")
    return f


def param_names(f):
    from incad.core import model
    name = model.bound_parameter(f, "Distance")
    return {"distance": name} if name else {}


def arrow(f, distance=None):
    """The drag arrow's place: the first face's centre and outward normal, in global coordinates (after the move;
    `distance`: moved by that instead, the last distance that worked after a refused one). None while the step has
    no shape, its face is gone or its To target gives no gap."""
    sigs = _load_json(getattr(f, "Signatures", ""), [])
    base = f.BaseFeature
    if not sigs or f.Shape.isNull() or base is None or base.Shape.isNull():
        return None
    if _is_wall_sig(sigs[0]):
        face = rw.find(base.Shape, sigs[0])
        if face is None:
            return None
        d = f.Distance.Value if distance is None else distance
        point, n = rw.arrow(base.Shape, face, d)
        to_global = _frame(f)
        return to_global.multVec(point), to_global.Rotation.multVec(n)
    face = mf.find(base.Shape, sigs[0])  # where the face is now (an earlier feature may have moved it)
    if face is None:
        return None
    n = App.Vector(*sigs[0]["n"])
    d = f.Distance.Value if distance is None else distance
    if f.Extent == "to" and distance is None:
        try:
            d = mf.gap_to([face], _target(f))
        except ValueError:
            return None
    to_global = _frame(f)
    return to_global.multVec(face.CenterOfMass + n * d), to_global.Rotation.multVec(n)


def overlay(shell, session):
    """While the dialog is open: the drag arrow on the first face with the distance at its tip."""
    from incad.ui.move_face_arrow import FaceArrow
    return FaceArrow(shell, session)
