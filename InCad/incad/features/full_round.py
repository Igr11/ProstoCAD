"""3D Model ▸ Modify ▸ Fillet ▸ Full Round Fillet (Inventor's Full Round; the user: two fillets on a rectangle meeting
on its face): a flat face between two parallel faces becomes a half cylinder running on into both. A step at the end
of the part's history (a PartDesign::FeaturePython built on the part before it) that finds its face again by
geometry (the same plane), so editing earlier features keeps it working. Geometry: incad.core.full_round."""
import json

import FreeCAD as App

from incad.features.common import Field, format_picks, parse_picks, resolve

TITLE = "Full Round Fillet"  # picked by the two edges to round between (the user)
ICON = "fillet"
TYPES = ("PartDesign::FeaturePython",)
MARK = "InCadFullRound"  # its settings (JSON): what tells a Full Round apart from the other Python features
NEEDS_PROFILE = False
PROPS = {}
FIELDS = (Field("edges", "Edges", "picks", pick="edge"),)
PRESELECT = {"edges": "edge"}


def claims(f):
    return MARK in f.PropertiesList


def _settings(f):
    try:
        return json.loads(getattr(f, MARK, "") or "{}")
    except ValueError:
        return {}


class FullRoundFeature:
    """The proxy. FreeCAD saves its module path (incad.features.full_round) and class name in the file."""
    def __init__(self, obj):
        if MARK not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", MARK, "InCad", "The full round's settings")
            obj.setPropertyStatus(MARK, "Hidden")
        obj.Proxy = self

    def execute(self, obj):
        from incad.core import full_round
        from incad.features import emboss
        base = obj.BaseFeature
        if base is None or base.Shape.isNull():
            return
        s = _settings(obj)
        if not s.get("face"):
            obj.Shape = base.Shape  # just made, its face not picked yet
            return
        face = emboss.find(base.Shape, s["face"])
        if face is None:
            raise ValueError("The full round's edges are gone: pick others")
        obj.Shape = full_round.apply(base.Shape, face, App.Vector(*s["across"]) if s.get("across") else None)

    def dumps(self):
        return None

    def loads(self, state):
        return None


def defaults(doc, body, profile=None):
    return {"edges": ""}


def _apply(doc, f, values):
    from incad.core import full_round
    from incad.features import emboss
    s = _settings(f)
    picks = parse_picks(doc, values.get("edges"))
    if len(picks) == 2:
        (o1, s1), (o2, s2) = (resolve(*p) for p in picks)
        e1, e2 = (o.getSubObject(sub) if sub else None for o, sub in ((o1, s1), (o2, s2)))
        if e1 is None or e2 is None or e1.ShapeType != "Edge" or e2.ShapeType != "Edge" or o1 is not o2:
            raise ValueError(full_round.EDGES_MSG)
        face, across = full_round.between(o1.Shape, e1, e2)
        s["face"] = emboss.signature(face)
        s["across"] = [across.x, across.y, across.z]
        s["edges_pick"] = values.get("edges")
    elif picks:
        raise ValueError(full_round.EDGES_MSG)
    setattr(f, MARK, json.dumps(s))


def pending(values):
    """Waiting for the face (the dialog opens before it is picked): a hint, OK off until then."""
    n = len([i for i in (values.get("edges") or "").split(",") if i.strip()])
    return "" if n == 2 else "Click the two parallel edges to round off between (the end of a bar, a plate's side)"


def create(doc, body, profile, values):
    tip = body.Tip
    f = body.newObject("PartDesign::FeaturePython", "FullRound")
    f.Label = TITLE
    FullRoundFeature(f)
    f.BaseFeature = tip
    from incad.core import document
    document.show_like(f, tip)
    try:
        _apply(doc, f, values)
        body.Tip = f
        doc.recompute([f, f.getParentGeoFeatureGroup()])  # (what is built on this Solid follows on OK)
        document.show_like(f, tip)
        if "Invalid" in f.State:
            raise ValueError(f.getStatusString() or "The face could not be rounded off")
    except Exception:
        body.removeObject(f)
        doc.removeObject(f.Name)
        body.Tip = tip
        raise  # the step is taken back by the caller, its Solid with it
    return f


def update(doc, f, values):
    _apply(doc, f, values)
    doc.recompute([f, f.getParentGeoFeatureGroup()])  # (what is built on this Solid follows on OK)
    return f


def read(doc, f):
    from incad.features import emboss
    from incad.core import full_round
    s = _settings(f)
    edges = s.get("edges_pick", "")
    base = f.BaseFeature
    if base is not None and not base.Shape.isNull() and s.get("face"):
        found = emboss.find(base.Shape, s["face"])
        try:
            (a, b), _, _ = full_round.sides(base.Shape, found, App.Vector(*s["across"]) if s.get("across") else None)
            picks = []
            for side in (a, b):  # the face's edge shared with each side: the edges picked
                shared = next(e for e in found.Edges if any(e.isSame(x) for x in side.Edges))
                picks.append((base, "Edge%d" % (next(k for k, x in enumerate(base.Shape.Edges) if x.isSame(shared)) + 1)))
            edges = format_picks(picks)
        except Exception:
            pass
    return {"edges": edges}
