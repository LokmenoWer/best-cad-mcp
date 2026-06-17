# best-cad-mcp

<!-- mcp-name: io.github.LokmenoWer/best-cad-mcp -->

`best-cad-mcp` 是一个面向真实 DWG 项目的 Windows AutoCAD MCP 服务。它通过
Model Context Protocol 暴露 AutoCAD 绘图、编辑、检查、元数据、验证、导出、视觉定位和规划工具，让 agent 可以按真实 AutoCAD handle 安全地理解和修改图纸。

[English README](README.md)

## 项目能做什么

很多 CAD 自动化示例只停留在绘制基础图元。`best-cad-mcp` 面向更接近生产环境的 agent 工作流：

- 修改图纸前先检查现有 DWG；
- 将实体、图层、块、拓扑、尺寸和布局扫描到本地 SQLite 工作区数据库；
- 解释并编辑 AutoCAD 返回的精确 handle；
- 构建 CAD-IR、语义图、约束、验证报告和可复用 MCP 资源；
- 导出干净视图和带标号 overlay 的审阅图；
- 将 VLM 的像素框或 overlay ID 定位回候选 AutoCAD handle；
- 对多步骤 CADPlan 进行校验、静态 dry-run，并在明确授权后执行。

服务在本机运行，通过 Windows COM 连接 AutoCAD。agent 的上下文和私有标注写入工作区数据库，不会为了记忆而往 DWG 里塞隐藏图层、辅助文字或临时几何。

## 核心特性

- **290+ 个 MCP 工具**：覆盖绘图、编辑、图层、块、属性、填充、尺寸、表格、布局、打印、三维实体、查询、元数据和工作流指导。
- **handle 优先的编辑方式**：先扫描，再查询结构化元数据，最后编辑 AutoCAD 返回的准确 handle。
- **CAD 理解层**：提供 CAD-IR、图纸摘要、语义对象、语义图、尺寸绑定、约束、验证报告和 MCP resources。
- **视觉定位**：将导出视图中的像素坐标、世界坐标或 overlay ID 映射回可能的 AutoCAD handle。
- **受保护的 CADPlan 流程**：支持校验、静态 dry-run、变量、`save_as` handle 捕获、依赖、后置条件、事务式执行和回滚尝试。
- **工作区级 SQLite 记忆**：支持多图纸、多轮对话和多线程 agent 会话。
- **模型私有空间标注**：标注存储在 SQLite 中，不写入隐藏 DWG 图层、XData、块、标签或可见标记。
- **提示词和技能资产**：包含图纸理解、精确绘图、VLM 审阅、修复规划，以及模块化装配图规范。

## 环境要求

- Windows
- 推荐 AutoCAD 2020 或更新版本
- Python 3.11 或更新版本
- 支持 MCP 的客户端，例如 Codex 或 Claude Code
- 本机 AutoCAD 可以通过 Windows COM 自动化访问

AutoCAD 必须安装、授权，并能在运行 MCP 服务的同一 Windows 用户下正常启动。

## 安装

克隆仓库并安装依赖：

```powershell
git clone https://github.com/LokmenoWer/best-cad-mcp.git
cd best-cad-mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -e .
```

从源码运行：

```powershell
python -m src.server
```

或运行安装后的命令：

```powershell
cad-mcp
```

这个服务是一个 MCP stdio 进程。正常使用时，MCP 客户端会根据配置自动启动它。

## MCP 客户端配置

建议从目标工作区启动 MCP 客户端。运行时数据默认写入该工作区；也可以通过
`CAD_MCP_WORKSPACE_ROOT` 指定。

### Codex

仓库内置 `.codex/config.toml`，用于项目级 Codex 配置。信任项目后，Codex 可以直接从当前 checkout 启动服务。

如果执行过 `pip install -e .`，也可以把下面的配置加入用户级 `~/.codex/config.toml`：

```toml
[mcp_servers.best-cad-mcp]
enabled = true
command = "cad-mcp"
cwd = "C:/path/to/best-cad-mcp"
startup_timeout_sec = 30
tool_timeout_sec = 120
default_tools_approval_mode = "approve"
```

如果要从虚拟环境里的源码 checkout 启动：

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

建议让原生命令和破坏性工具保持人工确认：

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

### Claude Code

仓库内置：

- `.mcp.json`：把本地 stdio 服务注册为 `best-cad-mcp`。
- `.claude/settings.json`：启用该服务，并对原生命令或破坏性工具保持确认。

内置 `.mcp.json` 使用 `CLAUDE_PROJECT_DIR`：

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

