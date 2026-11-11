"""⚙ Settings (0.18): the new program's Settings panel (prostocad/main.py, the settings button) in the old program's
title bar — sections on the left, one section on the right, a click applies at once and the panel stays open (the
user, 2026-11-10: the same window in both programs, each with its own values).

What the trial knows goes in: the new sketch's own settings (kept where it keeps its Polygon and Text choices,
QSettings "ProstoCAD Try New Sketch") and the old program's ⚙ menu (core/prefs, applied by the same code as the menu
did). Left out: what the trial has no function for (the new program's looks, tree and ribbon text sizes, solid
colours, hidden edges, Hide Ribbon) and the old Sketcher's own (Type Sizes While Drawing, Rectangle Diagonals, Polygon
Circle; Dimensions While Drawing stays dropped)."""
from incad import host
from incad.core import log, prefs
from incad.qt import QtCore, QtGui, QtWidgets

MARK_SIZES = ("14", "18", "22")  # prostocad/main.py
SNAP_GRID = ("Off", "Magnet", "Node")
COLOR_ROWS = (("free", "Undefined Lines"), ("part", "Partly Defined Lines"), ("full", "Fully Defined Lines"),
              ("done", "Finished Sketch"), ("snap", "Hints and Selection"), ("ref", "Reference Dimensions"),
              ("cursor", "Cursor"))
CURSOR_NAMES = {"large": "Large Cross", "small": "Small Cross", "arrow": "Arrow"}  # the new program's (d482dbb)
LABEL_OF = {"value": "Value", "name": "Name", "expression": "Both"}  # the old Dimension Display <-> Dimensions ▸ Label
DISPLAY_OF = {v: k for k, v in LABEL_OF.items()}
SECTIONS = ("Theme", "Text Size", "Dimensions", "Sketch Colors", "Sketch", "View", "Other")


def store():
    """The new sketch's settings (its Polygon and Text choices and Right-Click Menu too). INNCAD_SKETCH_SETTINGS: an
    .ini file instead (tests: the user's own are never touched)."""
    import os
    path = os.environ.get("INNCAD_SKETCH_SETTINGS")
    if path:
        return QtCore.QSettings(path, QtCore.QSettings.IniFormat)
    return QtCore.QSettings("ProstoCAD", "ProstoCAD Try New Sketch")


def _theme_name():
    from incad.ui import theme as themes
    return "light" if themes.current() == "light" else "dark"


def _palette():
    """The new program's palette for the old program's theme now on (prostocad.theme's own stays as the sketch has it)."""
    from prostocad import theme as nt
    return nt.LIGHT if _theme_name() == "light" else nt.DARK


def _color_key(key):
    return "color_%s_%s" % (key, _theme_name())


def _cursor_shape(st):
    from prostocad.sketch import CURSOR_SHAPES
    saved = str(st.value("cursor_shape", CURSOR_SHAPES[0]))
    return saved if saved in CURSOR_SHAPES else CURSOR_SHAPES[0]


def values():
    """Every sketch setting as saved, with the new program's defaults."""
    from prostocad import dimension, theme as nt
    st = store()
    look = {}
    for key in dimension.CHOICES:
        saved = str(st.value("dim_" + key, dimension.DEFAULTS[key]))
        look[key] = saved if saved in dimension.CHOICES[key] else dimension.DEFAULTS[key]
    look["label"] = LABEL_OF.get(prefs.dimension_display(), "Value")
    mark = str(st.value("mark_px", 18))
    snap = str(st.value("snap_grid", "Off"))
    drawing = str(st.value("text_drawing", nt.TEXT_DEFAULTS["drawing"]))
    return dict(
        dim_look=look,
        inches=str(st.value("inch_look", "Decimals")),
        mark_px=mark if mark in MARK_SIZES else "18",
        colors={k: str(st.value(_color_key(k))) for k, _l in COLOR_ROWS if st.value(_color_key(k))},
        show_grid=str(st.value("show_grid", "On")) != "Off",
        snap_grid=snap if snap in SNAP_GRID else "Off",
        right_menu=str(st.value("right_menu", "Ring")),
        cursor_shape=_cursor_shape(st),
        angle_mode="180" if str(st.value("angle_mode", "360")) == "180" else "360",
        drawing=drawing if drawing in nt.TEXT_SIZES else nt.TEXT_DEFAULTS["drawing"])


