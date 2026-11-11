"""How a dimension is drawn: Settings ▸ Dimensions (size, arrows, value). Sizes are pixels, the same at any zoom."""
import math

from PySide6 import QtCore, QtGui, QtWidgets
from prostocad import theme

P = QtCore.QPointF
SIZES = {"Small": dict(text=11, arrow=6, gap=3, over=4, off=26), "Medium": dict(text=13, arrow=9, gap=4, over=5, off=34),
         "Large": dict(text=16, arrow=12, gap=5, over=6, off=44)}  # ProstoCAD 0.18 trial: its own sizes (the user, 2026-11-11:
# the new program's rule for where arrows and values go, the trial's sizes)


def _z(look):
    """The sizes of a dimension: Settings' size, grown with the sketch when it is zoomed in past the size it had at
    the last Fit (look["k"], never below 1: never smaller than readable; the user)."""
    k = max(1.0, float(look.get("k", 1.0)))
    return {key: v * k for key, v in SIZES[look["size"]].items()}


ARROWS = ("Filled", "Open", "Ticks", "Dots")
VALUES = ("Above Line", "On Tile", "In Gap")
LABELS = ("Value", "Name", "Both")  # what it reads: 40, d3 or d3 = 40
WHEEL = ("0.1", "0.01")  # what a click of the wheel changes a size by
DEFAULTS = dict(size="Medium", arrows="Filled", value="In Gap", label="Value", wheel="0.1")
CHOICES = dict(size=tuple(SIZES), arrows=ARROWS, value=VALUES, label=LABELS, wheel=WHEEL)
PREVIEW_PX_PER_MM = 4.6


def _arrow(g, tip, u, s, filled, ink):
    n = P(-u.y(), u.x())
    a, b = tip - u * s + n * (s * .32), tip - u * s - n * (s * .32)
    if filled:
        g.setBrush(ink)
        g.setPen(QtCore.Qt.NoPen)
        g.drawPolygon(QtGui.QPolygonF([tip, a, b]))
    else:
        g.setBrush(QtCore.Qt.NoBrush)
        g.setPen(QtGui.QPen(ink, 1.2))
        g.drawPolyline(QtGui.QPolygonF([a, tip, b]))


def _inks(p, ink):
    ink = QtGui.QColor(ink) if ink is not None else QtGui.QColor("#000000" if p["name"] == "light" else "#ffffff")  # as the constraint tiles
    quiet = QtGui.QColor(ink)
    quiet.setAlphaF(.75)
    return ink, quiet


def _text_box(g, text, z):
    f = QtGui.QFont(theme.FONT)
    f.setPixelSize(z["text"])
    g.setFont(f)
    fm = QtGui.QFontMetrics(f)
    return fm.horizontalAdvance(text) + 10, fm.height() + 4


def _text_size(text, look):
    f = QtGui.QFont(theme.FONT)
    f.setPixelSize(_z(look)["text"])
    fm = QtGui.QFontMetrics(f)
    return fm.horizontalAdvance(text) + 10, fm.height() + 4


def text_width(text, look):
    return _text_size(text, look)[0]


def beside_span(A, B, text, look, side=1):
    """A dimension too short for its value: where the value stands, past the end it reads towards (the user: never
    inside, it cannot be seen there). None when it fits. side: 1 past the end it reads towards, -1 past the other
    (a dimension stands on that side already), 0 above its own line, a row higher (both sides taken): the user,
    2026-11-11, "dimensions must not overlap"."""
    d = B - A
    n = math.hypot(d.x(), d.y())
    w, h = _text_size(text, look)
    if n >= w + 8 or n < 1e-9:
        return None
    u = d / n
    if u.x() < -1e-6 or (abs(u.x()) <= 1e-6 and u.y() > 0):  # as the painter turns it: the value on the reading side
        A, B, u = B, A, u * -1
    if side == 0:
        up = P(u.y(), -u.x())
        return (A + B) * .5 + up * ((1.5 * h + 6) if look["value"] == "Above Line" else (h + 6))
    return B + u * (w / 2 + 8) if side > 0 else A - u * (w / 2 + 8)


