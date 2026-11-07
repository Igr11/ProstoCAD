"""Light/dark palettes, the InCad Qt stylesheet, icons and the 3D-view background colors."""
import os

from incad.core import prefs

PALETTES = {
    "light": dict(window="#ececec", panel="#f7f7f7", panel_alt="#f3f3f3", border="#c8c8c8", text="#1d1d1f",
                  text_dim="#888888", accent="#0a64d6", accent_soft="#dbe9fb", selection_text="#ffffff",
                  error="#b00020", rolled_back="#999999", eop="#b00020", marker_end="#e67e22",
                  view_top="#cfd8e3", view_bottom="#f4f6f9", cube="#e3e7ec", context="#8c939c"),
    "dark": dict(window="#1e1e1e", panel="#323234", panel_alt="#262628", border="#111111", text="#e6e6e6",
                 text_dim="#888888", accent="#5aa2ff", accent_soft="#1f3b5e", selection_text="#ffffff",
                 error="#ff6b7f", rolled_back="#777777", eop="#ff6b7f", marker_end="#f39c4a",
                 view_top="#2b3440", view_bottom="#14181e", cube="#3d4450", context="#a3aab3"),
}
ICON_PX = {"compact": 20, "medium": 24, "large": 32}
ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources", "icons")

STYLE = """
QWidget#InCadTitle {{ background: {window}; color: {text}; border-bottom: 1px solid {border}; }}
QLabel#InCadDocName {{ color: {text}; font-weight: 600; }}
QLabel#InCadVersion {{ color: {text_dim}; font-size: 11px; padding: 0 6px; }}
QWidget#InCadRibbon {{ background: {panel}; color: {text}; border-bottom: 1px solid {border}; }}
QTabBar#InCadRibbonTabs {{ background: {window}; }}
QTabBar#InCadRibbonTabs::tab {{ background: {window}; color: {text}; padding: 5px 14px; border: none; }}
QTabBar#InCadRibbonTabs::tab:selected {{ background: {panel}; color: {accent}; font-weight: 600; }}
QFrame#InCadRibbonGroup {{ border: none; border-right: 1px solid {border}; }}
QLabel#InCadRibbonGroupLabel {{ color: {text_dim}; font-size: 10px; }}
QToolButton {{ color: {text}; border: none; border-radius: 6px; padding: 3px; background: transparent; }}
QToolButton:hover {{ background: {accent_soft}; }}
QToolButton:checked {{ background: {accent_soft}; }}
QWidget#InCadBrowser {{ background: {panel_alt}; color: {text}; }}
QTreeWidget#InCadTree, QTableWidget#InCadParams {{ background: {panel_alt}; color: {text}; border: none; }}
QTreeWidget#InCadTree::item:selected {{ background: {accent}; color: {selection_text}; }}
QLabel#InCadPanelHeader {{ color: {text_dim}; font-weight: 600; font-size: 11px; padding: 6px 8px; }}
QHeaderView::section {{ background: {panel}; color: {text_dim}; border: none; padding: 2px 6px; }}
QWidget#InCadStatus {{ background: {window}; color: {text_dim}; border-top: 1px solid {border}; }}
QWidget#InCadStatus QLabel {{ color: {text_dim}; padding: 0 8px; }}
QDialog#InCadNewDialog {{ background: {panel}; color: {text}; }}
QDialog#InCadNewDialog QLabel, QDialog#InCadNewDialog QCheckBox {{ color: {text}; }}
QPushButton#InCadTile {{ background: {panel_alt}; color: {text}; border: 1px solid {border}; border-radius: 9px; padding: 12px; }}
QPushButton#InCadTile:checked {{ border: 2px solid {accent}; }}
QPushButton#InCadTile:disabled {{ color: {text_dim}; }}
"""

