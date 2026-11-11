"""The right-click ring: up to eight commands round the pointer, and More below it for what is left, opening when the pointer
comes to it. A right click opens it for hover and click; a right press, a flick towards a command and a release runs that
command at once."""
import math

from PySide6 import QtCore, QtGui, QtWidgets

from prostocad import log, theme

DIRECTIONS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
RADIUS = 46
DEAD_ZONE = 14
GAP = 4
PAD = 6
SHORT = {"Finish Sketch": "Finish", "Rectangle": "Rect", "Dimension": "Dim", "3-Point Arc": "Arc"}  # the ring is small, the words too


def direction_for(dx, dy, dead_zone=DEAD_ZONE):
    """A screen vector (dy down) as 'N'…'NW'; None inside the dead zone."""
    if math.hypot(dx, dy) < dead_zone:
        return None
    return DIRECTIONS[int((math.degrees(math.atan2(dx, -dy)) % 360 + 22.5) // 45) % 8]


def ring_rect(direction, w, h, radius=RADIUS):
    """A w × h button round the centre: it grows away from the centre, N and S clear of the diagonal ones."""
    if direction in ("N", "S"):
        dist = radius * math.sqrt(0.5) + h + GAP
        return QtCore.QRect(int(-w / 2), int(-dist - h if direction == "N" else dist), w, h)
    a = math.radians(DIRECTIONS.index(direction) * 45)
    x, y = radius * math.sin(a), -radius * math.cos(a)
    left = x if math.sin(a) > 0 else x - w
    top = y - h if math.cos(a) > 0.3 else y if math.cos(a) < -0.3 else y - h / 2
    return QtCore.QRect(int(left), int(top), w, h)


class Ring(QtWidgets.QWidget):
    def __init__(self, slots, items, parent=None, tap=None):
        """slots: {direction: (label, icon, action)}; items: [(label, action)] for the list below the ring.
        tap: the ring stays unseen, only the flick works (the command it points to shows); a plain right click calls tap."""
        super().__init__(parent, QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.actions_at, self.buttons, self.list = {}, {}, []
        self.flicking, self.hot, self.anchor, self.to = False, None, None, None
        self.gone = lambda: None  # who is told when it closes
        self.tap = tap
        p = theme.cur()
        self.ink = p["accent"]
        self.setStyleSheet("QToolButton {{ background: {0}; color: {1}; border: 1px solid {2}; border-radius: 6px; padding: 2px 10px; }}"
                           " QToolButton:hover, QToolButton[hot=\"true\"] {{ background: {3}; color: #ffffff; }}"
                           .format(p["panel"], p["text"], p["border"], p["accent"]))
        placed = []
        for direction, (label, icon, action) in slots.items():
            b = self._button(SHORT.get(label, label), action, icon)
            placed.append((b, ring_rect(direction, b.width(), b.height())))
            self.buttons[direction] = b
            self.actions_at[direction] = action
            b.setVisible(tap is None)
        for label, action in items:
            b = self._button(label, action)
            b.setMinimumWidth(120)
            b.adjustSize()
            b.hide()
            self.list.append(b)
        self.more = None
        if self.list:
            self.more = self._button("More  ▾", lambda: None)
            self.more.clicked.disconnect()
            self.more.clicked.connect(self._open_more)
            self.more.setMinimumWidth(120)
            self.more.adjustSize()
            self.more.installEventFilter(self)
            self.more.setVisible(tap is None)
        boxes = [r for _b, r in placed] + [QtCore.QRect(-DEAD_ZONE, -DEAD_ZONE, 2 * DEAD_ZONE, 2 * DEAD_ZONE)]
        wide = max([b.width() for b in self.list + ([self.more] if self.more else [])], default=0)
        left = min(min(r.left() for r in boxes), -wide // 2)
        right = max(max(r.right() for r in boxes), wide // 2)
        top = min(r.top() for r in boxes)
        bottom = max(r.bottom() for r in boxes)
        self.center = QtCore.QPoint(-left + PAD, -top + PAD)
        for b, r in placed:
            b.move(r.topLeft() + self.center)
        y = bottom + 6
        if self.more is not None:
            self.more.move(self.center.x() - self.more.width() // 2, self.center.y() + y)
            y += self.more.height() + 2
        for b in self.list:
            b.move(self.center.x() - b.width() // 2, self.center.y() + y)
            y += b.height() + 2
        self.resize(right - left + 2 * PAD, (y if self.list else bottom) - top + 2 * PAD)

    def _button(self, label, action, icon=None):
        b = QtWidgets.QToolButton(self)
        if icon:
            b.setIcon(theme.icon(icon))
            b.setIconSize(QtCore.QSize(16, 16))
        b.setText(label)
        b.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        b.setFocusPolicy(QtCore.Qt.NoFocus)
        b.clicked.connect(lambda _=False, a=action: self.run(a))
        b.adjustSize()
        return b

    def _open_more(self):
        for b in self.list:
            b.show()

    def eventFilter(self, obj, e):
        if obj is self.more and e.type() == QtCore.QEvent.Enter:
            self._open_more()
        return False

    def run(self, action):
        log.say("ring: a command runs")
        self.close()
        action()

    def show_at(self, at, top=None, left=None):
        """Round the point pressed; near a screen's edge it moves to stay whole, the flick still counts from that point."""
        self.anchor = QtCore.QPoint(at)
        self.flicking = True
        top_left = at - self.center
        screen = QtGui.QGuiApplication.screenAt(at) or QtGui.QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            if top is not None:  # under the ribbon, never over it
                area.setTop(max(area.top(), top))
            if left is not None:  # nor over the tree
                area.setLeft(max(area.left(), left))
            top_left = QtCore.QPoint(min(max(top_left.x(), area.left()), max(area.left(), area.right() - self.width() + 1)),
                                     min(max(top_left.y(), area.top()), max(area.top(), area.bottom() - self.height() + 1)))
        self.move(top_left)
        self.show()
        self.raise_()

    def flick_move(self, at):
        if not self.flicking:
            return
        d = at - self.anchor
        self._set_hot(direction_for(d.x(), d.y()))
        self.to = self.mapFromGlobal(at)
        self.update()

    def flick_end(self, at):
        """The right button let go: out of the dead zone it runs what it points to; inside, it was a click: the ring stays."""
        if not self.flicking:
            return
        self.flicking = False
        d = at - self.anchor
        direction = direction_for(d.x(), d.y())
        log.say("ring: the right button let go, moved", d.x(), d.y(), "to", direction)
        if direction is None and self.tap is not None:
            self.close()
            self.tap()
        elif direction is None:
            self.to = None
            self.update()
        elif direction in self.actions_at:
            self.run(self.actions_at[direction])
        else:
            self.close()

    def _set_hot(self, direction):
        self.hot = direction if direction in self.buttons else None
        for d, b in self.buttons.items():
            if self.tap is not None:
                b.setVisible(d == self.hot)
            b.setProperty("hot", d == self.hot)
            b.style().unpolish(b)
            b.style().polish(b)

    def paintEvent(self, _e):
        g = QtGui.QPainter(self)
        g.setRenderHint(QtGui.QPainter.Antialiasing)
        g.setPen(QtGui.QPen(QtGui.QColor(self.ink), 2))
        fill = QtGui.QColor(self.ink)
        fill.setAlpha(40)
        g.setBrush(fill)
        origin = self.mapFromGlobal(self.anchor) if self.anchor is not None else self.center
        g.drawEllipse(origin, DEAD_ZONE, DEAD_ZONE)
        if self.to is not None:
            g.drawLine(origin, self.to)

    def closeEvent(self, e):
        self.gone()
        super().closeEvent(e)

    def mouseMoveEvent(self, e):
        self.flick_move(e.globalPosition().toPoint())

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.RightButton:
            self.flick_end(e.globalPosition().toPoint())

    def mousePressEvent(self, e):
        log.say("ring: a click beside its buttons at", e.position().x(), e.position().y())
        self.close()  # a click on the empty place round the ring

    def keyPressEvent(self, e):
        if e.key() == QtCore.Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)
