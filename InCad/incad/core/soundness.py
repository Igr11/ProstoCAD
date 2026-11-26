"""OpenCASCADE's full check (faces that cross themselves, edges off their faces), asked for as little as can be: a shape
checked once is not checked again, and of a step's result only the faces it made or cut, with the faces near them: the
rest is the part before it, unchanged, and what was wrong in that is not this step's doing. (The whole check took 1.5
to 4 s on parts with letters, two or three times for one OK.)"""
import FreeCAD as App
import Part

_known = []  # [(shape, faulty)], the last ones: the shape is kept, so its faces stay the ones looked at
KEEP = 12


def forget():
    """A fillet, chamfer, shell or draft was computed: OpenCASCADE may have changed a shape it was built on in place."""
    del _known[:]
    del _valids[:]


def _whole(shape):
    try:
        shape.check(True)
        return False
    except Exception:
        return True


def faulty(shape, base=None):
    """True when the full check finds faults in `shape`; base: the shape it was made from (only what differs is looked
    at first, and a fault found there is confirmed on the whole shape)."""
    for s, f in _known:
        if s.isSame(shape):
            return f
    part = _changed(shape, base) if base is not None else None
    bad = False if part is not None and not _whole(part) else _whole(shape)
    if App.GuiUp:  # (forget() is the shell's to call)
        _known.append((shape, bad))
        del _known[:-KEEP]
    return bad


_valids = []


def valid(shape):
    """shape.isValid(), once per shape."""
    for s in _valids:
        if s.isSame(shape):
            return True
    ok = shape.isValid()
    if ok and App.GuiUp:
        _valids.append(shape)
        del _valids[:-KEEP]
    return ok


def _print(face):
    bb = face.BoundBox
    try:
        kind = face.Surface.TypeId
    except Exception:
        kind = ""
    return (kind, len(face.Edges), tuple(sorted((round(v.X, 5), round(v.Y, 5), round(v.Z, 5)) for v in face.Vertexes)),
            tuple(round(x, 4) for x in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)))


def _planes(faces):
    out = []
    for f in faces:
        try:
            srf = f.Surface
            if srf.TypeId == "Part::GeomPlane":
                out.append((srf.Axis, srf.Axis.dot(srf.Position), f.BoundBox))
        except Exception:
            pass
    return out


def _trimmed(face, planes):
    """A flat face that is a face of before cut smaller: it meets nothing it did not meet, but where it was cut (along
    the faces that cut it)."""
    try:
        srf = face.Surface
        if srf.TypeId != "Part::GeomPlane":
            return False
        bb, n, at = face.BoundBox, srf.Axis, srf.Axis.dot(srf.Position)
        for axis, d, box in planes:
            c = axis.dot(n)
            if abs(abs(c) - 1) < 1e-9 and abs(d - c * at) < 1e-6 and \
                    box.XMin - 1e-4 <= bb.XMin and box.XMax + 1e-4 >= bb.XMax and box.YMin - 1e-4 <= bb.YMin and \
                    box.YMax + 1e-4 >= bb.YMax and box.ZMin - 1e-4 <= bb.ZMin and box.ZMax + 1e-4 >= bb.ZMax:
                return True
    except Exception:
        pass
    return False


def _changed(shape, base):
    """The faces of `shape` that `base` has not, with those whose boxes touch theirs, in one compound; None when that is
    most of it."""
    try:
        faces = shape.Faces
        if len(faces) < 30 or base is None or base.isNull() or not base.Solids:
            return None
        before = base.Faces
        had = {_print(f) for f in before}
        new = [f for f in faces if _print(f) not in had]
        if not new or 2 * len(new) > len(faces):
            return None
        planes = _planes(before)
        boxes = []
        for f in new:
            if not _trimmed(f, planes):
                bb = f.BoundBox
                bb.enlarge(0.05 + 0.1 * bb.DiagonalLength)
                boxes.append(bb)
        if not boxes:
            return None
        known = {f.hashCode() for f in new}
        near = [f for f in faces if f.hashCode() not in known and any(b.intersect(f.BoundBox) for b in boxes)]
        if 10 * (len(new) + len(near)) > 8 * len(faces):
            return None
        return Part.makeCompound(new + near)
    except Exception:
        return None
