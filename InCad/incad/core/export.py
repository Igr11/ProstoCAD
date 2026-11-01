"""File ▸ Export: the part as the view shows it (every visible Solid at its End of Part; in an assembly the visible,
unsuppressed components where they stand) to STEP (exact) or STL (a mesh for slicers)."""
import os

import FreeCAD as App

from incad.core import document

FORMATS = {".step": "step", ".stp": "step", ".stl": "stl"}
FINENESS = {"normal": (0.05, 0.35), "fine": (0.01, 0.1)}  # STL: (LinearDeflection mm, AngularDeflection rad)


def _shown_solid(body):
    """A Solid the view shows: Hide / Show records the choice in InCadShown (Visibility is off while a sketch is
    edited)."""
    if document.SHOWN_PROP in body.PropertiesList:
        return bool(getattr(body, document.SHOWN_PROP))
    return True


def shapes(doc):
    """What Export writes: [(label, shape)] in model coordinates (mm)."""
    import Part
    from incad.core import assembly
    if assembly.is_assembly(doc):
        found = [(c.Label, Part.getShape(c)) for c in assembly.components(doc)
                 if c.Visibility and not assembly.is_suppressed(c)]
    else:
        found = [(b.Label, b.Shape) for b in document.bodies(doc) if _shown_solid(b)]
    found = [(label, s) for label, s in found if s is not None and not s.isNull()]
    if not found:
        raise ValueError("Nothing to export: %s shows no solid" % doc.Label)
    return found


def file_format(path):
    fmt = FORMATS.get(os.path.splitext(path)[1].lower())
    if fmt is None:
        raise ValueError("Export writes .step, .stp or .stl files, not %s" % os.path.basename(path))
    return fmt


SAME_VOLUME = 1e-3  # a B-spline made of a loft's offset wall held 0.04 % more: 0.0006 mm on average


def _without_offset_faces(solid):
    """The solid with its offset-surface faces (a round's, a shell's) as B-spline faces, sewn again; the solid
    itself when there are none or the result is not the same solid. A STEP's offset surfaces came out of
    Plasticity with edges 7-8 mm off and open faces (the user's bug1: unusable)."""
    import Part
    if not any(f.Surface.TypeId == "Part::GeomOffsetSurface" for f in solid.Faces):
        return solid
    try:
        faces = [f.toNurbs().Faces[0] if f.Surface.TypeId == "Part::GeomOffsetSurface" else f for f in solid.Faces]
        sewn = Part.Compound(faces).copy()
        sewn.sewShape()
        if len(sewn.Shells) != 1 or not sewn.Shells[0].isClosed():
            return solid
        out = Part.Solid(sewn.Shells[0])
        if out.Volume < 0:
            out.reverse()
    except Exception:
        return solid
    if not out.isValid() or abs(out.Volume - solid.Volume) > SAME_VOLUME * abs(solid.Volume) \
            or len(out.Faces) != len(solid.Faces):
        return solid
    return out


def step_shape(shape):
    """What a STEP file gets: each solid without offset surfaces (see _without_offset_faces)."""
    import Part
    solids = shape.Solids
    if not solids:
        return shape
    fixed = [_without_offset_faces(s) for s in solids]
    if all(a is b for a, b in zip(fixed, solids)):
        return shape
    return fixed[0] if shape.ShapeType == "Solid" else Part.Compound(fixed)


def write_step(shape, path):
    """A STEP file other CAD programs read as the solid it is: the edges' curves on their surfaces written too
    (FreeCAD leaves them out: Plasticity rebuilt them and edges drifted), offset surfaces as B-splines."""
    import Part
    try:
        Part.setStaticValue("write.surfacecurve.mode", 1)
    except Exception:
        pass
    step_shape(shape).exportStep(path)


def write(items, path, fineness="normal"):
    """Write [(label, shape)] to `path`: STEP keeps the exact solids, STL is a mesh at the chosen fineness.
    Returns the number of mesh facets for STL, else 0."""
    import Part
    fmt = file_format(path)
    shape = items[0][1] if len(items) == 1 else Part.Compound([s for _, s in items])
    if fmt == "step":
        write_step(shape, path)
        return 0
    import MeshPart
    linear, angular = FINENESS[fineness]
    mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=linear, AngularDeflection=angular, Relative=False)
    mesh.write(path)
    return mesh.CountFacets


def export(doc, path, fineness="normal"):
    return write(shapes(doc), path, fineness)


def threads_held_off(doc):
    """Threads Off is holding this part's (or the assembly's parts') threads off."""
    from incad.core import assembly, suppress
    return assembly.threads_off(doc) if assembly.is_assembly(doc) else suppress.threads_off(doc)


def toggle_threads(doc):
    """Threads on for the export, then back off: the part ends as it was."""
    from incad.core import assembly, suppress
    if assembly.is_assembly(doc):
        return assembly.toggle_threads(doc)
    state = suppress.toggle_threads(doc)
    doc.recompute()
    return state


def changed_documents(doc):
    """The files a threads toggle touches (the assembly's parts too)."""
    from incad.core import assembly
    return [doc] + (assembly.part_documents(doc) if assembly.is_assembly(doc) else [])
