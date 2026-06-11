"""CAD MCP Tools — modular tool implementations for the AutoCAD MCP server.

Tool modules:
  - drawing_tools:   2D primitives (line, circle, arc, ellipse, polyline, text, etc.)
  - edit_tools:      Entity editing (move, rotate, copy, delete, mirror, scale, array, etc.)
  - layer_tools:     Layer management (CRUD, freeze/thaw, lock/unlock, isolate)
  - text_tools:      Text styles, leaders, tables, find & replace
  - dimension_tools: All dimension types (linear, angular, radial, diametric, ordinate)
  - block_tools:     Blocks, Xrefs, block insertion
  - view_tools:      Zoom, pan, layouts
  - query_tools:     Entity scanning, selection sets, highlights, SQL queries
  - file_tools:      File I/O, export, undo/redo, system variables
  - utility_tools:   Database queries, groups, hatches, help
  - solid_tools:    3D solids, regions, meshes, boolean operations, 3D editing
  - advanced_tools:  Hyperlinks, XData, UCS, views, viewports, plot, materials,
                     linetypes, preferences, selection enhancements, etc.
"""