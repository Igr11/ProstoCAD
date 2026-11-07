"""InCad preferences, stored in FreeCAD's parameters (User parameter:BaseApp/Preferences/Mod/InCad).
Falls back to an in-memory store when FreeCAD is not importable (widget tests)."""

GROUP = "User parameter:BaseApp/Preferences/Mod/InCad"

THEMES = ("auto", "light", "dark")
RIBBON_SIZES = ("compact", "medium", "large")  # "small" was removed: too small to use; compact: icons only
UNITS = ("mm", "in")
QUALITIES = ("normal", "fine")  # how finely curved faces are drawn (incad.core.quality)


class _MemoryStore:
    def __init__(self):
        self.data = {}

    def get(self, kind, key, default):
        return self.data.get((kind, key), default)

    def set(self, kind, key, value):
        self.data[(kind, key)] = value

    def remove(self, kind, key):
        self.data.pop((kind, key), None)


class _ParamStore:
    def __init__(self):
        import FreeCAD
        self.p = FreeCAD.ParamGet(GROUP)

    def get(self, kind, key, default):
        return {"str": self.p.GetString, "bool": self.p.GetBool}[kind](key, default)

    def set(self, kind, key, value):
        {"str": self.p.SetString, "bool": self.p.SetBool}[kind](key, value)

    def remove(self, kind, key):
        {"str": self.p.RemString, "bool": self.p.RemBool}[kind](key)


_store = None


def _s():
    global _store
    if _store is None:
        try:
            _store = _ParamStore()
        except ImportError:
            _store = _MemoryStore()
    return _store


def use_memory_backend():
    global _store
    _store = _MemoryStore()


def _choice(key, allowed, default):
    value = _s().get("str", key, default)
    return value if value in allowed else default


def _set_choice(key, allowed, value):
    if value not in allowed:
        raise ValueError("%s must be one of %s, got %r" % (key, ", ".join(allowed), value))
    _s().set("str", key, value)


def theme():
    return _choice("Theme", THEMES, "auto")


def set_theme(value):
    _set_choice("Theme", THEMES, value)


def ribbon_size():
    return _choice("RibbonSize", RIBBON_SIZES, "large")


def set_ribbon_size(value):
    _set_choice("RibbonSize", RIBBON_SIZES, value)


def default_units():
    return _choice("DefaultUnits", UNITS, "mm")


def set_default_units(value):
    _set_choice("DefaultUnits", UNITS, value)


def display_quality():
    """Fine on a Mac (smooth highlights on small round faces), Normal elsewhere (a PC's built-in graphics)."""
    import sys
    return _choice("DisplayQuality", QUALITIES, "fine" if sys.platform == "darwin" else "normal")


def set_display_quality(value):
    _set_choice("DisplayQuality", QUALITIES, value)


def update_check_at_start():
    """⚙ ▸ Check for Updates at Start (on by default): once a day ask GitHub for a newer release."""
    return bool(_s().get("bool", "UpdateCheckAtStart", True))


def set_update_check_at_start(value):
    _s().set("bool", "UpdateCheckAtStart", bool(value))


def update_last_check():
    return _s().get("str", "UpdateLastCheck", "")


def set_update_last_check(day):
    _s().set("str", "UpdateLastCheck", day)


def update_skipped():
    """The version the user chose to skip (Skip This Version): the check at start says nothing about it."""
    return _s().get("str", "UpdateSkipped", "")


def set_update_skipped(version):
    _s().set("str", "UpdateSkipped", version)


def rectangle_diagonals():
    """⚙ ▸ Rectangle Diagonals: a new rectangle keeps its two diagonals as construction lines (off by default: the
    user chose the centre point only, the diagonals as an option)."""
    return bool(_s().get("bool", "RectangleDiagonals", False))


def set_rectangle_diagonals(value):
    _s().set("bool", "RectangleDiagonals", bool(value))


def remember_new_choice():
    return bool(_s().get("bool", "RememberNewChoice", False))


def set_remember_new_choice(value):
    _s().set("bool", "RememberNewChoice", bool(value))


def new_kind():
    """What New makes while the choice is remembered: 'part' or 'assembly'."""
    return _choice("NewKind", ("part", "assembly"), "part")


def set_new_kind(value):
    _set_choice("NewKind", ("part", "assembly"), value)


def last_workbench():
    return _s().get("str", "LastWorkbench", "") or "PartDesignWorkbench"


def set_last_workbench(name):
    _s().set("str", "LastWorkbench", name)


def takeover_snapshot():
    return _s().get("str", "TakeoverSnapshot", "")


def set_takeover_snapshot(text):
    _s().set("str", "TakeoverSnapshot", text)


def clear_takeover_snapshot():
    _s().remove("str", "TakeoverSnapshot")


def navigation_enabled():
    return bool(_s().get("bool", "NavigationEnabled", True))


def set_navigation_enabled(value):
    _s().set("bool", "NavigationEnabled", bool(value))


DISPLAYS = ("value", "name", "expression")


def dimension_display():
    return _choice("DimensionDisplay", DISPLAYS, "value")


def set_dimension_display(value):
    _set_choice("DimensionDisplay", DISPLAYS, value)


def thread_clearance():
    """Default print clearance for threads (mm, diametral)."""
    try:
        value = float(_s().get("str", "ThreadClearance", "0.2"))
    except ValueError:
        return 0.2
    return value if 0 <= value <= 2 else 0.2


def thread_preset():
    """Thread ▸ Save for the next part: the settings a new thread starts with ({} when none were saved)."""
    import json
    try:
        data = json.loads(_s().get("str", "ThreadPreset", "") or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def set_thread_preset(values):
    import json
    _s().set("str", "ThreadPreset", json.dumps(values or {}, sort_keys=True))


def set_thread_clearance(value):
    value = float(value)
    if not 0 <= value <= 2:
        raise ValueError("Thread clearance must be between 0 and 2 mm")
    _s().set("str", "ThreadClearance", "%g" % value)


EXPORT_TYPES = ("step", "stl_normal", "stl_fine")


def export_folder():
    """The folder File ▸ Export wrote to last ('' before the first export)."""
    return _s().get("str", "ExportFolder", "")


def set_export_folder(path):
    _s().set("str", "ExportFolder", path)


def export_type():
    return _choice("ExportType", EXPORT_TYPES, "step")


def set_export_type(value):
    _set_choice("ExportType", EXPORT_TYPES, value)


def assembly_axes():
    """View ▸ Show / Hide All Axes in an assembly: the components' axes drawn (on by default; the user: the white
    axis lines can be hidden). A constraint dialog shows them while it waits for a pick either way."""
    return bool(_s().get("bool", "AssemblyAxes", True))


def set_assembly_axes(value):
    _s().set("bool", "AssemblyAxes", bool(value))


def pick_radius_widened():
    """incad.ui.pick_radius has doubled FreeCAD's pick radius for a Retina screen (once: a value set later stays)."""
    return bool(_s().get("bool", "PickRadiusWidened", False))


def set_pick_radius_widened(value):
    _s().set("bool", "PickRadiusWidened", bool(value))


def pick_radius_reach():
    """incad.ui.pick_radius has set FreeCAD's pick radius to its reach in points, on any screen (once)."""
    return bool(_s().get("bool", "PickRadiusReach", False))


def set_pick_radius_reach(value):
    _s().set("bool", "PickRadiusReach", bool(value))
