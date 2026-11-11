"""A Sketcher sketch read into the new sketch's data (SketchView.dump() / load() form), with a report of everything
that does not come over (yet). Pure model: no Qt, no GUI; nothing in the document is changed.

The new sketch's points are shared: two line ends tied Coincident are one point there. Its lines are [a, b], its
arcs [centre, start, end] counterclockwise (a circle: [centre, rim, rim]), its ellipses [centre, a, b] with a and b
on the curve along the two half axes (an elliptical arc: an `ell_cut` tie [start, end, centre, a, b]), its splines
[kind, points, closed]. What holds it is `relations` (per line) and `ties` (per point; dimensions are `dim_*` ties
with name, formula and value). See prostocad/sketch.py (dump, load, _rels, _con_ties, _dim_make)."""
import math

from incad.core import log

ROOT = (-1, 1)  # Sketcher's origin as a point (H_Axis, start)
H_AXIS, V_AXIS = -1, -2
POS_START, POS_END, POS_MID = 1, 2, 3

# what each Sketcher constraint becomes, said in the report
MAPPED = "mapped"
LATER = "later"          # comes in a later step of the plan (patterns, text, projected edges …)
MISSING = "missing"      # the new sketch has nothing for it yet: to be built (plan step 5)
HELPER = "helper"        # Sketcher's own inner geometry ties (ellipse axes, spline poles): kept as they are


class Report:
    def __init__(self, name):
        self.name = name
        self.geometry = {}        # kind -> count
        self.inner = 0            # inner (InternalAlignment) geometry left to Sketcher
        self.external = 0         # projected edges
        self.constraints = {}     # (type, verdict, why) -> count
        self.splines = []         # (index, deviation mm, note)
        self.dims = []            # (name, kind, value, formula)
        self.extras = []          # texts, patterns, images …
        self.notes = []

    def geo(self, kind):
        self.geometry[kind] = self.geometry.get(kind, 0) + 1

    def con(self, ctype, verdict, why=""):
        key = (ctype, verdict, why)
        self.constraints[key] = self.constraints.get(key, 0) + 1

    def as_dict(self):
        return dict(name=self.name, geometry=self.geometry, inner=self.inner, external=self.external,
                    constraints=[dict(type=t, verdict=v, why=w, count=n) for (t, v, w), n in sorted(self.constraints.items())],
                    splines=self.splines, dims=self.dims, extras=self.extras, notes=self.notes)


