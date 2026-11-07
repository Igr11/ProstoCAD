"""Pattern on OpenCASCADE (the user's law: ProstoCAD's building operations are its own, not FreeCAD's PartDesign
with workarounds on top): one feature (a PartDesign::FeaturePython marked InCadPattern) for the Rectangular,
Circular and Mirror dialogs, in place of FreeCAD's LinearPattern / PolarPattern / Mirrored.

- Features: each original's tool (its AddSubShape) moved to every kept place; the copies of joins fused with the part
  in one general fuse, those of cuts cut, in the originals' order (the original itself is in the part already).
- Solid: the whole shape of a Solid copied to every place. Result Join: a step at the end of that Solid (it repeats
  what comes before it); New Solid: the copies together as a Solid of their own; New Solid per copy: each copy a
  Solid of its own (one step each, the steps of one pattern share its settings). The new Solids follow their source.

Then as our Extrude: one sound solid where the copies only touch (discs spaced their diameter apart), the volume
checked, Refine only while the result stays sound (off on an STL's triangles). The properties are those of FreeCAD's
patterns (Originals, Direction, Occurrences, Length, Reversed, Axis, Angle, MirrorPlane…), so the dialogs read and
write both; Excluded (copy numbers, 1 is the original) is the feature's own. A FreeCAD pattern of an older file
opens and computes as it is, and becomes this one when its dialog changes it (`convert`)."""
import FreeCAD as App
import Part

MARK = "InCadPattern"  # rectangular | circular | mirror
GROUP = "InCadPatternGroup"  # New Solid per copy: the name shared by the steps of one pattern
OLD = {"PartDesign::LinearPattern": "rectangular", "PartDesign::PolarPattern": "circular",
       "PartDesign::Mirrored": "mirror"}
NAMES = {"rectangular": "RectangularPattern", "circular": "CircularPattern", "mirror": "Mirror"}
_LINKS = ("Direction", "Direction2", "Axis", "MirrorPlane")
_VALUES = ("Reversed", "Reversed2", "Occurrences", "Occurrences2", "Length", "Length2", "Angle", "Refine")
_PROPS = (("Originals", "App::PropertyLinkList", "The features repeated"),
          ("Source", "App::PropertyLinkGlobal", "The Solid repeated (New Solid)"),
          ("PatternOf", "App::PropertyString", "features: the picked features; solid: the whole Solid"),
          ("Result", "App::PropertyString", "Solid: join, new (one new Solid) or each (a new Solid per copy)"),
          ("Copy", "App::PropertyInteger", "New Solid per copy: the copy this step makes"),
          ("Direction", "App::PropertyLinkSubGlobal", "Direction 1"),
          ("Reversed", "App::PropertyBool", "Direction 1 the other way"),
          ("Occurrences", "App::PropertyInteger", "Count"),
          ("Length", "App::PropertyLength", "First to last copy (direction 1)"),
          ("Direction2", "App::PropertyLinkSubGlobal", "Direction 2"),
          ("Reversed2", "App::PropertyBool", "Direction 2 the other way"),
          ("Occurrences2", "App::PropertyInteger", "Count 2"),
          ("Length2", "App::PropertyLength", "First to last copy (direction 2)"),
          ("Axis", "App::PropertyLinkSubGlobal", "The axis copies turn about"),
          ("Angle", "App::PropertyAngle", "First to last copy round the axis (360: all round)"),
          ("MirrorPlane", "App::PropertyLinkSubGlobal", "The mirror plane"),
          ("Excluded", "App::PropertyIntegerList", "Copies left out (1 is the original)"),
          ("Refine", "App::PropertyBool", "Merge faces lying in one plane"))


def kind_of(f):
    """rectangular / circular / mirror for our pattern and FreeCAD's, else None."""
    return OLD.get(getattr(f, "TypeId", "")) or getattr(f, MARK, None) or None


