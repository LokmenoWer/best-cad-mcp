import pytest

from src.cad_understanding import vlm


def _evaluate(monkeypatch, ground_truth, findings, **kwargs):
    monkeypatch.setattr(
        vlm,
        "get_vlm_findings",
        lambda **_ignored: {
            "ok": True,
            "data": {"findings": findings},
        },
    )
    return vlm.evaluate_vlm_grounding(
        ground_truth,
        database=object(),
        **kwargs,
    )


def _finding(finding_id, handles, *, issue_type="profile", score=0.9,
             status="grounded", grounded_handles=None):
    return {
        "finding_id": finding_id,
        "issue_type": issue_type,
        "status": status,
        "grounded_handles": (
            list(handles) if grounded_handles is None else list(grounded_handles)
        ),
        "grounding_candidates": [{
            "handles": list(handles),
            "score": score,
            "candidate_type": "semantic_shape",
        }],
    }


def test_issue_only_cases_use_optimal_one_to_one_shape_assignment(monkeypatch):
    # Database order is deliberately the opposite of ground-truth order. A
    # greedy first-match evaluator reports both shapes wrong even though the
    # two persisted findings are exactly correct as a set.
    findings = [
        _finding("pred-b", ["B"]),
        _finding("pred-a", ["A"]),
    ]
    evaluated = _evaluate(
        monkeypatch,
        [
            {"case_id": "expected-a", "issue_type": "profile",
             "expected_handle_group": ["A"]},
            {"case_id": "expected-b", "issue_type": "profile",
             "expected_handle_group": ["B"]},
        ],
        findings,
    )

    assert evaluated["ok"], evaluated
    cases = {case["case_id"]: case for case in evaluated["data"]["cases"]}
    assert cases["expected-a"]["matched_finding_id"] == "pred-a"
    assert cases["expected-b"]["matched_finding_id"] == "pred-b"
    assert all(
        case["matching_strategy"] == "global_optimal_one_to_one"
        for case in cases.values()
    )
    assert evaluated["data"]["metrics"]["top1_exact_group_accuracy"] == 1.0


def test_non_exhaustive_truth_does_not_label_unmatched_findings_false_positive(
    monkeypatch,
):
    findings = [
        _finding("annotated", ["A"], issue_type="known"),
        _finding(
            "unannotated-ambiguous", ["B"], issue_type="other",
            status="ambiguous", grounded_handles=[],
        ),
    ]
    ground_truth = [{
        "finding_id": "annotated",
        "issue_type": "known",
        "expected_handle_group": ["A"],
        "expected_status": "grounded",
    }]

    partial = _evaluate(
        monkeypatch,
        ground_truth,
        findings,
        ground_truth_exhaustive=False,
    )
    metrics = partial["data"]["metrics"]
    assert metrics["ground_truth_exhaustive"] is False
    assert metrics["unmatched_finding_count"] == 1
    assert metrics["finding_match_precision"] is None
    assert metrics["issue_precision"] is None
    assert metrics["ambiguity_false_positive"] == 0
    assert partial["data"]["unmatched_finding_ids"] == ["unannotated-ambiguous"]

    exhaustive = _evaluate(monkeypatch, ground_truth, findings)
    exhaustive_metrics = exhaustive["data"]["metrics"]
    assert exhaustive_metrics["finding_match_precision"] == 0.5
    assert exhaustive_metrics["ambiguity_false_positive"] == 1


def test_equivalent_complete_groups_receive_exact_shape_credit(monkeypatch):
    finding = _finding("polyline-representation", ["POLY"])
    evaluated = _evaluate(
        monkeypatch,
        [{
            "finding_id": "polyline-representation",
            "expected_handle_group": ["EDGE_1", "EDGE_2", "EDGE_3", "EDGE_4"],
            "expected_equivalent_handle_groups": [["POLY"]],
            "expected_status": "grounded",
        }],
        [finding],
    )

    metrics = evaluated["data"]["metrics"]
    case = evaluated["data"]["cases"][0]
    assert metrics["top1_exact_group_accuracy"] == 1.0
    assert metrics["top1_group_precision"] == 1.0
    assert metrics["top1_group_recall"] == 1.0
    assert metrics["selective_exact_group_accuracy"] == 1.0
    assert case["best_matching_expected_group"] == ["POLY"]
    assert case["committed_group_correct"] is True


