import math

import pytest

from src.cad_database import CADDatabase
from src.cad_understanding.semantic_graph import (
    detect_semantic_objects,
    find_semantic_objects,
    get_semantic_graph,
)
from src.cad_understanding.drawing_graph import (
    _bridge_edge_indices,
    _snap_segment_endpoints,
    infer_cross_entity_closed_profiles,
)


def make_db(tmp_path):
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.configure_context(workspace_root=str(tmp_path), conversation_id="conv", thread_id="thread")
    return db


def populate_semantic_fixture(db):
    db.upsert_entity(
        "P1",
        "Polyline",
        "AcDbPolyline",
        layer="OUTLINE",
        geometry={"vertices": [[0, 0, 0], [100, 0, 0], [100, 60, 0], [0, 60, 0]], "closed": True},
        bbox=(0, 0, 100, 60),
        topology_detail="full",
    )
    for i, x in enumerate([20, 50, 80], start=1):
        db.upsert_entity(
            f"C{i}",
            "Circle",
            "AcDbCircle",
            layer="HOLES",
            geometry={"center": [x, 30, 0], "radius": 3},
            bbox=(x - 3, 27, x + 3, 33),
            topology_detail="full",
        )
    db.upsert_entity(
        "T1",
        "Text",
        "AcDbText",
        layer="TEXT",
        geometry={"text": "PLATE A"},
        bbox=(5, 5, 20, 10),
    )
    db.upsert_entity(
        "B1",
        "BlockReference",
        "AcDbBlockReference",
        layer="PARTS",
        geometry={"block_name": "PIN"},
        bbox=(40, 20, 45, 25),
    )


def test_detect_semantic_objects_rule_based_fixture(tmp_path):
    db = make_db(tmp_path)
    populate_semantic_fixture(db)

    result = detect_semantic_objects("mechanical", database=db)
    graph = get_semantic_graph(database=db)["data"]
    types = {obj["object_type"] for obj in graph["semantic_objects"]}

    assert result["ok"] is True
    assert "closed_profile" in types
    assert "hole" in types
    assert "bolt_circle_pattern" in types or "hole_pattern" in types
    assert "text_annotation" in types
    assert "block_instance" in types
    assert graph["semantic_relations"]


def test_detect_architecture_electrical_and_drafting_candidates(tmp_path):
    db = make_db(tmp_path)
    db.upsert_entity(
        "W1",
        "Line",
        "AcDbLine",
        layer="A-WALL",
        geometry={"start": [0, 0, 0], "end": [20, 0, 0]},
        bbox=(0, 0, 20, 0),
    )
    db.upsert_entity(
        "DR1",
        "BlockReference",
        "AcDbBlockReference",
        layer="A-DOOR",
        geometry={"block_name": "DOOR_900"},
        bbox=(4, -1, 6, 1),
    )
    arch = detect_semantic_objects("architecture", database=db)
    arch_types = {obj["object_type"] for obj in arch["data"]["semantic_objects"]}

    assert "wall_candidate" in arch_types
    assert "door" in arch_types

    db.upsert_entity(
        "E1",
        "Polyline",
        "AcDbPolyline",
        layer="E-WIRE",
        geometry={"vertices": [[0, 5, 0], [20, 5, 0]], "closed": False},
        bbox=(0, 5, 20, 5),
    )
    db.upsert_entity(
        "TB1",
        "BlockReference",
        "AcDbBlockReference",
        layer="E-DEVICE",
        geometry={"block_name": "TERMINAL_BLOCK"},
        bbox=(10, 4, 12, 6),
    )
    electrical = detect_semantic_objects("electrical", database=db)
    electrical_types = {obj["object_type"] for obj in electrical["data"]["semantic_objects"]}

    assert "wire" in electrical_types
    assert "terminal" in electrical_types

    db.upsert_entity(
        "T1",
        "Text",
        "AcDbText",
        layer="TITLE",
        geometry={"text": "REVISION TABLE"},
        bbox=(0, -10, 20, -8),
    )
    drafting = detect_semantic_objects("drafting", database=db)
    drafting_types = {obj["object_type"] for obj in drafting["data"]["semantic_objects"]}

    assert "revision_table" in drafting_types


