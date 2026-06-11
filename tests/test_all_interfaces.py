"""
Comprehensive interface test suite for CAD MCP Server.

Tests all tool function signatures, module imports, and cross-module consistency.
Designed to run WITHOUT AutoCAD installed — uses mocking for COM-dependent parts.

Usage:
    cd best-cad-mcp
    python -m pytest tests/test_all_interfaces.py -v --tb=short

Or simply:
    python tests/test_all_interfaces.py
"""

import sys
import os
import json
import inspect
import unittest
from unittest.mock import MagicMock, patch, PropertyMock
from typing import get_type_hints, Optional, List, Dict, Any, Tuple

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Mock win32com before any imports ──────────────────────────────
import types

# Create mock modules
mock_win32com = types.ModuleType('win32com')
mock_win32com_client = types.ModuleType('win32com.client')
mock_pythoncom = types.ModuleType('pythoncom')

# Mock constants
mock_pythoncom.VT_ARRAY = 0x2000
mock_pythoncom.VT_R8 = 5
mock_pythoncom.VT_I2 = 2
mock_pythoncom.VT_VARIANT = 12
mock_pythoncom.VT_DISPATCH = 9

def mock_variant(vt, val):
    return val

mock_win32com_client.VARIANT = mock_variant
mock_win32com.client = mock_win32com_client
mock_win32com_client.Dispatch = MagicMock(return_value=MagicMock())

sys.modules['win32com'] = mock_win32com
sys.modules['win32com.client'] = mock_win32com_client
sys.modules['pythoncom'] = mock_pythoncom

# ── Now we can import project modules ─────────────────────────────

# We need to mock the controller and database before server import
# because server.py initializes them at module level.

# Patch the controller and database getters
with patch('src.cad_controller.CADController', autospec=True) as mock_ctrl_cls, \
     patch('src.cad_database.CADDatabase', autospec=True) as mock_db_cls:

    mock_ctrl = MagicMock()
    mock_ctrl.has_document = True
    mock_db = MagicMock()
    mock_ctrl_cls.return_value = mock_ctrl
    mock_db_cls.return_value = mock_db

    # Import tool modules
    from src.cad_tools import drawing_tools
    from src.cad_tools import edit_tools
    from src.cad_tools import layer_tools
    from src.cad_tools import text_tools
    from src.cad_tools import dimension_tools
    from src.cad_tools import block_tools
    from src.cad_tools import view_tools
    from src.cad_tools import query_tools
    from src.cad_tools import file_tools
    from src.cad_tools import utility_tools
    from src.cad_tools import solid_tools
    from src.cad_tools import advanced_tools
    from src.cad_tools import polyline_tools
    from src.cad_tools import hatch_tools
    from src.cad_tools import attribute_tools

    # Import shared modules
    from src import cad_utils
    from src import cad_data_model


# ══════════════════════════════════════════════════════════════════
#  Test: Module-level imports and function existence
# ══════════════════════════════════════════════════════════════════

