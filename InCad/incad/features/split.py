"""Split by sketch: each line of a sketch (segment, arc, spline) is extruded along the sketch's normal, both ways,
through the whole part into a surface of zero thickness; these knives divide the shape before this step.
Keep all (Inventor's Split Solid): this Solid keeps the largest piece and every other piece becomes a Solid of its own
(a body on a SplitPiece that follows the Split), so each can be hidden, worked on and made a part of its own.
Keep picked: only the pieces at the stored points (a spot picked on each) stay, the nearest piece to each point on
every recompute.
Gap (a model parameter, 0.05 mm steps; the user: a gap between the pieces, from one of them or from both): each piece
loses its material within its share of the gap from the cut lines — Both: half from every piece; This Solid: all of
it from this Solid's piece; New Solids: all of it from the other pieces."""
import json

import FreeCAD as App

from incad.features.common import Field, parse_picks

TITLE = "Split"
ICON = "split"
TYPES = ("PartDesign::FeaturePython",)
NEEDS_PROFILE = False
STRUCTURAL = ()
PICKS_ON_SELF = ("keep",)
PRESELECT = {"sketch": "sketch"}  # a sketch selected before the command fills the field
_STORE = "InCadSplit"

FIELDS = (
    Field("sketch", "Sketch", "pick", pick="sketch"),
    Field("mode", "Mode", "choice", (("split", "Keep all"), ("cut", "Keep picked")), "split"),
    Field("keep", "Keep", "picks", pick="piece", show_if=("mode", ("cut",))),
    Field("gap", "Gap", "value_length", default="0 mm", step=lambda values: GAP_STEP),
    Field("gap_from", "Gap from", "choice", (("both", "Both"), ("this", "This Solid"), ("new", "New Solids")), "both",
          show_if=("mode", ("split",))),
    Field("assembly", "Make Assembly", "bool", default=False, show_if=("mode", ("split",))),
)
GAP_STEP = 0.05  # mm: the Gap's arrows and wheel (the user: fits in 0.05 mm)


def after_ok(doc, f, values):
    """Make Assembly ticked (the user: after a Split, an option to make an assembly): the pieces become part files and
    an assembly, through Make Assembly's own window (names, where they go)."""
    if not values.get("assembly") or values.get("mode", "split") != "split":
        return
    from incad import actions
    from incad.core import log
    try:
        actions.make_assembly()
    except Exception as e:
        log.report("warning", "Make Assembly: %s" % e)


def _knives(sk, body, bb):
    """The knife faces in the body's frame: every edge of the sketch swept along its normal past the part."""
    pl = sk.getGlobalPlacement()
    if body is not None:
        pl = body.getGlobalPlacement().inverse().multiply(pl)
    n = pl.Rotation.multVec(App.Vector(0, 0, 1))
    reach = bb.DiagonalLength + (pl.Base - bb.Center).Length + 10.0
    faces = []
    for e in sk.Shape.Edges:  # sk.Shape is already placed (body frame); pl only gives the normal
        edge = e.copy()
        edge.translate(n * -reach)
        faces.append(edge.extrude(n * (2 * reach)))
    return faces


def _slice(base, knives):
    from BOPTools import SplitAPI
    out = SplitAPI.slice(base, knives, "Split")
    return sorted(out.Solids, key=lambda s: -s.Volume)


def _slab(sk, body, bb, half):
    """Everything within `half` of the sketch's lines, through the part: each knife face grown both ways, and a
    round post at every end and bend of the lines (the user: a strange sliver at an L-shaped cut: the grown knives
    alone left a wedge of material uncovered outside the bend)."""
    import Part
    pl = sk.getGlobalPlacement()
    if body is not None:
        pl = body.getGlobalPlacement().inverse().multiply(pl)
    n = pl.Rotation.multVec(App.Vector(0, 0, 1))
    reach = bb.DiagonalLength + (pl.Base - bb.Center).Length + 10.0
    out = None
    for k in _knives(sk, body, bb):
        grown = k.makeOffsetShape(half, 1e-6, fill=True).fuse(k.makeOffsetShape(-half, 1e-6, fill=True))
        out = grown if out is None else out.fuse(grown)
    for v in sk.Shape.Vertexes:
        post = Part.makeCylinder(half, 2 * reach, v.Point - n * reach, n)
        out = post if out is None else out.fuse(post)
    return out


