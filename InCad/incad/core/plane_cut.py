"""Project Geometry on a plane at an angle to the sketch: the line where the two planes meet, as a construction
line of the sketch's own that follows the plane (the sketch remembers which plane each such line comes from and
moves it after every recompute). FreeCAD's own projection can't do a plane that isn't square to the sketch."""
import json

import FreeCAD as App
import Part

PROP = "InCadPlaneCuts"  # {geometry id: plane name}


def _segment(sk, plane):
    """The line where `plane` meets the sketch, in sketch coordinates, about as long as the part; None when they
    are parallel."""
    sp = sk.getGlobalPlacement()
    pp = plane.getGlobalPlacement()
    n1 = sp.Rotation.multVec(App.Vector(0, 0, 1))
    n2 = pp.Rotation.multVec(App.Vector(0, 0, 1))
    d = n1.cross(n2)
    if d.Length < 1e-9:
        return None
    d.normalize()
    h1, h2, ab = n1.dot(sp.Base), n2.dot(pp.Base), n1.dot(n2)
    det = 1 - ab * ab
    p = n1 * ((h1 - h2 * ab) / det) + n2 * ((h2 - h1 * ab) / det)  # on both planes
    body = sk.getParentGeoFeatureGroup()
    shape = getattr(body, "Shape", None)
    half = 50.0
    if shape is not None and not shape.isNull() and shape.Solids:
        box = shape.BoundBox
        half = box.DiagonalLength * 0.6
        p = p + d * d.dot(box.Center - p)  # centred on the part
    inv = sp.inverse()
    s, e = inv.multVec(p - d * half), inv.multVec(p + d * half)
    return App.Vector(s.x, s.y, 0), App.Vector(e.x, e.y, 0)


def _cuts(sk):
    try:
        return {int(k): v for k, v in json.loads(getattr(sk, PROP, "") or "{}").items()}
    except Exception:
        return {}


def _store(sk, cuts):
    if PROP not in sk.PropertiesList:
        sk.addProperty("App::PropertyString", PROP, "InCad", "construction lines that follow a plane")
        sk.setPropertyStatus(PROP, "Hidden")
    setattr(sk, PROP, json.dumps(cuts))


def add(sk, plane):
    """The line where `plane` meets sketch `sk`, as a construction line that follows the plane; its index, or
    None when they are parallel. Call inside safe.run_safely."""
    seg = _segment(sk, plane)
    if seg is None:
        return None
    g = sk.addGeometry(Part.LineSegment(*seg), True)
    cuts = _cuts(sk)
    cuts[sk.getGeometryId(g)] = plane.Name
    _store(sk, cuts)
    return g


def follow(doc):
    """Every plane-cut line of the document's sketches put where its plane meets the sketch now. True when one
    moved (the caller recomputes)."""
    moved = False
    for sk in doc.Objects:
        if sk.TypeId != "Sketcher::SketchObject" or PROP not in sk.PropertiesList:
            continue
        cuts = _cuts(sk)
        ids = {sk.getGeometryId(i): i for i in range(len(sk.Geometry))}
        kept = {}
        for gid, name in cuts.items():
            plane, i = doc.getObject(name), ids.get(gid)
            if plane is None or i is None:
                continue  # the line or the plane was deleted: it stays a plain line
            kept[gid] = name
            seg = _segment(sk, plane)
            line = sk.Geometry[i]
            if seg is None or ((line.StartPoint - seg[0]).Length < 1e-7 and (line.EndPoint - seg[1]).Length < 1e-7):
                continue
            sk.moveGeometry(i, 1, seg[0])
            sk.moveGeometry(i, 2, seg[1])
            moved = True
        if kept != cuts:
            _store(sk, kept)
    return moved
