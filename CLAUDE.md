# InCad

Inventor/Alibre-style workbench for FreeCAD 1.1.3 (macOS). Spec: docs/superpowers/specs/, plans: docs/superpowers/plans/.

- Module: `InCad/` (package `incad`), installed by `scripts/install.sh` as a symlink in
  `~/Library/Application Support/FreeCAD/v1-1/Mod/InCad`. Restart FreeCAD to load code changes.
- Tests: `tests/run_widgets.sh` (offscreen PySide6), `tests/run_headless.sh` (freecadcmd),
  `tests/run_gui.sh` (real FreeCAD window — tell the user to keep hands off), `tests/run_all.sh`.
  Every FreeCAD run uses `-u <temp>/user.cfg`, so the user's settings are never touched.
- FreeCAD facts: bundled Python has no pytest (use unittest); `PySide` is FreeCAD's shim (PySide6 outside);
  new documents have `UndoMode == 0` — `incad.core.safe` turns it on; a failed feature has `'Invalid'` in
  `obj.State` and the reason in `obj.getStatusString()`; `Document.restore` pumps the Qt event loop (progress bar)
  on files that take long to load, so a deferred timer that recomputes runs mid-load and crashes the Sketcher —
  check `doc.Restoring` first (`tests/gui/test_open_during_restore.py`).
- UI text English. Chat with the user in any language.
- Messages in windows: errors (something is wrong) in red; hints (what to do next) in blue, the theme's accent colour. The new windows (`feature_window.py`, trial 0.18 and the new program) keep their hints grey. Warnings in the status line stay red. Agreed with the user on 2026-11-09.
- A failing test: first check whether it still describes how the program should work now. If the program changed on purpose (an agreed change), the test is outdated: update the test to the new behaviour, never turn the program back. If the change was not agreed, the failure is a bug. Unsure which: ask. The program changes, so its tests cannot stay old. Agreed with the user on 2026-11-09.
- 1b facts: `view.getCameraNode()` needs `from pivy import coin` first; Coin pixel positions are device px with a
  bottom-left origin (`incad.ui.nav.to_coin`); FreeCAD opens its 3D context menu on the right-button release, so
  InCad's gesture rules never pass a lone right release.
- Sketcher facts: a constraint expression may use `InCadModel.dN` and driving constraints, never a reference
  constraint of the same sketch; FreeCAD gives every geometry a permanent id (`getGeometryId`) that survives
  deletions and reload and that the pad's element names derive from, so never `setGeometryId` on existing
  geometry; virtual space hides constraints, never geometry (sketch patterns: `incad.core.sketch_patterns`).
  A running Sketcher tool is visible to Python only as the bitmap cursor on the viewport; FreeCAD's spline
  and polyline tools finish on a right click (press+release), which `incad.ui.nav` replays while that cursor shows.
  The Constraint List (`incad.core.sketch_constraints`, `incad.ui.constraints_panel`) lists and deletes constraints;
  pattern ties and dimensions used by a formula are refused; FreeCAD's preselection can name a deleted object (guard it).
- Visibility: a new part's origin planes say Visible while their App::Origin group is hidden (`document.is_shown` is the
  effective state); unused sketches follow `InCadShown` (`document.sketch_shown`: open contours shown by default);
  view markers (`incad.ui.point_markers`) are SoAnnotation nodes, never document objects; a raw `Gui.setEdit` on a
  sketch switches workbenches and crashed in a test, open sketches through `shell.sketches.edit`.
- Sketch patterns Along Path (`core/sketch_patterns.build_path`) place copies exactly and tie them with constant
  constraints (re-placed on edit); a Sweep's Wall is an inner `PartDesign::SubtractivePipe` of the offset profile plus cap Pads (helpers with
  `InCadHelperOf`; `kind_for` claims none, their rows open the owner); a body-wide Thickness hollows the whole part.
