from src.cad_database import CADDatabase
from src.cad_understanding.semantic_graph import detect_semantic_objects, get_semantic_graph


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
