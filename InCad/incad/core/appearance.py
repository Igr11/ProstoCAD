"""Appearance (the user: "rendering of parts, or at least colour"; "Hide Edges"): a Solid's colour and finish, faces
of it in their own colour and finish, its edges shown or hidden. Kept on the Solid itself (a hidden App property, so
it is saved with the file and every change is one undo step) and drawn through its view's ShapeAppearance — one
material per face. A face is remembered by where it is (its centre and area), not by its number: a feature added
or changed renumbers the faces, and the colour is found again on the face that is still there."""
import json

import FreeCAD as App

PROP = "InCadAppearance"
FINISHES = ("Matte", "Satin", "Glossy", "Metal", "Chrome", "Glass", "Rubber")
DEFAULT_FINISH = "Satin"
# finish -> (shininess, specular: how much white (or for metals the colour itself) in the highlight, diffuse factor,
# transparency); FreeCAD's own default is about Satin
_LOOK = {"Matte": (0.05, 0.03, 1.0, 0.0), "Satin": (0.25, 0.25, 1.0, 0.0), "Glossy": (0.75, 0.6, 1.0, 0.0),
         "Metal": (0.45, 0.9, 0.75, 0.0), "Chrome": (0.92, 1.0, 0.7, 0.0), "Glass": (0.9, 0.7, 0.9, 0.65),
         "Rubber": (0.0, 0.0, 0.85, 0.0)}
PALETTE = ((0.96, 0.96, 0.95), (0.80, 0.80, 0.80), (0.60, 0.61, 0.63), (0.35, 0.36, 0.38), (0.10, 0.10, 0.11),
           (0.85, 0.15, 0.13), (0.95, 0.50, 0.10), (0.98, 0.82, 0.15), (0.25, 0.65, 0.25), (0.10, 0.60, 0.60),
           (0.15, 0.45, 0.85), (0.10, 0.18, 0.45), (0.50, 0.25, 0.70), (0.93, 0.45, 0.65), (0.50, 0.32, 0.18),
           (0.90, 0.84, 0.70))


def record(body):
    """{'color': [r, g, b] | None (the material's), 'finish', 'edges': bool, 'faces': [{'color', 'finish', 'c', 'a'}]}
    — None for a Solid nobody coloured."""
    try:
        text = getattr(body, PROP, "") if PROP in body.PropertiesList else ""
        return json.loads(text) if text else None
    except (ValueError, TypeError):
        return None


def set_record(body, rec):
    """Store `rec` (None: back to the material's look); only a change is written (an equal write is still a step)."""
    text = json.dumps(rec, sort_keys=True) if rec else ""
    if PROP not in body.PropertiesList:
        if not text:
            return
        body.addProperty("App::PropertyString", PROP, "InCad", "Colour, finish, coloured faces, edges (InCad)")
        body.setPropertyStatus(PROP, "Hidden")
    if getattr(body, PROP) != text:
        setattr(body, PROP, text)


def material_color(doc):
    from incad.core import materials
    return materials.MATERIALS[materials.material(doc)][1]


def look(color, finish):
    """The App.Material a face is drawn with."""
    shine, spec, diffuse, transparency = _LOOK.get(finish, _LOOK[DEFAULT_FINISH])
    r, g, b = (float(c) for c in color[:3])
    if finish in ("Metal", "Chrome"):  # a metal's highlight has its own colour
        specular = tuple(min(1.0, spec * (0.35 + 0.65 * c)) for c in (r, g, b))
    else:
        specular = (spec, spec, spec)
    return App.Material(DiffuseColor=(r * diffuse, g * diffuse, b * diffuse),
                        AmbientColor=(r * 0.3, g * 0.3, b * 0.3), SpecularColor=specular,
                        EmissiveColor=(0.0, 0.0, 0.0), Shininess=shine, Transparency=transparency)


