"""Patterns ▸ Excluded (the user: click the copies a pattern doesn't need, as Inventor's Suppress occurrence).
FreeCAD 1.1's LinearPattern / PolarPattern can't leave a copy out, so a pattern with excluded copies gets a step
right after it (a PartDesign::FeaturePython, InCadHelperOf the pattern, as Hole ▸ Chamfer) that builds the part
again from the shape before the pattern with only the copies kept: each original's tool (AddSubShape) moved to
every kept place, fused or cut in the originals' order, as FreeCAD's pattern does. The pattern keeps its fields; while
the step is there it holds its count (the real one, with its parameter, sits on the step: `hold` / `release`) at a
single copy, so FreeCAD does not build every copy only for the step to build them again (the user: faster). Copies are numbered from 1, the original (1 is the original; with two
directions the first direction counts fastest)."""
import FreeCAD as App
import Part

HELPER = "InCadHelperOf"


def _line_of(link):
    """(point, unit direction) of a pattern's direction or axis link, in the body's coordinates."""
    obj, subs = link
    sub = subs[0] if subs else ""
    if sub.startswith("Edge"):
        e = obj.getSubObject(sub)
        c = e.Curve
        if hasattr(c, "Axis") and hasattr(c, "Center"):  # a circle: round its centre
            p, d = App.Vector(c.Center), App.Vector(c.Axis)
        else:
            p, d = App.Vector(getattr(c, "Location", e.valueAt(e.FirstParameter))), App.Vector(c.Direction)
    elif sub.startswith("Face"):
        s = obj.getSubObject(sub).Surface
        p, d = App.Vector(getattr(s, "Center", getattr(s, "Position", App.Vector()))), App.Vector(s.Axis)
    elif sub in ("H_Axis", "V_Axis"):
        p = App.Vector(obj.Placement.Base)
        d = obj.Placement.Rotation.multVec(App.Vector(1, 0, 0) if sub == "H_Axis" else App.Vector(0, 1, 0))
    else:
        shape = getattr(obj, "Shape", None)
        edges = shape.Edges if shape is not None and not shape.isNull() else []
        ends = [v.Point for v in edges[0].Vertexes] if len(edges) == 1 else []
        if len(ends) == 2 and (ends[1] - ends[0]).Length > 1e-9:  # a work axis: where it is in the part (its ends;
            p, d = App.Vector(ends[0]), ends[1] - ends[0]           # its curve alone is in its own frame)
        elif len(edges) == 1 and hasattr(edges[0].Curve, "Direction"):  # an origin axis: infinite
            c = edges[0].Curve
            p, d = App.Vector(getattr(c, "Location", App.Vector())), App.Vector(c.Direction)
        elif obj.TypeId == "PartDesign::Line":  # a work axis (its shape may be empty) runs along its own Z
            p, d = App.Vector(obj.Placement.Base), obj.Placement.Rotation.multVec(App.Vector(0, 0, 1))
        else:  # an origin axis runs along its own X
            p, d = App.Vector(obj.Placement.Base), obj.Placement.Rotation.multVec(App.Vector(1, 0, 0))
    d.normalize()
    return p, d


def _steps(occurrences, mode, length, offset, closed=False):
    """The distance (or angle) between two neighbours."""
    if mode == "Spacing":
        return offset
    if occurrences < 2:
        return 0.0
    return length / occurrences if closed else length / (occurrences - 1)


COUNTS = ("Occurrences", "Occurrences2")


def holder(f):
    """Where the pattern's counts are: its step for excluded copies while there is one, else the pattern."""
    g = of(f)
    return g if g is not None and "Occurrences" in g.PropertiesList else f


def count(f, prop):
    return int(getattr(holder(f), prop))


def _expression_of(obj, prop):
    return next((e for p, e in obj.ExpressionEngine if p.lstrip(".") == prop), None)


