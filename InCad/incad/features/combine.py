"""Combine (Inventor) = PartDesign Boolean in the base solid; the tool solids are always kept.

Cut can leave a print clearance (a model parameter):
- on the cavity: the base is cut by the tool grown by the clearance ("<Tool> grown": only the tool's piece near the
  base, its offset kept while that piece stays the same);
- on the tool: the cavity is exact and a fit copy "<Tool> fit" (a Body on a shrunk Part::Offset) is made to print;
- split: half on each.
Settings and helper names live on the Boolean (InCadCombine, JSON)."""
import json

import FreeCAD as App

from incad.features.common import Field, format_picks, parse_picks, resolve

TITLE = "Boolean"  # the ribbon button and the dialog (Inventor calls it Combine)
ICON = "combine"
TYPES = ("PartDesign::Boolean",)
NEEDS_PROFILE = False
STRUCTURAL = ()  # every operation can be switched on edit
RECREATE_ON = ("base",)  # a different base means a Boolean in another solid
PROPS = {}
_STORE = "InCadCombine"
_TYPE = {"join": "Fuse", "cut": "Cut", "intersect": "Common"}
# The kernel's fuzzy tolerance for a new Boolean, mm: faces this close count as one. Without it a tool 0.001 mm short
# left a skin that thin, and two solids 0.001 mm apart stayed two; far below anything a printer makes.
FUZZY = 1e-3

FIELDS = (
    Field("base", "Base", "pick", pick="body"),
    Field("tools", "Tools", "picks", pick="body"),
    Field("operation", "Operation", "choice", (("join", "Join"), ("cut", "Cut"), ("intersect", "Intersect")),
          "cut"),
    Field("clearance", "Clearance (per side)", "value_length", default="0.2 mm", show_if=("operation", ("cut",))),
    Field("apply_to", "Clearance on", "choice", (("cavity", "Cavity"), ("tool", "Tool"), ("split", "Split")),
          "cavity", show_if=("operation", ("cut",))),
    Field("keep_tool", "Keep tool", "bool", default=False),
)


def claims(feature):
    return hasattr(feature, _STORE)


def body_of(obj, sub=""):
    if obj is None:
        return None
    if obj.TypeId == "PartDesign::Body":
        return obj
    parent = obj.getParentGeoFeatureGroup()
    return parent if parent is not None and parent.TypeId == "PartDesign::Body" else None


def defaults(doc, body, profile=None):
    from incad.core import prefs
    values = {f.key: f.default for f in FIELDS}
    # Inventor's order: nothing preset — the first solid clicked is the base, every next one a tool
    values.update(base="", tools="", clearance="%g mm" % prefs.thread_clearance())
    return values


def preselect(pairs):
    """Selected solids: the first is the base, the others are tools."""
    bodies = []
    for obj, sub in pairs:
        b = body_of(obj, sub)
        if b is not None and b not in bodies:
            bodies.append(b)
    if not bodies:
        return {}
    out = {"base": bodies[0].Name}
    if len(bodies) > 1:
        out["tools"] = format_picks([(b, "") for b in bodies[1:]])
    return out


def _bodies(doc, text):
    """Solids named by the picks: bodies, or links to bodies (assembly cuts use links to other files)."""
    out = []
    for obj, sub in parse_picks(doc, text):
        if obj.TypeId == "App::Link" and not sub:
            if obj not in out:
                out.append(obj)
            continue
        b = body_of(*resolve(obj, sub)) if obj.TypeId != "PartDesign::Body" else obj
        if b is not None and b not in out:
            out.append(b)
    return out


def _load(f):
    try:
        return json.loads(getattr(f, _STORE, "") or "{}")
    except ValueError:
        return {}


def _store(f, data):
    if not hasattr(f, _STORE):
        f.addProperty("App::PropertyString", _STORE, "InCad", "InCad combine settings")
        f.setPropertyStatus(_STORE, "Hidden")
    setattr(f, _STORE, json.dumps(data, sort_keys=True))


def clearance_parameter(f):
    """The clearance parameter, read from a helper's formula (follows renames); else the stored name."""
    import re
    from incad.core import model
    data = _load(f)
    for helpers in data.get("helpers", {}).values():
        for key in ("grown", "fit_shape"):
            obj = f.Document.getObject(helpers.get(key, "")) if helpers.get(key) else None
            for path, expr in (getattr(obj, "ExpressionEngine", []) or []):
                m = re.search(r"%s\.([A-Za-z_][A-Za-z0-9_]*)" % model.MODEL_NAME, expr)
                if path.lstrip(".") == "Value" and m:
                    return m.group(1)
    return data.get("clearance_param")


