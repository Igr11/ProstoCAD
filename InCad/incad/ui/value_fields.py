"""The sizes at the pointer while a sketch shape is drawn (a line's length, a rectangle's sides, a slot's length and
radius) in one look for every tool (the user: all alike, and good-looking, as in other CAD programs): a rounded box,
see-through a little, the theme's text, the accent when it takes the keys. FreeCAD's own boxes (its on-view
parameters, Gui::QuantitySpinBox on the 3D view) are given this look as they appear; InCad's own tools (the slot)
draw theirs with it."""
from incad.qt import QtCore


def style(dark=None):
    """The stylesheet of a size box (QLineEdit or FreeCAD's QuantitySpinBox)."""
    from incad.ui import theme
    if dark is None:
        dark = theme.current() == "dark"
    bg, text, border, accent = (("rgba(36,39,46,230)", "#f2f2f2", "rgba(255,255,255,45)", "#4f9dff") if dark else
                                ("rgba(255,255,255,235)", "#1d1d1f", "rgba(0,0,0,50)", "#0a64d6"))
    box = ("background: %s; color: %s; border: 1px solid %s; border-radius: 7px; padding: 2px 8px;"
           " font-size: 13px; selection-background-color: %s;" % (bg, text, border, accent))
    return ("QLineEdit, QAbstractSpinBox { %s }"
            " QLineEdit:focus, QAbstractSpinBox:focus { border: 1px solid %s; }"
            " QAbstractSpinBox QLineEdit { background: transparent; border: none; padding: 0; }"
            " QAbstractSpinBox::up-button, QAbstractSpinBox::down-button { width: 0; border: none; }"
            % (box, accent))


def _on_3d_view(w):
    p = w.parentWidget()
    while p is not None:
        if p.metaObject().className() == "Gui::View3DInventor":
            return True
        p = p.parentWidget()
    return False


class Styler(QtCore.QObject):
    """Application event filter: FreeCAD's size boxes on the 3D view take the common look when they show."""

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Show:
            try:
                if obj.metaObject().className() == "Gui::QuantitySpinBox" and _on_3d_view(obj):
                    want = style()
                    if obj.styleSheet() != want:
                        obj.setStyleSheet(want)
            except Exception:
                pass
        return False
