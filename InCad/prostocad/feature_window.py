"""The feature windows (Thread, Extrude, Hole, Revolve, Fillet, Chamfer, Shell, Boolean): rounded groups, plain words,
no icons (the user: this CAD is not for idiots, and icons only make windows bigger). Every colour comes from the theme
that is on: the dark themes step from their own panel colour and the selected word has a thin orange line with a faint
orange fill; the light themes use their own field and accent colours. Nothing builds a solid yet: there is no geometry
engine in the program, so OK and Apply + only say so."""
import math
from dataclasses import dataclass, replace

from PySide6 import QtCore, QtGui, QtWidgets

from prostocad import theme, units
from prostocad.sketch import NAME, PLAIN_NUMBER, DimDot, _eval

Qt = QtCore.Qt


@dataclass(frozen=True)
class F:
    key: str
    label: str
    kind: str              # pick | combo | choice | value | check | points | slider
    choices: tuple = ()    # ((value, words), …)
    default: object = None
    show_if: tuple = ()    # ((other key, (values…)), …): all must hold
    unit: str = "mm"
    step: float = 1.0
    blank: bool = False    # a value that may stay empty: empty reads 0, a number must be above zero
    signed: bool = False   # below zero is the other side (Text's distance from its curve); a length stops at 0 (old program)
    same_row: bool = False  # shown in the row of the field before it, its name in front of it (Work Plane's X Y Z)
    ordered: bool = True   # a list: ▲▼ reorder it (Loft sections); False: ✕ per row and Clear (Fillet edges)
    hint: str = ""         # a pick's words while it is empty, when PICK_WORDS has none for its key
    whole: bool = True     # a number without a unit: a count, whole, at least 1; False: any number (a gear's Ratio)


ROLL = (("none", "None"), ("start", "Start"), ("both", "Both ends"))
EXTENTS = (("distance", "Distance"), ("to_next", "To Next"), ("through_all", "Through All"), ("to", "To"))
HANDS = (("right", "Right hand"), ("left", "Left hand"))
STANDARDS = (("iso", "ISO metric (DIN 13)"), ("iso_fine", "ISO fine (DIN 13)"), ("unc", "UNC"), ("unf", "UNF"),
             ("g", "G / BSP (ISO 228)"), ("npt", "NPT"), ("tr", "Trapezoidal (DIN 103)"), ("acme", "ACME"),
             ("rd", "Round (DIN 405)"), ("s", "Buttress (DIN 513)"), ("print", "Print-friendly"), ("custom", "Custom"))
_CUSTOM = (("standard", ("custom",)),)
_TAPPED = (("hole_type", ("tapped",)),)
_THREADED = (("hole_type", ("clearance", "tapped")),)
_TIP = (("depth_type", ("distance", "to")), ("hole_type", ("simple", "clearance", "tapped")))

