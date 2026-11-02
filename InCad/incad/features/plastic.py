"""Tools for printed plastic parts (the user picked them from SolidCraft's list: "Snap Fit, Lip, Boss"):

- Boss: a post for a screw or a heat-set insert at each point or circle centre of a sketch (as Hole takes its
  centres): outer Ø, height, a plain hole or an insert's hole, a fillet round its foot, ribs (gussets) to the floor.
- Lip / Groove: the rim of a shelled box gets a raised band along its inner or outer edge (Lip), or loses one
  (Groove: the band widened by the clearance and cut a little deeper, so the other half's lip goes in).
- Snap Fit: a strip grown onto the face of a box's wall at each line of a sketch (drawn on the rim along that edge),
  from down the wall to over the rim, a catch at its top: its height over the rim, how far down the wall, thickness,
  the catch's depth and length, a fillet where it leaves the rim.

Each one is a step of the part (a PartDesign::FeaturePython) that computes its solid from its values on every
recompute and joins it to the part (a Groove cuts), so it follows the sketch or the rim when they change. The rim is
found again by its plane and place, never by a face's number (they change when the part does)."""
import json
import math

import FreeCAD as App
import Part

from incad.features.common import Field, fine_step, format_picks, parse_picks, resolve, value_param, value_text
from incad.threads import inserts
from incad.core import offset2d

TYPES = ("PartDesign::FeaturePython",)
MARK = "InCadPlastic"  # its settings (JSON): which tool, and its choices
V = App.Vector
_E = 0.01  # mm a solid starts inside the part, so the join is clean


def settings(f):
    try:
        return json.loads(getattr(f, MARK, "") or "{}")
    except ValueError:
        return {}


def is_plastic(f, kind=None):
    return f is not None and MARK in getattr(f, "PropertiesList", ()) and \
        (kind is None or settings(f).get("kind") == kind)


def _local(f, placement):
    """A global placement in the coordinates of the body the feature is in (where its shapes are)."""
    body = f.getParentGeoFeatureGroup()
    return body.getGlobalPlacement().inverse().multiply(placement) if body is not None else placement


def _placed(shape, placement):
    """The shape moved into the geometry (a PartDesign feature drops a shape's own placement)."""
    m = placement.multiply(shape.Placement).toMatrix()
    shape.Placement = App.Placement()
    shape.transformShape(m, True)
    return shape


def _fillet_ring(r, f):
    """The fillet round the foot of a round post of radius r: a ring of radius f, as a solid (no edges picked)."""
    if f <= 0:
        return None
    box = Part.makeCylinder(r + f, f, V(0, 0, 0)).cut(Part.makeCylinder(r - _E, f, V(0, 0, 0)))
    torus = Part.makeTorus(r + f, f, V(0, 0, f))
    return box.cut(torus)


