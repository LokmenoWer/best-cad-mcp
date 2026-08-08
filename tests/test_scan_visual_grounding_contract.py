from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.cad_controller import CADController
from src.cad_database import CADDatabase
from src.cad_tools import query_tools
from src.cad_understanding.common import topology_for_handle
from src.cad_understanding.view_grounding import (
    apply_matrix_2d,
    export_view_image_with_mapping,
    ground_vlm_region,
)


class _FakeModelSpace:
    def __init__(self, entities):
        self._entities = list(entities)
        self.Count = len(self._entities)

    def Item(self, index):
        return self._entities[index]


class _VisualLine:
    ObjectName = "AcDbLine"
    Handle = "L1"
    Layer = "OUTLINE"
    Color = 256
    Linetype = "Continuous"
    StartPoint = (0.0, 10.0, 0.0)
    EndPoint = (10.0, 0.0, 0.0)
    Length = 14.142135624

    def GetBoundingBox(self):
        return ((0.0, 0.0, 0.0), (10.0, 10.0, 0.0))


class _VisualPolyline:
    ObjectName = "AcDbPolyline"
    Handle = "P1"
    Layer = "OUTLINE"
    Color = 256
    Linetype = "Continuous"
    Closed = True
    Length = 26.180339887
    # Six flat values are deliberately divisible by both two and three. The
    # entity type, not an ambiguous length heuristic, must select 2D stride.
    Coordinates = (20.0, 0.0, 30.0, 0.0, 30.0, 5.0)
    _bulges = (0.0, 0.5, -0.25)

    def GetBoundingBox(self):
        return ((20.0, 0.0, 0.0), (30.0, 5.0, 0.0))

    def GetBulge(self, index):
        return self._bulges[index]


class _Visual2dPolyline:
    ObjectName = "AcDb2dPolyline"
    Handle = "P2"
    Layer = "OUTLINE"
    Color = 256
    Linetype = "Continuous"
    Closed = False
    Length = 3.0
    Normal = (0.0, 0.0, 1.0)
    Elevation = 5.0
    # Legacy/heavy 2D POLYLINE coordinates use three values per OCS vertex;
    # the stored Z values are ignored in favor of Elevation.
    Coordinates = (1.0, 2.0, 99.0, 3.0, 4.0, 88.0)

    def GetBoundingBox(self):
        return ((1.0, 2.0, 5.0), (3.0, 4.0, 5.0))

    def GetBulge(self, index):
        return 0.0


def _controller_with_entities(*entities) -> CADController:
    document = MagicMock()
    document.ModelSpace = _FakeModelSpace(entities)
    controller = CADController()
    controller.acad = MagicMock()
    controller.acad.Documents.Count = 1
    controller.acad.ActiveDocument = document
    controller.doc = document
    return controller


def _database(tmp_path: Path) -> CADDatabase:
    database = CADDatabase(str(tmp_path / "scan-contract.db"))
    database.configure_context(
        workspace_root=str(tmp_path),
        conversation_id="scan-contract-conversation",
        thread_id="scan-contract-thread",
        drawing_name="scan-contract.dwg",
        drawing_path=str(tmp_path / "scan-contract.dwg"),
    )
    return database


def test_low_level_minimal_scan_default_remains_geometry_lightweight():
    class LeanLine(_VisualLine):
        @property
        def Color(self):
            raise AssertionError("minimal scan must not read display properties")

        @property
        def StartPoint(self):
            raise AssertionError("minimal scan must not read LINE geometry")

    class LeanPolyline(_VisualPolyline):
        @property
        def Color(self):
            raise AssertionError("minimal scan must not read display properties")

        @property
        def Coordinates(self):
            raise AssertionError("minimal scan must not read POLYLINE geometry")

    controller = _controller_with_entities(LeanLine(), LeanPolyline())

    with patch("src.cad_controller.win32com.client.Dispatch") as dispatch:
        result = controller.scan_model_space(
            max_entities=2,
            detail_level="minimal",
            include_bounding_boxes=True,
        )

    dispatch.assert_not_called()
    assert result["capture_visual_geometry"] is False
    by_handle = {item["handle"]: item for item in result["entities"]}
    assert by_handle["L1"]["bbox"] == [0.0, 0.0, 10.0, 10.0]
    assert by_handle["P1"]["bbox"] == [20.0, 0.0, 30.0, 5.0]
    for item in by_handle.values():
        assert "color" not in item
        assert "start" not in item
        assert "vertices" not in item
        assert "bulges" not in item


def test_minimal_visual_scan_captures_line_and_flat_polyline_paths():
    line = _VisualLine()
    polyline = _VisualPolyline()
    controller = _controller_with_entities(line, polyline)

    with patch(
        "src.cad_controller.win32com.client.Dispatch",
        side_effect=lambda entity: entity,
    ) as dispatch:
        result = controller.scan_model_space(
            max_entities=2,
            detail_level="minimal",
            include_bounding_boxes=True,
            capture_visual_geometry=True,
        )

    assert dispatch.call_count == 2
    assert result["detail_level"] == "minimal"
    assert result["capture_visual_geometry"] is True
    by_handle = {item["handle"]: item for item in result["entities"]}
    assert by_handle["L1"]["start"] == [0.0, 10.0, 0.0]
    assert by_handle["L1"]["end"] == [10.0, 0.0, 0.0]
    assert by_handle["P1"]["vertices"] == [
        [20.0, 0.0, 0.0],
        [30.0, 0.0, 0.0],
        [30.0, 5.0, 0.0],
    ]
    assert by_handle["P1"]["bulges"] == [0.0, 0.5, -0.25]


