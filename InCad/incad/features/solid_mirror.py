"""Mirror Solid (Inventor's Mirror ▸ Solid, New solid): a mirrored copy of a whole Solid, as a Solid of its own,
that follows its original — every change to the original, features added to it later, shows in the copy on the next
recompute — until Break Mirror Link (right-click its row) keeps the copy as it is (the user: "the exact way as in
the sketch"). The copy is the first step of its new Solid (a PartDesign::FeaturePython marked InCadMirrorSolid), so
its row sits in the part's history and features can be added on it. Result Join / Cut instead mirrors the Solid
into itself (a half made whole, a shape cut by its mirror image): a step at the end of that Solid that mirrors what
comes before it.

Break Mirror Link on a feature Mirror (PartDesign::Mirrored) keeps the material it added and removed as it is
now (a step of its own in its place); the features before it still change the part under it."""
import FreeCAD as App

from incad.features.common import Field, format_picks, parse_picks, resolve

TITLE = "Mirror Solid"
ICON = "mirror_solid"
TYPES = ("PartDesign::FeaturePython",)
NEEDS_PROFILE = False
PROPS = {}
MARK = "InCadMirrorSolid"  # "linked" or "frozen"
FROZEN_MARK = "InCadMirrorFrozen"  # on a feature Mirror kept as it was
_PLANES = ("App::Plane", "PartDesign::Plane")

RESULTS = (("new", "New Solid"), ("join", "Join"), ("cut", "Cut"))
# what is mirrored: the whole Solid, or only its part on one side of the plane (the user: a part crossing the plane
# mirrored to the other side — with Join it becomes symmetric, that side's own material replaced)
SIDES = (("whole", "Whole"), ("front", "One side"), ("back", "Other side"))
RECREATE_ON = ("result",)  # a new Solid or a step in the original: made again; fixed once made

FIELDS = (
    Field("solid", "Solid", "pick", pick="body"),
    Field("plane", "Mirror plane", "pick", pick="plane"),
    Field("result", "Result", "choice", RESULTS, "new"),
    Field("side", "Mirror", "choice", SIDES, "whole"),
)


def side_of(f):
    return getattr(f, "Side", "whole") or "whole"


def half(shape, point, normal, side):
    """The part of shape on one side of the plane (front: where the normal points); the whole shape for 'whole'."""
    import Part
    if side not in ("front", "back"):
        return shape
    bb = shape.BoundBox
    size = 2 * (bb.DiagonalLength + (bb.Center - point).Length) + 10
    box = Part.makeBox(2 * size, 2 * size, size, App.Vector(-size, -size, 0 if side == "front" else -size))
    box.Placement = App.Placement(point, App.Rotation(App.Vector(0, 0, 1), normal))
    out = shape.common(box)
    if not out.Solids:
        raise ValueError("Nothing of the Solid lies on that side of the plane: pick the other side")
    return out.Solids[0] if len(out.Solids) == 1 else out


def result_of(f):
    return getattr(f, "Result", "new") or "new"


def claims(feature):
    """A linked mirrored Solid (a frozen one is a plain step: nothing to edit)."""
    return getattr(feature, MARK, None) == "linked"


def is_mirror_solid(feature):
    return MARK in getattr(feature, "PropertiesList", ())


def linked_mirror(obj):
    """What Break Mirror Link frees for a row: a linked mirrored Solid (its row, or its Solid's), or a feature
    Mirror; else None."""
    if obj is None:
        return None
    if obj.TypeId == "PartDesign::Body":
        return next((o for o in obj.Group if claims(o) and result_of(o) == "new"), None)  # the copy's Solid
    return obj if claims(obj) or _feature_mirror(obj) else None


def _feature_mirror(obj):
    """A feature Mirror: FreeCAD's (an older file) or ProstoCAD's own Pattern of kind mirror."""
    from incad.features import own_pattern
    return own_pattern.kind_of(obj) == "mirror" and not own_pattern.is_solid(obj)


def is_linked(obj):
    return linked_mirror(obj) is not None


