"""Hole (Inventor) = PartDesign Hole. Placement from a sketch's points/circle centres, or concentric to round edges
and round faces (a hole at the centre of each; a hidden helper sketch on the flat face holds the centre points)."""
import FreeCAD as App
import Part

from incad.features.common import Field, fine_step, parse_picks, resolve, value_param, value_text
from incad.threads import inserts

TITLE = "Hole"
ICON = "hole"
TYPES = ("PartDesign::Hole",)
NEEDS_PROFILE = False
PROPS = {"diameter": "Diameter", "depth": "Depth", "cbore_diameter": "HoleCutDiameter",
         "cbore_depth": "HoleCutDepth", "csink_diameter": "HoleCutDiameter", "csink_angle": "HoleCutCountersinkAngle",
         "spot_diameter": "HoleCutDiameter", "spot_depth": "HoleCutDepth"}
_THREADED = ("clearance", "tapped")
FIELDS = (
    Field("placement", "Placement", "choice", (("sketch", "From Sketch"), ("concentric", "Concentric")), "sketch"),
    Field("sketch", "Sketch", "pick", show_if=("placement", ("sketch",)), pick="sketch"),
    Field("edge", "Round", "picks", show_if=("placement", ("concentric",)), pick="round"),
    Field("face", "Face", "pick", show_if=("placement", ("concentric",)), pick="hole_face", optional=True),
    Field("hole_type", "Hole", "choice", (("simple", "Simple"), ("clearance", "Clearance"), ("tapped", "Tapped"),
                                          ("insert", "Insert")), "simple"),
    Field("insert_size", "Insert", "combo", inserts.choices(), inserts.DEFAULT, show_if=("hole_type", ("insert",))),
    Field("thread_standard", "Standard", "combo", (("iso", "ISO metric"), ("iso_fine", "ISO metric fine"),
                                                   ("unc", "UNC"), ("unf", "UNF"), ("unef", "UNEF"), ("npt", "NPT"),
                                                   ("g", "G / BSP"), ("bsw", "BSW"), ("bsf", "BSF")), "iso",
          show_if=("hole_type", _THREADED)),
    Field("thread_size", "Size", "combo", (), "", show_if=("hole_type", _THREADED)),
    Field("fit", "Fit", "choice", (("close", "Close"), ("normal", "Normal"), ("loose", "Loose")), "normal",
          show_if=("hole_type", ("clearance",))),
    Field("diameter", "Diameter", "value_length", default="5 mm", show_if=("hole_type", ("simple",))),
    Field("seat", "Seat", "combo", (("none", "None (drilled)"), ("counterbore", "Counterbore"),
                                    ("countersink", "Countersink"), ("spotface", "Spotface")), "none"),
    Field("cbore_diameter", "C'bore Ø", "value_length", default="10 mm", show_if=("seat", ("counterbore",))),
    Field("cbore_depth", "C'bore depth", "value_length", default="3 mm", show_if=("seat", ("counterbore",))),
    Field("csink_diameter", "C'sink Ø", "value_length", default="10 mm", show_if=("seat", ("countersink",))),
    Field("csink_angle", "C'sink angle", "value_angle", default="90 °", show_if=("seat", ("countersink",))),
    Field("spot_diameter", "Spotface Ø", "value_length", default="10 mm", show_if=("seat", ("spotface",))),
    Field("spot_depth", "Spotface depth", "value_length", default="0.5 mm", show_if=("seat", ("spotface",))),
    Field("depth_type", "Termination", "choice", (("distance", "Distance"), ("through_all", "Through All"),
                                                 ("to", "To")), "through_all"),
    Field("depth", "Depth", "value_length", default="10 mm",
          show_if=(("depth_type", ("distance",)), ("hole_type", ("simple", "clearance", "tapped")))),
    Field("to_face", "To face", "pick", pick="plane", show_if=("depth_type", ("to",))),
    Field("tip", "Drill point", "choice", (("flat", "Flat"), ("angled", "Angle")), "angled",
          show_if=(("depth_type", ("distance", "to")), ("hole_type", ("simple", "clearance", "tapped")))),
    Field("tip_angle", "Point angle", "value_angle", default="118 °",
          show_if=(("depth_type", ("distance", "to")), ("tip", ("angled",)),
                   ("hole_type", ("simple", "clearance", "tapped")))),
    Field("thread_depth", "Thread", "choice", (("hole", "Whole hole"), ("distance", "Depth")), "hole",
          show_if=("hole_type", ("tapped",))),
    Field("thread_length", "Thread depth", "value_length", default="8 mm",
          show_if=(("hole_type", ("tapped",)), ("thread_depth", ("distance",)))),
    Field("clearance", "Print clearance (on diameter)", "value_length", default="0.2 mm", show_if=("hole_type", ("tapped",))),
    Field("hand", "Direction", "choice", (("right", "Right hand"), ("left", "Left hand")), "right",
          show_if=("hole_type", ("tapped",))),
    Field("flip", "Flip direction", "bool", default=False),
    Field("chamfer", "Chamfer", "choice", (("none", "None"), ("start", "Start"), ("both", "Both ends")), "none"),
    Field("chamfer_size", "Chamfer size", "value_length", default="0.5 mm", show_if=("chamfer", ("start", "both")),
          step=fine_step),
)
_THREAD_TYPES = {"iso": "ISOMetricProfile", "iso_fine": "ISOMetricFineProfile", "unc": "UNC", "unf": "UNF",
                 "unef": "UNEF", "npt": "NPT", "g": "BSP", "bsw": "BSW", "bsf": "BSF"}