def test_score_calibration_and_abstention_measure_exact_complete_shapes(monkeypatch):
    findings = [
        _finding("correct", ["A"], score=0.8),
        _finding(
            "abstained", ["WRONG"], score=0.9,
            status="ambiguous", grounded_handles=[],
        ),
    ]
    evaluated = _evaluate(
        monkeypatch,
        [
            {"finding_id": "correct", "expected_handle_group": ["A"],
             "expected_status": "grounded"},
            {"finding_id": "abstained", "expected_handle_group": ["B"],
             "expected_status": "grounded"},
        ],
        findings,
    )

    metrics = evaluated["data"]["metrics"]
    assert metrics["top1_score_case_count"] == 2
    assert metrics["top1_score_brier"] == pytest.approx(0.425)
    assert metrics["top1_score_ece"] == pytest.approx(0.55)
    assert metrics["grounding_coverage"] == 0.5
    assert metrics["grounding_abstention_rate"] == 0.5
    assert metrics["selective_exact_group_accuracy"] == 1.0
    assert metrics["missed_required_commit_count"] == 1
    assert evaluated["data"]["cases"][1]["abstained"] is True


def test_equivalence_reports_decision_invariance_separately_from_score_identity(
    monkeypatch,
):
    findings = [
        _finding("global", ["A"], score=0.8),
        _finding("tile", ["A"], score=0.9),
    ]
    evaluated = _evaluate(
        monkeypatch,
        [
            {"finding_id": "global", "expected_handle_group": ["A"],
             "expected_status": "grounded", "equivalence_group": "same-view"},
            {"finding_id": "tile", "expected_handle_group": ["A"],
             "expected_status": "grounded", "equivalence_group": "same-view"},
        ],
        findings,
    )

    metrics = evaluated["data"]["metrics"]
    assert metrics["tile_invariance_rate"] == 0.0
    assert metrics["equivalence_ranking_invariance_rate"] == 1.0
    assert metrics["equivalence_decision_invariance_rate"] == 1.0


def test_submit_abstains_when_all_close_candidates_are_below_safety_floor(
    monkeypatch,
):
    item = {
        "finding_id": "subpixel-profile",
        "snapshot_id": "snapshot",
        "bbox": [10.0, 10.0, 11.0, 11.0],
        "issue_type": "profile_check",
        "semantic_type": "closed_profile",
        "claimed_handles": [],
        "evidence": {"text": "subpixel contour"},
    }
    monkeypatch.setattr(
        vlm,
        "validate_vlm_review_output",
        lambda *_args, **_kwargs: {
            "ok": True,
            "data": {"findings": [item]},
            "warnings": [],
        },
    )

    def candidate(handle, score):
        return {
            "handle": handle,
            "handles": [handle],
            "score": score,
            "candidate_type": "semantic_shape",
            "evidence": {
                "spatial_support": {
                    "support_mode": "bbox",
                    "overlap_score": 0.2,
                    "query_coverage": 0.2,
                },
            },
        }

    monkeypatch.setattr(
        vlm,
        "_ground_finding",
        lambda *_args, **_kwargs: (
            [candidate("A", 0.20), candidate("B", 0.19)],
            [],
            {},
            {"sources": ["bbox", "semantic_type"], "conflicts": []},
        ),
    )
    monkeypatch.setattr(vlm, "_store_findings", lambda *_args, **_kwargs: None)

    submitted = vlm.submit_vlm_review(
        "snapshot",
        {"findings": [item]},
        database=object(),
    )

    assert submitted["ok"], submitted
    stored = submitted["data"]["findings"][0]
    assert stored["status"] == "validated"
    assert stored["grounded_handles"] == []
    assert stored["grounding_selection"]["top_candidate_acceptable"] is False
    assert stored["grounding_selection"]["runner_up_acceptable"] is False
    assert stored["grounding_selection"]["candidate_margin_ambiguous"] is False
