"""The feature windows of ProstoCAD 0.18 (trial): the new program's window (prostocad.feature_window.FeatureWindow)
with the old window's ways (incad.ui.feature_dialog.FeatureDialog), so every feature session works with it unchanged.

The old features keep their logic: their fields (FIELDS), what shows when (show_if), the session's previews, picks,
errors, choices greyed out, sizes offered, Undo/Redo inside the window. What changes is the window: the new
program's look and fields (a size pulled by its dot, a formula typed, through zero for Extrude's Distance), the
window a little smaller when the user says by how much (WIDTH, BOX_H).
- The eight windows the new program has (Thread, Extrude, Hole, Revolve, Fillet, Chamfer, Shell, Boolean): its cards,
  names and order; their choices, conditions and lists are the old feature's own (the keys are the same).
- The other windows: the old fields turned into the new kinds, in their order, picks in a card of their own, a heading
  starts a new card.
- Values go to the session as the old window gave them: texts "10 mm", "45 °", "4", a formula "d0*2"."""
import math
import re

from PySide6 import QtCore, QtWidgets

from incad.core import log
from incad.qt import Signal

Qt = QtCore.Qt
NEW = {"Thread": "Thread", "Extrude": "Extrude", "Hole": "Hole", "Revolve": "Revolve", "Fillet": "Fillet",
       "Chamfer": "Chamfer", "Shell": "Shell", "Boolean": "Boolean"}  # old TITLE -> the new program's window
WIDTH = 257  # px at the standard text size: the new program's 286 less 10 % (the user, 2026-11-08: "a little
BOX_H = 21   # smaller", 10 %); a row's height: 23 less 10 %
TYPE_PAUSE = 600  # ms: a value typed previews by itself after this pause (a click in the 3D view takes no focus)


def _fw():
    from prostocad import feature_window
    return feature_window


def _plain(text):
    """A number as the old window wrote it: no trailing zeros."""
    out = "%.10g" % text
    return "0" if out == "-0" else out


class _Field:
    """What the session reaches through dialog.field(key) / dialog._widgets: a choice's words (_buttons),
    setEnabled, and a size's insert (a parameter clicked in the tree)."""

    def __init__(self, dialog, key):
        self.dialog, self.key = dialog, key

    @property
    def _buttons(self):
        return self.dialog.tiles.get(self.key, {})

    def button(self, value):
        return self.dialog.tiles[self.key][value]

    def setEnabled(self, on):
        if self.key in self.dialog.boxes:
            self.dialog.enable(self.key, on)
        elif self.key in self.dialog.picks:
            self.dialog.picks[self.key].setEnabled(on)

    def isEnabled(self):
        w = self.dialog.boxes.get(self.key) or self.dialog.picks.get(self.key)
        return w is None or w.isEnabled()

    def insert(self, name):
        box = self.dialog.boxes.get(self.key)
        if box is not None and hasattr(box, "edit"):
            box.edit.insert(name)
            box.edit.setFocus()

    def text(self):
        if self._points():
            return str(self.dialog.values.get(self.key) or "")
        box = self.dialog.boxes.get(self.key)
        return box.edit.text() if box is not None and hasattr(box, "edit") else ""

    # a points field, as the old PointsField (3D Line's and Picture Scale's clicks in the view write it)
    def _points(self):
        f = self.dialog._fields.get(self.key)
        return f is not None and f.kind == "points"

    def set_text(self, text):
        """The points put in, nothing told (the old set_text); the caller says changed.emit() when it should."""
        self.dialog.values[self.key] = text or ""
        self.dialog.show_points(len([i for i in (text or "").split(";") if i.strip()]))

    def add(self, item):
        items = [i for i in self.text().split(";") if i.strip()]
        self.set_text("; ".join(items + [item]).strip())
        self.changed.emit()

    @property
    def changed(self):
        class _Said:
            def __init__(self, dialog):
                self.dialog = dialog

            def emit(self):
                self.dialog._update_rows()
                self.dialog._emit()
        return _Said(self.dialog)


