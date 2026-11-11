"""The sketch's relations as equations, and the solver that keeps them while something is pulled.

A relation is an equation on a few points: Rel(points, f), where f takes the flat coordinates of those points
[x0, y0, x1, y1…] and gives 0 when the relation holds. solve() moves the points as little as it can to bring every
equation to 0, the pulled points hardest of all to move, so they stay under the pointer whenever the relations allow.
Plain Python, one small system per pull: only the points the pulled ones reach through relations are solved."""
import math

DIFF = 1e-6  # the step of the numeric slopes, mm
PULLED = 1e-4  # how freely a pulled point may give, against 1 for the others
LOOSE = 25.0  # a point tied onto a line gives first: it follows the line, the line does not follow it
EPS = 1e-12


class Rel:
    __slots__ = ("points", "f", "loose")

    def __init__(self, points, f, loose=()):
        self.points, self.f, self.loose = tuple(points), f, tuple(loose)


def _norm(x, y):
    return math.hypot(x, y) + EPS


def level(a, b):
    return Rel((a, b), lambda c: c[1] - c[3])


def upright(a, b):
    return Rel((a, b), lambda c: c[0] - c[2])


def same_x(a, b):
    return upright(a, b)


def same_y(a, b):
    return level(a, b)


def x_is(a, x):
    return Rel((a,), lambda c: c[0] - x)


def y_is(a, y):
    return Rel((a,), lambda c: c[1] - y)


def perpendicular(a, b, c_, d):
    def f(c):
        ux, uy, wx, wy = c[2] - c[0], c[3] - c[1], c[6] - c[4], c[7] - c[5]
        return (ux * wx + uy * wy) / (_norm(ux, uy) * _norm(wx, wy))
    return Rel((a, b, c_, d), f)


def parallel(a, b, c_, d):
    def f(c):
        ux, uy, wx, wy = c[2] - c[0], c[3] - c[1], c[6] - c[4], c[7] - c[5]
        return (ux * wy - uy * wx) / (_norm(ux, uy) * _norm(wx, wy))
    return Rel((a, b, c_, d), f)


def on_line(p, a, b):
    def f(c):
        ux, uy = c[4] - c[2], c[5] - c[3]
        return (ux * (c[1] - c[3]) - uy * (c[0] - c[2])) / _norm(ux, uy)
    return Rel((p, a, b), f, (p,))


def middle(p, a, b):
    return [Rel((p, a, b), lambda c: 2 * c[0] - c[2] - c[4], (p,)),
            Rel((p, a, b), lambda c: 2 * c[1] - c[3] - c[5], (p,))]


def centre_at(a, b, p):
    """The middle of a…b stays on the point p."""
    return middle(p, a, b)


def centre_is(a, b, x, y):
    return [Rel((a, b), lambda c: c[0] + c[2] - 2 * x), Rel((a, b), lambda c: c[1] + c[3] - 2 * y)]


def centre_on_middle(a, b, p, q):
    """Two diagonals sharing their middle: the rectangle a…b centred on the middle of the line p…q."""
    return [Rel((a, b, p, q), lambda c: c[0] + c[2] - c[4] - c[6]), Rel((a, b, p, q), lambda c: c[1] + c[3] - c[5] - c[7])]


def same_distance(c_, a, b):
    """a and b on one circle round c_: an arc's two ends, or a point on an arc."""
    def f(c):
        return math.hypot(c[2] - c[0], c[3] - c[1]) - math.hypot(c[4] - c[0], c[5] - c[1])
    return Rel((c_, a, b), f)


def radius_is(c_, a, r):
    return Rel((c_, a), lambda c: math.hypot(c[2] - c[0], c[3] - c[1]) - r)


def same_radius(c1, a1, c2, a2):
    def f(c):
        return math.hypot(c[2] - c[0], c[3] - c[1]) - math.hypot(c[6] - c[4], c[7] - c[5])
    return Rel((c1, a1, c2, a2), f)


