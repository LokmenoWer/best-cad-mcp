import json
import math
from pathlib import Path

import pytest

from src.cad_database import CADDatabase
from src.cad_understanding import view_grounding
from src.cad_understanding.semantic_graph import detect_semantic_objects
from src.cad_understanding.view_grounding import (
    apply_matrix_2d,
    export_view_image_with_mapping,
    ground_vlm_region,
)


def make_db(tmp_path: Path) -> CADDatabase:
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="conv",
        thread_id="thread",
        drawing_name="adversarial.dwg",
        drawing_path=str(tmp_path / "adversarial.dwg"),
    )
    return db


def insert_line(
    db: CADDatabase,
    handle: str,
    start,
    end,
    *,
    layer: str = "OUTLINE",
) -> None:
    db.upsert_entity(
        handle,
        "Line",
        "AcDbLine",
        layer=layer,
        geometry={"start": [*start, 0.0], "end": [*end, 0.0]},
        bbox=(
            min(start[0], end[0]),
            min(start[1], end[1]),
            max(start[0], end[0]),
            max(start[1], end[1]),
        ),
        topology_detail="full",
    )


def pixel_query(snapshot, world_x: float, world_y: float, half_size: float = 3.0):
    pixel = apply_matrix_2d(snapshot["world_to_pixel"], world_x, world_y)
    return [
        pixel[0] - half_size,
        pixel[1] - half_size,
        pixel[0] + half_size,
        pixel[1] + half_size,
    ]