def test_find_semantic_objects_filters_by_handle_domain_and_confidence(tmp_path):
    db = make_db(tmp_path)
    populate_semantic_fixture(db)
    detect_semantic_objects("mechanical", database=db)

    result = find_semantic_objects(
        object_type="hole",
        handle="C1",
        domain="mechanical",
        confidence_threshold=0.5,
        database=db,
    )
    objects = result["data"]["semantic_objects"]

    assert result["ok"]
    assert objects
    assert objects[0]["entity_handles"] == ["C1"]


def test_independent_lines_are_promoted_to_one_closed_profile(tmp_path):
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
    # A visually nearby construction/center line must not become part of the
    # contour merely because it crosses the rectangle.
    db.upsert_entity(
        "CL1",
        "Line",
        "AcDbLine",
        layer="CENTER",
        linetype="CENTER",
        geometry={"start": [-10, 10, 0], "end": [50, 10, 0]},
        bbox=(-10, 10, 50, 10),
        topology_detail="full",
    )

    result = detect_semantic_objects("mechanical", database=db)

    assert result["ok"], result
    profiles = [
        item for item in result["data"]["semantic_objects"]
        if item["object_type"] == "closed_profile"
        and set(item["entity_handles"]) == set(edges)
    ]
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile["properties"]["rule_name"] == "cross_entity_endpoint_cycle"
    assert profile["properties"]["segment_count"] == 4
    assert profile["properties"]["area"] == 800.0
    assert "CL1" not in profile["entity_handles"]


def _insert_line(db, handle, start, end, layer="OUTLINE"):
    db.upsert_entity(
        handle,
        "Line",
        "AcDbLine",
        layer=layer,
        geometry={"start": [*start, 0], "end": [*end, 0]},
        bbox=(
            min(start[0], end[0]), min(start[1], end[1]),
            max(start[0], end[0]), max(start[1], end[1]),
        ),
        topology_detail="full",
    )


def test_bounded_face_survives_outline_branch_at_vertex(tmp_path):
    db = make_db(tmp_path)
    boundary = {
        "L1": ((0, 0), (40, 0)),
        "L2": ((40, 0), (40, 20)),
        "L3": ((40, 20), (0, 20)),
        "L4": ((0, 20), (0, 0)),
    }
    for handle, (start, end) in boundary.items():
        _insert_line(db, handle, start, end)
    _insert_line(db, "BRANCH", (40, 20), (55, 28))

    profiles = infer_cross_entity_closed_profiles(db)

    matches = [item for item in profiles if set(item["entity_handles"]) == set(boundary)]
    assert len(matches) == 1
    assert matches[0]["area"] == 800.0
    assert matches[0]["branch_node_count"] == 1
    assert matches[0]["topology_evidence"]["method"] == "planar_half_edge_face_walk"
    assert "BRANCH" not in matches[0]["entity_handles"]


def test_internal_dangling_branch_is_removed_before_face_walk(tmp_path):
    db = make_db(tmp_path)
    boundary = {
        "L1": ((0, 0), (40, 0)),
        "L2": ((40, 0), (40, 20)),
        "L3": ((40, 20), (0, 20)),
        "L4": ((0, 20), (0, 0)),
    }
    for handle, (start, end) in boundary.items():
        _insert_line(db, handle, start, end)
    _insert_line(db, "INNER_BRANCH", (40, 20), (20, 10))

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 1
    assert set(profiles[0]["entity_handles"]) == set(boundary)
    assert profiles[0]["area"] == 800.0
    assert profiles[0]["adjacent_bridge_count"] == 1
    assert profiles[0]["topology_evidence"]["removed_bridge_count"] == 1