def test_heavy_2d_polyline_uses_three_value_ocs_stride_and_wcs_elevation():
    controller = _controller_with_entities(_Visual2dPolyline())

    with patch(
        "src.cad_controller.win32com.client.Dispatch",
        side_effect=lambda entity: entity,
    ):
        result = controller.scan_model_space(
            max_entities=1,
            detail_level="minimal",
            capture_visual_geometry=True,
        )

    polyline = result["entities"][0]
    assert polyline["vertices"] == [[1.0, 2.0, 5.0], [3.0, 4.0, 5.0]]
    assert polyline["vertices_coordinate_system"] == "WCS"
    assert polyline["normal"] == [0.0, 0.0, 1.0]
    assert polyline["elevation"] == 5.0
    assert CADController._scan_ocs_points_to_wcs(
        (2.0, 3.0), step=2, normal=(0.0, 1.0, 0.0), elevation=4.0
    ) == [[-2.0, 4.0, 3.0]]


def test_query_scan_persists_paths_for_export_and_region_grounding(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    database = _database(tmp_path)
    controller = _controller_with_entities(_VisualLine(), _VisualPolyline())

    with (
        patch.object(query_tools, "ctrl", controller),
        patch.object(query_tools, "db", database),
        patch.object(query_tools, "_sync_db_active_drawing"),
        patch(
            "src.cad_controller.win32com.client.Dispatch",
            side_effect=lambda entity: entity,
        ),
    ):
        scan_message = query_tools.scan_all_entities(
            clear_db=True,
            max_entities=2,
            detail_level="minimal",
            topology_detail="full",
        )

    assert "Boundary path geometry was captured" in scan_message
    line = database.get_entity("L1")
    polyline = database.get_entity("P1")
    assert line is not None
    assert polyline is not None
    assert line["geometry"]["start"] == [0.0, 10.0, 0.0]
    assert line["geometry"]["end"] == [10.0, 0.0, 0.0]
    assert polyline["geometry"]["vertices"] == [
        [20.0, 0.0, 0.0],
        [30.0, 0.0, 0.0],
        [30.0, 5.0, 0.0],
    ]
    assert polyline["geometry"]["bulges"] == [0.0, 0.5, -0.25]
    topology = topology_for_handle(database, "P1")
    bulge_curves = [
        primitive for primitive in topology["primitives"]
        if primitive.get("primitive_type") == "curve"
        and (primitive.get("properties") or {}).get("source") == "polyline_bulge"
    ]
    assert len(bulge_curves) == 2
    assert {primitive["properties"]["direction"] for primitive in bulge_curves} == {
        "ccw", "cw"
    }

    exported = export_view_image_with_mapping(
        filepath=str(tmp_path / "scan-contract.wmf"),
        include_overlay=True,
        overlay_granularity="both",
        database=database,
    )

    assert exported["ok"], exported
    snapshot = exported["data"]["snapshot"]
    entity_items = {
        item["handle"]: item for item in snapshot["entity_overlay_items"]
    }
    # The negative-slope LINE path proves grounding did not invent the other
    # diagonal from its axis-aligned bounding box.
    assert entity_items["L1"]["world_path"] == [
        [0.0, 10.0],
        [10.0, 0.0],
    ]
    polyline_path = entity_items["P1"]["world_path"]
    assert polyline_path[0] == [20.0, 0.0]
    assert polyline_path[-1] == [20.0, 0.0]
    assert [30.0, 0.0] in polyline_path
    assert [30.0, 5.0] in polyline_path
    assert len(polyline_path) > 4
    # The positive bulge on the vertical segment bows beyond its x=30 chord.
    assert max(point[0] for point in polyline_path) > 30.0
    bulge_primitive_items = [
        item for item in snapshot["primitive_overlay_items"]
        if item.get("handle") == "P1" and item.get("role") == "arc"
    ]
    assert len(bulge_primitive_items) == 2
    assert all(len(item["world_path"]) > 8 for item in bulge_primitive_items)
    assert all(item["world_bbox"]["width"] > 0 for item in bulge_primitive_items)
    sidecar = json.loads(Path(snapshot["overlay_items_path"]).read_text(
        encoding="utf-8"
    ))
    sidecar_entities = {
        item["handle"]: item
        for item in sidecar["overlay_items"]
        if item.get("item_kind") == "entity"
    }
    assert sidecar_entities["L1"]["pixel_path"] == entity_items["L1"][
        "pixel_path"
    ]

    line_center = apply_matrix_2d(snapshot["world_to_pixel"], 5.0, 5.0)
    grounded = ground_vlm_region(
        snapshot["snapshot_id"],
        [
            line_center[0] - 3.0,
            line_center[1] - 3.0,
            line_center[0] + 3.0,
            line_center[1] + 3.0,
        ],
        database=database,
    )

    assert grounded["ok"], grounded
    top_line = next(
        item for item in grounded["data"]["candidates"]
        if item.get("handle") == "L1"
    )
    assert top_line["support_mode"] == "path"
    assert top_line["evidence"]["spatial_support"]["path_distance_px"] == 0.0
