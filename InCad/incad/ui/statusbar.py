"""InCad status bar: next-step prompt, messages, units, sketch DOF, part statistics."""
from incad.qt import QtCore, QtWidgets, Signal
from incad.ui import theme as themes

UNIT_TEXT = {"mm": "mm · deg", "in": "in · deg"}


def _plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


class _Elided(QtWidgets.QLabel):
    """A label that never widens the window: a text longer than its room is cut with … (the whole of it in the tip).
    A long message (a save failed, with the file's path) pushed the window's right side off the screen (the user)."""

    def __init__(self, text=""):
        super().__init__(text)
        self._full = text
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.setMinimumWidth(40)

    def setText(self, text):
        self._full = text or ""
        self._fit()

    def text(self):
        return self._full

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _fit(self):
        shown = self.fontMetrics().elidedText(self._full, QtCore.Qt.ElideRight, max(0, self.width() - 2))
        QtWidgets.QLabel.setText(self, shown)

    def sizeHint(self):
        h = super().sizeHint()
        return QtCore.QSize(min(h.width(), self.fontMetrics().horizontalAdvance(self._full) + 4), h.height())


class StatusBar(QtWidgets.QWidget):
    dofClicked = Signal()

    def __init__(self, theme="light", parent=None):
        super().__init__(parent)
        self.setObjectName("InCadStatus")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self._theme = theme
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        self._prompt = QtWidgets.QLabel("")
        self._message = _Elided("")
        self._units = QtWidgets.QLabel("")
        self._dof = QtWidgets.QLabel("")
        self._stats = QtWidgets.QLabel("")
        row.addWidget(self._prompt)
        row.addWidget(self._message, 100)  # it takes the room there is, never more (cut with …)
        row.addStretch(1)
        for w in (self._units, self._dof, self._stats):
            row.addWidget(w)
        for w in (self._message, self._units, self._dof, self._stats):
            w.hide()
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.clear_message)
        self._dof.installEventFilter(self)
        self._dof.setCursor(QtCore.Qt.PointingHandCursor)
        self._dof.setToolTip("Show the geometry that is still free")
        self._tint_prompt()

    def _tint_prompt(self):
        """The next-step prompt is a tip: blue, as the tips in the dialogs (errors are red)."""
        self._prompt.setStyleSheet("color: %s;" % themes.PALETTES[self._theme]["accent"])

    def set_prompt(self, text):
        self._prompt.setText(text)

    def show_message(self, level, text, timeout_ms=8000):
        p = themes.PALETTES[self._theme]
        # the user: tips blue, errors red (a warning is something that did not work: red too)
        color = {"error": p["error"], "warning": p["error"]}.get(level, p["accent"])
        self._message.setStyleSheet("color: %s; font-weight: 600;" % color)
        self._message.setText(text)
        self._message.setToolTip(text)
        self._message.show()
        self._timer.start(timeout_ms)

    def clear_message(self):
        self._message.setText("")
        self._message.hide()

    def _show(self, label, text):
        label.setText(text)
        label.setVisible(bool(text))

    def set_units(self, units):
        self._show(self._units, UNIT_TEXT.get(units, ""))

    def set_dof(self, dof):
        self._show(self._dof, "" if dof is None else ("Fully constrained ✓" if dof == 0 else "DOF: %d" % dof))

    def set_stats(self, solids, features):
        text = "" if solids is None else "%s · %s" % (_plural(solids, "solid"), _plural(features, "feature"))
        self._show(self._stats, text)

    def set_theme(self, theme):
        self._theme = theme
        self._tint_prompt()

    def texts(self):
        def t(label):
            return "" if label.isHidden() else label.text()
        return {"prompt": self._prompt.text(), "message": t(self._message), "units": t(self._units),
                "dof": t(self._dof), "stats": t(self._stats)}

    def eventFilter(self, obj, event):
        if obj is self._dof and event.type() == QtCore.QEvent.MouseButtonPress:
            self.dofClicked.emit()
            return True
        return False
