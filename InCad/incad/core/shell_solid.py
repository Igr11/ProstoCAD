"""Shell on OpenCASCADE directly (the user: Shell matters most; several walls open at once; use OCC to the full).
FreeCAD's Thickness is OCC's ThickSolid with one setting; it failed on a block with rounded vertical edges with a
side opened, on chamfered walls, and it can't hollow a part with no face removed at all. Here the ways are tried in
turn and the first sound result is kept:

1. ThickSolid, arc joins (what FreeCAD's Thickness does);
2. ThickSolid, intersection joins;
3. ThickSolid, intersection mode;
4. the wall as the part minus its offset copy (inside: the part less its inner offset; outside: the outer offset
   less the part), each removed face opened by the offset face a wall's thickness away pushed through the wall,
   and between two neighbouring openings the corner bar along their edge cut away too. With no face removed this
   is the closed hollow (Inventor's shell with no opening).

Sound: one solid, valid, its full check clean, and its volume on the right side of the part's. On 74 test cases
(blocks plain, rounded, chamfered, with a boss, a hole, an L, a cylinder, a turned bottle; one face, two opposite,
two neighbouring, top and bottom, none) ways 1-3 made 52, way 4 made 72, equal in volume to OCC's where both work."""
import FreeCAD as App
import Part

TOL = 1e-3  # mm: the offset's tolerance (as the Boolean's fuzzy one)
MANY_FACES = 3000  # a part with more (an STL's triangles): no closed hollow, it would offset every face


def _sound(shape, base, inside):
    if shape is None or shape.isNull() or len(shape.Solids) != 1 or not shape.isValid():
        return False
    try:
        shape.check(True)
    except Exception:
        return False
    vol, whole = shape.Volume, base.Volume
    if abs(vol - whole) <= 1e-4 * whole:  # OCC's ThickSolid with intersection joins handed back the part itself
        return False                      # (a block filleted all round): a wall, not the part, is the result
    return 0 < vol < whole if inside else vol > 1e-9


def _normal(face):
    u, v = face.Surface.parameter(face.CenterOfMass)
    return face.normalAt(u, v)


def _offset_solid(shape, d):
    """The part offset by d (a solid), the first way OCC gives a valid one in: a block with fillets no larger than the
    wall offset inward with arc joins came back invalid, with intersection joins sound (its inner corners sharp)."""
    for inter, join in ((False, 0), (False, 2), (True, 0), (True, 2)):
        try:
            off = shape.makeOffsetShape(d, TOL, inter, False, 0, join, False)
        except Exception:
            continue
        if off.isNull():
            continue
        try:
            solid = off.Solids[0] if off.Solids else (Part.Solid(off.Shells[0]) if off.Shells else None)
        except Exception:
            solid = None
        if solid is None or not solid.isValid():
            continue
        vol = abs(solid.Volume)
        if (d < 0 and 0 < vol < shape.Volume) or (d > 0 and vol > shape.Volume):
            return solid
    return None


def _match(face, normal, faces, d):
    """The face of `faces` parallel to `face` (same kind of surface, same normal) lying `d` along its normal."""
    c = face.CenterOfMass
    for g in faces:
        if g.Surface.TypeId != face.Surface.TypeId:
            continue
        try:
            if _normal(g).dot(normal) < 1 - 1e-6:
                continue
        except Exception:
            continue
        if abs((g.CenterOfMass - c).dot(normal) - d) < 1e-4 * max(1.0, abs(d)):
            return g
    return None


def _without_crumbs(shape):
    """The largest solid alone when every other piece is a crumb (under 0.1 % of it): fillets smaller than the wall
    left a 2 mm3 crumb in a corner of a 3500 mm3 shell."""
    solids = list(shape.Solids)
    if len(solids) < 2:
        return shape
    solids.sort(key=lambda s: -s.Volume)
    if all(s.Volume < 1e-3 * solids[0].Volume for s in solids[1:]):
        return solids[0]
    return shape


