"""Text for sketches (Inventor's Text and Geometry Text): letter outlines as faces in sketch coordinates, either from an
anchor point (lines, alignment, angle) or along a curve of the sketch (start / centre / end, a shift along it, a
distance off it, flipped to the other side). Size is the height of a capital letter (FreeCAD's own height is a
font unit that differs per font). Fonts are the computer's installed .ttf / .otf / .ttc files, grouped by family with their
Bold / Italic files."""
import math
import os

import FreeCAD as App
import Part

from incad import host

FONT_DIRS = host.font_dirs()
STYLES = ("Regular", "Bold", "Italic", "Bold Italic")
DEFAULT_FAMILY = "Arial"
_fonts = None
_cap = {}      # font path -> capital height per unit of FreeCAD height
_glyphs = {}   # (font path, char) -> (faces at height 1, advance at height 1)


def _style_of(stem, family):
    rest = stem[len(family):].strip().lower()
    bold = "bold" in rest
    italic = "italic" in rest or "oblique" in rest
    return "Bold Italic" if bold and italic else "Bold" if bold else "Italic" if italic else "Regular"


def _names_in_file(path):
    """(family, style) from the font's own name table (its first font, for a .ttc), or None. The legacy family
    (name 1) and subfamily (name 2) are the Regular / Bold / Italic / Bold Italic model InCad offers."""
    import struct
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            base = struct.unpack(">I", f.read(4))[0] if head[:4] == b"ttcf" else 0
            f.seek(base + 4)
            tables = struct.unpack(">H", f.read(2))[0]
            f.seek(base + 12)
            directory = f.read(16 * tables)
            name_at = name_len = None
            for i in range(tables):
                tag, _, offset, length = struct.unpack_from(">4sIII", directory, 16 * i)
                if tag == b"name":
                    name_at, name_len = offset, length
            if name_at is None:
                return None
            f.seek(name_at)
            data = f.read(name_len)  # only the name table: Windows' Fonts folder holds hundreds of MB
        _, count, strings = struct.unpack_from(">HHH", data, 0)
        found = {}
        for i in range(count):
            platform, encoding, language, name_id, length, offset = struct.unpack_from(">6H", data, 6 + 12 * i)
            if name_id not in (1, 2):
                continue
            raw = data[strings + offset:strings + offset + length]
            if platform == 3 and encoding in (0, 1, 10):
                rank, text = (0 if language == 0x409 else 1), raw.decode("utf-16-be", "replace")
            elif platform == 1 and encoding == 0:
                rank, text = 2, raw.decode("latin-1")
            else:
                continue
            if name_id not in found or rank < found[name_id][0]:
                found[name_id] = (rank, text.strip())
        if 1 not in found:
            return None
        sub = found.get(2, (0, "Regular"))[1].lower()
        bold, italic = "bold" in sub, "italic" in sub or "oblique" in sub
        return found[1][1], "Bold Italic" if bold and italic else "Bold" if bold else "Italic" if italic else "Regular"
    except (OSError, struct.error):
        return None


def fonts():
    """{family: {style: path}} of the installed fonts. A file 'Arial Bold Italic.ttf' is Arial's Bold Italic; a
    family without a Regular file uses its first one."""
    global _fonts
    if _fonts is not None:
        return _fonts
    files = []
    for d in FONT_DIRS:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            if n.startswith(".") or not n.lower().endswith((".ttf", ".otf", ".ttc")):
                continue
            files.append((os.path.splitext(n)[0], os.path.join(d, n)))
    stems = {stem for stem, _ in files}
    out = {}
    for stem, path in sorted(files):
        named = _names_in_file(path) if host.WINDOWS else None  # Windows' file names are 'arialbd.ttf'
        if named is not None:
            family, style = named
        else:
            family = stem
            for word in (" Bold Italic", " Bold Oblique", " Bold", " Italic", " Oblique"):  # (Arial Black: a family)
                if stem.endswith(word) and stem[:-len(word)] in stems:
                    family = stem[:-len(word)]
                    break
            style = _style_of(stem, family) if family != stem else "Regular"
        out.setdefault(family, {}).setdefault(style, path)
    for family, styles in out.items():
        if "Regular" not in styles:
            styles["Regular"] = next(iter(styles.values()))
    _fonts = out
    return out


