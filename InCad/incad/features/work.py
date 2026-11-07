"""Work Plane, Work Axis, Work Point (Inventor) = PartDesign datum Plane / Line / Point.

Every method that FreeCAD's attacher can follow stays associative (probed modes). Three Inventor methods have no
attacher mode — midplane, plane at an angle around an edge, axis parallel to an edge through a point — so their
offset is computed from the picks when the feature is created or edited (ruling). The chosen method and picks
are stored on the feature (InCadWork, JSON) so the dialog reads them back."""
import json
from types import SimpleNamespace

import FreeCAD as App

from incad.features.common import Field, parse_picks, resolve

_STORE = "InCadWork"
QUIET = {"dark": (0.88, 0.92, 1.00), "light": (0.22, 0.30, 0.42)}  # a work plane: lighter than a dark view,
SEE_THROUGH = 0.7  # darker than a light one, and see-through so the part shows through it


def quiet_look(o):
    """Draw work plane `o` in the theme's plane colour, see-through (FreeCAD's datum yellow hid the part)."""
    vo = getattr(o, "ViewObject", None)
    if vo is None:
        return
    try:
        from incad.ui import theme
        color = QUIET[theme.current()]
    except Exception:
        color = QUIET["dark"]
    try:
        mat = vo.ShapeAppearance[0]
        if abs(mat.Transparency - SEE_THROUGH) < 1e-3 and all(abs(a - b) < 1e-3 for a, b in zip(mat.DiffuseColor, color)):
            return  # in this look already (show_result calls it often: no redraw for nothing)
        mat.DiffuseColor = color + (1.0,)
        mat.Transparency = SEE_THROUGH
        vo.ShapeAppearance = [mat]
    except Exception:
        pass
    for prop, value in (("Transparency", round(SEE_THROUGH * 100)), ("LineColor", color)):
        try:
            setattr(vo, prop, value)
        except Exception:
            pass


def _first(doc, text, what="what the work feature is based on"):
    picks = parse_picks(doc, text)
    if not picks:
        raise ValueError("Select %s" % what)
    return resolve(*picks[0])


def _all(doc, text):
    return [resolve(o, s) for o, s in parse_picks(doc, text)]


def _support_items(f):
    """'Obj:Sub' for every element of the attachment (FreeCAD groups several subs of one object together)."""
    out = []
    for obj, subs in f.AttachmentSupport:
        subs = subs if isinstance(subs, (tuple, list)) else [subs]
        out.extend("%s:%s" % (obj.Name, s) if s else obj.Name for s in (subs or [""]))
    return out


def _element(obj, sub):
    if sub:
        return obj.getSubObject(sub)
    shape = getattr(obj, "Shape", None)
    if shape is None:
        return None
    if shape.Faces:
        return shape.Faces[0]
    if shape.Edges:
        return shape.Edges[0]
    return shape.Vertexes[0] if shape.Vertexes else shape  # a work point: its vertex (.Point)


def _line_ends(edge):
    """Two points of a straight edge: its ends, or for an origin axis (an endless line, no vertices) its point and
    one step along it."""
    if edge is None or not hasattr(edge, "Curve"):
        raise ValueError("Pick a straight edge or an axis")
    if len(edge.Vertexes) >= 2:
        return edge.Vertexes[0].Point, edge.Vertexes[-1].Point
    c = edge.Curve
    if not hasattr(c, "Direction"):
        raise ValueError("Pick a straight edge or an axis")
    return App.Vector(c.Location), App.Vector(c.Location) + App.Vector(c.Direction)


def _store(f, data):
    if not hasattr(f, _STORE):
        f.addProperty("App::PropertyString", _STORE, "InCad", "InCad work feature settings")
        f.setPropertyStatus(_STORE, "Hidden")
    setattr(f, _STORE, json.dumps(data, sort_keys=True))


def _load(f):
    try:
        return json.loads(getattr(f, _STORE, "") or "{}")
    except ValueError:
        return {}


def _attach(doc, f, refs, mode, offset=None):
    """Attach with zero offset, recompute, then apply a computed offset (from the attached placement). Only the
    work feature is recomputed (the user: the whole part was, a heavy Emboss took 9 s a pick); the rest on OK."""
    from incad.core import model
    for path in (".AttachmentOffset.Base.z", "AttachmentOffset.Base.z"):
        if model.bound_parameter(f, path) is not None:
            f.setExpression(path, None)
    _same_solid(f, refs)
    f.AttachmentOffset = App.Placement()
    try:
        f.AttachmentSupport = refs
        f.MapMode = mode
        doc.recompute([f])
    except Exception as e:  # FreeCAD's own refusal ("Unknown C++ exception" on two equal points)
        raise ValueError(_plain(str(e)))
    if "Invalid" in f.State:
        raise ValueError(_plain(f.getStatusString()))
    if offset is not None:
        f.AttachmentOffset = offset(App.Placement(f.Placement))
        doc.recompute([f])


