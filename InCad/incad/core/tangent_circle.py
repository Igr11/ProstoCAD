"""Sketch ▸ Tangent Circle (the user: "a circle tangent to two or three lines or shapes"): a circle tied by Tangent
constraints to the lines, arcs and circles picked. Three picks fix it (three lines: the circle inside their
triangle); with two its radius stays free, for a dimension. Where it starts decides which of the solver's answers it
takes, so it starts where the picks enclose: the incircle of three lines, else a circle in their middle."""
import math

import FreeCAD as App

V = App.Vector
KINDS = ("Part::GeomLineSegment", "Part::GeomCircle", "Part::GeomArcOfCircle")


def _meet(a, b):
    """Where two lines (infinite) cross, or None for parallel ones."""
    p, r = V(a.StartPoint), V(a.EndPoint) - V(a.StartPoint)
    q, s = V(b.StartPoint), V(b.EndPoint) - V(b.StartPoint)
    cross = r.x * s.y - r.y * s.x
    if abs(cross) < 1e-9 * max(r.Length * s.Length, 1e-12):
        return None
    t = ((q.x - p.x) * s.y - (q.y - p.y) * s.x) / cross
    return p + r * t


def _distance(g, c):
    """How far the point c is from the curve g (a line taken as infinite, a circle by its rim)."""
    if g.TypeId == "Part::GeomLineSegment":
        a, b = V(g.StartPoint), V(g.EndPoint)
        d = b - a
        return abs(d.x * (c.y - a.y) - d.y * (c.x - a.x)) / max(d.Length, 1e-12)
    centre = V(g.Center)
    return abs((c - centre).Length - g.Radius)


def _middle(g):
    if g.TypeId == "Part::GeomLineSegment":
        return (V(g.StartPoint) + V(g.EndPoint)) * 0.5
    if g.TypeId == "Part::GeomArcOfCircle":
        return g.value(0.5 * (g.FirstParameter + g.LastParameter))
    return V(g.Center)


def start(geos):
    """(centre, radius) to start the circle from."""
    lines = [g for g in geos if g.TypeId == "Part::GeomLineSegment"]
    if len(geos) == 3 and len(lines) == 3:
        corners = [_meet(lines[0], lines[1]), _meet(lines[1], lines[2]), _meet(lines[2], lines[0])]
        if all(c is not None for c in corners):
            a, b, c = corners
            la, lb, lc = (b - c).Length, (c - a).Length, (a - b).Length
            per = la + lb + lc
            if per > 1e-9:
                centre = (a * la + b * lb + c * lc) * (1.0 / per)
                return _fitted(geos, centre, _distance(lines[0], centre))
    mids = [_middle(g) for g in geos]
    centre = sum(mids, V()) * (1.0 / len(mids))
    if len(geos) == 2 and len(lines) == 2:
        corner = _meet(lines[0], lines[1])
        if corner is not None:  # on the angle's bisector, towards where the lines are drawn
            u = (_middle(lines[0]) - corner)
            w = (_middle(lines[1]) - corner)
            u.normalize()
            w.normalize()
            bis = u + w
            if bis.Length > 1e-9:
                bis.normalize()
                reach = 0.5 * min((_middle(g) - corner).Length for g in lines)
                centre = corner + bis * reach
    r = sum(_distance(g, centre) for g in geos) / len(geos)
    return _fitted(geos, centre, max(r, 1e-3))


def _fitted(geos, centre, r, rounds=60):
    """The start made to touch every pick (least squares of its gaps; with two picks the radius kept): the sketch's
    solver then has nothing to move — from a rough start it moved the user's own lines to meet the circle."""
    free_r = len(geos) >= 3
    x = [centre.x, centre.y, r]

    def gaps(v):
        c = V(v[0], v[1], 0)
        return [_distance(g, c) - v[2] for g in geos]
    for _ in range(rounds):
        f = gaps(x)
        if max(abs(e) for e in f) < 1e-9:
            break
        cols = []
        for j in range(3 if free_r else 2):
            h = 1e-6 * max(1.0, abs(x[j]))
            y = list(x)
            y[j] += h
            cols.append([(a - b) / h for a, b in zip(gaps(y), f)])
        # normal equations (J^T J) d = -J^T f, a 2 x 2 or 3 x 3 solve
        n_ = len(cols)
        A = [[sum(cols[i][k] * cols[j][k] for k in range(len(f))) for j in range(n_)] for i in range(n_)]
        b = [-sum(cols[i][k] * f[k] for k in range(len(f))) for i in range(n_)]
        d = _solve(A, b)
        if d is None:
            break
        step = 1.0
        while step > 1e-4:
            y = list(x)
            for j in range(n_):
                y[j] += step * d[j]
            if y[2] > 1e-6 and sum(e * e for e in gaps(y)) < sum(e * e for e in f):
                x = y
                break
            step *= 0.5
        else:
            break
    return V(x[0], x[1], 0), x[2]


def _solve(A, b):
    n_ = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for i in range(n_):
        p = max(range(i, n_), key=lambda r: abs(M[r][i]))
        if abs(M[p][i]) < 1e-14:
            return None
        M[i], M[p] = M[p], M[i]
        for r in range(n_):
            if r != i:
                k = M[r][i] / M[i][i]
                M[r] = [a - k * c for a, c in zip(M[r], M[i])]
    return [M[i][n_] / M[i][i] for i in range(n_)]


def geometry(sk, i):
    """The sketch's geometry by its Sketcher id: its own from 0 up, the axes -1 and -2, projected edges -3 down."""
    return sk.Geometry[i] if i >= 0 else sk.ExternalGeo[-i - 1]


def add(sk, picks):
    """A circle tangent to the sketch's geometries `picks` (2 or 3 Sketcher ids: projected edges too). Returns its
    index."""
    import Part
    import Sketcher
    if len(picks) not in (2, 3):
        raise ValueError("Tangent Circle: select two or three lines, arcs or circles")
    geos = [geometry(sk, i) for i in picks]
    if any(g.TypeId not in KINDS for g in geos):
        raise ValueError("Tangent Circle: only lines, arcs and circles (a spline can't be touched)")
    centre, r = start(geos)
    k = sk.addGeometry(Part.Circle(V(centre.x, centre.y, 0), V(0, 0, 1), r), False)
    for i in picks:
        sk.addConstraint(Sketcher.Constraint("Tangent", k, i))
    if sk.solve() != 0:
        raise ValueError("Tangent Circle: no circle touches all of these")
    return k
