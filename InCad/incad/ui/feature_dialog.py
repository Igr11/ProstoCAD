"""Floating feature dialog (Inventor style): fields built from the feature kind's FIELDS, live changes, an error
line, OK / Apply / Cancel. Value fields accept units, parameter names and formulas; clicking a dimension or a
parameter while one has focus inserts the name."""
import re

from incad.qt import QtCore, QtWidgets, Signal
from incad.ui import theme as themes


_NUMBER = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)\s*([^\d\s][^\d]*?)?\s*$")


def step_text(text, delta, kind, signed=False, size=None):
    """The text of a value field one step up or down (delta ±1, ±10 with Shift): 1 of the unit shown (1 mm, 1°,
    1 count; 1/16 in) or the field's own step, the number of decimals kept. None when the text is a formula, which
    cannot be stepped; a length never goes below zero, unless it is signed (a level: below zero is the other side)."""
    m = _NUMBER.match(text or "")
    if m is None:
        return None
    number, unit = m.group(1), m.group(2) or ""
    if unit and not re.match(r"^(mm|cm|m|in|\"|ft|'|°|deg|rad|µm|um)$", unit):
        return None
    step = 0.0625 if unit in ("in", '"') else 1.0
    if size and unit in ("", "mm"):
        step = float(size)  # the field's own step (3D Line's At: one step of the cube)
    value = float(number) + delta * step
    if kind != "value_angle" and not signed and value < 0:
        value = 0.0
    decimals = len(number.split(".")[1]) if "." in number else 0
    if size:
        decimals = max(decimals, len(("%g" % step).partition(".")[2]))  # a 2.5 mm step keeps its half
    if unit in ("in", '"'):
        decimals = max(decimals, 4)
    out = "%.*f" % (decimals, value)
    if "." in out:
        out = out.rstrip("0").rstrip(".")
    head = text[:text.index(number)] if number in text else ""
    between = text[m.end(1):m.start(2)] if unit else ""
    return (head.strip() + out + between + unit).strip()


class _StepButton(QtWidgets.QToolButton):
    """An arrow button that remembers whether Shift was down at the press (auto-repeat keeps that)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.big = False

    def mousePressEvent(self, event):
        import time
        self.big = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
        self._last_step = time.monotonic()  # the first auto-repeat comes 0.3 s after the press (FeatureDialog._arrow)
        super().mousePressEvent(event)


class ValueField(QtWidgets.QLineEdit):
    stepped = Signal(int)  # ±1 (±10 with Shift) from the Up/Down keys
    wheel_steps = False    # a compact field without arrows: the wheel over it steps it too

    def wheelEvent(self, event):
        if not self.wheel_steps or not event.angleDelta().y():
            return super().wheelEvent(event)
        big = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
        self.stepped.emit((1 if event.angleDelta().y() > 0 else -1) * (10 if big else 1))
        event.accept()

    def insert(self, name):
        super().insert(name)
        self.setFocus()

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down):
            big = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
            self.stepped.emit((1 if event.key() == QtCore.Qt.Key_Up else -1) * (10 if big else 1))
            return
        super().keyPressEvent(event)


class SliderField(QtWidgets.QWidget):
    """A slider with its value in % beside it; changes as it moves."""
    changed = Signal()

    def __init__(self, low, high, parent=None):
        super().__init__(parent)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal, self)
        self.slider.setRange(int(low), int(high))
        self.label = QtWidgets.QLabel(self)
        self.label.setFixedWidth(44)
        row.addWidget(self.slider, 1)
        row.addWidget(self.label)
        self.slider.valueChanged.connect(self._moved)
        self._moved(self.slider.value(), quiet=True)

    def _moved(self, v, quiet=False):
        self.label.setText("%d %%" % v)
        if not quiet:
            self.changed.emit()

    def value(self):
        return self.slider.value()

    def set_value(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(int(v))
        self.slider.blockSignals(False)
        self._moved(self.slider.value(), quiet=True)


class PointsField(QtWidgets.QWidget):
    """'x y z; …' clicked in the view: shows how many, with Undo (the last one) and Clear."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.count = QtWidgets.QLabel("")
        row.addWidget(self.count, 1)
        self.undo = QtWidgets.QToolButton(self)
        self.undo.setText("Undo")
        self.clear = QtWidgets.QToolButton(self)
        self.clear.setText("Clear")
        for b in (self.undo, self.clear):
            b.setObjectName("InCadClear")
            b.setFocusPolicy(QtCore.Qt.NoFocus)
            row.addWidget(b)
        self.undo.clicked.connect(lambda: self._drop(last=True))
        self.clear.clicked.connect(lambda: self._drop(last=False))
        self._text = ""
        self.set_text("")

    def _items(self):
        return [i for i in self._text.split(";") if i.strip()]

    def text(self):
        return self._text

    def set_text(self, text):
        self._text = text or ""
        n = len(self._items())
        self.count.setText("No points yet" if not n else "1 point" if n == 1 else "%d points" % n)
        self.undo.setEnabled(n > 0)
        self.clear.setEnabled(n > 0)

    def add(self, item):
        self.set_text("; ".join(self._items() + [item]).strip())
        self.changed.emit()

    def _drop(self, last):
        items = self._items()
        self.set_text("; ".join(items[:-1]) if last else "")
        self.changed.emit()


