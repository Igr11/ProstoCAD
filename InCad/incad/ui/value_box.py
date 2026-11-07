"""Floating box to type a dimension's value or formula (Inventor's edit-dimension box). Enter applies, Esc cancels.
While it is open, clicking a dimension or a parameter inserts that name at the cursor."""
from incad.qt import QtCore, QtWidgets, Signal
from incad.ui import theme as themes


class ValueBox(QtWidgets.QFrame):
    applied = Signal(str, str)
    closed = Signal()

    def __init__(self, name, text, theme="light", parent=None, apply=None):
        # a panel inside the main window (as the feature dialogs), not a window of its own: macOS gives the
        # keyboard back to a separate window unreliably once the 3D view was clicked (the Dimension tool's
        # placing click), so typing, Enter and Esc went to the view
        super().__init__(parent)
        self.setObjectName("InCadValueBox")
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.name = name
        self._apply_fn = apply  # apply(name, text) -> (ok, error); the box stays open and red on error
        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(8, 6, 8, 6)
        row = QtWidgets.QHBoxLayout()
        col.addLayout(row)
        row.addWidget(QtWidgets.QLabel(name + " ="))
        self.edit = QtWidgets.QLineEdit(text)
        self.edit.setMinimumWidth(170)
        self.edit.selectAll()
        self._typed = False  # until the user types, taking the keyboard selects the value (typing replaces it)
        self.closed_by_user = False  # Enter/Esc/✓/✕ (not InCad closing it because the user moved on)
        self.edit.textEdited.connect(lambda _t: setattr(self, "_typed", True))
        self.edit.returnPressed.connect(self._apply)
        self.edit.installEventFilter(self)
        row.addWidget(self.edit)
        # ✓ applies (= Enter), ✕ cancels (= Esc): clickable as well
        self.apply_button = QtWidgets.QToolButton()
        self.apply_button.setText("✓")
        self.apply_button.setToolTip("Apply (Enter)")
        self.apply_button.setObjectName("InCadValueApply")
        self.apply_button.clicked.connect(self._apply)
        self.cancel_button = QtWidgets.QToolButton()
        self.cancel_button.setText("✕")
        self.cancel_button.setToolTip("Cancel (Esc)")
        self.cancel_button.setObjectName("InCadValueCancel")
        self.cancel_button.clicked.connect(self._cancel)
        for b in (self.apply_button, self.cancel_button):
            b.setFocusPolicy(QtCore.Qt.NoFocus)  # a click must not take the keyboard from the field
            b.setCursor(QtCore.Qt.PointingHandCursor)
            row.addWidget(b)
        self._kinds = QtWidgets.QHBoxLayout()  # set_kinds(): how a distance between two arcs measures
        col.addLayout(self._kinds)
        self.kind_buttons = {}
        self._error = QtWidgets.QLabel("")
        self._error.setObjectName("InCadValueError")
        self._error.hide()
        col.addWidget(self._error)
        p = themes.PALETTES[theme]
        self.setStyleSheet(
            "QFrame#InCadValueBox {{ background: {panel}; border: 1px solid {accent}; border-radius: 8px; }}"
            " QLabel {{ color: {text}; }} QLabel#InCadValueError {{ color: {error}; }} QToolButton {{ color: {text}; background: {panel_alt}; border: 1px solid {border}; border-radius: 4px;"
            " min-width: 22px; min-height: 20px; font-size: 13px; padding: 0 6px; }} QToolButton:hover {{ background: {accent}; color: {selection_text}; }}"
            " QToolButton:checked {{ background: {accent}; color: {selection_text}; }}"
            " QLineEdit {{ background: {panel_alt}; color: {text}; border: 1px solid {border}; border-radius: 4px;"
            " padding: 2px 4px; }}".format(**p))

    def set_kinds(self, kinds, current, on_pick):
        """A row of buttons (key, label) choosing what the dimension measures; on_pick(key) for another one."""
        for key, label in kinds:
            b = QtWidgets.QToolButton()
            b.setText(label)
            b.setCheckable(True)
            b.setChecked(key == current)
            b.setObjectName("InCadValueKind")
            b.setFocusPolicy(QtCore.Qt.NoFocus)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key, btn=b: (btn.setChecked(k == current), k != current and on_pick(k)))
            self._kinds.addWidget(b)
            self.kind_buttons[key] = b
        self._kinds.addStretch(1)
        self.adjustSize()

    def show_near(self, global_pos):
        self.ensurePolished()  # its style's sizes first: a box sized before them grew past the edge once shown
        self.adjustSize()
        parent = self.parentWidget()
        self.move(parent.mapFromGlobal(global_pos + QtCore.QPoint(16, 16)) if parent is not None else global_pos)
        self._keep_inside()
        self.show()
        self.take_keyboard()
        QtCore.QTimer.singleShot(0, self._keep_inside)  # once more with the size it was shown at (a scaled screen)

    def _keep_inside(self):
        """Inside the view and on the screen's free area: on Windows a box at the view's bottom edge went under the
        taskbar (the window reached below it) and could not be got out of the corner (the user)."""
        parent = self.parentWidget()
        if parent is None:
            return
        try:
            self.adjustSize()
            area = QtCore.QRect(0, 0, parent.width(), parent.height())
            screen = parent.screen()
            if screen is not None:
                free = screen.availableGeometry()
                area = area.intersected(QtCore.QRect(parent.mapFromGlobal(free.topLeft()), free.size()))
            if area.width() <= 0 or area.height() <= 0:
                return
            pos = self.pos()
            pos.setX(max(area.left(), min(pos.x(), area.right() + 1 - self.width())))
            pos.setY(max(area.top(), min(pos.y(), area.bottom() + 1 - self.height())))
            self.move(pos)
        except RuntimeError:  # closed meanwhile
            pass

    def take_keyboard(self):
        try:
            self.raise_()
            self.window().activateWindow()
            self.edit.setFocus(QtCore.Qt.OtherFocusReason)
            if not self._typed:
                self.edit.selectAll()
        except RuntimeError:
            pass

    def insert(self, name):
        if name == self.name:  # the dimension being edited itself: a formula can't refer to it
            self.take_keyboard()
            return
        whole = self.edit.hasSelectedText() and self.edit.selectedText() == self.edit.text()
        if self._typed and not whole:
            self.edit.insert(name)  # at the cursor: 'd0+' then a click -> 'd0+d3'
        else:
            self.edit.setText(name)  # the untouched value is replaced (its selection is lost to the view's click)
        self._typed = True
        self.take_keyboard()  # a click in the view or the table took the keyboard

    def _cancel(self):
        self.closed_by_user = True
        self.close()

    def _apply(self):
        self.closed_by_user = True
        text = self.edit.text().strip()
        if not text:
            self.close()
            return
        if self._apply_fn is not None:
            ok, error = self._apply_fn(self.name, text)
            if not ok:
                self.set_error(error or "This value cannot be used")
                return
            self.close()
            return
        self.close()
        self.applied.emit(self.name, text)

    def set_error(self, text):
        self._error.setText(text)
        self._error.setVisible(bool(text))
        self.edit.setStyleSheet("border: 1px solid #d0021b;" if text else "")
        self.adjustSize()
        self.edit.setFocus()

    def error_text(self):
        return self._error.text()

    def eventFilter(self, obj, event):
        if obj is self.edit and event.type() == QtCore.QEvent.KeyPress and event.key() == QtCore.Qt.Key_Escape:
            self._cancel()
            return True
        return False

    def closeEvent(self, event):
        # the keyboard goes back to the 3D view the box sits in: left to Qt it moves to the next widget in the
        # window, which can be another document's tab — that tab's own workbench (SaveWBbyTab) then took over
        # and FreeCAD crashed in the Dimension tool still running in this sketch
        focus = QtWidgets.QApplication.focusWidget()
        view = self.parentWidget()
        if focus is not None and (focus is self or self.isAncestorOf(focus)) and view is not None:
            target = view.parentWidget() if isinstance(view.parentWidget(), QtWidgets.QGraphicsView) else view
            target.setFocus(QtCore.Qt.OtherFocusReason)
            if QtWidgets.QApplication.focusWidget() is focus:
                focus.clearFocus()
        self.closed.emit()
        super().closeEvent(event)