WINDOWS = {
    "Thread": (
        (F("face", "Face", "pick"), F("start_face", "Start face", "pick"), F("offset", "Offset", "value", default=0.0)),
        (F("standard", "Standard", "combo", STANDARDS, "iso"),
         F("size", "Size", "combo", (("auto", "Auto (from face)"),), "auto",
           (("standard", tuple(k for k, _w in STANDARDS if k != "custom")),)),
         F("custom_profile", "Profile", "choice", (("triangle", "Triangle"), ("trapezoid", "Trapezoid"),
                                                   ("semicircle", "Semicircle")), "triangle", _CUSTOM),
         F("custom_pitch", "Pitch", "value", default=1.5, show_if=_CUSTOM, step=0.1),
         F("custom_angle", "Flank angle", "value", default=60.0, unit="°",
           show_if=_CUSTOM + (("custom_profile", ("triangle", "trapezoid")),)),
         F("custom_depth", "Depth", "value", default=0.9, show_if=_CUSTOM, step=0.1),
         F("build", "Thread", "choice", (("cut", "Cut into"), ("grow", "Grow onto")), "cut"),
         F("hand", "Hand", "choice", HANDS, "right")),
        (F("length_mode", "Length", "choice", (("full", "Full length"), ("distance", "Distance")), "full"),
         F("length", "Distance", "value", default=10.0, show_if=(("length_mode", ("distance",)),)),
         F("flip", "Flip direction", "check", default=False)),
        (F("chamfer", "Chamfer", "choice", ROLL, "none"),
         F("chamfer_size", "Chamfer size", "value", default=0.5, show_if=(("chamfer", ("start", "both")),), step=0.1),
         F("clearance", "Print clearance", "value", default=0.0, unit="mm on Ø", step=0.05),
         F("remember", "Save for the next part", "check", default=False))),
    "Extrude": (
        (F("operation", "Operation", "choice", (("auto", "Auto"), ("join", "Join"), ("cut", "Cut"),
                                                ("intersect", "Intersect"), ("new_solid", "New Solid")), "auto"),),
        (F("extent", "Extents", "choice", EXTENTS, "distance"),
         F("to_face", "To", "pick", show_if=(("extent", ("to",)),)),
         F("distance", "Distance", "value", default=10.0, show_if=(("extent", ("distance",)),), signed=True),  # through 0: the other way (the user)
         F("direction", "Direction", "choice", (("default", "Forward"), ("flip", "Flipped"), ("symmetric", "Symmetric"),
                                                ("two_sides", "Two sides")), "default"),
         F("extent2", "Extents 2", "choice", EXTENTS, "distance", (("direction", ("two_sides",)),)),
         F("to_face2", "To 2", "pick", show_if=(("direction", ("two_sides",)), ("extent2", ("to",)))),
         F("distance2", "Distance 2", "value", default=5.0, show_if=(("direction", ("two_sides",)), ("extent2", ("distance",)))),
         F("taper", "Taper", "value", default=0.0, unit="°"))),
    "Hole": (
        (F("placement", "Placement", "choice", (("sketch", "From Sketch"), ("concentric", "Concentric")), "sketch"),
         F("sketch", "Sketch", "pick", show_if=(("placement", ("sketch",)),)),
         F("edge", "Round", "pick", show_if=(("placement", ("concentric",)),)),
         F("face", "Face", "pick", show_if=(("placement", ("concentric",)),))),
        (F("hole_type", "Hole", "choice", (("simple", "Simple"), ("clearance", "Clearance"), ("tapped", "Tapped"),
                                           ("insert", "Insert")), "simple"),
         F("thread_standard", "Standard", "combo", (("iso", "ISO metric"), ("iso_fine", "ISO metric fine"), ("unc", "UNC"),
                                                    ("unf", "UNF"), ("unef", "UNEF"), ("npt", "NPT"), ("g", "G / BSP"),
                                                    ("bsw", "BSW"), ("bsf", "BSF")), "iso", _THREADED),
         F("thread_size", "Size", "combo", (("", "Sizes not in yet"),), "", _THREADED),
         F("insert_size", "Insert", "combo", (("", "Sizes not in yet"),), "", (("hole_type", ("insert",)),)),
         F("fit", "Fit", "choice", (("close", "Close"), ("normal", "Normal"), ("loose", "Loose")), "normal",
           (("hole_type", ("clearance",)),)),
         F("diameter", "Diameter", "value", default=5.0, show_if=(("hole_type", ("simple",)),)),
         F("seat", "Seat", "combo", (("none", "None (drilled)"), ("counterbore", "Counterbore"),
                                     ("countersink", "Countersink"), ("spotface", "Spotface")), "none"),
         F("cbore_diameter", "C'bore Ø", "value", default=10.0, show_if=(("seat", ("counterbore",)),)),
         F("cbore_depth", "C'bore depth", "value", default=3.0, show_if=(("seat", ("counterbore",)),)),
         F("csink_diameter", "C'sink Ø", "value", default=10.0, show_if=(("seat", ("countersink",)),)),
         F("csink_angle", "C'sink angle", "value", default=90.0, unit="°", show_if=(("seat", ("countersink",)),)),
         F("spot_diameter", "Spotface Ø", "value", default=10.0, show_if=(("seat", ("spotface",)),)),
         F("spot_depth", "Spotface depth", "value", default=0.5, show_if=(("seat", ("spotface",)),), step=0.1)),
        (F("depth_type", "Termination", "choice", (("distance", "Distance"), ("through_all", "Through All"), ("to", "To")),
           "through_all"),
         F("depth", "Depth", "value", default=10.0,
           show_if=(("depth_type", ("distance",)), ("hole_type", ("simple", "clearance", "tapped")))),
         F("to_face", "To face", "pick", show_if=(("depth_type", ("to",)),)),
         F("tip", "Drill point", "choice", (("flat", "Flat"), ("angled", "Angle")), "angled", _TIP),
         F("tip_angle", "Point angle", "value", default=118.0, unit="°", show_if=_TIP + (("tip", ("angled",)),)),
         F("thread_depth", "Thread", "choice", (("hole", "Whole hole"), ("distance", "Depth")), "hole", _TAPPED),
         F("thread_length", "Thread depth", "value", default=8.0, show_if=_TAPPED + (("thread_depth", ("distance",)),)),
         F("clearance", "Print clearance", "value", default=0.0, unit="mm on Ø", show_if=_TAPPED, step=0.05),
         F("hand", "Direction", "choice", HANDS, "right", _TAPPED),
         F("flip", "Flip direction", "check", default=False),
         F("chamfer", "Chamfer", "choice", ROLL, "none"),
         F("chamfer_size", "Chamfer size", "value", default=0.5, show_if=(("chamfer", ("start", "both")),), step=0.1))),
    "Revolve": (
        (F("operation", "Operation", "choice", (("join", "Join"), ("cut", "Cut"), ("new_solid", "New Solid")), "join"),),
        (F("axis_mode", "Axis", "combo", (("reference", "Line, axis or edge"), ("construction", "Line of the sketch"),
                                          ("two_points", "Two points"), ("V_Axis", "Sketch vertical"),
                                          ("H_Axis", "Sketch horizontal")), "reference"),
         F("axis_construction", "Line", "combo", (("", "No lines yet"),), "", (("axis_mode", ("construction",)),)),
         F("axis_ref", "Pick", "pick", show_if=(("axis_mode", ("reference",)),)),
         F("axis_points", "Points", "pick", show_if=(("axis_mode", ("two_points",)),))),
        (F("extent", "Extents", "choice", (("full", "Full"), ("angle", "Angle"), ("two_angles", "Two angles")), "full"),
         F("angle", "Angle", "value", default=90.0, unit="°", show_if=(("extent", ("angle", "two_angles")),)),
         F("angle2", "Angle 2", "value", default=90.0, unit="°", show_if=(("extent", ("two_angles",)),)),
         F("flip", "Flip", "check", default=False, show_if=(("extent", ("angle",)),)))),
    "Scale / Transparency": (
        (F("points", "Points", "points"),
         F("align", "Line", "choice", (("as_is", "As is"), ("horizontal", "Horizontal"), ("vertical", "Vertical")), "as_is"),
         F("length", "Real length", "value", default=0.0, blank=True)),
        (F("transparency", "Transparency", "slider", (0, 90), 0),),
    ),
    "Sketch Fillet": ((F("radius", "Radius", "value", default=2.0, step=0.5),),),
    "Sketch Chamfer": (
        (F("type", "Type", "choice", (("equal", "Distance"), ("two", "Two distances"), ("angle", "Distance + angle")), "equal"),
         F("size", "Distance", "value", default=1.0, step=0.5),
         F("size2", "Distance 2", "value", default=1.0, show_if=(("type", ("two",)),), step=0.5),
         F("angle", "Angle", "value", default=45.0, unit="°", show_if=(("type", ("angle",)),), step=5.0)),),
    "Along Path": (
        (F("geometry", "Shapes", "pick"), F("path", "Path", "pick")),
        (F("mode", "Mode", "choice", (("forward", "Forward"), ("both", "Both ways")), "forward"),
         F("count1", "Count", "value", default=3.0, unit="", step=1.0),
         F("fit", "Spread", "choice", (("spacing", "By spacing"), ("fit", "Fit to path")), "spacing"),
         F("spacing1", "Spacing", "value", default=10.0, show_if=(("fit", ("spacing",)),)),
         F("orient", "Rotation", "choice", (("identical", "Without"), ("follow", "With the curve")), "identical"),
         F("flip1", "Flip", "check", default=False, show_if=(("mode", ("forward",)),)),
         F("start", "Start", "pick", show_if=(("mode", ("both",)), ("fit", ("spacing",)))),
         F("base", "Base Point", "pick")),
        (F("excluded", "Left out", "pick"),)),
    "Circular Pattern": (
        (F("geometry", "Shapes", "pick"), F("axis", "Centre", "pick")),
        (F("mode", "Mode", "choice", (("around", "Around"), ("between", "Between")), "around"),
         F("count1", "Count", "value", default=4.0, unit="", step=1.0),
         F("fit", "Spacing", "choice", (("fitted", "Whole angle"), ("between", "Step")), "fitted", show_if=(("mode", ("around",)),)),
         F("sweep", "Angle", "value", default=360.0, unit="°", show_if=(("mode", ("around",)),), step=15.0),
         F("flip1", "Flip", "check", default=False, show_if=(("mode", ("around",)),)),
         F("from1", "From", "pick", show_if=(("mode", ("between",)),)),
         F("to1", "To", "pick", show_if=(("mode", ("between",)),))),
        (F("excluded", "Left out", "pick"),)),
    "Rectangular Pattern": (
        (F("geometry", "Geometry", "pick"),),
        (F("fit1", "Distribution", "choice", (("spacing", "By spacing"), ("equal", "Equal spacing")), "spacing"),
         F("dir1", "Direction 1", "pick", show_if=(("fit1", ("spacing",)),)),
         F("flip1", "Flip direction", "check", default=False),
         F("count1", "Count", "value", default=2.0, unit="", step=1.0),
         F("spacing1", "Spacing", "value", default=10.0, show_if=(("fit1", ("spacing",)),)),
         F("from1", "From", "pick", show_if=(("fit1", ("equal",)),)),
         F("to1", "To", "pick", show_if=(("fit1", ("equal",)),))),
        (F("two", "Two directions", "check", default=False, show_if=(("fit1", ("spacing",)),)),
         F("dir2", "Direction 2", "pick", show_if=(("two", (True,)), ("fit1", ("spacing",)))),
         F("count2", "Count 2", "value", default=2.0, unit="", show_if=(("two", (True,)), ("fit1", ("spacing",))), step=1.0),
         F("spacing2", "Spacing 2", "value", default=10.0, show_if=(("two", (True,)), ("fit1", ("spacing",))))),
        (F("excluded", "Left out", "pick"),)),
    "Polygon": (
        (F("sides", "Sides", "value", default=6.0, unit="", step=1.0),
         F("circle", "Circle", "choice", (("inside", "Inside"), ("through", "Through corners")), "inside"),
         F("diameter", "Diameter", "value", default=0.0, show_if=(("mode", ("draw",)),)),
         F("turn", "Angle", "value", default=0.0, unit="°", show_if=(("mode", ("draw",)),), step=15.0)),),
    "Text": (
        (F("text", "Text", "text", default="Text"),
         F("placement", "Placement", "choice", (("box", "In a box"), ("curve", "Along a curve")), "box"),
         F("frame", "Frame", "choice", (("rect", "Rectangle"), ("ellipse", "Ellipse"), ("circle", "Circle")), "rect",
           (("placement", ("box",)),)),
         F("curve", "Curve", "pick", show_if=(("placement", ("curve",)),))),
        (F("font", "Font", "combo", (("Arial", "Arial"),), "Arial"), F("bold", "Bold", "check", default=False),
         F("italic", "Italic", "check", default=False), F("mirror", "Mirror", "check", default=False), F("size", "Size", "value", default=5.0, show_if=(("fit", (False,)),), step=0.5)),
        (F("halign", "Horizontal", "choice", (("left", "Left"), ("center", "Centre"), ("right", "Right")), "center",
           (("placement", ("box",)),)),
         F("valign", "Vertical", "choice", (("top", "Top"), ("middle", "Middle"), ("bottom", "Bottom")), "middle",
           (("placement", ("box",)),)),
         F("fit", "Fit to the box", "check", default=False, show_if=(("placement", ("box",)),)),
         F("position", "Position", "choice", (("start", "Start"), ("center", "Centre"), ("end", "End")), "center",
           (("placement", ("curve",)),)),
         F("shift", "Shift", "value", default=0.0, show_if=(("placement", ("curve",)),), step=0.5),
         F("distance", "Distance", "value", default=0.0, show_if=(("placement", ("curve",)),), step=0.5, signed=True),
         F("flip", "Flip side", "check", default=False, show_if=(("placement", ("curve",)),)),
         F("spacing", "Letter spacing", "value", default=0.0, step=0.1))),
    "Fillet": (
        (F("edges", "Edges", "pick"),),
        (F("radius", "Radius", "value", default=0.5, step=0.1), F("full", "Full", "check", default=False))),
    "Chamfer": (
        (F("edges", "Edges", "pick"),),
        (F("type", "Type", "choice", (("equal", "Distance"), ("two", "Two distances"), ("angle", "Distance + angle")), "equal"),
         F("size", "Distance", "value", default=0.5, step=0.1),
         F("size2", "Distance 2", "value", default=0.5, show_if=(("type", ("two",)),), step=0.1),
         F("angle", "Angle", "value", default=45.0, unit="°", show_if=(("type", ("angle",)),)),
         F("flip", "Flip", "check", default=False, show_if=(("type", ("two", "angle")),)))),
    "Shell": (
        (F("closed", "No opening", "check", default=False),
         F("faces", "Remove faces", "pick", show_if=(("closed", (False,)),))),
        (F("thickness", "Thickness", "value", default=1.0, step=0.1),
         F("direction", "Direction", "choice", (("inside", "Inside"), ("outside", "Outside")), "inside"))),
    "Boolean": (
        (F("base", "Base", "pick"), F("tools", "Tools", "pick")),
        (F("operation", "Operation", "choice", (("join", "Join"), ("cut", "Cut"), ("intersect", "Intersect")), "cut"),
         F("clearance", "Clearance", "value", default=0.0, unit="mm per side", show_if=(("operation", ("cut",)),), step=0.05),
         F("apply_to", "Clearance on", "choice", (("cavity", "Cavity"), ("tool", "Tool"), ("split", "Split")), "cavity",
           (("operation", ("cut",)),)),
         F("keep_tool", "Keep tool", "check", default=False))),
}

