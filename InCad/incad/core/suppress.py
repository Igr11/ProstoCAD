"""Suppress / Unsuppress. A feature goes off together with the pieces InCad made for it (a thread's fill, bore and
chamfer, a sweep's wall), so the part looks as it did before the feature; its profile sketch or one of those pieces
means the feature. Threads can also go off all at once (faster redraws while designing) and come back on at the end;
a thread that was off before stays off."""
import FreeCAD as App

from incad.core import document

OFF_MARK = "InCadThreadsOff"  # on a thread the 'all threads off' switch turned off
QUIET_MARK = "InCadSuppressedThread"  # on a suppressed tapped Hole: its modeled thread is off until it is back on


def _signature(el):
    """What an element is, independent of its number: kind, type, the surface / curve it lies on, and its centre.
    Threads change a face's outline (its area, a little its centre), never the plane or cylinder it lies on."""
    if el.ShapeType == "Vertex":
        return ("Vertex", "", el.Point, None)
    if el.ShapeType == "Edge":
        try:
            c = el.Curve
        except Exception:  # "undefined curve type" (a thread's helical edges): found again by its centre only
            return ("Edge", "", el.CenterOfMass, None)
        geom = (App.Vector(c.Center), App.Vector(c.Axis), c.Radius) if hasattr(c, "Radius") and hasattr(c, "Axis") \
            else (App.Vector(el.Vertexes[0].Point), (el.Vertexes[-1].Point - el.Vertexes[0].Point).normalize()) \
            if c.TypeId in ("Part::GeomLine", "Part::GeomLineSegment") else None
        return ("Edge", c.TypeId, el.CenterOfMass, geom)
    srf = el.Surface
    if srf.TypeId == "Part::GeomPlane":
        n = App.Vector(srf.Axis).normalize()
        geom = (n, n.dot(srf.Position))
    elif hasattr(srf, "Radius") and hasattr(srf, "Axis"):
        geom = (App.Vector(srf.Center), App.Vector(srf.Axis).normalize(), srf.Radius)
    else:
        geom = None
    return ("Face", srf.TypeId, el.CenterOfMass, geom)


def _same_place(a, b):
    """The same surface / curve (or the same point): the element can be found again on it."""
    if a[0] != b[0] or a[1] != b[1]:
        return False
    if a[0] == "Vertex":
        return (a[2] - b[2]).Length < 1e-6
    ga, gb = a[3], b[3]
    if ga is None or gb is None:
        return (a[2] - b[2]).Length < 1e-6
    if len(ga) == 2 and a[0] == "Face":  # a plane: its normal and offset
        return abs(abs(ga[0].dot(gb[0])) - 1) < 1e-7 and abs(ga[1] * ga[0].dot(gb[0]) - gb[1]) < 1e-6
    if len(ga) == 2:  # a line: parallel and on it
        d = gb[0] - ga[0]
        return abs(abs(ga[1].dot(gb[1])) - 1) < 1e-7 and (d - ga[1] * d.dot(ga[1])).Length < 1e-6
    axis_ok = abs(abs(ga[1].dot(gb[1])) - 1) < 1e-7
    d = gb[0] - ga[0]
    return axis_ok and abs(ga[2] - gb[2]) < 1e-6 and (d - ga[1] * d.dot(ga[1])).Length < 1e-6


def _same(a, b):
    return _same_place(a, b) and (a[2] - b[2]).Length < 1e-6


def _snapshot(doc):
    """Every attachment to a feature's face, edge or vertex, with what that element is now. Threads going on or off
    renumber the elements of the features after them; FreeCAD does not always find them again ('subshape not
    found Extrude001.?Face10')."""
    out = []
    for o in doc.Objects:
        for i, (ref, subs) in enumerate(getattr(o, "AttachmentSupport", None) or []):
            if ref is None or not ref.isDerivedFrom("PartDesign::Feature"):
                continue
            for sub in subs:
                if sub.startswith(("Face", "Edge", "Vertex")):
                    try:
                        el = ref.Shape.getElement(sub)
                    except Exception:  # a name FreeCAD already lost: nothing to keep
                        continue
                    out.append((o, i, ref, sub, _signature(el)))
    return out


def _reattach(doc, snap):
    """Point each attachment at the element that is still the same one (found by its geometry)."""
    changed = False
    for o, i, ref, sub, sig in snap:
        try:
            el = ref.Shape.getElement(sub)
        except Exception:  # gone after the change
            el = None
        if el is not None and _same(_signature(el), sig):
            continue
        kind = sig[0]
        items = {"Face": ref.Shape.Faces, "Edge": ref.Shape.Edges, "Vertex": ref.Shape.Vertexes}[kind]
        on_it = [(n, (_signature(el)[2] - sig[2]).Length) for n, el in enumerate(items)
                 if _same_place(_signature(el), sig)]
        found = "%s%d" % (kind, min(on_it, key=lambda t: t[1])[0] + 1) if on_it else None  # the nearest on it
        if found is None:
            continue
        support = [(r, list(ss)) for r, ss in o.AttachmentSupport]
        # FreeCAD marks a name it lost with '?' ('?Face10') once a recompute failed to find it
        support[i] = (support[i][0], [found if x.split(".")[-1].lstrip("?") == sub else x for x in support[i][1]])
        o.AttachmentSupport = [(r, tuple(ss)) for r, ss in support]
        changed = True
    if changed:
        doc.recompute()


def _keeping_attachments(doc, change):
    """Run change() (threads on or off) and keep what is attached to the part's faces on the same faces."""
    snap = _snapshot(doc)
    result = change()
    doc.recompute()
    _reattach(doc, snap)
    return result


