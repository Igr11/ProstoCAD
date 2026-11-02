"""InCad sketches: create on a plane or a flat face, status, named dimensions (d0, d1, …) and formulas that use
parameter and dimension names. Pure model logic: no Qt, no GUI."""
import math
import re

import FreeCAD as App

from incad.core import document, safe

DIMENSIONAL = ("Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle")
_DNAME = re.compile(r"^d(\d+)$")
PATTERN_PREFIX = "pat"
_PATNAME = re.compile(r"^pat\d+_\d+$")


def is_pattern_name(name):
    """Constraints a sketch pattern added (pat<n>_<i>): never dimensions, never symbols, never auto-named."""
    return bool(name) and _PATNAME.match(name) is not None
_TOKEN = re.compile(r"(?<![\w.<>])([A-Za-z_][A-Za-z0-9_]*)(?![\w(.])")


def is_sketch(obj):
    return obj is not None and getattr(obj, "TypeId", "") == "Sketcher::SketchObject"


def valid_support(obj, sub=""):
    """True for an origin plane, a datum plane or a flat face."""
    if obj is None:
        return False
    if obj.TypeId in ("App::Plane", "PartDesign::Plane"):
        return True
    if sub.startswith("Face"):
        try:
            face = obj.getSubObject(sub)
        except Exception:
            return False
        return face is not None and face.Surface.TypeId == "Part::GeomPlane"
    return False


def is_open_contour(sk):
    """A sketch with normal geometry whose wires are all open (a path, a guide): shown in the part view."""
    try:
        wires = sk.Shape.Wires
        return bool(sk.Shape.Edges) and bool(wires) and not any(w.isClosed() for w in wires)
    except Exception:
        return False


def key_points(sk):
    """[(kind, global Vector)] the view marks on a sketch: a spline's poles ('pole') and the points it passes
    through ('end'), a line's or arc's ends and
    a point ('end'), a circle's or ellipse's centre ('centre'). Construction geometry and every projected edge count
    like normal geometry (the user's law: equal rights; only a solid's profile leaves construction out); points within
    1e-6 of an earlier one are merged (the first kind wins)."""
    pl = sk.getGlobalPlacement()
    out = []

    def add(kind, p):
        g = pl.multVec(p)
        if not any((g - q).Length < 1e-6 for _, q in out):
            out.append((kind, g))

    normal = list(sk.Geometry) + list(getattr(sk, "ExternalGeo", []) or [])[2:]  # not the H / V axes
    for geo in normal:
        tid = geo.TypeId
        if tid == "Part::GeomBSplineCurve":
            for p in geo.getPoles():
                add("pole", p)
            for k in geo.getKnots():  # the points the line passes through (a spline drawn through points)
                add("end", geo.value(k))
        elif tid in ("Part::GeomCircle", "Part::GeomEllipse"):
            add("centre", geo.Center)
        elif tid == "Part::GeomPoint":
            add("end", App.Vector(geo.X, geo.Y, geo.Z))
        elif hasattr(geo, "StartPoint") and hasattr(geo, "EndPoint"):
            add("end", geo.StartPoint)
            add("end", geo.EndPoint)
    return out


def _next_label(doc, stem):
    used = {o.Label for o in doc.Objects}
    n = 1
    while "%s%d" % (stem, n) in used:
        n += 1
    return "%s%d" % (stem, n)


def _as_geometry(edge):
    """A flat edge in sketch coordinates as sketch geometry (a line, circle, arc, ellipse or B-spline); None for a
    point (an edge seen end on)."""
    import Part
    if edge.Length < 1e-7:
        return None
    c = edge.Curve
    if isinstance(c, (Part.Line, Part.LineSegment)):
        return Part.LineSegment(edge.Vertexes[0].Point, edge.Vertexes[-1].Point)
    if isinstance(c, Part.Circle):
        return c.copy() if edge.isClosed() else Part.ArcOfCircle(c, edge.FirstParameter, edge.LastParameter)
    if isinstance(c, Part.Ellipse):
        return c.copy() if edge.isClosed() else Part.ArcOfEllipse(c, edge.FirstParameter, edge.LastParameter)
    return c.toBSpline(edge.FirstParameter, edge.LastParameter)


def copy_edges(sk, edges):
    """Edges of another Solid drawn into the sketch as its own geometry (FreeCAD links projected edges only from
    the sketch's own Solid; the user: nothing of the lid could be projected into a sketch of the jar): each edge
    projected along the sketch's normal onto its plane. They don't follow the other Solid's changes. Returns the
    new geometry ids."""
    import Part
    to_sketch = sk.getGlobalPlacement().inverse().toMatrix()
    plane = Part.makePlane(2e4, 2e4, App.Vector(-1e4, -1e4, 0))
    ids = []
    for e in edges:
        local = e.copy()
        local.transformShape(to_sketch)
        try:
            flat = plane.makeParallelProjection(local, App.Vector(0, 0, 1))
        except Exception:
            continue
        for f in flat.Edges:
            g = _as_geometry(f)
            if g is not None:
                ids.append(sk.addGeometry(g, False))
    return ids


