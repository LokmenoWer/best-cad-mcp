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
- Use pixel coordinates with origin at top-left, x right, y down. When pixels
  are measured from `get_trace_source_image`, copy that returned image's entire
  `source_ref_template` unchanged into the observation. It records the exact
  observed dimensions and observed-to-source/global matrices, including any
  downscaling and tile translation. Only observations measured directly in the
  original normalized global artifact may omit `source_ref`; never relabel
  observed/downscaled numbers as tile-local or global-image coordinates.

## Required Recognition Passes

1. Read the whole image once for sheet layout, views, section/detail regions,
   title block, BOM/parts list, and repeated features.
2. Select relevant tile IDs from the prepared tile index and call
   `get_trace_source_image(tile_id=...)` to inspect the real local crops. Use
   these crops for complex feature details: chamfers,
   fillets, holes, counterbores, slots, grooves, steps, centerlines, hatches,
   leaders, dimensions, and small text. Record every reviewed tile under
   top-level `inspected_tiles` so coverage is auditable.
3. Extract calibration candidates only from clearly readable real dimensions.
   If a value is uncertain, keep it out of calibration and record uncertainty.
4. Encode repeated holes or parts as `pattern` items with member IDs and the
   grid/polar relationship when visible.
5. For curved mechanical regions such as bulkheads, shells, ribs, and paired
   wall contours, identify whether the visual stroke is a line, circular arc,
   `ellipse_arc`, `paired_ellipse_arcs`, spline, or true polyline. Include fit
   evidence such as center, major axis, radius ratio, start/end angle, sampled
   points, and `fit_error_px` when available.
6. Reconcile overlapping tiles in global-image space. Do not emit the same
   feature twice merely because it appears in two overlapping crops.
7. Encode dimensions as `dimension` annotations with measurement points and
   text point when visible. Do not convert dimensions to plain text.

## JSON Shape

```json
{
  "schema_version": "ImageDrawingSpec/v1",
  "domain": "mechanical",
  "units": "mm",
  "inspected_tiles": [],
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

## Worked Example (copy this structure)

`evidence` should name concrete visible cues. `confidence` is a number in
[0, 1]. Every item needs `id`, `kind`, `confidence`, `evidence`, and either
`pixel_bbox` ([x1, y1, x2, y2]) or `pixel_geometry`. Add the exact returned
`source_ref_template` whenever coordinates came from an embedded image. The
validator checks its dimensions/transforms and rebases valid observed-image
geometry to normalized-image/global pixels.

```json
{
  "schema_version": "ImageDrawingSpec/v1",
  "domain": "mechanical",
  "units": "mm",
  "inspected_tiles": ["T004"],
  "calibration_candidates": [
    {"id": "cal_1", "value": 80, "pixel_distance": 320,
     "confidence": 0.9, "evidence": {"text": "80 mm overall width dimension"}}
  ],
  "features": [
    {"id": "hole_1", "kind": "hole", "confidence": 0.92,
     "source_ref": {"schema_version": "VisualSourceRef/v1",
                    "artifact_role": "tile", "coordinate_space": "observed_image",
                    "observed_image": {"width": 320, "height": 320},
                    "source_image": {"width": 640, "height": 640},
                    "source_coordinate_space": "tile_local",
                    "global_coordinate_space": "image_global",
                    "observed_to_source": [[2,0,0],[0,2,0],[0,0,1]],
                    "source_to_global": [[1,0,384],[0,1,256],[0,0,1]],
                    "observed_to_global": [[2,0,384],[0,2,256],[0,0,1]],
                    "image_id": "img_example", "tile_id": "T004"},
     "pixel_bbox": [120, 96, 140, 116],
     "pixel_geometry": {"center": [130, 106], "radius": 10},
     "evidence": {"visible_cues": ["closed circular stroke", "clear white interior"],
                  "text": "upper-left hole in T004"}}
  ],
  "geometry": [
    {"id": "plate", "kind": "rectangle", "confidence": 0.95,
     "pixel_bbox": [40, 40, 360, 220],
     "evidence": {"text": "outer plate outline"}}
  ],
  "annotations": [
    {"id": "dim_w", "kind": "dimension", "confidence": 0.88,
     "pixel_bbox": [40, 230, 360, 250],
     "pixel_geometry": {"measure_points": [[40, 220], [360, 220]],
                        "text_point": [200, 245]},
     "evidence": {"text": "horizontal 80 mm dimension below the plate"}}
  ],
  "relations": [],
  "tables": [],
  "component_hypotheses": [],
  "uncertainties": []
}
```

The validator accepts a partial spec: items that fail validation are reported
in `rejected_items` and dropped, while valid items still compile. Prefer
emitting a slightly-uncertain item with `evidence` and `confidence` over
omitting it, but never invent geometry.

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
