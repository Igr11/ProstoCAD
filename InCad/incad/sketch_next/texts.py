"""Text in the new sketch, as the old program's Text object (ProstoCAD 0.18 New Sketch, stage 4).

The new sketch holds a text as a tie (kind "sk_text") on its box's four corners, or on the points of the curve it runs
along; the old program holds it as a Text object beside the sketch (incad.features.sketch_text), which Extrude raises.
Here:
- outlines(view, tie): the letters the new sketch draws, built by the Text object's own code (incad.core.sketch_text),
  so the sketch shows exactly what the part gets;
- read(sk, data): each Text object of the sketch as a tie in the new sketch's data (on Open);
- write(sk, data, done): each tie as a Text object again (on Finish), a tie taken away: its object deleted;
- fonts(): the fonts the part can build its letters from (the window's list).
Size, Letter spacing, Shift and Distance stay named parameters (d12 …) as the old program has them."""
import json
import math

from incad.core import log

FIELD_PROPS = {"size": "Size", "spacing": "Spacing", "shift": "Shift", "distance": "Distance"}
KEYS = ("text", "font", "bold", "italic", "mirror", "halign", "valign", "fit", "position", "flip", "frame")


def fonts():
    from incad.core import sketch_text as st
    return sorted(st.fonts(), key=str.lower)


def names(sk):
    """{name: value in mm or degrees} of the part's named values outside this sketch (dimensions of its other
    sketches, the features' dN: a Text's Size): what a formula in the sketch may use, as in the old program."""
    import FreeCAD as App
    from incad.core import model, sketch as sketches
    own = {name for name, s, _ in sketches.dimensions(sk.Document) if s is sk}
    out = {}
    for name, _formula, value in model.model_parameters(sk.Document):
        if name in own:
            continue
        try:
            out[name] = float(App.Units.Quantity(value).Value)
        except Exception:
            try:
                out[name] = float(value)
            except Exception:
                pass
    return out


# ----- the letters, drawn in the new sketch -----
def _curve_edge(view, t):
    """The Part edge of the curve a text runs along (in sketch coordinates), or None."""
    from incad.sketch_next import convert
    n = t["ref"].get("nbox", 4)
    have = list(t["pts"][n:])
    if not have:
        return None
    for item in view._all_shapes(everything=True):
        if list(view._shape_points(item)) != have:
            continue
        kind, i = item
        shape = {"line": view.lines, "arc": view.arcs, "ell": view.ells}.get(kind)
        shape = shape[i] if shape is not None else view.spls[i]
        cut = None
        if kind == "ell":
            cut = next((q["pts"][:2] for q in view.ties if q["kind"] == "ell_cut" and list(q["pts"][2:]) == list(shape)), None)
        data = {"pts": [(p.x(), p.y()) for p in view.pts]}
        return convert._geometry(kind, list(shape) if kind != "spl" else shape, data, cut).toShape()
    return None


def faces(view, t):
    """The Text object's faces for this tie, from the view's points as they stand now."""
    import FreeCAD as App
    from incad.core import sketch_text as st
    s = t["ref"]
    path = st.font_path(s.get("font") or st.DEFAULT_FAMILY, s.get("bold"), s.get("italic"))
    words = str(s.get("text") or "")
    if not words.strip():
        return []
    size = float(s.get("size") or 0.0)
    if s.get("placement") == "curve":
        edge = _curve_edge(view, t)
        if edge is None:
            return []
        return st.along(words, path, size, edge, s.get("position") or "center", float(s.get("shift") or 0.0),
                        float(s.get("distance") or 0.0), bool(s.get("flip")), float(s.get("spacing") or 0.0),
                        bool(s.get("mirror")))
    corners = [App.Vector(view.pts[k].x(), view.pts[k].y(), 0) for k in t["pts"][:4]]
    return st.in_box(words, path, size, corners, s.get("halign") or "center", s.get("valign") or "middle",
                     float(s.get("spacing") or 0.0), fit=bool(s.get("fit")), mirror=bool(s.get("mirror")),
                     frame=s.get("frame") or "rect")


def outlines(view, t):
    """The letters' outlines (lists of sketch points) for the new sketch to draw; [] when there is nothing to draw (a
    text longer than its curve, an empty one)."""
    from PySide6 import QtCore
    try:
        made = faces(view, t)
    except ValueError:
        return []
    out = []
    for f in made:
        for w in f.Wires:
            pts = w.discretize(Deflection=0.01)
            out.append([QtCore.QPointF(p.x, p.y) for p in pts])
    return out


# ----- reading: Text objects into the new sketch's data -----
def _corner_points(data, sk, ids):
    """The new sketch's point indexes of a box's four corners (in order round it), from its lines' geometry ids."""
    line_ids = data.get("shape_ids", {}).get("line", [])
    pts = []
    for g in ids:
        if g not in line_ids:
            return None
        a, b = data["lines"][line_ids.index(g)]
        for k in (a, b):
            if k not in pts:
                pts.append(k)
    if len(pts) != 4:
        return None
    xy = data["pts"]
    c = (sum(xy[k][0] for k in pts) / 4.0, sum(xy[k][1] for k in pts) / 4.0)
    return sorted(pts, key=lambda k: math.atan2(xy[k][1] - c[1], xy[k][0] - c[0]))