def test_shared_edge_network_yields_each_bounded_face_not_outer_cycle(tmp_path):
    db = make_db(tmp_path)
    edges = {
        "LEFT": ((0, 0), (0, 10)),
        "MID": ((10, 0), (10, 10)),
        "RIGHT": ((20, 0), (20, 10)),
        "B0": ((0, 0), (10, 0)),
        "B1": ((10, 0), (20, 0)),
        "T0": ((0, 10), (10, 10)),
        "T1": ((10, 10), (20, 10)),
    }
    for handle, (start, end) in edges.items():
        _insert_line(db, handle, start, end)

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 2
    assert [item["area"] for item in profiles] == [100.0, 100.0]
    handle_sets = {frozenset(item["entity_handles"]) for item in profiles}
    assert handle_sets == {
        frozenset({"LEFT", "MID", "B0", "T0"}),
        frozenset({"MID", "RIGHT", "B1", "T1"}),
    }
    assert all(item["branch_node_count"] == 2 for item in profiles)


def test_t_junctions_split_long_edges_into_two_bounded_faces(tmp_path):
    db = make_db(tmp_path)
    for handle, start, end in (
        ("BOTTOM", (0, 0), (20, 0)),
        ("TOP", (0, 10), (20, 10)),
        ("LEFT", (0, 0), (0, 10)),
        ("MID", (10, 0), (10, 10)),
        ("RIGHT", (20, 0), (20, 10)),
    ):
        _insert_line(db, handle, start, end)

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 2
    assert [item["area"] for item in profiles] == [100.0, 100.0]
    assert all(item["topology_evidence"]["planarized_boundary_edges"] == 2 for item in profiles)
    assert {frozenset(item["entity_handles"]) for item in profiles} == {
        frozenset({"BOTTOM", "TOP", "LEFT", "MID"}),
        frozenset({"BOTTOM", "TOP", "MID", "RIGHT"}),
    }


def test_long_line_grid_returns_only_four_cells(tmp_path):
    db = make_db(tmp_path)
    for index, y in enumerate((0, 10, 20)):
        _insert_line(db, f"H{index}", (0, y), (20, y))
    for index, x in enumerate((0, 10, 20)):
        _insert_line(db, f"V{index}", (x, 0), (x, 20))

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 4
    assert all(item["area"] == 100.0 for item in profiles)
    assert sum(item["area"] for item in profiles) == 400.0
    assert not any(item["area"] == 400.0 for item in profiles)


def test_crossing_lines_are_planarized_but_do_not_invent_a_face(tmp_path):
    db = make_db(tmp_path)
    _insert_line(db, "X1", (0, 0), (10, 10))
    _insert_line(db, "X2", (0, 10), (10, 0))

    assert infer_cross_entity_closed_profiles(db) == []


def test_endpoint_clustering_does_not_transitively_merge_a_chain():
    segments = [
        {
            "segment_id": f"S{index}",
            "start": start,
            "end": (start[0], 10.0 + index),
        }
        for index, start in enumerate(((0.0, 0.0), (0.75, 0.0), (1.5, 0.0)))
    ]

    snapped, _, _ = _snap_segment_endpoints(segments, tolerance=1.0)

    assert len({segment["start_node"] for segment in snapped}) == 2


def test_profile_id_is_stable_when_line_directions_are_reversed(tmp_path):
    db = make_db(tmp_path)
    edges = {
        "L1": ((0, 0), (40, 0)),
        "L2": ((40, 0), (40, 20)),
        "L3": ((40, 20), (0, 20)),
        "L4": ((0, 20), (0, 0)),
    }
    for handle, (start, end) in edges.items():
        _insert_line(db, handle, start, end)
    original_id = infer_cross_entity_closed_profiles(db)[0]["profile_id"]

    for handle, (start, end) in reversed(list(edges.items())):
        _insert_line(db, handle, end, start)
    reversed_id = infer_cross_entity_closed_profiles(db)[0]["profile_id"]

    assert reversed_id == original_id


