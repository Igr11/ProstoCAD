"""InCad's sketch Offset (Inventor's): the whole outline a picked edge belongs to (edges joined end to end), offset
to the pointer's side — one copy for an open chain (FreeCAD's tool rings it with a slot), sharp corners (FreeCAD's
starts rounded), an ellipse / spline as its exact offset B-spline (FreeCAD's drops it) — and tied to the original:
lines parallel at one offset dimension, circles / arcs concentric. Geometry in sketch coordinates."""
import FreeCAD as App
import Part
import Sketcher

from incad.core import offset2d, safe

PICK_MSG = "Click a line, arc, circle, ellipse or spline of the sketch"
TOO_LARGE_MSG = "The offset is too large for this shape"
NEAR_MSG = "Move the pointer away from the shape to set the distance"
_TOL = 1e-6
_CLOSED_KINDS = ("Circle", "Ellipse")


def _kind(geo):
    return type(geo).__name__


def _geo(sk, g):
    """The geometry of a Sketcher id: the sketch's own from 0 up, projected edges -3 down."""
    return sk.Geometry[g] if g >= 0 else sk.ExternalGeo[-g - 1]


def _ids(sk):
    """The sketch's own geometry and its projected edges (not the axes)."""
    return list(range(sk.GeometryCount)) + [-1 - k for k in range(2, len(sk.ExternalGeo))]


def _construction(sk, g):
    """Construction? A projected edge counts as construction unless it is marked normal (part of the profile)."""
    if g >= 0:
        return bool(sk.getConstruction(g))
    from incad.core import sketch as sketches
    return not sketches.is_defining(sk, g)


def _usable(sk, g):
    if g >= sk.GeometryCount or (g < 0 and not -len(sk.ExternalGeo) <= g <= -3):
        return False
    return _kind(_geo(sk, g)) != "Point"  # construction and projected too (the user's law: a tool takes every line)


def _closed_alone(geo):
    if _kind(geo) in _CLOSED_KINDS:
        return True
    if _kind(geo) == "BSplineCurve":
        return geo.isClosed()
    return False


def _ends(sk, g):
    if g < 0:
        geo = _geo(sk, g)
        return App.Vector(geo.StartPoint), App.Vector(geo.EndPoint)
    return sk.getPoint(g, 1), sk.getPoint(g, 2)


def outline(sk, g):
    """(geometry indexes in order, closed) of the outline geometry `g` belongs to: the geometry joined to it end to
    end (a rectangle's four lines, a chain drawn line by line), normal and construction alike (the user's law: equal
    rights), or the one closed curve itself. A point is never part of one."""
    if not _usable(sk, g):
        raise ValueError(PICK_MSG)
    if _closed_alone(_geo(sk, g)):
        return [g], True
    # its own kind first (a construction diagonal drawn before a rectangle turned a click on a side into a
    # triangle, the finder); both kinds when only that closes the outline (two sides construction, two normal)
    kind = _construction(sk, g)
    own = _chain(sk, g, lambda h: _construction(sk, h) == kind)
    if own[1]:
        return own
    mixed = _chain(sk, g, lambda h: True)
    if not mixed[1]:
        return own
    for h in mixed[0]:  # a line of the other kind that is part of a closed outline of its own kind is that
        if _construction(sk, h) != kind and _chain(sk, h, lambda x: _construction(sk, x) != kind)[1]:
            return own  # outline's, not this one's (a click on a construction diagonal: the diagonal alone)
    return mixed


def _chain(sk, g, take):
    """(indexes, closed) of the chain through g over the usable geometry `take` accepts."""
    others = [h for h in _ids(sk) if h != g and _usable(sk, h) and not _closed_alone(_geo(sk, h))
              and take(h)]

    def step(cur, point, used):
        for h in others:
            if h in used:
                continue
            a, b = _ends(sk, h)
            if (a - point).Length < _TOL:
                return h, b
            if (b - point).Length < _TOL:
                return h, a
        return None

    start, end = _ends(sk, g)
    forward, used, point = [], {g}, end
    closed = False
    while True:
        nxt = step(None, point, used)
        if nxt is None:
            break
        h, point = nxt
        forward.append(h)
        used.add(h)
        if (point - start).Length < _TOL:
            closed = True
            break
    backward = []
    if not closed:
        point = start
        while True:
            nxt = step(None, point, used)
            if nxt is None:
                break
            h, point = nxt
            backward.append(h)
            used.add(h)
    return list(reversed(backward)) + [g] + forward, closed