def line_touches(t, centre, a, b):
    """The line a…b touches the circle round centre at t: the radius to t is square to the line."""
    def f(c):
        rx, ry, ux, uy = c[0] - c[2], c[1] - c[3], c[6] - c[4], c[7] - c[5]
        return (rx * ux + ry * uy) / (_norm(rx, ry) * _norm(ux, uy))
    return Rel((t, centre, a, b), f)


def corner_cut(a, far_a, b, far_b, d):
    """a lies d from where the lines far_a…a and far_b…b would meet: a chamfer's side keeps its size."""
    def f(c):
        ax, ay, ux, uy = c[0], c[1], c[0] - c[2], c[1] - c[3]
        bx, by, wx, wy = c[4], c[5], c[4] - c[6], c[5] - c[7]
        den = ux * wy - uy * wx
        if abs(den) < 1e-12:
            return 0.0
        t = ((bx - ax) * wy - (by - ay) * wx) / den
        return t * _norm(ux, uy) - d  # signed: past the corner is not the same as before it
    return Rel((a, far_a, b, far_b), f)


def circles_touch(t, c1, c2):
    """Two circles touching at t: both radii to t lie along one line."""
    def f(c):
        ux, uy, wx, wy = c[0] - c[2], c[1] - c[3], c[0] - c[4], c[1] - c[5]
        return (ux * wy - uy * wx) / (_norm(ux, uy) * _norm(wx, wy))
    return Rel((t, c1, c2), f)


def collinear(a, b, c_, d):
    return [on_line(c_, a, b), on_line(d, a, b)]


def equal_length(a, b, c_, d):
    return Rel((a, b, c_, d), lambda c: math.hypot(c[2] - c[0], c[3] - c[1]) - math.hypot(c[6] - c[4], c[7] - c[5]))


def line_at_radius(centre, on_circle, a, b):
    """The line a…b touches the circle round centre through on_circle, wherever they stand."""
    def f(c):
        ux, uy = c[6] - c[4], c[7] - c[5]
        d = abs(ux * (c[1] - c[5]) - uy * (c[0] - c[4])) / _norm(ux, uy)
        return d - math.hypot(c[2] - c[0], c[3] - c[1])
    return Rel((centre, on_circle, a, b), f)


def axis_at_radius(centre, on_circle, axis):
    """The circle round centre through on_circle touches the axis "x" or "y"."""
    k = 1 if axis == "x" else 0  # its distance from the X axis is its y
    return Rel((centre, on_circle), lambda c: abs(c[k]) - math.hypot(c[2] - c[0], c[3] - c[1]))


def circles_apart(c1, a1, c2, a2, inside):
    """Two circles touching from outside, or one inside the other."""
    def f(c):
        d = math.hypot(c[4] - c[0], c[5] - c[1])
        r1, r2 = math.hypot(c[2] - c[0], c[3] - c[1]), math.hypot(c[6] - c[4], c[7] - c[5])
        return d - (abs(r1 - r2) if inside else r1 + r2)
    return Rel((c1, a1, c2, a2), f)


def ell_nearest(cx, cy, ux, uy, vx, vy, px, py):
    """(t, distance) of the place on the ellipse c + u cos t + v sin t nearest to p."""
    def far(t):
        co, si = math.cos(t), math.sin(t)
        return math.hypot(cx + ux * co + vx * si - px, cy + uy * co + vy * si - py)
    best = min((2 * math.pi * k / 48 for k in range(48)), key=far)
    step = 2 * math.pi / 48
    for _k in range(26):
        best = min((best - step, best, best + step), key=far)
        step /= 2
    return best, far(best)


def line_touches_ell(c_, a, b, p, q):
    """The line p…q touches the ellipse round c_ through a and b."""
    def f(c):
        cx, cy, ux, uy, vx, vy = c[0], c[1], c[2] - c[0], c[3] - c[1], c[4] - c[0], c[5] - c[1]
        dx, dy = c[8] - c[6], c[9] - c[7]
        nx, ny = -dy / _norm(dx, dy), dx / _norm(dx, dy)
        return abs(nx * (c[6] - cx) + ny * (c[7] - cy)) - math.hypot(nx * ux + ny * uy, nx * vx + ny * vy)
    return Rel((c_, a, b, p, q), f)


