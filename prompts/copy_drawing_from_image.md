# Copy Drawing From Image

## Fidelity Contract

- Output only `ImageDrawingSpec/v1` JSON. Do not include prose outside JSON.
- Preserve mechanical drawing semantics and feature geometry. A chamfered square
  is not a square; a filleted rectangle is not a rectangle; a hole pattern is
  not unrelated circles.
- Preserve true curve primitives. Elliptical arcs, paired elliptical wall or
  bulkhead curves, and smooth fitted curves must not be flattened into plain
  polylines. If you are unsure, include `geometry_candidates` and record the
  ambiguity in `uncertainties`.
- Do not invent unclear dimensions, hidden features, materials, tolerances, BOM
  rows, or text. Put unresolved observations in `uncertainties`.
- Every feature, geometry element, annotation, and table must include
  `confidence`, `evidence`, and either `pixel_bbox` or `pixel_geometry`.
- Include `component_hypotheses` for open-vocabulary part recognition when the
  view provides enough evidence. Use `*_like_component` labels when the exact
  part name is ambiguous.
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
5. For curved mechanical regions such as bulkheads, shells, ribs, and paired
   wall contours, identify whether the visual stroke is a line, circular arc,
   `ellipse_arc`, `paired_ellipse_arcs`, spline, or true polyline. Include fit
   evidence such as center, major axis, radius ratio, start/end angle, sampled
   points, and `fit_error_px` when available.
6. Encode dimensions as `dimension` annotations with measurement points and
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
  "component_hypotheses": [],
  "uncertainties": []
}
```

Supported `kind` values:

```text
line, circle, arc, ellipse, polyline, rectangle, chamfered_rectangle,
ellipse_arc, paired_ellipse_arcs, filleted_rectangle, hole, slot, centerline,
dimension, text, leader, hatch, table, pattern, bulkhead
```

## Feature Rules

- `chamfered_rectangle`: include explicit `pixel_geometry.vertices`,
  `chamfers`, or `chamfer_points`. Do not use a plain `rectangle`.
- `filleted_rectangle`: include `fillets`, `radius`/`radii`, or explicit arc
  `segments`. Do not use a plain `rectangle`.
- `slot`: include centerline/ends/radius or an explicit closed polyline/arc
  segment description.
- `ellipse_arc`: include `pixel_geometry.center`, `major_axis`,
  `radius_ratio`, `start_angle`, and `end_angle`, or include enough sampled
  `points`/`vertices` for fitting. Angles are in degrees.
- `paired_ellipse_arcs`/`bulkhead`: include two curve members under
  `pixel_geometry.curves`; each member should be an `ellipse_arc` candidate
  with center, major axis, radius ratio, start/end angle, confidence, evidence,
  and optional `fit_error_px`.
- If the visible curve was initially traced as a `polyline`, include
  `primitive_hint` or `geometry_candidates` so downstream geometry arbitration
  can promote it to `ellipse_arc` or `paired_ellipse_arcs`.
- `hole`: include center and radius/diameter when visible. Use `pattern` for
  repeated holes.
- `hatch`: include the region bbox, pattern direction if visible, and related
  boundary IDs when known.
- `table`: include rows as nested arrays when readable; unreadable cells must
  be empty strings and recorded in `uncertainties`.
- `component_hypotheses`: include top-k open-vocabulary component labels with
  `id`, `label`, `confidence`, visible `evidence`, optional `pixel_bbox`,
  optional `view_type`, and `missing_evidence`. Do not force exact names when
  only a section, partial view, or cropped view is visible.

## Uncertainty Rules

Use `uncertainties` for:

- blurred, cropped, or occluded geometry;
- dimensions that are visible but unreadable;
- ambiguous chamfer/fillet sizes;
- possible hidden lines or section relationships;
- any feature that cannot be confidently compiled without user confirmation.
