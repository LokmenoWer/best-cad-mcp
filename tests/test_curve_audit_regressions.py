import math

import pytest

from src.cad_database import CADDatabase
from src.cad_understanding.drawing_graph import infer_cross_entity_closed_profiles
from src.cad_understanding.semantic_graph import detect_semantic_objects


def _make_db(tmp_path):
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="curve-audit",
        thread_id="regressions",
    )
    return db


def _insert_line(db, handle, start, end):
    db.upsert_entity(
        handle,
        "Line",
        "AcDbLine",
        layer="OUTLINE",
        geometry={"start": [*start, 0.0], "end": [*end, 0.0]},
        topology_detail="full",
    )


def _curve(topology):
    return next(
        primitive
        for primitive in topology["primitives"]
        if primitive["primitive_type"] == "curve"
    )


def test_unitless_semicircle_arc_bbox_is_conservative_full_circle(tmp_path):
    db = _make_db(tmp_path)
    db.upsert_entity(
        "ARC",
        "Arc",
        "AcDbArc",
        layer="OUTLINE",
        geometry={
            "center": [2.0, -3.0, 0.0],
            "radius": 10.0,
            "start_angle": 0.0,
            "end_angle": 180.0,
            # Legacy unitless values cannot safely be interpreted as either
            # degrees or radians. The endpoints therefore describe only the
            # chord and must not be used as the curve's bounding box.
            "start_point": [12.0, -3.0, 0.0],
            "end_point": [-8.0, -3.0, 0.0],
        },
        topology_detail="full",
    )

    entity = db.get_entity("ARC")
    curve = _curve(db.get_entity_topology("ARC"))

    assert curve["properties"]["start_parameter"] is None
    assert curve["properties"]["end_parameter"] is None
    assert curve["properties"]["sweep"] is None
    assert (
        entity["bbox_min_x"],
        entity["bbox_min_y"],
        entity["bbox_max_x"],
        entity["bbox_max_y"],
    ) == pytest.approx((-8.0, -13.0, 12.0, 7.0))


def test_sampled_points_only_spline_persists_contract_and_forms_profile(tmp_path):
    db = _make_db(tmp_path)
    sampled_points = [
        [-10.0, 0.0, 0.0],
        [-6.0, 5.0, 0.0],
        [0.0, 8.0, 0.0],
        [6.0, 5.0, 0.0],
        [10.0, 0.0, 0.0],
    ]
    _insert_line(db, "BASE", (10.0, 0.0), (-10.0, 0.0))
    db.upsert_entity(
        "SPL",
        "Spline",
        "AcDbSpline",
        layer="OUTLINE",
        geometry={"sampled_points": sampled_points, "degree": 3},
        topology_detail="full",
    )

    entity = db.get_entity("SPL")
    topology = db.get_entity_topology("SPL")
    curve = _curve(topology)
    properties = curve["properties"]
    expected_length = sum(
        math.dist(sampled_points[index], sampled_points[index + 1])
        for index in range(len(sampled_points) - 1)
    )

    assert topology["summary"]["curve_count"] == 1
    assert topology["summary"]["line_count"] == 0
    assert topology["summary"]["length"] == pytest.approx(expected_length)
    assert properties["start_point"] == sampled_points[0]
    assert properties["end_point"] == sampled_points[-1]
    assert properties["sampled_points"] == sampled_points
    assert properties["fit_points"] is None
    assert properties["sampling_contract"] == "sampled_points"
    assert properties["sampling"] == {
        "method": "explicit_samples",
        "approximate": True,
    }
    assert [
        [primitive["x"], primitive["y"], primitive["z"]]
        for primitive in topology["primitives"]
        if primitive["role"] == "sample_point"
    ] == sampled_points
    assert (
        entity["bbox_min_x"],
        entity["bbox_min_y"],
        entity["bbox_max_x"],
        entity["bbox_max_y"],
    ) == pytest.approx((-10.0, 0.0, 10.0, 8.0))

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 1
    assert set(profiles[0]["entity_handles"]) == {"BASE", "SPL"}
    assert profiles[0]["curve_count"] == 1
    assert profiles[0]["approximate_curve_count"] == 1
    spline_member = next(
        member
        for member in profiles[0]["member_primitives"]
        if member["handle"] == "SPL"
    )
    assert spline_member["approximate"] is True
    assert spline_member["sampling_method"] == "explicit_samples"


def test_closed_ellipse_with_unknown_arc_flag_is_closed_everywhere(tmp_path):
    db = _make_db(tmp_path)
    db.upsert_entity(
        "ELL",
        "Ellipse",
        "AcDbEllipse",
        layer="OUTLINE",
        geometry={
            "center": [0.0, 0.0, 0.0],
            "major_axis": [12.0, 0.0, 0.0],
            "minor_axis": [0.0, 5.0, 0.0],
            "radius_ratio": 5.0 / 12.0,
            "closed": True,
            "is_arc": None,
        },
        topology_detail="full",
    )

    topology = db.get_entity_topology("ELL")
    curve = _curve(topology)
    result = detect_semantic_objects("mechanical", database=db)
    semantic_profiles = [
        item
        for item in result["data"]["semantic_objects"]
        if item["object_type"] == "closed_profile"
        and item["entity_handles"] == ["ELL"]
    ]

    assert curve["is_closed"] == 1
    assert curve["properties"]["is_arc"] is None
    assert topology["summary"]["is_closed"] == 1
    assert len(semantic_profiles) == 1
    assert semantic_profiles[0]["properties"]["closed"] is True
    assert semantic_profiles[0]["properties"]["curve_kind"] == "ellipse"
