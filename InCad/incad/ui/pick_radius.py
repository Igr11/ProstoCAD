"""FreeCAD's pick radius (Preferences ▸ Display ▸ 3D View ▸ Pick radius) counts device pixels: its default 5 is 2.5
points round a sketch point on a Retina screen and a tiny ring on any screen, so a point between lines (a
rectangle's centre point on its diagonals) caught the pointer one time in five (the user, on the Mac and again on
Windows). On every screen, whatever its scale, InCad sets it to REACH points of the screen (device pixels = points x
the pixel ratio), once; a radius the user chose stays. Points still win over the lines under them (drawn above)."""
import FreeCAD as App

from incad.core import prefs

VIEW = "User parameter:BaseApp/Preferences/View"
DEFAULT = 5.0  # FreeCAD's own
REACH = 8.0  # points round the pointer a click takes a point from


def widen():
    from incad.qt import QtWidgets
    app = QtWidgets.QApplication.instance()
    screen = app.primaryScreen() if app is not None else None
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    p = App.ParamGet(VIEW)
    now = p.GetFloat("PickRadius", DEFAULT)
    ours = (DEFAULT, DEFAULT * ratio, DEFAULT * 2.0)  # FreeCAD's, or what an older InCad set (Retina only)
    if prefs.pick_radius_reach() or not any(abs(now - v) < 1e-6 for v in ours):
        return  # set already, or the user's own
    p.SetFloat("PickRadius", REACH * max(ratio, 1.0))
    prefs.set_pick_radius_reach(True)
