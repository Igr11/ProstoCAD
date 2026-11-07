"""Model parameters, Inventor style: every sketch dimension and every feature value has a name (d0, d1, …) that
formulas can use. Dimensions are named Sketcher constraints; feature values are properties of a hidden VarSet
("InCadModel") bound to the feature properties by FreeCAD expressions."""
import contextlib
import re

import FreeCAD as App

from incad.core import document

MODEL_NAME = "InCadModel"
_GROUP = "Model"
_DNAME = re.compile(r"^d(\d+)$")
_TOKEN = re.compile(r"(?<![\w.<>])([A-Za-z_][A-Za-z0-9_]*)(?![\w(.])")


UNIT_WORDS = {"mm", "cm", "m", "in", "ft", "deg", "rad", "um", "km", "mil", "thou", "yd"}


_REVOLVE_TYPES = ("PartDesign::Revolution", "PartDesign::Groove")
_KERNEL_FAILURE = {
    "PartDesign::Chamfer": "The chamfer couldn't be made: use a smaller distance, or pick other edges",
    "PartDesign::Fillet": "The fillet couldn't be made: use a smaller radius, or pick other edges",
    "PartDesign::Thickness": "The shell couldn't be made: use a smaller thickness, or remove other faces",
    "PartDesign::Draft": "The draft couldn't be made: use a smaller angle, or pick other faces",
}


_RAW = (
    ("end face of tapered extrusion is empty",
     "The taper closes the profile before the end: use a smaller angle or a shorter distance"),
    ("Linked shape object is empty",
     "The sketch has nothing to use: it is empty, or all its lines are construction"),
    ("No faces found in this direction",
     "Nothing of the part lies ahead for To Next: turn the direction round, or give a distance"),
    ("Up to face: Failed to extract face",
     "The face it goes up to is gone (the part changed): pick the face again"),
    ("V1==V2", "The profile must be a closed outline: a single line can't be swept into a solid"),
    ("result of offsetting is null", "The wall is as thick as the profile, or thicker: make it thinner"),
    ("height too small", "Give the coil a height above zero"),
    ("Pitch too small", "Give the coil a pitch above zero"),
    ("Pattern length too small", "The spacing must be more than 0: give the copies a spacing"),
    ("Invalid edge link", "An edge it was on is gone after the change: edit it and pick the edges again"),
    ("Invalid face link", "A face it was on is gone after the change: edit it and pick the faces again"),
    ("Must not intersect sketch", "What it goes to lies in the sketch's own plane: pick a face or point off it"),
    ("cannot determine type of null shape",
     "The shape can't be built on this face (a free-form face): try a flat or round face, or a smaller depth"),
    ("Unsupported geometry type to get reference axis",
     "Pick a straight line, an edge or an axis for the axis (a round face is not taken)"),
)


def friendly(message, type_id=None):
    """FreeCAD's expression errors in plain words (no internal paths). type_id (of the feature that failed) lets a
    kernel failure be explained for that feature instead of as a revolve."""
    m = re.search(r"Property '([^']+)' not found", message)
    if m:
        return "Unknown name '%s' (not a parameter or dimension)" % m.group(1)
    m = re.search(r"Failed to parse expression '(.*)'", message)
    if m:
        text = re.sub(r"\b\w+\.Constraints\.", "", m.group(1))  # no internal paths
        glued = re.search(r"(\d+\.?\d*)([A-Za-z_]\w*)", text)
        if glued and glued.group(2).lower() not in UNIT_WORDS:
            return "Put an operator between %s and %s" % (glued.group(1), glued.group(2))
        return "Cannot read the formula '%s'" % text
    if "cyclic" in message.lower():
        return "This formula refers back to itself (a loop)"
    if "axis intersects the sketch" in message:
        return "The axis crosses the profile: choose another axis or move the profile off it"
    if "perpendicular to the sketch" in message:
        return "The axis is perpendicular to the profile: pick an axis that lies in the profile's plane"
    for raw, said in _RAW:  # FreeCAD's and OpenCASCADE's own words, in plain ones (the finder's list)
        if raw in message:
            return said
    if "command not done" in message or "NullObject" in message or "BRepAlgoAPI" in message or \
            "BRepOffsetAPI" in message:
        if type_id is not None and type_id not in _REVOLVE_TYPES:
            return _KERNEL_FAILURE.get(type_id, "The shape couldn't be built: the values are too large for the "
                                                "geometry, or the picks don't fit it")
        return ("The revolve couldn't be built: the axis must lie IN the profile's plane and not cross or touch "
                "the profile. Draw the profile on a plane that passes through the axis.")
    return message.split("\n")[0]


def _model_obj(doc, create=False):
    obj = doc.getObject(MODEL_NAME)
    if obj is None and create:
        obj = doc.addObject("App::VarSet", MODEL_NAME)
        obj.Label = "Model Parameters"
    return obj


