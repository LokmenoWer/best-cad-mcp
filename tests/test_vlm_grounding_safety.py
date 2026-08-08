from __future__ import annotations

import math

import pytest

from src.cad_database import CADDatabase
from src.cad_understanding.view_grounding import (
    apply_matrix_2d,
    export_view_image_with_mapping,
    ground_vlm_region,
)
from src.cad_understanding.vlm import (
    evaluate_vlm_grounding,
    submit_vlm_review,
    validate_vlm_review_output,
)


def _make_db(tmp_path) -> CADDatabase:
    database = CADDatabase(str(tmp_path / "cad.db"))
    database.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="conv",
        thread_id="thread",
        drawing_name="part.dwg",
        drawing_path=str(tmp_path / "part.dwg"),
    )
    return database


def _make_single_entity_snapshot(tmp_path, database: CADDatabase) -> dict:
    database.upsert_entity(
        "C1",
        "Circle",
        "AcDbCircle",
        layer="HOLES",
        geometry={"center": [0, 0, 0], "radius": 1},
        bbox=(-1, -1, 1, 1),
        topology_detail="full",
    )
    exported = export_view_image_with_mapping(
        filepath=str(tmp_path / "grounding-safety.wmf"),
        include_overlay=True,
        include_entity_bboxes=True,
        database=database,
    )
    assert exported["ok"], exported
    return exported["data"]["snapshot"]


def _farthest_corner_bbox(snapshot: dict, handle: str) -> list[float]:
    width = float(snapshot["image"]["width"])
    height = float(snapshot["image"]["height"])
    entity_bbox = [float(value) for value in snapshot["entity_screen_bboxes"][handle]]
    boxes = [
        [2.0, 2.0, 6.0, 6.0],
        [width - 6.0, 2.0, width - 2.0, 6.0],
        [2.0, height - 6.0, 6.0, height - 2.0],
        [width - 6.0, height - 6.0, width - 2.0, height - 2.0],
    ]

    def distance(box: list[float]) -> float:
        center_x = (box[0] + box[2]) / 2.0
        center_y = (box[1] + box[3]) / 2.0
        dx = max(entity_bbox[0] - center_x, 0.0, center_x - entity_bbox[2])
        dy = max(entity_bbox[1] - center_y, 0.0, center_y - entity_bbox[3])
        return math.hypot(dx, dy)

    return max(boxes, key=distance)


@pytest.mark.parametrize(
    ("finding", "error_fragment"),
    [
        (
            {
                "issue_type": "invalid_confidence",
                "confidence": float("nan"),
                "bbox": [0, 0, 10, 10],
            },
            "confidence must be a number",
        ),
        (
            {
                "issue_type": "invalid_bbox",
                "confidence": 0.5,
                "bbox": [0, 0, float("inf"), 10],
            },
            "localization",
        ),
    ],
)
def test_review_validation_rejects_non_finite_numbers(
    tmp_path, finding, error_fragment
):
    database = _make_db(tmp_path)

    result = validate_vlm_review_output(
        {"findings": [finding]},
        database=database,
    )

    assert not result["ok"]
    assert result["data"]["findings"] == []
    errors = " ".join(result["data"]["errors"][0]["errors"]).lower()
    assert error_fragment.lower() in errors


def test_ground_vlm_region_rejects_infinite_bbox_directly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    database = _make_db(tmp_path)
    snapshot = _make_single_entity_snapshot(tmp_path, database)

    result = ground_vlm_region(
        snapshot["snapshot_id"],
        [0, 0, float("inf"), 10],
        database=database,
    )

    assert not result["ok"]
    assert "finite" in result["message"].lower()