def by_offset(base, faces, t, inside=True):
    """Way 4 (see the module): the wall from the part and its offset copy; None when it can't be made."""
    try:
        normals = [_normal(f) for f in faces]
        if inside:
            inner = _offset_solid(base, -t)
            if inner is None:
                return None
            prisms, cutters, inner_faces = [], [inner], []
            for f, n in zip(faces, normals):
                g = _match(f, n, inner.Faces, -t)
                if g is None:
                    return None
                inner_faces.append(g)
                # started a little inside the inner solid: a prism only touching it face to face made the
                # kernel's fuse come back empty (a block filleted all round, fillets no larger than the wall)
                prisms.append(g.translated(n * (-0.01 * t)).extrude(n * (t * 1.51)))
                cutters.append(prisms[-1])
            for i in range(len(faces)):  # two neighbouring openings: the corner bar along their edge goes too
                for j in range(i + 1, len(faces)):
                    if any(e.isSame(x) for e in faces[i].Edges for x in faces[j].Edges):
                        shared = [e for e in inner_faces[i].Edges for x in inner_faces[j].Edges if e.isSame(x)]
                        if shared:  # the inner edge pushed out along both openings: exactly the corner bar (shifted
                            # prisms missed it when the inner face was narrower than 1.5 walls, the finder)
                            strip = shared[0].extrude(normals[i] * (t * 1.6))
                            corner = strip.extrude(normals[j] * (t * 1.6))
                        else:
                            corner = prisms[i].translated(normals[j] * (t * 1.5)).common(
                                prisms[j].translated(normals[i] * (t * 1.5)))
                        if corner.Volume > 1e-9:
                            cutters.append(corner)
            result = base
            for c in cutters:  # one at a time (no fuse of the cutters)
                result = result.cut(c)
            return _without_crumbs(result.removeSplitter())
        outer = _offset_solid(base, t)
        if outer is None:
            return None
        wall = outer.cut(base)
        for f, n in zip(faces, normals):  # the cap over the removed face: from the offset face down to the face
            g = _match(f, n, outer.Faces, t)
            if g is None:
                return None
            wall = wall.cut(g.translated(n * 0.01).extrude(n * -(t + 0.01)))
        return wall.removeSplitter()
    except Exception:
        return None


def make(base, faces, t, inside=True):
    """(shell, how) of the solid `base` with `faces` (of it) removed and walls `t` mm thick; ValueError in plain
    words when no way works."""
    if base is None or base.isNull() or not base.Solids:
        raise ValueError("Nothing to hollow: the part has no solid here")
    if len(base.Solids) > 1:
        raise ValueError("the part's body holds %d separate solids (a box drawn away from the part stays its own "
                         "piece); Shell works on one connected solid. Join the piece to the part, or shell it in a "
                         "part file of its own" % len(base.Solids))
    if t <= 0:
        raise ValueError("Give the wall a thickness above zero")
    solid = base.Solids[0]
    if len(solid.Faces) <= MANY_FACES:  # OCC's offset crashed the whole program on a faulty shape (an imported STEP
        try:                            # whose round face crossed itself): such a part is said, never offset
            solid.check(True)
        except Exception:
            raise ValueError("the part's shape is faulty here (faces that cross themselves or edges off their faces "
                             "— often from an imported STEP file), and hollowing it could crash the program. Rebuild "
                             "or repair the shape first")
    if not faces and len(solid.Faces) > MANY_FACES:  # the closed hollow offsets the whole part: minutes on an STL's
        raise ValueError("Pick the faces to open (a closed hollow of a part with %d faces would take minutes)"
                         % len(solid.Faces))
    if faces:
        for join, inter, how in ((0, False, "arc joins"), (2, False, "intersection joins"),
                                 (0, True, "intersection")):
            try:
                r = solid.makeThickness(faces, -t if inside else t, TOL, inter, False, 0, join)
            except Exception:
                continue
            if _sound(r, solid, inside):
                return r, how
    r = by_offset(solid, faces, t, inside)
    if _sound(r, solid, inside):
        return r, "offset walls"
    if faces and r is not None and not r.isNull() and len(r.Solids) > 1:
        raise ValueError("these openings cut the wall into %d separate pieces: open fewer faces (a part with a hole "
                         "through it, opened top and bottom, leaves the hole's tube apart)" % len(r.Solids))
    raise ValueError("couldn't hollow this shape with %g mm walls: try a thinner wall, open other faces, or make "
                     "the Shell earlier in the tree, before fillets and chamfers" % t)
