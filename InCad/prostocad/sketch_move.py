"""Copy by a start point, Paste onto magnets, Rotate round a pivot. Copy asks for its start point (Esc: the middle); the
copy hangs on the pointer by it and goes on being placed until Esc; Space turns it. Rotate takes what is chosen, a pivot,
then the angle: magnets every 15°, typed, or anywhere. It always makes a turned copy; the original stays with its ties, and
is deleted by hand if it is not wanted (the user)."""
import math

from PySide6 import QtCore, QtGui

P = QtCore.QPointF
TAU = 2 * math.pi
SNAP_PX = 8.0


def _len(p):
    return math.hypot(p.x(), p.y())


def _ang(p):
    return math.atan2(p.y(), p.x())


def _turned(p, c, a):
    d = p - c
    co, si = math.cos(a), math.sin(a)
    return c + P(d.x() * co - d.y() * si, d.x() * si + d.y() * co)


class MoveTools:
    """Mixed into SketchView."""

    def copy_chosen(self, cut=False):
        """Cmd+C, Cmd+X: what is chosen goes to the clipboard, then its start point is asked for."""
        had = self.clip
        self.clip = None
        self._copy()
        if self.clip is None:
            self.clip = had
            return
        self.clip["base"] = P(self.clip["middle"])
        self.basing = dict(cut=cut)
        self.prompt.emit("Copy  Click the start point  ·  Esc: from its middle")
        self._refresh()

    def _base_done(self, at=None):
        cut = self.basing["cut"]
        self.basing = None
        self.hint = None
        if at is not None:
            self.clip["base"] = P(at)
        if cut:
            self._delete()
        self.prompt.emit("Copied  ·  Cmd+V: place it")
        self._refresh()

    def end_copying(self):
        """Another command: a start point not given is the middle, a copy on the pointer is given up."""
        if self.basing is not None:
            self._base_done()
        if self.pasting is not None:
            self._paste_cancel()

    def paste_at(self):
        """Where a copy would land: a lit magnet, or the pointer."""
        return self.hint["at"] if self.hint is not None else self.mouse

    # ----- Rotate -----
    def start_rotate(self):
        """What is chosen, or the shape clicked next; then the pivot, anywhere; then the angle."""
        self.rotating = dict(chosen=None, had=self.clip, clip=None, pivot=None, angle=0.0, sweep=0.0, resume=False)
        if self.has_chosen() or self._loose_chosen():
            self._rot_take()
        self._rot_prompt()
        self._refresh()
        return True

    def _rot_take(self):
        r = self.rotating
        for kind, chosen in (("line", self.sel_lines), ("arc", self.sel_arcs), ("ell", self.sel_ells), ("spl", self.sel_spls)):
            chosen -= {i for i in chosen if self.is_projected((kind, i))}  # the part's: not turned (as the old program)
        self.sel_pts -= {k for t in self.ties if t["kind"] == "sk_point" and self._is_ext(t.get("ref")) for k in t["pts"]}
        r["chosen"] = (set(self.sel_lines), set(self.sel_arcs), set(self.sel_ells), set(self.sel_spls), set(self.sel_pts))
        self._copy()
        r["clip"] = self.clip

    def _connected(self, item):
        """All that hangs together with this shape by shared points: one click takes a whole figure."""
        shapes = self._all_shapes()
        points = set(self._shape_points(item))
        taken = {item}
        grown = True
        while grown:
            grown = False
            for q in shapes:
                if q not in taken and points & set(self._shape_points(q)):
                    taken.add(q)
                    points |= set(self._shape_points(q))
                    grown = True
        return taken

    def paste_turn(self):
        """Space while pasting: the copy stands at its end point, or at the one just placed, and turns round it."""
        c = self.clip
        last = self.last_place
        if last is not None and self.mouse is not None and _len(self.mouse - last) * self.scale <= 12:
            self.undo()
            self.redo_steps.pop()
            at = last
        else:
            at = P(self.paste_at())
        self.last_place = None
        step = at - c.get("base", c["middle"])
        moved = dict(c, pts=[p + step for p in c["pts"]], middle=c["middle"] + step)
        self.rotating = dict(chosen=None, had=c, clip=moved, pivot=at, angle=0.0, sweep=0.0, resume=True)
        self.clip = moved
        self.hint = None
        self.prompt.emit("Paste  Click the angle, or type it  Enter  ·  Esc: straight again")
        self._refresh()

    def _rot_prompt(self):
        r = self.rotating
        self.prompt.emit("Rotate  " + ("Click the shape to rotate  ·  Esc gives it up" if r["clip"] is None else
                                       "Click the pivot point, anywhere  ·  Esc gives it up" if r["pivot"] is None else
                                       "Click the angle, or type it  Enter  ·  The copy is added turned  ·  Esc gives it up"))

    def rot_cancel(self):
        r, self.rotating = self.rotating, None
        self.clip = r["had"]
        self.box_sw.reset()
        self.box_sw.hide()
        self.hint = None
        if r["resume"]:
            self.prompt.emit("Paste  Click where the copy goes  ·  Space: turn it  ·  Esc ends")
        else:
            self.stopped.emit()
            self.prompt.emit("Pick a sketch tool  ·  pull a line or a point to move it  ·  right-click: Finish Sketch")
        self._refresh()

    def _rot_start_ray(self):
        """The angle counts from the line from the pivot to the middle of what turns."""
        r = self.rotating
        d = r["clip"]["middle"] - r["pivot"]
        return _ang(d) if _len(d) > 1e-6 else 0.0

    def _rot_track(self):
        """The angle by the pointer, followed round; every 15° a magnet, anywhere between."""
        r = self.rotating
        if r["pivot"] is None or self.mouse is None:
            return
        typed = self.box_sw.typed
        if typed is not None:
            r["angle"] = math.radians(typed)
            return
        raw = _ang(self.mouse - r["pivot"]) - self._rot_start_ray()
        r["sweep"] = raw + round((r["sweep"] - raw) / TAU) * TAU
        angle = r["sweep"]
        reach = _len(self.mouse - r["pivot"])
        mark = round(math.degrees(angle) / 15) * 15
        off = math.radians(mark) - angle
        if abs(off) * reach * self.scale <= SNAP_PX:
            angle += off
            self.hint = dict(kind="sweep", label="%d°" % mark, at=_turned(self.mouse, r["pivot"], off), ref=None, guides=[])
        else:
            self.hint = None
        r["angle"] = angle

    def rot_click(self, s=None):
        r = self.rotating
        if r["clip"] is None:
            what = self._pick(s, ("line", "arc", "ell", "spl")) if s is not None else None
            if what is not None:
                self._clear_choice()
                for kind, i in self._connected(what):
                    {"line": self.sel_lines, "arc": self.sel_arcs, "ell": self.sel_ells, "spl": self.sel_spls}[kind].add(i)
                self._rot_take()
                self._rot_prompt()
                self._refresh()
            return
        if r["pivot"] is None:
            r["pivot"] = P(self.hint["at"] if self.hint is not None else self.mouse)
            self.hint = None
            self.setFocus()
            self._rot_prompt()
            self._refresh()
            return
        self.box_sw.apply()
        self._rot_track()
        self._rot_finish()

    def _rot_finish(self):
        """The turned copy in; the original goes unless it is kept. One Cmd+Z takes it all back."""
        r = self.rotating
        a = r["angle"]
        if abs(a) < 1e-9 and not r["resume"]:
            self.rot_cancel()
            return
        st = self._state()
        n = len(self.steps)
        c = dict(r["clip"], relations_was=r["clip"]["relations"])
        quarter = abs(math.remainder(math.degrees(a), 90)) < 1e-9
        half = abs(math.remainder(math.degrees(a), 180)) < 1e-9
        swap = {"h": "v", "v": "h"} if quarter and not half else {}
        c["relations"] = [(swap.get(q[0], q[0]), *q[1:]) for q in c["relations"] if q[0] not in ("h", "v") or quarter]
        keep = []
        spans = {frozenset(ln) for ln in c["lines"]}
        for t in c["ties"]:
            if t["kind"] == "dim_angx":
                continue
            if t["kind"] in ("dim_dx", "dim_dy") and not half:
                if quarter or frozenset(t["pts"]) not in spans:
                    continue
                t = dict(t, kind="dim_len", ref=abs(t["ref"]))  # along its own line it is that line's length
            keep.append(dict(t, off=t["off"] + a) if t["kind"] in ("dim_r", "dim_d") and t.get("off") is not None else t)
        c["ties"] = keep + ([] if quarter else self._rot_ties(c))
        self.clip = c
        self.pts_turn = (r["pivot"], a)
        self._paste_place(r["pivot"])
        self.pts_turn = None
        del self.steps[n:]
        self.steps.append(("state", st, len(self.lines), len(self.arcs)))
        self.clip = r["had"]
        self.rotating = None
        self.last_place = None
        self.box_sw.reset()
        self.box_sw.hide()
        self.hint = None
        if r["resume"]:
            self.prompt.emit("Paste  Click where the next copy goes  ·  Space: turn it  ·  Esc ends")
        else:
            self.pasting = None
            self.stopped.emit()
            self.prompt.emit("Pick a sketch tool  ·  pull a line or a point to move it  ·  right-click: Finish Sketch")
        self.changed.emit()
        self._refresh()

    def _rot_ties(self, c):
        """Level and upright do not turn: what they held among the figure's lines goes on as parallel and square."""
        flat = [q[1] for q in c["relations_was"] if q[0] == "h"]
        up = [q[1] for q in c["relations_was"] if q[0] == "v"]
        tie = lambda kind, one, two: dict(kind=kind, pts=[*c["lines"][one], *c["lines"][two]], ref=None, mark=None)
        out = [tie("con_par", group[0], k) for group in (flat, up) if group for k in group[1:]]
        if flat and up:
            out.append(tie("con_perp", flat[0], up[0]))
        return out

    def _paint_rot(self, g):
        r = self.rotating
        if r["pivot"] is None or self.mouse is None:
            return
        o = self.to_screen(r["pivot"])
        g.setPen(QtGui.QPen(QtGui.QColor(self.p["accent"]), 1, QtCore.Qt.DashLine))
        g.setBrush(QtCore.Qt.NoBrush)
        g.drawEllipse(o, 4, 4)
        reach = max(_len(self.mouse - r["pivot"]), 1e-6)
        ray = self._rot_start_ray()
        g.drawLine(o, self.to_screen(r["pivot"] + P(math.cos(ray), math.sin(ray)) * reach))
        g.drawLine(o, self.to_screen(r["pivot"] + P(math.cos(ray + r["angle"]), math.sin(ray + r["angle"])) * reach))
        self.box_sw.show_value(math.degrees(r["angle"]) % 360)
        at = self.to_screen(self.mouse)
        self.box_sw.move(int(at.x() + 14), int(at.y() + 12))
        self.box_sw.show()
