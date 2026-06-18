# Copy Drawing From Image

## Fidelity Contract

- Output only `ImageDrawingSpec/v1` JSON. Do not include prose outside JSON.
- Preserve mechanical drawing semantics and feature geometry. A chamfered square
  is not a square; a filleted rectangle is not a rectangle; a hole pattern is
  not unrelated circles.
- Do not invent unclear dimensions, hidden features, materials, tolerances, BOM
  rows, or text. Put unresolved observations in `uncertainties`.
- Every feature, geometry element, annotation, and table must include
  `confidence`, `evidence`, and either `pixel_bbox` or `pixel_geometry`.
- Use pixel coordinates from the supplied image or tile: origin at top-left,
  x right, y down.

## Required Recognition Passes

1. Read the whole image for sheet layout, views, section/detail regions,
   title block, BOM/parts list, and repeated features.
2. Inspect complex regions and tiles for local feature details: chamfers,
   fillets, holes, counterbores, slots, grooves, steps, centerlines, hatches,
   leaders, dimensions, and small text.
3. Extract calibration candidates only from clearly readable real dimensions.
   If a value is uncertain, keep it out of calibration and record uncertainty.
4. Encode repeated holes or parts as `pattern` items with member IDs and the
   grid/polar relationship when visible.
5. Encode dimensions as `dimension` annotations with measurement points and
   text point when visible. Do not convert dimensions to plain text.

## JSON Shape

```json
{
  "schema_version": "ImageDrawingSpec/v1",
  "domain": "mechanical",
  "units": "mm",
  "calibration_candidates": [],
  "features": [],
  "geometry": [],
  "annotations": [],
  "relations": [],
  "tables": [],
  "uncertainties": []
}
```

Supported `kind` values:

```text
line, circle, arc, ellipse, polyline, rectangle, chamfered_rectangle,
filleted_rectangle, hole, slot, centerline, dimension, text, leader, hatch,
table, pattern
```

## Feature Rules

- `chamfered_rectangle`: include explicit `pixel_geometry.vertices`,
  `chamfers`, or `chamfer_points`. Do not use a plain `rectangle`.
- `filleted_rectangle`: include `fillets`, `radius`/`radii`, or explicit arc
  `segments`. Do not use a plain `rectangle`.
- `slot`: include centerline/ends/radius or an explicit closed polyline/arc
  segment description.
- `hole`: include center and radius/diameter when visible. Use `pattern` for
  repeated holes.
- `hatch`: include the region bbox, pattern direction if visible, and related
  boundary IDs when known.
- `table`: include rows as nested arrays when readable; unreadable cells must
  be empty strings and recorded in `uncertainties`.

## Uncertainty Rules

Use `uncertainties` for:

- blurred, cropped, or occluded geometry;
- dimensions that are visible but unreadable;
- ambiguous chamfer/fillet sizes;
- possible hidden lines or section relationships;
- any feature that cannot be confidently compiled without user confirmation.