def _curve_points(data, sk, ref):
    """The new sketch's points of the curve a text runs along (as _shape_points gives them), or None."""
    if ref is None:
        return None
    if "ext" in ref:
        gid = "ext:%d" % ref["ext"]
    else:
        gid = ref["id"]
    for kind, key in (("line", "lines"), ("arc", "arcs"), ("ell", "ells"), ("spl", "spls")):
        ids = data.get("shape_ids", {}).get(kind, [])
        if gid in ids:
            shape = data[key][ids.index(gid)]
            if kind == "spl":
                return list(shape[1])
            if kind == "ell":
                cut = next((q["pts"][:2] for q in data["ties"] if q["kind"] == "ell_cut" and list(q["pts"][2:]) == list(shape)), None)
                return list(shape) + (list(cut) if cut else [])
            return list(shape)
    return None


def _value(doc, f, prop):
    """(number in mm, formula or None) of one of the Text's named values."""
    from incad.features.common import value_text
    v = float(getattr(f, prop).Value) if prop in f.PropertiesList else 0.0
    text = value_text(doc, f, prop) or ""
    plain = text.replace("mm", "").strip()
    try:
        float(plain)
        expr = None
    except ValueError:
        expr = text or None
    return v, expr


def read(sk, data):
    """Each Text object written in the sketch, as a tie of the new sketch (appended to data's ties). A text whose box
    or curve is not in the new sketch's data is left as it is (said in the log)."""
    from incad.features import sketch_text as T
    doc = sk.Document
    for f in T.texts_of(sk):
        s = T.settings(f)
        ref = {k: s[k] for k in KEYS if k in s}
        ref.setdefault("frame", "rect")
        for key, prop in FIELD_PROPS.items():
            v, expr = _value(doc, f, prop)
            ref[key] = v
            if expr:
                ref[key + "_expr"] = expr
        if s.get("mode") == "curve":
            pts = _curve_points(data, sk, s.get("curve"))
            ref.update(placement="curve", nbox=0)
        else:
            pts = _corner_points(data, sk, s.get("box") or [])
            ref.update(placement="box", nbox=4)
        if pts is None:
            log.logger().info("new sketch: Text %s has no box or curve here: left as it is", f.Label)
            continue
        ref["obj"] = f.Name
        data["ties"].append(dict(kind="sk_text", pts=pts, ref=ref, mark=None))


# ----- writing: the new sketch's texts as Text objects -----
def write(sk, data, slots):
    """Each sk_text tie of data as a Text object (made, or the one it came from brought to it); a Text object of the
    sketch no tie names any more is deleted. slots(item points) -> the geometry id of the line / curve with those
    points in the written sketch, as the Text keeps it ({"id": geometry id, "pos": 0}, a projected one {"ext": -3 …, "pos": 0}),
    or None. Inside the sketch's undo step."""
    from incad.features import sketch_text as T
    from incad.features.common import value_param
    doc = sk.Document
    had = {f.Name: f for f in T.texts_of(sk)}
    keep = set()
    for t in data["ties"]:
        if t["kind"] != "sk_text":
            continue
        r = t["ref"]
        f = had.get(r.get("obj")) if r.get("obj") not in keep else None  # a copy of a text: a Text of its own
        s = T.settings(f) if f is not None else {}
        s.update({k: r[k] for k in KEYS if k in r})
        if r.get("placement") == "curve":
            g = slots(tuple(t["pts"][r.get("nbox", 0):]))
            if g is None:
                log.report("warning", "Text: its curve is gone, the text is left out")
                continue
            s.update(mode="curve", curve=g)
        else:
            corners = t["pts"][:4]
            ids = [slots((corners[i], corners[(i + 1) % 4])) for i in range(4)]
            if any(g is None or "id" not in g for g in ids):
                log.report("warning", "Text: its box is gone, the text is left out")
                continue
            s.update(mode="box", box=[g["id"] for g in ids])
        if f is None:
            f = doc.addObject("Part::Part2DObjectPython", "Text")
            T.TextFeature(f)
            f.Label = "Text"
            owner = sk.getParentGeoFeatureGroup()
            if owner is not None:
                owner.addObject(f)
            f.Sketch = sk
            from incad.core import document
            document.show_like(f, sk)
        setattr(f, T.MARK, json.dumps(s, ensure_ascii=False))
        for key, prop in FIELD_PROPS.items():
            expr = r.get(key + "_expr")
            v = float(r.get(key) or 0.0)
            value_param(doc, f, prop, expr if expr else "%.10g mm" % v, "length")
        keep.add(f.Name)
        f.touch()
    from incad.core import delete
    from incad.features.common import consumers
    for name, f in had.items():
        if name in keep:
            continue
        if consumers(f):  # an Extrude raises it: left as the old program leaves it when its box goes (its error says
            # so), the Extrude is not broken behind the user's back
            log.report("warning", "Text %s is used by %s: it stays (delete it from the model tree)"
                       % (f.Label, ", ".join(o.Label for o in consumers(f))))
            continue
        delete.delete(f)
    return keep