class TestModuleImports(unittest.TestCase):
    """Verify all tool modules can be imported and have expected functions."""

    def test_drawing_tools_functions(self):
        expected = [
            'create_new_drawing', 'open_drawing', 'save_drawing', 'close_drawing',
            'draw_line', 'draw_circle', 'draw_arc', 'draw_ellipse',
            'draw_polyline', 'draw_3d_polyline', 'draw_rectangle', 'draw_polygon',
            'draw_spline', 'draw_point', 'draw_text', 'draw_mtext',
            'draw_donut', 'draw_ray', 'draw_xline', 'draw_mline',
            'draw_2d_solid', 'draw_raster_image', 'draw_tolerance',
            'draw_trace', 'insert_minert_block', 'add_shape',
        ]
        for name in expected:
            with self.subTest(name=name):
                self.assertTrue(hasattr(drawing_tools, name), f"Missing: {name}")
                self.assertTrue(callable(getattr(drawing_tools, name)), f"Not callable: {name}")

    def test_edit_tools_functions(self):
        expected = [
            'move_entity', 'rotate_entity', 'copy_entity',
            'delete_entity', 'delete_entities', 'mirror_entity',
            'scale_entity', 'offset_entity', 'array_rectangular',
            'array_polar', 'explode_entity', 'set_entity_properties',
            'get_entity_properties', 'set_entity_truecolor',
            'set_entity_transparency', 'set_entity_plot_style',
            'get_extension_dictionary', 'fillet_entities', 'chamfer_entities',
            'trim_entity', 'extend_entity', 'break_entity',
            'join_entities', 'stretch_entities', 'lengthen_entity',
        ]
        for name in expected:
            with self.subTest(name=name):
                self.assertTrue(hasattr(edit_tools, name), f"Missing: {name}")
                self.assertTrue(callable(getattr(edit_tools, name)), f"Not callable: {name}")

    def test_layer_tools_functions(self):
        expected = [
            'create_layer', 'delete_layer', 'rename_layer',
            'freeze_layer', 'thaw_layer', 'lock_layer', 'unlock_layer',
            'turn_off_layer', 'turn_on_layer', 'set_current_layer',
            'get_all_layers', 'isolate_layer', 'unisolate_layers',
            'save_layers_to_db',
        ]
        for name in expected:
            with self.subTest(name=name):
                self.assertTrue(hasattr(layer_tools, name), f"Missing: {name}")
                self.assertTrue(callable(getattr(layer_tools, name)), f"Not callable: {name}")

    def test_text_tools_functions(self):
        expected = [
            'create_text_style', 'set_current_text_style', 'get_text_styles',
            'add_leader', 'add_mleader', 'add_table', 'edit_table_cell',
            'find_text', 'replace_text',
        ]
        for name in expected:
            with self.subTest(name=name):
                self.assertTrue(hasattr(text_tools, name), f"Missing: {name}")
                self.assertTrue(callable(getattr(text_tools, name)), f"Not callable: {name}")

    def test_dimension_tools_functions(self):
        expected = [
            'add_linear_dimension', 'add_rotated_dimension',
            'add_angular_dimension', 'add_radial_dimension',
            'add_diametric_dimension', 'add_ordinate_dimension',
            'get_dimension_styles', 'set_current_dimension_style',
            'copy_dimension_style', 'add_qdim',
            'add_baseline_dimension', 'add_continue_dimension',
            'draw_wipeout', 'add_arc_dimension',
            'add_3point_angular_dimension', 'set_dimension_text_override',
            'get_dimension_measurement', 'set_text_alignment',
            'set_text_properties',
        ]
        for name in expected:
            with self.subTest(name=name):
                self.assertTrue(hasattr(dimension_tools, name), f"Missing: {name}")
                self.assertTrue(callable(getattr(dimension_tools, name)), f"Not callable: {name}")

    def test_block_tools_functions(self):
        expected = [
            'create_block', 'insert_block', 'get_all_blocks',
            'explode_block', 'attach_xref', 'get_xrefs',
            'unload_xref', 'reload_xref',
        ]
        for name in expected:
            with self.subTest(name=name):
                self.assertTrue(hasattr(block_tools, name), f"Missing: {name}")
                self.assertTrue(callable(getattr(block_tools, name)), f"Not callable: {name}")

    def test_view_tools_functions(self):
        expected = [
            'zoom_extents', 'zoom_window', 'zoom_center',
            'zoom_scale', 'zoom_previous', 'zoom_all', 'pan',
            'get_current_view', 'get_layouts', 'set_active_layout',
            'create_layout',
        ]
        for name in expected:
            with self.subTest(name=name):
                self.assertTrue(hasattr(view_tools, name), f"Missing: {name}")
                self.assertTrue(callable(getattr(view_tools, name)), f"Not callable: {name}")

    def test_query_tools_functions(self):
        expected = [
            'scan_all_entities', 'scan_entities_in_area',
            'select_by_window', 'select_by_crossing', 'select_all',
            'highlight_entity', 'highlight_entities',
            'reset_entity_color', 'highlight_query_results',
            'get_entity_statistics',
        ]
        for name in expected:
            with self.subTest(name=name):
                self.assertTrue(hasattr(query_tools, name), f"Missing: {name}")
                self.assertTrue(callable(getattr(query_tools, name)), f"Not callable: {name}")

    def test_file_tools_functions(self):
        expected = [
            'get_document_info', 'export_pdf', 'export_dxf',
            'export_dwf', 'export_image', 'purge_drawing',
            'audit_drawing', 'undo', 'redo', 'regen',
            'send_command', 'get_variable', 'set_variable',
            'measure_distance', 'create_snapshot', 'get_snapshots',
        ]
        for name in expected:
            with self.subTest(name=name):
                self.assertTrue(hasattr(file_tools, name), f"Missing: {name}")
                self.assertTrue(callable(getattr(file_tools, name)), f"Not callable: {name}")

    def test_utility_tools_functions(self):
        expected = [
            'get_all_tables', 'get_table_schema', 'execute_query',
            'execute_sql_query', 'create_group', 'get_all_groups',
            'add_hatch', 'angle_to_real', 'angle_to_string',
            'distance_to_real', 'real_to_string', 'select_on_screen',
            'delete_selection_set', 'clear_selection_set', 'get_tool_help',
        ]
        for name in expected:
            with self.subTest(name=name):
                self.assertTrue(hasattr(utility_tools, name), f"Missing: {name}")
                self.assertTrue(callable(getattr(utility_tools, name)), f"Not callable: {name}")

    def test_solid_tools_functions(self):
        expected = [
            'draw_box', 'draw_cone', 'draw_cylinder', 'draw_sphere',
            'draw_torus', 'draw_wedge', 'draw_elliptical_cone',
            'draw_elliptical_cylinder', 'add_region', 'extrude_region',
            'extrude_region_along_path', 'revolve_region',
            'solid_boolean', 'check_interference', 'slice_solid',
            'section_solid', 'draw_3d_mesh', 'draw_polyface_mesh',
            'draw_3d_face', 'rotate_3d', 'mirror_3d',
            'get_bounding_box', 'intersect_with', 'transform_entity',
        ]
        for name in expected:
            with self.subTest(name=name):
                self.assertTrue(hasattr(solid_tools, name), f"Missing: {name}")
                self.assertTrue(callable(getattr(solid_tools, name)), f"Not callable: {name}")

    def test_advanced_tools_functions(self):
        expected = [
            'add_hyperlink', 'get_hyperlinks', 'remove_hyperlink',
            'get_xdata', 'set_xdata', 'create_ucs', 'get_all_ucs',
            'set_active_ucs', 'get_active_ucs', 'save_named_view',
            'restore_named_view', 'get_named_views', 'delete_named_view',
            'add_viewport', 'get_viewports', 'set_viewport_properties',
            'plot_to_device', 'plot_to_file', 'plot_preview',
            'get_plot_devices', 'get_plot_style_tables',
            'get_plot_configurations', 'create_material', 'get_materials',
            'set_entity_material', 'set_active_material',
            'load_linetype', 'get_linetypes', 'polar_point',
            'translate_coordinates', 'angle_from_xaxis',
            'format_angle', 'format_distance', 'get_preference',
            'set_preference', 'get_preferences_display',
            'get_preferences_drafting', 'get_preferences_files',
            'get_preferences_opensave', 'get_preferences_selection',
            'get_preferences_system', 'get_preferences_user',
            'get_application_info', 'is_autocad_idle',
            'set_document_properties', 'set_drawing_password',
            'get_file_dependencies', 'get_active_space_info',
            'select_by_fence', 'select_by_wpolygon',
            'select_by_cpolygon', 'select_at_point',
            'create_registered_application', 'get_registered_applications',
            'get_dictionaries',
        ]
        for name in expected:
            with self.subTest(name=name):
                self.assertTrue(hasattr(advanced_tools, name), f"Missing: {name}")
                self.assertTrue(callable(getattr(advanced_tools, name)), f"Not callable: {name}")

    def test_polyline_tools_functions(self):
        expected = [
            'polyline_set_bulge', 'polyline_get_bulge',
            'polyline_set_width', 'polyline_get_width',
            'polyline_add_vertex', 'polyline_constant_width',
            'polyline_num_vertices', 'polyline_get_point_at_param',
            'polyline_get_segment_type',
        ]
        for name in expected:
            with self.subTest(name=name):
                self.assertTrue(hasattr(polyline_tools, name), f"Missing: {name}")
                self.assertTrue(callable(getattr(polyline_tools, name)), f"Not callable: {name}")

    def test_hatch_tools_functions(self):
        expected = [
            'hatch_add_boundary', 'hatch_add_inner_loop',
            'hatch_set_properties', 'hatch_get_properties',
            'hatch_set_gradient',
        ]
        for name in expected:
            with self.subTest(name=name):
                self.assertTrue(hasattr(hatch_tools, name), f"Missing: {name}")
                self.assertTrue(callable(getattr(hatch_tools, name)), f"Not callable: {name}")

    def test_attribute_tools_functions(self):
        expected = [
            'insert_block_with_attributes', 'get_block_attributes',
            'set_block_attribute',
        ]
        for name in expected:
            with self.subTest(name=name):
                self.assertTrue(hasattr(attribute_tools, name), f"Missing: {name}")
                self.assertTrue(callable(getattr(attribute_tools, name)), f"Not callable: {name}")