def test_bridge_detection_handles_long_imported_chain_without_recursion():
    segments = [
        {"start_node": index, "end_node": index + 1}
        for index in range(2500)
    ]

    assert len(_bridge_edge_indices(segments)) == len(segments)


def test_near_duplicate_straight_edge_coalesces_after_endpoint_snapping(tmp_path):
    db = make_db(tmp_path)
    for handle, start, end in (
        ("BOTTOM", (0, 0), (10, 0)),
        ("BOTTOM_DUP", (0, 0.5), (10, 0.5)),
        ("RIGHT", (10, 0), (10, 10)),
        ("TOP", (10, 10), (0, 10)),
        ("LEFT", (0, 10), (0, 0)),
    ):
        _insert_line(db, handle, start, end)

    profiles = infer_cross_entity_closed_profiles(db, tolerance=1.0)

    assert len(profiles) == 1
    assert set(profiles[0]["entity_handles"]) == {
        "BOTTOM", "BOTTOM_DUP", "RIGHT", "TOP", "LEFT",
    }
    assert profiles[0]["topology_evidence"]["coincident_sources_preserved"] is True


def test_incomplete_planarization_budget_never_promotes_partial_outer_face(tmp_path):
    db = make_db(tmp_path)
    for handle, start, end in (
        ("BOTTOM", (0, 0), (20, 0)),
        ("TOP", (0, 10), (20, 10)),
        ("LEFT", (0, 0), (0, 10)),
        ("MID", (10, 0), (10, 10)),
        ("RIGHT", (20, 0), (20, 10)),
    ):
        _insert_line(db, handle, start, end)

    assert infer_cross_entity_closed_profiles(db, max_pair_checks=1) == []


def test_projected_xy_loop_at_different_elevations_is_not_a_planar_face(tmp_path):
    db = make_db(tmp_path)
    for handle, start, end in (
        ("L1", (0, 0, 0), (10, 0, 0)),
        ("L2", (10, 0, 10), (10, 10, 10)),
        ("L3", (10, 10, 20), (0, 10, 20)),
        ("L4", (0, 10, 30), (0, 0, 30)),
    ):
        db.upsert_entity(
            handle, "Line", "AcDbLine", layer="OUTLINE",
            geometry={"start": list(start), "end": list(end)},
            bbox=(
                min(start[0], end[0]), min(start[1], end[1]),
                max(start[0], end[0]), max(start[1], end[1]),
            ),
            topology_detail="full",
        )

    assert infer_cross_entity_closed_profiles(db) == []


def test_arc_topology_binds_endpoints_to_curve_without_fake_chord(tmp_path):
    db = make_db(tmp_path)
    db.upsert_entity(
        "A1", "Arc", "AcDbArc", layer="OUTLINE",
        geometry={
            "center": [0, 0, 0], "radius": 10,
            "start_angle": 0, "end_angle": 180, "angle_unit": "degree",
        },
        topology_detail="full",
    )

    topology = db.get_entity_topology("A1")

    assert topology["summary"]["line_count"] == 0
    assert topology["summary"]["curve_count"] == 1
    assert not [item for item in topology["primitives"] if item["primitive_type"] == "line"]
    curve = next(item for item in topology["primitives"] if item["primitive_type"] == "curve")
    assert curve["properties"]["parameter_unit"] == "radian"
    assert curve["properties"]["start_point"][:2] == [10.0, 0.0]
    assert math.isclose(curve["properties"]["end_point"][0], -10.0, abs_tol=1e-9)
    endpoint_relations = {
        (item["from_key"], item["to_key"], item["relation_type"])
        for item in topology["relations"]
    }
    assert ("c0", "p0", "starts_at") in endpoint_relations
    assert ("c0", "p1", "ends_at") in endpoint_relations
    entity = db.get_entity("A1")
    assert math.isclose(entity["bbox_min_x"], -10.0, abs_tol=1e-9)
    assert math.isclose(entity["bbox_min_y"], 0.0, abs_tol=1e-9)
    assert math.isclose(entity["bbox_max_x"], 10.0, abs_tol=1e-9)
    assert math.isclose(entity["bbox_max_y"], 10.0, abs_tol=1e-9)


