"""Round walls for Move Face: a cylinder or a curve extruded straight along one direction (a hole, a boss, an extruded
ellipse), offset along its own surface by a ring slab fused on or cut away (closed walls: the section is a ring)."""
import FreeCAD as App
import Part

from incad.core import move_face as mf
from incad.core import offset2d

MIXED_MSG = "Pick flat faces or round walls, not both"
NOT_STRAIGHT_MSG = "Pick a flat face or a straight round wall"
OPEN_MSG = "Pick a whole round wall (a ring); a part of one comes later"
CROSSED_MSG = "A round wall crossed by another hole or slot comes later"
INWARD_BLEND_MSG = "Together can't move a fillet or chamfer inward yet — try Step"
_TOL = 1e-6


def direction(face):
    """The unit direction a straight round wall runs along, or None (a flat face, a cone, a sphere, a free form)."""
    if mf._is_plane(face):
        return None
    s = face.Surface
    while type(s).__name__ == "OffsetSurface":  # a wall moved Together before: OCC's offset of the wall's surface
        s = s.BasisSurface
    kind = type(s).__name__
    if kind == "Cylinder":
        return App.Vector(s.Axis).normalize()
    if kind == "SurfaceOfExtrusion":
        return App.Vector(s.Direction).normalize()
    if kind in ("BSplineSurface", "BezierSurface"):  # a straight B-spline wall (FreeCAD's refine makes them)
        u0, u1, v0, v1 = face.ParameterRange
        n1 = face.normalAt(u0 + (u1 - u0) * 0.25, (v0 + v1) / 2)
        n2 = face.normalAt(u0 + (u1 - u0) * 0.75, (v0 + v1) / 2)
        a = n1.cross(n2)
        if a.Length < 1e-9:
            return None
        a.normalize()
        return a if mf._along(face, a) else None
    return None


def is_round_wall(face):
    return direction(face) is not None


def _same_surface(face, other, a):
    d = direction(other)
    if d is None or abs(abs(d.dot(a)) - 1) > 1e-6:
        return False
    u0, u1, v0, v1 = other.ParameterRange
    p = other.valueAt((u0 + u1) / 2, (v0 + v1) / 2)
    try:
        return face.Surface.projectPoint(p, "LowerDistance") < 1e-6
    except Exception:
        return False


def pieces(shape, face):
    """Every face of the wall `face` belongs to: the faces of the same surface joined to it by shared edges."""
    a = direction(face)
    if a is None:
        raise ValueError(NOT_STRAIGHT_MSG)
    group, todo = [face], [face]
    while todo:
        f = todo.pop()
        for e in f.Edges:
            for g in shape.ancestorsOfType(e, Part.Face):
                if not any(g.isSame(x) for x in group) and _same_surface(face, g, a):
                    group.append(g)
                    todo.append(g)
    if any(len(f.Wires) > 1 for f in group):  # a hole or slot through the wall leaves an inner loop in it
        raise ValueError(CROSSED_MSG)
    return group, a


def extent(faces, a):
    hs = [p.dot(a) for f in faces for e in f.Edges for p in e.discretize(16)]
    return min(hs), max(hs)


def _iso_at(face, a, height):
    """The face's own curve at `height` along a (a circle, the extruded ellipse / spline itself), trimmed to the face:
    exact, where a slice gives a B-spline approximation that OCC's 2D offset cannot take. None for a B-spline wall."""
    s = face.Surface
    base = s
    while type(base).__name__ == "OffsetSurface":
        base = base.BasisSurface
    if type(base).__name__ not in ("Cylinder", "SurfaceOfExtrusion"):
        return None
    u0, u1, v0, v1 = face.ParameterRange
    h_at = lambda v: s.value(u0, v).dot(a)
    if abs(h_at(1.0) - h_at(0.0)) < 1e-12:
        return None
    v = (height - h_at(0.0)) / (h_at(1.0) - h_at(0.0))
    if s.isUPeriodic() and u1 - u0 > s.UPeriod():  # a fused face may run past one turn: once round, no overlap
        u1 = u0 + s.UPeriod()
    return Part.Edge(s.vIso(v), u0, u1)