PICK_WORDS = {"curve": "Click a curve…", "face": "Click a face…", "faces": "Click faces…", "edges": "Click edges…", "tools": "Click the bodies…",
              "base": "Click a body…", "sketch": "Click a sketch…", "edge": "Click a round edge…",
              "axis_points": "Click two points…", "axis_ref": "Click an axis…", "geometry": "Click the shapes…",
              "axis": "Click the centre…", "path": "Click the curve…", "start": "Click on the curve…", "base": "First point", "dir1": "X axis", "dir2": "Y axis", "from1": "Click a point…", "to1": "Click a point…", "excluded": "Click a copy…"}
# what the strip over the tree says for each window: the step, then what else helps
HELP = {"Along Path": ("Click the shapes, then the curve", "Both ways: click where on the curve they start from", "Base Point: the point of the shape that sits on the curve"),
        "Circular Pattern": ("Click the shapes, then the centre", "Click an arc: the copies lie along it", "Between: click the two points they lie between"),
        "Rectangular Pattern": ("Click the shapes, the copies show at once", "Left out: click a copy, a dashed one comes back", "Spacing: in Parameters for formulas"),
        "Sketch Fillet": ("Click a corner, or two lines, or draw a box round corners", "Radius: for all the corners of this run", "Space: Chamfer"),
        "Sketch Chamfer": ("Click a corner, or two lines, or draw a box round corners", "Distance: for all the corners of this run", "Space: Fillet"),
        "Scale / Transparency": ("Click two points of the image, then type the real length", "Line: Horizontal or Vertical turns it about the first point",
                                 "Transparency: how see-through it is, kept with OK alone"),
        "Polygon": ("Change the sides: it changes at once", "Circle: inside it, or through its corners"),
        "Text": ("Type the words", "Size: the height of a capital letter", "Double click on the box: back here"),
        "Thread": ("Click the round face, then the face the thread starts from",
                   "Standard and Size: Auto reads the size from the face", "Print clearance: added on the diameter"),
        "Extrude": ("Choose the profile, then how far it goes", "Operation: Auto joins or cuts by the direction",
                    "Two sides: a distance each way"),
        "Hole": ("Choose the points of a sketch, or a round edge and a face", "Hole: Simple, Clearance, Tapped or Insert",
                 "Seat: counterbore, countersink or spotface"),
        "Revolve": ("Choose the profile, then the axis", "Axis: a line, an edge, two points or a sketch axis",
                    "Extents: Full, one angle or two"),
        "Fillet": ("Click the edges to round", "Radius: the same on every edge picked"),
        "Chamfer": ("Click the edges to cut", "Type: one distance, two, or a distance and an angle"),
        "Shell": ("Click the faces to remove", "No opening: a closed hollow part", "Thickness goes Inside or Outside"),
        "Boolean": ("Click the base body, then the tools", "Clearance: for parts that must fit, on a Cut",
                    "Keep tool: the tool stays in the part")}

COMMANDS = {"feature.thread": "Thread", "feature.extrude": "Extrude", "feature.hole": "Hole", "feature.revolve": "Revolve",
            "feature.fillet": "Fillet", "feature.chamfer": "Chamfer", "feature.shell": "Shell", "feature.combine": "Boolean"}


def _mix(a, b, t):
    a, b = QtGui.QColor(a), QtGui.QColor(b)
    return QtGui.QColor(*[round(x + (y - x) * t) for x, y in zip(a.getRgb()[:3], b.getRgb()[:3])]).name()


FONTS = None  # the Text window's font list, if a host gives its own (ProstoCAD 0.18 New Sketch: the fonts the part can
# build its letters from); otherwise the computer's fonts as Qt knows them


def _ok_text(p):
    c = QtGui.QColor(p["accent"])
    return "#10141a" if 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue() > 170 else "#ffffff"


def colours(p):
    """The window's colours from the theme. A dark theme steps from its own panel colour, so Warm stays warm and the
    blackest themes step up, never into black wells (the user: the black fields were FreeCAD)."""
    if p["name"] == "light":
        return dict(win=p["window"], card=p["panel"], card_line=p["border"], fld=p["field"], fld_line=p["border"],
                    track="transparent", tile=p["field"], tile_line=p["border"], line=p["edge_line"],
                    on=p["accent_soft"], on_line=p["accent"])
    b, t, o = p["panel"], p["text"], p["construction"]
    up = lambda x: _mix(b, t, x)
    dn = lambda x: _mix(b, "#000000", x)
    if QtGui.QColor(b).lightness() < 40:
        card, fld, line = up(.07), up(.17), up(.26)
    else:
        card, fld, line = dn(.16), up(.12), dn(.38)
    return dict(win=b, card=card, card_line=card, fld=fld, fld_line=fld, track=fld, tile="transparent",
                tile_line="transparent", line=line, on=_mix(fld, o, .12), on_line=o)


def list_row():
    """A row's height in every list: as far apart as the rows of a feature window."""
    return QtGui.QFontMetrics(QtWidgets.QApplication.font()).height() + 14  # the step between rows in Fillet and the pattern windows


def fit_rows(win, table, rows, most=12):
    """A list window is as tall as its rows, one empty row at the least; past `most` rows the list scrolls."""
    head = table.horizontalHeader() if isinstance(table, QtWidgets.QTableView) else table.header()
    table.setFixedHeight(head.sizeHint().height() + list_row() * max(1, min(rows, most)) + 2 * table.frameWidth() + 2)
    for w in (table.parentWidget(), win):  # the new height climbs to the window by posted events: take them now
        QtWidgets.QApplication.sendPostedEvents(w, QtCore.QEvent.LayoutRequest)
    win.adjustSize()


def list_style(p):
    """A list or a table inside a window: the same ticks, lines and chosen colour as the rest of it."""
    c = colours(p)
    return f"""
QAbstractItemView {{ font-size: {QtWidgets.QApplication.font().pixelSize()}px; background: transparent; color: {p['text']}; border: none; outline: none; gridline-color: {c['line']}; }}
QAbstractItemView::item {{ border: none; padding: 2px 0; }}
QAbstractItemView::item:selected {{ background: {c['on']}; color: {p['text']}; }}
QAbstractItemView::indicator {{ width: 13px; height: 13px; background: {c['fld']}; border: 1px solid {c['line']}; border-radius: 3px; }}
QAbstractItemView::indicator:checked {{ background: {c['on_line']}; border-color: {c['on_line']}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: transparent; color: {p['text_dim']}; border: none; border-bottom: 1px solid {c['line']}; padding: 2px 5px; }}
QTableCornerButton::section {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; border: none; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0; border: none; }}
QScrollBar::handle {{ background: {c['fld']}; border-radius: 4px; min-height: 24px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; border: none; width: 0; height: 0; }}"""