def _gapped(pieces, sk, body, bb, gap, gap_from, main):
    """The pieces with the gap taken off where they were cut: `main` (this Solid's piece) loses all of it (This
    Solid), none (New Solids) or half (Both), every other piece the rest."""
    if gap <= 0 or not pieces:
        return pieces
    share = {"this": (gap, 0.0), "new": (0.0, gap)}.get(gap_from, (gap / 2.0, gap / 2.0))
    slabs = {}
    out = []
    for p in pieces:
        amount = share[0] if p is main else share[1]
        if amount <= 0:
            out.append(p)
            continue
        if amount not in slabs:
            slabs[amount] = _slab(sk, body, bb, amount)
        left = p.cut(slabs[amount]).Solids
        if not left:
            raise ValueError("The gap takes a whole piece away: make it smaller")
        out.append(max(left, key=lambda s: s.Volume))
    return sorted(out, key=lambda s: -s.Volume)


def _nearest(solids, point):
    import Part
    probe = Part.Vertex(point)
    return min(solids, key=lambda s: s.distToShape(probe)[0])


def inside_point(solid):
    """A point strictly inside the solid: its centre of mass when that is inside (a C-shaped piece of a split ring
    has it in the hole), else a face's middle pushed a little into the material — never a point on a face that two
    split pieces share."""
    c = solid.CenterOfMass
    if solid.isInside(c, 1e-7, False):
        return c
    for f in solid.Faces:
        u0, u1, v0, v1 = f.ParameterRange
        p, n = f.valueAt((u0 + u1) / 2, (v0 + v1) / 2), f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
        for step in (1e-3, 1e-2, 0.1):
            for q in (p - n * step, p + n * step):
                if solid.isInside(q, 1e-7, False):
                    return q
    return c


class SplitFeature:
    """The proxy. FreeCAD saves its module path (incad.features.split) and class name in the file."""
    def __init__(self, obj):
        for prop, kind in (("Sketch", "App::PropertyLink"), ("Mode", "App::PropertyString"),
                           ("Keep", "App::PropertyVectorList"), ("ShowAll", "App::PropertyBool"),
                           ("Gap", "App::PropertyLength"), ("GapFrom", "App::PropertyString")):
            if prop not in obj.PropertiesList:
                obj.addProperty(kind, prop, "Split", "")
        obj.setPropertyStatus("ShowAll", ["Hidden", "Transient"])
        obj.Proxy = self

    def execute(self, obj):
        base = obj.BaseFeature
        if base is None or base.Shape.isNull() or obj.Sketch is None:
            return
        before = len(base.Shape.Solids)
        pieces = all_pieces(obj, before)
        import Part
        if obj.Keep and (obj.Mode == "split" or not obj.ShowAll):  # Keep all: this Solid's piece; Keep picked
            kept = []
            for p in obj.Keep:
                best = _nearest(pieces, p)
                if best not in kept:
                    kept.append(best)
            pieces = kept
        obj.Shape = pieces[0] if len(pieces) == 1 else Part.makeCompound(pieces)

    def dumps(self):
        return None

    def loads(self, state):
        return None


