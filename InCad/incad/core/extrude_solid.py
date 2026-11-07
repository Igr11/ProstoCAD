"""Extrude on OpenCASCADE directly (the user's law: ProstoCAD's building operations are its own, not FreeCAD's Pad and
Pocket with workarounds on top; "the extrude just needs to work, period").

The tool is the profile's faces swept along the direction (OCC's prism; with a taper a ruled loft from the outline to
its 2D offset at the far end, sharp corners kept), cut back where its extent ends on a face. Then with the part:

- Join on a face (the profile on a flat face of the part, going out of it: the common case) makes no Boolean. The
  face is cut by the profile in its own plane, and the part's other faces, those pieces, the tool's walls and its far
  cap are sewn into one solid. OCC's fuse of a disc inscribed in a block's face (touching the face's top and bottom
  edges, the user's air2) lost half the volume in every mode, fuzzy too; sewn, it is exact.
- Cut from a face into the part the same way: the face less the profile, the tool's walls and floor.
- Otherwise OCC's Boolean (fuse / cut / common), then again with a fuzzy value.
- A tool running either way from the profile (Symmetric, Two sides) is cut at the profile's plane when its other side
  changes nothing (a Join's side inside the part, a Cut's side in the air), and sewn the same way.

A result is kept when it is valid and its volume is right (part + tool - common for a join, part - common for a cut)
within VOL_REL, and sound to the full check (else glued, see _glued; on a faulty part the part is healed first);
otherwise the next way is tried, and none right is an error in plain words."""
import math

import FreeCAD as App
import Part

LIN = 1e-6  # mm: a point on a plane, a face in the profile's plane
VOL_REL = 1e-6  # a result's volume within this part of the volumes it is made of
FUZZY = (1e-6, 1e-5, 1e-4)  # mm: the Boolean's fuzzy values, tried in turn after the plain one
SEW = (1e-7, 1e-6, 1e-5)  # mm: the sewing tolerances
MANY_FACES = 3000  # a part with more (an STL's triangles): the Boolean first, no full validity check


# ----- the profile -----
def profile_faces(shape):
    """The faces of a sketch's closed outlines, an outline inside another a hole (as FreeCAD's Pad makes them)."""
    wires = [w for w in shape.Wires if w.isClosed()]
    if not wires:
        raise ValueError("the profile has no closed outline")
    try:
        made = Part.makeFace(wires, "Part::FaceMakerBullseye")
    except Exception:
        raise ValueError("no face can be made of the profile's outlines (lines crossing or ending mid-way)")
    faces = [f for f in made.Faces if f.Area > 1e-12]
    if not faces or not all(f.isValid() for f in faces):
        raise ValueError("no face can be made of the profile's outlines (lines crossing or ending mid-way)")
    return faces


def _span(shape, origin, d):
    """(nearest, farthest): how far `shape` reaches along the unit vector d from the plane through origin."""
    m = App.Placement(App.Vector(), App.Rotation(d, App.Vector(0, 0, 1))).toMatrix()
    m = m.multiply(App.Placement(-origin, App.Rotation()).toMatrix())
    s = _moved(shape, m)
    try:
        bb = s.optimalBoundingBox()
    except Exception:
        bb = s.BoundBox
    return bb.ZMin, bb.ZMax


def _moved(shape, matrix):
    s = shape.copy()
    s.transformShape(matrix, True)
    return s


def _in_plane(face, origin, d):
    """A flat face lying in the profile's plane."""
    try:
        if face.Surface.TypeId != "Part::GeomPlane":
            return False
        if abs(abs(face.Surface.Axis.dot(d)) - 1) > 1e-9:
            return False
        return abs((face.Surface.Position - origin).dot(d)) < LIN
    except Exception:
        return False


def _outward(face):
    u, v = face.Surface.parameter(face.CenterOfMass)
    return face.normalAt(u, v)


# ----- the tool -----
def _offset(wire, x):
    """The closed flat wire moved out by x (in when x < 0), corners kept sharp."""
    try:
        made = wire.makeOffset2D(x, 2, False, False, False)
    except Exception:
        made = None
    wires = made.Wires if made is not None and not made.isNull() else []
    if len(wires) != 1 or not wires[0].isClosed():
        raise ValueError("the taper closes the profile before the extrusion ends: make the taper or the extrusion "
                         "smaller")
    return wires[0]


