"""Sketch ▸ Text (Inventor's Text and Geometry Text): words in a sketch, at a point of the sketch or along one of its
curves, that Extrude raises (Join) or engraves (Cut). The text is one object — a Part2DObject in the Solid, linked to
its sketch, rebuilt whenever the sketch changes — so it stays editable: double-click its row to change the words or
the font. Its point and curve are kept by FreeCAD's permanent geometry ids; Size, Angle, Spacing, Shift and
Distance are named parameters (d12 …) like every InCad value. Geometry: incad.core.sketch_text."""
import json
from types import SimpleNamespace

import FreeCAD as App

from incad.features.common import Field, format_picks, parse_picks, value_param, value_text

MARK = "InCadText"   # the text's settings (JSON); also what tells a Text object apart
_pending_box = None  # two corners clicked in the sketch before the dialog opened: the new text's box (Shell)

FIELDS = (
    Field("text", "Text", "text", default="Text"),
    Field("font", "Font", "combo", (("Arial", "Arial"),), "Arial"),  # the full list comes from options()
    Field("bold", "Bold", "bool", default=False),
    Field("italic", "Italic", "bool", default=False),
    Field("mirror", "Mirror (read from behind)", "bool", default=False),
    Field("size", "Size", "value_length", default="5 mm", show_if=("fit", (False,))),
    Field("mode", "Placement", "choice", (("box", "In a box"), ("curve", "Along a curve")), "box"),
    Field("box", "Box", "pick", pick="sketch_box", show_if=("mode", ("box",))),
    Field("frame", "Frame", "choice", (("rect", "Rectangle"), ("ellipse", "Ellipse"), ("circle", "Circle")), "rect",
          show_if=("mode", ("box",))),
    Field("halign", "Horizontal", "choice", (("left", "Left"), ("center", "Centre"), ("right", "Right")), "center",
          show_if=("mode", ("box",))),
    Field("valign", "Vertical", "choice", (("top", "Top"), ("middle", "Middle"), ("bottom", "Bottom")), "middle",
          show_if=("mode", ("box",))),
    Field("fit", "Fit to the box", "bool", default=False, show_if=("mode", ("box",))),
    Field("curve", "Curve", "pick", pick="sketch_curve", show_if=("mode", ("curve",))),
    Field("position", "Position", "choice", (("start", "Start"), ("center", "Centre"), ("end", "End")), "center",
          show_if=("mode", ("curve",))),
    Field("shift", "Shift", "value_length", default="0 mm", show_if=("mode", ("curve",)), signed=True),
    Field("distance", "Distance", "value_length", default="0 mm", show_if=("mode", ("curve",)), signed=True),
    Field("flip", "Flip side", "bool", default=False, show_if=("mode", ("curve",))),
    Field("spacing", "Letter spacing", "value_length", default="0 mm", signed=True),
)
PROPS = {"size": "Size", "spacing": "Spacing", "shift": "Shift", "distance": "Distance"}
_UNITS = {"size": "length", "spacing": "length", "shift": "length", "distance": "length"}
_PROP_TYPES = {"Size": "App::PropertyLength", "Spacing": "App::PropertyDistance",
               "Shift": "App::PropertyDistance", "Distance": "App::PropertyDistance"}


def is_text(obj):
    return obj is not None and getattr(obj, "TypeId", "") == "Part::Part2DObjectPython" and MARK in obj.PropertiesList


def texts_of(sk):
    """The Text objects written in this sketch."""
    return [o for o in sk.InList if is_text(o) and getattr(o, "Sketch", None) is sk]


# ----- references to the sketch's point / curve (kept by geometry id: indexes shift when geometry is deleted) -----
def _ref(sk, sub):
    from incad.core import sketch_patterns as sp
    geo, pos = sp.element(sk, sub)
    if geo >= 0:
        return {"id": sk.getGeometryId(geo), "pos": pos}
    return {"ext": geo, "pos": pos}  # projected geometry (-3 …) or the origin (-1, 1)


