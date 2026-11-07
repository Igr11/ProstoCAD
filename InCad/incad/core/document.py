"""An InCad Part is a normal FreeCAD document holding PartDesign Bodies. InCad marks it in doc.Meta and keeps
the Body concept away from the user. Pure model logic: no Qt, no GUI."""
import re

import FreeCAD as App

TYPE_KEY = "InCadType"
UNITS_KEY = "InCadUnits"
PRIMARY_KEY = "InCadPrimaryBody"
UNIT_SCHEMES = {"mm": "Standard (mm, kg, s, °)", "in": "Imperial decimal (in, lb)"}
PARAMS_NAME = "InCadParameters"


def _meta_set(doc, **items):
    meta = dict(doc.Meta)
    meta.update(items)
    doc.Meta = meta


def is_incad_part(doc):
    return doc is not None and doc.Meta.get(TYPE_KEY) == "Part"


def part_units(doc):
    units = doc.Meta.get(UNITS_KEY) if doc is not None else None
    return units if units in UNIT_SCHEMES else None


def set_units(doc, units):
    if units not in UNIT_SCHEMES:
        raise ValueError("Units must be 'mm' or 'in', got %r" % (units,))
    _meta_set(doc, **{UNITS_KEY: units})
    if "UnitSystem" in doc.PropertiesList:
        doc.UnitSystem = UNIT_SCHEMES[units]


def bodies(doc):
    return [o for o in doc.Objects if o.TypeId == "PartDesign::Body"]


def primary_body(doc):
    if doc is None:
        return None
    name = doc.Meta.get(PRIMARY_KEY)
    body = doc.getObject(name) if name else None
    return body if body is not None and body.TypeId == "PartDesign::Body" else None


def make_part(doc, units="mm"):
    if units not in UNIT_SCHEMES:
        raise ValueError("Units must be 'mm' or 'in', got %r" % (units,))
    if is_incad_part(doc):
        raise ValueError("%s is already an InCad part" % doc.Label)
    if bodies(doc):
        raise ValueError("This file already has bodies; use Convert to InCad Part")
    body = doc.addObject("PartDesign::Body", "Body")
    body.Label = "Solid1"
    _meta_set(doc, **{TYPE_KEY: "Part", PRIMARY_KEY: body.Name})
    set_units(doc, units)
    return body


def convert_to_part(doc, units="mm"):
    found = bodies(doc)
    if len(found) != 1:
        raise ValueError("Only a file with exactly one Body can be converted (this one has %d)" % len(found))
    _meta_set(doc, **{TYPE_KEY: "Part", PRIMARY_KEY: found[0].Name})
    set_units(doc, units)
    return found[0]


def new_solid(doc):
    if not is_incad_part(doc):
        raise ValueError("New Solid needs an InCad part")
    body = doc.addObject("PartDesign::Body", "Body")
    body.Label = "Solid%d" % len(bodies(doc))
    return body


FACE_REF_PROP = "InCadFaceRefOf"


def _foreign_support(sketch, body):
    """The object of another Solid the sketch stands on (a face of the part), or None."""
    sup = getattr(sketch, "AttachmentSupport", None) or []
    obj = sup[0][0] if sup else None
    if obj is None or getattr(obj, FACE_REF_PROP, None):
        return None
    owner = obj.getParentGeoFeatureGroup() if hasattr(obj, "getParentGeoFeatureGroup") else None
    if obj.TypeId == "PartDesign::Body":
        owner = obj
    return obj if owner is not None and owner is not body and owner.TypeId == "PartDesign::Body" else None


def _projects_from(sketch, solid):
    for obj, _subs in getattr(sketch, "ExternalGeometry", None) or []:
        owner = obj.getParentGeoFeatureGroup() if hasattr(obj, "getParentGeoFeatureGroup") else None
        if obj is solid or owner is solid:
            return True
    return False


def settle_support(sketch, body):
    """A sketch in `body` standing on a face of another Solid. A Pad first in a Solid takes its sketch's support as
    its base, so the new Solid would hold that whole other Solid too. With nothing projected from the other Solid
    the face was only a plane: the sketch is left where it is, unattached (an independent Solid). With projections
    it depends on that Solid: it is attached through a copy of the face inside `body` (a SubShapeBinder), so it
    follows the face and the Solid still holds only its own piece. Returns True when it changed the sketch."""
    obj = _foreign_support(sketch, body)
    if obj is None:
        return False
    other = obj if obj.TypeId == "PartDesign::Body" else obj.getParentGeoFeatureGroup()
    doc = sketch.Document
    if _projects_from(sketch, other):
        glob = sketch.getGlobalPlacement()
        sub = sketch.AttachmentSupport[0][1]
        sub = sub[0] if isinstance(sub, (tuple, list)) and sub else sub
        ref = doc.addObject("PartDesign::SubShapeBinder", "FaceRef")
        body.insertObject(ref, sketch, False)  # before the sketch it carries
        ref.Support = [(obj, (sub,) if sub else ())]
        ref.addProperty("App::PropertyString", FACE_REF_PROP, "InCad", "The sketch this face copy carries")
        ref.setPropertyStatus(FACE_REF_PROP, "Hidden")
        setattr(ref, FACE_REF_PROP, sketch.Name)
        _set_visible(ref, False)
        doc.recompute()
        sketch.AttachmentSupport = [(ref, "Face1")]
        sketch.recompute()
        # the copy's face has an origin of its own (a work plane's sheet): the sketch stays exactly where it was
        # drawn (the user: a feature never moves its sketch)
        frame = sketch.Placement.multiply(sketch.AttachmentOffset.inverse())
        want = body.getGlobalPlacement().inverse().multiply(glob)
        if not want.isSame(sketch.Placement, 1e-9):
            sketch.AttachmentOffset = frame.inverse().multiply(want)
            sketch.recompute()
    else:
        glob = sketch.getGlobalPlacement()
        sketch.MapMode = "Deactivated"
        sketch.AttachmentSupport = None
        sketch.Placement = body.getGlobalPlacement().inverse().multiply(glob)
    return True


