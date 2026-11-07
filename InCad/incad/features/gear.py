"""3D Model ▸ Create ▸ Gear and Worm (Inventor's spur gear and worm generators, as part features). A step of the
part (a PartDesign::FeaturePython) that builds the gear from its values on every recompute (incad.core.gears) and
joins it to the part; one that touches nothing of the part becomes its own Solid, as an Extrude does.

Where: the centre is a circle (a shaft's end, a hole, a sketch circle: its centre and its plane), a point, or by
default the part's origin; a plane or flat face picked sets the plane. Gear ▸ Mesh with: another Gear — this one
takes its module, pressure and helix angle (the other hand), stands on its +X at the centre distance and is turned
so the teeth meet; or a Worm — this one becomes its wheel (the worm's axial module, helix angle = its lead angle,
the same hand), across the worm at its middle."""
import dataclasses
import json
import math

import FreeCAD as App

from incad.core import gears
from incad.features.common import Field, format_picks, parse_picks, value_param, value_text

TYPES = ("PartDesign::FeaturePython",)
MARK = "InCadGear"  # its settings (JSON): what tells a Gear or Worm apart from the other Python features
V = App.Vector

_PROPS = (("Module", "App::PropertyLength"), ("Teeth", "App::PropertyInteger"),
          ("PitchDiameter", "App::PropertyLength"), ("PressureAngle", "App::PropertyAngle"),
          ("HelixAngle", "App::PropertyAngle"), ("Width", "App::PropertyLength"), ("Bore", "App::PropertyLength"),
          ("Turn", "App::PropertyAngle"), ("Starts", "App::PropertyInteger"), ("Length", "App::PropertyLength"),
          ("Ratio", "App::PropertyFloat"), ("CenterDistance", "App::PropertyLength"),
          ("AxisRef", "App::PropertyLinkSubGlobal"), ("FixedPlacement", "App::PropertyPlacement"),
          ("RotateX", "App::PropertyAngle"), ("RotateY", "App::PropertyAngle"), ("OffsetX", "App::PropertyDistance"),
          ("OffsetY", "App::PropertyDistance"), ("OffsetZ", "App::PropertyDistance"),
          ("CenterRef", "App::PropertyLinkSubGlobal"), ("PlaneRef", "App::PropertyLinkSubGlobal"),
          ("MeshWith", "App::PropertyLinkGlobal"), ("Around", "App::PropertyAngle"))


def settings(f):
    try:
        return json.loads(getattr(f, MARK, "") or "{}")
    except ValueError:
        return {}


def is_gear(f, kind=None):
    return f is not None and MARK in getattr(f, "PropertiesList", ()) and \
        (kind is None or settings(f).get("kind") == kind)


def gear_of(obj, sub):
    """The Gear or Worm a pick stands for: the feature itself (a tree row, or the Tip showing it), or the one whose
    teeth hold the face clicked on a part where more came after it."""
    import Part
    if obj is None:
        return None
    if is_gear(obj):
        return obj
    body = obj if obj.TypeId == "PartDesign::Body" else obj.getParentGeoFeatureGroup()
    if body is None or not sub.startswith(("Face", "Edge", "Vertex")):
        return None
    try:
        el = obj.getSubObject(sub)
    except Exception:
        return None
    if el is None or el.isNull():  # a pick on an element that is gone (the log: "null shape")
        return None
    probe = Part.Vertex(el.CenterOfMass if el.ShapeType == "Face" else el.Vertexes[0].Point)
    for g in reversed(body.Group):
        if is_gear(g):
            try:
                if tool(g).distToShape(probe)[0] < 1e-6:
                    return g
            except Exception:
                continue
    return None



# ----- the values each gear builds from -----
def _hand(f):
    return settings(f).get("hand") or "right"


def mate_of(f, depth=0):
    """The Gear or Worm `f` meshes with (None for none, itself, or a chain that runs too deep: a loop)."""
    mate = getattr(f, "MeshWith", None)
    return mate if is_gear(mate) and mate is not f and depth < 8 else None


def effective(f, depth=0):
    """{module, teeth, pressure, helix, hand} a Gear builds with (a mate's own values when it meshes with one); for a
    Worm {module, starts, diameter, pressure, hand, length} — a worm meshing a gear takes the gear's transverse module,
    pressure angle and hand."""
    s = settings(f)
    mate = mate_of(f, depth)
    if s.get("kind") == "worm":
        module, pressure, hand = f.Module.Value, f.PressureAngle.Value, _hand(f)
        if is_gear(mate, "gear"):
            e = effective(mate, depth + 1)
            module, pressure, hand = e["module"] / math.cos(math.radians(e["helix"])), e["pressure"], e["hand"]
        return {"module": module, "starts": max(1, int(f.Starts)), "diameter": f.PitchDiameter.Value,
                "pressure": pressure, "hand": hand, "length": f.Length.Value}
    if is_gear(mate, "worm"):
        w = effective(mate, depth + 1)
        gamma = gears.lead_angle(w["module"], w["starts"], w["diameter"])
        module, pressure, helix, hand = w["module"] * math.cos(math.radians(gamma)), w["pressure"], gamma, w["hand"]
    elif mate is not None:
        e = effective(mate, depth + 1)
        module, pressure, helix = e["module"], e["pressure"], e["helix"]
        hand = "left" if e["hand"] == "right" else "right"
    else:
        module, pressure, helix, hand = f.Module.Value, f.PressureAngle.Value, f.HelixAngle.Value, _hand(f)
    size = s.get("size") or "teeth"
    if size == "diameter":
        teeth = gears.teeth_for(f.PitchDiameter.Value, module, helix)
    elif size in ("ratio", "distance"):
        if mate is None:
            raise ValueError("Ratio and Center distance size a gear against the one it meshes with: pick Mesh with")
        theirs = _mate_size(mate, depth + 1)  # (teeth or starts, pitch Ø)
        if size == "ratio":
            teeth = max(gears.MIN_TEETH, int(round(getattr(f, "Ratio", 1.0) * theirs[0])))
        else:
            d = 2 * getattr(f, "CenterDistance", App.Units.Quantity("0 mm")).Value - theirs[1]
            if d <= 0:
                raise ValueError("The center distance must be more than half the other's pitch Ø (%.4g mm)"
                                 % (theirs[1] / 2))
            teeth = gears.teeth_for(d, module, helix)
    else:
        teeth = int(f.Teeth)
    return {"module": module, "teeth": teeth, "pressure": pressure, "helix": helix, "hand": hand}