def _geo(sk, ref):
    if ref is None:
        return None
    if "ext" in ref:
        return ref["ext"]
    for i in range(sk.GeometryCount):
        if sk.getGeometryId(i) == ref["id"]:
            return i
    return None


def _sub(sk, ref):
    """The sub-element name of a reference ('Vertex4', 'Edge2', 'ExternalEdge1', 'RootPoint'), or ''."""
    geo = _geo(sk, ref)
    if geo is None:
        return ""
    pos = ref.get("pos", 0)
    if geo == -1 and pos == 1:
        return "RootPoint"
    if pos == 0:
        return "Edge%d" % (geo + 1) if geo >= 0 else "ExternalEdge%d" % (-geo - 2)
    for i in range(sk.GeometryCount * 4 + len(sk.ExternalGeo) * 3 + 4):
        try:
            if tuple(sk.getGeoVertexIndex(i)) == (geo, pos):
                return "Vertex%d" % (i + 1)
        except Exception:
            break
    return ""


def box_lines(sk, g):
    """The four lines (indexes, in order round it) of the closed four-sided box the line g belongs to, or None:
    a rectangle drawn as the text box (construction) or any four lines joined end to end."""
    def line(i):
        return 0 <= i < sk.GeometryCount and sk.Geometry[i].TypeId == "Part::GeomLineSegment"
    if not line(g):
        return None
    lines = [i for i in range(sk.GeometryCount) if line(i)]

    def ends(i):
        geo = sk.Geometry[i]
        return geo.StartPoint, geo.EndPoint

    chain, cur, at = [g], g, ends(g)[1]
    for _ in range(4):
        nxt = next((i for i in lines if i not in chain[1:] and i != cur
                    and any((p - at).Length < 1e-6 for p in ends(i))), None)
        if nxt is None:
            return None
        if nxt == g:
            return chain if len(chain) == 4 else None
        a, b = ends(nxt)
        at = b if (a - at).Length < 1e-6 else a
        chain.append(nxt)
        cur = nxt
    return None


def _corners(sk, ids):
    """The box's four corners (in order round it) from its lines' geometry ids, or None if one is gone."""
    idx = [_geo(sk, {"id": i}) for i in ids]
    if any(i is None for i in idx):
        return None
    pts = []
    for i in idx:
        for p in (sk.Geometry[i].StartPoint, sk.Geometry[i].EndPoint):
            if not any((p - q).Length < 1e-6 for q in pts):
                pts.append(p)
    if len(pts) != 4:
        return None
    import math
    c = sum(pts, App.Vector()) * 0.25
    return sorted(pts, key=lambda p: math.atan2(p.y - c.y, p.x - c.x))


def _make_box(sk, a, b):
    """A construction rectangle from two opposite corners, held square to the sketch (H / V): the text box."""
    import Part
    import Sketcher
    x0, x1, y0, y1 = min(a.x, b.x), max(a.x, b.x), min(a.y, b.y), max(a.y, b.y)
    if x1 - x0 < 1e-6 or y1 - y0 < 1e-6:
        raise ValueError("The text box has no size: click two opposite corners")
    pts = [App.Vector(x0, y0, 0), App.Vector(x1, y0, 0), App.Vector(x1, y1, 0), App.Vector(x0, y1, 0)]
    ids = [sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), True) for i in range(4)]
    for k in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", ids[k], 2, ids[(k + 1) % 4], 1))
    for k, kind in enumerate(("Horizontal", "Vertical", "Horizontal", "Vertical")):
        sk.addConstraint(Sketcher.Constraint(kind, ids[k]))
    return [sk.getGeometryId(i) for i in ids]


