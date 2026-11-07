"""Revolve on OpenCASCADE directly (the user's law: ProstoCAD's building operations are its own, not FreeCAD's
Revolution and Groove with workarounds on top).

The tool is the profile's faces turned about the axis (OCC's revolution): a full turn closes on itself, a profile
touching the axis (a side on it, or a corner) closes there, as one valid solid each. The axis is any straight line
(a line of the sketch of whatever kind, a work or origin axis, a straight edge of the part): a line of the sketch
that closes no outline is no part of the profile, it stays what it is (normal stays normal). Then with the part as
our Extrude (incad.core.extrude_solid.combine): a tool standing on a flat face of the part (a cylinder on a block,
a quarter turn off a side face) is sewn onto that face, else OCC's Boolean, every result checked for validity and
its volume, sound to the full check where the part was."""
import FreeCAD as App
import Part

from incad.core import extrude_solid

LIN = 1e-6  # mm: a point on the axis, a face on a face
ANG = 1e-9  # degrees: an angle that is a full turn


# ----- the axis -----
def axis_of_line(start, end):
    d = end - start
    if d.Length < LIN:
        raise ValueError("the axis has no length: pick another line")
    d.normalize()
    return App.Vector(start), d


def axis_of_edge(edge):
    """(point, unit direction) of a straight edge, along its line as FreeCAD's Revolution takes it."""
    curve = edge.Curve
    if curve.TypeId not in ("Part::GeomLine", "Part::GeomLineSegment"):
        raise ValueError("the axis must be straight: pick a line, an axis or a straight edge")
    d = App.Vector(curve.Direction)
    d.normalize()
    return App.Vector(curve.Location), d


def axis_of_object(obj):
    """(point, unit direction) of a work line (along its Z) or an origin axis (along its X), in its Solid's
    coordinates."""
    rot = obj.Placement.Rotation
    if obj.TypeId == "App::Line":
        return App.Vector(obj.Placement.Base), rot.multVec(App.Vector(1, 0, 0))
    return App.Vector(obj.Placement.Base), rot.multVec(App.Vector(0, 0, 1))


# ----- the profile -----
def _on_axis(edge, point, d):
    """True when the whole edge lies on the axis line."""
    if edge.Curve.TypeId not in ("Part::GeomLine", "Part::GeomLineSegment"):
        return False
    for v in edge.Vertexes:
        r = v.Point - point
        if (r - d * r.dot(d)).Length > LIN:
            return False
    return True


def profile_faces(shape, point, d):
    """The faces of the sketch's closed outlines; an open line (the axis line itself, of any kind) is no part of
    them. When the outlines cannot be made with every line in (a line on the axis touching an outline at a corner),
    the lines lying on the axis that close no outline are left out."""
    try:
        return extrude_solid.profile_faces(shape)
    except ValueError as first:
        edges = [e for e in shape.Edges if not _on_axis(e, point, d)]
        if len(edges) == len(shape.Edges) or not edges:
            raise first
        try:
            wires = [Part.Wire(group) for group in Part.sortEdges(edges)]
            return extrude_solid.profile_faces(Part.Compound(wires))
        except Exception:
            raise first


def plane_of(faces):
    """(point, unit normal) of the profile's plane."""
    face = faces[0]
    try:
        n = App.Vector(face.Surface.Axis)
        o = App.Vector(face.Surface.Position)
    except Exception:
        u, v = face.Surface.parameter(face.CenterOfMass)
        n, o = face.normalAt(u, v), face.CenterOfMass
    n.normalize()
    return o, n


def _check_axis(faces, point, d):
    """Plain errors for an axis the profile can't turn about: square to its plane, or crossing it."""
    o, n = plane_of(faces)
    if abs(abs(d.dot(n)) - 1) < 1e-9:
        raise ValueError("the axis is perpendicular to the profile: pick an axis that lies in the profile's plane")
    if abs(d.dot(n)) > 1e-7:
        return  # an axis at a slant to the plane: OCC's own check below decides
    side = d.cross(n)  # in the plane, square to the axis
    for f in faces:  # (each on its own: outlines on both sides of the axis turn each on its side)
        lo = hi = 0.0
        for e in f.Edges:
            for p in e.discretize(Number=max(8, min(200, int(e.Length) + 8))):
                s = (p - point).dot(side)
                lo, hi = min(lo, s), max(hi, s)
        if lo < -LIN * 10 and hi > LIN * 10:
            raise ValueError("the axis crosses the profile: choose another axis or move the profile off it")