# ----- Boss -----
def boss_solid(values, centres, inside=None):
    """values: diameter, height, hole ('none' | 'plain' | 'insert'), hole_d, hole_depth (0 = the whole boss), insert,
    fillet, ribs, rib_thick, rib_length. The bosses at `centres` (sketch xy), standing on z = 0 along +z. `inside`:
    the part's box in the same frame: ribs stop at it (a boss beside a wall ran its rib out through the wall)."""
    r = values["diameter"] / 2.0
    h = values["height"]
    if r <= 0 or h <= 0:
        raise ValueError("The boss needs a diameter and a height above 0")
    hole = values["hole"]
    if hole == "insert":
        ins = inserts.named(values["insert"])
        hole_r, hole_depth = ins.hole / 2.0, inserts.depth(ins)
        if r < inserts.boss_diameter(ins) / 2.0 - 1e-6:
            raise ValueError("A boss for an %s insert needs Outer Ø %g mm or more" % (ins.name, inserts.boss_diameter(ins)))
    elif hole == "plain":
        hole_r, hole_depth = values["hole_d"] / 2.0, values["hole_depth"] or h
        if hole_r >= r:
            raise ValueError("The hole must be narrower than the boss")
    else:
        hole_r, hole_depth = 0.0, 0.0
    one = Part.makeCylinder(r, h + _E, V(0, 0, -_E))
    ring = _fillet_ring(r, values["fillet"])
    if ring is not None:
        one = one.fuse(ring)
    ribs = int(values["ribs"])
    skip = set(values.get("rib_skip") or ())  # the user: a rib with no room for it can be left out
    turn = values.get("rib_angle") or 0.0  # 0: rib 1 along the sketch's x axis (square to a box's walls)
    rib_set = None
    if ribs and len(skip & set(range(1, ribs + 1))) < ribs:
        t, length = values["rib_thick"], values["rib_length"]
        top = max(h * 0.75, min(h, values["fillet"] * 2))
        tri = Part.makePolygon([V(r * 0.5, 0, -_E), V(r + length, 0, -_E), V(r * 0.5, 0, top), V(r * 0.5, 0, -_E)])
        rib = Part.Face(tri).extrude(V(0, t, 0))
        rib.translate(V(0, -t / 2.0, 0))
        rib_set = []
        for k in range(ribs):
            if k + 1 in skip:
                continue
            piece = rib.copy()
            piece.rotate(V(), V(0, 0, 1), turn + 360.0 * k / ribs)
            rib_set.append(piece)
        rib_set = Part.makeCompound(rib_set)
    drill = Part.makeCylinder(hole_r, hole_depth + _E, V(0, 0, h - hole_depth)) if hole_r > 0 else None
    pieces = []
    for c in centres:
        p = one.copy()
        p.translate(V(c.x, c.y, 0))
        if rib_set is not None:
            r_set = rib_set.copy()
            r_set.translate(V(c.x, c.y, 0))
            if inside is not None:
                r_set = r_set.common(inside)
            if r_set.Solids:
                p = p.fuse(r_set.Solids)
        if drill is not None:
            d = drill.copy()
            d.translate(V(c.x, c.y, 0))
            p = p.cut(d)
        pieces.append(p)
    if not pieces:
        raise ValueError("The sketch has no points or circles for the bosses")
    return pieces


def _centres(sk):
    out = []
    for i, g in enumerate(sk.Geometry):
        if sk.getConstruction(i):
            continue
        if g.TypeId in ("Part::GeomCircle", "Part::GeomArcOfCircle"):
            out.append(V(g.Center))
        elif g.TypeId == "Part::GeomPoint":
            out.append(V(g.X, g.Y, 0))
    seen = []
    for c in out:  # a circle and its centre point: one boss
        if all((c - q).Length > 1e-6 for q in seen):
            seen.append(c)
    return seen


def _lines(sk):
    return [g for i, g in enumerate(sk.Geometry) if not sk.getConstruction(i) and g.TypeId == "Part::GeomLineSegment"]


# ----- Snap Fit -----
def _across_wall(support, mid, right):
    """How far the wall's top reaches from `mid` towards -right and +right (a, b), or None off the wall."""
    z = support.BoundBox.ZMin
    m = V(mid.x, mid.y, z)
    probe = Part.LineSegment(m - right * 500, m + right * 500).toShape()
    try:
        pieces = probe.common(support).Edges
    except Exception:
        return None
    for e in pieces:
        if e.distToShape(Part.Vertex(m))[0] < 1e-4:
            ends = [(v.Point - m).dot(right) for v in e.Vertexes]
            return -min(ends), max(ends)
    return None


