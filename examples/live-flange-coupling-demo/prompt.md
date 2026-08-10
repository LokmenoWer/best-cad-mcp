# Live AutoCAD demo prompt

Use the `precise_draw_from_spec` workflow with the active AutoCAD session.

Create a production-style A3 landscape mechanical assembly drawing titled
`BOLTED FLANGE COUPLING ASSEMBLY`. Use millimetres, draw model-space geometry
at true 1:1 size, and apply the repository's generic mechanical assembly
practice without claiming ISO, GB, ASME, or other formal drawing compliance.

Show:

- a longitudinal half-section main view, an aligned end view, and a compact
  exploded assembly schematic;
- two flanged hubs, two diameter-40 shafts, and two 12 x 8 x 70 parallel keys;
- four equally spaced M12 bolt sets on a diameter-90 bolt circle;
- flange outside diameter 120, hub outside diameter 65, flange thickness 16,
  and hub length 50;
- centerlines, true dimension entities, and differentiated section hatching;
- an eight-item BOM with item number, part number, description, quantity,
  material/specification, and remarks;
- matching item leaders outside the assembly outline;
- an A3 border, title block, revision field, units, scale, projection method,
  author/checker fields, and concise assembly notes.

Reuse suitable existing styles when present. Use arrays for repeated fasteners.
Use real dimensions, a real CAD table, real hatches, and real multileaders; do
not fake those semantics with loose text and linework.

Before editing, inspect the active document, active space, and units. Build
bounded CADPlan phases, validate each phase, and dry-run it. The user has
authorized creation of a new blank drawing and transactional modification of
that demo drawing, plus saving and exporting only to this example's documented
paths. After execution, rescan, build CAD-IR, detect mechanical semantics, bind
dimensions, extract and check constraints, validate geometry, and export a
clean view. Export a mapped overlay only when the export raster and the
AutoCAD view share a verified transform; otherwise omit it and record why.
Run at most two autonomous visual-repair rounds. Any later presentation-only
release QA requires explicit operator authorization, a separate bounded plan,
validation, and dry-run; do not represent it as part of the autonomous repair
loop. Then save the DWG and review exports.