def settle_cross_solid_sketches(doc):
    """Files from before: a sketch in one Solid still standing on a face of another (the new Solid then held that
    other Solid too). Each is settled as settle_support does. Returns the sketches changed."""
    changed = []
    for b in bodies(doc):
        old = {f.Name: f.Shape.copy() for f in b.Group
               if f.isDerivedFrom("PartDesign::Feature") and not f.Shape.isNull()}
        places = {o.Name: App.Placement(o.Placement) for o in b.Group if o.TypeId == "Sketcher::SketchObject"}
        settled = [o for o in list(b.Group) if o.TypeId == "Sketcher::SketchObject" and settle_support(o, b)]
        if not settled:
            continue
        changed += settled
        for o in settled:
            o.touch()  # untying moves nothing: the steps on it would keep the shape that held the other Solid
        doc.recompute()
        _refind_faces(b, old, places)
    if changed:  # the old Pad took the other Solid's last step as its base, and FreeCAD hid that step
        for b in bodies(doc):
            show_result(b)
    return changed


def _refind_faces(body, old, places):
    """The Solid's steps lost the other Solid's faces, so theirs are numbered anew: a sketch standing on one (a cut
    sketched on the piece) and a fillet's or chamfer's edges are found again on the new shape. A sketch keeps the
    place it had (`places`): the same face found again may lie the other way round in FreeCAD's eyes."""
    doc = body.Document
    for o in body.Group:
        if o.TypeId == "Sketcher::SketchObject":
            sup = o.AttachmentSupport
            if not sup or sup[0][0] is None or sup[0][0].Name not in old:
                continue
            sub = sup[0][1]
            sub = sub[0] if isinstance(sub, (tuple, list)) and sub else sub
            fresh = remap_sub(old[sup[0][0].Name], sup[0][0].Shape, sub) if sub else None
            if fresh and fresh != sub:
                o.AttachmentSupport = [(sup[0][0], fresh)]
                doc.recompute()
                was = places.get(o.Name)
                if was is not None and not o.Placement.isSame(was, 1e-9):
                    o.AttachmentOffset = o.AttachmentOffset.multiply(o.Placement.inverse()).multiply(was)
        elif o.isDerivedFrom("PartDesign::DressUp") and o.Base:
            src, subs = o.Base[0], list(o.Base[1] or [])
            if src is None or src.Name not in old:
                continue
            fresh = [remap_sub(old[src.Name], src.Shape, s) or s for s in subs]
            if fresh != subs:
                o.Base = (src, fresh)
        doc.recompute()


def _solid_count(obj):
    try:
        shape = obj.Shape
        return 0 if shape.isNull() else len(shape.Solids)
    except Exception:
        return 0


def is_detached_lump(feature):
    """True for an additive feature that added a solid of its own, not touching what came before (a box drawn
    away from the part, wherever it sits in the tree): it belongs in its own Solid."""
    body = feature.getParentGeoFeatureGroup() if hasattr(feature, "getParentGeoFeatureGroup") else None
    if body is None or body.TypeId != "PartDesign::Body":
        return False
    if not feature.isDerivedFrom("PartDesign::FeatureAddSub") or feature.TypeId.startswith("PartDesign::Subtractive") \
            or feature.TypeId in ("PartDesign::Pocket", "PartDesign::FeatureSubtractivePython"):
        return False
    base = getattr(feature, "BaseFeature", None)
    return base is not None and _solid_count(feature) > _solid_count(base) >= 1


def _profile_of(feature):
    prof = getattr(feature, "Profile", None)
    return prof[0] if isinstance(prof, (tuple, list)) and prof else prof


def show_like(feature, base):
    """A Python feature made from code (Split, Hole ▸ Chamfer) gets a view provider with no display mode, so it
    draws nothing when it is the Tip: give it the display mode and look of the step before it."""
    import FreeCAD as App
    if not App.GuiUp or getattr(feature, "ViewObject", None) is None:
        return
    vo = feature.ViewObject
    if getattr(vo, "Proxy", None) is None:
        from incad.features.view_python import ViewProviderFeature
        ViewProviderFeature(vo)
    bvo = getattr(base, "ViewObject", None) if base is not None else None
    for mode in (getattr(bvo, "DisplayMode", None), "Flat Lines"):  # modes may not be listed yet on a new one
        if mode:
            try:
                vo.DisplayMode = mode
                break
            except Exception:
                continue
    for prop in ("ShapeAppearance", "LineColor", "PointColor", "LineWidth"):
        if bvo is not None and hasattr(bvo, prop) and hasattr(vo, prop):
            try:
                setattr(vo, prop, getattr(bvo, prop))
            except Exception:
                pass


def lump_at(body, point):
    """The feature that added the separate piece of `body` holding `point` (a box drawn away from the part, left in
    the part by an older file), or None when the body is one piece. The latest such feature wins."""
    import Part
    solids = body.Shape.Solids if body is not None and not body.Shape.isNull() else []
    if len(solids) < 2:
        return None
    probe = Part.Vertex(point)
    piece = min(solids, key=lambda s: s.distToShape(probe)[0])
    found = None
    for f in body.Group:
        if not is_detached_lump(f):
            continue
        base = f.BaseFeature.Shape
        added = [s for s in f.Shape.Solids if s.distToShape(base)[0] > 1e-7]
        if any(s.distToShape(piece)[0] < 1e-7 for s in added):
            found = f
    return found


def _on_piece(obj, subs, piece):
    """Is the attachment (obj, subs) on the separate piece? A feature's shape holds the whole part, so its face may
    lie on the rest of it; an element that can't be found counts as on the piece (as before)."""
    if not piece or not subs or not hasattr(obj, "Shape"):
        return True
    for sub in subs:
        try:
            el = obj.Shape.getElement(sub)
        except Exception:
            return True
        if any(el.distToShape(p)[0] < 1e-6 for p in piece):
            return True
    return False


