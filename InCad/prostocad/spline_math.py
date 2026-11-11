"""The sketch's two splines as the standard curves every CAD draws (the user, 2026-11-10: "a spline as Plasticity's
or close"; the old program's are the same two), made by OpenCASCADE, no Qt here:

- "cv", steered by its points: the B-spline on them as poles, degree 3 (2 on three points, a line on two), its knots
  evenly spaced; open, it is held to its two ends and leaves each along its first leg; closed, it is periodic. The
  old program's Control-Vertex Spline (FreeCAD's buildFromPoles).
- "fit", through its points: OpenCASCADE's interpolation (GeomAPI_Interpolate), smooth in direction and in curvature
  at every point, its parameters the distances between the points. The old program's Spline.

The curve itself goes into the solid and the STEP (solid.py), no longer a fit through the points drawn. Before this
the sketch drew formulas of its own (a uniform B-spline with its ends tripled, a Catmull-Rom curve), written before
OpenCASCADE came into the program: an old file's spline stood up to 2.8 mm off, and a curve through points jumped in
curvature at each point.

Points one on another in a row count once (the user's next_thread: three control points on each end hooked the curve
to a 0.01 mm radius, and its STEP was faulty).

A third kind keeps a curve of an old file that neither of the two gives back (one drawn by a script, its knots
uneven): its kind is "bs" followed by its degree, knots, multiplicities and weights (exact_kind), its points its
poles. The numbers ride in the kind itself, so every tool that copies, mirrors or deletes a spline carries them along
with no list of its own to keep in step; with a pole added or taken away they no longer fit, and it is a "cv" again.

The two OpenCASCADE calls are _poles_curve and _through_curve: a copy of this file run without OCP swaps those two."""
import json

TOL = 1e-6  # mm: points nearer than this are one point
EXACT = "bs"  # the kind's first letters of a curve kept as its file has it


def exact_kind(degree, knots, mults, weights=None):
    """The kind of a spline kept exactly: 'bs{"d": 3, "k": [...], "m": [...]}' ("w": its weights when any is not 1)."""
    d = {"d": int(degree), "k": [float(k) for k in knots], "m": [int(m) for m in mults]}
    if weights and any(abs(w - 1.0) > 1e-12 for w in weights):
        d["w"] = [float(w) for w in weights]
    return EXACT + json.dumps(d, separators=(",", ":"))


def exact_of(kind, n=None, closed=False):
    """{"d", "k", "m", "w"} of a kind made by exact_kind, else None; with n given, None too when the numbers no
    longer fit n poles (a point added or taken away since)."""
    if not isinstance(kind, str) or not kind.startswith(EXACT + "{"):
        return None
    try:
        d = json.loads(kind[len(EXACT):])
        d = {"d": int(d["d"]), "k": list(d["k"]), "m": list(d["m"]), "w": d.get("w")}
    except (ValueError, KeyError, TypeError):
        return None
    if n is not None:
        poles = sum(d["m"][:-1]) if closed else sum(d["m"]) - d["d"] - 1
        if poles != n or len(d["k"]) != len(d["m"]) or (d["w"] is not None and len(d["w"]) != n):
            return None
    return d


def steered(kind):
    """True for a spline whose points steer it (its poles): "cv" and a curve kept exactly; "fit" goes through them."""
    return kind != "fit"


def distinct(pts, closed=False, tol=TOL):
    """pts without a point lying on the one before it (nor, closed, the last on the first)."""
    out = []
    for q in pts:
        if not out or abs(q[0] - out[-1][0]) > tol or abs(q[1] - out[-1][1]) > tol:
            out.append((float(q[0]), float(q[1])))
    if closed and len(out) > 1 and abs(out[-1][0] - out[0][0]) <= tol and abs(out[-1][1] - out[0][1]) <= tol:
        out.pop()
    return out