def _mate_size(mate, depth=0):
    """(teeth — a worm's starts —, pitch Ø) of the gear or worm another one meshes with."""
    e = effective(mate, depth)
    if settings(mate).get("kind") == "worm":
        return e["starts"], e["diameter"]
    return e["teeth"], gears.pitch_diameter(e["module"], e["teeth"], e["helix"])


# ----- where it stands -----
def _rotation(z, x_hint):
    z = V(z).normalize()
    x = x_hint - z * x_hint.dot(z)
    if x.Length < 1e-9:
        x = V(0, 1, 0) - z * z.y
    x.normalize()
    return App.Rotation(x, z.cross(x), z, "ZXY")


def _element(obj, sub):
    import Part
    return Part.getShape(obj, sub, needSubElement=True) if sub else None


def _ref(link):
    if not link or link[0] is None:
        return None, ""
    subs = link[1]
    return link[0], (subs[0] if subs else "")


def _axis_line(obj, sub):
    """(start point, direction) of an axis pick: a work / origin axis, a sketch line, a straight edge."""
    if not sub:
        gp = obj.getGlobalPlacement()
        return gp.Base, gp.Rotation.multVec(V(0, 0, 1))
    try:
        edge = _element(obj, sub)
        a, b = edge.Vertexes[0].Point, edge.Vertexes[-1].Point
    except Exception:
        if obj.TypeId != "Sketcher::SketchObject" or not sub.startswith("Edge"):
            raise
        # a construction line is not in the sketch's shape: the sketch's own geometry by number
        geo = obj.Geometry[int(sub[4:]) - 1]
        pl = obj.getGlobalPlacement()
        a, b = pl.multVec(geo.StartPoint), pl.multVec(geo.EndPoint)
    d = b - a
    if d.Length < 1e-9:
        raise ValueError("Axis: pick a straight line or edge")
    return a, d.normalize()


def _ref_frame(f):
    """(origin, axis or None, x hint) in global coordinates from the axis, centre and plane picks: an axis sets the
    gear's axis and its start (a centre picked as well slides along it), else a plane and / or a centre."""
    body = f.getParentGeoFeatureGroup()
    base = body.getGlobalPlacement() if body is not None else App.Placement()
    origin, axis, x_hint = None, None, base.Rotation.multVec(V(1, 0, 0))
    obj, sub = _ref(getattr(f, "AxisRef", None))
    if obj is not None:
        origin, axis = _axis_line(obj, sub)
        c_obj, c_sub = _ref(f.CenterRef)
        if c_obj is not None:
            el = _element(c_obj, c_sub) if c_sub else None
            p = c_obj.getGlobalPlacement().Base if el is None else (
                el.Point if el.ShapeType == "Vertex" else el.Curve.Center)
            origin = origin + axis * (p - origin).dot(axis)
        return origin, axis, x_hint
    obj, sub = _ref(f.PlaneRef)
    if obj is not None:
        if not sub:  # a work or origin plane, a sketch: its own placement
            gp = obj.getGlobalPlacement()
            origin, axis, x_hint = gp.Base, gp.Rotation.multVec(V(0, 0, 1)), gp.Rotation.multVec(V(1, 0, 0))
        else:
            face = _element(obj, sub)
            u, v = face.Surface.parameter(face.CenterOfMass)[:2]
            origin, axis = face.CenterOfMass, face.normalAt(u, v)
    obj, sub = _ref(f.CenterRef)
    if obj is not None:
        point, circle_axis = None, None
        if not sub:
            point = obj.getGlobalPlacement().Base  # a work point
        else:
            el = _element(obj, sub)
            if el.ShapeType == "Vertex":
                point = el.Point
            else:
                c = el.Curve
                point, circle_axis = c.Center, c.Axis
        if axis is None:
            axis = circle_axis
            if axis is not None and obj.TypeId == "Sketcher::SketchObject":
                axis = obj.getGlobalPlacement().Rotation.multVec(V(0, 0, 1))  # the sketch's own side
        if origin is not None and axis is not None:
            point = point - axis * (point - origin).dot(axis)  # onto the plane picked
        origin = point
    if origin is None:
        origin = base.Base
    if axis is None:
        axis = base.Rotation.multVec(V(0, 0, 1))
    return origin, axis, x_hint


_ACROSS = App.Rotation(V(-1, 0, 0), V(0, 0, 1), V(0, 1, 0), "ZXY")  # a worm wheel's frame in its worm's: its
# axis along the worm's Y, its X pointing back at the worm


def _mid(g):
    """Where a gear's middle plane is along its own axis: a worm wheel is built centred, others stand on z = 0."""
    if is_gear(mate_of(g), "worm"):
        return 0.0
    return g.Width.Value / 2 * (-1 if settings(g).get("reverse") else 1)


def _around(f):
    """Degrees round the gear `f` meshes with, from that gear's X (none round a worm: a wheel sits across it)."""
    return f.Around.Value if hasattr(f, "Around") and is_gear(mate_of(f), "gear") else 0.0


def frame(f, depth=0):
    """The gear's own frame in global coordinates: origin at its centre, Z along its axis, X where a tooth's middle
    (a worm's thread) is at turn 0."""
    mate = mate_of(f, depth)
    if mate is not None:
        mf = frame(mate, depth + 1)
        mine = effective(f)
        if settings(f).get("kind") == "worm":  # across the gear at its middle plane, on the gear's +X
            e = effective(mate)
            a = (mine["diameter"] + gears.pitch_diameter(e["module"], e["teeth"], e["helix"])) / 2
            at_wheel = App.Placement(V(a, 0, mine["length"] / 2), _ACROSS)
            return mf.multiply(App.Placement(V(0, 0, _mid(mate)), App.Rotation(V(0, 0, 1), _around(f)))).multiply(
                at_wheel.inverse())
        if is_gear(mate, "worm"):
            w = effective(mate)
            a = (w["diameter"] + gears.pitch_diameter(mine["module"], mine["teeth"], mine["helix"])) / 2
            length = w["length"] * (-1 if settings(mate).get("reverse") else 1)
            # across the worm at its middle: the wheel's axis is the worm's Y, its X points back at the worm
            return mf.multiply(App.Placement(V(a, 0, length / 2), _ACROSS))
        e = effective(mate)
        a = gears.center_distance(mine["module"], e["teeth"], mine["teeth"], mine["helix"])
        return mf.multiply(App.Placement(V(), App.Rotation(V(0, 0, 1), _around(f)))).multiply(
            App.Placement(V(a, 0, 0), App.Rotation()))
    if settings(f).get("fixed"):  # baked (Make Assembly): where it stood when its mate was there
        place = App.Placement(f.FixedPlacement)
    else:
        origin, axis, x_hint = _ref_frame(f)
        place = App.Placement(origin, _rotation(axis, x_hint))
    # the gizmo's slides and its tilts round its own X and Y, in its own frame
    rx, ry = (getattr(f, p).Value if hasattr(f, p) else 0.0 for p in ("RotateX", "RotateY"))
    tilt = App.Rotation(V(1, 0, 0), rx).multiply(App.Rotation(V(0, 1, 0), ry))
    return place.multiply(App.Placement(offset(f), tilt))


