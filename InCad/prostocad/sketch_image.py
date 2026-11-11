"""Images to sketch over: a JPG, PNG or BMP under the drawing, only something to look at. Each is a dict in `images`:
name, data (the file itself, base64, so it travels with the drawing), x y (its middle, mm), w h (its size, mm), turn
(degrees), mirror, alpha (how see-through, %), shown. Scale / Transparency: two points of the image and the real length
between them; the image scales so it is, the first point staying put; the line made Horizontal or Vertical turns the
image about that point. The points are kept as fractions of the image, so they stay on the same spots of the photo."""
import base64
import math
import os

from PySide6 import QtCore, QtGui

from prostocad import loupe, units

IMAGES = (".jpg", ".jpeg", ".png", ".bmp")
SIZE = 100.0  # its longer side when it comes in, mm
P = QtCore.QPointF


class ImageTools:
    def _image_start(self):
        self.images = []
        self.img = None  # Scale / Transparency is open
        self.img_read = {}  # the data: its picture
        self.img_bare = False  # drawing the glass's picture: the points stay out of it
        self.img_layer, self.img_layer_key = None, None  # the images as painted, kept while nothing changes
        self.glass = None

    # ----- what an image is -----
    def _image_picture(self, d):
        pic = self.img_read.get(d["data"])
        if pic is None:
            pic = self.img_read[d["data"]] = QtGui.QImage.fromData(base64.b64decode(d["data"]))
        return pic

    @staticmethod
    def image_world(d, u, v, at=None):
        """The point (u, v) of the image, fractions of its width and height from its lower left corner, in the sketch.
        at: (x, y, w, h, turn) in place of where it is now."""
        x, y, w, h, turn = at or (d["x"], d["y"], d["w"], d["h"], d["turn"])
        lx, ly = (u - 0.5) * w * (-1 if d["mirror"] else 1), (v - 0.5) * h
        c, s = math.cos(math.radians(turn)), math.sin(math.radians(turn))
        return P(x + lx * c - ly * s, y + lx * s + ly * c)

    @staticmethod
    def image_uv(d, q):
        """(u, v) of the image at this point of the sketch, or None off it."""
        dx, dy = q.x() - d["x"], q.y() - d["y"]
        c, s = math.cos(math.radians(d["turn"])), math.sin(math.radians(d["turn"]))
        lx, ly = dx * c + dy * s, -dx * s + dy * c
        u, v = lx * (-1 if d["mirror"] else 1) / d["w"] + 0.5, ly / d["h"] + 0.5
        return (u, v) if -1e-6 <= u <= 1 + 1e-6 and -1e-6 <= v <= 1 + 1e-6 else None

    def image_corners(self):
        return [self.image_world(d, u, v) for d in self.images if d["shown"] for u, v in ((0, 0), (1, 0), (1, 1), (0, 1))]

    # ----- commands -----
    def _image_step(self, st):
        self.steps.append(("state", st, len(self.lines), len(self.arcs)))
        self._drawn_again()
        self.changed.emit()
        self.update()

    def image_add(self, path):
        """The file under the drawing: centred on the origin, its longer side 100 mm, its proportions kept. What is wrong, or None."""
        name = os.path.basename(path)
        if os.path.splitext(path)[1].lower() not in IMAGES:
            return "An image (JPG, PNG, BMP) is imported for reference, not %s" % name
        if not os.path.isfile(path):
            return "%s is not there" % name
        with open(path, "rb") as f:
            raw = f.read()
        pic = QtGui.QImage.fromData(raw)
        if pic.isNull():
            return "%s cannot be read as an image" % name
        k = SIZE / max(pic.width(), pic.height())
        st = self._state()
        self.images.append(dict(name="%s (image)" % os.path.splitext(name)[0], data=base64.b64encode(raw).decode("ascii"), x=0.0, y=0.0,
                                w=pic.width() * k, h=pic.height() * k, turn=0.0, mirror=False, alpha=0, shown=True))
        self._image_step(st)
        self.fit()
        d = self.images[-1]
        self.prompt.emit("%s: an image on the XY plane, %s × %s. Next: Scale / Transparency" % (name, units.short(d["w"], 1).rsplit(" ", 1)[0], units.short(d["h"], 1)))
        return None

    def image_change(self, i, **values):
        """One step of Cmd+Z: Turn, Mirror, Hide, Show, Rename."""
        st = self._state()
        self.images[i].update(values)
        self._image_step(st)

    def image_turn(self, i):
        self.image_change(i, turn=(self.images[i]["turn"] + 90) % 360)

    def image_mirror(self, i):
        self.image_change(i, mirror=not self.images[i]["mirror"])

    def image_delete(self, i):
        st = self._state()
        del self.images[i]
        self._image_step(st)

    def images_show_hide(self):
        """Every image at once: all shown again unless one is showing."""
        st = self._state()
        show = not any(d["shown"] for d in self.images)
        for d in self.images:
            d["shown"] = show
        self._image_step(st)

    # ----- Scale / Transparency -----
    def start_image_scale(self, i):
        self.stop_tool()
        d = self.images[i]
        self.tool = "img_scale"
        self.img = dict(i=i, st=self._state(), start=(d["x"], d["y"], d["w"], d["h"], d["turn"]), alpha=d["alpha"], pts=[], hover=None)
        self.prompt.emit("Scale / Transparency  Click two points of the image whose real distance you know")
        self.update()

    def _img_put(self, at):
        d = self.images[self.img["i"]]
        d["x"], d["y"], d["w"], d["h"], d["turn"] = at

    def img_set(self, values):
        """The window's values on the image, always from where it stood when the window opened."""
        r = self.img
        d = self.images[r["i"]]
        d["alpha"] = int(values.get("transparency") or 0)
        r["values"], r["wrong"] = dict(values), None
        x0, y0, w0, h0, turn0 = r["start"]
        align, length = values.get("align") or "as_is", values.get("length") or 0
        if len(r["pts"]) < 2 or (length <= 0 and align == "as_is"):  # nothing to scale or level by yet: as it was
            self._img_put(r["start"])
            self.update()
            return
        a, b = (self.image_world(d, u, v, r["start"]) for u, v in r["pts"][:2])
        span = math.hypot(b.x() - a.x(), b.y() - a.y())
        if span < 1e-9:
            r["wrong"] = "Click two different points of the image"
            self._img_put(r["start"])
            self.update()
            return
        cx, cy, turn = x0, y0, turn0
        if align != "as_is":  # the line made horizontal or vertical: the image turned about the first point, the nearer way
            dx, dy = b.x() - a.x(), b.y() - a.y()
            wx, wy = (1.0, 0.0) if align == "horizontal" else (0.0, 1.0)
            ang = math.degrees(math.atan2(dx * wy - dy * wx, dx * wx + dy * wy))
            ang = ang - 180 if ang > 90 else ang + 180 if ang < -90 else ang
            c, s = math.cos(math.radians(ang)), math.sin(math.radians(ang))
            ox, oy = cx - a.x(), cy - a.y()
            cx, cy, turn = a.x() + ox * c - oy * s, a.y() + ox * s + oy * c, turn0 + ang
        k = length / span if length > 0 else 1.0
        self._img_put((a.x() + (cx - a.x()) * k, a.y() + (cy - a.y()) * k, w0 * k, h0 * k, turn))
        self.update()

    def img_pending(self):
        """What a scaling still waits for (OK off); none with no point clicked: the transparency alone is kept."""
        r = self.img
        n = len(r["pts"])
        values = r.get("values") or {}
        if r.get("wrong"):
            return r["wrong"]
        if n == 1:
            return "Click the second point"
        if n >= 2 and not values.get("length") and (values.get("align") or "as_is") == "as_is":
            return "Type the real length between the two points, or make the line Horizontal or Vertical"
        return ""

    def img_info(self):
        """The length between the points on the image as it is now."""
        r = self.img
        if not r["pts"]:
            return "To scale it: click two points of the image whose real distance you know"
        if len(r["pts"]) < 2:
            return ""
        return "On the image now: %s" % units.short(self._img_length(), 3)

    def _img_length(self):
        r = self.img
        d = self.images[r["i"]]
        a, b = (self.image_world(d, u, v) for u, v in r["pts"][:2])
        return math.hypot(b.x() - a.x(), b.y() - a.y())

    def img_points(self, last):
        """Undo (the last point) or Clear."""
        r = self.img
        r["pts"] = r["pts"][:-1] if last else []
        if self.glass is not None:
            self.glass.forget()
        self.image_points.emit()
        self.update()

    def _img_press(self, s):
        """A click sets a point; a third starts over."""
        r = self.img
        uv = self.image_uv(self.images[r["i"]], self.to_model(s))
        if uv is None:
            return
        r["pts"] = [uv] if len(r["pts"]) >= 2 else r["pts"] + [uv]
        r["hover"] = None
        if self.glass is not None:
            self.glass.forget()  # the new point shows in the glass too
        self.image_points.emit()
        self.update()

    def _img_track(self, s):
        r = self.img
        uv = self.image_uv(self.images[r["i"]], self.to_model(s))
        if uv != r["hover"]:
            r["hover"] = uv
            self.update()
        self._img_magnify(s, uv is not None)

    def _img_key(self):
        c = self.center
        return (c.x(), c.y(), self.scale, self.width(), self.height(), self.dim_constr, tuple(self.img["pts"]),
                tuple(tuple(sorted((k, x) for k, x in d.items() if k != "data")) for d in self.images), len(self.pts), len(self.lines), len(self.arcs))

    def _img_frame(self):
        """The view as it is drawn now, without the points; the windows over it stay out."""
        ratio = self.devicePixelRatioF()
        frame = QtGui.QImage(round(self.width() * ratio), round(self.height() * ratio), QtGui.QImage.Format_ARGB32_Premultiplied)
        frame.setDevicePixelRatio(ratio)
        self.img_bare = True
        try:
            self.render(frame, QtCore.QPoint(0, 0), renderFlags=self.RenderFlag.DrawWindowBackground)
        finally:
            self.img_bare = False
        return frame

    def _img_magnify(self, s, on):
        """The glass beside the pointer while it is over the image."""
        if not on:
            self.img_glass_off()
            return
        if self.glass is None:
            self.glass = loupe.Loupe(self.window())
        r = self.img
        d = self.images[r["i"]]
        marks = [self.to_screen(self.image_world(d, u, v)) for u, v in r["pts"][:2]]
        self.glass.follow(self, s.x(), s.y(), [(q.x(), q.y()) for q in marks], self._img_frame, self._img_key())

    def img_glass_off(self):
        if self.glass is not None:
            self.glass.hide()

    def img_end(self, keep):
        """OK keeps it, one step of Cmd+Z. Otherwise the image goes back; the transparency chosen stays, it is only how it looks."""
        r, self.img = self.img, None
        if self.glass is not None:
            self.glass.hide()
            self.glass.deleteLater()
            self.glass = None
        if r is None:
            return
        d = self.images[r["i"]]
        moved = (d["x"], d["y"], d["w"], d["h"], d["turn"]) != r["start"]
        if not keep:
            d["x"], d["y"], d["w"], d["h"], d["turn"] = r["start"]
        elif moved or d["alpha"] != r["alpha"]:
            self._image_step(r["st"])
        self.update()

    # ----- painting -----
    def _paint_images(self, g):
        if not any(d["shown"] for d in self.images):
            return
        ratio = self.devicePixelRatioF()
        c = self.center
        key = (c.x(), c.y(), self.scale, self.width(), self.height(), ratio,
               tuple((id(d["data"]), d["x"], d["y"], d["w"], d["h"], d["turn"], d["mirror"], d["alpha"], d["shown"]) for d in self.images))
        if key != self.img_layer_key:  # a pointer's move repaints the view: the images are drawn only when they change
            layer = QtGui.QImage(round(self.width() * ratio), round(self.height() * ratio), QtGui.QImage.Format_ARGB32_Premultiplied)
            layer.setDevicePixelRatio(ratio)
            layer.fill(QtCore.Qt.transparent)
            p = QtGui.QPainter(layer)
            p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
            for d in self.images:
                if d["shown"]:
                    pic = self._image_picture(d)
                    p.save()
                    p.setOpacity(1 - d["alpha"] / 100.0)
                    p.translate(self.to_screen(P(d["x"], d["y"])))
                    p.rotate(-d["turn"])
                    p.scale(self.scale * d["w"] / pic.width() * (-1 if d["mirror"] else 1), self.scale * d["h"] / pic.height())
                    p.drawImage(P(-pic.width() / 2, -pic.height() / 2), pic)
                    p.restore()
            p.end()
            self.img_layer, self.img_layer_key = layer, key
        g.drawImage(0, 0, self.img_layer)

    def _paint_img_marks(self, g):
        """The points, small and bright; the line between them, with its length in grey; dashed while it follows the pointer."""
        r = self.img
        if r is None or self.img_bare:
            return
        d = self.images[r["i"]]
        pts = list(r["pts"][:2])
        ends = pts + [r["hover"]] if len(pts) == 1 and r["hover"] is not None else pts
        g.save()
        if len(ends) == 2:
            a, b = (self.image_world(d, u, v) for u, v in ends)
            sa, sb = self.to_screen(a), self.to_screen(b)
            pen = QtGui.QPen(QtGui.QColor(self.p["accent"]), 1.5)
            if len(pts) < 2:
                pen.setStyle(QtCore.Qt.DashLine)
            g.setPen(pen)
            g.drawLine(sa, sb)
            f = QtGui.QFont(g.font())
            f.setPixelSize(13)
            g.setFont(f)
            g.setPen(QtGui.QColor(self.p["text_dim"]))
            g.drawText((sa + sb) / 2, units.short(math.hypot(b.x() - a.x(), b.y() - a.y())))
        dots = pts + ([r["hover"]] if r["hover"] is not None else [])
        g.setPen(QtCore.Qt.NoPen)
        for size, ink in ((7, QtGui.QColor(20, 20, 26)), (4, QtGui.QColor(255, 219, 38))):  # a dark rim, a bright core
            g.setBrush(ink)
            for u, v in dots:
                g.drawEllipse(self.to_screen(self.image_world(d, u, v)), size / 2, size / 2)
        g.restore()