def _mm(text):
    try:
        return App.Units.Quantity(text or "0 mm").Value
    except Exception:
        raise ValueError("Clearance: enter a length such as 0.2 mm")


def _clearance_param(doc, data, text):
    from incad.core import model
    name = data.get("clearance_param")
    if name and name in [p[0] for p in model.feature_parameters(doc)]:
        if model.parameter_text(doc, name) != text:
            model.set_model_parameter(doc, name, text, everything=False)
        return name
    return model.new_feature_parameter(doc, text, "length")


NEAR = 5.0  # mm round the base's box: more than any clearance


def _near_piece(tool, base_feature):
    """The tool's piece inside the base feature's box grown by NEAR (what a grown cavity needs), else the whole tool."""
    import Part
    shape = tool.Shape if tool is not None else None
    if shape is None or shape.isNull():
        return None
    base = base_feature.Shape if base_feature is not None else None
    if base is not None and not base.isNull():
        bb = base.BoundBox
        box = Part.makeBox(bb.XLength + 2 * NEAR, bb.YLength + 2 * NEAR, bb.ZLength + 2 * NEAR,
                           App.Vector(bb.XMin - NEAR, bb.YMin - NEAR, bb.ZMin - NEAR))
        try:
            solids = shape.common(box).Solids
            if solids:
                return solids[0] if len(solids) == 1 else Part.makeCompound(solids)
        except Exception:
            pass
    return shape.copy()


def _signature(shape):
    bb = shape.BoundBox
    return (round(shape.Volume, 4), round(shape.Area, 4), len(shape.Faces),
            tuple(round(x, 4) for x in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)))


class _Grown:
    """The tool grown by the clearance for a cavity: only its piece near the base (the user's jar with a wide wrapped
    label far from the lid could not be offset as a whole), and the offset kept while that piece stays the same — a
    Part::Offset of the whole jar ran 8 s on every change to the jar, a label far below the lid included."""

    def __init__(self, obj):
        obj.Proxy = self

    def execute(self, obj):
        import Part
        piece = _near_piece(obj.Tool, obj.Base)
        if piece is None:
            obj.Shape = Part.Shape()
            return
        self.piece_volume = piece.Volume
        key = (_signature(piece), round(obj.Value.Value, 7), obj.Join, bool(obj.Intersection))
        cache = getattr(self, "_cache", None)
        if cache is not None and cache[0] == key:
            obj.Shape = cache[1]
            return
        self.offsets = getattr(self, "offsets", 0) + 1
        grown = Part.Shape()
        tries = [(2 if obj.Join == "Intersection" else 0, bool(obj.Intersection)), (0, True), (0, False)]
        for join, inter in tries:  # on every recompute, not only in the dialog: a tool changed later came out
            try:                   # empty and the Boolean failed ("Tool shape is null", the user's log)
                grown = piece.makeOffsetShape(obj.Value.Value, 1e-6, inter=inter, self_inter=False, offsetMode=0,
                                              join=join, fill=False)
            except Exception:
                grown = Part.Shape()
            if not grown.isNull() and grown.isValid() and grown.Volume > piece.Volume:
                break
        else:
            from incad.core import log
            log.report("warning", "%s: the tool couldn't be grown by the clearance now: cut without it"
                       % obj.Label)
            grown = piece.copy()  # the cavity exact rather than the part broken
        self._cache = (key, grown)
        obj.Shape = grown

    def dumps(self):
        return None

    def loads(self, state):
        return None


def _is_grown(obj):
    return obj is not None and isinstance(getattr(obj, "Proxy", None), _Grown)


def _grown(doc, name, label, tool, base_feature, expr):
    """The cavity's grown tool (a _Grown); an older file's Part::Offset is replaced (the old one goes with the
    helpers this setup no longer uses)."""
    obj = doc.getObject(name) if name else None
    if not _is_grown(obj):
        obj = doc.addObject("Part::FeaturePython", "CombineGrown")
        obj.addProperty("App::PropertyLink", "Tool", "InCad", "The tool solid")
        obj.addProperty("App::PropertyLink", "Base", "InCad", "The base solid's feature before the Boolean")
        obj.addProperty("App::PropertyDistance", "Value", "InCad", "The clearance")
        obj.addProperty("App::PropertyEnumeration", "Join", "InCad", "How offset faces meet")
        obj.Join = ["Arc", "Tangent", "Intersection"]
        obj.Join = "Intersection"
        obj.addProperty("App::PropertyBool", "Intersection", "InCad", "Self-intersections removed")
        _Grown(obj)
        obj.Visibility = False
    obj.Label = label
    obj.Tool = tool
    obj.Base = base_feature
    obj.setExpression("Value", expr)
    return obj