_PLAIN = (("infinite radius", "Pick a round edge (a circle or an arc)"),
          ("don't intersect", "These do not meet (parallel?): pick ones that cross"),
          ("collinear", "The three points lie in a line: pick three that make a triangle"),
          ("coincident", "Pick two different points"))


def _plain(status):
    """FreeCAD's attacher message in the user's words, where known."""
    low = (status or "").lower()
    for key, words in _PLAIN:
        if key in low:
            return words
    return "These picks don't define the work feature (%s)" % status


def _same_solid(f, refs):
    """Picks in the work feature's own Solid (or outside every Solid: origin of the part, a 3D line): another
    Solid's face or work plane was read in the wrong Solid's coordinates, with no error."""
    own = f.getParentGeoFeatureGroup()
    for obj, _ in refs:
        group = obj.getParentGeoFeatureGroup() if hasattr(obj, "getParentGeoFeatureGroup") else None
        while group is not None and group.TypeId != "PartDesign::Body":  # an origin plane: its Origin's Solid
            group = group.getParentGeoFeatureGroup() if hasattr(group, "getParentGeoFeatureGroup") else None
        if group is not None and own is not None and group is not own:
            raise ValueError("%s is in %s: pick in %s, the Solid this work feature is in" % (
                obj.Label, group.Label, own.Label))


def _straight(doc, text, what):
    """A straight edge (or an origin / work axis) for an axis along it, else a plain refusal."""
    pick = _first(doc, text, what)
    el = _element(*pick)
    if el is None or not hasattr(el, "Curve") or el.Curve.TypeId != "Part::GeomLine":
        raise ValueError("Pick a straight edge (for a circle's axis use Axis of a cylinder)")
    return pick


def _bound_offset(f):
    from incad.core import model
    return model.bound_parameter(f, ".AttachmentOffset.Base.z") or model.bound_parameter(f, "AttachmentOffset.Base.z")


def _offset_param(doc, f, text, pname=None):
    """f's offset value: the one it had before _attach unbound it (the planes of its series use it), else a new one."""
    from incad.core import model
    pname = pname or _bound_offset(f)
    if pname is not None and pname not in model._feature_names(doc):
        pname = None
    if pname is None:
        pname = model.new_feature_parameter(doc, text or "10 mm", "length")
        f.setExpression(".AttachmentOffset.Base.z", "%s.%s" % (model.MODEL_NAME, pname))
    else:
        if _bound_offset(f) != pname:
            f.setExpression(".AttachmentOffset.Base.z", "%s.%s" % (model.MODEL_NAME, pname))
        if model.parameter_text(doc, pname) != text:
            model.set_model_parameter(doc, pname, text, everything=False)


def _deg(text):
    try:
        return App.Units.Quantity((text or "90").replace("°", " deg")).Value
    except Exception:
        raise ValueError("Angle: enter an angle such as 30°")


def _modes(*names):
    return ("mode", names)


# ----- planes -----
_PLANE_MODES = (("offset", "Offset from plane"), ("parallel_point", "Parallel through point"),
                ("midplane", "Midplane"), ("angle", "Angle around edge"), ("three_points", "Three points"),
                ("normal_edge", "Normal to edge at point"), ("tangent", "Tangent to surface at point"))


