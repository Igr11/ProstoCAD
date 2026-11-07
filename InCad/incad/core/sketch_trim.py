"""Break Link on projected edges (the user: "do it like in Inventor"): a projected edge follows the part and is not
trimmed; Break Link makes it the sketch's own geometry of the same kind (construction stays construction), its
ties (tangents, points on it, its radius) moved onto the copy, and from then on it is trimmed like any line."""
import Sketcher

UNDEF = -2000  # FreeCAD's GeoUndef


def _moved(c, g, new):
    """Constraint c with projected edge g put as `new`, the other projected edges renumbered as FreeCAD will once g
    is gone (those after it move up by one); None for a kind not carried over."""
    def geo(x):
        if x == g:
            return new
        if x != UNDEF and x < g:
            return x + 1
        return x
    t = c.Type
    g1 = geo(c.First)
    g2 = geo(c.Second) if c.Second != UNDEF else None
    if t in ("Radius", "Diameter") and g2 is None:
        return Sketcher.Constraint(t, g1, c.Value)
    if t in ("Equal", "Parallel") and g2 is not None:
        return Sketcher.Constraint(t, g1, g2)
    if t in ("Coincident", "Tangent", "Perpendicular", "PointOnObject") and g2 is not None:
        if c.FirstPos == 0 and c.SecondPos == 0:
            return Sketcher.Constraint(t, g1, g2)
        if c.SecondPos == 0:
            return Sketcher.Constraint(t, g1, c.FirstPos, g2)
        return Sketcher.Constraint(t, g1, c.FirstPos, g2, c.SecondPos)
    return None


def own_copy(sk, g):
    """Projected edge g (-3, -4, …) made the sketch's own geometry of the same kind; returns its new id."""
    from incad.core import sketch as sketches
    geom = sk.ExternalGeo[-g - 1].copy()
    for kind in ("Sketcher::ExternalGeometryExtension", "Sketcher::SketchGeometryExtension"):
        try:  # the copy is no projection (and gets an id of its own)
            geom.deleteExtensionOfType(kind)
        except Exception:
            pass
    construction = not sketches.is_defining(sk, g)
    new = sk.addGeometry(geom, construction)
    ties = [m for m in (_moved(c, g, new) for c in sk.Constraints
                        if g in (c.First, c.Second, c.Third)) if m is not None]
    from incad.features import revolve
    revolve.externals_going(sk, [-g - 3])  # a revolve axis along it stays where it is
    sk.delExternal(-g - 3)
    for m in ties:
        sk.addConstraint(m)
    return new


def break_link(sk, geos):
    """Sketch ▸ Break Link on projected edges: each becomes the sketch's own geometry of its kind, no longer following
    the part (then it can be trimmed, as any line). The last projected first, so the ids of the others hold.
    Returns the new ids. Call inside safe.run_safely."""
    new = []
    for g in sorted({g for g in geos if g <= -3}):
        new.append(own_copy(sk, g))
        sk.solve()
    return new


def trim_projected(sk, g, point):
    """Trim projected edge g at `point` (sketch coordinates): it becomes own geometry of its kind, then is trimmed.
    Returns the new id. Call inside safe.run_safely."""
    new = own_copy(sk, g)
    sk.solve()
    sk.trim(new, point)
    return new