def hook_solid(values, line, support, bounds=None):
    """values: length (above the rim), overlap (down the wall), thick, catch_depth, catch_length, fillet, flip_side.
    The user's pick of three: a strip grown onto the face of the wall at the edge the line runs along, from `overlap`
    down the wall to `length` over the rim, the catch at its top reaching back over the rim (Catch on the other side:
    away from it); its lower edge chamfered 45° over its thickness (reinforces the joint, prints without supports),
    a fillet where it leaves the rim. `support`:
    the rim (the part just under the sketch) as a flat face; `bounds`: a box it is kept inside (the part's)."""
    a, b = V(line.StartPoint), V(line.EndPoint)
    a.z = b.z = 0
    width = (b - a).Length
    L, down, t = values["length"], values["overlap"], values["thick"]
    ch = t  # the lower edge chamfered 45° over the whole thickness: reinforces the joint, prints without supports
    d, cl, fr = values["catch_depth"], values["catch_length"], values["fillet"]
    if width <= 1e-6:
        raise ValueError("A line of the snap fit's sketch has no length")
    if min(L, t) <= 0 or down < 0 or d < 0 or cl < 0 or cl >= L:
        raise ValueError("Snap Fit: the catch must be shorter than the height above the rim, every size above 0")
    along = (b - a).normalize()
    right = along.cross(V(0, 0, 1))
    reach = _across_wall(support, (a + b) * 0.5, right) if support is not None else None
    if reach is None:
        raise ValueError("Snap Fit: draw each line on the top of a wall, along the edge the snap fit goes on")
    ra, rb = reach
    if fr >= ra + rb:
        raise ValueError("Snap Fit: the fillet must be thinner than the wall (%g mm)" % (ra + rb))
    # the nearer edge is the line's: the strip goes on that face, u away from the wall
    if rb <= ra:
        origin, u_dir, y_dir = a + right * rb, right, along
    else:  # x, y, z right-handed: the line walked from its other end
        origin, u_dir, y_dir = a - right * ra + along * width, right * -1, along * -1
    # the side profile (u across, z up): the strip, its sloped lower end, the catch at its top
    low = -down
    catch = cl > 1e-6 and d > 1e-6
    if values.get("flip_side"):
        pts = [V(0, 0, low - ch), V(t, 0, low)] + ([V(t, 0, L - cl), V(t + d, 0, L - cl)] if catch else []) + \
            [V(t, 0, L), V(0, 0, L)]
    else:
        pts = [V(0, 0, low - ch), V(t, 0, low), V(t, 0, L), V(0, 0, L)] + \
            ([V(-d, 0, L - cl), V(0, 0, L - cl)] if catch else [])
    pts.append(pts[0])
    hook = Part.Face(Part.makePolygon(pts)).extrude(V(0, width, 0))
    if fr > 0:  # where it leaves the rim: a fillet on the rim's top against the strip
        square = Part.makeBox(fr + _E, width, fr, V(-fr, 0, -_E))
        hook = hook.fuse(square.cut(Part.makeCylinder(fr, width, V(-fr, 0, fr), V(0, 1, 0))))
    frame = App.Placement(origin, App.Rotation(u_dir, y_dir, V(0, 0, 1), "ZXY"))
    hook = _placed(hook.removeSplitter(), frame)
    if bounds is not None:
        hook = hook.common(bounds)  # never out under the part's bottom
    return hook.Solids[0] if len(hook.Solids) == 1 else hook


def _support(base, pl, down):
    """The part's material just under the sketch plane, as a flat face at z ~ 0 in the sketch's coordinates (the side
    the hooks stand on: `down` = -1 under it, +1 above it when flipped), or None."""
    if base is None or base.Shape.isNull() or not base.Shape.Solids:
        return None
    local = base.Shape.copy()
    local.Placement = pl.inverse().multiply(local.Placement)
    try:
        wires = local.slice(V(0, 0, 1), 0.01 * down)
        if not wires:
            return None
        face = Part.makeFace(wires, "Part::FaceMakerBullseye")  # at z = -0.01, where the hooks start
        if down > 0:
            face = face.mirror(V(), V(0, 0, 1))
        return face if face.Area > 1e-9 else None
    except Exception:
        return None


# ----- Lip / Groove -----
def _plane_of(face):
    n = face.normalAt(*face.Surface.parameter(face.Edges[0].Vertexes[0].Point)) if face.Edges else face.normalAt(0, 0)
    n = V(n).normalize()
    c = face.OuterWire.BoundBox.Center
    return n, n.dot(V(face.Vertexes[0].Point)), c


def find_rim(shape, normal, offset, centre):
    """The flat face of `shape` in the stored plane whose outer edge's middle is nearest the stored one."""
    best = None
    for face in shape.Faces:
        if face.Surface.TypeId != "Part::GeomPlane":
            continue
        n, off, c = _plane_of(face)
        if n.dot(normal) < 1 - 1e-6 or abs(off - offset) > 1e-4:
            continue
        dist = (c - centre).Length
        if best is None or dist < best[0]:
            best = (dist, face)
    if best is None:
        raise ValueError("The rim face is gone: pick it again")
    return best[1]