def _loft(wire, x, v):
    """The ruled solid from `wire` to its offset by x moved by v."""
    far = _offset(wire, x)
    far.translate(v)
    made = Part.makeLoft([wire, far], True, True)
    solid = made.Solids[0] if made.Solids else Part.Solid(Part.Shell(made.Faces))
    if solid.Volume < 0:
        solid.reverse()
    return solid


def _draft(face, d, length, taper):
    """One face swept with a taper: positive grows the outline along the extrusion (FreeCAD's Pad and Pocket), its
    holes shrink by as much."""
    x = length * math.tan(math.radians(taper))
    v = d * length
    outer = _loft(face.OuterWire, x, v)
    holes = [_loft(w, -x, v) for w in face.Wires if not w.isSame(face.OuterWire)]
    solid = outer.cut(holes) if holes else outer
    solid = solid.Solids[0] if len(solid.Solids) == 1 else solid
    if not solid.isValid() or solid.Volume <= 0:
        raise ValueError("the taper makes no sound shape here: try a smaller taper")
    return solid


def prism(faces, d, length, taper=0.0):
    """The profile's faces swept `length` along the unit vector d."""
    if length <= LIN:
        raise ValueError("the extrusion has no length")
    if abs(taper) >= 89.9:
        raise ValueError("the taper must be less than 90°")
    solids = [f.extrude(d * length) if abs(taper) < 1e-9 else _draft(f, d, length, taper) for f in faces]
    return solids[0] if len(solids) == 1 else Part.Compound(solids)


def _extended(face, size, near):
    """The face's surface reaching `size` mm round the point nearest `near` (a flat face its plane there, a round
    one its whole cylinder), so an extent ends on it everywhere across the profile, as FreeCAD's To Face does."""
    s = face.Surface
    kind = s.TypeId
    try:
        if kind == "Part::GeomPlane":  # (a work plane's face is infinite: from its surface, never its bounds)
            u, v = s.parameter(near)
            return s.toShape(u - size, u + size, v - size, v + size)
        if kind == "Part::GeomCylinder":
            v = s.parameter(near)[1]
            return s.toShape(0, 2 * math.pi, v - size, v + size)
        if kind in ("Part::GeomCone", "Part::GeomSphere", "Part::GeomTorus", "Part::GeomSurfaceOfRevolution"):
            u1, u2, v1, v2 = face.ParameterRange
            return s.toShape(0, 2 * math.pi, v1, v2)
    except Exception:
        pass
    return face


def _target_face(target):
    """The face an extent ends on, from a picked face or a plane's shape."""
    faces = target.Faces if target is not None and not target.isNull() else []
    if not faces:
        raise ValueError("the To target has no face")
    return faces[0]


def _up_to(faces, origin, d, taper, target, reach):
    """The tool from the profile to the face `target` (its surface extended): a flat face square to the direction
    ends the prism at its distance; any other is sliced off a longer prism, the pieces at the profile kept."""
    face = _target_face(target)
    if face.Surface.TypeId == "Part::GeomPlane":
        m = face.Surface.Axis
        along = m.dot(d)
        if abs(along) < 1e-9:
            raise ValueError("the To face runs along the extrusion: it never ends there")
        if abs(abs(along) - 1) < 1e-9:
            length = (face.Surface.Position - origin).dot(d)
            if length <= LIN:
                raise ValueError("the To face lies behind the profile: flip the direction")
            return prism(faces, d, length, taper)
    centre = Part.Compound(faces).BoundBox.Center
    if face.Surface.TypeId == "Part::GeomPlane":
        size = 2 * (reach + abs((face.Surface.Position - centre).dot(face.Surface.Axis))) + 10.0
    else:
        lo, hi = _span(face, origin, d)
        size = reach + abs(hi) + abs(lo) + 10.0
    size += Part.Compound(faces).BoundBox.DiagonalLength
    surface = _extended(face, size, centre)
    fhi = _span(surface, origin, d)[1]
    if fhi <= LIN:
        raise ValueError("the To face lies behind the profile: flip the direction")
    long = prism(faces, d, fhi + 1.0, taper)
    import BOPTools.SplitAPI
    try:
        pieces = BOPTools.SplitAPI.slice(long, [surface], "Split").Solids
    except Exception:
        pieces = []
    keep = [p for p in pieces if any(_in_plane(g, origin, d) for g in p.Faces)]
    if not keep or len(keep) == len(pieces):
        raise ValueError("the To face doesn't cross the extrusion: pick a face it reaches")
    return keep[0] if len(keep) == 1 else Part.Compound(keep)


