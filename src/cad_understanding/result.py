"""Standard structured result helpers for CAD understanding tools."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict


class ToolResult(TypedDict):
    ok: bool
    message: str
    data: Dict[str, Any]
    handles: List[str]
    warnings: List[str]
    next_tools: List[str]


def _json_safe(value: Any) -> Any:
    """Return a JSON-serializable copy of value."""
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _string_list(value: Optional[List[Any]]) -> List[str]:
    return [str(item) for item in (value or []) if item is not None]


def ok_result(message: str = "",
              data: Optional[Dict[str, Any]] = None,
              handles: Optional[List[str]] = None,
              warnings: Optional[List[str]] = None,
              next_tools: Optional[List[str]] = None) -> ToolResult:
    result: ToolResult = {
        "ok": True,
        "message": str(message or ""),
        "data": _json_safe(data or {}),
        "handles": _string_list(handles),
        "warnings": _string_list(warnings),
        "next_tools": _string_list(next_tools),
    }
    json.dumps(result, ensure_ascii=False)
    return result


def error_result(message: str,
                 data: Optional[Dict[str, Any]] = None,
                 warnings: Optional[List[str]] = None,
                 next_tools: Optional[List[str]] = None) -> ToolResult:
    result: ToolResult = {
        "ok": False,
        "message": str(message or "CAD understanding tool failed"),
        "data": _json_safe(data or {}),
        "handles": [],
        "warnings": _string_list(warnings),
        "next_tools": _string_list(next_tools),
    }
    json.dumps(result, ensure_ascii=False)
    return result


__all__ = ["ToolResult", "ok_result", "error_result"]