# ══════════════════════════════════════════════════════════════════
#  Test: Function signatures (parameter count and types)
# ══════════════════════════════════════════════════════════════════

class TestFunctionSignatures(unittest.TestCase):
    """Verify function signatures have correct parameter counts."""

    def _get_params(self, func):
        sig = inspect.signature(func)
        return list(sig.parameters.keys())

    def test_drawing_function_sigs(self):
        # draw_line should have: start_x, start_y, end_x, end_y, start_z, end_z, layer, color
        params = self._get_params(drawing_tools.draw_line)
        self.assertIn('start_x', params)
        self.assertIn('end_x', params)
        self.assertIn('layer', params)

        # draw_circle: center_x, center_y, radius, layer, color
        params = self._get_params(drawing_tools.draw_circle)
        self.assertIn('center_x', params)
        self.assertIn('radius', params)

        # create_new_drawing: template
        params = self._get_params(drawing_tools.create_new_drawing)
        self.assertIn('template', params)

    def test_edit_function_sigs(self):
        params = self._get_params(edit_tools.move_entity)
        self.assertIn('handle', params)
        self.assertIn('from_point', params)
        self.assertIn('to_point', params)

        params = self._get_params(edit_tools.delete_entity)
        self.assertIn('handle', params)

        params = self._get_params(edit_tools.delete_entities)
        self.assertIn('handles', params)

    def test_layer_function_sigs(self):
        params = self._get_params(layer_tools.create_layer)
        self.assertIn('name', params)
        self.assertIn('color', params)
        self.assertIn('linetype', params)

    def test_solid_function_sigs(self):
        params = self._get_params(solid_tools.draw_box)
        self.assertIn('center_x', params)
        self.assertIn('length', params)
        self.assertIn('width', params)
        self.assertIn('height', params)

        params = self._get_params(solid_tools.solid_boolean)
        self.assertIn('target_handle', params)
        self.assertIn('tool_handle', params)
        self.assertIn('operation', params)


