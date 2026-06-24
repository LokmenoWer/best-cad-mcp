"""Regression tests for VLM-pipeline robustness and tool-surface gating.

These lock in the usability fixes:
- partial ImageDrawingSpec validation (valid items survive a bad sibling),
- VLM review accepting localized findings without separate evidence text,
- the env-gated tool profile mechanism, and
- structured, actionable error guidance.
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

from src.cad_database import CADDatabase
from src.cad_understanding.image_trace import validate_image_drawing_spec
from src.cad_understanding.vlm import validate_vlm_review_output


def make_db(tmp_path):
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="conv",
        thread_id="thread",
        drawing_name="usability.dwg",
        drawing_path=str(tmp_path / "usability.dwg"),
    )
    return db


def _good_and_bad_spec():
    return {
        "schema_version": "ImageDrawingSpec/v1",
        "domain": "mechanical",
        "units": "mm",
        "calibration_candidates": [],
        "features": [
            {
                "id": "good_hole",
                "kind": "hole",
                "confidence": 0.9,
                "pixel_bbox": [10, 10, 20, 20],
                "pixel_geometry": {"center": [15, 15], "radius": 5},
                "evidence": {"text": "clear circular hole"},
            },
            {
                # Invalid: unsupported kind + out-of-range confidence + no evidence.
                "id": "bad_item",
                "kind": "plain_square",
                "confidence": 1.4,
                "pixel_bbox": [0, 0, 1, 1],
            },
        ],
        "geometry": [],
        "annotations": [],
        "relations": [],
        "tables": [],
        "uncertainties": [],
    }


# ── Partial ImageDrawingSpec validation ──────────────────────────────


def test_spec_validation_keeps_valid_items_and_reports_rejected(tmp_path):
    db = make_db(tmp_path)
    result = validate_image_drawing_spec(_good_and_bad_spec(), database=db)

    assert result["ok"], result
    features = result["data"]["spec"]["features"]
    assert [f["id"] for f in features] == ["good_hole"]
    rejected = result["data"]["rejected_items"]
    assert len(rejected) == 1
    assert rejected[0]["path"].endswith("bad_item")
    assert any("rejected" in w.lower() for w in result["warnings"])


def test_spec_validation_fails_when_no_valid_items(tmp_path):
    db = make_db(tmp_path)
    spec = _good_and_bad_spec()
    spec["features"] = [spec["features"][1]]  # only the bad one
    result = validate_image_drawing_spec(spec, database=db)

    assert not result["ok"]
    messages = " ".join(
        " ".join(err.get("errors", [])) for err in result["data"]["errors"]
    )
    assert "kind must be one" in messages


def test_spec_validation_hard_fails_on_structural_error(tmp_path):
    db = make_db(tmp_path)
    spec = _good_and_bad_spec()
    spec["features"] = [spec["features"][0]]  # valid item present
    del spec["tables"]  # structural: required section missing
    result = validate_image_drawing_spec(spec, database=db)

    assert not result["ok"]
    assert any(err.get("path") == "tables" for err in result["data"]["errors"])


def test_bad_component_hypothesis_is_per_item_not_structural(tmp_path):
    db = make_db(tmp_path)
    spec = _good_and_bad_spec()
    spec["features"] = [spec["features"][0]]  # one valid drawable feature
    spec["component_hypotheses"] = [
        {"id": "guess", "label": "flange", "confidence": 0.8}  # missing evidence
    ]
    result = validate_image_drawing_spec(spec, database=db)

    # Valid CAD items survive; the optional bad hypothesis is dropped + reported.
    assert result["ok"], result
    assert [f["id"] for f in result["data"]["spec"]["features"]] == ["good_hole"]
    assert result["data"]["spec"]["component_hypotheses"] == []
    assert any(
        "component_hypotheses" in err.get("path", "")
        for err in result["data"]["rejected_items"]
    )


def test_component_hypotheses_not_a_list_is_structural(tmp_path):
    db = make_db(tmp_path)
    spec = _good_and_bad_spec()
    spec["features"] = [spec["features"][0]]
    spec["component_hypotheses"] = "flange"  # whole section malformed
    result = validate_image_drawing_spec(spec, database=db)

    assert not result["ok"]
    assert any(
        err.get("path") == "component_hypotheses" for err in result["data"]["errors"]
    )


def test_ellipse_arc_accepts_geometry_candidates(tmp_path):
    db = make_db(tmp_path)
    spec = {
        "schema_version": "ImageDrawingSpec/v1",
        "domain": "mechanical",
        "units": "mm",
        "calibration_candidates": [],
        "features": [],
        "geometry": [
            {
                "id": "arc_guess",
                "kind": "ellipse_arc",
                "confidence": 0.7,
                "pixel_bbox": [0, 0, 40, 40],
                "geometry_candidates": [
                    {
                        "kind": "ellipse_arc",
                        "center": [20, 20],
                        "major_axis": [18, 0],
                        "radius_ratio": 0.5,
                        "start_angle": 10,
                        "end_angle": 200,
                    }
                ],
                "evidence": {"text": "elliptical edge, angles uncertain"},
            }
        ],
        "annotations": [],
        "relations": [],
        "tables": [],
        "uncertainties": [],
    }
    result = validate_image_drawing_spec(spec, database=db)
    assert result["ok"], result
    assert result["data"]["spec"]["geometry"][0]["id"] == "arc_guess"


# ── VLM review evidence is optional when the finding is localized ─────


def test_vlm_review_accepts_localized_finding_without_evidence(tmp_path):
    db = make_db(tmp_path)
    review = {
        "findings": [
            {
                "issue_type": "missing_dimension",
                "confidence": 0.8,
                "claimed_handles": ["2AB"],
                # no "evidence" key
            }
        ]
    }
    # No snapshot_id -> handle/overlay membership is not cross-checked.
    result = validate_vlm_review_output(review, database=db)
    assert result["ok"], result
    assert len(result["data"]["findings"]) == 1


def test_vlm_review_still_requires_some_localization(tmp_path):
    db = make_db(tmp_path)
    review = {"findings": [{"issue_type": "vague", "confidence": 0.8}]}
    result = validate_vlm_review_output(review, database=db)
    assert not result["ok"]


# ── Tool profile gating (logic only; no re-import needed) ─────────────


def _import_server():
    for name in ("win32com", "win32com.client", "pythoncom", "pyautocad"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["win32com"].client = sys.modules["win32com.client"]
    sys.modules["win32com.client"].Dispatch = MagicMock(return_value=MagicMock())
    sys.modules["win32com.client"].VARIANT = MagicMock()
    for key, val in {
        "VT_ARRAY": 0x2000, "VT_R8": 5, "VT_I2": 2, "VT_I4": 3,
        "VT_VARIANT": 12, "VT_DISPATCH": 9,
    }.items():
        setattr(sys.modules["pythoncom"], key, val)
    for attr in ("Autocad", "APoint", "aDouble", "aInt"):
        setattr(sys.modules["pyautocad"], attr, MagicMock())
    from src import server
    return server


def test_tool_profile_core_hides_long_tail_keeps_workflow(monkeypatch):
    server = _import_server()
    monkeypatch.setenv("CAD_MCP_TOOL_PROFILE", "core")
    monkeypatch.delenv("CAD_MCP_TOOLS_INCLUDE", raising=False)
    monkeypatch.delenv("CAD_MCP_TOOLS_EXCLUDE", raising=False)

    # Essential workflow tools stay enabled.
    for name in (
        "scan_all_entities", "build_drawing_ir", "draw_rectangle",
        "export_view_image_with_mapping", "validate_image_drawing_spec",
        "execute_cad_plan", "recommend_cad_tools",
    ):
        assert server._tool_enabled(name), name
    # Long-tail tools are hidden.
    assert not server._tool_enabled("get_preferences_user")
    assert not server._tool_enabled("draw_torus")
    # The typo duplicate is gone entirely.
    assert "insert_minert_block" not in {t.name for t in server._registered_tools()}


def test_tool_profile_full_exposes_everything(monkeypatch):
    server = _import_server()
    monkeypatch.setenv("CAD_MCP_TOOL_PROFILE", "full")
    monkeypatch.delenv("CAD_MCP_TOOLS_INCLUDE", raising=False)
    monkeypatch.delenv("CAD_MCP_TOOLS_EXCLUDE", raising=False)
    assert server._tool_enabled("get_preferences_user")
    assert server._tool_enabled("draw_torus")


def test_tool_profile_include_exclude_overrides(monkeypatch):
    server = _import_server()
    monkeypatch.setenv("CAD_MCP_TOOL_PROFILE", "lean")
    monkeypatch.setenv("CAD_MCP_TOOLS_INCLUDE", "draw_torus")
    monkeypatch.setenv("CAD_MCP_TOOLS_EXCLUDE", "send_command")
    assert server._tool_enabled("draw_torus")        # force-included
    assert not server._tool_enabled("send_command")  # force-excluded


def test_lean_is_subset_of_core(monkeypatch):
    server = _import_server()
    hidden_in_core = server._CORE_HIDDEN_TOOLS
    hidden_in_lean = server._CORE_HIDDEN_TOOLS | server._LEAN_EXTRA_HIDDEN_TOOLS
    assert hidden_in_core <= hidden_in_lean
    # The two denylists never overlap the always-needed router/workflow tools.
    assert "scan_all_entities" not in hidden_in_lean
    assert "recommend_cad_tools" not in hidden_in_lean


# ── Structured error guidance ────────────────────────────────────────


def test_error_hint_detects_autocad_problems():
    server = _import_server()
    hint = server._error_recovery_hint(RuntimeError("No open document"))
    assert "check_runtime_environment" in hint


def test_error_hint_generic_points_to_router():
    server = _import_server()
    hint = server._error_recovery_hint(ValueError("bad argument shape"))
    assert "recommend_cad_tools" in hint or "get_tool_help" in hint