_FIT = {"close": "Fine", "normal": "Medium", "loose": "Coarse"}
_PLAIN = "InCadPlainDiameter"
_STORE = "InCadHole"
_SIZES = {}
_CUT = {"none": "None", "counterbore": "Counterbore", "countersink": "Countersink", "spotface": "Counterbore"}


def defaults(doc, body, profile):
    values = {f.key: f.default for f in FIELDS}
    values.update(sketch="", face="", edge="")
    try:
        from incad.core import prefs
        values["clearance"] = "%g mm" % prefs.thread_clearance()
    except Exception:
        pass
    return values


def thread_sizes(std):
    """FreeCAD's size names for a thread type (asked once from a hidden probe Hole, then cached)."""
    kind = _THREAD_TYPES.get(std, "ISOMetricProfile")
    if kind not in _SIZES:
        active = App.ActiveDocument.Name if App.ActiveDocument is not None else None
        doc = App.newDocument("InCadHoleSizes", hidden=True)
        try:
            h = doc.addObject("PartDesign::Hole", "Probe")
            h.ThreadType = kind
            _SIZES[kind] = list(h.getEnumerationsOfProperty("ThreadSize"))
        finally:
            App.closeDocument(doc.Name)
            if active is not None and active in App.listDocuments():
                App.setActiveDocument(active)  # closing the probe must not leave FreeCAD without a document
    return _SIZES[kind]


def options(key, values):
    if key == "thread_size":
        return [("", "Auto (from Diameter)")] + [(n, n) for n in thread_sizes(values.get("thread_standard") or "iso")]
    return None


def _default_size(std, names, diameter_text):
    from incad.threads import standards
    try:
        d = App.Units.Quantity(diameter_text or "5 mm").Value
        guess = standards.suggest(std if std in ("iso", "iso_fine", "unc", "unf", "npt", "g") else "iso", d, False)
    except Exception:
        guess = ""
    guess = guess.replace("G", "").split("-")[0] if std in ("g", "unc", "unf", "unef", "bsw", "bsf") else guess
    return next((n for n in names if n == guess or n.startswith(guess + "x")), names[0] if names else "")