# ══════════════════════════════════════════════════════════════════
#  Test: Server.py @mcp.tool() coverage
# ══════════════════════════════════════════════════════════════════

class TestServerToolCoverage(unittest.TestCase):
    """Verify all tool module functions are exposed as @mcp.tool() in server.py."""

    @classmethod
    def setUpClass(cls):
        """Parse server.py to find all @mcp.tool() decorated functions."""
        server_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'src', 'server.py'
        )
        with open(server_path, 'r', encoding='utf-8') as f:
            cls.server_content = f.read()

    def _find_tool_names(self):
        """Extract all tool function names from server.py."""
        import re
        # Find all @mcp.tool() decorated function names
        pattern = r'def\s+(\w+)\s*\(.*?ctx:\s*Context'
        matches = re.findall(pattern, self.server_content)
        return set(matches)

    # Known imports that are not tool functions
    _utility_imports = {
        'ctrl', 'db', 'get_controller', 'get_database', 'format_success',
        'resolve_color', 'to_variant_point', 'List', 'Dict', 'Optional',
        'Tuple', 'Any', 'json', 'os', 'importlib', 'math', 'pythoncom',
        'win32com',
    }

    def _find_tool_module_functions(self, module, exclude=None):
        """Get all public tool function names from a tool module."""
        if exclude is None:
            exclude = set()
        funcs = []
        for name in dir(module):
            if name.startswith('_'):
                continue
            if name in self._utility_imports:
                continue
            if name in exclude:
                continue
            obj = getattr(module, name)
            if callable(obj) and not isinstance(obj, type):
                funcs.append(name)
        return set(funcs)

    def test_server_has_drawing_tools(self):
        tool_names = self._find_tool_names()
        module_funcs = self._find_tool_module_functions(drawing_tools)
        # All drawing functions should be registered
        for func in module_funcs:
            with self.subTest(func=func):
                self.assertIn(func, tool_names,
                    f"drawing_tools.{func} NOT exposed as @mcp.tool() in server.py")

    def test_server_has_edit_tools(self):
        tool_names = self._find_tool_names()
        module_funcs = self._find_tool_module_functions(edit_tools)
        for func in module_funcs:
            with self.subTest(func=func):
                self.assertIn(func, tool_names,
                    f"edit_tools.{func} NOT exposed as @mcp.tool() in server.py")

    def test_server_has_layer_tools(self):
        tool_names = self._find_tool_names()
        module_funcs = self._find_tool_module_functions(layer_tools)
        # save_layers_to_db is a utility function, not a tool
        for func in module_funcs - {'save_layers_to_db'}:
            with self.subTest(func=func):
                self.assertIn(func, tool_names,
                    f"layer_tools.{func} NOT exposed as @mcp.tool() in server.py")

    def test_server_has_polyline_tools(self):
        tool_names = self._find_tool_names()
        module_funcs = self._find_tool_module_functions(polyline_tools)
        for func in module_funcs:
            with self.subTest(func=func):
                self.assertIn(func, tool_names,
                    f"polyline_tools.{func} NOT exposed as @mcp.tool() in server.py")

    def test_server_has_hatch_tools(self):
        tool_names = self._find_tool_names()
        module_funcs = self._find_tool_module_functions(hatch_tools)
        for func in module_funcs:
            with self.subTest(func=func):
                self.assertIn(func, tool_names,
                    f"hatch_tools.{func} NOT exposed as @mcp.tool() in server.py")

    def test_server_has_attribute_tools(self):
        tool_names = self._find_tool_names()
        module_funcs = self._find_tool_module_functions(attribute_tools)
        for func in module_funcs:
            with self.subTest(func=func):
                self.assertIn(func, tool_names,
                    f"attribute_tools.{func} NOT exposed as @mcp.tool() in server.py")

    def test_server_has_solid_tools(self):
        tool_names = self._find_tool_names()
        module_funcs = self._find_tool_module_functions(solid_tools)
        for func in module_funcs:
            with self.subTest(func=func):
                self.assertIn(func, tool_names,
                    f"solid_tools.{func} NOT exposed as @mcp.tool() in server.py")

    def test_total_tool_count(self):
        """Verify we have 100+ tools exposed."""
        tool_names = self._find_tool_names()
        # Exclude prompts which also match
        self.assertGreater(len(tool_names), 100,
            f"Expected 100+ tools, got {len(tool_names)}")