def font_path(family, bold=False, italic=False):
    """The font file for a family and style (the family's Regular when it has no such style)."""
    styles = fonts().get(family) or fonts().get(DEFAULT_FAMILY) or next(iter(fonts().values()), {})
    if not styles:
        raise ValueError("No fonts found on this computer")
    want = "Bold Italic" if bold and italic else "Bold" if bold else "Italic" if italic else "Regular"
    return styles.get(want) or styles["Regular"]


def _wires(text, path, height):
    d, f = os.path.split(path)
    return Part.makeWireString(text, d + os.sep, f, height, 0)


def _cap_height(path):
    """Height of a capital H per unit of FreeCAD's text height (Size means the capital letters' height)."""
    if path not in _cap:
        ws = [w for ch in _wires("H", path, 10.0) for w in ch]
        b = Part.makeCompound(ws).BoundBox if ws else None
        _cap[path] = (b.YMax - b.YMin) / 10.0 if b is not None and b.YMax > b.YMin else 0.7
    return _cap[path]


def _faces(wires):
    ws = [w for w in wires if w.isClosed()]
    if not ws:
        return []
    try:
        return list(Part.makeFace(ws, "Part::FaceMakerBullseye").Faces)  # outer outlines with their holes (O, A, Р)
    except Part.OCCError:
        return [Part.Face(w) for w in ws]


CORNER_DEG = 10.0  # a turn sharper than this between two pieces of a letter's outline is a corner of the letter


def _points_of(edges):
    """Points along pieces of an outline, one after another, each piece the way the chain runs."""
    pts = []
    for e in edges:
        n = max(12, min(60, int(e.Length / 0.01)))
        p = e.discretize(Number=n)
        if pts and (p[-1] - pts[-1]).Length < (p[0] - pts[-1]).Length:
            p = p[::-1]
        elif not pts and len(edges) > 1:
            nxt = edges[1]
            ends = [v.Point for v in nxt.Vertexes]
            if min((p[0] - q).Length for q in ends) < min((p[-1] - q).Length for q in ends):
                p = p[::-1]
        pts += p[1:] if pts else p
    return pts


def _smooth_wire(w):
    """A letter's outline with the pieces between two corners made into one smooth curve (the font gives a piece
    per arc of the letter: each became a wall of its own, with a seam between, in Extrude and Emboss). Corners
    stay sharp; a round outline with no corner (an O) is one closed curve."""
    es = w.OrderedEdges
    if len(es) < 2:
        return w

    def turn(a, b):  # the angle where a ends and b starts, in degrees
        pa, pb = [v.Point for v in a.Vertexes], [v.Point for v in b.Vertexes]
        i, j = min(((i, j) for i in range(len(pa)) for j in range(len(pb))), key=lambda ij: (pa[ij[0]] - pb[ij[1]]).Length)
        ta = a.tangentAt(a.FirstParameter if i == 0 else a.LastParameter)
        tb = b.tangentAt(b.FirstParameter if j == 0 else b.LastParameter)
        if ta.Length < 1e-12 or tb.Length < 1e-12:
            return 180.0
        ang = math.degrees(ta.getAngle(tb))
        return min(ang, 180.0 - ang)

    sharp = [turn(es[k], es[(k + 1) % len(es)]) > CORNER_DEG for k in range(len(es))]  # after piece k
    try:
        if not any(sharp):  # no corner: one closed smooth curve
            pts = _points_of(es)[:-1]
            c = Part.BSplineCurve()
            c.interpolate(pts, PeriodicFlag=True)
            return Part.Wire([c.toShape()])
        k0 = sharp.index(True) + 1  # start just after a corner
        order = es[k0:] + es[:k0]
        flags = sharp[k0:] + sharp[:k0]
        runs, cur = [], []
        for e, corner in zip(order, flags):
            cur.append(e)
            if corner:
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        out = []
        for run in runs:
            if len(run) == 1:
                out.append(run[0])
                continue
            pts = _points_of(run)
            c = Part.BSplineCurve()
            c.interpolate(pts)
            out.append(c.toShape())
        made = Part.Wire(out)
        return made if made.isClosed() else w
    except Exception:
        return w


