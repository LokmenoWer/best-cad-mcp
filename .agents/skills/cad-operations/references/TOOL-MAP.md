# best-cad-mcp Tool Map

This catalog reflects the current server surface: 256 registered MCP tools parsed from
`src/server.py` and `src/cad_tools/*.py`. Names below are MCP tool names. Prefer these tools over
manual primitive composition.

Internal helpers not registered as MCP tools include `save_layers_to_db`, `format_angle`, and
`format_distance`.

## 0. Discovery and Help

| Tool | Use when |
|---|---|
| `recommend_cad_tools(intent, max_results?)` | Route a natural-language CAD intent to purpose-built tools. Use early when unsure. |
| `get_tool_help(tool_name?)` | Get server-side help for one tool or a category overview. |
| `get_application_info()` | Check AutoCAD application/version/path state. |
| `is_autocad_idle()` | Confirm AutoCAD is ready before interactive or long operations. |

## 1. Drawing Lifecycle and Document State

| Tool | Use when |
|---|---|
| `create_new_drawing(template?)` | Start a new drawing, optionally from a DWT template. |
| `open_drawing(filepath, password?)` | Open an existing DWG. |
| `save_drawing(filepath?)` | Save the current drawing or save-as to a path. |
| `close_drawing(save?)` | Close the active drawing. |
| `get_document_info()` | Read drawing metadata, counts, active layer/style, and document state. |
| `set_document_properties(...)` | Set title, subject, author, keywords, or comments. |
| `set_drawing_password(password)` | Password-protect the drawing on save. |
| `get_file_dependencies()` | Inspect xrefs, images, fonts, and other external dependencies. |
| `get_active_space_info()` | Determine model space vs paper/layout space. |
| `create_snapshot(name?)` | Record a named drawing state snapshot. |
| `get_snapshots(limit?)` | List recent snapshots. |

## 2. Variables, Preferences, Cleanup, Undo

| Tool | Use when |
|---|---|
| `get_variable(variable_name)` / `set_variable(variable_name,value)` | Read or set AutoCAD sysvars such as `INSUNITS`, `LTSCALE`, `DIMSCALE`. |
| `get_preference(pref_path)` / `set_preference(pref_path,value)` | Read or write a specific AutoCAD preference. |
| `get_preferences_display()` | Inspect display preferences. |
| `get_preferences_drafting()` | Inspect drafting/autosnap preferences. |
| `get_preferences_files()` | Inspect support/template/font paths. |
| `get_preferences_opensave()` | Inspect open/save preferences. |
| `get_preferences_selection()` | Inspect selection preferences. |
| `get_preferences_system()` | Inspect system preferences. |
| `get_preferences_user()` | Inspect user preferences. |
| `undo(count?)` / `redo(count?)` | Step backward/forward. Use before risky operations. |
| `regen(which?)` | Regenerate the drawing view/database. |
| `purge_drawing()` | Remove unused definitions after user approval or at cleanup. |
| `audit_drawing()` | Check and repair drawing database errors. |
| `send_command(command)` | Raw AutoCAD command escape hatch. Last resort. |

## 3. 2D Drawing and Construction Geometry

Use these to create real CAD geometry. Primitive tools are allowed for their own simple object,
but not as substitutes for higher-level tools.

| Tool | Use when |
|---|---|
| `draw_line(...)` | One straight segment only. Last resort for composed shapes. |
| `draw_circle(...)` | Circle, hole, round marker. |
| `draw_arc(...)` | Circular arc by center/radius/start/end angle. |
| `draw_ellipse(...)` | Ellipse. |
| `draw_polyline(points, closed?)` | Connected path, boundaries, routes, irregular closed outlines. |
| `draw_3d_polyline(points, closed?)` | Polyline with Z per vertex. |
| `draw_rectangle(x1,y1,x2,y2,...)` | Any rectangle or rectangular opening/frame. |
| `draw_polygon(center_x,center_y,radius,sides,...)` | Regular polygon. |
| `draw_spline(fit_points,...)` | Smooth free-form curve. |
| `draw_point(x,y,z?)` | Survey point or node marker. |
| `draw_donut(...)` | Ring/washer/donut. |
| `draw_ray(...)` | One-direction infinite construction line. |
| `draw_xline(...)` | Bidirectional infinite construction/axis line. |
| `draw_mline(points,...)` | Multiline/parallel wall or route. |
| `draw_2d_solid(...)` | Filled triangular/quadrilateral region. |
| `draw_raster_image(...)` | Insert image underlay/reference. |
| `draw_tolerance(...)` | GD&T feature control frame. |
| `draw_trace(points,...)` | Wide trace. |
| `add_shape(...)` | Insert a symbol from a loaded SHX shape file. |

## 4. Polyline Detailing

