from src.cad_database import CADDatabase
from src.cad_understanding.constraints import extract_drawing_constraints, get_constraints


def make_db(tmp_path):
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.configure_context(workspace_root=str(tmp_path), conversation_id="conv", thread_id="thread")
    return db


def test_extract_constraints_for_basic_geometry(tmp_path):
    db = make_db(tmp_path)
    db.upsert_entity(
        "C1",
        "Circle",
        "AcDbCircle",
        geometry={"center": [0, 0, 0], "radius": 5},
        bbox=(-5, -5, 5, 5),
    )
    db.upsert_entity(
        "L1",
        "Line",
        "AcDbLine",
        geometry={"start": [0, 0, 0], "end": [10, 0, 0]},
        bbox=(0, 0, 10, 0),
    )
    db.upsert_entity(
        "L2",
        "Line",
        "AcDbLine",
        geometry={"start": [0, 5, 0], "end": [10, 5, 0]},
        bbox=(0, 5, 10, 5),
    )
    db.upsert_entity(
        "P1",
        "Polyline",
        "AcDbPolyline",
        geometry={"vertices": [[0, 0, 0], [1, 0, 0], [1, 1, 0]], "closed": True},
        bbox=(0, 0, 1, 1),
    )

    result = extract_drawing_constraints(database=db)
    constraints = get_constraints(database=db)["data"]["constraints"]
    types = {item["constraint_type"] for item in constraints}

    assert result["ok"] is True
    assert {"radius", "diameter", "distance", "parallel", "closed_profile"}.issubset(types)
    assert all(item["status"] in {"satisfied", "unknown"} for item in constraints)
