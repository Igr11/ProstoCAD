"""Safe command guard. Every model change runs in one undo transaction; on an exception or an invalid result
the transaction is aborted, the model recomputed back to its previous state, and the user told why."""
import traceback
from dataclasses import dataclass, field

from incad.core import faceted, log

_depth = 0
_busy = None


def curve_type(edge):
    """An edge's curve TypeId, or "" when FreeCAD has no Python type for it ("undefined curve type": a modelled
    thread's helical edges). Reading .Curve on those raised in every tool that looked at all of a part's edges."""
    try:
        return edge.Curve.TypeId
    except Exception:
        return ""


@dataclass
class SafeResult:
    ok: bool
    value: object = None
    error: str = ""
    broken: list = field(default_factory=list)  # the objects the change broke (names), when that undid it


class _InvalidResult(Exception):
    def __init__(self, text, names=()):
        super().__init__(text)
        self.names = list(names)


def _status(obj):
    try:
        return obj.getStatusString()
    except Exception:
        return ""


def loose_piece(feature):
    """Where a feature's profile sketch has a piece that is no part of a closed outline (the user: Revolve failed
    with "Wire is not closed" — one 0.2 mm line of a half-section left as normal geometry), said in the sketch's
    own coordinates; '' when there is none."""
    from incad.features.common import profile_of
    sk = profile_of(feature)  # (through an Extrude's grown profile)
    if sk is None or getattr(sk, "TypeId", "") != "Sketcher::SketchObject":
        return ""
    try:
        wires = [w for w in sk.Shape.Wires if not w.isClosed()]
        if not wires:
            return ""
        inv = sk.getGlobalPlacement().inverse()
        w = min(wires, key=lambda x: x.Length)
        c = inv.multVec(w.BoundBox.Center)
        more = " (and %d more)" % (len(wires) - 1) if len(wires) > 1 else ""
        return ("%s: the sketch %s has a loose piece, %.2f mm long, near x %.2f, y %.2f%s — it is no part of a closed "
                "outline. Delete it or make it Construction" % (feature.Label, sk.Label, w.Length, c.x, c.y, more))
    except Exception:
        return ""


def _valid(obj, shape, checked=None):
    """shape.isValid(), once per check (`checked`: a set of names found valid): a Solid's shape is a copy of its
    tip's, so the two count as one (the user's STL hook: 4 s an isValid, twice for every Extrude)."""
    tip = getattr(obj, "Tip", None) if obj.TypeId == "PartDesign::Body" else None
    key = getattr(tip if tip is not None else obj, "Name", None)
    if checked is not None and key is not None and key in checked:
        return True
    ok = shape.isValid()
    if ok and checked is not None and key is not None:
        checked.add(key)
    return ok


