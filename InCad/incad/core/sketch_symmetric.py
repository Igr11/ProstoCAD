"""Sketch ▸ Symmetric, InCad's own (the user: choosing the points was hard, and a click sometimes took a dimension's
point). A pick is a point of the sketch's geometry (an end, a centre; the origin) when the click is near one, else the
line or curve under it, else the sketch's X / Y axis — dimensions and constraint symbols are never picked. Two picks
are mirrored about a third (a straight line, an axis or a point): two points as they are; two lines by their ends
(each end with the other line's end nearest its mirror image); two circles or arcs by their centres, with equal
radii. Projected edges count like the sketch's own (the user's law: construction, normal or projected, a tool takes
them all); they stay where they are, so one of a pair must be the sketch's own. No Qt here: picks are tuples,
('point', geo, pos) or ('edge', geo); the axes are geo -1 (X) and -2 (Y), projected edges -3 down, the origin
('point', -1, 1), as FreeCAD numbers them."""
import FreeCAD as App
import Part
import Sketcher

H_AXIS, V_AXIS = -1, -2
ROOT = ("point", -1, 1)
ROUND = ("Part::GeomCircle", "Part::GeomArcOfCircle")
LINE = "Part::GeomLineSegment"


def geometry(sk, geo):
    """The geometry of a Sketcher id: the sketch's own from 0 up, the axes -1 and -2, projected edges -3 down."""
    return sk.Geometry[geo] if geo >= 0 else sk.ExternalGeo[-geo - 1]


def ids(sk):
    """Every geometry id that can be picked: the sketch's own, then the projected edges (not the axes)."""
    return list(range(sk.GeometryCount)) + [-1 - k for k in range(2, len(sk.ExternalGeo))]


def _point(sk, geo, pos):
    if (geo, pos) == (-1, 1):
        return App.Vector(0, 0, 0)
    if geo >= 0:
        return App.Vector(sk.getPoint(geo, pos))
    g = geometry(sk, geo)  # a projected edge, read off its geometry
    if pos == 3 or g.TypeId == "Part::GeomPoint":
        return App.Vector(g.Center if pos == 3 else (g.X, g.Y, g.Z))
    return App.Vector(g.StartPoint if pos == 1 else g.EndPoint)


def vertices(sk):
    """[((geo, pos), point)]: every line's and arc's ends, circles' and arcs' centres, the sketch's points, the
    origin; normal, construction and projected geometry alike (a centreline is often construction)."""
    out = [((-1, 1), App.Vector(0, 0, 0))]
    for g in ids(sk):
        geom = geometry(sk, g)
        t = geom.TypeId
        if t == "Part::GeomPoint":
            out.append(((g, 1), _point(sk, g, 1)))
            continue
        if t != "Part::GeomCircle":
            for pos in (1, 2):
                try:
                    out.append(((g, pos), _point(sk, g, pos)))
                except Exception:
                    pass
        if t in ROUND or t in ("Part::GeomEllipse", "Part::GeomArcOfEllipse"):
            out.append(((g, 3), _point(sk, g, 3)))
    return out


def pick_at(sk, pt, vertex_tol, edge_tol):
    """The pick for a click at pt (sketch coordinates): a point within vertex_tol, else an edge within edge_tol, else
    an axis within edge_tol, else None."""
    pt = App.Vector(pt.x, pt.y, 0)
    best, best_d = None, vertex_tol
    for (g, pos), p in (vertices(sk) if vertex_tol > 0 else ()):  # a zero tolerance looks for none (a point exactly
        # on a line took the line for a point, and snap_at failed: IndexError, the finder)
        d = (p - pt).Length
        if d <= best_d:
            best, best_d = ("point", g, pos), d
    if best is not None:
        return best
    if edge_tol <= 0:
        return None
    probe = Part.Vertex(pt)
    best_d = edge_tol
    for g in ids(sk):
        geom = geometry(sk, g)
        if geom.TypeId == "Part::GeomPoint":
            continue
        try:
            d = geom.toShape().distToShape(probe)[0]
        except Exception:
            continue
        if d <= best_d:
            best, best_d = ("edge", g), d
    if best is not None:
        return best
    for axis, d in ((H_AXIS, abs(pt.y)), (V_AXIS, abs(pt.x))):
        if d <= best_d:
            best, best_d = ("edge", axis), d
    return best


def snap_at(sk, pt, tol):
    """(point, pick) for a point placed at pt by a drawing tool: onto a point of the sketch within tol (normal,
    construction or projected; the origin), else onto a line or curve within tol (the nearest place on it), else
    (pt, None). The pick says what to tie the new point to (Coincident / PointOnObject)."""
    pt = App.Vector(pt.x, pt.y, 0)
    pick = pick_at(sk, pt, tol, 0.0)
    if pick is not None:
        return _point(sk, pick[1], pick[2]), pick
    pick = pick_at(sk, pt, 0.0, tol)
    if pick is None:
        return pt, None
    g = pick[1]
    if g in (H_AXIS, V_AXIS):
        return (App.Vector(pt.x, 0, 0) if g == H_AXIS else App.Vector(0, pt.y, 0)), pick
    try:
        on = geometry(sk, g).toShape().distToShape(Part.Vertex(pt))[1][0][0]
        return App.Vector(on.x, on.y, 0), pick
    except Exception:
        return pt, None


def tie_point(sk, geo, pos, pick):
    """Tie the point (geo, pos) to what snap_at found; kept only when the sketch still solves. True when tied."""
    if pick is None:
        return False
    if pick[0] == "point":
        c = Sketcher.Constraint("Coincident", geo, pos, pick[1], pick[2])
    else:
        c = Sketcher.Constraint("PointOnObject", geo, pos, pick[1])
    try:
        i = sk.addConstraint(c)
    except Exception:
        return False
    if sk.solve() != 0:
        sk.delConstraint(i)
        sk.solve()
        return False
    return True


