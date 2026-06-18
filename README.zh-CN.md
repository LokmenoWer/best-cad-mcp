# best-cad-mcp

<!-- mcp-name: io.github.LokmenoWer/best-cad-mcp -->

`best-cad-mcp` 是一个面向真实 DWG 图纸的 Windows AutoCAD MCP 服务。它在本机运行，通过 Windows COM 连接 AutoCAD，并通过 Model Context Protocol 为 agent 提供检查、理解、修改、验证和导出 CAD 图纸的能力。

[English README](README.md)

## 项目状态

本项目处于 beta 阶段。核心架构、命令行入口、工作区数据库和主要 CAD 工作流已经就位，但工具表面仍在演进。请把它当成面向受控本地工作流的 CAD 自动化基础设施，而不是无需审阅的全自动制图机器人。

## 为什么需要它

很多 CAD 自动化示例只会画线、画圆或画矩形。真实的 agent 工作流需要更多东西：

- 修改已有 DWG 前先检查图纸；
- 用 AutoCAD 返回的精确 handle 定位实体，而不是靠文字或截图猜；
- 在多轮对话中保留图纸理解、验证报告和审阅上下文；
- 多步骤修改先 dry-run，再实际写入图纸；
- 导出视觉证据，并把 VLM 发现映射回候选实体；
- 把 agent 的私有标注保存在数据库里，而不是塞进 DWG。

`best-cad-mcp` 就是围绕这些要求设计的。它把较完整的 AutoCAD 工具表面、本地 SQLite 工作区数据库、CAD 理解产物、视觉定位、提示词资产和受保护的 CADPlan 执行路径组合在一起。

## 能力概览

| 领域 | 能力 |
| --- | --- |
| AutoCAD 操作 | 绘图、编辑、图层、块、属性、尺寸、表格、填充、布局、打印、视图控制、三维实体、文件导出、查询、选择和实用工具。 |
| handle 优先检查 | 将图纸扫描到 SQLite，查询结构化元数据，解释实体，并按 AutoCAD handle 精确编辑。 |
| CAD 理解 | CAD-IR、图纸摘要、语义对象、语义图、尺寸绑定、约束提取、验证报告和 MCP resources。 |
| 视觉审阅 | 导出干净视图、可选数字 overlay，以及用于像素/世界坐标/实体映射的 sidecar JSON。 |
| CADPlan | 对多步骤绘图或修复计划进行校验、dry-run 和显式执行，支持变量、依赖、handle 捕获、后置条件、事务式执行和回滚尝试。 |
| agent 记忆 | 将工作区上下文和模型私有空间标注存入 SQLite，不在 DWG 中隐藏辅助几何、XData、标签或标记。 |
| 提示词与技能资产 | 提供图纸理解、精确绘图、VLM 审阅和修复提示词；提供面向装配图的规范化技能参考。 |

服务当前注册了数百个 MCP 工具入口。推荐工作方式不是随机调用基础图元直到图纸看起来差不多，而是扫描、理解、规划、按 handle 修改、验证，再进行视觉确认。

## 边界

`best-cad-mcp` 不包含 AutoCAD，不替代 AutoCAD 授权，也不是云端 CAD 渲染器。它假设运行 MCP 服务的同一 Windows 用户已经安装并可以正常自动化 AutoCAD。

项目也不承诺仅凭截图就能完美解释几何。视觉定位工具会返回候选项、置信度和警告；重要修改前，agent 应使用 `explain_entity` 和结构化元数据确认目标。

## 环境要求

- Windows
- 推荐 AutoCAD 2020 或更新版本
- Python 3.11 或更新版本
- 支持 MCP 的客户端，例如 Codex 或 Claude Code
- 本机 AutoCAD 可以通过 Windows COM 自动化访问

可选视觉审阅能力可以使用 ImageMagick、Inkscape、librsvg、Chrome、Edge 等系统渲染器，也可以安装 `visual` extra 中的 Python 依赖。

## 安装

### 从源码安装

