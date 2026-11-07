"""Extrude (Inventor): ProstoCAD's own feature on OpenCASCADE (ExtrudeFeature, the geometry in
incad.core.extrude_solid), a Join or a Cut; Intersect = a second body + Boolean Common; New Solid = the profile
sketch moved into a new body. Files made before it hold FreeCAD's Pad (Join) / Pocket (Cut): they open and edit as
they are. A Cut goes into the part (against the sketch's normal) unless flipped, as FreeCAD's Pocket does."""
import FreeCAD as App

from incad.core import document
from incad.features.common import Field, profile_of, value_param, value_text

TITLE = "Extrude"
ICON = "extrude"
JOIN, CUT = "PartDesign::FeatureAdditivePython", "PartDesign::FeatureSubtractivePython"  # ProstoCAD's own Extrude
LEGACY = ("PartDesign::Pad", "PartDesign::Pocket")  # FreeCAD's, in files made before it
TYPES = LEGACY + (JOIN, CUT)
MARK = "InCadExtrude"  # on ProstoCAD's own Extrude: how its last result was made
PROPS = {"distance": "Length", "distance2": "Length2", "taper": "TaperAngle"}
FIELDS = (
    Field("operation", "Operation", "choice", (("join", "Join"), ("cut", "Cut"), ("intersect", "Intersect"),
                                               ("new_solid", "New Solid")), "join"),
    Field("extent", "Extents", "choice", (("distance", "Distance"), ("to_next", "To Next"),
                                          ("through_all", "Through All"), ("to", "To")), "distance"),
    Field("to_face", "To", "pick", show_if=("extent", ("to",)), pick="to_face"),
    Field("distance", "Distance", "value_length", default="10 mm", show_if=("extent", ("distance",))),
    Field("direction", "Direction", "choice", (("default", "→"), ("flip", "←"), ("symmetric", "⇆"),
                                               ("two_sides", "⇄ 2")), "default"),
    Field("extent2", "Extents 2", "choice", (("distance", "Distance"), ("to_next", "To Next"),
                                             ("through_all", "Through All"), ("to", "To")), "distance",
          show_if=("direction", ("two_sides",))),
    Field("to_face2", "To 2", "pick", show_if=(("direction", ("two_sides",)), ("extent2", ("to",))), pick="to_face"),
    Field("distance2", "Distance 2", "value_length", default="5 mm",
          show_if=(("direction", ("two_sides",)), ("extent2", ("distance",)))),
    Field("taper", "Taper", "value_angle", default="0 °"),
)
_TYPE = {"distance": "Length", "to_next": "UpToFirst", "to": "UpToFace"}
UP_TO = "InCadUpTo"  # on an Extrude whose To target needs a helper: what was picked ('Obj:Sub')
UP_TO2 = "InCadUpTo2"  # the same for the second side (⇄ 2 ▸ Extents 2 ▸ To)
HELPER, ROLE = "InCadHelperOf", "InCadHelperRole"
_POINTS = ("PartDesign::Point", "App::Point")
_LINES = ("PartDesign::Line", "App::Line")


def _normal(sk):
    return sk.Placement.Rotation.multVec(App.Vector(0, 0, 1))


def claims(f):
    return f.TypeId in LEGACY or MARK in getattr(f, "PropertiesList", ())


def is_own(f):
    """ProstoCAD's own Extrude (not a Pad / Pocket of an older file)."""
    return f is not None and f.TypeId in (JOIN, CUT)


def is_cut(f):
    return f is not None and f.TypeId in ("PartDesign::Pocket", CUT)


_EXTENTS = {"Length": "length", "UpToFirst": "first", "UpToLast": "last", "UpToFace": "face",
            "ThroughAll": "through"}


def _link_shape(link):
    """The shape a To link names, in the Solid's coordinates: a face of a feature, a plane's face, an origin plane."""
    if not link or link[0] is None:
        raise ValueError("pick a face, a plane or a point for To")
    obj, subs = link[0], [s for s in (link[1] or []) if s]
    shape = getattr(obj, "Shape", None)
    if shape is not None and not shape.isNull():
        try:
            return shape.getElement(subs[0]) if subs else shape
        except Exception:
            raise ValueError("the To face (%s of %s) is no longer there: pick it again" % (subs[0], obj.Label))
    import Part  # an origin plane has no shape: its own plane
    pl = obj.Placement
    return Part.Plane(pl.Base, pl.Rotation.multVec(App.Vector(0, 0, 1))).toShape(-1, 1, -1, 1)


