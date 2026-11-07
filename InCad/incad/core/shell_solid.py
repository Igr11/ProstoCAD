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


def _sound(shape, base, inside, faulty_base=False):
    """faulty_base: the part was faulty already (a STEP's): a wall no worse than it is kept, its full check left out."""
    if shape is None or shape.isNull() or len(shape.Solids) != 1 or not shape.isValid():
        return False
    if not faulty_base:
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


_CHILD = """
import os, Part
a = os.environ["INCAD_OCC_TRIAL"].split("|")
s = Part.read(a[0])
d, inter, join = float(a[3]), a[4] == "1", int(a[5])
if a[2] == "thick":
    r = s.makeThickness([s.Faces[int(i)] for i in a[6].split(",") if i], d, %r, inter, False, 0, join)
else:
    r = s.makeOffsetShape(d, %r, inter, False, 0, join, a[2] == "fill")
r.exportBrep(a[1])
""" % (TOL, TOL)
CHILD_SECONDS = 60.0


def _in_child(solid, kind, d, inter, join, faces=()):
    """OCC's offset with intersection joins in a freecadcmd process of its own: on the user's air2 it brought the
    whole program down (BRepOffset_Inter2d::ConnexIntByInt, a segfault), and again on opening the file. A crash there
    is only a failed try. kind: 'thick' (faces removed) or 'offset'. The shape, or None."""
    import os
    import subprocess
    import tempfile
    from incad import host
    exe = host.freecadcmd(App.getHomePath())
    if exe is None:
        return None
    idx = []
    for f in faces:
        i = next((k for k, g in enumerate(solid.Faces) if g.isSame(f)), None)
        if i is None:
            return None
        idx.append(str(i))
    with tempfile.TemporaryDirectory() as tmp:
        src, out, script = (os.path.join(tmp, n) for n in ("in.brep", "out.brep", "occ.py"))
        solid.exportBrep(src)
        with open(script, "w") as fh:
            fh.write(_CHILD)
        env = dict(os.environ, INCAD_OCC_TRIAL="|".join(
            [src, out, kind, repr(d), "1" if inter else "0", str(join), ",".join(idx)]))
        try:
            subprocess.run([exe, "-u", os.path.join(tmp, "user.cfg"), script], env=env, timeout=CHILD_SECONDS,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=host.quiet_process_flags())
        except Exception:
            return None
        if not os.path.exists(out):
            return None
        try:
            return Part.read(out)
        except Exception:
            return None


def _offset_solid(shape, d, faulty=False):
    """The part offset by d (a solid), the first way OCC gives a valid one in: a block with fillets no larger than the
    wall offset inward with arc joins came back invalid, with intersection joins sound (its inner corners sharp)."""
    for inter, join in ((False, 2), (True, 2), (False, 0), (True, 0)):  # sharp first: arc joins round corners
        try:
            off = _in_child(shape, "offset", d, inter, join) if join == 2 or faulty else \
                shape.makeOffsetShape(d, TOL, inter, False, 0, join, False)
        except Exception:
            continue
        if off is None or off.isNull():
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


def _same_face(face, solid):
    """The face of `solid` that is `face` (of the solid before it was healed): same kind, same centre and area."""
    c, a = face.CenterOfMass, face.Area
    for g in solid.Faces:
        if g.Surface.TypeId == face.Surface.TypeId and (g.CenterOfMass - c).Length < 1e-3 \
                and abs(g.Area - a) < 1e-4 * max(a, 1.0):
            return g
    raise ValueError("a removed face was lost repairing the part's faulty shape: pick it again")


