# Assembly Drawing Module Index

Use this module before creating, checking, repairing, or exporting an assembly
drawing, BOM, item numbering scheme, section view, exploded view, or
standards-compliance claim.

## Select A Standard Module

Apply standards in this order:

1. Explicit project, company, customer, or contract standard from the user.
2. Explicit national or industry standard from the user.
3. A matching module under `references/assembly/standards/`.
4. `standards/generic-mechanical.md` as the default mechanical assembly module.

If the user requests a standard that is not yet represented by a module, use
the default module as a fallback, state the assumption, and avoid claiming
compliance with the missing standard.

Currently available modules:

- `standards/generic-mechanical.md`: baseline mechanical assembly drawing
  practice for BOMs, item numbers, assembly dimensions, sections, hatches,
  exploded views, and best-cad-mcp verification.

## Module Contract

Each standard module should be self-contained and include:

- Applicability and precedence.
- Required sheet/content elements.
- View, section, and exploded-view rules.
- BOM and item-numbering rules.
- Assembly dimensioning and technical note rules.
- best-cad-mcp tool mapping for geometry, annotations, BOMs, and verification.
- A completion checklist.
- Source notes or assumptions.

Keep standard-specific wording inside the module. The main `SKILL.md` should
only route to this index and describe best-cad-mcp operating rules.

## Standard Module Naming

Use lowercase kebab-case filenames:

- `generic-mechanical.md`
- `asme-y14-mechanical.md`
- `gb-mechanical.md`
- `iso-mechanical.md`
- `company-example.md`

When adding a module, add it to the available module list above and keep the
module focused on rules that change agent behavior.

## Assembly Workflow

1. Load the applicable standard module.
2. Inspect drawing units, active space, layout, title block context, and
   available styles.
3. Build or detect a component register before BOMs and balloons.
4. Create or inspect assembly views, sections, hatches, centerlines, and
   exploded alignments according to the selected module.
5. Create or check BOM rows and item balloons together so item numbers cannot
   drift.
6. Use true dimension entities for assembly/interface dimensions.
7. Run scan, CAD-IR, semantic detection, dimension binding, constraints,
   validation, and mapped export checks before reporting completion.