class ExtrudeFeature:
    """ProstoCAD's Extrude on OpenCASCADE (incad.core.extrude_solid): Pad's properties (Profile, Type, Length, SideType,
    Reversed, TaperAngle, UpToFace and their second side), so the dialog reads and writes it as it did a Pad or
    Pocket; a Join is a PartDesign additive feature, a Cut a subtractive one (patterns repeat them). Reversed is
    physical: against the sketch's normal, for a Join and a Cut alike (a Cut's default, see _cut_by_default). The proxy; FreeCAD saves its module path and
    class name in the file."""
    KINDS = ["Length", "UpToLast", "UpToFirst", "UpToFace", "ThroughAll"]

    def __init__(self, obj):
        for name, kind, doc in (("Profile", "App::PropertyLinkSub", "The sketch extruded"),
                                ("Type", "App::PropertyEnumeration", "How far it goes"),
                                ("Type2", "App::PropertyEnumeration", "How far it goes on the second side"),
                                ("Length", "App::PropertyLength", "Its length"),
                                ("Length2", "App::PropertyLength", "Its length on the second side"),
                                ("UpToFace", "App::PropertyLinkSub", "The face it goes to (To)"),
                                ("UpToFace2", "App::PropertyLinkSub", "The face it goes to on the second side"),
                                ("SideType", "App::PropertyEnumeration", "One side, symmetric or two sides"),
                                ("Reversed", "App::PropertyBool", "Against the sketch's normal"),
                                ("TaperAngle", "App::PropertyAngle", "The taper: above zero the outline grows"),
                                ("TaperAngle2", "App::PropertyAngle", "The taper on the second side"),
                                (MARK, "App::PropertyString", "How the result was made")):
            if name not in obj.PropertiesList:
                obj.addProperty(kind, name, "Extrude", doc)
        obj.Type = obj.Type2 = self.KINDS
        obj.Type, obj.Type2 = "Length", "Length"
        obj.SideType = ["One side", "Symmetric", "Two sides"]
        obj.SideType = "One side"
        obj.Length, obj.Length2 = 10.0, 5.0
        obj.setPropertyStatus(MARK, "Hidden")
        obj.Proxy = self

    def execute(self, obj):
        from incad.core import extrude_solid
        from incad.features.common import profile_of
        sk = profile_of(obj)
        if sk is None or sk.Shape.isNull():
            raise ValueError("the Extrude has no profile")
        faces = extrude_solid.profile_faces(sk.Shape)
        origin, n = sk.Placement.Base, _normal(sk)
        d = -n if obj.Reversed else n
        base = _material(obj)
        if obj.SideType == "One side" and _EXTENTS.get(obj.Type) == "face":
            target = _link_shape(obj.UpToFace)  # To goes where its face is (Inventor; FreeCAD's Pad did too)
            if not extrude_solid.ahead(target, origin, d) and extrude_solid.ahead(target, origin, -d):
                d = -d

        def spec(direction, kind, length, taper, link):
            kind = _EXTENTS.get(kind, "length")
            return (direction, kind, length, taper, base, _link_shape(link) if kind == "face" else None)
        one = (obj.Type, obj.Length.Value, obj.TaperAngle.Value, obj.UpToFace)
        if obj.SideType == "Symmetric":
            half = (obj.Type, obj.Length.Value / 2) + one[2:]
            sides = [spec(d, *half), spec(-d, *half)]
        elif obj.SideType == "Two sides":
            sides = [spec(d, *one), spec(-d, obj.Type2, obj.Length2.Value, obj.TaperAngle2.Value, obj.UpToFace2)]
        else:
            sides = [spec(d, *one)]
        tool = extrude_solid.tool(faces, origin, sides)
        shape, how = extrude_solid.combine(base, tool, "cut" if is_cut(obj) else "join", faces, origin, d,
                                           bool(getattr(obj, "Refine", False)))
        obj.AddSubShape = tool
        setattr(obj, MARK, how)
        obj.Shape = shape

    def dumps(self):
        return None

    def loads(self, state):
        return None


