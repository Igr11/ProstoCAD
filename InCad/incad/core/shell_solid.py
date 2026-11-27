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


def _stop():
    """Esc stops the Shell between its steps (the user: Esc must cancel it; the wall of an imported gear took 20
    minutes). safe.Stopped goes through every `except Exception` here (_stopped)."""
    from incad.core import safe
    safe.stop_if_esc("the Shell")


def _stopped(exc):
    from incad.core import safe
    return isinstance(exc, safe.Stopped)


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
    import time
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
            proc = subprocess.Popen([exe, "-u", os.path.join(tmp, "user.cfg"), script], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=host.quiet_process_flags())
        except Exception:
            return None
        end = time.monotonic() + CHILD_SECONDS
        try:
            while True:  # waited for in short steps: Esc closes the process (wait returns as soon as it ends)
                try:
                    proc.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if time.monotonic() > end:
                    raise subprocess.TimeoutExpired(exe, CHILD_SECONDS)
                _stop()
        except BaseException as e:
            proc.kill()
            proc.wait()
            if _stopped(e) or not isinstance(e, Exception):
                raise
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
        _stop()
        try:
            off = _in_child(shape, "offset", d, inter, join) if join == 2 or faulty else \
                shape.makeOffsetShape(d, TOL, inter, False, 0, join, False)
        except Exception as e:
            if _stopped(e):
                raise
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
        slabs = []
        for f in keep:
            if f.Area > 1e-9:
                _stop()
                slabs.append(_slab(f, t, faulty))
        if not slabs or not all(x.isValid() for x in slabs):
            return None
        _stop()
        slabs += _corner_blocks(base, keep, t)
        _stop()
        fused = slabs[0].multiFuse(slabs[1:])
        _stop()
        wall = base.common(fused)  # (the part first: the other way round gave nothing on the user's faulty air)
        if not wall.Solids:
            wall = fused.common(base)
        try:
            wall = wall.removeSplitter()
        except Exception:  # (on the user's faulty air: "Bnd_Box is void"; the Boolean's result is kept as it is)
            pass
        return _without_crumbs(wall)
    except Exception as e:
        if _stopped(e):
            raise
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
                _stop()
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
    except Exception as e:
        if _stopped(e):
            raise
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
        sound, faulty = heal.checked(solid)  # the full check once (the user: 15 s each on an imported gear, twice)
        if sound is not solid:
            faces = [_same_face(f, sound) for f in faces]
            solid = sound
        # faulty: OCC's offset crashed the whole program on a faulty shape (an imported STEP whose round face crossed
        # itself): there every offset runs in a process of its own, where a crash is only a failed try (the user:
        # "it has to work")
    else:
        faulty = False
    if not faces and len(solid.Faces) > MANY_FACES:  # the closed hollow offsets the whole part: minutes on an STL's
        raise ValueError("Pick the faces to open (a closed hollow of a part with %d faces would take minutes)"
                         % len(solid.Faces))
    if True:  # (with no face removed only the walls face by face: OCC's ThickSolid needs a face to remove)
        # sharp first: the walls face by face and OCC's intersection joins keep every corner as the part has it;
        # arc joins round corners the part never had (the user: "everything has to be sharp")
        for join, inter, how in (("slabs", None, "walls face by face"), (2, False, "intersection joins"),
                                 (0, False, "arc joins"), (0, True, "intersection")):
            _stop()
            if join == "slabs":  # before OCC's intersection joins, which crashed on the user's air2
                r = by_slabs(solid, faces, t, faulty) if inside else None
            elif not faces:
                continue
            else:
                try:
                    r = _in_child(solid, "thick", -t if inside else t, inter, join, faces) if join == 2 or faulty \
                        else solid.makeThickness(faces, -t if inside else t, TOL, inter, False, 0, join)
                except Exception as e:
                    if _stopped(e):
                        raise
                    continue
            if _sound(r, solid, inside, faulty):
                return r, how
    _stop()
    r = by_offset(solid, faces, t, inside, faulty)
    if _sound(r, solid, inside, faulty):
        return r, "offset walls"
    if faces and r is not None and not r.isNull() and len(r.Solids) > 1:
        raise ValueError("these openings cut the wall into %d separate pieces: open fewer faces (a part with a hole "
                         "through it, opened top and bottom, leaves the hole's tube apart)" % len(r.Solids))
    raise ValueError("couldn't hollow this shape with %g mm walls: try a thinner wall, open other faces, or make "
                     "the Shell earlier in the tree, before fillets and chamfers" % t)


