from src.cad_database import CADDatabase
from src.cad_understanding.validators import get_validation_report
from src.cad_understanding.view_grounding import export_view_image_with_mapping
from src.cad_understanding.vlm import (
    get_vlm_findings,
    promote_vlm_finding_to_validation_issue,
    submit_vlm_review,
    validate_vlm_review_output,
)


def make_db(tmp_path):
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="conv",
        thread_id="thread",
        drawing_name="part.dwg",
        drawing_path=str(tmp_path / "part.dwg"),
    )
    return db


def populate_fixture(db):
    db.upsert_entity(
        "C1",
        "Circle",
        "AcDbCircle",
        layer="HOLES",
        geometry={"center": [10, 10, 0], "radius": 2},
        bbox=(8, 8, 12, 12),
        topology_detail="full",
    )


def test_vlm_review_validation_submit_and_promote(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    populate_fixture(db)
    snapshot_result = export_view_image_with_mapping(
        filepath=str(tmp_path / "view.wmf"),
        include_overlay=True,
        include_entity_bboxes=True,
        database=db,
    )
    snapshot = snapshot_result["data"]["snapshot"]
    overlay_id = snapshot["overlay_items"][0]["overlay_id"]
    review = {
        "findings": [
            {
                "overlay_id": overlay_id,
                "issue_type": "missing_diameter_dimension",
                "severity": "high",
                "confidence": 0.91,
                "evidence": {"text": "Hole has no diameter callout."},
            }
        ]
    }

    validation = validate_vlm_review_output(review, snapshot_id=snapshot["snapshot_id"], database=db)
    submitted = submit_vlm_review(
        snapshot["snapshot_id"],
        review,
        source_model="unit-test-vlm",
        database=db,
    )
    findings = get_vlm_findings(snapshot_id=snapshot["snapshot_id"], database=db)
    promoted = promote_vlm_finding_to_validation_issue(database=db)
    report = get_validation_report(database=db)

    assert validation["ok"]
    assert submitted["ok"]
    assert findings["data"]["findings"][0]["status"] == "grounded"
    assert findings["data"]["findings"][0]["grounded_handles"] == ["C1"]
    assert promoted["ok"]
    assert promoted["data"]["promoted_issues"][0]["issue_type"] == "vlm_missing_diameter_dimension"
    assert report["data"]["validation_report"]["issue_count"] == 1


def test_vlm_review_rejects_unknown_overlay_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    populate_fixture(db)
    snapshot_result = export_view_image_with_mapping(
        filepath=str(tmp_path / "view.wmf"),
        include_overlay=True,
        include_entity_bboxes=True,
        database=db,
    )
    review = {
        "findings": [
            {
                "overlay_id": "E999",
                "issue_type": "bad_reference",
                "confidence": 0.5,
                "evidence": "invalid overlay",
            }
        ]
    }

    result = validate_vlm_review_output(
        review,
        snapshot_id=snapshot_result["data"]["snapshot"]["snapshot_id"],
        database=db,
    )

    assert not result["ok"]
    assert "overlay_id E999" in result["data"]["errors"][0]["errors"][0]