def test_d_profile_keeps_arc_and_chord_as_two_distinct_boundary_edges(tmp_path):
    db = make_db(tmp_path)
    _insert_line(db, "DIA", (-10, 0), (10, 0))
    db.upsert_entity(
        "ARC", "Arc", "AcDbArc", layer="OUTLINE",
        geometry={
            "center": [0, 0, 0], "radius": 10,
            "start_parameter": 0.0,
            "end_parameter": math.pi,
            "parameter_unit": "radian",
        },
        bbox=(-10, 0, 10, 10),
        topology_detail="full",
    )

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 1
    profile = profiles[0]
    assert set(profile["entity_handles"]) == {"DIA", "ARC"}
    assert profile["boundary_edge_count"] == 2
    assert profile["curve_count"] == 1
    assert math.isclose(profile["area"], math.pi * 50.0, rel_tol=0.003)
    assert math.isclose(profile["perimeter"], 20.0 + math.pi * 10.0, rel_tol=0.003)
    assert {item["primitive_type"] for item in profile["member_primitives"]} == {"line", "curve"}


def test_half_ellipse_and_diameter_form_an_analytic_profile(tmp_path):
    db = make_db(tmp_path)
    _insert_line(db, "DIA", (-10, 0), (10, 0))
    db.upsert_entity(
        "ELL", "Ellipse", "AcDbEllipse", layer="OUTLINE",
        geometry={
            "center": [0, 0, 0],
            "major_axis": [10, 0, 0],
            "radius_ratio": 0.5,
            "start_angle": 0,
            "end_angle": 180,
            "angle_unit": "degree",
            "is_arc": True,
        },
        topology_detail="full",
    )

    profile = infer_cross_entity_closed_profiles(db)[0]

    assert set(profile["entity_handles"]) == {"DIA", "ELL"}
    assert profile["curve_count"] == 1
    assert profile["approximate_curve_count"] == 0
    assert math.isclose(profile["area"], math.pi * 10.0 * 5.0 / 2.0, rel_tol=0.003)
    assert all(math.isclose(value, expected, abs_tol=1e-7) for value, expected in zip(
        profile["bbox"], (-10.0, 0.0, 10.0, 5.0)
    ))


def test_half_ellipse_crossed_by_line_creates_two_atomic_faces(tmp_path):
    db = make_db(tmp_path)
    _insert_line(db, "BASE", (-10, 0), (10, 0))
    _insert_line(db, "THROUGH", (-20, 2.5), (20, 2.5))
    db.upsert_entity(
        "ELL", "Ellipse", "AcDbEllipse", layer="OUTLINE",
        geometry={
            "center": [0, 0, 0],
            "major_axis": [10, 0, 0],
            "radius_ratio": 0.5,
            "start_angle": 0,
            "end_angle": 180,
            "angle_unit": "degree",
            "is_arc": True,
        },
        topology_detail="full",
    )

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 2
    by_handles = {
        frozenset(profile["entity_handles"]): profile for profile in profiles
    }
    upper = by_handles[frozenset({"ELL", "THROUGH"})]
    lower = by_handles[frozenset({"ELL", "BASE", "THROUGH"})]
    expected_upper = 50.0 * math.pi / 3.0 - 12.5 * math.sqrt(3.0)
    assert upper["area"] == pytest.approx(expected_upper, rel=5e-4)
    assert lower["area"] == pytest.approx(
        25.0 * math.pi - expected_upper, rel=5e-4
    )
    assert sum(profile["area"] for profile in profiles) == pytest.approx(
        25.0 * math.pi, rel=5e-4
    )
    assert upper["bbox"] == pytest.approx(
        (-5.0 * math.sqrt(3.0), 2.5, 5.0 * math.sqrt(3.0), 5.0),
        abs=1e-8,
    )
    assert upper["boundary_edge_count"] == 2
    assert lower["boundary_edge_count"] == 4


