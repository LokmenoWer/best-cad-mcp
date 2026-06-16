import importlib
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch


def install_com_mocks():
    mock_win32com = types.ModuleType("win32com")
    mock_win32com_client = types.ModuleType("win32com.client")
    mock_pythoncom = types.ModuleType("pythoncom")
    mock_pythoncom.VT_ARRAY = 0x2000
    mock_pythoncom.VT_R8 = 5
    mock_pythoncom.VT_I2 = 2
    mock_pythoncom.VT_I4 = 3
    mock_pythoncom.VT_VARIANT = 12
    mock_pythoncom.VT_DISPATCH = 9
    mock_win32com_client.VARIANT = lambda vt, val: val
    mock_win32com_client.Dispatch = MagicMock(return_value=MagicMock())
    mock_win32com.client = mock_win32com_client
    sys.modules["win32com"] = mock_win32com
    sys.modules["win32com.client"] = mock_win32com_client
    sys.modules["pythoncom"] = mock_pythoncom


def test_cad_understanding_modules_do_not_import_com_directly():
    root = Path(__file__).resolve().parents[1] / "src" / "cad_understanding"

    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "win32com" not in text
        assert "pythoncom" not in text


def test_server_understanding_tool_functions_importable():
    install_com_mocks()
    with patch("src.cad_controller.CADController", autospec=True) as mock_ctrl_cls, patch(
        "src.cad_database.CADDatabase", autospec=True
    ) as mock_db_cls:
        mock_ctrl_cls.return_value = MagicMock()
        mock_db_cls.return_value = MagicMock()
        server = importlib.import_module("src.server")

    for name in [
        "build_drawing_ir",
        "summarize_drawing",
        "explain_entity",
        "find_entities_by_description",
        "analyze_drawing_intent",
        "detect_semantic_objects",
        "get_semantic_graph",
        "find_semantic_objects",
        "extract_drawing_constraints",
        "check_drawing_constraints",
        "get_drawing_constraints",
        "validate_geometry",
        "get_validation_report",
        "propose_repair_plan",
        "export_view_image_with_mapping",
        "ground_vlm_region",
        "validate_cad_plan",
        "dry_run_cad_plan",
        "execute_cad_plan",
        "list_cad_resources",
        "get_cad_resource",
    ]:
        assert callable(getattr(server, name))
