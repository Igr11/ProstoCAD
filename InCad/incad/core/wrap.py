"""Emboss geometry (Inventor's Emboss / Engrave, with Wrap to face): a profile's faces (a Text's letters, a sketch's
regions) raised from or cut into a face of the part. On a cylinder or a cone the profile is wrapped: both unroll flat
without stretching, so a letter's outline maps exactly onto it — across the profile goes round the axis (arc length
= the flat distance), up it goes along the axis — and the letter is thickened along the radius. On a flat face it is
a straight emboss along the face's normal. The profile's plane must be parallel to the cylinder's axis (a plane
touching it, say); the profile lands on the side of the cylinder facing that plane. A sphere cannot be unrolled: the
profile is projected onto it along the sketch's normal (Inventor's emboss on a curved face), its walls along that
direction, its height along the radius."""
import math

import FreeCAD as App
import Part

from incad.core import faceted

ROUND_MSG = "Put the profile's sketch on a plane parallel to the cylinder's axis (a plane touching it, for example)"
FLAT_MSG = "Pick a flat face parallel to the profile's sketch, or a round (cylindrical) face"
FACE_MSG = "Pick a round (cylinder, cone or sphere) or a flat face of the part to emboss on"
SPHERE_EDGE_MSG = ("Some of the profile is past the sphere's edge as seen from the sketch: move it towards the sphere's "
                   "centre or shrink it")
SPHERE_SPREAD_MSG = "Evenly round works on a cylinder or a cone; on a sphere the profile is projected as drawn"
OFF_MSG = "Some of the profile is off the face (letters past its edge, or too deep): move or shrink it"
_TOL = 1e-7


def _unit(v):
    v = App.Vector(v)
    if v.Length < 1e-12:
        raise ValueError("Zero direction")
    v.normalize()
    return v


def is_round(face):
    """A cylinder or a cone: the faces a profile can be wrapped round."""
    return face.Surface.TypeId in ("Part::GeomCylinder", "Part::GeomCone")


def is_flat(face):
    return face.Surface.TypeId == "Part::GeomPlane"


def is_sphere(face):
    return face.Surface.TypeId == "Part::GeomSphere"


def _profile_normal(faces):
    n = faces[0].normalAt(*faces[0].Surface.parameter(faces[0].CenterOfMass)[:2]) if faces else None
    return _unit(n)


def _to2d(edge, uv):
    """The edge (in the profile's plane) as a 2D curve in the surface's parameters, through the linear map uv(point)
    -> (u, v): lines stay lines, everything else as its exact B-spline with mapped poles."""
    c = edge.Curve
    if type(c).__name__ in ("Line", "LineSegment"):
        a, b = uv(edge.Vertexes[0].Point), uv(edge.Vertexes[-1].Point)
        return Part.Geom2d.Line2dSegment(App.Base.Vector2d(*a), App.Base.Vector2d(*b))
    bs = c if isinstance(c, Part.BSplineCurve) else edge.toNurbs().Edges[0].Curve
    if abs(edge.FirstParameter - bs.FirstParameter) > 1e-12 or abs(edge.LastParameter - bs.LastParameter) > 1e-12:
        bs = bs.copy()
        bs.segment(edge.FirstParameter, edge.LastParameter)
    poles = [App.Base.Vector2d(*uv(p)) for p in bs.getPoles()]
    b2 = Part.Geom2d.BSplineCurve2d()
    b2.buildFromPolesMultsKnots(poles, bs.getMultiplicities(), bs.getKnots(), bs.isPeriodic(), bs.Degree,
                                bs.getWeights())
    return b2


def _face_on(surface, face, uv):
    """The profile face rebuilt on the surface: its outer wire and its holes mapped through uv."""
    def wire(w):
        return Part.Wire([_to2d(e, uv).toShape(surface) for e in w.OrderedEdges])
    outer = face.OuterWire
    made = Part.Face(surface, wire(outer))
    for w in face.Wires:
        if not w.isSame(outer):  # a hole (O, A, Р): cut out of the face on the surface
            made = made.cut(Part.Face(surface, wire(w)))
    made = made.Faces[0]
    made.validate()
    return made


