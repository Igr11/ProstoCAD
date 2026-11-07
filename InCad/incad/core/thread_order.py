"""A cut or join through a modeled (helical) thread breaks in FreeCAD's kernel: OpenCASCADE gives an invalid shape
for a cut, and a join keeps only the new piece (the part vanished: the user's slot revolved over a tapped Hole,
p2_test). The same boolean before the thread works. So such a feature goes into the tree right before the thread by
itself (the user's choice), its profile sketch and helpers with it, attached to the same face of the part as it is
there; the thread is made after it and cuts its result. A projected edge that only exists with the thread (the
hole's rim, a face of the thread's chamfer) is let go: FreeCAD can't point it elsewhere, and the sketch keeps its
lines where they are."""
from incad.core import suppress

KINDS = ("PartDesign::Pad", "PartDesign::Pocket", "PartDesign::Revolution", "PartDesign::Groove",
         "PartDesign::FeatureAdditivePython", "PartDesign::FeatureSubtractivePython")  # (the last two: ProstoCAD's own Extrude, Revolve)
JOINS = ("PartDesign::Pad", "PartDesign::Revolution", "PartDesign::FeatureAdditivePython")
MARGIN = 0.5  # mm around a thread's region: a feature that comes this close crosses it


def threaded(f):
    """A helix feature (InCad's Thread) or a Hole with a modeled thread."""
    return f.TypeId.endswith("Helix") or (f.TypeId == "PartDesign::Hole" and bool(getattr(f, "ModelThread", False)))


def _chain(f):
    """The solid features f is built on, nearest first."""
    out, b = [], getattr(f, "BaseFeature", None)
    while b is not None and b not in out:
        out.append(b)
        b = getattr(b, "BaseFeature", None)
    return out


def crossed_thread(feature):
    """The earliest thread before feature whose region the feature's own shape reaches, or None."""
    if feature.TypeId not in KINDS:
        return None
    try:
        box = feature.AddSubShape.BoundBox
    except Exception:
        return None
    if not box.isValid():
        return None
    hit = None
    for b in _chain(feature):
        if threaded(b):
            try:
                tb = b.AddSubShape.BoundBox
            except Exception:
                continue
            tb.enlarge(MARGIN)
            if tb.isValid() and tb.intersect(box):
                hit = b
    return hit


def broken(feature):
    """True if the feature's boolean failed: no solid, a join smaller than what it joins, a cut bigger than what it
    cuts, or an invalid shape (checked last: 0.6 s on a big threaded part)."""
    base = getattr(feature, "BaseFeature", None)
    try:
        if base is None or base.Shape.isNull():
            return False
        shape = feature.Shape
        if shape.isNull() or not shape.Solids:
            return True
        before, after = base.Shape.Volume, shape.Volume
        if feature.TypeId in JOINS and after < before - 1e-6:
            return True
        if feature.TypeId not in JOINS and after > before + 1e-6:
            return True
        return not shape.isValid()
    except Exception:
        return False


def _find(prior, ref, sub):
    """The element of prior's shape that is ref's sub (on the same surface / curve, the nearest), or None."""
    try:
        sig = suppress._signature(ref.Shape.getElement(sub.split(".")[-1].lstrip("?")))
    except Exception:
        return None
    items = {"Face": prior.Shape.Faces, "Edge": prior.Shape.Edges, "Vertex": prior.Shape.Vertexes}[sig[0]]
    on_it = [(n, (suppress._signature(el)[2] - sig[2]).Length) for n, el in enumerate(items)
             if suppress._same_place(suppress._signature(el), sig)]
    return "%s%d" % (sig[0], min(on_it, key=lambda t: t[1])[0] + 1) if on_it else None


def _flat(sketch):
    """A sketch's external geometry, one (object, element) per projected element, in its order."""
    return [(r, s) for r, subs in sketch.ExternalGeometry for s in (subs if isinstance(subs, (tuple, list)) else (subs,))]


def _link_plan(prior, late, link):
    """A (obj, [subs]) link with late features' elements put on prior's; None if one of them is not there."""
    obj, subs = link
    if obj not in late:
        return link
    if not obj.isDerivedFrom("PartDesign::Feature"):
        return None
    found = [_find(prior, obj, s) for s in subs]
    return None if None in found or not found else (prior, found)


def settle(feature):
    """Put a feature whose boolean the thread before it broke right before that thread. The thread's label if it
    moved, else None (nothing crossed, nothing broken, or what it is attached to exists only with the thread)."""
    if feature.TypeId not in KINDS:
        return None
    thread = crossed_thread(feature)
    if thread is None or not broken(feature):
        return None
    return thread.Label if place_before(feature, thread) else None


def place_before(feature, thread):
    body = feature.getParentGeoFeatureGroup()
    if body is None or thread not in body.Group:
        return False
    group = list(body.Group)
    i = group.index(thread)
    while i > 0 and getattr(group[i - 1], "InCadHelperOf", None) == thread.Name:  # its fill / bore stays with it
        i -= 1
    target = group[i]
    prior = getattr(target, "BaseFeature", None)
    if prior is None:
        return False
    after = group[i:]
    profile = getattr(feature, "Profile", None)
    profile = profile[0] if profile else None
    helpers = [o for o in group if getattr(o, "InCadHelperOf", None) == feature.Name]
    movers = ([profile] if profile is not None and profile in after else []) + [h for h in helpers if h in after]
    late = set(after) - set(movers) - {feature}  # what is made only with the thread

    plan = {}  # (object, property) -> new value
    drop = {}  # sketch -> its projected elements on what is made only with the thread
    for o in movers:
        support = list(getattr(o, "AttachmentSupport", None) or [])
        if any(r in late for r, _s in support):
            new = [_link_plan(prior, late, (r, list(s))) for r, s in support]
            if None in new:
                return False
            plan[(o, "AttachmentSupport")] = [(r, tuple(s)) for r, s in new]
        if o.TypeId == "Sketcher::SketchObject":
            gone = [n for n, (r, _s) in enumerate(_flat(o)) if r in late]
            if gone:
                drop[o] = gone
    for prop in ("ReferenceAxis", "UpToFace", "UpToFace2"):
        link = getattr(feature, prop, None) if prop in feature.PropertiesList else None
        if link and link[0] in late:
            new = _link_plan(prior, late, (link[0], list(link[1])))
            if new is None:
                return False
            plan[(feature, prop)] = (new[0], new[1])

    was_tip = body.Tip is feature
    last = None
    for o in group:  # the part's last solid step without this feature: End of Part goes back there
        if o is not feature and o not in movers and o.isDerivedFrom("PartDesign::Feature") and o not in helpers:
            last = o

    def change():
        for o in movers + [feature]:
            body.removeObject(o)
        for o in movers + [feature]:
            body.insertObject(o, target, False)
        for (o, prop), value in plan.items():
            setattr(o, prop, value)
        from incad.features import revolve
        for o, gone in drop.items():
            revolve.externals_going(o, gone)  # an axis on one of them stays where it is
            for n in sorted(gone, reverse=True):
                o.delExternal(n)  # its constraints go with it; the lines stay where they are
        if was_tip and last is not None:
            body.Tip = last
    # the features after it are renumbered: the thread's own sketches keep their edges (found by geometry)
    suppress._keeping_attachments(feature.Document, change)
    return True