def _normal(face):
    """A flat face's outward normal ([x, y, z]), else None."""
    try:
        if face.Surface.TypeId != "Part::GeomPlane":
            return None
        u0, u1, v0, v1 = face.ParameterRange
        n = face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
        return [round(n.x, 6), round(n.y, 6), round(n.z, 6)]
    except Exception:
        return None


def face_entry(face, color, finish, props=None):
    """props: the face's (surface type, centre, area, normal) from _face_props, when worked out already."""
    kind, centre, area, normal = props or (face.Surface.TypeId, face.CenterOfMass, face.Area, _normal(face))
    return {"color": [round(x, 4) for x in color[:3]], "finish": finish,
            "c": [round(v, 6) for v in centre], "a": round(area, 6), "n": normal, "t": kind}


_PROPS = {}  # the faces' hashes -> [(surface type, centre, area, normal)]: worked out once per shape


def _face_props(faces):
    """Each face's surface type, centre of mass, area and normal, kept for these faces (the user: every click hung
    for 20 s on a lid cut by a thread — each painted face had every face's centre of mass worked out again)."""
    key = tuple(f.hashCode() for f in faces)
    props = _PROPS.get(key)
    if props is None:
        if len(_PROPS) > 32:
            _PROPS.clear()
        props = _PROPS[key] = [(f.Surface.TypeId, f.CenterOfMass, f.Area, _normal(f)) for f in faces]
    return props


def find_face(faces, entry, tol, props=None):
    """The index of the face of `faces` the entry stands for, or None: the same area at the same place; else the same
    area facing the same way (a face moved by a longer Extrude); else facing the same way, nearest, not far."""
    c, a = App.Vector(*entry["c"]), entry["a"]
    n = App.Vector(*entry["n"]) if entry.get("n") else None
    same_area, same_way = [], []
    for i, (kind, centre, area, normal) in enumerate(_face_props(faces) if props is None else props):
        if entry.get("t") and kind != entry["t"]:
            continue
        d = (centre - c).Length
        area_ok = abs(area - a) <= max(1e-6, 0.01 * a)
        if area_ok and d <= tol:
            return i
        way = n is not None and normal is not None and App.Vector(*normal).dot(n) > 1 - 1e-6
        if area_ok and (n is None or way):
            same_area.append((d, i))
        elif way:
            same_way.append((d, i))
    if same_area:
        return min(same_area)[1]
    far = tol * 1e3  # 10 % of the part's size (tol is 1e-4 of its diagonal)
    near = [t for t in same_way if t[0] <= far]
    return min(near)[1] if near else None


def entry_at(rec, faces, index, tol):
    """The record's entry for face `index`, or None."""
    for e in (rec or {}).get("faces", []):
        if find_face(faces, e, tol) == index:
            return e
    return None


def _tolerance(shape):
    return max(1e-4, 1e-4 * shape.BoundBox.DiagonalLength)


def wanted(body, rec=None):
    """(the materials, one per face or one for all, edges shown) the Solid should be drawn with."""
    rec = record(body) if rec is None else rec
    rec = rec or {}
    color = rec.get("color") or material_color(body.Document)
    base = look(color, rec.get("finish") or DEFAULT_FINISH)
    edges = rec.get("edges", True)
    entries = rec.get("faces") or []
    shape = body.Shape
    if not entries or shape is None or shape.isNull():
        return [base], edges
    faces = shape.Faces
    mats = [base] * len(faces)
    tol = _tolerance(shape)
    props = _face_props(faces)  # (once: telling the faces apart by their hashes for every entry took longer than all)
    for e in entries:
        i = find_face(faces, e, tol, props)
        if i is not None:
            mats[i] = look(e["color"], e.get("finish") or DEFAULT_FINISH)
    return mats, edges


def _same(a, b):
    def key(m):
        return tuple(round(x, 3) for x in tuple(m.DiffuseColor[:3]) + tuple(m.SpecularColor[:3])) + \
            (round(m.Shininess, 3), round(m.Transparency, 3))
    return len(a) == len(b) and all(key(x) == key(y) for x, y in zip(a, b))


