"""Which commands the right-click marking menu shows, per context. Slots N…NW sit around the cursor (flick
directions); 'list' is the short list below the ring. Later plans move sketch/feature commands into the slots."""
DIRECTIONS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

_EDGE = {"N": "view.look_at", "S": "view.zoom_selection", "E": "view.style_shaded", "W": "view.style_wireframe",
         "NW": "edit.undo", "NE": "edit.redo", "SW": "view.zoom_all", "SE": "view.home", "list": []}

LAYOUT = {
    "part": {"N": "sketch.start", "NE": "edit.redo", "E": "view.style_shaded", "SE": "view.parameters",
             "S": "feature.extrude", "SW": "view.theme_toggle", "W": "view.style_wireframe", "NW": "edit.undo",
             # Zoom All and Home are on the view strip; the shell leaves out what does nothing here
             # (incad.ui.menu_rules)
             "list": ["view.freecad", "view.hide", "view.show", "view.show_all_parts", "tools.appearance",
                      "view.origin_show_all", "view.origin_hide_all", "file.make_assembly", "feature.move_face"]},
    "face": {"N": "sketch.start", "NE": "view.look_at", "E": "view.style_shaded", "SE": "view.home",
             "S": "feature.extrude", "SW": "view.zoom_all", "W": "view.style_wireframe", "NW": "edit.undo",
             "list": ["view.edit_feature", "view.show_in_browser", "view.zoom_selection", "feature.hole", "feature.fillet", "feature.move_face",
                      "feature.thread", "view.hide", "view.show", "view.show_all_parts", "tools.appearance"]},
    "edge": dict(_EDGE, E="feature.fillet", W="feature.chamfer",
                 list=["view.edit_feature", "view.show_in_browser", "view.style_shaded", "view.style_wireframe",
                       "view.hide", "view.show",
                       "view.show_all_parts"]),
    "vertex": dict(_EDGE, list=["view.show_in_browser", "view.hide", "view.show", "view.show_all_parts"]),
    "sketch": {"N": "sketch.finish", "NE": "sketch.line", "E": "sketch.circle", "SE": "sketch.rectangle",
               "S": "sketch.dimension", "SW": "sketch.arc", "W": "edit.undo", "NW": "sketch.trim",
               # the shell keeps only what applies to the line or point under the cursor (the user: too many buttons)
               "list": ["sketch.stop", "edit.delete", "sketch.construction", "sketch.break_link",
                        "sketch.constraints_toggle",
                        "sketch.constraints_window", "sketch.edit_pattern", "sketch.delete_pattern"]},
    "assembly": {"N": "assembly.place", "S": "constrain.mate", "E": "joint.rigid", "W": "assembly.create",
                 "NE": "view.zoom_all", "NW": "edit.undo", "SE": "view.home", "SW": "assembly.ground",
                 "list": ["assembly.edit", "assembly.open", "view.hide", "view.show", "view.show_all_parts", "assembly.suppress", "assembly.remove",
                          "constrain.flush", "constrain.insert", "view.freecad"]},
    "asm_face": {"N": "constrain.mate", "S": "constrain.flush", "E": "constrain.angle", "W": "constrain.tangent",
                 "NE": "view.look_at", "SW": "assembly.ground",
                 "list": ["assembly.edit", "assembly.open", "view.hide", "view.show", "view.show_all_parts", "assembly.suppress", "assembly.remove",
                          "joint.rigid", "constrain.parallel", "constrain.perpendicular", "view.zoom_selection"]},
    "asm_edge": {"N": "constrain.insert", "S": "joint.rotational", "E": "joint.cylindrical", "W": "joint.slider",
                 "SW": "assembly.ground", "list": ["assembly.edit", "assembly.open", "view.hide", "view.show", "view.show_all_parts", "assembly.suppress",
                                                   "assembly.remove", "joint.rigid", "constrain.parallel",
                                                   "constrain.perpendicular", "constrain.angle",
                                                   "view.zoom_selection"]},
    "empty": {"N": "file.new", "S": "file.open", "E": "view.theme_toggle", "W": "view.freecad", "list": []},
    "origin": {"N": "sketch.start", "S": "view.toggle_visibility", "list": ["sketch.start", "view.hide", "view.show"]},
    "solid": {"N": "view.toggle_visibility", "S": "browser.rename", "E": "browser.show_in_window",
              "W": "browser.delete", "NE": "browser.edit_solid", "list": ["browser.edit_solid", "view.hide", "view.show", "view.show_all_parts", "browser.show_in_window", "browser.rename",
                                              "tools.appearance", "browser.break_mirror", "browser.delete"]},
    "browser": {"N": "browser.edit", "S": "browser.move_end_of_part", "E": "browser.suppress",
                "W": "browser.rename", "list": ["browser.edit", "browser.show_in_window", "sketch.start", "browser.break_mirror",
                         "browser.delete", "view.hide", "view.show", "view.show_all_parts"]},
    "joint": {"N": "browser.edit", "S": "browser.delete", "E": "constraint.suppress", "W": "browser.rename",
              "list": ["browser.edit", "browser.delete", "constraint.suppress", "browser.rename"]},
    "picture": {"N": "view.toggle_visibility", "E": "browser.edit", "S": "browser.rename", "W": "browser.delete",
                "list": ["browser.edit", "view.hide", "view.show", "view.show_all_parts", "browser.rename", "browser.delete"]},
    "asm_cut": {"N": "browser.edit", "S": "browser.delete", "W": "browser.rename",
                "list": ["browser.edit", "browser.delete", "browser.rename"]},
}


def resolve(context, registry, layout=None):
    """([(direction, Command)] in N…NW order, [Command] for the list); unknown command ids are skipped."""
    spec = (LAYOUT if layout is None else layout).get(context, {})
    slots = [(d, registry.get(spec[d])) for d in DIRECTIONS if spec.get(d) and registry.has(spec[d])]
    items = [registry.get(i) for i in spec.get("list", []) if registry.has(i)]
    return slots, items
