"""Guarded CAD plan DSL with variables, dry-run, and transactional execution."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .result import ToolResult, error_result, ok_result

SAFE_PLAN_OPS = {
    "draw_line",
    "draw_arc",
    "draw_circle",
    "draw_donut",
    "draw_ellipse",
    "draw_rectangle",
    "draw_polyline",
    "draw_polygon",
    "draw_spline",
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
    "add_mleader",
    "add_table",
    "edit_table_cell",
    "add_linear_dimension",
    "add_radial_dimension",
    "add_diametric_dimension",
    "add_hatch",
    "hatch_add_boundary",
    "chamfer_polyline",
    "fillet_polyline",
    "create_block",
    "insert_block",
    "set_dimension_text_override",
}

DANGEROUS_OPS = {"send_command", "execute_sql_query", "execute_query", "purge_drawing", "audit_drawing"}
PLAN_OP_ARG_SCHEMAS = {
    "draw_line": {
        "required": {"start_x", "start_y", "end_x", "end_y"},
        "optional": {"start_z", "end_z", "layer", "color"},
        "aliases": {
            "start": "Use start_x/start_y/start_z fields instead of a start point array.",
            "end": "Use end_x/end_y/end_z fields instead of an end point array.",
        },
    },
}
VARIABLE_RE = re.compile(r"^\$[A-Za-z_][A-Za-z0-9_]*$")
HANDLE_RE = re.compile(r"(?:handle(?:s)?\s*[:=]?\s*)([A-Za-z0-9_.:-]+)", re.IGNORECASE)
FAILURE_TEXT_RE = re.compile(
    r"(^\s*ERROR\b|\bfailed\b|\bfailure\b|\bexception\b|unable to connect|"
    r"no drawing|invalid literal|refused|\u5931\u8d25|\u9519\u8bef|"
    r"\u62d2\u7edd|\u6fb6\u8fab\u89e6)",
    re.IGNORECASE,
)


def _tool_dispatch() -> Dict[str, Callable[..., Any]]:
    from src.cad_tools import (
        block_tools,
        dimension_tools,
        drawing_tools,
        edit_tools,
        hatch_tools,
        layer_tools,
        text_tools,
    )

    modules = [drawing_tools, edit_tools, layer_tools, dimension_tools, hatch_tools, block_tools, text_tools]
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
        "variables": dict(plan.get("variables") or {}),
        "steps": list(plan.get("steps") or []),
        "constraints": list(plan.get("constraints") or []),
        "risk_level": str(plan.get("risk_level") or "medium"),
        "requires_confirmation": bool(plan.get("requires_confirmation", True)),
        "allow_dangerous": bool(plan.get("allow_dangerous", False)),
    }


def _var_name(value: str) -> str:
    return value[1:] if value.startswith("$") else value


def _collect_variables(value: Any) -> Set[str]:
    variables: Set[str] = set()
    if isinstance(value, str) and VARIABLE_RE.match(value.strip()):
        variables.add(_var_name(value.strip()))
    elif isinstance(value, dict):
        for nested in value.values():
            variables.update(_collect_variables(nested))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            variables.update(_collect_variables(nested))
    return variables


def _resolve_value(value: Any,
                   state: Dict[str, Any],
                   allow_future: bool = False) -> Tuple[Any, List[str]]:
    unresolved: List[str] = []
    if isinstance(value, str) and VARIABLE_RE.match(value.strip()):
        name = _var_name(value.strip())
        if name in state:
            return state[name], []
        if allow_future:
            return {"unresolved_variable": value}, [name]
        return value, [name]
    if isinstance(value, dict):
        resolved: Dict[str, Any] = {}
        for key, nested in value.items():
            resolved_value, nested_unresolved = _resolve_value(nested, state, allow_future=allow_future)
            resolved[key] = resolved_value
            unresolved.extend(nested_unresolved)
        return resolved, unresolved
    if isinstance(value, list):
        resolved_list = []
        for nested in value:
            resolved_value, nested_unresolved = _resolve_value(nested, state, allow_future=allow_future)
            resolved_list.append(resolved_value)
            unresolved.extend(nested_unresolved)
        return resolved_list, unresolved
    return value, []


def resolve_plan_variables(plan: Dict[str, Any],
                           state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    normalized = _normalize_plan(plan or {})
    runtime_state = {**normalized["variables"], **(state or {})}
    resolved_steps = []
    unresolved_by_step: Dict[str, List[str]] = {}
    for index, step in enumerate(normalized["steps"]):
        step_id = str(step.get("step_id") or f"step_{index + 1}")
        args, unresolved = _resolve_value(step.get("args", {}) or {}, runtime_state, allow_future=True)
        resolved_steps.append({**step, "step_id": step_id, "args": args})
        if unresolved:
            unresolved_by_step[step_id] = sorted(set(unresolved))
        save_as = str(step.get("save_as") or "").strip()
        if VARIABLE_RE.match(save_as):
            runtime_state[_var_name(save_as)] = {"unresolved_future_handle": save_as, "from_step": step_id}
    return {
        "plan": {**normalized, "steps": resolved_steps},
        "state": runtime_state,
        "unresolved_by_step": unresolved_by_step,
    }


def _validate_variables(normalized: Dict[str, Any]) -> List[Dict[str, Any]]:
    errors: List[Dict[str, Any]] = []
    known = set(normalized.get("variables", {}).keys())
    for index, step in enumerate(normalized["steps"]):
        step_id = str(step.get("step_id") or f"step_{index + 1}")
        refs = _collect_variables(step.get("args", {}) or {})
        missing = sorted(ref for ref in refs if ref not in known)
        for ref in missing:
            errors.append({
                "path": f"steps[{index}].args",
                "message": f"Unknown variable ${ref} referenced before it is defined.",
            })
        save_as = str(step.get("save_as") or "").strip()
        if save_as:
            if not VARIABLE_RE.match(save_as):
                errors.append({
                    "path": f"steps[{index}].save_as",
                    "message": "save_as must be a variable name like $outer_circle.",
                })
            else:
                known.add(_var_name(save_as))
        for post_index, postcondition in enumerate(step.get("postconditions", []) or []):
            for ref in _collect_variables(postcondition):
                if ref not in known:
                    errors.append({
                        "path": f"steps[{index}].postconditions[{post_index}]",
                        "message": f"Unknown variable ${ref} in postcondition.",
                    })
    return errors


def _validate_step_args(op: str, args: Any, index: int) -> List[Dict[str, Any]]:
    if not isinstance(args, dict):
        return []
    schema = PLAN_OP_ARG_SCHEMAS.get(op)
    if not schema:
        return []

    required = set(schema.get("required", set()))
    optional = set(schema.get("optional", set()))
    aliases = dict(schema.get("aliases", {}))
    allowed = required | optional
    provided = set(args.keys())
    errors: List[Dict[str, Any]] = []

    for name in sorted(required - provided):
        errors.append({
            "path": f"steps[{index}].args.{name}",
            "message": f"{op} requires argument {name}.",
        })
    for name in sorted(provided - allowed):
        hint = aliases.get(name)
        message = f"{op} does not support argument {name}."
        if hint:
            message = f"{message} {hint}"
        errors.append({
            "path": f"steps[{index}].args.{name}",
            "message": message,
        })
    return errors


def validate_cad_plan(plan: Dict[str, Any]) -> ToolResult:
    normalized = _normalize_plan(plan or {})
    errors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    if not normalized["steps"]:
        errors.append({"path": "steps", "message": "Plan must contain at least one step."})
    step_ids = set()
    declared_step_ids = {str(step.get("step_id") or f"step_{index + 1}") for index, step in enumerate(normalized["steps"])}
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
        errors.extend(_validate_step_args(op, step.get("args", {}) or {}, index))
        for dep in step.get("depends_on", []) or []:
            if str(dep) not in declared_step_ids:
                errors.append({"path": f"steps[{index}].depends_on", "message": f"Unknown dependency {dep}."})
            elif str(dep) not in step_ids:
                errors.append({"path": f"steps[{index}].depends_on", "message": f"Dependency {dep} must run before {step_id}."})
        if step.get("postconditions") is not None and not isinstance(step.get("postconditions"), list):
            errors.append({"path": f"steps[{index}].postconditions", "message": "Postconditions must be a list."})
    errors.extend(_validate_variables(normalized))
    data = {"plan": normalized, "errors": errors, "warnings": warnings, "valid": not errors}
    if errors:
        return error_result("CAD plan validation failed.", data=data, warnings=warnings)
    return ok_result(
        "CAD plan is valid.",
        data=data,
        warnings=warnings,
        next_tools=["dry_run_cad_plan", "execute_cad_plan"],
    )


def _expected_entity_type(op: str) -> Optional[str]:
    mapping = {
        "draw_line": "AcDbLine",
        "draw_arc": "AcDbArc",
        "draw_circle": "AcDbCircle",
        "draw_donut": "AcDbPolyline",
        "draw_ellipse": "AcDbEllipse",
        "draw_rectangle": "AcDbPolyline",
        "draw_polyline": "AcDbPolyline",
        "draw_polygon": "AcDbPolyline",
        "draw_spline": "AcDbSpline",
        "draw_text": "AcDbText",
        "draw_mtext": "AcDbMText",
        "add_mleader": "AcDbMLeader",
        "add_table": "AcDbTable",
        "insert_block": "AcDbBlockReference",
        "add_linear_dimension": "AcDbRotatedDimension",
        "add_radial_dimension": "AcDbRadialDimension",
        "add_diametric_dimension": "AcDbDiametricDimension",
    }
    return mapping.get(op)


def _handles_from_args(args: Dict[str, Any]) -> List[str]:
    handles = []
    for key in ("handle", "target_handle", "tool_handle"):
        if args.get(key) and not isinstance(args.get(key), dict):
            handles.append(str(args[key]))
    if isinstance(args.get("handles"), list):
        handles.extend(str(handle) for handle in args["handles"] if not isinstance(handle, dict))
    return handles


def dry_run_cad_plan(plan: Dict[str, Any]) -> ToolResult:
    validation = validate_cad_plan(plan)
    if not validation["ok"]:
        return validation
    normalized = validation["data"]["plan"]
    resolved = resolve_plan_variables(normalized)
    steps = []
    affected_handles: List[str] = []
    for step in resolved["plan"]["steps"]:
        args = step.get("args", {}) or {}
        handles = _handles_from_args(args)
        affected_handles.extend(handles)
        op = step.get("op")
        save_as = str(step.get("save_as") or "")
        steps.append({
            "step_id": step.get("step_id"),
            "op": op,
            "would_modify_dwg": bool(step.get("writes", True)),
            "affected_handles": handles,
            "expected_entity_type": step.get("expect", {}).get("entity_type") or _expected_entity_type(op),
            "args": args,
            "save_as": save_as or None,
            "unresolved_variables": resolved["unresolved_by_step"].get(step.get("step_id"), []),
            "postconditions": step.get("postconditions", []),
        })
    return ok_result(
        f"Dry-run completed for {len(steps)} CAD plan steps. No DWG changes were made.",
        data={
            "plan_id": normalized["plan_id"],
            "steps": steps,
            "constraints": normalized.get("constraints", []),
            "risk_level": normalized.get("risk_level", "medium"),
            "requires_confirmation": True,
            "variable_state": resolved["state"],
            "unresolved_by_step": resolved["unresolved_by_step"],
        },
        handles=sorted(set(affected_handles)),
        warnings=["Dry-run is static and does not call AutoCAD."],
        next_tools=["execute_cad_plan"],
    )


def parse_tool_result_handles(result: Any) -> List[str]:
    handles: List[str] = []
    if isinstance(result, dict):
        for key in ("handles", "handle", "entity_handles"):
            value = result.get(key)
            if isinstance(value, list):
                handles.extend(str(item) for item in value if item)
            elif value:
                handles.append(str(value))
        data = result.get("data")
        if isinstance(data, dict):
            handles.extend(parse_tool_result_handles(data))
    elif isinstance(result, list):
        for item in result:
            handles.extend(parse_tool_result_handles(item))
    elif isinstance(result, str):
        for match in HANDLE_RE.finditer(result):
            handles.append(match.group(1).strip(".,;()[]{}"))
        # Common terse CAD tool strings: "... H1 ..." after "created".
        if not handles and any(word in result.lower() for word in ("created", "handle", "inserted", "drawn")):
            tokens = re.findall(r"\b[A-Fa-f0-9]{2,}\b", result)
            handles.extend(tokens[:3])
    return sorted(dict.fromkeys(handle for handle in handles if handle))


def tool_result_failure_message(result: Any) -> Optional[str]:
    """Return a compact failure message when a CAD tool result is unsuccessful."""
    if isinstance(result, dict):
        if result.get("ok") is False:
            return str(result.get("message") or result.get("error") or "ok=false")
        if result.get("success") is False:
            return str(result.get("message") or result.get("error") or "success=false")
        for key in ("message", "error", "result"):
            value = result.get(key)
            if isinstance(value, str) and FAILURE_TEXT_RE.search(value):
                return value
        data = result.get("data")
        if isinstance(data, dict):
            nested = tool_result_failure_message(data)
            if nested:
                return nested
        return None
    if isinstance(result, str) and FAILURE_TEXT_RE.search(result):
        return result
    return None


def _compact_failure_message(message: str, limit: int = 500) -> str:
    text = " ".join(str(message or "").split())
    if len(text) <= limit:
        return text
    return text[:limit - 15] + "...[truncated]"


def _save_step_outputs(step: Dict[str, Any],
                       result: Any,
                       state: Dict[str, Any],
                       all_handles: List[str]) -> Dict[str, Any]:
    handles = parse_tool_result_handles(result)
    all_handles.extend(handles)
    save_as = str(step.get("save_as") or "").strip()
    saved_value: Any = None
    if save_as and VARIABLE_RE.match(save_as):
        saved_value = handles[0] if len(handles) == 1 else handles
        state[_var_name(save_as)] = saved_value
    return {"handles": handles, "save_as": save_as or None, "saved_value": saved_value}


def _check_postcondition(postcondition: Dict[str, Any],
                         state: Dict[str, Any],
                         result_handles: List[str]) -> Dict[str, Any]:
    resolved, unresolved = _resolve_value(postcondition, state, allow_future=False)
    if unresolved:
        return {"ok": False, "postcondition": postcondition, "reason": f"unresolved variables: {unresolved}"}
    ptype = str(resolved.get("type") or "")
    target = resolved.get("target")
    if ptype == "exists":
        exists = bool(target) and (not isinstance(target, dict)) and (
            str(target) in result_handles or any(str(target) == str(value) for value in state.values())
        )
        return {"ok": exists, "postcondition": resolved, "reason": "target handle captured" if exists else "target handle not captured"}
    if ptype in {"entity_type", "radius", "diameter", "distance", "layer"}:
        return {
            "ok": None,
            "postcondition": resolved,
            "reason": "requires rescan/validation evidence after execution",
        }
    return {"ok": None, "postcondition": resolved, "reason": "postcondition type is recorded but not statically checkable"}


def _transaction_begin(enabled: bool) -> Dict[str, Any]:
    if not enabled:
        return {"enabled": False, "ok": True, "message": "transaction disabled"}
    try:
        from src.cad_tools import file_tools

        return file_tools.begin_undo_group("CADPlan")
    except Exception as exc:
        return {"enabled": True, "ok": False, "message": f"begin undo group failed: {exc}"}


def _transaction_end(enabled: bool) -> Dict[str, Any]:
    if not enabled:
        return {"enabled": False, "ok": True, "message": "transaction disabled"}
    try:
        from src.cad_tools import file_tools

        return file_tools.end_undo_group("CADPlan")
    except Exception as exc:
        return {"enabled": True, "ok": False, "message": f"end undo group failed: {exc}"}


def _transaction_rollback(enabled: bool) -> Dict[str, Any]:
    if not enabled:
        return {"enabled": False, "ok": False, "message": "transaction disabled"}
    try:
        from src.cad_tools import file_tools

        return file_tools.rollback_undo_group("CADPlan")
    except Exception as exc:
        return {"enabled": True, "ok": False, "message": f"rollback failed: {exc}"}


def execute_cad_plan(plan: Dict[str, Any],
                     allow_modify: bool = False,
                     transactional: bool = True,
                     rollback_on_error: bool = True,
                     rollback_on_high_severity_validation: bool = True,
                     validate_after_each_step: bool = False,
                     validate_after_plan: bool = True,
                     rescan_after_plan: bool = False,
                     export_view_after_plan: bool = False) -> ToolResult:
    del validate_after_each_step, rollback_on_high_severity_validation
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
    state = dict(normalized.get("variables") or {})
    results = []
    handles: List[str] = []
    postcondition_results: List[Dict[str, Any]] = []
    transaction_status = _transaction_begin(transactional)
    if transactional and not transaction_status.get("ok"):
        transaction_status["warning"] = "Execution will continue without a confirmed AutoCAD undo mark."
    failed_step = None
    failed_result = None
    rollback_status: Optional[Dict[str, Any]] = None
    try:
        for index, step in enumerate(normalized["steps"]):
            op = step.get("op")
            fn = dispatch.get(op)
            if fn is None:
                raise RuntimeError(f"Validated operation has no execution binding: {op}")
            args, unresolved = _resolve_value(step.get("args", {}) or {}, state, allow_future=False)
            if unresolved:
                raise RuntimeError(f"Unresolved variables at step {step.get('step_id')}: {unresolved}")
            result = fn(**args)
            failure_message = tool_result_failure_message(result)
            if failure_message:
                failed_result = result
                raise RuntimeError(
                    f"{op} returned failure: {_compact_failure_message(failure_message)}"
                )
            output = _save_step_outputs(step, result, state, handles)
            step_postconditions = [
                _check_postcondition(postcondition, state, output["handles"])
                for postcondition in (step.get("postconditions", []) or [])
            ]
            postcondition_results.extend(step_postconditions)
            if any(item["ok"] is False for item in step_postconditions):
                raise RuntimeError(f"Postcondition failed at step {step.get('step_id')}")
            results.append({
                "step_id": step.get("step_id") or f"step_{index + 1}",
                "op": op,
                "args": args,
                "result": result,
                "outputs": output,
                "postconditions": step_postconditions,
            })
    except Exception as exc:
        failed_step = step if "step" in locals() else None
        if rollback_on_error:
            rollback_status = _transaction_rollback(transactional)
        return error_result(
            f"CAD plan execution failed at step {(failed_step or {}).get('step_id')}: {exc}",
            data={
                "plan_id": normalized["plan_id"],
                "completed_steps": results,
                "failed_step": failed_step,
                "failed_result": failed_result,
                "rollback_status": rollback_status,
                "transaction_status": transaction_status,
                "postconditions": postcondition_results,
                "state": state,
            },
            warnings=["Execution may have modified the DWG before failure."],
            next_tools=["scan_all_entities", "validate_geometry"],
        )
    end_status = _transaction_end(transactional)
    after_artifacts: Dict[str, Any] = {}
    after_warnings: List[str] = []
    if rescan_after_plan:
        try:
            from src.cad_tools import query_tools

            after_artifacts["rescan"] = query_tools.scan_all_entities()
        except Exception as exc:
            after_warnings.append(f"Post-plan rescan failed: {exc}")
    if validate_after_plan:
        try:
            from .validators import validate_geometry

            after_artifacts["validation"] = validate_geometry()
        except Exception as exc:
            after_warnings.append(f"Post-plan validation failed: {exc}")
    if export_view_after_plan:
        try:
            from .view_grounding import export_view_image_with_mapping

            after_artifacts["view_snapshot"] = export_view_image_with_mapping()
        except Exception as exc:
            after_warnings.append(f"Post-plan view export failed: {exc}")
    return ok_result(
        f"Executed {len(results)} CAD plan steps.",
        data={
            "plan_id": normalized["plan_id"],
            "results": results,
            "state": state,
            "postconditions": postcondition_results,
            "transaction_status": transaction_status,
            "end_transaction_status": end_status,
            "after_artifacts": after_artifacts,
        },
        handles=handles,
        warnings=[
            "Execution modified the DWG through existing safe MCP tool implementations.",
            *after_warnings,
        ],
        next_tools=["scan_all_entities", "validate_geometry", "export_view_image_with_mapping"],
    )