# ══════════════════════════════════════════════════════════════════
#  Test: Data model integrity
# ══════════════════════════════════════════════════════════════════

class TestDataModels(unittest.TestCase):
    """Verify data model classes are correctly defined."""

    def test_entity_type_mapping(self):
        from src.cad_data_model import EntityType
        # Verify key mappings
        self.assertEqual(EntityType.from_object_name("AcDbLine"), EntityType.LINE)
        self.assertEqual(EntityType.from_object_name("AcDbCircle"), EntityType.CIRCLE)
        self.assertEqual(EntityType.from_object_name("AcDbArc"), EntityType.ARC)
        self.assertEqual(EntityType.from_object_name("AcDbPolyline"), EntityType.POLYLINE)
        self.assertEqual(EntityType.from_object_name("AcDb3dPolyline"), EntityType.POLYLINE_3D)
        self.assertEqual(EntityType.from_object_name("AcDbBlockReference"), EntityType.BLOCK_REF)
        self.assertEqual(EntityType.from_object_name("UnknownType"), EntityType.UNKNOWN)

    def test_point3d(self):
        from src.cad_data_model import Point3D
        p1 = Point3D(1, 2, 3)
        p2 = Point3D(4, 6, 8)
        self.assertAlmostEqual(p1.distance_to(p2), 7.071, places=2)

        mid = p1.midpoint(p2)
        self.assertEqual(mid.x, 2.5)
        self.assertEqual(mid.y, 4.0)

    def test_bounding_box(self):
        from src.cad_data_model import BoundingBox, Point3D
        bb = BoundingBox(Point3D(0, 0, 0), Point3D(10, 20, 30))
        self.assertEqual(bb.width, 10)
        self.assertEqual(bb.height, 20)
        self.assertTrue(bb.contains(Point3D(5, 10, 15)))
        self.assertFalse(bb.contains(Point3D(15, 25, 35)))

    def test_cad_document_to_dict(self):
        from src.cad_data_model import CADDocument
        doc = CADDocument(name="test.dwg", entity_count=42)
        d = doc.to_dict()
        self.assertEqual(d["name"], "test.dwg")
        self.assertEqual(d["entity_count"], 42)

    def test_color_resolution(self):
        # Test ACI color mapping
        self.assertEqual(cad_utils.resolve_color("red"), 1)
        self.assertEqual(cad_utils.resolve_color("blue"), 5)
        self.assertEqual(cad_utils.resolve_color("bylayer"), 256)
        self.assertEqual(cad_utils.resolve_color("unknown"), 7)  # default white
        self.assertEqual(cad_utils.resolve_color(3), 3)

    def test_geometry_helpers(self):
        self.assertAlmostEqual(cad_utils.distance_2d(0, 0, 3, 4), 5.0)
        self.assertAlmostEqual(cad_utils.distance_3d([0, 0, 0], [1, 2, 2]), 3.0)
        mid = cad_utils.midpoint(0, 0, 10, 10)
        self.assertEqual(mid, (5.0, 5.0))


# ══════════════════════════════════════════════════════════════════
#  Test: Database operations
# ══════════════════════════════════════════════════════════════════

