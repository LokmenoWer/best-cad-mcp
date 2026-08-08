import math

import pytest

from src.cad_database import CADDatabase
import src.cad_understanding.drawing_graph as drawing_graph
from src.cad_understanding.drawing_graph import (
    _snap_segment_endpoints,
    infer_cross_entity_closed_profiles,
)


def _make_db(tmp_path) -> CADDatabase:
    db = CADDatabase(str(tmp_path / "face-safety.db"))
    db.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="face-safety",
        thread_id="face-safety",
    )
    return db


def _point3(point):
    return [float(point[0]), float(point[1]), float(point[2]) if len(point) > 2 else 0.0]


def _insert_line(db: CADDatabase, handle: str, start, end) -> None:
    start3 = _point3(start)
    end3 = _point3(end)
    db.upsert_entity(
        handle,
        "Line",
        "AcDbLine",
        layer="OUTLINE",
        geometry={"start": start3, "end": end3},
        bbox=(
            min(start3[0], end3[0]),
            min(start3[1], end3[1]),
            max(start3[0], end3[0]),
            max(start3[1], end3[1]),
        ),
        topology_detail="full",
    )


def _insert_upper_d_profile(db: CADDatabase) -> None:
    _insert_line(db, "BASE", (-10, 0), (10, 0))
    db.upsert_entity(
        "ARC",
        "Arc",
        "AcDbArc",
        layer="OUTLINE",
        geometry={
            "center": [0, 0, 0],
            "radius": 10,
            "start_parameter": 0.0,
            "end_parameter": math.pi,
            "parameter_unit": "radian",
        },
        bbox=(-10, 0, 10, 10),
        topology_detail="full",
    )


def _endpoint_segment(segment_id: str, start, end):
    return {
        "segment_id": segment_id,
        "start": (float(start[0]), float(start[1])),
        "end": (float(end[0]), float(end[1])),
        "start_z": float(start[2]) if len(start) > 2 else 0.0,
        "end_z": float(end[2]) if len(end) > 2 else 0.0,
    }


def test_nonzero_z_coplanar_loop_is_still_a_face(tmp_path):
    db = _make_db(tmp_path)
    for handle, start, end in (
        ("BOTTOM", (0, 0, 10), (10, 0, 10)),
        ("RIGHT", (10, 0, 10), (10, 10, 10)),
        ("TOP", (10, 10, 10), (0, 10, 10)),
        ("LEFT", (0, 10, 10), (0, 0, 10)),
    ):
        _insert_line(db, handle, start, end)

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 1
    assert profiles[0]["area"] == pytest.approx(100.0)


def test_locally_snapped_but_globally_non_coplanar_loop_is_rejected(tmp_path):
    db = _make_db(tmp_path)
    for handle, start, end in (
        ("BOTTOM", (0, 0, 0.0), (10, 0, 0.0)),
        ("RIGHT", (10, 0, 0.75), (10, 10, 0.75)),
        ("TOP", (10, 10, 1.5), (0, 10, 1.5)),
        ("LEFT", (0, 10, 0.75), (0, 0, 0.75)),
    ):
        _insert_line(db, handle, start, end)

    assert infer_cross_entity_closed_profiles(db, tolerance=1.0) == []


def test_cross_plane_bridge_does_not_suppress_valid_xy_face(tmp_path):
    db = _make_db(tmp_path)
    for handle, start, end in (
        ("BOTTOM", (0, 0, 0), (10, 0, 0)),
        ("RIGHT", (10, 0, 0), (10, 10, 0)),
        ("TOP", (10, 10, 0), (0, 10, 0)),
        ("LEFT", (0, 10, 0), (0, 0, 0)),
    ):
        _insert_line(db, handle, start, end)
    _insert_line(db, "Z_BRIDGE", (0, 5, 10), (10, 5, 10))

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 1
    assert set(profiles[0]["entity_handles"]) == {
        "BOTTOM", "RIGHT", "TOP", "LEFT",
    }
    assert profiles[0]["area"] == pytest.approx(100.0)


def test_same_plane_single_contact_bridge_keeps_curve_face(tmp_path):
    db = _make_db(tmp_path)
    _insert_upper_d_profile(db)
    _insert_line(db, "DANGLING", (0, 10), (0, 5))

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 1
    assert set(profiles[0]["entity_handles"]) == {"ARC", "BASE"}


def test_multisegment_divider_with_two_curve_contacts_creates_atomic_faces(tmp_path):
    db = _make_db(tmp_path)
    _insert_upper_d_profile(db)
    arc_x = 10.0 * math.sqrt(3.0) / 2.0
    _insert_line(db, "DIVIDER_LEFT", (-arc_x, 5), (0, 2))
    _insert_line(db, "DIVIDER_RIGHT", (0, 2), (arc_x, 5))

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 2
    by_handles = {
        frozenset(profile["entity_handles"]): profile for profile in profiles
    }
    upper = by_handles[frozenset({"ARC", "DIVIDER_LEFT", "DIVIDER_RIGHT"})]
    lower = by_handles[frozenset({
        "ARC", "BASE", "DIVIDER_LEFT", "DIVIDER_RIGHT",
    })]
    assert upper["area"] == pytest.approx(
        100.0 * math.pi / 3.0 - 10.0 * math.sqrt(3.0), rel=5e-4
    )
    assert lower["area"] == pytest.approx(
        50.0 * math.pi - upper["area"], rel=5e-4
    )
    assert sum(profile["area"] for profile in profiles) == pytest.approx(
        50.0 * math.pi, rel=5e-4
    )