def detach_lump(feature):
    """Move a detached feature into a new Solid with its profile sketch and every later feature built on it;
    the features left behind are rebuilt on the one before it. Returns the new Solid."""
    doc = feature.Document
    body = feature.getParentGeoFeatureGroup()
    if not is_detached_lump(feature):
        raise ValueError("%s is not a separate piece of the part" % feature.Label)
    group = list(body.Group)
    before = feature.BaseFeature  # what the part was before the piece: faces of the part named on the piece's
    base = before.Shape           # steps are found again on it
    piece = [p for p in feature.Shape.Solids if p.distToShape(base)[0] > 1e-7]
    related = [feature]  # the piece, its sketch, anything attached to it or built on those, in body order
    grown = True
    while grown:
        grown = False
        for o in group:
            if o in related:
                continue
            attached = getattr(o, "AttachmentSupport", None) or []
            links = [x for x in o.OutList if x is not body and x is not getattr(o, "BaseFeature", None)
                     and x not in [sup for sup, _ in attached]]
            support = [sup for sup, subs in attached
                       if _on_piece(sup, subs, piece)]  # a face of a later step may be on the rest of the part
            profiles = [_profile_of(r) for r in related]
            # every sketch a moving step is built from goes with it: a Sweep's path, a Loft's sections (left
            # behind, FreeCAD said "links go out of the allowed scope" and the move undid itself over and over)
            inputs = [x for r in related if r.TypeId != "Sketcher::SketchObject" for x in r.OutList]
            if (o in profiles or (o.TypeId == "Sketcher::SketchObject" and o in inputs)
                    or any(r in links or r in support for r in related)) and (
                    o.TypeId == "Sketcher::SketchObject" or o.isDerivedFrom("PartDesign::Feature")):
                related.append(o)
                grown = True
    related.sort(key=group.index)
    for sk in [o for o in related if o.TypeId == "Sketcher::SketchObject"]:
        users = [u for u in sk.InList if u in group and u not in related and u.isDerivedFrom("PartDesign::Feature")]
        if users:
            raise ValueError("%s shares %s with %s: it stays in %s" % (feature.Label, sk.Label, users[0].Label,
                                                                        body.Label))
    movers = [o for o in related if o.TypeId != "Sketcher::SketchObject"]
    sketches = [o for o in related if o.TypeId == "Sketcher::SketchObject"]
    old_shapes = {m.Name: m.Shape.copy() for m in movers if not m.Shape.isNull()}
    was_tip = body.Tip in movers
    new = new_solid(doc)
    for m in reversed(movers):  # FreeCAD relinks the feature after each removed one onto the one before it
        body.removeObject(m)
    for p in sketches:
        body.removeObject(p)
        support = p.AttachmentSupport
        if support and support[0][0] is not None and support[0][0].TypeId in ORIGIN_TYPES \
                and getattr(support[0][0], "Role", None):  # an origin plane of the old body: the same of the new
            same = [o for o in new.Origin.OriginFeatures if o.Role == support[0][0].Role]
            if same:
                p.AttachmentSupport = [(same[0], support[0][1])]
        new.addObject(p)
        # on a face of the part that stays behind (the box was sketched on the part, extruded away): a Pad takes
        # its sketch's support as its base, so the new Solid would keep the whole part — independent unless it
        # projects from the part (then it follows the face through a copy of it)
        settle_support(p, new)
    for i, m in enumerate(movers):
        new.addObject(m)
        if i == 0:
            try:
                m.BaseFeature = None
            except Exception:
                pass
        new.Tip = m
    if was_tip:
        solids = [o for o in body.Group if _is_solid_feature(o)]
        body.Tip = solids[-1] if solids else None
    doc.recompute()
    for p in group:  # left behind, attached to a face of the part named on a moved step: the same face before it
        support = getattr(p, "AttachmentSupport", None) if p not in related else None
        if not support or support[0][0] is None or support[0][0].Name not in old_shapes:
            continue
        sub = support[0][1]
        sub = sub[0] if isinstance(sub, (tuple, list)) and sub else sub
        fresh = remap_sub(old_shapes[support[0][0].Name], before.Shape, sub) if sub else None
        if fresh:
            p.AttachmentSupport = [(before, fresh)]
        else:  # not on the part before the piece: keep its place, unattached
            glob = p.Placement
            p.MapMode = "Deactivated"
            p.AttachmentSupport = None
            p.Placement = glob
    doc.recompute()
    for p in sketches:  # a sketch on a face of a moved piece: that face has a new number now
        support = p.AttachmentSupport
        if not support or support[0][0] is None or support[0][0].Name not in old_shapes:
            continue
        sub = support[0][1]
        sub = sub[0] if isinstance(sub, (tuple, list)) and sub else sub  # FreeCAD hands subs back as a tuple
        if not sub:
            continue
        fresh = remap_sub(old_shapes[support[0][0].Name], support[0][0].Shape, sub)
        if fresh and fresh != sub:
            p.AttachmentSupport = [(support[0][0], fresh)]
    doc.recompute()
    show_result(body)
    show_result(new)
    return new


def remap_sub(old_shape, new_shape, sub):
    """The element of `new_shape` that is the same face/edge/vertex as `sub` of `old_shape` (a feature moved to
    its own Solid renumbers its elements): nearest centre of the same kind, or None. FreeCAD marks an element it lost
    '?Face12'."""
    sub = sub.lstrip("?")
    try:
        old = old_shape.getElement(sub)
    except Exception:
        return None
    kind = old.ShapeType
    pool = {"Face": new_shape.Faces, "Edge": new_shape.Edges, "Vertex": new_shape.Vertexes}.get(kind)
    if not pool:
        return None
    centre = old.CenterOfMass if kind != "Vertex" else old.Point
    best, best_d = None, None
    for i, el in enumerate(pool):
        c = el.CenterOfMass if kind != "Vertex" else el.Point
        d = (c - centre).Length
        if best_d is None or d < best_d:
            best, best_d = i, d
    return "%s%d" % (kind, best + 1) if best is not None and best_d < 1e-3 else None