```powershell
git clone https://github.com/LokmenoWer/best-cad-mcp.git
cd best-cad-mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

安装可选视觉审阅依赖：

```powershell
python -m pip install -e ".[visual]"
```

安装开发依赖：

```powershell
python -m pip install -e ".[dev]"
```

### 已发布包

使用已发布版本时，可以直接安装包：

```powershell
python -m pip install best-cad-mcp
```

安装后的命令行入口：

```powershell
cad-mcp
cad-mcp-doctor
```

服务本身是一个 MCP stdio 进程。正常使用时，MCP 客户端会根据配置启动它，而不是由用户手动在终端里长期运行。

## 运行时预检

真实 CAD 工作前，先检查运行环境：

```powershell
cad-mcp-doctor --check-autocad
```

同样的检查也暴露为 MCP 工具：

```text
check_runtime_environment(check_autocad=true, require_visual_export=false)
```

预检会报告 Windows、Python、依赖包、工作区可写性、可选视觉渲染器支持，以及 `check_autocad=true` 时的 AutoCAD COM 连接状态。`ok=false` 应视为绘图或编辑前的阻塞问题。

如果部署要求服务启动时必须通过检查，可以启用严格启动模式：

```powershell
$env:CAD_MCP_STRICT_PREFLIGHT = "1"
$env:CAD_MCP_PREFLIGHT_CHECK_AUTOCAD = "1"
$env:CAD_MCP_PREFLIGHT_REQUIRE_VISUAL = "0"
cad-mcp
```

当视觉导出是硬性要求时，将 `CAD_MCP_PREFLIGHT_REQUIRE_VISUAL=1`。

## MCP 客户端配置

建议从目标工作区启动 MCP 客户端。运行时数据默认写入该工作区；也可以通过 `CAD_MCP_WORKSPACE_ROOT` 指定。

### Codex

仓库内置 `.codex/config.toml`，用于项目级 Codex 配置。信任项目后，Codex 可以直接启动当前 checkout。

执行 `pip install -e .` 或安装已发布包后，可以使用用户级配置：

```toml
[mcp_servers.best-cad-mcp]
enabled = true
command = "cad-mcp"
cwd = "C:/path/to/best-cad-mcp"
startup_timeout_sec = 30
tool_timeout_sec = 120
default_tools_approval_mode = "approve"
```

从 checkout 虚拟环境启动的配置：

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

- `.mcp.json`：将本地 stdio 服务注册为 `best-cad-mcp`。
- `.claude/settings.json`：启用服务，并对原生命令和破坏性工具保持确认。

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

使用 `claude mcp list` 或 Claude Code 内的 `/mcp` 确认服务已连接。

## 推荐工作流

### 检查或修复已有 DWG

1. `check_runtime_environment(check_autocad=true)`。
2. 用户提供 DWG 路径时调用 `open_drawing`。
3. `scan_all_entities(clear_db=true, detail_level="minimal", topology_detail="summary")`。
4. `build_drawing_ir`，然后 `summarize_drawing`。
5. 根据领域调用 `detect_semantic_objects(domain="mechanical")` 或其他合适 domain。
6. `bind_all_dimensions`、`extract_drawing_constraints` 和 `check_drawing_constraints`。
7. `validate_geometry`。
8. 需要视觉证据时调用 `export_view_image_with_mapping(include_overlay=true)`。
9. 对 VLM 发现调用 `ground_vlm_region` 或 `ground_vlm_overlay_id`。
10. 修改前对目标调用 `explain_entity(handle)`。
11. 通过 handle 精确编辑，或通过已校验、已 dry-run 的 CADPlan 编辑。
12. 重新扫描、验证、视觉确认，然后保存或导出。

### 创建新图纸

1. `check_runtime_environment(check_autocad=true)`。
2. `create_new_drawing`。
3. 设置单位、图层、文字样式、尺寸样式、布局和视图状态。
4. 使用高层 CAD 操作构建 CADPlan，并加入依赖、`save_as` 变量和后置条件。
5. `validate_cad_plan`，然后 `dry_run_cad_plan`。
6. 只有在明确允许修改后，调用 `execute_cad_plan(..., allow_modify=true)`。
7. `scan_all_entities`、`build_drawing_ir`、`validate_geometry`，并导出审阅图。
8. 保存或导出最终 DWG、PDF、DXF 或 DWF 交付文件。

### 视觉审阅

1. `export_view_image_with_mapping(include_overlay=true)`。
2. 审阅干净图、overlay 图和 sidecar mapping JSON。
3. 对 overlay ID 使用 `ground_vlm_overlay_id`，对像素框使用 `ground_vlm_region`。
4. 用 `explain_entity` 确认候选实体。
5. 对选中的问题使用 `propose_repair_plan` 或 `propose_constraint_repair_plan`。

## 核心概念

### 工作区数据库

运行时元数据默认存储在：

```text
<workspace>/.cad_mcp/workspace.db
```

数据库按 workspace、drawing、conversation 和 thread 隔离数据，避免不同图纸中的相同 handle 冲突，也让并行 agent 会话拥有独立的私有标注和查询历史。

`execute_query` 是只读、按 scope 隔离、带限制的 SQL。请使用 `cad_entities` 等公开表名；直接访问 `main.<table>` 会被拒绝，避免绕过 scoped view。结果默认限制为 1000 行、5 秒、约 1 MB JSON。可以通过工具参数或以下环境变量调整：

- `CAD_MCP_SQL_MAX_ROWS`
- `CAD_MCP_SQL_TIMEOUT_MS`
- `CAD_MCP_SQL_MAX_RESULT_BYTES`

常用工作区工具：

- `get_workspace_context`
- `set_workspace_context`
- `activate_workspace_drawing`
- `list_workspace_drawings`
- `get_database_maintenance_status`
- `maintain_database`
- `clear_understanding_cache`
- `get_legacy_database_status`

### ToolResult

CAD 理解类工具返回统一的 `ToolResult` 结构：

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

`scan_all_entities(clear_db=true)` 默认会清空当前 thread 的旧语义对象、约束、验证报告和视图快照。只有确实要跨扫描保留缓存时才传 `clear_understanding=false`。

关键理解工具包括：

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
  "variables": {
    "origin": [0, 0, 0]
  },
  "steps": [
    {
      "step_id": "plate",
      "op": "draw_rectangle",
      "args": {
        "corner1": "$origin",
        "corner2": [120, 80, 0],
        "layer": "M-PART"
      },
      "writes": true,
      "save_as": "$plate",
      "postconditions": [
        {"type": "exists", "target": "$plate"}
      ]
    }
  ],
  "constraints": [
    {"type": "distance", "expected": 120.0}
  ]
}
```

