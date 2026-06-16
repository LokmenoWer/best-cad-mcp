# best-cad-mcp

面向真实 AutoCAD 工作流的 Windows MCP 服务。它不是只提供 `draw_line`、
`draw_circle` 这类简单绘图接口，而是把扫描、查询、编辑、标注、块、图层、
布局、导出、三维实体和模型私有上下文组织成一个可持续工作的 CAD Agent
工具层。

[English README](README.md)

## 项目优势

- 提供 260+ 个专用 AutoCAD MCP 工具，覆盖绘图、编辑、尺寸标注、块、填充、
  布局、打印导出、元数据、三维实体和辅助工作流。
- 支持“先扫描，再按 handle 查询和编辑”的可靠流程。Agent 不需要猜测图元，
  可以通过 AutoCAD 返回的真实 handle 操作对象。
- 使用 workspace 架构的 SQLite 数据库存储 CAD 元数据，兼容多图纸、多轮对话
  和多个线程。
- SQL 查询默认按当前 workspace/drawing/thread 作用域过滤：
  `execute_query("SELECT * FROM cad_entities")` 只返回当前上下文的数据，同时仍
  暴露 AutoCAD 原始 handle。
- 支持模型私有空间标注：Agent 可以在 SQLite 里记录零件、区域、点、边界框、
  语义区域或类似 pointer 的引用，不会向 DWG 写入辅助图层、XData 或可见文字。
- 生成派生拓扑表，包含点、线、曲线、面、实体和关系，便于 Agent 对几何结构
  做 SQL 分析，而不是反复解析原始 COM 字段。
- 提供 `export_view_image` 视觉核验工具，输出审阅图像但不修改 DWG。
- 内置工具选择指导，鼓励优先使用 rectangle、array、dimension、block、hatch、
  trim、fillet、solid 等高层 CAD 工具，而不是用低层 primitive 拼装复杂对象。

## Workspace 数据库架构

默认运行时数据库位置：

```text
<workspace>/.cad_mcp/workspace.db
```

数据库按四级作用域管理：

- `workspace`：项目工作区，可被多个 Agent 会话共享。
- `drawing`：每张 DWG 独立保存实体、图层、块、快照和查询数据；不同图纸中
  相同 AutoCAD handle 不会冲突。
- `conversation`：一次多轮对话的上下文。
- `thread`：并发线程的上下文。多个线程可以共享同一个 workspace 数据库，
  但模型私有标注和查询历史按 thread 隔离。

数据库内部使用带作用域的物理键；MCP 工具和只读 SQL 视图对外仍返回原始
AutoCAD handle/name，从而兼容旧工作流。

相关工具：

- `get_workspace_context`
- `set_workspace_context`
- `activate_workspace_drawing`
- `list_workspace_drawings`

## 环境要求

- Windows
- 推荐 AutoCAD 2020+
- Python 3.11+
- MCP 兼容客户端
- AutoCAD 需要可通过 Windows COM 自动化访问

安装依赖：

```powershell
pip install -r requirements.txt
```

## 快速开始

```powershell
git clone https://github.com/LokmenoWer/best-cad-mcp.git
cd best-cad-mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

直接运行：

```powershell
python src\server.py
```

或安装本地命令：

```powershell
pip install -e .
cad-mcp
```

## MCP 客户端配置

安装后推荐使用 console script：

```json
{
  "mcpServers": {
    "CAD": {
      "command": "cad-mcp"
    }
  }
}
```

也可以从源码运行：

```json
{
  "mcpServers": {
    "CAD": {
      "command": "python",
      "args": ["C:/path/to/best-cad-mcp/src/server.py"]
    }
  }
}
```

建议从目标 workspace 目录启动 MCP 客户端。也可以通过环境变量显式设置：
`CAD_MCP_WORKSPACE_ROOT`、`CAD_MCP_WORKSPACE_ID`、
`CAD_MCP_CONVERSATION_ID`、`CAD_MCP_THREAD_ID` 以及 drawing 相关变量。

## 推荐工作流

1. 用 `open_drawing` 或 `create_new_drawing` 打开/创建图纸。
2. 对现有图纸运行 `scan_all_entities`。默认扫描适合大图：
   `detail_level="minimal"`，同时保留 `topology_detail="summary"` 拓扑摘要。
3. 使用 `get_entity_statistics`、`execute_query`、`get_entity_topology` 或
   `get_topology_summary` 理解图纸内容。
   需要端点、边界、primitive/relation 等细粒度拓扑时，使用
   `scan_all_entities(topology_detail="full")`。
4. 捕获 handle 后用专用工具编辑，例如 `move_entity`、`array_rectangular`、
   `fillet_polyline`、`add_qdim`、`insert_block`、`add_hatch`、`solid_boolean`。
5. 需要模型记忆时，用 `add_spatial_annotation` 保存私有空间标注。
6. 需要视觉确认时，用 `export_view_image` 输出审阅图。
7. 最后保存或导出 DWG/PDF/DXF/DWF。

## 运行时文件

服务可能在 workspace 下生成：

- `.cad_mcp/workspace.db`
- `.cad_mcp/workspace.db-wal`
- `.cad_mcp/workspace.db-shm`
- `cad_mcp.log`
- `cad_visual_exports/`

这些都是运行时产物，不应提交到 Git。

## 开发和测试

运行测试：

```powershell
python -m pytest
```

对真实 AutoCAD 会话做 MCP 工具冒烟验证：

```powershell
python scripts\verify_autocad_mcp_tools.py
```

单元测试会 mock COM 依赖，不需要安装 AutoCAD；真实冒烟验证需要本机 AutoCAD
COM 会话。

## 致谢

模型私有标注和 pointer-style CAD 上下文设计在概念上参考了 Pointer-CAD 项目和
论文：https://github.com/Snitro/Pointer-CAD

本仓库没有复制 Pointer-CAD 源码。

## 许可证

MIT。参见 [LICENSE](LICENSE)。
