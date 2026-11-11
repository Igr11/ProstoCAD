"""The Sweep dialog's view part: while the path bends too tight for the profile (incad.features.sweep_check), the
sweep is shown as a cage of thin lines instead of a solid, the tight stretch in the edge highlight's red-orange."""
from incad.features import sweep_check
from incad.ui import pattern_ghost

TIGHT = (1.0, 0.27, 0.0)  # #FF4500, as an edge under the pointer


class SweepCage:
    """The shell's overlay for a Sweep dialog: nothing to drag, only the cage when the bend is refused."""

    def __init__(self, shell, session):
        self.shell, self.session = shell, session
        self._node = self._view = None
        sweep_check.LISTENERS.append(self.refresh)
        self.refresh()

    # the navigator's handle hook: nothing to drag
    def dragging(self):
        return False

    def hover(self, *a):
        return False

    def press(self, *a):
        return False

    def move(self, *a):
        return False

    def release(self):
        return False

    def finish(self, *a, **k):
        return None

    def _drop_node(self):
        if self._node is not None and self._view is not None:
            try:
                self._view.getSceneGraph().removeChild(self._node)
            except Exception:
                pass
        self._node = None

    @staticmethod
    def _group(lines, rgb, width):
        from pivy import coin
        sep = coin.SoSeparator()
        color = coin.SoBaseColor()
        color.rgb.setValue(*rgb)
        style = coin.SoDrawStyle()
        style.lineWidth = width
        sep.addChild(color)
        sep.addChild(style)
        sep.addChild(pattern_ghost._lines(lines))
        return sep

    def refresh(self):
        from pivy import coin
        import FreeCADGui as Gui
        self._drop_node()
        cage = sweep_check.CAGE
        if cage is None:
            return
        doc_name, lines = cage
        root = coin.SoSeparator()
        pick = coin.SoPickStyle()
        pick.style = coin.SoPickStyle.UNPICKABLE
        root.addChild(pick)
        plain = [l for l in lines.get("rings", []) + lines.get("long", []) if len(l) > 1]
        if plain:
            root.addChild(self._group(plain, pattern_ghost._color(), 1))
        tight = [l for l in lines.get("tight", []) if len(l) > 1]
        if tight:
            root.addChild(self._group(tight, TIGHT, 4))
        try:
            self._view = Gui.getDocument(doc_name).ActiveView
            self._view.getSceneGraph().addChild(root)
            self._node = root
        except Exception:
            self._node = None

    def remove(self):
        try:
            sweep_check.LISTENERS.remove(self.refresh)
        except ValueError:
            pass
        self._drop_node()
        sweep_check.clear()


def overlay(shell, session):
    return SweepCage(shell, session)