class SplitPieceFeature:
    """A piece of a Split (Keep all) that is not the Split's own: the base of a Solid of its own. Follows the Split
    (its sketch, the part before it): the piece nearest its point."""
    def __init__(self, obj):
        for prop, kind in (("Source", "App::PropertyLink"), ("Point", "App::PropertyVector")):
            if prop not in obj.PropertiesList:
                obj.addProperty(kind, prop, "Split", "")
        if _PIECE not in obj.PropertiesList:
            obj.addProperty("App::PropertyBool", _PIECE, "InCad", "A piece of a Split")
            obj.setPropertyStatus(_PIECE, "Hidden")
        setattr(obj, _PIECE, True)
        obj.Proxy = self

    def execute(self, obj):
        import Part
        f = obj.Source
        if f is None or f.BaseFeature is None or f.Sketch is None:
            return
        if not any(b.TypeId == "PartDesign::Body" and b.BaseFeature is obj for b in obj.InList):
            return  # its Solid was deleted by the user: nothing of it is shared out again
        mine, unclaimed = _assigned(f, obj)
        more = unclaimed and len(pieces(f)) > int(getattr(f, _COUNT, 0) or 0)  # a Solid the user deleted stays gone
        if mine is None or more:  # a Solid without a piece, or new pieces without a Solid: shared out again
            NEEDS_SYNC.add((f.Document.Name, f.Name))  # right after this recompute (incad.ui.shell)
        if mine is not None:
            obj.Shape = mine  # (none: left as it was until the Solid goes)

    def dumps(self):
        return None

    def loads(self, state):
        return None


_PIECE = "InCadSplitPiece"
_COUNT = "InCadSplitPieces"
NEEDS_SYNC = set()  # (document, Split) whose pieces and piece Solids no longer match: see sync_pending


def sync_pending():
    """Share the pieces out again for every Split a recompute found out of step (its knife sketch edited outside
    the dialog): a Solid left without a piece goes, a new piece gets a Solid. Its own undo step."""
    from incad.core import safe
    todo, done = sorted(NEEDS_SYNC), []
    NEEDS_SYNC.clear()
    for doc_name, name in todo:
        try:
            doc = App.getDocument(doc_name)
        except Exception:
            continue
        f = doc.getObject(name)
        if f is None or not claims(f):
            continue
        r = safe.run_safely("Split pieces", lambda d=doc, s=f: _sync_pieces(d, s), doc)
        if r.ok:
            done.append(f.Label)
    NEEDS_SYNC.clear()  # (the sync's own recompute is in step)
    return done


def _assigned(f, piece_obj):
    """(the piece of `piece_obj`, how many pieces have no Solid), shared out as the dialog does (_sync_pieces): the
    Split keeps the piece at its point, then each piece Solid in turn the free piece nearest its point. Each piece
    then sits in one Solid only: the knife sketch edited outside the dialog made a piece Solid take the Split's own
    piece (material twice) and lose another (the finder's repro: 9000 / 3000 / 9000 for 6000 / 3000 / 3000)."""
    all_ = pieces(f)
    main = _nearest(all_, f.Keep[0]) if f.Keep else all_[0]
    claimed, mine = [main], None
    for b in piece_bodies(f):
        point = getattr(b.BaseFeature, "Point", None)
        free = [p for p in all_ if p not in claimed]
        if point is None or not free:
            continue
        best = _nearest(free, point)
        claimed.append(best)
        if b.BaseFeature is piece_obj:
            mine = best
    return mine, len(all_) - len(claimed)


def claims(feature):
    return hasattr(feature, _STORE)


def piece_bodies(f):
    """The Solids the Split made for its other pieces (Keep all), in the document's order."""
    return [b for b in f.Document.Objects if b.TypeId == "PartDesign::Body" and b.BaseFeature is not None
            and getattr(b.BaseFeature, _PIECE, False) and b.BaseFeature.Source is f]


def _drop_piece_body(doc, body):
    """A piece's Solid goes with its piece, unless something was made on it or a Boolean uses it as a tool (then it
    stays)."""
    from incad.features import combine
    if any(combine.claims(f) and body.Name in combine._load(f).get("tools", []) for f in doc.Objects):
        return
    origin = [body.Origin] + list(body.Origin.OriginFeatures) if body.Origin is not None else []
    stand_in = [o for o in body.Group if o.TypeId == "PartDesign::FeatureBase"]  # FreeCAD's for BaseFeature
    if any(o not in origin and o not in stand_in for o in body.Group):
        return
    piece = body.BaseFeature
    body.BaseFeature = None
    for o in stand_in:
        if doc.getObject(o.Name) is not None:
            doc.removeObject(o.Name)
    doc.removeObject(body.Name)
    if piece is not None:
        doc.removeObject(piece.Name)


def is_piece(obj):
    return bool(getattr(obj, _PIECE, False))