如果依赖只安装在项目虚拟环境里，把 `command` 改成：

```json
"C:/path/to/best-cad-mcp/.venv/Scripts/python.exe"
```

可以用 `claude mcp list` 或 Claude Code 内的 `/mcp` 确认服务是否已连接。

## 首次使用流程

### 检查并修复已有 DWG

1. 用户提供 DWG 路径时调用 `open_drawing`。
2. 调用 `scan_all_entities(clear_db=True, detail_level="minimal", topology_detail="summary")`。
3. 调用 `build_drawing_ir`。
4. 调用 `summarize_drawing`。
5. 根据领域调用 `detect_semantic_objects(domain="mechanical")` 或其他合适 domain。
6. 调用 `bind_all_dimensions`、`extract_drawing_constraints` 和 `check_drawing_constraints`。
7. 调用 `validate_geometry`。
8. 需要视觉证据时调用 `export_view_image_with_mapping(include_overlay=True)`。
9. 对 VLM 发现调用 `ground_vlm_region` 或 `ground_vlm_overlay_id`。
10. 修改前对目标调用 `explain_entity(handle)`。
11. 通过 handle 精确编辑，或通过已校验、已 dry-run 的 CADPlan 编辑。
12. 重新扫描、验证、视觉确认，然后保存或导出。

### 创建新图纸

1. 调用 `create_new_drawing`。
2. 设置图层、文字样式、尺寸样式、布局和单位。
3. 使用高层 CAD 操作构建 CADPlan，并加入依赖、`save_as` 变量和后置条件。
4. 调用 `validate_cad_plan`，再调用 `dry_run_cad_plan`。
5. 只有在明确允许修改后，调用 `execute_cad_plan(..., allow_modify=True)`。
6. 调用 `scan_all_entities`、`build_drawing_ir`、`validate_geometry`，并导出审阅图。
7. 保存或导出最终 DWG/PDF/DXF/DWF 交付文件。

## 核心概念

### 工作区数据库

运行时元数据默认存储在：

```text
<workspace>/.cad_mcp/workspace.db
```

数据库按 workspace、drawing、conversation 和 thread 隔离数据，避免不同图纸中的相同 handle 冲突，也让并行 agent 会话拥有独立的私有标注和查询历史。

常用工作区工具：

- `get_workspace_context`
- `set_workspace_context`
- `activate_workspace_drawing`
- `list_workspace_drawings`

### CAD 理解层

理解类工具返回统一的 `ToolResult` 结构：

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

只读理解工具不会修改 DWG。语义对象、约束、验证报告、视图快照和 VLM 映射信息会存入工作区数据库。

关键工具包括：

- `build_drawing_ir` 和 `export_drawing_ir`
- `summarize_drawing`
- `find_entities_by_description`
- `explain_entity`
- `detect_semantic_objects`、`get_semantic_graph` 和 `find_semantic_objects`
- `bind_dimension_to_geometry` 和 `bind_all_dimensions`
- `extract_drawing_constraints`、`check_drawing_constraints` 和 `get_drawing_constraints`
- `validate_geometry` 和 `get_validation_report`
- `propose_repair_plan` 和 `propose_constraint_repair_plan`
- `list_cad_resources` 和 `get_cad_resource`

### CADPlan

CADPlan 是多步骤绘图或修复的受保护路径，适合一次修改多个实体、需要可审阅意图或需要先 dry-run 的任务。

```json
{
  "plan_id": "mounting-plate",
  "description": "Draw a plate with four mounting holes",
  "units": "mm",
  "risk_level": "low",
  "requires_confirmation": true,
  "variables": {"origin": [0, 0, 0]},
  "steps": [
    {
      "step_id": "plate",
      "op": "draw_rectangle",
      "args": {"corner1": "$origin", "corner2": [120, 80, 0], "layer": "M-PART"},
      "writes": true,
      "save_as": "$plate",
      "postconditions": [{"type": "exists", "target": "$plate"}]
    }
  ],
  "constraints": [
    {"type": "distance", "expected": 120.0}
  ]
}
```

CADPlan 当前可执行常见绘图、编辑、图层、尺寸、填充和块操作。尚未绑定到 CADPlan 的合法 CAD 操作仍可直接调用对应 MCP 工具。

`send_command`、SQL mutation、purge 和 audit 默认不能通过 CADPlan 校验。

### 视觉定位

`export_view_image_with_mapping(include_overlay=True)` 会生成：

- 干净视图导出图；
- 带数字 ID 的可选 overlay 图；
- 记录视图参数、可见 handle、像素框和映射数据的 sidecar JSON。