ON = 1e-4  # mm: a point this near a face lies on it (a picked face's part on the built part)


def _inner_point(face):
    """A point inside the face (its centre of mass may lie off it: a ring, an arc): the middle of its largest
    triangle."""
    pts, tris = face.tessellate(max(0.05, 0.01 * face.BoundBox.DiagonalLength))
    if not tris:
        return face.CenterOfMass
    def area(tri):
        a, b, c = (pts[i] for i in tri)
        return (b - a).cross(c - a).Length
    a, b, c = (pts[i] for i in max(tris, key=area))
    return (a + b + c) * (1.0 / 3)


def _lies_on(point, shape):
    try:
        return shape.distToShape(Part.Vertex(point))[0] < ON
    except Exception:
        return False


def make_built(base, imported, faces, t, inside=True, near=None):
    """(shell, how) as make(), on a part built in ProstoCAD on an imported one (the body's base: a STEP): only the
    part built outside the import is hollowed, the import stays solid as it came (the user: the Shell of a Loft on
    an imported gear offset all 270 faces of the gear and took 20 minutes; the Loft alone takes a second). Where the
    built part lies on the import it keeps its wall: the wall stands on the import and grows into one with it (the
    user's Loft touched the gear along a ring 0.4 mm wide; with no wall there the wall met the gear along a line and
    stayed apart). Outside, the wall goes round the built part and stops at the import. near: a box holding all
    the features made on the import (outside it the part is the import as it came): the import is taken away
    inside it only (the gear: 0.9 s, not 28 s). With nothing built outside the import, or the import not
    taken away cleanly, make() on the whole part (as before)."""
    if base is None or base.isNull() or not base.Solids or imported is None or imported.isNull() \
            or not imported.Solids or t <= 0 or len(base.Solids) > 1:
        return make(base, faces, t, inside)
    solid, imp = base.Solids[0], imported
    _stop()
    if BACKGROUND["on"]:
        return _in_background(solid, imp, near, faces, t, inside)
    key = _built_key(solid, imp, near)
    pieces = _BUILT["pieces"] if _BUILT["key"] == key else None
    if pieces is None:
        pieces = _built_pieces(solid, imp, near)
        _BUILT.update(key=key, pieces=pieces, shapes=(solid, imp))  # (held: their keys can't come back on others)
    if not pieces:
        return make(base, faces, t, inside)
    return _shell_pieces(pieces, imp, faces, t, inside)


_BUILT = {"key": None, "pieces": None, "shapes": None}  # the last part's built pieces: a new wall thickness doesn't take the import
# away again (the user: "a bit slow when the wall thickness changes"; 1.1 s of 3.9 on the gear)


def _box_key(near):
    return None if near is None else (near.XMin, near.YMin, near.ZMin, near.XMax, near.YMax, near.ZMax)


def _built_key(solid, imp, near):
    return solid.hashCode(), imp.hashCode(), _box_key(near)