def freeze_pieces(doc, f):
    """Every piece Solid of the Split keeps its piece as a plain shape (the Split is going away with its Solid)."""
    for b in piece_bodies(f):
        piece = b.BaseFeature
        frozen = doc.addObject("Part::Feature", "SplitPieceShape")
        frozen.Shape = piece.Shape.copy()
        frozen.Visibility = False
        b.BaseFeature = frozen
        doc.removeObject(piece.Name)


def delete(doc, f):
    """Delete a Split: its empty piece Solids go with it; a piece Solid with work on it stays on a frozen copy of
    its piece (a plain shape: nothing links to the deleted Split)."""
    for b in piece_bodies(f):
        before = len(doc.Objects)
        _drop_piece_body(doc, b)
        if len(doc.Objects) < before:
            continue
        piece = b.BaseFeature
        frozen = doc.addObject("Part::Feature", "SplitPieceShape")
        frozen.Shape = piece.Shape.copy()
        frozen.Visibility = False
        b.BaseFeature = frozen
        doc.removeObject(piece.Name)


def upgrade(doc):
    """A Split saved before Keep all gave each piece a Solid holds every piece in its own Solid: give the others
    their Solids now. Returns the Splits upgraded."""
    for o in doc.Objects:  # a piece shape left visible by an earlier version draws beside its Solid
        if is_piece(o) and o.Visibility:
            o.Visibility = False
    done = []
    for f in [o for o in doc.Objects if claims(o)]:
        if f.Mode == "split" and not f.Keep and not piece_bodies(f) and not f.Shape.isNull() \
                and len(f.Shape.Solids) > 1:
            _sync_pieces(doc, f)
            done.append(f)
    if done:
        doc.recompute()
    return done


def _new_piece_body(doc, f, point):
    from incad.core import document
    piece = doc.addObject("Part::FeaturePython", "SplitPiece")
    SplitPieceFeature(piece)
    piece.Source = f
    piece.Point = point
    piece.Visibility = False
    body = document.new_solid(doc)
    owner = f.getParentGeoFeatureGroup()
    if owner is not None:
        body.Placement = owner.Placement  # the piece is in the Split's body frame
    body.BaseFeature = piece
    piece.Visibility = False  # again: setting a Body's base can show it, and it would draw beside the Solid
    return body


def _sync_pieces(doc, f):
    """Keep all: this Solid keeps the largest piece (first time) and every other piece has a Solid; on an edit each
    Solid keeps the piece nearest its point, new pieces get one, Solids left without a piece go. Keep picked: no
    piece Solids."""
    bodies = piece_bodies(f)
    if f.Mode != "split":
        for b in bodies:
            _drop_piece_body(doc, b)
        return
    all_pieces = pieces(f)
    main = _nearest(all_pieces, f.Keep[0]) if f.Keep else all_pieces[0]
    f.Keep = [inside_point(main)]
    claimed = [main]
    for b in bodies:
        free = [p for p in all_pieces if p not in claimed]
        if not free:
            _drop_piece_body(doc, b)
            continue
        best = _nearest(free, b.BaseFeature.Point)
        b.BaseFeature.Point = inside_point(best)
        claimed.append(best)
    for p in all_pieces:
        if p not in claimed:
            _new_piece_body(doc, f, inside_point(p))
            claimed.append(p)
    if _COUNT not in f.PropertiesList:  # (see SplitPieceFeature.execute: only more pieces than this are new ones)
        f.addProperty("App::PropertyInteger", _COUNT, "InCad", "Pieces at the last share-out")
        f.setPropertyStatus(_COUNT, "Hidden")
    setattr(f, _COUNT, len(all_pieces))


def defaults(doc, body, profile=None):
    values = {f.key: f.default for f in FIELDS}
    values.update(sketch=profile.Name if profile is not None else "", keep="")
    return values


def _points(doc, text):
    """Keep: picks on the Split's own pieces ('Split:Face7') or plain 'x y z' triples; each becomes a point."""
    out = []
    for part in [p for p in (text or "").split(",") if p.strip()]:
        nums = part.split()
        if len(nums) == 3:
            try:
                out.append(App.Vector(*[float(v) for v in nums]))
                continue
            except ValueError:
                pass
        for obj, sub in parse_picks(doc, part):
            if sub:
                out.append(obj.Shape.getElement(sub).CenterOfMass)
    return out


