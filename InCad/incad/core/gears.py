"""Gear geometry (Inventor's spur / helical gear and worm generators, as part features: incad.features.gear).

Gears: an involute profile (FreeCAD's own generator, PartDesign's fcgear) in the transverse plane — for a helical
gear the module and pressure angle given are the normal ones, the profile uses the transverse ones — extruded along
+Z, or for a helical gear swept along +Z with a helix as auxiliary spine, so the section turns evenly with height
(one exact sweep, no Booleans). Worms: Archimedean (ZA, straight flanks in the axial section). Each radius r of a
thread lies at the angle ±2π·w(r)/lead from the thread's middle, w(r) being the half-width of the axial profile
there, so the worm's cross-section is drawn exactly and twisted with the lead. Every solid stands on z = 0, axis +Z.

Sizes (module m, teeth z, helix angle β): pitch Ø d = m·z / cos β, tip Ø d + 2m, root Ø d − 2.5m. A worm wheel is
a helical gear with β = the worm's lead angle γ = atan(starts·m / d₁), the same hand, transverse module = the
worm's axial module."""
import math

import FreeCAD as App
import Part

V = App.Vector
MIN_TEETH = 6
ADDENDUM, DEDENDUM = 1.0, 1.25  # × module: the standard tooth (ISO 53)


def _generator():
    try:
        from fcgear import fcgear, involute
    except ImportError:  # FreeCAD puts Mod/PartDesign on the path; a bare engine may not
        import os
        import sys
        sys.path.append(os.path.join(App.getHomePath(), "Mod", "PartDesign"))
        from fcgear import fcgear, involute
    return fcgear, involute


# ----- sizes -----
def pitch_diameter(module, teeth, helix=0.0):
    return module * teeth / math.cos(math.radians(helix))


def tip_diameter(module, teeth, helix=0.0):
    return pitch_diameter(module, teeth, helix) + 2 * ADDENDUM * module


def root_diameter(module, teeth, helix=0.0):
    return pitch_diameter(module, teeth, helix) - 2 * DEDENDUM * module


MODULES_1 = (0.05, 0.06, 0.08, 0.1, 0.12, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0,
             5.0, 6.0, 8.0, 10.0, 12.0, 16.0, 20.0, 25.0, 32.0, 40.0, 50.0)  # ISO 54 / GOST 9563, the first series
SUGGEST_MODULES = tuple(m for m in reversed(MODULES_1) if m <= 2.0)  # from 2 down (the user, 2026-11-27)
UNDERCUT_TEETH = 17  # below this a 20° gear cut by a rack has its tooth root undercut
SUGGEST_MAX_TEETH = 400


def suggest(ratio, want, helix=0.0, step=0):
    """The pair that holds Ratio and comes nearest a wanted centre distance at one standard module (the user,
    2026-11-27; the same as ProstoCAD Next's gear_solid.suggest for an outside pair): module 2 first, each `step` the
    next one down the first series (ISO 54); whole teeth, the Ratio exact when whole teeth allow it (else the nearest),
    then the distance nearest `want`; gear 2 has Ratio × gear 1's teeth, a = m (z1 + z2) / (2 cos β).
    dict(module, teeth1, teeth2, ratio, distance, small, warn, last) or None."""
    try:
        ratio, want = float(ratio), float(want)
    except (TypeError, ValueError):
        return None
    if ratio <= 0 or want <= 0 or not 0 <= step < len(SUGGEST_MODULES):
        return None
    m = SUGGEST_MODULES[step]
    c = math.cos(math.radians(helix or 0.0))
    best = None
    for z1 in range(MIN_TEETH, SUGGEST_MAX_TEETH + 1):
        z2 = int(round(z1 * ratio))
        if z2 < MIN_TEETH or z2 > SUGGEST_MAX_TEETH:
            continue
        got = z2 / z1
        a = m * (z1 + z2) / (2 * c)
        key = (round(abs(got - ratio), 9), abs(a - want))
        if best is None or key < best[0]:
            best = (key, z1, z2, got, a)
    if best is None:
        return None
    _key, z1, z2, got, a = best
    small = min(z1, z2)
    warn = ("fewer than %d teeth (%d): the root of the tooth is undercut" % (UNDERCUT_TEETH, small)
            if small < UNDERCUT_TEETH else "")
    return dict(module=m, teeth1=z1, teeth2=z2, ratio=got, distance=a, small=small, warn=warn,
                last=step + 1 >= len(SUGGEST_MODULES))


