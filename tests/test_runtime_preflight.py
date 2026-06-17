import json
import tomllib

import pytest

from src import doctor
from src.cad_tools import utility_tools


def test_doctor_cli_json_success(monkeypatch, capsys):
    monkeypatch.setattr(
        doctor.utility_tools,
        "check_runtime_environment",
        lambda **kwargs: {
            "ok": True,
            "message": "Runtime preflight passed.",
            "data": {"checks": []},
            "handles": [],
            "warnings": [],
            "next_tools": [],
        },
    )

    exit_code = doctor.main(["--json"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["ok"] is True


def test_doctor_cli_human_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        doctor.utility_tools,
        "check_runtime_environment",
        lambda **kwargs: {
            "ok": False,
            "message": "Runtime preflight failed.",
            "data": {
                "checks": [
                    {
                        "name": "autocad_com_live",
                        "ok": False,
                        "required": True,
                        "detail": "not connected",
                        "remediation": "Start AutoCAD.",
                    }
                ]
            },
            "handles": [],
            "warnings": [],
            "next_tools": [],
        },
    )

    exit_code = doctor.main(["--check-autocad"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "BLOCKER" in output
    assert "Start AutoCAD" in output


def test_visual_optional_modules_satisfy_required_visual_preflight(monkeypatch, tmp_path):
    monkeypatch.setenv("CAD_MCP_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(utility_tools.platform, "system", lambda: "Windows")
    monkeypatch.setattr(utility_tools.platform, "platform", lambda: "Windows-UnitTest")
    monkeypatch.setattr(utility_tools, "_module_available", lambda name: True)
    monkeypatch.setattr(utility_tools.shutil, "which", lambda name: None)
    monkeypatch.setattr(utility_tools, "_first_existing_path", lambda paths: "")

    result = utility_tools.check_runtime_environment(
        check_autocad=False,
        require_visual_export=True,
    )

    assert result["ok"] is True
    visual = [
        check for check in result["data"]["checks"]
        if check["name"] == "visual_review_renderer"
    ][0]
    assert visual["detail"] == "python:cairosvg+Pillow"


def test_strict_startup_preflight_blocks(monkeypatch):
    from src import server

    monkeypatch.setenv("CAD_MCP_STRICT_PREFLIGHT", "1")
    monkeypatch.setenv("CAD_MCP_PREFLIGHT_CHECK_AUTOCAD", "0")
    monkeypatch.setattr(
        server.utility_tools,
        "check_runtime_environment",
        lambda **kwargs: {
            "ok": False,
            "data": {"blockers": [{"name": "workspace_root", "detail": "not writable"}]},
        },
    )

    with pytest.raises(RuntimeError, match="strict preflight failed"):
        server._run_strict_startup_preflight()


def test_pyproject_exposes_doctor_and_visual_extra():
    with open("pyproject.toml", "rb") as handle:
        config = tomllib.load(handle)

    assert config["project"]["scripts"]["cad-mcp-doctor"] == "src.doctor:main"
    assert "visual" in config["project"]["optional-dependencies"]