- Thread helpers: a fill (Revolution) or bore-out (Groove) with `InCadHelperRole = "prep"` sits right before the thread
  (a hole off the size's tap diameter, filled only where the hole is walled all round), the chamfer Groove after it;
  Suppress acts on the owner with all its helpers, and a profile sketch or helper means its owner (`incad.core.suppress`).
- A cut / join through a modeled thread (Thread helix, Hole ▸ ModelThread) breaks in the kernel (invalid cut, a join keeps
  only the new piece): `incad.core.thread_order.settle` (feature dialogs) puts it before the thread with its sketch, the
  sketch's attachment found again on the earlier shape, thread-only projections let go (`tests/gui/test_revolve_thread.py`).
- Hole ▸ Chamfer is a `PartDesign::FeaturePython` helper (`incad.features.hole_chamfer`) right after the Hole that cuts
  cones computed from the hole on every recompute (no edge names); a kind may grey out choices with `unavailable(values)`.
- DOF gizmo (`incad.ui.dof_handles`): the selected free assembly part's glyph is its handles, hit-tested in screen space
  (`HANDLE_PX`), dragged by InCad itself through `Navigator.handle_hook` (never reaches FreeCAD, no setEdit): other free
  parts temporarily grounded, solve per move, one "Move part" step; a far turn axis has its arc drawn at the part's
  centre (`Handle.pivot` keeps the real axis).
- Speed (0.5.2): a recompute's progress bar runs deferred handlers mid-recompute too — a handler that recomputes or
  writes must wait for `doc.Recomputing` to end; write sketch properties only when they differ (an equal write still
  touches and recomputes everything after). Feature dialogs preview with `doc.recompute([feature + helpers])` (part
  rolled back to the edited feature, the rest once on OK) and check only the shapes that preview computed;
  `set_model_parameter(..., everything=False)` inside dialogs. `incad.core.perf` logs `Slow:` calls and event-loop stalls.
  `InCadShown` is an `Output` property (a Hide / Show choice touches nothing: on the origin it rebuilt the whole part);
  work features recompute only themselves (`doc.recompute([f])`, the rest on OK).
- Overall dimensions (`incad.core.overall_dims`): a construction axis through two arc centres + a point where it leaves
  each arc; the value box offers Centers / Overall / Gap for any distance between two arcs (FreeCAD's own is the gap).
- Gears (from InnCad Plus): Spur Gears / Worm Gear are `PartDesign::FeaturePython` with `InCadGear` settings
  (`incad.features.gear`, teeth from FreeCAD's own `fcgear` in `core/gears`); a set is a lead gear + a second in its own
  Solid (delete the lead = both); `ui/gear_gizmo` drives Offset / Turn; Make Assembly bakes mates and joins each gear by a
  Rotational joint to a hidden grounded Gear Axes (`core/gear_train`), the DOF gizmo turns meshing gears by their ratio.
- Windows (untested build, `scripts/make_dist_windows.sh`): what differs per system lives in `incad.host`.
- Model values live in one VarSet (InCadModel): FreeCAD touches every object using ANY value when one changes — wrap
  value changes in `model.changing_values` (only users of a changed value stay touched); `bind` touches its feature.
  A Solid's Visibility write, even unchanged, touches the Boolean using it: write visibility via
  `document._set_visible` (only on change). In-sketch dialogs (Text, sketch patterns) recompute only the sketch and
  the object.
- Emboss on a sphere (`wrap._project_sphere`): each closed profile face is a prism along the sketch normal (from the
  plane through the centre) cut by the spherical shell; `BODY_FROM = "face"` makes a feature session re-create the
  feature in the picked face's Solid (the profile sketch is adopted there). ⚙ ▸ Display Quality (`incad.core.quality`)
  writes FreeCAD's Mod/Part MeshDeviation / MeshAngularDeflection and sets open files' view providers (keeps
  `Gui.Document.Modified`).