def body_of(obj):
    """The Solid an object belongs to: a feature's or sketch's body, an origin item's body; None otherwise."""
    if obj is None:
        return None
    if getattr(obj, "TypeId", "") == "PartDesign::Body":
        return obj
    if getattr(obj, "TypeId", "") in ORIGIN_TYPES:
        origin = _origin_of(obj)
        for o in (origin.InList if origin is not None else []):
            if o.TypeId == "PartDesign::Body":
                return o
        return None
    owner = obj.getParentGeoFeatureGroup() if hasattr(obj, "getParentGeoFeatureGroup") else None
    return owner if owner is not None and owner.TypeId == "PartDesign::Body" else None


def target_body(obj):
    """The Solid a feature built on this sketch belongs to: the one owning the face the sketch is attached to (a
    sketch can be left in another Solid when the piece it sits on moved, or when its feature was deleted), else
    the sketch's own Solid."""
    for support in getattr(obj, "AttachmentSupport", None) or []:
        owner = support[0] if isinstance(support, (tuple, list)) else support
        if owner is not None and getattr(owner, "TypeId", "").startswith("PartDesign::") \
                and owner.TypeId != "PartDesign::Body" and owner.TypeId not in DATUM_TYPES:
            body = body_of(owner)
            if body is not None:
                return body
    return body_of(obj)


def adopt(body, obj):
    """Move a sketch into `body` from the Solid it sits in; a secondary Solid left with nothing is removed, with
    its origin (the user saw a fourth Solid appear for a Cut on a sketch left in an empty third one)."""
    old = body_of(obj)
    if old is None or old is body:
        return
    doc = body.Document
    old.removeObject(obj)
    body.addObject(obj)
    if old.Group or old is primary_body(doc):
        return
    origin = old.Origin
    leftovers = ([o.Name for o in origin.OriginFeatures] + [origin.Name]) if origin is not None else []
    doc.removeObject(old.Name)  # FreeCAD takes the origin with the body; whatever stays is removed here
    for name in leftovers:
        if doc.getObject(name) is not None:
            doc.removeObject(name)


def history(doc):
    """Every Solid's objects in one list of (body, obj), in the order they were made: Inventor's one history for
    the part. Each Solid keeps its own order; the Solids interleave by creation (a sketch moved into a Solid
    keeps its place in time)."""
    order = {o.Name: i for i, o in enumerate(doc.Objects)}
    lanes = [(b, list(b.Group)) for b in bodies(doc)]
    heads = [0] * len(lanes)
    out = []
    while True:
        best = None
        for k, (b, lane) in enumerate(lanes):
            if heads[k] < len(lane):
                idx = order.get(lane[heads[k]].Name, 0)
                if best is None or idx < best[0]:
                    best = (idx, k)
        if best is None:
            return out
        b, lane = lanes[best[1]]
        out.append((b, lane[heads[best[1]]]))
        heads[best[1]] += 1


def set_end_of_part_at(doc, feature):
    """The part's one End of Part, after `feature` in the history (None: before everything): every Solid rolls
    back to its last object at or before that point, so a drop on another Solid's row means the same thing.
    Sketches, Text and work features after that point roll back too (Inventor; FreeCAD's Tip is only ever a
    solid feature, so End of Part dropped before them went back below them: the user)."""
    hist = history(doc)
    pos = next((i for i, (_, o) in enumerate(hist) if o is feature), -1) if feature is not None else -1
    if feature is not None and pos == -1:
        raise ValueError("%s is not part of a Solid" % feature.Label)
    for body in bodies(doc):
        last = None
        for b, o in hist[:pos + 1]:
            if b is body:
                last = o
        set_end_of_part(body, last)
    _mark_end(doc, feature, [o for _, o in hist[pos + 1:]])


END_AFTER = "InCadEndAfter"    # on the main Solid: the object End of Part follows when sketches etc. lie below it
END_ROLLED = "InCadEndRolled"  # the sketches, Texts, work features rolled back below it then (JSON names)
END_HIDDEN = "InCadEndHidden"  # those of them InCad hid for it (shown again when they come back)
_TOP = "^"  # End of Part before everything


def _end_prop(body, name, value):
    if name not in body.PropertiesList:
        if not value:
            return
        body.addProperty("App::PropertyString", name, "InCad", "Where End of Part is (InCad)")
        body.setPropertyStatus(name, "Hidden")
    if getattr(body, name) != value:
        setattr(body, name, value)


def _names(body, prop):
    import json
    try:
        return list(json.loads(getattr(body, prop, "") or "[]"))
    except ValueError:
        return []


def _release_end(doc, body):
    """The objects hidden for End of Part shown again; the mark cleared."""
    for name in _names(body, END_HIDDEN):
        o = doc.getObject(name)
        if o is not None:
            _set_visible(o, True)
    for prop in (END_AFTER, END_ROLLED, END_HIDDEN):
        _end_prop(body, prop, "")


def _mark_end(doc, feature, after):
    """Remember End of Part's place when sketches, Texts or work features lie below it, and hide them there."""
    import json
    body = primary_body(doc)
    if body is None:
        return
    _release_end(doc, body)
    rolled = [o for o in after if not _is_solid_feature(o) and not getattr(o, "InCadHelperOf", None)]
    if not rolled:
        return
    hidden = [o.Name for o in rolled if o.Visibility]
    for name in hidden:
        _set_visible(doc.getObject(name), False)
    _end_prop(body, END_AFTER, feature.Name if feature is not None else _TOP)
    _end_prop(body, END_ROLLED, json.dumps([o.Name for o in rolled]))
    _end_prop(body, END_HIDDEN, json.dumps(hidden))