def is_own(f):
    return f is not None and MARK in getattr(f, "PropertiesList", ())


def is_solid(f):
    return is_own(f) and getattr(f, "PatternOf", "") == "solid"


def result_of(f):
    return (getattr(f, "Result", "") or "join") if is_solid(f) else ""


def group_of(f):
    """The steps of one New Solid per copy pattern (f among them), by copy number; [f] for any other pattern."""
    name = getattr(f, GROUP, "") if is_own(f) else ""
    if not name:
        return [f]
    out = [o for o in f.Document.Objects if is_own(o) and getattr(o, GROUP, "") == name]
    return sorted(out, key=lambda o: o.Copy)


def lead_of(f):
    """The step the dialog reads and writes: any pattern itself; for New Solid per copy the first copy's (each step
    holds the same settings, see sync_each)."""
    return group_of(f)[0] if is_own(f) else f


class PatternFeature:
    """The proxy. FreeCAD saves its module path (incad.features.own_pattern) and class name in the file."""
    def __init__(self, obj, kind=None):
        for prop, type_id, doc in _PROPS:
            if prop not in obj.PropertiesList:
                obj.addProperty(type_id, prop, "Pattern", doc)
        if MARK not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", MARK, "InCad", "rectangular, circular or mirror")
            obj.setPropertyStatus(MARK, "Hidden")
            for prop in ("PatternOf", "Result", "Copy"):
                obj.setPropertyStatus(prop, "Hidden")
            obj.Occurrences, obj.Occurrences2, obj.Angle = 2, 1, 360.0
            obj.Length, obj.Length2 = 10.0, 10.0  # the dialog's formula comes after: no "spacing 0" in between
            obj.PatternOf = "features"
            obj.Refine = App.ParamGet("User parameter:BaseApp/Preferences/Mod/PartDesign").GetBool("RefineModel", True)
        if kind:
            setattr(obj, MARK, kind)
        obj.Proxy = self

    def execute(self, obj):
        obj.Shape = build(obj)

    def dumps(self):
        return None

    def loads(self, state):
        return None


def new(doc, body, kind, name=None):
    """Our pattern of `kind` as the next step of `body` (after its Tip), the Tip now."""
    tip = body.Tip
    f = body.newObject("PartDesign::FeaturePython", name or NAMES[kind])
    PatternFeature(f, kind)
    f.BaseFeature = tip
    body.Tip = f
    from incad.core import document
    document.show_like(f, tip)
    return f


# ----- where the copies go -----
def _plane(link):
    """(point, normal) of a mirror plane in its Solid's coordinates: an origin or work plane, or a flat face."""
    obj, subs = link
    sub = subs[0] if subs else ""
    if not sub:
        pl = obj.Placement
        return App.Vector(pl.Base), pl.Rotation.multVec(App.Vector(0, 0, 1))
    face = obj.Shape.getElement(sub)
    if face.Surface.TypeId != "Part::GeomPlane":
        raise ValueError("The mirror plane must be flat: an origin or work plane, or a flat face")
    return App.Vector(face.Surface.Position), App.Vector(face.Surface.Axis)


def transforms(f):
    """Every copy's place, the original (identity) first: a Placement, or ('mirror', point, normal)."""
    if kind_of(f) == "mirror":
        if not f.MirrorPlane or f.MirrorPlane[0] is None:
            raise ValueError("Pick the mirror plane")
        point, normal = _plane(f.MirrorPlane)
        return [App.Placement(), ("mirror", point, normal)]
    from incad.features import pattern_exclude
    return pattern_exclude.transforms(f)


def placed(shape, where):
    """A copy of shape at one place of `transforms`."""
    if isinstance(where, tuple):
        return shape.mirror(where[1], where[2])
    moved = Part.Shape(shape)  # its geometry shared, as FreeCAD's pattern does (a full copy each: 8 ms in 100)
    moved.Placement = where.multiply(moved.Placement)
    return moved