def make_sketch(body, support, sub=""):
    """A sketch in `body`, attached to `support` (+ face name), placed after End of Part."""
    if not valid_support(support, sub):
        raise ValueError("Pick a plane or a flat face to sketch on")
    sk = body.newObject("Sketcher::SketchObject", "Sketch")
    sk.AttachmentSupport = [(support, sub)]
    sk.MapMode = "FlatFace"
    sk.Label = _next_label(body.Document, "Sketch")
    body.Document.recompute([sk])  # it alone: nothing uses it yet (a full recompute retried every broken feature)
    return sk


def status(sk):
    return {"dof": sk.DoF, "fully": bool(sk.FullyConstrained), "conflicting": list(sk.ConflictingConstraints),
            "redundant": list(sk.RedundantConstraints), "malformed": list(sk.MalformedConstraints)}


def _sketches(doc):
    return [o for o in doc.Objects if is_sketch(o)]


def dimensions(doc):
    """[(name, sketch, index)] for every named dimension, in document order."""
    out = []
    for sk in _sketches(doc):
        for i, c in enumerate(sk.Constraints):
            if c.Type in DIMENSIONAL and c.Name and not is_pattern_name(c.Name):
                out.append((c.Name, sk, i))
    return out


def find_dimension(doc, name):
    for n, sk, i in dimensions(doc):
        if n == name:
            return sk, i
    return None, None


def _used_names(doc):
    from incad.core import model
    return model.used_names(doc)


NEXT_KEY = "InCadNextDimension"


def _next_number(doc):
    """Never reuse a dN: a counter in the file (doc.Meta), so a deleted d5 does not come back for a new dimension."""
    numbers = [int(m.group(1)) for m in (_DNAME.match(n) for n in _used_names(doc)) if m]
    try:
        stored = int(doc.Meta.get(NEXT_KEY, "0"))
    except ValueError:
        stored = 0
    return max([stored] + [n + 1 for n in numbers])


def name_new_dimensions(doc):
    """Give every unnamed driving dimension the next dN (never reused; see model.claim_name)."""
    from incad.core import model
    added = []
    for sk in _sketches(doc):
        for i, c in enumerate(sk.Constraints):
            if c.Type in DIMENSIONAL and not c.Name and c.Driving:
                name = model.claim_name(doc)
                sk.renameConstraint(i, name)
                added.append(name)
    return added


def to_expression(doc, text):
    from incad.core import model
    return model.to_expression(doc, text)


def to_display(doc, expr):
    from incad.core import model
    return model.to_display(doc, expr)


def _expression_of(obj, path):
    for p, e in obj.ExpressionEngine:
        if p.lstrip(".") == path:
            return e
    return ""


def dimension_parameters(doc):
    """[(name, formula with plain names or '', value text)] for every named dimension."""
    rows = []
    for name, sk, _ in dimensions(doc):
        expr = _expression_of(sk, "Constraints." + name)
        rows.append((name, to_display(doc, expr) if expr else "", document._format(sk.getDatum(name))))
    return rows


def set_dimension(doc, name, text, everything=True):
    """Set a named dimension to a value ('40 mm', '12') or a formula ('d3*2', 'Width/2').
    Call inside safe.run_safely: a failing formula raises and must be rolled back."""
    sk, index = find_dimension(doc, name)
    if sk is None:
        raise ValueError("No dimension named %r" % name)
    text = (text or "").strip()
    if not text:
        raise ValueError("%s needs a value" % name)
    path = "Constraints." + name
    from incad.core import overall_dims
    held = overall_dims.hold_radii(sk, index)  # an overall length: the slot gets longer, not wider
    try:
        _set_value(doc, sk, index, name, path, text, everything and not held)  # the part once, below
    finally:
        overall_dims.release(sk, held)
    if held:
        doc.recompute() if everything else doc.recompute([sk])


def _set_value(doc, sk, index, name, path, text, everything):
    try:
        quantity = App.Units.Quantity(text)
    except Exception:
        quantity = None
    if quantity is not None:
        if quantity.Unit == App.Units.Quantity("1").Unit:  # a plain number: the part's length unit or degrees
            unit = "deg" if sk.Constraints[index].Type == "Angle" else (document.part_units(doc) or "mm")
            quantity = App.Units.Quantity("%s %s" % (quantity.Value, unit))
        sk.setExpression(path, None)
        try:
            sk.setDatum(name, quantity)
        except Exception as e:
            raise ValueError("%s cannot be %s: %s" % (name, text, e))
    else:
        try:
            from incad.core import model
            unit = "deg" if sk.Constraints[index].Type == "Angle" else (document.part_units(doc) or "mm")
            sk.setExpression(path, to_expression(doc, model.with_units(text, unit)))
        except Exception as e:
            from incad.core import model
            raise ValueError("%s: %s" % (name, model.friendly(str(e))))
    doc.recompute() if everything else doc.recompute([sk])  # the open sketch alone: the part follows on Finish
    if "Invalid" in sk.State or status(sk)["conflicting"]:
        from incad.core import model
        raise ValueError("%s = %s cannot be satisfied: %s" % (name, text, model.friendly(sk.getStatusString())))