def ahead(target, origin, d):
    """True when the To face `target` reaches past the profile's plane along d (some of it lies ahead)."""
    face = _target_face(target)
    if face.Surface.TypeId == "Part::GeomPlane" and abs(abs(face.Surface.Axis.dot(d)) - 1) < 1e-9:
        return (face.Surface.Position - origin).dot(d) > LIN
    return _span(face, origin, d)[1] > LIN


def _next_face(base, faces, origin, d, last=False):
    """The part's face the extrusion meets first (To Next), or last (Through All of a Join), within the profile."""
    if base is None:
        raise ValueError("there is no part here for To Next / Through All to reach")
    lo, hi = _span(base, origin, d)
    if hi <= LIN:
        raise ValueError("the part lies behind the profile: flip the direction")
    probe = prism(faces, d, hi + 1.0)
    box = probe.BoundBox
    best, best_at = None, None
    for f in base.Faces:
        if not f.BoundBox.intersect(box):
            continue
        try:
            inside = f.common(probe)
        except Exception:
            continue
        if inside.isNull() or not inside.Faces or inside.Area < 1e-9:
            continue
        a, b = _span(inside, origin, d)
        at = b if last else a
        if at <= LIN:
            continue  # a face in the profile's plane, or one starting there
        if best is None or (at > best_at if last else at < best_at):
            best, best_at = f, at
    if best is None:
        raise ValueError("no face of the part lies ahead of the profile: flip the direction")
    return best


def side(faces, origin, d, kind, length=0.0, taper=0.0, base=None, target=None):
    """The tool on one side of the profile. kind: 'length', 'through' (past the whole part), 'first' (To Next),
    'last' (to the part's farthest face), 'face' (to `target`)."""
    if kind == "length":
        return prism(faces, d, length, taper)
    reach = base.BoundBox.DiagonalLength if base is not None else 0.0
    if kind == "through":
        if base is None:
            raise ValueError("there is no part here for Through All to go through")
        lo, hi = _span(base, origin, d)  # past the part either way: a cut into the air cuts nothing (and Auto,
        return prism(faces, d, max(hi, -lo, 0.0) + 1.0, taper)  # or a hand-chosen Cut, turns it round)
    if kind in ("first", "last"):
        return _up_to(faces, origin, d, taper, _next_face(base, faces, origin, d, kind == "last"), reach)
    if kind == "face":
        size = reach + sum(f.BoundBox.DiagonalLength for f in faces)
        return _up_to(faces, origin, d, taper, target, size)
    raise ValueError("unknown extent %r" % kind)


def tool(faces, origin, sides):
    """The whole tool: sides is [(d, kind, length, taper, base, target)], one or two (two run either way from the
    profile)."""
    if len(sides) == 2 and all(s[1] == "length" and abs(s[3]) < 1e-9 for s in sides):
        (d, _, a, _, _, _), (_, _, b, _, _, _) = sides  # one prism from one end to the other: no seam at the profile
        if a + b <= LIN:
            raise ValueError("the extrusion has no length")
        moved = [f.translated(-d * b) for f in faces]
        return prism(moved, d, a + b)
    made = [side(faces, origin, *s) for s in sides]
    if len(made) == 1:
        return made[0]
    joined = made[0].fuse(made[1])
    try:
        refined = joined.removeSplitter()
        if refined.isValid():
            joined = refined
    except Exception:
        pass
    return joined.Solids[0] if len(joined.Solids) == 1 else joined


# ----- the tool with the part -----
def _volume(shape):
    try:
        return sum(abs(s.Volume) for s in shape.Solids)
    except Exception:
        return None


def _common_volume(base, tool_shape):
    if not base.BoundBox.intersect(tool_shape.BoundBox):
        return 0.0
    try:
        return _volume(base.common(tool_shape))
    except Exception:
        return None