Use these after `draw_polyline` when a polyline needs arcs, width, or vertex editing.

| Tool | Use when |
|---|---|
| `polyline_set_bulge(handle,index,bulge)` / `polyline_get_bulge(...)` | Make/read an arc segment. |
| `polyline_set_width(handle,seg_index,start_width,end_width)` / `polyline_get_width(...)` | Tapered, arrow, or variable-width segments. |
| `polyline_add_vertex(handle,index,x,y)` | Insert a vertex. |
| `polyline_constant_width(handle,width?)` | Get/set uniform polyline width. |
| `polyline_num_vertices(handle)` | Count vertices. |
| `polyline_get_point_at_param(handle,param)` | Sample a point along the curve. |
| `polyline_get_segment_type(handle,index)` | Determine whether a segment is line or arc. |

## 5. Editing and Entity Properties

Never delete-and-redraw to simulate these operations. Capture handles and edit entities directly.

| Tool | Use when |
|---|---|
| `move_entity(handle,from_point,to_point)` | Move an entity. |
| `rotate_entity(handle,base_point,angle)` | Rotate in the XY plane. |
| `copy_entity(handle,from_point?,to_point?)` | Duplicate one entity. For repeated grids/circles, use arrays. |
| `delete_entity(handle)` / `delete_entities(handles)` | Delete by known handles. Confirm selection first. |
| `mirror_entity(handle,line_start,line_end)` | Mirror an entity. |
| `scale_entity(handle,base_point,scale_factor)` | Uniform scale. |
| `offset_entity(handle,distance)` | Parallel/concentric offset. |
| `array_rectangular(handle,rows,cols,...)` | Rectangular pattern. |
| `array_polar(handle,count,fill_angle,center...)` | Circular pattern. |
| `explode_entity(handle)` | Explode block/polyline/dimension when explicitly needed. |
| `fillet_entities(...)` / `chamfer_entities(...)` | Round or bevel two edges. |
| `trim_entity(...)` / `extend_entity(...)` | Trim/extend to boundaries. |
| `break_entity(...)` | Split or remove a span. |
| `join_entities(handles)` | Join contiguous entities. |
| `stretch_entities(...)` | Stretch selected geometry while keeping connectivity. |
| `lengthen_entity(...)` | Delta/percent/total length changes. |
| `divide_entity(handle,segments,block?)` | Divide by equal count. |
| `measure_entity(handle,length,block?)` | Mark by fixed interval. |
| `align_entities(handles,points)` | Align by source/target point pairs. |
| `chamfer_polyline(handle,...)` / `fillet_polyline(handle,...)` | Apply to all polyline corners. |
| `set_entity_properties(...)` | Change layer, color, linetype, lineweight, etc. |
| `get_entity_properties(handle)` | Inspect full entity properties/geometry. |
| `set_entity_truecolor(handle,r,g,b)` | Exact RGB color. Prefer layer styling for normal drawings. |
| `set_entity_transparency(handle,value)` | Fade reference/watermark entities. |
| `set_entity_plot_style(handle,name)` | Plot-style override. |
| `get_extension_dictionary(handle)` | Inspect entity extension dictionary/XRecords. |

## 6. Layers, Linetypes, and Visibility

Set up layers before drawing. Draw with `color="bylayer"` unless the user requests otherwise.

| Tool | Use when |
|---|---|
| `create_layer(name,color?,linetype?)` | Create/recolor a logical layer. |
| `delete_layer(name)` / `rename_layer(old,new)` | Manage layer definitions. |
| `freeze_layer(name)` / `thaw_layer(name)` | Freeze/thaw. |
| `lock_layer(name)` / `unlock_layer(name)` | Protect or unlock a layer. |
| `turn_off_layer(name)` / `turn_on_layer(name)` | Hide/show a layer. |
| `set_current_layer(name)` | Set default layer for new entities. |
| `get_all_layers()` | Inspect layers and state. |
| `isolate_layer(name)` / `unisolate_layers()` | Focus on one layer and restore. |
| `load_linetype(name,filename?)` | Load CENTER, HIDDEN, DASHDOT, etc. |
| `get_linetypes()` | List loaded linetypes. |

## 7. Text, Leaders, Tables, and Dimensions

Do not fake annotations. Use true annotation entities.

