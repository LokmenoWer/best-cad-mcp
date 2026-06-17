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

    assert drawing_ir["entity_count"] == 0
    assert drawing_ir["entities"] == []
    assert drawing_ir["semantic_objects"] == []
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
    entity = drawing_ir["entities"][0]

    assert entity["handle"] == "H1"
    assert entity["native_handle"] == "H1"
    assert "workspace_id" not in entity
    assert "drawing_id" not in entity
    assert drawing_ir["topology"]["primitives"]
    json.dumps(drawing_ir)


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

    assert drawing_ir["drawing_name"] == "Drawing4.dwg"
    assert drawing_ir["entity_count"] == 1
    assert [entity["handle"] for entity in drawing_ir["entities"]] == ["NEW"]