class ChoiceField(QtWidgets.QWidget):
    """Segment buttons, at most PER_ROW in a row, so a long list of choices does not make the dialog too wide.
    `multi` (a "toggles" field): each one on or off by itself, the value the "on" ones joined by commas."""
    changed = Signal()
    PER_ROW = 4

    def __init__(self, choices, parent=None, multi=False):
        super().__init__(parent)
        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(not multi)
        self._multi = multi
        self.PER_ROW = 6 if multi else self.PER_ROW
        self._buttons = {}
        for i, (value, label) in enumerate(choices):
            b = QtWidgets.QToolButton(self)
            b.setText(label)
            b.setCheckable(True)
            b.setObjectName("InCadSegment")
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            self._group.addButton(b)
            grid.addWidget(b, i // self.PER_ROW, i % self.PER_ROW)
            self._buttons[value] = b
            b.clicked.connect(lambda _=False: self.changed.emit())

    def button(self, value):
        return self._buttons[value]

    def value(self):
        if self._multi:
            return ",".join(v for v, b in self._buttons.items() if b.isChecked())
        for v, b in self._buttons.items():
            if b.isChecked():
                return v
        return None

    def set_value(self, value):
        if self._multi:
            on = set(str(value or "").split(","))
            for v, b in self._buttons.items():
                b.setChecked(v in on)
        elif value in self._buttons:
            self._buttons[value].setChecked(True)


class _FieldsArea(QtWidgets.QScrollArea):
    """The dialog's fields: as tall as they are, but never past the view it sits in (the user: Worm's fields ran off
    the screen and took OK with them) — then they scroll, and the line under them and the buttons stay in sight."""
    ROOM = 170  # px the dialog's title, info / error lines, buttons and margins take beside the fields

    def __init__(self, dialog, box):
        super().__init__(dialog)
        self._dialog = dialog
        self.setObjectName("InCadFieldsArea")
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setWidgetResizable(True)
        self.setWidget(box)
        self.viewport().setAutoFillBackground(False)
        self._refit = QtCore.QTimer(self)  # the fields re-laid out (styled once shown, a row shown or hidden):
        self._refit.setSingleShot(True)    # the dialog fits them again, once per burst
        self._refit.setInterval(0)
        self._refit.timeout.connect(lambda: dialog._fit_if_alive())
        box.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.widget() and event.type() == QtCore.QEvent.LayoutRequest:
            self._refit.start()
        return False

    def _limit(self):
        parent = self._dialog.parentWidget()
        if parent is None:
            return 10 ** 6
        return max(160, parent.height() - 32 - self.ROOM)

    def sizeHint(self):
        inner = self.widget().sizeHint()
        h = min(inner.height(), self._limit())
        # the scroll bar's room always: taken out of the fields when it appears, it pushed the ▲▼ out of sight
        bar = max(self.verticalScrollBar().sizeHint().width(),
                  self.style().pixelMetric(QtWidgets.QStyle.PM_ScrollBarExtent, None, self))
        return QtCore.QSize(inner.width() + bar + 2, h)

    def minimumSizeHint(self):
        return self.sizeHint()


class FeatureDialog(QtWidgets.QFrame):
    """A feature's settings over the 3D view. Its title row drags it (the user: "these windows sometimes take up more
    than half the screen ... the ability to move them"); the next dialog opens where the last one was left."""
    LONG_INFO = 110  # characters: a longer info line shows its first sentence and "more"
    spot = None  # QPoint where the user last left a dialog, kept for the next one
    changed = Signal(dict)
    accepted = Signal()
    applied = Signal()
    cancelled = Signal()
    pickRequested = Signal(str)
    activePickChanged = Signal(str)  # "" when picking ends
    picksEdited = Signal(str)  # a listed picks field changed from its rows (remove, reorder): the session reselects

    def __init__(self, title, icon, fields, values, theme="light", parent=None):
        super().__init__(parent)
        self.setObjectName("InCadFeatureDialog")
        self._fields = {f.key: f for f in fields}
        self._widgets, self._labels, self._rows = {}, {}, {}
        self._loading = False
        self._sent = {}  # the value fields' texts at the last change signal
        self._history, self._future, self._stepping = [], [], False  # Undo/Redo inside the dialog: its changes
        self._type_timer = QtCore.QTimer(self)  # a value typed previews by itself after a pause: no Enter, no
        self._type_timer.setSingleShot(True)    # focus change (a click in the 3D view takes no Qt focus)
        self._type_timer.setInterval(600)
        self._type_timer.timeout.connect(self._emit)
        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(10, 8, 10, 8)
        head = QtWidgets.QHBoxLayout()
        icon_label = QtWidgets.QLabel()
        icon_label.setPixmap(themes.icon(icon, theme).pixmap(18, 18))
        head.addWidget(icon_label)
        title_label = QtWidgets.QLabel(title)
        title_label.setObjectName("InCadDialogTitle")
        title_label.setCursor(QtCore.Qt.OpenHandCursor)
        title_label.setToolTip("Drag to move")
        head.addWidget(title_label, 1)
        self._drag = None
        col.addLayout(head)
        form = QtWidgets.QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(4)
        self._steppers, self._lists = {}, {}
        self._toggles = {}  # key -> (button, the value field it greys out)
        r = -1
        line = None  # the row same_row fields join: its layout
        for f in fields:
            if getattr(f, "same_row", False) and line is not None:
                small = QtWidgets.QLabel(f.label)
                widget = self._make(f)
                if isinstance(widget, ValueField):
                    widget.setMinimumWidth(0)
                    widget.setFixedWidth(max(58, widget.fontMetrics().horizontalAdvance("-188.8°") + 14))
                    widget.wheel_steps = True
                    widget.stepped.connect(lambda d, k=f.key: self._step(k, d))
                line.addWidget(small)
                line.addWidget(widget)
                self._labels[f.key], self._widgets[f.key], self._rows[f.key] = small, widget, (small, widget)
                continue
            r += 1
            if f.kind == "heading":  # a group's title over its fields (Spur Gears: Gear 1, Gear 2)
                head_label = QtWidgets.QLabel(f.label)
                head_label.setObjectName("InCadDialogSection")
                form.addWidget(head_label, r, 0, 1, 2)
                self._labels[f.key] = self._widgets[f.key] = head_label
                self._rows[f.key] = (head_label,)
                continue
            label = QtWidgets.QLabel(f.label)
            widget = self._make(f)
            self._widgets[f.key] = widget  # the arrows read it while the row is built
            row = self._with_arrows(f, widget) if f.kind in ("value_length", "value_angle") else \
                self._with_list(f, widget) if f.kind == "picks" and getattr(f, "listed", False) else widget
            if row is not widget and f.kind == "picks":  # a list under the field: its name stays at the top
                label.setFixedHeight(widget.sizeHint().height())
                form.addWidget(label, r, 0, QtCore.Qt.AlignTop)
            else:
                form.addWidget(label, r, 0)
            joined = [g for g in fields[fields.index(f) + 1:]]
            if joined and getattr(joined[0], "same_row", False):  # the next fields share this row
                holder = QtWidgets.QWidget(self)
                line = QtWidgets.QHBoxLayout(holder)
                line.setContentsMargins(0, 0, 0, 0)
                line.setSpacing(4)
                line.addWidget(row)
                row = holder
            else:
                line = None
            form.addWidget(row, r, 1)
            self._labels[f.key], self._widgets[f.key], self._rows[f.key] = label, widget, (label, row)
        form.setColumnStretch(1, 1)  # spare width goes to the fields, never between the names and the fields
        box = QtWidgets.QWidget()
        box.setObjectName("InCadFieldRows")
        box.setAutoFillBackground(False)
        form.setContentsMargins(0, 0, 0, 0)
        box.setLayout(form)
        self._fields_area = _FieldsArea(self, box)
        col.addWidget(self._fields_area)
        self._info = QtWidgets.QLabel("")  # what the values make (a gear's diameters): never blocks OK
        self._info.setObjectName("InCadDialogInfo")
        self._info.setWordWrap(True)
        self._info.setTextFormat(QtCore.Qt.RichText)
        self._info.linkActivated.connect(self._toggle_info)
        self._info_full, self._info_open = "", False
        self._info.hide()
        col.addWidget(self._info)
        self._error = QtWidgets.QLabel("")
        self._error.setObjectName("InCadDialogError")
        self._error.setWordWrap(True)
        self._error.hide()
        col.addWidget(self._error)
        self.rescue_button = QtWidgets.QPushButton("")  # a way around the error, offered by the feature kind
        self.rescue_button.setObjectName("InCadDialogRescue")
        self.rescue_button.hide()
        self._rescue = None
        self.rescue_button.clicked.connect(lambda: self._rescue() if self._rescue is not None else None)
        col.addWidget(self.rescue_button)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        self.apply_button = QtWidgets.QPushButton("Apply +")
        self.ok_button = QtWidgets.QPushButton("OK")
        self.ok_button.setDefault(True)
        for b in (self.cancel_button, self.apply_button, self.ok_button):
            buttons.addWidget(b)
        self.cancel_button.clicked.connect(self.cancelled.emit)
        self.apply_button.clicked.connect(self.applied.emit)
        self.ok_button.clicked.connect(self.accepted.emit)
        col.addLayout(buttons)
        p = themes.PALETTES[theme]
        self.setStyleSheet(
            "QFrame#InCadFeatureDialog {{ background: {panel}; border: 1px solid {border}; border-radius: 9px; }}"
            " QLabel {{ color: {text}; }} QLabel#InCadDialogTitle {{ font-weight: 600; }}"
            " QLabel#InCadDialogSection {{ color: {accent}; font-weight: 600; padding-top: 6px; }}"
            " QLabel#InCadDialogInfo {{ color: {text_dim}; }} QLabel#InCadDialogError {{ color: {error}; }} QLabel#InCadDialogError[waiting=\"true\"] {{ color: {accent}; }}"
            " QLineEdit {{ background: {panel_alt}; color: {text}; border: 1px solid {border}; border-radius: 4px;"
            " padding: 2px 4px; }} QLineEdit:disabled {{ color: {text_dim}; }}"
            " QToolButton#InCadStep {{ background: {panel_alt}; color: {text};"
            " border: 1px solid {border}; padding: 0; font-size: 7px; }} QToolButton#InCadStep:disabled {{"
            " color: {text_dim}; }} QToolButton#InCadSegment {{ background: {panel_alt}; color: {text};"
            " border: 1px solid {border}; padding: 2px 8px; }} QToolButton#InCadSegment:checked {{"
            " background: {accent}; color: {selection_text}; }}"
            " QPushButton {{ background: {panel_alt}; color: {text}; border: 1px solid {border}; border-radius: 6px;"
            " padding: 4px 14px; }} QPushButton:default {{ background: {accent}; color: {selection_text}; }}"
            " QPushButton:disabled {{ color: {text_dim}; background: {panel}; }}"
            " QPushButton#InCadPick {{ border-radius: 4px; padding: 2px 8px; text-align: left; }}"
            " QPushButton#InCadPick:checked {{ border: 2px solid {accent}; }}"
            " QScrollArea#InCadFieldsArea, QWidget#InCadFieldRows {{ background: transparent; border: none; }}".format(**p))
        self.set_values(values)
        self._history = [self.values()]  # the floor: what the dialog opened with

    # ----- building -----
    def _make(self, f):
        if f.kind in ("value_length", "value_angle"):
            w = ValueField(self)
            w.setMinimumWidth(130)
            w.textChanged.connect(lambda _t: self._typed())
            w.editingFinished.connect(self._finished)
            w.returnPressed.connect(self.accepted.emit)
            w.installEventFilter(self)
            return w
        if f.kind == "text":  # words (Text): two lines tall, Return starts a new line; previews as you type
            w = QtWidgets.QPlainTextEdit(self)
            w.setMinimumWidth(260)
            w.setTabChangesFocus(True)
            line = w.fontMetrics().lineSpacing()
            w.setFixedHeight(int(line * 2.6 + 14))
            w.textChanged.connect(self._typed)
            return w
        if f.kind in ("choice", "toggles"):
            w = ChoiceField(f.choices, self, multi=f.kind == "toggles")
            w.changed.connect(self._emit)
            return w
        if f.kind == "combo":
            w = QtWidgets.QComboBox(self)
            w.setMinimumWidth(130)
            for value, label in f.choices:
                w.addItem(label, value)
            w.currentIndexChanged.connect(lambda _=0: self._emit())
            return w
        if f.kind == "bool":
            w = QtWidgets.QCheckBox(self)
            w.toggled.connect(lambda _=False: self._emit())
            return w
        if f.kind == "slider":  # a whole number dragged between choices[0] and choices[1] (a picture's transparency)
            w = SliderField(f.choices[0], f.choices[1], self)
            w.changed.connect(self._emit)
            return w
        if f.kind == "points":  # points clicked in the view (3D Line): how many, Undo the last, Clear
            w = PointsField(self)
            w.changed.connect(self._emit)
            return w
        w = QtWidgets.QPushButton("Select…", self)
        w.setMinimumWidth(200)  # a hint like "Click sketches in order…" shows whole
        w.setObjectName("InCadPick")
        w.setCheckable(True)
        w.clicked.connect(lambda _=False, k=f.key: self._pick_clicked(k))
        return w

    def _with_arrows(self, f, w):
        """The value field with its ▲▼ arrows: one step per press (Shift ×10), each press previews at once."""
        box = QtWidgets.QWidget(self)
        row = QtWidgets.QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        row.addWidget(w, 1)
        col = QtWidgets.QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        arrows = []
        for text, delta in (("▲", 1), ("▼", -1)):
            b = _StepButton(box)
            b.setText(text)
            b.setObjectName("InCadStep")
            b.setFixedSize(18, 11)
            b.setFocusPolicy(QtCore.Qt.NoFocus)
            b.setAutoRepeat(True)
            b.clicked.connect(lambda _=False, k=f.key, d=delta, b=b: self._arrow(b, k, d))
            b.released.connect(lambda b=b: self._arrow_released(b))
            col.addWidget(b)
            arrows.append(b)
        row.addLayout(col)
        if f.toggle:
            key, text = f.toggle
            t = QtWidgets.QToolButton(box)
            t.setText(text)
            t.setObjectName("InCadSegment")
            t.setCheckable(True)
            t.setFocusPolicy(QtCore.Qt.NoFocus)
            t.toggled.connect(lambda _on, k=key: self._toggled(k))
            row.addWidget(t)
            self._toggles[key] = (t, f.key)
        self._steppers[f.key] = tuple(arrows)
        w.stepped.connect(lambda d, k=f.key: self._step(k, d))
        w.textChanged.connect(lambda _t, k=f.key: self._update_arrows(k))
        self._update_arrows(f.key)
        return box

    def _with_list(self, f, button):
        """A picks field shown as Inventor shows Loft sections: the pick button, then one numbered row per pick
        with ▲ ▼ (order) and ✕ (remove)."""
        box = QtWidgets.QWidget(self)
        col = QtWidgets.QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        col.addWidget(button)
        rows = QtWidgets.QWidget(box)
        lay = QtWidgets.QVBoxLayout(rows)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        col.addWidget(rows)
        self._lists[f.key] = (rows, [])
        return box

    @staticmethod
    def _object_label(name):
        try:
            import FreeCAD as App
            obj = App.ActiveDocument.getObject(name) if App.ActiveDocument is not None else None
            return obj.Label if obj is not None else name
        except Exception:
            return name

    def _refresh_list(self, key):
        if key not in self._lists:
            return
        rows, old = self._lists[key]
        for w in old:
            w.setParent(None)
            w.deleteLater()
        items = [i.strip() for i in (self._widgets[key].property("pick") or "").split(",") if i.strip()]
        made = []
        for i, item in enumerate(items):
            r = QtWidgets.QWidget(rows)
            h = QtWidgets.QHBoxLayout(r)
            h.setContentsMargins(4, 0, 0, 0)
            h.setSpacing(2)
            label = QtWidgets.QLabel("%d.  %s" % (i + 1, self._object_label(item.partition(":")[0])), r)
            h.addWidget(label, 1)
            buttons = {}
            for name, text, tip in (("up", "▲", "Earlier in the loft"), ("down", "▼", "Later in the loft"),
                                    ("remove", "✕", "Remove this section")):
                b = QtWidgets.QToolButton(r)
                b.setText(text)
                b.setToolTip(tip)
                b.setObjectName("InCadStep")
                b.setFixedSize(18, 16)
                b.setFocusPolicy(QtCore.Qt.NoFocus)
                b.clicked.connect(lambda _=False, k=key, n=i, a=name: self._edit_list(k, n, a))
                h.addWidget(b)
                buttons[name] = b
            buttons["up"].setEnabled(i > 0)
            buttons["down"].setEnabled(i < len(items) - 1)
            r.label, r.buttons = label, buttons
            rows.layout().addWidget(r)
            made.append(r)
        self._lists[key] = (rows, made)
        rows.setVisible(bool(made))
        self._widgets[key].setText(self._list_hint(items))
        self._fit()

    @staticmethod
    def _list_hint(items):
        return "Click sketches in order…" if not items else "Click to add more…"

    def _edit_list(self, key, index, action):
        items = [i.strip() for i in (self._widgets[key].property("pick") or "").split(",") if i.strip()]
        if action == "remove":
            items.pop(index)
        elif action == "up" and index > 0:
            items[index - 1], items[index] = items[index], items[index - 1]
        elif action == "down" and index < len(items) - 1:
            items[index + 1], items[index] = items[index], items[index + 1]
        else:
            return
        self.set_pick(key, ",".join(items))
        self.picksEdited.emit(key)

    def list_rows(self, key):
        """The texts of a listed picks field's rows (tests)."""
        return [r.label.text() for r in self._lists[key][1]]

    def row_button(self, key, index, name):
        """A row's 'up' / 'down' / 'remove' button (tests)."""
        return self._lists[key][1][index].buttons[name]

    def _toggled(self, key):
        self._grey_toggled(key)
        if not self._loading:
            self._emit()

    def _grey_toggled(self, key):
        button, value_key = self._toggles[key]
        on = button.isChecked()
        self._widgets[value_key].setEnabled(not on)
        if on:
            for b in self._steppers.get(value_key, ()):
                b.setEnabled(False)
        else:
            self._update_arrows(value_key)

    def toggle(self, key):
        return self._toggles[key][0]

    def _update_arrows(self, key):
        f = self._fields[key]
        can = step_text(self._widgets[key].text(), 1, f.kind, getattr(f, "signed", False)) is not None \
            and self._widgets[key].isEnabled()  # greyed by its toggle (Fillet ▸ Full): no steps
        for b in self._steppers.get(key, ()):
            b.setEnabled(can)

    def _arrow(self, button, key, delta):
        """An arrow click, or its auto-repeat while held. A repeat that comes after a slow preview held up the
        window means the release may have been lost then: the button lets go (the user: the pitch climbed by itself
        to 1243 mm, one millimetre a repeat, the button stuck down)."""
        import time
        now = time.monotonic()
        last, button._last_step = getattr(button, "_last_step", 0.0), now
        if button.isDown() and now - last > 0.6:
            button.setDown(False)
            self._arrow_released(button)
            return
        held = button.isDown()  # an auto-repeat while held: the number only, the preview once let go (the user
        # 2026-11-08: each repeat previewed - Emboss 0.4 s - the release came late and the number ran on by itself)
        if self._step(key, delta * (10 if button.big else 1), preview=not held) and held:
            button._held_key = key
        button._last_step = time.monotonic()  # the gap to the next repeat counts from the end of this one

    def _arrow_released(self, button):
        """The held arrow let go: the steps made while it was held preview now, once."""
        if getattr(button, "_held_key", None) is not None:
            button._held_key = None
            self._emit()

    def _step(self, key, delta, preview=True):
        w = self._widgets[key]
        f = self._fields[key]
        size = None
        if getattr(f, "step", None) is not None:
            try:
                size = f.step(self.values())
            except Exception:
                size = None
        text = step_text(w.text(), delta, f.kind, getattr(f, "signed", False), size)
        if text is None or text == w.text():
            return False
        self._type_timer.stop()
        w.setText(text)
        self._type_timer.stop()  # (the text change started it: no preview of its own while the arrow is held)
        if preview:
            self._emit()  # one preview per press, no waiting for the typing pause
        return True

    def step_buttons(self, key):
        """(up, down) arrow buttons of a value field (tests)."""
        return self._steppers[key]

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.FocusIn and isinstance(obj, ValueField):
            self._last_value_field = obj
            if self.active_pick() is not None:
                self.set_active_pick(None)  # typing a value ends picking (the preview shows again)
        if event.type() == QtCore.QEvent.KeyPress and event.key() == QtCore.Qt.Key_Escape:
            self.cancelled.emit()
            return True
        return False

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self.cancelled.emit()
            return
        super().keyPressEvent(event)

    # ----- values -----
    def values(self):
        out = {}
        for key, f in self._fields.items():
            w = self._widgets[key]
            if f.kind == "heading":
                continue
            if f.kind in ("value_length", "value_angle"):
                out[key] = w.text().strip()
            elif f.kind == "text":
                out[key] = w.toPlainText()
            elif f.kind in ("choice", "toggles"):
                out[key] = w.value()
            elif f.kind == "combo":
                out[key] = w.currentData() if w.currentIndex() >= 0 else ""
            elif f.kind == "bool":
                out[key] = w.isChecked()
            elif f.kind == "points":
                out[key] = w.text()
            elif f.kind == "slider":
                out[key] = w.value()
            else:
                out[key] = w.property("pick") or ""
        for key, (button, _value_key) in self._toggles.items():
            out[key] = button.isChecked()
        return out

    def set_values(self, values):
        self._loading = True
        try:
            for key, value in values.items():
                if key in self._toggles:
                    self._toggles[key][0].setChecked(bool(value))
                    self._grey_toggled(key)
                    continue
                f, w = self._fields.get(key), self._widgets.get(key)
                if f is None or f.kind == "heading":
                    continue
                if f.kind in ("value_length", "value_angle"):
                    w.setText(str(value))
                elif f.kind == "text":
                    if w.toPlainText() != str(value or ""):  # (setting it again would move the cursor home)
                        w.setPlainText(str(value or ""))
                elif f.kind in ("choice", "toggles"):
                    w.set_value(value)
                elif f.kind == "combo":
                    i = w.findData(value)
                    if i >= 0:
                        w.setCurrentIndex(i)
                elif f.kind == "bool":
                    w.setChecked(bool(value))
                elif f.kind == "points":
                    w.set_text(value or "")
                elif f.kind == "slider":
                    w.set_value(int(value or 0))
                else:
                    w.setProperty("pick", value or "")
                    w.setText(self._pick_label(f, value))
                    self._refresh_list(key)
        finally:
            self._loading = False
            self._sent = self._value_texts()
        self._update_visibility()

    def _value_texts(self):
        return {k: w.text() for k, w in self._widgets.items() if self._fields[k].kind.startswith("value")}

    def _typed(self):
        if not self._loading:
            self._type_timer.start()

    def _finished(self):
        """Enter or a focus change: preview now if a typed value is still waiting, or if the text changed since
        the last signal; a value already previewed is not previewed again."""
        if self._type_timer.isActive():
            self._type_timer.stop()
            self._emit()
        elif self._value_texts() != self._sent:
            self._emit()

    def _emit(self):
        self._type_timer.stop()
        self._update_visibility()
        if not self._loading:
            self._sent = self._value_texts()
            values = self.values()
            if not self._stepping and (not self._history or values != self._history[-1]):
                self._history.append(values)
                self._future = []  # a new change drops the redo trail
            self.changed.emit(values)

    # ----- Undo / Redo inside the dialog -----
    def reset_history(self):
        """The values now become the floor: what the dialog 'opened with' once its seeding (the selection made
        before the command, a kind's defaults) is in."""
        self._history, self._future = [self.values()], []

    def undo_change(self):
        """Back to the values before the last change made in this dialog (previews like any change). False when
        the dialog is at the values it opened with."""
        if len(self._history) < 2:
            return False
        self._future.append(self._history.pop())
        return self._step_to(self._history[-1])

    def redo_change(self):
        if not self._future:
            return False
        values = self._future.pop()
        self._history.append(values)
        return self._step_to(values)

    def _step_to(self, values):
        self._stepping = True
        try:
            self.set_values(values)
            self._emit()
        finally:
            self._stepping = False
        return True

    def _update_visibility(self):
        from incad.features.common import shown as _shown
        values = self.values()
        changed = False
        for key, f in self._fields.items():
            shown = _shown(f, values)
            for w in self._rows[key]:
                if w.isHidden() == shown:
                    changed = True
                w.setVisible(shown)
        if changed and self.isVisible():
            self._fit()

    # ----- helpers for the session and tests -----
    def field(self, key):
        return self._widgets[key]

    def row_visible(self, key):
        return not self._rows[key][-1].isHidden()

    def label_text(self, key):
        return self._labels[key].text()

    def set_param_name(self, key, name):
        f = self._fields[key]
        self._labels[key].setText("%s  (%s)" % (f.label, name) if name else f.label)

    def _pick_label(self, f, value):
        from incad.features.common import pick_label
        return pick_label(f.pick or "item", value or "")

    def _pick_clicked(self, key):
        """A click on the active field stops picking, unless the dialog made it active by itself (at the start, or
        when a choice showed it): then the click only confirms it, as the user expected to start picking."""
        if key == self.active_pick() and getattr(self, "_clicked_active", False):
            self.set_active_pick(None)
            return
        if key == self.active_pick():
            self.set_active_pick(key)  # the button unchecked itself on the click: show it pressed again
            self._clicked_active = True
            return
        self.set_active_pick(key)
        self._clicked_active = True
        self.pickRequested.emit(key)

    def set_active_pick(self, key):
        """The pick field that takes the next selection in the 3D view (one at a time, shown pressed)."""
        for k, f in self._fields.items():
            if f.kind in ("pick", "picks"):
                self._widgets[k].setChecked(k == key)
        changed = key != self.active_pick()
        self._active_pick = key
        self._clicked_active = False  # made active from code; _pick_clicked marks its own
        if changed:
            self.activePickChanged.emit(key or "")

    def active_pick(self):
        return getattr(self, "_active_pick", None)

    def set_pick(self, key, text):
        w = self._widgets[key]
        w.setProperty("pick", text or "")
        w.setText(self._pick_label(self._fields[key], text))
        self._refresh_list(key)
        self._emit()

    def field_def(self, key):
        return self._fields[key]

    def set_pick_text(self, key, text):
        if key in self._lists:
            return  # the rows say what is picked
        self._widgets[key].setText(text or "Select…")

    def pick_text(self, key):
        return self._widgets[key].text()

    def set_options(self, key, options):
        """Replace a combo's items; keeps the current value when still offered, else the first. No signal."""
        w = self._widgets[key]
        current = w.currentData()
        was = self._loading
        self._loading = True
        try:
            w.clear()
            for value, label in options:
                w.addItem(label, value)
            i = w.findData(current)
            w.setCurrentIndex(i if i >= 0 else (0 if w.count() else -1))
        finally:
            self._loading = was
        self._update_visibility()

    def set_choice_enabled(self, key, value, enabled):
        self._widgets[key].button(value).setEnabled(enabled)

    def set_error(self, text, rescue=None, waiting=False):
        """Show why the feature fails; `rescue` = (button label, callable) offers a way around it. `waiting`: the
        feature is not made yet because the picks are still coming (shown grey, it is no error)."""
        self._error.setProperty("waiting", bool(waiting and text))
        self._error.style().unpolish(self._error)
        self._error.style().polish(self._error)
        self._error.setText(text)
        self._error.setVisible(bool(text))
        self.ok_button.setEnabled(not text)
        self.apply_button.setEnabled(not text)
        self._rescue = rescue[1] if rescue else None
        self.rescue_button.setText(rescue[0] if rescue else "")
        self.rescue_button.setVisible(bool(text and rescue))
        self._fit()  # grow (or shrink back) so the reason never squeezes the fields

    def _fit(self):
        """Grow or shrink to what is shown now (rows shown or hidden, a message): the fields' area first."""
        area = getattr(self, "_fields_area", None)
        if area is not None:
            area.widget().adjustSize()
            hint = area.sizeHint()
            area.setMinimumWidth(hint.width())  # the layout keeps a stale hint otherwise: the ▲▼ went out of sight
            area.setFixedHeight(hint.height())
            width = max(260, hint.width())  # the lines under the fields wrap at the fields' width: a long
            for label in (self._info, self._error):  # hint made the dialog half the screen wide
                label.setMaximumWidth(width)
        self.adjustSize()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit()

    def _fit_if_alive(self):
        try:
            if self.isVisible() and not getattr(self, "_fitting", False):
                self._fitting = True  # a fit re-lays the fields out: never a loop
                try:
                    self._fit()
                finally:
                    self._fitting = False
        except RuntimeError:
            pass

    def set_info(self, text):
        """A line on what the values make (a kind's info hook): grey, OK stays as it is. A long one shows its first
        sentence and "more" (the dialog stays small); "less" folds it again."""
        if text != self._info_full:
            self._info_full = text
            self._show_info_text()

    def _show_info_text(self):
        import html
        text = self._info_full
        first = text.split(". ")[0]
        if len(text) <= self.LONG_INFO or len(first) >= len(text) - 2:
            shown = html.escape(text)
        elif self._info_open:
            shown = '%s <a href="less">less</a>' % html.escape(text)
        else:
            shown = '%s… <a href="more">more</a>' % html.escape(first.rstrip(".:"))
        self._info.setText(shown)
        self._info.setVisible(bool(text))
        self._fit()

    def _toggle_info(self, _link=""):
        self._info_open = not self._info_open
        self._show_info_text()

    def info_text(self):
        return self._info_full

    # ----- moving: the title row drags the dialog inside the view -----
    def mousePressEvent(self, event):
        child = self.childAt(event.position().toPoint() if hasattr(event, "position") else event.pos())
        if event.button() == QtCore.Qt.LeftButton and (child is None or isinstance(child, QtWidgets.QLabel)
                                                        and child.objectName() in ("InCadDialogTitle", "")
                                                        and child.parentWidget() is self):
            self._drag = event.globalPosition().toPoint() - self.pos() if hasattr(event, "globalPosition") \
                else event.globalPos() - self.pos()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            at = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
            self.move(self.kept_inside(at - self._drag))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            self._drag = None
            self.unsetCursor()
            FeatureDialog.spot = self.pos()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def kept_inside(self, point):
        """point moved so the dialog's title row stays in the view (never dragged out of reach)."""
        parent = self.parentWidget()
        if parent is None:
            return point
        x = min(max(point.x(), 40 - self.width()), parent.width() - 40)
        y = min(max(point.y(), 0), parent.height() - 30)
        return QtCore.QPoint(x, y)

    def place(self):
        """Where the last dialog was left (kept inside this view), else the view's top left corner."""
        self.move(self.kept_inside(FeatureDialog.spot) if FeatureDialog.spot is not None else QtCore.QPoint(16, 16))

    def error_text(self):
        return self._error.text()

    def error_waiting(self):
        return bool(self._error.property("waiting"))

    def focused_value_field(self):
        for key, f in self._fields.items():
            w = self._widgets[key]
            if f.kind in ("value_length", "value_angle") and w.hasFocus():
                return w
        return getattr(self, "_last_value_field", None)
