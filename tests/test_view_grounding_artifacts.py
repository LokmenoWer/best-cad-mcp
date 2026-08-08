import json
from pathlib import Path

from src.cad_database import CADDatabase
from src.cad_understanding.view_grounding import (
    apply_matrix_2d,
    export_view_image_with_mapping,
    ground_vlm_overlay_id,
    ground_vlm_region,
)
from src.cad_understanding.semantic_graph import detect_semantic_objects
from src.cad_understanding.vlm import submit_vlm_review, validate_vlm_review_output


def make_db(tmp_path):
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="conv",
        thread_id="thread",
        drawing_name="overlay.dwg",
        drawing_path=str(tmp_path / "overlay.dwg"),
    )
    return db


def populate_fixture(db):
    db.upsert_entity(
        "P1",
        "Polyline",
        "AcDbPolyline",
        layer="OUTLINE",
        geometry={"vertices": [[0, 0, 0], [80, 0, 0], [80, 40, 0], [0, 40, 0]], "closed": True},
        bbox=(0, 0, 80, 40),
        topology_detail="full",
    )
    db.upsert_entity(
        "C1",
        "Circle",
        "AcDbCircle",
        layer="HOLES",
        geometry={"center": [20, 20, 0], "radius": 5},
        bbox=(15, 15, 25, 25),
        topology_detail="full",
    )


