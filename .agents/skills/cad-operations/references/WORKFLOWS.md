# CAD Workflows

These patterns show how to combine tools without falling back to primitive-only drafting. Capture
handles from every creation step and re-scan after meaningful edits.

## 1. Existing Drawing Audit and Targeted Edit

1. `open_drawing(path)`.
2. `get_document_info()`, `get_active_space_info()`, `get_variable("INSUNITS")`.
3. `scan_all_entities(clear_db=True)`.
4. `get_entity_statistics()`.
5. Query targets:
   ```sql
   SELECT handle, type, layer
   FROM cad_entities
   WHERE layer='建筑-墙体' OR layer='WALL';
   ```
6. `highlight_query_results(sql,color=1)` so the user can review.
7. Edit by handle with `move_entity`, `set_entity_properties`, `trim_entity`, `extend_entity`, `offset_entity`, etc.
8. Re-run `scan_all_entities`, then `get_entity_statistics`.
9. `zoom_extents()`, `audit_drawing()` if appropriate, `save_drawing()`.

Wrong pattern: start editing by visual guesswork or delete-and-redraw objects that already have handles.

## 2. Mainland China Construction Plan

Use `CN-CONSTRUCTION.md` first.

1. `create_new_drawing(template?)` or `open_drawing(template.dwg)`.
2. Confirm units and scale: `get_variable("INSUNITS")`; set units when required.
3. Create Chinese layers: `建筑-轴网`, `建筑-墙体`, `建筑-门窗`, `标注-尺寸`, `标注-文字`, `填充-材料`, `图框-标题栏`.
4. Check fonts: `get_text_styles()`. If needed, choose a local Chinese font and `create_text_style("施工图-正文", font="SimSun", ...)`.
5. Axis grid: use `draw_xline` or proper axis geometry on `建筑-轴网`; labels with text tools.
6. Walls: `draw_mline` or closed `draw_polyline` plus hatch; do not hand-draw double-line walls from many segments.
7. Doors/windows: create once as blocks (`create_block("门-M0921",...)`, `create_block("窗-C1215",...)`), then `insert_block`.
8. Dimensions: exterior totals, axis dimensions, and openings use `add_linear_dimension`, `add_continue_dimension`, or `add_qdim`.
9. Notes and callouts: `add_mleader`, `draw_mtext`, and `add_table` for schedules.
10. Layout: `create_layout`, `add_viewport`, set viewport scale/lock with `set_viewport_properties`, then `export_pdf`.

Wrong pattern: draw a plan that looks plausible but lacks dimensions, layers, symbols, font checks, or "待确认" notes.

## 3. MEP or Fire-Protection Route

1. Create system layers, for example `给排水-给水`, `给排水-排水`, `暖通-风管`, `电气-照明`, `消防-喷淋`.
2. Use polylines/multilines for continuous routes: `draw_polyline`, `draw_mline`.
3. Use blocks for valves, sprinklers, detectors, lamps, receptacles, dampers, and equipment.
4. Use `insert_block_with_attributes` when symbols need tags such as system, diameter, circuit, elevation, or equipment number.
5. Repeated sprinkler/lamp grids use `array_rectangular`; radial patterns use `array_polar`.
6. Pipe/duct/equipment tags use `add_mleader` or `draw_mtext`.
7. Schedules use `add_table` plus `edit_table_cell`.
8. Validate continuity with `scan_all_entities`, SQL by layer/type, and `get_entity_topology` for route entities.

Wrong pattern: hide critical data such as pipe diameter, elevation, or circuit number only inside layer names.

## 4. Mechanical Plate With Bolt Circle

1. Layers: `create_layer("零件-轮廓",7)`, `create_layer("标注-尺寸",6)`, `create_layer("辅助-中心线",8,"CENTER")`.
2. Outer circle: `draw_circle(0,0,80, layer="零件-轮廓")`.
3. First bolt hole: `draw_circle(50,0,12, layer="零件-轮廓")`.
4. Pattern: `array_polar(hole_handle,count=6,fill_angle=360,center_x=0,center_y=0)`.
5. Center axes: `draw_xline` on centerline layer.
6. Dimensions: `add_diametric_dimension`, `add_radial_dimension`, `add_angular_dimension`.
7. Callout: `add_mleader("6x 直径24 等分", points=[...])`.

Wrong pattern: place each bolt hole with hand-computed points or write dimensions as plain text.

## 5. Hatch or Section Fill

1. Draw or identify a closed boundary: `draw_polyline(..., closed=True)`, `draw_rectangle`, `draw_circle`, or queried existing boundary.
2. `add_hatch("ANSI31", associativity=True, layer="填充-材料")`.
3. `hatch_add_boundary(hatch_handle,[boundary_handle])`.
4. Add islands with `hatch_add_inner_loop`.
5. Tune with `hatch_set_properties`.
6. Query hatch area with `hatch_get_properties`.

Wrong pattern: draw dozens of diagonal hatch lines. They will not stay associative.

## 6. 3D Bracket or Part

1. Base: `draw_box(...)`.
2. Boss/pad: `draw_cylinder(...)`, then `solid_boolean(base,boss,"union")`.
3. Hole/cut: create cutter solid, then `solid_boolean(target,cutter,"subtract")`.
4. For profile-based solids: closed 2D profile -> `add_region` -> `extrude_region` or `revolve_region`.
5. Check assembly conflicts with `check_interference`.
6. Use `section_solid` or `slice_solid` for section output.

Wrong pattern: draw a 3D object as twelve lines unless the user explicitly asked for wireframe.

## 7. Layout, Viewport, and Export

1. Confirm active space: `get_active_space_info()`.
2. Create/switch layout: `create_layout("A3-1")`, `set_active_layout("A3-1")`.
3. Add title block/frame using block insertion or rectangle/table tools.
4. Add viewport: `add_viewport(center_x,center_y,width,height, layer="视口")`.
5. Set scale and lock: `set_viewport_properties(handle, custom_scale=..., display_locked=True)`.
6. Preview plot settings: `get_plot_devices`, `get_plot_style_tables`, `get_plot_configurations`.
7. `export_pdf` or `plot_to_file`.

Wrong pattern: scale model geometry to fit a sheet.

## 8. Review Metadata and Traceability

1. `create_snapshot("before revision")`.
2. Add review tags with `create_registered_application("AI_REVIEW")` and `set_xdata`.
3. Attach references with `add_hyperlink`.
4. Use `get_xdata`, `get_hyperlinks`, and `get_file_dependencies` before handoff.

Wrong pattern: bury assumptions in chat only. Important entity-level decisions can be attached to entities.

## Final Checklist

- Drawing open/created and units checked.
- Existing drawings scanned before edits.
- Layers and styles planned before drawing.
- Shapes, hatches, dimensions, tables, blocks, arrays, and 3D solids use named tools.
- Handles captured and reused.
- SQL/topology checks used for existing or complex drawings.
- `zoom_extents`, statistics, and export/plot checks completed.
- Construction drawings list assumptions and "待确认" items.