def _body_of(obj):
    if obj is None:
        return None
    if obj.TypeId == "PartDesign::Body":
        return obj
    parent = obj.getParentGeoFeatureGroup()
    return parent if parent is not None and parent.TypeId == "PartDesign::Body" else None


def plane_of(link):
    """(point, normal) of the mirror plane in the part's frame: an origin or work plane, or a flat face."""
    obj, subs = link
    sub = subs[0] if subs else ""
    if not sub:
        pl = obj.getGlobalPlacement()
        return pl.Base, pl.Rotation.multVec(App.Vector(0, 0, 1))
    face = obj.Shape.getElement(sub)  # in its Solid's own frame
    if face.Surface.TypeId != "Part::GeomPlane":
        raise ValueError("The mirror plane must be flat: an origin or work plane, or a flat face")
    body = _body_of(obj)
    pl = body.getGlobalPlacement() if body is not None else App.Placement()
    return pl.multVec(face.Surface.Position), pl.Rotation.multVec(face.Surface.Axis)


def mirrored(source, link, side="whole"):
    """The source mirrored, in the part's frame: a Solid, or one step of it (the Solid as it is at that step, see
    stop_before)."""
    shape = source.Shape
    if shape.isNull() or not shape.Solids:
        raise ValueError("%s has no solid to mirror yet" % source.Label)
    shape = shape.copy()
    if source.TypeId != "PartDesign::Body":  # a step's shape is in its Solid's own frame
        body = _body_of(source)
        if body is not None:
            shape.Placement = body.getGlobalPlacement().multiply(shape.Placement)
    base, normal = plane_of(link)
    return half(shape, base, normal, side).mirror(base, normal)


def stop_before(base, step):
    """A Boolean in `base` takes a mirrored copy of `base` as its tool (the user: the mirror is a new part, why can't
    I cut or join it): each such copy follows `base` only up to the step before the Boolean — it can't mirror a
    shape that is made from itself. Returns the copies changed."""
    out = []
    for m in copies(base.Document):
        if m.Source is base and step is not None:
            m.Source = step
            out.append(m)
    return out


def _into(obj, shape):
    """The part-frame shape in obj's Solid's own frame (a PartDesign feature drops a shape's own placement)."""
    body = obj.getParentGeoFeatureGroup()
    place = body.getGlobalPlacement().inverse() if body is not None else App.Placement()
    m = place.multiply(shape.Placement).toMatrix()
    shape.Placement = App.Placement()
    shape.transformShape(m, True)
    return shape


class MirrorSolidFeature:
    """The proxy. FreeCAD saves its module path (incad.features.solid_mirror) and class name in the file."""
    def __init__(self, obj):
        for prop, kind in (("Source", "App::PropertyLinkGlobal"), ("Plane", "App::PropertyLinkSubGlobal")):
            if prop not in obj.PropertiesList:
                obj.addProperty(kind, prop, "Mirror", "")
        if "Frozen" not in obj.PropertiesList:
            obj.addProperty("Part::PropertyPartShape", "Frozen", "Mirror", "The copy kept by Break Mirror Link")
            obj.setPropertyStatus("Frozen", "Hidden")
        if MARK not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", MARK, "InCad", "linked: follows its Solid; frozen: kept as it is")
            obj.setPropertyStatus(MARK, "Hidden")
        if "Result" not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", "Result", "Mirror", "new: a Solid of its own; join / cut: into itself")
        if "Side" not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", "Side", "Mirror", "whole, or front / back: only that side of the plane")
        obj.Proxy = self

    def execute(self, obj):
        if result_of(obj) in ("join", "cut"):
            obj.Shape = _into_itself(obj)
            return
        if getattr(obj, MARK, "") == "frozen" or obj.Source is None:
            made = obj.Frozen.copy()
        else:
            made = _into(obj, mirrored(obj.Source, obj.Plane, side_of(obj)))
        base = obj.BaseFeature
        if base is not None and not base.Shape.isNull() and base.Shape.Solids:
            made = base.Shape.fuse(made)
        if len(made.Solids) == 1:
            made = sound(made.Solids[0])
        if hasattr(obj, "AddSubShape"):
            try:
                obj.AddSubShape = made
            except Exception:
                pass
        obj.Shape = made

    def dumps(self):
        return None

    def loads(self, state):
        return None


