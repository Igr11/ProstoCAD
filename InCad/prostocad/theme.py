"""Colours and type of ProstoCAD's clean interface. Quiet colours (the user: "the blind don't work in CAD"); the light
theme is muted, never glaring white. The browser lies on the window's own colour, a light grey line as its edge."""
import os

ICONS = os.path.join(os.path.dirname(__file__), "icons")

FONT = "Tahoma"
FONT_PX = 12
# Settings ▸ Text Size: four clearly different sizes; each group has its own (the user)
TEXT_SIZES = {"S": 11, "M": 12, "L": 14, "XL": 17}
TEXT_DEFAULTS = {"tree": "L", "ui": "M", "drawing": "M"}
TREE_PX = 14  # the browser's text: Tahoma 12 px came out smaller than the system font it had (the user)

DARK = dict(
    name="dark",
    # grey, not black (the user: a black browser is the old program): the browser lies on this window colour
    window="#3a3b3e", panel="#444548", strip="#3e3f42", border="#242527", field="#2e2f31",
    edge_line="#6b6e73",  # the browser's right edge
    hover="#505155", hover_border="#5e5f63", disabled="#c8c8cc", icon_off=1.0,
    text="#f2f2f2", text_dim="#c4c4c8", accent="#5aa2ff", accent_soft="#33557d",
    view_top="#2b3440", view_bottom="#14181e", grid="#323c49", grid_major="#3f4a59",
    axis_x="#c98e87", axis_y="#93b899", axis_z="#93a4cf",  # muted: no poisonous red / green
    geometry="#e6ebf0", state_free="#5aa2ff", state_part="#c792ea", start="#5aa2ff", snap="#e8a94c", hint_bg="#1e1e20", construction="#f39c4a",
    finish="#2ea043", finish_quiet="#6fa57b", end_of_part="#d8665f",
    cube_line=(175, 183, 193, 205), cube_fill=(30, 37, 46, 130), cube_text=(200, 206, 213, 230),
)

LIGHT = dict(
    name="light",
    window="#d6dade", panel="#e2e5e9", strip="#d9dde2", border="#aeb5bd", field="#eceef1",
    edge_line="#9aa2ac",
    hover="#cfd5dc", hover_border="#b3bbc5", disabled="#2f353c", icon_off=1.0,
    text="#14181d", text_dim="#3d4550", accent="#2f6fc0", accent_soft="#bfd2ea",
    view_top="#b9c1ca", view_bottom="#d3d8de", grid="#b4bcc6", grid_major="#a1aab5",
    axis_x="#9a564e", axis_y="#4c7a54", axis_z="#4c6296",
    geometry="#1f2832", state_free="#1f6fd0", state_part="#8a3fc7", start="#2f6fc0", snap="#a8620e", hint_bg="#eef0f2", construction="#c86a12",
    finish="#2b8a3e", finish_quiet="#4f8a5c", end_of_part="#b4413a",
    cube_line=(40, 47, 56, 240), cube_fill=(240, 242, 245, 175), cube_text=(25, 30, 37, 245),
)

def _look(base, name, panel, view_top, view_bottom, **more):
    """A look the user can pick in Settings: the window, the ribbon and the view's gradient (the browser lies on the
    view) in one tone family; anything else as the base theme has it."""
    p = dict(base, panel=panel, window=panel, view_top=view_top, view_bottom=view_bottom, look=name, **more)
    c = QColorless(panel)
    if "strip" not in more:  # the name strip under the ribbon groups: a shade off the panel
        p["strip"] = c.darker(0.06 if base["name"] == "light" else -0.06)
    return p


