"""Sweep, Loft, Coil and Draft (Inventor) on native PartDesign features. Join/Cut is chosen when creating."""
from types import SimpleNamespace

import FreeCAD as App
import Part

from incad.features.common import Field, format_picks, is_line3d, parse_picks, resolve, value_param, value_text
from incad.core import offset2d

_OP = Field("operation", "Operation", "choice", (("join", "Join"), ("cut", "Cut")), "join")
_TYPES = {"sweep": ("PartDesign::AdditivePipe", "PartDesign::SubtractivePipe"),
          "loft": ("PartDesign::AdditiveLoft", "PartDesign::SubtractiveLoft"),
          "coil": ("PartDesign::AdditiveHelix", "PartDesign::SubtractiveHelix")}


def _sketches(doc, text):
    out = []
    for obj, sub in parse_picks(doc, text):
        obj = resolve(obj, sub)[0] if obj.TypeId != "Sketcher::SketchObject" and not is_line3d(obj) else obj
        if obj is not None and (obj.TypeId == "Sketcher::SketchObject" or is_line3d(obj)) and obj not in out:
            out.append(obj)
    return out


def _one(doc, text, what):
    found = _sketches(doc, text)
    if not found:
        raise ValueError("Pick the %s sketch" % what)
    return found[0]


def _new(body, key, values, name):
    tid = _TYPES[key][1 if values.get("operation") == "cut" else 0]
    f = body.newObject(tid, name)
    body.Tip = f
    return f


def _op(f, key):
    return "cut" if f.TypeId == _TYPES[key][1] else "join"


def _profile(f):
    p = f.Profile
    return p[0] if isinstance(p, (tuple, list)) and p else p


def _sketch_preselect(keys):
    def pre(pairs):
        found = []
        for obj, sub in pairs:
            if (obj.TypeId == "Sketcher::SketchObject" or is_line3d(obj)) and obj not in found:
                found.append(obj)
        if len(keys) == 1:  # one picks field takes them all in order
            return {keys[0]: format_picks([(o, "") for o in found])} if found else {}
        return {k: o.Name for k, o in zip(keys, found)}
    return pre


# ----- Sweep -----
HELPER = "InCadHelperOf"
WALL_PROP, ENDS_PROP = "InCadWall", "InCadWallEnds"
_ENDS = (("both_open", "Both open"), ("start_closed", "Start closed"), ("end_closed", "End closed"))
# (no "both closed": that is the solid sweep itself)


def _helpers(f):
    """Everything InCad made for this Sweep's wall (inner profile sketch, subtractive pipe, caps), body order."""
    body = f.getParentGeoFeatureGroup()
    return [o for o in (body.Group if body is not None else []) if getattr(o, HELPER, None) == f.Name]


def wall_of(f):
    """The Sweep's wall: the subtractive pipe InCad keeps under it, or None (a solid sweep)."""
    for o in _helpers(f):
        if o.TypeId == "PartDesign::SubtractivePipe":
            return o
    return None


def _mark(o, f):
    o.addProperty("App::PropertyString", HELPER, "InCad", "The Sweep this belongs to")
    setattr(o, HELPER, f.Name)


def _remove_wall(doc, body, f):
    helpers = _helpers(f)
    if body.Tip in helpers:
        body.Tip = f
    for o in reversed(helpers):
        body.removeObject(o)
        doc.removeObject(o.Name)


def _inner_geometry(prof, t):
    """Sketch geometry (sketch coordinates) of the profile's area shrunk by `t`: its outline inward and its holes
    outward (each wire offset on its own shrank a ring's hole too: the wall cut into the hole, a hole smaller than
    the wall was refused, and a ring thinner than two walls left a cut that emptied the part)."""
    from incad.core import regions
    sh = prof.Shape.copy()
    sh.transformShape(prof.Placement.inverse().toMatrix())
    closed = [w for w in sh.Wires if w.isClosed()]
    too_thin = ValueError("The wall (%g mm) is as thick as the profile, or thicker: make it thinner" % t)
    geos = []
    try:
        area = Part.makeFace(closed, "Part::FaceMakerBullseye") if closed else None
    except Exception as e:
        raise ValueError("The profile can't take a %g mm wall: %s" % (t, e))
    def offset(w, d):
        try:
            off = offset2d.offset2d(w, d, 2, False, False)  # (a 1 mm circle crashed OCC: by its radius)
        except Exception as e:
            if "null" in str(e).lower() or "larger than the circle" in str(e) or "no wires" in str(e):
                # (OCC's own words were shown: "makeOffset2D: offset result has no wires" for a slot or a square)
                raise too_thin
            raise ValueError("The profile can't take a %g mm wall: %s" % (t, e))
        return Part.makeFace(off.Wires, "Part::FaceMakerBullseye")

    for face in (area.Faces if area is not None else []):
        # the outline inward, each hole outward, the grown holes cut out of the shrunk outline (a face offset with
        # holes in it came back as the shrunk hole alone)
        inside = offset(face.OuterWire, -t)
        for hole in (w for w in face.Wires if not w.isSame(face.OuterWire)):
            inside = inside.cut(offset(hole, t))
        inside = [f for f in inside.Faces if f.Area > 1e-6]
        if not inside:  # a ring no thicker than two walls: nothing left between them
            raise too_thin
        for f in inside:
            for w in f.Wires:
                geos.extend(regions._geometry(e) for e in w.Edges)
    if not geos:
        raise ValueError("The wall is as thick as the profile: nothing would be left inside")
    return geos