def _on_the_face(letter, face):
    """Every letter lies wholly on the picked face (Inventor refuses a profile past the face's edge)."""
    try:
        inside = letter.common(face).Area
    except Part.OCCError:
        inside = 0.0
    if inside < letter.Area * (1 - 1e-6) - 1e-9:
        raise ValueError(OFF_MSG)


def _letters(faces, w, origin):
    """The faces grouped into letters by where they lie across the profile (an i's dot, a Ж's parts are one
    letter): [(centre across, [faces])] in reading order."""
    spans = []
    for f in faces:
        b = [(v.Point - origin).dot(w) for v in f.Vertexes] or [0.0]
        spans.append((min(b), max(b), f))
    spans.sort(key=lambda t: t[0])
    groups = []
    for lo, hi, f in spans:
        if groups and lo < groups[-1][1] - 1e-9:
            g = groups[-1]
            groups[-1] = (g[0], max(g[1], hi), g[2] + [f])
        else:
            groups.append((lo, hi, [f]))
    return [((lo + hi) / 2.0, fs) for lo, hi, fs in groups]


def _spread(faces, w, origin, circumference):
    """Evenly round: the letters keep their widths, their spacing grows so they go once round the whole face (the
    gap after the last letter as the others), centred where the profile faces the part."""
    letters = _letters(faces, w, origin)
    if len(letters) < 2:
        return faces
    first, last = letters[0][0], letters[-1][0]
    pitch = (last - first) / (len(letters) - 1)
    if pitch <= 1e-9:
        return faces
    f = circumference / ((last - first) + pitch)
    mid = (first + last) / 2.0
    out = []
    for c, fs in letters:
        shift = (c - mid) * f - c  # its new centre, measured from the profile's centre
        for face in fs:
            g = face.copy()
            g.translate(w * shift)
            out.append(g)
    return out


def _wrap_round(profile_faces, round_face, depth, spread=False, normal=None):
    """The profile's faces wrapped on the cylinder or cone and thickened `depth` along its normal (outward > 0,
    inward < 0). Both unroll flat without stretching: across the profile goes round the axis (arc length = the flat
    distance, at the profile's centre), up it goes along the surface (a cone: along its slant). spread: the letters
    evenly round the whole face."""
    surface = round_face.Surface.copy()
    a, centre, r = _unit(surface.Axis), App.Vector(surface.Center), surface.Radius
    cone = surface.TypeId == "Part::GeomCone"
    alpha = surface.SemiAngle if cone else 0.0
    n_sk = _unit(normal) if normal is not None else _profile_normal(profile_faces)
    if abs(n_sk.dot(a)) > 1e-6:
        raise ValueError(ROUND_MSG)
    bb = Part.makeCompound(profile_faces).BoundBox
    pc = App.Vector(bb.Center)  # the profile's centre: it lands on the face where the face meets the profile's side
    zc = (pc - centre).dot(a)
    vc = zc / math.cos(alpha)
    rc = r + vc * math.sin(alpha)  # the face's radius at the profile's height
    if rc <= 1e-6:
        raise ValueError("The profile sits past the cone's tip: move it to where the cone is round")
    off = pc - (centre + a * zc)
    d = off.dot(n_sk)  # the profile plane's distance from the axis, along its normal
    n = n_sk if d >= 0 else n_sk * -1  # from the axis towards the plane (a plane through the axis: its own side)
    w = a.cross(n)  # across the profile = round the axis (seen from outside, to the right)
    base = centre + a * zc + n * rc
    u0, v0 = surface.parameter(base)
    probe = surface.value(u0 + 1e-3, v0) - surface.value(u0, v0)
    su = 1.0 if probe.dot(w) > 0 else -1.0
    sv = 1.0 if (surface.value(u0, v0 + 1.0) - surface.value(u0, v0)).dot(a) > 0 else -1.0
    if spread:
        profile_faces = _spread(profile_faces, w, pc, 2 * math.pi * rc)

    def uv(p):
        q = p - pc
        return (u0 + su * q.dot(w) / rc, v0 + sv * q.dot(a))  # up the profile = along the surface (its slant)

    from incad.core import safe
    out = []
    for f in profile_faces:
        safe.stop_if_esc("the Emboss")
        on = _face_on(surface, f, uv)
        _on_the_face(on, round_face)
        # outward is away from the axis: a part's surface may point inward (its face is then the reversed one)
        p = on.CenterOfMass
        uu, vv = on.Surface.parameter(p)[:2]
        radial = p - (centre + a * (p - centre).dot(a))
        sign = 1.0 if on.normalAt(uu, vv).dot(radial) > 0 else -1.0
        out.append(on.makeOffsetShape(depth * sign, 1e-6, fill=True))
    return out