def _broken(obj, check_shape, deep=True, checked=None):
    """Reason text if obj failed to compute or (check_shape) has invalid PartDesign geometry, else ''. deep=False
    leaves out the full geometry check (isValid: 0.6 s on a big threaded part, 2 s on a slow PC, every preview);
    checked: shapes already found valid in this check."""
    state = getattr(obj, "State", [])
    if "Invalid" in state or "Error" in state:
        status = _status(obj)
        if obj.TypeId == "PartDesign::Body" and status == "Tip shape is empty":
            return ""  # an empty part (everything suppressed / rolled back) is a normal state
        if obj.TypeId == "PartDesign::Thickness" and "Null input shape" in status:
            base = getattr(obj, "BaseFeature", None)
            shape = getattr(base, "Shape", None) if base is not None else None
            n = len(shape.Solids) if shape is not None and not shape.isNull() else 0
            if n > 1:
                return ("%s: the part's body holds %d separate solids (a box drawn away from the part stays its own "
                        "piece); Shell works on one connected solid. Join the piece to the part, or shell it in a "
                        "part file of its own" % (obj.Label, n))
            return "%s: the kernel got no solid to hollow (is the face on the part's body?)" % obj.Label
        if obj.TypeId == "PartDesign::Thickness" and "thick solid" in status:
            if _has_modeled_thread(getattr(obj, "BaseFeature", None)):
                return ("%s: FreeCAD can't hollow a part with a modeled thread in it: make the Shell before the "
                        "thread (drag End of Part above the thread, make the Shell there, then move End of Part "
                        "back)" % obj.Label)
            return ("%s: FreeCAD couldn't hollow this shape. Try a smaller thickness, or make the Shell earlier "
                    "in the tree, before fillets and chamfers" % obj.Label)
        if "not closed" in status.lower() or "open wire" in status.lower():
            loose = loose_piece(obj)
            if loose:
                return loose
        if status and status not in ("Valid", "Touched"):
            from incad.core import model
            return "%s: %s" % (obj.Label, model.friendly(status, obj.TypeId))
        from incad.core import model
        if obj.TypeId in model._KERNEL_FAILURE:  # (a Draft gave no reason at all)
            return "%s: %s" % (obj.Label, model._KERNEL_FAILURE[obj.TypeId])
        return "%s failed to compute" % obj.Label
    if check_shape and obj.TypeId.startswith("PartDesign::"):
        shape = getattr(obj, "Shape", None)
        if deep and shape is not None and not shape.isNull() and not _valid(obj, shape, checked):
            from incad.core import thread_order
            if obj.TypeId in thread_order.KINDS and _has_modeled_thread(getattr(obj, "BaseFeature", None)):
                return ("%s crosses a modeled (helical) thread, which FreeCAD's kernel can't cut or join through: "
                        "put it before the thread in the tree" % obj.Label)
            if obj.TypeId in ("PartDesign::Groove", "PartDesign::Revolution") or \
                    "InCadRevolve" in obj.PropertiesList:  # (ProstoCAD's own Revolve)
                return ("%s: the revolve couldn't form a valid shape — the axis must lie IN the profile's plane "
                        "and not cross or touch the profile (draw the profile on a plane through the axis)" % obj.Label)
            return _why_invalid(obj) or "%s produced invalid geometry" % obj.Label
        if _is_pattern(obj) and (shape is None or shape.isNull() or not shape.Solids):
            return ("%s: the pattern produced nothing — the picked feature can't be repeated here (a feature that "
                    "changes nothing, or a cut that can't be moved). Pick another feature, or put the pattern "
                    "right after it" % obj.Label)
        if _is_pattern(obj) and not _one_copy(obj) and not _drawn_only(obj) and _changes_nothing(obj, shape):
            return ("%s: the pattern changes nothing — its copies fall where the part has no material, or the "
                    "picked feature (a thread, for example) can't be repeated. Pick another feature" % obj.Label)
        # a volume only where there is next to nothing: the volume of a part with a modeled thread took 0.2 s,
        # twice at every preview (the user: "Extrude Cut lags")
        if obj.TypeId in _SUBTRACTIVE and shape is not None and (shape.isNull() or not shape.Solids
                                                                   or (len(shape.Faces) < 4 and shape.Volume < 1e-9)):
            base = getattr(obj, "BaseFeature", None)
            if base is not None and not base.Shape.isNull() and bool(base.Shape.Solids):
                if _has_modeled_thread(base):
                    return ("%s: the cut failed in the kernel and left nothing — a modeled (helical) thread earlier "
                            "in the part is the usual cause. Put this cut BEFORE the thread, or make the thread "
                            "cosmetic" % obj.Label)
                return _whole_part_cut(obj)
        if obj.TypeId == "PartDesign::Boolean" and shape is not None and (shape.isNull() or not shape.Solids):
            base = getattr(obj, "BaseFeature", None)  # an empty result was kept silently: the part simply vanished
            if base is not None and not base.Shape.isNull() and bool(base.Shape.Solids):
                if getattr(obj, "Type", "") == "Common":
                    return ("%s: the solids don't overlap, so Intersect leaves nothing: move a tool into the part"
                            % obj.Label)
                return "%s cuts away the whole part: the tool covers all of it" % obj.Label
        if obj.TypeId == "PartDesign::Boolean" and deep and shape is not None and not shape.isNull() \
                and _touching(shape.Solids) and _faulty(shape):
            return ("%s: the solids only touch along a line or at a point, which makes no sound solid (later "
                    "features fail on it): move the tool a little into the part, or clear of it" % obj.Label)
    return ""


def _whole_part_cut(obj):
    """A cut that leaves nothing, in the words of what was cut: a hole has no axis, extent or profile to choose
    (a 40 mm hole in a 20 mm block), and a hollow Sweep's wall is no cut the user made (its profile shrank below
    the wall)."""
    owner = getattr(obj, "InCadHelperOf", "") if obj.TypeId == "PartDesign::SubtractivePipe" else ""
    sweep = obj.Document.getObject(owner) if owner else None
    if sweep is not None:
        wall = getattr(sweep, "InCadWall", None)
        size = ("%g mm " % wall.Value) if wall is not None else ""
        return ("%s: the %swall is as thick as the profile now, or thicker, so nothing is left of the tube: make the "
                "wall thinner or the profile bigger" % (sweep.Label, size))
    if obj.TypeId == "PartDesign::Hole":
        return ("%s is wider than the part there, so nothing would be left: make the diameter smaller"
                % obj.Label)
    return "%s cuts away the whole part: choose another axis, extent or profile" % obj.Label


def _faulty(shape):
    """True when the full check finds faults in `shape` (faces that cross themselves, edges off their faces)."""
    try:
        shape.check(True)
        return False
    except Exception:
        return True