def circle_touches_ell(centre, on_circle, c_, a, b):
    """The circle round centre through on_circle touches the ellipse."""
    def f(c):
        return ell_nearest(c[4], c[5], c[6] - c[4], c[7] - c[5], c[8] - c[4], c[9] - c[5], c[0], c[1])[1] \
            - math.hypot(c[2] - c[0], c[3] - c[1])
    return Rel((centre, on_circle, c_, a, b), f)


def touches_ell_at(p, centre, c_, a, b):
    """An arc round centre leaves the ellipse at p, its point on it, along the ellipse's tangent there."""
    def f(c):
        ux, uy, vx, vy, dx, dy = c[6] - c[4], c[7] - c[5], c[8] - c[4], c[9] - c[5], c[0] - c[4], c[1] - c[5]
        det = ux * vy - uy * vx
        if abs(det) < 1e-12:
            return 0.0
        co, si = (dx * vy - dy * vx) / det, (ux * dy - uy * dx) / det
        tx, ty = vx * co - ux * si, vy * co - uy * si  # the tangent there
        rx, ry = c[0] - c[2], c[1] - c[3]
        return (rx * tx + ry * ty) / (_norm(rx, ry) * _norm(tx, ty))
    return Rel((p, centre, c_, a, b), f)


def span_of(c1, a1, c2, a2, v, sign):
    """Two rounds: centre to centre plus (1: overall) or less (-1: the gap between them) their radii is v."""
    def f(c):
        r1, r2 = math.hypot(c[2] - c[0], c[3] - c[1]), math.hypot(c[6] - c[4], c[7] - c[5])
        return math.hypot(c[4] - c[0], c[5] - c[1]) + sign * (r1 + r2) - v
    return Rel((c1, a1, c2, a2), f)


def ring_gap(c_, a, b, v):
    """Two circles round one centre: the one through b stands v outside the one through a."""
    return Rel((c_, a, b), lambda c: math.hypot(c[4] - c[0], c[5] - c[1]) - math.hypot(c[2] - c[0], c[3] - c[1]) - v)


def _off_value(kind, c):
    """An offset as it stands: a point's signed distance from a line (p, a, b), or a ring's gap (centre, a, b)."""
    if kind == "l":
        ux, uy = c[4] - c[2], c[5] - c[3]
        return (ux * (c[1] - c[3]) - uy * (c[0] - c[2])) / _norm(ux, uy)
    return math.hypot(c[4] - c[0], c[5] - c[1]) - math.hypot(c[2] - c[0], c[3] - c[1])


def off_same(kinds, pts, k):
    """The second offset of an outline's copy keeps the first's size (k: the other way round: -1)."""
    return Rel(tuple(pts), lambda c: _off_value(kinds[0], c[:6]) * k - _off_value(kinds[1], c[6:12]))


def carried(o, c_, a, tx, ty):
    """c_ is o turned by a about the origin and then moved by (tx, ty): a rigid copy."""
    co, si = math.cos(a), math.sin(a)
    return [Rel((o, c_), lambda c: co * c[0] - si * c[1] + tx - c[2]), Rel((o, c_), lambda c: si * c[0] + co * c[1] + ty - c[3])]


def turned(o, c_, a, ax=None):
    """c_ is o turned by a about the point ax (the origin when None)."""
    co, si = math.cos(a), math.sin(a)
    if ax is None:
        return [Rel((o, c_), lambda c: co * c[0] - si * c[1] - c[2]), Rel((o, c_), lambda c: si * c[0] + co * c[1] - c[3])]
    return [Rel((ax, o, c_), lambda c: c[0] + co * (c[2] - c[0]) - si * (c[3] - c[1]) - c[4]),
            Rel((ax, o, c_), lambda c: c[1] + si * (c[2] - c[0]) + co * (c[3] - c[1]) - c[5])]


