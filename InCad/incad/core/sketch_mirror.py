"""Sketch ▸ Mirror, InCad's own (the user: the mirrored shape should keep the original's size and follow its changes,
without bringing the original's dimensions along — they filled the sketch). The picked shapes are copied about a
line (any line, the X or Y axis) or a point; each copy is held to its original by Symmetric ties only (ends, centres;
circles and arcs Equal too), so it follows every change of the original and needs no dimension of its own.
Break Mirror Link (afterwards, on the copy) takes those ties off and gives the copy its own: the original's joins,
tangents, parallels… mapped onto it (never its dimensions); the copy stays where it is, free.
Lines, circles, arcs and points are mirrored; FreeCAD mirrors arcs onto the sketch's back (its axis flips), so they
are built again facing up. Picks are sketch_symmetric picks: ('edge', geo), ('point', geo, pos)."""
import math

import FreeCAD as App
import Part
import Sketcher

from incad.core import sketch_symmetric as sym

Z = App.Vector(0, 0, 1)
KEEP = ("Coincident", "Tangent", "Perpendicular", "Parallel", "Equal", "Horizontal", "Vertical", "PointOnObject")
UNDEF = -2000  # FreeCAD's GeoUndef


def _reflector(sk, axis):
    """(function mirroring a point, True when the mirror reverses orientation: a line does, a point (a half turn)
    does not)."""
    if axis[0] == "point":
        c = sym._point(sk, axis[1], axis[2])
        return (lambda p: c * 2 - p), False
    return (lambda p: sym._mirror(sk, axis, p)), True


def _copy(sk, g, reflect, reverses):
    """(mirrored geometry, {original pos: copy pos}) of geometry g."""
    geom = sym.geometry(sk, g)  # (a projected edge mirrored too)
    t = geom.TypeId
    if t == "Part::GeomLineSegment":
        return Part.LineSegment(reflect(geom.StartPoint), reflect(geom.EndPoint)), {1: 1, 2: 2}
    if t == "Part::GeomCircle":
        return Part.Circle(reflect(geom.Center), Z, geom.Radius), {3: 3}
    if t == "Part::GeomArcOfCircle":
        c = reflect(geom.Center)
        start, end = reflect(geom.StartPoint), reflect(geom.EndPoint)
        if reverses:
            start, end = end, start
        a0 = math.atan2(start.y - c.y, start.x - c.x)
        a1 = math.atan2(end.y - c.y, end.x - c.x)
        while a1 <= a0:
            a1 += 2 * math.pi
        arc = Part.ArcOfCircle(Part.Circle(c, Z, geom.Radius), a0, a1)
        return arc, ({1: 2, 2: 1, 3: 3} if reverses else {1: 1, 2: 2, 3: 3})
    if t == "Part::GeomPoint":
        return Part.Point(reflect(App.Vector(geom.X, geom.Y, 0))), {1: 1}
    raise ValueError("Mirror takes lines, circles, arcs and points (not %s)" % t.replace("Part::Geom", ""))


def mirror(sk, geos, axis):
    """Copies of geometry `geos` about `axis` (a pick: a line, an axis or a point), each tied to its original.
    Returns the new geometry indexes. Call inside safe.run_safely (one undo step)."""
    from incad.core import sketch as sketches
    if sym.kind(sk, axis) not in ("line", "point"):
        raise ValueError("Mirror about a straight line, an axis or a point (pick it last)")
    geos = [g for g in dict.fromkeys(geos) if not (axis[0] == "edge" and g == axis[1])]
    if not geos:
        raise ValueError("Pick the shapes to mirror, then the mirror line last")
    reflect, reverses = _reflector(sk, axis)
    made = [(g, _copy(sk, g, reflect, reverses)) for g in geos]
    s = sym._sym_args(axis)
    first_tie = sk.ConstraintCount
    new = []

    def tie(g, opos, c, cpos):
        """A point on the mirror line is its own mirror image: Symmetric can't hold it (FreeCAD: partially
        redundant), the copy shares it (a half profile mirrored about its closing line joins up)."""
        p = sym._point(sk, g, opos)
        if (reflect(p) - p).Length < 1e-7:
            sk.addConstraint(Sketcher.Constraint("Coincident", g, opos, c, cpos))
        else:
            sk.addConstraint(Sketcher.Constraint("Symmetric", g, opos, c, cpos, *s))
    for g, (geom, posmap) in made:
        c = sk.addGeometry(geom, bool(g >= 0 and sk.getConstruction(g)))  # a projected edge's copy: normal
        new.append(c)
        t = geom.TypeId
        if t in ("Part::GeomCircle",):
            tie(g, 3, c, 3)
        else:
            for opos in (1, 2) if t != "Part::GeomPoint" else (1,):
                tie(g, opos, c, posmap[opos])
        if t in ("Part::GeomCircle", "Part::GeomArcOfCircle"):
            sk.addConstraint(Sketcher.Constraint("Equal", g, c))
    ties = list(range(first_tie, sk.ConstraintCount))
    for _ in range(len(ties)):  # a tie the others imply already (an original's point on the mirror line) goes
        red = [i for i in sketches.redundant(sk) if i in ties]
        if not red:
            break
        sk.delConstraint(red[-1])
        ties = ties[:-1]
    if sk.solve() != 0:
        raise ValueError("The copy can't be held to the original: other constraints fight it")
    return new