def _project_sphere(profile_faces, sphere_face, depth, spread=False, normal=None):
    """The profile projected onto the sphere along the sketch's normal, on the half that faces the sketch, and given
    `depth` along the radius (out of the material > 0, into it < 0): each letter is its prism along the normal cut
    by the spherical shell between the face and `depth` from it."""
    if spread:
        raise ValueError(SPHERE_SPREAD_MSG)
    s = sphere_face.Surface
    c, r = App.Vector(s.Center), s.Radius
    n_sk = _unit(normal) if normal is not None else _profile_normal(profile_faces)
    pc = App.Vector(Part.makeCompound(profile_faces).BoundBox.Center)
    toward = n_sk if (pc - c).dot(n_sk) >= 0 else n_sk * -1  # from the centre towards the sketch's side
    p0 = c + toward * r
    u0, v0 = s.parameter(p0)[:2]
    out_of_material = 1.0 if sphere_face.normalAt(u0, v0).dot(toward) > 0 else -1.0  # a ball: outward; a hollow: in
    lo, hi = sorted((r, r + depth * out_of_material))
    if lo <= 1e-6:
        raise ValueError("Too deep for this sphere: make the depth smaller than its radius")
    shell = Part.makeSphere(hi, c).cut(Part.makeSphere(lo, c))
    full = Part.makeSphere(r, c).Faces[0]
    reach = r + abs(depth) + 1.0
    out = []
    for f in profile_faces:
        for v in f.Vertexes:  # the sphere's outline seen along the normal: a letter past it hangs off
            q = v.Point - c
            if (q - toward * q.dot(toward)).Length >= min(lo, r) * (1 - 1e-6):
                raise ValueError(SPHERE_EDGE_MSG)
        g = f.copy()
        g.translate(toward * -((g.Vertexes[0].Point - c).dot(toward)))  # onto the plane through the centre
        prism = g.extrude(toward * reach)
        on = sphere_face.common(prism).Area
        if on < full.common(prism).Area * (1 - 1e-4) - 1e-9:  # past the picked face's own edge (a dome's rim)
            raise ValueError(OFF_MSG)
        out.append(prism.common(shell))
    return out


def _straight(profile_faces, flat_face, depth):
    """The profile moved onto the flat face (along its normal) and extruded `depth` along it (out > 0, in < 0)."""
    n = _unit(flat_face.normalAt(*flat_face.Surface.parameter(flat_face.CenterOfMass)[:2]))
    if abs(abs(_profile_normal(profile_faces).dot(n)) - 1) > 1e-6:
        raise ValueError(FLAT_MSG)
    gap = (flat_face.CenterOfMass - profile_faces[0].Vertexes[0].Point).dot(n)
    out = []
    for f in profile_faces:
        g = f.copy()
        g.translate(n * gap)
        _on_the_face(g, flat_face)
        out.append(g.extrude(n * depth))
    return out


def _faces_of(solid, edge, but):
    try:
        found = solid.ancestorsOfType(edge, Part.Face)
    except Exception:
        found = [f for f in solid.Faces if any(e.isSame(edge) for e in f.Edges)]
    return [f for f in found if not f.isSame(but)]


def _normal_at(face, p):
    u, v = face.Surface.parameter(p)[:2]
    return _unit(face.normalAt(u, v))


def _along(face, a):
    """A face that runs straight along `a`: a plane containing it, or a cylinder round it."""
    if is_flat(face):
        return abs(_normal_at(face, face.CenterOfMass).dot(a)) < 1e-6
    return face.Surface.TypeId == "Part::GeomCylinder" and abs(abs(_unit(face.Surface.Axis).dot(a)) - 1) < 1e-6


