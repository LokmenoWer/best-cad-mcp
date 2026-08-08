"""Smoke-test the installed/source cad-mcp entrypoint over real MCP stdio."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client


MODERN_PROTOCOL = "2026-07-28"
LEGACY_PROTOCOL = "2025-11-25"


async def _verify_mode(
    params: StdioServerParameters,
    mode: str,
    image_path: Path,
) -> dict[str, Any]:
    async with Client(
        stdio_client(params),
        mode=mode,
        read_timeout_seconds=30,
    ) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        prompts = await client.list_prompts()
        resource = await client.read_resource("cad://tool-selection")
        prompt = await client.get_prompt("cad_workflow_guide")
        result = await client.call_tool(
            "recommend_cad_tools",
            {"intent": "draw a rectangle", "max_results": 3},
        )
        structured = await client.call_tool("get_vision_capabilities", {})
        image = await client.call_tool("view_image", {"path": str(image_path)})

        expected_protocol = MODERN_PROTOCOL if mode == MODERN_PROTOCOL else LEGACY_PROTOCOL
        if client.protocol_version != expected_protocol:
            raise RuntimeError(
                f"{mode} negotiated {client.protocol_version}, expected {expected_protocol}"
            )
        if not tools.tools or not resources.resources or not prompts.prompts:
            raise RuntimeError(
                f"{mode} returned an empty MCP surface: "
                f"tools={len(tools.tools)} resources={len(resources.resources)} "
                f"prompts={len(prompts.prompts)}"
            )
        if result.is_error:
            raise RuntimeError(f"{mode} tool call failed: {result}")
        if not resource.contents or resource.contents[0].mime_type != "text/markdown":
            raise RuntimeError(f"{mode} resource read returned unexpected content")
        if not prompt.messages:
            raise RuntimeError(f"{mode} prompt read returned no messages")
        if structured.is_error or not structured.structured_content:
            raise RuntimeError(f"{mode} structured tool call failed: {structured}")
        if structured.structured_content.get("result", {}).get("ok") is not True:
            raise RuntimeError(f"{mode} structured tool payload is invalid: {structured}")
        if [block.type for block in image.content] != ["text", "image"]:
            raise RuntimeError(f"{mode} image tool returned unexpected content: {image}")

        return {
            "mode": mode,
            "protocol_version": client.protocol_version,
            "tools": len(tools.tools),
            "resources": len(resources.resources),
            "prompts": len(prompts.prompts),
            "tool_call_ok": True,
            "resource_read_ok": True,
            "prompt_read_ok": True,
            "structured_content_ok": True,
            "image_content_ok": True,
        }


async def verify_stdio(
    *,
    command: str,
    args: list[str],
    cwd: Path,
    workspace_root: Path,
) -> dict[str, Any]:
    workspace_root.mkdir(parents=True, exist_ok=True)
    env = {
        "CAD_MCP_TOOL_PROFILE": "lean",
        "CAD_MCP_WORKSPACE_ROOT": str(workspace_root),
        "CAD_MCP_LOG_PATH": str(workspace_root / "cad_mcp.log"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    params = StdioServerParameters(
        command=command,
        args=args,
        cwd=cwd,
        env=env,
    )
    image_path = workspace_root / "mcp-v2-smoke.png"
    image_path.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8A"
        "AQUBAScY42YAAAAASUVORK5CYII="
    ))
    results = []
    try:
        for mode in (MODERN_PROTOCOL, "legacy"):
            results.append(await _verify_mode(params, mode, image_path))
        return {"ok": True, "stdio": results}
    finally:
        image_path.unlink(missing_ok=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify modern and legacy MCP protocols over real cad-mcp stdio.",
    )
    parser.add_argument(
        "--server-command",
        help="Installed cad-mcp executable. Defaults to python -m src.server.",
    )
    parser.add_argument(
        "--server-cwd",
        type=Path,
        help="Server working directory. Defaults to the repository root.",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        help="Isolated CAD metadata workspace. Defaults to a temporary directory.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    options = _build_parser().parse_args(argv)
    repository_root = Path(__file__).resolve().parents[1]
    command = options.server_command or sys.executable
    args = [] if options.server_command else ["-m", "src.server"]
    cwd = (options.server_cwd or repository_root).resolve()

    if options.workspace_root:
        result = asyncio.run(
            verify_stdio(
                command=command,
                args=args,
                cwd=cwd,
                workspace_root=options.workspace_root.resolve(),
            )
        )
    else:
        with tempfile.TemporaryDirectory(prefix="best-cad-mcp-v2-stdio-") as tmp:
            result = asyncio.run(
                verify_stdio(
                    command=command,
                    args=args,
                    cwd=cwd,
                    workspace_root=Path(tmp),
                )
            )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