def end_marker(doc):
    """(the object End of Part follows, or None for before everything; names rolled back below it) while End of
    Part sits before sketches, Texts or work features; None when it is simply after the latest Tip, or when the
    part changed since (a new feature moved a Tip on: the mark no longer holds)."""
    body = primary_body(doc)
    name = getattr(body, END_AFTER, "") if body is not None else ""
    if not name:
        return None
    hist = history(doc)
    if name == _TOP:
        obj, pos = None, -1
    else:
        obj = doc.getObject(name)
        pos = next((i for i, (_, o) in enumerate(hist) if o is obj), None) if obj is not None else None
        if pos is None:
            return None
    for b in bodies(doc):
        last = None
        for bb, o in hist[:pos + 1]:
            if bb is b and _is_solid_feature(o):
                last = o
        if b.Tip is not last:
            return None
    return obj, _names(body, END_ROLLED)


def rolled_back_by_end(obj):
    """Is obj a sketch, Text or work feature End of Part was put above (end_marker)?"""
    try:
        body = primary_body(obj.Document)
    except Exception:
        return False
    if body is None or not getattr(body, END_AFTER, "") or obj.Name not in _names(body, END_ROLLED):
        return False
    return end_marker(obj.Document) is not None


def settle_end_marker(doc):
    """A mark the part no longer matches (a feature added moved the Tip on) is dropped, its hidden objects shown."""
    body = primary_body(doc)
    if body is None or not getattr(body, END_AFTER, "") or end_marker(doc) is not None:
        return False
    _release_end(doc, body)
    return True


def end_of_part(body):
    return body.Tip


def show_result(body):
    """Show only the feature at End of Part (the body Tip), hiding the other features and the sketches, so the 3D
    view follows the model and isn't cluttered by profile sketches. A broken tip (invalid or no shape) would leave
    the view blank, so the nearest earlier feature that still has a shape is shown instead."""
    feats = [o for o in body.Group if o.isDerivedFrom("PartDesign::Feature")]
    from incad.features import work
    for o in body.Group:  # work planes of older files muted too
        if o.TypeId == "PartDesign::Plane":
            work.quiet_look(o)
    for o in feats:  # an own Extrude / Revolve saved without a view provider draws nothing: give it one
        if o.TypeId.endswith("Python") and getattr(getattr(o, "ViewObject", None), "Proxy", 0) is None:
            show_like(o, getattr(o, "BaseFeature", None))
    tip = body.Tip
    shown = tip
    if tip is not None and _broken_shape(tip) and tip in feats:
        for o in reversed(feats[:feats.index(tip)]):
            if not _broken_shape(o):
                shown = o
                break
    chosen = bool(getattr(body, SHOWN_PROP, True))
    base = getattr(body, "BaseFeature", None)
    if base is not None and getattr(base, "InCadSplitPiece", False) and base.Visibility:
        _set_visible(base, False)  # a Split's piece is drawn by the Solid made on it, never on its own
    try:
        # a file saved while a sketch was being edited keeps the Body hidden (FreeCAD hides it in sketch edit mode):
        # shown features inside a hidden Body show nothing. A Solid hidden by Hide / Show stays hidden.
        _set_visible(body, chosen)
    except Exception:
        pass
    for o in feats:
        try:
            _set_visible(o, (o is shown) and chosen)  # a Split piece's base draws even inside a hidden Body
        except Exception:
            pass
    for o in body.Group:  # profile sketches shouldn't linger in the model view after a tip move; an unused
        if _follows_sketch_rule(o):  # a sketch or 3D Line follows the user's choice, else its shape
            try:
                _set_visible(o, sketch_shown(o))
            except Exception:
                pass
    return shown



def _set_visible(obj, visible):
    """Only a change is written: writing a Solid's Visibility, even unchanged, touched the Boolean that uses it as a
    tool, and the next recompute rebuilt the part from there (16 s after deleting an unused sketch)."""
    if bool(obj.Visibility) != bool(visible):
        obj.Visibility = visible

SHOWN_PROP = "InCadShown"  # a sketch the user hid or showed by hand (absent: the default of sketch_shown)
ORIGIN_TYPES = ("App::Plane", "App::Line", "App::Point")
DATUM_TYPES = ("PartDesign::Plane", "PartDesign::Line", "PartDesign::Point")  # work planes, axes, points


def is_line3d(o):
    from incad.features import line3d
    return getattr(o, "TypeId", "") == "Part::Part2DObjectPython" and line3d.claims(o)


def is_sketch_like(o):
    """A sketch, a sketch's Text or a 3D Line: hidden and shown as sketches are (the user: a 3D Line could not be
    hidden, it should be treated as a regular sketch)."""
    from incad.features import sketch_text
    return getattr(o, "TypeId", "") == "Sketcher::SketchObject" or sketch_text.is_text(o) or is_line3d(o)


def _follows_sketch_rule(o):
    """What the part view shows by the sketch rule (used: hidden; unused: an open path shown): sketches and 3D
    Lines (always an open path). A Text keeps its own rule; only a Hide / Show choice of the user's applies to it."""
    return getattr(o, "TypeId", "") == "Sketcher::SketchObject" or is_line3d(o)


def is_unused_sketch(sk):
    from incad.features import common
    return _follows_sketch_rule(sk) and not common.consumers(sk)


def sketch_shown(sk):
    """Whether the part view shows this sketch: the user's InCadShown choice when there is one; else hidden when
    a feature uses it; else an open contour (a path) is shown and a closed sketch hidden."""
    from incad.core import sketch as sketches
    if rolled_back_by_end(sk):
        return False  # below End of Part: rolled back
    if SHOWN_PROP in sk.PropertiesList:
        return bool(getattr(sk, SHOWN_PROP))
    if not is_unused_sketch(sk):
        return False
    return is_line3d(sk) or sketches.is_open_contour(sk)


def origin_items(body):
    return list(body.Origin.OriginFeatures)