def apply_to(v):
    """The settings onto a SketchView (the open sketch's layer), as the new program's main window sets its view."""
    from prostocad import theme as nt
    from prostocad import units
    s = values()
    try:
        v.dim_look.update({k: val for k, val in s["dim_look"].items()})
        units.fractions = s["inches"] == "Fractions"
        v.mark_px = int(s["mark_px"])
        v.colors = dict(s["colors"])
        v.cursor_shape = s["cursor_shape"]  # Settings ▸ Sketch Colors ▸ Cursor Shape (and ▸ Cursor, its colour)
        v.setCursor(v.cross)
        v.show_grid = s["show_grid"]
        v.snap_grid = s["snap_grid"]
        v.angle_360 = s["angle_mode"] == "360"
        px = nt.TEXT_SIZES[s["drawing"]]
        v.drawing_px = px
        for b in (getattr(v, "box_len", None), getattr(v, "box_ang", None)):
            if b is not None:
                f = QtGui.QFont(nt.FONT)
                f.setPixelSize(px)
                b.setFont(f)
                if hasattr(b, "_fit"):
                    b._fit()
        v._refresh()
        v.update()
    except Exception:
        log.logger().exception("sketch settings onto the open sketch")


def _apply_open():
    """A change seen at once in the open sketch (the new program: its view follows each click)."""
    try:
        from incad.sketch_next import session
        cur = session.current()
        if cur is not None:
            apply_to(cur.layer)
    except Exception:
        log.logger().exception("sketch settings: the open sketch")


def _choice_row(col, label, keys, current, pick, names=None, width=118):
    """prostocad/main.py _choice_row: its name and a button per choice after it, the current one pressed."""
    row = QtWidgets.QWidget()
    row.setObjectName("ChoiceRow")
    lay = QtWidgets.QHBoxLayout(row)
    lay.setContentsMargins(0, 1, 0, 1)
    lay.setSpacing(4)
    name = QtWidgets.QLabel(label)
    name.setFixedWidth(width)
    lay.addWidget(name)
    group = QtWidgets.QButtonGroup(row)
    buttons = {}
    for key in keys:
        b = QtWidgets.QPushButton((names or {}).get(key, key))
        b.setCheckable(True)
        b.setChecked(key == current)
        b.setFocusPolicy(QtCore.Qt.NoFocus)
        group.addButton(b)
        b.clicked.connect(lambda _=False, k=key: pick(k))
        lay.addWidget(b)
        buttons[key] = b
    lay.addStretch(1)
    col.addWidget(row)
    return buttons


class StayMenu(QtWidgets.QMenu):
    """prostocad/main.py StayMenu: holds the panel; a click on a choice applies it and it stays open."""

    def mouseReleaseEvent(self, e):
        pass


def _preview_class():
    from prostocad import dimension
    from prostocad import theme as nt

    class Preview(dimension.Preview):
        """The new program's sample line, drawn in the old program's theme now on."""

        def paintEvent(self, e):
            keep = nt.cur()
            nt.use(_palette())
            try:
                super().paintEvent(e)
            finally:
                nt.use(keep)
    return Preview