def _new(container, operation, name="Extrude"):
    """A new own Extrude in the Solid `container`."""
    from incad.core import document
    tip = getattr(container, "Tip", None)
    tip = tip if getattr(tip, "TypeId", "").startswith("PartDesign::") else None  # a solid step's look, not a sketch's
    f = container.newObject(_feature_type(operation), name)
    ExtrudeFeature(f)
    document.show_like(f, tip)  # (a Python feature made from code draws nothing without a view provider)
    return f


def default_operation(body, sk):
    """Cut when the profile lies inside the material (Inventor pre-selects Cut there), otherwise Join."""
    shape = body.Shape
    if shape.isNull() or not shape.Solids:  # (not its volume: 0.5 s on a part with a modeled thread)
        return "join"
    try:
        centre = sk.Shape.BoundBox.Center
        return "cut" if shape.isInside(centre + _normal(sk) * 0.01, 1e-6, True) else "join"
    except Exception:
        return "join"


def defaults(doc, body, profile):
    values = {f.key: f.default for f in FIELDS}
    values["operation"] = default_operation(body, profile)
    values["to_face"] = values["to_face2"] = ""
    return values


def _material(f):
    """The material before the feature, or None when there is none."""
    base = f.BaseFeature.Shape if f.BaseFeature is not None else None
    return base if base is not None and not base.isNull() and base.Solids else None


def _feature_type(operation, like=None):
    """The type for a Join / Cut: ProstoCAD's own, or for a Pad / Pocket of an older file (`like`) its FreeCAD twin
    (switched in place it keeps its family: an own Extrude would refuse when a sketch sits on its face)."""
    if like is not None and like.TypeId in LEGACY:
        return "PartDesign::Pocket" if operation == "cut" else "PartDesign::Pad"
    return CUT if operation == "cut" else JOIN


def _type(f, extent):
    cut = is_cut(f)
    return ("ThroughAll" if cut else "UpToLast") if extent == "through_all" else _TYPE.get(extent, "Length")


def _cut_by_default(f):
    """True for an own Extrude's Cut: Reversed (against the sketch's normal) is its default, into the part, as
    FreeCAD's Pocket goes there unless Reversed."""
    return is_own(f) and is_cut(f)


def _apply(doc, f, values):
    extent = values.get("extent", "distance")
    f.Type = _type(f, extent)
    if extent == "to" and values.get("to_face"):
        f.UpToFace = _to_target(doc, f, values["to_face"])
    else:
        _drop_helpers(doc, f, side=1)
    direction = values.get("direction", "default")
    extent2 = values.get("extent2", "distance") if direction == "two_sides" else "distance"
    f.Type2 = _type(f, extent2)  # the second side has its own extents (Inventor's two directions)
    if extent2 == "to" and values.get("to_face2"):
        f.UpToFace2 = _to_target(doc, f, values["to_face2"], side=2)
    else:
        _drop_helpers(doc, f, side=2)
    f.SideType = {"symmetric": "Symmetric", "two_sides": "Two sides"}.get(direction, "One side")
    f.Reversed = (direction == "flip") != _cut_by_default(f)
    if extent == "to_next" and direction == "default" and _material_only_behind(f):
        f.Reversed = not f.Reversed  # Inventor: To Next goes where the part is
    if extent == "to" and direction in ("default", "flip") and is_own(f) and _to_face_behind(f):
        f.Reversed = not f.Reversed  # To goes where its face is: the arrow shows that way
    value_param(doc, f, "Length", values.get("distance") or "10 mm", "length")
    if direction == "two_sides":
        value_param(doc, f, "Length2", values.get("distance2") or "5 mm", "length")
    value_param(doc, f, "TaperAngle", values.get("taper") or "0 °", "angle")
    _tapered_through(f, extent == "through_all" and _angle(values.get("taper")) != 0)


REACH = "InCadThroughAllReach"  # on a tapered Through All: its length is the part's reach (see _tapered_through)


def _angle(text):
    try:
        return App.Units.Quantity((text or "0 °").replace("°", " deg")).Value
    except Exception:
        return 1.0  # a formula: taken as tapered


