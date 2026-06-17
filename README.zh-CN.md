# best-cad-mcp

<!-- mcp-name: io.github.LokmenoWer/best-cad-mcp -->

`best-cad-mcp` 是一个面向真实 AutoCAD 工作流的 Windows MCP 服务。它让
agent 不只是调用几个绘图 primitive，而是可以扫描图纸、理解图纸、按 handle
精确编辑、验证几何、把视觉发现映射回 AutoCAD 实体，并导出交付文件。

[English README](README.md)

## 核心能力

- 260+ 个专用 AutoCAD 工具，覆盖绘图、编辑、图层、块、属性、填充、尺寸、
  表格、布局、打印、三维实体、元数据和工作流指导。
- handle 优先的可靠流程：先扫描图纸，再查询结构化元数据，最后编辑 AutoCAD
  返回的真实 handle。
- workspace 作用域 SQLite 元数据，支持多图纸、多轮对话和多线程 agent 会话。
- CAD Understanding Layer，提供 CAD-IR、语义对象、约束、验证报告和资源端点。
- VLM grounding：把导出的视图像素区域或 overlay ID 映射回可能的 AutoCAD
  handle。
- CADPlan 安全流程：校验、静态 dry-run、显式执行开关，适合多步骤绘图和修复。
- 模型私有空间标注存储在 SQLite 中，不写入隐藏 DWG 图层、辅助文字、XData
  或块。
- 内置工具选择建议，引导 agent 使用 rectangle、array、dimension、hatch、
  block 等高层 CAD 工具，而不是用低层 primitive 拼复杂对象。

## 环境要求

- Windows
- 推荐 AutoCAD 2020+
- Python 3.11+
- MCP 兼容客户端
- AutoCAD 可通过 Windows COM 自动化访问

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

从源码运行：

```powershell
python src\server.py
```

或安装本地命令：

```powershell
pip install -e .
cad-mcp
```

## Codex MCP 配置

Codex 从 `config.toml` 读取 MCP server 配置。本仓库已提供项目级
`.codex/config.toml`，在 Codex 信任该项目后会自动加载。它会从当前源码
checkout 启动 server，自动批准常规 CAD 工具，同时让原始命令和破坏性兜底工具
继续走人工审批。

如果要写入用户级 Codex 配置，执行 `pip install -e .` 后，把下面内容加入
`~/.codex/config.toml`：

```toml
[mcp_servers.best-cad-mcp]
enabled = true
command = "cad-mcp"
cwd = "C:/path/to/best-cad-mcp"
startup_timeout_sec = 30
tool_timeout_sec = 120
default_tools_approval_mode = "approve"
```

如果直接从源码 checkout 和虚拟环境启动：

```toml
[mcp_servers.best-cad-mcp]
enabled = true
command = "C:/path/to/best-cad-mcp/.venv/Scripts/python.exe"
args = ["-m", "src.server"]
cwd = "C:/path/to/best-cad-mcp"
startup_timeout_sec = 30
tool_timeout_sec = 120
default_tools_approval_mode = "approve"
```

即使常规工具自动批准，也建议让高风险工具保持人工审批：

```toml
[mcp_servers.best-cad-mcp.tools.send_command]
approval_mode = "prompt"

[mcp_servers.best-cad-mcp.tools.execute_cad_plan]
approval_mode = "prompt"

[mcp_servers.best-cad-mcp.tools.delete_entity]
approval_mode = "prompt"

[mcp_servers.best-cad-mcp.tools.delete_entities]
approval_mode = "prompt"

[mcp_servers.best-cad-mcp.tools.erase_selection_entities]
approval_mode = "prompt"

[mcp_servers.best-cad-mcp.tools.delete_layer]
approval_mode = "prompt"

[mcp_servers.best-cad-mcp.tools.purge_drawing]
approval_mode = "prompt"

[mcp_servers.best-cad-mcp.tools.audit_drawing]
approval_mode = "prompt"

[mcp_servers.best-cad-mcp.tools.save_drawing]
approval_mode = "prompt"

[mcp_servers.best-cad-mcp.tools.close_drawing]
approval_mode = "prompt"
```

## Claude Code MCP 配置