CADPlan 当前可执行常见绘图、编辑、图层、尺寸、填充和块操作。尚未绑定到 CADPlan 的合法 CAD 操作仍可直接调用对应 MCP 工具。

CADPlan 校验默认禁止原始 `send_command`、SQL mutation、purge 和 audit 操作。执行过程中，如果绑定工具失败、返回 `ok=false`、返回 `success=false`，或返回可识别的错误文本，计划会停止，并在启用回滚时尝试回滚。

### 视觉定位

`export_view_image_with_mapping(include_overlay=true)` 会生成：

- 干净视图导出图；
- 带数字 ID 的可选 overlay 图；
- 记录视图参数、可见 handle、像素框和映射数据的 sidecar JSON。

VLM 返回像素框时使用 `ground_vlm_region(snapshot_id, bbox)`；返回 overlay ID 时使用 `ground_vlm_overlay_id(snapshot_id, overlay_id)`。编辑前应对最可能的候选实体调用 `explain_entity`。

顶视或平面模型空间视图最可靠。带 twist、UCS、三维视图或复杂布局视口的场景会返回警告或较低置信度。

### 提示词与装配图技能

`prompts/` 目录包含 MCP prompt 源文件：

- 理解已有图纸；
- 按规格精确绘图；
- VLM 图纸审阅；
- 修复规划。