def _reach(f):
    """How far the part reaches from the profile's plane, either way, plus 1 mm (a Through All's length)."""
    base = f.BaseFeature.Shape if f.BaseFeature is not None else None
    prof = f.Profile[0] if isinstance(f.Profile, (tuple, list)) and f.Profile else f.Profile
    if base is None or base.isNull() or prof is None:
        return None
    pl = prof.getGlobalPlacement()
    n = pl.Rotation.multVec(App.Vector(0, 0, 1))
    bb = base.BoundBox
    corners = [App.Vector(x, y, z) for x in (bb.XMin, bb.XMax) for y in (bb.YMin, bb.YMax) for z in (bb.ZMin, bb.ZMax)]
    return max(abs((c - pl.Base).dot(n)) for c in corners) + 1.0


def _tapered_through(f, on):
    """Through All with a taper: FreeCAD's Through All runs the length of the whole box's diagonal, and an inward
    taper closed the profile long before (refused: "values too large", the finder). It is a length just past the
    part instead, measured again when the part changes (follow_reach)."""
    if not on:
        if REACH in f.PropertiesList:
            f.removeProperty(REACH)
        return
    reach = _reach(f)
    if reach is None:
        return
    if REACH not in f.PropertiesList:
        f.addProperty("App::PropertyBool", REACH, "InCad", "Through All with a taper: the length reaches past the part")
        f.setPropertyStatus(REACH, "Hidden")
    setattr(f, REACH, True)
    f.Type = "Length"
    f.setExpression("Length", None)
    f.Length = reach * (2 if f.SideType == "Symmetric" else 1)  # (a symmetric Length is both halves)


def follow_reach(doc):
    """After a recompute (incad.ui.shell): tapered Through All extrudes reach past the part as it is now."""
    from incad.core import safe
    todo = []
    for f in doc.Objects:
        if REACH in getattr(f, "PropertiesList", ()):
            reach = _reach(f)
            reach = reach * (2 if f.SideType == "Symmetric" else 1) if reach is not None else None
            if reach is not None and abs(reach - f.Length.Value) > 1e-6:
                todo.append((f, reach))
    if not todo:
        return []

    def go():
        for f, reach in todo:
            f.Length = reach
    r = safe.run_safely("Through All follows the part", go, doc)
    return [f.Label for f, _ in todo] if r.ok else []


def _helper(doc, f, role, type_id, label):
    """The hidden object InCad keeps right before `f` for its To target (made when missing)."""
    body = f.getParentGeoFeatureGroup()
    for o in body.Group:
        if getattr(o, HELPER, None) == f.Name and getattr(o, ROLE, None) == role:
            if o.TypeId == type_id:
                return o
            _remove(doc, o)
            break
    h = doc.addObject(type_id, "ExtrudeTo")
    body.insertObject(h, f, False)  # before the extrude: a feature never references a later object
    h.Label = label
    for prop, doc_text in ((HELPER, "The extrude this belongs to"), (ROLE, "What it is for the extrude")):
        h.addProperty("App::PropertyString", prop, "InCad", doc_text)
        h.setPropertyStatus(prop, "Hidden")
    setattr(h, HELPER, f.Name)
    setattr(h, ROLE, role)
    h.Visibility = False
    return h


def _remove(doc, o):
    body = o.getParentGeoFeatureGroup()
    if body is not None:
        body.removeObject(o)
    doc.removeObject(o.Name)


def _side_of(role):
    return 2 if role.endswith("2") else 1


def _drop_helpers(doc, f, keep=(), side=None):
    """f's To helpers (of one side, or of both) except `keep`."""
    body = f.getParentGeoFeatureGroup()
    for o in list(body.Group if body is not None else []):
        role = getattr(o, ROLE, "")
        if getattr(o, HELPER, None) == f.Name and role.startswith("to_") and o not in keep \
                and side in (None, _side_of(role)):
            _remove(doc, o)
    if not keep:
        for prop in ((UP_TO, UP_TO2) if side is None else ((UP_TO,) if side == 1 else (UP_TO2,))):
            if hasattr(f, prop):
                setattr(f, prop, "")


def _bound(doc, f, obj, sub, role):
    """`obj`'s element `sub` as seen from f's Solid: itself when it is in that Solid, else a binder of it (a face
    of another Solid as it is placed: FreeCAD reads another Solid's feature in that Solid's own coordinates)."""
    body = f.getParentGeoFeatureGroup()
    owner = obj.getParentGeoFeatureGroup()
    if owner is body:
        return obj, sub, None
    binder = _helper(doc, f, role, "PartDesign::SubShapeBinder", "Extrude To (binder)")
    path = "%s.%s" % (obj.Name, sub) if sub else obj.Name + "."
    support = [(owner, path)] if owner is not None else [(obj, sub)]
    if list(binder.Support) != support:
        binder.Support = support
    binder.recompute()
    element = "Vertex1" if sub.startswith("Vertex") or obj.TypeId in _POINTS else \
        "Edge1" if sub.startswith("Edge") or obj.TypeId in _LINES else "Face1"
    return binder, element, binder