def qss(p):
    c = colours(p)
    return f"""
QFrame#FwWin {{ background: {c['win']}; border: 1px solid {c['line']}; border-radius: 10px; }}
QDialog#FwWin {{ background: {c['win']}; }}
QLineEdit#FwFind {{ background: {c['fld']}; border: 1px solid {c['fld_line']}; border-radius: 6px; padding: 2px 7px; color: {p['text']}; }}
QLineEdit#FwFind:focus {{ border-color: {p['accent']}; }}
QFrame#FwHead {{ background: transparent; border: none; border-bottom: 1px solid {c['line']}; }}
QLabel#FwTitle {{ color: {p['text']}; font-weight: bold; background: transparent; }}
QFrame#FwCard {{ background: {c['card']}; border: 1px solid {c['card_line']}; border-radius: 8px; }}
QLabel#FwLabel {{ color: {p['text_dim']}; background: transparent; }}
QFrame#FwTrack {{ background: {c['track']}; border: none; border-radius: 6px; }}
QPushButton#FwTile {{ background: {c['tile']}; border: 1px solid {c['tile_line']}; border-radius: 4px; color: {p['text_dim']};
    padding: 0 1px; }}
QPushButton#FwTile:hover {{ color: {p['text']}; }}
QPushButton#FwTile:checked {{ background: {c['on']}; border: 1px solid {c['on_line']}; color: {p['text']}; }}
QPushButton#FwTile:disabled {{ color: {_mix(c['win'], p['text_dim'], .45)}; }}
QFrame#FwValue:disabled, QComboBox#FwCombo:disabled, QPushButton#FwPick:disabled {{ color: {p['text_dim']}; }}
QLineEdit#FwValueEdit:disabled {{ color: {p['text_dim']}; }}
QPushButton#FwRowButton {{ background: transparent; border: none; color: {p['text_dim']}; padding: 0; }}
QPushButton#FwRowButton:hover {{ color: {p['text']}; }}
QPushButton#FwRowButton:disabled {{ color: {_mix(c['win'], p['text_dim'], .35)}; }}
QLabel#FwRow {{ color: {p['text']}; background: transparent; }}
QLabel#FwInfo {{ color: {p['text_dim']}; background: transparent; padding: 6px 10px 0 10px; }}
QLabel#FwInfo a {{ color: {p['accent']}; }}
QLabel#FwHeading {{ color: {p['accent']}; font-weight: bold; background: transparent; }}
QPushButton#FwPick {{ background: {c['fld']}; border: 1px solid {c['fld_line']}; border-radius: 6px; text-align: left;
    padding: 0 7px; color: {p['text_dim']}; }}
QPushButton#FwPick[active="true"] {{ border-color: {p['accent']}; }}
QPlainTextEdit#FwText {{ background: {c['fld']}; border: 1px solid {c['fld_line']}; border-radius: 6px; color: {p['text']};
    selection-background-color: {p['accent']}; }}
QPlainTextEdit#FwText:focus {{ border-color: {p['accent']}; }}
QComboBox#FwCombo, QFrame#FwValue {{ background: {c['fld']}; border: 1px solid {c['fld_line']}; border-radius: 6px;
    color: {p['text']}; selection-background-color: {p['accent']}; }}
QFrame#FwValue[wrong="true"] {{ border-color: {p['end_of_part']}; }}
QLineEdit#FwValueEdit {{ background: transparent; border: none; padding: 0; color: {p['text']}; selection-background-color: {p['accent']}; }}
QComboBox#FwCombo {{ padding: 0 7px; }}
QFrame#FwValue[typing="true"] {{ border-color: {p['accent']}; }}
QComboBox#FwCombo::drop-down {{ border: none; width: 16px; }}
QComboBox#FwCombo::down-arrow {{ image: none; }}
QComboBox#FwCombo QAbstractItemView {{ background: {c['fld']}; border: 1px solid {c['line']}; color: {p['text']};
    selection-background-color: {c['on']}; selection-color: {p['text']}; outline: 0; }}
QCheckBox#FwCheck {{ color: {p['text_dim']}; background: transparent; spacing: 7px; }}
QCheckBox#FwCheck::indicator {{ width: 13px; height: 13px; background: {c['fld']}; border: 1px solid {c['line']}; border-radius: 3px; }}
QCheckBox#FwCheck::indicator:checked {{ background: {c['on_line']}; border-color: {c['on_line']}; }}
QPushButton#FwButton {{ background: {c['fld']}; border: 1px solid {c['fld_line']}; border-radius: 6px; color: {p['text']};
    padding: 4px 11px; }}
QSlider#FwSlider::groove:horizontal {{ background: {c['fld']}; border: 1px solid {c['fld_line']}; height: 4px; border-radius: 2px; }}
QSlider#FwSlider::handle:horizontal {{ background: {p['accent']}; border: none; width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; }}
QPushButton#FwOk:disabled {{ background: {c['fld']}; border: 1px solid {c['fld_line']}; color: {p['text_dim']}; }}
QPushButton#FwButton:disabled {{ color: {p['text_dim']}; }}
QPushButton#FwOk {{ background: {p['accent']}; border: 1px solid {p['accent']}; border-radius: 6px; color: {_ok_text(p)};
    font-weight: bold; padding: 4px 18px; }}
"""


class Combo(QtWidgets.QComboBox):
    """The style sheet takes the arrow away, so it is drawn here, in the theme's dim text colour."""

    def wheelEvent(self, e):
        e.ignore()  # the window's wheel never changes a choice by accident

    elide = False  # a long name (a font's) shown shortened in the middle, whole when the pointer rests on it

    def paintEvent(self, e):
        if self.elide:
            p = QtWidgets.QStylePainter(self)
            o = QtWidgets.QStyleOptionComboBox()
            self.initStyleOption(o)
            o.currentText = self.fontMetrics().elidedText(o.currentText, Qt.ElideMiddle, self.width() - 30)
            p.drawComplexControl(QtWidgets.QStyle.CC_ComboBox, o)
            p.drawControl(QtWidgets.QStyle.CE_ComboBoxLabel, o)
            p.end()
        else:
            super().paintEvent(e)
        g = QtGui.QPainter(self)
        g.setRenderHint(QtGui.QPainter.Antialiasing)
        g.setPen(Qt.NoPen)
        g.setBrush(QtGui.QColor(theme.cur()["text_dim"]))
        x, y = self.width() - 13.0, self.height() / 2 - 1.5
        g.drawPolygon([QtCore.QPointF(x, y), QtCore.QPointF(x + 7, y), QtCore.QPointF(x + 3.5, y + 4)])
        g.end()