def _band(face, edge, width):
    """The band of the rim along its inner edges (every hole of the rim face) or its outer edge, `width` wide."""
    holes = [w for w in face.Wires if not w.isSame(face.OuterWire)]
    if edge == "inside":
        if not holes:
            raise ValueError("The rim has no inside edge: shell the part first, or pick Outside")
        bands = []
        for w in holes:
            inner = Part.Face(w)
            bands.append(offset2d.offset2d(inner, width, 2, False, False).cut(inner))  # corners stay as sharp as the box's
        band = bands[0]
        for b in bands[1:]:
            band = band.fuse(b)
        return band
    outer = Part.Face(face.OuterWire)
    return outer.cut(offset2d.offset2d(outer, -width, 2, False, False))


def lip_solid(face, values):
    """values: type ('lip' | 'groove'), edge ('inside' | 'outside'), width, height, clearance."""
    w, h, c = values["width"], values["height"], values["clearance"]
    if w <= 0 or h <= 0:
        raise ValueError("Lip / Groove: Width and Height above 0")
    n, _off, _c = _plane_of(face)
    if values["type"] == "groove":
        band = _band(face, values["edge"], w + c)
        band.translate(n * _E)
        return band.extrude(n * -(h + c + _E))
    band = _band(face, values["edge"], w)
    band.translate(n * -_E)
    return band.extrude(n * (h + _E))


# ----- the feature -----
_PROPS = (("Sketch", "App::PropertyLink"), ("Diameter", "App::PropertyLength"), ("Height", "App::PropertyLength"),
          ("HoleDiameter", "App::PropertyLength"), ("HoleDepth", "App::PropertyLength"),
          ("FilletRadius", "App::PropertyLength"), ("Ribs", "App::PropertyInteger"),
          ("RibThickness", "App::PropertyLength"), ("RibLength", "App::PropertyLength"),
          ("Length", "App::PropertyLength"), ("Thickness", "App::PropertyLength"), ("Overlap", "App::PropertyLength"),
          ("CatchDepth", "App::PropertyLength"), ("CatchLength", "App::PropertyLength"),
          ("Width", "App::PropertyLength"), ("Clearance", "App::PropertyLength"),
          ("RimNormal", "App::PropertyVector"), ("RimOffset", "App::PropertyFloat"),
          ("RimCentre", "App::PropertyVector"))


def _sketch_frame(f):
    sk = f.Sketch
    return sk, _local(f, sk.getGlobalPlacement())


def tool(f):
    """(the solid the feature adds or cuts, 'join' | 'cut'), in the body's coordinates."""
    s = settings(f)
    kind = s.get("kind")
    if kind == "lip":
        base = f.BaseFeature
        if base is None or base.Shape.isNull():
            raise ValueError("Lip / Groove needs a part to put it on")
        face = find_rim(base.Shape, V(f.RimNormal), f.RimOffset, V(f.RimCentre))
        values = dict(type=s.get("type") or "lip", edge=s.get("edge") or "inside", width=f.Width.Value,
                      height=f.Height.Value, clearance=f.Clearance.Value)
        return lip_solid(face, values), ("cut" if values["type"] == "groove" else "join")
    if f.Sketch is None:
        raise ValueError("Pick the sketch")
    sk, pl = _sketch_frame(f)
    if kind == "boss":
        values = dict(diameter=f.Diameter.Value, height=f.Height.Value, hole=s.get("hole") or "none",
                      hole_d=f.HoleDiameter.Value, hole_depth=f.HoleDepth.Value if s.get("hole_to") == "depth" else 0,
                      insert=s.get("insert") or inserts.DEFAULT, fillet=f.FilletRadius.Value, ribs=f.Ribs,
                      rib_thick=f.RibThickness.Value, rib_length=f.RibLength.Value,
                      rib_angle=_degrees(s.get("rib_angle")), rib_skip=_numbers(s.get("rib_skip")))
        inside = None
        base = f.BaseFeature
        if base is not None and not base.Shape.isNull():
            local = base.Shape.copy()
            local.Placement = pl.inverse().multiply(local.Placement)
            bb = local.BoundBox
            inside = Part.makeBox(bb.XLength, bb.YLength, bb.ZLength + 2 * values["height"] + 1,
                                  V(bb.XMin, bb.YMin, bb.ZMin - values["height"] - 0.5))
        pieces = boss_solid(values, _centres(sk), inside)
    else:
        values = dict(length=f.Length.Value, overlap=getattr(f, "Overlap", None) and f.Overlap.Value or 0.0,
                      thick=f.Thickness.Value, catch_depth=f.CatchDepth.Value, catch_length=f.CatchLength.Value,
                      fillet=f.FilletRadius.Value, flip_side=s.get("flip_side"))
        lines = _lines(sk)
        if not lines:
            raise ValueError("The sketch has no lines for the snap fits: each line is where one goes")
        down = 1 if s.get("flip") else -1
        support = _support(f.BaseFeature, pl, down)  # the rim under the lines (flipped: over the sketch, mirrored)
        bounds = None
        if f.BaseFeature is not None and not f.BaseFeature.Shape.isNull():
            local = f.BaseFeature.Shape.copy()
            local.Placement = pl.inverse().multiply(local.Placement)
            if down > 0:
                local = local.mirror(V(), V(0, 0, 1))
            bb = local.BoundBox
            bounds = Part.makeBox(bb.XLength + 200, bb.YLength + 200, bb.ZLength + 200 + values["length"],
                                  V(bb.XMin - 100, bb.YMin - 100, bb.ZMin))
        pieces = [hook_solid(values, g, support, bounds) for g in lines]
    solid = pieces[0] if len(pieces) == 1 else Part.makeCompound(pieces)
    if s.get("flip"):  # the other side of the sketch, on the same points and lines: a mirror in its plane (a half
        solid = solid.mirror(V(), V(0, 0, 1))  # turn about its x axis sent them to the mirror place across it too)
    return _placed(solid, pl), "join"