def _wire(sk, chain):
    edges = [_geo(sk, g).toShape() for g in chain]
    return Part.Wire(Part.__sortEdges__(edges)) if len(edges) > 1 else Part.Wire(edges)


def _line_chain_offset(sk, chain, closed, d, sign):
    """An outline of lines only, offset by sign*d to the left of its walking direction, sharp corners: each line
    shifted along its normal, consecutive ones met at their intersection (OCC needs a plane a single line lacks)."""
    pts = []
    order = []
    point = None
    for g in chain:  # walk the chain end to end
        a, b = _ends(sk, g)
        if point is not None and (b - point).Length < _TOL:
            a, b = b, a
        order.append((a, b))
        point = b
    if closed and len(order) > 1 and (order[0][0] - order[-1][1]).Length > _TOL:
        order[0] = (order[0][1], order[0][0])
    shifted = []
    for a, b in order:
        u = b - a
        u.normalize()
        n = App.Vector(-u.y, u.x, 0) * (sign * d)
        shifted.append((a + n, b + n, u))

    def meet(l1, l2):
        (p1, _, u1), (p2, _, u2) = l1, l2
        den = u1.x * u2.y - u1.y * u2.x
        if abs(den) < 1e-12:
            return None  # parallel neighbours (a straight chain): keep the shifted end
        t = ((p2.x - p1.x) * u2.y - (p2.y - p1.y) * u2.x) / den
        return p1 + u1 * t

    count = len(shifted)
    starts = [s[0] for s in shifted]
    ends = [s[1] for s in shifted]
    for i in range(count - 1 if not closed else count):
        j = (i + 1) % count
        m = meet(shifted[i], shifted[j])
        if m is not None:
            ends[i], starts[j] = m, m
    edges = []
    for a, b, (oa, ob) in zip(starts, ends, order):
        if (b - a).Length < 1e-6 or (b - a).dot(ob - oa) <= 0:
            raise ValueError(TOO_LARGE_MSG)  # a side vanished or turned round: the outline folds
        edges.append(Part.LineSegment(a, b).toShape())
    return Part.Wire(edges)


def _offset(sk, chain, closed, pointer, distance=None):
    """(the offset wire, d, the side's sign): d from the pointer unless given."""
    wire = _wire(sk, chain)
    probe = Part.Vertex(App.Vector(pointer.x, pointer.y, 0))
    d = distance if distance is not None else wire.distToShape(probe)[0]
    if d < 1e-4:
        raise ValueError(NEAR_MSG)
    if all(_kind(_geo(sk, g)) == "LineSegment" for g in chain):
        best = None
        for sign in (1, -1):
            try:
                w = _line_chain_offset(sk, chain, closed, d, sign)
            except ValueError:
                continue
            gap = w.distToShape(probe)[0]
            if closed:  # a closed outline: the side the pointer is on (inside / outside), not just the nearest
                inside_new = Part.Face(w).isInside(probe.Point, 1e-7, True)
                inside_old = Part.Face(wire).isInside(probe.Point, 1e-7, True)
                grows = Part.Face(w).Area > Part.Face(wire).Area
                if grows == inside_old:
                    continue
            if best is None or gap < best[0]:
                best = (gap, w, sign)
        if best is None:
            raise ValueError(TOO_LARGE_MSG)
        return best[1], d, best[2]
    try:
        if closed:
            face = Part.Face(wire)
            sign = -1 if face.isInside(App.Vector(pointer.x, pointer.y, 0), 1e-7, True) else 1
            res = offset2d.offset2d(face, sign * d, 2)  # (a small circle crashed OCC: by its radius)
            if res.isNull() or not res.Faces or res.Area < _TOL:
                raise ValueError(TOO_LARGE_MSG)
            new = res.Faces[0].OuterWire
        else:
            best = None
            for sign in (1, -1):
                w = wire.makeOffset2D(sign * d, 2, False, True)
                if w.isNull() or not w.Edges:
                    continue
                gap = w.distToShape(probe)[0]
                if best is None or gap < best[0]:
                    best = (gap, w, sign)
            if best is None:
                raise ValueError(TOO_LARGE_MSG)
            _, new, sign = best
    except ValueError:
        raise
    except Exception:  # OCC's offset fails on a fold
        raise ValueError(TOO_LARGE_MSG)
    # a smooth outline (a circle, an ellipse, a spline) offset past its curvature folds: OCC trims it to pieces.
    # Every point of a curved original must then still be exactly d from the copy (straight sides may be farther
    # at a sharp inner corner, which is right)
    for e in wire.Edges:
        if _kind(e.Curve) == "Line":
            continue
        for p in e.discretize(32):
            if abs(new.distToShape(Part.Vertex(p))[0] - d) > 1e-3:
                raise ValueError(TOO_LARGE_MSG)
    return new, d, sign