def _consumer_label(sk):
    from incad.features import common
    users = common.consumers(sk)
    return users[0].Label if users else ""


def _origin_of(item):
    return next((o for o in item.InList if o.TypeId == "App::Origin"), None)


def is_shown(obj):
    """What the view shows: an origin item needs its Origin group visible too (a new part keeps the group
    hidden while every item says Visible)."""
    if obj.TypeId in ORIGIN_TYPES:
        origin = _origin_of(obj)
        return bool(obj.Visibility) and (origin is None or bool(origin.Visibility))
    return bool(obj.Visibility)


def _record_choice(o, flag):
    """The choice goes into InCadShown: FreeCAD records that in the undo step (a Visibility change alone leaves
    the step empty), and apply_visibility reads it back after undo/redo. An output: writing it touches nothing (the
    user: Show All Origin took 11 s — it touched the planes, every sketch on them and the part was rebuilt)."""
    if SHOWN_PROP not in o.PropertiesList:
        o.addProperty("App::PropertyBool", SHOWN_PROP, "InCad", "Shown in the part view by choice")
    if "Output" not in o.getPropertyStatus(SHOWN_PROP):  # also on files saved before
        o.setPropertyStatus(SHOWN_PROP, "Output")
    if bool(getattr(o, SHOWN_PROP)) != bool(flag):
        setattr(o, SHOWN_PROP, bool(flag))


def prepare_choice(objects):
    """Before the Hide / Show step: give every target its InCadShown holding the state it has now. Done outside
    the transaction, so the step changes a value FreeCAD can restore (a property added inside the step would be
    removed by undo, taking the previous state with it)."""
    for o in list(objects) + [s for s in (_solid_of(x) for x in objects) if s is not None]:
        if SHOWN_PROP not in o.PropertiesList and (o.TypeId in ORIGIN_TYPES + DATUM_TYPES or is_sketch_like(o)
                                                    or o.TypeId in ("PartDesign::Body", "App::Link")):
            _record_choice(o, is_shown(o))


def set_visibility(objects, flag, record=True):
    """Show or hide origin items, work features and sketches; every one remembers the choice in
    InCadShown (saved, undoable) unless `record` is False (a temporary show). Showing an origin item whose
    group is hidden turns the group on and hides its other items first, so only the chosen one appears."""
    for o in objects:
        if record:
            _record_choice(o, flag)
        if flag and o.TypeId in ORIGIN_TYPES:
            origin = _origin_of(o)
            if origin is not None and not origin.Visibility:
                for other in origin.OriginFeatures:
                    _set_visible(other, other in objects)
                _set_visible(origin, True)
        _set_visible(o, bool(flag))
        _tip_follows(o)
        linked = getattr(o, "LinkedObject", None) if o.TypeId == "App::Link" else None
        if flag and getattr(linked, "TypeId", "") == "PartDesign::Body" and not is_shown(linked):
            if SHOWN_PROP in linked.PropertiesList:  # its Solid hidden inside its part's file (an older InCad did
                _record_choice(linked, True)        # that): back too, and kept so after an undo or a reopen
            _set_visible(linked, True)
            _tip_follows(linked, True)


def apply_visibility(doc):
    """After undo/redo (which restore InCadShown but not Visibility) and once when a file opens: every origin
    item, work feature and sketch with a choice shows or hides as it says (a sketch: sketch_shown, so a consumed
    one stays hidden); a consumed sketch without a choice is hidden too (redo of its feature leaves it visible);
    anything else is left alone; an Origin group is on when any of its items is shown."""
    origins = set()
    for o in doc.Objects:
        chosen = SHOWN_PROP in o.PropertiesList
        if o.TypeId in ORIGIN_TYPES:
            origin = _origin_of(o)
            if origin is not None:
                origins.add(origin.Name)
            if not chosen:
                continue
            want = bool(getattr(o, SHOWN_PROP))
        elif o.TypeId in DATUM_TYPES:
            if not chosen:
                continue
            want = bool(getattr(o, SHOWN_PROP)) and not rolled_back_by_end(o)
        elif _follows_sketch_rule(o):
            if not chosen and is_unused_sketch(o):
                continue
            want = sketch_shown(o)
        elif is_sketch_like(o):  # a Text: only the user's choice
            if not chosen:
                continue
            want = bool(getattr(o, SHOWN_PROP)) and not rolled_back_by_end(o)
        elif o.TypeId in ("PartDesign::Body", "App::Link") and chosen:  # a Solid / component hidden by choice
            want = bool(getattr(o, SHOWN_PROP))
            _tip_follows(o, want)
        else:
            continue
        if bool(o.Visibility) != want:
            _set_visible(o, want)
    show_finished(doc)
    for name in origins:
        origin = doc.getObject(name)
        items = list(origin.OriginFeatures)
        chosen_shown = [i for i in items if SHOWN_PROP in i.PropertiesList and getattr(i, SHOWN_PROP)]
        if chosen_shown and not origin.Visibility:  # a chosen item wants to show: the group comes on, and the
            for i in items:                          # items nobody chose stay hidden (a new part keeps them
                if SHOWN_PROP not in i.PropertiesList:  # 'visible' inside a hidden group)
                    _set_visible(i, False)
            _set_visible(origin, True)
        elif not chosen_shown and origin.Visibility and not any(bool(i.Visibility) for i in items):
            _set_visible(origin, False)


def _tip_follows(o, flag=None):
    """A Solid hidden or shown: its Tip too (a Split piece's base keeps drawing inside a hidden Body)."""
    tip = getattr(o, "Tip", None) if o.TypeId == "PartDesign::Body" else None
    if tip is not None:
        try:
            _set_visible(tip, bool(o.Visibility if flag is None else flag))
        except Exception:
            pass


