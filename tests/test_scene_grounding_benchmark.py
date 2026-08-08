"""Scene-level visual-grounding stress benchmark.

These cases intentionally exercise whole-scene ambiguity, rather than one
geometry primitive at a time.  A passing result must either select the exact
canonical CAD handle group or explicitly abstain when the raster evidence is
not capable of distinguishing a closed contour.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence

import pytest

from src.cad_database import CADDatabase
from src.cad_understanding import view_grounding
from src.cad_understanding.semantic_graph import detect_semantic_objects
from src.cad_understanding.view_grounding import (
    apply_matrix_2d,
    export_view_image_with_mapping,
    ground_vlm_region,
)
from src.cad_understanding.vlm import submit_vlm_review


def _make_db(tmp_path: Path, drawing_name: str) -> CADDatabase:
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="scene-benchmark",
        thread_id="grounding",
        drawing_name=drawing_name,
        drawing_path=str(tmp_path / drawing_name),
    )
    return db


def _insert_line(
    db: CADDatabase,
    handle: str,
    start: Sequence[float],
    end: Sequence[float],
    *,
    layer: str = "OUTLINE",
) -> None:
    db.upsert_entity(
        handle,
        "Line",
        "AcDbLine",
        layer=layer,
        geometry={
            "start": [float(start[0]), float(start[1]), 0.0],
            "end": [float(end[0]), float(end[1]), 0.0],
        },
        bbox=(
            min(float(start[0]), float(end[0])),
            min(float(start[1]), float(end[1])),
            max(float(start[0]), float(end[0])),
            max(float(start[1]), float(end[1])),
        ),
        topology_detail="full",
    )


def _insert_rectangle(
    db: CADDatabase,
    prefix: str,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
) -> set[str]:
    edges = {
        f"{prefix}_B": ((x1, y1), (x2, y1)),
        f"{prefix}_R": ((x2, y1), (x2, y2)),
        f"{prefix}_T": ((x2, y2), (x1, y2)),
        f"{prefix}_L": ((x1, y2), (x1, y1)),
    }
    for handle, (start, end) in edges.items():
        _insert_line(db, handle, start, end)
    return set(edges)


def _force_view(
    monkeypatch: pytest.MonkeyPatch,
    extent: Sequence[float],
) -> None:
    """Make image/world mapping deterministic without touching AutoCAD."""

    def context(_filepath=None, image_size=None):
        width, height = image_size or view_grounding.DEFAULT_IMAGE_SIZE
        view = view_grounding._view_from_extent(
            tuple(float(value) for value in extent),
            int(width),
            int(height),
        )
        return {
            "space": "model",
            "ucs": {},
            "view": view,
            "viewport": {},
            "image": {"width": int(width), "height": int(height)},
            "transform_chain": {},
            "limitations": [],
            "warnings": [],
            "confidence": 1.0,
        }

    monkeypatch.setattr(view_grounding, "get_current_view_context", context)


def _export_png_scene(
    db: CADDatabase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    extent: Sequence[float],
    *,
    image_size: tuple[int, int] = (1200, 800),
    include_tiles: bool = False,
    tile_size: int = 384,
    tile_overlap: float = 0.25,
) -> dict:
    image_module = pytest.importorskip("PIL.Image")
    clean_path = tmp_path / f"{name}.png"
    image_module.new("RGB", image_size, "white").save(clean_path)
    _force_view(monkeypatch, extent)
    result = export_view_image_with_mapping(
        filepath=str(clean_path),
        include_overlay=True,
        overlay_granularity="all",
        overlay_style="som",
        include_tiles=include_tiles,
        tile_size=tile_size,
        tile_overlap=tile_overlap,
        database=db,
    )
    assert result["ok"], result
    snapshot = result["data"]["snapshot"]
    assert snapshot["vlm_ready"] is True
    assert snapshot["overlay_vlm_ready"] is True
    assert Path(snapshot["overlay_image_path"]).suffix.lower() == ".png"
    assert Path(snapshot["overlay_items_path"]).exists()
    return snapshot


def _candidate_handles(candidate: dict | None) -> set[str]:
    if not candidate:
        return set()
    values = candidate.get("handles")
    if not isinstance(values, list) or not values:
        values = [candidate.get("handle")]
    return {str(value) for value in values if value}


def _profile_item(snapshot: dict, handles: Iterable[str]) -> dict:
    expected = {str(handle) for handle in handles}
    matches = [
        item
        for item in snapshot["semantic_overlay_items"]
        if item.get("object_type") == "closed_profile"
        and set(item.get("handles") or []) == expected
    ]
    assert len(matches) == 1, {
        "expected": sorted(expected),
        "available_profiles": [
            sorted(item.get("handles") or [])
            for item in snapshot["semantic_overlay_items"]
            if item.get("object_type") == "closed_profile"
        ],
    }
    return matches[0]


def _assert_exact_grounding(result: dict, expected_handles: Iterable[str]) -> None:
    assert result["ok"], result
    expected = {str(handle) for handle in expected_handles}
    selection = result["data"]["selection"]
    assert selection["ambiguous"] is False, selection
    assert _candidate_handles(result["data"]["recommended_candidate"]) == expected
    assert set(selection["recommended_handle_group"]) == expected


def _pixel_box_at(
    snapshot: dict,
    world_x: float,
    world_y: float,
    half_size: float = 4.0,
) -> list[float]:
    x, y = apply_matrix_2d(snapshot["world_to_pixel"], world_x, world_y)
    return [x - half_size, y - half_size, x + half_size, y + half_size]


def _submit_closed_profile_observation(
    db: CADDatabase,
    snapshot: dict,
    finding_id: str,
    bbox: Sequence[float],
) -> dict:
    submitted = submit_vlm_review(
        snapshot["snapshot_id"],
        {
            "findings": [
                {
                    "finding_id": finding_id,
                    "bbox": list(bbox),
                    "issue_type": "scene_profile_observation",
                    "semantic_type": "closed_profile",
                    "confidence": 0.95,
                    "evidence": {
                        "text": "visual observation claims one closed contour"
                    },
                }
            ]
        },
        source_model="synthetic-scene-benchmark",
        database=db,
    )
    assert submitted["ok"], submitted
    return submitted["data"]["findings"][0]


def test_repeated_congruent_profiles_use_position_specific_canonical_groups(
    tmp_path, monkeypatch
):
    db = _make_db(tmp_path, "repeated.dwg")
    left = _insert_rectangle(db, "LEFT", -60.0, -10.0, -20.0, 10.0)
    right = _insert_rectangle(db, "RIGHT", 20.0, -10.0, 60.0, 10.0)
    assert detect_semantic_objects("mechanical", database=db)["ok"]
    snapshot = _export_png_scene(
        db, tmp_path, monkeypatch, "repeated", (-80.0, -35.0, 80.0, 35.0)
    )

    for expected in (left, right):
        item = _profile_item(snapshot, expected)
        grounded = ground_vlm_region(
            snapshot["snapshot_id"],
            item["pixel_bbox"],
            semantic_type="closed_profile",
            database=db,
        )
        _assert_exact_grounding(grounded, expected)


def test_shared_and_coincident_edges_preserve_group_or_force_abstention(
    tmp_path, monkeypatch
):
    db = _make_db(tmp_path, "shared-overlap.dwg")
    segments = {
        "LEFT": ((0.0, 0.0), (0.0, 20.0)),
        "MID": ((20.0, 0.0), (20.0, 20.0)),
        "RIGHT": ((40.0, 0.0), (40.0, 20.0)),
        "B0": ((0.0, 0.0), (20.0, 0.0)),
        "B0_DUP": ((0.0, 0.0), (20.0, 0.0)),
        "B1": ((20.0, 0.0), (40.0, 0.0)),
        "T0": ((0.0, 20.0), (20.0, 20.0)),
        "T1": ((20.0, 20.0), (40.0, 20.0)),
    }
    for handle, (start, end) in segments.items():
        _insert_line(db, handle, start, end)
    left_group = {"LEFT", "MID", "B0", "B0_DUP", "T0"}
    right_group = {"MID", "RIGHT", "B1", "T1"}
    assert detect_semantic_objects("mechanical", database=db)["ok"]
    snapshot = _export_png_scene(
        db, tmp_path, monkeypatch, "shared-overlap", (-10.0, -10.0, 50.0, 30.0)
    )

    left_item = _profile_item(snapshot, left_group)
    _profile_item(snapshot, right_group)
    left_result = ground_vlm_region(
        snapshot["snapshot_id"],
        left_item["pixel_bbox"],
        semantic_type="closed_profile",
        database=db,
    )
    _assert_exact_grounding(left_result, left_group)

    shared_edge_query = _pixel_box_at(snapshot, 20.0, 10.0, half_size=3.0)
    shared_result = ground_vlm_region(
        snapshot["snapshot_id"],
        shared_edge_query,
        semantic_type="closed_profile",
        database=db,
    )
    decision_groups = {
        frozenset(_candidate_handles(candidate))
        for candidate in shared_result["data"]["selection"]["decision_candidates"]
    }
    assert shared_result["data"]["selection"]["ambiguous"] is True
    assert decision_groups == {frozenset(left_group), frozenset(right_group)}
    finding = _submit_closed_profile_observation(
        db, snapshot, "shared-edge-must-abstain", shared_edge_query
    )
    assert finding["status"] == "ambiguous"
    assert finding["grounded_handles"] == []


def test_x_crossing_abstains_while_t_junction_cells_ground_exactly(
    tmp_path, monkeypatch
):
    db = _make_db(tmp_path, "x-vs-t.dwg")
    t_segments = {
        "T_BOTTOM": ((-60.0, -10.0), (-20.0, -10.0)),
        "T_TOP": ((-60.0, 10.0), (-20.0, 10.0)),
        "T_LEFT": ((-60.0, -10.0), (-60.0, 10.0)),
        "T_MID": ((-40.0, -10.0), (-40.0, 10.0)),
        "T_RIGHT": ((-20.0, -10.0), (-20.0, 10.0)),
    }
    for handle, (start, end) in t_segments.items():
        _insert_line(db, handle, start, end)
    _insert_line(db, "X_A", (20.0, -10.0), (40.0, 10.0))
    _insert_line(db, "X_B", (20.0, 10.0), (40.0, -10.0))
    left_cell = {"T_BOTTOM", "T_TOP", "T_LEFT", "T_MID"}
    right_cell = {"T_BOTTOM", "T_TOP", "T_MID", "T_RIGHT"}

    assert detect_semantic_objects("mechanical", database=db)["ok"]
    snapshot = _export_png_scene(
        db, tmp_path, monkeypatch, "x-vs-t", (-75.0, -30.0, 55.0, 30.0)
    )
    available_groups = {
        frozenset(item.get("handles") or [])
        for item in snapshot["semantic_overlay_items"]
        if item.get("object_type") == "closed_profile"
    }
    assert available_groups == {frozenset(left_cell), frozenset(right_cell)}

    for expected in (left_cell, right_cell):
        item = _profile_item(snapshot, expected)
        result = ground_vlm_region(
            snapshot["snapshot_id"],
            item["pixel_bbox"],
            semantic_type="closed_profile",
            database=db,
        )
        _assert_exact_grounding(result, expected)

    crossing_query = _pixel_box_at(snapshot, 30.0, 0.0, half_size=4.0)
    crossing = ground_vlm_region(
        snapshot["snapshot_id"],
        crossing_query,
        semantic_type="closed_profile",
        database=db,
    )
    assert crossing["data"]["shape_candidates"] == []
    finding = _submit_closed_profile_observation(
        db, snapshot, "isolated-x-must-abstain", crossing_query
    )
    assert finding["status"] in {"ambiguous", "validated"}
    assert finding["grounded_handles"] == []


def test_partial_and_occluded_contours_fail_closed(
    tmp_path, monkeypatch
):
    db = _make_db(tmp_path, "partial.dwg")
    # Open U profile.
    _insert_line(db, "U_L", (-60.0, -10.0), (-60.0, 10.0))
    _insert_line(db, "U_B", (-60.0, -10.0), (-20.0, -10.0))
    _insert_line(db, "U_R", (-20.0, -10.0), (-20.0, 10.0))
    # Rectangle-like contour whose top edge has a visible gap.
    _insert_line(db, "G_L", (20.0, -10.0), (20.0, 10.0))
    _insert_line(db, "G_B", (20.0, -10.0), (60.0, -10.0))
    _insert_line(db, "G_R", (60.0, -10.0), (60.0, 10.0))
    _insert_line(db, "G_T0", (20.0, 10.0), (37.0, 10.0))
    _insert_line(db, "G_T1", (43.0, 10.0), (60.0, 10.0))

    assert detect_semantic_objects("mechanical", database=db)["ok"]
    snapshot = _export_png_scene(
        db, tmp_path, monkeypatch, "partial", (-75.0, -30.0, 75.0, 30.0)
    )
    assert not any(
        item.get("object_type") == "closed_profile"
        for item in snapshot["semantic_overlay_items"]
    )

    for finding_id, center in (
        ("open-u-must-abstain", (-40.0, 0.0)),
        ("gapped-contour-must-abstain", (40.0, 0.0)),
    ):
        # Cover the visually implied contour, not just one authored segment.
        center_px = apply_matrix_2d(snapshot["world_to_pixel"], *center)
        corner_a = apply_matrix_2d(
            snapshot["world_to_pixel"], center[0] - 20.0, center[1] - 10.0
        )
        corner_b = apply_matrix_2d(
            snapshot["world_to_pixel"], center[0] + 20.0, center[1] + 10.0
        )
        bbox = [
            min(corner_a[0], corner_b[0]),
            min(corner_a[1], corner_b[1]),
            max(corner_a[0], corner_b[0]),
            max(corner_a[1], corner_b[1]),
        ]
        assert bbox[0] < center_px[0] < bbox[2]
        assert bbox[1] < center_px[1] < bbox[3]
        direct = ground_vlm_region(
            snapshot["snapshot_id"],
            bbox,
            semantic_type="closed_profile",
            database=db,
        )
        assert direct["data"]["shape_candidates"] == []
        finding = _submit_closed_profile_observation(
            db, snapshot, finding_id, bbox
        )
        assert finding["status"] in {"ambiguous", "validated"}
        assert finding["grounded_handles"] == []


def test_nested_profiles_select_inner_extent_and_outer_annulus(
    tmp_path, monkeypatch
):
    db = _make_db(tmp_path, "nested.dwg")
    outer = _insert_rectangle(db, "OUTER", -50.0, -40.0, 50.0, 40.0)
    inner = _insert_rectangle(db, "INNER", -15.0, -12.0, 15.0, 12.0)
    assert detect_semantic_objects("mechanical", database=db)["ok"]
    snapshot = _export_png_scene(
        db, tmp_path, monkeypatch, "nested", (-70.0, -55.0, 70.0, 55.0)
    )
    outer_item = _profile_item(snapshot, outer)
    inner_item = _profile_item(snapshot, inner)

    inner_result = ground_vlm_region(
        snapshot["snapshot_id"],
        inner_item["pixel_bbox"],
        semantic_type="closed_profile",
        database=db,
    )
    _assert_exact_grounding(inner_result, inner)

    annulus_query = _pixel_box_at(snapshot, -35.0, 0.0, half_size=5.0)
    outer_result = ground_vlm_region(
        snapshot["snapshot_id"],
        annulus_query,
        semantic_type="closed_profile",
        database=db,
    )
    _assert_exact_grounding(outer_result, outer)
    assert _candidate_handles(outer_result["data"]["recommended_candidate"]) != set(
        inner_item["handles"]
    )
    assert outer_item["object_id"] == outer_result["data"]["recommended_candidate"][
        "object_id"
    ]


def test_multi_scale_scene_grounds_resolved_shapes_and_abstains_below_pixel_scale(
    tmp_path, monkeypatch
):
    db = _make_db(tmp_path, "multi-scale.dwg")
    large = _insert_rectangle(db, "LARGE", 0.0, 0.0, 1000.0, 600.0)
    resolved = _insert_rectangle(db, "SMALL", 1080.0, 100.0, 1100.0, 112.0)
    micro = _insert_rectangle(db, "MICRO", 1180.0, 100.0, 1180.05, 100.03)
    assert detect_semantic_objects("mechanical", database=db)["ok"]
    snapshot = _export_png_scene(
        db,
        tmp_path,
        monkeypatch,
        "multi-scale",
        (-50.0, -50.0, 1230.0, 650.0),
        image_size=(1600, 900),
    )

    for expected in (large, resolved):
        item = _profile_item(snapshot, expected)
        result = ground_vlm_region(
            snapshot["snapshot_id"],
            item["pixel_bbox"],
            semantic_type="closed_profile",
            database=db,
        )
        _assert_exact_grounding(result, expected)

    micro_item = _profile_item(snapshot, micro)
    micro_width = micro_item["pixel_bbox"][2] - micro_item["pixel_bbox"][0]
    micro_height = micro_item["pixel_bbox"][3] - micro_item["pixel_bbox"][1]
    assert max(micro_width, micro_height) < 1.0
    center_x = (micro_item["pixel_bbox"][0] + micro_item["pixel_bbox"][2]) / 2.0
    center_y = (micro_item["pixel_bbox"][1] + micro_item["pixel_bbox"][3]) / 2.0
    observation = [center_x - 2.0, center_y - 2.0, center_x + 2.0, center_y + 2.0]
    result = ground_vlm_region(
        snapshot["snapshot_id"],
        observation,
        semantic_type="closed_profile",
        database=db,
    )
    untyped_result = ground_vlm_region(
        snapshot["snapshot_id"], observation, database=db
    )
    assert result["data"]["recommended_candidate"] is None
    assert result["data"]["selection"]["recommended_handle_group"] == []
    assert untyped_result["data"]["recommended_candidate"] is None
    finding = _submit_closed_profile_observation(
        db, snapshot, "subpixel-profile-must-abstain", observation
    )
    assert finding["status"] == "validated"
    assert finding["grounded_handles"] == []


def test_overlapping_tiles_are_invariant_for_same_global_observation(
    tmp_path, monkeypatch
):
    db = _make_db(tmp_path, "tile-invariance.dwg")
    # Unique radii avoid a multi-hole pattern object competing with each hole.
    index = 0
    for y in (-30.0, 0.0, 30.0):
        for x in (-60.0, -30.0, 0.0, 30.0, 60.0):
            index += 1
            radius = 1.2 + index * 0.07
            db.upsert_entity(
                f"C{index:02d}",
                "Circle",
                "AcDbCircle",
                layer="HOLES",
                geometry={"center": [x, y, 0.0], "radius": radius},
                bbox=(x - radius, y - radius, x + radius, y + radius),
                topology_detail="full",
            )
    assert detect_semantic_objects("mechanical", database=db)["ok"]
    snapshot = _export_png_scene(
        db,
        tmp_path,
        monkeypatch,
        "tile-invariance",
        (-80.0, -50.0, 80.0, 50.0),
        image_size=(1024, 768),
        include_tiles=True,
        tile_size=384,
        tile_overlap=0.25,
    )
    overlay_sidecar = json.loads(
        Path(snapshot["overlay_items_path"]).read_text(encoding="utf-8")
    )
    tile_sidecar = json.loads(
        Path(snapshot["tile_index_path"]).read_text(encoding="utf-8")
    )
    assert overlay_sidecar["schema_version"] == "cad-overlay-items/v2"
    assert tile_sidecar["schema_version"] == "cad-view-tiles/v2"
    assert all(Path(tile["clean_tile_path"]).exists() for tile in snapshot["tiles"])

    chosen = None
    for item in snapshot["semantic_overlay_items"]:
        if item.get("object_type") != "hole" or len(item.get("handles") or []) != 1:
            continue
        bbox = item["pixel_bbox"]
        containing_tiles = [
            tile
            for tile in snapshot["tiles"]
            if tile["global_pixel_bbox"][0] <= bbox[0]
            and tile["global_pixel_bbox"][1] <= bbox[1]
            and tile["global_pixel_bbox"][2] >= bbox[2]
            and tile["global_pixel_bbox"][3] >= bbox[3]
        ]
        if len(containing_tiles) >= 2:
            chosen = (item, containing_tiles[:2])
            break
    assert chosen is not None, "fixture must place a complete hole in a tile overlap"
    item, tiles = chosen
    global_bbox = list(item["pixel_bbox"])
    findings = [
        {
            "finding_id": "tile-global",
            "bbox": global_bbox,
            "issue_type": "tile_scene_invariance",
            "confidence": 0.95,
        }
    ]
    for suffix, tile in zip(("a", "b"), tiles):
        x0, y0 = tile["global_pixel_bbox"][:2]
        findings.append(
            {
                "finding_id": f"tile-{suffix}",
                "bbox": [
                    global_bbox[0] - x0,
                    global_bbox[1] - y0,
                    global_bbox[2] - x0,
                    global_bbox[3] - y0,
                ],
                "source_ref": {
                    "artifact_role": "tile",
                    "tile_id": tile["tile_id"],
                    "coordinate_space": "tile_local",
                },
                "issue_type": "tile_scene_invariance",
                "confidence": 0.95,
            }
        )

    submitted = submit_vlm_review(
        snapshot["snapshot_id"],
        {"findings": findings},
        source_model="synthetic-scene-benchmark",
        top_k=5,
        database=db,
    )
    assert submitted["ok"], submitted
    normalized = submitted["data"]["findings"]
    expected_group = set(item["handles"])
    for finding in normalized:
        assert finding["bbox"] == pytest.approx(global_bbox)
        assert finding["status"] == "grounded"
        assert set(finding["grounded_handles"]) == expected_group
    signatures = [
        [
            (
                tuple(sorted(_candidate_handles(candidate))),
                candidate["score"],
            )
            for candidate in finding["grounding_candidates"]
        ]
        for finding in normalized
    ]
    assert signatures[0] == signatures[1] == signatures[2]
