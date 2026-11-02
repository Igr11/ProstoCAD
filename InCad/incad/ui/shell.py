"""Full UI takeover. While InCad is active FreeCAD's menus, toolbars, docks and status bar are hidden and InCad's
title strip, ribbon, Model Browser and status bar are shown. Everything is restored on exit, also after a quit or
a crash with InCad active: the snapshot stays in InCad's preferences until the restore has been done."""
import json
import os
import re
import time
import traceback

from incad import host
from incad.core import commands, faceted, log, perf, prefs, safe
from incad.qt import dispose, QAction, QtCore, QtGui, QtWidgets
from incad.ui import fresh_redraw, ribbon_layout
from incad.ui import theme as themes
from incad.ui.browser import ModelBrowser
from incad.ui.ribbon import Ribbon
from incad.ui.statusbar import StatusBar
from incad.ui.titlebar import TitleStrip
from incad.ui.sketch_session import SketchSession
from incad.ui.value_box import ValueBox
from incad.ui.feature_session import FeatureSession
from incad.ui.info_panel import InfoPanel
from incad.ui.plane_panel import PlanePanel
from incad.ui import marking_layout
from incad.ui.marking_menu import MarkingMenu
from incad.ui.nav import Navigator, to_coin
from incad.ui.nav_strip import NavStrip

OURS = ("InCadTop", "InCadBrowserDock", "InCadStatusBar")
MENU_PREFIX = "InCadMenu_"
TOOLBAR_GROUP = "User parameter:BaseApp/MainWindow/Toolbars"
NAV_KEYS = {"NavigationStyle": "String", "ZoomAtCursor": "Bool"}
NAV_VALUES = {"NavigationStyle": ("String", "Gui::CADNavigationStyle"), "ZoomAtCursor": ("Bool", True)}
MENUS = [("File", ["file.new", "file.new_assembly", "file.open", "file.save", "file.save_as", None, "file.export", "view.screenshot", "view.screenshot_window", None, "file.close", None, "file.quit"]),
         ("Edit", ["edit.undo", "edit.redo"]),
         ("View", ["view.theme_toggle", "view.zoom_all", "view.home", "view.style_shaded",
                   "view.style_wireframe", "view.assembly_context", "view.parameters", None, "view.freecad"])]

_shell = None


# FreeCAD commands whose shortcuts InCad's own menus take over while active: FreeCAD's actions sit in its hidden
# menus, where they no longer answer keys (probed: ⌘Z did nothing), so InCad owns the keys and gives them back.
TAKEN_SHORTCUTS = ("Std_New", "Std_Open", "Std_Save", "Std_SaveAs", "Std_CloseActiveWindow", "Std_Undo", "Std_Redo",
                   "Std_Quit", "Std_Export", "Std_ToggleVisibility")  # Space: InCad's own Hide / Show


def _std_actions(name):
    import FreeCADGui as Gui
    try:
        return list(Gui.Command.get(name).getAction())
    except Exception:
        return []


def _std_new_actions():
    return _std_actions("Std_New")


class _DocObserver:
    """FreeCAD document observer: any change schedules one (coalesced) panel refresh."""

    def __init__(self, callback, sketch_callback=None, undo_callback=None, activate_callback=None,
                 delete_callback=None, projection_callback=None):
        self.callback = callback
        self.sketch_callback = sketch_callback
        self.projection_callback = projection_callback
        self.undo_callback = undo_callback
        self.activate_callback = activate_callback
        self.delete_callback = delete_callback

    def slotCreatedDocument(self, doc):
        self.callback()

    def slotDeletedDocument(self, doc):
        self.callback()
        if self.delete_callback is not None:
            self.delete_callback(doc)

    def slotRelabelDocument(self, doc):
        self.callback()

    def slotActivateDocument(self, doc):
        self.callback()
        if self.activate_callback is not None:
            self.activate_callback(doc)

    def slotCreatedObject(self, obj):
        self.callback()

    def slotDeletedObject(self, obj):
        self.callback()

    def slotChangedObject(self, obj, prop):
        self.callback()
        if prop == "BaseFeature":  # a feature put into a Solid: no Refine on an STL's triangles
            faceted.base_changed(obj)
        if prop == "ExternalGeometry" and self.projection_callback is not None \
                and getattr(obj, "TypeId", "") == "Sketcher::SketchObject":
            self.projection_callback(obj)  # FreeCAD sets it on many recomputes: only the projection follow-up
        if prop in ("Constraints", "Geometry") and getattr(obj, "TypeId", "") == "Sketcher::SketchObject" \
                and self.sketch_callback is not None:
            self.sketch_callback(obj)

    def slotUndoDocument(self, doc):
        self.callback()
        if self.undo_callback is not None:
            self.undo_callback(doc)

    def slotRedoDocument(self, doc):
        self.slotUndoDocument(doc)

    def slotRecomputedDocument(self, doc):
        self.callback()
        from incad.features import split
        if split.NEEDS_SYNC:  # a Split's knife edited outside its dialog: its piece Solids shared out again
            QtCore.QTimer.singleShot(0, split.sync_pending)
        if any(o.TypeId in ("PartDesign::Hole", "PartDesign::SubtractiveHelix", "PartDesign::AdditivePipe")
               or o.TypeId in safe._LINKED or "InCadThroughAllReach" in o.PropertiesList  # holes, threads, hollow
               for o in getattr(doc, "Objects", ())):  # sweeps, tapered Through All, dress-ups: measured again
            QtCore.QTimer.singleShot(0, lambda d=doc: _follow_holes(d))


_FOLLOWED_AT = {}  # document -> its undo count when the follows last ran and changed nothing
_LINKS = {}  # document -> safe.link_snapshot of it after the last recompute (see _follow_holes)


def _follow_holes(doc):
    """After a recompute (not while a dialog owns the model): holes that measured the part once follow it. A follow
    that fails rolls back, and the rollback's recompute brought it here again, for ever (the finder): with no new
    undo step since the last run that changed nothing, nothing is tried again."""
    from incad.core import safe
    from incad.features import hole
    try:
        if safe.busy() or safe.edit_open() or doc.Name not in __import__("FreeCAD").listDocuments():
            return
        links = _LINKS.get(doc.Name)  # a sketch or parameter edit lost a fillet's edges: found again first
        if links and safe.repair_links(doc, links):
            doc.recompute()
        _LINKS[doc.Name] = safe.link_snapshot(doc)
        if _FOLLOWED_AT.get(doc.Name) == doc.UndoCount:
            return
        from incad.features import extrude, shapes, thread
        moved = hole.follow(doc) + thread.follow_part(doc) + shapes.follow_walls(doc) + extrude.follow_reach(doc)
        if not moved:
            _FOLLOWED_AT[doc.Name] = doc.UndoCount
        if moved:
            log.report("info", "Following the part: %s" % ", ".join(moved))
    except Exception:
        log.logger().exception("holes follow the part")


class _MeasurePicker:
    """While Measure is live: any selection change measures again."""

    def __init__(self, shell, mode):
        self.shell, self.mode = shell, mode

    def _again(self):
        shell = self.shell
        QtCore.QTimer.singleShot(0, lambda: shell._measure is self and shell.active and shell.inspect(self.mode))

    def addSelection(self, doc_name, obj_name, sub, pnt):
        self._again()

    def removeSelection(self, doc_name, obj_name, sub):
        self._again()

    def clearSelection(self, doc_name):
        self._again()


def _fit_points_in_view(view, points):
    """Zoom out (keeping the camera direction) until every point is inside the viewport, so markers never land
    off screen. Only ever zooms OUT — a view that already shows them is left alone."""
    try:
        import FreeCAD as App
        if not points:
            return
        w, h = view.getSize()
        if not w or not h:
            return
        margin = 0.08
        need = False
        for p in points:
            x, y = view.getPointOnViewport(App.Vector(p))
            if not (w * margin <= x <= w * (1 - margin) and h * margin <= y <= h * (1 - margin)):
                need = True
                break
        if not need:
            return
        view.fitAll()
        # fitAll may still clip markers right at the edge; nudge the zoom out a touch so all sit inside
        from pivy import coin
        cam = view.getCameraNode()
        if cam.isOfType(coin.SoOrthographicCamera.getClassTypeId()):
            cam.height.setValue(cam.height.getValue() * 1.15)
        elif cam.isOfType(coin.SoPerspectiveCamera.getClassTypeId()):
            pos = cam.position.getValue()
            focal = cam.focalDistance.getValue()
            cam.position.setValue(pos.getValue()[0], pos.getValue()[1], pos.getValue()[2])
            cam.focalDistance.setValue(focal * 1.15)
    except Exception:
        pass


