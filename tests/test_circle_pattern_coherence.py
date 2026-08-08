from pathlib import Path

from src.cad_database import CADDatabase
from src.cad_understanding.semantic_graph import detect_semantic_objects


def _database(tmp_path: Path) -> CADDatabase:
    database = CADDatabase(str(tmp_path / "cad.db"))
    database.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="circle-patterns",
        thread_id="coherence",
        drawing_name="patterns.dwg",
        drawing_path=str(tmp_path / "patterns.dwg"),
    )
    return database


def _circle(database: CADDatabase, handle: str, center, radius: float = 5.0):
    x, y = center
    database.upsert_entity(
        handle,
        "Circle",
        "AcDbCircle",
        layer="HOLES",
        geometry={"center": [x, y, 0.0], "radius": radius},
        bbox=(x - radius, y - radius, x + radius, y + radius),
        topology_detail="full",
    )


def _patterns(result):
    return [
        item for item in result["data"]["semantic_objects"]
        if item["object_type"] in {"bolt_circle_pattern", "hole_pattern"}
    ]


def test_remote_equal_radius_outlier_does_not_form_global_pattern(tmp_path):
    database = _database(tmp_path)
    _circle(database, "A", (0.0, 0.0))
    _circle(database, "B", (0.0, 20.0))
    _circle(database, "REMOTE", (1000.0, 0.0))

    result = detect_semantic_objects("mechanical", database=database)

    assert result["ok"], result
    assert _patterns(result) == []


def test_separate_regular_rows_become_distinct_local_patterns(tmp_path):
    database = _database(tmp_path)
    first = {"A0", "A1", "A2"}
    second = {"B0", "B1", "B2"}
    for index, x in enumerate((0.0, 20.0, 40.0)):
        _circle(database, f"A{index}", (x, 0.0))
        _circle(database, f"B{index}", (x + 500.0, 0.0))

    result = detect_semantic_objects("mechanical", database=database)
    groups = {
        frozenset(item["entity_handles"])
        for item in _patterns(result)
    }

    assert groups == {frozenset(first), frozenset(second)}


def test_regular_bolt_circle_retains_radial_pattern(tmp_path):
    database = _database(tmp_path)
    expected = {"N", "E", "S", "W"}
    for handle, center in {
        "N": (0.0, 40.0),
        "E": (40.0, 0.0),
        "S": (0.0, -40.0),
        "W": (-40.0, 0.0),
    }.items():
        _circle(database, handle, center)

    result = detect_semantic_objects("mechanical", database=database)
    pattern = next(
        item for item in _patterns(result)
        if set(item["entity_handles"]) == expected
    )

    assert pattern["object_type"] == "bolt_circle_pattern"
    assert pattern["properties"]["radial_deviation_ratio"] <= 1e-9