def _kinds(old):
    """The new window's fields for an old window that has none of its own: its fields in order; the picks it starts
    with in a card of their own, a heading starts a new card."""
    fw = _fw()
    groups, card = [], []
    for f in old:
        picks_only = card and all(x.kind in ("pick", "list") for x in card)
        if card and (f.kind == "heading" or (picks_only and f.kind not in ("pick", "picks"))):
            groups.append(tuple(card))
            card = []
        card.append(_new_field(fw, f, None))
        tog = getattr(f, "toggle", ())
        if tog:
            card.append(fw.F(tog[0], tog[1], "check", default=False, same_row=True))
    if card:
        groups.append(tuple(card))
    return tuple(groups)


def _unitless(f):
    d = f.default
    if f.kind != "value_length" or not isinstance(d, str) or not d.strip():
        return False
    return re.fullmatch(r"\s*[-+]?\d+(\.\d+)?\s*", d) is not None


def _own_step(f):
    """The old field's step (its arrows'), a number; None when it has none or it cannot be had without values."""
    s = getattr(f, "step", None)
    if s is None:
        return None
    try:
        v = s({}) if callable(s) else s
        return float(v) if v else None
    except Exception:
        return None


def _new_field(fw, f, new):
    """The new window's field for the old field f (on the new window's own field `new`, if it has one)."""
    from incad.features.common import shown
    test = None
    if f.show_if is not None:
        test = (lambda values, old=f: shown(old, values))
    kind = {"value_length": "value", "value_angle": "value", "bool": "check", "picks": "pick"}.get(f.kind, f.kind)
    if f.kind in ("pick", "picks") and f.listed:
        kind = "list"
    if f.kind == "value_angle":
        unit = "°"
    elif _unitless(f):
        unit = ""
    else:
        unit = new.unit if new is not None and new.kind == "value" else "mm"
    signed = bool(getattr(f, "signed", False)) or (new is not None and new.signed)
    blank = f.kind == "value_length" and isinstance(f.default, str) and not f.default.strip()
    own = _own_step(f) if unit else None  # the old field's own step (Split's Gap and Emboss's Depth 0.05 mm, Fillet's
    # 0.1 mm): the old window's arrows stepped by it, so does ↑↓ here (it fell back to 0.1 and 1)
    if new is None:
        step = own if own is not None else (0.1 if f.kind == "value_length" and unit and f.step is not None else 1.0)
        if kind == "value":
            default = 0.0
        elif kind == "check":
            default = bool(f.default)
        elif kind == "slider":
            default = int(f.default or 0)
        elif kind in ("choice", "combo"):
            default = f.default if f.default is not None else (f.choices[0][0] if f.choices else "")
        else:
            default = f.default if f.default is not None else ""
        return fw.F(f.key, f.label, kind, tuple(f.choices) if kind in ("choice", "combo", "toggles", "slider") else (),
                    default, test or (), unit, step, blank, signed, bool(f.same_row), bool(f.ordered),
                    whole=not f.key.startswith("ratio"))
    from dataclasses import replace
    out = replace(new, kind=kind if kind in ("list",) else new.kind, show_if=test or (), signed=signed,
                  ordered=bool(f.ordered))
    if own is not None and new.kind == "value":
        out = replace(out, step=own)
    if f.kind in ("combo", "choice") and {v for v, _w in f.choices} != {v for v, _w in new.choices}:
        out = replace(out, choices=tuple(f.choices))  # the old feature's own choices (Hole's inserts, Thread's sizes);
        # the same choices keep the new window's words (Extrude's Forward, Flipped …: the old window had arrows)
    elif f.kind in ("combo", "choice") and new.choices:  # in the old feature's order (Revolve's axis: the pick
        words = dict(new.choices)  # last, the user)
        out = replace(out, choices=tuple((v, words[v]) for v, _w in f.choices))
    return out