def hold(f, g):
    """The pattern's counts (values and parameters) go to the step; the pattern builds a single copy."""
    for prop in (p for p in COUNTS if p in f.PropertiesList):
        if prop not in g.PropertiesList:
            g.addProperty("App::PropertyInteger", prop, "Pattern", "The pattern's count, held here")
        expr = _expression_of(f, prop)
        setattr(g, prop, int(getattr(f, prop)))
        g.setExpression(prop, expr)
        f.setExpression(prop, None)
        if getattr(f, prop) != 1:
            setattr(f, prop, 1)


def release(f):
    """The counts back on the pattern (before its dialog writes them: the same parameters are reused)."""
    g = of(f)
    if g is None:
        return
    for prop in (p for p in COUNTS if p in g.PropertiesList and p in f.PropertiesList):
        expr = _expression_of(g, prop)
        setattr(f, prop, int(getattr(g, prop)))
        f.setExpression(prop, expr)
        g.setExpression(prop, None)


def _still_held(f):
    """The pattern builds a single copy, its counts held by its step (see `hold`)."""
    props = [p for p in COUNTS if p in f.PropertiesList]
    return bool(props) and all(int(getattr(f, p)) == 1 and _expression_of(f, p) is None for p in props)


def transforms(f):
    """[Placement] of every copy in the body's coordinates, the original (identity) first. FreeCAD's patterns and
    ours (incad.features.own_pattern: Extent mode only, no Offset)."""
    from incad.features.own_pattern import kind_of
    kind = kind_of(f)

    def offset(prop):
        return getattr(f, prop).Value if prop in f.PropertiesList else 0.0
    if kind == "rectangular":
        _p, d1 = _line_of(f.Direction)
        if f.Reversed:
            d1 = -d1
        n1 = count(f, "Occurrences")
        s1 = _steps(n1, getattr(f, "Mode", "Extent"), f.Length.Value, offset("Offset"))
        n2, s2, d2 = 1, 0.0, App.Vector()
        if count(f, "Occurrences2") > 1 and f.Direction2:
            _p, d2 = _line_of(f.Direction2)
            if f.Reversed2:
                d2 = -d2
            n2 = count(f, "Occurrences2")
            s2 = _steps(n2, getattr(f, "Mode2", "Extent"), f.Length2.Value, offset("Offset2"))
        return [App.Placement(d1 * (s1 * i) + d2 * (s2 * j), App.Rotation())
                for j in range(n2) for i in range(n1)]
    if kind == "circular":
        p, d = _line_of(f.Axis)
        if f.Reversed:
            d = -d
        n = count(f, "Occurrences")
        full = abs(f.Angle.Value - 360.0) < 1e-9
        step = _steps(n, getattr(f, "Mode", "Extent"), f.Angle.Value, offset("Offset"), closed=full)
        return [App.Placement(App.Vector(), App.Rotation(d, step * i), p) for i in range(n)]
    return [App.Placement()]


def _subtractive(o):
    """A cut: FreeCAD's Pocket, Groove, Hole, Subtractive… and ProstoCAD's own Extrude / Revolve cut
    (FeatureSubtractivePython, whose getAddSubType said Additive)."""
    if o.TypeId in ("PartDesign::Pocket", "PartDesign::Groove", "PartDesign::Hole") or "Subtractive" in o.TypeId:
        return True
    try:
        return o.getAddSubType() == "Subtractive"
    except Exception:
        return False


def _tools(f):
    from incad.features import own_pattern
    from incad.features.common import tool_shape
    if own_pattern.is_own(f):
        return own_pattern.tools(f)  # a pattern among its originals: each of that pattern's copies
    out = []
    for o in f.Originals:
        try:
            t = tool_shape(o)
        except Exception:
            continue
        if not t.isNull():
            out.append((o, t))
    return out


def instance_at(f, point, tol):
    """The copy number (1 = the original) whose material or cut is nearest `point` (body coordinates), within
    `tol` mm, or None."""
    vertex = Part.Vertex(point)
    best, best_d = None, tol
    tools = _tools(f)
    for n, pl in enumerate(transforms(f), 1):
        for _o, t in tools:
            moved = t.copy()
            moved.Placement = pl.multiply(moved.Placement)
            try:
                dist = moved.distToShape(vertex)[0]
            except Exception:
                continue
            if dist <= best_d:
                best, best_d = n, dist
    return best