def test_full_circle_diameter_creates_two_unique_semicircle_faces(tmp_path):
    db = make_db(tmp_path)
    _insert_line(db, "DIA", (-20, 0), (20, 0))
    db.upsert_entity(
        "C", "Circle", "AcDbCircle", layer="OUTLINE",
        geometry={
            "center": [0, 0, 0], "radius": 10, "normal": [0, 0, 1],
        },
        bbox=(-10, -10, 10, 10),
        topology_detail="full",
    )

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 2
    assert all(set(profile["entity_handles"]) == {"C", "DIA"} for profile in profiles)
    assert all(
        profile["area"] == pytest.approx(50.0 * math.pi, rel=5e-4)
        for profile in profiles
    )
    assert len({profile["profile_id"] for profile in profiles}) == 2
    assert {round(profile["bbox"][1], 8) for profile in profiles} == {-10.0, 0.0}


def test_full_ellipse_diameter_creates_two_unique_half_faces(tmp_path):
    db = make_db(tmp_path)
    _insert_line(db, "DIA", (-20, 0), (20, 0))
    db.upsert_entity(
        "ELL", "Ellipse", "AcDbEllipse", layer="OUTLINE",
        geometry={
            "center": [0, 0, 0],
            "major_axis": [10, 0, 0],
            "radius_ratio": 0.5,
            "is_arc": False,
            "normal": [0, 0, 1],
        },
        bbox=(-10, -5, 10, 5),
        topology_detail="full",
    )

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 2
    assert all(
        profile["area"] == pytest.approx(25.0 * math.pi, rel=5e-4)
        for profile in profiles
    )
    assert len({profile["profile_id"] for profile in profiles}) == 2


def test_rotated_negative_minor_axis_ellipse_arc_splits_in_native_domain(tmp_path):
    db = make_db(tmp_path)
    angle = math.radians(30.0)
    major = (10.0 * math.cos(angle), 10.0 * math.sin(angle))
    # Deliberately reverse the conventional minor axis; the inverse-map
    # determinant is negative and must not reverse root ordering accidentally.
    minor = (5.0 * math.sin(angle), -5.0 * math.cos(angle))
    offset = (minor[0] / 2.0, minor[1] / 2.0)
    unit_major = (math.cos(angle), math.sin(angle))
    _insert_line(db, "BASE", (-major[0], -major[1]), major)
    _insert_line(
        db,
        "THROUGH",
        (offset[0] - 20.0 * unit_major[0], offset[1] - 20.0 * unit_major[1]),
        (offset[0] + 20.0 * unit_major[0], offset[1] + 20.0 * unit_major[1]),
    )
    db.upsert_entity(
        "ELL", "Ellipse", "AcDbEllipse", layer="OUTLINE",
        geometry={
            "center": [0, 0, 0],
            "major_axis": [major[0], major[1], 0],
            "minor_axis": [minor[0], minor[1], 0],
            "radius_ratio": 0.5,
            "start_parameter": 0.0,
            "end_parameter": math.pi,
            "parameter_unit": "radian",
            "is_arc": True,
            "normal": [0, 0, 1],
        },
        bbox=(-12, -12, 12, 12),
        topology_detail="full",
    )

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 2
    assert sum(profile["area"] for profile in profiles) == pytest.approx(
        25.0 * math.pi, rel=5e-4
    )
    assert {frozenset(profile["entity_handles"]) for profile in profiles} == {
        frozenset({"ELL", "THROUGH"}),
        frozenset({"ELL", "BASE", "THROUGH"}),
    }


