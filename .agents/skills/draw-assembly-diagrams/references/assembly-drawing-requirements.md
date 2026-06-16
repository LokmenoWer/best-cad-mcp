# Assembly Drawing Requirements

Use this reference before drafting or checking an assembly drawing. It distills public engineering drawing guidance and standards-oriented sources into actionable best practices for best-cad-mcp.

## Source Baseline

- ASME Y14.24 is the drawing-type baseline for engineering drawings. Treat it as a classification/minimum-content reference, then apply the user's project, company, national, or customer standard first.
- McGill Engineering Design summarizes assembly drawings as showing all parts in operating position, a BOM/parts list, leaders/balloons or part names, and only machining/assembly information plus critical dimensions needed for the assembly.
- RoyMech's item reference guidance, based on ISO/BS item reference/list standards, says item references should be outside the drawing outline, each distinct item/subassembly should have a reference number, identical parts should share the same number, item numbers should be visually distinct, and the item list should include at least item, description, quantity, reference, and material.
- BYU Capstone drawing guidance is useful as a practical checklist: fill the title block, put BOM above the title block, remove hidden lines in assembly/pictorial views unless needed, choose descriptive views, and keep dimensions legible and outside views where possible.
- NASA GSFC drawing manual explains item/find numbers as locators in the drawing field and cross-references them to parts lists or procurement part numbers. Its assembly list-of-material guidance includes item number, quantity, part number, description, material, and material specification.
- Chinese technical drawing references for parts lists say assembly drawings generally include a parts list above the title block, filled bottom-up when placed on the drawing; if it does not fit, provide continuation pages. Typical columns include item number, drawing/standard code, name, quantity, material, weight, zone, and remarks.
- Chinese CAD drafting guidance for part numbers says all parts/subassemblies should be numbered, item numbers should match the parts list, identical parts normally use one number, leaders should not cross, and leader/baseline drawing should follow GB/T 4457.2 while lettering should follow GB/T 14691 when those standards are required.

## Required Drawing Content

Include these unless the user explicitly asks for a schematic-only or concept-only sketch:

- Assembly view in operating position.
- Additional views only when needed: section, partial section, local detail, auxiliary, or exploded/isometric.
- BOM/parts list with item numbers matching balloons.
- Item balloons/leaders or clear part-name leaders for very small/simple assemblies.
- Title block with assembly name, drawing number, revision, scale, units, projection method, author/checker fields, dates, and applicable default tolerances.
- Revision block or revision note when the deliverable is controlled.
- Technical requirements for assembly, fit, torque, inspection, lubrication, welding/bonding, transport, installation, testing, or maintenance.
- Assembly/interface/overall/installation/inspection dimensions. Do not fully detail every part on the assembly sheet.
- Standard/purchased item identification by standard number, catalog number, or procurement reference.

## View Selection Rules

- Start with the view that best explains how the assembly works or is built.
- Prefer the fewest views that clearly show part relationships.
- Use section views for internal relationships, but simplify standard purchased parts.
- In sectioned assemblies, hatch adjacent cut parts differently. Keep the same part's hatch style consistent across views.
- Avoid hidden lines in assembly and pictorial views unless they are necessary for clarity.
- Use exploded views for service, installation, or assembly sequence; keep parts aligned along real assembly axes.
- Show centerlines/axes for shafts, bolt circles, bearings, holes, and exploded alignments.

## BOM and Item Number Rules

- Every distinct part or subassembly receives one item number.
- Identical parts use the same item number; show total quantity in the BOM.
- BOM item numbers and balloons must match exactly.
- Use one row per item. Include item number, part/reference number, description/name, quantity, material/specification, and remarks.
- Place the BOM/parts list above the title block when possible. For Chinese-style sheets, fill bottom-up when the list is placed over the title block.
- If BOM space is insufficient, create a continuation/list sheet and note the relationship in the title block or notes.
- Put balloons outside the part outline; align them horizontally or vertically for readability.
- Avoid crossing leader lines. A leader may be bent once if needed.
- For a clear group of fasteners or tightly grouped parts, one common leader may identify multiple item numbers if it remains unambiguous.

## Dimensioning Rules

Assembly dimensions should answer how to build, inspect, install, or interface with the assembly:

- Overall/envelope dimensions.
- Mounting/interface dimensions.
- Fit, clearance, alignment, and motion limits.
- Critical inspection dimensions.
- Machining-after-assembly dimensions.
- Installation or service access dimensions.

Do not duplicate full part-manufacturing dimensions unless the assembly drawing is intentionally replacing part detail drawings.

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

## best-cad-mcp Verification Checklist

- `get_document_info`, `get_active_space_info`, and units checked.
- Existing drawings scanned with `scan_all_entities` before edits, keeping default topology summaries for agent recognition.
- Layers, linetypes, text style, dimension style, sheet, and layout set before final annotation.
- Component register created before balloons.
- Geometry created with high-level tools and handles captured.
- BOM created with `add_table` and filled with `edit_table_cell`.
- Balloons/leaders created with `add_mleader` or a consistent balloon block/callout pattern.
- Dimensions created with true dimension tools and sampled with `get_dimension_measurement` when important.
- Hatches created with `add_hatch` plus boundary tools.
- Repeated items created with blocks/arrays, not repeated manual copies.
- Any needed Python COM automation converted into a best-cad-mcp tool before use; no standalone Python COM drawing scripts used as an agent-side shortcut.
- `scan_all_entities` and `get_entity_statistics` rerun after major phases. Use `topology_detail="summary"` for normal large-drawing verification and `topology_detail="full"` only when primitive/relation topology is required.
- `zoom_extents`, `audit_drawing`, `save_drawing`, and export/plot checks completed.

## Sources Consulted

- https://www.asme.org/codes-standards/find-codes-standards/y14-24-types-applications-engineering-drawings
- https://dictionary.action-engineering.com/pageview/ASME%20Y14.24/null
- https://www.mcgill.ca/engineeringdesign/step-step-design-process/basics-graphics-communication/working-drawings-and-assemblies
- https://www.roymech.co.uk/Useful_Tables/Drawing/Item_Ref.html
- https://capstone.byu.edu/engineering-drawing-standards
- https://s3vi.ndc.nasa.gov/ssri-kb/static/resources/NASA%20GSFC-X-673-64-1F.pdf
- https://u.dianyuan.com/bbs/u/32/1124951601.pdf
- https://www.gstarcad.com/cmsDetail/12240/
