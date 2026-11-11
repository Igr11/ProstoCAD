"""Auto-projection (the user, after Inventor): a point a Sketcher tool has just drawn onto a model edge or vertex
in the sketch's plane gets it projected and is tied to it — coincident with a vertex, on an edge. The edge is
normal or reference geometry as ⚙ ▸ Projected Edges says. Runs after a drawing step (with Dimensions While
Drawing, before them: what the tie fixes needs no dimension). A tie the sketch can't take is taken off at once."""
import FreeCAD as App

TIES = ("Coincident", "PointOnObject", "Tangent", "Perpendicular", "Symmetric")
PLANE_TOL = 1e-5  # mm off the sketch's plane that still counts as in it


def _base(sk):
    """The solid feature the sketch can project from: the last one before it in its body."""
    body = sk.getParentGeoFeatureGroup()
    if body is None or body.TypeId != "PartDesign::Body":
        return None
    base = None
    for o in body.Group:
        if o is sk:
            break
        shape = getattr(o, "Shape", None)
        if o.isDerivedFrom("PartDesign::Feature") and shape is not None and not shape.isNull() and shape.Solids:
            base = o
    return base


def _points(sk, first):
    """(geo, pos, point) of the geometry drawn from `first` on: line and arc ends, centres, points."""
    out = []
    for g in range(first, sk.GeometryCount):
        kind = sk.Geometry[g].TypeId
        if kind == "Part::GeomLineSegment":
            poses = (1, 2)
        elif kind in ("Part::GeomArcOfCircle", "Part::GeomArcOfEllipse"):
            poses = (1, 2, 3)
        elif kind in ("Part::GeomCircle", "Part::GeomEllipse"):
            poses = (3,)
        elif kind == "Part::GeomPoint":
            poses = (1,)
        else:
            continue
        out += [(g, p, sk.getPoint(g, p)) for p in poses]
    return out


def _tied(sk, first):
    """(geo, pos) already tied to something drawn before or projected (the tool's own snaps)."""
    out = set()
    for c in sk.Constraints:
        if c.Type not in TIES:
            continue
        ends = [(c.First, c.FirstPos), (c.Second, getattr(c, "SecondPos", 0))]
        for (g, p), (h, _q) in (ends, ends[::-1]):
            if g >= first and p and h != -2000 and h < first:
                out.add((g, p))
    return out


def _external(sk, base, sub, at, point, defining):
    """The geo id of `base`'s element `sub` projected into the sketch (projecting it unless it is already there);
    `at`: a point of it in sketch coordinates. (id, made now)."""
    import Part
    try:
        sk.addExternal(base.Name, sub, bool(defining and not point))
        return -len(sk.ExternalGeo), True
    except Exception:  # projected already: find it
        probe = Part.Vertex(at)
        for i, g in enumerate(sk.ExternalGeo[2:], 2):
            if (g.TypeId == "Part::GeomPoint") != point:
                continue
            try:
                if g.toShape().distToShape(probe)[0] < 1e-6:
                    return -i - 1, False
            except Exception:
                continue
    return None, False


def _drop_external(sk, geo):
    """An external this pass projected and nothing ended up using."""
    if any(geo in (c.First, c.Second) for c in sk.Constraints):
        return
    try:
        from incad.features import revolve
        revolve.externals_going(sk, [-geo - 3])
        sk.delExternal(-geo - 3)
    except Exception:
        pass


def snap(sk, first, tol, defining):
    """Ties the points drawn from `first` on to the model's edges and vertices within `tol` mm in the sketch's
    plane; returns how many. `defining`: projected edges are normal geometry."""
    import Part
    import Sketcher
    base = _base(sk)
    if base is None or first >= sk.GeometryCount or sk.solve() != 0:
        return 0
    place = sk.Placement  # sketch → body coordinates (the features' shapes are in the body's)
    inv = place.inverse()
    shape = base.Shape
    verts = [(i, inv.multVec(v.Point)) for i, v in enumerate(shape.Vertexes, 1)]
    verts = [(i, p) for i, p in verts if abs(p.z) < PLANE_TOL]
    edges = []
    for i, e in enumerate(shape.Edges, 1):
        try:
            pts = e.discretize(7)
        except Exception:
            continue
        if all(abs(inv.multVec(p).z) < PLANE_TOL for p in pts):
            edges.append((i, e))
    if not verts and not edges:
        return 0
    tied = _tied(sk, first)
    done = 0
    for g, pos, p in _points(sk, first):
        if (g, pos) in tied:
            continue
        local = App.Vector(p.x, p.y, 0)
        near = min(verts, key=lambda iv: (iv[1] - local).Length, default=None)
        if near is not None and (near[1] - local).Length <= tol:
            sub, at, point = "Vertex%d" % near[0], App.Vector(near[1].x, near[1].y, 0), True
        else:
            vertex = Part.Vertex(place.multVec(local))
            hits = [(e.distToShape(vertex)[0], i, e) for i, e in edges]
            hits = [h for h in hits if h[0] <= tol]
            if not hits:
                continue
            _d, i, e = min(hits, key=lambda h: h[0])
            foot = inv.multVec(e.valueAt(e.Curve.parameter(place.multVec(local))))
            sub, at, point = "Edge%d" % i, App.Vector(foot.x, foot.y, 0), False
        ext, made = _external(sk, base, sub, at, point, defining)
        if ext is None:
            continue
        c = Sketcher.Constraint("Coincident", g, pos, ext, 1) if point else Sketcher.Constraint("PointOnObject", g, pos, ext)
        index = sk.addConstraint(c)
        if sk.solve() != 0:  # the drawing can't take it (two ends on one point, a fixed size)
            sk.delConstraint(index)
            if made:
                _drop_external(sk, ext)
            sk.solve()
            continue
        tied.add((g, pos))
        done += 1
    return done