def _degrees(text):
    try:
        return App.Units.Quantity(str(text or "0").replace("°", " deg")).Value
    except Exception:
        return 0.0


def _numbers(text):
    return {int(n) for n in str(text or "").split(",") if n.strip().isdigit()}


def _fused(base, made):
    shape = base.fuse(made) if not isinstance(made, Part.Compound) else base.fuse(made.Solids)
    shape = shape.removeSplitter()
    return shape.Solids[0] if len(shape.Solids) == 1 else shape


class PlasticFeature:
    """The proxy. FreeCAD saves its module path (incad.features.plastic) and class name in the file."""
    def __init__(self, obj):
        for prop, kind in _PROPS:
            if prop not in obj.PropertiesList:
                obj.addProperty(kind, prop, "Plastic", "")
        for prop in ("RimNormal", "RimOffset", "RimCentre"):
            obj.setPropertyStatus(prop, "Hidden")
        if MARK not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", MARK, "InCad", "The tool's settings")
            obj.setPropertyStatus(MARK, "Hidden")
        obj.Proxy = self

    def execute(self, obj):
        made, op = tool(obj)
        base = obj.BaseFeature
        has_base = base is not None and not base.Shape.isNull() and base.Shape.Solids
        if op == "cut":
            shape = base.Shape.cut(made)
            shape = shape.Solids[0] if len(shape.Solids) == 1 else shape
        elif has_base:
            shape = _fused(base.Shape, made)
        else:
            shape = made.fuse(made.Solids[1:]) if isinstance(made, Part.Compound) and len(made.Solids) > 1 else made
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