def _remove_offset(doc, name):
    if name and doc.getObject(name) is not None:
        doc.removeObject(name)


def _offset(doc, name, label, source, expr):
    obj = doc.getObject(name) if name else None
    if obj is None:
        obj = doc.addObject("Part::Offset", "CombineOffset")
        obj.Join = "Intersection"
        obj.Visibility = False
    obj.Label = label
    obj.Source = source
    obj.setExpression("Value", expr)
    return obj


def _remove(doc, name):
    """Remove a helper; a fit copy the user has worked on (features added, or used elsewhere) is kept.
    Returns False when it was kept."""
    obj = doc.getObject(name) if name else None
    if obj is None:
        return True
    if obj.TypeId == "PartDesign::Body":
        work = [o for o in obj.Group if o.TypeId != "PartDesign::FeatureBase"]
        if work or list(obj.InList):
            obj.Visibility = True
            return False
        base = obj.BaseFeature
        obj.BaseFeature = None
        for child in list(obj.Group):
            obj.removeObject(child)
            if doc.getObject(child.Name) is not None:
                doc.removeObject(child.Name)
        doc.removeObject(obj.Name)
        if base is not None and doc.getObject(base.Name) is not None:
            doc.removeObject(base.Name)
        return True
    _remove_offset(doc, obj.Name)
    return True


class _NoClearance(ValueError):
    pass


def _build(doc, f, base, values):
    """The Boolean as the values say; a clearance its tool can't be grown or shrunk by (an Emboss, a thread: the
    user's Cut 'doesn't work') is left out: the cut is made with Clearance 0 mm, and the status line says so."""
    try:
        return _build_with(doc, f, base, values)
    except _NoClearance as e:
        from incad.core import log
        made = _build_with(doc, f, base, dict(values, clearance="0 mm"), fallback=True)
        log.report("warning", "%s: made with Clearance 0 mm — %s" % (f.Label, e))
        return made


def adjusted(doc, f):
    """The dialog shows Clearance 0 mm when the clearance asked for could not be made."""
    return {"clearance": "0 mm"} if _load(f).get("clearance_fallback") else {}


def _build_with(doc, f, base, values, fallback=False):
    from incad.core import model
    tools = _bodies(doc, values.get("tools"))
    if not tools:
        raise ValueError("Pick at least one other solid as a tool")
    if base in tools:
        raise ValueError("The base can't be a tool: pick the other solid(s) as tools")
    from incad.features import solid_mirror
    for m in solid_mirror.stop_before(base, f.BaseFeature):
        from incad.core import log
        log.report("info", "%s now mirrors %s as it is before %s" % (m.getParentGeoFeatureGroup().Label, base.Label,
                                                                        f.Label))
    for tool in tools:
        if base in tool.OutListRecursive:
            raise ValueError("%s already depends on %s (they were combined): pick another base or tool"
                             % (tool.Label, base.Label))
    from incad.core import faceted
    for b in [base] + tools:  # an STL's triangles: hours of kernel time, the program frozen (the user's force quit)
        if faceted.is_faceted(b.Shape):
            raise ValueError(faceted.refusal(b.Label, b.Shape))
    op = values.get("operation") or "cut"
    data = _load(f)
    old_helpers = data.get("helpers", {})
    clearance = _mm(values.get("clearance")) if op == "cut" else 0.0
    apply_to = values.get("apply_to") or "cavity"
    name = None
    if op == "cut" and clearance > 0:
        name = _clearance_param(doc, {"clearance_param": clearance_parameter(f)}, values.get("clearance") or "0.2 mm")
    ref = "%s.%s" % (model.MODEL_NAME, name) if name else None
    grow = {"cavity": ref, "split": "%s / 2" % ref} if ref else {}
    shrink = {"tool": "-%s" % ref, "split": "-%s / 2" % ref} if ref else {}
    helpers, group = {}, []
    for tool in tools:
        old = old_helpers.get(tool.Name, {})
        mine = {}
        if apply_to in grow:
            g = _grown(doc, old.get("grown"), "%s grown" % tool.Label, tool, f.BaseFeature, grow[apply_to])
            mine["grown"] = g.Name
            group.append(g)
        else:
            group.append(tool)
        if apply_to in shrink:
            s = _offset(doc, old.get("fit_shape"), "%s fit shape" % tool.Label, tool, shrink[apply_to])
            mine["fit_shape"] = s.Name
            fit = doc.getObject(old["fit"]) if old.get("fit") else None
            if fit is None:
                fit = doc.addObject("PartDesign::Body", "Fit")
                fit.BaseFeature = s
            fit.Label = "%s fit" % tool.Label
            mine["fit"] = fit.Name
        helpers[tool.Name] = mine
        tool.Visibility = bool(values.get("keep_tool"))  # off: used up (shown under the Boolean in the tree)
    f.Type = _TYPE.get(op, "Cut")
    show_result(f)
    f.Group = group
    for tool_name, old in old_helpers.items():  # drop helpers this setup no longer uses
        mine = helpers.get(tool_name, {})
        kept_fit = False
        for key in ("grown", "fit"):
            if old.get(key) and old.get(key) != mine.get(key):
                kept_fit = (not _remove(doc, old[key])) or kept_fit
        if old.get("fit_shape") and old.get("fit_shape") != mine.get("fit_shape") and not kept_fit and \
                doc.getObject(old["fit_shape"]) is not None:
            doc.removeObject(old["fit_shape"])
    try:
        _check_helpers(doc, helpers, tools)
    except ValueError as e:
        if clearance <= 0:
            raise
        _drop_attempt(doc, f, old_helpers, helpers, data.get("clearance_param"), name)
        raise _NoClearance(str(e).split(" (")[0].replace("Couldn't", "couldn't"))
    _store(f, {"base": base.Name, "tools": [t.Name for t in tools], "operation": op, "apply_to": apply_to,
               "clearance_param": name or data.get("clearance_param"),
               "clearance": values.get("clearance") or "0.2 mm", "helpers": helpers,
               "keep_tool": bool(values.get("keep_tool")), "clearance_fallback": fallback})
    return f