def drop_redundant(sk):
    """Take off the constraints the sketch has twice (FreeCAD's own pick), until it solves; the status line says
    which. Returns the types taken off."""
    from incad.core import log
    gone = []
    for _ in range(4):
        if sk.solve() != -2:
            break
        before = [c.Type for c in sk.Constraints]
        sk.autoRemoveRedundants(True)
        after = [c.Type for c in sk.Constraints]
        if len(after) == len(before):
            break
        left = list(before)
        for t in after:
            left.remove(t)
        gone += left
    if gone:
        log.report("info", "Took off what the sketch had already: %s" % ", ".join(gone))
    return gone


IMPLIED = ("Horizontal", "Vertical", "Parallel", "Perpendicular", "Equal")  # ties a newer one may make redundant
TIES = IMPLIED + ("Coincident", "PointOnObject", "Tangent", "Symmetric")  # what the constraint tools add


def redundant(sk):
    """0-based indexes of the constraints FreeCAD finds redundant (none when the sketch solves)."""
    return [i - 1 for i in sk.RedundantConstraints] if sk.solve() == -2 else []


def make_room(sk, new):
    """The constraints `new` (indexes a tool just added, at the end) repeat what older ones tie already (a slot's arc
    centres made symmetric about an axis while its line is Horizontal): the sketch stops solving, FreeCAD naming any
    of the ties that repeat each other. The older ties of the IMPLIED kinds that the new ones cover go instead (the
    named ones first, then newest first); when that is not enough, FreeCAD's own pick goes (drop_redundant).
    Returns the types taken off."""
    from incad.core import log
    red = redundant(sk)
    if not red:
        return []
    first = min(new)
    added = [sk.Constraints[i].Type for i in new]
    gone = []
    while red:
        older = [i for i in range(first) if sk.Constraints[i].Type in IMPLIED]
        older.sort(key=lambda i: (i not in red, -i))
        if not older:
            break
        i = older[0]
        c = sk.Constraints[i]
        sk.delConstraint(i)
        new = [n - 1 for n in new]  # the tool's ties move down by one
        first = min(new)
        left = redundant(sk)
        if len(left) < len(red):
            gone.append(c.Type)
            red = left
        else:
            sk.addConstraint(c)  # needed after all: back at the end, past the tool's (never tried again)
            red = redundant(sk)
    if gone:
        log.report("info", "%s covers what %s tied: took %s off" % (
            ", ".join(sorted(set(added))), ", ".join(gone), "it" if len(gone) == 1 else "them"))
    if red:
        gone += drop_redundant(sk)
    return gone


def rename_dimension(doc, old, new):
    sk, index = find_dimension(doc, old)
    if sk is None:
        raise ValueError("No dimension named %r" % old)
    if not document._NAME_RE.match(new or ""):
        raise ValueError("A name starts with a letter or _ and has only letters, digits and _ (got %r)" % new)
    if document._is_literal(new):
        raise ValueError("%r is a unit symbol; choose another name" % new)
    if new != old and new in _used_names(doc):
        raise ValueError("%r is already used" % new)
    sk.renameConstraint(index, new)
    doc.recompute()


def model_parameters(doc):
    """Dimensions and feature values (see incad.core.model)."""
    from incad.core import model
    return model.model_parameters(doc)


def _symbol_constraints(sk):
    return [i for i, c in enumerate(sk.Constraints) if c.Type not in DIMENSIONAL and not is_pattern_name(c.Name)]


def constraints_hidden(sk):
    """Are the constraint symbols hidden? (dimensions always stay visible, as in Inventor)"""
    ids = _symbol_constraints(sk)
    return bool(ids) and all(sk.getVirtualSpace(i) for i in ids)


def set_constraints_hidden(sk, hide):
    """Hide/show constraint symbols by moving them to FreeCAD's virtual space; dimensions stay."""
    ids = _symbol_constraints(sk)
    if ids:
        sk.setVirtualSpace(ids, bool(hide))


def _joined(c):
    """The two geometries a constraint joins end to end (coincident ends, or a tangent join at the ends)."""
    if c.First < 0 or c.Second < 0 or c.FirstPos == 0 or c.SecondPos == 0:
        return None
    if c.Type == "Coincident" or c.Type == "Tangent":
        return c.First, c.Second
    return None


def toggle_construction(sk, geo_ids):
    """Inventor's Construction on picked edges: those edges (only them: one side of a rectangle becomes an axis, the
    user) become construction, or normal again when all of them already are construction.
    A projected (external) edge switches FreeCAD's own 'defining' flag; the choice of construction is remembered
    (CHOSEN_PROP) so adopt_projections leaves it. Returns the ids changed."""
    flags = _projected_flags(sk)
    external = [i for i in geo_ids if i in flags]
    geo_ids = [i for i in geo_ids if 0 <= i < sk.GeometryCount]
    states = [sk.getConstruction(i) for i in geo_ids] + [not flags[i][0] for i in external]
    if not states:
        return []
    make = not all(states)
    ids = sorted(set(geo_ids))
    for i in ids:
        if sk.getConstruction(i) != make:
            sk.setConstruction(i, make)
    changed = set_projected_construction(sk, external, make)
    safe.recompute(sk.Document)  # the open sketch only: the part follows on Finish
    return ids + changed


