import contextvars
import json

from src.cad_database import CADDatabase
from src.cad_understanding.ir_builder import build_drawing_ir


def make_db(tmp_path):
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="conv",
        thread_id="thread",
        drawing_name="unit.dwg",
        drawing_path=str(tmp_path / "unit.dwg"),
    )
    return db


def test_build_drawing_ir_empty_database(tmp_path):
    db = make_db(tmp_path)

    drawing_ir = build_drawing_ir(database=db)

    assert drawing_ir["schema_version"] == "cad-ir/v2"
    assert drawing_ir["drawing"]["counts"]["entities"] == 0
    assert drawing_ir["sections"]["entities"]["items"] == []
    assert drawing_ir["sections"]["semantics"]["objects"] == []
    assert drawing_ir["quality"]["scan_state"] == "empty"
    assert "scan_all_entities" in drawing_ir["quality"]["recommended_next_tools"]
    json.dumps(drawing_ir)


def test_build_drawing_ir_exposes_native_handles_not_scoped_keys(tmp_path):
    db = make_db(tmp_path)
    db.upsert_entity(
        "H1",
        "Line",
        "AcDbLine",
        layer="A-WALL",
        geometry={"start": [0, 0, 0], "end": [10, 0, 0], "length": 10},
        bbox=(0, 0, 10, 0),
        topology_detail="full",
    )

    drawing_ir = build_drawing_ir(database=db)
    entity = drawing_ir["sections"]["entities"]["items"][0]

    assert entity["handle"] == "H1"
    assert "native_handle" not in entity
    assert "geometry" not in entity
    assert "properties" not in entity
    assert "workspace_id" not in entity
    assert "drawing_id" not in entity
    assert entity["topology"]["detail"] == "full"
    assert entity["flags"]["has_topology"] is True
    assert drawing_ir["sections"]["topology"]["primitives"]
    json.dumps(drawing_ir)

    raw_ir = build_drawing_ir(database=db, include_raw=True)
    raw_entity = raw_ir["sections"]["entities"]["items"][0]
    assert raw_entity["geometry"]["length"] == 10
    assert raw_entity["properties"] == {}


def test_build_drawing_ir_filters_sections_and_reports_truncation(tmp_path):
    db = make_db(tmp_path)
    db.upsert_entity(
        "H1",
        "Line",
        "AcDbLine",
        geometry={"start": [0, 0, 0], "end": [1, 0, 0]},
        bbox=(0, 0, 1, 0),
    )
    db.upsert_entity(
        "H2",
        "Line",
        "AcDbLine",
        geometry={"start": [2, 0, 0], "end": [3, 0, 0]},
        bbox=(2, 0, 3, 0),
    )

    drawing_ir = build_drawing_ir(
        database=db,
        sections=["entities"],
        entity_limit=1,
    )

    assert drawing_ir["manifest"]["sections"] == ["entities"]
    assert set(drawing_ir["sections"]) == {"entities"}
    assert drawing_ir["sections"]["entities"]["total"] == 2
    assert drawing_ir["sections"]["entities"]["count"] == 1
    assert drawing_ir["sections"]["entities"]["truncated"] is True
    assert drawing_ir["quality"]["coverage"]["entities"]["truncated"] is True
    assert any("truncated" in warning for warning in drawing_ir["manifest"]["warnings"])


def test_build_drawing_ir_quality_reports_missing_bbox_and_summary_topology(tmp_path):
    db = make_db(tmp_path)
    db.upsert_entity(
        "S1",
        "Line",
        "AcDbLine",
        geometry={"start": [0, 0, 0], "end": [5, 0, 0]},
        bbox=None,
        derive_bbox=False,
        topology_detail="summary",
    )

    drawing_ir = build_drawing_ir(database=db)
    issue_types = {
        issue["issue_type"] for issue in drawing_ir["quality"]["issues"]
    }
    entity = drawing_ir["sections"]["entities"]["items"][0]

    assert "missing_bbox" in issue_types
    assert "summary_only_topology" in issue_types
    assert entity["flags"]["has_bbox"] is False
    assert entity["topology"]["detail"] == "summary"
    assert drawing_ir["quality"]["coverage"]["topology"]["detail_level"] == "summary"


def test_database_context_survives_fresh_async_task_context(tmp_path):
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.upsert_entity(
        "OLD",
        "OldLine",
        "AcDbLine",
        geometry={"start": [0, 0, 0], "end": [1, 0, 0]},
        bbox=(0, 0, 1, 0),
    )

    db.activate_drawing("Drawing4.dwg", r"C:\Users\qxqxx\Documents")
    db.upsert_entity(
        "NEW",
        "NewLine",
        "AcDbLine",
        geometry={"start": [10, 0, 0], "end": [20, 0, 0]},
        bbox=(10, 0, 20, 0),
    )

    fresh_task = contextvars.Context()
    drawing_ir = fresh_task.run(lambda: build_drawing_ir(database=db))

    assert drawing_ir["drawing"]["name"] == "Drawing4.dwg"
    assert drawing_ir["drawing"]["counts"]["entities"] == 1
    assert [
        entity["handle"]
        for entity in drawing_ir["sections"]["entities"]["items"]
    ] == ["NEW"]
