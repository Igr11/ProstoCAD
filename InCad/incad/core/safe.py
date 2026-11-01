"""Safe command guard. Every model change runs in one undo transaction; on an exception or an invalid result
the transaction is aborted, the model recomputed back to its previous state, and the user told why."""
import traceback
from dataclasses import dataclass, field

from incad.core import faceted, log

_depth = 0
_busy = None


def curve_type(edge):
    """An edge's curve TypeId, or "" when FreeCAD has no Python type for it ("undefined curve type": a modelled
    thread's helical edges). Reading .Curve on those raised in every tool that looked at all of a part's edges."""
    try:
        return edge.Curve.TypeId
    except Exception:
        return ""


@dataclass
class SafeResult:
    ok: bool
    value: object = None
    error: str = ""
    broken: list = field(default_factory=list)  # the objects the change broke (names), when that undid it


class _InvalidResult(Exception):
    def __init__(self, text, names=()):
        super().__init__(text)
        self.names = list(names)


def _status(obj):
    try:
        return obj.getStatusString()
    except Exception:
        return ""


def loose_piece(feature):
    """Where a feature's profile sketch has a piece that is no part of a closed outline (the user: Revolve failed
    with "Wire is not closed" — one 0.2 mm line of a half-section left as normal geometry), said in the sketch's
    own coordinates; '' when there is none."""
    prof = getattr(feature, "Profile", None)
    sk = prof[0] if isinstance(prof, (tuple, list)) and prof else prof
    if sk is None or getattr(sk, "TypeId", "") != "Sketcher::SketchObject":
        return ""
    try:
        wires = [w for w in sk.Shape.Wires if not w.isClosed()]
        if not wires:
            return ""
        inv = sk.getGlobalPlacement().inverse()
        w = min(wires, key=lambda x: x.Length)
        c = inv.multVec(w.BoundBox.Center)
        more = " (and %d more)" % (len(wires) - 1) if len(wires) > 1 else ""
        return ("%s: the sketch %s has a loose piece, %.2f mm long, near x %.2f, y %.2f%s — it is no part of a closed "
                "outline. Delete it or make it Construction" % (feature.Label, sk.Label, w.Length, c.x, c.y, more))
    except Exception:
        return ""


def _valid(obj, shape, checked=None):
    """shape.isValid(), once per check (`checked`: a set of names found valid): a Solid's shape is a copy of its
    tip's, so the two count as one (the user's STL hook: 4 s an isValid, twice for every Extrude)."""
    tip = getattr(obj, "Tip", None) if obj.TypeId == "PartDesign::Body" else None
    key = getattr(tip if tip is not None else obj, "Name", None)
    if checked is not None and key is not None and key in checked:
        return True
    ok = shape.isValid()
    if ok and checked is not None and key is not None:
        checked.add(key)
    return ok


