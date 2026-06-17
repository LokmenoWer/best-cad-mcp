# Generic Mechanical Assembly Standard Module

Use this module when no more specific project, company, customer, national, or
industry standard is available. It is a practical baseline for mechanical
assembly drawings created or checked with best-cad-mcp.

## Precedence

- User-specified project, company, customer, contract, national, or industry
  requirements override this module.
- If an explicit requirement conflicts with this module, follow the explicit
  requirement and mention the override in the final response when it affects
  drawing interpretation.
- Do not claim ASME, ISO, GB, or other formal compliance unless the applicable
  formal standard is provided or a dedicated module exists and has been applied.

## Source Baseline

- Assembly drawings should show all parts in operating position, a BOM/parts
  list, leaders/balloons or part names, and only the machining, assembly,
  installation, inspection, or interface dimensions needed for the assembly.
- Item references should be outside the drawing outline where practical. Each
  distinct part or subassembly receives one item number; identical parts share
  one item number and total quantity.
- BOM rows should include at least item number, part/reference number,
  description or name, quantity, material/specification, and remarks.
- A controlled sheet should include title block fields, revision information,
  scale, units, projection method, dates, author/checker fields, and applicable
  default tolerances.
- For Chinese-style sheets, a parts list is commonly placed above the title
  block and filled bottom-up when it shares the title-block area.

## Required Drawing Content

Include these unless the user explicitly requests a schematic-only or
concept-only sketch:

- Assembly view in operating position.
- Additional views only when needed: section, partial section, local detail,
  auxiliary, or exploded/isometric.
- BOM/parts list with item numbers matching balloons.
- Item balloons/leaders or clear part-name leaders for very small/simple
  assemblies.
- Title block with assembly name, drawing number, revision, scale, units,
  projection method, author/checker fields, dates, and default tolerances.
- Revision block or revision note when the deliverable is controlled.
- Technical requirements for assembly, fit, torque, inspection, lubrication,
  welding/bonding, transport, installation, testing, or maintenance.
- Assembly/interface/overall/installation/inspection dimensions. Do not fully
  detail every part on the assembly sheet.
- Standard/purchased item identification by standard number, catalog number, or
  procurement reference.

## View Rules

- Start with the view that best explains how the assembly works or is built.
- Prefer the fewest views that clearly show part relationships.
- Use section views for internal relationships, but simplify standard purchased
  parts when full section detail would obscure the assembly.
- In sectioned assemblies, hatch adjacent cut parts differently. Keep the same
  part's hatch style consistent across views.
- Avoid hidden lines in assembly and pictorial views unless they are necessary
  for clarity.
- Use exploded views for service, installation, or assembly sequence; keep
  parts aligned along real assembly axes.
- Show centerlines/axes for shafts, bolt circles, bearings, holes, and exploded
  alignments.

## BOM And Item Number Rules

- Every distinct part or subassembly receives one item number.
- Identical parts use the same item number; show total quantity in the BOM.
- BOM item numbers and balloons must match exactly.
- Use one row per item. Include item number, part/reference number,
  description/name, quantity, material/specification, and remarks.
- Place the BOM/parts list above the title block when possible.
- If BOM space is insufficient, create a continuation/list sheet and note the
  relationship in the title block or notes.
- Put balloons outside the part outline; align them horizontally or vertically
  for readability.
- Avoid crossing leader lines. A leader may be bent once if needed.
- For a clear group of fasteners or tightly grouped parts, one common leader may
  identify multiple item numbers if it remains unambiguous.

## Dimensioning Rules

Assembly dimensions should answer how to build, inspect, install, or interface
with the assembly:

- Overall/envelope dimensions.
- Mounting/interface dimensions.
- Fit, clearance, alignment, and motion limits.
- Critical inspection dimensions.
- Machining-after-assembly dimensions.
- Installation or service access dimensions.

