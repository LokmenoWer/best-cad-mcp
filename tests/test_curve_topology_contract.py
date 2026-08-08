import math

import pytest

from src.cad_database import CADDatabase
from src.cad_understanding.drawing_graph import infer_cross_entity_closed_profiles
from src.cad_understanding.semantic_graph import detect_semantic_objects


def _make_db(tmp_path):
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="curve-contract",
        thread_id="regression",
    )
    return db


def _insert_line(db, handle, start, end):
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


def _closed_profiles(result):
    return [
        item
        for item in result["data"]["semantic_objects"]
        if item["object_type"] == "closed_profile"
    ]


def test_unitless_legacy_arc_angles_do_not_create_a_closable_curve_profile(tmp_path):
    db = _make_db(tmp_path)
    _insert_line(db, "DIA", (-10.0, 0.0), (10.0, 0.0))
    db.upsert_entity(
        "ARC",
        "Arc",
        "AcDbArc",
        layer="OUTLINE",
        geometry={
            "center": [0.0, 0.0, 0.0],
            "radius": 10.0,
            "start_angle": 0.0,
            "end_angle": 180.0,
            # Deliberately no angle_unit: legacy producers mixed degrees and
            # radians, so these parameters must stay unresolved.
            "start_point": [10.0, 0.0, 0.0],
            "end_point": [-10.0, 0.0, 0.0],
        },
        topology_detail="full",
    )

    topology = db.get_entity_topology("ARC")
    curve = next(
        item for item in topology["primitives"]
        if item["primitive_type"] == "curve"
    )

    assert curve["properties"]["start_parameter"] is None
    assert curve["properties"]["end_parameter"] is None
    assert curve["properties"]["sweep"] is None
    assert infer_cross_entity_closed_profiles(db) == []


@pytest.mark.parametrize(
    "spline_geometry",
    [
        {
            "start_point": [-10.0, 0.0, 0.0],
            "end_point": [10.0, 0.0, 0.0],
            "degree": 3,
        },
        {
            "start_point": [-10.0, 0.0, 0.0],
            "end_point": [10.0, 0.0, 0.0],
            "control_points": [
                [-10.0, 0.0, 0.0],
                [-5.0, 8.0, 0.0],
                [5.0, 8.0, 0.0],
                [10.0, 0.0, 0.0],
            ],
            "degree": 3,
        },
    ],
    ids=["endpoints-only", "control-points-only"],
)
def test_unsampled_spline_does_not_participate_in_face_graph(
    tmp_path, spline_geometry
):
    db = _make_db(tmp_path)
    _insert_line(db, "BASE", (10.0, 0.0), (-10.0, 0.0))
    db.upsert_entity(
        "SPL",
        "Spline",
        "AcDbSpline",
        layer="OUTLINE",
        geometry=spline_geometry,
        topology_detail="full",
    )

    topology = db.get_entity_topology("SPL")
    curve = next(
        item for item in topology["primitives"]
        if item["primitive_type"] == "curve"
    )

    assert curve["properties"]["fit_points"] is None
    assert curve["properties"]["sampling_contract"] == "endpoints_only"
    assert infer_cross_entity_closed_profiles(db) == []


def test_authored_arc_endpoints_are_used_for_face_connectivity(tmp_path):
    db = _make_db(tmp_path)
    authored_start = (10.0, 0.01)
    authored_end = (-10.0, -0.01)
    _insert_line(db, "CHORD", authored_end, authored_start)
    db.upsert_entity(
        "ARC",
        "Arc",
        "AcDbArc",
        layer="OUTLINE",
        geometry={
            "center": [0.0, 0.0, 0.0],
            "radius": 10.0,
            "start_parameter": 0.0,
            "end_parameter": math.pi,
            "parameter_unit": "radian",
            # The scanned COM endpoints can differ slightly from evaluating
            # the nominal parameters. Connectivity follows authored evidence.
            "start_point": [*authored_start, 0.0],
            "end_point": [*authored_end, 0.0],
        },
        topology_detail="full",
    )

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 1
    assert set(profiles[0]["entity_handles"]) == {"ARC", "CHORD"}
    arc_member = next(
        item for item in profiles[0]["member_primitives"]
        if item["handle"] == "ARC"
    )
    assert arc_member["primitive_type"] == "curve"