def _broken(obj, check_shape, deep=True, checked=None):
    """Reason text if obj failed to compute or (check_shape) has invalid PartDesign geometry, else ''. deep=False
    leaves out the full geometry check (isValid: 0.6 s on a big threaded part, 2 s on a slow PC, every preview);
    checked: shapes already found valid in this check."""
    state = getattr(obj, "State", [])
    if "Invalid" in state or "Error" in state:
        status = _status(obj)
        if obj.TypeId == "PartDesign::Body" and status == "Tip shape is empty":
            return ""  # an empty part (everything suppressed / rolled back) is a normal state
        if obj.TypeId == "PartDesign::Thickness" and "Null input shape" in status:
            base = getattr(obj, "BaseFeature", None)
            shape = getattr(base, "Shape", None) if base is not None else None
            n = len(shape.Solids) if shape is not None and not shape.isNull() else 0
            if n > 1:
                return ("%s: the part's body holds %d separate solids (a box drawn away from the part stays its own "
                        "piece); Shell works on one connected solid. Join the piece to the part, or shell it in a "
                        "part file of its own" % (obj.Label, n))
            return "%s: the kernel got no solid to hollow (is the face on the part's body?)" % obj.Label
        if obj.TypeId == "PartDesign::Thickness" and "thick solid" in status:
            if _has_modeled_thread(getattr(obj, "BaseFeature", None)):
                return ("%s: FreeCAD can't hollow a part with a modeled thread in it: make the Shell before the "
                        "thread (drag End of Part above the thread, make the Shell there, then move End of Part "
                        "back)" % obj.Label)
            return ("%s: FreeCAD couldn't hollow this shape. Try a smaller thickness, or make the Shell earlier "
                    "in the tree, before fillets and chamfers" % obj.Label)
        if "not closed" in status.lower() or "open wire" in status.lower():
            loose = loose_piece(obj)
            if loose:
                return loose
        if status and status not in ("Valid", "Touched"):
            from incad.core import model
            return "%s: %s" % (obj.Label, model.friendly(status, obj.TypeId))
        return "%s failed to compute" % obj.Label
    if check_shape and obj.TypeId.startswith("PartDesign::"):
        shape = getattr(obj, "Shape", None)
        if deep and shape is not None and not shape.isNull() and not _valid(obj, shape, checked):
            from incad.core import thread_order
            if obj.TypeId in thread_order.KINDS and _has_modeled_thread(getattr(obj, "BaseFeature", None)):
                return ("%s crosses a modeled (helical) thread, which FreeCAD's kernel can't cut or join through: "
                        "put it before the thread in the tree" % obj.Label)
            if obj.TypeId in ("PartDesign::Groove", "PartDesign::Revolution"):
                return ("%s: the revolve couldn't form a valid shape — the axis must lie IN the profile's plane "
                        "and not cross or touch the profile (draw the profile on a plane through the axis)" % obj.Label)
            return "%s produced invalid geometry" % obj.Label
        if obj.TypeId in _TRANSFORMED and (shape is None or shape.isNull() or not shape.Solids):
            return ("%s: the pattern produced nothing — the picked feature can't be repeated here (a feature that "
                    "changes nothing, or a cut that can't be moved). Pick another feature, or put the pattern "
                    "right after it" % obj.Label)
        if obj.TypeId in _TRANSFORMED and _changes_nothing(obj, shape):
            return ("%s: the pattern changes nothing — its copies fall where the part has no material, or the "
                    "picked feature (a thread, for example) can't be repeated. Pick another feature" % obj.Label)
        # a volume only where there is next to nothing: the volume of a part with a modeled thread took 0.2 s,
        # twice at every preview (the user: "Extrude Cut lags")
        if obj.TypeId in _SUBTRACTIVE and shape is not None and (shape.isNull() or not shape.Solids
                                                                   or (len(shape.Faces) < 4 and shape.Volume < 1e-9)):
            base = getattr(obj, "BaseFeature", None)
            if base is not None and not base.Shape.isNull() and bool(base.Shape.Solids):
                if _has_modeled_thread(base):
                    return ("%s: the cut failed in the kernel and left nothing — a modeled (helical) thread earlier "
                            "in the part is the usual cause. Put this cut BEFORE the thread, or make the thread "
                            "cosmetic" % obj.Label)
                return "%s cuts away the whole part: choose another axis, extent or profile" % obj.Label
    return ""


def _has_modeled_thread(feature):
    """True if feature, or a feature it is built on, carries a modeled helix (helix feature or Hole with ModelThread)."""
    seen = set()
    while feature is not None and feature.Name not in seen:
        seen.add(feature.Name)
        if feature.TypeId.endswith("Helix") or (feature.TypeId == "PartDesign::Hole"
                                                and getattr(feature, "ModelThread", False)):
            return True
        try:  # InCad's own threads: cut in, or grown on (the user's jar: Shell after the thread failed, unexplained)
            from incad.features import thread
            if thread.claims(feature):
                return True
        except Exception:
            pass
        feature = getattr(feature, "BaseFeature", None)
    return False


def _changes_nothing(pattern, shape):
    """True if the pattern's result has the same volume as what it was built on (it added or removed nothing)."""
    try:
        base = getattr(pattern, "BaseFeature", None)
        if base is None:
            group = pattern.getParentGeoFeatureGroup()
            earlier = [o for o in (group.Group if group is not None else [])
                       if o.isDerivedFrom("PartDesign::Feature")]
            earlier = earlier[:earlier.index(pattern)] if pattern in earlier else []
            base = earlier[-1] if earlier else None
        if base is None or base.Shape.isNull() or shape.isNull():
            return False
        return abs(shape.Volume - base.Shape.Volume) < 0.01  # mm3: nothing a pattern could be meant to make
    except Exception:
        return False


_TRANSFORMED = ("PartDesign::LinearPattern", "PartDesign::PolarPattern", "PartDesign::Mirrored",
                "PartDesign::MultiTransform")