def _into_itself(obj):
    """Join / Cut: what comes before the step, with its mirror image joined or cut away (in the Solid's own frame)."""
    base = obj.BaseFeature
    if base is None or base.Shape.isNull() or not base.Shape.Solids:
        raise ValueError("Nothing to mirror yet: the Solid has no shape before this step")
    shape = base.Shape.copy()
    body = obj.getParentGeoFeatureGroup()
    inv = body.getGlobalPlacement().inverse() if body is not None else App.Placement()
    point, normal = plane_of(obj.Plane)
    point, normal = inv.multVec(point), inv.Rotation.multVec(normal)
    kept = half(shape, point, normal, side_of(obj))
    image = kept.mirror(point, normal)
    if result_of(obj) == "join":  # one side and its image: the other side's own material is replaced
        return _one_piece(kept, image, "join")
    return _one_piece(shape, image, result_of(obj))


_WAYS = (0.0, 1e-5, 1e-4, 1e-3, 3e-3, 1e-2)  # plain, then fuzzy (0.01 mm at most): an image crossing the Solid
# at a few places (the user's sweep about YZ) fell apart into four pieces plainly, one clean solid fuzzily


def sound(solid):
    """A joined solid with faces that only touch (a disc tangent to the plane meets its image along a line) made to
    meet on a common edge, as our Extrude does it: the kernel called it valid, the full check faulty (faces crossing
    themselves) and a later Shell or Fillet failed there. The solid as it was when that doesn't help."""
    try:
        from incad.core.extrude_solid import _faulty, _glued
        return (_glued(solid) or solid) if _faulty(solid) else solid  # (_glued gives None when it can't)
    except Exception:
        return solid


def _one_piece(shape, image, result):
    """shape joined with / cut by its mirror image, as one valid solid: the first way that gives one."""
    pieces = 0
    for fuzz in _WAYS:
        try:
            if result == "join":
                out = shape.fuse([image], fuzz) if fuzz else shape.fuse(image)
            else:
                out = shape.cut([image], fuzz) if fuzz else shape.cut(image)
        except Exception:
            continue
        try:
            clean = out.removeSplitter()  # a fuzzy result can fall apart again here: kept only when it doesn't
            if len(clean.Solids) == 1 and clean.isValid():
                out = clean
        except Exception:
            pass
        if not out.Solids:
            if result == "cut":
                raise ValueError("Cut by its mirror image nothing is left: the Solid lies inside its image")
            continue
        if len(out.Solids) == 1 and out.isValid():
            return sound(out.Solids[0])
        pieces = len(out.Solids)
    if result == "join" and shape.distToShape(image)[0] > 1e-6:
        raise ValueError("The Solid and its mirror image do not touch: pick a plane on (or through) the Solid, "
                         "or Result: New Solid")
    raise ValueError("%s by its mirror image the Solid falls apart into %d pieces: a Solid stays one piece — "
                     "pick another plane, or Result: New Solid" % ("Joined" if result == "join" else "Cut",
                                                                 pieces or 2))


class FrozenMirrorFeature:
    """A feature Mirror kept as it was: the material it added and removed, on whatever comes before it."""
    def __init__(self, obj):
        for prop in ("Added", "Removed"):
            if prop not in obj.PropertiesList:
                obj.addProperty("Part::PropertyPartShape", prop, "Mirror", "")
                obj.setPropertyStatus(prop, "Hidden")
        if FROZEN_MARK not in obj.PropertiesList:
            obj.addProperty("App::PropertyBool", FROZEN_MARK, "InCad", "A Mirror kept as it was")
            obj.setPropertyStatus(FROZEN_MARK, "Hidden")
        setattr(obj, FROZEN_MARK, True)
        obj.Proxy = self

    def execute(self, obj):
        base = obj.BaseFeature
        shape = base.Shape.copy() if base is not None and not base.Shape.isNull() else None
        if not obj.Added.isNull() and obj.Added.Solids:
            shape = shape.fuse(obj.Added) if shape is not None else obj.Added.copy()
        if shape is not None and not obj.Removed.isNull() and obj.Removed.Solids:
            shape = shape.cut(obj.Removed)
        if shape is None:
            return
        shape = shape.removeSplitter()
        if len(shape.Solids) == 1:
            shape = sound(shape.Solids[0])
        obj.Shape = shape

    def dumps(self):
        return None

    def loads(self, state):
        return None