def _eight(name, old):
    """The new program's window for name, its fields' choices, conditions and lists the old feature's own."""
    fw = _fw()
    by_key = {f.key: f for f in old}
    groups = []
    for g in fw.WINDOWS[NEW[name]]:
        card = []
        for n in g:
            f = by_key.get(n.key)
            card.append(_new_field(fw, f, n) if f is not None else n)
        groups.append(tuple(card))
    seen = {n.key for g in groups for n in g}
    left = [f for f in old if f.key not in seen and f.kind != "heading"]
    if left:  # the old feature has a field the new window has not: never left out without a word
        log.logger().warning("%s window: old fields not in the new one: %s", name, [f.key for f in left])
        groups.append(tuple(_new_field(fw, f, None) for f in left))
    return tuple(groups)


class _Values(dict):
    """The new window keeps its values in self.values (a dict); the session calls dialog.values() for the old
    window's texts: this dict answers both."""
    owner = None

    def __call__(self):
        return self.owner._old_values()


def window_class():
    fw = _fw()

    class NewFeatureDialog(fw.FeatureWindow):
        """FeatureDialog's API (incad.ui.feature_dialog) on the new program's window."""
        spot = None  # where the user last left a window, kept for the next one
        changed = Signal(dict)
        accepted = Signal()
        applied = Signal()
        cancelled = Signal()
        pickRequested = Signal(str)
        activePickChanged = Signal(str)
        picksEdited = Signal(str)

        def __init__(self, title, icon, fields, values, theme="light", parent=None):
            from prostocad import theme as next_theme
            self._fields = {f.key: f for f in fields}
            self._ready = False
            self._picks = {f.key: "" for f in fields if f.kind in ("pick", "picks")}
            self._pick_words = {}  # key -> what the session wrote on the pick (set_pick_text)
            self._active_pick = None
            self._clicked_active = False
            self._error_text, self._waiting = "", False
            self._history, self._future, self._stepping = [], [], False
            self._last_value = None
            self._params = {}  # key -> its parameter's name (d0), shown in the field's tip
            self._toggle_of = {f.toggle[0]: f.key for f in fields if getattr(f, "toggle", ())}
            groups = _eight(title, fields) if title in NEW else _kinds(fields)
            super().__init__(title, parent, {}, groups=groups, width=WIDTH, box_h=BOX_H, apply=True,
                             scroll=True, grow=True)  # the old window's rules: fields scroll past the view's
            # height, a combo's longest words show whole (wider only then; Thread's "ISO metric (DIN 13)")
            self.ok_button, self.apply_button, self.cancel_button = self.ok, self.apply_b, self.cancel_b
            kept = _Values(self.values)
            kept.owner = self
            self.values = kept
            self.setStyleSheet(next_theme.qss(next_theme.cur()) + fw.qss(next_theme.cur()))
            self._widgets = {k: _Field(self, k) for k in self._fields}
            for k in self._toggle_of:
                self._widgets[k] = _Field(self, k)
            self.names = self._env
            self.on_points = self._drop_points
            for k, b in self.picks.items():  # a pick's button: the old window's rule (a second click stops picking)
                b.clicked.disconnect()
                b.clicked.connect(lambda _=False, key=k: self._pick_clicked(key))
            self.finished.connect(self._finished)
            self.edited.connect(self._edited)
            self.listEdited.connect(self._edit_list)
            self._type_timer = QtCore.QTimer(self)
            self._type_timer.setSingleShot(True)
            self._type_timer.setInterval(TYPE_PAUSE)
            self._type_timer.timeout.connect(self._apply_typed)
            for b in self.findChildren(fw.Value):
                b.edit.textEdited.connect(lambda _t: self._type_timer.start())
                b.edit.installEventFilter(self)
            self.set_active_pick(None)
            self._ready = True
            self.set_values(values)
            self._history = [self.values()]

        # ----- values: the old window's texts <-> the new window's numbers -----
        def _env(self):
            """The part's named values (dN, sketch dimensions) a formula may use."""
            out = {}
            try:
                import FreeCAD as App
                from incad.core import model
                doc = App.ActiveDocument
                for name, _formula, value in (model.model_parameters(doc) if doc is not None else ()):
                    try:
                        out[name] = float(App.Units.Quantity(value).Value)
                    except Exception:
                        try:
                            out[name] = float(value)
                        except Exception:
                            pass
            except Exception:
                log.logger().exception("feature window: named values")
            return out

        def _number(self, text):
            """(number, formula or None) of an old value text: '10 mm', '45 °', '4', 'd0*2'."""
            text = str(text if text is not None else "").strip()
            if not text:
                return 0.0, None
            try:
                import FreeCAD as App
                return float(App.Units.Quantity(text).Value), None
            except Exception:
                pass
            try:
                from prostocad import units
                from prostocad.sketch import _eval
                return float(_eval(units.literals(text), self._env())), text
            except Exception:
                return 0.0, text

        def _value_text(self, key):
            """The old window's text of a value field."""
            f, nf = self._fields[key], self.rows[key][0]
            expr = self.values.get(key + "_expr")
            if expr:
                return expr
            v = float(self.values.get(key) or 0.0)
            if nf.blank and not v:
                return ""
            if f.kind == "value_angle":
                return "%s °" % _plain(v)
            if nf.unit == "":
                return _plain(v)
            return "%s mm" % _plain(v)

        def _old_values(self):
            out = {}
            for key, f in self._fields.items():
                if f.kind == "heading" or key not in self.rows:
                    continue
                if f.kind in ("value_length", "value_angle"):
                    out[key] = self._value_text(key)
                elif f.kind in ("pick", "picks"):
                    out[key] = self._picks.get(key, "")
                elif f.kind == "bool":
                    out[key] = bool(self.values.get(key))
                elif f.kind == "slider":
                    out[key] = int(self.values.get(key) or 0)
                elif f.kind == "combo":
                    v = self.values.get(key)
                    out[key] = v if v is not None else ""
                else:
                    out[key] = self.values.get(key)
            for key in self._toggle_of:
                out[key] = bool(self.values.get(key))
            if self.name == "Extrude" and "distance" in out and not self.values.get("distance_expr") \
                    and float(self.values.get("distance") or 0.0) < 0:
                # through zero: the new window's Distance below zero is the other way; the old Extrude has no minus,
                # its Direction says the side (Forward <-> Flipped)
                out["distance"] = "%s mm" % _plain(-float(self.values["distance"]))
                out["direction"] = {"default": "flip", "flip": "default"}.get(out.get("direction"), out.get("direction"))
            return out

        def set_values(self, values):
            new = {}
            for key, value in values.items():
                f = self._fields.get(key)
                if key in self._toggle_of:
                    new[key] = bool(value)
                    continue
                if f is None or f.kind == "heading" or key not in self.rows:
                    continue
                if f.kind in ("value_length", "value_angle"):
                    v, expr = self._number(value)
                    new[key] = v
                    new[key + "_expr"] = expr
                elif f.kind in ("pick", "picks"):
                    self._put_pick(key, value or "")
                elif f.kind == "bool":
                    new[key] = bool(value)
                else:
                    new[key] = value
            self.put(new)
            for key, f in self._fields.items():
                if f.kind == "points" and key in values:
                    self.values[key] = values[key] or ""
                    self.show_points(len([i for i in str(values[key] or "").split(";") if i.strip()]))
            self._grey()

        # ----- changes -----
        def _edited(self):
            if not self._ready:
                return
            self._grey()
            self._emit()

        def _grey(self):
            """A value whose toggle is on (Fillet ▸ Full) is not used: greyed, as the old window had it."""
            for key, value_key in self._toggle_of.items():
                if value_key in self.boxes:
                    self.enable(value_key, not self.values.get(key))

        def _value(self, f):
            """A size box: Enter takes the typed size and is OK, as in the old window (the user: the old way)."""
            b = fw.FeatureWindow._value(self, f)
            b.edit.returnPressed.connect(lambda: QtCore.QTimer.singleShot(0, self._enter_ok))
            return b

        def _enter_ok(self):
            if self.ok.isEnabled() and self.isVisible():
                self._finished(self.name, "ok")

        def _emit(self):
            self._type_timer.stop()
            values = self.values()
            if not self._stepping and (not self._history or values != self._history[-1]):
                self._history.append(values)
                self._future = []
            self.changed.emit(values)

        def _apply_typed(self):
            for b in self.findChildren(fw.Value):
                if b.edit.hasFocus():
                    b.apply()

        def _finished(self, _name, what):
            self._type_timer.stop()
            self._apply_typed()  # a value typed but not applied (OK clicked: on macOS the click takes no focus)
            {"ok": self.accepted, "apply": self.applied, "cancel": self.cancelled}[what].emit()

        def eventFilter(self, obj, e):
            if e.type() == QtCore.QEvent.FocusIn and isinstance(obj, QtWidgets.QLineEdit):
                box = obj.parentWidget()
                self._last_value = next((k for k, b in self.boxes.items() if b is box), None)
                if self.active_pick() is not None:
                    self.set_active_pick(None)  # typing a value ends picking (the preview shows again)
            return False

        # ----- Undo / Redo inside the window -----
        def reset_history(self):
            self._history, self._future = [self.values()], []

        def undo_change(self):
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

        # ----- picks -----
        def _first_pick(self):
            pass  # the session says which pick is on (the old window never chose one by itself)

        def activate(self, key):
            """The pick's look: on (the accent edge) or off; what it means is set_active_pick's."""
            self.active = key
            for k, b in self.picks.items():
                b.setProperty("active", k == key)
                b.style().unpolish(b)
                b.style().polish(b)

        def _pick_clicked(self, key):
            if key == self.active_pick() and self._clicked_active:
                self.set_active_pick(None)
                return
            if key == self.active_pick():
                self._clicked_active = True
                return
            self.set_active_pick(key)
            self._clicked_active = True
            self.pickRequested.emit(key)

        def set_active_pick(self, key):
            self.activate(key)
            changed = key != self._active_pick
            self._active_pick = key
            self._clicked_active = False
            if changed and self._ready:
                self.activePickChanged.emit(key or "")

        def active_pick(self):
            return self._active_pick

        def _label_of(self, key, value):
            from incad.features.common import pick_label
            return pick_label(self._fields[key].pick or "item", value or "")

        def _put_pick(self, key, text):
            self._picks[key] = text
            self.values[key] = text  # a condition may look at it (shown while a sketch is picked)
            f = self._fields[key]
            if key in self.lists:
                self._fill_list(key)
            words = self._pick_words.get(key) if text else None
            if f.listed and f.ordered:
                words = "Click sketches in order…" if not text.strip() else "Click to add more…"
            if not text.strip() and key in fw.PICK_WORDS and self.name in NEW:
                words = words or fw.PICK_WORDS[key]  # the new window's words while nothing is picked
            self.set_pick_text(key, words or self._label_of(key, text))
            whole = self._label_of(key, text)
            if key in self.picks and not self.picks[key].toolTip() and whole != self.picks[key].text():
                self.picks[key].setToolTip(whole)  # the old words (Shift: a loop …) in its tip

        def set_pick(self, key, text):
            if self._fields[key].kind == "points":
                self.values[key] = text or ""
                self.show_points(len([i for i in (text or "").split(";") if i.strip()]))
            else:
                self._put_pick(key, text or "")
            self._update_rows()
            self._emit()

        def set_pick_text(self, key, text):
            if key in self.picks:
                fw.FeatureWindow.set_pick_text(self, key, text)

        def pick_text(self, key):
            return self.picks[key].text() if key in self.picks else ""

        def field_def(self, key):
            return self._fields[key]

        @staticmethod
        def _object_label(name):
            try:
                import FreeCAD as App
                obj = App.ActiveDocument.getObject(name) if App.ActiveDocument is not None else None
                return obj.Label if obj is not None else name
            except Exception:
                return name

        def _fill_list(self, key):
            """The rows of a listed pick, as the old window wrote them."""
            f = self._fields[key]
            items = [i.strip() for i in self._picks.get(key, "").split(",") if i.strip()]
            objects = {i.partition(":")[0] for i in items}
            texts = []
            for item in items:
                obj, _, sub = item.partition(":")
                if f.ordered:
                    texts.append(self._object_label(obj))
                    continue
                text = re.sub(r"^([A-Za-z]+)(\d+)$", r"\1 \2", sub or obj)  # Edge12 -> Edge 12
                if (f.pick or "").endswith("instance"):
                    text = text.replace("Instance", "Copy")
                if len(objects) > 1 and sub:
                    text = "%s · %s" % (self._object_label(obj), text)
                texts.append(text)
            self.set_list(key, texts)

        def _edit_list(self, key, index, action):
            items = [i.strip() for i in self._picks.get(key, "").split(",") if i.strip()]
            if action == "clear":
                items = []
            elif action == "remove":
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
            return fw.FeatureWindow.list_rows(self, key)

        def _drop_points(self, last):
            key = next(k for k, f in self._fields.items() if f.kind == "points")
            items = [i for i in str(self.values.get(key) or "").split(";") if i.strip()]
            self.set_pick(key, "; ".join(items[:-1]).strip() if last else "")

        # ----- the rest of the old window's ways -----
        def field(self, key):
            return self._widgets[key]

        def row_visible(self, key):
            return key in self.rows and self.rows[key][1].isVisible()

        def label_text(self, key):
            row = self.rows[key][1]
            l = row if isinstance(row, QtWidgets.QCheckBox) else row.findChild(QtWidgets.QLabel, "FwLabel")
            return l.text() if l is not None else self._fields[key].label

        def set_param_name(self, key, name):
            if key not in self.rows:
                return
            # the size's parameter (d0) in its tip, not in its name: the name stays as short as the new program's
            # (the user, 2026-11-08: "(d0)" beside the name took the room of the choices)
            row, box = self.rows[key][1], self.boxes.get(key)
            tip = "Parameter %s" % name if name else ""
            for w in (row.findChild(QtWidgets.QLabel, "FwLabel") if not isinstance(row, QtWidgets.QLabel) else None, box,
                      getattr(box, "edit", None)):
                if w is not None:
                    w.setToolTip(tip)
            self._params[key] = name

        def set_error(self, text, rescue=None, waiting=False):
            self._error_text, self._waiting = text or "", bool(waiting and text)
            if self._waiting:
                self.note(text)
            else:
                self.say(text)
            self.lock(bool(text), rescue)

        def error_text(self):
            return self._error_text

        def error_waiting(self):
            return self._waiting

        def set_info(self, text):
            self.info(text or "")

        def info_text(self):
            return self._info_full

        def focused_value_field(self):
            for key, b in self.boxes.items():
                if hasattr(b, "edit") and b.edit.hasFocus():
                    return self._widgets.get(key)
            return self._widgets.get(self._last_value) if self._last_value else None

        # ----- where it stands -----
        def place(self, pos=None):
            """Where the last window was left (inside this view), else the view's top left corner."""
            self.adjustSize()
            at = pos if pos is not None else NewFeatureDialog.spot if NewFeatureDialog.spot is not None \
                else QtCore.QPoint(16, 16)
            fw.FeatureWindow.place(self, at)

        def _release(self, e):
            if self._drag is not None:
                NewFeatureDialog.spot = self.pos()
            fw.FeatureWindow._release(self, e)

        def keyPressEvent(self, e):
            if e.key() == Qt.Key_Escape:
                self._type_timer.stop()
                self.cancelled.emit()
                return
            QtWidgets.QFrame.keyPressEvent(self, e)

    return NewFeatureDialog


_CLASS = None


def NewFeatureDialog(title, icon, fields, values, theme="light", parent=None):
    """The window for a feature session (made as FeatureDialog is)."""
    global _CLASS
    if _CLASS is None:
        _CLASS = window_class()
    return _CLASS(title, icon, fields, values, theme, parent)
