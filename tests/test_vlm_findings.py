import pytest

from src.cad_database import CADDatabase
from src.cad_understanding import vision
from src.cad_understanding.engineering_review import analyze_engineering_drawing_stages
from src.cad_understanding.ir_builder import build_drawing_ir
from src.cad_understanding.semantic_graph import detect_semantic_objects, get_semantic_graph
from src.cad_understanding.validators import get_validation_report
from src.cad_understanding.view_grounding import export_view_image_with_mapping
from src.cad_understanding.vlm import (
    evaluate_vlm_grounding,
    fuse_vlm_findings_into_semantic_graph,
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


def test_bbox_without_source_ref_reports_global_coordinate_assumption(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    populate_fixture(db)
    snapshot = export_view_image_with_mapping(
        filepath=str(tmp_path / "source-ref-warning.wmf"),
        include_overlay=True,
        database=db,
    )["data"]["snapshot"]
    bbox = snapshot["overlay_items"][0]["pixel_bbox"]
    finding = {
        "bbox": bbox,
        "issue_type": "coordinate_contract_check",
        "confidence": 0.9,
    }

    assumed = validate_vlm_review_output(
        {"findings": [finding]},
        snapshot_id=snapshot["snapshot_id"],
        database=db,
    )
    explicit = validate_vlm_review_output(
        {
            "findings": [{
                **finding,
                "source_ref": {"coordinate_space": "snapshot_global"},
            }]
        },
        snapshot_id=snapshot["snapshot_id"],
        database=db,
    )

    assert assumed["ok"], assumed
    assert any("assumed to be snapshot-global" in warning for warning in assumed["warnings"])
    assert explicit["ok"], explicit
    assert not any("assumed to be snapshot-global" in warning for warning in explicit["warnings"])


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
                "semantic_type": "dimension_annotation",
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
    evaluation = evaluate_vlm_grounding(
        [
            {
                "overlay_id": overlay_id,
                "issue_type": "missing_diameter_dimension",
                "expected_handles": ["C1"],
            }
        ],
        snapshot_id=snapshot["snapshot_id"],
        database=db,
    )
    fused = fuse_vlm_findings_into_semantic_graph(database=db)
    graph = get_semantic_graph(database=db)
    drawing_ir = build_drawing_ir(database=db, sections=["vlm_findings"])
    interpretation = analyze_engineering_drawing_stages(snapshot_id=snapshot["snapshot_id"], database=db)
    promoted = promote_vlm_finding_to_validation_issue(database=db)
    report = get_validation_report(database=db)

    assert validation["ok"]
    assert submitted["ok"]
    assert findings["data"]["findings"][0]["status"] == "grounded"
    assert findings["data"]["findings"][0]["grounded_handles"] == ["C1"]
    assert evaluation["data"]["metrics"]["handle_top1_accuracy"] == 1.0
    assert evaluation["data"]["metrics"]["issue_type_recall"] == 1.0
    assert fused["ok"]
    assert fused["data"]["semantic_objects"][0]["source"] == "vlm:unit-test-vlm"
    assert "dimension_annotation" in {
        obj["object_type"] for obj in graph["data"]["semantic_objects"]
    }
    assert drawing_ir["sections"]["vlm_findings"]["count"] == 1
    assert interpretation["data"]["interpretation"]["summary"]["vlm_finding_count"] == 1
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


def test_bbox_review_persists_multi_handle_closed_shape(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    edges = {
        "L1": ([0, 0, 0], [40, 0, 0]),
        "L2": ([40, 0, 0], [40, 20, 0]),
        "L3": ([40, 20, 0], [0, 20, 0]),
        "L4": ([0, 20, 0], [0, 0, 0]),
    }
    for handle, (start, end) in edges.items():
        db.upsert_entity(
            handle,
            "Line",
            "AcDbLine",
            layer="OUTLINE",
            geometry={"start": start, "end": end},
            bbox=(
                min(start[0], end[0]), min(start[1], end[1]),
                max(start[0], end[0]), max(start[1], end[1]),
            ),
            topology_detail="full",
        )
    detect_semantic_objects("mechanical", database=db)
    exported = export_view_image_with_mapping(
        filepath=str(tmp_path / "shape.wmf"),
        include_overlay=True,
        overlay_granularity="semantic",
        database=db,
    )
    snapshot = exported["data"]["snapshot"]
    shape_item = next(
        item for item in snapshot["semantic_overlay_items"]
        if set(item["handles"]) == set(edges)
    )
    review = {
        "findings": [{
            "bbox": shape_item["pixel_bbox"],
            "issue_type": "profile_review",
            "semantic_type": "closed_profile",
            "severity": "medium",
            "confidence": 0.9,
            "evidence": {"text": "one closed rectangular contour"},
        }]
    }

    submitted = submit_vlm_review(snapshot["snapshot_id"], review, database=db)
    evaluation = evaluate_vlm_grounding(
        [{
            "issue_type": "profile_review",
            "expected_handles": sorted(edges),
            "expected_status": "grounded",
        }],
        snapshot_id=snapshot["snapshot_id"],
        database=db,
    )

    assert submitted["ok"], submitted
    finding = submitted["data"]["findings"][0]
    assert finding["status"] == "grounded"
    assert set(finding["grounded_handles"]) == set(edges)
    assert finding["grounding_candidates"][0]["candidate_type"] == "semantic_shape"
    metrics = evaluation["data"]["metrics"]
    assert metrics["top1_exact_group_accuracy"] == 1.0
    assert metrics["top1_group_precision"] == 1.0
    assert metrics["top1_group_recall"] == 1.0
    assert metrics["top1_group_f1"] == 1.0
    assert metrics["decision_accuracy"] == 1.0


def test_claimed_handle_only_is_verified_and_grounded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    populate_fixture(db)
    exported = export_view_image_with_mapping(
        filepath=str(tmp_path / "claimed.wmf"),
        include_overlay=True,
        database=db,
    )
    snapshot = exported["data"]["snapshot"]
    review = {
        "findings": [{
            "claimed_handles": ["C1"],
            "issue_type": "handle_check",
            "severity": "low",
            "confidence": 0.8,
            "evidence": {"text": "overlay sidecar identifies C1"},
        }]
    }

    submitted = submit_vlm_review(snapshot["snapshot_id"], review, database=db)

    assert submitted["ok"], submitted
    finding = submitted["data"]["findings"][0]
    assert finding["status"] == "grounded"
    assert finding["grounded_handles"] == ["C1"]


def test_claimed_multi_handle_group_is_one_grounding_decision(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    populate_fixture(db)
    db.upsert_entity(
        "C2", "Circle", "AcDbCircle", layer="HOLES",
        geometry={"center": [30, 10, 0], "radius": 2},
        bbox=(28, 8, 32, 12), topology_detail="full",
    )
    exported = export_view_image_with_mapping(
        filepath=str(tmp_path / "claimed-group.wmf"),
        include_overlay=True,
        database=db,
    )
    snapshot = exported["data"]["snapshot"]
    review = {"findings": [{
        "finding_id": "claimed-group",
        "claimed_handles": ["C2", "C1"],
        "issue_type": "group_check",
        "confidence": 0.9,
        "evidence": {"text": "the two visible members form one reviewed group"},
    }]}

    submitted = submit_vlm_review(snapshot["snapshot_id"], review, database=db)
    evaluated = evaluate_vlm_grounding(
        [{
            "finding_id": "claimed-group",
            "expected_handle_group": ["C1", "C2"],
            "expected_status": "grounded",
        }],
        snapshot_id=snapshot["snapshot_id"],
        database=db,
    )

    assert submitted["ok"], submitted
    finding = submitted["data"]["findings"][0]
    assert finding["status"] == "grounded"
    assert finding["grounded_handles"] == ["C1", "C2"]
    assert finding["grounding_candidates"][0]["candidate_type"] == "verified_claim_group"
    metrics = evaluated["data"]["metrics"]
    assert metrics["top1_exact_group_accuracy"] == 1.0
    assert metrics["ambiguity_precision"] is None
    assert metrics["ambiguity_recall"] is None


def test_bbox_claim_group_requires_exact_match_and_extra_ambiguity_is_fp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    populate_fixture(db)
    db.upsert_entity(
        "C2", "Circle", "AcDbCircle", layer="HOLES",
        geometry={"center": [30, 10, 0], "radius": 2},
        bbox=(28, 8, 32, 12), topology_detail="full",
    )
    exported = export_view_image_with_mapping(
        filepath=str(tmp_path / "claim-mismatch.wmf"),
        include_overlay=True,
        database=db,
    )
    snapshot = exported["data"]["snapshot"]
    review = {"findings": [
        {
            "finding_id": "expected-grounded",
            "claimed_handles": ["C1"],
            "issue_type": "expected_grounded",
            "confidence": 0.9,
            "evidence": {"text": "verified visible handle"},
        },
        {
            "finding_id": "unexpected-ambiguous",
            "bbox": snapshot["entity_screen_bboxes"]["C1"],
            "claimed_handles": ["C1", "C2"],
            "issue_type": "wrong_extra_claim",
            "confidence": 0.9,
            "evidence": {"text": "C2 is not inside the observed region"},
        },
    ]}

    submitted = submit_vlm_review(snapshot["snapshot_id"], review, database=db)
    evaluated = evaluate_vlm_grounding(
        [{
            "finding_id": "expected-grounded",
            "expected_handle_group": ["C1"],
            "expected_status": "grounded",
        }],
        snapshot_id=snapshot["snapshot_id"],
        database=db,
    )
    missing_id = evaluate_vlm_grounding(
        [{
            "finding_id": "does-not-exist",
            "issue_type": "expected_grounded",
            "expected_handle_group": ["C1"],
        }],
        snapshot_id=snapshot["snapshot_id"],
        database=db,
    )

    assert submitted["ok"], submitted
    by_id = {item["finding_id"]: item for item in submitted["data"]["findings"]}
    assert by_id["unexpected-ambiguous"]["status"] == "ambiguous"
    assert by_id["unexpected-ambiguous"]["grounded_handles"] == []
    metrics = evaluated["data"]["metrics"]
    assert metrics["ambiguity_false_positive"] == 1
    assert metrics["ambiguity_precision"] == 0.0
    assert metrics["ambiguity_recall"] is None
    assert missing_id["data"]["metrics"]["matched_case_count"] == 0


@pytest.mark.parametrize("top_k", [1, 2, 10])
def test_equal_bbox_candidates_remain_ambiguous(tmp_path, monkeypatch, top_k):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    for handle in ("C1", "C2"):
        db.upsert_entity(
            handle,
            "Circle",
            "AcDbCircle",
            layer="HOLES",
            geometry={"center": [10, 10, 0], "radius": 2},
            bbox=(8, 8, 12, 12),
            topology_detail="full",
        )
    exported = export_view_image_with_mapping(
        filepath=str(tmp_path / "ambiguous.wmf"),
        include_overlay=True,
        database=db,
    )
    snapshot = exported["data"]["snapshot"]
    review = {
        "findings": [{
            "bbox": snapshot["entity_screen_bboxes"]["C1"],
            "issue_type": "overlap_check",
            "severity": "medium",
            "confidence": 0.8,
            "evidence": {"text": "two coincident candidates are visually indistinguishable"},
        }]
    }

    submitted = submit_vlm_review(
        snapshot["snapshot_id"], review, top_k=top_k, database=db
    )

    assert submitted["ok"], submitted
    finding = submitted["data"]["findings"][0]
    assert finding["status"] == "ambiguous"
    assert finding["grounded_handles"] == []
    assert finding["grounding_selection"]["score_margin"] == 0.0
    evaluation = evaluate_vlm_grounding(
        [{
            "issue_type": "overlap_check",
            "expected_alternative_groups": [["C1"], ["C2"]],
            "expected_status": "ambiguous",
        }],
        snapshot_id=snapshot["snapshot_id"],
        database=db,
    )
    metrics = evaluation["data"]["metrics"]
    assert metrics["exact_group_case_count"] == 0
    assert metrics["top1_exact_group_accuracy"] == 0.0
    assert metrics["alternative_case_count"] == 1
    assert metrics["alternative_top1_accuracy"] == 1.0
    assert metrics["alternative_topk_coverage"] == 1.0
    assert metrics["alternative_topk_full_coverage_rate"] == 1.0
    assert metrics["ambiguity_precision"] == 1.0
    assert metrics["ambiguity_recall"] == 1.0
    assert metrics["decision_accuracy"] == 1.0


def test_shared_edge_localization_sources_are_reconciled_before_promotion(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    db = make_db(tmp_path)
    segments = {
        "LEFT": ([0, 0, 0], [0, 10, 0]),
        "B0": ([0, 0, 0], [10, 0, 0]),
        "T0": ([0, 10, 0], [10, 10, 0]),
        "MID": ([10, 0, 0], [10, 10, 0]),
        "B1": ([10, 0, 0], [20, 0, 0]),
        "T1": ([10, 10, 0], [20, 10, 0]),
        "RIGHT": ([20, 0, 0], [20, 10, 0]),
    }
    for handle, (start, end) in segments.items():
        db.upsert_entity(
            handle,
            "Line",
            "AcDbLine",
            layer="OUTLINE",
            geometry={"start": start, "end": end},
            bbox=(
                min(start[0], end[0]), min(start[1], end[1]),
                max(start[0], end[0]), max(start[1], end[1]),
            ),
            topology_detail="full",
        )
    detected = detect_semantic_objects("mechanical", database=db)
    assert detected["ok"], detected
    exported = export_view_image_with_mapping(
        filepath=str(tmp_path / "shared-edge.wmf"),
        include_overlay=True,
        overlay_granularity="all",
        database=db,
    )
    snapshot = exported["data"]["snapshot"]
    left_handles = {"LEFT", "B0", "T0", "MID"}
    left_profile = next(
        item for item in snapshot["semantic_overlay_items"]
        if item.get("object_type") == "closed_profile"
        and set(item.get("handles", [])) == left_handles
    )
    mid_overlay = next(
        item for item in snapshot["overlay_items"]
        if item.get("item_kind") == "entity" and item.get("handle") == "MID"
    )
    review = {"findings": [
        {
            "finding_id": "member-overlay-with-bbox",
            "overlay_id": mid_overlay["overlay_id"],
            "bbox": left_profile["pixel_bbox"],
            "issue_type": "left_profile_review",
            "semantic_type": "closed_profile",
            "confidence": 0.95,
            "evidence": {"text": "shared edge is one member of the left profile"},
        },
        {
            "finding_id": "claim-only-incomplete",
            "claimed_handles": ["MID"],
            "issue_type": "incomplete_profile_claim",
            "semantic_type": "closed_profile",
            "confidence": 0.95,
            "evidence": {"text": "one shared edge cannot identify a complete profile"},
        },
        {
            "finding_id": "overlay-only-shared",
            "overlay_id": mid_overlay["overlay_id"],
            "issue_type": "shared_profile_reference",
            "semantic_type": "closed_profile",
            "confidence": 0.95,
            "evidence": {"text": "the edge belongs to either adjacent cell"},
        },
        {
            "finding_id": "all-sources-match",
            "overlay_id": left_profile["overlay_id"],
            "bbox": left_profile["pixel_bbox"],
            "claimed_handles": sorted(left_handles),
            "issue_type": "confirmed_left_profile",
            "semantic_type": "closed_profile",
            "confidence": 0.95,
            "evidence": {"text": "semantic overlay, region, and exact group agree"},
        },
    ]}

    submitted = submit_vlm_review(
        snapshot["snapshot_id"], review, database=db
    )

    assert submitted["ok"], submitted
    findings = {
        item["finding_id"]: item for item in submitted["data"]["findings"]
    }
    member = findings["member-overlay-with-bbox"]
    assert member["status"] == "grounded"
    assert set(member["grounded_handles"]) == left_handles
    assert member["grounding_candidates"][0]["object_type"] == "closed_profile"
    assert member["grounding_selection"]["reconciliation"]["conflicts"] == []
    assert member["grounding_selection"]["reconciliation"]["agreements"][0][
        "relation"
    ] == "overlay_member_of_shape"

    for finding_id in ("claim-only-incomplete", "overlay-only-shared"):
        finding = findings[finding_id]
        assert finding["status"] == "ambiguous"
        assert finding["grounded_handles"] == []
        assert finding["grounding_selection"]["reconciliation"]["conflicts"]
    matched = findings["all-sources-match"]
    assert matched["status"] == "grounded"
    assert set(matched["grounded_handles"]) == left_handles
    assert matched["grounding_selection"]["reconciliation"]["conflicts"] == []

    persisted = get_vlm_findings(
        snapshot_id=snapshot["snapshot_id"], database=db
    )
    persisted_by_id = {
        item["finding_id"]: item for item in persisted["data"]["findings"]
    }
    for finding_id in ("claim-only-incomplete", "overlay-only-shared"):
        reconciliation = persisted_by_id[finding_id]["evidence"][
            "grounding_reconciliation"
        ]
        assert reconciliation["conflicts"]
        assert persisted_by_id[finding_id]["grounding_candidates"]

    conflicted_ids = ["claim-only-incomplete", "overlay-only-shared"]
    fused = fuse_vlm_findings_into_semantic_graph(
        finding_ids=conflicted_ids, database=db
    )
    promoted = promote_vlm_finding_to_validation_issue(
        finding_ids=conflicted_ids, database=db
    )
    assert fused["data"]["semantic_objects"] == []
    assert promoted["data"]["promoted_issues"] == []


def test_snapshot_tile_local_bbox_is_rebased_and_validated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    image_module = pytest.importorskip("PIL.Image")
    db = make_db(tmp_path)
    populate_fixture(db)
    view_path = tmp_path / "tile-review.png"
    image_module.new("RGB", (1024, 768), "white").save(view_path)
    exported = export_view_image_with_mapping(
        filepath=str(view_path),
        include_overlay=True,
        include_tiles=True,
        tile_size=384,
        database=db,
    )
    snapshot = exported["data"]["snapshot"]
    tile = next(
        item for item in snapshot["tiles"]
        if item["global_pixel_bbox"][0] > 0 and item["global_pixel_bbox"][1] > 0
    )
    x0, y0 = tile["global_pixel_bbox"][:2]
    review = {
        "findings": [{
            "bbox": [10, 20, 40, 60],
            "source_ref": {
                "artifact_role": "tile",
                "tile_id": tile["tile_id"],
                "coordinate_space": "tile_local",
            },
            "coordinate_normalization": {
                "normalized_coordinate_space": "snapshot_global",
                "local_to_global": [1, 0, 0, 1, 0, 0],
            },
            "issue_type": "local_detail_check",
            "severity": "low",
            "confidence": 0.75,
            "evidence": {"text": "local crop evidence"},
        }]
    }

    validation = validate_vlm_review_output(
        review, snapshot_id=snapshot["snapshot_id"], database=db
    )

    assert validation["ok"], validation
    finding = validation["data"]["findings"][0]
    assert finding["bbox"] == [x0 + 10, y0 + 20, x0 + 40, y0 + 60]
    assert finding["coordinate_normalization"]["local_to_global"] == tile["local_to_global"]
    assert finding["source_ref"]["coordinate_space"] == "snapshot_global"

    invalid = review.copy()
    invalid["findings"] = [dict(review["findings"][0])]
    invalid["findings"][0]["source_ref"] = {
        "artifact_role": "tile",
        "tile_id": "T999",
        "coordinate_space": "tile_local",
    }
    rejected = validate_vlm_review_output(
        invalid, snapshot_id=snapshot["snapshot_id"], database=db
    )
    assert not rejected["ok"]
    assert "unknown" in " ".join(rejected["data"]["errors"][0]["errors"]).lower()


def test_downscaled_global_snapshot_bbox_uses_echoed_visual_source_ref(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    image_module = pytest.importorskip("PIL.Image")
    db = make_db(tmp_path)
    populate_fixture(db)
    view_path = tmp_path / "downscaled-global.png"
    image_module.new("RGB", (1024, 768), "white").save(view_path)
    snapshot = export_view_image_with_mapping(
        filepath=str(view_path), include_overlay=True, database=db
    )["data"]["snapshot"]
    resolved = vision.resolve_snapshot_images(
        snapshot["snapshot_id"],
        which="clean",
        max_dim=512,
        database=db,
    )
    assert resolved["ok"], resolved
    source_ref = resolved["data"]["source_ref_template"]
    review = {"findings": [{
        "finding_id": "scaled-global",
        "bbox": [25, 30, 75, 90],
        "source_ref": source_ref,
        "issue_type": "scaled_global_check",
        "semantic_type": "outer_profile",
        "confidence": 0.9,
    }]}

    validation = validate_vlm_review_output(
        review, snapshot_id=snapshot["snapshot_id"], database=db
    )

    assert validation["ok"], validation
    finding = validation["data"]["findings"][0]
    assert finding["bbox"] == [50.0, 60.0, 150.0, 180.0]
    normalization = finding["coordinate_normalization"]
    assert normalization["observed_pixel_bbox"] == [25.0, 30.0, 75.0, 90.0]
    assert normalization["source_pixel_bbox"] == finding["bbox"]
    assert normalization["observed_to_global"] == [
        [2.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, 0.0, 1.0],
    ]
    assert finding["source_ref"]["coordinate_space"] == "snapshot_global"

    submitted = submit_vlm_review(
        snapshot["snapshot_id"], review, database=db
    )
    loaded = get_vlm_findings(
        snapshot_id=snapshot["snapshot_id"], database=db
    )
    assert submitted["ok"], submitted
    assert loaded["ok"], loaded
    stored = next(
        item for item in loaded["data"]["findings"]
        if item["finding_id"] == "scaled-global"
    )
    submitted_finding = submitted["data"]["findings"][0]
    assert stored["prompt_version"] == "vlm_review_drawing/v3"
    assert stored["bbox"] == submitted_finding["bbox"]
    assert stored["source_ref"] == submitted_finding["source_ref"]
    assert stored["coordinate_normalization"] == submitted_finding[
        "coordinate_normalization"
    ]
    assert stored["semantic_type"] == "closed_profile"
    assert stored["grounding_selection"] == submitted_finding[
        "grounding_selection"
    ]
    assert stored["evidence"]["grounding_audit"]["schema_version"] == (
        "VLMGroundingAudit/v1"
    )

    tampered_ref = {
        **source_ref,
        "observed_to_global": [
            [3.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, 0.0, 1.0],
        ],
    }
    rejected = validate_vlm_review_output(
        {"findings": [{**review["findings"][0], "source_ref": tampered_ref}]},
        snapshot_id=snapshot["snapshot_id"],
        database=db,
    )
    assert not rejected["ok"]
    assert "inconsistent" in " ".join(
        rejected["data"]["errors"][0]["errors"]
    ).lower()


def test_downscaled_snapshot_tile_bbox_composes_scale_and_translation_exactly(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    image_module = pytest.importorskip("PIL.Image")
    db = make_db(tmp_path)
    populate_fixture(db)
    view_path = tmp_path / "downscaled-tile.png"
    image_module.new("RGB", (1024, 768), "white").save(view_path)
    snapshot = export_view_image_with_mapping(
        filepath=str(view_path),
        include_overlay=True,
        include_tiles=True,
        tile_size=640,
        tile_overlap=0.2,
        database=db,
    )["data"]["snapshot"]
    tile = next(
        item for item in snapshot["tiles"]
        if item["global_pixel_bbox"][0] > 0
        and item["global_pixel_bbox"][1] > 0
        and item["image"] == {"width": 640, "height": 640}
    )
    resolved = vision.resolve_snapshot_images(
        snapshot["snapshot_id"],
        which="clean",
        max_dim=320,
        database=db,
        tile_id=tile["tile_id"],
    )
    assert resolved["ok"], resolved
    source_ref = resolved["data"]["source_ref_template"]
    validation = validate_vlm_review_output(
        {"findings": [{
            "finding_id": "scaled-tile",
            "bbox": [10, 20, 40, 60],
            "source_ref": source_ref,
            "issue_type": "scaled_tile_check",
            "confidence": 0.9,
        }]},
        snapshot_id=snapshot["snapshot_id"],
        database=db,
    )

    assert validation["ok"], validation
    finding = validation["data"]["findings"][0]
    x0, y0 = tile["global_pixel_bbox"][:2]
    assert finding["bbox"] == [
        x0 + 20.0, y0 + 40.0, x0 + 80.0, y0 + 120.0,
    ]
    normalization = finding["coordinate_normalization"]
    assert normalization["source_pixel_bbox"] == [20.0, 40.0, 80.0, 120.0]
    assert normalization["observed_to_global"] == [
        [2.0, 0.0, x0], [0.0, 2.0, y0], [0.0, 0.0, 1.0],
    ]


def test_global_and_overlapping_tile_local_grounding_are_invariant(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    image_module = pytest.importorskip("PIL.Image")
    db = make_db(tmp_path)
    db.upsert_entity(
        "FRAME", "Polyline", "AcDbPolyline", layer="OUTLINE",
        geometry={
            "vertices": [[0, 0, 0], [100, 0, 0], [100, 100, 0], [0, 100, 0]],
            "closed": True,
        },
        bbox=(0, 0, 100, 100),
        topology_detail="full",
    )
    db.upsert_entity(
        "TARGET", "Circle", "AcDbCircle", layer="HOLES",
        geometry={"center": [30, 50, 0], "radius": 3},
        bbox=(27, 47, 33, 53),
        topology_detail="full",
    )
    view_path = tmp_path / "tile-invariance.png"
    image_module.new("RGB", (1024, 768), "white").save(view_path)
    exported = export_view_image_with_mapping(
        filepath=str(view_path),
        include_overlay=True,
        include_tiles=True,
        tile_size=384,
        tile_overlap=0.25,
        database=db,
    )
    snapshot = exported["data"]["snapshot"]
    target_bbox = snapshot["entity_screen_bboxes"]["TARGET"]
    overlap_tiles = None
    overlap_bbox = None
    for index, first in enumerate(snapshot["tiles"]):
        for second in snapshot["tiles"][index + 1:]:
            first_bbox = first["global_pixel_bbox"]
            second_bbox = second["global_pixel_bbox"]
            intersection = [
                max(target_bbox[0], first_bbox[0], second_bbox[0]),
                max(target_bbox[1], first_bbox[1], second_bbox[1]),
                min(target_bbox[2], first_bbox[2], second_bbox[2]),
                min(target_bbox[3], first_bbox[3], second_bbox[3]),
            ]
            if intersection[2] - intersection[0] >= 6 and intersection[3] - intersection[1] >= 6:
                overlap_tiles = (first, second)
                overlap_bbox = [
                    intersection[0] + 1, intersection[1] + 1,
                    intersection[0] + 5, intersection[1] + 5,
                ]
                break
        if overlap_tiles:
            break

    assert overlap_tiles is not None, "fixture must place TARGET in a tile overlap"
    assert overlap_bbox is not None
    findings = [{
        "finding_id": "global",
        "bbox": overlap_bbox,
        "issue_type": "tile_invariance",
        "confidence": 0.9,
        "evidence": {"text": "global observation"},
    }]
    for suffix, tile in zip(("tile_a", "tile_b"), overlap_tiles):
        x0, y0 = tile["global_pixel_bbox"][:2]
        findings.append({
            "finding_id": suffix,
            "bbox": [
                overlap_bbox[0] - x0, overlap_bbox[1] - y0,
                overlap_bbox[2] - x0, overlap_bbox[3] - y0,
            ],
            "source_ref": {
                "artifact_role": "tile",
                "tile_id": tile["tile_id"],
                "coordinate_space": "tile_local",
            },
            "issue_type": "tile_invariance",
            "confidence": 0.9,
            "evidence": {"text": f"observation from {suffix}"},
        })

    submitted = submit_vlm_review(
        snapshot["snapshot_id"], {"findings": findings}, top_k=2, database=db
    )
    evaluated = evaluate_vlm_grounding(
        [
            {
                "finding_id": finding_id,
                "expected_handle_group": ["TARGET"],
                "expected_status": "grounded",
                "equivalence_group": "target-global-and-tiles",
            }
            for finding_id in ("global", "tile_a", "tile_b")
        ],
        snapshot_id=snapshot["snapshot_id"],
        top_k=2,
        database=db,
    )

    assert submitted["ok"], submitted
    grounded = submitted["data"]["findings"]
    assert [item["bbox"] for item in grounded] == [overlap_bbox] * 3
    assert len({item["status"] for item in grounded}) == 1
    signatures = []
    for item in grounded:
        candidates = item["grounding_candidates"]
        signatures.append([
            (
                tuple(candidate.get("handles") or [candidate.get("handle")]),
                candidate["score"],
            )
            for candidate in candidates
        ])
    assert signatures[0] == signatures[1] == signatures[2]
    assert signatures[0][0][0] == ("TARGET",)
    assert evaluated["data"]["metrics"]["tile_invariance_rate"] == 1.0