def teeth_for(diameter, module, helix=0.0):
    """The whole number of teeth closest to a pitch Ø (at least MIN_TEETH)."""
    return max(MIN_TEETH, int(round(diameter * math.cos(math.radians(helix)) / module)))


def center_distance(module, teeth_a, teeth_b, helix=0.0):
    """Axis to axis of two meshing gears of the same module, pressure and helix angle (opposite hands)."""
    return (pitch_diameter(module, teeth_a, helix) + pitch_diameter(module, teeth_b, helix)) / 2


def lead(module, starts):
    return starts * math.pi * module


def lead_angle(module, starts, diameter):
    """γ (degrees): the worm thread's slope at its pitch Ø — the helix angle of its wheel."""
    return math.degrees(math.atan(lead(module, starts) / (math.pi * diameter)))


def mesh_turn(teeth_a, turn_a, teeth_b):
    """How far (degrees) to turn gear B, set on gear A's +X at the centre distance, so a tooth of A meets a space
    of B there. A tooth's middle lies on +X at turn 0 (FreeCAD's profile); turning A turns B back by the ratio."""
    pitch_b = 360.0 / teeth_b
    turn = (180.0 - pitch_b / 2 - turn_a * teeth_a / teeth_b) % pitch_b
    return 0.0 if pitch_b - turn < 1e-9 else turn


# ----- checks -----
def check_gear(module, teeth, pressure, helix, width, bore):
    if module <= 0:
        raise ValueError("The module must be more than 0")
    if teeth < MIN_TEETH:
        raise ValueError("A gear needs %d teeth or more" % MIN_TEETH)
    if not 10 <= pressure <= 35:
        raise ValueError("The pressure angle must be between 10° and 35° (20° is standard)")
    if not 0 <= helix <= 45:
        raise ValueError("The helix angle must be between 0° (spur) and 45°")
    if width <= 0:
        raise ValueError("The width must be more than 0")
    root = root_diameter(module, teeth, helix)
    if bore < 0 or bore >= root - module:
        raise ValueError("The bore must be smaller than the root Ø less one module (%.4g mm)" % (root - module))


def check_worm(module, starts, diameter, length, pressure, bore):
    if module <= 0:
        raise ValueError("The module must be more than 0")
    if not 1 <= starts <= 6:
        raise ValueError("A worm has 1 to 6 starts")
    if not 10 <= pressure <= 35:
        raise ValueError("The pressure angle must be between 10° and 35° (20° is standard)")
    if length <= 0:
        raise ValueError("The length must be more than 0")
    root = diameter - 2 * DEDENDUM * module
    if root < 2 * module:
        raise ValueError("The worm Ø is too small for this module: at least %.4g mm" % (2 * DEDENDUM * module + 2 * module))
    if bore < 0 or bore >= root - module:
        raise ValueError("The bore must be smaller than the root Ø less one module (%.4g mm)" % (root - module))
    if lead_angle(module, starts, diameter) > 45:
        raise ValueError("Too many starts for this Ø: the thread would be steeper than 45°")


# ----- shapes -----
def gear_profile(module, teeth, pressure=20.0, helix=0.0):
    """The tooth outline in the transverse plane (z = 0), a tooth's middle on +X."""
    fcgear, involute = _generator()
    b = math.radians(helix)
    m_t = module / math.cos(b)
    alpha_t = math.degrees(math.atan(math.tan(math.radians(pressure)) / math.cos(b)))
    w = fcgear.FCWireBuilder()
    involute.CreateExternalGear(w, m_t, int(teeth), alpha_t, split=False)
    return Part.Wire([o.toShape() for o in w.wire])


