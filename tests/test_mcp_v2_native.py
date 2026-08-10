"""Native MCP Python SDK v2 integration and AutoCAD thread-safety tests."""

import asyncio
import base64
import inspect
import sys
import threading
import time
import tomllib
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError

from src import server


ROOT = Path(__file__).resolve().parents[1]


def test_project_requires_mcp_v2_and_exposes_native_server():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]

    mcp_requirement = next(
        dependency
        for dependency in project["dependencies"]
        if dependency.startswith("mcp[")
    )

    assert mcp_requirement == "mcp[cli]>=2.0.0,<3.0.0"
    assert version("mcp").split(".", 1)[0] == "2"
    assert isinstance(server.mcp, MCPServer)
    assert server.mcp.version == project["version"]
    assert server.mcp.title == "best-cad-mcp"


def test_all_registered_sync_tools_use_async_event_loop_facades():
    registered = server.mcp._tool_manager.list_tools()

    assert registered
    assert all(inspect.iscoroutinefunction(tool.fn) for tool in registered)
    # Direct Python calls remain synchronous for the existing internal/test API.
    assert not inspect.iscoroutinefunction(server.recommend_cad_tools)


def test_protocol_models_use_v2_python_names_and_wire_aliases():
    tools = asyncio.run(server.mcp.list_tools())
    tool = next(tool for tool in tools if tool.name == "recommend_cad_tools")
    wire = tool.model_dump(by_alias=True, mode="json")

    assert tool.input_schema["type"] == "object"
    assert "inputSchema" in wire
    assert "input_schema" not in wire


def test_tool_error_wrapper_preserves_native_mcp_errors():
    def sync_tool() -> str:
        raise MCPError(-32603, "sync MCP failure")

    async def async_tool() -> str:
        raise MCPError(-32603, "async MCP failure")

    with pytest.raises(MCPError, match="sync MCP failure"):
        server._wrap_tool_errors(sync_tool)()
    with pytest.raises(MCPError, match="async MCP failure"):
        asyncio.run(server._wrap_tool_errors(async_tool)())


def test_native_v2_client_discovers_and_calls_tool_on_event_loop_thread():
    caller_thread = threading.get_ident()
    tool_threads = []

    def fake_recommend(intent: str, max_results: int = 8) -> str:
        tool_threads.append(threading.get_ident())
        return f"native-v2:{intent}:{max_results}"

    async def exercise_native_client():
        async with Client(
            server.mcp,
            raise_exceptions=True,
            mode="2026-07-28",
        ) as client:
            tools = await client.list_tools()
            result = await client.call_tool(
                "recommend_cad_tools",
                {"intent": "draw a rectangle", "max_results": 3},
            )
            return client.protocol_version, tools, result

    with patch.object(
        server.utility_tools,
        "recommend_cad_tools",
        side_effect=fake_recommend,
    ):
        protocol_version, tools, result = asyncio.run(
            exercise_native_client()
        )

    assert protocol_version == "2026-07-28"
    response_server_info = tools.meta["io.modelcontextprotocol/serverInfo"]
    assert response_server_info["name"] == "AutoCAD-Comprehensive-Server"
    assert response_server_info["version"] == server.mcp.version
    assert "recommend_cad_tools" in {tool.name for tool in tools.tools}
    assert result.is_error is False
    assert result.structured_content == {
        "result": "native-v2:draw a rectangle:3"
    }
    assert tool_threads == [caller_thread]


def test_native_tool_facades_serialize_concurrent_calls():
    state = {"active": 0, "max_active": 0}
    threads = []

    def fake_recommend(intent: str, max_results: int = 8) -> str:
        threads.append(threading.get_ident())
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.02)
        state["active"] -= 1
        return f"serialized:{intent}:{max_results}"

    async def exercise_concurrent_calls():
        async with Client(
            server.mcp,
            raise_exceptions=True,
            mode="2026-07-28",
        ) as client:
            return await asyncio.gather(
                client.call_tool("recommend_cad_tools", {"intent": "first"}),
                client.call_tool("recommend_cad_tools", {"intent": "second"}),
            )

    with patch.object(
        server.utility_tools,
        "recommend_cad_tools",
        side_effect=fake_recommend,
    ):
        results = asyncio.run(exercise_concurrent_calls())

    assert all(result.is_error is False for result in results)
    assert state["max_active"] == 1
    assert len(set(threads)) == 1