def _store(f, data):
    if not hasattr(f, _STORE):
        f.addProperty("App::PropertyString", _STORE, "InCad", "InCad split settings")
        f.setPropertyStatus(_STORE, "Hidden")
    setattr(f, _STORE, json.dumps(data, sort_keys=True))


def _load(f):
    try:
        return json.loads(getattr(f, _STORE, "") or "{}")
    except ValueError:
        return {}


def _build(doc, f, values):
    sk = doc.getObject((values.get("sketch") or "").split(":")[0])
    if sk is None or sk.TypeId != "Sketcher::SketchObject":
        raise ValueError("Pick the sketch whose lines cut the part")
    if sk.Shape.isNull() or "Touched" in sk.State:
        sk.recompute()  # a sketch drawn just now has no shape yet
    if not sk.Shape.Edges:
        raise ValueError("The sketch has no lines")
    f.Sketch = sk
    _set_gap(doc, f, values)
    was = f.Mode
    f.Mode = values.get("mode") or "split"
    if f.Mode == "cut":
        f.Keep = _points(doc, values.get("keep"))
    elif was != "split":
        f.Keep = []  # Keep all: _sync_pieces gives this Solid the largest piece
    # the picks as typed ('Split:Face7', named on every piece shown while Keep is picked): edit reads them back, so a
    # click adds to them instead of replacing them
    _store(f, {"sketch": sk.Name, "mode": f.Mode, "keep": (values.get("keep") or "") if f.Mode == "cut" else "",
               "gap_from": f.GapFrom})
    return f


def _gap_mm(text):
    try:
        q = App.Units.Quantity((text or "0 mm").strip())
    except Exception:
        raise ValueError("Gap: enter a length such as 0.2 mm")
    if str(q.Unit) == str(App.Units.Quantity("1").Unit):  # a plain number: mm
        q = App.Units.Quantity("%s mm" % q.Value)
    if q.Value < 0:
        raise ValueError("Gap: 0 mm or more")
    return q.Value


def _set_gap(doc, f, values):
    """Gap: a model parameter (named in the Parameters list) once it is above 0; 0 with none keeps none."""
    from incad.core import model
    for prop, kind in (("Gap", "App::PropertyLength"), ("GapFrom", "App::PropertyString")):
        if prop not in f.PropertiesList:  # a Split saved before Gap
            f.addProperty(kind, prop, "Split", "")
    text = (values.get("gap") or "0 mm").strip()
    gap = _gap_mm(text)
    name = model.bound_parameter(f, "Gap")
    if name is None and gap > 0:
        name = model.new_feature_parameter(doc, text, "length")
        model.bind(f, "Gap", name)
    elif name is not None and model.parameter_text(doc, name) != text:
        model.set_model_parameter(doc, name, text, everything=False)
    elif name is None:
        f.Gap = 0
    gap_from = values.get("gap_from") or "both"
    if f.GapFrom != gap_from:
        f.GapFrom = gap_from


def _has_solid(b):
    try:
        return b is not None and b.Tip is not None and not b.Shape.isNull() and bool(b.Shape.Solids)
    except Exception:
        return False


def _target(doc, body, values):
    """The Solid the sketch splits: its own when that has a solid; else the one its lines cut through (the user: a
    sketch in the empty first Solid across the rebuilt STL — "'NoneType' object has no attribute 'Shape'"); the
    sketch goes into that Solid, as an Extrude's profile does for a New Solid."""
    if _has_solid(body):
        return body
    picks = parse_picks(doc, values.get("sketch"))
    sk = picks[0][0] if picks else None
    if sk is None or getattr(sk, "TypeId", "") != "Sketcher::SketchObject":
        return body
    from incad.core import document
    import Part
    found = []
    for b in document.bodies(doc):
        if b is body or not _has_solid(b):
            continue
        try:
            knives = Part.makeCompound(_knives(sk, b, b.Shape.BoundBox))
            if b.Shape.section(knives).Edges:
                found.append(b)
        except Exception:
            continue
    if not found:
        raise ValueError("The sketch's lines cut no Solid: draw them across the part")
    target = max(found, key=lambda b: (document.is_shown(b), b.Shape.Volume))
    owner = sk.getParentGeoFeatureGroup()
    if owner is not target:
        if owner is not None:
            owner.removeObject(sk)
        target.addObject(sk)
        document.settle_support(sk, target)
        doc.recompute()
    return target