def _plane_apply(doc, body, f, values):
    mode = values.get("mode") or "offset"
    old = _load(f)
    if mode != "offset" and series(doc, f):  # the first plane of a row takes another method: the rest stay where
        _release_row(doc, f)                 # they are, as planes of their own
    if mode == "offset":
        pname = _bound_offset(f)
        _attach(doc, f, [_first(doc, values.get("base"), "a plane or a flat face")], "FlatFace")
        _offset_param(doc, f, values.get("offset") or "10 mm", pname)
        _turn(doc, f, values)
        owner = _row_owner(doc, f, old, values)
        count = _plane_count(values.get("count"))
        if owner is f:
            _sync_series(doc, f, count, values.get("base") or "")
        elif count != 1 + len(series(doc, owner)):  # Planes on the 2nd, 3rd... plane: the whole row's count
            k = old.get("k") or 2
            if count < k:
                raise ValueError("This is plane %d of its row: Planes can't be below %d" % (k, k))
            _sync_series(doc, owner, count, _load(owner).get("base") or "")
    elif mode == "parallel_point":
        base = _first(doc, values.get("base"), "a plane or a flat face")
        free = _is_free(values.get("point"))
        point = None if free else _first(doc, values.get("point"), "a point")
        if not base[1] and not free:  # a whole plane object: FreeCAD follows the point (associative)
            _attach(doc, f, [base, point], "ParallelPlane")
        else:  # a face or a marked point: FreeCAD's ParallelPlane needs whole objects, so the offset is computed
            p = _local(doc, body or f.getParentGeoFeatureGroup(), values["point"]) if free else _element(*point).Point

            def to_point(p0):
                return App.Placement(App.Vector(0, 0, p0.inverse().multVec(p).z), App.Rotation())
            _attach(doc, f, [base], "FlatFace", to_point)
    elif mode == "midplane":
        a = _first(doc, values.get("base"), "the first plane")
        fb = _element(*_first(doc, values.get("base2"), "the second plane"))

        def mid(p0):
            z = p0.Rotation.multVec(App.Vector(0, 0, 1))
            if fb is None or fb.ShapeType != "Face" or abs(abs(z.dot(fb.normalAt(0, 0))) - 1) > 1e-6:
                raise ValueError("The two planes must be parallel for a midplane")
            if abs((fb.Surface.Position - p0.Base).dot(z)) < 1e-6:
                raise ValueError("The two planes are the same: pick two different ones")
            return App.Placement(App.Vector(0, 0, (fb.Surface.Position - p0.Base).dot(z) / 2.0), App.Rotation())
        _attach(doc, f, [a], "FlatFace", mid)
    elif mode == "angle":
        base = _first(doc, values.get("base"), "a plane or a flat face")
        edge = _element(*_first(doc, values.get("edge"), "the edge to turn around"))
        angle = _deg(values.get("angle"))

        def turn(p0):
            inv = p0.inverse()
            a, b = _line_ends(edge)
            l1, l2 = inv.multVec(a), inv.multVec(b)
            if abs(l1.z) > 1e-6 or abs(l2.z) > 1e-6:
                raise ValueError("The edge must lie in the plane it turns")
            axis = l2 - l1
            axis.normalize()
            return App.Placement(l1, App.Rotation(axis, angle)) * App.Placement(-l1, App.Rotation())
        _attach(doc, f, [base], "FlatFace", turn)
    elif mode == "three_points":
        items = [i.strip() for i in (values.get("points") or "").split(",") if i.strip()]
        if len(items) != 3:
            raise ValueError("Pick three points")
        if not any(i.startswith("@") for i in items):
            _attach(doc, f, _all(doc, ",".join(items)), "ThreePointsPlane")  # corners: it follows them
        else:
            body = body or f.getParentGeoFeatureGroup()
            _place_through(doc, body, f, [_point_of(doc, body, i) for i in items])
    elif mode == "normal_edge":
        if _is_free(values.get("point")):  # a marked point that is no corner: fixed where it is now
            pick = _first(doc, values.get("edge"), "an edge")
            _same_solid(f, [pick])
            edge = _element(*pick)
            p = _local(doc, body or f.getParentGeoFeatureGroup(), values["point"])
            u = edge.Curve.parameter(p)
            _set_fixed(doc, f, App.Placement(p, App.Rotation(App.Vector(0, 0, 1), edge.tangentAt(u))))
        else:
            _attach(doc, f, [_first(doc, values.get("edge"), "an edge"),
                             _first(doc, values.get("point"), "a point")], "NormalToEdge")
    elif mode == "tangent":
        if _is_free(values.get("point")):
            pick = _first(doc, values.get("face"), "a curved face")
            _same_solid(f, [pick])
            face = _element(*pick)
            p = _local(doc, body or f.getParentGeoFeatureGroup(), values["point"])
            u, v = face.Surface.parameter(p)
            _set_fixed(doc, f, App.Placement(face.Surface.value(u, v),
                                             App.Rotation(App.Vector(0, 0, 1), face.normalAt(u, v))))
        else:
            _attach(doc, f, [_first(doc, values.get("face"), "a curved face"),
                             _first(doc, values.get("point"), "a point")], "TangentPlane")
    else:
        raise ValueError("Unknown work plane method %r" % mode)
    data = {k: values.get(k, "") for k in ("mode", "base", "base2", "point", "edge", "face", "angle", "points",
                                           "turn_ref")}
    if mode == "offset" and old.get("series_of") and old.get("base") == data["base"]:
        data.update(series_of=old["series_of"], k=old.get("k"))  # still one of its first plane's series
    _store(f, data)


TURN_KEYS = ("turn_x", "turn_y", "turn_z")


def _global_rotation(f):
    body = f.getParentGeoFeatureGroup()
    g = body.getGlobalPlacement() if body is not None else App.Placement()
    return g.Rotation.multiply(f.Placement.Rotation)


def _previous(doc, f):
    """The plane a Turn relative to the previous plane is measured from: the one before it in its series (the first
    plane for the second), else None (the base)."""
    data = _load(f)
    owner = doc.getObject(data.get("series_of") or "")
    if owner is None:
        return None
    k = data.get("k") or 2
    before = [p for p in series(doc, owner) if (_load(p).get("k") or 0) < k]
    return before[-1] if before else owner


def _reference(doc, f, ref, base_rot):
    """The rotation Turn's angles are measured from: the global axes, or the previous plane (the base for the first)."""
    if ref == "global":
        return App.Rotation()
    prev = _previous(doc, f)
    return _global_rotation(prev) if prev is not None else base_rot