def _age(sk, g):
    """FreeCAD's permanent id of geometry g (it grows with every new geometry): a copy is newer than its original."""
    try:
        return sk.getGeometryId(g)
    except Exception:
        return g


def _ties(sk, geos):
    """The mirror ties of copies `geos`: ([constraint indexes], {copy: original}, {(original, pos): copy pos}).
    Of the two shapes a tie holds, the newer is the copy: the original's side is never taken for one (breaking from
    it would leave the copy with no ties at all)."""
    geos = set(geos)
    idx, pairs, points = [], {}, {}
    for i, c in enumerate(sk.Constraints):
        if c.Type != "Symmetric" or c.First < 0 or c.Second < 0:
            continue
        a, b = (c.First, c.FirstPos), (c.Second, c.SecondPos)
        copy, orig = (a, b) if _age(sk, a[0]) > _age(sk, b[0]) else (b, a)
        if copy[0] not in geos or orig[0] in geos:
            continue
        idx.append(i)
        pairs[copy[0]] = orig[0]
        points[orig] = copy[1]
    for i, c in enumerate(sk.Constraints):
        if c.Type == "Equal" and ((c.First in pairs and pairs[c.First] == c.Second)
                                  or (c.Second in pairs and pairs[c.Second] == c.First)):
            idx.append(i)
    for copy, orig in pairs.items():
        points.setdefault((orig, 3), 3)  # a circle's or an arc's centre
    return sorted(set(idx)), pairs, points


def is_linked(sk, geos):
    return bool(_ties(sk, geos)[0])


def _mapped(c, to_copy, points):
    """Constraint c of the originals, onto their copies; None when it ties anything else."""
    def geo(g):
        return to_copy.get(g)

    def pos(g, p):
        return p if p in (0, 3) else points.get((g, p), p)
    g1, g2 = geo(c.First), geo(c.Second) if c.Second != UNDEF else None
    if g1 is None or (c.Second != UNDEF and g2 is None):
        return None
    t = c.Type
    if t == "Coincident":
        return Sketcher.Constraint(t, g1, pos(c.First, c.FirstPos), g2, pos(c.Second, c.SecondPos))
    if t in ("Tangent", "Perpendicular"):
        if c.FirstPos == 0 and c.SecondPos == 0:
            return Sketcher.Constraint(t, g1, g2)
        if c.SecondPos == 0:
            return Sketcher.Constraint(t, g1, pos(c.First, c.FirstPos), g2)
        return Sketcher.Constraint(t, g1, pos(c.First, c.FirstPos), g2, pos(c.Second, c.SecondPos))
    if t in ("Parallel", "Equal"):
        return Sketcher.Constraint(t, g1, g2)
    if t in ("Horizontal", "Vertical"):
        if g2 is None:
            return Sketcher.Constraint(t, g1)
        return Sketcher.Constraint(t, g1, pos(c.First, c.FirstPos), g2, pos(c.Second, c.SecondPos))
    if t == "PointOnObject":
        return Sketcher.Constraint(t, g1, pos(c.First, c.FirstPos), g2)
    return None


def _holds(sk, c):
    """A Horizontal / Vertical mapped onto a copy holds only if the copy lies so (a slanted mirror line turns it):
    added anyway, the solver would turn the copy. Every other tie a mirror keeps true."""
    if c.Type not in ("Horizontal", "Vertical"):
        return True
    i = 1 if c.Type == "Horizontal" else 0
    if c.Second == UNDEF:
        g = sk.Geometry[c.First]
        a, b = g.StartPoint, g.EndPoint
    else:
        a, b = sk.getPoint(c.First, c.FirstPos), sk.getPoint(c.Second, c.SecondPos)
    return abs(a[i] - b[i]) < 1e-7


def break_link(sk, geos):
    """Copies `geos` (mirrored with Mirror) no longer follow their originals: the mirror ties go, the originals' own
    ties come onto the copies (a Horizontal that a slanted mirror line turned no longer holds is left out).
    Returns the number of ties given to the copies. Call inside safe.run_safely."""
    idx, pairs, points = _ties(sk, geos)
    if not idx:
        raise ValueError("No mirror link here: select a copy made with Mirror")
    to_copy = {orig: copy for copy, orig in pairs.items()}
    own = [m for m in (_mapped(c, to_copy, points) for c in sk.Constraints if c.Type in KEEP) if m is not None]
    for i in reversed(idx):
        sk.delConstraint(i)
    given = 0
    for c in own:
        if not _holds(sk, c):
            continue
        n = sk.addConstraint(c)
        if sk.solve() in (-2, -3):  # it repeats another, or no longer holds (a Horizontal mirrored slanted)
            sk.delConstraint(n)
            continue
        given += 1
    return given