def reach_beside(A, B, text, look):
    """((along the line from A towards B, from, to) for each side a too-short dimension's value may stand on: what
    it would cover there, the arrows outside it too. {} when the value fits inside."""
    d = B - A
    n = math.hypot(d.x(), d.y())
    w, _h = _text_size(text, look)
    if n >= w + 8 or n < 1e-9:
        return {}
    u = d / n
    flip = u.x() < -1e-6 or (abs(u.x()) <= 1e-6 and u.y() > 0)
    out = {1: (n, n + w + 16), -1: (-(w + 16), 0.0)}  # along A -> B as the painter reads it
    if flip:
        out = {1: (-(w + 16), 0.0), -1: (n, n + w + 16)}
    return out


def beside_radius(a, b, text, look):
    """The value of a small radius or diameter: past the curve, on the line's way out. None when it fits."""
    d = b - a
    n = math.hypot(d.x(), d.y())
    w, _h = _text_size(text, look)
    if n >= w + 8 or n < 1e-9:
        return None
    return b + d / n * (w / 2 + 8)


def beside_angle(poly, text, look, at=None):
    """The value of a small angle: outside its arc, at the arc's point at if given. None when it fits."""
    w, h = _text_size(text, look)
    chord = poly[-1] - poly[0]
    n = math.hypot(chord.x(), chord.y())
    if n >= w + 8:
        return None
    mid = poly[len(poly) // 2]
    out = mid - (poly[0] + poly[-1]) * .5
    mid = at if at is not None else mid
    size = math.hypot(out.x(), out.y())
    if size < 1e-6:
        out, size = P(-chord.y(), chord.x()), n or 1.0
    return mid + out / size * (max(w, h) / 2 + 8)


def _value(g, r, text, look, p, ink):
    if look["value"] == "On Tile":
        g.setBrush(QtGui.QColor(p["hint_bg"]))
        g.setPen(QtGui.QPen(QtGui.QColor(p["edge_line"]), 1))
        g.drawRoundedRect(r, 4, 4)
    g.setPen(ink)
    g.drawText(r, QtCore.Qt.AlignCenter, text)


def _tips(g, tips, look, ink, s):
    """The ends of a dimension line: [(the place, the way its arrow points)…]."""
    if look["arrows"] in ("Filled", "Open"):
        for tip, u in tips:
            _arrow(g, tip, u, s, look["arrows"] == "Filled", ink)
    elif look["arrows"] == "Ticks":
        g.setPen(QtGui.QPen(ink, 1.6))
        for q, _u in tips:
            g.drawLine(q + P(-s * .4, s * .4), q + P(s * .4, -s * .4))
    else:
        g.setBrush(ink)
        g.setPen(QtCore.Qt.NoPen)
        for q, _u in tips:
            g.drawEllipse(q, s * .28, s * .28)


def _measure(g, A, B, text, look, p, ink, ends=(True, True), mid=None):
    """The dimension line A…B, level, with its ends and value; mid: where the value stands, its middle if not given."""
    z = _z(look)
    ink, quiet = _inks(p, ink)
    y = A.y()
    lifted = mid is not None and abs(mid.y() - y) > 1.0  # above its own line, a row higher (both sides taken)
    lift_y = mid.y() if lifted else y
    mid = (A + B) * .5 if mid is None else P(mid.x(), y)
    u = P(1, 0)
    w, h = _text_box(g, text, z)
    # too short for its arrows (and for its value between them): the arrows stand outside, pointing in, each on
    # a short tail (the user, 2026-11-11: the arrows of a short dimension ran into each other and under its value)
    lo, hi = min(A.x(), B.x()), max(A.x(), B.x())
    within = lo <= mid.x() - w / 2 and mid.x() + w / 2 <= hi and not lifted
    room = 2 * z["arrow"] + 4 + (w + 4 if within and look["value"] != "Above Line" else 0)
    outside = hi - lo < room and ends == (True, True) and look["arrows"] in ("Filled", "Open")
    if outside:
        g.setPen(QtGui.QPen(ink, 1.1))
        tail = z["arrow"] * 1.8
        g.drawLine(P(lo - tail, y), P(lo, y))
        g.drawLine(P(hi, y), P(hi + tail, y))
    if mid.x() - w / 2 > max(A.x(), B.x()):  # beside it: the line runs on to the value
        g.setPen(QtGui.QPen(ink, 1.1))
        g.drawLine(P(min(A.x(), B.x()), y), P(mid.x() - w / 2 - 2, y))
    elif mid.x() + w / 2 < min(A.x(), B.x()):
        g.setPen(QtGui.QPen(ink, 1.1))
        g.drawLine(P(max(A.x(), B.x()), y), P(mid.x() + w / 2 + 2, y))
    elif look["value"] == "In Gap" and abs(B.x() - A.x()) > w + 4:
        g.setPen(QtGui.QPen(quiet, 1))
        g.drawLine(A, mid - u * (w / 2 + 2))
        g.drawLine(mid + u * (w / 2 + 2), B)
    else:
        g.setPen(QtGui.QPen(ink, 1.1))
        g.drawLine(A, B)
    way = -1 if outside else 1
    _tips(g, [(q, u * (-way) if q is A else u * way) for q, on in ((A, ends[0]), (B, ends[1])) if on], look, ink, z["arrow"])
    r = QtCore.QRectF(mid.x() - w / 2, y - h - 2, w, h)
    if lifted:
        r.moveCenter(P(mid.x(), lift_y))
    elif look["value"] != "Above Line":
        r.moveCenter(mid)
    _value(g, r, text, look, p, ink)


def _level(g, A, B):
    """The painter turned so that A…B lies level and reads left to right; returns what turns a screen point into it."""
    d = B - A
    deg = math.degrees(math.atan2(d.y(), d.x()))
    if deg > 90.0001 or deg <= -90.0001:
        A, B, deg = B, A, deg + 180
    g.translate(A)
    g.rotate(deg)
    return (lambda q: _local(q, A, deg)), A, B


def _local(q, A, deg):
    co, si = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    d = q - A
    return P(d.x() * co + d.y() * si, -d.x() * si + d.y() * co)


def paint_span(g, a, b, A, B, text, look, p=None, ink=None, at=None):
    """A dimension of a…b on the line A…B, at any angle, screen points; at: where its value stands."""
    p = p or theme.cur()
    z = _z(look)
    g.save()
    local, A0, B0 = _level(g, A, B)
    e, f = local(a), local(b)
    AL, BL = P(0, 0), local(B0)
    _ink, quiet = _inks(p, ink)
    g.setPen(QtGui.QPen(quiet, 1))
    for q, Q in ((e, AL), (f, BL)) if A0 == A else ((f, AL), (e, BL)):  # a gap at the part, a little past the line
        way = 1.0 if Q.y() > q.y() else -1.0
        if abs(Q.y() - q.y()) > z["gap"]:
            g.drawLine(P(q.x(), q.y() + way * z["gap"]), P(Q.x(), Q.y() + way * z["over"]))
    _measure(g, AL, BL, text, look, p, ink, mid=None if at is None else local(at))
    g.restore()


def paint(g, a, b, text, look, p=None):
    """A dimension above the horizontal line a…b, in screen points. look: {size, arrows, value}."""
    off = _z(look)["off"]
    paint_span(g, a, b, P(a.x(), a.y() - off), P(b.x(), b.y() - off), text, look, p)


def clear_of_centre(look):
    """How far from a circle's centre its value must stand, px: the centre's mark must not cover it (the user)."""
    return _z(look)["text"] * 3.6


def paint_radius(g, a, b, text, look, p=None, ink=None, both=False, at=None):
    """From a (the centre, or the far side for a diameter) to b on the curve. The value stands at at, on the line or
    past the curve: the line then runs on to it and the arrow comes from outside (the user)."""
    p = p or theme.cur()
    g.save()
    local, A0, B0 = _level(g, a, b)
    ends = (True, both) if A0 != a else (both, True)
    L = local(B0).x()
    x = local(at).x() if at is not None else L / 2
    z = _z(look)
    ink, quiet = _inks(p, ink)
    w, h = _text_box(g, text, z)
    lo, hi = min(0.0, x - w / 2), max(L, x + w / 2)
    if look["value"] == "In Gap":
        g.setPen(QtGui.QPen(quiet, 1))
        if x - w / 2 - 2 > lo:
            g.drawLine(P(lo, 0), P(x - w / 2 - 2, 0))
        if x + w / 2 + 2 < hi:
            g.drawLine(P(x + w / 2 + 2, 0), P(hi, 0))
    else:
        g.setPen(QtGui.QPen(ink, 1.1))
        g.drawLine(P(lo, 0), P(hi, 0))
    tips = []
    if ends[0]:
        tips.append((P(0, 0), P(1, 0) if lo < 0 else P(-1, 0)))
    if ends[1]:
        tips.append((P(L, 0), P(-1, 0) if hi > L else P(1, 0)))
    _tips(g, tips, look, ink, z["arrow"])
    r = QtCore.QRectF(x - w / 2, -h - 2, w, h)
    if look["value"] != "Above Line":
        r.moveCenter(P(x, 0))
    _value(g, r, text, look, p, ink)
    g.restore()


def paint_angle(g, poly, text, look, p=None, ink=None, mid=None, reach=()):
    """An angle along the screen points poly, its ends arrowed, the value at its middle. reach: [(from, to)]: the thin
    lines that carry each side out to the arc."""
    p = p or theme.cur()
    z = _z(look)
    ink_, quiet = _inks(p, ink)
    g.setPen(QtGui.QPen(quiet, 1))
    for a, b in reach:
        d = b - a
        n = math.hypot(d.x(), d.y())
        if n > z["gap"]:
            u = d / n
            g.drawLine(a + u * z["gap"], b + u * z["over"])
    w, h = _text_box(g, text, z)
    mid = poly[len(poly) // 2] if mid is None else mid
    g.setBrush(QtCore.Qt.NoBrush)
    g.setPen(QtGui.QPen(quiet if look["value"] == "In Gap" else ink_, 1.1))
    clear = (w / 2 + 3) if look["value"] == "In Gap" else 0
    for q0, q1 in zip(poly, poly[1:]):
        if math.hypot((q0 - mid).x(), (q0 - mid).y()) > clear:
            g.drawLine(q0, q1)
    s = z["arrow"]
    for tip, nxt in ((poly[0], poly[1]), (poly[-1], poly[-2])):
        d = tip - nxt
        n = math.hypot(d.x(), d.y()) or 1.0
        u = d / n
        if look["arrows"] in ("Filled", "Open"):
            _arrow(g, tip, u, s, look["arrows"] == "Filled", ink_)
        elif look["arrows"] == "Ticks":
            g.setPen(QtGui.QPen(ink_, 1.6))
            k = P(-u.y(), u.x())
            g.drawLine(tip + (u + k) * (s * .4), tip - (u + k) * (s * .4))
        else:
            g.setBrush(ink_)
            g.setPen(QtCore.Qt.NoPen)
            g.drawEllipse(tip, s * .28, s * .28)
    r = QtCore.QRectF(mid.x() - w / 2, mid.y() - h / 2, w, h)
    if look["value"] == "Above Line":
        r.moveCenter(mid + P(0, -h * .8))
    _value(g, r, text, look, p, ink_)


class Preview(QtWidgets.QWidget):
    """Settings: a line of 100 mm with its dimension, at the scale of the sketch samples; shows the choices at once."""

    def __init__(self, look):
        super().__init__()
        self.look = look
        self.setFixedSize(round(100 * PREVIEW_PX_PER_MM) + 44, 108)

    def paintEvent(self, _e):
        p = theme.cur()
        g = QtGui.QPainter(self)
        g.setRenderHint(QtGui.QPainter.Antialiasing)
        g.fillRect(self.rect(), QtGui.QColor(p["view_bottom"]))
        a, b = P(22, self.height() - 16), P(self.width() - 22, self.height() - 16)
        g.setPen(QtGui.QPen(QtGui.QColor(p["geometry"]), 1.6))
        g.drawLine(a, b)
        g.setBrush(QtGui.QColor(p["geometry"]))
        for q in (a, b):
            g.drawEllipse(q, 2.5, 2.5)
        paint(g, a, b, {"Name": "d0", "Both": "d0 = 100.00"}.get(self.look.get("label"), "100.00"), dict(self.look, k=1.0), p)
        g.end()
