"""Inventor's browser link both ways: which faces of the shown solid a feature made (its row lights them in the
3D view) and which feature made a face or edge clicked on the solid (its row lights in the tree).

A feature 'made' an element when a point inside it lies on no element of the same geometry in its base (a face
the feature only trimmed stays its older feature's); a face of the shown solid (the body's Tip) belongs to the
feature whose new element it lies on."""
import Part

TOL = 1e-5
_index = {}  # (shape hash, kind) -> [(enlarged box, element)] of a shape's elements
_points = {}  # (shape hash, kind) -> [(geometry type, a point inside)] of a shape's elements
_print_sets = {}  # (shape hash, kind) -> {fingerprint} of a shape's elements
_tips = {}  # (feature, kind, shape hashes) -> on_tip's names


def _elements(shape, kind):
    return shape.Faces if kind == "Face" else shape.Edges


def _shape(obj):
    try:
        s = obj.Shape
        return None if s.isNull() else s
    except Exception:
        return None


def _geom_type(el, kind):
    try:
        return (el.Surface if kind == "Face" else el.Curve).TypeId
    except Exception:
        return ""


def _point(el, kind):
    """A point inside the element (a face's centre of mass can lie in its hole)."""
    if kind == "Edge":
        return el.valueAt(0.5 * (el.FirstParameter + el.LastParameter))
    try:  # a point of its parameter range well inside the face, from the middle out (a tessellation took 8 ms a
        u0, u1, v0, v1 = el.ParameterRange  # face, and a thin triangle's centre can lie just off the face)
        du, dv = 0.02 * (u1 - u0), 0.02 * (v1 - v0)
        steps = sorted(((i, j) for i in range(1, 8) for j in range(1, 8)), key=lambda ij: abs(ij[0] - 4) + abs(ij[1] - 4))
        for i, j in steps:
            u, v = u0 + (u1 - u0) * i / 8.0, v0 + (v1 - v0) * j / 8.0
            if all(el.isPartOfDomain(u + a, v + b) for a, b in ((0, 0), (du, 0), (-du, 0), (0, dv), (0, -dv))):
                return el.Surface.value(u, v)
    except Exception:
        pass
    pts, tris = el.tessellate(0.5)
    p = el.CenterOfMass
    if tris:
        a, b, c = (pts[i] for i in tris[0])
        p = (a + b + c) * (1.0 / 3.0)  # inside the face, but on a chord: brought back onto the surface
    try:
        u, v = el.Surface.parameter(p)
        if el.isPartOfDomain(u, v):
            return el.Surface.value(u, v)
        return el.distToShape(Part.Vertex(p))[1][0][0]  # a sliver (a chamfer left 0.01 mm of a face): its nearest
    except Exception:                                   # point on the face, not one just off it
        return p


def _on(point, el):
    bb = _box(el)
    bb.enlarge(TOL * 10)
    if not bb.isInside(point):
        return False
    return el.distToShape(Part.Vertex(point))[0] < TOL


def _cached(store, shape, kind, make):
    key = (shape.hashCode(), kind)
    if key not in store:
        if len(store) > 64:  # shapes of earlier recomputes
            store.clear()
        store[key] = make()
    return store[key]


def _box(el):
    """The element's exact box: BoundBox follows the display's triangles once the shape is drawn, and those cut
    inside a curve (an ellipse's side face ended 0.0002 mm short of its end: the point there lay "on no face", and a
    fillet's preview outlined the whole side face)."""
    try:
        return el.optimalBoundingBox(False, False)
    except Exception:
        return el.BoundBox


def _boxes(shape, kind):
    def make():
        out = []
        for o in _elements(shape, kind):
            bb = _box(o)
            bb.enlarge(TOL * 10)
            out.append((bb, o))
        return out
    return _cached(_index, shape, kind, make)


def _inner_points(shape, kind):
    """[(geometry type, a point inside, fingerprint)] of the shape's elements."""
    return _cached(_points, shape, kind, lambda: [(_geom_type(e, kind), _point(e, kind), _fingerprint(e, kind))
                                                  for e in _elements(shape, kind)])


def _fingerprint(el, kind):
    """What makes an element the same one in another feature's shape: geometry type, corners, edge count, box. Shapes
    read from a file share no elements, and projecting a point onto a wrapped letter's spline took 4 ms a face."""
    r = lambda x: round(x, 4)
    bb = el.BoundBox
    return (_geom_type(el, kind), len(el.Edges), tuple(sorted((r(v.X), r(v.Y), r(v.Z)) for v in el.Vertexes)),
            tuple(r(x) for x in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)))