| Tool | Use when |
|---|---|
| `create_text_style(name,font?,height?,width?)` | Create text style. Check local fonts first for Chinese output. |
| `set_current_text_style(name)` / `get_text_styles()` | Select or inspect text styles. |
| `draw_text(...)` | Single-line label. |
| `draw_mtext(...)` | Multi-line note, room label, specification note. |
| `set_text_alignment(...)` | Text justification. |
| `set_text_properties(...)` | Oblique angle, width factor, style. |
| `add_leader(points,annotation?)` | Classic leader. |
| `add_mleader(text,points,...)` | Preferred callout/multileader. |
| `add_table(...)` | Door/window schedule, BOM, equipment list, drawing index. |
| `edit_table_cell(table_handle,row,col,text)` | Fill or update a table cell. |
| `find_text(pattern,highlight_color?)` | Locate text entities. |
| `replace_text(find,replace)` | Bulk text replacement. |
| `add_linear_dimension(...)` | Aligned linear distance. |
| `add_rotated_dimension(...)` | Distance along a specified rotation. |
| `add_angular_dimension(...)` / `add_3point_angular_dimension(...)` | Angle dimensions. |
| `add_radial_dimension(...)` / `add_diametric_dimension(...)` | Radius/diameter. |
| `add_arc_dimension(...)` | Arc length. |
| `add_ordinate_dimension(...)` | X/Y coordinate callout. |
| `add_qdim(entity_handles,dimension_type?,...)` | Fast batch dimensions. |
| `add_baseline_dimension(...)` / `add_continue_dimension(...)` | Dimension chains from a prior dimension. |
| `get_dimension_styles()` / `copy_dimension_style(...)` / `set_current_dimension_style(...)` | Dimension style management. |
| `set_dimension_text_override(handle,text)` | Override text only when justified. |
| `get_dimension_measurement(handle)` | Read true measured value. |
| `draw_wipeout(...)` | Mask background for readable annotation. |

## 8. Blocks, Attributes, Xrefs, and Reuse

Repeated components should become blocks or arrays.

| Tool | Use when |
|---|---|
| `create_block(name,base_x,base_y,base_z,entity_handles)` | Define a reusable block. |
| `insert_block(name,x,y,...)` | Insert an existing block. |
| `insert_minert_block(...)` / `insert_minsert_block(...)` | Insert a rectangular block array as MInsert. Both names exist; prefer `insert_minsert_block`. |
| `get_all_blocks()` | List definitions. |
| `explode_block(handle)` | Explode a block reference only when needed. |
| `insert_block_with_attributes(...)` | Insert and set attribute values in one step. |
| `get_block_attributes(handle)` / `set_block_attribute(handle,tag,value)` | Read/update block attributes. |
| `attach_xref(filepath,...)` | Attach external DWG reference. |
| `get_xrefs()` / `unload_xref(name)` / `reload_xref(name)` | Inspect and manage external references. |

## 9. Hatches, Fills, and Materials

| Tool | Use when |
|---|---|
| `add_hatch(pattern_name?,associativity?,...)` | Create hatch/fill object. Add boundaries next. |
| `hatch_add_boundary(handle,boundary_handles)` | Add outer hatch boundary. |
| `hatch_add_inner_loop(handle,inner_handles)` | Add holes/islands. |
| `hatch_set_properties(handle,...)` | Pattern scale, angle, double, style. |
| `hatch_get_properties(handle)` | Read hatch area/properties. |
| `hatch_set_gradient(handle,...)` | Gradient fill. |
| `create_material(name,description?)` / `get_materials()` | Manage material definitions. |
| `set_entity_material(handle,material_name)` / `set_active_material(material_name)` | Assign material to solids/entities. |

## 10. Query, Selection, Topology, and Database

For existing drawings, this is the AI's main sightline. Scan first, query second, edit third.

| Tool | Use when |
|---|---|
| `scan_all_entities(clear_db?,max_entities?)` | Snapshot drawing entities into SQLite. |
| `scan_entities_in_area(x_min,y_min,x_max,y_max)` | Scan a bounded area. |
| `get_entity_statistics()` | Counts by type/layer. |
| `get_all_tables()` / `get_table_schema(table_name)` | Inspect metadata DB structure. |
| `execute_query(query)` / `execute_sql_query(query)` | Read-only SQL over scanned CAD metadata. |
| `get_entity_topology(handle)` | Derived point/line/surface topology for one entity. |
| `get_topology_summary(limit?)` | Compact topology summary for scanned entities. |
| `select_by_window(...)` / `select_by_crossing(...)` | Window/crossing selection. |
| `select_all()` | Select all entities. Be careful before destructive commands. |
| `select_by_fence(points)` | Fence selection. |
| `select_by_wpolygon(points)` / `select_by_cpolygon(points)` | Polygon window/crossing selection. |
| `select_at_point(x,y,z?)` | Select through a point. |
| `select_on_screen()` | User-interactive selection. |
| `highlight_entity(handle,color?)` / `highlight_entities(handles,color?)` | Visual review of selected handles. |
| `highlight_query_results(sql_query,color?)` | Query and highlight matching handles. |
| `reset_entity_color(handle,original_color?)` | Restore color after highlight. |
| `create_group(name,handles)` / `get_all_groups()` | Named entity groups. |
| `delete_selection_set(ss_name?)` | Delete a selection-set object, not necessarily the entities. |
| `erase_selection_entities(ss_name?)` | Erase entities in a selection set. Destructive. |
| `clear_selection_set(ss_name?)` | Clear membership without erasing drawing entities. |