def _faulty_base(obj):
    """Plain words when the shape `obj` builds on was faulty already (an imported solid whose faces cross
    themselves: every next feature fails on it, the user's air2), naming where it went wrong; '' else."""
    base = getattr(obj, "BaseFeature", None)
    bshape = getattr(base, "Shape", None)
    if bshape is None or bshape.isNull() or not bshape.Solids or len(bshape.Faces) > MANY_FACES:
        return ""
    if _faulty(bshape):
        first = base
        while getattr(first, "BaseFeature", None) is not None and _faulty(first.BaseFeature.Shape):
            first = first.BaseFeature
        imported = getattr(first, "BaseFeature", None) if first.TypeId == "PartDesign::FeatureBase" else None
        where = ("the solid imported as %s" % imported.Label) if imported is not None else first.Label
        return ("%s: the shape it builds on is faulty already (%s has faces that cross themselves or edges off "
                "their faces), so every feature after it fails. Remake that shape, or start the part from a "
                "sound one" % (obj.Label, where))
    return ""


def _why_invalid(obj):
    """Plain words for a feature whose result came out invalid: a faulty shape before it (_faulty_base), or a join
    that touches the part only along a line (a disc resting on a block's top: no kernel makes one solid of that);
    '' else."""
    why = _faulty_base(obj)
    if why:
        return why
    if obj.TypeId == "PartDesign::Fillet":
        return ("%s: the radius is too big for these edges (the rounds meet each other or run past a face next to "
                "them, which is narrower than the radius allows): make the radius smaller" % obj.Label)
    if obj.TypeId == "PartDesign::Chamfer":
        return ("%s: the chamfer is too big for these edges (it runs past a face next to them): make it smaller"
                % obj.Label)
    if obj.TypeId.endswith("Helix"):
        return ("%s: its turns touch or overlap (or the profile crosses the axis), so no solid can be made: make "
                "the pitch larger than the profile is high, or move the profile off the axis" % obj.Label)
    if obj.TypeId in _JOINS:
        return ("%s touches the part only along a line or at a point somewhere (a round face resting on a flat "
                "one, an edge on a face): no solid can be made of that. Make it reach a little into the part, "
                "or keep it clear" % obj.Label)
    return ""


_JOINS = ("PartDesign::Pad", "PartDesign::Revolution", "PartDesign::AdditivePipe", "PartDesign::AdditiveLoft",
          "PartDesign::FeatureAdditivePython")  # (ProstoCAD's own Extrude and Revolve: a Join, a Cut)
_OWN_CUT = "PartDesign::FeatureSubtractivePython"
_CUTS = ("PartDesign::Pocket", "PartDesign::Groove", "PartDesign::Hole", _OWN_CUT)  # (a hole off the part cut nothing too)
_SWEPT = ("PartDesign::AdditivePipe", "PartDesign::SubtractivePipe", "PartDesign::AdditiveLoft",
          "PartDesign::SubtractiveLoft", "PartDesign::AdditiveHelix", "PartDesign::SubtractiveHelix")
_EDGES = ("PartDesign::Fillet", "PartDesign::Chamfer")
MANY_FACES = 3000  # more: the full check (about 0.4 s a thousand faces) is left out at OK


def unsound(obj):
    """Text for a cut or Boolean whose result the kernel calls valid but whose full check fails — a cut touching a
    wall along a line (a circle tangent to the side) leaves a wall of no thickness there, and later features (a
    fillet, a shell, another cut) fail on it; '' when sound, or not looked at (big, or a modeled thread: the full
    check finds false faults on helical faces)."""
    if obj is not None and _is_pattern(obj):
        return _loose_copies(obj) or _faulty_copies(obj)
    if obj is None or (obj.TypeId not in _CUTS + ("PartDesign::Boolean", "PartDesign::Pad", _JOINS[-1]) + _SWEPT + _EDGES
                       and "InCadEmboss" not in getattr(obj, "PropertiesList", ())):  # (an Emboss wrapped on a round
        return ""                                                                     # face had faulty edges)
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull() or len(shape.Faces) > MANY_FACES or \
            _has_modeled_thread(getattr(obj, "BaseFeature", None)) or hasattr(obj, "InCadThread") or \
            (obj.TypeId == "PartDesign::Hole" and getattr(obj, "ModelThread", False)):  # (a Coil is looked at)
        return ""
    try:
        shape.check(True)
        return ""
    except Exception:
        if _faulty_base(obj):  # the fault was there before it (a STEP's solid): no worse than its part, not this
            return ""          # feature's to answer for (the message stays for a feature that fails outright)
        if obj.TypeId in _EDGES:
            return ("%s leaves a faulty spot (tiny edges or faces that cross where it meets another face): a later "
                    "feature may fail there. Try a slightly different size" % obj.Label)
        if obj.TypeId.endswith("Helix"):
            return ("%s runs through itself (its turns touch or overlap, or its profile crosses the axis): make the "
                    "pitch larger than the profile is high, or move the profile off the axis" % obj.Label)
        if obj.TypeId in _SWEPT:
            return ("%s runs through itself somewhere (a path that bends tighter than the profile is wide, or a "
                    "closed loft that comes back across itself): a later feature may fail on it. Make the profile "
                    "smaller, the bends wider, or the loft open" % obj.Label)
        if obj.TypeId in ("PartDesign::Pad", _JOINS[-1]):
            return _why_invalid(obj) or ("%s touches the part only along a line or at a point somewhere: a later "
                                         "feature may fail there. Make it reach a little into the part, or keep it "
                                         "clear" % obj.Label)
        return ("%s leaves a wall of no thickness somewhere (the cut touches a side along a line, or at a point): "
                "it can't be printed, and a later fillet or shell may fail there. Move the profile a little into "
                "the side, or clear of it" % obj.Label)