def _to_target(doc, f, pick, side=1):
    """f.UpToFace for the picked To target: a face or plane of the extrude's own Solid as it is; any face of another
    Solid (flat or curved) through a binder; a point (a corner, a sketch or work point) through a plane parallel to
    the sketch, which follows the point."""
    name, _, sub = pick.partition(":")
    obj = doc.getObject(name)
    if obj is None:
        raise ValueError("Select a face, a plane or a point for To")
    tail, prop = ("", UP_TO) if side == 1 else ("2", UP_TO2)
    point = sub.startswith("Vertex") or (not sub and obj.TypeId in _POINTS)
    edge = sub.startswith("Edge") or (not sub and obj.TypeId in _LINES)
    keep = []
    if point or edge:
        target, element, binder = _bound(doc, f, obj, sub, "to_binder" + tail)
        keep += [binder] if binder is not None else []
        sk = profile_of(f)
        if edge:  # an edge or axis: through its middle (a work point on it, which follows the edge)
            mine = [o for o in f.getParentGeoFeatureGroup().Group
                    if getattr(o, HELPER, None) == f.Name and getattr(o, ROLE, None) in ("to_point" + tail,
                                                                                         "to_plane" + tail)]
            if not any(getattr(o, ROLE, None) == "to_point" + tail for o in mine):
                for o in mine:  # the plane is made again after the new point: nothing refers to a later object
                    _remove(doc, o)
            mid = _helper(doc, f, "to_point" + tail, "PartDesign::Point", "Extrude To (edge)")
            refs = [(target, element)]
            if list(mid.AttachmentSupport) != refs or mid.MapMode != "OnEdge":
                mid.AttachmentSupport = refs
                mid.MapMode = "OnEdge"
                mid.MapPathParameter = 0.5
            mid.recompute()
            if "Invalid" in mid.State:
                raise ValueError("To: no point on that edge (%s)" % mid.getStatusString())
            keep.append(mid)
            target, element = mid, ""
        plane = _helper(doc, f, "to_plane" + tail, "PartDesign::Plane", "Extrude To (point)")
        refs = [(sk, ""), (target, element)]
        if list(plane.AttachmentSupport) != refs or plane.MapMode != "ParallelPlane":
            plane.AttachmentSupport = refs
            plane.MapMode = "ParallelPlane"
        plane.recompute()
        if "Invalid" in plane.State:
            raise ValueError("To: no plane through that point (%s)" % plane.getStatusString())
        keep.append(plane)
        result = (plane, [""])
    else:
        target, element, binder = _bound(doc, f, obj, sub, "to_binder" + tail)
        keep += [binder] if binder is not None else []
        result = (target, [element] if element else [])
    _drop_helpers(doc, f, keep, side)
    if keep:
        if not hasattr(f, prop):
            f.addProperty("App::PropertyString", prop, "InCad", "What To was picked on")
            f.setPropertyStatus(prop, "Hidden")
        setattr(f, prop, pick)
    elif hasattr(f, prop):
        setattr(f, prop, "")
    return result


def _to_face_behind(f):
    """True when f's To face lies only behind the way it goes (a sketch above the part, To its top face)."""
    from incad.core import extrude_solid
    sk = profile_of(f)
    if sk is None or sk.Shape.isNull() or f.Type != "UpToFace":
        return False
    try:
        target = _link_shape(f.UpToFace)
        n = _normal(sk)
        d = -n if f.Reversed else n
        return not extrude_solid.ahead(target, sk.Placement.Base, d) and \
            extrude_solid.ahead(target, sk.Placement.Base, -d)
    except Exception:
        return False


