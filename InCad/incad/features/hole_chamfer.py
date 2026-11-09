"""Hole ▸ Chamfer: a 45° lead-in at the mouth of every hole of a Hole feature (Start) and at the exit of a through
hole (Both ends). A step right after the Hole (a PartDesign::FeaturePython, InCadHelperOf the Hole) that cuts cones
computed from the hole itself on every recompute — its centres, direction, diameter (a tapped hole: the thread's
diameter, so a screw starts easily) — so it never depends on edge names. A hole with a seat (counterbore,
countersink, spotface) keeps its seat at the start; only the exit is chamfered."""
import FreeCAD as App
import Part

HELPER = "InCadHelperOf"
_E = 0.01  # the cone starts this far outside the part, so the cut opens cleanly


def _centres(sk, body=None):
    """The hole centres and the sketch normal in the body's own frame (where the shapes are)."""
    pl = sk.getGlobalPlacement()
    if body is not None:
        pl = body.getGlobalPlacement().inverse().multiply(pl)
    out = []
    for i, g in enumerate(sk.Geometry):
        if sk.getConstruction(i):
            continue
        if g.TypeId in ("Part::GeomCircle", "Part::GeomArcOfCircle"):
            out.append(pl.multVec(g.Center))
        elif g.TypeId == "Part::GeomPoint":
            out.append(pl.multVec(App.Vector(g.X, g.Y, g.Z)))
    return out, pl.Rotation.multVec(App.Vector(0, 0, 1))


def _runs(solid, p, d, reach):
    """[(t_in, t_out)] where the line p + t·d runs through material."""
    line = Part.LineSegment(p - d * reach, p + d * reach).toShape()
    out = []
    for e in solid.common(line).Edges:
        a, b = (e.Vertexes[0].Point - p).dot(d), (e.Vertexes[-1].Point - p).dot(d)
        out.append((min(a, b), max(a, b)))
    return sorted(out)


_PAST = 0.05  # the cone runs on this far inside the drilled radius (into the hole's air): it crosses the wall or
# a modelled thread's crests instead of ending exactly on them, which left the cut invalid on a tapped hole


def _cone(at, d, r_drill, r_mouth, s):
    """45° from r_mouth + s at the surface down to the drilled radius (a tapped hole's mouth is its thread's
    diameter: the cone runs on to the drill, no flat ledge)."""
    end = max(r_drill - _PAST, r_drill * 0.5)
    depth = s + (r_mouth - end)
    return Part.makeCone(end + depth + _E, end, depth + _E, at - d * _E, d)


def tools(hole, ends, size):
    """The cones the chamfer cuts, as one compound (None when there is nothing to cut)."""
    if size <= 0 or ends not in ("start", "both"):
        return None
    sk = hole.Profile[0] if isinstance(hole.Profile, (tuple, list)) else hole.Profile
    before = hole.BaseFeature.Shape if hole.BaseFeature is not None else None
    if sk is None or before is None or before.isNull():
        return None
    centres, normal = _centres(sk, hole.getParentGeoFeatureGroup())
    d = normal if hole.Reversed else -normal  # a Hole drills against its sketch's normal
    threaded = getattr(hole, "Threaded", False)
    r_drill = hole.Diameter.Value / 2.0
    r_thread = hole.ThreadDiameter.Value / 2.0 if threaded else r_drill
    thread_len = hole.ThreadDepth.Value if threaded and hole.ThreadDepthType == "Dimension" else None
    seat = getattr(hole, "HoleCutType", "None") not in ("None", "")
    bb = before.BoundBox
    through = hole.DepthType == "ThroughAll"
    cones = []
    for p in centres:
        reach = (p - bb.Center).Length + bb.DiagonalLength + 10.0  # a sketch may sit far from the part
        runs = [run for run in _runs(before, p, d, reach) if run[1] > 1e-6]
        if not runs:
            continue
        entry = runs[0][0] if runs[0][0] > -1e-6 else None  # a hole starting inside the part has no mouth
        if not seat and entry is not None:
            cones.append(_cone(p + d * max(entry, 0.0), d, r_drill, r_thread, size))
        if ends == "both":
            exit_t = runs[-1][1]
            if through or (hole.DepthType == "Dimension" and hole.Depth.Value >= exit_t - 1e-6):
                start = max(entry or 0.0, 0.0)
                threaded_there = thread_len is None or start + thread_len >= exit_t - 1e-6
                cones.append(_cone(p + d * exit_t, -d, r_drill, r_thread if threaded_there else r_drill, size))
    return Part.makeCompound(cones) if cones else None