class QColorless:
    """A colour by hex, made a little darker or lighter without Qt (the theme loads before the app)."""

    def __init__(self, hex_colour):
        self.rgb = [int(hex_colour.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]

    def darker(self, amount):
        return "#" + "".join("%02x" % max(0, min(255, round(v * (1 - amount)) if amount > 0 else round(v + (255 - v) * -amount)))
                             for v in self.rgb)


# Settings ▸ Light Theme: from the lightest to the darkest, wider steps (the user: too alike); ours — the old
# "Light 5" — kept as it was and still the default, wherever it falls in the row
# as distinct as the dark ones (the user: the light ones looked alike): paper white, ours, warm sand, cool blue,
# neutral grey with a strong gradient, slate
LIGHTS = [_look(LIGHT, "Light %d" % (k + 1), *c) for k, c in enumerate([
    ("#f8f8f6", "#f2f2ee", "#ffffff"),   # paper: almost white, a warm touch, hardly a gradient
    ("#eef0f2", "#cfd5dc", "#e6e9ed"),   # ours, as it was
    ("#e8e2d8", "#d2c7b6", "#efe9e0"),   # sand: warm beige
    ("#dbe4ee", "#a9bdd4", "#e2eaf3"),   # cool: clear blue-grey, a stronger gradient
    ("#cfd1d4", "#969ca4", "#d8dadd"),   # neutral grey, strong gradient
    ("#b9c0c9", "#76838f", "#c3cad3")])]  # slate: the darkest light one
LIGHT_DEFAULT = 1  # ours (Light 2 in this row)

# Settings ▸ Dark Theme: from the lightest to the darkest; ours stays the default wherever it falls
DARKS = [_look(DARK, "Steel", "#4a4f57", "#5c6672", "#2f353d", grid="#56606c", grid_major="#66717e"),
         dict(DARK, look="Default"),
         _look(DARK, "Warm", "#332d29", "#2d2825", "#161311", grid="#352f2b", grid_major="#433b36", accent_soft="#4a3a2c"),
         _look(DARK, "Graphite", "#2f2f30", "#262627", "#1c1c1d", grid="#303031", grid_major="#3b3b3c"),
         _look(DARK, "High Contrast", "#141414", "#0a0a0a", "#000000", text="#ffffff", text_dim="#e6e6e6",
               disabled="#e0e0e0", grid="#1f1f1f", grid_major="#2e2e2e", geometry="#ffffff", hint_bg="#000000"),
         _look(DARK, "Black & White", "#000000", "#000000", "#000000", text="#ffffff", text_dim="#d8d8d8",
               grid="#1c1c1c", grid_major="#2a2a2a", geometry="#ffffff", border="#333333", hover="#1e1e1e",
               hover_border="#3a3a3a", field="#0d0d0d", edge_line="#5a5a5a", hint_bg="#000000")]
DARK_DEFAULT = 1  # ours

_current = [DARK]


def cur():
    return _current[0]


def use(palette):
    _current[0] = palette


def icon_path(name, theme=None):
    return os.path.join(ICONS, theme or cur()["name"], name + ".svg")


def icon(name):
    """Our icon, crisp: drawn at full strength; switched off it keeps its colours, only a little dimmer (Qt's own
    grey-out washed the icons out; on the light theme nothing could be seen — the user: much more contrast)."""
    from PySide6 import QtCore, QtGui
    src = QtGui.QIcon(icon_path(name))
    out = QtGui.QIcon()
    for px in (16, 24, 32, 48, 64):
        pm = src.pixmap(QtCore.QSize(px, px))
        out.addPixmap(pm, QtGui.QIcon.Normal)
        dim = QtGui.QPixmap(pm.size())
        dim.setDevicePixelRatio(pm.devicePixelRatio())
        dim.fill(QtCore.Qt.transparent)
        g = QtGui.QPainter(dim)
        g.setOpacity(cur()["icon_off"])
        g.drawPixmap(0, 0, pm)
        g.end()
        out.addPixmap(dim, QtGui.QIcon.Disabled)
    return out


def qss(p, tree_px=FONT_PX, ui_px=FONT_PX, drawing_px=FONT_PX):
    return f"""
* {{ font-family: "{FONT}"; font-size: {ui_px}px; color: {p['text']}; }}
QMainWindow, QWidget#Root {{ background: {p['window']}; }}
QWidget#Quick {{ background: {p['window']}; border-bottom: 1px solid {p['border']}; }}
QTabBar#DocTabs {{ background: transparent; border: none; }}
QTabBar#DocTabs::tab {{ background: transparent; color: {p['text_dim']}; padding: 3px 8px 3px 12px; border: none; max-width: 200px; }}
QTabBar#DocTabs::tab:selected {{ background: {p['panel']}; color: {p['text']}; font-weight: bold; border-bottom: 2px solid {p['accent']}; }}
QTabBar#DocTabs::tab:hover {{ color: {p['text']}; }}
QTabBar#RibbonTabs {{ background: {p['window']}; }}
QTabBar#RibbonTabs::tab {{ background: transparent; color: {p['text_dim']}; padding: 3px 12px 4px 12px;
    border: 1px solid transparent; border-bottom: none; margin-right: 1px; }}
QTabBar#RibbonTabs::tab:selected {{ background: {p['panel']}; color: {p['text']}; border-color: {p['border']};
    border-top: 2px solid {p['finish']}; }}
QStackedWidget#RibbonStack {{ background: {p['panel']}; }}
QWidget#RibbonPage {{ background: {p['panel']}; border-top: 1px solid {p['border']}; border-bottom: 1px solid {p['border']}; }}
QFrame#Panel {{ border-right: 1px solid {p['border']}; }}
QLabel#PanelName {{ color: {p['text_dim']}; font-family: "{FONT}"; font-size: {ui_px - 1}px; background: {p['strip']}; padding: 1px 0 2px 0; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 2px; padding: 1px 2px; }}
QToolButton:hover:enabled {{ background: {p['hover']}; border-color: {p['hover_border']}; }}
QToolButton:checked {{ background: {p['accent_soft']}; border-color: {p['accent']}; }}
QToolButton:disabled {{ color: {p['disabled']}; }}
QWidget#Browser {{ background: transparent; }}
QFrame#Edge {{ background: {p['edge_line']}; }}
QToolButton#FinishSketch {{ color: {p['text_dim']}; font-family: "{FONT}"; font-size: {drawing_px - 1}px; background: transparent; border: 1px solid transparent; }}
QToolButton#FinishSketch:hover {{ color: {p['text']}; background: {p['hover']}; border-color: {p['hover_border']}; }}
QFrame#NavStrip {{ background: {p['panel']}; border: 1px solid {p['edge_line']}; border-radius: 8px; }}
QToolButton#NavButton {{ background: transparent; border: none; border-radius: 5px; padding: 3px; }}
QToolButton#NavButton:hover {{ background: {p['accent_soft']}; }}
QWidget#TopSlot {{ background: {p['window']}; border-bottom: 1px solid {p['edge_line']}; }}
QLabel#ToolStrip {{ background: transparent; border-bottom: 1px solid {p['edge_line']}; padding: 8px 10px;
    font-family: "{FONT}"; font-size: {drawing_px}px; }}
QLabel#BrowserTab {{ background: transparent; padding: 4px 10px; color: {p['text']}; font-weight: bold; }}
QTreeWidget {{ background: transparent; border: none; outline: 0; font-family: "{FONT}"; font-size: {tree_px}px; }}
QTreeWidget::item {{ padding: 2px 2px; }}
QTreeWidget::item:selected {{ background: {p['accent_soft']}; color: {p['text']}; }}
QWidget#Status {{ background: {p['window']}; border-top: 1px solid {p['border']}; }}
QLabel#Prompt {{ color: {p['text']}; font-family: "{FONT}"; font-size: {drawing_px}px; }}
QLabel#StatusDim {{ color: {p['text_dim']}; font-family: "{FONT}"; font-size: {drawing_px}px; }}
QWidget#ChoiceRow QLabel {{ color: {p['text']}; }}
QWidget#ChoiceRow QPushButton {{ background: {p['field']}; border: 1px solid {p['border']}; padding: 2px 8px;
    min-width: 22px; }}
QWidget#ChoiceRow QPushButton:checked {{ background: {p['accent_soft']}; border-color: {p['accent']}; }}
QLineEdit#ValueBox {{ background: {p['field']}; border: 1px solid {p['border']}; padding: 1px 4px;
    font-family: "{FONT}"; font-size: {drawing_px}px;
    selection-background-color: {p['accent'] if p['name'] == 'light' else p['accent_soft']}; selection-color: #ffffff; }}
QLabel#Toast {{ background: {p['panel']}; color: {p['finish']}; border: 3px solid {p['finish']}; border-radius: 12px;
    padding: 20px 40px; font-family: "{FONT}"; font-size: {ui_px + 12}px; font-weight: bold; }}
QFrame#DimPanel {{ background: {p['window']}; border: 1px solid {p['accent']}; border-radius: 6px; }}
QLabel#DimName {{ color: {p['text']}; background: transparent; font-family: "{FONT}"; font-size: {drawing_px}px; }}
QLabel#DimError {{ color: {p['end_of_part']}; background: transparent; font-family: "{FONT}"; font-size: {drawing_px}px; }}
QToolButton#DimButton {{ color: {p['text']}; background: {p['field']}; border: 1px solid {p['border']}; border-radius: 4px;
    min-width: 20px; min-height: 18px; padding: 0 4px; }}
QToolButton#DimButton:hover {{ background: {p['accent']}; }}
QLineEdit#ValueBox[active="true"] {{ border-color: {p['accent']}; }}
QFrame#Variants {{ background: {p['panel']}; border: 1px solid {p['edge_line']}; }}
QToolButton#Variant {{ text-align: left; padding: 2px 8px 2px 4px; }}
QWidget#SettingsPanel {{ background: {p['panel']}; }}
QListWidget#SettingsSections {{ background: {p['field']}; border: 1px solid {p['border']}; outline: 0; padding: 3px; }}
QListWidget#SettingsSections::item {{ padding: 4px 6px; color: {p['text']}; }}
QListWidget#SettingsSections::item:selected {{ background: {p['accent_soft']}; color: {p['text']}; }}
QWidget#SettingsPanel QRadioButton::indicator, QWidget#SettingsPanel QCheckBox::indicator {{ width: 12px; height: 12px;
    background: {p['field']}; border: 1px solid {p['border']}; }}
QWidget#SettingsPanel QRadioButton::indicator {{ border-radius: 7px; }}
QWidget#SettingsPanel QCheckBox::indicator {{ border-radius: 2px; }}
QWidget#SettingsPanel QRadioButton::indicator:checked, QWidget#SettingsPanel QCheckBox::indicator:checked {{
    background: {p['accent']}; border-color: {p['accent']}; }}
QLabel#SettingsHead {{ color: {p['text']}; font-weight: bold; }}
QMenu {{ background: {p['panel']}; border: 1px solid {p['border']}; }}
QMenu::item:selected {{ background: {p['accent_soft']}; }}
QToolTip {{ background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']}; }}
"""
