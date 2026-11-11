"""A magnifying glass beside the pointer, for putting a point on an image so it lands on the spot: a round window that
shows the view four times larger, the pixels as they are, with a thin cross at its middle. The view is drawn once into
a picture and the glass crops that picture as the pointer moves; it is drawn again when the view changes or a point is
set (forget). The points and the line between them are left out of that picture and drawn here small, not magnified."""
import time

from PySide6 import QtCore, QtGui, QtWidgets

SIZE = 140  # px across
ZOOM = 4.0
OFFSET = 28  # px from the pointer, up and to the right
INTERVAL = 0.03


class Loupe(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowTransparentForInput)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        self.setFixedSize(SIZE, SIZE)
        self.image = None
        self.last = 0.0
        self.frame, self.frame_key = None, None  # the view's picture, and the state of the view it was taken in

    def forget(self):
        self.frame = None

    def _spot(self, view, x, y):
        return view.mapToGlobal(QtCore.QPoint(int(x) + OFFSET, int(y) - OFFSET - SIZE))

    def follow(self, view, x, y, marks, take, key):
        """What is around (x, y) of the view, magnified. take(): the view's picture without the marks; key: changes when
        that picture is out of date; marks: points in the view, drawn small."""
        now = time.monotonic()
        if now - self.last < INTERVAL and self.isVisible():
            self.move(self._spot(view, x, y))
            return
        self.last = now
        if self.frame is None or key != self.frame_key:
            self.frame, self.frame_key = take(), key
        frame = self.frame
        ratio = frame.width() / max(1, view.width())
        side = SIZE / ZOOM * ratio  # device px taken
        src = QtCore.QRectF(x * ratio - side / 2, y * ratio - side / 2, side, side)
        img = QtGui.QImage(SIZE * 2, SIZE * 2, QtGui.QImage.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(2.0)
        img.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(img)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        clip = QtGui.QPainterPath()
        clip.addEllipse(QtCore.QRectF(1, 1, SIZE - 2, SIZE - 2))
        p.setClipPath(clip)
        p.fillRect(QtCore.QRectF(0, 0, SIZE, SIZE), QtGui.QColor(30, 33, 38))
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, False)  # the pixels as they are, larger
        p.drawImage(QtCore.QRectF(0, 0, SIZE, SIZE), frame, src)
        pts = [QtCore.QPointF(SIZE / 2.0 + (mx - x) * ZOOM, SIZE / 2.0 + (my - y) * ZOOM) for mx, my in marks]
        if len(pts) > 1:
            p.setPen(QtGui.QPen(QtGui.QColor(90, 160, 255), 1.5))
            p.drawLine(pts[0], pts[1])
        for q in pts:  # a small cross, not a dot: a dark one under a bright one
            for pen in (QtGui.QPen(QtGui.QColor(20, 20, 26), 3), QtGui.QPen(QtGui.QColor(255, 220, 40), 1.2)):
                p.setPen(pen)
                p.drawLine(QtCore.QPointF(q.x() - 4, q.y()), QtCore.QPointF(q.x() + 4, q.y()))
                p.drawLine(QtCore.QPointF(q.x(), q.y() - 4), QtCore.QPointF(q.x(), q.y() + 4))
        p.setClipping(False)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 210, 40), 1))
        c = SIZE / 2.0
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):  # a cross with a gap at its middle: the spot stays seen
            p.drawLine(QtCore.QPointF(c + 3 * dx, c + 3 * dy), QtCore.QPointF(c + 10 * dx, c + 10 * dy))
        p.setPen(QtGui.QPen(QtGui.QColor(200, 205, 215), 2))
        p.drawEllipse(QtCore.QRectF(1, 1, SIZE - 2, SIZE - 2))
        p.end()
        self.image = img
        self.move(self._spot(view, x, y))
        if not self.isVisible():
            self.show()
        self.update()

    def paintEvent(self, _event):
        if self.image is not None:
            p = QtGui.QPainter(self)
            p.drawImage(0, 0, self.image)
            p.end()