def _ordered_edges(wire):
    try:
        return list(wire.OrderedEdges)
    except Exception:
        return list(wire.Edges)


def _as_geometry(e):
    """A result edge as sketch geometry: line, circle or arc as such, anything else as its B-spline."""
    c = e.Curve
    a, b = e.valueAt(e.FirstParameter), e.valueAt(e.LastParameter)
    kind = _kind(c)
    if kind == "Line":
        return Part.LineSegment(App.Vector(a.x, a.y, 0), App.Vector(b.x, b.y, 0))
    if kind == "Circle":
        center = App.Vector(c.Center.x, c.Center.y, 0)
        if e.isClosed():
            return Part.Circle(center, App.Vector(0, 0, 1), c.Radius)
        mid = e.valueAt((e.FirstParameter + e.LastParameter) / 2)
        return Part.ArcOfCircle(App.Vector(a.x, a.y, 0), App.Vector(mid.x, mid.y, 0), App.Vector(b.x, b.y, 0))
    try:
        bs = c.toBSpline(e.FirstParameter, e.LastParameter)
    except Exception:
        bs = None
    if bs is None or _kind(bs) != "BSplineCurve":
        bs = Part.BSplineCurve()
        pts = e.discretize(96)
        if e.isClosed():
            bs.interpolate(pts[:-1], PeriodicFlag=True)
        else:
            bs.interpolate(pts)
    return bs


def _curves(new, closed):
    edges = _ordered_edges(new)
    if closed and edges and all(_kind(e.Curve) not in ("Line", "Circle") for e in edges):
        # an ellipse's / spline's offset comes back in pieces: one closed curve through the whole outline
        bs = Part.BSplineCurve()
        bs.interpolate(new.discretize(160)[:-1], PeriodicFlag=True)
        return [bs]
    return [_as_geometry(e) for e in edges]


def offset_curves(sk, chain, closed, pointer, distance=None):
    """The offset copy of the outline as sketch geometry, in order (not added)."""
    new, _, _ = _offset(sk, chain, closed, pointer, distance)
    return _curves(new, closed)


def _same(p, q):
    return (p - q).Length < 1e-6


def _original_line(sk, chain, line, d):
    """The original line the copy `line` is the offset of: parallel, d away."""
    u = line.EndPoint - line.StartPoint
    if u.Length < _TOL:
        return None
    u.normalize()
    best, best_err = None, 1e-4
    for g in chain:
        o = _geo(sk, g)
        if _kind(o) != "LineSegment":
            continue
        w = o.EndPoint - o.StartPoint
        if w.Length < _TOL or abs(abs(u.cross(w).Length / w.Length)) > 1e-6:
            continue
        gap = o.toShape().Curve.toShape().distToShape(Part.Vertex(line.StartPoint))[0]  # to its infinite line
        if abs(gap - d) < best_err:
            best, best_err = g, abs(gap - d)
    return best