def section(faces, a, h0, h1):
    """The wall cut square to a at mid height: a closed wire, else OPEN_MSG."""
    mid = (h0 + h1) / 2
    edges = []
    for f in faces:
        lo, hi = extent([f], a)
        if not lo - _TOL <= mid <= hi + _TOL:
            continue  # a piece above or below the section's height (the wall stacked in faces) adds nothing
        exact = _iso_at(f, a, mid)
        if exact is not None:
            edges.append(exact)
            continue
        for w in f.slice(a, mid):
            edges += w.Edges
    try:
        wire = Part.Wire(Part.__sortEdges__(edges))
    except Exception:
        raise ValueError(OPEN_MSG)
    if not wire.isClosed():
        raise ValueError(OPEN_MSG)
    return wire


def _middle(face):
    u0, u1, v0, v1 = face.ParameterRange
    return face.valueAt((u0 + u1) / 2, (v0 + v1) / 2), face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)


def material_inside(faces, wire, a):
    """True when the material is inside the section (a boss), False when outside (a hole): a step off the wall along
    its outward normal (FreeCAD's normalAt follows the solid's face orientation) lands outside or inside it."""
    p, n = _middle(faces[0])
    probe = p + n * 0.01
    probe = probe - a * (probe.dot(a) - wire.Vertexes[0].Point.dot(a))  # onto the section's plane
    return not Part.Face(wire).isInside(probe, 1e-7, True)


def _ring(wire, d, inside):
    """The region between the section and its offset by d away from (d > 0: into the room beside) the material."""
    s = 1 if inside else -1
    try:  # the face offset, not the wire: a face made of an offset ellipse's wire is invalid (OCC), this one is not
        a = Part.Face(wire)
        b = offset2d.offset2d(a, s * d, 2)  # join 2: intersection (a smooth outline stays smooth)
    except Exception:
        raise ValueError(mf.TOO_LARGE_MSG)
    if not b.isValid() or b.Area < _TOL:
        raise ValueError(mf.TOO_LARGE_MSG)
    # an outline offset past its curvature folds: OCC trims the fold into straight pieces. Every point of the old
    # outline must then still be exactly |d| from the new one
    new_outline = b.OuterWire
    for e in wire.Edges:
        for p in e.discretize(48):
            if abs(new_outline.distToShape(Part.Vertex(p))[0] - abs(d)) > 1e-4:
                raise ValueError(mf.TOO_LARGE_MSG)
    ring = b.cut(a) if s * d > 0 else a.cut(b)
    if ring.Area < _TOL:
        raise ValueError(mf.TOO_LARGE_MSG)
    return ring


def _rim_neighbours(shape, faces):
    """The faces across the wall's top and bottom edges (not its own pieces)."""
    out = []
    for f in faces:
        for e in f.Edges:
            for g in shape.ancestorsOfType(e, Part.Face):
                if not any(g.isSame(x) for x in faces) and not any(g.isSame(x) for x in out):
                    out.append(g)
    return out


def _toward(shape, plane_face, faces, a, mid):
    """A point on the moved faces' side of a flat face's plane: on the edge they share, stepped toward the wall's
    middle height (a section point can lie beyond a steep slanted end, on the air side of its plane)."""
    for f in faces:
        for e in f.Edges:
            if any(e.isSame(x) for x in plane_face.Edges):
                p = e.valueAt((e.FirstParameter + e.LastParameter) / 2)
                inward = a * (mid - p.dot(a))
                return p + inward * (min(0.1, inward.Length / 2) / inward.Length if inward.Length > _TOL else 0)
    return plane_face.CenterOfMass - mf._normal(plane_face)  # no shared edge: the face's own inner side