def kept(f):
    """The copy numbers made (1 is the original), the excluded ones left out."""
    total = len(transforms(f))
    out = set(f.Excluded)
    return [n for n in range(1, total + 1) if n not in out]


def _check_steps(f):
    if kind_of(f) == "rectangular":
        for count, length in (("Occurrences", "Length"), ("Occurrences2", "Length2")):
            if int(getattr(f, count)) > 1 and getattr(f, length).Value < 1e-9 and \
                    (count == "Occurrences" or f.Direction2):
                raise ValueError("The spacing must be more than 0: give the copies a spacing")
    if kind_of(f) == "circular" and int(f.Occurrences) > 1 and abs(f.Angle.Value) < 1e-9:
        raise ValueError("The angle must be more than 0: give the copies an angle")
    for count in ("Occurrences", "Occurrences2"):
        if count in f.PropertiesList and int(getattr(f, count)) < 1:
            raise ValueError("The count must be a whole number, 1 or more")


# ----- the shapes -----
def _subtractive(o):
    from incad.features import pattern_exclude
    return pattern_exclude._subtractive(o)


def tools(f):
    """[(original, its tool)] of a Features pattern, suppressed originals left out."""
    from incad.features.common import tool_shape
    out = []
    for o in f.Originals:
        if o is None or getattr(o, "Suppressed", False):
            continue
        if is_own(o) and not is_solid(o):  # a pattern picked: every copy it makes (a Mirror of a pattern mirrors all)
            places = transforms(o)
            out.extend((inner, placed(t, places[n - 1])) for inner, t in tools(o) for n in kept(o))
            continue
        try:
            t = tool_shape(o)
        except Exception:
            continue
        if not t.isNull() and t.Solids:
            out.append((o, t))
    return out


def source_shape(f):
    """Solid pattern: the shape repeated, in the coordinates its directions are in (the source Solid's own)."""
    if result_of(f) == "join":
        return _base(f)
    src = f.Source
    if src is None or src.Shape.isNull() or not src.Shape.Solids:
        raise ValueError("Pick the Solid to repeat" if src is None else "%s has no solid yet" % src.Label)
    shape = src.Shape.copy()
    shape.Placement = src.Placement.inverse().multiply(shape.Placement)
    return shape


def _base(f):
    base = f.BaseFeature.Shape if f.BaseFeature is not None else None
    if base is None or base.isNull() or not base.Solids:
        raise ValueError("The pattern has no part to work on")
    return base


GHOST = set()  # patterns whose dialog is open: the copies are drawn as outlines (ui/pattern_ghost), built on OK


def build(f):
    """The pattern's shape (see the module's notes). While its dialog is open only the part (Join) or one copy (a
    New Solid) is built; the copies show as outlines."""
    _check_steps(f)
    places = transforms(f)
    numbers = kept(f)
    if f.Name in GHOST:
        moved = [places[n - 1] for n in numbers if n > 1]
        if not moved:
            raise ValueError("The pattern makes no copy: a count of 2 or more, or fewer excluded")
        if result_of(f) in ("new", "each"):
            return _in_own_frame(f, placed(source_shape(f), moved[0]))
        return _base(f).copy()
    if result_of(f) == "each":
        if f.Copy not in numbers:
            raise ValueError("Copy %d is no longer in the pattern: open the pattern and press OK" % f.Copy)
        return _in_own_frame(f, placed(source_shape(f), places[f.Copy - 1]))
    moved = [places[n - 1] for n in numbers if n > 1]
    if result_of(f) == "new":
        if not moved:
            raise ValueError("The pattern makes no copy: a count of 2 or more, or fewer excluded")
        src = source_shape(f)
        copies = [placed(src, pl) for pl in moved]
        shape, touches = (copies[0], False) if len(copies) == 1 else \
            _boolean(copies[0], copies[1:], False, _many(src), (_volume(src) or 0.0) * (len(copies) - 1))
        return _in_own_frame(f, _finished(f, shape, copies[0], touches))
    base = _base(f)
    if not moved:
        return base.copy()
    many = _many(base)
    if is_solid(f):
        steps = [[False, [placed(base, pl) for pl in moved], (_volume(base) or 0.0) * len(moved)]]
    else:
        pairs = tools(f)
        if not pairs:
            raise ValueError("Click a feature (tree or view) to repeat")
        steps = []  # [cut, copies, their volume]: the originals in order, neighbours of one kind in one Boolean
        for o, t in pairs:
            cut = _subtractive(o)
            copies = [placed(t, pl) for pl in moved]
            if steps and steps[-1][0] == cut:
                steps[-1][1].extend(copies)
                steps[-1][2] += (_volume(t) or 0.0) * len(copies)
            else:
                steps.append([cut, copies, (_volume(t) or 0.0) * len(copies)])
    shape = base
    for cut, copies, vt in steps:
        shape, touches = _boolean(shape, copies, cut, many, vt)
    return _finished(f, shape, base, touches)