VIEW_GROUP = "User parameter:BaseApp/Preferences/View"
VIEW_KEYS = {"Simple": "Bool", "Gradient": "Bool", "RadialGradient": "Bool", "UseBackgroundColorMid": "Bool",
             "BackgroundColor2": "Unsigned", "BackgroundColor3": "Unsigned",
             "AxisXColor": "Unsigned", "AxisYColor": "Unsigned", "AxisZColor": "Unsigned"}
# muted axis colours (FreeCAD's pure red/green/blue were too loud); still red/green/blue for X/Y/Z
AXIS_COLORS = {"light": {"AxisXColor": "#b86a6a", "AxisYColor": "#6a9a70", "AxisZColor": "#6a82b8"},
               "dark": {"AxisXColor": "#a86565", "AxisYColor": "#679470", "AxisZColor": "#6a80b0"}}


def resolve(pref, system_dark):
    if pref in ("light", "dark"):
        return pref
    return "dark" if system_dark else "light"


def system_is_dark():
    from incad.qt import QtCore, QtWidgets
    app = QtWidgets.QApplication.instance()
    if app is None:
        return False
    return app.styleHints().colorScheme() == QtCore.Qt.ColorScheme.Dark


def current():
    return resolve(prefs.theme(), system_is_dark())


def toggled(theme):
    return "light" if theme == "dark" else "dark"


def icon_path(name, theme):
    return os.path.join(ICON_DIR, theme, name + ".svg")


def icon(name, theme):
    from incad.qt import QtGui
    return QtGui.QIcon(icon_path(name, theme))


def stylesheet(theme):
    return STYLE.format(**PALETTES[theme])


def _rgba(hex_color):
    return (int(hex_color.lstrip("#"), 16) << 8) | 0xFF


def view_params(theme):
    """FreeCAD 'View' parameters that give the 3D view InCad's gradient background."""
    p = PALETTES[theme]
    return {"Simple": ("Bool", False), "Gradient": ("Bool", True), "RadialGradient": ("Bool", False),
            "UseBackgroundColorMid": ("Bool", False),
            "BackgroundColor2": ("Unsigned", _rgba(p["view_top"])),
            "BackgroundColor3": ("Unsigned", _rgba(p["view_bottom"])),
            **{k: ("Unsigned", _rgba(v)) for k, v in AXIS_COLORS[theme].items()}}


NAVICUBE_GROUP = "User parameter:BaseApp/Preferences/NaviCube"
NAVICUBE_KEYS = {"BaseColor": "Unsigned"}


def navicube_params(theme):
    """FreeCAD NaviCube (ViewCube) settings for InCad's look."""
    return {"BaseColor": ("Unsigned", _rgba(PALETTES[theme]["cube"]))}


SKETCH_ROOT = "User parameter:BaseApp/Preferences/Mod/Sketcher"
SKETCH_TOOLS = "User parameter:BaseApp/Preferences/Mod/Sketcher/Tools"
SKETCH_GENERAL = "User parameter:BaseApp/Preferences/Mod/Sketcher/General"
SKETCH_VIEW = "User parameter:BaseApp/Preferences/Mod/Sketcher/View"
# real lines 3 px; construction lines (and projected ones switched to construction, an ellipse's axes) 2 px with
# long gaps (the user: thinner than the real lines yet well visible — 1 px was a hairline on a Retina screen;
# FreeCAD: all 2 px, construction 6 on / 2 off) — a Coin 16-bit pattern, 8 px on, 8 px off
DASHED_LINES = {"EdgeWidth": ("Int", 3), "ExternalDefiningWidth": ("Int", 3),
                "ConstructionWidth": ("Int", 2), "ExternalWidth": ("Int", 2), "InternalWidth": ("Int", 2),
                "ConstructionPattern": ("Int", 0xFF00), "ExternalPattern": ("Int", 0xFF00),
                "InternalPattern": ("Int", 0xFF00)}
# FreeCAD's B-spline overlays (the green curvature comb, the degree, knots, weights): Inventor shows none — an offset
# ellipse (a B-spline) was buried under its comb. The control polygon stays as the user has it
BSPLINE_INFO = ("BSplineCombVisible", "BSplineDegreeVisible", "BSplineKnotMultiplicityVisible",
                "BSplinePoleWeightVisible")