def _straight_edges(face, a):
    """The face's straight edges along `a`."""
    out = []
    for e in face.Edges:
        if type(e.Curve).__name__ in ("Line", "LineSegment") and len(e.Vertexes) > 1 and \
                abs(abs(_unit(e.Vertexes[-1].Point - e.Vertexes[0].Point).dot(a)) - 1) < 1e-6:
            out.append(e)
    return out


def _smooth_axis(face, solid):
    """The axis of a round face that runs on smoothly from the flat `face` (the end of a slot-shaped part), or None:
    the direction along which a profile can run on round onto it."""
    for e in face.Edges:
        if type(e.Curve).__name__ not in ("Line", "LineSegment") or len(e.Vertexes) < 2:
            continue
        for g in _faces_of(solid, e, face):
            if g.Surface.TypeId != "Part::GeomCylinder":
                continue
            a = _unit(g.Surface.Axis)
            p = e.valueAt((e.FirstParameter + e.LastParameter) / 2)
            if abs(abs(_unit(e.Vertexes[-1].Point - e.Vertexes[0].Point).dot(a)) - 1) < 1e-6 and \
                    _normal_at(g, p).dot(_normal_at(face, p)) > 1 - 1e-6:
                return a
    return None


def _chain(solid, start, place0, tangent0, a, base, w, s_lo, s_hi, want_lo, want_hi):
    """The faces the profile runs onto past the start face, each way along `w`, while they go on smoothly (the same
    normal at the shared edge) and run along `a`: [(lo, hi, face, place)] in arc length s from `base`, where
    place(s, t) is the point at s and t along `a` from base's level. Stops where the profile ends or the way round
    comes back to the start face."""
    pieces = []
    for sign, s_edge, want in ((1.0, s_hi, want_hi), (-1.0, s_lo, want_lo)):
        cur, d = start, tangent0(s_edge) * sign  # exact: a chord's slant tilted the next flat piece
        out_point = place0(s_edge, 0.0)
        reach = sign * s_edge  # arc length covered this way, from base
        while sign * want > reach + 1e-9:
            # where the profile leaves this face: its edge along the axis at the point it reaches
            edge = min(_straight_edges(cur, a), key=lambda e: e.distToShape(Part.Vertex(out_point))[0], default=None)
            if edge is None or edge.distToShape(Part.Vertex(out_point))[0] > 1e-4:
                break
            mid = edge.valueAt((edge.FirstParameter + edge.LastParameter) / 2)
            p0 = mid - a * (mid - base).dot(a)  # on the edge, at base's level
            nxt = [g for g in _faces_of(solid, edge, cur)
                   if _along(g, a) and _normal_at(g, mid).dot(_normal_at(cur, mid)) > 1 - 1e-6]
            if not nxt or nxt[0].isSame(start):
                break
            g = nxt[0]
            if is_flat(g):
                width = max((v.Point - p0).dot(d) for v in g.Vertexes)
                if width <= 1e-9:
                    break

                def place(s, t, p0=p0, d=d, s0=reach, k=sign):
                    return p0 + d * (k * s - s0) + a * t
                out_point = p0 + d * width
            else:
                cyl = g.Surface
                c = App.Vector(cyl.Center)
                c0 = c + a * (base - c).dot(a)
                r = cyl.Radius
                e1, e2 = _unit(p0 - c0), d
                u1, u2 = g.ParameterRange[:2]
                th = abs(u2 - u1)
                width = th * r

                def place(s, t, c0=c0, r=r, e1=e1, e2=e2, s0=reach, k=sign):
                    q = (k * s - s0) / r
                    return c0 + (e1 * math.cos(q) + e2 * math.sin(q)) * r + a * t
                out_point = c0 + (e1 * math.cos(th) + e2 * math.sin(th)) * r
                d = _unit(e2 * math.cos(th) - e1 * math.sin(th))
            lo, hi = reach, reach + width
            pieces.append((min(sign * lo, sign * hi), max(sign * lo, sign * hi), g, place))
            reach = hi
            cur = g
    return pieces