def _feature_names(doc):
    obj = _model_obj(doc)
    return [p for p in obj.PropertiesList if obj.getGroupOfProperty(p) == _GROUP] if obj is not None else []


def _dimension_entries(doc):
    from incad.core import sketch
    return sketch.dimensions(doc)


def used_names(doc):
    from incad.core import sketch
    names = {c.Name for sk in doc.Objects if sketch.is_sketch(sk) for c in sk.Constraints if c.Name}
    names |= set(_feature_names(doc))
    return names | {n for n, _, _ in document.list_parameters(doc)}


def next_name(doc, taken=()):
    from incad.core import sketch
    numbers = [int(m.group(1)) for m in (_DNAME.match(n) for n in set(taken)) if m]
    return "d%d" % max([sketch._next_number(doc)] + [n + 1 for n in numbers])


def claim_name(doc):
    """The next dN for a dimension or a feature value; never handed out again (counter kept in the file)."""
    from incad.core import sketch
    name = next_name(doc)
    meta = dict(doc.Meta)
    meta[sketch.NEXT_KEY] = str(int(name[1:]) + 1)
    doc.Meta = meta
    return name


def refs(doc):
    out = {n: "%s.%s" % (document.PARAMS_NAME, n) for n, _, _ in document.list_parameters(doc)}
    for name, sk, _ in _dimension_entries(doc):
        out[name] = "%s.Constraints.%s" % (sk.Name, name)
    for name in _feature_names(doc):
        out[name] = "%s.%s" % (MODEL_NAME, name)
    return out


_NUMBER = re.compile(r"(?<![\w.])(\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)(?![\w.])")
_HAS_UNIT = re.compile(r"\s*[A-Za-z°\"']")


def with_units(text, unit):
    """Inventor reads a plain number added to or subtracted from a dimension in its unit ('d16+2' = d16 + 2 mm);
    multiplied or divided it stays a plain number ('d3*2'). FreeCAD refuses mixing them ('Unit mismatch').
    unit None: unchanged (counts)."""
    if not unit:
        return text
    out, last = [], 0
    for m in _NUMBER.finditer(text):
        before = text[:m.start()].rstrip()
        after = text[m.end():]
        if _HAS_UNIT.match(after):  # '2 mm', '2mm', '30°' — the user gave a unit
            continue
        left = before[-1:] if before else ""
        right = after.lstrip()[:1]
        if left in "*/^" and left or right in "*/^" and right:
            continue
        if left == "(" and re.search(r"[A-Za-z_]\w*\s*\($", before):  # a function's argument: sqrt(4)
            continue
        out.append(text[last:m.end()] + " " + unit)
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def to_expression(doc, text):
    r = refs(doc)
    return _TOKEN.sub(lambda m: r.get(m.group(1), m.group(1)), text)


def to_display(doc, expr):
    for path, name in sorted(((p, n) for n, p in refs(doc).items()), key=lambda pn: -len(pn[0])):
        expr = expr.replace(path, name)
    return re.sub(r"(?<![\w])\.Constraints\.([A-Za-z_]\w*)", r"\1", expr)


def _expression(obj, path):
    for p, e in obj.ExpressionEngine:
        if p.lstrip(".") == path:
            return e
    return ""


def feature_parameters(doc):
    obj = _model_obj(doc)
    if obj is None:
        return []
    return [(n, parameter_text(doc, n) if not document._is_literal(_expression(obj, n)) else "",
             document._format(getattr(obj, n))) for n in _feature_names(doc)]


def model_parameters(doc):
    from incad.core import sketch
    return sketch.dimension_parameters(doc) + feature_parameters(doc)


def is_model_parameter(doc, name):
    from incad.core import sketch
    return name in _feature_names(doc) or sketch.find_dimension(doc, name)[0] is not None


def _with_unit(doc, text, unit_kind):
    if unit_kind == "count":
        return text
    try:
        q = App.Units.Quantity(text)
    except Exception:
        return text
    if q.Unit == App.Units.Quantity("1").Unit:
        return "%s %s" % (q.Value, "deg" if unit_kind == "angle" else (document.part_units(doc) or "mm"))
    return text


def _formula_units(doc, text, unit_kind):
    unit = {"count": None, "angle": "deg"}.get(unit_kind, document.part_units(doc) or "mm")
    return with_units(text, unit)


def named_parameter(doc, base):
    """The first free '<base>N' (Mate1, Flush2…): a constraint's value is named after it."""
    taken = set(refs(doc))
    obj = _model_obj(doc, create=False)
    if obj is not None:
        taken |= set(obj.PropertiesList)
    n = 1
    while "%s%d" % (base, n) in taken:
        n += 1
    return "%s%d" % (base, n)