def _prints(shape, kind):
    return _cached(_print_sets, shape, kind, lambda: {_fingerprint(e, kind) for e in _elements(shape, kind)})


def _on_any(point, t, shape, kind, among=None):
    """The point lies on an element of `shape` (or of `among`, indexes into it) with geometry type t. Boxes first,
    type and distance only for the few elements whose box holds the point."""
    boxes = _boxes(shape, kind)
    for i in (range(len(boxes)) if among is None else among):
        bb, o = boxes[i]
        if bb.isInside(point) and _geom_type(o, kind) == t and _lies_on(point, o, kind):
            return True
    return False


def _lies_on(point, el, kind):
    """The point lies on the element: off its surface or curve is a quick no, inside its parameter domain a quick
    yes; only what is left (a seam, a periodic range) asks distToShape (4-10 ms a face of a wrapped letter)."""
    try:
        geom = el.Surface if kind == "Face" else el.Curve
        prm = geom.parameter(point)
        if kind == "Face":
            if (geom.value(*prm) - point).Length > TOL:
                return False
            if el.isPartOfDomain(*prm):
                return True
        else:
            if (geom.value(prm) - point).Length > TOL:
                return False
            if el.FirstParameter - 1e-9 <= prm <= el.LastParameter + 1e-9:
                return True
    except Exception:
        pass
    return el.distToShape(Part.Vertex(point))[0] < TOL


def _brought(feature, point, t, kind, fp=None):
    """The feature brought in the element at `point` (type t, fingerprint fp): its shape has one there, its base has
    none (one point tested against each shape: comparing every element with every element of the base took 60-80 s
    on the first click on a 600-face part). The same element (fp) found in a shape answers without geometry."""
    shape = _shape(feature)
    if shape is None:
        return False
    if not (fp is not None and fp in _prints(shape, kind)) and not _on_any(point, t, shape, kind):
        return False
    base = getattr(feature, "BaseFeature", None)
    base_shape = _shape(base) if base is not None else None
    if base_shape is None:
        return True
    if fp is not None and fp in _prints(base_shape, kind):
        return False
    return not _on_any(point, t, base_shape, kind)


def _made_by(feature, el, kind):
    return _brought(feature, _point(el, kind), _geom_type(el, kind), kind, _fingerprint(el, kind))


def _applied(feature):
    """(body, tip) when the feature is applied (at or above End of Part), else (body, None)."""
    body = feature.getParentGeoFeatureGroup() if hasattr(feature, "getParentGeoFeatureGroup") else None
    if body is None or body.TypeId != "PartDesign::Body" or body.Tip is None:
        return body, None
    group = list(body.Group)
    if feature not in group or body.Tip not in group or group.index(feature) > group.index(body.Tip):
        return body, None
    return body, body.Tip


def on_tip(feature, kind="Face"):
    """Names of the shown solid's faces (or edges) this feature made; [] when it is rolled back or made none."""
    body, tip = _applied(feature)
    shape = _shape(tip) if tip is not None else None
    if shape is None:
        return []
    own, base = _shape(feature), _shape(getattr(feature, "BaseFeature", None))
    key = (feature.Name, kind, shape.hashCode(), own.hashCode() if own is not None else 0,
           base.hashCode() if base is not None else 0)
    if key not in _tips:  # a row clicked again on an unchanged part
        if len(_tips) > 64:
            _tips.clear()
        # the same rule as creator(): the solid's element whose inner point the feature brought in
        _tips[key] = ["%s%d" % (kind, i + 1) for i, (t, p, fp) in enumerate(_inner_points(shape, kind))
                      if _brought(feature, p, t, kind, fp)]
    return list(_tips[key])


def creator(tip, sub):
    """The feature that made this face or edge of the shown solid (the latest one that brought it in)."""
    kind = "Face" if sub.startswith("Face") else "Edge" if sub.startswith("Edge") else None
    shape = _shape(tip)
    body = tip.getParentGeoFeatureGroup() if hasattr(tip, "getParentGeoFeatureGroup") else None
    if kind is None or shape is None or body is None:
        return tip
    try:
        el = shape.getElement(sub)
    except Exception:
        return tip
    group = list(body.Group)
    upto = group.index(tip) if tip in group else len(group) - 1
    t, p, fp = _geom_type(el, kind), _point(el, kind), _fingerprint(el, kind)
    for f in reversed(group[:upto + 1]):
        if f.isDerivedFrom("PartDesign::Feature") and f.TypeId not in (
                "PartDesign::Plane", "PartDesign::Line", "PartDesign::Point") and _brought(f, p, t, kind, fp):
            return f
    return tip