def _turned(face, point, d, degrees):
    face = face.copy()
    face.rotate(point, d, degrees)
    return face


def _revolved(face, point, d, angle):
    """One face turned `angle` degrees about the axis: one valid solid."""
    try:
        made = face.revolve(point, d, angle)
    except Exception:
        raise ValueError("OpenCASCADE could not turn the profile about this axis: the axis must lie in the "
                         "profile's plane and not cross it")
    solids = made.Solids
    if len(solids) == 1 and solids[0].isValid() and solids[0].Volume > 0:
        return solids[0]
    if solids:
        s = solids[0] if len(solids) == 1 else made
        fixed = extrude_solid._sew([g for g in s.Faces if g.Area > 1e-12])  # a degenerate face on the axis
        if fixed is not None and fixed.Volume > 0:
            return fixed
    raise ValueError("the revolve makes no sound solid here: the axis must not cross or touch the profile along "
                     "a line inside it")


def tool(faces, point, d, angle, start=0.0):
    """The profile's faces turned from `start` to `start + angle` degrees about the axis (point, unit d)."""
    if angle <= ANG:
        raise ValueError("the angle must be more than 0°")
    if angle > 360 + ANG:
        raise ValueError("the angle is more than a full turn: make it 360° at most")
    _check_axis(faces, point, d)
    angle = min(angle, 360.0)
    solids = [_revolved(_turned(f, point, d, start) if abs(start) > ANG else f, point, d, angle) for f in faces]
    if len(solids) == 1:
        return solids[0]
    joined = solids[0].fuse(solids[1:])  # (outlines side by side: their turns may meet)
    out = [s for s in joined.Solids if s.Volume > 1e-12]
    return out[0] if len(out) == 1 else Part.Compound(out)


# ----- the tool on the part -----
def _flat(face):
    return face.Surface.TypeId == "Part::GeomPlane"


def seat(base, tool_shape, cut):
    """(faces, origin, d) for combine's sewn way: the tool's flat faces lying on a flat face of the part, the tool
    going out of the part (a Join) or into it (a Cut) from there; d points into the tool. None when there is none."""
    if base is None or tool_shape is None:
        return None
    best = None
    planes = [(g, App.Vector(g.Surface.Position), extrude_solid._outward(g)) for g in base.Faces if _flat(g)]
    for t in tool_shape.Faces:
        if not _flat(t):
            continue
        try:
            nt = extrude_solid._outward(t)
        except Exception:
            continue
        o = App.Vector(t.Surface.Position)
        for g, og, ng in planes:
            if abs(abs(nt.dot(ng)) - 1) > 1e-9 or abs((o - og).dot(ng)) > LIN:
                continue
            if (nt.dot(ng) > 0) != cut:
                continue  # a Join's face looks into the part's face, a Cut's lies along it
            if not t.BoundBox.intersect(g.BoundBox):
                continue
            d = -nt
            same = [x for x in tool_shape.Faces if _flat(x) and extrude_solid._in_plane(x, o, d)]
            area = sum(x.Area for x in same)
            if best is None or area > best[0]:
                best = (area, same, o, d)
            break
    return None if best is None else tuple(best[1:])


def combine(base, tool_shape, operation, refine=False):
    """(shape, how): see the module's notes and extrude_solid.combine."""
    faces = origin = d = None
    if base is not None and not base.isNull() and base.Solids and operation in ("join", "cut") \
            and len(base.Faces) <= extrude_solid.MANY_FACES:
        try:
            found = seat(base, tool_shape, operation == "cut")
        except Exception:
            found = None
        if found is not None:
            faces, origin, d = found
    return extrude_solid.combine(base, tool_shape, operation, faces, origin, d, refine)

