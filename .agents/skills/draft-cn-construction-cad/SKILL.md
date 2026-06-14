---
name: draft-cn-construction-cad
description: Guide CAD agents using best-cad-mcp to draft, revise, annotate, check, and export standard, constructible mainland China construction CAD drawings. Use for architectural, structural, MEP, fire-protection, civil/site, interior, shop, and construction-detail DWG/DXF work that must use Chinese layer names, local available fonts, China drawing conventions, code-aware annotations, correct model/paper space, dimensions, hatches, blocks, and purpose-built CAD tools instead of primitive linework.
---

# Mainland China Construction CAD Drafting

Use this skill to produce CAD drawings that can survive a real design review: organized,
measurable, legible, and buildable. Treat every output as a construction document, not a
decorative sketch.

## Core Rules

1. Use `cad-operations` together with this skill whenever operating AutoCAD through this repo. If tool choice is unclear, read `../cad-operations/references/TOOL-MAP.md`; use the named CAD tool for the intent.
2. Read `references/cn-drafting-baseline.md` and `references/cn-layer-font-style.md` before creating a new construction drawing.
3. Use Chinese layer names by default. Keep entity color, linetype, and lineweight `bylayer` unless a project standard requires otherwise.
4. Prefer fonts already present in the drawing or local Windows font directory. Do not reference a missing font just because it is common in examples.
5. Draw model geometry 1:1 in millimeters. Put plotting scale, title block, viewports, and sheet composition in layout/paper space when available.
6. Never fake advanced CAD content with low-level primitives: dimensions are dimension entities, hatches are hatch entities, repeated components are blocks/arrays, walls are multilines or purpose-built wall geometry, rectangles are rectangles.
7. If a required construction decision is missing, ask a concise question or mark it explicitly as "待确认"; do not invent safety-critical sizes, fire ratings, structural capacity, or code compliance.

## Workflow

1. Confirm the drawing type, discipline, phase, scale, sheet size, project location, and governing project standard. If not specified, make conservative assumptions and state them.
2. Create or open the drawing. For existing files, run `scan_all_entities` and `get_entity_statistics` before editing.
3. Establish units, text style, dimension style, title block/layout, and the Chinese layer set before drawing content.
4. Draft using the highest-level applicable tool. For construction-specific routing, read `references/tool-selection-rules.md`.
5. Dimension, annotate, hatch, tag, and schedule using CAD annotation/table tools. Keep all annotations legible at plotted size.
6. Run `references/constructibility-checklist.md` before final save/export. Fix layer, scale, closed-boundary, dimension, and reference issues before delivering.
7. Save the DWG and export PDF/DXF only after `zoom_extents`, layer statistics, and visual/layout checks pass.

## Reference Routing

- `references/cn-drafting-baseline.md`: read for standards posture, units, sheet sizes, scales, lineweights, plotting, and drawing content expectations.
- `references/cn-layer-font-style.md`: read before creating layers, text styles, dimension styles, title blocks, or annotation.
- `references/tool-selection-rules.md`: read when deciding which best-cad-mcp tool to use for walls, grids, doors/windows, MEP routes, hatches, dimensions, blocks, and repeated content.
- `references/constructibility-checklist.md`: read before declaring a drawing ready for construction review, PDF export, or handoff.

## Handoff Standard

At the end, report the assumptions, standards checked, sheet/model scale, layer set used,
font/text style chosen, and any "待确认" items. Do not claim full statutory compliance unless
the project standards, local审图 requirements, and professional design inputs were actually
provided and checked.
