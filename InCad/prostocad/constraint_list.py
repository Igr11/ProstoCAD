"""The sketch's Constraint List: what holds it, one row each. A row and the mark on the sketch choose each other and light
together; Delete Selected or the Delete key takes what is chosen; with shapes chosen the list shows only theirs."""
from PySide6 import QtCore, QtGui, QtWidgets

from prostocad import feature_window, theme

Qt = QtCore.Qt
P = QtCore.QPointF
WORDS = {"h": "Horizontal", "v": "Vertical", "par": "Parallel", "perp": "Perpendicular", "tan": "Tangent", "tan_on": "Tangent"}


class ConstraintTools:
    """Mixed into SketchView. An owner is ("tie", id of the tie) or ("rel", number of the relation)."""

    def con_owner_tie(self, owner):
        return next((t for t in self.ties if id(t) == owner[1]), None) if owner[0] == "tie" else None

    def _con_pts(self, owner):
        t = self.con_owner_tie(owner)
        if t is not None:
            return list(t["pts"])
        if owner[0] == "rel" and owner[1] < len(self.relations):
            line = self.relations[owner[1]][1]
            return list(self.lines[line]) if line is not None and line < len(self.lines) else []
        return []

    def _con_words(self, pts):
        """What these points belong to, in words."""
        out, rest = [], list(pts)
        for i, ln in enumerate(self.lines):
            if set(ln) <= set(rest):
                out.append("Line %d" % (i + 1))
                rest = [k for k in rest if k not in ln]
        for i, arc in enumerate(self.arcs):
            if set(rest) & set(arc):
                out.append(("Circle %d" if arc[1] == arc[2] else "Arc %d") % (i + 1))
                rest = [k for k in rest if k not in arc]
        for k in rest:
            who = next((i for i, ln in enumerate(self.lines) if k in ln), None)
            out.append("Line %d end" % (who + 1) if who is not None else "Point %d" % (k + 1))
        return ", ".join(dict.fromkeys(out)) or "Sketch"

    def con_rows(self):
        """[(owner, kind, what, value)], for what is chosen if something is."""
        rows = []
        for i, (kind, line, _end, _ref) in enumerate(self.relations):
            if kind in WORDS and line is not None and line < len(self.lines):
                rows.append((("rel", i), WORDS[kind], self._con_words(self.lines[line]), ""))
        for t in self.ties:
            if t.get("mark") and not t.get("mirror") and not t.get("pat"):
                rows.append((("tie", id(t)), t["mark"], self._con_words(t["pts"]), ""))
        for t in self.dims():
            rows.append((("tie", id(t)), "Dimension " + (t.get("name") or ""), self._con_words(t["pts"]), self._dim_text(t)))
        whole = [set(self.lines[i]) for i in self.sel_lines if i < len(self.lines)] + [set(self.arcs[i]) for i in self.sel_arcs if i < len(self.arcs)]
        if whole or self.sel_pts:  # a line's own, not its neighbours' at a shared corner
            def mine(owner):
                pts = set(self._con_pts(owner))
                return bool(pts & self.sel_pts) or any(q <= pts or (len(q) == 3 and pts & q) for q in whole)
            rows = [r for r in rows if mine(r[0])]
        return rows

    def con_mark_owner(self, s):
        """The mark at the screen point s: who it stands for."""
        for (r, _name), owner in zip(self.tiles, self.tile_owner):
            if r.contains(s):
                return owner
        return None

    def con_pick(self, owner, adding=False):
        self.con_lit = (self.con_lit ^ {owner}) if adding else {owner}
        self.con_changed.emit()
        self.update()

    def con_set(self, owners):
        """The list chose these; a dimension lights as it does when clicked."""
        self.con_lit = set(owners)
        self.sel_dims = {o[1] for o in owners if o[0] == "tie" and (self.con_owner_tie(o) or {}).get("kind", "").startswith("dim")}
        self.setFocus()
        self.update()

    def con_clear(self):
        if self.con_lit:
            self.con_lit = set()
            self.sel_dims = set()
            self.con_changed.emit()
            self.update()

    def con_delete(self):
        """What is chosen goes; a dimension a formula uses stays, and says so."""
        owners = set(self.con_lit)
        used = [t.get("name") for t in map(self.con_owner_tie, owners) if t is not None and t.get("name") and self._dim_uses(t["name"])]
        if used:
            self.prompt.emit("A formula uses %s: it stays" % ", ".join(used))
            return
        st = self._state()
        ids = {o[1] for o in owners if o[0] == "tie"}
        rels = {o[1] for o in owners if o[0] == "rel"}
        self.ties = [t for t in self.ties if id(t) not in ids]
        self.relations = [q for i, q in enumerate(self.relations) if i not in rels]
        self.steps.append(("state", st, len(self.lines), len(self.arcs)))
        self.con_lit = set()
        self.changed.emit()
        self.con_changed.emit()
        self._refresh()

    def _paint_con_lit(self, g):
        lit = set(self.con_lit)
        if self.con_hover is not None:
            lit.add(self.con_hover)
        if not lit:
            return
        g.setBrush(Qt.NoBrush)
        g.setPen(QtGui.QPen(QtGui.QColor(self.p["snap"]), 2.6, Qt.SolidLine, Qt.RoundCap))
        for owner in lit:
            pts = self._con_pts(owner)
            drawn = set()
            for ln in self.lines:
                if set(ln) <= set(pts):
                    g.drawLine(self.to_screen(self.pts[ln[0]]), self.to_screen(self.pts[ln[1]]))
                    drawn |= set(ln)
            for i, arc in enumerate(self.arcs):
                if arc[0] in pts or (set(arc[1:]) & set(pts) and not set(arc[1:]) <= drawn):
                    c, r, a0, sw = self.arc_shape(i)
                    g.drawPolyline(self._arc_poly(c, r, a0, sw))
                    drawn |= set(arc)
            for k in pts:
                if k not in drawn and k < len(self.pts):
                    g.drawEllipse(self.to_screen(self.pts[k]), 5, 5)
        for (r, _name), owner in zip(self.tiles, self.tile_owner):
            if owner in lit:
                g.drawRoundedRect(r.adjusted(-2, -2, 2, 2), 4, 4)