class Value(QtWidgets.QFrame):
    """A size as the sketch's dimension box has it: type a number or a formula (d0/2), Enter or leaving the field applies;
    the dot pulled left or right goes by whole numbers, the wheel by Settings' step, ↑ ↓ by the field's step."""
    valueChanged = QtCore.Signal(float)

    def __init__(self, field):
        super().__init__()
        self.setObjectName("FwValue")
        self.field = field
        self.number = float(field.default)
        self.expr = None
        self.px = self.roll = 0
        self.least = None if field.signed or field.key in ("offset", "shift", "spacing", "turn", "sweep") else 0.0
        self.held = False  # typed or pulled: it no longer follows the pointer
        r = QtWidgets.QHBoxLayout(self)
        r.setContentsMargins(7, 0, 4, 0)
        r.setSpacing(2)
        self.edit = QtWidgets.QLineEdit()
        self.edit.setObjectName("FwValueEdit")
        self.edit.setFrame(False)
        self.edit.installEventFilter(self)
        self.edit.returnPressed.connect(self.apply)
        r.addWidget(self.edit, 1)
        self.dot = DimDot(self)
        self.dot.pulled.connect(self._pulled)
        self.setFocusProxy(self.edit)
        r.addWidget(self.dot)
        self._show()

    def value(self):
        return self.number

    def setValue(self, v):
        self._take(float(v), None)

    def lineEdit(self):
        return self.edit

    def set_live(self, v):
        """What the pointer makes it, while nothing is held here."""
        if not self.held and not self.edit.hasFocus():
            self.number = v
            self._show()

    def release(self):
        self.held = False

    def set_expr(self, expr):
        self.expr = expr or None
        self._show()

    def _show(self):
        v = self.number / self._k()
        word = self.expr if self.expr else units.number(v) if self._long() else "%.2f" % v
        if not self.expr and "/" not in word:
            word = word.rstrip("0").rstrip(".")
        if self.field.blank and not self.expr and not self.number:
            self.edit.setText("")
        else:
            self.edit.setText(word if self.edit.hasFocus() else word + ("" if self.expr else " " + self._word()))
        self._mark(False)

    def _long(self):
        """A length: it goes by the file's units; an angle or a count does not."""
        return "mm" in self.field.unit

    def _k(self):
        return units.k() if self._long() else 1.0

    def _word(self):
        return self.field.unit.replace("mm", units.word())

    def _mark(self, wrong):
        self.setProperty("wrong", wrong)
        self.style().unpolish(self)
        self.style().polish(self)

    def _names(self):
        w = self.parentWidget()
        while w is not None and not hasattr(w, "_dim_env"):
            w = w.parentWidget()
        return w._dim_env() if w is not None else {}

    def _take(self, v, expr):
        if not self.field.unit and self.field.whole:  # a count: whole, never less than one
            v = float(max(1, round(v)))
        self.expr = expr
        self.held = True
        changed = abs(v - self.number) > 1e-12
        self.number = v
        self._show()
        if changed or expr:
            self.valueChanged.emit(v)

    def apply(self):
        """What was typed: a plain number, or a formula on names; wrong: the edge goes red, nothing changes."""
        text = self.edit.text().strip()
        for tail in (self._word(),):
            if tail and text.endswith(tail):
                text = text[:-len(tail)].strip()
        if not text:
            if self.field.blank:
                self._take(0.0, None)
            self._show()
            return
        plain = PLAIN_NUMBER.fullmatch(text) is not None
        ready = units.literals(text)
        try:
            v = float(text.replace(",", ".")) if plain else _eval(ready, self._names())
        except (ValueError, KeyError):
            v = None
        if v is not None:
            v *= self._k()
        if v is None or (self.least is not None and v < self.least) or (self.field.blank and v <= 0):
            self._mark(True)
            return
        self._take(float(v), None if plain else text)

    def _step(self, step):
        v = self.number + step * self._k()
        if self.least is not None:
            v = max(self.least, v)
        self._take(v, None)

    def _pulled(self, n):
        """The dot: whole numbers, one per 5 px, as on a dimension."""
        self.px += n
        steps = int(self.px / 5)
        if steps:
            self.px -= steps * 5
            now = self.number / self._k()
            self._step((math.floor(now + 1e-9) if steps > 0 else math.ceil(now - 1e-9)) + steps - now)

    def wheelEvent(self, e):
        """The wheel: tenths or hundredths by Settings, as on a dimension."""
        self.roll += e.angleDelta().y()
        steps = int(self.roll / 120)
        if steps:
            self.roll -= steps * 120
            w = self.parentWidget()
            while w is not None and not hasattr(w, "dim_look"):
                w = w.parentWidget()
            self._step(steps if not self.field.unit else steps * (units.wheel(float(w.dim_look.get("wheel", "0.1"))) if w is not None else 0.1))
        e.accept()

    def eventFilter(self, obj, e):
        t = e.type()
        if t in (QtCore.QEvent.FocusIn, QtCore.QEvent.FocusOut):
            self.setProperty("typing", t == QtCore.QEvent.FocusIn)
            self.style().unpolish(self)
            self.style().polish(self)
        if t == QtCore.QEvent.FocusIn:
            QtCore.QTimer.singleShot(0, self._begin)
        elif t == QtCore.QEvent.FocusOut:
            self.apply()
            if not self.property("wrong"):
                self._show()
        elif t == QtCore.QEvent.KeyPress:
            if e.key() == Qt.Key_Space:  # a window may use Space, as the sketch does
                w = self.parentWidget()
                while w is not None and not hasattr(w, "space"):
                    w = w.parentWidget()
                if w is not None and w.space is not None:
                    self.apply()
                    w.space()
                    return True
            if e.key() in (Qt.Key_Up, Qt.Key_Down):
                self.apply()
                step = units.step(self.field.step) if self._long() else self.field.step
                self._step(step if e.key() == Qt.Key_Up else -step)
                return True
            if e.text() in ("-", "+") and PLAIN_NUMBER.fullmatch(self.edit.text()) and self.edit.text().lstrip("+-") and self.least is None:
                self.edit.setText(("-" if e.text() == "-" else "") + self.edit.text().lstrip("+-"))  # the sign, wherever the cursor is
                return True
        return False

    def _begin(self):
        if self.edit.hasFocus():
            self._show()
            self.edit.selectAll()