def bake(f):
    """A gear or worm that meshes with another stands on its own from now on, exactly where and as it is: its
    module, pressure and helix angle, hand, teeth, turn and place are written into it and the mate is let go
    (Make Assembly: each gear goes into a part file of its own, its mate into another)."""
    import FreeCAD
    from incad.features.common import value_param
    if mate_of(f) is None:
        return False
    for prop, typ in _PROPS:
        if prop not in f.PropertiesList:
            f.addProperty(typ, prop, "Gear", "")
    doc = f.Document
    s = settings(f)
    e = effective(f)
    turn = _turn(f)
    place = frame(f)
    if s.get("kind") != "worm" and is_gear(mate_of(f), "worm"):
        place = place.multiply(FreeCAD.Placement(V(0, 0, -f.Width.Value / 2), FreeCAD.Rotation()))  # was centred
    texts = [("Module", "%.9g mm" % e["module"], "length"), ("PressureAngle", "%.9g °" % e["pressure"], "angle"),
             ("Turn", "%.9g °" % turn, "angle")]
    if s.get("kind") != "worm":
        texts += [("Teeth", "%d" % e["teeth"], "count"), ("HelixAngle", "%.9g °" % e["helix"], "angle")]
    for prop, text, unit in texts:
        value_param(doc, f, prop, text, unit)
    for prop in ("OffsetX", "OffsetY", "OffsetZ"):
        value_param(doc, f, prop, "0 mm", "length")
    for prop in ("RotateX", "RotateY"):  # the frame baked in holds the tilts too
        value_param(doc, f, prop, "0 °", "angle")
    f.FixedPlacement = place
    s.update(fixed=True, hand=e["hand"], size="teeth")
    setattr(f, MARK, json.dumps(s))
    f.MeshWith = None
    f.CenterRef = None
    f.PlaneRef = None
    f.AxisRef = None
    return True


def bake_all(doc, bodies=None):
    """bake() every gear and worm (in `bodies`, or the whole document) that meshes with one in another Solid."""
    done = []
    for o in list(doc.Objects):
        if is_gear(o) and mate_of(o) is not None and (bodies is None or o.getParentGeoFeatureGroup() in bodies):
            if mate_of(o).getParentGeoFeatureGroup() is not o.getParentGeoFeatureGroup() and bake(o):
                done.append(o)
    if done:
        doc.recompute()
    return done


def turn_ratio(f):
    """Degrees `f` turns about its frame's Z for one degree of the gear it meshes with about that one's Z (Make
    Assembly: the gears turn together). Spur gears in mesh turn the other way by the ratio of their teeth; a worm
    and its wheel as _wheel_turn / _worm_turn place them."""
    mate = mate_of(f)
    if is_gear(mate, "worm") or settings(f).get("kind") == "worm":
        worm, wheel = (mate, f) if is_gear(mate, "worm") else (f, mate)
        w, e = effective(worm), effective(wheel)
        r = gears.pitch_diameter(e["module"], e["teeth"], e["helix"]) / 2
        per = gears.lead(w["module"], w["starts"]) / (2 * math.pi * r)  # wheel turns per worm turn
        d = -(1 if w["hand"] == "right" else -1) * (per if worm is mate else 1 / per)
        flip = (-1 if settings(f).get("reverse") else 1) * (-1 if settings(mate).get("reverse") else 1)
        return d * flip
    return -effective(mate)["teeth"] / effective(f)["teeth"]


def train_of(doc, pieces):
    """For Make Assembly: {piece index: (origin, axis, bore radius)} of the gears among the part's pieces, and
    [(mate's piece, gear's piece, turn_ratio)] for each gear in mesh with another piece."""
    where, index_of = {}, {}
    for o in doc.Objects:
        body = o.getParentGeoFeatureGroup() if is_gear(o) else None
        found = [i for i, p in enumerate(pieces) if body is not None and p.body_name == body.Name]
        if len(found) != 1:
            continue
        place = frame(o)
        where[found[0]] = (place.Base, place.Rotation.multVec(V(0, 0, 1)), o.Bore.Value / 2)
        index_of[o.Name] = found[0]
    pairs = []
    for o in doc.Objects:
        mate = mate_of(o) if o.Name in index_of else None
        if mate is not None and mate.Name in index_of and index_of[mate.Name] != index_of[o.Name]:
            pairs.append((index_of[mate.Name], index_of[o.Name], turn_ratio(o)))
    return where, pairs


def offset(f):
    return V(*(getattr(f, p).Value if hasattr(f, p) else 0.0 for p in ("OffsetX", "OffsetY", "OffsetZ")))


def gizmo_frame(f):
    """(global frame at the gear's middle, size for the gizmo) or None: a gear that meshes with another is placed by
    it — move that one (a set's first: the gizmo of either member is the first one's)."""
    f = lead_of(f)
    if f is None or mate_of(f) is not None:
        return None
    place = frame(f)
    s = settings(f)
    if s.get("kind") == "worm":
        mid = f.Length.Value / 2
    else:
        mid = f.Width.Value / 2
    if s.get("reverse"):
        mid = -mid
    return place.multiply(App.Placement(V(0, 0, mid), App.Rotation()))


def _wheel_turn(f, worm):
    """A worm wheel's turn (degrees) that puts a space of the worm in front of the tooth at its middle plane. The
    wheel's +X points at the worm's axis, its +Y runs along the worm's +Z; worm threads cross the wheel's side of the
    worm at z = z0 + j·p (p = π·m), so a tooth there must stand at z0 + p/2 (mod p)."""
    w, e = effective(worm), effective(f)
    p = math.pi * w["module"]
    sign = 1 if w["hand"] == "right" else -1
    z0 = -sign * math.radians(worm.Turn.Value) * gears.lead(w["module"], w["starts"]) / (2 * math.pi)  # turning
    # a worm on moves its threads back along its axis (right hand; the other way for a left-hand one)
    r = gears.pitch_diameter(e["module"], e["teeth"], e["helix"]) / 2
    twist = (f.Width.Value / 2) * math.tan(math.radians(e["helix"])) / r * (1 if e["hand"] == "right" else -1)
    length = w["length"]
    turn = (z0 + p / 2 - length / 2) / r - twist
    pitch = 2 * math.pi / e["teeth"]
    return math.degrees(turn % pitch)