def _turn(doc, f, values):
    """Turn X / Y / Z: the plane turned about its own point (where its offset puts it), angles about the axes of the
    previous plane or the global ones (X first, then Y, then Z). The result is kept as the plane's own turn from its
    base, so turning the previous plane later leaves this one as it is (the user)."""
    angles = [_deg(values.get(k) or "0") for k in TURN_KEYS]
    base_rot = _global_rotation(f)  # just attached, unturned
    ref = _reference(doc, f, values.get("turn_ref") or "previous", base_rot)
    wanted = ref.multiply(App.Rotation(angles[2], angles[1], angles[0]))
    local = base_rot.inverted().multiply(wanted)
    if any(abs(a) > 1e-12 for a in angles) or values.get("turn_ref") == "global" or not local.isIdentity(1e-12):
        f.AttachmentOffset = App.Placement(f.AttachmentOffset.Base, local)
        doc.recompute([f])


def turn_text(doc, f, ref):
    """Turn X / Y / Z as the dialog shows them, from where the plane is now."""
    body_rot = _global_rotation(f)
    offset = App.Rotation(f.AttachmentOffset.Rotation)
    base_rot = body_rot.multiply(offset.inverted())
    rel = _reference(doc, f, ref, base_rot).inverted().multiply(body_rot)
    z, y, x = rel.getYawPitchRoll()
    return {k: "%g °" % (round(a, 6) + 0.0) for k, a in zip(TURN_KEYS, (x, y, z))}


def _plane_count(text):
    t = (text or "1").strip()
    if not t.isdigit() or not 1 <= int(t) <= 50:
        raise ValueError("Planes: a whole number from 1 to 50")
    return int(t)


def series(doc, f):
    """The planes made with f by its Planes count (for lofts), in order: the k-th one is k offsets from f's base."""
    body = f.getParentGeoFeatureGroup()
    group = body.Group if body is not None else doc.Objects
    out = [o for o in group if o.TypeId == "PartDesign::Plane" and _load(o).get("series_of") == f.Name]
    return sorted(out, key=lambda o: _load(o).get("k") or 0)


def _row_owner(doc, f, data, values):
    """The first plane of the row f stays in: its first plane while f is still offset from the same base (what
    _plane_apply keeps it in the row for), else f itself."""
    owner = doc.getObject(data.get("series_of") or "")
    if owner is None or (values.get("mode") or "offset") != "offset" or (values.get("base") or "") != data.get("base"):
        return f
    return owner


def _release_row(doc, f):
    """f's row planes become planes of their own: out of the row, each offset frozen at its value now (it was a
    formula of f's offset, which f no longer has)."""
    from incad.core import model
    for o in series(doc, f):
        name = _bound_offset(o)
        if name:
            model.set_model_parameter(doc, name, "%.12g mm" % o.AttachmentOffset.Base.z, everything=False)
        data = _load(o)
        data.pop("series_of", None)
        data.pop("k", None)
        _store(o, data)


def row_mate(doc, f, obj):
    """obj when it is another plane of f's row (the first plane or one its Planes count made), else None: clicking
    it while f's dialog is open moves the dialog and its rings there (the user)."""
    if f is None or obj is None or obj is f or obj.TypeId != "PartDesign::Plane":
        return None
    owner = doc.getObject(_load(f).get("series_of") or "") or f
    return obj if obj is owner or obj in series(doc, owner) else None


def _sync_series(doc, f, count, base):
    """Planes 2..count of f's series at 2x, 3x... its offset from the same base, each with its own value (a formula of
    f's: 'd5 * 2'), so a changed offset spaces them all and one plane can still be moved on its own."""
    from incad.core import model
    pname = model.bound_parameter(f, ".AttachmentOffset.Base.z") or model.bound_parameter(f, "AttachmentOffset.Base.z")
    have = series(doc, f)
    for o in reversed(have[count - 1:]):
        users = [u.Label for u in o.InList if u.TypeId != "PartDesign::Body"]
        if users:
            raise ValueError("%s is used by %s: it stays (Planes can't drop it)" % (o.Label, ", ".join(users)))
        name = model.bound_parameter(o, ".AttachmentOffset.Base.z") or model.bound_parameter(o, "AttachmentOffset.Base.z")
        body = o.getParentGeoFeatureGroup()
        if body is not None:
            body.removeObject(o)
        doc.removeObject(o.Name)
        if name:
            try:
                model.remove_model_parameter(doc, name)
            except ValueError:
                pass
    have = have[:count - 1]
    for o in have:
        if list(o.AttachmentSupport) != list(f.AttachmentSupport):
            o.AttachmentSupport = f.AttachmentSupport
            data = _load(o)
            data["base"] = base
            _store(o, data)
    body = f.getParentGeoFeatureGroup()
    for k in range(len(have) + 2, count + 1):
        o = body.newObject("PartDesign::Plane", "WorkPlane")
        quiet_look(o)
        o.AttachmentSupport = f.AttachmentSupport
        o.MapMode = "FlatFace"
        name = model.new_feature_parameter(doc, "%s * %d" % (pname, k), "length")
        o.setExpression(".AttachmentOffset.Base.z", "%s.%s" % (model.MODEL_NAME, name))
        _store(o, {"mode": "offset", "base": base, "base2": "", "point": "", "edge": "", "face": "", "angle": "",
                   "points": "", "series_of": f.Name, "k": k})
        have.append(o)
    if have:
        doc.recompute(have)