def _many(shape):
    from incad.core.extrude_solid import MANY_FACES
    return len(shape.Faces) > MANY_FACES


def _volume(shape):
    try:
        return sum(abs(s.Volume) for s in shape.Solids)
    except Exception:
        return None


def _boolean(shape, copies, cut, many, vt):
    """(result, touches): shape with all the copies (`vt` their volume) fused or cut in one Boolean, plain, then
    fuzzy. Where faces of the result touch without an edge (see `touching`: the kernel's weak spot, a fuse there
    lost half a disc in our Extrude) the result is kept only when its volume lies where it can (no more than all
    the copies add, no less than the part). Elsewhere it is kept as it comes, as FreeCAD's pattern did: its volume
    and validity checks took half the time of a pattern (10 x 10 holes: 0.1 s of 0.2); the quick check runs on OK
    (incad.core.safe)."""
    from incad.core.extrude_solid import FUZZY, VOL_REL
    known = []
    for fuzzy in (0.0,) + tuple(FUZZY):
        try:
            if cut:
                out = shape.cut(copies, fuzzy) if fuzzy else shape.cut(copies)
            else:
                out = shape.fuse(copies, fuzzy) if fuzzy else shape.fuse(copies)
        except Exception:
            continue
        if not out.Solids:
            if cut:
                raise ValueError("The copies cut the whole part away")
            continue
        if many or not touching(out):
            return out, False
        if not known:
            known.append(_volume(shape) or 0.0)
        vb = known[0]
        lo, hi = (vb - vt, vb) if cut else (vb, vb + vt)
        solids = [(s, abs(s.Volume)) for s in out.Solids]
        kept = [s for s, v in solids if v > 1e-12]
        vol = sum(v for _s, v in solids)
        if not kept or not lo - VOL_REL * max(vb, vt, 1.0) <= vol <= hi + VOL_REL * max(vb, vt, 1.0):
            continue
        return (Part.Compound(kept) if len(kept) < len(solids) else out), True
    raise ValueError("OpenCASCADE could not %s the copies soundly: try another spacing or count"
                     % ("cut" if cut else "join"))


def _finished(f, shape, base, touches):
    """One sound solid where the copies only touch (see extrude_solid._finished), refined when asked; the full
    check (with it everywhere 10 x 10 holes took 0.64 s, FreeCAD's pattern 0.19) only where faces touch."""
    from incad.core import extrude_solid
    many = _many(shape)
    refine = bool(getattr(f, "Refine", False))
    if refine and many:
        from incad.core import faceted
        if faceted.is_faceted(shape):  # an STL's triangles: Refine looked at 25 000 faces for a minute
            refine = False
    if many or not touches:
        if not refine or len(shape.Solids) != 1:
            return shape
        try:
            refined = shape.copy().removeSplitter()  # (a copy: see extrude_solid._refined)
            if len(refined.Solids) == 1:
                return refined
        except Exception:
            pass
        return shape
    known = []

    def base_faulty():
        if not known:
            known.append(extrude_solid._faulty(base))
        return known[0]
    shape, _sound = extrude_solid._finished(shape, refine, many, base_faulty)
    return shape