# ----- projected (external) edges -----
CHOSEN_PROP = "InCadProjConstruction"  # Refs of projected edges the user switched to construction


def _external_ext(geo):
    for x in geo.getExtensions():
        if "External" in type(x).__name__:
            return x
    return None


def _projected_flags(sk):
    """{geo id: (defining, Ref)} for the projected edges (external geometry minus the H/V axes and points), in
    order; sk.ExternalGeo read once."""
    try:
        geos = sk.ExternalGeo
    except Exception:
        return {}
    out = {}
    for i in range(2, len(geos)):
        if geos[i].TypeId == "Part::GeomPoint":
            continue
        x = _external_ext(geos[i])
        out[-i - 1] = (bool(x is not None and x.testFlag("Defining")), (x.Ref if x is not None else "") or "")
    return out


def projected(sk):
    """Geo ids (-3, -4, …) of the sketch's projected edges: its external geometry minus the H/V axes and points."""
    return list(_projected_flags(sk))


def defining_geometry(sk):
    """The projected edges that are normal geometry (Part geometries, sketch coordinates)."""
    flags = _projected_flags(sk)
    if not flags:
        return []
    geos = sk.ExternalGeo
    return [geos[-g - 1] for g, (on, _ref) in flags.items() if on]


def is_defining(sk, geo_id):
    """Is the projected edge normal geometry (part of the profile)? FreeCAD's getConstruction says True for every
    external edge, so it is never asked."""
    try:
        x = _external_ext(sk.ExternalGeo[-geo_id - 1])
    except Exception:
        return False
    return bool(x is not None and x.testFlag("Defining"))


def set_defining(sk, geo_ids, on):
    """Projected edges become normal (on) or construction; returns the ids changed. The caller recomputes."""
    geos = sk.ExternalGeo
    changed = []
    for g in geo_ids:
        i = -g - 1
        if g > -3 or i >= len(geos):
            continue
        x = _external_ext(geos[i])
        if x is None or bool(x.testFlag("Defining")) == bool(on):
            continue
        x.setFlag("Defining", bool(on))
        geos[i].setExtension(x)
        changed.append(g)
    if changed:
        sk.ExternalGeo = geos
    return changed


def set_projected_construction(sk, geo_ids, construction):
    """The user's choice for projected edges: construction (remembered in CHOSEN_PROP, so adopt_projections leaves
    them) or normal again. Returns the ids changed; the caller recomputes."""
    flags = _projected_flags(sk)
    changed = set_defining(sk, geo_ids, not construction)
    refs = {flags[g][1] for g in geo_ids if g in flags} - {""}
    if refs:
        before = _strings(sk, CHOSEN_PROP)
        after = (before | refs) if construction else (before - refs)
        if after != before:
            _set_strings(sk, CHOSEN_PROP, after, "Projected edges switched to construction by the user")
    return changed


def _strings(sk, prop):
    return set(getattr(sk, prop, None) or [])


def _set_strings(sk, prop, refs, doc):
    if prop not in sk.PropertiesList:
        sk.addProperty("App::PropertyStringList", prop, "InCad", doc, 0, False, True)
    setattr(sk, prop, sorted(refs))


def users(sk):
    """What is built on the sketch: the features that consume it (features.common.consumers: a Profile, a Sweep's
    path or a Loft's section, a Split's knife, the features on its areas), a revolve whose axis is one of its lines,
    and its InCad area profiles that a feature uses (separate sketches that name their source only in their data;
    one left over from a cancelled dialog used nothing, yet kept the sketch's projected edges construction). An
    expression using one of its dimensions, a sketch attached to it or projecting from it does not make it used."""
    from incad.core import regions
    from incad.features import common
    out = list(common.consumers(sk))
    for o in sk.InList:
        axis = getattr(o, "ReferenceAxis", None) if "ReferenceAxis" in getattr(o, "PropertiesList", ()) else None
        if axis and axis[0] is sk and o not in out:
            out.append(o)
    return out + [p for p in regions.profiles_of(sk) if p not in out and regions.users(p)]


def pending_projections(sk):
    """Refs of the projected edges that are construction without being the user's choice: in a used sketch, the
    ones adopt_projections leaves for the user."""
    chosen = _strings(sk, CHOSEN_PROP)
    return {ref for on, ref in _projected_flags(sk).values() if not on and ref not in chosen}