def _make_round(sk, box, frame):
    """The round frame drawn in the box (construction, dashed like it): an ellipse touching its sides, or a circle
    as wide as its shorter side, its centre in the box's middle, held to the box's lines so it follows the corners.
    Returns the geometry ids it added (the curve and its axes), or [] if the box is gone."""
    import Part
    import Sketcher
    from incad.core import sketch_text as st
    idx = [_geo(sk, {"id": i}) for i in box]
    corners = _corners(sk, box)
    if corners is None or any(i is None for i in idx):
        return []
    centre, u, v, width, height = st.box_frame(corners)
    before = sk.GeometryCount
    if frame == "circle":
        geo = Part.Circle(centre, App.Vector(0, 0, 1), min(width, height) / 2.0)
    else:
        a, b = (u, v) if width >= height else (v, u)
        big, small = max(width, height) / 2.0, min(width, height) / 2.0
        geo = Part.Ellipse(centre + a * big, centre + b * small, centre)
    g = sk.addGeometry(geo, True)
    # the centre halfway between two opposite corners
    first = sk.Geometry[idx[0]].StartPoint
    far = sk.Geometry[idx[2]]
    pos = 1 if (far.StartPoint - first).Length > (far.EndPoint - first).Length else 2
    sk.addConstraint(Sketcher.Constraint("Symmetric", idx[0], 1, idx[2], pos, g, 3))

    def along(i, d):  # the box line i runs along the direction d
        line = sk.Geometry[i]
        e = line.EndPoint - line.StartPoint
        return e.Length > 1e-9 and abs(e.normalize().dot(d)) > 0.7

    lines_u = [i for i in idx if along(i, u)]
    lines_v = [i for i in idx if along(i, v)]
    if frame == "circle":
        sk.addConstraint(Sketcher.Constraint("Tangent", g, (lines_u if width >= height else lines_v)[0]))
    else:
        sk.exposeInternalGeometry(g)
        axes = [i for i in range(before + 1, sk.GeometryCount) if sk.Geometry[i].TypeId == "Part::GeomLineSegment"]
        major = max(axes, key=lambda i: sk.Geometry[i].length())
        sk.addConstraint(Sketcher.Constraint("Parallel", major, (lines_u if width >= height else lines_v)[0]))
        sk.addConstraint(Sketcher.Constraint("Tangent", g, lines_u[0]))
        sk.addConstraint(Sketcher.Constraint("Tangent", g, lines_v[0]))
    return [sk.getGeometryId(i) for i in range(before, sk.GeometryCount)]


def _drop_round(sk, ids):
    """The round frame taken out of the sketch (with its axes)."""
    idx = sorted({i for i in (_geo(sk, {"id": x}) for x in ids or []) if i is not None}, reverse=True)
    if idx:
        sk.delGeometries(idx)


def _round_frame(sk, s, box, mode, frame):
    """The sketch shows the round frame chosen (Ellipse / Circle) the moment it is chosen; Rectangle or Along a
    curve takes it away. Kept in the text's settings as `round`: its kind, its box and its geometry ids."""
    want = mode == "box" and frame in ("ellipse", "circle") and bool(box)
    old = s.get("round") or {}
    have = old and all(_geo(sk, {"id": x}) is not None for x in old.get("ids") or [])
    if want and have and old.get("frame") == frame and old.get("box") == list(box):
        return
    if old:
        _drop_round(sk, old.get("ids"))
        s.pop("round", None)
    if want:
        ids = _make_round(sk, list(box), frame)
        if ids:
            s["round"] = {"frame": frame, "box": list(box), "ids": ids}


def _edge(sk, geo):
    g = sk.Geometry[geo] if geo >= 0 else sk.ExternalGeo[-geo - 1]
    return g.toShape()


