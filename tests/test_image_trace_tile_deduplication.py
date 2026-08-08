from pathlib import Path

import pytest

from src.cad_database import CADDatabase
from src.cad_understanding.image_trace import (
    compile_image_spec_to_cad_plan,
    prepare_image_trace,
    validate_image_drawing_spec,
)


def _database(tmp_path: Path) -> CADDatabase:
    database = CADDatabase(str(tmp_path / "cad.db"))
    database.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="tile-dedup-conversation",
        thread_id="tile-dedup-thread",
        drawing_name="tile-dedup.dwg",
        drawing_path=str(tmp_path / "tile-dedup.dwg"),
    )
    return database


def _prepared_trace(tmp_path: Path, database: CADDatabase):
    image_module = pytest.importorskip("PIL.Image")
    image = image_module.new("RGB", (900, 300), "white")
    source = tmp_path / "overlapping-tiles.png"
    image.save(source)
    result = prepare_image_trace(
        str(source),
        tile_size=512,
        tile_overlap=0.2,
        database=database,
    )
    assert result["ok"], result
    tiles = result["data"]["tiles"]
    overlapping_pair = next(
        (left, right)
        for left in tiles
        for right in tiles
        if left["tile_id"] < right["tile_id"]
        and min(left["global_pixel_bbox"][2], right["global_pixel_bbox"][2])
        > max(left["global_pixel_bbox"][0], right["global_pixel_bbox"][0])
        and min(left["global_pixel_bbox"][3], right["global_pixel_bbox"][3])
        > max(left["global_pixel_bbox"][1], right["global_pixel_bbox"][1])
    )
    return result, overlapping_pair


def _source_ref(tile):
    return {
        "artifact_role": "tile",
        "tile_id": tile["tile_id"],
        "coordinate_space": "tile_local",
    }


def _local_point(tile, global_point):
    x0, y0 = tile["global_pixel_bbox"][:2]
    return [global_point[0] - x0, global_point[1] - y0]


def _hole_observation(item_id, tile, global_center, radius=8.0):
    center = _local_point(tile, global_center)
    return {
        "id": item_id,
        "kind": "hole",
        "confidence": 0.92,
        "source_ref": _source_ref(tile),
        "pixel_bbox": [
            center[0] - radius,
            center[1] - radius,
            center[0] + radius,
            center[1] + radius,
        ],
        "pixel_geometry": {"center": center, "radius": radius},
        "evidence": {"text": f"circular edge visible in {tile['tile_id']}"},
    }


def _line_observation(item_id, tile, global_start, global_end, reverse=False):
    start = _local_point(tile, global_start)
    end = _local_point(tile, global_end)
    points = [end, start] if reverse else [start, end]
    return {
        "id": item_id,
        "kind": "line",
        "confidence": 0.9,
        "source_ref": _source_ref(tile),
        "pixel_bbox": [
            min(start[0], end[0]),
            min(start[1], end[1]),
            max(start[0], end[0]),
            max(start[1], end[1]),
        ],
        "pixel_geometry": {"points": points},
        "evidence": {"text": f"continuous edge visible in {tile['tile_id']}"},
    }


def _spec(tiles, features, geometry, relations=None):
    return {
        "schema_version": "ImageDrawingSpec/v1",
        "domain": "mechanical",
        "units": "mm",
        "inspected_tiles": [tile["tile_id"] for tile in tiles],
        "calibration_candidates": [],
        "features": features,
        "geometry": geometry,
        "annotations": [],
        "relations": relations or [],
        "tables": [],
        "uncertainties": [],
    }


def test_overlapping_tile_observations_deduplicate_after_global_normalization(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    database = _database(tmp_path)
    prepared, (left, right) = _prepared_trace(tmp_path, database)
    global_center = [460.0, 150.0]
    global_start = [430.0, 80.0]
    global_end = [490.0, 110.0]
    spec = _spec(
        [left, right],
        [
            _hole_observation("hole_from_left", left, global_center),
            _hole_observation("hole_from_right", right, global_center),
        ],
        [
            _line_observation("edge_from_left", left, global_start, global_end),
            _line_observation(
                "edge_from_right", right, global_start, global_end, reverse=True
            ),
        ],
        relations=[
            {
                "type": "attached_to",
                "source": "edge_from_right",
                "target": "hole_from_right",
            }
        ],
    )

    result = validate_image_drawing_spec(
        spec,
        image_id=prepared["data"]["image_id"],
        database=database,
    )

    assert result["ok"], result
    normalized = result["data"]["spec"]
    assert [item["id"] for item in normalized["features"]] == ["hole_from_left"]
    assert [item["id"] for item in normalized["geometry"]] == ["edge_from_left"]
    hole = normalized["features"][0]
    assert hole["pixel_geometry"] == {"center": global_center, "radius": 8.0}
    assert hole["observation_ids"] == ["hole_from_left", "hole_from_right"]
    assert {
        observation["source_ref"]["tile_id"]
        for observation in hole["source_observations"]
    } == {left["tile_id"], right["tile_id"]}
    assert normalized["deduplication"]["id_aliases"] == {
        "hole_from_right": "hole_from_left",
        "edge_from_right": "edge_from_left",
    }
    assert normalized["relations"] == [
        {
            "type": "attached_to",
            "source": "edge_from_left",
            "target": "hole_from_left",
        }
    ]

    repeated = validate_image_drawing_spec(
        normalized,
        image_id=prepared["data"]["image_id"],
        database=database,
    )
    assert repeated["ok"], repeated
    assert len(repeated["data"]["spec"]["features"]) == 1
    assert len(repeated["data"]["spec"]["geometry"]) == 1
    assert repeated["data"]["spec"]["deduplication"] == normalized["deduplication"]

    compiled = compile_image_spec_to_cad_plan(
        image_id=prepared["data"]["image_id"],
        spec=normalized,
        database=database,
    )
    assert compiled["ok"], compiled
    operations = [step["op"] for step in compiled["data"]["plan"]["steps"]]
    assert operations.count("draw_circle") == 1
    assert operations.count("draw_line") == 1


def test_nearby_tile_features_remain_distinct(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    database = _database(tmp_path)
    prepared, (left, right) = _prepared_trace(tmp_path, database)
    spec = _spec(
        [left, right],
        [
            _hole_observation("hole_a", left, [460.0, 150.0]),
            _hole_observation("hole_b", right, [461.25, 150.0]),
        ],
        [],
    )

    result = validate_image_drawing_spec(
        spec,
        image_id=prepared["data"]["image_id"],
        database=database,
    )

    assert result["ok"], result
    normalized = result["data"]["spec"]
    assert [item["id"] for item in normalized["features"]] == ["hole_a", "hole_b"]
    assert "deduplication" not in normalized

    compiled = compile_image_spec_to_cad_plan(
        image_id=prepared["data"]["image_id"],
        spec=normalized,
        database=database,
    )
    assert compiled["ok"], compiled
    operations = [step["op"] for step in compiled["data"]["plan"]["steps"]]
    assert operations.count("draw_circle") == 2