def _spine_ends(path):
    """(start point, direction into the path there, end point, direction out of the path there)."""
    wire = path.Shape.Wires[0]
    p0, p1 = wire.OrderedVertexes[0].Point, wire.OrderedVertexes[-1].Point
    e0, e1 = wire.OrderedEdges[0], wire.OrderedEdges[-1]

    def direction(e, p, ahead):
        par = e.Curve.parameter(p)
        par = min(max(par, e.FirstParameter), e.LastParameter) if e.Curve.TypeId == "Part::GeomLine" else par
        d = e.tangentAt(par)
        towards_centre = (e.CenterOfMass - p).dot(d) > 0
        if towards_centre != ahead:
            d = -d
        return d.normalize()
    return p0, direction(e0, p0, True), p1, direction(e1, p1, False)


def _wall_sketch(doc, body, f, prof, geos, name, placement=None):
    sk = body.newObject("Sketcher::SketchObject", name)
    _mark(sk, f)
    if placement is None:
        # the whole attachment: a profile flipped on its plane (MapReversed) gave a copy turned over, the wall's
        # inner outline mirrored off the outer one (the user's uneven wall)
        for prop in ("AttachmentSupport", "MapMode", "MapReversed", "MapPathParameter", "AttachmentOffset"):
            if prop in prof.PropertiesList and prop in sk.PropertiesList:
                setattr(sk, prop, getattr(prof, prop))
    else:
        sk.MapMode = "Deactivated"
        sk.Placement = placement
    for g in geos:
        sk.addGeometry(g)
    return sk


def _cap(doc, body, f, sk, t, into):
    """A cap of thickness `t` padded from `sk` into the tube (`into`: unit direction from the cap's plane)."""
    pad = body.newObject("PartDesign::Pad", "SweepWallCap")
    _mark(pad, f)
    pad.Profile = sk
    pad.Type = "Length"
    pad.Length = t
    normal = sk.Placement.Rotation.multVec(App.Vector(0, 0, 1))
    pad.Reversed = normal.dot(into) < 0
    return pad


