"""After an undo, a redo or a rolled-back step FreeCAD has put every object's values AND its computed shape back, but
marks all of them to be computed again: the whole part was rebuilt into the very shapes it already had (49 s on a part
with many letters). Here an object whose shape came back together with what it is built from is left as it is; only
what is not proven so is computed."""
import re

import FreeCAD as App

from incad.core import log

_OUTPUTS = {"Shape", "AddSubShape", "SuppressedShape", "PreviewShape", "_Body", "Visibility", "Label", "ShapeMaterial",
            "_GroupTouched", "Support", "AttacherEngine", "FullyConstrained", "ExternalTypes", "Geometry2d"}
_DONE = {"Shape", "SuppressedShape"}  # set by every computing (a Boolean without tools leaves its shape, sets the second)
_PLAIN = ("App::VarSet", "App::Part", "App::DocumentObjectGroup", "App::Origin", "App::Plane", "App::Line", "App::Point",
          "App::OriginFeature")
_WHOLE = ("Assembly::", "Spreadsheet::")  # a document holding one is computed as FreeCAD would
_failed = {}  # document name -> names of the objects seen failing: a step back may bring the failure back unmarked


class _Watch:
    def __init__(self):
        self.changed = {}
        self.made = set()

    def slotChangedObject(self, obj, prop):
        try:
            self.changed.setdefault((obj.Document.Name, obj.Name), set()).add(prop)
        except Exception:
            pass

    def slotCreatedObject(self, obj):
        try:
            self.made.add((obj.Document.Name, obj.Name))
        except Exception:
            pass


_back = 0


def taking_back():
    """Is a step being taken back right now (what follows an object's change has come back too)?"""
    return _back > 0


def _watched(step):
    global _back
    watch = _Watch()
    App.addDocumentObserver(watch)
    _back += 1
    try:
        step()
    finally:
        _back -= 1
        App.removeDocumentObserver(watch)
    return watch


def note_failed(doc):
    """After a recompute or at the end of a step: the objects that failed."""
    try:
        bad = {o.Name for o in doc.Objects if not o.isValid()}
        if bad:
            _failed.setdefault(doc.Name, set()).update(bad)
    except Exception:
        pass


def forget(name):
    _failed.pop(name, None)
    _loose.pop(name, None)


_loose = {}  # document name -> names of the objects changed outside any undo step (a script): no undo brings that back,
# so what is built on them is computed as FreeCAD would
_LOOK = _OUTPUTS - {"Shape"} | {"Placement", "ExpressionEngine"}


def note_change(obj, prop):
    """Every change of an object (the shell's observer)."""
    if prop in _LOOK or _back or obj.TypeId in _PLAIN or obj.TypeId.startswith(("TechDraw::", "App::")):
        return
    doc = obj.Document
    if doc.HasPendingTransaction or not doc.UndoMode or getattr(doc, "Restoring", False):
        return
    names = _loose.setdefault(doc.Name, set())
    if obj.Name not in names:
        names.add(obj.Name)
        log.logger().info("changed outside an undo step: %s.%s", obj.Name, prop)


def _shape(o):
    s = getattr(o, "Shape", None)
    return s if s is not None and hasattr(s, "isNull") and not s.isNull() else None


def _links(doc):
    return {o.Name: {(x.Document.Name, x.Name) for x in o.OutList} for o in doc.Objects}


def _values(key, watch):
    """What of an object changed that others are computed from: its shape too, not its name or Show / Hide."""
    got = watch.changed.get(key, set()) - (_OUTPUTS - {"Shape"})
    if got & {"Group", "Tip"}:
        o = App.getDocument(key[0]).getObject(key[1])
        if o is not None and o.TypeId == "PartDesign::Body":
            got = got - {"Group", "Tip"}  # (what others take of a Solid is its shape)
    return got


def _sources(o, deps):
    """What its shape comes from: a Solid's is its last step's, the others in it are only kept in it."""
    if o.TypeId != "PartDesign::Body":
        return deps
    return {(o.Document.Name, o.Tip.Name)} if o.Tip is not None else set()