这些提示词按工作流组织，目的是让 agent 面对复杂图纸时先走
CAD-IR、语义对象、约束、验证、视觉定位和受保护 CADPlan，而不是把
装配图、剖视/详图、BOM、标题栏、填充、尺寸或 3D 意图简化成普通线段和文字。

`.agents/skills/draw-assembly-diagrams` 提供面向 agent 的装配图工作流。装配图规则已经模块化：

- `references/assembly/index.md` 负责选择适用规范模块。
- `references/assembly/standards/generic-mechanical.md` 是默认机械装配图模块。
- 后续可以添加 ASME、ISO、GB 或公司规范模块，而不需要重写主技能。

## 安全模型

- 真实 CAD 工作前先运行预检。
- 先扫描和理解，再修改。
- 优先使用具名高层 CAD 工具，而不是拼低层 primitive。
- 使用扫描或查询工具返回的 AutoCAD handle，不只凭文字猜测编辑目标。
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

当前数据库是 `.cad_mcp/workspace.db`。如果旧版本留下根目录 `autocad_data.db`，`check_runtime_environment` 和 `get_legacy_database_status` 会以 warning 报告；确认没有旧 MCP 进程继续使用后，可以归档或删除。

日志使用 UTF-8 并按大小轮转。可通过以下环境变量配置：

- `CAD_MCP_LOG_PATH`
- `CAD_MCP_LOG_MAX_BYTES`
- `CAD_MCP_LOG_BACKUP_COUNT`
- `CAD_MCP_LOG_LEVEL`
- `CAD_MCP_MCP_LOG_LEVEL`

每行日志包含 workspace、drawing 和 thread ID，便于关联排查。

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
server.json                 MCP registry 元数据
```

## 开发

运行不需要 AutoCAD 的单元测试：

```powershell
python -m pytest -q -m "not autocad_com"
```

运行当前环境可执行的完整测试：

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

发布 workflow 会在 Windows 上构建分发包、运行非 AutoCAD 测试、校验 `server.json`，并在 tagged release 中发布到 PyPI 和 MCP Registry。

## 常见问题

- **服务启动了但工具调用失败**：确认 AutoCAD 已安装、已授权，并能在同一 Windows 用户下正常打开。
- **MCP 客户端无法导入 `src`**：把服务 `cwd` 设置为仓库根目录，或将 `PYTHONPATH` 指向仓库根目录。
- **工作区数据写到了错误目录**：从目标工作区启动 MCP 客户端，或设置 `CAD_MCP_WORKSPACE_ROOT`。
- **视觉导出不可用**：安装 `visual` extra，或安装 ImageMagick、Inkscape、librsvg、Chrome、Edge 等受支持渲染器。
- **需要的绘图行为没有工具**：应把能力补成 `best-cad-mcp` 工具，而不是依赖 agent 侧临时 COM 脚本。
- **视觉定位不确定**：查看 warnings，优先使用 overlay ID，并用 `explain_entity` 确认候选 handle。

## 贡献

欢迎贡献。一个好的改动通常包含：

- `src/cad_tools/` 或 `src/cad_understanding/` 中聚焦的工具实现；
- 需要暴露给 MCP 时，在 `src/server.py` 增加 wrapper；
- 不依赖 AutoCAD 的单元测试，方便普通 CI 运行；
- 工作流变化对应的文档或 prompt 更新；
- 不提交运行时产物。

请避免提交 `.cad_mcp/`、日志、导出的审阅图、本地数据库、虚拟环境、构建输出或 AutoCAD 冒烟测试产物。

## 致谢

模型私有标注和 pointer-style CAD 上下文设计在概念上参考了公开的 Pointer-CAD 项目和论文：
https://github.com/Snitro/Pointer-CAD

本仓库没有复制 Pointer-CAD 源码。

## 许可证

MIT。见 [LICENSE](LICENSE)。
