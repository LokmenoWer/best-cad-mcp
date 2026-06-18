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
mock_pythoncom.VT_I4 = 3
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
    from src.cad_understanding import analysis as understanding_analysis
    from src.cad_understanding import constraints as understanding_constraints
    from src.cad_understanding import dimension_binding as understanding_dimensions
    from src.cad_understanding import engineering_review as understanding_engineering
    from src.cad_understanding import ir_builder as understanding_ir_builder
    from src.cad_understanding import plan as understanding_plan
    from src.cad_understanding import resources as understanding_resources
    from src.cad_understanding import semantic_graph as understanding_semantic
    from src.cad_understanding import validators as understanding_validators
    from src.cad_understanding import view_grounding as understanding_view
    from src.cad_understanding import vlm as understanding_vlm

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
            'draw_trace', 'insert_minert_block', 'insert_minsert_block',
            'add_shape',
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
            'export_dwf', 'export_image', 'export_view_image', 'purge_drawing',
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
            'delete_selection_set', 'erase_selection_entities',
            'clear_selection_set',
            'recommend_cad_tools', 'get_tool_help',
            'check_runtime_environment',
            'restart_mcp',
            'get_entity_topology', 'get_topology_summary',
            'add_spatial_annotation', 'list_spatial_annotations',
            'clear_spatial_annotations',
            'get_workspace_context', 'set_workspace_context',
            'activate_workspace_drawing', 'list_workspace_drawings',
            'get_database_maintenance_status', 'maintain_database',
            'clear_understanding_cache', 'get_legacy_database_status',
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