def cv_knots(n, closed):
    """(degree, knots, multiplicities, periodic) of the "cv" spline on n poles (the old buildFromPoles)."""
    periodic = bool(closed and n >= 3)
    degree = min(3, n - 1)
    if periodic:
        return degree, [float(k) for k in range(n + 1)], [1] * (n + 1), True
    inner = n - degree - 1
    return degree, [float(k) for k in range(inner + 2)], [degree + 1] + [1] * inner + [degree + 1], False


# ----- OpenCASCADE: the only two calls (ProstoCAD 0.18 trial: through FreeCAD's Part, the same OpenCASCADE; no OCP
# in the old program's Python) -----
class _Pnt:
    def __init__(self, v):
        self.v = v

    def X(self):
        return self.v.x

    def Y(self):
        return self.v.y

    def Z(self):
        return self.v.z

    def Distance(self, other):
        return (self.v - other.v).Length


class _Curve:
    """A Part.BSplineCurve answering as OCP's Geom_BSplineCurve does where this file asks it."""

    def __init__(self, bs):
        self.bs = bs

    def Pole(self, i):
        return _Pnt(self.bs.getPole(i))

    def NbPoles(self):
        return self.bs.NbPoles

    def Degree(self):
        return self.bs.Degree

    def NbKnots(self):
        return self.bs.NbKnots

    def Knot(self, i):
        return self.bs.getKnot(i)

    def Multiplicity(self, i):
        return self.bs.getMultiplicity(i)

    def IsPeriodic(self):
        return bool(self.bs.isPeriodic())

    def Weight(self, i):
        return self.bs.getWeight(i)

    def Value(self, u):
        return _Pnt(self.bs.value(u))

    def FirstParameter(self):
        return self.bs.FirstParameter

    def LastParameter(self):
        return self.bs.LastParameter


def _poles_curve(poles, degree, knots, mults, periodic, weights=None):
    import FreeCAD as App
    import Part
    p = [App.Vector(q[0], q[1], q[2] if len(q) > 2 else 0.0) for q in poles]
    bs = Part.BSplineCurve()
    w = [float(v) for v in weights] if weights and any(abs(v - 1.0) > 1e-12 for v in weights) else None
    if w is not None:
        bs.buildFromPolesMultsKnots(p, [int(m) for m in mults], [float(k) for k in knots], bool(periodic), int(degree), w)
    else:
        bs.buildFromPolesMultsKnots(p, [int(m) for m in mults], [float(k) for k in knots], bool(periodic), int(degree))
    return _Curve(bs)


def _through_curve(points, periodic):
    import FreeCAD as App
    import Part
    bs = Part.BSplineCurve()
    try:
        bs.interpolate(Points=[App.Vector(q[0], q[1], q[2] if len(q) > 2 else 0.0) for q in points],
                       PeriodicFlag=bool(periodic), Tolerance=1e-9)
    except Exception:
        raise ValueError("no curve goes through these points")
    return _Curve(bs)


def part_curve(c):
    """The Part.BSplineCurve inside a curve of this file (the trial writes it into Sketcher)."""
    return c.bs


# ----- the sketch's splines -----
def definition(kind, pts, closed, place=None):
    """The spline as plain numbers: {"degree", "poles", "knots", "mults", "periodic", "weights"}, or None with fewer
    than two points apart. place(x, y) -> (x, y, z) puts it on a sketch's plane (the solid's edge); else z = 0."""
    c = curve(kind, pts, closed, place)
    return None if c is None else numbers(c)


def numbers(c):
    """An OpenCASCADE B-spline curve as plain numbers."""
    poles = [(c.Pole(i).X(), c.Pole(i).Y(), c.Pole(i).Z()) for i in range(1, c.NbPoles() + 1)]
    return {"degree": c.Degree(), "poles": poles, "knots": [c.Knot(i) for i in range(1, c.NbKnots() + 1)],
            "mults": [c.Multiplicity(i) for i in range(1, c.NbKnots() + 1)], "periodic": bool(c.IsPeriodic()),
            "weights": [c.Weight(i) for i in range(1, c.NbPoles() + 1)]}