def _moved(o, deps, watch):
    """Did anything `o` is built from change? A holder of values (parameters) only by the values o's formulas use."""
    text = None
    for d in _sources(o, deps):
        if d in watch.made:
            return True
        got = _values(d, watch)
        if not got:
            continue
        x = App.getDocument(d[0]).getObject(d[1])
        if x is None:
            return True
        if x.TypeId == "App::VarSet":
            if text is None:
                text = set(re.findall(r"\w+", " ".join(e for _p, e in o.ExpressionEngine)))
            if not text & got:
                continue
        return True
    return False


def _done(o):
    return _DONE | {"Placement"} if o.isDerivedFrom("Part::Datum") else _DONE


def _as_tip(o):
    """A Solid shows its last step: the very shape, not a copy."""
    try:
        return o.TypeId == "PartDesign::Body" and o.Tip is not None and o.Shape.isPartner(o.Tip.Shape)
    except Exception:
        return False


def _cleared(doc, watch, links, was_touched, failed):
    """The objects whose touched mark says nothing: computed again they would give the shape they have."""
    out = []
    after = _links(doc)
    for o in doc.Objects:
        if "Touched" not in o.State or o.Name in failed or not o.isValid() or \
                (o.Name in was_touched and not _as_tip(o)):
            continue
        key = (doc.Name, o.Name)
        if o.TypeId.startswith("TechDraw::"):
            continue
        mine = watch.changed.get(key, set())
        if o.TypeId in _PLAIN or o.TypeId.startswith("App::Origin"):
            out.append(o)  # it holds values only, nothing is computed from them in it
        elif _shape(o) is None and not o.isDerivedFrom("Sketcher::SketchObject") and not o.isDerivedFrom("Part::Datum"):
            continue  # (a sketch of a Text only has none; a Work Axis is its place)
        elif key in watch.made or _as_tip(o):
            out.append(o)  # taken out and put back as it was, its shape with it
        elif (bool(mine - _OUTPUTS - ({"Group"} if o.TypeId == "PartDesign::Body" else set())) or _moved(o, links.get(o.Name, set()) | after[o.Name], watch)) == bool(mine & _done(o)):
            out.append(o)  # its shape came back with its inputs; or neither changed
    return out


def _views(doc, watch):
    """Drawing views of a shape that changed: their lines are no stored value, they are drawn again."""
    for o in doc.Objects:
        if not o.TypeId.startswith("TechDraw::DrawView"):
            continue
        try:
            if any("Shape" in watch.changed.get((x.Document.Name, x.Name), ()) for x in o.OutListRecursive):
                o.touch()
        except Exception:
            pass


def _relink(doc):
    """model.relink without its recompute: the formulas give the values just restored."""
    from incad.core import model
    before = {o.Name for o in doc.Objects if "Touched" in o.State}
    fixed = model.relink(doc, compute=False)
    if fixed:
        for o in doc.Objects:
            if "Touched" in o.State and o.Name not in before:
                o.purgeTouched()
    return fixed


def invalid(doc):
    return {o.Name for o in doc.Objects if "Invalid" in o.State}


def recompute(doc, skip=()):
    global _back
    _back += 1  # (a step taken back: what is computed is what that step's record says, see note_change)
    try:
        _recompute(doc, skip)
    finally:
        _back -= 1


def _recompute(doc, skip):
    """doc.recompute(), the failed objects named in `skip` not tried again (they were, at every window opened; OK tries
    them)."""
    bad = invalid(doc) & set(skip)
    touched = [o for o in doc.Objects if "Touched" in o.State and o.Name not in bad]
    if not bad:
        doc.recompute()
        return
    todo = {}
    for o in touched:
        todo[o.Name] = o
        for u in o.InListRecursive:
            if u.Document is doc and u.Name not in bad:
                todo[u.Name] = u
    if todo:
        doc.recompute(list(todo.values()))