def touching(shape, tol=1e-6):
    """True when two faces of shape that have no edge in common lie within tol of each other: copies resting on
    one another along a line (discs spaced their diameter apart: the kernel put corners at the ends of that line,
    no edge along it), a disc touching the edges of the face it stands on. That is where the kernel's Boolean
    leaves faces that meet without an edge (valid to the quick check, faulty to the full one). Only faces whose
    boxes meet are measured; faces meeting at a corner only are counted too (the full check then decides)."""
    import numpy as np
    faces = shape.Faces
    if len(faces) < 2:
        return False
    boxes = np.array([(b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax) for b in (g.BoundBox for g in faces)])
    lo, hi = boxes[:, :3] - tol, boxes[:, 3:] + tol
    meet = np.all((lo[:, None, :] <= hi[None, :, :]) & (lo[None, :, :] <= hi[:, None, :]), axis=2)
    edges = {}
    for i, j in zip(*np.nonzero(np.triu(meet, 1))):
        for k in (i, j):
            if k not in edges:
                edges[k] = {e.hashCode() for e in faces[k].Edges}
        if edges[i] & edges[j]:
            continue
        try:
            if faces[i].distToShape(faces[j])[0] <= tol:
                return True
        except Exception:
            return True
    return False


def _in_own_frame(f, shape):
    """A shape in the source Solid's coordinates, in f's own Solid's (a PartDesign feature drops a shape's own
    placement: it is baked in)."""
    src = f.Source
    gs = src.getGlobalPlacement() if src is not None else App.Placement()
    body = f.getParentGeoFeatureGroup()
    gt = body.getGlobalPlacement() if body is not None else App.Placement()
    m = gt.inverse().multiply(gs).multiply(shape.Placement).toMatrix()
    shape = shape.copy()
    shape.Placement = App.Placement()
    shape.transformShape(m, True)
    return shape


def instance_at(f, point, tol):
    """The copy number (1 = the original) whose material or cut is nearest `point` (in the coordinates the
    pattern's directions are in), within `tol` mm, or None."""
    vertex = Part.Vertex(point)
    best, best_d = None, tol
    shapes = [source_shape(f)] if is_solid(f) else [t for _o, t in tools(f)]
    for n, where in enumerate(transforms(f), 1):
        for t in shapes:
            try:
                dist = placed(t, where).distToShape(vertex)[0]
            except Exception:
                continue
            if dist <= best_d:
                best, best_d = n, dist
    return best


# ----- Solid: the new Solids -----
def new_in_solid(doc, kind, name, result, source):
    """A Solid pattern's first step: Join at the end of `source`, else in a new Solid that follows `source`."""
    from incad.core import document
    if result == "join":
        f = new(doc, source, kind, name)
    else:
        target = document.new_solid(doc)
        f = target.newObject("PartDesign::FeaturePython", name or NAMES[kind])
        PatternFeature(f, kind)
        target.Tip = f
        f.Source = source
        document.show_like(f, source.Tip)
    f.PatternOf, f.Result = "solid", result
    if result == "each":
        f.Copy = 2  # the first copy until sync_each knows which ones are kept
        f.addProperty("App::PropertyString", GROUP, "InCad", "The pattern this copy belongs to")
        f.setPropertyStatus(GROUP, "Hidden")
        setattr(f, GROUP, f.Name)
    return f