# ----- the saved object -----
class TextFeature:
    """The proxy. FreeCAD saves its module path (incad.features.sketch_text) and class name in the file."""
    def __init__(self, obj):
        if "Sketch" not in obj.PropertiesList:
            obj.addProperty("App::PropertyLink", "Sketch", "Text", "The sketch the text is written in")
        for prop, kind in _PROP_TYPES.items():
            if prop not in obj.PropertiesList:
                obj.addProperty(kind, prop, "Text", "")
        if MARK not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", MARK, "InCad", "The text's settings")
            obj.setPropertyStatus(MARK, "Hidden")
        obj.Proxy = self

    def execute(self, obj):
        import Part
        from incad.core import sketch_text as st
        sk = obj.Sketch
        if sk is None:
            raise ValueError("The text's sketch is gone")
        s = settings(obj)
        path = st.font_path(s.get("font") or st.DEFAULT_FAMILY, s.get("bold"), s.get("italic"))
        words = s.get("text") or ""
        if not words.strip():
            raise ValueError("Type the text")
        size = obj.Size.Value
        if size <= 0 and not s.get("fit"):
            return  # just made, its values not set yet
        if s.get("mode") == "curve":
            geo = _geo(sk, s.get("curve"))
            if geo is None:
                raise ValueError("The text's curve is gone: pick another one")
            faces = st.along(words, path, size, _edge(sk, geo), s.get("position") or "center", obj.Shift.Value,
                             obj.Distance.Value, bool(s.get("flip")), obj.Spacing.Value, bool(s.get("mirror")))
        elif s.get("mode") == "box" or "box" in s:
            corners = _corners(sk, s.get("box") or [])
            if corners is None:
                raise ValueError("The text's box is gone: pick a box (four lines) or draw one")
            faces = st.in_box(words, path, size, corners, s.get("halign") or "center", s.get("valign") or "middle",
                              obj.Spacing.Value, fit=bool(s.get("fit")), mirror=bool(s.get("mirror")),
                              frame=s.get("frame") or "rect")
        else:  # a text made before the box: at its point
            ref = s.get("point")
            geo = _geo(sk, ref)
            if geo is None:
                raise ValueError("The text's point is gone: pick another one")
            p = sk.getPoint(geo, ref.get("pos", 1)) if not (geo == -1 and ref.get("pos") == 1) else App.Vector()
            faces = st.at_point(words, path, size, (p.x, p.y), s.get("align") or "left",
                                obj.Angle.Value if "Angle" in obj.PropertiesList else 0.0, obj.Spacing.Value)
        if not faces:
            raise ValueError("The text has no letters to draw")
        obj.Shape = Part.makeCompound(faces)
        if not obj.Placement.isSame(sk.Placement, 1e-9):
            obj.Placement = sk.Placement  # in the sketch's plane (the Solid's frame, like the sketch); Extrude
            # takes its direction from it

    def dumps(self):
        return None

    def loads(self, state):
        return None


def settings(obj):
    try:
        return json.loads(getattr(obj, MARK, "") or "{}")
    except ValueError:
        return {}


# ----- the dialog kind -----
def options(key, values):
    if key == "font":
        from incad.core import sketch_text as st
        return [(name, name) for name in sorted(st.fonts(), key=str.lower)]
    return None


def defaults(doc, body, sk):
    values = {f.key: f.default for f in FIELDS}
    values.update(box="", curve="")
    try:  # a curve or a point of the sketch selected before the command: the text goes on it
        import FreeCADGui as Gui
        from incad.features import common
        for sel in Gui.Selection.getSelectionEx(doc.Name):
            for sub in sel.SubElementNames:
                if sel.Object is sk and common.accepts("sketch_box", sk, sub):
                    values.update(mode="box", box=format_picks([(sk, sub)]))
                elif sel.Object is sk and common.accepts("sketch_curve", sk, sub):
                    values.update(mode="curve", curve=format_picks([(sk, sub)]))
    except Exception:
        pass
    return values


def _pick_ref(doc, sk, text):
    picks = parse_picks(doc, text)
    if not picks or picks[0][0] is not sk:
        return None
    return _ref(sk, picks[0][1])


def _box_ids(doc, sk, text):
    picks = parse_picks(doc, text)
    if not picks or picks[0][0] is not sk:
        return None
    from incad.core import sketch_patterns as sp
    try:
        geo, pos = sp.element(sk, picks[0][1])
    except (ValueError, IndexError):
        return None
    lines = box_lines(sk, geo) if pos == 0 else None
    return [sk.getGeometryId(i) for i in lines] if lines else None


