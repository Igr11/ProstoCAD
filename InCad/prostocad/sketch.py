"""The sketch view and its Line.

The Line draws as the professional CADs do: click, click, click — each click ends one line and starts the next; a
click on the chain's first point closes it. While a line is drawn its length and angle stand beside it: type a number
and the line holds it, Tab goes to the other, Enter places the point.

Magnets only light up, the click decides: the line's end always stays under the pointer. Within 8 px of the pointer
the nearest place a click would go is lit (one at a time), named (End, Midpoint, Perpendicular, Intersection, On line,
Horizontal, Vertical…) and tied to the pointer by a light dashed line; a click while it is lit lands exactly there.
Resting the pointer on a point for a moment takes it up (a small diamond): level with it or straight above it a
dashed guide runs from it, and where two guides cross an Intersection lights up. Every point rested on stays taken
until the chain (or the shape) ends: no limit, and passing over it again does not drop it (the user).
Resting on a line while a line is drawn makes the new line parallel to it, without a click; the right button lets go.

A typed size holds until the point is placed (click / Enter) or the right button lets it go; Tab holds the size it
leaves at the value it shows (typed or not) and lets go the one it moves to: that one follows the pointer, selected,
until a number is typed or the next Tab holds it. So Tab, Tab, Tab: length held, then angle held, then length again. The right mouse button stops what is going on: a
held size first, then the chain; then the Line stops and the sketch's menu (Finish
Sketch…) comes up with the same click. Esc: the chain, then the Line. Trackpad: a pinch and the wheel zoom about the pointer (the wheel never pans), the middle button pans."""
import itertools
import time
import ast
import math
import re

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from prostocad import dimension, log, sketch_solve as sk, theme, units
from prostocad.constraint_list import ConstraintTools
from prostocad.sketch_corner import CORNER_TOOLS, CornerTools
from prostocad.sketch_image import ImageTools
from prostocad.sketch_move import MoveTools
from prostocad.sketch_mirror import MirrorTools
from prostocad.sketch_offset import OffsetTools
from prostocad.sketch_pattern import PatternTools
from prostocad.sketch_polygon import PolygonTools
from prostocad.sketch_text import TextTools
from prostocad.sketch_slot import SLOT_ARC, SLOT_COMMANDS, SLOT_LINE, SLOT_NAMES, SLOT_NEXT, SLOT_PROMPTS, SLOTS, SlotTools
from prostocad.sketch_trim import TRIM_TOOLS, TrimTools

P = QtCore.QPointF
SNAP_PX = 8.0
SPL_GROUP = 64  # a spline's pieces looked at in groups of this many by the magnets
PICK_POINT_PX = 8.0  # a point is taken this near, before any line under it
PICK_LINE_PX = 6.0
RECT_NAMES = {"rect": "Rectangle", "rect_center": "Center Rectangle", "rect_3pt": "3-Point Rectangle",
              "rect_center_3pt": "3-Point Center Rectangle", "rect_rounded": "Rounded Rectangle",
              "arc": "3-Point Arc", "arc_center": "Center Arc", "arc_tangent": "Tangent Arc",
              "circle": "Circle", "circle_3pt": "3-Point Circle", "circle_tangent": "Tangent Circle",
              "ellipse": "Ellipse", "ellipse_3pt": "3-Point Ellipse", "ellipse_centre": "3-Point Ellipse",
              "ellipse_rot": "3-Point Ellipse", "arc_ellipse": "Elliptical Arc", "polygon": "Polygon", "point": "Point", "text": "Text", **SLOT_NAMES}  # the tools drawn in picked stages
RECT_PROMPTS = {
    "rect": ("Click the first corner", "Click the opposite corner, or type the sizes  Tab  Enter", ""),
    "rect_center": ("Click the centre", "Click a corner, or type the sizes  Tab  Enter", ""),
    "rect_3pt": ("Click where the first side starts", "Click where it ends, or type its length  Tab  angle  Enter",
                 "Click the height, or type it  Enter"),
    "rect_center_3pt": ("Click the centre", "Click the middle of a side, or type the side's length  Tab  angle  Enter",
                        "Click the height, or type it  Enter"),
    "rect_rounded": ("Click the first corner", "Click the opposite corner, or type the sizes  Tab  Enter",
                     "Click the corners' radius, or type it  Enter"),
    "arc": ("Click where the arc starts", "Click where it ends, or type the chord  Tab  angle  Enter",
            "Click a point on the arc, or type its radius  Enter"),
    "arc_center": ("Click the centre", "Click where the arc starts, or type the radius  Tab  angle  Enter",
                   "Click where it ends, or type its sweep  Enter"),
    "arc_tangent": ("Click a line or an arc where the arc starts", "Click where it ends, or type the radius  Enter", ""),
    "circle": ("Click the centre", "Click a point on the circle, or type its diameter  Enter", ""),
    "circle_3pt": ("Click the first point", "Click the second point, or type the chord  Tab  angle  Enter",
                   "Click the third point, or type the diameter  Enter"),
    "circle_tangent": ("Click a line, an arc, a circle or a point", "Click the second one, or type the diameter  Tab",
                       "Click the third one, or type the diameter  Enter"),
    "ellipse": ("Click the centre", "Click the end of the first axis, or type its length  Tab  angle  Enter",
                "Click a point on the ellipse, or type the second axis  Enter"),
    "arc_ellipse": ("Click the centre", "Click the end of the first axis, or type its length  Tab  angle  Enter",
                    "Click a point on the ellipse, or type the second axis  Enter",
                    "Click where the arc starts, or type its angle  Enter",
                    "Click where it ends, or type its sweep  Enter  ·  Space: the other way"),
    "ellipse_3pt": ("Click the first corner of its frame  ·  Space: from the centre",
                    "Click the opposite corner, or type the sizes  Tab  Enter", ""),
    "ellipse_rot": ("Click where the first side starts  ·  Space: from a corner",
                    "Click where it ends, at any angle, or type its length  Tab  angle  Enter",
                    "Click the height, or type it  Enter"),
    "ellipse_centre": ("Click the centre  ·  Space: at an angle",
                       "Click a corner of its frame, or type the sizes  Tab  Enter", ""),
    "point": ("Click where the point goes", "", ""),
    "text": ("Click the first corner of its box  ·  Space: rectangle, ellipse, circle",
             "Click the opposite corner, or type the sizes  Tab  Enter  ·  Space: rectangle, ellipse, circle", ""),
    "polygon": ("Click the centre  ·  Space: the circle inside or through the corners",
                "Click the middle of a side  ·  Space: the circle", ""),
    **SLOT_PROMPTS,
}
ARC_TOOLS = ("arc", "arc_center", *SLOT_ARC, "arc_tangent", "circle", "circle_3pt", "circle_tangent")
CIRCLE_TOOLS = ("circle", "circle_3pt", "circle_tangent")
CENTER_ARCS = ("arc_center", *SLOT_ARC)
ROUND_LINE = ("dim_rl_c", "dim_rl_gap", "dim_rl_far")  # a round and a line: from its centre, its near edge (the gap), its far edge
ROUND_POINT = ("dim_rp_c", "dim_rp_gap", "dim_rp_far", "dim_rp_gap_x", "dim_rp_gap_y", "dim_rp_far_x", "dim_rp_far_y")  # a round and a point: the same, and along X and Y
ROUND_ANY = ROUND_LINE + ROUND_POINT
SPANS = ("dim_c2c", "dim_over", "dim_gap", "dim_far_x", "dim_far_y", "dim_gap_x", "dim_gap_y")  # two round ends: centre to centre, outer edges, inner edges, far and near edges along X and along Y
FRAME_TOOLS = ("ellipse_3pt", "ellipse_centre")  # drawn by the frame, as Rectangle and Center Rectangle
ELLIPSE_TOOLS = ("ellipse", "ellipse_rot", "arc_ellipse", *FRAME_TOOLS)
PICKING_TOOLS = ("constraint", "circle_tangent", "dimension", "trim", "extend", "sk_offset", "sk_mirror", "sk_pattern", *CORNER_TOOLS)  # they take what is under the pointer: no magnets
DWELL_MS = 600
POINT_HINTS = ("grid", "point", "mid", "arc_mid", "x", "cross", "perp", "half", "square", "along", "sweep", "quad", "ell_x", "tan_ell")  # the hints that are a place, not a direction
# what each constraint takes, in the order it is picked: each way is a row of slots, each slot the kinds it accepts
_ANY = ("line", "arc")
# The sketch's own X and Y axes and the origin (where they cross) are objects of the constraints as they are in the old
# program (the user, 2026-11-14): a point on an axis or in the origin, a line on, parallel or square to an axis, an arc's
# centre in the origin, a circle touching an axis. Each way below is a pair of picks, in either order.
CONSTRAINTS = {
    "Coincident": ((("point",), ("point", "line", "arc", "ell")), (("line", "arc", "ell"), ("point",)),
                   (("point",), ("origin", "axis")), (("origin", "axis"), ("point",)),
                   (("line",), ("axis",)), (("axis",), ("line",)), (("arc",), ("origin",)), (("origin",), ("arc",))),
    "Concentric": ((("arc",), ("arc",)), (("arc",), ("origin",)), (("origin",), ("arc",))),
    "Horizontal": ((("line",),), (("point",), ("point",)), (("point",), ("origin",)), (("origin",), ("point",))),
    "Vertical": ((("line",),), (("point",), ("point",)), (("point",), ("origin",)), (("origin",), ("point",))),
    "Parallel": ((("line",), ("line",)), (("line",), ("axis",)), (("axis",), ("line",))),
    "Collinear": ((("line",), ("line",)), (("line",), ("axis",)), (("axis",), ("line",))),
    "Perpendicular": ((("line",), ("line",)), (("line",), ("axis",)), (("axis",), ("line",))),
    "Tangent": ((("line",), ("arc", "ell")), (("arc",), ("line", "arc", "ell")), (("ell",), ("line", "arc")),
                (("arc",), ("axis",)), (("axis",), ("arc",))),
    "Equal": ((("line",), ("line",)), (("arc",), ("arc",))),
    "Symmetric": tuple(((k,), (k,), ("line", "point")) for k in ("point", "line", "arc", "ell", "spl")),
    "Fix": ((("point",),),),
}
CONSTRAINT_NEEDS = {"Coincident": "a point and a point, a line, an arc, an ellipse, an axis or the origin", "Concentric": "two arcs, or an arc and the origin",
                    "Horizontal": "one line or two points (or a point and the origin)", "Vertical": "one line or two points (or a point and the origin)",
                    "Parallel": "two lines, or a line and an axis", "Collinear": "two lines, or a line and an axis", "Perpendicular": "two lines, or a line and an axis",
                    "Tangent": "a line and an arc or an ellipse, or two arcs, or an arc and an ellipse, or an arc and an axis", "Equal": "two lines or two arcs",
                    "Symmetric": "two like figures and a line, an axis or a point", "Fix": "one point"}


def _len(v):
    return math.hypot(v.x(), v.y())


def _parse(text):
    """The first number in the text: "12", "12.5 mm", "-45° (+315°)" (the angle box shows both)."""
    import re
    m = re.search(r"[-+]?\d+(?:[.,]\d*)?|[-+]?[.,]\d+", text)
    return float(m.group(0).replace(",", ".")) if m else None


def _angle_text(a, signed):
    """-45° shown with its 0…360 twin in brackets when the angle is below the level (Settings ▸ Angle ±180)."""
    if signed and a < -1e-9:
        return "%.1f° (+%.1f°)" % (a, a % 360.0)
    return "%.1f°" % a


def _on_segment(p, a, b, eps=1e-6):
    """p lies on the segment a-b (its ends included)."""
    u = b - a
    n = _len(u)
    if n < 1e-12:
        return _len(p - a) <= eps
    t = QtCore.QPointF.dotProduct(p - a, u) / (n * n)
    return -eps <= t <= 1 + eps and abs(u.x() * (p - a).y() - u.y() * (p - a).x()) / n <= eps


def _dist_to_line(p, a, u, n):
    """Distance from p to the infinite line through a along u (|u| = n)."""
    return abs(u.x() * (p - a).y() - u.y() * (p - a).x()) / n


def _cross(a, b, c, d):
    """(t on a-b, s on c-d, point) where the segments a-b and c-d cross, or None (parallel / apart)."""
    r, q = b - a, d - c
    den = r.x() * q.y() - r.y() * q.x()
    if abs(den) < 1e-12:
        return None
    w = c - a
    t = (w.x() * q.y() - w.y() * q.x()) / den
    s = (w.x() * r.y() - w.y() * r.x()) / den
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= s <= 1 + 1e-9:
        return t, s, a + r * t
    return None


TAU = 2 * math.pi


def _ang(v):
    return math.atan2(v.y(), v.x())


def _arc_of(c, a, b):
    """(radius, start angle, sweep) of the counterclockwise arc round c from a to b (sweep in (0, 2π])."""
    r = _len(a - c)
    a0 = _ang(a - c)
    sweep = (_ang(b - c) - a0) % TAU
    return r, a0, (sweep if sweep > 1e-9 else TAU)


def _in_sweep(ang, a0, sweep, eps=1e-9):
    return (ang - a0) % TAU <= sweep + eps


def _ell_at(c, u, v, t):
    """The ellipse round c with the half axes (or conjugate halves) u and v."""
    return c + u * math.cos(t) + v * math.sin(t)


def _ell_t(c, u, v, p):
    """The parameter of p, a place on the ellipse."""
    det = u.x() * v.y() - u.y() * v.x()
    if abs(det) < 1e-12:
        return 0.0
    d = p - c
    return math.atan2((u.x() * d.y() - u.y() * d.x()) / det, (d.x() * v.y() - d.y() * v.x()) / det)


def _ell_angle(u, v, t):
    """The true angle at parameter t, from the first axis towards the second."""
    return math.atan2(_len(v) * math.sin(t), _len(u) * math.cos(t))


def _ell_param(u, v, th):
    """The parameter at a true angle: the way back from _ell_angle."""
    return math.atan2(math.sin(th) / _len(v), math.cos(th) / _len(u))


def _dist_seg(p, a, b):
    d = b - a
    n2 = QtCore.QPointF.dotProduct(d, d)
    t = max(0.0, min(1.0, QtCore.QPointF.dotProduct(p - a, d) / n2)) if n2 > 1e-12 else 0.0
    return _len(a + d * t - p)


def _on_circle(c, r, ang):
    return P(c.x() + r * math.cos(ang), c.y() + r * math.sin(ang))


def _circle_through(a, b, c):
    """The centre of the circle through a, b, c, or None when they are on one line."""
    ax, ay, bx, by, cx, cy = a.x(), a.y(), b.x(), b.y(), c.x(), c.y()
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    return P(ux, uy)


def _seg_circle(a, b, c, r):
    """[(t on a-b, point)] where the segment a-b meets the circle (c, r)."""
    d = b - a
    f = a - c
    A = QtCore.QPointF.dotProduct(d, d)
    if A < 1e-18:
        return []
    B = 2 * QtCore.QPointF.dotProduct(f, d)
    C = QtCore.QPointF.dotProduct(f, f) - r * r
    disc = B * B - 4 * A * C
    if disc < 0:
        return []
    out = []
    for sgn in ((-1, 1) if disc > 1e-18 else (-1,)):
        t = (-B + sgn * math.sqrt(disc)) / (2 * A)
        if -1e-9 <= t <= 1 + 1e-9:
            out.append((t, a + d * t))
    return out


class _Near:
    """Which of these segments come near a box or a circle: a quick look at all of them at once before the exact
    crossing of each (a hundred lines against a spline's pieces took a sixth of a second at every click)."""
    EPS = 1e-6

    def __init__(self, segs):
        self.a = np.array([(p.x(), p.y()) for p, _q in segs], float).reshape(-1, 2)
        self.b = np.array([(q.x(), q.y()) for _p, q in segs], float).reshape(-1, 2)
        self.lo, self.hi = np.minimum(self.a, self.b) - self.EPS, np.maximum(self.a, self.b) + self.EPS

    def box(self, p, q, start=0):
        x0, x1 = min(p.x(), q.x()), max(p.x(), q.x())
        y0, y1 = min(p.y(), q.y()), max(p.y(), q.y())
        lo, hi = self.lo[start:], self.hi[start:]
        return (np.nonzero((lo[:, 0] <= x1) & (hi[:, 0] >= x0) & (lo[:, 1] <= y1) & (hi[:, 1] >= y0))[0] + start).tolist()

    def boxes(self, segs):
        """(index in segs, index here) of every pair whose boxes meet."""
        other = _Near(segs)
        meet = np.all((other.lo[:, None, :] <= self.hi[None]) & (other.hi[:, None, :] >= self.lo[None]), axis=2)
        return np.argwhere(meet).tolist()

    def circle(self, c, r):
        m = np.array((c.x(), c.y()))
        d = self.b - self.a
        n2 = np.maximum((d * d).sum(axis=1), 1e-30)
        t = np.clip(((m - self.a) * d).sum(axis=1) / n2, 0.0, 1.0)
        near = np.hypot(*(self.a + d * t[:, None] - m).T)
        far = np.maximum(np.hypot(*(self.a - m).T), np.hypot(*(self.b - m).T))
        slack = self.EPS + 1e-6 * (r + far)
        return np.nonzero((near <= r + slack) & (far >= r - slack))[0].tolist()


def _circle_circle(c1, r1, c2, r2):
    """Points where two circles meet."""
    d = _len(c2 - c1)
    if d < 1e-12 or d > r1 + r2 + 1e-9 or d < abs(r1 - r2) - 1e-9:
        return []
    x = (d * d + r1 * r1 - r2 * r2) / (2 * d)
    h = math.sqrt(max(0.0, r1 * r1 - x * x))
    u = (c2 - c1) / d
    m = c1 + u * x
    n = P(-u.y(), u.x())
    return [m + n * h, m - n * h] if h > 1e-12 else [m]


CONSTRAINT_TIES = {  # the constraints set by hand, as equations on their points
    "con_on_line": sk.on_line, "con_on_arc": sk.same_distance, "con_par": sk.parallel, "con_perp": sk.perpendicular,
    "con_col": sk.collinear, "con_eq": sk.equal_length, "con_eq_r": sk.same_radius, "con_sym": sk.symmetric,
    "con_sym_ax": sk.symmetric_axis, "con_sym_o": sk.symmetric_origin, "con_sym_pt": sk.symmetric_point,
    "con_touch": sk.line_touches, "con_dist": sk.line_at_radius, "con_circles": sk.circles_touch,
    "dim_c2c": lambda c1, _a1, c2, _a2, v: sk.radius_is(c1, c2, v), "dim_over": lambda c1, a1, c2, a2, v: sk.span_of(c1, a1, c2, a2, v, 1),
    "dim_gap": lambda c1, a1, c2, a2, v: sk.span_of(c1, a1, c2, a2, v, -1),
    "dim_rp_c": lambda c, a, p, v: sk.round_to_point(c, a, p, v, 0), "dim_rp_gap": lambda c, a, p, v: sk.round_to_point(c, a, p, v, -1),
    "dim_rp_far": lambda c, a, p, v: sk.round_to_point(c, a, p, v, 1),
    "dim_rl_c": lambda c, a, p, q, v: sk.round_to_line(c, a, p, q, v, 0), "dim_rl_gap": lambda c, a, p, q, v: sk.round_to_line(c, a, p, q, v, -1),
    "dim_rl_far": lambda c, a, p, q, v: sk.round_to_line(c, a, p, q, v, 1),
    "dim_far_x": lambda c1, a1, c2, a2, v: sk.span_along(c1, a1, c2, a2, v, 0),
    "dim_far_y": lambda c1, a1, c2, a2, v: sk.span_along(c1, a1, c2, a2, v, 1),
    "dim_gap_x": lambda c1, a1, c2, a2, v: sk.span_along(c1, a1, c2, a2, v, 0, -1),
    "dim_gap_y": lambda c1, a1, c2, a2, v: sk.span_along(c1, a1, c2, a2, v, 1, -1),
    "dim_rp_gap_x": lambda c, a, p, v: sk.round_point_along(c, a, p, v, 0, -1), "dim_rp_gap_y": lambda c, a, p, v: sk.round_point_along(c, a, p, v, 1, -1),
    "dim_rp_far_x": lambda c, a, p, v: sk.round_point_along(c, a, p, v, 0, 1), "dim_rp_far_y": lambda c, a, p, v: sk.round_point_along(c, a, p, v, 1, 1),
    "pat_xy": lambda a, b, q: [sk.offset_x(a, b, q[0]), sk.offset_y(a, b, q[1])],
    "pat_rig": lambda a, b, q: sk.carried(a, b, *q),
    "pat_rot": lambda *q: sk.turned(q[-3], q[-2], q[-1], q[0] if len(q) == 4 else None), "dim_ring": sk.ring_gap, "off_ll": lambda *q: sk.off_same("ll", q[:6], q[6]), "off_lr": lambda *q: sk.off_same("lr", q[:6], q[6]),
    "off_rl": lambda *q: sk.off_same("rl", q[:6], q[6]), "off_rr": lambda *q: sk.off_same("rr", q[:6], q[6]),
    "dim_len": sk.radius_is, "dim_r": sk.radius_is, "dim_d": lambda c, a, v: sk.radius_is(c, a, v / 2),
    "dim_dx": sk.offset_x, "dim_dy": sk.offset_y, "dim_par": sk.line_distance, "dim_ang": sk.angle_is,
    "dim_x": sk.x_is, "dim_y": sk.y_is, "dim_o": sk.origin_distance, "dim_lo": sk.line_origin,
    "con_apart": sk.circles_apart, "con_axis": sk.axis_at_radius, "con_ell_line": sk.line_touches_ell,
    "con_ell_arc": sk.circle_touches_ell, "con_on_ell": sk.on_ell, "con_ell_at": sk.touches_ell_at,
    "poly_round": sk.same_distance,  # a polygon's corners stand on one circle
    "slot_end": sk.middle, "slot_ray": sk.on_line,  # a slot's end: its centre between its two corners, in line with the arc's centre
    "ell_cut": lambda s, e, c, a, b: [sk.on_ell(s, c, a, b), sk.on_ell(e, c, a, b)],  # an elliptical arc's two ends
    "ell_frame": lambda c, a, b, p0, p1, p2, p3: sk.middle(a, p0, p1) + sk.middle(b, p1, p2) + sk.middle(c, p0, p2)
    + sk.middle(c, p1, p3),
    "con_same": lambda a, b: [sk.same_x(a, b), sk.same_y(a, b)],
    "con_centre": lambda a, b: [sk.same_x(a, b), sk.same_y(a, b)],
}


def _mark_glyph(g, name, c, s, col, w=1.5):
    """A constraint's mark: lines only, in one colour, so it takes the theme by itself. s: half its size."""
    g.save()
    g.translate(c)
    g.setPen(QtGui.QPen(col, w, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    g.setBrush(QtCore.Qt.NoBrush)

    def line(a, b, x, y):
        g.drawLine(P(a * s, b * s), P(x * s, y * s))

    if name == "Coincident":
        g.drawEllipse(P(0, 0), s * .62, s * .62)
        g.setBrush(col)
        g.drawEllipse(P(0, 0), s * .2, s * .2)
    elif name == "Concentric":
        g.drawEllipse(P(0, 0), s * .85, s * .85)
        g.drawEllipse(P(0, 0), s * .42, s * .42)
    elif name == "Horizontal":
        line(-.9, 0, .9, 0)
        line(-.9, -.4, -.9, .4)
        line(.9, -.4, .9, .4)
    elif name == "Vertical":
        line(0, -.9, 0, .9)
        line(-.4, -.9, .4, -.9)
        line(-.4, .9, .4, .9)
    elif name == "Parallel":
        line(-.6, .85, .05, -.85)
        line(-.05, .85, .6, -.85)
    elif name == "Collinear":
        line(-1, .7, 1, -.7)
        g.setBrush(col)
        g.drawEllipse(P(-.45 * s, .315 * s), s * .17, s * .17)
        g.drawEllipse(P(.45 * s, -.315 * s), s * .17, s * .17)
    elif name == "Perpendicular":
        line(-.85, .8, .85, .8)
        line(0, .8, 0, -.85)
    elif name == "Tangent":
        g.drawEllipse(P(0, .25 * s), s * .55, s * .55)
        line(-1, -.3, 1, -.3)
    elif name == "Equal":
        line(-.75, -.32, .75, -.32)
        line(-.75, .32, .75, .32)
    elif name == "Symmetric":
        line(0, -.95, 0, .95)
        g.drawPolyline(QtGui.QPolygonF([P(-.28 * s, -.5 * s), P(-.9 * s, 0), P(-.28 * s, .5 * s)]))
        g.drawPolyline(QtGui.QPolygonF([P(.28 * s, -.5 * s), P(.9 * s, 0), P(.28 * s, .5 * s)]))
    elif name == "Fix":
        line(-.85, .8, .85, .8)
        for k in (-.6, 0, .6):
            line(k, .8, k - .35, 1.1)
        g.drawPolygon(QtGui.QPolygonF([P(0, -.75 * s), P(.55 * s, .8 * s), P(-.55 * s, .8 * s)]))
    g.restore()


class ValueBox(QtWidgets.QLineEdit):
    """A size beside the line. It shows the live value; what is typed waits — nothing moves while the digits go in
    (1, 12, 123… was a jumping line: the user) — and applies on Tab (holds this size, on to the other) or Enter."""
    tabbed = QtCore.Signal()
    entered = QtCore.Signal()
    cancelled = QtCore.Signal()

    def move(self, x, y):
        """Never over the browser: the work field starts at its edge (the user). A sketch turned in 3D: by its point."""
        x, y = self.parent().tilted(x, y)
        super().move(max(x, self.parent().left_inset + 4), y)

    def __init__(self, unit, parent):
        super().__init__(parent)
        self.unit = unit
        self.typed = None  # the size this box holds (applied)
        self.pending = None  # what is being typed, not applied yet
        self.prefix = ""  # Ø for a circle's diameter
        self.setObjectName("ValueBox")
        self.setFixedSize(76, 20)  # the height follows the text size (set_text_px)
        self.textEdited.connect(self._edited)
        self.hide()

    def _edited(self, text):
        self.pending = text
        self._fit()

    def apply(self):
        """Tab / Enter: what was typed becomes the held size (±180 below the level: 45 typed is −45, the user)."""
        if self.pending is None:
            return
        value = _parse(self.pending)
        if self.unit == "mm":  # the file's units, or its own: "2 in", "10 mm", 1 1/2
            exact = _eval(units.literals(self.pending), {})
            value = units.to_mm(exact if exact is not None else value) if exact is not None or value is not None else None
        if value is not None and self.unit == "deg" and self.below() and "-" not in self.pending and value > 0:
            value = -value
        log.say("box", self.unit, "applies", repr(self.pending), "->", value)
        self.pending = None
        if value is not None:
            self.typed = value
            if self.unit == "deg":
                self.setText(_angle_text(value, getattr(self.parent(), "angle_360", True) is False))
                self._fit()

    def below(self):
        """The angle box of a view set to ±180 whose line now points below the level."""
        v = self.parent()
        return getattr(v, "angle_360", True) is False and v._pointer_below()

    def _fit(self):
        """Wide enough for "-45.0° (+315.0°)", tall enough for the text size."""
        fm = QtGui.QFontMetrics(self.font())
        self.setFixedWidth(max(76, fm.horizontalAdvance(self.text() + "  ") + 12, fm.horizontalAdvance("000.00 mm  " if units.now == "mm" else "00.0000 in  ") + 12))
        self.setFixedHeight(fm.height() + 8)

    def show_value(self, value):
        """The live value while nothing is held or being typed here — also in the box Tab moved to (it froze there:
        the user); it stays selected, so the first key typed replaces it."""
        if self.typed is None and self.pending is None:
            self.setText(self.prefix + units.length(value) if self.unit == "mm" else _angle_text(value, self.below()))
            self._fit()
            if self.hasFocus():
                self.selectAll()

    def reset(self):
        self.typed = self.pending = None
        self.setProperty("active", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def hold(self):
        """Tab out of this box: what was typed applies and holds; nothing typed — it stays free, following the
        pointer (the user: an angle not touched must not lock)."""
        self.apply()
        if self.unit == "deg" and self.typed is not None:
            self.setText(_angle_text(self.typed, getattr(self.parent(), "angle_360", True) is False))
            self._fit()
        self.setProperty("active", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def activate(self, first_char=None):
        self.setProperty("active", True)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setFocus()
        if first_char is not None:
            self.setText(first_char)
            self._edited(first_char)
        else:  # Tab into it: its size lets go and follows the pointer, selected, until a new one is typed (the user)
            self.typed = self.pending = None
            self.selectAll()

    def event(self, e):
        if e.type() == QtCore.QEvent.KeyPress and e.key() in (QtCore.Qt.Key_Tab, QtCore.Qt.Key_Backtab):
            log.say("box", self.unit, "Tab, text", repr(self.text()), "held", self.typed, "pending", repr(self.pending))
            self.tabbed.emit()
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        log.say("box", self.unit, "key", repr(e.text()) if e.text().strip() else e.key(), "text was", repr(self.text()))
        if e.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.entered.emit()
            return
        if e.key() == QtCore.Qt.Key_Escape:
            self.cancelled.emit()
            return
        if e.key() == QtCore.Qt.Key_Space:  # no spaces in a size: it switches, as it does outside the box (the user)
            self.parent().keyPressEvent(e)
            return
        super().keyPressEvent(e)


PLAIN_NUMBER = re.compile(r"[-+]?(\d+([.,]\d*)?|[.,]\d+)?")
NAME = re.compile(r"[A-Za-z_]\w*")


def _names(text):
    """The names in a formula; the mm and in of its numbers are none."""
    return NAME.findall(units.literals(text))


def _eval(text, names):
    """A size's formula: + - * / ^ and brackets on numbers and names. None if it is not one."""
    def walk(n):
        if isinstance(n, ast.Expression):
            return walk(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.Name):
            return names[n.id]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            return -walk(n.operand) if isinstance(n.op, ast.USub) else walk(n.operand)
        if isinstance(n, ast.BinOp):
            a, b = walk(n.left), walk(n.right)
            ops = {ast.Add: lambda: a + b, ast.Sub: lambda: a - b, ast.Mult: lambda: a * b, ast.Div: lambda: a / b,
                   ast.Pow: lambda: a ** b}
            if type(n.op) in ops:
                return ops[type(n.op)]()
        raise ValueError
    try:
        v = walk(ast.parse(text.replace(",", ".").replace("^", "**"), mode="eval"))
        return v if isinstance(v, float) and math.isfinite(v) else None
    except (SyntaxError, ValueError, KeyError, ZeroDivisionError, OverflowError, TypeError):
        return None


class DimEdit(ValueBox):
    """The field of a dimension's box. Minus and plus turn a plain number negative and back wherever the cursor is, in
    a formula they are signs like any other; R and D turn a diameter into a radius and back (the user)."""

    def __init__(self, view, panel):
        super().__init__("mm", panel)
        self.view, self.panel = view, panel
        self.wait_d = None  # D pressed: it may be the start of the name d0

    def move(self, x, y):
        QtWidgets.QLineEdit.move(self, x, y)

    def hide(self):
        (getattr(self, "panel", None) or super()).hide()

    def show(self):
        QtWidgets.QLineEdit.show(self)
        self.panel.show()
        self.panel.adjustSize()

    def _put(self, text, select=False):
        self.setText(text)
        self._edited(text)
        if select:
            self.selectAll()

    def keyPressEvent(self, e):
        key, text = e.text(), self.text()
        plain = PLAIN_NUMBER.fullmatch(text) is not None
        whole = self.hasSelectedText() and self.selectedText() == text
        round_one = self.view._dim_round()
        if e.key() == QtCore.Qt.Key_Space:
            self.view._dim_space()
            return
        if key in ("-", "+") and plain and text.lstrip("+-"):
            self._put(("-" if key == "-" else "") + text.lstrip("+-"), whole)
            return
        if round_one and plain and key in ("r", "R", "Ø", "ø"):
            self.view._dim_swap("dim_r" if key in "rR" else "dim_d")
            return
        if round_one and plain and key in ("d", "D"):
            self.wait_d = (self.view._dim_kind(self.view.dim_edit), text)
            self.view._dim_swap("dim_d")
            return
        if self.wait_d is not None and key.isdigit():  # d0: a name, not the letter for a diameter
            kind, _was = self.wait_d
            self.wait_d = None
            self.view._dim_swap(kind)
            self._put("d" + key)
            return
        self.wait_d = None
        if whole and text.startswith("-") and key.isdigit():  # the sign was set first, the number comes after it
            self._put("-" + key)
            return
        super().keyPressEvent(e)

    def insert_name(self, name):
        """A dimension clicked while the box is open: its name at the cursor, or in place of the untouched value."""
        if self.pending is not None and not (self.hasSelectedText() and self.selectedText() == self.text()):
            self.insert(name)
            self._edited(self.text())
        else:
            self._put(name)
        self.setFocus()


class DimDot(QtWidgets.QWidget):
    """The small dot beside the value: pulled left or right, the size follows."""
    pulled = QtCore.Signal(int)

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self.setCursor(QtCore.Qt.SizeHorCursor)
        self.setToolTip("Pull left or right to change the size")
        self.was = None

    def paintEvent(self, _e):
        g = QtGui.QPainter(self)
        g.setRenderHint(QtGui.QPainter.Antialiasing)
        g.setBrush(QtGui.QColor(theme.cur()["accent"]))
        g.setPen(QtCore.Qt.NoPen)
        g.drawEllipse(P(7, 7), 4.5, 4.5)
        g.end()

    def mousePressEvent(self, e):
        self.was = e.globalPosition().x()
        self.parent().px = 0

    def mouseMoveEvent(self, e):
        if self.was is None:
            return
        n = int(e.globalPosition().x() - self.was)
        if n:
            self.was += n
            self.pulled.emit(n)

    def mouseReleaseEvent(self, _e):
        self.was = None


class DimPanel(QtWidgets.QFrame):
    """A dimension's box: d0 = value, the dot, ✓ and ✕, and what is wrong with the value."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.setObjectName("DimPanel")
        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(6, 4, 6, 4)
        col.setSpacing(3)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        col.addLayout(row)
        self.name = QtWidgets.QLabel()
        self.name.setObjectName("DimName")
        row.addWidget(self.name)
        self.edit = DimEdit(view, self)
        row.addWidget(self.edit)
        self.dot = DimDot(self)
        self.dot.pulled.connect(self._pulled)
        self.px = self.roll = 0  # what the dot and the wheel have gone that made no step yet
        # a click anywhere in the box keeps the keyboard in the field: left alone, Qt hands it to the view under it
        for w in (self, self.name, self.dot):
            w.setFocusPolicy(QtCore.Qt.ClickFocus)
            w.setFocusProxy(self.edit)
        row.addWidget(self.dot)
        self.ok, self.no = [QtWidgets.QToolButton(self) for _k in range(2)]
        for b, word, tip, do in ((self.ok, "✓", "Apply (Enter)", view._dim_commit), (self.no, "✕", "Cancel (Esc)", view._dim_cancel)):
            b.setObjectName("DimButton")
            b.setText(word)
            b.setToolTip(tip)
            b.setFocusPolicy(QtCore.Qt.NoFocus)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.clicked.connect(do)
            row.addWidget(b)
        self.error = QtWidgets.QLabel()
        self.error.setObjectName("DimError")
        self.error.hide()
        col.addWidget(self.error)
        self.hide()

    def move(self, x, y):
        """Never over the browser, always on the view."""
        v = self.view
        x, y = v.tilted(x, y)
        super().move(max(v.left_inset + 4, min(x, v.width() - self.width() - 4)), max(4, min(y, v.height() - self.height() - 4)))

    def say(self, text):
        self.error.setText(text)
        self.error.setVisible(bool(text))
        self.edit.setStyleSheet("border: 1px solid %s;" % theme.cur()["end_of_part"] if text else "")
        self.adjustSize()

    def mousePressEvent(self, e):
        e.accept()  # the box's own ground: not a click in the sketch

    def _pulled(self, n):
        """The dot: whole numbers, one per 5 px (the user)."""
        self.px += n
        steps = int(self.px / 5)
        if steps:
            self.px -= steps * 5
            self.view._dim_whole(steps)

    def wheelEvent(self, e):
        """The wheel: tenths or hundredths by Settings, 50.90 goes on to 51.00 (the user)."""
        self.roll += e.angleDelta().y()
        steps = int(self.roll / 120)
        if steps:
            self.roll -= steps * 120
            self.view._dim_nudge(steps * units.wheel(float(self.view.dim_look.get("wheel", "0.1"))))
        e.accept()


COMB_PX = 60  # the longest spike of a curvature comb
SPLINE_TOOLS = {"spline": "Spline", "spline_cv": "Control-Vertex Spline"}


def _spline_curve(kind, pts, closed, steps=14):
    """The curve as a list of points. "fit" goes through pts, "cv" is steered by them: the standard curves, the old
    program's two splines (spline_math.py: the user, 2026-11-10, "a spline as Plasticity's or close"; before that a
    Catmull-Rom curve and a B-spline with its ends tripled, formulas of this sketch's own). Points one on another in
    a row count once."""
    from prostocad import spline_math
    return [P(x, y) for x, y in spline_math.polyline(kind, [(q.x(), q.y()) for q in pts], closed, steps)]


class SketchView(TrimTools, SlotTools, PolygonTools, MoveTools, TextTools, CornerTools, OffsetTools, MirrorTools, PatternTools, ConstraintTools, ImageTools, QtWidgets.QWidget):
    prompt = QtCore.Signal(str)
    changed = QtCore.Signal()
    dim_changed = QtCore.Signal(bool)  # Dim Construction Lines on or off
    con_changed = QtCore.Signal()  # a constraint mark chosen, or the list must be read again
    pattern_changed = QtCore.Signal()  # the shapes, a direction or a copy left out: the window says so
    pattern_open = QtCore.Signal(str)  # Edit Pattern
    pattern_step = QtCore.Signal(str)  # the window goes on to this field by itself
    corner_swapped = QtCore.Signal()  # Space went from Fillet to Chamfer or back
    corner_open = QtCore.Signal(object)  # Edit on corners: their groups
    corner_moved = QtCore.Signal()  # Space went to the next corners of an Edit
    corner_quit = QtCore.Signal(str)  # Esc or Enter in the view during that Edit
    image_points = QtCore.Signal()  # a point of Scale / Transparency set or taken back
    image_key = QtCore.Signal(str)  # Esc or Enter in the view while its window is open
    image_quit = QtCore.Signal(str)  # it was given up from the view: its window goes
    poly_live = QtCore.Signal(float, float)  # the diameter and angle under the pointer, for the window
    poly_open = QtCore.Signal(object)  # Edit on a polygon: its window opens
    text_picked = QtCore.Signal()  # a curve was clicked for the open Text window
    text_open = QtCore.Signal(object, object)  # (the text's tie, the sketch before a new one): its window opens
    poly_set = QtCore.Signal(int, bool)  # the sides and the circle a polygon was drawn with: remembered
    tool_changed = QtCore.Signal(str)  # Space went from Trim to Extend or back
    stopped = QtCore.Signal()  # the Line ended (right button / Esc with no chain)
    context = QtCore.Signal(QtCore.QPoint)  # right-click with no tool running: the sketch's menu (Finish Sketch…)
    cursor_at = QtCore.Signal(float, float)

    def __init__(self, palette=None, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self.left_inset = 0  # px on the left under the browser (it lies over the view)
        self.angle_360 = True  # Settings ▸ Angle: 0…360 (True) or −180…+180
        self.drawing_px = 12  # Settings ▸ Text Size ▸ Drawing
        self.samples = None  # choosing a look: [(top, bottom)…] — the view split into numbered zones (the user)
        self.scale = 5.0  # px per mm
        self.center = P(0, 0)  # mm at the middle of the work field
        self.pts, self.lines = [], []  # points (mm); lines as pairs of point indices
        self.constr = []  # per line: True for a construction line (dashed; never an overlap fault)
        self.arcs = []  # arcs as (centre, start, end) point indices, counterclockwise from start to end
        self.arc_constr = []  # per arc: construction
        self.dim_look = dict(dimension.DEFAULTS)  # Settings ▸ Dimensions
        self.dim_picks, self.dim_new = [], None  # the Dimension tool: what was picked, the dimension on the pointer
        self.dim_edit = None  # the dimension whose value is being changed
        self.sel_dims = set()  # ids of the chosen dimensions' ties
        self.dim_panel = DimPanel(self)
        self.box_dim = self.dim_panel.edit
        self.dim_st, self.dim_fresh = None, False  # the sketch before the box opened; a dimension only just placed
        self.dim_next = 0  # the next dN: a name is never handed out twice
        self.before_paint = self.after_change = None
        self.under3d = False  # the 3D view lies under this one: no background of its own, nothing drawn outside a sketch
        self.tilt = self.tilt_back = None  # the sketch turned in 3D: QTransform from its straight-on drawing to the screen, and back
        self.foreign = {}  # the part's other sketches' dimensions {dN: value in the file's units}: names and formulas
        self.params = []  # the user's own parameters: {name, expr, value}
        self.line_state, self.arc_state, self.ell_state, self.pt_state, self.ways = {}, {}, {}, {}, {}
        self.colors = {}  # Settings ▸ Sketch Colors: {free, part, full, done}: what was chosen
        self._held = (0, 0, 0, 0, 0)
        self.origin_off = set()  # the tree's Origin rows hidden: xy … z, centre
        self.sketch_plane = "xy"  # the plane the sketch lies on (Start 2D Sketch picks it)
        self.lit_origin = None  # the row clicked
        self.sketch_name = "Sketch1"
        self.box_dim.entered.connect(self._dim_commit)
        self.box_dim.tabbed.connect(self._dim_commit)
        self.box_dim.cancelled.connect(self._dim_cancel)
        self.spls = []  # splines as (kind, points, closed): kind "fit" goes through its points, "cv" is steered by them;
        # a kind "bs{…}" is an old file's curve kept exactly, steered by its points as a cv (spline_math.exact_kind)
        self.spl_constr = []
        self.spl_comb = []  # beside spls: its curvature comb is on, until Hide Curvature (the user)
        self.sel_spls, self.box_spls = set(), set()
        self.spl_pts = []  # the spline being drawn: places, no points of the sketch yet
        self.ells = []  # ellipses as (centre, a, b): a and b are on it, at the ends of its axes, or at the middles of its frame
        self.ell_constr = []
        # each shape's id from the file it came from (ProstoCAD 0.18 New Sketch: FreeCAD's geometry id), beside lines,
        # arcs, ells, spls; a shape drawn here has none (None). Only appends and deletions change the shape lists, so
        # the lists are kept the same length by _fit_ids before every deletion and whenever they are read.
        self.shape_ids = dict(line=[], arc=[], ell=[], spl=[])
        self.sel_ells = set()
        self.box_ells = set()
        self.steps = []  # (lines, arcs) each action added: Cmd+Z takes back a whole action (a rectangle: 4 lines)
        self.arc_sweep = 0.0  # Center Arc: the sweep followed as the pointer goes round (signed, radians)
        self.tc_picks = []  # Tangent Circle: what it touches, [(kind, index)], with the clicks in rect_pts
        self.tc_key, self.tc_sols, self.tc_at = None, [], 0
        self.tc_seen = (None, None)  # the last circle followed the pointer: painting asks for it often
        self.arc_flip = False  # the space bar: the other side of the chord, or the other way round from the start
        self.ring = None  # the right-click ring while it is open
        self.dim_constr = False  # Dim Construction Lines: barely seen, not taken, no magnets; kept with the sketch
        self.snap_grid = "Off"  # Settings ▸ Snap to Grid: Off, Magnet, Node
        self.ext = self.ext_at = self.trim_at = None  # Extend's shape, what the pointer offers; Trim's piece
        self.slot_arc = None  # an arc slot's arc, once it is settled
        self.text_font = "Arial"  # the last font used
        self.window_top = 8  # feature windows stand below this
        self.text_frame = "rect"  # Space while a text's box is drawn
        self.run = None  # Fillet or Chamfer under way
        self.corner_edit = None  # Edit on corners: dict(groups, at, st)
        self.off = None  # Offset under way
        self.spl_kept, self.text_kept = {}, {}  # curves worked out once: a repaint only draws them
        self.text_source = None  # (view, text tie) -> the letters' outlines, from a host that builds them itself; None
        self.spans_kept = self.crossings_kept = None
        self.mir = None  # Mirror under way
        self.pat = None  # a pattern's window is open
        self.corner_loose = set()  # the fillets being pulled by hand: their radius may change
        self.corner_size = dict(radius=2.0, type="equal", size=1.0, size2=1.0, angle=45.0)
        self.text_wait = None  # the text whose window waits for a curve to be clicked
        self.poly_sides, self.poly_in = 6, True  # Polygon: its sides; the circle inside it (or through its corners)
        self.construction = False  # Format ▸ Construction on: new lines are construction lines
        self.relations = []  # what each click tied: (kind, line, which end, what to)
        self.ties = []  # what holds the points: dict(kind, pts, ref, n) — n: lines + arcs when tied (undo takes it back)
        self.first_hint = None  # the magnet the chain's first point landed on: tied when its line comes
        self.rect_hints = []  # the magnets a shape's clicks landed on
        self.drag = None  # what is being pulled: dict(kind, index, pts, was, from, moved)
        self.over = None  # what the pointer would take with no tool running: ("point" / "line" / "arc", index)
        self.tab_point = None  # the point Tab chose among those standing in one place
        self.sel_lines, self.sel_arcs = set(), set()  # what is chosen
        self.sel_pts = set()  # tied points chosen: only to break what ties them
        self.sel_places = set()  # the centre point and the axes chosen: ("origin", None), ("axis", "x" / "y"); a constraint takes them
        self.boxing = None  # the box being pulled: dict(at, to, touch, add)
        self.clip = None  # what Copy took: dict(pts, lines, arcs, constr, arc_constr, relations, ties, middle)
        self.basing = None  # Copy: its start point is being asked for
        self.rotating = None  # Rotate under way
        self.last_place = None  # where the last copy was placed: Space turns it there
        self.pts_turn = None  # (pivot, angle): what is pasted is turned
        self.pasting = None  # Paste: the copy hanging on the pointer until a click places it
        self.just_done = False  # a shape was finished: the next right click keeps the tool, the one after stops it
        self.con_kind = None  # the constraint tool on (self.tool == "constraint"): its name
        self.con_picks = []  # what was clicked so far: ("point" / "line" / "arc", index)
        self.con_ask = []  # the box took these: "Click what stays"
        self.mark_px = 18  # Settings ▸ Constraint Marks
        self._image_start()
        self.marks_hidden = False  # Show / Hide Constraints: the tiles go, dimensions stay
        self.tiles = []  # the tiles as painted: (rect on screen, name)
        self.tile_owner = []  # beside them: the constraint each stands for
        self.con_lit, self.con_hover, self.con_open = set(), None, False
        self.tile_tip = None  # the pointer on a tile: (rect, name)
        self.hint_rect = None  # where the hint's label was painted
        self.editing = True
        self.sketch_off = False  # hidden or suppressed in the tree
        self.tool = None
        self.cur = None  # index of the point the line being drawn starts from
        self.first = None  # the chain's first point (a click on it closes the chain)
        self.chain = 0  # lines in the chain so far
        self.mouse = None  # the pointer, mm (raw: the line's end stays here)
        self.hint = None  # the lit magnet: dict(kind, label, at, ref, guides)
        self.taken = []  # points taken up for guides: point index, or "origin"
        self.par = None  # the line (index) the new line is parallel to: rested on while drawing (the user)
        self.tan_dir = None  # Tangent Arc: which way along the tangent it leaves (+1 / -1), None: not known yet
        self.tan_src = None  # Tangent Arc: the line or arc it is tangent to now: ("line", i) / ("arc", i)
        self.tan_pick = None  # started on a corner: the line it holds to, Tab goes on to the next
        self.tan_held = False  # the space bar set its way: the pointer no longer changes it
        self._tan = None  # Tangent Arc at a second curve: dict(curve, q, t, src, pick, preview)
        self._tan_menu = None
        self._dwell_key = None
        self._dwell = QtCore.QTimer(self)
        self._dwell.setSingleShot(True)
        self._dwell.timeout.connect(self._take)
        self._pan_from = None
        # Rectangle (its four ways): the corners picked so far and its own size boxes (the Line's rules)
        self.rect_pts = []  # picked points: [first] or [first, second] (3-point ways)
        self.box_w = ValueBox("mm", self)  # width / first side
        self.box_h = ValueBox("mm", self)  # height / second side
        self.box_ra = ValueBox("deg", self)  # the first side's angle (3-point ways)
        self.box_r = ValueBox("mm", self)  # a radius (3-point arc, rounded rectangle)
        self.box_sw = ValueBox("deg", self)  # an arc's sweep (center arc)
        for b in self.shape_boxes():
            b.tabbed.connect(lambda me=b: self._rect_tab(me))
            b.entered.connect(self._rect_enter)
            b.cancelled.connect(self._rect_cancel_typing)
        self.box_len = ValueBox("mm", self)
        self.box_ang = ValueBox("deg", self)
        for b, other in ((self.box_len, self.box_ang), (self.box_ang, self.box_len)):
            b.tabbed.connect(lambda me=b, o=other: (me.hold(), o.activate()))
            b.entered.connect(self._enter)
            b.cancelled.connect(self._cancel_typing)
        self.grabGesture(QtCore.Qt.PinchGesture)
        self.redo_steps, self._redoing = [], False
        self.changed.connect(self._drawn_again)
        self._crosses = {}
        self.cursor_shape = CURSOR_SHAPES[0]
        self.setCursor(self.cross)
        self._cursor_at = 0.0

    @property
    def cross(self):
        """The pointer over the view: its colour and shape by Settings ▸ Sketch Colors (the user, 2026-11-11; the new
        program's, commit d482dbb)."""
        ink = self.colors.get("cursor") or CURSOR_INK  # Settings ▸ Sketch Colors ▸ Cursor, each theme its own
        shape = self.cursor_shape if self.cursor_shape in CURSOR_SHAPES else CURSOR_SHAPES[0]
        got = self._crosses.get((ink, shape))
        if got is None:
            got = self._crosses[(ink, shape)] = _cross_cursor(ink, shape)
        return got

    @property
    def p(self):
        return dict(theme.cur(), snap=self.colors["snap"]) if self.colors.get("snap") else theme.cur()

    # ----- view transform -----
    def _mid_x(self):
        """The middle of the work field: what lies under the browser is not part of it (the user)."""
        return (self.left_inset + self.width()) // 2

    def tilted(self, x, y):
        """A spot of the straight-on drawing where it is on the screen, the sketch turned in 3D."""
        if self.tilt is None:
            return int(x), int(y)
        q = self.tilt.map(QtCore.QPointF(x, y))
        return int(q.x()), int(q.y())

    def to_screen(self, p):
        return P(self._mid_x() + (p.x() - self.center.x()) * self.scale,
                 self.height() / 2 - (p.y() - self.center.y()) * self.scale)

    def to_model(self, s):
        return P(self.center.x() + (s.x() - self._mid_x()) / self.scale,
                 self.center.y() - (s.y() - self.height() / 2) / self.scale)

    def _dim_grow(self):
        """Dimensions stay Settings' size at any zoom, as the old program's (FreeCAD's sketcher keeps its text one
        size in pixels; the user, 2026-11-07: grown with the zoom they covered the sketch). The new program's."""
        self.dim_look["k"] = 1.0

    def zoom_at(self, s, factor):
        before = self.to_model(s)
        self.scale = min(400.0, max(0.05, self.scale * factor))
        self._dim_grow()
        after = self.to_model(s)
        self.center += before - after
        self._refresh()

    def fit(self):
        seen = self.pts + self.image_corners()
        if not seen:
            self.scale, self.center = 5.0, P(0, 0)
        else:
            xs = [p.x() for p in seen] + [0]
            ys = [p.y() for p in seen] + [0]
            w, h = max(xs) - min(xs) or 10, max(ys) - min(ys) or 10
            self.scale = min((self.width() - self.left_inset) / (w * 1.4), self.height() / (h * 1.4))
            self.center = P((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2)
        self.dim_ref_scale = self.scale
        self._dim_grow()
        self._refresh()

    # ----- the tool -----
    def start_line(self, single=False):
        """Line draws a chain; Single Line (single) the same, one line at a time: no next line from its end."""
        self.tool = "line_single" if single else "line"
        self.just_done = False
        self._rect_reset()  # a rectangle begun is given up when the Line is taken
        self._end_chain()
        self.setFocus()
        self.prompt.emit(self._tool_name() + "  Click where the line starts  ·  right-click stops " + self._tool_name())

    def _tool_name(self):
        if self.tool == "constraint":
            return self.con_kind
        if self.tool in TRIM_TOOLS:
            return self.tool.capitalize()
        if self.tool in CORNER_TOOLS:
            return self.tool[3:].capitalize()
        if self.tool == "sk_offset":
            return "Offset"
        if self.tool == "sk_mirror":
            return "Mirror"
        if self.tool == "sk_pattern":
            return "Pattern"
        return RECT_NAMES.get(self.tool) or SPLINE_TOOLS.get(self.tool) or ("Single Line" if self.tool == "line_single" else "Line")

    def shape_boxes(self):
        return (self.box_w, self.box_h, self.box_ra, self.box_r, self.box_sw)

    def free_count(self):
        """How much of the sketch is not held yet; None when nothing is drawn. It also works out each line's state:
        free (nothing holds it), part (something does, it can still move) or full."""
        used = {k for l in self.lines for k in l} | {k for a in self.arcs for k in a} | {k for e in self.ells for k in e} \
            | {k for s_ in self.spls for k in s_[1]}
        self.line_state, self.arc_state, self.ell_state, self.pt_state, self.ways = {}, {}, {}, {}, {}
        self.spl_state = {}
        if not used:
            return None
        used |= {k for t in self.ties if t["kind"] in ("mid", "ell_cut", "sk_point") for k in t["pts"]}
        held = []
        for arc in self.arcs:  # a circle's rim point going round it is no move
            if arc[1] == arc[2]:
                u = self.pts[arc[1]] - self.pts[arc[0]]
                held.append({(arc[1], 0): -u.y(), (arc[1], 1): u.x()})
        rels, fixed = self._rels(), self._fixed()
        n, self.ways = sk.free_ways([(p.x(), p.y()) for p in self.pts], rels, fixed, used, held)
        own = sum(1 for a in self.arcs if a[1] != a[2])  # _rels starts with what makes each arc an arc
        touched = {p for r in rels[own:] for p in r.points} | fixed

        def state(points):
            if not any(self.ways.get(k) for k in points):
                return "full"
            return "part" if touched & set(points) else "free"
        self.line_state = {i: state(l) for i, l in enumerate(self.lines)}
        self.arc_state = {i: state(a) for i, a in enumerate(self.arcs)}
        self.ell_state = {i: state(self._ell_keys(i)) for i in range(len(self.ells))}
        self.spl_state = {i: state(s_[1]) for i, s_ in enumerate(self.spls)}
        rank = {"full": 0, "part": 1, "free": 2}
        for shapes, states in ((self.lines, self.line_state), (self.arcs, self.arc_state),
                               ([self._ell_keys(i) for i in range(len(self.ells))], self.ell_state),
                               ([s_[1] for s_ in self.spls], self.spl_state)):
            for i, pts in enumerate(shapes):
                for k in pts:  # a point goes by its freest line
                    if rank[states[i]] > rank[self.pt_state.get(k, "full")]:
                        self.pt_state[k] = states[i]
        return n

    def begun(self):
        """A figure or a pick is under way: a tool's key waits (the user)."""
        return self.busy() or bool(self.dim_picks or self.dim_new or self.con_picks or self.tc_picks) or \
            self.dim_panel.isVisible() or self.pasting is not None

    def state_ink(self, state):
        p = self.p
        if self.colors.get(state):  # Settings ▸ Sketch Colors
            return QtGui.QColor(self.colors[state])
        return QtGui.QColor({"free": p["state_free"], "part": p["state_part"], "done": p["accent"], "snap": p["snap"], "ref": p["text_dim"],
                             "cursor": CURSOR_INK}.get(
            state, p["text"] if p["name"] == "light" else p["geometry"]))

    def show_dimension(self, name):
        """A dimension picked in the Parameters window: lit in the sketch."""
        self._clear_choice()
        self.sel_dims = {id(t) for t in self.dims() if t.get("name") == name}
        self._refresh()

    def busy(self):
        """Something is being drawn (a line pulled, a rectangle begun): the ribbon keeps away."""
        return self.cur is not None or bool(self.rect_pts) or bool(self.spl_pts)

    def stop_tool(self):
        log.say("tool stops:", self.tool)
        self.just_done = False
        if self.img is not None:
            self.img_end(False)
            self.image_quit.emit("cancel")
        self.basing = self.pasting = None
        self.run = None
        self.off = None
        self.mir = None
        self.pat = None
        self.con_picks, self.con_ask = [], []
        self.con_kind = None if self.tool == "constraint" else self.con_kind
        if self.dim_panel.isVisible():
            self._dim_cancel()
        self.dim_picks, self.dim_new = [], None
        self._end_chain()
        self._rect_reset()
        self._spl_reset()
        self.tool = None
        self.hint = None
        self.ext = self.ext_at = self.trim_at = None
        self.update()
        self.prompt.emit("Pick a sketch tool  ·  pull a line or a point to move it  ·  right-click: Finish Sketch")
        self.stopped.emit()

    def _end_chain(self):
        if self.cur is not None:
            log.say("chain ends at", log.pt(self.pts[self.cur]), "lines", len(self.lines))
        self.cur = self.first = None
        self.first_hint = None
        self.chain = 0
        self.taken = []
        self.par = None
        self._drop_lone_points()
        for b in (self.box_len, self.box_ang):
            b.reset()
            b.hide()
        self.setFocus()
        if self.tool and self.tool != "constraint":
            self.prompt.emit(self._tool_name() + "  Click where the line starts  ·  right-click stops " + self._tool_name())
        self._refresh()

    def _arc_spans(self):
        """(arc, centre, radius, mid angle) for every piece of every arc: lines and arcs crossing it or ending on it
        split it, as they split lines; each piece has its midpoint."""
        out = []
        shapes = [self.arc_shape(i) for i in range(len(self.arcs))]
        boxes = []  # each line's box: a line whose box stays off a circle's box cannot meet it (a drag was slow)
        for ia, ib in self.lines:
            a, b = self.pts[ia], self.pts[ib]
            m = 1e-6 * (1.0 + _len(b - a))
            boxes.append((min(a.x(), b.x()) - m, max(a.x(), b.x()) + m, min(a.y(), b.y()) - m, max(a.y(), b.y()) + m, a, b))
        for i in range(len(self.arcs)):
            c, r, a0, sw = shapes[i]
            cx0, cx1, cy0, cy1 = c.x() - r, c.x() + r, c.y() - r, c.y() + r
            cuts = {0.0, sw}
            for x0, x1, y0, y1, pa, pb in boxes:
                if x1 < cx0 or x0 > cx1 or y1 < cy0 or y0 > cy1:
                    continue
                for _t, q in _seg_circle(pa, pb, c, r):
                    d = (_ang(q - c) - a0) % TAU
                    if 1e-9 < d < sw - 1e-9:
                        cuts.add(d)
            for k in range(len(self.arcs)):
                if k == i:
                    continue
                c2, r2, b0, sw2 = shapes[k]
                for q in _circle_circle(c, r, c2, r2):
                    d = (_ang(q - c) - a0) % TAU
                    if 1e-9 < d < sw - 1e-9 and _in_sweep(_ang(q - c2), b0, sw2):
                        cuts.add(d)
            cuts = sorted(cuts)
            out += [(i, c, r, a0 + (x0 + x1) / 2) for x0, x1 in zip(cuts, cuts[1:])]
        return out

    def _drop_lone_points(self):
        """A line started and given up leaves no point behind (the user: stray dots): every point no line uses goes."""
        used = sorted({i for ln in self.lines for i in ln} | {i for arc in self.arcs for i in arc}
                      | {i for e in self.ells for i in e} | {k for t in self.ties if t["kind"] in ("mid", "ell_cut", "sk_point") for k in t["pts"]}
                      | {k for s_ in self.spls for k in s_[1]})
        if len(used) == len(self.pts):
            return
        new = {old: k for k, old in enumerate(used)}
        self.pts = [self.pts[i] for i in used]
        self.lines = [(new[a], new[b]) for a, b in self.lines]  # same order: self.constr still fits
        self.arcs = [(new[c], new[a], new[b]) for c, a, b in self.arcs]
        self.ells = [(new[c], new[a], new[b]) for c, a, b in self.ells]
        self.spls = [(kind, tuple(new[k] for k in pts), closed) for kind, pts, closed in self.spls]
        self.taken = [k if k == "origin" else new[k] for k in self.taken if k == "origin" or k in new]
        for t in self.ties:
            t["pts"] = [new[k] for k in t["pts"]]

    def zoom_to(self, pts, margin=0.35):
        """The view on these points (the overlapping pieces before Finish Sketch warns about them: the user)."""
        xs, ys = [p.x() for p in pts], [p.y() for p in pts]
        w, h = max(max(xs) - min(xs), 1.0), max(max(ys) - min(ys), 1.0)
        self.scale = min(400.0, (self.width() - self.left_inset) / (w * (1 + 2 * margin) + 1e-9), self.height() / (h * (1 + 2 * margin) + 1e-9))
        self.center = P((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2)
        self._dim_grow()
        self._refresh()

    def arc_shape(self, i):
        """(centre, radius, start angle, sweep) of arc i."""
        ic, ia, ib = self.arcs[i]
        c = self.pts[ic]
        r, a0, sweep = _arc_of(c, self.pts[ia], self.pts[ib])
        return c, r, a0, sweep

    def is_circle(self, i):
        """A circle is an arc from its rim point round to the same point. The rim point only carries the radius: it
        is not shown and cannot be taken."""
        return self.arcs[i][1] == self.arcs[i][2]

    def rims(self):
        return {arc[1] for arc in self.arcs if arc[1] == arc[2]}

    def _hold_radii(self, skip=()):
        """What moves a circle's centre moves all of it: its radius changes only when asked for."""
        out = []
        rims = self._poly_rims()  # a polygon's circle goes with its sides
        for i, (c, a, b) in enumerate(self.arcs):
            if a == b and i not in skip and a not in rims:
                out.append(sk.radius_is(c, a, _len(self.pts[a] - self.pts[c])))
        return out

    def is_arc_construction(self, i):
        return i < len(self.arc_constr) and self.arc_constr[i]

    def centres(self):
        return {arc[0] for arc in self.arcs} | {e[0] for e in self.ells}

    def ell_shape(self, i):
        c, a, b = self.ells[i]
        return self.pts[c], self.pts[a] - self.pts[c], self.pts[b] - self.pts[c]

    def _ell_back(self, i, p):
        """p in the ellipse's own frame, where it is the unit circle; None if it has flattened out."""
        c, u, v = self.ell_shape(i)
        det = u.x() * v.y() - u.y() * v.x()
        if abs(det) < 1e-9:
            return None
        d = p - c
        return P((d.x() * v.y() - d.y() * v.x()) / det, (u.x() * d.y() - u.y() * d.x()) / det)

    def _ell_nearest(self, i, p):
        """The parameter of the place on ellipse i nearest to p."""
        c, u, v = self.ell_shape(i)
        return sk.ell_nearest(c.x(), c.y(), u.x(), u.y(), v.x(), v.y(), p.x(), p.y())[0]

    def _ell_poly(self, c, u, v, t0=0.0, sw=TAU):
        n = max(8, math.ceil(sw / TAU * 96))
        return QtGui.QPolygonF([self.to_screen(_ell_at(c, u, v, t0 + sw * k / n)) for k in range(n + 1)])

    def _ell_cut(self, i):
        """An elliptical arc's (start, end) points, the parameter rising from one to the other; None: a whole ellipse.
        It is an ellipse all the same: its centre and axes' ends stay, the ends are held on it by a tie."""
        e = list(self.ells[i])
        return next((tuple(t["pts"][:2]) for t in self.ties if t["kind"] == "ell_cut" and t["pts"][2:] == e), None)

    def _ell_keys(self, i):
        return (*self.ells[i], *(self._ell_cut(i) or ()))

    def _ell_of_end(self, k):
        """The elliptical arc this point ends."""
        return next((i for i in range(len(self.ells)) if k in (self._ell_cut(i) or ())), None)

    def _ell_range(self, i):
        """(first parameter, sweep) of what is drawn of it."""
        cut = self._ell_cut(i)
        if cut is None:
            return 0.0, TAU
        c, u, v = self.ell_shape(i)
        t0 = _ell_t(c, u, v, self.pts[cut[0]])
        return t0, (_ell_t(c, u, v, self.pts[cut[1]]) - t0) % TAU

    def _ell_has(self, i, t):
        t0, sw = self._ell_range(i)
        return _in_sweep(t, t0, sw, 1e-6)

    def _ell_path(self, i, n=96):
        """Places along what is drawn of it."""
        c, u, v = self.ell_shape(i)
        t0, sw = self._ell_range(i)
        return [_ell_at(c, u, v, t0 + sw * k / n) for k in range(n + 1)]

    def _ell_poly_of(self, i):
        return self._ell_poly(*self.ell_shape(i), *self._ell_range(i))

    def _ell_plain(self, i):
        """Drawn by its axes, not in a frame: a and b stay square to each other."""
        c, a, b = self.ells[i]
        return any(t["kind"] == "con_perp" and t["pts"] == [c, a, c, b] for t in self.ties)

    def _hold_ells(self, pulled=(), whole=None):
        """A plain ellipse keeps its size when something else moves it; a pulled end changes that axis only."""
        out = []
        for i, (c, a, b) in enumerate(self.ells):
            if i == whole or not self._ell_plain(i):
                continue
            if a not in pulled:
                out.append(sk.radius_is(c, a, _len(self.pts[a] - self.pts[c])))
            if b not in pulled:
                out.append(sk.radius_is(c, b, _len(self.pts[b] - self.pts[c])))
        return out

    def arc_overlaps(self):
        """[(centre, radius, start, sweep)]: where two normal arcs lie on each other (same circle, shared stretch)."""
        out = []
        normal = [self.arc_shape(i) for i in range(len(self.arcs)) if not self.is_arc_construction(i)]
        for k in range(len(normal)):
            c1, r1, a1, s1 = normal[k]
            for c2, r2, a2, s2 in normal[k + 1:]:
                if _len(c1 - c2) > 1e-6 or abs(r1 - r2) > 1e-6:
                    continue
                # the shared stretch: walk arc 2's start / end into arc 1's frame
                lo2 = (a2 - a1) % TAU
                for start, end in ((lo2, lo2 + s2), (lo2 - TAU, lo2 - TAU + s2)):
                    lo, hi = max(0.0, start), min(s1, end)
                    if hi - lo > 1e-6:
                        out.append((c1, r1, a1 + lo, hi - lo))
        return out

    def is_construction(self, i):
        return i < len(self.constr) and self.constr[i]

    def overlaps(self):
        """[(a, b)]: the pieces where two normal (not construction) lines lie on each other — Extrude, Revolve and
        Trim cannot make sense of them (the user: show it at once, warn before the sketch is closed). Worked out
        once until the sketch changes: every pair of lines at every paint, a sketch of 300 lines on a part's face
        hardly moved (the user, 2026-11-21)."""
        pts = tuple((p.x(), p.y()) for p in self.pts)
        shape = (tuple(self.lines), tuple(self.constr))
        kept = getattr(self, "_overlaps_kept", None)
        if kept is not None and kept[0] == pts and kept[1] == shape:
            return kept[3]
        if kept is not None and kept[1] == shape and len(kept[0]) == len(pts):
            # a point dragged: only the pairs with a line on a moved point are worked out again (every pair of 300
            # lines at every move was half of a drag's time)
            moved = {k for k, (was, now) in enumerate(zip(kept[0], pts)) if was != now}
            lines_moved = {i for i, (ia, ib) in enumerate(self.lines) if ia in moved or ib in moved}
            pairs = {kj: q for kj, q in kept[2].items() if kj[0] not in lines_moved and kj[1] not in lines_moved}
            normal = [i for i in range(len(self.lines)) if not self.is_construction(i)]
            for i in lines_moved:
                if self.is_construction(i):
                    continue
                for j in normal:
                    if j == i or (j in lines_moved and j < i):
                        continue
                    k, j_ = min(i, j), max(i, j)
                    q = self._overlap_piece(k, j_)
                    if q is not None:
                        pairs[(k, j_)] = q
        else:
            pairs = self._overlaps_find_pairs()
        out = [pairs[kj] for kj in sorted(pairs)]
        self._overlaps_kept = (pts, shape, pairs, out)
        return out

    def _overlap_piece(self, k, j):
        """The piece where normal lines k < j lie on each other, or None: the same sums as _overlaps_find."""
        a, b = self.pts[self.lines[k][0]], self.pts[self.lines[k][1]]
        u = b - a
        n = _len(u)
        if n < 1e-9:
            return None
        c, d = self.pts[self.lines[j][0]], self.pts[self.lines[j][1]]
        if not (_dist_to_line(c, a, u, n) < 1e-6 and _dist_to_line(d, a, u, n) < 1e-6):
            return None
        t0, t1 = sorted((QtCore.QPointF.dotProduct(c - a, u) / n, QtCore.QPointF.dotProduct(d - a, u) / n))
        lo, hi = max(0.0, t0), min(n, t1)
        if hi - lo > 1e-6:
            return (a + u * (lo / n), a + u * (hi / n))
        return None

    def _overlaps_find_pairs(self):
        """{(k, j): piece} for every pair of normal lines k < j lying on each other."""
        normal = [(i, self.pts[ia], self.pts[ib]) for i, (ia, ib) in enumerate(self.lines) if not self.is_construction(i)]
        pairs = {}
        for x in range(len(normal)):
            k, a, b = normal[x]
            u = b - a
            n = _len(u)
            if n < 1e-9:
                continue
            for j, c, d in normal[x + 1:]:
                if not (_dist_to_line(c, a, u, n) < 1e-6 and _dist_to_line(d, a, u, n) < 1e-6):
                    continue
                t0, t1 = sorted((QtCore.QPointF.dotProduct(c - a, u) / n, QtCore.QPointF.dotProduct(d - a, u) / n))
                lo, hi = max(0.0, t0), min(n, t1)
                if hi - lo > 1e-6:
                    pairs[(k, j)] = (a + u * (lo / n), a + u * (hi / n))
        return pairs

    def _overlaps_find(self):
        """The whole check, every pair (kept as it was: the tests compare the quick way with it)."""
        out = []
        normal = [(self.pts[ia], self.pts[ib]) for i, (ia, ib) in enumerate(self.lines) if not self.is_construction(i)]
        for k in range(len(normal)):
            a, b = normal[k]
            u = b - a
            n = _len(u)
            if n < 1e-9:
                continue
            for c, d in normal[k + 1:]:
                if not (_dist_to_line(c, a, u, n) < 1e-6 and _dist_to_line(d, a, u, n) < 1e-6):
                    continue
                t0, t1 = sorted((QtCore.QPointF.dotProduct(c - a, u) / n, QtCore.QPointF.dotProduct(d - a, u) / n))
                lo, hi = max(0.0, t0), min(n, t1)
                if hi - lo > 1e-6:
                    out.append((a + u * (lo / n), a + u * (hi / n)))
        return out

    def _pointer_below(self):
        """The pointer below the level of the line's start (the minus side of the angle)."""
        if self.tool in RECT_NAMES and len(self.rect_pts) == 1:  # a 3-point way's first side: same rule
            return self.mouse is not None and self.mouse.y() < self.rect_pts[0].y() - 1e-9
        return self.cur is not None and self.mouse is not None and self.mouse.y() < self.pts[self.cur].y() - 1e-9

    def _point_at(self, key):
        return P(0, 0) if key == "origin" else self.pts[key]

    # ----- magnets -----
    def _candidates(self, m, moving=None):
        """moving: the points on the way while something is pulled: they and what hangs on them give no hint."""
        tol = SNAP_PX / self.scale
        out = []  # (priority, distance, hint)
        start = self.pts[self.cur] if self.cur is not None else None
        if self.tool in SPLINE_TOOLS and self.spl_pts:
            start = self.spl_pts[-1]

        # the lines the new line starts on: a hint lying on one of them would draw the new line back over it, hidden
        # under it — the "jumping line" (the user's screenshot: a click near the line it started from)
        under = []
        if start is not None:
            for ia, ib in self.lines:
                a, b = self.pts[ia], self.pts[ib]
                if _on_segment(start, a, b):
                    under.append((a, b))

        def add(prio, at, kind, label, ref=None, guides=()):
            if kind != "point" and any(_on_segment(at, a, b) and _len(at - start) > 1e-9 for a, b in under):
                return  # a point stays lit: a rest on it gives guides; a click there is refused
            if moving is not None and self._hint_moves(kind, ref, moving):
                return
            if kind == "on" and start is not None and _len(at - start) < 1e-9:
                return  # the line's own start: it is no place to go, and a rest here took Parallel to the line just drawn
            d = _len(at - m)
            if d <= tol and not (self.dim_constr and self._ghost_place(at, kind)):
                out.append((prio, d, dict(kind=kind, label=label, at=P(at), ref=ref, guides=list(guides))))

        if not any(_len(q) < 1e-9 for q in self.pts):  # a point put on the origin answers for it
            add(0, P(0, 0), "point", "Origin", "origin")
        centres = self.centres()
        rims = self.rims()
        ends = {k for e in self.ells for k in e[1:]}
        for i, p in enumerate(self.pts):
            if i != self.cur and i not in rims:
                add(0, p, "point", "Center" if i in centres else "Axis end" if i in ends else "End", i)
        sources = [(k, self._point_at(k)) for k in self.taken]
        if start is not None:
            sources.append(("start", start))
        for ka, a in sources:
            for kb, b in sources:
                if ka != kb and not (ka == "start" and kb == "start"):
                    x = P(a.x(), b.y())
                    if _len(x - a) > 1e-9 and _len(x - b) > 1e-9:
                        add(1, x, "cross", "Intersection", (ka, kb), [(a, x), (b, x)])
        for ka, a in sources:  # a guide crossing an axis: the guide stays lit there, not only "On X axis" (the user)
            vertical, level = ("v", "h") if ka == "start" else ("above", "level")
            ref = None if ka == "start" else ka
            if abs(a.y()) > 1e-9:
                add(1, P(a.x(), 0), vertical, "Intersection", ref, [(a, P(a.x(), 0))])
            if abs(a.x()) > 1e-9:
                add(1, P(0, a.y()), level, "Intersection", ref, [(a, P(0, a.y()))])
        for i, a, b in self._spans():
            add(2, (a + b) * 0.5, "mid", "Midpoint", i)
        for i, c, r, ang in self._arc_spans():
            if not self.is_circle(i):
                add(2, _on_circle(c, r, ang), "arc_mid", "Midpoint", i)
        for i in range(len(self.arcs)):
            if self.is_circle(i):  # a circle's own places: right, top, left, bottom
                c, r = self.arc_shape(i)[:2]
                for way in (P(1, 0), P(0, 1), P(-1, 0), P(0, -1)):
                    add(2, c + way * r, "quad", "Quadrant", i)
        for at, kind, ref in self._crossings():  # worked out once; only those near the pointer go further
            if abs(at.x() - m.x()) <= tol and abs(at.y() - m.y()) <= tol:
                add(1, at, kind, "Intersection", ref)
        if start is not None:
            for i, (ia, ib) in enumerate(self.lines):
                a, b = self.pts[ia], self.pts[ib]
                u = b - a
                n = _len(u)
                if n < 1e-9:
                    continue
                u /= n
                t = QtCore.QPointF.dotProduct(start - a, u)
                foot = a + u * t
                if 1e-6 < t < n - 1e-6 and _len(foot - start) > 1e-6:
                    add(3, foot, "perp", "Perpendicular", i)
        for i, (ia, ib) in enumerate(self.lines):
            a, b = self.pts[ia], self.pts[ib]
            u = b - a
            n2 = QtCore.QPointF.dotProduct(u, u)
            if n2 < 1e-12:
                continue
            t = max(0.0, min(1.0, QtCore.QPointF.dotProduct(m - a, u) / n2))
            add(4, a + u * t, "on", "On line", i)
        for i in range(len(self.arcs)):  # on an arc: the nearest place of it
            c, r, a0, sw = self.arc_shape(i)
            ang = _ang(m - c)
            if _len(m - c) > 1e-9 and _in_sweep(ang, a0, sw):
                add(4, _on_circle(c, r, ang), "on_arc", "On circle" if self.is_circle(i) else "On arc", i)
            if start is not None:  # the line from the start touching the arc: tangent (the user's must-have)
                d = _len(start - c)
                if d > r + 1e-6:
                    base, phi = _ang(start - c), math.acos(r / d)
                    for t_ang in (base + phi, base - phi):
                        if _in_sweep(t_ang, a0, sw):
                            add(3, _on_circle(c, r, t_ang), "tan", "Tangent", i)
                elif abs(d - r) <= 1e-6 and _in_sweep(_ang(start - c), a0, sw, 1e-6):
                    # the line goes on from the arc's end: along its tangent there
                    rad = (start - c) / d
                    u = P(-rad.y(), rad.x())
                    q = start + u * QtCore.QPointF.dotProduct(m - start, u)
                    if _len(q - start) > 1e-6:
                        add(5, q, "tan_on", "Tangent", i, [(start, q)])
        if self.tool == "arc_tangent" and self._rect_stage() == 1 and self._tan is None:
            at0 = self.rect_pts[0]  # a half circle: its diameter stands square to the line at the start (the user)
            t, _src = self._tan_choice(m)
            if t is not None:
                n = P(-t.y(), t.x())
                along = QtCore.QPointF.dotProduct(m - at0, n)
                if self.box_r.typed is not None:  # with the radius given: two radii from the start
                    along = math.copysign(2 * abs(self.box_r.typed), along or 1.0)
                foot = at0 + n * along
                if _len(foot - at0) > 1e-6:
                    add(3, foot, "half", "Half circle", None, [(at0, foot)])
        if self.tool == "arc" and self._rect_stage() == 2 and self.box_r.typed is None:
            a, b = self.rect_pts  # the third click on the circle round the two ends: the chord is the diameter (the user)
            c, r = (a + b) * 0.5, _len(b - a) / 2
            if r > 1e-9 and _len(m - c) > 1e-9:
                add(3, c + (m - c) / _len(m - c) * r, "half", "Half circle", None, [(a, b)])
        if self.tool == "circle" and self._rect_stage() == 1:
            at0 = self.rect_pts[0]  # the circle touching a line, an arc or another circle (the user)
            for ia, ib in self.lines:
                a, b = self.pts[ia], self.pts[ib]
                u = b - a
                n = _len(u)
                if n > 1e-9:
                    foot = a + u / n * QtCore.QPointF.dotProduct(at0 - a, u / n)
                    if _len(foot - at0) > 1e-6 and _on_segment(foot, a, b):
                        add(3, foot, "square", "Tangent", None, [(at0, foot)])
            for i in range(len(self.arcs)):
                c, r, a0, sw = self.arc_shape(i)
                d = _len(at0 - c)
                if d > 1e-6:
                    for ang in (_ang(at0 - c), _ang(at0 - c) + math.pi):
                        q = _on_circle(c, r, ang)
                        if _len(q - at0) > 1e-6 and _in_sweep(ang, a0, sw):
                            add(3, q, "square", "Tangent", None, [(at0, q)])
        if self.tool == "circle_3pt" and self._rect_stage() == 2 and self.box_r.typed is None:
            a, b = self.rect_pts  # the chord as the diameter
            c, r = (a + b) * 0.5, _len(b - a) / 2
            if r > 1e-9 and _len(m - c) > 1e-9:
                add(3, c + (m - c) / _len(m - c) * r, "half", "Diameter", None, [(a, b)])
        if self.tool in ("arc", "arc_center", *SLOT_ARC, "circle", "circle_3pt") and self._rect_stage() == 1:
            at0 = self.rect_pts[0]  # its second point: square to the line it starts on, or on its continuation (the user)
            for t, _src in self._curve_tangents(at0):
                n = P(-t.y(), t.x())
                foot = at0 + n * QtCore.QPointF.dotProduct(m - at0, n)
                if _len(foot - at0) > 1e-6:
                    add(3, foot, "square", "Perpendicular", None, [(at0, foot)])
                foot = at0 + t * QtCore.QPointF.dotProduct(m - at0, t)
                if _len(foot - at0) > 1e-6 and not self._on_any_line(foot):
                    add(3, foot, "along", "Extension", None, [(at0, foot)])
        if self.tool in ("arc", "arc_center", *SLOT_ARC, "rect_3pt", "rect_center_3pt", "circle", "circle_3pt", "ellipse", "ellipse_rot", "arc_ellipse", *SLOT_LINE, "polygon") and self._rect_stage() == 1:
            at0 = self.rect_pts[0]  # the second point: level or upright through the first (the user)
            add(5, P(m.x(), at0.y()), "h", "Horizontal", None, [(at0, P(m.x(), at0.y()))])
            add(5, P(at0.x(), m.y()), "v", "Vertical", None, [(at0, P(at0.x(), m.y()))])
        if self.tool in CENTER_ARCS and self._rect_stage() == 2:
            self._sweep_marks(m, tol, out)
        if self.tool == "arc_ellipse" and self._rect_stage() >= 3:
            self._ell_arc_marks(m, tol, out)
        for i in range(len(self.spls)):  # on the spline, and where lines cross it
            if moving is not None and any(k in moving for k in self.spls[i][1]):
                continue
            poly = self.spl_poly(i)
            best = None
            # the curve's pieces in groups, each with its box: a group whose box is further than the magnet reaches
            # holds no place it would take (thousands of pieces at every move slowed a drag)
            for k0, x0, x1, y0, y1 in self._spl_boxes(poly):
                if m.x() < x0 - tol or m.x() > x1 + tol or m.y() < y0 - tol or m.y() > y1 + tol:
                    continue
                for p_, q_ in itertools.pairwise(poly[k0:k0 + SPL_GROUP + 1]):
                    u_ = q_ - p_
                    n2 = QtCore.QPointF.dotProduct(u_, u_)
                    if n2 < 1e-12:
                        continue
                    t_ = max(0.0, min(1.0, QtCore.QPointF.dotProduct(m - p_, u_) / n2))
                    at_ = p_ + u_ * t_
                    if best is None or _len(at_ - m) < _len(best - m):
                        best = at_
            if best is not None:
                add(4, best, "on_ell", "On spline", None)
        for i in range(len(self.ells)):
            if moving is not None and any(k in moving for k in self.ells[i]):
                continue
            c, u, v = self.ell_shape(i)
            back = lambda p, i=i: self._ell_back(i, p)
            if back(c + u) is None:
                continue
            if start is not None:  # the line from the start touching it
                q = back(start)
                d = _len(q)
                if d > 1 + 1e-6:
                    base, phi = _ang(q), math.acos(1 / d)
                    for t in (base + phi, base - phi):
                        if self._ell_has(i, t):
                            add(3, _ell_at(c, u, v, t), "tan_ell", "Tangent", None)
                elif abs(d - 1) <= 1e-4 and self._ell_has(i, _ang(q)):  # the line goes on from the ellipse: along its tangent there
                    t = _ang(q)
                    along = v * math.cos(t) - u * math.sin(t)
                    along /= _len(along)
                    end = start + along * QtCore.QPointF.dotProduct(m - start, along)
                    if _len(end - start) > 1e-6:
                        add(5, end, "tan_on", "Tangent", None, [(start, end)])
            for j, (ja, jb) in enumerate(self.lines):  # in the ellipse's own frame it is the unit circle
                if not (i < len(self.ells) and (ja in self.ells[i] or jb in self.ells[i])):
                    for t, q in _seg_circle(back(self.pts[ja]), back(self.pts[jb]), P(0, 0), 1.0):
                        if 1e-9 < t < 1 - 1e-9 and self._ell_has(i, _ang(q)):
                            add(1, c + u * q.x() + v * q.y(), "ell_x", "Intersection", None)
            best = self._ell_nearest(i, m)
            if self._ell_has(i, best):
                add(4, _ell_at(c, u, v, best), "on_ell", "On ellipse", None)
        targets = getattr(self, "host_targets", None)  # ProstoCAD 0.18 New Sketch: the part's edges and vertices
        if targets:  # in the sketch's plane, magnets as the sketch's own (a point there projects them, by the host)
            for q in targets.get("points", []):
                add(0, q, "part_point", "Part vertex", None)
            for poly in targets.get("lines", []):
                best = None
                for p_, q_ in itertools.pairwise(poly):
                    u_ = q_ - p_
                    n2 = QtCore.QPointF.dotProduct(u_, u_)
                    if n2 < 1e-12:
                        continue
                    t_ = max(0.0, min(1.0, QtCore.QPointF.dotProduct(m - p_, u_) / n2))
                    at_ = p_ + u_ * t_
                    if best is None or _len(at_ - m) < _len(best - m):
                        best = at_
                if best is not None:
                    add(4, best, "part_on", "On part edge", None)
        add(4, P(m.x(), 0), "on", "On X axis", "x")
        add(4, P(0, m.y()), "on", "On Y axis", "y")
        if self.snap_grid != "Off":  # a place only, it ties nothing
            step = self._grid_step()
            node = P(round(m.x() / step) * step, round(m.y() / step) * step)
            if self.snap_grid == "Magnet":
                add(6, node, "grid", "Grid", None)  # the lowest of the magnets
            else:  # Node: only a shape's own magnets come before it, from any distance
                out.append((4.5, _len(node - m), dict(kind="grid", label="Grid", at=node, ref=None, guides=[])))
        if start is not None:
            add(5, P(m.x(), start.y()), "h", "Horizontal", None, [(start, P(m.x(), start.y()))])
            add(5, P(start.x(), m.y()), "v", "Vertical", None, [(start, P(start.x(), m.y()))])
        for k in self.taken:  # a slanted line ending on a point rested on: its continuation (the user)
            for i, (ia, ib) in enumerate(self.lines):
                if k not in (ia, ib):
                    continue
                end, far = self.pts[k], self.pts[ib if k == ia else ia]
                u = end - far
                n = _len(u)
                if n < 1e-9 or abs(u.x()) < 1e-9 or abs(u.y()) < 1e-9:
                    continue  # level and upright lines: Level with point and Vertical to point answer for them
                u /= n
                t = QtCore.QPointF.dotProduct(m - end, u)
                if t > 1e-6:
                    foot = end + u * t
                    add(4, foot, "ext", "Extension", i, [(end, foot)])
        for k, a in ((k, self._point_at(k)) for k in self.taken):
            add(6, P(m.x(), a.y()), "level", "Level with point", k, [(a, P(m.x(), a.y()))])
            add(6, P(a.x(), m.y()), "above", "Vertical to point", k, [(a, P(a.x(), m.y()))])
        if not out:
            return None
        # points (End, Intersection, Midpoint, Perpendicular): the nearest to the pointer wins, an End a little
        # sooner; only with none of them near does a line, then level / upright, then a guide light up
        bonus = {0: 3.0, 1: 2.0, 2: 1.0, 3: 1.0}
        out.sort(key=lambda c: (0, c[1] - bonus[c[0]] / self.scale) if c[0] <= 3 else (c[0], c[1]))
        return out[0][2]

    def _crossings(self):
        """(place, kind, ref) of every crossing of lines, arcs and splines. Worked out once until the sketch changes: with
        a hundred lines and a spline this took a sixth of a second on every move of the mouse."""
        if self.drag is not None and self.crossings_kept is not None:  # while pulling: what moves gives no hints anyway
            return self.crossings_kept[1]
        key = (tuple((p.x(), p.y()) for p in self.pts), tuple(self.lines), tuple(self.arcs), tuple(s_[1] for s_ in self.spls))
        if self.crossings_kept is not None and self.crossings_kept[0] == key:
            return self.crossings_kept[1]
        out = []
        ends = [(self.pts[a], self.pts[b]) for a, b in self.lines]
        near = _Near(ends)
        for i in range(len(self.arcs)):  # where an arc meets a line or another arc
            c, r, a0, sw = self.arc_shape(i)
            for j in near.circle(c, r):
                a, b = ends[j]
                for t, q in _seg_circle(a, b, c, r):
                    if 1e-9 < t < 1 - 1e-9 and _in_sweep(_ang(q - c), a0, sw, -1e-9):
                        out.append((q, "x", ("arc", i, j)))
            for k in range(i + 1, len(self.arcs)):
                c2, r2, b0, sw2 = self.arc_shape(k)
                for q in _circle_circle(c, r, c2, r2):
                    if _in_sweep(_ang(q - c), a0, sw, -1e-9) and _in_sweep(_ang(q - c2), b0, sw2, -1e-9):
                        out.append((q, "x", ("arcs", i, k)))
        for i, (a, b) in enumerate(ends):  # where two lines cross
            for j in near.box(a, b, i + 1):
                hit = _cross(a, b, *ends[j])
                if hit is not None and 1e-9 < hit[0] < 1 - 1e-9 and 1e-9 < hit[1] < 1 - 1e-9:
                    out.append((hit[2], "x", (i, j)))
        for i in range(len(self.spls)):  # where lines cross a spline
            pieces = list(itertools.pairwise(self.spl_poly(i)))
            for k, j in near.boxes(pieces):  # piece by piece, the lines in their order
                p_, q_ = pieces[k]
                u_ = q_ - p_
                a_, b_ = ends[j]
                w_ = b_ - a_
                det = u_.x() * w_.y() - u_.y() * w_.x()
                if abs(det) > 1e-12:
                    d_ = a_ - p_
                    s_ = (d_.x() * w_.y() - d_.y() * w_.x()) / det
                    r_ = (d_.x() * u_.y() - d_.y() * u_.x()) / det
                    if 0 <= s_ <= 1 and 0 <= r_ <= 1:
                        out.append((p_ + u_ * s_, "ell_x", None))
        self.crossings_kept = (key, out)
        return out

    def _spans(self):
        """(line, a, b) for every piece of every line: its ends, and every other line crossing it or ending on it,
        split it; each piece has its own midpoint (the user). A point on its own splits nothing. Worked out once until
        the sketch changes: with a hundred lines this alone took a tenth of a second on every move of the mouse."""
        if self.drag is not None and self.spans_kept is not None:
            return self.spans_kept[1]
        key = (tuple((p.x(), p.y()) for p in self.pts), tuple(self.lines), tuple(self.arcs))
        if self.spans_kept is not None and self.spans_kept[0] == key:
            return self.spans_kept[1]
        out = []
        near = _Near([(self.pts[a], self.pts[b]) for a, b in self.lines])
        for i, (ia, ib) in enumerate(self.lines):
            a, b = self.pts[ia], self.pts[ib]
            u = b - a
            n2 = QtCore.QPointF.dotProduct(u, u)
            if n2 < 1e-12:
                continue
            ts = {0.0, 1.0}  # a point lying on the line does not split it (the user): only a crossing line does
            for j in near.box(a, b):  # where another line crosses it splits it too (the user)
                ja, jb = self.lines[j]
                if j != i:
                    hit = _cross(a, b, self.pts[ja], self.pts[jb])
                    if hit is not None and 1e-9 < hit[0] < 1 - 1e-9:
                        ts.add(hit[0])  # a line crossing it or ending on it (the user); a lone point does not
            for k in range(len(self.arcs)):  # an arc crossing it or ending on it splits it too
                c, r, a0, sw = self.arc_shape(k)
                for t, q in _seg_circle(a, b, c, r):
                    if 1e-9 < t < 1 - 1e-9 and _in_sweep(_ang(q - c), a0, sw):
                        ts.add(t)
            ts = sorted(ts)
            out += [(i, a + u * t0, a + u * t1) for t0, t1 in zip(ts, ts[1:])]
        self.spans_kept = (key, out)
        return out

    def _rest(self):
        """A moment's rest on a point takes it up for guides (the timer); anywhere else the wait starts over."""
        key = None
        if self.hint and self.hint["kind"] == "point":
            key = self.hint["ref"]
        elif self.hint and self.hint["kind"] == "on" and isinstance(self.hint["ref"], int) and self.cur is not None:
            key = ("line", self.hint["ref"])  # a plain line under the pointer; its midpoint and the like stay magnets
        if key != self._dwell_key:
            self._dwell_key = key
            self._dwell.stop()
            if key is not None:
                self._dwell.start(DWELL_MS)

    def _take(self):
        k = self._dwell_key
        if k is None:
            return
        if isinstance(k, tuple):
            self.par = k[1]
            log.say("parallel to line", k[1])
        elif k not in self.taken:
            self.taken.append(k)
        self._refresh()

    # ----- the line being drawn -----
    def _typed(self):
        return self.box_len.typed, self.box_ang.typed

    def _end(self):
        """Where the line's end is now: the pointer, or what the typed length / angle hold."""
        a = self.pts[self.cur]
        m = self.mouse if self.mouse is not None else a
        length, angle = self._typed()
        d = m - a
        if angle is not None:
            u = P(math.cos(math.radians(angle)), math.sin(math.radians(angle)))
            reach = length if length is not None else max(QtCore.QPointF.dotProduct(d, u), 0.0)
            return a + u * reach
        if self.hint is not None and self.hint["kind"] == "par_foot" and length is None:
            return self.hint["at"]
        if self.par is not None and not (self.hint and self.hint["kind"] in POINT_HINTS):
            u = self._par_dir()
            along = QtCore.QPointF.dotProduct(d, u)
            return a + u * (along if length is None else math.copysign(length, along or 1.0))
        if length is not None:
            n = _len(d)
            u = d / n if n > 1e-9 else P(1, 0)
            return a + u * length
        return m

    def _par_foot(self):
        """On a Parallel: the foot of the perpendicular from a point or a middle nearest the pointer, along the
        parallel (the user)."""
        a, u = self.pts[self.cur], self._par_dir()
        here = QtCore.QPointF.dotProduct(self.mouse - a, u)
        places = [("Perpendicular", p) for k, p in enumerate(self.pts) if k != self.cur]
        places += [("Midpoint", (p + q) * 0.5) for _i, p, q in self._spans()]
        places += [("Midpoint", _on_circle(c, r, ang)) for _i, c, r, ang in self._arc_spans()]
        best = None
        for label, p in places:
            along = QtCore.QPointF.dotProduct(p - a, u)
            d = abs(along - here) * self.scale
            if abs(along) > 1e-6 and d <= SNAP_PX and (best is None or d < best[0]):
                best = (d, label, p, along)
        if best is None:
            return None
        foot = a + u * best[3]
        return dict(kind="par_foot", label=best[1], at=foot, ref=None, guides=[(best[2], foot)])

    def _par_dir(self):
        ia, ib = self.lines[self.par]
        u = self.pts[ib] - self.pts[ia]
        return u / _len(u)

    def _refresh(self):
        held = (len(self.ties), len(self.relations), len(self.lines), len(self.arcs), len(self.ells), len(self.spls))
        if held != self._held:  # a constraint added or gone says so here: the colours and the count follow it
            self._held = held
            self.changed.emit()
        if self.over is not None:  # a tool started, or Cmd+Z took it away
            kind, i = self.over
            there = {"point": self.pts, "line": self.lines, "arc": self.arcs, "ell": self.ells, "dim": self.ties,
                     "spl": self.spls}[kind]
            if (self.tool and self.tool not in PICKING_TOOLS) or not self.editing or i >= len(there):
                self.over = None
        if self.tool != "constraint":
            self.con_picks, self.con_ask = [], []
        if self.tool or not self.editing:
            self._clear_choice()
        if self.tool in CENTER_ARCS and self._rect_stage() == 2 and self.mouse is not None:
            c, a = self.rect_pts
            raw = _ang(self.mouse - c) - _ang(a - c)  # followed round: the side the pointer went is the sweep
            k = round((self.arc_sweep - raw) / TAU)
            self.arc_sweep = max(-TAU + 1e-6, min(TAU - 1e-6, raw + k * TAU))
        if self.tool == "arc_ellipse" and self._rect_stage() == 4 and self.mouse is not None:
            c, a, b, st_ = self.rect_pts
            u, v = a - c, b - c
            raw = sk.ell_nearest(c.x(), c.y(), u.x(), u.y(), v.x(), v.y(), self.mouse.x(), self.mouse.y())[0] - _ell_t(c, u, v, st_)
            k = round((self.arc_sweep - raw) / TAU)
            self.arc_sweep = max(-TAU + 1e-6, min(TAU - 1e-6, raw + k * TAU))
        before = self.hint and self.hint["label"]
        boxes_hold = any(b.typed is not None for b in self.shape_boxes()) and self.tool != "arc_tangent"
        if self.par is not None and self.cur is not None and self.mouse is not None and self._typed() == (None, None):
            near = self._candidates(self.mouse)  # a point's hint beats Parallel (the user: it had the top priority)
            self.hint = None
            self.hint = near if near is not None and near["kind"] in POINT_HINTS else self._par_foot() or \
                dict(kind="par", label="Parallel", at=self._end(), ref=self.par, guides=[])
        elif self._tan is not None and self._tan["pick"]:
            self.hint = None
        elif self.drag is not None:
            pass  # the pull lit it, and went there
        elif self.rotating is not None and self.rotating["pivot"] is not None:
            self._rot_track()
        elif self.rotating is not None and self.rotating["clip"] is None:
            self.hint = None
        elif (self.basing is not None or self.pasting is not None or self.rotating is not None) and self.mouse is not None:
            self.hint = self._candidates(self.mouse)
        elif self.tool and self.tool not in PICKING_TOOLS and self.mouse is not None \
                and not any(v is not None for v in self._typed()) and not boxes_hold:
            self.hint = self._candidates(self.mouse)
        else:
            self.hint = None
        self._tan_track()
        now = self.hint and self.hint["label"]
        if now != before:
            log.say("hint", now or "off", "at", log.pt(self.hint["at"]) if self.hint else "", "pointer", log.pt(self.mouse))
        self._rest()
        self._place_boxes()
        self._part_boxes()
        self.update()

    def _place_boxes(self):
        if self.dim_edit is not None and self.dim_panel.isVisible():  # it goes with its dimension when the view moves
            shape = self._dim_shape(self.dim_edit)
            if shape is not None:
                at = self._dim_mid(shape, self._dim_text(self.dim_edit), self.dim_edit)
                self.dim_panel.move(*self._dim_free_spot(at, self.dim_panel))
        if self.tool in RECT_NAMES:
            self._rect_place_boxes()
        else:
            for b in self.shape_boxes():
                b.hide()
        if self.cur is None or self.mouse is None:
            for b in (self.box_len, self.box_ang):
                b.hide()
            return
        a = self.pts[self.cur]
        e = self._end()
        d = e - a
        length = _len(d)
        ang = math.degrees(math.atan2(d.y(), d.x())) if length > 1e-9 else 0.0  # −180…180, counterclockwise +
        if self.angle_360:
            ang %= 360.0  # Settings ▸ Angle: 0…360 (no minus to type under the level)
        self.box_len.show_value(length)
        self.box_ang.show_value(ang)
        sa, se = self.to_screen(a), self.to_screen(e)
        v = se - sa
        n = _len(v)
        u = v / n if n > 1e-9 else P(1, 0)
        nrm = P(u.y(), -u.x())
        if nrm.y() > 0:
            nrm = -nrm
        clear = 8 + abs(nrm.x()) * 38 + abs(nrm.y()) * 10  # the box's half size across the line: never on it
        mid = (sa + se) * 0.5 + nrm * clear
        self.box_len.move(int(mid.x() - 38), int(mid.y() - 10))
        at = sa + u * min(40.0, n * 0.25) - nrm * clear
        self.box_ang.move(int(at.x() - self.box_ang.width() / 2), int(at.y() - 10))
        for b in (self.box_len, self.box_ang):
            b.show()

    def _part_boxes(self):
        """Two size boxes never lie on each other (the user: the length hid behind the degrees): the later one steps
        down or up, whichever way stays in the view."""
        on = [b for b in (*self.shape_boxes(), self.box_len, self.box_ang) if b.isVisible()]
        for k, b in enumerate(on):
            for a in on[:k]:
                g, o = b.geometry(), a.geometry()
                if g.intersects(o):
                    y = o.bottom() + 5
                    if y + g.height() > self.height():
                        y = o.top() - g.height() - 4
                    b.move(g.x(), y)

    # ----- placing points -----
    def _place(self, at, hint):
        """The index of the point a click puts down: an existing point when it landed on one."""
        if hint is not None and hint["kind"] == "point" and hint["ref"] != "origin":
            return hint["ref"]
        self.pts.append(P(at))
        return len(self.pts) - 1

    def _back_over(self, end):
        """The line from the start to end lies back over a line starting there: even past that line's far end."""
        start = self.pts[self.cur]
        d = end - start
        for ln in self.lines:
            if self.cur in ln:
                far = self.pts[ln[1] if ln[0] == self.cur else ln[0]] - start
                n = _len(far)
                if n > 1e-9 and QtCore.QPointF.dotProduct(d, far) > 0 and abs(d.x() * far.y() - d.y() * far.x()) / n < 1e-6:
                    return True
        return False

    def click(self, m):
        log.say("click at", log.pt(m), "hint", self.hint and (self.hint["label"], log.pt(self.hint["at"])),
                "held", self._typed(), "start", log.pt(self.pts[self.cur]) if self.cur is not None else "-")
        hint = self.hint if not any(v is not None for v in self._typed()) else None
        if hint is not None and hint["kind"] == "par":
            hint = None  # the end is where _end() holds it
        if hint is not None and hint["kind"] == "par_foot" and self.par is None:
            hint = None
        if self.cur is None:
            at = hint["at"] if hint else m
            self.cur = self.first = self._place(at, hint)
            self.first_hint = hint
            if hint is not None:
                self.relations.append((hint["kind"], None, 1, hint["ref"]))
            self.prompt.emit(self._tool_name() + "  Click the end point, or type a length  Tab  angle  Enter  ·  right-click stops " + self._tool_name())
            self._refresh()
            return
        end = hint["at"] if hint else self._end()
        if _len(end - self.pts[self.cur]) < 1e-6:
            return
        if self._back_over(end):
            self.prompt.emit(self._tool_name() + "  The next line cannot go back over the one it starts from")
            return  # nonsense (the user): a line over its own neighbour
        closing = hint is not None and hint["kind"] == "point" and hint["ref"] == self.first and self.chain >= 2
        j = self._place(end, hint)
        log.say("line", len(self.lines), "from", log.pt(self.pts[self.cur]), "to", log.pt(self.pts[j]),
                "tied", hint and hint["label"], "closing" if closing else "")
        self.lines.append((self.cur, j))
        self.steps.append((1, 0))
        self.constr = (self.constr + [False] * len(self.lines))[:len(self.lines) - 1] + [self.construction]
        if self.first_hint is not None:
            self._tie(self.cur, self.first_hint)
            self.first_hint = None
        if hint is not None:
            self.relations.append((hint["kind"], len(self.lines) - 1, 2, hint["ref"]))
            self._tie(j, hint)
        if self.par is not None:
            if hint is None or hint["kind"] == "par_foot":  # on a point's hint instead: that holds it, not Parallel
                self.relations.append(("par", len(self.lines) - 1, None, self.par))
            self.par = None
        self.chain += 1
        for b in (self.box_len, self.box_ang):
            b.reset()
        self.changed.emit()
        if closing or self.tool == "line_single":  # Single Line: done — the next click starts a new one
            self._end_chain()
            self.just_done = True
            return
        self.cur = j
        self.setFocus()
        self._refresh()

    def _enter(self):
        """Enter: what was typed applies, the line is fixed as it stands, the next one starts from its end."""
        if self.cur is not None:
            self.box_len.apply()
            self.box_ang.apply()
            self.click(self._end())

    def _cancel_typing(self):
        self.par = None
        for b in (self.box_len, self.box_ang):
            b.reset()
        self.setFocus()
        self._refresh()

    def undo(self):
        """One step back, and the sketch as it stood is kept for Redo."""
        if self.img is not None:  # while points are put: the last point
            if self.img["pts"]:
                self.img_points(True)
            return
        if self.tool in CORNER_TOOLS and self.corner_undo():
            return
        if self.tool == "sk_pattern" and self.pat is not None:  # the window's own step: the sketch below it stays
            self.pattern_undo()
            return
        st = self._state()
        was = (len(self.steps), len(self.lines), len(self.arcs), len(self.ells), len(self.spls), [(p.x(), p.y()) for p in self.pts])
        self._redoing = True
        self._undo_step()
        self._redoing = False
        if was != (len(self.steps), len(self.lines), len(self.arcs), len(self.ells), len(self.spls), [(p.x(), p.y()) for p in self.pts]):
            self.redo_steps.append(st)  # a plain line keeps no step of its own: it is seen in what changed

    def redo(self):
        if not self.redo_steps:
            return
        log.say("redo")
        self._end_chain()
        self._rect_reset()
        self._clear_choice()
        self._redoing = True
        self._restore(self.redo_steps.pop())
        self.changed.emit()
        self._refresh()
        self._redoing = False

    def _drawn_again(self):
        """Anything new drawn or changed: what was undone is gone for good."""
        if not self._redoing:
            self.redo_steps = []

    def _undo_step(self):
        log.say("undo, lines", len(self.lines), "arcs", len(self.arcs))
        if not self.lines and not self.arcs and not self.steps:
            return
        self._end_chain()
        self._rect_reset()
        self._clear_choice()
        if self.steps and self.steps[-1][0] == "move":
            self.pts = self.steps.pop()[1]
            self.changed.emit()
            self._refresh()
            return
        if self.steps and self.steps[-1][0] == "state":
            self._restore(self.steps[-1][1])
            self.changed.emit()
            self._refresh()
            return
        kept = next((k for k in range(len(self.steps) - 1, -1, -1) if self.steps[k][0] == "state"), None)
        base = self.steps[kept][2:] if kept is not None else (0, 0)
        drawn = [s_ for s_ in self.steps[(0 if kept is None else kept + 1):] if s_[0] != "move"]
        consistent = self.steps and base[0] + sum(s_[0] for s_ in drawn) == len(self.lines) \
            and base[1] + sum(s_[1] for s_ in drawn) == len(self.arcs)
        n_l, n_a = self.steps.pop() if consistent else ((1, 0) if self.lines else (0, 1))
        self._fit_ids()
        for _ in range(min(n_l, len(self.lines))):
            self.lines.pop()
        for _ in range(min(n_a, len(self.arcs))):
            self.arcs.pop()
        del self.constr[len(self.lines):]
        del self.arc_constr[len(self.arcs):]
        self.ties = [t for t in self.ties if t["n"] <= len(self.lines) + len(self.arcs)]
        used = {i for ln in self.lines for i in ln} | {i for arc in self.arcs for i in arc} | {i for e in self.ells for i in e} \
            | {k for t in self.ties if t["kind"] in ("mid", "ell_cut", "sk_point") for k in t["pts"]} | {k for s_ in self.spls for k in s_[1]}
        while self.pts and len(self.pts) - 1 not in used:
            self.pts.pop()
        self.taken = [k for k in self.taken if k == "origin" or k < len(self.pts)]
        self.relations = [r for r in self.relations if r[1] is None or r[1] < len(self.lines)]
        self.changed.emit()
        self._refresh()

    # ----- Rectangle -----
    def start_rect(self, kind):
        """Rectangle, one of its ways: rect (two corners), rect_center (centre, corner), rect_3pt (two corners of a
        side, then its height), rect_center_3pt (centre, middle of a side, then the height). The Line's rules: the
        end stays at the pointer, magnets only light up, sizes typed wait for Tab / Enter, the long side first, RMB
        lets go and gives up."""
        self._end_chain()
        self.tool = kind
        self.just_done = False
        self._rect_reset()
        self.setFocus()
        self.prompt.emit(self._tool_name() + "  " + RECT_PROMPTS[kind][0] + "  ·  right-click stops")

    # ----- Spline -----
    def start_spline(self, kind):
        """Spline: through its points, or steered by them (Control-Vertex). The Line's rules: every click takes the
        magnets, the right button or Enter ends it and the tool stays, a click on the first point closes it."""
        self._end_chain()
        self._rect_reset()
        self.tool = kind
        self._spl_reset()
        self.setFocus()
        self._spl_prompt()

    def _spl_reset(self):
        self.spl_pts = []

    def _spl_prompt(self):
        self.prompt.emit(self._tool_name() + "  " + ("Click the next point" if self.spl_pts else "Click where it starts")
                         + ("  ·  Enter ends it  ·  click the first point to close it" if len(self.spl_pts) > 1 else "")
                         + "  ·  right-click " + ("ends it" if len(self.spl_pts) > 1 else "gives it up" if self.spl_pts else "stops " + self._tool_name()))

    def _spl_click(self, at):
        if len(self.spl_pts) >= 3 and _len(self.to_screen(at) - self.to_screen(self.spl_pts[0])) <= PICK_POINT_PX:
            self._spl_finish(True)
            return
        if self.spl_pts and _len(at - self.spl_pts[-1]) < 1e-6:
            return
        self.spl_pts.append(P(at))
        self._spl_prompt()
        self._refresh()

    def _spl_finish(self, closed=False):
        places = self.spl_pts
        if len(places) < 2:  # one click: no curve, no point left behind
            self._spl_reset()
            self._spl_prompt()
            self._refresh()
            return
        st = self._state()
        idx = tuple(self._point_index(q) for q in places)
        self.spls.append(("fit" if self.tool == "spline" else "cv", idx, closed))
        self.spl_constr = (self.spl_constr + [False] * len(self.spls))[:len(self.spls) - 1] + [self.construction]
        self.spl_comb = (self.spl_comb + [False] * len(self.spls))[:len(self.spls)]
        self.steps.append(("state", st, len(self.lines), len(self.arcs)))
        log.say("spline", self.tool, len(idx), "points", "closed" if closed else "open")
        self._spl_reset()
        self.changed.emit()
        self._spl_prompt()
        self._refresh()

    def spline_at(self, s):
        """The spline under the screen point s, or None."""
        what = self._pick(s, ("spl",))
        return what[1] if what else None

    def comb_on(self, i):
        return i < len(self.spl_comb) and self.spl_comb[i]

    def set_comb(self, i, on):
        self.spl_comb = (self.spl_comb + [False] * len(self.spls))[:len(self.spls)]
        self.spl_comb[i] = on
        self.update()

    def _paint_comb(self, g, i, ink):
        """A spike across the curve at each sample, as long as the curve is bent there, and the line along their tips.
        Only a picture: it takes no clicks."""
        poly = [self.to_screen(q) for q in self.spl_poly(i)]
        spikes = []
        for a, b, c in zip(poly, poly[1:], poly[2:]):
            u, w = b - a, c - b
            n1, n2 = _len(u), _len(w)
            if n1 < 1e-9 or n2 < 1e-9:
                continue
            turn = (u.x() * w.y() - u.y() * w.x()) / (n1 * n2)
            d = (u + w) / (_len(u + w) or 1.0)
            spikes.append((b, P(-d.y(), d.x()), turn / ((n1 + n2) / 2)))
        most = max((abs(k) for _b, _n, k in spikes), default=0.0)
        if most < 1e-9:
            return
        reach = COMB_PX / most
        pen = QtGui.QPen(ink, 1)
        g.setPen(pen)
        tips = []
        for b, n, k in spikes:
            tip = b - n * (k * reach)
            g.drawLine(b, tip)
            tips.append(tip)
        g.drawPolyline(QtGui.QPolygonF(tips))

    def _spl_boxes(self, poly):
        """[(first, x0, x1, y0, y1)]: the curve's pieces in groups of SPL_GROUP, each group's box; kept per curve."""
        kept = self.__dict__.setdefault("_spl_boxes_kept", {})
        got = kept.get(id(poly))
        if got is not None and got[0] is poly:
            return got[1]
        boxes = []
        for k0 in range(0, max(0, len(poly) - 1), SPL_GROUP):
            xs = [q.x() for q in poly[k0:k0 + SPL_GROUP + 1]]
            ys = [q.y() for q in poly[k0:k0 + SPL_GROUP + 1]]
            boxes.append((k0, min(xs), max(xs), min(ys), max(ys)))
        if len(kept) > 500:
            kept.clear()
        kept[id(poly)] = (poly, boxes)
        return boxes

    def spl_poly(self, i):
        kind, pts, closed = self.spls[i]
        places = [self.pts[k] for k in pts]
        key = (kind, closed, tuple((p.x(), p.y()) for p in places))
        kept = self.spl_kept.get(key)
        if kept is None:
            if len(self.spl_kept) > 500:
                self.spl_kept.clear()
            kept = self.spl_kept[key] = _spline_curve(kind, places, closed)
        return kept

    def _paint_spl_nets(self, g, dashed):
        """A Control-Vertex Spline's dashed net, seen while it is chosen; and the spline being drawn."""
        for i in self.sel_spls:
            if i < len(self.spls) and self.spls[i][0] != "fit":  # "cv", and a curve kept as its file has it: its
                # points steer it too, seen and pulled as a cv spline's (the user, 2026-11-10)
                pts = [self.to_screen(self.pts[k]) for k in self.spls[i][1]]
                g.setPen(dashed)
                g.drawPolyline(QtGui.QPolygonF(pts + pts[:1] if self.spls[i][2] else pts))
        if self.tool in SPLINE_TOOLS and self.spl_pts and self.mouse is not None:
            at = self.hint["at"] if self.hint else self.mouse
            places = self.spl_pts + [at]
            if self.tool == "spline_cv":
                g.setPen(dashed)
                g.drawPolyline(QtGui.QPolygonF([self.to_screen(q) for q in places]))
            g.setPen(QtGui.QPen(QtGui.QColor(self.p["accent"]), 1.6))
            g.drawPolyline(QtGui.QPolygonF([self.to_screen(q) for q in _spline_curve(
                "fit" if self.tool == "spline" else "cv", places, False)]))
            g.setBrush(QtGui.QColor(self.p["accent"]))
            g.setPen(QtCore.Qt.NoPen)
            for q in self.spl_pts:
                g.drawEllipse(self.to_screen(q), 2.5, 2.5)
            g.setBrush(QtCore.Qt.NoBrush)

    def _rect_reset(self):
        self.arc_flip = False
        self.tc_picks = []
        self.rect_pts = []
        self.rect_hints = []
        self.tan_pick = None
        self.tan_held = False
        self.taken = []
        self._tan = None
        self.tan_dir = self.tan_src = None
        for b in self.shape_boxes():
            b.reset()
            b.hide()
        self._refresh()

    def _rect_stage(self):
        """0: nothing picked; 1: first point picked; 2: first side picked (3-point ways)."""
        return len(self.rect_pts)

    def _rect_boxes(self):
        """The boxes of this stage, in Tab order — the long side first (the user)."""
        if self.tool in ("rect", "rect_center", "rect_rounded", "text", *FRAME_TOOLS) and self._rect_stage() == 1:
            w, h = self._rect_live_wh()
            return [self.box_w, self.box_h] if abs(w) >= abs(h) else [self.box_h, self.box_w]
        if self.tool == "arc_tangent":
            return [self.box_r] if self._rect_stage() == 1 and self._tan is None else []
        if self.tool == "arc_ellipse" and self._rect_stage() >= 3:
            return [self.box_ra if self._rect_stage() == 3 else self.box_sw]
        if self._slot_widening():
            return [self.box_h]
        if self.tool == "polygon":
            return []  # its window has them: nothing at the pointer over a tiny shape (the user)
        if self.tool == "circle":
            return [self.box_r] if self._rect_stage() == 1 else []
        if self.tool == "circle_tangent":
            return [self.box_r] if self._rect_stage() in (1, 2) else []
        if self._rect_stage() == 2 and self.tool in ("rect_rounded", "arc", "circle_3pt"):
            return [self.box_r]
        if self._rect_stage() == 2 and self.tool in CENTER_ARCS:
            return [self.box_sw]
        if self.tool == "ellipse_rot" and self._rect_stage() == 1:
            return [self.box_w, self.box_ra, self.box_h]  # the height can be typed ahead of the second click
        if self._rect_stage() == 1:
            return [self.box_w, self.box_ra]
        if self._rect_stage() == 2:
            return [self.box_h]
        return []

    def _rect_target(self):
        """Where the pointer stands for: the lit magnet, or the pointer itself."""
        if self._tan is not None and not self._tan["pick"]:
            return self._tan["q"]  # the Tangent Arc's menu is up: the arc stands where it was clicked
        return self.hint["at"] if self.hint is not None else self.mouse

    def _rect_live_wh(self):
        """Width and height (signed) from the first point to the pointer, before any typed size."""
        a, m = self.rect_pts[0], self._rect_target()
        d = m - a
        if self.tool in ("rect_center", "ellipse_centre"):
            return 2 * d.x(), 2 * d.y()
        return d.x(), d.y()

    def _rect_side_end(self):
        """3-point ways, first stage: where the first side (or half side from the centre) ends."""
        a, m = self.rect_pts[0], self._rect_target()
        d = m - a
        length, angle = self.box_w.typed, self.box_ra.typed
        if self.tool in ("rect_center_3pt", "ellipse", "arc_ellipse", "slot_center", "polygon") and length is not None:
            length = length / 2.0  # typed: the whole side; from the centre it is half of it
        if angle is not None:
            u = P(math.cos(math.radians(angle)), math.sin(math.radians(angle)))
            reach = length if length is not None else max(QtCore.QPointF.dotProduct(d, u), 0.0)
            return a + u * reach
        if length is not None:
            n = _len(d)
            return a + (d / n if n > 1e-9 else P(1, 0)) * length
        return m

    def _rect_corners(self):
        """The four corners as they stand now, or None while a 3-point way is still on its first side."""
        if not self.rect_pts:
            return None
        a = self.rect_pts[0]
        if self.tool in ARC_TOOLS or self.tool in ("ellipse", "arc_ellipse", "polygon", *SLOT_LINE):
            return None
        if self.tool == "rect_rounded" and self._rect_stage() == 2:
            b = self.rect_pts[1]
            return [a, P(b.x(), a.y()), b, P(a.x(), b.y())]
        if self.tool in ("rect", "rect_center", "rect_rounded", "text", *FRAME_TOOLS):
            w, h = self._rect_live_wh()
            if self.box_w.typed is not None:
                w = math.copysign(abs(self.box_w.typed), w or 1.0)
            if self.box_h.typed is not None:
                h = math.copysign(abs(self.box_h.typed), h or 1.0)
            if self.tool in ("rect", "rect_rounded", "ellipse_3pt", "text"):
                return [a, P(a.x() + w, a.y()), P(a.x() + w, a.y() + h), P(a.x(), a.y() + h)]
            return [P(a.x() - w / 2, a.y() - h / 2), P(a.x() + w / 2, a.y() - h / 2),
                    P(a.x() + w / 2, a.y() + h / 2), P(a.x() - w / 2, a.y() + h / 2)]
        if self._rect_stage() < 2:
            return None
        b = self.rect_pts[1]
        u = b - a
        n = _len(u)
        if n < 1e-9:
            return None
        u /= n
        nrm = P(-u.y(), u.x())
        side = QtCore.QPointF.dotProduct(self._rect_target() - a, nrm)
        if self.tool == "rect_center_3pt":
            side *= 2  # from the centre: the whole height is twice the pointer's distance
        h = side if self.box_h.typed is None else math.copysign(abs(self.box_h.typed), side or 1.0)
        if self.tool in ("rect_3pt", "ellipse_rot"):
            return [a, b, b + nrm * h, a + nrm * h]
        half = b - a  # centre to the middle of a side
        hn = nrm * (h / 2.0)
        return [a - half - hn, a + half - hn, a + half + hn, a - half + hn]

    def _rect_click(self, m):
        log.say("rect click", self.tool, "stage", self._rect_stage(), "at", log.pt(m),
                "hint", self.hint and self.hint["label"])
        if self.tool == "arc_tangent":
            self._tan_click(m)
            return
        if self.tool == "circle_tangent":
            self._tc_click(m)
            return
        stage = self._rect_stage()
        typed = any(b.typed is not None for b in self.shape_boxes())
        if self.hint is not None and not typed:
            self.rect_hints.append(self.hint)
        if stage == 0 and self.tool == "point":
            self._point_place()
            return
        if stage == 0:
            self.rect_pts = [P(self._rect_target())]
            self.prompt.emit(self._tool_name() + "  " + RECT_PROMPTS[self.tool][1] + "  ·  right-click gives it up")
            self._refresh()
            return
        if self.tool == "rect_rounded" and stage == 1:
            corners = self._rect_corners()
            if not corners or abs((corners[2] - corners[0]).x()) < 1e-6 or abs((corners[2] - corners[0]).y()) < 1e-6:
                return
            self.rect_pts.append(P(corners[2]))
            for b in self.shape_boxes():
                b.reset()
                b.hide()
            self.prompt.emit(self._tool_name() + "  " + RECT_PROMPTS[self.tool][2] + "  ·  right-click gives it up")
            self._refresh()
            return
        if self.tool in ("rect_3pt", "rect_center_3pt", "arc", "arc_center", *SLOT_ARC, "circle_3pt", "ellipse", "ellipse_rot", "arc_ellipse", *SLOT_LINE) and stage == 1:
            end = self._rect_side_end() if typed else P(self._rect_target())
            if _len(end - self.rect_pts[0]) < 1e-6:
                return
            self.rect_pts.append(P(end))
            self.arc_sweep = 0.0
            self.arc_flip = False
            for b in (self.box_w, self.box_ra):
                b.reset()
                b.hide()
            self.prompt.emit(self._tool_name() + "  " + RECT_PROMPTS[self.tool][2] + "  ·  right-click gives it up")
            self._refresh()
            return
        if self.tool == "polygon" and stage == 1:
            self._poly_finish()
            return
        if self.tool in SLOT_ARC and stage == 2:
            self._slot_next_click()
            return
        if self._slot_widening():
            self._slot_finish()
            return
        if self.tool == "arc_ellipse" and stage in (2, 3):
            now = self._ell_now()
            if now is None:
                return
            c, a, b, _frame = now
            self.rect_pts.append(P(b if stage == 2 else _ell_at(c, a - c, b - c, self._cut_start())))
            self.arc_sweep = 0.0
            self.arc_flip = False
            for box in self.shape_boxes():
                box.reset()
                box.hide()
            self.prompt.emit(self._tool_name() + "  " + RECT_PROMPTS[self.tool][stage + 1] + "  ·  right-click gives it up")
            self._refresh()
            return
        if self.tool in ARC_TOOLS:
            self._arc_finish()
        elif self.tool in ELLIPSE_TOOLS:
            self._ell_finish()
        elif self.tool == "rect_rounded":
            self._rounded_finish()
        elif self.tool == "text":
            self._text_finish()
        else:
            self._rect_finish()

    def _point_place(self):
        """Point: where it is clicked, on a magnet if one is lit; it stays until it is deleted."""
        st = self._state()
        n = len(self.pts)
        k = self._point_index(self._rect_target(), 1e-6)
        if k == n:
            self._add_tie("sk_point", [k])
            self._tie_shape((k,))
            self.steps.append(("state", st, len(self.lines), len(self.arcs)))
        self._shape_done("point")

    def _point_index(self, c, tol=1e-9):
        """The point at c: an existing one there (tied to it), or a new one."""
        rims = self.rims()
        hit = next((k for k, q in enumerate(self.pts) if _len(q - c) < tol and k not in rims), None)
        if hit is None:
            self.pts.append(P(c))
            hit = len(self.pts) - 1
        return hit

    def _shape_done(self, what):
        log.say(what, "construction" if self.construction else "")
        self.just_done = True
        self.rect_pts = []
        self.rect_hints = []
        self.tan_pick = None
        self.tan_held = False
        self.taken = []
        self._tan = None
        self.tan_dir = self.tan_src = None
        for b in self.shape_boxes():
            b.reset()
            b.hide()
        self.changed.emit()
        self.prompt.emit(self._tool_name() + "  " + RECT_PROMPTS[self.tool][0] + "  ·  right-click stops")
        self.setFocus()
        self._refresh()

    def _arc_now(self):
        """The arc as it stands: (centre, radius, start angle, sweep counterclockwise), or None."""
        if self.tool == "arc_tangent":
            return self._tan_now()
        if self.tool in SLOT_ARC and self._rect_stage() == 3:
            return self.slot_arc
        if self.tool == "circle_tangent":
            sols = self._tc_solutions()
            if not sols:
                return self._tc_preview()
            x, y, r = sols[self.tc_at % len(sols)]
            return P(x, y), r, 0.0, TAU
        if self.tool == "circle":
            if self._rect_stage() < 1 or self.mouse is None:
                return None
            c, t = self.rect_pts[0], self._rect_target()
            r = abs(self.box_r.typed) / 2 if self.box_r.typed is not None else _len(t - c)
            return (c, r, _ang(t - c) if _len(t - c) > 1e-9 else 0.0, TAU) if r > 1e-9 else None
        if self._rect_stage() < 2 or self.mouse is None:
            return None
        if self.tool in CENTER_ARCS:
            c, a = self.rect_pts
            r = _len(a - c)
            sweep = self.arc_sweep
            if self.hint is not None and self.hint["kind"] == "sweep":  # exactly on the magnet, the way it was going
                raw = _ang(self.hint["at"] - c) - _ang(a - c)
                sweep = raw + round((sweep - raw) / TAU) * TAU
            if self.arc_flip and sweep:
                sweep -= math.copysign(TAU, sweep)  # to the same end, the other way round
            if self.box_sw.typed is not None:
                sweep = math.copysign(math.radians(abs(self.box_sw.typed)), sweep or 1.0)
            if abs(sweep) < 1e-9 or r < 1e-9:
                return None
            a0 = _ang(a - c)
            return (c, r, a0, sweep) if sweep > 0 else (c, r, a0 + sweep, -sweep)
        a, b = self.rect_pts
        t = self._rect_target()
        chord = _len(b - a)
        if chord < 1e-9:
            return None
        if self.arc_flip:  # the pointer mirrored in the chord: the arc on the other side
            u = (b - a) / chord
            t = a + u * (2 * QtCore.QPointF.dotProduct(t - a, u)) - (t - a)
        if self.box_r.typed is not None:
            r = max(abs(self.box_r.typed) / (2 if self.tool == "circle_3pt" else 1), chord / 2)
            m = (a + b) * 0.5
            u = (b - a) / chord
            n = P(-u.y(), u.x())
            side = 1.0 if QtCore.QPointF.dotProduct(t - m, n) >= 0 else -1.0
            hgt = math.sqrt(max(0.0, r * r - (chord / 2) ** 2))
            c = m - n * (side * hgt)  # the short arc bulging to the pointer's side
            through = m + n * (side * (r - hgt))
        else:
            c = _circle_through(a, t, b)
            if c is None:
                return None
            r, through = _len(a - c), t
        if self.tool == "circle_3pt":
            return c, _len(a - c), _ang(a - c), TAU
        ra, a0, sweep = _arc_of(c, a, b)
        if _in_sweep(_ang(through - c), a0, sweep):
            return c, ra, a0, sweep
        r2, b0, sweep2 = _arc_of(c, b, a)
        return c, r2, b0, sweep2

    # ----- dimensions -----
    def dims(self):
        return [t for t in self.ties if t["kind"].startswith("dim_")]

    def start_dimension(self):
        items = [("line", i) for i in sorted(self.sel_lines)] + [("arc", i) for i in sorted(self.sel_arcs)] + \
            [("point", k) for k in sorted(self.sel_pts)]
        self._end_chain()
        self._rect_reset()
        self.tool = "dimension"
        self.dim_picks, self.dim_new = [], None
        self.hint = None
        self._clear_choice()
        self.setFocus()
        self._dim_prompt()
        for what in items[:2]:
            self._dim_take(what)
        self._refresh()

    def _dim_prompt(self, first=""):
        self.prompt.emit((first + "  ·  " if first else "") + "Dimension  " + (
            "Click where it goes  ·  Space: the next kind  ·  or click a second one" if self.dim_new else
            "Click the second point" if self.dim_picks else
            "Click a line, an arc, a circle, an axis or two points") + "  ·  right-click " + ("gives it up" if self.dim_picks else "stops"))

    def _dim_make(self):
        """The dimension of what is picked, on the pointer; None while one more is needed."""
        p = self.dim_picks
        kinds = [k for k, _i in p]
        new = None
        if kinds == ["line"] and self._slot_rounds(p[0][1]):  # a slot: Space goes centres, overall, gap
            new = dict(kinds=list(SPANS), pts=self._slot_rounds(p[0][1]))
        elif sorted(kinds) == ["arc", "point"]:
            arc = next(i for k, i in p if k == "arc")
            k = next(i for q, i in p if q == "point")
            new = dict(kinds=list(ROUND_POINT), pts=[*self.arcs[arc][:2], k])
        elif sorted(kinds) == ["arc", "line"]:
            arc = next(i for k, i in p if k == "arc")
            line = next(i for k, i in p if k == "line")
            new = dict(kinds=list(ROUND_LINE), pts=[*self.arcs[arc][:2], *self.lines[line]])
        elif kinds == ["arc", "arc"]:
            new = dict(kinds=list(SPANS), pts=[*self.arcs[p[0][1]][:2], *self.arcs[p[1][1]][:2]])
        elif kinds == ["line"]:
            new = dict(kinds=["dim_len", "dim_dx", "dim_dy"], pts=list(self.lines[p[0][1]]))
        elif kinds == ["point", "point"]:
            # the space bar goes horizontal, vertical, then aligned (the user, 2026-11-11: for every point-to-point
            # dimension the level and the upright ones first); the pointer's place still picks one first (_dim_type)
            new = dict(kinds=["dim_dx", "dim_dy", "dim_len"], pts=[p[0][1], p[1][1]])
        elif kinds == ["arc"]:
            c, a = self.arcs[p[0][1]][:2]
            new = dict(kinds=["dim_d", "dim_r"] if self.is_circle(p[0][1]) else ["dim_r", "dim_d"], pts=[c, a], arc=p[0][1])
        elif kinds in (["line", "point"], ["point", "line"]):
            k = p[0][1] if kinds[0] == "point" else p[1][1]
            line = p[1][1] if kinds[0] == "point" else p[0][1]
            new = dict(kinds=["dim_par"], pts=[k, *self.lines[line]])
        elif "axis" in kinds or "origin" in kinds:
            new = self._dim_to_axis(p)
        elif kinds == ["line", "line"]:
            a, b = self.lines[p[0][1]]
            c, d = self.lines[p[1][1]]
            u, w = self.pts[b] - self.pts[a], self.pts[d] - self.pts[c]
            if abs(u.x() * w.y() - u.y() * w.x()) < 1e-6 * _len(u) * _len(w):
                new = dict(kinds=["dim_par"], pts=[c, a, b])
            else:
                new = dict(kinds=["dim_ang"], pts=[a, b, c, d], lines=(a, b, c, d), turn=0)
        if new is not None:
            new.update(i=0, off=0.0, ref=None, far=None)
            self.dim_new = new
            self._dim_track()
        return new

    def _dim_to_axis(self, picks):
        """A point or a line against an axis or the origin: the sketch's own X and Y count as much as its lines (the
        user). A plane other than XY will have its own two axes here."""
        other = next((q for q in picks if q[0] in ("point", "line", "arc")), None)
        place = next((q for q in picks if q[0] in ("axis", "origin")), None)
        if other is None or place is None:
            return None
        if other[0] == "arc":  # a circle or an arc: from its centre
            other = ("point", self.arcs[other[1]][0])
        if other[0] == "point":
            if place[0] == "origin":
                return dict(kinds=["dim_x", "dim_y", "dim_o"], pts=[other[1]])  # (the same order: level, upright, aligned)
            return dict(kinds=["dim_y" if place == ("axis", "x") else "dim_x"], pts=[other[1]])
        a, b = [self.pts[k] for k in self.lines[other[1]]]
        level, upright = abs(a.y() - b.y()) < 1e-6, abs(a.x() - b.x()) < 1e-6
        if (level and place != ("axis", "y")) or (upright and place != ("axis", "x")):
            return dict(kinds=["dim_y" if level else "dim_x"], pts=[self.lines[other[1]][0]])
        if place[0] == "axis":  # a line at an angle to the axis: the angle between them, as between two lines
            return dict(kinds=["dim_angx"], pts=list(self.lines[other[1]]), axis=place[1], turn=0, base=0.0, first="line")
        return dict(kinds=["dim_lo"], pts=list(self.lines[other[1]]))  # a slanted line and the origin (the user)

    def _dim_kind(self, t):
        return t["kinds"][t["i"]] if "kinds" in t else t["kind"]

    def _dim_track(self):
        new = self.dim_new
        if new is None or self.mouse is None:
            return
        if self._dim_kind(new) == "dim_ang":
            self._dim_sector(new)
        elif self._dim_kind(new) == "dim_angx":
            self._dim_sector_axis(new)
        self._dim_type(new, self.mouse)
        self._dim_put(new, self.mouse)

    def _dim_put(self, t, m):
        """Where it stands: m its place. A radius or a diameter also keeps how far out along its line the value is."""
        t["off"] = self._dim_off(t, m)
        if self._dim_kind(t) in ("dim_r", "dim_d"):
            t["far"] = _len(m - self.pts[t["pts"][0]])
        shape = self._dim_shape(t)
        s = self.to_screen(m)
        if shape is not None and shape[0] == "span":  # and how far along its line the value is
            A, B = shape[3], shape[4]
            n = _len(B - A)
            if n > 1e-9:
                t["along"] = QtCore.QPointF.dotProduct(s - (A + B) * 0.5, (B - A) / n) / self.scale
        elif shape is not None and shape[0] == "ang":
            poly = shape[1]
            t["along"] = min(range(len(poly)), key=lambda k: _len(poly[k] - s)) / (len(poly) - 1)

    def _dim_type(self, t, m):
        """The mouse chooses the kind: above or below the two points horizontal, beside them vertical; at a corner or
        between them the kind chosen stays (aligned only by the space bar). 15 px past a side before it changes; the
        space bar settles it (the user)."""
        kinds = t["kinds"]
        names = {0: "dim_len", 1: "dim_dx", 2: "dim_dy"} if "dim_len" in kinds else \
            {0: "dim_o", 1: "dim_x", 2: "dim_y"} if "dim_o" in kinds else None
        if t.get("settled") or names is None or not all(n in kinds for n in names.values()):
            return
        a, b = [self.pts[k] for k in t["pts"]] if "dim_len" in kinds else (P(0, 0), self.pts[t["pts"][0]])
        x0, x1, y0, y1 = min(a.x(), b.x()), max(a.x(), b.x()), min(a.y(), b.y()), max(a.y(), b.y())
        if x1 - x0 < 1e-6 or y1 - y0 < 1e-6:
            # one above the other, or side by side: the one distance there is (the level one comes first now, and
            # between two points one above the other it is nothing)
            # (a line's own length keeps Length first: there the first kind is the aligned one)
            if kinds[0] != names[0] and x1 - x0 < 1e-6 <= y1 - y0:
                t["i"] = kinds.index(names[2])
            elif kinds[0] != names[0] and y1 - y0 < 1e-6 <= x1 - x0:
                t["i"] = kinds.index(names[1])
            return
        dead = 15 / self.scale
        dx, dy = min(dead, (x1 - x0) / 4), min(dead, (y1 - y0) / 4)
        in_x, in_y = x0 + dx < m.x() < x1 - dx, y0 + dy < m.y() < y1 - dy
        out_x, out_y = m.x() < x0 - dead or m.x() > x1 + dead, m.y() < y0 - dead or m.y() > y1 + dead
        # the pointer picks the level or the upright one only; at a corner or between the points the one chosen
        # stays, the aligned one comes by the space bar (the user, 2026-11-20: aligned still came first)
        way = 1 if in_x and out_y else 2 if in_y and out_x else None
        if way is not None:
            t["i"] = kinds.index(names[way])

    def _dim_centre(self, t):
        if t.get("axis"):  # where the line, carried on, crosses the axis
            a, b = [self.pts[k] for k in t["pts"]]
            u = b - a
            run = u.y() if t["axis"] == "x" else u.x()
            if abs(run) < 1e-12:
                return None
            return a - u * ((a.y() if t["axis"] == "x" else a.x()) / run)
        a, b, c, d = [self.pts[k] for k in t["pts"]]
        u, w = b - a, d - c
        det = u.x() * w.y() - u.y() * w.x()
        if abs(det) < 1e-12:
            return None
        s_ = ((c - a).x() * w.y() - (c - a).y() * w.x()) / det
        return a + u * s_

    def _dim_sector_axis(self, t):
        """Which of the four angles between a line and an axis: the one the pointer is in, the space bar going round."""
        at = self._dim_centre(t)
        if at is None:
            return
        p, q = t["pts"]
        rays = [(_ang(self.pts[q] - self.pts[p]) % TAU, "line", p, q), (_ang(self.pts[p] - self.pts[q]) % TAU, "line", q, p)]
        rays += [(b % TAU, "axis", b, None) for b in ((0.0, math.pi) if t["axis"] == "x" else (math.pi / 2, -math.pi / 2))]
        rays.sort(key=lambda r: r[0])
        ang = _ang(self.mouse - at) % TAU
        k = next((n for n in range(4) if (ang - rays[n][0]) % TAU < (rays[(n + 1) % 4][0] - rays[n][0]) % TAU), 0)
        k = (k + t["turn"]) % 4
        one, two = rays[k], rays[(k + 1) % 4]
        line, axis = (one, two) if one[1] == "line" else (two, one)
        t["pts"], t["base"], t["first"] = [line[2], line[3]], axis[2], one[1]

    def _dim_sector(self, t):
        """Which of the four angles between two lines: the one the pointer is in, the space bar going round them."""
        a, b, c, d = t["lines"]
        at = self._dim_centre(dict(pts=[a, b, c, d]))
        if at is None:
            return
        rays = []
        for p_, q_ in ((a, b), (b, a), (c, d), (d, c)):
            rays.append(((_ang(self.pts[q_] - self.pts[p_])) % TAU, p_, q_))
        rays.sort()
        ang = _ang(self.mouse - at) % TAU
        k = next((n for n in range(4) if (ang - rays[n][0]) % TAU < (rays[(n + 1) % 4][0] - rays[n][0]) % TAU), 0)
        k = (k + t["turn"]) % 4
        t["pts"] = [rays[k][1], rays[k][2], rays[(k + 1) % 4][1], rays[(k + 1) % 4][2]]

    def _dim_off(self, t, m):
        kind, pts = self._dim_kind(t), t["pts"]
        if kind in ("dim_len", "dim_par", "dim_o", "dim_lo", "dim_ring", *SPANS, *ROUND_ANY):
            a, b = self._dim_ends(kind, pts)
            d = b - a
            n = P(-d.y(), d.x()) / (_len(d) or 1.0)
            return QtCore.QPointF.dotProduct(m - a, n)
        if kind == "dim_x":
            return m.y() - self.pts[pts[0]].y()
        if kind == "dim_y":
            return m.x() - self.pts[pts[0]].x()
        if kind == "dim_dx":
            return m.y() - self.pts[pts[0]].y()
        if kind == "dim_dy":
            return m.x() - self.pts[pts[0]].x()
        if kind in ("dim_r", "dim_d"):
            return _ang(m - self.pts[pts[0]])
        at = self._dim_centre(t)
        return _len(m - at) if at is not None else t["off"]

    def _dim_ends(self, kind, pts):
        """The two places a length is measured between."""
        if kind in ("dim_rp_gap_x", "dim_rp_far_x", "dim_rp_gap_y", "dim_rp_far_y"):  # square to the axis, at the point's height
            c, rim, p = [self.pts[k] for k in pts]
            far, r_ = kind[7:10] == "far", _len(rim - c)
            if kind.endswith("x"):
                s_ = 1.0 if p.x() >= c.x() else -1.0
                return P(c.x() + s_ * (-r_ if far else r_), p.y()), p
            s_ = 1.0 if p.y() >= c.y() else -1.0
            return P(p.x(), c.y() + s_ * (-r_ if far else r_)), p
        if kind in ROUND_POINT:
            c, rim, p = [self.pts[k] for k in pts]
            way = (p - c) / (_len(p - c) or 1.0)
            k = {"dim_rp_c": 0.0, "dim_rp_gap": 1.0, "dim_rp_far": -1.0}[kind]
            return c + way * (k * _len(rim - c)), p
        if kind in ROUND_LINE:
            c, rim, p, q = [self.pts[k] for k in pts]
            u = q - p
            foot = p + u * (QtCore.QPointF.dotProduct(c - p, u) / (QtCore.QPointF.dotProduct(u, u) or 1.0))
            way = (foot - c) / (_len(foot - c) or 1.0)
            k = {"dim_rl_c": 0.0, "dim_rl_gap": 1.0, "dim_rl_far": -1.0}[kind]
            return c + way * (k * _len(rim - c)), foot
        if kind in ("dim_far_x", "dim_far_y", "dim_gap_x", "dim_gap_y"):  # measured square to the axis, at the first one's height
            c1, a1, c2, a2 = [self.pts[k] for k in pts]
            k = 1.0 if kind[4:7] == "far" else -1.0
            r1, r2 = _len(a1 - c1) * k, _len(a2 - c2) * k
            if kind.endswith("x"):
                s_ = 1.0 if c2.x() >= c1.x() else -1.0
                return P(c1.x() - s_ * r1, c1.y()), P(c2.x() + s_ * r2, c1.y())
            s_ = 1.0 if c2.y() >= c1.y() else -1.0
            return P(c1.x(), c1.y() - s_ * r1), P(c1.x(), c2.y() + s_ * r2)
        if kind in SPANS:
            c1, a1, c2, a2 = [self.pts[k] for k in pts]
            n = _len(c2 - c1)
            u = (c2 - c1) / n if n > 1e-9 else P(1, 0)
            k = {"dim_c2c": 0.0, "dim_over": -1.0, "dim_gap": 1.0}[kind]
            return c1 + u * (k * _len(a1 - c1)), c2 - u * (k * _len(a2 - c2))
        if kind == "dim_ring":
            c, a, b = [self.pts[k] for k in pts]
            return a, c + (a - c) / (_len(a - c) or 1.0) * _len(b - c)
        if kind == "dim_o":
            return P(0, 0), self.pts[pts[0]]
        if kind == "dim_lo":  # from the origin square to the line, carried on
            a, b = self.pts[pts[0]], self.pts[pts[1]]
            u = b - a
            return P(0, 0), a + u * (-QtCore.QPointF.dotProduct(a, u) / (QtCore.QPointF.dotProduct(u, u) or 1.0))
        if kind == "dim_par":
            p, a, b = [self.pts[k] for k in pts]
            u = b - a
            return p, a + u * (QtCore.QPointF.dotProduct(p - a, u) / (QtCore.QPointF.dotProduct(u, u) or 1.0))
        return self.pts[pts[0]], self.pts[pts[1]]

    def _dim_value(self, t):
        """What it measures now, as its equation has it: signed where the side counts, radians for an angle."""
        kind, pts = self._dim_kind(t), t["pts"]
        if kind in SPANS or kind in ROUND_ANY or kind == "dim_lo":
            a, b = self._dim_ends(kind, pts)
            return _len(b - a)
        if kind == "dim_ring":
            c, a, b = [self.pts[k] for k in pts]
            return _len(b - c) - _len(a - c)
        if kind == "dim_par":
            p, a, b = [self.pts[k] for k in pts]
            u = b - a
            return (u.x() * (p - a).y() - u.y() * (p - a).x()) / (_len(u) or 1.0)
        a = self.pts[pts[0]]
        if kind == "dim_angx":
            th = _ang(self.pts[pts[1]] - a)
            return (th - t["base"] if t["first"] == "axis" else t["base"] - th) % TAU
        if kind == "dim_x":
            return a.x()
        if kind == "dim_y":
            return a.y()
        if kind == "dim_o":
            return _len(a)
        b = self.pts[pts[1]]
        if kind == "dim_dx":
            return b.x() - a.x()
        if kind == "dim_dy":
            return b.y() - a.y()
        if kind == "dim_ang":
            return (_ang(self.pts[pts[3]] - self.pts[pts[2]]) - _ang(b - a)) % TAU
        return _len(b - a) * (2 if kind == "dim_d" else 1)

    def _dim_text(self, t):
        kind = self._dim_kind(t)
        v = self._dim_shown(t)
        body = "%.2f°" % v if kind in ("dim_ang", "dim_angx") else {"dim_r": "R ", "dim_d": "Ø "}.get(kind, "") + units.number(v)
        name, label = t.get("name"), self.dim_look.get("label", "Value")
        if t.get("driven"):
            body = "(" + body + ")"
        if not name:
            return body
        if label == "Name":
            return name
        if label == "Both" or self.dim_panel.isVisible():  # while the box is open: to click by name
            return name + " = " + body
        return body

    def _dim_shown(self, t):
        """The value as people read it: the file's units or degrees, with the sign it was given."""
        v = t["ref"] if t.get("ref") is not None and not t.get("driven") else self._dim_value(t)
        if self._dim_kind(t) in ("dim_ang", "dim_angx"):
            return math.degrees(v) - (360.0 if t.get("neg") else 0.0)
        v = units.shown(v)
        return -abs(v) if t.get("neg") else abs(v)

    def _dim_shape(self, t):
        """How it stands on the screen: ("span", a, b, A, B), ("rad", a, b, both) or ("ang", points); None if nowhere.
        While the dimensions are painted, each one's shape is worked out once (every dimension looking for a free side
        worked out all the others again)."""
        now = getattr(self, "_dim_shapes_now", None)
        if now is None:
            return self._dim_shape_find(t)
        got = now.get(id(t))
        if got is None or got[0] is not t:
            got = now[id(t)] = (t, self._dim_shape_find(t))
        return got[1]

    def _dim_shape_find(self, t):
        kind, pts, off = self._dim_kind(t), t["pts"], t["off"]
        if kind in ("dim_x", "dim_y"):
            p = self.pts[pts[0]]
            foot = P(0, p.y()) if kind == "dim_x" else P(p.x(), 0)
            A, B = (P(0, p.y() + off), P(p.x(), p.y() + off)) if kind == "dim_x" else (P(p.x() + off, 0), P(p.x() + off, p.y()))
            return ("span", self.to_screen(foot), self.to_screen(p), self.to_screen(A), self.to_screen(B))
        if kind in ("dim_len", "dim_par", "dim_o", "dim_lo", "dim_ring", *SPANS, *ROUND_ANY):
            a, b = self._dim_ends(kind, pts)
            d = b - a
            if _len(d) < 1e-9:
                return None
            n = P(-d.y(), d.x()) / _len(d)
            return ("span", self.to_screen(a), self.to_screen(b), self.to_screen(a + n * off), self.to_screen(b + n * off))
        if kind in ("dim_dx", "dim_dy"):
            a, b = self.pts[pts[0]], self.pts[pts[1]]
            if kind == "dim_dx":
                A, B = P(a.x(), a.y() + off), P(b.x(), a.y() + off)
            else:
                A, B = P(a.x() + off, a.y()), P(a.x() + off, b.y())
            return ("span", self.to_screen(a), self.to_screen(b), self.to_screen(A), self.to_screen(B))
        if kind in ("dim_r", "dim_d"):
            c, r = self.pts[pts[0]], _len(self.pts[pts[1]] - self.pts[pts[0]])
            on = _on_circle(c, r, off)
            room = dimension.clear_of_centre(self.dim_look) / self.scale
            far = t.get("far") if t.get("far") is not None else r * .6
            if abs(far - r) < room:  # never across the curve, where its arrow is
                far = r + room if far >= r else r - room
            if far < room:  # nor on the centre's mark; no room inside: outside
                far = room if r > 2 * room else r + room
            return ("rad", self.to_screen(c if kind == "dim_r" else _on_circle(c, r, off + math.pi)), self.to_screen(on),
                    kind == "dim_d", self.to_screen(_on_circle(c, far, off)))
        at = self._dim_centre(t)
        if at is None:
            return None
        a0, sw = _ang(self.pts[pts[1]] - self.pts[pts[0]]), self._dim_value(t)
        if kind == "dim_angx" and t["first"] == "axis":
            a0 = t["base"]
        poly = [self.to_screen(_on_circle(at, off, a0 + sw * k / 32)) for k in range(33)]
        return ("ang", poly, self._dim_reach(t, kind, poly, self.to_screen(at)))

    def _dim_reach(self, t, kind, poly, at):
        """The thin lines from each side of an angle out to its arc: from the side's nearest point, the axis from
        where the line crosses it."""
        def side(p, q, end):
            a, b = self.to_screen(self.pts[p]), self.to_screen(self.pts[q])
            u = b - a
            s_ = max(0.0, min(1.0, QtCore.QPointF.dotProduct(end - a, u) / (QtCore.QPointF.dotProduct(u, u) or 1.0)))
            return a + u * s_, end
        pts = t["pts"]
        if kind == "dim_ang":
            return [side(pts[0], pts[1], poly[0]), side(pts[2], pts[3], poly[-1])]
        line, axis = (poly[0], poly[-1]) if t["first"] == "line" else (poly[-1], poly[0])
        return [side(pts[0], pts[1], line), (at, axis)]

    def _dim_field(self):
        """Where a value can be read: the work field, a little in from its edges."""
        return QtCore.QRectF(self.left_inset + 45, 14, max(1, self.width() - self.left_inset - 90), max(1, self.height() - 28))

    def _dim_seen(self, A, B):
        """The middle of the part of A…B that is on the screen; None if none of it is."""
        r, t0, t1, d = self._dim_field(), 0.0, 1.0, B - A
        for q, p_ in ((-d.x(), A.x() - r.left()), (d.x(), r.right() - A.x()), (-d.y(), A.y() - r.top()), (d.y(), r.bottom() - A.y())):
            if abs(q) < 1e-12:
                if p_ < 0:
                    return None
                continue
            t = p_ / q
            if q < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
        return A + d * ((t0 + t1) / 2) if t0 <= t1 else None

    def _dim_free_side(self, t, A, B, text):
        """Which side the value of a dimension too short to hold it stands on: 1 past the end it reads towards, as
        always; -1 past the other when another dimension on the same line stands on the first; 0 (above, a row
        higher) with both taken (the user, 2026-11-11: two dimensions end to end on one line, the value of the short
        one lay over the other's arrows)."""
        sides = dimension.reach_beside(A, B, text, self.dim_look)
        if not sides or t is None:
            return 1
        n = _len(B - A)
        u = (B - A) / n
        taken = set()
        dims = self.dims()
        if len(dims) > 200:
            return 1
        for o in dims:
            if o is t:
                continue
            shape = self._dim_shape(o)
            if shape is None or shape[0] != "span":
                continue
            C, D = shape[3], shape[4]
            if any(abs((q - A).x() * u.y() - (q - A).y() * u.x()) > 3.0 for q in (C, D)):
                continue  # not on the same line
            c, d = sorted((QtCore.QPointF.dotProduct(C - A, u), QtCore.QPointF.dotProduct(D - A, u)))
            for side, (lo, hi) in sides.items():
                if c < hi - 1.0 and d > lo + 1.0:
                    taken.add(side)
        return 1 if 1 not in taken else -1 if -1 not in taken else 0

    def _dim_mid(self, shape, text=None, t=None):
        """Where the value stands: where it was put (t's along), else the middle of what is seen, so a long line zoomed
        in on still shows it (the user). Inside a dimension too small to hold it, never: beside it, on the side it was put."""
        field = self._dim_field()
        along = t.get("along") if t is not None else None
        if shape[0] == "span":
            A, B = shape[3], shape[4]
            n = _len(B - A)
            if along is not None and n > 1e-9:
                u = (B - A) / n
                q = (A + B) * 0.5 + u * along * self.scale
                if text is not None:
                    w = dimension.text_width(text, self.dim_look)
                    x = along * self.scale
                    if n < w + 8 and abs(x) < n / 2 + w / 2 + 8:
                        q = (A + B) * 0.5 + u * math.copysign(n / 2 + w / 2 + 8, x or 1.0)
                if field.contains(q):
                    return q
            beside = dimension.beside_span(A, B, text, self.dim_look, self._dim_free_side(t, A, B, text)) \
                if text is not None else None
            return beside or self._dim_seen(A, B) or (A + B) * 0.5
        if shape[0] == "rad":
            if text is not None and self._dim_inside(shape):
                q = dimension.beside_radius(shape[1], shape[2], text, self.dim_look)
                if q is not None:
                    return q
            return shape[4] if field.contains(shape[4]) else self._dim_seen(shape[1], shape[2]) or shape[4]
        poly = shape[1]
        if along is not None:
            q = poly[max(0, min(len(poly) - 1, round(along * (len(poly) - 1))))]
            if field.contains(q):
                if text is not None:
                    return dimension.beside_angle(poly, text, self.dim_look, q) or q
                return q
        if text is not None:
            q = dimension.beside_angle(poly, text, self.dim_look)
            if q is not None:
                return q
        seen = [q for q in poly if field.contains(q)]
        return seen[len(seen) // 2] if seen else poly[16]

    @staticmethod
    def _dim_inside(shape):
        """A radius' value left on its line (one pulled out past the curve stays where it was put)."""
        a, b, q = shape[1], shape[2], shape[4]
        d = b - a
        return QtCore.QPointF.dotProduct(q - a, d) <= QtCore.QPointF.dotProduct(d, d) + 1e-6

    def _dim_paint(self, g, t, ink=None):
        shape = self._dim_shape(t)
        if shape is None:
            return
        g.setRenderHint(QtGui.QPainter.Antialiasing)
        text = self._dim_text(t)
        if shape[0] == "span":
            dimension.paint_span(g, shape[1], shape[2], shape[3], shape[4], text, self.dim_look, self.p, ink, self._dim_mid(shape, text, t))
        elif shape[0] == "rad":
            dimension.paint_radius(g, shape[1], shape[2], text, self.dim_look, self.p, ink, shape[3], self._dim_mid(shape, text, t))
        else:
            dimension.paint_angle(g, shape[1], text, self.dim_look, self.p, ink, self._dim_mid(shape, text, t), shape[2])

    def _paint_dims(self, g):
        snap = QtGui.QColor(self.p["snap"])
        light = self.p["name"] == "light"  # black there, placed or being placed (the user)
        over = self.ties[self.over[1]] if self.over is not None and self.over[0] == "dim" and self.over[1] < len(self.ties) else None
        self._dim_shapes_now = {}  # nothing moves while they are painted: each shape worked out once
        try:
            for t in self.dims():
                lit = id(t) in self.sel_dims or t is over or t is self.dim_edit
                self._dim_paint(g, t, None if light and t is self.dim_edit else snap if lit else self.state_ink("ref") if t.get("driven") else None)
            self._paint_dim_picks(g, snap)
            if self.dim_new is not None:
                self._dim_paint(g, self.dim_new, None if light else self.p["accent"])
        finally:
            self._dim_shapes_now = None

    def _dim_axis_over(self):
        """The axis or the centre point under the pointer, with Dimension on and nothing nearer: lit as a line is."""
        if self.tool == "constraint" and self.con_kind == "Symmetric" and len(self.con_picks) == 2 and self.mouse is not None \
                and self.over is None:
            return self._dim_place_at(self.to_screen(self.mouse))
        if self.tool == "constraint" and self.con_kind != "Symmetric" and self.mouse is not None and self.over is None \
                and self.boxing is None and not self.con_ask:  # an axis or the origin lights where this constraint takes one
            place = self._dim_place_at(self.to_screen(self.mouse))
            return place if place is not None and place[0] in self._con_wanted() else None
        if not self.tool and self.editing and self.mouse is not None and self.over is None and self.boxing is None \
                and self.pasting is None and self.rotating is None:  # what a click would choose, lit as the figures are
            return self._dim_place_at(self.to_screen(self.mouse))
        if self.tool != "dimension" or self.dim_panel.isVisible() or self.mouse is None or self.over is not None:
            return None
        if self.dim_new is not None and not (len(self.dim_picks) == 1 and self.dim_picks[0][0] == "line"):
            return None
        return self._dim_place_at(self.to_screen(self.mouse))

    def _paint_dim_picks(self, g, ink):
        """What is picked for a dimension stays lit: an axis too, or nothing shows it was taken (the user)."""
        hover = self._dim_axis_over()
        if self.boxing is not None and (not self.tool or (self.tool == "constraint" and self.con_kind != "Symmetric"
                and "origin" in self._con_wanted())) and self._con_box_origin(self.boxing):
            hover = ("origin", None)  # the frame will take the centre point: lit while it is pulled
        picks = self.dim_picks + ([hover] if hover else []) + [q for q in self.con_picks + self.con_ask if q[0] in ("axis", "origin")] \
            + sorted(self.sel_places, key=str)
        if not picks:
            return
        g.setBrush(QtCore.Qt.NoBrush)
        g.setPen(QtGui.QPen(ink, 2.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        o = self.to_screen(P(0, 0))
        for what, i in picks:
            if what == "axis":  # dashed: a drawn line near it or on it must not be taken for the axis (the user)
                dashed = QtGui.QPen(ink, 2.6, QtCore.Qt.CustomDashLine, QtCore.Qt.FlatCap)
                dashed.setDashPattern([5, 3])
                g.setPen(dashed)
                g.drawLine(P(0, o.y()), P(self.width(), o.y())) if i == "x" else g.drawLine(P(o.x(), 0), P(o.x(), self.height()))
                g.setPen(QtGui.QPen(ink, 2.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            elif what == "origin":
                g.drawEllipse(o, 6, 6)
            elif what == "point":
                g.drawEllipse(self.to_screen(self.pts[i]), 5, 5)
            elif what == "line":
                a, b = self.lines[i]
                g.drawLine(self.to_screen(self.pts[a]), self.to_screen(self.pts[b]))
            elif what == "arc":
                g.drawPolyline(self._arc_poly(*self.arc_shape(i)))

    def _dim_hit(self, s):
        """The tie of the dimension at the screen point s."""
        best = None
        for k, t in enumerate(self.ties):
            if not t["kind"].startswith("dim_"):
                continue
            shape = self._dim_shape(t)
            if shape is None:
                continue
            d = _len(self._dim_mid(shape, self._dim_text(t), t) - s) - 18
            if shape[0] == "span":
                d = min(d, _dist_seg(s, shape[3], shape[4]))
            elif shape[0] == "rad":
                d = min(d, _dist_seg(s, shape[1], shape[2]))
            else:
                d = min(d, min(_len(q - s) for q in shape[1]))
            if d <= PICK_LINE_PX and (best is None or d < best[0]):
                best = (d, k)
        return best[1] if best else None

    def _dim_take(self, what):
        if self.dim_new is not None and len(self.dim_picks) == 2:
            return
        self.dim_picks.append(what)
        self.dim_new = None
        if self._dim_make() is None and len(self.dim_picks) >= 2:
            self.dim_picks = []
            self._dim_prompt("Pick a point or a level or upright line against an axis, a line, an arc or two points")
            return
        self._dim_prompt()

    def _dim_click(self, s):
        self.setFocus()
        if self.box_dim.isVisible():
            self._dim_commit()
            return
        if self.dim_new is not None:
            what = None
            if len(self.dim_picks) == 1 and self.dim_picks[0][0] == "line":  # an axis counts as much as another line
                slot = self._slot_rounds(self.dim_picks[0][1])  # a slot's dimension: its own arcs are no second pick
                what = self._pick(s, ("point", "line") if slot else ("point", "line", "arc")) or self._dim_place_at(s)
            elif len(self.dim_picks) == 1 and self.dim_picks[0][0] == "arc":  # two rounds: the distance between them
                what = self._pick(s, ("point", "line", "arc")) or self._dim_place_at(s)
                if what is not None and what[0] == "point" and what[1] in self.arcs[self.dim_picks[0][1]]:
                    what = None  # its own centre or end: a place for the dimension
            if what is not None and what[0] == "point" and self.dim_picks[0][0] == "line" and what[1] in self.lines[self.dim_picks[0][1]]:
                what = None  # the line's own end: a place for the dimension, not a second pick
            if what is not None and what != self.dim_picks[0]:
                self._dim_take(what)
            else:
                self._dim_place()
            self._refresh()
            return
        what = self._pick(s, ("point", "line", "arc")) or self._dim_place_at(s)
        if what is not None and what not in self.dim_picks:
            self._dim_take(what)
        self._refresh()

    def _dim_place_at(self, s):
        """The origin or an axis at the screen point s."""
        if _len(s - self.to_screen(P(0, 0))) <= PICK_POINT_PX:
            return ("origin", None)
        return self._tc_axis(s)

    def _dim_place(self):
        """The click that puts it down: it is a dimension from here on, the box only changes its value."""
        new = self.dim_new
        kind, pts = self._dim_kind(new), list(new["pts"])
        if self._dim_shape(dict(new, kind=kind)) is None:  # nothing to measure: a point on its own line
            self.dim_picks, self.dim_new = [], None
            self._dim_prompt("There is no distance here")
            return
        coords = [(p.x(), p.y()) for p in self.pts]
        value = self._dim_value(new)
        row = self._dim_row(new, kind, pts, value)
        if sk.adds_nothing(coords, self._rels(), row if isinstance(row, list) else [row], self._fixed()):
            if self.ask_reference():
                st = self._state()
                self._add_tie(kind, pts, value)
                self.ties[-1].update(off=new["off"], far=new.get("far"), along=new.get("along"), name=self._dim_claim(), expr=None, neg=False, driven=True,
                                     axis=new.get("axis"), base=new.get("base"), first=new.get("first"))
                self.steps.append(("state", st, len(self.lines), len(self.arcs)))
                self.dim_picks, self.dim_new = [], None
                self._dim_prompt()
                self.changed.emit()
            else:
                self.dim_picks, self.dim_new = [], None
                self._dim_prompt()
            return
        self.dim_st, self.dim_fresh = self._state(), True
        self._add_tie(kind, pts, value)
        t = self.ties[-1]
        t.update(off=new["off"], far=new.get("far"), along=new.get("along"), name=self._dim_claim(), expr=None, neg=False,
                 axis=new.get("axis"), base=new.get("base"), first=new.get("first"))
        self.dim_picks, self.dim_new = [], None
        self._dim_open(t)

    def ask_reference(self):
        """A size other constraints set already: say so, and let it go in for information."""
        part = getattr(self, "part_picks", None) or []
        if part and any(p in part for p in self.dim_picks):  # ProstoCAD 0.18 trial: a dimension to the part goes in
            # as a reference at once, as the old program's (the user, 2026-11-10)
            said = getattr(self, "on_part_reference", None)
            if said is not None:
                said()
            return True
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Reference Dimension")
        box.setText("Other constraints already set this size.")
        box.setInformativeText("It can go in as a reference dimension: shown in brackets, it follows the sketch and "
                               "does not change it. Place it anyway?")
        yes = box.addButton("Place Reference", QtWidgets.QMessageBox.AcceptRole)
        box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def _dim_open(self, t):
        """The box at the value, ready for digits."""
        shape = self._dim_shape(t)
        if shape is None:
            return
        if t.get("driven"):
            self.prompt.emit("Reference dimension: it follows the sketch, its value cannot be set")
            return
        self._dim_prune()
        self.dim_edit = t
        if self.dim_st is None:
            self.dim_st, self.dim_fresh = self._state(), False
        box, panel = self.box_dim, self.dim_panel
        box.reset()
        box.wait_d = None
        panel.px = panel.roll = 0
        panel.say("")
        box.setText(t.get("expr") or units.edit(self._dim_shown(t)) if self._dim_kind(t) not in ("dim_ang", "dim_angx") else t.get("expr") or "%.2f" % self._dim_shown(t))
        box._fit()
        self._dim_label()
        box.show()
        at = self._dim_mid(shape, self._dim_text(t), t)
        panel.adjustSize()
        panel.move(*self._dim_free_spot(at, panel))
        panel.raise_()
        box.activate()
        kind = self._dim_kind(t)
        space = "  ·  Space: centres, overall, gap, far edges along X and Y, gap along X and Y" if kind in SPANS else "  ·  Space: from the centre, the gap, the far edge, along X and Y" if kind in ROUND_POINT else "  ·  Space: from the centre, the gap, the far edge" if kind in ROUND_LINE else "  ·  Space: radius or diameter" if kind in ("dim_r", "dim_d") else ""
        self.prompt.emit("Dimension  Type a value or a formula, click a dimension for its name  ·  Enter applies" + space + "  ·  Esc gives it up")

    def _dim_free_spot(self, at, panel):
        """Where the box goes: on its dimension if nothing is under it, else the nearest place that leaves the shapes seen."""
        w, h = panel.width(), panel.height()
        low = getattr(self, "window_top", 8)
        polys = [[self.to_screen(self.pts[a]), self.to_screen(self.pts[b])] for a, b in self.lines]
        polys += [list(self._arc_poly(*self.arc_shape(i))) for i in range(len(self.arcs))]
        polys += [list(self._ell_poly_of(i)) for i in range(len(self.ells))]
        polys += [[self.to_screen(q) for q in self.spl_poly(i)] for i in range(len(self.spls))]

        def covers(x, y):
            box = QtCore.QRectF(x, y, w, h).adjusted(-4, -4, 4, 4)
            n = 0
            for poly in polys:
                for a, b in itertools.pairwise(poly):
                    lo_x, hi_x, lo_y, hi_y = min(a.x(), b.x()), max(a.x(), b.x()), min(a.y(), b.y()), max(a.y(), b.y())
                    if hi_x < box.left() or lo_x > box.right() or hi_y < box.top() or lo_y > box.bottom():
                        continue
                    if box.contains(a) or box.contains(b) or any(
                            QtCore.QLineF(a, b).intersects(side)[0] == QtCore.QLineF.BoundedIntersection for side in (
                                QtCore.QLineF(box.topLeft(), box.topRight()), QtCore.QLineF(box.bottomLeft(), box.bottomRight()),
                                QtCore.QLineF(box.topLeft(), box.bottomLeft()), QtCore.QLineF(box.topRight(), box.bottomRight()))):
                        n += 1
                        break
            return n

        x0, y0 = at.x() - w / 2, at.y() - h / 2
        best = None
        for reach in range(0, 5):
            for dx, dy in ((0, 0),) if reach == 0 else ((0, -1), (0, 1), (-1, 0), (1, 0), (-1, -1), (1, -1), (-1, 1), (1, 1)):
                x = min(max(x0 + dx * reach * (w + 8), self.left_inset + 4), self.width() - w - 4)
                y = min(max(y0 + dy * reach * (h + 8), low), self.height() - h - 4)
                n = covers(x, y)
                if best is None or n < best[0]:
                    best = (n, x, y)
                if n == 0:
                    return int(x), int(y)
        return int(best[1]), int(best[2])

    def _dim_label(self):
        t = self.dim_edit
        self.dim_panel.name.setText(t.get("name", "") + " =" + {"dim_r": " R", "dim_d": " Ø"}.get(self._dim_kind(t), ""))
        self.dim_panel.adjustSize()

    def _dim_round(self):
        return self.dim_edit is not None and self._dim_kind(self.dim_edit) in ("dim_r", "dim_d")

    def _dim_swap(self, kind):
        """R or D in the box: a radius becomes a diameter and back, the number with it, the circle stays."""
        t = self.dim_edit
        if t is None or self._dim_kind(t) == kind:
            return
        k = 2.0 if kind == "dim_d" else 0.5
        t["kind"] = kind
        t["as_d"] = False
        t["ref"] *= k
        box = self.box_dim
        v = _eval(units.literals(box.text()), {})
        if v is not None:
            text = units.edit(v * k)
            box.setText(text)
            if box.pending is not None:
                box.pending = text
            box.selectAll()
        self._dim_label()
        self._refresh()

    def _dim_space(self):
        t = self.dim_edit
        if self._dim_round():
            self._dim_swap("dim_d" if self._dim_kind(self.dim_edit) == "dim_r" else "dim_r")
        elif t is not None and self._dim_kind(t) in (*SPANS, *ROUND_ANY):
            if self._dim_uses(t.get("name")):
                self.prompt.emit("Dimension: a formula uses it, it keeps its kind")
                return
            t.pop("kinds", None)
            row = SPANS if t["kind"] in SPANS else ROUND_LINE if t["kind"] in ROUND_LINE else ROUND_POINT
            t["kind"] = row[(row.index(t["kind"]) + 1) % len(row)]
            t["ref"] = self._dim_value(t)
            t["expr"] = None
            self.box_dim.setText(units.edit(units.shown(t["ref"])))
            self.box_dim.pending = None
            self.box_dim.selectAll()
            self._refresh()

    def _dim_whole(self, steps):
        """The next whole numbers up or down: 49.06 goes to 50, then 51."""
        t = self.dim_edit
        if t is not None:
            now = self._dim_shown(t)
            self._dim_nudge((math.floor(now + 1e-9) if steps > 0 else math.ceil(now - 1e-9)) + steps - now)

    def _dim_nudge(self, step):
        """The dot or the wheel: the size changes at once, ✕ and Esc bring the old one back."""
        t = self.dim_edit
        if t is None:
            return
        now = self._dim_shown(t)
        v = now + step
        if now != 0 and v * now <= 0:  # it stops short of zero, the sign stays
            v = math.copysign(1.0 if abs(now) > 1 else abs(now), now)
        text = units.edit(v) if self._dim_kind(t) not in ("dim_ang", "dim_angx") else "%.2f" % v
        if self._dim_set(t, text) is None:
            self.box_dim._put(text, True)
            self.dim_panel.say("")
            self._refresh()

    def _dim_claim(self):
        names = self._dim_names()
        while "d%d" % self.dim_next in names:
            self.dim_next += 1
        self.dim_next += 1
        return "d%d" % (self.dim_next - 1)

    def _dim_names(self):
        return {t["name"] for t in self.dims() if t.get("name")} | {q["name"] for q in self.params} | set(self.foreign)

    def _dim_prune(self):
        """Every dimension has its own name; a formula naming something gone stays as the number it came to."""
        seen = set()
        for t in self.dims():
            if not t.get("name") or t["name"] in seen:
                t["name"], t["expr"] = self._dim_claim(), None
            seen.add(t["name"])
        names = self._dim_names()
        for q in (*self.dims(), *self.params):
            if q.get("expr") and not set(_names(q["expr"])) <= names:
                q["expr"] = None

    def _dim_env(self):
        out = dict(self.foreign)  # the other sketches' dimensions, as they stand (as in the old program: one part)
        out.update({t["name"]: self._dim_public(t) for t in self.dims() if t.get("name")})
        out.update({q["name"]: q["value"] for q in self.params})
        return out

    def _dim_public(self, t):
        """Its value for formulas and Parameters: a circle's diameter cut to an arc reads R on the arc, and stays a
        diameter for everything else (as_d)."""
        return self._dim_shown(t) * (2 if t.get("as_d") else 1)

    def _dim_public_expr(self, t):
        expr = t.get("expr")
        if expr and t.get("as_d") and expr.startswith("(") and expr.endswith(")/2"):
            return expr[1:-3]
        return expr

    def _dim_uses(self, name):
        return self.pat_uses(name) + [q["name"] for q in (*self.dims(), *self.params) if q.get("expr") and name in _names(q["expr"])]

    def _dim_loops(self, name, text):
        """The formula text for name would come back to name."""
        exprs = {q["name"]: q.get("expr") or "" for q in (*self.dims(), *self.params) if q.get("name")}
        exprs[name] = text
        seen, todo = set(), list(_names(text))
        while todo:
            k = todo.pop()
            if k == name:
                return True
            if k not in seen:
                seen.add(k)
                todo += _names(exprs.get(k, ""))
        return False

    def _dim_read(self, name, text):
        """(value, formula or None, what is wrong or None) for what was typed."""
        text = text.strip()
        names = set(_names(text))
        env = self._dim_env()
        unknown = sorted(names - set(env))
        if unknown:
            return None, None, "No dimension is called " + unknown[0]
        if self._dim_loops(name, text):
            return None, None, "It would depend on itself"
        v = _eval(units.literals(text), env)
        if v is None:
            return None, None, "This is not a number or a formula"
        return v, text if names else None, None

    def _dim_row(self, t, kind, pts, v):
        if kind == "dim_angx":
            return sk.angle_axis(*pts, t["base"], t["first"], v)
        return CONSTRAINT_TIES[kind](*pts, v)

    def _dim_solve(self, kind, pts, raw, rels, skip, t=None):
        coords = [(p.x(), p.y()) for p in self.pts]
        stays, loose = self._off_of(pts)
        rels = rels + self._hold_radii([*skip, *loose]) + self._hold_ells()
        if kind in SPANS:  # longer, not wider
            rels += [sk.radius_is(c, a, _len(self.pts[a] - self.pts[c])) for c, a in (pts[:2], pts[2:])]
        steady = []
        level = {"dim_dx": (pts[:2], True), "dim_dy": (pts[:2], False), "dim_far_x": (pts[0::2], True), "dim_gap_x": (pts[0::2], True),
                 "dim_far_y": (pts[0::2], False), "dim_gap_y": (pts[0::2], False), "dim_rp_gap_x": (pts[0::2], True), "dim_rp_far_x": (pts[0::2], True),
                 "dim_rp_gap_y": (pts[0::2], False), "dim_rp_far_y": (pts[0::2], False)}.get(kind)
        if level is not None:  # a size along X moves it along X only, and along Y along Y only
            (p, q), along_x = level
            d = self.pts[q] - self.pts[p]
            steady = [sk.offset_y(p, q, d.y()) if along_x else sk.offset_x(p, q, d.x())]  # first choice: a line tied to a length has to turn
        if kind in ROUND_ANY:  # the round goes, the line or point and its radius stay
            rels += [sk.radius_is(pts[0], pts[1], _len(self.pts[pts[1]] - self.pts[pts[0]]))]
        got = self._dim_row(t, kind, pts, raw)
        rows = got if isinstance(got, list) else [got]
        holds = list(steady)
        keep = {"dim_par": pts[1:], "dim_ring": pts[:2], **dict.fromkeys(ROUND_ANY, pts[2:]), "dim_ang": pts[:2], "dim_x": [], "dim_y": [], "dim_o": [], "dim_lo": []}.get(kind, pts[:1])
        keep = [*keep, *stays]  # an offset's size moves the copy, never what it was copied from
        if kind in ("dim_r", "dim_d"):
            stays, holds = self._slot_dim_holds(pts)
            keep = [*keep, *stays]
            widest = self.slot_widest(pts[0])
            if widest is not None and (raw / 2 if kind == "dim_d" else raw) > widest:
                return None  # an arc slot's end wider than the slot can be
        if kind == "dim_angx":  # the end nearer the axis stays, the line turns round it
            at = self._dim_centre(t)
            keep = [min(pts, key=lambda k: _len(self.pts[k] - at))] if at is not None else pts[:1]
        move = [k for k in pts if k not in keep]
        fixed = self._fixed()
        seeds = [k for k in move if k not in fixed]
        out = sk.solve(coords, rels + holds + rows, {}, fixed | set(keep), seeds=seeds) if holds else None
        if out is None:  # the width has a dimension of its own, say: the other radius goes with it
            out = sk.solve(coords, rels + rows, {}, fixed | set(keep), seeds=seeds)
        if out is None:
            out = sk.solve(coords, rels + rows, {}, fixed, seeds=list(pts))
        return out

    def _dim_write(self, t, v):
        """The value v, as people read it, into the sketch; what is wrong, or None."""
        kind, pts = self._dim_kind(t), t["pts"]
        if kind not in ("dim_ang", "dim_angx"):
            v = units.to_mm(v)
        if abs(v) < 1e-9:
            return "It cannot be zero"
        if v < 0 and kind in ("dim_r", "dim_d"):
            return "A radius or a diameter cannot be negative"
        was = t["ref"] if t.get("ref") is not None else self._dim_value(t)
        if kind in ("dim_ang", "dim_angx"):
            raw = math.radians(v) % TAU
        elif kind in ("dim_dx", "dim_dy", "dim_par", "dim_ring", "dim_x", "dim_y"):  # negative: over to the other side
            raw = math.copysign(1.0, was) * (-1.0 if t.get("neg") else 1.0) * v
            if kind in ("dim_dx", "dim_dy"):
                self._dim_along(kind, pts, raw)
        else:
            raw = abs(v)
            if kind == "dim_len" and (v < 0) != bool(t.get("neg")):
                a, b = self.pts[pts[0]], self.pts[pts[1]]
                self.pts[pts[1]] = a + a - b
        arcs = [i for i, arc in enumerate(self.arcs) if arc[0] == pts[0] and arc[1] == pts[1]] if kind in ("dim_r", "dim_d") else []
        t["ref"] = raw
        got = self._dim_solve(kind, list(pts), raw, self._rels(), arcs, t)
        if got is None:
            return "It conflicts with the other constraints"
        for p, (x, y) in zip(self.pts, got):
            p.setX(x)
            p.setY(y)
        t["neg"] = v < 0
        return None

    def _dim_along(self, kind, pts, raw):
        """A line's horizontal or vertical size changes its length, not its angle: its end goes along it (the user)."""
        if tuple(pts) not in self.lines and tuple(pts[::-1]) not in self.lines:
            return
        a, b = self.pts[pts[0]], self.pts[pts[1]]
        now = (b - a).x() if kind == "dim_dx" else (b - a).y()
        if abs(now) < 1e-9:
            return
        fixed, k = self._fixed(), raw / now
        if pts[1] not in fixed:
            self.pts[pts[1]] = a + (b - a) * k
        elif pts[0] not in fixed:
            self.pts[pts[0]] = b - (b - a) * k

    def _dim_follow(self):
        """Every formula again, until nothing changes; what is wrong, or None."""
        for _pass in range(len(self.ties) + len(self.params) + 2):
            moved = False
            for q in self.params:
                if q.get("expr"):
                    v = _eval(units.literals(q["expr"]), self._dim_env())
                    if v is None:
                        return "A formula cannot be worked out"
                    moved = moved or abs(v - q["value"]) > 1e-6
                    q["value"] = v
            for t in self.dims():
                if t.get("expr"):
                    v = _eval(units.literals(t["expr"]), self._dim_env())
                    if v is None:
                        return "A formula cannot be worked out"
                    if abs(v - self._dim_shown(t)) > 1e-6:
                        moved = True
                        wrong = self._dim_write(t, v)
                        if wrong:
                            return t["name"] + ": " + wrong
            if not moved:
                self.pat_follow()
                return None
        return "These formulas feed themselves, they never settle"  # through the shape, by way of a reference dimension

    def dim_follow_reference(self):
        """A formula on a reference dimension: the sketch moved, so that value did too. It settles at rest; if it
        cannot, what stood stays."""
        driven = {t["name"] for t in self.dims() if t.get("driven")}
        if not driven or self.dim_panel.isVisible() or self.begun():
            return
        if not any(q.get("expr") and driven & set(_names(q["expr"])) for q in (*self.dims(), *self.params)):
            return
        back = self._dim_keep()
        wrong = self._dim_follow()
        if wrong:
            back()
            self.prompt.emit(wrong + ", nothing changed")
        self.update()

    def _dim_keep(self):
        kept = [P(p) for p in self.pts], [(t, t.get("ref"), t.get("neg"), t.get("expr")) for t in self.dims()], \
            [dict(q) for q in self.params]

        def back():
            self.pts = kept[0]
            for t, ref, neg, expr in kept[1]:
                t["ref"], t["neg"], t["expr"] = ref, neg, expr
            self.params = kept[2]
        return back

    def _dim_set(self, t, text):
        """What was typed for this dimension, and all that follows from it; what is wrong, or None."""
        back = self._dim_keep()
        v, expr, wrong = self._dim_read(t["name"], text)
        if wrong is None:
            t["expr"] = expr
            wrong = self._dim_write(t, v) or self._dim_follow()
        if wrong:
            back()
        return wrong

    def _dim_commit(self):
        box, t = self.box_dim, self.dim_edit
        if t is None:
            box.hide()
            return
        if box.pending is not None:
            wrong = self._dim_set(t, box.pending)
            if wrong:
                self.dim_panel.say(wrong)
                self.prompt.emit("Dimension: " + wrong.lower() + ", nothing changed")
                box.setFocus()
                self._refresh()
                return
        self.steps.append(("state", self.dim_st, len(self.lines), len(self.arcs)))
        log.say("dimension", t["name"], self._dim_kind(t), t["pts"], t["ref"], t.get("expr"))
        self._dim_close()
        self.changed.emit()
        if self.tool == "dimension":
            self._dim_prompt()
        self._refresh()

    def _dim_close(self):
        self.dim_picks, self.dim_new, self.dim_edit, self.dim_st = [], None, None, None
        self.box_dim.reset()
        self.box_dim.hide()
        self.dim_panel.say("")
        self.setFocus()

    def _dim_cancel(self):
        """Esc or ✕: all as it was, and one only just placed is gone."""
        self._pan_from = None
        if self.dim_st is not None:
            self._restore(self.dim_st)
            self.sel_dims = set()
        self._dim_close()
        if self.tool == "dimension":
            self._dim_prompt()
        else:
            self.prompt.emit("Pick a sketch tool  ·  pull a line or a point to move it  ·  right-click: Finish Sketch")
        self.changed.emit()
        self._refresh()

    # ----- the Parameters window: every dimension and the user's own values -----
    def param_rows(self):
        """(kind, name, where it comes from, value or formula, result, used by) for each one."""
        self._dim_prune()
        rows = [("dimension", t["name"], self.sketch_name, self._dim_public_expr(t) or self._dim_word(t), self._dim_word(t),
                 ", ".join(self._dim_uses(t["name"]))) for t in self.dims()]
        extra = self.extra_rows() if getattr(self, "extra_rows", None) else []  # the part's features' values
        return rows + extra + [("user", q["name"], "", q.get("expr") or units.number(q["value"]), units.number(q["value"]),
                                ", ".join(self._dim_uses(q["name"]))) for q in self.params]

    def _dim_word(self, t):
        v = self._dim_public(t)
        return "%.2f" % v if self._dim_kind(t) in ("dim_ang", "dim_angx") else units.number(v)

    def _param_done(self, st):
        self.steps.append(("state", st, len(self.lines), len(self.arcs)))
        self.changed.emit()
        self._refresh()

    def param_set(self, name, text):
        if getattr(self, "extra_set", None) and name not in {t.get("name") for t in self.dims()} \
                and name not in {q["name"] for q in self.params}:
            return self.extra_set(name, text)  # a feature's value: the part takes it
        st = self._state()
        t = next((t for t in self.dims() if t.get("name") == name), None)
        if t is not None and t.get("driven"):
            return "A reference dimension follows the sketch"
        if t is not None:
            if t.get("as_d"):  # typed here it is the diameter
                v = _eval(units.literals(text), {})
                text = "%.6f" % (v / 2) if v is not None else "(%s)/2" % text
            wrong = self._dim_set(t, text)
        else:
            q = next(q for q in self.params if q["name"] == name)
            back = self._dim_keep()
            v, expr, wrong = self._dim_read(name, text)
            if wrong is None:
                q["value"], q["expr"] = v, expr
                wrong = self._dim_follow()
            if wrong:
                back()
        if wrong is None:
            self._param_done(st)
        return wrong

    def param_rename(self, old, new):
        """The user's own values only: a dimension stays d0 for good, formulas stand on that (the user)."""
        if not re.fullmatch(r"[A-Za-z_]\w*", new):
            return "A name is letters and digits, starting with a letter"
        if new in self._dim_names():
            return new + " is taken"
        st = self._state()
        for q in (*self.dims(), *self.params):
            if q.get("name") == old:
                q["name"] = new
            if q.get("expr"):
                q["expr"] = NAME.sub(lambda m: new if m.group(0) == old else m.group(0), q["expr"])
        self._param_done(st)
        return None

    def param_add(self, name, text):
        if not re.fullmatch(r"[A-Za-z_]\w*", name):
            return "A name is letters and digits, starting with a letter"
        if name in self._dim_names():
            return name + " is taken"
        v, expr, wrong = self._dim_read(name, text)
        if wrong:
            return wrong
        st = self._state()
        self.params.append(dict(name=name, expr=expr, value=v))
        self._param_done(st)
        return None

    def param_remove(self, name):
        if any(t.get("name") == name for t in self.dims()):
            return "A dimension goes from the sketch, with Delete"
        used = self._dim_uses(name)
        if used:
            return "Used by " + ", ".join(used)
        st = self._state()
        self.params = [q for q in self.params if q["name"] != name]
        self._param_done(st)
        return None

    def param_delete_unused(self):
        st = self._state()
        n = len(self.params)
        self.params = [q for q in self.params if self._dim_uses(q["name"])]
        if len(self.params) != n:
            self._param_done(st)

    def _dim_back(self):
        """Esc or the right button: what was begun goes first, then the tool."""
        if self.box_dim.isVisible() or self.dim_picks:
            self._dim_cancel()
        else:
            self.stop_tool()

    # ----- Ellipse -----
    def _ell_now(self):
        """The ellipse as it stands: (centre, a, b, frame corners or None)."""
        if self.mouse is None:
            return None
        if self.tool == "ellipse_rot" and self._rect_stage() == 1:
            a, e = self.rect_pts[0], self._rect_side_end()  # the side is still going: half as high, until it is known
            u = e - a
            n = _len(u)
            if n < 1e-9:
                return None
            h = abs(self.box_h.typed) if self.box_h.typed is not None else n / 2
            side = P(-u.y(), u.x()) / n * h
            frame = [P(a), P(e), e + side, a + side]
            return (frame[0] + frame[2]) * 0.5, (frame[0] + frame[1]) * 0.5, (frame[1] + frame[2]) * 0.5, frame
        if self.tool in ("ellipse_rot", *FRAME_TOOLS):
            frame = self._rect_corners()
            if self.tool == "ellipse_rot" and frame and self.arc_flip:  # mirrored in the first side
                p, q = frame[0], frame[1]
                w = (q - p) / _len(q - p)
                frame = [P(p), P(q)] + [p + w * (2 * QtCore.QPointF.dotProduct(r - p, w)) - (r - p) for r in frame[2:]]
            if not frame or abs((frame[1] - frame[0]).x() * (frame[2] - frame[1]).y()
                                - (frame[1] - frame[0]).y() * (frame[2] - frame[1]).x()) < 1e-9:
                return None
            return (frame[0] + frame[2]) * 0.5, (frame[0] + frame[1]) * 0.5, (frame[1] + frame[2]) * 0.5, frame
        if self._rect_stage() < 2:
            return None
        if self._rect_stage() >= 3:  # an elliptical arc: the ellipse is settled
            return *self.rect_pts[:3], None
        t = self._rect_target()
        c, a = self.rect_pts
        u = a - c
        n = _len(u)
        if n < 1e-9:
            return None
        w = u / n
        side = P(-w.y(), w.x())
        d = t - c
        x, y = QtCore.QPointF.dotProduct(d, w), QtCore.QPointF.dotProduct(d, side)
        h = abs(y) / math.sqrt(1 - (x / n) ** 2) if abs(x) < n * 0.999 else abs(y)
        if self.box_h.typed is not None:
            h = abs(self.box_h.typed) / 2
        if h < 1e-6:
            return None
        sign = (1.0 if y >= 0 else -1.0) * (-1.0 if self.arc_flip else 1.0)
        return c, P(a), c + side * (h * sign), None

    def _cut_start(self):
        """Elliptical Arc: the parameter where it starts, as it stands."""
        c, a, b = self.rect_pts[:3]
        u, v = a - c, b - c
        if len(self.rect_pts) > 3:
            return _ell_t(c, u, v, self.rect_pts[3])
        if self.box_ra.typed is not None:
            return _ell_param(u, v, math.radians(self.box_ra.typed))
        q = self._rect_target()
        return sk.ell_nearest(c.x(), c.y(), u.x(), u.y(), v.x(), v.y(), q.x(), q.y())[0]

    def _cut_now(self):
        """Elliptical Arc, last stage: (centre, u, v, start parameter, sweep, signed), or None."""
        if self._rect_stage() != 4 or self.mouse is None:
            return None
        c, a, b = self.rect_pts[:3]
        u, v = a - c, b - c
        t0, sweep = self._cut_start(), self.arc_sweep
        if self.hint is not None:  # exactly on the magnet, the way it was going
            q = self.hint["at"]
            raw = sk.ell_nearest(c.x(), c.y(), u.x(), u.y(), v.x(), v.y(), q.x(), q.y())[0] - t0
            sweep = raw + round((sweep - raw) / TAU) * TAU
        if self.arc_flip and sweep:
            sweep -= math.copysign(TAU, sweep)  # to the same end, the other way round
        if self.box_sw.typed is not None:
            way = math.copysign(1.0, sweep or 1.0)
            t1 = _ell_param(u, v, _ell_angle(u, v, t0) + way * math.radians(abs(self.box_sw.typed)))
            sweep = way * ((way * (t1 - t0)) % TAU)
        return (c, u, v, t0, sweep) if abs(sweep) > 1e-9 else None

    def _cut_seen(self, cut):
        """The sweep as a true angle, for the box."""
        _c, u, v, t0, sweep = cut
        way = 1.0 if sweep >= 0 else -1.0
        return (way * (_ell_angle(u, v, t0 + sweep) - _ell_angle(u, v, t0))) % TAU

    def _ell_arc_marks(self, m, tol, out):
        """Elliptical Arc's start and end: the axes' ends, and from the start every 15° the way it is going."""
        c, a, b = self.rect_pts[:3]
        u, v = a - c, b - c
        at = _ell_at(c, u, v, sk.ell_nearest(c.x(), c.y(), u.x(), u.y(), v.x(), v.y(), m.x(), m.y())[0])
        marks = [("Axis end", c + u), ("Axis end", c - u), ("Axis end", c + v), ("Axis end", c - v)]
        if self._rect_stage() == 4:
            t0 = self._cut_start()
            way = -1.0 if (self.arc_sweep >= 0) == self.arc_flip else 1.0
            th = _ell_angle(u, v, t0)
            marks = [("Half turn" if k == 180 else "%d°" % k,
                      _ell_at(c, u, v, _ell_param(u, v, th + way * math.radians(k)))) for k in range(15, 360, 15)] + marks
            marks = [q for q in marks if _len(q[1] - self.rect_pts[3]) > 1e-6]  # the start itself is no end
        for label, p in marks:
            d = _len(p - at)
            if d <= tol:
                out.append((2, d, dict(kind="sweep", label=label, at=P(p), ref=None, guides=[])))

    def _ell_finish(self):
        now = self._ell_now()
        if now is None:
            return
        cut = None
        if self.tool == "arc_ellipse":
            cut = self._cut_now()
            if cut is None:
                return
        st = self._state()
        c, a, b, frame = now
        self.arc_flip = False
        ic = self._point_index(c)
        if cut is not None:  # the ends first: on an axis's end, the arc's own end is what a pull takes
            _c, u, v, t0, sweep = cut
            ie, ig = [self._point_index(_ell_at(c, u, v, t), 1e-6) for t in ((t0, t0 + sweep) if sweep > 0 else (t0 + sweep, t0))]
        self.pts.append(P(a))
        ia = len(self.pts) - 1
        self.pts.append(P(b))
        ib = len(self.pts) - 1
        self.ells.append((ic, ia, ib))
        self.ell_constr = (self.ell_constr + [False] * len(self.ells))[:len(self.ells) - 1] + [self.construction]
        if cut is not None:
            self._add_tie("con_perp", [ic, ia, ic, ib])
            self._add_tie("ell_cut", [ie, ig, ic, ia, ib])
            self._tie_shape((ic, ie, ig))
            self.steps.append(("state", st, len(self.lines), len(self.arcs)))
            self._shape_done("elliptical arc %s" % log.pt(c))
            return
        if frame is None:
            self._add_tie("con_perp", [ic, ia, ic, ib])
            self._tie_shape((ic,))
        else:
            idx = [self._point_index(q) for q in frame]
            first = len(self.lines)
            for k in range(4):
                self.lines.append((idx[k], idx[(k + 1) % 4]))
            self.constr = (self.constr + [False] * len(self.lines))[:first] + [True] * 4  # the frame stays, dashed (the user)
            self._add_tie("ell_frame", [ic, ia, ib, *idx])
            if self.tool == "ellipse_rot":  # at an angle it stays a rectangle
                self._add_tie("con_perp", [idx[0], idx[1], idx[1], idx[2]])
            self._tie_shape(idx)
        self.steps.append(("state", st, len(self.lines), len(self.arcs)))
        self._shape_done("ellipse %s" % log.pt(c))

    # ----- Tangent Circle -----
    def _tc_solutions(self):
        """The circles touching what was picked: three things, or two and the diameter; the one by the pointer first."""
        typed = self.box_r.typed
        n = len(self.tc_picks)
        if not (n == 3 or (n == 2 and typed is not None)):
            return []
        key = (tuple(self.tc_picks), typed)
        if key != self.tc_key:
            objs = self._tc_objects()
            sols = sk.tangent_circles(objs, self._tc_seeds(), abs(typed) / 2 if typed is not None else None)
            at = self.mouse if self.mouse is not None else self.rect_pts[0]
            self.tc_sols = sorted(sols, key=lambda v: math.hypot(v[0] - at.x(), v[1] - at.y()))
            self.tc_key, self.tc_at = key, 0
        return self.tc_sols

    def _tc_seeds(self):
        clicks = self.rect_pts
        mid = sum(clicks, P(0, 0)) / len(clicks)
        far = max(1.0, max(_len(q - mid) for q in clicks))
        seeds = [(q.x(), q.y()) for q in [mid, *clicks]]
        for k in range(8):
            for ring in (0.6, 2.0):
                ang = k * math.pi / 4
                seeds.append((mid.x() + far * ring * math.cos(ang), mid.y() + far * ring * math.sin(ang)))
        return seeds

    def _tc_why(self, dia):
        """Why no circle of the diameter dia touches the two picked: too large or too small, and what would do."""
        objs = self._tc_objects()
        r = abs(dia) / 2
        if all(o[0] == "line" for o in objs):
            (ax, ay, bx, by), (cx, cy, ex, ey) = objs[0][1:], objs[1][1:]
            ux, uy = bx - ax, by - ay
            if abs(ux * (ey - cy) - uy * (ex - cx)) < 1e-9 * math.hypot(ux, uy) * math.hypot(ex - cx, ey - cy):
                need = abs(ux * (cy - ay) - uy * (cx - ax)) / math.hypot(ux, uy)
                return "Diameter too %s: parallel lines need exactly Ø %s" % ("large" if abs(dia) > need else "small", units.length(need))
        seeds = self._tc_seeds()
        works = lambda q: bool(sk.tangent_circles(objs, seeds, q))
        for way in (0.5, 2.0):
            q = r
            for _k in range(12):
                q *= way
                if works(q):
                    lo, hi = q, r  # q works, r does not: the limit lies between
                    for _i in range(30):
                        m = (lo + hi) / 2
                        lo, hi = (m, hi) if works(m) else (lo, m)
                    if way < 1:
                        return "Diameter too large: at most Ø %s" % units.length(2 * lo)
                    return "Diameter too small: at least Ø %s" % units.length(2 * lo)
        return "No circle of Ø %s touches them" % units.length(abs(dia))

    def _tc_objects(self):
        objs = []
        for kind, i in self.tc_picks:
            if kind == "axis":
                objs.append(("line", 0.0, 0.0, 1.0, 0.0) if i == "x" else ("line", 0.0, 0.0, 0.0, 1.0))
            elif kind == "line":
                a, b = [self.pts[k] for k in self.lines[i]]
                objs.append(("line", a.x(), a.y(), b.x(), b.y()))
            elif kind == "arc":
                c, r = self.arc_shape(i)[:2]
                objs.append(("circle", c.x(), c.y(), r))
            elif kind == "ell":
                c, u, v = self.ell_shape(i)
                objs.append(("ellipse", c.x(), c.y(), u.x(), u.y(), v.x(), v.y()))
            else:
                objs.append(("point", self.pts[i].x(), self.pts[i].y()))
        return objs

    def _tc_preview(self):
        """Before the last pick the circle follows the pointer: it goes through it and touches what is picked, at the
        click if that is one thing."""
        if not self.tc_picks or self.mouse is None:
            return None
        m = self._rect_target()
        key = (tuple(self.tc_picks), m.x(), m.y(), self.box_r.typed)
        if key != self.tc_seen[0]:
            self.tc_seen = (key, self._tc_through(m))
        return self.tc_seen[1]

    def _tc_through(self, m):
        click = self.rect_pts[0]
        size = abs(self.box_r.typed) / 2 if self.box_r.typed is not None else None
        if len(self.tc_picks) == 1:
            kind, i = self.tc_picks[0]
            if kind == "point":
                d = m - self.pts[i]
                if size is not None and _len(d) > 1e-9:
                    return self.pts[i] + d / _len(d) * size, size, 0.0, TAU
                c, r = (self.pts[i] + m) * 0.5, _len(self.pts[i] - m) / 2
                return (c, r, 0.0, TAU) if r > 1e-9 else None
            if kind == "axis":
                a, b = P(0, 0), P(1, 0) if i == "x" else P(0, 1)
                u = b - a
                foot = a + u * QtCore.QPointF.dotProduct(click - a, u)
                way = P(-u.y(), u.x())
            elif kind == "line":
                a, b = [self.pts[k] for k in self.lines[i]]
                u = (b - a) / _len(b - a)
                foot = a + u * QtCore.QPointF.dotProduct(click - a, u)
                way = P(-u.y(), u.x())
            elif kind == "ell":
                c0, u, v = self.ell_shape(i)
                t = self._ell_nearest(i, click)
                foot = _ell_at(c0, u, v, t)
                along = v * math.cos(t) - u * math.sin(t)
                way = P(-along.y(), along.x()) / _len(along)
            else:
                c0, r0 = self.arc_shape(i)[:2]
                way = (click - c0) / _len(click - c0)
                foot = c0 + way * r0
            d = m - foot
            across = QtCore.QPointF.dotProduct(d, way)
            if abs(across) < 1e-9:
                return None
            t = QtCore.QPointF.dotProduct(d, d) / (2 * across)  # the centre on the normal at the foot, as far from both
            if size is not None:
                t = math.copysign(size, across)
            c = foot + way * t
            return c, abs(t), _ang(foot - c), TAU
        clicks = self.rect_pts
        seeds = [(m.x(), m.y())] + [(q.x(), q.y()) for q in clicks] + [((clicks[0].x() + m.x()) / 2, (clicks[0].y() + m.y()) / 2)]
        sols = sk.tangent_circles(self._tc_objects() + [("point", m.x(), m.y())], seeds)
        if not sols:
            return None
        x, y, r = min(sols, key=lambda v: v[2])
        return P(x, y), r, 0.0, TAU

    def _tc_prompt(self):
        sols = self._tc_solutions()
        if sols:
            many = "  ·  Space: the next of %d" % len(sols) if len(sols) > 1 else ""
            self.prompt.emit(self._tool_name() + "  Click, Enter or right-click places it" + many)
        else:
            self.prompt.emit(self._tool_name() + "  " + RECT_PROMPTS[self.tool][min(2, len(self.tc_picks))]
                             + "  ·  right-click " + ("places it" if len(self.tc_picks) == 2 else "gives it up" if self.tc_picks else "stops"))

    def _tc_axis(self, s):
        """The axis at the screen point s: a circle can touch the axes too (the user)."""
        o = self.to_screen(P(0, 0))
        near = [(abs(s.y() - o.y()), "x"), (abs(s.x() - o.x()), "y")]
        d, axis = min(near)
        return ("axis", axis) if d <= PICK_LINE_PX else None

    def _tc_click(self, m):
        if self._tc_solutions():
            self._arc_finish()
            return
        what = self._pick(self.to_screen(m)) or self._tc_axis(self.to_screen(m))
        if what is None or what in self.tc_picks:
            return
        self.tc_picks.append(what)
        self.rect_pts.append(P(m))
        if len(self.tc_picks) == 2 and self.box_r.typed is not None:  # the size was given: this was all it waited for
            if self._tc_solutions():
                self._arc_finish()
            else:
                why = self._tc_why(self.box_r.typed)
                self.tc_picks.pop()
                self.rect_pts.pop()
                self.prompt.emit(self._tool_name() + "  " + why + "  ·  click another one, or right-click to let the size go")
            return
        if len(self.tc_picks) == 3 and not self._tc_solutions():
            objs = self._tc_objects()
            self.tc_picks.pop()
            self.rect_pts.pop()
            lines_ = [o for o in objs if o[0] == "line"]
            turns = {round(math.atan2(o[4] - o[2], o[3] - o[1]) % math.pi, 7) % round(math.pi, 7) for o in lines_}
            why = "All three are parallel, no circle can touch them" if len(lines_) == 3 and len(turns) == 1 \
                else "They stand so that no circle touches all three"
            self.prompt.emit(self._tool_name() + "  " + why + "  ·  click another one")
        else:
            self._tc_prompt()
        self._refresh()

    def _tc_ties(self, picks, i):
        """The new circle stays on what it touches."""
        was, self.con_kind = self.con_kind, "Tangent"
        c, rim = self.arcs[i][:2]
        for what in picks:
            if what[0] == "point":
                self._add_tie("con_on_arc", [c, rim, what[1]], None, "Coincident")
            elif what[0] == "axis":
                self._add_tie("con_axis", [c, rim], what[1], "Tangent")
            else:
                for t in self._con_ties([what, ("arc", i)]):
                    self._add_tie(t["kind"], t["pts"], t["ref"], t["mark"])
        self.con_kind = was

    def _arc_finish(self):
        now = self._arc_now()
        if now is None:
            return
        c, r, a0, sweep = now
        self.arc_flip = False
        picks, self.tc_picks = self.tc_picks, []
        tol = 1e-6 if self.tool == "arc_tangent" else 1e-9  # it starts and ends on points worked out, not picked
        ic = self._point_index(c, tol)
        if self.tool in CIRCLE_TOOLS:  # its own rim point, shared with nothing: it is not shown
            self.pts.append(_on_circle(c, r, a0))
            ia = ib = len(self.pts) - 1
        else:
            ia = self._point_index(_on_circle(c, r, a0), tol)
            ib = self._point_index(_on_circle(c, r, a0 + sweep), tol)
        self.arcs.append((ic, ia, ib))
        self.arc_constr = (self.arc_constr + [False] * len(self.arcs))[:len(self.arcs) - 1] + [self.construction]
        self.steps.append((0, 1))
        self._tie_shape((ic,) if self.tool in CIRCLE_TOOLS else (ic, ia, ib))
        if self.tool == "arc_tangent":
            start = self._point_index(self.rect_pts[0], tol)
            if self.tan_src is not None and self.tan_src[0] == "ell":  # it stays on the ellipse, touching it
                e = self.ells[self.tan_src[1]]
                self._add_tie("con_on_ell", [start, *e])
                self._add_tie("con_ell_at", [start, ic, *e])
            elif self.tan_src is not None:
                self._add_tie("touch", [start], (len(self.arcs) - 1, self.tan_src))
            if self._tan is not None and self._tan["pick"]:
                self._add_tie("touch", [ib if start == ia else ia], (len(self.arcs) - 1, self._tan["curve"]))
        self._tc_ties(picks, len(self.arcs) - 1)
        self._shape_done(("circle" if self.tool in CIRCLE_TOOLS else "arc") + " %s r=%.3f from %.2f° sweep %.2f°" % (log.pt(c), r, math.degrees(a0), math.degrees(sweep)))

    # ----- Tangent Arc -----
    def _curve_tangents(self, p):
        """[(unit tangent, source)] of every line and arc p lies on; source: ("line", i) or ("arc", i)."""
        out = []
        for i, (ia, ib) in enumerate(self.lines):
            a, b = self.pts[ia], self.pts[ib]
            if _len(b - a) > 1e-9 and _on_segment(p, a, b):
                out.append(((b - a) / _len(b - a), ("line", i)))
        for i in range(len(self.arcs)):
            c, r, a0, sw = self.arc_shape(i)
            if abs(_len(p - c) - r) < 1e-6 and _in_sweep(_ang(p - c), a0, sw, 1e-6):
                rad = (p - c) / r
                out.append((P(-rad.y(), rad.x()), ("arc", i)))
        for i in range(len(self.ells)):
            q = self._ell_back(i, p)
            if q is not None and abs(_len(q) - 1) < 1e-4 and self._ell_has(i, _ang(q)):
                c, u, v = self.ell_shape(i)
                t = _ang(q)
                along = v * math.cos(t) - u * math.sin(t)
                out.append((along / _len(along), ("ell", i)))
        return out

    def _tan_choice(self, q):
        """The tangent the arc takes at its start: of those there, the one lying nearest the pointer's direction."""
        if self._tan is not None:
            return self._tan["t"], self._tan["src"]
        at = self.rect_pts[0]
        d = q - at
        n = _len(d)
        options = self._curve_tangents(at)
        if not options:
            return None, None
        if self.tan_pick is not None:  # a corner: it holds, the pointer does not move it (the user)
            return next((o for o in options if o[1] == self.tan_pick), options[0])
        return max(options, key=lambda o: abs(QtCore.QPointF.dotProduct(d, o[0])) / (n or 1.0))

    @staticmethod
    def _tan_arc(at, t, way, q, radius=None):
        """(centre, radius, start, sweep) of the arc leaving `at` along t * way, on the circle touching t there and
        going through q (or of the given radius, as far as the direction to q), or None when it would be straight."""
        d = q - at
        left = P(-t.y(), t.x())
        side = QtCore.QPointF.dotProduct(d, left)
        if abs(side) < 1e-9:
            return None
        n = left if side > 0 else -left
        r = abs(radius) if radius else QtCore.QPointF.dotProduct(d, d) / (2 * abs(side))
        if r > 1e5 or r < 1e-6:
            return None
        c = at + n * r
        e = t * way
        rad = at - c
        ccw = rad.x() * e.y() - rad.y() * e.x() > 0
        a0, aq = _ang(rad), _ang(q - c)
        s = (aq - a0) % TAU if ccw else (a0 - aq) % TAU
        if s < 1e-9:
            return None
        return (c, r, a0, s) if ccw else (c, r, a0 - s, s)

    def _tan_track(self):
        """Follows the pointer's way round: past half a circle the arc grows on; nearly round it, or back at the
        start, the arc leaves the other way."""
        if self.tool != "arc_tangent" or self._rect_stage() != 1 or self.mouse is None or self._tan is not None:
            return
        at, q = self.rect_pts[0], self._rect_target()
        t, src = self._tan_choice(q)
        if t is None or _len(q - at) * self.scale < 3:
            if not self.tan_held:
                self.tan_dir = None
            return
        if src != self.tan_src:
            self.tan_src = src
            if not self.tan_held:
                self.tan_dir = None
        if self.tan_dir is None:
            self.tan_dir = 1 if QtCore.QPointF.dotProduct(q - at, t) >= 0 else -1
        arc = self._tan_arc(at, t, self.tan_dir, q)
        if not self.tan_held and arc is not None and arc[3] > TAU - 0.35:
            self.tan_dir = -self.tan_dir

    def _tan_circles(self, at, t, curve):
        """[(centre, radius, touch point)]: the circles touching t at `at` and touching the other curve as well."""
        left = P(-t.y(), t.x())
        out = []
        kind, i = curve
        if kind == "line":
            a, b = self.pts[self.lines[i][0]], self.pts[self.lines[i][1]]
            length = _len(b - a)
            u = (b - a) / length
            m = P(-u.y(), u.x())
            h = QtCore.QPointF.dotProduct(at - a, m)
            k = QtCore.QPointF.dotProduct(left, m)
            for sign in (1.0, -1.0):
                if abs(sign - k) < 1e-9:
                    continue  # lines going the same way: that circle is endless
                s = h / (sign - k)
                c = at + left * s
                q = c - m * (h + s * k)
                along = QtCore.QPointF.dotProduct(q - a, u)
                if abs(s) > 1e-6 and -1e-6 <= along <= length + 1e-6:
                    out.append((c, abs(s), q))
            return out
        c2, r2, b0, sw = self.arc_shape(i)
        w = at - c2
        for sign in (1.0, -1.0):
            denom = 2 * (QtCore.QPointF.dotProduct(w, left) - sign * r2)
            if abs(denom) < 1e-9:
                continue
            s = (r2 * r2 - QtCore.QPointF.dotProduct(w, w)) / denom
            c = at + left * s
            dist = _len(c - c2)
            if abs(s) < 1e-6 or dist < 1e-9:
                continue
            for q in (c2 + (c - c2) / dist * r2, c2 - (c - c2) / dist * r2):
                if abs(_len(q - c) - abs(s)) < 1e-6 and _in_sweep(_ang(q - c2), b0, sw, 1e-6):
                    out.append((c, abs(s), q))
                    break
        return out

    def _tan_solved(self, q):
        """The arc touching both curves, the way q says: the nearest touch point, and the side of it q is on."""
        at = self.rect_pts[0]
        t = self._tan["t"]
        circles = self._tan_circles(at, t, self._tan["curve"])
        if not circles:
            return None
        c, r, touch = min(circles, key=lambda o: _len(o[2] - q))
        best = None
        for way in (1, -1):
            rad = at - c
            e = t * way
            ccw = rad.x() * e.y() - rad.y() * e.x() > 0
            a0, at_touch = _ang(rad), _ang(touch - c)
            s = (at_touch - a0) % TAU if ccw else (a0 - at_touch) % TAU
            if s < 1e-9:
                continue
            arc = (c, r, a0, s) if ccw else (c, r, a0 - s, s)
            out = (touch - c) / r
            arrives = P(-out.y(), out.x()) if ccw else P(out.y(), -out.x())
            score = -QtCore.QPointF.dotProduct(q - touch, arrives)  # the arc lies behind where it arrives
            if best is None or score > best[0]:
                best = (score, arc)
        return best and best[1]

    def _tan_now(self):
        if self._rect_stage() != 1 or self.mouse is None:
            return None
        if self._tan is not None and (self._tan["pick"] or self._tan["preview"]):
            return self._tan_solved(self.mouse if self._tan["pick"] else self._tan["q"])
        q = self._rect_target()
        t, _src = self._tan_choice(q)
        if t is None or self.tan_dir is None:
            return None
        return self._tan_arc(self.rect_pts[0], t, self.tan_dir, q, self.box_r.typed)

    def _tan_click(self, m):
        if self._rect_stage() == 0:
            at = self.hint["at"] if self.hint is not None else m
            if not self._curve_tangents(at):
                self.prompt.emit("Tangent Arc  Click a line or an arc: the arc starts on one")
                return
            self.rect_pts = [P(at)]
            self.rect_hints = [self.hint] if self.hint is not None else []
            self.tan_dir = self.tan_src = None
            options = [src for _t, src in self._curve_tangents(at)]
            if len(options) > 1:  # the last one drawn; Tab goes to the others
                self.tan_pick = max(options, key=lambda src: (src[0] == "line", src[1]))
            self.prompt.emit(self._tool_name() + "  " + RECT_PROMPTS[self.tool][1] + "  ·  right-click gives it up")
            self._refresh()
            return
        if self._tan is not None:
            if self._tan_now() is None:
                self.prompt.emit("Tangent Arc  No arc touches both here  ·  right-click goes back")
                return
            self._arc_finish()
            return
        hint = self.hint
        curve = None
        if hint is not None and isinstance(hint["ref"], int):
            curve = {"on": ("line", hint["ref"]), "mid": ("line", hint["ref"]),
                     "on_arc": ("arc", hint["ref"]), "arc_mid": ("arc", hint["ref"]), "quad": ("arc", hint["ref"])}.get(hint["kind"])
        if curve is not None and curve not in [s for _t, s in self._curve_tangents(self.rect_pts[0])]:
            self._tan_ask(curve, P(hint["at"]))
            return
        self._arc_finish()

    def _tan_ask(self, curve, q):
        """A click on another line or arc: tangent to it as well, or just the arc as it stands (the user)."""
        t, src = self._tan_choice(q)
        if t is None:
            return
        self._tan = dict(curve=curve, q=q, t=t, src=src, pick=False, preview=False)
        menu = QtWidgets.QMenu(self)
        menu.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        tangent = menu.addAction("Tangent")
        tangent.setEnabled(bool(self._tan_circles(self.rect_pts[0], t, curve)))
        drawn = menu.addAction("Arc as drawn")
        if not tangent.isEnabled():
            self.prompt.emit("Tangent Arc  No arc from here touches that " + curve[0])
        menu.hovered.connect(lambda act: self._tan_hover(act is tangent))
        tangent.triggered.connect(self._tan_take)
        drawn.triggered.connect(self._tan_as_drawn)
        menu.aboutToHide.connect(lambda: QtCore.QTimer.singleShot(0, self._tan_gone))
        self._tan_menu = menu
        self._refresh()
        menu.popup(QtGui.QCursor.pos() if self.underMouse() else self.mapToGlobal(self.to_screen(q).toPoint()))

    def _tan_hover(self, on_tangent):
        if self._tan is not None and not self._tan["pick"]:
            self._tan["preview"] = on_tangent
            self.update()

    def _tan_take(self):
        """Tangent chosen: the pointer along the line picks the way, the next click places it."""
        self._tan_menu = None
        if self._tan is None:
            return
        self._tan["pick"] = True
        self.box_r.reset()
        self.box_r.hide()
        self.prompt.emit("Tangent Arc  Move along the line to choose the way, click to place  ·  right-click goes back")
        self._refresh()

    def _tan_as_drawn(self):
        self._tan_menu = None
        if self._tan is not None:
            self._tan["preview"] = False
        self._arc_finish()
        self._tan = None

    def _tan_gone(self):
        """The menu closed with nothing chosen: the arc goes on following the pointer."""
        if self._tan is not None and not self._tan["pick"] and self._tan_menu is not None:
            self._tan_menu = None
            self._tan = None
            self._refresh()

    def _rounded_now(self):
        """Rounded Rectangle: (x0, y0, x1, y1, radius) as it stands, or None."""
        if self._rect_stage() < 2:
            return None
        a, b = self.rect_pts
        x0, x1 = sorted((a.x(), b.x()))
        y0, y1 = sorted((a.y(), b.y()))
        limit = min(x1 - x0, y1 - y0) / 2 * 0.999
        if self.box_r.typed is not None:
            r = abs(self.box_r.typed)
        else:  # the pointer's distance from the nearest corner, along the inside diagonal
            t = self._rect_target()
            corner = min((P(x0, y0), P(x1, y0), P(x1, y1), P(x0, y1)), key=lambda q: _len(q - t))
            r = _len(t - corner) / math.sqrt(2)
        r = max(1e-3, min(r, limit))
        return x0, y0, x1, y1, r

    def _rounded_finish(self):
        now = self._rounded_now()
        if now is None:
            return
        x0, y0, x1, y1, r = now
        pt = self._point_index
        first_line = len(self.lines)
        sides = [((x0 + r, y0), (x1 - r, y0)), ((x1, y0 + r), (x1, y1 - r)),
                 ((x1 - r, y1), (x0 + r, y1)), ((x0, y1 - r), (x0, y0 + r))]
        for p, q in sides:
            self.lines.append((pt(P(*p)), pt(P(*q))))
        corners = [((x1 - r, y0 + r), (x1 - r, y0), (x1, y0 + r)), ((x1 - r, y1 - r), (x1, y1 - r), (x1 - r, y1)),
                   ((x0 + r, y1 - r), (x0 + r, y1), (x0, y1 - r)), ((x0 + r, y0 + r), (x0, y0 + r), (x0 + r, y0))]
        for c, p, q in corners:
            self.arcs.append((pt(P(*c)), pt(P(*p)), pt(P(*q))))
        self.constr = (self.constr + [False] * len(self.lines))[:first_line] + [self.construction] * 4
        self.arc_constr = (self.arc_constr + [False] * len(self.arcs))[:len(self.arcs) - 4] + [self.construction] * 4
        self.steps.append((5, 4))
        arcs0 = len(self.arcs) - 4
        for k in range(4):
            self.relations.append(("h" if k % 2 == 0 else "v", first_line + k, None, None))
            c, p, q = self.arcs[arcs0 + k]
            self._add_tie("touch", [p], (arcs0 + k, ("line", first_line + k)))
            self._add_tie("touch", [q], (arcs0 + k, ("line", first_line + (k + 1) % 4)))
            if k:
                self._add_tie("same_r", [], (arcs0, arcs0 + k))
        self._diagonal(self.arcs[arcs0][0], self.arcs[arcs0 + 2][0])
        self._shape_done("rounded rectangle %.3f×%.3f r=%.3f" % (x1 - x0, y1 - y0, r))

    def _rect_finish(self):
        corners = self._rect_corners()
        if not corners or _len(corners[0] - corners[2]) < 1e-6 or \
                abs((corners[1] - corners[0]).x() * (corners[3] - corners[0]).y()
                    - (corners[1] - corners[0]).y() * (corners[3] - corners[0]).x()) < 1e-9:
            return  # no area: nothing drawn
        idx = []
        for c in corners:
            hit = next((k for k, q in enumerate(self.pts) if _len(q - c) < 1e-9), None)
            if hit is None:
                self.pts.append(P(c))
                hit = len(self.pts) - 1
            idx.append(hit)
        first = len(self.lines)
        for k in range(4):
            self.lines.append((idx[k], idx[(k + 1) % 4]))
        self.steps.append((5, 0))
        self.constr = (self.constr + [False] * len(self.lines))[:first] + [self.construction] * 4
        aligned = self.tool in ("rect", "rect_center", "text")
        for k in range(4):  # what the click tied: level / upright sides, or square and parallel sides
            self.relations.append((("h" if k % 2 == 0 else "v") if aligned else ("perp" if k else "free"),
                                   first + k, None, None))
        self._tie_centre(idx)
        self._tie_shape(idx)
        self._diagonal(idx[0], idx[2])
        log.say("rectangle", self.tool, [log.pt(c) for c in corners], "construction" if self.construction else "")
        self.just_done = True
        self.rect_pts = []
        self.rect_hints = []
        for b in self.shape_boxes():
            b.reset()
            b.hide()
        self.changed.emit()
        self.prompt.emit(self._tool_name() + "  " + RECT_PROMPTS[self.tool][0] + "  ·  right-click stops")
        self.setFocus()
        self._refresh()

    def _tc_place(self):
        """The diameter given: the circle goes in, or says it cannot."""
        if self._tc_solutions():
            self._arc_finish()
            return
        why = self._tc_why(self.box_r.typed)
        self.box_r.reset()
        self.prompt.emit(self._tool_name() + "  " + why + "  ·  type another")
        self.setFocus()
        self._refresh()

    def _rect_tab(self, me):
        boxes = self._rect_boxes()
        me.hold()
        if self.tool == "circle_tangent":  # the second Tab: with two picked it goes in, with one it waits (the user)
            self.setFocus()
            if len(self.tc_picks) == 2 and me.typed is not None:
                self._tc_place()
                return
            self._tc_prompt()
            self._refresh()
            return
        if me in boxes and len(boxes) > 1:
            boxes[(boxes.index(me) + 1) % len(boxes)].activate()
        else:
            self.setFocus()
        self._refresh()

    def _rect_enter(self):
        """Enter: what was typed applies; the rectangle (or the 3-point way's first side) is fixed as it stands."""
        if self.rotating is not None:
            self.rot_click()
            return
        if self.tool == "circle_tangent":
            ready = bool(self._tc_solutions())
            for b in self.shape_boxes():
                b.apply()
            self.setFocus()
            if ready:
                self._arc_finish()
            elif len(self.tc_picks) == 2 and self.box_r.typed is not None and not self._tc_solutions():
                self._tc_place()
            else:
                self._tc_prompt()
                self._refresh()
            return
        for b in self.shape_boxes():
            b.apply()
        if self.tool == "arc_tangent" and self.rect_pts and self._tan is None:
            self._arc_finish()  # Enter: the arc as it stands, the radius typed
        elif self.rect_pts and self.mouse is not None:
            self._rect_click(self.mouse)

    def _rect_cancel_typing(self):
        for b in self.shape_boxes():
            b.reset()
        self.setFocus()
        self._refresh()

    def _rect_key(self, e):
        k = e.key()
        if k == QtCore.Qt.Key_Escape:
            if self.rect_pts:
                self._rect_reset()
            else:
                self.stop_tool()
            return True
        if k == QtCore.Qt.Key_Space and self.tool in ("ellipse_3pt", "ellipse_centre", "ellipse_rot") and not self.rect_pts:
            self.tool = {"ellipse_3pt": "ellipse_centre", "ellipse_centre": "ellipse_rot", "ellipse_rot": "ellipse_3pt"}[self.tool]
            self.prompt.emit(self._tool_name() + "  " + RECT_PROMPTS[self.tool][0] + "  ·  right-click stops")
            self._refresh()
            return True
        if k == QtCore.Qt.Key_Space and self.tool in SLOTS and (not self.rect_pts or (self.tool in SLOT_ARC and self._rect_stage() in (1, 3))):
            self.tool = SLOT_NEXT[self.tool]  # an arc slot's ends can change right to the width, where they are seen
            self.tool_changed.emit(SLOT_COMMANDS[self.tool])
            self.prompt.emit(self._tool_name() + "  " + RECT_PROMPTS[self.tool][self._rect_stage()] + "  ·  right-click " + ("gives it up" if self.rect_pts else "stops"))
            self._refresh()
            return True
        if k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and self.tool == "polygon" and self._rect_stage() == 1:
            self._poly_finish()
            return True
        if k == QtCore.Qt.Key_Space and self.tool == "text":
            self.text_frame = {"rect": "ellipse", "ellipse": "circle", "circle": "rect"}[self.text_frame]
            self.prompt.emit("Text  " + self.text_frame.capitalize() + " frame  ·  " + RECT_PROMPTS["text"][self._rect_stage()].split("  ·  ")[0])
            self._refresh()
            return True
        if k == QtCore.Qt.Key_Space and self.tool == "polygon":
            self.poly_in = not self.poly_in
            self.poly_set.emit(self.poly_sides, self.poly_in)
            self.prompt.emit(self._tool_name() + "  " + self._poly_prompt(self._rect_stage()) + "  ·  right-click " + ("gives it up" if self.rect_pts else "stops"))
            self._refresh()
            return True
        if k == QtCore.Qt.Key_Space and self.tool == "circle_tangent":
            self.box_r.hold()  # typing in the box: the size goes in first
            if self._tc_solutions():
                self.setFocus()
                self.tc_at += 1  # the space bar does every change (the user)
                self._refresh()
            return True
        if k == QtCore.Qt.Key_Tab and self.tool == "circle_tangent" and len(self.tc_picks) == 2 \
                and self.box_r.typed is not None:
            self._tc_place()
            return True
        if k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and self.tool == "circle_tangent" and self._tc_solutions():
            self._rect_enter()
            return True
        boxes = self._rect_boxes()
        if not boxes:
            return False
        if e.text() and (e.text()[0].isdigit() or e.text()[0] in ".-,"):
            boxes[0].activate(e.text())
            return True
        if k == QtCore.Qt.Key_Space and (self.tool in ("arc", "arc_center", *SLOT_ARC, "circle_3pt", "ellipse", "ellipse_rot", "arc_ellipse")
                                         and self._rect_stage() == 2 or self.tool == "arc_ellipse" and self._rect_stage() == 4):
            self.arc_flip = not self.arc_flip  # the space bar does every change (the user)
            self._refresh()
            return True
        if k == QtCore.Qt.Key_Space and self.tool == "arc_tangent" and self.tan_dir is not None and self._tan is None:
            # the space bar does every change (the user): the other way; on a corner, then the next line
            if self.tan_pick is not None and self.tan_held:
                options = [src for _t, src in self._curve_tangents(self.rect_pts[0])]
                self.tan_pick = options[(options.index(self.tan_pick) + 1) % len(options)]
                self.tan_src = self.tan_pick
                self.tan_dir, self.tan_held = None, False
            else:
                self.tan_dir = -self.tan_dir
                self.tan_held = True
            self._refresh()
            return True
        if k in (QtCore.Qt.Key_Tab, QtCore.Qt.Key_Backtab):
            boxes[0].activate()  # the first Tab: into the long side (the user)
            self._refresh()
            return True
        if k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self._rect_enter()
            return True
        return False

    def _rect_place_boxes(self):
        stage = self._rect_stage()
        show = []
        if stage and self.mouse is not None:
            corners = self._rect_corners()
            arc = self._arc_now() if self.tool in ARC_TOOLS else None
            rounded = self._rounded_now() if self.tool == "rect_rounded" else None
            if self.tool == "circle_tangent":
                if stage in (1, 2):
                    self.box_r.prefix = "Ø "
                    self.box_r.show_value(2 * arc[1] if arc is not None else 0.0)
                    at = self.to_screen(self.rect_pts[-1])
                    self.box_r.move(int(at.x() + 12), int(at.y() + 12))
                    show = [self.box_r]
            elif self.tool == "polygon":
                now = self._poly_now()
                if now is not None:
                    self.poly_live.emit(2 * now[2], math.degrees(now[1]))
            elif self._slot_widening():
                show = self._slot_box()
            elif self.tool == "arc_tangent" and self._tan is not None:
                pass  # the arc is being settled against a second curve: no size to type
            elif arc is not None:
                c, r, a0, sw = arc
                box = self.box_sw if self.tool in CENTER_ARCS else self.box_r
                self.box_r.prefix = "Ø " if self.tool in CIRCLE_TOOLS else ""
                box.show_value(math.degrees(sw) if self.tool in CENTER_ARCS else 2 * r if self.tool in CIRCLE_TOOLS else r)
                sm = self.to_screen(_on_circle(c, r, a0 + sw / 2))
                sc = self.to_screen(c)
                u = sm - sc
                n = _len(u)
                u = u / n if n > 1e-9 else P(1, 0)
                at = sm + u * (14 + box.width() / 2)
                box.move(int(at.x() - box.width() / 2), int(at.y() - box.height() / 2))
                show = [box]
            elif rounded is not None:
                x0, y0, x1, y1, r = rounded
                self.box_r.show_value(r)
                sc = self.to_screen(P(x1, y1))
                self.box_r.move(int(sc.x() + 10), int(sc.y() - self.box_r.height() - 6))
                show = [self.box_r]
            elif self.tool == "arc_tangent":
                show = [self.box_r]
            elif self.tool == "arc_ellipse" and stage >= 3:
                cut = self._cut_now()
                box = self.box_ra if stage == 3 else self.box_sw
                c, a, b = self.rect_pts[:3]
                t = self._cut_start() if cut is None else cut[3] + cut[4] / 2
                box.show_value(math.degrees(_ell_angle(a - c, b - c, t)) if stage == 3 else
                               math.degrees(self._cut_seen(cut)) if cut is not None else 0.0)
                at = self.to_screen(_ell_at(c, a - c, b - c, t))
                box.move(int(at.x() + 14), int(at.y() + 10))
                show = [box]
            elif self.tool in ("ellipse", "arc_ellipse") and stage == 2:
                now = self._ell_now()
                if now is not None:
                    c, a, b, frame = now
                    self.box_h.show_value(2 * _len(b - c))
                    sc, sb = self.to_screen(c), self.to_screen(b)
                    at = sc - (sb - sc) - P(self.box_h.width() / 2, self.box_h.height() / 2)  # across from the pointer
                    self.box_h.move(int(at.x()), int(at.y()))
                show = [self.box_h]
            elif self.tool in ("arc", "arc_center", *SLOT_ARC, "circle", "circle_3pt") and (stage == 2 or self.tool == "circle"):
                show = [self.box_sw if self.tool in CENTER_ARCS else self.box_r]
            elif self.tool in ("rect", "rect_center", "rect_rounded", "text", *FRAME_TOOLS) and corners:
                w, h = corners[1].x() - corners[0].x(), corners[3].y() - corners[0].y()
                self.box_w.show_value(abs(w))
                self.box_h.show_value(abs(h))
                top = max(corners, key=lambda p: p.y()).y()
                right = max(corners, key=lambda p: p.x()).x()
                cx = sum(p.x() for p in corners) / 4
                cy = sum(p.y() for p in corners) / 4
                st, sr = self.to_screen(P(cx, top)), self.to_screen(P(right, cy))
                self.box_w.move(int(st.x() - self.box_w.width() / 2), int(st.y() - self.box_w.height() - 8))
                self.box_h.move(int(sr.x() + 10), int(sr.y() - self.box_h.height() / 2))
                show = [self.box_w, self.box_h]
            elif stage == 1:
                a, e = self.rect_pts[0], self._rect_side_end()
                d = e - a
                length = _len(d) * (2 if self.tool in ("rect_center_3pt", "ellipse", "arc_ellipse", "slot_center", "polygon") else 1)  # arc: the chord; centre arc: radius
                self.box_w.show_value(length)
                ang = math.degrees(math.atan2(d.y(), d.x())) if _len(d) > 1e-9 else 0.0
                self.box_ra.show_value(ang % 360.0 if self.angle_360 else ang)
                sa, se = self.to_screen(a), self.to_screen(e)
                mid = (sa + se) * 0.5
                self.box_w.move(int(mid.x() - self.box_w.width() / 2), int(mid.y() - self.box_w.height() - 14))
                self.box_ra.move(int(sa.x() + 14), int(sa.y() + 10))
                show = [self.box_w, self.box_ra]
                now = self._ell_now() if self.tool == "ellipse_rot" else None
                if now is not None:
                    self.box_h.show_value(_len(now[3][2] - now[3][1]))
                    sb = self.to_screen(now[2])
                    self.box_h.move(int(sb.x() + 10), int(sb.y() - self.box_h.height() / 2))
                    show = [self.box_w, self.box_ra, self.box_h]
            elif corners:
                h = _len(corners[3] - corners[0])
                self.box_h.show_value(h)
                s3 = self.to_screen((corners[2] + corners[3]) * 0.5)
                self.box_h.move(int(s3.x() + 10), int(s3.y() - self.box_h.height() / 2))
                show = [self.box_h]
        for b in self.shape_boxes():
            b.setVisible(b in show)

    # ----- constraints -----
    def start_constraint(self, kind):
        """The tool first, then the clicks; what is chosen already is taken at once (the user)."""
        items = [("point", k) for k in sorted(self.sel_pts)] + [("line", i) for i in sorted(self.sel_lines)] + \
            [("arc", i) for i in sorted(self.sel_arcs)] + [("ell", i) for i in sorted(self.sel_ells)] + \
            sorted(self.sel_places, key=str)
        self._end_chain()
        self._rect_reset()
        self.tool, self.con_kind = "constraint", kind
        self.con_picks, self.con_ask = [], []
        self.hint = None
        self._clear_choice()
        self.setFocus()
        log.say("constraint tool", kind, "with", items)
        self._con_prompt()
        if items:
            self._con_given(items)
        self._refresh()

    def _con_prompt(self, first=""):
        self.prompt.emit((first + "  ·  " if first else "") + self.con_kind + "  Pick " +
                         CONSTRAINT_NEEDS[self.con_kind] + ", or a box  ·  right-click stops " + self.con_kind)

    def _con_back(self):
        """Esc or the right button: the picks go first, then the tool."""
        if self.con_picks or self.con_ask:
            self.con_picks, self.con_ask = [], []
            self._con_prompt()
            self._refresh()
        else:
            self.stop_tool()

    def _con_ways(self):
        """The ways of this constraint that fit what was picked so far."""
        n = len(self.con_picks)
        return [w for w in CONSTRAINTS[self.con_kind]
                if len(w) >= n and all(pick[0] in slot for pick, slot in zip(self.con_picks, w))]

    def _con_wanted(self):
        n = len(self.con_picks)
        if self.con_kind == "Symmetric":  # two like figures, then what they mirror in
            return ("point", "line", "arc", "ell", "spl") if n == 0 else (self.con_picks[0][0],) if n == 1 else ("point", "line")
        return {k for w in self._con_ways() if len(w) > n for k in w[n]}

    def _con_click(self, s):
        self.setFocus()
        if self.con_ask:  # the question stands: what is clicked stays
            what = self._pick(s, {k for k, _i in self.con_ask})
            if what is None and any(k in ("axis", "origin") for k, _i in self.con_ask):
                what = self._dim_place_at(s)
            if what in self.con_ask:
                rest = [x for x in self.con_ask if x != what]
                self.con_ask = []
                self._con_apply([what] + rest)
            return
        wanted = self._con_wanted()
        what = self._pick(s, wanted)
        if what is None and self.con_kind == "Symmetric" and len(self.con_picks) == 2:
            what = self._dim_place_at(s)  # the axes and their crossing count as mirrors
        if what is None and {"axis", "origin"} & set(wanted):  # an axis or the origin, where this constraint takes one
            place = self._dim_place_at(s)
            if place is not None and place[0] in wanted:
                what = place
        if what is None:
            at = self.to_model(s)
            self.boxing = dict(at=at, to=P(at), touch=False, add=False, lines=set(), arcs=set(), pts=set())
            return
        if what in self.con_picks:
            return
        self.con_picks.append(what)
        if self.con_kind == "Symmetric":
            if len(self.con_picks) == 3:
                picks, self.con_picks = self.con_picks, []
                self._con_apply(picks)
            else:
                self._con_prompt()
        elif any(len(w) == len(self.con_picks) for w in self._con_ways()):
            picks, self.con_picks = self.con_picks, []
            self._con_apply(picks)
        self._refresh()

    def _con_box_points(self, b):
        """With a constraint tool on the box takes points too, those its lines and arcs do not bring along."""
        x0, x1 = sorted((b["at"].x(), b["to"].x()))
        y0, y1 = sorted((b["at"].y(), b["to"].y()))
        own = {k for i in b["lines"] for k in self.lines[i]} | {k for i in b["arcs"] for k in self.arcs[i]}
        rims = self.rims()
        return {k for k, p in enumerate(self.pts) if x0 <= p.x() <= x1 and y0 <= p.y() <= y1 and k not in own and k not in rims}

    def _con_box_end(self, b):
        small = _len(b["to"] - b["at"]) * self.scale < 3
        if small:
            self._refresh()
            return
        kinds = {k for w in CONSTRAINTS[self.con_kind] for slot in w for k in slot}
        lines, arcs = b["lines"] if "line" in kinds else set(), b["arcs"] if "arc" in kinds else set()
        pts = self._con_box_points(b) if "point" in kinds and not (lines or arcs) or "line" not in kinds and "arc" not in kinds \
            or self.con_kind in ("Coincident", "Symmetric") else set()
        items = [("point", k) for k in sorted(pts)] + [("line", i) for i in sorted(lines)] + [("arc", i) for i in sorted(arcs)]
        if self._con_box_origin(b) and "origin" in kinds:  # the centre point inside the frame is taken like a point
            items.append(("origin", None))
        self._con_given(items)
        self._refresh()

    def _con_box_origin(self, b):
        x0, x1 = sorted((b["at"].x(), b["to"].x()))
        y0, y1 = sorted((b["at"].y(), b["to"].y()))
        return x0 <= 0 <= x1 and y0 <= 0 <= y1

    def _con_given(self, items):
        """Objects given together, in no order (a box, or chosen before the tool)."""
        for way in CONSTRAINTS[self.con_kind]:
            if len(way) != len(items):
                continue
            for order in itertools.permutations(items):
                if all(item[0] in slot for item, slot in zip(order, way)):
                    if len(items) == 2 and any(it[0] in ("axis", "origin") for it in order):
                        # an axis or the centre point never moves: it stays, nothing to ask
                        self._con_apply(sorted(order, key=lambda it: it[0] not in ("axis", "origin")))
                    elif len(items) == 2:  # no first and second here: the program asks (the user)
                        self.con_ask = list(order)
                        self.prompt.emit(self.con_kind + "  Click what stays  ·  Esc gives it up")
                    else:
                        self._con_apply(list(order))
                    return
        self.prompt.emit("%s needs %s" % (self.con_kind, CONSTRAINT_NEEDS[self.con_kind]))
        log.say("constraint refused: given", items)

    def _con_points(self, item):
        kind, i = item
        if kind in ("axis", "origin"):
            return ()
        if kind == "spl":
            return self.spls[i][1]
        return (i,) if kind == "point" else self.lines[i] if kind == "line" else self.ells[i] if kind == "ell" else self.arcs[i]

    def _sym_image(self, q, mirror):
        """Where the place q lands in the mirror."""
        if mirror[0] == "axis":
            return P(q.x(), -q.y()) if mirror[1] == "x" else P(-q.x(), q.y())
        if mirror[0] == "origin":
            return P(-q.x(), -q.y())
        if mirror[0] == "point":
            return self.pts[mirror[1]] * 2 - q
        a, b = [self.pts[k] for k in self.lines[mirror[1]]]
        u = (b - a) / (_len(b - a) or 1.0)
        foot = a + u * QtCore.QPointF.dotProduct(q - a, u)
        return foot * 2 - q

    def _sym_pairs(self, first, second, mirror):
        """Which point of the second answers which of the first; or a refusal, as text."""
        kind = first[0]
        if kind == "point":
            return [(first[1], second[1])]
        if kind == "arc":
            return [(self.arcs[first[1]][0], self.arcs[second[1]][0])]  # the centres; the radii go equal
        one, two = list(self._con_points(first)), list(self._con_points(second))
        if len(one) != len(two):
            return "Symmetric: the two splines need as many points"
        if kind == "ell":  # the centres answer each other, then the axes' ends
            one, two = one[1:], two[1:]
        cost = lambda pair: sum(_len(self._sym_image(self.pts[a], mirror) - self.pts[b]) for a, b in zip(one, pair))
        best = min(itertools.permutations(two), key=cost) if len(two) <= 2 else min((two, two[::-1]), key=cost)
        pairs = list(zip(one, best))
        if kind == "ell":
            pairs.insert(0, (self.ells[first[1]][0], self.ells[second[1]][0]))
            ends = self._ell_cut(first[1]), self._ell_cut(second[1])
            if ends[0] and ends[1]:  # an elliptical arc's ends answer each other too
                pairs += list(zip(ends[0], min((ends[1], ends[1][::-1]), key=lambda pair: cost(pair))))
        return pairs

    def _sym_ties(self, items, tie):
        first, second, mirror = items
        pairs = self._sym_pairs(first, second, mirror)
        if isinstance(pairs, str):
            return pairs
        out = []
        for a, b in pairs:
            if a == b:
                continue
            if mirror[0] == "axis":
                out.append(tie("con_sym_ax", a, b, ref=mirror[1]))
            elif mirror[0] == "origin":
                out.append(tie("con_sym_o", a, b))
            elif mirror[0] == "point":
                out.append(tie("con_sym_pt", a, b, mirror[1]))
            else:
                out.append(tie("con_sym", a, b, *self.lines[mirror[1]]))
        if first[0] == "arc":
            out.append(tie("con_eq_r", *self.arcs[first[1]][:2], *self.arcs[second[1]][:2]))
        return out or "Symmetric: nothing to tie"

    def _con_ties(self, items):
        """The constraint as ties on points: they renumber, copy and go with the points. Or a refusal, as text."""
        name = self.con_kind
        tie = lambda kind, *pts, ref=None: dict(kind=kind, pts=list(pts), ref=ref, mark=name)
        a, b = items[0], items[-1]
        if name != "Symmetric" and any(it[0] in ("axis", "origin") for it in items):  # (Symmetric has its own)
            return self._con_axis_ties(items, tie)
        if name == "Coincident" and "ell" in (a[0], b[0]):
            point, ell = (a, b) if b[0] == "ell" else (b, a)
            return [tie("con_on_ell", point[1], *self.ells[ell[1]])]
        if name == "Tangent" and "ell" in (a[0], b[0]):
            ell, other = (a, b) if a[0] == "ell" else (b, a)
            if other[0] == "line":
                return [tie("con_ell_line", *self.ells[ell[1]], *self.lines[other[1]])]
            return [tie("con_ell_arc", *self.arcs[other[1]][:2], *self.ells[ell[1]])]
        if name == "Fix":
            return [tie("fix", a[1])]
        if name in ("Horizontal", "Vertical"):
            pts = self.lines[a[1]] if a[0] == "line" else (a[1], b[1])
            if pts[0] == pts[1]:
                return "One point: nothing to line up"
            return [tie("same_y" if name == "Horizontal" else "same_x", *pts)]
        if name == "Coincident":
            if a[0] != "point":
                a, b = b, a
            if b[0] == "point":
                if a[1] == b[1]:
                    return "They are one point already"
                if any(a[1] in ln and b[1] in ln for ln in self.lines):
                    return "Both are ends of one line"
                return [tie("con_same", a[1], b[1])]
            if b[0] == "line":
                return [tie("con_on_line", a[1], *self.lines[b[1]])]
            c, s_, _e = self.arcs[b[1]]
            return [tie("con_on_arc", c, s_, a[1])]
        if name == "Concentric":
            return [tie("con_centre", self.arcs[a[1]][0], self.arcs[b[1]][0])]
        if name == "Symmetric":
            return self._sym_ties(items, tie)
        if name == "Equal" and a[0] == "arc":
            return [tie("con_eq_r", *self.arcs[a[1]][:2], *self.arcs[b[1]][:2])]
        if name in ("Parallel", "Perpendicular", "Collinear", "Equal"):
            return [tie({"Parallel": "con_par", "Perpendicular": "con_perp", "Collinear": "con_col",
                         "Equal": "con_eq"}[name], *self.lines[a[1]], *self.lines[b[1]])]
        # Tangent: they share a point, or the arc is to come to touch
        if a[0] == "arc" and b[0] == "arc":
            ca, sa, ea = self.arcs[a[1]]
            cb, sb, eb = self.arcs[b[1]]
            t = ({sa, ea} & {sb, eb})
            if t:
                return [tie("con_circles", t.pop(), ca, cb)]
            d = _len(self.pts[ca] - self.pts[cb])
            r1, r2 = _len(self.pts[sa] - self.pts[ca]), _len(self.pts[sb] - self.pts[cb])
            return [tie("con_apart", ca, sa, cb, sb, ref=d < max(r1, r2))]
        line, arc = (a, b) if a[0] == "line" else (b, a)
        la, lb = self.lines[line[1]]
        c, s_, e_ = self.arcs[arc[1]]
        t = {la, lb} & {s_, e_}
        if t:
            return [tie("con_touch", t.pop(), c, la, lb)]
        return [tie("con_dist", c, s_, la, lb)]

    def _con_axis_ties(self, items, tie):
        """A constraint between a figure of the sketch and one of its axes or the origin, as ties (x0 / y0: a point on
        the Y / X axis; the others as between points). Or a refusal, as text."""
        name = self.con_kind
        axis = next(it for it in items if it[0] in ("axis", "origin"))
        kind, i = next(it for it in items if it is not axis)
        if axis[0] == "origin":
            if kind == "point" and name == "Coincident":
                return [tie("x0", i), tie("y0", i)]
            if kind == "point" and name == "Horizontal":
                return [tie("y0", i)]  # level with the origin: on the X axis
            if kind == "point" and name == "Vertical":
                return [tie("x0", i)]  # upright to the origin: on the Y axis
            if kind == "arc" and name in ("Coincident", "Concentric"):
                c = self.arcs[i][0]
                return [tie("x0", c), tie("y0", c)]  # its centre in the origin
        else:
            ax = axis[1]
            on = "y0" if ax == "x" else "x0"  # on the X axis: y is 0
            if kind == "point" and name == "Coincident":
                return [tie(on, i)]
            if kind == "line":
                a_, b_ = self.lines[i]
                if name in ("Coincident", "Collinear"):
                    return [tie(on, a_), tie(on, b_)]  # the line lies on the axis
                if name == "Parallel":
                    return [tie("same_y" if ax == "x" else "same_x", a_, b_)]
                if name == "Perpendicular":
                    return [tie("same_x" if ax == "x" else "same_y", a_, b_)]
            if kind == "arc" and name == "Tangent":
                c, s_, _e = self.arcs[i]
                return [tie("con_axis", c, s_, ref=ax)]
        return "%s: not with that" % name

    def _con_apply(self, items):
        """The first stays, the rest comes to it; if it cannot, the first gives; neither: refused, nothing changes."""
        name = self.con_kind
        new = self._con_ties(items)
        if isinstance(new, str):
            self._con_prompt(new)
            return
        fixed = self._fixed()
        if name == "Fix":
            if new[0]["pts"][0] in fixed:
                self._con_prompt("Fixed already")
                return
            fixed = set()
        coords = [(p.x(), p.y()) for p in self.pts]
        rels = self._rels()
        extra = []
        for t in new:
            if t["kind"] == "same_x":
                got = sk.same_x(*t["pts"])
            elif t["kind"] == "same_y":
                got = sk.same_y(*t["pts"])
            elif t["kind"] == "x0":
                got = sk.x_is(t["pts"][0], 0.0)
            elif t["kind"] == "y0":
                got = sk.y_is(t["pts"][0], 0.0)
            elif t["kind"] in CONSTRAINT_TIES:
                got = CONSTRAINT_TIES[t["kind"]](*t["pts"], *(() if t["ref"] is None else (t["ref"],)))
            else:
                got = []
            extra += got if isinstance(got, list) else [got]
        if extra and sk.adds_nothing(coords, rels, extra, fixed):
            self._con_prompt("%s holds already" % name)
            log.say("constraint refused: holds already", name, items)
            return
        got = coords
        rels = rels + self._hold_radii([i for kind_, i in items if kind_ == "arc"] if name in ("Equal", "Tangent", "Symmetric") else ()) \
            + self._hold_ells()
        if extra:
            first = set(self._con_points(items[0]))
            rest = {k for it in items[1:] for k in self._con_points(it)}
            if len(items) == 1:  # one line: its start stays, its end comes
                first, rest = {self._con_points(items[0])[0]}, {self._con_points(items[0])[-1]}
            if name != "Symmetric" and any(it[0] in ("axis", "origin") for it in items):  # an axis does not move: the figure comes to it
                first, rest = set(), {k for it in items for k in self._con_points(it)}
            if name == "Symmetric":  # the first point stays, the second comes; the line is the axis
                first = set(self._con_points(items[2])) | set(self._con_points(items[0]))
                rest = set(self._con_points(items[1])) - first
            got = sk.solve(coords, rels + extra, {}, fixed | first, seeds=[k for k in rest if k not in first])
            if got is None:  # it cannot come: then what stood gives
                got = sk.solve(coords, rels + extra, {}, fixed, seeds=list(first | rest))
            bad = got is None or any(math.hypot(got[a][0] - got[b][0], got[a][1] - got[b][1]) < 1e-6 for a, b in self.lines) \
                or any(math.hypot(got[c][0] - got[s_][0], got[c][1] - got[s_][1]) < 1e-6 for c, s_, _e in self.arcs)
            if bad:
                self._con_prompt("%s: conflict with the other constraints, nothing changed" % name)
                log.say("constraint refused: conflict", name, items)
                return
        st = self._state()
        for p, (x, y) in zip(self.pts, got):
            p.setX(x)
            p.setY(y)
        for t in new:
            if t["kind"] == "con_same":  # one point now
                keep, gone = t["pts"]
                swap = lambda k, keep=keep, gone=gone: keep if k == gone else k
                self.lines = [tuple(map(swap, ln)) for ln in self.lines]
                self.arcs = [tuple(map(swap, arc)) for arc in self.arcs]
                self.ells = [tuple(map(swap, e)) for e in self.ells]
                self.spls = [(s_[0], tuple(map(swap, s_[1])), s_[2]) for s_ in self.spls]
                for o in self.ties:
                    o["pts"] = [swap(k) for k in o["pts"]]
                self._add_tie("coin", [keep], mark=name)
            else:
                self._add_tie(t["kind"], t["pts"], t["ref"], mark=name)
        self._drop_lone_points()
        self.steps.append(("state", st, len(self.lines), len(self.arcs)))
        log.say("constraint", name, items)
        self._con_prompt("%s set" % name)
        self.changed.emit()
        self._refresh()

    # ----- what holds the sketch -----
    def _add_tie(self, kind, pts, ref=None, mark=None):
        self.ties.append(dict(kind=kind, pts=list(pts), ref=ref, n=len(self.lines) + len(self.arcs), mark=mark))

    def _key(self, k):
        return self.cur if k == "start" else k

    def _fixed(self):
        """The points that never move: on the origin, unless they merely lie there; and Fix."""
        free = {k for t in self.ties if t["kind"] == "free" for k in t["pts"]}
        return {k for k, p in enumerate(self.pts) if _len(p) < 1e-9 and k not in free} | \
            {k for t in self.ties if t["kind"] == "fix" for k in t["pts"]} | self._proj_points()

    # ----- projected from the part (ProstoCAD 0.18 New Sketch: FreeCAD's external geometry) -----
    # A projected shape's id (shape_ids) is "ext:N", N its external geometry number (-3, -4 ...); a projected vertex
    # is a Point whose sk_point tie has that ref. Projections never move: the part holds them.
    @staticmethod
    def _is_ext(ref):
        return isinstance(ref, str) and ref.startswith("ext:")

    def is_projected(self, item):
        """Is this shape (kind, i) projected from the part?"""
        kind, i = item
        ids = self._fit_ids().get(kind, [])
        return 0 <= i < len(ids) and self._is_ext(ids[i])

    def _paint_host_lit(self, g):
        """What the program around the sketch lights under the pointer (ProstoCAD 0.18 New Sketch: Project Geometry's
        edge or face of the part, as it lands on the sketch): host_lit = dict(lines=[[P, …]], points=[P]), sketch
        coordinates, in the colour of a line under the pointer, 2.6 px."""
        lit = getattr(self, "host_lit", None)
        if not lit:
            return
        ink = QtGui.QColor(self.p["snap"])
        g.setPen(QtGui.QPen(ink, 2.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
        g.setBrush(QtCore.Qt.NoBrush)
        for poly in lit.get("lines", []):
            g.drawPolyline(QtGui.QPolygonF([self.to_screen(q) for q in poly]))
        g.setPen(QtCore.Qt.NoPen)
        g.setBrush(ink)
        for q in lit.get("points", []):
            g.drawEllipse(self.to_screen(q), 4, 4)

    def _proj_points(self):
        ids = getattr(self, "shape_ids", None) or {}
        out = {k for t in self.ties if t["kind"] == "sk_point" and self._is_ext(t.get("ref")) for k in t["pts"]}
        for kind, shapes in (("line", self.lines), ("arc", self.arcs), ("ell", self.ells), ("spl", self.spls)):
            for i, ref in enumerate(ids.get(kind, [])):
                if self._is_ext(ref) and i < len(shapes):
                    out |= set(self._shape_points((kind, i)))
        return out

    def _tie_key(self, kind, p, k):
        """p level with / upright to the point k."""
        k = self._key(k)
        if k == "origin":
            self._add_tie("y0" if kind == "same_y" else "x0", [p])
        elif k is not None and k != p:
            self._add_tie(kind, [p, k])

    def _tie(self, p, hint):
        """The point p landed on this magnet: what it means stays as a relation."""
        kind, ref = hint["kind"], hint["ref"]
        if kind == "mid":
            a, b = self.lines[ref]
            whole = _len((self.pts[a] + self.pts[b]) * 0.5 - hint["at"]) < 1e-6
            self._add_tie("mid" if whole else "on_line", [p], ref)  # the middle of a piece: on the line
        elif kind in ("on", "perp"):  # the continuation is a guide only: landing on it ties nothing (the user)
            if ref == "x":
                self._add_tie("y0", [p])
            elif ref == "y":
                self._add_tie("x0", [p])
            else:
                self._add_tie("on_line", [p], ref)
        elif kind in ("arc_mid", "on_arc", "tan", "quad"):
            self._add_tie("on_arc", [p], ref)
        elif kind == "x":
            if ref[0] == "arc":
                self._add_tie("on_arc", [p], ref[1])
                self._add_tie("on_line", [p], ref[2])
            elif ref[0] == "arcs":
                self._add_tie("on_arc", [p], ref[1])
                self._add_tie("on_arc", [p], ref[2])
            else:
                self._add_tie("on_line", [p], ref[0])
                self._add_tie("on_line", [p], ref[1])
        elif kind == "cross":
            self._tie_key("same_x", p, ref[0])
            self._tie_key("same_y", p, ref[1])
        elif kind == "level":
            self._tie_key("same_y", p, ref)
        elif kind == "above":
            self._tie_key("same_x", p, ref)

    def _tie_shape(self, points):
        """A shape's clicks: each magnet goes to the shape's point standing on it."""
        for hint in self.rect_hints:
            for k in points:
                if _len(self.pts[k] - hint["at"]) < 1e-6:
                    self._tie(k, hint)
                    break

    def _diagonal(self, a, b):
        """A rectangle's dashed diagonal with a point in its middle: something to pull and to tie to (the user)."""
        self.lines.append((a, b))
        self.constr = (self.constr + [False] * len(self.lines))[:len(self.lines) - 1] + [True]
        self.pts.append((self.pts[a] + self.pts[b]) * 0.5)
        self._add_tie("mid", [len(self.pts) - 1], len(self.lines) - 1)

    def _tie_centre(self, idx):
        """A rectangle from its centre: a centre that landed on something stays there."""
        if self.tool not in ("rect_center", "rect_center_3pt") or not self.rect_hints:
            return
        hint = self.rect_hints[0]
        if _len((self.pts[idx[0]] + self.pts[idx[2]]) * 0.5 - hint["at"]) > 1e-6:
            return
        if hint["kind"] == "point":
            if hint["ref"] == "origin":
                self._add_tie("centre", [idx[0], idx[2]])
            else:
                self._add_tie("centre_pt", [idx[0], idx[2], hint["ref"]])
        elif hint["kind"] == "mid":
            a, b = self.lines[hint["ref"]]
            if _len((self.pts[a] + self.pts[b]) * 0.5 - hint["at"]) < 1e-6:
                self._add_tie("centre_mid", [idx[0], idx[2], a, b])

    def _rels(self):
        """Every relation of the sketch as equations."""
        out = []
        sk_ties = CONSTRAINT_TIES
        for c, a, b in self.arcs:
            if a != b:
                out.append(sk.same_distance(c, a, b))
        for kind, line, _end, ref in self.relations:
            if line is None or line >= len(self.lines):
                continue
            a, b = self.lines[line]
            if kind == "h":
                out.append(sk.level(a, b))
            elif kind == "v":
                out.append(sk.upright(a, b))
            elif kind == "perp":
                other = line - 1 if ref is None else ref
                out.append(sk.perpendicular(a, b, *self.lines[other]))
            elif kind == "par":
                out.append(sk.parallel(a, b, *self.lines[ref]))
            elif kind == "tan":
                out.append(sk.line_touches(b, self.arcs[ref][0], a, b))
            elif kind == "tan_on":
                out.append(sk.line_touches(a, self.arcs[ref][0], a, b))
        out += self._corner_holds()
        for t in self.ties:
            kind, pts, ref = t["kind"], t["pts"], t["ref"]
            if t.get("driven"):  # for information: it follows the sketch, it holds nothing
                continue
            if kind == "dim_angx":
                out.append(sk.angle_axis(*pts, t["base"], t["first"], ref))
                continue
            if kind == "on_line":
                out.append(sk.on_line(pts[0], *self.lines[ref]))
            elif kind == "mid":
                out += sk.middle(pts[0], *self.lines[ref])
            elif kind == "on_arc":
                c, a, _b = self.arcs[ref]
                out.append(sk.same_distance(c, a, pts[0]))
            elif kind == "x0":
                out.append(sk.x_is(pts[0], 0.0))
            elif kind == "y0":
                out.append(sk.y_is(pts[0], 0.0))
            elif kind == "same_x":
                out.append(sk.same_x(*pts))
            elif kind == "same_y":
                out.append(sk.same_y(*pts))
            elif kind == "centre":
                out += sk.centre_is(pts[0], pts[1], 0.0, 0.0)
            elif kind == "centre_pt":
                out += sk.centre_at(*pts)
            elif kind == "centre_mid":
                out += sk.centre_on_middle(*pts)
            elif kind == "same_r":
                c1, a1, _ = self.arcs[ref[0]]
                c2, a2, _ = self.arcs[ref[1]]
                out.append(sk.same_radius(c1, a1, c2, a2))
            elif kind in sk_ties:
                got = sk_ties[kind](*pts, *(() if ref is None else (ref,)))
                out += got if isinstance(got, list) else [got]
            elif kind == "touch":
                arc, (what, i) = ref
                c = self.arcs[arc][0]
                if what == "line":
                    out.append(sk.line_touches(pts[0], c, *self.lines[i]))
                else:
                    out.append(sk.circles_touch(pts[0], c, self.arcs[i][0]))
        return out

    def _on_any_line(self, p):
        return any(_on_segment(p, self.pts[a], self.pts[b]) for a, b in self.lines)

    def _sweep_marks(self, m, tol, out):
        """Center Arc's end: every 15° from its start; with the centre on a line, also along that line and square to
        it. The arc goes by the pointer's angle: so does the magnet."""
        c, a = self.rect_pts
        r = _len(a - c)
        d = m - c
        if r < 1e-9 or _len(d) < 1e-9:
            return
        at = c + d / _len(d) * r
        start = (a - c) / r
        marks = []  # every 15° from the arc's start, counted the way it is going (the user)
        for k in range(15, 360, 15):
            ang = math.radians(k)
            marks.append(("Half circle" if k == 180 else "%d°" % (k if (self.arc_sweep >= 0) != self.arc_flip else 360 - k),
                          P(start.x() * math.cos(ang) - start.y() * math.sin(ang),
                            start.x() * math.sin(ang) + start.y() * math.cos(ang))))
        for t, _src in self._curve_tangents(c):
            for way in (t, t * -1):
                marks.append(("Extension", way))
            for way in (P(-t.y(), t.x()), P(t.y(), -t.x())):
                marks.append(("Perpendicular", way))
        for label, u in reversed(marks):  # on one place the line's name goes first
            p = c + u * r
            gone = _len(p - a) < 1e-6  # the start itself is no end
            dist = _len(p - at)
            if not gone and dist <= tol:
                guide = (a, p) if label == "Half circle" else (c, p)  # a half circle shows its diameter
                out.append((3, dist, dict(kind="sweep", label=label, at=P(p), ref=None, guides=[guide])))

    def _hint_moves(self, kind, ref, moving):
        """The hint hangs on something being pulled."""
        def line(i):
            return self.lines[i][0] in moving or self.lines[i][1] in moving

        def arc(i):
            return any(k in moving for k in self.arcs[i])

        if kind in ("point", "level", "above"):
            return ref in moving
        if kind == "cross":
            return ref[0] in moving or ref[1] in moving
        if kind in ("mid", "ext") or (kind == "on" and isinstance(ref, int)):
            return line(ref)
        if kind in ("arc_mid", "on_arc", "quad"):
            return arc(ref)
        if kind == "x":
            if ref[0] == "arc":
                return arc(ref[1]) or line(ref[2])
            if ref[0] == "arcs":
                return arc(ref[1]) or arc(ref[2])
            return line(ref[0]) or line(ref[1])
        return False

    def _on_the_way(self, pts, rels, fixed):
        """All that moves with a pull, and the points next to a pulled one: landing there would make a line of nothing."""
        out = set(pts)
        for r in sk._reached(rels, [k for k in pts if k not in fixed], fixed):
            out.update(r.points)
        out -= fixed
        for a, b in self.lines:
            if a in pts:
                out.add(b)
            if b in pts:
                out.add(a)
        for arc in self.arcs:
            if any(k in pts for k in arc):
                out.update(arc)
        return out

    # ----- moving what is drawn -----
    def _pick(self, s, want=("point", "line", "arc", "ell")):
        """What stands at the screen point s: a point first, then a line or an arc; want: only these kinds."""
        best = None
        rims = self.rims()
        ghost = self._ghost_points()
        for k, p in enumerate(self.pts):
            d = _len(self.to_screen(p) - s)
            if k not in rims and k not in ghost and "point" in want and d <= PICK_POINT_PX and (best is None or d < best[0]):
                best = (d, ("point", k))
        if best:
            return best[1]
        m = self.to_model(s)
        for i, (ia, ib) in enumerate(self.lines):
            if "line" not in want:
                break
            a, b = self.pts[ia], self.pts[ib]
            u = b - a
            n2 = QtCore.QPointF.dotProduct(u, u)
            if n2 < 1e-12 or self._ghost(("line", i)):
                continue
            t = max(0.0, min(1.0, QtCore.QPointF.dotProduct(m - a, u) / n2))
            d = _len(a + u * t - m) * self.scale
            if d <= PICK_LINE_PX and (best is None or d < best[0]):
                best = (d, ("line", i))
        for i in range(len(self.arcs)):
            if "arc" not in want:
                break
            c, r, a0, sw = self.arc_shape(i)
            d = abs(_len(m - c) - r) * self.scale
            if not self._ghost(("arc", i)) and d <= PICK_LINE_PX and _in_sweep(_ang(m - c), a0, sw) and (best is None or d < best[0]):
                best = (d, ("arc", i))
        if "spl" in want:
            for i in range(len(self.spls)):
                if self._ghost(("spl", i)):
                    continue
                d = min(_dist_seg(s, self.to_screen(a), self.to_screen(b)) for a, b in itertools.pairwise(self.spl_poly(i)))
                if d <= PICK_LINE_PX and (best is None or d < best[0]):
                    best = (d, ("spl", i))
        if "ell" in want:
            for i in range(len(self.ells)):
                if self._ghost(("ell", i)):
                    continue
                d = min(_len(self.to_screen(q) - s) for q in self._ell_path(i))
                if d <= PICK_LINE_PX and (best is None or d < best[0]):
                    best = (d, ("ell", i))
        return best[1] if best else None

    def _stack(self, k):
        """The points standing in one place with k, lowest first."""
        rims = self.rims()
        return [q for q, p in enumerate(self.pts) if _len(p - self.pts[k]) < 1e-6 and q not in rims]

    def _alone(self, i):
        """A line nothing else is joined to and nothing holds: it moves whole."""
        a, b = self.lines[i]
        joined = sum(1 for ln in self.lines for k in ln if k in (a, b)) > 2 \
            or any(k in (a, b) for arc in self.arcs for k in arc)
        held = any(r[1] == i and r[0] not in ("h", "v") for r in self.relations) \
            or any(a in t["pts"] or b in t["pts"] for t in self.ties)  # level or upright goes along with it
        return not joined and not held

    @staticmethod
    def _adding(e):
        return bool(e.modifiers() & (QtCore.Qt.ShiftModifier | QtCore.Qt.ControlModifier))

    def _chosen_points(self):
        return {k for i in self.sel_lines for k in self.lines[i]} | {k for i in self.sel_arcs for k in self.arcs[i]} \
            | {k for i in self.sel_ells for k in self._ell_keys(i)} | {k for i in self.sel_spls for k in self.spls[i][1]} \
            | self._loose_chosen()

    def _drag_start(self, s, adding=False):
        self.setFocus()
        what = self._pick(s, ("point", "line", "arc", "ell", "spl"))
        if what is None:
            k = self._dim_hit(s)
            if k is not None:
                self.drag = dict(kind="dim", index=k, pts=(), state=self._state(), moved=False, pick=("dim", id(self.ties[k])), adding=adding)
                return
            place = self._dim_place_at(s)  # the centre point or an axis: chosen by a click, for a constraint
            if place is not None:
                if adding:
                    self.sel_places ^= {place}
                else:
                    held = place in self.sel_places and len(self.sel_places) == 1 and not (
                        self.sel_lines or self.sel_arcs or self.sel_ells or self.sel_spls or self.sel_pts or self.sel_dims)
                    self._clear_choice()
                    if not held:
                        self.sel_places = {place}
                self._refresh()
                return
        if what is not None and what[0] == "point" and self.tab_point in self._stack(what[1]):
            what = ("point", self.tab_point)  # the one Tab lit
        if what is None:
            at = self.to_model(s)
            self.boxing = dict(at=at, to=P(at), touch=False, add=adding, lines=set(), arcs=set())
            return
        kind, i = what
        chosen = self._chosen_points()
        taken = (kind == "point" and i in chosen) or (kind == "line" and i in self.sel_lines) \
            or (kind == "arc" and i in self.sel_arcs) or (kind == "ell" and i in self.sel_ells) \
            or (kind == "spl" and i in self.sel_spls)
        pick = what
        alone = len(self.sel_lines) + len(self.sel_arcs) + len(self.sel_ells) + len(self.sel_spls) + len(self.sel_pts) == 1
        in_slot = kind in ("line", "arc") and any(t["kind"] == "slot_end" and set(t["pts"]) & set(self._shape_points((kind, i))) for t in self.ties)
        in_walls = kind == "line" and bool(self._corner_walls(i))
        if taken and not adding and not (alone and (in_slot or in_walls)):  # a slot's part keeps its rules, chosen or not (the user: it came apart)
            kind = "group"
        pts = {"point": (i,), "line": self.lines[i] if kind == "line" else (), "arc": (), "ell": (), "spl": (), "group": ()}[kind]
        if kind == "spl":
            pts = self.spls[i][1]
        if kind == "arc":
            pts = self.arcs[i][1:3]
        if kind == "ell":
            pts = self._ell_keys(i)[1:]
        if kind == "point":  # the centre carries its circles along, and its ellipses with their frames
            pts = (i, *(arc[1] for arc in self.arcs if arc[0] == i and arc[1] == arc[2]),
                   *(k for j, e in enumerate(self.ells) if e[0] == i for k in self._ell_keys(j)[1:]),
                   *(k for t in self.ties if t["kind"] == "ell_frame" and t["pts"][0] == i for k in t["pts"][3:]))
        if kind == "group":
            pts = sorted(chosen)
        self.corner_loose = self._corner_pulled(kind, i)
        if kind == "point":
            pts = (*pts, *self._slot_carried(i), *self._poly_carried(i))
        at = self.to_model(s)
        if kind == "point":
            at = P(self.pts[i])
        elif kind == "line":  # held by the place on the line, so that the line itself lands on a hint
            a, b = [self.pts[k] for k in self.lines[i]]
            u = b - a
            n2 = QtCore.QPointF.dotProduct(u, u)
            if n2 > 1e-12:
                at = a + u * max(0.0, min(1.0, QtCore.QPointF.dotProduct(at - a, u) / n2))
        self._spans(), self._crossings()  # the hints of this pull are worked out now, once
        before, cut = self._state(), self._mir_pair_cut(pts)  # a mirror copy pulled: it and its original alone
        rels = self._rels()
        if kind != "arc":  # a rounded rectangle keeps its radius unless the corner itself is pulled
            for t in self.ties:
                if t["kind"] == "same_r":
                    for arc in t["ref"]:
                        c, a, _b = self.arcs[arc]
                        rels.append(sk.radius_is(c, a, _len(self.pts[a] - self.pts[c])))
        rels += self._hold_radii((i,) if kind == "arc" else ()) + self._hold_ells(pts, i if kind == "ell" else None) + self._hold_slots(pts, kind == "arc")
        fixed = self._fixed()
        long = self._slot_long(i) if kind == "arc" else None
        self.drag = dict(kind=kind, index=i, pts=tuple(pts), was=[P(p) for p in self.pts], state=before, mir_cut=cut,
                         start={k: P(self.pts[k]) for k in pts}, at=at, moved=False, rels=rels,
                         fixed=fixed, moving=self._on_the_way(set(pts), rels, fixed),
                         whole=kind == "line" and self._alone(i), pick=pick, adding=adding,
                         stay=self._drag_stay(kind, pts, long), long=long, walls=self._corner_walls(i) if kind == "line" else [],
                         poly=self._poly_of(pts) if kind in ("line", "arc", "point") and not (kind == "point" and self._poly_carried(i)) else None)
        self.drag["fixed"] = set(self.drag["fixed"]) | self._mir_bound(pts, rels, self.drag["stay"])
        log.say("pull", kind, i, "at", log.pt(self.drag["at"]))

    def _drag_stay(self, kind, pts, long):
        """What stays where it is while this is pulled."""
        if kind in ("line", "arc", "point") and not long:
            stay = self._poly_stay(pts) if kind != "point" or not self._poly_carried(pts[0]) else {}
            if kind != "point":
                stay = {**stay, **self._slot_stay(pts, kind == "line")}
            return {**stay, **self._mir_stay(pts)}
        return self._mir_stay(pts) if kind == "group" else {}

    def _drag_targets(self, m):
        d = self.drag
        step = m - d["at"]
        if d["poly"]:
            return self._poly_targets(d, m)
        if d["kind"] == "point" and (i := self._ell_of_end(d["index"])) is not None:
            c, u, v = self.ell_shape(i)  # an elliptical arc's end goes along the ellipse: the ellipse stays as it is
            return {d["index"]: _ell_at(c, u, v, self._ell_nearest(i, m))}
        if d["kind"] == "point" and (turned := self._slot_turn(d["index"], m, d["was"])) is not None:
            return turned
        if d["kind"] == "line" and self._slot_flat_end(tuple(d["pts"])):
            return {k: d["was"][k] + step for k in self._slot_group(d["pts"])}  # a flat end's line moves the slot (the user)
        if d["kind"] == "point":
            for i, (ic, ia, ib) in enumerate(self.ells):  # an axis's end pulled: the arc's ends keep their place on it
                cut = self._ell_cut(i)
                if cut is None or d["index"] not in (ia, ib):
                    continue
                was = d["was"]
                c, u, v = was[ic], was[ia] - was[ic], was[ib] - was[ic]
                ts = [_ell_t(c, u, v, was[k]) for k in cut]
                new = m - c
                if _len(new) < 1e-6:
                    break
                turn = _ang(new) - _ang(u if d["index"] == ia else v)
                co, si = math.cos(turn), math.sin(turn)
                other = v if d["index"] == ia else u
                other = P(other.x() * co - other.y() * si, other.x() * si + other.y() * co)
                u, v = (new, other) if d["index"] == ia else (other, new)
                return {ic: c, ia: c + u, ib: c + v, cut[0]: _ell_at(c, u, v, ts[0]), cut[1]: _ell_at(c, u, v, ts[1])}
        if d["kind"] == "arc":
            c = self.pts[self.arcs[d["index"]][0]]
            r = _len(m - c)
            widest = self.slot_widest(self.arcs[d["index"]][0])
            r = r if widest is None else min(r, widest)  # an arc slot's end: the slot stays a slot
            return {k: (lambda u: c + u / _len(u) * r)(q - c) for k, q in d["start"].items()}
        if d["kind"] == "ell":
            c = self.pts[self.ells[d["index"]][0]]
            k = _len(m - c) / (_len(d["at"] - c) or 1.0)
            return {i: c + (q - c) * k for i, q in d["start"].items()}
        if d["kind"] == "line" and not d["whole"]:  # a side goes across: along it, it would drag the shape away
            a, b = [d["start"][k] for k in d["pts"]]
            u = b - a
            n = _len(u)
            if n > 1e-9:
                u /= n
                step = step - u * QtCore.QPointF.dotProduct(step, u)
        if d["kind"] == "line" and d["walls"]:
            return self._wall_targets(d, step)
        if d["kind"] == "line" and (over := self._mir_pulled(d, step)) is not None:
            return over
        return {k: q + step for k, q in d["start"].items()}

    def _drag_to(self, m):
        d = self.drag
        if d["kind"] == "dim":
            t = self.ties[d["index"]]
            self._dim_put(t, m)
            d["moved"] = True
            return
        self.hint = self._candidates(m, d["moving"])
        if self.hint is not None:
            m = self.hint["at"]
        if d["long"]:  # what the sizes allow: only this arc, then the width kept, then the whole slot
            got = None
            for how in (0, 1, 2):
                targets = {k: (p.x(), p.y()) for k, p in self._slot_long_targets(d, m, how).items()}
                got = sk.solve([(p.x(), p.y()) for p in self.pts], d["rels"], targets, d["fixed"])
                if got is not None and all(math.hypot(got[k][0] - q[0], got[k][1] - q[1]) <= 1e-3 for k, q in targets.items()):
                    break
                got = None
            if got is None:
                return
            for p, (x, y) in zip(self.pts, got):
                if abs(p.x() - x) > 1e-12 or abs(p.y() - y) > 1e-12:
                    p.setX(x)
                    p.setY(y)
                    d["moved"] = True
            return
        targets = {k: (p.x(), p.y()) for k, p in self._drag_targets(m).items()}
        targets.update({k: (p.x(), p.y()) for k, p in d["stay"].items()})
        got = sk.solve([(p.x(), p.y()) for p in self.pts], d["rels"], targets, d["fixed"])
        if d["stay"] and (got is None or any(math.hypot(got[k][0] - q[0], got[k][1] - q[1]) > 1e-3 for k, q in targets.items())):
            # a slot whose width is held does not get wider: a pulled side moves all of it
            step = m - d["at"]
            whole = {k: (d["was"][k].x() + step.x(), d["was"][k].y() + step.y()) for k in self._slot_group(d["pts"])}
            got = sk.solve([(p.x(), p.y()) for p in self.pts], d["rels"], whole, d["fixed"])
        if got is None:
            return  # the relations do not reach there: it stays at the last place they did
        for p, (x, y) in zip(self.pts, got):
            if abs(p.x() - x) > 1e-12 or abs(p.y() - y) > 1e-12:
                p.setX(x)
                p.setY(y)
                d["moved"] = True

    def _drag_end(self, tie=False):
        self.corner_loose = set()
        d, self.drag = self.drag, None
        log.say("pull ends", d["kind"], d["index"], "moved", d["moved"], "on", self.hint and self.hint["label"])
        hint, self.hint = self.hint, None
        self.taken = []
        if d.get("mir_cut") and not d["moved"]:  # a click, no pull: nothing is cut
            self.ties += d["mir_cut"]
        if d["kind"] == "dim":
            if d["moved"]:
                self.steps.append(("state", d["state"], len(self.lines), len(self.arcs)))
            else:
                self._choose(d["pick"], d["adding"])
            self._refresh()
            return
        if d["moved"] and d.get("mir_cut"):  # Cmd+Z brings the links back as well as the place
            self.steps.append(("state", d["state"], len(self.lines), len(self.arcs)))
            self._refresh()
            self.changed.emit()
            return
        if d["moved"]:
            self.sel_pts = set()  # a tying pull renumbers the points
            self.tab_point = None
            if d["kind"] == "point" and hint is not None and self._attach(d["index"], hint, tie):
                self.steps.append(("state", d["state"], len(self.lines), len(self.arcs)))
            else:
                self.steps.append(("move", d["was"]))
            self.changed.emit()
        else:
            self._choose(d["pick"], d["adding"])  # a click, not a pull: it chooses
        self._refresh()

    def _choose(self, what, adding):
        kind, i = what
        if kind == "dim":
            self.con_lit = (self.con_lit ^ {("tie", i)}) if adding or self.con_open else {("tie", i)}  # its row in the list too
            self.con_changed.emit()
            if adding:
                self.sel_dims ^= {i}
            else:
                self._clear_choice()
                self.sel_dims = {i}
            return
        if kind == "point":  # a point is taken by a click only; among several in one place, Tab goes on from it
            if adding:
                self.sel_pts ^= {i}
            else:
                self._clear_choice()
                self.sel_pts = {i}
            self.tab_point = i if i in self.sel_pts and len(self._stack(i)) > 1 else None
            return
        chosen = {"line": self.sel_lines, "arc": self.sel_arcs, "ell": self.sel_ells, "spl": self.sel_spls}.get(kind)
        if adding:
            chosen ^= {i}
        elif i not in chosen or len(self.sel_lines) + len(self.sel_arcs) + len(self.sel_ells) + len(self.sel_spls) > 1:
            self._clear_choice()
            ({"line": self.sel_lines, "arc": self.sel_arcs, "ell": self.sel_ells, "spl": self.sel_spls}[kind]).add(i)

    def _attach(self, p, hint, tie):
        """A pulled point let go on a hint: it only lies there; with Shift it is tied to it (the user), as a drawn one
        is. True: the sketch's ties changed."""
        if hint["kind"] == "point" and hint["ref"] == "origin":
            # a point standing on the origin holds to it, unless it merely lies there
            had = [t for t in self.ties if t["kind"] == "free" and p in t["pts"]]
            self.ties = [t for t in self.ties if t not in had]
            if not tie:
                self._add_tie("free", [p])
            return bool(had) or not tie
        if not tie:
            return False
        if hint["kind"] == "ext":
            hint = dict(hint, kind="on")
        if hint["kind"] == "point":
            q = hint["ref"]
            swap = lambda k: q if k == p else k
            self.lines = [tuple(map(swap, ln)) for ln in self.lines]
            self.arcs = [tuple(map(swap, arc)) for arc in self.arcs]
            self.ells = [tuple(map(swap, e)) for e in self.ells]
            self.spls = [(s_[0], tuple(map(swap, s_[1])), s_[2]) for s_ in self.spls]
            for t in self.ties:
                t["pts"] = [swap(k) for k in t["pts"]]
            self._drop_lone_points()
            return True
        before = len(self.ties)
        self._tie(p, hint)
        return len(self.ties) != before

    def _hold_cancel(self):
        """Esc or the right button while a pull or a box goes on: all as it was."""
        if self.drag is not None:
            d, self.drag = self.drag, None
            log.say("pull given up", d["kind"], d["index"])
            self.pts = d["was"]
            self.hint = None
            self.taken = []
        self.boxing = None
        self._refresh()

    # ----- choosing by a box -----
    def _box_end(self):
        b, self.boxing = self.boxing, None
        if self.tool == "constraint":
            self._con_box_end(b)
            return
        if self.tool in CORNER_TOOLS:
            self.corner_box_end(b)
            return
        if self.tool == "sk_pattern":
            self.pattern_box_end(b)
            return
        small = _len(b["to"] - b["at"]) * self.scale < 3
        self.box_ells = set()
        lines, arcs = (set(), set()) if small else self._in_box(b["at"], b["to"], b["touch"])
        log.say("box", "touching" if b["touch"] else "inside", log.pt(b["at"]), log.pt(b["to"]), "lines", len(lines),
                "arcs", len(arcs))
        centre = {("origin", None)} if not small and self._con_box_origin(b) else set()  # the centre point inside is taken
        if b["add"]:
            self.sel_lines |= lines
            self.sel_arcs |= arcs
            self.sel_ells |= self.box_ells
            self.sel_spls |= self.box_spls
            self.sel_places |= centre
        else:
            self._clear_choice()
            self.sel_lines, self.sel_arcs = lines, arcs
            self.sel_ells = set(self.box_ells)
            self.sel_spls = set(self.box_spls)
            self.sel_places = centre
        self.box_ells = set()
        self.box_spls = set()
        if not small:
            x0, x1 = sorted((b["at"].x(), b["to"].x()))
            y0, y1 = sorted((b["at"].y(), b["to"].y()))
            for t in self.dims():
                shape = self._dim_shape(t)
                q = self.to_model(self._dim_mid(shape, self._dim_text(t), t)) if shape else None
                if q is not None and x0 <= q.x() <= x1 and y0 <= q.y() <= y1:
                    self.sel_dims.add(id(t))
            self.sel_pts |= self._loose_in(b["at"], b["to"])
        self._refresh()

    def _loose_in(self, a, b):
        """The points placed as points inside the frame (a line's or a shape's own points are never taken by it)."""
        x0, x1 = sorted((a.x(), b.x()))
        y0, y1 = sorted((a.y(), b.y()))
        ghost = self._ghost_points()
        return {k for t in self.ties if t["kind"] == "sk_point" for k in t["pts"]
                if x0 <= self.pts[k].x() <= x1 and y0 <= self.pts[k].y() <= y1 and k not in ghost}

    def _is_tied(self, k):
        """Something holds this point: two shapes share it, or it is tied to a line, an arc, a point, the origin."""
        users = sum(1 for ln in self.lines if k in ln) + sum(1 for arc in self.arcs if k in arc) \
            + sum(s_[1].count(k) for s_ in self.spls)
        if users > 1 or any(k in t["pts"] and t["kind"] not in ("free", "same_r") for t in self.ties):
            return True
        return _len(self.pts[k]) < 1e-9 and not any(t["kind"] == "free" and k in t["pts"] for t in self.ties)

    # ----- what a file holds -----
    def dump(self):
        """All of the sketch as plain values; a temporary file of ours until .FCStd comes."""
        return dict(version=1, units=units.now, plane=getattr(self, "sketch_plane", "xy"), name=self.sketch_name, pts=[(p.x(), p.y()) for p in self.pts], lines=list(self.lines),
                    arcs=list(self.arcs), ells=list(self.ells), constr=list(self.constr), arc_constr=list(self.arc_constr),
                    ell_constr=list(self.ell_constr), relations=list(self.relations),
                    ties=[dict(t, pts=list(t["pts"])) for t in self.ties], params=[dict(q) for q in self.params],
                    dim_next=self.dim_next, centre=(self.center.x(), self.center.y()), scale=self.scale,
                    spls=list(self.spls), spl_constr=list(self.spl_constr), spl_comb=list(self.spl_comb),
                    dim_constr=self.dim_constr, images=[dict(d) for d in self.images],
                    shape_ids={k: list(v) for k, v in self._fit_ids().items()})

    def load(self, data):
        if self.tool:
            self.stop_tool()
        units.now = data.get("units", "mm")
        self.sketch_plane = data.get("plane", "xy")
        self.pts = [P(x, y) for x, y in data["pts"]]
        self.lines, self.arcs, self.ells = list(data["lines"]), list(data["arcs"]), list(data["ells"])
        self.constr, self.arc_constr = list(data["constr"]), list(data["arc_constr"])
        self.ell_constr, self.relations = list(data["ell_constr"]), list(data["relations"])
        self.ties, self.params = list(data["ties"]), list(data["params"])
        self.spls, self.spl_constr = list(data.get("spls", [])), list(data.get("spl_constr", []))
        self.spl_comb = list(data.get("spl_comb", []))
        self.images = [dict(d) for d in data.get("images", [])]
        self.shape_ids = {k: list(v) for k, v in (data.get("shape_ids") or {}).items()}
        self._fit_ids()
        self.set_dim(bool(data.get("dim_constr")))
        self.dim_next, self.sketch_name = data["dim_next"], data["name"]
        self.center, self.scale = P(*data["centre"]), data["scale"]
        self.dim_ref_scale = self.scale
        if hasattr(self, "dim_look"):
            self._dim_grow()
        self.steps, self.taken = [], []
        self._clear_choice()
        self.over = None
        self.changed.emit()
        self._refresh()

    def _clear_choice(self):
        self.sel_lines, self.sel_arcs, self.sel_pts = set(), set(), set()
        self.sel_ells = set()
        self.sel_spls = set()
        self.sel_dims = set()
        self.sel_places = set()
        self.tab_point = None

    def set_dim(self, on):
        self.dim_constr = on
        if on:
            self._clear_choice()
            self.over = None
        self.dim_changed.emit(on)
        self.update()

    def _ghost(self, item):
        """Dim Construction Lines is on and this shape is a construction one."""
        if not self.dim_constr:
            return False
        kind, i = item
        return self.is_construction(i) if kind == "line" else self.is_arc_construction(i) if kind == "arc" else \
            (i < len(self.ell_constr) and self.ell_constr[i]) if kind == "ell" else (i < len(self.spl_constr) and self.spl_constr[i])

    def _shape_points(self, item):
        kind, i = item
        return self.lines[i] if kind == "line" else self.arcs[i] if kind == "arc" else self._ell_keys(i) if kind == "ell" else self.spls[i][1]

    def _ghost_points(self):
        """Dimmed: the points only construction shapes use."""
        if not self.dim_constr:
            return set()
        ghost, solid = set(), set()
        for item in self._all_shapes(everything=True):
            (ghost if self._ghost(item) else solid).update(self._shape_points(item))
        ghost |= {k for t in self.ties if t["kind"] == "mid" and self._ghost(("line", t["ref"])) for k in t["pts"]}
        return ghost - solid

    def _ghost_place(self, at, kind):
        """A magnet that stands only on dimmed shapes; a crossing with one is no magnet either."""
        on_ghost = on_solid = False
        for item in self._all_shapes(everything=True):
            if self._on_geom(self._geom(item), at):
                if self._ghost(item):
                    on_ghost = True
                else:
                    on_solid = True
        return on_ghost and (not on_solid or kind in ("x", "ell_x"))

    def choose_free(self):
        """Show Free Geometry: all that can still move is chosen, ready for a constraint, Construction or Delete. How many."""
        self.free_count()
        self._clear_choice()
        self.sel_lines = {i for i, x in self.line_state.items() if x != "full"}
        self.sel_arcs = {i for i, x in self.arc_state.items() if x != "full"}
        self.sel_ells = {i for i, x in self.ell_state.items() if x != "full"}
        self.sel_spls = {i for i, x in self.spl_state.items() if x != "full"}
        self.sel_pts = {k for t in self.ties if t["kind"] == "sk_point" for k in t["pts"] if self.ways.get(k)}
        self.update()
        return len(self.sel_lines) + len(self.sel_arcs) + len(self.sel_ells) + len(self.sel_spls) + len(self.sel_pts)

    def has_chosen(self):
        return bool(self.sel_lines or self.sel_arcs or self.sel_ells or self.sel_spls)

    def flip_construction(self):
        """Format ▸ Construction with something chosen: each chosen shape turns over, normal to construction and back.
        One Cmd+Z takes it all back."""
        if not self.has_chosen():
            return 0
        st = self._state()
        count = len(self.sel_lines) + len(self.sel_arcs) + len(self.sel_ells) + len(self.sel_spls)
        for chosen, flags, n in ((self.sel_lines, "constr", len(self.lines)), (self.sel_arcs, "arc_constr", len(self.arcs)),
                                 (self.sel_ells, "ell_constr", len(self.ells)), (self.sel_spls, "spl_constr", len(self.spls))):
            now = (list(getattr(self, flags)) + [False] * n)[:n]
            for i in chosen:
                now[i] = not now[i]
            setattr(self, flags, now)
        self.steps.append(("state", st, len(self.lines), len(self.arcs)))
        log.say("construction turned over", count)
        self._clear_choice()
        self.changed.emit()
        self._refresh()
        return count

    def take_under(self, s):
        """A right click on something not chosen chooses it first."""
        if self.drag is not None:
            return None
        what = self._pick(s, ("point", "line", "arc", "ell", "spl"))
        if what is None:
            k = self._dim_hit(s)
            what = ("dim", k) if k is not None else None
        if what is None:
            return None
        kind, i = what
        chosen = {"point": self.sel_pts, "line": self.sel_lines, "arc": self.sel_arcs, "ell": self.sel_ells, "spl": self.sel_spls}.get(kind)
        if kind == "dim":
            if id(self.ties[i]) not in self.sel_dims:
                self._clear_choice()
                self.sel_dims = {id(self.ties[i])}
        elif i not in chosen:
            self._clear_choice()
            chosen = {"point": self.sel_pts, "line": self.sel_lines, "arc": self.sel_arcs, "ell": self.sel_ells, "spl": self.sel_spls}[kind]
            chosen.add(i)
        self._refresh()
        return what

    def _loose_chosen(self):
        return {k for t in self.ties if t["kind"] == "sk_point" for k in t["pts"]} & self.sel_pts

    def delete_chosen(self):
        if self.can_delete():
            self._delete()
            self._refresh()

    def can_delete(self):
        return self.has_chosen() or bool(self.sel_dims) or bool(self._loose_chosen())

    def can_break(self):
        return any(self._is_tied(k) for k in self.sel_pts)

    def tab_line(self):
        """What the point Tab chose belongs to: the line lit with it."""
        k = self.tab_point
        if k is None or k >= len(self.pts):
            return None
        return next((i for i, ln in enumerate(self.lines) if k in ln), None)

    def break_constraint(self):
        """The chosen tied points come apart: each shape gets its own point, standing where it stood; whatever tied them
        goes. Cmd+Z ties them again."""
        chosen = {k for k in self.sel_pts if self._is_tied(k)}
        if not chosen:
            return
        st = self._state()
        self.ties = [t for t in self.ties if not (chosen & set(t["pts"])) or t["kind"] == "free"]
        for k in sorted(chosen):
            if _len(self.pts[k]) < 1e-9:
                self._add_tie("free", [k])
            first = True
            for i, ln in enumerate(self.lines):
                if k in ln:
                    if not first:
                        self.pts.append(P(self.pts[k]))
                        self.lines[i] = tuple(len(self.pts) - 1 if q == k else q for q in ln)
                    first = False
            for i, arc in enumerate(self.arcs):
                if k in arc:
                    if not first:
                        self.pts.append(P(self.pts[k]))
                        self.arcs[i] = tuple(len(self.pts) - 1 if q == k else q for q in arc)
                    first = False
            for i, (kind, pts, closed) in enumerate(self.spls):
                if k in pts:  # every use of it but the first gets a point of its own, a spline through itself too
                    new = []
                    for q in pts:
                        if q == k and not first:
                            self.pts.append(P(self.pts[k]))
                            q = len(self.pts) - 1
                        elif q == k:
                            first = False
                        new.append(q)
                    self.spls[i] = (kind, tuple(new), closed)
        self.steps.append(("state", st, len(self.lines), len(self.arcs)))
        log.say("break constraint at", len(chosen), "points")
        self._clear_choice()
        self.changed.emit()
        self._refresh()

    def _in_box(self, a, b, touch):
        x0, x1 = sorted((a.x(), b.x()))
        y0, y1 = sorted((a.y(), b.y()))

        def inside(p):
            return x0 <= p.x() <= x1 and y0 <= p.y() <= y1

        def crossing(p, q):
            """p…q meets the box (Liang–Barsky)."""
            t0, t1 = 0.0, 1.0
            d = q - p
            for dd, lo in ((-d.x(), p.x() - x0), (d.x(), x1 - p.x()), (-d.y(), p.y() - y0), (d.y(), y1 - p.y())):
                if abs(dd) < 1e-12:
                    if lo < 0:
                        return False
                    continue
                t = lo / dd
                if dd < 0:
                    t0 = max(t0, t)
                else:
                    t1 = min(t1, t)
                if t0 > t1:
                    return False
            return True

        def taken(path):
            if touch:
                return any(crossing(p, q) for p, q in zip(path, path[1:]))
            return all(inside(p) for p in path)

        lines = {i for i, (ia, ib) in enumerate(self.lines) if taken([self.pts[ia], self.pts[ib]])}
        arcs = set()
        for i in range(len(self.arcs)):
            c, r, a0, sw = self.arc_shape(i)
            n = max(8, int(sw / TAU * 96))
            if taken([_on_circle(c, r, a0 + sw * k / n) for k in range(n + 1)]):
                arcs.add(i)
        self.box_ells = set()
        for i in range(len(self.ells)):
            if taken(self._ell_path(i, 48)):
                self.box_ells.add(i)
        self.box_spls = {i for i in range(len(self.spls)) if taken(self.spl_poly(i))}
        if self.dim_constr:  # dimmed: the box goes past them
            lines = {i for i in lines if not self._ghost(("line", i))}
            arcs = {i for i in arcs if not self._ghost(("arc", i))}
            self.box_ells = {i for i in self.box_ells if not self._ghost(("ell", i))}
            self.box_spls = {i for i in self.box_spls if not self._ghost(("spl", i))}
        return lines, arcs

    # ----- taking away -----
    def _fit_ids(self):
        """The shape ids as long as the shapes: a shape added since has none (None); a removed one's is gone."""
        ids = getattr(self, "shape_ids", None) or dict(line=[], arc=[], ell=[], spl=[])
        for kind, shapes in (("line", self.lines), ("arc", self.arcs), ("ell", self.ells), ("spl", self.spls)):
            got = list(ids.get(kind, []))[:len(shapes)]
            ids[kind] = got + [None] * (len(shapes) - len(got))
        self.shape_ids = ids
        return ids

    def _state(self):
        """All the sketch as it stands: taking something away renumbers it, so Cmd+Z brings the whole of it back."""
        return dict(pts=[P(p) for p in self.pts], lines=list(self.lines), arcs=list(self.arcs),
                    ells=list(self.ells), ell_constr=list(self.ell_constr), constr=list(self.constr), arc_constr=list(self.arc_constr), relations=list(self.relations),
                    ties=[dict(t, pts=list(t["pts"])) for t in self.ties], steps=list(self.steps),
                    params=[dict(q) for q in self.params], spls=list(self.spls), spl_constr=list(self.spl_constr),
                    spl_comb=list(self.spl_comb), images=[dict(d) for d in self.images],
                    shape_ids={k: list(v) for k, v in self._fit_ids().items()})

    def _restore(self, st):
        self.pts, self.lines, self.arcs = st["pts"], st["lines"], st["arcs"]
        self.ells, self.ell_constr = st["ells"], st["ell_constr"]
        self.constr, self.arc_constr = st["constr"], st["arc_constr"]
        self.relations, self.ties, self.steps = st["relations"], st["ties"], st["steps"]
        self.params = st["params"]
        self.spls, self.spl_constr, self.spl_comb = st["spls"], st["spl_constr"], st["spl_comb"]
        self.images = st["images"]
        self.shape_ids = {k: list(v) for k, v in st.get("shape_ids", {}).items()}
        self._fit_ids()
        # points rested on or chosen that the state put back has no more (a Text window cancelled: its box's corners
        # went): the paint asked for them and failed
        self.taken = [k for k in self.taken if k == "origin" or k < len(self.pts)]
        self.sel_pts = {k for k in self.sel_pts if k < len(self.pts)}

    def _delete(self):
        gone_l, gone_a = self.sel_lines, self.sel_arcs
        gone_e = self.sel_ells
        gone_s = self.sel_spls
        gone_p = {k for t in self.ties if t["kind"] == "sk_point" for k in t["pts"]} & self.sel_pts
        if self.sel_dims:  # the dimensions chosen go with whatever else is chosen
            st = self._state()
            going = {t["name"] for t in self.dims() if id(t) in self.sel_dims}
            kept = [n for n in sorted(going) if set(self._dim_uses(n)) - going]
            if kept:
                self.prompt.emit(kept[0] + " stays: " + ", ".join(self._dim_uses(kept[0])) + " uses it")
            self.ties = [t for t in self.ties if id(t) not in self.sel_dims or t["name"] in kept]
            self.sel_dims = set()
            if not gone_l and not gone_a and not gone_e and not gone_s and not gone_p:
                self.over = None
                self.steps.append(("state", st, len(self.lines), len(self.arcs)))
                self.changed.emit()
                self._refresh()
                return
        if not gone_l and not gone_a and not gone_e and not gone_s and not gone_p:
            return
        st = self._state()
        self.ties = [t for t in self.ties if not (t["kind"] == "sk_point" and set(t["pts"]) & gone_p)]
        new_l = {}
        for i in range(len(self.lines)):
            if i not in gone_l:
                new_l[i] = len(new_l)
        new_a = {}
        for i in range(len(self.arcs)):
            if i not in gone_a:
                new_a[i] = len(new_a)
        relations = []
        for kind, line, end, ref in self.relations:
            if line is None or line not in new_l:
                continue
            if kind == "perp":
                ref = new_l.get(line - 1 if ref is None else ref)
                if ref is None:
                    continue
            elif kind == "par":
                ref = new_l.get(ref)
                if ref is None:
                    continue
            elif kind in ("tan", "tan_on"):
                ref = new_a.get(ref)
                if ref is None:
                    continue
            relations.append((kind, new_l[line], end, ref))
        ids = self._fit_ids()
        self.shape_ids = dict(line=[g for i, g in enumerate(ids["line"]) if i in new_l],
                              arc=[g for i, g in enumerate(ids["arc"]) if i in new_a],
                              ell=[g for i, g in enumerate(ids["ell"]) if i not in gone_e],
                              spl=[g for i, g in enumerate(ids["spl"]) if i not in gone_s])
        self.lines = [ln for i, ln in enumerate(self.lines) if i in new_l]
        self.arcs = [arc for i, arc in enumerate(self.arcs) if i in new_a]
        self.ell_constr = [c for i, c in enumerate(st["ell_constr"]) if i not in gone_e]
        self.ells = [e for i, e in enumerate(self.ells) if i not in gone_e]
        self.spl_constr = [c for i, c in enumerate(st["spl_constr"]) if i not in gone_s]
        self.spl_comb = [c for i, c in enumerate((st["spl_comb"] + [False] * len(self.spls))[:len(self.spls)]) if i not in gone_s]
        self.spls = [s_ for i, s_ in enumerate(self.spls) if i not in gone_s]
        self.constr = [c for i, c in enumerate(st["constr"]) if i in new_l]
        self.arc_constr = [c for i, c in enumerate(st["arc_constr"]) if i in new_a]
        self.relations = relations
        used = {k for ln in self.lines for k in ln} | {k for arc in self.arcs for k in arc} | {k for e in self.ells for k in e} \
            | {k for s_ in self.spls for k in s_[1]}
        used |= {k for t in self.ties if t["kind"] == "mid" and t["ref"] in new_l for k in t["pts"]}  # a diagonal's middle stays
        used |= {k for t in self.ties if t["kind"] == "ell_cut" and tuple(t["pts"][2:]) in self.ells for k in t["pts"]}
        used |= {k for t in self.ties if t["kind"] == "sk_point" for k in t["pts"]}
        ties = []
        for t in self.ties:
            kind, ref = t["kind"], t["ref"]
            if any(k not in used for k in t["pts"]):
                continue
            if kind in ("on_line", "mid"):
                ref = new_l.get(ref)
            elif kind == "on_arc":
                ref = new_a.get(ref)
            elif kind == "same_r":
                ref = (new_a[ref[0]], new_a[ref[1]]) if ref[0] in new_a and ref[1] in new_a else None
            elif kind == "touch":
                arc, (what, i) = ref
                other = (new_l if what == "line" else new_a).get(i)
                ref = (new_a[arc], (what, other)) if arc in new_a and other is not None else None
            if ref is None and kind in ("on_line", "mid", "on_arc", "same_r", "touch"):
                continue
            ties.append(dict(t, ref=ref, n=0))  # older than anything drawn from here on
        self.ties = ties
        self._clear_choice()
        self.over = None
        self.taken = []
        self._drop_lone_points()
        self.steps.append(("state", st, len(self.lines), len(self.arcs)))
        log.say("delete, left", len(self.lines), "lines", len(self.arcs), "arcs")
        self.changed.emit()
        self._refresh()

    # ----- copy and paste -----
    def _copy(self):
        lines, arcs = sorted(self.sel_lines), sorted(self.sel_arcs)
        ells = sorted(self.sel_ells)
        spls = sorted(self.sel_spls)
        if not lines and not arcs and not ells and not spls and not self._loose_chosen():
            return
        new_l = {old: k for k, old in enumerate(lines)}
        new_a = {old: k for k, old in enumerate(arcs)}
        # a chosen diagonal brings the point in its middle along
        used = sorted(self._chosen_points() | {k for t in self.ties if t["kind"] == "mid" and t["ref"] in new_l for k in t["pts"]})
        new_p = {old: k for k, old in enumerate(used)}
        pts = [P(self.pts[k]) for k in used]
        relations = []
        for kind, line, end, ref in self.relations:  # only what holds inside the copy goes with it
            if line not in new_l:
                continue
            if kind in ("h", "v"):
                relations.append((kind, new_l[line], end, None))
            elif kind == "perp":
                other = line - 1 if ref is None else ref
                if other in new_l:
                    relations.append((kind, new_l[line], end, new_l[other]))
            elif kind == "par" and ref in new_l:
                relations.append((kind, new_l[line], end, new_l[ref]))
            elif kind in ("tan", "tan_on") and ref in new_a:
                relations.append((kind, new_l[line], end, new_a[ref]))
        ties = []
        for t in self.ties:
            kind, ref = t["kind"], t["ref"]
            if kind in ("x0", "y0", "centre", "fix") or any(k not in new_p for k in t["pts"]):
                continue  # held to the origin or to something left behind
            if kind == "sk_point" and self._is_ext(ref):
                ref = None  # a copy of a projected vertex is the sketch's own point
            if kind in ("on_line", "mid"):
                if ref not in new_l:
                    continue
                ref = new_l[ref]
            elif kind == "on_arc":
                if ref not in new_a:
                    continue
                ref = new_a[ref]
            elif kind == "same_r":
                if ref[0] not in new_a or ref[1] not in new_a:
                    continue
                ref = (new_a[ref[0]], new_a[ref[1]])
            elif kind == "touch":
                arc, (what, i) = ref
                other = new_l.get(i) if what == "line" else new_a.get(i)
                if arc not in new_a or other is None:
                    continue
                ref = (new_a[arc], (what, other))
            ties.append(dict(kind=kind, pts=[new_p[k] for k in t["pts"]], ref=ref, mark=t.get("mark"), off=t.get("off"), along=t.get("along"),
                             far=t.get("far"), neg=t.get("neg"), driven=t.get("driven"), axis=t.get("axis"),
                             base=t.get("base"), first=t.get("first"), as_d=t.get("as_d")))
        shape = [P(_on_circle(*self.arc_shape(i)[:2], self.arc_shape(i)[2] + self.arc_shape(i)[3] * k / 16))
                 for i in arcs for k in range(17)] + pts
        xs, ys = [p.x() for p in shape], [p.y() for p in shape]
        self.clip = dict(pts=pts, lines=[(new_p[a], new_p[b]) for a, b in (self.lines[i] for i in lines)],
                         arcs=[tuple(new_p[k] for k in self.arcs[i]) for i in arcs],
                         ells=[tuple(new_p[k] for k in self.ells[i]) for i in ells],
                         ell_constr=[i < len(self.ell_constr) and self.ell_constr[i] for i in ells],
                         spls=[(self.spls[i][0], tuple(new_p[k] for k in self.spls[i][1]), self.spls[i][2]) for i in spls],
                         spl_constr=[i < len(self.spl_constr) and self.spl_constr[i] for i in spls],
                         constr=[self.is_construction(i) for i in lines],
                         arc_constr=[self.is_arc_construction(i) for i in arcs], relations=relations, ties=ties,
                         middle=P((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2))
        log.say("copy", len(lines), "lines", len(arcs), "arcs")

    def _paste(self):
        if self.basing is not None:
            self._base_done()
        if self.clip is None or not self.editing:
            return
        if self.tool:
            self.stop_tool()
        self.pasting = True
        self.prompt.emit("Paste  Click where the copy goes  ·  Space: turn it  ·  Esc gives it up")
        self._refresh()

    def _paste_cancel(self):
        self.pasting = self.last_place = None
        self.prompt.emit("Pick a sketch tool  ·  pull a line or a point to move it  ·  right-click: Finish Sketch")
        self._refresh()

    def _paste_points(self, m):
        if self.pts_turn is not None:
            return [self._turn_point(p) for p in self.clip["pts"]]
        if self.rotating is not None:
            c, a = self.rotating["pivot"], self.rotating["angle"]
            co, si = math.cos(a), math.sin(a)
            return [c + P((p - c).x() * co - (p - c).y() * si, (p - c).x() * si + (p - c).y() * co) for p in self.clip["pts"]]
        step = m - self.clip.get("base", self.clip["middle"])
        return [p + step for p in self.clip["pts"]]

    def _turn_point(self, p):
        c, a = self.pts_turn
        co, si = math.cos(a), math.sin(a)
        return c + P((p - c).x() * co - (p - c).y() * si, (p - c).x() * si + (p - c).y() * co)

    def _paste_place(self, m):
        c = self.clip
        base, base_l, base_a = len(self.pts), len(self.lines), len(self.arcs)
        base_e = len(self.ells)
        st = self._state() if c["ells"] or c["spls"] else None
        base_s = len(self.spls)
        self.pts += self._paste_points(m)
        self.spls += [(kind, tuple(k + base for k in pts), closed) for kind, pts, closed in c["spls"]]
        self.spl_constr = (self.spl_constr + [False] * base_s)[:base_s] + list(c["spl_constr"])
        self.ells += [tuple(k + base for k in e) for e in c["ells"]]
        self.ell_constr = (self.ell_constr + [False] * base_e)[:base_e] + list(c["ell_constr"])
        self.lines += [(a + base, b + base) for a, b in c["lines"]]
        self.arcs += [tuple(k + base for k in arc) for arc in c["arcs"]]
        self.constr = (self.constr + [False] * base_l)[:base_l] + list(c["constr"])
        self.arc_constr = (self.arc_constr + [False] * base_a)[:base_a] + list(c["arc_constr"])
        self.steps.append(("state", st, len(self.lines), len(self.arcs)) if st else (len(c["lines"]), len(c["arcs"])))
        for kind, line, end, ref in c["relations"]:
            ref = ref if ref is None else ref + (base_a if kind in ("tan", "tan_on") else base_l)
            self.relations.append((kind, line + base_l, end, ref))
        for t in c["ties"]:
            ref, kind = t["ref"], t["kind"]
            if kind in ("on_line", "mid"):
                ref += base_l
            elif kind == "on_arc":
                ref += base_a
            elif kind == "same_r":
                ref = (ref[0] + base_a, ref[1] + base_a)
            elif kind == "touch":
                ref = (ref[0] + base_a, (ref[1][0], ref[1][1] + (base_l if ref[1][0] == "line" else base_a)))
            self._add_tie(kind, [k + base for k in t["pts"]], dict(ref) if kind == "sk_text" else ref, t.get("mark"))
            if t.get("off") is not None:
                self.ties[-1].update(off=t["off"], far=t.get("far"), along=t.get("along"), neg=t.get("neg"), driven=t.get("driven"),
                                     axis=t.get("axis"), base=t.get("base"), first=t.get("first"))
        self.sel_lines = set(range(base_l, len(self.lines)))
        self.sel_arcs = set(range(base_a, len(self.arcs)))
        self.sel_ells = set(range(base_e, len(self.ells)))
        self.sel_spls = set(range(base_s, len(self.spls)))
        log.say("paste at", log.pt(m), len(c["lines"]), "lines", len(c["arcs"]), "arcs")
        self.changed.emit()  # the next copy hangs on the pointer until Esc
        self.prompt.emit("Paste  Click where the next copy goes  ·  Space: turn it  ·  Esc ends")
        self._refresh()

    # ----- events -----
    def mouseMoveEvent(self, e):
        if time.monotonic() - self._cursor_at > 0.3:
            # a menu, or another app, leaves the Mac's arrow behind and Qt gets no Enter to bring the cross back: the
            # pointer came out of the right-click menu as an arrow (the log: "pointer leaves the view" at the menu)
            self._cursor_at = time.monotonic()
            self.unsetCursor()
            self.setCursor(self.cross)
        if self.ring is not None:  # the right button is still down: the flick goes on, wherever Qt sends the moves
            self.ring.flick_move(e.globalPosition().toPoint())
            return
        s = e.position()
        if self._pan_from is not None and not e.buttons() & QtCore.Qt.MiddleButton:
            self._pan_from = None  # its release went somewhere else: the view must not stay stuck to the mouse
        if self._pan_from is not None:
            d = s - self._pan_from
            self._pan_from = s
            self.center -= P(d.x() / self.scale, -d.y() / self.scale)
        self.mouse = self.to_model(s)
        self.cursor_at.emit(self.mouse.x(), self.mouse.y())
        if self.drag is not None:
            self._drag_to(self.mouse)
        elif self.boxing is not None:
            self.boxing["to"] = P(self.mouse)
            self.boxing["touch"] = self.mouse.x() < self.boxing["at"].x()
            self.boxing["lines"], self.boxing["arcs"] = self._in_box(self.boxing["at"], self.mouse, self.boxing["touch"])
            self.boxing["ells"] = set(self.box_ells)
            if self.tool == "constraint":
                self.boxing["pts"] = self._con_box_points(self.boxing)
        else:
            if self.tool in TRIM_TOOLS:
                over = None
                self._trim_track(s)
            elif self.tool in CORNER_TOOLS:
                over = None
                if self.run is not None:
                    self.run["hover"] = self._corner_at(s)
            elif self.tool == "sk_offset":
                over = None
                self._off_track(s)
            elif self.tool == "sk_mirror":
                over = None
                self._mir_track(s)
            elif self.tool == "img_scale":
                over = None
                self._img_track(s)
            elif self.tool == "sk_pattern":
                over = None
                if self.pat is not None:
                    self._pat_track(s)
            elif self.tool == "constraint":
                over = None if self.con_ask else self._pick(s, self._con_wanted())
            elif self.tool == "circle_tangent":
                over = None if self._tc_solutions() else self._pick(s)
            elif self.tool == "dimension":
                self._dim_track()
                over = None if self.box_dim.isVisible() else self._pick(s, ("point", "line", "arc"))
            else:
                over = self._pick(s, ("point", "line", "arc", "ell", "spl")) if self.editing and not self.tool and self.pasting is None else None
                if over is None and self.editing and not self.tool and self.pasting is None:
                    k = self._dim_hit(s)
                    over = ("dim", k) if k is not None else None
            self.tile_tip = next((t for t in self.tiles if t[0].contains(s)), None) \
                if self.editing and not self.tool and self.pasting is None else None
            if over is not None and over[0] == "point" and self.tab_point in self._stack(over[1]):
                over = ("point", self.tab_point)  # Tab chose it: it stays chosen until a point is moved
            if over != self.over:
                self.over = over
            if over is not None and over[0] == "dim" and self.tile_tip is None:
                t = self.ties[over[1]]
                name = {"dim_len": "Length", "dim_c2c": "Between centres", "dim_rl_c": "Centre to line", "dim_rl_gap": "Gap to line", "dim_rl_far": "Far edge to line", "dim_rp_c": "Centre to point", "dim_rp_gap": "Gap to point", "dim_rp_far": "Far edge to point", "dim_far_x": "Overall along X", "dim_far_y": "Overall along Y", "dim_gap_x": "Gap along X", "dim_gap_y": "Gap along Y",
                        "dim_rp_gap_x": "Gap along X", "dim_rp_gap_y": "Gap along Y", "dim_rp_far_x": "Far edge along X", "dim_rp_far_y": "Far edge along Y", "dim_over": "Overall", "dim_gap": "Between inner edges", "dim_dx": "Horizontal distance", "dim_dy": "Vertical distance", "dim_par": "Distance",
                        "dim_r": "Radius", "dim_d": "Diameter", "dim_ang": "Angle", "dim_angx": "Angle to the axis",
                        "dim_x": "Distance to the Y axis", "dim_y": "Distance to the X axis", "dim_o": "Distance to the origin", "dim_lo": "Distance to the origin"}[t["kind"]]
                self.tile_tip = (QtCore.QRectF(s.x(), s.y() - 24, 1, 18), name + " " + self._dim_text(t))
            if over is not None and over[0] == "point" and len(self._stack(over[1])) > 1:
                self.setFocus()  # for the space bar
        self._refresh()

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.just_done = False
        log.say("mouse", {QtCore.Qt.LeftButton: "left", QtCore.Qt.RightButton: "RMB",
                          QtCore.Qt.MiddleButton: "middle"}.get(e.button(), e.button()),
                "screen", log.pt(e.position()), "model", log.pt(self.to_model(e.position())), "tool", self.tool,
                "chain", self.cur is not None)
        if e.button() == QtCore.Qt.MiddleButton:
            self._pan_from = e.position()
            return
        if self.dim_panel.isVisible():  # a dimension clicked gives its name, anywhere else the value goes in
            if e.button() == QtCore.Qt.LeftButton:
                k = self._dim_hit(e.position())
                if k is not None and self.ties[k] is not self.dim_edit:
                    self.box_dim.insert_name(self.ties[k]["name"])
                else:
                    self._dim_commit()
            elif e.button() == QtCore.Qt.RightButton:
                self._dim_cancel()
            return
        if not self.tool and self.editing and e.button() == QtCore.Qt.LeftButton:  # a mark on the sketch: it chooses its row
            owner = self.con_mark_owner(e.position())
            if owner is not None:
                self.con_pick(owner, self.con_open or bool(e.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier)))
                self.setFocus()
                return
            if not self.con_open:
                self.con_clear()
        if self.tool == "img_scale" and e.button() == QtCore.Qt.LeftButton:
            self._img_press(e.position())
            return
        if self.tool == "sk_pattern" and e.button() == QtCore.Qt.LeftButton and self.pat is not None:
            self.mouse = self.to_model(e.position())
            self.pattern_press(e.position(), bool(e.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier | QtCore.Qt.ShiftModifier)))
            self.update()
            return
        if self.tool == "sk_mirror" and e.button() == QtCore.Qt.LeftButton:
            self.mouse = self.to_model(e.position())
            self.mirror_press(e.position(), bool(e.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier | QtCore.Qt.ShiftModifier)))
            self.update()
            return
        if self.tool == "sk_offset" and e.button() == QtCore.Qt.LeftButton:
            self.mouse = self.to_model(e.position())
            self.offset_press(e.position(), bool(e.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier | QtCore.Qt.ShiftModifier)))
            self.update()
            return
        if self.tool in CORNER_TOOLS and e.button() == QtCore.Qt.LeftButton:
            self.mouse = self.to_model(e.position())
            self.corner_press(e.position())
            self.update()
            return
        if self.tool in CORNER_TOOLS and e.button() == QtCore.Qt.RightButton and self.run and self.run["first"] is not None:
            self.run["first"] = None
            self._corner_prompt()
            self.update()
            return
        if self.tool in TRIM_TOOLS and e.button() == QtCore.Qt.LeftButton:
            (self._trim_click if self.tool == "trim" else self._ext_click)(e.position())
            self.update()
            return
        if self.tool == "extend" and self.ext is not None and e.button() == QtCore.Qt.RightButton:
            self.ext_cancel()
            return
        if self.text_wait is not None and e.button() == QtCore.Qt.LeftButton:
            what = self._pick(e.position(), ("line", "arc", "ell", "spl"))
            if what is not None:
                self.text_take_curve(self.text_wait, what)
                self.text_picked.emit()
            return
        if self.basing is not None:
            if e.button() == QtCore.Qt.LeftButton:
                self.mouse = self.to_model(e.position())
                self._refresh()
                self._base_done(self.hint["at"] if self.hint is not None else self.mouse)
            elif e.button() == QtCore.Qt.RightButton:
                self._base_done()
            return
        if self.rotating is not None:
            if e.button() == QtCore.Qt.LeftButton:
                self.mouse = self.to_model(e.position())
                self._refresh()
                self.rot_click(e.position())
            elif e.button() == QtCore.Qt.RightButton:
                self.rot_cancel()
            return
        if self.pasting is not None:
            if e.button() == QtCore.Qt.LeftButton:
                self.mouse = self.to_model(e.position())
                self._refresh()
                self.last_place = P(self.paste_at())
                self._paste_place(self.last_place)
            elif e.button() == QtCore.Qt.RightButton:
                self._paste_cancel()
            return
        if self.drag is not None or self.boxing is not None:
            if e.button() == QtCore.Qt.RightButton:
                self._hold_cancel()
            return
        if e.button() == QtCore.Qt.LeftButton and self.editing and not self.tool:
            self._drag_start(e.position(), self._adding(e))
            return
        if self.tool == "constraint":
            if e.button() == QtCore.Qt.LeftButton:
                self._con_click(e.position())
            elif e.button() == QtCore.Qt.RightButton:
                self._con_back()
            return
        if self.tool == "dimension":
            if e.button() == QtCore.Qt.LeftButton:
                self._dim_click(e.position())
            elif e.button() == QtCore.Qt.RightButton:
                self._dim_back()
            return
        if e.button() == QtCore.Qt.RightButton and self._tan is not None:
            self._tan = None  # back to the arc following the pointer
            self.prompt.emit(self._tool_name() + "  " + RECT_PROMPTS[self.tool][1] + "  ·  right-click gives it up")
            self._refresh()
            return
        if e.button() == QtCore.Qt.RightButton and self.tool == "circle_tangent" and self.tc_picks:
            if len(self.tc_picks) == 3:  # the one on show goes in; the right button never takes it away (the user)
                self._arc_finish()
                return
            typing = self.box_r.pending is not None
            self.box_r.hold()
            self.setFocus()
            if len(self.tc_picks) == 2:  # two are enough: it goes in as it stands, at the size typed (the user)
                if self.box_r.typed is not None:
                    self._tc_place()
                elif self._arc_now() is not None:
                    self._arc_finish()
                return
            if typing and self.box_r.typed is not None:  # one: the size holds, the second one is still to come
                self._tc_prompt()
                self._refresh()
                return
        if e.button() == QtCore.Qt.RightButton and self.tool in SPLINE_TOOLS and self.spl_pts:
            self._spl_finish()
            return
        if e.button() == QtCore.Qt.LeftButton and self.tool in SPLINE_TOOLS:
            self.mouse = self.to_model(e.position())
            self._refresh()
            self._spl_click(self.hint["at"] if self.hint else self.mouse)
            return
        if e.button() == QtCore.Qt.RightButton and self.tool in RECT_NAMES and self.rect_pts:
            if any(b.typed is not None or b.pending is not None for b in self.shape_boxes()):
                self._rect_cancel_typing()  # held sizes: the right button lets them go, the rectangle stays
            else:
                self._rect_reset()  # the rectangle begun is given up, no point left behind
                self.prompt.emit(self._tool_name() + "  " + RECT_PROMPTS[self.tool][0])
            return
        if e.button() == QtCore.Qt.RightButton:
            if self.cur is not None and (any(t is not None for t in self._typed()) or self.par is not None
                                         or self.box_len.pending is not None or self.box_ang.pending is not None):
                self._cancel_typing()  # a typed size held: the right button lets it go, the line stays
            elif self.cur is not None:
                self._end_chain()
            elif self.just_done:  # the shape is done, the next one goes the same way (the user)
                self.just_done = False
                self.setFocus()
            elif self.tool:  # the right button is Esc (the user): the tool stops; the menu is the next click
                self.stop_tool()
            elif self.editing:
                self.context.emit(e.globalPosition().toPoint())
            return
        if e.button() == QtCore.Qt.LeftButton and self.tool in RECT_NAMES:
            self.mouse = self.to_model(e.position())
            self._refresh()
            self._rect_click(self.mouse)
            return
        if e.button() == QtCore.Qt.LeftButton and self.tool in ("line", "line_single"):
            self.mouse = self.to_model(e.position())
            self._refresh()
            self.click(self.mouse)

    def mouseDoubleClickEvent(self, e):
        k = self._dim_hit(e.position()) if self.editing and not self.tool and e.button() == QtCore.Qt.LeftButton else None
        if k is None:
            t = self.text_at(e.position()) if self.editing and not self.tool and e.button() == QtCore.Qt.LeftButton else None
            if t is not None:
                self.drag = self.boxing = None
                self.text_open.emit(t, None)
                return
            name = self._pat_at(e.position()) if self.editing and not self.tool and e.button() == QtCore.Qt.LeftButton else None
            if name is not None:
                self.drag = self.boxing = None
                self.pattern_open.emit(name)
                return
            super().mouseDoubleClickEvent(e)
            return
        if self.drag is not None:
            self.drag = None
        self._dim_open(self.ties[k])
        self._refresh()

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.RightButton and self.ring is not None:
            self.ring.flick_end(e.globalPosition().toPoint())
            return
        if e.button() == QtCore.Qt.MiddleButton:
            self._pan_from = None
        if e.button() == QtCore.Qt.LeftButton and self.drag is not None:
            self._drag_end(bool(e.modifiers() & QtCore.Qt.ShiftModifier))
        if e.button() == QtCore.Qt.LeftButton and self.boxing is not None:
            self._box_end()

    def leaveEvent(self, _e):
        log.say("pointer leaves the view at", log.pt(self.mouse))
        self.over = None
        if self.img is not None:
            self.img["hover"] = None
            self.img_glass_off()
        self.tile_tip = None
        """The pointer off the view (up to the title, onto the browser): the line being drawn stays where it was,
        with its sizes (it vanished and left its sizes behind: the user); only the magnet hint goes."""
        self.hint = None
        self.update()

    def wheelEvent(self, e):
        """The wheel only zooms, about the pointer (the user: moving the drawing with it was useless); with Shift
        macOS turns the wheel sideways — still a zoom. Pan: the middle button."""
        d = e.angleDelta()
        steps = (d.y() or d.x()) / 120.0
        if steps:
            self.zoom_at(e.position(), 1.15 ** steps)
            if self.img is not None:  # the glass shows the view as it is now
                self._img_track(e.position())

    def event(self, e):
        t = e.type()
        if getattr(self, "tilt_back", None) is not None and not getattr(self, "_remapping", False) and t in (
                QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease, QtCore.QEvent.MouseMove,
                QtCore.QEvent.MouseButtonDblClick, QtCore.QEvent.Wheel):
            at = self.tilt_back.map(e.position())  # the sketch turned in 3D: the pointer back onto its straight-on drawing
            if t == QtCore.QEvent.Wheel:
                m = QtGui.QWheelEvent(at, e.globalPosition(), e.pixelDelta(), e.angleDelta(), e.buttons(), e.modifiers(),
                                      e.phase(), e.inverted())
            else:
                m = QtGui.QMouseEvent(t, at, e.globalPosition(), e.button(), e.buttons(), e.modifiers())
            self._remapping = True
            try:
                return self.event(m)
            finally:
                self._remapping = False
        if e.type() == QtCore.QEvent.KeyPress and e.key() in (QtCore.Qt.Key_Tab, QtCore.Qt.Key_Backtab):
            self.keyPressEvent(e)  # Qt would take Tab for moving the focus: here it holds a size
            return True
        if e.type() == QtCore.QEvent.NativeGesture and e.gestureType() == QtCore.Qt.ZoomNativeGesture:
            self.zoom_at(e.position(), 1.0 + e.value())
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        k = e.key()
        log.say("view key", repr(e.text()) if e.text().strip() else k, "chain", self.cur is not None)
        if k == QtCore.Qt.Key_Escape:  # whatever else it does, Esc always frees the view from the mouse (the user)
            self._pan_from = None
        if self.drag is not None or self.boxing is not None:
            if k == QtCore.Qt.Key_Escape:
                self._hold_cancel()
            return
        if k == QtCore.Qt.Key_Space and self.tool in TRIM_TOOLS:
            self.trim_swap()
            return
        if k == QtCore.Qt.Key_Escape and self.tool == "sk_mirror" and self.mirror_back():
            return
        if k == QtCore.Qt.Key_Escape and self.tool == "sk_pattern" and self.pat is not None:
            self.pattern_done("cancel")
            return
        if self.img is not None and k in (QtCore.Qt.Key_Escape, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.image_key.emit("cancel" if k == QtCore.Qt.Key_Escape else "ok")
            return
        if self.corner_edit is not None and k in (QtCore.Qt.Key_Space, QtCore.Qt.Key_Escape, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            if k == QtCore.Qt.Key_Space:
                self.corner_next()
            else:
                self.corner_quit.emit("cancel" if k == QtCore.Qt.Key_Escape else "ok")
            return
        if k == QtCore.Qt.Key_Space and self.tool in CORNER_TOOLS:
            self.corner_swap()
            return
        if k == QtCore.Qt.Key_Escape and self.tool == "extend" and self.ext is not None:
            self.ext_cancel()
            return
        if k == QtCore.Qt.Key_Space and self.pasting is not None and self.rotating is None and self.mouse is not None:
            self.paste_turn()
            return
        if k == QtCore.Qt.Key_Space and not self.tool and self.rotating is None:
            at = self.tab_point if self.tab_point is not None and self.tab_point < len(self.pts) \
                else self.over[1] if self.over is not None and self.over[0] == "point" else None
            if at is not None and len(self._stack(at)) > 1:
                # points in one place: each Tab goes to the next, until one is moved (the user), pointer there or not
                stack = self._stack(at)
                self.tab_point = stack[(stack.index(at) + 1) % len(stack)]
                if self.over is not None and self.over[0] == "point":
                    self.over = ("point", self.tab_point)
                if self.sel_pts & set(stack):
                    self.sel_pts = (self.sel_pts - set(stack)) | {self.tab_point}
                self.update()
            return
        if self.basing is not None:
            if k == QtCore.Qt.Key_Escape:
                self._base_done()
                return
        if self.rotating is not None:
            if k == QtCore.Qt.Key_Escape:
                self.rot_cancel()
            elif k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and self.rotating["pivot"] is not None:
                self.rot_click()
            elif e.text() and (e.text()[0].isdigit() or e.text()[0] in ".-,") and self.rotating["pivot"] is not None:
                self.box_sw.activate(e.text())
            return
        if self.pasting is not None:
            if k == QtCore.Qt.Key_Escape:
                self._paste_cancel()
            return
        if self.tool == "constraint":
            if k == QtCore.Qt.Key_Escape:
                self._con_back()
            return
        if self.tool == "dimension":
            if k == QtCore.Qt.Key_Escape:
                self._dim_back()
            elif k == QtCore.Qt.Key_Space and self.dim_new is not None:
                new = self.dim_new  # the next kind of it; between two lines, the next of the four angles
                if self._dim_kind(new) in ("dim_ang", "dim_angx"):
                    new["turn"] += 1
                else:
                    new["i"] = (new["i"] + 1) % len(new["kinds"])
                    new["settled"] = True  # the space bar chose: the mouse no longer does
                self._dim_track()
                self._refresh()
            return
        if e.matches(QtGui.QKeySequence.Copy):
            self.copy_chosen()
            return
        if e.matches(QtGui.QKeySequence.Paste):
            self._paste()
            return
        if e.matches(QtGui.QKeySequence.Cut):
            self.copy_chosen(cut=True)
            return
        if self.tool in SPLINE_TOOLS and self.spl_pts:
            if k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                self._spl_finish()
            elif k == QtCore.Qt.Key_Escape:
                self._spl_reset()
                self._spl_prompt()
                self._refresh()
            return
        if k in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace) and self.con_lit:
            self.con_delete()
            return
        if k == QtCore.Qt.Key_Escape and self.con_lit:
            self.con_clear()
            return
        if k in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace) and self.can_delete():
            self._delete()
            return
        if k == QtCore.Qt.Key_Escape and not self.tool and self.cur is None and \
                (self.sel_lines or self.sel_arcs or self.sel_ells or self.sel_spls or self.sel_pts or self.sel_dims
                 or self.sel_places):
            self._clear_choice()
            self.update()
            return
        if self.tool in RECT_NAMES and self._rect_key(e):
            return
        if k == QtCore.Qt.Key_Escape:
            if self.cur is not None:
                self._end_chain()
            elif self.tool:
                self.stop_tool()
            return
        if self.cur is not None and e.text() and (e.text()[0].isdigit() or e.text()[0] in ".-,"):
            self.box_len.activate(e.text())
            self._refresh()
            return
        if self.cur is not None and k in (QtCore.Qt.Key_Tab, QtCore.Qt.Key_Backtab):
            self.box_len.activate()  # the first Tab goes into the length (the user); the next one holds it
            self._refresh()
            return
        if k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and self.cur is not None and self.mouse is not None:
            self.click(self._end())
            return
        super().keyPressEvent(e)

    def resizeEvent(self, _e):
        self._place_boxes()
        self._part_boxes()

    # ----- painting -----
    def _arc_poly(self, c, r, a0, sweep):
        """The arc on the screen. Its points in the sketch are kept until it changes and are turned onto the screen in
        one call (40 circles of 129 points each, worked out point by point at every paint, slowed a drag)."""
        key = (c.x(), c.y(), r, a0, sweep)
        kept = self.__dict__.setdefault("_arc_polys", {})
        poly = kept.get(key)
        if poly is None:
            if len(kept) > 4000:
                kept.clear()
            n = max(8, int(abs(sweep) / TAU * 128))
            poly = kept[key] = QtGui.QPolygonF([_on_circle(c, r, a0 + sweep * k / n) for k in range(n + 1)])
        return self._screen_tf().map(poly)

    def _screen_tf(self):
        """to_screen as one transform."""
        s, mx, h2 = self.scale, self._mid_x(), self.height() / 2
        return QtGui.QTransform(s, 0, 0, -s, mx - self.center.x() * s, h2 + self.center.y() * s)

    def _grid_step(self):
        raw = 14.0 / self.scale
        k = 10 ** math.floor(math.log10(raw))
        for m in (1, 2, 5, 10):
            if m * k >= raw:
                return m * k
        return 10 * k

    def paint_into(self, g):
        """The sketch drawn by the 3D view, on its own painter (the view's spots)."""
        self._into_3d = True
        try:
            self._paint(g)
        finally:
            self._into_3d = False

    def update(self, *a):
        super().update(*a)
        if getattr(self, "after_change", None) is not None:
            self.after_change()  # the 3D view draws the sketch: it is drawn again with it

    def paintEvent(self, _e):
        self._paint(QtGui.QPainter(self))

    def _end_paint(self, g):
        if not getattr(self, "_into_3d", False):
            g.end()

    def _paint(self, g):
        g.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if self.under3d and not getattr(self, "_into_3d", False):
            grad = QtGui.QLinearGradient(0, 0, 0, h)  # only under the browser: the 3D view over the rest draws the sketch
            grad.setColorAt(0, QtGui.QColor(self.p["view_top"]))
            grad.setColorAt(1, QtGui.QColor(self.p["view_bottom"]))
            g.fillRect(QtCore.QRect(0, 0, self.left_inset, h), grad)
            g.end()
            return
        if self.samples:
            self._paint_samples(g, w, h)
        elif not self.under3d:
            grad = QtGui.QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0, QtGui.QColor(self.p["view_top"]))
            grad.setColorAt(1, QtGui.QColor(self.p["view_bottom"]))
            g.fillRect(self.rect(), grad)
        g.setClipRect(self.left_inset, 0, w - self.left_inset, h)  # the sketch and its grid stay in the work field (the user)
        if self.tilt is not None:
            g.setTransform(self.tilt, True)  # the sketch as the 3D view sees it: all of it drawn as before, then turned
        self._paint_images(g)
        if self.editing:
            self._paint_grid(g, w, h)
        o = self.to_screen(P(0, 0))
        sx0, sy0, sx1, sy1 = self._span(w, h)
        for key, row, a, b in (("axis_x", "x", P(sx0, o.y()), P(sx1, o.y())), ("axis_y", "y", P(o.x(), sy0), P(o.x(), sy1))):
            if row in self.origin_off:
                continue
            c = QtGui.QColor(self.p[key])  # the axes: thin and quiet (the user)
            c.setAlphaF(1.0 if self.lit_origin == row else 0.55)
            pen = QtGui.QPen(c, 2 if self.lit_origin == row else 1)
            if self.lit_origin == row:  # dashed like an axis picked for a dimension: no drawn line can be taken for it
                pen = QtGui.QPen(QtGui.QColor(self.p["snap"]), 2.6, QtCore.Qt.CustomDashLine, QtCore.Qt.FlatCap)
                pen.setDashPattern([5, 3])
            pen.setCosmetic(True)
            g.setPen(pen)
            g.setRenderHint(QtGui.QPainter.Antialiasing, False)
            g.drawLine(a, b)
            g.setRenderHint(QtGui.QPainter.Antialiasing, True)
        if self.sketch_off and not self.editing:
            if not self.under3d:
                self._paint_triad(g, h)
            self._end_paint(g)
            return
        geo = QtGui.QColor(self.p["geometry"])
        if not self.editing:
            geo = self.state_ink("done")
        solid = QtGui.QPen(geo, 1.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap)

        mirrored = self._mir_points()

        def pen(state, shape=()):  # a line is coloured by how far it is held; finished, a held one takes the finished colour
            ink = self.state_ink("done" if state == "full" and not self.editing else state)
            if mirrored[0] and set(shape) & mirrored[0] and not set(shape) & mirrored[1]:
                ink = self._mir_ink(ink)  # a mirror copy: a shade off, its state still reads
            return QtGui.QPen(ink, 1.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap)
        faint = QtGui.QColor(self.p["construction"])
        faint.setAlphaF(0.45)
        dashed = QtGui.QPen(faint, 1.2, QtCore.Qt.CustomDashLine, QtCore.Qt.FlatCap) if self.dim_constr else \
            QtGui.QPen(QtGui.QColor(self.p["construction"]), 1.4, QtCore.Qt.CustomDashLine, QtCore.Qt.FlatCap)
        dashed.setDashPattern([6, 4])
        run, run_pen = [], None  # lines one after another with the same pen: drawn in one call, in the same order
        tf = self._screen_tf()
        for i, (ia, ib) in enumerate(self.lines):
            pn = dashed if self.is_construction(i) else pen(self.line_state.get(i, "free"), (ia, ib))
            if run and pn != run_pen:
                g.setPen(run_pen)
                g.drawLines(run)
                run = []
            run_pen = pn
            run.append(tf.map(QtCore.QLineF(self.pts[ia], self.pts[ib])))
        if run:
            g.setPen(run_pen)
            g.drawLines(run)
        g.setBrush(QtCore.Qt.NoBrush)
        for i in range(len(self.arcs)):
            g.setPen(dashed if self.is_arc_construction(i) else pen(self.arc_state.get(i, "free"), self.arcs[i]))
            c, r, a0, sw = self.arc_shape(i)
            g.drawPolyline(self._arc_poly(c, r, a0, sw))
        for i in range(len(self.ells)):
            g.setPen(dashed if i < len(self.ell_constr) and self.ell_constr[i] else pen(self.ell_state.get(i, "free"), self.ells[i]))
            g.drawPolyline(self._ell_poly_of(i))
            if self._ell_cut(i) is not None:  # the rest of its ellipse, dashed: its axes' ends lie there
                t0, sw = self._ell_range(i)
                g.setPen(dashed)
                g.drawPolyline(self._ell_poly(*self.ell_shape(i), t0 + sw, TAU - sw))
        for i in range(len(self.spls)):
            if i < len(self.spl_constr) and self.spl_constr[i]:
                g.setPen(dashed)
            else:
                g.setPen(pen(self.spl_state.get(i, "free"), self.spls[i][1]))
            g.drawPolyline(QtGui.QPolygonF([self.to_screen(q) for q in self.spl_poly(i)]))
        if self.editing:
            self._paint_spl_nets(g, dashed)
        for i in range(len(self.spls)):
            if self.comb_on(i):
                ink = self.state_ink(self.spl_state.get(i, "free"))
                ink.setAlphaF(.55)
                self._paint_comb(g, i, ink)
        if self.editing:  # normal lines lying on each other: the shared piece in bright red, seen at once (the user)
            g.setPen(QtGui.QPen(QtGui.QColor("#ff2a2a"), 3.2, QtCore.Qt.SolidLine, QtCore.Qt.FlatCap))
            for a, b in self.overlaps():
                g.drawLine(self.to_screen(a), self.to_screen(b))
            for c, r, a0, sw in self.arc_overlaps():
                g.drawPolyline(self._arc_poly(c, r, a0, sw))
        if self.editing:
            self._paint_texts(g)
            self._paint_tiles(g)
            self._paint_con_lit(g)
            self._paint_host_lit(g)
            self._paint_dims(g)
            self._paint_img_marks(g)
            if self.tile_tip is not None and not self.tool and not self.marks_hidden:  # above all of them
                self._paint_tile_tip(g)
        if self.editing and (self.sel_lines or self.sel_arcs or self.sel_ells or self.sel_spls or self.sel_pts or self.boxing or self.con_picks
                             or self.con_ask):
            self._paint_chosen(g)
        if self.tool in TRIM_TOOLS:
            self._paint_trim(g)
        if self.tool in CORNER_TOOLS:
            self._paint_corner(g)
        if self.tool == "sk_offset":
            self._paint_offset(g)
        if self.tool == "sk_mirror":
            self._paint_mirror(g)
        if self.tool == "sk_pattern":
            self._paint_pattern(g)
        if self.corner_edit is not None:
            self._paint_corner_edit(g)
        if self.editing and self.over is not None:
            self._paint_over(g)
        elif self.tool == "circle_tangent" and self.mouse is not None and len(self.tc_picks) < 3:
            self._paint_curve(g, self._tc_axis(self.to_screen(self.mouse)))
        if self.mouse is not None and (self.pasting is not None or (self.rotating is not None and self.rotating["pivot"] is not None)):
            self._paint_paste(g)
        if self.rotating is not None:
            self._paint_rot(g)
        if self.boxing is not None:
            self._paint_box(g)
        if self.tool in RECT_NAMES and self.rect_pts and self.mouse is not None:
            pen = QtGui.QPen(QtGui.QColor(self.p["construction"] if self.construction else self.p["accent"]), 1.6)
            if self.construction:
                pen.setStyle(QtCore.Qt.CustomDashLine)
                pen.setDashPattern([6, 4])
            g.setPen(pen)
            g.setBrush(QtCore.Qt.NoBrush)
            pts = self._rect_corners()
            arc = self._arc_now() if self.tool in ARC_TOOLS else None
            rounded = self._rounded_now() if self.tool == "rect_rounded" else None
            if self.tool in SLOTS:
                self._paint_slot(g, pen)
                g.setPen(pen)
            if self.tool == "polygon":
                self._paint_poly(g, pen)
                g.setPen(pen)
            if self.tool == "text" and self.text_frame != "rect" and pts:
                c_ = (pts[0] + pts[2]) * 0.5
                u, w_ = (pts[1] - pts[0]) * 0.5, (pts[2] - pts[1]) * 0.5
                if self.text_frame == "circle":
                    side = min(_len(u), _len(w_))
                    u, w_ = u / (_len(u) or 1.0) * side, w_ / (_len(w_) or 1.0) * side
                g.setPen(QtGui.QPen(pen.color(), 1, QtCore.Qt.DashLine))
                g.drawPolyline(self._ell_poly(c_, u, w_))
                g.setPen(pen)
            if self.tool in ELLIPSE_TOOLS:
                pts = None  # its frame is dashed, drawn here
                now = self._ell_now()
                cut = self._cut_now() if self.tool == "arc_ellipse" else None
                if self.tool in ("ellipse", "arc_ellipse") and self._rect_stage() == 1:
                    g.drawLine(self.to_screen(self.rect_pts[0]), self.to_screen(self._rect_side_end()))
                elif cut is not None:
                    c_, u, v, t0, sweep = cut
                    lo, sw = (t0, sweep) if sweep > 0 else (t0 + sweep, -sweep)
                    g.drawPolyline(self._ell_poly(c_, u, v, lo, sw))
                    g.setPen(QtGui.QPen(pen.color(), 1, QtCore.Qt.DashLine))
                    g.drawPolyline(self._ell_poly(c_, u, v, lo + sw, TAU - sw))
                elif now is not None:
                    c, a, b, frame = now
                    g.drawPolyline(self._ell_poly(c, a - c, b - c))
                    if frame:
                        g.setPen(QtGui.QPen(pen.color(), 1, QtCore.Qt.DashLine))
                        g.drawPolygon(QtGui.QPolygonF([self.to_screen(q) for q in frame]))
            if self.tool == "circle_tangent":
                for what in self.tc_picks:
                    if what[0] != "point":
                        self._paint_curve(g, what)
                g.setPen(pen)
                g.setBrush(QtCore.Qt.NoBrush)
            if self.tool == "arc_tangent":
                self._paint_curve(g, self._tan["src"] if self._tan else self.tan_src)
                if self._tan and (self._tan["pick"] or self._tan["preview"]):
                    self._paint_curve(g, self._tan["curve"])
                g.setPen(pen)
                g.setBrush(QtCore.Qt.NoBrush)
            if arc is not None:
                c, r, a0, sw = arc
                g.drawPolyline(self._arc_poly(c, r, a0, sw))
                faint = QtGui.QPen(pen.color(), 1, QtCore.Qt.DotLine)
                g.setPen(faint)  # the centre and the radius it hangs on, light
                g.drawLine(self.to_screen(c), self.to_screen(_on_circle(c, r, a0)))
                g.drawLine(self.to_screen(c), self.to_screen(_on_circle(c, r, a0 + sw)))
            elif rounded is not None:
                x0, y0, x1, y1, r = rounded
                path = QtGui.QPainterPath()
                for k, (cx, cy, start) in enumerate(((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90),
                                                     (x0 + r, y0 + r, 180))):
                    poly = self._arc_poly(P(cx, cy), r, math.radians(start), math.pi / 2)
                    if k == 0:
                        path.moveTo(poly[0])
                    for q in poly:
                        path.lineTo(q)
                path.closeSubpath()
                g.drawPath(path)
            elif pts:
                g.drawPolygon(QtGui.QPolygonF([self.to_screen(p) for p in pts]))
            elif self.tool in ("arc", "arc_center", *SLOT_ARC) and self._rect_stage() == 2:  # no arc yet (on one line): its chord
                g.drawLine(self.to_screen(self.rect_pts[0]), self.to_screen(self.rect_pts[1]))
            elif self.tool != "arc_tangent" and self.tool not in SLOT_LINE and self.tool != "polygon":  # 3-point ways, the first side being pulled
                g.drawLine(self.to_screen(self.rect_pts[0]), self.to_screen(self._rect_side_end()))
        if self.cur is not None and self.mouse is not None:
            rubber = QtGui.QPen(QtGui.QColor(self.p["construction"] if self.construction else self.p["accent"]), 1.6)
            if self.construction:
                rubber.setStyle(QtCore.Qt.CustomDashLine)
                rubber.setDashPattern([6, 4])
            if self.par is not None:
                self._paint_curve(g, ("line", self.par))
            g.setPen(rubber)
            g.drawLine(self.to_screen(self.pts[self.cur]), self.to_screen(self._end()))
        if self.editing:
            g.setPen(QtCore.Qt.NoPen)
            g.setBrush(geo)
            centres = self.centres()
            rims = self.rims()
            ghost = self._ghost_points()
            for k, p in enumerate(self.pts):
                if k not in centres and k not in rims:
                    ink = QtGui.QColor(self.state_ink(self.pt_state.get(k, "free")))
                    if k in ghost:
                        ink.setAlphaF(0.45)
                    g.setBrush(ink)
                    g.drawEllipse(self.to_screen(p), 2.5, 2.5)
            g.setPen(QtGui.QPen(geo, 1.2))
            for k in centres:  # an arc's centre: a small cross
                s_ = self.to_screen(self.pts[k])
                g.drawLine(s_ + P(-4, 0), s_ + P(4, 0))
                g.drawLine(s_ + P(0, -4), s_ + P(0, 4))
            g.setPen(QtCore.Qt.NoPen)
            g.setPen(QtGui.QPen(QtGui.QColor(self.p["axis_z"]), 1.4))
            g.setBrush(QtCore.Qt.NoBrush)
            if "centre" not in self.origin_off:
                g.drawEllipse(o, 5.5 if self.lit_origin == "centre" else 3.5, 5.5 if self.lit_origin == "centre" else 3.5)
            if self.cur is not None:
                g.setPen(QtCore.Qt.NoPen)
                g.setBrush(QtGui.QColor(self.p["start"]))
                g.drawEllipse(self.to_screen(self.pts[self.cur]), 3.2, 3.2)
            self._paint_taken(g)
            self._paint_hint(g)
        if not self.under3d:  # the 3D view has its own corner axes
            self._paint_triad(g, h)
        if self.samples:
            self._paint_sample_numbers(g, w, h)
        self._end_paint(g)

    def _zones(self, w, h):
        x0 = 0  # side by side, full height, zone 1 from the left edge — under the browser too (the user)
        zw = (w - x0) / len(self.samples)
        return [QtCore.QRectF(x0 + k * zw, 0, zw, h) for k in range(len(self.samples))]

    def _paint_samples(self, g, w, h):
        """Choosing a look on the real screen: each zone its own gradient, a big number to name it (the user)."""
        for r, (top, bottom) in zip(self._zones(w, h), self.samples):
            grad = QtGui.QLinearGradient(0, r.top(), 0, r.bottom())
            grad.setColorAt(0, QtGui.QColor(top))
            grad.setColorAt(1, QtGui.QColor(bottom))
            g.fillRect(r, grad)

    def _paint_sample_numbers(self, g, w, h):
        f = QtGui.QFont("Tahoma")
        f.setPixelSize(64)
        f.setBold(True)
        g.setFont(f)
        for k, r in enumerate(self._zones(w, h), 1):
            g.setPen(QtGui.QColor(self.p["text"]))
            g.drawText(r, QtCore.Qt.AlignCenter, str(k))
            g.setPen(QtGui.QPen(QtGui.QColor(self.p["edge_line"]), 1))
            g.setBrush(QtCore.Qt.NoBrush)
            g.drawRect(r)

    def _span(self, w, h):
        """(x0, y0, x1, y1): the straight-on drawing that covers the screen; the sketch turned in 3D: all that the
        turn brings onto the screen, so the grid and the axes reach every edge (the user)."""
        if self.tilt is None:
            return 0.0, 0.0, float(w), float(h)
        if self.tilt_back is None:  # seen edge on
            return 0.0, 0.0, float(w), float(h)
        corners = [self.tilt_back.map(P(x, y)) for x in (self.left_inset, w) for y in (0, h)]
        return (min(c.x() for c in corners), min(c.y() for c in corners),
                max(c.x() for c in corners), max(c.y() for c in corners))

    def _paint_grid(self, g, w, h):
        """Seen straight on, the grid is kept as a picture until the view, the size or the colours change: a point
        dragged does not move it (drawn line by line it was a third of a paint). Turned in 3D: drawn as before."""
        if self.tilt is not None:
            return self._paint_grid_lines(g, w, h)
        dev = g.device()
        dpr = dev.devicePixelRatioF() if dev is not None else 1.0
        key = (w, h, dpr, self.scale, self.center.x(), self.center.y(), self._mid_x(), self.p["grid"], self.p["grid_major"])
        kept = getattr(self, "_grid_kept", None)
        if kept is None or kept[0] != key:
            pm = QtGui.QPixmap(max(1, round(w * dpr)), max(1, round(h * dpr)))
            pm.setDevicePixelRatio(dpr)
            pm.fill(QtCore.Qt.transparent)
            gp = QtGui.QPainter(pm)
            gp.setRenderHint(QtGui.QPainter.Antialiasing)
            self._paint_grid_lines(gp, w, h)
            gp.end()
            kept = self._grid_kept = (key, pm)
        g.drawPixmap(0, 0, kept[1])

    def _paint_grid_lines(self, g, w, h):
        step = self._grid_step()
        x0, y0, x1, y1 = self._span(w, h)
        a, b = self.to_model(P(x0, y1)), self.to_model(P(x1, y0))
        while (b.x() - a.x()) / step + (b.y() - a.y()) / step > 1600:  # seen very slant: fewer lines, still the grid
            step *= 5
        minor, major = QtGui.QColor(self.p["grid"]), QtGui.QColor(self.p["grid_major"])
        # minor and major lines each in one call (a line at a time cost a third of a paint); the vertical ones first,
        # as before, so the level ones still lie over them
        i0, i1 = math.floor(a.x() / step), math.ceil(b.x() / step)
        ups = ([], [])
        for i in range(i0, i1 + 1):
            x = self.to_screen(P(i * step, 0)).x()
            ups[i % 5 == 0].append(QtCore.QLineF(x, y0, x, y1))
        j0, j1 = math.floor(a.y() / step), math.ceil(b.y() / step)
        levels = ([], [])
        for j in range(j0, j1 + 1):
            y = self.to_screen(P(0, j * step)).y()
            levels[j % 5 == 0].append(QtCore.QLineF(x0, y, x1, y))
        for group in (ups, levels):
            for k, ink in ((0, minor), (1, major)):
                if group[k]:
                    g.setPen(QtGui.QPen(ink, 1))
                    g.drawLines(group[k])

    def _paint_curve(self, g, src):
        """The line or arc something now depends on, lit (Parallel, Tangent Arc)."""
        if src is None:
            return
        g.setPen(QtGui.QPen(QtGui.QColor(self.p["snap"]), 2.2))
        g.setBrush(QtCore.Qt.NoBrush)
        if src[0] == "ell":
            g.drawPolyline(self._ell_poly_of(src[1]))
        elif src[0] == "axis":
            o = self.to_screen(P(0, 0))
            if src[1] == "x":
                g.drawLine(P(0, o.y()), P(self.width(), o.y()))
            else:
                g.drawLine(P(o.x(), 0), P(o.x(), self.height()))
        elif src[0] == "line":
            ia, ib = self.lines[src[1]]
            g.drawLine(self.to_screen(self.pts[ia]), self.to_screen(self.pts[ib]))
        else:
            c, r, a0, sw = self.arc_shape(src[1])
            g.drawPolyline(self._arc_poly(c, r, a0, sw))

    def _paint_chosen(self, g):
        g.setPen(QtGui.QPen(QtGui.QColor(self.p["snap"]), 2.4, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        g.setBrush(QtCore.Qt.NoBrush)
        lines, arcs, pts = self.sel_lines, self.sel_arcs, self.sel_pts  # the box lights what it will take at once
        if self.boxing is not None:
            lines, arcs = lines | self.boxing["lines"], arcs | self.boxing["arcs"]
            pts = pts | self.boxing.get("pts", set())
        for kind, i in self.con_picks + self.con_ask:
            if kind in ("axis", "origin"):
                continue
            if kind == "spl":
                g.drawPolyline(QtGui.QPolygonF([self.to_screen(q) for q in self.spl_poly(i)]))
            elif kind == "point":
                pts = pts | {i}
            elif kind == "line":
                lines = lines | {i}
            elif kind == "arc":
                arcs = arcs | {i}
            else:
                g.drawPolyline(self._ell_poly_of(i))
        if self.boxing:
            pts = pts | self._loose_in(self.boxing["at"], self.boxing["to"])
        for k in pts:
            g.drawEllipse(self.to_screen(self.pts[k]), 5, 5)
        if self.tab_point is not None and self.tab_point < len(self.pts):  # and the shape of the one Tab chose
            k = self.tab_point
            lines, arcs = lines | {i for i, ln in enumerate(self.lines) if k in ln}, \
                arcs | {i for i, arc in enumerate(self.arcs) if k in arc}
        for i in lines:
            a, b = self.lines[i]
            g.drawLine(self.to_screen(self.pts[a]), self.to_screen(self.pts[b]))
        for i in arcs:
            c, r, a0, sw = self.arc_shape(i)
            g.drawPolyline(self._arc_poly(c, r, a0, sw))
        for i in self.sel_ells | (self.boxing.get("ells", set()) if self.boxing else set()):
            g.drawPolyline(self._ell_poly_of(i))
        for i in self.sel_spls | (self.box_spls if self.boxing else set()):
            if i < len(self.spls):
                g.drawPolyline(QtGui.QPolygonF([self.to_screen(q) for q in self.spl_poly(i)]))

    def _paint_paste(self, g):
        c = self.clip
        g.setPen(QtGui.QPen(QtGui.QColor(self.p["accent"]), 1.6))
        g.setBrush(QtCore.Qt.NoBrush)
        pts = self._paste_points(self.paste_at())
        for a, b in c["lines"]:
            g.drawLine(self.to_screen(pts[a]), self.to_screen(pts[b]))
        for ic, ia, ib in c["arcs"]:
            r, a0, sw = _arc_of(pts[ic], pts[ia], pts[ib])
            g.drawPolyline(self._arc_poly(pts[ic], r, a0, sw))
        for ic, ia, ib in c["ells"]:
            u, v = pts[ia] - pts[ic], pts[ib] - pts[ic]
            cut = next((t["pts"][:2] for t in c["ties"] if t["kind"] == "ell_cut" and t["pts"][2:] == [ic, ia, ib]), None)
            t0 = _ell_t(pts[ic], u, v, pts[cut[0]]) if cut else 0.0
            g.drawPolyline(self._ell_poly(pts[ic], u, v, t0, (_ell_t(pts[ic], u, v, pts[cut[1]]) - t0) % TAU if cut else TAU))

    def _paint_box(self, g):
        """Left to right a solid box: what lies inside. Right to left a dashed one: what it touches."""
        b = self.boxing
        accent = QtGui.QColor(self.p["accent"])
        pen = QtGui.QPen(accent, 1)
        if b["touch"]:
            pen.setStyle(QtCore.Qt.CustomDashLine)
            pen.setDashPattern([5, 4])
        g.setPen(pen)
        fill = QtGui.QColor(accent)
        fill.setAlphaF(0.10)
        g.setBrush(fill)
        g.drawRect(QtCore.QRectF(self.to_screen(b["at"]), self.to_screen(b["to"])).normalized())

    def _paint_over(self, g):
        """What the pointer would take with no tool running, lit."""
        kind, i = self.over
        if kind == "dim":
            return  # it lights in its own colour with the others
        snap = QtGui.QColor(self.p["snap"])
        g.setPen(QtGui.QPen(snap, 2.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        g.setBrush(QtCore.Qt.NoBrush)
        if kind == "line":
            a, b = self.lines[i]
            g.drawLine(self.to_screen(self.pts[a]), self.to_screen(self.pts[b]))
        elif kind == "arc":
            c, r, a0, sw = self.arc_shape(i)
            g.drawPolyline(self._arc_poly(c, r, a0, sw))
        elif kind == "ell":
            g.drawPolyline(self._ell_poly_of(i))
        elif kind == "spl":
            g.drawPolyline(QtGui.QPolygonF([self.to_screen(q) for q in self.spl_poly(i)]))
        else:
            g.drawEllipse(self.to_screen(self.pts[i]), 4.5, 4.5)
            if len(self._stack(i)) > 1:  # two in one place: the line it belongs to shows which one it is
                for ia, ib in self.lines:
                    if i in (ia, ib):
                        g.drawLine(self.to_screen(self.pts[ia]), self.to_screen(self.pts[ib]))
                for k, arc in enumerate(self.arcs):
                    if i in arc:
                        c, r, a0, sw = self.arc_shape(k)
                        g.drawPolyline(self._arc_poly(c, r, a0, sw))

    def _mark_places(self):
        """(name, what it sits by): ("line", a, b) or ("point", k), one per thing it holds."""
        out = []
        for k_, (kind, line, _end, _ref) in enumerate(self.relations):
            if line is not None and line < len(self.lines) and kind in ("h", "v"):
                out.append(("Horizontal" if kind == "h" else "Vertical", ("line",) + tuple(self.lines[line]), ("rel", k_)))
        for t in self.ties:
            name, pts = t.get("mark"), t["pts"]
            if not name:
                continue
            if len(pts) == 4 and t["kind"] in ("con_par", "con_perp", "con_col", "con_eq"):
                out += [(name, ("line", pts[0], pts[1]), ("tie", id(t))), (name, ("line", pts[2], pts[3]), ("tie", id(t)))]
            elif t["kind"] in ("same_x", "same_y"):
                on_line = tuple(pts) in self.lines or tuple(reversed(pts)) in self.lines
                out += [(name, ("line", pts[0], pts[1]), ("tie", id(t)))] if on_line else [(name, ("point", k), ("tie", id(t))) for k in pts]
            elif t.get("mirror") or t.get("pat"):
                continue  # a mirror copy or a pattern brings no marks: they would fill the sketch
            elif t["kind"] == "con_sym":
                out += [(name, ("point", pts[0]), ("tie", id(t))), (name, ("point", pts[1]), ("tie", id(t)))]
            elif t["kind"] in ("con_on_line", "con_touch", "coin", "fix"):
                out.append((name, ("point", pts[0]), ("tie", id(t))))
            elif t["kind"] == "con_axis":
                out.append((name, ("axis", pts[0], t["ref"]), ("tie", id(t))))
            elif t["kind"] == "con_on_ell":
                out.append((name, ("point", pts[0]), ("tie", id(t))))
            elif t["kind"] in ("con_ell_line", "con_ell_arc"):
                out.append((name, (t["kind"], *pts), ("tie", id(t))))
            elif t["kind"] in ("con_on_arc", ):
                out.append((name, ("point", pts[2]), ("tie", id(t))))
            elif t["kind"] == "con_dist":
                out.append((name, ("foot", pts[0], pts[2], pts[3]), ("tie", id(t))))  # where the arc touches the line (the user)
            elif t["kind"] == "con_apart":
                out.append((name, ("meet", pts[0], pts[1], pts[2]), ("tie", id(t))))  # where the two arcs touch
            elif t["kind"] in ("con_centre", "con_eq_r"):
                out.append((name, ("point", pts[0]), ("tie", id(t))))
            elif t["kind"] == "con_circles":
                out.append((name, ("point", pts[0]), ("tie", id(t))))
        return out

    def _paint_tiles(self, g):
        px = self.mark_px
        gap = px / 2 + 6
        taken = {}
        self.tiles = []
        self.tile_owner = []
        if self.marks_hidden:  # nothing painted, nothing to point at or click
            self.tile_tip = None
            return
        ink = QtGui.QColor("#000000" if self.p["name"] == "light" else "#ffffff")
        bg = QtGui.QColor(self.p["hint_bg"])
        bg.setAlpha(235)
        edge = QtGui.QColor(self.p["edge_line"])
        for name, place, owner in self._mark_places():
            n = taken.get(place, 0)
            taken[place] = n + 1
            if place[0] == "line":
                a, b = self.to_screen(self.pts[place[1]]), self.to_screen(self.pts[place[2]])
                u = b - a
                d = _len(u) or 1.0
                u /= d
                c = (a + b) * 0.5 + P(-u.y(), u.x()) * gap + u * (n * (px + 3))  # outside a shape drawn counterclockwise
            elif place[0] == "foot":  # the place on the line nearest the arc's centre
                c0, a, b = [self.pts[k] for k in place[1:]]
                u = b - a
                t = QtCore.QPointF.dotProduct(c0 - a, u) / (QtCore.QPointF.dotProduct(u, u) or 1.0)
                c = self.to_screen(a + u * t) + P(gap + n * (px + 3), -gap)
            elif place[0] == "con_ell_line":  # where the line touches the ellipse
                c0, ea, eb, a, b = [self.pts[k] for k in place[1:]]
                u, v, d = ea - c0, eb - c0, b - a
                n = P(-d.y(), d.x()) / (_len(d) or 1.0)
                nu, nv = QtCore.QPointF.dotProduct(n, u), QtCore.QPointF.dotProduct(n, v)
                side = 1.0 if QtCore.QPointF.dotProduct(n, a - c0) >= 0 else -1.0
                c = self.to_screen(c0 + (u * nu + v * nv) / (math.hypot(nu, nv) or 1.0) * side)
                c += P(gap, -gap)
            elif place[0] == "con_ell_arc":
                c0, _on, ec, ea, eb = [self.pts[k] for k in place[1:]]
                u, v = ea - ec, eb - ec
                t_ = sk.ell_nearest(ec.x(), ec.y(), u.x(), u.y(), v.x(), v.y(), c0.x(), c0.y())[0]
                c = self.to_screen(_ell_at(ec, u, v, t_)) + P(gap, -gap)
            elif place[0] == "axis":  # on the axis, by the circle's centre
                c0 = self.pts[place[1]]
                c = self.to_screen(P(c0.x(), 0) if place[2] == "x" else P(0, c0.y())) + P(gap + n * (px + 3), -gap)
            elif place[0] == "meet":  # on the first arc, towards the second's centre
                c0, on, c2 = [self.pts[k] for k in place[1:]]
                d = c2 - c0
                c = self.to_screen(c0 + d / (_len(d) or 1.0) * _len(on - c0)) + P(gap + n * (px + 3), -gap)
            else:
                c = self.to_screen(self.pts[place[1]]) + P(gap + n * (px + 3), -gap)
            r = QtCore.QRectF(c.x() - px / 2, c.y() - px / 2, px, px)
            g.setPen(QtGui.QPen(edge, 1))
            g.setBrush(bg)
            g.drawRoundedRect(r, px * .2, px * .2)
            _mark_glyph(g, name, c, px * .32, ink, max(1.1, px / 15))
            self.tiles.append((r, name))
            self.tile_owner.append(owner)

    def _paint_tile_tip(self, g):
        """The name of the constraint, for those who see poorly: orange on black, black on white (the user)."""
        r, name = self.tile_tip
        light = self.p["name"] == "light"
        f = QtGui.QFont("Tahoma")
        f.setPixelSize(max(11, self.drawing_px))
        g.setFont(f)
        fm = QtGui.QFontMetrics(f)
        box = QtCore.QRectF(r.right() + 6, r.top() - 2, fm.horizontalAdvance(name) + 14, fm.height() + 6)
        g.setPen(QtGui.QPen(QtGui.QColor("#000000" if light and not self.colors.get("snap") else self.p["snap"]), 1))
        g.setBrush(QtGui.QColor("#ffffff" if light else "#000000"))
        g.drawRoundedRect(box, 3, 3)
        g.setPen(QtGui.QColor("#000000" if light and not self.colors.get("snap") else self.p["snap"]))
        g.drawText(box, QtCore.Qt.AlignCenter, name)

    def _paint_taken(self, g):
        g.setPen(QtGui.QPen(QtGui.QColor(self.p["text_dim"]), 1.2))
        g.setBrush(QtCore.Qt.NoBrush)
        for k in self.taken:
            s = self.to_screen(self._point_at(k))
            g.drawPolygon(QtGui.QPolygonF([s + P(0, -5), s + P(5, 0), s + P(0, 5), s + P(-5, 0)]))

    def _hint_rect(self, at, w, h):
        """The hint's label: below right of its point, or the first free side: never under a size's box (the user)."""
        boxes = [QtCore.QRectF(b.geometry()) for b in (*self.shape_boxes(), self.box_len, self.box_ang) if b.isVisible()]
        tries = [QtCore.QRectF(at.x() + dx, at.y() + dy, w, h) for dx, dy in
                 ((11, 9), (-11 - w, 9), (11, -9 - h), (-11 - w, -9 - h))]
        view = QtCore.QRectF(self.rect().adjusted(self.left_inset, 0, 0, 0))
        self.hint_rect = next((r for r in tries if view.contains(r) and not any(r.intersects(b) for b in boxes)), tries[0])
        return self.hint_rect

    def _paint_hint(self, g):
        hnt = self.hint
        if hnt is None or self.mouse is None:
            return
        snap = QtGui.QColor(self.p["snap"])
        dash = QtGui.QPen(QtGui.QColor(snap.red(), snap.green(), snap.blue(), 170), 1, QtCore.Qt.CustomDashLine)
        dash.setDashPattern([4, 4])
        g.setPen(dash)
        for a, b in hnt["guides"]:
            g.drawLine(self.to_screen(a), self.to_screen(b))
        at, ms = self.to_screen(hnt["at"]), self.to_screen(self.mouse)
        if _len(at - ms) > 2:
            g.drawLine(ms, at)
        g.setPen(QtGui.QPen(snap, 1.5))
        g.setBrush(QtCore.Qt.NoBrush)
        g.drawEllipse(at, 5, 5)
        f = QtGui.QFont("Tahoma")
        f.setPixelSize(max(10, self.drawing_px - 1))  # Settings ▸ Text Size ▸ Drawing
        g.setFont(f)
        fm = QtGui.QFontMetrics(f)
        r = self._hint_rect(at, fm.horizontalAdvance(hnt["label"]) + 12, fm.height() + 4)
        g.setPen(QtGui.QPen(snap, 1))
        g.setBrush(QtGui.QColor(self.p["hint_bg"]))
        g.drawRect(r)
        g.setPen(snap)
        g.drawText(r, QtCore.Qt.AlignCenter, hnt["label"])

    def _paint_triad(self, g, h):
        o = P(self.width() - 46, h - 40)  # the bottom right corner, as in 3D (the user)
        for vec, key, name in ((P(26, 0), "axis_x", "X"), (P(0, -26), "axis_y", "Y")):
            c = QtGui.QColor(self.p[key])
            g.setPen(QtGui.QPen(c, 1.6))
            g.drawLine(o, o + vec)
            g.drawText(o + vec + (P(4, 4) if name == "X" else P(-3, -4)), name)
        g.setPen(QtCore.Qt.NoPen)
        g.setBrush(QtGui.QColor(self.p["axis_z"]))
        g.drawEllipse(o, 3, 3)


CURSOR_INK = "#2f7dff"  # the pointer's colour as it comes, on both themes (the user, 2026-11-11)
CURSOR_SHAPES = ("large", "small", "arrow")  # Settings ▸ Sketch Colors ▸ Cursor Shape; "large" as it comes


def _cross_cursor(ink, shape="small"):
    """The pointer over the view, by Settings ▸ Sketch Colors (the user, 2026-11-11: "a bit pale" on both themes):
    "large": 33 px, one 2 px line of `ink`, no rim (as it was here); "small": 17 px, a thin line of `ink` in a dark
    rim, so it shows on the sketch's view and on the part alike; "arrow": the system's own arrow (`ink` is not used).
    Drawn at the screen's own density, so it is sharp on Retina. (The new program's, commit d482dbb.)"""
    if shape == "arrow":
        return QtGui.QCursor(QtCore.Qt.ArrowCursor)
    large = shape == "large"
    n, c, gap, edge = (33, 16.0, 3, 2.0) if large else (17, 8.5, 2, 1.5)
    screen = QtGui.QGuiApplication.primaryScreen()
    dpr = max(1.0, screen.devicePixelRatio() if screen is not None else 1.0)
    pm = QtGui.QPixmap(round(n * dpr), round(n * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(QtCore.Qt.transparent)
    g = QtGui.QPainter(pm)
    g.setRenderHint(QtGui.QPainter.Antialiasing, False)
    pens = ((QtGui.QColor(ink), 2, QtCore.Qt.FlatCap),) if large else \
        ((QtGui.QColor(0, 0, 0, 235), 3, QtCore.Qt.SquareCap), (QtGui.QColor(ink), 1, QtCore.Qt.FlatCap))
    for colour, width, cap in pens:
        g.setPen(QtGui.QPen(colour, width, QtCore.Qt.SolidLine, cap))
        for a, b in (((c, edge), (c, c - gap)), ((c, c + gap), (c, n - edge)), ((edge, c), (c - gap, c)), ((c + gap, c), (n - edge, c))):
            g.drawLine(QtCore.QPointF(a[0], a[1]), QtCore.QPointF(b[0], b[1]))
    g.end()
    return QtGui.QCursor(pm, int(c), int(c))