def round_to_line(c_, a, p, q, v, k):
    """A round and a line: from the centre to the line, plus k radii, is v (k -1: the near edge, 1: the far edge, 0: the centre)."""
    def f(c):
        ux, uy = c[6] - c[4], c[7] - c[5]
        d = abs(ux * (c[1] - c[5]) - uy * (c[0] - c[4])) / _norm(ux, uy)
        return d + k * math.hypot(c[2] - c[0], c[3] - c[1]) - v
    return Rel((c_, a, p, q), f)


def round_to_point(c_, a, p, v, k):
    """A round and a point: from the centre to the point, plus k radii, is v."""
    return Rel((c_, a, p), lambda c: math.hypot(c[4] - c[0], c[5] - c[1]) + k * math.hypot(c[2] - c[0], c[3] - c[1]) - v)


def span_along(c1, a1, c2, a2, v, axis, k=1):
    """Two rounds: their edges stand v apart along the axis (0: X, 1: Y): the far edges (k 1) or the near ones (k -1)."""
    def f(c):
        r1, r2 = math.hypot(c[2] - c[0], c[3] - c[1]), math.hypot(c[6] - c[4], c[7] - c[5])
        return abs(c[4 + axis] - c[0 + axis]) + k * (r1 + r2) - v
    return Rel((c1, a1, c2, a2), f)


def round_point_along(c_, a, p, v, axis, k):
    """A round's far (k 1) or near (k -1) edge stands v from the point along the axis."""
    def f(c):
        return abs(c[4 + axis] - c[0 + axis]) + k * math.hypot(c[2] - c[0], c[3] - c[1]) - v
    return Rel((c_, a, p), f)


def on_ell(p, c_, a, b):
    """p lies on the ellipse."""
    def f(c):
        ux, uy, vx, vy, dx, dy = c[4] - c[2], c[5] - c[3], c[6] - c[2], c[7] - c[3], c[0] - c[2], c[1] - c[3]
        det = ux * vy - uy * vx
        if abs(det) < 1e-12:
            return 0.0
        return math.hypot((dx * vy - dy * vx) / det, (ux * dy - uy * dx) / det) - 1.0
    return Rel((p, c_, a, b), f)


def line_origin(a, b, v):
    """The line a…b, carried on, passes the origin at the distance v."""
    def f(c):
        ux, uy = c[2] - c[0], c[3] - c[1]
        return abs(ux * c[1] - uy * c[0]) / _norm(ux, uy) - v
    return Rel((a, b), f)


def origin_distance(a, v):
    def f(c):
        return math.hypot(c[0], c[1]) - v
    return Rel((a,), f)


def offset_x(a, b, v):
    return Rel((a, b), lambda c: c[2] - c[0] - v)


def offset_y(a, b, v):
    return Rel((a, b), lambda c: c[3] - c[1] - v)


def line_distance(p, a, b, v):
    """p stands v from the line a…b, signed: to its left is plus."""
    def f(c):
        ux, uy = c[4] - c[2], c[5] - c[3]
        return (ux * (c[1] - c[3]) - uy * (c[0] - c[2])) / _norm(ux, uy) - v
    return Rel((p, a, b), f)


def angle_is(a, b, c_, d, v):
    """From a→b round counterclockwise to c_→d: v radians."""
    def f(c):
        turn = math.atan2(c[7] - c[5], c[6] - c[4]) - math.atan2(c[3] - c[1], c[2] - c[0]) - v
        return math.atan2(math.sin(turn), math.cos(turn))
    return Rel((a, b, c_, d), f)


def angle_axis(a, b, base, first, v):
    """Between the line a→b and an axis going at angle base: v radians counterclockwise, from whichever is first."""
    def f(c):
        th = math.atan2(c[3] - c[1], c[2] - c[0])
        turn = (th - base if first == "axis" else base - th) - v
        return math.atan2(math.sin(turn), math.cos(turn))
    return Rel((a, b), f)