def _solid_of(o):
    """The Solid (or assembly component) a pick stands for: a Body itself, a feature or the stand-in base inside
    it, a Split's piece (outside the Solid made on it), a component link; None for anything else."""
    if o.TypeId in ("PartDesign::Body", "App::Link"):
        return o
    if getattr(o, "InCadSplitPiece", False):
        return next((b for b in o.InList if b.TypeId == "PartDesign::Body" and b.BaseFeature is o), None)
    parent = o.getParentGeoFeatureGroup() if hasattr(o, "getParentGeoFeatureGroup") else None
    if parent is not None and parent.TypeId == "PartDesign::Body" and o.isDerivedFrom("PartDesign::Feature"):
        return parent
    return None


def visibility_targets(objects):
    """What Hide / Show acts on for the picked objects: sketches, planes, axes and points as they are; anything of a
    Solid (a face, a feature) means that whole Solid; an assembly's component means the component."""
    targets = [o for o in objects if is_sketch_like(o) or o.TypeId in ORIGIN_TYPES
               or o.TypeId in DATUM_TYPES or hasattr(o, "InCadMeshReference")  # InnCad Plus: an STL Reference,
               or hasattr(o, "InCadImportedSurfaces")]  # an STL's surfaces
    targets += [s for s in dict.fromkeys(_solid_of(o) for o in objects if o not in targets) if s is not None]
    if not targets:
        seen = ", ".join(dict.fromkeys(o.Label for o in objects if hasattr(o, "Label")))
        raise ValueError("Nothing to hide or show in %s: select a solid, a plane, an axis, a point or a sketch"
                         % seen if seen else "Select a solid, a plane, an axis, a point or a sketch first")
    return targets


def toggle_visibility(objects):
    """Hide / Show: hides when any target is visible, else shows them all. Returns the new state."""
    targets = visibility_targets(objects)
    flag = not any(is_shown(o) for o in targets)
    set_visibility(targets, flag)
    return flag


def all_parts(doc):
    """What Show All brings back: every Solid of a part (not a Boolean's used-up tool), every component of an
    assembly (InCad's links, or the Bodies and shapes put in an assembly made elsewhere)."""
    from incad.core import assembly
    if assembly.is_assembly(doc):
        asm = assembly.assembly_of(doc)
        out = []

        def walk(objs):
            for o in objs:
                if o.TypeId in ("App::Part", "App::DocumentObjectGroup"):
                    walk(o.Group)
                elif o.TypeId == "App::Link" or o.TypeId == "PartDesign::Body" or (
                        o.TypeId.startswith("Part::") and hasattr(o, "Shape")):
                    out.append(o)
        walk(asm.Group if asm is not None else [])
        # a component's own Solid hidden inside its part's file (an older InCad hid it there from the assembly):
        # Show All brings that back too, so nobody has to go looking for it
        out += [l.LinkedObject for l in out if l.TypeId == "App::Link"
                and getattr(l.LinkedObject, "TypeId", "") == "PartDesign::Body"]
        return out
    from incad.features import combine
    used = combine.used_up_names(doc)
    return [b for b in bodies(doc) if b.Name not in used]


def adopt_foreign(doc, units="mm"):
    """A file made outside InCad (a script, FreeCAD, a STEP opened as it is) becomes an InCad part, so it has every
    tool an InCad part has (the user: parts not made in InCad must be edited the same way). With Bodies: the
    largest is the main Solid. Without: every shown solid shape becomes a Solid on it (its base, as Import makes
    one), placed where it is shown. Returns the main Solid, or None when there is nothing to make a part of."""
    from incad.core import assembly
    if is_incad_part(doc) or assembly.is_assembly(doc):
        return None
    found = bodies(doc)
    if not found:
        from incad.core import section
        shapes = [(o, sh) for o, sh, _ in section.shown_shapes(doc) if sh.Solids and o.TypeId != "App::Link"]
        if not shapes:
            return None
        for o, sh in shapes:
            base = doc.addObject("Part::Feature", "Imported")
            # in the file's frame, moved in its geometry: a Solid's base drops a shape's own placement (the block of a
            # STEP group moved 100 mm came back at the origin)
            where = o.getGlobalPlacement() if hasattr(o, "getGlobalPlacement") else o.Placement
            moved = o.Shape.copy()
            moved.Placement = App.Placement()
            base.Shape = moved.transformed(where.toMatrix(), True)
            base.Label = o.Label + " (imported)"
            body = doc.addObject("PartDesign::Body", "Body")
            body.Label = o.Label
            body.BaseFeature = base
            o.Visibility = False
            base.Visibility = False
        doc.recompute()
        found = bodies(doc)

    def volume(b):
        try:
            return b.Shape.Volume
        except Exception:
            return 0.0
    main = max(found, key=volume)
    _meta_set(doc, **{TYPE_KEY: "Part", PRIMARY_KEY: main.Name})
    set_units(doc, units)
    return main


def _broken_shape(obj):
    try:
        return "Invalid" in obj.State or obj.Shape.isNull()
    except Exception:
        return True


def helpers_of(body, feature):
    """Objects InCad made for `feature` (a thread's chamfer), in body order."""
    return [o for o in body.Group if getattr(o, "InCadHelperOf", None) == feature.Name]


def show_finished(doc, features=None):
    """A feature shown on screen whose shape a helper step right after it finishes (Hole ▸ Chamfer, a thread's
    chamfer) shows as that helper instead: both drawn at once put the unchamfered hole over the chamfer (the user's
    "it is not chamfer"; flickering stripes where the two walls overlap)."""
    for f in (features if features is not None else doc.Objects):
        try:
            if not f.TypeId.startswith("PartDesign::") or getattr(f, "InCadHelperOf", None) or not f.Visibility:
                continue
            body = f.getParentGeoFeatureGroup()
            if body is None or body.TypeId != "PartDesign::Body":
                continue
            group = list(body.Group)
            at = group.index(f)
            after = [h for h in helpers_of(body, f) if _is_solid_feature(h) and group.index(h) > at]
            if after:
                last = after[-1]
                _set_visible(f, False)
                for h in after:
                    _set_visible(h, h is last)
        except Exception:
            continue