class TestDatabase(unittest.TestCase):
    """Test the SQLite database layer directly (no COM needed)."""

    def setUp(self):
        import tempfile
        from src.cad_database import CADDatabase
        self.tmpfile = os.path.join(tempfile.gettempdir(), 'test_cad_mcp.db')
        self.db = CADDatabase(self.tmpfile)

    def tearDown(self):
        try:
            os.remove(self.tmpfile)
        except Exception:
            pass

    def test_get_tables(self):
        tables = self.db.get_tables()
        self.assertIn('cad_entities', tables)
        self.assertIn('cad_layers', tables)
        self.assertIn('cad_blocks', tables)
        self.assertIn('text_patterns', tables)
        self.assertIn('query_history', tables)
        self.assertIn('drawing_snapshots', tables)

    def test_upsert_and_get_entity(self):
        self.db.upsert_entity(
            handle="TEST001", name="TestLine", entity_type="AcDbLine",
            layer="0", color=256,
            geometry={"start_point": [0, 0, 0], "end_point": [10, 10, 0]}
        )
        ent = self.db.get_entity("TEST001")
        self.assertIsNotNone(ent)
        self.assertEqual(ent["name"], "TestLine")
        self.assertEqual(ent["type"], "AcDbLine")
        geom = ent.get("geometry", {})
        self.assertEqual(geom["start_point"], [0, 0, 0])

    def test_query_entities(self):
        self.db.clear_entities()
        self.db.upsert_entity("H1", "Line1", "AcDbLine", layer="WALL", color=1)
        self.db.upsert_entity("H2", "Circle1", "AcDbCircle", layer="WALL", color=3)
        self.db.upsert_entity("H3", "Text1", "AcDbText", layer="TEXT", color=5)

        results = self.db.query_entities(layer="WALL")
        self.assertEqual(len(results), 2)

        results = self.db.query_entities(entity_type="AcDbCircle")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["handle"], "H2")

    def test_clear_entities(self):
        self.db.upsert_entity("H4", "Test", "AcDbLine")
        self.db.clear_entities()
        self.assertIsNone(self.db.get_entity("H4"))

    def test_type_stats(self):
        self.db.clear_entities()
        self.db.upsert_entity("H1", "L1", "AcDbLine")
        self.db.upsert_entity("H2", "L2", "AcDbLine")
        self.db.upsert_entity("H3", "C1", "AcDbCircle")
        stats = self.db.get_type_stats()
        self.assertEqual(stats.get("AcDbLine"), 2)
        self.assertEqual(stats.get("AcDbCircle"), 1)

    def test_layer_save_and_get(self):
        self.db.save_layers([
            {"name": "WALL", "color": 1, "linetype": "Continuous", "is_on": True},
            {"name": "HIDDEN", "color": 8, "linetype": "Dashed", "is_frozen": True},
        ])
        layers = self.db.get_layers()
        self.assertEqual(len(layers), 2)
        layer_names = [l["name"] for l in layers]
        self.assertIn("WALL", layer_names)
        self.assertIn("HIDDEN", layer_names)

    def test_block_save_and_get(self):
        self.db.save_blocks([
            {"name": "Door_900", "count": 3, "origin": [0, 0, 0]},
        ])
        blocks = self.db.get_blocks()
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["name"], "Door_900")

    def test_snapshot(self):
        sid = self.db.create_snapshot("test.dwg", 100, 5, 2, {"Line": 50, "Circle": 50})
        self.assertIsNotNone(sid)
        snapshots = self.db.get_recent_snapshots(5)
        self.assertGreaterEqual(len(snapshots), 1)

    def test_execute_sql(self):
        self.db.clear_entities()
        self.db.upsert_entity("H1", "L1", "AcDbLine", layer="0", color=1)
        result = self.db.execute("SELECT * FROM cad_entities WHERE color = 1")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["rows"][0]["handle"], "H1")

    def test_table_schema(self):
        schema = self.db.get_table_schema("cad_entities")
        self.assertGreater(len(schema), 5)
        col_names = [c["name"] for c in schema]
        self.assertIn("handle", col_names)
        self.assertIn("type", col_names)


# ══════════════════════════════════════════════════════════════════
#  Test: Controller API completeness
# ══════════════════════════════════════════════════════════════════