def _to_global(body):
    return body.getGlobalPlacement() if body is not None else App.Placement()


def format_point_picks(body, base, points):
    """Clicked marker points (global) as the Points field's text: a corner of the part's own shape as its
    vertex (the plane follows it), anything else (an edge middle, a circle centre, another Solid's corner, a
    sketch point) as '@x y z', global."""
    from incad.features import line3d
    out = []
    owners = [base] if base is not None and not base.Shape.isNull() else []
    if body is not None:  # a 3D line's corner in the same Solid: the feature follows the line too (the user)
        owners += [o for o in body.Group if line3d.claims(o) and not o.Shape.isNull()]
    g = _to_global(body)
    marks = [o for o in body.Group if o.TypeId == "PartDesign::Point"] if body is not None else []
    for p in points:
        item = next((o.Name for o in marks if (g.multVec(o.Placement.Base) - p).Length < 1e-6), None)  # a work
        for owner in owners if item is None else ():                                                  # point

            for i, v in enumerate(owner.Shape.Vertexes):
                if (g.multVec(v.Point) - p).Length < 1e-6:
                    item = "%s:Vertex%d" % (owner.Name, i + 1)
                    break
            if item:
                break
        out.append(item or "@%.9g %.9g %.9g" % (p.x, p.y, p.z))
    return ",".join(out)


def _point_of(doc, body, item):
    """A Points item as a global point."""
    if item.startswith("@"):
        return App.Vector(*(float(v) for v in item[1:].split()))
    el = _element(*_first(doc, item, "a point"))
    if el is None or el.ShapeType != "Vertex":
        raise ValueError("Pick the points again")
    return _to_global(body).multVec(el.Point)  # a feature's shape lies in its body


def _is_free(text):
    """A marked point that is no corner of the part ('@x y z', global): the feature stands where it is now."""
    return (text or "").strip().startswith("@")


def _local(doc, body, item):
    """A point pick in the body's coordinates (where the attached placements are)."""
    return _to_global(body).inverse().multVec(_point_of(doc, body, item.strip()))


def _set_fixed(doc, f, plc):
    """No attachment: the feature at `plc` (in its body)."""
    f.AttachmentSupport = []
    f.MapMode = "Deactivated"
    f.AttachmentOffset = App.Placement()
    f.Placement = plc
    doc.recompute([f])


def _line_through(doc, body, f, pts):
    """An axis through two fixed points (global): no attachment, its placement in the body."""
    a, b = pts
    if (b - a).Length < 1e-9:
        raise ValueError("Pick two different points")
    plc = App.Placement(a, App.Rotation(App.Vector(0, 0, 1), b - a))
    if body is not None:
        plc = body.getGlobalPlacement().inverse().multiply(plc)
    f.AttachmentSupport = []
    f.MapMode = "Deactivated"
    f.AttachmentOffset = App.Placement()
    f.Placement = plc
    doc.recompute([f])


def _place_through(doc, body, f, pts):
    """A plane through three fixed points (global): no attachment, its placement in the body."""
    a, b, c = pts
    n = (b - a).cross(c - a)
    if n.Length < 1e-9:
        raise ValueError("Pick three different points that are not in a line")
    x = b - a
    x.normalize()
    n.normalize()
    rot = App.Rotation(x, n.cross(x), n, "ZXY")
    plc = App.Placement(a, rot)
    if body is not None:
        plc = body.getGlobalPlacement().inverse().multiply(plc)
    f.AttachmentSupport = []
    f.MapMode = "Deactivated"
    f.AttachmentOffset = App.Placement()
    f.Placement = plc
    doc.recompute([f])


def fit_size(f):
    """Work plane `f` drawn just over the part (FreeCAD's automatic size spans the whole view), centred on the
    part's middle as seen on the plane; 20 mm on an empty part."""
    body = f.getParentGeoFeatureGroup()
    shape = getattr(body, "Shape", None)
    if "ResizeMode" not in f.PropertiesList:
        return
    pl = f.Placement
    if shape is None or shape.isNull() or not shape.Solids:
        size = 20.0
    else:
        inv = pl.inverse()
        box = shape.BoundBox
        pts = [inv.multVec(App.Vector(x, y, z)) for x in (box.XMin, box.XMax) for y in (box.YMin, box.YMax)
               for z in (box.ZMin, box.ZMax)]
        size = max(max(abs(p.x) for p in pts), max(abs(p.y) for p in pts)) * 2 * 1.1
        size = min(max(size, 20.0), 1.1 * 2 * box.DiagonalLength)
    try:
        f.ResizeMode = "Manual"
        f.Length = size
        f.Width = size
    except Exception:
        pass


def _plane_create(doc, body, profile, values):
    f = body.newObject("PartDesign::Plane", "WorkPlane")
    quiet_look(f)
    _plane_apply(doc, body, f, values)
    doc.recompute([f])
    fit_size(f)
    return f


