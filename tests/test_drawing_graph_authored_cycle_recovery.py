import pytest

from src.cad_database import CADDatabase
from src.cad_understanding.drawing_graph import (
    infer_cross_entity_closed_profiles,
)
from src.cad_understanding.semantic_graph import detect_semantic_objects
from src.cad_understanding.view_grounding import (
    export_view_image_with_mapping,
    ground_vlm_overlay_id,
    ground_vlm_region,
)


def _make_db(tmp_path) -> CADDatabase:
    db = CADDatabase(str(tmp_path / "authored-cycle-recovery.db"))
    db.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="authored-cycle-recovery",
        thread_id="authored-cycle-recovery",
    )
    return db


def _insert_line(db: CADDatabase, handle: str, start, end) -> None:
    db.upsert_entity(
        handle,
        "Line",
        "AcDbLine",
        layer="OUTLINE",
        geometry={"start": [*start, 0.0], "end": [*end, 0.0]},
        bbox=(
            min(start[0], end[0]),
            min(start[1], end[1]),
            max(start[0], end[0]),
            max(start[1], end[1]),
        ),
        topology_detail="full",
    )


def _insert_rectangle(db: CADDatabase, prefix: str, left, bottom, right, top):
    edges = (
        ((left, bottom), (right, bottom)),
        ((right, bottom), (right, top)),
        ((right, top), (left, top)),
        ((left, top), (left, bottom)),
    )
    handles = set()
    for index, (start, end) in enumerate(edges):
        handle = f"{prefix}{index}"
        handles.add(handle)
        _insert_line(db, handle, start, end)
    return handles


def test_overlapping_authored_rectangles_keep_sources_and_atomic_faces(tmp_path):
    db = _make_db(tmp_path)
    rectangle_a = _insert_rectangle(db, "A", 0.0, 0.0, 10.0, 10.0)
    rectangle_b = _insert_rectangle(db, "B", 5.0, -5.0, 15.0, 5.0)

    profiles = infer_cross_entity_closed_profiles(db)

    # The planar arrangement contains three safe atomic faces.  The two raw
    # degree-2 endpoint cycles remain useful authored-object hypotheses.
    assert len(profiles) == 5
    recovered = [
        profile
        for profile in profiles
        if profile["topology_evidence"]["authored_cycle_recovered"]
    ]
    assert {frozenset(profile["entity_handles"]) for profile in recovered} == {
        frozenset(rectangle_a),
        frozenset(rectangle_b),
    }
    assert all(profile["area"] == pytest.approx(100.0) for profile in recovered)
    assert all(
        profile["perimeter"] == pytest.approx(40.0) for profile in recovered
    )
    assert all(profile["boundary_edge_count"] == 4 for profile in recovered)
    assert all(profile["segment_count"] == 4 for profile in recovered)
    assert all(profile["branch_node_count"] == 0 for profile in recovered)
    assert all(
        profile["topology_evidence"]["method"]
        == "authored_endpoint_cycle_recovery"
        for profile in recovered
    )
    assert all(
        profile["topology_evidence"]["authored_crossing_node_count"] == 2
        for profile in recovered
    )
    atomic = [
        profile
        for profile in profiles
        if not profile["topology_evidence"]["authored_cycle_recovered"]
    ]
    assert atomic
    assert all(set(profile) == set(atomic[0]) for profile in recovered)
    assert all(
        set(profile["topology_evidence"])
        == set(atomic[0]["topology_evidence"])
        for profile in recovered
    )
    assert len({profile["profile_id"] for profile in profiles}) == len(profiles)


def test_authored_recovery_does_not_emit_shared_edge_outer_union(tmp_path):
    db = _make_db(tmp_path)
    edges = {
        "LEFT": ((0.0, 0.0), (0.0, 10.0)),
        "MID": ((10.0, 0.0), (10.0, 10.0)),
        "RIGHT": ((20.0, 0.0), (20.0, 10.0)),
        "B0": ((0.0, 0.0), (10.0, 0.0)),
        "B1": ((10.0, 0.0), (20.0, 0.0)),
        "T0": ((0.0, 10.0), (10.0, 10.0)),
        "T1": ((10.0, 10.0), (20.0, 10.0)),
    }
    for handle, (start, end) in edges.items():
        _insert_line(db, handle, start, end)

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 2
    assert [profile["area"] for profile in profiles] == [100.0, 100.0]
    assert not any(
        profile["topology_evidence"]["authored_cycle_recovered"]
        for profile in profiles
    )
    assert not any(profile["area"] == 200.0 for profile in profiles)


def test_overlapping_authored_profiles_ground_by_full_observed_extent(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    db = _make_db(tmp_path)
    rectangle_a = _insert_rectangle(db, "A", 0.0, 0.0, 10.0, 10.0)
    rectangle_b = _insert_rectangle(db, "B", 5.0, -5.0, 15.0, 5.0)
    detected = detect_semantic_objects("mechanical", database=db)
    assert detected["ok"], detected
    snapshot = export_view_image_with_mapping(
        filepath=str(tmp_path / "authored-overlap.wmf"),
        include_overlay=True,
        overlay_granularity="semantic",
        database=db,
    )["data"]["snapshot"]

    for expected in (rectangle_a, rectangle_b):
        authored_item = next(
            item
            for item in snapshot["semantic_overlay_items"]
            if item.get("object_type") == "closed_profile"
            and set(item.get("handles") or []) == expected
        )
        grounded = ground_vlm_region(
            snapshot["snapshot_id"],
            authored_item["pixel_bbox"],
            semantic_type="closed_profile",
            database=db,
        )

        assert grounded["ok"], grounded
        assert set(
            grounded["data"]["recommended_candidate"]["handles"]
        ) == expected
        # An atomic L-shaped face can share the same axis-aligned extent as the
        # authored rectangle. Bbox-only evidence must keep that decision
        # ambiguous even though the recovered authored contour ranks first.
        assert grounded["data"]["selection"]["ambiguous"] is True
        exact = ground_vlm_overlay_id(
            snapshot["snapshot_id"], authored_item["overlay_id"], database=db
        )
        assert exact["ok"], exact
        assert set(exact["handles"]) == expected