def _wall_apply(doc, body, f, values):
    """The Wall option: a second, subtractive sweep of the profile offset inward along the same path hollows
    the tube (and nothing else); a closed end is a cap padded from the inner profile at that end."""
    _remove_wall(doc, body, f)
    if not values.get("hollow"):
        for prop in (WALL_PROP, ENDS_PROP):
            if prop in f.PropertiesList:
                f.removeProperty(prop)
        return None
    if _op(f, "sweep") != "join":
        raise ValueError("A wall needs a Join sweep: a Cut sweep removes material, there is nothing to hollow")
    try:
        t = float(App.Units.Quantity(values.get("wall") or "1 mm").getValueAs("mm"))
    except Exception:
        raise ValueError("Wall thickness must be a length, such as 1 mm")
    if t <= 0:
        raise ValueError("Give the wall a thickness")
    ends = values.get("ends") or "both_open"
    prof, path = _profile(f), (f.Spine[0] if f.Spine else None)
    doc.recompute()
    if prof is None or path is None or path.Shape.isNull() or not path.Shape.Wires:
        raise ValueError("The wall needs the sweep's profile and path")
    geos = _inner_geometry(prof, t)
    old_tip = body.Tip
    body.Tip = f  # the wall goes right after its sweep
    inner = _wall_sketch(doc, body, f, prof, geos, "SweepWallProfile")
    pipe = body.newObject("PartDesign::SubtractivePipe", "SweepWall")
    _mark(pipe, f)
    pipe.Profile = inner
    pipe.Spine = (path, [])
    pipe.Transition = f.Transition
    doc.recompute()
    if "Invalid" in pipe.State:
        raise ValueError("The wall could not be made: %s" % pipe.getStatusString())
    last = pipe
    p0, t0, p1, t1 = _spine_ends(path)
    if ends == "start_closed":
        last = _cap(doc, body, f, inner, t, t0)
    elif ends == "end_closed":
        local_start = prof.Placement.inverse().multVec(p0)
        rot = App.Rotation(t0, t1).multiply(prof.Placement.Rotation)
        placement = App.Placement(p1 - rot.multVec(local_start), rot)
        end_sk = _wall_sketch(doc, body, f, prof, geos, "SweepWallEndProfile", placement)
        last = _cap(doc, body, f, end_sk, t, -t1)
    doc.recompute()
    for o in _helpers(f):
        if o.isDerivedFrom("PartDesign::Feature") and "Invalid" in o.State:
            raise ValueError("The wall could not be made: %s: %s" % (o.Label, o.getStatusString()))
    body.Tip = last if old_tip in (f, None) or getattr(old_tip, HELPER, None) == f.Name else old_tip
    if WALL_PROP not in f.PropertiesList:
        f.addProperty("App::PropertyLength", WALL_PROP, "InCad", "Wall thickness")
        f.addProperty("App::PropertyString", ENDS_PROP, "InCad", "Which ends stay open")
    setattr(f, WALL_PROP, t)
    setattr(f, ENDS_PROP, ends)
    return pipe


def _inside_out(doc, f, prof, path):
    """A Sweep the kernel built inside out (a negative volume: the profile drawn 40 mm from where the path
    starts was kept, with a warning about bends): refused in plain words."""
    try:  # (the tool alone came out +89.5 while the Sweep's result was -89.5: both are looked at)
        if not any(not sh.isNull() and sh.Solids and sh.Volume < 0 for sh in (f.Shape, f.AddSubShape)):
            return
    except Exception:
        return
    away = None
    try:
        import Part
        p0 = _spine_ends(path)[0]
        face = Part.makeFace(prof.Shape.Wires, "Part::FaceMakerBullseye")
        away = face.distToShape(Part.Vertex(p0))[0]
    except Exception:
        pass
    if away is not None and away > 1e-3:
        raise ValueError("The profile is %.3g mm away from the path's start, so the sweep comes out inside out: draw "
                         "the profile where the path starts (or start the path on the profile)" % away)
    raise ValueError("The sweep comes out inside out (the path doubles back through the profile): start the path "
                     "on the profile, square to it")


def _sweep_apply(doc, f, values):
    prof, path = _one(doc, values.get("profile"), "profile"), _one(doc, values.get("path"), "path")
    if prof is path:
        raise ValueError("The path must be another sketch than the profile")
    if path.TypeId == "Sketcher::SketchObject" and path.Shape.isNull():
        path.recompute()  # (a sketch just made, not computed yet: the pieces check below was skipped, and FreeCAD
                          # said only "Sweep: TopoDS::Wire")
    if path.TypeId == "Sketcher::SketchObject" and not path.Shape.isNull() and len(path.Shape.Wires) > 1:
        raise ValueError("The path %s is in %d separate pieces: a path is one chain of lines, arcs and splines "
                         "joined end to end (make the other pieces Construction, or join them)"
                         % (path.Label, len(path.Shape.Wires)))  # FreeCAD said only "TopoDS::Wire"
    f.Profile = prof
    f.Spine = (path, [])  # the whole sketch: segments added or redrawn later are followed (probed)
    f.Transition = "Round corner" if "Round corner" in f.getEnumerationsOfProperty("Transition") else f.Transition
    doc.recompute()
    _inside_out(doc, f, prof, path)
    _wall_apply(doc, f.getParentGeoFeatureGroup(), f, values)


def _sweep_create(doc, body, profile, values):
    _one(doc, values.get("profile"), "profile")
    _one(doc, values.get("path"), "path")
    f = _new(body, "sweep", values, "Sweep")
    _sweep_apply(doc, f, values)
    return f