def _apply(doc, f, sk, values):
    from incad.core import sketch_text as st
    s = settings(f)
    mode = values.get("mode") or "box"
    box = _box_ids(doc, sk, values.get("box")) or s.get("box")
    curve = _pick_ref(doc, sk, values.get("curve")) or s.get("curve")
    if mode == "box" and not box:
        raise ValueError("Click a line of a box (four lines) for the text, or draw one with Text")
    if mode == "curve" and curve is None:
        raise ValueError("Click a line, arc, circle or curve of the sketch for the text to run along")
    family = values.get("font") or st.DEFAULT_FAMILY
    if family not in st.fonts():
        raise ValueError("The font %s is not installed on this computer" % family)
    s.update(text=values.get("text") or "", font=family, bold=bool(values.get("bold")),
             italic=bool(values.get("italic")), mirror=bool(values.get("mirror")), mode=mode,
             frame=values.get("frame") or "rect",
             halign=values.get("halign") or "center",
             valign=values.get("valign") or "middle", fit=bool(values.get("fit")),
             position=values.get("position") or "center", flip=bool(values.get("flip")), box=box, curve=curve)
    _round_frame(sk, s, box, mode, s["frame"])
    setattr(f, MARK, json.dumps(s, ensure_ascii=False))
    for key, prop in PROPS.items():
        default = next(x.default for x in FIELDS if x.key == key)
        value_param(doc, f, prop, values.get(key) or default, _UNITS[key])


def create(doc, body, sk, values):
    global _pending_box
    if sk is None:
        raise ValueError("Open a sketch first")
    corners, _pending_box = _pending_box, None
    f = doc.addObject("Part::Part2DObjectPython", "Text")
    TextFeature(f)
    f.Label = "Text"
    owner = sk.getParentGeoFeatureGroup() or body
    if owner is not None:
        owner.addObject(f)
    f.Sketch = sk
    from incad.core import document
    document.show_like(f, sk)
    values = dict(values)
    if (values.get("mode") or "box") == "box" and not values.get("box") and corners is not None:
        s = settings(f)
        s["box"] = _make_box(sk, *corners)  # the two corners the user clicked: the text's box
        setattr(f, MARK, json.dumps(s))
    _apply(doc, f, sk, values)
    doc.recompute([f])  # the text and its sketch: the part follows on Finish Sketch (a whole recompute took a minute)
    return f


def update(doc, f, values):
    _apply(doc, f, f.Sketch, values)
    doc.recompute([f])
    return f


def read(doc, f):
    s = settings(f)
    sk = f.Sketch
    values = {x.key: x.default for x in FIELDS}
    values.update({k: v for k, v in s.items() if k in ("text", "font", "bold", "italic", "mirror", "mode", "halign",
                                                        "valign", "fit", "position", "flip", "frame")})
    if values.get("mode") not in ("box", "curve"):
        values["mode"] = "box"  # a text made at a point (before the box): pick or draw a box for it
    sub = _sub(sk, s.get("curve")) if sk is not None and s.get("curve") else ""
    values["curve"] = format_picks([(sk, sub)]) if sub else ""
    values["box"] = _box_text(sk, s.get("box"))
    for key, prop in PROPS.items():
        values[key] = value_text(doc, f, prop) or values[key]
    return values


def _box_text(sk, ids):
    if sk is None or not ids:
        return ""
    g = _geo(sk, {"id": ids[0]})
    return format_picks([(sk, "Edge%d" % (g + 1))]) if g is not None else ""


def adjusted(doc, f):
    """The box drawn from the user's two clicks shows in the Box field (so the next change keeps it)."""
    s = settings(f)
    sk = getattr(f, "Sketch", None)
    if s.get("mode") != "box" or not s.get("box"):
        return {}
    text = _box_text(sk, s["box"])
    return {"box": text} if text else {}


def follow(before, values):
    """Along a curve there is no box to fit: Fit goes off (it hides Size)."""
    if values.get("mode") == "curve" and values.get("fit"):
        return {"fit": False}
    return {}


TEXT = SimpleNamespace(
    __name__="incad.features.sketch_text", TITLE="Text", ICON="text", TYPES=("Part::Part2DObjectPython",),
    claims=is_text, IN_SKETCH=True, NEEDS_PROFILE=False, FIELDS=FIELDS, PROPS=PROPS, options=options,
    defaults=defaults, create=create, read=read, update=update, adjusted=adjusted, follow=follow)
