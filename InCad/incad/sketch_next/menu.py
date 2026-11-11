"""The new sketch's right-click menu in the old program (ProstoCAD Try — 0.18 New Sketch): the new program's ring (or
list, its Right-Click Menu setting), with the same commands in the same places (prostocad/main.py _sketch_menu,
RING_TOOLS, _extend_from), moved unchanged; the layer stands in for the new program's sketch view."""
from incad.qt import QtCore, QtWidgets

RING_TOOLS = {"sketch.finish": ("Finish Sketch", "finish"), "sketch.line": ("Line", "line"), "sketch.circle": ("Circle", "circle"),
              "sketch.rectangle": ("Rectangle", "rectangle"), "sketch.dimension": ("Dimension", "dimension"), "sketch.arc": ("3-Point Arc", "arc"),
              "sketch.trim": ("Trim", "trim"), "sketch.extend": ("Extend", "extend"), "sketch.fillet": ("Fillet", "sk_fillet"),
              "sketch.chamfer": ("Chamfer", "sk_chamfer"), "sketch.offset": ("Offset", "offset"), "sketch.mirror": ("Mirror", "mirror")}


class Menu:
    """Mixed into sketch_next.session.Session."""

    def _menu_start(self):
        self.last_tool = None
        self.layer.context.connect(self._sketch_menu)

    def _extend_from(self, here):
        self.command("sketch.extend")
        self.layer.ext_from(here)

    def _sketch_menu(self, at):
        """Right-click in the sketch with no tool running: the ring or the list, the same commands in both. What is
        under the pointer is chosen first; the list offers only what applies to it."""
        from prostocad import marking, theme
        v = self.layer
        here = QtCore.QPointF(v.mapFromGlobal(at))
        v.take_under(here)
        items = []
        spline = v.spline_at(here)
        if spline is not None:
            on = v.comb_on(spline)
            items.append(("Hide Curvature" if on else "Show Curvature", lambda: v.set_comb(spline, not on)))
        if v.can_break():
            items.append(("Break Constraint", v.break_constraint))
        if v.can_unmirror():
            items.append(("Break Mirror Link", v.break_mirror))
        pattern = v._pat_at(here)
        if pattern is not None:
            items.append(("Delete Pattern", lambda: v.delete_pattern(pattern)))
        edit = v.edit_at(here)
        if edit is not None:
            items.append(("Edit", edit))
        if v.can_extend(here):
            items.append(("Extend", lambda: self._extend_from(here)))
        if v.has_chosen():
            items.append(("Construction", lambda: self.command("sketch.construction")))
            items.append(("Rotate", lambda: self.command("sketch.rotate")))
        if v.can_delete():
            items.append(("Delete", v.delete_chosen))
        items.append(("Single Line", lambda: self.command("sketch.line_single")))
        items.append(("Redo", lambda: self.command("edit.redo")))
        items.append(("Cancel Sketch", lambda: self.command("sketch.cancel")))  # under the ring, far from Finish on top
        tool = lambda cmd: (*RING_TOOLS[cmd], lambda: self.command(cmd))
        slots = {"N": tool("sketch.finish"), "W": ("Undo", "undo", lambda: self.command("edit.undo")), "E": tool("sketch.line"), "S": tool("sketch.dimension")}
        by_label = dict(items)
        what = v._pick(here, ("point", "line", "arc", "ell", "spl"))
        corner = v._corners_of(what[1]) if what is not None and what[0] == "point" else None
        if corner is not None:  # a corner: it is cut at once, the tool stays for the next
            def cut(cmd):
                self.command(cmd)
                v._corner_add([corner])
            slots["NE"] = ("Fillet", "sk_fillet", lambda: cut("sketch.fillet"))
            slots["SE"] = ("Chamfer", "sk_chamfer", lambda: cut("sketch.chamfer"))
        elif what is not None and what[0] != "point" and v.is_projected(what):  # the part's edge: Break Link, not Trim
            slots["NE"] = ("Break Link", "construction", lambda: self.break_link(what))
        elif what is not None and what[0] != "point":
            slots["NE"] = tool("sketch.trim")
            if "Extend" in by_label:
                slots["SE"] = ("Extend", "extend", by_label["Extend"])
        elif what is not None and "Break Constraint" in by_label:
            slots["NE"] = ("Break Constraint", "trim", by_label["Break Constraint"])
        if what is not None and "Construction" in by_label:
            slots["SW"] = ("Construction", "construction", by_label["Construction"])
        if what is not None and "Delete" in by_label:
            slots["NW"] = ("Delete", "delete", by_label["Delete"])
        last = self.last_tool
        recent = tool(last) if last in RING_TOOLS and last not in ("sketch.line", "sketch.dimension", "sketch.circle", "sketch.rectangle", "sketch.arc") else tool("sketch.trim")
        for way, row in (("NE", tool("sketch.circle")), ("SE", tool("sketch.rectangle")), ("SW", tool("sketch.arc")), ("NW", recent)):
            slots.setdefault(way, row)
        items = [q for q in items if q[0] not in [row[0] for row in slots.values()]]

        def plain():  # a right click: the list; the flick works here too, unseen
            menu = QtWidgets.QMenu(v)
            for label, action in items:
                menu.addAction(label, action)
            menu.addSeparator()
            for label, icon, action in slots.values():
                menu.addAction(theme.icon(icon), label, action)
            menu.exec(at)
        from incad.ui import settings_panel
        listed = str(settings_panel.store().value("right_menu", "Ring")) != "Ring"
        ring = marking.Ring(slots, items, v.window(), tap=(lambda: QtCore.QTimer.singleShot(0, plain)) if listed else None)
        v.ring = ring
        ring.gone = lambda: setattr(v, "ring", None)
        top = v.mapToGlobal(QtCore.QPoint(0, 0)).y()
        ring.show_at(at, top, None)