def _tidy(shape):
    if shape is None or shape.isNull():
        return None
    solids = [s for s in shape.Solids if abs(s.Volume) > 1e-12]
    if not solids:
        return None
    if len(solids) == len(shape.Solids):
        return shape  # as OCC made it: its one solid taken out came out "unorientable" to the full check
    return Part.Compound(solids)


def _right(shape, want, scale, full_check=True):
    if shape is None:
        return False
    if full_check and not shape.isValid():
        return False
    vol = _volume(shape)
    return vol is not None and vol > 0 and abs(vol - want) <= VOL_REL * max(scale, 1.0)


def _sew(faces):
    """One closed solid sewn of `faces`, or None."""
    for tol in SEW:
        try:
            c = Part.Compound([f.copy() for f in faces])
            c.sewShape(tol)
            shells = c.Shells
            if len(shells) != 1 or not shells[0].isClosed():
                continue
            solid = Part.Solid(shells[0])
            if solid.Volume < 0:
                solid.reverse()
            if solid.isValid():
                return solid
        except Exception:
            continue
    return None


def _half(shape, origin, d):
    """The part of `shape` on the side of the profile's plane the unit vector d points to, or None."""
    size = shape.BoundBox.DiagonalLength + abs((shape.BoundBox.Center - origin).dot(d)) + 10.0
    try:
        slab = Part.Plane(origin, d).toShape(-size, size, -size, size).extrude(d * size)
        made = shape.common(slab)
    except Exception:
        return None
    solids = [s for s in made.Solids if abs(s.Volume) > 1e-12]
    return solids[0] if len(solids) == 1 else (Part.Compound(solids) if solids else None)


def _one_side(base, tool_shape, origin, d, cut, common):
    """(tool, direction, common) for the sewn way: a tool running either way from the profile (Symmetric, Two
    sides) cut at the profile's plane, when the side left out changes nothing — a Join's side lying wholly in the
    part, a Cut's side wholly out in the air (the user's air2: a disc on a face, Join Symmetric 10, every Boolean
    came out wrong). The tool as it is when it lies on one side; None when neither side can be left out."""
    lo, hi = _span(tool_shape, origin, d)
    if lo > -LIN or hi < LIN:
        return tool_shape, d, common
    for dk in (d, -d):
        keep, drop = _half(tool_shape, origin, dk), _half(tool_shape, origin, -dk)
        vd = _volume(drop) if drop is not None else None
        cd = _common_volume(base, drop) if drop is not None else None
        if keep is None or vd is None or cd is None:
            continue
        if cut and cd <= VOL_REL * max(vd, 1.0):
            return keep, dk, common
        if not cut and abs(cd - vd) <= VOL_REL * max(vd, 1.0):
            return keep, dk, common - cd
    return None


def _on_face(base, tool_shape, faces, origin, d, cut, common):
    """Join / Cut sewn on a face (see the module's notes), or None when the profile doesn't lie on a flat face of
    the part with the tool wholly outside it (a Join) or wholly inside (a Cut)."""
    if common is None:
        return None
    one = _one_side(base, tool_shape, origin, d, cut, common)
    if one is None:
        return None
    tool_shape, d, common = one
    vt = _volume(tool_shape)
    if vt is None:
        return None
    if cut and abs(common - vt) > VOL_REL * max(vt, 1.0):
        return None  # the cut runs out of the part somewhere
    if not cut and common > VOL_REL * max(vt, 1.0):
        return None  # the join runs into the part somewhere
    on = []
    for g in base.Faces:
        if _in_plane(g, origin, d):
            try:
                out = _outward(g).dot(d)
            except Exception:
                continue
            if (out < 0) == cut:
                on.append(g)
    if not on:
        return None
    area = sum(f.Area for f in faces)
    prof = Part.Compound(faces)
    try:
        covered = sum(g.common(prof).Area for g in on)
        rest = [p for g in on for p in g.cut(prof).Faces]
    except Exception:
        return None
    if abs(covered - area) > 1e-6 * max(area, 1.0):
        return None  # the profile reaches past the face
    walls = [g for g in tool_shape.Faces if not _in_plane(g, origin, d)]
    others = [g for g in base.Faces if not any(g.isSame(o) for o in on)]
    ends = [g for g in walls if _span(g, origin, d)[0] > LIN]  # the far cap (a Cut's floor)
    if ends:
        try:
            if Part.Compound(ends).common(Part.Compound(others)).Area > 1e-9:
                return None  # it ends on another face of the part (To Next up to a bridge): the Boolean's case
        except Exception:
            return None
    return _sew(others + rest + walls)


