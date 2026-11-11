"""The new sketch's tool windows in the old program (ProstoCAD Try — 0.18 New Sketch): Fillet / Chamfer, the sketch
patterns, the Constraint List, Polygon and Text, opened and answered as the new program's main window does
(prostocad/main.py _corner_window, _pattern_window, _constraint_list, _poly_draw_start, _open_polygon, _open_text,
_corner_edit_*, _feature_done, _close_feature), the layer standing in for its sketch view. The logic is the new
program's, moved unchanged; only where the windows stand differs (over the 3D view, top left)."""
from incad.core import log
from incad.qt import QtCore


def _settings():
    from incad.ui import settings_panel
    return settings_panel.store()


class Windows:
    """Mixed into sketch_next.session.Session: self.layer is the SketchView."""

    def _windows_start(self):
        v = self.layer
        self.feature = None
        self.con_window = None
        st = _settings()
        try:
            v.poly_sides = int(st.value("poly_sides", v.poly_sides))
            v.poly_in = str(st.value("poly_in", v.poly_in)).lower() in ("true", "1")
            v.text_font = st.value("text_font", v.text_font) or v.text_font
        except Exception:
            pass
        from incad.sketch_next import texts
        if v.text_font not in texts.fonts():  # a font the part cannot build letters from: the old program's own
            v.text_font = "Arial"
        self.poly_wait = QtCore.QTimer(v)
        self.poly_wait.setSingleShot(True)
        self.poly_wait.timeout.connect(self._poly_show)
        v.text_open.connect(self._open_text)
        v.poly_open.connect(self._open_polygon)
        v.poly_live.connect(self._poly_live)
        v.corner_swapped.connect(self._corner_window)
        v.corner_open.connect(self._corner_edit_open)
        v.pattern_open.connect(self._pattern_edit)
        v.pattern_changed.connect(self._pattern_words)
        v.corner_moved.connect(self._corner_edit_window)
        v.corner_quit.connect(self._corner_edit_done)
        v.stopped.connect(self._poly_draw_end)
        v.poly_set.connect(self._poly_drawn)

    def _window_at(self, w):
        """A window stands top left over the 3D view (the new program: right of the browser, below the ribbon)."""
        self.layer.window_top = 8
        w.adjustSize()
        w.place(QtCore.QPoint(10, self.layer.window_top))

    # ----- Fillet / Chamfer -----
    def _corner_window(self):
        from prostocad import feature_window
        self._close_feature()
        v = self.layer
        w = feature_window.FeatureWindow("Sketch Fillet" if v.tool == "sk_fillet" else "Sketch Chamfer", v, values=dict(v.corner_size))
        w.drawing = True
        w.finished.connect(lambda _name, what: self._corner_done(what))

        def edited():
            v.corner_set({k: w.values[k] for k in w.values if not k.endswith("_expr")})
            v.setFocus()

        w.edited.connect(edited)
        w.space = v.corner_swap
        self._window_at(w)
        w.show()
        self.feature = w
        v.setFocus()

    def _corner_done(self, what):
        v = self.layer
        if what == "cancel" and v.run is not None:  # the whole run goes
            v._restore(v.run["st"])
            v.changed.emit()
        v.stop_tool()

    def _corner_edit_open(self, groups):
        self._close_feature()
        self.layer.corner_edit = dict(groups=groups, at=0, st=self.layer._state())
        self._corner_edit_window()

    def _corner_edit_window(self):
        from prostocad import feature_window
        v = self.layer
        old = self.feature
        if old is not None:
            old.close()
            old.deleteLater()
        e = v.corner_edit
        q = e["groups"][e["at"]][0]["ref"]
        w = feature_window.FeatureWindow("Sketch Fillet" if q["tool"] == "sk_fillet" else "Sketch Chamfer", v, values=dict(q))
        w.finished.connect(lambda _name, what: self._corner_edit_done(what))

        def edited():
            v.corner_edit_set({k: w.values[k] for k in w.values if not k.endswith("_expr")})
            v.setFocus()

        w.edited.connect(edited)
        if len(e["groups"]) > 1:
            w.space = v.corner_next
        self._window_at(w)
        w.show()
        self.feature = w
        v.setFocus()

    def _corner_edit_done(self, what):
        v = self.layer
        if what == "cancel":
            v._restore(v.corner_edit["st"])
            v.corner_edit = None
            v.changed.emit()
            v._refresh()
        self._close_feature()

    # ----- the Constraint List -----
    def _constraint_list(self):
        w = self.con_window
        if w is not None:
            w.close_it()
            return
        from prostocad.constraint_list import ConstraintList
        w = self.con_window = ConstraintList(self.layer, lambda: setattr(self, "con_window", None))
        self.layer.window_top = 8
        w.move(self.layer.width() - w.width() - 190, self.layer.window_top)  # left of the cube
        w.show()
        w.raise_()

    # ----- patterns -----
    def _pattern_edit(self, name):
        kind = next(t["ref"]["kind"] for t in self.layer._pat_recs() if t["ref"]["name"] == name)
        self._pattern_window(kind, name)

    def _pattern_window(self, kind, name):
        from prostocad import feature_window
        self._close_feature()
        v = self.layer
        given = dict(next(t["ref"]["values"] for t in v._pat_recs() if t["ref"]["name"] == name)) if name else {}
        for key, tail in (("spacing1", "_spacing"), ("spacing2", "_spacing2"), ("sweep", "_angle")):
            q = next((p for p in v.params if name and p["name"] == name + tail), None)
            if q is not None:
                given[key] = q["value"]
        w = feature_window.FeatureWindow({"circ": "Circular Pattern", "path": "Along Path"}.get(kind, "Rectangular Pattern"), v, values=given)
        w.drawing = True
        self.feature = w
        if name:
            v.pat_reopen(name, given)
        else:
            v.start_pattern(kind, {k: x for k, x in w.values.items()})
        w.finished.connect(lambda _n, what: self._pattern_done(what))

        def edited():
            v.pat_set({k: x for k, x in w.values.items() if not k.endswith("_expr")})
            v.setFocus()

        w.edited.connect(edited)
        w.on_active = v.pat_active
        v.pat_active("geometry")
        w.show_at = None
        self._window_at(w)
        w.show()
        self._pattern_words()
        v.setFocus()

    def _pattern_words(self):
        v, w = self.layer, self.feature
        if v.pat is None or w is None or "excluded" not in w.picks:
            return
        r = v.pat
        said = lambda p, none: none if p is None else {"axis": "X axis" if p[1] == "x" else "Y axis", "origin": "Origin", "line": "Line", "point": "Point"}[p[0]]
        n = len(r["items"])
        w.picks["geometry"].setText(("%d shape%s" % (n, "" if n == 1 else "s")) if n else "Click the shapes…")
        for key, none in (("dir1", "X axis"), ("dir2", "Y axis"), ("axis", "Click the centre…")):
            if key in w.picks:
                w.picks[key].setText(said(r[key], none))
        if "mode" in w.values and r["v"].get("mode") != w.values["mode"]:
            w.show_choice("mode", r["v"]["mode"])
            w._update_rows()
        for key in ("from1", "to1"):
            if key in w.picks:
                w.picks[key].setText(said(r[key], "Click a point…"))
        if "path" in w.picks:
            w.picks["path"].setText(("%d piece%s" % (len(r["path"]), "" if len(r["path"]) == 1 else "s")) if r["path"] else "Click the curve…")
            w.picks["base"].setText("Point" if r["base"] is not None else "First point")
            w.picks["start"].setText("Point on the curve" if r["start"] is not None else "Click on the curve…")
        w.picks["excluded"].setText(", ".join(str(q) for q in sorted(r["excluded"])) or "Click a copy…")
        w.say(r.get("say"))

    def _pattern_done(self, what):
        if what == "ok" and self.layer.pat is not None and self.layer.pat.get("say"):
            self._prompt("Pattern  " + self.layer.pat["say"])  # not finished yet: the window stays
            return
        self.layer.pattern_done(what)
        self._close_feature()

    # ----- Polygon -----
    def _poly_draw_start(self):
        from prostocad import feature_window
        self._close_feature()
        v = self.layer
        w = feature_window.FeatureWindow("Polygon", v, values=dict(sides=float(v.poly_sides), circle="inside" if v.poly_in else "through"))
        w.drawing = True
        w.values["mode"] = "draw"
        w._update_rows()
        w.finished.connect(lambda _name, _what: v.stop_tool())

        def edited():
            self.poly_wait.stop()
            v.poly_sides = max(3, min(64, int(round(w.values["sides"]))))
            v.poly_in = w.values["circle"] == "inside"
            size, turn = w.field("diameter"), w.field("turn")
            v.box_w.typed = size.number if size.held and size.number > 0 else None
            v.box_ra.typed = turn.number if turn.held else None
            st = _settings()
            st.setValue("poly_sides", v.poly_sides)
            st.setValue("poly_in", v.poly_in)
            v.setFocus()
            v._refresh()

        w.edited.connect(edited)
        self._window_at(w)
        w.show()
        self.feature = w
        v.setFocus()

    def _poly_live(self, size, turn):
        self.poly_shown = (size, turn)
        import time
        if time.monotonic() - getattr(self, "poly_when", 0.0) >= 0.12:
            self._poly_show()
        elif not self.poly_wait.isActive():
            self.poly_wait.start(120)

    def _poly_show(self):
        import time
        self.poly_when = time.monotonic()
        w = self.feature
        if w is not None and w.drawing and w.name == "Polygon" and getattr(self, "poly_shown", None):
            w.field("diameter").set_live(self.poly_shown[0])
            w.field("turn").set_live(self.poly_shown[1])

    def _poly_drawn(self, sides, inside):
        st = _settings()
        st.setValue("poly_sides", sides)
        st.setValue("poly_in", inside)
        w = self.feature
        if w is not None and w.drawing:
            w.show_choice("circle", "inside" if inside else "through")
            if not self.layer.rect_pts:
                w.field("diameter").release()
                w.field("turn").release()

    def _poly_draw_end(self):
        v = self.layer
        if v.pat is not None:  # another command while a pattern's window is open: it is cancelled
            v.pattern_done("cancel")
        w = self.feature
        if w is not None and w.drawing:
            self._close_feature()

    def _open_polygon(self, tie):
        from prostocad import feature_window
        self._close_feature()
        v = self.layer
        self.text_was = v._state()
        inside = (tie["ref"] or {}).get("inside", True)
        w = feature_window.FeatureWindow("Polygon", v, values=dict(sides=float(len(tie["pts"]) - 2), circle="inside" if inside else "through"))
        w.finished.connect(self._feature_done)
        self.poly_tie = tie

        def edited():
            self.poly_tie = v.poly_rebuild(self.poly_tie, w.values["sides"], w.values["circle"] == "inside")
            v.poly_sides, v.poly_in = len(self.poly_tie["pts"]) - 2, w.values["circle"] == "inside"

        w.edited.connect(edited)
        self._window_at(w)
        w.show()
        self.feature = w

    # ----- Text -----
    def edit_text(self, name):
        """The Text window on the text that is the Text object `name` (a double click on its row in the model tree)."""
        tie = next((t for t in self.layer.ties if t["kind"] == "sk_text" and t["ref"].get("obj") == name), None)
        if tie is None:
            log.report("warning", "That text is not in the sketch as it stands: its box or curve is gone")
            return
        self.layer.text_open.emit(tie, None)

    def _open_text(self, tie, before):
        from prostocad import feature_window
        self._close_feature()
        v = self.layer
        was = v._state() if before is None else before
        w = feature_window.FeatureWindow("Text", v, values=tie["ref"])
        w.finished.connect(self._feature_done)

        def edited():
            frame = w.values.get("frame", "rect")
            if frame != tie["ref"].get("frame", "rect"):
                v.text_set_frame(tie, frame)
            tie.update(ref=dict(tie["ref"], **{k: x for k, x in w.values.items() if k != "curve"}))
            v.text_wait = tie if w.values["placement"] == "curve" else None
            v.update()

        def picked():
            w.picks["curve"].setText("Curve taken  ·  click another")

        w.edited.connect(edited)
        self._window_at(w)
        v.text_picked.connect(picked)
        self.text_picked_slot = picked
        self.text_tie = tie
        v.text_wait = tie if w.values["placement"] == "curve" else None
        if tie["pts"][tie["ref"].get("nbox", 4):]:
            picked()
        w.show()
        self.feature = w
        self.text_was = was
        self.text_new = before is not None
        w.first_field.setFocus()
        v.update()

    # ----- closing -----
    def _close_feature(self):
        v = self.layer
        w = self.feature
        if w is not None:
            w.close()
            w.deleteLater()
        self.feature = None
        e = v.corner_edit
        if e is not None:
            v.corner_edit = None
            if [(p.x(), p.y()) for p in e["st"]["pts"]] != [(p.x(), p.y()) for p in v.pts]:
                v.steps.append(("state", e["st"], len(v.lines), len(v.arcs)))
            v.update()

    def _feature_done(self, name, what):
        v = self.layer
        if name == "Polygon":
            self._close_feature()
            if what == "cancel":
                v._restore(self.text_was)
            else:
                v.steps.append(("state", self.text_was, len(v.lines), len(v.arcs)))
                v.changed.emit()
            v.setFocus()
            v.update()
            return
        if name == "Text":
            font = self.feature.values.get("font")
            self._close_feature()
            v.text_wait = None
            v.text_picked.disconnect(self.text_picked_slot)
            tie = self.text_tie
            if what == "cancel":
                v._restore(self.text_was)
            else:
                if tie["ref"].get("placement") == "curve":
                    if tie["pts"][tie["ref"]["nbox"]:]:
                        v.text_drop_box(tie)
                    else:
                        tie["ref"] = dict(tie["ref"], placement="box")
                v.text_font = font
                _settings().setValue("text_font", font)
                v.steps.append(("state", self.text_was, len(v.lines), len(v.lines)))
                v.changed.emit()
            v.setFocus()
            v.update()
            return
        self._close_feature()
