"""Sweep: a path that bends tighter than the profile reaches into the bend is refused before the kernel builds it
(the user's Sweep folded through itself on such a bend, and showing it crashed the app: the mesher recursed without
end). Instead of the solid, a cage of lines shows the sweep: the profile's outline every step along the path and the
lines its points run along, the tight stretch marked (the user: virtual lines, to judge the bend without everything
falling apart). The profile rides the path as it stands at its start, shifted aside or along it (the user: any shift
is kept all the way); only what reaches INTO a bend further than its radius folds. No Qt here: the view part
listens (incad.ui.sweep_cage)."""
import FreeCAD as App
import Part

STEP = 0.5     # mm between the path's samples (at most; at least SAMPLES_MIN of them)
SAMPLES_MIN, SAMPLES_MAX = 200, 4000
RINGS = 24     # outlines along the path in the cage
RUNNERS = 16   # points of the profile whose runs along the path are drawn

CAGE = None        # (document name, {"rings": [[Vector]], "long": [[Vector]], "tight": [[Vector]]}) or None
LISTENERS = []     # callables() told when CAGE changes


FORCE = False      # True: the user chose to build anyway (asked to save first); the check lets it through


class TooTight(ValueError):
    risky = True  # the window asks to save, then builds it anyway on OK (the user: build with consent)


def _set(cage):
    global CAGE
    CAGE = cage
    for f in list(LISTENERS):
        try:
            f()
        except Exception:
            pass


def clear():
    if CAGE is not None:
        _set(None)


def _square(t):
    """A unit vector square to t."""
    u = t.cross(App.Vector(0, 0, 1))
    if u.Length < 1e-6:
        u = t.cross(App.Vector(1, 0, 0))
    u.normalize()
    return u


def frames(path_shape, start):
    """[(point, tangent, u, v)] along the path from `start`: a frame carried without twist (as the sweep carries the
    profile), sampled about every STEP mm."""
    wire = Part.Wire(Part.__sortEdges__(path_shape.Edges))
    n = int(min(SAMPLES_MAX, max(SAMPLES_MIN, wire.Length / STEP)))
    pts = wire.discretize(n + 1)
    if (pts[-1] - start).Length < (pts[0] - start).Length:
        pts.reverse()
    out = []
    u = None
    for i, p in enumerate(pts):
        a, b = pts[max(0, i - 1)], pts[min(len(pts) - 1, i + 1)]
        t = b - a
        if t.Length < 1e-12:
            t = out[-1][1] if out else App.Vector(0, 0, 1)
        t = App.Vector(t).normalize()
        if u is None:
            u = _square(t)
        else:  # carried over: the part of the last u along the new tangent taken out (no twist)
            u = u - t * u.dot(t)
            u = u.normalize() if u.Length > 1e-9 else _square(t)
        out.append((p, t, u, t.cross(u)))
    return out


def local(points, frame):
    """The points in the frame's coordinates (along, u, v)."""
    p0, t, u, v = frame
    return [((q - p0).dot(t), (q - p0).dot(u), (q - p0).dot(v)) for q in points]


def place(coords, frame):
    p0, t, u, v = frame
    return [p0 + t * a + u * b + v * c for a, b, c in coords]


def _profile_points(profile_shape, per_edge=24):
    pts = []
    for e in profile_shape.Edges:
        pts.extend(e.discretize(per_edge))
    return pts


def fold(path_shape, profile_shape, start):
    """(smallest bend radius where the profile folds, how far the profile reaches into that bend,
    [[points of each folding stretch]], the frames, the profile in the start frame) — radius None when it never
    folds. A profile point folds where the path's curvature times its reach towards the bend's centre is 1 or more."""
    fr = frames(path_shape, start)
    prof = local(_profile_points(profile_shape), fr[0])
    worst = None  # (k * reach, radius, reach)
    stretches, run = [], []
    for i in range(1, len(fr) - 1):
        p, t, u, v = fr[i]
        a, b = fr[i - 1], fr[i + 1]
        ds = (b[0] - a[0]).Length
        if ds < 1e-12:
            continue
        kv = (b[1] - a[1]) * (1.0 / ds)  # curvature vector: towards the bend's centre, length 1/radius
        k = kv.Length
        if k < 1e-9:
            if len(run) > 1:
                stretches.append(run)
            run = []
            continue
        ku, kw = kv.dot(u), kv.dot(v)
        reach_k = max(c1 * ku + c2 * kw for _a, c1, c2 in prof)  # k * (the profile's reach towards the centre)
        if reach_k >= 1.0:
            run.append(p)
            if worst is None or reach_k > worst[0]:
                worst = (reach_k, 1.0 / k, reach_k / k)
        else:
            if len(run) > 1:
                stretches.append(run)
            run = []
    if len(run) > 1:
        stretches.append(run)
    if worst is None:
        return None, None, [], fr, prof
    return worst[1], worst[2], stretches, fr, prof


def cage(profile_shape, fr, prof_runners=None):
    """Lines of the sweep, from the profile carried along the frames (the kernel never asked: nothing to fold or
    crash): its outline every step and the runs of some of its points."""
    rings = []
    outline = [local(e.discretize(12), fr[0]) for e in profile_shape.Edges]
    for i in range(RINGS + 1):
        f = fr[min(len(fr) - 1, round(i * (len(fr) - 1) / RINGS))]
        for c in outline:
            rings.append(place(c, f))
    pts = []
    for w in profile_shape.Wires:
        pts.extend(w.discretize(RUNNERS + 1)[:-1])
    runners = local(pts, fr[0])
    along = [[f[0] + f[1] * a + f[2] * b + f[3] * c for f in fr] for a, b, c in runners]
    return rings, along


def check(doc, profile, path, start):
    """Raise TooTight (and show the cage) when the profile reaches into a bend further than its radius; else clear
    the cage."""
    try:
        radius, reach, stretches, fr, _prof = fold(path.Shape, profile.Shape, start)
    except Exception:
        clear()
        return
    if radius is None or FORCE:
        clear()
        return
    lines = {"rings": [], "long": [], "tight": stretches}
    try:
        lines["rings"], lines["long"] = cage(profile.Shape, fr)
    except Exception:
        pass  # the message alone, then
    _set((doc.Name, lines))
    raise TooTight("The path bends too tight for this profile: radius %.2f mm where the profile reaches %.2f mm "
                   "into the bend. The sweep would fold through itself and the program may crash. OK builds it "
                   "anyway, after asking to save" % (radius, reach))
