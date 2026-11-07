"""Slots Inventor draws but FreeCAD's Sketcher doesn't: Overall (picked points are the outer ends) and Center
Point (center, one end center). Geometry in sketch coordinates; built like FreeCAD's slot (two arcs, two
lines, tangent end to end, equal arcs)."""
import math

import FreeCAD as App
import Part
import Sketcher


def _width_from(a, b, p):
    """Half the slot width: distance of p from the line a-b."""
    d = b - a
    if d.Length < 1e-9:
        raise ValueError("Pick two different points")
    u = App.Vector(d)
    u.normalize()
    w = p - a
    return abs(w.x * u.y - w.y * u.x)


START_RADIUS = 1.0  # mm: (kept for the tool's "widened" mark) a 2 mm start jumped, so the width follows the pointer
LEAST_RADIUS = 0.05  # the narrowest a slot gets while its width is set: never nothing (it vanished on the line)


def _at_least(r, length, least):
    """The width's half, never under `least` (nor so wide the ends meet on a short slot)."""
    return max(r, min(least, 0.4 * length))


def overall(p1, p2, p3, least=LEAST_RADIUS):
    """Outer ends p1, p2; width from p3. Returns (center1, center2, radius)."""
    d = p2 - p1
    r = _at_least(_width_from(p1, p2, p3), d.Length, least)
    if d.Length <= 2 * r + 1e-6:
        raise ValueError("The slot is wider than it is long: pick the ends farther apart")
    u = App.Vector(d)
    u.normalize()
    return p1 + u * r, p2 - u * r, r


def center_to_center(c1, c2, p3, least=LEAST_RADIUS):
    """The arcs' centers c1, c2; width from p3."""
    r = _at_least(_width_from(c1, c2, p3), 2.5 * (c2 - c1).Length, least)
    return c1, c2, r


def center_point(center, end, p3, least=LEAST_RADIUS):
    """Center of the slot, one end's arc center, width from p3."""
    r = _at_least(_width_from(center, end, p3), 2 * (end - center).Length, least)
    return center - (end - center), end, r


def add(sk, c1, c2, r):
    """Add the slot to the sketch; returns the new geometry ids (arc1, arc2, line1, line2)."""
    th = math.atan2(c2.y - c1.y, c2.x - c1.x)
    z = App.Vector(0, 0, 1)
    a1 = Part.ArcOfCircle(Part.Circle(App.Vector(c1.x, c1.y, 0), z, r), th + math.pi / 2, th + 3 * math.pi / 2)
    a2 = Part.ArcOfCircle(Part.Circle(App.Vector(c2.x, c2.y, 0), z, r), th - math.pi / 2, th + math.pi / 2)
    n = App.Vector(-math.sin(th), math.cos(th), 0) * r
    c1v, c2v = App.Vector(c1.x, c1.y, 0), App.Vector(c2.x, c2.y, 0)
    l1 = Part.LineSegment(c2v + n, c1v + n)   # from arc2's end to arc1's start
    l2 = Part.LineSegment(c1v - n, c2v - n)   # from arc1's end to arc2's start
    ids = sk.addGeometry([a1, a2, l1, l2])
    i1, i2, j1, j2 = ids
    sk.addConstraint([Sketcher.Constraint("Tangent", j1, 2, i1, 1), Sketcher.Constraint("Tangent", i1, 2, j2, 1),
                      Sketcher.Constraint("Tangent", j2, 2, i2, 1), Sketcher.Constraint("Tangent", i2, 2, j1, 1),
                      Sketcher.Constraint("Equal", i1, i2)])
    if abs(math.sin(th)) < 1e-9:
        sk.addConstraint(Sketcher.Constraint("Horizontal", j1))
    elif abs(math.cos(th)) < 1e-9:
        sk.addConstraint(Sketcher.Constraint("Vertical", j1))
    return ids
