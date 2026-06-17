from src.cad_database import CADDatabase
from src.cad_understanding.semantic_graph import (
    detect_semantic_objects,
    find_semantic_objects,
    get_semantic_graph,
)


def make_db(tmp_path):
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.configure_context(workspace_root=str(tmp_path), conversation_id="conv", thread_id="thread")
    return db


def populate_semantic_fixture(db):
    db.upsert_entity(
        "P1",
        "Polyline",
        "AcDbPolyline",
        layer="OUTLINE",
        geometry={"vertices": [[0, 0, 0], [100, 0, 0], [100, 60, 0], [0, 60, 0]], "closed": True},
        bbox=(0, 0, 100, 60),
        topology_detail="full",
    )
    for i, x in enumerate([20, 50, 80], start=1):
        db.upsert_entity(
            f"C{i}",
            "Circle",
            "AcDbCircle",
            layer="HOLES",
            geometry={"center": [x, 30, 0], "radius": 3},
            bbox=(x - 3, 27, x + 3, 33),
            topology_detail="full",
        )
    db.upsert_entity(
        "T1",
        "Text",
        "AcDbText",
        layer="TEXT",
        geometry={"text": "PLATE A"},
        bbox=(5, 5, 20, 10),
    )
    db.upsert_entity(
        "B1",
        "BlockReference",
        "AcDbBlockReference",
        layer="PARTS",
        geometry={"block_name": "PIN"},
        bbox=(40, 20, 45, 25),
    )


def test_detect_semantic_objects_rule_based_fixture(tmp_path):
    db = make_db(tmp_path)
    populate_semantic_fixture(db)

    result = detect_semantic_objects("mechanical", database=db)
    graph = get_semantic_graph(database=db)["data"]
    types = {obj["object_type"] for obj in graph["semantic_objects"]}

    assert result["ok"] is True
    assert "closed_profile" in types
    assert "hole" in types
    assert "bolt_circle_pattern" in types or "hole_pattern" in types
    assert "text_annotation" in types
    assert "block_instance" in types
    assert graph["semantic_relations"]


def test_detect_architecture_electrical_and_drafting_candidates(tmp_path):
    db = make_db(tmp_path)
    db.upsert_entity(
        "W1",
        "Line",
        "AcDbLine",
        layer="A-WALL",
        geometry={"start": [0, 0, 0], "end": [20, 0, 0]},
        bbox=(0, 0, 20, 0),
    )
    db.upsert_entity(
        "DR1",
        "BlockReference",
        "AcDbBlockReference",
        layer="A-DOOR",
        geometry={"block_name": "DOOR_900"},
        bbox=(4, -1, 6, 1),
    )
    arch = detect_semantic_objects("architecture", database=db)
    arch_types = {obj["object_type"] for obj in arch["data"]["semantic_objects"]}

    assert "wall_candidate" in arch_types
    assert "door" in arch_types

    db.upsert_entity(
        "E1",
        "Polyline",
        "AcDbPolyline",
        layer="E-WIRE",
        geometry={"vertices": [[0, 5, 0], [20, 5, 0]], "closed": False},
        bbox=(0, 5, 20, 5),
    )
    db.upsert_entity(
        "TB1",
        "BlockReference",
        "AcDbBlockReference",
        layer="E-DEVICE",
        geometry={"block_name": "TERMINAL_BLOCK"},
        bbox=(10, 4, 12, 6),
    )
    electrical = detect_semantic_objects("electrical", database=db)
    electrical_types = {obj["object_type"] for obj in electrical["data"]["semantic_objects"]}

    assert "wire" in electrical_types
    assert "terminal" in electrical_types

    db.upsert_entity(
        "T1",
        "Text",
        "AcDbText",
        layer="TITLE",
        geometry={"text": "REVISION TABLE"},
        bbox=(0, -10, 20, -8),
    )
    drafting = detect_semantic_objects("drafting", database=db)
    drafting_types = {obj["object_type"] for obj in drafting["data"]["semantic_objects"]}

    assert "revision_table" in drafting_types


def test_find_semantic_objects_filters_by_handle_domain_and_confidence(tmp_path):
    db = make_db(tmp_path)
    populate_semantic_fixture(db)
    detect_semantic_objects("mechanical", database=db)

    result = find_semantic_objects(
        object_type="hole",
        handle="C1",
        domain="mechanical",
        confidence_threshold=0.5,
        database=db,
    )
    objects = result["data"]["semantic_objects"]

    assert result["ok"]
    assert objects
    assert objects[0]["entity_handles"] == ["C1"]