def adopt_projections(sk, notes=None):
    """Inventor: projected edges are normal geometry. In a sketch NOTHING is built on yet (see users), every
    projected edge becomes defining unless the user switched it to construction (CHOSEN_PROP), and the old
    Construction button's tied normal copies are removed, their projected edges taking their place. A used sketch
    is left exactly as it is, so no feature or area changes behind the user's back: its new projections stay
    construction and `notes` (a list) gets one line saying so; once nothing uses the sketch, the next call adopts.
    One burst of changes at most, no trial recomputes; a second call does nothing. True when it changed something
    (then it has recomputed). Makes no undo step. Never inside an open undo step (a feature dialog's, a Sketcher
    tool's) and never while the document restores or recomputes (the GUI's progress bar runs deferred handlers
    mid-recompute): the next call adopts."""
    doc = sk.Document
    if getattr(doc, "Restoring", False) or getattr(doc, "Recomputing", False) or doc.HasPendingTransaction:
        return False
    flags = _projected_flags(sk)
    if not flags:
        return False
    chosen = _strings(sk, CHOSEN_PROP)
    todo = [g for g, (on, ref) in flags.items() if not on and ref not in chosen]
    if not todo:
        return False
    used = users(sk)
    if used:
        if notes is not None:
            notes.append("%s is used by %s: projected edges stay construction — Construction makes them normal" % (
                sk.Label, ", ".join(o.Label for o in used[:3]) + (" …" if len(used) > 3 else "")))
        return False
    copies = _old_copies(sk)
    if copies:
        construction = {ext for copy, ext in copies.items() if sk.getConstruction(copy)}  # a copy switched back
        refs = {flags[g][1] for g in construction} - {""}
        lost = _remove_copies(sk, copies)
        if refs - chosen:
            chosen |= refs
            _set_strings(sk, CHOSEN_PROP, chosen, "Projected edges switched to construction by the user")
        todo = [g for g in todo if g not in construction]
        if lost and notes is not None:
            notes.append("%s: %s on the old copies of projected edges could not move to the projected edges" % (
                sk.Label, ", ".join(lost)))
    set_defining(sk, todo, True)
    safe.recompute(doc)
    return True


def _same_curve(a, b, tol=1e-6):
    import Part
    if a.TypeId != b.TypeId:
        return False
    try:
        ea, eb = a.toShape(), b.toShape()
        if abs(ea.Length - eb.Length) > tol:
            return False
        p0, p1 = ea.FirstParameter, ea.LastParameter
        return all(eb.distToShape(Part.Vertex(ea.valueAt(p0 + (p1 - p0) * k / 4.0)))[0] < tol for k in range(5))
    except Exception:
        return False


def _old_copies(sk):
    """{copy index: projected geo id} for the normal copies the old Construction button put on projected edges:
    the same curve, tied to its ends (centre for a circle or ellipse), or Blocked on a closed spline."""
    out = {}
    ids = projected(sk)
    if not ids:
        return out
    geos = sk.ExternalGeo
    for ext in ids:
        for i in _copies_of_external(sk, ext):
            if i not in out and _same_curve(sk.Geometry[i], geos[-ext - 1]):
                out[i] = ext
    blocked = {c.First for c in sk.Constraints if c.Type == "Block" and c.First >= 0}
    for i in sorted(blocked - set(out)):
        g = sk.Geometry[i]
        if not getattr(g, "isPeriodic", lambda: False)():
            continue
        for ext in ids:
            if _same_curve(g, geos[-ext - 1]):
                out[i] = ext
                break
    return out


def _remove_copies(sk, copies):
    """Delete the copies; constraints joining other geometry to a copy move to its projected edge (same point
    numbers). The ties themselves go; a constraint left on projected geometry alone goes (FreeCAD refuses it).
    Returns the names (or kinds) of constraints that could not move."""
    gone = sorted(copies)
    lost = []

    def moved(g):
        if g in copies:
            return copies[g]
        if g >= 0:
            return g - sum(1 for c in gone if c < g)
        return g

    keep = []
    for c in sk.Constraints:
        refs = [g for g in (c.First, c.Second, c.Third) if g != GEO_UNDEF]
        if not any(g in copies for g in refs):
            continue
        if c.Type == "Block" or (c.Type == "Coincident" and
                                 (copies.get(c.First) == c.Second or copies.get(c.Second) == c.First)):
            continue  # the tie
        if all(moved(g) < 0 for g in refs):
            if c.Name or c.Type in DIMENSIONAL:
                lost.append(c.Name or c.Type)  # a dimension on the copy alone: FreeCAD refuses it on projections
            continue
        for field in ("First", "Second", "Third"):  # c is the sketch's copy (sk.Constraints copies): ours to change
            g = getattr(c, field)
            if g != GEO_UNDEF:
                setattr(c, field, moved(g))
        expr = dict(sk.ExpressionEngine).get(".Constraints.%s" % c.Name) if c.Name else None
        keep.append((c, c.Name, expr))
    for i in reversed(gone):
        sk.delGeometry(i)

    def key(c):
        ends = sorted([(c.First, c.FirstPos), (c.Second, c.SecondPos)]) if c.Type == "Coincident" else \
            [(c.First, c.FirstPos), (c.Second, c.SecondPos)]
        return (c.Type, tuple(ends), c.Third, c.ThirdPos)
    there = {key(c) for c in sk.Constraints}  # FreeCAD 1.1 moves some coincidences itself when a geometry goes
    for n, name, expr in keep:
        if key(n) in there:
            continue
        try:
            k = sk.addConstraint(n)
            if name and sk.Constraints[k].Name != name:
                sk.renameConstraint(k, name)
            if expr:
                sk.setExpression(".Constraints.%s" % name, expr)
        except Exception:
            lost.append(name or n.Type)
    return lost