def _profile(feature):
    from incad.features.common import profile_of
    return profile_of(feature)  # (through an Extrude's grown profile: the user's sketch)


def owner_of(obj):
    """The feature that Suppress acts on for this object (a helper -> its owner, a profile sketch -> its feature)."""
    name = getattr(obj, "InCadHelperOf", None)
    owner = obj.Document.getObject(name) if name else None
    if owner is not None:
        return owner
    if "Suppressed" not in obj.PropertiesList:
        users = {o.Name: o for o in obj.InList if "Suppressed" in o.PropertiesList and _profile(o) is obj}
        if len(users) == 1:  # (InList names a feature once per link: Profile and ReferenceAxis)
            return owner_of(next(iter(users.values())))
    return obj


def set_suppressed(feature, on):
    body = feature.getParentGeoFeatureGroup()
    pieces = document.helpers_of(body, feature) if body is not None else []
    for o in [feature] + pieces:
        if "Suppressed" in o.PropertiesList and o.Suppressed != on:
            if on:
                _quiet_thread(o, True)
            o.Suppressed = on
            if not on:
                _quiet_thread(o, False)


def _quiet_thread(hole, on):
    """FreeCAD still computes a suppressed feature in full (only to throw it away): a suppressed Hole with a modeled
    thread took 3.6 s on every recompute, 0.06 s without the thread. So its thread is off while it is suppressed,
    and back with it (the hole keeps Tapped: its dialog reads Threaded)."""
    if hole.TypeId != "PartDesign::Hole":
        return
    if on and hole.ModelThread:
        hole.ModelThread = False
        _set_mark(hole, QUIET_MARK, True)
    elif not on and getattr(hole, QUIET_MARK, False):
        hole.ModelThread = True
        _set_mark(hole, QUIET_MARK, False)


def quiet_suppressed_holes(doc):
    """On open: tapped Holes suppressed before InCad did this (see _quiet_thread) get their thread off too. Their
    shape (the part before them) does not change, so nothing needs a recompute for it."""
    done = []
    for h in doc.Objects:
        if h.TypeId == "PartDesign::Hole" and getattr(h, "Suppressed", False) and h.ModelThread:
            was_touched = "Touched" in h.State
            _quiet_thread(h, True)
            if not was_touched:
                h.purgeTouched()
            done.append(h)
    return done


def _repair(doc):
    from incad.features import thread
    thread.repair(doc)  # a thread hung on another one would break when that one goes off


def toggle(obj):
    _repair(obj.Document)
    feature = owner_of(obj)
    if "Suppressed" not in feature.PropertiesList:
        raise ValueError("%s cannot be suppressed" % feature.Label)
    _keeping_attachments(obj.Document, lambda: set_suppressed(feature, not feature.Suppressed))
    return feature


def threads(doc):
    from incad.features import thread
    return [o for o in doc.Objects if thread.claims(o) and not getattr(o, "InCadHelperOf", None)]


def tapped_holes(doc):
    """Holes with a modeled thread (Hole > Tapped): Threads Off turns the thread off, never the hole."""
    return [o for o in doc.Objects if o.TypeId == "PartDesign::Hole" and getattr(o, "Threaded", False)]


def _thread_on(t, holes):
    return (t.ModelThread and not getattr(t, "Suppressed", False)) if t in holes else not t.Suppressed


def threads_off(doc):
    """True while the 'all threads off' switch holds threads off and none is on (a thread made or turned on since
    makes it a mixed state: the next click turns everything off)."""
    ts, holes = threads(doc), tapped_holes(doc)
    if any(_thread_on(t, holes) for t in ts + holes):
        return False
    return any(getattr(t, OFF_MARK, False) for t in ts + holes)


def _mark(t, on):
    _set_mark(t, OFF_MARK, on)


def _set_mark(t, name, on):
    if name not in t.PropertiesList:
        if not on:
            return
        t.addProperty("App::PropertyBool", name, "InCad",
                      "Turned off by Threads Off" if name == OFF_MARK else "Modeled thread off while suppressed")
        t.setPropertyStatus(name, "Hidden")
    setattr(t, name, on)


def toggle_threads(doc):
    """Every thread off — Thread features suppressed, tapped Holes without their modeled thread (the holes stay);
    those already off are left alone — or back on: 'off' or 'on'."""
    ts, holes = threads(doc), tapped_holes(doc)
    if not ts and not holes:
        raise ValueError("This part has no threads")
    _repair(doc)
    for t in ts + holes:  # turned back on by hand meanwhile (an edited hole, an unsuppressed thread): not ours
        if getattr(t, OFF_MARK, False) and _thread_on(t, holes):
            _mark(t, False)
    return _keeping_attachments(doc, lambda: _switch_threads(ts, holes))


def _switch_threads(ts, holes):
    """Any thread on (some on and some off is mixed: a new thread made while the others were off) -> all off;
    all off -> the ones the switch turned off come back."""
    marked = [t for t in ts + holes if getattr(t, OFF_MARK, False)]
    if marked and not any(_thread_on(t, holes) for t in ts + holes):
        for t in marked:
            if t in holes and getattr(t, "Suppressed", False):
                _set_mark(t, QUIET_MARK, True)  # suppressed meanwhile: its thread comes back with it
            elif t in holes:
                t.ModelThread = True
            elif t.Suppressed:
                set_suppressed(t, False)
            _mark(t, False)
        return "on"
    for t in ts:
        if not t.Suppressed:
            set_suppressed(t, True)
            _mark(t, True)
    for h in holes:  # the hole stays (its tap drill size); only its modeled thread goes
        if h.ModelThread and not getattr(h, "Suppressed", False):
            h.ModelThread = False
            _mark(h, True)
    return "off"