def _worm_turn(f, wheel):
    """A worm's turn (degrees) that puts a space of its thread in front of the wheel's tooth (the other way round
    from _wheel_turn: the wheel is there first)."""
    w, e = effective(f), effective(wheel)
    p = math.pi * w["module"]
    sign = 1 if w["hand"] == "right" else -1
    r = gears.pitch_diameter(e["module"], e["teeth"], e["helix"]) / 2
    twist = _mid(wheel) * math.tan(math.radians(e["helix"])) / r * (1 if e["hand"] == "right" else -1)
    z0 = ((math.radians(_turn(wheel) - _around(f)) + twist) * r - p / 2 + w["length"] / 2) % p  # in its frame
    return math.degrees(-sign * z0 * 2 * math.pi / gears.lead(w["module"], w["starts"]))


def _turn(f, depth=0):
    turn = f.Turn.Value
    mate = mate_of(f, depth)
    if mate is None:
        return turn
    if settings(f).get("kind") == "worm":
        return turn + _worm_turn(f, mate)
    if is_gear(mate, "worm"):
        return turn + _wheel_turn(f, mate)
    # in the frame turned round the mate by Around, the mate's teeth stand Around less
    return turn + gears.mesh_turn(effective(mate)["teeth"], _turn(mate, depth + 1) - _around(f),
                                  effective(f)["teeth"])


def tool(f):
    """The gear or worm alone, where it stands, in the coordinates of the body it is in."""
    s = settings(f)
    if s.get("kind") == "worm":
        w = effective(f)
        solid = gears.worm_solid(w["module"], w["starts"], w["diameter"], w["length"], w["pressure"], w["hand"],
                                 f.Bore.Value)
    else:
        e = effective(f)
        solid = gears.gear_solid(e["module"], e["teeth"], e["pressure"], e["helix"], e["hand"], f.Width.Value,
                                 f.Bore.Value)
        if is_gear(mate_of(f), "worm"):
            solid.translate(V(0, 0, -f.Width.Value / 2))  # a worm wheel: centred on the worm's axis
    turn = _turn(f)
    if turn:
        solid.rotate(V(), V(0, 0, 1), turn)
    if s.get("reverse"):
        solid.rotate(V(), V(1, 0, 0), 180)
    place = frame(f)
    body = f.getParentGeoFeatureGroup()
    if body is not None:
        place = body.getGlobalPlacement().inverse().multiply(place)
    m = place.multiply(solid.Placement).toMatrix()
    solid.Placement = App.Placement()
    solid.transformShape(m, True)  # into the geometry: a PartDesign feature drops a shape's own placement
    return solid


class GearFeature:
    """The proxy. FreeCAD saves its module path (incad.features.gear) and class name in the file."""
    def __init__(self, obj):
        for prop, kind in _PROPS:
            if prop not in obj.PropertiesList:
                obj.addProperty(kind, prop, "Gear", "")
        if MARK not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", MARK, "InCad", "The gear's settings")
            obj.setPropertyStatus(MARK, "Hidden")
        obj.Proxy = self

    def execute(self, obj):
        made = tool(obj)
        base = obj.BaseFeature
        if base is not None and not base.Shape.isNull() and base.Shape.Solids:
            shape = base.Shape.fuse(made)
            if len(shape.Solids) == 1:
                shape = shape.Solids[0]
        else:
            shape = made
        if hasattr(obj, "AddSubShape"):
            try:
                obj.AddSubShape = made
            except Exception:
                pass
        obj.Shape = shape

    def dumps(self):
        return None

    def loads(self, state):
        return None


# ----- the dialog -----
_WHERE = (Field("center", "Center", "pick", pick="gear_center", optional=True),
          Field("plane", "Plane", "pick", pick="gear_plane", optional=True),
          Field("axis", "Axis", "pick", pick="axis", optional=True))
_MOVE = tuple(Field(k, label, "value_length", default="0 mm", signed=True, show_if=("mesh", ("",)))
              for k, label in (("offset_x", "Offset X"), ("offset_y", "Offset Y"), ("offset_z", "Offset Z"))) + tuple(
    Field(k, label, "value_angle", default="0 °", signed=True, show_if=("mesh", ("",)))
    for k, label in (("rotate_x", "Rotate X"), ("rotate_y", "Rotate Y")))
_HAND = (("right", "Right"), ("left", "Left"))
# where round the gear it meshes with a gear (or worm) stands: degrees from that gear's X (a third gear beside a pair)
_AROUND = Field("around", "Around", "value_angle", default="0 °", signed=True, show_if=("mesh", bool))


def _no_mate(field):
    import dataclasses
    return dataclasses.replace(field, show_if=("mesh", ("",)))


GEAR_FIELDS = (
    Field("mesh", "Mesh with", "pick", pick="gear", optional=True), _AROUND,
    _no_mate(_WHERE[0]), _no_mate(_WHERE[1]), _no_mate(_WHERE[2]),
    Field("size", "Size by", "choice", (("teeth", "Teeth"), ("diameter", "Diameter"), ("ratio", "Ratio"),
                                        ("distance", "Center dist.")), "teeth"),
    Field("teeth", "Teeth", "value_length", default="20", show_if=("size", ("teeth",))),
    Field("diameter", "Pitch Ø", "value_length", default="40 mm", show_if=("size", ("diameter",))),
    Field("ratio", "Ratio", "value_length", default="2", show_if=("size", ("ratio",))),
    Field("distance", "Center distance", "value_length", default="60 mm", show_if=("size", ("distance",))),
    Field("module", "Module", "value_length", default="2 mm", show_if=("mesh", ("",))),
    Field("pressure", "Pressure angle", "value_angle", default="20 °", show_if=("mesh", ("",))),
    Field("helix", "Helix angle", "value_angle", default="0 °", show_if=("mesh", ("",))),
    Field("hand", "Hand", "choice", _HAND, "right", show_if=("mesh", ("",))),
    Field("width", "Width", "value_length", default="10 mm"),
    Field("bore", "Bore Ø", "value_length", default="0 mm"),
    Field("turn", "Turn", "value_angle", default="0 °", signed=True),
) + _MOVE + (
    Field("reverse", "Reverse", "bool", default=False),
)
WORM_FIELDS = (
    Field("mesh", "Mesh with", "pick", pick="gear", optional=True), _AROUND,
    _no_mate(_WHERE[2]), _no_mate(_WHERE[0]), _no_mate(_WHERE[1]),
    Field("module", "Module", "value_length", default="2 mm", show_if=("mesh", ("",))),
    Field("starts", "Starts", "value_length", default="1"),
    Field("diameter", "Pitch Ø", "value_length", default="20 mm"),
    Field("length", "Length", "value_length", default="40 mm"),
    Field("pressure", "Pressure angle", "value_angle", default="20 °", show_if=("mesh", ("",))),
    Field("hand", "Hand", "choice", _HAND, "right", show_if=("mesh", ("",))),
    Field("bore", "Bore Ø", "value_length", default="0 mm"),
    Field("turn", "Turn", "value_angle", default="0 °", signed=True),
) + _MOVE + (
    Field("reverse", "Reverse", "bool", default=False),
)
# value field -> (property, unit kind)
_GEAR_VALUES = {"teeth": ("Teeth", "count"), "diameter": ("PitchDiameter", "length"), "ratio": ("Ratio", "count"),
                "distance": ("CenterDistance", "length"), "module": ("Module", "length"),
                "pressure": ("PressureAngle", "angle"), "helix": ("HelixAngle", "angle"), "width": ("Width", "length"),
                "bore": ("Bore", "length"), "turn": ("Turn", "angle"), "offset_x": ("OffsetX", "length"),
                "offset_y": ("OffsetY", "length"), "offset_z": ("OffsetZ", "length"),
                "rotate_x": ("RotateX", "angle"), "rotate_y": ("RotateY", "angle"), "around": ("Around", "angle")}