def _round_centre(obj, sub):
    """(a point on the axis, unit axis) of a round edge or a round face (cylinder, cone), global."""
    el = obj.getSubObject(sub)
    if sub.startswith("Edge") and el is not None and el.Curve.TypeId == "Part::GeomCircle":
        point, axis = App.Vector(el.Curve.Center), App.Vector(el.Curve.Axis)
    elif sub.startswith("Face") and el is not None and el.Surface.TypeId in ("Part::GeomCylinder", "Part::GeomCone"):
        point, axis = App.Vector(el.Surface.Center), App.Vector(el.Surface.Axis)
    else:
        raise ValueError("%s is not a round edge or a round face" % sub)
    return point, axis.normalize()


def _start_face(obj, sub, axis):
    """The flat face the holes start on when none is picked: one square to the axis beside the first round edge or
    face; of two (a round face's both ends) the one that looks at the viewer, else the upper one."""
    shape = obj.Shape
    el = shape.getElement(sub)
    flats = [f for f in shape.ancestorsOfType(el, Part.Face) if f.Surface.TypeId == "Part::GeomPlane"] \
        if sub.startswith("Edge") else \
        [f for e in el.Edges for f in shape.ancestorsOfType(e, Part.Face) if f.Surface.TypeId == "Part::GeomPlane"]
    flats = [f for f in flats if abs(abs(f.normalAt(0, 0).dot(axis)) - 1) < 1e-6]
    if not flats:
        raise ValueError("Pick the flat face the holes start on")
    try:
        import FreeCADGui as Gui
        look = Gui.ActiveDocument.ActiveView.getViewDirection()
    except Exception:
        look = App.Vector(0, 0, -1)
    best = max(flats, key=lambda f: -f.normalAt(0, 0).dot(look))
    i = next(k for k, f in enumerate(shape.Faces, 1) if f.isSame(best))
    return obj, "Face%d" % i


def _helper_sketch(doc, body, values, sk=None):
    """A hidden sketch on the flat face with a point at each round edge's or face's centre (fixed positions). The
    picks are kept on the sketch (InCadPicks) so the dialog can show and change them; `sk` reuses an existing helper."""
    rounds = parse_picks(doc, values.get("edge"))
    if not rounds:
        raise ValueError("Pick the round edges or faces the holes are centred on")
    axes = [_round_centre(*resolve(o, s)) for o, s in rounds]
    faces = parse_picks(doc, values.get("face"))
    face_ref = resolve(*faces[0]) if faces else _start_face(*resolve(*rounds[0]), axes[0][1])
    if sk is None:
        sk = body.newObject("Sketcher::SketchObject", "HoleCentre")
        sk.addProperty("App::PropertyString", "InCadPicks", "InCad")
    sk.deleteAllGeometry()
    sk.AttachmentSupport = [face_ref]
    sk.MapMode = "FlatFace"
    sk.InCadPicks = "%s|%s" % (values.get("face"), values.get("edge"))
    doc.recompute()
    inv = sk.getGlobalPlacement().inverse()
    normal = sk.getGlobalPlacement().Rotation.multVec(App.Vector(0, 0, 1))
    placed = []
    for point, axis in axes:
        if abs(abs(axis.dot(normal)) - 1) > 1e-6:
            raise ValueError("A round edge or face is not square to the face the holes start on")
        local = inv.multVec(point)
        at = App.Vector(local.x, local.y, 0)
        if all((at - q).Length > 1e-6 for q in placed):  # an edge and its face: one hole
            placed.append(at)
            sk.addGeometry(Part.Point(at))
    sk.Visibility = False
    doc.recompute()
    return sk


def _profile(doc, body, values):
    if values.get("placement") == "concentric":
        return _helper_sketch(doc, body, values)
    picks = parse_picks(doc, values.get("sketch"))
    if not picks:
        raise ValueError("Pick a sketch with points or circles for the hole centres")
    return picks[0][0]