def test_circle_bbox_corner_is_rejected_by_analytic_path_support(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    db.upsert_entity(
        "CIRCLE",
        "Circle",
        "AcDbCircle",
        layer="HOLES",
        geometry={"center": [0.0, 0.0, 0.0], "radius": 10.0},
        bbox=(-10.0, -10.0, 10.0, 10.0),
        topology_detail="full",
    )
    snapshot = export_view_image_with_mapping(
        filepath=str(tmp_path / "circle.wmf"),
        include_overlay=True,
        database=db,
    )["data"]["snapshot"]
    circle_item = next(
        item for item in snapshot["overlay_items"]
        if item.get("handle") == "CIRCLE"
    )
    assert len(circle_item["pixel_path"]) >= 48

    x1, y1, _, _ = circle_item["pixel_bbox"]
    corner_query = [x1 + 2.0, y1 + 2.0, x1 + 10.0, y1 + 10.0]
    direct = view_grounding._candidate_from_overlay_item(
        db, circle_item, corner_query, snapshot
    )
    grounded = ground_vlm_region(
        snapshot["snapshot_id"], corner_query, database=db
    )

    assert direct["support_mode"] == "path"
    assert direct["evidence"]["spatial_support"]["path_distance_px"] > 20.0
    assert direct["score"] < 0.1
    assert grounded["ok"], grounded
    assert "CIRCLE" not in {
        candidate["handle"] for candidate in grounded["data"]["candidates"]
    }
    assert grounded["data"]["recommended_candidate"] is None

    primitive_snapshot = export_view_image_with_mapping(
        filepath=str(tmp_path / "circle-primitive.wmf"),
        include_overlay=True,
        overlay_granularity="primitive",
        database=db,
    )["data"]["snapshot"]
    primitive_circle = next(
        item for item in primitive_snapshot["overlay_items"]
        if item.get("role") == "circle"
    )
    assert len(primitive_circle["pixel_path"]) >= 48
    px1, py1, _, _ = primitive_circle["pixel_bbox"]
    primitive_corner = [px1 + 2.0, py1 + 2.0, px1 + 10.0, py1 + 10.0]
    primitive_grounded = ground_vlm_region(
        primitive_snapshot["snapshot_id"], primitive_corner, database=db
    )
    assert "CIRCLE" not in {
        candidate["handle"]
        for candidate in primitive_grounded["data"]["candidates"]
    }
    assert primitive_grounded["data"]["recommended_candidate"] is None


def test_arc_and_ellipse_primitives_use_sampled_extents_not_center_bboxes(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    db.upsert_entity(
        "ARC",
        "Arc",
        "AcDbArc",
        layer="OUTLINE",
        geometry={
            "center": [0.0, 0.0, 0.0],
            "radius": 10.0,
            "start": [10.0, 0.0, 0.0],
            "end": [0.0, 10.0, 0.0],
            "start_parameter": 0.0,
            "end_parameter": math.pi / 2.0,
            "parameter_unit": "radian",
            "normal": [0.0, 0.0, 1.0],
        },
        bbox=(0.0, 0.0, 10.0, 10.0),
        topology_detail="full",
    )
    db.upsert_entity(
        "ELLIPSE",
        "Ellipse",
        "AcDbEllipse",
        layer="OUTLINE",
        geometry={
            "center": [30.0, 0.0, 0.0],
            "major_axis": [10.0, 0.0, 0.0],
            "minor_axis": [0.0, 5.0, 0.0],
            "radius_ratio": 0.5,
            "is_arc": False,
            "normal": [0.0, 0.0, 1.0],
        },
        bbox=(20.0, -5.0, 40.0, 5.0),
        topology_detail="full",
    )
    snapshot = export_view_image_with_mapping(
        filepath=str(tmp_path / "analytic-primitives.wmf"),
        include_overlay=True,
        overlay_granularity="primitive",
        database=db,
    )["data"]["snapshot"]
    arc = next(item for item in snapshot["overlay_items"] if item.get("role") == "arc")
    ellipse = next(
        item for item in snapshot["overlay_items"] if item.get("role") == "ellipse"
    )

    assert len(arc["world_path"]) > 8
    assert arc["world_bbox"]["min"][0] >= -1e-8
    assert arc["world_bbox"]["min"][1] >= -1e-8
    assert len(ellipse["world_path"]) >= 48
    assert ellipse["world_bbox"]["width"] > 19.9
    assert ellipse["world_bbox"]["height"] > 9.9
    x1, y1, _, _ = ellipse["pixel_bbox"]
    empty_corner = [x1 + 2.0, y1 + 2.0, x1 + 8.0, y1 + 8.0]
    grounded = ground_vlm_region(
        snapshot["snapshot_id"], empty_corner, database=db
    )
    assert "ELLIPSE" not in {
        candidate["handle"] for candidate in grounded["data"]["candidates"]
    }


def test_legacy_concave_snapshot_refreshes_polygon_before_notch_grounding(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    vertices = [
        (0.0, 0.0),
        (10.0, 0.0),
        (10.0, 4.0),
        (4.0, 4.0),
        (4.0, 10.0),
        (0.0, 10.0),
        (0.0, 0.0),
    ]
    handles = []
    for index, (start, end) in enumerate(zip(vertices, vertices[1:])):
        handle = f"L{index}"
        handles.append(handle)
        insert_line(db, handle, start, end)
    assert detect_semantic_objects("mechanical", database=db)["ok"]
    fresh = export_view_image_with_mapping(
        filepath=str(tmp_path / "legacy-concave.wmf"),
        include_overlay=True,
        overlay_granularity="semantic",
        database=db,
    )["data"]["snapshot"]
    shape = next(
        item for item in fresh["semantic_overlay_items"]
        if set(item.get("handles", [])) == set(handles)
    )
    object_id = shape["object_id"]
    assert len(shape["pixel_polygon"]) >= 6

    legacy = json.loads(json.dumps(fresh))
    for collection_name in ("overlay_items", "semantic_overlay_items"):
        for item in legacy.get(collection_name, []):
            if item.get("object_id") != object_id:
                continue
            for key in (
                "pixel_polygon",
                "world_polygon",
                "pixel_path",
                "world_path",
            ):
                item.pop(key, None)
    view_grounding._store_snapshot(db, legacy)

    notch = pixel_query(legacy, 8.0, 8.0)
    inside_leg = pixel_query(legacy, 2.0, 8.0)
    notch_result = ground_vlm_region(
        legacy["snapshot_id"], notch, database=db
    )
    leg_result = ground_vlm_region(
        legacy["snapshot_id"], inside_leg, database=db
    )

    assert notch_result["ok"], notch_result
    assert all(
        candidate.get("object_id") != object_id
        for candidate in notch_result["data"]["shape_candidates"]
    )
    assert notch_result["data"]["recommended_candidate"] is None
    refreshed = next(
        candidate for candidate in leg_result["data"]["shape_candidates"]
        if candidate.get("object_id") == object_id
    )
    assert refreshed["geometry_refreshed_from_current_graph"] is True
    assert refreshed["polygon_support"]["center_inside"] is True
    assert set(refreshed["handles"]) == set(handles)


def test_adaptive_overlay_keeps_uncovered_entity_handles(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    profile_edges = {
        "P0": ((0.0, 0.0), (20.0, 0.0)),
        "P1": ((20.0, 0.0), (20.0, 10.0)),
        "P2": ((20.0, 10.0), (0.0, 10.0)),
        "P3": ((0.0, 10.0), (0.0, 0.0)),
    }
    for handle, (start, end) in profile_edges.items():
        insert_line(db, handle, start, end)
    for index in range(125):
        y = 30.0 + index * 2.0
        insert_line(db, f"U{index:03d}", (100.0, y), (101.0, y))
    assert detect_semantic_objects("mechanical", database=db)["ok"]

    snapshot = export_view_image_with_mapping(
        filepath=str(tmp_path / "adaptive.wmf"),
        include_overlay=True,
        overlay_granularity="adaptive",
        database=db,
    )["data"]["snapshot"]
    group = next(
        item for item in snapshot["overlay_items"]
        if item.get("item_kind") == "semantic"
        and set(item.get("handles", [])) == set(profile_edges)
    )
    represented_handles = set()
    for item in snapshot["overlay_items"]:
        represented_handles.update(
            str(handle) for handle in item.get("handles", []) if handle
        )
        if item.get("handle"):
            represented_handles.add(str(item["handle"]))

    assert group
    assert len(snapshot["entity_overlay_items"]) == len(
        snapshot["visible_handles"]
    )
    assert len(snapshot["visible_handles"]) > 120
    assert represented_handles == set(snapshot["visible_handles"])
    assert not any(
        item.get("item_kind") == "entity"
        and item.get("handle") in profile_edges
        for item in snapshot["overlay_items"]
    )
    assert {
        item.get("handle") for item in snapshot["overlay_items"]
        if item.get("item_kind") == "entity"
    } == {f"U{index:03d}" for index in range(125)}


def test_extreme_and_malformed_overlay_geometry_is_fail_closed(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    insert_line(db, "BASE", (0.0, 0.0), (100.0, 0.0))
    snapshot = export_view_image_with_mapping(
        filepath=str(tmp_path / "hostile-sidecar.wmf"),
        include_overlay=True,
        database=db,
    )["data"]["snapshot"]
    hostile = json.loads(json.dumps(snapshot))
    hostile["overlay_items"].extend([
        {
            "overlay_id": "E998",
            "item_kind": "entity",
            "handle": "HUGE",
            "entity_type": "Line",
            "pixel_bbox": [9.0e307, 9.0e307, 9.1e307, 9.1e307],
            "pixel_path": [
                [-1.0e308, -1.0e308],
                [1.0e308, 1.0e308],
            ],
            "confidence": 1.0,
        },
        {
            "overlay_id": "E999",
            "item_kind": "entity",
            "handle": "MALFORMED",
            "entity_type": "Line",
            "pixel_bbox": ["bad", None, float("inf"), float("nan")],
            "pixel_path": [
                ["bad", 0.0],
                [None, 1.0],
                [float("inf"), 2.0],
                [float("nan"), 3.0],
            ],
            "confidence": 1.0,
        },
    ])
    view_grounding._store_snapshot(db, hostile)
    width = float(hostile["image"]["width"])
    height = float(hostile["image"]["height"])
    query = [
        max(1.0, width * 0.05),
        max(1.0, height * 0.05),
        max(2.0, width * 0.05 + 8.0),
        max(2.0, height * 0.05 + 8.0),
    ]

    result = ground_vlm_region(
        hostile["snapshot_id"], query, database=db
    )

    assert result["ok"], result
    candidate_handles = {
        candidate.get("handle") for candidate in result["data"]["candidates"]
    }
    assert "HUGE" not in candidate_handles
    assert "MALFORMED" not in candidate_handles
    assert result["data"]["recommended_candidate"] is None


def test_png_path_overlay_and_tiles_ignore_diagonal_aabb_only_overlap(
    tmp_path, monkeypatch
):
    image_module = pytest.importorskip("PIL.Image")
    monkeypatch.chdir(tmp_path)
    clean_path = tmp_path / "diagonal.png"
    image_module.new("RGB", (512, 512), "white").save(clean_path)
    db = make_db(tmp_path)
    insert_line(db, "DIAGONAL", (0.0, 0.0), (100.0, 100.0))

    snapshot = export_view_image_with_mapping(
        filepath=str(clean_path),
        include_overlay=True,
        overlay_style="som",
        include_tiles=True,
        tile_size=128,
        tile_overlap=0.0,
        database=db,
    )["data"]["snapshot"]
    item = next(
        overlay for overlay in snapshot["overlay_items"]
        if overlay.get("handle") == "DIAGONAL"
    )
    overlay_path = Path(snapshot["overlay_image_path"])
    assert snapshot["overlay_vlm_ready"] is True
    assert overlay_path.suffix.lower() == ".png"
    assert overlay_path.exists()
    assert len(item["pixel_path"]) == 2

    x1, y1, x2, y2 = item["pixel_bbox"]
    anchor = view_grounding._overlay_anchor(item, item["pixel_bbox"])
    with image_module.open(overlay_path) as overlay_image:
        overlay = overlay_image.convert("RGB")
        anchor_pixel = overlay.getpixel((round(anchor[0]), round(anchor[1])))
        empty_bbox_edge = overlay.getpixel((round((x1 + x2) / 2.0), round(y1)))
    assert anchor_pixel != (255, 255, 255)
    assert empty_bbox_edge == (255, 255, 255)

    def aabb_overlaps(tile_bbox):
        tx1, ty1, tx2, ty2 = tile_bbox
        return not (x2 < tx1 or tx2 < x1 or y2 < ty1 or ty2 < y1)

    off_stroke_tiles = [
        tile for tile in snapshot["tiles"]
        if aabb_overlaps(tile["pixel_bbox"])
        and item["overlay_id"] not in tile["overlay_ids"]
    ]
    assert off_stroke_tiles, (
        "expected at least one tile whose bbox overlaps the diagonal AABB "
        "without intersecting its authored stroke"
    )
    assert all(
        view_grounding._overlay_item_intersects_region(
            item, tile["pixel_bbox"]
        ) is False
        for tile in off_stroke_tiles
    )


def test_som_anchor_is_on_path_or_inside_concave_polygon():
    path_item = {
        "item_kind": "entity",
        "pixel_bbox": [0.0, 0.0, 100.0, 100.0],
        "pixel_path": [[0.0, 100.0], [100.0, 0.0]],
    }
    path_anchor = view_grounding._overlay_anchor(
        path_item, path_item["pixel_bbox"]
    )
    assert view_grounding._point_to_segment_distance(
        path_anchor,
        path_item["pixel_path"][0],
        path_item["pixel_path"][1],
    ) <= 1e-9

    polygon = [
        [0.0, 0.0],
        [10.0, 0.0],
        [10.0, 4.0],
        [4.0, 4.0],
        [4.0, 10.0],
        [0.0, 10.0],
    ]
    polygon_item = {
        "item_kind": "semantic",
        "pixel_bbox": [0.0, 0.0, 10.0, 10.0],
        "pixel_polygon": polygon,
    }
    polygon_anchor = view_grounding._overlay_anchor(
        polygon_item, polygon_item["pixel_bbox"]
    )
    assert view_grounding._point_in_polygon(polygon_anchor, polygon)
    assert not (polygon_anchor[0] > 4.0 and polygon_anchor[1] > 4.0)
    assert all(math.isfinite(value) for value in polygon_anchor)
