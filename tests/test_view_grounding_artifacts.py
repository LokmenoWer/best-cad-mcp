from pathlib import Path

from src.cad_database import CADDatabase
from src.cad_understanding.view_grounding import (
    export_view_image_with_mapping,
    ground_vlm_overlay_id,
)


def make_db(tmp_path):
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="conv",
        thread_id="thread",
        drawing_name="overlay.dwg",
        drawing_path=str(tmp_path / "overlay.dwg"),
    )
    return db


def populate_fixture(db):
    db.upsert_entity(
        "P1",
        "Polyline",
        "AcDbPolyline",
        layer="OUTLINE",
        geometry={"vertices": [[0, 0, 0], [80, 0, 0], [80, 40, 0], [0, 40, 0]], "closed": True},
        bbox=(0, 0, 80, 40),
        topology_detail="full",
    )
    db.upsert_entity(
        "C1",
        "Circle",
        "AcDbCircle",
        layer="HOLES",
        geometry={"center": [20, 20, 0], "radius": 5},
        bbox=(15, 15, 25, 25),
        topology_detail="full",
    )


def test_export_includes_som_primitive_overlay_and_tile_index(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    populate_fixture(db)

    result = export_view_image_with_mapping(
        filepath=str(tmp_path / "view.wmf"),
        include_overlay=True,
        include_entity_bboxes=True,
        overlay_granularity="both",
        overlay_style="som",
        include_tiles=True,
        tile_size=512,
        database=db,
    )
    snapshot = result["data"]["snapshot"]
    primitive_items = snapshot["primitive_overlay_items"]
    primitive_id = primitive_items[0]["overlay_id"]
    grounded = ground_vlm_overlay_id(snapshot["snapshot_id"], primitive_id, database=db)

    assert result["ok"]
    assert snapshot["overlay_style"] == "som"
    assert snapshot["overlay_granularity"] == "both"
    assert Path(snapshot["overlay_image_path"]).exists()
    assert Path(snapshot["tile_index_path"]).exists()
    assert snapshot["tiles"]
    assert primitive_items
    assert primitive_id.startswith("E")
    assert ".P" in primitive_id
    assert grounded["ok"]
    assert grounded["data"]["candidate"]["item_kind"] == "primitive"
    assert grounded["data"]["candidate"]["primitive_key"]