def _plane_defaults(doc, body, profile=None):
    return {"mode": "offset", "base": "", "base2": "", "offset": "10 mm", "count": "1", "point": "", "edge": "",
            "face": "", "angle": "90 °", "points": "", "turn_ref": "previous", "turn_x": "0 °", "turn_y": "0 °",
            "turn_z": "0 °"}


def _plane_read(doc, f):
    from incad.core import model
    values = _plane_defaults(doc, None)
    data = _load(f)
    if data:
        values.update({k: v for k, v in data.items() if v is not None})
    elif f.MapMode == "ThreePointsPlane":
        values.update(mode="three_points", points=",".join(_support_items(f)))
    else:
        items = _support_items(f)
        values.update(mode="offset", base=items[0] if items else "")
    if values["mode"] == "three_points" and not values.get("points"):
        values["points"] = ",".join(_support_items(f))
    pname = model.bound_parameter(f, ".AttachmentOffset.Base.z") or model.bound_parameter(f, "AttachmentOffset.Base.z")
    if pname:
        values["offset"] = model.parameter_text(doc, pname)
    values["count"] = str(1 + len(series(doc, _row_owner(doc, f, data, values))))
    if values["mode"] == "offset":
        values["turn_ref"] = values.get("turn_ref") or "previous"
        values.update(turn_text(doc, f, values["turn_ref"]))
    return values


def _plane_update(doc, f, values):
    _plane_apply(doc, f.getParentGeoFeatureGroup(), f, values)
    doc.recompute([f])
    fit_size(f)
    return f


PLANE = SimpleNamespace(
    __name__="incad.features.work_plane", TITLE="Work Plane", ICON="work_plane", TYPES=("PartDesign::Plane",),
    NEEDS_PROFILE=False, PROPS={}, PRESELECT={"base": "plane"},
    FIELDS=(Field("mode", "Method", "combo", _PLANE_MODES, "offset"),
            Field("base", "Plane", "pick", pick="plane",
                  show_if=_modes("offset", "parallel_point", "midplane", "angle")),
            Field("base2", "Second plane", "pick", pick="plane", show_if=_modes("midplane")),
            Field("offset", "Offset", "value_length", default="10 mm", show_if=_modes("offset"), signed=True),
            Field("count", "Planes", "value_length", default="1", show_if=_modes("offset")),
            Field("turn_ref", "Turn", "choice", (("previous", "Prev"), ("global", "Global")), "previous",
                  show_if=_modes("offset")),
            Field("turn_x", "X", "value_angle", default="0 °", show_if=_modes("offset"), signed=True, same_row=True),
            Field("turn_y", "Y", "value_angle", default="0 °", show_if=_modes("offset"), signed=True, same_row=True),
            Field("turn_z", "Z", "value_angle", default="0 °", show_if=_modes("offset"), signed=True, same_row=True),
            Field("edge", "Edge", "pick", pick="edge_axis", show_if=_modes("angle", "normal_edge")),
            Field("angle", "Angle", "value_angle", default="90 °", show_if=_modes("angle")),
            Field("face", "Surface", "pick", pick="face", show_if=_modes("tangent")),
            Field("point", "Point", "pick", pick="marked_point",
                  show_if=_modes("parallel_point", "normal_edge", "tangent")),
            Field("points", "Points", "picks", pick="three_points", show_if=_modes("three_points"))),
    defaults=_plane_defaults, create=_plane_create, read=_plane_read, update=_plane_update, _pad_of=lambda f: f,
    switch_to=row_mate,
    overlay=lambda shell, session: __import__("incad.ui.plane_turn", fromlist=["x"]).plane_overlay(shell, session))


# ----- axes -----
_AXIS_MODES = (("edge", "On edge"), ("two_points", "Two points"), ("intersection", "Intersection of two planes"),
               ("normal_plane", "Normal to plane through point"), ("cylinder", "Axis of a cylinder"),
               ("parallel_point", "Parallel to edge through point"))


