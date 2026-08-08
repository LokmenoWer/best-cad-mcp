import math

import pytest

from src.cad_database import CADDatabase
from src.cad_understanding.drawing_graph import (
    _planarize_line_segments,
    infer_cross_entity_closed_profiles,
)


def _make_db(tmp_path) -> CADDatabase:
    db = CADDatabase(str(tmp_path / "drawing-graph-audit.db"))
    db.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="drawing-graph-audit",
        thread_id="regressions",
    )
    return db


def _raw_line(segment_id, start, end):
    return {
        "segment_id": segment_id,
        "handle": segment_id,
        "primitive_key": "",
        "start": (float(start[0]), float(start[1])),
        "end": (float(end[0]), float(end[1])),
        "samples": [
            (float(start[0]), float(start[1])),
            (float(end[0]), float(end[1])),
        ],
        "length": math.dist(start, end),
        "layer": "OUTLINE",
        "primitive_type": "line",
        "curve_kind": "",
        "start_z": 0.0,
        "end_z": 0.0,
        "z_span": 0.0,
        "plane_z": 0.0,
    }


def _insert_line(db: CADDatabase, handle: str, start, end) -> None:
    start3 = [float(start[0]), float(start[1]), 0.0]
    end3 = [float(end[0]), float(end[1]), 0.0]
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


def _insert_upper_d(db: CADDatabase) -> None:
    _insert_line(db, "BASE", (-10.0, 0.0), (10.0, 0.0))
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
        },
        bbox=(-10.0, 0.0, 10.0, 10.0),
        topology_detail="full",
    )


def test_y_separated_same_x_segments_do_not_exhaust_pair_budget():
    segments = [
        _raw_line(
            f"V{index}",
            (0.0, index * 2.0),
            (0.0, index * 2.0 + 0.5),
        )
        for index in range(1000)
    ]

    planarized = _planarize_line_segments(
        segments,
        tolerance=1e-9,
        max_pair_checks=4,
    )

    assert len(planarized) == len(segments)
    assert not any(segment["planarization_capped"] for segment in planarized)
    assert not any(segment["planarized"] for segment in planarized)


def test_microscale_oblique_crossing_is_split_with_picometer_tolerance():
    segments = [
        _raw_line("UP", (0.0, 0.0), (1e-6, 1e-6)),
        _raw_line("DOWN", (0.0, 1e-6), (1e-6, 0.0)),
    ]

    planarized = _planarize_line_segments(
        segments,
        tolerance=1e-12,
        max_pair_checks=10,
    )

    assert len(planarized) == 4
    assert not any(segment["planarization_capped"] for segment in planarized)
    assert {segment["source_segment_id"] for segment in planarized} == {
        "UP",
        "DOWN",
    }
    assert all(segment["planarized"] for segment in planarized)
    intersection = (5e-7, 5e-7)
    assert sum(
        endpoint == pytest.approx(intersection, abs=1e-18)
        for segment in planarized
        for endpoint in (segment["start"], segment["end"])
    ) == 4


def test_external_two_tangent_v_preserves_d_and_adds_real_tangent_face(tmp_path):
    db = _make_db(tmp_path)
    _insert_upper_d(db)
    baseline_id = infer_cross_entity_closed_profiles(db)[0]["profile_id"]
    tangent = 10.0 / math.sqrt(2.0)
    apex = 10.0 * math.sqrt(2.0)
    _insert_line(db, "TANGENT_LEFT", (-tangent, tangent), (0.0, apex))
    _insert_line(db, "TANGENT_RIGHT", (0.0, apex), (tangent, tangent))

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 2
    by_handles = {
        frozenset(profile["entity_handles"]): profile for profile in profiles
    }
    assert by_handles[frozenset({"ARC", "BASE"})]["area"] == pytest.approx(
        50.0 * math.pi, rel=5e-4
    )
    assert by_handles[frozenset({"ARC", "BASE"})]["profile_id"] == baseline_id
    assert by_handles[frozenset({
        "ARC", "TANGENT_LEFT", "TANGENT_RIGHT",
    })]["area"] == pytest.approx(100.0 - 25.0 * math.pi, rel=5e-4)


def test_external_tangent_path_with_internal_dead_end_keeps_face(tmp_path):
    db = _make_db(tmp_path)
    _insert_upper_d(db)
    tangent = 10.0 / math.sqrt(2.0)
    apex = 10.0 * math.sqrt(2.0)
    left_contact = (-tangent, tangent)
    _insert_line(db, "TANGENT_LEFT", left_contact, (0.0, apex))
    _insert_line(db, "TANGENT_RIGHT", (0.0, apex), (tangent, tangent))
    _insert_line(db, "INTERIOR_DEAD_END", left_contact, (0.0, 5.0))

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 2
    assert {frozenset(profile["entity_handles"]) for profile in profiles} == {
        frozenset({"ARC", "BASE"}),
        frozenset({"ARC", "TANGENT_LEFT", "TANGENT_RIGHT"}),
    }
    assert all(
        "INTERIOR_DEAD_END" not in profile["entity_handles"]
        for profile in profiles
    )


def test_severely_wrong_authored_arc_endpoints_do_not_create_face(tmp_path):
    db = _make_db(tmp_path)
    authored_start = (10.0, 2.0)
    authored_end = (-10.0, -2.0)
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
            "start_point": [*authored_start, 0.0],
            "end_point": [*authored_end, 0.0],
        },
        bbox=(-10.0, -2.0, 10.0, 10.0),
        topology_detail="full",
    )

    assert infer_cross_entity_closed_profiles(db) == []


def test_shallow_arc_endpoint_limit_tracks_visible_sagitta(tmp_path):
    db = _make_db(tmp_path)
    center_y = -1000.0
    radius = math.sqrt(1000.0 ** 2 + 5.0 ** 2)
    start_parameter = math.atan2(1000.0, 5.0)
    end_parameter = math.atan2(1000.0, -5.0)
    authored_start = (5.0, 1.0)
    authored_end = (-5.0, 1.0)
    _insert_line(db, "CHORD", authored_end, authored_start)
    db.upsert_entity(
        "SHALLOW",
        "Arc",
        "AcDbArc",
        layer="OUTLINE",
        geometry={
            "center": [0.0, center_y, 0.0],
            "radius": radius,
            "start_parameter": start_parameter,
            "end_parameter": end_parameter,
            "parameter_unit": "radian",
            "start_point": [*authored_start, 0.0],
            "end_point": [*authored_end, 0.0],
        },
        topology_detail="full",
    )

    assert infer_cross_entity_closed_profiles(db) == []