def build(f, excluded):
    """The part after pattern `f` with the copies numbered in `excluded` left out."""
    base = f.BaseFeature.Shape if f.BaseFeature is not None else Part.Shape()
    if base.isNull():
        raise ValueError("The pattern has no part to work on")
    # the original (1) is in the shape before the pattern already: only the other kept copies are added
    places = [pl for n, pl in enumerate(transforms(f), 1) if n > 1 and n not in set(excluded)]
    shape = base.copy()
    for o, t in _tools(f):
        copies = []
        for pl in places:
            moved = t.copy()
            moved.Placement = pl.multiply(moved.Placement)
            copies.append(moved)
        if not copies:
            continue
        shape = shape.cut(copies) if _subtractive(o) else shape.fuse(copies)
    if getattr(f, "Refine", False):
        shape = shape.removeSplitter()
    if len(shape.Solids) == 1:
        from incad.features.solid_mirror import sound
        shape = sound(shape.Solids[0])  # copies touching each other along a line: one sound solid
    return shape


class PatternExcludeFeature:
    """The proxy. FreeCAD saves its module path (incad.features.pattern_exclude) and class name in the file."""
    def __init__(self, obj):
        for prop, kind in (("Pattern", "App::PropertyLink"), ("Excluded", "App::PropertyIntegerList")):
            if prop not in obj.PropertiesList:
                obj.addProperty(kind, prop, "Pattern", "")
        obj.Proxy = self

    def execute(self, obj):
        f = obj.Pattern
        if f is None or obj.BaseFeature is None:
            return
        obj.Shape = build(f, list(obj.Excluded))

    def dumps(self):
        return None

    def loads(self, state):
        return None


def of(f):
    body = f.getParentGeoFeatureGroup()
    return next((o for o in (body.Group if body is not None else [])
                 if getattr(o, HELPER, None) == f.Name and hasattr(o, "Excluded")), None)


def excluded(f):
    """The copies left out that the pattern still has."""
    g = of(f)
    total = len(transforms(f))
    return sorted(n for n in set(g.Excluded) if n <= total) if g is not None else []


def sync(doc, f, numbers):
    """Make, update or remove the pattern's step for the excluded copies (the original can't be one). Called right
    after the dialog wrote the pattern (its counts on it, see `release`)."""
    numbers = sorted({int(n) for n in numbers if int(n) > 1})
    body = f.getParentGeoFeatureGroup()
    g = of(f)
    if not numbers:
        if g is not None:
            if _still_held(f):  # counts still held on the step (not through the dialog): back on the pattern.
                release(f)      # After the dialog wrote new ones, a second release put the old count back
            if body.Tip is g:   # and dropped its parameter (Count 5 -> 2 came out 3, then 5 again)
                body.Tip = f
            for o in body.Group:
                if getattr(o, "BaseFeature", None) is g:
                    o.BaseFeature = f
            body.removeObject(g)
            doc.removeObject(g.Name)
        return None
    # numbers past the pattern's end are kept as given (a count set by a formula is computed only on the next
    # recompute): the build and `excluded` skip them
    if g is None:
        g = doc.addObject("PartDesign::FeaturePython", "PatternExcluded")
        PatternExcludeFeature(g)
        from incad.core import document
        document.show_like(g, f)
        g.addProperty("App::PropertyString", HELPER, "InCad", "The pattern whose copies this leaves out")
        g.setPropertyStatus(HELPER, "Hidden")
        setattr(g, HELPER, f.Name)
        after = [o for o in body.Group if getattr(o, "BaseFeature", None) is f]
        was_tip = body.Tip is f
        body.insertObject(g, f, True)  # right after the pattern
        g.BaseFeature = f
        for o in after:
            o.BaseFeature = g
        if was_tip:
            body.Tip = g
    g.Pattern = f
    if list(g.Excluded) != numbers:
        g.Excluded = numbers
    hold(f, g)
    return g