def test_export_includes_som_primitive_overlay_and_tile_index(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    populate_fixture(db)

    result = export_view_image_with_mapping(
        filepath=str(tmp_path / "view.wmf"),
        include_overlay=True,
        include_entity_bboxes=True,
        overlay_granularity="both",
        overlay_style="som",
        include_tiles=True,
        tile_size=512,
        database=db,
    )
    snapshot = result["data"]["snapshot"]
    primitive_items = snapshot["primitive_overlay_items"]
    primitive_id = primitive_items[0]["overlay_id"]
    grounded = ground_vlm_overlay_id(snapshot["snapshot_id"], primitive_id, database=db)

    assert result["ok"]
    assert snapshot["schema_version"] == "cad-view-snapshot/v3"
    assert snapshot["grounding_geometry_version"] == "path-polygon/v1"
    assert snapshot["overlay_style"] == "som"
    assert snapshot["overlay_granularity"] == "both"
    assert Path(snapshot["overlay_image_path"]).exists()
    assert Path(snapshot["tile_index_path"]).exists()
    overlay_sidecar = json.loads(
        Path(snapshot["overlay_items_path"]).read_text(encoding="utf-8")
    )
    tile_sidecar = json.loads(
        Path(snapshot["tile_index_path"]).read_text(encoding="utf-8")
    )
    assert overlay_sidecar["schema_version"] == "cad-overlay-items/v2"
    assert tile_sidecar["schema_version"] == "cad-view-tiles/v2"
    assert overlay_sidecar["grounding_geometry_version"] == "path-polygon/v1"
    assert tile_sidecar["grounding_geometry_version"] == "path-polygon/v1"
    assert snapshot["tiles"]
    assert primitive_items
    assert primitive_id.startswith("E")
    assert ".P" in primitive_id
    assert grounded["ok"]
    assert grounded["data"]["candidate"]["item_kind"] == "primitive"
    assert grounded["data"]["candidate"]["primitive_key"]


def test_region_grounding_uses_stroke_support_for_long_thin_line(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    db.upsert_entity(
        "TARGET",
        "Line",
        "AcDbLine",
        layer="OUTLINE",
        geometry={"start": [0, 0, 0], "end": [100, 0, 0]},
        bbox=(0, 0, 100, 0),
        topology_detail="full",
    )
    db.upsert_entity(
        "DISTRACTOR",
        "Line",
        "AcDbLine",
        layer="OUTLINE",
        geometry={"start": [1, 10, 0], "end": [2, 10, 0]},
        bbox=(1, 10, 2, 10),
        topology_detail="full",
    )
    exported = export_view_image_with_mapping(
        filepath=str(tmp_path / "thin-lines.wmf"),
        include_overlay=True,
        database=db,
    )
    snapshot = exported["data"]["snapshot"]
    endpoint = apply_matrix_2d(snapshot["world_to_pixel"], 0, 0)

    grounded = ground_vlm_region(
        snapshot["snapshot_id"],
        [endpoint[0] - 4, endpoint[1] - 4, endpoint[0] + 4, endpoint[1] + 4],
        database=db,
    )

    assert grounded["ok"], grounded
    assert grounded["data"]["candidates"][0]["handle"] == "TARGET"
    evidence = grounded["data"]["candidates"][0]["evidence"]["spatial_support"]
    assert evidence["query_coverage"] > 0
    assert evidence["stroke_padding_px"] >= 2


def test_primitive_region_score_is_spatial_but_direct_overlay_is_exact(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    db.upsert_entity(
        "L1",
        "Line",
        "AcDbLine",
        layer="OUTLINE",
        geometry={"start": [0, 0, 0], "end": [100, 0, 0]},
        bbox=(0, 0, 100, 0),
        topology_detail="full",
    )
    snapshot = export_view_image_with_mapping(
        filepath=str(tmp_path / "primitive-region.wmf"),
        include_overlay=True,
        overlay_granularity="primitive",
        database=db,
    )["data"]["snapshot"]
    primitive = next(
        item for item in snapshot["overlay_items"]
        if item.get("primitive_type") == "line"
    )
    path = primitive["pixel_path"]
    midpoint = [
        (path[0][0] + path[-1][0]) / 2.0,
        (path[0][1] + path[-1][1]) / 2.0,
    ]
    query = [
        midpoint[0] - 3.0,
        midpoint[1] - 3.0,
        midpoint[0] + 3.0,
        midpoint[1] + 3.0,
    ]

    region = ground_vlm_region(
        snapshot["snapshot_id"], query, database=db
    )
    direct = ground_vlm_overlay_id(
        snapshot["snapshot_id"], primitive["overlay_id"], database=db
    )

    region_candidate = next(
        candidate for candidate in region["data"]["candidates"]
        if candidate.get("overlay_id") == primitive["overlay_id"]
    )
    region_primitive = region_candidate["candidate_primitives"][0]
    direct_candidate = direct["data"]["candidate"]
    direct_primitive = direct_candidate["candidate_primitives"][0]
    assert region_candidate["score"] < 1.0
    assert "ranked by region" in region_primitive["evidence"]["reason"]
    assert direct_candidate["score"] == 1.0
    assert direct_primitive["score"] == 1.0
    assert "referenced" in direct_primitive["evidence"]["reason"]


def test_region_grounding_prefers_cross_entity_closed_shape_over_clutter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    edges = {
        "L1": ([0, 0, 0], [40, 0, 0]),
        "L2": ([40, 0, 0], [40, 20, 0]),
        "L3": ([40, 20, 0], [0, 20, 0]),
        "L4": ([0, 20, 0], [0, 0, 0]),
    }
    for handle, (start, end) in edges.items():
        db.upsert_entity(
            handle,
            "Line",
            "AcDbLine",
            layer="OUTLINE",
            geometry={"start": start, "end": end},
            bbox=(
                min(start[0], end[0]), min(start[1], end[1]),
                max(start[0], end[0]), max(start[1], end[1]),
            ),
            topology_detail="full",
        )
    db.upsert_entity(
        "CLUTTER",
        "Line",
        "AcDbLine",
        layer="CENTER",
        linetype="CENTER",
        geometry={"start": [-10, 10, 0], "end": [50, 10, 0]},
        bbox=(-10, 10, 50, 10),
        topology_detail="full",
    )
    semantic = detect_semantic_objects("mechanical", database=db)
    assert semantic["ok"], semantic
    exported = export_view_image_with_mapping(
        filepath=str(tmp_path / "closed-shape.wmf"),
        include_overlay=True,
        overlay_granularity="semantic",
        database=db,
    )
    snapshot = exported["data"]["snapshot"]
    semantic_item = next(
        item for item in snapshot["overlay_items"]
        if item["item_kind"] == "semantic" and set(item["handles"]) == set(edges)
    )
    overlay_grounded = ground_vlm_overlay_id(
        snapshot["snapshot_id"], semantic_item["overlay_id"], database=db
    )
    assert overlay_grounded["ok"], overlay_grounded
    assert set(overlay_grounded["handles"]) == set(edges)
    corners = [
        apply_matrix_2d(snapshot["world_to_pixel"], 0, 0),
        apply_matrix_2d(snapshot["world_to_pixel"], 40, 20),
    ]
    query = [
        min(point[0] for point in corners), min(point[1] for point in corners),
        max(point[0] for point in corners), max(point[1] for point in corners),
    ]

    grounded = ground_vlm_region(snapshot["snapshot_id"], query, database=db)

    assert grounded["ok"], grounded
    shapes = grounded["data"]["shape_candidates"]
    assert shapes
    assert shapes[0]["candidate_type"] == "semantic_shape"
    assert set(shapes[0]["handles"]) == set(edges)
    assert grounded["data"]["recommended_candidate"]["object_id"] == shapes[0]["object_id"]
    assert "CLUTTER" not in shapes[0]["handles"]


def test_semantic_and_entity_views_of_same_handle_do_not_create_ambiguity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    populate_fixture(db)
    detect_semantic_objects("mechanical", database=db)
    exported = export_view_image_with_mapping(
        filepath=str(tmp_path / "unique-circle.wmf"),
        include_overlay=True,
        overlay_granularity="both",
        database=db,
    )
    snapshot = exported["data"]["snapshot"]

    grounded = ground_vlm_region(
        snapshot["snapshot_id"],
        snapshot["entity_screen_bboxes"]["C1"],
        top_k=1,
        database=db,
    )

    assert grounded["ok"], grounded
    selection = grounded["data"]["selection"]
    assert selection["ambiguous"] is False
    assert selection["recommended_handle_group"] == ["C1"]
    assert selection["runner_up_handle_group"] != ["C1"]
    assert len(selection["decision_candidates"]) == 2


def test_concave_semantic_polygon_rejects_bbox_only_false_grounding(
    tmp_path, monkeypatch
):
    """A bbox inside an L-profile's empty notch is not evidence for the L."""
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
        db.upsert_entity(
            handle,
            "Line",
            "AcDbLine",
            layer="OUTLINE",
            geometry={"start": [*start, 0.0], "end": [*end, 0.0]},
            bbox=(
                min(start[0], end[0]), min(start[1], end[1]),
                max(start[0], end[0]), max(start[1], end[1]),
            ),
            topology_detail="full",
        )
    detect_semantic_objects("mechanical", database=db)
    exported = export_view_image_with_mapping(
        filepath=str(tmp_path / "concave.wmf"),
        include_overlay=True,
        overlay_granularity="semantic",
        include_tiles=True,
        tile_size=128,
        tile_overlap=0.0,
        database=db,
    )
    snapshot = exported["data"]["snapshot"]
    shape_item = next(
        item for item in snapshot["semantic_overlay_items"]
        if set(item["handles"]) == set(handles)
    )
    assert len(shape_item["pixel_polygon"]) == 6
    assert "<polygon" in Path(snapshot["overlay_image_path"]).read_text(
        encoding="utf-8"
    )

    def query_at(world_x, world_y):
        pixel = apply_matrix_2d(snapshot["world_to_pixel"], world_x, world_y)
        return [pixel[0] - 2.0, pixel[1] - 2.0, pixel[0] + 2.0, pixel[1] + 2.0]

    empty_notch = query_at(8.0, 8.0)
    inside_leg = query_at(2.0, 8.0)
    notch_center = [
        (empty_notch[0] + empty_notch[2]) / 2.0,
        (empty_notch[1] + empty_notch[3]) / 2.0,
    ]
    notch_tile = next(
        tile for tile in snapshot["tiles"]
        if tile["pixel_bbox"][0] <= notch_center[0] <= tile["pixel_bbox"][2]
        and tile["pixel_bbox"][1] <= notch_center[1] <= tile["pixel_bbox"][3]
    )
    assert shape_item["overlay_id"] not in notch_tile["overlay_ids"]
    empty_grounding = ground_vlm_region(
        snapshot["snapshot_id"], empty_notch, database=db
    )
    inside_grounding = ground_vlm_region(
        snapshot["snapshot_id"], inside_leg, database=db
    )

    assert empty_grounding["data"]["shape_candidates"] == []
    assert empty_grounding["data"]["recommended_candidate"] is None
    assert set(inside_grounding["data"]["shape_candidates"][0]["handles"]) == set(
        handles
    )
    polygon_support = inside_grounding["data"]["shape_candidates"][0][
        "polygon_support"
    ]
    assert polygon_support["center_inside"] is True
    assert polygon_support["query_coverage"] == 1.0

    submitted = submit_vlm_review(
        snapshot["snapshot_id"],
        {"findings": [
            {
                "finding_id": "concave-empty-notch",
                "bbox": empty_notch,
                "claimed_handles": handles,
                "issue_type": "shape_at_notch",
                "confidence": 0.9,
                "evidence": {"text": "bbox lies in the concave notch"},
            },
            {
                "finding_id": "concave-inside-leg",
                "bbox": inside_leg,
                "claimed_handles": handles,
                "issue_type": "shape_in_leg",
                "confidence": 0.9,
                "evidence": {"text": "bbox lies inside the L profile"},
            },
        ]},
        database=db,
    )

    findings = {
        item["finding_id"]: item for item in submitted["data"]["findings"]
    }
    assert findings["concave-empty-notch"]["status"] == "ambiguous"
    assert findings["concave-empty-notch"]["grounded_handles"] == []
    assert findings["concave-inside-leg"]["status"] == "grounded"
    assert set(findings["concave-inside-leg"]["grounded_handles"]) == set(handles)


def test_diagonal_aabb_does_not_false_ground_point(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    db.upsert_entity(
        "TARGET_POINT",
        "Point",
        "AcDbPoint",
        layer="MARKERS",
        geometry={"point": [50, 10, 0]},
        bbox=(50, 10, 50, 10),
        topology_detail="full",
    )
    db.upsert_entity(
        "DIAGONAL",
        "Line",
        "AcDbLine",
        layer="CENTER",
        geometry={"start": [0, 100, 0], "end": [100, 0, 0]},
        bbox=(0, 0, 100, 100),
        topology_detail="full",
    )
    exported = export_view_image_with_mapping(
        filepath=str(tmp_path / "point-vs-diagonal.wmf"),
        include_overlay=True,
        database=db,
    )
    snapshot = exported["data"]["snapshot"]
    diagonal_item = next(
        item for item in snapshot["overlay_items"]
        if item.get("handle") == "DIAGONAL"
    )
    assert len(diagonal_item["pixel_path"]) == 2
    point_pixel = apply_matrix_2d(snapshot["world_to_pixel"], 50, 10)
    query = [
        point_pixel[0] - 10, point_pixel[1] - 10,
        point_pixel[0] + 10, point_pixel[1] + 10,
    ]

    grounded = ground_vlm_region(snapshot["snapshot_id"], query, database=db)

    assert grounded["ok"], grounded
    assert grounded["data"]["recommended_candidate"]["handle"] == "TARGET_POINT"
    assert grounded["data"]["selection"]["ambiguous"] is False
    candidates = {
        item["handle"]: item for item in grounded["data"]["candidates"]
    }
    if "DIAGONAL" in candidates:
        diagonal_support = candidates["DIAGONAL"]["evidence"]["spatial_support"]
        assert diagonal_support["support_mode"] == "path"
        assert diagonal_support["path_distance_px"] > 10
        assert candidates["DIAGONAL"]["score"] < candidates["TARGET_POINT"]["score"]

    submitted = submit_vlm_review(
        snapshot["snapshot_id"],
        {"findings": [{
            "finding_id": "point-not-diagonal",
            "bbox": query,
            "issue_type": "point_marker_review",
            "confidence": 0.9,
            "evidence": {"text": "small marker around the visible point"},
        }]},
        database=db,
    )
    finding = submitted["data"]["findings"][0]
    assert finding["status"] == "grounded"
    assert finding["grounded_handles"] == ["TARGET_POINT"]


def test_closed_profile_intent_survives_diagonal_bbox_clutter_top_k_one(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    edges = {
        "L1": ([0, 0, 0], [40, 0, 0]),
        "L2": ([40, 0, 0], [40, 20, 0]),
        "L3": ([40, 20, 0], [0, 20, 0]),
        "L4": ([0, 20, 0], [0, 0, 0]),
    }
    clutter = {
        "D1": ([0, 0, 0], [40, 20, 0]),
        "D2": ([0, 20, 0], [40, 0, 0]),
    }
    for handle, (start, end) in {**edges, **clutter}.items():
        db.upsert_entity(
            handle,
            "Line",
            "AcDbLine",
            layer="OUTLINE" if handle in edges else "CENTER",
            linetype="Continuous" if handle in edges else "CENTER",
            geometry={"start": start, "end": end},
            bbox=(
                min(start[0], end[0]), min(start[1], end[1]),
                max(start[0], end[0]), max(start[1], end[1]),
            ),
            topology_detail="full",
        )
    detected = detect_semantic_objects("mechanical", database=db)
    assert detected["ok"], detected
    exported = export_view_image_with_mapping(
        filepath=str(tmp_path / "profile-with-diagonals.wmf"),
        include_overlay=True,
        overlay_granularity="both",
        database=db,
    )
    snapshot = exported["data"]["snapshot"]
    profile = next(
        item for item in snapshot["semantic_overlay_items"]
        if item.get("object_type") == "closed_profile"
        and set(item.get("handles", [])) == set(edges)
    )
    review = {"findings": [{
        "finding_id": "profile-among-diagonals",
        "bbox": profile["pixel_bbox"],
        "issue_type": "profile_review",
        "semantic_type": "outer_profile",
        "confidence": 0.94,
        "evidence": {"text": "one closed outer contour"},
    }]}

    validated = validate_vlm_review_output(
        review, snapshot_id=snapshot["snapshot_id"], database=db
    )
    grounded = ground_vlm_region(
        snapshot["snapshot_id"],
        profile["pixel_bbox"],
        top_k=1,
        database=db,
        semantic_type="outer_profile",
    )
    spatial_only = ground_vlm_region(
        snapshot["snapshot_id"],
        profile["pixel_bbox"],
        top_k=1,
        database=db,
    )
    submitted = submit_vlm_review(
        snapshot["snapshot_id"], review, top_k=1, database=db
    )

    assert validated["data"]["findings"][0]["semantic_type"] == "closed_profile"
    recommended = grounded["data"]["recommended_candidate"]
    assert recommended["object_type"] == "closed_profile"
    assert set(recommended["handles"]) == set(edges)
    assert grounded["data"]["selection"]["semantic_intent_applied"] is True
    assert grounded["data"]["selection"]["ambiguous"] is False
    spatial_recommended = spatial_only["data"]["recommended_candidate"]
    assert spatial_recommended["object_type"] == "closed_profile"
    assert set(spatial_recommended["handles"]) == set(edges)
    assert spatial_only["data"]["selection"]["ambiguous"] is False
    finding = submitted["data"]["findings"][0]
    assert finding["status"] == "grounded"
    assert set(finding["grounded_handles"]) == set(edges)
    assert finding["grounding_candidates"][0]["object_type"] == "closed_profile"
    assert finding["evidence"]["grounding_reconciliation"]["conflicts"] == []

    semantic_export = export_view_image_with_mapping(
        filepath=str(tmp_path / "profile-semantic-only.wmf"),
        include_overlay=True,
        overlay_granularity="semantic",
        database=db,
    )["data"]["snapshot"]
    semantic_profile = next(
        item for item in semantic_export["semantic_overlay_items"]
        if item.get("object_type") == "closed_profile"
        and set(item.get("handles", [])) == set(edges)
    )
    assert all(
        len(item.get("pixel_path") or []) >= 2
        for item in semantic_export["entity_overlay_items"]
        if item.get("handle") in clutter
    )
    semantic_grounded = ground_vlm_region(
        semantic_export["snapshot_id"],
        semantic_profile["pixel_bbox"],
        top_k=1,
        database=db,
    )
    assert semantic_grounded["data"]["recommended_candidate"][
        "object_type"
    ] == "closed_profile"
    assert semantic_grounded["data"]["selection"]["ambiguous"] is False