def _drop_attempt(doc, f, old_helpers, helpers, old_param, param):
    """What a failed clearance made goes again: its new offsets and fit copies, and a parameter made for it."""
    f.Group = []
    kept = {n for mine in old_helpers.values() for n in mine.values()}
    for mine in helpers.values():
        for key in ("fit", "grown", "fit_shape"):
            n = mine.get(key)
            if n and n not in kept and doc.getObject(n) is not None:
                obj = doc.getObject(n)
                if obj.TypeId == "PartDesign::Body":
                    obj.BaseFeature = None
                    obj.removeObjectsFromDocument()
                    doc.removeObject(n)
                else:
                    _remove_offset(doc, n)
    if param and param != old_param:
        from incad.core import model
        try:
            model.remove_model_parameter(doc, param)
        except ValueError:
            pass


def delete(doc, f):
    """Remove a Combine with its helper offsets and fit copies; the tools stay (shown again)."""
    data = _load(f)
    body = f.getParentGeoFeatureGroup()
    if body is not None:
        if body.Tip is f:
            body.Tip = f.BaseFeature
        body.removeObject(f)
    f.Group = []
    doc.removeObject(f.Name)
    for tool_name, helpers in data.get("helpers", {}).items():
        kept_fit = False
        for key in ("grown", "fit"):
            if helpers.get(key):
                kept_fit = (not _remove(doc, helpers[key])) or kept_fit
        if helpers.get("fit_shape") and not kept_fit and doc.getObject(helpers["fit_shape"]) is not None:
            doc.removeObject(helpers["fit_shape"])  # a kept fit copy still stands on it
        tool = doc.getObject(tool_name)
        if tool is not None and tool.TypeId == "PartDesign::Body":
            tool.Visibility = True


def _check_helpers(doc, helpers, tools):
    """Part::Offset can fail on complex shapes (threads): say so instead of a silently wrong result."""
    doc.recompute()
    for tool in tools:
        mine = helpers.get(tool.Name, {})
        for key, bigger in (("grown", True), ("fit_shape", False)):
            obj = doc.getObject(mine[key]) if mine.get(key) else None
            if obj is None:
                continue
            if _is_grown(obj):  # compared with the piece it grew
                volume = getattr(obj.Proxy, "piece_volume", 0.0)
            else:
                volume = tool.Shape.Volume if not tool.Shape.isNull() else 0.0
            def good():
                shape = obj.Shape
                return not shape.isNull() and shape.isValid() and shape.Volume > 1e-6 and \
                    ((shape.Volume > volume) if bigger else (shape.Volume < volume))
            if not good() and getattr(obj, "Join", "") != "Arc":
                # sharp corners failed on a grown thread (the user's jar: the lid came out with 0 mm): round ones,
                # self-intersections removed, grow it right
                obj.Join = "Arc"
                obj.Intersection = True
                obj.touch()
                doc.recompute()
            if not good():
                raise ValueError("Couldn't %s %s by the clearance (threads and other complex shapes can't be "
                                 "offset): use Clearance 0 mm, or put the clearance on the thread itself"
                                 % ("grow" if bigger else "shrink", tool.Label))