def _run_on(profile_faces, start, depth, normal=None, solid=None):
    """A profile past the edge of the picked face runs on round onto the faces that go on smoothly from it (a
    slot-shaped part's flat side and its round ends, or a round face and the flat sides after it), as if wrapped:
    across the profile is the arc length along those faces, up it runs along their common axis. Each letter is cut
    where the faces meet, each piece laid on its face without stretching and given `depth` along that face's normal
    (out of the material > 0). None when no face goes on smoothly from the picked one."""
    n_sk = _unit(normal) if normal is not None else _profile_normal(profile_faces)
    if is_flat(start):
        a = _smooth_axis(start, solid)
        if a is None:
            return None
        n0 = _normal_at(start, start.CenterOfMass)
        if abs(abs(n_sk.dot(n0)) - 1) > 1e-6:
            raise ValueError(FLAT_MSG)
        pc = App.Vector(Part.makeCompound(profile_faces).BoundBox.Center)
        base = pc + n0 * (start.CenterOfMass - pc).dot(n0)
        w = a.cross(n0)
        spans = [(v.Point - base).dot(w) for v in start.Vertexes]
        s_lo, s_hi = min(spans), max(spans)

        def place0(s, t):
            return base + w * s + a * t

        def tangent0(s):
            return w
    else:
        cyl = start.Surface
        a, c, r = _unit(cyl.Axis), App.Vector(cyl.Center), cyl.Radius
        if abs(n_sk.dot(a)) > 1e-6:
            raise ValueError(ROUND_MSG)
        pc = App.Vector(Part.makeCompound(profile_faces).BoundBox.Center)
        c0 = c + a * (pc - c).dot(a)
        n0 = n_sk if (pc - c0).dot(n_sk) >= 0 else n_sk * -1
        base = c0 + n0 * r
        w = a.cross(n0)
        # the start face's own arc, measured from base: its edges along the axis, as angles round it
        angles = []
        for v in start.Vertexes:
            q = v.Point - c0 - a * (v.Point - c0).dot(a)
            angles.append(math.atan2(q.dot(w), q.dot(n0)) * r)
        s_lo, s_hi = min(angles), max(angles)

        def place0(s, t):
            th = s / r
            return c0 + (n0 * math.cos(th) + w * math.sin(th)) * r + a * t

        def tangent0(s):
            th = s / r
            return w * math.cos(th) - n0 * math.sin(th)
    s_all = [(v.Point - pc).dot(w) for f in profile_faces for v in f.Vertexes]
    t_all = [(v.Point - pc).dot(a) for f in profile_faces for v in f.Vertexes]
    want_lo, want_hi = min(s_all), max(s_all)
    pieces = [(s_lo, s_hi, start, place0)] + _chain(solid, start, place0, tangent0, a, base, w, s_lo, s_hi, want_lo, want_hi)
    covered_lo = min(lo for lo, _, _, _ in pieces)
    covered_hi = max(hi for _, hi, _, _ in pieces)
    if want_lo < covered_lo - 1e-6 or want_hi > covered_hi + 1e-6:
        raise ValueError(OFF_MSG)
    if len(pieces) == 1:
        return None  # nothing to run on to: the plain emboss says what is wrong
    t_reach = (max(t_all) - min(t_all)) + 1.0
    out = []
    for lo, hi, face, place in pieces:
        corners = [pc + w * lo + a * (min(t_all) - t_reach), pc + w * hi + a * (min(t_all) - t_reach),
                   pc + w * hi + a * (max(t_all) + t_reach), pc + w * lo + a * (max(t_all) + t_reach)]
        slab = Part.Face(Part.makePolygon(corners + corners[:1]))
        for f in profile_faces:
            try:
                part = f.common(slab)
            except Part.OCCError:
                continue
            for piece in part.Faces:
                if piece.Area < 1e-9:
                    continue
                out.append(_lay(piece, face, place, pc, w, a, depth, solid))
    return out