_DRESSUPS = ("PartDesign::Fillet", "PartDesign::Chamfer", "PartDesign::Thickness", "PartDesign::Draft")


def dressup_bases(doc, names):
    """Names of the features the fillets, chamfers, shells and drafts among `names` are built on."""
    out = set()
    for n in names:
        o = doc.getObject(n)
        base = getattr(o, "BaseFeature", None) if o is not None and o.TypeId in _DRESSUPS else None
        if base is not None:
            out.add(base.Name)
    return out


_LINKED = ("PartDesign::Fillet", "PartDesign::Chamfer", "PartDesign::Thickness", "PartDesign::Draft")


def _sig(el, box):
    """What tells an edge or face again after a size change: its kind, curve / surface type, direction (an edge's
    either way; a face's outward, sign kept) and where it sits in the part's box (0..1 on each axis: the right-hand
    edge stays the right-hand one when the part gets wider)."""
    if el.ShapeType == "Edge":
        c = el.Curve
        name = type(c).__name__
        d = None
        if name == "Line":
            d = el.Vertexes[-1].Point - el.Vertexes[0].Point
        elif hasattr(c, "Axis"):
            d = c.Axis
        signed = False
    else:
        name = type(el.Surface).__name__
        try:
            u, v = el.Surface.parameter(el.CenterOfMass)
            d = el.normalAt(u, v)
        except Exception:
            d = None
        signed = True
    c = el.CenterOfMass
    rel = []
    for lo, length, x in ((box.XMin, box.XLength, c.x), (box.YMin, box.YLength, c.y), (box.ZMin, box.ZLength, c.z)):
        rel.append((x - lo) / length if length > 1e-9 else 0.0)
    if d is not None and d.Length > 1e-12:
        d = d * (1.0 / d.Length)
    return (el.ShapeType, name, d, signed, tuple(rel))


def _alike(a, b):
    """Same kind, type and direction (an edge's either way, a face's the same way)."""
    if a[0] != b[0] or a[1] != b[1]:
        return False
    if a[2] is None or b[2] is None:
        return a[2] is None and b[2] is None
    dot = a[2].dot(b[2])
    return abs(dot - 1) < 1e-6 if a[3] else abs(abs(dot) - 1) < 1e-6


def _gap(a, b):
    return sum((x - y) ** 2 for x, y in zip(a[4], b[4])) ** 0.5


def _side(face):
    """What tells a face next to an edge again, wherever it moved: its surface type and its facing (a plane's
    outward normal, sign kept; a round face's axis, either way)."""
    import FreeCAD as App
    srf = face.Surface
    name = type(srf).__name__
    try:
        if name == "Plane":
            u, v = srf.parameter(face.CenterOfMass)
            d, signed = face.normalAt(u, v), True
        elif hasattr(srf, "Axis"):
            d, signed = App.Vector(srf.Axis), False
        else:
            d, signed = None, False
    except Exception:
        d, signed = None, False
    if d is not None and d.Length > 1e-12:
        d = d * (1.0 / d.Length)
    return ("Face", name, d, signed, ())


def _sides(shape, edge):
    """The faces next to `edge` in `shape`, each as _side; None when they can't be told."""
    try:
        import Part
        return sorted((_side(f) for f in shape.ancestorsOfType(edge, Part.Face)), key=lambda s: s[1])
    except Exception:
        return None


def _same_sides(a, b):
    """The same faces next to the edge (by kind and facing), each matched once."""
    if a is None or b is None or len(a) != len(b):
        return False
    left = list(b)
    for side in a:
        hit = next((x for x in left if _alike(side, x)), None)
        if hit is None:
            return False
        left.remove(hit)
    return True


def link_snapshot(doc):
    """{feature: (its picks, {element: signature} of every edge and face of the shape they are on)}, taken before
    a change (see repair_links)."""
    out = {}
    for f in doc.Objects:
        if f.TypeId not in _LINKED and "InCadShell" not in getattr(f, "PropertiesList", ()):
            continue
        link = getattr(f, "Base", None)
        if not isinstance(link, tuple) or len(link) != 2 or link[0] is None:
            continue
        if any(not isinstance(s, str) or s.startswith("?") for s in link[1]):
            continue
        try:
            shape = link[0].Shape
            box = shape.BoundBox
            sigs = {}
            for kind, pool in (("Edge", shape.Edges), ("Face", shape.Faces)):
                if any(s.startswith(kind) for s in link[1]):
                    for i, el in enumerate(pool):
                        sigs["%s%d" % (kind, i + 1)] = _sig(el, box)
            sides = {}  # the picked edges' own faces (the edge on the same two faces is the same edge)
            for s in link[1]:
                if s.startswith("Edge"):
                    sides[s] = _sides(shape, shape.getElement(s))
            out[f.Name] = (list(link[1]), sigs, sides)
        except Exception:
            pass
    return out