def _material_only_behind(f):
    """True when the part lies only against this feature's default direction (a sketch above the part: To Next
    would find nothing)."""
    import Part
    sk = profile_of(f)
    base = f.BaseFeature.Shape if f.BaseFeature is not None else None
    if sk is None or base is None or base.isNull() or sk.Shape.isNull():
        return False
    n = _normal(sk)
    if is_cut(f):
        n = -n  # a Cut goes against the sketch's normal
    c = sk.Shape.BoundBox.Center
    reach = base.BoundBox.DiagonalLength + (c - base.BoundBox.Center).Length + 1.0

    def hits(d):
        ray = Part.LineSegment(c + d * 1e-4, c + d * reach).toShape()
        return any(e.Length > 1e-6 for e in base.common(ray).Edges)
    return not hits(n) and hits(-n)


def _touches(f):
    """Does the extruded shape touch or overlap the material before it?"""
    base = f.BaseFeature.Shape if f.BaseFeature is not None else None
    if base is None or base.isNull() or not base.Solids:
        return False
    try:
        tool = f.AddSubShape
        return not tool.isNull() and tool.BoundBox.intersect(base.BoundBox) and tool.distToShape(base)[0] < 1e-6
    except Exception:
        return False


def _solids(feature):
    try:
        shape = feature.Shape
        return 0 if shape.isNull() else len(shape.Solids)
    except Exception:
        return 0


def create(doc, body, profile, values):
    operation = values.get("operation", "join")
    target = body
    if operation in ("new_solid", "intersect"):
        target = document.new_solid(doc)
        body.removeObject(profile)
        target.addObject(profile)
        document.settle_support(profile, target)  # on a face of the part: not the whole part in the new Solid
    before = _solids(body.Tip) if body.Tip is not None else 0
    f = _new(target, "cut" if operation == "cut" else "join")
    f.Profile = profile
    _apply(doc, f, values)
    if operation == "join" and before:
        doc.recompute()
        if _solids(f) > before and _touches(f):  # some of it joins the part: it stays a Join (one profile on the part,
            from incad.core import log           # one beside it went into a new Solid of two lumps before)
            log.report("warning", "%s: a shape of its profile doesn't reach into the part (it lies beside it, or "
                       "touches it only along a line or at a point): it stays a separate lump in the Solid (move it "
                       "a little into the part, or extrude it as a New Solid)" % f.Label)
        elif _solids(f) > before:  # the new solid does not touch the part: a piece of its own, not a second
            _drop_helpers(doc, f)   # lump in the part's body (nothing could be shelled or filleted there)
            target.removeObject(f)
            doc.removeObject(f.Name)
            target = document.new_solid(doc)
            body.removeObject(profile)
            target.addObject(profile)
            document.settle_support(profile, target)
            f = _new(target, "join")
            f.Profile = profile
            _apply(doc, f, values)
            document.show_result(body)  # the first try, in the part, hid the part's last step (FreeCAD hides a base)
            from incad.core import log
            log.report("info", "%s does not touch the part: made as its own Solid (%s)" % (f.Label, target.Label))
    if operation == "intersect":
        doc.recompute()
        common = body.newObject("PartDesign::Boolean", "Intersect")
        common.Type = "Common"
        common.addObjects([target])
        return common
    return f


def _pad_of(feature):
    if feature.TypeId == "PartDesign::Boolean":
        tool = feature.Group[0] if feature.Group else None
        return tool.Tip if tool is not None else None
    return feature


def _starts_own_solid(doc, f):
    """True for the Join a Solid of its own starts with (New Solid): no material before it, off the part. A later
    feature in that Solid (a Cut through the box drawn beside the part) is a Join or a Cut of that Solid."""
    body = f.getParentGeoFeatureGroup()
    return body is not None and document.primary_body(doc) is not body and _material(f) is None