def draw(body, mats, edges, texture=None):
    """Put the look on the Solid's view (only what differs: an equal write still redraws)."""
    vo = getattr(body, "ViewObject", None)
    if vo is None or not hasattr(vo, "ShapeAppearance"):
        return
    from incad.core import textures
    textures.put(body, texture)
    if not _same(list(vo.ShapeAppearance), mats):
        vo.ShapeAppearance = mats
    mode = getattr(vo, "DisplayMode", "")
    if mode in ("Flat Lines", "Shaded"):  # Wireframe and Points are the view's style: left alone
        want = "Flat Lines" if edges else "Shaded"
        if mode != want:
            vo.DisplayMode = want


_drawn = {}  # body key -> what it was last drawn from (a refresh redraws only what changed)


def apply(body, force=False):
    """Draw the Solid as its record says; a Solid nobody coloured is left as it is."""
    rec = record(body)
    if rec is None and not force:
        from incad.core import textures
        if App.GuiUp and textures.name_on(body) != "None":  # its first texture undone: the view's nodes go too
            textures.put(body, None)
        return False
    if not App.GuiUp or body.Document.Restoring or body.Document.Recomputing:
        return False
    shape = body.Shape
    key = (body.Document.Name, body.Name)
    from incad.core import textures
    stamp = (json.dumps(rec, sort_keys=True), shape.hashCode() if not shape.isNull() else 0, textures.name_on(body),
             id(body.ViewObject.RootNode) if body.ViewObject is not None else 0)
    if not force and _drawn.get(key) == stamp:
        return False
    mats, edges = wanted(body, rec)
    draw(body, mats, edges, (rec or {}).get("texture"))
    _drawn[key] = stamp[:2] + (textures.name_on(body),) + stamp[3:]
    return True


def apply_all(doc):
    """Every coloured Solid of the part (a refresh after a recompute, an undo, a file opened)."""
    from incad.core import document
    if doc is None:
        return
    for body in document.bodies(doc):
        try:
            apply(body)
        except Exception:
            from incad.core import log
            log.logger().exception("appearance of %s", body.Name)


def change(body, face_indices, color=None, finish=None, edges=None, reset=False, texture=None):
    """The record after a change: the whole Solid (no faces) or those faces get the colour and finish (None: keep);
    reset: the Solid back to the material's look, or those faces back to the Solid's. edges: the whole Solid's."""
    rec = dict(record(body) or {})
    shape = body.Shape
    faces = shape.Faces if shape is not None and not shape.isNull() else []
    tol = _tolerance(shape) if faces else 1e-4
    entries = list(rec.get("faces") or [])
    if face_indices:
        picked = [i for i in face_indices if 0 <= i < len(faces)]
        chosen = set(picked)
        where = [find_face(faces, e, tol) for e in entries]  # once: per picked face it took 55 s for 185 letters
        kept = [e for e, w in zip(entries, where) if w not in chosen]
        first = {}
        for e, w in zip(entries, where):
            first.setdefault(w, e)  # entry_at's answer: the first entry standing for that face
        if not reset:
            props = _face_props(faces)
            for i in picked:
                old = first.get(i) or {}
                c = color or old.get("color") or rec.get("color") or material_color(body.Document)
                f = finish or old.get("finish") or rec.get("finish") or DEFAULT_FINISH
                kept.append(face_entry(faces[i], c, f, props[i]))
        rec["faces"] = kept
    elif reset:
        rec = {}
    else:
        if color is not None:
            rec["color"] = [round(x, 4) for x in color[:3]]
        if finish is not None:
            rec["finish"] = finish
    if edges is not None:
        rec["edges"] = bool(edges)
    if texture is not None:
        rec["texture"] = texture
    if rec.get("texture") in (None, "None"):
        rec.pop("texture", None)
    if not rec.get("faces"):
        rec.pop("faces", None)
    if rec.get("edges", True):
        rec.pop("edges", None)
    return rec or None
