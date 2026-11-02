"""2D offsets without OpenCASCADE's crash: OCC's makeOffset2D brought the whole program down (a segfault, nothing to
catch) on a lone full circle of radius 1 mm or less offset inward - a 2 mm tube's hollow Sweep, Sketch Offset
inside a 2 mm circle (the finder, 2026-11-02). A lone circle is offset here by its radius; anything else goes to
OCC as before."""
import Part

TOO_SMALL = "The offset is larger than the circle: nothing would be left"


def _lone_circle(shape):
    """The circle edge when `shape` (a wire or a face) is one full circle and nothing else, else None."""
    try:
        wires = [shape] if shape.ShapeType == "Wire" else list(shape.Wires)
        if len(wires) != 1 or len(wires[0].Edges) != 1:
            return None
        e = wires[0].Edges[0]
        if type(e.Curve).__name__ == "Circle" and e.isClosed():
            return e
    except Exception:
        pass
    return None


def _halved(wire):
    """The wire with every full circle in it as two half arcs (that wire does not bring OCC down)."""
    edges = []
    for e in wire.Edges:
        c = e.Curve
        if type(c).__name__ == "Circle" and e.isClosed():
            a0 = e.FirstParameter
            for lo, hi in ((a0, a0 + 3.141592653589793), (a0 + 3.141592653589793, a0 + 6.283185307179586)):
                edges.append(Part.ArcOfCircle(c, lo, hi).toShape())
        else:
            edges.append(e)
    return Part.Wire(Part.__sortEdges__(edges))


def _with_small_circles(shape):
    try:
        return any(type(e.Curve).__name__ == "Circle" and e.isClosed() and e.Curve.Radius <= 2.0 for e in shape.Edges)
    except Exception:
        return False


def offset2d(shape, d, join=2, fill=False, open_result=False):
    """shape.makeOffset2D(d, join, fill, open_result), a lone circle done by its radius (same kind of shape back:
    a wire for a wire, a face for a face); a small full circle inside a face or wire (a hole of r 1 or less crashed
    OCC too) split into two half arcs first."""
    e = _lone_circle(shape)
    if e is None:
        if _with_small_circles(shape):
            if shape.ShapeType == "Face":
                wires = [_halved(w) for w in shape.Wires]
                shape = Part.Face(wires[0]) if len(wires) == 1 else Part.makeFace(wires, "Part::FaceMakerBullseye")
            elif shape.ShapeType == "Wire":
                shape = _halved(shape)
            else:  # a compound, a shell, a bare edge holding a small circle: OCC crashed there too
                raise ValueError("This outline can't be offset as one piece: offset its shapes one at a time")
        return shape.makeOffset2D(d, join, fill, open_result)
    c = e.Curve
    r = c.Radius + d
    if r <= 1e-7:
        raise ValueError(TOO_SMALL)
    wire = Part.Wire(Part.makeCircle(r, c.Center, c.Axis))
    return Part.Face(wire) if shape.ShapeType == "Face" else wire