def _smooth(faces):
    out = []
    for f in faces:
        try:
            made = Part.makeFace([_smooth_wire(w) for w in f.Wires], "Part::FaceMakerBullseye").Faces
            ok = len(made) == 1 and made[0].isValid() and abs(made[0].Area - f.Area) <= 0.002 * f.Area
            out += made if ok else [f]
        except Exception:
            out.append(f)
    return out


def _glyph(path, char):
    """(faces of one letter at FreeCAD height 1, its advance width at height 1): the advance is where the next
    letter would start, measured with a marker letter after it (FreeCAD gives no metrics)."""
    key = (path, char)
    if key not in _glyphs:
        faces = _smooth(_faces(_wires(char, path, 1.0)[0])) if char.strip() else []
        mark = _wires("I", path, 1.0)
        pair = _wires(char + "I", path, 1.0)
        try:
            x0 = Part.makeCompound(mark[0]).BoundBox.XMin
            x1 = Part.makeCompound(pair[-1]).BoundBox.XMin
            advance = x1 - x0
        except Exception:
            advance = 0.5
        _glyphs[key] = (faces, advance)
    return _glyphs[key]


def _mirrored(faces, x=0.0):
    """The faces flipped left-right about the vertical line at x (text read from the plane's other side)."""
    return [f.mirror(App.Vector(x, 0, 0), App.Vector(1, 0, 0)).Faces[0] for f in faces]


def _scaled(faces, s, mat=None):
    out = []
    for f in faces:
        m = App.Matrix()
        m.scale(s, s, 1)
        g = f.transformGeometry(m)  # a generic shape: its face again
        g = g.Faces[0] if g.Faces else g
        if mat is not None:
            g.transformShape(mat)
        out.append(g)
    return out


def _merged(faces):
    """Letters that lie over each other (a tight spacing, a script font) as one outline: no wall inside where they
    join; letters apart stay apart, each with its holes."""
    if len(faces) < 2:
        return faces
    owner = list(range(len(faces)))  # letters that really overlap (not only their boxes, as round a curve) join

    def root(i):
        while owner[i] != i:
            owner[i] = owner[owner[i]]
            i = owner[i]
        return i

    boxes = [f.BoundBox for f in faces]
    for i in range(len(faces)):
        for j in range(i + 1, len(faces)):
            if root(i) == root(j) or not boxes[i].intersect(boxes[j]):
                continue
            try:
                if faces[i].common(faces[j]).Area > 1e-9:
                    owner[root(j)] = root(i)
            except Exception:
                pass
    groups = {}
    for i in range(len(faces)):  # in the letters' order (the first letter of each group)
        groups.setdefault(root(i), [[], None])[0].append(faces[i])
    groups = list(groups.values())
    out = []
    for group, _b in groups:
        if len(group) == 1:
            out += group
            continue
        try:  # the fused pieces' outer edges (an edge two pieces share is inside), made into faces again: the
            # fuse alone leaves the joined letters cut into pieces, and removeSplitter does not join them in a plane
            pieces = group[0].fuse(group[1:]).Faces
            uses = {}
            for f in pieces:
                for e in f.Edges:
                    uses.setdefault(e.hashCode(), [e, 0])[1] += 1
            edges = [e for e, n in uses.values() if n == 1]
            wires = [Part.Wire(es) for es in Part.sortEdges(edges)]
            made = [f for f in Part.makeFace(wires, "Part::FaceMakerBullseye").Faces if f.Area > 1e-12]
            out += made if made and all(f.isValid() for f in made) else group
        except Exception:
            out += group
    return out