def _original_round(sk, chain, curve, d):
    for g in chain:
        o = _geo(sk, g)
        if _kind(o) in ("Circle", "ArcOfCircle") and _same(o.Center, curve.Center) and \
                abs(abs(o.Radius - curve.Radius) - d) < 1e-4:
            return g
    return None


def _smooth(sk, gi, gj, p):
    """Do geometries gi and gj run on in one direction through their shared point p?"""
    def direction(g):
        geo = _geo(sk, g)
        t = geo.tangent(geo.parameter(p))[0]
        return App.Vector(t.x, t.y, 0)
    a, b = direction(gi), direction(gj)
    return a.Length > _TOL and b.Length > _TOL and a.cross(b).Length / (a.Length * b.Length) < 1e-6


def add(sk, chain, closed, pointer, distance=None):
    """Add the offset copy, its corners joined and tied to the original. Returns (new geometry indexes, the name of
    the offset dimension or None when nothing could be tied — an ellipse / spline outline)."""
    from incad.core import sketch as sketches
    new_wire, d, sign = _offset(sk, chain, closed, pointer, distance)
    curves = _curves(new_wire, closed)
    first = sk.GeometryCount
    ids = sk.addGeometry(curves, False)
    if isinstance(ids, int):
        ids = [ids]
    if len(ids) == len(chain):  # each copy of the kind of its original (a construction side stays construction)
        for orig, copy in zip(chain, ids):
            if orig >= 0 and sk.getConstruction(orig):  # (a projected edge's copy is drawn: normal)
                sk.setConstruction(copy, True)
    joints = []
    for i, gi in enumerate(ids):  # consecutive copies share their end points: Tangent where the outline runs on
        for gj in ids[i + 1:]:    # smoothly (a slot's arcs and lines), Coincident at a sharp corner
            for pi in (1, 2):
                for pj in (1, 2):
                    try:
                        p = sk.getPoint(gi, pi)
                        if _same(p, sk.getPoint(gj, pj)):
                            kind = "Tangent" if _smooth(sk, gi, gj, p) else "Coincident"
                            joints.append(Sketcher.Constraint(kind, gi, pi, gj, pj))
                    except Exception:
                        pass
    ties = []  # per copy: its ties to the original, the offset Distance last
    for gi, curve in zip(ids, curves):
        k = _kind(curve)
        if k == "LineSegment":
            o = _original_line(sk, chain, curve, d)
            if o is not None:
                ties.append([Sketcher.Constraint("Parallel", gi, o), Sketcher.Constraint("Distance", gi, 1, o, d)])
        elif k in ("Circle", "ArcOfCircle"):
            o = _original_round(sk, chain, curve, d)
            if o is not None:  # circle to circle: their radii d apart
                ties.append([Sketcher.Constraint("Coincident", gi, 3, o, 3), Sketcher.Constraint("Distance", gi, o, d)])
    if joints:
        sk.addConstraint(joints)
    # one by one: a tie the others imply already (a slot's second line once its arcs are tied) makes the sketch
    # redundant and stop solving, so it goes again at once. One line first (the offset dimension: between two
    # lines, not two concentric arcs the value box can't measure), then the arcs and circles, then the other
    # lines: all lines first, an arc's centre (two equations, one of them implied) went whole and the arc could slide
    lines = [g for g in ties if g[0].Type == "Parallel"]
    rounds = [g for g in ties if g[0].Type != "Parallel"]
    ties = lines[:1] + rounds + lines[1:]
    distances = []
    already = len(sketches.redundant(sk))
    for group in ties:
        for c in group:
            index = sk.addConstraint(c)
            if len(sketches.redundant(sk)) > already:  # FreeCAD may name any of the ties repeating each other
                sk.delConstraint(index)
            elif c.Type == "Distance":
                distances.append(index)
    dim = None
    if distances:
        sketches.name_new_dimensions(sk.Document)
        dim = sk.Constraints[distances[0]].Name or None
        for t in distances[1:]:
            if dim:
                sk.setExpression("Constraints[%d]" % t, "Constraints.%s" % dim)
                sk.setVirtualSpace(t, True)  # one dimension on view (the user): the others follow it, hidden
    safe.recompute(sk.Document)
    return list(range(first, first + len(curves))), dim