VLM 返回像素框时使用 `ground_vlm_region(snapshot_id, bbox)`；返回 overlay ID 时使用
`ground_vlm_overlay_id(snapshot_id, overlay_id)`。编辑前应对最可能的候选实体调用
`explain_entity`。

顶视/平面模型空间视图最可靠。带 twist、UCS、三维视图或复杂布局视口的场景会返回警告或较低置信度，不应超出工具返回的信息声称精确定位。

### 提示词与装配图技能

`prompts/` 目录包含 MCP prompt 源文件：

- 理解已有图纸；
- 按规格精确绘图；
- VLM 图纸审阅；
- 修复规划。

`.agents/skills/draw-assembly-diagrams` 提供面向 agent 的装配图工作流。装配图规则已经模块化：

- `references/assembly/index.md` 负责选择适用规范模块。
- `references/assembly/standards/generic-mechanical.md` 是默认机械装配图模块。
- 后续可以添加 ASME、ISO、GB 或公司规范模块，而不需要重写主技能。

## 安全模型

- 先扫描和理解，再修改。
- 优先使用高层 CAD 工具，而不是拼低层 primitive。
- 使用 AutoCAD 返回的 handle，不只凭文字猜测编辑目标。
- 破坏性工具和原始 `send_command` 保持人工确认。
- 多步骤编辑先走 CADPlan 校验和 dry-run。
- agent 记忆写入 SQLite，不写入隐藏 DWG 实体。
- 修改后重新扫描并验证。

## 运行时文件

服务可能在当前工作区生成：

- `.cad_mcp/workspace.db`
- `.cad_mcp/workspace.db-wal`
- `.cad_mcp/workspace.db-shm`
- `cad_mcp.log`
- `cad_visual_exports/`

这些是运行时产物，不应提交到仓库。

## 仓库结构

```text
src/
  server.py                 MCP tool、prompt 和 resource 定义
  cad_controller.py         AutoCAD COM 桥接
  cad_database.py           SQLite 持久化
  cad_tools/                按 CAD 领域组织的工具实现
  cad_understanding/        CAD-IR、语义、约束、验证、视觉定位
prompts/                    MCP prompt 函数加载的提示词源文件
scripts/                    验证和冒烟测试脚本
tests/                      mock COM 依赖的单元测试
.agents/skills/             agent 技能说明和装配图规范
.codex/                     项目级 Codex MCP 配置
.claude/ 和 .mcp.json       Claude Code MCP 配置
```

## 开发

安装开发依赖：

```powershell
python -m pip install -e .[dev]
```

运行单元测试：

```powershell
python -m pytest
```

运行 AutoCAD MCP 工具冒烟验证：

```powershell
python scripts\verify_autocad_mcp_tools.py
```

运行 CAD 理解工作流冒烟基准：

```powershell
python scripts\verify_cad_understanding_workflow.py
```

单元测试会 mock COM 相关行为，不需要安装 AutoCAD。真实冒烟验证需要本机 AutoCAD COM 会话。

## 常见问题

- **服务启动了但工具调用失败**：确认 AutoCAD 已安装、已授权，并能在同一 Windows 用户下正常打开。
- **MCP 客户端无法导入 `src`**：把服务 `cwd` 设置为仓库根目录，或将 `PYTHONPATH` 指向仓库根目录。
- **工作区数据写到了错误目录**：从目标工作区启动 MCP 客户端，或设置 `CAD_MCP_WORKSPACE_ROOT`。
- **需要的绘图行为没有工具**：应把能力补成 best-cad-mcp 工具，而不是在 agent 侧写临时 COM 脚本。
- **视觉定位不确定**：查看 warnings，优先使用 overlay ID，并用 `explain_entity` 确认候选 handle。

## 贡献

欢迎贡献。一个好的改动通常包含：

- `src/cad_tools/` 或 `src/cad_understanding/` 中聚焦的工具实现；
- 需要暴露给 MCP 时，在 `src/server.py` 增加 wrapper；
- 不依赖 AutoCAD 的单元测试，方便普通 CI 运行；
- 工作流变化对应的文档或 prompt 更新。

请不要提交运行时产物，例如 `.cad_mcp/`、日志、导出的审阅图、本地数据库、虚拟环境或 AutoCAD 冒烟测试输出。

## 致谢

模型私有标注和 pointer-style CAD 上下文设计在概念上参考了公开的 Pointer-CAD 项目和论文：
https://github.com/Snitro/Pointer-CAD

本仓库没有复制 Pointer-CAD 源码。

## 许可证

MIT。见 [LICENSE](LICENSE)。