def test_legacy_client_initializes_and_calls_tool():
    async def exercise_legacy_client():
        async with Client(server.mcp, mode="legacy") as client:
            tools = await client.list_tools()
            result = await client.call_tool(
                "recommend_cad_tools",
                {"intent": "draw a circle", "max_results": 2},
            )
            return client.protocol_version, tools, result

    protocol_version, tools, result = asyncio.run(exercise_legacy_client())

    assert protocol_version == "2025-11-25"
    assert "recommend_cad_tools" in {tool.name for tool in tools.tools}
    assert result.is_error is False


def test_auto_client_negotiates_a_supported_protocol():
    async def exercise_auto_client():
        async with Client(server.mcp, mode="auto") as client:
            tools = await client.list_tools()
            return client.protocol_version, tools

    protocol_version, tools = asyncio.run(exercise_auto_client())

    assert protocol_version
    assert tools.tools


def test_modern_protocol_runs_over_real_stdio(tmp_path):
    image_path = tmp_path / "mcp-v2-smoke.png"
    image_path.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8A"
        "AQUBAScY42YAAAAASUVORK5CYII="
    ))
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.server"],
        cwd=ROOT,
        env={
            "CAD_MCP_TOOL_PROFILE": "lean",
            "CAD_MCP_WORKSPACE_ROOT": str(tmp_path),
            "CAD_MCP_LOG_PATH": str(tmp_path / "nested" / "cad_mcp.log"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )

    async def exercise_stdio():
        async with Client(
            stdio_client(params),
            mode="2026-07-28",
            read_timeout_seconds=30,
        ) as client:
            tools = await client.list_tools()
            resources = await client.list_resources()
            prompts = await client.list_prompts()
            resource = await client.read_resource("cad://tool-selection")
            prompt = await client.get_prompt("cad_workflow_guide")
            precise_prompt = await client.get_prompt("precise_draw_from_spec")
            result = await client.call_tool(
                "recommend_cad_tools",
                {"intent": "draw a rectangle", "max_results": 3},
            )
            structured = await client.call_tool("get_vision_capabilities", {})
            image = await client.call_tool("view_image", {"path": str(image_path)})
            return (
                client.protocol_version,
                client.instructions,
                tools,
                resources,
                prompts,
                resource,
                prompt,
                precise_prompt,
                result,
                structured,
                image,
            )

    (
        protocol,
        instructions,
        tools,
        resources,
        prompts,
        resource,
        prompt,
        precise_prompt,
        result,
        structured,
        image,
    ) = asyncio.run(exercise_stdio())

    assert protocol == "2026-07-28"
    assert len(tools.tools) == 114
    assert resources.resources
    assert prompts.prompts
    assert resource.contents[0].mime_type == "text/markdown"
    if instructions is not None:
        assert instructions.strip() == server.TOOL_SELECTION_INSTRUCTIONS.strip()
    assert resource.contents[0].text == server.TOOL_SELECTION_INSTRUCTIONS.strip()
    assert "Closed-loop operating contract" in resource.contents[0].text
    assert prompt.messages
    assert prompt.messages[0].content.text == server.cad_workflow_guide()
    assert precise_prompt.messages[0].content.text == (
        ROOT / "prompts" / "precise_draw_from_spec.md"
    ).read_text(encoding="utf-8").strip()
    assert result.is_error is False
    assert structured.is_error is False
    assert structured.structured_content["result"]["ok"] is True
    assert [block.type for block in image.content] == ["text", "image"]
    assert image.content[1].mime_type == "image/png"