class _Points:
    """The new sketch's points: Sketcher's (geo, pos) vertices merged where Coincident ties them (union-find)."""

    def __init__(self):
        self.xy = []
        self.parent = []
        self.of = {}  # (geo, pos) -> index before merging

    def add(self, x, y, key=None):
        self.xy.append((x, y))
        self.parent.append(len(self.parent))
        if key is not None:
            self.of[key] = len(self.xy) - 1
        return len(self.xy) - 1

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def join(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.parent[max(a, b)] = min(a, b)

    def renumber(self):
        """{old index: final index} and the final points (the merged one stands where the first of them stood)."""
        roots, out, xy = {}, {}, []
        for i in range(len(self.xy)):
            r = self.find(i)
            if r not in roots:
                roots[r] = len(xy)
                xy.append(self.xy[r])
            out[i] = roots[r]
        return out, xy


def _ext_flags(sk):
    """{geo id (-3, -4 …): normal?} for the projected edges (incad.core.sketch._projected_flags: the Defining flag)."""
    from incad.core import sketch as sketches
    return {g: on for g, (on, _ref) in sketches._projected_flags(sk).items()}


def _inner_geometry(sk):
    """Geo ids Sketcher holds as an ellipse's or spline's inner geometry (InternalAlignment): left to Sketcher."""
    return {c.First for c in sk.Constraints if c.Type == "InternalAlignment"}


def _arc_ends(sk, g_id):
    """(start, end) of an arc as Sketcher's own start and end vertices: counterclockwise in the sketch. An arc whose
    axis points down (a mirrored one) has its geometry's StartPoint at Sketcher's end vertex (seen 2026-11-07 in
    engrave.FCStd), so the geometry's StartPoint / EndPoint are not used."""
    return sk.getPoint(g_id, POS_START), sk.getPoint(g_id, POS_END)


def _spline_points(g):
    """The new sketch's control-vertex spline nearest to Sketcher's: its poles. Returns (poles, closed)."""
    return [(p.x, p.y) for p in g.getPoles()], bool(g.isPeriodic())


def _spline_deviation(g, poles, closed):
    """The largest distance (mm) between Sketcher's spline and the new sketch's cv spline on the same poles, measured on
    the new sketch's own curve formula (prostocad.sketch._spline_curve), without Qt."""
    try:
        curve = _cv_curve(poles, closed)
    except Exception:
        return None
    import FreeCAD as App
    worst = 0.0
    for x, y in curve:
        p = App.Vector(x, y, 0)
        try:
            u = g.parameter(p)
            worst = max(worst, (g.value(u) - p).Length)
        except Exception:
            return None
    return worst


def _cv_curve(pts, closed, steps=14):
    """prostocad.sketch._spline_curve(kind='cv') in plain numbers (a uniform cubic B-spline, held to its ends)."""
    n = len(pts)
    if n < 3 and not closed:
        return list(pts)
    cv = list(pts) if closed else [pts[0], pts[0]] + list(pts) + [pts[-1], pts[-1]]
    m = len(cv)
    out = []
    spans = m if closed else m - 3
    for k in range(spans):
        p0, p1, p2, p3 = (cv[(k + j) % m] for j in range(4))
        for s in range(steps + (0 if closed or k < spans - 1 else 1)):
            t = s / steps
            b0, b1 = (1 - t) ** 3 / 6, (3 * t ** 3 - 6 * t ** 2 + 4) / 6
            b2, b3 = (-3 * t ** 3 + 3 * t ** 2 + 3 * t + 1) / 6, t ** 3 / 6
            out.append((b0 * p0[0] + b1 * p1[0] + b2 * p2[0] + b3 * p3[0],
                        b0 * p0[1] + b1 * p1[1] + b2 * p2[1] + b3 * p3[1]))
    return out


def read(sk):
    """(data, report): the sketch as the new sketch's data, and what did not come over. Nothing is changed."""
    rep = Report(sk.Label)
    inner = _inner_geometry(sk)
    pts = _Points()
    lines, arcs, ells, spls = [], [], [], []
    constr, arc_constr, ell_constr, spl_constr = [], [], [], []
    shape = {}     # geo -> ("line", i) / ("arc", i) / ("ell", i) / ("spl", i) / ("point", k)
    shape_ids = dict(line=[], arc=[], ell=[], spl=[])  # FreeCAD's geometry id of each shape (kept on writing back)
    ext_flags = _ext_flags(sk)  # projected edges (-3, -4 …): normal (part of the profile) or construction
    gid = lambda g_id: sk.getGeometryId(g_id) if g_id >= 0 else "ext:%d" % g_id
    ties = []
    ell_cut = []
    try:
        external = list(sk.ExternalGeo)
    except Exception:
        external = []
    every = list(enumerate(sk.Geometry)) + [(-i - 1, external[i]) for i in range(2, len(external))]
    for g_id, g in every:
        kind = g.TypeId
        if g_id in inner:
            rep.inner += 1
            continue
        construction = bool(sk.getConstruction(g_id)) if g_id >= 0 else not ext_flags.get(g_id, False)
        if kind == "Part::GeomLineSegment":
            rep.geo("line")
            a = pts.add(g.StartPoint.x, g.StartPoint.y, (g_id, POS_START))
            b = pts.add(g.EndPoint.x, g.EndPoint.y, (g_id, POS_END))
            shape[g_id] = ("line", len(lines))
            shape_ids["line"].append(gid(g_id))
            lines.append([a, b])
            constr.append(construction)
        elif kind == "Part::GeomCircle":
            rep.geo("circle")
            c = pts.add(g.Center.x, g.Center.y, (g_id, POS_MID))
            r = pts.add(g.Center.x + g.Radius, g.Center.y)
            shape[g_id] = ("arc", len(arcs))
            shape_ids["arc"].append(gid(g_id))
            arcs.append([c, r, r])
            arc_constr.append(construction)
        elif kind == "Part::GeomArcOfCircle":
            rep.geo("arc")
            s, e = _arc_ends(sk, g_id)
            c = pts.add(g.Center.x, g.Center.y, (g_id, POS_MID))
            a = pts.add(s.x, s.y, (g_id, POS_START))
            b = pts.add(e.x, e.y, (g_id, POS_END))
            shape[g_id] = ("arc", len(arcs))
            shape_ids["arc"].append(gid(g_id))
            arcs.append([c, a, b])
            arc_constr.append(construction)
        elif kind in ("Part::GeomEllipse", "Part::GeomArcOfEllipse"):
            rep.geo("ellipse" if kind == "Part::GeomEllipse" else "elliptical arc")
            el = g if kind == "Part::GeomEllipse" else g.Ellipse
            cen, ax = el.Center, el.MajorAxis if hasattr(el, "MajorAxis") else el.XAxis
            ax = ax.normalize() if ax.Length > 1e-12 else ax
            up = el.Axis
            perp = up.cross(ax)
            c = pts.add(cen.x, cen.y, (g_id, POS_MID))
            a = pts.add(cen.x + ax.x * el.MajorRadius, cen.y + ax.y * el.MajorRadius)
            b = pts.add(cen.x + perp.x * el.MinorRadius, cen.y + perp.y * el.MinorRadius)
            if up.z < 0:
                a, b = b, a  # the new sketch turns from a towards b counterclockwise
            shape[g_id] = ("ell", len(ells))
            shape_ids["ell"].append(gid(g_id))
            ells.append([c, a, b])
            ell_constr.append(construction)
            if kind == "Part::GeomArcOfEllipse":
                s, e = _arc_ends(sk, g_id)
                ks = pts.add(s.x, s.y, (g_id, POS_START))
                ke = pts.add(e.x, e.y, (g_id, POS_END))
                ell_cut.append([ks, ke, c, a, b])
        elif kind == "Part::GeomBSplineCurve":
            rep.geo("spline")
            poles, closed = _spline_points(g)
            weights = [w for w in g.getWeights() if abs(w - 1.0) > 1e-9]
            try:  # the new program's reading of an old file's spline (prostocad/spline_math.from_file): "cv" on its
                # poles or "fit" through its points when that gives the curve back, else kept exactly
                from prostocad import spline_math
                spl_kind, places, dev = spline_math.from_file(poles, g.Degree, g.getKnots(), g.getMultiplicities(),
                                                              closed, g.getWeights())
            except Exception:
                log.logger().exception("new sketch: reading a spline")
                spl_kind, places, dev = "cv", poles, _spline_deviation(g, poles, closed)
            idx = [pts.add(x, y) for x, y in places]
            if not closed:  # its ends are Sketcher's start and end points
                pts.of[(g_id, POS_START)] = idx[0]
                pts.of[(g_id, POS_END)] = idx[-1]
            note = "degree %d, %d poles%s, read as %s" % (g.Degree, len(poles), ", weighted" if weights else "",
                                                          spl_kind if spl_kind in ("cv", "fit") else "kept exactly")
            rep.splines.append((g_id, None if dev is None else round(dev, 4), note))
            shape[g_id] = ("spl", len(spls))
            shape_ids["spl"].append(gid(g_id))
            spls.append([spl_kind, idx, closed])
            spl_constr.append(construction)
        elif kind == "Part::GeomPoint":
            rep.geo("point")
            k = pts.add(g.X, g.Y, (g_id, POS_START))
            shape[g_id] = ("point", k)
            if g_id >= 0:
                ties.append(dict(kind="sk_point", pts=[k], ref=None, mark=None, gid=gid(g_id)))
            else:  # a projected vertex: a Point the part holds
                ties.append(dict(kind="sk_point", pts=[k], ref=gid(g_id), mark=None))
        else:
            rep.geo("other: " + kind)
            rep.notes.append("geometry %d is a %s: not read" % (g_id, kind))
    try:
        rep.external = max(0, len(sk.ExternalGeo) - 2)
    except Exception:
        pass

    def vertex(geo, pos):
        """The point index of a Sketcher vertex, or None (axes, projected, inner geometry)."""
        if (geo, pos) == ROOT:
            return "origin"
        return pts.of.get((geo, pos))

    # 1. Coincident (and the end-to-end Tangent) join points first: the new sketch's points are shared
    for c in sk.Constraints:
        if c.Type == "Coincident" or (c.Type == "Tangent" and c.FirstPos and c.SecondPos):
            a, b = vertex(c.First, c.FirstPos), vertex(c.Second, c.SecondPos)
            if isinstance(a, int) and isinstance(b, int):
                pts.join(a, b)
    old_to_new, xy = pts.renumber()
    N = lambda k: old_to_new[k]
    lines = [[N(a), N(b)] for a, b in lines]
    arcs = [[N(c), N(a), N(b)] for c, a, b in arcs]
    ells = [[N(c), N(a), N(b)] for c, a, b in ells]
    spls = [[kind, [N(k) for k in idx], closed] for kind, idx, closed in spls]
    for t in ties:
        t["pts"] = [N(k) for k in t["pts"]]
    for cut in ell_cut:
        ties.append(dict(kind="ell_cut", pts=[N(k) for k in cut], ref=None, mark=None))
    shape = {g: (s[0], N(s[1]) if s[0] == "point" else s[1]) for g, s in shape.items()}

    def P(geo, pos):
        v = vertex(geo, pos)
        return N(v) if isinstance(v, int) else v

    def line_of(geo):
        s = shape.get(geo)
        return lines[s[1]] if s and s[0] == "line" else None

    def arc_of(geo):
        s = shape.get(geo)
        return arcs[s[1]] if s and s[0] == "arc" else None

    def ell_of(geo):
        s = shape.get(geo)
        return ells[s[1]] if s and s[0] == "ell" else None

    def kind_of(geo):
        if geo in (H_AXIS, V_AXIS):
            return "axis"
        if geo in inner:
            return "inner"
        s = shape.get(geo)
        return s[0] if s else "?"

    dist = lambda a, b: math.hypot(xy[a][0] - xy[b][0], xy[a][1] - xy[b][1])
    mapped = set()  # the Sketcher constraints the new sketch now holds (written back from its ties)
    now = [None]  # the constraint being read
    names = {}  # constraint index -> its name (dimensions)
    exprs = {}
    for path, e in getattr(sk, "ExpressionEngine", []) or []:
        p = path.lstrip(".")
        if p.startswith("Constraints."):
            from incad.core import model
            exprs[p[len("Constraints."):]] = model.to_display(sk.Document, e)  # plain names, as the user typed them

    def dim(kind, pts_, value, c, i, **extra):
        name = c.Name or ""
        expr = exprs.get(name) or None
        t = dict(kind=kind, pts=list(pts_), ref=value, mark=None, off=0.0, far=None, along=None, name=name or None,
                 expr=expr, neg=False, axis=None, base=None, first=None)
        t.update(extra)
        if not c.Driving:
            t["driven"] = True
        ties.append(t)
        mapped.add(i)
        rep.dims.append((name, kind, round(value, 6), expr or ""))
        rep.con(c.Type, MAPPED, kind + (" (reference)" if not c.Driving else ""))

    def tie(kind, *p, ref=None, mark=None):
        ties.append(dict(kind=kind, pts=list(p), ref=ref, mark=mark))
        mapped.add(now[0])

    from incad.core import sketch as sketches
    for i, c in enumerate(sk.Constraints):
        now[0] = i
        t = c.Type
        k1, k2 = kind_of(c.First), (kind_of(c.Second) if c.Second != -2000 else None)
        if t == "InternalAlignment" or t == "Weight" or (c.First in inner and t in ("Radius", "Diameter")):
            rep.con(t, HELPER, "inner geometry of an ellipse or spline")
            continue
        if sketches.is_pattern_name(c.Name):
            rep.con(t, LATER, "sketch pattern tie (step 4)")
            continue
        if k1 == "inner" and k2 in ("inner", None) and t in ("Equal", "Radius", "Diameter"):
            rep.con(t, HELPER, "between a spline's pole circles (its weights)")
            continue
        if "inner" in (k1, k2):
            rep.con(t, MISSING, "on an ellipse's axis or a spline's pole")
            continue
        if t == "Coincident":
            a, b = P(c.First, c.FirstPos), P(c.Second, c.SecondPos)
            if "origin" in (a, b):
                rep.con(t, MAPPED, "point at the origin (held there)")
                mapped.add(i)
            elif a is None or b is None:
                rep.con(t, MISSING, "Coincident of %s and %s" % (k1, k2))
            else:
                rep.con(t, MAPPED, "shared point")
                mapped.add(i)
            continue
        if t == "Tangent" and c.FirstPos and c.SecondPos:
            shared = P(c.First, c.FirstPos)
            if (k1, k2) in (("line", "arc"), ("arc", "line")):
                line, arc = (c.First, c.Second) if k1 == "line" else (c.Second, c.First)
                la, lb = line_of(line)
                tie("con_touch", shared, arc_of(arc)[0], la, lb, mark="Tangent")
                rep.con(t, MAPPED, "line–arc end to end")
            elif (k1, k2) == ("arc", "arc"):
                tie("con_circles", shared, arc_of(c.First)[0], arc_of(c.Second)[0], mark="Tangent")
                rep.con(t, MAPPED, "arc–arc end to end")
            elif (k1, k2) == ("line", "line"):
                a, b = line_of(c.First)
                c2, d = line_of(c.Second)
                tie("con_col", a, b, c2, d, mark="Collinear")
                rep.con(t, MAPPED, "line–line end to end (collinear)")
            else:
                rep.con(t, MISSING, "end-to-end tangency %s–%s" % (k1, k2))
            continue
        if t == "Tangent" and (c.FirstPos or c.SecondPos):
            rep.con(t, MISSING, "endpoint-to-edge tangency %s–%s" % (k1, k2))
            continue
        if t == "Tangent":
            pair = {k1, k2}
            if pair == {"line"}:
                a, b = line_of(c.First)
                c2, d = line_of(c.Second)
                tie("con_col", a, b, c2, d, mark="Collinear")
                rep.con(t, MAPPED, "line–line (collinear)")
            elif pair == {"line", "arc"}:
                line, arc = (c.First, c.Second) if k1 == "line" else (c.Second, c.First)
                cc, s_, _e = arc_of(arc)
                tie("con_dist", cc, s_, *line_of(line), mark="Tangent")
                rep.con(t, MAPPED, "line–arc")
            elif pair == {"arc"}:
                c1, a1, _ = arc_of(c.First)
                c2, a2, _ = arc_of(c.Second)
                r1, r2, d = dist(c1, a1), dist(c2, a2), dist(c1, c2)
                tie("con_apart", c1, a1, c2, a2, ref=d < max(r1, r2), mark="Tangent")
                rep.con(t, MAPPED, "arc–arc")
            elif pair == {"line", "ell"}:
                line, el = (c.First, c.Second) if k1 == "line" else (c.Second, c.First)
                tie("con_ell_line", *ell_of(el), *line_of(line), mark="Tangent")
                rep.con(t, MAPPED, "line–ellipse")
            elif pair == {"arc", "axis"}:
                arc, axis = (c.First, c.Second) if k1 == "arc" else (c.Second, c.First)
                tie("con_axis", *arc_of(arc)[:2], ref="x" if axis == H_AXIS else "y", mark="Tangent")
                rep.con(t, MAPPED, "circle–axis")
            elif pair == {"arc", "ell"}:
                arc, el = (c.First, c.Second) if k1 == "arc" else (c.Second, c.First)
                tie("con_ell_arc", *arc_of(arc)[:2], *ell_of(el), mark="Tangent")
                rep.con(t, MAPPED, "arc–ellipse")
            else:
                rep.con(t, MISSING, "Tangent %s–%s" % (k1, k2))
            continue
        if t == "PointOnObject":
            p = P(c.First, c.FirstPos)
            if p == "origin" or p is None:
                rep.con(t, MISSING, "PointOnObject of the origin or a hidden point")
            elif c.Second == H_AXIS:
                tie("y0", p)
                rep.con(t, MAPPED, "point on the X axis")
            elif c.Second == V_AXIS:
                tie("x0", p)
                rep.con(t, MAPPED, "point on the Y axis")
            elif k2 == "line":
                tie("con_on_line", p, *line_of(c.Second), mark="Coincident")
                rep.con(t, MAPPED, "point on a line")
            elif k2 == "arc":
                cc, s_, _e = arc_of(c.Second)
                tie("con_on_arc", cc, s_, p, mark="Coincident")
                rep.con(t, MAPPED, "point on an arc")
            elif k2 == "ell":
                tie("con_on_ell", p, *ell_of(c.Second), mark="Coincident")
                rep.con(t, MAPPED, "point on an ellipse")
            else:
                rep.con(t, MISSING, "point on a %s" % k2)
            continue
        if t in ("Horizontal", "Vertical"):
            same = "same_y" if t == "Horizontal" else "same_x"
            if c.FirstPos and c.SecondPos:  # two points
                a, b = P(c.First, c.FirstPos), P(c.Second, c.SecondPos)
                if isinstance(a, int) and isinstance(b, int):
                    tie(same, a, b, mark=t)
                    rep.con(t, MAPPED, "two points")
                else:
                    rep.con(t, MISSING, "%s with the origin" % t)
            elif k1 == "line":
                tie(same, *line_of(c.First), mark=t)
                rep.con(t, MAPPED, "line")
            else:
                rep.con(t, MISSING, "%s on a %s" % (t, k1))
            continue
        if t in ("Parallel", "Perpendicular"):
            if k1 == "line" and k2 == "line":
                tie("con_par" if t == "Parallel" else "con_perp", *line_of(c.First), *line_of(c.Second), mark=t)
                rep.con(t, MAPPED, "two lines")
            elif {k1, k2} == {"line", "axis"}:
                line = c.First if k1 == "line" else c.Second
                axis = c.Second if k1 == "line" else c.First
                level = (axis == H_AXIS) == (t == "Parallel")
                tie("same_y" if level else "same_x", *line_of(line), mark="Horizontal" if level else "Vertical")
                rep.con(t, MAPPED, "line and an axis")
            else:
                rep.con(t, MISSING, "%s %s–%s%s" % (t, k1, k2, " at their ends" if c.FirstPos else ""))
            continue
        if t == "Equal":
            if k1 == k2 == "line":
                tie("con_eq", *line_of(c.First), *line_of(c.Second), mark="Equal")
                rep.con(t, MAPPED, "two lines")
            elif k1 == k2 == "arc":
                tie("con_eq_r", *arc_of(c.First)[:2], *arc_of(c.Second)[:2], mark="Equal")
                rep.con(t, MAPPED, "two arcs / circles")
            else:
                rep.con(t, MISSING, "Equal %s–%s" % (k1, k2))
            continue
        if t == "Symmetric":
            a, b = P(c.First, c.FirstPos), P(c.Second, c.SecondPos)
            if not (isinstance(a, int) and isinstance(b, int)):
                rep.con(t, MISSING, "Symmetric of the origin")
            elif c.ThirdPos == 0 and c.Third == H_AXIS:
                tie("con_sym_ax", a, b, ref="x", mark="Symmetric")
                rep.con(t, MAPPED, "about the X axis")
            elif c.ThirdPos == 0 and c.Third == V_AXIS:
                tie("con_sym_ax", a, b, ref="y", mark="Symmetric")
                rep.con(t, MAPPED, "about the Y axis")
            elif c.ThirdPos == 0 and kind_of(c.Third) == "line":
                tie("con_sym", a, b, *line_of(c.Third), mark="Symmetric")
                rep.con(t, MAPPED, "about a line")
            else:
                m = P(c.Third, c.ThirdPos)
                if m == "origin":
                    tie("con_sym_o", a, b, mark="Symmetric")
                    rep.con(t, MAPPED, "about the origin")
                elif isinstance(m, int):
                    tie("con_sym_pt", a, b, m, mark="Symmetric")
                    rep.con(t, MAPPED, "about a point")
                else:
                    rep.con(t, MISSING, "Symmetric about a %s" % kind_of(c.Third))
            continue
        if t == "Block":
            rep.con(t, MISSING, "Block (a whole %s held where it is)" % k1)
            continue
        if t == "Lock":
            rep.con(t, MISSING, "Lock")
            continue
        # dimensions: their value is read from the geometry as it stands (Sketcher solved it), so the new sketch's
        # own sign and angle conventions hold; the name and formula come with it
        if t in ("Radius", "Diameter"):
            arc = arc_of(c.First)
            if arc is None:
                rep.con(t, MISSING, "%s of a %s" % (t, k1))
                continue
            cc, a = arc[0], arc[1]
            r = dist(cc, a)
            dim("dim_r" if t == "Radius" else "dim_d", [cc, a], r if t == "Radius" else 2 * r, c, i)
            continue
        if t in ("DistanceX", "DistanceY"):
            axis = 0 if t == "DistanceX" else 1
            if c.FirstPos == 0 and k1 == "line" and c.Second == -2000:  # a line's own run along X / Y
                a, b = line_of(c.First)
            else:
                a, b = P(c.First, c.FirstPos), (P(c.Second, c.SecondPos) if c.Second != -2000 else "origin")
                if c.Second == -2000 and c.FirstPos:  # one point: from the origin
                    a, b = "origin", a
            if a == "origin" and isinstance(b, int):
                dim("dim_x" if axis == 0 else "dim_y", [b], xy[b][axis], c, i)
            elif b == "origin" and isinstance(a, int):
                dim("dim_x" if axis == 0 else "dim_y", [a], xy[a][axis], c, i)
            elif isinstance(a, int) and isinstance(b, int):
                dim("dim_dx" if axis == 0 else "dim_dy", [a, b], xy[b][axis] - xy[a][axis], c, i)
            else:
                rep.con(t, MISSING, "%s between %s and %s" % (t, k1, k2))
            continue
        if t == "Distance":
            if c.Second == -2000 and k1 == "line":  # a line's length
                a, b = line_of(c.First)
                dim("dim_len", [a, b], dist(a, b), c, i)
            elif c.FirstPos and c.SecondPos:  # two points
                a, b = P(c.First, c.FirstPos), P(c.Second, c.SecondPos)
                if isinstance(a, int) and isinstance(b, int):
                    dim("dim_len", [a, b], dist(a, b), c, i)
                elif "origin" in (a, b):
                    k = b if a == "origin" else a
                    dim("dim_o", [k], math.hypot(*xy[k]), c, i)
                else:
                    rep.con(t, MISSING, "Distance between hidden points")
            elif c.FirstPos and k2 == "line":  # a point from a line
                p = P(c.First, c.FirstPos)
                if not isinstance(p, int):
                    rep.con(t, MISSING, "Distance from the origin to a line")
                    continue
                a, b = line_of(c.Second)
                ux, uy = xy[b][0] - xy[a][0], xy[b][1] - xy[a][1]
                v = (ux * (xy[p][1] - xy[a][1]) - uy * (xy[p][0] - xy[a][0])) / math.hypot(ux, uy)
                dim("dim_par", [p, a, b], v, c, i)
            elif c.FirstPos and c.Second in (H_AXIS, V_AXIS):
                p = P(c.First, c.FirstPos)
                axis = 1 if c.Second == H_AXIS else 0
                dim("dim_y" if axis else "dim_x", [p], xy[p][axis], c, i, via="axis")  # written back as it was
            elif {k1, k2} == {"arc"} and not c.FirstPos and not c.SecondPos:  # between two circles' edges
                c1, a1, _ = arc_of(c.First)
                c2, a2, _ = arc_of(c.Second)
                r1, r2, d = dist(c1, a1), dist(c2, a2), dist(c1, c2)
                if c1 == c2:  # one round the other (a ring): Sketcher measures the difference of the radii
                    small, big = (a1, a2) if r1 <= r2 else (a2, a1)
                    dim("dim_ring", [c1, small, big], abs(r2 - r1), c, i)
                elif d + min(r1, r2) <= max(r1, r2) + 1e-9:  # one inside the other, off centre: no such dimension yet
                    rep.con(t, MISSING, "Distance between a circle and one inside it")
                else:
                    dim("dim_gap", [c1, a1, c2, a2], d - r1 - r2, c, i)
            elif {k1, k2} == {"line", "arc"} and not c.FirstPos and not c.SecondPos:  # a circle's edge from a line
                line, arc = (c.First, c.Second) if k1 == "line" else (c.Second, c.First)
                cc, a, _ = arc_of(arc)
                la, lb = line_of(line)
                ux, uy = xy[lb][0] - xy[la][0], xy[lb][1] - xy[la][1]
                d = abs(ux * (xy[cc][1] - xy[la][1]) - uy * (xy[cc][0] - xy[la][0])) / math.hypot(ux, uy)
                dim("dim_rl_gap", [cc, a, la, lb], d - dist(cc, a), c, i)
            elif c.FirstPos and k2 == "arc":  # a point from a circle's edge
                p = P(c.First, c.FirstPos)
                cc, a, _ = arc_of(c.Second)
                dim("dim_rp_gap", [cc, a, p], dist(cc, p) - dist(cc, a), c, i)
            else:
                rep.con(t, MISSING, "Distance %s–%s" % (k1, k2))
            continue
        if t == "Angle":
            if k1 == "line" and k2 == "line":
                a, b = line_of(c.First)
                c2, d = line_of(c.Second)
                v = math.atan2(xy[d][1] - xy[c2][1], xy[d][0] - xy[c2][0]) - math.atan2(xy[b][1] - xy[a][1], xy[b][0] - xy[a][0])
                v = math.atan2(math.sin(v), math.cos(v))
                dim("dim_ang", [a, b, c2, d], v, c, i, lines=(a, b, c2, d), turn=0)
            elif k1 == "line" and c.Second == -2000:  # a line against the X axis
                a, b = line_of(c.First)
                th = math.atan2(xy[b][1] - xy[a][1], xy[b][0] - xy[a][0])
                dim("dim_angx", [a, b], th, c, i, axis="x", turn=0, base=0.0, first="axis")
            else:
                rep.con(t, MISSING, "Angle %s%s" % (k1, (" – " + k2) if k2 else ""))
            continue
        rep.con(t, MISSING, t)

    ties += _stored_ties(sk, xy, rep)
    data = dict(version=1, units="mm", plane="xy", name=sk.Label, pts=xy, lines=lines, arcs=arcs, ells=ells,
                constr=constr, arc_constr=arc_constr, ell_constr=ell_constr, relations=[], ties=ties, params=[],
                dim_next=0, centre=(0.0, 0.0), scale=4.0, spls=spls, spl_constr=spl_constr, spl_comb=[],
                dim_constr=False, images=[], shape_ids=shape_ids)
    from incad.sketch_next import texts
    texts.read(sk, data)  # the sketch's Text objects, as the new sketch's texts
    _extras(sk, rep)
    rep.mapped = sorted(mapped)
    return data, rep


def _stored_ties(sk, xy, rep):
    """The new sketch's ties Sketcher has no constraint for, written beside the sketch (STORE) with the places of
    their points: each point found again where it stands (1e-6 mm); a tie whose point is gone is left out."""
    import json
    text = getattr(sk, "InCadSketchNext", "") or ""
    if not text:
        return []
    try:
        stored = json.loads(text).get("ties", [])
    except ValueError:
        rep.notes.append("InCadSketchNext could not be read")
        return []
    out = []
    for t in stored:
        found = []
        for x, y in t.get("xy", []):
            k = min(range(len(xy)), key=lambda i: (xy[i][0] - x) ** 2 + (xy[i][1] - y) ** 2) if xy else None
            if k is None or math.hypot(xy[k][0] - x, xy[k][1] - y) > 1e-6:
                found = None
                break
            found.append(k)
        if found is None:
            rep.notes.append("a stored %s tie lost its points" % t.get("kind"))
            continue
        out.append(dict({k: v for k, v in t.items() if k != "xy"}, pts=found))
    return out


def _extras(sk, rep):
    """What lives beside the sketch's geometry: Text, sketch patterns, pictures, area profiles (later steps)."""
    try:
        from incad.core import sketch_patterns
        n = len(sketch_patterns.records(sk))
        if n:
            rep.extras.append("%d sketch pattern%s (InCadPatterns, step 4)" % (n, "" if n == 1 else "s"))
    except Exception:
        pass
    for o in sk.InList:
        if getattr(o, "Sketch", None) is sk and "Text" in (getattr(o, "Label", "") + o.Name):
            rep.extras.append("Text %s (step 4)" % o.Label)
    try:
        from incad.core import regions
        n = len(regions.profiles_of(sk))
        if n:
            rep.extras.append("%d area profile%s (follow the sketch on their own)" % (n, "" if n == 1 else "s"))
    except Exception:
        pass


# ----- writing back -----
STORE = "InCadSketchNext"  # on the sketch: the new sketch's ties that Sketcher has no constraint for (JSON)
EPS = 1e-9


def _same(a, b):
    """Nothing to write: the new sketch ends as it was read (points within 1e-9 mm, the same shapes and ties)."""
    if len(a["pts"]) != len(b["pts"]) or any(abs(x[0] - y[0]) > EPS or abs(x[1] - y[1]) > EPS
                                             for x, y in zip(a["pts"], b["pts"])):
        return False
    for key in ("lines", "arcs", "ells", "constr", "arc_constr", "ell_constr", "spl_constr"):
        if [list(x) if isinstance(x, (list, tuple)) else x for x in a.get(key, [])] != \
                [list(x) if isinstance(x, (list, tuple)) else x for x in b.get(key, [])]:
            return False
    if [[s[0], list(s[1]), bool(s[2])] for s in a.get("spls", [])] != [[s[0], list(s[1]), bool(s[2])] for s in b.get("spls", [])]:
        return False
    if a.get("shape_ids") != b.get("shape_ids") or list(a.get("relations", [])) != list(b.get("relations", [])):
        return False
    key = lambda t: (t["kind"], list(t["pts"]), repr(t.get("ref")) if not t["kind"].startswith("dim_") else round(t["ref"], 9),
                     t.get("name"), t.get("expr"), bool(t.get("driven")))
    return sorted(map(key, a["ties"]), key=repr) == sorted(map(key, b["ties"]), key=repr)


def _true_axes(c, a, b):
    """(major half axis vector, major, minor) of the ellipse round c through a and b (half axes or conjugate halves)."""
    a11, a12, a21, a22 = a[0] - c[0], b[0] - c[0], a[1] - c[1], b[1] - c[1]
    sq = a11 * a11 + a12 * a12 + a21 * a21 + a22 * a22
    det = a11 * a22 - a12 * a21
    root = math.sqrt(max(sq * sq - 4 * det * det, 0.0))
    major, minor = math.sqrt((sq + root) / 2), math.sqrt(max((sq - root) / 2, 0.0))
    th = 0.5 * math.atan2(2 * (a11 * a21 + a12 * a22), (a11 * a11 + a12 * a12) - (a21 * a21 + a22 * a22))
    return (math.cos(th), math.sin(th)), major, minor, det


def _geometry(kind, shape, data, cut=None, old=None):
    """Part geometry of one of the new sketch's shapes (sketch coordinates). old: the kept Sketcher geometry, used
    for a spline (its knots and weights stay; only the poles move)."""
    import FreeCAD as App
    import Part
    V = lambda k: App.Vector(data["pts"][k][0], data["pts"][k][1], 0)
    if kind == "line":
        return Part.LineSegment(V(shape[0]), V(shape[1]))
    if kind == "point":
        return Part.Point(V(shape))
    if kind == "arc":
        c, a, b = shape
        r = (V(a) - V(c)).Length
        circ = Part.Circle(V(c), App.Vector(0, 0, 1), r)
        if a == b:
            return circ
        a0 = math.atan2(V(a).y - V(c).y, V(a).x - V(c).x)
        a1 = math.atan2(V(b).y - V(c).y, V(b).x - V(c).x)
        while a1 <= a0:
            a1 += 2 * math.pi
        return Part.ArcOfCircle(circ, a0, a1)
    if kind == "ell":
        c, a, b = shape
        P = data["pts"]
        (ux, uy), major, minor, det = _true_axes(P[c], P[a], P[b])
        cp = V(c)
        ax = App.Vector(ux, uy, 0)
        if det < 0:  # the new sketch turns from a to b clockwise here: the ellipse's own axis points down
            el = Part.Ellipse(cp + ax * major, cp - App.Vector(-uy, ux, 0) * minor, cp)
        else:
            el = Part.Ellipse(cp + ax * major, cp + App.Vector(-uy, ux, 0) * minor, cp)
        if cut is None:
            return el
        s, e = (V(k) for k in cut)
        t0, t1 = el.parameter(s), el.parameter(e)
        while t1 <= t0:
            t1 += 2 * math.pi
        return Part.ArcOfEllipse(el, t0, t1)
    if kind == "spl":
        spl_kind, idx, closed = shape
        try:  # the new program's own curve (prostocad/spline_math): the one drawn is the one written
            from prostocad import spline_math
            c = spline_math.curve(spl_kind, [tuple(data["pts"][k]) for k in idx], bool(closed))
            if c is not None:
                return spline_math.part_curve(c)
        except Exception:
            log.logger().exception("new sketch: writing a spline")
        if old is not None and old.TypeId == "Part::GeomBSplineCurve" and old.NbPoles == len(idx):
            g = old.copy()
            for n, k in enumerate(idx, 1):
                g.setPole(n, V(k))
            return g
        poles = [V(k) for k in idx]
        cv = poles if closed else [poles[0], poles[0]] + poles + [poles[-1], poles[-1]]
        g = Part.BSplineCurve()
        n = len(cv)
        if closed:
            g.buildFromPolesMultsKnots(cv, [1] * (n + 1), list(range(n + 1)), True, 3)
        else:
            g.buildFromPolesMultsKnots(cv, [1] * (n + 4), list(range(n + 4)), False, 3)
        return g
    raise ValueError("no geometry for %s" % kind)


def _arc_the_old_way(arc):
    """The arc as one turned the other way round (axis down), as the file had it: the same curve, its edge running
    the same way, so a feature built on it is built as before (seen 2026-11-07: Q45_final's Block went null when an
    arc came back counterclockwise)."""
    import Part
    mid = arc.value((arc.FirstParameter + arc.LastParameter) / 2)
    return Part.ArcOfCircle(arc.EndPoint, mid, arc.StartPoint)  # end, middle, start: clockwise, its axis down


def _turned_like(new, old):
    """An ellipse (or its arc) written with its major axis the way the old one pointed: turned half round, Sketcher's
    inner axis lines and foci would swap ends and the solver turn the ellipse over."""
    try:
        el_new = new if new.TypeId == "Part::GeomEllipse" else new.Ellipse
        el_old = old if old.TypeId == "Part::GeomEllipse" else old.Ellipse
        a, b = el_new.XAxis, el_old.XAxis
        if a.x * b.x + a.y * b.y >= 0:
            return new
        import FreeCAD as App
        import Part
        c = el_new.Center
        flip = Part.Ellipse(c - a * el_new.MajorRadius, c - (el_new.Axis.cross(a)) * el_new.MinorRadius, c)
        if new.TypeId == "Part::GeomEllipse":
            return flip
        s, e = new.StartPoint, new.EndPoint
        t0, t1 = flip.parameter(s), flip.parameter(e)
        while t1 <= t0:
            t1 += 2 * math.pi
        return Part.ArcOfEllipse(flip, t0, t1)
    except Exception:
        return new


def _same_geometry(a, b):
    """Two Part geometries alike to 1e-9 mm (their ends, centre, radius or poles). An ellipse is the same whichever way
    its major axis is said to point (+X or -X along it)."""
    if a.TypeId != b.TypeId:
        return False
    if a.TypeId == "Part::GeomArcOfCircle":  # an arc turned the other way round (its axis down) is the same arc
        ends = lambda g: sorted([(round(g.StartPoint.x, 9), round(g.StartPoint.y, 9)), (round(g.EndPoint.x, 9), round(g.EndPoint.y, 9))])
        same_ends = all(abs(p[0] - q[0]) <= EPS and abs(p[1] - q[1]) <= EPS for p, q in zip(ends(a), ends(b)))
        return same_ends and (a.Center - b.Center).Length <= EPS and abs(a.Radius - b.Radius) <= EPS and \
            abs((a.LastParameter - a.FirstParameter) - (b.LastParameter - b.FirstParameter)) <= 1e-9
    if a.TypeId == "Part::GeomEllipse":
        ax, bx = a.XAxis, b.XAxis
        return (a.Center - b.Center).Length <= EPS and abs(a.MajorRadius - b.MajorRadius) <= EPS and \
            abs(a.MinorRadius - b.MinorRadius) <= EPS and abs(abs(ax.x * bx.x + ax.y * bx.y) - 1.0) <= 1e-9
    vals = lambda g: [x for p in _key_points(g) for x in (p.x, p.y)] + _key_numbers(g)
    va, vb = vals(a), vals(b)
    return len(va) == len(vb) and all(abs(x - y) <= EPS for x, y in zip(va, vb))


def _key_points(g):
    t = g.TypeId
    if t == "Part::GeomLineSegment":
        return [g.StartPoint, g.EndPoint]
    if t == "Part::GeomPoint":
        import FreeCAD as App
        return [App.Vector(g.X, g.Y, 0)]
    if t in ("Part::GeomCircle", "Part::GeomEllipse"):
        return [g.Center]
    if t in ("Part::GeomArcOfCircle", "Part::GeomArcOfEllipse"):
        return [g.Center, g.StartPoint, g.EndPoint]
    if t == "Part::GeomBSplineCurve":
        return list(g.getPoles())
    return []


def _key_numbers(g):
    t = g.TypeId
    if t in ("Part::GeomCircle", "Part::GeomArcOfCircle"):
        return [g.Radius]
    if t == "Part::GeomEllipse":
        return [g.MajorRadius, g.MinorRadius, g.XAxis.x, g.XAxis.y]
    if t == "Part::GeomArcOfEllipse":
        return [g.MajorRadius, g.MinorRadius]
    return []


def write(sk, data, force=False):
    """The sketch brought to `data` (the new sketch's dump): kept shapes (by their FreeCAD geometry id, data
    "shape_ids") change in place and keep their id; shapes gone are deleted; shapes drawn are added at the end; the
    constraints the new sketch holds are written again from its ties (names and formulas kept); Sketcher's own inner
    geometry and the constraints the new sketch does not hold stay as they were. Nothing is written when nothing
    changed (unless force). Returns what was done. Call inside one undo step (safe.run_safely)."""
    import json
    import Sketcher
    base, brep = read(sk)
    if not force and _same(data, base):
        return dict(changed=False, ext_map={})
    done = dict(changed=True, moved=0, added=0, removed=0, constraints=0, kept_only=0, ext_map={})
    pts = data["pts"]
    doc = sk.Document

    # the vertices Sketcher had on the origin, and those tied there (a point on the origin is held there in the new
    # sketch: tied to it again only if it was, or if it is new there)
    on_origin, tied_origin = set(), set()
    for i in range(sk.GeometryCount):
        for pos in (POS_START, POS_END, POS_MID):
            try:
                q = sk.getPoint(i, pos)
            except Exception:
                continue
            if abs(q.x) < EPS and abs(q.y) < EPS:
                on_origin.add((sk.getGeometryId(i), pos))
    for c in sk.Constraints:
        if c.Type == "Coincident" and (c.Second, c.SecondPos) == ROOT and c.First >= 0:
            tied_origin.add((sk.getGeometryId(c.First), c.FirstPos))
        elif c.Type == "Coincident" and (c.First, c.FirstPos) == ROOT and c.Second >= 0:
            tied_origin.add((sk.getGeometryId(c.Second), c.SecondPos))

    # 1. the constraints the new sketch holds go (written again below), then the shapes it no longer has
    if brep.mapped:
        sk.delConstraints(sorted(brep.mapped))
    want = {kind: [g for g in data.get("shape_ids", {}).get(kind, [])] for kind in ("line", "arc", "ell", "spl")}
    keep_ids = {g for ids in want.values() for g in ids if g is not None}
    keep_ids |= {t.get("gid") for t in data["ties"] if t["kind"] == "sk_point" and t.get("gid") is not None}
    inner_of = {}
    for c in sk.Constraints:
        if c.Type == "InternalAlignment":
            inner_of.setdefault(c.Second, []).append(c.First)
    gone = []
    inner = _inner_geometry(sk)
    for i in range(sk.GeometryCount):
        if i in inner:
            continue
        if sk.getGeometryId(i) not in keep_ids:
            gone += [i] + inner_of.get(i, [])
    if gone:
        sk.delGeometries(sorted(set(gone)))
        done["removed"] = len(set(gone))

    # projected edges taken away in the new sketch go from the sketch (Sketcher's own delExternal); the others keep
    # their place, their numbers closing up behind the ones gone
    ext_ref = lambda ref: int(ref[4:]) if isinstance(ref, str) and ref.startswith("ext:") else None
    ext_had = [g for g in (ext_ref(r) for ids in base.get("shape_ids", {}).values() for r in ids) if g is not None] + \
        [ext_ref(t.get("ref")) for t in base["ties"] if t["kind"] == "sk_point" and ext_ref(t.get("ref")) is not None]
    ext_want = {g for g in (ext_ref(r) for ids in want.values() for r in ids) if g is not None} | \
        {ext_ref(t.get("ref")) for t in data["ties"] if t["kind"] == "sk_point" and ext_ref(t.get("ref")) is not None}
    ext_gone = sorted(set(ext_had) - ext_want)  # the farthest first: the nearer ones keep their numbers meanwhile
    if ext_gone:
        count = len(sk.ExternalGeo)
        for g in ext_gone:
            sk.delExternal(-g - 3)
        if len(sk.ExternalGeo) != count - len(ext_gone):
            raise ValueError("Projected edges: Sketcher took away %d, not %d" % (count - len(sk.ExternalGeo), len(ext_gone)))
        done["removed"] += len(ext_gone)
    ext_now = lambda g: g + sum(1 for d in ext_gone if d > g)
    done["ext_map"] = {g: ext_now(g) for g in ext_want}  # a projected edge's number then -> now

    # 2. kept shapes changed in place (their id put back), new ones added
    at = {sk.getGeometryId(i): i for i in range(sk.GeometryCount)}
    cuts = {tuple(t["pts"][2:]): t["pts"][:2] for t in data["ties"] if t["kind"] == "ell_cut"}
    shapes = []  # (kind, shape, construction, gid)
    for kind, key, flags in (("line", "lines", "constr"), ("arc", "arcs", "arc_constr"), ("ell", "ells", "ell_constr"),
                             ("spl", "spls", "spl_constr")):
        ids = want[kind] + [None] * (len(data[key]) - len(want[kind]))
        fl = list(data.get(flags, [])) + [False] * len(data[key])
        for n, shape in enumerate(data[key]):
            shapes.append((kind, shape, bool(fl[n]), ids[n]))
    for t in data["ties"]:
        if t["kind"] == "sk_point":
            shapes.append(("point", t["pts"][0], False, t["ref"] if ext_ref(t.get("ref")) is not None else t.get("gid")))
    geo = sk.Geometry
    replaced, added, slot = [], [], {}
    ext_kind = {}  # projected edge (its number now) -> construction wanted
    for n, (kind, shape, construction, g_id) in enumerate(shapes):
        if ext_ref(g_id) is not None:  # projected: the part holds it, Sketcher draws it; only its kind is ours
            slot[n] = ext_now(ext_ref(g_id))
            if kind != "point":
                ext_kind[slot[n]] = construction
            continue
        cut = cuts.get(tuple(shape)) if kind == "ell" else None
        idx = at.get(g_id) if g_id is not None else None
        new = _geometry(kind, shape, data, cut, geo[idx] if idx is not None else None)
        if idx is not None and kind == "ell":
            new = _turned_like(new, geo[idx])
        if idx is not None and kind == "arc" and new.TypeId == "Part::GeomArcOfCircle" and geo[idx].Axis.z < 0:
            new = _arc_the_old_way(new)
        if idx is not None:
            slot[n] = idx
            if not _same_geometry(geo[idx], new):
                geo[idx] = new
                replaced.append((idx, g_id))
        else:
            added.append((n, new, construction))
    if replaced:
        # Sketcher gives the geometry written this way new ids, and a point loses its construction flag (seen
        # 2026-11-07 on an ellipse's foci): every id and flag is put back as it was, the replaced ones' too
        before = [(sk.getGeometryId(i), bool(sk.getConstruction(i))) for i in range(sk.GeometryCount)]
        sk.Geometry = geo
        for i, (g_id, construction) in enumerate(before):
            if sk.getGeometryId(i) != g_id:
                sk.setGeometryId(i, g_id)
            if bool(sk.getConstruction(i)) != construction:
                sk.setConstruction(i, construction)
        done["moved"] = len(replaced)
    for n, new, construction in added:
        slot[n] = sk.addGeometry(new, construction)
    done["added"] = len(added)
    for n, (kind, shape, construction, g_id) in enumerate(shapes):
        if n in slot and slot[n] >= 0 and kind != "point" and bool(sk.getConstruction(slot[n])) != construction:
            sk.setConstruction(slot[n], construction)
    flags = _ext_flags(sk)
    switch = [g for g, c in ext_kind.items() if flags.get(g, False) == c]  # normal wanted construction, or back
    if switch:
        from incad.core import sketch as sketches
        for want_c in (True, False):
            gs = [g for g in switch if ext_kind[g] == want_c]
            if gs:
                sketches.set_projected_construction(sk, gs, want_c)

    # 3. the vertices of each point (Sketcher's (geo, pos)), and the shapes by their points
    verts = {}
    line_at, arc_at, ell_at = {}, {}, {}
    for n, (kind, shape, _c, _g) in enumerate(shapes):
        g = slot[n]
        if kind == "line":
            verts.setdefault(shape[0], []).append((g, POS_START))
            verts.setdefault(shape[1], []).append((g, POS_END))
            line_at[(shape[0], shape[1])] = line_at[(shape[1], shape[0])] = g
        elif kind == "arc":
            verts.setdefault(shape[0], []).append((g, POS_MID))
            if shape[1] != shape[2]:
                verts.setdefault(shape[1], []).append((g, POS_START))
                verts.setdefault(shape[2], []).append((g, POS_END))
            arc_at[(shape[0], shape[1])] = g
        elif kind == "ell":
            verts.setdefault(shape[0], []).append((g, POS_MID))
            cut = cuts.get(tuple(shape))
            if cut:
                verts.setdefault(cut[0], []).append((g, POS_START))
                verts.setdefault(cut[1], []).append((g, POS_END))
            ell_at[tuple(shape)] = g
        elif kind == "spl" and not shape[2]:
            verts.setdefault(shape[1][0], []).append((g, POS_START))
            verts.setdefault(shape[1][-1], []).append((g, POS_END))
        elif kind == "point":
            verts.setdefault(shape, []).append((g, POS_START))

    C = Sketcher.Constraint
    out, named, kept_only = [], [], []
    free = {k for t in data["ties"] if t["kind"] == "free" for k in t["pts"]}
    # an end-to-end tangency is one Sketcher constraint (it joins the ends too): written so, as Sketcher makes it; a
    # Coincident beside a tangency of the edges made Sketcher call them redundant
    ends = {}  # point -> the two ends (geo, pos) a tangency joins there
    tan_ends = []
    for t in data["ties"]:
        if t["kind"] in ("con_touch", "con_circles", "con_col"):
            k = t["pts"][0] if t["kind"] != "con_col" else next(iter(set(t["pts"][:2]) & set(t["pts"][2:])), None)
            if k is None:
                continue
            vs = [v for v in verts.get(k, []) if v[1] in (POS_START, POS_END)]
            if len(vs) == 2 and k not in ends:
                ends[k] = vs
                tan_ends.append(t)
    # vertices the constraints left in place join already (a sketch pattern's own Coincident ties, kept by name):
    # joined again, Sketcher calls both redundant
    joined = {}

    def root(v):
        while joined.get(v, v) != v:
            v = joined[v]
        return v
    for c in sk.Constraints:
        if c.Type == "Coincident" or (c.Type == "Tangent" and c.FirstPos and c.SecondPos):
            a, b = root((c.First, c.FirstPos)), root((c.Second, c.SecondPos))
            if a != b:
                joined[a] = b
    for k, (a, b) in list(ends.items()):
        if root(a) == root(b):  # their ends meet already: the tangency of the edges is enough
            del ends[k]
    tan_ends = [t for t in tan_ends if (t["pts"][0] if t["kind"] != "con_col" else
                                        next(iter(set(t["pts"][:2]) & set(t["pts"][2:])), None)) in ends]
    for k, vs in verts.items():
        vs = sorted(vs, key=lambda v: v[0] < 0)  # the sketch's own vertex first: two projected ones are never tied
        if k in ends:  # the two ends the tangency joins; any other vertex there joins the first of them
            first = ends[k][0]
            rest = [v for v in vs if v not in ends[k]]
        else:
            first, rest = vs[0], vs[1:]
        for v in rest:
            if root(first) == root(v) or (first[0] < 0 and v[0] < 0):
                continue
            joined[root(v)] = root(first)
            out.append(C("Coincident", first[0], first[1], v[0], v[1]))
        if abs(pts[k][0]) < EPS and abs(pts[k][1]) < EPS and k not in free and first[0] >= 0:
            gids = {(sk.getGeometryId(g), pos) for g, pos in vs if g >= 0}
            if gids & tied_origin or not gids & on_origin:  # tied there before, or new there
                out.append(C("Coincident", first[0], first[1], -1, 1))  # a point on the origin is held there
    for k, (a, b) in ends.items():
        out.append(C("Tangent", a[0], a[1], b[0], b[1]))
    tan_ends = {id(t) for t in tan_ends}

    V = lambda k: verts[k][0] if k in verts else None
    L = lambda a, b: line_at.get((a, b))
    A = lambda c, s_: arc_at.get((c, s_))
    E = lambda c, a, b: ell_at.get((c, a, b))
    lines, arcs = data["lines"], data["arcs"]

    def axis(ref):
        return H_AXIS if ref == "x" else V_AXIS

    def one(t):
        """Sketcher constraints for one tie of the new sketch ([] for geometry ties, None: no Sketcher constraint)."""
        kind, p, ref = t["kind"], t["pts"], t.get("ref")
        if kind in ("sk_point", "ell_cut", "free", "sk_text"):  # a text: its Text object, written below
            return []
        if kind in ("same_x", "same_y"):
            word = "Vertical" if kind == "same_x" else "Horizontal"
            g = L(p[0], p[1])
            if g is not None:
                return [C(word, g)]
            a, b = V(p[0]), V(p[1])
            return [C(word, a[0], a[1], b[0], b[1])] if a and b else None
        if kind in ("x0", "y0"):
            a = V(p[0])
            return [C("PointOnObject", a[0], a[1], V_AXIS if kind == "x0" else H_AXIS)] if a else None
        if kind in ("con_on_line", "slot_ray"):
            a, g = V(p[0]), L(p[1], p[2])
            return [C("PointOnObject", a[0], a[1], g)] if a and g is not None else None
        if kind == "on_line":
            a = V(p[0])
            return [C("PointOnObject", a[0], a[1], L(*lines[ref]))] if a else None
        if kind == "con_on_arc":
            a, g = V(p[2]), A(p[0], p[1])
            return [C("PointOnObject", a[0], a[1], g)] if a and g is not None else None
        if kind == "on_arc":
            a = V(p[0])
            return [C("PointOnObject", a[0], a[1], A(*arcs[ref][:2]))] if a else None
        if kind == "con_on_ell":
            a, g = V(p[0]), E(*p[1:4])
            return [C("PointOnObject", a[0], a[1], g)] if a and g is not None else None
        if kind in ("con_par", "con_perp", "con_eq", "con_col"):
            g1, g2 = L(p[0], p[1]), L(p[2], p[3])
            word = {"con_par": "Parallel", "con_perp": "Perpendicular", "con_eq": "Equal", "con_col": "Tangent"}[kind]
            return [C(word, g1, g2)] if g1 is not None and g2 is not None else None
        if kind == "con_eq_r":
            g1, g2 = A(p[0], p[1]), A(p[2], p[3])
            return [C("Equal", g1, g2)] if g1 is not None and g2 is not None else None
        if kind == "same_r":
            return [C("Equal", A(*arcs[ref[0]][:2]), A(*arcs[ref[1]][:2]))]
        if kind == "con_touch":
            g1, g2 = L(p[2], p[3]), next((g for (c, _s), g in arc_at.items() if c == p[1]), None)
            return [C("Tangent", g1, g2)] if g1 is not None and g2 is not None else None
        if kind == "con_dist":
            g1, g2 = L(p[2], p[3]), A(p[0], p[1])
            return [C("Tangent", g1, g2)] if g1 is not None and g2 is not None else None
        if kind == "con_circles":
            gs = [g for (c, _s), g in arc_at.items() if c in (p[1], p[2])]
            return [C("Tangent", gs[0], gs[1])] if len(gs) == 2 else None
        if kind == "con_apart":
            g1, g2 = A(p[0], p[1]), A(p[2], p[3])
            return [C("Tangent", g1, g2)] if g1 is not None and g2 is not None else None
        if kind == "con_ell_line":
            g1, g2 = E(*p[0:3]), L(p[3], p[4])
            return [C("Tangent", g2, g1)] if g1 is not None and g2 is not None else None
        if kind == "con_ell_arc":
            g1, g2 = A(p[0], p[1]), E(*p[2:5])
            return [C("Tangent", g1, g2)] if g1 is not None and g2 is not None else None
        if kind == "con_axis":
            g = A(p[0], p[1])
            return [C("Tangent", g, axis(ref))] if g is not None else None
        if kind in ("con_sym", "con_sym_ax", "con_sym_o", "con_sym_pt", "mid", "slot_end", "centre", "centre_pt"):
            if kind in ("mid", "slot_end", "centre_pt"):  # p in the middle of a and b
                m, a, b = (p[0], *lines[ref]) if kind == "mid" else (p[0], p[1], p[2]) if kind == "slot_end" else (p[2], p[0], p[1])
                va, vb, vm = V(a), V(b), V(m)
                return [C("Symmetric", va[0], va[1], vb[0], vb[1], vm[0], vm[1])] if va and vb and vm else None
            va, vb = V(p[0]), V(p[1])
            if not (va and vb):
                return None
            if kind == "con_sym":
                g = L(p[2], p[3])
                return [C("Symmetric", va[0], va[1], vb[0], vb[1], g)] if g is not None else None
            if kind == "con_sym_ax":
                return [C("Symmetric", va[0], va[1], vb[0], vb[1], axis(ref))]
            if kind in ("con_sym_o", "centre"):
                return [C("Symmetric", va[0], va[1], vb[0], vb[1], -1, 1)]
            vm = V(p[2])
            return [C("Symmetric", va[0], va[1], vb[0], vb[1], vm[0], vm[1])] if vm else None
        if kind in ("con_same", "con_centre"):
            va, vb = V(p[0]), V(p[1])
            return [C("Coincident", va[0], va[1], vb[0], vb[1])] if va and vb else None
        if kind == "fix":
            a = V(p[0])
            return [C("DistanceX", -1, 1, a[0], a[1], pts[p[0]][0]), C("DistanceY", -1, 1, a[0], a[1], pts[p[0]][1])] if a else None
        return None

    def dim(t):
        """The Sketcher dimension for a dim_* tie, or None."""
        kind, p, v = t["kind"], t["pts"], t["ref"]
        if kind == "dim_len":
            g = L(p[0], p[1])
            if g is not None:
                return C("Distance", g, abs(v))
            a, b = V(p[0]), V(p[1])
            return C("Distance", a[0], a[1], b[0], b[1], abs(v)) if a and b else None
        if kind in ("dim_dx", "dim_dy"):
            word = "DistanceX" if kind == "dim_dx" else "DistanceY"
            a, b = V(p[0]), V(p[1])
            if not (a and b):
                return None
            return C(word, a[0], a[1], b[0], b[1], v) if v >= 0 else C(word, b[0], b[1], a[0], a[1], -v)
        if kind in ("dim_x", "dim_y"):
            word = "DistanceX" if kind == "dim_x" else "DistanceY"
            a = V(p[0])
            if not a:
                return None
            if t.get("via") == "axis":  # a point's distance from an axis, as Sketcher had it
                return C("Distance", a[0], a[1], H_AXIS if kind == "dim_y" else V_AXIS, abs(v))
            return C(word, -1, 1, a[0], a[1], v) if v >= 0 else C(word, a[0], a[1], -1, 1, -v)
        if kind == "dim_o":
            a = V(p[0])
            return C("Distance", -1, 1, a[0], a[1], abs(v)) if a else None
        if kind == "dim_par":
            a, g = V(p[0]), L(p[1], p[2])
            return C("Distance", a[0], a[1], g, abs(v)) if a and g is not None else None
        if kind in ("dim_r", "dim_d"):
            g = A(p[0], p[1])
            return C("Radius" if kind == "dim_r" else "Diameter", g, v) if g is not None else None
        if kind == "dim_ang":
            g1, g2 = L(p[0], p[1]), L(p[2], p[3])
            return C("Angle", g1, g2, v) if g1 is not None and g2 is not None else None
        if kind == "dim_angx" and t.get("first") == "axis" and not t.get("base"):
            g = L(p[0], p[1])
            return C("Angle", g, v) if g is not None else None
        if kind == "dim_gap":
            g1, g2 = A(p[0], p[1]), A(p[2], p[3])
            return C("Distance", g1, g2, v) if g1 is not None and g2 is not None else None
        if kind == "dim_ring":
            g1 = next((g for (c, s_), g in arc_at.items() if c == p[0] and s_ == p[1]), None)
            g2 = next((g for (c, s_), g in arc_at.items() if c == p[0] and s_ == p[2]), None)
            return C("Distance", g1, g2, v) if g1 is not None and g2 is not None else None
        if kind == "dim_rl_gap":
            g1, g2 = A(p[0], p[1]), L(p[2], p[3])
            return C("Distance", g1, g2, v) if g1 is not None and g2 is not None else None
        if kind == "dim_rp_gap":
            a, g = V(p[2]), A(p[0], p[1])
            return C("Distance", a[0], a[1], g, v) if a and g is not None else None
        return None

    for t in data["ties"]:
        if t["kind"].startswith("dim_"):
            c = dim(t)
            if c is None:
                kept_only.append(t)
                continue
            named.append((len(out), t))
            out.append(c)
            continue
        if id(t) in tan_ends:
            continue  # written above, end to end
        got = one(t)
        if got is None:
            kept_only.append(t)
        else:
            out += got
    for kind, line, _end, ref in data.get("relations", []):
        if line is None or line >= len(lines):
            continue
        g = L(*lines[line])
        if kind in ("h", "v"):
            out.append(C("Horizontal" if kind == "h" else "Vertical", g))
        elif kind == "perp":
            out.append(C("Perpendicular", g, L(*lines[line - 1 if ref is None else ref])))
        elif kind == "par":
            out.append(C("Parallel", g, L(*lines[ref])))
        elif kind in ("tan", "tan_on"):
            out.append(C("Tangent", g, A(*arcs[ref][:2])))
    # a constraint on projected edges and axes alone holds nothing of the sketch's own (Sketcher refuses it)
    own = lambda c: any(g >= 0 for g in (c.First, c.Second, c.Third) if g != -2000)
    named = [(n, t) for n, t in named if own(out[n])]
    keep_idx = [n for n, c in enumerate(out) if own(c)]
    renum = {n: i for i, n in enumerate(keep_idx)}
    named = [(renum[n], t) for n, t in named]
    out = [out[n] for n in keep_idx]
    first = sk.ConstraintCount
    if out:
        sk.addConstraint(out)
    done["constraints"] = len(out)
    from incad.core import document, model
    unit = document.part_units(doc) or "mm"
    for n, t in named:
        i = first + n
        if t.get("name"):
            sk.renameConstraint(i, t["name"])
        if t.get("driven"):
            sk.setDriving(i, False)
    now = {p.lstrip("."): e for p, e in sk.ExpressionEngine}
    for n, t in named:
        if not t.get("name"):
            continue
        path = "Constraints." + t["name"]
        had = model.to_display(doc, now[path]) if path in now else None
        if (t.get("expr") or None) == had:
            continue  # the formula as it was: left exactly as written in the file
        if t.get("expr"):
            word = "deg" if t["kind"] in ("dim_ang", "dim_angx") else unit
            sk.setExpression(path, model.to_expression(doc, model.with_units(t["expr"], word)))
        else:
            sk.setExpression(path, None)

    # 4. what Sketcher has no constraint for: kept beside the sketch, by the places of its points
    keep = [dict({k: x for k, x in t.items() if k != "n"}, xy=[list(pts[k]) for k in t["pts"]]) for t in kept_only]
    if keep or STORE in sk.PropertiesList:
        if STORE not in sk.PropertiesList:
            sk.addProperty("App::PropertyString", STORE, "InCad", "The new sketch's ties Sketcher has no constraint for")
        setattr(sk, STORE, json.dumps(dict(version=1, ties=keep)) if keep else "")
    done["kept_only"] = len(keep)

    # 5. the texts: Text objects beside the sketch, on its box's lines or its curve (by their geometry ids)
    geo_of = {}
    for n, (kind, shape, _c, _g) in enumerate(shapes):
        if n not in slot or kind == "point":
            continue
        if kind == "line":
            geo_of[(shape[0], shape[1])] = geo_of[(shape[1], shape[0])] = slot[n]
        elif kind == "spl":
            geo_of[tuple(shape[1])] = slot[n]
        elif kind == "ell":
            geo_of[tuple(shape) + tuple(cuts.get(tuple(shape)) or ())] = slot[n]
        else:
            geo_of[tuple(shape)] = slot[n]

    def text_ref(points):
        g = geo_of.get(tuple(points))
        if g is None:
            return None
        return {"id": sk.getGeometryId(g), "pos": 0} if g >= 0 else {"ext": g, "pos": 0}

    from incad.sketch_next import texts
    done["texts"] = len(texts.write(sk, data, text_ref))
    return done