def create(doc, body, profile, values):
    body = _target(doc, body, values)
    tip = body.Tip
    f = body.newObject("PartDesign::FeaturePython", "Split")
    SplitFeature(f)
    f.BaseFeature = tip
    from incad.core import document
    document.show_like(f, tip)  # the view proxy first: FreeCAD then draws it in the base's display mode
    try:
        _build(doc, f, values)
        body.Tip = f
        sk = f.Sketch
        doc.recompute()
        document.show_like(f, tip)
        if "Invalid" in f.State:
            raise ValueError(f.getStatusString() or "The sketch does not cut the part")
        _sync_pieces(doc, f)
        doc.recompute()
        sk.Visibility = False
    except Exception:
        for b in piece_bodies(f):
            _drop_piece_body(doc, b)
        body.removeObject(f)
        doc.removeObject(f.Name)
        body.Tip = tip
        doc.recompute()
        raise
    return f


def read(doc, f):
    data = _load(f)
    values = defaults(doc, f.getParentGeoFeatureGroup(), None)
    mode = data.get("mode", "split")
    keep = ""
    if mode == "cut":  # Keep all keeps its own piece's point in Keep: that is no pick of the user's
        keep = data.get("keep") or ", ".join("%r %r %r" % (p.x, p.y, p.z) for p in f.Keep)
    values.update(sketch=data.get("sketch", getattr(f.Sketch, "Name", "")), mode=mode, keep=keep)
    from incad.core import model
    name = model.bound_parameter(f, "Gap") if hasattr(f, "Gap") else None
    values["gap"] = model.parameter_text(doc, name) if name else "0 mm"
    values["gap_from"] = getattr(f, "GapFrom", "") or data.get("gap_from") or "both"
    return values


def update(doc, f, values):
    _build(doc, f, values)
    doc.recompute()
    if "Invalid" in f.State:
        raise ValueError(f.getStatusString() or "The sketch does not cut the part")
    _sync_pieces(doc, f)
    doc.recompute()
    return f


def all_pieces(f, before=None):
    """The pieces the sketch cuts the part before the Split into, the gap taken off (largest first)."""
    base = f.BaseFeature
    knives = _knives(f.Sketch, f.getParentGeoFeatureGroup(), base.Shape.BoundBox)
    cut = _slice(base.Shape, knives)
    if before is not None and len(cut) <= before:
        raise ValueError("The sketch does not cut the part")
    gap = f.Gap.Value if hasattr(f, "Gap") else 0.0  # a Split saved before Gap has none
    if gap <= 0:
        return cut
    main = cut[0]
    if f.Mode == "split" and f.Keep:
        main = _nearest(cut, f.Keep[0])
    side = (getattr(f, "GapFrom", "") or "both") if f.Mode == "split" else "both"  # Keep picked: no choice shown
    return _gapped(cut, f.Sketch, f.getParentGeoFeatureGroup(), base.Shape.BoundBox, gap, side, main)


def pieces(f):
    return all_pieces(f)


def keep_piece(f, point):
    f.Mode = "cut"
    f.Keep = [point]
    data = _load(f)
    data["mode"] = "cut"
    data["keep"] = "%r %r %r" % (point.x, point.y, point.z)
    _store(f, data)
    f.touch()


def picking(f, active):
    """While Keep is picked every piece shows (a removed one can be picked back)."""
    if f.ShowAll != active:
        f.ShowAll = active
        f.touch()


def param_names(f):
    from incad.core import model
    name = model.bound_parameter(f, "Gap") if hasattr(f, "Gap") else None
    return {"gap": name} if name else {}