def _faulty(shape):
    try:
        shape.check(True)
        return False
    except Exception:
        return True


def _glued(solid):
    """A faulty solid's faces split against one another (OCC's general fuse) and closed again, or None when that
    gives no sound solid of the same volume. Faces that only touch come to meet on a common edge (a disc inscribed in
    a face touches the face's edges at a point each: the full check called that faulty, and a Shell after it came
    out faulty too); a piece two faces share (a tool's wall lying on a wall of the part: a rectangle joined against a
    taller step, the user's air2 arch cut under its round top) is inside the result or cut away: both left out."""
    faces = solid.Faces
    for fuzzy in (0.0, 1e-7):
        try:
            images = faces[0].generalFuse(faces[1:], fuzzy)[1]
            uses = {}
            for pieces in images:
                for p in pieces:
                    uses.setdefault(p.hashCode(), [p, 0])[1] += 1
            keep = [p for p, n in uses.values() if n == 1]
            try:
                glued = Part.Solid(Part.Shell(keep))
                if glued.Volume < 0:
                    glued.reverse()
            except Exception:
                glued = _sew(keep)
        except Exception:
            continue
        if glued is not None and glued.isValid() and not _faulty(glued) \
                and abs(glued.Volume - solid.Volume) <= VOL_REL * max(solid.Volume, 1.0):
            return glued
    return None


def _finished(shape, refine, many, base_faulty):
    """(shape, sound): the result refined when asked, and sound to the full check where it can be: refined, then
    as it is, each glued (see _glued) when faulty. OCC's Refine merged the two halves of a round wall a disc cut
    inscribed in a face leaves, past the line where it touches the side, and that came out faulty. A big part (an
    STL's triangles) isn't looked at; on a faulty part (base_faulty() true) nothing more is tried: the part is
    healed first (see combine)."""
    refined = _refined(shape) if refine else shape
    if many or not _faulty(refined):
        return refined, True
    if base_faulty():
        return refined, False
    for s in ([refined, shape] if refined is not shape else [shape]):
        if s is not refined and not _faulty(s):
            return s, True
        glued = _glued(s)
        if glued is not None:
            return glued, True
    return refined, False


def _refined(shape):
    """Coplanar faces merged (FreeCAD's Refine), when that keeps it valid and its volume."""
    if len(shape.Solids) != 1:
        return shape  # two solids touching along a line (a disc resting on a block) came out faulty refined
    try:
        refined = shape.copy().removeSplitter()  # (a copy: OCC's unify changed the faces it shares with the part
        # before, and that one then came out "unorientable" to the full check)
        if refined.isValid() and abs(refined.Volume - shape.Volume) <= VOL_REL * max(shape.Volume, 1.0):
            return refined  # (as it is: its Solids[0] came out "unorientable" to the full check)
    except Exception:
        pass
    return shape


def combine(base, tool_shape, operation, faces=None, origin=None, d=None, refine=False):
    """(shape, how): the tool joined to the part ('join'), cut from it ('cut') or the common part of both
    ('intersect'), refined when asked. base None: the tool alone (a first Extrude). When no way gives a result sound
    to the full check and the part itself is faulty (a STEP's arcs written as splines that stray off their faces,
    the user's air2), the part is healed first (incad.core.heal) and it is made again."""
    try:
        shape, how, sound = _combine(base, tool_shape, operation, faces, origin, d, refine)
    except ValueError as e:
        shape, how, sound, error = None, None, False, e
    if not sound and base is not None and not base.isNull() and base.Solids and len(base.Faces) <= MANY_FACES:
        from incad.core import heal
        healed = heal.healed(base)
        if healed is not base:
            try:
                again = _combine(healed, tool_shape, operation, faces, origin, d, refine)
            except ValueError:
                again = None
            if again is not None and (again[2] or shape is None):
                return named(again[0], base, tool_shape, operation), again[1]
    if shape is None:
        raise error
    return named(shape, base, tool_shape, operation), how