Claude Code 从 `.mcp.json` 读取共享的项目 MCP server，从 `.claude/settings.json`
读取共享的项目权限。本仓库已经提供这两个文件：

- `.mcp.json` 把本地 stdio server 注册为 `best-cad-mcp`。
- `.claude/settings.json` 启用该项目 MCP server，自动允许常规
  `mcp__best-cad-mcp__*` 工具，同时让高风险工具继续确认。

仓库内的 MCP server 配置使用 Claude 暴露的项目目录变量
`CLAUDE_PROJECT_DIR`：

```json
{
  "mcpServers": {
    "best-cad-mcp": {
      "command": "python",
      "args": ["${CLAUDE_PROJECT_DIR:-.}/src/server.py"],
      "env": {
        "CAD_MCP_WORKSPACE_ROOT": "${CLAUDE_PROJECT_DIR:-.}",
        "PYTHONPATH": "${CLAUDE_PROJECT_DIR:-.}"
      }
    }
  }
}
```

如果依赖只安装在项目虚拟环境里，把 `.mcp.json` 里的 `command` 改成：

```json
"C:/path/to/best-cad-mcp/.venv/Scripts/python.exe"
```

可以用 `claude mcp list` 或 Claude Code 内的 `/mcp` 确认 server 已连接。

建议从目标 workspace 目录启动 MCP 客户端，这样运行时元数据会落在对应工作区。
也可以在启动前设置 `CAD_MCP_WORKSPACE_ROOT`、`CAD_MCP_WORKSPACE_ID`、
`CAD_MCP_CONVERSATION_ID`、`CAD_MCP_THREAD_ID` 以及图纸相关环境变量。

## Workspace 数据库

默认运行时数据库位置：

```text
<workspace>/.cad_mcp/workspace.db
```

数据库按以下作用域隔离数据：

- `workspace`：共享项目目录。
- `drawing`：每张 DWG 独立保存实体、图层、块、拓扑、视图和查询数据；不同图纸
  中相同 handle 不会冲突。
- `conversation`：一次多轮客户端会话。
- `thread`：并行 agent 线程，隔离私有标注和查询历史。

内部 SQLite 物理键会带作用域；MCP 工具和只读 SQL 视图对外仍返回原生 AutoCAD
handle 和名称。

常用上下文工具：

- `get_workspace_context`
- `set_workspace_context`
- `activate_workspace_drawing`
- `list_workspace_drawings`

## Agent 推荐流程

处理现有 DWG：

1. 用户提供路径时调用 `open_drawing`。
2. `scan_all_entities(clear_db=True, detail_level="minimal", topology_detail="summary")`。
3. `build_drawing_ir`。
4. `summarize_drawing`。
5. 根据领域调用 `detect_semantic_objects(domain="mechanical")` 或其他 domain。
6. `extract_drawing_constraints`。
7. `validate_geometry`。
8. 需要视觉确认时调用 `export_view_image_with_mapping(include_overlay=True)`。
9. 精确编辑前先对目标 `explain_entity(handle)`。
10. 按 handle 直接编辑，或通过已校验和 dry-run 的 CADPlan 编辑。
11. 重新扫描、验证、视觉确认，然后保存或导出。

生成新图纸：

1. `create_new_drawing`。
2. 按需创建图层、文字样式、标注样式和布局上下文。
3. 用高层操作构造 CADPlan。
4. `validate_cad_plan`，再 `dry_run_cad_plan`。
5. 只有明确允许修改时调用 `execute_cad_plan(..., allow_modify=True)`。
6. `scan_all_entities`、`validate_geometry`，并导出审阅图。
7. 保存或导出最终 DWG/PDF/DXF/DWF。

## CAD 理解层

理解工具统一返回结构化 `ToolResult`：

```json
{
  "ok": true,
  "message": "",
  "data": {},
  "handles": [],
  "warnings": [],
  "next_tools": []
}
```

理解、查询、语义检测、约束提取、验证、视觉映射和 dry-run 工具不会修改 DWG。
语义对象、约束、验证报告、视图快照和 VLM 映射信息会写入 workspace SQLite
数据库。

关键工具：

