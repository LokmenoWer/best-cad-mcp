"""Command-line runtime preflight for best-cad-mcp."""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

from src.cad_tools import utility_tools


def _format_check(check: Dict[str, Any]) -> str:
    status = "OK" if check.get("ok") else ("BLOCKER" if check.get("required") else "WARN")
    line = f"[{status}] {check.get('name')}: {check.get('detail')}"
    remediation = str(check.get("remediation") or "")
    if remediation and not check.get("ok"):
        line += f"\n    fix: {remediation}"
    return line


def format_human(result: Dict[str, Any]) -> str:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    checks: List[Dict[str, Any]] = list(data.get("checks") or [])
    lines = [
        f"best-cad-mcp doctor: {'PASS' if result.get('ok') else 'FAIL'}",
        str(result.get("message") or ""),
        "",
    ]
    lines.extend(_format_check(check) for check in checks)
    warnings = result.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines).rstrip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cad-mcp-doctor",
        description="Check the local runtime required by best-cad-mcp.",
    )
    parser.add_argument(
        "--check-autocad",
        action="store_true",
        help="try to connect to a live AutoCAD COM instance",
    )
    parser.add_argument(
        "--require-visual-export",
        action="store_true",
        help="fail when no supported visual review renderer is available",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the structured ToolResult JSON",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = utility_tools.check_runtime_environment(
        check_autocad=args.check_autocad,
        require_visual_export=args.require_visual_export,
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print(format_human(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
