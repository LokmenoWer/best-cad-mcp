import json

from src.cad_understanding.result import error_result, ok_result


def test_ok_result_is_json_serializable():
    result = ok_result(
        "done",
        data={"point": (1, 2), "nested": {"value": object()}},
        handles=["A1"],
        warnings=["rule-based"],
        next_tools=["summarize_drawing"],
    )

    encoded = json.dumps(result, ensure_ascii=False)

    assert result["ok"] is True
    assert result["handles"] == ["A1"]
    assert "done" in encoded


def test_error_result_shape():
    result = error_result("failed", data={"why": "bad"})

    assert result == {
        "ok": False,
        "message": "failed",
        "data": {"why": "bad"},
        "handles": [],
        "warnings": [],
        "next_tools": [],
    }