_WORM_VALUES = {"module": ("Module", "length"), "starts": ("Starts", "count"), "diameter": ("PitchDiameter", "length"),
                "length": ("Length", "length"), "pressure": ("PressureAngle", "angle"), "bore": ("Bore", "length"),
                "turn": ("Turn", "angle"), "offset_x": ("OffsetX", "length"), "offset_y": ("OffsetY", "length"),
                "offset_z": ("OffsetZ", "length"), "rotate_x": ("RotateX", "angle"), "rotate_y": ("RotateY", "angle"),
                "around": ("Around", "angle")}


def _number(text):
    """The value a field says, for the info line only (a formula says nothing there; the feature takes every value
    through its parameter)."""
    try:
        return float(App.Units.Quantity((text or "0").strip()).Value)
    except Exception:
        return None


def _link(doc, text):
    picks = parse_picks(doc, text)
    if not picks:
        return None
    obj, sub = picks[0]
    if "." in sub:
        parts = sub.split(".")
        obj, sub = doc.getObject(parts[-2]) or obj, parts[-1]
    if obj.TypeId == "PartDesign::Body" and obj.Tip is not None:
        obj = obj.Tip
    return (obj, [sub] if sub else [""])


def _check_count(text, what):
    t = (text or "").strip()
    try:
        n = float(t)
    except ValueError:
        return  # a formula: its value is checked when the gear is built
    if n != int(n) or n < 1:
        raise ValueError("%s must be a whole number, 1 or more" % what)


def _apply(doc, f, values, kind):
    for prop, typ in _PROPS:  # a gear from an older file lacks the newer ones
        if prop not in f.PropertiesList:
            f.addProperty(typ, prop, "Gear", "")
    s = settings(f)
    s.update(kind=kind, hand=values.get("hand") or "right", reverse=bool(values.get("reverse")))
    mate = parse_picks(doc, values.get("mesh"))
    mate_obj = gear_of(*mate[0]) if mate else None
    if mate and mate_obj is None:
        raise ValueError("Mesh with: pick a Gear or a Worm")
    if kind == "worm" and mate_obj is not None and not is_gear(mate_obj, "gear"):
        raise ValueError("Mesh with: a worm meshes with a Gear (its wheel)")
    link = mate_obj
    while link is not None:  # never a loop: A meshes with B meshes with A
        if link is f:
            raise ValueError("%s meshes with this one already: pick another" % mate_obj.Label
                             if mate_obj is not f else "It can't mesh with itself")
        link = mate_of(link)
    f.MeshWith = mate_obj
    if kind == "gear":
        s["size"] = values.get("size") or "teeth"
        if s["size"] in ("ratio", "distance") and mate_obj is None:
            raise ValueError("Ratio and Center distance size a gear against the one it meshes with: pick Mesh with")
        _check_count(values.get("teeth"), "The number of teeth")
        if s["size"] == "ratio":
            try:
                ratio = float(values.get("ratio") or "2")
            except ValueError:
                ratio = None  # a formula: its value is checked when the gear is built
            if ratio is not None and ratio <= 0:
                raise ValueError("The ratio must be more than 0")
        table = _GEAR_VALUES
    else:
        _check_count(values.get("starts"), "The number of starts")
        table = _WORM_VALUES
    f.CenterRef = _link(doc, values.get("center"))
    f.PlaneRef = _link(doc, values.get("plane"))
    f.AxisRef = _link(doc, values.get("axis"))
    setattr(f, MARK, json.dumps(s))
    for key, (prop, unit) in table.items():
        default = next(x.default for x in (GEAR_FIELDS if kind == "gear" else WORM_FIELDS) if x.key == key)
        value_param(doc, f, prop, (values.get(key) or default).strip(), unit)


def _defaults(fields):
    def defaults(doc, body, profile=None):
        values = {x.key: x.default for x in fields}
        for x in fields:
            if x.kind == "pick":
                values[x.key] = ""
        return values
    return defaults


def _create(kind, name):
    def create(doc, body, profile, values):
        from incad.core import document, log
        target = body
        # a Solid of its own unless a place on the part was picked (a gear on its shaft joins it): meshing teeth
        # touch, a gear at the origin would run through what is there, and either way the part became one lump
        if body.Tip is not None and (values.get("mesh") or not (values.get("center") or values.get("plane")
                                                                 or values.get("axis"))):
            target = document.new_solid(doc)
        for attempt in (0, 1):
            tip = target.Tip
            f = target.newObject("PartDesign::FeaturePython", name)
            f.Label = name
            GearFeature(f)
            from incad.core import quality
            quality.draw_as_gear(f)
            f.BaseFeature = tip
            document.show_like(f, tip)
            try:
                _apply(doc, f, values, kind)
                target.Tip = f
                doc.recompute()
                document.show_like(f, tip)
                if "Invalid" in f.State:
                    raise ValueError(f.getStatusString() or "The %s could not be made" % name.lower())
            except Exception:
                target.removeObject(f)
                doc.removeObject(f.Name)
                target.Tip = tip
                doc.recompute()
                raise
            before = len(tip.Shape.Solids) if tip is not None and not tip.Shape.isNull() else 0
            if attempt or not before or len(f.Shape.Solids) <= before:
                return f
            # it touches nothing of the part: a piece of its own (a gear set beside another), as Extrude does
            target.removeObject(f)
            doc.removeObject(f.Name)
            target.Tip = tip
            document.show_result(target)
            target = document.new_solid(doc)
            log.report("info", "%s does not touch the part: made as its own Solid (%s)" % (name, target.Label))
        return f
    return create


