import math

import pytest

from src.cad_database import CADDatabase
from src.cad_understanding.drawing_graph import infer_cross_entity_closed_profiles


def _make_db(tmp_path, name="extreme"):
    db = CADDatabase(str(tmp_path / f"{name}.db"))
    db.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="curve-extreme",
        thread_id=name,
    )
    return db


def _line(db, handle, start, end):
    start = [*start, 0.0] if len(start) == 2 else list(start)
    end = [*end, 0.0] if len(end) == 2 else list(end)
    db.upsert_entity(
        handle,
        "Line",
        "AcDbLine",
        layer="OUTLINE",
        geometry={"start": start, "end": end},
        topology_detail="full",
    )


def _circle(db, handle="C", center=(0.0, 0.0, 0.0), radius=10.0):
    db.upsert_entity(
        handle,
        "Circle",
        "AcDbCircle",
        layer="OUTLINE",
        geometry={"center": list(center), "radius": radius},
        topology_detail="full",
    )


def _ellipse(db, handle, seam, minor=(0.0, 5.0, 0.0), z=0.0):
    db.upsert_entity(
        handle,
        "Ellipse",
        "AcDbEllipse",
        layer="OUTLINE",
        geometry={
            "center": [0.0, 0.0, z],
            "major_axis": [10.0, 0.0, 0.0],
            "minor_axis": list(minor),
            "radius_ratio": 0.5,
            "is_arc": False,
            "start_parameter": seam,
            "end_parameter": seam + 2.0 * math.pi,
            "parameter_unit": "radian",
        },
        topology_detail="full",
    )


def _ids_by_vertical_side(db, tolerance=1e-6):
    return {
        "upper" if (profile["bbox"][1] + profile["bbox"][3]) > 0.0 else "lower": (
            profile["profile_id"]
        )
        for profile in infer_cross_entity_closed_profiles(
            db, tolerance=tolerance
        )
    }


def test_shallow_large_radius_arc_keeps_resolved_cap(tmp_path):
    db = _make_db(tmp_path)
    depth = 1e6
    half_chord = 5.0
    radius = math.hypot(depth, half_chord)
    start = math.atan2(depth, half_chord)
    end = math.atan2(depth, -half_chord)
    db.upsert_entity(
        "ARC",
        "Arc",
        "AcDbArc",
        layer="OUTLINE",
        geometry={
            "center": [0.0, -depth, 0.0],
            "radius": radius,
            "start_parameter": start,
            "end_parameter": end,
            "parameter_unit": "radian",
            "start_point": [half_chord, 0.0, 0.0],
            "end_point": [-half_chord, 0.0, 0.0],
        },
        topology_detail="full",
    )
    _line(db, "CHORD", (-half_chord, 0.0), (half_chord, 0.0))

    profiles = infer_cross_entity_closed_profiles(db, tolerance=1e-12)

    # Stable leading term of the circular-segment series. The direct
    # r^2*acos(d/r)-d*a expression catastrophically cancels at this scale.
    expected_area = 2.0 * half_chord ** 3 / (3.0 * depth)
    assert len(profiles) == 1
    assert profiles[0]["area"] == pytest.approx(expected_area, rel=2e-3)
    assert profiles[0]["max_curve_sampling_error"] < 2e-8


def test_profile_ids_ignore_source_tails_direction_and_rigid_move(tmp_path):
    def build(path, shift, tail, reverse=False):
        path.mkdir()
        db = _make_db(path)
        _circle(db, center=(shift, 0.0, 0.0))
        endpoints = ((shift - tail, 0.0), (shift + tail, 0.0))
        if reverse:
            endpoints = tuple(reversed(endpoints))
        _line(db, "DIA", *endpoints)
        return _ids_by_vertical_side(db)

    baseline = build(tmp_path / "baseline", 0.0, 20.0)
    assert build(tmp_path / "tail", 0.0, 200.0, True) == baseline
    assert build(tmp_path / "move", 1e9, 200.0) == baseline


def test_profile_ids_ignore_full_ellipse_seam_and_traversal(tmp_path):
    def build(path, seam, minor):
        path.mkdir()
        db = _make_db(path)
        _ellipse(db, "ELL", seam, minor)
        _line(db, "DIA", (-20.0, 0.0), (20.0, 0.0))
        return _ids_by_vertical_side(db)

    baseline = build(tmp_path / "seam0", 0.0, (0.0, 5.0, 0.0))
    assert baseline["upper"] != baseline["lower"]
    assert build(
        tmp_path / "seam1", math.pi / 3.0, (0.0, 5.0, 0.0)
    ) == baseline
    assert build(
        tmp_path / "reverse", math.pi, (0.0, -5.0, 0.0)
    ) == baseline


def test_complementary_semicircle_entities_form_one_circle(tmp_path):
    db = _make_db(tmp_path)
    for handle, start, end in (
        ("UP", 0.0, math.pi),
        ("LOW", math.pi, 2.0 * math.pi),
    ):
        db.upsert_entity(
            handle,
            "Arc",
            "AcDbArc",
            layer="OUTLINE",
            geometry={
                "center": [0.0, 0.0, 0.0],
                "radius": 10.0,
                "start_parameter": start,
                "end_parameter": end,
                "parameter_unit": "radian",
            },
            topology_detail="full",
        )

    profiles = infer_cross_entity_closed_profiles(db, tolerance=1e-6)

    assert len(profiles) == 1
    assert set(profiles[0]["entity_handles"]) == {"UP", "LOW"}
    assert profiles[0]["area"] == pytest.approx(100.0 * math.pi, rel=5e-4)