def _values(obj):
    """The holder's values by name; a quantity by its number (its unit is not saved: it reads '90', not '90°', until
    recomputed after a reopen, which is no change)."""
    out = {}
    for p in obj.PropertiesList:
        try:
            v = obj.getPropertyByName(p)
            out[p] = round(v.Value, 12) if hasattr(v, "Value") else repr(v)
        except Exception:
            pass
    return out


@contextlib.contextmanager
def changing_values(doc, obj):
    """Around a change of the model's values (a new, changed or removed one): afterwards the values are recomputed
    and only the objects whose formulas use a value that changed are left touched. FreeCAD touches every object that
    uses ANY of its values the moment one of them changes: one new value recomputed the whole part afterwards
    (35 features, 65 s for a Text on the user's part)."""
    before = _values(obj) if obj is not None else {}
    touched = {o.Name for o in doc.Objects if "Touched" in o.State}
    yield
    obj = _model_obj(doc)
    if obj is None:
        return
    doc.recompute([obj])
    after = _values(obj)
    changed = {n for n, v in after.items() if before.get(n) != v} | (set(before) - set(after))
    changed -= {"ExpressionEngine", "Label2", "Label"}
    used = re.compile(r"(?<!\w)(%s)(?!\w)" % "|".join(re.escape(n) for n in sorted(changed))) if changed else None
    for o in doc.Objects:
        if o is obj or o.Name in touched or "Touched" not in o.State:
            continue
        exprs = [e for _, e in (getattr(o, "ExpressionEngine", []) or [])]
        if not exprs or (used is not None and any(used.search(e) for e in exprs)):
            continue  # touched for a reason of its own, or it reads a value that changed
        o.purgeTouched()


def new_feature_parameter(doc, text, unit_kind="length", base=None):
    obj = _model_obj(doc, create=True)
    name = named_parameter(doc, base) if base else claim_name(doc)
    with changing_values(doc, obj):  # a new value: nothing uses it yet, nothing else recomputes for it
        obj.addProperty("App::PropertyQuantity", name, _GROUP)
        try:
            obj.setExpression(name, to_expression(doc, _formula_units(doc, _with_unit(doc, text, unit_kind), unit_kind)))
        except Exception as e:
            obj.removeProperty(name)
            raise ValueError("Invalid value %r: %s" % (text, e))
        _remember_kind(obj, name, unit_kind)
    if "Invalid" in obj.State or "Error" in obj.State:
        message = friendly(obj.getStatusString())
        with changing_values(doc, obj):
            obj.setExpression(name, None)
            obj.removeProperty(name)
        raise ValueError("Invalid value %r: %s" % (text, message))
    return name


def users_of(doc, name):
    """Labels of the objects whose formulas use the feature value `name` (the value itself excluded)."""
    pattern = re.compile(r"(?:%s\.|(?<![\w.]))%s(?!\w)" % (MODEL_NAME, re.escape(name)))
    model_obj = _model_obj(doc)
    out = []
    for obj in doc.Objects:
        for path, expr in getattr(obj, "ExpressionEngine", []) or []:
            if obj is model_obj and path.lstrip(".") == name:
                continue
            if pattern.search(expr):
                out.append(obj.Label if obj is not model_obj else path.lstrip("."))
                break
    return out


def remove_model_parameter(doc, name):
    """Remove a feature value nothing uses any more (left behind by a deleted feature)."""
    from incad.core import sketch
    if sketch.find_dimension(doc, name)[0] is not None:
        raise ValueError("%s is a sketch dimension: delete it in its sketch" % name)
    obj = _model_obj(doc)
    if obj is None or name not in _feature_names(doc):
        raise ValueError("No parameter named %r" % name)
    users = users_of(doc, name)
    if users:
        raise ValueError("%s is used by %s" % (name, ", ".join(users)))
    with changing_values(doc, obj):
        obj.setExpression(name, None)
        obj.removeProperty(name)


def _remember_kind(obj, name, unit_kind):
    kinds = dict(item.split("=", 1) for item in obj.Label2.split(";") if "=" in item) if obj.Label2 else {}
    kinds[name] = unit_kind
    obj.Label2 = ";".join("%s=%s" % kv for kv in sorted(kinds.items()))


def _unit_kind(obj, name):
    kinds = dict(item.split("=", 1) for item in obj.Label2.split(";") if "=" in item) if obj.Label2 else {}
    return kinds.get(name, "length")


def bind(feature, prop, name):
    feature.setExpression(prop, "%s.%s" % (MODEL_NAME, name))
    feature.touch()  # it must take the value on its next recompute (changing_values keeps what was touched before)


def bound_parameter(feature, prop):
    expr = _expression(feature, prop)
    prefix = MODEL_NAME + "."
    if expr.startswith("min(" + prefix):  # a value held under a limit (a chamfer's Fit step): clamped()
        m = re.match(r"min\(%s\.(\w+)\s*;\s*[\w.]+\)$" % re.escape(MODEL_NAME), expr)
        return m.group(1) if m else None
    return expr[len(prefix):] if expr.startswith(prefix) else None


