from src.cad_understanding import plan as plan_module
from src.cad_understanding.plan import dry_run_cad_plan, execute_cad_plan, validate_cad_plan


def test_unknown_op_fails_validation():
    result = validate_cad_plan({"steps": [{"step_id": "s1", "op": "unknown", "args": {}}]})

    assert result["ok"] is False
    assert result["data"]["errors"]


def test_send_command_disallowed_by_default():
    result = validate_cad_plan({"steps": [{"step_id": "s1", "op": "send_command", "args": {"command": "LINE"}}]})

    assert result["ok"] is False
    assert "send_command" in result["data"]["errors"][0]["message"]


def test_dry_run_and_execute_gate():
    plan = {"plan_id": "p1", "steps": [{"step_id": "s1", "op": "draw_line", "args": {"start_x": 0, "start_y": 0, "end_x": 1, "end_y": 1}}]}

    dry = dry_run_cad_plan(plan)
    execute = execute_cad_plan(plan, allow_modify=False)

    assert dry["ok"] is True
    assert "No DWG changes" in dry["message"]
    assert execute["ok"] is False
    assert "allow_modify" in execute["message"]


def test_draw_line_accepts_explicit_coordinate_args():
    plan = {
        "plan_id": "p1",
        "steps": [
            {
                "step_id": "s1",
                "op": "draw_line",
                "args": {
                    "start_x": 0,
                    "start_y": 0,
                    "end_x": 1,
                    "end_y": 1,
                    "start_z": 0,
                    "end_z": 0,
                    "layer": "CHECK",
                    "color": "red",
                },
            }
        ],
    }

    result = validate_cad_plan(plan)

    assert result["ok"] is True


def test_draw_line_rejects_point_array_args():
    result = validate_cad_plan({
        "plan_id": "p1",
        "steps": [
            {
                "step_id": "s1",
                "op": "draw_line",
                "args": {"start": [0, 0, 0], "end": [1, 1, 0]},
            }
        ],
    })

    assert result["ok"] is False
    messages = " ".join(error["message"] for error in result["data"]["errors"])
    assert "start_x/start_y/start_z" in messages
    assert "end_x/end_y/end_z" in messages


def test_plan_variables_save_as_and_reference():
    plan = {
        "plan_id": "p1",
        "steps": [
            {
                "step_id": "outer",
                "op": "draw_circle",
                "args": {"center_x": 0, "center_y": 0, "radius": 5},
                "save_as": "$outer_circle",
            },
            {
                "step_id": "move",
                "op": "move_entity",
                "args": {"handle": "$outer_circle", "from_point": [0, 0, 0], "to_point": [1, 0, 0]},
                "depends_on": ["outer"],
            },
        ],
    }

    validation = validate_cad_plan(plan)
    dry = dry_run_cad_plan(plan)

    assert validation["ok"] is True
    assert dry["ok"] is True
    assert dry["data"]["steps"][1]["args"]["handle"]["unresolved_future_handle"] == "$outer_circle"


def test_missing_variable_fails_validation():
    result = validate_cad_plan({
        "steps": [
            {"step_id": "move", "op": "move_entity", "args": {"handle": "$missing", "from_point": [0, 0, 0], "to_point": [1, 0, 0]}}
        ]
    })

    assert result["ok"] is False
    assert "Unknown variable" in result["data"]["errors"][0]["message"]


def test_execute_captures_handles_and_postcondition(monkeypatch):
    def draw_circle(**kwargs):
        assert kwargs["radius"] == 5
        return "Created circle handle: C1"

    monkeypatch.setattr(plan_module, "_tool_dispatch", lambda: {"draw_circle": draw_circle})

    result = execute_cad_plan(
        {
            "plan_id": "p1",
            "steps": [
                {
                    "step_id": "outer",
                    "op": "draw_circle",
                    "args": {"center_x": 0, "center_y": 0, "radius": 5},
                    "save_as": "$outer_circle",
                    "postconditions": [{"type": "exists", "target": "$outer_circle"}],
                }
            ],
        },
        allow_modify=True,
        transactional=False,
        validate_after_plan=False,
    )

    assert result["ok"] is True
    assert result["handles"] == ["C1"]
    assert result["data"]["state"]["outer_circle"] == "C1"
    assert result["data"]["postconditions"][0]["ok"] is True


def test_execute_rolls_back_on_failure(monkeypatch):
    rollback_calls = []

    def failing_tool(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(plan_module, "_tool_dispatch", lambda: {"draw_line": failing_tool})
    monkeypatch.setattr(plan_module, "_transaction_begin", lambda enabled: {"enabled": enabled, "ok": True})
    monkeypatch.setattr(plan_module, "_transaction_rollback", lambda enabled: rollback_calls.append(enabled) or {"enabled": enabled, "ok": True})

    result = execute_cad_plan(
        {
            "plan_id": "p1",
            "steps": [
                {
                    "step_id": "s1",
                    "op": "draw_line",
                    "args": {"start_x": 0, "start_y": 0, "end_x": 1, "end_y": 1},
                }
            ],
        },
        allow_modify=True,
        transactional=True,
        rollback_on_error=True,
        validate_after_plan=False,
    )

    assert result["ok"] is False
    assert rollback_calls == [True]
    assert result["data"]["failed_step"]["step_id"] == "s1"


def test_execute_rolls_back_on_failure_text(monkeypatch):
    rollback_calls = []

    def failing_tool(**kwargs):
        return "ERROR: create_layer failed: invalid literal for int()"

    monkeypatch.setattr(plan_module, "_tool_dispatch", lambda: {"draw_line": failing_tool})
    monkeypatch.setattr(plan_module, "_transaction_begin", lambda enabled: {"enabled": enabled, "ok": True})
    monkeypatch.setattr(plan_module, "_transaction_rollback", lambda enabled: rollback_calls.append(enabled) or {"enabled": enabled, "ok": True})

    result = execute_cad_plan(
        {
            "plan_id": "p1",
            "steps": [
                {
                    "step_id": "s1",
                    "op": "draw_line",
                    "args": {"start_x": 0, "start_y": 0, "end_x": 1, "end_y": 1},
                }
            ],
        },
        allow_modify=True,
        transactional=True,
        rollback_on_error=True,
        validate_after_plan=False,
    )

    assert result["ok"] is False
    assert "returned failure" in result["message"]
    assert result["data"]["failed_result"] == "ERROR: create_layer failed: invalid literal for int()"
    assert rollback_calls == [True]


def test_execute_rolls_back_on_structured_failure(monkeypatch):
    rollback_calls = []

    def failing_tool(**kwargs):
        return {"ok": False, "message": "tool refused unsafe edit"}

    monkeypatch.setattr(plan_module, "_tool_dispatch", lambda: {"draw_line": failing_tool})
    monkeypatch.setattr(plan_module, "_transaction_begin", lambda enabled: {"enabled": enabled, "ok": True})
    monkeypatch.setattr(plan_module, "_transaction_rollback", lambda enabled: rollback_calls.append(enabled) or {"enabled": enabled, "ok": True})

    result = execute_cad_plan(
        {
            "plan_id": "p1",
            "steps": [
                {
                    "step_id": "s1",
                    "op": "draw_line",
                    "args": {"start_x": 0, "start_y": 0, "end_x": 1, "end_y": 1},
                }
            ],
        },
        allow_modify=True,
        transactional=True,
        rollback_on_error=True,
        validate_after_plan=False,
    )

    assert result["ok"] is False
    assert "tool refused unsafe edit" in result["message"]
    assert result["data"]["failed_result"] == {"ok": False, "message": "tool refused unsafe edit"}
    assert rollback_calls == [True]