def curve(kind, pts, closed, place=None):
    """The spline's own curve (an OpenCASCADE B-spline), or None with fewer than two points apart."""
    ex = exact_of(kind, len(pts), closed)
    if ex is not None:  # kept as its file has it: every pole counts, one on another too
        at = [place(q[0], q[1]) for q in pts] if place is not None else [(float(q[0]), float(q[1]), 0.0) for q in pts]
        return _poles_curve(at, ex["d"], ex["k"], ex["m"], bool(closed), ex["w"])
    pts = distinct(pts, closed)
    n = len(pts)
    if n < 2:
        return None
    at = [place(x, y) for x, y in pts] if place is not None else [(x, y, 0.0) for x, y in pts]
    if n == 2:  # two points: the line between them, either kind
        return _poles_curve(at, 1, [0.0, 1.0], [2, 2], False)
    if kind == "fit":
        return _through_curve(at, bool(closed))
    degree, knots, mults, periodic = cv_knots(n, closed)
    return _poles_curve(at, degree, knots, mults, periodic)


def apart(a, b, n=600):
    """How far two curves stand from each other (mm), measured at the same shares of their parameters."""
    a0, a1, b0, b1 = a.FirstParameter(), a.LastParameter(), b.FirstParameter(), b.LastParameter()
    return max(a.Value(a0 + (a1 - a0) * i / n).Distance(b.Value(b0 + (b1 - b0) * i / n)) for i in range(n + 1))


SAME = 1e-3  # mm: a file's curve one of the two standard splines gives back this closely is read as that spline


def from_file(poles, degree, knots, mults, periodic, weights=None):
    """An old file's B-spline as the sketch's spline: (kind, points, how far its curve is from the file's, mm).
    "cv" on its poles or "fit" through the points at its knots when that gives the file's curve back within SAME;
    else kept exactly (exact_kind) on its poles."""
    poles = [(float(q[0]), float(q[1])) for q in poles]
    old = _poles_curve(poles, degree, knots, mults, periodic, weights)
    params = list(knots[:-1] if periodic else knots)
    tries = [("cv", poles)]
    if all(abs(w - 1.0) < 1e-12 for w in (weights or [])):
        tries.append(("fit", [(old.Value(u).X(), old.Value(u).Y()) for u in params]))
    for kind, places in tries:
        try:
            c = curve(kind, places, periodic)
            dev = apart(old, c) if c is not None else None
        except Exception:
            dev = None
        if dev is not None and dev < SAME and len(distinct(places, periodic)) == len(places):
            return kind, places, dev
    return exact_kind(degree, knots, mults, weights), poles, 0.0


def polyline(kind, pts, closed, steps=14):
    """The curve as points [(x, y)] for drawing and picking: `steps` to each stretch between two knots, its last
    point the curve's end (closed: its first point again)."""
    c = curve(kind, pts, closed)
    apart = distinct(pts, closed)
    if c is None:
        return apart
    # as many points as the sketch drew before (`steps` to each leg between two of the spline's points), spread
    # over the curve's own stretches: a curve through three points is one stretch, and 14 points drew it angular
    legs = len(apart) if closed else len(apart) - 1
    stretches = max(1, c.NbKnots() - 1)
    return sampled(c, max(steps, -(-steps * legs // stretches)))


def sampled(c, steps=14):
    """Points [(x, y)] along an OpenCASCADE curve: `steps` to each stretch between two of its knots."""
    first, last = c.FirstParameter(), c.LastParameter()
    knots = [c.Knot(i) for i in range(1, c.NbKnots() + 1)] if hasattr(c, "NbKnots") else [first, last]
    knots = [first] + [k for k in knots if first + 1e-12 < k < last - 1e-12] + [last]
    out = []
    for a, b in zip(knots, knots[1:]):
        for s in range(steps):
            p = c.Value(a + (b - a) * s / steps)
            out.append((p.X(), p.Y()))
    p = c.Value(last)
    out.append((p.X(), p.Y()))
    return out
