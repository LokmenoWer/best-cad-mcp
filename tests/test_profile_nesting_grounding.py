from pathlib import Path

from src.cad_database import CADDatabase
from src.cad_understanding.semantic_graph import detect_semantic_objects
from src.cad_understanding.view_grounding import (
    export_view_image_with_mapping,
    ground_vlm_region,
)


def _database(tmp_path: Path) -> CADDatabase:
    database = CADDatabase(str(tmp_path / "cad.db"))
    database.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="conversation",
        thread_id="thread",
        drawing_name="profiles.dwg",
        drawing_path=str(tmp_path / "profiles.dwg"),
    )
    return database


def _insert_loop(database: CADDatabase, prefix: str, vertices) -> set[str]:
    handles = set()
    for index, (start, end) in enumerate(zip(vertices, vertices[1:])):
        handle = f"{prefix}{index}"
        handles.add(handle)
        database.upsert_entity(
            handle,
            "Line",
            "AcDbLine",
            layer="OUTLINE",
            geometry={
                "start": [start[0], start[1], 0.0],
                "end": [end[0], end[1], 0.0],
            },
            bbox=(
                min(start[0], end[0]),
                min(start[1], end[1]),
                max(start[0], end[0]),
                max(start[1], end[1]),
            ),
            topology_detail="full",
        )
    return handles


def test_cross_entity_profiles_preserve_nesting_and_resolve_inner_extent(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    database = _database(tmp_path)
    outer_handles = _insert_loop(
        database,
        "O",
        [(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)],
    )
    inner_handles = _insert_loop(
        database,
        "I",
        [(40, 40), (60, 40), (60, 60), (40, 60), (40, 40)],
    )

    semantic = detect_semantic_objects("mechanical", database=database)
    assert semantic["ok"], semantic
    roles = {
        frozenset(item["entity_handles"]): item
        for item in semantic["data"]["semantic_objects"]
        if item["object_type"] in {"outer_profile", "inner_profile"}
    }
    assert roles[frozenset(outer_handles)]["object_type"] == "outer_profile"
    inner_role = roles[frozenset(inner_handles)]
    assert inner_role["object_type"] == "inner_profile"
    assert inner_role["properties"]["nesting_depth"] == 1
    assert inner_role["properties"]["containment_methods"] == [
        "polygon_containment"
    ]

    snapshot = export_view_image_with_mapping(
        filepath=str(tmp_path / "nested.wmf"),
        include_overlay=True,
        overlay_granularity="semantic",
        database=database,
    )["data"]["snapshot"]
    inner_shape = next(
        item
        for item in snapshot["semantic_overlay_items"]
        if item.get("object_type") == "closed_profile"
        and set(item.get("handles", [])) == inner_handles
    )
    grounded = ground_vlm_region(
        snapshot["snapshot_id"],
        inner_shape["pixel_bbox"],
        semantic_type="closed_profile",
        database=database,
    )

    assert grounded["ok"], grounded
    recommended = grounded["data"]["recommended_candidate"]
    assert set(recommended["handles"]) == inner_handles
    assert grounded["data"]["selection"]["ambiguous"] is False
    assert grounded["data"]["selection"]["score_margin"] >= 0.08
    outer_candidate = next(
        item
        for item in grounded["data"]["shape_candidates"]
        if item.get("object_type") == "closed_profile"
        and set(item.get("handles", [])) == outer_handles
    )
    assert outer_candidate["polygon_support"]["extent_iou"] < 0.1


def test_concave_notch_does_not_create_false_inner_profile(tmp_path):
    database = _database(tmp_path)
    concave_handles = _insert_loop(
        database,
        "L",
        [(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10), (0, 0)],
    )
    notch_handles = _insert_loop(
        database,
        "N",
        [(6, 6), (8, 6), (8, 8), (6, 8), (6, 6)],
    )

    semantic = detect_semantic_objects("mechanical", database=database)
    assert semantic["ok"], semantic
    roles = {
        frozenset(item["entity_handles"]): item
        for item in semantic["data"]["semantic_objects"]
        if item["object_type"] in {"outer_profile", "inner_profile"}
    }

    assert roles[frozenset(concave_handles)]["object_type"] == "outer_profile"
    assert roles[frozenset(notch_handles)]["object_type"] == "outer_profile"
    assert roles[frozenset(notch_handles)]["properties"]["nesting_depth"] == 0
