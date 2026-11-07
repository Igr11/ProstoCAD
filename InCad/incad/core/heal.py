"""A faulty solid made sound where it can be, without changing its shape: a STEP's arcs and lines written as
splines that stray off their faces (the user's air.step: "STEP is for exchange between CAD programs... it has to
work"). Each spline edge that is a line or an arc within TOL is made exact; a round face whose edges were such
splines is cut again from its own surface by its parameter range (exact when it is a rectangle there); faces of no
area go; the faces are sewn again. The solid back unchanged when that doesn't give one sound solid of the same
volume."""
import Part

TOL = 1e-3  # mm: how far a spline may be from the line or arc it is taken for
MANY_FACES = 3000  # more (an STL's triangles): not looked at


def faulty(shape):
    try:
        shape.check(True)
        return False
    except Exception:
        return True


def _exact(e):
    if type(e.Curve).__name__ != "BSplineCurve":
        return e.copy()
    a, b = e.FirstParameter, e.LastParameter
    pts = [e.valueAt(a + (b - a) * k / 16.0) for k in range(17)]
    if (pts[-1] - pts[0]).Length > 1e-9:
        line = Part.LineSegment(pts[0], pts[-1]).toShape()
        if max(line.distToShape(Part.Vertex(q))[0] for q in pts) < TOL:
            return line
    try:
        arc = Part.Arc(pts[0], pts[8], pts[16]).toShape()
        if max(arc.distToShape(Part.Vertex(q))[0] for q in pts) < TOL:
            return arc
    except Exception:
        pass
    return e.copy()


def _face(f):
    spline = any(type(e.Curve).__name__ == "BSplineCurve" for e in f.Edges)
    if not spline:
        return f.copy()
    if f.Surface.TypeId == "Part::GeomPlane":
        wires = [Part.Wire(Part.__sortEdges__([_exact(e) for e in w.Edges])) for w in f.Wires]
        return Part.Face(wires[0]) if len(wires) == 1 else Part.makeFace(wires, "Part::FaceMakerBullseye")
    u0, u1, v0, v1 = f.ParameterRange
    patch = f.Surface.toShape(u0, u1, v0, v1)
    if abs(patch.Area - f.Area) > 1e-3 * f.Area:
        raise ValueError("not a rectangle on its surface")
    return patch


def healed(solid):
    """`solid` made sound (see the module), or `solid` itself."""
    if len(solid.Faces) > MANY_FACES or not faulty(solid):
        return solid
    try:
        faces = [_face(f) for f in solid.Faces if f.Area > 1e-6]
        sewn = Part.Compound(faces)
        sewn.sewShape(TOL)
        if len(sewn.Shells) != 1 or not sewn.Shells[0].isClosed():
            return solid
        out = Part.Solid(sewn.Shells[0])
        if out.Volume < 0:
            out.reverse()
    except Exception:
        return solid
    if not out.isValid() or faulty(out) or abs(out.Volume - solid.Volume) > 1e-4 * abs(solid.Volume):
        return solid
    return out