def _copies_of_external(sk, geo_id):
    """Normal geometry tied by Coincident constraints to the projected edge at both ends or at the centre (what the
    old Construction button made; _old_copies also checks it is the same curve)."""
    tied = {}
    for c in sk.Constraints:
        if c.Type != "Coincident":
            continue
        if c.Second == geo_id and c.First >= 0:
            tied.setdefault(c.First, set()).add(c.FirstPos)
        elif c.First == geo_id and c.Second >= 0:
            tied.setdefault(c.Second, set()).add(c.SecondPos)
    return [i for i, ends in tied.items() if ends in ({1, 2}, {3})]


SNAP_TOL = 1e-6


def _polygon_circles(sk, first):
    """Construction circles from `first` on that carry a polygon's corners (points on them from line ends)."""
    found = []
    for i in range(first, sk.GeometryCount):
        if sk.Geometry[i].TypeId != "Part::GeomCircle" or not sk.getConstruction(i):
            continue
        corners = [c for c in sk.Constraints if c.Type == "PointOnObject" and c.Second == i and c.First >= 0]
        if len(corners) >= 3:
            found.append(i)
    return found


def pin_polygon_centres(sk, first=0):
    """Inventor remembers where a polygon's centre was snapped: on the origin it is coincident with it, on an axis
    it lies on that axis. Adds what FreeCAD's polygon tool left out (it can drop its first snap); returns the
    circles pinned."""
    import Sketcher
    pinned = []
    for i in _polygon_circles(sk, first):
        c = sk.Geometry[i].Center
        on_x, on_y = abs(c.y) < SNAP_TOL, abs(c.x) < SNAP_TOL
        ties = {(k.Type, k.Second if k.First == i else k.First) for k in sk.Constraints
                if (k.First == i and k.FirstPos == 3) or (k.Second == i and k.SecondPos == 3)}
        if ("Coincident", -1) in ties:
            continue
        if on_x and on_y:
            sk.addConstraint(Sketcher.Constraint("Coincident", i, 3, -1, 1))
        elif on_x and ("PointOnObject", -1) not in ties:
            sk.addConstraint(Sketcher.Constraint("PointOnObject", i, 3, -1))
        elif on_y and ("PointOnObject", -2) not in ties:
            sk.addConstraint(Sketcher.Constraint("PointOnObject", i, 3, -2))
        else:
            continue
        pinned.append(i)
    return pinned


def _rectangles(sk, first=0):
    """New rectangles: four non-construction line segments from index `first` on, each end tied (Coincident) to the
    next one's start in a closed loop, making a rectangle or a parallelogram. Returns [[(geo, pos) of each corner, in loop order]]."""
    geo = sk.Geometry  # once: each read copies every element
    lines = [i for i in range(first, len(geo)) if geo[i].TypeId == "Part::GeomLineSegment" and not sk.getConstruction(i)]
    known = set(lines)
    ends = {}  # (geo, pos) -> the (geo, pos) it is tied to
    for c in sk.Constraints:
        if c.Type == "Coincident" and c.First in known and c.Second in known:
            ends[(c.First, c.FirstPos)] = (c.Second, c.SecondPos)
            ends[(c.Second, c.SecondPos)] = (c.First, c.FirstPos)
    out, used = [], set()
    for start in lines:
        if start in used:
            continue
        loop, pos, geo = [], 1, start
        for _ in range(4):
            loop.append((geo, pos))
            nxt = ends.get((geo, 3 - pos))  # this line's other end, tied to the next line's corner
            if nxt is None:
                break
            geo, pos = nxt
        if len(loop) != 4 or (geo, pos) != (start, 1) or len({g for g, _p in loop}) != 4:
            continue
        if not _right_angled([sk.getPoint(g, p_) for g, p_ in loop]):
            continue  # a general quadrilateral: no centre point
        used |= {g for g, _p in loop}
        out.append(loop)
    return out


def _rounded_rectangles(sk, first=0):
    """New rounded rectangles: four non-construction arcs from index `first` on, of one radius, whose centres make a
    rectangle (FreeCAD's Rounded Rectangle). Returns [[(arc, 3) of each corner centre, in loop order]]."""
    import itertools
    import math
    geo = sk.Geometry  # once: each read copies every element
    arcs = [i for i in range(first, len(geo)) if geo[i].TypeId == "Part::GeomArcOfCircle" and not sk.getConstruction(i)]
    by_radius = {}
    for i in arcs:
        by_radius.setdefault(round(geo[i].Radius, 6), []).append(i)
    out, used = [], set()
    for group in by_radius.values():
        if len(group) < 4 or len(group) > 12:
            continue
        for four in itertools.combinations(group, 4):
            if used & set(four):
                continue
            centres = {i: sk.getPoint(i, 3) for i in four}
            mid = sum(centres.values(), centres[four[0]] * 0) * 0.25
            loop = sorted(four, key=lambda i: math.atan2(centres[i].y - mid.y, centres[i].x - mid.x))
            pts = [centres[i] for i in loop]
            if _right_angled(pts):
                used |= set(four)
                out.append([(i, 3) for i in loop])
    return out