NEAR = 0.02  # how close (as a share of the part's size) a found element must sit to where the lost one was
FAR = 0.25   # how far it may have moved when it also lies between the same two kinds of face (a pocket made wider)


def repair_links(doc, snap):
    """Fillets / chamfers / shells / drafts whose edges or faces FreeCAD lost in a size change ('?Edge1': a Pad
    made 30 high, or a sketch made wider, lost the vertical edges, and the change was refused - the commonest edit
    there is) get them back: the one element of the same kind, type and direction that sits where the lost one
    sat in the part's box and is no other element's continuation. An edge that lies between the same two kinds of
    face as before (the rim of a pocket made 0.4 mm wider: the top and the same wall) may have moved further, as
    long as it is clearly the nearest such edge. Nothing is guessed: when none or two fit, the link stays lost and
    the feature says so (a fillet moved silently to another edge was worse). Returns the features repaired."""
    fixed = []
    for f in doc.Objects:
        if f.Name not in snap:
            continue
        link = getattr(f, "Base", None)
        if not isinstance(link, tuple) or len(link) != 2 or link[0] is None or \
                not any(isinstance(s, str) and s.startswith("?") for s in link[1]):
            continue
        picks, old = snap[f.Name][:2]
        old_sides = snap[f.Name][2] if len(snap[f.Name]) > 2 else {}
        obj, subs = link[0], list(link[1])
        if len(subs) != len(picks):
            continue
        shape = obj.Shape
        box = shape.BoundBox
        new_sigs = {}
        for kind, pool in (("Edge", shape.Edges), ("Face", shape.Faces)):
            for i, el in enumerate(pool):
                new_sigs["%s%d" % (kind, i + 1)] = _sig(el, box)
        taken = {s for s in subs if not s.startswith("?")}
        result, ok = [], True
        for sub, was in zip(subs, picks):
            if not sub.startswith("?"):
                result.append(sub)
                continue
            lost = old.get(was)
            if lost is None:
                ok = False
                break
            sides = old_sides.get(was)
            reach = FAR if sides else NEAR
            near = []
            for name, sig in new_sigs.items():
                if name in taken or not _alike(sig, lost) or _gap(sig, lost) > reach:
                    continue
                # no other element of before sits closer to it: it is the lost one moved, not a survivor
                if any(o_name != was and _alike(o_sig, sig) and _gap(o_sig, sig) < _gap(lost, sig)
                       for o_name, o_sig in old.items()):
                    continue
                if _gap(sig, lost) > NEAR and not _same_sides(sides, _sides(shape, shape.getElement(name))):
                    continue  # moved further than a size change moves an edge: only on the same two faces
                near.append((_gap(sig, lost), name))
            near.sort()
            if not near or (len(near) > 1 and near[1][0] - near[0][0] < max(NEAR / 2, near[0][0])):
                ok = False  # none, or two alike: not guessed
                break
            taken.add(near[0][1])
            result.append(near[0][1])
        if ok:
            f.Base = (obj, result)
            fixed.append(f.Name)
    if fixed:
        log.logger().info("Edges / faces found again after a size change: %s", ", ".join(fixed))
    return fixed


def heal_bases(doc, names):
    """A fillet or chamfer the kernel refused changed the shape it was built on (OCC updates the tolerances of the
    shared edges in place): that feature was left "Unorientable", and every next feature failed on it (after a
    Boolean join with a near-flush step). Each of `names` whose shape is invalid now is computed again. Returns
    the names healed."""
    healed = []
    for n in names:
        o = doc.getObject(n)
        shape = getattr(o, "Shape", None) if o is not None else None
        try:
            if shape is None or shape.isNull() or shape.isValid():
                continue
            o.touch()
            doc.recompute([o])
            healed.append(n)
        except Exception:
            pass
    if healed:
        try:
            doc.recompute()  # the Solid over it took the spoilt shape too: settled with the rest
        except Exception:
            pass
        log.logger().info("Computed again after a refused fillet / chamfer: %s", ", ".join(healed))
    return healed