def test_capsule_profile_uses_analytic_arc_samples_for_area_and_bbox(tmp_path):
    db = make_db(tmp_path)
    _insert_line(db, "TOP", (0, 10), (40, 10))
    _insert_line(db, "BOTTOM", (40, -10), (0, -10))
    for handle, center, start, end in (
        ("RIGHT", [40, 0, 0], 3.0 * math.pi / 2.0, math.pi / 2.0),
        ("LEFT", [0, 0, 0], math.pi / 2.0, 3.0 * math.pi / 2.0),
    ):
        db.upsert_entity(
            handle, "Arc", "AcDbArc", layer="OUTLINE",
            geometry={
                "center": center, "radius": 10,
                "start_parameter": start,
                "end_parameter": end,
                "parameter_unit": "radian",
            },
            topology_detail="full",
        )

    profile = infer_cross_entity_closed_profiles(db)[0]

    assert set(profile["entity_handles"]) == {"TOP", "BOTTOM", "LEFT", "RIGHT"}
    assert profile["curve_count"] == 2
    assert profile["approximate_curve_count"] == 0
    assert math.isclose(profile["area"], 800.0 + math.pi * 100.0, rel_tol=0.003)
    assert math.isclose(profile["perimeter"], 80.0 + math.pi * 20.0, rel_tol=0.003)
    assert all(math.isclose(value, expected, abs_tol=1e-7) for value, expected in zip(
        profile["bbox"], (-10.0, -10.0, 50.0, 10.0)
    ))


def test_spline_topology_is_curve_not_fit_point_chords(tmp_path):
    db = make_db(tmp_path)
    db.upsert_entity(
        "S1", "Spline", "AcDbSpline", layer="OUTLINE",
        geometry={
            "fit_points": [[0, 0, 0], [5, 4, 0], [10, 0, 0]],
            "degree": 2,
        },
        topology_detail="full",
    )

    topology = db.get_entity_topology("S1")

    assert topology["summary"]["curve_count"] == 1
    assert topology["summary"]["line_count"] == 0
    assert topology["summary"]["length"] > 10.0
    assert not [item for item in topology["primitives"] if item["primitive_type"] == "line"]
    curve = next(item for item in topology["primitives"] if item["primitive_type"] == "curve")
    assert curve["properties"]["sampling"]["approximate"] is True
    assert curve["properties"]["sampling"]["method"] == "fit_point_polyline"


def test_spline_plus_closing_line_is_kept_as_explicit_low_confidence_approximation(tmp_path):
    db = make_db(tmp_path)
    _insert_line(db, "BASE", (10, 0), (-10, 0))
    db.upsert_entity(
        "S1", "Spline", "AcDbSpline", layer="OUTLINE",
        geometry={
            "fit_points": [[-10, 0, 0], [0, 5, 0], [10, 0, 0]],
            "degree": 2,
        },
        topology_detail="full",
    )

    profile = infer_cross_entity_closed_profiles(db)[0]

    assert set(profile["entity_handles"]) == {"BASE", "S1"}
    assert profile["curve_count"] == 1
    assert profile["approximate_curve_count"] == 1
    spline_member = next(
        item for item in profile["member_primitives"] if item["handle"] == "S1"
    )
    assert spline_member["approximate"] is True
    assert spline_member["sampling_method"] == "fit_point_polyline"
    assert spline_member["sampling_error_bound"] is None
    assert spline_member["sampling_certified"] is False
    assert profile["topology_evidence"]["curve_sampling_certified"] is False
    assert profile["confidence"] < 0.9


def test_uncertified_spline_interior_crossing_fails_closed(tmp_path):
    db = make_db(tmp_path)
    _insert_line(db, "BASE", (10, 0), (-10, 0))
    _insert_line(db, "THROUGH", (-20, 2.5), (20, 2.5))
    db.upsert_entity(
        "S1", "Spline", "AcDbSpline", layer="OUTLINE",
        geometry={
            "fit_points": [[-10, 0, 0], [0, 5, 0], [10, 0, 0]],
            "degree": 2,
        },
        topology_detail="full",
    )

    # Fit points bound neither the real NURBS path nor its intersection count.
    # The sample chord detects ambiguity but must not invent a high-confidence
    # topological split.
    assert infer_cross_entity_closed_profiles(db) == []