DISPLAY_FORMAT = {"value": (False, "%V"), "name": (True, "%N"), "expression": (True, "%N = %V")}
SKETCH_COLORS = {
    # calm constraint colours (FreeCAD's red/orange was too loud): blue-grey symbols and dimensions; construction
    # geometry orange (the user: told apart from the real lines at a glance) — a projected line switched to
    # construction too (ExternalColor; a normal projected one keeps FreeCAD's ExternalDefiningColor) — the line
    # being drawn blue; a sketch shown outside its edit blue too, 3 px (the user: seen on a plane and on the part)
    "light": dict(EditedEdgeColor="#1d1d1f", SketchEdgeColor="#1565d8", SketchVertexColor="#1d1d1f",
                  FullyConstrainedColor="#1a8a2e", ConstructionColor="#e67e22", ConstrainedDimColor="#35557f",
                  FullyConstraintConstructionElementColor="#e67e22", ExternalColor="#e67e22",
                  ExprBasedConstrDimColor="#6a5a9e", NonDrivingConstrDimColor="#7d8896", CursorTextColor="#1d1d1f",
                  CreateLineColor="#0a64d6", ConstrainedIcoColor="#5a7698", DeactivatedConstrDimColor="#9aa3ad"),
    "dark": dict(EditedEdgeColor="#f2f2f2", SketchEdgeColor="#2673ff", SketchVertexColor="#f2f2f2",
                 FullyConstrainedColor="#5ad16b", ConstructionColor="#f39c4a", ConstrainedDimColor="#9db8de",
                 FullyConstraintConstructionElementColor="#f39c4a", ExternalColor="#f39c4a",
                 ExprBasedConstrDimColor="#b9a8e6", NonDrivingConstrDimColor="#8d99a8", CursorTextColor="#f2f2f2",
                 CreateLineColor="#5aa2ff", ConstrainedIcoColor="#7f9cc0", DeactivatedConstrDimColor="#6b7480"),
}
SHOWN_SKETCH_WIDTH = 3.0
SKETCH_KEYS = {
    SKETCH_ROOT: {"ShowDialogOnDistanceConstraint": "Bool", "ShowDimensionalName": "Bool",
                  "DimensionalStringFormat": "String", "LeaveSketchWithEscape": "Bool"},
    SKETCH_TOOLS: {"OnViewParameterVisibility": "Int", "ContinuousCreationMode": "Bool"},
    SKETCH_GENERAL: {k: "Bool" for k in BSPLINE_INFO},
    VIEW_GROUP: {k: "Unsigned" for k in SKETCH_COLORS["light"]},
    SKETCH_VIEW: {k: kind for k, (kind, _) in DASHED_LINES.items()},
}


def sketch_params(theme, display="value"):
    """Sketcher settings InCad uses: no modal dimension dialog and no on-view value field (InCad's value box is
    the one place to type), continuous tools, Esc cancels a tool but never leaves the sketch (Finish Sketch
    does), names shown per display mode, colors."""
    show, fmt = DISPLAY_FORMAT[display]
    return {
        SKETCH_ROOT: {"ShowDialogOnDistanceConstraint": ("Bool", False), "ShowDimensionalName": ("Bool", show),
                      "DimensionalStringFormat": ("String", fmt), "LeaveSketchWithEscape": ("Bool", False)},
        SKETCH_TOOLS: {"OnViewParameterVisibility": ("Int", 0), "ContinuousCreationMode": ("Bool", True)},
        SKETCH_GENERAL: {k: ("Bool", False) for k in BSPLINE_INFO},
        VIEW_GROUP: {k: ("Unsigned", _rgba(v)) for k, v in SKETCH_COLORS[theme].items()},
        SKETCH_VIEW: dict(DASHED_LINES),
    }