def _warped_edge(edge, fn):
    """The edge moved point by point through fn (x, y) -> (x, y): a smooth curve through its moved points, its ends
    exactly on the moved ends (the corners of the letter stay sharp)."""
    n = max(8, int(edge.Length / 0.02)) if edge.Length > 0 else 8
    n = min(n, 200)
    pts = [App.Vector(*fn(p.x, p.y), 0) for p in edge.discretize(Number=n + 1)]
    c = Part.BSplineCurve()
    c.interpolate(pts)
    return c.toShape()


def _warped(faces, fn):
    """The faces moved point by point through fn (their outlines rebuilt as smooth curves)."""
    out = []
    for f in faces:
        wires = []
        for w in f.Wires:
            edges = [_warped_edge(e, fn) for e in w.OrderedEdges]
            wires.append(Part.Wire(edges))
        try:
            out += list(Part.makeFace(wires, "Part::FaceMakerBullseye").Faces)
        except Part.OCCError:
            out += [Part.Face(w) for w in wires]
    return out


def _line_faces(line, path, size, spacing):
    """The letters of one line from x=0 on the baseline y=0, and the line's width."""
    k = size / _cap_height(path)  # FreeCAD height for capital letters of `size`
    pen, faces = 0.0, []
    for ch in line:
        glyph, adv = _glyph(path, ch)
        m = App.Matrix()
        m.move(App.Vector(pen, 0, 0))
        faces += _scaled(glyph, k, m)
        pen += adv * k + spacing
    return faces, max(0.0, pen - spacing)


def at_point(text, path, size, anchor=(0.0, 0.0), align="left", angle=0.0, spacing=0.0, line_spacing=1.5):
    """Faces of the text in sketch coordinates: the first line's baseline through the anchor, aligned left / centre /
    right on it, turned by `angle` (degrees) about it; further lines below, line_spacing × size apart."""
    if size <= 0:
        raise ValueError("Size must be more than 0")
    out = []
    for i, line in enumerate(text.split("\n")):
        faces, width = _line_faces(line, path, size, spacing)
        dx = {"left": 0.0, "center": -width / 2.0, "right": -width}.get(align, 0.0)
        m = App.Matrix()
        m.move(App.Vector(dx, -i * size * line_spacing, 0))
        for f in faces:
            f.transformShape(m)
        out += faces
    rot = App.Matrix()
    rot.rotateZ(math.radians(angle))
    move = App.Matrix()
    move.move(App.Vector(anchor[0], anchor[1], 0))
    m = move.multiply(rot)
    for f in out:
        f.transformShape(m)
    return _merged(out)


def box_frame(corners):
    """(centre, u, v, width, height) of a text box from its four corners (in order round it): u runs along the
    side nearest the sketch's X direction (pointing right), v square to it (up). A tilted box tilts its text."""
    pts = [App.Vector(c.x, c.y, 0) for c in corners]
    centre = sum(pts, App.Vector()) * 0.25
    sides = [pts[(i + 1) % 4] - pts[i] for i in range(4)]
    u = max(sides, key=lambda d: abs(d.x) / d.Length if d.Length > 1e-12 else 0)
    u = App.Vector(u)
    if u.Length < 1e-9:
        raise ValueError("The text box has no size")
    u.normalize()
    if u.x < -1e-9 or (abs(u.x) < 1e-9 and u.y < 0):
        u = u * -1
    v = App.Vector(-u.y, u.x, 0)
    us = [(p - centre).dot(u) for p in pts]
    vs = [(p - centre).dot(v) for p in pts]
    return centre, u, v, max(us) - min(us), max(vs) - min(vs)


