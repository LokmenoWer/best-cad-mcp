from pathlib import Path

from src.cad_database import CADDatabase
from src.cad_understanding.analysis import summarize_drawing
from src.cad_understanding.constraints import extract_drawing_constraints
from src.cad_understanding.ir_builder import build_drawing_ir
from src.cad_understanding.plan import dry_run_cad_plan, validate_cad_plan
from src.cad_understanding.resources import get_cad_resource, list_cad_resources
from src.cad_understanding.semantic_graph import detect_semantic_objects
from src.cad_understanding.validators import propose_repair_plan, validate_geometry
from src.cad_understanding.view_grounding import (
    export_view_image_with_mapping,
    get_visible_entities_in_view,
    ground_vlm_overlay_id,
    ground_vlm_region,
)


def make_db(tmp_path):
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="conv",
        thread_id="thread",
        drawing_name="assembly.dwg",
        drawing_path=str(tmp_path / "assembly.dwg"),
    )
    return db


def populate_fixture(db):
    db.upsert_entity(
        "P1",
        "Polyline",
        "AcDbPolyline",
        layer="OUTLINE",
        geometry={"vertices": [[0, 0, 0], [100, 0, 0], [100, 60, 0], [0, 60, 0]], "closed": True},
        bbox=(0, 0, 100, 60),
        topology_detail="full",
    )
    db.upsert_entity(
        "C1",
        "Circle",
        "AcDbCircle",
        layer="HOLES",
        geometry={"center": [25, 30, 0], "radius": 5},
        bbox=(20, 25, 30, 35),
        topology_detail="full",
    )
    db.upsert_entity(
        "L1",
        "Line",
        "AcDbLine",
        layer="CENTER",
        geometry={"start": [0, 30, 0], "end": [100, 30, 0]},
        bbox=(0, 30, 100, 30),
        topology_detail="full",
    )


def test_understanding_layer_end_to_end_without_autocad(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    populate_fixture(db)

    drawing_ir = build_drawing_ir(database=db)
    summary = summarize_drawing(level="normal", database=db)
    semantics = detect_semantic_objects(domain="mechanical", database=db)
    constraints = extract_drawing_constraints(database=db)
    validation = validate_geometry(database=db)
    resources = list_cad_resources(database=db)
    ir_resource = get_cad_resource("cad://drawing/current/ir", database=db)
    overview_resource = get_cad_resource("cad://drawing/current/ir/overview", database=db)
    entities_resource = get_cad_resource("cad://drawing/current/ir/entities", database=db)
    rich_ir = build_drawing_ir(database=db)

    snapshot_result = export_view_image_with_mapping(
        filepath=str(tmp_path / "view.wmf"),
        include_overlay=True,
        include_entity_bboxes=True,
        database=db,
    )
    snapshot = snapshot_result["data"]["snapshot"]
    visible = get_visible_entities_in_view(snapshot["snapshot_id"], database=db)
    grounded = ground_vlm_region(snapshot["snapshot_id"], [300, 300, 600, 700], database=db)
    overlay_grounded = ground_vlm_overlay_id(
        snapshot["snapshot_id"],
        snapshot["overlay_items"][0]["overlay_id"],
        database=db,
    )

    plan = {
        "plan_id": "p1",
        "steps": [
            {
                "step_id": "s1",
                "op": "move_entity",
                "args": {"handle": "C1", "from_point": [25, 30, 0], "to_point": [30, 30, 0]},
                "writes": True,
            }
        ],
    }
    plan_validation = validate_cad_plan(plan)
    dry_run = dry_run_cad_plan(plan)
    repair = propose_repair_plan([], database=db)

    assert drawing_ir["schema_version"] == "cad-ir/v2"
    assert drawing_ir["drawing"]["counts"]["entities"] == 3
    assert {
        entity["handle"] for entity in drawing_ir["sections"]["entities"]["items"]
    } == {"P1", "C1", "L1"}
    assert rich_ir["drawing"]["counts"]["semantic_objects"] > 0
    assert rich_ir["drawing"]["counts"]["constraints"] > 0
    assert rich_ir["quality"]["coverage"]["validation"]["has_report"] is True
    assert summary["ok"]
    assert semantics["ok"]
    assert constraints["ok"]
    assert validation["ok"]
    assert resources["ok"]
    assert "cad://drawing/current/ir/overview" in resources["data"]["resources"]
    assert "cad://drawing/current/ir/entities" in resources["data"]["resources"]
    assert "cad://drawing/current/vlm-findings" in resources["data"]["resources"]
    assert "cad://drawing/current/engineering-interpretation" in resources["data"]["resources"]
    assert ir_resource["ok"]
    assert ir_resource["data"]["payload"]["drawing_ir"]["schema_version"] == "cad-ir/v2"
    assert overview_resource["ok"]
    assert set(overview_resource["data"]["payload"]["drawing_ir"]["sections"]) == {"overview"}
    assert entities_resource["ok"]
    assert entities_resource["data"]["payload"]["drawing_ir"]["sections"]["entities"]["total"] == 3
    assert snapshot_result["ok"]
    assert Path(snapshot["context_json_path"]).exists()
    assert Path(snapshot["overlay_image_path"]).exists()
    assert snapshot["mapping_view_source"] == "scanned_entity_extent_for_wmf_export"
    assert snapshot["overlay_items"][0]["overlay_id"].startswith("E")
    assert visible["ok"]
    assert grounded["ok"]
    assert overlay_grounded["ok"]
    assert plan_validation["ok"]
    assert dry_run["ok"]
    assert repair["ok"]