class TestControllerAPI(unittest.TestCase):
    """Verify CADController has all methods that tool modules expect."""

    @classmethod
    def setUpClass(cls):
        from src.cad_controller import CADController
        # Get all public methods
        cls.methods = [m for m in dir(CADController)
                       if not m.startswith('_') and callable(getattr(CADController, m, None))]

    def test_has_connection_methods(self):
        self.assertIn('connect', self.methods)

    def test_has_file_methods(self):
        self.assertIn('create_drawing', self.methods)
        self.assertIn('open_drawing', self.methods)
        self.assertIn('save_drawing', self.methods)
        self.assertIn('export_drawing', self.methods)
        self.assertIn('close_drawing', self.methods)
        self.assertIn('get_document_info', self.methods)

    def test_has_drawing_primitives(self):
        for m in ['add_line', 'add_circle', 'add_arc', 'add_ellipse',
                   'add_polyline', 'add_polyline_3d', 'add_rectangle',
                   'add_polygon', 'add_spline', 'add_point',
                   'add_text', 'add_mtext', 'add_leader', 'add_mleader',
                   'add_table', 'add_ray', 'add_xline', 'add_mline',
                   'add_solid', 'add_donut', 'add_tolerance',
                   'add_raster_image', 'add_trace', 'add_minert_block',
                   'add_shape', 'add_3d_face']:
            with self.subTest(method=m):
                self.assertIn(m, self.methods, f"Controller missing method: {m}")

    def test_has_edit_methods(self):
        for m in ['move_entity', 'rotate_entity', 'copy_entity',
                   'delete_entity', 'delete_entities', 'mirror_entity',
                   'scale_entity', 'offset_entity', 'array_rectangular',
                   'array_polar', 'explode_entity', 'set_entity_properties',
                   'get_entity_properties', 'rotate_3d', 'mirror_3d',
                   'transform_entity']:
            with self.subTest(method=m):
                self.assertIn(m, self.methods, f"Controller missing method: {m}")

    def test_has_3d_methods(self):
        for m in ['add_box', 'add_cone', 'add_cylinder', 'add_sphere',
                   'add_torus', 'add_wedge', 'add_elliptical_cone',
                   'add_elliptical_cylinder', 'add_region',
                   'add_extruded_solid', 'add_extruded_solid_along_path',
                   'add_revolved_solid', 'solid_boolean',
                   'solid_check_interference', 'solid_slice', 'solid_section',
                   'add_3d_mesh', 'add_polyface_mesh']:
            with self.subTest(method=m):
                self.assertIn(m, self.methods, f"Controller missing method: {m}")

    def test_has_layer_methods(self):
        for m in ['create_layer', 'delete_layer', 'rename_layer',
                   'get_all_layers', 'set_layer_state', 'set_current_layer']:
            with self.subTest(method=m):
                self.assertIn(m, self.methods, f"Controller missing method: {m}")

    def test_has_view_methods(self):
        for m in ['zoom_extents', 'zoom_window', 'zoom_center',
                   'zoom_scale', 'zoom_previous', 'zoom_all', 'pan',
                   'get_current_view', 'get_layouts', 'set_active_layout',
                   'create_layout']:
            with self.subTest(method=m):
                self.assertIn(m, self.methods, f"Controller missing method: {m}")

    def test_has_hatch_methods(self):
        for m in ['add_hatch', 'hatch_boundary', 'hatch_append_inner_loop',
                   'hatch_set_properties', 'hatch_get_properties',
                   'hatch_set_gradient']:
            with self.subTest(method=m):
                self.assertIn(m, self.methods, f"Controller missing method: {m}")

    def test_has_polyline_methods(self):
        for m in ['polyline_set_bulge', 'polyline_get_bulge',
                   'polyline_set_width', 'polyline_get_width',
                   'polyline_add_vertex', 'polyline_constant_width',
                   'polyline_num_vertices', 'polyline_get_point_at_param',
                   'polyline_get_segment_type']:
            with self.subTest(method=m):
                self.assertIn(m, self.methods, f"Controller missing method: {m}")


# ══════════════════════════════════════════════════════════════════
#  Test: Cross-module tool wiring consistency
# ══════════════════════════════════════════════════════════════════