def _axis_apply(doc, body, f, values):
    mode = values.get("mode") or "edge"
    if mode == "edge":
        _attach(doc, f, [_straight(doc, values.get("edge"), "an edge")], "TwoPointLine")
    elif mode == "two_points":
        items = [i.strip() for i in (values.get("points") or "").split(",") if i.strip()]
        if len(items) != 2:
            raise ValueError("Pick two points")
        if not any(i.startswith("@") for i in items):
            _attach(doc, f, _all(doc, ",".join(items)), "TwoPointLine")  # corners: it follows them
        else:
            body = body or f.getParentGeoFeatureGroup()
            _line_through(doc, body, f, [_point_of(doc, body, i) for i in items])
    elif mode == "intersection":
        _attach(doc, f, [_first(doc, values.get("base"), "the first plane"),
                         _first(doc, values.get("base2"), "the second plane")], "IntersectionLine")
    elif mode == "normal_plane":
        if _is_free(values.get("point")):
            pick = _first(doc, values.get("base"), "a plane or a flat face")
            _same_solid(f, [pick])
            face = _element(*pick)
            p = _local(doc, body or f.getParentGeoFeatureGroup(), values["point"])
            _set_fixed(doc, f, App.Placement(p, App.Rotation(App.Vector(0, 0, 1),
                                                             face.normalAt(*face.Surface.parameter(p)))))
        else:
            _attach(doc, f, [_first(doc, values.get("base"), "a plane or a flat face"),
                             _first(doc, values.get("point"), "a point")], "FaceNormal")
    elif mode == "cylinder":
        obj, sub = _first(doc, values.get("face"), "a cylindrical face or a round edge")
        if sub.startswith("Edge"):
            _attach(doc, f, [(obj, sub)], "AxisOfCurvature")
        else:
            _attach_round_face(doc, f, obj, sub)
    elif mode == "parallel_point":
        if _is_free(values.get("point")):
            at = _local(doc, body or f.getParentGeoFeatureGroup(), values["point"])
        else:
            at = _element(*_first(doc, values.get("point"), "a point")).Point

        def through(p0):
            local = p0.inverse().multVec(at)
            return App.Placement(App.Vector(local.x, local.y, 0), App.Rotation())
        _attach(doc, f, [_straight(doc, values.get("edge"), "an edge")], "TwoPointLine", through)
    else:
        raise ValueError("Unknown work axis method %r" % mode)
    _store(f, {k: values.get(k, "") for k in ("mode", "edge", "points", "base", "base2", "point", "face")})


def round_axis(face):
    """(point, unit direction) of a round face's own axis — cylinder, cone, torus, sphere, a surface of revolution —
    or None. Not the face's axes of inertia: a part of a cylinder (a half ring) or a cone had them across the part."""
    s = face.Surface
    if s.TypeId == "Part::GeomSurfaceOfRevolution":
        point, direction = App.Vector(s.Location), App.Vector(s.Direction)
    elif s.TypeId in ("Part::GeomCylinder", "Part::GeomCone", "Part::GeomToroid", "Part::GeomSphere"):
        point, direction = App.Vector(s.Center), App.Vector(s.Axis)
    else:
        return None
    return point, direction.normalize()


def _attach_round_face(doc, f, obj, sub):
    """The axis of a round face: on a circle of the face that has that axis (it follows the part); a face with none
    gets the axis placed from the surface, attached to its object."""
    face = _element(obj, sub)
    axis = round_axis(face) if face is not None and hasattr(face, "Surface") else None
    if axis is None:
        raise ValueError("Axis of a cylinder: pick a round face (a cylinder or a cone) or a round edge")
    point, direction = axis
    shape = obj.Shape
    for e in face.Edges:
        c = e.Curve
        if c.TypeId != "Part::GeomCircle" or App.Vector(c.Axis).cross(direction).Length > 1e-7:
            continue
        off = App.Vector(c.Center) - point
        if (off - direction * off.dot(direction)).Length > 1e-6:
            continue  # a circle beside the axis (a letter's round on the face)
        i = next((k for k, x in enumerate(shape.Edges, 1) if x.isSame(e)), None)
        if i is not None:
            _attach(doc, f, [(obj, "Edge%d" % i)], "AxisOfCurvature")
            return
    rot = App.Rotation(App.Vector(0, 0, 1), direction)
    _attach(doc, f, [(obj, "")], "ObjectZ",
            lambda p0: p0.inverse().multiply(App.Placement(point, rot)))


def _axis_create(doc, body, profile, values):
    f = body.newObject("PartDesign::Line", "WorkAxis")
    _axis_apply(doc, body, f, values)
    return f


def _axis_defaults(doc, body, profile=None):
    return {"mode": "edge", "edge": "", "points": "", "base": "", "base2": "", "point": "", "face": ""}


def _axis_read(doc, f):
    values = _axis_defaults(doc, None)
    data = _load(f)
    if data:
        values.update(data)
    else:
        items = _support_items(f)
        if len(items) == 2:
            values.update(mode="two_points", points=",".join(items))
        else:
            values.update(mode="edge", edge=items[0] if items else "")
    if values["mode"] == "two_points" and not values.get("points"):
        values["points"] = ",".join(_support_items(f))
    return values


def _axis_update(doc, f, values):
    _axis_apply(doc, f.getParentGeoFeatureGroup(), f, values)
    return f


AXIS = SimpleNamespace(
    __name__="incad.features.work_axis", TITLE="Work Axis", ICON="work_axis", TYPES=("PartDesign::Line",),
    NEEDS_PROFILE=False, PROPS={}, PRESELECT={"edge": "axis"},
    FIELDS=(Field("mode", "Method", "combo", _AXIS_MODES, "edge"),
            Field("edge", "Edge", "pick", pick="edge", show_if=_modes("edge", "parallel_point")),
            Field("points", "Points", "picks", pick="axis_points", show_if=_modes("two_points")),
            Field("base", "Plane", "pick", pick="plane", show_if=_modes("intersection", "normal_plane")),
            Field("base2", "Second plane", "pick", pick="plane", show_if=_modes("intersection")),
            Field("face", "Cylinder", "pick", pick="element", show_if=_modes("cylinder")),
            Field("point", "Point", "pick", pick="marked_point", show_if=_modes("normal_plane", "parallel_point"))),
    defaults=_axis_defaults, create=_axis_create, read=_axis_read, update=_axis_update, _pad_of=lambda f: f)