def twisted(wire, height, lead_length, left=False):
    """The section `wire` swept up +Z by `height`, turning one full turn every `lead_length` (right hand: counter-
    clockwise seen from above as it rises)."""
    spine = Part.Wire(Part.LineSegment(V(0, 0, 0), V(0, 0, height)).toShape())
    helix = Part.makeHelix(lead_length, height, 10.0, 0, bool(left))
    ps = Part.BRepOffsetAPI.MakePipeShell(spine)
    ps.setAuxiliarySpine(Part.Wire(helix), True, 0)
    ps.add(wire)
    ps.build()
    ps.makeSolid()
    shape = ps.shape()
    solids = shape.Solids
    if len(solids) != 1 or not solids[0].isValid():
        raise ValueError("The teeth could not be twisted (try another helix angle)")
    return solids[0]


def _with_bore(solid, bore, height):
    if bore <= 0:
        return solid
    cut = solid.cut(Part.makeCylinder(bore / 2, height + 2, V(0, 0, -1)))
    return cut.Solids[0] if len(cut.Solids) == 1 else cut


def gear_solid(module, teeth, pressure=20.0, helix=0.0, hand="right", width=10.0, bore=0.0):
    check_gear(module, teeth, pressure, helix, width, bore)
    wire = gear_profile(module, teeth, pressure, helix)
    if helix <= 1e-9:
        solid = Part.Face(wire).extrude(V(0, 0, width))
    else:
        r = pitch_diameter(module, teeth, helix) / 2
        solid = twisted(wire, width, 2 * math.pi * r / math.tan(math.radians(helix)), hand == "left")
    return _with_bore(solid, bore, width)


def _polar(r, a):
    return V(r * math.cos(a), r * math.sin(a), 0)


def _curve(points):
    b = Part.BSplineCurve()
    b.interpolate(points)
    return b.toShape()


def worm_section(module, starts, diameter, pressure=20.0):
    """The worm's cross-section at z = 0 (a thread's middle on +X): flanks, tip and root arcs per start."""
    p = math.pi * module
    lead_length = starts * p
    rp, rt = diameter / 2, diameter / 2 + ADDENDUM * module
    rr = diameter / 2 - DEDENDUM * module
    tan_a = math.tan(math.radians(pressure))

    def angle(r):  # the flank at radius r, from the thread's middle
        return 2 * math.pi * (p / 4 - (r - rp) * tan_a) / lead_length
    n = 12
    rs = [rr + (rt - rr) * i / n for i in range(n + 1)]
    edges = []
    for k in range(starts):
        c = 2 * math.pi * k / starts
        nxt = 2 * math.pi * (k + 1) / starts
        edges.append(_curve([_polar(r, c - angle(r)) for r in rs]))
        a, e = c - angle(rt), c + angle(rt)
        edges.append(Part.Arc(_polar(rt, a), _polar(rt, (a + e) / 2), _polar(rt, e)).toShape())
        edges.append(_curve([_polar(r, c + angle(r)) for r in reversed(rs)]))
        a, e = c + angle(rr), nxt - angle(rr)
        edges.append(Part.Arc(_polar(rr, a), _polar(rr, (a + e) / 2), _polar(rr, e)).toShape())
    return Part.Wire(edges), lead_length


def worm_solid(module, starts, diameter, length, pressure=20.0, hand="right", bore=0.0):
    check_worm(module, starts, diameter, length, pressure, bore)
    wire, lead_length = worm_section(module, int(starts), diameter, pressure)
    return _with_bore(_round_ends(twisted(wire, length, lead_length, hand == "left"), module, diameter, length),
                      bore, length)


def _round_ends(solid, module, diameter, length):
    """The thread runs out one module before each end, turned down to the root Ø: the ends are exactly round (the
    user: a worm's ends must be round to work with — pick their circle, put a bearing on them)."""
    # a hair under the root Ø: the section's root is drawn by a spline that only nearly keeps it
    root, tip = diameter / 2 - (DEDENDUM + 0.02) * module, diameter / 2 + ADDENDUM * module + 1.0
    run = min(module, length / 4)
    for z in (-1.0, length - run):
        h = run + 1.0
        solid = solid.cut(Part.makeCylinder(tip, h, V(0, 0, z)).cut(Part.makeCylinder(root, h, V(0, 0, z))))
    return solid.Solids[0] if len(solid.Solids) == 1 else solid