class TestToolWiring(unittest.TestCase):
    """Verify server.py @mcp.tool() functions correctly call tool module functions."""

    @classmethod
    def setUpClass(cls):
        """Parse server.py to find all tool definitions and their module calls."""
        server_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'src', 'server.py'
        )
        with open(server_path, 'r', encoding='utf-8') as f:
            cls.server_content = f.read()

    def _parse_tool_body_calls(self):
        """Find all tool functions and what module function they call."""
        import re
        # Find pattern: def tool_name(...) -> str:\n    ...\n    return module.func(...)
        # This is heuristic but effective for this codebase
        tools = {}
        current_tool = None
        for line in self.server_content.split('\n'):
            # Detect tool function definition
            m = re.match(r'def\s+(\w+)\s*\(', line)
            if m:
                current_tool = m.group(1)
                tools[current_tool] = {'module': None, 'func': None}
            # Detect return statement with module call
            if current_tool:
                m = re.match(r'\s+return\s+(\w+)_tools\.(\w+)\(', line)
                if m:
                    tools[current_tool] = {
                        'module': f'{m.group(1)}_tools',
                        'func': m.group(2)
                    }
        return tools

    def test_tool_wiring_consistency(self):
        tools = self._parse_tool_body_calls()
        modules = {
            'drawing_tools': drawing_tools,
            'edit_tools': edit_tools,
            'layer_tools': layer_tools,
            'text_tools': text_tools,
            'dimension_tools': dimension_tools,
            'block_tools': block_tools,
            'view_tools': view_tools,
            'query_tools': query_tools,
            'file_tools': file_tools,
            'utility_tools': utility_tools,
            'solid_tools': solid_tools,
            'advanced_tools': advanced_tools,
            'polyline_tools': polyline_tools,
            'hatch_tools': hatch_tools,
            'attribute_tools': attribute_tools,
        }

        issues = []
        for tool_name, call_info in tools.items():
            if not call_info['module']:
                continue
            mod_name = call_info['module']
            func_name = call_info['func']

            if mod_name not in modules:
                issues.append(f"{tool_name}: module '{mod_name}' not found")
                continue

            mod = modules[mod_name]
            if not hasattr(mod, func_name):
                issues.append(
                    f"{tool_name}: calls {mod_name}.{func_name}() "
                    f"but function doesn't exist"
                )
            elif not callable(getattr(mod, func_name)):
                issues.append(
                    f"{tool_name}: {mod_name}.{func_name} exists but is not callable"
                )

        if issues:
            self.fail("Tool wiring issues found:\n" + "\n".join(f"  - {i}" for i in issues))

    def test_all_server_tools_have_valid_module_calls(self):
        """Every @mcp.tool() function must call a valid module function."""
        tools = self._parse_tool_body_calls()
        # Tools that don't have a direct module call (like help, prompts, main)
        exceptions = {
            'cad_workflow_guide', 'cad_layer_planning', 'main',
        }

        missing = []
        for tool_name, call_info in tools.items():
            if tool_name in exceptions:
                continue
            if not call_info['module']:
                missing.append(tool_name)

        # Some tools may have complex bodies - check manually
        known_complex = {
            'create_block', 'create_group', 'save_named_view',
            'restore_named_view', 'get_named_views',
        }
        # These have multi-line bodies, just ensure they exist in server.py
        for name in known_complex:
            if name in missing:
                missing.remove(name)

        if missing:
            self.fail(
                f"Tools without clear module calls (may need review): {missing}"
            )


# ══════════════════════════════════════════════════════════════════
#  Test: Bug detection — known issues that need fixing
# ══════════════════════════════════════════════════════════════════

class TestBugDetection(unittest.TestCase):
    """Tests that identify known bugs in the codebase."""

    def test_cad_controller_add_solid_dead_code(self):
        """add_solid had dead code ('flat' variable) — verify it's removed."""
        ctrl_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'src', 'cad_controller.py'
        )
        with open(ctrl_path, 'r', encoding='utf-8') as f:
            content = f.read()
        idx = content.find('def add_solid(')
        body = content[idx:idx + 500] if idx > 0 else ''
        # The dead 'flat' variable should NOT be present after fix
        self.assertNotIn('flat = []', body,
            "Dead code fix: 'flat' dead code should be removed from add_solid")

    def test_block_tools_attach_xref_direct_doc_access(self):
        """attach_xref directly accesses ctrl.doc — fragile pattern."""
        import inspect
        source = inspect.getsource(block_tools.attach_xref)
        self.assertIn('ctrl.doc.ModelSpace.AttachExternalReference', source)

    def test_text_tools_direct_doc_access(self):
        """find_text and replace_text directly access ctrl.doc.ModelSpace."""
        source_find = inspect.getsource(text_tools.find_text)
        source_replace = inspect.getsource(text_tools.replace_text)
        self.assertIn('ctrl.doc', source_find)
        self.assertIn('ctrl.doc', source_replace)

    def test_view_tools_pan_uses_zoomcenter(self):
        """pan() correctly uses ZoomCenter with view center offset for panning."""
        ctrl_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'src', 'cad_controller.py'
        )
        with open(ctrl_path, 'r', encoding='utf-8') as f:
            content = f.read()
        pan_idx = content.find('def pan(')
        if pan_idx > 0:
            pan_body = content[pan_idx:pan_idx + 500]
            self.assertIn('ZoomCenter', pan_body,
                "pan() uses ZoomCenter with view center offset — correct approach")
            self.assertIn('current_view', pan_body,
                "pan() should use current view center for proper offset")

    def test_chamfer_fillet_use_send_command_with_handles(self):
        """chamfer_entities and fillet_entities use send_command with raw handles."""
        source = inspect.getsource(edit_tools.fillet_entities)
        self.assertIn('send_command', source)
        source = inspect.getsource(edit_tools.chamfer_entities)
        self.assertIn('send_command', source)

    def test_advanced_tools_add_viewport_on_property(self):
        """add_viewport uses vp.ViewportOn (correct property name)."""
        source = inspect.getsource(advanced_tools.add_viewport)
        self.assertIn('vp.ViewportOn', source,
            "add_viewport should use 'vp.ViewportOn' (not 'vp.On')")


# ══════════════════════════════════════════════════════════════════
#  Runner
# ══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    unittest.main(verbosity=2)