def _store(f, data):
    import json
    if not hasattr(f, _STORE):
        f.addProperty("App::PropertyString", _STORE, "InCad", "InCad hole settings")
        f.setPropertyStatus(_STORE, "Hidden")
    setattr(f, _STORE, json.dumps(data, sort_keys=True))


def _load(f):
    import json
    try:
        return json.loads(getattr(f, _STORE, "") or "{}")
    except ValueError:
        return {}


def _depth_to(doc, f, text):
    """Depth from the hole's start plane (its sketch) to a picked plane/flat face (computed; re-done on edit)."""
    picks = parse_picks(doc, text)
    if not picks:
        raise ValueError("Pick the face or plane the hole goes to")
    obj, sub = resolve(*picks[0])
    target = obj.getSubObject(sub) if sub else (obj.Shape.Faces[0] if obj.Shape.Faces else None)
    if target is None or target.ShapeType != "Face":
        raise ValueError("Pick a flat face or a plane for the hole to stop at")
    prof = f.Profile[0] if isinstance(f.Profile, (tuple, list)) else f.Profile
    pl = prof.getGlobalPlacement()
    n = pl.Rotation.multVec(App.Vector(0, 0, 1))
    depth = abs((target.Surface.Position - pl.Base).dot(n)) if hasattr(target.Surface, "Position") else \
        abs((target.CenterOfMass - pl.Base).dot(n))
    if depth < 1e-6:
        raise ValueError("That face is where the hole starts: pick another one")
    return depth


def _centres(doc, sk):
    """The hole centres a Concentric hole's helper sketch should hold now (its round edges where they are), in
    the sketch's coordinates; None when they can't be found (an edge gone: left as it is)."""
    face_text, _, edge_text = (getattr(sk, "InCadPicks", "") or "").partition("|")
    try:
        axes = [_round_centre(*resolve(o, s)) for o, s in parse_picks(doc, edge_text)]
    except Exception:
        return None
    if not axes:
        return None
    inv = sk.getGlobalPlacement().inverse()
    out = []
    for point, _axis in axes:
        local = inv.multVec(point)
        at = App.Vector(local.x, local.y, 0)
        if all((at - q).Length > 1e-6 for q in out):
            out.append(at)
    return out


def _reach(f):
    """How far the part reaches from the hole's start plane, either way, plus 1 mm."""
    base = f.BaseFeature.Shape if f.BaseFeature is not None else None
    prof = f.Profile[0] if isinstance(f.Profile, (tuple, list)) and f.Profile else f.Profile
    if base is None or base.isNull() or prof is None:
        return None
    pl = prof.getGlobalPlacement()
    n = pl.Rotation.multVec(App.Vector(0, 0, 1))
    bb = base.BoundBox
    corners = [App.Vector(x, y, z) for x in (bb.XMin, bb.XMax) for y in (bb.YMin, bb.YMax) for z in (bb.ZMin, bb.ZMax)]
    return max(abs((c - pl.Base).dot(n)) for c in corners) + 1.0


def _drifted(doc, f):
    """What of the hole no longer matches the part: 'depth' (To a face that moved) and / or 'centres' (a
    Concentric hole whose round edges moved). Both were measured once and stayed (the finder: a 10 mm block made
    20 mm left the hole blind; a moved boss left its hole off-centre)."""
    out = []
    data = _load(f)
    if data.get("depth_type") == "through_all" and data.get("hole_type") == "tapped" and f.DepthType == "Dimension":
        reach = _reach(f)
        if reach is not None and abs(reach - f.Depth.Value) > 1e-6:
            out.append("reach")
    if data.get("depth_type") == "to" and data.get("to_face"):
        try:
            if abs(_depth_to(doc, f, data["to_face"]) - f.Depth.Value) > 1e-6:
                out.append("depth")
        except Exception:
            pass
    prof = f.Profile[0] if isinstance(f.Profile, (tuple, list)) and f.Profile else f.Profile
    if _is_helper(prof):
        now = _centres(doc, prof)
        have = [App.Vector(g.X, g.Y, 0) for g in prof.Geometry if g.TypeId == "Part::GeomPoint"]
        if now is not None and (len(now) != len(have) or any((a - b).Length > 1e-6 for a, b in zip(now, have))):
            out.append("centres")
    return out


