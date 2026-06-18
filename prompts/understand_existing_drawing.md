# Understand Existing Drawing

## Fidelity Contract

- Preserve uncertainty and structure. A complex drawing is not just a count of
  lines, arcs, circles, and text; treat views, dimensions, blocks, hatches,
  tables, title blocks, BOMs, section/detail views, and repeated parts as
  semantic evidence.
- Do not modify the DWG during understanding. Do not draw helper labels into
  the DWG to remember what a region means; use model-private spatial
  annotations or returned handles.
- Do not reduce a dense engineering drawing to a simplified outline unless the
  user explicitly asks for abstraction.

## Workflow

1. Call `scan_all_entities`; use `topology_detail="full"` when primitive
   grounding or dimension binding matters.
2. Call `build_drawing_ir` and read CAD-IR v2 top-level `drawing`,
   `quality`, and `manifest` first.
3. Call `detect_semantic_objects` with an appropriate domain or `generic`.
4. Call `extract_drawing_constraints`.
5. Call `bind_all_dimensions`.
6. Call `check_drawing_constraints`.
7. Call `validate_geometry`.
8. Call `summarize_drawing`.
9. For engineering drawings, assemblies, title blocks, BOMs, GD&T, surface
   finish symbols, section/detail views, or exploded views, call
   `analyze_engineering_drawing_stages`.
10. Optionally call `export_view_image_with_mapping(include_overlay=true)`;
    use `overlay_granularity="both"` and `include_tiles=true` for dense sheets.

Do not modify the DWG during understanding. Use structured handles, evidence,
confidence, constraints, validation issues, and recommended next tools for the
agent response.
Ambiguous dimensions or low-confidence semantic objects must remain uncertain.

CAD-IR v2 stores large payloads under `sections`. The default entity index is
compact; use `sections=["overview"]` or `cad://drawing/current/ir/overview` for
fast orientation, `sections=["entities"]` or
`cad://drawing/current/ir/entities` for handle lookup, and `include_raw=true`
only when decoded geometry/properties are required.