# ----- points -----
_POINT_MODES = (("vertex", "At a point"), ("center", "Center of circle"), ("midpoint", "Midpoint of edge"),
                ("edge_plane", "Edge meets plane"), ("two_edges", "Two edges meet"))


def _point_apply(doc, body, f, values):
    mode = values.get("mode") or "vertex"
    if mode == "vertex":
        if _is_free(values.get("vertex")):
            _set_fixed(doc, f, App.Placement(_local(doc, body or f.getParentGeoFeatureGroup(), values["vertex"]),
                                             App.Rotation()))
        else:
            _attach(doc, f, [_first(doc, values.get("vertex"), "a point")], "Vertex")
    elif mode == "center":
        _attach(doc, f, [_first(doc, values.get("edge"), "a round edge")], "CenterOfCurvature")
    elif mode == "midpoint":  # halfway along the edge (CenterOfMass was the centroid: off an arc, inside it)
        f.MapPathParameter = 0.5
        _attach(doc, f, [_first(doc, values.get("edge"), "an edge")], "OnEdge")
    elif mode == "edge_plane":
        edge, base = _first(doc, values.get("edge"), "an edge"), _first(doc, values.get("base"), "a plane")
        _attach(doc, f, [edge, base], "ProximityPoint1")
        _check_meets(f, [edge], [base], "The edge does not cross the plane")
    elif mode == "two_edges":
        a = _first(doc, values.get("edge"), "the first edge")
        b = _first(doc, values.get("edge2"), "the second edge")
        _attach(doc, f, [a, b], "ProximityPoint1")
        _check_meets(f, [a, b], [], "The two edges do not meet")
    else:
        raise ValueError("Unknown work point method %r" % mode)
    _store(f, {k: values.get(k, "") for k in ("mode", "vertex", "edge", "edge2", "base")})


def _check_meets(f, curves, planes, words):
    """ProximityPoint1 gives the nearest point even where nothing meets: the point must lie on every edge (a
    straight one taken endless, as the edge meets the plane past its end too) and on every plane (endless)."""
    import Part
    p, tol = f.Placement.Base, 1e-6  # in the body, as the picks' shapes
    for obj, sub in curves:
        el = _element(obj, sub)
        if el is None or not hasattr(el, "Curve"):
            continue
        if el.Curve.TypeId == "Part::GeomLine":
            d = App.Vector(el.Curve.Direction).normalize()
            off = p - App.Vector(el.Curve.Location)
            off_line = (off - d * off.dot(d)).Length
        else:
            off_line = Part.Vertex(p).distToShape(el)[0]
        if off_line > tol:
            raise ValueError(words)
    for obj, sub in planes:
        el = _element(obj, sub)
        if el is not None and hasattr(el, "Surface") and hasattr(el.Surface, "Position"):
            if abs((p - App.Vector(el.Surface.Position)).dot(el.normalAt(0, 0))) > tol:
                raise ValueError(words)


def _point_create(doc, body, profile, values):
    f = body.newObject("PartDesign::Point", "WorkPoint")
    _point_apply(doc, body, f, values)
    return f


def _point_defaults(doc, body, profile=None):
    return {"mode": "vertex", "vertex": "", "edge": "", "edge2": "", "base": ""}


def _point_read(doc, f):
    values = _point_defaults(doc, None)
    data = _load(f)
    if data:
        values.update(data)
    else:
        items = _support_items(f)
        values.update(mode="vertex", vertex=items[0] if items else "")
    return values


def _point_update(doc, f, values):
    _point_apply(doc, f.getParentGeoFeatureGroup(), f, values)
    return f


POINT = SimpleNamespace(
    __name__="incad.features.work_point", TITLE="Work Point", ICON="work_point", TYPES=("PartDesign::Point",),
    NEEDS_PROFILE=False, PROPS={}, PRESELECT={"vertex": "vertex"},
    FIELDS=(Field("mode", "Method", "combo", _POINT_MODES, "vertex"),
            Field("vertex", "Point", "pick", pick="marked_point", show_if=_modes("vertex")),
            Field("edge", "Edge", "pick", pick="edge",
                  show_if=_modes("center", "midpoint", "edge_plane", "two_edges")),
            Field("edge2", "Second edge", "pick", pick="edge", show_if=_modes("two_edges")),
            Field("base", "Plane", "pick", pick="plane", show_if=_modes("edge_plane"))),
    defaults=_point_defaults, create=_point_create, read=_point_read, update=_point_update, _pad_of=lambda f: f)