def follow(before, values):
    """Dialog values that follow a change: an Insert hole goes as deep as the insert (a little deeper), not Through
    All: the insert's hole ran through the part (the user)."""
    if values.get("hole_type") == "insert" and before.get("hole_type") != "insert" \
            and values.get("depth_type") == "through_all":
        return {"depth_type": "distance"}
    return {}


def follow_part(doc):
    """Bring every hole in step with the part after a recompute (incad.ui.shell): a To-a-face depth measured again,
    a Concentric hole's centres put where its round edges are. One undo step when anything changed."""
    from incad.core import safe
    holes = [o for o in doc.Objects if o.TypeId == "PartDesign::Hole" and hasattr(o, _STORE)]
    todo = [(h, _drifted(doc, h)) for h in holes]
    todo = [(h, what) for h, what in todo if what]
    if not todo:
        return []

    def go():
        for h, what in todo:
            if "centres" in what:
                prof = h.Profile[0] if isinstance(h.Profile, (tuple, list)) else h.Profile
                face_text, _, edge_text = prof.InCadPicks.partition("|")
                _helper_sketch(doc, h.getParentGeoFeatureGroup(), {"face": face_text, "edge": edge_text}, prof)
            if "depth" in what:
                h.Depth = _depth_to(doc, h, _load(h)["to_face"])
            if "reach" in what:
                h.Depth = _reach(h)
    r = safe.run_safely("Holes follow the part", go, doc)
    return [h.Label for h, _ in todo] if r.ok else []


def unavailable(values):
    """Choices the dialog greys out now: a seat (counterbore, countersink, spotface) is the start of the hole, so
    its chamfer can only be at the exit."""
    return {"chamfer": {"start"} if (values.get("seat") or "none") != "none" else set()}


def _apply(doc, f, values):
    from incad.core import model
    from incad.features import hole_chamfer
    _apply_hole(doc, f, values)
    hole_chamfer.sync(doc, f, values.get("chamfer") or "none", values.get("chamfer_size") or "0.5 mm")


def _apply_hole(doc, f, values):
    from incad.core import model
    seat = values.get("seat") or "none"
    f.HoleCutType = _CUT.get(seat, "None")
    f.Reversed = bool(values.get("flip"))
    term = values.get("depth_type") or "through_all"
    f.DepthType = "ThroughAll" if term == "through_all" else "Dimension"
    tapped_through = term == "through_all" and values.get("hole_type") == "tapped"
    insert = inserts.named(values.get("insert_size")) if values.get("hole_type") == "insert" else None
    f.DrillPoint = "Angled" if values.get("tip") == "angled" and insert is None else "Flat"
    if f.DrillPoint == "Angled":
        try:
            f.DrillPointAngle = App.Units.Quantity((values.get("tip_angle") or "118 °").replace("°", " deg")).Value
        except Exception:
            raise ValueError("Point angle: enter an angle such as 118°")
    _apply_thread(doc, f, values)
    if term == "distance":  # an insert's hole: a little deeper than the insert
        value_param(doc, f, "Depth", "%g mm" % inserts.depth(insert) if insert else values.get("depth") or "10 mm",
                    "length")
    elif term == "to":
        for path in ("Depth", ".Depth"):
            if model.bound_parameter(f, path) is not None:
                f.setExpression(path, None)
        doc.recompute()
        f.Depth = _depth_to(doc, f, values.get("to_face"))
    elif tapped_through:  # a modeled thread Through All ran its helix the whole box diagonal: 7x slower than a
        reach = _reach(f)  # depth; it goes just past the part instead, measured again when the part changes
        if reach is not None:
            f.DepthType = "Dimension"
            f.setExpression("Depth", None)
            f.Depth = reach
    if seat == "counterbore":
        value_param(doc, f, "HoleCutDiameter", values.get("cbore_diameter") or "10 mm", "length")
        value_param(doc, f, "HoleCutDepth", values.get("cbore_depth") or "3 mm", "length")
    elif seat == "countersink":
        value_param(doc, f, "HoleCutDiameter", values.get("csink_diameter") or "10 mm", "length")
        value_param(doc, f, "HoleCutCountersinkAngle", values.get("csink_angle") or "90 °", "angle")
    elif seat == "spotface":
        value_param(doc, f, "HoleCutDiameter", values.get("spot_diameter") or "10 mm", "length")
        value_param(doc, f, "HoleCutDepth", values.get("spot_depth") or "0.5 mm", "length")
    _store(f, {"hole_type": values.get("hole_type") or "simple", "seat": seat, "fit": values.get("fit") or "normal",
               "insert_size": insert.name if insert else "",
               "depth_type": term, "to_face": values.get("to_face") or "", "flip": bool(values.get("flip")),
               "tip_angle": values.get("tip_angle") or "118 °"})