def test_duplicate_full_ellipses_coalesce_across_seams_but_not_planes(tmp_path):
    db = _make_db(tmp_path)
    _ellipse(db, "E1", 0.0)
    _ellipse(db, "E2", math.pi / 3.0)
    _ellipse(db, "E10", 0.0, z=10.0)
    _line(db, "DIA", (-20.0, 0.0), (20.0, 0.0))

    profiles = infer_cross_entity_closed_profiles(db, tolerance=1e-6)

    assert len(profiles) == 2
    assert all(
        set(profile["entity_handles"]) == {"DIA", "E1", "E2"}
        for profile in profiles
    )
    assert all("E10" not in profile["entity_handles"] for profile in profiles)


def test_equivalent_circle_and_round_ellipse_coalesce_geometrically(tmp_path):
    db = _make_db(tmp_path)
    _circle(db, "CIRCLE")
    _ellipse(db, "ROUND_ELLIPSE", math.pi / 3.0, minor=(0.0, 10.0, 0.0))
    _line(db, "DIA", (-20.0, 0.0), (20.0, 0.0))

    profiles = infer_cross_entity_closed_profiles(db, tolerance=1e-6)

    assert len(profiles) == 2
    assert all(
        set(profile["entity_handles"])
        == {"CIRCLE", "ROUND_ELLIPSE", "DIA"}
        for profile in profiles
    )


def test_equivalent_open_arc_and_round_ellipse_arc_coalesce(tmp_path):
    db = _make_db(tmp_path)
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
        topology_detail="full",
    )
    db.upsert_entity(
        "ELLIPSE_ARC",
        "Ellipse",
        "AcDbEllipse",
        layer="OUTLINE",
        geometry={
            "center": [0.0, 0.0, 0.0],
            "major_axis": [10.0, 0.0, 0.0],
            "minor_axis": [0.0, 10.0, 0.0],
            "radius_ratio": 1.0,
            "is_arc": True,
            "start_parameter": 0.0,
            "end_parameter": math.pi,
            "parameter_unit": "radian",
        },
        topology_detail="full",
    )
    _line(db, "DIA", (-10.0, 0.0), (10.0, 0.0))

    profiles = infer_cross_entity_closed_profiles(db, tolerance=1e-6)

    assert len(profiles) == 1
    assert set(profiles[0]["entity_handles"]) == {
        "ARC", "ELLIPSE_ARC", "DIA",
    }


def test_resolvably_distinct_near_conics_are_not_coalesced(tmp_path):
    db = _make_db(tmp_path)
    _circle(db, "C0", radius=10.0)
    _circle(db, "C1", radius=10.0 + 1e-5)
    _line(db, "DIA", (-20.0, 0.0), (20.0, 0.0))

    assert infer_cross_entity_closed_profiles(db, tolerance=1e-3) == []


def test_intersecting_curve_domains_fail_closed_before_face_promotion(tmp_path):
    db = _make_db(tmp_path)
    for handle, center_x in (("LEFT_ARC", -5.0), ("RIGHT_ARC", 5.0)):
        db.upsert_entity(
            handle,
            "Arc",
            "AcDbArc",
            layer="OUTLINE",
            geometry={
                "center": [center_x, 0.0, 0.0],
                "radius": 10.0,
                "start_parameter": 0.0,
                "end_parameter": math.pi,
                "parameter_unit": "radian",
            },
            topology_detail="full",
        )
    _line(db, "LEFT_BASE", (-15.0, 0.0), (5.0, 0.0))
    _line(db, "RIGHT_BASE", (-5.0, 0.0), (15.0, 0.0))

    assert infer_cross_entity_closed_profiles(db, tolerance=1e-6) == []


def test_below_area_resolution_tangent_cap_fails_atomically(tmp_path):
    db = _make_db(tmp_path)
    _circle(db)
    _line(db, "DIA", (-20.0, 0.0), (20.0, 0.0))
    _line(db, "NEAR", (-20.0, 9.999999), (20.0, 9.999999))

    assert infer_cross_entity_closed_profiles(db, tolerance=1e-3) == []


def test_nonfinite_geometry_is_rejected_and_arc_normalization_is_bounded(tmp_path):
    invalid = _make_db(tmp_path, "invalid")
    _circle(invalid)
    _line(
        invalid,
        "BAD_Z",
        (-20.0, 0.0, float("nan")),
        (20.0, 0.0, float("nan")),
    )
    assert infer_cross_entity_closed_profiles(invalid) == []

    sweep = _make_db(tmp_path, "sweep")
    sweep.upsert_entity(
        "BAD_ARC",
        "Arc",
        "AcDbArc",
        geometry={
            "center": [0.0, 0.0, 0.0],
            "radius": 10.0,
            "start_parameter": 1e12,
            "end_parameter": 0.0,
            "parameter_unit": "radian",
        },
        topology_detail="full",
    )
    assert sweep.get_entity("BAD_ARC") is not None


def test_analytic_extrema_make_profile_bbox_conservative(tmp_path):
    db = _make_db(tmp_path)
    _circle(db)
    _line(db, "LOW", (-20.0, -3.0), (20.0, -3.0))
    _line(db, "HIGH", (-20.0, 4.0), (20.0, 4.0))

    middle = next(
        profile for profile in infer_cross_entity_closed_profiles(db)
        if profile["bbox"][1] == pytest.approx(-3.0)
        and profile["bbox"][3] == pytest.approx(4.0)
    )

    assert middle["bbox"][0] == pytest.approx(-10.0, abs=1e-12)
    assert middle["bbox"][2] == pytest.approx(10.0, abs=1e-12)
