import math

from src.cad_database import CADDatabase
from src.cad_understanding.drawing_graph import infer_cross_entity_closed_profiles


def _make_db(tmp_path):
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="conv",
        thread_id="thread",
    )
    return db


def _insert_shallow_arc(db, handle, center_y):
    center_offset = abs(center_y)
    radius = math.hypot(center_offset, 5.0)
    start_parameter = math.atan2(center_offset, 5.0)
    end_parameter = math.atan2(center_offset, -5.0)
    db.upsert_entity(
        handle,
        "Arc",
        "AcDbArc",
        layer="OUTLINE",
        geometry={
            "center": [0.0, center_y, 0.0],
            "radius": radius,
            "start_parameter": start_parameter,
            "end_parameter": end_parameter,
            "parameter_unit": "radian",
        },
        topology_detail="full",
    )
    sweep = end_parameter - start_parameter
    return 0.5 * radius * radius * (sweep - math.sin(sweep))


def test_shallow_lens_uses_bounded_adaptive_curve_sampling(tmp_path):
    db = _make_db(tmp_path)
    outer_segment_area = _insert_shallow_arc(db, "OUTER", -1000.0)
    inner_segment_area = _insert_shallow_arc(db, "INNER", -2000.0)
    expected_area = abs(outer_segment_area - inner_segment_area)

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 1
    profile = profiles[0]
    assert set(profile["entity_handles"]) == {"OUTER", "INNER"}
    assert profile["boundary_edge_count"] == 2
    assert profile["curve_count"] == 2
    assert math.isclose(profile["area"], expected_area, rel_tol=0.002)

    curve_members = profile["member_primitives"]
    assert len(curve_members) == 2
    assert all(member["sampling_method"] == "analytic_adaptive" for member in curve_members)
    assert all(16 <= member["sampling_segment_count"] <= 2048 for member in curve_members)
    assert all(member["sampling_error_target"] > 0.0 for member in curve_members)
    assert all(member["sampling_error_bound"] > 0.0 for member in curve_members)
    assert all(
        member["sampling_error_bound"]
        <= member["sampling_error_target"] * (1.0 + 1e-9)
        for member in curve_members
    )
    assert math.isclose(
        profile["max_curve_sampling_error"],
        max(member["sampling_error_bound"] for member in curve_members),
        rel_tol=1e-12,
    )
    assert profile["topology_evidence"]["curve_sampling_capped"] is False