def _loose_copies(obj):
    """A pattern or mirror whose copies land off the part: each such copy is a lump of its own in the Solid (a
    later Shell or Fillet fails on a Solid of several pieces) - said, the copies kept."""
    shape = getattr(obj, "Shape", None)
    base = getattr(getattr(obj, "BaseFeature", None), "Shape", None)
    try:
        extra = len(shape.Solids) - (len(base.Solids) if base is not None and not base.isNull() else 1)
    except Exception:
        return ""
    if extra <= 0 or getattr(obj, "Result", "") in ("new", "each") and "InCadPattern" in obj.PropertiesList:
        return ""  # copies made a Solid of their own: lying apart is what was asked for
    originals = [o for o in getattr(obj, "Originals", None) or [] if o is not None]
    if originals and all(o.TypeId in _SUBTRACTIVE for o in originals):  # cuts: they split the part, nothing lands off
        return ("%s cuts the part into %d separate pieces in one Solid (a Shell or Fillet then fails on it): split it "
                "with Split if that is meant" % (obj.Label, len(shape.Solids)))
    return ("%s: %d of its copies land off the part and stay separate lumps in the Solid (a Shell or Fillet then "
            "fails on it): fewer copies, a smaller spacing, or exclude them" % (obj.Label, extra))


def _faulty_copies(obj):
    """A pattern or mirror whose result the kernel calls valid but whose full check fails on a sound base: copies
    that touch each other or the part only along a line (discs of a pattern spaced their diameter apart) - said."""
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull() or not shape.Solids or len(shape.Faces) > MANY_FACES or \
            _has_modeled_thread(getattr(obj, "BaseFeature", None)) or not _faulty(shape) or _faulty_base(obj):
        return ""
    return ("%s: its copies touch each other or the part only along a line or at a point (copies spaced exactly "
            "their width apart, round faces resting on each other): a later Shell or Fillet may fail there. Make "
            "the spacing a little smaller, so they overlap, or larger, so they stand apart" % obj.Label)


def _touching(solids, most=12):
    """Two of the separate solids touching (a line or a point in common). Only a sign: a join of a disc on a block's
    side gives pieces with an edge each there and the full check fails, while a cut by a tool tangent to two walls
    (a 20 mm round through a 20 mm block) gives pieces sharing that edge, which is sound — the caller asks the full
    check too. Solids apart (a part cut in two) are fine. More than `most` solids: not looked at (slow)."""
    if not 1 < len(solids) <= most:
        return False
    for i, a in enumerate(solids):
        for b in solids[i + 1:]:
            try:
                if a.BoundBox.intersect(b.BoundBox) and a.distToShape(b)[0] < 1e-6:
                    return True
            except Exception:
                pass
    return False


def _has_modeled_thread(feature):
    """True if feature, or a feature it is built on, carries a modeled helix (helix feature or Hole with ModelThread)."""
    seen = set()
    while feature is not None and feature.Name not in seen:
        seen.add(feature.Name)
        if feature.TypeId.endswith("Helix") or (feature.TypeId == "PartDesign::Hole"
                                                and getattr(feature, "ModelThread", False)):
            return True
        try:  # InCad's own threads: cut in, or grown on (the user's jar: Shell after the thread failed, unexplained)
            from incad.features import thread
            if thread.claims(feature):
                return True
        except Exception:
            pass
        feature = getattr(feature, "BaseFeature", None)
    return False


def _drawn_only(obj):
    """A pattern whose dialog is open: its copies are drawn as outlines, built on OK (and checked then)."""
    try:
        from incad.features import own_pattern
        return obj.Name in own_pattern.GHOST
    except Exception:
        return False


def _one_copy(pattern):
    """True for a pattern of Count 1 (in every direction): it is the original alone, as meant (a count parameter set
    to 1 switches the copies off), not a pattern that "changes nothing"."""
    counts = pattern
    props = [p for p in ("Occurrences", "Occurrences2") if p in getattr(counts, "PropertiesList", ())]
    try:
        return bool(props) and all(int(getattr(counts, p)) <= 1 for p in props)
    except Exception:
        return False


def _changes_nothing(pattern, shape):
    """True if the pattern's result has the same volume as what it was built on (it added or removed nothing)."""
    try:
        base = getattr(pattern, "BaseFeature", None)
        if base is None:
            group = pattern.getParentGeoFeatureGroup()
            earlier = [o for o in (group.Group if group is not None else [])
                       if o.isDerivedFrom("PartDesign::Feature")]
            earlier = earlier[:earlier.index(pattern)] if pattern in earlier else []
            base = earlier[-1] if earlier else None
        if base is None or base.Shape.isNull() or shape.isNull():
            return False
        return abs(shape.Volume - base.Shape.Volume) < 0.01  # mm3: nothing a pattern could be meant to make
    except Exception:
        return False


_TRANSFORMED = ("PartDesign::LinearPattern", "PartDesign::PolarPattern", "PartDesign::Mirrored",
                "PartDesign::MultiTransform")


def _is_pattern(obj):
    """FreeCAD's patterns and mirrors, and InCad's own Pattern (incad.features.own_pattern)."""
    return obj.TypeId in _TRANSFORMED or "InCadPattern" in getattr(obj, "PropertiesList", ())