def symmetric_axis(a, b, axis):
    """a and b mirror each other in the sketch's X or Y axis."""
    if axis == "x":
        return [Rel((a, b), lambda c: c[0] - c[2]), Rel((a, b), lambda c: c[1] + c[3])]
    return [Rel((a, b), lambda c: c[0] + c[2]), Rel((a, b), lambda c: c[1] - c[3])]


def symmetric_origin(a, b):
    """a and b stand either side of the origin."""
    return [Rel((a, b), lambda c: c[0] + c[2]), Rel((a, b), lambda c: c[1] + c[3])]


def symmetric_point(a, b, c_):
    """a and b stand either side of the point c_."""
    return [Rel((a, b, c_), lambda c: c[0] + c[2] - 2 * c[4]), Rel((a, b, c_), lambda c: c[1] + c[3] - 2 * c[5])]


def symmetric(a, b, p, q):
    """a and b mirror each other in the line p…q."""
    def f(c):
        mx, my = (c[0] + c[2]) / 2, (c[1] + c[3]) / 2
        ux, uy = c[6] - c[4], c[7] - c[5]
        return (ux * (my - c[5]) - uy * (mx - c[4])) / _norm(ux, uy)
    return [Rel((a, b, p, q), f), perpendicular(a, b, p, q)]


def _reached(rels, pulled, fixed):
    """The relations the pulled points reach, through points that may move."""
    by_point = {}
    for r in rels:
        for p in r.points:
            by_point.setdefault(p, []).append(r)
    seen, todo, out = set(pulled), list(pulled), []
    done = set()
    while todo:
        for r in by_point.get(todo.pop(), ()):
            if id(r) in done:
                continue
            done.add(id(r))
            out.append(r)
            for p in r.points:
                if p not in seen and p not in fixed:
                    seen.add(p)
                    todo.append(p)
    return out


def _linear(a, b):
    """Solves a·y = b in place by Gauss with the biggest pivot; a row that falls to nothing (a relation saying what
    the others already say) gives 0."""
    n = len(b)
    order = list(range(n))
    for k in range(n):
        piv = max(range(k, n), key=lambda i: abs(a[order[i]][k]))
        order[k], order[piv] = order[piv], order[k]
        top = a[order[k]]
        if abs(top[k]) < 1e-14:
            continue
        for i in range(k + 1, n):
            row = a[order[i]]
            m = row[k] / top[k]
            if m:
                for j in range(k, n):
                    row[j] -= m * top[j]
                b[order[i]] -= m * b[order[k]]
    y = [0.0] * n
    for k in range(n - 1, -1, -1):
        top = a[order[k]]
        if abs(top[k]) < 1e-14:
            continue
        y[k] = (b[order[k]] - sum(top[j] * y[j] for j in range(k + 1, n))) / top[k]
    return y


