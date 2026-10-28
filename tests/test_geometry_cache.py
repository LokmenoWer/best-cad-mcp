from unittest.mock import MagicMock

from src.cad_database import CADDatabase
from src.cad_tools import edit_tools
from src.cad_understanding.plan import parse_tool_result_handles


def test_invalidate_clears_coordinates_bbox_and_topology(tmp_path):
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.upsert_entity(
        "AB12",
        "Line",
        "AcDbLine",
        geometry={"start_point": [0, 0, 0], "end_point": [10, 0, 0]},
        properties={"source": "scan"},
    )

    original = db.get_entity("AB12")
    assert original["geometry"]["end_point"] == [10, 0, 0]
    assert original["bbox_min_x"] == 0
    assert db.get_entity_topology("AB12")["primitives"]

    assert db.invalidate_entity_geometry("AB12") == 1
    assert db.invalidate_entity_geometry("MISSING") == 0

    stale = db.get_entity("AB12")
    assert stale["geometry"] == {}
    assert stale["properties"]["geometry_stale"] is True
    assert stale["properties"]["source"] == "scan"
    assert stale["bbox_min_x"] is None
    assert stale["bbox_max_x"] is None
    assert db.get_entity_topology("AB12")["primitives"] == []
    assert db.get_entity("MISSING") is None


def test_invalidate_without_handles_covers_the_drawing_scope(tmp_path):
    db = CADDatabase(str(tmp_path / "cad.db"))
    db.upsert_entity("A1", "Line", "AcDbLine", geometry={"start_point": [0, 0, 0], "end_point": [1, 0, 0]})
    db.upsert_entity("B2", "Line", "AcDbLine", geometry={"start_point": [0, 1, 0], "end_point": [1, 1, 0]})

    assert db.invalidate_entity_geometry() == 2

    assert db.get_entity("A1")["geometry"] == {}
    assert db.get_entity("B2")["properties"]["geometry_stale"] is True


def test_move_marks_cached_geometry_stale(monkeypatch):
    ctrl = MagicMock()
    ctrl.move_entity.return_value = {"success": True}
    db = MagicMock()
    monkeypatch.setattr(edit_tools, "ctrl", ctrl)
    monkeypatch.setattr(edit_tools, "db", db)

    result = edit_tools.move_entity("16E", [0, 0, 0], [-5, 0, 0])

    assert parse_tool_result_handles(result) == ["16E"]
    assert "geometry_cache: stale" in result
    db.invalidate_entity_geometry.assert_called_once_with(["16E"])
    db.upsert_entity.assert_not_called()


def test_failed_move_leaves_the_cache_untouched(monkeypatch):
    ctrl = MagicMock()
    ctrl.move_entity.return_value = {"success": False, "message": "busy"}
    db = MagicMock()
    monkeypatch.setattr(edit_tools, "ctrl", ctrl)
    monkeypatch.setattr(edit_tools, "db", db)

    result = edit_tools.move_entity("16E", [0, 0, 0], [-5, 0, 0])

    assert parse_tool_result_handles(result) == []
    db.invalidate_entity_geometry.assert_not_called()
    db.upsert_entity.assert_not_called()


def test_copy_move_failure_is_not_success_and_does_not_cache_source_geometry(monkeypatch):
    ctrl = MagicMock()
    ctrl.copy_entity.return_value = {"success": True, "new_handle": "20A"}
    ctrl.move_entity.return_value = {"success": False, "message": "busy"}
    db = MagicMock()
    monkeypatch.setattr(edit_tools, "ctrl", ctrl)
    monkeypatch.setattr(edit_tools, "db", db)

    result = edit_tools.copy_entity("16E", [0, 0, 0], [5, 0, 0])

    assert result.startswith("OK:") is False
    assert "失败" in result
    assert "20A" in result
    db.upsert_entity.assert_not_called()
    ctrl.move_entity.assert_called_once_with("20A", [0, 0, 0], [5, 0, 0])


def test_moved_copy_does_not_store_the_unmoved_geometry(monkeypatch):
    ctrl = MagicMock()
    ctrl.copy_entity.return_value = {"success": True, "new_handle": "20A"}
    ctrl.move_entity.return_value = {"success": True}
    db = MagicMock()
    monkeypatch.setattr(edit_tools, "ctrl", ctrl)
    monkeypatch.setattr(edit_tools, "db", db)

    result = edit_tools.copy_entity("16E", [0, 0, 0], [5, 0, 0])

    assert result.startswith("OK:")
    assert parse_tool_result_handles(result) == ["16E", "20A"]
    db.upsert_entity.assert_not_called()


def test_plain_copy_stores_the_source_geometry(monkeypatch):
    ctrl = MagicMock()
    ctrl.copy_entity.return_value = {"success": True, "new_handle": "20A"}
    db = MagicMock()
    db.get_entity.return_value = {
        "name": "Line",
        "type": "AcDbLine",
        "layer": "0",
        "color": 1,
        "linetype": "ByLayer",
        "properties": {"source": "scan"},
        "geometry": {"start_point": [0, 0, 0], "end_point": [1, 0, 0]},
    }
    monkeypatch.setattr(edit_tools, "ctrl", ctrl)
    monkeypatch.setattr(edit_tools, "db", db)

    result = edit_tools.copy_entity("16E")

    assert result.startswith("OK:")
    kwargs = db.upsert_entity.call_args.kwargs
    assert kwargs["geometry"] == {"start_point": [0, 0, 0], "end_point": [1, 0, 0]}
    assert db.upsert_entity.call_args.args[0] == "20A"


def test_stretch_rejects_injected_coordinates_before_sending_a_command(monkeypatch):
    ctrl = MagicMock()
    db = MagicMock()
    monkeypatch.setattr(edit_tools, "ctrl", ctrl)
    monkeypatch.setattr(edit_tools, "db", db)

    result = edit_tools.stretch_entities(0, 0, 10, '1"); (command "ERASE")', 0, 0, 0, 1, 0, 0)

    assert "失败" in result
    ctrl.send_command.assert_not_called()
    db.invalidate_entity_geometry.assert_not_called()