def create(doc, body, profile, values):
    bases = _bodies(doc, values.get("base"))
    base = bases[0] if bases else None
    if base is None:
        raise ValueError("Click the base solid (the one that stays), then the tools")
    tools = _bodies(doc, values.get("tools"))
    if not tools:
        raise ValueError("Pick at least one other solid as a tool")
    if base in tools:
        raise ValueError("The base can't be a tool: pick the other solid(s) as tools")
    tip = base.Tip
    f = base.newObject("PartDesign::Boolean", "Combine")
    if hasattr(f, "FuzzyTolerance"):
        f.FuzzyTolerance = FUZZY
    try:
        _build(doc, f, base, values)
    except Exception:
        delete(doc, f)  # a refused Combine leaves nothing behind
        base.Tip = tip
        doc.recompute()
        raise
    base.Tip = f
    return f


def read(doc, f):
    from incad.core import model
    data = _load(f)
    name = data.get("clearance_param")
    clearance = model.parameter_text(doc, name) if name and name in [p[0] for p in model.feature_parameters(doc)] \
        else data.get("clearance", "0.2 mm")
    tools = [doc.getObject(n) for n in data.get("tools", [])]
    return {"base": data.get("base", f.getParentGeoFeatureGroup().Name),
            "tools": format_picks([(t, "") for t in tools if t is not None]),
            "operation": data.get("operation", "cut"), "apply_to": data.get("apply_to", "cavity"),
            "clearance": clearance, "keep_tool": bool(data.get("keep_tool", False))}


def update(doc, f, values):
    body = f.getParentGeoFeatureGroup()
    bases = _bodies(doc, values.get("base"))
    if bases and bases[0] is not body:
        raise ValueError("The base can't change on an existing Boolean: cancel and start Boolean on the other "
                         "solid")
    return _build(doc, f, body, values)


def show_result(f):
    """The Boolean drawn as its result: FreeCAD's first display mode, Group, draws its tool Solids instead — a lid whose
    last step was a Boolean showed nothing once the file was opened again (the user: 'there's no lid')."""
    try:
        vo = f.ViewObject
        if vo is not None and vo.DisplayMode == "Group":
            vo.DisplayMode = "Flat Lines"
    except Exception:
        pass


def show_results(doc):
    """Every InCad Boolean of the file drawn as its result (files saved before show_result)."""
    for f in doc.Objects:
        if f.TypeId == "PartDesign::Boolean" and _STORE in f.PropertiesList:
            show_result(f)


def upgrade(doc):
    """Booleans whose cavity was grown by a Part::Offset of the whole tool (0.15.0) get the near piece kept between
    changes (the user's jar lid: 8 s on every change to the jar). Returns their labels."""
    done = []
    for f in [o for o in doc.Objects if o.TypeId == "PartDesign::Boolean" and _STORE in o.PropertiesList]:
        grown = [doc.getObject(h.get("grown")) for h in _load(f).get("helpers", {}).values() if h.get("grown")]
        if not any(g is not None and g.TypeId == "Part::Offset" for g in grown):
            continue
        try:
            update(doc, f, read(doc, f))
            done.append(f.Label)
        except Exception as e:
            from incad.core import log
            log.logger().warning("Boolean %s kept its old clearance helper: %s", f.Label, e)
    if done:
        doc.recompute()
    return done


def used_up_tools(f):
    """The tools this Boolean uses up (Keep tool off; older Booleans had no setting and hid their tools)."""
    data = _load(f)
    if data.get("keep_tool"):
        return []
    return [t for t in (f.Document.getObject(n) for n in data.get("tools", [])) if t is not None]


def used_up_names(doc):
    """Names of every solid some Boolean uses up (one pass over the document)."""
    return {t.Name for f in doc.Objects if claims(f) for t in used_up_tools(f)}


def is_used_up(body):
    return body.Name in used_up_names(body.Document)


def param_names(f):
    name = clearance_parameter(f)
    return {"clearance": name} if name else {}


def _pad_of(f):
    return f