def copy_settings(src, dst):
    """src's settings (values, links, formulas) onto dst: a FreeCAD pattern into ours, or one step of a New Solid per
    copy pattern into the others."""
    for prop in ("Originals",) + _LINKS:
        if prop in src.PropertiesList and prop in dst.PropertiesList:
            value = getattr(src, prop)
            if prop == "Originals":
                dst.Originals = list(value)
            elif value and value[0] is not None:
                sub = value[1] if isinstance(value[1], (list, tuple)) else [value[1]]
                setattr(dst, prop, (value[0], list(sub) or [""]))
    for prop in _VALUES:
        if prop in src.PropertiesList and prop in dst.PropertiesList:
            setattr(dst, prop, getattr(src, prop))
    if is_own(src):
        for prop in ("Source", "Excluded"):
            setattr(dst, prop, getattr(src, prop))
    if src.TypeId == "PartDesign::LinearPattern":  # Spacing mode: the length first to last copy (Extent's)
        for mode, offset, count, length in (("Mode", "Offset", "Occurrences", "Length"),
                                            ("Mode2", "Offset2", "Occurrences2", "Length2")):
            if getattr(src, mode, "Extent") == "Spacing":
                setattr(dst, length, getattr(src, offset).Value * max(int(getattr(src, count)) - 1, 0))
    if "InCadFit" in src.PropertiesList:
        if "InCadFit" not in dst.PropertiesList:
            dst.addProperty("App::PropertyString", "InCadFit", "InCad", "How the copies are spaced")
        dst.InCadFit = src.InCadFit
    own = set(dst.PropertiesList)
    exprs = {p.lstrip("."): e for p, e in src.ExpressionEngine}
    for p, _e in dst.ExpressionEngine:
        if p.lstrip(".") not in exprs:
            dst.setExpression(p.lstrip("."), None)
    for prop, expr in exprs.items():
        if prop in own and prop not in ("Offset", "Offset2"):
            dst.setExpression(prop, expr)


def sync_each(doc, lead):
    """New Solid per copy: one step (in a Solid of its own) for every kept copy, the lead's settings on each. Steps
    of copies gone are removed with their Solids. Returns the steps."""
    from incad.core import document
    _settle_formulas(lead)  # how many copies there are, without building one that is no longer kept
    want = [n for n in kept(lead) if n > 1]
    if not want:
        raise ValueError("The pattern makes no copy: a count of 2 or more, or fewer excluded")
    steps = group_of(lead)
    others = [s for s in steps if s is not lead]
    lead.Copy = want[0]
    for n in want[1:]:
        s = next((o for o in others if o.Copy == n), None)
        if s is None:
            s = next((o for o in others if o.Copy not in want), None)
        if s is None:
            body = document.new_solid(doc)
            s = body.newObject("PartDesign::FeaturePython", lead.Name)
            PatternFeature(s, kind_of(lead))
            s.addProperty("App::PropertyString", GROUP, "InCad", "The pattern this copy belongs to")
            s.setPropertyStatus(GROUP, "Hidden")
            setattr(s, GROUP, getattr(lead, GROUP))
            s.PatternOf, s.Result = "solid", "each"
            body.Tip = s
            document.show_like(s, lead)
        else:
            others.remove(s)
        copy_settings(lead, s)
        s.Copy = n
        s.Label = lead.Label
    for s in others:
        _drop_with_solid(doc, s)
    return group_of(lead)


def _settle_formulas(f):
    """The counts' and lengths' formulas (model parameters) worked out into their properties now."""
    for path, expr in f.ExpressionEngine:
        prop = path.lstrip(".")
        if prop not in _VALUES:
            continue
        try:
            value = f.evalExpression(expr)
            value = getattr(value, "Value", value)
            setattr(f, prop, int(round(value)) if prop.startswith("Occurrences") else value)
        except Exception:
            pass