def test_blank_bbox_cannot_ground_with_or_without_claimed_handle(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    database = _make_db(tmp_path)
    snapshot = _make_single_entity_snapshot(tmp_path, database)
    blank_bbox = _farthest_corner_bbox(snapshot, "C1")
    overlay_id = snapshot["overlay_items"][0]["overlay_id"]

    direct = ground_vlm_region(
        snapshot["snapshot_id"], blank_bbox, database=database
    )
    assert direct["ok"], direct
    assert direct["data"]["candidates"] == []
    assert direct["data"]["shape_candidates"] == []
    assert direct["data"]["recommended_candidate"] is None

    submitted = submit_vlm_review(
        snapshot["snapshot_id"],
        {
            "findings": [
                {
                    "finding_id": "blank-only",
                    "issue_type": "blank_region",
                    "confidence": 0.9,
                    "bbox": blank_bbox,
                },
                {
                    "finding_id": "blank-with-claim",
                    "issue_type": "blank_region_claim",
                    "confidence": 0.9,
                    "bbox": blank_bbox,
                    "claimed_handles": ["C1"],
                },
                {
                    "finding_id": "blank-with-overlay",
                    "issue_type": "blank_region_overlay",
                    "confidence": 0.9,
                    "bbox": blank_bbox,
                    "overlay_id": overlay_id,
                },
            ]
        },
        database=database,
    )

    assert submitted["ok"], submitted
    findings = {
        finding["finding_id"]: finding
        for finding in submitted["data"]["findings"]
    }
    assert findings["blank-only"]["status"] == "validated"
    assert findings["blank-only"]["status"] != "grounded"
    assert findings["blank-only"]["grounded_handles"] == []
    assert findings["blank-only"]["grounding_candidates"] == []
    assert findings["blank-with-claim"]["status"] == "ambiguous"
    assert findings["blank-with-claim"]["status"] != "grounded"
    assert findings["blank-with-claim"]["grounded_handles"] == []
    assert findings["blank-with-claim"]["grounding_candidates"] == []
    assert findings["blank-with-overlay"]["status"] == "ambiguous"
    assert findings["blank-with-overlay"]["grounded_handles"] == []
    assert findings["blank-with-overlay"]["grounding_candidates"] == []
    conflicts = findings["blank-with-overlay"]["grounding_selection"][
        "reconciliation"
    ]["conflicts"]
    assert conflicts[0]["reason"] == "overlay_item_has_no_spatial_support_in_bbox"


def test_near_but_nonintersecting_path_is_not_a_recommended_grounding(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    database = _make_db(tmp_path)
    database.upsert_entity(
        "L1",
        "Line",
        "AcDbLine",
        layer="OUTLINE",
        geometry={"start": [0, 0, 0], "end": [100, 0, 0]},
        bbox=(0, 0, 100, 0),
        topology_detail="full",
    )
    snapshot = export_view_image_with_mapping(
        filepath=str(tmp_path / "near-line.wmf"),
        include_overlay=True,
        database=database,
    )["data"]["snapshot"]
    midpoint = apply_matrix_2d(snapshot["world_to_pixel"], 50.0, 0.0)
    query = [
        midpoint[0] - 1.0,
        midpoint[1] + 9.0,
        midpoint[0] + 1.0,
        midpoint[1] + 11.0,
    ]

    direct = ground_vlm_region(
        snapshot["snapshot_id"], query, database=database
    )
    submitted = submit_vlm_review(
        snapshot["snapshot_id"],
        {"findings": [{
            "finding_id": "near-line",
            "issue_type": "near_line",
            "bbox": query,
            "confidence": 0.9,
        }]},
        database=database,
    )

    assert direct["ok"], direct
    weak = next(
        candidate for candidate in direct["data"]["candidates"]
        if candidate.get("handle") == "L1"
    )
    assert 0.1 < weak["score"] < 0.35
    assert weak["evidence"]["spatial_support"]["path_length_in_query_px"] == 0.0
    assert direct["data"]["recommended_candidate"] is None
    assert direct["data"]["selection"]["acceptable_candidate_count"] == 0
    finding = submitted["data"]["findings"][0]
    assert finding["status"] == "validated"
    assert finding["grounded_handles"] == []


def test_duplicate_finding_ids_reject_every_duplicate(tmp_path):
    database = _make_db(tmp_path)
    finding = {
        "finding_id": "duplicate-id",
        "issue_type": "duplicate",
        "confidence": 0.8,
        "bbox": [0, 0, 10, 10],
    }

    result = validate_vlm_review_output(
        {"findings": [dict(finding), dict(finding)]},
        database=database,
    )

    assert not result["ok"]
    assert result["data"]["findings"] == []
    assert len(result["data"]["rejected_findings"]) == 2
    for rejection in result["data"]["rejected_findings"]:
        assert any("duplicated" in error for error in rejection["errors"])


def test_explicit_finding_id_cannot_overwrite_another_scope(tmp_path):
    database = _make_db(tmp_path)
    finding = {
        "finding_id": "scope-owned-id",
        "issue_type": "scope_safety",
        "confidence": 0.8,
        "bbox": [0, 0, 10, 10],
    }
    first = submit_vlm_review(
        "", {"findings": [finding]}, database=database
    )
    assert first["ok"], first

    database.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="another-conversation",
        thread_id="another-thread",
        drawing_name="another-part.dwg",
        drawing_path=str(tmp_path / "another-part.dwg"),
    )
    second = validate_vlm_review_output(
        {"findings": [finding]}, database=database
    )

    assert not second["ok"]
    assert "different drawing scope" in " ".join(
        second["data"]["errors"][0]["errors"]
    )


def test_unmatched_equivalence_cases_are_not_counted_as_invariant(tmp_path):
    database = _make_db(tmp_path)

    evaluated = evaluate_vlm_grounding(
        [
            {
                "finding_id": "missing-global",
                "expected_handle_group": ["C1"],
                "equivalence_group": "global-and-tile",
            },
            {
                "finding_id": "missing-tile",
                "expected_handle_group": ["C1"],
                "equivalence_group": "global-and-tile",
            },
        ],
        snapshot_id="snapshot-with-no-findings",
        database=database,
    )

    assert evaluated["ok"], evaluated
    metrics = evaluated["data"]["metrics"]
    assert metrics["tile_invariance_rate"] != 1.0
    assert metrics["equivalence_case_coverage"] == 0.0
    assert metrics["declared_equivalence_group_count"] == 1
    assert metrics["equivalence_group_count"] == 0