def _sweep_read(doc, f):
    spine = f.Spine[0] if f.Spine else None
    out = {"profile": _profile(f).Name if _profile(f) else "", "path": spine.Name if spine else "",
           "operation": _op(f, "sweep"), "hollow": False, "wall": "1 mm", "ends": "both_open"}
    if wall_of(f) is not None:
        wall = getattr(f, WALL_PROP, None)
        out.update(hollow=True, wall="%g mm" % wall.Value if wall is not None else "1 mm",
                   ends=getattr(f, ENDS_PROP, "") or "both_open")
    return out


_JOIN = ("operation", ("join",))
_WALL = (("hollow", (True,)), _JOIN)
SWEEP = SimpleNamespace(
    __name__="incad.features.sweep", TITLE="Sweep", ICON="sweep", TYPES=_TYPES["sweep"], NEEDS_PROFILE=False,
    PROPS={}, STRUCTURAL=("join", "cut"), preselect=_sketch_preselect(["profile", "path"]),
    FIELDS=(Field("profile", "Profile", "pick", pick="sketch"), Field("path", "Path", "pick", pick="path"), _OP,
            Field("hollow", "Wall", "bool", default=False, show_if=_JOIN),
            Field("wall", "Thickness", "value_length", default="1 mm", show_if=_WALL),
            Field("ends", "Ends", "choice", _ENDS, "both_open", show_if=_WALL)),
    defaults=lambda doc, body, profile=None: {"profile": "", "path": "", "operation": "join", "hollow": False,
                                              "wall": "1 mm", "ends": "both_open"},
    create=_sweep_create, read=_sweep_read,
    update=lambda doc, f, values: (_sweep_apply(doc, f, values), f)[1], _pad_of=lambda f: f)


def _wall_drifted(f):
    """True when a hollow Sweep's inner outline is no longer its profile offset by the wall (the profile edited:
    a 0.5 mm wall became 1.5 mm, the finder)."""
    t = getattr(f, WALL_PROP, None)
    prof = _profile(f)
    inner = next((o for o in _helpers(f) if o.TypeId == "Sketcher::SketchObject" and o.Name.startswith("SweepWallProfile")),
                 None)
    if t is None or prof is None or inner is None:
        return False
    try:
        want = _inner_geometry(prof, t.Value)
    except Exception:
        return True  # the profile can't take the wall any more (shrunk below it): said by the rebuild, not hidden
    try:  # by shape, not geometry by geometry: a spline comes back with other poles (it never matched: a rebuild
        want_edges = [g.toShape() for g in want]  # after every recompute, for ever - the finder)
        have = inner.Shape.copy()
        have.transformShape(inner.Placement.inverse().toMatrix())
        length = sum(e.Length for e in want_edges)
        a, b = Part.Compound(want_edges).BoundBox, have.BoundBox
        same_box = all(abs(x - y) < 1e-5 for x, y in zip((a.XMin, a.YMin, a.XMax, a.YMax), (b.XMin, b.YMin, b.XMax, b.YMax)))
        return not (same_box and abs(length - have.Length) < 1e-5 * max(1.0, length))
    except Exception:
        return False


def follow_walls(doc):
    """After a recompute (incad.ui.shell): every hollow Sweep whose profile changed gets its wall again. Returns the
    labels rebuilt."""
    from incad.core import log, safe
    todo = [o for o in doc.Objects if o.TypeId == _TYPES["sweep"][0] and WALL_PROP in o.PropertiesList
            and _wall_drifted(o)]
    done = []
    for f in todo:
        label = f.Label
        r = safe.run_safely("%s follows its profile" % label,
                            lambda s=f: SWEEP.update(doc, s, _sweep_read(doc, s)), doc)
        if r.ok:
            done.append(label)
        elif _undo_the_change(doc, f):
            log.report("warning", "%s: the profile change was undone, the part is as it was" % label)
    return done


def _undo_the_change(doc, f):
    """A wall that can't follow its changed profile (shrunk below the wall): the old inner outline left in place cut
    the whole tube away (volume 0). The last undo step is taken back when that gives the wall its own profile again
    (the part exactly as before), else it is redone and nothing is changed."""
    if not doc.UndoCount:
        return False
    doc.undo()
    doc.recompute()
    try:
        back = _profile(f) is not None and not _wall_drifted(f)
    except Exception:  # (the Sweep itself gone with the step: not the profile change)
        back = False
    if not back:
        doc.redo()
        doc.recompute()
    return back