def _right_angled(pts):
    """A parallelogram (FreeCAD's 3-Point Rectangle draws one from three corners; a rectangle is one too): opposite
    sides equal and opposite. Its diagonals cross in the middle, so the centre point works the same."""
    sides = [pts[(k + 1) % 4] - pts[k] for k in range(4)]
    size = max(v.Length for v in sides)
    if size < 1e-9 or min(v.Length for v in sides) < 1e-9:
        return False
    return (sides[0] + sides[2]).Length < 1e-6 * max(size, 1.0) and (sides[1] + sides[3]).Length < 1e-6 * max(size, 1.0)


def add_rectangle_centres(sk, first=0, done=None, ours=None):
    """The user: a new rectangle gets a construction point in its middle, so one constraint (that point on the origin)
    makes it symmetric. The point is Symmetric between two opposite corners (a Rounded Rectangle's: its corner arcs'
    centres), so it follows the rectangle; FreeCAD's Center Rectangle has its own, so it gets none. The diagonals
    show only while the rectangle is drawn (incad.ui.rectangle_preview): the user's choice, over construction
    diagonals kept in the sketch.
    done: a set of the rectangles handled (their elements' permanent geometry ids): each is handled once, so a point
    the user deletes stays deleted. All points come in one call and one solve (one at a time, FreeCAD solves the
    whole sketch after each).
    Returns the points added."""
    import Part
    import Sketcher
    from incad.core import prefs
    done = set() if done is None else done
    geo = sk.Geometry  # once: each read copies every element
    points = [(i, App.Vector(g.X, g.Y, g.Z)) for i, g in enumerate(geo) if g.TypeId == "Part::GeomPoint"]
    base = len(geo)
    new, ties, theirs = [], [], []
    diagonals = prefs.rectangle_diagonals()
    lines, ends = [], []  # ⚙ ▸ Rectangle Diagonals: (corner, corner) of each diagonal, added after the points
    for loop in _rectangles(sk, first) + _rounded_rectangles(sk, first):
        ident = frozenset(sk.getGeometryId(g) for g, _p in loop)
        if ident in done:
            continue
        done.add(ident)
        a, b = sk.getPoint(*loop[0]), sk.getPoint(*loop[2])
        if diagonals:
            for c1, c2 in ((loop[0], loop[2]), (loop[1], loop[3])):
                p1, p2 = sk.getPoint(*c1), sk.getPoint(*c2)
                if (p1 - p2).Length > 1e-9:
                    lines.append(Part.LineSegment(p1, p2))
                    ends.append((c1, c2))
        centre = (a + b) * 0.5
        there = next((i for i, p in points if _same(p, centre)), None)
        if there is not None:
            if there < base:
                theirs.append(there)  # a Center Rectangle's own: its Symmetric icon hides too
            continue
        points.append((base + len(new), centre))
        ties.append((loop[0], loop[2], base + len(new)))
        new.append(Part.Point(centre))
    if new or lines:
        sk.addGeometry(new + lines, True)
        first_line = base + len(new)
        cons = [Sketcher.Constraint("Symmetric", g1, p1, g2, p2, c, 1) for (g1, p1), (g2, p2), c in ties]
        for k, ((g1, p1), (g2, p2)) in enumerate(ends):
            cons += [Sketcher.Constraint("Coincident", first_line + k, 1, g1, p1),
                     Sketcher.Constraint("Coincident", first_line + k, 2, g2, p2)]
        sk.addConstraint(cons)
        sk.solve()  # a list added is not solved: the solver kept the sketch from before, and a drag lost the points
        if ours is not None:
            ours.update(sk.getGeometryId(base + k) for k in range(len(new)))
    hide_symmetric_icons(sk, theirs + [base + k for k in range(len(new))])
    return len(new)


def hide_symmetric_icons(sk, points):
    """FreeCAD draws a Symmetric constraint's icon on its middle point, where it took the clicks meant for the point
    (the user could not put a rectangle's centre point on an axis: the click picked the constraint). The Symmetric
    constraints whose middle is one of `points` go to virtual space: they still hold, their icon is not drawn."""
    points = set(points)
    hide = [n for n, c in enumerate(sk.Constraints)
            if c.Type == "Symmetric" and c.Third in points and c.ThirdPos == 1 and not sk.getVirtualSpace(n)]
    if hide:
        sk.setVirtualSpace(hide, True)
    return len(hide)


def drop_doubled_centres(sk, ours):
    """A centre point of ours that another point now shares (FreeCAD's Center Rectangle adds its own a moment after
    the lines) goes: the rectangle keeps one centre point. ours: our points' permanent geometry ids. Returns the
    number removed."""
    if not ours:
        return 0
    geo = sk.Geometry  # once: each read copies every element
    pts = [i for i, g in enumerate(geo) if g.TypeId == "Part::GeomPoint"]
    gone = [i for i in pts if sk.getGeometryId(i) in ours
            and any(j != i and sk.getGeometryId(j) not in ours and _same(sk.getPoint(j, 1), sk.getPoint(i, 1))
                    for j in pts)]
    for i in gone:
        ours.discard(sk.getGeometryId(i))
    if gone:  # the point that stays (the Center Rectangle's own) shows no icon either
        keep = [j for j in pts if sk.getGeometryId(j) not in ours and j not in gone
                and any(_same(sk.getPoint(j, 1), sk.getPoint(i, 1)) for i in gone)]
        hide_symmetric_icons(sk, keep)
        sk.delGeometries(gone)
    return len(gone)