class Panel(QtWidgets.QWidget):
    """The sections and their pages; `shell` is the old program's Shell (its handlers do the old settings' work)."""

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        from prostocad import dimension, feature_window
        from prostocad import theme as nt
        self.shell = shell
        self.setObjectName("SettingsPanel")
        p = _palette()
        self.setStyleSheet(nt.qss(p) + feature_window.qss(p))
        s = values()
        self.dim_look = dict(s["dim_look"])
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(12)
        self.sections = QtWidgets.QListWidget()
        self.sections.setObjectName("SettingsSections")
        self.sections.setFixedWidth(110)
        self.sections.setFocusPolicy(QtCore.Qt.NoFocus)
        pages = QtWidgets.QStackedWidget()
        self.sections.currentRowChanged.connect(pages.setCurrentIndex)
        lay.addWidget(self.sections)
        lay.addWidget(pages, 1)
        self.buttons = {}  # (section row, key) -> {choice: button}

        def section(title):
            page = QtWidgets.QWidget()
            col = QtWidgets.QVBoxLayout(page)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(4)
            self.sections.addItem(title)
            pages.addWidget(page)
            return col

        # ---- Theme: the system's, light or dark (the user: "Match System Settings", not everyone is on a Mac)
        col = section("Theme")
        self.buttons["theme"] = _choice_row(col, "Theme", ["auto", "light", "dark"], prefs.theme(), self.set_theme,
                                            {"auto": "Match System Settings", "light": "Light", "dark": "Dark"})
        col.addStretch(1)
        # ---- Text Size: the drawing's (the trial has no tree or ribbon sizes) and the constraint marks
        col = section("Text Size")
        self.buttons["drawing"] = _choice_row(col, "Drawing", list(nt.TEXT_SIZES), s["drawing"], self.set_drawing)
        self.buttons["marks"] = _choice_row(col, "Constraint Marks", list(MARK_SIZES), s["mark_px"], self.set_marks)
        col.addStretch(1)
        # ---- Dimensions: as the new program's, the line below shows them at once
        col = section("Dimensions")
        for key, label in (("size", "Size"), ("arrows", "Arrows"), ("value", "Value"), ("label", "Label"),
                           ("wheel", "Wheel Step")):
            self.buttons["dim_" + key] = _choice_row(col, label, list(dimension.CHOICES[key]), self.dim_look[key],
                                                     lambda val, k=key: self.set_dimension(k, val), width=60)
        self.buttons["inches"] = _choice_row(col, "Inches", ["Decimals", "Fractions"], s["inches"], self.set_inches,
                                             width=90)
        self.preview = _preview_class()(self.dim_look)
        col.addWidget(self.preview)
        col.addStretch(1)
        # ---- Sketch Colors: the lines by how far they are held, a finished sketch; each theme keeps its own
        col = section("Sketch Colors")
        self.color_buttons = {}
        for key, label in COLOR_ROWS:
            row = QtWidgets.QWidget()
            row.setObjectName("ChoiceRow")
            rl = QtWidgets.QHBoxLayout(row)
            rl.setContentsMargins(0, 1, 0, 1)
            name = QtWidgets.QLabel(label)
            name.setFixedWidth(150)
            rl.addWidget(name)
            b = QtWidgets.QPushButton()
            b.setFixedSize(60, 20)
            b.setFocusPolicy(QtCore.Qt.NoFocus)
            b.clicked.connect(lambda _=False, k=key: self._ask_color(k))
            rl.addWidget(b)
            rl.addStretch(1)
            col.addWidget(row)
            self.color_buttons[key] = b
        from prostocad.sketch import CURSOR_SHAPES
        self.buttons["cursor_shape"] = _choice_row(col, "Cursor Shape", list(CURSOR_SHAPES), s["cursor_shape"],
                                                   self.set_cursor_shape, CURSOR_NAMES, width=100)
        back = QtWidgets.QPushButton("Reset Colors")
        back.setFocusPolicy(QtCore.Qt.NoFocus)
        back.clicked.connect(self.reset_colors)
        col.addWidget(back, 0, QtCore.Qt.AlignLeft)
        col.addStretch(1)
        self._paint_swatches()
        # ---- Sketch
        col = section("Sketch")
        self.buttons["grid"] = _choice_row(col, "Grid", ["On", "Off"], "On" if s["show_grid"] else "Off",
                                           self.set_show_grid)
        self.buttons["snap"] = _choice_row(col, "Snap to Grid", list(SNAP_GRID), s["snap_grid"], self.set_snap_grid)
        self.buttons["projected"] = _choice_row(col, "Projected Edges", ["normal", "reference"], prefs.projected_edges(),
                                                self.set_projected_edges, {"normal": "Normal", "reference": "Reference"},
                                                width=90)
        self.buttons["touch"] = _choice_row(col, "Project What a Drawn Point Touches", ["On", "Off"],
                                            "On" if prefs.auto_project() else "Off",
                                            lambda k: prefs.set_auto_project(k == "On"), width=230)
        self.buttons["right_menu"] = _choice_row(col, "Right-Click Menu", ["Ring", "List"], s["right_menu"],
                                                 lambda k: store().setValue("right_menu", k))
        self.buttons["angle"] = _choice_row(col, "Angle", ["360", "180"], s["angle_mode"], self.set_angle,
                                            {"360": "0 … 360°", "180": "−180 … +180°"}, width=90)
        col.addStretch(1)
        # ---- View
        col = section("View")
        self.buttons["quality"] = _choice_row(col, "Display Quality", ["normal", "fine"], prefs.display_quality(),
                                              self.set_quality, {"normal": "Normal", "fine": "Fine"})
        self.buttons["nav"] = _choice_row(col, "Mouse Navigation", ["On", "Off"],
                                          "On" if prefs.navigation_enabled() else "Off", self.set_navigation)
        col.addStretch(1)
        # ---- Other
        col = section("Other")
        from incad.ui import ribbon
        ribbon_size = shell.ribbon.size if getattr(shell, "ribbon", None) is not None else prefs.ribbon_size()
        self.buttons["ribbon"] = _choice_row(col, "Ribbon Size", list(ribbon.SIZES), ribbon_size, self.set_ribbon_size,
                                             ribbon.SIZE_LABELS, width=90)
        units_now = prefs.default_units() if prefs.remember_new_choice() else "ask"
        self.buttons["units"] = _choice_row(col, "Units for New Files", ["ask", "mm", "in"], units_now, self.set_units,
                                            {"ask": "Ask Every Time", "mm": "Millimeters", "in": "Inches"}, width=130)
        self.buttons["updates"] = _choice_row(col, "Check for Updates at Start", ["On", "Off"],
                                              "On" if prefs.update_check_at_start() else "Off",
                                              lambda k: prefs.set_update_check_at_start(k == "On"), width=180)
        reset = QtWidgets.QPushButton("Reset to Defaults")
        reset.setFocusPolicy(QtCore.Qt.NoFocus)
        reset.clicked.connect(self._ask_reset)
        col.addWidget(reset, 0, QtCore.Qt.AlignLeft)
        col.addStretch(1)
        row = QtWidgets.QHBoxLayout()  # the commands at the bottom of Other (the user)
        row.setSpacing(6)
        for text, slot in (("Show Log File", lambda: shell._show_log()), ("Report a Bug…", lambda: shell.report_bug()),
                           ("Check for Updates…", lambda: shell.check_updates())):
            b = QtWidgets.QPushButton(text)
            b.setFocusPolicy(QtCore.Qt.NoFocus)
            b.clicked.connect(lambda _=False, f=slot: self._command(f))
            row.addWidget(b)
            self.buttons.setdefault("commands", {})[text] = b
        row.addStretch(1)
        col.addLayout(row)
        self.sections.setCurrentRow(0)

    # ----- the old program's settings: by the same code as its ⚙ menu -----
    def _close(self):
        m = self.parent()
        while m is not None and not isinstance(m, QtWidgets.QMenu):
            m = m.parent()
        if m is not None:
            m.close()

    def _command(self, f):
        self._close()
        QtCore.QTimer.singleShot(0, f)

    def set_theme(self, value):
        self._close()  # the panel was drawn in the old theme: it opens again in the new one
        QtCore.QTimer.singleShot(0, lambda: self.shell._set_theme_pref(value))

    def set_ribbon_size(self, size):
        if getattr(self.shell, "ribbon", None) is not None:
            self.shell.ribbon.set_size(size)
        else:
            prefs.set_ribbon_size(size)

    def set_units(self, value):
        self.shell._set_new_units(value)

    def set_projected_edges(self, value):
        self.shell._set_projected_edges(value)

    def set_quality(self, value):
        self.shell.set_display_quality(value)

    def set_navigation(self, key):
        self.shell.set_navigation(key == "On")

    # ----- the new sketch's own: as the new program's main window -----
    def set_drawing(self, size):
        store().setValue("text_drawing", size)
        _apply_open()

    def set_marks(self, px):
        store().setValue("mark_px", int(px))
        _apply_open()

    def set_dimension(self, key, value):
        self.dim_look[key] = value
        if key == "label":
            prefs.set_dimension_display(DISPLAY_OF.get(value, "value"))  # the old Dimension Display, the same choice
        else:
            store().setValue("dim_" + key, value)
        self.preview.update()
        _apply_open()

    def set_inches(self, value):
        from prostocad import units
        units.fractions = value == "Fractions"
        store().setValue("inch_look", value)
        _apply_open()

    def set_show_grid(self, key):
        store().setValue("show_grid", key)
        _apply_open()

    def set_cursor_shape(self, key):
        store().setValue("cursor_shape", key)
        _apply_open()

    def set_snap_grid(self, key):
        store().setValue("snap_grid", key)
        _apply_open()

    def set_angle(self, key):
        store().setValue("angle_mode", key)
        _apply_open()

    def _ink(self, key):
        """A line's colour now: the one chosen, else the theme's (prostocad/sketch.py state_ink)."""
        chosen = values()["colors"].get(key)
        if chosen:
            return QtGui.QColor(chosen)
        p = _palette()
        from prostocad.sketch import CURSOR_INK
        return QtGui.QColor({"free": p["state_free"], "part": p["state_part"], "done": p["accent"], "snap": p["snap"],
                             "ref": p["text_dim"], "cursor": CURSOR_INK}.get(key, p["text"] if p["name"] == "light"
                                                                           else p["geometry"]))

    def _paint_swatches(self):
        for key, b in self.color_buttons.items():
            c = self._ink(key).name()
            b.setStyleSheet("QPushButton { background: %s; border: 1px solid #777; border-radius: 4px; }" % c)

    def _ask_color(self, key):
        c = QtWidgets.QColorDialog.getColor(self._ink(key), self, dict(COLOR_ROWS)[key])
        if c.isValid():
            store().setValue(_color_key(key), c.name())
            self._paint_swatches()
            _apply_open()

    def reset_colors(self):
        st = store()
        for key, _l in COLOR_ROWS:
            st.remove(_color_key(key))
        self._paint_swatches()
        _apply_open()

    def _ask_reset(self):
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Reset to Defaults")
        box.setText("Reset all settings to defaults?")
        box.setInformativeText("Theme, text sizes, dimensions, colors, sketch, view and ribbon. This cannot be undone. "
                               "Your sketch is not touched.")
        yes = box.addButton("Reset", QtWidgets.QMessageBox.DestructiveRole)
        box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is yes:
            self.reset_defaults()

    def reset_defaults(self):
        """Every setting here back as it comes (prostocad/main.py reset_defaults: each choice clicked back)."""
        from prostocad import dimension
        from prostocad import theme as nt
        st = store()
        for key, _l in COLOR_ROWS:
            for name in ("light", "dark"):
                st.remove("color_%s_%s" % (key, name))
        for key, value in dimension.DEFAULTS.items():
            self.buttons["dim_" + key][value].click()
        self.buttons["inches"]["Decimals"].click()
        self.buttons["drawing"][nt.TEXT_DEFAULTS["drawing"]].click()
        self.buttons["marks"]["18"].click()
        self.buttons["cursor_shape"]["large"].click()
        self.buttons["grid"]["On"].click()
        self.buttons["snap"]["Off"].click()
        self.buttons["projected"]["normal"].click()
        self.buttons["touch"]["On"].click()
        self.buttons["right_menu"]["Ring"].click()
        self.buttons["angle"]["360"].click()
        self.buttons["quality"]["fine" if host.MAC else "normal"].click()
        self.buttons["nav"]["On"].click()
        self.buttons["ribbon"]["large"].click()
        self.buttons["units"]["ask"].click()
        self.buttons["updates"]["On"].click()
        self._paint_swatches()
        if prefs.theme() != "auto":
            self.buttons["theme"]["auto"].click()


def menu(shell, parent):
    """The ⚙ button's menu: the panel in it (prostocad/main.py: a QWidgetAction in a StayMenu)."""
    m = StayMenu(parent)
    panel = Panel(shell, m)
    act = QtWidgets.QWidgetAction(m)
    act.setDefaultWidget(panel)
    m.addAction(act)
    m.panel = panel
    return m