# ----- the dialog -----
def defaults(doc, body, profile=None):
    """The part's only Solid is the one mirrored; with several the dialog waits for a click on one (the user: which
    one, when a few parts are on the screen), then for the plane — as in the sketch: the shapes, then the line."""
    from incad.core import document
    full = [b for b in document.bodies(doc) if not b.Shape.isNull() and b.Shape.Solids]
    return {"solid": full[0].Name if len(full) == 1 else "", "plane": "", "result": "new", "side": "whole"}


def preselect(pairs):
    """A Solid selected (its row, or a click on it) is the one mirrored; a plane selected with it is the mirror."""
    out = {}
    planes = [(o, s) for o, s in pairs if not s and o.TypeId in _PLANES]
    for o, s in pairs:
        if (o, s) in planes:
            continue
        b = _body_of(resolve(o, s)[0])
        if b is not None:
            out["solid"] = b.Name
    if planes:
        out["plane"] = format_picks(planes[-1:])
    return out


def _source(doc, values):
    picks = parse_picks(doc, values.get("solid"))
    b = _body_of(resolve(*picks[0])[0]) if picks else None
    if b is None:
        raise ValueError("Pick the Solid to mirror")
    return b


def _plane_link(doc, values):
    picks = parse_picks(doc, values.get("plane"))
    if not picks:
        raise ValueError("Pick the mirror plane: an origin or work plane, or a flat face")
    obj, sub = picks[0]
    if sub:
        obj, sub = resolve(obj, sub)
    return (obj, [sub])


def _check(f, source, link):
    own = f.getParentGeoFeatureGroup()
    if result_of(f) != "new":
        if link[0] is f:
            raise ValueError("The mirror plane can't be a face of the mirror itself")
        return
    if source is own:
        raise ValueError("A Solid can't be mirrored into itself: pick another Solid")
    if _body_of(link[0]) is own and link[0].TypeId not in _PLANES:
        raise ValueError("The mirror plane can't be a face of the copy itself")


def create(doc, body, profile, values):
    from incad.core import document
    source, link = _source(doc, values), _plane_link(doc, values)
    if values.get("result") in ("join", "cut"):
        return _create_into_itself(doc, source, link, values["result"], values)
    target = document.new_solid(doc)
    try:
        f = target.newObject("PartDesign::FeaturePython", "MirrorSolid")
        f.Label = TITLE
        MirrorSolidFeature(f)
        setattr(f, MARK, "linked")
        f.Result = "new"
        f.Side = values.get("side") or "whole"
        _check(f, source, link)
        f.Source, f.Plane = source, link
        target.Tip = f
        document.show_like(f, source.Tip)
        doc.recompute()
        if "Invalid" in f.State:
            raise ValueError(f.getStatusString() or "The mirror could not be made")
    except Exception:
        _drop_body(doc, target)
        raise
    return f


def _create_into_itself(doc, source, link, result, values):
    from incad.core import document
    tip = source.Tip
    f = source.newObject("PartDesign::FeaturePython", "MirrorSolid")
    f.Label = TITLE
    MirrorSolidFeature(f)
    setattr(f, MARK, "linked")
    f.Result = result
    f.Side = values.get("side") or "whole"
    try:
        _check(f, source, link)
        f.BaseFeature = tip
        f.Plane = link
        source.Tip = f
        document.show_like(f, tip)
        doc.recompute()
        if "Invalid" in f.State:
            raise ValueError(f.getStatusString() or "The mirror could not be made")
        document.show_result(source)
    except Exception:
        source.removeObject(f)
        doc.removeObject(f.Name)
        source.Tip = tip
        doc.recompute()
        raise
    return f