# ----- the dialogs -----
_FLIP = Field("flip", "Flip direction", "bool", default=False)
BOSS_FIELDS = (
    Field("sketch", "Sketch", "pick", pick="sketch"),
    Field("diameter", "Outer Ø", "value_length", default="9 mm"),
    Field("height", "Height", "value_length", default="10 mm"),
    Field("hole", "Hole", "choice", (("none", "None"), ("plain", "Plain"), ("insert", "Insert")), "insert"),
    Field("insert", "Insert", "combo", inserts.choices(), inserts.DEFAULT, show_if=("hole", ("insert",))),
    Field("hole_d", "Hole Ø", "value_length", default="2.5 mm", show_if=("hole", ("plain",))),
    Field("hole_to", "Hole depth", "choice", (("boss", "Whole boss"), ("depth", "Depth")), "boss",
          show_if=("hole", ("plain",))),
    Field("hole_depth", "Depth", "value_length", default="6 mm", show_if=(("hole", ("plain",)), ("hole_to", ("depth",)))),
    Field("fillet", "Fillet at foot", "value_length", default="0.5 mm", step=fine_step),
    Field("ribs", "Ribs", "choice", (("0", "None"), ("3", "3"), ("4", "4"), ("6", "6")), "0"),
    Field("rib_thick", "Rib thickness", "value_length", default="1.2 mm", show_if=("ribs", ("3", "4", "6"))),
    Field("rib_length", "Rib length", "value_length", default="4 mm", show_if=("ribs", ("3", "4", "6"))),
    Field("rib_angle", "Rib angle", "value_angle", default="0°", show_if=("ribs", ("3", "4", "6"))),
    Field("rib_skip", "Leave out", "toggles", tuple((str(i), str(i)) for i in range(1, 7)), "",
          show_if=("ribs", ("3", "4", "6"))),
    _FLIP,
)
SNAP_FIELDS = (
    Field("sketch", "Sketch", "pick", pick="sketch"),
    Field("length", "Above the rim", "value_length", default="10 mm"),
    Field("overlap", "Down the wall", "value_length", default="8 mm"),
    Field("thick", "Thickness", "value_length", default="1.5 mm"),
    Field("catch_depth", "Catch depth", "value_length", default="1 mm"),
    Field("catch_length", "Catch length", "value_length", default="2.5 mm"),
    Field("fillet", "Fillet at the rim", "value_length", default="0.5 mm", step=fine_step),
    Field("flip_side", "Catch on the other side", "bool", default=False),
    _FLIP,
)
LIP_FIELDS = (
    Field("face", "Rim face", "pick", pick="face"),
    Field("type", "Type", "choice", (("lip", "Lip"), ("groove", "Groove")), "lip"),
    Field("edge", "Edge", "choice", (("inside", "Inside"), ("outside", "Outside")), "inside"),
    Field("width", "Width", "value_length", default="1 mm"),
    Field("height", "Height", "value_length", default="2 mm"),
    Field("clearance", "Clearance", "value_length", default="0.2 mm", show_if=("type", ("groove",))),
)
_VALUES = {
    "boss": {"diameter": "Diameter", "height": "Height", "hole_d": "HoleDiameter", "hole_depth": "HoleDepth",
             "fillet": "FilletRadius", "rib_thick": "RibThickness", "rib_length": "RibLength"},
    "snap_fit": {"length": "Length", "overlap": "Overlap", "thick": "Thickness", "catch_depth": "CatchDepth",
                 "catch_length": "CatchLength", "fillet": "FilletRadius"},
    "lip": {"width": "Width", "height": "Height", "clearance": "Clearance"},
}
_CHOICES = {"boss": ("hole", "insert", "hole_to", "flip", "rib_angle", "rib_skip"), "snap_fit": ("flip_side", "flip"),
            "lip": ("type", "edge")}
_FIELDS = {"boss": BOSS_FIELDS, "snap_fit": SNAP_FIELDS, "lip": LIP_FIELDS}


def _defaults(kind):
    def defaults(doc, body, profile=None):
        values = {x.key: x.default for x in _FIELDS[kind]}
        values.update({x.key: "" for x in _FIELDS[kind] if x.kind == "pick"})
        if kind == "boss":  # a boss that holds the default insert
            values["diameter"] = "%g mm" % math.ceil(inserts.boss_diameter(inserts.named(inserts.DEFAULT)))
        return values
    return defaults


def _apply(doc, f, values, kind):
    for prop, typ in _PROPS:  # a property added since the feature was made
        if prop not in f.PropertiesList:
            f.addProperty(typ, prop, "Plastic", "")
    s = {"kind": kind}
    for key in _CHOICES[kind]:
        s[key] = values.get(key)
    if kind == "lip":
        picks = parse_picks(doc, values.get("face"))
        if picks:
            obj, sub = resolve(*picks[0])
            face = obj.Shape.getElement(sub) if sub.startswith("Face") else None
            if face is None or face.Surface.TypeId != "Part::GeomPlane":
                raise ValueError("Rim face: pick the flat top face of the wall")
            pl = _local(f, obj.getGlobalPlacement()) if obj.getParentGeoFeatureGroup() is not f.getParentGeoFeatureGroup() \
                else App.Placement()
            face = face.transformGeometry(pl.toMatrix()) if not pl.isIdentity() else face
            n, off, c = _plane_of(face)
            f.RimNormal, f.RimOffset, f.RimCentre = n, off, c
            s["face"] = values.get("face")
        elif not getattr(f, MARK, ""):
            raise ValueError("Pick the rim face: the flat top of the wall")
        else:
            s["face"] = settings(f).get("face", "")
    else:
        picks = parse_picks(doc, values.get("sketch"))
        if not picks:
            raise ValueError("Pick a sketch: %s" % ("points or circles for the bosses" if kind == "boss"
                                                    else "lines, each a hook's root"))
        f.Sketch = picks[0][0]
    if kind == "boss":
        try:
            f.Ribs = int(values.get("ribs") or 0)
        except ValueError:
            f.Ribs = 0
    setattr(f, MARK, json.dumps(s))
    for key, prop in _VALUES[kind].items():
        default = next(x.default for x in _FIELDS[kind] if x.key == key)
        value_param(doc, f, prop, (values.get(key) or default).strip(), "length")


