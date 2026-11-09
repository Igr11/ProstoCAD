"""Inventor-style marking menu: up to 8 commands in a ring around the cursor plus a short list below it.
Right-click opens it for hover-and-click; a right-drag (flick) picks the slot in the drag direction on release."""
import math

from incad.qt import QtCore, QtGui, QtWidgets, Signal
from incad.ui import theme as themes
from incad.ui.marking_layout import DIRECTIONS

RADIUS = 70
DEAD_ZONE = 18
GAP = 6


def direction_for(dx, dy, dead_zone=DEAD_ZONE):
    """Screen vector (dy positive downward) -> 'N'…'NW', or None inside the dead zone."""
    if math.hypot(dx, dy) < dead_zone:
        return None
    angle = math.degrees(math.atan2(dx, -dy)) % 360
    return DIRECTIONS[int((angle + 22.5) // 45) % 8]


def ring_rect(direction, w, h, radius=RADIUS):
    """Rect of a w x h slot button relative to the centre: each button grows away from the centre (Inventor
    style) and N/S sit clear of the diagonal buttons, so long labels never overlap."""
    if direction in ("N", "S"):
        dist = radius * math.sqrt(0.5) + h + GAP
        return QtCore.QRect(int(-w / 2), int(-dist - h if direction == "N" else dist), w, h)
    a = math.radians(DIRECTIONS.index(direction) * 45)
    x, y = radius * math.sin(a), -radius * math.cos(a)
    left = x if math.sin(a) > 0 else x - w
    top = y - h if math.cos(a) > 0.3 else y if math.cos(a) < -0.3 else y - h / 2
    return QtCore.QRect(int(left), int(top), w, h)


class MarkingMenu(QtWidgets.QWidget):
    triggered = Signal(str)

    def __init__(self, registry, slots, items, theme="light", parent=None, extras=()):
        """extras: (label, icon, callable) entries after the list (a gap above them), each running its own action."""
        super().__init__(parent, QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
        self.setObjectName("InCadMarkingMenu")
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self._registry, self._theme = registry, theme
        self._buttons, self._slot_ids, self._list = {}, {}, []
        self._flick, self._flick_to, self._hot = False, None, None
        p = themes.PALETTES[theme]
        self.setStyleSheet(
            "QToolButton {{ background: {panel}; color: {text}; border: 1px solid {border}; border-radius: 8px;"
            " padding: 4px 10px; }} QToolButton:hover, QToolButton[hot=\"true\"] {{ background: {accent};"
            " color: {selection_text}; }}".format(**p))
        placed = []
        for direction, cmd in slots:
            b = self._make_button(cmd)
            placed.append((b, ring_rect(direction, b.width(), b.height())))
            self._buttons[direction] = b
            self._slot_ids[direction] = cmd.id
        for cmd in items:
            b = self._make_button(cmd)
            b.setMinimumWidth(170)
            b.adjustSize()
            self._list.append(b)
        self._extras = {}
        for label, icon, fn in extras:
            b = QtWidgets.QToolButton(self)
            b.setIcon(themes.icon(icon, self._theme))
            b.setIconSize(QtCore.QSize(20, 20))
            b.setText(label)
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            b.clicked.connect(lambda _=False, lb=label, f=fn: self._choose_extra(lb, f))
            b.setMinimumWidth(170)
            b.adjustSize()
            self._list.append(b)
            self._extras[label] = b
        # lay everything out around the centre (0, 0), then shift it into the widget
        boxes = [r for _, r in placed] + [QtCore.QRect(-DEAD_ZONE, -DEAD_ZONE, 2 * DEAD_ZONE, 2 * DEAD_ZONE)]
        left = min(r.left() for r in boxes)
        right = max(r.right() for r in boxes)
        top = min(r.top() for r in boxes)
        bottom = max(r.bottom() for r in boxes)
        list_width = max([b.width() for b in self._list], default=0)
        left, right = min(left, -list_width // 2), max(right, list_width // 2)
        pad = 12
        self._center = QtCore.QPoint(-left + pad, -top + pad)
        for b, r in placed:
            b.move(r.topLeft() + self._center)
        y = bottom + 16
        first_extra = next(iter(self._extras.values()), None)
        for b in self._list:
            if b is first_extra and len(self._list) > len(self._extras):
                y += 8  # a gap between the menu's own list and the entries for the picked element
            b.move(self._center.x() - b.width() // 2, self._center.y() + y)
            y += b.height() + 2
        self.resize(right - left + 2 * pad, (y if self._list else bottom) - top + 2 * pad)

    def _make_button(self, cmd):
        b = QtWidgets.QToolButton(self)
        b.setIcon(themes.icon(cmd.icon, self._theme))
        b.setIconSize(QtCore.QSize(20, 20))
        b.setText(cmd.label)
        b.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        b.clicked.connect(lambda _=False, i=cmd.id: self.choose(i))
        b.adjustSize()
        return b

    # ----- public -----
    def show_at(self, global_pos, left=None):
        """Open the ring around the point pressed; near an edge of the screen the menu is shifted to stay whole,
        and the gesture is still measured from the point pressed. left: the tree's right edge (global x), the
        menu never covers the tree (a right-click on a tree row opens it beside the tree, at the row's height)."""
        self._anchor = QtCore.QPoint(global_pos)
        top_left = global_pos - self._center
        screen = QtGui.QGuiApplication.screenAt(global_pos) or QtGui.QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            if left is not None:
                area.setLeft(max(area.left(), left))
            x = min(max(top_left.x(), area.left()), max(area.left(), area.right() - self.width() + 1))
            y = min(max(top_left.y(), area.top()), max(area.top(), area.bottom() - self.height() + 1))
            top_left = QtCore.QPoint(x, y)
        self.move(top_left)
        self.show()
        self.raise_()

    def center_global(self):
        """Where the gesture starts: the point pressed (the ring's centre unless the menu was shifted)."""
        return QtCore.QPoint(self._anchor) if getattr(self, "_anchor", None) is not None \
            else self.mapToGlobal(self._center)

    def button(self, direction):
        return self._buttons.get(direction)

    def list_labels(self):
        return [b.text() for b in self._list]

    def hot_direction(self):
        return self._hot

    def extra_button(self, label):
        return self._extras.get(label)

    def _choose_extra(self, label, fn):
        self.close()
        self.triggered.emit("extra:" + label)
        fn()

    def choose(self, cmd_id):
        self.close()
        self.triggered.emit(cmd_id)
        self._registry.run(cmd_id)

    def start_flick(self):
        self._flick = True

    def flick_move(self, global_pos):
        d = global_pos - self.center_global()
        self._set_hot(direction_for(d.x(), d.y()))
        self._flick_to = self.mapFromGlobal(global_pos)
        self.update()

    def flick_end(self, global_pos):
        d = global_pos - self.center_global()
        cmd_id = self._slot_ids.get(direction_for(d.x(), d.y()))
        if cmd_id:
            self.choose(cmd_id)
        else:
            self.close()

    # ----- internals -----
    def _set_hot(self, direction):
        self._hot = direction if direction in self._buttons else None
        for d, b in self._buttons.items():
            b.setProperty("hot", d == self._hot)
            b.style().unpolish(b)
            b.style().polish(b)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        pal = themes.PALETTES[self._theme]
        pen = QtGui.QPen(QtGui.QColor(pal["accent"]))
        pen.setWidth(2)
        painter.setPen(pen)
        fill = QtGui.QColor(pal["accent"])
        fill.setAlpha(40)
        painter.setBrush(fill)
        origin = self.mapFromGlobal(self.center_global())
        painter.drawEllipse(origin, DEAD_ZONE, DEAD_ZONE)
        if self._flick_to is not None:
            painter.drawLine(origin, self._flick_to)
        painter.end()

    def mouseMoveEvent(self, event):
        if self._flick:
            self.flick_move(event.globalPosition().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._flick and event.button() == QtCore.Qt.RightButton:
            self.flick_end(event.globalPosition().toPoint())
            return
        super().mouseReleaseEvent(event)

    def mousePressEvent(self, event):
        self.close()  # a click on the empty area around the ring

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