def _drop_body(doc, body):
    """The new Solid goes again (the mirror could not be made in it)."""
    origin = getattr(body, "Origin", None)
    for o in list(body.Group):
        if o is not origin and o not in getattr(origin, "OriginFeatures", ()) and doc.getObject(o.Name) is not None:
            body.removeObject(o)
            doc.removeObject(o.Name)
    doc.removeObject(body.Name)
    if origin is not None and doc.getObject(origin.Name) is not None:
        for o in list(origin.OriginFeatures):
            if doc.getObject(o.Name) is not None:
                doc.removeObject(o.Name)
        doc.removeObject(origin.Name)


def read(doc, f):
    sub = f.Plane[1][0] if f.Plane and f.Plane[1] else ""
    plane = "" if not f.Plane else (format_picks([(f.Plane[0], sub)]))
    result = result_of(f)
    solid = _body_of(f.Source) if result == "new" else f.getParentGeoFeatureGroup()
    return {"solid": solid.Name if solid is not None else "", "plane": plane, "result": result, "side": side_of(f)}


def update(doc, f, values):
    MirrorSolidFeature(f)  # a mirror made before a setting existed gets it
    source, link = _source(doc, values), _plane_link(doc, values)
    _check(f, source, link)
    if result_of(f) == "new" and f.Source is not source and _body_of(f.Source) is not source:
        f.Source = source  # the same Solid picked again keeps the step it follows up to (stop_before)
    f.Plane = link
    if side_of(f) != (values.get("side") or "whole"):
        f.Side = values.get("side") or "whole"
    doc.recompute()
    return f


def summary(doc, f):
    return {}


# ----- Break Mirror Link -----
def break_link(f):
    """The copy keeps its shape and stops following (a mirrored Solid), or a feature Mirror keeps the material it
    added and removed. Returns the step that stands for it now."""
    f = linked_mirror(f) or f
    doc = f.Document
    if claims(f) and result_of(f) != "new":  # into itself: the material it added or cut is kept
        return _freeze_feature_mirror(f)
    if claims(f):
        f.Frozen = f.Shape.copy()
        setattr(f, MARK, "frozen")
        f.Source, f.Plane = None, None
        f.Label = "%s (unlinked)" % f.Label
        doc.recompute()
        return f
    if not _feature_mirror(f):
        raise ValueError("%s is not a mirror" % f.Label)
    return _freeze_feature_mirror(f)


def _freeze_feature_mirror(f):
    doc = f.Document
    body = f.getParentGeoFeatureGroup()
    base = f.BaseFeature
    if "Invalid" in f.State or f.Shape.isNull():
        raise ValueError("%s is broken: fix it before breaking its link" % f.Label)
    before = base.Shape if base is not None and not base.Shape.isNull() else None
    added = f.Shape.cut(before) if before is not None else f.Shape.copy()
    removed = before.cut(f.Shape) if before is not None else None
    label, visible = f.Label, f.Visibility
    nf = doc.addObject("PartDesign::FeaturePython", "MirrorKept")
    FrozenMirrorFeature(nf)
    nf.Added = added
    if removed is not None:
        nf.Removed = removed
    body.insertObject(nf, f, True)
    nf.BaseFeature = base
    for o in body.Group:
        if o is not nf and getattr(o, "BaseFeature", None) is f:
            o.BaseFeature = nf
    if body.Tip is f:
        body.Tip = nf
    from incad.core import document
    document.show_like(nf, f)
    body.removeObject(f)
    doc.removeObject(f.Name)
    nf.Label = "%s (unlinked)" % label
    doc.recompute()
    if visible:
        nf.Visibility = True
    return nf


def users_of(body):
    """The linked mirrored Solids made from `body`."""
    return [o for o in copies(body.Document) if o.Source is body or _body_of(o.Source) is body]


def copies(doc):
    """The linked mirrored Solids of a part (New Solid: they follow another Solid)."""
    return [o for o in doc.Objects if claims(o) and result_of(o) == "new"]