def _update(kind):
    def update(doc, f, values):
        _apply(doc, f, values, kind)
        doc.recompute()
        return f
    return update


def _pick_text(link):
    obj, sub = _ref(link)
    return format_picks([(obj, sub)]) if obj is not None else ""


def _read(fields, table):
    def read(doc, f):
        s = settings(f)
        values = _defaults(fields)(doc, None)
        for key, (prop, _unit) in table.items():
            values[key] = value_text(doc, f, prop) or values[key]
        values.update(center=_pick_text(f.CenterRef), plane=_pick_text(f.PlaneRef),
                      axis=_pick_text(getattr(f, "AxisRef", None)), hand=s.get("hand") or "right",
                      reverse=bool(s.get("reverse")))
        values["mesh"] = f.MeshWith.Name if is_gear(f.MeshWith) else ""
        if "size" in values:
            values["size"] = s.get("size") or "teeth"
        return values
    return read


def _gear_info(values):
    doc = App.ActiveDocument
    mate = gear_of(*parse_picks(doc, values.get("mesh"))[0]) if doc is not None and values.get("mesh") else None
    try:
        if mate is not None:
            e = effective(mate)
            if is_gear(mate, "worm"):
                gamma = gears.lead_angle(e["module"], e["starts"], e["diameter"])
                module, helix = e["module"] * math.cos(math.radians(gamma)), gamma
            else:
                module, helix = e["module"], e["helix"]
        else:
            module, helix = _number(values.get("module")), _number(values.get("helix")) or 0.0
        size = values.get("size")
        theirs = _mate_size(mate) if mate is not None else None
        if size in ("ratio", "distance") and theirs is None:
            return "Ratio and Center distance need Mesh with: the gear this one meshes with"
        if size == "diameter":
            teeth = gears.teeth_for(_number(values.get("diameter")), module, helix)
        elif size == "ratio":
            teeth = max(gears.MIN_TEETH, int(round(_number(values.get("ratio")) * theirs[0])))
        elif size == "distance":
            teeth = gears.teeth_for(2 * _number(values.get("distance")) - theirs[1], module, helix)
        else:
            teeth = int(_number(values.get("teeth")))
        d, tip_d, root_d = (gears.pitch_diameter(module, teeth, helix), gears.tip_diameter(module, teeth, helix),
                            gears.root_diameter(module, teeth, helix))
    except Exception:
        return ""
    text = "%d teeth · pitch Ø %.4g · tip Ø %.4g · root Ø %.4g mm" % (teeth, d, tip_d, root_d)
    if theirs is not None:
        a = (d + theirs[1]) / 2
        text += " · ratio %d:%d = %.4g · centre distance %.4g mm" % (teeth, theirs[0], teeth / theirs[0], a)
        if size == "distance" and abs(a - _number(values.get("distance"))) > 1e-6:
            text += " (the nearest whole number of teeth)"
        if helix:
            text += " · helix %.3g° %s" % (helix, effective(mate)["hand"] if is_gear(mate, "worm") else
                                            ("left" if effective(mate)["hand"] == "right" else "right"))
    return text


def _worm_info(values):
    doc = App.ActiveDocument
    wheel = gear_of(*parse_picks(doc, values.get("mesh"))[0]) if doc is not None and values.get("mesh") else None
    try:
        n, d = int(_number(values.get("starts"))), _number(values.get("diameter"))
        if wheel is not None:
            e = effective(wheel)
            m = e["module"] / math.cos(math.radians(e["helix"]))
        else:
            m = _number(values.get("module"))
        gamma = gears.lead_angle(m, n, d)
        text = "Lead %.4g mm · lead angle %.3g° · tip Ø %.4g · root Ø %.4g mm" % (
            gears.lead(m, n), gamma, d + 2 * gears.ADDENDUM * m, d - 2 * gears.DEDENDUM * m)
        if wheel is None:
            return text + " — its wheel: Gear ▸ Mesh with this worm"
        text += " · module %.4g mm from %s" % (m, wheel.Label)
        if abs(gamma - e["helix"]) > 0.5:  # the wheel's teeth should lean as much as the thread
            text += " — for a true mesh give %s a helix angle of %.3g° (now %.3g°), or this worm another Ø" % (
                wheel.Label, gamma, e["helix"])
        return text
    except Exception:
        return ""


def _overlay(shell, session):
    """While the dialog is open: the assembly's gizmo on the gear — arrows to slide it, an arc to turn it."""
    from incad.ui.gear_gizmo import GearGizmo
    return GearGizmo(shell, session)


def _namespace(kind, title, fields, table, info):
    from types import SimpleNamespace
    return SimpleNamespace(
        RECREATE_ON=("mesh", "center", "plane", "axis"),  # where it goes decides its Solid: made again there
        overlay=_overlay,
        __name__="incad.features.%s" % kind, TITLE=title, ICON=kind, TYPES=TYPES, NEEDS_PROFILE=False,
        PROPS={key: prop for key, (prop, _unit) in table.items()}, FIELDS=fields,
        claims=lambda f, k=kind: is_gear(f, k), defaults=_defaults(fields), create=_create(kind, title),
        read=_read(fields, table), update=_update(kind), info=info, _pad_of=lambda f: f)


GEAR = _namespace("gear", "Gear", GEAR_FIELDS, _GEAR_VALUES, _gear_info)  # one gear (older files, tests)
WORM = _namespace("worm", "Worm", WORM_FIELDS, _WORM_VALUES, _worm_info)


# ----- sets: Spur Gears and Worm Gear (Inventor's Design Accelerator) -----
# One dialog makes both: the first (the "lead": a gear, or the worm) and the second, a Gear that meshes with it in a
# Solid of its own (its settings name the lead). Double-click either one: the set's dialog. Make Assembly puts each
# in a part file of its own (bake).
def _head(key, label, show_if=None):
    return Field(key, label, "heading", show_if=show_if)


