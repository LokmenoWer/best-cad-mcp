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