Do not duplicate full part-manufacturing dimensions unless the assembly drawing
is intentionally replacing part detail drawings.

## Technical Notes Checklist

Consider adding notes for:

- Assembly sequence.
- Torque values and fastener locking.
- Fits, clearances, preload, backlash, or bearing seating.
- Lubricant, adhesive, sealant, weld, or coating requirements.
- Inspection/test requirements.
- Standard parts and purchased item references.
- Handling, shipping, storage, or maintenance.
- Unconfirmed assumptions marked `TBD` or "to confirm".

## best-cad-mcp Tool Mapping

- Plates and rectangular parts: `draw_rectangle`.
- Regular nuts/forms: `draw_polygon`.
- Washers/gaskets/rings: `draw_donut`.
- Repeated parts: `create_block`, `insert_block`, `array_rectangular`,
  `array_polar`, or `insert_minsert_block`.
- 3D forms: `draw_box`, `draw_cylinder`, `draw_torus`, `add_region`,
  `extrude_region`, `revolve_region`, and `solid_boolean`.
- Sections: `add_hatch`, `hatch_add_boundary`, `hatch_add_inner_loop`, and
  `hatch_set_properties`; avoid gradient hatches by default.
- Edits by handle: `move_entity`, `rotate_entity`, `offset_entity`,
  `mirror_entity`, `trim_entity`, `extend_entity`, `fillet_entities`, and
  `chamfer_entities`.
- Dimensions: use real dimension entities such as `add_linear_dimension`,
  `add_radial_dimension`, `add_diametric_dimension`, `add_angular_dimension`,
  and `add_qdim`; never fake dimensions with text and lines.
- BOMs: create the parts list with `add_table`, fill it with
  `edit_table_cell`, and ensure every balloon maps to one BOM row.
- Balloons/leaders: prefer `add_mleader`. If circular balloons are required,
  create one consistent circle/text/leader unit and block or group it.

## Completion Checklist

- `get_document_info`, `get_active_space_info`, and units checked.
- Existing drawings scanned with `scan_all_entities` before edits, using
  `topology_detail="summary"` by default.
- Layers, linetypes, text style, dimension style, sheet, and layout checked or
  set before final annotation.
- Component register created before balloons.
- Geometry created with high-level tools and handles captured.
- BOM created with `add_table` and filled with `edit_table_cell`.
- Balloons/leaders created with `add_mleader` or a consistent balloon
  block/callout pattern.
- Dimensions created with true dimension tools and sampled with
  `get_dimension_measurement` when important.
- Hatches created with `add_hatch` plus boundary tools.
- Repeated items created with blocks/arrays, not repeated manual copies.
- Any needed COM automation converted into a best-cad-mcp tool before use; no
  standalone drawing scripts used as an agent-side shortcut.
- `scan_all_entities`, `build_drawing_ir`, semantic detection, dimension
  binding, constraints, validation, and mapped export checks completed.
- `zoom_extents`, `audit_drawing`, `save_drawing`, and export/plot checks run
  only when explicitly appropriate for the deliverable.

## Source Notes

This module distills public engineering drawing guidance and standards-oriented
sources into operational best practices for best-cad-mcp. Sources consulted:

- https://www.asme.org/codes-standards/find-codes-standards/y14-24-types-applications-engineering-drawings
- https://dictionary.action-engineering.com/pageview/ASME%20Y14.24/null
- https://www.mcgill.ca/engineeringdesign/step-step-design-process/basics-graphics-communication/working-drawings-and-assemblies
- https://www.roymech.co.uk/Useful_Tables/Drawing/Item_Ref.html
- https://capstone.byu.edu/engineering-drawing-standards
- https://s3vi.ndc.nasa.gov/ssri-kb/static/resources/NASA%20GSFC-X-673-64-1F.pdf
- https://u.dianyuan.com/bbs/u/32/1124951601.pdf
- https://www.gstarcad.com/cmsDetail/12240/
