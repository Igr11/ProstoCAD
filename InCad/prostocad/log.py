"""ProstoCAD's journal: every action of the user and what the program made of it, one line each, in
~/Library/Logs/ProstoCAD-new.log (the user: read it to see what happened, instead of guessing from a picture)."""
import logging
import os

PATH = os.environ.get("PROSTOCAD_LOG_FILE") or os.path.expanduser("~/Library/Logs/ProstoCAD-new.log")
_log = logging.getLogger("prostocad")


def start():
    if _log.handlers:
        return
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    h = logging.FileHandler(PATH, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", "%Y-%m-%d %H:%M:%S"))
    _log.addHandler(h)
    _log.setLevel(logging.INFO)
    _log.info("---- ProstoCAD started")


def catch():
    """Whatever goes wrong goes in the journal too: a mistake inside a click or a key leaves no other trace in the app."""
    import sys
    import traceback
    before = sys.excepthook

    def hook(kind, value, tb):
        _log.error("ERROR\n%s", "".join(traceback.format_exception(kind, value, tb)))
        before(kind, value, tb)

    sys.excepthook = hook


def say(*parts):
    _log.info(" ".join(str(p) for p in parts))


def pt(p):
    return "(%.3f, %.3f)" % (p.x(), p.y()) if p is not None else "(-)"
