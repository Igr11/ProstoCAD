"""Move Face: flat faces moved along their normal by d (outward > 0) — the slab between the faces' old and new place is
added (d > 0) or cut (d < 0). Three ways for the neighbours (the faces sharing an edge with a moved face):
- step: the slab is the face pushed straight (a slanted neighbour stays, a straight wall appears at its edge);
- inventor: the slab's side walls lie on the neighbours (flat at any angle, or a cylinder along the move), so a chamfer
  keeps its angle and grows / shrinks; a round neighbour across the move cannot follow yet;
- together: the face moves with its neighbours that are not along the move (chamfers, fillets) as one piece; the walls
  around them must run along the move and grow.
Faces are kept by geometry (signature / find), never by name. Pure Part code, headless."""
import FreeCAD as App
import Part

FACES_MSG = "The faces must be flat and face the same way"
PARALLEL_MSG = "Pick a face, plane, edge or point parallel to the faces"
ROUND_MSG = "A neighbouring face is round: Smart can't follow it yet — try Step or Together"
WALLS_MSG = "The walls around the chamfer / fillet must run along the move — try Smart or Step"
TOO_LARGE_MSG = "The move is too large: a neighbouring face would vanish"
GONE_MSG = "A face to move is no longer there"
_TOL = 1e-6
_ANG = 1e-6


def _is_plane(face):
    """A flat face, whatever its surface: OCC's offsets and booleans give flat faces back as B-splines too."""
    if face.Surface.__class__.__name__ == "Plane":
        return True
    if face.Surface.__class__.__name__ in ("Cylinder", "Cone", "Sphere", "Toroid", "SurfaceOfExtrusion"):
        return False
    try:
        return face.findPlane() is not None
    except Exception:
        return False


def _normal(face):
    """The outward normal of a flat face of a solid (FreeCAD's normalAt follows the face's orientation)."""
    return face.normalAt(0, 0)


def signature(face):
    n = _normal(face)
    pts = [v.Point for v in face.OuterWire.OrderedVertexes] or [face.CenterOfMass]
    if len(pts) < 3:  # a round face (a boss top): its outline sampled
        pts = face.OuterWire.discretize(24)
    return {"n": [n.x, n.y, n.z], "p": list(face.CenterOfMass), "outline": [list(p) for p in pts],
            "area": face.Area}


def _sig_face(sig):
    pts = [App.Vector(*p) for p in sig["outline"]]
    return Part.Face(Part.makePolygon(pts + [pts[0]]))


def find(shape, sig):
    """The face again: same direction, its outline overlapping the stored one seen along n; the stored plane first,
    then the most overlap, then the nearest plane (an earlier feature made the part taller: the top moved)."""
    n, p = App.Vector(*sig["n"]), App.Vector(*sig["p"])
    try:
        ref = _sig_face(sig)
    except Exception:
        return None
    best, key = None, None
    for f in shape.Faces:
        if not _is_plane(f) or _normal(f).dot(n) < 1 - _ANG:
            continue
        off = (f.CenterOfMass - p).dot(n)
        try:
            s = f.translated(n * -off).common(ref).Area
        except Exception:
            s = 0.0
        if s <= _TOL:
            continue
        k = (abs(off) > _TOL, -s, abs(off))
        if key is None or k < key:
            best, key = f, k
    return best


def split_blends(shape, faces):
    """(faces to move, blends picked with them): the flat faces facing the way of the first flat one move; any other
    picked face must be a chamfer / fillet that touches one of them (or another picked blend) and does not run along
    the move — it is carried with its face (Together). Anything else is no pick for Move Face."""
    flat = [f for f in faces if _is_plane(f)]
    if not flat:
        raise ValueError(FACES_MSG)
    n = _normal(flat[0])
    main = [f for f in flat if _normal(f).dot(n) > 1 - _ANG]
    rest = [f for f in faces if not any(_same(f, m) for m in main)]
    if not rest:
        return main, []
    if shape is None:
        raise ValueError(FACES_MSG)
    touched, blends, todo = list(main), [], list(rest)
    while todo:
        nxt = next((f for f in todo if not _along(f, n) and any(
            _neighbour(shape, t, e) is not None and _same(_neighbour(shape, t, e), f) for t in touched for e in t.Edges)),
            None)
        if nxt is None:
            raise ValueError(FACES_MSG)
        todo = [f for f in todo if not _same(f, nxt)]
        blends.append(nxt)
        touched.append(nxt)
    return main, blends


def check_faces(faces):
    if not faces or not all(_is_plane(f) for f in faces):
        raise ValueError(FACES_MSG)
    n = _normal(faces[0])
    for f in faces[1:]:
        if _normal(f).dot(n) < 1 - _ANG:
            raise ValueError(FACES_MSG)
    return n


