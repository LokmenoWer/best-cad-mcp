from src.cad_database import CADDatabase
from src.cad_understanding.plan import dry_run_cad_plan, validate_cad_plan
from src.cad_understanding.validators import propose_repair_plan, validate_geometry


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


def test_validate_geometry_uses_bbox_when_scanned_geometry_has_zero_placeholders(tmp_path):
    db = make_db(tmp_path)
    db.upsert_entity(
        "L1",
        "Line",
        "AcDbLine",
        geometry={"length": 0.0},
        bbox=(0, 20, 140, 20),
    )
    db.upsert_entity(
        "C1",
        "Circle",
        "AcDbCircle",
        geometry={"radius": 0.0},
        bbox=(50, -95, 90, -55),
    )
    db.upsert_entity(
        "C2",
        "Circle",
        "AcDbCircle",
        geometry={"radius": 0.0},
        bbox=(60, -85, 80, -65),
    )

    result = validate_geometry(
        checks=["zero_length_lines", "duplicate_entities"],
        database=db,
    )
    issues = result["data"]["validation_report"]["issues"]

    assert issues == []


def test_proposed_repair_plan_can_be_validated_and_dry_run(tmp_path):
    db = make_db(tmp_path)
    db.upsert_entity(
        "Z1",
        "Line",
        "AcDbLine",
        geometry={"start": [0, 0, 0], "end": [0, 0, 0]},
        bbox=(0, 0, 0, 0),
    )

    validation = validate_geometry(checks=["zero_length_lines"], database=db)
    issue_id = validation["data"]["validation_report"]["issues"][0]["issue_id"]
    repair = propose_repair_plan([issue_id], database=db)
    plan = repair["data"]["plan"]
    plan_validation = validate_cad_plan(plan)
    dry_run = dry_run_cad_plan(plan)

    assert repair["ok"] is True
    assert plan["steps"][0]["op"] == "delete_entity"
    assert plan_validation["ok"] is True
    assert dry_run["ok"] is True
    assert dry_run["handles"] == ["Z1"]