_NAMES = {"boss": "Boss", "snap_fit": "SnapFit", "lip": "Lip"}


def _create(kind):
    def create(doc, body, profile, values):
        from incad.core import document
        tip = body.Tip
        f = body.newObject("PartDesign::FeaturePython", _NAMES[kind])
        PlasticFeature(f)
        f.BaseFeature = tip
        document.show_like(f, tip)
        try:
            _apply(doc, f, values, kind)
            if kind == "lip" and values.get("type") == "groove":
                f.Label = "Groove"
            body.Tip = f
            doc.recompute()
            document.show_like(f, tip)
            if "Invalid" in f.State:
                raise ValueError(f.getStatusString() or "The %s could not be made" % TITLES[kind].lower())
        except Exception:
            body.removeObject(f)
            doc.removeObject(f.Name)
            body.Tip = tip
            doc.recompute()
            raise
        sk = getattr(f, "Sketch", None)
        if sk is not None:
            document.set_visibility([sk], False)
        return f
    return create


def _update(kind):
    def update(doc, f, values):
        _apply(doc, f, values, kind)
        if kind == "lip":
            f.Label = "Groove" if values.get("type") == "groove" else "Lip"
        doc.recompute()
        return f
    return update


def _read(kind):
    def read(doc, f):
        s = settings(f)
        values = _defaults(kind)(doc, None)
        for key, prop in _VALUES[kind].items():
            values[key] = value_text(doc, f, prop) or values[key]
        for key in _CHOICES[kind]:
            if s.get(key) is not None:
                values[key] = s[key]
        if kind == "boss":
            values["ribs"] = str(f.Ribs)
        if kind == "lip":
            values["face"] = s.get("face", "")
        elif f.Sketch is not None:
            values["sketch"] = format_picks([(f.Sketch, "")])
        return values
    return read


TITLES = {"boss": "Boss", "snap_fit": "Snap Fit", "lip": "Lip / Groove"}


def _namespace(kind):
    from types import SimpleNamespace
    return SimpleNamespace(
        __name__="incad.features.%s" % kind, TITLE=TITLES[kind], ICON=kind, TYPES=TYPES, NEEDS_PROFILE=False,
        PROPS=dict(_VALUES[kind]), FIELDS=_FIELDS[kind], claims=lambda f, k=kind: is_plastic(f, k),
        defaults=_defaults(kind), create=_create(kind), read=_read(kind), update=_update(kind), _pad_of=lambda f: f,
        info=_INFO.get(kind, lambda values: ""), unavailable=_UNAVAILABLE.get(kind))


_INFO = {  # under the fields: where its sketch goes (the user: "it is unclear how to put the line on the sketch")
    "snap_fit": lambda values: ("Draw a line on top of the wall, along the edge the snap fit goes on (its length is the "
                                "snap fit's width). The strip grows onto that face of the wall, down the wall and up "
                                "over the rim, its lower edge chamfered 45°; its catch reaches back over the rim "
                                "(Catch on the other side: away from it). Flip direction: down."),
    "boss": lambda values: "A boss stands on every point of the sketch and in the middle of every circle." + (
        " Rib 1 points along the sketch's X axis, turned by Rib angle; the others follow anticlockwise. Leave out: "
        "the ribs pressed are not made." if str(values.get("ribs") or "0") != "0" else ""),
}
_UNAVAILABLE = {  # Leave out: only the numbers of the ribs there are
    "boss": lambda values: {"rib_skip": [str(i) for i in range(int(values.get("ribs") or 0) + 1, 7)]},
}


BOSS = _namespace("boss")
SNAP_FIT = _namespace("snap_fit")
LIP = _namespace("lip")
KINDS = [BOSS, LIP, SNAP_FIT]