- `build_drawing_ir`：生成 JSON CAD 中间表示，包含原生 handles、实体、图层、
  块、拓扑、语义对象、约束、验证和视图。
- `summarize_drawing`：总结图纸意图、实体构成、图层、块、警告和下一步工具。
- `find_entities_by_description`：按类型、图层、文字、块内容、标注、bbox 位置
  或简单几何词查找 handle。
- `explain_entity`：解释单个 handle，包括附近实体、拓扑、尺寸、标注和语义猜测。
- `detect_semantic_objects`：把规则检测到的语义对象写入 SQLite。
- `get_semantic_graph` / `find_semantic_objects`：查看语义对象 ID、handle、证据、
  confidence 和关系。
- `extract_drawing_constraints`、`check_drawing_constraints`、
  `get_drawing_constraints`：管理测量和推断约束。
- `validate_geometry` / `get_validation_report`：返回带 severity、handles、
  evidence、repair_hint 和 suggested_tools 的问题报告。
- `propose_repair_plan`：只提出修复计划，不执行修改。
- `list_cad_resources` / `get_cad_resource`：复用当前 CAD-IR、摘要、拓扑、
  语义图、约束、验证报告和工具指南。

## CADPlan

CADPlan 是多步骤绘图或修复的安全路径，适合一次修改多个实体、需要可审阅计划
或需要 dry-run 的任务。

必须遵循的顺序：

1. 构造 plan。
2. `validate_cad_plan(plan)`。
3. `dry_run_cad_plan(plan)`。
4. 需要时获得明确修改许可。
5. `execute_cad_plan(plan, allow_modify=True)`。
6. 重新扫描、验证，并进行视觉确认。

Plan 结构示例：

```json
{
  "plan_id": "mounting-plate",
  "description": "Draw a plate with four mounting holes",
  "units": "mm",
  "risk_level": "low",
  "requires_confirmation": true,
  "steps": [
    {
      "step_id": "layer",
      "op": "create_layer",
      "args": {"name": "M-PART", "color": 1},
      "writes": true
    },
    {
      "step_id": "outline",
      "op": "draw_rectangle",
      "args": {"corner1": [0, 0, 0], "corner2": [120, 80, 0], "layer": "M-PART"},
      "writes": true,
      "depends_on": ["layer"]
    }
  ],
  "constraints": [
    {"type": "distance", "expected": 120.0}
  ]
}
```

当前 CADPlan 可执行操作：

```text
draw_line, draw_circle, draw_rectangle, draw_polyline, draw_polygon,
draw_text, draw_mtext, move_entity, rotate_entity, copy_entity,
delete_entity, delete_entities, scale_entity, mirror_entity, offset_entity,
array_rectangular, array_polar, set_entity_properties, create_layer,
set_current_layer, add_linear_dimension, add_radial_dimension,
add_diametric_dimension, add_hatch, hatch_add_boundary, create_block,
insert_block
```

如果操作是合法 CAD 行为但尚未绑定到 CADPlan executor，例如 `draw_donut`、
`draw_box`、`solid_boolean`、`trim_entity`、`extend_entity`、`fillet_entities`、
`chamfer_entities`、`add_table`、`edit_table_cell`、`add_mleader`、布局工具或打印
工具，请直接调用对应 MCP 工具。

`send_command`、SQL mutation、purge 和 audit 默认不能通过 CADPlan 校验。

## 视觉 Grounding

`export_view_image_with_mapping(include_overlay=True)` 会生成：

- 干净的视图导出图；
- 可选的带数字 ID overlay 图；
- sidecar JSON，记录像素、视图参数、可见 handles 和实体屏幕 bbox。

当 VLM 返回像素 bbox 时，调用 `ground_vlm_region(snapshot_id, bbox)` 可以按
重叠度和距离排序候选 handle。编辑前应再对最佳候选调用 `explain_entity`。

首版映射最适合 top/plan view；twist、UCS 和 3D view 会返回 warnings，应按近似
结果处理。

## 工具选择建议

有专用高层工具时优先使用专用工具：

- 矩形、多边形、圆环、样条、多线、阵列、块、填充、尺寸、引线、表格、圆角、
  倒角、修剪、偏移和三维实体都应使用对应工具。