def gaps_to(faces, target):
    """One signed gap per face from its plane to `target` (faces in different planes each reach the target plane)."""
    n = check_faces(faces)
    if isinstance(target, (Part.Edge, Part.Vertex)):  # an edge or a point: all of it at one height above the faces
        pts = [target.Point] if isinstance(target, Part.Vertex) else target.discretize(24)
        heights = [p.dot(n) for p in pts]
        if max(heights) - min(heights) > _TOL:
            raise ValueError(PARALLEL_MSG)
        h = sum(heights) / len(heights)
        return [h - f.CenterOfMass.dot(n) for f in faces]
    if isinstance(target, Part.Face):
        if not _is_plane(target):
            raise ValueError(PARALLEL_MSG)
        point, normal = target.CenterOfMass, _normal(target)
    else:
        point, normal = target
    normal = App.Vector(normal)
    if normal.Length < _TOL or abs(abs(normal.normalize().dot(n)) - 1) > _ANG:
        raise ValueError(PARALLEL_MSG)
    return [(App.Vector(point) - f.CenterOfMass).dot(n) for f in faces]


def gap_to(faces, target):
    return gaps_to(faces, target)[0]


def _along(face, n):
    """A face the move runs along: a plane containing n, a cylinder whose axis is n, a curve extruded along n (the
    walls of an extruded ellipse or spline), or any other surface square to n everywhere (a B-spline wall). An offset
    of one of those (a wall moved Together before) runs the same way."""
    surface = face.Surface
    while surface.__class__.__name__ == "OffsetSurface":
        surface = surface.BasisSurface
    kind = surface.__class__.__name__
    if kind == "Plane":
        return abs(_normal(face).dot(n)) < _ANG
    if kind == "Cylinder":
        return abs(abs(App.Vector(surface.Axis).normalize().dot(n)) - 1) < _ANG
    if kind == "SurfaceOfExtrusion":
        return abs(abs(App.Vector(surface.Direction).normalize().dot(n)) - 1) < _ANG
    if kind not in ("BSplineSurface", "BezierSurface"):
        return False  # a cone, sphere or torus never runs along a straight move
    u0, u1, v0, v1 = face.ParameterRange
    steps = [k / 4.0 for k in range(5)]
    return all(abs(face.normalAt(u0 + (u1 - u0) * a, v0 + (v1 - v0) * b).dot(n)) < 1e-4 for a in steps for b in steps)


def _straight(edge):
    """A straight edge: a line, or a B-spline / Bezier as long as its chord (FreeCAD's refine — removeSplitter —
    makes one of the seam of a hole wall it merges)."""
    try:
        kind = edge.Curve.__class__.__name__
    except Exception:  # "undefined curve type" (a thread's helix)
        return False
    if kind == "Line":
        return True
    if kind not in ("BSplineCurve", "BezierCurve") or edge.isClosed():
        return False
    return abs(edge.Length - edge.Vertexes[0].Point.distanceToPoint(edge.Vertexes[-1].Point)) < _TOL


def _same(a, b):
    return a.isSame(b)


def _neighbour(shape, face, edge):
    for f in shape.ancestorsOfType(edge, Part.Face):
        if not _same(f, face):
            return f
    return None


def _corner_patches(shape, face):
    """The faces touching `face` at a corner vertex only, between its two neighbours there (OCC's corner patch where
    blends made in one feature meet: a sphere for fillets, a flat triangle for chamfers), each with those neighbours."""
    out = []
    for v in face.Vertexes:
        nbs = []
        for e in face.Edges:
            nb = _neighbour(shape, face, e) if any(x.isSame(v) for x in e.Vertexes) else None
            if nb is not None and not any(_same(nb, g) for g in nbs):
                nbs.append(nb)
        for g in shape.ancestorsOfType(v, Part.Face):
            if _same(g, face) or any(_same(g, f) for f in nbs) or any(_same(g, p) for p, _ in out):
                continue
            touching = [f for f in nbs if any(_same(e, x) for e in g.Edges for x in f.Edges)]
            if len(touching) >= 2:
                out.append((g, touching))
    return out


def _side_of(face, size, way):
    """A block on one side of a flat face's plane (way 1: the air side, -1: the material side), `size` beyond it
    every way."""
    n = _normal(face)
    sheet = Part.Plane(face.CenterOfMass, n).toShape(-size, size, -size, size)
    return sheet.extrude(n * (size * way))