_SUBTRACTIVE = ("PartDesign::Groove", "PartDesign::Pocket", "PartDesign::SubtractiveHelix",
                "PartDesign::SubtractivePipe", "PartDesign::SubtractiveLoft", "PartDesign::Hole")


_drag_edit = False


def set_drag_edit(on):
    """An assembly is in FreeCAD's edit only for a drag of one of its parts (incad.ui.asm_drag): that is no FreeCAD
    task, InCad's commands and refreshes go on."""
    global _drag_edit
    _drag_edit = bool(on)


def drag_edit():
    return _drag_edit


def edit_open():
    """True while FreeCAD has a sketch or feature task dialog open (GUI only)."""
    if _drag_edit:
        return False
    try:
        import FreeCAD as App
        if not App.GuiUp:
            return False
        import FreeCADGui as Gui
        gdoc = Gui.ActiveDocument
        vp = gdoc.getInEdit() if gdoc is not None else None
        if vp is not None and getattr(getattr(vp, "Object", None), "TypeId", "") == "Sketcher::SketchObject":
            return False  # InCad's own sketch editing: Sketcher commits each tool step itself
        return bool(Gui.Control.activeDialog()) or vp is not None
    except Exception:
        return False


class Stopped(Exception):
    """The user pressed Esc during a long computation (the user: Escape must cancel any operation)."""


_esc = {"seen": 0.0, "since": 0.0, "watching": False}


def _watch_esc():
    """A thread that notes when Esc was last down (checked every 30 ms): a short press during one long step of a loop
    is still seen afterwards."""
    import time
    from incad import host
    while True:
        if host.esc_down():
            _esc["seen"] = time.monotonic()
        time.sleep(0.03)


def watch_esc():
    """A long computation starts: an Esc pressed from now on stops it (stop_if_esc)."""
    import time
    _esc["since"] = time.monotonic()
    if not _esc["watching"]:
        _esc["watching"] = True
        import threading
        threading.Thread(target=_watch_esc, name="InCad Esc", daemon=True).start()


def stop_if_esc(what="the computation"):
    """Raises Stopped when Esc was pressed since watch_esc(): called inside InCad's long loops, which run with the
    event loop stopped (a key press is no event then). The step it is part of fails and is rolled back."""
    from incad import host
    import time
    if _esc["watching"] and _esc["seen"] > _esc["since"] or host.esc_down():
        _esc["since"] = time.monotonic()  # one press stops one computation
        raise Stopped("Stopped %s (Esc)" % what)


def open_sketch(doc):
    """The sketch open in InCad's sketch editing in `doc` (GUI only), else None."""
    try:
        import FreeCAD as App
        if doc is None or not App.GuiUp:
            return None
        import FreeCADGui as Gui
        gdoc = Gui.getDocument(doc.Name)
        obj = getattr(gdoc.getInEdit() if gdoc is not None else None, "Object", None)
        return obj if getattr(obj, "TypeId", "") == "Sketcher::SketchObject" else None
    except Exception:
        return None


def recompute(doc):
    """doc.recompute(), but while a sketch is open in `doc` that sketch only: the rest of the part follows on Finish
    Sketch (the user's jar, a thread and letters wrapped on a curved face after the sketch: 45 s on every click)."""
    sk = open_sketch(doc)
    return doc.recompute([sk]) if sk is not None else doc.recompute()


def set_busy(label):
    """A feature dialog is open (label) or closed (None): no other model change may start meanwhile."""
    global _busy
    _busy = label


def busy():
    return _busy


_on_refused = None  # shows the open dialog when a command is refused because of it (set by the shell)


def set_refused_hook(fn):
    global _on_refused
    _on_refused = fn


def refused():
    """A command was refused because a dialog is open: make sure the user sees that dialog."""
    if _on_refused is not None:
        try:
            _on_refused()
        except Exception:
            pass


def snapshot_broken(doc):
    return {o.Name for o in doc.Objects if _broken(o, False)}


def problems(doc, broken_before, touched, deep=True):
    """Reasons why objects that were fine (not in broken_before) are now broken; shapes checked for `touched`."""
    return [why for _name, why in broken_now(doc, broken_before, touched, deep)]