# ----- the result's element names -----
# A sketch or a fillet on the result refers to its faces / edges by FreeCAD's element names. The ways above name
# them differently (OCC's Boolean after its history, the sewn way not at all), and a change of size can change the
# way: a sketch on a Cut's face lost its face ('?Face5') and the features on it were not computed again. So every
# result is named the same way: a face after the face of the part or of the tool it lies on, an edge after its
# faces, a vertex after its edges (several of one name told apart by where they are).
_OP = {"join": "FUS", "cut": "CUT", "intersect": "CMN"}


def _where(shape):
    c = shape.Point if shape.ShapeType == "Vertex" else shape.CenterOfMass
    return (round(c.x, 4), round(c.y, 4), round(c.z, 4))


def _point_in(face):
    """A point on the face, inside its outline."""
    u0, u1, v0, v1 = face.ParameterRange
    for fu, fv in ((.5, .5), (.3, .3), (.7, .7), (.3, .7), (.7, .3), (.1, .5), (.9, .5), (.5, .1), (.5, .9)):
        u, v = u0 + (u1 - u0) * fu, v0 + (v1 - v0) * fv
        try:
            if face.isPartOfDomain(u, v):
                return face.valueAt(u, v)
        except Exception:
            pass
    return face.CenterOfMass


def _same_surface(f, g):
    a, b = f.Surface, g.Surface
    if a.TypeId != b.TypeId:
        return False
    if a.TypeId == "Part::GeomPlane":
        return abs(abs(a.Axis.dot(b.Axis)) - 1) < 1e-9 and abs((a.Position - b.Position).dot(b.Axis)) < LIN
    return True


def _source(face, pools):
    """(pool letter, index) of the face of the part / tool `face` lies on, or None."""
    pt = _point_in(face)
    v = Part.Vertex(pt)
    for letter, pool in pools:
        for i, g in enumerate(pool):
            try:  # (exact: BoundBox follows the display's triangles once the part is drawn, inside a curve)
                bb = g.optimalBoundingBox(False, False)
            except Exception:
                bb = g.BoundBox
            bb.enlarge(1e-6)
            if not bb.isInside(pt) or not _same_surface(face, g):
                continue
            try:
                if g.distToShape(v)[0] < LIN:
                    return letter, i
            except Exception:
                pass
    return None


def _unique(groups, elements, kind, names):
    for stem, idx in groups.items():
        idx.sort(key=lambda i: _where(elements[i]))
        for k, i in enumerate(idx):
            names["%s;:%s%s" % (stem, kind, k if len(idx) > 1 else "")] = "%s%d" % (
                {"F": "Face", "E": "Edge", "V": "Vertex"}[kind], i + 1)


def named(shape, base, tool_shape, operation):
    """The result with its elements named after where they come from (see above); as it is on a big part (an STL's
    triangles: Boolean first there, and the time it would take) or when naming fails."""
    if base is None or base.isNull() or len(base.Faces) > MANY_FACES or shape is base:
        return shape
    try:
        pools, known = [], {}
        for letter, src in (("B", base), ("T", tool_shape)):
            faces = src.Faces
            pools.append((letter, faces))
            for i, g in enumerate(faces):
                known.setdefault(g.hashCode(), (letter, i))
        sources = {"B": base, "T": tool_shape}
        op = _OP.get(operation, "OP")
        faces, names, groups = shape.Faces, {}, {}
        for i, f in enumerate(faces):
            src = known.get(f.hashCode()) or _source(f, pools)
            if src is None:
                stem = "N"
            else:
                owner = sources[src[0]]
                mapped = owner.getElementMappedName("Face%d" % (src[1] + 1)) if owner.ElementMapSize else None
                mapped = mapped[0] if isinstance(mapped, (tuple, list)) else mapped
                stem = "%s;:%s" % (mapped or "Face%d" % (src[1] + 1), src[0])
            groups.setdefault("%s;%s" % (stem, op), []).append(i)
        _unique(groups, faces, "F", names)
        face_name = {v: k for k, v in names.items()}
        by_edge = {}
        for i, f in enumerate(faces):
            for e in f.Edges:
                by_edge.setdefault(e.hashCode(), set()).add(face_name["Face%d" % (i + 1)])
        edges, groups = shape.Edges, {}
        for i, e in enumerate(edges):
            groups.setdefault("(%s)" % "|".join(sorted(by_edge.get(e.hashCode(), ()))), []).append(i)
        edge_names = {}
        _unique(groups, edges, "E", edge_names)
        names.update(edge_names)
        edge_name = {v: k for k, v in edge_names.items()}
        by_vertex = {}
        for i, e in enumerate(edges):
            for v in e.Vertexes:
                by_vertex.setdefault(v.hashCode(), set()).add(edge_name["Edge%d" % (i + 1)])
        vertexes, groups = shape.Vertexes, {}
        for i, v in enumerate(vertexes):
            groups.setdefault("(%s)" % "|".join(sorted(by_vertex.get(v.hashCode(), ()))), []).append(i)
        _unique(groups, vertexes, "V", names)
        shape.ElementMap = names
    except Exception:
        pass
    return shape