def _is_solid_feature(obj):
    """Only a PartDesign solid feature can be the tip (work planes/axes and sketches are refused or empty)."""
    return obj.isDerivedFrom("PartDesign::Feature")


def set_end_of_part(body, feature):
    """Put End of Part right after `feature` — with the helpers InCad made for it, so "after Thread" includes its
    chamfer; None puts it before the first feature. The shown solid follows: only the tip feature is displayed,
    as FreeCAD's own Move Tip does (the 3D view otherwise keeps showing the last feature it showed, and moving
    End of Part seems to do nothing)."""
    if feature is not None and feature not in body.Group:
        raise ValueError("%s is not part of %s" % (feature.Label, body.Label))
    tip = feature
    if feature is not None:
        group = list(body.Group)
        here = group.index(feature)
        chain = [feature] + [h for h in helpers_of(body, feature) if group.index(h) > here]
        solids = [o for o in chain if _is_solid_feature(o)]
        if solids:
            tip = max(solids, key=group.index)
        else:  # a work plane/axis/sketch cannot be the tip: the last solid feature before it
            before = [o for o in group[:here] if _is_solid_feature(o)]
            tip = before[-1] if before else None
    body.Tip = tip
    body.Document.recompute()
    show_result(body)


_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_GROUP = "Parameters"


def parameters(doc, create=True):
    obj = doc.getObject(PARAMS_NAME)
    if obj is None and create:
        obj = doc.addObject("App::VarSet", PARAMS_NAME)
        obj.Label = "Parameters"
    return obj


def _is_param(obj, name):
    return name in obj.PropertiesList and obj.getGroupOfProperty(name) == _GROUP


def list_parameters(doc):
    """[(name, formula or '', value as text)] in the order FreeCAD lists the properties."""
    obj = parameters(doc, create=False) if doc is not None else None
    if obj is None:
        return []
    formulas = {path.lstrip("."): expr for path, expr in obj.ExpressionEngine}
    return [(name, "" if _is_literal(formulas.get(name, "")) else formulas[name], _format(getattr(obj, name)))
            for name in obj.PropertiesList if obj.getGroupOfProperty(name) == _GROUP]


def _is_literal(text):
    """True for a plain value like '80 mm' (stored as an expression so any unit is accepted)."""
    if not text:
        return True
    try:
        App.Units.Quantity(text)
        return True
    except Exception:
        return False


def _format(quantity):
    """'80.00 mm' -> '80 mm', '0.20 mm' -> '0.2 mm' (FreeCAD pads to the decimals setting)."""
    m = re.match(r"^(-?[\d.,]+)(.*)$", quantity.UserString)
    if not m:
        return quantity.UserString
    number, unit = m.groups()
    sep = "." if "." in number else "," if "," in number else ""
    if sep:
        number = number.rstrip("0").rstrip(sep)
    return number + unit


def set_parameter(doc, name, text):
    """Create or update a parameter: a quantity ('80 mm', '12') or a formula ('Width/8').
    Call inside safe.run_safely: a formula that fails to evaluate raises and must be rolled back."""
    if not _NAME_RE.match(name or ""):
        raise ValueError("A parameter name starts with a letter or _ and has only letters, digits and _ (got %r)" % name)
    text = (text or "").strip()
    if not text:
        raise ValueError("Parameter %s needs a value" % name)
    if _is_literal(name):  # 'mm', 'A', 'deg'… are read as units inside formulas
        raise ValueError("%r is a unit symbol; choose another parameter name" % name)
    obj = parameters(doc)
    if name in obj.PropertiesList and not _is_param(obj, name):
        raise ValueError("%r is a reserved name" % name)
    from incad.core import model as _model  # names are unique across user and model parameters
    if name not in obj.PropertiesList and _model.is_model_parameter(doc, name):
        raise ValueError("%r is already a model parameter (a dimension or a feature value)" % name)
    created = name not in obj.PropertiesList
    if created:
        obj.addProperty("App::PropertyQuantity", name, _GROUP)
    try:  # plain values go through the expression engine too: a unitless PropertyQuantity refuses '80 mm'
        obj.setExpression(name, text)
    except Exception as e:
        if created:
            obj.removeProperty(name)
        from incad.core import model as _m
        raise ValueError("%s: %s" % (name, _m.friendly(str(e))))
    doc.recompute()
    if "Invalid" in obj.State or "Error" in obj.State:
        from incad.core import model as _m
        raise ValueError("%s: %s" % (name, _m.friendly(obj.getStatusString())))


def remove_parameter(doc, name):
    obj = parameters(doc, create=False)
    if obj is None or not _is_param(obj, name):
        raise ValueError("No parameter named %r" % name)
    users = _users_of(doc, obj, name)
    if users:
        raise ValueError("%s is used by %s" % (name, ", ".join(users)))
    obj.setExpression(name, None)
    obj.removeProperty(name)
    doc.recompute()


def _users_of(doc, params_obj, name):
    """Labels of the other parameters and objects whose formulas use the user parameter `name`."""
    pattern = re.compile(r"(?:%s\.|(?<![\w.]))%s(?!\w)" % (re.escape(params_obj.Name), re.escape(name)))
    out = []
    for obj in doc.Objects:
        for path, expr in getattr(obj, "ExpressionEngine", []) or []:
            if obj is params_obj and path.lstrip(".") == name:
                continue
            if pattern.search(expr):
                out.append(path.lstrip(".") if obj is params_obj else obj.Label)
                break
    return out


def owning_feature(obj):
    """The feature that uses `obj` as its profile (a sketch's Extrude); obj itself otherwise."""
    for user in obj.InList:
        prof = getattr(user, "Profile", None)
        if isinstance(prof, (tuple, list)):
            prof = prof[0] if prof else None
        if prof is obj:
            return user
    return obj