def _drop_with_solid(doc, f):
    body = f.getParentGeoFeatureGroup()
    if body is not None and len([o for o in body.Group if o.isDerivedFrom("PartDesign::Feature")]) == 1:
        from incad.features.solid_mirror import _drop_body
        _drop_body(doc, body)
        return
    if body is not None:
        if body.Tip is f:
            body.Tip = f.BaseFeature
        body.removeObject(f)
    doc.removeObject(f.Name)


# ----- a FreeCAD pattern of an older file becomes ours -----
def convert(doc, old):
    """Our pattern in the place of FreeCAD's `old` (same name label, values, formulas, picks; its copies excluded
    taken over from its step, which goes): the features after it are built on the new one, sketches on its faces
    (or its step's) stay on the same faces. Returns the new pattern."""
    from incad.core import document
    from incad.features import pattern_exclude, revolve
    kind = kind_of(old)
    body = old.getParentGeoFeatureGroup()
    step = pattern_exclude.of(old) if kind != "mirror" else None
    excluded = pattern_exclude.excluded(old) if step is not None else []
    if step is not None:
        pattern_exclude.release(old)  # its counts (values and parameters) back on it
    gone = [(o, o.Shape.copy()) for o in (step, old) if o is not None]
    label, visible = old.Label, old.Visibility
    was_tip = body.Tip in (old, step)
    f = doc.addObject("PartDesign::FeaturePython", NAMES[kind])
    PatternFeature(f, kind)
    body.insertObject(f, step or old, True)  # right after it, then in its place
    f.BaseFeature = old.BaseFeature
    copy_settings(old, f)
    f.Excluded = excluded
    for h in [o for o in body.Group if getattr(o, revolve.HELPER, None) == old.Name]:  # a two-point axis
        setattr(h, revolve.HELPER, f.Name)
    users = [o for g, _s in gone for o in g.InList if o is not body and o is not f and o not in (old, step)]
    for o in users:
        base = getattr(o, "BaseFeature", None)
        if base is not None and base in (old, step):
            o.BaseFeature = f
    if was_tip:
        body.Tip = f
    doc.recompute([f])
    for g, shape in gone:
        for o in users:
            _relink(o, g, f, shape)
    document.show_like(f, old)
    for g, _s in gone:
        body.removeObject(g)
        doc.removeObject(g.Name)
    f.Label = label
    f.Visibility = visible
    return f


def _relink(obj, old, f, old_shape):
    """obj's links to `old` (a sketch on one of its faces, a fillet's edges) moved to `f`, on the same elements."""
    from incad.core import document

    def moved(sub):
        return (document.remap_sub(old_shape, f.Shape, sub) or sub) if sub else sub
    for prop in obj.PropertiesList:
        if prop in ("BaseFeature", "ExpressionEngine"):
            continue
        try:
            value = getattr(obj, prop)
        except Exception:
            continue
        if value is old:
            setattr(obj, prop, f)
        elif isinstance(value, list) and value and any(_names(v, old) for v in value):
            out = []
            for v in value:
                if v is old:
                    out.append(f)
                elif isinstance(v, tuple) and v and v[0] is old:
                    subs = v[1] if isinstance(v[1], (list, tuple)) else [v[1]]
                    many = isinstance(v[1], (list, tuple))
                    out.append((f, [moved(s) for s in subs]) if many else (f, moved(subs[0])))
                else:
                    out.append(v)
            try:
                setattr(obj, prop, out)
            except Exception:
                pass
        elif isinstance(value, tuple) and value and value[0] is old:
            subs = value[1] if isinstance(value[1], (list, tuple)) else [value[1]]
            try:
                setattr(obj, prop, (f, [moved(s) for s in subs]))
            except Exception:
                pass
    for path, expr in list(getattr(obj, "ExpressionEngine", []) or []):
        if old.Name + "." in expr:
            obj.setExpression(path.lstrip("."), expr.replace(old.Name + ".", f.Name + "."))


def _names(v, old):
    return v is old or (isinstance(v, tuple) and v and v[0] is old)
