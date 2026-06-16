"""Guarded CAD plan DSL with dry-run and explicit execution gates."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from .result import ToolResult, error_result, ok_result

SAFE_PLAN_OPS = {
    "draw_line",
    "draw_circle",
    "draw_rectangle",
    "draw_polyline",
    "draw_polygon",
    "draw_text",
    "draw_mtext",
    "move_entity",
    "rotate_entity",
    "copy_entity",
    "delete_entity",
    "delete_entities",
    "scale_entity",
    "mirror_entity",
    "offset_entity",
    "array_rectangular",
    "array_polar",
    "set_entity_properties",
    "create_layer",
    "set_current_layer",
    "add_linear_dimension",
    "add_radial_dimension",
    "add_diametric_dimension",
    "add_hatch",
    "hatch_add_boundary",
    "create_block",
    "insert_block",
}

DANGEROUS_OPS = {"send_command", "execute_sql_query", "execute_query", "purge_drawing", "audit_drawing"}


def _tool_dispatch() -> Dict[str, Callable[..., Any]]:
    from src.cad_tools import (
        block_tools,
        dimension_tools,
        drawing_tools,
        edit_tools,
        hatch_tools,
        layer_tools,
    )

    modules = [drawing_tools, edit_tools, layer_tools, dimension_tools, hatch_tools, block_tools]
    dispatch: Dict[str, Callable[..., Any]] = {}
    for module in modules:
        for op in SAFE_PLAN_OPS:
            fn = getattr(module, op, None)
            if callable(fn):
                dispatch[op] = fn
    return dispatch


def _normalize_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "plan_id": str(plan.get("plan_id") or ""),
        "description": str(plan.get("description") or ""),
        "units": str(plan.get("units") or "drawing_units"),
        "steps": list(plan.get("steps") or []),
        "constraints": list(plan.get("constraints") or []),
        "risk_level": str(plan.get("risk_level") or "medium"),
        "requires_confirmation": bool(plan.get("requires_confirmation", True)),
        "allow_dangerous": bool(plan.get("allow_dangerous", False)),
    }


def validate_cad_plan(plan: Dict[str, Any]) -> ToolResult:
    normalized = _normalize_plan(plan or {})
    errors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    if not normalized["steps"]:
        errors.append({"path": "steps", "message": "Plan must contain at least one step."})
    step_ids = set()
    for index, step in enumerate(normalized["steps"]):
        step_id = str(step.get("step_id") or f"step_{index + 1}")
        op = str(step.get("op") or "")
        if step_id in step_ids:
            errors.append({"path": f"steps[{index}].step_id", "message": f"Duplicate step_id {step_id}."})
        step_ids.add(step_id)
        if op in DANGEROUS_OPS and not normalized["allow_dangerous"]:
            errors.append({
                "path": f"steps[{index}].op",
                "message": f"{op} is disallowed in CAD plans unless allow_dangerous=true.",
            })
        elif op not in SAFE_PLAN_OPS:
            errors.append({"path": f"steps[{index}].op", "message": f"Unknown or unsupported operation: {op}."})
        if not isinstance(step.get("args", {}), dict):
            errors.append({"path": f"steps[{index}].args", "message": "Step args must be a dict."})
        for dep in step.get("depends_on", []) or []:
            if dep not in step_ids:
                warnings.append(f"Step {step_id} depends on {dep}; dependency order was not verified before declaration.")
    data = {"plan": normalized, "errors": errors, "warnings": warnings, "valid": not errors}
    if errors:
        return error_result("CAD plan validation failed.", data=data, warnings=warnings)
    return ok_result(
        "CAD plan is valid.",
        data=data,
        warnings=warnings,
        next_tools=["dry_run_cad_plan", "execute_cad_plan"],
    )


def dry_run_cad_plan(plan: Dict[str, Any]) -> ToolResult:
    validation = validate_cad_plan(plan)
    if not validation["ok"]:
        return validation
    normalized = validation["data"]["plan"]
    steps = []
    affected_handles: List[str] = []
    for step in normalized["steps"]:
        args = step.get("args", {}) or {}
        handles = []
        for key in ("handle", "target_handle", "tool_handle"):
            if args.get(key):
                handles.append(str(args[key]))
        if isinstance(args.get("handles"), list):
            handles.extend(str(handle) for handle in args["handles"])
        affected_handles.extend(handles)
        op = step.get("op")
        steps.append({
            "step_id": step.get("step_id"),
            "op": op,
            "would_modify_dwg": bool(step.get("writes", True)),
            "affected_handles": handles,
            "expected_entity_type": _expected_entity_type(op),
            "args": args,
        })
    return ok_result(
        f"Dry-run completed for {len(steps)} CAD plan steps. No DWG changes were made.",
        data={
            "plan_id": normalized["plan_id"],
            "steps": steps,
            "constraints": normalized.get("constraints", []),
            "risk_level": normalized.get("risk_level", "medium"),
            "requires_confirmation": True,
        },
        handles=sorted(set(affected_handles)),
        warnings=["Dry-run is static and does not call AutoCAD."],
        next_tools=["execute_cad_plan"],
    )


def _expected_entity_type(op: str) -> Optional[str]:
    mapping = {
        "draw_line": "AcDbLine",
        "draw_circle": "AcDbCircle",
        "draw_rectangle": "AcDbPolyline",
        "draw_polyline": "AcDbPolyline",
        "draw_polygon": "AcDbPolyline",
        "draw_text": "AcDbText",
        "draw_mtext": "AcDbMText",
        "insert_block": "AcDbBlockReference",
        "add_linear_dimension": "AcDbRotatedDimension",
        "add_radial_dimension": "AcDbRadialDimension",
        "add_diametric_dimension": "AcDbDiametricDimension",
    }
    return mapping.get(op)


def execute_cad_plan(plan: Dict[str, Any],
                     allow_modify: bool = False) -> ToolResult:
    validation = validate_cad_plan(plan)
    if not validation["ok"]:
        return validation
    if not allow_modify:
        return error_result(
            "execute_cad_plan refused to modify the DWG because allow_modify is not true.",
            data={"plan_id": validation["data"]["plan"].get("plan_id")},
            next_tools=["dry_run_cad_plan"],
        )
    normalized = validation["data"]["plan"]
    dispatch = _tool_dispatch()
    results = []
    handles: List[str] = []
    for step in normalized["steps"]:
        op = step.get("op")
        fn = dispatch.get(op)
        if fn is None:
            return error_result(f"Validated operation has no execution binding: {op}")
        args = step.get("args", {}) or {}
        try:
            result = fn(**args)
            results.append({"step_id": step.get("step_id"), "op": op, "result": result})
            if isinstance(result, str):
                for token in result.replace(",", " ").split():
                    if token.lower().startswith("handle"):
                        handles.append(token.split(":", 1)[-1])
        except Exception as exc:
            return error_result(
                f"CAD plan execution failed at step {step.get('step_id')}: {exc}",
                data={"completed_steps": results},
            )
    return ok_result(
        f"Executed {len(results)} CAD plan steps.",
        data={"plan_id": normalized["plan_id"], "results": results},
        handles=handles,
        warnings=["Execution modified the DWG through existing safe MCP tool implementations."],
        next_tools=["scan_all_entities", "validate_geometry", "export_view_image_with_mapping"],
    )