class FeatureWindow(QtWidgets.QFrame):
    """One window over the view. Its rows come from WINDOWS; a row shows only while its show_if holds, and a group
    with nothing to show goes too, so the window is never bigger than what can be set now."""
    finished = QtCore.Signal(str, str)  # (window's name, "ok" | "apply" | "cancel")
    edited = QtCore.Signal()  # a value changed

    BOX_H = 23
    WIDTH = 286
    listEdited = QtCore.Signal(str, int, str)  # (list field, row, "up" | "down" | "remove" | "clear")

    def __init__(self, name, parent=None, values=None, groups=None, width=None, box_h=None, apply=None,
                 scroll=False, grow=False):
        """groups: the window's cards, each a tuple of F (a host's own window: ProstoCAD 0.18's old features); without
        them the window's own in WINDOWS. width, box_h: its size (px at the standard text size), smaller or larger
        than the standard 286 and 23. scroll: the cards scroll when the window would be taller than the view it
        stands in (the old program's windows: Spur Gears ran off the screen and took OK with it). apply: Apply + shown (True) or not (False); None: as the window's name says.
        grow: wider where a combo's longest words would be cut (the old program's windows: its combos took their
        longest words, Thread's "ISO metric (DIN 13)"); the others keep their width."""
        super().__init__(parent)
        self.name = name
        self.given = values or {}
        self.boxes = {}  # key -> the field's own widget (a Value, a Combo, a check box …)
        self.lists = {}  # key -> (rows' column, [row widgets])
        self.names = None  # a host's named values for formulas (d0 …), when the window is not in a sketch
        self.with_apply = apply
        self._rescue = None
        self._info_full, self._info_open = "", False
        self.setObjectName("FwWin")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.values, self.rows, self.cards, self.picks = {}, {}, [], {}
        self.space = None  # what Space does while typing in a size
        self.active = None
        self.on_active = None  # told which pick field is on
        self.tiles = {}
        self.drawing = False  # open while its tool draws
        self.on_points = None  # Undo (True) or Clear (False) of a points field
        self._drag = None
        px = QtWidgets.QApplication.font().pixelSize()
        self.scale = (px if px > 0 else theme.FONT_PX) / theme.FONT_PX
        self.fm = QtGui.QFontMetrics(QtWidgets.QApplication.font())
        self.full_w = round((width or self.WIDTH) * self.scale)
        groups = WINDOWS[name] if groups is None else groups
        self.label_w = max((self.fm.horizontalAdvance(f.label) for g in groups for f in g
                            if f.kind not in ("check", "heading") and not f.same_row), default=0) + 4
        self.box_h = max(box_h or self.BOX_H, self.fm.height() + (8 if box_h is None else 4))
        self.grow = grow
        if grow:
            words = [w for g in groups for f in g if f.kind == "combo" for _v, w in f.choices]
            self.full_w = max(self.full_w, self._width_for(words))
        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.setSizeConstraint(QtWidgets.QLayout.SetFixedSize)  # the height follows what shows; the cards hold the width
        col.addWidget(self._head())
        body = QtWidgets.QVBoxLayout()
        body.setContentsMargins(7, 7, 7, 0)
        body.setSpacing(7)
        for group in groups:
            card = QtWidgets.QFrame()
            card.setObjectName("FwCard")
            card.setFixedWidth(self.full_w - 14)
            c = QtWidgets.QVBoxLayout(card)
            c.setContentsMargins(8, 7, 8, 8)
            c.setSpacing(5)
            fields = []
            last = None  # the row a same_row field joins
            for f in group:
                if f.key in self.given:
                    f = replace(f, default=self.given[f.key])
                if name == "Text" and f.key == "font":
                    names = [k for k in (FONTS() if FONTS is not None else QtGui.QFontDatabase.families())
                             if not k.startswith(".") and not QtGui.QFontDatabase.isPrivateFamily(k)]  # hidden fonts left out
                    f = replace(f, choices=tuple((k, k) for k in names))
                if f.same_row and last is not None:
                    self.rows[f.key] = (f, self._joined(f, last))
                else:
                    self.rows[f.key] = (f, self._row(f))
                    c.addWidget(self.rows[f.key][1])
                    last = self.rows[f.key][1]
                fields.append(f.key)
            body.addWidget(card)
            self.cards.append((card, fields))
        self.area = None
        if scroll:
            inner = QtWidgets.QWidget()
            inner.setObjectName("FwBody")
            inner.setAttribute(Qt.WA_StyledBackground, False)
            inner.setLayout(body)
            self.area = QtWidgets.QScrollArea()
            self.area.setObjectName("FwArea")
            self.area.setFrameShape(QtWidgets.QFrame.NoFrame)
            self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.area.setWidgetResizable(True)
            self.area.setStyleSheet("QScrollArea#FwArea, QWidget#FwBody { background: transparent; }"
                                    "QScrollBar:vertical { background: transparent; width: 7px; margin: 0; }"
                                    "QScrollBar::handle:vertical { background: %s; border-radius: 3px; min-height: 24px; }"
                                    "QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page"
                                    " { background: transparent; border: none; height: 0; }" % theme.cur()["text_dim"])
            self.area.viewport().setAutoFillBackground(False)
            self.area.setWidget(inner)
            col.addWidget(self.area)
        else:
            col.addLayout(body)
        self.info_line = QtWidgets.QLabel()
        self.info_line.setObjectName("FwInfo")
        self.info_line.setWordWrap(True)
        self.info_line.setTextFormat(Qt.RichText)
        self.info_line.linkActivated.connect(self._fold_info)
        self.info_line.hide()
        col.addWidget(self.info_line)
        self.warn = QtWidgets.QLabel()
        self.warn.setObjectName("FwWarn")
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet("color: #ff6b5e; padding: 6px 10px 0 10px;")
        self.warn.hide()
        col.addWidget(self.warn)
        self.rescue_button = QtWidgets.QPushButton()
        self.rescue_button.setObjectName("FwButton")
        self.rescue_button.clicked.connect(lambda: self._rescue() if self._rescue is not None else None)
        self.rescue_button.hide()
        rb = QtWidgets.QHBoxLayout()
        rb.setContentsMargins(9, 6, 9, 0)
        rb.addWidget(self.rescue_button)
        rb.addStretch(1)
        col.addLayout(rb)
        col.addLayout(self._footer())
        self._first_pick()
        self._update_rows()

    def _head(self):
        h = QtWidgets.QFrame()
        h.setObjectName("FwHead")
        r = QtWidgets.QHBoxLayout(h)
        r.setContentsMargins(10, 4, 6, 4)
        t = QtWidgets.QLabel(self.name)
        t.setObjectName("FwTitle")
        r.addWidget(t)
        h.mousePressEvent = self._press
        h.mouseMoveEvent = self._move
        h.mouseReleaseEvent = self._release
        return h

    def _footer(self):
        r = QtWidgets.QHBoxLayout()
        r.setContentsMargins(9, 8, 9, 9)
        r.setSpacing(6)
        self.apply_b = None
        for text, name, what in (("OK", "FwOk", "ok"), ("Cancel", "FwButton", "cancel")):
            b = QtWidgets.QPushButton(text)
            b.setObjectName(name)
            if what == "ok":
                self.ok = b
            else:
                self.cancel_b = b
            b.clicked.connect(lambda _=False, w=what: self.finished.emit(self.name, w))
            r.addWidget(b)
        r.addStretch(1)
        if self.with_apply if self.with_apply is not None else self.name not in ("Scale / Transparency", "Text", "Polygon", "Sketch Fillet", "Sketch Chamfer", "Rectangular Pattern", "Circular Pattern", "Along Path"):
            b = QtWidgets.QPushButton("Apply +")
            b.setObjectName("FwButton")
            b.clicked.connect(lambda: self.finished.emit(self.name, "apply"))
            r.addWidget(b)
            self.apply_b = b
        return r

    def _row(self, f):
        if f.kind == "check":
            w = QtWidgets.QCheckBox(f.label)
            w.setObjectName("FwCheck")
            w.setChecked(bool(f.default))
            self.values[f.key] = f.default
            w.toggled.connect(lambda on, k=f.key: self._set(k, on))
            self.boxes[f.key] = w
            return w
        if f.kind == "heading":  # a group's title over its fields (Gear 1, Gear 2)
            w = QtWidgets.QLabel(f.label)
            w.setObjectName("FwHeading")
            return w
        row = QtWidgets.QWidget()
        r = QtWidgets.QHBoxLayout(row)
        r.setContentsMargins(0, 0, 0, 0)
        r.setSpacing(6)
        l = QtWidgets.QLabel(f.label)
        l.setObjectName("FwLabel")
        l.setFixedSize(self.label_w, self.box_h)
        r.addWidget(l, 0, Qt.AlignTop)
        box = getattr(self, "_" + f.kind)(f)
        self.boxes.setdefault(f.key, box)
        r.addWidget(box, 1)
        return row

    def _joined(self, f, row):
        """A field in the row before it: its own short name, then the field (Work Plane's X Y Z)."""
        part = QtWidgets.QWidget()
        r = QtWidgets.QHBoxLayout(part)
        r.setContentsMargins(0, 0, 0, 0)
        r.setSpacing(4)
        if f.kind == "check":
            w = QtWidgets.QCheckBox(f.label)
            w.setObjectName("FwCheck")
            w.setChecked(bool(f.default))
            self.values[f.key] = f.default
            w.toggled.connect(lambda on, k=f.key: self._set(k, on))
        else:
            l = QtWidgets.QLabel(f.label)
            l.setObjectName("FwLabel")
            l.setFixedHeight(self.box_h)
            r.addWidget(l)
            w = getattr(self, "_" + f.kind)(f)
        self.boxes.setdefault(f.key, w)
        r.addWidget(w, 1)
        row.layout().addWidget(part, 1)
        return part

    def _room(self):
        return self.full_w - 14 - 16 - 2 - self.label_w - 6

    def _width_for(self, words):
        """The window width a combo needs to show these words whole (its padding and arrow beside them)."""
        if not words:
            return 0
        need = max(self.fm.horizontalAdvance(w) for w in words) + 7 + 16 + 6
        return need + 14 + 16 + 2 + self.label_w + 6

    def _widen(self, words):
        """Wider (never narrower) when these combo words would be cut: the cards and the scrolling area follow."""
        w = self._width_for(words)
        if not self.grow or w <= self.full_w:
            return
        self.full_w = w
        for card, _keys in self.cards:
            card.setFixedWidth(self.full_w - 14)
        self._fit_area()
        self.adjustSize()

    def _text(self, f):
        b = QtWidgets.QPlainTextEdit(str(f.default))
        b.setObjectName("FwText")
        b.setFixedHeight(self.box_h * 2 + 6)
        b.setTabChangesFocus(True)
        self.values[f.key] = f.default
        b.textChanged.connect(lambda k=f.key, w=b: self._set(k, w.toPlainText()))
        b.selectAll()
        self.first_field = b
        return b

    def _pick(self, f):
        b = QtWidgets.QPushButton(f.hint or PICK_WORDS.get(f.key, "Click a face…"))
        b.setObjectName("FwPick")
        b.setFixedHeight(self.box_h)
        b.setProperty("active", False)
        b.clicked.connect(lambda _=False, k=f.key: self.activate(k))
        self.picks[f.key] = b
        self.values[f.key] = ""
        return b

    def _list(self, f):
        """A pick whose picks show as rows under it: ✕ takes one away, ▲▼ move it (ordered: Loft's sections), Clear
        takes them all (unordered: Fillet's edges). The host edits the picks (listEdited) and calls set_list."""
        box = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        head = QtWidgets.QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(4)
        head.addWidget(self._pick(f), 1)
        if not f.ordered:
            b = QtWidgets.QPushButton("Clear")
            b.setObjectName("FwButton")
            b.setFixedSize(self.fm.horizontalAdvance("Clear") + 12, self.box_h)
            b.setStyleSheet("padding: 0;")
            b.setFocusPolicy(Qt.NoFocus)
            b.setEnabled(False)
            b.clicked.connect(lambda _=False, k=f.key: self.listEdited.emit(k, -1, "clear"))
            head.addWidget(b)
            box.clear_button = b
        col.addLayout(head)
        rows = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(rows)
        lay.setContentsMargins(4, 0, 0, 0)
        lay.setSpacing(0)
        if f.ordered:
            col.addWidget(rows)
            box.area = None
        else:  # past LIST_ROWS rows it scrolls: the window keeps its size
            area = QtWidgets.QScrollArea()
            area.setFrameShape(QtWidgets.QFrame.NoFrame)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            area.setWidgetResizable(True)
            area.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
            area.setWidget(rows)
            col.addWidget(area)
            box.area = area
        rows.hide() if box.area is None else box.area.hide()
        self.lists[f.key] = (rows, [], box)
        self.values[f.key] = ""
        return box

    LIST_ROWS = 6

    def set_list(self, key, texts):
        """The rows of a list field: one per pick, in its order."""
        rows, old, box = self.lists[key]
        f = self.rows[key][0]
        for w in old:
            w.setParent(None)
            w.deleteLater()
        made = []
        for i, text in enumerate(texts):
            r = QtWidgets.QWidget(rows)
            h = QtWidgets.QHBoxLayout(r)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(2)
            l = QtWidgets.QLabel(("%d.  %s" % (i + 1, text)) if f.ordered else text)
            l.setObjectName("FwRow")
            l.setFixedHeight(self.box_h - 4)
            h.addWidget(l, 1)
            r.buttons = {}
            for what, word in ((("up", "▲"), ("down", "▼"), ("remove", "✕")) if f.ordered else (("remove", "✕"),)):
                b = QtWidgets.QPushButton(word)
                b.setObjectName("FwRowButton")
                b.setFixedSize(self.box_h - 4, self.box_h - 4)
                b.setFocusPolicy(Qt.NoFocus)
                b.clicked.connect(lambda _=False, k=key, n=i, a=what: self.listEdited.emit(k, n, a))
                h.addWidget(b)
                r.buttons[what] = b
            if f.ordered:
                r.buttons["up"].setEnabled(i > 0)
                r.buttons["down"].setEnabled(i < len(texts) - 1)
            r.label = l
            rows.layout().addWidget(r)
            made.append(r)
        self.lists[key] = (rows, made, box)
        if box.area is not None:
            box.area.setFixedHeight(min(len(made), self.LIST_ROWS) * (self.box_h - 4))
            box.area.setVisible(bool(made))
            box.clear_button.setEnabled(bool(made))
        else:
            rows.setVisible(bool(made))
        self.adjustSize()

    def list_rows(self, key):
        return [r.label.text() for r in self.lists[key][1]]

    def _toggles(self, f):
        """Words each on or off by itself (Rib's Leave out 1 … 6): the value is the "on" ones joined by commas."""
        t = QtWidgets.QFrame()
        t.setObjectName("FwTrack")
        t.setFixedHeight(self.box_h)
        r = QtWidgets.QHBoxLayout(t)
        r.setContentsMargins(2, 2, 2, 2)
        r.setSpacing(2)
        on = set(str(f.default or "").split(","))
        self.tiles[f.key] = {}
        for value, words in f.choices:
            b = QtWidgets.QPushButton(words)
            b.setObjectName("FwTile")
            b.setCheckable(True)
            b.setChecked(value in on)
            b.setFocusPolicy(Qt.NoFocus)
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            b.clicked.connect(lambda _=False, k=f.key: self._set(k, self._toggled(k)))
            self.tiles[f.key][value] = b
            r.addWidget(b, 1)
        self.values[f.key] = str(f.default or "")
        return t

    def _toggled(self, key):
        return ",".join(v for v, b in self.tiles[key].items() if b.isChecked())

    def _points(self, f):
        """Points clicked in the view: how many, Undo the last, Clear."""
        box = QtWidgets.QWidget()
        r = QtWidgets.QHBoxLayout(box)
        r.setContentsMargins(0, 0, 0, 0)
        r.setSpacing(4)
        self.point_count = QtWidgets.QLabel("No points yet")
        self.point_count.setObjectName("FwLabel")
        self.point_count.setFixedHeight(self.box_h)
        r.addWidget(self.point_count, 1)
        self.point_buttons = []
        for word, last in (("Undo", True), ("Clear", False)):
            b = QtWidgets.QPushButton(word)
            b.setObjectName("FwButton")
            b.setFixedSize(self.fm.horizontalAdvance(word) + 12, self.box_h)
            b.setStyleSheet("padding: 0;")  # the count beside them keeps its words at every text size
            b.setFocusPolicy(Qt.NoFocus)
            b.setEnabled(False)
            b.clicked.connect(lambda _=False, x=last: self.on_points and self.on_points(x))
            self.point_buttons.append(b)
            r.addWidget(b)
        self.values[f.key] = ""
        return box

    def show_points(self, n):
        self.point_count.setText("No points yet" if not n else "1 point" if n == 1 else "%d points" % n)
        for b in self.point_buttons:
            b.setEnabled(n > 0)

    def _slider(self, f):
        """A whole number dragged between choices[0] and choices[1], with its value in % beside it; changes as it moves."""
        box = QtWidgets.QWidget()
        r = QtWidgets.QHBoxLayout(box)
        r.setContentsMargins(0, 0, 0, 0)
        r.setSpacing(6)
        b = QtWidgets.QSlider(Qt.Horizontal)
        b.setObjectName("FwSlider")
        b.setRange(int(f.choices[0]), int(f.choices[1]))
        b.setValue(int(f.default))
        b.setFixedHeight(self.box_h)
        word = QtWidgets.QLabel("%d %%" % int(f.default))
        word.setObjectName("FwLabel")
        word.setFixedWidth(self.fm.horizontalAdvance("90 %") + 6)
        word.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        r.addWidget(b, 1)
        r.addWidget(word)
        self.values[f.key] = int(f.default)
        b.valueChanged.connect(lambda v, k=f.key, l=word: (l.setText("%d %%" % v), self._set(k, v)))
        return box

    def _combo(self, f):
        b = Combo()
        b.setObjectName("FwCombo")
        b.setFixedHeight(self.box_h)
        for value, words in f.choices:
            b.addItem(words, value)
        b.setCurrentIndex(max(0, b.findData(f.default)))
        if f.key == "font":  # long font names shortened in the middle, whole on hover (the user)
            b.elide = True
            b.view().setTextElideMode(Qt.ElideMiddle)
            for i in range(b.count()):
                b.setItemData(i, b.itemText(i), Qt.ToolTipRole)
            b.setToolTip(b.currentText())
            b.currentTextChanged.connect(b.setToolTip)
        self.values[f.key] = f.default
        b.currentIndexChanged.connect(lambda _i, k=f.key, w=b: self._set(k, w.currentData()))
        return b

    def _value(self, f):
        b = Value(f)
        b.setFixedHeight(self.box_h)
        self.values[f.key] = f.default
        b.valueChanged.connect(lambda v, k=f.key, w=b: self._set_value(k, v, w))
        b.set_expr(self.given.get(f.key + "_expr"))
        return b

    def _choice(self, f):
        """The words in one bar; what does not fit goes to a second bar, the words are never cut."""
        room = self._room() - 4
        lines, line, used = [], [], 0
        for value, words in f.choices:
            w = self.fm.horizontalAdvance(words) + 12
            if line and used + 2 + w > room:
                lines.append(line)
                line, used = [], 0
            used += w + (2 if line else 0)
            line.append((value, words, w))
        lines.append(line)
        box = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        group = QtWidgets.QButtonGroup(box)
        self.tiles[f.key] = {}
        for ln in lines:
            t = QtWidgets.QFrame()
            t.setObjectName("FwTrack")
            t.setFixedHeight(self.box_h)
            r = QtWidgets.QHBoxLayout(t)
            r.setContentsMargins(2, 2, 2, 2)
            r.setSpacing(2)
            for value, words, w in ln:
                b = QtWidgets.QPushButton(words)
                b.setObjectName("FwTile")
                b.setCheckable(True)
                b.setChecked(value == f.default)
                b.setFocusPolicy(Qt.NoFocus)
                b.setMinimumWidth(w)
                b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
                group.addButton(b)
                self.tiles[f.key][value] = b
                b.clicked.connect(lambda _=False, k=f.key, v=value: self._set(k, v))
                r.addWidget(b, w)
            col.addWidget(t)
        self.values[f.key] = f.default
        return box

    # ----- behaviour -----
    def _set(self, key, value):
        self.values[key] = value
        self._update_rows()
        self.edited.emit()

    def show_choice(self, key, value):
        """A choice changed from outside (Space): the window says so."""
        self.tiles[key][value].setChecked(True)
        self.values[key] = value

    def field(self, key):
        return self.rows[key][1].findChild(Value)

    def _set_value(self, key, v, box):
        self.values[key + "_expr"] = box.expr
        self._set(key, v)

    def _shown(self, f):
        if callable(f.show_if):  # a host's own test of the values (the old features' show_if)
            return bool(f.show_if(self.values))
        return all(self.values.get(k) in vals for k, vals in f.show_if)

    def _update_rows(self):
        for f, w in self.rows.values():
            w.setVisible(self._shown(f))
        for card, keys in self.cards:
            card.setVisible(any(self._shown(self.rows[k][0]) for k in keys))
        active = [k for k, b in self.picks.items() if b.property("active")]
        if active and not self._shown(self.rows[active[0]][0]):
            self._first_pick()
        self._fit_area()
        self.adjustSize()

    ROOM = 170  # px the title, the lines under the cards, the buttons and the margins take beside the cards

    def _fit_area(self):
        """The scrolling cards: as tall as they are, never past the view (then they scroll, OK stays in sight)."""
        if self.area is None:
            return
        inner = self.area.widget()
        inner.layout().activate()
        need = inner.layout().sizeHint().height()
        view = self.parentWidget()
        most = max(160, view.height() - 32 - self.ROOM) if view is not None else need
        bar = 0 if need <= most else 9
        self.area.setFixedSize(self.full_w + bar, min(need, most))

    def showEvent(self, e):
        super().showEvent(e)
        if self.area is not None:
            self._fit_area()
            self.adjustSize()

    # ----- set from outside, quietly (no edited signal): a host's own values, choices and lines -----
    def put(self, values):
        """Values set by the host: each field shows its value, nothing is told back."""
        for key, value in values.items():
            if key.endswith("_expr") and key[:-5] in self.rows:
                continue
            row = self.rows.get(key)
            if row is None:
                continue
            f = row[0]
            box = self.boxes.get(key)
            self.values[key] = value
            if f.kind == "value":
                box.number = float(value or 0.0)
                box.held = False
                box.set_expr(values.get(key + "_expr"))
                self.values[key + "_expr"] = values.get(key + "_expr")
            elif f.kind == "check":
                box.blockSignals(True)
                box.setChecked(bool(value))
                box.blockSignals(False)
            elif f.kind == "combo":
                box.blockSignals(True)
                i = box.findData(value)
                if i >= 0:
                    box.setCurrentIndex(i)
                box.blockSignals(False)
            elif f.kind == "choice":
                if value in self.tiles.get(key, {}):
                    self.tiles[key][value].setChecked(True)
            elif f.kind == "toggles":
                on = set(str(value or "").split(","))
                for v, b in self.tiles[key].items():
                    b.setChecked(v in on)
            elif f.kind == "text":
                if box.toPlainText() != str(value or ""):
                    box.blockSignals(True)
                    box.setPlainText(str(value or ""))
                    box.blockSignals(False)
            elif f.kind == "slider":
                s = box.findChild(QtWidgets.QSlider)
                s.blockSignals(True)
                s.setValue(int(value or 0))
                s.blockSignals(False)
                box.findChild(QtWidgets.QLabel).setText("%d %%" % int(value or 0))
        self._update_rows()

    def set_options(self, key, options):
        """A combo's choices replaced (Hole's sizes for its standard): the value kept while it is offered, else the
        first."""
        box = self.boxes[key]
        self._widen([w for _v, w in options])
        now = self.values.get(key, box.currentData())  # a value put in before its choices came (Hole's size)
        box.blockSignals(True)
        box.clear()
        for value, words in options:
            box.addItem(words, value)
        i = box.findData(now)
        box.setCurrentIndex(i if i >= 0 else (0 if box.count() else -1))
        box.blockSignals(False)
        self.values[key] = box.currentData() if box.count() else ""

    def set_choice_enabled(self, key, value, on):
        """A word of a choice greyed out (Extrude's Join with nothing to join)."""
        b = self.tiles.get(key, {}).get(value)
        if b is not None:
            b.setEnabled(on)

    def enable(self, key, on):
        self.boxes[key].setEnabled(on)

    def set_pick_text(self, key, text):
        """A pick's words; longer than its field: cut with … and whole in its tip (never cut mid-word unseen)."""
        b = self.picks[key]
        text = text or self.rows[key][0].hint or PICK_WORDS.get(key, "Click a face…")
        room = self._room() - 16 - (self.fm.horizontalAdvance("Clear") + 16 if self.rows[key][0].kind == "list"
                                    and not self.rows[key][0].ordered else 0)
        short = self.fm.elidedText(text, Qt.ElideRight, room)
        b.setText(short)
        b.setToolTip(text if short != text else "")

    def set_label(self, key, text):
        """A field's name (with its parameter: Distance  (d3))."""
        f, row = self.rows[key]
        l = row if isinstance(row, QtWidgets.QCheckBox) else row.findChild(QtWidgets.QLabel, "FwLabel")
        if l is not None:
            l.setText(text)
            if not isinstance(row, QtWidgets.QCheckBox) and not f.same_row \
                    and self.fm.horizontalAdvance(text) + 4 > self.label_w:  # every row's fields start at one line
                self.label_w = self.fm.horizontalAdvance(text) + 4
                for g, r in self.rows.values():
                    n = r.findChild(QtWidgets.QLabel, "FwLabel") if not isinstance(r, QtWidgets.QLabel) else None
                    if n is not None and not g.same_row and g.kind not in ("check", "heading"):
                        n.setFixedWidth(self.label_w)
                self._widen([b.itemText(i) for b in self.boxes.values() if isinstance(b, QtWidgets.QComboBox)
                             for i in range(b.count())])  # a longer name: the combos keep their words whole
                self.adjustSize()

    def lock(self, wrong, rescue=None):
        """OK and Apply + off while something is wrong; rescue = (words, what it does) offers a way out."""
        self.ok.setEnabled(not wrong)
        if self.apply_b is not None:
            self.apply_b.setEnabled(not wrong)
        self._rescue = rescue[1] if rescue else None
        self.rescue_button.setText(rescue[0] if rescue else "")
        self.rescue_button.setVisible(bool(wrong and rescue))
        self.adjustSize()

    LONG_INFO = 110  # characters: a longer info line shows its first sentence and "more"

    def info(self, text):
        """A grey line on what the values make (a gear's diameters); a long one folds to its first sentence."""
        if text != self._info_full:
            self._info_full = text or ""
            self._show_info()

    def _show_info(self):
        import html
        text = self._info_full
        first = text.split(". ")[0]
        if len(text) <= self.LONG_INFO or len(first) >= len(text) - 2:
            shown = html.escape(text)
        elif self._info_open:
            shown = '%s <a href="less">less</a>' % html.escape(text)
        else:
            shown = '%s… <a href="more">more</a>' % html.escape(first.rstrip(".:"))
        self.info_line.setText(shown)
        self.info_line.setVisible(bool(text))
        self.adjustSize()

    def _fold_info(self, _link=""):
        self._info_open = not self._info_open
        self._show_info()

    def _dim_env(self):
        """The names a formula may use: the host's, else those of the sketch the window is in."""
        if self.names is not None:
            return self.names()
        w = self.parentWidget()
        while w is not None and not hasattr(w, "_dim_env"):
            w = w.parentWidget()
        return w._dim_env() if w is not None else {}

    def say(self, text):
        """A line above OK: why nothing shows now; None takes it away."""
        self.warn.setStyleSheet("color: #ff6b5e; padding: 6px 10px 0 10px;")
        self.warn.setText(text or "")
        self.warn.setVisible(bool(text))

    def note(self, text):
        """A plain line above OK: what to do next, not what is wrong."""
        self.warn.setStyleSheet("color: %s; padding: 6px 10px 0 10px;" % theme.cur()["text_dim"])
        self.warn.setText(text or "")
        self.warn.setVisible(bool(text))

    def _first_pick(self):
        for k in self.picks:
            if self._shown(self.rows[k][0]):
                self.activate(k)
                return

    def activate(self, key):
        """The field the next click in the view goes to: one at a time, its edge in the accent colour."""
        self.active = key
        if self.on_active is not None:
            self.on_active(key)
        for k, b in self.picks.items():
            b.setProperty("active", k == key)
            b.style().unpolish(b)
            b.style().polish(b)

    # ----- moved by its head -----
    def _press(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.pos()

    def place(self, pos):
        """Where it may stand: right of the tree and below the ribbon's reach, whole in the view as far as it fits, its
        title always there to take hold of."""
        view = self.parentWidget()
        low = getattr(view, "window_top", 8)
        left = getattr(view, "left_inset", 0) + 6
        x = min(max(pos.x(), left), max(left, view.width() - self.width() - 6))
        y = max(low, min(pos.y(), view.height() - self.height() - 6))
        self.move(x, y)

    def _move(self, e):
        if self._drag is not None:
            at = e.globalPosition().toPoint() - self._drag
            box = self.parentWidget().rect()
            top = getattr(self.parentWidget(), "window_top", 0)  # its title never goes under the ribbon
            self.move(max(0, min(at.x(), box.width() - self.width())), max(top, min(at.y(), box.height() - 40)))

    def _release(self, _e):
        self._drag = None

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.finished.emit(self.name, "cancel")
        else:
            super().keyPressEvent(e)