def read(doc, feature):
    if feature.TypeId == "PartDesign::Boolean":
        values = read(doc, _pad_of(feature))
        values["operation"] = "intersect"
        return values
    f = feature
    operation = "cut" if is_cut(f) else "join"
    if operation == "join" and _starts_own_solid(doc, f):
        operation = "new_solid"
    extent = {"Length": "distance", "UpToFirst": "to_next", "UpToFace": "to", "ThroughAll": "through_all",
              "UpToLast": "through_all"}.get(f.Type, "distance")
    if getattr(f, REACH, False):
        extent = "through_all"
    direction = {"Symmetric": "symmetric", "Two sides": "two_sides"}.get(
        f.SideType, "flip" if f.Reversed != _cut_by_default(f) else "default")
    def picked(prop, link):
        if getattr(f, prop, ""):
            return getattr(f, prop)  # through a helper: what was picked
        if link and link[0] is not None:
            sub = link[1][0] if link[1] else ""
            return "%s:%s" % (link[0].Name, sub) if sub else link[0].Name
        return ""
    extent2 = {"Length": "distance", "UpToFirst": "to_next", "UpToFace": "to", "ThroughAll": "through_all",
               "UpToLast": "through_all"}.get(getattr(f, "Type2", "Length"), "distance")
    return {"operation": operation, "extent": extent, "direction": direction,
            "to_face": picked(UP_TO, f.UpToFace), "extent2": extent2,
            "to_face2": picked(UP_TO2, getattr(f, "UpToFace2", None)),
            "distance": value_text(doc, f, "Length") or "10 mm",
            "distance2": value_text(doc, f, "Length2") or "5 mm",
            "taper": value_text(doc, f, "TaperAngle") or "0 °"}


def update(doc, feature, values):
    """Apply dialog values. Join<->Cut replaces Pad/Pocket in place, keeping its parameters."""
    is_boolean = feature.TypeId == "PartDesign::Boolean"  # decided first: a replaced Pad no longer exists
    f = _pad_of(feature)
    wanted = values.get("operation", "join")
    own = _starts_own_solid(doc, f)
    if wanted in ("join", "cut") and (is_boolean or own) and read(doc, feature)["operation"] != wanted:
        raise ValueError("This extrude is a Solid of its own (New Solid or Intersect): it can't turn into a %s of "
                         "the part here. Join or cut it with Boolean, or delete it and "
                         "extrude again" % ("Join" if wanted == "join" else "Cut"))
    if wanted in ("join", "cut") and f.TypeId != _feature_type(wanted, f) and not is_boolean:
        f = _replace(doc, f, _feature_type(wanted, f))
    _apply(doc, f, values)
    return feature if is_boolean else f


def _refers_to(value, target):
    if value is target:
        return True
    if isinstance(value, (list, tuple)):
        return any(_refers_to(v, target) for v in value)
    return False


def _dependents(old, body):
    """Objects (other than the body and the next feature's BaseFeature link) that use `old` — they would lose it."""
    out = []
    for dep in old.InList:
        if dep is body:
            continue
        uses = any(prop != "BaseFeature" and _refers_to(getattr(dep, prop, None), old) for prop in dep.PropertiesList)
        uses = uses or any(old.Name + "." in expr for _, expr in getattr(dep, "ExpressionEngine", []) or [])
        if uses:
            out.append(dep.Label)
    return out


def _replace(doc, old, type_id):
    body = old.getParentGeoFeatureGroup()
    users = _dependents(old, body)
    if users:
        raise ValueError("Join/Cut can't be switched here: %s %s built on this Extrude"
                         % (", ".join(users), "is" if len(users) == 1 else "are"))
    visible = old.Visibility
    profile = profile_of(old)
    bindings = {p: e for p, e in old.ExpressionEngine}
    was_tip = body.Tip is old
    index = body.Group.index(old)
    after = body.Group[index - 1] if index > 0 else None
    label = old.Label
    helpers = [o for o in body.Group if getattr(o, HELPER, None) == old.Name]
    up_to = {p: getattr(old, p, "") for p in (UP_TO, UP_TO2)}
    body.removeObject(old)
    doc.removeObject(old.Name)
    new = doc.addObject(type_id, "Extrude")
    if type_id in (JOIN, CUT):
        ExtrudeFeature(new)
    body.insertObject(new, after, True) if after is not None else body.insertObject(new, body.Group[0] if body.Group else None, False)
    new.Label = label
    new.Profile = profile
    from incad.core import document
    document.show_like(new, getattr(new, "BaseFeature", None))  # (a re-made feature draws nothing without it)
    for h in helpers:  # the To target's helpers follow the re-made feature
        setattr(h, HELPER, new.Name)
    for prop, text in up_to.items():
        if text:
            new.addProperty("App::PropertyString", prop, "InCad", "What To was picked on")
            new.setPropertyStatus(prop, "Hidden")
            setattr(new, prop, text)
    for path, expr in bindings.items():
        new.setExpression(path.lstrip("."), expr)
    if was_tip:
        body.Tip = new
    else:
        new.Visibility = visible
    return new
