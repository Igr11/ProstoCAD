"""A long message at the bottom (a save that failed, with the file's whole path) never widens the window: it is cut
with … and said whole in its tip (the user: the window's right side went off the screen)."""
import os
import sys

sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests", "gui"))
sys.path.insert(0, os.path.join(os.environ["INCAD_REPO"], "tests"))
import harness  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from incad.core import prefs  # noqa: E402
from incad.ui import shell  # noqa: E402


def test_long_message_keeps_the_window():
    prefs.set_remember_new_choice(True)
    Gui.activateWorkbench("InCadWorkbench")
    harness.pump(1000)
    mw = Gui.getMainWindow()
    harness.pump(300)
    width = mw.width()
    text = "Save failed: No such file or directory: '" + "/a/very/long/folder" * 40 + "/Solid1.FCStd'"
    shell.instance().status.show_message("error", text)
    harness.pump(600)
    assert mw.width() <= width + 1, ("the window grew", width, mw.width())
    label = shell.instance().status._message
    assert label.isVisible() and "…" in QtLabelText(label), QtLabelText(label)[:80]
    assert label.toolTip() == text and shell.instance().status.texts()["message"] == text


def QtLabelText(label):
    from incad.qt import QtWidgets
    return QtWidgets.QLabel.text(label)  # what is drawn (cut), not the whole message


harness.run([test_long_message_keeps_the_window])
