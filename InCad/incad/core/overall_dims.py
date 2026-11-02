"""Overall dimensions (the user, 2026-10-17): a distance between the OUTER edges of two arcs or circles — a slot's
overall length, end to end over its arcs — set by hand like any dimension (FreeCAD's own Dimension gives the
centre-to-centre one). A construction axis through the two centres, a point where it leaves each arc, and the
distance between those points. It drives (a normal dN) when the sketch takes it; when the length is already fixed
(a centre-to-centre dimension, say) it is added as a reference, which follows. No GUI: ui/overall_dim_tool picks."""
import FreeCAD as App
import Part
import Sketcher

_ARC, _CIRCLE, _LINE = "Part::GeomArcOfCircle", "Part::GeomCircle", "Part::GeomLineSegment"
ROUND = (_ARC, _CIRCLE)


def geometry(sk, g):
    """The geometry of a Sketcher id: the sketch's own from 0 up, projected edges -3 down (the user's law: a tool takes
    normal, construction and projected geometry alike)."""
    return sk.Geometry[g] if g >= 0 else sk.ExternalGeo[-g - 1]


def _state(sk):
    sk.solve()
    return sk.DoF, set(sk.ConflictingConstraints), set(sk.RedundantConstraints), set(sk.MalformedConstraints)


def _try(sk, geometry, constraints, fixes, reference=()):
    """Add construction `geometry` and `constraints` (a function of the new geometry ids) and keep them only when
    they take away exactly `fixes` degrees of freedom and break nothing; else the sketch is put back. `reference`:
    positions in the constraint list made reference (shown, never driving)."""
    dof, conflicting, redundant, malformed = _state(sk)
    n_geo, n_con = sk.GeometryCount, sk.ConstraintCount
    try:
        ids = [sk.addGeometry(g, True) for g in geometry]
        made = sk.addConstraint(constraints(ids))
        made = list(made) if isinstance(made, (list, tuple)) else [made]
        for k in reference:
            sk.setDriving(made[k], False)
        dof2, conflicting2, redundant2, malformed2 = _state(sk)
        if dof2 == dof - fixes and conflicting2 <= conflicting and redundant2 <= redundant \
                and malformed2 <= malformed:
            return True
    except Exception:
        pass
    for i in range(sk.ConstraintCount - 1, n_con - 1, -1):
        sk.delConstraint(i)
    for i in range(sk.GeometryCount - 1, n_geo - 1, -1):
        sk.delGeometry(i)
    sk.solve()
    return False


def _slots(sk, ids):
    """(arc, arc, line, line) of each slot among the new geometry: two equal arcs, two lines each tangent to both."""
    geo = sk.Geometry
    arcs = [i for i in ids if geo[i].TypeId == _ARC]
    lines = [i for i in ids if geo[i].TypeId == _LINE]
    cons = sk.Constraints
    tangent = {}
    for c in cons:
        if c.Type == "Tangent" and c.Second >= 0:
            tangent.setdefault(c.First, set()).add(c.Second)
            tangent.setdefault(c.Second, set()).add(c.First)
    found, used = [], set()
    for a in arcs:
        for b in arcs:
            if a >= b or a in used or b in used:
                continue
            equal = any(c.Type == "Equal" and {c.First, c.Second} == {a, b} for c in cons)
            sides = [ln for ln in lines if ln not in used and {a, b} <= tangent.get(ln, set())]
            if equal and len(sides) == 2:
                found.append((a, b, sides[0], sides[1]))
                used |= {a, b, sides[0], sides[1]}
    return found


def slot_of(sk, g):
    """The two arcs of the slot that geometry `g` (one of its arcs or sides) belongs to, or None."""
    ids = list(range(sk.GeometryCount))
    return next(((a, b) for a, b, l1, l2 in _slots(sk, ids) if g in (a, b, l1, l2)), None)


def overall(sk, a, b):
    """Add the overall dimension between round geometry `a` and `b` (arcs or circles); returns its index. Driving
    when the sketch takes it, else a reference."""
    ga, gb = geometry(sk, a), geometry(sk, b)
    if ga.TypeId not in ROUND or gb.TypeId not in ROUND or a == b:
        raise ValueError("Pick two arcs or circles (or one edge of a slot)")
    c1, c2 = App.Vector(ga.Center), App.Vector(gb.Center)
    r1, r2 = ga.Radius, gb.Radius
    u = c2 - c1
    if u.Length < 1e-7:
        raise ValueError("These two are concentric: dimension their diameters instead")
    length = u.Length + r1 + r2
    u.normalize()
    shapes = [Part.LineSegment(c1, c2), Part.Point(c1 - u * r1), Part.Point(c2 + u * r2)]

    def constraints(ids):
        axis, p1, p2 = ids
        return [Sketcher.Constraint("Coincident", axis, 1, a, 3), Sketcher.Constraint("Coincident", axis, 2, b, 3),
                Sketcher.Constraint("PointOnObject", p1, 1, axis), Sketcher.Constraint("PointOnObject", p1, 1, a),
                Sketcher.Constraint("PointOnObject", p2, 1, axis), Sketcher.Constraint("PointOnObject", p2, 1, b),
                Sketcher.Constraint("Distance", p1, 1, p2, 1, length)]
    if _try(sk, shapes, constraints, 1) or _try(sk, shapes, constraints, 0, reference=(6,)):
        return sk.ConstraintCount - 1
    raise ValueError("This dimension doesn't fit the sketch")