def _slab(face, t, faulty=False):
    """The wall under one face: a flat face pushed in by t, a round one offset by itself with the gap filled (on a
    faulty part in a process of its own)."""
    if face.Surface.TypeId == "Part::GeomPlane":
        slab = face.extrude(_normal(face) * -t)
        if not slab.isValid():  # a face with a loose edge in it (where a disc touched it along a line, the user's
            from incad.core import heal  # air2) or arcs written as splines (a STEP): its closed outlines, exact
            try:
                closed = [w for w in face.Wires if w.isClosed()]
                flat = Part.makeFace(closed, "Part::FaceMakerBullseye") if len(closed) > 1 else Part.Face(closed[0])
                exact = heal._face(flat)
                if exact.Surface.TypeId == "Part::GeomPlane" and _normal(exact).dot(_normal(face)) < 0:
                    exact.reverse()
                slab = exact.extrude(_normal(face) * -t)
            except Exception:
                pass
        return slab
    ring = _round_slab(face, t)
    if ring is not None:
        return ring
    off = _in_child(face, "fill", -t, False, 0) if faulty else face.makeOffsetShape(-t, 1e-4, False, False, 0, 0, True)
    if off is None or off.isNull():
        raise ValueError("no slab")
    return off.Solids[0] if off.Solids else Part.Solid(Part.Shell(off.Faces))


def _normal_at(face, p):
    u, v = face.Surface.parameter(p)
    return face.normalAt(u, v)


def _corner_blocks(base, keep, t):
    """At each inward-turning edge between two faces that stay, the square block of wall inside the corner that neither
    face's slab reaches (an L opened on top: the hollow ran out to the outer edge there, a wall of 0 mm, the
    finder). The block: the edge swept back along both faces' inward normals by t."""
    blocks = []
    kept = list(keep)
    for i, a in enumerate(kept):
        for b in kept[i + 1:]:
            if not a.BoundBox.intersect(b.BoundBox):
                continue
            for e in a.Edges:
                if not any(e.isSame(x) for x in b.Edges) or e.Length < 1e-9:
                    continue
                p = e.valueAt(0.5 * (e.FirstParameter + e.LastParameter))
                try:
                    na, nb = _normal_at(a, p), _normal_at(b, p)
                except Exception:
                    continue
                if abs(na.dot(nb)) > 1 - 1e-9:
                    continue  # smooth: no corner
                eps = 1e-3 * t
                if not (base.isInside(p + (nb - na) * eps, 1e-7, False) and
                        base.isInside(p + (na - nb) * eps, 1e-7, False)):
                    continue  # outward-turning: the slabs overlap there
                try:
                    blocks.append(e.extrude(na * -t).extrude(nb * -t))
                except Exception:
                    pass
    return [x for x in blocks if not x.isNull() and x.Volume > 1e-12]


def _round_slab(face, t):
    """The wall under a round face whose rims are two circles or arcs (a fillet, a boss, a turned wall), built exactly
    as the ring sector between its radius and its radius less (convex) or more (concave) the wall: OCC's offset of
    such a face came back as B-spline faces the part never had (the finder), and refused one no wider than the wall,
    whose slice then runs to the axis, the inner corner sharp. None for other faces (OCC offsets those)."""
    surf = face.Surface
    if surf.TypeId != "Part::GeomCylinder":
        return None
    arcs = [e for e in face.Edges if type(e.Curve).__name__ == "Circle"]
    if len(arcs) != 2:
        return None
    axis = App.Vector(surf.Axis)
    axis.normalize()
    arcs.sort(key=lambda e: e.Curve.Center.dot(axis))
    low, high = arcs
    centre, r = low.Curve.Center, surf.Radius
    on = low.valueAt(low.FirstParameter)  # a point on the face (a whole round face's centre of mass is on its axis)
    convex = (on - centre).dot(_normal_at(face, on)) > 0
    inner = r - t if convex else r + t
    height = axis * (high.Curve.Center - centre).dot(axis)
    if low.isClosed():  # a whole round wall: a ring (a disc when the wall reaches the axis)
        outer = Part.Face(Part.Wire(Part.makeCircle(max(r, inner), centre, axis)))
        if min(r, inner) > 1e-9:
            outer = outer.cut(Part.Face(Part.Wire(Part.makeCircle(min(r, inner), centre, axis))))
        return outer.extrude(height)
    a0, a1 = low.Vertexes[0].Point, low.Vertexes[-1].Point
    if inner <= 1e-9:  # no wider than the wall: the slice to the axis
        wire = Part.Wire([Part.makeLine(centre, a0), low.copy(), Part.makeLine(a1, centre)])
    else:
        def towards(p):
            d = p - centre
            d.normalize()
            return centre + d * inner
        b0, b1 = towards(a0), towards(a1)
        mid = low.valueAt(0.5 * (low.FirstParameter + low.LastParameter))
        back = Part.Arc(b1, towards(mid), b0).toShape()
        wire = Part.Wire([low.copy(), Part.makeLine(a1, b1), back, Part.makeLine(b0, a0)])
    return Part.Face(wire).extrude(height)