def beyond_end(obj):
    """Is obj a Solid's step below its End of Part (rolled back: not part of the result, its failure no failure)?"""
    try:
        if not obj.isDerivedFrom("PartDesign::Feature") or obj.TypeId == "PartDesign::Body":
            return False
        body = obj.getParentGeoFeatureGroup()
        if body is None or body.TypeId != "PartDesign::Body":
            return False
        group = list(body.Group)
        if obj not in group:
            return False
        tip = body.Tip
        return tip is None or (tip in group and group.index(obj) > group.index(tip))
    except Exception:
        return False


def rolled_back(obj, _seen=None):
    """beyond_end, or a helper used only by steps beyond it (a Boolean's grown tool below End of Part)."""
    if beyond_end(obj):
        return True
    seen = _seen if _seen is not None else set()
    if obj.Name in seen:
        return False
    seen.add(obj.Name)
    users = [u for u in getattr(obj, "InList", []) if u.TypeId not in ("PartDesign::Body", "App::Part")]
    return bool(users) and all(rolled_back(u, seen) for u in users)


def broken_now(doc, broken_before, touched, deep=True):
    """[(name, reason)] of the objects that were fine (not in broken_before) and are broken now — a step below End
    of Part left out (the user: moving End of Part above the lid's grip ribs was undone, the lid's Boolean below it
    having lost its tool)."""
    checked = set()
    found = [(o.Name, _broken(o, o.Name in touched, deep, checked)) for o in doc.Objects
             if o.Name not in broken_before and not beyond_end(o)]
    return [(n, why) for n, why in found if why]


def _relink(doc):
    try:
        from incad.core import model
        model.relink(doc)  # objects restored by the abort lose their formula links (FreeCAD quirk)
    except Exception as e:
        log.report("warning", "Could not repair formula links: %s" % e, traceback.format_exc())


def run_safely(label, fn, doc=None, only=None, quiet_broken=False):
    """Run fn() as one undo step named `label`. Never raises at the outermost level. only: the objects to recompute
    (an open sketch: the part follows on Finish Sketch; or a function of fn's result giving them), else the whole
    document. quiet_broken: when the change broke
    other objects it is undone without a message, their names in the result's `broken` (the caller asks)."""
    global _depth
    if _depth > 0:
        return SafeResult(True, fn())  # nested: the outer guard owns the transaction and catches errors
    if _busy:
        msg = "Finish the %s dialog first" % _busy
        refused()
        log.report("warning", msg)
        return SafeResult(False, error=msg)
    import FreeCAD as App
    doc = doc or App.ActiveDocument
    if doc is None:
        try:
            return SafeResult(True, fn())
        except Exception as e:
            msg = "%s failed: %s" % (label, e)
            log.report("error", msg, traceback.format_exc())
            return SafeResult(False, error=msg)
    if edit_open():  # the open edit owns the pending transaction and its OK/Cancel
        refused()
        msg = "Finish or cancel the current edit before %s" % label
        log.report("warning", msg)
        return SafeResult(False, error=msg)
    if doc.HasPendingTransaction:  # a stray (implicit) change: keep it as its own undo step, apart from ours
        doc.commitTransaction()
    if not doc.UndoMode:
        doc.UndoMode = 1
    broken_before = snapshot_broken(doc)
    names_before = {o.Name for o in doc.Objects}
    watch_esc()
    _depth += 1
    doc.openTransaction(label)
    try:
        value = fn()
        if callable(only):  # the objects to recompute known only from what fn made (a new sketch)
            only = only(value)
        if only is None and open_sketch(doc) is not None:  # a sketch tool: the open sketch and what it made only
            only = [open_sketch(doc)] + [o for o in doc.Objects if o.Name not in names_before]
        touched = {o.Name for o in doc.Objects if "Touched" in o.State or o.Name not in names_before}
        faceted.skip_refine([o for o in doc.Objects if o.Name in touched])
        doc.recompute(only) if only else doc.recompute()
        found = broken_now(doc, broken_before, {o.Name for o in only} if only else touched)
        if found:
            raise _InvalidResult("; ".join(why for _n, why in found), [n for n, _why in found])
    except Exception as e:
        doc.abortTransaction()
        doc.recompute()
        _relink(doc)
        invalid = isinstance(e, _InvalidResult)
        msg = str(e) if invalid else "%s failed: %s" % (label, e)
        if not (invalid and quiet_broken):
            log.report("error", msg, "" if invalid else traceback.format_exc())
        return SafeResult(False, error=msg, broken=e.names if invalid else [])
    finally:
        _depth -= 1
    doc.commitTransaction()
    return SafeResult(True, value)