def _side_dir(shape, face, vertex, n):
    """The line a vertex of `face` moves along (inventor), on both neighbours of the vertex (the faces across the two
    edges of `face` meeting there): their common edge, pointing at the vertex, with its length; else (a chamfered
    corner between them touches the face at the vertex only) the line the two neighbour planes meet in, up along n,
    with no length."""
    own = list(face.Edges)
    nbs = []
    for e in own:
        if any(v.isSame(vertex) for v in e.Vertexes):
            nb = _neighbour(shape, face, e)
            if nb is None:
                raise ValueError(ROUND_MSG)
            if not any(_same(nb, g) for g in nbs):
                nbs.append(nb)
    for e in shape.ancestorsOfType(vertex, Part.Edge):
        if any(_same(e, o) for o in own) or not all(any(_same(e, g) for g in nb.Edges) for nb in nbs):
            continue
        if not _straight(e):
            raise ValueError(ROUND_MSG)
        a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
        far = b if a.distanceToPoint(vertex.Point) < _TOL else a
        return (vertex.Point - far).normalize(), e.Length
    if len(nbs) == 2 and all(_is_plane(f) for f in nbs):
        u = _normal(nbs[0]).cross(_normal(nbs[1]))
        if u.Length > _ANG:
            u.normalize()
            return (u if u.dot(n) > 0 else u * -1), None
    raise ValueError(ROUND_MSG)


def _moved_edges(shape, face, wire, n, d):
    """Each edge of the wire with its moved copy in the new plane, every vertex along its side line, every edge on
    its neighbour (inventor): straight edges stay straight, an arc / circle on a cylinder along the move slides."""
    for e in wire.Edges:
        nb = _neighbour(shape, face, e)
        if nb is None or not (_is_plane(nb) or _along(nb, n)):
            raise ValueError(ROUND_MSG)
    moved = {}
    for v in wire.OrderedVertexes:
        u, length = _side_dir(shape, face, v, n)
        if abs(u.dot(n)) < _ANG:
            raise ValueError(TOO_LARGE_MSG)
        t = d / u.dot(n)
        # back along its side edge past that edge's far end: a neighbour vanishes (no side edge: the checks on the
        # result catch a cut that leaves the material)
        if t < 0 and length is not None and -t >= length - _TOL:
            raise ValueError(TOO_LARGE_MSG)
        moved[v.hashCode()] = v.Point + u * t
    pairs = []
    for e in wire.OrderedEdges:
        a, b = moved[e.Vertexes[0].hashCode()], moved[e.Vertexes[-1].hashCode()]
        if _straight(e):
            old = e.Vertexes[-1].Point - e.Vertexes[0].Point
            if (b - a).dot(old) <= _TOL:  # shrunk to nothing, or turned round: the outline would cross itself
                raise ValueError(TOO_LARGE_MSG)
            pairs.append((e, Part.LineSegment(a, b).toShape()))
        else:
            pairs.append((e, e.translated(n * d)))
    w = Part.Wire(Part.__sortEdges__([m for _, m in pairs]))
    if not w.isClosed():
        raise ValueError(TOO_LARGE_MSG)
    return pairs, w


def _side_face(e, moved, n, d):
    """The slab's wall between an edge and its moved copy: flat (in the neighbour's plane) for a straight edge, a
    piece of the cylinder along the move for an arc / circle. Exact faces: no B-spline edges for later moves."""
    if _straight(e):
        a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
        a2, b2 = moved.Vertexes[0].Point, moved.Vertexes[-1].Point
        return Part.Face(Part.makePolygon([a, b, b2, a2, a]))
    return e.extrude(n * d).Faces[0]


def _inventor_slab(shape, face, n, d):
    walls, new_wires = [], []
    try:
        for wire in [face.OuterWire] + [w for w in face.Wires if not w.isSame(face.OuterWire)]:
            pairs, w = _moved_edges(shape, face, wire, n, d)
            new_wires.append(w)
            walls += [_side_face(e, m, n, d) for e, m in pairs]
        outline = Part.Face(new_wires[0])
        if not outline.isValid() or outline.Area < _TOL:
            raise ValueError(TOO_LARGE_MSG)
        new_face = Part.Face(new_wires, "Part::FaceMakerBullseye")  # the moved outline with its holes
        shell = Part.Shell([face.copy(), new_face] + walls)
        shell.sewShape()
        slab = Part.Solid(shell)
    except Part.OCCError:
        raise ValueError(TOO_LARGE_MSG)
    if slab.Volume < 0:
        slab.reverse()
    if not slab.isValid() or not slab.Shells[0].isClosed() or slab.Volume < _TOL:
        raise ValueError(TOO_LARGE_MSG)
    if d < 0:  # a corner patch (touching the face at a vertex only) cuts the corner off: the slab stops at its plane
        # there only — on a non-convex face (an L, a U) the plane runs on through the slab far from that corner, so
        # each clip is the cone at the corner vertex on the patch's air side, inside both neighbours there (the
        # slab's own walls): its three edges run down from the vertex, it meets the slab in that corner only
        size = shape.BoundBox.DiagonalLength + abs(d)
        for patch, nbs in _corner_patches(shape, face):
            if not _is_plane(patch) or not all(_is_plane(f) for f in nbs):
                raise ValueError(ROUND_MSG)
            cone = _side_of(patch, size, 1)
            for f in nbs:
                cone = cone.common(_side_of(f, size, -1))
            slab = slab.cut(cone)
        if len(slab.Solids) != 1:
            raise ValueError(TOO_LARGE_MSG)
        slab = slab.Solids[0]
    return slab