def _half_space(plane_face, toward, size, point=None, normal=None):
    """A big solid on the side of a flat face's plane (or of the plane through `point` square to `normal`) where
    `toward` (a point of the wall) is: a boss's top keeps what is below it, the block's face the boss stands on keeps
    what is above it."""
    n = normal if normal is not None else mf._normal(plane_face)
    c = point if point is not None else plane_face.CenterOfMass
    side = n if (toward - c).dot(n) > 0 else n * -1
    u = n.cross(App.Vector(1, 0, 0) if abs(n.x) < 0.9 else App.Vector(0, 1, 0)).normalize()
    v = n.cross(u)
    sheet = Part.Face(Part.makePolygon([c + (u * x + v * y) * size for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1),
                                                                               (-1, -1))]))
    return sheet.extrude(side * 2 * size)


def _slab(shape, faces, a, d, mode):
    h0, h1 = extent(faces, a)
    wire = section(faces, a, h0, h1)
    inside = material_inside(faces, wire, a)
    ring = _ring(wire, d, inside)
    mid = (h0 + h1) / 2
    size = shape.BoundBox.DiagonalLength + abs(d) + 1
    if mode == "step":
        # the wall alone: a flat end is met as Inventor meets it (a slanted top: no material above it, no gap under
        # it); at a fillet / chamfer the slab stops at the wall's own end, leaving the ledge
        slab = ring.translated(a * (h0 - mid - size)).extrude(a * (h1 - h0 + 2 * size))
        middle = wire.Vertexes[0].Point
        for g in _rim_neighbours(shape, faces):
            if mf._is_plane(g):
                slab = slab.common(_half_space(g, _toward(shape, g, faces, a, mid), size))
            else:
                end = h1 if g.CenterOfMass.dot(a) > mid else h0
                slab = slab.common(_half_space(g, middle, size, point=middle + a * (end - mid), normal=a))
        return slab
    if mode == "inventor":
        ends = _rim_neighbours(shape, faces)
        if any(not mf._is_plane(g) for g in ends):
            raise ValueError(mf.ROUND_MSG)
        slab = ring.translated(a * (h0 - mid - size)).extrude(a * (h1 - h0 + 2 * size))
        for g in ends:  # trimmed / extended to the planes of the flat ends (slanted ones too)
            slab = slab.common(_half_space(g, _toward(shape, g, faces, a, mid), size))
        return slab
    if mode == "together":
        return _together(shape, faces, a, d, wire, inside)
    raise NotImplementedError(mode)


def _together(shape, faces, a, d, wire, inside):
    """The wall and the fillets / chamfers chained to its top and bottom edges move as one piece: those faces, as an
    open sheet, thickened by d (OCC fills the layer between them and their offset), trimmed to the flat faces
    beyond them (which extend or trim to meet it). Only those faces move: other walls at that height — an inner
    hole, a shell's inner wall 1 mm in — are not touched. The faces beyond must be flat or run along a."""
    group = list(faces)
    todo = list(faces)
    while todo:
        f = todo.pop()
        for g in _rim_neighbours(shape, [f]):
            if any(g.isSame(x) for x in group) or mf._is_plane(g) or mf._along(g, a):
                continue
            group.append(g)
            todo.append(g)
    if d < 0 and len(group) > len(faces):  # exact inward needs the blends re-made at their size (a later phase)
        raise ValueError(INWARD_BLEND_MSG)
    beyond = _rim_neighbours(shape, group)
    for g in beyond:  # a guard: the chain takes every other kind of neighbour, so this holds by construction
        if not (mf._is_plane(g) or mf._along(g, a)):
            raise ValueError(mf.WALLS_MSG)
    sheet = Part.Shell(group)
    if any(type(g.Surface).__name__ == "OffsetSurface" for g in group):
        sheet = sheet.toNurbs()  # OCC cannot offset an offset surface again (a band moved Together before)
    try:  # arc joins (OCC's intersection joins fail on blends); FreeCAD raises ValueError / CADKernelError
        layer = sheet.makeOffsetShape(d, 1e-6, join=0, fill=True)
    except Exception:
        raise ValueError(mf.TOO_LARGE_MSG)
    if layer.isNull() or not layer.isValid():
        raise ValueError(mf.TOO_LARGE_MSG)
    size = shape.BoundBox.DiagonalLength + abs(d) + 1
    h0, h1 = extent(faces, a)
    for g in beyond:
        if mf._is_plane(g):
            layer = layer.common(_half_space(g, _toward(shape, g, group, a, (h0 + h1) / 2), size))
    return layer