def ends_of(sk, index):
    """The two arcs / circles the overall dimension `index` spans, or None (another kind of dimension)."""
    cons, geo = sk.Constraints, sk.Geometry
    if not 0 <= index < len(cons):
        return None
    c = cons[index]
    if c.Type != "Distance" or c.First < 0 or c.Second < 0:
        return None
    if geo[c.First].TypeId != "Part::GeomPoint" or geo[c.Second].TypeId != "Part::GeomPoint":
        return None

    def round_of(p):
        return [k.Second for k in cons if k.Type == "PointOnObject" and k.First == p
                and (k.Second >= 0 or -len(sk.ExternalGeo) <= k.Second <= -3)
                and geometry(sk, k.Second).TypeId in ROUND]
    a, b = round_of(c.First), round_of(c.Second)
    return (a[0], b[0]) if len(a) == 1 and len(b) == 1 else None


def hold_radii(sk, index):
    """Before the overall dimension `index` gets a new value: its arcs keep their radius while the sketch solves
    (a slot gets longer, not wider — FreeCAD's solver otherwise put the whole change into a free radius) unless
    something else fixes it. Returns the temporary constraints for release()."""
    ends = ends_of(sk, index)
    if not ends:
        return []
    held = []
    for g in dict.fromkeys(g for g in ends if g >= 0):  # a projected one can't change its radius anyway
        if _try(sk, [], lambda ids, g=g: [Sketcher.Constraint("Radius", g, sk.Geometry[g].Radius)], 1):
            held.append(sk.ConstraintCount - 1)
    return held


def release(sk, held):
    for i in sorted(held, reverse=True):
        sk.delConstraint(i)
    if held:
        sk.solve()


KINDS = (("centers", "Centers"), ("overall", "Overall"), ("gap", "Gap"))  # a distance between two arcs / circles


def kind_of(sk, index):
    """(kind, a, b) when dimension `index` is a distance between two arcs / circles: 'centers' (centre to centre),
    'overall' (outer edges, see overall()) or 'gap' (inner edges: FreeCAD's Dimension on two arcs); else None."""
    cons = sk.Constraints
    if not 0 <= index < len(cons) or cons[index].Type != "Distance":
        return None
    ends = ends_of(sk, index)
    if ends:
        return ("overall",) + tuple(ends)
    c = cons[index]
    try:
        ga, gb = geometry(sk, c.First), geometry(sk, c.Second)
    except (IndexError, ValueError):
        return None
    if c.First in (-1, -2) or c.Second in (-1, -2) or ga.TypeId not in ROUND or gb.TypeId not in ROUND:
        return None
    if (App.Vector(ga.Center) - App.Vector(gb.Center)).Length < 1e-7:
        return None  # concentric (an Offset's copy): no centres to measure between, no overall length
    if c.FirstPos == 3 and c.SecondPos == 3:
        return "centers", c.First, c.Second
    if c.FirstPos == 0 and c.SecondPos == 0:
        return "gap", c.First, c.Second
    return None


def _measure(sk, kind, a, b):
    ga, gb = geometry(sk, a), geometry(sk, b)
    d = (App.Vector(gb.Center) - App.Vector(ga.Center)).Length
    return {"centers": d, "overall": d + ga.Radius + gb.Radius, "gap": d - ga.Radius - gb.Radius}[kind]


def set_kind(sk, index, kind):
    """Dimension `index` measures the same two arcs / circles another way (its current size that way), under the
    same name; driving when the sketch takes it, else a reference. Returns its new index."""
    found = kind_of(sk, index)
    if found is None:
        raise ValueError("Not a distance between two arcs or circles")
    old, a, b = found
    if kind == old:
        return index
    c = sk.Constraints[index]
    name = c.Name
    if name:
        from incad.core import model
        users = model.users_of(sk.Document, name)
        if users or sk.ExpressionEngine and any(p == "Constraints." + name for p, _ in sk.ExpressionEngine):
            raise ValueError("%s is used in a formula (%s): it keeps its kind" % (name, ", ".join(users) or name))
    if kind == "gap" and _measure(sk, "gap", a, b) <= 1e-7:
        raise ValueError("These two overlap: there is no gap to dimension")
    if old == "overall":
        p1, p2 = c.First, c.Second
        axis = (set(_point_on(sk, p1)) & set(_point_on(sk, p2))).pop()
        for g in sorted((axis, p1, p2), reverse=True):  # its own construction (made after a and b: they keep ids)
            sk.delGeometry(g)
    else:
        sk.delConstraint(index)
    if kind == "overall":
        new = overall(sk, a, b)
    else:
        pos = 3 if kind == "centers" else 0
        value = _measure(sk, kind, a, b)
        make = lambda ids: [Sketcher.Constraint("Distance", a, pos, b, pos, value)]  # noqa: E731
        if not (_try(sk, [], make, 1) or _try(sk, [], make, 0, reference=(0,))):
            raise ValueError("This dimension doesn't fit the sketch")
        new = sk.ConstraintCount - 1
    if name:
        sk.renameConstraint(new, name)
    return new


def _point_on(sk, point):
    """The construction lines a point lies on (PointOnObject)."""
    return [k.Second for k in sk.Constraints if k.Type == "PointOnObject" and k.First == point and k.Second >= 0
            and sk.Geometry[k.Second].TypeId == _LINE]
