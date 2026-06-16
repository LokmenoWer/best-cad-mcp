from src.cad_database import CADDatabase
from src.cad_understanding.validators import validate_geometry


def make_db(tmp_path):
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.configure_context(workspace_root=str(tmp_path), conversation_id="conv", thread_id="thread")
    return db


def test_validate_geometry_detects_basic_issues(tmp_path):
    db = make_db(tmp_path)
    db.upsert_entity(
        "Z1",
        "Line",
        "AcDbLine",
        geometry={"start": [0, 0, 0], "end": [0, 0, 0]},
        bbox=(0, 0, 0, 0),
    )
    for handle in ("D1", "D2"):
        db.upsert_entity(
            handle,
            "Line",
            "AcDbLine",
            geometry={"start": [1, 1, 0], "end": [5, 1, 0]},
            bbox=(1, 1, 5, 1),
        )
    db.upsert_entity(
        "P1",
        "Polyline",
        "AcDbPolyline",
        geometry={"vertices": [[0, 0, 0], [1, 0, 0], [1, 1, 0]], "closed": False},
        bbox=(0, 0, 1, 1),
    )

    result = validate_geometry(
        checks=["zero_length_lines", "duplicate_entities", "unclosed_polylines"],
        database=db,
    )
    issues = result["data"]["validation_report"]["issues"]
    types = {issue["issue_type"] for issue in issues}

    assert result["ok"] is True
    assert {"zero_length_lines", "duplicate_entities", "unclosed_polylines"}.issubset(types)