def take_back(doc, step, skip=()):
    """step() undoes, redoes or aborts in `doc`; then only what it left out of step is computed. Returns the names
    whose formula links were repaired."""
    if any(o.TypeId.startswith(_WHOLE) for o in doc.Objects):
        step()
        doc.recompute()
        from incad.core import model
        return model.relink(doc)
    was = {o.Name for o in doc.Objects if "Touched" in o.State}
    failed = _failed.get(doc.Name, set()) | {o.Name for o in doc.Objects if not o.isValid()}
    loose = _loose.get(doc.Name, set())
    if loose:
        failed = failed | {o.Name for o in doc.Objects if o.Name in loose or any(x.Name in loose for x in o.OutListRecursive)}
    links = _links(doc)
    watch = _watched(step)
    fixed = []
    try:
        fixed = _relink(doc)
        clear = _cleared(doc, watch, links, was, failed)
        _views(doc, watch)
        for o in clear:
            o.purgeTouched()
    except Exception:
        log.logger().exception("settle after undo")
    # nothing marks an object whose base came back while it did not (not computed in the step taken back)
    for o in doc.Objects:
        key = (doc.Name, o.Name)
        if "Touched" in o.State or not o.isDerivedFrom("Part::Feature") or _shape(o) is None or not o.isValid() \
                or watch.changed.get(key, set()) & _done(o) or key in watch.made or _as_tip(o):
            continue
        if any("Shape" in watch.changed.get(d, ()) for d in _sources(o, links.get(o.Name, ()))):
            o.touch()
    recompute(doc, skip)
    return fixed


def touched(doc):
    return {o.Name for o in doc.Objects if "Touched" in o.State}


def aborted(doc, step, was):
    """step() aborts the whole open step of `doc` (nothing of it committed): everything is as when it opened, so what
    was settled then is settled now, whatever the step computed meanwhile. was: the names touched when it opened."""
    failed = {o.Name for o in doc.Objects if not o.isValid()}
    watch = _watched(step)
    try:
        _relink(doc)
        _views(doc, watch)
        for o in doc.Objects:
            if "Touched" not in o.State or o.Name in was or o.Name in failed or not o.isValid():
                continue
            if o.TypeId in _PLAIN or o.isDerivedFrom("Part::Feature") or o.isDerivedFrom("App::Origin"):
                o.purgeTouched()
    except Exception:
        log.logger().exception("settle after an abort")


class Added:
    """Round a change that adds a step nothing is built on yet (a new sketch, made and computed alone): what its Solid
    and the Solid's users got touched for is no reason to compute them."""

    def __init__(self, doc):
        self.doc, self.was, self.links = doc, touched(doc), _links(doc)
        self.watch = _Watch()
        App.addDocumentObserver(self.watch)

    def close(self, done=True):
        App.removeDocumentObserver(self.watch)
        if not done:
            return
        try:
            failed = {o.Name for o in self.doc.Objects if not o.isValid()}
            made = {n for _d, n in self.watch.made}
            for o in _cleared(self.doc, self.watch, self.links, self.was | made, failed):
                if o.Name not in made:
                    o.purgeTouched()
        except Exception:
            log.logger().exception("settle after an added step")


class Quiet:
    """From now until untouch(): nothing but looks changed (a sketch opened and closed again as it was)? Then what
    got touched meanwhile is not computed."""

    def __init__(self, doc):
        self.doc, self.was, self.steps = doc, touched(doc), len(doc.UndoNames)
        self.failed = invalid(doc)
        self.watch = _Watch()
        App.addDocumentObserver(self.watch)

    def untouch(self):
        if self.watch is None:
            return
        try:
            App.removeDocumentObserver(self.watch)
        except Exception:
            pass
        watch, self.watch = self.watch, None
        try:
            doc = self.doc
            if len(doc.UndoNames) != self.steps or any(d == doc.Name for d, _n in watch.made):
                return
            if any(_values(k, watch) for k in watch.changed if k[0] == doc.Name):
                log.logger().info("sketch closed, changed meanwhile: %s",
                                  {k[1]: sorted(_values(k, watch)) for k in watch.changed if _values(k, watch)})
                return
            for o in doc.Objects:
                if "Touched" in o.State and o.Name not in self.was and o.isValid():
                    o.purgeTouched()
        except Exception:
            log.logger().exception("untouch")