def _same(a, b, tol=1e-6):
    """Two sketch points at one place (a distance: rounding can split two equal points across a boundary)."""
    return (a - b).Length < tol



GEO_UNDEF = -2000


def _datum_points(sk, c):
    """The two points a distance dimension spans (sketch coordinates), as FreeCAD gives its SoDatumLabel."""
    if c.Second == GEO_UNDEF:  # a line's length
        return sk.getPoint(c.First, 1), sk.getPoint(c.First, 2)
    if c.FirstPos and c.SecondPos:
        return sk.getPoint(c.First, c.FirstPos), sk.getPoint(c.Second, c.SecondPos)
    if c.FirstPos and not c.SecondPos:  # point to line: the point and its foot on the line
        p = sk.getPoint(c.First, c.FirstPos)
        a, b = sk.getPoint(c.Second, 1), sk.getPoint(c.Second, 2)
        d = b - a
        t = (p - a).dot(d) / d.dot(d) if d.Length > 1e-12 else 0.0
        return p, a + d * t
    return None


def label_point(sk, name):
    """Where FreeCAD draws the text of a dimension (global), as its SoDatumLabel does: the datum line is the two
    points moved by LabelDistance along the normal; the text sits at its middle, moved by LabelPosition along it.
    None for an unknown name or a kind not handled (angles)."""
    found = [c for c in sk.Constraints if c.Name == name]
    if not found:
        return None
    c = found[0]
    if c.Type in ("Distance", "DistanceX", "DistanceY"):
        pts = _datum_points(sk, c)
        if pts is None:
            return None
        p1, p2 = pts
        if c.Type == "DistanceX":
            d, n = App.Vector(1 if p2.x >= p1.x else -1, 0, 0), App.Vector(0, 1, 0)
        elif c.Type == "DistanceY":
            d, n = App.Vector(0, 1 if p2.y >= p1.y else -1, 0), App.Vector(-1, 0, 0)
        else:
            d = p2 - p1
            if d.Length < 1e-12:
                return None
            d.normalize()
            n = App.Vector(-d.y, d.x, 0)
        local = (p1 + p2) * 0.5 + n * c.LabelDistance + d * c.LabelPosition
    elif c.Type in ("Radius", "Diameter"):
        g = sk.Geometry[c.First]
        d = App.Vector(math.cos(c.LabelPosition), math.sin(c.LabelPosition), 0)
        local = g.Center + d * (g.Radius + c.LabelDistance)
    else:
        return None
    return sk.getGlobalPlacement().multVec(local)


def dimension_snapshot(sk):
    """{name: {type, value, expr}} of the named driving dimensions, taken after every change: the next change
    is compared with it (FreeCAD's Dimension tool replaces a dimension when it is placed)."""
    out = {}
    for c in sk.Constraints:
        if c.Name and c.Type in DIMENSIONAL and c.Driving and not is_pattern_name(c.Name):
            out[c.Name] = {"type": c.Type, "value": c.Value, "expr": _expression_of(sk, "Constraints." + c.Name)}
    return out


def vanished_dimensions(sk, known):
    names = {c.Name for c in sk.Constraints if c.Name}
    return [n for n in known if n not in names and not is_pattern_name(n)]


def _fresh_dimensions(sk):
    return [i for i, c in enumerate(sk.Constraints) if c.Type in DIMENSIONAL and not c.Name and c.Driving]


def reconcile_replaced(sk, known):
    """One named dimension gone and one unnamed appeared in the same change: it is the same dimension replaced
    (FreeCAD 1.1's Dimension tool does that at the placing click). It gets its name back and the value or formula
    the user gave it, in one undo step. Returns the name, or None when nothing was replaced."""
    gone, fresh = vanished_dimensions(sk, known), _fresh_dimensions(sk)
    if len(gone) != 1 or len(fresh) != 1:
        return None
    name, index, doc = gone[0], fresh[0], sk.Document
    was = known[name]
    # Never inside an undo step of our own: FreeCAD's Dimension tool keeps its own step open while the dimension
    # is placed, and opening another breaks its bookkeeping (it then re-adds the dimension on every update —
    # probed: a sketch full of copies). While that step is open only the name is put back; the value/formula
    # follows once the tool has committed (the next change).
    sk.renameConstraint(index, name)
    if doc.HasPendingTransaction:
        return name
    if was["expr"]:
        sk.setExpression("Constraints." + name, was["expr"])
    elif sk.Constraints[index].Type == was["type"] and abs(sk.Constraints[index].Value - was["value"]) > 1e-9:
        sk.setDatum(name, App.Units.Quantity(was["value"], "deg" if was["type"] == "Angle" else "mm"))
    safe.recompute(doc)
    return name