def _built_pieces(solid, imp, near):
    """The solids of `solid` outside `imp` (see make_built), [] when there are none or the cut failed."""
    try:
        built = None
        if near is not None:
            box = Part.makeBox(near.XLength, near.YLength, near.ZLength,
                               App.Vector(near.XMin, near.YMin, near.ZMin))
            part_in, imp_in = solid.common(box), imp.common(box)
            # outside the box the part must be the import as it came: the volumes say so (within 1e-4: 0.03 mm3 off
            # on the user's gear, a STEP), else the whole cut
            if abs((solid.Volume - imp.Volume) - (part_in.Volume - imp_in.Volume)) <= 1e-4 * solid.Volume:
                built = part_in.cut(imp_in)
        if built is None:
            built = solid.cut(imp)
        return [s for s in built.Solids if s.Volume > 1e-6 * solid.Volume]
    except Exception:
        return []


def _shell_pieces(pieces, imp, faces, t, inside):
    """make_built() once the built pieces are there: each hollowed, the picked faces open, joined to the import."""
    # the picked faces on the pieces (a face the cut split: each of its parts)
    picked = []
    for f in faces:
        on = [g for p in pieces for g in p.Faces if g.Surface.TypeId == f.Surface.TypeId
              and f.BoundBox.intersect(g.BoundBox) and _lies_on(_inner_point(g), f)]
        if not on:
            raise ValueError("Pick a face of the part built in ProstoCAD: the imported shape stays solid")
        picked += on
    walls, hows = [], []
    for p in pieces:
        _stop()
        opened = [g for g in p.Faces if any(g.isSame(x) for x in picked)]
        r, how = make(p, opened, t, inside)
        walls.append(r if inside else r.cut(imp))
        hows.append(how)
    _stop()
    whole = imp.multiFuse(walls)  # (no Refine of the whole: 0.6 s of 2.2 on the user's gear; each wall is refined)
    return whole, "built part: " + ", ".join(sorted(set(hows)))



# ----- the Shell window's preview in a process of its own (the user: the window must not stand still while a new
# wall is computed, 2.5 s on an imported gear; FreeCAD's geometry holds Python, so no thread can do it) -----
BACKGROUND = {"on": False}  # set by the Shell window around its preview's recompute


class Pending(Exception):
    """The wall asked for is being computed in the background: the feature keeps what it shows."""


_JOB = {"job": None}
_READY = {"key": None, "value": None}  # the last job's answer: (shape, how), or the error's words

_JOB_CHILD = r"""
import json, os, sys
a = os.environ["INCAD_SHELL_JOB"]
sys.path.insert(0, os.environ["INCAD_SHELL_PATH"])
import FreeCAD as App
import Part
def put(name, text):
    with open(os.path.join(a, name + ".tmp"), "w") as fh:
        fh.write(text)
    os.rename(os.path.join(a, name + ".tmp"), os.path.join(a, name))
try:
    from incad.core import shell_solid as ss
    with open(os.path.join(a, "spec.json")) as fh:
        spec = json.load(fh)
    solid = Part.read(os.path.join(a, "solid.brep")).Solids[0]
    imp = Part.read(os.path.join(a, "imp.brep"))
    faces = [solid.Faces[i] for i in spec["faces"]]
    if os.path.exists(os.path.join(a, "pieces.brep")):
        pieces = list(Part.read(os.path.join(a, "pieces.brep")).Solids)
    else:
        near = App.BoundBox(*spec["near"]) if spec["near"] else None
        pieces = ss._built_pieces(solid, imp, near)
        if pieces:
            Part.makeCompound(pieces).exportBrep(os.path.join(a, "pieces_out.brep"))
    if pieces:
        shape, how = ss._shell_pieces(pieces, imp, faces, spec["t"], spec["inside"])
    else:
        shape, how = ss.make(solid, faces, spec["t"], spec["inside"])
    shape.exportBrep(os.path.join(a, "out.brep"))
    put("how.txt", how)
except BaseException as e:
    put("error.txt", str(e) if isinstance(e, ValueError) else "Shell: %s" % e)
"""