def _row(r, x, fixed):
    """The relation's value at x and its slopes {(point, 0 or 1): slope}."""
    flat = [v for p in r.points for v in x[p]]
    value = r.f(flat)
    grad = {}
    for k in range(len(flat)):
        p = r.points[k // 2]
        if p in fixed:
            continue
        keep = flat[k]
        flat[k] = keep + DIFF
        hi = r.f(flat)
        flat[k] = keep - DIFF
        lo = r.f(flat)
        flat[k] = keep
        g = (hi - lo) / (2 * DIFF)
        if g:
            key = (p, k % 2)
            grad[key] = grad.get(key, 0.0) + g
    return value, grad


def _rank(rows):
    keys = sorted({k for row in rows for k in row})
    grid = [[row.get(k, 0.0) for k in keys] for row in rows]
    rank = 0
    for col in range(len(keys)):
        piv = max(range(rank, len(grid)), key=lambda i: abs(grid[i][col]), default=None)
        if piv is None or abs(grid[piv][col]) < 1e-6:
            continue
        grid[rank], grid[piv] = grid[piv], grid[rank]
        top = grid[rank]
        for i in range(rank + 1, len(grid)):
            m = grid[i][col] / top[col]
            if m:
                grid[i] = [v - m * t for v, t in zip(grid[i], top)]
        rank += 1
    return rank


def adds_nothing(coords, rels, new, fixed=()):
    """The new relations hold already and say nothing the others do not."""
    fixed = set(fixed)
    x = [list(c) for c in coords]
    if any(abs(_row(r, x, fixed)[0]) > 1e-7 for r in new):
        return False
    start = {p for r in new for p in r.points if p not in fixed}
    near = _reached(list(rels) + list(new), list(start), fixed)
    old = [_row(r, x, fixed)[1] for r in near if r not in new]
    return _rank(old + [_row(r, x, fixed)[1] for r in new]) == _rank(old)


def free_ways(coords, rels, fixed, points, held=()):
    """How the sketch can still move: (how many sizes are free, {point: the ways it can go, [] [(dx, dy)] or two}).
    held: more rows {(point, 0 or 1): slope} of what does not count as a move (a circle's rim point going round it)."""
    fixed = set(fixed)
    x = [list(c) for c in coords]
    rows = [_row(r, x, fixed)[1] for r in rels] + list(held)
    keys = sorted({(p, axis) for p in points if p not in fixed for axis in (0, 1)} | {k for row in rows for k in row})
    grid = [[row.get(k, 0.0) for k in keys] for row in rows]
    pivots, top = [], 0
    for col in range(len(keys)):  # reduced to steps, each pivot alone in its column
        piv = max(range(top, len(grid)), key=lambda i: abs(grid[i][col]), default=None)
        if piv is None or abs(grid[piv][col]) < 1e-6:
            continue
        grid[top], grid[piv] = grid[piv], grid[top]
        lead = grid[top][col]
        grid[top] = [v / lead for v in grid[top]]
        for i in range(len(grid)):
            m = grid[i][col]
            if i != top and m:
                grid[i] = [v - m * t for v, t in zip(grid[i], grid[top])]
        pivots.append(col)
        top += 1
    taken = set(pivots)
    basis = []
    for col in (c for c in range(len(keys)) if c not in taken):
        v = [0.0] * len(keys)
        v[col] = 1.0
        for r, pc in enumerate(pivots):
            v[pc] = -grid[r][col]
        basis.append(v)
    at = {k: i for i, k in enumerate(keys)}
    ways = {}
    for p in points:
        if p in fixed:
            ways[p] = []
            continue
        ix, iy = at[(p, 0)], at[(p, 1)]
        found = []
        for v in basis:
            dx, dy = v[ix], v[iy]
            for ux, uy in found:  # what the ways found so far do not give
                d = dx * ux + dy * uy
                dx, dy = dx - d * ux, dy - d * uy
            n = math.hypot(dx, dy)
            if n > 1e-7:
                found.append((dx / n, dy / n))
            if len(found) == 2:
                break
        ways[p] = found
    return len(basis), ways


def solve(coords, rels, targets, fixed=(), steps=40, tol=1e-9, seeds=()):
    """coords: [(x, y)…]; targets: {point: (x, y)}; fixed: points that never move; seeds: points to start from when
    nothing is pulled. Returns the new coords, or None when the relations cannot be met."""
    fixed = set(fixed)
    pulled = [p for p in targets if p not in fixed]
    if not pulled and not seeds:
        return None
    x = [list(c) for c in coords]
    for p in pulled:
        x[p] = list(targets[p])
    rels = _reached(rels, pulled + [p for p in seeds if p not in fixed], fixed)
    for _ in range(steps):
        rows, res = [], []
        for r in rels:
            value, grad = _row(r, x, fixed)
            res.append(-value)
            rows.append(grad)
        if max(map(abs, res), default=0.0) < tol:
            return [tuple(c) for c in x]
        m = len(rows)
        give = {p: LOOSE for r in rels for p in r.loose}
        give.update({p: PULLED for p in pulled})
        a = [[0.0] * m for _ in range(m)]
        for i in range(m):
            for j in range(i, m):
                small, big = (rows[i], rows[j]) if len(rows[i]) < len(rows[j]) else (rows[j], rows[i])
                s = sum(v * big[key] * give.get(key[0], 1.0) for key, v in small.items() if key in big)
                a[i][j] = a[j][i] = s
            a[i][i] += 1e-12
        y = _linear(a, res)
        for i in range(m):
            for key, v in rows[i].items():
                x[key[0]][key[1]] += y[i] * v * give.get(key[0], 1.0)
    return None


def _touch_error(obj, sign, x, y, r):
    """How far the circle (x, y, r) is from touching obj; sign: which way round it touches."""
    if obj[0] == "line":
        ax, ay, bx, by = obj[1:]
        ux, uy = bx - ax, by - ay
        n = math.hypot(ux, uy)
        return (ux * (y - ay) - uy * (x - ax)) / n - sign * r
    if obj[0] == "ellipse":
        return ell_nearest(*obj[1:], x, y)[1] - r
    if obj[0] == "circle":
        cx, cy, big = obj[1:]
        return math.hypot(x - cx, y - cy) - (big + r if sign > 0 else abs(big - r))
    return math.hypot(x - obj[1], y - obj[2]) - r


def _gauss(a, b):
    """Gauss with pivoting; None when singular."""
    n = len(b)
    m = [row[:] + [v] for row, v in zip(a, b)]
    for i in range(n):
        p = max(range(i, n), key=lambda k: abs(m[k][i]))
        if abs(m[p][i]) < 1e-12:
            return None
        m[i], m[p] = m[p], m[i]
        for k in range(i + 1, n):
            f = m[k][i] / m[i][i]
            for j in range(i, n + 1):
                m[k][j] -= f * m[i][j]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (m[i][n] - sum(m[i][j] * x[j] for j in range(i + 1, n))) / m[i][i]
    return x


def tangent_circles(objs, seeds, radius=None):
    """Every circle touching all objs: three of them, or two and a radius. objs: ("line", ax, ay, bx, by),
    ("circle", cx, cy, r), ("point", x, y). seeds: (x, y) places to start from. Returns [(x, y, r)]."""
    def signs(k):
        if k == len(objs):
            yield ()
            return
        for rest in signs(k + 1):
            for s in ((1,) if objs[k][0] in ("point", "ellipse") else (1, -1)):
                yield (s,) + rest

    found = []
    for combo in signs(0):
        def errors(v):
            r = radius if radius is not None else v[2]
            return [_touch_error(o, s, v[0], v[1], r) for o, s in zip(objs, combo)]

        for sx, sy in seeds:
            v = [sx, sy] if radius is not None else [sx, sy, sum(abs(e) for e in
                                                                 (_touch_error(o, s, sx, sy, 0.0) for o, s in zip(objs, combo))) / len(objs) or 1.0]
            for _step in range(40):
                e = errors(v)
                if max(abs(x) for x in e) < 1e-10:
                    break
                jac = []
                for i in range(len(e)):
                    row = []
                    for j in range(len(v)):
                        w = list(v)
                        w[j] += 1e-7
                        row.append((errors(w)[i] - e[i]) / 1e-7)
                    jac.append(row)
                d = _gauss(jac, [-x for x in e])
                if d is None:
                    break
                size = max(abs(x) for x in d)
                k = min(1.0, 1000.0 / size) if size > 0 else 1.0
                v = [a + b * k for a, b in zip(v, d)]
            else:
                continue
            r = radius if radius is not None else v[2]
            if r > 1e-6 and max(abs(x) for x in errors(v)) < 1e-9 and \
                    not any(math.hypot(q[0] - v[0], q[1] - v[1]) < 1e-6 and abs(q[2] - r) < 1e-6 for q in found):
                found.append((v[0], v[1], r))
    return found
