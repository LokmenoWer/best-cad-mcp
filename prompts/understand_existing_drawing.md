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
- Use two complementary evidence channels: structured CAD evidence for exact
  identity, geometry, dimensions, constraints, and relationships; mapped visual
  evidence for sheet layout, visibility, overlap, annotation legibility, and
  overall composition. Neither channel is a substitute for the other.

## Workflow

0. Before live CAD inspection, call
   `check_runtime_environment(check_autocad=true)`. Stop on required blockers.
1. If needed, call `open_drawing`, then inspect `get_document_info`,
   `get_active_space_info`, and drawing units. Do not infer scale or units from
   appearance alone.
2. Call `scan_all_entities(clear_db=true, detail_level="minimal",
   topology_detail="summary")`. Use `explain_entity` and local queries first.
   If primitive relations are still required for dimension binding or local
   section/detail reasoning, explicitly rescan with `topology_detail="full"`
   and keep the resulting interpretation scoped to the relevant task/handles.
3. Call `build_drawing_ir`, then `summarize_drawing`. Read CAD-IR v2 top-level
   `drawing`, `quality`, and `manifest` before loading large sections.
4. Call `analyze_drawing_intent` and `detect_semantic_objects` with an
   evidence-supported domain such as `mechanical`; otherwise use `generic`.
5. Call `bind_all_dimensions`, then `extract_drawing_constraints` and
   `check_drawing_constraints` so measured geometry and design intent are
   evaluated together.
6. Call `validate_geometry`.
7. For engineering drawings, assemblies, title blocks, BOMs, GD&T, surface
   finish symbols, section/detail views, or exploded views, call
   `analyze_engineering_drawing_stages`.
8. Acquire visual evidence when it can resolve layout or visibility:
   - Use `render_drawing_view` when the model itself needs to inspect the
     current view immediately.
   - Use `export_view_image_with_mapping(include_overlay=true)` when pixel to
     handle grounding, sidecar data, or a later VLM review is needed. Prefer
     `overlay_granularity="adaptive"`; add `include_tiles=true` for dense
     sheets.
9. Reconcile structured and visual evidence. If they conflict, do not guess:
   use `explain_entity`, inspect the relevant handles or selected full topology,
   and keep unresolved interpretations explicitly uncertain.

## Completion Contract

Return the drawing purpose, units/space, view and component structure, important
handles, bound and unbound dimensions, constraints, validation issues, visual
observations, confidence, evidence, and unresolved uncertainty. Do not claim
that the drawing is understood merely because scanning or rendering succeeded.

CAD-IR v2 stores large payloads under `sections`. The default entity index is
compact; use `sections=["overview"]` or `cad://drawing/current/ir/overview` for
fast orientation, `sections=["entities"]` or
`cad://drawing/current/ir/entities` for handle lookup, and `include_raw=true`
only when decoded geometry/properties are required.