## 11. Views, Layouts, Viewports, and Plotting

| Tool | Use when |
|---|---|
| `zoom_extents()` / `zoom_all()` | Show full drawing or drawing limits. |
| `zoom_window(...)` / `zoom_center(...)` / `zoom_scale(...)` / `zoom_previous()` | View navigation. |
| `pan(x_offset,y_offset)` | Pan view. |
| `get_current_view()` | Inspect active view. |
| `get_layouts()` / `set_active_layout(name)` / `create_layout(name)` | Model/layout management. |
| `add_viewport(center_x,center_y,width,height,...)` | Create paper-space viewport. |
| `get_viewports()` / `set_viewport_properties(handle,...)` | Inspect/lock/scale viewports. |
| `save_named_view(name)` / `restore_named_view(name)` | Save or restore a view. |
| `get_named_views()` / `delete_named_view(name)` | Manage named views. |
| `export_pdf(filepath)` / `export_dxf(filepath)` / `export_dwf(filepath)` / `export_image(filepath)` | Export deliverables. |
| `plot_to_device(plot_config?)` | Plot current layout to printer/plotter. |
| `plot_to_file(filepath,plot_config?)` | Plot current layout to file. |
| `plot_preview(preview_type?)` | Show AutoCAD plot preview. |
| `get_plot_devices()` / `get_plot_style_tables()` / `get_plot_configurations()` | Inspect plot setup options. |

## 12. 3D Solids, Surfaces, and Transformations

Use solids and surfaces for 3D. Do not draw wireframe cages unless the requested output is a wireframe.

| Tool | Use when |
|---|---|
| `draw_box(...)` / `draw_wedge(...)` | Box or wedge solid. |
| `draw_cone(...)` / `draw_elliptical_cone(...)` | Cone solids. |
| `draw_cylinder(...)` / `draw_elliptical_cylinder(...)` | Cylinder solids. |
| `draw_sphere(...)` / `draw_torus(...)` | Sphere or torus. |
| `add_region(entity_handles,...)` | Convert closed curves to region for 3D operations. |
| `extrude_region(...)` / `extrude_region_along_path(...)` | Extrude/sweep a region. |
| `revolve_region(...)` | Revolve region around an axis. |
| `solid_boolean(target_handle,tool_handle,operation?)` | Union, subtract, intersect. |
| `check_interference(handle1,handle2,create_solid?)` | Clash/interference check. |
| `slice_solid(...)` / `section_solid(...)` | Slice or create a section region. |
| `draw_3d_mesh(...)` / `draw_polyface_mesh(...)` / `draw_3d_face(...)` | Mesh/surface entities. |
| `rotate_3d(...)` / `mirror_3d(...)` | 3D transforms. |
| `transform_entity(handle,matrix)` | 4x4 matrix transform. |
| `get_bounding_box(handle)` | Axis-aligned extents. |
| `intersect_with(handle1,handle2,extend_option?)` | Curve/entity intersection points. |

## 13. Coordinate and Measurement Utilities

| Tool | Use when |
|---|---|
| `measure_distance(x1,y1,x2,y2,...)` | Distance and angle between points. |
| `polar_point(x,y,z,angle_deg,distance)` | Compute point by angle and distance. |
| `angle_from_xaxis(x1,y1,x2,y2,...)` | Angle from X axis. |
| `translate_coordinates(x,y,z,from_cs?,to_cs?)` | WCS/UCS/DCS/PSDCS/OCS conversion. |
| `angle_to_real(angle_str,unit?)` / `angle_to_string(angle_rad,unit?,precision?)` | Parse/format angles. |
| `distance_to_real(dist_str,unit?)` / `real_to_string(value,unit?,precision?)` | Parse/format distances. |
| `create_ucs(...)` / `get_all_ucs()` / `set_active_ucs(name)` / `get_active_ucs()` | User coordinate systems. |

## 14. Metadata, Links, and Extended Data

| Tool | Use when |
|---|---|
| `add_hyperlink(handle,url,description?,named_location?)` | Attach link to entity. |
| `get_hyperlinks(handle)` / `remove_hyperlink(handle,index?)` | Inspect/remove links. |
| `create_registered_application(name)` / `get_registered_applications()` | Register/list XData app names. |
| `set_xdata(handle,app_name,data_pairs)` / `get_xdata(handle,app_name?)` | Store/read structured entity metadata. |
| `get_dictionaries()` | Inspect named object dictionaries. |
| `get_extension_dictionary(handle)` | Inspect one entity extension dictionary. |