def in_box(text, path, size, corners, halign="center", valign="middle", spacing=0.0, line_spacing=1.5, fit=False,
           mirror=False, frame="rect"):
    """Faces of the text inside a box (four corners, in sketch coordinates): each line aligned left / centre /
    right across it, the block of lines top / middle / bottom in it, the capital letters `size` tall. fit: the
    letters scaled so the widest line fills the box's width (never taller than the box). Longer than the box:
    the text runs past it (Inventor). mirror: flipped left-right in the box, to read from the plane's other side
    (left / right then mean as seen from there). frame: "rect" the box itself; "ellipse" the ellipse in the box,
    "circle" the circle in it (as wide as the box's shorter side): the text stands in the rectangle inside the round
    frame, or with fit takes the frame's shape, each letter as high as the frame is where it stands."""
    centre, u, v, width, height = box_frame(corners)
    if width < 1e-6 or height < 1e-6:
        raise ValueError("The text box has no size")
    lines = text.split("\n")

    def ink(faces):  # the letters' own extent (the advance has a little air beside each letter)
        return Part.makeCompound(faces).BoundBox if faces else None

    to_box = App.Matrix(u.x, v.x, 0, centre.x,
                        u.y, v.y, 0, centre.y,
                        0, 0, 1, 0,
                        0, 0, 0, 1)
    if frame == "circle":
        width = height = min(width, height)
    if frame in ("ellipse", "circle") and fit:
        out = _filled(lines, path, spacing, line_spacing, halign, width, height, mirror)
        for f in out:
            f.transformShape(to_box)
        return _merged(out)
    if frame in ("ellipse", "circle"):  # the rectangle inside the round frame
        width, height = width / math.sqrt(2.0), height / math.sqrt(2.0)

    if fit:
        boxes = [ink(_line_faces(line, path, 1.0, spacing)[0]) for line in lines]
        widest = max((b.XMax - b.XMin for b in boxes if b is not None), default=0.0)
        block = 1.0 + (len(lines) - 1) * line_spacing  # the block's height at size 1
        if widest <= 1e-9:
            raise ValueError("Type the text")
        size = min(width / widest, height / block)
    if size <= 0:
        raise ValueError("Size must be more than 0")
    n = len(lines)
    drop = (n - 1) * size * line_spacing  # first baseline to last
    y0 = {"top": height / 2.0 - size, "bottom": -height / 2.0 + drop}.get(valign, (drop - size) / 2.0)
    out = []
    for i, line in enumerate(lines):
        faces, _ = _line_faces(line, path, size, spacing)
        bb = ink(faces)
        if bb is None:
            continue  # an empty line: just its space
        x = {"left": -width / 2.0 - bb.XMin, "right": width / 2.0 - bb.XMax}.get(halign, -(bb.XMin + bb.XMax) / 2.0)
        m = App.Matrix()
        m.move(App.Vector(x, y0 - i * size * line_spacing, 0))
        for f in faces:
            f.transformShape(m)
        out += faces
    if mirror:
        out = _mirrored(out)
    for f in out:
        f.transformShape(to_box)
    return _merged(out)


def _filled(lines, path, spacing, line_spacing, halign, width, height, mirror):
    """Fit in an ellipse (width × height, round the origin): the letters take the frame's shape, each from its own
    foot to its own top as high as the frame is where it stands; across, the text fills the frame (to 0.97 of it:
    at the very ends the frame has no height left). Letters over each other from left to right are one (the dot on
    an i)."""
    faces = []
    for i, line in enumerate(lines):
        row, _ = _line_faces(line, path, 1.0, spacing)
        bb = Part.makeCompound(row).BoundBox if row else None
        if bb is None:
            continue
        x = {"left": -bb.XMin, "right": -bb.XMax}.get(halign, -(bb.XMin + bb.XMax) / 2.0)
        m = App.Matrix()
        m.move(App.Vector(x, -i * line_spacing, 0))
        for f in row:
            f.transformShape(m)
        faces += row
    if not faces:
        raise ValueError("Type the text")
    whole = Part.makeCompound(faces).BoundBox
    if whole.XLength <= 1e-9:
        raise ValueError("Type the text")
    cx, half_w, wide = (whole.XMin + whole.XMax) / 2.0, whole.XLength / 2.0, 0.97
    letters = []  # [right end, [faces]]
    for f in sorted(faces, key=lambda f: f.BoundBox.XMin):
        b = f.BoundBox
        if letters and b.XMin < letters[-1][0]:
            letters[-1][0] = max(letters[-1][0], b.XMax)
            letters[-1][1].append(f)
        else:
            letters.append([b.XMax, [f]])
    out = []
    for _right, group in letters:
        b = Part.makeCompound(group).BoundBox
        middle, half = (b.YMin + b.YMax) / 2.0, ((b.YMax - b.YMin) / 2.0) or 1.0

        def fn(px, py, middle=middle, half=half):
            x = (px - cx) / half_w * wide
            y = (py - middle) / half
            x = -x if mirror else x
            return width / 2.0 * x, height / 2.0 * y * math.sqrt(max(0.0, 1.0 - x * x))

        out += _warped(group, fn)
    return out


