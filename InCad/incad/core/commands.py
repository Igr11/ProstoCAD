"""One registry for every InCad command: the ribbon, marking menu, menus and shortcuts all read it."""
import traceback
from dataclasses import dataclass

from incad.core import log

ANY = "any"

_before_run = None  # callable(cmd_id) run before every command (the shell ends an active part drag there)


def set_before_run_hook(fn):
    global _before_run
    _before_run = fn


@dataclass(frozen=True)
class Command:
    id: str
    label: str
    icon: str                      # icon name in resources/icons/<theme>/<icon>.svg
    handler: object                # callable() -> anything
    tooltip: str = ""
    shortcut: str = ""             # Qt key sequence text, e.g. "Ctrl+N" (shown as ⌘N on macOS)
    contexts: frozenset = frozenset({ANY})
    transactional: bool = False    # True: runs inside safe.run_safely (one undo step, rollback on failure)
    checkable: bool = False
    is_checked: object = None      # callable() -> bool, for checkable commands
    short: str = ""                # shorter ribbon label (the full label stays in tooltips and menus)


class Registry:
    def __init__(self):
        self._cmds = {}

    def register(self, cmd):
        if cmd.id in self._cmds:
            raise ValueError("duplicate command id %r" % cmd.id)
        if cmd.shortcut:
            for other in self._cmds.values():
                if other.shortcut == cmd.shortcut and self._overlap(other.contexts, cmd.contexts):
                    raise ValueError("shortcut %s of %s clashes with %s" % (cmd.shortcut, cmd.id, other.id))
        self._cmds[cmd.id] = cmd
        return cmd

    @staticmethod
    def _overlap(a, b):
        return ANY in a or ANY in b or bool(a & b)

    def get(self, cmd_id):
        return self._cmds[cmd_id]

    def has(self, cmd_id):
        return cmd_id in self._cmds

    def ids(self):
        return list(self._cmds)

    def for_context(self, ctx):
        return [c for c in self._cmds.values() if ANY in c.contexts or ctx in c.contexts]

    def run(self, cmd_id):
        """Run a command. Never raises; returns safe.SafeResult."""
        from incad.core import perf
        with perf.measure("command " + cmd_id):
            return self._run(cmd_id)

    def _run(self, cmd_id):
        from incad.core.safe import SafeResult
        try:
            from incad.sketch_next import session as new_sketch
            if new_sketch.handles(cmd_id):  # the new sketch is open: its own tools (ProstoCAD Try — 0.18 New Sketch)
                return SafeResult(True, new_sketch.command(cmd_id))
        except Exception as e:
            log.report("error", "%s failed: %s" % (cmd_id, e), traceback.format_exc())
            return SafeResult(False, error=str(e))
        cmd = self._cmds.get(cmd_id)
        if cmd is None:
            log.report("error", "Unknown command %s" % cmd_id)
            return SafeResult(False, error="unknown command %s" % cmd_id)
        if _before_run is not None:
            try:
                _before_run(cmd_id)
            except Exception:
                log.logger().exception("before %s", cmd_id)
        if cmd.transactional:
            from incad.core import safe
            return safe.run_safely(cmd.label, cmd.handler)
        try:
            return SafeResult(True, cmd.handler())
        except Exception as e:
            log.report("error", "%s failed: %s" % (cmd.label, e), traceback.format_exc())
            return SafeResult(False, error=str(e))


REGISTRY = Registry()