def by_slabs(base, faces, t, faulty=False):
    """The inside wall without offsetting the part as a whole: every face that stays is thickened inward on its own
    and the pieces, fused, are cut back to the part. OCC's offset of the whole part (ThickSolid, or its offset
    with intersection joins) brought the program down on the user's air2 (a disc on a block, its edge running a
    hair into the block's top); faces one at a time it does in a second. None when it can't be made."""
    try:
        keep = [f for f in base.Faces if not any(f.isSame(g) for g in faces)]
        slabs = [_slab(f, t, faulty) for f in keep if f.Area > 1e-9]
        if not slabs or not all(x.isValid() for x in slabs):
            return None
        slabs += _corner_blocks(base, keep, t)
        fused = slabs[0].multiFuse(slabs[1:])
        wall = base.common(fused)  # (the part first: the other way round gave nothing on the user's faulty air)
        if not wall.Solids:
            wall = fused.common(base)
        try:
            wall = wall.removeSplitter()
        except Exception:  # (on the user's faulty air: "Bnd_Box is void"; the Boolean's result is kept as it is)
            pass
        return _without_crumbs(wall)
    except Exception:
        return None


def by_offset(base, faces, t, inside=True, faulty=False):
    """Way 4 (see the module): the wall from the part and its offset copy; None when it can't be made."""
    try:
        normals = [_normal(f) for f in faces]
        if inside:
            inner = _offset_solid(base, -t, faulty)
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
        outer = _offset_solid(base, t, faulty)
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
    if len(solid.Faces) <= MANY_FACES:  # a STEP's faulty arcs made exact first (the user's air2: Shell refused)
        from incad.core import heal
        sound = heal.healed(solid)
        if sound is not solid:
            faces = [_same_face(f, sound) for f in faces]
            solid = sound
    faulty = False
    if len(solid.Faces) <= MANY_FACES:  # OCC's offset crashed the whole program on a faulty shape (an imported STEP
        try:                            # whose round face crossed itself): there every offset runs in a process of
            solid.check(True)           # its own, where a crash is only a failed try (the user: "it has to work")
        except Exception:
            faulty = True
    if not faces and len(solid.Faces) > MANY_FACES:  # the closed hollow offsets the whole part: minutes on an STL's
        raise ValueError("Pick the faces to open (a closed hollow of a part with %d faces would take minutes)"
                         % len(solid.Faces))
    if True:  # (with no face removed only the walls face by face: OCC's ThickSolid needs a face to remove)
        # sharp first: the walls face by face and OCC's intersection joins keep every corner as the part has it;
        # arc joins round corners the part never had (the user: "everything has to be sharp")
        for join, inter, how in (("slabs", None, "walls face by face"), (2, False, "intersection joins"),
                                 (0, False, "arc joins"), (0, True, "intersection")):
            if join == "slabs":  # before OCC's intersection joins, which crashed on the user's air2
                r = by_slabs(solid, faces, t, faulty) if inside else None
            elif not faces:
                continue
            else:
                try:
                    r = _in_child(solid, "thick", -t if inside else t, inter, join, faces) if join == 2 or faulty \
                        else solid.makeThickness(faces, -t if inside else t, TOL, inter, False, 0, join)
                except Exception:
                    continue
            if _sound(r, solid, inside, faulty):
                return r, how
    r = by_offset(solid, faces, t, inside, faulty)
    if _sound(r, solid, inside, faulty):
        return r, "offset walls"
    if faces and r is not None and not r.isNull() and len(r.Solids) > 1:
        raise ValueError("these openings cut the wall into %d separate pieces: open fewer faces (a part with a hole "
                         "through it, opened top and bottom, leaves the hole's tube apart)" % len(r.Solids))
    raise ValueError("couldn't hollow this shape with %g mm walls: try a thinner wall, open other faces, or make "
                     "the Shell earlier in the tree, before fillets and chamfers" % t)