class _Job:
    """One wall computed by a freecadcmd of its own; its files in a folder of its own."""

    def __init__(self, key, solid, imp, near, idx, t, inside):
        import json
        import os
        import subprocess
        import tempfile
        from incad import host
        self.key, self.shapes, self.near = key, (solid, imp), near
        self.dir = tempfile.mkdtemp(prefix="incad-shell-")
        solid.exportBrep(os.path.join(self.dir, "solid.brep"))
        imp.exportBrep(os.path.join(self.dir, "imp.brep"))
        if _BUILT["key"] == _built_key(solid, imp, near) and _BUILT["pieces"]:
            Part.makeCompound(_BUILT["pieces"]).exportBrep(os.path.join(self.dir, "pieces.brep"))
        with open(os.path.join(self.dir, "spec.json"), "w") as fh:
            json.dump({"faces": list(idx), "t": t, "inside": inside, "near": list(_box_key(near) or ()) or None}, fh)
        with open(os.path.join(self.dir, "job.py"), "w") as fh:
            fh.write(_JOB_CHILD)
        exe = host.freecadcmd(App.getHomePath())
        if exe is None:
            raise RuntimeError("no freecadcmd")
        here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # (the folder of incad)
        env = dict(os.environ, INCAD_SHELL_JOB=self.dir, INCAD_SHELL_PATH=here)
        self.proc = subprocess.Popen([exe, "-u", os.path.join(self.dir, "user.cfg"),
                                      os.path.join(self.dir, "job.py")], env=env, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, creationflags=host.quiet_process_flags())

    def done(self):
        return self.proc.poll() is not None

    def answer(self):
        """(shape, how) or the error's words; the built pieces kept for the next job."""
        import os
        f = lambda n: os.path.join(self.dir, n)
        if os.path.exists(f("pieces_out.brep")):
            try:
                _BUILT.update(key=_built_key(*self.shapes, self.near),
                              pieces=list(Part.read(f("pieces_out.brep")).Solids), shapes=self.shapes)
            except Exception:
                pass
        try:
            if os.path.exists(f("how.txt")):
                with open(f("how.txt")) as fh:
                    return Part.read(f("out.brep")), fh.read()
            if os.path.exists(f("error.txt")):
                with open(f("error.txt")) as fh:
                    return fh.read() or "Shell: the wall could not be made"
        except Exception as e:
            return "Shell: %s" % e
        return "Shell: the computation stopped without an answer"

    def kill(self):
        try:
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait()
        except Exception:
            pass
        self.clean()

    def clean(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)


def _in_background(solid, imp, near, faces, t, inside):
    """make_built() for the Shell window: the answer when this wall was computed already, else Pending (a job for it
    is started; one for other values is stopped: the last value typed wins)."""
    idx = []
    for f in faces:
        i = next((k for k, g in enumerate(solid.Faces) if g.isSame(f)), None)
        if i is None:  # (not a face of the part: computed here, as without the window)
            BACKGROUND["on"] = False
            try:
                return make_built(solid, imp, faces, t, inside, near)
            finally:
                BACKGROUND["on"] = True
        idx.append(i)
    key = _built_key(solid, imp, near) + (tuple(idx), float(t), bool(inside))
    if _READY["key"] == key:
        value = _READY["value"]
        if isinstance(value, str):
            raise ValueError(value)
        return value
    job = _JOB["job"]
    if job is not None and job.key == key:
        raise Pending()
    stop()
    _JOB["job"] = _Job(key, solid, imp, near, idx, t, inside)
    raise Pending()


def running():
    """A wall is being computed in the background."""
    return _JOB["job"] is not None


def poll():
    """True once the background job ended (its answer kept for the next recompute), False while it runs, None with
    no job."""
    job = _JOB["job"]
    if job is None:
        return None
    if not job.done():
        return False
    _JOB["job"] = None
    try:
        _READY.update(key=job.key, value=job.answer())
    finally:
        job.clean()
    return True


def stop():
    """The background job, if any, killed (Esc, a new value, Cancel)."""
    job, _JOB["job"] = _JOB["job"], None
    if job is not None:
        job.kill()
