from src.cad_database import CADDatabase
from src.cad_understanding.drawing_graph import (
    _canonical_ring,
    infer_cross_entity_closed_profiles,
)


def _make_db(tmp_path) -> CADDatabase:
    db = CADDatabase(str(tmp_path / "polygon-safety.db"))
    db.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="polygon-safety",
        thread_id="polygon-safety",
    )
    return db


def _insert_line(db: CADDatabase, handle: str, start, end) -> None:
    db.upsert_entity(
        handle,
        "Line",
        "AcDbLine",
        layer="OUTLINE",
        geometry={
            "start": [float(start[0]), float(start[1]), 0.0],
            "end": [float(end[0]), float(end[1]), 0.0],
        },
        bbox=(
            min(float(start[0]), float(end[0])),
            min(float(start[1]), float(end[1])),
            max(float(start[0]), float(end[0])),
            max(float(start[1]), float(end[1])),
        ),
        topology_detail="full",
    )


def _insert_polygon_edges(db: CADDatabase, points) -> set[str]:
    handles = {f"EDGE_{index}" for index in range(len(points))}
    for index, handle in enumerate(sorted(handles, key=lambda value: int(value.split("_")[1]))):
        _insert_line(db, handle, points[index], points[(index + 1) % len(points)])
    return handles


def test_valid_concave_polygon_with_long_diagonal_is_inferred(tmp_path):
    points = [
        (0, 0),
        (10, 0),
        (15, -5),
        (20, 0),
        (5, 5),
        (0, 5),
    ]
    db = _make_db(tmp_path)
    handles = _insert_polygon_edges(db, points)

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 1
    assert set(profiles[0]["entity_handles"]) == handles
    assert profiles[0]["area"] > 0.0


def test_valid_large_scale_concave_polygon_is_inferred(tmp_path):
    points = [
        (0, 0),
        (1, 1),
        (0, 1),
        (-1000, 1),
        (-1000, -10),
        (0, -10),
    ]
    db = _make_db(tmp_path)
    handles = _insert_polygon_edges(db, points)

    profiles = infer_cross_entity_closed_profiles(db)

    assert len(profiles) == 1
    assert set(profiles[0]["entity_handles"]) == handles
    assert profiles[0]["area"] > 0.0


def test_self_intersecting_sampled_spline_and_closing_chord_is_rejected(tmp_path):
    db = _make_db(tmp_path)
    db.upsert_entity(
        "BOW_TIE",
        "Spline",
        "AcDbSpline",
        layer="OUTLINE",
        geometry={
            "fit_points": [
                [0.0, 0.0, 0.0],
                [10.0, 10.0, 0.0],
                [0.0, 10.0, 0.0],
                [10.0, 0.0, 0.0],
            ],
            "degree": 3,
        },
        topology_detail="full",
    )
    _insert_line(db, "CHORD", (10, 0), (0, 0))

    assert infer_cross_entity_closed_profiles(db) == []


def test_canonical_ring_is_stable_for_large_rotation_and_reversal():
    ring = [
        (float(index), float((index * 7919) % 10007))
        for index in range(4096)
    ]
    split = 1379
    rotated = ring[split:] + ring[:split]
    reversed_rotated = list(reversed(rotated))

    canonical = _canonical_ring(ring, quantum=1e-6)

    assert _canonical_ring(rotated, quantum=1e-6) == canonical
    assert _canonical_ring(reversed_rotated, quantum=1e-6) == canonical