class _PointPick:
    """N points clicked on markers drawn on the view (never in the document): the section plane by 3 points, a
    revolve axis by 2. `on_done(points)` when all are picked, `on_cancel()` on Esc or another pick taking over."""

    def __init__(self, view, points, kinds, count=3, prompt="", on_done=None, on_cancel=None):
        from pivy import coin
        self.view, self.points, self.kinds, self.picked = view, points, kinds, []
        self.count, self.prompt, self.on_done, self.on_cancel = count, prompt, on_done, on_cancel
        self._plane = None  # the faint plane through the points (show_plane)
        self.node = coin.SoAnnotation()  # drawn on top of the part
        for kind, color in (("corner", (0.95, 0.62, 0.29)), ("other", (0.40, 0.70, 1.0))):
            pts = [p for p, k in zip(points, kinds) if (k == "corner") == (kind == "corner")]
            if not pts:
                continue
            sep = coin.SoSeparator()
            c = coin.SoBaseColor()
            c.rgb.setValue(*color)
            style = coin.SoDrawStyle()
            style.pointSize = 13
            coords = coin.SoCoordinate3()
            coords.point.setValues(0, len(pts), [(p.x, p.y, p.z) for p in pts])
            for n in (c, style, coords, coin.SoPointSet()):
                sep.addChild(n)
            self.node.addChild(sep)
        view.getSceneGraph().addChild(self.node)

    def remove(self):
        try:
            self.view.getSceneGraph().removeChild(self.node)
        except Exception:
            pass

    def show_plane(self, pts):
        """A faint plane through three points (the user: see the plane the three points make while picking them),
        a little larger than they span; None or points in a line: none."""
        from pivy import coin
        if self._plane is not None:
            self.node.removeChild(self._plane)
            self._plane = None
        if not pts or len(pts) != 3:
            return
        a, b, c = pts
        n = (b - a).cross(c - a)
        if n.Length < 1e-9 * max(1.0, (b - a).Length * (c - a).Length):
            return
        n.normalize()
        u = b - a
        u.normalize()
        w = n.cross(u)
        centre = (a + b + c) * (1.0 / 3.0)
        span = max((p - centre).Length for p in pts) * 1.6 + 1.0
        corners = [centre + u * su * span + w * sw * span for su, sw in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        sep = coin.SoSeparator()
        mat = coin.SoMaterial()  # light enough to see on the dark background too (the user)
        mat.diffuseColor.setValue(0.55, 0.78, 1.0)
        mat.emissiveColor.setValue(0.25, 0.4, 0.6)
        mat.transparency.setValue(0.65)
        hints = coin.SoShapeHints()
        hints.vertexOrdering = coin.SoShapeHints.UNKNOWN_ORDERING  # both sides lit
        coords = coin.SoCoordinate3()
        coords.point.setValues(0, 4, [(p.x, p.y, p.z) for p in corners])
        face = coin.SoFaceSet()
        face.numVertices.setValue(4)
        edge = coin.SoSeparator()
        ec = coin.SoBaseColor()
        ec.rgb.setValue(0.6, 0.85, 1.0)
        width = coin.SoDrawStyle()
        width.lineWidth = 2
        line = coin.SoLineSet()
        lc = coin.SoCoordinate3()
        lc.point.setValues(0, 5, [(p.x, p.y, p.z) for p in corners + corners[:1]])
        for x in (ec, width, lc, line):
            edge.addChild(x)
        for x in (mat, hints, coords, face, edge):
            sep.addChild(x)
        self.node.insertChild(sep, 0)  # under the markers
        self._plane = sep

    def nearest(self, viewport, x, y, within_px=16):
        """Index of the marker under a viewport click (logical px, top-left origin), or None."""
        dpr = viewport.devicePixelRatioF()
        px, py = x * dpr, (viewport.height() - y) * dpr
        best, best_d = None, within_px * dpr
        for i, p in enumerate(self.points):
            try:
                sx, sy = self.view.getPointOnViewport(p)
            except Exception:
                continue
            d = ((sx - px) ** 2 + (sy - py) ** 2) ** 0.5
            if d < best_d:
                best, best_d = i, d
        return best


class _EscWhileComputing(QtCore.QObject):
    """Esc while a document recomputes (the user: Escape must cancel any operation): FreeCAD's progress bar stops the
    recompute between features when it gets the key — InCad's own Esc shortcut (a dialog open) took it first — and its
    'Abort the operation?' is answered Yes (Esc was the answer)."""

    def eventFilter(self, obj, event):
        t = event.type()
        if t in (QtCore.QEvent.ShortcutOverride, QtCore.QEvent.KeyPress) and event.key() == QtCore.Qt.Key_Escape \
                and _computing():
            if t == QtCore.QEvent.ShortcutOverride:
                event.accept()  # no shortcut: the key press goes on to FreeCAD's progress bar
                QtCore.QTimer.singleShot(0, _answer_abort)  # fires in its question's own loop (it takes the press)
        return False


def _computing():
    try:
        import FreeCAD as App
        return any(getattr(App.getDocument(n), "Recomputing", False) for n in App.listDocuments())
    except Exception:
        return False


def _answer_abort(tries=0):
    box = QtWidgets.QApplication.activeModalWidget()
    if isinstance(box, QtWidgets.QMessageBox) and "Abort" in box.text():
        yes = box.button(QtWidgets.QMessageBox.Yes)
        if yes is not None:
            yes.click()
            log.report("info", "Stopped (Esc): the features after this point are computed on the next recompute")
            return
    if tries < 10:
        QtCore.QTimer.singleShot(50, lambda: _answer_abort(tries + 1))


class _SelectionWatch:
    """Selection observer while InCad is active: the tree follows the model's selection (deferred, once per burst)."""

    def __init__(self, shell):
        self.shell = shell
        self._due = False

    def _check(self):
        if not self._due:
            self._due = True
            QtCore.QTimer.singleShot(0, self._fire)

    @perf.timed("selection changed")
    def _fire(self):
        self._due = False
        try:
            self.shell._on_model_selection_changed()
        except Exception:
            log.logger().exception("InCad selection sync")
        handles = getattr(self.shell, "dof_handles", None)
        linger = getattr(self.shell, "_dof_linger", None)
        if linger is not None and linger[2] is not None and time.monotonic() > linger[2] - self.shell.LINGER + 0.4:
            self.shell._dof_linger = None  # a click elsewhere (a new selection, or none): the dragged part's lingering
            self.shell.dof_display_changed()  # gizmo goes at once (not the drag's own release, just before)
        if handles is not None and handles.wants_selection():
            self.shell.dof_display_changed()  # the clicked part's DOF gizmo comes or goes (assemblies only)

    def addSelection(self, doc, obj, sub, pnt):
        self.shell._gizmo_seen = None  # every click on a part shows its gizmo again, for a couple of seconds
        self._check()

    def removeSelection(self, doc, obj, sub):
        self._check()

    def setSelection(self, doc):
        self._check()

    def clearSelection(self, doc):
        self._check()


class _RegionPicker:
    """Waits for a click on one of the shown areas of a sketch, then starts the feature on it."""

    def __init__(self, shell, kind, doc_name, body_name, source_name, names, faces):
        self.shell, self.kind = shell, kind
        self.doc_name, self.body_name, self.source_name, self.names, self.faces = doc_name, body_name, source_name, names, faces

    def addSelection(self, doc_name, obj_name, sub, pnt):
        if doc_name == self.doc_name and obj_name in self.names:
            index, shell = self.names.index(obj_name), self.shell
            QtCore.QTimer.singleShot(0, lambda: shell._region_pick is self and shell._region_picked(self, index))


class _ProfilePicker:
    """Waits for the user to pick a sketch with a closed profile, then starts the feature."""

    def __init__(self, shell, kind):
        self.shell, self.kind = shell, kind

    def addSelection(self, doc_name, obj_name, sub, pnt):
        import FreeCAD as App
        from incad.core import sketch
        from incad.features.common import closed_profile
        obj = App.getDocument(doc_name).getObject(obj_name)
        from incad.core import regions
        from incad.features import sketch_text
        if sketch_text.is_text(obj) or sketch.is_sketch(obj) and (closed_profile(obj) or regions.needs_regions(obj)):
            shell, kind = self.shell, self.kind
            QtCore.QTimer.singleShot(0, lambda: (shell._cancel_profile_pick(), shell.start_feature(kind)))


def _is_overlay(w):
    return isinstance(w, QtWidgets.QTabWidget) and w.objectName().startswith("Overlay")


def _overlays(mw):
    return [t for t in mw.findChildren(QtWidgets.QTabWidget) if t.parent() is mw and _is_overlay(t)]


class Shell(QtCore.QObject):
    @property
    def feature_session(self):
        """The open feature dialog's session — never a dead one (closed, or its dialog gone): that one is let go,
        so no command is refused for a dialog nobody can see."""
        fs = self.__dict__.get("_feature_session")
        if fs is not None and getattr(fs, "finishing", False):
            return fs  # OK pressed, its step being committed: still busy, not lost
        if fs is not None and not getattr(fs, "alive", True):
            self.__dict__["_feature_session"] = None
            log.logger().warning("A closed %s dialog was still held open: let go", fs.kind.TITLE)
            if not getattr(fs, "_closed", True):
                try:  # its window gone but never finished: Cancel rolls back its preview (the next dialog would
                    fs._finish(commit=False)  # commit that half-made step otherwise)
                except Exception:
                    log.logger().exception("cancel the lost %s dialog", fs.kind.TITLE)
            from incad.core import safe
            if safe.busy() == fs.kind.TITLE:
                safe.set_busy(None)
            self.schedule_refresh()
            return None
        return fs

    @feature_session.setter
    def feature_session(self, fs):
        self.__dict__["_feature_session"] = fs

    def __init__(self, mw, registry):
        super().__init__(mw)
        self.mw, self.registry = mw, registry
        self.active = False
        self.theme = "light"
        self.ribbon = self.title = self.browser = self.status = None
        self._snapshot = None
        self._hidden_by_us = set()
        self._widgets = []
        self._menus = []
        self._observer = None
        self._refresh_pending = False
        self._pending = False
        self.navigator = None
        self.strip = None
        self.sketches = SketchSession(on_change=self.schedule_refresh, on_edit=self._seed_geometry)
        self._box_owner = None
        self.feature_session = None
        self._overlay = None  # the open dialog's own view handle (Move Face's arrow): the navigator's handle hook
        self._gear_gizmo = None  # the gizmo on a gear selected in the part, no dialog open (ui/gear_gizmo)
        self.info_panel = None
        self.plane_panel = None
        self.slot_tool = None
        self.construction_mode = False   # InCad's own: FreeCAD's construction mode can't be read back
        self._construction_from = {}     # (doc, sketch) -> geometry count when construction mode was turned on
        self._profile_pick = None
        self.value_box = None
        self._shortcuts = {}
        self._geometry_counts = {}
        self._constraints_seen = {}  # (document, sketch) -> (constraint count, geometry count) at the last change
        self.section_panel = None
        self.constraints_panel = None  # the sketch's Constraint List window
        self._solid_edit = None  # (document, Solid) edited with the others see-through (Edit on a Solid)
        self.projecting = False  # Project Geometry started: a pick on another Solid is copied in
        self._solid_see = []  # [(root node, override material)] put on the other Solids meanwhile
        self.parameters_window = None  # the Parameters button's window (incad.ui.parameters_window)
        self._constraints_owner = None
        self._constraints_subs = []
        self._point_pick = None  # waiting for three clicks on the section markers
        self._peeked = None      # (document, object name, [(obj, was visible)]): shown while its tree row is selected
        self._syncing = False    # the tree is setting the model's selection: the watcher stays quiet
        self._area_warned = set()  # area copies already reported as lost (until their area is back)
        self._visibility_applied = set()  # document names whose saved visibility was put in step once
        self._adopt_tried = set()  # files made outside InCad already offered to become InCad parts
        self._projections_told = {}  # (doc, sketch) -> Refs of projected edges a used sketch was told to keep
        self._sel_watch = None
        self._section_display = None  # ([SectionDisplay], doc, view, [Caps], [(shape, colour)]) while sectioned
        self._section_base = None  # the picked section plane; the panel offsets/flips it
        self._section_sig = None  # what the open section was meshed from (_section_signature)
        self._section_last = None  # where it cuts now
        self._region_pick = None  # waiting for a click on one of a sketch's shown areas
        self._measure = None  # live Measure (a selection observer) while it is on
        self._dim_known = {}  # (doc, sketch) -> dimension snapshot after the last change (replacement tracking)
        self._box_pending = None  # (key, name): a new dimension whose box opens once the tool has placed it
        self._box_focus_hooked = False

        self._wait_notice = None
        self._dof_pending = False
        self.asm_drag = None
        self.dof_handles = None   # the selected part's DOF gizmo: grab an arc to turn it, an arrow to slide it
        self._axis_lines = None  # the components' axes in an assembly (incad.ui.axis_lines)
        self._dof_glyphs = None   # DofGlyphs on the active assembly view
        self._dof_on = set()      # assemblies whose View > Degrees of Freedom is on
        self._interference_on = set()  # assemblies whose Interference is on (incad.core.interference)
        self._interference = None      # (InterferenceDisplay, document name, signature drawn)
        self._interference_pending = False
        self._dof_hover = None    # (document name, part name) under the pointer (the tip)
        self._dof_linger = None   # (document name, part name, until) a part just dragged keeps its gizmo a while
        self._contexts = {}       # part document name -> AssemblyContext (Edit from an assembly: the parts around it)
        self.start_page = None    # the recent files, while no file is open (incad.ui.start_page)
        self._hover_watch = None
        self._polygons_from = {}
        self._rects_from = {}  # per sketch: where the geometry drawn since it was opened starts
        self._rects_done = {}  # per sketch: the rectangles given their centre point (geometry ids)
        self._rects_pending = []  # the sketch whose rectangle check waits for a quiet moment
        self.marking_menu = None
        self.menu_kind = None

    # ----- FreeCAD's own UI -----
    def _mdi(self):
        return self.mw.findChild(QtWidgets.QMdiArea)

    def _tune_tabs(self):
        """The file tabs (the user: an assembly's parts opened one by one): on the Mac Qt neither scrolls nor
        shortens them, so past the window's width they were out of reach (5 of 18 shown): arrows, long names cut
        with …, the full name in the tab's tooltip."""
        mdi = self._mdi()
        if mdi is None:
            return
        for bar in mdi.findChildren(QtWidgets.QTabBar):
            if not bar.usesScrollButtons():
                bar.setUsesScrollButtons(True)
            if bar.elideMode() != QtCore.Qt.ElideRight:
                bar.setElideMode(QtCore.Qt.ElideRight)
            for i in range(bar.count()):
                if bar.tabToolTip(i) != bar.tabText(i):
                    bar.setTabToolTip(i, bar.tabText(i))

    def _freecad_toolbars(self):
        return [t for t in self.mw.findChildren(QtWidgets.QToolBar)
                if t.parent() is self.mw and t.objectName() and t.objectName() not in OURS]

    def _freecad_docks(self):
        """FreeCAD's panels, and the overlay containers (OverlayLeft/Right/…) they float in when overlay mode is on
        — those showed FreeCAD's 'Tasks' strip over InCad's view."""
        docks = [d for d in self.mw.findChildren(QtWidgets.QDockWidget)
                 if d.objectName() and d.objectName() not in OURS]
        return docks + _overlays(self.mw)

    def _freecad_menu_actions(self):
        return [a for a in self.mw.menuBar().actions()
                if a.text() and not (a.menu() is not None and a.menu().objectName().startswith(MENU_PREFIX))]

    def _capture(self):
        from incad.core import params
        new = _std_new_actions()
        mdi = self._mdi()
        return {
            "toolbars": {t.objectName(): not t.isHidden() for t in self._freecad_toolbars()},
            "docks": {d.objectName(): not d.isHidden() for d in self._freecad_docks()},
            "menus": {a.text(): a.isVisible() for a in self._freecad_menu_actions()},
            "statusbar": not self.mw.statusBar().isHidden(),
            "view": params.snapshot(themes.VIEW_GROUP, themes.VIEW_KEYS),
            "toolbar_params": params.snapshot_group(TOOLBAR_GROUP),
            "nav": params.snapshot(themes.VIEW_GROUP, NAV_KEYS),
            "navicube": params.snapshot(themes.NAVICUBE_GROUP, themes.NAVICUBE_KEYS),
            "sketch": {g: params.snapshot(g, keys) for g, keys in themes.SKETCH_KEYS.items()},
            "std_new": new[0].shortcut().toString() if new else "",
            "std_shortcuts": {c: (_std_actions(c)[0].shortcut().toString() if _std_actions(c) else "")
                              for c in TAKEN_SHORTCUTS},
            "mdi": [mdi.viewMode().name, mdi.tabPosition().name] if mdi is not None else None,
        }

    def _hide_freecad(self):
        for w in self._freecad_toolbars() + self._freecad_docks():
            if not w.isHidden():
                self._hidden_by_us.add(w.objectName())
            w.hide()
        for a in self._freecad_menu_actions():
            a.setVisible(False)
        self.mw.statusBar().hide()
        for name in TAKEN_SHORTCUTS:
            for a in _std_actions(name):
                a.setShortcut(QtGui.QKeySequence())
        # macOS wires the app menu's Quit to the first QuitRole action: FreeCAD's Exit sits in its hidden File menu,
        # where a click on that Quit never reaches Qt (probed with a real menu click). InCad's takes the slot.
        for a in _std_actions("Std_Quit"):
            a.setMenuRole(QAction.NoRole)
        mdi = self._mdi()
        QtCore.QTimer.singleShot(0, self._front_last_view)
        if mdi is not None:
            mdi.setViewMode(QtWidgets.QMdiArea.TabbedView)
            mdi.setTabPosition(QtWidgets.QTabWidget.South)

    def _restore_widgets(self, widgets, saved):
        """Only re-show what InCad hid; FreeCAD itself decides what each workbench keeps hidden."""
        for w in widgets:
            name = w.objectName()
            if saved.get(name) or name in self._hidden_by_us:
                w.show()

    @staticmethod
    def _restore_shortcuts(snap):
        saved = snap.get("std_shortcuts") or {"Std_New": snap.get("std_new") or "Ctrl+N"}  # older snapshots
        for name, seq in saved.items():
            for a in _std_actions(name):
                a.setShortcut(QtGui.QKeySequence(seq))
        for a in _std_actions("Std_Quit"):
            a.setMenuRole(QAction.QuitRole)

    def _on_subwindow(self, window):
        if self.dof_handles is not None and self.dof_handles.dragging():
            self.dof_handles.finish()  # another tab: a handle drag going on ends (its release goes elsewhere)
        if self._overlay is not None and self._overlay.dragging():
            self._overlay.finish()
        if window is not None and _is_start_page(window):  # FreeCAD's Start page came up (seen after Finish
            QtCore.QTimer.singleShot(0, self._front_last_view)  # Sketch): the part stays in front

    def _front_last_view(self):
        """FreeCAD's Start page never stays in front in InCad: back to the view shown before it. It is not closed
        or hidden: closing it crashes FreeCAD while its setup timer is pending, hiding it breaks the tabbed layout
        (both probed). No Python handle on these windows is kept (handles left at exit crashed FreeCAD)."""
        mdi = self._mdi()
        if mdi is None or not self.active:
            return
        try:
            active = mdi.activeSubWindow()
            if active is None or not _is_start_page(active):
                return
            history = [w for w in mdi.subWindowList(QtWidgets.QMdiArea.ActivationHistoryOrder)
                       if not _is_start_page(w)]
            if history:
                mdi.setActiveSubWindow(history[-1])
        except RuntimeError:
            pass

    def _restore_mdi(self, saved):
        mdi = self._mdi()
        if mdi is not None and saved:
            mdi.setViewMode(getattr(QtWidgets.QMdiArea.ViewMode, saved[0]))
            mdi.setTabPosition(getattr(QtWidgets.QTabWidget.TabPosition, saved[1]))

    def _restore(self, snap):
        from incad.core import params
        steps = [
            ("toolbars", lambda: self._restore_widgets(self._freecad_toolbars(), snap.get("toolbars", {}))),
            ("panels", lambda: self._restore_widgets(self._freecad_docks(), snap.get("docks", {}))),
            ("menus", lambda: [a.setVisible(snap.get("menus", {}).get(a.text(), True))
                               for a in self._freecad_menu_actions()]),
            ("status bar", lambda: self.mw.statusBar().setVisible(snap.get("statusbar", True))),
            # FreeCAD saves toolbar visibility (hidden by us) before calling Deactivated and re-applies it a moment
            # later, so its saved values must be put back too
            ("toolbar settings", lambda: params.restore(TOOLBAR_GROUP, snap.get("toolbar_params", {}))),
            ("3D view colors", lambda: params.restore(themes.VIEW_GROUP, snap["view"]) if "view" in snap else None),
            ("navigation", lambda: params.restore(themes.VIEW_GROUP, snap["nav"]) if "nav" in snap else None),
            ("ViewCube colors", lambda: params.restore(themes.NAVICUBE_GROUP, snap["navicube"])
             if "navicube" in snap else None),
            ("Sketcher settings", lambda: [params.restore(g, v) for g, v in snap.get("sketch", {}).items()]),
            ("shortcuts", lambda: self._restore_shortcuts(snap)),
            ("document tabs", lambda: self._restore_mdi(snap.get("mdi"))),
        ]
        for name, step in steps:
            try:
                step()
            except Exception as e:
                log.report("warning", "Could not restore FreeCAD's %s: %s" % (name, e), traceback.format_exc())
        self._hidden_by_us = set()

    # ----- activate / deactivate -----
    @staticmethod
    def _edit_open():
        """An edit InCad cannot take over: anything but a sketch in an InCad part (InCad's sketch mode handles those)."""
        import FreeCADGui as Gui
        from incad.core import document, safe, sketch
        gdoc = Gui.ActiveDocument
        vp = gdoc.getInEdit() if gdoc is not None else None
        obj = getattr(vp, "Object", None)
        if sketch.is_sketch(obj) and document.is_incad_part(obj.Document):
            return False
        return (vp is not None or safe.edit_open()) and not safe.drag_edit()

    @staticmethod
    def _finish_freecad_sketch():
        """A FreeCAD sketch being edited is simply closed (its changes are already in the model), so switching to
        InCad from Sketcher or Part Design takes over at once."""
        import FreeCADGui as Gui
        from incad.core import document, sketch
        gdoc = Gui.ActiveDocument
        vp = gdoc.getInEdit() if gdoc is not None else None
        obj = getattr(vp, "Object", None)
        if sketch.is_sketch(obj) and not document.is_incad_part(obj.Document):
            from incad.ui.sketch_session import close_edit
            close_edit(gdoc)

    def _show_wait_notice(self):
        """Other FreeCAD tasks keep their OK/Cancel: say plainly why InCad's interface isn't there yet."""
        if self._wait_notice is None:
            box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Information, host.app_name(),
                                        host.brand("Finish or cancel the current FreeCAD task (OK or Cancel in its "
                                                   "panel).\nInCad opens as soon as it is closed."),
                                        QtWidgets.QMessageBox.Ok, self.mw)
            box.setObjectName("InCadWaitNotice")
            box.setWindowModality(QtCore.Qt.NonModal)
            self._wait_notice = box
        self._wait_notice.show()
        self._wait_notice.raise_()

    def _close_wait_notice(self):
        if self._wait_notice is not None:
            self._wait_notice.hide()
            dispose(self._wait_notice)
            self._wait_notice = None

    def _activate_when_edit_done(self):
        import FreeCADGui as Gui
        if not self._pending:
            return
        if self._edit_open():
            QtCore.QTimer.singleShot(500, self._activate_when_edit_done)
            return
        self._pending = False
        self._close_wait_notice()
        wb = Gui.activeWorkbench()
        if wb is None or wb.name() != "InCadWorkbench":
            # closing a Part Design task switches back to Part Design by itself; the user asked for InCad
            Gui.activateWorkbench("InCadWorkbench")
        else:
            self.activate()

    def activate(self):
        if self.active:
            return True
        self._finish_freecad_sketch()
        if self._edit_open():  # its OK/Cancel and its open transaction belong to FreeCAD: wait until it is finished
            if not self._pending:
                self._pending = True
                self._show_wait_notice()
                QtCore.QTimer.singleShot(500, self._activate_when_edit_done)
            return False
        self._close_wait_notice()
        stale = prefs.takeover_snapshot()
        try:
            self._snapshot = json.loads(stale) if stale else self._capture()
        except ValueError:
            self._snapshot = self._capture()
        prefs.set_takeover_snapshot(json.dumps(self._snapshot))
        try:
            self._hide_freecad()
            self._build()
            self._install_hooks()
            self.apply_theme()
            self.active = True
            self.refresh()
            try:
                from incad.ui import update_check
                update_check.at_start(self.mw, busy=lambda: self.feature_session is not None)
            except Exception:
                log.logger().info("Update check at start not set up", exc_info=True)
        except Exception as e:
            log.report("error", "InCad could not start its interface: %s" % e, traceback.format_exc())
            self.active = False
            self._teardown()
            self._restore(self._snapshot)
            prefs.clear_takeover_snapshot()
            self._snapshot = None
            return False
        return True

    def deactivate(self):
        if self._pending and self._edit_open():  # the user left InCad while the FreeCAD task is still open
            self._pending = False
            self._close_wait_notice()
        # (a task that just closed switches to its own workbench by itself: keep waiting, InCad comes back)
        if not self.active:
            return
        self.active = False
        self._teardown()
        snap = self._snapshot
        if snap is None:
            text = prefs.takeover_snapshot()
            snap = json.loads(text) if text else None
        if snap is not None:
            self._restore(snap)
        prefs.clear_takeover_snapshot()
        self._snapshot = None

    def _reactivate_if_still_open(self):
        import FreeCADGui as Gui
        if QtWidgets.QApplication.activeModalWidget() is not None:  # e.g. "Save changes?" while quitting
            QtCore.QTimer.singleShot(300, self._reactivate_if_still_open)
            return
        wb = Gui.activeWorkbench()
        if self.mw.isVisible() and wb is not None and wb.name() == "InCadWorkbench":
            self.activate()

    # ----- InCad's UI -----
    @staticmethod
    def _setup_bar(bar):
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.toggleViewAction().setVisible(False)

    def _build(self):
        mw = self.mw
        self.theme = themes.current()
        top = QtWidgets.QToolBar(host.app_name(), mw)
        top.setObjectName("InCadTop")
        self._setup_bar(top)
        self._widgets.append(top)
        box = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.title = TitleStrip(self.registry, self.theme, self._settings_menu)
        self.ribbon = Ribbon(self.registry, ribbon_layout.LAYOUT, mode="empty", size=prefs.ribbon_size(),
                             theme=self.theme)
        self.ribbon.sizeChanged.connect(prefs.set_ribbon_size)
        col.addWidget(self.title)
        col.addWidget(self.ribbon)
        box.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        top.addWidget(box)
        mw.addToolBar(QtCore.Qt.TopToolBarArea, top)
        dock = QtWidgets.QDockWidget("Model", mw)
        dock.setObjectName("InCadBrowserDock")
        dock.setFeatures(QtWidgets.QDockWidget.NoDockWidgetFeatures)
        dock.setTitleBarWidget(QtWidgets.QWidget())
        dock.setMinimumWidth(230)
        self._widgets.append(dock)
        self.browser = ModelBrowser(self.theme)
        dock.setWidget(self.browser)
        mw.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock)
        bottom = QtWidgets.QToolBar(host.app_name() + " Status", mw)
        bottom.setObjectName("InCadStatusBar")
        self._setup_bar(bottom)
        self._widgets.append(bottom)
        self.status = StatusBar(self.theme)
        self.status.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        bottom.addWidget(self.status)
        mw.addToolBar(QtCore.Qt.BottomToolBarArea, bottom)
        self.browser.featureActivated.connect(self._on_feature_activated)
        self.browser.endOfPartMoved.connect(self._on_end_of_part_moved)
        self.browser.actionTriggered.connect(self._on_browser_action)
        self.browser.parameterSet.connect(self._on_parameter_set)
        self.browser.parameterRemoved.connect(self._on_parameter_removed)
        self.navigator = Navigator(self._open_marking_menu, self._on_nav_mode_changed, self)
        self.browser.markingMenuRequested.connect(self._on_browser_marking_menu)
        self.browser.featureSelected.connect(self._on_browser_select)
        mdi = self._mdi()
        if mdi is not None:
            self.strip = NavStrip(self.registry, self.theme, mdi.viewport())
            self.strip.place(mdi.viewport().rect())
            self.strip.show()
            self.plane_panel = PlanePanel(self.theme, mdi.viewport())
            self.plane_panel.picked.connect(self._on_plane_picked)
            self.plane_panel.cancelled.connect(self.sketches.cancel_pick)
            self.plane_panel.hide()
        self.browser.parameterRenamed.connect(self._on_parameter_renamed)
        self.browser.parameterClicked.connect(self._on_parameter_clicked)
        self.status.dofClicked.connect(lambda: self.registry.run("sketch.show_dof"))
        self.navigator.click_hook = self._click_hook
        self.navigator.move_hook = self._move_hook
        self.navigator.dblclick_hook = self._dblclick_hook
        from incad.ui.asm_drag import AssemblyDrag
        self.asm_drag = AssemblyDrag(self)
        self.navigator.drag_hook = self.asm_drag
        from incad.ui.dof_handles import Handles
        self.dof_handles = Handles(self)
        self.navigator.handle_hook = self.dof_handles
        from incad.ui.dimension_place import DeadZone
        self.navigator.place_hook = DeadZone(self.sketches.editing)
        self._build_shortcuts()

    def _build_menus(self):
        bar = self.mw.menuBar()
        for title, ids in MENUS:
            menu = QtWidgets.QMenu(title, bar)
            menu.setObjectName(MENU_PREFIX + title)
            for cmd_id in ids:
                if cmd_id is None:
                    menu.addSeparator()
                    continue
                if not self.registry.has(cmd_id):
                    continue
                cmd = self.registry.get(cmd_id)
                act = menu.addAction(themes.icon(cmd.icon, self.theme), cmd.label)
                if cmd.checkable:
                    act.setCheckable(True)
                    menu.aboutToShow.connect(lambda a=act, c=cmd: a.setChecked(bool(c.is_checked and c.is_checked())))
                if cmd.shortcut:
                    act.setShortcut(QtGui.QKeySequence(cmd.shortcut))
                act.triggered.connect(lambda _=False, i=cmd_id: self.registry.run(i))
                if cmd_id == "file.quit":
                    # ours stays a normal File item that owns ⌘Q; a second one, without a shortcut, becomes the app
                    # menu's Quit (macOS moves the QuitRole action there)
                    act.setMenuRole(QAction.NoRole)
                    app_quit = menu.addAction(cmd.label)
                    app_quit.setObjectName("InCadAppQuit")
                    app_quit.setMenuRole(QAction.QuitRole)
                    app_quit.triggered.connect(lambda _=False: self.registry.run("file.quit"))
            if title == "View":
                menu.addMenu(self.ribbon.size_menu(menu))
            bar.addAction(menu.menuAction())
            self._menus.append(menu)

    def _remove_menus(self):
        for menu in self._menus:
            self.mw.menuBar().removeAction(menu.menuAction())
            dispose(menu)
        self._menus = []

    def _teardown(self):
        self._drop_gear_gizmo()
        if self._sel_watch is not None:
            try:
                import FreeCADGui as Gui
                Gui.Selection.removeObserver(self._sel_watch)
            except Exception:
                pass
            self._sel_watch = None
        if self._hover_watch is not None:
            try:
                import FreeCADGui as Gui
                Gui.Selection.removeObserver(self._hover_watch)
            except Exception:
                pass
            self._hover_watch = None
        if self._dof_glyphs is not None:
            self._dof_glyphs.remove()
            self._dof_glyphs = None
        if getattr(self, "_ground_marks", None) is not None:
            self._ground_marks.remove()
            self._ground_marks = None
        if self._axis_lines is not None:
            self._axis_lines.remove()
            self._axis_lines = None
        for ctx in self._contexts.values():
            ctx.remove()
        self._contexts = {}
        if self.start_page is not None:
            try:
                self.start_page.hide()
                self.start_page.deleteLater()
            except RuntimeError:
                pass
            self.start_page = None
        self._peeked = None
        if self.asm_drag is not None and self.asm_drag.active():
            self.asm_drag.finish(moved_ok=False)
        if self.dof_handles is not None:
            self.dof_handles.finish(moved_ok=False)
            self.dof_handles.clear_drawn()
        self.stop_slot_tool()
        self._stop_measuring()
        self.close_constraints_window()
        self.close_parameters_window()
        self.stop_section(keep_cut=False)
        self._cancel_point_pick()
        self._cancel_region_pick()
        self._close_feature_session(cancel=True)
        self._close_info_panel()
        self._cancel_profile_pick()
        self.sketches.close_all()
        self._close_value_box()
        for act in self._shortcuts.values():
            try:
                self.mw.removeAction(act)
                dispose(act)
            except RuntimeError:
                pass
        self._shortcuts = {}
        self._remove_hooks()
        self._remove_menus()
        for w in self._widgets:
            try:
                if isinstance(w, QtWidgets.QToolBar):
                    self.mw.removeToolBar(w)
                else:
                    self.mw.removeDockWidget(w)
                dispose(w)
            except RuntimeError:
                pass
        if self.marking_menu is not None:
            try:
                self.marking_menu.close()
            except RuntimeError:
                pass
            self.marking_menu = None
        if self.navigator is not None:
            self.navigator.detach_all()
            self.navigator = None
        if self.strip is not None:
            try:
                dispose(self.strip)
            except RuntimeError:
                pass
            self.strip = None
        if self.plane_panel is not None:
            try:
                dispose(self.plane_panel)
            except RuntimeError:
                pass
            self.plane_panel = None
        self._widgets = []
        self.ribbon = self.title = self.browser = self.status = None

    def _settings_menu(self, parent):
        menu = QtWidgets.QMenu(parent)
        themes_menu = menu.addMenu("Theme")
        for value, label in (("auto", "Match " + host.OS_NAME), ("light", "Light"), ("dark", "Dark")):
            act = themes_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(prefs.theme() == value)
            act.triggered.connect(lambda _=False, v=value: self._set_theme_pref(v))
        menu.addMenu(self.ribbon.size_menu(menu))
        units = menu.addMenu("Units for New Files")
        current = prefs.default_units() if prefs.remember_new_choice() else "ask"
        for value, label in (("ask", "Ask every time"), ("mm", "Always millimeters"), ("in", "Always inches")):
            act = units.addAction(label)
            act.setCheckable(True)
            act.setChecked(current == value)
            act.triggered.connect(lambda _=False, v=value: self._set_new_units(v))
        dims = menu.addMenu("Dimension Display")
        for value, label in (("value", "Value (40)"), ("name", "Name (d3)"), ("expression", "Expression (d3 = 40)")):
            act = dims.addAction(label)
            act.setCheckable(True)
            act.setChecked(prefs.dimension_display() == value)
            act.triggered.connect(lambda _=False, v=value: (prefs.set_dimension_display(v), self.apply_theme()))
        quality_menu = menu.addMenu("Display Quality")
        for value, label in (("normal", "Normal (faster)"), ("fine", "Fine (smoother curves)")):
            act = quality_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(prefs.display_quality() == value)
            act.triggered.connect(lambda _=False, v=value: self.set_display_quality(v))
        diag_act = menu.addAction("Rectangle Diagonals")
        diag_act.setCheckable(True)
        diag_act.setChecked(prefs.rectangle_diagonals())
        diag_act.setToolTip("New rectangles keep their two diagonals as construction lines")
        diag_act.triggered.connect(lambda checked: prefs.set_rectangle_diagonals(checked))
        menu.addAction("Show Log File").triggered.connect(lambda _=False: self._show_log())
        menu.addAction("Report a Bug…").triggered.connect(lambda _=False: self.report_bug())
        menu.addAction("Check for Updates…").triggered.connect(lambda _=False: self.check_updates())
        upd_act = menu.addAction("Check for Updates at Start")
        upd_act.setCheckable(True)
        upd_act.setChecked(prefs.update_check_at_start())
        upd_act.triggered.connect(lambda checked: prefs.set_update_check_at_start(checked))
        nav_act = menu.addAction("Mouse Navigation (LMB+RMB orbit, right-click menu)")
        nav_act.setCheckable(True)
        nav_act.setChecked(prefs.navigation_enabled())
        nav_act.triggered.connect(lambda checked: self.set_navigation(checked))
        return menu

    @staticmethod
    def set_display_quality(value):
        from incad.core import quality
        prefs.set_display_quality(value)
        quality.set_defaults()
        quality.apply_all(force=True)
        log.report("info", "Display quality: %s" % value.capitalize())

    @staticmethod
    def _show_log():
        """⚙ ▸ Show Log File: the file to send with a problem report, selected in Explorer / Finder (on Windows it sits
        in the hidden AppData folder, which Windows search does not look into)."""
        path = log.log_path()
        try:
            log.logger().info("Log shown: %s", path)  # the file exists (and has today's line) before it is shown
            host.reveal(path)
            log.report("info", "InCad's log: %s" % path)
        except Exception as e:
            log.report("warning", "Could not show the log (%s): %s" % (path, e))

    REPORT_PAUSE = 60  # s between two reports sent from here (a stuck button never floods the Issues)

    def check_updates(self):
        """⚙ ▸ Check for Updates… (incad.ui.update_check): the answer even when there is nothing new."""
        from incad.ui import update_check
        return update_check.check(self.mw, busy=lambda: self.feature_session is not None)

    def report_bug(self, exec_dialog=True):
        """⚙ ▸ Report a Bug: the report straight into the Issues on GitHub (incad.core.bugreport); a build without the
        sending key opens GitHub's filled-in page in the browser instead. Returns the dialog (tests: not run)."""
        from incad.core import bugreport
        from incad.ui.bugreport_dialog import BugReportDialog
        direct = bugreport.key() is not None

        def send(what, steps, contact, attach):
            try:
                title, body = bugreport.compose(what, steps, contact, attach)
            except ValueError as e:
                return False, str(e), None
            if not direct:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl(bugreport.browser_url(title, body)))
                return True, "GitHub's page is open in your browser: press Submit new issue there", None
            last = getattr(self, "_report_sent_at", 0.0)
            if time.time() - last < self.REPORT_PAUSE:
                return False, "One report a minute: wait a moment and Send again", None
            try:
                url = bugreport.send(title, body)
            except ValueError as e:
                log.logger().warning("Bug report not sent: %s", e)
                return False, str(e), None
            self._report_sent_at = time.time()
            log.logger().info("Bug report sent: %s", url)
            return True, "Sent, thank you", url
        dlg = BugReportDialog("%s %s" % bugreport.product(), send, self.theme, self.mw, direct)
        if exec_dialog:
            dlg.exec()
        return dlg

    def _set_theme_pref(self, value):
        prefs.set_theme(value)
        self.apply_theme()

    @staticmethod
    def _set_new_units(value):
        if value == "ask":
            prefs.set_remember_new_choice(False)
        else:
            prefs.set_default_units(value)
            prefs.set_remember_new_choice(True)

    def apply_theme(self):
        from incad.core import params
        if self.ribbon is None:
            return
        self.theme = themes.current()
        self._close_info_panel()  # it was drawn for the old theme
        self._drop_sketch_markers()  # the next refresh draws them in the new colours
        if self.plane_panel is not None:
            self.plane_panel.set_theme(self.theme)
        try:
            import FreeCAD as App
            from incad.ui import wire_style
            wire_style.refresh(App.ActiveDocument, self.theme)  # Wireframe lines in the new theme's color
        except Exception as e:
            log.logger().warning("Wireframe lines: %s", e)
        if self.constraints_panel is not None:
            self.constraints_panel.set_theme(self.theme)
        if self.parameters_window is not None:
            self.parameters_window.set_theme(self.theme)
        qss = themes.stylesheet(self.theme)
        for w in self._widgets:
            w.setStyleSheet(qss)
        self.ribbon.set_theme(self.theme)
        self.title.set_theme(self.theme)
        self.browser.set_theme(self.theme)
        self.status.set_theme(self.theme)
        self._remove_menus()
        self._build_menus()
        if self.strip is not None:
            self.strip.set_theme(self.theme)
        params.write(themes.NAVICUBE_GROUP, themes.navicube_params(self.theme))
        for group, values in themes.sketch_params(self.theme, prefs.dimension_display()).items():
            params.write(group, values)
        params.write(themes.VIEW_GROUP, themes.view_params(self.theme))
        for ctx in self._contexts.values():
            ctx.set_theme(self.theme)  # the assembly around an edited part in the new grey
        self.dof_display_changed()  # DOF glyphs in the new theme's colours
        if self._overlay is not None:
            try:
                self._overlay.refresh()  # Move Face's arrow too
            except Exception:
                log.logger().exception("InCad overlay")

    def set_navigation(self, enabled):
        """Off gives FreeCAD's own navigation back exactly (the user's style and zoom setting)."""
        from incad.core import params
        prefs.set_navigation_enabled(enabled)
        if self.navigator is None:
            return
        if enabled:
            params.write(themes.VIEW_GROUP, NAV_VALUES)
            self.navigator.attach_all()
        else:
            self.navigator.set_mode(None)
            self.navigator.detach_all()
            snap = self._snapshot or json.loads(prefs.takeover_snapshot() or "{}")
            if "nav" in snap:
                params.restore(themes.VIEW_GROUP, snap["nav"])

    def _raise_strip(self, *_):
        """QMdiArea raises a sub-window when it is added or activated, over InCad's panels in the view area: keep
        them all on top (a buried feature dialog stayed open unseen, and every other command was refused)."""
        QtCore.QTimer.singleShot(0, self._raise_overlays)

    def _dialog_first(self):
        """The error for a command refused while a feature dialog is open, and that dialog brought to the front."""
        self._show_open_dialog()
        return ValueError("Finish the %s dialog first" % self.feature_session.kind.TITLE)

    def _show_open_dialog(self):
        fs = self.feature_session
        if fs is None and self._edit_open():
            self._show_wait_notice()
            return
        if fs is not None and fs.dialog is not None:
            try:
                if not fs.dialog.isVisible():
                    fs.dialog.show()
                fs.dialog.raise_()  # raise only: show() on a visible dialog moves the keyboard focus
            except RuntimeError:
                pass

    def _raise_overlays(self):
        fs = self.feature_session
        for w in (self.strip, self.plane_panel, self.info_panel, self.value_box, self.section_panel, self.constraints_panel,
                  fs.dialog if fs is not None else None):
            try:
                if w is not None and w.isVisible():
                    w.raise_()
            except RuntimeError:
                pass

    def _on_nav_mode_changed(self, mode):
        if self.strip is not None:
            self.strip.refresh_checks()
        if self.status is not None:
            hints = {"orbit": "Orbit: drag with the left button (click Orbit again to stop)",
                     "pan": "Pan: drag with the left button (click Pan again to stop)"}
            self.status.set_prompt(hints.get(mode, "Ready"))

    def _show_marking_menu(self, kind, global_pos, flick=False, extras=(), top=None, drop=()):
        """top: a command that takes the N slot (the open dialog's finish: Move Face's Finish move); drop: list
        entries that don't apply here."""
        slots, items = marking_layout.resolve(kind, self.registry)
        drop = set(drop)
        if kind != "sketch":  # the sketch menu has its own (_sketch_menu)
            try:
                import FreeCAD as App
                from incad.ui import menu_rules
                drop |= menu_rules.inapplicable(kind, App.ActiveDocument)
            except Exception:
                log.logger().exception("Right-click menu rules")
        items = [c for c in items if c.id not in drop]
        if kind in ("part", "face", "edge", "vertex") and self.registry.has("assembly.return"):
            try:  # a part opened with Edit from its assembly: Return first in every right-click menu (the user went
                # back to the 3D Model tab for it every time)
                if self._active_context() is not None:
                    items = [self.registry.get("assembly.return")] + [c for c in items if c.id != "assembly.return"]
            except Exception:
                log.logger().exception("Return in the right-click menu")
        if top and self.registry.has(top):
            slots = [("N", self.registry.get(top))] + [(d, c) for d, c in slots if d != "N"]
        if not slots and not items and not extras:
            return None
        if self.marking_menu is not None:
            try:
                self.marking_menu.close()
            except RuntimeError:
                pass
        menu = MarkingMenu(self.registry, slots, items, self.theme, self.mw, extras=extras)
        menu.destroyed.connect(lambda _=None: setattr(self, "marking_menu", None))
        self.marking_menu, self.menu_kind = menu, kind
        menu.show_at(global_pos)
        if flick:
            menu.start_flick()
        return menu

    def _open_marking_menu(self, view, viewport, local, global_pos, flick):
        import FreeCAD as App
        import FreeCADGui as Gui
        from incad.core import context, document
        if self.sketches.editing() is not None:
            return self._sketch_menu(global_pos, flick)
        doc = App.ActiveDocument
        info = view.getObjectInfo(to_coin(viewport, local.x(), local.y())) if doc is not None else None
        context.set_target(None)
        if info:
            obj = doc.getObject(info["Object"]) if info.get("Document") == doc.Name else None
            datum = obj is not None and (obj.TypeId in document.ORIGIN_TYPES or obj.TypeId in document.DATUM_TYPES)
            if obj is not None and (datum or document.is_sketch_like(obj)):
                # a shown plane, axis or point (origin or work feature), or a shown sketch: the object's own menu
                # (as from its tree row); the tree's old selection steps aside so Hide / Show acts on what is
                # under the cursor
                context.set_target(obj.Name)
                self.browser.tree.clearSelection()
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(doc.Name, obj.Name)
                kind = "origin" if obj.TypeId in document.ORIGIN_TYPES else "browser"  # a work plane: Edit too
                return self._show_marking_menu(kind, global_pos, flick, top=self._session_finish())
            comp = info.get("Component", "")
            kind = "edge" if comp.startswith("Edge") else "vertex" if comp.startswith("Vertex") else "face"
            Gui.Selection.clearSelection()
            parent = info.get("ParentObject")
            if info.get("SubName") and getattr(parent, "Document", None) is doc:
                # a part of an assembly: picked as the assembly's component ('Assembly' + 'Cover.Hole4.Face4'), never
                # as the object in the part's own file — that made the part's file the active one, opened its tab and
                # Hide hid it there (the user: it opened in a new window and vanished, with no way back)
                Gui.Selection.addSelection(doc.Name, parent.Name, info["SubName"])
            else:
                Gui.Selection.addSelection(info["Document"], info["Object"], comp)
        else:
            kind = "part" if document.is_incad_part(doc) else "empty"
        from incad.core import assembly
        if assembly.is_assembly(doc):
            kind = {"face": "asm_face", "edge": "asm_edge", "vertex": "asm_edge"}.get(kind, "assembly")
        return self._show_marking_menu(kind, global_pos, flick, top=self._session_finish())

    def _session_finish(self):
        """The open dialog's own finish command for the top of the view's menu (Move Face: Finish move = OK)."""
        fs = self.feature_session
        return getattr(fs.kind, "FINISH", None) if fs is not None else None

    def finish_dialog(self):
        """Finish move (and any dialog's finish command): the same as the dialog's OK."""
        if self.feature_session is not None:
            self._close_feature_session(cancel=False)

    def _on_browser_select(self, name):
        """A click in the model tree selects the object in the model: the view highlights it, a dialog waiting
        for a sketch or a feature takes it (Inventor picks from the browser). A hidden plane, axis, point or
        sketch is shown while its row is selected; the next selection, Esc or a cleared selection hides it again."""
        import FreeCAD as App
        import FreeCADGui as Gui
        doc = App.ActiveDocument
        obj = doc.getObject(name) if doc is not None else None
        if obj is None:
            return
        if self._peeked is not None and self._peeked[1] != name:
            self._unpeek()
        if self.sketches.editing() is None:
            self._peek(obj)
        names = self.browser.selected_names() if self.browser is not None else []
        if name not in names:
            names = [name]  # a plain click: the clicked row alone (Shift/Cmd-clicks keep the others)
        fs = self.feature_session
        picking = fs is not None and fs.dialog is not None and fs.dialog.active_pick() is not None
        self._syncing = True
        try:
            Gui.Selection.clearSelection()
            for n in names:
                o = doc.getObject(n)
                if o is None:
                    continue
                faces = [] if picking else self._faces_of(o)  # a dialog waiting for a pick takes the feature
                if faces:
                    Gui.Selection.addSelection(doc.getObject(faces[0]), faces[1])  # Inventor: its faces light up
                else:
                    Gui.Selection.addSelection(doc.Name, n)
            from incad.features import common as _common
            self._tree_picked = self._selection_key(_common.selection_ex(doc.Name))
        finally:
            self._syncing = False

    @staticmethod
    def _selection_key(sels):
        return tuple(sorted((s.ObjectName, tuple(sorted(s.SubElementNames))) for s in sels))

    def show_in_browser(self):
        """Inventor's Find in Browser: the tree opens to the rows of what is selected in the view (a face: the
        feature that made it) and scrolls to them."""
        import FreeCAD as App
        import FreeCADGui as Gui
        doc = App.ActiveDocument
        if doc is None or not Gui.Selection.getSelectionEx(doc.Name):
            raise ValueError("Select something in the view first")
        was, self._syncing = self._syncing, False
        try:
            self._on_model_selection_changed()
        finally:
            self._syncing = was

    SOLID_SEE = 0.8  # the other Solids while one is edited

    def edit_solid(self, name):
        """Edit on a Solid (the user: the lid of a jar in the same part): the other Solids turn see-through, an
        override on their scene (nothing saved, nothing to undo), until Esc or Edit on it again; this Solid is
        worked on as usual — a sketch on its face, its features' dialogs."""
        import FreeCAD as App
        doc = App.ActiveDocument
        body = doc.getObject(name) if doc is not None else None
        if body is None or body.TypeId != "PartDesign::Body":
            raise ValueError("Right-click a Solid in the Model Browser")
        if self._solid_edit == (doc.Name, body.Name):
            self.end_solid_edit()
            return
        self.end_solid_edit(quiet=True)
        from pivy import coin
        for other in doc.findObjects("PartDesign::Body"):
            tip = other.Tip
            if other is body or tip is None or not tip.Visibility:
                continue
            try:
                mat = coin.SoMaterial()
                vo = tip.ViewObject
                c = tuple(vo.ShapeAppearance[0].DiffuseColor[:3]) if hasattr(vo, "ShapeAppearance") \
                    else tuple(vo.ShapeColor[:3])
                mat.diffuseColor.setValue(*c)
                mat.transparency.setValue(self.SOLID_SEE)
                mat.setOverride(True)
                root = vo.RootNode
                root.insertChild(mat, 0)
                self._solid_see.append((root, mat))
            except Exception:
                log.logger().exception("see-through %s", other.Name)
        self._solid_edit = (doc.Name, body.Name)
        log.report("info", "Editing %s: the other Solids are see-through (Esc, or Edit on it again, ends)"
                   % body.Label)
        self.schedule_refresh()

    def end_solid_edit(self, quiet=False):
        see, self._solid_see = self._solid_see, []
        for root, mat in see:
            try:
                root.removeChild(mat)
            except Exception:
                pass
        was, self._solid_edit = self._solid_edit, None
        if was is not None and not quiet:
            log.report("info", "Done editing the Solid: all Solids shown as before")
            self.schedule_refresh()

    def _projection_running(self):
        """Project Geometry still runs: its own cursor, or the 'not allowed' one it shows over another Solid."""
        mdi = self._mdi()
        views = [w for w in mdi.findChildren(QtWidgets.QGraphicsView) if w.isVisible()] if mdi is not None else []
        try:
            return bool(views) and views[0].viewport().cursor().shape() in (QtCore.Qt.BitmapCursor,
                                                                            QtCore.Qt.ForbiddenCursor)
        except RuntimeError:
            return False

    def _project_other_at(self, view, viewport, x, y):
        """A click while Project Geometry runs: on an edge or face of another Solid it is copied in (True); anything
        else is FreeCAD's (False)."""
        from incad.ui.nav import to_coin
        try:
            info = view.getObjectInfo(to_coin(viewport, x, y))
        except Exception:
            info = None
        if not info or not str(info.get("Component", "")).startswith(("Edge", "Face")):
            return False
        return self.copy_foreign_pick(info.get("Document", ""), info.get("Object", ""), info["Component"])

    def copy_foreign_pick(self, doc_name, obj_name, sub):
        """Project Geometry clicked an edge or face of another Solid, which FreeCAD will not link into the open
        sketch: its edges are copied in as the sketch's own lines (they don't follow that Solid). True when it was
        such a pick (copied, or tried and said why)."""
        import FreeCAD as App
        from incad.core import document, safe
        from incad.core import sketch as sketches
        sk = self.sketches.editing()
        if sk is None or not self._projection_running():
            self.projecting = False  # the tool or the sketch is over
            return False
        doc = App.getDocument(doc_name) if doc_name in App.listDocuments() else None
        obj = doc.getObject(obj_name) if doc is not None else None
        if obj is None or doc is not sk.Document or obj is sk:
            return False
        if "." in sub:  # 'Lid' + 'Chamfer.Edge3'
            parts = sub.split(".")
            obj, sub = doc.getObject(parts[-2]) or obj, parts[-1]
        if not sub.startswith(("Edge", "Face")) or not hasattr(obj, "Shape"):
            return False
        mine = document.body_of(sk)
        theirs = obj if obj.TypeId == "PartDesign::Body" else document.body_of(obj)
        if theirs is None or theirs is mine or not obj.isDerivedFrom("Part::Feature"):
            return False  # the sketch's own Solid: FreeCAD projects it, linked
        try:
            edges = obj.Shape.getElement(sub).Edges
        except Exception:
            return False
        r = safe.run_safely("Project (copy)", lambda: sketches.copy_edges(sk, edges), doc, only=[sk])
        if r.ok and r.value:
            log.report("info", "Copied %d edge%s of %s into the sketch: another Solid can't be linked, so they don't "
                       "follow its changes" % (len(r.value), "" if len(r.value) == 1 else "s", theirs.Label))
        return True

    def show_in_window(self, name):
        """Inventor's Show in Window: the row's faces light up on the solid and the view zooms to them."""
        import FreeCADGui as Gui
        self._on_browser_select(name)
        try:
            Gui.SendMsgToActiveView("ViewSelection")
        except Exception:
            log.logger().exception("zoom to selection")

    @staticmethod
    def _faces_of(obj):
        """(shown solid's name, [FaceN…]) a solid feature made on the shown solid, or None."""
        from incad.core import document, feature_faces
        if not obj.isDerivedFrom("PartDesign::Feature") or obj.TypeId in document.DATUM_TYPES:
            return None
        try:
            subs = feature_faces.on_tip(obj)
        except Exception:
            log.logger().exception("faces of %s", obj.Name)
            return None
        return (obj.getParentGeoFeatureGroup().Tip.Name, subs) if subs else None

    def _peek(self, obj):
        """Show a hidden plane, axis, point or sketch while its row is selected; nothing is saved (its own rule
        comes back when the selection moves on)."""
        from incad.core import document
        if self._peeked is not None and self._peeked[1] == obj.Name:
            return
        if obj.TypeId in document.ORIGIN_TYPES:
            if document.is_shown(obj):
                return
            origin = next((o for o in obj.InList if o.TypeId == "App::Origin"), None)
            was = [(o, bool(o.Visibility)) for o in ([origin] if origin is not None else []) + list(origin.OriginFeatures if origin else [])]
            self._peeked = (obj.Document.Name, obj.Name, was)
            try:
                document.set_visibility([obj], True, record=False)  # a look, not a choice
            except Exception:
                pass
        elif document.is_sketch_like(obj) or obj.TypeId in document.DATUM_TYPES:
            if obj.Visibility:
                return
            self._peeked = (obj.Document.Name, obj.Name, [(obj, False)])
            try:
                obj.ViewObject.Visibility = True
            except Exception:
                pass
        else:
            return
        self.schedule_refresh()

    def _unpeek(self):
        """Put back what a peek changed (unless the user made a choice meanwhile: forget_peek dropped it)."""
        import FreeCAD as App
        peeked, self._peeked = self._peeked, None
        if peeked is None:
            return
        if peeked[0] in App.listDocuments():
            for o, vis in peeked[2]:
                try:
                    o.Visibility = vis
                except Exception:
                    pass
        self.schedule_refresh()

    def forget_peek(self):
        """A sketch goes into edit mode: FreeCAD shows it and restores it after, the peek has nothing to put back."""
        self._peeked = None

    def end_peek(self):
        """Hide / Show is about to run: the temporary show ends first, so the toggle sees the real state (a
        hidden axis shown for its row is hidden, and the toggle shows it for good)."""
        self._unpeek()

    def _on_model_selection_changed(self):
        """The model's selection changed (a 3D pick, a cleared selection, a tree click): the tree shows it, and a
        peek whose object left the selection ends."""
        import FreeCAD as App
        import FreeCADGui as Gui
        if not self.active or self._syncing or self.browser is None:
            return
        try:
            self._sync_gear_gizmo()  # a gear picked: its gizmo (the user: there after the gears are placed)
        except Exception:
            log.logger().exception("gear gizmo")
        doc = App.ActiveDocument
        from incad.features import common as _common
        sels = _common.selection_ex(doc.Name) if doc is not None else []
        names = [s.ObjectName for s in sels]
        if self._peeked is not None and self._peeked[1] not in names:
            self._unpeek()
        if sels and self._selection_key(sels) == getattr(self, "_tree_picked", None):
            self.refresh_constraints_window()  # the tree's own pick (a row's faces): mapping every face back to
            return  # its feature took 25 s on a jar
        self._tree_picked = None
        rows = []
        from incad.core import feature_faces
        for sel in sels:  # a face or edge of the shown solid: the feature that made it (Inventor)
            if sel.Object.TypeId == "Assembly::AssemblyObject":  # a pick in an assembly: its component's row
                for sub in (sel.SubElementNames or ("",)):
                    obj = _common.resolve(sel.Object, sub)[0] if sub else sel.Object
                    rows.append(obj.Name if obj is not None else sel.ObjectName)
                continue
            subs = [n for n in sel.SubElementNames if n.startswith(("Face", "Edge"))]
            if subs and sel.Object.isDerivedFrom("PartDesign::Feature"):
                for sub in subs:
                    try:
                        rows.append(feature_faces.creator(sel.Object, sub).Name)
                    except Exception:
                        rows.append(sel.ObjectName)
            else:
                rows.append(sel.ObjectName)
        from incad.core import document
        for i, name in enumerate(rows):  # a Split piece's base has no row of its own: its Solid's row
            o = doc.getObject(name) if doc is not None else None
            if o is not None and (o.TypeId == "PartDesign::FeatureBase" or getattr(o, "InCadSplitPiece", False)):
                solid = document._solid_of(o)
                rows[i] = solid.Name if solid is not None else name
        try:
            self.browser.select_names(list(dict.fromkeys(rows)))
        except RuntimeError:
            pass
        self.refresh_constraints_window()  # a picked line or point filters the Constraint List

    def _document_activated(self, doc):
        """Back in an assembly's tab: when parts were edited in their own tabs meanwhile, ask whether to update
        (declined: the tree shows that it needs an update until Refresh). Deferred: FreeCAD is still switching
        the tab."""
        name = getattr(doc, "Name", None)
        if name:
            QtCore.QTimer.singleShot(0, lambda n=name: self._update_assembly(n))

    def _update_assembly(self, name):
        import FreeCAD as App
        from incad.core import assembly, log, safe
        doc = App.listDocuments().get(name)
        if doc is None or getattr(doc, "Restoring", False) or not assembly.is_assembly(doc) or safe.busy():
            return
        seen = getattr(self, "_assembly_seen", {})
        self._assembly_seen = seen
        try:
            changed = assembly.parts_changed(doc, seen.get(name))
            seen[name] = assembly.parts_signature(doc)  # asked once per change
            if not changed:
                return
            ask = UPDATE_ASK or (lambda label: QtWidgets.QMessageBox.question(
                self.mw, "Update Assembly", "Parts of %s changed. Update the assembly now?" % label,
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes)
            if ask(doc.Label):
                seen[name] = assembly.bring_parts_in(doc)
                log.report("info", "Updated %s" % doc.Label)
            else:
                assembly.mark_needs_update(doc)
                log.report("info", "%s needs an update (Refresh)" % doc.Label)
        except Exception:
            log.logger().exception("assembly update on return")
            return
        self.schedule_refresh()

    def assembly_refreshed(self, doc):
        """Refresh brought the parts in: remember how they look, so returning to the tab does not ask again."""
        from incad.core import assembly
        seen = getattr(self, "_assembly_seen", {})
        self._assembly_seen = seen
        seen[doc.Name] = assembly.parts_signature(doc)

    def _on_browser_marking_menu(self, name, global_pos):
        import FreeCAD as App
        from incad.core import context
        context.set_target(name)
        self._on_browser_select(name)  # the row's object is the selection: shown in the view while the menu is up
        obj = App.ActiveDocument.getObject(name) if App.ActiveDocument is not None else None
        kind = "origin" if obj is not None and obj.TypeId in ("App::Plane", "App::Line", "App::Point") else "browser"
        if obj is not None and obj.TypeId == "App::Link":  # a component's row (or a row of its history)
            kind = "asm_face"
        elif obj is not None and obj.TypeId == "PartDesign::Body":  # a Solid's row (Solid Bodies)
            kind = "solid"
        from incad.core import asm_cut, assembly
        if obj is not None and obj in assembly.joints(App.ActiveDocument):
            kind = "joint"  # a Relationships row: Edit, Delete, Suppress, Rename
        elif obj is not None and asm_cut.is_record(obj):
            kind = "asm_cut"  # an assembly cut's row: no Suppress (it is no constraint)
        from incad.core import image_reference
        if image_reference.is_picture(obj):
            kind = "picture"  # an imported picture's row: Edit scales it by a known length
        self._show_marking_menu(kind, global_pos)

    # ----- sketching -----
    def _build_shortcuts(self):
        """Single-key shortcuts (Inventor defaults) for registry commands; enabled per mode in _refresh."""
        menu_owned = {i for _, ids in MENUS for i in ids if i}
        for cmd_id in self.registry.ids():
            cmd = self.registry.get(cmd_id)
            if not cmd.shortcut or cmd_id in menu_owned:
                continue
            act = QAction(cmd.label, self.mw)
            act.setShortcut(QtGui.QKeySequence(cmd.shortcut))
            act.setShortcutContext(QtCore.Qt.WindowShortcut)
            act.triggered.connect(lambda _=False, i=cmd_id: self.registry.run(i))
            self.mw.addAction(act)
            self._shortcuts[cmd_id] = act
        self._esc = QAction("Cancel", self.mw)
        self._esc.setShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Escape))
        self._esc.setEnabled(False)
        self._esc.triggered.connect(self._on_escape)
        self.mw.addAction(self._esc)
        self._shortcuts["_esc"] = self._esc

    def _on_escape(self):
        """Esc, Inventor style: the value box first, then InCad's own picks and tools, then the feature dialog,
        else the running Sketcher tool (the sketch itself is left with Finish Sketch, never with Esc)."""
        if self._peeked is not None:
            self._unpeek()  # the object shown for its tree row goes back to its rule
        if self._solid_edit is not None and self.feature_session is None and self.sketches.editing() is None \
                and self.value_box is None:
            self.end_solid_edit()
            return
        if self.value_box is not None:
            self.value_box.closed_by_user = True  # Esc
            self._close_value_box()
            return
        if self.sketches.picking or self._profile_pick is not None or self.slot_tool is not None \
                or self._region_pick is not None:
            self.sketches.cancel_pick()
            self._cancel_profile_pick()
            self._cancel_region_pick()
            self.stop_slot_tool()
            return
        if self._measure is not None:
            self._stop_measuring()
            self._close_info_panel()
            self.status.set_prompt("Ready")
            return
        if self._point_pick is not None:
            self._cancel_point_pick()
            self.status.set_prompt("Ready")
            return
        if self.section_panel is not None:
            self.stop_section(keep_cut=True)  # Esc = Done: the cut stays, the panel goes
            return
        if self.feature_session is not None:
            self.feature_session.cancel()
            return
        import FreeCADGui as Gui
        if self.sketches.editing() is not None:
            Gui.runCommand("Sketcher_StopOperation")  # ends the running Sketcher tool; the sketch stays open
            return
        mdi = self._mdi()
        views = [w for w in mdi.findChildren(QtWidgets.QGraphicsView) if w.isVisible()] if mdi is not None else []
        if views:  # outside a sketch: FreeCAD's own Esc handling, with InCad's shortcut off meanwhile (it would
            vp = views[0].viewport()  # catch its own key again: recursion)
            self._esc.setEnabled(False)
            try:
                for etype in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease):
                    QtWidgets.QApplication.sendEvent(vp, QtGui.QKeyEvent(etype, QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier))
            finally:
                self._esc.setEnabled(True)

    # ----- InCad's own sketch tools -----
    def start_slot_tool(self, mode):
        import FreeCADGui as Gui
        from incad.ui.slot_tool import SlotTool
        gdoc = Gui.ActiveDocument
        edit = gdoc.getInEdit() if gdoc is not None else None
        sk = getattr(edit, "Object", None)
        if sk is None or sk.TypeId != "Sketcher::SketchObject":
            raise ValueError("Open a sketch first")
        self.stop_slot_tool()
        Gui.runCommand("Sketcher_StopOperation")  # no FreeCAD tool fighting for the clicks
        self.slot_tool = SlotTool(gdoc.ActiveView, sk, mode, on_finish=self._slot_tool_done)
        self.slot_tool.start()
        self.schedule_refresh()
        return self.slot_tool

    def start_offset_tool(self):
        """InCad's own Offset (incad.ui.offset_tool) in the slot tool's place: Esc, another command, leaving the
        sketch end it the same way."""
        import FreeCADGui as Gui
        from incad.features import common
        from incad.ui.offset_tool import OffsetTool
        gdoc = Gui.ActiveDocument
        edit = gdoc.getInEdit() if gdoc is not None else None
        sk = getattr(edit, "Object", None)
        if sk is None or sk.TypeId != "Sketcher::SketchObject":
            raise ValueError("Open a sketch first")
        picked = []
        for sel in common.selection_ex(sk.Document.Name):
            for sub in sel.SubElementNames or ():
                if sel.Object is sk and sub.startswith("Edge"):
                    picked.append(int(sub[4:]) - 1)
        self.stop_slot_tool()
        Gui.runCommand("Sketcher_StopOperation")  # no FreeCAD tool fighting for the clicks
        Gui.Selection.clearSelection()
        self.slot_tool = OffsetTool(gdoc.ActiveView, sk, on_finish=self._slot_tool_done,
                                    on_placed=lambda s, name: self.open_value_box(s, name))
        self.slot_tool.start(picked)
        self.schedule_refresh()
        return self.slot_tool

    def start_overall_dim_tool(self):
        """Sketch ▸ Overall Dimension (incad.ui.overall_dim_tool) in the slot tool's place."""
        import FreeCADGui as Gui
        from incad.features import common
        from incad.ui.overall_dim_tool import OverallDimTool
        gdoc = Gui.ActiveDocument
        edit = gdoc.getInEdit() if gdoc is not None else None
        sk = getattr(edit, "Object", None)
        if sk is None or sk.TypeId != "Sketcher::SketchObject":
            raise ValueError("Open a sketch first")
        from incad.ui.tangent_circle_tool import from_subname
        picked = [g for sel in common.selection_ex(sk.Document.Name) if sel.Object is sk
                  for g in map(from_subname, sel.SubElementNames or ()) if g is not None and g not in (-1, -2)]
        self.stop_slot_tool()
        Gui.runCommand("Sketcher_StopOperation")  # no FreeCAD tool fighting for the clicks
        Gui.Selection.clearSelection()
        self.slot_tool = OverallDimTool(gdoc.ActiveView, sk, on_finish=self._slot_tool_done,
                                        on_placed=lambda s, name: self.open_value_box(s, name))
        self.slot_tool.start(picked)
        self.schedule_refresh()
        return self.slot_tool

    def start_tangent_circle_tool(self):
        """Sketch ▸ Tangent Circle (incad.ui.tangent_circle_tool) in the slot tool's place."""
        import FreeCADGui as Gui
        from incad.features import common
        from incad.ui.tangent_circle_tool import TangentCircleTool, from_subname
        gdoc = Gui.ActiveDocument
        edit = gdoc.getInEdit() if gdoc is not None else None
        sk = getattr(edit, "Object", None)
        if sk is None or sk.TypeId != "Sketcher::SketchObject":
            raise ValueError("Open a sketch first")
        picked = [g for sel in common.selection_ex(sk.Document.Name) if sel.Object is sk
                  for g in map(from_subname, sel.SubElementNames or ()) if g is not None]
        self.stop_slot_tool()
        Gui.runCommand("Sketcher_StopOperation")  # no FreeCAD tool fighting for the clicks
        Gui.Selection.clearSelection()
        self.slot_tool = TangentCircleTool(gdoc.ActiveView, sk, on_finish=self._slot_tool_done)
        self.slot_tool.start(picked)
        self.schedule_refresh()
        return self.slot_tool

    def start_mirror_tool(self):
        """Sketch ▸ Mirror (incad.ui.mirror_tool), tool first: the shapes, then the mirror line, by clicks. Shapes
        selected before the command are taken; a mirror line selected last too — an axis, the origin, or a line or
        point outside the shapes — makes the copy at once."""
        import FreeCADGui as Gui
        from incad.core import safe, sketch_mirror, sketch_symmetric as sym
        from incad.features import common
        from incad.ui.mirror_tool import MirrorTool, outlines
        sk = self.sketches.editing()
        if sk is None:
            raise ValueError("Open a sketch first")
        picks = []
        for sel in common.selection_ex(sk.Document.Name):
            if sel.Object is sk:
                for sub in sel.SubElementNames or ():
                    pick = sym.from_subname(sk, sub)
                    if pick is not None and pick not in picks:
                        picks.append(pick)
        axis = None
        if len(picks) >= 2:
            last, rest = picks[-1], outlines(sk, picks[:-1])
            if last[1] < 0 or last[1] not in rest:  # clearly no shape of the ones picked: the mirror line
                axis, picks = last, picks[:-1]
        geos = outlines(sk, picks)
        self.stop_slot_tool()
        Gui.runCommand("Sketcher_StopOperation")
        if axis is not None and geos:
            r = safe.run_safely("Mirror", lambda: sketch_mirror.mirror(sk, geos, axis), sk.Document, only=[sk])
            if not r.ok:
                raise ValueError(r.error)
            Gui.Selection.clearSelection()
            for g in r.value:
                Gui.Selection.addSelection(sk.Document.Name, sk.Name, "Edge%d" % (g + 1))
            log.report("info", "Mirrored %d: the copy follows its original (right-click it ▸ Break Mirror Link "
                               "frees it)" % len(r.value))
            self.schedule_refresh()
            return None
        self.slot_tool = MirrorTool(Gui.ActiveDocument.ActiveView, sk, geos, on_finish=self._slot_tool_done)
        self.slot_tool.start()
        self.schedule_refresh()
        return self.slot_tool

    def start_symmetric_tool(self):
        """Sketch ▸ Symmetric (incad.ui.symmetric_tool) in the slot tool's place: what is selected now comes first."""
        import FreeCADGui as Gui
        from incad.core import sketch_symmetric as sym
        from incad.features import common
        from incad.ui.symmetric_tool import SymmetricTool
        sk = self.sketches.editing()
        if sk is None:
            raise ValueError("Open a sketch first")
        picked = []
        for sel in common.selection_ex(sk.Document.Name):
            if sel.Object is sk:
                for sub in sel.SubElementNames or ():
                    pick = sym.from_subname(sk, sub)
                    if pick is not None and pick not in picked:
                        picked.append(pick)
        self.stop_slot_tool()
        Gui.runCommand("Sketcher_StopOperation")  # no FreeCAD tool fighting for the clicks
        Gui.Selection.clearSelection()
        self.slot_tool = SymmetricTool(Gui.ActiveDocument.ActiveView, sk, on_finish=self._slot_tool_done)
        self.slot_tool.start(picked[:3])
        self.schedule_refresh()
        return self.slot_tool

    def start_text_tool(self):
        """Sketch ▸ Text: on a box (a line of it) or a curve of the sketch picked before, the dialog opens on it;
        otherwise two clicks draw the text box (made inside the dialog's undo step)."""
        import FreeCADGui as Gui
        from incad.features import common, sketch_text
        from incad.ui.text_place import TextPlaceTool
        sk = self.sketches.editing()
        if sk is None:
            raise ValueError("Open a sketch first")
        if self.feature_session is not None:
            raise self._dialog_first()
        for sel in common.selection_ex(sk.Document.Name):
            if sel.Object is sk and any(common.accepts("sketch_curve", sk, x) for x in sel.SubElementNames or ()):
                self.start_feature(sketch_text.TEXT)
                return
        self.stop_slot_tool()
        Gui.runCommand("Sketcher_StopOperation")

        def place(a, b):
            sketch_text._pending_box = (a, b)
            try:
                self.start_feature(sketch_text.TEXT)
            except Exception as e:
                sketch_text._pending_box = None
                log.report("warning", "Text: %s" % e)

        self.slot_tool = TextPlaceTool(Gui.ActiveDocument.ActiveView, sk, place, on_finish=self._slot_tool_done)
        self.slot_tool.start()
        self.schedule_refresh()

    def stop_slot_tool(self):
        tool, self.slot_tool = getattr(self, "slot_tool", None), None
        if tool is not None:
            tool.stop()

    def _slot_tool_done(self, tool):
        if getattr(self, "slot_tool", None) is tool:
            self.slot_tool = None
        self.schedule_refresh()

    def shortcut_action(self, cmd_id):
        return self._shortcuts.get(cmd_id)

    @perf.timed("sketch changed")
    def _sketch_changed(self, sk):
        """New dimensions get dN names; one added by the Dimension tool (no new geometry) opens the value box."""
        if getattr(sk.Document, "Restoring", False):
            return  # a file being loaded sets every sketch's geometry: nothing was drawn, and a recompute from
            # these follow-ups on the half-restored document crashed the Sketcher (external geometry)
        QtCore.QTimer.singleShot(0, lambda: self._name_dimensions(sk))
        QtCore.QTimer.singleShot(0, lambda: self._pin_new_polygons(sk))
        self._rects_later(sk)  # once the tool's step is over: its own centre point comes a moment later
        QtCore.QTimer.singleShot(0, lambda: self._settle_constraints(sk))
        QtCore.QTimer.singleShot(0, lambda: self._update_area_profiles(sk))
        QtCore.QTimer.singleShot(0, lambda: self._adopt_projections(sk))
        if self.construction_mode:
            QtCore.QTimer.singleShot(0, lambda: self._make_construction(sk))
        QtCore.QTimer.singleShot(0, self.refresh_constraints_window)  # a delete, an undo: never stale rows
        self._follow_texts(sk)

    def _follow_texts(self, sk):
        """The sketch's Text follows its box or curve as soon as they move (a drag, a dimension): FreeCAD rebuilds
        the text only on the next full recompute — while the sketch is open, after Finish. Many changes in a row
        (a drag) rebuild once."""
        from incad.features import sketch_text
        if not sketch_text.texts_of(sk):
            return
        due = self.__dict__.setdefault("_texts_due", set())
        key = (sk.Document.Name, sk.Name)
        if key in due:
            return
        due.add(key)

        def rebuild():
            due.discard(key)
            if not _alive(sk) or getattr(sk.Document, "Restoring", False) or getattr(sk.Document, "Recomputing", False):
                return
            for t in sketch_text.texts_of(sk):
                try:
                    t.recompute()
                except Exception:
                    log.logger().exception("rebuild %s after its sketch changed", t.Name)
            self.sketches._sync_text_lines(self.sketches.editing())

        QtCore.QTimer.singleShot(30, rebuild)

    def toggle_construction(self):
        """Inventor's Construction button: with sketch geometry selected, switch it (and end the drawing mode if it
        was on); otherwise turn the drawing mode on/off (new geometry becomes construction while on)."""
        import FreeCADGui as Gui
        from incad.core import log
        sk = self.sketches.editing()
        if sk is None:
            raise ValueError("Open a sketch first")
        picked = [sub for sel in Gui.Selection.getSelectionEx(sk.Document.Name) if sel.Object is sk
                  for sub in sel.SubElementNames if sub.startswith(("Edge", "ExternalEdge"))]
        log.logger().info("Construction pressed: picked=%s mode=%s", picked, self.construction_mode)
        if picked:  # the whole profile of each picked edge (a polygon's sides), not one edge at a time
            from incad.core import safe, sketch
            ids = [-int(sub[len("ExternalEdge"):]) - 2 if sub.startswith("ExternalEdge") else int(sub[4:]) - 1
                   for sub in picked]
            was_on, self.construction_mode = self.construction_mode, False  # the same click ends the drawing mode
            # the sketch and what is built right on it (its Extrude): a profile it breaks is refused, the rest of the
            # part (a chamfer further on, the user's jar) follows on Finish Sketch
            users = [o for o in sk.InList if o.isDerivedFrom("PartDesign::Feature")]
            r = safe.run_safely("Construction", lambda: sketch.toggle_construction(sk, ids), sk.Document,
                                only=[sk] + users)
            Gui.Selection.clearSelection()
            projected = [i for i in (r.value or []) if i < 0] if r.ok else []
            if projected:  # a projected edge looks the same either way: say what it is now
                flags = sketch._projected_flags(sk)
                normal = [i for i in projected if flags.get(i, (False,))[0]]
                log.report("info", "%d projected edge%s now %s" % (
                    len(projected), "" if len(projected) == 1 else "s",
                    "normal: part of the profile" if len(normal) == len(projected) else "construction"))
            if was_on:
                log.report("info", "Construction off: normal geometry")
            if self.ribbon is not None:
                self.ribbon.refresh_checks()
            return
        log.logger().info("Construction pressed with no edge picked; selection=%s preselection=%s",
                          [(sel.Object.Name, list(sel.SubElementNames)) for sel in Gui.Selection.getSelectionEx("")],
                          self._preselection_text(Gui))
        self.construction_mode = not self.construction_mode
        self._construction_from[(sk.Document.Name, sk.Name)] = sk.GeometryCount
        log.report("info", "Construction on: new geometry is construction (click again to stop)"
                   if self.construction_mode else "Construction off: normal geometry")
        if self.ribbon is not None:
            self.ribbon.refresh_checks()

    @staticmethod
    def _preselection_text(Gui):
        try:
            pre = Gui.Selection.getPreselection()
            return "%s.%s" % (getattr(getattr(pre, "Object", None), "Name", None), getattr(pre, "SubElementName", ""))
        except Exception as e:
            return "n/a (%s)" % e

    @staticmethod
    def _adopt_waits(doc):
        """Someone else's open undo step (a feature dialog, a Sketcher tool still placing) or a recompute running
        (its progress bar runs these handlers mid-way): adopting waits."""
        from incad.core import safe
        return bool(safe.busy() or doc.HasPendingTransaction or getattr(doc, "Recomputing", False))

    @staticmethod
    def _sketch_in_edit(sk):
        try:
            import FreeCADGui as Gui
            gdoc = Gui.getDocument(sk.Document.Name)
            edit = gdoc.getInEdit() if gdoc is not None else None
            return edit is not None and edit.Object is sk
        except Exception:
            return False

    @perf.timed("projection changed")
    def _projection_changed(self, sk):
        """An edge projected (or its projection undone / redone): it becomes normal geometry right away."""
        if getattr(sk.Document, "Restoring", False):
            return  # a file being loaded: the on-open migration handles its sketches
        QtCore.QTimer.singleShot(0, lambda: self._adopt_projections(sk))

    def _adopt_projections(self, sk, tries=0):
        """Inventor: a projected edge is normal geometry right away (FreeCAD's tool projects construction). When it
        must wait (see _adopt_waits) it looks again a little later, then leaves it to the next change."""
        from incad.core import document
        from incad.core import sketch as sketches
        if not _alive(sk):
            return
        try:
            doc = sk.Document
            if not document.is_incad_part(doc) or getattr(doc, "Restoring", False) or not sketches.projected(sk):
                return
            if self._adopt_waits(doc):
                if tries < 40 or self._sketch_in_edit(sk):  # a Sketcher tool still projecting: as long as it takes
                    QtCore.QTimer.singleShot(150 if tries < 40 else 400,
                                             lambda: self._adopt_projections(sk, tries + 1))
                else:
                    log.logger().info("Projected edges of %s: still busy after 6 s, left to the next change", sk.Label)
                return
            notes = []
            adopted = sketches.adopt_projections(sk, notes)
            key = (doc.Name, sk.Name)
            pending = sketches.pending_projections(sk)
            new = pending - self._projections_told.get(key, set())
            self._projections_told[key] = pending
            if adopted or new:  # a used sketch says so once per new projection, not on every edit
                for text in notes:
                    log.report("info", text)
        except Exception as e:
            log.report("warning", "Could not make the projected edges normal geometry: %s" % e, traceback.format_exc())

    def _adopt_all_projections(self, name, tries=0):
        """Once per opened file: sketches made before follow the rule (projected edges normal, old copies gone)."""
        import FreeCAD as App
        from incad.core import sketch as sketches
        if name not in App.listDocuments():
            return
        doc = App.getDocument(name)
        try:
            if getattr(doc, "Restoring", False) or self._adopt_waits(doc):
                if tries < 200:  # a long recompute of a big part: half a minute
                    QtCore.QTimer.singleShot(150, lambda: self._adopt_all_projections(name, tries + 1))
                else:
                    log.logger().info("Projected edges on open of %s: still busy after 30 s, left to the next "
                                      "change of each sketch", name)
                return
            notes = []
            for sk in [o for o in doc.Objects if sketches.is_sketch(o)]:
                told = []
                if sketches.adopt_projections(sk, told):  # recomputes only when it changed something
                    notes += told  # a used sketch is left as it is, silently: it was made that way
                self._projections_told[(name, sk.Name)] = sketches.pending_projections(sk)
            for text in notes:
                log.report("info", text)
        except Exception as e:
            log.logger().warning("Projected edges on open: %s", e)

    def _make_construction(self, sk):
        if not _alive(sk):
            return  # deleted meanwhile (these handlers run just after the change)
        try:
            key = (sk.Document.Name, sk.Name)
            start = self._construction_from.get(key, sk.GeometryCount)
            new = [i for i in range(start, sk.GeometryCount) if not sk.getConstruction(i)]
            self._construction_from[key] = sk.GeometryCount
            for i in new:
                sk.setConstruction(i, True)
            if new:
                safe.recompute(sk.Document)
        except Exception as e:
            log.report("warning", "Could not make the new geometry construction: %s" % e)

    def _settle_constraints(self, sk, tries=0):
        """A constraint tool (Symmetric, Parallel, Equal...) that ties what older ties hold already: FreeCAD names the
        new one redundant and the sketch stops solving (a slot made symmetric about an axis: its Horizontal). Once
        the tool's step is closed, the older ties it covers go (sketches.make_room)."""
        from incad.core import sketch as sketches
        if not _alive(sk):
            return
        try:
            doc, key = sk.Document, (sk.Document.Name, sk.Name)
            if doc.HasPendingTransaction or getattr(doc, "Recomputing", False):
                if tries < 100:
                    QtCore.QTimer.singleShot(50, lambda: self._settle_constraints(sk, tries + 1))
                return
            count, geo = sk.ConstraintCount, sk.GeometryCount
            before = self._constraints_seen.get(key) or (count - 1, geo)  # the first change seen: one tie added
            self._constraints_seen[key] = (count, geo)
            if count <= before[0] or geo != before[1] or self.sketches.editing() is not sk:
                return  # nothing added, or drawn (_auto_dimension's)
            steps = list(getattr(doc, "UndoNames", []) or [])
            if not steps or not (steps[0].startswith("Add ") and steps[0].endswith(" constraint")):
                return
            new = [i for i in range(max(before[0], 0), count) if sk.Constraints[i].Type in sketches.TIES]
            if new and sketches.make_room(sk, new):
                self._constraints_seen[key] = (sk.ConstraintCount, sk.GeometryCount)
        except Exception as e:
            log.report("warning", "Could not settle the new constraint: %s" % e, traceback.format_exc())

    def _name_dimensions(self, sk):
        from incad.core import document
        from incad.core import sketch as sketches
        try:
            doc = sk.Document
        except Exception:
            return  # its file was closed meanwhile: nothing to name
        try:
            if not document.is_incad_part(doc):
                return
            key = (doc.Name, sk.Name)
            count = sk.GeometryCount
            drawn = count != self._geometry_counts.get(key, count)
            self._geometry_counts[key] = count
            known = self._dim_known.get(key)
            replaced = sketches.reconcile_replaced(sk, known) if known else None
            box = self.value_box
            if replaced and box is not None and box.name == replaced:
                if not box._typed:  # the replacement may be another kind (two lines picked: an angle)
                    box.edit.setText(sketches.document._format(sk.getDatum(replaced)))
                for ms in (150, 400, 800):  # the view takes the keyboard back when the placing click ends
                    QtCore.QTimer.singleShot(ms, lambda b=box: self.value_box is b and b.take_keyboard())
            elif known and box is not None and self._box_owner == key and box.name in sketches.vanished_dimensions(sk, known):
                self._close_value_box()  # its dimension is gone (tool cancelled, undo): nothing to apply to
            added = [] if replaced else sketches.name_new_dimensions(doc)  # never in an undo step of our own:
            self._dim_known[key] = sketches.dimension_snapshot(sk)       # the Sketcher tool's step is open
            placing = doc.HasPendingTransaction  # the Dimension tool still drags the line (its step is open)
        except Exception as e:
            log.report("warning", "Could not name new dimensions: %s" % e, traceback.format_exc())
            return
        if self.sketches.editing() is not sk:
            return
        # Inventor: place the dimension first, then type. The box waits until the tool has put it down.
        if len(added) == 1 and not drawn:
            if placing:
                self._box_pending = (key, added[0], sk)
                QtCore.QTimer.singleShot(150, self._open_pending_box)
            else:
                self.open_value_box(sk, added[0])
            return
        pending = self._box_pending
        if pending and pending[0] == key and replaced:
            self._box_pending = (key, replaced, sk)  # the placing click replaced it: same box, same name

    def _open_pending_box(self, tries=0):
        """The tool put the dimension down (its undo step closed): open its box. Nothing fires at that moment,
        so this looks every 150 ms, for a while."""
        pending = self._box_pending
        if pending is None or not self.active:
            return
        key, name, sk = pending
        if not _alive(sk) or self.sketches.editing() is not sk or name not in [c.Name for c in sk.Constraints]:
            self._box_pending = None  # cancelled, finished, or gone
            return
        if sk.Document.HasPendingTransaction:
            if tries < 400:  # a minute of dragging the line is plenty
                QtCore.QTimer.singleShot(150, lambda: self._open_pending_box(tries + 1))
            else:
                self._box_pending = None
            return
        self._box_pending = None
        self.open_value_box(sk, name)
        if self.value_box is not None:
            self.value_box.restart_tool = "sketch.dimension"  # the tool stops when the value is applied

    @perf.timed("after undo")
    def _after_undo(self, doc):
        """Undo/redo can bring objects back without their formula links (FreeCAD quirk): repair them."""
        def repair():
            from incad.core import model
            try:
                model.relink(doc)
            except Exception as e:
                log.report("warning", "Could not repair formula links: %s" % e, traceback.format_exc())
        QtCore.QTimer.singleShot(0, repair)
        box = self.value_box
        if box is not None and self._box_owner and self._box_owner[0] == doc.Name:
            from incad.core import sketch as sketches
            QtCore.QTimer.singleShot(0, lambda: self.value_box is box and sketches.find_dimension(doc, box.name)[0] is None
                                     and self._close_value_box())  # its dimension was undone
        editing = self.sketches.editing()
        if editing is not None:
            from incad.core import sketch as sketches
            QtCore.QTimer.singleShot(0, lambda: _alive(editing) and self._dim_known.__setitem__(
                (editing.Document.Name, editing.Name), sketches.dimension_snapshot(editing)))

    def _seed_geometry(self, sk):
        """Baseline for the 'drawn or dimensioned?' rule when a sketch is opened."""
        from incad.core import sketch as sketches
        self._geometry_counts[(sk.Document.Name, sk.Name)] = sk.GeometryCount
        self._dim_known[(sk.Document.Name, sk.Name)] = sketches.dimension_snapshot(sk)
        self._polygons_from[(sk.Document.Name, sk.Name)] = sk.GeometryCount
        self._rects_from[(sk.Document.Name, sk.Name)] = sk.GeometryCount
        self._rects_done[(sk.Document.Name, sk.Name)] = set()

    def _update_area_profiles(self, sk):
        """Features built on an area of this sketch follow its edits (the area holding the remembered point)."""
        from incad.core import regions
        if not _alive(sk) or regions.is_profile(sk):
            return
        if getattr(sk.Document, "Recomputing", False):
            # a recompute's progress bar runs this mid-way: a recompute from here ran the rest of the document
            # again (a big part took twice as long). Once, when it is over.
            due = self.__dict__.setdefault("_areas_due", set())
            key = (sk.Document.Name, sk.Name)
            if key not in due:
                due.add(key)
                QtCore.QTimer.singleShot(150, lambda: (due.discard(key), self._update_area_profiles(sk)))
            return
        try:
            if not regions.profiles_of(sk):
                return
            warnings = regions.follow(sk, self._area_warned)  # each lost area once, naming its features
            safe.recompute(sk.Document)
            for text in warnings:
                log.report("warning", text)
        except Exception as e:
            log.report("warning", "Could not update the areas of %s: %s" % (sk.Label, e), traceback.format_exc())

    def _pin_new_polygons(self, sk):
        """A new polygon keeps its centre's snap to the origin or an axis (only new geometry: a constraint the user
        deleted later is not put back)."""
        from incad.core import sketch as sketches
        if not _alive(sk):
            return  # deleted meanwhile (these handlers run just after the change)
        try:
            key = (sk.Document.Name, sk.Name)
            first = self._polygons_from.get(key, sk.GeometryCount)
            if sk.GeometryCount <= first:
                self._polygons_from[key] = sk.GeometryCount
                return
            if sketches.pin_polygon_centres(sk, first):
                sk.Document.recompute([sk])  # the open sketch only: the part follows when it closes
            self._polygons_from[key] = sk.GeometryCount
        except Exception as e:
            log.report("warning", "Could not keep the polygon's centre constraint: %s" % e)

    def _rects_later(self, sk):
        """The rectangle check waits until the sketch has been quiet for a moment: FreeCAD's Center Rectangle adds its
        centre point after the lines, and a check in between gave the rectangle a second one."""
        timer = self.__dict__.get("_rects_timer")
        if timer is None:
            timer = self._rects_timer = QtCore.QTimer()
            timer.setSingleShot(True)
            timer.setInterval(350)
            timer.timeout.connect(lambda: self._rects_pending and self._centres_for_new_rectangles(
                self._rects_pending.pop()) if self._rects_pending else None)
        self._rects_pending = [sk]
        timer.start()

    def _centres_for_new_rectangles(self, sk):
        """A rectangle drawn since the sketch was opened gets a construction centre point (the user: one-constraint
        symmetry). Every change looks again — FreeCAD's tool adds the lines before their corner ties — and each
        rectangle is handled once."""
        from incad.core import sketch as sketches
        if not _alive(sk) or self.sketches.editing() is not sk:
            return
        try:
            key = (sk.Document.Name, sk.Name)
            first = self._rects_from.setdefault(key, sk.GeometryCount)
            if sk.GeometryCount < first:  # older geometry deleted: what was drawn moved down
                self._rects_from[key] = first = sk.GeometryCount
            ours = self.__dict__.setdefault("_rects_ours", {}).setdefault(key, set())  # our centre points
            changed = sketches.drop_doubled_centres(sk, ours) if ours else 0
            if sk.GeometryCount > first:
                changed += sketches.add_rectangle_centres(sk, first, self._rects_done.setdefault(key, set()), ours)
            if changed:
                sk.Document.recompute([sk])  # the open sketch only (the whole part took long: a thread)
        except Exception as e:
            log.report("warning", "Could not add the rectangle's centre point: %s" % e)

    def _apply_from_box(self, name, text):
        """The value box applies to its own sketch's document; returns (ok, error) so the box can stay red."""
        import FreeCAD as App
        from incad.core import safe
        from incad.core import sketch as sketches
        doc_name, _ = self._box_owner or (None, None)
        try:
            doc = App.getDocument(doc_name)
        except Exception:
            return False, "The sketch's document is closed"
        sk = doc.getObject(self._box_owner[1])
        key = tuple(self._box_owner)
        if sk is not None and sketches.find_dimension(doc, name)[0] is None and self._dim_known.get(key):
            try:
                sketches.reconcile_replaced(sk, self._dim_known[key])  # replaced by the placing click just now
            except Exception as e:
                log.report("warning", "Could not find %s again: %s" % (name, e))
        open_sketch = sk is not None and self.sketches.editing() is sk  # its part follows on Finish Sketch
        r = safe.run_safely("Set %s" % name, lambda: sketches.set_dimension(doc, name, text, not open_sketch), doc,
                            only=[sk] if open_sketch else None)
        if r.ok and sk is not None:
            self._dim_known[key] = sketches.dimension_snapshot(sk)
        self.schedule_refresh()
        return r.ok, r.error

    def open_value_box(self, sk, name):
        from incad.core import sketch as sketches
        self._close_value_box(apply_if_typed=True)
        expr = sketches._expression_of(sk, "Constraints." + name)
        text = sketches.to_display(sk.Document, expr) if expr else \
            sketches.document._format(sk.getDatum(name))
        parent = self._mdi().viewport() if self._mdi() is not None else self.mw
        box = ValueBox(name, text, self.theme, parent, apply=self._apply_from_box)
        self._box_owner = (sk.Document.Name, sk.Name)
        box.closed.connect(lambda: self._value_box_closed(box))
        self.value_box = box
        app = QtWidgets.QApplication.instance()
        if not self._box_focus_hooked:
            app.focusChanged.connect(self._keep_box_keyboard)
            self._box_focus_hooked = True
        from incad.core import overall_dims
        index = next((i for i, c in enumerate(sk.Constraints) if c.Name == name), -1)
        kind = overall_dims.kind_of(sk, index)
        if kind is not None:  # two arcs / circles: centre to centre, outer edges (a slot's overall length) or gap
            box.set_kinds(overall_dims.KINDS, kind[0], lambda k: self._switch_dimension_kind(sk, name, k))
        box.show_near(QtGui.QCursor.pos())

    def _switch_dimension_kind(self, sk, name, kind):
        """The value box's Centers / Overall / Gap: the dimension measures the two arcs that way (same name, its
        current size), and the box opens again on it."""
        from incad.core import overall_dims, safe
        doc = sk.Document

        def go():
            index = next(i for i, c in enumerate(sk.Constraints) if c.Name == name)
            overall_dims.set_kind(sk, index, kind)

        r = safe.run_safely("Dimension Type", go, doc, only=[sk])
        if not r.ok:
            if self.value_box is not None:
                self.value_box.set_error(r.error)
            return
        self._close_value_box(apply_if_typed=False)  # a value typed was for the other kind
        self.open_value_box(sk, name)

    def _close_value_box(self, apply_if_typed=False):
        """apply_if_typed: the user moved on (next dimension, Finish Sketch, another tab) — what was typed is
        applied first instead of silently dropped; a value that can't be used is reported."""
        box = self.value_box
        if box is None:
            return
        try:
            if apply_if_typed and box._typed and box.edit.text().strip():
                ok, error = self._apply_from_box(box.name, box.edit.text().strip())
                if not ok:
                    log.report("warning", "%s = %s was not applied: %s" % (box.name, box.edit.text().strip(), error))
            box.close()
        except RuntimeError:
            pass
        self.value_box = None

    def _keep_box_keyboard(self, _old, new):
        """FreeCAD's 3D view takes the keyboard whenever the mouse moves over it: while the dimension box is open,
        typing, Enter and Esc belong to the box (other fields, e.g. the parameter table, keep it)."""
        box = self.value_box
        if box is None or new is None or not self.active:
            return
        w = new
        while w is not None:
            if isinstance(w, QtWidgets.QGraphicsView):
                QtCore.QTimer.singleShot(0, lambda b=box: self.value_box is b and b.take_keyboard())
                return
            w = w.parentWidget()

    def _value_box_closed(self, box):
        if self.value_box is box:
            self.value_box = None
        tool = getattr(box, "restart_tool", None)
        if tool and getattr(box, "closed_by_user", False) and self.sketches.editing() is not None:
            # Inventor keeps dimensioning after a value is entered; FreeCAD's tool is left dead by the change
            # under it (its cursor stays, probed): stop what is left of it and start it again
            def stop():
                if self.active and self.sketches.editing() is not None and self.value_box is None:
                    import FreeCADGui as Gui
                    try:
                        Gui.runCommand("Sketcher_StopOperation")
                    except Exception as e:
                        log.logger().warning("Could not stop the Sketcher tool: %s", e)
                    QtCore.QTimer.singleShot(250, start)  # FreeCAD lets go of the old handler on the event loop

            def start():
                if self.active and self.sketches.editing() is not None and self.value_box is None:
                    try:
                        self.registry.run(tool)
                    except Exception as e:
                        log.logger().warning("Could not restart %s: %s", tool, e)
            QtCore.QTimer.singleShot(100, stop)

    def _sketch_tool_active(self):
        """A Sketcher tool shows its own bitmap cursor in the view (FreeCAD tells Python nothing else)."""
        mdi = self._mdi()
        views = [w for w in mdi.findChildren(QtWidgets.QGraphicsView) if w.isVisible()] if mdi is not None else []
        return bool(views) and Navigator.tool_cursor(views[0].viewport())



    def _on_value_applied(self, name, text):
        self._on_parameter_set(name, text)

    def _on_parameter_renamed(self, old, new):
        from incad.core import safe
        from incad.core import sketch as sketches
        doc = self._doc()
        if doc is not None:
            from incad.core import model
            self._parameters_said(safe.run_safely("Rename %s" % old,
                                                  lambda: model.rename_model_parameter(doc, old, new), doc))
        self.schedule_refresh()

    def _on_parameter_clicked(self, name):
        if self.value_box is not None:
            if name != self.value_box.name:
                self.value_box.insert(name)
        elif self.feature_session is not None:
            self.feature_session.insert(name)

    def _edit_plane_at(self, view, viewport, x, y):
        """A double-click on a work plane in the view: its Work Plane dialog (the user: not a sketch, and not
        FreeCAD's own attachment panel). What is under the pointer, as the right-click menu finds it (the
        preselection names the Body, and is empty when no hover reached the view)."""
        import FreeCAD as App
        if self.feature_session is not None:
            return False
        doc = App.ActiveDocument
        try:
            info = view.getObjectInfo(to_coin(viewport, x, y)) if doc is not None else None
            obj = doc.getObject(info["Object"]) if info and info.get("Document") == doc.Name else None
            if obj is None or obj.TypeId != "PartDesign::Plane":
                return False
        except (RuntimeError, KeyError):  # the object under the pointer was just deleted
            return False
        try:
            self.edit_feature(obj)
        except ValueError as e:
            log.report("warning", str(e))
        return True

    @staticmethod
    def _preselected_dimension():
        """(sketch, name) of the dimension under the cursor, or (None, None)."""
        import FreeCADGui as Gui
        try:
            pre = Gui.Selection.getPreselection()
            obj = getattr(pre, "Object", None)
            subs = getattr(pre, "SubElementNames", ()) or ()
            if obj is None or obj.TypeId != "Sketcher::SketchObject":
                return None, None
        except RuntimeError:  # the preselected object was just deleted
            return None, None
        for sub in subs:
            if sub.startswith("Constraint"):
                try:
                    c = obj.Constraints[int(sub[len("Constraint"):]) - 1]
                except (ValueError, IndexError):
                    continue
                if c.Name:
                    return obj, c.Name
        return None, None

    def _move_hook(self, view, viewport, x, y):
        """A plain move: picking three points, the plane through two picked and the marker under the pointer shows."""
        pick = self._point_pick
        if pick is not None and pick.count == 3 and len(pick.picked) == 2:
            i = pick.nearest(viewport, x, y)
            pick.show_plane(pick.picked + [pick.points[i]] if i is not None else None)

    def _click_hook(self, view, viewport, x, y):
        if self._point_pick is not None:
            return self._point_clicked(viewport, x, y)
        if self.projecting and self.sketches.editing() is not None and self._project_other_at(view, viewport, x, y):
            return True  # Project Geometry on another Solid: copied (FreeCAD refuses to link it)
        mods = getattr(self.navigator, "press_modifiers", None)
        if mods is None:
            mods = QtWidgets.QApplication.keyboardModifiers()
        if mods & QtCore.Qt.ShiftModifier and self.sketches.editing() is not None \
                and self.feature_session is None and self.value_box is None and self.slot_tool is None \
                and not self._sketch_tool_active() and self._select_chain_at(view, viewport, x, y, True):
            return True  # Shift-click: the whole chain or shape, added to the selection
        if self.value_box is None and self.feature_session is None:
            return False
        if self.feature_session is not None and self.feature_session.unpick_under_cursor():
            return True
        sk, name = self._preselected_dimension()
        if name is None and self.value_box is not None and not self._geometry_under_cursor():
            # FreeCAD doesn't preselect dimensions while a Sketcher tool runs (the Dimension tool keeps running
            # after placing one): find the label under the click ourselves — but a point or line FreeCAD offers
            # under the cursor is what the user is aiming at (the Sketcher's own snapping must get that click)
            sk, name = self._dimension_near(view, viewport, x, y)
        if name is None:
            return False
        if self.value_box is not None:
            if name == self.value_box.name:
                return True  # the dimension being edited: nothing to insert, the click is not a tool click
            self.value_box.insert(name)
        elif not self.feature_session.insert(name):
            return False
        return True

    LABEL_PICK_PX = 12  # logical pixels around a dimension's text: on the text, not near it

    @staticmethod
    def _geometry_under_cursor():
        import FreeCADGui as Gui
        try:
            pre = Gui.Selection.getPreselection()
            subs = getattr(pre, "SubElementNames", ()) or ()
            return any(s.startswith(("Vertex", "Edge", "RootPoint", "H_Axis", "V_Axis")) for s in subs if s)
        except Exception:
            return False

    def _dimension_near(self, view, viewport, x, y):
        from incad.core import sketch as sketches
        sk = self.sketches.editing()
        if sk is None or view is None or viewport is None:
            return None, None
        try:
            dpr = viewport.devicePixelRatioF()
            px, py = x * dpr, (viewport.height() - y) * dpr  # Coin: device px, bottom-left origin
            best, best_d = None, self.LABEL_PICK_PX * dpr
            for c in sk.Constraints:
                if not c.Name:
                    continue
                pt = sketches.label_point(sk, c.Name)
                if pt is None:
                    continue
                lx, ly = view.getPointOnViewport(pt)
                d = ((lx - px) ** 2 + (ly - py) ** 2) ** 0.5
                if d < best_d:
                    best, best_d = c.Name, d
            return (sk, best) if best else (None, None)
        except Exception:
            log.logger().exception("InCad dimension pick")
            return None, None

    def _dblclick_hook(self, view, viewport, x, y):
        if self.sketches.editing() is None:
            return self._edit_plane_at(view, viewport, x, y)
        sk, name = self._preselected_pattern_copy()  # a copy opens its pattern; the original keeps its meaning
        if name is not None:
            try:
                self.edit_sketch_pattern(sk, name)
            except ValueError as e:
                log.report("warning", str(e))
            return True
        sk, name = self._preselected_dimension()
        if name is None:
            sk, name = self._dimension_near(view, viewport, x, y)  # FreeCAD preselects nothing while a tool runs
        if name is None:
            return False
        self.open_value_box(sk, name)
        return True

    CHAIN_PICK_PX = 8  # logical px around the pointer that count as a Shift-click on a line

    def _select_chain_at(self, view, viewport, x, y, adding):
        """Shift-click: the whole chain or shape of the sketch line at the click selected, coloured at once (the user:
        a shape should show it is caught right when it is selected; a double-click on a thin line was hard).
        adding: kept beside what is selected. One plain click still selects one line. Returns True on a line."""
        import FreeCADGui as Gui
        from incad.core import sketch_offset as so
        from incad.core import sketch_symmetric as sym
        from incad.ui.overall_dim_tool import OverallDimTool
        sk = self.sketches.editing()
        if sk is None or view is None or viewport is None:
            return False
        probe = OverallDimTool(view, sk)
        cx, cy = to_coin(viewport, x, y)
        pt = probe.to_sketch(cx, cy)
        dpr = viewport.devicePixelRatioF()
        far = probe.to_sketch(cx + self.CHAIN_PICK_PX * dpr, cy)
        if pt is None or far is None:
            return False
        pick = sym.pick_at(sk, pt, 0.0, (far - pt).Length)
        if pick is None or pick[0] != "edge" or pick[1] < 0:
            return False
        try:
            chain = so.outline(sk, pick[1])[0]
        except (ValueError, IndexError):
            chain = [pick[1]]
        if not adding:
            Gui.Selection.clearSelection()
        for g in chain:
            Gui.Selection.addSelection(sk.Document.Name, sk.Name, "Edge%d" % (g + 1))
        log.report("info", "Selected the whole shape (%d lines): Mirror, Offset or a pattern takes it" % len(chain))
        return True

    # ----- features -----
    def _close_feature_session(self, cancel):
        fs = self.feature_session
        if fs is not None:
            fs.cancel() if cancel else fs.ok()
        self.feature_session = None

    def _session_closed(self, fs):
        if self.__dict__.get("_feature_session") is fs:  # (raw: the property would call this normal close dead)
            self.feature_session = None
        if not getattr(fs, "_committed", False):  # the area copy made for it comes before its undo step: it stays
            try:                                   # behind on Cancel unless it goes here
                import FreeCAD as App
                from incad.core import regions
                doc = App.getDocument(fs.doc_name) if fs.doc_name in App.listDocuments() else None
                if doc is not None and regions.purge_orphans(doc):
                    doc.recompute()
            except Exception as e:
                log.logger().warning("Area copies after a cancelled dialog: %s", e)
        if self._overlay is None or getattr(self._overlay, "session", fs) is fs:
            self._drop_overlay()
        dispose(fs)
        self.schedule_refresh()

    def _profile_for(self, doc, body):
        import FreeCADGui as Gui
        from incad.core import sketch
        from incad.features.common import closed_profile, consumers, unconsumed_closed_sketches
        from incad.core import regions
        from incad.features import sketch_text
        for sel in Gui.Selection.getSelectionEx(doc.Name):
            if sketch_text.is_text(sel.Object):  # a click on the letters
                return sel.Object
            if sketch.is_sketch(sel.Object) and (closed_profile(sel.Object) or regions.needs_regions(sel.Object)):
                return sel.Object
        editing = self.sketches.editing()
        if editing is not None and (closed_profile(editing) or regions.needs_regions(editing)):
            self.sketches.finish()
            return editing
        if editing is not None:  # a sketch with only text in it: that text (one), or a click on the letters
            texts = [t for t in sketch_text.texts_of(editing) if not consumers(t)]
            if len(texts) == 1:
                self.sketches.finish()
                return texts[0]
        row = self._tree_row()  # the sketch the user clicked in the model tree (Inventor: browser pick)
        obj = doc.getObject(row) if row else None
        if obj is not None and sketch_text.is_text(obj):
            return obj
        if obj is not None and sketch.is_sketch(obj) and (closed_profile(obj) or regions.needs_regions(obj)):
            return obj
        candidates = unconsumed_closed_sketches(body)
        texts = [o for o in (body.Group if body is not None else []) if sketch_text.is_text(o) and not consumers(o)]
        if not candidates and len(texts) == 1:
            return texts[0]
        return candidates[0] if len(candidates) == 1 else None

    def _tree_row(self):
        """Name of the model tree's current row (feature or sketch), focused or not."""
        try:
            from incad.ui.browser import KIND_ROLE, NAME_ROLE
            item = self.browser.tree.currentItem() if self.browser is not None else None
            if item is not None and item.data(0, KIND_ROLE) in ("feature", "sketch"):
                return item.data(0, NAME_ROLE)
        except RuntimeError:
            pass
        return None

    def _profile_from_areas(self, kind, doc, body, source):
        """Inventor: the areas between the sketch's lines are the profiles. One area: used at once. Several: they
        are shown and the user clicks the one to use (Esc cancels)."""
        from incad.core import regions
        faces = regions.regions(source)
        if not faces:
            raise ValueError("%s has no closed area to %s" % (source.Label, kind.TITLE.lower()))
        if len(faces) == 1 or regions.islands(faces):  # one area, or separate shapes (a pattern of polygons): all
            prof = regions.make_profile(doc, body, source, faces if len(faces) > 1 else faces[0])
            self._open_session(kind, doc, body, profile=prof)
            return
        self._cancel_region_pick()
        doc.openTransaction("Pick area")  # the shown areas vanish with the pick (aborted): no undo step
        names = []
        pl = source.getGlobalPlacement()
        for i, f in enumerate(faces):
            o = doc.addObject("Part::Feature", "Area%d" % (i + 1))
            o.Label = "Area %d" % (i + 1)
            o.addProperty("App::PropertyBool", "InCadAreaPick", "InCad", "Shown while an area is picked")
            shape = f.copy()
            shape.Placement = pl
            o.Shape = shape
            try:
                vo = o.ViewObject
                vo.ShapeColor = (0.95, 0.62, 0.29)
                vo.Transparency = 55
                vo.LineWidth = 1.0
            except Exception:
                pass
            names.append(o.Name)
        doc.recompute()
        import FreeCADGui as Gui
        self._region_pick = _RegionPicker(self, kind, doc.Name, body.Name, source.Name, names, faces)
        Gui.Selection.clearSelection()
        Gui.Selection.addObserver(self._region_pick)
        self.status.set_prompt("Click the area to %s (Esc cancels)" % kind.TITLE.lower())
        log.report("info", "%d areas: click the one to %s" % (len(faces), kind.TITLE.lower()))
        self.schedule_refresh()

    def _purge_stale_areas(self, doc):
        """The "Area n" faces shown while an area is picked are gone once it is over; a pick left open (another
        command started) left them in the part, over its edges (Chamfer then picked them). Removed when no pick
        runs: the marked ones, and older unmarked ones (a Part::Feature "Area n" outside every Solid, used by
        nothing)."""
        if doc is None or self._region_pick is not None:
            return
        stale = []
        for o in doc.Objects:
            if o.TypeId != "Part::Feature" or not re.match(r"^Area\d+$", o.Name):
                continue
            if "InCadAreaPick" in o.PropertiesList or (re.match(r"^Area \d+$", o.Label) and not o.InList
                                                        and o.getParentGeoFeatureGroup() is None):
                stale.append(o.Name)
        for name in stale:
            try:
                doc.removeObject(name)
            except Exception:
                pass
        if stale:
            log.logger().info("Removed stale area faces %s", stale)

    def _cancel_region_pick(self):
        import FreeCAD as App
        import FreeCADGui as Gui
        pick = self._region_pick
        if pick is None:
            return
        self._region_pick = None
        try:
            Gui.Selection.removeObserver(pick)
        except Exception:
            pass
        doc = App.getDocument(pick.doc_name) if pick.doc_name in App.listDocuments() else None
        if doc is not None and doc.HasPendingTransaction:
            doc.abortTransaction()  # the shown areas go
            doc.recompute()
        self.schedule_refresh()

    def _region_picked(self, pick, index):
        import FreeCAD as App
        from incad.core import regions
        face, kind = pick.faces[index], pick.kind
        doc = App.getDocument(pick.doc_name)
        self._cancel_region_pick()
        body, source = doc.getObject(pick.body_name), doc.getObject(pick.source_name)
        if body is None or source is None:
            return
        try:
            prof = regions.make_profile(doc, body, source, face)
            self._open_session(kind, doc, body, profile=prof)
        except Exception as e:
            log.report("error", "%s: %s" % (kind.TITLE, e), traceback.format_exc())

    def start_feature(self, kind):
        from incad.core import assembly, document
        doc = self._doc()
        if getattr(kind, "ASSEMBLY", False):
            if not assembly.is_assembly(doc):
                raise ValueError("Open an InCad assembly first")
            if self.feature_session is not None:
                raise self._dialog_first()
            self._open_session(kind, doc, None)
            return
        if not document.is_incad_part(doc):
            raise ValueError("Create or open an InCad part first")
        if getattr(kind, "IN_SKETCH", False):
            editing = self.sketches.editing()
            if editing is None:
                raise ValueError("Open a sketch first")
            if self.feature_session is not None:
                raise self._dialog_first()
            self._leave_sketch_tools()
            import FreeCADGui as Gui
            picked = [(sel.Object.Name, sub) for sel in Gui.Selection.getSelectionEx(doc.Name)
                      for sub in (sel.SubElementNames or ("",))]
            Gui.runCommand("Sketcher_StopOperation")  # no Sketcher tool fighting for the clicks; it clears the
            for name, sub in picked:                   # selection, so the geometry picked beforehand is put back
                Gui.Selection.addSelection(doc.Name, name, sub)
            self._open_session(kind, doc, document.primary_body(doc), profile=editing)
            return
        if self.feature_session is not None:
            raise self._dialog_first()
        body = self._selected_body(doc) or document.primary_body(doc)
        if not getattr(kind, "NEEDS_PROFILE", True):
            self._open_session(kind, doc, body)
            return
        profile = self._profile_for(doc, body)
        if profile is None:
            self._begin_profile_pick(kind)
            return
        body = document.target_body(profile) or body  # a sketch on a face of a second Solid: the feature goes there
        from incad.core import regions
        from incad.features import sketch_text
        if not sketch_text.is_text(profile) and regions.needs_regions(profile):  # (letters are faces already)
            self._profile_from_areas(kind, doc, body, profile)
            return
        self._open_session(kind, doc, body, profile=profile)

    def edit_sketch_pattern(self, sk, name):
        from incad.features import sketch_patterns as kinds
        from incad.core import document, sketch_patterns as sp
        rec = sp.find(sk, name)
        if rec is None:
            raise ValueError("No pattern named %s in %s" % (name, sk.Label))
        if self.feature_session is not None:
            raise self._dialog_first()
        kind = {"rect": kinds.SKETCH_RECT, "circ": kinds.SKETCH_CIRC, "path": kinds.SKETCH_PATH}[rec["kind"]]
        kinds._set_editing(sk, name)
        if sp.is_broken(sk, name):
            log.report("warning", "Some copies of %s were deleted; the pattern will be rebuilt" % name)
        self._leave_sketch_tools()
        import FreeCADGui as Gui
        Gui.runCommand("Sketcher_StopOperation")
        self._open_session(kind, sk.Document, document.primary_body(sk.Document), profile=sk, feature=sk)

    def _leave_sketch_tools(self):
        """A sketch pattern dialog takes the sketch over: InCad's own tools stop, and the construction drawing
        mode ends (its hook would turn every copy the dialog adds into construction geometry)."""
        self.stop_slot_tool()
        if self.construction_mode:
            self.construction_mode = False
            log.report("info", "Construction off: normal geometry")
            if self.ribbon is not None:
                self.ribbon.refresh_checks()

    def delete_sketch_pattern(self, sk, name):
        from incad.core import safe, sketch_patterns as sp
        if self.feature_session is not None:
            raise self._dialog_first()
        r = safe.run_safely("Delete Pattern", lambda: sp.remove(sk.Document, sk, name), sk.Document)
        if not r.ok:
            raise ValueError(r.error)
        safe.recompute(sk.Document)
        self.schedule_refresh()

    def selected_sketch_pattern(self):
        """(sketch, pattern name) of the pattern whose geometry is selected in the sketch being edited."""
        import FreeCADGui as Gui
        from incad.core import sketch_patterns as sp
        sk = self.sketches.editing()
        if sk is None:
            return None, None
        for sel in Gui.Selection.getSelectionEx(sk.Document.Name):
            if sel.Object is not sk:
                continue
            for sub in sel.SubElementNames or ():
                try:
                    geo, _ = sp.element(sk, sub)
                except ValueError:
                    continue
                if geo >= 0:
                    name = sp.pattern_of(sk, geo)
                    if name:
                        return sk, name
        return None, None

    def _preselected_pattern_copy(self):
        """(sketch, pattern) of the pattern copy under the cursor: the preselection, or, when FreeCAD still holds a
        stale one (an object gone with an earlier Cancel), the selection the double-click's first click made."""
        import FreeCADGui as Gui
        from incad.core import sketch_patterns as sp
        candidates = []
        try:
            pre = Gui.Selection.getPreselection()
            obj = getattr(pre, "Object", None)
            if obj is not None and obj.TypeId == "Sketcher::SketchObject":
                candidates.append((obj, list(getattr(pre, "SubElementNames", ()) or ())))
        except RuntimeError:
            pass
        sk = self.sketches.editing()
        if sk is not None:
            for sel in Gui.Selection.getSelectionEx(sk.Document.Name):
                if sel.Object is sk:
                    candidates.append((sk, list(sel.SubElementNames or ())))
        for obj, subs in candidates:
            for sub in subs:
                try:
                    geo, _ = sp.element(obj, sub)
                except ValueError:
                    continue
                if geo >= 0 and sp.is_copy(obj, geo):
                    return obj, sp.pattern_of(obj, geo)
        return None, None

    def edit_feature(self, feature):
        from incad import features
        kind = features.kind_for(feature)
        if kind is None:
            raise ValueError("%s cannot be edited yet" % feature.Label)
        if self.feature_session is not None:
            raise self._dialog_first()
        body = None if getattr(kind, "ASSEMBLY", False) else feature.getParentGeoFeatureGroup()
        sk = getattr(feature, "Sketch", None) if getattr(kind, "IN_SKETCH", False) else None
        if sk is not None:  # a Text: edited inside its sketch (its point and curve are picked there)
            if self.sketches.editing() is not sk:
                self.sketches.edit(sk)
            self._leave_sketch_tools()
            self._open_session(kind, feature.Document, body, profile=sk, feature=feature)
            return
        self._open_session(kind, feature.Document, body, feature=feature)

    @staticmethod
    def _selected_body(doc):
        """The Solid the 3D selection belongs to (a face of a box drawn away from the part), or None."""
        import FreeCADGui as Gui
        for sel in Gui.Selection.getSelectionEx(doc.Name):
            owner = sel.Object.getParentGeoFeatureGroup() if hasattr(sel.Object, "getParentGeoFeatureGroup") else None
            if owner is not None and owner.TypeId == "PartDesign::Body":
                return owner
        return None

    def _open_session(self, kind, doc, body, profile=None, feature=None):
        parent = self._mdi().viewport() if self._mdi() is not None else self.mw
        fs = FeatureSession(kind, doc, body, profile=profile, feature=feature, theme=self.theme, parent=parent,
                            on_close=self._session_closed)
        self.feature_session = fs
        try:
            started = fs.start()
        except Exception:
            self.feature_session = None  # a start that raised must not leave a 'dialog still open' behind
            if getattr(kind, "IN_SKETCH", False) and profile is not None:
                try:
                    profile.InCadEditing = ""  # nor a pattern marked as open in the dialog
                except Exception:
                    pass
            dispose(fs)
            self.schedule_refresh()
            raise
        if not started:
            self.feature_session = None
        hook = getattr(kind, "overlay", None)
        if started:
            self._drop_gear_gizmo()  # a dialog has the mouse now (its own gizmo, if any)
        if started and hook is not None and self.feature_session is fs and fs.dialog is not None:
            try:  # (an Esc or OK during the first preview closed it already)
                self._overlay = hook(self, fs)
                fs.on_previewed = lambda: self._overlay.refresh() if self._overlay is not None else None
                if self.navigator is not None:
                    self.navigator.handle_hook = self._overlay
            except Exception:
                log.logger().exception("%s overlay", kind.TITLE)
                self._drop_overlay()
        self.schedule_refresh()

    def _selected_gear(self):
        """The set's first gear when exactly one gear (or worm) is selected in the active part and nothing else owns
        the mouse; else None."""
        import FreeCAD as App
        from incad.core import assembly
        from incad.features import common as _common
        from incad.features import gear
        if self.feature_session is not None or self.sketches.editing() is not None or any(
                getattr(self, a, None) is not None for a in ("_point_pick", "_region_pick", "_profile_pick")):
            return None
        doc = App.ActiveDocument
        if doc is None or assembly.is_assembly(doc):
            return None
        sels = _common.selection_ex(doc.Name)
        if len(sels) != 1:
            return None
        sel = sels[0]
        subs = sel.SubElementNames or ("",)
        found = set()
        for sub in subs:
            obj, el = _common.resolve(sel.Object, sub)
            g = gear.gear_of(obj, el)
            if g is None:
                return None
            found.add(gear.lead_of(g).Name)
        if len(found) != 1:
            return None
        lead = doc.getObject(found.pop())
        return lead if gear.gizmo_frame(lead) is not None else None

    def _sync_gear_gizmo(self):
        """The gizmo on the selected gear comes, follows the gear (undo, an edit) or goes."""
        from incad.ui.gear_gizmo import GearGizmo
        cur = self._gear_gizmo
        if cur is not None and cur.dragging():
            return
        want = self._selected_gear() if self.active else None
        if cur is not None and (want is None or cur.target is not want):
            self._drop_gear_gizmo()
            cur = None
        if want is None:
            return
        if cur is None:
            self._gear_gizmo = GearGizmo(self, feature=want)
            if self.navigator is not None:
                self.navigator.handle_hook = self._gear_gizmo
        else:
            cur.refresh()

    def _drop_gear_gizmo(self):
        g, self._gear_gizmo = self._gear_gizmo, None
        if g is None:
            return
        try:
            g.remove()
        except Exception:
            log.logger().exception("gear gizmo")
        if self.navigator is not None and self.navigator.handle_hook is g:
            self.navigator.handle_hook = self.dof_handles

    def _drop_overlay(self):
        overlay, self._overlay = self._overlay, None
        if overlay is not None:
            try:
                overlay.remove()
            except Exception:
                log.logger().exception("InCad overlay")
        if self.navigator is not None and self.navigator.handle_hook is not self.dof_handles:
            self.navigator.handle_hook = self.dof_handles

    def _begin_profile_pick(self, kind):
        import FreeCADGui as Gui
        self._cancel_profile_pick()
        self._profile_pick = _ProfilePicker(self, kind)
        Gui.Selection.clearSelection()
        Gui.Selection.addObserver(self._profile_pick)
        self.status.set_prompt("Select a sketch to %s (Esc cancels)" % kind.TITLE.lower())
        log.report("info", "Select a sketch with a closed profile")

    def _cancel_profile_pick(self):
        import FreeCADGui as Gui
        if self._profile_pick is not None:
            try:
                Gui.Selection.removeObserver(self._profile_pick)
            except Exception:
                pass
            self._profile_pick = None
            self.schedule_refresh()

    # ----- hooks -----
    def _install_hooks(self):
        import FreeCADGui as Gui
        if getattr(self, "_esc_computing", None) is None:
            self._esc_computing = _EscWhileComputing(self.mw)
            QtWidgets.QApplication.instance().installEventFilter(self._esc_computing)
        if self._sel_watch is None:
            self._sel_watch = _SelectionWatch(self)
            Gui.Selection.addObserver(self._sel_watch)
        if self._hover_watch is None:
            from incad.ui.dof_glyphs import HoverWatch
            self._hover_watch = HoverWatch(self._on_dof_hover)
            Gui.Selection.addObserver(self._hover_watch)
        import FreeCAD as App
        log.set_reporter(self._report)
        from incad.core import safe
        safe.set_refused_hook(self._show_open_dialog)
        from incad.core import commands
        commands.set_before_run_hook(self._end_drag)
        self.mw.installEventFilter(self)
        for w in self._freecad_toolbars() + self._freecad_docks():
            w.installEventFilter(self)
        self._observer = _DocObserver(self.schedule_refresh, self._sketch_changed, self._after_undo,
                                      self._document_activated, self._document_deleted, self._projection_changed)
        App.addDocumentObserver(self._observer)
        QtWidgets.QApplication.instance().styleHints().colorSchemeChanged.connect(self._on_system_scheme)
        from incad.core import params
        mdi = self._mdi()
        if mdi is not None:
            mdi.viewport().installEventFilter(self)
            mdi.subWindowActivated.connect(self._raise_strip)
            mdi.subWindowActivated.connect(self._on_subwindow)
        if prefs.navigation_enabled():
            params.write(themes.VIEW_GROUP, NAV_VALUES)
            self.navigator.attach_all()
        fresh_redraw.attach_all()  # a sketch's view keeps up with the mouse

    def _remove_hooks(self):
        import FreeCAD as App
        mdi = self._mdi()
        if mdi is not None:
            try:
                mdi.viewport().removeEventFilter(self)
                mdi.subWindowActivated.disconnect(self._raise_strip)
                mdi.subWindowActivated.disconnect(self._on_subwindow)
            except (RuntimeError, TypeError):
                pass
        if self.navigator is not None:
            self.navigator.detach_all()
        fresh_redraw.detach_all()
        log.set_reporter(None)
        from incad.core import safe
        safe.set_refused_hook(None)
        from incad.core import commands
        commands.set_before_run_hook(None)
        if self._box_focus_hooked:
            try:
                QtWidgets.QApplication.instance().focusChanged.disconnect(self._keep_box_keyboard)
            except (RuntimeError, TypeError):
                pass
            self._box_focus_hooked = False
        for w in [self.mw] + self._freecad_toolbars() + self._freecad_docks():
            try:
                w.removeEventFilter(self)
            except RuntimeError:
                pass
        if self._observer is not None:
            try:
                App.removeDocumentObserver(self._observer)
            except Exception:
                pass
            self._observer = None
        try:
            QtWidgets.QApplication.instance().styleHints().colorSchemeChanged.disconnect(self._on_system_scheme)
        except (RuntimeError, TypeError):
            pass

    @perf.timed("window events")
    def eventFilter(self, obj, event):
        try:
            etype = event.type()
            # only these matter; anything else returns before touching Qt (widgets being destroyed on quit send
            # events through here, and a findChild on them aborted FreeCAD at exit)
            if etype not in _FILTERED_EVENTS:
                return False
            if etype == QtCore.QEvent.Resize:
                mdi = self._mdi()
                if self.strip is not None and mdi is not None and obj is mdi.viewport():
                    self.strip.place(mdi.viewport().rect())
                    QtCore.QTimer.singleShot(0, self._raise_overlays)  # QMdiArea re-raises the document window
                return False
            if obj is self.mw:
                if etype == QtCore.QEvent.Close and not self.active:
                    shutdown()  # still waiting for a FreeCAD task: nothing to ask, just leave nothing behind
                elif etype == QtCore.QEvent.Close and self.active:
                    # quitting (⌘Q, Dock, app menu, red button): ask about unsaved files while InCad still shows
                    # (Cancel keeps everything), then switch off — FreeCAD saves its layout right after — and
                    # delete InCad's Qt objects now: macOS ends the process inside this close (probed crash)
                    if not _ask_unsaved():
                        event.ignore()
                        return True
                    QtCore.QTimer.singleShot(0, _resume_after_cancelled_quit)  # only runs if the quit is cancelled
                    shutdown()  # deletes this object: nothing may touch self after it
                    _mark_answered()  # switching off may have touched documents: already answered
                elif etype == QtCore.QEvent.ChildAdded and self.active:
                    child = event.child()
                    if isinstance(child, (QtWidgets.QToolBar, QtWidgets.QDockWidget)):
                        QtCore.QTimer.singleShot(0, lambda c=child: self._adopt(c))
            elif etype == QtCore.QEvent.Show and self.active and obj.objectName() not in OURS \
                    and (isinstance(obj, (QtWidgets.QToolBar, QtWidgets.QDockWidget)) or _is_overlay(obj)):
                QtCore.QTimer.singleShot(0, lambda o=obj: self._rehide(o))
        except Exception:
            log.logger().exception("InCad event filter")
        return False

    def _adopt(self, widget):
        try:
            if not self.active or widget.objectName() in OURS or widget.parent() is not self.mw:
                return
            if not widget.isHidden():
                self._hidden_by_us.add(widget.objectName())
            widget.hide()
            widget.installEventFilter(self)
        except RuntimeError:
            pass

    def _rehide(self, widget):
        try:
            if self.active and not widget.isHidden():
                self._hidden_by_us.add(widget.objectName())
                widget.hide()
        except RuntimeError:
            pass

    def _on_system_scheme(self, *_):
        if prefs.theme() == "auto":
            self.apply_theme()

    def _report(self, level, message):
        if self.status is not None:
            self.status.show_message(level, message)

    # ----- panels -----
    def _sync_sketch_markers(self, doc, editing):
        """Key points of every shown unused sketch, drawn on the part view; none while a sketch is edited or a
        feature dialog shows its own. An unused open contour the user never hid is shown here too (a file saved
        before, or FreeCAD, may have hidden it); closed sketches are left as they are (a sketch just finished must
        stay in view for the feature that will use it)."""
        import FreeCAD as App
        import FreeCADGui as Gui
        from incad.core import document, sketch as sketches
        from incad.ui.point_markers import PointMarkers
        if not hasattr(self, "sketch_markers"):
            self.sketch_markers = {}  # document name -> (view, PointMarkers)
        for name in [n for n in self.sketch_markers if n not in App.listDocuments()]:
            self.sketch_markers.pop(name)  # the view went with its document
        points, view = [], None
        if doc is not None and editing is None and self.feature_session is None:
            gdoc = Gui.getDocument(doc.Name)
            view = gdoc.ActiveView if gdoc is not None else None
            body = document.primary_body(doc)
            if body is not None and view is not None:
                for sk in body.Group:
                    if sk.TypeId != "Sketcher::SketchObject":
                        continue
                    try:
                        peeked = self._peeked is not None and self._peeked[1] == sk.Name
                        if not sk.Visibility and document.SHOWN_PROP not in sk.PropertiesList \
                                and document.sketch_shown(sk):
                            sk.Visibility = True
                        if sk.Visibility and (peeked or document.SHOWN_PROP in sk.PropertiesList
                                              or document.is_unused_sketch(sk)):
                            points.extend(sketches.key_points(sk))
                    except Exception as e:
                        log.logger().warning("Key points of %s: %s", sk.Name, e)
        if doc is None:
            for _, m in self.sketch_markers.values():
                m.clear()
            return
        entry = self.sketch_markers.get(doc.Name)
        try:
            if view is None:
                if entry is not None:
                    entry[1].clear()
                return
            if entry is None:
                entry = self.sketch_markers[doc.Name] = (view, PointMarkers(view, self.theme))
            entry[1].set(points)
        except Exception as e:
            log.logger().warning("Sketch markers: %s", e)

    def _drop_sketch_markers(self):
        for _, m in getattr(self, "sketch_markers", {}).values():
            m.remove()
        self.sketch_markers = {}

    @staticmethod
    def _body_hidden(doc):
        from incad.core import document
        try:
            body = document.primary_body(doc)
            return body is not None and not body.Visibility and body.Tip is not None
        except Exception:
            return False

    def schedule_refresh(self):
        if self._refresh_pending or self.ribbon is None:
            return
        self._refresh_pending = True
        QtCore.QTimer.singleShot(0, self.refresh)

    @perf.timed("panel refresh")
    def refresh(self):
        self._refresh_pending = False
        if self.ribbon is None:
            return
        try:
            self._sync_gear_gizmo()
        except Exception:
            log.logger().exception("gear gizmo")
        import FreeCAD as App
        if any(getattr(App.getDocument(n), "Restoring", False) for n in App.listDocuments()):
            # a file is still loading: FreeCAD pumps events for its progress bar, and a refresh now would
            # recompute a half-restored document (the Sketcher crashed on a projected edge not yet linked)
            QtCore.QTimer.singleShot(300, self.schedule_refresh)
            return
        if any(getattr(App.getDocument(n), "Recomputing", False) for n in App.listDocuments()):
            # the same progress bar during a recompute: the panels would show a half-recomputed part, many times
            QtCore.QTimer.singleShot(150, self.schedule_refresh)
            return
        try:
            self._refresh()
        except Exception as e:
            log.report("warning", "InCad could not update its panels: %s" % e, traceback.format_exc())

    def _purge_old_markers(self, doc):
        from incad.core import section
        try:
            if section.purge_markers(doc):
                doc.recompute()
        except Exception as e:
            log.logger().warning("Could not remove old section markers: %s", e)

    def _refresh(self):
        import FreeCAD as App
        from incad.core import document, tree
        doc = App.ActiveDocument
        fs = self.feature_session
        if fs is not None and fs.doc_name not in App.listDocuments():
            self._close_feature_session(cancel=True)  # its document was closed: nothing to roll back, free busy
        if self._solid_edit is not None and (doc is None or doc.Name != self._solid_edit[0]
                                             or doc.getObject(self._solid_edit[1]) is None):
            self.end_solid_edit(quiet=True)  # another file in front, or the Solid deleted: everything as before
        from incad.core import assembly as assemblies
        if doc is not None and fs is None:
            self._purge_stale_areas(doc)
        if doc is not None and fs is None and doc.Name not in self._adopt_tried and not document.is_incad_part(doc) \
                and not assemblies.is_assembly(doc) and not getattr(doc, "Restoring", False) \
                and not getattr(doc, "Recomputing", False) and self._in_its_tab(doc):
            self._adopt_tried.add(doc.Name)
            self._adopt_foreign(doc)
        is_part = document.is_incad_part(doc)
        is_asm = assemblies.is_assembly(doc)
        editing = self.sketches.editing() if is_part else None
        try:
            self._tune_tabs()
        except Exception:
            log.logger().exception("file tabs")
        try:
            from incad.ui import wire_style
            wire_style.refresh(doc, self.theme)  # objects made while Wireframe is on; a file saved in it
        except Exception as e:
            log.logger().warning("Wireframe lines: %s", e)
        try:  # coloured faces found again after a recompute renumbered them; textures on a file opened (also a part
            from incad.core import appearance  # an assembly opened: its components show the part's view)
            for n in App.listDocuments():
                d = App.getDocument(n)
                if not getattr(d, "Restoring", False) and document.is_incad_part(d):
                    appearance.apply_all(d)
        except Exception as e:
            log.logger().warning("Appearance: %s", e)
        try:
            from incad.core import quality
            quality.apply_all()  # files opened or made since: drawn at ⚙ ▸ Display Quality (once each)
        except Exception as e:
            log.logger().warning("Display quality: %s", e)
        self.sketches.sync()
        if is_part and editing is None and fs is None and self._body_hidden(doc):
            document.show_result(document.primary_body(doc))  # a file saved during sketch editing opens blank
        self._visibility_applied &= set(App.listDocuments())  # a closed file opened again is a new document
        if is_part and editing is None and fs is None and doc.Name not in self._visibility_applied:
            self._visibility_applied.add(doc.Name)  # once per document: a file saved with a feature's sketch
            try:                                     # left visible (an older InCad, FreeCAD's Space key) opens clean
                document.apply_visibility(doc)
            except Exception as e:
                log.logger().warning("Visibility on open: %s", e)
            try:  # parameters lose their units in the file (FreeCAD does not save a quantity's unit)
                from incad.core import model
                if not getattr(doc, "Restoring", False):
                    model.restore_units(doc)
            except Exception as e:
                log.logger().warning("Parameter units on open: %s", e)
            try:  # a Boolean's clearance grew the whole tool on every change (a jar's lid: 8 s): its near piece now
                from incad.features import combine
                if not getattr(doc, "Restoring", False):
                    combine.show_results(doc)
                    done = combine.upgrade(doc)
                    if done:
                        log.report("info", "%s: the clearance is now computed only near the cut (faster)"
                                   % ", ".join(done))
            except Exception as e:
                log.logger().warning("Boolean clearance on open: %s", e)
            try:  # a Split made before Keep all gave each piece its own Solid: its pieces get their Solids now
                from incad.features import split
                if not getattr(doc, "Restoring", False) and split.upgrade(doc):
                    log.report("info", "Split pieces are separate Solids now (hide or show each one)")
            except Exception as e:
                log.logger().warning("Split pieces on open: %s", e)
            try:  # a Solid sketched on another Solid's face held that whole Solid too (older InCad): settle them
                if not getattr(doc, "Restoring", False):
                    settled = document.settle_cross_solid_sketches(doc)
                    if settled:
                        doc.recompute()
                        log.report("info", "%s: on another Solid's face, now holds only its own piece"
                                   % ", ".join(s.Label for s in settled))
            except Exception as e:
                log.logger().warning("Sketches on another Solid's face on open: %s", e)
            try:  # area copies whose feature was deleted outside InCad: nothing uses them, they go
                from incad.core import regions
                if regions.purge_orphans(doc):
                    doc.recompute()
            except Exception as e:
                log.logger().warning("Orphan area copies on open: %s", e)
            try:  # suppressed tapped holes computed their thread on every recompute (3.6 s each)
                from incad.core import suppress
                suppress.quiet_suppressed_holes(doc)
            except Exception as e:
                log.logger().warning("Suppressed holes on open: %s", e)
            try:  # FreeCAD leaves every sketch Text touched after loading: the first edit anywhere recomputed it and
                # everything after it (4.6 s on a Mac, 15 s on a slow PC); its saved shape is the computed one
                from incad.features import sketch_text
                for t in [o for o in doc.Objects if sketch_text.is_text(o)]:
                    if "Touched" in t.State and not t.Shape.isNull() and "Invalid" not in t.State:
                        t.purgeTouched()
            except Exception as e:
                log.logger().warning("Texts on open: %s", e)
            self._adopt_all_projections(doc.Name)  # after the purge: an orphan area copy uses nothing
        if self.value_box is not None and (editing is None or
                                           (editing.Document.Name, editing.Name) != self._box_owner):
            self._close_value_box(apply_if_typed=True)
        if self.constraints_panel is not None and (editing is None or
                                                   (editing.Document.Name, editing.Name) != self._constraints_owner):
            self.close_constraints_window()
        if editing is not None and self._peeked is not None:
            self.forget_peek()  # FreeCAD shows the sketch for editing and restores it after
        if editing is not None and self._profile_pick is not None:
            self._cancel_profile_pick()  # the user went sketching instead: a pick would finish the sketch under them
        if editing is None and self.slot_tool is not None:
            self.stop_slot_tool()
        if editing is None and self.construction_mode:
            self.construction_mode = False  # each sketch starts with normal geometry
        if self.sketches.picking and self.sketches.pick_doc != getattr(doc, "Name", None):
            self.sketches.cancel_pick()  # another tab in front: the plane pick was for the part left behind
        mode = "sketch" if editing is not None else "part" if is_part else "assembly" if is_asm else "empty"
        self.ribbon.set_mode("part_edit" if mode == "part" and self._active_context() is not None else mode)
        self._sync_start_page(doc is None)
        if self.plane_panel is not None:
            if self.sketches.picking and not self.plane_panel.isVisible():
                self.plane_panel.adjustSize()
                self.plane_panel.move(16, 16)
                self.plane_panel.show()
                self.plane_panel.raise_()
            elif not self.sketches.picking:
                self.plane_panel.hide()
        self.title.set_document_name(doc.Label if doc is not None else "")
        if is_part and editing is None and not getattr(doc, "Restoring", False) \
                and not getattr(doc, "Recomputing", False) and not doc.HasPendingTransaction:
            try:
                document.settle_end_marker(doc)  # End of Part above sketches, then a feature added after it
            except Exception:
                log.logger().exception("End of Part mark")
        self.browser.set_nodes(tree.build(doc))
        self._sync_sketch_markers(doc if is_part else None, editing)
        from incad.core import sketch as sketches
        self.browser.set_parameters(document.list_parameters(doc) if is_part or is_asm else [],
                                    sketches.model_parameters(doc) if is_part or is_asm else [])
        self.browser.set_parameters_enabled(is_part or is_asm)
        self.refresh_parameters_window(doc if is_part or is_asm else None)
        self.status.set_units(document.part_units(doc) if is_part or is_asm else None)
        self.status.set_dof(editing.DoF if editing is not None else None)
        if is_part:
            bodies = document.bodies(doc)
            features = sum(1 for b in bodies for o in b.Group if o.TypeId != "Sketcher::SketchObject")
            self.status.set_stats(len(bodies), features)
            if editing is not None:
                self.status.set_prompt("Sketch: draw and dimension, then Finish Sketch (Esc)")
            elif self.sketches.picking:
                self.status.set_prompt("Pick a plane or a flat face for the new sketch (Esc cancels)")
            else:
                self.status.set_prompt("Ready")
        elif is_asm:
            self._tame_padlocks(doc)
            self._mark_grounded(doc)
            if self.feature_session is None:
                from incad.core import asm_cut
                try:
                    asm_cut.sync(doc)  # cut parts follow the tools' positions
                except Exception as e:
                    log.report("warning", "Could not update the assembly cuts: %s" % e, traceback.format_exc())
            self.status.set_stats(None, None)
            comps = len(assemblies.components(doc))
            self.status.set_prompt("Place part files (P), then constrain them (C)" if comps < 2 else
                                   "%d components · Constrain (C), Joint (J), Ground" % comps)
        else:
            self.status.set_stats(None, None)
            self.status.set_prompt("Create or open a part" if doc is None else host.brand("This file was not made in InCad"))
        if doc is not None and self._point_pick is None:
            self._purge_old_markers(doc)
        if self._region_pick is not None:
            self.status.set_prompt("Click the area to %s (Esc cancels)" % self._region_pick.kind.TITLE.lower())
        if self._point_pick is not None:
            pick = self._point_pick
            self.status.set_prompt(pick.prompt if not pick.picked else
                                   "%d of %d points picked (Esc cancels)" % (len(pick.picked), pick.count))
        for cmd_id, act in self._shortcuts.items():
            if cmd_id == "_esc":
                act.setEnabled(mode == "sketch" or self.sketches.picking or self._profile_pick is not None or
                               getattr(self, "slot_tool", None) is not None or self.feature_session is not None
                               or self._measure is not None or self._region_pick is not None
                               or self.section_panel is not None or self._point_pick is not None
                               or self._solid_edit is not None)
                continue
            ctx = self.registry.get(cmd_id).contexts
            act.setEnabled(commands.ANY in ctx or mode in ctx)
        if self.navigator is not None and prefs.navigation_enabled():
            self.navigator.attach_all()
        fresh_redraw.attach_all()  # new views too
        if self.strip is not None:
            self.strip.refresh_checks()
        self._raise_overlays()
        if self._wait_notice is not None and not self._edit_open():
            self._close_wait_notice()
        if is_asm and fs is None:
            self._schedule_dof(doc)
        self._schedule_interference(doc)  # off where it was not turned on
        self._section_follow(doc)  # an open section cut again when the part changed
        self.dof_display_changed()  # a tab switch, an undo or a finished drag: glyphs at the parts' new places

    # ----- assemblies -----
    def _assembly_doc(self):
        from incad.core import assembly
        doc = self._doc()
        if not assembly.is_assembly(doc):
            raise ValueError("Open an InCad assembly first")
        if self.feature_session is not None:
            raise self._dialog_first()
        return doc

    def _ensure_saved(self, doc, folder=""):
        """Linking needs the assembly on disk: ask for its file the first time."""
        if doc.FileName:
            return True
        QtWidgets.QMessageBox.information(self.mw, "Save the assembly",
                                          "The assembly links to part files, so it is saved first: choose a name and "
                                          "a folder for the assembly itself.")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self.mw, "Save the assembly (not the part)",
                                                        os.path.join(folder, doc.Label + ".FCStd"),
                                                        "FreeCAD files (*.FCStd)")
        if not path:
            return False
        doc.saveAs(path if path.lower().endswith(".fcstd") else path + ".FCStd")
        return True

    def _back_to(self, doc):
        """Opening part files can switch the active tab: show the assembly again."""
        import FreeCAD as App
        import FreeCADGui as Gui
        App.setActiveDocument(doc.Name)
        gdoc = Gui.getDocument(doc.Name)
        if gdoc is not None and gdoc.ActiveView is not None:
            Gui.ActiveDocument = gdoc
            mdi = self._mdi()
            for sub in (mdi.subWindowList() if mdi is not None else []):
                if sub.windowTitle().startswith(doc.Label):
                    mdi.setActiveSubWindow(sub)
                    break

    def assembly_place(self, path=None):
        """Place: choose the part (or assembly) file first; a new assembly not saved yet is saved next to it (the
        user took the old 'Save the assembly first' dialog for the part chooser: its files are greyed out)."""
        from incad.core import assembly, log, safe
        doc = self._assembly_doc()
        if path is None:
            from incad.ui import open_dialog
            path, _ = open_dialog.get_open_file(self.mw, "Place a part or assembly", os.path.dirname(doc.FileName or ""),
                                                "FreeCAD files (*.FCStd)")
            if not path:
                return None
        if not doc.FileName:
            import re
            if re.match(r"^(Assembly|Unnamed)\d*$", doc.Label):  # not named yet: the part's name + _assy (the user)
                doc.Label = os.path.splitext(os.path.basename(path))[0] + "_assy"
            target = os.path.join(os.path.dirname(path), doc.Label + ".FCStd")
            if os.path.exists(target) or os.path.abspath(target) == os.path.abspath(path):
                if not self._ensure_saved(doc, os.path.dirname(path)):
                    return None
            else:
                doc.saveAs(target)
                log.report("info", "Assembly saved as %s (next to the part)" % target)
        result = safe.run_safely("Place", lambda: assembly.place(doc, path), doc)
        self._back_to(doc)
        self.schedule_refresh()
        return result.value if result.ok else None

    def assembly_create(self, name=None):
        from incad.core import assembly, safe
        doc = self._assembly_doc()
        if not self._ensure_saved(doc):
            return None
        if name is None:
            name, ok = QtWidgets.QInputDialog.getText(self.mw, "Create component", "New part file name:",
                                                      text="Part")
            if not ok or not name.strip():
                return None
        result = safe.run_safely("Create", lambda: assembly.create_part(doc, name.strip()), doc)
        self.schedule_refresh()
        return result.value if result.ok else None

    def _picked_component(self, doc, link, what):
        """The component the command acts on: given, the row the menu was opened on, or the 3D / tree pick."""
        import FreeCADGui as Gui
        from incad.core import assembly, context
        from incad.features import common
        if link is None:
            name = context.target()
            link = doc.getObject(name) if name else None
        if link is None:
            for sel in common.selection_ex(doc.Name):
                for sub in (sel.SubElementNames or ("",)):
                    obj, _ = common.resolve(sel.Object, sub)
                    if obj is not None and obj.TypeId == "App::Link":
                        link = obj
        if link is None or link not in assembly.components(doc):
            raise ValueError("Select a component to %s" % what)
        return link

    def assembly_suppress(self, link=None):
        from incad.core import assembly, log, safe
        doc = self._assembly_doc()
        link = self._picked_component(doc, link, "suppress or unsuppress")
        r = safe.run_safely("Suppress", lambda: assembly.toggle_suppressed(link), doc)
        if r.ok:
            log.report("info", "%s %s" % (link.Label, "suppressed" if r.value else "back"))
        self.schedule_refresh()

    def assembly_edit(self, link=None):
        doc = self._assembly_doc()
        self.open_component(self._picked_component(doc, link, "edit"))

    def assembly_open(self, link=None):
        """Inventor's Open: the component's part in its own window, as a part of its own (no assembly around it)."""
        doc = self._assembly_doc()
        link = self._picked_component(doc, link, "open")
        self.open_component(link, context=False)
        log.report("info", "%s opened in its own window" % link.LinkedObject.Document.Label)

    def assembly_remove(self, link=None):
        from incad.core import delete, log, safe
        doc = self._assembly_doc()
        link = self._picked_component(doc, link, "remove")
        label = link.Label
        r = safe.run_safely("Remove from Assembly", lambda: delete.delete(link), doc)
        if r.ok:
            log.report("info", "%s removed from the assembly (its part file stays)" % label)
        self.schedule_refresh()

    def assembly_ground(self, link=None):
        from incad.core import assembly, safe
        doc = self._assembly_doc()
        link = self._picked_component(doc, link, "ground or unground")
        action = assembly.unground if assembly.is_grounded(link) else assembly.ground
        safe.run_safely("Ground", lambda: action(link), doc)
        self.schedule_refresh()

    def _schedule_dof(self, doc):
        """Count the parts' degrees of freedom after the refresh (never inside it): the rows fill in when done."""
        if self._dof_pending:
            return
        self._dof_pending = True
        QtCore.QTimer.singleShot(0, lambda name=doc.Name: self._update_dof(name))

    def _update_dof(self, name):
        import FreeCAD as App
        from incad.core import dof
        self._dof_pending = False
        if name not in App.listDocuments() or self.feature_session is not None or dof.loading():
            return  # a file still loading (any tab): the refresh after it counts
        drag = getattr(self, "asm_drag", None)
        if drag is not None and drag.active() or self.dof_handles is not None and self.dof_handles.dragging():
            return
        try:
            _, computed = dof.ensure(App.getDocument(name))
        except Exception as e:
            log.logger().warning("DOF count: %s", e)
            return
        if computed:
            self.schedule_refresh()  # the rows read the new counts (cached now: no second count)
        self.dof_display_changed()

    def _end_drag(self, cmd_id=None):
        """Before any command (Save, Undo, ...): a part drag still going ends first, so its open step and the
        temporary grounds of the other parts never reach the command. A waiting Offset ends too (it takes every
        plain click while it runs: Construction then found no line picked); view commands leave it."""
        from incad.ui.offset_tool import OffsetTool
        from incad.ui.text_place import TextPlaceTool
        other = not str(cmd_id or "").startswith(("view.", "nav."))
        if isinstance(getattr(self, "slot_tool", None), (OffsetTool, TextPlaceTool)) and other \
                and cmd_id != "sketch.text":
            self.stop_slot_tool()
        if self._region_pick is not None and other:  # its shown "Area n" faces stayed and took a Chamfer's clicks
            self._cancel_region_pick()
        if self.asm_drag is not None and self.asm_drag.active():
            self.asm_drag.finish()
        if self.dof_handles is not None and self.dof_handles.dragging():
            self.dof_handles.finish()
        if self._overlay is not None and self._overlay.dragging():
            self._overlay.finish()

    def _document_deleted(self, doc):
        """A document is closing: a drag of one of its parts ends now (its release will never come), and its DOF
        state goes (a file opened later under the same name starts clean)."""
        try:
            if self.asm_drag is not None and self.asm_drag.active() and self.asm_drag.doc_name == doc.Name:
                self.asm_drag.finish(moved_ok=False, closing=True)
            if self.dof_handles is not None:
                self.dof_handles.document_closing(doc.Name)
        except Exception:
            log.logger().exception("InCad drag on close")
        try:
            self._contexts.pop(doc.Name, None)  # the part's tab goes: its picture goes with its view
            for name, ctx in list(self._contexts.items()):
                if ctx.asm_name == doc.Name:  # the assembly closes: no more parts around the edited one
                    ctx.remove()
                    del self._contexts[name]
        except Exception:
            log.logger().exception("InCad assembly context on close")
        try:
            from incad.core import dof
            self._dof_on.discard(doc.Name)
            dof._CACHE.pop(doc.Name, None)
            if self._dof_hover is not None and self._dof_hover[0] == doc.Name:
                self._dof_hover = None
                if self._hover_watch is not None:
                    self._hover_watch.current = None
            if self._dof_glyphs is not None and self._dof_glyphs.doc_name == doc.Name:
                self._dof_glyphs.remove()
                self._dof_glyphs = None
            if self._axis_lines is not None and self._axis_lines.doc_name == doc.Name:
                self._axis_lines.remove()
                self._axis_lines = None
        except Exception:
            log.logger().exception("InCad DOF on close")

    def dof_display_changed(self):
        """Redraw the DOF glyphs: every free part while View > Degrees of Freedom is on, else the part under the
        pointer or being dragged (highlighted), and the selected part's gizmo (its handles, ui/dof_handles). Only the
        freedoms a part has; none for DOF 0, none for a part without a solid. Never raises: it runs from a
        selection observer, timers and a drag's finally."""
        try:
            self._draw_dof()
        except Exception:
            log.logger().exception("DOF glyphs")

    def _draw_dof(self):
        import FreeCAD as App
        import FreeCADGui as Gui
        from incad.core import assembly, dof
        from incad.ui.dof_glyphs import DofGlyphs
        doc = App.ActiveDocument
        view = Gui.ActiveDocument.ActiveView if assembly.is_assembly(doc) and Gui.ActiveDocument else None
        if self._dof_glyphs is not None and (view is None or self._dof_glyphs.view is not view):
            self._dof_glyphs.remove()
            self._dof_glyphs = None
        handles = self.dof_handles
        self._draw_axes(doc, view)
        if view is None or not hasattr(view, "getSceneGraph"):
            if handles is not None:
                handles.clear_drawn()
            return
        counts = dof.last(doc) or {}
        hover = self._dof_hover[1] if self._dof_hover is not None and self._dof_hover[0] == doc.Name else None
        dragged = self.asm_drag.link_name if self.asm_drag is not None and self.asm_drag.active() else None
        tip = dragged  # the part under the pointer draws nothing of its own (the user: the gizmo stayed while the mouse
        # was on the part — that was its hover glyph, the same arrows)
        gizmo = handles.gizmo_part(doc) if handles is not None else None  # the clicked part: its glyph has handles
        if gizmo and not dragged:
            self._dof_linger = None  # a part selected: its own gizmo, no lingering one
            gizmo = self._gizmo_wanted(doc, gizmo, hover, handles)
        gizmo = gizmo or self._lingering(doc, dragged, handles)
        names = list(counts) if doc.Name in self._dof_on else [n for n in dict.fromkeys((gizmo, tip)) if n]
        items, drawn = [], False
        for name in names:
            f, link = counts.get(name), doc.getObject(name)
            bb = assembly._box(link) if link is not None else None  # an empty part's link has no Shape at all
            if f is None or not f.n or bb is None:
                continue
            size = dof.glyph_size(bb.DiagonalLength)
            hs = dof.handles(f, link.Placement, bb.Center, size)
            if name == gizmo:  # normal colour, the handle under the pointer lit
                handles.set_drawn(doc.Name, name, hs, size, view)
                drawn = True
                items.append((name, hs, size, False, handles.lit))
            else:
                items.append((name, hs, size, name == tip, None))
        if handles is not None and not drawn:
            handles.clear_drawn()
        if drawn and not dragged and not getattr(self, "_gizmo_check_due", False):
            # a gizmo on screen goes out on time whatever else happens (a timer set earlier can come before its
            # moment): looked at again every half second while it is there
            self._gizmo_check_due = True

            def again():
                self._gizmo_check_due = False
                self.dof_display_changed()
            QtCore.QTimer.singleShot(500, again)
        if self._dof_glyphs is None:
            if not items:
                return
            self._dof_glyphs = DofGlyphs(view, self.theme, doc.Name)
        self._dof_glyphs.theme = self.theme
        self._dof_glyphs.grabbed = handles is not None and handles.dragging()
        self._dof_glyphs.set(items)

    def _draw_axes(self, doc, view):
        """The components' axes, grey dashed (incad.ui.axis_lines): in an assembly's view, always."""
        from incad.ui.axis_lines import AxisLines
        if self._axis_lines is not None and (view is None or self._axis_lines.view is not view):
            self._axis_lines.remove()
            self._axis_lines = None
        fs = self.feature_session
        picking = fs is not None and getattr(fs.kind, "ASSEMBLY", False)  # a constraint dialog: axes to pick
        if view is None or not hasattr(view, "getSceneGraph") or not (prefs.assembly_axes() or picking):
            if self._axis_lines is not None:
                self._axis_lines.remove()
                self._axis_lines = None
            return
        if self._axis_lines is None:
            self._axis_lines = AxisLines(view, self.theme, doc.Name)
        self._axis_lines.theme = self.theme
        self._axis_lines.set(doc)

    _AXIS_PICKS = {"face", "element", "cylinder", "direction", "circular_edge"}  # picks an axis can stand for

    def axis_pressed(self, view, viewport, x, y):
        """A click on a component's axis while a constraint dialog waits for a pick: the axis's round face (a round
        edge of it for Insert) goes into the selection, and so into the dialog. True when it took the click."""
        import FreeCAD as App
        import FreeCADGui as Gui
        from incad.core import assembly
        fs, lines = self.feature_session, self._axis_lines
        if fs is None or fs.dialog is None or lines is None or not getattr(fs.kind, "ASSEMBLY", False):
            return False
        key = fs.dialog.active_pick()
        if key is None or fs.dialog.field_def(key).pick not in self._AXIS_PICKS:
            return False
        hit = lines.hit(viewport, x, y)
        doc = App.ActiveDocument
        if hit is None or doc is None or doc.Name != lines.doc_name:
            return False
        name, start, end, face, circles = hit
        sub = "Face%d" % face
        if fs.dialog.field_def(key).pick == "circular_edge":
            if not circles:
                return False
            from incad.ui import dof_handles as dh
            link = doc.getObject(name)

            def off(k):
                c = link.Shape.Edges[k - 1].Curve.Center
                sx, sy = dh.screen(view, viewport, c)
                return (sx - x) ** 2 + (sy - y) ** 2
            sub = "Edge%d" % min(circles, key=off)  # the round edge nearer the click
        asm = assembly.assembly_of(doc)
        Gui.Selection.addSelection(doc.Name, asm.Name, "%s.%s" % (name, sub))
        return True

    LINGER = 3.0  # s a part's gizmo stays after it was dragged (the user: it went out at once, hard to grab)

    def _gizmo_wanted(self, doc, name, hover, handles):
        """The selected part's gizmo: shown when the part is clicked, kept while a handle is dragged or under the pointer,
        and out LINGER seconds after (the user: always on it was in the way — "it must go out when nobody pulls it",
        "give a couple of seconds to grab it"). A click on the part shows it again. None: not now."""
        now = time.monotonic()
        seen = getattr(self, "_gizmo_seen", None)
        fresh = seen is None or seen[:2] != (doc.Name, name)  # just clicked: shown, then it fades unless held
        on_it = fresh or handles.dragging() or (  # dragged, or the pointer on a handle about to grab it
            handles.lit is not None and handles.drawn is not None and handles.drawn[1] == name)
        if on_it:
            self._gizmo_seen = (doc.Name, name, now)
            return name
        seen = getattr(self, "_gizmo_seen", None)
        if seen is not None and seen[:2] == (doc.Name, name) and now - seen[2] < self.LINGER:
            if getattr(self, "_gizmo_timer_for", None) != seen:
                self._gizmo_timer_for = seen
                QtCore.QTimer.singleShot(int((self.LINGER - (now - seen[2])) * 1000) + 100, self.dof_display_changed)
            return name
        return None

    def _lingering(self, doc, dragged, handles):
        """The part just dragged by the mouse (not selected: a press that moved is a drag, no click): its gizmo stays
        LINGER seconds — longer while the pointer is on its handles — so a handle can still be grabbed."""
        import time
        from incad.qt import QtCore
        now = time.monotonic()
        if dragged:
            self._dof_linger = (doc.Name, dragged, None)  # counts down once the drag ends
            return None
        linger = self._dof_linger
        if linger is None or linger[0] != doc.Name or handles is None or not handles.can_grab():
            return None
        name, until = linger[1], linger[2]
        if until is None or (handles.lit is not None and handles.drawn is not None and handles.drawn[1] == name):
            until = now + self.LINGER
            self._dof_linger = (doc.Name, name, until)
            QtCore.QTimer.singleShot(int(self.LINGER * 1000) + 100, self.dof_display_changed)
        if now > until or doc.getObject(name) is None:
            self._dof_linger = None
            return None
        return name

    def toggle_interference(self):
        """Interference on / off for the assembly in front (the user: parts going into each other in a bright
        colour): on, every overlap is drawn red over the parts, the pairs listed, and redrawn as parts move. In a
        part, between its shown Solids (the user: a jar and its lid in one part)."""
        import FreeCAD as App
        from incad.core import assembly, document
        doc = App.ActiveDocument
        if not (assembly.is_assembly(doc) or document.is_incad_part(doc)):
            raise ValueError("Open an assembly, or a part with more than one Solid")
        self._interference_on ^= {doc.Name}
        if doc.Name not in self._interference_on:
            self._clear_interference(close_panel=True)
        self.schedule_refresh()
        self._schedule_interference(doc, force=True)

    def interference_shown(self):
        import FreeCAD as App
        return App.ActiveDocument is not None and App.ActiveDocument.Name in self._interference_on

    def _clear_interference(self, close_panel=False):
        if self._interference is not None:
            self._interference[0].remove()
            self._interference = None
        if close_panel and getattr(self.info_panel, "_incad_interference", False):
            self._close_info_panel()

    def _schedule_interference(self, doc, force=False):
        """Drawn after the refresh (never in it: the booleans take a moment), only when something moved."""
        if doc is None or doc.Name not in self._interference_on:
            if self._interference is not None:
                self._clear_interference(close_panel=True)
            return
        if force and self._interference is not None:
            self._interference = (self._interference[0], self._interference[1], None)
        if self._interference_pending:
            return
        self._interference_pending = True
        QtCore.QTimer.singleShot(0, lambda name=doc.Name: self._update_interference(name))

    def _update_interference(self, name):
        import FreeCAD as App
        import FreeCADGui as Gui
        from incad.core import interference
        from incad.ui.info_panel import InfoPanel
        from incad.ui.interference_display import InterferenceDisplay
        self._interference_pending = False
        doc = App.ActiveDocument
        if doc is None or doc.Name != name or name not in self._interference_on or doc.Recomputing:
            return
        drag = getattr(self, "asm_drag", None)
        if drag is not None and drag.active() or self.dof_handles is not None and self.dof_handles.dragging():
            return  # the drag's end refreshes, and draws it then
        sig = interference.signature(doc)
        view = Gui.getDocument(name).ActiveView
        if self._interference is not None and self._interference[1] == name and self._interference[2] == sig \
                and self._interference[0].view is view:
            return
        with perf.measure("interference"):
            found = interference.overlaps(doc)
        if self._interference is None or self._interference[0].view is not view:
            self._clear_interference()
            self._interference = (InterferenceDisplay(view), name, sig)
        self._interference[0].set([shape for _, _, shape, _ in found])
        self._interference = (self._interference[0], name, sig)
        rows = [("%s ↔ %s" % (a.Label, b.Label), "%.3g mm³" % v) for a, b, _, v in found[:12]]
        if len(found) > 12:
            rows.append(("…", "%d more" % (len(found) - 12)))
        if not rows:
            rows = [("No parts go into each other", "")]
        self._close_info_panel()
        parent = self._mdi().viewport() if self._mdi() is not None else self.mw
        self.info_panel = InfoPanel("Interference: %d" % len(found), rows, self.theme, parent)
        self.info_panel._incad_interference = True
        self.info_panel.adjustSize()
        self.info_panel.move(16, parent.height() - self.info_panel.height() - 16)
        self.info_panel.show()
        log.report("info", "Interference: %s" % ("; ".join("%s ↔ %s %.3g mm³" % (a.Label, b.Label, v)
                                                            for a, b, _, v in found[:4]) or "none"))

    def toggle_dof(self):
        import FreeCAD as App
        from incad.core import assembly
        doc = App.ActiveDocument
        if not assembly.is_assembly(doc):
            raise ValueError("Open an assembly first")
        self._dof_on ^= {doc.Name}
        self.dof_display_changed()
        self.schedule_refresh()  # the ribbon button's checked state

    def dof_shown(self):
        import FreeCAD as App
        return App.ActiveDocument is not None and App.ActiveDocument.Name in self._dof_on

    def _on_dof_hover(self, name):
        import FreeCAD as App
        doc = App.ActiveDocument
        self._dof_hover = (doc.Name, name) if name and doc is not None else None  # (document, part)
        self.dof_display_changed()

    def open_component(self, link, context=True):
        """Open the component's file in its own tab — Edit: with the other parts drawn around it (context); Open:
        as a part of its own."""
        import FreeCAD as App
        import FreeCADGui as Gui
        if link.LinkedObject is None:  # its part file was not found where the assembly says
            raise ValueError("%s: its part file was not found — open the assembly from the folder its parts are in, "
                             "or Place the part again" % link.Label)
        path = link.LinkedObject.Document.FileName
        part = link.LinkedObject.Document
        if Gui.getDocument(part.Name) is None or Gui.getDocument(part.Name).ActiveView is None:
            if part.FileName:
                part.save()  # changes InCad made there (assembly cuts) belong to that file: never drop them
            App.closeDocument(part.Name)
            part = App.openDocument(path)
            link.Document.recompute()
        App.setActiveDocument(part.Name)
        Gui.ActiveDocument = Gui.getDocument(part.Name)
        self._adopt_foreign_part(part, link.Document)
        if context:
            self._show_context(link, part)
        else:
            old = self._contexts.pop(part.Name, None)  # an earlier Edit's picture goes: this is the part alone
            if old is not None:
                old.remove()
        self.schedule_refresh()

    @staticmethod
    def _in_its_tab(doc):
        """The document is the one shown in front, in a tab of its own (an assembly's part files load without one, and
        one of them can be the active document for a moment while the assembly opens: never made a part then)."""
        import FreeCADGui as Gui
        g = Gui.getDocument(doc.Name)
        return g is not None and g.ActiveView is not None and Gui.ActiveDocument is not None \
            and Gui.ActiveDocument.Document is doc

    def _adopt_foreign_part(self, part, asm):
        """A component's file opened from the assembly: made outside InCad, it becomes an InCad part at once, in the
        assembly's units (the user: it opened as "Not an InnCad part" — no tools, and Hide took it away)."""
        from incad.core import document
        self._adopt_tried.add(part.Name)
        return self._adopt_foreign(part, document.part_units(asm))

    def _adopt_foreign(self, doc, units=None):
        """A file made outside InCad — a script's or FreeCAD's part, a STEP opened as it is — gets every tool of an
        InCad part (the user: parts not made in InCad must be edited the same way). Nothing to make a part of
        (no solid): left as it is."""
        from incad.core import document, safe
        if document.is_incad_part(doc):
            return False
        r = safe.run_safely(host.brand("Open as InCad Part"), lambda: document.adopt_foreign(doc, units or prefs.default_units()),
                            doc)
        if not r.ok or r.value is None:
            return False
        log.report("info", "%s was made outside InCad: it is an InCad part now (Save keeps that)" % doc.Label)
        self.schedule_refresh()
        return True

    def _show_context(self, link, part):
        """Edit from the assembly: the other parts drawn around this one in its tab (ui/assembly_context), from the
        assembly's current placements; any earlier picture in that tab goes first."""
        import FreeCADGui as Gui
        from incad.core import assembly
        from incad.ui.assembly_context import AssemblyContext
        old = self._contexts.pop(part.Name, None)
        if old is not None:
            old.remove()
        asm = link.Document
        if not assembly.is_assembly(asm):
            return
        gui = Gui.getDocument(part.Name)
        view = gui.ActiveView if gui is not None else None
        if view is None or not hasattr(view, "getSceneGraph"):
            return
        try:
            self._contexts[part.Name] = AssemblyContext(view, assembly.context_of(asm, link), self.theme, asm.Name)
        except Exception:
            log.logger().exception("Assembly context")
            return
        self._contexts[part.Name].add_bar(link.Label, lambda n=part.Name: self._return_from(n))
        log.report("info", "Editing %s in %s: the other parts are shown for reference" % (link.Label, asm.Label))

    def return_to_assembly(self):
        """File ▸ Return to Assembly (the bar on a part edited from its assembly): the part saved, the assembly
        brought up to date and shown; the part's picture of the parts around it goes."""
        import FreeCAD as App
        doc = App.ActiveDocument
        if doc is None or doc.Name not in self._contexts:
            raise ValueError("This part was not opened with Edit from an assembly")
        self._return_from(doc.Name)

    def _return_from(self, name):
        import FreeCAD as App
        import FreeCADGui as Gui
        ctx = self._contexts.pop(name, None)
        part = App.getDocument(name) if name in App.listDocuments() else None
        asm = App.getDocument(ctx.asm_name) if ctx is not None and ctx.asm_name in App.listDocuments() else None
        if ctx is not None:
            ctx.remove()
        if part is not None and part.FileName:
            if part.Recomputing:
                return QtCore.QTimer.singleShot(200, lambda: self._return_from(name))
            part.recompute()
            part.save()
            gdoc = Gui.getDocument(name)
            if gdoc is not None:
                gdoc.Modified = False
        if asm is None:
            log.report("warning", "The assembly is no longer open: the part is saved")
            return
        asm.recompute()
        self._back_to(asm)
        log.report("info", "Back in %s: %s saved, the assembly brought up to date"
                   % (asm.Label, part.Label if part is not None else "the part"))
        self.schedule_refresh()

    def _sync_start_page(self, on):
        """The recent files over the empty window while no file is open; gone as soon as one is."""
        try:
            mdi = self._mdi()
            if mdi is None:
                return
            if on and self.start_page is None:
                from incad.ui.start_page import StartPage
                self.start_page = StartPage(mdi.viewport(), self.theme)
            if self.start_page is not None:
                self.start_page.set_theme(self.theme)
                self.start_page.show_now(on)
        except RuntimeError:  # the window went
            self.start_page = None
        except Exception:
            log.logger().exception("Start page")

    def _active_context(self):
        import FreeCAD as App
        doc = App.ActiveDocument
        return self._contexts.get(doc.Name) if doc is not None else None

    def toggle_assembly_context(self):
        ctx = self._active_context()
        if ctx is None:
            raise ValueError("Edit this part from its assembly (Edit on the part) to see the parts around it")
        ctx.set_visible(not ctx.shown)
        self.schedule_refresh()  # the button's checked state

    def assembly_context_shown(self):
        ctx = self._active_context()
        return ctx is not None and ctx.shown

    PADLOCK_SCALE = 2.0

    def _mark_grounded(self, doc):
        """The word Grounded on the grounded part itself (incad.ui.ground_marks), in this assembly's view."""
        import FreeCADGui as Gui
        from incad.ui import ground_marks
        try:
            view = Gui.getDocument(doc.Name).ActiveView
            marks = getattr(self, "_ground_marks", None)
            if marks is None or marks.view is not view:
                if marks is not None:
                    marks.remove()
                marks = self._ground_marks = ground_marks.GroundMarks(view)
            marks.set(ground_marks.points(doc), themes.PALETTES[self.theme]["accent"])
        except Exception:
            log.logger().exception("Grounded marks")

    def _tame_padlocks(self, doc):
        """FreeCAD draws the grounded padlock big (scale 3) and in loud red: smaller and in the theme's accent — not
        grey (the user: in an assembly it was not clear which part is grounded)."""
        from incad.core import assembly
        p = themes.PALETTES[self.theme]
        c = QtGui.QColor(p["accent"])
        for j in assembly.joints(doc):
            proxy = getattr(getattr(j, "ViewObject", None), "Proxy", None)
            scale = getattr(proxy, "scale", None)
            if scale is None:
                continue
            try:
                if abs(scale.scaleFactor.getValue() - self.PADLOCK_SCALE) > 1e-6:
                    scale.scaleFactor = self.PADLOCK_SCALE
                proxy.lockpadColor.rgb.setValue(c.redF(), c.greenF(), c.blueF())
            except Exception:
                pass

    def _on_plane_picked(self, role):
        try:
            self.sketches.pick_origin(role)
        except Exception as e:
            log.report("error", "New sketch failed: %s" % e, traceback.format_exc())
        self.schedule_refresh()

    def inspect(self, what):
        import FreeCADGui as Gui
        from incad.core import document, inspect
        from incad.features import common
        doc = self._doc()
        if not document.is_incad_part(doc):
            raise ValueError("Open an InCad part first")
        if what in inspect.MODES or what == "measure":
            mode = "auto" if what == "measure" else what
            self._start_measuring(mode)
            pairs = [common.resolve(sel.Object, sub) for sel in Gui.Selection.getSelectionEx(doc.Name)
                     for sub in (sel.SubElementNames or ("",))]
            try:
                rows = inspect.measure(pairs, mode)
            except ValueError as e:
                self.status.set_prompt("%s (Esc finishes)" % e)
                self._close_info_panel()
                return
            title = "Measure" if mode == "auto" else "Measure %s" % mode.capitalize()
        else:
            from incad.core import materials
            rows = inspect.mass_properties(document.primary_body(doc), materials.density(doc))
            title = "Mass Properties (%s %.2f g/cm³)" % (materials.material(doc), materials.density(doc))
        self._close_info_panel()
        parent = self._mdi().viewport() if self._mdi() is not None else self.mw
        self.info_panel = InfoPanel(title, rows, self.theme, parent)
        self.info_panel.adjustSize()
        self.info_panel.move(16, parent.height() - self.info_panel.height() - 16)
        self.info_panel.show()

    # ----- section view -----
    def start_section(self, base):
        """Inventor's slice: the cut at the picked plane, with a panel to offset and flip it."""
        from incad.core import document, section
        from incad.ui.section_panel import SectionPanel
        self.stop_section(keep_cut=False)
        doc = self._doc()
        targets = self._section_targets(doc)
        if not targets:
            raise ValueError("Open a part with a solid (or an assembly with parts) to see a section")
        self._section_base = base
        self._show_cut(doc, section.adjusted(base), targets)
        reach = 50.0
        try:
            reach = max(10.0, self._compound([sh for sh, _ in targets]).BoundBox.DiagonalLength / 2.0)
        except Exception:
            pass
        parent = self._mdi().viewport() if self._mdi() is not None else self.mw
        panel = SectionPanel(self.theme, parent, reach=reach)
        panel.changed.connect(self._section_changed)
        panel.done.connect(lambda: self.stop_section(keep_cut=True))
        panel.off.connect(lambda: self.stop_section(keep_cut=False))
        panel.adjustSize()
        panel.move(16, 16)
        panel.show()
        panel.raise_()
        self.section_panel = panel
        self.schedule_refresh()

    def start_section_points(self):
        """Markers at corners, edge middles and circle centres; three clicks make the plane."""
        import FreeCADGui as Gui
        from incad.core import document, section
        doc = self._doc()
        targets = self._section_targets(doc)
        if not targets:
            raise ValueError("Open a part with a solid (or an assembly with parts) first")
        self.stop_section(keep_cut=False)

        def done(points):
            try:
                self.start_section(section.through_points(*points[:3]))
            except ValueError as e:
                log.report("warning", str(e))
        self.start_point_pick(section.snap_points(self._compound([sh for sh, _ in targets])), 3,
                              "Section: click 3 points — corners, edge middles, circle centres (Esc cancels)", done)

    def start_point_pick(self, points, count, prompt, on_done, on_cancel=None):
        """Markers at `points` ([(kind, Vector)]) on the view; `count` clicks on them call on_done(points)."""
        import FreeCADGui as Gui
        doc = self._doc()
        if doc is None:
            raise ValueError("Open a part first")
        if self._point_pick is not None:
            self._point_pick.on_cancel = None  # replacing it, not cancelling back to a dialog field
        self._cancel_point_pick()
        view = Gui.getDocument(doc.Name).ActiveView
        self._point_pick = _PointPick(view, [p for _, p in points], [k for k, _ in points], count, prompt,
                                      on_done, on_cancel)
        _fit_points_in_view(view, [p for _, p in points])  # so every marker is on screen (they sit on the solid,
        Gui.Selection.clearSelection()                     # which can be off-screen when zoomed into the sketch)
        self.status.set_prompt(prompt)
        self.schedule_refresh()

    def cancel_point_pick(self, quiet=False):
        """End a point pick from outside (the dialog's field was left); the pick's on_cancel is not called."""
        pick = self._point_pick
        if pick is not None:
            pick.on_cancel = None
        self._cancel_point_pick()
        if not quiet:
            self.status.set_prompt("Ready")

    def _point_clicked(self, viewport, x, y):
        """A click while picking points: on a marker it counts; elsewhere a drag still turns the view (the user:
        only pan worked) and a plain click is ignored."""
        pick = self._point_pick
        i = pick.nearest(viewport, x, y)
        if i is None:
            return "drag"
        p = pick.points[i]
        if any((p - q).Length < 1e-9 for q in pick.picked):
            return True
        pick.picked.append(p)
        n = len(pick.picked)
        if pick.count == 3 and n == 3:
            pick.show_plane(pick.picked)  # (the section or the plane dialog takes over from here)
        if n < pick.count:
            self.status.set_prompt("%d of %d points picked (Esc cancels)" % (n, pick.count))
            self.schedule_refresh()
            return True
        picked = list(pick.picked)
        pick.on_cancel = None
        self._cancel_point_pick()
        if pick.on_done is not None:
            try:
                pick.on_done(picked)
            except Exception as e:
                log.report("warning", str(e), traceback.format_exc())
        return True

    def _cancel_point_pick(self):
        pick = self._point_pick
        if pick is None:
            return
        self._point_pick = None
        pick.remove()
        if pick.on_cancel is not None:
            try:
                pick.on_cancel()
            except Exception as e:
                log.report("warning", str(e), traceback.format_exc())
        self.schedule_refresh()

    def _show_cut(self, doc, placement, targets=None):
        """FreeCAD's clipping plane removes one side (fast); the material on the plane is drawn on top of it — for a
        part, its shown Solids; in an assembly, every shown component in its own colour, its hatching turned from its
        neighbour's (the user: a section in the assembly cuts all the parts the plane goes through)."""
        import FreeCAD as App
        import FreeCADGui as Gui
        from incad.core import section
        from incad.ui.section_display import SectionDisplay
        view = Gui.getDocument(doc.Name).ActiveView
        self._section_last = placement
        if self._section_display is not None and self._section_sig != self._section_signature(doc):
            # the part changed under the open section (the user: a chamfer added while it was on showed as a hole
            # in the cut): meshed again from what is shown now
            displays, _doc, old_view = self._section_display[:3]
            self._section_display = None
            for display in displays:
                display.remove()
            try:
                old_view.toggleClippingPlane(0)
            except Exception:
                pass
            targets = None
        if self._section_display is None:
            targets = targets if targets is not None else self._section_targets(doc)
            caps = [section.Caps(sh) for sh, _ in targets]  # meshed once per shape: the slider slices in ms
            self._section_sig = self._section_signature(doc)
        else:
            targets, caps = self._section_display[4], self._section_display[3]
        # a plane that misses everything (or leaves nothing) is turned round: a section always shows material
        cut = [c.faces(placement) for c in caps]
        if not any(cut):
            flipped = section.adjusted(placement, 0.0, True)
            again = [c.faces(flipped) for c in caps]
            if any(again):
                placement, cut = flipped, again
        kept = placement.Rotation.multVec(App.Vector(0, 0, 1))  # the side FreeCAD keeps with `clip` (probed)
        clip = App.Placement(placement.Base, placement.Rotation.multiply(App.Rotation(App.Vector(1, 0, 0), 180)))
        if self._section_display is None:
            displays = [SectionDisplay(view, color) for _, color in targets]
            self._section_display = (displays, doc, view, caps, targets)
        else:
            view.toggleClippingPlane(0)
        view.toggleClippingPlane(1, False, True, clip)  # no drag handle: destroying it crashes FreeCAD (probed)
        size = self._compound([sh for sh, _ in targets]).BoundBox.DiagonalLength
        shift = kept * max(0.01, size / 2000.0)
        for k, (display, c, faces) in enumerate(zip(self._section_display[0], caps, cut)):
            display.set_faces(faces, shift, c.polygons)
            try:  # hatching, as Inventor draws cut material; the loops faces() just sliced, no second slice
                # between the plane and the face (nearer the viewer than the face, still on the kept side)
                display.set_hatch(section.hatch(c.last_loops, placement, size / 45.0, 45.0 + 90.0 * (k % 2)
                                                + 30.0 * (k // 2 % 2)), shift * 0.5)
            except Exception as e:
                log.logger().warning("Section hatch: %s", e)
                try:
                    display.set_hatch([], shift * 0.5)  # never the previous position's hatch
                except Exception:
                    pass

    def _section_signature(self, doc):
        """What the open section was meshed from: every shown Solid (a part) or shown shape (an assembly)."""
        from incad.core import document, section
        try:
            if document.is_incad_part(doc):
                return tuple((b.Name, b.Shape.hashCode()) for b in document.bodies(doc) if document.is_shown(b))
            return tuple((o.Name, str(getattr(o, "Placement", "")), getattr(o, "Shape").hashCode())
                         for o, _shape, _c in section.shown_shapes(doc))
        except Exception:
            return None

    def _section_follow(self, doc):
        """After a change: an open section whose part changed is cut again where it was."""
        if self._section_display is None or doc is None or self._section_display[1] is not doc \
                or getattr(doc, "Recomputing", False) or getattr(self, "_section_last", None) is None:
            return
        if self._section_sig == self._section_signature(doc):
            return
        try:
            self._show_cut(doc, self._section_last)
        except Exception as e:
            log.logger().warning("Section after a change: %s", e)

    @staticmethod
    def _compound(shapes):
        import Part
        return shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)

    def _section_targets(self, doc):
        """[(shape, colour)] the section cuts: an InCad part's shown Solids (one); else everything shown — an
        assembly's components each in its part's colour, a STEP's or another program's shapes."""
        from incad.core import document, section
        if doc is None:
            return []
        body = document.primary_body(doc)
        if body is not None and not body.Shape.isNull() and body.Shape.Solids:
            return [(self._section_shape(body), self._part_colour(body))]
        return [(shape, self._part_colour(colour_of)) for _, shape, colour_of in section.shown_shapes(doc)]

    @staticmethod
    def _part_colour(obj):
        try:
            vo = obj.ViewObject
            return tuple(vo.ShapeAppearance[0].DiffuseColor[:3]) if hasattr(vo, "ShapeAppearance") \
                else tuple(vo.ShapeColor[:3])
        except Exception:
            try:
                return tuple(obj.Tip.ViewObject.ShapeColor[:3])
            except Exception:
                return (0.8, 0.8, 0.8)

    @staticmethod
    def _section_shape(body):
        """What the section cuts: every Solid of the part that is shown (the second gear of a pair, a piece of its
        own, looked hollow when only the main Solid got its cut faces)."""
        import Part
        from incad.core import document
        shapes = []
        for b in document.bodies(body.Document):
            try:
                if not b.Shape.isNull() and b.Shape.Solids and document.is_shown(b):
                    shapes.append(b.Shape)
            except Exception:
                continue
        if not shapes:
            return body.Shape
        return shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)

    def section_on(self):
        return self._section_display is not None

    def _section_changed(self, offset, flip):
        from incad.core import section
        if self._section_base is None or self._section_display is None:
            return
        try:
            self._show_cut(self._section_display[1], section.adjusted(self._section_base, offset, flip))
        except Exception as e:
            log.report("warning", "Could not move the section: %s" % e)

    def stop_section(self, keep_cut=False):
        """Done keeps the cut (the panel goes); Off / Section again removes it."""
        if self.section_panel is not None:
            try:
                self.section_panel.close()
                dispose(self.section_panel)
            except RuntimeError:
                pass
            self.section_panel = None
        if not keep_cut and self._section_display is not None:
            displays, _doc, view = self._section_display[:3]
            self._section_display = None
            for display in displays:
                display.remove()
            try:
                view.toggleClippingPlane(0)
            except Exception:
                pass
            self._section_base = None
        self.schedule_refresh()


    # ----- Parameters window -----
    def open_parameters_window(self):
        """The Parameters button: its window (every parameter, what uses it, Delete Unused); a second click brings
        the open one to the front."""
        from incad.ui.parameters_window import ParametersWindow
        if self.parameters_window is None:
            w = ParametersWindow(self.theme, self.mw)
            w.parameterSet.connect(self._on_parameter_set)
            w.parameterRenamed.connect(self._on_parameter_renamed)
            w.parameterRemoved.connect(self._on_parameter_removed)
            w.parameterClicked.connect(self._on_parameter_clicked)
            w.deleteUnusedRequested.connect(self._delete_unused_parameters)
            w.closed.connect(self._parameters_window_closed)
            self.parameters_window = w
            self.refresh_parameters_window(self._doc())
        self.parameters_window.show()
        self.parameters_window.raise_()
        self.parameters_window.activateWindow()

    def refresh_parameters_window(self, doc):
        """doc: the open part or assembly, None for anything else (the window lists nothing, adds nothing)."""
        w = self.parameters_window
        if w is None:
            return
        from incad.core import param_table
        try:
            rows = param_table.rows(doc) if doc is not None else []
        except Exception:
            log.logger().exception("Parameters window rows")
            rows = []
        w.title.setText("Parameters — %s" % doc.Label if doc is not None else "Parameters")
        w.set_rows(rows, doc is not None)

    def _parameters_said(self, r):
        """A change made from the window or the panel: its error shown in the window too."""
        if self.parameters_window is not None:
            self.parameters_window.set_message("" if r.ok else r.error)

    def _delete_unused_parameters(self):
        from incad.core import param_table, safe
        doc = self._doc()
        if doc is None:
            return
        r = safe.run_safely("Delete Unused Parameters", lambda: param_table.delete_unused(doc), doc)
        if self.parameters_window is not None:
            if r.ok:
                text = ("Deleted %d: %s" % (len(r.value), ", ".join(r.value))) if r.value else "Nothing unused"
                self.parameters_window.set_message(text, ok=True)
            else:
                self.parameters_window.set_message(r.error)
        self.schedule_refresh()

    def _parameters_window_closed(self):
        w, self.parameters_window = self.parameters_window, None
        if w is not None:
            QtCore.QTimer.singleShot(0, lambda: dispose(w))

    def close_parameters_window(self):
        if self.parameters_window is not None:
            try:
                self.parameters_window.close()
            except RuntimeError:
                self.parameters_window = None

    # ----- sketch Constraint List -----
    def open_constraints_window(self, subs=None):
        """subs: the line or point to filter by (the right-click entry: the one under the cursor), picked for it."""
        import FreeCADGui as Gui
        from incad.ui.constraints_panel import ConstraintsPanel
        sk = self.sketches.editing()
        if sk is None:
            raise ValueError("Open a sketch first")
        if subs:
            Gui.Selection.clearSelection()
            for sub in subs:
                Gui.Selection.addSelection(sk.Document.Name, sk.Name, sub)
        if self.constraints_panel is None:
            parent = self._mdi().viewport() if self._mdi() is not None else self.mw
            p = ConstraintsPanel(self.theme, parent)
            p.delete_requested.connect(self._delete_constraints)
            p.highlight.connect(self._highlight_constraint)
            p.show_all.connect(self._constraints_filter_clear)
            p.closed.connect(self.close_constraints_window)
            p.show()
            p.place()
            self.constraints_panel = p
        self._constraints_owner = (sk.Document.Name, sk.Name)
        self.refresh_constraints_window()
        self.constraints_panel.raise_()

    def close_constraints_window(self):
        if self.constraints_panel is not None:
            try:
                self.constraints_panel.close()
                dispose(self.constraints_panel)
            except RuntimeError:
                pass
            self.constraints_panel = None
        self._constraints_subs = []

    def _picked_sketch_elements(self, sk):
        """Lines and points picked in the sketch. While a row's constraint is selected (its highlight), the pick
        the list was filtered by stays."""
        import FreeCADGui as Gui
        subs, constraint = [], False
        for sel in Gui.Selection.getSelectionEx(sk.Document.Name):
            if sel.Object is sk:
                for n in sel.SubElementNames:
                    if n.startswith("Constraint"):
                        constraint = True
                    else:
                        subs.append(n)
        return list(self._constraints_subs) if constraint else subs

    @staticmethod
    def _element(sk, sub):
        from incad.core import sketch_patterns as sp
        return sp.element(sk, sub)

    def refresh_constraints_window(self, reset=False):
        from incad.core import sketch_constraints as sc
        p, sk = self.constraints_panel, self.sketches.editing()
        if p is None or sk is None:
            return
        try:
            shape = [(c.Type, c.Name) + tuple(sc.ties_of(c)) for c in sk.Constraints]
            if shape != getattr(self, "_constraints_shape", None):
                reset = True  # a delete, an undo: the same index is another constraint now
                self._constraints_shape = shape
            subs = self._picked_sketch_elements(sk)
            rows = sc.for_elements(sk, subs)
            names = []
            for sub in subs[:3]:
                try:
                    names.append(sc.element_name(sk, *self._element(sk, sub)))
                except (ValueError, IndexError):
                    pass
            header = ("Picked: %s  ·  Esc shows all" % ", ".join(dict.fromkeys(names))) if subs \
                else "All constraints (%d)" % len(sk.Constraints)
            p.set_rows("Constraints — %s" % sk.Label, header,
                       [(r.index, r.type, r.text, ("%s = %s" % (r.name, r.value)) if r.value else "", bool(r.owner))
                        for r in rows], reset=reset)
            self._constraints_subs = subs
        except RuntimeError:
            pass

    def _constraints_filter_clear(self):
        import FreeCADGui as Gui
        self._constraints_subs = []
        Gui.Selection.clearSelection()
        self.refresh_constraints_window()

    def _highlight_constraint(self, index):
        """A row selected in the list: its constraint and the lines it ties are selected in the sketch."""
        import FreeCADGui as Gui
        from incad.core import sketch_constraints as sc
        sk = self.sketches.editing()
        if sk is None or index < 0 or index >= len(sk.Constraints):
            return
        from incad.core import sketch_patterns as sp
        geos = sk.Geometry
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(sk.Document.Name, sk.Name, "Constraint%d" % (index + 1))
        for geo, _pos in sc.ties_of(sk.Constraints[index]):
            if 0 <= geo < len(geos):  # a sketch Point is a vertex in the Sketcher, every other curve an edge
                point = geos[geo].TypeId == "Part::GeomPoint"
                try:
                    sub = sp.sub_of(sk, geo, 1) if point else "Edge%d" % (geo + 1)
                except ValueError:  # no vertex for it right now: the constraint alone is selected
                    sub = None
                if sub:
                    Gui.Selection.addSelection(sk.Document.Name, sk.Name, sub)

    def _delete_constraints(self, indices):
        from incad.core import safe, sketch_constraints as sc
        sk = self.sketches.editing()
        if sk is None:
            return
        valid = [i for i in indices if 0 <= i < len(sk.Constraints)]
        why = sc.refusal(sk, valid)
        if why:
            if self.constraints_panel is not None:
                self.constraints_panel.set_message(why)
            else:
                log.report("warning", why)
            return
        r = safe.run_safely("Delete Constraints", lambda: sc.delete(sk, valid), sk.Document)
        if self.constraints_panel is not None:
            self.constraints_panel.set_message(("Deleted %d" % r.value) if r.ok else r.error, ok=r.ok)
        elif not r.ok:
            log.report("warning", r.error)
        self.refresh_constraints_window()

    def _constraint_menu_items(self):
        """The sketch menu's own entries for the line or point under the cursor (else the picked one): its
        Constraints (n)… — the Constraint List filtered to it, where one or all of them are deleted (the user: a
        Delete row per constraint made the menu too long) — and Break Mirror Link on a mirrored copy."""
        from incad.core import sketch_constraints as sc
        sk = self.sketches.editing()
        subs, geo = self._sketch_menu_element(sk)
        if not subs:
            return []
        items = []
        rows = sc.for_elements(sk, subs)
        if rows:
            items.append(("Constraints (%d)…" % len(rows), "constraints_window",
                          lambda picked=list(subs): self.open_constraints_window(picked)))
        copy = self._mirror_copy_under(sk, geo)
        if copy:
            items.append(("Break Mirror Link", "mirror", lambda geos=copy: self._break_mirror(sk, geos)))
        return items

    @staticmethod
    def _mirror_copy_under(sk, geo):
        """The whole mirrored copy (its outline) when geometry `geo` is one made with Mirror, else []."""
        from incad.core import sketch_mirror
        from incad.core import sketch_offset as so
        if geo is None or geo < 0:
            return []
        try:
            whole = so.outline(sk, geo)[0]
        except (ValueError, IndexError):
            whole = [geo]
        return whole if sketch_mirror.is_linked(sk, whole) else []

    def _break_mirror(self, sk, geos):
        """Right-click ▸ Break Mirror Link: the copy keeps its shape and stops following its original."""
        from incad.core import safe, sketch_mirror
        r = safe.run_safely("Break Mirror Link", lambda: sketch_mirror.break_link(sk, geos), sk.Document, only=[sk])
        if r.ok:
            log.report("info", "The copy is free now: it keeps its shape and no longer follows the original")
        self.schedule_refresh()

    def open_sketch_menu_for_test(self):
        """The sketch right-click menu at the view's centre, as a right click opens it (GUI tests)."""
        view = self._mdi().viewport()
        return self._sketch_menu(view.mapToGlobal(view.rect().center()), False)

    def _sketch_menu(self, global_pos, flick):
        """The sketch's right-click menu with only what applies (the user: too many buttons): on a line or point
        Delete, Construction, its Constraints (n)…, Edit / Delete Pattern on a pattern's geometry, Break Mirror Link
        on a mirrored copy; on empty space the Constraint List; Stop Tool while a tool runs."""
        from incad.core import sketch_patterns
        sk = self.sketches.editing()
        picked = self._sketch_menu_pick(sk)
        subs, geo = self._sketch_menu_element(sk)
        drop = set()
        if subs:
            drop.add("sketch.constraints_window")  # its own Constraints (n)… instead, filtered to it
            if geo is None or geo < 0 or not sketch_patterns.pattern_of(sk, geo):
                drop |= {"sketch.edit_pattern", "sketch.delete_pattern"}
        else:
            drop |= {"sketch.construction", "sketch.edit_pattern", "sketch.delete_pattern"}
        if not picked:
            drop.add("edit.delete")  # the user: Delete for selected lines AND dimensions
        if self.slot_tool is None and not self._sketch_tool_active():
            drop.add("sketch.stop")
        return self._show_marking_menu("sketch", global_pos, flick, extras=self._constraint_menu_items(), drop=drop)

    def _sketch_menu_pick(self, sk):
        """What the menu's Delete acts on: a line, point or dimension under the cursor that is not selected becomes
        the selection (Inventor); one that is part of the selection keeps it. Returns the sketch's selected names."""
        import FreeCADGui as Gui
        if sk is None:
            return []
        doc = sk.Document.Name
        selected = [n for sel in Gui.Selection.getSelectionEx(doc) if sel.Object is sk for n in sel.SubElementNames if n]
        try:
            pre = Gui.Selection.getPreselection()
            under = [n for n in (getattr(pre, "SubElementNames", ()) or ()) if n] if getattr(pre, "Object", None) is sk \
                else []
        except RuntimeError:  # the preselected object was just deleted (a stale preselection)
            under = []
        if under and under[0] not in selected:
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(doc, sk.Name, under[0])
            selected = [under[0]]
        return selected

    def _sketch_menu_element(self, sk):
        """(selection names, geometry) of the line or point under the cursor, else the picked one; ([], None)."""
        import FreeCADGui as Gui
        if sk is None:
            return [], None
        try:
            pre = Gui.Selection.getPreselection()
            pre_subs = [n for n in (getattr(pre, "SubElementNames", ()) or ()) if n and not n.startswith("Constraint")] \
                if getattr(pre, "Object", None) is sk else []
        except RuntimeError:  # the preselected object was just deleted (a stale preselection)
            pre_subs = []
        subs = pre_subs[:1] or self._picked_sketch_elements(sk)[:1]
        if not subs:
            return [], None
        try:
            geo, _pos = self._element(sk, subs[0])
        except (ValueError, IndexError):
            return subs, None
        return subs, geo

    def _start_measuring(self, mode):
        """Measure stays live, as in Inventor: every change of the selection measures again (Esc finishes)."""
        import FreeCADGui as Gui
        self._stop_measuring()
        self._measure = _MeasurePicker(self, mode)
        Gui.Selection.addObserver(self._measure)

    def _stop_measuring(self):
        import FreeCADGui as Gui
        if self._measure is not None:
            try:
                Gui.Selection.removeObserver(self._measure)
            except Exception:
                pass
            self._measure = None

    def measuring(self):
        return self._measure.mode if self._measure is not None else None

    def _close_info_panel(self):
        if self.info_panel is not None:
            try:
                self.info_panel.close()
                dispose(self.info_panel)
            except RuntimeError:
                pass
            self.info_panel = None

    # ----- browser actions -----
    @staticmethod
    def _doc():
        import FreeCAD as App
        return App.ActiveDocument

    def _on_feature_activated(self, name):
        from incad.core import context
        context.set_target(name)
        self.registry.run("browser.edit")

    def _on_end_of_part_moved(self, name):
        from incad.core import document, safe
        doc = self._doc()
        if doc is None:
            return
        obj = doc.getObject(name) if name else None
        if obj is not None and document.body_of(obj) is None:
            return
        # the part's one history: every Solid rolls back to this point (Inventor's End of Part)
        r = safe.run_safely("Move End of Part", lambda: document.set_end_of_part_at(doc, obj), doc)
        if r.ok:
            log.report("info", "End of Part moved after %s" % obj.Label if obj is not None
                       else "End of Part moved before the first feature")
        self.schedule_refresh()

    def _on_browser_action(self, action):
        from incad.core import document, safe
        doc = self._doc()
        if action == "convert" and doc is not None:
            safe.run_safely(host.brand("Convert to InCad Part"),
                            lambda: document.convert_to_part(doc, prefs.default_units()), doc)
            self.schedule_refresh()
        elif action == "freecad":
            self.registry.run("view.freecad")

    def _on_parameter_set(self, name, text):
        from incad.core import document, safe
        from incad.core import sketch as sketches
        doc = self._doc()
        if doc is not None:
            from incad.core import model
            if model.is_model_parameter(doc, name):
                r = safe.run_safely("Set %s" % name, lambda: model.set_model_parameter(doc, name, text), doc)
            else:
                r = safe.run_safely("Set %s" % name, lambda: document.set_parameter(doc, name, text), doc)
            self._parameters_said(r)
        self.schedule_refresh()

    def _on_parameter_removed(self, name):
        from incad.core import document, safe, sketch
        doc = self._doc()
        if doc is not None:
            from incad.core import model
            sk, index = sketch.find_dimension(doc, name)
            if sk is not None:  # the user: a dimension can be deleted from Parameters too (Constraint List's rules)
                from incad.core import sketch_constraints

                def remove(doc, name):
                    sketch_constraints.delete(sk, [index])
                    doc.recompute()
            else:
                remove = model.remove_model_parameter if model.is_model_parameter(doc, name) \
                    else document.remove_parameter
            self._parameters_said(safe.run_safely("Remove %s" % name, lambda: remove(doc, name), doc))
        self.schedule_refresh()


# ----- module API -----
def instance():
    return _shell


def activate():
    global _shell
    import FreeCADGui as Gui
    log.catch_uncaught()
    if _shell is None:
        _shell = Shell(Gui.getMainWindow(), commands.REGISTRY)
    return _shell.activate()


def deactivate():
    if _shell is not None:
        _shell.deactivate()


def apply_theme():
    if _shell is not None and _shell.active:
        _shell.apply_theme()


def open_parameters():
    if _shell is not None and _shell.active:
        _shell.open_parameters_window()


SAVE_ASK = None  # tests: callable(files) -> ("save" | "discard" | "cancel", [names]) instead of the dialog


def _finish_open_tools():
    """Open dialogs and tools change documents when they close: close them before asking about saving."""
    if _shell is None or not _shell.active:
        return
    try:
        if _shell.asm_drag is not None and _shell.asm_drag.active():
            _shell.asm_drag.finish()  # its temporary grounds and open step must not reach the Save dialog
        if _shell.dof_handles is not None and _shell.dof_handles.dragging():
            _shell.dof_handles.finish()
        _shell.stop_slot_tool()
        _shell._close_feature_session(cancel=True)
        _shell._cancel_profile_pick()
        _shell.sketches.cancel_pick()
        if _shell.sketches.editing() is not None:
            _shell.sketches.finish()
    except Exception as e:
        log.report("warning", "Could not close the open tools: %s" % e, traceback.format_exc())


UPDATE_ASK = None  # tests: callable(assembly label) -> True to update
CONFIRM = None  # tests: callable(title, text) -> True for Yes


def confirm(title, text):
    """A Yes / No question; True for Yes."""
    import FreeCADGui as Gui
    if CONFIRM is not None:
        return CONFIRM(title, text)
    return QtWidgets.QMessageBox.question(Gui.getMainWindow(), title, text,
                                          QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No) \
        == QtWidgets.QMessageBox.Yes


def ask_save(docs, action="quitting"):
    """InCad's Save dialog for the changed documents among `docs`; False when cancelled. Answered documents are
    marked unchanged so FreeCAD doesn't ask again."""
    import FreeCADGui as Gui
    changed = [d for d in docs if Gui.getDocument(d.Name) is not None and Gui.getDocument(d.Name).Modified]
    if not changed:
        return True
    files = [(d.Label, d.FileName) for d in changed]
    if SAVE_ASK is not None:
        choice = SAVE_ASK(files)
    else:
        from incad.ui.save_dialog import SaveDialog
        dlg = SaveDialog(files, _shell.theme if _shell is not None else themes.current(), Gui.getMainWindow(),
                         action)
        dlg.exec()
        choice = dlg.result_choice()
    if choice[0] == "cancel":
        return False
    from incad.core import assembly
    for d in changed:
        if choice[0] == "save" and d.Label in choice[1] and assembly.is_assembly(d):
            from incad import actions  # an assembly goes into its folder with its parts (the user)
            if actions.save_assembly(d) is None:
                return False
            continue
        if choice[0] == "save" and d.Label in choice[1]:
            if d.FileName:
                d.save()
            else:
                path, _ = QtWidgets.QFileDialog.getSaveFileName(Gui.getMainWindow(), "Save %s" % d.Label,
                                                                d.Label + ".FCStd", "FreeCAD files (*.FCStd)")
                if not path:
                    return False
                d.saveAs(path if path.lower().endswith(".fcstd") else path + ".FCStd")
        Gui.getDocument(d.Name).Modified = False
    return True


def _ask_unsaved():
    import FreeCAD as App
    _finish_open_tools()
    return ask_save(list(App.listDocuments().values()), "quitting")


def _mark_answered():
    import FreeCAD as App
    import FreeCADGui as Gui
    for d in App.listDocuments().values():
        g = Gui.getDocument(d.Name)
        if g is not None:
            g.Modified = False


def _release_python_markers():
    """PySide marks every Qt object Python has touched with a '_PySideInvalidatePtr' property. On a Dock/app-menu
    quit macOS ends the process before Qt destroys the widgets, after Python is gone, and destroying a marked
    object then crashes (probed). The process is ending: drop the markers while Python still runs."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    objs = list(app.allWidgets()) + list(app.findChildren(QtCore.QObject))
    for top in app.topLevelWidgets():
        objs += list(top.findChildren(QtCore.QObject))
    for o in objs:
        try:
            if b"_PySideInvalidatePtr" in [bytes(n) for n in o.dynamicPropertyNames()]:
                o.setProperty("_PySideInvalidatePtr", None)
        except RuntimeError:
            pass


def _alive(obj):
    try:
        return obj.Document is not None
    except Exception:
        return False


def _is_start_page(window):
    """FreeCAD's Start page, by its tab title (InCad's UI is English; looking inside the window would leave a
    Python handle on it)."""
    try:
        return window.windowTitle() == "Start"
    except RuntimeError:
        return False


_FILTERED_EVENTS = (QtCore.QEvent.Resize, QtCore.QEvent.Close, QtCore.QEvent.ChildAdded, QtCore.QEvent.Show)


def _resume_after_cancelled_quit():
    """FreeCAD cancelled the quit (or the window stayed open): InCad starts again."""
    import FreeCADGui as Gui
    from incad.qt import dispose_now
    if QtWidgets.QApplication.activeModalWidget() is not None:  # e.g. a question still open: wait for it
        QtCore.QTimer.singleShot(300, _resume_after_cancelled_quit)
        return
    try:
        dispose_now(False)
        wb = Gui.activeWorkbench()
        if wb is not None and wb.name() == "InCadWorkbench" and Gui.getMainWindow().isVisible():
            activate()
    except Exception as e:
        log.report("warning", "InCad could not start again: %s" % e, traceback.format_exc())


def shutdown():
    """FreeCAD is quitting: switch InCad off (FreeCAD's interface back, saved clean) and delete every Qt object
    InCad made now — not later, when Python is already finalized."""
    global _shell
    from incad.qt import dispose_now
    try:
        dispose_now(True)
        sh = _shell
        nav = getattr(sh, "navigator", None) if sh is not None else None
        if sh is not None and sh.active:
            sh.deactivate()
        # InCad's own event-filter objects go too: deleting a filter detaches it from every widget, so Qt never
        # calls into Python while the process ends (probed crash: QObjectWrapper::eventFilter at exit)
        for obj in (nav, sh):
            if obj is not None:
                dispose(obj)
        _shell = None
        app = QtCore.QCoreApplication.instance()
        if app is not None:
            app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        _release_python_markers()
    except Exception as e:
        log.report("warning", "InCad could not shut down cleanly: %s" % e, traceback.format_exc())


def recover_after_crash():
    """At startup: if a snapshot survived (quit or crash while InCad was active) and InCad is not the active
    workbench, put FreeCAD's interface back the way it was."""
    import FreeCADGui as Gui
    text = prefs.takeover_snapshot()
    if not text:
        return
    wb = Gui.activeWorkbench()
    if wb is not None and wb.name() == "InCadWorkbench":
        return  # activate() reuses the saved snapshot
    try:
        Shell(Gui.getMainWindow(), commands.REGISTRY)._restore(json.loads(text))
        log.report("info", "InCad restored FreeCAD's interface after an unexpected exit")
    except Exception as e:
        log.report("warning", "InCad could not restore FreeCAD's interface: %s" % e, traceback.format_exc())
    finally:
        prefs.clear_takeover_snapshot()


def toggle_nav_mode(mode):
    if _shell is not None and _shell.active and _shell.navigator is not None:
        _shell.navigator.set_mode(None if _shell.navigator.mode == mode else mode)


def nav_mode():
    if _shell is not None and _shell.active and _shell.navigator is not None:
        return _shell.navigator.mode
    return None


def sketch_session():
    if _shell is None or not _shell.active:
        raise ValueError("InCad is not active")
    return _shell.sketches


def open_constraints_window():
    if _shell is None or not _shell.active:
        raise ValueError("InCad is not active")
    _shell.open_constraints_window()


def start_feature(kind):
    if _shell is None or not _shell.active:
        raise ValueError("InCad is not active")
    _shell.start_feature(kind)


def inspect(what):
    if _shell is None or not _shell.active:
        raise ValueError("InCad is not active")
    _shell.inspect(what)


def measuring():
    return _shell.measuring() if _shell is not None and _shell.active else None


def start_section(base):
    return _active().start_section(base)


def start_section_points():
    return _active().start_section_points()


def section_on():
    return _shell is not None and _shell.active and _shell.section_on()


def stop_section(keep_cut=False):
    if _shell is not None and _shell.active:
        _shell.stop_section(keep_cut)


def _active():
    if _shell is None or not _shell.active:
        raise ValueError("InCad is not active")
    return _shell


def assembly_place(path=None):
    return _active().assembly_place(path)


def assembly_create(name=None):
    return _active().assembly_create(name)


def assembly_ground(link=None):
    return _active().assembly_ground(link)


def assembly_suppress(link=None):
    return _active().assembly_suppress(link)


def assembly_edit(link=None):
    return _active().assembly_edit(link)


def assembly_remove(link=None):
    return _active().assembly_remove(link)


def toggle_dof():
    return _active().toggle_dof()


def toggle_interference():
    return _active().toggle_interference()


def interference_shown():
    return _shell is not None and _shell.interference_shown()


def toggle_assembly_context():
    return _active().toggle_assembly_context()


def return_to_assembly():
    return _active().return_to_assembly()


def assembly_context_shown():
    sh = _shell
    return bool(sh is not None and sh.active and sh.assembly_context_shown())


def dof_shown():
    sh = _shell
    return bool(sh is not None and sh.active and sh.dof_shown())


def current_tree_object():
    """Name of the model-tree row in focus (a feature or sketch), else None."""
    sh = _shell
    if sh is None or not sh.active or sh.browser is None:
        return None
    try:
        tree = sh.browser.tree
        item = tree.currentItem()
        if item is None or not tree.hasFocus():
            return None
        from incad.ui.browser import KIND_ROLE, NAME_ROLE
        return item.data(0, NAME_ROLE) if item.data(0, KIND_ROLE) in ("feature", "sketch") else None
    except RuntimeError:
        return None


def toggle_construction():
    return _active().toggle_construction()


def start_slot_tool(mode):
    return _active().start_slot_tool(mode)


def open_component(link, context=True):
    return _active().open_component(link, context)


def assembly_open(link=None):
    return _active().assembly_open(link)


def edit_feature(feature):
    if _shell is None or not _shell.active:
        raise ValueError("InCad is not active")
    _shell.edit_feature(feature)