def test_external_line_crossing_curve_twice_creates_two_atomic_faces(tmp_path):
    db = _make_db(tmp_path)
    _insert_upper_d_profile(db)
    _insert_line(db, "THROUGH", (-20, 5), (20, 5))

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 2
    by_handles = {
        frozenset(profile["entity_handles"]): profile for profile in profiles
    }
    upper = by_handles[frozenset({"ARC", "THROUGH"})]
    lower = by_handles[frozenset({"ARC", "BASE", "THROUGH"})]
    expected_upper = 100.0 * math.pi / 3.0 - 25.0 * math.sqrt(3.0)
    assert upper["area"] == pytest.approx(expected_upper, rel=5e-4)
    assert lower["area"] == pytest.approx(
        50.0 * math.pi - expected_upper, rel=5e-4
    )
    assert upper["perimeter"] == pytest.approx(
        10.0 * math.sqrt(3.0) + 20.0 * math.pi / 3.0, rel=5e-6
    )
    assert lower["perimeter"] == pytest.approx(
        20.0 + 10.0 * math.sqrt(3.0) + 10.0 * math.pi / 3.0,
        rel=5e-6,
    )
    assert upper["boundary_edge_count"] == 2
    assert lower["boundary_edge_count"] == 4
    assert not any(
        set(profile["entity_handles"]) == {"ARC", "BASE"}
        for profile in profiles
    )
    assert all(
        profile["topology_evidence"]["line_curve_planarization_complete"]
        for profile in profiles
    )


def test_chord_endpoints_on_arc_interior_create_same_two_atomic_faces(tmp_path):
    db = _make_db(tmp_path)
    _insert_upper_d_profile(db)
    arc_x = 5.0 * math.sqrt(3.0)
    _insert_line(db, "CHORD", (-arc_x, 5), (arc_x, 5))

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 2
    assert {frozenset(profile["entity_handles"]) for profile in profiles} == {
        frozenset({"ARC", "CHORD"}),
        frozenset({"ARC", "BASE", "CHORD"}),
    }
    assert sum(profile["area"] for profile in profiles) == pytest.approx(
        50.0 * math.pi, rel=5e-4
    )


def test_internal_internal_arc_tangent_does_not_split_face(tmp_path):
    db = _make_db(tmp_path)
    _insert_upper_d_profile(db)
    _insert_line(db, "TANGENT", (-20, 10), (20, 10))

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 1
    assert set(profiles[0]["entity_handles"]) == {"ARC", "BASE"}
    assert profiles[0]["area"] == pytest.approx(50.0 * math.pi, rel=5e-4)


def test_line_extension_root_outside_finite_segment_does_not_split_arc(tmp_path):
    db = _make_db(tmp_path)
    _insert_upper_d_profile(db)
    _insert_line(db, "SHORT", (-20, 5), (-15, 5))

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 1
    assert set(profiles[0]["entity_handles"]) == {"ARC", "BASE"}


def test_line_crossing_circle_outside_open_arc_domain_does_not_split(tmp_path):
    db = _make_db(tmp_path)
    _insert_upper_d_profile(db)
    _insert_line(db, "LOWER", (-20, -5), (20, -5))

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 1
    assert set(profiles[0]["entity_handles"]) == {"ARC", "BASE"}


def test_projected_line_curve_crossing_on_different_z_is_ignored(tmp_path):
    db = _make_db(tmp_path)
    _insert_upper_d_profile(db)
    _insert_line(db, "Z_THROUGH", (-20, 5, 10), (20, 5, 10))

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 1
    assert set(profiles[0]["entity_handles"]) == {"ARC", "BASE"}


def test_line_curve_pair_budget_fails_atomically(tmp_path):
    db = _make_db(tmp_path)
    _insert_upper_d_profile(db)
    _insert_line(db, "THROUGH", (-20, 5), (20, 5))

    assert infer_cross_entity_closed_profiles(db, max_pair_checks=0) == []
    assert len(infer_cross_entity_closed_profiles(db, max_pair_checks=100)) == 2


