"""Solids made of an STL's triangles (STL → STEP, a mesh turned into a solid): thousands of flat three-edged faces.
A Boolean with one can run for hours and freeze the program (the user's ProstoCAD stood 30 minutes on an AI's cut of
such a solid and had to be force quit), so Booleans refuse them with a way round. No FreeCAD import at module level:
usable from widget tests."""

LEAST = 2000  # faces: fewer is an ordinary part, however detailed
SHARE = 0.9   # of the faces looked at, at least this many flat triangles
LOOK = 400    # faces looked at (evenly spread): counting them all took long on big meshes


def is_faceted(shape, least=LEAST):
    """True for a shape of at least `least` faces that are nearly all flat triangles."""
    try:
        faces = shape.Faces
    except Exception:
        return False
    n = len(faces)
    if n < least:
        return False
    step = max(1, n // LOOK)
    seen = flat = 0
    for f in faces[::step]:
        seen += 1
        try:
            if len(f.Edges) == 3 and f.Surface.TypeId == "Part::GeomPlane":
                flat += 1
        except Exception:
            pass
    return seen > 0 and flat >= SHARE * seen


def refusal(label, shape):
    return ("%s is made of an STL's triangles (%d faces): a Boolean with it can run for hours and freeze the program. "
            "Rebuild it as features first (Smooth, or by hand from sections), then combine."
            % (label, len(shape.Faces)))


def skip_refine(objs):
    """Refine off for each feature among `objs` built on a faceted solid (the user: Extrude / Revolve on an STL hook took
    a minute each, every preview again): FreeCAD's Refine looked at all 25 856 faces, 67-75 s, without it 3-7 s; the
    STL's flat triangles are merged already (STL → STEP). Written only on change; returns the names turned off."""
    off = []
    for o in objs or ():
        try:
            if not getattr(o, "Refine", False):
                continue
            base = getattr(o, "BaseFeature", None)
            if base is None or not is_faceted(base.Shape):
                continue
            o.Refine = False
            off.append(o.Name)
        except Exception:
            pass
    if off:
        from incad.core import log
        log.logger().info("Refine off (built on an STL's triangles): %s", ", ".join(off))
    return off


def base_changed(obj):
    """A feature put into a Solid (FreeCAD sets its BaseFeature) by a command or dialog: Refine off before anything
    computes it (Extrude's and Revolve's own create computed it once, 58 s, before the dialog's first preview). Not
    while a file loads, an undo replays or a recompute runs: only inside a command's open step."""
    try:
        doc = obj.Document
        if doc.Restoring or doc.Recomputing or not doc.HasPendingTransaction:
            return []
    except Exception:
        return []
    return skip_refine([obj])