def along(text, path, size, edge, position="center", shift=0.0, distance=0.0, flip=False, spacing=0.0, mirror=False):
    """Faces of one line of text along a curve (an edge in sketch coordinates): each letter sits on the curve,
    turned with it, `distance` off it on the letters' upper side. position: where the text sits on the curve —
    start, centre (a closed curve: its top) or end — moved by `shift` along it. A closed curve is walked clockwise
    (the text reads on the outside); flip walks the other way (inside a ring, below a line)."""
    if size <= 0:
        raise ValueError("Size must be more than 0")
    line = text.replace("\n", " ")
    if mirror:  # read from the plane's other side: the letters in turn from the other end, each flipped
        line = line[::-1]
    k = size / _cap_height(path)
    letters, pen = [], 0.0
    for ch in line:
        glyph, adv = _glyph(path, ch)
        if mirror and glyph:
            glyph = _mirrored(glyph, adv / 2.0)
        letters.append((glyph, pen, adv * k))
        pen += adv * k + spacing
    total = max(0.0, pen - spacing)
    length = edge.Length
    closed = edge.isClosed()

    # walk: arc length s from the walk's start -> point and walking direction
    reverse = (closed and _counter_clockwise(edge)) != bool(flip) if closed else bool(flip)

    def at(s):
        if closed:
            s %= length
        else:
            s = min(max(s, 0.0), length)
        t = edge.getParameterByLength(length - s if reverse else s)
        p, d = edge.valueAt(t), edge.tangentAt(t)
        return p, (d * -1 if reverse else d)

    if closed:  # centre: the middle of the text at the curve's top (its highest point) — flipped, at its bottom,
        # where text inside a ring reads upright (a coin); start / end at the curve's start point
        n = 720
        pts = edge.discretize(Number=n + 1)  # evenly spaced by length: index i is arc length i·L/n
        pick = min if flip else max
        s_top = pick(range(n), key=lambda j: pts[j].y) * length / n
        if reverse:
            s_top = (length - s_top) % length
        start = {"start": 0.0, "end": -total}.get(position, s_top - total / 2.0) + shift
        if total > length + 1e-6:
            raise ValueError("The text is longer than the curve: make it smaller")
    else:
        start = {"start": 0.0, "end": length - total}.get(position, (length - total) / 2.0) + shift
        if start < -1e-6 or start + total > length + 1e-6:
            raise ValueError("The text is longer than the curve (or shifted off it): make it smaller or shift it")
    out = []
    for glyph, x, adv in letters:
        if not glyph:
            continue
        p, d = at(start + x + adv / 2.0)
        ang = math.atan2(d.y, d.x)
        m = App.Matrix()
        m.move(App.Vector(-adv / 2.0, distance, 0))  # the letter's middle on the curve, its baseline `distance` off
        r = App.Matrix()
        r.rotateZ(ang)
        t = App.Matrix()
        t.move(App.Vector(p.x, p.y, 0))
        out += _scaled(glyph, k, t.multiply(r).multiply(m))
    return _merged(out)


def _counter_clockwise(edge):
    """A closed sketch curve runs counter-clockwise (FreeCAD's circles and ellipses do): its signed area > 0."""
    pts = edge.discretize(64)
    area = sum(p.x * q.y - q.x * p.y for p, q in zip(pts, pts[1:] + pts[:1]))
    return area > 0