_WAYS = ("plain", 1e-5, 1e-4, "healed")
_last_way = {}  # (document, feature) -> the way that gave a valid cut last time: tried first on the next recompute


def _cut_by(shape, cut, way, plain):
    """One way of cutting; plain: [the plain cut], made once and kept for the healed way."""
    if way in ("plain", "healed") and not plain:
        plain.append(shape.cut(cut))
    if way == "plain":
        return plain[0]
    if way == "healed":
        healed = plain[0].copy()
        try:
            healed.fix(1e-7, 1e-7, 1e-4)
        except Exception:
            pass
        return healed
    return shape.cut([cut], way)


def _cut(shape, cut, key=None):
    """shape − cut, valid: a plain cut, else a fuzzy one (a modelled thread's faces sit within a hair of the cones),
    else the healed result. The way that worked is tried first next time: on a modelled thread every recompute
    made the three failing cuts again before the healed one (2 s per hole)."""
    first = _last_way.get(key)
    plain = []
    for way in ((first,) if first in _WAYS else ()) + tuple(w for w in _WAYS if w != first):
        try:
            out = _cut_by(shape, cut, way, plain)
        except Exception:
            continue
        if out.isValid():
            if key is not None:
                _last_way[key] = way
            return out
    raise ValueError("The chamfer can't be cut into this thread. Try another chamfer size, or Chamfer: None")


class HoleChamferFeature:
    """The proxy. FreeCAD saves its module path (incad.features.hole_chamfer) and class name in the file."""
    def __init__(self, obj):
        for prop, kind in (("Hole", "App::PropertyLink"), ("Ends", "App::PropertyString"),
                           ("Size", "App::PropertyDistance")):
            if prop not in obj.PropertiesList:
                obj.addProperty(kind, prop, "Chamfer", "")
        obj.Proxy = self

    def execute(self, obj):
        base = obj.BaseFeature
        if base is None or base.Shape.isNull():
            return
        cut = tools(obj.Hole, obj.Ends, obj.Size.Value) if obj.Hole is not None else None
        shape = base.Shape.copy()
        if cut is not None:
            shape = _cut(shape, cut, (obj.Document.Name, obj.Name))
            if len(shape.Solids) == 1:
                shape = shape.Solids[0]
        obj.Shape = shape

    def dumps(self):
        return None

    def loads(self, state):
        return None


def of(hole):
    body = hole.getParentGeoFeatureGroup()
    return next((o for o in (body.Group if body is not None else [])
                 if getattr(o, HELPER, None) == hole.Name and hasattr(o, "Ends")), None)


def _remove(doc, body, hole, g):
    if body.Tip is g:
        body.Tip = hole
    if g.Visibility:  # the chamfer was what showed the hole (document.show_finished): the hole shows again, or the part
        from incad.core import document  # was gone from the screen while the Hole window was open (Counterbore,
        document._set_visible(hole, True)  # Chamfer None: the user)
    body.removeObject(g)
    doc.removeObject(g.Name)


def sync(doc, hole, mode, size_text):
    """Make, update or remove the Hole's chamfer step."""
    from incad.features.common import value_param
    body = hole.getParentGeoFeatureGroup()
    g = of(hole)
    if mode not in ("start", "both"):
        if g is not None:
            _remove(doc, body, hole, g)
        return None
    if g is None:
        g = doc.addObject("PartDesign::FeaturePython", "HoleChamfer")
        HoleChamferFeature(g)
        from incad.core import document
        document.show_like(g, hole)  # drawn when it is the Tip (a chamfered Hole made last)
        g.addProperty("App::PropertyString", HELPER, "InCad", "The hole this chamfer belongs to")
        g.setPropertyStatus(HELPER, "Hidden")
        setattr(g, HELPER, hole.Name)
        after = [o for o in body.Group if getattr(o, "BaseFeature", None) is hole]
        was_tip = body.Tip is hole
        body.insertObject(g, hole, True)  # right after the hole
        g.BaseFeature = hole
        for o in after:
            o.BaseFeature = g
        if was_tip:
            body.Tip = g
    g.Hole = hole
    g.Ends = mode
    value_param(doc, g, "Size", size_text or "0.5 mm", "length")
    return g


def read(doc, hole):
    """(mode, size text) of the Hole's chamfer: ('none', '0.5 mm') when it has none."""
    from incad.features.common import value_text
    g = of(hole)
    if g is None:
        return "none", "0.5 mm"
    return g.Ends or "start", value_text(doc, g, "Size") or "0.5 mm"