_PAIR = (("pair", "Pair"), ("one", "One gear"))
# bores by default (the user: a worm without one gave no axis to pick and work on): 8 mm in the first, 10 mm in the
# second — inside the default sizes' roots (Ø 35, 55; the worm's Ø 15)
_SECOND_SIZE = (("teeth", "Teeth"), ("ratio", "Ratio"), ("distance", "Center dist."))
_SECOND = (
    Field("size2", "Size by", "choice", _SECOND_SIZE, "ratio", show_if=("make", ("pair",))),
    Field("teeth2", "Teeth", "value_length", default="30", show_if=(("make", ("pair",)), ("size2", ("teeth",)))),
    Field("ratio", "Ratio", "value_length", default="1.5", show_if=(("make", ("pair",)), ("size2", ("ratio",)))),
    Field("distance", "Center distance", "value_length", default="50 mm",
          show_if=(("make", ("pair",)), ("size2", ("distance",)))),
    Field("width2", "Width", "value_length", default="10 mm", show_if=("make", ("pair",))),
    Field("bore2", "Bore Ø", "value_length", default="10 mm", show_if=("make", ("pair",))),
    Field("around2", "Around gear 1", "value_angle", default="0 °", signed=True, show_if=("make", ("pair",))),
)
# a set whose first meshes with a gear already there stands where that gear puts it: no place of its own
_PLACE = (_head("h_place", "Place (optional)", show_if=("mesh", ("",))), _no_mate(_WHERE[2]), _no_mate(_WHERE[0]),
          _no_mate(_WHERE[1]), Field("turn", "Turn", "value_angle", default="0 °", signed=True)) + tuple(
    Field(k, label, "value_length", default="0 mm", signed=True, show_if=("mesh", ("",)))
    for k, label in (("offset_x", "Offset X"), ("offset_y", "Offset Y"), ("offset_z", "Offset Z"))) + tuple(
    Field(k, label, "value_angle", default="0 °", signed=True, show_if=("mesh", ("",)))
    for k, label in (("rotate_x", "Rotate X"), ("rotate_y", "Rotate Y"))) + (
    Field("reverse", "Reverse", "bool", default=False),)
_FREE = ("mesh", ("",))  # sizes a mate decides

PAIR_FIELDS = (
    _head("h_both", "Both gears"),
    Field("make", "Make", "choice", _PAIR, "pair"),
    Field("mesh", "Mesh with", "pick", pick="gear", optional=True),  # gear 1 with a gear already there
    dataclasses.replace(_AROUND, label="Around it"),
    Field("module", "Module", "value_length", default="2 mm", show_if=_FREE),
    Field("pressure", "Pressure angle", "value_angle", default="20 °", show_if=_FREE),
    Field("helix", "Helix angle", "value_angle", default="0 °", show_if=_FREE),
    Field("hand", "Hand (gear 1)", "choice", _HAND, "right", show_if=_FREE),
    _head("h_first", "Gear 1"),
    Field("teeth", "Teeth", "value_length", default="20"),
    Field("width", "Width", "value_length", default="10 mm"),
    Field("bore", "Bore Ø", "value_length", default="8 mm"),
    _head("h_second", "Gear 2", show_if=("make", ("pair",))),
) + _SECOND + _PLACE

WORMSET_FIELDS = (
    _head("h_worm", "Worm"),
    Field("make", "Make", "choice", (("pair", "Worm and wheel"), ("one", "Worm only")), "pair"),
    Field("mesh", "Mesh with", "pick", pick="gear", optional=True),  # the worm with a gear already there
    dataclasses.replace(_AROUND, label="Around it"),
    Field("module", "Module (axial)", "value_length", default="2 mm", show_if=_FREE),
    Field("pressure", "Pressure angle", "value_angle", default="20 °", show_if=_FREE),
    Field("hand", "Hand", "choice", _HAND, "right", show_if=_FREE),
    Field("starts", "Starts", "value_length", default="1"),
    Field("diameter", "Pitch Ø", "value_length", default="20 mm"),
    Field("length", "Length", "value_length", default="40 mm"),
    Field("bore", "Bore Ø", "value_length", default="8 mm"),
    _head("h_second", "Wheel", show_if=("make", ("pair",))),
) + tuple(f if f.key != "ratio" else Field("ratio", "Ratio", "value_length", default="30",
                                            show_if=(("make", ("pair",)), ("size2", ("ratio",))))
    for f in _SECOND if f.key != "around2") + _PLACE  # a wheel sits across its worm: no Around

_PLACE_KEYS = ("turn", "offset_x", "offset_y", "offset_z", "rotate_x", "rotate_y", "center", "plane", "axis",
               "reverse", "mesh", "around")
_LEAD_KEYS = {"gear": ("module", "pressure", "helix", "hand", "teeth", "width", "bore") + _PLACE_KEYS,
              "worm": ("module", "pressure", "hand", "starts", "diameter", "length", "bore") + _PLACE_KEYS}


def lead_of(f):
    """The set's first member for either of its two (a gear on its own: itself)."""
    name = settings(f).get("leader") if f is not None else None
    lead = f.Document.getObject(name) if name else None
    return lead if is_gear(lead) else f


def second_of(lead):
    """The gear that meshes with `lead` as the second of its set, or None."""
    if lead is None:
        return None
    return next((o for o in lead.Document.Objects if is_gear(o) and o is not lead
                 and settings(o).get("leader") == lead.Name), None)


def _lead_values(kind, values):
    out = (GEAR if kind == "gear" else WORM).defaults(None, None)
    out.update({k: values[k] for k in _LEAD_KEYS[kind] if k in values})
    out["size"] = "teeth"
    return out


def _second_values(lead, values):
    out = GEAR.defaults(None, None)
    out.update(mesh=lead.Name, size=values.get("size2") or "ratio", teeth=values.get("teeth2") or "30",
               ratio=values.get("ratio") or "1.5", distance=values.get("distance") or "50 mm",
               width=values.get("width2") or "10 mm", bore=values.get("bore2") or "10 mm",
               around=values.get("around2") or "0 °")
    return out


def _mark_second(lead, second):
    s = settings(second)
    s["leader"] = lead.Name
    setattr(second, MARK, json.dumps(s))


def _set_create(kind, title):
    def create(doc, body, profile, values):
        lead = _create(kind, "Gear" if kind == "gear" else "Worm")(doc, body, profile, _lead_values(kind, values))
        if values.get("make", "pair") == "pair":
            second = _create("gear", "Gear")(doc, body, profile, _second_values(lead, values))
            _mark_second(lead, second)
        return lead
    return create


