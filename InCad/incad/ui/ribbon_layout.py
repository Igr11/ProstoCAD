"""Which commands sit on which ribbon tab and group, per mode. Later plans add groups with add_group().
Modes: 'part' (an InCad Part is active), 'empty' (no document, or a file not made in InCad);
'sketch' and 'assembly' arrive with later plans."""

LAYOUT = {
    "part": [
        ["3D Model", [["Sketch", ["sketch.start", "feature.line3d"]],
                      ["Create", ["feature.extrude", "feature.revolve", "feature.hole",
                                  ["feature.sweep", "feature.loft", "feature.coil"], ["feature.gear", "feature.worm"],
                                  "feature.emboss", ["feature.boss", "feature.lip", "feature.snap_fit"]]],
                      ["Modify", [["feature.fillet", "feature.full_round", "feature.chamfer"], ["feature.shell", "feature.draft"],
                                  "feature.thread", "feature.threads_toggle", "feature.combine", "feature.split",
                                  "feature.move_face"]],
                      ["Pattern", [["feature.rect_pattern", "feature.circ_pattern", "feature.mirror"], "feature.mirror_solid"]],
                      ["Work Features", [["work.plane", "work.axis", "work.point"]]],
                      ["Assemble", ["file.make_assembly"]]]],
        ["Sketch", []],
        ["Inspect", [["Measure", [["inspect.measure", "inspect.distance", "inspect.angle", "inspect.length",
                                   "inspect.area"], "inspect.mass"]],
                     ["Analyze", ["assembly.interference"]]]],  # between a part's Solids (a jar and its lid)
        ["Image", [["Insert", ["picture.insert"]], ["Scale", ["picture.scale"]],
                     ["Place", [["picture.on_xy", "picture.on_xz", "picture.on_yz"], "picture.turn", "picture.mirror"]],
                     ["Look", ["picture.show_hide"]]]],
        ["Tools", [["Material", ["tools.material", "tools.appearance"]], ["Options", ["view.freecad", "tools.ask_on_new"]]]],
        ["View", [["Appearance", ["view.theme_toggle"]],
                  ["Navigate", ["view.zoom_all", "view.home", "view.look_at", "view.zoom_selection", "nav.orbit", "nav.pan"]],
                  ["Visual Style", ["view.style_shaded", "view.style_wireframe", ["view.section", "view.section_points"],
                                    ["view.hide", "view.show", "view.show_all_parts", "view.origin_show_all",
                                     "view.origin_hide_all", "view.sketches_show_all", "view.sketches_hide_all"],
                                    "view.assembly_context"]],
                  ["Capture", [["view.screenshot", "view.screenshot_window"]]],
                  ["Windows", ["view.parameters"]]]],
    ],
    "sketch": [
        # the 0.18 New Sketch trial: the new sketch's buttons, as chosen on 2026-11-07 (59 kept; Ellipse by axes,
        # Overall Slot, Flat-End Arc Slot, Triangle, Square, Pentagon, Octagon, Polygon (N sides) and Overall
        # Dimension are covered by the new sketch's own tools; Tangent Arc, Rotate and Dim Construction Lines are new)
        ["Sketch", [["Cancel", ["sketch.cancel"]],
                    ["Create", [["sketch.line", "sketch.line_single"], ["sketch.spline", "sketch.spline_cv"],
                                ["sketch.circle", "sketch.circle_3pt", "sketch.circle_tangent", "sketch.ellipse_3pt"],
                                ["sketch.arc", "sketch.arc_center", "sketch.arc_tangent", "sketch.arc_ellipse"],
                                ["sketch.rectangle", "sketch.rectangle_center", "sketch.rectangle_3pt",
                                 "sketch.rectangle_center_3pt", "sketch.rectangle_rounded"],
                                ["sketch.slot", "sketch.slot_center", "sketch.slot_arc"],
                                "sketch.polygon", "sketch.point", "sketch.text", "feature.line3d"]],
                    ["Modify", [["sketch.fillet", "sketch.chamfer"], "sketch.trim", "sketch.extend", "sketch.offset",
                                "sketch.rotate"]],
                    ["Pattern", [["sketch.rect_pattern", "sketch.circ_pattern", "sketch.path_pattern"], "sketch.mirror"]],
                    ["Dimension", ["sketch.dimension"]],
                    ["Constrain", ["sketch.coincident", "sketch.concentric", "sketch.horizontal", "sketch.vertical", "sketch.parallel",
                                   "sketch.perpendicular", "sketch.tangent", "sketch.equal", "sketch.symmetric",
                                   "sketch.fix", "sketch.constraints_window"], "stacked"],
                    ["Insert", ["sketch.project"]],
                    ["Format", ["sketch.construction", "sketch.dim_construction", "sketch.constraints_toggle",
                                "sketch.show_dof", "sketch.slice"], "stacked"],
                    ["Exit", ["sketch.finish"]]]],
        ["View", [["Appearance", ["view.theme_toggle"]],
                  ["Navigate", ["view.zoom_all", "view.home", "nav.orbit", "nav.pan"]],
                  ["Visual Style", ["view.style_shaded", "view.style_wireframe", ["view.section", "view.section_points"]]],
                  ["Capture", [["view.screenshot", "view.screenshot_window"]]],
                  ["Windows", ["view.parameters"]]]],
    ],
    "assembly": [
        ["Assemble", [["Component", ["assembly.place", "assembly.create", "assembly.ground", ["assembly.edit", "assembly.open"], "assembly.combine",
                                     "assembly.threads_toggle"]],
                      ["Constrain", ["constrain.mate", "constrain.flush", "constrain.angle", "constrain.parallel",
                                     "constrain.perpendicular", "constrain.tangent", "constrain.insert"]],
                      ["Joint", ["joint.rigid", "joint.rotational", "joint.slider", "joint.cylindrical",
                                 "joint.ball"]],
                      ["Analyze", ["assembly.interference"]]]],
        ["View", [["Appearance", ["view.theme_toggle"]],
                  ["Navigate", ["view.zoom_all", "view.home", "view.look_at", "view.zoom_selection", "nav.orbit", "nav.pan"]],
                  ["Visual Style", ["view.style_shaded", "view.style_wireframe", ["view.section", "view.section_points"],
                                    ["view.hide", "view.show", "view.show_all_parts", "view.axes_show_all",
                                     "view.axes_hide_all",
                                     "view.origin_show_all", "view.origin_hide_all", "view.sketches_show_all",
                                     "view.sketches_hide_all"], "view.dof"]],
                  ["Capture", [["view.screenshot", "view.screenshot_window"]]],
                  ["Windows", ["view.parameters"]]]],
    ],
    "empty": [
        # New is in the title bar: the first button starts the sketch (a new part comes with it)
        ["Get Started", [["Start", ["sketch.start", "file.new_assembly", "file.open"]]]],
        ["View", [["Appearance", ["view.theme_toggle"]]]],
    ],
}


def tabs_of(mode, layout=None):
    """The tabs of a mode. 'part_edit', a part opened with Edit from its assembly, is 'part' with Return first on
    3D Model (Inventor: Return; the user found no way to finish editing the part — at the end of the tab it was cut
    off on a narrower screen)."""
    layout = LAYOUT if layout is None else layout
    if mode != "part_edit":
        return layout.get(mode, [])
    tabs = [[name, list(groups)] for name, groups in layout.get("part", [])]
    if tabs:
        tabs[0][1].insert(0, ["Return", ["assembly.return"]])
    return tabs


def add_group(mode, tab, group, cmd_ids, layout=None):
    """Append a group of commands to a tab (the tab is created at the end if missing)."""
    layout = LAYOUT if layout is None else layout
    tabs = layout.setdefault(mode, [])
    for name, groups in tabs:
        if name == tab:
            groups.append([group, list(cmd_ids)])
            return
    tabs.append([tab, [[group, list(cmd_ids)]]])