def kind(sk, pick):
    """'point', 'line' (a line segment or an axis), 'round' (circle, arc) or 'other' (a spline…)."""
    if pick[0] == "point":
        return "point"
    g = pick[1]
    if g in (H_AXIS, V_AXIS):
        return "line"
    t = geometry(sk, g).TypeId
    return "line" if t == LINE else "round" if t in ROUND else "other"


def subname(sk, pick):
    """FreeCAD's selection name of a pick (to show it selected)."""
    if pick[0] == "edge":
        g = pick[1]
        return {H_AXIS: "H_Axis", V_AXIS: "V_Axis"}.get(g, "Edge%d" % (g + 1) if g >= 0 else "ExternalEdge%d" % (-g - 2))
    if pick[1] < -2:
        return None  # a projected edge's point has no selection name
    if pick == ROOT:
        return "RootPoint"
    for n in range(sk.GeometryCount * 3 + 4):
        try:
            if tuple(sk.getGeoVertexIndex(n)) == (pick[1], pick[2]):
                return "Vertex%d" % (n + 1)
        except Exception:
            break
    return None


def from_subname(sk, sub):
    """The pick for a selection name ('Edge3', 'Vertex5', 'H_Axis', 'RootPoint'), or None."""
    if sub == "H_Axis":
        return ("edge", H_AXIS)
    if sub == "V_Axis":
        return ("edge", V_AXIS)
    if sub == "RootPoint":
        return ROOT
    if sub.startswith("Edge"):
        return ("edge", int(sub[4:]) - 1)
    if sub.startswith("ExternalEdge"):
        return ("edge", -int(sub[12:]) - 2)
    if sub.startswith("Vertex"):
        geo, pos = sk.getGeoVertexIndex(int(sub[6:]) - 1)
        return ("point", geo, int(pos))
    return None


def _mirror(sk, sym, p):
    """p mirrored about the symmetry line or point."""
    if sym[0] == "point":
        c = _point(sk, sym[1], sym[2])
        return c * 2 - p
    if sym[1] == H_AXIS:
        a, d = App.Vector(0, 0, 0), App.Vector(1, 0, 0)
    elif sym[1] == V_AXIS:
        a, d = App.Vector(0, 0, 0), App.Vector(0, 1, 0)
    else:
        a, b = _point(sk, sym[1], 1), _point(sk, sym[1], 2)
        d = (b - a).normalize()
    v = p - a
    foot = a + d * v.dot(d)
    return foot * 2 - p


def _sym_args(sym):
    return (sym[1], sym[2]) if sym[0] == "point" else (sym[1],)


def ties(sk, a, b, sym):
    """[Sketcher.Constraint] making a and b symmetric about sym; ValueError in plain words when they can't be."""
    ka, kb, ks = kind(sk, a), kind(sk, b), kind(sk, sym)
    if ks not in ("line", "point"):
        raise ValueError("Mirror about a straight line, an axis or a point")
    if a == b:
        raise ValueError("Pick two different things to mirror")
    if sym in (a, b):
        raise ValueError("The mirror line can't be one of the two things mirrored")
    if a[1] < -2 and b[1] < -2:
        raise ValueError("Both are projected and can't move: pick one of the sketch's own")
    s = _sym_args(sym)
    if ka == kb == "point":
        if ks == "line":  # a point on the mirror line is its own mirror image: Symmetric can't hold it (FreeCAD:
            for fixed, free in ((a, b), (b, a)):  # partially redundant, the finder), the other goes onto it
                p = _point(sk, fixed[1], fixed[2])
                if (_mirror(sk, sym, p) - p).Length < 1e-7:
                    return [Sketcher.Constraint("Coincident", free[1], free[2], fixed[1], fixed[2])]
        return [Sketcher.Constraint("Symmetric", a[1], a[2], b[1], b[2], *s)]
    if ka == kb == "line":
        if a[1] in (H_AXIS, V_AXIS) or b[1] in (H_AXIS, V_AXIS):
            raise ValueError("The X and Y axes can't be mirrored: pick them as the mirror line")
        ends_b = [(pos, _point(sk, b[1], pos)) for pos in (1, 2)]
        m1 = _mirror(sk, sym, _point(sk, a[1], 1))
        first = min(ends_b, key=lambda e: (e[1] - m1).Length)[0]
        second = 2 if first == 1 else 1
        return [Sketcher.Constraint("Symmetric", a[1], 1, b[1], first, *s),
                Sketcher.Constraint("Symmetric", a[1], 2, b[1], second, *s)]
    if ka == kb == "round":
        return [Sketcher.Constraint("Symmetric", a[1], 3, b[1], 3, *s), Sketcher.Constraint("Equal", a[1], b[1])]
    raise ValueError("Pick two of a kind to mirror: two points, two lines, or two circles / arcs")


def constrain(sk, a, b, sym):
    """Add the ties (call inside safe.run_safely: one undo step); older ties they repeat go (sketch.make_room).
    Returns the number of constraints added. ValueError when the sketch can't solve with them."""
    from incad.core import sketch as sketches
    new = ties(sk, a, b, sym)
    first = sk.ConstraintCount
    for c in new:
        sk.addConstraint(c)
    sketches.make_room(sk, list(range(first, first + len(new))))
    if sk.solve() not in (0,):
        raise ValueError("They can't be made symmetric: other constraints hold them where they are")
    return len(new)