- `draw_line`、`draw_circle`、`draw_polyline` 只用于简单几何或没有更合适工具的
  场景。
- `send_command` 只作为最后手段，并且需要用户明确接受风险。
- 使用 `create_text_style` 创建文字样式。它会通过 AutoCAD `SetFont` 支持
  TrueType 字体名，并通过 `FontFile` 支持 SHX/TTF/OTF/TTC 字体文件。

## 装配图工作流

面向 agent 的装配图流程位于 `.agents/skills/draw-assembly-diagrams`。该 skill
覆盖：

- 装配图内容要求；
- BOM 和件号规则；
- CADPlan 生成与修复；
- VLM grounding；
- handle 精确编辑；
- 最终验证和导出清单。

机械装配图中建议：

- 板件和矩形零件用 `draw_rectangle`；
- 规则形体用 `draw_polygon`；
- 垫圈、密封圈、环形件用 `draw_donut`；
- 重复零件使用块和阵列；
- 尺寸必须用真实 dimension 实体；
- BOM 使用 `add_table` 和 `edit_table_cell`；
- 指引线/气泡优先用 `add_mleader` 或一致的气泡块。

## 运行时文件

服务可能生成：

- `.cad_mcp/workspace.db`
- `.cad_mcp/workspace.db-wal`
- `.cad_mcp/workspace.db-shm`
- `cad_mcp.log`
- `cad_visual_exports/`

这些是运行时产物，不应提交到 Git。

## 开发和测试

运行单元测试：

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

## 生产级 CAD Agent 工作流

既有 DWG 审阅建议流程：

1. 需要打开文件时先调用 `open_drawing`。
2. 调用 `scan_all_entities(topology_detail="full")`。
3. 调用 `build_drawing_ir`。
4. 调用 `summarize_drawing(level="deep")`。
5. 按领域调用 `detect_semantic_objects(domain=...)`。
6. 调用 `extract_drawing_constraints` 和 `bind_all_dimensions`。
7. 调用 `check_drawing_constraints` 与 `validate_geometry`。
8. 调用 `export_view_image_with_mapping(include_overlay=True)`，把 clean image、overlay image、sidecar JSON 交给 VLM 审阅。
9. 使用 `ground_vlm_region` 或 `ground_vlm_overlay_id` 把 VLM 发现定位回 handle/primitive。
10. 修改前调用 `explain_entity`，再使用 `propose_repair_plan` 或 `propose_constraint_repair_plan`。
11. 任何修改都必须先 `validate_cad_plan`、再 `dry_run_cad_plan`，最后仅在明确授权后调用 `execute_cad_plan(allow_modify=True, transactional=True)`。
12. 执行后重新扫描、重新验证并导出最终复核产物。

新图绘制建议流程：

1. `create_new_drawing`。
2. 生成带 `variables`、`save_as`、依赖、`expect` 和 `postconditions` 的 CADPlan。
3. `validate_cad_plan`。
4. `dry_run_cad_plan`。
5. `execute_cad_plan(allow_modify=True, transactional=True)`。
6. `scan_all_entities`、`build_drawing_ir`、`validate_geometry`、`export_view_image_with_mapping`。

### 能力与限制

- 顶视/平面模型空间的 world/pixel 映射支持 view twist；当视图上下文提供 UCS 轴时会纳入计算。
- 非平面 3D 视图和复杂布局视口会返回 `limitations`、`warnings` 和较低 confidence，不应宣称精确 grounding。
- VLM overlay 是外部产物，不会向 DWG 写入辅助图层、XData、标签或几何。
- 尺寸绑定会把径向、直径、线性尺寸关联到候选圆/弧/线，并给出证据和 confidence；歧义尺寸保持 `unknown`。
- 语义图是确定性规则和证据优先设计，低置信度复杂对象会标记为 candidate。
- CADPlan 支持变量、`save_as`、输出 handle 捕获、postconditions、事务 undo group 和失败 rollback 尝试。
- 修复工具只生成计划，永不自动执行；危险编辑必须显式 `allow_modify=True`。

真实 AutoCAD COM 工作流冒烟测试：

```powershell
python scripts\verify_cad_understanding_workflow.py
```