def test_negative_minor_axis_controls_ellipse_arc_sampling_and_bbox(tmp_path):
    db = _make_db(tmp_path)
    _insert_line(db, "DIA", (-10.0, 0.0), (10.0, 0.0))
    db.upsert_entity(
        "ELL",
        "Ellipse",
        "AcDbEllipse",
        layer="OUTLINE",
        geometry={
            "center": [0.0, 0.0, 0.0],
            "major_axis": [10.0, 0.0, 0.0],
            "minor_axis": [0.0, -5.0, 0.0],
            "radius_ratio": 0.5,
            "start_parameter": 0.0,
            "end_parameter": math.pi,
            "parameter_unit": "radian",
            "is_arc": True,
        },
        topology_detail="full",
    )

    entity = db.get_entity("ELL")
    profile = infer_cross_entity_closed_profiles(db)[0]

    assert math.isclose(entity["bbox_min_x"], -10.0, abs_tol=1e-9)
    assert math.isclose(entity["bbox_min_y"], -5.0, abs_tol=1e-9)
    assert math.isclose(entity["bbox_max_x"], 10.0, abs_tol=1e-9)
    assert math.isclose(entity["bbox_max_y"], 0.0, abs_tol=1e-9)
    assert set(profile["entity_handles"]) == {"DIA", "ELL"}
    assert all(
        math.isclose(actual, expected, abs_tol=1e-7)
        for actual, expected in zip(profile["bbox"], (-10.0, -5.0, 10.0, 0.0))
    )


def test_only_explicit_full_ellipse_is_a_single_entity_closed_profile(tmp_path):
    db = _make_db(tmp_path)
    db.upsert_entity(
        "FULL",
        "Ellipse",
        "AcDbEllipse",
        layer="OUTLINE",
        geometry={
            "center": [0.0, 0.0, 0.0],
            "major_axis": [10.0, 0.0, 0.0],
            "minor_axis": [0.0, 5.0, 0.0],
            "radius_ratio": 0.5,
            "is_arc": False,
        },
        topology_detail="full",
    )
    db.upsert_entity(
        "UNKNOWN",
        "Ellipse",
        "AcDbEllipse",
        layer="OUTLINE",
        geometry={
            "center": [30.0, 0.0, 0.0],
            "major_axis": [10.0, 0.0, 0.0],
            "radius_ratio": 0.5,
            # Missing is_arc is deliberately an unknown tri-state value.
        },
        topology_detail="full",
    )

    result = detect_semantic_objects("mechanical", database=db)
    handle_sets = {
        frozenset(item["entity_handles"])
        for item in _closed_profiles(result)
    }

    assert frozenset({"FULL"}) in handle_sets
    assert frozenset({"UNKNOWN"}) not in handle_sets
    assert db.get_entity_topology("FULL")["summary"]["is_closed"] == 1
    assert db.get_entity_topology("UNKNOWN")["summary"]["is_closed"] == 0


def test_closed_spline_is_a_single_entity_closed_profile(tmp_path):
    db = _make_db(tmp_path)
    db.upsert_entity(
        "SPL",
        "Spline",
        "AcDbSpline",
        layer="OUTLINE",
        geometry={
            "fit_points": [
                [0.0, 0.0, 0.0],
                [10.0, 0.0, 0.0],
                [10.0, 10.0, 0.0],
                [0.0, 10.0, 0.0],
            ],
            "degree": 3,
            "is_closed": True,
        },
        topology_detail="full",
    )

    result = detect_semantic_objects("mechanical", database=db)
    matches = [
        item for item in _closed_profiles(result)
        if item["entity_handles"] == ["SPL"]
    ]

    assert len(matches) == 1
    assert matches[0]["properties"]["curve_kind"] == "spline"
    assert db.get_entity_topology("SPL")["summary"]["is_closed"] == 1