def test_many_arc_dividers_emit_all_atomic_bands_or_none(tmp_path):
    db = _make_db(tmp_path)
    _insert_upper_d_profile(db)
    for y in range(1, 9):
        _insert_line(db, f"H{y}", (-20, y), (20, y))

    assert infer_cross_entity_closed_profiles(db, max_pair_checks=4) == []
    profiles = infer_cross_entity_closed_profiles(db, max_pair_checks=100)

    assert len(profiles) == 9
    assert sum(profile["area"] for profile in profiles) == pytest.approx(
        50.0 * math.pi, rel=5e-4
    )
    handle_sets = {frozenset(profile["entity_handles"]) for profile in profiles}
    assert frozenset({"ARC", "BASE", "H1"}) in handle_sets
    assert frozenset({"ARC", "H8"}) in handle_sets
    assert all(
        frozenset({"ARC", f"H{index}", f"H{index + 1}"}) in handle_sets
        for index in range(1, 8)
    )


def test_below_resolution_near_tangent_fails_closed(tmp_path):
    db = _make_db(tmp_path)
    _insert_upper_d_profile(db)
    _insert_line(db, "NEAR_TANGENT", (-20, 10 - 1e-10), (20, 10 - 1e-10))

    assert infer_cross_entity_closed_profiles(db, tolerance=1e-4) == []


def test_two_roots_inside_one_parent_sample_interval_keep_thin_curved_face(tmp_path):
    db = _make_db(tmp_path)
    _insert_upper_d_profile(db)
    radius = 10.0
    midpoint_angle = 0.3068
    half_sweep = 0.003
    chord_start = (
        radius * math.cos(midpoint_angle - half_sweep),
        radius * math.sin(midpoint_angle - half_sweep),
    )
    chord_end = (
        radius * math.cos(midpoint_angle + half_sweep),
        radius * math.sin(midpoint_angle + half_sweep),
    )
    _insert_line(db, "THIN_CHORD", chord_start, chord_end)

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 2
    thin = next(
        profile for profile in profiles
        if set(profile["entity_handles"]) == {"ARC", "THIN_CHORD"}
    )
    expected_area = radius * radius * (
        half_sweep - math.sin(half_sweep) * math.cos(half_sweep)
    )
    assert thin["area"] == pytest.approx(expected_area, rel=5e-4)
    assert thin["boundary_edge_count"] == 2


def test_line_curve_profile_ids_ignore_source_line_direction(tmp_path):
    forward_path = tmp_path / "forward"
    reverse_path = tmp_path / "reverse"
    forward_path.mkdir()
    reverse_path.mkdir()
    forward = _make_db(forward_path)
    _insert_upper_d_profile(forward)
    _insert_line(forward, "THROUGH", (-20, 5), (20, 5))
    reverse = _make_db(reverse_path)
    _insert_upper_d_profile(reverse)
    _insert_line(reverse, "BASE", (10, 0), (-10, 0))
    _insert_line(reverse, "THROUGH", (20, 5), (-20, 5))

    def ids_by_handles(database):
        return {
            frozenset(profile["entity_handles"]): profile["profile_id"]
            for profile in infer_cross_entity_closed_profiles(database)
        }

    assert ids_by_handles(forward) == ids_by_handles(reverse)


def test_repeated_endpoints_preserve_weighted_node_center_and_exact_diameter():
    segments = [
        _endpoint_segment(
            f"COMMON_{index}",
            (0, 0, 0),
            (1000 + 2 * index, 1000, 0),
        )
        for index in range(100)
    ]
    segments.append(
        _endpoint_segment("OFFSET", (0.5, 0, 0), (2000, 2000, 0))
    )

    snapped, centers, spreads = _snap_segment_endpoints(segments, tolerance=1.0)
    common_node = snapped[0]["start_node"]

    assert {segment["start_node"] for segment in snapped} == {common_node}
    assert centers[common_node][0] == pytest.approx(0.5 / 101.0)
    assert centers[common_node][1] == pytest.approx(0.0)
    assert spreads[common_node] == pytest.approx(0.5)


def test_endpoint_spread_is_pairwise_diameter_not_centroid_upper_bound():
    starts = ((0, 0, 0), (0.6, 0, 0), (0.3, 0.4, 0))
    segments = [
        _endpoint_segment(
            f"S{index}",
            start,
            (100 + 10 * index, 100, 0),
        )
        for index, start in enumerate(starts)
    ]

    snapped, _, spreads = _snap_segment_endpoints(segments, tolerance=1.0)
    common_node = snapped[0]["start_node"]

    assert {segment["start_node"] for segment in snapped} == {common_node}
    assert spreads[common_node] == pytest.approx(0.6)


def test_identical_endpoint_fast_path_avoids_quadratic_distance_checks(monkeypatch):
    original_dist = drawing_graph.math.dist
    distance_calls = 0

    def counted_dist(first, second):
        nonlocal distance_calls
        distance_calls += 1
        return original_dist(first, second)

    monkeypatch.setattr(drawing_graph.math, "dist", counted_dist)
    segment_count = 5000
    segments = [
        _endpoint_segment(
            f"STAR_{index}",
            (0, 0, 0),
            (100 + 2 * index, 100, 0),
        )
        for index in range(segment_count)
    ]

    snapped, _, _ = _snap_segment_endpoints(segments, tolerance=1.0)

    assert len(snapped) == segment_count
    assert len({segment["start_node"] for segment in snapped}) == 1
    assert distance_calls < segment_count