def _release_diameter(f):
    from incad.core import model
    name = model.bound_parameter(f, "Diameter")
    if name is not None:  # the thread/bolt sets the drill size; keep the plain diameter for switching back
        if not hasattr(f, _PLAIN):
            f.addProperty("App::PropertyString", _PLAIN, "InCad")
            f.setPropertyStatus(_PLAIN, "Hidden")
        setattr(f, _PLAIN, name)
        f.setExpression("Diameter", None)


def _apply_thread(doc, f, values):
    from incad.core import model
    kind_of_hole = values.get("hole_type") or ("tapped" if values.get("threaded") else "simple")
    if kind_of_hole in _THREADED:
        _release_diameter(f)
        std = values.get("thread_standard") or "iso"
        kind = _THREAD_TYPES.get(std, "ISOMetricProfile")
        tapped = kind_of_hole == "tapped"
        f.Threaded = tapped
        f.ModelThread = tapped
        if f.ThreadType != kind:
            f.ThreadType = kind
        names = list(f.getEnumerationsOfProperty("ThreadSize"))
        size = values.get("thread_size")
        f.ThreadSize = size if size in names else _default_size(std, names, values.get("diameter"))
        if not tapped:  # clearance hole for that bolt: FreeCAD sizes it from the fit
            f.ThreadFit = _FIT.get(values.get("fit") or "normal", "Medium")
            return
        f.ThreadDirection = "Left" if values.get("hand") == "left" else "Right"
        f.UseCustomThreadClearance = True
        try:
            f.CustomThreadClearance = App.Units.Quantity(values.get("clearance") or "0.2 mm").Value
        except Exception:
            raise ValueError("Print clearance: enter a length such as 0.2 mm")
        if values.get("thread_depth") == "distance":
            f.ThreadDepthType = "Dimension"
            value_param(doc, f, "ThreadDepth", values.get("thread_length") or "8 mm", "length")
        else:
            f.ThreadDepthType = "Hole Depth"
        return
    if f.Threaded or f.ThreadType != "None":
        f.Threaded = False
        f.ModelThread = False
        f.ThreadType = "None"
    if kind_of_hole == "insert":  # the insert's hole; the Simple diameter is kept for switching back
        _release_diameter(f)
        f.Diameter = inserts.named(values.get("insert_size")).hole
        return
    plain = getattr(f, _PLAIN, "")
    if plain and model.bound_parameter(f, "Diameter") is None:
        model.bind(f, "Diameter", plain)
    value_param(doc, f, "Diameter", values.get("diameter") or "5 mm", "length")


def create(doc, body, profile, values):
    sk = _profile(doc, body, values)
    f = body.newObject("PartDesign::Hole", "Hole")
    f.Profile = sk
    _apply(doc, f, values)
    return f