# ----- Loft -----
def _loft_apply(doc, f, values):
    secs = _sketches(doc, values.get("sections"))
    if len(secs) < 2:
        raise ValueError("Pick at least two section sketches, in order")
    from incad.core import loft_guard
    loft_guard.check(f.getParentGeoFeatureGroup(), secs, values.get("ruled"), values.get("closed"))  # before OCC can freeze
    f.Profile = secs[0]
    f.Sections = secs[1:]
    f.Ruled = bool(values.get("ruled"))
    f.Closed = bool(values.get("closed"))
    doc.recompute()
    shape = f.AddSubShape
    if not shape.isNull() and shape.isValid() and shape.Volume < 0:  # probed: out-of-order sections give a
        raise ValueError("The sections are out of order: pick them in order along the loft")  # negative volume
    if shape.isNull() or not shape.isValid() or shape.Volume <= 0:  # (every failure said "out of order")
        raise ValueError("No loft can be made through these sections (two in one plane, two points, a point "
                         "between sections, or outlines too different): change the sections")
    _in_order(secs, values.get("closed"))


def _in_order(secs, closed):
    """Sections picked out of order (heights 0, 20, 10): the loft folded back through itself and was kept, valid and
    with a positive volume. Measured along each section's own normal, every step goes the same way: a zig-zag or a
    curved loft (sections turning round an arc) is in order; one that comes back is not. A sketch's normal points
    whichever way it was drawn (on a downward face, or flipped): each normal is first turned to agree with the one
    before it, the first one towards the second section, so the direction is ONE for the whole loft (in-order
    sections with one sketch facing down were refused, and a real fold-back with one passed)."""
    if closed or len(secs) < 3:
        return
    try:
        centres = [s.Shape.BoundBox.Center for s in secs]
        normals = [s.getGlobalPlacement().Rotation.multVec(App.Vector(0, 0, 1)) for s in secs]
    except Exception:
        return
    along = None
    for k in range(len(secs) - 1):
        n = normals[k]
        if along is None:
            first = (centres[k + 1] - centres[k]).dot(n)
            if abs(first) <= 1e-6:
                continue
            along = n if first > 0 else -n
            continue
        along = n if n.dot(along) >= 0 else -n
        if (centres[k + 1] - centres[k]).dot(along) < -1e-6:
            raise ValueError("The sections are out of order: %s comes back towards %s - pick them in order along "
                             "the loft" % (secs[k + 1].Label, secs[k].Label))


def _loft_create(doc, body, profile, values):
    secs = _sketches(doc, values.get("sections"))
    if len(secs) < 2:
        raise ValueError("Pick at least two section sketches, in order")
    from incad.core import loft_guard
    loft_guard.check(body, secs, values.get("ruled"), values.get("closed"))  # nothing is made for a loft that would hang
    f = _new(body, "loft", values, "Loft")
    _loft_apply(doc, f, values)
    return f


def _loft_read(doc, f):
    secs = [_profile(f)] + [s if not isinstance(s, (tuple, list)) else s[0] for s in f.Sections]
    return {"sections": ",".join(s.Name for s in secs if s is not None), "ruled": bool(f.Ruled),
            "closed": bool(f.Closed), "operation": _op(f, "loft")}


LOFT = SimpleNamespace(
    __name__="incad.features.loft", TITLE="Loft", ICON="loft", TYPES=_TYPES["loft"], NEEDS_PROFILE=False,
    PROPS={}, STRUCTURAL=("join", "cut"), preselect=_sketch_preselect(["sections"]),
    FIELDS=(Field("sections", "Sections", "picks", pick="sketch", listed=True), Field("ruled", "Ruled", "bool", default=False),
            Field("closed", "Closed", "bool", default=False), _OP),
    defaults=lambda doc, body, profile=None: {"sections": "", "ruled": False, "closed": False, "operation": "join"},
    create=_loft_create, read=_loft_read,
    update=lambda doc, f, values: (_loft_apply(doc, f, values), f)[1], _pad_of=lambda f: f)


# ----- Coil -----
def _axis_link(doc, body, text):
    picks = parse_picks(doc, text)
    if picks:
        obj, sub = resolve(*picks[0])
        return (obj, [sub])
    return ([o for o in body.Origin.OriginFeatures if o.Role == "Z_Axis"][0], [""])


def _coil_apply(doc, f, values):
    f.Profile = _one(doc, values.get("profile"), "profile")
    f.ReferenceAxis = _axis_link(doc, f.getParentGeoFeatureGroup(), values.get("axis"))
    f.Mode = "pitch-height-angle"
    f.Refine = False  # probed: refining helical solids can corrupt them
    f.LeftHanded = values.get("hand") == "left"
    value_param(doc, f, "Pitch", values.get("pitch") or "5 mm", "length")
    value_param(doc, f, "Height", values.get("height") or "20 mm", "length")