def clamped(name, obj_name, prop):
    """The expression of a value bound to the parameter but never above obj_name.prop (bound_parameter reads it)."""
    return "min(%s.%s; %s.%s)" % (MODEL_NAME, name, obj_name, prop)


def parameter_text(doc, name):
    """What the user typed: the formula with plain names, or the value."""
    from incad.core import sketch
    obj = _model_obj(doc)
    if obj is not None and name in _feature_names(doc):
        expr = _expression(obj, name)
        if expr and not document._is_literal(expr):
            return to_display(doc, expr)
        # a plain value: from what was stored, not from the property, whose unit FreeCAD does not save ('90°' read
        # '90' after a reopen until the parameter was recomputed)
        return document._format(App.Units.Quantity(expr) if expr else getattr(obj, name))
    sk, _ = sketch.find_dimension(doc, name)
    if sk is not None:
        expr = _expression(sk, "Constraints." + name)
        return to_display(doc, expr) if expr else document._format(sk.getDatum(name))
    raise ValueError("No parameter named %r" % name)


def set_model_parameter(doc, name, text, everything=True):
    """Set a dimension or a feature value. Call inside safe.run_safely or a feature session's transaction.
    everything=False: only the parameter itself is computed (to check it) — a feature dialog computes the rest
    itself, and a whole recompute here recomputed a big part for every value of the dialog (Hole: up to nine)."""
    from incad.core import sketch
    if sketch.find_dimension(doc, name)[0] is not None:
        return sketch.set_dimension(doc, name, text, everything)
    obj = _model_obj(doc)
    if obj is None or name not in _feature_names(doc):
        raise ValueError("No parameter named %r" % name)
    text = (text or "").strip()
    if not text:
        raise ValueError("%s needs a value" % name)
    with changing_values(doc, obj):  # only what uses this value recomputes after it
        try:
            kind = _unit_kind(obj, name)
            obj.setExpression(name, to_expression(doc, _formula_units(doc, _with_unit(doc, text, kind), kind)))
        except Exception as e:
            raise ValueError("%s: %s" % (name, friendly(str(e))))
    if everything:
        doc.recompute()
    if "Invalid" in obj.State or "Error" in obj.State:
        raise ValueError("%s: %s" % (name, friendly(obj.getStatusString())))
    from incad.features import constraints  # an assembly Angle may change FreeCAD joint type with its value
    constraints.follow_parameters(doc)


def rename_model_parameter(doc, old, new):
    from incad.core import sketch
    if sketch.find_dimension(doc, old)[0] is not None:
        return sketch.rename_dimension(doc, old, new)
    obj = _model_obj(doc)
    if obj is None or old not in _feature_names(doc):
        raise ValueError("No parameter named %r" % old)
    if not document._NAME_RE.match(new or ""):
        raise ValueError("A name starts with a letter or _ and has only letters, digits and _ (got %r)" % new)
    if document._is_literal(new):
        raise ValueError("%r is a unit symbol; choose another name" % new)
    if new != old and new in used_names(doc):
        raise ValueError("%r is already used" % new)
    kind = _unit_kind(obj, old)
    obj.renameProperty(old, new)
    _remember_kind(obj, new, kind)
    doc.recompute()


_TARGET = re.compile(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_]*)\.")


def relink(doc):
    """Repair formula links after undo/abort. FreeCAD (probed) drops the dependency of an object it restores, so the
    object stops following its parameters; clearing and setting the formula again re-registers it.
    Returns the names of the objects repaired."""
    repaired = []
    for obj in doc.Objects:
        entries = list(getattr(obj, "ExpressionEngine", []) or [])
        if not entries:
            continue
        targets = {doc.getObject(n) for _, e in entries for n in _TARGET.findall(e)} - {None, obj}
        if all(t in obj.OutList for t in targets):
            continue
        for path, expr in entries:
            obj.setExpression(path.lstrip("."), None)
            obj.setExpression(path.lstrip("."), expr)
        repaired.append(obj.Name)
    if repaired:
        doc.recompute()
    return repaired


def restore_units(doc):
    """Give the parameters their units back after a document opens: a parameter is an App::PropertyQuantity, whose
    unit FreeCAD does not save ('90°' reads '90', '12 mm' reads '12' until it is recomputed). Recomputing the two
    parameter holders (not the part) puts them back from their stored values and formulas."""
    for obj in (document.parameters(doc, create=False), _model_obj(doc)):
        if obj is not None and list(obj.ExpressionEngine):
            obj.recompute()
            obj.purgeTouched()  # the values are the same numbers: nothing that uses them needs a recompute