def _combine(base, tool_shape, operation, faces, origin, d, refine):
    """(shape, how, sound): the first way whose result is valid with the right volume and sound to the full check
    (see _finished), else the first right one, not sound."""
    if base is None or base.isNull() or not base.Solids:
        if operation == "join":
            return tool_shape, "the tool alone", True
        raise ValueError("there is no part here to %s" % ("cut" if operation == "cut" else "intersect"))
    many = len(base.Faces) > MANY_FACES
    vb, vt = _volume(base), _volume(tool_shape)
    scale = max(vb or 0.0, vt or 0.0)
    if not base.BoundBox.intersect(tool_shape.BoundBox):
        if operation == "join":
            return Part.Compound(list(base.Solids) + list(tool_shape.Solids)), "apart", True
        if operation == "cut":
            return base, "apart", True
        raise ValueError("the extrusion doesn't overlap the part: Intersect leaves nothing")
    vc = _common_volume(base, tool_shape)
    want = None if vc is None else {"join": vb + vt - vc, "cut": vb - vc, "intersect": vc}[operation]
    if operation == "intersect" and want is not None and want <= VOL_REL * max(scale, 1.0):
        raise ValueError("the extrusion doesn't overlap the part: Intersect leaves nothing")
    if operation == "cut" and want is not None and want <= VOL_REL * max(scale, 1.0):
        raise ValueError("the cut takes away the whole part")
    boolean = {"join": base.fuse, "cut": base.cut, "intersect": base.common}[operation]
    tries = [("Boolean", lambda: boolean(tool_shape))] + \
        [("Boolean, fuzzy %g mm" % t, lambda t=t: boolean(tool_shape, t)) for t in FUZZY]
    if operation in ("join", "cut") and faces and origin is not None and d is not None:
        sewn = ("sewn on the face", lambda: _on_face(base, tool_shape, faces, origin, d, operation == "cut", vc))
        tries = tries[:1] + [sewn] + tries[1:] if many else [sewn] + tries
    found, first, known = [], None, []

    def base_faulty():
        if not known:
            known.append(_faulty(base))
        return known[0]
    for how, make in tries:
        try:
            shape = _tidy(make())
        except Exception:
            continue
        if shape is None:
            continue
        if want is None:  # the common part failed to compute: the first valid result in reason
            vol = _volume(shape)
            low, high = {"join": (max(vb, vt), vb + vt), "cut": (0.0, vb), "intersect": (0.0, min(vb, vt))}[operation]
            right = vol is not None and low * (1 - VOL_REL) <= vol <= high * (1 + VOL_REL) and (many or shape.isValid())
        else:
            right = _right(shape, want, scale, not many)
            if not right:
                found.append(how)
        if not right:
            continue
        shape, sound = _finished(shape, refine, many, base_faulty)
        if sound or base_faulty():
            return shape, how, sound  # (a faulty part: healed and made again, see combine)
        first = first or (shape, how, False)
    if first is not None:
        return first
    raise ValueError("OpenCASCADE could not %s it soundly (%s): move the profile a little, or change the extent"
                     % ({"join": "join", "cut": "cut", "intersect": "intersect"}[operation],
                        ", ".join(found) + " came out wrong" if found else "every way failed"))