_SUBTRACTIVE = ("PartDesign::Groove", "PartDesign::Pocket", _OWN_CUT, "PartDesign::SubtractiveHelix",
                "PartDesign::SubtractivePipe", "PartDesign::SubtractiveLoft", "PartDesign::Hole")


_drag_edit = False


def set_drag_edit(on):
    """An assembly is in FreeCAD's edit only for a drag of one of its parts (incad.ui.asm_drag): that is no FreeCAD
    task, InCad's commands and refreshes go on."""
    global _drag_edit
    _drag_edit = bool(on)


def drag_edit():
    return _drag_edit


def edit_open():
    """True while FreeCAD has a sketch or feature task dialog open (GUI only)."""
    if _drag_edit:
        return False
    try:
        import FreeCAD as App
        if not App.GuiUp:
            return False
        import FreeCADGui as Gui
        gdoc = Gui.ActiveDocument
        vp = gdoc.getInEdit() if gdoc is not None else None
        if vp is not None and getattr(getattr(vp, "Object", None), "TypeId", "") == "Sketcher::SketchObject":
            return False  # InCad's own sketch editing: Sketcher commits each tool step itself
        return bool(Gui.Control.activeDialog()) or vp is not None
    except Exception:
        return False


class Stopped(Exception):
    """The user pressed Esc during a long computation (the user: Escape must cancel any operation)."""


_esc = {"seen": 0.0, "since": 0.0, "watching": False}


def _watch_esc():
    """A thread that notes when Esc was last down (checked every 30 ms): a short press during one long step of a loop
    is still seen afterwards."""
    import time
    from incad import host
    while True:
        if host.esc_down():
            _esc["seen"] = time.monotonic()
        time.sleep(0.03)


def watch_esc():
    """A long computation starts: an Esc pressed from now on stops it (stop_if_esc)."""
    import time
    _esc["since"] = time.monotonic()
    if not _esc["watching"]:
        _esc["watching"] = True
        import threading
        threading.Thread(target=_watch_esc, name="InCad Esc", daemon=True).start()


def stop_if_esc(what="the computation"):
    """Raises Stopped when Esc was pressed since watch_esc(): called inside InCad's long loops, which run with the
    event loop stopped (a key press is no event then). The step it is part of fails and is rolled back."""
    from incad import host
    import time
    if _esc["watching"] and _esc["seen"] > _esc["since"] or host.esc_down():
        _esc["since"] = time.monotonic()  # one press stops one computation
        raise Stopped("Stopped %s (Esc)" % what)


def open_sketch(doc):
    """The sketch open in InCad's sketch editing in `doc` (GUI only), else None."""
    try:
        import FreeCAD as App
        if doc is None or not App.GuiUp:
            return None
        import FreeCADGui as Gui
        gdoc = Gui.getDocument(doc.Name)
        obj = getattr(gdoc.getInEdit() if gdoc is not None else None, "Object", None)
        return obj if getattr(obj, "TypeId", "") == "Sketcher::SketchObject" else None
    except Exception:
        return None


def recompute(doc):
    """doc.recompute(), but while a sketch is open in `doc` that sketch only: the rest of the part follows on Finish
    Sketch (the user's jar, a thread and letters wrapped on a curved face after the sketch: 45 s on every click)."""
    sk = open_sketch(doc)
    return doc.recompute([sk]) if sk is not None else doc.recompute()


def set_busy(label):
    """A feature dialog is open (label) or closed (None): no other model change may start meanwhile."""
    global _busy
    _busy = label


def busy():
    return _busy


_on_refused = None  # shows the open dialog when a command is refused because of it (set by the shell)


def set_refused_hook(fn):
    global _on_refused
    _on_refused = fn


def refused():
    """A command was refused because a dialog is open: make sure the user sees that dialog."""
    if _on_refused is not None:
        try:
            _on_refused()
        except Exception:
            pass


def snapshot_broken(doc):
    return {o.Name for o in doc.Objects if _broken(o, False)}


def problems(doc, broken_before, touched, deep=True):
    """Reasons why objects that were fine (not in broken_before) are now broken; shapes checked for `touched`."""
    return [why for _name, why in broken_now(doc, broken_before, touched, deep)]


def beyond_end(obj):
    """Is obj a Solid's step below its End of Part (rolled back: not part of the result, its failure no failure)?"""
    try:
        if not obj.isDerivedFrom("PartDesign::Feature") or obj.TypeId == "PartDesign::Body":
            return False
        body = obj.getParentGeoFeatureGroup()
        if body is None or body.TypeId != "PartDesign::Body":
            return False
        group = list(body.Group)
        if obj not in group:
            return False
        tip = body.Tip
        return tip is None or (tip in group and group.index(obj) > group.index(tip))
    except Exception:
        return False