def _coil_create(doc, body, profile, values):
    _one(doc, values.get("profile"), "profile")
    f = _new(body, "coil", values, "Coil")
    _coil_apply(doc, f, values)
    return f


def _coil_read(doc, f):
    axis = f.ReferenceAxis
    sub = axis[1][0] if axis and axis[1] else ""
    return {"profile": _profile(f).Name if _profile(f) else "",
            "axis": ("%s:%s" % (axis[0].Name, sub) if sub else axis[0].Name) if axis and axis[0] else "",
            "pitch": value_text(doc, f, "Pitch") or "5 mm", "height": value_text(doc, f, "Height") or "20 mm",
            "hand": "left" if f.LeftHanded else "right", "operation": _op(f, "coil")}


COIL = SimpleNamespace(
    __name__="incad.features.coil", TITLE="Coil", ICON="coil", TYPES=_TYPES["coil"], NEEDS_PROFILE=False,
    PROPS={"pitch": "Pitch", "height": "Height"}, STRUCTURAL=("join", "cut"),
    preselect=_sketch_preselect(["profile"]), claims=lambda f: not hasattr(f, "InCadThread"),
    FIELDS=(Field("profile", "Profile", "pick", pick="sketch"), Field("axis", "Axis", "pick", pick="axis"),
            Field("pitch", "Pitch", "value_length", default="5 mm"),
            Field("height", "Height", "value_length", default="20 mm"),
            Field("hand", "Direction", "choice", (("right", "Right hand"), ("left", "Left hand")), "right"), _OP),
    defaults=lambda doc, body, profile=None: {"profile": "", "axis": "", "pitch": "5 mm", "height": "20 mm",
                                              "hand": "right", "operation": "join"},
    create=_coil_create, read=_coil_read,
    update=lambda doc, f, values: (_coil_apply(doc, f, values), f)[1], _pad_of=lambda f: f)


# ----- Draft -----
def _draft_apply(doc, f, values):
    faces = [resolve(o, s) for o, s in parse_picks(doc, values.get("faces"))]
    if not faces:
        raise ValueError("Pick the faces to draft")
    obj = faces[0][0]
    if any(o is not obj for o, _ in faces):
        raise ValueError("Pick the faces on the current shape")
    f.Base = (obj, [s for _, s in faces])
    neutral = parse_picks(doc, values.get("neutral"))
    if not neutral:
        raise ValueError("Pick the neutral plane (a flat face or a plane)")
    n_obj, n_sub = resolve(*neutral[0])
    f.NeutralPlane = (n_obj, [n_sub] if n_sub else [""])
    f.Reversed = bool(values.get("reverse"))
    value_param(doc, f, "Angle", values.get("angle") or "1 °", "angle")


def _draft_create(doc, body, profile, values):
    f = body.newObject("PartDesign::Draft", "Draft")
    _draft_apply(doc, f, values)
    body.Tip = f
    return f


def _draft_read(doc, f):
    base = f.Base
    faces = ",".join("%s:%s" % (base[0].Name, s) for s in base[1]) if base and base[0] else ""
    n = f.NeutralPlane
    n_sub = n[1][0] if n and n[1] else ""
    return {"faces": faces, "neutral": ("%s:%s" % (n[0].Name, n_sub) if n_sub else n[0].Name) if n and n[0] else "",
            "angle": value_text(doc, f, "Angle") or "1 °", "reverse": bool(f.Reversed)}


DRAFT = SimpleNamespace(
    __name__="incad.features.draft", TITLE="Draft", ICON="draft", TYPES=("PartDesign::Draft",),
    NEEDS_PROFILE=False, PROPS={"angle": "Angle"}, PRESELECT={"faces": "face"}, SOLID_PICKS=True,
    FIELDS=(Field("faces", "Faces", "picks", pick="face"), Field("neutral", "Neutral plane", "pick", pick="plane"),
            Field("angle", "Angle", "value_angle", default="1 °"), Field("reverse", "Reverse", "bool", default=False)),
    defaults=lambda doc, body, profile=None: {"faces": "", "neutral": "", "angle": "1 °", "reverse": False},
    create=_draft_create, read=_draft_read,
    update=lambda doc, f, values: (_draft_apply(doc, f, values), f)[1], _pad_of=lambda f: f)

KINDS = [SWEEP, LOFT, COIL, DRAFT]