def offset(shape, walls, d, mode):
    """The part with every picked wall offset by d (+ away from the material, i.e. more material)."""
    if abs(d) < _TOL:
        return shape
    adds, cuts = [], []
    try:
        for picked in walls:
            faces, a = pieces(shape, picked)
            slab = _slab(shape, faces, a, d, mode)
            if slab.isNull() or slab.Volume < 1e-6:  # nothing would move: a refusal, never "done" unchanged
                raise ValueError(mf.TOO_LARGE_MSG)
            if d < 0:
                if slab.cut(shape).Volume > 1e-6:  # the cut must lie inside the material
                    raise ValueError(mf.TOO_LARGE_MSG)
                cuts.append(slab)
            else:
                adds.append(slab)
        return mf.combine(shape, adds, cuts)
    except ValueError as e:  # FreeCAD raises OCC failures as ValueError too ("Null shape"): only our own messages pass
        if str(e) in _known():
            raise
        raise ValueError(mf.TOO_LARGE_MSG)
    except Exception:  # Part.OCCError, Base.CADKernelError
        raise ValueError(mf.TOO_LARGE_MSG)


def _known():
    return {MIXED_MSG, NOT_STRAIGHT_MSG, OPEN_MSG, CROSSED_MSG, INWARD_BLEND_MSG, mf.TOO_LARGE_MSG, mf.ROUND_MSG,
            mf.WALLS_MSG, mf.GONE_MSG}


def signature(shape, face):
    """The wall by geometry: its direction and points of its section (the mid height ring, sampled)."""
    faces, a = pieces(shape, face)
    h0, h1 = extent(faces, a)
    wire = section(faces, a, h0, h1)
    return {"kind": "wall", "a": [a.x, a.y, a.z], "pts": [list(p) for p in wire.discretize(16)]}


def find(shape, sig):
    """A face of the stored wall in `shape`: a straight round wall along a on which the stored points lie (the part
    may have moved it along a: points of the ring slid along a still lie on an extruded wall's surface)."""
    a = App.Vector(*sig["a"])
    pts = [App.Vector(*p) for p in sig["pts"]]
    on_surface = []
    for f in shape.Faces:
        d = direction(f)
        if d is None or abs(abs(d.dot(a)) - 1) > 1e-6:
            continue
        try:
            if not all(f.Surface.projectPoint(p, "LowerDistance") < 1e-5 for p in pts):
                continue
        except Exception:
            continue
        on_surface.append(f)
    # the face that holds the points (a shaft's lower segment, never its upper one on the same cylinder); when none
    # does (an earlier feature slid the wall along a), the nearest face of that surface
    holding = [f for f in on_surface if all(f.distToShape(Part.Vertex(p))[0] < 1e-5 for p in pts)]
    if holding:
        return holding[0]
    if not on_surface:
        return None
    return min(on_surface, key=lambda f: sum(f.distToShape(Part.Vertex(p))[0] for p in pts))


def arrow(shape, face, d):
    """The arrow's place: a point of the wall at mid height (on its section) moved by d along the outward normal."""
    faces, a = pieces(shape, face)
    h0, h1 = extent(faces, a)
    p = section(faces, a, h0, h1).Vertexes[0].Point
    owner = min(faces, key=lambda f: f.distToShape(Part.Vertex(p))[0])
    u, v = owner.Surface.parameter(p)
    n = owner.normalAt(u, v)
    return p + n * d, n