def rolled_back(obj, _seen=None):
    """beyond_end, or a helper used only by steps beyond it (a Boolean's grown tool below End of Part)."""
    if beyond_end(obj):
        return True
    seen = _seen if _seen is not None else set()
    if obj.Name in seen:
        return False
    seen.add(obj.Name)
    users = [u for u in getattr(obj, "InList", []) if u.TypeId not in ("PartDesign::Body", "App::Part")]
    return bool(users) and all(rolled_back(u, seen) for u in users)


def broken_now(doc, broken_before, touched, deep=True):
    """[(name, reason)] of the objects that were fine (not in broken_before) and are broken now — a step below End
    of Part left out (the user: moving End of Part above the lid's grip ribs was undone, the lid's Boolean below it
    having lost its tool)."""
    checked = set()
    found = [(o.Name, _broken(o, o.Name in touched, deep, checked)) for o in doc.Objects
             if o.Name not in broken_before and not beyond_end(o)]
    found = [(n, why) for n, why in found if why]
    # a Solid's shape is its last feature's: when that feature is named with its reason, the Solid's own bare
    # "Solid1 produced invalid geometry" is the same fault said twice (and read as a second culprit)
    named = {n for n, _why in found}
    inside = set()
    for n in named:
        o = doc.getObject(n)
        group = o.getParentGeoFeatureGroup() if o is not None and hasattr(o, "getParentGeoFeatureGroup") else None
        if group is not None and group.Name != n:
            inside.add(group.Name)
    return [(n, why) for n, why in found
            if not (n in inside and why == "%s produced invalid geometry" % doc.getObject(n).Label)]


def _relink(doc):
    try:
        from incad.core import model
        model.relink(doc)  # objects restored by the abort lose their formula links (FreeCAD quirk)
    except Exception as e:
        log.report("warning", "Could not repair formula links: %s" % e, traceback.format_exc())


def run_safely(label, fn, doc=None, only=None, quiet_broken=False):
    """Run fn() as one undo step named `label`. Never raises at the outermost level. only: the objects to recompute
    (an open sketch: the part follows on Finish Sketch; or a function of fn's result giving them), else the whole
    document. quiet_broken: when the change broke
    other objects it is undone without a message, their names in the result's `broken` (the caller asks)."""
    global _depth
    if _depth > 0:
        return SafeResult(True, fn())  # nested: the outer guard owns the transaction and catches errors
    if _busy:
        msg = "Finish the %s dialog first" % _busy
        refused()
        log.report("warning", msg)
        return SafeResult(False, error=msg)
    import FreeCAD as App
    doc = doc or App.ActiveDocument
    if doc is None:
        try:
            return SafeResult(True, fn())
        except Exception as e:
            msg = "%s failed: %s" % (label, e)
            log.report("error", msg, traceback.format_exc())
            return SafeResult(False, error=msg)
    if edit_open():  # the open edit owns the pending transaction and its OK/Cancel
        refused()
        msg = "Finish or cancel the current edit before %s" % label
        log.report("warning", msg)
        return SafeResult(False, error=msg)
    if doc.HasPendingTransaction:  # a stray (implicit) change: keep it as its own undo step, apart from ours
        doc.commitTransaction()
    if not doc.UndoMode:
        doc.UndoMode = 1
    broken_before = snapshot_broken(doc)
    links = link_snapshot(doc)
    names_before = {o.Name for o in doc.Objects}
    watch_esc()
    _depth += 1
    doc.openTransaction(label)
    try:
        value = fn()
        if callable(only):  # the objects to recompute known only from what fn made (a new sketch)
            only = only(value)
        if only is None and open_sketch(doc) is not None:  # a sketch tool: the open sketch and what it made only
            only = [open_sketch(doc)] + [o for o in doc.Objects if o.Name not in names_before]
        touched = {o.Name for o in doc.Objects if "Touched" in o.State or o.Name not in names_before}
        faceted.skip_refine([o for o in doc.Objects if o.Name in touched])
        doc.recompute(only) if only else doc.recompute()
        if repair_links(doc, links):  # a size change lost a fillet's edges: found again, computed again
            doc.recompute()
        found = broken_now(doc, broken_before, {o.Name for o in only} if only else touched)
        if found:
            raise _InvalidResult("; ".join(why for _n, why in found), [n for n, _why in found])
    except Exception as e:
        try:
            bases = dressup_bases(doc, {o.Name for o in doc.Objects if o.Name not in names_before
                                        or "Touched" in o.State})
        except Exception:
            bases = set()
        doc.abortTransaction()
        doc.recompute()
        heal_bases(doc, bases)
        _relink(doc)
        invalid = isinstance(e, _InvalidResult)
        msg = str(e) if invalid else "%s failed: %s" % (label, e)
        if not (invalid and quiet_broken):
            log.report("error", msg, "" if invalid else traceback.format_exc())
        return SafeResult(False, error=msg, broken=e.names if invalid else [])
    finally:
        _depth -= 1
    doc.commitTransaction()
    return SafeResult(True, value)