def read(doc, f):
    prof = f.Profile[0] if isinstance(f.Profile, (tuple, list)) else f.Profile
    concentric = _is_helper(prof)
    data = _load(f)
    seat = data.get("seat") or {v: k for k, v in _CUT.items() if k != "spotface"}.get(f.HoleCutType, "none")
    if f.Threaded:
        hole_type = "tapped"
    elif f.ThreadType != "None":
        hole_type = "clearance"
    else:
        hole_type = "insert" if data.get("hole_type") == "insert" else "simple"
    values = defaults(f.Document, None, None)
    values.update(placement="concentric" if concentric else "sketch", sketch="" if concentric else prof.Name,
                  hole_type=hole_type, seat=seat,
                  depth_type=data.get("depth_type") or ("through_all" if f.DepthType == "ThroughAll" else "distance"),
                  to_face=data.get("to_face", ""), flip=bool(f.Reversed),
                  tip="angled" if f.DrillPoint == "Angled" else "flat", tip_angle=data.get("tip_angle", "118 °"),
                  diameter=value_text(doc, f, "Diameter") or "5 mm", depth=value_text(doc, f, "Depth") or "10 mm")
    if hole_type == "insert":
        values["insert_size"] = data.get("insert_size") or inserts.DEFAULT
        plain = getattr(f, _PLAIN, "")
        if plain:
            from incad.core import model
            values["diameter"] = model.parameter_text(doc, plain)
    if hole_type in _THREADED:
        std = {v: k for k, v in _THREAD_TYPES.items()}.get(f.ThreadType, "iso")
        values.update(thread_standard=std, thread_size=f.ThreadSize)
        plain = getattr(f, _PLAIN, "")
        if plain:
            from incad.core import model
            values["diameter"] = model.parameter_text(doc, plain)
    if hole_type == "clearance":
        values["fit"] = {v: k for k, v in _FIT.items()}.get(f.ThreadFit, "normal")
    if hole_type == "tapped":
        values.update(clearance="%g mm" % f.CustomThreadClearance.Value,
                      hand="left" if f.ThreadDirection == "Left" else "right",
                      thread_depth="distance" if f.ThreadDepthType == "Dimension" else "hole",
                      thread_length=value_text(doc, f, "ThreadDepth") or "8 mm")
    if concentric:
        face, _, edge = getattr(prof, "InCadPicks", "").partition("|")
        values.update(face=face, edge=edge)
    if seat == "counterbore":
        values.update(cbore_diameter=value_text(doc, f, "HoleCutDiameter") or "10 mm",
                      cbore_depth=value_text(doc, f, "HoleCutDepth") or "3 mm")
    if seat == "countersink":
        values.update(csink_diameter=value_text(doc, f, "HoleCutDiameter") or "10 mm",
                      csink_angle=value_text(doc, f, "HoleCutCountersinkAngle") or "90°")
    if seat == "spotface":
        values.update(spot_diameter=value_text(doc, f, "HoleCutDiameter") or "10 mm",
                      spot_depth=value_text(doc, f, "HoleCutDepth") or "0.5 mm")
    from incad.features import hole_chamfer
    values["chamfer"], values["chamfer_size"] = hole_chamfer.read(doc, f)
    return values


def _is_helper(sk):
    return sk is not None and hasattr(sk, "InCadPicks")


def update(doc, f, values):
    current = f.Profile[0] if isinstance(f.Profile, (tuple, list)) else f.Profile
    body = f.getParentGeoFeatureGroup()
    if values.get("placement") == "concentric":
        if _is_helper(current):
            _helper_sketch(doc, body, values, current)
        else:
            f.Profile = _helper_sketch(doc, body, values)
    else:
        picks = parse_picks(doc, values.get("sketch"))
        if picks and current is not picks[0][0]:
            f.Profile = picks[0][0]
            if _is_helper(current):
                body.removeObject(current)
                doc.removeObject(current.Name)
    _apply(doc, f, values)
    return f


def _pad_of(f):
    return f