class ConstraintList(QtWidgets.QFrame):
    """The window. It floats over the sketch and is moved by its head."""
    WIDTH = 288

    def __init__(self, view, closed):
        super().__init__(view)
        self.view, self.closed = view, closed
        self.setObjectName("FwWin")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._drag = None
        self._busy = False
        self.setFixedWidth(self.WIDTH)
        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        head = QtWidgets.QFrame()
        head.setObjectName("FwHead")
        h = QtWidgets.QHBoxLayout(head)
        h.setContentsMargins(10, 4, 6, 4)
        self.title = QtWidgets.QLabel("Constraint List")
        self.title.setObjectName("FwTitle")
        h.addWidget(self.title)
        h.addStretch(1)
        shut = QtWidgets.QPushButton("Close")
        shut.setObjectName("FwButton")
        shut.clicked.connect(self.close_it)
        h.addWidget(shut)
        head.mousePressEvent = self._press
        head.mouseMoveEvent = self._move
        head.mouseReleaseEvent = self._release
        col.addWidget(head)
        self.table = QtWidgets.QTreeWidget()
        self.table.setRootIsDecorated(False)
        self.table.setStyleSheet(feature_window.list_style(theme.cur()))
        self.table.setColumnCount(3)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        head = self.table.header()
        head.setStretchLastSection(False)
        for k, mode in enumerate((QtWidgets.QHeaderView.ResizeToContents, QtWidgets.QHeaderView.Stretch, QtWidgets.QHeaderView.ResizeToContents)):
            head.setSectionResizeMode(k, mode)  # the middle gives way, so the value stays inside the narrow window
        self.table.setHeaderLabels(("Constraint", "What it holds", "Value"))
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)  # each click takes a row or lets it go: one hand is enough
        self.table.setMouseTracking(True)
        self.table.itemSelectionChanged.connect(self._chosen)
        self.table.itemChanged.connect(self._ticked)
        self.table.itemEntered.connect(self._over)
        self.table.viewport().installEventFilter(self)
        card = QtWidgets.QFrame()  # the same card the other windows hold their rows in
        card.setObjectName("FwCard")
        c = QtWidgets.QVBoxLayout(card)
        c.setContentsMargins(4, 4, 4, 4)
        c.addWidget(self.table)
        body = QtWidgets.QVBoxLayout()
        body.setContentsMargins(7, 7, 7, 0)
        body.addWidget(card)
        col.addLayout(body)
        foot = QtWidgets.QHBoxLayout()
        foot.setContentsMargins(9, 8, 9, 9)
        self.all_on = QtWidgets.QPushButton("Select All")
        self.all_on.setObjectName("FwButton")
        self.all_on.clicked.connect(self.table.selectAll)
        self.gone = QtWidgets.QPushButton("Delete Selected")
        self.gone.setObjectName("FwOk")
        self.gone.clicked.connect(self._delete)
        foot.addWidget(self.all_on)
        foot.addStretch(1)
        foot.addWidget(self.gone)
        col.addLayout(foot)
        self.adjustSize()
        view.con_changed.connect(self.fill)
        view.changed.connect(self.fill)
        view.con_open = True  # with the list open, clicks add up without a key held
        self.fill()

    def fill(self):
        self._busy = True
        v = self.view
        rows = v.con_rows()
        self.title.setText("Constraint List  ·  %d" % len(rows))
        self.table.clear()
        for owner, kind, what, value in rows:
            item = QtWidgets.QTreeWidgetItem((kind, what, value))
            item.setData(0, Qt.UserRole, owner)
            item.setSizeHint(1, QtCore.QSize(0, feature_window.list_row()))  # column 1 gives way; column 0 keeps its width from its words
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked if owner in v.con_lit else Qt.Unchecked)
            self.table.addTopLevelItem(item)
            if owner in v.con_lit:
                item.setSelected(True)
                self.table.scrollToItem(item)
        self._busy = False
        feature_window.fit_rows(self, self.table, len(rows))

    def showEvent(self, e):
        super().showEvent(e)
        feature_window.fit_rows(self, self.table, self.table.topLevelItemCount())

    def _chosen(self):
        if self._busy:
            return
        self._busy = True
        for k in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(k)
            item.setCheckState(0, Qt.Checked if item.isSelected() else Qt.Unchecked)
        self._busy = False
        self.view.con_set([i.data(0, Qt.UserRole) for i in self.table.selectedItems()])

    def _ticked(self, item, _col):
        """The little tick itself: the same as the row."""
        if not self._busy:
            item.setSelected(item.checkState(0) == Qt.Checked)

    def _over(self, item, _col):
        self.view.con_hover = item.data(0, Qt.UserRole)
        self.view.update()

    def eventFilter(self, _obj, e):
        if e.type() == QtCore.QEvent.Leave:
            self.view.con_hover = None
            self.view.update()
        return False

    def _delete(self):
        self.view.con_delete()

    def close_it(self):
        self.view.con_changed.disconnect(self.fill)
        self.view.changed.disconnect(self.fill)
        self.view.con_hover = None
        self.view.con_open = False
        self.close()
        self.deleteLater()
        self.closed()

    def _press(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.pos()

    def _move(self, e):
        if self._drag is not None:
            at = e.globalPosition().toPoint() - self._drag
            top = getattr(self.parentWidget(), "window_top", 0)
            self.move(max(0, at.x()), max(top, at.y()))

    def _release(self, _e):
        self._drag = None