def _remove_gear(doc, g):
    """Take a set's second gear away, with the Solid it had to itself."""
    body = g.getParentGeoFeatureGroup()
    if body is not None:
        if body.Tip is g:
            body.Tip = g.BaseFeature
        body.removeObject(g)
    doc.removeObject(g.Name)
    if body is not None and not [o for o in body.Group if o.TypeId != "App::Origin"]:
        for o in list(body.Group):
            body.removeObject(o)
            doc.removeObject(o.Name)
        doc.removeObject(body.Name)


def _set_update(kind):
    def update(doc, f, values):
        lead = lead_of(f)
        _apply(doc, lead, _lead_values(kind, values), kind)
        second = second_of(lead)
        if values.get("make", "pair") == "pair":
            if second is None:
                body = lead.getParentGeoFeatureGroup()
                second = _create("gear", "Gear")(doc, body, None, _second_values(lead, values))
                _mark_second(lead, second)
            else:
                _apply(doc, second, _second_values(lead, values), "gear")
        elif second is not None:
            _remove_gear(doc, second)
        doc.recompute()
        return f
    return update


def _set_read(kind, fields):
    def read(doc, f):
        lead = lead_of(f)
        values = {x.key: x.default for x in fields if x.kind != "heading"}
        values.update({x.key: "" for x in fields if x.kind == "pick"})
        own = (GEAR if kind == "gear" else WORM).read(doc, lead)
        values.update({k: own[k] for k in _LEAD_KEYS[kind] if k in own})
        second = second_of(lead)
        values["make"] = "pair" if second is not None else "one"
        if second is not None:
            sv = GEAR.read(doc, second)
            values.update(size2=sv.get("size") or "ratio", teeth2=sv["teeth"], ratio=sv["ratio"],
                          distance=sv["distance"], width2=sv["width"], bore2=sv["bore"], around2=sv["around"])
        return values
    return read


def _pair_info(values):
    try:
        m, helix = _number(values.get("module")), _number(values.get("helix")) or 0.0
        z1 = int(_number(values.get("teeth")))
        d1 = gears.pitch_diameter(m, z1, helix)
        text = "Gear 1: %d teeth, pitch Ø %.4g mm" % (z1, d1)
        if values.get("make") != "pair":
            return text + " · tip Ø %.4g mm" % gears.tip_diameter(m, z1, helix)
        size = values.get("size2")
        if size == "ratio":
            z2 = max(gears.MIN_TEETH, int(round(_number(values.get("ratio")) * z1)))
        elif size == "distance":
            z2 = gears.teeth_for(2 * _number(values.get("distance")) - d1, m, helix)
        else:
            z2 = int(_number(values.get("teeth2")))
        d2 = gears.pitch_diameter(m, z2, helix)
        a = (d1 + d2) / 2
        text += " · Gear 2: %d teeth, pitch Ø %.4g mm · ratio %d:%d = %.4g · centre distance %.4g mm" % (
            z2, d2, z2, z1, z2 / z1, a)
        if size == "distance" and abs(a - _number(values.get("distance"))) > 1e-6:
            text += " (the nearest whole number of teeth)"
        return text
    except Exception:
        return ""


def _wormset_info(values):
    try:
        m, n, d = _number(values.get("module")), int(_number(values.get("starts"))), _number(values.get("diameter"))
        gamma = gears.lead_angle(m, n, d)
        text = "Worm: lead %.4g mm, lead angle %.3g°, tip Ø %.4g mm" % (gears.lead(m, n), gamma, d + 2 * m)
        if values.get("make") != "pair":
            return text
        size = values.get("size2")
        if size == "ratio":
            z2 = max(gears.MIN_TEETH, int(round(_number(values.get("ratio")) * n)))
        elif size == "distance":
            z2 = gears.teeth_for(2 * _number(values.get("distance")) - d, m * math.cos(math.radians(gamma)), gamma)
        else:
            z2 = int(_number(values.get("teeth2")))
        d2 = m * z2
        return text + " · Wheel: %d teeth, pitch Ø %.4g mm · ratio %d:%d = %.4g · centre distance %.4g mm" % (
            z2, d2, z2, n, z2 / n, (d + d2) / 2)
    except Exception:
        return ""


def _clear_of_gears(doc, kind):
    """Offset X for a new set: beside the gears the document has already (the user: a third gear landed in the
    pair). Gear 2 goes on gear 1's +X, so past their right side is clear."""
    right = None
    for o in (doc.Objects if doc is not None else ()):
        if not is_gear(o):
            continue
        try:
            bb = o.Shape.BoundBox  # in its Solid, with what came before it there
            body = o.getParentGeoFeatureGroup()
            if body is not None:
                bb = bb.transformed(body.getGlobalPlacement().toMatrix())
        except Exception:
            continue
        if bb.isValid():
            right = bb.XMax if right is None else max(right, bb.XMax)
    if right is None:
        return "0 mm"
    reach = gears.tip_diameter(2.0, 20, 0.0) / 2 if kind == "gear" else (20.0 + 2 * gears.ADDENDUM * 2.0) / 2
    return "%g mm" % (math.ceil((right + reach + 5.0) / 10.0) * 10.0)


def _set_defaults(kind, fields):
    def defaults(doc, body, profile=None):
        values = {x.key: ("" if x.kind == "pick" else x.default) for x in fields if x.kind != "heading"}
        values["offset_x"] = _clear_of_gears(doc, kind)
        return values
    return defaults


def _set_namespace(kind, title, fields, info):
    from types import SimpleNamespace
    table = _GEAR_VALUES if kind == "gear" else _WORM_VALUES
    return SimpleNamespace(
        RECREATE_ON=("make", "center", "plane", "axis"), overlay=_overlay,
        LOCKED_WHEN_EDITING=(),  # the user: editing a set had its fields greyed out; update adds / drops the second
        __name__="incad.features.%s" % kind, TITLE=title, ICON=kind, TYPES=TYPES, NEEDS_PROFILE=False,
        PROPS={key: prop for key, (prop, _unit) in table.items() if key in _LEAD_KEYS[kind]}, FIELDS=fields,
        claims=lambda f, k=kind: is_gear(lead_of(f), k),
        defaults=_set_defaults(kind, fields),
        create=_set_create(kind, title), read=_set_read(kind, fields), update=_set_update(kind), info=info,
        _pad_of=lambda f: lead_of(f))


SPUR_GEARS = _set_namespace("gear", "Spur Gears", PAIR_FIELDS, _pair_info)
WORM_GEAR = _set_namespace("worm", "Worm Gear", WORMSET_FIELDS, _wormset_info)
KINDS = [SPUR_GEARS, WORM_GEAR]