def _together_slab(shape, face, n, d):
    moved = [face] + [nb for nb in (_neighbour(shape, face, e) for e in face.Edges)
                      if nb is not None and not _along(nb, n)]
    group = []
    for f in moved:
        if not any(_same(f, g) for g in group):
            group.append(f)
    # the corner patches between two moved blends (a sphere, a triangle where they meet) move with them
    group += [p for p, nbs in _corner_patches(shape, face)
              if not _along(p, n) and all(any(_same(f, g) for g in group) for f in nbs)]
    count = {}
    for f in group:
        for e in f.Edges:
            key = e.hashCode()
            count[key] = (count.get(key, (0, e))[0] + 1, e)
    rim = [e for c, e in count.values() if c == 1 and not e.Degenerated]  # a sphere's pole is no edge
    for e in rim:
        wall = next((f for f in shape.ancestorsOfType(e, Part.Face) if not any(_same(f, g) for g in group)), None)
        if wall is not None and not _along(wall, n):
            raise ValueError(WALLS_MSG)
    caps = [f.copy() for f in group] + [f.translated(n * d) for f in group]
    strips = [e.extrude(n * d).Faces[0] for e in rim]
    shell = Part.Shell(caps + strips)
    shell.sewShape()
    return Part.Solid(shell)


_SLABS = {"step": lambda shape, f, n, d: f.extrude(n * d), "together": _together_slab, "inventor": _inventor_slab}


def _on_far_side(shape, face, n, d):
    """The moved face lies on the part's boundary facing the other way: the cut would open the part through there."""
    moved = face.translated(n * d)
    plane = moved.CenterOfMass
    for g in shape.Faces:
        if not _is_plane(g) or _normal(g).dot(n) > -1 + _ANG or abs((g.CenterOfMass - plane).dot(n)) > _TOL:
            continue
        if moved.common(g).Area > _TOL:
            return True
    return False


def apply(shape, faces, d, mode):
    """`d` one distance for every face, or a list of one per face (To: faces in different planes reach one plane)."""
    if mode not in _SLABS:
        raise ValueError("Unknown neighbours mode: %s" % mode)
    faces, blends = split_blends(shape, faces)
    if blends:
        mode = "together"  # a chamfer / fillet picked with its face goes with it
    ds = list(d) if isinstance(d, (list, tuple)) else [d] * len(faces)
    if len(ds) != len(faces):
        raise ValueError("One distance per face: %d faces, %d distances" % (len(faces), len(ds)))
    if all(abs(x) < _TOL for x in ds):
        return shape
    n = check_faces(faces)
    try:
        return _moved(shape, faces, ds, n, mode)
    except Part.OCCError:  # a slab or a boolean OCC can't make: a refusal like the others, never a raw OCC error
        raise ValueError(TOO_LARGE_MSG)


def combine(shape, adds, cuts):
    """The moved part: the slabs fused on and cut away, the split faces merged; a broken or split part is refused."""
    result = shape
    if adds:
        result = result.fuse(adds)
    if cuts:
        result = result.cut(cuts)
    result = result.removeSplitter()
    if not result.isValid() or len(result.Solids) != len(shape.Solids):
        raise ValueError(TOO_LARGE_MSG)
    return result


def _moved(shape, faces, ds, n, mode):
    adds, cuts = [], []
    for f, x in zip(faces, ds):
        if abs(x) < _TOL:
            continue
        slab = _SLABS[mode](shape, f, n, x)
        if x < 0:  # the slab must lie inside the material, and not end on the part's far side
            if slab.cut(shape).Volume > _TOL or _on_far_side(shape, f, n, x):
                raise ValueError(TOO_LARGE_MSG)
            cuts.append(slab)
        else:
            adds.append(slab)
    return combine(shape, adds, cuts)
