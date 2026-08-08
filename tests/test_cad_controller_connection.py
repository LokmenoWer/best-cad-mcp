from unittest.mock import MagicMock, patch

from src.cad_controller import CADController


def _fresh_controller() -> CADController:
    CADController._instance = None
    return CADController()


def test_connect_falls_back_to_registered_versioned_progid():
    controller = _fresh_controller()
    application = MagicMock()
    application.Documents.Count = 1
    application.ActiveDocument = MagicMock()

    def get_active_object(prog_id: str):
        if prog_id == "AutoCAD.Application.23.1":
            return application
        raise OSError(f"not registered: {prog_id}")

    with patch.object(
        controller,
        "_autocad_prog_id_candidates",
        return_value=["AutoCAD.Application", "AutoCAD.Application.23.1"],
    ), patch(
        "src.cad_controller.win32com.client.GetActiveObject",
        side_effect=get_active_object,
        create=True,
    ) as get_active:
        assert controller.connect() is True

    assert controller.acad is application
    assert controller.doc is application.ActiveDocument
    assert [call.args[0] for call in get_active.call_args_list] == [
        "AutoCAD.Application",
        "AutoCAD.Application.23.1",
    ]


def test_autocad_progid_override_is_tried_first(monkeypatch):
    monkeypatch.setenv("CAD_MCP_AUTOCAD_PROGID", "AutoCAD.Application.23.1")
    candidates = CADController._autocad_prog_id_candidates()
    assert candidates[0] == "AutoCAD.Application.23.1"
    assert "AutoCAD.Application" in candidates


def test_has_document_refreshes_a_stale_cross_thread_proxy():
    controller = _fresh_controller()

    class StaleApplication:
        @property
        def Documents(self):
            raise RuntimeError("COM proxy belongs to another thread")

    live_application = MagicMock()
    live_application.Documents.Count = 1
    controller.acad = StaleApplication()

    def refresh():
        controller.acad = live_application
        controller.doc = MagicMock()

    with patch.object(controller, "_refresh_active_document", side_effect=refresh) as refresh_active:
        assert controller.has_document is True

    refresh_active.assert_called_once_with()