def _lay(piece, face, place, pc, w, a, depth, solid):
    """One piece of a letter (flat, in the profile's plane) laid onto `face` through place(s, t) and thickened."""
    def st(p):
        q = p - pc
        return q.dot(w), q.dot(a)
    if is_flat(face):
        o, x_to, y_to = place(0.0, 0.0), place(1.0, 0.0), place(0.0, 1.0)  # a plane: place is a rigid motion
        frame_to = App.Matrix()
        x, y = x_to - o, y_to - o
        z = x.cross(y)
        for i, v in enumerate((x, y, z)):
            frame_to.setCol(i, v)
        frame_to.setCol(3, o)
        n_from = w.cross(a)
        frame_from = App.Matrix()
        for i, v in enumerate((w, a, n_from)):
            frame_from.setCol(i, v)
        frame_from.setCol(3, pc)
        g = piece.copy()
        g.transformShape(frame_to.multiply(frame_from.inverse()))
        _on_the_face(g, face)
        return g.extrude(_normal_at(face, g.CenterOfMass) * depth)
    surface = face.Surface.copy()
    p0 = place(0.0, 0.0)
    u_ref, v_ref = surface.parameter(p0)[:2]
    eps = 1e-3
    du = (surface.value(u_ref + eps, v_ref) - surface.value(u_ref, v_ref)).dot(place(eps * surface.Radius, 0.0) - p0)
    su = 1.0 if du > 0 else -1.0
    sv = 1.0 if (surface.value(u_ref, v_ref + 1.0) - surface.value(u_ref, v_ref)).dot(a) > 0 else -1.0

    def uv(p):
        s, t = st(p)
        return u_ref + su * s / surface.Radius, v_ref + sv * t
    on = _face_on(surface, piece, uv)
    _on_the_face(on, face)
    c = on.CenterOfMass
    uu, vv = on.Surface.parameter(c)[:2]
    sign = 1.0 if on.normalAt(uu, vv).dot(_normal_at(face, on.Surface.value(uu, vv))) > 0 else -1.0
    return on.makeOffsetShape(depth * sign, 1e-6, fill=True)


def tools(profile_faces, face, depth, spread=False, normal=None, solid=None):
    """The solids an emboss (depth > 0) adds or an engrave (depth < 0) removes. solid: the part, whose faces going on
    smoothly from the picked one a profile past its edge runs onto."""
    if not profile_faces:
        raise ValueError("The profile has no closed shapes to emboss")
    try:
        if is_round(face):
            return _wrap_round(profile_faces, face, depth, spread, normal)
        if is_flat(face):
            return _straight(profile_faces, face, depth)
    except ValueError as e:  # past the face's edge: on round onto the faces that go on smoothly from it
        if str(e) != OFF_MSG or spread or solid is None or face.Surface.TypeId not in ("Part::GeomPlane",
                                                                                       "Part::GeomCylinder"):
            raise
        run = _run_on(profile_faces, face, depth, normal, solid)
        if run is None:
            raise
        return run
    if is_sphere(face):
        return _project_sphere(profile_faces, face, depth, spread, normal)
    raise ValueError(FACE_MSG)


def apply(shape, profile_faces, face, depth, engrave, spread=False, normal=None):
    """The part with the profile embossed on (or engraved into) the face; refused when letters hang off it. An
    engrave may go right through a thin wall: the loose letter middles are dropped."""
    if depth <= 0:
        raise ValueError("Depth must be more than 0")
    cut = tools(profile_faces, face, -depth if engrave else depth, spread, normal, shape)
    before = len(shape.Solids)
    result = shape.cut(cut) if engrave else shape.fuse(cut)
    if not faceted.is_faceted(shape):  # an STL's triangles are merged already, and merging thousands took half the time
        result = result.removeSplitter()
    if engrave and len(result.Solids) > before:
        # cut right through a thin wall (a stencil): the middles of O, A, D come loose — they would fall out; the part
        # keeps its own pieces, the largest ones
        keep = sorted(result.Solids, key=lambda x: x.Volume, reverse=True)[:before]
        result = keep[0] if len(keep) == 1 else Part.makeCompound(keep)
    if not result.isValid() or len(result.Solids) != before:
        raise ValueError(OFF_MSG)
    if engrave and abs(result.Volume - shape.Volume) < 1e-9:
        raise ValueError(OFF_MSG)
    return result