class TestMCPToolSchemas(unittest.TestCase):
    """Verify registered MCP schemas are concrete enough for clients to call."""

    def _get_input_schema(self, tool_name):
        import asyncio
        from src import server

        async def find_tool():
            tools = await server.mcp.list_tools()
            for tool in tools:
                if tool.name == tool_name:
                    return tool.inputSchema
            return None

        schema = asyncio.run(find_tool())
        self.assertIsNotNone(schema, f"MCP tool not registered: {tool_name}")
        return schema

    def _get_tool_description(self, tool_name):
        import asyncio
        from src import server

        async def find_tool():
            tools = await server.mcp.list_tools()
            for tool in tools:
                if tool.name == tool_name:
                    return tool.description
            return None

        description = asyncio.run(find_tool())
        self.assertIsNotNone(description, f"MCP tool not registered: {tool_name}")
        return description

    def _resolve_schema_ref(self, root_schema, node):
        ref = node.get("$ref") if isinstance(node, dict) else None
        if not ref:
            return node
        self.assertTrue(ref.startswith("#/$defs/"), f"Unexpected schema ref: {ref}")
        return root_schema["$defs"][ref.rsplit("/", 1)[-1]]

    def test_add_mleader_points_schema_has_typed_items(self):
        schema = self._get_input_schema("add_mleader")
        points_schema = schema["properties"]["points"]

        variants = points_schema.get("anyOf", [points_schema])
        self.assertEqual(len(variants), 2)
        for variant in variants:
            self.assertEqual(variant.get("type"), "array")
            self.assertNotEqual(variant.get("items"), {},
                                "points schema must not be untyped List[Any]")

    def test_add_mleader_registered_call_reaches_text_tool(self):
        import asyncio
        from src import server

        async def call_tool():
            with patch.object(server.text_tools, "add_mleader",
                              return_value="ok") as mocked:
                result = await server.mcp.call_tool(
                    "add_mleader",
                    {"text": "Note",
                     "points": [[0, 0, 0], [10, 10, 0]]},
                )
                return result, mocked.call_args

        result, call_args = asyncio.run(call_tool())

        self.assertEqual(result[1], {"result": "ok"})
        self.assertEqual(call_args.args,
                         ("Note", [[0.0, 0.0, 0.0], [10.0, 10.0, 0.0]], None))

    def test_tool_descriptions_steer_away_from_primitive_overuse(self):
        draw_line_desc = self._get_tool_description("draw_line")
        rectangle_desc = self._get_tool_description("draw_rectangle")
        send_command_desc = self._get_tool_description("send_command")

        self.assertIn("LAST RESORT", draw_line_desc)
        self.assertIn("draw_rectangle", draw_line_desc)
        self.assertIn("instead of four draw_line", rectangle_desc)
        self.assertIn("LAST RESORT raw AutoCAD command", send_command_desc)
        self.assertIn("recommend_cad_tools", send_command_desc)

    def test_recommend_cad_tools_registered_and_routes_common_intents(self):
        schema = self._get_input_schema("recommend_cad_tools")
        self.assertIn("intent", schema["properties"])

        result = utility_tools.recommend_cad_tools("draw a rectangle floor plan with dimensions")

        self.assertIn("draw_rectangle", result)
        self.assertIn("add_linear_dimension", result)
        self.assertIn("Avoid", result)

    def test_recommend_cad_tools_routes_complex_drawings_to_advanced_workflows(self):
        result = utility_tools.recommend_cad_tools(
            "understand a complex mechanical assembly drawing with BOM, "
            "GD&T, section view, dimensions, and validation issues"
        )

        self.assertIn("Workflow route:", result)
        self.assertIn("Existing or complex drawing understanding", result)
        self.assertIn("build_drawing_ir", result)
        self.assertIn("detect_semantic_objects", result)
        self.assertIn("bind_all_dimensions", result)
        self.assertIn("validate_geometry", result)
        self.assertIn("export_view_image_with_mapping", result)
        self.assertIn("analyze_engineering_drawing_stages", result)
        self.assertIn("Do not flatten", result)

    def test_set_xdata_schema_requires_code_value(self):
        schema = self._get_input_schema("set_xdata")
        data_pairs_schema = schema["properties"]["data_pairs"]
        item_schema = self._resolve_schema_ref(schema, data_pairs_schema["items"])

        self.assertEqual(item_schema.get("type"), "object")
        self.assertEqual(set(item_schema.get("required", [])), {"code", "value"})
        self.assertIn("code", item_schema["properties"])
        self.assertIn("value", item_schema["properties"])

    def test_database_query_tools_are_read_only_and_topology_registered(self):
        execute_desc = self._get_tool_description("execute_query")
        topology_schema = self._get_input_schema("get_entity_topology")
        summary_schema = self._get_input_schema("get_topology_summary")

        self.assertIn("read-only", execute_desc.lower())
        self.assertIn("handle", topology_schema["properties"])
        self.assertIn("limit", summary_schema["properties"])

    def test_get_tool_help_uses_registered_tool_index(self):
        import asyncio
        from src import server

        async def list_tool_names():
            return {tool.name for tool in await server.mcp.list_tools()}

        tool_names = asyncio.run(list_tool_names())
        help_text = server.get_tool_help(None)

        self.assertIn(f"CAD MCP registered tools: {len(tool_names)}", help_text)
        for name in [
            "polyline_set_bulge",
            "hatch_add_boundary",
            "insert_block_with_attributes",
            "insert_minsert_block",
            "erase_selection_entities",
            "export_view_image",
            "add_spatial_annotation",
            "list_spatial_annotations",
            "clear_spatial_annotations",
        ]:
            self.assertIn(name, tool_names)
            self.assertIn(name, help_text)

    def test_visual_and_spatial_tools_registered_with_guidance(self):
        export_desc = self._get_tool_description("export_view_image")
        add_schema = self._get_input_schema("add_spatial_annotation")
        scan_schema = self._get_input_schema("scan_all_entities")

        self.assertIn("Vision-model verification", export_desc)
        self.assertIn("label", add_schema["properties"])
        self.assertIn("target_kind", add_schema["properties"])
        self.assertIn("clear_annotations", scan_schema["properties"])
        self.assertIn("detail_level", scan_schema["properties"])
        self.assertIn("include_bounding_boxes", scan_schema["properties"])
        self.assertIn("derive_topology", scan_schema["properties"])
        self.assertIn("topology_detail", scan_schema["properties"])
        self.assertIn("clear_understanding", scan_schema["properties"])

        recommendation = utility_tools.recommend_cad_tools(
            "visually verify drawing and mark the base plate"
        )

        self.assertIn("export_view_image", recommendation)
        self.assertIn("add_spatial_annotation", recommendation)

    def test_restart_mcp_registered_and_schedules_delayed_exit(self):
        schema = self._get_input_schema("restart_mcp")
        self.assertIn("delay_seconds", schema["properties"])
        self.assertIn("exit_code", schema["properties"])

        with patch.object(utility_tools, "_schedule_process_exit") as schedule:
            result = utility_tools.restart_mcp(delay_seconds=0, exit_code=0)

        schedule.assert_called_once_with(0.1, 0)
        self.assertIn("MCP restart requested", result)

    def test_check_runtime_environment_registered_and_structured(self):
        schema = self._get_input_schema("check_runtime_environment")
        self.assertIn("check_autocad", schema["properties"])
        self.assertIn("require_visual_export", schema["properties"])

        with patch.object(utility_tools.ctrl, "connect") as connect:
            result = utility_tools.check_runtime_environment(check_autocad=False)

        connect.assert_not_called()
        self.assertIn("checks", result["data"])
        self.assertIn("policy", result["data"])

    def test_get_dimension_styles_formats_normal_style_list(self):
        with patch.object(dimension_tools, "ctrl") as mock_ctrl:
            mock_ctrl.get_dim_styles.return_value = [
                {"name": "Standard", "handle": "D1"},
                {"name": "Annotative", "handle": "D2"},
            ]

            result = dimension_tools.get_dimension_styles()

        mock_ctrl.get_dim_styles.assert_called_once_with()
        self.assertIn("[0] Standard", result)
        self.assertIn("[1] Annotative", result)

    def test_get_dimension_styles_returns_controller_error_message(self):
        with patch.object(dimension_tools, "ctrl") as mock_ctrl:
            mock_ctrl.get_dim_styles.return_value = {
                "success": False,
                "message": "No open document",
            }

            result = dimension_tools.get_dimension_styles()

        mock_ctrl.get_dim_styles.assert_called_once_with()
        self.assertEqual(result, "ERROR: Get dimension styles failed: No open document")

    def test_export_view_image_uses_non_dwg_review_artifact(self):
        with patch.object(file_tools, "ctrl") as mock_ctrl:
            mock_ctrl.export_drawing.return_value = {
                "success": True,
                "message": r"exported C:\tmp\view.wmf",
            }

            result = file_tools.export_view_image(
                r"C:\tmp\view.wmf",
                zoom_extents_first=True,
            )

        mock_ctrl.zoom_extents.assert_called_once_with()
        mock_ctrl.regen.assert_called_once_with("all")
        mock_ctrl.export_drawing.assert_called_once_with(r"C:\tmp\view.wmf", "WMF")
        self.assertIn("Visual verification artifact", result)
        self.assertIn("does not modify the DWG", result)

    def test_export_view_image_rejects_unsupported_raster_extensions(self):
        with patch.object(file_tools, "ctrl") as mock_ctrl:
            result = file_tools.export_view_image(r"C:\tmp\view.png")

        mock_ctrl.export_drawing.assert_not_called()
        self.assertIn("supports WMF reliably", result)

    def test_tool_call_exceptions_return_error_text(self):
        import asyncio
        from src import server

        async def call_tool():
            with patch.object(server.dimension_tools, "get_dimension_styles",
                              side_effect=RuntimeError("boom")):
                return await server.mcp.call_tool("get_dimension_styles", {})

        result = asyncio.run(call_tool())

        self.assertIn("ERROR: get_dimension_styles failed: boom",
                      result[1]["result"])

    def test_model_facing_prompts_are_english(self):
        import asyncio
        import re
        from src import server

        layer_prompt = server.cad_layer_planning()
        workflow_prompt = server.cad_workflow_guide()
        cjk = re.compile(r"[\u4e00-\u9fff]")

        self.assertIn("CAD Layer Planning Guide", layer_prompt)
        self.assertIn("Recommended workflow", workflow_prompt)
        self.assertIn("Classify the request", workflow_prompt)
        self.assertIn("Engineering drawing or assembly", workflow_prompt)
        self.assertIsNone(cjk.search(layer_prompt))
        self.assertIsNone(cjk.search(workflow_prompt))

        async def list_tool_descriptions():
            return {
                tool.name: tool.description or ""
                for tool in await server.mcp.list_tools()
            }

        descriptions = asyncio.run(list_tool_descriptions())
        self.assertIn("AutoCAD document and export tool",
                      descriptions["create_new_drawing"])
        cjk_descriptions = [
            name for name, description in descriptions.items()
            if cjk.search(description)
        ]
        self.assertEqual(cjk_descriptions, [])

    def test_recommend_cad_tools_routes_minsert_alias(self):
        result = utility_tools.recommend_cad_tools("MInsert block array")

        self.assertIn("insert_minsert_block", result)
        self.assertIn("insert_block plus array_rectangular", result)

    def test_prompt_files_include_complex_drawing_fidelity_contracts(self):
        from src import server

        precise_prompt = server.precise_draw_from_spec()
        understand_prompt = server.understand_existing_drawing()
        repair_prompt = server.repair_drawing()
        vlm_prompt = server.vlm_review_drawing()

        self.assertIn("Fidelity Contract", precise_prompt)
        self.assertIn("Do not simplify", precise_prompt)
        self.assertIn("CADPlan", precise_prompt)
        self.assertIn("not just a count of", understand_prompt)
        self.assertIn("analyze_engineering_drawing_stages", understand_prompt)
        self.assertIn("Do not delete and redraw complex geometry", repair_prompt)
        self.assertIn("hypothesis until validated", vlm_prompt)

    def test_add_mleader_rejects_mixed_point_shapes_without_throwing(self):
        result = text_tools.add_mleader("Note", [[0, 0, 0], 10, 10, 0])

        self.assertIn("points must be either", result)

    def test_xdata_validation_rejects_ambiguous_pairs(self):
        with self.assertRaises(ValueError):
            advanced_tools._normalize_xdata_pairs("APP", [{"value": "missing code"}])
        with self.assertRaises(ValueError):
            advanced_tools._normalize_xdata_pairs("APP", [{"code": 1001, "value": "APP"}])
        with self.assertRaises(ValueError):
            advanced_tools._normalize_xdata_pairs("APP", [{"code": 1000, "value": {"bad": "shape"}}])

        pairs = advanced_tools._normalize_xdata_pairs(
            "APP",
            [{"code": 1000, "value": "wall"}, {"code": 1040, "value": "3.5"}],
        )
        self.assertEqual(pairs, [{"code": 1000, "value": "wall"},
                                 {"code": 1040, "value": 3.5}])


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
        fd, self.tmpfile = tempfile.mkstemp(prefix='test_cad_mcp_', suffix='.db')
        os.close(fd)
        self.db = CADDatabase(self.tmpfile)

    def tearDown(self):
        for path in (self.tmpfile, f"{self.tmpfile}-wal", f"{self.tmpfile}-shm"):
            try:
                os.remove(path)
            except Exception:
                pass

    def test_get_tables(self):
        tables = self.db.get_tables()
        self.assertIn('cad_entities', tables)
        self.assertIn('cad_geometry_primitives', tables)
        self.assertIn('cad_geometry_relations', tables)
        self.assertIn('cad_topology_summary', tables)
        self.assertIn('cad_spatial_annotations', tables)
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
        topo = self.db.get_entity_topology("TEST001")
        self.assertEqual(topo["summary"]["point_count"], 2)
        self.assertEqual(topo["summary"]["line_count"], 1)
        self.assertEqual(topo["summary"]["dimensionality"], 1)
        relation_types = {r["relation_type"] for r in topo["relations"]}
        self.assertIn("starts_at", relation_types)
        self.assertIn("ends_at", relation_types)

        ent = self.db.get_entity("TEST001")
        self.assertEqual(ent["bbox_min_x"], 0.0)
        self.assertEqual(ent["bbox_max_x"], 10.0)

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
        self.assertIsNone(self.db.get_entity_topology("H4")["summary"])

    def test_spatial_annotations_are_hidden_model_context(self):
        self.db.upsert_entity("H5", "Plate", "AcDbPolyline")
        annotation = self.db.upsert_spatial_annotation(
            annotation_id="ann_plate",
            label="base plate",
            target_kind="entity",
            entity_handle="H5",
            description="Main plate remembered by the model.",
            confidence=0.9,
            properties={"role": "support"},
        )

        self.assertEqual(annotation["annotation_id"], "ann_plate")
        self.assertEqual(annotation["label"], "base plate")
        self.assertTrue(annotation["hidden"])
        self.assertEqual(annotation["properties"]["role"], "support")

        rows = self.db.list_spatial_annotations(entity_handle="H5")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["description"], "Main plate remembered by the model.")

        self.db.clear_entities()
        self.assertIsNone(self.db.get_entity("H5"))
        self.assertEqual(len(self.db.list_spatial_annotations(entity_handle="H5")), 1)

        deleted = self.db.delete_spatial_annotations(annotation_id="ann_plate")
        self.assertEqual(deleted, 1)
        self.assertEqual(self.db.list_spatial_annotations(entity_handle="H5"), [])

        self.db.upsert_spatial_annotation(
            annotation_id="ann_plate",
            label="base plate",
            target_kind="entity",
            entity_handle="H5",
        )

        self.db.clear_entities(clear_annotations=True)
        self.assertEqual(self.db.list_spatial_annotations(entity_handle="H5"), [])

    def test_closed_polyline_topology_has_surface(self):
        self.db.upsert_entity(
            "P1", "Rect", "AcDbPolyline",
            geometry={
                "vertices": [[0, 0, 0], [10, 0, 0], [10, 5, 0], [0, 5, 0]],
                "closed": True,
            },
        )
        topology = self.db.get_entity_topology("P1")
        summary = topology["summary"]

        self.assertEqual(summary["point_count"], 4)
        self.assertEqual(summary["line_count"], 4)
        self.assertEqual(summary["surface_count"], 1)
        self.assertEqual(summary["dimensionality"], 2)
        self.assertAlmostEqual(summary["area"], 50.0)
        self.assertIn("bounded_by", {r["relation_type"] for r in topology["relations"]})

    def test_query_text_and_near_point_use_geometry(self):
        self.db.clear_entities()
        self.db.upsert_entity(
            "T1", "DoorText", "AcDbText", layer="TEXT",
            geometry={"text": "Door 900", "position": [5, 5, 0]},
        )
        self.db.upsert_entity(
            "L1", "Line", "AcDbLine", layer="WALL",
            geometry={"start": [0, 0, 0], "end": [10, 0, 0]},
        )

        text_results = self.db.query_entities(text_contains="Door")
        self.assertEqual([r["handle"] for r in text_results], ["T1"])

        near_results = self.db.query_near_point(5, 0, 6)
        self.assertIn("L1", {r["handle"] for r in near_results})

    def test_type_stats(self):
        self.db.clear_entities()
        self.db.upsert_entity("H1", "L1", "AcDbLine")
        self.db.upsert_entity("H2", "L2", "AcDbLine")
        self.db.upsert_entity("H3", "C1", "AcDbCircle")
        stats = self.db.get_type_stats()
        self.assertEqual(stats.get("AcDbLine"), 2)
        self.assertEqual(stats.get("AcDbCircle"), 1)

    def test_batch_upsert_can_skip_topology_for_fast_scans(self):
        count = self.db.upsert_entities_batch(
            [
                {
                    "handle": "B1",
                    "name": "Line",
                    "type": "AcDbLine",
                    "layer": "WALL",
                    "bbox": [0, 0, 10, 1],
                    "geometry": {"start": [0, 0, 0], "end": [10, 0, 0]},
                }
            ],
            derive_topology=False,
            derive_bbox=False,
        )

        self.assertEqual(count, 1)
        ent = self.db.get_entity("B1")
        self.assertEqual(ent["bbox_min_x"], 0.0)
        self.assertEqual(ent["bbox_max_x"], 10.0)
        self.assertIsNone(self.db.get_entity_topology("B1")["summary"])

    def test_batch_upsert_summary_topology_keeps_agent_recognition(self):
        count = self.db.upsert_entities_batch(
            [
                {
                    "handle": "B2",
                    "name": "LightLine",
                    "type": "AcDbLine",
                    "layer": "WALL",
                    "bbox": [0, 0, 10, 1],
                    "geometry": {},
                },
                {
                    "handle": "B3",
                    "name": "LightCircle",
                    "type": "AcDbCircle",
                    "layer": "HOLES",
                    "bbox": [4, 4, 6, 6],
                    "geometry": {},
                },
            ],
            derive_topology=True,
            derive_bbox=False,
            topology_detail="summary",
        )

        self.assertEqual(count, 2)
        line_topology = self.db.get_entity_topology("B2")
        circle_topology = self.db.get_entity_topology("B3")
        self.assertEqual(line_topology["summary"]["line_count"], 1)
        self.assertEqual(circle_topology["summary"]["curve_count"], 1)
        self.assertEqual(circle_topology["summary"]["is_closed"], 1)
        self.assertEqual(line_topology["primitives"], [])
        self.assertEqual(line_topology["relations"], [])

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

    def test_workspace_scopes_multiple_drawings_with_same_handle(self):
        self.db.activate_drawing("a.dwg", r"C:\drawings\a.dwg")
        self.db.upsert_entity("H1", "LineA", "AcDbLine", layer="A")

        self.db.activate_drawing("b.dwg", r"C:\drawings\b.dwg")
        self.assertIsNone(self.db.get_entity("H1"))
        self.db.upsert_entity("H1", "LineB", "AcDbLine", layer="B")
        b_rows = self.db.execute("SELECT handle, name, layer FROM cad_entities")
        self.assertEqual(b_rows["rows"], [{"handle": "H1", "name": "LineB", "layer": "B"}])

        self.db.activate_drawing("a.dwg", r"C:\drawings\a.dwg")
        a_rows = self.db.execute("SELECT handle, name, layer FROM cad_entities")
        self.assertEqual(a_rows["rows"], [{"handle": "H1", "name": "LineA", "layer": "A"}])

    def test_spatial_annotations_are_thread_scoped(self):
        self.db.activate_drawing("shared.dwg", r"C:\drawings\shared.dwg")
        self.db.configure_context(conversation_id="conv", thread_id="thread-a")
        self.db.upsert_spatial_annotation(
            annotation_id="ann1",
            label="thread a mark",
            target_kind="entity",
            entity_handle="H1",
        )

        self.db.configure_context(conversation_id="conv", thread_id="thread-b")
        self.assertEqual(self.db.list_spatial_annotations(annotation_id="ann1"), [])
        self.db.upsert_spatial_annotation(
            annotation_id="ann1",
            label="thread b mark",
            target_kind="entity",
            entity_handle="H1",
        )
        self.assertEqual(
            self.db.list_spatial_annotations(annotation_id="ann1")[0]["label"],
            "thread b mark",
        )

        self.db.configure_context(conversation_id="conv", thread_id="thread-a")
        self.assertEqual(
            self.db.list_spatial_annotations(annotation_id="ann1")[0]["label"],
            "thread a mark",
        )

    def test_workspace_database_allows_multiple_threads_same_workspace(self):
        import threading
        from src.cad_database import CADDatabase

        errors = []

        def worker(idx):
            try:
                local_db = CADDatabase(self.tmpfile)
                local_db.configure_context(
                    workspace_id="shared-workspace",
                    conversation_id="conv",
                    thread_id=f"thread-{idx}",
                    drawing_name="shared.dwg",
                    drawing_path=r"C:\drawings\shared.dwg",
                )
                local_db.upsert_entity(f"H{idx}", f"Line{idx}", "AcDbLine")
                local_db.upsert_spatial_annotation(
                    annotation_id="mark",
                    label=f"thread-{idx}",
                    target_kind="entity",
                    entity_handle=f"H{idx}",
                )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.db.configure_context(
            workspace_id="shared-workspace",
            conversation_id="conv",
            thread_id="thread-0",
            drawing_name="shared.dwg",
            drawing_path=r"C:\drawings\shared.dwg",
        )
        self.assertEqual(self.db.count_entities(), 6)
        self.assertEqual(
            self.db.list_spatial_annotations(annotation_id="mark")[0]["label"],
            "thread-0",
        )

    def test_execute_read_only_rejects_writes(self):
        self.db.clear_entities()
        self.db.upsert_entity("H1", "L1", "AcDbLine", layer="0", color=1)

        with self.assertRaises(ValueError):
            self.db.execute("UPDATE cad_entities SET color = 2", read_only=True)
        with self.assertRaises(Exception):
            self.db.execute("PRAGMA journal_mode=OFF", read_only=True)

        ent = self.db.get_entity("H1")
        self.assertEqual(ent["color"], 1)

    def test_execute_read_only_rejects_main_table_scope_bypass(self):
        self.db.configure_context(
            workspace_id="scope-a",
            conversation_id="conv",
            thread_id="thread",
            drawing_name="a.dwg",
            drawing_path=r"C:\drawings\a.dwg",
        )
        self.db.upsert_entity("A1", "A", "AcDbLine")
        self.db.configure_context(
            workspace_id="scope-b",
            conversation_id="conv",
            thread_id="thread",
            drawing_name="b.dwg",
            drawing_path=r"C:\drawings\b.dwg",
        )
        self.db.upsert_entity("B1", "B", "AcDbLine")

        scoped = self.db.execute(
            "SELECT handle, name FROM cad_entities ORDER BY handle",
            read_only=True,
        )
        self.assertEqual(scoped["rows"], [{"handle": "B1", "name": "B"}])
        with self.assertRaises(Exception):
            self.db.execute(
                "SELECT native_handle, name, workspace_id FROM main.cad_entities",
                read_only=True,
            )

    def test_execute_read_only_limits_results_and_records_history(self):
        self.db.clear_entities()
        for idx in range(5):
            self.db.upsert_entity(f"H{idx}", f"Line{idx}", "AcDbLine")

        result = self.db.execute(
            "SELECT handle FROM cad_entities ORDER BY handle",
            read_only=True,
            max_rows=2,
        )
        self.assertEqual(result["count"], 2)
        self.assertTrue(result["truncated"])
        self.assertEqual([row["handle"] for row in result["rows"]], ["H0", "H1"])

        history = self.db.execute(
            "SELECT query, result_count, truncated FROM query_history ORDER BY id",
            read_only=True,
        )["rows"]
        matching = [
            row for row in history
            if row["query"] == "SELECT handle FROM cad_entities ORDER BY handle"
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["result_count"], 2)
        self.assertEqual(matching[0]["truncated"], 1)

    def test_clear_entities_can_clear_understanding_cache(self):
        from src.cad_understanding.common import ensure_understanding_schema

        ensure_understanding_schema(self.db)
        ctx = self.db.get_context()
        with self.db._conn() as conn:
            conn.execute('''
                INSERT INTO cad_semantic_objects
                    (object_id, object_type, label, source, confidence,
                     workspace_id, drawing_id, conversation_id, thread_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                "obj-test", "part", "cached", "test", 1.0,
                ctx.workspace_id, ctx.drawing_id,
                ctx.conversation_id, ctx.thread_id,
            ))

        self.db.clear_entities(clear_understanding=True)
        with self.db._conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM cad_semantic_objects"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_maintenance_prunes_cached_reports_and_snapshots(self):
        from src.cad_understanding.common import ensure_understanding_schema

        ensure_understanding_schema(self.db)
        ctx = self.db.get_context()
        with self.db._conn() as conn:
            for idx in range(3):
                conn.execute('''
                    INSERT INTO cad_view_snapshots
                        (snapshot_id, snapshot_data, created_at,
                         workspace_id, drawing_id, conversation_id, thread_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    f"shot-{idx}", "{}", f"2026-01-0{idx + 1}T00:00:00",
                    ctx.workspace_id, ctx.drawing_id,
                    ctx.conversation_id, ctx.thread_id,
                ))
                conn.execute('''
                    INSERT INTO cad_validation_reports
                        (report_id, generated_at, issues,
                         workspace_id, drawing_id, conversation_id, thread_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    f"report-{idx}", f"2026-01-0{idx + 1}T00:00:00", "[]",
                    ctx.workspace_id, ctx.drawing_id,
                    ctx.conversation_id, ctx.thread_id,
                ))

        result = self.db.maintain(
            max_view_snapshots_per_scope=1,
            max_validation_reports_per_scope=1,
            incremental_vacuum_pages=1,
        )
        self.assertEqual(result["deleted"]["cad_view_snapshots"], 2)
        self.assertEqual(result["deleted"]["cad_validation_reports"], 2)
        with self.db._conn() as conn:
            snapshots = conn.execute(
                "SELECT snapshot_id FROM cad_view_snapshots ORDER BY snapshot_id"
            ).fetchall()
            reports = conn.execute(
                "SELECT report_id FROM cad_validation_reports ORDER BY report_id"
            ).fetchall()
        self.assertEqual([row[0] for row in snapshots], ["shot-2"])
        self.assertEqual([row[0] for row in reports], ["report-2"])

    def test_table_schema(self):
        schema = self.db.get_table_schema("cad_entities")
        self.assertGreater(len(schema), 5)
        col_names = [c["name"] for c in schema]
        self.assertIn("handle", col_names)
        self.assertIn("type", col_names)

    def test_table_schema_rejects_unknown_table(self):
        with self.assertRaises(ValueError):
            self.db.get_table_schema("cad_entities); DROP TABLE cad_entities;--")


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
        known_modules = {
            'drawing_tools', 'edit_tools', 'layer_tools', 'text_tools',
            'dimension_tools', 'block_tools', 'view_tools', 'query_tools',
            'file_tools', 'utility_tools', 'solid_tools', 'advanced_tools',
            'polyline_tools', 'hatch_tools', 'attribute_tools',
            'understanding_analysis', 'understanding_constraints',
            'understanding_dimensions', 'understanding_engineering',
            'understanding_ir_builder', 'understanding_plan',
            'understanding_resources', 'understanding_semantic',
            'understanding_validators', 'understanding_view', 'understanding_vlm',
        }
        # Find pattern: def tool_name(...): ... module.func(...)
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
                m = re.search(r'(\w+)\.(\w+)\(', line)
                if m and m.group(1) in known_modules and not tools[current_tool]['module']:
                    tools[current_tool] = {
                        'module': m.group(1),
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
            'understanding_analysis': understanding_analysis,
            'understanding_constraints': understanding_constraints,
            'understanding_dimensions': understanding_dimensions,
            'understanding_engineering': understanding_engineering,
            'understanding_ir_builder': understanding_ir_builder,
            'understanding_plan': understanding_plan,
            'understanding_resources': understanding_resources,
            'understanding_semantic': understanding_semantic,
            'understanding_validators': understanding_validators,
            'understanding_view': understanding_view,
            'understanding_vlm': understanding_vlm,
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
            '_humanize_tool_name', '_registration_category',
            '_default_tool_description', '_wrap_tool_errors',
            '_safe_mcp_tool', '_registered_tools', '_tool_category',
            '_first_description_line', '_build_registered_tool_help',
            '_load_prompt_file', '_env_flag',
            '_env_int', '_configure_logging', '_safe_log_value',
            '_tool_call_log_context',
            'cad_tool_selection_resource',
            'cad_registered_tools_resource', 'cad_workflow_guide',
            'cad_layer_planning', 'understand_existing_drawing',
            'precise_draw_from_spec', 'vlm_review_drawing',
            'repair_drawing', 'main',
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
            'restore_named_view', 'get_named_views', 'get_tool_help',
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
#  Test: COM call shapes for fragile ActiveX APIs
# ══════════════════════════════════════════════════════════════════

class TestActiveXCallShapes(unittest.TestCase):
    """Verify tools call AutoCAD ActiveX APIs with usable signatures."""

    def _controller_with_doc(self, doc):
        from src.cad_controller import CADController
        controller = CADController()
        controller.acad = MagicMock()
        controller.acad.Documents.Count = 1
        controller.acad.ActiveDocument = doc
        controller.doc = doc
        return controller

    def test_create_drawing_allows_no_existing_document_and_falls_back_for_name(self):
        from src.cad_controller import CADController

        new_doc = MagicMock()
        type(new_doc).Name = PropertyMock(side_effect=Exception("Add.Name"))
        active_doc = MagicMock()
        active_doc.Name = "Drawing1.dwg"

        controller = CADController()
        controller.acad = MagicMock()
        controller.acad.Documents.Count = 0
        controller.acad.Documents.Add.return_value = new_doc
        controller.acad.ActiveDocument = active_doc
        controller.doc = None

        result = controller.create_drawing()

        self.assertTrue(result["success"], result)
        controller.acad.Documents.Add.assert_called_once_with()
        self.assertIs(controller.doc, active_doc)
        self.assertEqual(result["name"], "Drawing1.dwg")

    def test_create_drawing_falls_back_to_discovered_template(self):
        from src.cad_controller import CADController

        template = r"C:\Users\me\AppData\Local\Autodesk\AutoCAD 2020\Template\acadiso.dwt"
        new_doc = MagicMock()
        new_doc.Name = "Drawing2.dwg"

        controller = CADController()
        controller.acad = MagicMock()
        controller.acad.Documents.Count = 0
        controller.acad.Documents.Add.side_effect = [Exception("file handler"), new_doc]
        controller.doc = None

        with patch.object(controller, "_ensure_connected") as ensure, \
             patch.object(controller, "_default_template_candidates", return_value=[template]):
            result = controller.create_drawing()

        ensure.assert_called_once_with()
        self.assertTrue(result["success"], result)
        controller.acad.Documents.Add.assert_any_call()
        controller.acad.Documents.Add.assert_any_call(template)
        self.assertEqual(result["template"], template)
        self.assertEqual(result["fallback_attempts"][0]["template"], "<default>")

    def test_create_drawing_opens_copied_template_when_add_fails(self):
        from src.cad_controller import CADController

        template = r"C:\Templates\acadiso.dwt"
        opened_doc = MagicMock()
        opened_doc.Name = "acadiso_123.dwg"

        controller = CADController()
        controller.acad = MagicMock()
        controller.acad.Documents.Count = 0
        controller.acad.Documents.Add.side_effect = Exception("file handler")
        controller.acad.Documents.Open.return_value = opened_doc
        controller.doc = None

        with patch.object(controller, "_ensure_connected") as ensure, \
             patch.object(controller, "_default_template_candidates", return_value=[template]), \
             patch("src.cad_controller.os.path.isfile", return_value=True), \
             patch("src.cad_controller.os.makedirs") as makedirs, \
             patch("src.cad_controller.shutil.copyfile") as copyfile, \
             patch("src.cad_controller.time.time", return_value=123.456):
            result = controller.create_drawing()

        ensure.assert_called_once_with()
        self.assertTrue(result["success"], result)
        self.assertEqual(result["fallback_method"], "open_copied_template")
        copyfile.assert_called_once()
        makedirs.assert_called_once()
        controller.acad.Documents.Open.assert_called_once()
        self.assertIn("acadiso_123456.dwg", controller.acad.Documents.Open.call_args.args[0])

    def test_create_drawing_uses_qnew_after_template_fallbacks_fail(self):
        from src.cad_controller import CADController

        template = r"C:\Templates\acadiso.dwt"
        controller = CADController()
        controller.acad = MagicMock()
        controller.acad.Documents.Count = 1
        controller.acad.Documents.Add.side_effect = Exception("file handler")
        controller.doc = MagicMock()
        qnew_result = {
            "success": True,
            "message": "Created new drawing with QNEW fallback",
            "name": "Drawing2.dwg",
            "fallback_method": "qnew_command",
        }

        with patch.object(controller, "_ensure_connected") as ensure, \
             patch.object(controller, "_default_template_candidates", return_value=[template]), \
             patch.object(controller, "_open_copied_template_drawing", side_effect=Exception("invalid context")), \
             patch.object(controller, "_create_drawing_with_qnew", return_value=qnew_result) as qnew:
            result = controller.create_drawing()

        ensure.assert_called_once_with()
        qnew.assert_called_once_with()
        self.assertTrue(result["success"], result)
        self.assertEqual(result["fallback_method"], "qnew_command")
        self.assertEqual(result["fallback_attempts"][-1]["method"], "open_copied_template")

    def test_create_layer_uses_truecolor_when_color_property_is_incompatible(self):
        class FakeAcCmColor:
            def __init__(self):
                self.ColorIndex = None

        class ColorIncompatibleLayer:
            def __init__(self):
                self.Linetype = None
                self.TrueColor = None

            def __setattr__(self, name, value):
                if name == "Color":
                    raise Exception("Color property incompatible")
                super().__setattr__(name, value)

        doc = MagicMock()
        layer = ColorIncompatibleLayer()
        true_color = FakeAcCmColor()
        doc.Layers.Item.side_effect = Exception("not found")
        doc.Layers.Add.return_value = layer
        controller = self._controller_with_doc(doc)
        controller.acad.GetInterfaceObject.return_value = true_color

        result = controller.create_layer("A-WALL", color_idx=1)

        self.assertTrue(result["success"], result)
        self.assertFalse(result["existing"])
        self.assertTrue(result["color_set"], result)
        self.assertIs(layer.TrueColor, true_color)
        self.assertEqual(true_color.ColorIndex, 1)
        doc.Layers.Add.assert_called_once_with("A-WALL")

    def test_create_layer_warns_when_all_color_paths_fail(self):
        class ColorIncompatibleLayer:
            def __init__(self):
                self.Linetype = None

            def __setattr__(self, name, value):
                if name == "Color":
                    raise Exception("Color property incompatible")
                super().__setattr__(name, value)

        doc = MagicMock()
        layer = ColorIncompatibleLayer()
        doc.Layers.Item.side_effect = Exception("not found")
        doc.Layers.Add.return_value = layer
        controller = self._controller_with_doc(doc)
        controller.acad.GetInterfaceObject.side_effect = Exception("no AcCmColor")

        result = controller.create_layer("A-WALL", color_idx=1)

        self.assertTrue(result["success"], result)
        self.assertFalse(result["color_set"])
        self.assertIn("Color property incompatible", result["color_warning"])
        self.assertIn("TrueColor fallback failed", result["color_warning"])
        doc.Layers.Add.assert_called_once_with("A-WALL")

    @patch("src.cad_controller.time.sleep", return_value=None)
    def test_save_drawing_retries_after_callee_rejected(self, _sleep):
        doc = MagicMock()
        doc.Name = "retry.dwg"
        doc.SaveAs.side_effect = [Exception("callee rejected"), None]
        controller = self._controller_with_doc(doc)
        controller._set_file_dialog_vars = MagicMock(return_value={"FILEDIA": 1})
        controller._restore_vars = MagicMock()
        controller._prepare_application_for_file_operation = MagicMock()
        controller._wait_quiescent = MagicMock(return_value=True)
        controller._refresh_active_document = MagicMock()
        controller._ensure_export_parent = MagicMock()

        result = controller.save_drawing(r"C:\tmp\retry.dwg")

        self.assertTrue(result["success"], result)
        self.assertEqual(doc.SaveAs.call_count, 2)
        self.assertEqual(result["path"], r"C:\tmp\retry.dwg")
        self.assertEqual(result["retry_attempts"], ["callee rejected"])
        controller._ensure_export_parent.assert_called_once_with(r"C:\tmp\retry.dwg")
        controller._restore_vars.assert_called_once_with({"FILEDIA": 1})

    def test_array_rectangular_uses_six_activex_arguments(self):
        doc = MagicMock()
        ent = MagicMock()
        copy_a = MagicMock()
        copy_a.Handle = "A2"
        copy_b = MagicMock()
        copy_b.Handle = "A3"
        ent.ArrayRectangular.return_value = [copy_a, copy_b]
        doc.HandleToObject.return_value = ent

        controller = self._controller_with_doc(doc)
        result = controller.array_rectangular("A1", 2, 3, 10.0, 20.0)

        self.assertTrue(result["success"], result)
        ent.ArrayRectangular.assert_called_once_with(2, 3, 1, 10.0, 20.0, 0.0)
        self.assertEqual(result["new_handles"], ["A2", "A3"])

    def test_export_pdf_uses_plot_to_file_not_document_export(self):
        doc = MagicMock()
        doc.ActiveLayout = MagicMock()
        controller = self._controller_with_doc(doc)

        with patch("src.cad_controller.os.path.exists", return_value=True), \
             patch("src.cad_controller.os.path.getsize", return_value=1):
            result = controller.export_drawing(r"C:\tmp\out.pdf", "PDF")

        self.assertTrue(result["success"], result)
        doc.Plot.PlotToFile.assert_called_once_with(
            r"C:\tmp\out.pdf", "DWG To PDF.pc3"
        )
        doc.Export.assert_not_called()

    def test_export_pdf_falls_back_to_active_layout_plot(self):
        doc = MagicMock()
        doc.ActiveLayout = MagicMock()
        doc.Plot.PlotToFile.side_effect = [False, True]
        controller = self._controller_with_doc(doc)

        with patch("src.cad_controller.os.path.exists", return_value=True), \
             patch("src.cad_controller.os.path.getsize", return_value=1):
            result = controller.export_drawing(r"C:\tmp\out.pdf", "PDF")

        self.assertTrue(result["success"], result)
        self.assertEqual(doc.Plot.PlotToFile.call_args_list[0].args, (r"C:\tmp\out.pdf", "DWG To PDF.pc3"))
        self.assertEqual(doc.Plot.PlotToFile.call_args_list[1].args, (r"C:\tmp\out.pdf",))
        doc.Export.assert_not_called()

    def test_export_dwf_uses_active_layout_plot_config_with_single_arg_plot(self):
        doc = MagicMock()
        doc.ActiveLayout = MagicMock()
        doc.Plot.PlotToFile.return_value = True
        controller = self._controller_with_doc(doc)

        with patch("src.cad_controller.os.path.exists", return_value=True), \
             patch("src.cad_controller.os.path.getsize", return_value=1):
            result = controller.export_drawing(r"C:\tmp\out.dwf", "DWF")

        self.assertTrue(result["success"], result)
        doc.Plot.PlotToFile.assert_called_once_with(r"C:\tmp\out.dwf")
        doc.Export.assert_not_called()

    def test_export_dxf_supplies_required_selection_set(self):
        doc = MagicMock()
        selection_set = MagicMock()
        doc.SelectionSets.Item.side_effect = Exception("not found")
        doc.SelectionSets.Add.return_value = selection_set
        controller = self._controller_with_doc(doc)

        with patch("src.cad_controller.os.path.exists", return_value=True), \
             patch("src.cad_controller.os.path.getsize", return_value=1):
            result = controller.export_drawing(r"C:\tmp\out.dxf", "DXF")

        self.assertTrue(result["success"], result)
        doc.Export.assert_called_once_with(r"C:\tmp\out", "DXF", selection_set)
        selection_set.Delete.assert_called_once()

    def test_delete_entities_reports_failed_handles_and_falls_back_to_space_scan(self):
        class FakeEntity:
            Handle = "A1"

            def __init__(self):
                self.deleted = False

            def Delete(self):
                self.deleted = True

        class FakeSpace:
            def __init__(self, items):
                self.items = items
                self.Count = len(items)

            def Item(self, index):
                return self.items[index]

        entity = FakeEntity()
        doc = MagicMock()
        doc.HandleToObject.side_effect = Exception("HandleToObject failed")
        doc.ModelSpace = FakeSpace([entity])
        doc.PaperSpace = FakeSpace([])
        controller = self._controller_with_doc(doc)

        result = controller.delete_entities(["a1", "A1", "missing"])

        self.assertFalse(result["success"], result)
        self.assertEqual(result["deleted"], ["a1"])
        self.assertTrue(entity.deleted)
        self.assertEqual(result["failed"][0]["handle"], "missing")
        doc.Regen.assert_called_once_with(0)

    def test_get_current_view_prefers_system_variables(self):
        doc = MagicMock()
        stale_view = MagicMock()
        stale_view.Center = [9999, 9999]
        stale_view.Height = 9999
        stale_view.Width = 9999
        doc.ActiveViewport = stale_view
        values = {
            "VIEWCTR": [10.0, 20.0],
            "TARGET": [0.0, 0.0, 0.0],
            "VIEWDIR": [0.0, 0.0, 1.0],
            "VIEWSIZE": 50.0,
            "SCREENSIZE": [1600.0, 1000.0],
            "VIEWTWIST": 0.25,
        }
        doc.GetVariable.side_effect = lambda name: values[name]
        controller = self._controller_with_doc(doc)

        view = controller.get_current_view()

        self.assertEqual(view["center"], [10.0, 20.0, 0.0])
        self.assertEqual(view["height"], 50.0)
        self.assertEqual(view["width"], 80.0)
        self.assertEqual(view["twist"], 0.25)
        self.assertEqual(view["source"], "system_variables")

    def test_get_acad_state_tolerates_missing_loaded_property(self):
        from src.cad_controller import CADController

        class State:
            IsQuiescent = True

        controller = CADController()
        controller.acad = MagicMock()
        controller.acad.GetAcadState.return_value = State()

        with patch.object(controller, "_ensure_connected") as ensure:
            result = controller.get_acad_state()

        ensure.assert_called_once_with()
        self.assertEqual(result, {"is_quiescent": True, "is_loaded": True})

    def test_export_wmf_supplies_all_modelspace_entities(self):
        doc = MagicMock()
        selection_set = MagicMock()
        ent_a = MagicMock()
        ent_b = MagicMock()
        ent_c = MagicMock()
        doc.SelectionSets.Item.side_effect = Exception("not found")
        doc.SelectionSets.Add.return_value = selection_set
        doc.ModelSpace.Count = 3
        doc.ModelSpace.Item.side_effect = [ent_a, ent_b, ent_c]
        controller = self._controller_with_doc(doc)

        with patch("src.cad_controller.os.path.exists", return_value=True), \
             patch("src.cad_controller.os.path.getsize", return_value=1):
            result = controller.export_drawing(r"C:\tmp\out.wmf", "WMF")

        self.assertTrue(result["success"], result)
        selection_set.AddItems.assert_called_once_with([ent_a, ent_b, ent_c])
        doc.Export.assert_called_once_with(r"C:\tmp\out", "WMF", selection_set)
        selection_set.Delete.assert_called_once()

    def test_text_tool_mleader_handles_controller_error_dict(self):
        with patch.object(text_tools, "ctrl") as mock_ctrl:
            mock_ctrl.add_mleader.return_value = {
                "success": False,
                "message": "boom",
            }

            result = text_tools.add_mleader("Note", [0, 0, 0, 10, 10, 0])

        self.assertIn("boom", result)

    def test_text_tool_mleader_accepts_nested_point_lists(self):
        mleader = MagicMock()
        mleader.Handle = "ML1"
        with patch.object(text_tools, "ctrl") as mock_ctrl:
            mock_ctrl.add_mleader.return_value = mleader

            result = text_tools.add_mleader(
                "Note",
                [[0, 0, 0], [10, 10, 0]],
            )

        self.assertIn("ML1", result)
        mock_ctrl.add_mleader.assert_called_once_with(
            "Note",
            [(0.0, 0.0, 0.0), (10.0, 10.0, 0.0)],
        )


# ══════════════════════════════════════════════════════════════════
#  Test: Bug detection – known issues that need fixing
# ══════════════════════════════════════════════════════════════════

class TestDocumentOpenBugs(unittest.TestCase):
    """Regression tests for document lifecycle behavior."""

    def test_open_drawing_allows_autocad_start_tab_without_document(self):
        from src.cad_controller import CADController

        controller = CADController()
        acad = MagicMock()
        acad.Documents.Count = 0
        doc = MagicMock()
        doc.Name = "opened.dwg"
        acad.Documents.Open.return_value = doc
        controller.acad = acad
        controller.doc = None

        with patch.object(controller, "_ensure_connected") as ensure:
            result = controller.open_drawing(r"C:\drawings\opened.dwg")

        ensure.assert_called_once_with()
        acad.Documents.Open.assert_called_once_with(r"C:\drawings\opened.dwg")
        self.assertTrue(result["success"], result)
        self.assertEqual(controller.doc, doc)
        self.assertEqual(result["name"], "opened.dwg")


class TestViewportToolBugs(unittest.TestCase):
    """Regression tests for paper-space viewport delivery-view helpers."""

    def _controller_with_doc(self, doc):
        from src.cad_controller import CADController
        controller = CADController()
        controller.acad = MagicMock()
        controller.acad.Documents.Count = 1
        controller.acad.ActiveDocument = doc
        controller.doc = doc
        return controller

    def test_get_pviewports_returns_delivery_geometry_and_tolerates_bad_properties(self):
        class FakeLayout:
            Name = "Layout1"

        class FakeViewport:
            ObjectName = "AcDbViewport"
            Handle = "VP1"
            Layer = "A-VPORT"
            Center = (10, 20, 0)
            Width = 100
            Height = 50
            Target = (1000, 2000, 0)
            Direction = (0, 0, 1)
            TwistAngle = 0.25
            DisplayLocked = True
            StandardScale = 0
            StandardScale2 = 5
            CustomScale = 0.02
            ViewportOn = True
            Visible = True
            Clipped = False

            @property
            def ViewCenter(self):
                raise Exception("ViewCenter is not available on AcadPViewport")

        class BadEntity:
            @property
            def ObjectName(self):
                raise Exception("COM property failure")

        class FakePaperSpace:
            Count = 2

            def __init__(self):
                self.items = [BadEntity(), FakeViewport()]

            def Item(self, index):
                return self.items[index]

        doc = MagicMock()
        doc.ActiveLayout = FakeLayout()
        doc.PaperSpace = FakePaperSpace()
        controller = self._controller_with_doc(doc)

        result = controller.get_pviewports()

        self.assertEqual(len(result), 1)
        viewport = result[0]
        self.assertEqual(viewport["handle"], "VP1")
        self.assertEqual(viewport["layout"], "Layout1")
        self.assertEqual(viewport["center"], [10.0, 20.0, 0.0])
        self.assertEqual(viewport["paper_center"], [10.0, 20.0, 0.0])
        self.assertEqual(viewport["width"], 100.0)
        self.assertEqual(viewport["height"], 50.0)
        self.assertEqual(
            viewport["paper_bounds"],
            {"min": [-40.0, -5.0, 0.0], "max": [60.0, 45.0, 0.0]},
        )
        self.assertEqual(viewport["target"], [1000.0, 2000.0, 0.0])
        self.assertEqual(viewport["view_center"], [1000.0, 2000.0, 0.0])
        self.assertEqual(viewport["direction"], [0.0, 0.0, 1.0])

    def test_advanced_tools_add_viewport_displays_created_viewport(self):
        viewport = MagicMock()
        viewport.Handle = "VP1"

        with patch.object(advanced_tools, "ctrl") as mock_ctrl:
            mock_ctrl.add_pviewport.return_value = viewport

            result = advanced_tools.add_viewport(10, 20, 100, 50)

        viewport.Display.assert_called_once_with(True)
        self.assertTrue(viewport.ViewportOn)
        self.assertIn("VP1", result)

    def test_advanced_tools_set_viewport_properties_reports_controller_failure(self):
        with patch.object(advanced_tools, "ctrl") as mock_ctrl:
            mock_ctrl.set_pviewport_props.return_value = {
                "success": False,
                "message": "Not a viewport",
            }

            result = advanced_tools.set_viewport_properties("BAD", display_locked=True)

        self.assertIn("设置视口属性失败", result)
        self.assertIn("Not a viewport", result)

    def test_set_pviewport_props_uses_display_for_on_state(self):
        doc = MagicMock()
        viewport = MagicMock()
        viewport.ObjectName = "AcDbViewport"
        doc.HandleToObject.return_value = viewport
        controller = self._controller_with_doc(doc)

        result = controller.set_pviewport_props("VP1", on=True)

        self.assertTrue(result["success"], result)
        viewport.Display.assert_called_once_with(True)
        self.assertEqual(result["changed"], {"on": True})

    def test_set_pviewport_props_reports_failure_when_no_properties_changed(self):
        class ReadOnlyViewport:
            ObjectName = "AcDbViewport"

            def Display(self, state):
                raise Exception("Display failed")

            def __setattr__(self, name, value):
                if name.lower() == "viewporton":
                    raise Exception("ViewportOn is read-only")
                super().__setattr__(name, value)

        doc = MagicMock()
        doc.HandleToObject.return_value = ReadOnlyViewport()
        controller = self._controller_with_doc(doc)

        result = controller.set_pviewport_props("VP1", on=True)

        self.assertFalse(result["success"], result)
        self.assertEqual(result["changed"], {})


class TestBlockToolBugs(unittest.TestCase):
    """Regression tests for block and xref helper delivery behavior."""

    def _controller_with_doc(self, doc):
        from src.cad_controller import CADController
        controller = CADController()
        controller.acad = MagicMock()
        controller.acad.Documents.Count = 1
        controller.acad.ActiveDocument = doc
        controller.doc = doc
        return controller

    def test_get_xrefs_uses_dedicated_lightweight_block_scan(self):
        class FakeBlock:
            def __init__(self, name, is_xref, path=""):
                self.Name = name
                self.IsXRef = is_xref
                self._path = path
                self.slow_reads = []

            @property
            def Path(self):
                self.slow_reads.append("Path")
                if not self.IsXRef:
                    raise Exception("Path should not be read for ordinary blocks")
                return self._path

            @property
            def Count(self):
                self.slow_reads.append("Count")
                raise Exception("Count should not be read by get_xrefs")

            @property
            def Origin(self):
                self.slow_reads.append("Origin")
                raise Exception("Origin should not be read by get_xrefs")

        class FakeBlocks:
            def __init__(self, items):
                self.items = items
                self.Count = len(items)

            def Item(self, index):
                return self.items[index]

        normal = FakeBlock("NORMAL", False)
        xref = FakeBlock("XR_MAIN", True, r"C:\refs\main.dwg")
        doc = MagicMock()
        doc.Blocks = FakeBlocks([normal, xref])
        controller = self._controller_with_doc(doc)

        result = controller.get_xrefs()

        self.assertEqual(result, [{"name": "XR_MAIN", "path": r"C:\refs\main.dwg"}])
        self.assertEqual(normal.slow_reads, [])
        self.assertEqual(xref.slow_reads, ["Path"])

    def test_block_tools_get_xrefs_does_not_rescan_all_blocks(self):
        with patch.object(block_tools, "ctrl") as mock_ctrl:
            mock_ctrl.has_document = True
            mock_ctrl.get_xrefs.return_value = [
                {"name": "XR_MAIN", "path": r"C:\refs\main.dwg"}
            ]

            result = block_tools.get_xrefs()

        mock_ctrl.get_xrefs.assert_called_once_with()
        mock_ctrl.get_all_blocks.assert_not_called()
        self.assertIn("XR_MAIN", result)
        self.assertIn("main.dwg", result)


class TestLayerToolBugs(unittest.TestCase):
    """Regression tests for large layer-table operations."""

    def test_layer_tools_isolate_uses_controller_fast_path(self):
        with patch.object(layer_tools, "ctrl") as mock_ctrl:
            mock_ctrl.isolate_layer.return_value = {
                "success": True,
                "message": "isolated",
            }

            result = layer_tools.isolate_layer("A-WALL")

        self.assertEqual(result, "isolated")
        mock_ctrl.isolate_layer.assert_called_once_with("A-WALL")
        mock_ctrl.get_all_layers.assert_not_called()
        mock_ctrl.set_layer_state.assert_not_called()

    def test_layer_tools_unisolate_uses_controller_fast_path(self):
        with patch.object(layer_tools, "ctrl") as mock_ctrl:
            mock_ctrl.unisolate_layers.return_value = {
                "success": True,
                "message": "unisolate",
            }

            result = layer_tools.unisolate_layers()

        self.assertEqual(result, "unisolate")
        mock_ctrl.unisolate_layers.assert_called_once_with()
        mock_ctrl.get_all_layers.assert_not_called()
        mock_ctrl.set_layer_state.assert_not_called()


class TestTextToolBugs(unittest.TestCase):
    """Regression tests for text search on large drawings."""

    class _NoModelSpaceDoc:
        @property
        def ModelSpace(self):
            raise AssertionError("text search should use filtered selection sets")

    class _FakeSelectionSet:
        def __init__(self, entities):
            self.entities = entities
            self.deleted = False
            self.select_args = None

        def Select(self, *args):
            self.select_args = args

        def Delete(self):
            self.deleted = True

        def __iter__(self):
            return iter(self.entities)

    class _FakeText:
        ObjectName = "AcDbText"
        Handle = "T1"
        Layer = "TEXT"
        Color = 256

        def __init__(self, value):
            self.TextString = value

    class _FakeTextStyle:
        def __init__(self, font_args=None, setfont_error=None,
                     fontfile_failures=None):
            self.font_args = font_args
            self.setfont_error = setfont_error
            self.fontfile_failures = set(fontfile_failures or [])
            self.set_font_calls = []
            self.font_file_values = []
            self.Height = None
            self.Width = None

        def GetFont(self):
            if isinstance(self.font_args, Exception):
                raise self.font_args
            return self.font_args

        def SetFont(self, typeface, bold, italic, charset, pitch_and_family):
            self.set_font_calls.append(
                (typeface, bold, italic, charset, pitch_and_family)
            )
            if self.setfont_error:
                raise self.setfont_error

        @property
        def FontFile(self):
            return self.font_file_values[-1] if self.font_file_values else ""

        @FontFile.setter
        def FontFile(self, value):
            if value in self.fontfile_failures:
                raise Exception(f"invalid font file {value}")
            self.font_file_values.append(value)

        @property
        def fontFile(self):
            return self.FontFile

        @fontFile.setter
        def fontFile(self, value):
            self.FontFile = value

    class _FakeTextStyleOleObject:
        def __init__(self):
            self.invocations = []

        def GetIDsOfNames(self, name):
            if name in {"FontFile", "fontFile"}:
                return 42
            raise AttributeError(name)

        def Invoke(self, dispid, lcid, flags, result_wanted, *args):
            self.invocations.append((dispid, lcid, flags, result_wanted, args))

    class _FakeGeneratedTextStyle:
        __slots__ = (
            "font_args", "setfont_error", "set_font_calls", "Height", "Width",
            "_oleobj_",
        )

        def __init__(self, font_args=None, setfont_error=None):
            self.font_args = font_args
            self.setfont_error = setfont_error
            self.set_font_calls = []
            self.Height = None
            self.Width = None
            self._oleobj_ = TestTextToolBugs._FakeTextStyleOleObject()

        def __setattr__(self, name, value):
            if name in {"FontFile", "fontFile"}:
                raise AttributeError(name)
            object.__setattr__(self, name, value)

        def GetFont(self):
            return self.font_args

        def SetFont(self, typeface, bold, italic, charset, pitch_and_family):
            self.set_font_calls.append(
                (typeface, bold, italic, charset, pitch_and_family)
            )
            if self.setfont_error:
                raise self.setfont_error

    class _FakeTextStyles:
        Count = 0

        def __init__(self, style):
            self.style = style
            self.added_names = []

        def Add(self, name):
            self.added_names.append(name)
            return self.style

    def _controller_with_text_style(self, style, active_style=None):
        from src.cad_controller import CADController
        doc = MagicMock()
        doc.TextStyles = self._FakeTextStyles(style)
        doc.ActiveTextStyle = active_style or self._FakeTextStyle(
            font_args=("Arial", False, False, 0, 34)
        )
        controller = CADController()
        controller.acad = MagicMock()
        controller.acad.Documents.Count = 1
        controller.acad.ActiveDocument = doc
        controller.doc = doc
        return controller, doc

    def test_find_text_uses_filtered_selection_set(self):
        entity = self._FakeText("needle here")
        selection = self._FakeSelectionSet([entity])
        with patch.object(text_tools, "ctrl") as mock_ctrl:
            mock_ctrl.has_document = True
            mock_ctrl.acad.ActiveDocument = self._NoModelSpaceDoc()
            mock_ctrl.create_selection_set.return_value = selection

            result = text_tools.find_text("needle", highlight_color=3)

        self.assertIn("T1", result)
        self.assertEqual(entity.Color, 3)
        self.assertTrue(selection.deleted)
        self.assertEqual(selection.select_args[0], 5)
        mock_ctrl.create_selection_set.assert_called_once()

    def test_replace_text_uses_filtered_selection_set(self):
        entity = self._FakeText("old value")
        selection = self._FakeSelectionSet([entity])
        with patch.object(text_tools, "ctrl") as mock_ctrl:
            mock_ctrl.has_document = True
            mock_ctrl.acad.ActiveDocument = self._NoModelSpaceDoc()
            mock_ctrl.create_selection_set.return_value = selection

            result = text_tools.replace_text("old", "new")

        self.assertIn("1", result)
        self.assertEqual(entity.TextString, "new value")
        self.assertTrue(selection.deleted)
        self.assertEqual(selection.select_args[0], 5)

    def test_create_text_style_reuses_valid_pitch_and_family(self):
        style = self._FakeTextStyle(font_args=Exception("new style has no font"))
        active_style = self._FakeTextStyle(
            font_args=("Arial", False, False, 0, 32)
        )
        controller, doc = self._controller_with_text_style(style, active_style)

        result = controller.create_text_style("AI_NOTE", "SimSun", 2.5, 0.85)

        self.assertTrue(result["success"], result)
        self.assertEqual(doc.TextStyles.added_names, ["AI_NOTE"])
        self.assertEqual(
            style.set_font_calls,
            [("SimSun", False, False, 0, 32)],
        )
        self.assertEqual(style.Height, 2.5)
        self.assertEqual(style.Width, 0.85)

    def test_create_text_style_uses_fontfile_for_font_files(self):
        style = self._FakeTextStyle()
        controller, _ = self._controller_with_text_style(style)

        result = controller.create_text_style("SHX_NOTE", "romans.shx")

        self.assertTrue(result["success"], result)
        self.assertEqual(style.set_font_calls, [])
        self.assertEqual(style.font_file_values, ["romans.shx"])

    def test_create_text_style_uses_late_bound_fontfile_when_wrapper_hides_property(self):
        style = self._FakeGeneratedTextStyle()
        controller, _ = self._controller_with_text_style(style)

        result = controller.create_text_style("SHX_NOTE", "romans.shx")

        self.assertTrue(result["success"], result)
        self.assertEqual(style.set_font_calls, [])
        self.assertEqual(
            style._oleobj_.invocations,
            [(42, 0, 4, 0, ("romans.shx",))],
        )

    def test_create_text_style_falls_back_to_shx_fontfile(self):
        style = self._FakeTextStyle(
            setfont_error=Exception("AutoCAD input invalid"),
            fontfile_failures={"romans"},
        )
        controller, _ = self._controller_with_text_style(style)

        result = controller.create_text_style("ROMANS_NOTE", "romans")

        self.assertTrue(result["success"], result)
        self.assertEqual(style.set_font_calls[0][0], "romans")
        self.assertEqual(style.font_file_values, ["romans.shx"])


class TestSelectionToolBugs(unittest.TestCase):
    """Regression tests for selection helpers on large drawings."""

    def _controller_with_doc(self, doc):
        from src.cad_controller import CADController
        controller = CADController()
        controller.acad = MagicMock()
        controller.acad.Documents.Count = 1
        controller.acad.ActiveDocument = doc
        controller.doc = doc
        return controller

    def test_select_all_large_drawing_returns_handle_sample_without_global_selection(self):
        class FakeEntity:
            def __init__(self, handle):
                self.Handle = handle

        class FakeModelSpace:
            Count = 5

            def Item(self, index):
                return FakeEntity(f"H{index}")

        doc = MagicMock()
        doc.ModelSpace = FakeModelSpace()
        doc.SelectionSets.Add.side_effect = AssertionError(
            "large select_all should not create a global selection set"
        )
        controller = self._controller_with_doc(doc)

        result = controller.select_all(max_handles=2, max_com_selection=3)

        self.assertTrue(result["success"], result)
        self.assertFalse(result["selected"])
        self.assertEqual(result["count"], 5)
        self.assertEqual(result["handles"], ["H0", "H1"])
        self.assertTrue(result["truncated"])
        doc.SelectionSets.Add.assert_not_called()

    def test_query_tool_select_all_reports_sampled_large_selection(self):
        with patch.object(query_tools, "ctrl") as mock_ctrl:
            mock_ctrl.select_all.return_value = {
                "success": True,
                "count": 5000,
                "handles": ["H1", "H2"],
                "selected": False,
                "truncated": True,
            }

            result = query_tools.select_all()

        self.assertIn("H1", result)
        self.assertIn("5000", result)
        self.assertIn("truncated", result)


class TestScanToolBugs(unittest.TestCase):
    """Regression tests for scanning large drawings without per-entity DB writes."""

    def _controller_with_doc(self, doc):
        from src.cad_controller import CADController
        controller = CADController()
        controller.acad = MagicMock()
        controller.acad.Documents.Count = 1
        controller.acad.ActiveDocument = doc
        controller.doc = doc
        return controller

    def test_minimal_scan_avoids_dispatch_and_detail_properties(self):
        class FakeEntity:
            ObjectName = "AcDbLine"
            Handle = "H1"
            Layer = "WALL"

            @property
            def Color(self):
                raise AssertionError("minimal scan should not read Color")

            @property
            def StartPoint(self):
                raise AssertionError("minimal scan should not read geometry")

            def GetBoundingBox(self):
                return ((0, 0, 0), (10, 1, 0))

        class FakeModelSpace:
            Count = 1

            def Item(self, index):
                return FakeEntity()

        doc = MagicMock()
        doc.ModelSpace = FakeModelSpace()
        controller = self._controller_with_doc(doc)

        with patch("src.cad_controller.win32com.client.Dispatch") as dispatch:
            result = controller.scan_model_space(
                max_entities=1,
                detail_level="minimal",
                include_bounding_boxes=True,
            )

        dispatch.assert_not_called()
        entity = result["entities"][0]
        self.assertEqual(entity["handle"], "H1")
        self.assertEqual(entity["bbox"], [0.0, 0.0, 10.0, 1.0])
        self.assertNotIn("color", entity)
        self.assertNotIn("start", entity)

    def test_standard_scan_dispatches_for_geometry_properties(self):
        class FakeEntity:
            ObjectName = "AcDbLine"
            Handle = "H1"
            Layer = "WALL"
            Color = 256
            Linetype = "ByLayer"

            def GetBoundingBox(self):
                return ((0, 0, 0), (10, 0, 0))

        class FakeTypedLine:
            StartPoint = (0, 0, 0)
            EndPoint = (10, 0, 0)
            Length = 10

        class FakeModelSpace:
            Count = 1

            def Item(self, index):
                return FakeEntity()

        doc = MagicMock()
        doc.ModelSpace = FakeModelSpace()
        controller = self._controller_with_doc(doc)

        with patch("src.cad_controller.win32com.client.Dispatch", return_value=FakeTypedLine()) as dispatch:
            result = controller.scan_model_space(
                max_entities=1,
                detail_level="standard",
                include_bounding_boxes=True,
            )

        dispatch.assert_called_once()
        entity = result["entities"][0]
        self.assertEqual(entity["start"], [0.0, 0.0, 0.0])
        self.assertEqual(entity["end"], [10.0, 0.0, 0.0])
        self.assertEqual(entity["length"], 10.0)

    def test_scan_all_entities_uses_lightweight_scan_and_batch_write(self):
        with patch.object(query_tools, "ctrl") as mock_ctrl, \
             patch.object(query_tools, "db") as mock_db:
            mock_ctrl.get_document_info.return_value = {
                "name": "large.dwg",
                "full_name": r"C:\drawings\large.dwg",
            }
            mock_ctrl.scan_model_space.return_value = {
                "entities": [
                    {
                        "handle": "H1",
                        "type": "AcDbLine",
                        "name": "Line",
                        "layer": "WALL",
                        "bbox": [0, 0, 10, 1],
                    },
                    {"index": 2, "error": "bad entity"},
                ],
                "type_stats": {"AcDbLine": 1},
                "total_available": 10000,
                "scanned": 2,
                "truncated": True,
                "detail_level": "minimal",
            }
            mock_db.upsert_entities_batch.return_value = 1

            result = query_tools.scan_all_entities(max_entities=2)

        mock_ctrl.scan_model_space.assert_called_once_with(
            2,
            detail_level="minimal",
            include_bounding_boxes=True,
        )
        mock_db.upsert_entity.assert_not_called()
        mock_db.upsert_entities_batch.assert_called_once()
        records = mock_db.upsert_entities_batch.call_args.args[0]
        self.assertEqual(records[0]["handle"], "H1")
        self.assertEqual(records[0]["bbox"], (0, 0, 10, 1))
        self.assertEqual(records[0]["geometry"], {})
        self.assertTrue(mock_db.upsert_entities_batch.call_args.kwargs["derive_topology"])
        self.assertFalse(mock_db.upsert_entities_batch.call_args.kwargs["derive_bbox"])
        self.assertEqual(mock_db.upsert_entities_batch.call_args.kwargs["topology_detail"], "summary")
        self.assertIn("truncated=True", result)
        self.assertIn("Skipped 1 entities", result)
        self.assertIn("Topology summaries were derived", result)


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
        """chamfer_entities and fillet_entities use run_lisp or ctrl wrappers."""
        source = inspect.getsource(edit_tools.fillet_entities)
        self.assertIn('ctrl.fillet', source)
        source = inspect.getsource(edit_tools.chamfer_entities)
        self.assertIn('ctrl.chamfer', source)

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
